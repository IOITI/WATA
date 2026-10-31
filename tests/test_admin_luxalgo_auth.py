"""The admin's sign-in to a LuxAlgo account (src/admin/luxalgo_auth.py), on a LuxAlgo authorisation server made up here."""

import asyncio
import base64
import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from src.admin import luxalgo_auth
from src.admin.errors import InvalidRequest, NotPossible
from src.admin.luxalgo import LUXALGO_URL
from src.admin.luxalgo_auth import AUTHORIZE, ISSUER, METADATA, REGISTER, REVOKE, TOKEN, LuxAlgoSignIn, _challenge
from tests.test_admin_vault import config, key_root, vault_with  # noqa: F401 (fixtures)
from tests.test_vault_loader import Database

ORIGIN = "https://wata.example:8443"


def identity(claims: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"eyJhbGciOiJFZERTQSJ9.{body}.signature"


class Table(Database):
    """The credential table, rows removed too."""

    async def execute(self, query, *args):
        if query.startswith("DELETE FROM credential WHERE"):
            self.executed.append((query, args))
            return "DELETE 1" if self.rows.pop(args[0], None) is not None else "DELETE 0"
        return await super().execute(query, *args)


class FakeAuthorisation:
    """LuxAlgo's authorisation server as it answers: every request kept."""

    def __init__(self):
        self.requests: list[tuple[str, str, dict]] = []
        self.metadata = {
            "issuer": ISSUER, "authorization_endpoint": AUTHORIZE, "token_endpoint": TOKEN,
            "registration_endpoint": REGISTER, "revocation_endpoint": REVOKE,
            "code_challenge_methods_supported": ["S256"],
        }
        self.challenge = None
        self.issued = 0
        self.refuse_refresh = False
        self.revoke_status = 200

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        body = request.content.decode()
        given = json.loads(body) if body.startswith("{") else {key: values[0] for key, values in parse_qs(body).items()}
        self.requests.append((request.method, url, given))
        if url == METADATA:
            return httpx.Response(200, json=self.metadata)
        if url == REGISTER:
            return httpx.Response(201, json={"client_id": "wata-client-0001", "redirect_uris": given["redirect_uris"]})
        if url == TOKEN and given["grant_type"] == "authorization_code":
            if _challenge(given["code_verifier"]) != self.challenge or given["code"] != "the-code-0001":
                return httpx.Response(400, json={"error": "invalid_grant", "error_description": "PKCE check failed"})
            return httpx.Response(200, json=self._tokens(identity({"email": "trader@example.com"})))
        if url == TOKEN and given["grant_type"] == "refresh_token":
            if self.refuse_refresh:
                return httpx.Response(400, json={"error": "invalid_grant", "error_description": "Refresh token revoked"})
            return httpx.Response(200, json=self._tokens())
        if url == REVOKE:
            return httpx.Response(self.revoke_status, json={} if self.revoke_status == 200 else {"error": "invalid_client"})
        return httpx.Response(404)

    def _tokens(self, id_token=None) -> dict:
        self.issued += 1
        tokens = {"access_token": f"access-{self.issued:04d}-abcdefgh", "refresh_token": f"refresh-{self.issued:04d}-abcdefgh",
                  "token_type": "Bearer", "expires_in": 3600}
        return {**tokens, "id_token": id_token} if id_token else tokens

    def asked(self, url: str) -> list[dict]:
        return [given for _, asked, given in self.requests if asked == url]


@pytest.fixture
def setup(config, key_root):
    _, view = key_root
    table = Table()
    vault = vault_with(table, config, view)
    server = FakeAuthorisation()
    now = [1_800_000_000.0]
    sign_in = LuxAlgoSignIn(vault=vault, http=lambda: httpx.AsyncClient(transport=httpx.MockTransport(server.handler)),
                            clock=lambda: now[0])
    return sign_in, server, vault, table, now


def begun(sign_in, server) -> dict:
    address = asyncio.run(sign_in.begin(ORIGIN))
    query = {key: values[0] for key, values in parse_qs(urlsplit(address).query).items()}
    server.challenge = query["code_challenge"]
    return query


def test_a_sign_in_sends_the_operator_to_luxalgo_with_pkce_coming_back_to_this_admin(setup):
    sign_in, server, *_ = setup
    query = begun(sign_in, server)
    assert query["response_type"] == "code" and query["code_challenge_method"] == "S256"
    assert query["client_id"] == "wata-client-0001"
    assert query["redirect_uri"] == f"{ORIGIN}/api/luxalgo/callback"
    assert query["scope"] == "openid profile email offline_access" and query["resource"] == LUXALGO_URL
    assert len(query["state"]) >= 32
    registered = server.asked(REGISTER)
    assert registered == [{"client_name": "WATA admin", "redirect_uris": [f"{ORIGIN}/api/luxalgo/callback"],
                           "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
                           "token_endpoint_auth_method": "none", "scope": "openid profile email offline_access"}]
    # Registered once per return address.
    begun(sign_in, server)
    assert len(server.asked(REGISTER)) == 1


@pytest.mark.parametrize("origin", ["", "javascript:alert(1)", "https://wata.example/path", "https://a b"])
def test_a_sign_in_is_begun_only_from_an_address_of_the_admin(setup, origin):
    sign_in, server, *_ = setup
    with pytest.raises(InvalidRequest):
        asyncio.run(sign_in.begin(origin))
    assert server.requests == []


def test_nothing_is_sent_to_a_sign_in_that_is_not_where_wata_expects_it(setup):
    sign_in, server, *_ = setup
    server.metadata["token_endpoint"] = "https://elsewhere.example/token"
    with pytest.raises(NotPossible, match="not where WATA expects it .token_endpoint."):
        asyncio.run(sign_in.begin(ORIGIN))
    server.metadata["token_endpoint"] = TOKEN
    server.metadata["code_challenge_methods_supported"] = ["plain"]
    with pytest.raises(NotPossible, match="PKCE"):
        asyncio.run(sign_in.begin(ORIGIN))
    assert server.asked(REGISTER) == []


def test_the_code_is_exchanged_once_and_sealed_for_the_admin_alone(setup):
    sign_in, server, vault, table, _ = setup
    query = begun(sign_in, server)
    status = asyncio.run(sign_in.finish("the-code-0001", query["state"], "operator", ISSUER))
    assert status == {"signed_in": True, "account": "trader@example.com", "expires_at": 1_800_003_600,
                      "refused": None}
    exchanged = server.asked(TOKEN)[0]
    assert exchanged["redirect_uri"] == f"{ORIGIN}/api/luxalgo/callback" and exchanged["resource"] == LUXALGO_URL
    row = table.rows["luxalgo.oauth"]
    assert row["readers"] == ["admin"] and "access-0001" not in json.dumps(row["public"])
    assert asyncio.run(vault.open("luxalgo.oauth"))["refresh_token"] == "refresh-0001-abcdefgh"
    with pytest.raises(InvalidRequest, match="unknown, used, or older than 10 minutes"):
        asyncio.run(sign_in.finish("the-code-0001", query["state"], "operator"))
    listed = {entry["name"]: entry for entry in asyncio.run(vault.listed())["items"]}["luxalgo.oauth"]
    assert listed["signed_in"] is True and "access-0001" not in json.dumps(listed, default=str)


def test_a_sign_in_comes_back_within_ten_minutes_from_luxalgo_and_with_a_code(setup):
    sign_in, server, vault, table, now = setup
    query = begun(sign_in, server)
    now[0] += 601
    with pytest.raises(InvalidRequest, match="older than 10 minutes"):
        asyncio.run(sign_in.finish("the-code-0001", query["state"], "operator"))
    query = begun(sign_in, server)
    with pytest.raises(InvalidRequest, match="another server than LuxAlgo's"):
        asyncio.run(sign_in.finish("the-code-0001", query["state"], "operator", "https://evil.example"))
    query = begun(sign_in, server)
    with pytest.raises(InvalidRequest, match="no code"):
        asyncio.run(sign_in.finish("", query["state"], "operator"))
    query = begun(sign_in, server)
    with pytest.raises(NotPossible, match="PKCE check failed"):
        asyncio.run(sign_in.finish("another-code-0001", query["state"], "operator"))
    assert "luxalgo.oauth" not in table.rows


def test_the_address_pasted_finishes_a_sign_in_or_says_what_luxalgo_said(setup):
    sign_in, server, *_ = setup
    query = begun(sign_in, server)
    with pytest.raises(InvalidRequest, match="LuxAlgo said: access_denied"):
        asyncio.run(sign_in.hand_over(f"{ORIGIN}/api/luxalgo/callback?error=access_denied&state={query['state']}",
                                      "operator"))
    landed = f"{ORIGIN}/api/luxalgo/callback?code=the-code-0001&state={query['state']}&iss={ISSUER}"
    assert asyncio.run(sign_in.hand_over(landed, "operator"))["signed_in"] is True


def test_the_token_is_renewed_before_it_lapses_once_for_concurrent_callers(setup):
    sign_in, server, vault, _, now = setup
    query = begun(sign_in, server)
    asyncio.run(sign_in.finish("the-code-0001", query["state"], "operator"))
    assert asyncio.run(sign_in.bearer()) == "access-0001-abcdefgh"
    now[0] += 3600 - luxalgo_auth.RENEW_BEFORE_SECONDS + 1

    async def both():
        return await asyncio.gather(sign_in.bearer(), sign_in.bearer())

    assert asyncio.run(both()) == ["access-0002-abcdefgh", "access-0002-abcdefgh"]
    renewals = [given for given in server.asked(TOKEN) if given["grant_type"] == "refresh_token"]
    assert renewals == [{"grant_type": "refresh_token", "refresh_token": "refresh-0001-abcdefgh",
                         "client_id": "wata-client-0001", "resource": LUXALGO_URL}]
    kept = asyncio.run(vault.open("luxalgo.oauth"))
    assert (kept["refresh_token"], kept["account"]) == ("refresh-0002-abcdefgh", "trader@example.com")


def test_a_renewal_refused_reads_signed_out_and_says_why_until_signed_in_again(setup):
    sign_in, server, _, _, now = setup
    query = begun(sign_in, server)
    asyncio.run(sign_in.finish("the-code-0001", query["state"], "operator"))
    server.refuse_refresh = True
    now[0] += 3600
    assert asyncio.run(sign_in.bearer()) is None
    assert "Refresh token revoked" in asyncio.run(sign_in.status())["refused"]
    server.refuse_refresh = False
    query = begun(sign_in, server)
    assert asyncio.run(sign_in.finish("the-code-0001", query["state"], "operator"))["refused"] is None


def test_signing_out_revokes_at_luxalgo_and_removes_the_sign_in_whatever_it_answers(setup):
    sign_in, server, vault, table, _ = setup
    query = begun(sign_in, server)
    asyncio.run(sign_in.finish("the-code-0001", query["state"], "operator"))
    server.revoke_status = 401
    assert asyncio.run(sign_in.sign_out("operator"))["signed_in"] is False
    assert server.asked(REVOKE) == [{"token": "refresh-0001-abcdefgh", "token_type_hint": "refresh_token",
                                     "client_id": "wata-client-0001"}]
    assert "luxalgo.oauth" not in table.rows and asyncio.run(sign_in.bearer()) is None


def test_a_sign_in_is_never_typed_into_the_credentials(setup):
    _, _, vault, _, _ = setup
    with pytest.raises(InvalidRequest, match="signing in"):
        asyncio.run(vault.set("luxalgo.oauth", {"client_id": "abcd", "redirect_uri": f"{ORIGIN}/api/luxalgo/callback",
                                                "access_token": "a" * 10, "refresh_token": "r" * 10,
                                                "expires_at": "1800000000", "account": "me"}, by="operator"))


def test_the_account_is_said_from_the_identity_tokens_claims_or_left_unnamed():
    assert luxalgo_auth._account_of(identity({"email": "a@b.c", "name": "A"})) == "a@b.c"
    assert luxalgo_auth._account_of(identity({"name": "A"})) == "A"
    assert luxalgo_auth._account_of(None) == "a LuxAlgo account"
    assert luxalgo_auth._account_of("not.a-token") == "a LuxAlgo account"
