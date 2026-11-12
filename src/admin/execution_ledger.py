"""
What each of WATA's real trades lost between its signal and its fills: the execution ledger.

For each closed position, from what the services recorded of it (``turbo_data_position``, ``order_execution``,
``order_request``, ``signal_journal``, ``signal_alert_time``, ``ta_signal``) and the turbo's recorded quotes
(``price_bar_10s``):

- its times: the signal's way to WATA (from TradingView's alert, or from the engine's bar close), WATA's decision,
  Saxo's acceptance and Saxo's fill, and for its sale the attempts between the first and the one Saxo took;
- its costs, in points of the position's result (a cost is positive):
  - delay: how much dearer the turbo's recorded ask was when the purchase left than when the signal was raised;
  - entry slippage: the price paid above the ask WATA sized the order on (a quote up to 30 s old: Saxo fills at
    its live price);
  - exit slippage: the price the sale was decided on (Saxo's bid, or the index's estimate between quotes) above
    the price it was sold at, over the price paid;
  - the spread of the recorded quote when the purchase left, which the result pays once and is shown apart.

Pure, but for the SQL it reads with (``read_rows``, given a fetch that answers an absent table with no rows). It
is the admin's only: nothing of it is in a backtest's code digest (src/backtest/figures.py ``_CODE``).
"""

import math
import re
from datetime import date, datetime, timedelta, timezone
from statistics import mean, median

from src.admin.analysis import exit_group
from src.backtest.execution_calibration import QUOTES_SQL
from src.trade.index_estimate import BETWEEN_QUOTES

# The core of a trade: its position, its purchase and its sale as WATA recorded them, and the signal it came from.
POSITIONS_SQL = """
SELECT p.position_id, p.action, p.instrument_uic, p.instrument_name, p.position_open_price, p.position_close_price,
       p.position_total_performance_percent, p.position_close_reason, p.execution_time_open, p.execution_time_close,
       buy.sent_at AS buy_sent_at, buy.accepted_at AS buy_accepted_at,
       asked.price_seen AS buy_price_seen,
       j.signal_uuid, j.signal_timestamp, j.received_timestamp,
       first_sale.requested_at AS first_sale_at,
       sale.requested_at AS sale_requested_at, sale.price_seen AS sale_price_seen, sale.reason AS sale_reason,
       sold.accepted_at AS sale_accepted_at
FROM turbo_data_position p
LEFT JOIN LATERAL (
    SELECT sent_at, accepted_at FROM order_execution e
    WHERE e.position_id = p.position_id AND e.purpose = 'open' ORDER BY e.sent_at LIMIT 1
) buy ON true
LEFT JOIN LATERAL (
    SELECT price_seen FROM order_request r
    WHERE r.order_id = p.order_id AND r.operation = 'place' AND r.purpose = 'open' AND r.outcome = 'accepted'
    ORDER BY r.requested_at DESC LIMIT 1
) asked ON true
LEFT JOIN LATERAL (
    SELECT signal_uuid, signal_timestamp, received_timestamp FROM signal_journal s
    WHERE s.position_id = p.position_id ORDER BY s.decided_at LIMIT 1
) j ON true
LEFT JOIN LATERAL (
    SELECT requested_at FROM order_request r
    WHERE r.position_id = p.position_id AND r.operation = 'place' AND r.purpose = 'close'
    ORDER BY r.requested_at LIMIT 1
) first_sale ON true
LEFT JOIN LATERAL (
    SELECT requested_at, price_seen, reason FROM order_request r
    WHERE r.position_id = p.position_id AND r.operation = 'place' AND r.purpose = 'close' AND r.outcome = 'accepted'
    ORDER BY r.requested_at DESC LIMIT 1
) sale ON true
LEFT JOIN LATERAL (
    SELECT accepted_at FROM order_execution e
    WHERE e.position_id = p.position_id AND e.purpose = 'close' ORDER BY e.sent_at DESC LIMIT 1
) sold ON true
WHERE p.position_status = 'Closed' AND p.execution_time_open >= $1 AND p.execution_time_open < $2
ORDER BY p.execution_time_open
"""

# TradingView's alert moment ({{timenow}}), journaled since v0.85.0: its ``signal_timestamp`` is the bar's open.
ALERTS_SQL = "SELECT signal_uuid, alert_timestamp FROM signal_alert_time WHERE signal_uuid = ANY($1::text[])"
# The engine's own signals: their bar closed then (their ``signal_timestamp`` says it to the second).
ENGINE_SQL = "SELECT signal_uuid, bar_closed_at FROM ta_signal WHERE signal_uuid = ANY($1::text[])"

