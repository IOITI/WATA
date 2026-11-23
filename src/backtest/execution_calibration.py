"""
How long WATA's own orders take and what they cost, measured on what it
recorded of them, so that a backtest's fills are not assumed: from a signal
to the trader's decision (``signal_journal``), from an order leaving to
Saxo's acceptance (``order_execution``), the price a position was bought and
sold at against the turbo's quote recorded at that moment (``price_bar_10s``),
and the orders Saxo refused (``order_request``).

A price is measured twice. Against the quote standing when the order left, as
it always was: but Saxo sends a turbo a new quote about every 30 s and fills at
its live price, so that measure is mostly how far the turbo moved since its last
quote, which is no cost (the position is worth what was paid for it). And, since
v0.100.0, against the quote a run prices the order at (the bid that decided
it for an exit of the position monitor sent on a quote Saxo had just sent,
the turbo's next quote from its fill for a purchase and any other sale,
``at_the_fill``): what the order paid beyond the run's own price, which is
what a run has left to add.

Pure, over rows read with the SQL below, by the admin (``AdminQueries``) and
by the lab alike. What a backtest assumes (``signal_delay_seconds``,
``order_seconds``, ``slippage``, ``exit_slippage_percent``) can be set from
``suggested``.
"""

import math
from collections import defaultdict
from datetime import datetime
from statistics import median

from src.backtest.clock import BAR_SECONDS, TradingClock, ceil10, floor10
from src.backtest.data import LONG, NEXT_QUOTE_SECONDS, TurboRef, TurboSeries

# Fewer than this many measures of a kind: no value is suggested from them.
LEAST_MEASURES = 10
TICK = 0.01
# The recorded quote an order is set against: the open of its turbo's bar beginning at or after the moment it left,
# within this, as a backtest reads the quote standing at an order (``TurboSeries.quote_at`` at ``ceil10``).
QUOTE_WITHIN_SECONDS = 10
# An order Saxo took longer than this to accept waited for Saxo's order limit, which WATA waited 60 s for until
# v0.48.0 (``wait_after_refusal``): not what an order takes now, and left out of the measure.
ORDER_SECONDS_MOST = 30.0
# The bars read around each order's fill, to price it as a run does: those of the half minute before (whether
# Saxo had just sent a quote when the order left, ``quoted_just_before``), and as long after as a run looks for
# the next quote.
BARS_BEFORE_SECONDS = 40
BARS_AFTER_SECONDS = NEXT_QUOTE_SECONDS + 20
# A sale the trader sent (a signal, the day's close, the operator): its order left whenever, between two quotes. Any
# other is the position monitor's, sent on the quote that decided it when Saxo had just sent that quote.
EXPLICIT_CLOSE = "Explicit Close"

SIGNALS_SQL = """
SELECT signal_timestamp, received_timestamp, decided_at, outcome, position_id
FROM signal_journal
WHERE decided_at >= $1 AND decided_at < $2 AND action IN ('long', 'short')
"""

ORDERS_SQL = """
SELECT e.order_id, e.purpose, e.position_id, e.sent_at, e.accepted_at, e.outcome,
       p.instrument_uic, p.position_open_price, p.position_close_price, p.position_close_reason
FROM order_execution e
LEFT JOIN turbo_data_position p ON p.position_id = e.position_id
WHERE e.sent_at >= $1 AND e.sent_at < $2 AND e.purpose IN ('open', 'close')
"""

# The quote standing when each order left, as a backtest reads it: the open of the bar beginning at or after then.
QUOTES_SQL = f"""
SELECT q.order_id, b.bid_open AS bid_close, b.ask_open AS ask_close
FROM unnest($1::text[], $2::int[], $3::timestamptz[]) AS q(order_id, uic, at)
JOIN price_instrument i ON i.uic = q.uic AND i.role = 'turbo'
JOIN LATERAL (
    SELECT bid_open, ask_open FROM price_bar_10s b
    WHERE b.instrument_id = i.id AND b.ts >= q.at AND b.ts < q.at + interval '{QUOTE_WITHIN_SECONDS} seconds'
    ORDER BY b.ts LIMIT 1
) b ON true
"""

# The turbo's bars around each order's fill (Saxo's acceptance, which its fill follows within 0.05 s), from which the
# quote a run prices that order at is read by the run's own code (``at_the_fill``).
BARS_SQL = f"""
SELECT q.order_id, i.id AS instrument_id, extract(epoch FROM b.ts)::bigint AS ts,
       b.bid_open, b.bid_high, b.bid_low, b.bid_close, b.ask_open, b.ask_close
FROM unnest($1::text[], $2::int[], $3::timestamptz[]) AS q(order_id, uic, at)
JOIN price_instrument i ON i.uic = q.uic AND i.role = 'turbo'
JOIN price_bar_10s b ON b.instrument_id = i.id
    AND b.ts >= q.at - interval '{BARS_BEFORE_SECONDS} seconds' AND b.ts < q.at + interval '{BARS_AFTER_SECONDS} seconds'
ORDER BY q.order_id, i.id, b.ts
"""

