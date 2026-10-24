"""
Which credentials there are and who reads each (src/vault/credentials.py),
what a service reads of them at its start (loader.py), the web server's
tokens rotated (tokens.py), and writing one (writer.py, store.py).
"""

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone

import pytest

from src.configuration import ConfigurationManager
from src.logging_helper.redaction import forget_secrets, redact
from src.vault import credentials as catalog
from src.vault import keys, loader, tokens
from src.vault.sealed import seal
from src.vault.writer import put

TELEGRAM = {"token": "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi", "chat_id": "4242"}
CAPITAL = {"api_key": "capitalkey1234567890", "identifier": "me@example.com", "password": "pass word 1"}


@pytest.fixture(autouse=True)
def fresh():
    loader.forget()
    forget_secrets()
    yield
    loader.forget()
    forget_secrets()


@pytest.fixture
def key_root(tmp_path):
    root = tmp_path / "keys"
    keys.generate(str(root))
    return root


def container_keys(key_root, role):
    """A container's view of the keys, as the compose file mounts them."""
    view = key_root.parent / f"view-{role}"
    if not view.exists():
        (view / "own").mkdir(parents=True)
        (view / "own" / "private.key").write_bytes((key_root / role / "private.key").read_bytes())
        (view / "public").symlink_to(key_root / "public")
    return str(view)


@pytest.fixture
def config(tmp_path):
    with open("tests/test_config.json") as source:
        data = json.load(source)
    data["telegram"] = {"bot_token": "999:old-token-from-the-file-xxxxxxxxxxxxxxx", "chat_id": "1"}
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data))
    return ConfigurationManager(str(path))


class Database:
    """The credential table in memory, answering the statements of src/vault/store.py."""

    def __init__(self, rows=None, failing=None):
        self.rows = dict(rows or {})
        # The rows of saxo_token, by environment.
        self.saxo = {}
        self.failing = failing
        self.executed = []
        self.closed = False

    async def fetchval(self, query, *args):
        query = " ".join(query.split())
        if "to_regclass('saxo_token') IS NOT NULL AND EXISTS" in query:
            return bool(self.saxo.get(args[0], {}).get("sealed"))
        if query == "SELECT to_regclass('saxo_token') IS NOT NULL":
            return True
        raise AssertionError(query)

    async def fetchrow(self, query, *args):
        query = " ".join(query.split())
        if query.startswith("SELECT generation FROM saxo_token"):
            return self.saxo.get(args[0])
        raise AssertionError(query)

    async def fetch(self, query, *args):
        if self.failing is not None:
            raise self.failing
        if "FROM credential WHERE name = ANY" in query:
            return [{"name": name, **row} for name, row in self.rows.items() if name in args[0]]
        if "FROM credential ORDER BY" in query:
            return [{"name": name, **row} for name, row in sorted(self.rows.items())]
        if "FROM credential_check" in query:
            return []
        raise AssertionError(query)

    async def execute(self, query, *args):
        self.executed.append((query, args))
        if query.strip().startswith("INSERT INTO saxo_token"):
            environment, envelope, refresh_at, ends_at = args
            self.saxo.setdefault(environment, {"sealed": json.loads(envelope), "generation": 1})
            return "INSERT 0 1"
        if query.strip().startswith("INSERT INTO credential "):
            name, public, sealed, readers, fingerprint, set_at, by = args
            if "DO NOTHING" in query and name in self.rows:
                return "INSERT 0 0"
            self.rows[name] = {"public": json.loads(public), "sealed": json.loads(sealed), "readers": readers,
                               "fingerprint": fingerprint, "set_at": set_at, "set_by": by}
            return "INSERT 0 1"
        return "OK"

    async def close(self):
        self.closed = True


class MissingTable(Exception):
    sqlstate = "42P01"


def sealed_row(key_root, name, value):
    publics = keys.public_keys(str(key_root))
    readers = catalog.BY_NAME[name].readers
    return {"public": {}, "sealed": seal(name, value, {role: publics[role] for role in readers}), "readers": list(readers),
            "fingerprint": None, "set_at": None, "set_by": "test"}


