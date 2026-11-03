"""
What the AI agent writes of a script beside its code (describe_script, src/admin/agent/tools.py): its family and its
How it works, at once and as its conversation's, every version kept; refused when written from an older revision,
for a script that is not there, or with what reads as a secret. And what the research tools say of them
(pine_list, pine_get), which the agent and Claude Code read.
"""

import asyncio
import json

from src.admin.agent.proposals import Proposals
from src.admin.agent.tools import DESCRIBE_SCRIPT, AgentTools
from src.admin.script_details import ScriptDetails
from tests.test_admin_backtests import NOW, config, cross_id, scripts  # noqa: F401 (fixtures)
from tests.test_agent_tools import CONVERSATION, calls, store  # noqa: F401 (fixtures)
from tests.test_mcp_tools_read import answer, journal, research  # noqa: F401 (fixtures)


def described(research, store):
    research.details = ScriptDetails(research.scripts)
    tools = AgentTools(research, Proposals(store, research.scripts), clock=lambda: NOW)
    asyncio.run(tools.start())
    return tools


def test_it_is_offered_only_where_scripts_have_details(research, store):
    offered = AgentTools(research, Proposals(store, research.scripts))
    asyncio.run(offered.start())

    assert DESCRIBE_SCRIPT not in [entry["name"] for entry in offered.definitions()]
    assert DESCRIBE_SCRIPT in [entry["name"] for entry in described(research, store).definitions()]


def test_the_agent_writes_a_family_and_a_how_it_works_as_its_own(research, store, scripts):
    tools = described(research, store)
    script_id = cross_id(scripts)

    [written] = calls(tools, (DESCRIBE_SCRIPT, {"script_id": script_id, "family": "Trend", "how_it_works": "It crosses two averages."}))

    said = json.loads(written.text)
    assert not written.is_error and (said["family"], said["how_it_works_revision"]) == ("Trend", 1)
    assert f"/ta/indicators/{script_id}?tab=how" in said["next"]
    how = research.details.how(script_id)
    assert (how["text"], how["by"]) == ("It crosses two averages.", f"agent:{CONVERSATION}")


def test_a_how_it_works_written_from_an_older_revision_is_refused(research, store, scripts):
    tools = described(research, store)
    script_id = cross_id(scripts)
    research.details.save_how(script_id, "The operator's own words.", 0, "operator")

    unread, read = calls(
        tools,
        (DESCRIBE_SCRIPT, {"script_id": script_id, "how_it_works": "Mine instead."}),
        (DESCRIBE_SCRIPT, {"script_id": script_id, "how_it_works": "Mine, from what I read.", "revision": 1}),
    )

    assert unread.is_error and "saved meanwhile" in unread.text and "pine_get" in unread.text
    assert not read.is_error and research.details.how(script_id)["text"] == "Mine, from what I read."
    assert [entry["by"] for entry in research.details.how_history(script_id)] == [f"agent:{CONVERSATION}", "operator"]


def test_nothing_is_written_of_a_script_not_there_with_nothing_given_or_with_a_secret(research, store, scripts):
    tools = described(research, store)
    script_id = cross_id(scripts)

    unknown, nothing, secret = calls(
        tools,
        (DESCRIBE_SCRIPT, {"script_id": "ffffffffffff", "family": "Trend"}),
        (DESCRIBE_SCRIPT, {"script_id": script_id}),
        (DESCRIBE_SCRIPT, {"script_id": script_id, "how_it_works": "Its key: sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789"}),
    )

    assert unknown.is_error and "pine_list" in unknown.text
    assert nothing.is_error and "Give its family" in nothing.text
    assert secret.is_error and "secret" in secret.text
    assert research.details.of(script_id)["how"] is None


def test_the_research_tools_say_a_scripts_family_star_and_how_it_works(research, scripts):
    research.details = ScriptDetails(research.scripts)
    script_id = cross_id(scripts)
    research.details.set_favourites([script_id], True)
    research.details.set_family(script_id, "Trend", "operator")
    research.details.save_how(script_id, "It crosses two averages.", 0, "operator")

    listed = {item["id"]: item for item in answer(research, "pine_list")["items"]}
    of_family = answer(research, "pine_list", {"family": "trend"})["items"]
    got = answer(research, "pine_get", {"script_id": script_id})

    assert (listed[script_id]["favourite"], listed[script_id]["family"]) == (True, "Trend")
    assert [item["id"] for item in of_family] == [script_id]
    assert got["how_it_works"]["text"] == "It crosses two averages." and got["how_it_works"]["revision"] == 1
    assert got["how_it_works"]["cut"] is False and got["picture"] is False
