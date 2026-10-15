from dataclasses import replace
from datetime import date

import pytest

from src.strategy import engine
from src.strategy.bars import IndexBar
from src.strategy.engine import DAY_ENDED, FLAT, LONG, SHORT, Decision, LocalClock, seconds_of
from src.strategy.kernel_vwap import CATCH_UP, CROSS, SESSION_END, KernelVwap, KernelVwapParams

CLOCK = LocalClock("Europe/Paris")
# Monday 12 October 2026: Paris is two hours ahead of UTC.
DAY = date(2026, 10, 12)
NEXT_DAY = date(2026, 10, 13)

# Small numbers, so that a scenario is a few minutes of prices: range bars of
# 2 points, a kernel over 3 of them, a session that ends at nine.
PARAMS = KernelVwapParams(range_points=2.0, kernel_bandwidth=3, min_band_width=0, session_end="09:00")


def at(clock_time: str, day: date = DAY) -> int:
    return CLOCK.epoch(day, clock_time)


def ramp(first: float, last: float) -> list[float]:
    """Every whole price from one to the other."""
    step = 1 if last >= first else -1
    return [first + step * index for index in range(int(abs(last - first)) + 1)]


def walk(start: int, prices: list[float], updates: int = 5) -> list[IndexBar]:
    """Ten-second bars going from each price to the next, the first one at ``start``."""
    bars = []
    for index, (opened, closed) in enumerate(zip(prices, prices[1:])):
        high, low = max(opened, closed), min(opened, closed)
        bars.append(IndexBar(start + 10 * index, opened, high, low, closed, opened, high, low, closed, updates))
    return bars


def before_the_session(day: date = DAY) -> list[IndexBar]:
    """Enough range bars before eight for the kernel to have a value, ending at 100."""
    return walk(at("07:50", day), ramp(100, 104) + ramp(104, 100) + ramp(100, 104) + ramp(104, 100))


