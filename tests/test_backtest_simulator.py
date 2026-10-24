import json
import sys
import types
from dataclasses import replace
from datetime import date

import pytest

# The live helpers are imported from the trading code, which imports the database layer.
asyncpg_stub = types.ModuleType("asyncpg")
asyncpg_stub.Pool = type("Pool", (), {})
asyncpg_stub.Record = dict
sys.modules.setdefault("asyncpg", asyncpg_stub)
sys.modules.setdefault("aio_pika", types.ModuleType("aio_pika"))

from src.backtest.clock import TradingClock, ceil10  # noqa: E402
from src.backtest.data import DayData, TurboRef, TurboSeries, sort_turbos  # noqa: E402
from src.backtest.rules import compounded  # noqa: E402
from src.backtest.selection import NotSelected, Selected, select_turbo  # noqa: E402
from src.backtest.settings import from_config  # noqa: E402
from src.backtest.simulator import SignalEvent, Simulator  # noqa: E402

CLOCK = TradingClock("Europe/Paris")
# A Wednesday.
DAY = date(2026, 10, 14)

with open("etc/config_example.json", encoding="utf-8") as config_file:
    LIVE = from_config(json.load(config_file))

# The live rules, without what makes the arithmetic of a test long: no scaling, no trailing stop, no cooldown, and
# the quote standing at a fill taken for the price and judged at once (the tests of the next quote say otherwise).
PLAIN = replace(
    LIVE, time_scaling_enabled=False, confidence_enabled=False, trailing_enabled=False, cooldown_after_loss_minutes=0,
    max_trades_per_day=0, day_profit_target=50.0, no_entry_above_day_profit=50.0, capital=10_000.0,
    fill_price="standing", wait_for_fresh_quote=False,
)


def at(clock_time: str) -> float:
    return CLOCK.epoch(DAY, clock_time)


def series(instrument_id: int, direction: str, barrier: float, bars, *, start: str = "08:00",
           spread: float = 0.05, issuer: str = "SG") -> TurboSeries:
    """
    A turbo's ten-second bars from ``start``: each a price (the same four
    times) or (open, high, low, close); the ask ``spread`` above the bid.
    """
    turbo = TurboSeries(TurboRef(
        instrument_id=instrument_id, uic=40_000_000 + instrument_id, asset_type="WarrantOpenEndKnockOut",
        symbol=f"T{instrument_id}", description=f"NASDAQ 100 Turbo {direction.title()} {barrier:g} {issuer}",
        issuer=issuer, barrier=barrier, direction=direction, currency="EUR",
    ))
    moment = int(at(start))
    for bar in bars:
        opened, high, low, close = bar if isinstance(bar, tuple) else (bar, bar, bar, bar)
        turbo.append(moment, opened, high, low, close, opened + spread, close + spread)
        moment += 10
    return turbo


def flat(price: float, minutes: float) -> list[float]:
    return [price] * int(minutes * 6)


def day_of(*turbos: TurboSeries, covered=None) -> DayData:
    start, end = CLOCK.day_bounds(DAY)
    by_direction = {}
    for turbo in turbos:
        by_direction.setdefault(turbo.ref.direction, []).append(turbo)
    return DayData(day=DAY, start=start, end=end, indice="us100", turbos=sort_turbos(by_direction), covered=covered)


def signal(number: int, action: str, clock_time: str, *, confidence=None, source="pine", indice="us100") -> SignalEvent:
    moment = at(clock_time)
    return SignalEvent(number, source, action, arrived_at=moment, alert_at=moment, indice=indice, confidence=confidence)


def close_of_day(number: int = 99) -> SignalEvent:
    moment = at(PLAIN.close_position_time) + 0.5
    return SignalEvent(number, "schedule", "close-position", arrived_at=moment, alert_at=moment, indice="n/a")


def run(day: DayData, events, settings=PLAIN) -> Simulator:
    simulator = Simulator(settings, CLOCK)
    simulator.run_day(day, events)
    return simulator


# ── the turbo bought ──


