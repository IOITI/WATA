import copy
import json
import random
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
    Execution,
    REFUSED_CLOSED_DATE,
    REFUSED_CONFIDENCE,
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
from src.strategy.report import (
    FAIR,
    FEW,
    TOO_FEW,
    format_summaries,
    longest_losing_streak,
    money,
    profit_by_day,
    quantile,
    sample_size,
    sized,
    summarize,
    summarize_parts,
    total_range,
    worst_drawdown,
    write_days,
    write_trades,
)

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


def test_rules_written_out_read_back_the_same_and_can_be_changed_on_the_way():
    written = RULES.to_dict()

    assert WataRules.from_dict(written) == RULES
    assert isinstance(written["closed_dates"], list) and written["closed_dates"] == sorted(written["closed_dates"])
    assert WataRules.from_dict({**written, "stoploss_percent": -10}).stoploss_percent == -10


def test_rules_written_out_must_be_whole_and_hold_nothing_else():
    written = RULES.to_dict()

    with pytest.raises(ValueError, match="unknown stop_loss"):
        WataRules.from_dict({**written, "stop_loss": -10})
    with pytest.raises(ValueError, match="missing stoploss_percent"):
        WataRules.from_dict({name: value for name, value in written.items() if name != "stoploss_percent"})


def test_the_confidence_asked_of_an_entry_is_read_when_that_check_is_on():
    assert RULES.min_confidence == 0.3

    config = copy.deepcopy(EXAMPLE_CONFIG)
    config["trade"]["config"]["position_sizing"]["confidence_scaling"]["enabled"] = False
    assert WataRules.from_config(config).min_confidence == 0.0


def test_rules_written_before_a_rule_existed_take_the_value_that_changes_nothing():
    written = RULES.to_dict()
    for added in ("min_confidence", "funds_percent", "safety_units", "time_scaling", "confidence_scaling", "default_confidence"):
        del written[added]

    assert WataRules.from_dict(written) == replace(
        RULES, min_confidence=0.0, time_scaling=(), confidence_scaling=(),
    )


def test_the_size_of_an_order_is_read_as_the_trader_sizes_it():
    assert (RULES.funds_percent, RULES.safety_units, RULES.default_confidence) == (100, 1, 1.0)
    # Minutes of the day: 100 % until half past two, half of it at the US open, three quarters after.
    assert RULES.time_scaling == ((480, 870, 100), (870, 960, 50), (960, 1320, 75))
    assert RULES.confidence_scaling == ((0.0, 0.5, 50), (0.5, 0.8, 75), (0.8, 2.0, 100))
    # Written out and read back, from a file that only knows lists.
    assert WataRules.from_dict(json.loads(json.dumps(RULES.to_dict()))) == RULES

    config = copy.deepcopy(EXAMPLE_CONFIG)
    config["trade"]["config"]["buying_power"].update(max_account_funds_to_use_percentage=80, reserve_cash_percent=30)
    config["trade"]["config"]["position_sizing"]["time_of_day_scaling"]["enabled"] = False
    unscaled = WataRules.from_config(config)
    assert (unscaled.funds_percent, unscaled.time_scaling) == (50, ())


@pytest.mark.parametrize("clock_time, confidence, scale", [
    ("09:00", 1.2, 1.0),
    ("14:29", 1.2, 1.0),
    ("14:30", 1.2, 0.5),            # the US open
    ("16:00", 1.2, 0.75),
    ("16:00", 0.6, 0.5625),         # three quarters of three quarters
    ("09:00", 0.4, 0.5),
    ("09:00", None, 1.0),           # without a confidence: the default one, 1.0
    ("07:00", 1.2, 1.0),            # outside every period
    ("09:00", 2.0, 1.0),            # beyond every rule
])
def test_wata_puts_less_on_an_entry_at_some_hours_and_with_little_confidence(clock_time, confidence, scale):
    hours, minutes = (int(part) for part in clock_time.split(":"))

    assert RULES.position_scale(hours * 3600 + minutes * 60 + 59, confidence) == pytest.approx(scale)


# ── fills less kind than the bars ──


