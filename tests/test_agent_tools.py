"""
The AI agent's tools (src/admin/agent/tools.py), called as the agent calls them:
the MCP endpoint's research tools in process, through the SDK's own client, over
the admin's real services on files (the fixtures of the MCP tools' tests). The
allowlist is exact, the schemas fit the Claude API, a call outside it or with a
wrong input is refused, the caller is the conversation, an answer too long is
refused, a script's versions are cut, and backtest_start's run is the agent's.
Claude's shadow rules are kept through the MCP endpoint's own tools, gated and
run first as there; a rule saved is the conversation's artefact.
"""

import asyncio
import json
from functools import partial

import pytest

from src.admin.agent import tools as agent_tools
from src.admin.agent.proposals import Proposals
from src.admin.agent.store import ConversationStore
from src.admin.agent.tools import AGENT_TOOLS, OWN_TOOLS, AgentTools, OpsReads
from src.admin.logs import read_log
from src.admin.mcp_server.facade import ReadQueries
from src.admin.mcp_server.limits import McpLimits
from tests.test_admin_backtests import NOW, config, pine, scripts  # noqa: F401 (fixtures)
from tests.test_mcp_tools_read import journal, research  # noqa: F401 (fixtures)
from tests.test_mcp_tools_shadow import ALERTS, Feed, Studies

CONVERSATION = "20261028-080000-a1b2c3"


@pytest.fixture
def store(tmp_path):
    store = ConversationStore(str(tmp_path / "agent"))
    store.create({"id": CONVERSATION, "title": "Tune", "status": "running", "request": 0})
    return store


def started(research, store):
    tools = AgentTools(research, Proposals(store, research.scripts))
    asyncio.run(tools.start())
    return tools


def calls(tools, *asked):
    """Each (name, arguments) asked in one session of the conversation: their answers."""

    async def calling():
        async with tools.session(CONVERSATION) as session:
            return [await session.call(name, arguments, 0) for name, arguments in asked]

    return asyncio.run(calling())


def test_the_agent_is_offered_its_allowlist_and_its_own_tools_in_the_api_s_shape(research, store):
    tools = started(research, store)
    definitions = tools.definitions()

    assert [definition["name"] for definition in definitions] == [*AGENT_TOOLS, *OWN_TOOLS]
    for definition in definitions:
        schema = definition["input_schema"]
        assert schema["type"] == "object" and not {"anyOf", "oneOf", "allOf"} & set(schema), definition["name"]
        for words in ("Claude Code", "the Mac", "job_wait", "CSV"):
            assert words not in definition["description"], (definition["name"], words)
    by_name = {definition["name"]: definition for definition in definitions}
    for name in ("data_signals", "data_trades", "backtest_trades", "backtest_days", "backtest_signals"):
        assert "format" not in by_name[name]["input_schema"]["properties"], name
    assert "seconds" not in by_name["backtest_wait"]["input_schema"]["properties"]
    # Reads run at once, the studies one at a time (a shadow rule is run first), the rest in their order.
    assert tools.is_read("backtest_result") and not tools.is_read("pine_check") and not tools.is_read("backtest_start")
    assert not tools.is_read("rule_shadow_save") and "rule_shadow_save" in agent_tools.STUDIES


def test_a_call_is_the_conversation_s_and_one_outside_the_list_or_malformed_is_refused(research, store):
    tools = started(research, store)

    status, outside, malformed, listed = calls(
        tools, ("status", {}), ("pine_delete", {"script_id": "0123456789ab"}), ("backtest_result", {}),
        ("backtest_list", {}),
    )

    assert not status.is_error and json.loads(status.text)["token"]["name"] == f"agent:{CONVERSATION}"
    assert outside.is_error and "No tool 'pine_delete'" in outside.text
    assert malformed.is_error and malformed.text.startswith("INVALID_JSON")
    assert not listed.is_error and json.loads(listed.text)["total"] == 0
    # Its calls are in its own log, not in Claude Code's.
    assert [entry["caller"] for entry in research.calls.entries][:2] == [f"agent:{CONVERSATION}"] * 2


