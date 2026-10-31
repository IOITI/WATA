"""
What each signal rule ran, and since when (src/admin/rule_history.py): the
line each change of what a rule runs leaves beside rules.json, what a rule ran
at a moment, and the writers of the admin that keep them.
"""

import json
import os

import pytest

from src.admin.agent.proposals import Proposals
from src.admin.agent.store import ConversationStore
from src.admin.mcp_server.facade import ResearchServices
from src.admin.rule_history import (
    HISTORY_FILE,
    RecordedRuleStore,
    RuleHistory,
    changes_between,
    definition_at,
    rule_timeline,
    run_fields,
)
from tests.test_admin_api import SAME_ORIGIN  # noqa: F401
from tests.test_admin_ta_rules import (  # noqa: F401 (fixtures)
    STRATEGY,
    api,
    body,
    config_path,
    cross,
    ctx,
    feed_config_path,
    fields,
    scripts,
)

DAY = 86_400
# 2026-10-23 12:00 UTC: a day of the past.
NOON = 1_792_756_800


@pytest.fixture
def recorded(scripts):
    return RecordedRuleStore(scripts)


def history_of(scripts) -> list[dict]:
    return RuleHistory(scripts.root).lines()


def test_a_rule_made_changed_and_deleted_leaves_what_it_ran_each_time_and_nothing_else(recorded, scripts, cross):
    rule = recorded.create(fields(cross), recorded.load()["revision"])["rule"]
    made = history_of(scripts)
    assert made == [{"rule_id": rule["id"], "at": rule["created_at"], "after": run_fields(rule)}]
    # What it runs, not its name, its alerts nor its mode.
    assert set(made[0]["after"]) == {"asset", "side", "bars", "script", "libraries", "inputs"}

    renamed = recorded.update(rule["id"], fields(cross, name="Renamed", alerts=[{"title": "Exit", "action": "close-position"}]),
                              recorded.load()["revision"])["rule"]
    recorded.set_mode(rule["id"], "off", recorded.load()["revision"])
    assert history_of(scripts) == made

    tuned = recorded.update(rule["id"], fields(cross, name="Renamed", inputs=[
        {"id": "in_0", "var": "fast", "title": "Fast", "value": 20}]), recorded.load()["revision"])["rule"]
    changed = history_of(scripts)[1]
    assert changed == {
        "rule_id": rule["id"], "at": tuned["updated_at"], "before": run_fields(renamed),
        "before_since": rule["created_at"], "after": run_fields(tuned),
    }
    assert changed["before"]["inputs"][0]["value"] == 12 and changed["after"]["inputs"][0]["value"] == 20

    recorded.delete(rule["id"], recorded.load()["revision"])
    deleted = history_of(scripts)[2]
    assert deleted["deleted"] is True and deleted["before"] == run_fields(tuned)
    assert deleted["before_since"] == tuned["run_changed_at"]


def test_a_line_that_cannot_be_written_leaves_the_rule_saved(recorded, scripts, cross, caplog):
    # A folder where the file would be: nothing can be appended to it.
    os.makedirs(os.path.join(scripts.root, HISTORY_FILE))

    rule = recorded.create(fields(cross), recorded.load()["revision"])["rule"]

    assert [entry["id"] for entry in recorded.load()["rules"]] == [rule["id"]]
    assert "could not be written" in caplog.text


def test_a_line_that_cannot_be_read_is_passed_over(scripts):
    with open(os.path.join(scripts.root, HISTORY_FILE), "w", encoding="utf-8") as history_file:
        history_file.write('{"rule_id": "a", "at": "2026-10-20T08:00:00+00:00", "after": {}}\nnot json\n[1]\n')

    assert history_of(scripts) == [{"rule_id": "a", "at": "2026-10-20T08:00:00+00:00", "after": {}}]


# ── what a rule ran at a moment ──