def test_a_fill_can_wait_longer_than_the_next_bar():
    sim = Simulator(NO_DAILY_TARGET, TURBO, CLOCK, execution=Execution(fill_delay=20))
    start = at("09:00")
    sim.on_bar(bar(start - 10, INDEX))
    sim.decide(Decision(start, LONG, "cross"))

    sim.on_bar(bar(start, INDEX + 4))
    sim.on_bar(bar(start + 10, INDEX + 8))
    assert sim.busy and sim.trades == []
    sim.on_bar(bar(start + 20, INDEX + 12))
    sim.finish()

    # Decided at 09:00:00, filled at the open of the bar of 09:00:20.
    assert (sim.trades[0].entry_at, sim.trades[0].entry_index) == (start + 20, INDEX + 12)


def test_an_exit_waits_as_an_entry_does():
    sim = Simulator(NO_DAILY_TARGET, TURBO, CLOCK, execution=Execution(fill_delay=10))
    start = at("09:00")
    sim.on_bar(bar(start - 10, INDEX))
    sim.decide(Decision(start, LONG, "cross"))
    sim.on_bar(bar(start, INDEX))
    sim.on_bar(bar(start + 10, INDEX))
    sim.decide(Decision(start + 20, FLAT, "reverse cross"))

    sim.on_bar(bar(start + 20, INDEX + 4))
    assert sim.trades == []
    sim.on_bar(bar(start + 30, INDEX + 8))

    assert (sim.trades[0].exit_at, sim.trades[0].performance) == (start + 30, 2.0)


def test_each_fill_loses_the_slippage_against_the_trade():
    sim = Simulator(NO_DAILY_TARGET, TURBO, CLOCK, execution=Execution(slippage=2.0))
    start = entered(sim)
    sim.decide(Decision(start + 10, FLAT, "reverse cross"))

    sim.on_bar(bar(start + 10, INDEX + 8))

    trade = sim.trades[0]
    # Bought with the index two points further on, sold two points back: four of the eight are lost.
    assert (trade.entry_index, trade.exit_index, trade.points, trade.performance) == (INDEX + 2, INDEX + 6, 4.0, 1.0)
    # From the first moment the turbo is behind by what the entry lost.
    assert trade.min_performance == -0.5


def test_slippage_is_lost_by_a_short_the_other_way_round_and_by_a_stop_as_well():
    sim = Simulator(RULES, TURBO, CLOCK, execution=Execution(slippage=2.0))
    start = entered(sim, SHORT)

    sim.on_bar(bar(start + 10, INDEX, high=INDEX + 70))

    trade = sim.trades[0]
    assert (trade.entry_index, trade.exit_index, trade.exit_reason) == (INDEX - 2, INDEX + 72, STOPLOSS)
    assert trade.performance == -18.5


def test_a_fill_is_neither_early_nor_better_than_the_price():
    with pytest.raises(ValueError):
        Execution(fill_delay=-10)
    with pytest.raises(ValueError):
        Execution(slippage=-1)


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


def test_an_entry_under_the_confidence_asked_is_refused_and_one_without_any_is_not():
    sim = simulator()
    start = at("09:00")
    sim.on_bar(bar(start - 10, INDEX))
    sim.decide(Decision(start, LONG, "cross", 0.2))
    sim.on_bar(bar(start, INDEX))
    assert [refusal.reason for refusal in sim.refusals] == [REFUSED_CONFIDENCE]

    sim.decide(Decision(start + 10, LONG, "cross"))
    sim.on_bar(bar(start + 10, INDEX))
    assert sim.busy and len(sim.refusals) == 1


def test_decisions_that_wait_for_the_same_bar_are_acted_on_in_the_order_they_came():
    sim = simulator(NO_DAILY_TARGET)
    start = at("09:00")
    sim.on_bar(bar(start - 10, INDEX))
    assert not sim.busy
    sim.decide(Decision(start, LONG, "signal"))
    sim.decide(Decision(start, FLAT, "close-position"))
    assert sim.busy

    sim.on_bar(bar(start, INDEX))

    assert [(trade.direction, trade.exit_reason, trade.entry_at, trade.exit_at) for trade in sim.trades] == [
        (LONG, "Signal: close-position", start, start),
    ]
    assert not sim.busy


# ── signals as the trader receives them ──


def orders(rules: WataRules = NO_DAILY_TARGET) -> Simulator:
    return Simulator(rules, TURBO, CLOCK, entries_are_orders=True)


def test_a_signal_the_trader_refuses_leaves_the_other_side_open():
    sim = orders(replace(NO_DAILY_TARGET, max_trades_per_day=1))
    start = entered(sim)
    sim.decide(Decision(start + 10, SHORT, "signal"))

    sim.on_bar(bar(start + 10, INDEX + 8))

    # A strategy's turn would have sold the long whatever became of the short.
    assert sim.trades == [] and sim.busy
    assert [refusal.reason for refusal in sim.refusals] == [REFUSED_TRADE_LIMIT]