def load(config, role, db=None, key_root=None, **kwargs):
    async def connect(dsn):
        if db is None:
            raise ConnectionRefusedError("no database")
        return db

    return asyncio.run(loader.load(
        config, role, connect=connect, keys_dir=container_keys(key_root, role) if key_root else "/nowhere", **kwargs,
    ))


# ── which credentials there are ──


def test_every_reader_is_a_service_with_a_key_and_the_capital_login_is_the_feeds_alone():
    for credential in catalog.CREDENTIALS:
        assert set(credential.readers) <= set(keys.ROLES), credential.name
    assert catalog.BY_NAME["capital.login"].readers == ("capital_feed",)
    assert catalog.BY_NAME["heartbeat.url"].readers == ("position_monitor",)
    # TradingView's token opens the webhook alone: the other services hold the internal one.
    assert catalog.BY_NAME["webhook.token"].readers == ("web_server", "admin")
    assert [credential.name for credential in catalog.of_role("scheduler")] == ["internal.token"]
    # The watchlist manager logs in to Saxo: its apps, nothing else.
    assert [credential.name for credential in catalog.of_role("watchlist_manager")] == ["saxo.live.app", "saxo.simulation.app"]
    assert catalog.of_role("web_server")[0].name == "webhook.token" and catalog.of_role("control") == []


def test_the_telegram_bot_is_sealed_for_every_service_that_sends_to_telegram_itself():
    senders = set()
    for folder, _, files in os.walk("src"):
        for file in files:
            path = os.path.join(folder, file)
            if file.endswith(".py") and "AsyncTelegramSender(" in open(path).read() and "mq_telegram" not in folder:
                senders.add(folder.split(os.sep)[1])
    roles = {"trader": "trader", "position_monitor": "position_monitor", "price_recorder": "price_recorder",
             "capital_feed": "capital_feed", "ta_engine": "ta_engine", "admin": "admin"}

    assert {roles[folder] for folder in senders} | {"telegram"} == set(catalog.BY_NAME["telegram.bot"].readers)


def test_each_part_is_checked_and_a_refusal_never_repeats_the_value():
    assert catalog.checked("telegram.bot", {"token": f" {TELEGRAM['token']} ", "chat_id": -100123}) == {
        "token": TELEGRAM["token"], "chat_id": "-100123",
    }
    assert catalog.checked("capital.login", CAPITAL) == CAPITAL
    for name, given, said in (
        ("telegram.bot", {"token": "not-a-token", "chat_id": "1"}, "bot token is not as expected"),
        ("telegram.bot", {"token": TELEGRAM["token"]}, "chat id is missing"),
        ("telegram.bot", {**TELEGRAM, "extra": "x"}, "has no extra"),
        ("heartbeat.url", {"url": "http://hc-ping.com/abc"}, "https://"),
        ("capital.login", {**CAPITAL, "api_key": "has spaces in it"}, "api key is not as expected"),
        ("nothing.here", {}, "not one of WATA's credentials"),
    ):
        with pytest.raises(ValueError, match=said) as refused:
            catalog.checked(name, given)
        for value in given.values():
            if isinstance(value, str) and len(value) > 3:
                assert value not in str(refused.value)


def test_what_is_shown_of_a_credential():
    assert catalog.fingerprint("telegram.bot", TELEGRAM) == "fghi"
    assert catalog.public_of("telegram.bot", TELEGRAM) == {"chat_id": "4242"}
    assert catalog.public_of("capital.login", CAPITAL) == {"identifier": "me@example.com"}
    url = {"url": "https://hc-ping.com/0f4e-secret-uuid"}
    assert catalog.public_of("heartbeat.url", url) == {"host": "hc-ping.com"}
    # Too short to give four characters of it away.
    assert catalog.fingerprint("capital.login", {**CAPITAL, "api_key": "short"}) is None