V5 = {"script": {"id": "0d4fd1117e2f", "version": 5}, "inputs": [{"id": "in_18", "var": "st", "value": 2}]}
V9 = {"script": {"id": "0d4fd1117e2f", "version": 9}, "inputs": [{"id": "in_18", "var": "st", "value": 2}]}
V10 = {"script": {"id": "0d4fd1117e2f", "version": 10}, "inputs": [{"id": "in_18", "var": "st", "value": 1}]}


def rule_now(**dates) -> dict:
    return {"id": "86fc6f91463e", "created_at": "2026-10-21T19:56:21+00:00", **V10, **dates}


def iso(moment: int) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(moment, timezone.utc).isoformat(timespec="seconds")


def test_a_rule_unchanged_since_a_moment_ran_then_what_it_runs_now():
    rule = rule_now(run_changed_at=iso(NOON - 2 * DAY))

    assert definition_at([], rule, rule["id"], NOON) == (run_fields(rule), True)
    # Changed after it, and no line says what it ran before: what it runs now, not known to be what ran.
    assert definition_at([], rule_now(run_changed_at=iso(NOON + 3600)), rule["id"], NOON) == (run_fields(rule), False)
    # Made after it.
    assert definition_at([], rule_now(created_at=iso(NOON + 3600)), rule["id"], NOON)[1] is False


def test_the_lines_say_what_a_rule_ran_before_and_after_each_change():
    rule = rule_now(run_changed_at=iso(NOON + 6 * DAY))
    lines = [
        {"rule_id": rule["id"], "at": iso(NOON - DAY), "before": V5, "before_since": iso(NOON - 4 * DAY), "after": V9},
        {"rule_id": "another00000", "at": iso(NOON - 3600), "before": V10, "before_since": iso(NOON - 9 * DAY), "after": V5},
        {"rule_id": rule["id"], "at": iso(NOON + 6 * DAY), "before": V9, "before_since": iso(NOON - DAY), "after": V10},
    ]

    # After the first change and before the second: what came after the first.
    assert definition_at(lines, rule, rule["id"], NOON) == (V9, True)
    # Before the first, since what it held began: what came before it.
    assert definition_at(lines, rule, rule["id"], NOON - 2 * DAY) == (V5, True)
    # Before even that: unknown, what it held then is the nearest guess.
    assert definition_at(lines, rule, rule["id"], NOON - 5 * DAY) == (V5, False)
    # After the last: what it runs now.
    assert definition_at(lines, rule, rule["id"], iso(NOON + 7 * DAY)) == (V10, True)


def test_a_deleted_rule_s_days_are_still_known_and_none_after_it():
    lines = [{"rule_id": "86fc6f91463e", "at": iso(NOON + DAY), "before": V9, "before_since": iso(NOON - DAY), "deleted": True}]

    assert definition_at(lines, None, "86fc6f91463e", NOON) == (V9, True)
    assert definition_at(lines, None, "86fc6f91463e", NOON + 2 * DAY) == (None, False)
    assert definition_at([], None, "86fc6f91463e", NOON) == (None, False)


# ── the writers ──


def test_the_ta_routes_keep_what_each_rule_runs(api, cross, scripts):
    client, _ = api
    revision = client.get("/api/ta/rules").json()["revision"]

    made = client.post("/api/ta/rules", json=body(cross, revision), headers=SAME_ORIGIN).json()
    client.put(f"/api/ta/rules/{made['rule']['id']}", json=body(cross, made["revision"], side="ask"), headers=SAME_ORIGIN)

    lines = history_of(scripts)
    assert [line["rule_id"] for line in lines] == [made["rule"]["id"]] * 2
    assert (lines[0]["after"]["side"], lines[1]["after"]["side"]) == ("bid", "ask")


