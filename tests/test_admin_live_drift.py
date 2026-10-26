"""The live settings against their reference (src/admin/live_drift.py), and what health says of it."""

from src.admin.health import OK, WARNING, live_drift_entry
from src.admin.live_drift import live_days, warning

REFERENCE = {"run_id": "r1", "name": "Replay of September", "days": 20, "low": -2.0, "median": 0.1, "high": 1.5}


def paper(day: str, result: float | None, state: str = "done") -> dict:
    return {"day": day, "run_id": f"p-{day}", "state": state, "day_percent": result, "trades": 2, "outside": None}


def test_each_day_sets_its_real_and_paper_results_beside_the_range_newest_first():
    days = live_days([paper("2026-10-19", -3.0), paper("2026-10-20", 0.5)], {"2026-10-20": 2.0}, REFERENCE)

    assert [day["day"] for day in days] == ["2026-10-20", "2026-10-19"]
    assert (days[0]["real_percent"], days[0]["real_outside"], days[0]["paper_outside"]) == (2.0, True, False)
    assert (days[1]["real_percent"], days[1]["real_outside"], days[1]["paper_outside"]) == (None, None, True)
    # Without a reference nothing is judged.
    assert live_days([paper("2026-10-19", -3.0)], {}, None)[0]["paper_outside"] is None


def test_the_warning_needs_the_last_two_days_with_a_result_outside():
    two = live_days([paper("2026-10-19", -3.0), paper("2026-10-20", -2.5)], {}, REFERENCE)
    one = live_days([paper("2026-10-19", -3.0), paper("2026-10-20", 0.0)], {}, REFERENCE)
    # A day still to run has no result and is passed over.
    pending = live_days([paper("2026-10-16", -4.0), paper("2026-10-19", -3.0), paper("2026-10-20", None, "running")],
                        {}, REFERENCE)

    assert warning(two, REFERENCE) == (
        "2 days in a row outside the live reference's daily range (-2.00 to +1.50 %): "
        "2026-10-20 paper -2.50 %; 2026-10-19 paper -3.00 %"
    )
    assert warning(one, REFERENCE) is None
    assert warning(pending, REFERENCE).endswith("2026-10-19 paper -3.00 %; 2026-10-16 paper -4.00 %")
    assert warning(live_days([paper("2026-10-20", -9.0)], {}, REFERENCE), REFERENCE) is None


def test_a_real_day_outside_counts_as_a_paper_one_does():
    days = live_days([paper("2026-10-19", -3.0)], {"2026-10-20": 3.1}, REFERENCE)

    assert warning(days, REFERENCE).endswith("2026-10-20 real +3.10 %; 2026-10-19 paper -3.00 %")


def test_health_warns_with_what_is_said_and_says_nothing_without_a_reference():
    days = live_days([paper("2026-10-19", -3.0), paper("2026-10-20", -2.5)], {}, REFERENCE)

    [warned] = live_drift_entry(days, warning(days, REFERENCE), REFERENCE)
    [calm] = live_drift_entry(live_days([paper("2026-10-20", 0.0)], {}, REFERENCE), None, REFERENCE)

    assert (warned["id"], warned["status"]) == ("live_drift", WARNING) and warned["detail"].startswith("2 days in a row")
    assert (calm["status"], calm["detail"]) == (OK, "Within the range of Replay of September: 1 day set beside it")
    assert live_drift_entry([], None, None) == []