REQUESTS_SQL = """
SELECT requested_at, purpose, outcome, error_code
FROM order_request
WHERE requested_at >= $1 AND requested_at < $2 AND operation = 'place'
"""


async def read_rows(fetch, start: datetime, end: datetime) -> dict:
    """
    The rows the measure is made of, between two moments, through ``fetch``
    (a coroutine taking SQL and its arguments). A table not created yet reads
    as no rows: that is ``fetch``'s to decide.
    """
    signals = await fetch(SIGNALS_SQL, start, end)
    orders = await fetch(ORDERS_SQL, start, end)
    priced = [row for row in orders if row["instrument_uic"] is not None and row["sent_at"] is not None]
    quotes = await fetch(
        QUOTES_SQL, [str(row["order_id"]) for row in priced], [int(row["instrument_uic"]) for row in priced],
        [row["sent_at"] for row in priced],
    ) if priced else []
    filled = [(row, _filled_at(row)) for row in priced]
    bars = await fetch(
        BARS_SQL, [str(row["order_id"]) for row, _ in filled], [int(row["instrument_uic"]) for row, _ in filled],
        [moment for _, moment in filled],
    ) if filled else []
    requests = await fetch(REQUESTS_SQL, start, end)
    return {
        "signals": [dict(row) for row in signals], "orders": [dict(row) for row in orders],
        "quotes": [dict(row) for row in quotes], "bars": [dict(row) for row in bars],
        "requests": [dict(row) for row in requests],
    }


def _filled_at(order: dict):
    """When an order was filled: Saxo's acceptance (its fill follows within 0.05 s), else the moment it left."""
    return order.get("accepted_at") or order.get("sent_at")


def _series_by_order(bars: list[dict]) -> dict[str, TurboSeries]:
    """
    The bars read around each order, as the series a run reads its quotes from.
    A Uic the recorder named under two instruments gives two sets of bars: the
    one with the most is the turbo's.
    """
    found: dict[str, dict[int, TurboSeries]] = defaultdict(dict)
    for row in bars:
        if row.get("ts") is None or row.get("bid_open") is None:
            continue
        instrument = int(row.get("instrument_id") or 0)
        series = found[str(row["order_id"])].get(instrument)
        if series is None:
            series = found[str(row["order_id"])][instrument] = TurboSeries(
                TurboRef(instrument, None, "", None, None, None, 0.0, LONG),
            )
        series.append(
            row["ts"], row["bid_open"], row["bid_high"], row["bid_low"], row["bid_close"], row.get("ask_open"),
            row.get("ask_close"),
        )
    return {order: max(by_instrument.values(), key=len) for order, by_instrument in found.items()}


def at_the_fill(series: TurboSeries, moment: float) -> tuple[float, float | None] | None:
    """
    The bid and ask a run prices an order filled at ``moment`` at (``fill_price``
    "next", src/backtest/simulator.py ``_purchase_fill`` and ``_close``): the
    turbo's next quote from the boundary the fill falls on, read by the run's own
    code, else the one standing there. None when the turbo had no bar then.
    """
    boundary = ceil10(moment)
    standing = series.quote_at(boundary)
    if standing is None:
        return None
    later = series.next_quote(boundary)
    if later is None:
        return standing
    return later[0], later[1] if later[1] is not None else standing[1]


def explicit(order: dict) -> bool:
    """Whether a sale was the trader's (a signal, the day's close, the operator), not an exit the monitor decided on a quote."""
    return EXPLICIT_CLOSE in str(order.get("position_close_reason") or "")


def quoted_just_before(series: TurboSeries, moment: float) -> bool:
    """
    Whether Saxo sent the turbo a new quote in the ten seconds ``moment`` falls
    in, or in the ten before: an order that left then left on a quote just
    sent, as the monitor's exits do (it sells within a second of the quote that
    decides one). Not so for an exit by the clock (the day's end, a release),
    nor for the stop of 19 October 2026, "hit" on a bid older than the purchase
    (before the monitor waited for a fresh quote, v0.38.0) and sold 9 % above
    it: set against that bid, it would be drawn as a windfall no exit gets.
    """
    start = floor10(moment)
    for boundary in (start, start - BAR_SECONDS):
        index = series.index_at(boundary)
        if index is not None and series.changed(index):
            return True
    return False


