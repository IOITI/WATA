"""
How long WATA's own orders take and what they cost, measured on what it
recorded of them, so that a backtest's fills are not assumed: from a signal
to the trader's decision (``signal_journal``), from an order leaving to
Saxo's acceptance (``order_execution``), the price a position was bought and
sold at against the turbo's quote recorded at that moment (``price_bar_10s``),
and the orders Saxo refused (``order_request``).

Pure, over rows read with the SQL below, by the admin (``AdminQueries``) and
by the lab alike. What a backtest assumes (``signal_delay_seconds``,
``order_seconds``, ``slippage``) can be set from ``suggested``.
"""

import math
from collections import defaultdict
from datetime import datetime
from statistics import median

from src.backtest.clock import TradingClock

# Fewer than this many measures of a kind: no value is suggested from them.
LEAST_MEASURES = 10
TICK = 0.01
# The recorded quote of a turbo is taken from the bars that ended within this before an order left.
QUOTE_WITHIN_SECONDS = 60

SIGNALS_SQL = """
SELECT signal_timestamp, received_timestamp, decided_at, outcome
FROM signal_journal
WHERE decided_at >= $1 AND decided_at < $2 AND action IN ('long', 'short')
"""

ORDERS_SQL = """
SELECT e.order_id, e.purpose, e.sent_at, e.accepted_at, e.outcome,
       p.instrument_uic, p.position_open_price, p.position_close_price
FROM order_execution e
LEFT JOIN turbo_data_position p ON p.position_id = e.position_id
WHERE e.sent_at >= $1 AND e.sent_at < $2 AND e.purpose IN ('open', 'close')
"""

# The quote standing when each order left: the last bar of its turbo that ended by then.
QUOTES_SQL = f"""
SELECT q.order_id, b.bid_close, b.ask_close
FROM unnest($1::text[], $2::int[], $3::timestamptz[]) AS q(order_id, uic, at)
JOIN price_instrument i ON i.uic = q.uic AND i.role = 'turbo'
JOIN LATERAL (
    SELECT bid_close, ask_close FROM price_bar_10s b
    WHERE b.instrument_id = i.id AND b.ts <= q.at - interval '10 seconds'
      AND b.ts > q.at - interval '{QUOTE_WITHIN_SECONDS + 10} seconds'
    ORDER BY b.ts DESC LIMIT 1
) b ON true
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
    requests = await fetch(REQUESTS_SQL, start, end)
    return {
        "signals": [dict(row) for row in signals], "orders": [dict(row) for row in orders],
        "quotes": [dict(row) for row in quotes], "requests": [dict(row) for row in requests],
    }


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


def measure(rows: dict, clock: TradingClock) -> dict:
    """What WATA's orders took and cost, and the values a backtest would be given from them."""
    signals = rows.get("signals") or []
    from_bar = _spread([_seconds(row["decided_at"], row["signal_timestamp"]) for row in signals])
    from_receipt = _spread([_seconds(row["decided_at"], row["received_timestamp"]) for row in signals])
    orders = rows.get("orders") or []
    order_seconds = _spread([_seconds(row["accepted_at"], row["sent_at"]) for row in orders], 3)
    quotes = {str(row["order_id"]): row for row in rows.get("quotes") or []}
    paid: dict[str, list[float]] = {"open": [], "close": []}
    for row in orders:
        quote = quotes.get(str(row["order_id"]))
        if quote is None or row.get("outcome") != "confirmed":
            continue
        if row["purpose"] == "open" and row["position_open_price"] is not None and quote["ask_close"] is not None:
            paid["open"].append(row["position_open_price"] - quote["ask_close"])
        elif row["purpose"] == "close" and row["position_close_price"] is not None and quote["bid_close"] is not None:
            paid["close"].append(quote["bid_close"] - row["position_close_price"])
    every = paid["open"] + paid["close"]
    slippage = {
        purpose: {**_spread(values, 4), "median_ticks": round(median(values) / TICK, 2) if values else None}
        for purpose, values in (("buy", paid["open"]), ("sell", paid["close"]), ("all", every))
    }
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
    if slippage["all"]["count"] >= LEAST_MEASURES:
        suggested["slippage"] = round(max(0.0, slippage["all"]["median"]), 3)
    return {
        "signals": len(signals),
        "orders": len(orders),
        "requests": len(requests),
        # From the signal's own time (a script's bar close, TradingView's alert) and from the web server's receipt.
        "signal_delay_seconds": {"from_signal": from_bar, "from_receipt": from_receipt},
        "order_seconds": order_seconds,
        # Euros a turbo worse than the quote recorded when the order left (positive: worse).
        "slippage": slippage,
        "refusals": {
            "share_percent": round(refused / len(requests) * 100, 1) if requests else None,
            "by_hour": [{"hour": hour, "requests": counted[0], "refused": counted[1]} for hour, counted in sorted(by_hour.items())],
            "codes": dict(sorted(codes.items(), key=lambda item: -item[1])),
        },
        "suggested": suggested,
        "least": LEAST_MEASURES,
    }