def test_the_turbo_bought_is_the_lowest_bid_in_range_among_the_first_twenty_by_level():
    day = day_of(
        series(1, "long", 24_900, flat(3.5, 840)),   # too close to its level: under the price range
        series(2, "long", 24_800, flat(4.6, 840)),
        series(3, "long", 24_700, flat(5.8, 840)),
        series(4, "short", 25_400, flat(4.2, 840)),
    )

    choice = select_turbo(day, "long", ceil10(at("09:30:03")), 4, 15)

    assert isinstance(choice, Selected)
    assert (choice.series.ref.instrument_id, choice.bid, choice.ask) == (2, 4.6, 4.6 + 0.05)
    assert [(entry["instrument_id"], entry["in_range"], entry["chosen"]) for entry in choice.candidates] == [
        (1, False, False), (2, True, True), (3, True, False),
    ]


def test_of_more_than_twenty_turbos_only_the_twenty_closest_to_their_level_are_looked_at():
    # The cheapest in range is the 21st by level: Saxo's search would not have given it.
    turbos = [series(number, "long", 25_000 - number * 10, flat(14.0 - number * 0.4, 600)) for number in range(1, 22)]

    choice = select_turbo(day_of(*turbos), "long", ceil10(at("09:00")), 4, 15)

    assert len(choice.candidates) == 20
    assert choice.series.ref.instrument_id == 20


def test_no_turbo_recorded_or_no_recorder_listening_is_no_price_not_a_trade():
    long_only = day_of(series(1, "long", 24_800, flat(5.0, 840)))
    nine = ceil10(at("09:00"))
    # Listening at nine, but its turbos were only found from three.
    after = day_of(series(1, "long", 24_800, flat(5.0, 60), start="15:00"), covered={nine})
    # Its own account of the day unknown: no bar of any turbo at nine says it was not listening.
    unknown = day_of(series(1, "long", 24_800, flat(5.0, 60), start="15:00"))

    for choice, rule in (
        (select_turbo(long_only, "short", nine, 4, 15), "no_recorded_turbo"),
        (select_turbo(after, "long", nine, 4, 15), "no_recorded_turbo"),
        (select_turbo(unknown, "long", nine, 4, 15), "recording_gap"),
        (select_turbo(day_of(series(1, "long", 24_800, flat(5.0, 840)), covered=set()), "long", ceil10(at("09:00")), 4, 15),
         "recording_gap"),
    ):
        assert isinstance(choice, NotSelected)
        assert (choice.fate, choice.rule) == ("unpriced", rule)


def test_turbos_quoted_out_of_range_fail_as_the_trader_fails():
    day = day_of(series(1, "long", 24_900, flat(2.5, 840)), series(2, "long", 24_000, flat(16.0, 840)))

    choice = select_turbo(day, "long", ceil10(at("09:00")), 4, 15)

    assert (choice.fate, choice.rule) == ("failed", "no_turbo_in_range")
    assert choice.reason == "NoTurbosAvailableException: No turbos in price range 4-15."


# ── an entry ──


def test_an_entry_is_sized_on_the_account_and_bought_at_the_ask_of_the_next_ten_seconds():
    day = day_of(series(2, "long", 24_800, flat(4.6, 840)))

    simulator = run(day, [signal(1, "long", "09:30:03"), close_of_day()])

    (trade,) = simulator.trades
    # 10,000 / 4.65 - 1 turbo kept for safety.
    assert trade["units"] == 2149
    assert trade["entry"]["price"] == pytest.approx(4.65)
    assert (trade["entry"]["decided_at"], trade["entry"]["filled_at"]) == ("2026-10-14T07:30:03Z", "2026-10-14T07:30:04Z")
    assert trade["turbo"]["instrument_id"] == 2
    # The quotes stopped at 22:00 and the day's close found it at its bid: no knock-out at that price.
    assert trade["exit"]["reason"] == "Explicit Close (All)"
    assert trade["performance_percent"] == round(4.6 * 100 / 4.65 - 100, 2)
    assert simulator.signals[0]["fate"] == "executed" and simulator.signals[0]["trade_id"] == trade["id"]
    assert simulator.signals[1]["fate"] == "closed"


