import json
from dataclasses import replace
from datetime import date

import pytest

from src.strategy.bars import IndexBar
from src.strategy.engine import FLAT, LONG, SHORT, Decision, LocalClock
from src.strategy.execution import (
    DAILY_TARGET,
    END_OF_DATA,
    END_OF_DAY,
    KNOCKED_OUT,
    REFUSED_CLOSED_DATE,
    REFUSED_COOLDOWN,
    REFUSED_DAY_LOSS,
    REFUSED_DAY_PROFIT,
    REFUSED_DUPLICATE,
    REFUSED_HOURS,
    REFUSED_RISKY_HOURS,
    REFUSED_TRADE_LIMIT,
    STOPLOSS,
    TAKEPROFIT,
    TRAILING_STOP,
    Simulator,
    TurboModel,
    WataRules,
    compounded,
)
from src.strategy.report import format_summaries, summarize, worst_drawdown, write_days, write_trades

CLOCK = LocalClock("Europe/Paris")
DAY = date(2026, 10, 12)
NEXT_DAY = date(2026, 10, 13)

with open("etc/config_example.json", encoding="utf-8") as config_file:
    EXAMPLE_CONFIG = json.load(config_file)
# Stop -15 %, daily target 1.7 %, no entry above 1.25 %, trailing 5 / 8, ten minutes after a loss, 8 trades.
RULES = WataRules.from_config(EXAMPLE_CONFIG)
# The monitor's other exits are easier to see without the daily target in the way.
NO_DAILY_TARGET = replace(RULES, day_profit_target=1000.0, no_entry_above_day_profit=1000.0)

# Round numbers: a turbo at 4.00 that moves by 1.00 for 100 points, without spread.
# Bought with the index at 30,000 its knock-out level is 29,600, and 4 points are 1 %.
TURBO = TurboModel(entry_price=4.0, points_per_unit=100.0, spread=0.0)
INDEX = 30000.0


def at(clock_time: str, day: date = DAY) -> int:
    return CLOCK.epoch(day, clock_time)


def bar(start: int, opened: float, high: float | None = None, low: float | None = None, closed: float | None = None) -> IndexBar:
    closed = opened if closed is None else closed
    high = max(opened, closed) if high is None else high
    low = min(opened, closed) if low is None else low
    return IndexBar(start, opened, high, low, closed, opened, high, low, closed, 1)


def simulator(rules: WataRules = RULES, turbo: TurboModel = TURBO) -> Simulator:
    return Simulator(rules, turbo, CLOCK)


def entered(sim: Simulator, direction: str = LONG, clock_time: str = "09:00", index: float = INDEX) -> int:
    """Decide ``direction`` and let the next bar open at ``index``; the start of that bar."""
    start = at(clock_time)
    sim.on_bar(bar(start - 10, index))
    sim.decide(Decision(start, direction, "cross", 1.2))
    sim.on_bar(bar(start, index))
    return start


# ── the rules ──


def test_the_rules_are_those_of_the_configuration():
    assert RULES.timezone == "Europe/Paris"
    assert (RULES.trading_start_hour, RULES.trading_end_hour) == (8, 22)
    assert (RULES.risky_start_hour, RULES.risky_start_minute) == (21, 54)
    assert RULES.close_position_time == "21:55"
    assert "25/12/2025" in RULES.closed_dates
    assert (RULES.day_profit_target, RULES.no_entry_above_day_profit, RULES.max_day_loss) == (1.7, 1.25, -25.0)
    assert (RULES.cooldown_after_loss_minutes, RULES.max_trades_per_day) == (10, 8)
    assert (RULES.stoploss_percent, RULES.max_profit_percent) == (-15, 60)
    assert (RULES.trailing_enabled, RULES.trailing_activation_percent, RULES.trailing_drawdown_percent) == (True, 5, 8)


def test_a_configuration_without_a_rule_the_trader_needs_is_refused():
    config = {"trade": {"rules": [rule for rule in EXAMPLE_CONFIG["trade"]["rules"] if rule["rule_type"] != "day_trading"]}}

    with pytest.raises(ValueError, match="'day_trading' not found"):
        WataRules.from_config(config)


