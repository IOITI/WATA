"""
Actions on live trading the AI agent proposes (src/admin/agent/proposals.py,
kind "action"; admin.ai_agent.action_proposals): a pause or a resume of new
entries, a position closed, services restarted, a rule taken live or out of
live, each checked against what is there when proposed, never applied by the
agent. The operator applies one from its card through the page's own route
(src/admin/proposed_actions.py), which checks it still waits, is of its kind and
is fresh, keeps its own checks, says it in its audit entry, and marks it applied
once done.
"""

import asyncio
import json

import pytest

from src.admin.agent.proposals import (
    ACTION_FRESH_MINUTES,
    APPLIED,
    CLOSE,
    PAUSE,
    RESTART,
    RULE_LIVE,
    RULE_OUT,
    ProposalRefused,
    Proposals,
)
from src.admin.agent.service import AgentService
from src.admin.agent.settings import AgentSettings
from src.admin.agent.store import ConversationStore
from src.admin.agent.tools import AgentTools
from src.admin.errors import InvalidRequest, NotPossible
from src.admin.ta_rules import LIVE, SHADOW
from src.admin.ta_scripts import ScriptStore
from tests.test_admin_api import SAME_ORIGIN, config_path, make_ctx  # noqa: F401 (fixtures)
from tests.test_admin_backtests import NOW as BACKTESTS_NOW, config, pine  # noqa: F401 (fixtures)
from tests.test_admin_ta_rules import created, cross, rules, scripts  # noqa: F401 (fixtures)
from tests.test_agent_api import signed_in
from tests.test_agent_loop import FakeBacktests, FakeProvider, FakeScripts, FakeTools
from tests.test_agent_proposals import fields_of
from tests.test_agent_tools import CONVERSATION, calls, store  # noqa: F401 (fixtures)
from tests.test_mcp_tools_read import journal, research  # noqa: F401 (fixtures)

NOW = 1_793_372_400.0
OPEN = {"position_id": "5001", "action": "long", "instrument_name": "Turbo long", "position_status": "Open",
        "execution_time_open": "2026-10-30T14:00:00Z"}


def asked(action, **given):
    return {"action": action, "title": f"{action} now", "rationale": "What I read calls for it.", **given}


@pytest.fixture
def proposals(store, scripts):
    clock = [NOW]
    made = Proposals(store, scripts, clock=lambda: clock[0])
    made.clock = clock
    return made


def test_each_action_is_checked_against_what_is_there_when_proposed(proposals, rules, cross):
    shadow = created(rules, cross)["rule"]

    close = proposals.propose_action(CONVERSATION, 0, asked(CLOSE, position_id="5001"), position=OPEN)
    restart = proposals.propose_action(CONVERSATION, 0, asked(RESTART, services=["position_monitor", "trader"]))
    live = proposals.propose_action(CONVERSATION, 0, asked(RULE_LIVE, rule_id=shadow["id"]))

    assert close["action"] == {"kind": CLOSE, "position_id": "5001", "action": "long", "instrument_name": "Turbo long",
                               "execution_time_open": "2026-10-30T14:00:00Z"}
    assert restart["action"]["services"] == [{"id": "position_monitor", "name": "Position monitor"},
                                             {"id": "trader", "name": "Trader"}]
    assert live["action"]["rule"]["mode"] == SHADOW and live["fresh_until"] == "2026-10-30T15:30:00Z"
    for refused, said in [
        (lambda: proposals.propose_action(CONVERSATION, 0, asked(CLOSE, position_id="9")), "no position 9"),
        (lambda: proposals.propose_action(CONVERSATION, 0, asked(CLOSE, position_id="5001"),
                                          position={**OPEN, "position_status": "Closed"}), "not open"),
        (lambda: proposals.propose_action(CONVERSATION, 0, asked(RESTART, services=["postgres"])), "among"),
        (lambda: proposals.propose_action(CONVERSATION, 0, asked(RULE_OUT, rule_id=shadow["id"])), "is not live"),
        (lambda: proposals.propose_action(CONVERSATION, 0, asked("trade")), "is one of"),
    ]:
        with pytest.raises(ProposalRefused, match=said):
            refused()


def test_a_live_rule_goes_out_of_live_and_never_back_through_a_proposal_of_claude_s_rule(proposals, rules, cross):
    rule = created(rules, cross)["rule"]
    rules.set_mode(rule["id"], LIVE, rules.load()["revision"])
    claude = rules.create({**fields_of(rule), "name": "Claude · cross"}, rules.load()["revision"], SHADOW,
                          made_by="claude")["rule"]

    out = proposals.propose_action(CONVERSATION, 0, asked(RULE_OUT, rule_id=rule["id"]))
    assert out["action"]["rule"]["mode"] == LIVE
    with pytest.raises(ProposalRefused, match="your own rules"):
        proposals.propose_action(CONVERSATION, 0, asked(RULE_LIVE, rule_id=claude["id"]))
    with pytest.raises(ProposalRefused, match="only a rule in shadow"):
        proposals.propose_action(CONVERSATION, 0, asked(RULE_LIVE, rule_id=rule["id"]))