def decisions(params: KernelVwapParams, bars: list[IndexBar]) -> list[tuple]:
    """Each change of target as (target, reason, minute of the day it was known)."""
    made = engine.run(KernelVwap(params, CLOCK), bars, CLOCK)
    return [(decision.target, decision.reason, CLOCK.at(decision.at)[1] // 60) for decision in made]


def minute(clock_time: str) -> int:
    return seconds_of(clock_time) // 60


# ── entries and exits ──


def test_the_kernel_crossing_the_session_average_is_an_entry_and_the_reverse_cross_turns_the_position():
    # Down from 100 to 90, up to 112 through the average, then down through it again.
    session = walk(at("08:00"), ramp(100, 90) + ramp(90, 112)[1:] + ramp(112, 84)[1:] + [84] * 5)

    made = decisions(PARAMS, before_the_session() + session)

    assert made == [
        (SHORT, CROSS, minute("08:00")),
        (LONG, CROSS, minute("08:02")),
        (SHORT, CROSS, minute("08:07")),
    ]


def test_an_entry_carries_the_pine_scripts_confidence_between_zero_and_two():
    session = walk(at("08:00"), ramp(100, 90) + ramp(90, 112)[1:])

    made = engine.run(KernelVwap(PARAMS, CLOCK), before_the_session() + session, CLOCK)

    assert [decision.target for decision in made] == [SHORT, LONG]
    assert all(0 < decision.confidence <= 2 for decision in made)
    # Known at the end of a ten-second bucket, never inside one.
    assert all(decision.at % 10 == 0 for decision in made)


def test_the_end_of_the_session_ends_the_trade_on_the_clock():
    session = walk(at("08:00"), ramp(100, 90) + ramp(90, 112)[1:])
    # Nothing moves any more: no range bar ends after nine, the clock alone closes.
    evening = walk(at("08:59"), [112] * 8)

    made = decisions(PARAMS, before_the_session() + session + evening)

    assert made[-1] == (FLAT, SESSION_END, minute("09:00"))


def test_nothing_is_entered_outside_the_session():
    # The same prices, an hour before the session opens: the average does not exist yet.
    early = walk(at("06:50"), ramp(100, 104) + ramp(104, 100) + ramp(100, 90) + ramp(90, 112)[1:] + ramp(112, 84)[1:])

    assert decisions(PARAMS, early) == []


def test_entries_wait_until_the_prices_of_the_session_have_spread_enough_once():
    session = walk(at("08:00"), ramp(100, 90) + ramp(90, 112)[1:] + ramp(112, 84)[1:] + [84] * 5)
    waiting = replace(PARAMS, min_band_width=6)

    made = decisions(waiting, before_the_session() + session)

    # The two crosses of the first minutes are let go; the band reaches 6 points at 08:05.
    assert made == [(SHORT, CROSS, minute("08:07"))]


def test_no_entry_while_the_market_is_racing():
    # Down to 60, then back to 100 inside ten seconds: twenty range bars at once,
    # and the kernel passes the average in the middle of them.
    session = walk(at("08:00"), ramp(100, 60) + [60, 100, 100, 100])
    calm = replace(PARAMS, fast_bars=50)

    assert LONG not in [made[0] for made in decisions(PARAMS, before_the_session() + session)]
    assert (LONG, CROSS, minute("08:07")) in decisions(calm, before_the_session() + session)


def test_no_entry_on_a_bar_that_opened_on_a_gap():
    # Under the average until 08:04, then nothing for five minutes, then prices 40 points higher.
    session = walk(at("08:00"), ramp(100, 80) + [80] * 3) + walk(at("08:10"), [120, 121, 122, 123, 124, 125, 126])
    watching = replace(PARAMS, gap_average_bars=3)
    careless = replace(PARAMS, gap_average_bars=3, gap_multiplier=1000.0)

    assert LONG not in [made[0] for made in decisions(watching, before_the_session() + session)]
    assert LONG in [made[0] for made in decisions(careless, before_the_session() + session)]


def test_after_an_exit_the_cooldown_lets_bars_pass_before_a_new_entry():
    session = walk(at("08:00"), ramp(100, 90) + ramp(90, 112)[1:] + ramp(112, 84)[1:] + [84] * 5)
    resting = replace(PARAMS, cooldown_bars=1000)

    made = decisions(resting, before_the_session() + session)

    # The turn at 08:02 is still allowed, as in the Pine script: the bars are
    # counted since the last exit, and that turn is the first one. The next
    # reverse cross then only exits.
    assert made == [
        (SHORT, CROSS, minute("08:00")),
        (LONG, CROSS, minute("08:02")),
        (FLAT, "reverse cross", minute("08:07")),
    ]


# ── the day of 12 October 2026: a fall that began before the session ──


def falling_since_before_the_session() -> list[IndexBar]:
    night = walk(at("07:50"), ramp(104, 100) + ramp(100, 104) + ramp(104, 96))
    session = walk(at("08:00"), ramp(96, 60) + [60] * 3)
    return night + session


def test_a_fall_that_began_before_the_session_leaves_no_cross_to_enter_on():
    waiting = replace(PARAMS, min_band_width=6)

    assert decisions(waiting, falling_since_before_the_session()) == []


def test_the_catch_up_enters_on_the_side_the_kernel_is_on_when_entries_become_allowed():
    catching_up = replace(PARAMS, min_band_width=6, entry_mode="cross_catchup")

    made = decisions(catching_up, falling_since_before_the_session())

    assert made == [(SHORT, CATCH_UP, minute("08:03"))]


def test_the_catch_up_does_not_chase_a_kernel_too_far_from_the_average():
    cautious = replace(PARAMS, min_band_width=6, entry_mode="cross_catchup", catchup_max_distance=1.0)

    assert decisions(cautious, falling_since_before_the_session()) == []


# ── days ──


def one_day(day: date) -> list[IndexBar]:
    session = walk(at("08:00", day), ramp(100, 90) + ramp(90, 112)[1:])
    return before_the_session(day) + session + walk(at("08:59", day), [112] * 8)


def test_each_day_starts_afresh_and_the_same_prices_give_the_same_decisions():
    made = engine.run(KernelVwap(PARAMS, CLOCK), one_day(DAY) + one_day(NEXT_DAY), CLOCK)

    first = [(decision.target, decision.reason, decision.at) for decision in made[:3]]
    second = [(decision.target, decision.reason, decision.at - 86400) for decision in made[3:]]
    assert len(made) == 6
    assert first == second


# ── parameters ──


def test_parameters_given_as_text_take_the_type_of_the_one_they_replace():
    params = KernelVwapParams().with_overrides({"entry_mode": "cross_catchup", "kernel_bandwidth": "8", "min_band_width": "12.5"})

    assert (params.entry_mode, params.kernel_bandwidth, params.min_band_width) == ("cross_catchup", 8, 12.5)
    assert params.range_points == 6.0


@pytest.mark.parametrize("overrides, message", [
    ({"entry_mod": "cross"}, "Unknown parameter 'entry_mod'"),
    ({"kernel_bandwidth": "eight"}, "needs a int"),
    ({"entry_mode": "always"}, "entry_mode must be one of"),
    ({"vwap_weight": "volume"}, "vwap_weight must be one of"),
    ({"range_points": "0.01"}, "one tick at least"),
    ({"range_points": "0.2"}, "range_points must be between 0.5 and 1000.0"),
    ({"kernel_bandwidth": "100000"}, "kernel_bandwidth must be between 2 and 500"),
    ({"kernel_style": "gaussian"}, "kernel_style must be one of wave"),
    ({"session_start": "8h"}, "session_start must be a time of day such as 08:00"),
    ({"session_start": "22:00"}, "session_end must be after session_start"),
])
def test_a_parameter_that_makes_no_sense_is_refused_saying_why(overrides, message):
    with pytest.raises(ValueError, match=message):
        KernelVwapParams().with_overrides(overrides)


# ── the engine ──


def quiet(start: int, count: int = 1) -> list[IndexBar]:
    return [IndexBar(start + 10 * index, 100, 100, 100, 100, 100, 100, 100, 100, 1) for index in range(count)]


class Scripted:
    """A strategy that decides what it is told to, at the bars it is told to."""

    def __init__(self, script: dict[int, list[Decision]], log: list):
        self._script = script
        self._log = log

    def start_day(self, day: date) -> None:
        self._log.append(("day", day))

    def on_bar(self, bar: IndexBar) -> list[Decision]:
        self._log.append(("strategy", bar.start))
        return self._script.get(bar.start, [])


def test_whoever_executes_sees_a_bar_before_the_strategy_and_its_decision_after():
    start = at("08:00")
    log: list = []
    strategy = Scripted({start: [Decision(start + 10, LONG, CROSS)]}, log)

    engine.run(
        strategy, quiet(start, 2), CLOCK,
        on_bar=lambda bar: log.append(("execution", bar.start)),
        on_decision=lambda decision: log.append(("decision", decision.target)),
    )

    assert log == [
        ("day", DAY),
        ("execution", start), ("strategy", start), ("decision", LONG),
        ("execution", start + 10), ("strategy", start + 10),
    ]


def test_several_decisions_in_the_same_ten_seconds_come_out_as_where_they_leave_the_strategy():
    start = at("08:00")
    script = {
        # In and out again: nothing changed.
        start: [Decision(start + 10, LONG, CROSS), Decision(start + 10, FLAT, "reverse cross")],
        # In, and turned: only the last one counts.
        start + 10: [Decision(start + 20, LONG, CROSS), Decision(start + 20, SHORT, CROSS)],
        # Already short.
        start + 20: [Decision(start + 30, SHORT, CATCH_UP)],
    }

    made = engine.run(Scripted(script, []), quiet(start, 3), CLOCK)

    assert [(decision.target, decision.at) for decision in made] == [(SHORT, start + 20)]


def test_a_position_the_strategy_never_closed_is_given_back_when_the_next_day_begins():
    start, next_start = at("08:00"), at("08:00", NEXT_DAY)
    log: list = []
    strategy = Scripted({start: [Decision(start + 10, LONG, CROSS)]}, log)

    made = engine.run(strategy, quiet(start) + quiet(next_start), CLOCK)

    assert [(decision.target, decision.reason, decision.at) for decision in made] == [
        (LONG, CROSS, start + 10), (FLAT, DAY_ENDED, next_start),
    ]
    assert [entry for entry in log if entry[0] == "day"] == [("day", DAY), ("day", NEXT_DAY)]


# ── the clock ──


def test_the_clock_gives_the_local_day_and_time_in_summer_and_in_winter():
    # 06:00 UTC on 12 October is eight in Paris; on 1 December it is seven.
    assert CLOCK.at(1791784800) == (DAY, 8 * 3600)
    assert CLOCK.at(CLOCK.epoch(date(2026, 12, 1), "08:00")) == (date(2026, 12, 1), 8 * 3600)
    assert CLOCK.epoch(date(2026, 12, 1), "08:00") % 86400 == 7 * 3600
    # 23:30 UTC is already the next day in Paris.
    assert CLOCK.at(1791784800 + 17 * 3600 + 1800) == (NEXT_DAY, 3600 + 1800)


def test_the_clock_follows_the_change_of_hour():
    # Sunday 25 October 2026, 01:00 UTC: three o'clock becomes two in Paris.
    change = 1792890000
    sunday = date(2026, 10, 25)

    assert CLOCK.at(change - 1) == (sunday, 3 * 3600 - 1)
    assert CLOCK.at(change) == (sunday, 2 * 3600)
    assert CLOCK.at(change + 3600) == (sunday, 3 * 3600)


def test_a_time_of_day_as_seconds():
    assert seconds_of("21:55") == 21 * 3600 + 55 * 60
    assert seconds_of("00:00") == 0
