"""
The edge of a strategy's entries (src/backtest/edge.py): how its asset moved
after each entry signal, against random moments of the same hours.
"""

import math
from array import array
from datetime import datetime, timezone
from statistics import fmean, stdev

import pytest

from src.backtest.clock import TradingClock
from src.backtest.data import AssetSeries
from src.backtest.edge import EdgeMeter, headline, move_at, moves_after
from src.backtest.simulator import SignalEvent

CLOCK = TradingClock("Europe/Paris")
DAY = "2026-10-14"


def at(clock_time: str) -> float:
    return datetime.fromisoformat(f"{DAY}T{clock_time}+00:00").timestamp()


def series(prices: dict[str, float], first: str = "07:00:00", last: str = "10:00:00") -> AssetSeries:
    """Ten-second bars from ``first`` to ``last`` (UTC), each price holding from its time on."""
    found = AssetSeries(ts=array("q"), close=array("d"))
    changes = sorted((at(moment), price) for moment, price in prices.items())
    moment, price = at(first), changes[0][1]
    while moment < at(last):
        for when, value in changes:
            if when <= moment:
                price = value
        found.ts.append(int(moment))
        found.close.append(price)
        moment += 10
    return found


def signal(number: int, clock_time: str, action: str = "long") -> SignalEvent:
    return SignalEvent(id=number, source="pine", action=action, arrived_at=at(clock_time), alert_at=at(clock_time), indice="us100")


# Flat at 25,000, down to 24,990 at 08:00:10, up to 25,050 from 08:00:30.
JUMP = series({"07:00:00": 25_000.0, "08:00:10": 24_990.0, "08:00:30": 25_050.0})


def test_a_move_is_read_from_the_last_price_known_and_signed_by_the_direction():
    # The bar of 08:00:50 ended at 08:01:00: 25,050 against 25,000 a minute before.
    assert move_at(JUMP, at("08:00:00"), "long", 1) == pytest.approx(20.0)
    assert move_at(JUMP, at("08:00:00"), "short", 1) == pytest.approx(-20.0)
    found = moves_after(JUMP, at("08:00:00"), "long")
    end, best, worst = found[5]
    assert (end, best) == (pytest.approx(20.0), pytest.approx(20.0))
    assert worst == pytest.approx(-4.0)
    # Nothing after the last bar, nothing before the first.
    assert 60 not in moves_after(JUMP, at("09:30:00"), "long")
    assert moves_after(JUMP, at("06:00:00"), "long") is None


def test_entries_that_catch_a_move_beat_random_moments_of_their_hour():
    meter = EdgeMeter(CLOCK)
    meter.add_day(DAY, JUMP, [
        signal(1, "08:00:00"),
        # Twenty seconds later: the same move again, not a second piece of evidence.
        signal(2, "08:00:20"),
        signal(3, "08:20:00", "short"),
        signal(4, "09:10:00"),
        # Not an entry, and one before the asset had a price.
        SignalEvent(id=5, source="schedule", action="close-position", arrived_at=at("08:30:00"), alert_at=at("08:30:00"), indice="us100"),
        signal(6, "06:00:00"),
    ])

    report = meter.report({1: "executed", 3: "refused"})

    one = next(row for row in report["horizons"] if row["minutes"] == 1)
    assert one["signals"] == 4 and report["unpriced"] == 1
    # +20 for the first, +24 for the second (bought in the dip), 0 for the others.
    assert one["mean_bp"] == pytest.approx(round((20.0 + 60 / 24_990 * 10_000) / 4, 2))
    assert one["hit_rate"] == 50.0
    assert one["independent"] == 3
    # The mean of all four over their spread, three of them counted as observations.
    moves = [20.0, 60 / 24_990 * 10_000, 0.0, 0.0]
    assert one["t"] == pytest.approx(round(fmean(moves) / (stdev(moves) / math.sqrt(3)), 2))
    assert one["random"]["draws"] == 20 and one["random"]["beaten"] == 100.0
    assert one["random"]["mean_bp"] < one["mean_bp"]
    assert [row["hour"] for row in report["by_hour"]] == [10, 11]
    assert [row["direction"] for row in report["by_direction"]] == ["long", "short"]
    assert {row["fate"]: row["1"]["signals"] for row in report["by_fate"]} == {"executed": 1, "not_taken": 3}
    first = report["signals"][0]
    assert (first["fate"], first["move_bp"]["1"], first["worst_bp"]["1"], first["at"]) == ("executed", 20.0, -4.0, "2026-10-14T08:00:00Z")


def test_the_random_moments_are_drawn_again_the_same():
    def measured():
        meter = EdgeMeter(CLOCK)
        meter.add_day(DAY, JUMP, [signal(1, "08:00:00"), signal(2, "08:40:00")])
        return meter.report()["horizons"]

    assert measured() == measured()


def test_too_few_independent_signals_give_no_t_statistic_and_the_headline_keeps_the_figures():
    meter = EdgeMeter(CLOCK)
    meter.add_day(DAY, JUMP, [signal(1, "08:00:00"), signal(2, "08:00:20")])
    meter.add_day("2026-10-15", None, [signal(3, "08:00:00")])

    report = meter.report()

    assert all(row["t"] is None for row in report["horizons"])
    assert report["unpriced"] == 1
    kept = headline(report)
    assert kept["horizons"][0] == {
        "minutes": 1, "signals": 2, "mean_bp": round((20.0 + 60 / 24_990 * 10_000) / 2, 2), "hit_rate": 100.0, "t": None, "independent": 1,
        "random_mean_bp": kept["horizons"][0]["random_mean_bp"], "beaten": 100.0,
    }


def test_steady_signals_on_a_steady_rise_have_a_t_statistic():
    rising = AssetSeries(ts=array("q"), close=array("d"))
    for step in range(3 * 360):
        rising.ts.append(int(at("07:00:00")) + step * 10)
        rising.close.append(25_000.0 + step * 0.1 + (0.3 if step % 2 else 0.0))
    meter = EdgeMeter(CLOCK)
    meter.add_day(DAY, rising, [signal(number, f"07:{10 + number * 2:02d}:05") for number in range(10)])

    one = meter.report()["horizons"][0]

    assert one["independent"] == 10 and one["t"] is not None and one["t"] > 2
    assert one["hit_rate"] == 100.0
