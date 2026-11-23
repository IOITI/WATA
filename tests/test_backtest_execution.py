"""
How a run's orders are filled (src/backtest/execution.py): at the settings'
fixed values, or drawn from WATA's own orders kept with the run; and the
service that keeps those orders with a run that asks for them.
"""

import asyncio
import json
from dataclasses import replace

import pytest

from src.admin.errors import NotPossible
from src.backtest.clock import TradingClock
from src.backtest import execution as execution_module
from src.backtest.execution import EXECUTION_FILE, FixedFills, MeasuredFills, fills_for, missing, priced_at_fill
from src.backtest.execution_calibration import BARS_SQL, at_the_fill, read_rows, samples
from src.backtest.settings import NEXT, BacktestSettings
from tests.test_admin_backtests import config, folder_of, pine, scripts, service  # noqa: F401 (fixtures)

# A tenth of the orders took a minute, as WATA's did.
SAMPLES = {
    "order_seconds": [0.8] * 18 + [61.0, 59.0],
    "slippage_buy": [0.03, 0.04] * 6,
    "slippage_sell": [0.02, 0.05] * 6,
    "signal_delay_seconds": [2.5] * 12,
}
MEASURED = replace(BacktestSettings(), execution="measured")


def test_fixed_fills_are_the_settings_own():
    fills = FixedFills(replace(BacktestSettings(), order_seconds=1.5, slippage=0.02))

    assert (fills.order_seconds(), fills.exit_seconds()) == (1.5, 0.0)
    # At the quote of the fill, a slippage worse; the quote when the order left plays no part.
    assert (round(fills.purchase(5.0, 5.1), 6), round(fills.sale(5.0, 4.9), 6)) == (5.12, 4.88)


def test_measured_fills_are_drawn_from_wata_s_orders_the_same_way_each_time():
    def day_of_draws(seed: str) -> list:
        fills = fills_for(MEASURED, SAMPLES, seed)
        fills.start_day("2026-10-14")
        return [(fills.order_seconds(), fills.purchase(5.0, 9.9), fills.sale(5.0, 0.1)) for _ in range(200)]

    first, again, other = day_of_draws("1:run"), day_of_draws("1:run"), day_of_draws("1:random:0")

    assert first == again and first != other
    # Priced from the quote when the order left, whatever the quote at the fill.
    assert {round(price, 2) for _, price, _ in first} == {5.03, 5.04}
    assert {round(price, 2) for _, _, price in first} == {4.98, 4.95}
    # The minute-long orders come back about a tenth of the time.
    late = sum(1 for seconds, _, _ in first if seconds > 50)
    assert 8 <= late <= 35


def test_a_cells_later_and_dearer_fills_add_to_the_draws():
    stressed = replace(MEASURED, order_seconds=MEASURED.order_seconds + 5, slippage=MEASURED.slippage + 0.02)

    fills = fills_for(stressed, SAMPLES, "1:run", base=MEASURED)

    assert isinstance(fills, MeasuredFills) and (fills.later_seconds, fills.worse_price) == (5, 0.02)
    assert fills.order_seconds() >= 5.8 and round(fills.purchase(5.0, 0), 2) in (5.05, 5.06)


def test_too_few_measures_to_draw_from_are_said():
    few = {**SAMPLES, "order_seconds": [1.0] * 3, "slippage_sell": []}

    assert missing(few) == ["3 order times", "0 sale prices"]
    assert missing(SAMPLES) == []
    with pytest.raises(ValueError, match="3 order times, 0 sale prices"):
        fills_for(MEASURED, few, "1:run")
    assert isinstance(fills_for(BacktestSettings(), None, "1:run"), FixedFills)


# What WATA's orders paid beyond the quote a run prices them at, measured since v0.100.0.
AT_FILL = {**SAMPLES, "slippage_buy_next": [0.0, 0.01] * 6, "slippage_sell_next": [0.01] * 12, "slippage_exit": [0.02] * 12}
MEASURED_NEXT = replace(MEASURED, fill_price=NEXT)


