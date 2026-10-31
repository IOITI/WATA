"""
LuxAlgo's tools for the AI agent (src/admin/agent/tools.py, built by
luxalgo_reads of src/admin/agent_ops.py), over the LuxAlgo made up in
tests/test_admin_luxalgo.py: offered only while on, its catalog cut to first
lines, a call checked and cut, an indicator brought in whole as one of the
agent's scripts through the endpoint's pine_save, its origin kept and carried to
the operator's script a proposal makes of it.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from src.admin.agent.loop import prompt_for
from src.admin.agent.prompts import LUXALGO as LUXALGO_PROMPT
from src.admin.agent.proposals import Proposals
from src.admin.agent.service import AgentService, _shared_luxalgo
from src.admin.agent.tools import ANSWER_CHARS, LUXALGO, AgentTools, LuxAlgoReads, ToolAnswer
from src.admin.agent.verdicts import VerdictStore
from src.admin.errors import InvalidRequest, NotPossible
from src.admin.agent_ops import luxalgo_reads
from src.admin.luxalgo import LuxAlgo
from src.admin.script_origins import ScriptOrigins
from tests.test_admin_backtests import NOW, config, pine, scripts  # noqa: F401 (fixtures)
from tests.test_admin_luxalgo import HEADER, FakeLuxAlgo
from tests.test_agent_tools import CONVERSATION, calls, store  # noqa: F401 (fixtures)
from tests.test_mcp_tools_read import journal, research  # noqa: F401 (fixtures)
from tests.test_mcp_tools_shadow import Studies


@pytest.fixture
def fake():
    return FakeLuxAlgo()


@pytest.fixture
def origins(tmp_path):
    return ScriptOrigins(str(tmp_path / "ta-origins"))


@pytest.fixture
def lux(fake, origins):
    return luxalgo_reads(SimpleNamespace(luxalgo=LuxAlgo(server=fake.server), script_origins=origins))


def started(research, store, luxalgo=None, origins=None, verdicts=None):
    research.studies = Studies()
    tools = AgentTools(research, Proposals(store, research.scripts, origins=origins), luxalgo=luxalgo,
                       verdicts=verdicts)
    asyncio.run(tools.start())
    return tools


def test_luxalgos_tools_are_offered_only_while_it_is_on_and_its_prompt_with_them(research, store, lux):
    off = started(research, store)
    assert not {definition["name"] for definition in off.definitions()} & set(LUXALGO)
    assert LUXALGO_PROMPT not in prompt_for(off.definitions())
    on = started(research, store, lux)
    names = [definition["name"] for definition in on.definitions()]
    assert names[-4:] == list(LUXALGO)
    assert LUXALGO_PROMPT in prompt_for(on.definitions())
    assert on.is_read("luxalgo_tools") and on.is_read("luxalgo_call") and not on.is_read("luxalgo_save_script")
    assert luxalgo_reads(SimpleNamespace(luxalgo=None, script_origins=None)) is None


def test_the_catalog_gives_first_lines_and_one_tool_whole(research, store, lux):
    tools = started(research, store, lux)
    every, trackers, one, unknown = calls(
        tools, ("luxalgo_tools", {}), ("luxalgo_tools", {"group": "trackers"}),
        ("luxalgo_tools", {"tool": "library_search"}), ("luxalgo_tools", {"tool": "journal_add_trade"}))
    listed = {entry["name"]: entry for entry in json.loads(every.text)["tools"]}
    assert "journal_add_trade" not in listed and listed["library_search"] == {
        "name": "library_search", "group": "library", "line": "Search the Library."}
    assert [entry["name"] for entry in json.loads(trackers.text)["tools"]] == ["trackers_latest"]
    whole = json.loads(one.text)
    assert whole["input_schema"]["required"] == ["query"] and "context" not in whole["input_schema"]["properties"]
    assert unknown.is_error and "not one of the LuxAlgo tools WATA calls" in unknown.text


def test_a_call_is_checked_and_said_and_what_is_not_allowed_never_reaches_luxalgo(research, store, lux, fake):
    tools = started(research, store, lux)
    found, journal_write, outside, malformed = calls(
        tools, ("luxalgo_call", {"tool": "library_search", "arguments": {"query": "supertrend"}}),
        ("luxalgo_call", {"tool": "journal_add_trade", "arguments": {"symbol": "NQ"}}),
        ("luxalgo_call", {"tool": "library_delete_everything", "arguments": {}}),
        ("luxalgo_call", {"tool": "library_search", "arguments": {"limit": 3}}))
    assert json.loads(found.text)["results"][0]["slug"] == "made-up"
    assert journal_write.is_error and journal_write.text.startswith("INVALID_JSON")
    assert outside.is_error and "not one of the LuxAlgo tools WATA calls" in outside.text
    assert malformed.is_error and "'query' is a required property" in malformed.text
    assert [tool for tool, _ in fake.received] == ["library_search"]


def test_a_long_answer_is_cut_saying_how_to_have_it_whole(research, store):
    async def long_call(tool, arguments):
        return "x" * (ANSWER_CHARS + 5000)

    async def nothing(*_):
        return {}

    tools = started(research, store, LuxAlgoReads(tools=nothing, call=long_call, source=nothing, keep=nothing))
    source, other = calls(tools, ("luxalgo_call", {"tool": "library_get_source_code", "arguments": {"slug": "x"}}),
                          ("luxalgo_call", {"tool": "trackers_latest", "arguments": {"dataset": "bills"}}))
    assert len(source.text) <= ANSWER_CHARS and "bring it in whole with luxalgo_save_script" in source.text
    assert "ask for less" in other.text and not other.is_error


def test_an_indicator_is_brought_in_whole_as_one_of_the_agents_scripts_its_origin_kept(research, store, lux, origins):
    tools = started(research, store, lux, origins)
    saved, = calls(tools, ("luxalgo_save_script", {"slug": "made-up"}))
    assert not saved.is_error, saved.text
    answer = json.loads(saved.text)
    meta = research.scripts.script(answer["id"])
    assert meta["name"] == "Made up (LuxAlgo)" and meta["made_by"] == "claude"
    assert meta["source"].startswith("// This work is licensed") and "\r" not in meta["source"]
    assert "LuxAlgo's Made up" in meta["description"] and "CC BY-NC-SA 4.0" in meta["description"]
    assert research.studies.asked[0][0] == "check"
    assert answer["origin"]["slug"] == "made-up" and answer["origin"]["by"] == f"agent:{CONVERSATION}"
    assert origins.get(answer["id"])["licence"] == "CC BY-NC-SA 4.0"
    assert saved.script == {"id": answer["id"], "name": "Made up (LuxAlgo)", "version": 1}


def test_a_part_of_an_indicator_is_never_saved(research, store, lux, fake):
    fake.whole = False
    tools = started(research, store, lux)
    before = len(research.scripts.scripts())
    refused, = calls(tools, ("luxalgo_save_script", {"slug": "made-up", "name": "Partial"}))
    assert refused.is_error and "sign in to LuxAlgo" in refused.text
    assert len(research.scripts.scripts()) == before


def test_the_operators_script_made_from_the_agents_keeps_its_origin(research, store, lux, origins):
    tools = started(research, store, lux, origins)
    saved, = calls(tools, ("luxalgo_save_script", {"slug": "made-up", "name": "Made up v2"}))
    draft = json.loads(saved.text)
    proposals = Proposals(store, research.scripts, origins=origins)
    proposal = proposals.propose_new_script(CONVERSATION, 0, {
        "from_script_id": draft["id"], "from_version": 1, "name": "Made up, mine", "title": "Keep it",
        "rationale": "It held on its runs."})
    applied = proposals.apply(CONVERSATION, proposal["id"], None)
    made = applied["result"]["script_id"]
    assert origins.get(made) == {**origins.get(draft["id"]), "from_script": draft["id"]}
    assert research.scripts.script(made).get("made_by") is None
    assert HEADER.splitlines()[1] in research.scripts.source(made, 1)


def test_the_research_reads_say_where_a_script_came_from(research, store, lux, origins):
    research.origins = origins
    tools = started(research, store, lux, origins)
    saved, = calls(tools, ("luxalgo_save_script", {"slug": "made-up"}))
    made = json.loads(saved.text)["id"]
    copy = research.scripts.create("A copy", HEADER + "//@version=6\nindicator('A copy')\nplot(close)\n")
    listed, got = calls(tools, ("pine_list", {}), ("pine_get", {"script_id": copy["id"]}))
    lines = {line["id"]: line for line in json.loads(listed.text)["items"]}
    assert lines[made]["origin"]["slug"] == "made-up" and lines[copy["id"]]["origin"]["derived"] is True
    assert not [line for script_id, line in lines.items() if script_id not in (made, copy["id"]) and "origin" in line]
    assert json.loads(got.text)["origin"]["licence"] == "CC BY-NC-SA 4.0"


def with_known(lux, answered=None):
    """LuxAlgo's reads with what WATA knows made up: each slug asked, and what was asked kept."""
    asked = []

    async def known(slugs):
        asked.append(slugs)
        return {slug: (answered or {}).get(slug) or {"slug": slug, "scripts": [], "runs": [], "verdict": None}
                for slug in slugs}

    return LuxAlgoReads(tools=lux.tools, call=lux.call, source=lux.source, keep=lux.keep, indicator=lux.indicator,
                        known=known), asked


