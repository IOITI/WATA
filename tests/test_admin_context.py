import asyncio
import json
import logging
import os
import runpy
import sys
import types
from unittest.mock import AsyncMock, patch

import pytest

asyncpg_stub = types.ModuleType("asyncpg")
asyncpg_stub.Pool = type("Pool", (), {})
asyncpg_stub.Record = dict
asyncpg_stub.create_pool = AsyncMock()
sys.modules.setdefault("asyncpg", asyncpg_stub)
sys.modules.setdefault("aio_pika", types.ModuleType("aio_pika"))

from src.admin.context import AdminSettings, build_context
from src.configuration import ConfigurationManager


def _write_config(tmp_path, admin=None, postgresql=None):
    with open("tests/test_config.json") as source:
        config = json.load(source)
    config["logging"]["persistant"]["log_path"] = str(tmp_path / "log")
    if admin is not None:
        config["admin"] = admin
    if postgresql is not None:
        config["postgresql"] = postgresql
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    return str(path)


def test_settings_default_to_a_locked_down_setup_when_the_section_is_absent(tmp_path):
    settings = AdminSettings.from_config(ConfigurationManager(_write_config(tmp_path)))

    assert settings.api_port == 8082
    assert settings.cookie_secure is True
    assert settings.credentials_path == "/app/var/lib/admin/credentials.json"
    assert settings.session_idle_minutes == 60
    assert settings.session_max_age_minutes == 720
    assert settings.timezone == "Europe/Paris"
    assert settings.log_path == str(tmp_path / "log")
    # Only the reverse proxy of the compose file says where a request comes from.
    assert settings.trusted_proxy_hosts == ("traefik",)


def test_settings_follow_the_admin_section(tmp_path):
    config_path = _write_config(tmp_path, admin={
        "api_port": 9000,
        "actions_enabled": False,
        "config_edit_enabled": False,
        "session_idle_minutes": 5,
        "persistant": {"credentials_path": str(tmp_path / "credentials.json")},
    })

    settings = AdminSettings.from_config(ConfigurationManager(config_path))

    assert settings.api_port == 9000
    assert settings.actions_enabled is False
    assert settings.config_edit_enabled is False
    assert settings.session_idle_minutes == 5
    assert settings.credentials_path == str(tmp_path / "credentials.json")
    assert settings.config_path == config_path


@pytest.mark.parametrize("configured, expected", [
    (["proxy1", "10.0.0.0/24"], ("proxy1", "10.0.0.0/24")),
    ("proxy1", ("proxy1",)),
    ([], ()),
])
def test_trusted_proxies_follow_the_admin_section(tmp_path, configured, expected):
    config_path = _write_config(tmp_path, admin={"trusted_proxy_hosts": configured})

    settings = AdminSettings.from_config(ConfigurationManager(config_path))
    ctx = build_context(ConfigurationManager(config_path))

    assert settings.trusted_proxy_hosts == expected
    assert ctx.trusted_proxies.is_trusted("10.0.0.5") is ("10.0.0.0/24" in expected)
    asyncio.run(ctx.close())


def test_a_malformed_list_of_trusted_proxies_stops_the_admin_from_starting(tmp_path):
    config_path = _write_config(tmp_path, admin={"trusted_proxy_hosts": [{"host": "traefik"}]})

    with pytest.raises(ValueError, match="admin.trusted_proxy_hosts"):
        AdminSettings.from_config(ConfigurationManager(config_path))


def test_production_context_reads_postgresql_through_a_small_read_only_pool(tmp_path):
    dsn = "postgresql://wata:secret@postgres1:5432/wata"
    ctx = build_context(ConfigurationManager(_write_config(tmp_path, postgresql={"dsn": dsn, "pool_max_size": 10})))
    pool = AsyncMock()
    pool.fetchval.return_value = 1

    async def scenario():
        with patch("src.database.postgres.asyncpg.create_pool", new=AsyncMock(return_value=pool)) as create_pool:
            assert await ctx.queries.ping() is True
            assert await ctx.queries.ping() is True
        await ctx.close()
        return create_pool

    create_pool = asyncio.run(scenario())

    create_pool.assert_awaited_once()
    assert create_pool.await_args.args == (dsn,)
    assert create_pool.await_args.kwargs["server_settings"] == {"default_transaction_read_only": "on"}
    # Its own small pool, whatever the trading services are configured with.
    assert create_pool.await_args.kwargs["max_size"] == 3
    pool.close.assert_awaited_once()