def test_the_old_places_are_the_file_and_the_environment(config):
    assert catalog.old_value("telegram.bot", config, {}) == {"token": "999:old-token-from-the-file-xxxxxxxxxxxxxxx", "chat_id": "1"}
    environ = {"CAPITAL_API_KEY": "k" * 16, "CAPITAL_IDENTIFIER": "me@example.com", "CAPITAL_PASSWORD": "p",
               "WATA_HEARTBEAT_URL": " https://hc-ping.com/x "}
    assert catalog.old_value("capital.login", config, environ) == {"api_key": "k" * 16, "identifier": "me@example.com", "password": "p"}
    assert catalog.old_value("capital.login", config, {**environ, "CAPITAL_PASSWORD": ""}) is None
    assert catalog.old_value("heartbeat.url", config, environ) == {"url": "https://hc-ping.com/x"}


# ── what a service reads at its start ──


def test_a_service_opens_its_rows_lays_them_over_the_configuration_and_hides_them_in_the_logs(config, key_root, caplog):
    db = Database({"telegram.bot": sealed_row(key_root, "telegram.bot", TELEGRAM)})
    caplog.set_level(logging.INFO)

    found = load(config, "trader", db, key_root)

    assert found["telegram.bot"] == TELEGRAM
    # Its Saxo app has no row: config.json's, at its old place.
    assert set(found) == {"telegram.bot", "saxo.live.app"} and loader.source("saxo.live.app") == loader.OLD_PLACE
    assert config.get_config_value("telegram.bot_token") == TELEGRAM["token"]
    assert config.get_config_value("telegram.chat_id") == "4242"
    assert loader.credential("telegram.bot") == TELEGRAM and loader.source("telegram.bot") == loader.DATABASE
    assert redact(f"https://api.telegram.org/bot{TELEGRAM['token']}/sendMessage") == "https://api.telegram.org/bot***/sendMessage"
    assert db.closed and TELEGRAM["token"] not in caplog.text
    assert "telegram.bot from the database" in caplog.text


def test_a_row_absent_is_read_at_its_old_place_and_said(config, key_root, caplog):
    found = load(config, "trader", Database(), key_root)

    assert found["telegram.bot"]["token"].startswith("999:old-token")
    assert loader.source("telegram.bot") == loader.OLD_PLACE
    assert "Telegram bot is not in PostgreSQL yet: read from config.json" in caplog.text


def test_a_table_not_created_yet_is_rows_absent(config, key_root):
    load(config, "trader", Database(failing=MissingTable()), key_root)

    assert loader.source("telegram.bot") == loader.OLD_PLACE


def test_a_row_that_cannot_be_opened_is_never_read_elsewhere(config, key_root, caplog):
    other = keys.public_keys(str(key_root))["admin"]
    # Sealed for the admin alone: the trader cannot open it.
    db = Database({"telegram.bot": {"public": {}, "sealed": seal("telegram.bot", TELEGRAM, {"admin": other}),
                                    "readers": ["admin"], "fingerprint": None, "set_at": None, "set_by": None}})

    found = load(config, "trader", db, key_root)

    assert "telegram.bot" not in found and loader.credential("telegram.bot") is None
    assert loader.source("telegram.bot") == loader.UNREADABLE
    # The file's token is not laid over it.
    assert "cannot be opened by the trader service" in caplog.text


def test_a_service_reads_only_what_it_is_a_reader_of(config, key_root):
    db = Database({"capital.login": sealed_row(key_root, "capital.login", CAPITAL),
                   "telegram.bot": sealed_row(key_root, "telegram.bot", TELEGRAM)})

    assert set(load(config, "capital_feed", db, key_root)) == {"capital.login", "telegram.bot"}
    loader.forget()
    assert set(load(config, "trader", db, key_root)) == {"telegram.bot", "saxo.live.app"}
    loader.forget()
    # Nothing to read: not even a connection.
    assert load(config, "control", None, None) == {}