def test_the_optional_rules_default_to_what_the_services_assume():
    config = {"trade": {"rules": [rule for rule in EXAMPLE_CONFIG["trade"]["rules"] if rule["rule_type"] != "risk_management"]}}

    rules = WataRules.from_config(config)

    assert (rules.cooldown_after_loss_minutes, rules.max_trades_per_day) == (0, 0)
    assert (rules.stoploss_percent, rules.max_profit_percent, rules.trailing_enabled) == (-15, 60, False)


# ── the turbo ──


def test_a_turbo_is_worth_its_distance_to_the_knock_out_level():
    barrier = TURBO.barrier(LONG, INDEX)

    assert barrier == 29600.0
    assert TURBO.performance(LONG, barrier, INDEX) == 0.0
    assert TURBO.performance(LONG, barrier, INDEX + 4) == 1.0
    assert TURBO.performance(LONG, barrier, INDEX - 60) == -15.0
    assert TURBO.performance(LONG, barrier, 29600.0) == -100.0
    assert TURBO.performance(LONG, barrier, 29000.0) == -100.0


def test_a_short_turbo_mirrors_a_long_one():
    barrier = TURBO.barrier(SHORT, INDEX)

    assert barrier == 30400.0
    assert TURBO.performance(SHORT, barrier, INDEX - 4) == 1.0
    assert TURBO.performance(SHORT, barrier, INDEX + 60) == -15.0
    assert TURBO.performance(SHORT, barrier, 30400.0) == -100.0


def test_a_turbo_bought_at_the_ask_starts_below_its_price_by_its_spread():
    turbo = TurboModel(entry_price=4.0, points_per_unit=100.0, spread=0.04)
    barrier = turbo.barrier(LONG, INDEX)

    # Paid 4.00, worth 3.96 at the bid the moment after.
    assert turbo.performance(LONG, barrier, INDEX) == -1.0
    assert turbo.performance(LONG, barrier, INDEX + 4) == 0.0


@pytest.mark.parametrize("direction", [LONG, SHORT])
def test_the_index_of_a_performance_is_where_the_turbo_shows_it(direction):
    turbo = TurboModel(entry_price=4.4, points_per_unit=110.0, spread=0.03)
    barrier = turbo.barrier(direction, INDEX)

    for performance in (-15.0, 1.7, 60.0):
        assert turbo.performance(direction, barrier, turbo.index_at(direction, barrier, performance)) == performance


def test_a_turbo_that_makes_no_sense_is_refused():
    with pytest.raises(ValueError):
        TurboModel(points_per_unit=0)
    with pytest.raises(ValueError):
        TurboModel(entry_price=0.02, spread=0.03)


def test_results_in_percent_are_applied_one_on_the_other():
    assert compounded([]) == 0.0
    assert compounded([10.0, -10.0]) == -1.0
    assert compounded([1.7]) == 1.7


# ── entries ──


def test_a_decision_is_acted_on_at_the_open_of_the_next_bar():
    sim = simulator(NO_DAILY_TARGET)
    start = at("09:00")
    sim.on_bar(bar(start - 10, 29990.0))
    sim.decide(Decision(start, LONG, "catch-up", 1.4))

    sim.on_bar(bar(start, 30000.0, closed=30002.0))
    sim.finish()

    trade = sim.trades[0]
    assert (trade.direction, trade.entry_reason, trade.confidence) == (LONG, "catch-up", 1.4)
    assert (trade.entry_at, trade.entry_index) == (start, 30000.0)
    assert (trade.exit_reason, trade.exit_index, trade.performance, trade.points) == (END_OF_DATA, 30002.0, 0.5, 2.0)