def test_a_signal_the_trader_accepts_closes_the_other_side_at_the_same_open():
    sim = orders()
    start = entered(sim)
    sim.decide(Decision(start + 10, SHORT, "signal"))

    sim.on_bar(bar(start + 10, INDEX + 8))
    sim.finish()

    assert [(trade.direction, trade.exit_reason, trade.entry_index) for trade in sim.trades] == [
        (LONG, "Signal: reversal", INDEX), (SHORT, END_OF_DATA, INDEX + 8),
    ]


def test_the_day_is_judged_before_the_other_side_is_closed():
    # No entry above 1.25 % on the day: the long is at +2 %, but nothing is closed yet when the trader checks.
    sim = orders(replace(RULES, day_profit_target=1000.0))
    start = entered(sim)
    sim.decide(Decision(start + 10, SHORT, "signal"))

    sim.on_bar(bar(start + 10, INDEX + 8))
    sim.finish()

    assert sim.refusals == []
    assert [(trade.direction, trade.performance) for trade in sim.trades] == [(LONG, 2.0), (SHORT, 0.0)]


def test_a_close_of_one_side_leaves_the_other_alone():
    sim = orders()
    start = entered(sim)

    sim.decide(Decision(start + 10, FLAT, "close-short", closes=SHORT))
    sim.on_bar(bar(start + 10, INDEX))
    assert sim.trades == []

    sim.decide(Decision(start + 20, FLAT, "close-long", closes=LONG))
    sim.on_bar(bar(start + 20, INDEX + 4))
    assert [(trade.exit_reason, trade.performance) for trade in sim.trades] == [("Signal: close-long", 1.0)]


# ── the position monitor ──


def test_the_daily_target_reached_inside_a_bar_is_sold_at_the_target_not_at_the_high():
    sim = simulator()
    start = entered(sim)

    sim.on_bar(bar(start + 10, INDEX, high=INDEX + 20))     # +5 % at the high

    trade = sim.trades[0]
    assert (trade.exit_reason, trade.performance) == (DAILY_TARGET, 1.7)
    assert trade.exit_index == pytest.approx(INDEX + 6.8)
    assert trade.exit_at == start + 10


def test_the_best_level_of_a_trade_sold_at_a_target_is_the_target_to_the_cent():
    # After a loss of 5 %, the day's target asks 7.0526... % of the next trade: its best level is kept as 7.05.
    sim = simulator(replace(RULES, cooldown_after_loss_minutes=0))
    start = entered(sim)
    sim.decide(Decision(start + 10, FLAT, "reverse cross"))
    sim.on_bar(bar(start + 10, INDEX - 20))
    sim.decide(Decision(start + 20, LONG, "cross"))
    sim.on_bar(bar(start + 20, INDEX - 20))

    sim.on_bar(bar(start + 30, INDEX - 20, high=INDEX + 40))

    assert (sim.trades[1].exit_reason, sim.trades[1].max_performance) == (DAILY_TARGET, 7.05)


def test_a_result_of_nothing_is_zero_and_not_minus_zero():
    turbo = TurboModel(entry_price=4.4, points_per_unit=110.0, spread=0.03)
    barrier = turbo.barrier(LONG, INDEX)

    # Just under the price paid: rounds to nothing, which is written 0.0.
    nothing = turbo.performance(LONG, barrier, INDEX + 3.29)
    assert str(nothing) == "0.0"


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


def test_the_worst_level_a_trade_went_through_is_kept_with_its_best():
    sim = simulator(NO_DAILY_TARGET)
    start = entered(sim)
    sim.on_bar(bar(start + 10, INDEX, high=INDEX + 12, low=INDEX - 20, closed=INDEX + 8))
    sim.decide(Decision(start + 20, FLAT, "reverse cross"))

    sim.on_bar(bar(start + 20, INDEX + 8))

    trade = sim.trades[0]
    assert (trade.min_performance, trade.max_performance, trade.performance) == (-5.0, 3.0, 2.0)


def test_a_stopped_trade_went_no_lower_than_where_it_was_sold():
    sim = simulator()
    start = entered(sim)

    sim.on_bar(bar(start + 10, INDEX, low=INDEX - 80))

    assert (sim.trades[0].performance, sim.trades[0].min_performance) == (-20.0, -20.0)


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


