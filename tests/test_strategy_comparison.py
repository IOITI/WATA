from datetime import date

from src.strategy.comparison import Leg, compare
from src.strategy.engine import LONG, SHORT, LocalClock

CLOCK = LocalClock("Europe/Paris")
DAY = date(2026, 10, 12)
NEXT_DAY = date(2026, 10, 13)

TARGET, STOP, END = "Daily profit target", "Stop-loss", "Signal or end of day"


def at(clock_time: str, day: date = DAY) -> int:
    return CLOCK.epoch(day, clock_time[:5]) + int(clock_time[6:] or 0)


def leg(direction: str, entered: str, left: str, performance: float, exit: str, reference: str = "", day: date = DAY) -> Leg:
    return Leg(direction, at(entered, day), at(left, day), performance, exit, reference)


def test_a_real_trade_is_paired_with_the_models_trade_entered_the_same_way_at_about_the_same_moment():
    real = [leg(LONG, "09:00:03", "09:05:00", 1.9, TARGET, "p1"), leg(SHORT, "11:00:02", "11:20:00", -15.6, STOP, "p2")]
    model = [leg(LONG, "09:00:10", "09:04:00", 1.7, TARGET), leg(SHORT, "11:00:10", "11:25:00", -15.0, STOP)]

    found = compare(real, model, CLOCK)

    assert [(pair["real"]["reference"], pair["gap"], pair["same_exit"], pair["left_later"]) for pair in found["pairs"]] == [
        ("p1", -0.2, True, -60), ("p2", 0.6, True, 300),
    ]
    assert (found["only_real"], found["only_model"]) == ([], [])
    summary = found["summary"]
    assert (summary["real_trades"], summary["model_trades"], summary["paired"], summary["same_exit"]) == (2, 2, 2, 2)
    # The model minus the real result: on average, and how far apart whatever the side.
    assert (summary["average_gap"], summary["median_gap"], summary["ninth_gap"]) == (0.2, 0.4, 0.56)


def test_a_trade_only_one_of_the_two_made_is_named():
    real = [leg(LONG, "09:00:00", "09:05:00", 1.9, TARGET, "p1"), leg(LONG, "15:00:00", "15:30:00", -3.0, END, "p2")]
    model = [
        # The other way: not the same trade, however close.
        leg(SHORT, "09:00:10", "09:04:00", 1.7, TARGET),
        # The same way, three minutes later: neither.
        leg(LONG, "15:03:00", "15:30:00", -2.0, END),
    ]

    found = compare(real, model, CLOCK)

    assert found["pairs"] == []
    assert [entry["reference"] for entry in found["only_real"]] == ["p1", "p2"]
    assert [entry["direction"] for entry in found["only_model"]] == [SHORT, LONG]
    assert (found["summary"]["paired"], found["summary"]["average_gap"], found["summary"]["median_gap"]) == (0, None, None)


def test_of_two_trades_of_the_model_the_nearest_is_the_pair_and_each_is_paired_once():
    real = [leg(LONG, "09:00:00", "09:05:00", 1.0, TARGET, "p1"), leg(LONG, "09:01:00", "09:06:00", 2.0, TARGET, "p2")]
    model = [leg(LONG, "09:00:50", "09:05:00", 5.0, TARGET), leg(LONG, "09:00:10", "09:05:00", 3.0, TARGET)]

    found = compare(real, model, CLOCK)

    assert [(pair["real"]["reference"], pair["model"]["performance"]) for pair in found["pairs"]] == [("p1", 3.0), ("p2", 5.0)]


def test_a_pair_that_ended_differently_is_said_so():
    found = compare([leg(LONG, "09:00:00", "09:05:00", -15.4, STOP, "p1")], [leg(LONG, "09:00:10", "21:55:00", 0.8, END)], CLOCK)

    assert (found["pairs"][0]["same_exit"], found["pairs"][0]["gap"], found["summary"]["same_exit"]) == (False, 16.2, 0)


def test_the_days_of_both_are_set_side_by_side_as_wata_counts_a_day():
    real = [
        leg(LONG, "09:00:00", "09:05:00", -5.0, END, "p1"),
        leg(SHORT, "10:00:00", "10:30:00", 7.05, TARGET, "p2"),
        leg(LONG, "09:00:00", "09:10:00", 1.8, TARGET, "p3", day=NEXT_DAY),
    ]
    model = [leg(LONG, "09:00:10", "09:05:00", -4.0, END), leg(SHORT, "10:00:10", "10:20:00", 5.94, TARGET)]

    found = compare(real, model, CLOCK)

    assert found["days"] == [
        # 0.95 x 1.0705 and 0.96 x 1.0594: each result applied on the one before.
        {"day": "2026-10-12", "real_percent": 1.7, "model_percent": 1.7, "gap": 0.0, "real_trades": 2, "model_trades": 2},
        {"day": "2026-10-13", "real_percent": 1.8, "model_percent": 0.0, "gap": -1.8, "real_trades": 1, "model_trades": 0},
    ]
    summary = found["summary"]
    assert (summary["days"], summary["real_total"], summary["model_total"], summary["median_day_gap"]) == (2, 3.53, 1.7, 0.9)


def test_a_trade_belongs_to_the_day_it_ended_on_in_the_trading_timezone():
    # Entered on Monday evening, sold after midnight in Paris: 22:30 UTC is already Tuesday there.
    overnight = Leg(LONG, at("21:00"), at("00:30", NEXT_DAY), 2.0, END, "p1")

    assert [day["day"] for day in compare([overnight], [], CLOCK)["days"]] == ["2026-10-13"]


def test_nothing_on_either_side_compares_to_nothing():
    found = compare([], [], CLOCK)

    assert (found["pairs"], found["days"], found["only_real"], found["only_model"]) == ([], [], [], [])
    assert (found["summary"]["real_total"], found["summary"]["model_total"], found["summary"]["median_day_gap"]) == (0.0, 0.0, None)