def test_an_unreachable_database_is_waited_for_and_given_up_when_asked(config):
    waits = []

    async def sleep(seconds):
        waits.append(seconds)

    async def connect(dsn):
        raise ConnectionRefusedError()

    with pytest.raises(loader.CredentialsUnavailable):
        asyncio.run(loader.load(config, "trader", connect=connect))
    assert asyncio.run(loader.load_waiting(config, "trader", connect=connect, sleep=sleep, give_up_after=10)) == {}
    # 2 s, then 5 s; the next wait (10 s) would pass the 10 s given.
    assert sum(waits) == 7
    waits.clear()
    assert asyncio.run(loader.load_waiting(config, "trader", connect=connect, sleep=sleep, stopped=lambda: True)) == {}
    assert waits == []


def test_a_check_is_written_when_it_changes_and_every_ten_minutes_otherwise():
    written = []
    now = [0.0]

    async def write(name, role, ok, detail):
        written.append((name, role, ok, detail))

    async def go():
        reporter = loader.CheckReporter(write, "position_monitor", clock=lambda: now[0])
        for moment, ok in ((0, True), (60, True), (120, False), (180, False), (800, False)):
            now[0] = moment
            reporter.report("heartbeat.url", ok, "Answered 200" if ok else "No answer")
            await asyncio.sleep(0)

    asyncio.run(go())

    assert written == [
        ("heartbeat.url", "position_monitor", True, "Answered 200"),
        ("heartbeat.url", "position_monitor", False, "No answer"),
        ("heartbeat.url", "position_monitor", False, "No answer"),
    ]


# ── writing one ──


def test_a_credential_is_written_sealed_for_its_readers_alone(key_root):
    db = Database()

    assert asyncio.run(put(db, "capital.login", CAPITAL, by="10.0.0.1", keys_dir=str(key_root))) is True

    row = db.rows["capital.login"]
    assert row["readers"] == ["capital_feed"] and list(row["sealed"]["readers"]) == ["capital_feed"]
    assert row["public"] == {"identifier": "me@example.com"} and row["fingerprint"] == "7890"
    assert CAPITAL["password"] not in json.dumps(row, default=str) and CAPITAL["api_key"] not in json.dumps(row, default=str)
    # The tables made first, should the admin write before any trading service started.
    assert "CREATE TABLE IF NOT EXISTS credential" in db.executed[0][0]
    # The migration writes only what has no row.
    assert asyncio.run(put(db, "capital.login", {**CAPITAL, "password": "other"}, by="migration",
                           keys_dir=str(key_root), only_if_missing=True)) is False


def test_nothing_is_written_while_a_reader_has_no_key(tmp_path):
    root = tmp_path / "keys"
    keys.generate(str(root), roles=("web_server",))

    with pytest.raises(keys.KeyProblem, match="No public key for admin"):
        asyncio.run(put(Database(), "webhook.token", {"token": "t" * 43}, by=None, keys_dir=str(root)))


# ── the web server's tokens ──


def test_a_rotated_token_keeps_the_one_before_for_a_while():
    first = tokens.rotated(None, datetime(2026, 10, 24, 8, tzinfo=timezone.utc))
    second = tokens.rotated(first, datetime(2026, 10, 24, 9, tzinfo=timezone.utc))

    assert re.match(r"^[A-Za-z0-9_-]{43}$", second["token"]) and second["previous"] == first["token"]
    at = datetime(2026, 10, 24, 9, tzinfo=timezone.utc)
    assert tokens.accepted("internal.token", second, at + timedelta(minutes=14)) == (second["token"], first["token"])
    assert tokens.accepted("internal.token", second, at + timedelta(minutes=16)) == (second["token"],)
    assert tokens.accepted("webhook.token", second, at + timedelta(minutes=59)) == (second["token"], first["token"])
    assert tokens.accepted("webhook.token", None) == ()
    assert tokens.accepted("webhook.token", {"token": "x" * 43}) == ("x" * 43,)
