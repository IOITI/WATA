"""
The credentials as the admin handles them (src/admin/vault.py,
src/admin/routers/credentials.py) and the MCP tokens in PostgreSQL
(src/admin/mcp_token_store.py).
"""

import asyncio
import json
import os

import httpx
import pytest

from src.admin.errors import InvalidRequest, NotPossible
from src.admin.mcp_server.tokens import TokenError
from src.admin.mcp_token_store import DbTokenStore
from src.admin.vault import AdminVault
from src.vault import keys
from src.vault.sealed import open_sealed
from tests.mcp_token_table import TokenTable
from tests.test_admin_api import PASSWORD, SAME_ORIGIN, config_path, make_client, make_ctx  # noqa: F401 (fixtures)
from tests.test_vault_loader import CAPITAL, TELEGRAM, Database

TOKEN_FILE_TOKEN = "file-token-of-before-0123456789abcdef"


@pytest.fixture
def key_root(tmp_path):
    root = tmp_path / "keys"
    keys.generate(str(root))
    # The admin's container: its own key and the public ones.
    view = tmp_path / "admin-keys"
    (view / "own").mkdir(parents=True)
    (view / "own" / "private.key").write_bytes((root / "admin" / "private.key").read_bytes())
    (view / "public").symlink_to(root / "public")
    return root, view


def vault_with(db, config, view, feed_state=None):
    return AdminVault(reader=db, writer=db, config_manager=config, keys_dir=str(view), feed_state=feed_state)


@pytest.fixture
def config(tmp_path):
    from src.configuration import ConfigurationManager

    with open("tests/test_config.json") as source:
        data = json.load(source)
    data["telegram"] = {"bot_token": "999:old-token-from-the-file-xxxxxxxxxxxxxxx", "chat_id": "1"}
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data))
    return ConfigurationManager(str(path))


def test_the_list_says_what_is_set_and_never_a_value(config, key_root):
    root, view = key_root
    db = Database()
    vault = vault_with(db, config, view)
    asyncio.run(vault.set("capital.login", CAPITAL, by="10.0.0.1"))

    listed = asyncio.run(vault.listed())

    by_name = {item["name"]: item for item in listed["items"]}
    assert listed["keys"] is None
    assert by_name["capital.login"]["where"] == "database"
    assert by_name["capital.login"]["public"] == {"identifier": "me@example.com"}
    assert by_name["capital.login"]["fingerprint"] == "7890" and by_name["capital.login"]["set_by"] == "10.0.0.1"
    # The bot is still in config.json, which the admin reads; the heartbeat's place is another container's .env.
    assert by_name["telegram.bot"]["where"] == "old place"
    assert by_name["heartbeat.url"]["where"] == "unknown"
    # Saxo's app of the account in use is still config.json's; the other account's never was anywhere.
    assert by_name["saxo.live.app"]["where"] == "old place"
    assert by_name["saxo.simulation.app"]["where"] == "missing"
    said = json.dumps(listed, default=str)
    assert CAPITAL["password"] not in said and CAPITAL["api_key"] not in said
    assert [part["key"] for part in by_name["telegram.bot"]["parts"] if part["secret"]] == ["token"]


def test_what_is_set_is_sealed_for_its_readers_alone_and_the_admin_cannot_open_it(config, key_root):
    root, view = key_root
    db = Database()
    vault = vault_with(db, config, view)

    result = asyncio.run(vault.set("capital.login", CAPITAL, by=None))

    assert result["services"] == ["capital_feed"]
    envelope = db.rows["capital.login"]["sealed"]
    assert open_sealed("capital.login", envelope, "capital_feed", keys.private_key_at(str(root / "capital_feed" / "private.key"))) == CAPITAL
    with pytest.raises(NotPossible, match="does not read"):
        asyncio.run(vault.open("capital.login"))