def test_an_answer_too_long_is_refused_as_one_asking_for_less(research, store, monkeypatch):
    tools = started(research, store)
    monkeypatch.setattr(agent_tools, "ANSWER_CHARS", 50)

    status, = calls(tools, ("status", {}))

    assert status.is_error and "ask for fewer rows" in status.text


def test_a_script_is_read_whole_with_its_last_versions(research, store, scripts):
    cross = next(meta for meta in scripts.scripts() if meta["name"] == "Cross")
    for number in range(1, 25):
        scripts.save(cross["id"], number, scripts.source(cross["id"]) + f"\n// {number}\n")
    tools = started(research, store)

    got, = calls(tools, ("pine_get", {"script_id": cross["id"]}))

    answer = json.loads(got.text)
    assert answer["current_version"] == 25 and len(answer["versions"]) == agent_tools.SHOWN_VERSIONS
    assert answer["versions"][-1]["number"] == 25 and answer["source"].endswith("// 24\n")


def test_a_backtest_started_is_the_agent_s_counted_in_the_request_and_in_its_experiment(research, store, scripts):
    tools = started(research, store)

    opened, = calls(tools, ("exp_new", {"title": "Faster", "hypothesis": "A faster line enters earlier and wins",
                                         "family": "cross"}))
    experiment = json.loads(opened.text)["id"]
    asked, = calls(tools, ("backtest_start", {"experiment_id": experiment, "request": pine(scripts)}))

    assert opened.experiment == experiment
    run_id = json.loads(asked.text)["id"]
    assert asked.runs == [run_id]
    request = research.backtests.get(run_id)["request"]
    assert request["made_by"] == "claude" and request["made_via"] == f"agent:{CONVERSATION}"


def test_the_agent_s_proposals_go_through_its_own_tools(research, store, scripts):
    tools = started(research, store)
    cross = next(meta for meta in scripts.scripts() if meta["name"] == "Cross")
    draft = scripts.create("Cross by Claude", scripts.source(cross["id"]) + "\n// tuned\n", made_by="claude")

    proposed, refused = calls(
        tools,
        ("propose_script_change", {"script_id": cross["id"], "base_version": 1, "from_script_id": draft["id"],
                                   "from_version": 1, "title": "Tuned", "rationale": "Run r beat it."}),
        ("propose_script_change", {"script_id": draft["id"], "base_version": 1, "from_script_id": draft["id"],
                                   "from_version": 1, "title": "Tuned", "rationale": "Run r beat it."}),
    )

    assert not proposed.is_error and proposed.proposal["script"]["id"] == cross["id"]
    assert refused.is_error and "one of your own scripts" in refused.text
    assert list(store.proposals(CONVERSATION)) == [proposed.proposal["id"]]


def _health():
    async def health():
        return {"status": "warning", "checked_at": "2026-10-28T15:40:00+00:00", "queues": {"trading-ops": 0},
                "services": [{"id": "trader", "name": "Trader", "status": "warning", "detail": "No signal for 40 min",
                              "beat": "2026-10-28T15:39:50+00:00"}]}
    return health