TRADINGVIEW = "TradingView"
ENGINE = "WATA's engine"
UNKNOWN_SOURCE = "Not known"
ON_SAXOS_QUOTE = "Saxo's quote"
ON_THE_INDEX = "The index between quotes"
# "<exit>; US100 between quotes: <estimate> from <Saxo's bid>, <age> s" (src/trade/index_estimate.py).
_ESTIMATE = re.compile(rf"{BETWEEN_QUOTES}: (-?[0-9.]+) from (-?[0-9.]+)")

# The days read when none are asked: as the measure of the orders reads them (src/admin/backtest_requests.py).
LEDGER_DAYS = 92
# The longest period asked.
MOST_DAYS = 366

# The measures, in the order the pages show them.
SECONDS = ("signal_to_received", "received_to_order", "order_to_accepted", "accepted_to_fill", "sale_attempts",
           "sale_to_fill")
COSTS = ("delay_percent", "entry_slippage_percent", "exit_slippage_percent")


async def read_rows(fetch, start: datetime, end: datetime) -> dict:
    """
    The rows the ledger is made of, of the positions opened between two moments, through ``fetch`` (a coroutine
    taking SQL and its arguments, which answers a table not created yet with no rows).
    """
    positions = [dict(row) for row in await fetch(POSITIONS_SQL, start, end)]
    uuids = sorted({str(row["signal_uuid"]) for row in positions if row.get("signal_uuid")})
    alerts = [dict(row) for row in await fetch(ALERTS_SQL, uuids)] if uuids else []
    engine = [dict(row) for row in await fetch(ENGINE_SQL, uuids)] if uuids else []
    keys, uics, moments = [], [], []
    for row in positions:
        if row.get("instrument_uic") is None:
            continue
        for kind, moment in (("signal", _signal_moment(row, alerts, engine)), ("buy", row.get("buy_sent_at"))):
            if moment is not None:
                keys.append(f"{row['position_id']}|{kind}")
                uics.append(int(row["instrument_uic"]))
                moments.append(moment)
    quotes = [dict(row) for row in await fetch(QUOTES_SQL, keys, uics, moments)] if keys else []
    return {"positions": positions, "alerts": alerts, "engine": engine, "quotes": quotes}


def _signal_moment(row: dict, alerts: list[dict], engine: list[dict]):
    """When the signal was raised: the engine's bar close, TradingView's alert, else nothing known."""
    uuid = row.get("signal_uuid")
    if not uuid:
        return None
    closed = next((item["bar_closed_at"] for item in engine if item.get("signal_uuid") == uuid), None)
    if closed is not None:
        return closed
    return next((item["alert_timestamp"] for item in alerts if item.get("signal_uuid") == uuid), None)


def _seconds(later, earlier) -> float | None:
    if later is None or earlier is None:
        return None
    return round((later - earlier).total_seconds(), 3)


