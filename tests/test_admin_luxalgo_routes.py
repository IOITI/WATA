"""The LuxAlgo Library's routes (src/admin/routers/luxalgo.py), over the LuxAlgo made up in tests/test_admin_luxalgo.py."""

import json

import pytest

from src.admin.errors import InvalidRequest
from src.admin.luxalgo import CONTEXT, LIBRARY, LuxAlgo
from src.admin.script_origins import ScriptOrigins
from src.admin.ta_scripts import ScriptStore
from tests.test_admin_api import PASSWORD, SAME_ORIGIN, config_path, make_client, make_ctx  # noqa: F401 (fixtures)
from tests.test_admin_luxalgo import HEADER, FakeLuxAlgo


class FakeSignIn:
    """The admin's sign-in to LuxAlgo, as its routes use it: what each was given kept."""

    def __init__(self):
        self.asked = []
        self.signed_in = False

    async def status(self):
        return {"signed_in": self.signed_in, "account": "trader@example.com" if self.signed_in else None,
                "expires_at": 1_800_003_600 if self.signed_in else None, "refused": None}

    async def begin(self, origin):
        self.asked.append(("begin", origin))
        return f"https://app.luxalgo.com/api/auth/oauth2/authorize?state=made-up&redirect_uri={origin}"

    async def finish(self, code, state, by, issuer=None):
        self.asked.append(("finish", code, state, issuer))
        if state != "made-up":
            raise InvalidRequest("This sign-in is unknown, used, or older than 10 minutes: sign in again")
        self.signed_in = True
        return await self.status()

    async def hand_over(self, address, by):
        self.asked.append(("hand_over", address))
        self.signed_in = True
        return await self.status()

    async def sign_out(self, by):
        self.asked.append(("sign_out",))
        self.signed_in = False
        return await self.status()


@pytest.fixture
def setup(config_path, tmp_path):
    ctx = make_ctx(config_path, tmp_path, config_edit_enabled=True)
    fake = FakeLuxAlgo()
    ctx.ta_scripts = ScriptStore(str(tmp_path / "ta"))
    ctx.script_origins = ScriptOrigins(str(tmp_path / "ta"))
    ctx.luxalgo = LuxAlgo(server=fake.server)
    ctx.luxalgo_sign_in = FakeSignIn()
    with make_client(ctx, tmp_path / "no-interface") as client:
        assert client.post("/api/session", json={"password": PASSWORD}, headers=SAME_ORIGIN).status_code == 204
        yield client, ctx, fake


def audited(ctx) -> list[str]:
    return [entry["action"] for entry in ctx.audit_log.read(50)]


def test_the_library_is_read_through_the_admins_client(setup):
    client, _, fake = setup
    assert client.get("/api/luxalgo/families").json()["families"][0]["key"] == "trend"
    listed = client.get("/api/luxalgo/indicators", params={"family": "trend", "page": 2, "page_size": 12}).json()
    assert listed["indicators"][0]["slug"] == "made-up"
    assert client.get("/api/luxalgo/search", params={"query": "supertrend"}).json()["results"][0]["slug"] == "made-up"
    assert client.get("/api/luxalgo/concepts/supertrend").json()["name"] == "Supertrend"
    asked = dict((tool, given) for tool, given in fake.received)
    assert asked["library_list_indicators"]["page"] == 2 and asked["library_list_indicators"]["family"] == "trend"
    assert {given["context"] for _, given in fake.received} == {CONTEXT[LIBRARY]}


@pytest.mark.parametrize("path,params", [
    ("/api/luxalgo/indicators", {"family": "astrology"}),
    ("/api/luxalgo/indicators", {"page_size": 500}),
    ("/api/luxalgo/indicators", {"concept": "../account"}),
    ("/api/luxalgo/search", {"query": ""}),
    ("/api/luxalgo/indicators/Not_A_Slug", {}),
])
def test_what_is_asked_of_the_library_is_checked_before_luxalgo_is(setup, path, params):
    client, _, fake = setup
    assert client.get(path, params=params).status_code == 422
    assert fake.received == []


def test_what_luxalgo_does_not_have_is_the_pages_404(setup):
    client, _, _ = setup
    unknown = client.get("/api/luxalgo/indicators/unknown/source")
    assert unknown.status_code == 404 and "Unknown indicator slug" in unknown.json()["detail"]
    assert client.post("/api/luxalgo/indicators/unknown/import", json={}, headers=SAME_ORIGIN).status_code == 404


def test_a_source_is_shown_as_luxalgo_gives_it_with_its_licence(setup):
    client, _, _ = setup
    shown = client.get("/api/luxalgo/indicators/made-up/source").json()
    assert shown["complete"] is True and "\r" not in shown["source"]
    assert (shown["licence"], shown["owner"]) == ("CC BY-NC-SA 4.0", "LuxAlgo")


def test_an_indicator_becomes_the_operators_script_its_origin_kept(setup):
    client, ctx, _ = setup
    made = client.post("/api/luxalgo/indicators/made-up/import", json={}, headers=SAME_ORIGIN)
    assert made.status_code == 201
    script_id = made.json()["id"]
    script = ctx.ta_scripts.script(script_id)
    assert script["name"] == "Made up" and script.get("made_by") is None and script["source"].startswith(HEADER[:40])
    assert "LuxAlgo's Made up" in script["description"] and "CC BY-NC-SA 4.0" in script["description"]
    origin = ctx.script_origins.get(script_id)
    assert origin["slug"] == "made-up" and origin["by"] == "operator" and origin["licence"] == "CC BY-NC-SA 4.0"
    listed = {item["id"]: item for item in client.get("/api/ta/scripts").json()["items"]}
    assert listed[script_id]["origin"]["url"] == "https://www.luxalgo.com/library/indicator/made-up/"
    assert client.get(f"/api/ta/scripts/{script_id}").json()["origin"]["slug"] == "made-up"
    assert client.get("/api/luxalgo/indicators/made-up").json()["known"]["scripts"][0]["id"] == script_id
    # The same name again is refused, another one is taken.
    assert client.post("/api/luxalgo/indicators/made-up/import", json={}, headers=SAME_ORIGIN).status_code == 409
    again = client.post("/api/luxalgo/indicators/made-up/import", json={"name": "Made up, mine"}, headers=SAME_ORIGIN)
    assert again.status_code == 201
    assert len(client.get("/api/luxalgo/indicators/made-up").json()["known"]["scripts"]) == 2


