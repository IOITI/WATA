"""
What the AI agent proposes and what becomes of it once the operator applies it
(src/admin/agent/proposals.py), on the real script and rule stores: a new
version of the operator's script from one of Claude's, a change to a rule that
is not live, a change of settings of config.json resting on finished runs, a
new script of the operator's; each refused where the stores, the rules, the
backtest's fields or the configuration's catalog say so.
"""

import copy

import pytest

from src.admin.agent.proposals import (
    APPLIED,
    DISMISSED,
    PROPOSED_SETTINGS,
    ProposalRefused,
    Proposals,
    catalog_path,
    fields_of,
)
from src.admin.agent.store import ConversationStore
from src.admin import config_catalog
from src.admin.errors import NotPossible, UnknownRun
from src.admin.ta_rules import LIVE, RuleStore
from src.admin.ta_scripts import ScriptConflict
from src.backtest.settings import FIELDS_BY_NAME, from_config
from tests.test_admin_ta_rules import LIBRARY_ONLY, STRATEGY, created, cross, rules, scripts  # noqa: F401 (fixtures)

NOW = 1_793_174_400.0
TUNED = STRATEGY.replace('input.int(9, "Fast")', 'input.int(14, "Fast")')


@pytest.fixture
def store(tmp_path):
    store = ConversationStore(str(tmp_path / "agent"))
    store.create({"id": "20261028-080000-a1b2c3", "title": "Tune", "status": "idle", "request": 0})
    return store


@pytest.fixture
def proposals(store, scripts):
    return Proposals(store, scripts, clock=lambda: NOW)


CONVERSATION = "20261028-080000-a1b2c3"


def draft(scripts, source=TUNED):
    return scripts.create("Cross, tuned by Claude", source, made_by="claude")


def script_change(cross, made, **changes):
    return {"script_id": cross["id"], "base_version": cross["version"], "from_script_id": made["id"],
            "from_version": made["version"], "title": "A slower fast line", "rationale": "Run X beat it.", **changes}


def test_a_new_version_of_the_operator_s_script_is_proposed_then_applied_and_says_who_wrote_it(store, scripts, cross, proposals):
    made = draft(scripts)

    proposal = proposals.propose_script(CONVERSATION, 0, script_change(cross, made))

    assert proposal["status"] == "proposed" and proposal["lines"] == {"added": 1, "removed": 1}
    assert scripts.script(cross["id"])["version"] == 1, "a proposal writes nothing"
    assert [line["kind"] for line in store.artefacts()] == ["proposal"]

    applied = proposals.apply(CONVERSATION, proposal["id"], "10.8.0.2")

    script = scripts.script(cross["id"])
    assert applied["status"] == APPLIED and applied["result"] == {"script_id": cross["id"], "version": 2}
    assert script["source"] == TUNED and script["versions"][-1]["made_by"] == "claude"
    assert script.get("made_by") is None, "the script stays the operator's"
    with pytest.raises(NotPossible, match="applied already"):
        proposals.apply(CONVERSATION, proposal["id"], "10.8.0.2")


def test_a_script_saved_meanwhile_keeps_its_change_and_the_proposal_is_refused(scripts, cross, proposals):
    proposal = proposals.propose_script(CONVERSATION, 0, script_change(cross, draft(scripts)))
    scripts.save(cross["id"], 1, STRATEGY + "\n// the operator's own change\n")

    with pytest.raises(ScriptConflict):
        proposals.apply(CONVERSATION, proposal["id"], None)


def test_what_a_script_proposal_may_not_be(scripts, cross, proposals):
    made = draft(scripts)
    library = next(meta for meta in scripts.scripts() if meta["name"] == "Bands")

    for arguments, said in (
        (script_change(cross, made, script_id=made["id"]), "one of your own scripts"),
        (script_change(cross, made, script_id=library["id"], base_version=1), "is a library"),
        (script_change(cross, made, base_version=3), "is at version 1"),
        (script_change(cross, made, from_script_id=cross["id"], from_version=1), "one of your own scripts, saved"),
        (script_change(cross, made, from_version=9), "No version 9"),
    ):
        with pytest.raises(ProposalRefused, match=said):
            proposals.propose_script(CONVERSATION, 0, arguments)


