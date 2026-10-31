"""
The admin's sign-in to a LuxAlgo account, for LuxAlgo's MCP tools to give
whole Pine sources (src/admin/luxalgo.py): signed out, LuxAlgo gives an
indicator's first lines alone. A free account is enough.

OAuth 2.1 with PKCE (S256), as LuxAlgo's authorisation server serves it. Its
addresses are constants, as LuxAlgo's MCP address is, and are checked against
what the server says of itself before a sign-in begins.
- The admin registers itself once per return address (dynamic registration, a
  public client without a secret).
- It sends the operator to LuxAlgo with a random state, kept hashed for
  STATE_SECONDS and used once, and takes the code back on CALLBACK, or from the
  address pasted when the browser cannot come back. A return that names
  another issuer than LuxAlgo's (``iss``, RFC 9207) is refused.
- The tokens are sealed for the admin alone (credential luxalgo.oauth),
  renewed before they lapse, and on signing out revoked at LuxAlgo when it
  accepts (it may ask a public client for credentials it has none of) and
  removed here whatever it answers.
- Only LuxAlgo's MCP tools of src/admin/luxalgo.py are ever called with them:
  never its journal or account, though the token would open them.
"""

import asyncio
import base64
import hashlib
import json
import re
import secrets
import time
from urllib.parse import parse_qs, urlencode, urlsplit

from src.admin.errors import InvalidRequest, NotPossible
from src.admin.luxalgo import LUXALGO_URL

ISSUER = "https://app.luxalgo.com/api/auth"
METADATA = f"{ISSUER}/.well-known/oauth-authorization-server"
AUTHORIZE = f"{ISSUER}/oauth2/authorize"
TOKEN = f"{ISSUER}/oauth2/token"
REGISTER = f"{ISSUER}/oauth2/register"
REVOKE = f"{ISSUER}/oauth2/revoke"
# The scopes LuxAlgo's MCP server names: offline_access for a token that renews.
SCOPES = "openid profile email offline_access"
NAME = "luxalgo.oauth"
CALLBACK = "/api/luxalgo/callback"
STATE_SECONDS = 600
RENEW_BEFORE_SECONDS = 120
HTTP_SECONDS = 15.0
_ORIGIN = re.compile(r"^https?://[^\s/?#]+$")


def _digest(state: str) -> str:
    return hashlib.sha256(state.encode()).hexdigest()


def _challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def _account_of(id_token) -> str:
    """Who signed in, read from the identity token's claims (never checked: it is only said)."""
    try:
        claims = json.loads(base64.urlsafe_b64decode(str(id_token).split(".")[1] + "==="))
    except (ValueError, IndexError, TypeError):
        return "a LuxAlgo account"
    said = claims.get("email") or claims.get("name") or claims.get("preferred_username")
    return str(said)[:254] if said else "a LuxAlgo account"