def _number(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _percent(part: float | None, whole: float | None) -> float | None:
    if part is None or whole is None or whole <= 0:
        return None
    return round(100 * part / whole, 3)


def decided_price(row: dict) -> tuple[float | None, str | None]:
    """The price the sale was decided on, and on what: the index's estimate when its reason says so, else Saxo's bid."""
    reason = row.get("sale_reason") or row.get("position_close_reason") or ""
    found = _ESTIMATE.search(reason) if BETWEEN_QUOTES in reason else None
    if found:
        return _number(found.group(1)), ON_THE_INDEX
    seen = _number(row.get("sale_price_seen"))
    return seen, (ON_SAXOS_QUOTE if seen is not None else None)


def trade_of(row: dict, alerts: dict, engine: dict, quotes: dict) -> dict:
    """One position's times and costs. ``alerts``, ``engine`` by signal; ``quotes`` by ``<position>|<signal|buy>``."""
    uuid = row.get("signal_uuid")
    bar_closed = engine.get(uuid) if uuid else None
    alert = alerts.get(uuid) if uuid else None
    source = ENGINE if bar_closed is not None else TRADINGVIEW if alert is not None else UNKNOWN_SOURCE
    raised = bar_closed if bar_closed is not None else alert
    paid, sold = _number(row.get("position_open_price")), _number(row.get("position_close_price"))
    asked = _number(row.get("buy_price_seen"))
    at_signal = quotes.get(f"{row['position_id']}|signal") or {}
    at_buy = quotes.get(f"{row['position_id']}|buy") or {}
    ask_then, ask_now, bid_now = (_number(at_signal.get("ask_close")), _number(at_buy.get("ask_close")),
                                  _number(at_buy.get("bid_close")))
    decided, decided_on = decided_price(row)
    return {
        "position_id": str(row["position_id"]),
        "action": row.get("action"),
        "instrument_name": row.get("instrument_name"),
        "opened_at": row.get("execution_time_open"),
        "closed_at": row.get("execution_time_close"),
        "result_percent": _number(row.get("position_total_performance_percent")),
        "exit_group": exit_group(row.get("position_close_reason")),
        "source": source,
        "decided_on": decided_on,
        "signal_to_received": _seconds(row.get("received_timestamp"), raised),
        "received_to_order": _seconds(row.get("buy_sent_at"), row.get("received_timestamp")),
        "order_to_accepted": _seconds(row.get("buy_accepted_at"), row.get("buy_sent_at")),
        # Saxo's clock against WATA's: the two are not the same clock.
        "accepted_to_fill": _seconds(row.get("execution_time_open"), row.get("buy_accepted_at")),
        "sale_attempts": _seconds(row.get("sale_requested_at"), row.get("first_sale_at")),
        "sale_to_fill": _seconds(row.get("execution_time_close"), row.get("sale_requested_at")),
        "delay_percent": _percent(None if ask_now is None or ask_then is None else ask_now - ask_then, ask_then),
        "entry_slippage_percent": _percent(None if paid is None or asked is None else paid - asked, asked),
        "exit_slippage_percent": _percent(None if decided is None or sold is None else decided - sold, paid),
        "spread_percent": _percent(None if ask_now is None or bid_now is None else ask_now - bid_now, ask_now),
        "price_paid": paid, "ask_decided": asked, "price_sold": sold, "price_decided": decided,
    }


def _figures(values) -> dict:
    kept = sorted(value for value in values if value is not None)
    if not kept:
        return {"count": 0, "median": None, "mean": None, "p90": None, "sum": None}
    return {
        "count": len(kept),
        "median": round(median(kept), 3),
        "mean": round(mean(kept), 3),
        "p90": round(kept[min(len(kept) - 1, math.ceil(0.9 * len(kept)) - 1)], 3),
        "sum": round(sum(kept), 3),
    }


def _cost_of(trade: dict) -> float | None:
    """What a trade lost to execution, in points of its result: the costs known of it, summed."""
    known = [trade[name] for name in COSTS if trade.get(name) is not None]
    return round(sum(known), 3) if known else None


def _by(trades: list[dict], key: str) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for trade in trades:
        groups.setdefault(trade.get(key) or UNKNOWN_SOURCE, []).append(trade)
    return [
        {
            "group": name,
            "trades": len(members),
            "result_percent": _figures(trade.get("result_percent") for trade in members),
            "cost_percent": _figures(trade.get("cost_percent") for trade in members),
            **{measure: _figures(trade.get(measure) for trade in members)
               for measure in ("signal_to_received", "entry_slippage_percent", "exit_slippage_percent")},
        }
        for name, members in sorted(groups.items(), key=lambda item: -len(item[1]))
    ]


def ledger(rows: dict) -> dict:
    """Every trade's times and costs, and their figures: in all, by exit, by what an exit was decided on, by source."""
    alerts = {str(row["signal_uuid"]): row.get("alert_timestamp") for row in rows.get("alerts") or []
              if row.get("alert_timestamp") is not None}
    engine = {str(row["signal_uuid"]): row.get("bar_closed_at") for row in rows.get("engine") or []
              if row.get("bar_closed_at") is not None}
    quotes = {str(row["order_id"]): row for row in rows.get("quotes") or []}
    trades = []
    for row in rows.get("positions") or []:
        trade = trade_of(row, alerts, engine, quotes)
        trade["cost_percent"] = _cost_of(trade)
        trades.append(trade)
    return {
        "trades": trades,
        "summary": {
            "trades": len(trades),
            **{name: _figures(trade.get(name) for trade in trades) for name in (*SECONDS, *COSTS, "spread_percent")},
            "cost_percent": _figures(trade.get("cost_percent") for trade in trades),
            "result_percent": _figures(trade.get("result_percent") for trade in trades),
        },
        "by_exit": _by(trades, "exit_group"),
        "by_decided_on": _by([trade for trade in trades if trade.get("decided_on")], "decided_on"),
        "by_source": _by(trades, "source"),
    }


class LedgerPeriodRefused(ValueError):
    """A period the ledger is not read over: in words."""


async def period_ledger(queries, timezone_name: str, first_day: date | None = None, last_day: date | None = None) -> dict:
    """
    The ledger of the positions opened between two days of the trading timezone (both included): the last
    LEDGER_DAYS days to today when none are given, a year at most.
    """
    from src.backtest.clock import TradingClock

    clock = TradingClock(timezone_name)
    last_day = last_day or datetime.now(timezone.utc).astimezone(clock.zone).date()
    first_day = first_day or last_day - timedelta(days=LEDGER_DAYS - 1)
    if last_day < first_day or (last_day - first_day).days >= MOST_DAYS:
        raise LedgerPeriodRefused("A period of a year at most, its first day before its last")
    start, end = clock.day_bounds(first_day)[0], clock.day_bounds(last_day)[1]
    rows = await queries.execution_ledger_rows(datetime.fromtimestamp(start, timezone.utc),
                                               datetime.fromtimestamp(end, timezone.utc))
    return {"from": first_day.isoformat(), "to": last_day.isoformat(), **ledger(rows)}