def test_an_entry_the_rules_refuse_closes_nothing():
    day = day_of(series(2, "long", 24_800, flat(4.6, 840)))

    simulator = run(day, [signal(1, "long", "09:00"), signal(2, "long", "09:10"), signal(3, "long", "07:59:30"), close_of_day()])

    duplicate = next(record for record in simulator.signals if record["id"] == 2)
    early = next(record for record in simulator.signals if record["id"] == 3)
    assert (duplicate["fate"], duplicate["rule"], duplicate["reason"]) == (
        "refused", "duplicate", "Duplicate signal: long position already open",
    )
    assert (early["fate"], early["rule"]) == ("refused", "market_hours")
    assert len(simulator.trades) == 1 and simulator.trades[0]["exit"]["reason"] == "Explicit Close (All)"


def test_an_opposite_entry_sells_first_and_buys_with_what_the_sale_gave():
    up = [series(2, "long", 24_800, flat(5.0, 60) + flat(5.5, 780))]
    down = [series(3, "short", 25_200, flat(6.0, 840))]

    simulator = run(day_of(*up, *down), [signal(1, "long", "08:30"), signal(2, "short", "09:30"), close_of_day()])

    first, second = simulator.trades
    assert first["exit"]["reason"] == "Explicit Close (All)" and first["exit"]["signal_id"] == 2
    assert first["performance_percent"] == round(5.5 * 100 / 5.05 - 100, 2)
    # Bought with the whole account after the sale.
    assert second["direction"] == "short"
    assert second["units"] == int(first["cash_after"] / 6.05 - 1)
    assert simulator.signals[1]["closed_trade_ids"] == [first["id"]]


def test_with_a_cash_reserve_the_new_position_is_bought_first_then_the_other_sold():
    reserve = replace(PLAIN, reserve_cash_percent=20)
    day = day_of(series(2, "long", 24_800, flat(5.0, 840)), series(3, "short", 25_200, flat(6.0, 840)))

    simulator = run(day, [signal(1, "long", "08:30"), signal(2, "short", "09:30"), close_of_day()], reserve)

    long_trade = simulator.trades[0]
    assert long_trade["direction"] == "long"
    assert long_trade["exit"]["filled_at"] == "2026-10-14T07:30:02Z"
    # 80 % of the account for the long; the short with 80 % of what was left before the long was sold.
    assert long_trade["units"] == int(10_000 * 0.8 / 5.05 - 1)


def test_an_account_too_small_for_one_turbo_fails_as_insufficient_funds():
    poor = replace(PLAIN, capital=8.0)
    simulator = run(day_of(series(2, "long", 24_800, flat(5.0, 840))), [signal(1, "long", "09:00"), close_of_day()], poor)

    assert (simulator.signals[0]["fate"], simulator.signals[0]["rule"]) == ("failed", "insufficient_funds")
    assert simulator.signals[0]["reason"] == "InsufficientFundsException: Insufficient funds @ 5.05"
    assert simulator.trades == []


def test_the_size_follows_the_time_of_day_and_the_confidence():
    scaled = replace(PLAIN, time_scaling_enabled=True, confidence_enabled=True, time_periods=LIVE.time_periods,
                     confidence_bands=LIVE.confidence_bands, min_confidence=0.3)
    day = day_of(series(2, "long", 24_800, flat(5.0, 840)))

    # 15:00 is the US open (50 %), a confidence of 0.6 is medium (75 %).
    simulator = run(day, [signal(1, "long", "15:00", confidence=0.6), close_of_day()], scaled)

    (trade,) = simulator.trades
    assert trade["scale_percent"] == 37.5
    assert trade["units"] == int(10_000 * 0.375 / 5.05 - 1)


# ── the position monitor ──