def test_measured_fills_draw_what_was_paid_beyond_the_quote_of_the_fill_when_the_run_fills_at_the_next_quote():
    assert execution_module.NEXT_FILL == NEXT
    # A run priced at the standing quote, and samples kept by a run made before, draw as before.
    assert priced_at_fill(MEASURED_NEXT, AT_FILL)
    assert not priced_at_fill(MEASURED, AT_FILL) and not priced_at_fill(MEASURED_NEXT, SAMPLES)
    assert not priced_at_fill(MEASURED_NEXT, None)

    fills = fills_for(MEASURED_NEXT, AT_FILL, "1:run")
    fills.start_day("2026-10-14")
    assert fills.at_fill and not fills_for(MEASURED, AT_FILL, "1:run").at_fill
    assert FixedFills(BacktestSettings()).at_fill
    # From the quote of the fill, whatever stood when the order left.
    assert {round(fills.purchase(1.0, 5.0), 2) for _ in range(100)} == {5.0, 5.01}
    assert {round(fills.sale(9.0, 5.0), 2) for _ in range(100)} == {4.99}
    assert {round(fills.sale(9.0, 5.0, decided=True), 2) for _ in range(100)} == {4.98}


def test_too_few_measures_beyond_the_quote_of_the_fill_are_said_too():
    few = {**AT_FILL, "slippage_buy_next": [0.01] * 4, "slippage_sell_next": [0.01] * 3, "slippage_exit": [0.02] * 3}

    assert missing(AT_FILL) == [] and missing(AT_FILL, at_fill=False) == []
    # The two kinds of sales are counted together: they are drawn as one while either is short.
    assert missing(few) == ["4 purchase prices", "6 sale prices"]
    assert missing({**few, "slippage_exit": [0.02] * 7}) == ["4 purchase prices"]
    # The same samples do for a run priced at the standing quote, which draws the measures of before.
    assert missing(few, at_fill=False) == []
    with pytest.raises(ValueError, match="4 purchase prices, 6 sale prices"):
        fills_for(MEASURED_NEXT, few, "1:run")
    assert isinstance(fills_for(MEASURED, few, "1:run"), MeasuredFills)


def _moment(clock_time: str):
    from datetime import datetime, timezone

    hour, minute, second = clock_time.split(":")
    whole, _, fraction = second.partition(".")
    return datetime(2026, 10, 19, int(hour), int(minute), int(whole), int((fraction or "0").ljust(6, "0")), tzinfo=timezone.utc)


def _bars(order_id: str, start: str, quotes: list, *, instrument_id: int = 3, spread: float = 0.02) -> list[dict]:
    """A turbo's ten-second bars from ``start`` around one order: each a bid, or (open, close) of a bar the quote changed in."""
    first = int(_moment(start).timestamp())
    rows = []
    for place, quote in enumerate(quotes):
        opened, closed = quote if isinstance(quote, tuple) else (quote, quote)
        rows.append({
            "order_id": order_id, "instrument_id": instrument_id, "ts": first + 10 * place,
            "bid_open": opened, "bid_high": max(opened, closed), "bid_low": min(opened, closed), "bid_close": closed,
            "ask_open": round(opened + spread, 6), "ask_close": round(closed + spread, 6),
        })
    return rows