@pytest.mark.parametrize("day, clock_time, reason", [
    (DAY, "07:59", REFUSED_HOURS),
    (DAY, "22:00", REFUSED_HOURS),
    (DAY, "21:54", REFUSED_RISKY_HOURS),
    (date(2025, 12, 25), "10:00", REFUSED_CLOSED_DATE),
])
def test_an_entry_the_trader_would_refuse_for_the_hour_or_the_day_is_refused(day, clock_time, reason):
    sim = simulator()
    start = at(clock_time, day)
    sim.on_bar(bar(start - 10, INDEX))
    sim.decide(Decision(start, LONG, "cross"))

    sim.on_bar(bar(start, INDEX))
    sim.finish()

    assert sim.trades == []
    assert [(refusal.at, refusal.direction, refusal.reason) for refusal in sim.refusals] == [(start, LONG, reason)]


def test_the_last_minute_before_the_risky_hours_is_still_traded():
    sim = simulator()

    entered(sim, clock_time="21:53")

    assert sim.refusals == []


def test_the_same_direction_twice_is_one_position():
    sim = simulator(NO_DAILY_TARGET)
    start = entered(sim)
    sim.decide(Decision(start + 10, LONG, "cross"))

    sim.on_bar(bar(start + 10, INDEX))

    assert [refusal.reason for refusal in sim.refusals] == [REFUSED_DUPLICATE]


def test_no_entry_once_the_day_is_won_or_lost_enough():
    won = simulator()
    start = entered(won)
    won.on_bar(bar(start + 10, INDEX, high=INDEX + 20))     # the daily target sells at +1.7 %
    won.decide(Decision(start + 20, LONG, "cross"))
    won.on_bar(bar(start + 20, INDEX))
    assert [refusal.reason for refusal in won.refusals] == [REFUSED_DAY_PROFIT]

    lost = simulator(replace(RULES, max_day_loss=-10.0, cooldown_after_loss_minutes=0))
    start = entered(lost)
    lost.on_bar(bar(start + 10, INDEX, low=INDEX - 60))     # the stop sells at -15 %
    lost.decide(Decision(start + 20, LONG, "cross"))
    lost.on_bar(bar(start + 20, INDEX))
    assert [refusal.reason for refusal in lost.refusals] == [REFUSED_DAY_LOSS]


def test_after_a_loss_entries_wait_for_the_cooldown():
    sim = simulator()
    start = entered(sim)
    sim.on_bar(bar(start + 10, INDEX, low=INDEX - 60))      # stopped at 09:00:10
    lost_at = start + 10

    sim.decide(Decision(lost_at + 590, SHORT, "cross"))
    sim.on_bar(bar(lost_at + 590, INDEX - 60))              # 9 minutes 50 after the loss
    sim.decide(Decision(lost_at + 600, SHORT, "cross"))
    sim.on_bar(bar(lost_at + 600, INDEX - 60))              # ten minutes after it
    sim.finish()

    assert [refusal.reason for refusal in sim.refusals] == [REFUSED_COOLDOWN]
    assert [trade.direction for trade in sim.trades] == [LONG, SHORT]


def test_no_more_entries_than_the_limit_of_the_day():
    sim = simulator(replace(NO_DAILY_TARGET, max_trades_per_day=2))
    start = at("09:00")
    sim.on_bar(bar(start, INDEX))
    for number in range(3):
        sim.decide(Decision(start, LONG, "cross"))
        sim.on_bar(bar(start + 20 * number + 10, INDEX))
        sim.decide(Decision(start, FLAT, "reverse cross"))
        sim.on_bar(bar(start + 20 * number + 20, INDEX))

    assert len(sim.trades) == 2
    assert [refusal.reason for refusal in sim.refusals] == [REFUSED_TRADE_LIMIT]


# ── the position monitor ──


def test_the_daily_target_reached_inside_a_bar_is_sold_at_the_target_not_at_the_high():
    sim = simulator()
    start = entered(sim)

    sim.on_bar(bar(start + 10, INDEX, high=INDEX + 20))     # +5 % at the high

    trade = sim.trades[0]
    assert (trade.exit_reason, trade.performance) == (DAILY_TARGET, 1.7)
    assert trade.exit_index == pytest.approx(INDEX + 6.8)
    assert trade.exit_at == start + 10


