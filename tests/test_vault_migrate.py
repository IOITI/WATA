"""The credentials moved from their places of before into PostgreSQL, and the old places cleared (src/vault/migrate.py)."""

import asyncio
import json

import pytest

from src.configuration import ConfigurationManager
from src.vault import keys, migrate
from tests.test_vault_loader import CAPITAL, TELEGRAM, Database

SAXO_APP = {"app_key": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6", "app_secret": "s3cr3ts3cr3ts3cr3ts3cr3ts3cr3t00"}
SAXO_TOKENS = {"access_token": "access-0123", "refresh_token": "refresh-0123", "expires_in": 1200,
               "refresh_token_expires_in": 3600, "date_saved": "2026-10-24T08:00:00"}


@pytest.fixture
def server(tmp_path):
    """A server of v0.42: credentials in config.json, the token file, .env; the keys made."""
    with open("tests/test_config.json") as source:
        config = json.load(source)
    config["telegram"] = {"bot_token": TELEGRAM["token"], "chat_id": TELEGRAM["chat_id"], "bot_name": "x"}
    token_folder = tmp_path / "web_server"
    config["webserver"]["persistant"]["token_path"] = str(token_folder / "persist_token.json")
    config["authentication"]["saxo"]["app_config_object"].update(AppKey=SAXO_APP["app_key"], AppSecret=SAXO_APP["app_secret"])
    config["authentication"]["persistant"]["token_path"] = str(tmp_path / "saxo_auth" / "persist_token.json")
    config.setdefault("admin", {}).setdefault("persistant", {})["mcp_tokens_path"] = str(tmp_path / "admin" / "mcp_tokens.json")
    etc = tmp_path / "etc"
    etc.mkdir()
    (etc / "config.json").write_text(json.dumps(config))
    (etc / "config.json.bak-20261020-101010").write_text(json.dumps(config))
    (etc / ".config.candidate-0123456789abcdef.json").write_text("{}")
    manager = ConfigurationManager(str(etc / "config.json"))
    from src.web_server_token import WebServerToken

    WebServerToken(manager).save_token_data("the-webhook-token-0123456789abcdefgh")
    # Saxo's token file, encrypted as SaxoAuth of before wrote it.
    (tmp_path / "saxo_auth").mkdir()
    (tmp_path / "saxo_auth" / ".key_salt").write_bytes(b"0123456789abcdef")
    migrate._saxo_file(manager)[1].save(SAXO_TOKENS, 0, by="test")
    (tmp_path / "admin").mkdir()
    (tmp_path / "admin" / "mcp_tokens.json.imported").write_text("{}")
    (tmp_path / ".env").write_text(
        "# WATA\nexport CAPITAL_API_KEY=capitalkey1234567890\nCAPITAL_IDENTIFIER='me@example.com'\n"
        'CAPITAL_PASSWORD="pass word 1"\n\nWATA_HEARTBEAT_URL=https://hc-ping.com/abc\nnot a line\n'
    )
    keys.generate(str(tmp_path / "keys"))
    return tmp_path, manager


def test_env_is_read_as_lines_never_run(server):
    root, _ = server

    assert migrate.read_env(str(root / ".env")) == {
        "CAPITAL_API_KEY": "capitalkey1234567890", "CAPITAL_IDENTIFIER": "me@example.com", "CAPITAL_PASSWORD": "pass word 1",
        "WATA_HEARTBEAT_URL": "https://hc-ping.com/abc",
    }
    assert migrate.read_env(str(root / "nowhere")) == {}


def test_a_dry_run_says_what_would_be_written_and_writes_nothing(server, capsys):
    root, manager = server
    db = Database()

    asyncio.run(migrate.migrate(db, manager, migrate.read_env(str(root / ".env")), dry_run=True, keys_dir=str(root / "keys")))

    printed = capsys.readouterr().out
    assert db.rows == {}
    for name in ("telegram.bot", "webhook.token", "internal.token", "capital.login", "heartbeat.url", "saxo.live.app"):
        assert f"{name}: would be written" in printed
    assert "saxo.live.tokens: would be moved in from" in printed and db.saxo == {}
    assert (root / "saxo_auth" / "persist_token.json").exists()
    assert "pass word 1" not in printed and TELEGRAM["token"] not in printed


def test_each_is_written_once_and_the_internal_token_made_new(server, capsys):
    root, manager = server
    db = Database()
    environ = migrate.read_env(str(root / ".env"))

    asyncio.run(migrate.migrate(db, manager, environ, dry_run=False, keys_dir=str(root / "keys")))
    first = {name: row["sealed"]["data"] for name, row in db.rows.items()}
    asyncio.run(migrate.migrate(db, manager, environ, dry_run=False, keys_dir=str(root / "keys")))

    assert sorted(db.rows) == ["capital.login", "heartbeat.url", "internal.token", "saxo.live.app", "telegram.bot", "webhook.token"]
    # Saxo's tokens moved in under the services' lock, sealed for the four that log in to Saxo, the file put aside.
    assert sorted(db.saxo["live"]["sealed"]["readers"]) == ["position_monitor", "price_recorder", "trader", "watchlist_manager"]
    assert not (root / "saxo_auth" / "persist_token.json").exists()
    assert (root / "saxo_auth" / "persist_token.json.imported").exists()
    # Run again, nothing is replaced.
    assert {name: row["sealed"]["data"] for name, row in db.rows.items()} == first
    assert "in PostgreSQL already, left as it is" in capsys.readouterr().out
    from src.vault.sealed import open_sealed

    def opened(name, role):
        return open_sealed(name, db.rows[name]["sealed"], role, keys.private_key_at(str(root / "keys" / role / "private.key")))

    assert opened("webhook.token", "web_server") == {"token": "the-webhook-token-0123456789abcdefgh"}
    assert opened("internal.token", "trader")["token"] != "the-webhook-token-0123456789abcdefgh"
    assert opened("capital.login", "capital_feed") == CAPITAL
    assert opened("saxo.live.app", "watchlist_manager") == SAXO_APP
    assert open_sealed("saxo.live.tokens", db.saxo["live"]["sealed"], "trader",
                       keys.private_key_at(str(root / "keys" / "trader" / "private.key"))) == SAXO_TOKENS


def test_a_value_found_but_not_as_expected_is_left_for_the_page(server, capsys):
    root, manager = server
    db = Database()

    asyncio.run(migrate.migrate(db, manager, {"WATA_HEARTBEAT_URL": "http://not-https/x"}, dry_run=False,
                                keys_dir=str(root / "keys")))

    assert "heartbeat.url" not in db.rows
    printed = capsys.readouterr().out
    assert "heartbeat.url: found at .env (WATA_HEARTBEAT_URL), but" in printed and "not-https" not in printed


def test_the_old_places_are_cleared_only_once_each_credential_has_its_row(server, capsys):
    root, manager = server
    config_path = str(root / "etc" / "config.json")
    db = Database()

    assert asyncio.run(migrate.purge(db, manager, config_path)) == 1
    assert "not in PostgreSQL yet" in capsys.readouterr().out
    assert TELEGRAM["token"] in (root / "etc" / "config.json").read_text()

    asyncio.run(migrate.migrate(db, manager, migrate.read_env(str(root / ".env")), dry_run=False, keys_dir=str(root / "keys")))
    assert asyncio.run(migrate.purge(db, manager, config_path)) == 0

    for path in (root / "etc" / "config.json", root / "etc" / "config.json.bak-20261020-101010"):
        purged = json.loads(path.read_text())
        assert "bot_token" not in purged["telegram"] and "chat_id" not in purged["telegram"]
        assert "AppSecret" not in purged["authentication"]["saxo"]["app_config_object"]
        assert "app_secret" not in purged["webserver"]
        # What the services still read there stays.
        assert purged["authentication"]["persistant"]["token_path"]
    assert not (root / "saxo_auth" / "persist_token.json.imported").exists()
    assert not (root / "saxo_auth" / ".key_salt").exists()
    assert not (root / "etc" / ".config.candidate-0123456789abcdef.json").exists()
    assert not (root / "web_server" / "persist_token.json").exists()
    assert not (root / "web_server" / ".webserver_key_salt").exists()
    assert not (root / "admin" / "mcp_tokens.json.imported").exists()
    assert "CAPITAL_API_KEY, CAPITAL_IDENTIFIER, CAPITAL_PASSWORD, WATA_HEARTBEAT_URL" in capsys.readouterr().out