def test_a_stop_crossed_inside_ten_seconds_sells_at_the_bid_that_crossed_it_or_at_its_level():
    # Bought at 5.05; a stop of -15 % is 4.2925: the bar falls to 4.10 and closes at 4.50.
    path = flat(5.0, 30) + [(5.0, 5.0, 4.10, 4.50)] + flat(4.5, 780)
    day = day_of(series(2, "long", 24_800, path))
    events = [signal(1, "long", "08:00:01"), close_of_day()]

    worst = run(day, events).trades[0]
    level = run(day, events, replace(PLAIN, stop_fill="level")).trades[0]

    assert worst["exit"]["reason"] == "Performance (Stoploss (-15%) hit at -18.81%)"
    assert worst["exit"]["group"] == "Stop-loss"
    assert worst["exit"]["bid"] == 4.10 and worst["performance_percent"] == -18.81
    # The stop's own price is 4.2925: the first quote under it, to the cent.
    assert level["exit"]["bid"] == 4.29
    assert level["performance_percent"] == -15.05
    assert worst["exit"]["filled_at"] == "2026-10-14T06:30:10Z"


def test_a_take_profit_crossed_inside_ten_seconds_sells_at_its_level_never_better():
    path = flat(5.0, 30) + [(5.0, 9.0, 5.0, 8.5)] + flat(8.5, 780)
    take = replace(PLAIN, max_profit_percent=60)

    trade = run(day_of(series(2, "long", 24_800, path)), [signal(1, "long", "08:00:01"), close_of_day()], take).trades[0]

    assert trade["exit"]["reason"].startswith("Performance (Takeprofit (60%) hit at")
    assert trade["exit"]["bid"] == pytest.approx(5.05 * 1.6)
    assert trade["performance_percent"] == 60.0
    assert trade["max_performance_percent"] == 60.0


def test_the_trailing_stop_sells_once_the_best_was_high_enough_and_fell_far_enough():
    trailing = replace(PLAIN, trailing_enabled=True, trailing_activation_percent=5, trailing_drawdown_percent=8)
    # Up to +10.9 % (5.6), then down: at 5.15 the fall from the best is 8.9 points.
    path = flat(5.0, 10) + flat(5.6, 10) + flat(5.15, 10) + flat(5.0, 780)

    trade = run(day_of(series(2, "long", 24_800, path)), [signal(1, "long", "08:00:01"), close_of_day()], trailing).trades[0]

    assert trade["exit"]["group"] == "Trailing stop"
    assert trade["exit"]["reason"] == "Performance (Trailing Stop: peak 10.9%, now 2.0% (dd 8.9% >= 8%))"
    assert trade["max_performance_percent"] == 10.89


def test_the_days_target_counts_what_the_day_already_made():
    target = replace(PLAIN, day_profit_target=1.7)
    # A first trade made +0.99 % (5.05 to 5.10); the second then sells once the day would reach 1.7 %.
    longs = series(2, "long", 24_800, flat(5.0, 60) + flat(5.10, 60) + flat(5.0, 60) + [(5.0, 5.2, 5.0, 5.2)] + flat(5.2, 600))
    shorts = series(3, "short", 25_200, flat(6.0, 840))
    events = [signal(1, "long", "08:00:01"), signal(2, "short", "09:00:01"), signal(3, "long", "10:00:01"), close_of_day()]

    simulator = run(day_of(longs, shorts), events, target)

    third = simulator.trades[-1]
    assert third["exit"]["group"] == "Daily profit target"
    day_before = third["day_percent_before"]
    assert compounded([day_before, third["performance_percent"]]) >= 1.7


def test_a_close_signal_closes_its_side_and_is_ignored_when_nothing_is_open():
    day = day_of(series(2, "long", 24_800, flat(5.0, 840)))
    events = [signal(1, "close-short", "08:30"), signal(2, "long", "09:00"), signal(3, "close-short", "09:30"),
              signal(4, "close-long", "10:00"), close_of_day()]

    simulator = run(day, events)

    fates = {record["id"]: (record["fate"], record["reason"]) for record in simulator.signals}
    assert fates[1] == ("ignored", "No open position to close")
    assert fates[3] == ("ignored", "No open position to close")
    assert fates[4] == ("closed", "Closed 1 position(s). Errors: 0")
    assert simulator.trades[0]["exit"]["reason"] == "Explicit Close (long)"
    assert fates[99] == ("ignored", "No open position to close")


