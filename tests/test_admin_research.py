"""
Claude's research as the operator watches it (src/admin/routers/research.py):
the MCP endpoint's state, its tokens and last calls, the pause, the journal's
experiments and the lock box.
"""

import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from src.admin.app import create_app
from src.admin.mcp_server.facade import CLAUDE, journal_of
from src.admin.mcp_server.pause import McpPause
from tests.test_admin_api import ORIGIN, PASSWORD, PROXY, SAME_ORIGIN, config_path  # noqa: F401 (fixtures)
from tests.test_mcp_gate import mcp_ctx

ACCEPT = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
LIST_TOOLS = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}


@pytest.fixture
def ctx(config_path, tmp_path):
    ctx = mcp_ctx(config_path, tmp_path)
    ctx.mcp_pause = McpPause(str(tmp_path / "admin" / "mcp_pause.json"))
    return ctx


@pytest.fixture
def client(ctx, tmp_path):
    with TestClient(create_app(ctx, static_dir=str(tmp_path / "no-interface")), base_url=ORIGIN,
                    client=(PROXY, 50000)) as test_client:
        assert test_client.post("/api/session", json={"password": PASSWORD}, headers=SAME_ORIGIN).status_code == 204
        yield test_client


def test_the_endpoints_state_says_its_tokens_without_their_secret_and_the_last_calls(ctx, client):
    token, entry = ctx.mcp_tokens.create("claude-mac")
    ctx.mcp_calls.record(caller="claude-mac", tool="status", arguments={}, outcome="ok", milliseconds=12)

    found = client.get("/api/mcp").json()

    assert found["enabled"] is True and found["paused"]["paused"] is False
    assert [(item["name"], item["state"]) for item in found["tokens"]] == [("claude-mac", "active")]
    assert token not in json.dumps(found) and "sha256" not in json.dumps(found)
    assert found["calls"][0]["tool"] == "status" and found["limits"]["shadow_rules"] is False
    assert set(found["share"]) >= {"waiting_runs", "runs_today", "scripts", "rules", "study_seconds_last_hour"}


def test_a_token_revoked_from_the_system_page_opens_the_endpoint_no_more_and_is_audited(ctx, client):
    token, entry = ctx.mcp_tokens.create("claude-mac")
    bearer = {**ACCEPT, "Authorization": f"Bearer {token}"}
    assert client.post("/mcp", json=LIST_TOOLS, headers=bearer).status_code == 200

    response = client.post(f"/api/mcp/tokens/{entry['id']}/revoke", headers=SAME_ORIGIN)

    assert response.status_code == 200 and response.json()["revoked"][0]["name"] == "claude-mac"
    assert client.post("/mcp", json=LIST_TOOLS, headers=bearer).status_code == 401
    audited = ctx.audit_log.read(1)[0]
    assert audited["action"] == "MCP token revoked" and entry["id"] in audited["detail"]
    assert client.post(f"/api/mcp/tokens/{entry['id']}/revoke", headers=SAME_ORIGIN).status_code == 409
    # By its id alone: a name is not taken for one.
    assert client.post("/api/mcp/tokens/claude-mac/revoke", headers=SAME_ORIGIN).status_code == 404


def test_the_endpoint_paused_answers_nothing_until_it_is_resumed_and_the_pause_outlives_a_restart(ctx, client, tmp_path):
    token, _ = ctx.mcp_tokens.create("claude-mac")
    bearer = {**ACCEPT, "Authorization": f"Bearer {token}"}

    paused = client.put("/api/mcp/paused", json={"paused": True}, headers=SAME_ORIGIN)
    while_paused = client.post("/mcp", json=LIST_TOOLS, headers=bearer).status_code
    again = McpPause(ctx.mcp_pause.path).paused()
    client.put("/api/mcp/paused", json={"paused": False}, headers=SAME_ORIGIN)

    assert paused.json()["paused"] is True and while_paused == 503 and again is True
    assert client.post("/mcp", json=LIST_TOOLS, headers=bearer).status_code == 200
    assert [entry["action"] for entry in ctx.audit_log.read(2)] == ["MCP endpoint resumed", "MCP endpoint paused"]


def test_a_switch_file_that_cannot_be_read_keeps_the_endpoint_paused(tmp_path):
    path = tmp_path / "mcp_pause.json"
    path.write_text("{not json")

    assert McpPause(str(path)).paused() is True
    assert McpPause(str(tmp_path / "none.json")).paused() is False


def test_the_experiments_are_listed_newest_first_with_the_lock_box(ctx, client):
    journal = journal_of(ctx.settings.backtests_path)
    first = journal.new("EMA cross", "Crosses lead", "ema", made_by=CLAUDE)
    second = journal.new("RSI", "Oversold bounces", "rsi", made_by=CLAUDE)
    journal.record_run(first["experiment"], "20261019-200000-abcdef", kind="run", trials=1)
    journal.set_lockbox(date(2026, 10, 14), set_by=CLAUDE, only_wider=True)

    found = client.get("/api/research/experiments").json()

    assert [entry["id"] for entry in found["experiments"]] == [second["experiment"], first["experiment"]]
    assert found["experiments"][1]["trials"] == 1
    assert found["lockbox"]["from"] == "2026-10-14" and found["lockbox"]["to"] is None


def test_the_operator_moves_or_clears_the_lock_box_as_claude_cannot(ctx, client):
    journal = journal_of(ctx.settings.backtests_path)
    journal.set_lockbox(date(2026, 10, 10), set_by=CLAUDE)

    moved = client.put("/api/research/lockbox", json={"start": "2026-10-16", "end": "2026-10-18"}, headers=SAME_ORIGIN)
    wrong = client.put("/api/research/lockbox", json={"start": "2026-10-18", "end": "2026-10-16"}, headers=SAME_ORIGIN)
    cleared = client.put("/api/research/lockbox", json={"start": None, "end": None}, headers=SAME_ORIGIN)

    assert moved.json()["lockbox"]["from"] == "2026-10-16" and moved.json()["lockbox"]["set_by"] == "operator"
    assert wrong.status_code == 422
    assert cleared.json() == {"lockbox": None} and journal.lockbox() is None