def test_a_value_not_as_expected_is_refused_before_anything_is_written(config, key_root):
    _, view = key_root
    db = Database()
    vault = vault_with(db, config, view)

    with pytest.raises(InvalidRequest, match="bot token is not as expected"):
        asyncio.run(vault.set("telegram.bot", {"token": "nope", "chat_id": "1"}, by=None))
    with pytest.raises(InvalidRequest, match="rotate it"):
        asyncio.run(vault.set("webhook.token", {"token": "x" * 43}, by=None))
    assert db.rows == {}


def test_a_token_is_rotated_the_one_before_kept_for_a_while(config, key_root, monkeypatch):
    _, view = key_root
    db = Database()
    vault = vault_with(db, config, view)
    monkeypatch.setattr("src.vault.credentials.old_value", lambda name, *_: {"token": TOKEN_FILE_TOKEN})

    first = asyncio.run(vault.rotate("webhook.token", by=None))
    opened = asyncio.run(vault.open("webhook.token"))

    # The token of the file, as TradingView's alerts carry it today, stays accepted while they are changed.
    assert opened["previous"] == TOKEN_FILE_TOKEN and first["previous_accepted_seconds"] == 3600
    second = asyncio.run(vault.rotate("webhook.token", by=None))
    assert asyncio.run(vault.open("webhook.token"))["previous"] == opened["token"]
    # The admin opens the token when it uses it: nothing of it to restart.
    assert second["services"] == ["web_server"]
    with pytest.raises(InvalidRequest, match="replace it"):
        asyncio.run(vault.rotate("telegram.bot", by=None))


def test_nothing_is_sealed_without_the_keys(config, tmp_path):
    vault = vault_with(Database(), config, tmp_path / "no-keys")

    assert "install script" in vault.keys_said()
    with pytest.raises(NotPossible, match="install script"):
        asyncio.run(vault.set("telegram.bot", TELEGRAM, by=None))


def test_the_feeds_own_state_says_whether_its_login_works(config, key_root):
    _, view = key_root

    async def feed_state():
        return {"state": "no_credentials", "detail": "No Capital.com login", "updated_at": "2026-10-24T08:00:00+00:00"}

    listed = asyncio.run(vault_with(Database(), config, view, feed_state).listed())

    [capital] = [item for item in listed["items"] if item["name"] == "capital.login"]
    assert capital["checks"] == [{"role": "capital_feed", "checked_at": "2026-10-24T08:00:00+00:00", "ok": False,
                                  "detail": "no_credentials: No Capital.com login"}]


# ── the routes ──


@pytest.fixture
def ctx(config_path, tmp_path, key_root):
    _, view = key_root
    found = make_ctx(config_path, tmp_path)
    found.vault = AdminVault(reader=Database(), writer=None, config_manager=found.config_manager, keys_dir=str(view))
    database = Database()
    found.vault._reader = found.vault._writer = database
    return found


def signed_in(ctx, tmp_path):
    client = make_client(ctx, tmp_path / "no-interface")
    client.__enter__()
    assert client.post("/api/session", json={"password": PASSWORD}, headers=SAME_ORIGIN).status_code == 204
    return client


def test_a_credential_set_is_audited_by_its_name_and_end_never_its_value(ctx, tmp_path):
    client = signed_in(ctx, tmp_path)
    try:
        answer = client.put("/api/credentials/telegram.bot", json={"parts": TELEGRAM}, headers=SAME_ORIGIN)
        refused = client.put("/api/credentials/telegram.bot", json={"parts": {"token": "x"}}, headers=SAME_ORIGIN)
        listed = client.get("/api/credentials").json()
    finally:
        client.__exit__(None, None, None)

    assert answer.status_code == 200 and answer.json()["services"][0] == "telegram"
    assert refused.status_code == 422
    entry = ctx.audit_log.read(5)[0]
    assert entry["action"] == "credential set" and entry["detail"].startswith("Telegram bot, ending …fghi; read by telegram")
    assert TELEGRAM["token"] not in json.dumps(ctx.audit_log.read(5)) and TELEGRAM["token"] not in json.dumps(listed)


