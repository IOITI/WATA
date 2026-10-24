"""
Whether a signal rule is ready to go live, as the server holds it
(src/admin/ta_readiness.py), the same checks the Signal rules page shows
(admin_ui/src/features/ta/signals/readiness.ts).
"""

from datetime import date, datetime, timezone

from src.admin.ta_readiness import SHADOW_DAYS, unmet_readiness, weekdays_between

NOW = datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc)  # a Monday


def rule(mode="shadow", since="2026-10-16T08:00:00Z"):
    return {"id": "r1", "mode": mode, "mode_changed_at": since}


RUNNING = {"state": "running", "last_alert_at": "2026-10-23T14:00:00Z"}


def test_a_week_of_weekdays_in_shadow_with_its_signals_is_ready():
    assert weekdays_between(date(2026, 10, 16), date(2026, 10, 26)) == 6
    assert unmet_readiness(rule(), RUNNING, None, NOW, "Europe/Paris") == []


def test_fewer_weekdays_in_shadow_are_said():
    assert weekdays_between(date(2026, 10, 22), date(2026, 10, 26)) == 2
    assert unmet_readiness(rule(since="2026-10-22T08:00:00Z"), RUNNING, None, NOW, "Europe/Paris") == [
        f"2 weekday(s) in shadow of {SHADOW_DAYS}",
    ]


def test_a_rule_off_a_silent_one_a_failing_one_and_paper_days_outside_its_range_are_not_ready():
    paper = {"days": [{"day": "2026-10-23", "outside": True}, {"day": "2026-10-22", "outside": False},
                      {"day": "2026-10-21", "outside": None}]}

    assert unmet_readiness(rule(mode="off"), {"state": "error", "detail": "the runner stopped"}, paper, NOW, "Europe/Paris") == [
        "it is not in shadow: what it would send is not being journaled",
        "no signal journaled yet",
        "1 day(s) on paper outside the range of its reference run",
        "the engine fails to run it (the runner stopped)",
    ]


def test_a_rule_without_its_moment_of_change_has_no_day_in_shadow():
    assert unmet_readiness({"id": "r1", "mode": "shadow"}, RUNNING, None, NOW, "Europe/Paris") == [
        f"0 weekday(s) in shadow of {SHADOW_DAYS}",
    ]