def test_a_turbo_whose_quotes_stop_after_it_fell_away_is_knocked_out_at_nothing():
    # Quotes fall to a fifth of the day's best and stop, while another turbo is still quoted.
    held = series(2, "long", 24_800, flat(5.0, 30) + [(5.0, 5.0, 0.9, 0.9)])
    other = series(3, "long", 24_700, flat(6.0, 840))
    no_stop = replace(PLAIN, stoploss_percent=-100)

    simulator = run(day_of(held, other), [signal(1, "long", "08:00:01"), close_of_day()], no_stop)

    trade = simulator.trades[0]
    assert (trade["exit"]["reason"], trade["exit"]["group"], trade["exit"]["bid"]) == ("SaxoAPI", "Closed at Saxo", 0.0)
    assert trade["performance_percent"] == -100.0
    assert "taken for knocked out" in trade["notes"][-1]


def test_a_losing_close_starts_the_cooldown_and_the_day_has_a_most_of_entries():
    rules = replace(PLAIN, cooldown_after_loss_minutes=10, max_trades_per_day=2)
    longs = series(2, "long", 24_800, flat(5.0, 840))
    shorts = series(3, "short", 25_200, flat(6.0, 840))
    events = [signal(1, "long", "08:00:01"), signal(2, "close-long", "08:30"), signal(3, "short", "08:35"),
              signal(4, "short", "08:45"), signal(5, "close-short", "09:00"), signal(6, "long", "09:30"), close_of_day()]

    simulator = run(day_of(longs, shorts), events, rules)

    fates = {record["id"]: (record["fate"], record["rule"]) for record in simulator.signals}
    # The long lost its spread: no entry for ten minutes after its sale.
    assert fates[3] == ("refused", "cooldown")
    assert fates[4] == ("executed", None)
    assert fates[6] == ("refused", "max_trades")
    assert simulator.signals[2]["reason"].startswith("Breaking trading rule: Cooldown active after last loss. Remaining: 30")


def test_a_day_that_made_enough_takes_no_more_entries():
    rules = replace(PLAIN, no_entry_above_day_profit=1.25)
    longs = series(2, "long", 24_800, flat(5.0, 60) + flat(5.2, 780))

    simulator = run(day_of(longs), [signal(1, "long", "08:00:01"), signal(2, "close-long", "09:30"),
                                    signal(3, "long", "10:00"), close_of_day()], rules)

    third = simulator.signals[2]
    assert (third["fate"], third["rule"]) == ("refused", "day_profit")
    assert third["reason"] == (
        "Breaking trading rule : The current profit percentage (2.97) is more than the allowed percentage (1.25), "
        "so no more trade are allowed for today."
    )


def test_a_gap_of_the_recorder_while_a_position_is_open_is_noted_on_the_trade():
    held = series(2, "long", 24_800, flat(5.0, 30) + flat(5.1, 30))
    held_later = series(2, "long", 24_800, flat(5.1, 600), start="09:10")
    # One turbo, its bars cut for ten minutes.
    merged = held
    for index in range(len(held_later.ts)):
        merged.append(held_later.ts[index], held_later.bid_open[index], held_later.bid_high[index], held_later.bid_low[index],
                      held_later.bid_close[index], held_later.ask_open[index], held_later.ask_close[index])
    covered = {stamp for stamp in merged.ts}

    simulator = run(day_of(merged, covered=covered), [signal(1, "long", "08:00:01"), close_of_day()])

    assert simulator.trades[0]["notes"][0] == "09:00:00–09:10:00: the price recorder was not listening"


def test_commission_and_slippage_are_taken_on_both_orders():
    costly = replace(PLAIN, commission_per_order=2.5, commission_percent=0.1, slippage=0.01)

    trade = run(day_of(series(2, "long", 24_800, flat(5.0, 840))), [signal(1, "long", "09:00"), close_of_day()], costly).trades[0]

    assert trade["entry"]["price"] == pytest.approx(5.06)
    assert trade["exit"]["bid"] == pytest.approx(4.99)
    bought = trade["units"] * 5.06
    sold = trade["units"] * 4.99
    assert trade["commission"] == round(2.5 + bought * 0.001 + 2.5 + sold * 0.001, 2)
    assert trade["profit_loss"] == round(sold - (2.5 + sold * 0.001) - (bought + 2.5 + bought * 0.001), 2)


