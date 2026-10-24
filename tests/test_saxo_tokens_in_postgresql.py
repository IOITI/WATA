"""
Saxo's tokens kept in PostgreSQL (src/saxo_authen/token_store.py, SaxoAuth's
database path), the admin's side of a login (src/admin/saxo.py, the callback)
and watasaxoauth. The SQL itself runs against PostgreSQL in
tests/test_infrastructure_live.py.
"""

import asyncio
import datetime
import json
import threading
from unittest.mock import MagicMock, patch

import pytest

from src.saxo_authen import AuthorizationRequired, SaxoAuth
from src.saxo_authen import cli as saxo_cli
from src.saxo_authen.token_store import DbTokenStore, FileTokenStore, Tokens, TokenStoreUnavailable, token_status
from src.vault import keys
from src.vault.sealed import open_sealed


def tokens(access: str, *, age_seconds: int = 0, refresh_lapsed: bool = False) -> dict:
    return {
        "access_token": access,
        "refresh_token": f"refresh-for-{access}",
        "expires_in": 1200,
        "refresh_token_expires_in": 0 if refresh_lapsed else 3600,
        "date_saved": (datetime.datetime.now() - datetime.timedelta(seconds=age_seconds)).isoformat(),
    }


class Store:
    """The environment's row in memory, behind DbTokenStore's interface."""

    kind = "database"

    def __init__(self, data=None, generation=1):
        self.row = Tokens(data, generation) if data else None
        self.code = None
        self.needs_since = None
        self.reminders = 0
        self.refusals = []
        self.saved = []
        self.lock = threading.Lock()

    def load(self):
        return Tokens(dict(self.row.data), self.row.generation) if self.row else None

    def save(self, data, expected_generation, by):
        current = self.row.generation if self.row else 0
        assert current == expected_generation, "written over another renewal"
        self.row = Tokens(dict(data), expected_generation + 1)
        self.needs_since = None
        self.saved.append((data["access_token"], expected_generation + 1, by))
        return expected_generation + 1

    def flush_spool(self):
        pass

    def needs_authorization(self, refusal):
        first = self.needs_since is None
        self.needs_since = self.needs_since or "now"
        return first

    def reminder_due(self, every):
        return False

    def claim_code(self):
        code, self.code = self.code, None
        return code

    def note_refusal(self, refusal):
        self.refusals.append(refusal)

    def status(self):
        return {"has_token": self.row is not None}


@pytest.fixture
def config(tmp_path):
    manager = MagicMock()
    values = {
        "authentication.saxo.app_config_object": {"AppKey": "k" * 32, "AppSecret": "s" * 32},
        "authentication.persistant.token_path": str(tmp_path / "saxo_auth" / "persist_token.json"),
        "saxo_auth.env": "live",
    }
    manager.get_config_value.side_effect = lambda key, default=None: values.get(key, default)
    return manager


REDIRECT = "https://admin.example.com/api/saxo/callback"


def saxo_with(config, store, environ=None):
    return SaxoAuth(config, token_store=store, environ=environ or {"WATA_APP_ROLE": "trader", "SAXO_REDIRECT_URL": REDIRECT})


def answered(access):
    response = MagicMock(status_code=201)
    response.json.return_value = {k: v for k, v in tokens(access).items() if k != "date_saved"}
    return response


def test_a_fresh_token_of_the_row_is_returned_without_asking_saxo(config):
    store = Store(tokens("current"))

    with patch("src.saxo_authen.requests.post") as post:
        assert saxo_with(config, store).get_token() == "current"
    post.assert_not_called()


def test_an_expired_token_is_renewed_once_under_the_lock_and_kept_as_the_next_generation(config):
    store = Store(tokens("old", age_seconds=1500), generation=7)
    saxo = saxo_with(config, store)

    with patch("src.saxo_authen.requests.post", return_value=answered("renewed")) as post:
        assert saxo.get_token() == "renewed"
        # Kept in memory: asked again only near its end.
        assert saxo.get_token() == "renewed"

    post.assert_called_once()
    # Saxo's own token address, never what a row or the file says.
    assert post.call_args.args[0] == "https://live.logonvalidation.net/token"
    assert post.call_args.kwargs["data"]["refresh_token"] == "refresh-for-old"
    assert store.saved == [("renewed", 8, "trader")]


