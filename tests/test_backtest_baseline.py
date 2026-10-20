import sys
import types
from dataclasses import replace
from datetime import date

asyncpg_stub = types.ModuleType("asyncpg")
asyncpg_stub.Pool = type("Pool", (), {})
asyncpg_stub.Record = dict
sys.modules.setdefault("asyncpg", asyncpg_stub)
sys.modules.setdefault("aio_pika", types.ModuleType("aio_pika"))

from src.backtest.baseline import Baseline, entry_hours, entry_moments, random_entries  # noqa: E402
from src.backtest.clock import TradingClock  # noqa: E402
from src.backtest.data import DayData  # noqa: E402
from src.backtest.simulator import SignalEvent  # noqa: E402
from tests.test_backtest_simulator import PLAIN, day_of, flat, series, signal  # noqa: E402

CLOCK = TradingClock("Europe/Paris")


def test_random_entries_fall_in_the_strategys_hours_where_the_recorder_listened():
    day = day_of(series(2, "long", 24_800, flat(5.0, 840)))
    hours = entry_hours([signal(1, "long", "09:30"), signal(2, "short", "15:10"), signal(3, "close-position", "21:00")], CLOCK)

    moments = entry_moments(day, hours, CLOCK)

    assert hours == (570, 910)
    assert CLOCK.minute_of_day(moments[0]) == 570 and CLOCK.minute_of_day(moments[-1]) == 910
    assert all(day.is_covered(moment) for moment in moments)


def test_on_the_day_the_clocks_change_the_hours_are_still_local():
    # Sunday 25 October 2026: 3 in the morning is 2 again in Paris.
    start, end = CLOCK.day_bounds(date(2026, 10, 25))
    day = DayData(day=date(2026, 10, 25), start=start, end=end, indice="us100", covered=set(range(start, end, 10)))

    moments = entry_moments(day, (600, 601), CLOCK)

    assert [CLOCK.local(moment).strftime("%H:%M:%S") for moment in (moments[0], moments[-1])] == ["10:00:00", "10:01:50"]


def test_the_draws_are_the_same_for_the_same_seed_repetition_and_day():
    day = day_of(series(2, "long", 24_800, flat(5.0, 840)))
    moments = entry_moments(day, (540, 1200), CLOCK)

    first = random_entries(day, moments, 3, 7, 0, first_id=1)
    again = random_entries(day, moments, 3, 7, 0, first_id=1)
    other = random_entries(day, moments, 3, 7, 1, first_id=1)

    assert [(event.arrived_at, event.action) for event in first] == [(event.arrived_at, event.action) for event in again]
    assert [event.arrived_at for event in first] != [event.arrived_at for event in other]
    assert all(isinstance(event, SignalEvent) and event.source == "random" for event in first)


def test_each_repetition_trades_the_day_under_the_same_rules():
    longs = series(2, "long", 24_800, flat(5.0, 840))
    shorts = series(3, "short", 25_200, flat(6.0, 840))
    day = day_of(longs, shorts)
    baseline = Baseline(replace(PLAIN), CLOCK, 4, 1, (540, 1200))

    baseline.run_day(day, 2, [])

    assert len(baseline.simulators) == 4 and baseline.entries == 2
    assert all(len(simulator.signals) == 2 for simulator in baseline.simulators)
    assert len(baseline.totals({day.day.isoformat()})) == 4