def test_the_trader_takes_signals_one_after_the_other():
    longs = series(2, "long", 24_800, flat(5.0, 840))
    shorts = series(3, "short", 25_200, flat(6.0, 840))
    # Arrived together: the second is decided once the first's orders are filled.
    events = [signal(1, "long", "09:00"), signal(2, "short", "09:00")]

    simulator = run(day_of(longs, shorts), events + [close_of_day()])

    assert simulator.signals[1]["decided_at"] == "2026-10-14T07:00:01Z"
    assert [trade["direction"] for trade in simulator.trades] == ["long", "short"]


def test_the_day_says_what_its_signals_became_and_what_was_recorded():
    day = day_of(series(2, "long", 24_800, flat(5.0, 840)), series(3, "short", 25_200, flat(6.0, 840)))

    simulator = run(day, [signal(1, "long", "09:00"), signal(2, "long", "09:10"), close_of_day()])

    (record,) = simulator.days
    assert record["signals"]["executed"] == 1 and record["signals"]["refused"] == 1 and record["signals"]["closed"] == 1
    assert (record["entries"], record["trades"], record["recorded"]["long"], record["recorded"]["short"]) == (1, 1, 1, 1)
    assert record["recorded"]["first"] == "2026-10-14T06:00:00Z"
    assert record["equity"] == pytest.approx(simulator.cash)


# ── what the trader and the monitor do so as not to hold what cannot be sold ──


def test_with_a_cash_reserve_an_entry_that_finds_no_turbo_still_sells_the_other_side():
    # As the trader does (handle_trading_signal): the signal turned the other way.
    reserve = replace(PLAIN, reserve_cash_percent=20)
    day = day_of(series(2, "long", 24_800, flat(5.0, 840)))

    simulator = run(day, [signal(1, "long", "08:30"), signal(2, "short", "09:30"), close_of_day()], reserve)

    (long_trade,) = simulator.trades
    assert long_trade["exit"]["reason"] == "Explicit Close (All)"
    # Nothing was bought: the sale leaves when the selection found nothing, filled an order's time later.
    assert long_trade["exit"]["filled_at"] == "2026-10-14T07:30:01Z"
    assert simulator.signals[1]["fate"] != "executed"
    assert simulator.signals[1]["closed_trade_ids"] == [long_trade["id"]]


def test_without_a_cash_reserve_the_sale_comes_first_as_before():
    day = day_of(series(2, "long", 24_800, flat(5.0, 840)))

    simulator = run(day, [signal(1, "long", "08:30"), signal(2, "short", "09:30"), close_of_day()])

    (long_trade,) = simulator.trades
    assert long_trade["exit"]["reason"] == "Explicit Close (All)"
    assert simulator.signals[1]["closed_trade_ids"] == [long_trade["id"]]


def _with_news(flatten_minutes_before: int):
    return replace(PLAIN, news_window=True, news_flatten_minutes_before=flatten_minutes_before)


def test_what_is_held_is_sold_before_a_us_release_when_the_rule_says_so():
    # CPI of 14 October 2026 at 14:30 in Paris; the turbo held through it was not quoted for 250 s that day.
    day = day_of(series(2, "long", 24_800, flat(5.0, 840)))

    simulator = run(day, [signal(1, "long", "14:00"), close_of_day()], _with_news(10))

    (trade,) = simulator.trades
    assert trade["exit"]["reason"] == "Performance (Flat before CPI (14:30))"
    assert trade["exit"]["filled_at"] == "2026-10-14T12:20:00Z"
    assert trade["exit"]["group"] == "Before a US release"


def test_no_entry_is_taken_that_would_be_sold_at_once():
    day = day_of(series(2, "long", 24_800, flat(5.0, 840)))

    simulator = run(day, [signal(1, "long", "14:21"), close_of_day()], _with_news(10))

    assert simulator.trades == []
    assert simulator.signals[0]["reason"] == "Breaking trading rule: no entry around CPI (14:30) from 14:20 to 14:45."


def test_without_the_sale_before_a_release_a_position_is_held_through_it_as_before():
    day = day_of(series(2, "long", 24_800, flat(5.0, 840)))

    simulator = run(day, [signal(1, "long", "14:00"), close_of_day()], _with_news(0))

    (trade,) = simulator.trades
    assert trade["exit"]["reason"] == "Explicit Close (All)"