class StepByStep(Simulator):
    """Goes through every bar of a position step by step, as if none could be passed over."""

    def _nothing_sells(self, bar: IndexBar) -> bool:
        return False


# As many entries as are asked for: neither a limit of the day nor a wait after a loss.
UNLIMITED = replace(NO_DAILY_TARGET, max_trades_per_day=0, cooldown_after_loss_minutes=0)


@pytest.mark.parametrize("rules", [
    RULES,
    UNLIMITED,
    replace(UNLIMITED, trailing_enabled=False, stoploss_percent=-4.0, max_profit_percent=6.0),
    replace(UNLIMITED, trailing_activation_percent=1.0, trailing_drawdown_percent=0.5, max_day_loss=-90.0),
    replace(UNLIMITED, day_profit_target=30.0, max_day_loss=-90.0),
    # Nothing sells before the end of the day: nearly every bar is passed over.
    replace(UNLIMITED, trailing_enabled=False, stoploss_percent=-99.0, max_profit_percent=5000.0),
], ids=["live", "no daily target", "tight stop and target", "tight trailing stop", "a far daily target", "held all day"])
def test_passing_over_the_bars_in_which_no_exit_can_be_reached_changes_nothing(rules):
    walk = random.Random(3)
    start = at("09:00")
    index = INDEX
    bars = []
    for number in range(4000):
        opened = index
        index += walk.gauss(0, 3)
        bars.append(bar(start + 10 * number, opened, max(opened, index) + abs(walk.gauss(0, 2)), min(opened, index) - abs(walk.gauss(0, 2)), index))
    # A change of mind every few minutes, flat now and then.
    decided = {number: walk.choice((LONG, SHORT, LONG, SHORT, FLAT)) for number in range(5, 4000, 37)}

    def replayed(sim: Simulator) -> Simulator:
        for number, given in enumerate(bars):
            sim.on_bar(given)
            if number in decided:
                sim.decide(Decision(given.start + 10, decided[number], "cross"))
        sim.finish()
        return sim

    fast, slow = replayed(Simulator(rules, TURBO, CLOCK)), replayed(StepByStep(rules, TURBO, CLOCK))

    # One trade when the day is won at once, dozens when nothing ends it.
    assert fast.trades == slow.trades and fast.trades
    assert fast.refusals == slow.refusals
    assert [day.performances for day in fast.days] == [day.performances for day in slow.days]


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


def test_the_summary_says_what_a_trade_brings_and_how_far_it_can_be_trusted():
    summary = summarize(traded())

    # One trade won 1.7, the other lost 15.
    assert (summary["expectancy"], summary["profit_factor"], summary["longest_losing_streak"]) == (-6.65, 0.11, 1)
    assert (summary["median_day"], summary["day_spread"]) == (-6.65, 8.35)
    assert (summary["winning_days"], summary["losing_days"]) == (1, 1)
    # Neither went against itself before it ended, nor for itself before it lost.
    assert summary["winners_lowest"] == {"median": 0.0, "tenth": 0.0}
    assert summary["losers_highest"] == {"median": 0.0, "tenth": 0.0}
    # Two trades over two days: said to be too few, and no range is made up from them.
    assert (summary["sample"], summary["total_range"]) == (TOO_FEW, None)


def test_what_was_won_cannot_be_divided_by_a_loss_there_was_not():
    sim = simulator()
    start = entered(sim)
    sim.on_bar(bar(start + 10, INDEX, high=INDEX + 20))
    sim.finish()

    summary = summarize(sim)

    assert (summary["profit_factor"], summary["average_loss"], summary["losers_highest"]) == (None, None, None)
    assert (summary["expectancy"], summary["longest_losing_streak"]) == (1.7, 0)


def test_the_longest_losing_streak_counts_trades_lost_in_a_row():
    assert longest_losing_streak([]) == 0
    assert longest_losing_streak([1.7, 1.7]) == 0
    # A trade that brought nothing was not won.
    assert longest_losing_streak([1.7, -15.0, -2.0, 0.0, 1.7, -15.0]) == 3


def test_a_share_of_values_falls_between_two_of_them_when_it_has_to():
    assert quantile([], 0.5) is None
    assert quantile([5.0], 0.9) == 5.0
    assert quantile([4.0, 1.0, 3.0, 2.0], 0.5) == 2.5
    assert quantile([0.0, 10.0], 0.1) == 1.0
    assert (quantile([1.0, 2.0, 3.0], 0.0), quantile([1.0, 2.0, 3.0], 1.0)) == (1.0, 3.0)