def _measured_rows() -> dict:
    def order(order_id, purpose, sent, accepted, *, paid=None, sold=None, reason=None):
        return {"order_id": order_id, "purpose": purpose, "position_id": f"p{order_id}", "sent_at": _moment(sent),
                "accepted_at": _moment(accepted), "outcome": "confirmed", "instrument_uic": 7,
                "position_open_price": paid, "position_close_price": sold, "position_close_reason": reason}

    return {
        "signals": [],
        "orders": [
            # Bought between two quotes: 5.00 asked when it left, 5.10 at Saxo's next quote; it paid 5.05.
            order("1", "open", "12:00:02", "12:00:02.3", paid=5.05),
            # Sold by a signal: 5.20 bid when it left, 5.10 at the next quote; it got 5.12.
            order("2", "close", "12:05:00.5", "12:05:00.8", sold=5.12, reason="Explicit Close (All)"),
            # The monitor's stop, sent on the quote that decided it, 4.60, which Saxo had just sent: it got 4.57.
            order("3", "close", "12:20:05", "12:20:05.3", sold=4.57, reason="Performance (Stoploss (-7%) hit at -7.3%)"),
            # An order that waited a minute for Saxo's order limit: filled at 12:11:00, when the ask stood at 5.30.
            order("4", "open", "12:10:00", "12:11:00.2", paid=5.31),
            # Its turbo was not recorded around its fill: measured against the quote when it left alone.
            order("5", "open", "12:30:00", "12:30:00.3", paid=6.02),
            # The stop of 19 October 2026: "hit" on a bid of 4.627 Saxo had sent before the purchase, its order a
            # minute at Saxo, sold at 5.081, where the turbo stood by then (5.083).
            order("6", "close", "12:40:07", "12:41:07", sold=5.081, reason="Performance (Stoploss (-7%) hit at -8.47%)"),
            # The monitor's sale at the day's end, by the clock: 4.90 bid since a while, 4.86 at Saxo's next quote.
            order("7", "close", "15:55:01", "15:55:01.3", sold=4.87, reason="Performance (End of the trading day)"),
        ],
        # The quote standing when each order left (QUOTES_SQL names the open of that bar bid_close and ask_close).
        "quotes": [
            {"order_id": "1", "bid_close": 4.98, "ask_close": 5.00}, {"order_id": "2", "bid_close": 5.20, "ask_close": 5.22},
            {"order_id": "3", "bid_close": 4.60, "ask_close": 4.62}, {"order_id": "4", "bid_close": 4.98, "ask_close": 5.00},
            {"order_id": "5", "bid_close": 5.98, "ask_close": 6.00},
            {"order_id": "6", "bid_close": 4.627, "ask_close": 4.636}, {"order_id": "7", "bid_close": 4.90, "ask_close": 4.92},
        ],
        "bars": [
            *_bars("1", "11:59:50", [4.98, 4.98, (4.98, 5.08), 5.08, 5.08]),
            *_bars("2", "12:04:50", [5.20, 5.20, 5.20, (5.20, 5.10), 5.10]),
            *_bars("3", "12:19:40", [4.80, 4.80, (4.80, 4.60), 4.60, 4.60]),
            *_bars("4", "12:10:50", [5.20, 5.28, *[5.28] * 13]),
            *_bars("6", "12:40:30", [4.627, (4.627, 5.083), 5.083, 5.083, 5.083, 5.083]),
            *_bars("7", "15:54:30", [4.90, 4.90, 4.90, 4.90, (4.90, 4.86), 4.86]),
            # The same Uic named under an instrument of another day: a single stray bar, not the turbo's.
            *_bars("1", "12:00:10", [9.0], instrument_id=99),
        ],
        "requests": [],
    }


def test_each_order_is_also_measured_against_the_quote_a_run_prices_it_at():
    found = samples(_measured_rows())

    def rounded(kind):
        return [round(value, 4) for value in found[kind]]

    # Against the quote standing when it left, as always: the turbo's move since its last quote is in it, and the
    # minute an order waited.
    assert rounded("slippage_buy") == [0.05, 0.31, 0.02]
    # The stale stop is in it as a sale 0.454 € better than its quote: 9 % of the turbo.
    assert rounded("slippage_sell") == [0.08, 0.03, -0.454, 0.03]
    # Against the quote a run fills it at: Saxo's next one for the purchase and the sale by a signal (each a little
    # better than it), the quote standing at its own fill for the order that waited (no new quote in two minutes)...
    assert rounded("slippage_buy_next") == [-0.05, 0.01]
    # ...and the same for the sales that left between two quotes: the stop on a bid Saxo had sent long before, set
    # against the quote of its fill (0.002 € under it, no windfall), and the sale by the clock.
    assert rounded("slippage_sell_next") == [-0.02, 0.002, -0.01]
    # The monitor's exit sent on the quote that decided it: against that bid, in euros and in percent of it.
    assert rounded("slippage_exit") == [0.03]
    assert [round(value, 3) for value in found["exit_slippage_percent"]] == [0.652]