def test_what_wata_knows_is_read_by_slug_and_offered_only_with_it(research, store, lux):
    plain = LuxAlgoReads(tools=lux.tools, call=lux.call, source=lux.source, keep=lux.keep)
    assert "luxalgo_known" not in [entry["name"] for entry in started(research, store, plain).definitions()]
    reads, asked = with_known(lux, {"made-up": {"slug": "made-up", "verdict": {"tone": "negative"}}})
    tools = started(research, store, reads)
    found, refused = calls(tools, ("luxalgo_known", {"slugs": ["made-up", "made-up", "other-one"]}),
                           ("luxalgo_known", {"slugs": ["../x"]}))
    assert json.loads(found.text)["made-up"]["verdict"]["tone"] == "negative"
    assert asked == [["made-up", "other-one"]] and tools.is_read("luxalgo_known")
    assert refused.is_error and refused.text.startswith("INVALID_JSON")


def test_a_verdict_on_an_indicator_needs_luxalgo_to_have_it(research, store, lux, tmp_path):
    verdicts = VerdictStore(str(tmp_path / "verdicts"))
    tools = started(research, store, lux, verdicts=verdicts)
    said, unknown = calls(
        tools, ("set_verdict", {"about": "luxalgo:made-up", "tone": "negative", "line": "No edge on 14 days."}),
        ("set_verdict", {"about": "luxalgo:unknown", "tone": "neutral", "line": "?"}))
    assert not said.is_error and said.verdict["about"] == "luxalgo:made-up"
    assert verdicts.of(["luxalgo:made-up"])["luxalgo:made-up"]["line"] == "No edge on 14 days."
    assert unknown.is_error and "LuxAlgo's Library has no indicator unknown" in unknown.text
    off, = calls(started(research, store, None, verdicts=verdicts),
                 ("set_verdict", {"about": "luxalgo:made-up", "tone": "neutral", "line": "?"}))
    assert off.is_error and "switched off" in off.text