def test_a_rule_not_live_is_changed_as_proposed_its_week_in_shadow_starting_again(store, scripts, cross, rules, proposals):
    rule = created(rules, cross)["rule"]
    newer = scripts.save(cross["id"], 1, TUNED)

    proposal = proposals.propose_rule(CONVERSATION, 0, {
        "rule_id": rule["id"], "script_version": newer["version"], "inputs": [{"id": "in_0", "value": 14}],
        "history_days": 3, "title": "Run v2 with 14", "rationale": "Its run held on 25 days."})

    assert proposal["changes"]["inputs"] == [{"id": "in_0", "title": "Fast", "before": 12, "after": 14}]
    assert proposal["changes"]["history_days"] == {"before": None, "after": 3}

    applied = proposals.apply(CONVERSATION, proposal["id"], None)

    changed = rules.rule(rule["id"])
    assert applied["result"]["script_version"] == 2 and changed["script"]["version"] == 2
    assert changed["inputs"][0]["value"] == 14 and changed["history_days"] == 3 and changed["mode"] == "shadow"
    assert changed["run_changed_at"] == changed["updated_at"]


def test_a_rule_proposal_after_a_script_proposal_waits_for_it(scripts, cross, rules, proposals):
    rule = created(rules, cross)["rule"]
    script_proposal = proposals.propose_script(CONVERSATION, 0, script_change(cross, draft(scripts)))
    rule_proposal = proposals.propose_rule(CONVERSATION, 0, {
        "rule_id": rule["id"], "after_proposal": script_proposal["id"], "title": "Run the new version",
        "rationale": "As proposed."})

    with pytest.raises(NotPossible, match="Apply the new version of the script first"):
        proposals.apply(CONVERSATION, rule_proposal["id"], None)
    proposals.apply(CONVERSATION, script_proposal["id"], None)
    proposals.apply(CONVERSATION, rule_proposal["id"], None)

    assert rules.rule(rule["id"])["script"]["version"] == 2


def test_a_live_rule_is_never_proposed_nor_changed(scripts, cross, rules, proposals):
    rule = created(rules, cross)["rule"]
    proposal = proposals.propose_rule(CONVERSATION, 0, {
        "rule_id": rule["id"], "inputs": [{"id": "in_0", "value": 20}], "title": "Faster", "rationale": "Why not."})
    rules.set_mode(rule["id"], LIVE, rules.load()["revision"])

    with pytest.raises(NotPossible, match="is live now"):
        proposals.apply(CONVERSATION, proposal["id"], None)
    with pytest.raises(ProposalRefused, match="is live"):
        proposals.propose_rule(CONVERSATION, 0, {"rule_id": rule["id"], "inputs": [{"id": "in_0", "value": 20}],
                                                 "title": "Faster", "rationale": "Why not."})
    assert rules.rule(rule["id"])["inputs"][0]["value"] == 12


def test_a_proposal_dismissed_is_kept_as_such(store, scripts, cross, proposals):
    proposal = proposals.propose_script(CONVERSATION, 0, script_change(cross, draft(scripts)))
    dismissed = proposals.dismiss(CONVERSATION, proposal["id"], "10.8.0.2")
    assert dismissed["status"] == DISMISSED and store.proposals(CONVERSATION)[proposal["id"]]["status"] == DISMISSED
    assert store.read(CONVERSATION, "activity")[-1]["event"] == DISMISSED


def test_a_stored_rule_saved_back_as_it_is_changes_nothing(cross, rules):
    rule = created(rules, cross)["rule"]
    revision = rules.load()["revision"]
    assert rules.update(rule["id"], fields_of(rule), revision)["revision"] == revision
    assert isinstance(RuleStore.find(rules.load()["rules"], rule["id"]), dict)


# ── settings of config.json ──

CONFIG = {
    "trade": {
        "rules": [
            {"rule_type": "market_hours", "rule_name": "market_hours",
             "rule_config": {"trading_start_hour": 9, "trading_end_hour": 21}},
            {"rule_type": "risk_management", "rule_name": "risk_management",
             "rule_config": {"cooldown_after_loss_minutes": 10, "max_trades_per_day": 8}},
        ],
        "config": {"position_management": {"performance_thresholds": {"stoploss_percent": -15,
                                                                      "max_profit_percent": 60}}},
    },
}
DONE = "20261027-101010-abcdef"
RUNNING = "20261028-090000-fedcba"
HEADLINE = {"total_percent": 12.5, "trades": 41, "days_tested": 22, "beaten": 0.97}