def test_a_bar_that_opens_beyond_the_target_is_sold_at_what_the_open_gives():
    sim = simulator()
    start = entered(sim)

    sim.on_bar(bar(start + 10, INDEX + 12))                 # +3 % from its first price

    assert (sim.trades[0].exit_reason, sim.trades[0].performance) == (DAILY_TARGET, 3.0)


def test_a_stop_is_sold_at_the_worst_price_of_the_ten_seconds_that_crossed_it():
    sim = simulator()
    start = entered(sim)

    sim.on_bar(bar(start + 10, INDEX, low=INDEX - 80, closed=INDEX - 10))

    assert (sim.trades[0].exit_reason, sim.trades[0].performance, sim.trades[0].exit_index) == (STOPLOSS, -20.0, INDEX - 80)


def test_inside_a_bar_that_rose_the_low_comes_before_the_high():
    rose, fell = simulator(), simulator()
    for sim in (rose, fell):
        start = entered(sim)

    rose.on_bar(bar(start + 10, INDEX, high=INDEX + 20, low=INDEX - 80, closed=INDEX + 5))
    fell.on_bar(bar(start + 10, INDEX, high=INDEX + 20, low=INDEX - 80, closed=INDEX - 5))

    assert rose.trades[0].exit_reason == STOPLOSS
    assert fell.trades[0].exit_reason == DAILY_TARGET


def test_the_take_profit_sells_at_its_level():
    sim = simulator(replace(NO_DAILY_TARGET, trailing_enabled=False))
    start = entered(sim)

    sim.on_bar(bar(start + 10, INDEX, high=INDEX + 300))

    assert (sim.trades[0].exit_reason, sim.trades[0].performance) == (TAKEPROFIT, 60.0)


def test_the_trailing_stop_sells_when_the_gain_falls_back_far_enough_from_its_peak():
    sim = simulator(NO_DAILY_TARGET)
    start = entered(sim)
    sim.on_bar(bar(start + 10, INDEX, closed=INDEX + 40))   # +10 %: the trailing stop is armed
    sim.on_bar(bar(start + 20, INDEX + 40, closed=INDEX + 12))  # back to +3 %: 7 from the peak
    assert sim.trades == []

    sim.on_bar(bar(start + 30, INDEX + 12, closed=INDEX + 8))   # +2 %: 8 from the peak

    trade = sim.trades[0]
    assert (trade.exit_reason, trade.performance, trade.max_performance) == (TRAILING_STOP, 2.0, 10.0)


def test_the_trailing_stop_does_nothing_before_the_gain_that_arms_it():
    sim = simulator(NO_DAILY_TARGET)
    start = entered(sim)
    sim.on_bar(bar(start + 10, INDEX, closed=INDEX + 16))   # +4 %: not armed
    sim.on_bar(bar(start + 20, INDEX + 16, closed=INDEX - 16))  # -4 %: 8 from the peak, but never armed

    assert sim.trades == []


def test_an_index_through_the_knock_out_level_loses_everything():
    sim = simulator()
    start = entered(sim)

    sim.on_bar(bar(start + 10, INDEX - 500))

    assert (sim.trades[0].exit_reason, sim.trades[0].performance) == (KNOCKED_OUT, -100.0)


def test_a_short_position_is_watched_the_other_way_round():
    sim = simulator()
    start = entered(sim, SHORT)

    sim.on_bar(bar(start + 10, INDEX, low=INDEX - 20))

    trade = sim.trades[0]
    assert (trade.exit_reason, trade.performance) == (DAILY_TARGET, 1.7)
    assert trade.points == pytest.approx(6.8)