@pytest.mark.parametrize("trades, days, size", [
    (29, 200, TOO_FEW), (500, 19, TOO_FEW), (30, 20, FEW), (99, 200, FEW), (500, 59, FEW), (100, 60, FAIR),
])
def test_the_figures_of_a_run_rest_on_too_few_trades_or_days_few_or_a_fair_number(trades, days, size):
    assert sample_size(trades, days) == size


def test_the_range_of_the_total_is_where_it_falls_when_the_days_are_drawn_again():
    days = [1.7, 1.7, -15.0, 1.7, 0.0, 1.7, -4.0, 1.7, 1.7, 1.7]

    drawn = total_range(days, resamples=500)

    assert (drawn["level"], drawn["resamples"]) == (90, 500)
    # The total of these days is -9.9 %: most draws lose, some with two bad days and some with none.
    assert drawn["low"] < compounded(days) < drawn["high"]
    assert drawn["low"] < -20 and drawn["high"] > 5
    assert 50 < drawn["losing_share"] < 100
    # The same days give the same range, run after run.
    assert total_range(days, resamples=500) == drawn
    assert total_range(days, resamples=500, seed=7) != drawn


def test_no_range_is_given_for_a_handful_of_days_and_days_all_alike_have_a_range_of_one_value():
    assert total_range([1.7, -15.0, 1.7, 1.7]) is None

    alike = total_range([1.0] * 5, resamples=50)
    assert (alike["low"], alike["high"], alike["losing_share"]) == (5.1, 5.1, 0.0)


# ── in money ──


def priced(*trades: tuple[float, float]) -> list:
    """Trades of that result, each at that share of what an order may use."""
    sim = traded()
    return [replace(sim.trades[0], performance=performance, scale=scale) for performance, scale in trades]


def test_each_trade_is_sized_on_the_account_as_it_stands_in_whole_turbos():
    trades = priced((10.0, 1.0), (-15.0, 0.5), (1.7, 0.75))

    amounts = sized(trades, RULES, TURBO, 1000.0)

    # 1000 buys 250 turbos at 4.00, one of them left unbought: 249. Then half of 1099.60 (137 less one),
    # then three quarters of 1018.00 (190 less one).
    assert [(row.stake, row.profit_loss, row.equity) for row in amounts] == [
        (996.0, 99.6, 1099.6), (544.0, -81.6, 1018.0), (756.0, 12.85, 1030.85),
    ]
    found = money(trades, amounts, 1000.0)
    assert (found["capital"], found["final"], found["profit_loss"]) == (1000.0, 1030.85, 30.85)
    assert found["return_percent"] == pytest.approx(3.085, abs=0.006)
    # From 1099.60 down to 1018.00.
    assert (found["worst_drawdown"], found["worst_drawdown_percent"]) == (-81.6, -7.42)
    assert (found["average_scale_percent"], found["unfunded"]) == (75.0, 0)


def test_only_the_share_of_the_account_an_order_may_use_is_put_on_a_trade():
    amounts = sized(priced((10.0, 1.0)), replace(RULES, funds_percent=50.0, safety_units=0), TURBO, 1000.0)

    assert (amounts[0].stake, amounts[0].profit_loss) == (500.0, 50.0)


def test_an_account_too_small_for_one_turbo_and_its_margin_buys_nothing():
    trades = priced((10.0, 1.0))

    amounts = sized(trades, RULES, TURBO, 7.0)

    assert (amounts[0].stake, amounts[0].profit_loss, amounts[0].equity) == (0.0, 0.0, 7.0)
    assert money(trades, amounts, 7.0)["unfunded"] == 1


def test_without_a_capital_there_is_no_money_and_without_a_trade_the_capital_is_kept():
    assert money([], [], 0.0) is None

    found = money([], [], 1000.0)
    assert (found["final"], found["profit_loss"], found["average_scale_percent"], found["worst_drawdown"]) == (1000.0, 0.0, None, 0.0)


def test_what_a_day_brought_in_money_is_counted_on_the_day_of_the_entry():
    sim = traded()
    amounts = sized(sim.trades, RULES, TURBO, 1000.0)

    assert profit_by_day(sim.trades, amounts, CLOCK) == {DAY: 16.93, NEXT_DAY: -151.8}