class LiveBacktests:
    """The backtests' service as a settings proposal reads it: the live configuration's settings, and runs."""

    def __init__(self, config=CONFIG):
        self.config = config

    def live_settings(self):
        return from_config(self.config)

    def get(self, run_id):
        state = {DONE: "done", RUNNING: "running"}.get(run_id)
        if state is None:
            raise UnknownRun(run_id)
        return {"id": run_id, "name": "Stop at -10", "kind": "run", "from": "2026-10-13", "to": "2026-10-27",
                "changes": {"stoploss_percent": -10}, "status": {"state": state},
                "headline": HEADLINE if state == "done" else None}


def tuned(**overrides):
    return {"changes": [{"setting": "stoploss_percent", "value": -10}, {"setting": "max_trades_per_day", "value": 4},
                        {"setting": "trading_start_hour", "value": 9}],
            "runs": [DONE], "title": "A tighter stop", "rationale": "Run X held on 22 days.", **overrides}


@pytest.fixture
def with_runs(store, scripts):
    return Proposals(store, scripts, clock=lambda: NOW, backtests=LiveBacktests())


def test_settings_are_proposed_where_config_json_keeps_them_resting_on_finished_runs(store, with_runs):
    proposal = with_runs.propose_settings(CONVERSATION, 0, tuned())

    assert proposal["kind"] == "settings" and proposal["status"] == "proposed"
    assert proposal["settings"] == [
        {"setting": "stoploss_percent", "label": "Stop-loss", "unit": "%", "group": "exits",
         "path": "trade.config.position_management.performance_thresholds.stoploss_percent",
         "before": -15, "after": -10},
        {"setting": "max_trades_per_day", "label": "Entries per day at most (0: no limit)", "unit": "",
         "group": "entries", "path": "trade.rules[risk_management].rule_config.max_trades_per_day",
         "before": 8, "after": 4},
    ]
    # The live value already: left out, and said.
    assert proposal["same"] == ["trading_start_hour"]
    assert proposal["runs"] == [{"id": DONE, "name": "Stop at -10", "kind": "run", "from": "2026-10-13",
                                 "to": "2026-10-27", "changes": {"stoploss_percent": -10}, "headline": HEADLINE}]
    assert [line["proposal"] for line in store.artefacts()] == ["settings"]


@pytest.mark.parametrize("changes, said", [
    ([{"setting": "nope", "value": 1}], "not a setting of backtest_options"),
    ([{"setting": "capital", "value": 500}], "the backtest's own"),
    ([{"setting": "news_window", "value": True}], "the operator's to change"),
    ([{"setting": "history_days", "value": 3}], "the operator's to change"),
    ([{"setting": "market_timezone", "value": "America/New_York"}], "the operator's to change"),
    ([{"setting": "news_minutes_before", "value": 20}], "window around US releases is off"),
    ([{"setting": "stoploss_percent", "value": 5}], "must be between -100 and -0.1"),
    ([{"setting": "trading_start_hour", "value": 22}], "end before they start"),
    # Within a run's bounds, beyond config.json's.
    ([{"setting": "no_entry_above_day_profit", "value": 150}], "from 0 up to 100 in config.json"),
    ([{"setting": "safety_units", "value": 1.5}], "whole number in config.json"),
    ([{"setting": "stoploss_percent", "value": -15}], "live one already"),
    ([{"setting": "stoploss_percent", "value": -10}, {"setting": "stoploss_percent", "value": -12}], "once"),
    ([], "Nothing to change"),
])
def test_what_a_settings_proposal_may_not_be(store, with_runs, changes, said):
    with pytest.raises(ProposalRefused, match=said):
        with_runs.propose_settings(CONVERSATION, 0, tuned(changes=changes))
    assert store.proposals(CONVERSATION) == {}


@pytest.mark.parametrize("runs, said", [
    ([], "Name the finished runs"), (["20261028-000000-aaaaaa"], "No run"), ([RUNNING], "is not finished")])
def test_a_settings_proposal_rests_on_finished_runs(with_runs, runs, said):
    with pytest.raises(ProposalRefused, match=said):
        with_runs.propose_settings(CONVERSATION, 0, tuned(runs=runs))