def test_the_admin_s_health_and_a_service_s_lines_around_a_moment_are_read_cleaned(research, store, tmp_path):
    folder = tmp_path / "logs" / "wata-trader-async"
    folder.mkdir(parents=True)
    lines = [f"2026-10-28 15:{minute:02d}:00,000 - INFO - trader - line {minute}" for minute in range(60)]
    lines.insert(30, "2026-10-28 15:29:30,000 - WARNING - trader - refused with key sk-ant-api03-abcdefghijklmnopqrstu")
    (folder / "wata-trader-async.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
    tools = AgentTools(research, Proposals(store, research.scripts),
                       ops=OpsReads(health=_health(), logs=partial(read_log, str(tmp_path / "logs"))))
    asyncio.run(tools.start())

    assert [definition["name"] for definition in tools.definitions()][-2:] == ["ops_health", "ops_logs"]
    assert tools.is_read("ops_health") and tools.is_read("ops_logs")
    health, window, warned, unknown = calls(
        tools, ("ops_health", {}), ("ops_logs", {"service": "wata-trader-async", "around": "2026-10-28 15:30",
                                                 "minutes": 2}),
        ("ops_logs", {"service": "wata-trader-async", "around": "2026-10-28 15:30", "level": "WARNING"}),
        ("ops_logs", {"service": "postgres", "around": "2026-10-28 15:30"}))

    assert json.loads(health.text)["services"] == [
        {"id": "trader", "name": "Trader", "status": "warning", "detail": "No signal for 40 min"}]
    said = json.loads(window.text)
    assert [entry["message"] for entry in said["entries"]][:2] == ["line 28", "line 29"]
    assert (said["lines"], said["from"], said["to"]) == (6, "2026-10-28 15:28", "2026-10-28 15:32")
    assert len(json.loads(warned.text)["entries"]) == 1 and "sk-ant-api03" not in warned.text
    assert unknown.is_error


def test_without_leave_to_read_the_logs_the_agent_reads_the_health_alone(research, store):
    tools = AgentTools(research, Proposals(store, research.scripts), ops=OpsReads(health=_health()))
    asyncio.run(tools.start())

    names = [definition["name"] for definition in tools.definitions()]
    assert "ops_health" in names and "ops_logs" not in names
    (refused,) = calls(tools, ("ops_logs", {"service": "wata-trader-async", "around": "2026-10-28 15:30"}))
    assert refused.is_error



def test_its_own_shadow_rules_are_kept_as_the_mcp_endpoint_keeps_them(research, store, scripts):
    cross = next(meta for meta in scripts.scripts() if meta["name"] == "Cross")
    asked = {"name": "cross", "script_id": cross["id"], "asset": "capital/US100", "indice": "us100", "alerts": ALERTS}
    research.studies = Studies()
    tools = started(research, store)

    off, = calls(tools, ("rule_shadow_save", asked))
    assert off.is_error and "switched off" in off.text and off.rule is None

    research.limits = McpLimits(shadow_rules=True)
    research.queries = ReadQueries(Feed())
    saved, live = calls(tools, ("rule_shadow_save", asked), ("rule_shadow_save", {**asked, "mode": "live"}))

    assert not saved.is_error and saved.rule == {"id": json.loads(saved.text)["id"], "name": "Claude · cross",
                                                 "mode": "shadow"}
    assert live.is_error and "going live is the operator's" in live.text
    assert [rule["mode"] for rule in research.rules.load()["rules"]] == ["shadow"]


def test_settings_and_new_scripts_are_proposed_through_its_own_tools(research, store, scripts):
    tools = started(research, store)
    cross = next(meta for meta in scripts.scripts() if meta["name"] == "Cross")
    draft = scripts.create("Cross by Claude", scripts.source(cross["id"]) + "\n// tuned\n", made_by="claude")

    settings, new = calls(
        tools,
        # The tools' own backtests are off here (no service handed to the proposals): said in words.
        ("propose_settings_change", {"changes": [{"setting": "stoploss_percent", "value": -10}],
                                     "runs": ["20261027-101010-abcdef"], "title": "Tighter", "rationale": "Run r."}),
        ("propose_new_script", {"from_script_id": draft["id"], "from_version": 1, "name": "Cross, tuned",
                                "title": "A tuned cross", "rationale": "Run r beat it."}),
    )

    assert settings.is_error and "Backtests are switched off" in settings.text
    assert not new.is_error and new.proposal["kind"] == "new_script"
    assert json.loads(new.text)["proposal"] == new.proposal["id"]


def test_only_a_research_task_says_it_is_done(research, store):
    tools = started(research, store)

    outside, = calls(tools, ("task_done", {"conclusion": "No edge."}))

    async def in_a_task():
        async with tools.session(CONVERSATION, task=True) as session:
            return await session.call("task_done", {"conclusion": " No edge. ", "runs_kept": ["r1", "r1"]}, 0)

    done = asyncio.run(in_a_task())

    assert outside.is_error and "not a research task" in outside.text and outside.task_done is None
    assert not done.is_error and done.task_done == {"conclusion": "No edge.", "runs_kept": ["r1"], "next_steps": None}