def test_a_trade_keeps_the_share_wata_would_have_put_on_it():
    sim = simulator(NO_DAILY_TARGET)
    entered(sim, clock_time="15:00")
    sim.finish()

    # The US open, at a confidence of 1.2: half of what an order may use.
    assert sim.trades[0].scale == 0.5


def test_some_of_the_trades_and_days_can_be_summed_up_apart():
    sim = traded()

    first = summarize_parts(sim.trades[:1], sim.days[:1], sim.refusals, with_range=False)
    second = summarize_parts(sim.trades[1:], sim.days[1:], [])

    assert (first["days"], first["trades"], first["total_percent"], first["refusals"]) == (1, 1, 1.7, {REFUSED_DAY_PROFIT: 1})
    assert (second["days"], second["trades"], second["total_percent"], second["refusals"]) == (1, 1, -15.0, {})
    assert summarize(sim) == summarize_parts(sim.trades, sim.days, sim.refusals)


def test_a_backtest_without_a_trade_still_has_a_summary():
    sim = simulator()
    sim.on_bar(bar(at("09:00"), INDEX))
    sim.finish()

    summary = summarize(sim)

    assert (summary["days"], summary["trades"], summary["win_rate"], summary["total_percent"]) == (1, 0, None, 0.0)
    assert (summary["expectancy"], summary["profit_factor"], summary["median_day"], summary["day_spread"]) == (None, None, 0.0, None)
    assert (summary["winners_lowest"], summary["losers_highest"]) == (None, None)
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
    assert [line for line in lines if line.startswith("Won for 1 lost")][0].split()[-2:] == ["0.11", "-"]
    # Said in words, once, for the runs it is true of.
    assert (
        "Too few to conclude: 0 trade(s) over 0 day(s), 2 trade(s) over 2 day(s). "
        "Under 30 trades or 20 days, these figures describe those days and nothing else."
    ) in lines


def test_a_range_and_the_random_entries_beaten_are_shown_when_there_are_some():
    summary = {**summarize(traded()), "total_range": {"low": -20.5, "high": 3.43}, "against_random": {"beaten": 61.5}}

    lines = format_summaries([("110 pts", summary)]).splitlines()

    assert [line for line in lines if "redrawn days" in line][0].endswith("-20.50 to 3.43")
    assert [line for line in lines if line.startswith("Random entries beaten")][0].split()[-1] == "61.50"


def test_trades_and_days_are_written_as_csv(tmp_path):
    sim = traded()
    trades_path, days_path = tmp_path / "out" / "trades.csv", tmp_path / "out" / "days.csv"

    write_trades(str(trades_path), sim.trades)
    write_days(str(days_path), sim.days)

    trades = trades_path.read_text().splitlines()
    assert trades[0].startswith("entry_time,exit_time,direction,entry_reason,confidence,entry_index,exit_index")
    assert trades[0].endswith(",exit_reason,min_performance_percent,scale_percent,stake,profit_loss")
    # Without amounts: the share WATA would have put on each, and no money.
    assert trades[1] == "2026-10-12T07:00:00Z,2026-10-12T07:00:10Z,long,cross,1.2,30000.0,30006.8,6.8,1.7,1.7,Daily profit target,0.0,100.0,,"
    assert trades[2].endswith(",short,catch-up,,30000.0,30060.0,-60.0,-15.0,0.0,Stoploss,-15.0,100.0,,")
    assert days_path.read_text().splitlines() == [
        "day,entries,closed,day_percent,profit_loss,equity", "2026-10-12,1,1,1.7,,", "2026-10-13,1,1,-15.0,,",
    ]


def test_trades_and_days_are_written_with_their_money_when_there_is_a_capital(tmp_path):
    sim = traded()
    amounts = sized(sim.trades, RULES, TURBO, 1000.0)
    trades_path, days_path = tmp_path / "trades.csv", tmp_path / "days.csv"

    write_trades(str(trades_path), sim.trades, amounts)
    write_days(str(days_path), sim.days, profit_by_day(sim.trades, amounts, CLOCK), 1000.0)

    assert [line.split(",")[-3:] for line in trades_path.read_text().splitlines()[1:]] == [
        ["100.0", "996.0", "16.93"], ["100.0", "1012.0", "-151.8"],
    ]
    assert [line.split(",")[-2:] for line in days_path.read_text().splitlines()[1:]] == [["16.93", "1016.93"], ["-151.8", "865.13"]]