def _seconds(later, earlier) -> float | None:
    if later is None or earlier is None:
        return None
    return (later - earlier).total_seconds()


def _spread(values: list[float], digits: int = 2) -> dict:
    """Median, ninetieth percentile and count of some measures."""
    kept = sorted(value for value in values if value is not None and math.isfinite(value))
    if not kept:
        return {"count": 0, "median": None, "p90": None}
    return {
        "count": len(kept),
        "median": round(median(kept), digits),
        "p90": round(kept[min(len(kept) - 1, math.ceil(0.9 * len(kept)) - 1)], digits),
    }


def _finite(values) -> list[float]:
    return [float(value) for value in values if value is not None and math.isfinite(value)]


def _order_seconds(orders: list[dict]) -> list[float]:
    """From an order leaving to Saxo's acceptance, the waits for Saxo's order limit left out (ORDER_SECONDS_MOST)."""
    return [seconds for seconds in _finite(_seconds(row["accepted_at"], row["sent_at"]) for row in orders)
            if seconds <= ORDER_SECONDS_MOST]


def _decided(signals: list[dict], orders: list[dict]) -> list[tuple[dict, object]]:
    """
    Each signal with the moment the trader decided on it: when its purchase left for Saxo, for a signal that bought;
    the journal's ``decided_at`` otherwise, which a purchase stamps only once Saxo filled it.
    """
    left = {str(row["position_id"]): row["sent_at"] for row in orders
            if row.get("purpose") == "open" and row.get("position_id") is not None and row.get("sent_at") is not None}
    return [(row, left.get(str(row.get("position_id")), row["decided_at"])) for row in signals]


def samples(rows: dict) -> dict:
    """
    Each measure one by one: what a run on measured fills draws from
    (``execution.json``, src/backtest/execution.py). Order seconds from an order
    leaving to Saxo's acceptance; seconds from a signal to the trader's
    decision; and what an order paid, in euros a turbo, positive when worse:

    - beyond the quote a run prices it at: ``slippage_exit`` for an exit of the
      position monitor that left on a quote Saxo had just sent
      (``quoted_just_before``), against that quote, the bid standing when its
      order left, which decided it; ``slippage_buy_next`` for a purchase and
      ``slippage_sell_next`` for any other sale (the trader's, the monitor's by
      the clock or on an older bid), against the turbo's next quote from its
      fill (``at_the_fill``). What a run with ``fill_price`` "next" draws.
    - beyond the quote standing when it left (``slippage_buy``,
      ``slippage_sell``), as before v0.100.0: what the runs made before drew,
      and what a run with ``fill_price`` "standing" still does.
    """
    signals = rows.get("signals") or []
    orders = rows.get("orders") or []
    quotes = {str(row["order_id"]): row for row in rows.get("quotes") or []}
    series = _series_by_order(rows.get("bars") or [])
    paid: dict[str, list[float]] = {"open": [], "close": []}
    beyond: dict[str, list[float]] = {"buy": [], "sell": [], "exit": []}
    exits_percent: list[float] = []
    for row in orders:
        if row.get("outcome") != "confirmed":
            continue
        order_id = str(row["order_id"])
        quote = quotes.get(order_id)
        around = series.get(order_id)
        opened, closed = row.get("position_open_price"), row.get("position_close_price")
        on_its_quote = False
        if quote is not None:
            if row["purpose"] == "open" and opened is not None and quote["ask_close"] is not None:
                paid["open"].append(opened - quote["ask_close"])
            elif row["purpose"] == "close" and closed is not None and quote["bid_close"] is not None:
                paid["close"].append(quote["bid_close"] - closed)
                sent = row.get("sent_at")
                if not explicit(row) and around is not None and sent is not None and quoted_just_before(around, sent.timestamp()):
                    # The monitor's exit, sent on the quote that decided it: against that quote.
                    on_its_quote = True
                    beyond["exit"].append(quote["bid_close"] - closed)
                    if quote["bid_close"] > 0:
                        exits_percent.append((quote["bid_close"] - closed) / quote["bid_close"] * 100)
        filled = _filled_at(row)
        priced = at_the_fill(around, filled.timestamp()) if around is not None and filled is not None else None
        if priced is None:
            continue
        bid, ask = priced
        if row["purpose"] == "open" and opened is not None and ask is not None:
            beyond["buy"].append(opened - ask)
        elif row["purpose"] == "close" and closed is not None and not on_its_quote:
            beyond["sell"].append(bid - closed)
    return {
        "order_seconds": _order_seconds(orders),
        "slippage_buy": _finite(paid["open"]),
        "slippage_sell": _finite(paid["close"]),
        "slippage_buy_next": _finite(beyond["buy"]),
        "slippage_sell_next": _finite(beyond["sell"]),
        "slippage_exit": _finite(beyond["exit"]),
        "exit_slippage_percent": _finite(exits_percent),
        "signal_delay_seconds": _finite(_seconds(decided, row["signal_timestamp"]) for row, decided in _decided(signals, orders)),
    }