def test_an_action_is_applied_by_its_route_alone_while_fresh_and_of_its_kind(proposals):
    pause = proposals.propose_action(CONVERSATION, 0, asked(PAUSE))

    with pytest.raises(NotPossible, match="from its card"):
        proposals.apply(CONVERSATION, pause["id"], None)
    with pytest.raises(InvalidRequest, match="not an action of this kind"):
        proposals.pending_action(CONVERSATION, pause["id"], RESTART)
    assert proposals.pending_action(CONVERSATION, pause["id"], PAUSE)["id"] == pause["id"]
    applied = proposals.action_applied(CONVERSATION, pause["id"], "10.8.0.2", {"entries_paused_since": "x"})
    assert (applied["status"], applied["result"]) == (APPLIED, {"entries_paused_since": "x"})
    stale = proposals.propose_action(CONVERSATION, 0, asked(PAUSE))
    proposals.clock[0] = NOW + ACTION_FRESH_MINUTES * 60 + 1
    with pytest.raises(NotPossible, match="over 30 minutes ago"):
        proposals.pending_action(CONVERSATION, stale["id"], PAUSE)


def test_the_agent_proposes_actions_only_while_it_may_and_reads_the_position_it_closes(research, store, monkeypatch):
    without = AgentTools(research, Proposals(store, research.scripts))
    asyncio.run(without.start())
    (refused,) = calls(without, ("propose_action", asked(PAUSE)))
    assert refused.is_error and "No tool" in refused.text

    tools = AgentTools(research, Proposals(store, research.scripts), actions=True)
    asyncio.run(tools.start())

    async def position_record(position_id):
        return {"position": {**OPEN, "position_id": position_id}} if position_id == "5001" else None

    monkeypatch.setattr(research.queries._queries, "position_record", position_record)
    close, unknown = calls(tools, ("propose_action", asked(CLOSE, position_id="5001")),
                           ("propose_action", asked(CLOSE, position_id="77")))
    assert close.proposal["action"]["position_id"] == "5001" and "the operator" in json.loads(close.text)["next"].lower()
    assert unknown.is_error and "no position 77" in unknown.text


# ── applied from its card, through the page's route ──


def with_proposals(ctx, tmp_path, clock):
    store = ConversationStore(str(tmp_path / "agent"))
    # Changed now: the agent's start prunes the conversations left unchanged for a year.
    store.create({"id": CONVERSATION, "title": "Triage", "status": "idle", "request": 0,
                  "updated_at": "2026-10-30T15:00:00Z"})
    proposals = Proposals(store, ScriptStore(str(tmp_path / "ta")), clock=lambda: clock[0])

    async def the_key():
        return "sk-ant-x"

    ctx.agent = AgentService(settings=AgentSettings(enabled=True), store=store, provider=FakeProvider(),
                             tools=FakeTools(), proposals=proposals, backtests=FakeBacktests(), scripts=FakeScripts(),
                             key=the_key, publish=ctx.events.publish)
    return proposals


def test_a_pause_proposed_is_applied_through_the_pause_s_route_audited_as_proposed(config_path, tmp_path):
    ctx, clock = make_ctx(config_path, tmp_path), [NOW]
    proposals = with_proposals(ctx, tmp_path, clock)
    pause = proposals.propose_action(CONVERSATION, 0, asked(PAUSE, title="Pause new entries"))
    stale = proposals.propose_action(CONVERSATION, 0, asked(PAUSE))
    client = signed_in(ctx, tmp_path)
    try:
        wrong_kind = client.post("/api/actions/resume-entries", json={"proposal": f"{CONVERSATION}/{pause['id']}"},
                                 headers=SAME_ORIGIN)
        paused = client.post("/api/actions/pause-entries", json={"proposal": f"{CONVERSATION}/{pause['id']}"},
                             headers=SAME_ORIGIN)
        again = client.post("/api/actions/pause-entries", json={"proposal": f"{CONVERSATION}/{pause['id']}"},
                            headers=SAME_ORIGIN)
        ctx.queries.paused_since = None
        clock[0] = NOW + ACTION_FRESH_MINUTES * 60 + 1
        too_old = client.post("/api/actions/pause-entries", json={"proposal": f"{CONVERSATION}/{stale['id']}"},
                              headers=SAME_ORIGIN)
        malformed = client.post("/api/actions/pause-entries", json={"proposal": "../x"}, headers=SAME_ORIGIN)
        plain = client.post("/api/actions/pause-entries", headers=SAME_ORIGIN)
    finally:
        client.__exit__(None, None, None)

    assert wrong_kind.status_code == 422 and paused.status_code == 200
    assert proposals._store.proposals(CONVERSATION)[pause["id"]]["status"] == APPLIED
    assert again.status_code == 409, "applied once"
    assert too_old.status_code == 409 and "over 30 minutes ago" in too_old.json()["detail"]
    assert malformed.status_code == 422 and plain.status_code == 200
    audit = [entry for entry in ctx.audit_log.read() if entry["action"] == "new entries paused"]
    assert [entry["detail"] for entry in audit] == ["", "Proposed by the AI agent: 'Pause new entries'"]


def test_a_close_proposed_closes_the_position_it_names_alone(config_path, tmp_path):
    ctx, clock = make_ctx(config_path, tmp_path), [NOW]
    proposals = with_proposals(ctx, tmp_path, clock)
    close = proposals.propose_action(CONVERSATION, 0, asked(CLOSE, position_id="5001"), position=OPEN)
    client = signed_in(ctx, tmp_path)
    try:
        other = client.post("/api/actions/close-position",
                            json={"position_id": "5002", "proposal": f"{CONVERSATION}/{close['id']}"}, headers=SAME_ORIGIN)
        closed = client.post("/api/actions/close-position",
                             json={"position_id": "5001", "proposal": f"{CONVERSATION}/{close['id']}"}, headers=SAME_ORIGIN)
    finally:
        client.__exit__(None, None, None)

    assert other.status_code == 422 and closed.status_code == 202
    assert [message["position_id"] for message in ctx.services.published] == ["5001"]
    assert proposals._store.proposals(CONVERSATION)[close["id"]]["result"] == {"status": "queued", "position_id": "5001"}