def test_a_token_renewed_by_another_service_while_this_one_waited_for_the_lock_is_used(config):
    store = Store(tokens("old", age_seconds=1500))
    saxo = saxo_with(config, store)
    real_lock = saxo._token_refresh_lock

    def renewed_meanwhile():
        store.row = Tokens(tokens("renewed-elsewhere"), 2)
        return real_lock()

    with patch.object(saxo, "_token_refresh_lock", renewed_meanwhile), patch("src.saxo_authen.requests.post") as post:
        assert saxo.get_token() == "renewed-elsewhere"
    post.assert_not_called()


def test_a_refused_renewal_is_said_in_the_row_and_raises(config):
    store = Store(tokens("old", age_seconds=1500))
    refused = MagicMock(status_code=400, text="")
    refused.json.return_value = {"error": "invalid_grant"}

    with patch("src.saxo_authen.requests.post", return_value=refused), pytest.raises(Exception, match="Failed to renew"):
        saxo_with(config, store).get_token()

    assert store.refusals == ["Saxo answered 400: invalid_grant"]


def test_a_lapsed_login_takes_a_code_handed_over_and_says_so(config):
    store = Store(tokens("old", refresh_lapsed=True, age_seconds=4000), generation=3)
    store.code = {"code": "the-code"}
    saxo = saxo_with(config, store)
    said = []

    with patch("src.saxo_authen.requests.post", return_value=answered("fresh")) as post, \
            patch.object(saxo, "_send_telegram_notification", side_effect=lambda message: said.append(message) or True):
        assert saxo.get_token() == "fresh"

    data = post.call_args.kwargs["data"]
    assert data["grant_type"] == "authorization_code" and data["code"] == "the-code"
    # The server's redirect, the same the admin's link was made with: never one a row says.
    assert data["redirect_uri"] == REDIRECT and post.call_args.args[0] == "https://live.logonvalidation.net/token"
    assert store.saved == [("fresh", 4, "trader")]
    assert said == ["✅ Saxo's login renewed (live account): trading can go on."]


def test_a_lapsed_login_is_said_once_and_its_code_awaited_without_holding_the_lock(config):
    store = Store(tokens("old", refresh_lapsed=True, age_seconds=4000))
    saxo = saxo_with(config, store)
    said = []
    waits = []

    def sleep(seconds):
        waits.append(seconds)
        # The lock is free while waiting: another service could take it.
        assert saxo_lock_free()
        if len(waits) == 3:
            store.code = {"code": "handed-over"}

    def saxo_lock_free():
        import fcntl

        with open(saxo.token_lock_path, "a") as other:
            try:
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(other, fcntl.LOCK_UN)
                return True
            except BlockingIOError:
                return False

    with patch("src.saxo_authen.time.sleep", side_effect=sleep), \
            patch("src.saxo_authen.requests.post", return_value=answered("fresh")), \
            patch.object(saxo, "_send_telegram_notification", side_effect=lambda message: said.append(message) or True):
        assert saxo.get_token() == "fresh"

    assert said[0].startswith("🔐 Saxo's login has lapsed (live account)") and len(said) == 2
    assert waits == [5, 5, 5]


def test_no_code_in_time_raises_and_a_second_service_does_not_say_it_again(config):
    store = Store(tokens("old", refresh_lapsed=True, age_seconds=4000))
    said = []
    clock = [1_000.0]

    def sleep(seconds):
        clock[0] += seconds

    for _ in range(2):
        saxo = saxo_with(config, store)
        with patch("src.saxo_authen.time.sleep", side_effect=sleep), patch("src.saxo_authen.time.time", side_effect=lambda: clock[0]), \
                patch.object(saxo, "_send_telegram_notification", side_effect=lambda message: said.append(message) or True), \
                pytest.raises(AuthorizationRequired, match="authorise from the admin"):
            saxo.get_token()

    assert len(said) == 1


def test_the_state_of_the_login_is_read_from_the_row_never_a_token():
    saved = datetime.datetime(2026, 10, 24, 8, tzinfo=datetime.timezone.utc)
    row = {"has_token": True, "saved_at": saved, "access_expires_at": saved + datetime.timedelta(minutes=20),
           "refresh_expires_at": saved + datetime.timedelta(hours=1), "needs_authorization_since": None, "last_refusal": None}

    status = token_status(row, now=saved + datetime.timedelta(minutes=30))

    assert status["has_token"] and status["access_token_expired"] and not status["refresh_token_expired"]
    assert status["refresh_token_expires_at"] == "2026-10-24T09:00:00+00:00"
    assert token_status(None)["has_token"] is False and token_status(None)["refresh_token_expired"] is True