def test_pause_is_written_through_a_separate_connection_that_is_not_read_only(tmp_path):
    dsn = "postgresql://wata:secret@postgres1:5432/wata"
    ctx = build_context(ConfigurationManager(_write_config(tmp_path, postgresql={"dsn": dsn})))
    read_pool, write_pool = AsyncMock(), AsyncMock()
    read_pool.fetchval.return_value = 1

    async def scenario():
        create_pool = AsyncMock(side_effect=[read_pool, write_pool])
        with patch("src.database.postgres.asyncpg.create_pool", new=create_pool):
            await ctx.queries.ping()
            await ctx.risk_state.pause_entries("2026-10-12")
            await ctx.risk_state.resume_entries()
        await ctx.close()
        return create_pool

    create_pool = asyncio.run(scenario())

    assert create_pool.await_count == 2
    read_options, write_options = (call.kwargs for call in create_pool.await_args_list)
    assert read_options["server_settings"] == {"default_transaction_read_only": "on"}
    assert write_options["server_settings"] is None
    # Opened only when the operator pauses, and one connection at most.
    assert (write_options["min_size"], write_options["max_size"]) == (0, 1)
    read_pool.execute.assert_not_awaited()
    statements = [call.args[0] for call in write_pool.execute.await_args_list]
    assert len(statements) == 2
    assert all("trade_risk_state" in statement for statement in statements)
    assert ctx.audit_log.path == str(tmp_path / "log" / "wata-admin" / "audit.jsonl")


def test_production_context_does_not_touch_tokens_until_they_are_needed(tmp_path):
    with patch("src.web_server_token.WebServerToken") as web_server_token, \
            patch("src.saxo_authen.SaxoAuth") as saxo_auth:
        ctx = build_context(ConfigurationManager(_write_config(tmp_path)))

    web_server_token.assert_not_called()
    saxo_auth.assert_not_called()
    assert not ctx.credentials.is_configured()
    asyncio.run(ctx.close())


@pytest.fixture
def restore_root_logging():
    root_logger = logging.getLogger()
    handlers, level = list(root_logger.handlers), root_logger.level
    yield
    for handler in root_logger.handlers:
        if handler not in handlers:
            handler.close()
    root_logger.handlers[:] = handlers
    root_logger.setLevel(level)


def test_entrypoint_starts_the_server_on_the_configured_address(tmp_path, monkeypatch, restore_root_logging):
    config_path = _write_config(tmp_path, admin={"api_host": "127.0.0.1", "api_port": 9000})
    monkeypatch.setenv("WATA_CONFIG_PATH", config_path)

    with patch("uvicorn.run") as run:
        runpy.run_path("src/admin/__init__.py", run_name="__main__")

    run.assert_called_once()
    # The wait for requests in progress is bounded: a page left open must not keep the container from stopping.
    assert run.call_args.kwargs == {"host": "127.0.0.1", "port": 9000, "timeout_graceful_shutdown": 5}
    app = run.call_args.args[0]
    assert any(route.path == "/api/session" for route in app.routes)
    assert os.path.exists(tmp_path / "log" / "wata-admin" / "wata-admin.log")


def test_entrypoint_refuses_to_start_without_a_config_path(monkeypatch):
    monkeypatch.delenv("WATA_CONFIG_PATH", raising=False)

    with patch("uvicorn.run") as run, pytest.raises(RuntimeError):
        runpy.run_path("src/admin/__init__.py", run_name="__main__")

    run.assert_not_called()
