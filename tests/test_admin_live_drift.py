"""The live days (src/admin/live_drift.py), and what health says of Edge Watch (src/admin/health.py)."""

from src.admin.edge_watch import AS_EXPECTED, BROKEN
from src.admin.health import OK, WARNING, edge_watch_entry
from src.admin.live_drift import live_days


def paper(day: str, result: float | None, state: str = "done") -> dict:
    return {"day": day, "run_id": f"p-{day}", "state": state, "day_percent": result, "trades": 2}


def test_each_day_sets_its_real_and_paper_results_side_by_side_newest_first():
    days = live_days([paper("2026-10-19", -3.0), paper("2026-10-20", 0.5)], {"2026-10-20": 2.0})

    assert [day["day"] for day in days] == ["2026-10-20", "2026-10-19"]
    assert (days[0]["real_percent"], days[0]["paper_percent"]) == (2.0, 0.5)
    assert (days[1]["real_percent"], days[1]["paper_percent"], days[1]["paper_run_id"]) == (None, -3.0, "p-2026-10-19")
    assert "real_outside" not in days[0]


def _state(verdict: str | None, *, rules: dict | None = None, differ: list[str] | None = None, age: float = 10.0) -> dict:
    real = None if verdict is None else {
        "verdict": verdict, "n": 24, "reasons": ["its trades look like random entries' rather than its reference's"],
        "settings_differ": differ or [],
    }
    live = None if verdict is None else {"tracks": {"real": real}}
    return {"judgements": {"live": live, "rules": rules or {}}, "age": age}


def test_health_warns_when_the_live_trades_are_broken_and_says_as_expected_otherwise():
    [broken] = edge_watch_entry(_state(BROKEN), guard_on=False)
    [calm] = edge_watch_entry(_state(AS_EXPECTED), guard_on=False)

    assert (broken["id"], broken["status"]) == ("edge_watch", WARNING)
    assert broken["detail"].startswith("The live trades: broken after 24 trades: its trades look like random")
    assert (calm["status"], calm["detail"]) == (OK, "The live trades: as expected after 24 trades")


def test_health_says_nothing_while_nothing_is_watched_and_the_guard_is_off():
    assert edge_watch_entry(_state(None), guard_on=False) == []
    assert edge_watch_entry({"judgements": None, "age": None}, guard_on=False) == []
    [rules] = edge_watch_entry(_state(None, rules={"a1": {}, "b2": {}}), guard_on=False)
    assert (rules["status"], rules["detail"]) == (OK, "2 rules watched on paper; no live reference")


def test_health_warns_when_the_guard_is_on_and_cannot_act():
    [no_reference] = edge_watch_entry(_state(None), guard_on=True)
    [differ] = edge_watch_entry(_state(AS_EXPECTED, differ=["stoploss_percent"]), guard_on=True)
    [silent] = edge_watch_entry(_state(AS_EXPECTED, age=600.0), guard_on=True)

    assert no_reference["status"] == WARNING and "no live reference" in no_reference["detail"]
    assert differ["status"] == WARNING and "stoploss_percent" in differ["detail"]
    assert silent["status"] == WARNING and "has not judged" in silent["detail"]
    # The guard off, settings that differ are said on the Performance page, not as a warning.
    assert edge_watch_entry(_state(AS_EXPECTED, differ=["stoploss_percent"]), guard_on=False)[0]["status"] == OK