def test_an_exit_left_on_its_quote_when_saxo_had_just_sent_it():
    from src.backtest.data import TurboRef, TurboSeries
    from src.backtest.execution_calibration import quoted_just_before

    series = TurboSeries(TurboRef(1, None, "", None, None, None, 0.0, "long"))
    start = int(_moment("12:00:00").timestamp())
    # A new quote inside 12:00:10-20 (4.80 to 4.60), and one the bar of 12:01:00 opens on after a silence.
    for place, (opened, closed) in enumerate([(4.80, 4.80), (4.80, 4.60), (4.60, 4.60), (4.60, 4.60), (4.60, 4.60)]):
        series.append(start + 10 * place, opened, max(opened, closed), min(opened, closed), closed, opened + 0.02, closed + 0.02)
    series.append(start + 60, 4.70, 4.70, 4.70, 4.70, 4.72, 4.72)

    def at(clock_time):
        return quoted_just_before(series, _moment(clock_time).timestamp())

    # In the ten seconds of the new quote, and in the ten after (the quote came at their very end).
    assert at("12:00:15") and at("12:00:19.9") and at("12:00:25")
    # Twenty seconds on, it is a quote that stands; before it, the one before it was.
    assert not at("12:00:35") and not at("12:00:05")
    assert at("12:01:03")
    # No bar there: nothing says a quote had just come.
    assert not at("13:00:00")


def test_the_quote_of_a_fill_is_read_by_the_run_s_own_code():
    from src.backtest.clock import TradingClock
    from src.backtest.simulator import Simulator
    from tests.test_backtest_simulator import NEXT_QUOTE, at, requoted_turbo

    turbo = requoted_turbo()
    simulator = Simulator(NEXT_QUOTE, TradingClock("Europe/Paris"))

    # WATA's purchase of 19 October, filled at 12:38:37 on the test's day: what the simulator prices it at.
    assert at_the_fill(turbo, at("12:38:37")) == simulator._next_quote(turbo, int(at("12:38:40")))
    assert at_the_fill(turbo, at("12:38:37")) == (5.083, pytest.approx(5.092))
    # No new quote within two minutes: the one standing at the fill. No bar at the fill: no price.
    assert at_the_fill(turbo, at("13:00:01")) == (5.292, pytest.approx(5.301))
    assert at_the_fill(turbo, at("07:00:00")) is None


def test_a_run_s_slippage_is_suggested_from_what_orders_paid_beyond_its_own_price():
    from src.backtest.execution_calibration import measure

    rows = _measured_rows()
    few = measure(rows, TradingClock("Europe/Paris"))
    # Measured, but on too few orders to suggest anything from.
    assert few["slippage_at_fill"]["buy"] == {"count": 2, "median": -0.02, "p90": 0.01, "median_ticks": -2.0}
    assert few["slippage_at_fill"]["exit"]["count"] == 1 and few["slippage_at_fill"]["exit_percent"]["median"] == 0.652
    assert "slippage" not in few["suggested"] and "exit_slippage_percent" not in few["suggested"]

    # Ten times those orders: the median of what purchases and sales between two quotes paid beyond the quote they
    # are priced at (-0.01 here: nothing to add), not the 0.03 of the measure against the quote standing when they left.
    many = {**rows, "orders": [dict(order, order_id=f"{order['order_id']}-{copy}") for copy in range(10) for order in rows["orders"]],
            "quotes": [dict(quote, order_id=f"{quote['order_id']}-{copy}") for copy in range(10) for quote in rows["quotes"]],
            "bars": [dict(bar, order_id=f"{bar['order_id']}-{copy}") for copy in range(10) for bar in rows["bars"]]}
    found = measure(many, TradingClock("Europe/Paris"))
    assert found["slippage"]["all"]["median"] == 0.03
    assert found["slippage_at_fill"]["orders"] == {"count": 50, "median": -0.01, "p90": 0.01, "median_ticks": -1.0}
    assert found["suggested"]["slippage"] == 0.0
    assert found["suggested"]["exit_slippage_percent"] == 0.65


def test_the_bars_around_each_fill_are_read_with_the_orders():
    asked = []
    rows = _measured_rows()

    async def fetch(sql, *arguments):
        asked.append((sql, arguments))
        if "FROM order_execution" in sql:
            return rows["orders"]
        return rows["bars"] if sql == BARS_SQL else []

    found = asyncio.run(read_rows(fetch, _moment("00:00:00"), _moment("23:59:59")))

    (bars,) = [arguments for sql, arguments in asked if sql == BARS_SQL]
    # Each order with its turbo and the moment Saxo accepted it, which its fill follows at once.
    assert bars[0] == ["1", "2", "3", "4", "5", "6", "7"] and bars[1] == [7] * 7
    assert bars[2][0] == _moment("12:00:02.3") and bars[2][3] == _moment("12:11:00.2")
    assert found["bars"] == rows["bars"] and samples(found)["slippage_buy_next"] == samples(rows)["slippage_buy_next"]
    # Read from the key of the bars, around each order alone.
    assert "b.instrument_id = i.id" in BARS_SQL and "q.at - interval '40 seconds'" in BARS_SQL and "interval '140 seconds'" in BARS_SQL


