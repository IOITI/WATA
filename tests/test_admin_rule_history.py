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
from src.admin.rule_history import HISTORY_FILE, RecordedRuleStore, RuleHistory, definition_at, run_fields
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