def test_without_backtests_settings_are_said_in_words(proposals):
    with pytest.raises(ProposalRefused, match="Backtests are switched off"):
        proposals.propose_settings(CONVERSATION, 0, tuned())


def test_a_change_of_settings_is_saved_by_the_editor_alone_and_never_adds_a_rule(with_runs):
    proposal = with_runs.propose_settings(CONVERSATION, 0, tuned())

    with pytest.raises(NotPossible, match="saved from its preview"):
        with_runs.apply(CONVERSATION, proposal["id"], None)
    config = Proposals.settings_config(with_runs.pending_settings(CONVERSATION, proposal["id"]), copy.deepcopy(CONFIG))
    assert config["trade"]["config"]["position_management"]["performance_thresholds"]["stoploss_percent"] == -10
    assert config["trade"]["rules"][1]["rule_config"] == {"cooldown_after_loss_minutes": 10, "max_trades_per_day": 4}

    # The rule removed from config.json meanwhile: never added back by the proposal.
    without = copy.deepcopy(CONFIG)
    del without["trade"]["rules"][1]
    with pytest.raises(NotPossible, match="no risk_management rule"):
        Proposals.settings_config(proposal, without)

    applied = with_runs.settings_saved(CONVERSATION, proposal["id"], "10.8.0.2", {
        "revision": "r2", "backup": "config.json.20261028-160000", "impact": {"services": ["trader"]}})
    assert applied["status"] == APPLIED and applied["settled_by"] == "10.8.0.2"
    assert applied["result"] == {"revision": "r2", "backup": "config.json.20261028-160000", "services": ["trader"]}
    with pytest.raises(NotPossible, match="applied already"):
        with_runs.pending_settings(CONVERSATION, proposal["id"])


def test_every_setting_proposed_is_one_the_configuration_page_may_change():
    for name in PROPOSED_SETTINGS:
        param = config_catalog.lookup(catalog_path(FIELDS_BY_NAME[name].source))
        assert param is not None and param.access == config_catalog.EDITABLE, name


# ── a new script of the operator's ──


def new_script(made, **changes):
    return {"from_script_id": made["id"], "from_version": made["version"], "name": "Cross,  slow",
            "description": "The cross with a slower fast line.", "title": "A slower cross",
            "rationale": "Run X held on 22 days.", **changes}


def test_a_new_script_is_the_operator_s_its_first_version_written_by_claude(store, scripts, proposals):
    made = draft(scripts)
    kept = len(scripts.scripts())

    proposal = proposals.propose_new_script(CONVERSATION, 0, new_script(made))
    assert proposal["kind"] == "new_script" and proposal["new_script"] == {
        "name": "Cross, slow", "description": "The cross with a slower fast line."}
    assert len(scripts.scripts()) == kept, "a proposal writes nothing"

    applied = proposals.apply(CONVERSATION, proposal["id"], "10.8.0.2")

    script = scripts.script(applied["result"]["script_id"])
    assert applied["result"] == {"script_id": script["id"], "version": 1, "name": "Cross, slow"}
    assert script["source"] == TUNED and script["description"] == "The cross with a slower fast line."
    assert script.get("made_by") is None and script["versions"][0]["made_by"] == "claude"


def test_what_a_new_script_may_not_be(scripts, cross, proposals):
    made = draft(scripts)
    library = scripts.create("Claude's lonely library", LIBRARY_ONLY, made_by="claude")
    for arguments, said in (
        (new_script(cross), "one of your own"),
        (new_script(library), "A library is the operator's"),
        (new_script(made, name="cross"), "named 'cross' already"),
        (new_script(made, from_version=4), "No version 4"),
    ):
        with pytest.raises(ProposalRefused, match=said):
            proposals.propose_new_script(CONVERSATION, 0, arguments)


def test_a_name_taken_meanwhile_is_said_when_applied(scripts, proposals):
    proposal = proposals.propose_new_script(CONVERSATION, 0, new_script(draft(scripts)))
    scripts.create("Cross, slow", STRATEGY)

    with pytest.raises(NotPossible, match="ask the agent for another name"):
        proposals.apply(CONVERSATION, proposal["id"], None)
    assert proposals.dismiss(CONVERSATION, proposal["id"], None)["status"] == DISMISSED