class LuxAlgoSignIn:
    """``vault``: the admin's (src/admin/vault.py). ``http``: () -> an httpx.AsyncClient (in the tests, a fake's)."""

    def __init__(self, *, vault, http=None, clock=time.time):
        self._vault = vault
        self._http = http
        self._clock = clock
        # hashed state -> what its return needs; return address -> the client LuxAlgo registered for it.
        self._pending: dict[str, dict] = {}
        self._clients: dict[str, str] = {}
        self._renewing = asyncio.Lock()
        # Why LuxAlgo would not renew the sign-in, until signed in again.
        self.refused: str | None = None

    def _client(self):
        if self._http is not None:
            return self._http()
        import httpx

        return httpx.AsyncClient(timeout=HTTP_SECONDS)

    async def _send(self, method: str, url: str, **given) -> dict:
        try:
            async with self._client() as http:
                answer = await http.request(method, url, **given)
        except Exception as e:
            raise NotPossible(f"LuxAlgo could not be reached: {type(e).__name__}") from None
        try:
            said = answer.json() if answer.content else {}
        except ValueError:
            said = {}
        if answer.status_code >= 400:
            why = said.get("error_description") or said.get("error") or f"HTTP {answer.status_code}"
            raise NotPossible(f"LuxAlgo refused: {str(why)[:200]}")
        return said

    async def _check_server(self) -> None:
        said = await self._send("GET", METADATA)
        expected = {"issuer": ISSUER, "authorization_endpoint": AUTHORIZE, "token_endpoint": TOKEN,
                    "registration_endpoint": REGISTER}
        changed = [key for key, value in expected.items() if said.get(key) != value]
        if changed or "S256" not in (said.get("code_challenge_methods_supported") or []):
            raise NotPossible("LuxAlgo's sign-in is not where WATA expects it (" + ", ".join(changed or ["PKCE"])
                              + "): nothing was sent")

    async def _registered(self, redirect_uri: str) -> str:
        if redirect_uri not in self._clients:
            said = await self._send("POST", REGISTER, json={
                "client_name": "WATA admin", "redirect_uris": [redirect_uri],
                "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
                "token_endpoint_auth_method": "none", "scope": SCOPES})
            if not said.get("client_id"):
                raise NotPossible("LuxAlgo did not register the admin")
            self._clients[redirect_uri] = str(said["client_id"])
        return self._clients[redirect_uri]

    def _prune(self) -> None:
        oldest = self._clock() - STATE_SECONDS
        self._pending = {key: entry for key, entry in self._pending.items() if entry["at"] >= oldest}

    async def begin(self, origin: str) -> str:
        """Where to send the operator: LuxAlgo's sign-in, coming back to this admin's own address."""
        if not isinstance(origin, str) or not _ORIGIN.match(origin):
            raise InvalidRequest("A sign-in is begun from the admin's own page")
        redirect_uri = f"{origin}{CALLBACK}"
        await self._check_server()
        client_id = await self._registered(redirect_uri)
        verifier, state = secrets.token_urlsafe(48), secrets.token_urlsafe(24)
        self._prune()
        self._pending[_digest(state)] = {"verifier": verifier, "redirect_uri": redirect_uri, "client_id": client_id,
                                         "at": self._clock()}
        return f"{AUTHORIZE}?" + urlencode({
            "response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri, "scope": SCOPES,
            "state": state, "code_challenge": _challenge(verifier), "code_challenge_method": "S256",
            "resource": LUXALGO_URL, "prompt": "consent"})

    async def finish(self, code: str, state: str, by: str | None, issuer: str | None = None) -> dict:
        """The code LuxAlgo gave back, exchanged for the account's tokens, sealed: who is signed in."""
        self._prune()
        pending = self._pending.pop(_digest(state or ""), None)
        if pending is None:
            raise InvalidRequest("This sign-in is unknown, used, or older than 10 minutes: sign in again")
        if issuer is not None and issuer != ISSUER:
            raise InvalidRequest("This sign-in came back from another server than LuxAlgo's: nothing was kept")
        if not isinstance(code, str) or not 8 <= len(code) <= 2048:
            raise InvalidRequest("LuxAlgo gave back no code")
        tokens = await self._send("POST", TOKEN, data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": pending["redirect_uri"],
            "client_id": pending["client_id"], "code_verifier": pending["verifier"], "resource": LUXALGO_URL})
        await self._keep(tokens, pending["client_id"], pending["redirect_uri"], _account_of(tokens.get("id_token")),
                         by)
        self.refused = None
        return await self.status()

    async def hand_over(self, address: str, by: str | None) -> dict:
        """The address the browser landed on, pasted when it could not come back to the admin by itself."""
        query = parse_qs(urlsplit(str(address or "")).query)
        if query.get("error"):
            raise InvalidRequest(f"LuxAlgo said: {query['error'][0][:200]}")
        return await self.finish((query.get("code") or [""])[0], (query.get("state") or [""])[0], by,
                                 (query.get("iss") or [None])[0])

    async def _keep(self, tokens: dict, client_id: str, redirect_uri: str, account: str, by: str | None) -> None:
        if not tokens.get("access_token") or not tokens.get("refresh_token"):
            raise NotPossible("LuxAlgo gave no token that renews (offline_access)")
        expires_at = int(self._clock() + float(tokens.get("expires_in") or 3600))
        await self._vault.keep_sign_in(NAME, {
            "client_id": client_id, "redirect_uri": redirect_uri, "access_token": tokens["access_token"],
            "refresh_token": tokens["refresh_token"], "expires_at": str(expires_at), "account": account}, by)

    async def bearer(self) -> str | None:
        """The account's access token, renewed when it is about to lapse; None signed out or refused."""
        value = await self._vault.open(NAME)
        if not value:
            return None
        if int(value["expires_at"]) - RENEW_BEFORE_SECONDS > self._clock():
            return value["access_token"]
        async with self._renewing:
            value = await self._vault.open(NAME)
            if not value:
                return None
            if int(value["expires_at"]) - RENEW_BEFORE_SECONDS > self._clock():
                return value["access_token"]
            try:
                tokens = await self._send("POST", TOKEN, data={
                    "grant_type": "refresh_token", "refresh_token": value["refresh_token"],
                    "client_id": value["client_id"], "resource": LUXALGO_URL})
                # A server that does not turn its refresh tokens keeps the one before.
                tokens.setdefault("refresh_token", value["refresh_token"])
                await self._keep(tokens, value["client_id"], value["redirect_uri"], value["account"], "renewal")
            except NotPossible as e:
                self.refused = f"LuxAlgo would not renew the sign-in ({e}): sign in again"
                return None
            self.refused = None
            return tokens["access_token"]

    async def status(self) -> dict:
        """Whether the admin is signed in, as whom and until when; never a token."""
        value = await self._vault.open(NAME)
        if not value:
            return {"signed_in": False, "account": None, "expires_at": None, "refused": self.refused}
        return {"signed_in": True, "account": value["account"], "expires_at": int(value["expires_at"]),
                "refused": self.refused}

    async def sign_out(self, by: str | None) -> dict:
        """The sign-in revoked at LuxAlgo (when it answers) and removed here."""
        value = await self._vault.open(NAME)
        if value:
            try:
                await self._send("POST", REVOKE, data={"token": value["refresh_token"],
                                                       "token_type_hint": "refresh_token",
                                                       "client_id": value["client_id"]})
            except NotPossible:
                pass
            await self._vault.remove(NAME)
        self.refused = None
        return await self.status()