def test_the_ai_agent_s_applied_changes_are_kept_too(tmp_path, scripts, cross):
    rules = RecordedRuleStore(scripts)
    rule = rules.create(fields(cross), rules.load()["revision"])["rule"]
    store = ConversationStore(str(tmp_path / "agent"))
    store.create({"id": "20261028-080000-a1b2c3", "title": "Tune", "status": "idle", "request": 0})
    proposals = Proposals(store, scripts, clock=lambda: 1_793_174_400.0)

    proposal = proposals.propose_rule("20261028-080000-a1b2c3", 0, {
        "rule_id": rule["id"], "inputs": [{"id": "in_0", "value": 14}], "title": "Fourteen", "rationale": "Held."})
    proposals.apply("20261028-080000-a1b2c3", proposal["id"], None)

    assert history_of(scripts)[-1]["after"]["inputs"][0]["value"] == 14


def test_claude_s_shadow_rules_are_saved_through_the_store_that_keeps_them(ctx, scripts):
    ctx.ta_scripts = scripts

    assert isinstance(ResearchServices.from_context(ctx).rules, RecordedRuleStore)


def test_the_file_is_one_object_a_line(recorded, scripts, cross):
    recorded.create(fields(cross), recorded.load()["revision"])

    with open(os.path.join(scripts.root, HISTORY_FILE), encoding="utf-8") as history_file:
        assert [json.loads(line)["rule_id"] for line in history_file.read().splitlines()] == [recorded.load()["rules"][0]["id"]]


# ── a rule's page: what it ran over time ──


def test_a_rule_s_history_says_what_it_ran_and_its_switches_into_and_out_of_live_newest_first(api, cross):
    client, _ = api
    made = client.post("/api/ta/rules", json=body(cross, client.get("/api/ta/rules").json()["revision"]),
                       headers=SAME_ORIGIN).json()
    rule_id = made["rule"]["id"]
    faster = [{"id": "in_0", "var": "fast", "title": "Fast", "value": 20}]
    tuned = client.put(f"/api/ta/rules/{rule_id}", json=body(cross, made["revision"], inputs=faster),
                       headers=SAME_ORIGIN).json()
    # Taken live short of its readiness, changed while live (what it runs and its name), back in shadow.
    live = client.post(f"/api/ta/rules/{rule_id}/mode", json={"mode": "live", "revision": tuned["revision"], "force": True},
                       headers=SAME_ORIGIN).json()
    asked = client.put(f"/api/ta/rules/{rule_id}", json=body(cross, live["revision"], inputs=faster, side="ask",
                                                              name="Cross, asked"), headers=SAME_ORIGIN).json()
    client.post(f"/api/ta/rules/{rule_id}/mode", json={"mode": "shadow", "revision": asked["revision"]}, headers=SAME_ORIGIN)

    answer = client.get(f"/api/ta/rules/{rule_id}/history")

    assert answer.status_code == 200 and answer.json()["mode"] == "shadow"
    left, while_live, went_live, changed, made_event = answer.json()["events"]
    assert made_event["kind"] == "made" and made_event["by"] is None
    assert made_event["runs"]["script"] == {"id": cross["id"], "name": "Cross", "version": 1}
    assert (made_event["runs"]["inputs"], made_event["runs"]["bars"]) == (1, {"type": "time", "timeframe": "1"})
    assert changed["kind"] == "changed" and changed["live"] is False
    assert changed["changes"] == [{"what": "input", "id": "in_0", "name": "Fast", "before": 12, "after": 20}]
    assert went_live["kind"] == "live" and went_live["unmet"] == ["0 weekday(s) in shadow of 5", "no signal journaled yet"]
    assert (went_live["licence"], went_live["proposed"]) == (None, None)
    # The live rule's change said once: what it runs from its line, its name from the audit trail.
    assert while_live["kind"] == "changed" and while_live["live"] is True and while_live["also"] == ["name"]
    assert while_live["changes"] == [{"what": "side", "before": "bid", "after": "ask"}]
    assert (left["kind"], left["to"], left["proposed"]) == ("left_live", "shadow", None)
    # Nobody's address: the audit trail's are left out.
    assert all("ip" not in event for event in answer.json()["events"]) and "testclient" not in answer.text