# ── which store, and the spool ──


def test_the_file_of_before_is_read_while_it_is_there_then_postgresql(config, tmp_path, monkeypatch):
    monkeypatch.setattr("src.vault.keys.has_private_key", lambda *_: True)
    folder = tmp_path / "saxo_auth"
    folder.mkdir()
    (folder / "persist_token.json").write_bytes(b"x")

    assert SaxoAuth(config, environ={"WATA_APP_ROLE": "trader"}).store.kind == "file"
    (folder / "persist_token.json").rename(folder / "persist_token.json.imported")
    assert SaxoAuth(config, environ={"WATA_APP_ROLE": "trader"}).store.kind == "database"
    # Without a key of its own (the lab, the tests): the file, as before.
    monkeypatch.setattr("src.vault.keys.has_private_key", lambda *_: False)
    assert SaxoAuth(config, environ={"WATA_APP_ROLE": "trader"}).store.kind == "file"


class Down:
    """A database that cannot be reached."""

    def run(self, work, timeout=15):
        raise TokenStoreUnavailable("ConnectionRefusedError")


def view_of(root, role, tmp_path):
    view = tmp_path / f"view-{role}"
    (view / "own").mkdir(parents=True)
    (view / "own" / "private.key").write_bytes((root / role / "private.key").read_bytes())
    (view / "public").symlink_to(root / "public")
    return str(view)


def test_tokens_renewed_while_postgresql_cannot_be_written_are_kept_in_the_spool_and_read_back(tmp_path):
    root = tmp_path / "keys"
    keys.generate(str(root))
    folder = tmp_path / "saxo_auth"
    folder.mkdir()
    store = DbTokenStore(Down(), "live", "position_monitor", str(folder), keys_dir=view_of(root, "position_monitor", tmp_path))

    generation = store.save(tokens("renewed"), 4, by="position_monitor")

    assert generation == 5
    spool = json.loads((folder / "token_spool.json").read_text())
    assert spool["generation"] == 5 and "renewed" not in json.dumps(spool)
    # Read back while the database is still down; by another service too, with its own key.
    assert store.load().data["access_token"] == "renewed"
    other = DbTokenStore(Down(), "live", "trader", str(folder), keys_dir=view_of(root, "trader", tmp_path))
    assert other.load().generation == 5
    assert open_sealed("saxo.live.tokens", spool["sealed"], "trader",
                       keys.private_key_at(str(root / "trader" / "private.key")))["refresh_token"] == "refresh-for-renewed"


def test_nothing_read_while_postgresql_is_down_and_nothing_spooled_is_an_error(tmp_path):
    root = tmp_path / "keys"
    keys.generate(str(root))
    store = DbTokenStore(Down(), "live", "trader", str(tmp_path), keys_dir=view_of(root, "trader", tmp_path))

    with pytest.raises(TokenStoreUnavailable):
        store.load()


# ── the admin's side, and watasaxoauth ──


class Codes:
    """The tables saxo_auth_code, credential and saxo_token in memory, for AdminSaxo."""

    def __init__(self):
        self.codes = {}
        self.credentials = {}
        self.now = datetime.datetime.now(datetime.timezone.utc)

    async def execute(self, query, *args):
        query = " ".join(query.split())
        if "CREATE TABLE" in query:
            return "CREATE"
        if query.startswith("INSERT INTO saxo_auth_code (environment, state_sha256"):
            self.codes.setdefault(args[0], {}).update(state=args[1], until=args[2])
            return "INSERT 0 1"
        if query.startswith("INSERT INTO saxo_auth_code (environment, sealed"):
            self.codes.setdefault(args[0], {}).update(sealed=json.loads(args[1]), at=args[2], by=args[3], consumed=None)
            return "INSERT 0 1"
        raise AssertionError(query)

    async def fetchrow(self, query, *args):
        query = " ".join(query.split())
        if query.startswith("UPDATE saxo_auth_code SET state_sha256 = NULL"):
            for environment, row in self.codes.items():
                if row.get("state") == args[0] and row.get("until") and row["until"] > args[1]:
                    row["state"] = row["until"] = None
                    return {"environment": environment}
            return None
        if query.startswith("SELECT sealed IS NOT NULL AS has_token"):
            return None
        raise AssertionError(query)

    async def fetch(self, query, *args):
        return [{"name": name, **row} for name, row in self.credentials.items()]