def test_the_credentials_are_not_changed_with_the_editing_switched_off(config_path, tmp_path, key_root):
    _, view = key_root
    off = make_ctx(config_path, tmp_path, config_edit_enabled=False)
    off.vault = AdminVault(reader=Database(), writer=Database(), config_manager=off.config_manager, keys_dir=str(view))
    client = signed_in(off, tmp_path)
    try:
        assert client.put("/api/credentials/telegram.bot", json={"parts": TELEGRAM}, headers=SAME_ORIGIN).status_code == 403
        assert client.post("/api/credentials/internal.token/rotate", headers=SAME_ORIGIN).status_code == 403
    finally:
        client.__exit__(None, None, None)


def test_the_webhooks_address_is_shown_on_request_and_audited(ctx, tmp_path, monkeypatch):
    monkeypatch.setenv("WATA_API_DOMAIN", "api.example.com")
    # No token file of before either.
    monkeypatch.setattr("src.vault.credentials.old_value", lambda *_: None)
    client = signed_in(ctx, tmp_path)
    try:
        none_yet = client.post("/api/credentials/webhook.token/show", headers=SAME_ORIGIN)
        client.post("/api/credentials/webhook.token/rotate", headers=SAME_ORIGIN)
        shown = client.post("/api/credentials/webhook.token/show", headers=SAME_ORIGIN).json()
    finally:
        client.__exit__(None, None, None)

    assert none_yet.status_code == 404
    assert shown["url"] == f"https://api.example.com/webhook?token={shown['token']}"
    assert [entry["action"] for entry in ctx.audit_log.read(5)][:2] == ["webhook address shown", "token rotated"]


def test_the_telegram_bot_is_tried_with_its_stored_value(ctx, tmp_path, monkeypatch):
    sent = []

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json):
            sent.append((url, json))
            return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr("src.admin.routers.credentials.httpx.AsyncClient", Client)
    client = signed_in(ctx, tmp_path)
    try:
        client.put("/api/credentials/telegram.bot", json={"parts": TELEGRAM}, headers=SAME_ORIGIN)
        tried = client.post("/api/credentials/telegram.bot/test", headers=SAME_ORIGIN).json()
    finally:
        client.__exit__(None, None, None)

    assert tried == {"ok": True, "detail": "Sent"}
    assert sent[0][0] == f"https://api.telegram.org/bot{TELEGRAM['token']}/sendMessage" and sent[0][1]["chat_id"] == "4242"
    assert ctx.audit_log.read(1)[0]["detail"] == "Sent"


def test_the_claude_api_key_is_set_without_a_restart_and_tried_with_its_stored_value(ctx, tmp_path, monkeypatch):
    tried_with = []

    async def check_key(key, model):
        tried_with.append((key, model))
        return True, "Works: Claude Opus 5.5 answers"

    monkeypatch.setattr("src.admin.agent.provider.check_key", check_key)
    key = "sk-ant-api03-" + "x" * 40
    client = signed_in(ctx, tmp_path)
    try:
        missing = [item for item in client.get("/api/credentials").json()["items"] if item["name"] == "anthropic.api"]
        refused = client.put("/api/credentials/anthropic.api", json={"parts": {"key": "not-a-key"}}, headers=SAME_ORIGIN)
        saved = client.put("/api/credentials/anthropic.api", json={"parts": {"key": key}}, headers=SAME_ORIGIN).json()
        tried = client.post("/api/credentials/anthropic.api/test", headers=SAME_ORIGIN).json()
        listed = [item for item in client.get("/api/credentials").json()["items"] if item["name"] == "anthropic.api"]
    finally:
        client.__exit__(None, None, None)

    assert missing[0]["where"] == "missing" and missing[0]["readers"] == ["admin"]
    assert refused.status_code == 422 and key not in refused.text
    # The agent opens it at each request: nothing has to start again.
    assert saved["services"] == []
    assert tried == {"ok": True, "detail": "Works: Claude Opus 5.5 answers"}
    assert tried_with == [(key, "claude-opus-5-5")]
    assert listed[0]["where"] == "database" and listed[0]["fingerprint"] == "xxxx"


# ── the MCP tokens in PostgreSQL ──