def test_after_a_loss_the_day_needs_more_than_the_target_from_the_next_trade():
    sim = simulator(replace(RULES, cooldown_after_loss_minutes=0))
    start = entered(sim)
    sim.decide(Decision(start + 10, FLAT, "reverse cross"))
    sim.on_bar(bar(start + 10, INDEX - 20))                 # sold at -5 %
    sim.decide(Decision(start + 20, LONG, "cross"))
    sim.on_bar(bar(start + 20, INDEX - 20))

    sim.on_bar(bar(start + 30, INDEX - 20, high=INDEX + 40))

    # 0.95 x 1.0705 = 1.017: the day is at its target with +7.05 % on the second trade.
    assert [trade.exit_reason for trade in sim.trades] == ["Signal: reverse cross", DAILY_TARGET]
    assert sim.trades[1].performance == pytest.approx(7.05, abs=0.01)
    assert sim.days[0].percent == pytest.approx(1.7, abs=0.01)


# ── exits the strategy or the clock asks for ──


def test_going_flat_sells_at_the_open_of_the_next_bar():
    sim = simulator(NO_DAILY_TARGET)
    start = entered(sim)
    sim.decide(Decision(start + 10, FLAT, "reverse cross"))

    sim.on_bar(bar(start + 10, INDEX + 8, closed=INDEX + 30))

    assert (sim.trades[0].exit_reason, sim.trades[0].performance, sim.trades[0].exit_at) == ("Signal: reverse cross", 2.0, start + 10)


def test_a_turn_sells_one_side_and_buys_the_other_at_the_same_open():
    sim = simulator(NO_DAILY_TARGET)
    start = entered(sim)
    sim.decide(Decision(start + 10, SHORT, "cross"))

    sim.on_bar(bar(start + 10, INDEX + 8))
    sim.finish()

    assert [(trade.direction, trade.exit_reason, trade.entry_index) for trade in sim.trades] == [
        (LONG, "Signal: reversal", INDEX), (SHORT, END_OF_DATA, INDEX + 8),
    ]


def test_a_turn_still_sells_when_the_new_entry_is_refused():
    sim = simulator(replace(RULES, day_profit_target=1000.0))
    start = entered(sim)
    sim.decide(Decision(start + 10, SHORT, "cross"))

    sim.on_bar(bar(start + 10, INDEX + 8))                  # the long is sold at +2 %: the day is above 1.25 %
    sim.finish()

    assert [(trade.direction, trade.performance) for trade in sim.trades] == [(LONG, 2.0)]
    assert [refusal.reason for refusal in sim.refusals] == [REFUSED_DAY_PROFIT]


def test_the_end_of_the_day_sells_at_the_closing_time():
    sim = simulator(NO_DAILY_TARGET)
    entered(sim, clock_time="21:00")
    sim.on_bar(bar(at("21:54") + 50, INDEX + 4))
    assert sim.trades == []

    sim.on_bar(bar(at("21:55"), INDEX + 8))

    assert (sim.trades[0].exit_reason, sim.trades[0].performance, sim.trades[0].exit_at) == (END_OF_DAY, 2.0, at("21:55"))


def test_without_a_bar_at_the_closing_time_the_position_is_sold_at_the_first_price_of_the_next_day():
    sim = simulator(NO_DAILY_TARGET)
    entered(sim, clock_time="15:00")

    sim.on_bar(bar(at("08:00", NEXT_DAY), INDEX - 8))
    sim.finish()

    assert (sim.trades[0].exit_reason, sim.trades[0].performance) == (END_OF_DAY, -2.0)
    # Counted in the day it was opened, and the new day starts from nothing.
    assert [(day.day, day.percent, day.entries) for day in sim.days] == [(DAY, -2.0, 1), (NEXT_DAY, 0.0, 0)]


# ── the report ──


def traded() -> Simulator:
    """Two days: a target reached, then a stop."""
    sim = simulator(replace(RULES, cooldown_after_loss_minutes=0))
    start = entered(sim)
    sim.on_bar(bar(start + 10, INDEX, high=INDEX + 20))
    sim.decide(Decision(start + 20, SHORT, "cross"))
    sim.on_bar(bar(start + 20, INDEX))                      # refused: the day is won

    start = at("09:00", NEXT_DAY)
    sim.on_bar(bar(start - 10, INDEX))
    sim.decide(Decision(start, SHORT, "catch-up"))
    sim.on_bar(bar(start, INDEX))
    sim.on_bar(bar(start + 10, INDEX, high=INDEX + 60))
    sim.finish()
    return sim