@pytest.fixture
def admin_saxo(tmp_path, config):
    from src.admin.saxo import AdminSaxo

    root = tmp_path / "keys"
    keys.generate(str(root))
    table = Codes()
    table.credentials["saxo.live.app"] = {"public": {"app_key": "a" * 32}, "readers": [], "fingerprint": "0000",
                                          "set_at": None, "set_by": None}
    saxo = AdminSaxo(reader=table, writer=table, config_manager=config, keys_dir=str(root),
                     environ={"SAXO_REDIRECT_URL": "https://admin.example.com/api/saxo/callback"})
    return saxo, table, root


def test_the_admins_link_carries_a_state_kept_hashed_for_ten_minutes(admin_saxo):
    import hashlib
    import urllib.parse

    saxo, table, _ = admin_saxo

    url = asyncio.run(saxo.authorize("live"))

    query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    assert url.startswith("https://live.logonvalidation.net/authorize?")
    assert query["client_id"] == ["a" * 32] and query["redirect_uri"] == ["https://admin.example.com/api/saxo/callback"]
    stored = table.codes["live"]
    assert stored["state"] == hashlib.sha256(query["state"][0].encode()).hexdigest()
    assert query["state"][0] not in json.dumps(table.codes, default=str)
    assert 590 <= (stored["until"] - datetime.datetime.now(datetime.timezone.utc)).total_seconds() <= 600


def test_a_code_comes_back_once_with_its_state_sealed_for_the_services_that_log_in(admin_saxo):
    import urllib.parse

    saxo, table, root = admin_saxo
    state = urllib.parse.parse_qs(urllib.parse.urlsplit(asyncio.run(saxo.authorize("live"))).query)["state"][0]

    assert asyncio.run(saxo.hand_over("the-code", state=state, by="10.77.0.2")) == "live"
    envelope = table.codes["live"]["sealed"]
    assert open_sealed("saxo.live.code", envelope, "watchlist_manager",
                       keys.private_key_at(str(root / "watchlist_manager" / "private.key"))) == {"code": "the-code"}
    from src.admin.errors import NotPossible

    with pytest.raises(NotPossible, match="unknown, used or older"):
        asyncio.run(saxo.hand_over("again", state=state))
    # Pasted alone, without its state: taken as before, for the environment in use.
    assert asyncio.run(saxo.hand_over("pasted")) == "live"


def test_a_state_too_old_is_refused(admin_saxo):
    import urllib.parse

    from src.admin.errors import NotPossible

    saxo, table, _ = admin_saxo
    state = urllib.parse.parse_qs(urllib.parse.urlsplit(asyncio.run(saxo.authorize("live"))).query)["state"][0]
    table.codes["live"]["until"] = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=1)

    with pytest.raises(NotPossible):
        asyncio.run(saxo.hand_over("late", state=state))


def test_watasaxoauth_reads_the_code_and_state_of_the_address_pasted():
    assert saxo_cli.code_and_state("https://admin.example.com/api/saxo/callback?code=abc&state=xyz") == ("abc", "xyz")
    assert saxo_cli.code_and_state("  abc-123  ") == ("abc-123", None)
    assert saxo_cli.code_and_state("http://localhost/?code=abc") == ("abc", None)


def test_watasaxoauth_prints_a_link_and_hands_the_code_over(config, tmp_path, monkeypatch):
    table = Codes()
    table.credentials["saxo.live.app"] = {"public": {"app_key": "a" * 32}, "readers": [], "fingerprint": None,
                                          "set_at": None, "set_by": None}
    root = tmp_path / "keys"
    keys.generate(str(root))
    monkeypatch.setattr("src.vault.keys.KEYS_DIR", str(root))
    monkeypatch.setattr("src.admin.saxo.keys.KEYS_DIR", str(root))
    monkeypatch.setenv("SAXO_REDIRECT_URL", "https://admin.example.com/api/saxo/callback")
    shown = []

    class Connection(Codes):
        async def close(self):
            pass

    connection = Connection()
    connection.credentials = table.credentials

    async def connect():
        return connection

    def ask(_prompt):
        link = shown[1].strip()
        state = link.split("state=")[1]
        return f"https://admin.example.com/api/saxo/callback?code=from-saxo&state={state}"

    from src.admin import saxo as admin_saxo_module

    original = admin_saxo_module.AdminSaxo.__init__

    def with_keys(self, **kwargs):
        original(self, **{**kwargs, "keys_dir": str(root)})

    monkeypatch.setattr(admin_saxo_module.AdminSaxo, "__init__", with_keys)
    environment = asyncio.run(saxo_cli._hand_over_in_postgresql(config, ask=ask, show=shown.append, connect=connect))

    assert environment == "live" and connection.codes["live"]["by"] == "console"
    assert "from-saxo" not in json.dumps(connection.codes, default=str)