def test_a_part_of_a_source_is_never_imported(setup):
    client, ctx, fake = setup
    fake.whole = False
    refused = client.post("/api/luxalgo/indicators/made-up/import", json={}, headers=SAME_ORIGIN)
    assert refused.status_code == 409 and "sign in to LuxAlgo" in refused.json()["detail"]
    assert ctx.ta_scripts.scripts() == []


def test_a_script_whose_header_says_luxalgo_is_shown_derived_from_it(setup):
    client, ctx, _ = setup
    made = ctx.ta_scripts.create("A copy", HEADER + "//@version=6\nindicator('A copy')\nplot(close)\n")
    mine = ctx.ta_scripts.create("Mine", "//@version=6\nindicator('Mine')\nplot(close)\n")
    listed = {item["id"]: item for item in client.get("/api/ta/scripts").json()["items"]}
    assert listed[made["id"]]["origin"] == {"provider": "luxalgo", "derived": True, "licence": "CC BY-NC-SA 4.0",
                                            "owner": "LuxAlgo"}
    assert "origin" not in listed[mine["id"]]


def test_with_luxalgo_off_its_routes_say_so(config_path, tmp_path):
    ctx = make_ctx(config_path, tmp_path, config_edit_enabled=True)
    with make_client(ctx, tmp_path / "no-interface") as client:
        client.post("/api/session", json={"password": PASSWORD}, headers=SAME_ORIGIN)
        assert client.get("/api/luxalgo/status").json() == {"enabled": False, "signed_in": False, "account": None,
                                                            "expires_at": None, "refused": None}
        off = client.get("/api/luxalgo/families")
        assert off.status_code == 409 and "admin.luxalgo_enabled" in off.json()["detail"]
        assert client.post("/api/luxalgo/sign-in", headers=SAME_ORIGIN).status_code == 409
        assert client.get("/api/luxalgo/callback", params={"code": "x", "state": "y"},
                          follow_redirects=False).headers["location"] == "/ta/luxalgo?luxalgo=refused&why=off"


def test_signing_in_is_a_change_of_credentials_begun_from_the_admins_own_page(setup):
    client, ctx, _ = setup
    begun = client.post("/api/luxalgo/sign-in", headers=SAME_ORIGIN)
    assert begun.status_code == 200 and begun.json()["url"].startswith("https://app.luxalgo.com/")
    assert ctx.luxalgo_sign_in.asked == [("begin", SAME_ORIGIN["Origin"])]
    assert client.post("/api/luxalgo/sign-in", headers={"Origin": "https://elsewhere.example"}).status_code == 403
    ctx.settings.config_edit_enabled = False
    assert client.post("/api/luxalgo/sign-in", headers=SAME_ORIGIN).status_code == 403
    assert client.delete("/api/luxalgo/sign-in", headers=SAME_ORIGIN).status_code == 403


def test_luxalgo_sends_the_browser_back_to_the_page_saying_what_came_of_it(setup):
    client, ctx, _ = setup
    anonymous = make_client(ctx, "no-interface")
    with anonymous:
        landed = anonymous.get("/api/luxalgo/callback", params={"code": "the-code", "state": "made-up",
                                                                 "iss": "https://app.luxalgo.com/api/auth"},
                               follow_redirects=False)
        assert landed.status_code == 303 and landed.headers["location"] == "/ta/luxalgo?luxalgo=signed-in"
        assert ctx.luxalgo_sign_in.asked[-1] == ("finish", "the-code", "made-up", "https://app.luxalgo.com/api/auth")
        stale = anonymous.get("/api/luxalgo/callback", params={"code": "the-code", "state": "used"},
                              follow_redirects=False)
        assert stale.headers["location"].startswith("/ta/luxalgo?luxalgo=refused&why=This%20sign-in%20is%20unknown")
        denied = anonymous.get("/api/luxalgo/callback", params={"error": "access_denied"}, follow_redirects=False)
        assert denied.headers["location"] == "/ta/luxalgo?luxalgo=refused&why=access_denied"
    assert client.get("/api/luxalgo/status").json()["account"] == "trader@example.com"
    assert audited(ctx).count("LuxAlgo account signed in") == 1


def test_the_address_pasted_signs_in_and_signing_out_is_audited(setup):
    client, ctx, _ = setup
    pasted = client.post("/api/luxalgo/sign-in/pasted", json={"address": "https://admin.test/api/luxalgo/callback?"
                                                                         "code=c&state=s"}, headers=SAME_ORIGIN)
    assert pasted.status_code == 200 and pasted.json()["signed_in"] is True
    assert client.delete("/api/luxalgo/sign-in", headers=SAME_ORIGIN).json()["signed_in"] is False
    # Newest first.
    assert audited(ctx)[:2] == ["LuxAlgo account signed out", "LuxAlgo account signed in"]
    assert "trader@example.com" not in json.dumps(ctx.audit_log.read(50))
