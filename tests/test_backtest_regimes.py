"""
The kind of day each day was (src/backtest/regimes.py), and the macro
calendar it reads (src/backtest/data/macro_calendar.csv).
"""

from array import array
from collections import Counter
from datetime import date, datetime

import pytest

from src.backtest.clock import TradingClock
from src.backtest.data import AssetSeries
from src.backtest.regimes import (
    MacroEvent,
    day_features,
    label_days,
    minutes_after_open,
    read_calendar,
    session,
    session_close,
    session_part,
    us_open,
)

CLOCK = TradingClock("Europe/Paris")
DAY = date(2026, 10, 14)


def at(clock_time: str, day: date = DAY) -> float:
    return datetime.fromisoformat(f"{day.isoformat()}T{clock_time}+00:00").timestamp()


def series(price, first: str = "07:00:00", last: str = "20:00:00", day: date = DAY) -> AssetSeries:
    """Ten-second bars of ``day`` from ``first`` to ``last`` (UTC), priced by ``price(step)``."""
    found = AssetSeries(ts=array("q"), close=array("d"))
    moment, step = at(first, day), 0
    while moment < at(last, day):
        found.ts.append(int(moment))
        found.close.append(price(step))
        moment += 10
        step += 1
    return found


# Trading hours of 9 to 22 in Paris: 07:00 to 20:00 UTC in October.
BOUNDS = session(DAY, CLOCK, 9, 22)


def test_the_trading_hours_and_the_us_open_are_moments_of_their_own_cities():
    assert BOUNDS == (at("07:00:00"), at("20:00:00"))
    # 9:30 in New York: 13:30 UTC in October, 13:30 UTC too in the weeks New York has changed its clocks and Paris not.
    assert us_open(DAY) == at("13:30:00")
    assert us_open(date(2026, 3, 16)) == at("13:30:00", date(2026, 3, 16))
    assert us_open(date(2026, 1, 14)) == at("14:30:00", date(2026, 1, 14))
    assert session(date(2026, 10, 14), CLOCK, 9, 24)[1] == CLOCK.day_bounds(date(2026, 10, 14))[1]


def test_a_steady_rise_is_a_trend_with_a_gap_from_the_day_before():
    rising = series(lambda step: 25_000 + step * 0.05)
    found = day_features(rising, 24_900.0, DAY, BOUNDS, [MacroEvent(DAY, "08:30", "CPI", "2026-09")])

    assert found["weekday"] == "Wednesday" and found["news"] == ["CPI"]
    assert found["day_type"] == "Trend up" and found["efficiency"] == 1.0
    assert found["gap_percent"] == pytest.approx(100 / 24_900 * 100, abs=1e-3)
    last = 25_000 + (len(rising.ts) - 1) * 0.05
    assert found["range_percent"] == pytest.approx((last - 25_000) / 25_000 * 100, abs=1e-3)
    assert found["volatility_percent"] > 0
    # From the bar that ended at 13:30 (the 2,340th) to the one that ended at 14:00: 180 bars of 0.05.
    at_open = 25_000 + 2_339 * 0.05
    assert found["us_open_move_percent"] == pytest.approx(180 * 0.05 / at_open * 100, abs=1e-3)
    assert session_close(rising, BOUNDS) == last


def test_a_day_that_goes_back_and_forth_and_one_without_enough_bars():
    swinging = series(lambda step: 25_000 + (50 if (step // 360) % 2 else -50) + step * 0.0001)
    assert day_features(swinging, None, DAY, BOUNDS)["day_type"] == "Back and forth"
    assert day_features(swinging, None, DAY, BOUNDS)["gap_percent"] is None

    short = series(lambda step: 25_000.0, first="07:00:00", last="07:02:00")
    found = day_features(short, 24_000.0, DAY, BOUNDS)
    assert found["bars"] == 12 and found["day_type"] is None and found["gap_percent"] is None
    assert day_features(None, None, DAY, BOUNDS)["bars"] == 0


def test_days_are_put_in_thirds_of_volatility_and_groups_of_gap_open_and_news():
    days = [
        {"volatility_percent": volatility, "gap_percent": gap, "us_open_move_percent": move, "day_type": "Mixed", "news": news}
        for volatility, gap, move, news in [
            (0.5, -0.5, -0.3, ["FOMC", "CPI"]), (0.9, 0.1, 0.0, []), (1.4, 0.6, 0.5, ["PCE"]),
            (0.7, None, None, []), (1.1, 0.0, 0.1, []), (2.0, 1.0, 0.0, ["NFP"]),
        ]
    ]
    label_days(days)

    assert [day["labels"]["volatility"] for day in days] == ["Calm", "Ordinary", "Agitated", "Calm", "Ordinary", "Agitated"]
    assert [day["labels"]["gap"] for day in days] == ["Gap down", "No gap", "Gap up", None, "No gap", "Gap up"]
    assert [day["labels"]["us_open"] for day in days] == ["Open fell", "Open flat", "Open rose", None, "Open flat", "Open flat"]
    assert [day["labels"]["news"] for day in days] == ["FOMC", "No news", "PCE", "No news", "No news", "NFP"]
    few = [{"volatility_percent": 1.0, "news": []}]
    label_days(few)
    assert few[0]["labels"]["volatility"] is None


def test_a_moment_is_placed_against_the_us_open():
    assert minutes_after_open(at("13:30:00"), DAY) == 0
    assert minutes_after_open(at("13:29:30"), DAY) == -1
    assert [session_part(minutes) for minutes in (-120, -30, 0, 59, 60, 239, 240, 389, 390)] == [
        "Before the US open", "Hour before the US open", "US open, first hour", "US open, first hour", "US midday",
        "US midday", "US afternoon", "US afternoon", "After the US close",
    ]


def test_the_macro_calendar_is_read_with_what_it_knows_until():
    calendar = read_calendar()

    assert [event.event for event in calendar.on(date(2026, 10, 14))] == ["CPI"]
    assert [event.event for event in calendar.on(date(2024, 11, 7))] == ["FOMC"]
    assert calendar.on(date(2026, 10, 15)) == []
    assert calendar.known_until() == date(2026, 12, 23)
    assert len(calendar.digest) == 16


def test_the_macro_calendar_has_each_release_once_on_a_weekday_at_its_hour():
    calendar = read_calendar()
    events = [event for day in calendar.by_day.values() for event in day]

    assert all(event.day.weekday() < 5 for event in events)
    assert len({(event.day, event.event) for event in events}) == len(events)
    assert {event.time_new_york for event in events if event.event == "FOMC"} == {"14:00"}
    assert {event.time_new_york for event in events if event.event in ("CPI", "NFP")} == {"08:30"}
    # Eight FOMC statements a year; a CPI and a jobs report a month, but for the month the 2025 shutdown cancelled.
    per_year = Counter((event.day.year, event.event) for event in events)
    assert [per_year[(year, "FOMC")] for year in (2024, 2025, 2026, 2027)] == [8, 8, 8, 8]
    assert per_year[(2024, "CPI")] == 12 and per_year[(2025, "CPI")] == 11 and per_year[(2025, "NFP")] == 11
    # The October 2025 figures were never published.
    assert not any(event.covers == "2025-10" for event in events if event.event in ("CPI", "NFP"))