def test_the_history_of_a_rule_that_is_not_there_is_not_found(api):
    client, _ = api

    assert client.get("/api/ta/rules/0123456789ab/history").status_code == 404


def test_a_rule_made_before_its_history_and_a_switch_the_ai_agent_proposed_are_read_back():
    rule = {"id": "a1b2c3d4e5f6", "created_at": "2026-10-01T08:00:00+00:00", "made_by": "claude"}
    audit = [
        {"at": "2026-10-20T09:00:00+00:00", "ip": "10.8.0.2",
         "action": "signal rule live WITHOUT its readiness (no signal journaled yet), the licence of its script "
                   "(CC BY-NC-SA 4.0) checked by the operator",
         "detail": "rule 'Lux' (a1b2c3d4e5f6): indice=us100 asset=capital/US100 bid bars=R60 script='Lux' v2 "
                   "Proposed by the AI agent: 'Take Lux live'"},
        {"at": "2026-10-19T09:00:00+00:00", "ip": "10.8.0.2", "action": "signal rule live",
         "detail": "rule 'Other' (ffffffffffff): indice=us100"},
        {"at": "2026-10-21T09:00:00+00:00", "ip": "10.8.0.2", "action": "live signal rule changed",
         "detail": "rule 'Lux' (a1b2c3d4e5f6): indice=us100 changed=name,alerts"},
    ]

    events = rule_timeline([], audit, rule, {})

    assert events == [
        # A change of the live rule that changed nothing it runs: said from the audit trail alone.
        {"at": "2026-10-21T09:00:00+00:00", "kind": "changed_live", "fields": ["name", "alerts"]},
        {"at": "2026-10-20T09:00:00+00:00", "kind": "live", "unmet": ["no signal journaled yet"],
         "licence": "CC BY-NC-SA 4.0", "proposed": "Take Lux live"},
        # Made before WATA kept what each rule ran: when and by whom, not what it ran then.
        {"at": "2026-10-01T08:00:00+00:00", "kind": "made", "by": "claude", "runs": None},
    ]


def test_a_change_says_each_field_it_changed_and_each_input_by_its_name():
    before = {
        "script": {"id": "s1", "version": 1, "sha256": "a"}, "libraries": [{"name": "Maths", "id": "m", "version": 1}],
        "asset": {"provider": "capital", "key": "US100"}, "side": "bid", "bars": {"type": "time", "timeframe": "1"},
        "inputs": [{"id": "in_0", "var": "fast", "title": "Fast", "value": 12},
                   {"id": "in_2", "var": "slow", "title": None, "value": 30}],
    }
    after = {
        **before, "script": {"id": "s1", "version": 2, "sha256": "b"}, "libraries": [{"name": "Maths", "id": "m", "version": 2}],
        "bars": {"type": "range", "ticks": 60}, "history_days": 0,
        "inputs": [{"id": "in_0", "var": "fast", "title": "Fast", "value": 12},
                   {"id": "in_10", "var": "band", "title": None, "value": 2.5}],
    }

    assert changes_between(before, after, {"s1": "Cross"}) == [
        {"what": "script", "before": {"id": "s1", "name": "Cross", "version": 1},
         "after": {"id": "s1", "name": "Cross", "version": 2}},
        {"what": "libraries", "before": [{"name": "Maths", "version": 1}], "after": [{"name": "Maths", "version": 2}]},
        {"what": "bars", "before": {"type": "time", "timeframe": "1"}, "after": {"type": "range", "ticks": 60}},
        {"what": "history_days", "before": None, "after": 0},
        {"what": "input", "id": "in_2", "name": "slow", "before": 30, "after": None},
        {"what": "input", "id": "in_10", "name": "band", "before": None, "after": 2.5},
    ]