def test_the_measure_keeps_each_order_one_by_one():
    from datetime import datetime, timedelta, timezone

    sent = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
    rows = {
        "signals": [{"signal_timestamp": sent, "received_timestamp": sent, "decided_at": sent + timedelta(seconds=3), "outcome": "executed"}],
        "orders": [
            {"order_id": "1", "purpose": "open", "sent_at": sent, "accepted_at": sent + timedelta(seconds=60), "outcome": "confirmed",
             "instrument_uic": 7, "position_open_price": 5.05, "position_close_price": None},
            {"order_id": "2", "purpose": "close", "sent_at": sent, "accepted_at": sent + timedelta(seconds=1), "outcome": "confirmed",
             "instrument_uic": 7, "position_open_price": None, "position_close_price": 4.9},
        ],
        "quotes": [{"order_id": "1", "bid_close": 4.98, "ask_close": 5.0}, {"order_id": "2", "bid_close": 4.95, "ask_close": 4.97}],
        "requests": [],
    }

    found = samples(rows)

    # The 60 s order waited for Saxo's order limit, which WATA no longer waits for (v0.48.0): left out.
    assert found["order_seconds"] == [1.0] and found["signal_delay_seconds"] == [3.0]
    assert [round(value, 4) for value in found["slippage_buy"]] == [0.05]
    assert [round(value, 4) for value in found["slippage_sell"]] == [0.05]


def test_a_signal_that_bought_was_decided_when_its_order_left_not_when_saxo_filled_it():
    from datetime import datetime, timedelta, timezone

    from src.backtest.execution_calibration import measure

    sent = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
    rows = {
        # Journaled once the purchase was filled, 62 s after its signal; its order left 2 s after it.
        "signals": [{"signal_timestamp": sent, "received_timestamp": sent, "decided_at": sent + timedelta(seconds=62),
                     "outcome": "executed", "position_id": "p1"}],
        "orders": [{"order_id": "1", "purpose": "open", "position_id": "p1", "sent_at": sent + timedelta(seconds=2),
                    "accepted_at": sent + timedelta(seconds=62), "outcome": "confirmed", "instrument_uic": 7,
                    "position_open_price": 5.05, "position_close_price": None}],
        "quotes": [], "requests": [],
    }

    assert samples(rows)["signal_delay_seconds"] == [2.0]
    found = measure(rows, TradingClock("Europe/Paris"))
    assert found["signal_delay_seconds"]["from_signal"]["median"] == 2.0
    assert (found["order_seconds"]["count"], found["order_seconds"]["left_out"]) == (0, 1)


# ── the service keeps the orders with the run ──


def test_a_run_on_measured_fills_keeps_the_orders_it_draws_from_and_its_stress_too(tmp_path, scripts, config):
    backtests = service(tmp_path, scripts, config)
    execution = {"from": "2026-07-22", "to": "2026-10-21", "samples": SAMPLES}

    async def ask():
        made = backtests.create(pine(scripts, changes={"execution": "measured"}), execution=execution)
        fixed = backtests.create(pine(scripts), execution=execution)
        refused = None
        try:
            backtests.create(pine(scripts, changes={"execution": "measured"}),
                             execution={**execution, "samples": {**SAMPLES, "slippage_buy": [0.01]}})
        except NotPossible as e:
            refused = str(e)
        return made, fixed, refused

    made, fixed, refused = asyncio.run(ask())

    assert json.loads((folder_of(tmp_path, made["id"]) / EXECUTION_FILE).read_text()) == execution
    # A run on fixed fills keeps none: it draws nothing.
    assert not (folder_of(tmp_path, fixed["id"]) / EXECUTION_FILE).exists()
    assert "1 purchase prices" in refused and "use fixed fills" in refused
