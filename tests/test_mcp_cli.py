"""
``watamcptoken``: the command line that makes, lists and revokes the MCP
endpoint's tokens, kept in PostgreSQL (here a table in memory).
"""

import asyncio
import json

import pytest

from src.admin import cli
from src.admin.context import AdminSettings
from src.admin.mcp_server.tokens import TOKEN_FORMAT
from src.admin.mcp_token_store import DbTokenStore
from tests.mcp_token_table import TokenTable


@pytest.fixture
def settings(tmp_path):
    return AdminSettings(
        config_path=str(tmp_path / "config.json"), log_path=str(tmp_path / "log"), timezone="Europe/Paris",
        mcp_tokens_path=str(tmp_path / "admin" / "mcp_tokens.json"),
    )


@pytest.fixture
def table(monkeypatch):
    found = TokenTable()
    real = cli.mcp_token

    def mcp_token(settings, words, days):
        async def connect():
            return found

        return real(settings, words, days, connect=connect)

    monkeypatch.setattr(cli, "mcp_token", mcp_token)
    return found


def verify(table, token):
    async def check():
        store = DbTokenStore(reader=table, writer=table)
        await store.refresh()
        return store.verify(token)

    return asyncio.run(check())


def audit_entries(settings):
    with open(f"{settings.log_path}/wata-admin/audit.jsonl") as audit_file:
        return [json.loads(line) for line in audit_file]


def test_create_prints_the_token_once_and_records_who_made_it(settings, table, capsys):
    cli.mcp_token(settings, ["create", "claude-mac"], 30)

    printed = capsys.readouterr().out
    [token] = [word for word in printed.split() if TOKEN_FORMAT.match(word)]
    assert verify(table, token).name == "claude-mac"
    # PostgreSQL keeps its sha256, never the token.
    assert token not in json.dumps(table.rows, default=str) and table.closed
    assert "admin.mcp_enabled" in printed
    [entry] = audit_entries(settings)
    assert (entry["ip"], entry["action"]) == ("console", "MCP token created")
    assert token not in json.dumps(entry)


def test_list_shows_the_tokens_never_their_value(settings, table, capsys):
    cli.mcp_token(settings, ["create", "claude-mac"], 30)
    token = [word for word in capsys.readouterr().out.split() if TOKEN_FORMAT.match(word)][0]

    cli.mcp_token(settings, ["list"], 30)

    printed = capsys.readouterr().out
    assert "claude-mac" in printed and "active" in printed
    assert token not in printed


def test_revoke_closes_the_endpoint_to_that_token(settings, table, capsys):
    cli.mcp_token(settings, ["create", "claude-mac"], 30)
    token = [word for word in capsys.readouterr().out.split() if TOKEN_FORMAT.match(word)][0]

    cli.mcp_token(settings, ["revoke", "claude-mac"], 30)

    assert "revoked" in capsys.readouterr().out
    assert verify(table, token) is None
    assert [entry["action"] for entry in audit_entries(settings)] == ["MCP token created", "MCP token revoked"]


@pytest.mark.parametrize("words", [["create"], ["delete", "claude-mac"], ["list", "extra"], ["revoke"]])
def test_anything_else_says_how_it_is_used(settings, table, capsys, words):
    with pytest.raises(SystemExit) as stopped:
        cli.mcp_token(settings, words, 30)

    assert stopped.value.code == 2
    assert "--mcp-token create NAME" in capsys.readouterr().out


def test_a_refusal_is_said_and_ends_with_an_error(settings, table, capsys):
    with pytest.raises(SystemExit) as stopped:
        cli.mcp_token(settings, ["create", "Claude Mac"], 30)

    assert stopped.value.code == 1
    assert "lowercase" in capsys.readouterr().out