# ── Saxo's next quote ──


# 19 October 2026 (here on the test's day): Saxo quoted the turbo every 30 s or so, at 4.627 from 10:38:10 UTC
# while the index rose, and at 5.083 from 10:38:40. WATA bought at 10:38:37 and paid 5.055.
def requoted_turbo() -> TurboSeries:
    bars = flat(4.638, 1) + [(4.638, 4.638, 4.627, 4.627)] + flat(4.627, 0.5) + [(4.627, 5.083, 4.627, 5.083)]
    bars += flat(5.083, 0.5) + [(5.083, 5.292, 5.083, 5.292)] + flat(5.292, 600)
    return series(2, "long", 29_041.634, bars, start="12:37:00", spread=0.009, issuer="Saxo")


NEXT_QUOTE = replace(PLAIN, fill_price="next", wait_for_fresh_quote=True, stoploss_percent=-7)


def test_an_order_is_priced_at_the_turbos_next_quote_and_judged_from_it():
    simulator = run(day_of(requoted_turbo()), [signal(1, "long", "12:38:36"), close_of_day()], NEXT_QUOTE)

    (trade,) = simulator.trades
    assert trade["entry"]["price"] == pytest.approx(5.092)
    assert (trade["entry"]["ask"], trade["entry"]["bid"]) == (pytest.approx(4.636), 4.627)
    assert trade["notes"][0] == "Priced at the turbo's next quote, 5.092: the ask standing at the fill was 4.636"
    # The bid standing at the purchase was never set against the price paid: no stop, no worst of -9 %.
    assert trade["exit"]["reason"] == "Explicit Close (All)"
    assert trade["min_performance_percent"] == pytest.approx(-0.18)


def test_without_the_wait_the_quote_standing_at_the_purchase_sells_as_the_monitor_of_before_did():
    simulator = run(day_of(requoted_turbo()), [signal(1, "long", "12:38:36"), close_of_day()],
                    replace(NEXT_QUOTE, wait_for_fresh_quote=False))

    (trade,) = simulator.trades
    assert trade["exit"]["reason"] == "Performance (Stoploss (-7%) hit at -9.13%)"
    assert trade["exit"]["bid"] == 4.627


def test_a_run_made_before_keeps_the_quote_standing_at_the_fill():
    simulator = run(day_of(requoted_turbo()), [signal(1, "long", "12:38:36"), close_of_day()], replace(PLAIN, stoploss_percent=-7))

    (trade,) = simulator.trades
    assert trade["entry"]["price"] == pytest.approx(4.636)
    assert trade["notes"] == []


def test_a_sale_is_priced_at_the_turbos_next_quote_too():
    simulator = run(day_of(requoted_turbo()), [signal(1, "long", "12:38:36"), signal(2, "close-position", "12:39:05")],
                    NEXT_QUOTE)

    (trade,) = simulator.trades
    assert trade["exit"]["bid"] == 5.292
    assert trade["notes"][-1] == "Sold at the turbo's next quote, 5.292: the bid standing at the fill was 5.083"


def test_a_turbo_saxo_did_not_quote_again_is_priced_at_its_standing_quote():
    simulator = run(day_of(series(2, "long", 24_800, flat(5.0, 840))), [signal(1, "long", "09:00"), close_of_day()], NEXT_QUOTE)

    (trade,) = simulator.trades
    assert trade["entry"]["price"] == pytest.approx(5.05)
    assert trade["exit"]["bid"] == 5.0


def test_measured_fills_keep_the_quote_their_measure_compares():
    turbo = requoted_turbo()
    boundary = ceil10(at("12:38:37"))

    assert Simulator(NEXT_QUOTE, CLOCK)._next_quote(turbo, boundary) == (5.083, pytest.approx(5.092))
    assert Simulator(replace(NEXT_QUOTE, execution="measured"), CLOCK)._next_quote(turbo, boundary) is None
    assert Simulator(replace(NEXT_QUOTE, fill_price="standing"), CLOCK)._next_quote(turbo, boundary) is None