def test_a_conversation_about_an_indicator_is_named_by_it_and_refused_unknown_or_off(lux):
    service = SimpleNamespace(_tools=SimpleNamespace(luxalgo=lux))
    subject = asyncio.run(AgentService._luxalgo_subject(service, "made-up"))
    assert subject == {"kind": "luxalgo", "id": "made-up", "label": "LuxAlgo's Made up"}
    with pytest.raises(InvalidRequest, match="no indicator unknown"):
        asyncio.run(AgentService._luxalgo_subject(service, "unknown"))
    with pytest.raises(InvalidRequest, match="slug"):
        asyncio.run(AgentService._luxalgo_subject(service, "Not_A_Slug"))
    with pytest.raises(NotPossible, match="switched off"):
        asyncio.run(AgentService._luxalgo_subject(SimpleNamespace(_tools=SimpleNamespace(luxalgo=None)), "made-up"))


class SharingSession:
    """A request's session as the start of a conversation reads through it."""

    def __init__(self, answers):
        self.answers = answers
        self.called = []

    def offers(self, name):
        return name in self.answers

    async def call(self, name, arguments, request):
        self.called.append((name, arguments))
        key = arguments.get("tool", name) if name == "luxalgo_call" else name
        return ToolAnswer(json.dumps(self.answers[key]))


def test_an_indicator_is_shared_with_its_write_up_its_sources_state_and_what_wata_knows():
    session = SharingSession({
        "library_get_indicator": {"slug": "made-up", "name": "Made up", "family": "trend", "body_markdown": "x" * 7000,
                                  "concepts": [{"slug": "supertrend"}]},
        "library_get_source_code": {"available": True, "complete": False, "source": HEADER + "//@version=6",
                                    "total_lines": 201},
        "luxalgo_known": {"made-up": {"verdict": {"tone": "negative", "line": "No edge."}}},
        "luxalgo_call": None,
    })
    text = asyncio.run(_shared_luxalgo(session, {"kind": "luxalgo", "id": "made-up", "label": "LuxAlgo's Made up"}, 0))
    assert "LuxAlgo's Made up (made-up)" in text and "(cut: read it whole with luxalgo_call)" in text
    assert '"complete": false' in text and '"total_lines": 201' in text and "CC BY-NC-SA 4.0" in text
    assert "No edge." in text
    # The source itself is not shared: the agent takes it with luxalgo_save_script.
    assert "//@version=6" not in text

