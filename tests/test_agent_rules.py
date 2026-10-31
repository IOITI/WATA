"""
Signal rules and the AI agent (src/admin/agent/service.py's rule subject,
src/admin/agent_dossier.py, propose_new_rule of src/admin/agent/proposals.py):
a rule asked about from its row is shared as rule_list reads it; once one of the
operator's rules in shadow meets its readiness, its go-live dossier is asked once
for what it runs, the market closed; a new rule of the operator's is proposed as
the rules' store checks one, in shadow or off, never live, and made by the
store once applied.
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from src.admin.agent.proposals import APPLIED, NEW_RULE, ProposalRefused, Proposals
from src.admin.agent.service import AgentService
from src.admin.agent.settings import AgentSettings
from src.admin.agent.store import ConversationStore
from src.admin.agent_dossier import RuleDossiers, judged_as
from src.admin.agent_telegram import DOSSIER
from src.admin.errors import InvalidRequest, NotPossible
from src.admin.ta_rules import SHADOW
from tests.test_admin_ta_rules import LIBRARY_ONLY, created, cross, rules, scripts  # noqa: F401 (fixtures)
from tests.test_agent_loop import FakeBacktests, FakeProvider, FakeTools
from tests.test_agent_proposals import DONE, LiveBacktests
from tests.test_agent_subjects import Session

CONVERSATION = "20261030-090000-a1b2c3"
NOW = datetime(2026, 10, 30, 21, 30, tzinfo=timezone.utc).timestamp()


@pytest.fixture
def store(tmp_path):
    store = ConversationStore(str(tmp_path / "agent"))
    store.create({"id": CONVERSATION, "title": "Rules", "status": "idle", "request": 0})
    return store


def agent_over(store, scripts):
    async def the_key():
        return "sk-ant-x"

    return AgentService(settings=AgentSettings(enabled=True), store=store, provider=FakeProvider(), tools=FakeTools(),
                        proposals=None, backtests=FakeBacktests(), scripts=scripts, key=the_key, publish=lambda *a, **k: None)


def test_a_rule_is_asked_about_by_its_name_and_shared_as_rule_list_reads_it(store, scripts, cross, rules):
    rule = created(rules, cross)["rule"]
    agent = agent_over(store, scripts)

    subject = asyncio.run(agent._subject({"kind": "rule", "id": rule["id"]}))
    with pytest.raises(InvalidRequest, match="no signal rule"):
        asyncio.run(agent._subject({"kind": "rule", "id": "a1b2c3d4e5f6"}))
    listed = json.dumps({"rules": [{"id": "other"}, {"id": rule["id"], "name": rule["name"], "readiness": []}]})
    session = Session({"rule_list": listed})
    shared = asyncio.run(agent._shared(session, subject, 0))

    assert subject == {"kind": "rule", "id": rule["id"], "label": "Cross on US100"}
    assert session.asked == [("rule_list", {})]
    assert f"The operator shares the signal rule Cross on US100 ({rule['id']})" in shared and '"readiness": []' in shared


# ── the go-live dossier ──


class FakeAgent:
    def __init__(self, **settings):
        self.settings = AgentSettings(**{"enabled": True, "dossier_when_ready": True, **settings})
        self.ready = True
        self.created = []
        self.refusal = None

    async def create(self, subject, prompt, by, model=None, effort=None, *, task=None, debrief=None, job=None):
        if self.refusal:
            raise NotPossible(self.refusal)
        self.created.append({"subject": subject, "by": by, "job": job})
        return {"id": f"20261030-2130{len(self.created):02d}-abcdef"}


class Teller:
    def __init__(self):
        self.notes = []

    def add(self, conversation_id, kind, *, request=None, label=None, text=None):
        self.notes.append((conversation_id, kind, label))


def a_rule(rule_id="a1b2c3d4e5f6", **changes):
    ten_days = (datetime.fromtimestamp(NOW, timezone.utc) - timedelta(days=10)).isoformat()
    return {"id": rule_id, "name": "Cross on US100", "mode": SHADOW, "mode_changed_at": ten_days, **changes}


def dossiers_of(tmp_path, agent, found, open_market=False, teller=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    config = tmp_path / "config.json"
    hours = (0, 24) if open_market else (9, 10)
    config.write_text(json.dumps({"trade": {"config": {"general": {"timezone": "Europe/Paris"}}, "rules": [
        {"rule_type": "market_hours", "rule_name": "market_hours",
         "rule_config": {"trading_start_hour": hours[0], "trading_end_hour": hours[1]}}]}}))

    async def states():
        return {rule["id"]: {"state": "running", "last_alert_at": "2026-10-29T15:00:00Z"} for rule in found}

    return RuleDossiers(agent=agent, config_path=str(config), path=str(tmp_path / "dossiers.json"),
                        rules=lambda: found, states=states, paper=lambda: {}, timezone_name="Europe/Paris",
                        teller=teller, clock=lambda: NOW)


def test_a_rule_ready_gets_its_dossier_once_for_what_it_runs(tmp_path):
    agent, teller = FakeAgent(), Teller()
    found = [a_rule()]
    dossiers = dossiers_of(tmp_path, agent, found, teller=teller)

    first = asyncio.run(dossiers.catch_up())
    again = asyncio.run(dossiers.catch_up())
    found[0] = a_rule(run_changed_at=(datetime.fromtimestamp(NOW, timezone.utc) - timedelta(days=8)).isoformat())
    changed = asyncio.run(dossiers.catch_up())

    assert first and again is None and changed
    assert agent.created[0]["subject"] == {"kind": "rule", "id": "a1b2c3d4e5f6"} and agent.created[0]["by"] == "dossier"
    assert agent.created[0]["job"]["title"] == "Go-live dossier of Cross on US100"
    assert agent.created[0]["job"]["backtests"] == 8, "a stress test is part of it"
    assert teller.notes[0] == (first, DOSSIER, "Cross on US100")
    assert set(dossiers.asked()) == {judged_as(a_rule()), judged_as(found[0])}


def test_no_dossier_while_not_ready_nor_for_claude_s_rules_nor_with_the_market_open(tmp_path):
    young = a_rule(mode_changed_at=datetime.fromtimestamp(NOW, timezone.utc).isoformat())
    claude = a_rule(made_by="claude")
    assert asyncio.run(dossiers_of(tmp_path, FakeAgent(), [young, claude]).catch_up()) is None
    assert asyncio.run(dossiers_of(tmp_path / "open", FakeAgent(), [a_rule()], open_market=True).catch_up()) is None
    assert asyncio.run(dossiers_of(tmp_path / "off", FakeAgent(dossier_when_ready=False), [a_rule()]).catch_up()) is None
    held = FakeAgent()
    held.refusal = "The AI agent is paused"
    dossiers = dossiers_of(tmp_path / "held", held, [a_rule()])
    assert asyncio.run(dossiers.catch_up()) is None and dossiers.asked() == {}


# ── a new rule proposed ──


@pytest.fixture
def proposals(store, scripts):
    return Proposals(store, scripts, clock=lambda: NOW)


def new_rule(script, **changes):
    return {"name": "Cross, slow", "script_id": script["id"], "asset": "capital/US100", "indice": "us100",
            "bars": "5", "alerts": {"Enter Long": "long", "Exit": "close-position"}, "inputs": {"in_0": 14},
            "title": "A slower cross, in shadow", "rationale": "Beat the cross on 22 days.", **changes}


def test_a_new_rule_is_proposed_as_the_store_checks_one_and_made_in_shadow(proposals, scripts, cross, rules):
    proposal = proposals.propose_new_rule(CONVERSATION, 0, new_rule(cross))
    applied = proposals.apply(CONVERSATION, proposal["id"], "10.8.0.2")

    assert proposal["kind"] == NEW_RULE and proposal["new_rule"]["mode"] == SHADOW
    assert proposal["new_rule"]["fields"]["bars"] == {"type": "time", "timeframe": "5"}
    assert applied["status"] == APPLIED and applied["result"]["mode"] == SHADOW
    made = rules.rule(applied["result"]["rule_id"])
    assert (made["name"], made["script"]["id"], made["inputs"][0]["value"]) == ("Cross, slow", cross["id"], 14)
    assert "made_by" not in made, "the operator's"


@pytest.mark.parametrize("changes, said", [
    ({"mode": "live"}, "shadow or off"),
    ({"asset": "dukascopy/us100"}, "capital/<EPIC>"),
    ({"bars": "7S"}, "Bars of time"),
    ({"alerts": {"Enter Long": "ignore"}}, "makes a signal"),
    ({"script_id": "nope"}, "No script"),
])
def test_what_a_new_rule_may_not_be(proposals, cross, changes, said):
    with pytest.raises(ProposalRefused, match=said):
        proposals.propose_new_rule(CONVERSATION, 0, new_rule(cross, **changes))


def test_a_new_rule_rests_on_finished_runs_of_its_script_once_backtests_are_on(store, scripts, cross):
    with_runs = Proposals(store, scripts, clock=lambda: NOW, backtests=LiveBacktests())

    with pytest.raises(ProposalRefused, match="backtests of its script on its asset and bars"):
        with_runs.propose_new_rule(CONVERSATION, 0, new_rule(cross))
    proposal = with_runs.propose_new_rule(CONVERSATION, 0, new_rule(cross, runs=[DONE]))
    assert [run["id"] for run in proposal["runs"]] == [DONE]


def test_a_name_taken_a_library_or_claude_s_script_is_refused(proposals, scripts, cross, rules):
    created(rules, cross)
    library = scripts.create("Lonely", LIBRARY_ONLY)
    claude = scripts.create("Claude · cross", scripts.source(cross["id"], 1), made_by="claude")

    with pytest.raises(ProposalRefused, match="named 'Cross on US100' already"):
        proposals.propose_new_rule(CONVERSATION, 0, new_rule(cross, name="Cross on US100"))
    with pytest.raises(ProposalRefused, match="is a library"):
        proposals.propose_new_rule(CONVERSATION, 0, new_rule(library))
    with pytest.raises(ProposalRefused, match="propose_new_script"):
        proposals.propose_new_rule(CONVERSATION, 0, new_rule(claude))


def test_a_rule_after_a_new_script_waits_for_it(proposals, scripts, cross, rules):
    draft = scripts.create("Claude · slow cross", scripts.source(cross["id"], 1), made_by="claude")
    script = proposals.propose_new_script(CONVERSATION, 0, {
        "from_script_id": draft["id"], "from_version": 1, "name": "Cross, slower", "title": "A slower cross",
        "rationale": "Beat it."})
    rule = proposals.propose_new_rule(CONVERSATION, 0, {**new_rule(cross), "script_id": None,
                                                        "after_proposal": script["id"]})

    with pytest.raises(NotPossible, match="Create the new script first"):
        proposals.apply(CONVERSATION, rule["id"], None)
    made_script = proposals.apply(CONVERSATION, script["id"], None)["result"]
    made_rule = proposals.apply(CONVERSATION, rule["id"], None)["result"]
    assert rules.rule(made_rule["rule_id"])["script"]["id"] == made_script["script_id"]