def store_with(table, legacy=None, clock=None):
    kwargs = {"clock": clock} if clock else {}
    return DbTokenStore(reader=table, writer=table, legacy_path=legacy, **kwargs)


def test_a_token_made_is_kept_as_its_digest_and_opens_the_endpoint():
    table = TokenTable()

    async def go():
        store = store_with(table)
        await store.refresh()
        token, entry = await store.create("claude-mac", 30)
        return store, token, entry

    store, token, entry = asyncio.run(go())

    assert store.verify(token).name == "claude-mac"
    assert store.verify(token[:-1] + ("A" if token[-1] != "A" else "B")) is None
    assert token not in json.dumps(table.rows, default=str)
    assert [listed["state"] for listed in store.entries()] == ["active"] and "sha256" not in store.entries()[0]


def test_a_token_revoked_or_expired_opens_nothing_and_names_are_unique_while_valid():
    table = TokenTable()
    now = [1_800_000_000.0]

    async def go():
        store = store_with(table, clock=lambda: now[0])
        await store.refresh()
        token, _ = await store.create("claude-mac", 1)
        with pytest.raises(TokenError, match="still valid"):
            await store.create("claude-mac", 1)
        await store.revoke("claude-mac")
        revoked = store.verify(token)
        again, _ = await store.create("claude-mac", 1)
        now[0] += 2 * 86_400
        await store.refresh()
        return revoked, store.verify(again)

    assert asyncio.run(go()) == (None, None)


def test_a_table_that_cannot_be_read_lets_no_token_in():
    table = TokenTable()

    async def go():
        store = store_with(table)
        await store.refresh()
        token, _ = await store.create("claude-mac", 30)
        table.failing = ConnectionError("down")
        await store.refresh()
        return store, token

    store, token = asyncio.run(go())

    assert store.readable() is False and store.verify(token) is None and store.entries() == []


def test_the_file_of_before_is_moved_in_once_then_put_aside(tmp_path):
    from src.admin.mcp_server.tokens import TokenStore

    legacy = tmp_path / "mcp_tokens.json"
    token, _ = TokenStore(str(legacy)).create("old-mac", 30)
    table = TokenTable()

    async def go():
        store = store_with(table, legacy=str(legacy))
        await store.refresh()
        return store

    store = asyncio.run(go())

    assert store.verify(token).name == "old-mac"
    assert not legacy.exists() and (tmp_path / "mcp_tokens.json.imported").exists()


def test_a_tokens_last_use_is_written_at_most_once_a_minute():
    table = TokenTable()
    now = [1_800_000_000.0]

    async def go():
        store = store_with(table, clock=lambda: now[0])
        await store.refresh()
        token, entry = await store.create("claude-mac", 30)
        for step in (0, 10, 70):
            now[0] += step
            store.verify(token)
            await asyncio.sleep(0)
        return entry["id"]

    token_id = asyncio.run(go())

    assert table.rows[token_id]["last_used_at"].timestamp() == 1_800_000_080.0


# ── what the admin writes ──


def test_the_admin_writes_five_tables_and_no_other():
    import re
    from pathlib import Path

    # Its own code, and the vault's that it calls (the migration is a tool of the server's, not the admin's).
    paths = [*Path("src/admin").rglob("*.py"), Path("src/vault/store.py"), Path("src/vault/writer.py")]
    written = set()
    for path in paths:
        written |= set(re.findall(r"(?:INSERT INTO|UPDATE|DELETE FROM)\s+([a-z_]+)", path.read_text()))
    # The pause of new entries is the trading services' own manager (AsyncDbRiskStateManager, trade_risk_state).
    assert written == {"credential", "credential_check", "mcp_token", "saxo_auth_code"}
    context = Path("src/admin/context.py").read_text()
    assert "risk_state=AsyncDbRiskStateManager(pause_database)" in context
    # Through the one writable connection: the pause's.
    assert "AdminVault(reader=database, writer=pause_database" in context
    assert "DbTokenStore(reader=database, writer=pause_database" in context
    assert "AdminSaxo(reader=database, writer=pause_database" in context