def test_the_callback_hands_the_code_over_and_leads_back_to_the_system_page(tmp_path):
    from tests.test_admin_api import config_path, make_client, make_ctx  # noqa: F401

    class Saxo:
        def __init__(self):
            self.handed = []

        async def hand_over(self, code, *, state=None, environment=None, by=None):
            from src.admin.errors import NotPossible

            if state != "good":
                raise NotPossible("This login's link is unknown, used or older than ten minutes: authorise again")
            self.handed.append(code)
            return "live"

    import json as json_module

    with open("tests/test_config.json") as source:
        data = json_module.load(source)
    data["logging"]["persistant"]["log_path"] = str(tmp_path / "log")
    path = tmp_path / "etc" / "config.json"
    path.parent.mkdir()
    path.write_text(json_module.dumps(data))
    ctx = make_ctx(str(path), tmp_path)
    ctx.saxo = Saxo()
    client = make_client(ctx, tmp_path / "no-interface")
    with client:
        good = client.get("/api/saxo/callback?code=c0de&state=good", follow_redirects=False)
        bad = client.get("/api/saxo/callback?code=c0de&state=forged", follow_redirects=False)
        refused = client.get("/api/saxo/callback?error=access_denied", follow_redirects=False)

    assert (good.status_code, good.headers["location"]) == (303, "/system?saxo=handed-over")
    assert bad.headers["location"].startswith("/system?saxo=refused&why=This%20login")
    assert refused.headers["location"] == "/system?saxo=refused&why=access_denied"
    assert ctx.saxo.handed == ["c0de"]
    assert ctx.audit_log.read(1)[0]["action"] == "Saxo authorization code received"


def test_going_back_writes_the_tokens_to_their_file_as_a_version_of_before_reads_them(config, tmp_path, capsys):
    from src.vault import migrate

    (tmp_path / "saxo_auth").mkdir()
    saxo = saxo_with(config, Store(tokens("current"), generation=4))

    assert migrate.export_saxo(config, saxo) == 0

    path = str(tmp_path / "saxo_auth" / "persist_token.json")
    assert FileTokenStore(path, migrate._file_cipher(path, "s" * 32)).load().data["access_token"] == "current"
    # config.json's app opens it, as SaxoAuth of before reads it.
    assert migrate._saxo_file(config)[1].load().data["access_token"] == "current"
    printed = capsys.readouterr().out
    assert "written back" in printed and "does not hold the app" not in printed


def test_going_back_after_the_purge_uses_the_stored_app_and_says_config_json_needs_it(tmp_path, capsys):
    from src.vault import loader, migrate

    manager = MagicMock()
    values = {
        "authentication.saxo.app_config_object": {},
        "authentication.persistant.token_path": str(tmp_path / "saxo_auth" / "persist_token.json"),
        "saxo_auth.env": "live",
    }
    manager.get_config_value.side_effect = lambda key, default=None: values.get(key, default)
    (tmp_path / "saxo_auth").mkdir()
    loader._loaded["saxo.live.app"] = {"app_key": "k" * 32, "app_secret": "t" * 32}
    try:
        saxo = saxo_with(manager, Store(tokens("current")))
        assert migrate.export_saxo(manager, saxo) == 0
    finally:
        loader.forget()

    path = str(tmp_path / "saxo_auth" / "persist_token.json")
    # The salt the purge removed is made again; the file opens with the secret of the app the tokens belong to.
    assert (tmp_path / "saxo_auth" / ".key_salt").exists()
    assert FileTokenStore(path, migrate._file_cipher(path, "t" * 32)).load().data["access_token"] == "current"
    printed = capsys.readouterr().out
    assert "does not hold the app these tokens belong to" in printed and "t" * 32 not in printed


def test_going_back_without_tokens_writes_nothing(config, tmp_path, capsys):
    from src.vault import migrate

    (tmp_path / "saxo_auth").mkdir()

    assert migrate.export_saxo(config, saxo_with(config, Store())) == 1
    assert not (tmp_path / "saxo_auth" / "persist_token.json").exists()
    assert "No Saxo tokens in PostgreSQL" in capsys.readouterr().out
