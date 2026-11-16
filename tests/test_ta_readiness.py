"""
Whether a signal rule is ready to go live, as the server holds it
(src/admin/ta_readiness.py), the same checks the Signal rules page shows
(admin_ui/src/features/ta/signals/readiness.ts).
"""

from datetime import date, datetime, timezone

from src.admin.ta_readiness import EDGE_OFF, SHADOW_DAYS, unmet_readiness, weekdays_between

NOW = datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc)  # a Monday


def rule(mode="shadow", since="2026-10-16T08:00:00Z"):
    return {"id": "r1", "mode": mode, "mode_changed_at": since}


RUNNING = {"state": "running", "last_alert_at": "2026-10-23T14:00:00Z"}


def edge(verdict="as_expected", n=24, rank=40.0, reasons=None) -> dict:
    """The rule's Edge Watch (src/admin/edge_watch_service.py): its paper trades against its reference."""
    return {"tracks": {"paper": {"verdict": verdict, "n": n, "rank": rank, "reasons": reasons or []}}}


READY = edge()


def test_a_week_of_weekdays_in_shadow_with_its_signals_is_ready():
    assert weekdays_between(date(2026, 10, 16), date(2026, 10, 26)) == 6
    assert unmet_readiness(rule(), RUNNING, READY, NOW, "Europe/Paris") == []


def test_fewer_weekdays_in_shadow_are_said():
    assert weekdays_between(date(2026, 10, 22), date(2026, 10, 26)) == 2
    assert unmet_readiness(rule(since="2026-10-22T08:00:00Z"), RUNNING, READY, NOW, "Europe/Paris") == [
        f"2 weekday(s) in shadow of {SHADOW_DAYS}",
    ]


def test_a_rule_off_a_silent_one_a_failing_one_and_one_whose_paper_trades_look_random_are_not_ready():
    broken = edge("broken", n=31, rank=2.0)

    assert unmet_readiness(rule(mode="off"), {"state": "error", "detail": "the runner stopped"}, broken, NOW, "Europe/Paris") == [
        "it is not in shadow: what it would send is not being journaled",
        "no signal journaled yet",
        "its paper trades look like random entries', not like its reference's",
        "the engine fails to run it (the runner stopped)",
    ]


def test_its_paper_trades_must_be_twenty_its_reference_s_kind_and_judged():
    assert unmet_readiness(rule(), RUNNING, None, NOW, "Europe/Paris") == [
        "no reference run to judge its paper trades against (set one from a run of the rule)",
    ]
    assert unmet_readiness(rule(), RUNNING, EDGE_OFF, NOW, "Europe/Paris") == [
        "Edge Watch is not running (it needs the backtests): its paper trades are not judged",
    ]
    assert unmet_readiness(rule(), RUNNING, edge("too_early", n=7), NOW, "Europe/Paris") == [
        "7 paper trade(s) since its reference of 20",
    ]
    assert unmet_readiness(rule(), RUNNING, edge("watch", rank=3.0), NOW, "Europe/Paris") == [
        "its paper result is under 5 % of its reference's paths",
    ]
    assert unmet_readiness(rule(), RUNNING, edge("no_edge_over_random"), NOW, "Europe/Paris") == [
        "its reference does no better than random entries",
    ]
    # Worth watching for another reason (a long losing streak), it is not held back.
    assert unmet_readiness(rule(), RUNNING, edge("watch", rank=30.0), NOW, "Europe/Paris") == []


def test_a_change_of_what_a_rule_runs_starts_its_week_in_shadow_again():
    changed = {**rule(), "run_changed_at": "2026-10-23T08:00:00Z"}
    assert unmet_readiness(changed, RUNNING, READY, NOW, "Europe/Paris") == [f"1 weekday(s) in shadow of {SHADOW_DAYS}"]
    # A change before it went into shadow counts from its going into shadow.
    earlier = {**rule(), "run_changed_at": "2026-10-01T08:00:00Z"}
    assert unmet_readiness(earlier, RUNNING, READY, NOW, "Europe/Paris") == []


def test_a_rule_without_its_moment_of_change_has_no_day_in_shadow():
    assert unmet_readiness({"id": "r1", "mode": "shadow"}, RUNNING, READY, NOW, "Europe/Paris") == [
        f"0 weekday(s) in shadow of {SHADOW_DAYS}",
    ]


def test_its_script_holds_it_back_when_it_cannot_be_a_rule_repaints_or_was_never_tested_as_it_runs():
    def said(verdict, worst=None, **more):
        check = {"verdict": verdict, "worst": {"id": worst, "level": "blocks"} if worst else None, **more}
        return unmet_readiness(rule(), RUNNING, READY, NOW, "Europe/Paris", rule_check=check)

    assert said("needs_alerts", "no-alerts") == ["its script cannot be a rule as it is (no alert)"]
    assert said("blocked", "live-ticker") == ["its script cannot be a rule as it is (a series WATA reads only in backtests)"]
    assert said("repaints") == ["its script repaints: its alerts differ live and in a backtest"]
    assert said("may_repaint") == ["its script may repaint, and was never tested on recorded bars as this rule runs it"]
    assert said("may_repaint", as_rule=True) == ["its script may repaint: its run test as this rule runs it had no alert to compare"]
    # What only asks a change, and a script whose check could not be read, hold nothing back.
    assert said("to_change") == [] and said("ready") == []
    assert unmet_readiness(rule(), RUNNING, READY, NOW, "Europe/Paris", rule_check=None) == []
