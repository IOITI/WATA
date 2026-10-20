"""The MCP server as Claude Code meets it: its tools, prompt and instructions, over a real socket."""

import asyncio
import json

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from src.admin.mcp_server.facade import ResearchServices
from src.admin.mcp_server.instructions import INSTRUCTIONS, RESEARCH_PROMPT
from src.admin.mcp_server.server import build_server
from tests.test_admin_api import RunningAdmin, config_path  # noqa: F401 (fixtures)
from tests.test_mcp_gate import mcp_ctx

# The whole list: a tool added or taken away is a decision, made here too.
EXPECTED_TOOLS = {
    "status",
    "data_assets", "data_coverage", "data_bars", "data_signals", "data_trades", "data_execution",
    "pine_list", "pine_get", "pine_check", "pine_run", "pine_signals", "pine_compare", "pine_save", "pine_delete",
    "backtest_options", "backtest_list", "backtest_result", "backtest_trades", "backtest_days", "backtest_signals",
    "backtest_compare", "turbo_model",
    "rule_list", "rule_compare",
    "exp_new", "exp_get", "exp_conclude", "lockbox_set",
    "backtest_start", "backtest_derive", "backtest_wait", "backtest_remove", "turbo_model_measure",
    "rule_shadow_save", "rule_shadow_remove", "rule_set_reference",
}
# What Claude Code keeps of the server's instructions, and a tool description it reads in full.
MAX_INSTRUCTIONS = 4096
MAX_DESCRIPTION = 1500


@pytest.fixture
def ctx(config_path, tmp_path):
    return mcp_ctx(config_path, tmp_path)


@pytest.fixture
def token(ctx):
    token, _ = ctx.mcp_tokens.create("claude-mac")
    return token


def listed(ctx):
    server = build_server(ResearchServices.from_context(ctx))
    return asyncio.run(server.list_tools())


def test_the_server_offers_exactly_its_tools(ctx):
    assert {tool.name for tool in listed(ctx)} == EXPECTED_TOOLS


def test_every_tool_says_what_it_does_and_whether_it_changes_anything(ctx):
    for tool in listed(ctx):
        assert tool.description and len(tool.description) <= MAX_DESCRIPTION, tool.name
        assert tool.title, tool.name
        assert tool.annotations.read_only_hint is not None, tool.name
        assert tool.annotations.open_world_hint is False, tool.name


def test_every_tool_takes_arguments_the_claude_api_accepts(ctx):
    for tool in listed(ctx):
        schema = tool.input_schema
        assert schema["type"] == "object", tool.name
        for combinator in ("anyOf", "oneOf", "allOf"):
            assert combinator not in schema, tool.name
        for name in schema.get("properties", {}):
            assert 1 <= len(name) <= 64 and name.replace("_", "").isalnum(), (tool.name, name)


def test_the_instructions_fit_what_claude_code_keeps_of_them():
    assert len(INSTRUCTIONS) <= MAX_INSTRUCTIONS
    assert INSTRUCTIONS.index("Start with `status`") < 1500


async def _session(url: str, token: str, mode: str) -> dict:
    async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=30) as http:
        async with Client(streamable_http_client(url, http_client=http), mode=mode) as client:
            tools = await client.list_tools()
            status = await client.call_tool("status", {})
            prompts = await client.list_prompts()
            prompt = await client.get_prompt("research", {})
            return {
                "protocol": client.protocol_version,
                "tools": sorted(tool.name for tool in tools.tools),
                "status_error": status.is_error,
                "status": json.loads(status.content[0].text),
                "prompts": [found.name for found in prompts.prompts],
                "prompt": prompt.messages[0].content.text,
            }


@pytest.mark.parametrize("mode,protocol", [("legacy", "2025-11-25"), ("auto", "2026-07-28"), ("2026-07-28", "2026-07-28")])
def test_claude_codes_client_reaches_the_tools_in_either_protocol(ctx, tmp_path, token, mode, protocol):
    with RunningAdmin(ctx, tmp_path / "no-interface") as admin:
        found = asyncio.run(_session(f"{admin.base_url}/mcp", token, mode))

    assert found["protocol"] == protocol
    assert found["tools"] == sorted(EXPECTED_TOOLS)
    assert found["status_error"] is False
    assert found["status"]["server"]["version"] == "9.9.9"
    assert found["status"]["token"]["name"] == "claude-mac"
    assert found["status"]["limits"]["max_waiting_runs"] == 3
    assert found["prompts"] == ["research"]
    assert found["prompt"] == RESEARCH_PROMPT
    assert [call["tool"] for call in ctx.mcp_calls.recent()] == ["status"]
    assert ctx.mcp_calls.recent()[0]["caller"] == "claude-mac"


def test_the_servers_public_name_is_accepted_behind_the_proxy(ctx, tmp_path, token):
    initialize = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
    }
    with RunningAdmin(ctx, tmp_path / "no-interface") as admin:
        response = httpx2.post(
            f"{admin.base_url}/mcp", json=initialize, timeout=10,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream",
                     "Host": "srv.example.net:8443"},
        )

    assert response.status_code == 200
    assert response.json()["result"]["serverInfo"]["name"] == "wata"


async def _research_loop(url: str, token: str, mode: str, request: dict) -> dict:
    """What Claude does: an experiment, a run for it, waiting for it, reading what it found."""
    async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=30) as http:
        async with Client(streamable_http_client(url, http_client=http), mode=mode) as client:
            async def tool(name, arguments):
                result = await client.call_tool(name, arguments)
                assert not result.is_error, result.content[0].text
                return json.loads(result.content[0].text)

            experiment = await tool("exp_new", {"title": "EMA", "hypothesis": "Crosses point the way", "family": "ema"})
            made = await tool("backtest_start", {"experiment_id": experiment["id"], "request": request})
            waited = await tool("backtest_wait", {"run_ids": [made["id"]], "seconds": 20})
            found = await tool("backtest_result", {"run_id": made["id"]})
            return {"made": made, "waited": waited, "found": found}


@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
def test_claude_runs_an_experiment_end_to_end_over_the_wire(config_path, tmp_path, mode):
    from src.admin.backtests import BacktestService
    from src.admin.ta_scripts import ScriptStore
    from tests.test_admin_backtests import FakeLauncher, pine
    from tests.test_admin_ta_rules import BANDS, MATHS, STRATEGY

    ctx = mcp_ctx(config_path, tmp_path)
    token, _ = ctx.mcp_tokens.create("claude-mac")
    scripts = ScriptStore(str(tmp_path / "ta"))
    for name, source in (("Maths", MATHS), ("Bands", BANDS), ("Cross", STRATEGY)):
        scripts.create(name, source)
    with open("etc/config_example.json") as example:
        (tmp_path / "live.json").write_text(example.read())
    ctx.ta_scripts = scripts
    ctx.backtests = BacktestService(
        root=str(tmp_path / "backtests"), prices_root=str(tmp_path / "prices"), config_path=str(tmp_path / "live.json"),
        scripts=scripts, launcher=FakeLauncher(),
    )

    with RunningAdmin(ctx, tmp_path / "no-interface") as admin:
        found = asyncio.run(_research_loop(f"{admin.base_url}/mcp", token, mode, pine(scripts)))

    assert found["made"]["made_by"] == "claude" and found["made"]["trials_recorded"] == 1
    assert found["waited"]["runs"][0]["state"] == "done"
    assert found["found"]["result"]["figures"]["total_percent"] == 1.5
    assert found["found"]["made_by"] == "claude"