def measure(rows: dict, clock: TradingClock) -> dict:
    """What WATA's orders took and cost, and the values a backtest would be given from them."""
    signals = rows.get("signals") or []
    orders = rows.get("orders") or []
    decided = _decided(signals, orders)
    from_bar = _spread([_seconds(moment, row["signal_timestamp"]) for row, moment in decided])
    from_receipt = _spread([_seconds(moment, row["received_timestamp"]) for row, moment in decided])
    order_seconds = _spread(_order_seconds(orders), 3)
    waited = len(_finite(_seconds(row["accepted_at"], row["sent_at"]) for row in orders)) - order_seconds["count"]
    measured = samples(rows)
    paid = {"open": measured["slippage_buy"], "close": measured["slippage_sell"]}
    every = paid["open"] + paid["close"]
    slippage = {
        purpose: {**_spread(values, 4), "median_ticks": round(median(values) / TICK, 2) if values else None}
        for purpose, values in (("buy", paid["open"]), ("sell", paid["close"]), ("all", every))
    }
    # Beyond the quote a run prices each order at: its next quote for a purchase and a sale by a signal (together
    # ``orders``, what the run's ``slippage`` adds to both), the bid that decided it for the monitor's exit.
    priced = measured["slippage_buy_next"] + measured["slippage_sell_next"]
    at_fill = {
        purpose: {**_spread(values, 4), "median_ticks": round(median(values) / TICK, 2) if values else None}
        for purpose, values in (
            ("buy", measured["slippage_buy_next"]), ("sell", measured["slippage_sell_next"]),
            ("exit", measured["slippage_exit"]), ("orders", priced),
        )
    }
    exits_percent = _spread(measured["exit_slippage_percent"], 3)
    requests = rows.get("requests") or []
    by_hour: dict[int, list[int]] = defaultdict(lambda: [0, 0])
    codes: dict[str, int] = defaultdict(int)
    for row in requests:
        if row["requested_at"] is None:
            continue
        counted = by_hour[clock.local(row["requested_at"].timestamp()).hour]
        counted[0] += 1
        if row["outcome"] == "refused":
            counted[1] += 1
            codes[row.get("error_code") or "Unknown"] += 1
    refused = sum(counted[1] for counted in by_hour.values())
    suggested = {}
    if from_bar["count"] >= LEAST_MEASURES:
        suggested["signal_delay_seconds"] = round(max(0.0, from_bar["median"]), 1)
    if order_seconds["count"] >= LEAST_MEASURES:
        suggested["order_seconds"] = round(max(0.0, order_seconds["median"]), 1)
    # A run prices an order at the turbo's next quote: what it has left to add is what the orders paid beyond that
    # one. The measure against the quote standing when they left (0.02 € in November 2026) would count the turbo's
    # move since its last quote a second time.
    if at_fill["orders"]["count"] >= LEAST_MEASURES:
        suggested["slippage"] = round(max(0.0, at_fill["orders"]["median"]), 3)
    if exits_percent["count"] >= LEAST_MEASURES:
        suggested["exit_slippage_percent"] = round(max(0.0, exits_percent["median"]), 2)
    return {
        "signals": len(signals),
        "orders": len(orders),
        "requests": len(requests),
        # From the signal's own time (a script's bar close, TradingView's alert) and from the web server's receipt.
        "signal_delay_seconds": {"from_signal": from_bar, "from_receipt": from_receipt},
        "order_seconds": {**order_seconds, "left_out": waited, "left_out_over": ORDER_SECONDS_MOST},
        # Euros a turbo worse than the quote recorded when the order left (positive: worse).
        "slippage": slippage,
        # Euros a turbo worse than the quote a run prices the order at; the monitor's exits in percent of their bid too.
        "slippage_at_fill": {**at_fill, "exit_percent": exits_percent},
        "refusals": {
            "share_percent": round(refused / len(requests) * 100, 1) if requests else None,
            "by_hour": [{"hour": hour, "requests": counted[0], "refused": counted[1]} for hour, counted in sorted(by_hour.items())],
            "codes": dict(sorted(codes.items(), key=lambda item: -item[1])),
        },
        "suggested": suggested,
        "least": LEAST_MEASURES,
    }