def test_the_summary_counts_days_trades_and_why_they_ended():
    summary = summarize(traded())

    assert (summary["days"], summary["days_with_an_entry"], summary["trades"]) == (2, 2, 2)
    assert (summary["win_rate"], summary["average_win"], summary["average_loss"]) == (50.0, 1.7, -15.0)
    assert (summary["best_day"], summary["worst_day"]) == (1.7, -15.0)
    # 1.017 x 0.85 = 0.86445
    assert summary["total_percent"] == pytest.approx(-13.555, abs=0.006)
    assert summary["worst_drawdown"] == -15.0
    assert summary["index_points"] == pytest.approx(6.8 - 60)
    assert summary["exits"] == {DAILY_TARGET: 1, STOPLOSS: 1}
    assert summary["entries"] == {"cross": 1, "catch-up": 1}
    assert summary["refusals"] == {REFUSED_DAY_PROFIT: 1}


def test_a_backtest_without_a_trade_still_has_a_summary():
    sim = simulator()
    sim.on_bar(bar(at("09:00"), INDEX))
    sim.finish()

    summary = summarize(sim)

    assert (summary["days"], summary["trades"], summary["win_rate"], summary["total_percent"]) == (1, 0, None, 0.0)
    assert "Winning trades %" in format_summaries([("110 pts", summary)])


def test_a_day_without_a_price_inside_the_market_hours_is_not_counted_as_a_day():
    sim = simulator()
    sim.on_bar(bar(at("23:00", date(2026, 10, 11)), INDEX))     # a Sunday evening of quotes
    sim.on_bar(bar(at("09:00"), INDEX))
    sim.on_bar(bar(at("01:00", NEXT_DAY), INDEX))               # the first hour of a day not fetched
    sim.finish()

    assert [(day.day, day.market_bars) for day in sim.days] == [(date(2026, 10, 11), 0), (DAY, 1), (NEXT_DAY, 0)]
    assert summarize(sim)["days"] == 1


def test_the_drawdown_is_the_deepest_fall_from_a_peak_of_the_days_put_end_to_end():
    sim = traded()

    assert worst_drawdown([]) == 0.0
    assert worst_drawdown(sim.days[:1]) == 0.0
    assert worst_drawdown(sim.days) == -15.0


def test_the_runs_are_shown_side_by_side():
    text = format_summaries([("80 pts", summarize(traded())), ("110 pts", summarize(simulator()))])
    lines = text.splitlines()

    assert lines[0].split() == ["80", "pts", "110", "pts"]
    assert [line for line in lines if line.startswith("Trades")][0].split() == ["Trades", "2", "0"]
    assert "Exits (80 pts): Daily profit target 1, Stoploss 1" in lines
    assert "Refused entries (110 pts): none" in lines


def test_trades_and_days_are_written_as_csv(tmp_path):
    sim = traded()
    trades_path, days_path = tmp_path / "out" / "trades.csv", tmp_path / "out" / "days.csv"

    write_trades(str(trades_path), sim.trades)
    write_days(str(days_path), sim.days)

    trades = trades_path.read_text().splitlines()
    assert trades[0].startswith("entry_time,exit_time,direction,entry_reason,confidence,entry_index,exit_index")
    assert trades[1] == "2026-10-12T07:00:00Z,2026-10-12T07:00:10Z,long,cross,1.2,30000.0,30006.8,6.8,1.7,1.7,Daily profit target"
    assert trades[2].endswith(",short,catch-up,,30000.0,30060.0,-60.0,-15.0,0.0,Stoploss")
    assert days_path.read_text().splitlines() == ["day,entries,closed,day_percent", "2026-10-12,1,1,1.7", "2026-10-13,1,1,-15.0"]
