"""
Read-only PostgreSQL access for the admin interface.

Figures the trading rules also compute ("today" counters, cooldown) come from
the same managers the trader uses, so the dashboard shows what the rules see.
History and reporting queries are specific to the admin and bucket days in the
configured trading timezone.
"""

import asyncio
import json
import logging
import math
from datetime import date, datetime, timedelta
from typing import Any

import pytz

from src.admin.analysis import summarise_exits
from src.admin.errors import DatabaseUnavailable
from src.admin.live_breakdowns import live_breakdowns
from src.admin.order_requests import refusal_runs, requests_by_order
from src.admin.prices import BAR_COLUMNS as PRICE_BAR_COLUMNS
from src.admin.prices import INTERVALS as PRICE_INTERVALS
from src.admin.prices import Run as PriceRun
from src.admin.prices import interval_served
from src.admin.ta_monitor import DELIVERY_STEPS
from src.admin.weekday_hour import opened_weekday_hour, stats_by_weekday_hour
from src.ta_engine.signals import CLOSE_WITH_ENTRY
from src.ta_engine.source import ROW_COLUMNS as ENGINE_ROW_COLUMNS
from src.database.postgres import ORDER_REQUEST_COLUMNS, TA_TICK_COLUMNS, AsyncDbPositionManager, AsyncDbRiskStateManager
from src.message_helper import DEFAULT_MILESTONES_EUR, build_daily_trading_report_payload

logger = logging.getLogger(__name__)

MAX_ROWS = 2000
# What a backtest of real signals, or the measure of the turbo, may read at once: years of trading.
MAX_REPLAYED_ROWS = 20000

# The worst result a position reached, read beside it from position_worst (since v0.48.0): only in a query
# FROM turbo_data_position without an alias.
_WORST = (
    "(SELECT worst_percent FROM position_worst WHERE position_worst.position_id = turbo_data_position.position_id)"
    " AS position_min_performance_percent"
)

_POSITION_COLUMNS = f"""
    action, position_id, position_amount, position_open_price, position_close_price,
    position_close_reason, position_profit_loss, position_total_open_price,
    position_total_close_price, position_total_performance_percent,
    position_max_performance_percent, {_WORST}, position_status, position_kind,
    execution_time_open, execution_time_close, order_id, related_order_id,
    instrument_name, instrument_symbol, instrument_uic, instrument_currency
"""

_ORDER_COLUMNS = """
    action, buy_sell, order_id, order_amount, order_type, order_kind, order_time,
    related_order_id, position_id, instrument_name, instrument_symbol, instrument_uic,
    instrument_price, instrument_currency, order_cost
"""

# When a position last changed: its close, or its open while it is still open.
_POSITION_ACTIVITY_TIME = "COALESCE(execution_time_close, execution_time_open)"

# What became of an order (order_execution), absent for the orders placed before it was kept.
_ORDER_EXECUTION_COLUMNS = (
    "purpose", "sent_at", "accepted_at", "confirmed_at", "gave_up_at", "lookups", "outcome",
    "cancel_accepted", "detail", "recorded_by",
)
# The position an order gave, or closed.
_ORDER_POSITION_COLUMNS = (
    "position_id", "position_status", "position_amount", "position_open_price", "position_close_price",
    "position_profit_loss", "position_total_performance_percent", "execution_time_open",
    "execution_time_close", "position_close_reason",
)
# A signal of the TA engine, and what the trader made of it once sent.
_TA_SIGNAL_COLUMNS = (
    "id", "rule_id", "rule_name", "script_id", "script_version", "asset", "side", "bars", "bar_time",
    "bar_closed_at", "alert_key", "alert_title", "alert_message", "action", "indice", "confidence", "mode",
    "raised_at", "outcome", "sent_at", "http_status", "signal_uuid", "detail",
)
_TA_TRADER_COLUMNS = ("decided_at", "outcome", "reason", "position_id")
# Milliseconds between two moments, and the start of the bar of time a moment falls in (epoch seconds, from
# the bucket's length given as $3): the Engine monitor's figures, in SQL so that a month of runs is never read.
_MS = "(extract(epoch FROM {}) * 1000)::float8"
_BUCKET = "extract(epoch FROM date_bin($3::interval, {}, TIMESTAMPTZ '2000-01-01 00:00:00+00'))::bigint"
# The ticks judged by their time: neither the daily restart, nor a load of a rule, nor a check of a continued run
# (src/ta_engine/engine.py), each a whole run more by design.
_STEADY = "loads = 0 AND NOT restarted AND figures->'checks' IS NULL"
# What the continued runs' checks against a whole run were, by rule (the ticks' figures.checks, c): a check
# differed when the whole run gave something else, or failed where the continued run went on.
_DIFFERED = "(c->>'same' = 'false' OR c->>'outcome' = 'failed')"
_CHECKS = f"""c->>'rule_id' AS rule_id, c->>'mode' AS mode, c->>'runner' AS runner, count(*) AS checks,
       count(*) FILTER (WHERE {_DIFFERED}) AS differed,
       max((c->>'ms')::int) AS check_max_ms,
       (array_agg(coalesce(c->'differences'->>0, c->>'detail') ORDER BY tick_at DESC) FILTER (WHERE {_DIFFERED}))[1]
           AS last_difference"""

# The signal an order came from.
_ORDER_SIGNAL_COLUMNS = ("signal_uuid", "signal_timestamp", "received_timestamp", "decided_at")
# The signal behind a buy Saxo refused: what the trader made of it too.
_REFUSED_SIGNAL_COLUMNS = (*_ORDER_SIGNAL_COLUMNS, "outcome", "reason")
# The requests about one position a page shows at most (a sell Saxo kept refusing is retried about once a second).
MAX_POSITION_REQUESTS = 500
# What the research tools (Claude's, and the AI agent's) read of a request to Saxo's order endpoint: never the
# request's body nor Saxo's answer's, which carry the order whole and its account.
REQUEST_BODIES = ("request_body", "response_body")
_ATTEMPT_COLUMNS = tuple(column for column in ORDER_REQUEST_COLUMNS if column not in REQUEST_BODIES)

# An order with what became of it, its position and its signal. What a sell
# order could not say of its instrument is taken from the position it closed.
_LINKED_ORDER_COLUMNS = ", ".join([
    "COALESCE(o.action, p.action) AS action",
    "o.buy_sell, o.order_id, o.order_amount, o.order_type, o.order_kind, o.order_time",
    "o.related_order_id, o.position_id",
    "COALESCE(o.instrument_name, p.instrument_name) AS instrument_name",
    "COALESCE(o.instrument_symbol, p.instrument_symbol) AS instrument_symbol",
    "COALESCE(o.instrument_uic, p.instrument_uic) AS instrument_uic",
    "o.instrument_price",
    "COALESCE(o.instrument_currency, p.instrument_currency) AS instrument_currency",
    "o.order_cost",
    *(f"e.{column}" for column in _ORDER_EXECUTION_COLUMNS),
    *(f"p.{column} AS position__{column}" for column in _ORDER_POSITION_COLUMNS),
    *(f"j.{column} AS signal__{column}" for column in _ORDER_SIGNAL_COLUMNS),
])
_LINKED_ORDER_TABLES = f"""
    turbo_data_order o
    LEFT JOIN order_execution e ON e.order_id = o.order_id
    LEFT JOIN turbo_data_position p ON p.position_id = o.position_id
    LEFT JOIN (
        SELECT DISTINCT ON (order_id) order_id, {", ".join(_ORDER_SIGNAL_COLUMNS)}
        FROM signal_journal
        WHERE order_id IS NOT NULL
        ORDER BY order_id, decided_at
    ) j ON j.order_id = o.order_id
"""


class LazyDatabase:
    """
    Connects on first use, so the admin still starts (and can report it) while
    PostgreSQL is down.
    """

    def __init__(self, conn_mgr):
        self._conn_mgr = conn_mgr
        self._connect_lock = asyncio.Lock()

    async def _call(self, method: str, query: str, *args):
        try:
            async with self._connect_lock:
                await self._conn_mgr.connect()
            return await getattr(self._conn_mgr, method)(query, *args)
        except Exception as e:
            logger.error(f"PostgreSQL {method} failed: {e}")
            raise DatabaseUnavailable(str(e)) from e

    async def fetch(self, query: str, *args):
        return await self._call("fetch", query, *args)

    async def fetchrow(self, query: str, *args):
        return await self._call("fetchrow", query, *args)

    async def fetchval(self, query: str, *args):
        return await self._call("fetchval", query, *args)

    async def execute(self, query: str, *args):
        return await self._call("execute", query, *args)

    async def copy_from_query(self, query: str, *args, output, **options):
        try:
            async with self._connect_lock:
                await self._conn_mgr.connect()
            return await self._conn_mgr.copy_from_query(query, *args, output=output, **options)
        except Exception as e:
            logger.error(f"PostgreSQL copy failed: {e}")
            raise DatabaseUnavailable(str(e)) from e

    async def close(self):
        await self._conn_mgr.close()


def _clean_row(record) -> dict:
    """A JSON-safe dict: NaN and infinities cannot be serialised, so they become None."""
    row = dict(record)
    for key, value in row.items():
        if isinstance(value, float) and not math.isfinite(value):
            row[key] = None
    return row


def _raised_signal(record) -> dict:
    """A row of ``ta_signal`` with what the trader made of it when it was sent (``trader``, None otherwise)."""
    signal = _clean_row(record)
    trader = {key[len("trader__"):]: signal.pop(key) for key in list(signal) if key.startswith("trader__")}
    signal["trader"] = trader if trader["decided_at"] is not None else None
    return signal


def _linked_order(record) -> dict:
    """A row of the linked orders: its position and its signal as dicts, None when it has none."""
    row = _clean_row(record)
    order = {key: value for key, value in row.items() if "__" not in key}
    for link in ("position", "signal"):
        prefix = f"{link}__"
        linked = {key[len(prefix):]: value for key, value in row.items() if key.startswith(prefix)}
        order[link] = linked if any(value is not None for value in linked.values()) else None
    return order


def _unlinked_order(record) -> dict:
    """An order read alone, in the shape of a linked one that has nothing known of it."""
    return {
        **_clean_row(record),
        **{column: None for column in _ORDER_EXECUTION_COLUMNS},
        "position": None,
        "signal": None,
    }


def json_safe(value):
    """``value`` with every non-finite float, at any depth, replaced by None."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value


def _finite(value):
    """``value``, or None where it is a float that is not a number."""
    return None if isinstance(value, float) and not math.isfinite(value) else value


def _json_value(value):
    """A jsonb column as asyncpg returns it without a codec (text), as Python values."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def _percentiles(row: dict, name: str) -> dict:
    """``<name>_ms``, a median and a 95 % made in one sort, as ``<name>_p50_ms`` and ``<name>_p95_ms``."""
    values = row.pop(f"{name}_ms", None) or [None, None]
    row[f"{name}_p50_ms"], row[f"{name}_p95_ms"] = values[0], values[1]
    return row


def _is_missing_table(error: DatabaseUnavailable) -> bool:
    return "does not exist" in str(error)


def _count(trades: list[dict], predicate) -> int:
    return sum(1 for trade in trades if predicate(trade.get("performance_percent") or 0))


def _profit(trades: list[dict]) -> float:
    return sum(trade.get("profit_loss") or 0 for trade in trades)


def _average_percent(trades: list[dict]) -> float | None:
    if not trades:
        return None
    return round(sum(trade.get("performance_percent") or 0 for trade in trades) / len(trades), 2)


def _group(trades: list[dict], key) -> dict:
    groups: dict = {}
    for trade in trades:
        groups.setdefault(key(trade), []).append(trade)
    return groups


def summarise_trades(trades: list[dict], profit_before: float = 0.0) -> dict:
    """
    Aggregate closed trades (ordered by close time) into the reporting payload.

    ``profit_before`` is the profit realised before the first trade, so the
    cumulative curve of a date range continues the all-time one.
    """
    daily = []
    cumulative = profit_before
    for day, day_trades in _group(trades, lambda trade: trade["day_date"]).items():
        day_profit = _profit(day_trades)
        cumulative += day_profit
        daily.append({
            "day_date": day,
            "trade_count": len(day_trades),
            "win_count": _count(day_trades, lambda percent: percent > 0),
            "loss_count": _count(day_trades, lambda percent: percent < 0),
            "profit_loss": round(day_profit, 2),
            "cumulative_profit_loss": round(cumulative, 2),
            # Compounded like the trader's own day performance.
            "performance_percent": AsyncDbPositionManager._calculate_final_percentage(
                [(trade.get("performance_percent"),) for trade in day_trades]
            ),
        })

    by_action = [
        {
            "action": action,
            "trade_count": len(action_trades),
            "win_count": _count(action_trades, lambda percent: percent > 0),
            "loss_count": _count(action_trades, lambda percent: percent < 0),
            "profit_loss": round(_profit(action_trades), 2),
            "average_percent": _average_percent(action_trades),
        }
        for action, action_trades in sorted(_group(trades, lambda trade: trade["action"] or "unknown").items())
    ]

    by_instrument = sorted(
        (
            {
                "instrument_name": instrument_name,
                "action": action,
                "trade_count": len(instrument_trades),
                "profit_loss": round(_profit(instrument_trades), 2),
            }
            for (instrument_name, action), instrument_trades in _group(
                trades, lambda trade: (trade["instrument_name"] or "unknown", trade["action"] or "unknown")
            ).items()
        ),
        key=lambda entry: entry["profit_loss"],
    )

    win_count = _count(trades, lambda percent: percent > 0)
    best_day = max(daily, key=lambda day: day["profit_loss"], default=None)
    worst_day = min(daily, key=lambda day: day["profit_loss"], default=None)
    return {
        "summary": {
            "trade_count": len(trades),
            "win_count": win_count,
            "loss_count": _count(trades, lambda percent: percent < 0),
            "win_rate": round(100.0 * win_count / len(trades), 2) if trades else None,
            "profit_loss": round(_profit(trades), 2),
            "trading_days": len(daily),
            "winning_days": sum(1 for day in daily if day["profit_loss"] > 0),
            "average_trade_percent": _average_percent(trades),
            "best_day": best_day and {"day_date": best_day["day_date"], "profit_loss": best_day["profit_loss"]},
            "worst_day": worst_day and {"day_date": worst_day["day_date"], "profit_loss": worst_day["profit_loss"]},
        },
        "daily": daily,
        "by_action": by_action,
        "by_instrument": by_instrument,
    }


class AdminQueries:
    def __init__(self, db, timezone: str):
        self.db = db
        self.timezone = timezone
        self._positions = AsyncDbPositionManager(db)
        self._risk_state = AsyncDbRiskStateManager(db)

    async def ping(self) -> bool:
        return await self.db.fetchval("SELECT 1") == 1

    async def copy_csv(self, query: str, *args, output, timeout: float | None = None) -> bool:
        """
        The rows of ``query`` as CSV with a header, written to ``output`` (a
        coroutine function given each chunk). False when its table does not
        exist yet: a service that creates it has not run with this version.
        """
        try:
            await self.db.copy_from_query(query, *args, output=output, format="csv", header=True, timeout=timeout)
        except DatabaseUnavailable as e:
            if _is_missing_table(e):
                return False
            raise
        return True

    # ── today, as the trading rules see it ──

    async def day_overview(self) -> dict:
        stats = await self._positions.get_stats_of_the_day()
        general = stats["general"][0] if stats["general"] else {}
        return {
            "trade_count": await self._positions.get_today_trade_count(),
            "closed_count": general.get("position_count", 0),
            "performance_percent": await self._positions.get_percent_of_the_day(),
            "profit_loss": round(general.get("sum_profit") or 0, 2),
            "best_percent": general.get("max_percent"),
            "worst_percent": general.get("min_percent"),
            "by_action": [_clean_row(row) for row in stats["detail_stats"]],
        }

    async def last_loss_timestamp(self):
        return await self._risk_state.get_last_loss_timestamp()

    async def entries_paused_since(self):
        return await self._risk_state.get_entries_paused_since()

    async def entries_paused_by_guard(self):
        """When the account guard paused entries, while its pause holds (src/trade/account_guard.py)."""
        return await self._risk_state.get_entries_paused_by_guard()

    async def today_closed_results(self) -> list[float]:
        """The day's closed results in the order they closed, as the trader counts its breakers."""
        return await self._positions.get_today_closed_results()

    async def _fetch_optional(self, query: str, *args) -> list:
        """
        Rows of a table the trading services create at their first start with
        this version: until then it does not exist, which reads as empty.
        """
        try:
            return await self.db.fetch(query, *args)
        except DatabaseUnavailable as e:
            if _is_missing_table(e):
                return []
            raise

    async def price_recorder_status(self) -> dict | None:
        """
        The last ten-second bucket the price recorder lived through (ts is its
        start), None when it never ran: its tables only exist once it has.
        """
        rows = await self._fetch_optional(
            "SELECT ts, feed_ok, instruments, bars FROM price_recorder_coverage ORDER BY ts DESC LIMIT 1"
        )
        return dict(rows[0]) if rows else None

    # ── recorded prices (written by the price recorder) ──

    _PRICE_INSTRUMENT_COLUMNS = (
        "i.id, i.uic, i.asset_type, i.symbol, i.description, i.currency, i.role, i.indice, "
        "i.underlying_uic, i.direction, i.barrier, i.issuer"
    )

    async def price_instruments(self, start: datetime, end: datetime) -> list[dict]:
        """
        The instruments that have a bar in [start, end), each with the quote of
        its last bar there (``last_t`` is the start of that bar): the index
        first, then the turbos by direction and knock-out level. The bars are
        reached through their primary key, one probe per instrument, never
        scanned.
        """
        rows = await self._fetch_optional(
            f"""
            SELECT {self._PRICE_INSTRUMENT_COLUMNS},
                   extract(epoch FROM latest.ts)::bigint AS last_t,
                   latest.bid_close AS last_bid, latest.ask_close AS last_ask
            FROM price_instrument i
            CROSS JOIN LATERAL (
                SELECT b.ts, b.bid_close, b.ask_close FROM price_bar_10s b
                WHERE b.instrument_id = i.id AND b.ts >= $1 AND b.ts < $2
                ORDER BY b.ts DESC
                LIMIT 1
            ) latest
            WHERE i.last_seen_at >= $1 AND i.first_seen_at < $2
            ORDER BY (i.role <> 'underlying'), i.indice, i.direction, i.barrier, i.id
            """,
            start, end,
        )
        return [_clean_row(row) for row in rows]

    async def price_runs(self, start: datetime, end: datetime, limit: int) -> list[PriceRun]:
        """
        The bars of [start, end) as stretches that follow one another without
        a break, by instrument and in time order: where one ends and the next
        begins, the instrument had no bar. Every bar of the period is read,
        once: bars ten seconds apart all have the same "time less ten seconds
        per bar before it", which is what they are grouped by. The bid a
        stretch ended on is then read by its key.
        """
        rows = await self._fetch_optional(
            f"""
            WITH runs AS (
                SELECT instrument_id, min(ts) AS first_ts, max(ts) AS last_ts,
                       count(*)::int AS bars,
                       sum(updates)::bigint AS updates,
                       (count(*) FILTER (WHERE ask_close IS NULL))::int AS without_ask,
                       max(delayed_minutes)::int AS delayed_minutes,
                       max(bid_high) AS high
                FROM (
                    SELECT instrument_id, ts, updates, ask_close, delayed_minutes, bid_high,
                           extract(epoch FROM ts)::bigint
                               - {PRICE_INTERVALS[0]} * row_number() OVER (PARTITION BY instrument_id ORDER BY ts) AS run
                    FROM price_bar_10s
                    WHERE ts >= $1 AND ts < $2
                ) bars
                GROUP BY instrument_id, run
            )
            SELECT runs.instrument_id,
                   extract(epoch FROM runs.first_ts)::bigint AS first,
                   extract(epoch FROM runs.last_ts)::bigint AS last,
                   runs.bars, runs.updates, runs.without_ask, runs.delayed_minutes, runs.high,
                   ending.bid_close AS last_bid
            FROM runs
            JOIN price_bar_10s ending ON ending.instrument_id = runs.instrument_id AND ending.ts = runs.last_ts
            ORDER BY runs.instrument_id, runs.first_ts
            LIMIT $3
            """,
            start, end, limit,
        )
        return [PriceRun(*(_finite(row[column]) for column in PriceRun._fields)) for row in rows]

    async def price_volumes(self, start: datetime, end: datetime) -> dict[int, float]:
        """
        What Saxo counted as traded on each instrument in [start, end): its
        total of each day as last received, the days added up. Empty until the
        recorder is asked to keep it (``record_volume``).
        """
        rows = await self._fetch_optional(
            """
            SELECT instrument_id, sum(volume) AS volume
            FROM (
                SELECT instrument_id, (array_agg(day_volume ORDER BY ts DESC))[1] AS volume
                FROM price_volume
                WHERE ts >= $1 AND ts < $2
                GROUP BY instrument_id, (ts AT TIME ZONE $3)::date
            ) days
            GROUP BY instrument_id
            """,
            start, end, self.timezone,
        )
        return {row["instrument_id"]: row["volume"] for row in rows if _finite(row["volume"]) is not None}

    async def price_assets(self, limit: int, since: datetime | None = None, instrument_id: int | None = None) -> list[dict]:
        """
        Every recorded instrument that still has bars, all time: when it was
        first and last in the recording set, and the start of its first bar and
        the start and quote of its last one. The index comes first, then the
        instruments quoted last. Each instrument's bars are reached through
        their primary key, from a day before it was first seen to a day after it
        was last seen (the recorder notes it when it refreshes its set, which
        can be after its first bar and before its last): one or two monthly
        partitions for a turbo, never a scan. An instrument whose months were
        dropped by the retention has no bar left and is not listed. ``since``
        keeps those in the recording set since then; ``instrument_id``, one.
        """
        if instrument_id is not None:
            seen, args = "WHERE i.id = $2", (limit, instrument_id)
        elif since is not None:
            seen, args = "WHERE i.last_seen_at >= $2", (limit, since)
        else:
            seen, args = "", (limit,)
        rows = await self._fetch_optional(
            f"""
            SELECT {self._PRICE_INSTRUMENT_COLUMNS}, i.first_seen_at, i.last_seen_at,
                   extract(epoch FROM first_bar.ts)::bigint AS first_t,
                   extract(epoch FROM last_bar.ts)::bigint AS last_t,
                   last_bar.bid_close AS last_bid, last_bar.ask_close AS last_ask
            FROM price_instrument i
            CROSS JOIN LATERAL (
                SELECT b.ts, b.bid_close, b.ask_close FROM price_bar_10s b
                WHERE b.instrument_id = i.id
                  AND b.ts >= i.first_seen_at - interval '1 day'
                  AND b.ts < i.last_seen_at + interval '1 day'
                ORDER BY b.ts DESC
                LIMIT 1
            ) last_bar
            CROSS JOIN LATERAL (
                SELECT b.ts FROM price_bar_10s b
                WHERE b.instrument_id = i.id
                  AND b.ts >= i.first_seen_at - interval '1 day'
                  AND b.ts <= last_bar.ts
                ORDER BY b.ts
                LIMIT 1
            ) first_bar
            {seen}
            ORDER BY (i.role <> 'underlying'), last_bar.ts DESC, i.indice, i.direction, i.barrier, i.id
            LIMIT $1
            """,
            *args,
        )
        return [_clean_row(row) for row in rows]

    async def price_index_of(self, indice: str, start: datetime, end: datetime) -> dict | None:
        """The index recorded for ``indice`` that has a bar in [start, end): the one quoted last, if several."""
        rows = await self._fetch_optional(
            f"""
            SELECT {self._PRICE_INSTRUMENT_COLUMNS}
            FROM price_instrument i
            CROSS JOIN LATERAL (
                SELECT b.ts FROM price_bar_10s b
                WHERE b.instrument_id = i.id AND b.ts >= $2 AND b.ts < $3
                ORDER BY b.ts DESC
                LIMIT 1
            ) latest
            WHERE i.role = 'underlying' AND i.indice = $1 AND i.last_seen_at >= $2 AND i.first_seen_at < $3
            ORDER BY latest.ts DESC, i.id
            LIMIT 1
            """,
            indice, start, end,
        )
        return _clean_row(rows[0]) if rows else None

    async def price_volume_days(self, instrument_id: int, start: datetime, end: datetime) -> dict[str, float]:
        """
        What Saxo counted as traded on one instrument each day of [start, end),
        by day of the trading timezone: its total of the day as last received.
        """
        rows = await self._fetch_optional(
            """
            SELECT (ts AT TIME ZONE $4)::date AS day, (array_agg(day_volume ORDER BY ts DESC))[1] AS volume
            FROM price_volume
            WHERE instrument_id = $1 AND ts >= $2 AND ts < $3
            GROUP BY 1
            ORDER BY 1
            """,
            instrument_id, start, end, self.timezone,
        )
        return {row["day"].isoformat(): row["volume"] for row in rows if _finite(row["volume"]) is not None}

    async def price_instrument(self, instrument_id: int) -> dict | None:
        rows = await self._fetch_optional(
            f"SELECT {self._PRICE_INSTRUMENT_COLUMNS}, i.first_seen_at, i.last_seen_at FROM price_instrument i WHERE i.id = $1",
            instrument_id,
        )
        return _clean_row(rows[0]) if rows else None

    # The tables of ten-second bars a chart can be read from: a request chooses
    # between these names, it never writes one into a statement.
    _BAR_TABLES = {"saxo": "price_bar_10s", "capital": "capital_bar_10s"}

    async def _bars(self, provider: str, instrument_id: int, start: datetime, end: datetime, interval: int,
                    limit: int) -> tuple[list[list], int | None]:
        """
        Bars of one instrument in [start, end), oldest first, as rows of
        PRICE_BAR_COLUMNS, and the start of the last ten-second bar they hold.
        ``interval`` is ten seconds, as recorded, or longer, up to a day: the
        bars that begin in each bucket are then merged into one (first open,
        highest high, lowest low, last close).
        """
        if not interval_served(interval):
            raise ValueError(f"Unsupported interval: {interval}")
        table = self._BAR_TABLES[provider]
        if interval == PRICE_INTERVALS[0]:
            query = f"""
                SELECT extract(epoch FROM ts)::bigint AS t,
                       bid_open, bid_high, bid_low, bid_close, ask_open, ask_high, ask_low, ask_close, updates,
                       extract(epoch FROM ts)::bigint AS through
                FROM {table}
                WHERE instrument_id = $1 AND ts >= $2 AND ts < $3
                ORDER BY ts
                LIMIT $4
            """
        else:
            # The interval is a whole number, checked above: written into the
            # statement, where a parameter would leave its type to be guessed.
            query = f"""
                SELECT (floor(extract(epoch FROM ts) / {interval}) * {interval})::bigint AS t,
                       (array_agg(bid_open ORDER BY ts ASC))[1] AS bid_open,
                       max(bid_high) AS bid_high,
                       min(bid_low) AS bid_low,
                       (array_agg(bid_close ORDER BY ts DESC))[1] AS bid_close,
                       (array_agg(ask_open ORDER BY ts ASC) FILTER (WHERE ask_open IS NOT NULL))[1] AS ask_open,
                       max(ask_high) AS ask_high,
                       min(ask_low) AS ask_low,
                       (array_agg(ask_close ORDER BY ts DESC) FILTER (WHERE ask_close IS NOT NULL))[1] AS ask_close,
                       sum(updates)::int AS updates,
                       extract(epoch FROM max(ts))::bigint AS through
                FROM {table}
                WHERE instrument_id = $1 AND ts >= $2 AND ts < $3
                GROUP BY 1
                ORDER BY 1
                LIMIT $4
            """
        rows = await self._fetch_optional(query, instrument_id, start, end, limit)
        through = rows[-1].get("through") if rows else None
        return [json_safe([row[column] for column in PRICE_BAR_COLUMNS]) for row in rows], through

    async def price_bars(self, instrument_id: int, start: datetime, end: datetime, interval: int, limit: int) -> list[list]:
        """Bars of one recorded instrument (the price recorder's), as ``_bars`` reads them."""
        rows, _ = await self._bars("saxo", instrument_id, start, end, interval, limit)
        return rows

    async def capital_bars(self, instrument_id: int, start: datetime, end: datetime, interval: int,
                           limit: int) -> tuple[list[list], int | None]:
        """
        Bars of one instrument of the Capital.com feed, and the start of the
        last ten-second bar they hold: a live chart adds only what came after it.
        """
        return await self._bars("capital", instrument_id, start, end, interval, limit)

    # The four prices of each side. Which side is asked for chooses between these
    # names; nothing of the request itself is ever written into a statement.
    _PRICE_SIDE_COLUMNS = {
        "bid": ("bid_open", "bid_high", "bid_low", "bid_close"),
        "ask": ("ask_open", "ask_high", "ask_low", "ask_close"),
    }

    async def _series(self, provider: str, instrument_id: int, start: datetime, end: datetime, side: str,
                      limit: int) -> list[tuple]:
        if side not in self._PRICE_SIDE_COLUMNS:
            raise ValueError(f"Unsupported side: {side}")
        table = self._BAR_TABLES[provider]
        opened, high, low, closed = self._PRICE_SIDE_COLUMNS[side]
        # The bid is never missing (NOT NULL); the ask can be.
        present = "" if side == "bid" else f" AND {opened} IS NOT NULL AND {high} IS NOT NULL AND {low} IS NOT NULL AND {closed} IS NOT NULL"
        rows = await self._fetch_optional(
            f"""
            SELECT extract(epoch FROM ts)::bigint AS t,
                   {opened} AS open, {high} AS high, {low} AS low, {closed} AS close, updates
            FROM {table}
            WHERE instrument_id = $1 AND ts >= $2 AND ts < $3{present}
            ORDER BY ts
            LIMIT $4
            """,
            instrument_id, start, end, limit,
        )
        return [(row["t"], row["open"], row["high"], row["low"], row["close"], row["updates"]) for row in rows]

    async def price_series(self, instrument_id: int, start: datetime, end: datetime, side: str, limit: int) -> list[tuple]:
        """
        The ten-second bars of one side of an instrument in [start, end), oldest
        first, as (epoch seconds, open, high, low, close, quote changes). Bars
        without that side (an instrument with no ask) are left out. Read a page
        at a time: pass the moment after the last row as ``start`` for the next.
        """
        return await self._series("saxo", instrument_id, start, end, side, limit)

    async def capital_series(self, instrument_id: int, start: datetime, end: datetime, side: str, limit: int) -> list[tuple]:
        """``price_series`` for an instrument of the Capital.com feed."""
        return await self._series("capital", instrument_id, start, end, side, limit)

    async def capital_engine_rows(self, instrument_id: int, start: datetime, end: datetime, limit: int) -> list[tuple]:
        """
        The ten-second bars of an instrument of the Capital.com feed in [start, end), oldest first,
        both sides, as the TA engine reads them (``ROW_COLUMNS``). Read a page at a time: pass the
        moment after the last row as ``start`` for the next.
        """
        rows = await self._fetch_optional(
            """
            SELECT extract(epoch FROM ts)::bigint AS ts, bid_open, bid_high, bid_low, bid_close,
                   ask_open, ask_high, ask_low, ask_close, updates
            FROM capital_bar_10s
            WHERE instrument_id = $1 AND ts >= $2 AND ts < $3
            ORDER BY ts
            LIMIT $4
            """,
            instrument_id, start, end, limit,
        )
        return [tuple(row[name] for name in ENGINE_ROW_COLUMNS) for row in rows]

    async def price_coverage(self, start: datetime, end: datetime) -> list[dict]:
        """One row per ten seconds the recorder lived through in [start, end), in time order."""
        rows = await self._fetch_optional(
            "SELECT ts, feed_ok FROM price_recorder_coverage WHERE ts >= $1 AND ts < $2 ORDER BY ts", start, end,
        )
        return [dict(row) for row in rows]

    async def price_trades(self, instrument: dict, start: datetime, end: datetime, limit: int = 500) -> list[dict]:
        """
        The positions that were open at some point of [start, end) on a
        recorded turbo, or on any recorded turbo of the indice when the
        instrument is its index.
        """
        if instrument["role"] == "underlying":
            which = "instrument_uic IN (SELECT uic FROM price_instrument WHERE indice = $1 AND role = 'turbo')"
            target = instrument["indice"]
        else:
            which = "instrument_uic = $1"
            target = instrument["uic"]
        rows = await self._fetch_optional(
            f"""
            SELECT {_POSITION_COLUMNS}
            FROM turbo_data_position
            WHERE {which}
              AND execution_time_open < $3
              AND COALESCE(execution_time_close, now()) >= $2
            ORDER BY execution_time_open ASC, position_id ASC
            LIMIT {min(max(limit, 1), MAX_ROWS)}
            """,
            target, start, end,
        )
        return [_clean_row(row) for row in rows]

    # ── what a backtest can be run on (src/backtest) ──

    async def backtest_turbo_days(self, indice: str, first_day: date, last_day: date) -> list[dict]:
        """
        By local day and direction, how many turbos of the indice the recorder
        kept bars of, and the first and last of those bars. Each turbo's bars
        are reached through their primary key: two probes per turbo and day.
        """
        rows = await self._fetch_optional(
            """
            WITH days AS (
                SELECT d::date AS day,
                       (d::timestamp AT TIME ZONE $2) AS day_start,
                       ((d + interval '1 day')::timestamp AT TIME ZONE $2) AS day_end
                FROM generate_series($3::date, $4::date, interval '1 day') AS d
            )
            SELECT days.day, i.direction, count(*) AS turbos, min(b.first_ts) AS first_ts, max(b.last_ts) AS last_ts
            FROM days
            JOIN price_instrument i
              ON i.role = 'turbo' AND i.indice = $1 AND i.barrier IS NOT NULL
             AND i.first_seen_at < days.day_end AND i.last_seen_at >= days.day_start - interval '1 day'
            CROSS JOIN LATERAL (
                SELECT (SELECT ts FROM price_bar_10s
                        WHERE instrument_id = i.id AND ts >= days.day_start AND ts < days.day_end
                        ORDER BY ts ASC LIMIT 1) AS first_ts,
                       (SELECT ts FROM price_bar_10s
                        WHERE instrument_id = i.id AND ts >= days.day_start AND ts < days.day_end
                        ORDER BY ts DESC LIMIT 1) AS last_ts
            ) b
            WHERE b.first_ts IS NOT NULL
            GROUP BY days.day, i.direction
            ORDER BY days.day, i.direction
            """,
            indice, self.timezone, first_day, last_day,
        )
        return [_clean_row(row) for row in rows]

    async def backtest_recorder_days(self, first_day: date, last_day: date) -> list[dict]:
        """By local day, how many ten seconds the price recorder listened to quotes in."""
        start, end = self._local_bounds(first_day, last_day)
        rows = await self._fetch_optional(
            """
            SELECT (ts AT TIME ZONE $3)::date AS day, count(*) FILTER (WHERE feed_ok) AS listened,
                   min(ts) AS first_ts, max(ts) AS last_ts
            FROM price_recorder_coverage
            WHERE ts >= $1 AND ts < $2
            GROUP BY 1
            ORDER BY 1
            """,
            start, end, self.timezone,
        )
        return [_clean_row(row) for row in rows]

    async def backtest_asset_days(self, provider: str, instrument_id: int, first_day: date, last_day: date) -> list[dict]:
        """By local day, the ten-second bars of one instrument of the recorder or of the Capital.com feed."""
        table = {"saxo": "price_bar_10s", "capital": "capital_bar_10s"}[provider]
        start, end = self._local_bounds(first_day, last_day)
        rows = await self._fetch_optional(
            f"""
            SELECT (ts AT TIME ZONE $4)::date AS day, count(*) AS bars
            FROM {table}
            WHERE instrument_id = $1 AND ts >= $2 AND ts < $3
            GROUP BY 1
            ORDER BY 1
            """,
            instrument_id, start, end, self.timezone,
        )
        return [_clean_row(row) for row in rows]

    async def last_balance(self) -> float | None:
        """The account's value at the last daily report: what a backtest's money starts from."""
        rows = await self._fetch_optional(
            "SELECT total_value FROM account_balance_history WHERE total_value IS NOT NULL ORDER BY date_day DESC LIMIT 1"
        )
        return float(rows[0]["total_value"]) if rows else None

    def _local_bounds(self, first_day: date, last_day: date) -> tuple[datetime, datetime]:
        zone = pytz.timezone(self.timezone)
        start = zone.localize(datetime.combine(first_day, datetime.min.time()))
        end = zone.localize(datetime.combine(last_day + timedelta(days=1), datetime.min.time()))
        return start, end

    # ── the Capital.com feed (src/capital_feed) ──

    async def capital_assets(self, epic: str | None = None) -> list[dict]:
        """
        Every instrument of the Capital.com feed that has a bar, with its first
        and last one (the last with its quote), the one quoted last first.
        Each is reached through the key of the bars, never scanned.
        """
        where, args = ("WHERE i.epic = $1", (epic,)) if epic is not None else ("", ())
        rows = await self._fetch_optional(
            f"""
            SELECT i.id, i.epic, i.label, i.name, i.instrument_type, i.currency, i.decimals,
                   i.first_seen_at, i.last_seen_at,
                   extract(epoch FROM first_bar.ts)::bigint AS first_t,
                   extract(epoch FROM last_bar.ts)::bigint AS last_t,
                   last_bar.bid_close AS last_bid, last_bar.ask_close AS last_ask
            FROM capital_instrument i
            CROSS JOIN LATERAL (
                SELECT b.ts, b.bid_close, b.ask_close FROM capital_bar_10s b
                WHERE b.instrument_id = i.id ORDER BY b.ts DESC LIMIT 1
            ) last_bar
            CROSS JOIN LATERAL (
                SELECT b.ts FROM capital_bar_10s b WHERE b.instrument_id = i.id ORDER BY b.ts LIMIT 1
            ) first_bar
            {where}
            ORDER BY last_bar.ts DESC, i.epic
            """,
            *args,
        )
        return [_clean_row(row) for row in rows]

    async def capital_instrument(self, epic: str) -> dict | None:
        """One instrument of the feed with its first and last bar; None without a bar."""
        rows = await self.capital_assets(epic)
        return rows[0] if rows else None

    async def capital_opening_hours(self, epic: str) -> dict | None:
        """The opening hours Capital.com gave for the instrument, as it gave them."""
        rows = await self._fetch_optional("SELECT opening_hours FROM capital_instrument WHERE epic = $1", epic)
        return _json_value(rows[0]["opening_hours"]) if rows else None

    async def service_heartbeats(self) -> dict[str, dict]:
        """What the trader and the position monitor last said of themselves, by service; none before they ran."""
        rows = await self._fetch_optional(
            "SELECT service, beat_at, started_at, version, figures FROM service_heartbeat"
        )
        beats = {}
        for row in rows:
            beat = dict(row)
            beat["figures"] = _json_value(beat.get("figures")) or {}
            beats[beat.pop("service")] = beat
        return beats

    async def capital_feed_state(self) -> dict | None:
        """What the feed last wrote of itself; None when it never ran."""
        rows = await self._fetch_optional(
            "SELECT updated_at, started_at, version, environment, state, detail, figures FROM capital_feed_state WHERE id = 1"
        )
        if not rows:
            return None
        row = dict(rows[0])
        row["figures"] = _json_value(row.get("figures")) or {}
        return row

    async def capital_coverage(self, start: datetime, end: datetime) -> list[dict]:
        """One row per ten seconds the feed lived through in [start, end), in time order."""
        rows = await self._fetch_optional(
            "SELECT ts, feed_ok FROM capital_coverage WHERE ts >= $1 AND ts < $2 ORDER BY ts", start, end,
        )
        return [dict(row) for row in rows]

    async def capital_volume(self, instrument_id: int, start: datetime, end: datetime, interval: int,
                             limit: int) -> list[list]:
        """
        Capital.com's volume of the minutes of [start, end), added up by
        ``interval`` (a minute or a multiple of one), as [epoch seconds, volume].
        """
        if interval not in PRICE_INTERVALS or interval < 60:
            raise ValueError(f"Unsupported interval for Capital.com's volume: {interval}")
        rows = await self._fetch_optional(
            f"""
            SELECT (floor(extract(epoch FROM ts) / {interval}) * {interval})::bigint AS t, sum(volume) AS volume
            FROM capital_minute
            WHERE instrument_id = $1 AND ts >= $2 AND ts < $3 AND volume IS NOT NULL
            GROUP BY 1
            ORDER BY 1
            LIMIT $4
            """,
            instrument_id, start, end, limit,
        )
        return [[row["t"], _finite(row["volume"])] for row in rows]

    async def capital_volume_days(self, instrument_id: int, start: datetime, end: datetime) -> dict[str, float]:
        """Capital.com's volume of each day of [start, end), by day of the trading timezone."""
        rows = await self._fetch_optional(
            """
            SELECT (ts AT TIME ZONE $4)::date AS day, sum(volume) AS volume
            FROM capital_minute
            WHERE instrument_id = $1 AND ts >= $2 AND ts < $3 AND volume IS NOT NULL
            GROUP BY 1
            ORDER BY 1
            """,
            instrument_id, start, end, self.timezone,
        )
        return {row["day"].isoformat(): row["volume"] for row in rows if _finite(row["volume"]) is not None}

    async def index_trades(self, indice: str, start: datetime, end: datetime, *, every_position: bool,
                           limit: int = 500) -> list[dict]:
        """
        The positions that were open at some point of [start, end) and were
        opened by a signal of ``indice``; every position when ``every_position``
        (WATA trades that indice alone, and positions older than the signal
        journal are then of it too).
        """
        rows = await self._fetch_optional(
            f"""
            SELECT {_POSITION_COLUMNS}
            FROM turbo_data_position
            WHERE ($4::bool OR position_id IN (
                      SELECT position_id FROM signal_journal WHERE indice = $1 AND position_id IS NOT NULL))
              AND execution_time_open < $3
              AND COALESCE(execution_time_close, now()) >= $2
            ORDER BY execution_time_open ASC, position_id ASC
            LIMIT {min(max(limit, 1), MAX_ROWS)}
            """,
            indice, start, end, every_position,
        )
        return [_clean_row(row) for row in rows]

    # ── the TA engine's signals (written by src/ta_engine, absent until it ran) ──

    async def ta_signals(
        self,
        start: date | None = None,
        end: date | None = None,
        rule_id: str | None = None,
        limit: int = 500,
    ) -> list[dict]:
        """
        The signals the TA engine raised between two days (both included), newest
        first, each with what the trader made of it when it was sent (``trader``).
        """
        args: list[Any] = []
        conditions = self._date_range_conditions("s.raised_at", start, end, args)
        if rule_id is not None:
            args.append(rule_id)
            conditions.append(f"s.rule_id = ${len(args)}")
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = await self._fetch_optional(
            f"""
            SELECT {", ".join(f"s.{column}" for column in _TA_SIGNAL_COLUMNS)},
                   {", ".join(f"j.{column} AS trader__{column}" for column in _TA_TRADER_COLUMNS)}
            FROM ta_signal s
            LEFT JOIN signal_journal j ON j.signal_uuid = s.signal_uuid
            {where}
            ORDER BY s.raised_at DESC, s.id DESC
            LIMIT {min(max(limit, 1), MAX_ROWS)}
            """,
            *args,
        )
        return [_raised_signal(row) for row in rows]

    async def rule_signals(self, rule_id: str, start: date, end: date, limit: int = MAX_REPLAYED_ROWS) -> list[dict]:
        """
        What a paper run of a rule replays: the signals it raised with an action, between two days (both
        included), as ``ta_signals`` gives them, oldest first. The newest ``limit`` at most (``MAX_REPLAYED_ROWS``):
        as many coming back means there were more, and the oldest are the ones left out.
        """
        args: list[Any] = [rule_id]
        conditions = ["s.rule_id = $1", "s.action IS NOT NULL", *self._date_range_conditions("s.raised_at", start, end, args)]
        rows = await self._fetch_optional(
            f"""
            SELECT {", ".join(f"s.{column}" for column in _TA_SIGNAL_COLUMNS)},
                   {", ".join(f"j.{column} AS trader__{column}" for column in _TA_TRADER_COLUMNS)}
            FROM ta_signal s
            LEFT JOIN signal_journal j ON j.signal_uuid = s.signal_uuid
            WHERE {' AND '.join(conditions)}
            ORDER BY s.raised_at DESC, s.id DESC
            LIMIT {min(max(limit, 1), MAX_REPLAYED_ROWS)}
            """,
            *args,
        )
        return [_raised_signal(row) for row in reversed(rows)]

    async def ta_other_signals(self, indice: str, day: date) -> list[dict]:
        """
        The signals the trader received for ``indice`` on ``day``, oldest first,
        except those the TA engine sent: TradingView's, for setting beside WATA's.
        """
        args: list[Any] = [indice]
        conditions = ["j.indice = $1", *self._date_range_conditions("j.decided_at", day, day, args)]
        columns = "j.signal_uuid, j.action, j.indice, j.confidence, j.signal_timestamp, j.received_timestamp, " \
                  "j.decided_at, j.outcome, j.reason, j.position_id"
        query = f"""
            SELECT {columns}
            FROM signal_journal j
            WHERE {' AND '.join(conditions)} {{wata}}
            ORDER BY j.signal_timestamp ASC NULLS LAST, j.signal_uuid ASC
            LIMIT {MAX_ROWS}
        """
        try:
            rows = await self.db.fetch(
                query.format(wata="AND NOT EXISTS (SELECT 1 FROM ta_signal s WHERE s.signal_uuid = j.signal_uuid)"), *args,
            )
        except DatabaseUnavailable as e:
            if not _is_missing_table(e):
                raise
            # The engine never ran: none of them is WATA's.
            rows = await self._fetch_optional(query.format(wata=""), *args)
        return [_clean_row(row) for row in rows]

    async def ta_engine_state(self) -> dict | None:
        """What the TA engine last wrote of itself; None when it never ran."""
        rows = await self._fetch_optional(
            "SELECT updated_at, started_at, version, state, detail, figures FROM ta_engine_state WHERE id = 1"
        )
        if not rows:
            return None
        row = _clean_row(rows[0])
        row["figures"] = _json_value(row.get("figures")) or {}
        return row

    async def ta_rule_states(self) -> dict[str, dict]:
        """Each rule as the TA engine runs it, by rule id."""
        rows = await self._fetch_optional(
            """
            SELECT rule_id, updated_at, state, detail, mode, script_version, bars, last_bar_time,
                   last_alert_at, figures
            FROM ta_rule_state
            """
        )
        states = {}
        for record in rows:
            row = _clean_row(record)
            row["figures"] = _json_value(row.get("figures")) or {}
            states[row.pop("rule_id")] = row
        return states

    # ── what the TA engine measured of itself (ta_engine_tick, ta_rule_run: src/ta_engine/monitoring.py) ──

    async def ta_monitor_recent(self, since: datetime, *, slow_tick_ms: int) -> dict:
        """
        The engine's last tick, and what its ticks, its runs and the live signals were since ``since``:
        the Engine monitor's checks, and health's. Empty before the engine measured anything.
        """
        last = await self._fetch_optional(
            f"SELECT {', '.join(TA_TICK_COLUMNS)}, (SELECT min(tick_at) FROM ta_engine_tick) AS kept_since "
            "FROM ta_engine_tick ORDER BY tick_at DESC LIMIT 1"
        )
        ticks = await self._fetch_optional(
            f"""
            SELECT count(*) AS ticks,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY milliseconds::float8) AS tick_p50_ms,
                   max(milliseconds) AS tick_max_ms,
                   -- A tick that loaded a rule or checked a continued run takes a whole run more, by design.
                   max(milliseconds) FILTER (WHERE {_STEADY}) AS steady_tick_max_ms,
                   count(*) FILTER (WHERE milliseconds > $2 AND {_STEADY}) AS slow_ticks,
                   -- The engine's first tick after a start may find the feed stopped with it (a deploy).
                   max({_MS.format("tick_at - covered_until")}) FILTER (WHERE figures->'first' IS NULL) AS feed_lag_max_ms,
                   max(memory_used_mb) AS memory_used_max_mb,
                   max(memory_limit_mb) AS memory_limit_mb,
                   greatest(max(oom_kills) - min(oom_kills), 0) AS oom_kills,
                   sum(cardinality(deferred_rules)) AS deferred
            FROM ta_engine_tick
            WHERE tick_at >= $1
            """,
            since, slow_tick_ms,
        )
        runs = await self._fetch_optional(
            f"""
            SELECT rule_id, mode, runner,
                   count(*) AS runs,
                   count(*) FILTER (WHERE kind = 'load') AS loads,
                   max(milliseconds) FILTER (WHERE kind = 'append') AS append_max_ms,
                   max(milliseconds) FILTER (WHERE kind = 'load') AS load_max_ms,
                   count(*) FILTER (WHERE outcome = 'failed') AS failed,
                   count(*) FILTER (WHERE outcome = 'ended') AS ended,
                   (array_agg(detail ORDER BY tick_at DESC) FILTER (WHERE outcome <> 'ok'))[1] AS last_failure,
                   max({_MS.format("tick_at - bar_closed_at")}) FILTER (WHERE kind = 'append') AS age_max_ms,
                   max({_MS.format("done_at - bar_closed_at")}) FILTER (WHERE kind = 'append') AS delay_max_ms
            FROM ta_rule_run
            WHERE tick_at >= $1
            GROUP BY rule_id, mode, runner
            ORDER BY rule_id, runner
            """,
            since,
        )
        deferred = await self._fetch_optional(
            """
            SELECT waited.rule_id, count(*) AS ticks
            FROM ta_engine_tick, unnest(deferred_rules) AS waited(rule_id)
            WHERE tick_at >= $1
            GROUP BY waited.rule_id
            ORDER BY waited.rule_id
            """,
            since,
        )
        checks = await self._fetch_optional(
            f"SELECT {_CHECKS} FROM ta_engine_tick, jsonb_array_elements(figures->'checks') AS c "
            "WHERE tick_at >= $1 GROUP BY 1, 2, 3 ORDER BY 1, 3",
            since,
        )
        signals = await self._fetch_optional(
            """
            SELECT rule_id, outcome, CASE WHEN outcome IN ('refused', 'failed') THEN detail END AS detail,
                   count(*) AS signals
            FROM ta_signal
            WHERE mode = 'live' AND raised_at >= $1
            GROUP BY 1, 2, 3
            ORDER BY 1, 2, 3
            """,
            since,
        )
        last_tick = _clean_row(last[0]) if last else None
        return {
            "last_tick": last_tick,
            "kept_since": last_tick.pop("kept_since") if last_tick else None,
            "ticks": _clean_row(ticks[0]) if ticks else {},
            "runs": [_clean_row(row) for row in runs],
            "deferred": [_clean_row(row) for row in deferred],
            "checks": [_clean_row(row) for row in checks],
            "signals": [_clean_row(row) for row in signals],
        }

    async def ta_monitor_period(
        self,
        start: datetime,
        end: datetime,
        bucket_seconds: int,
        rule_id: str | None = None,
        *,
        signal_age_seconds: int,
        slow_tick_ms: int,
    ) -> dict:
        """
        A period of what the engine measured, by bar of time (``bucket_seconds``) and by rule, with its
        signals' delivery (from the bar's close to the position) and results: the Engine monitor's page.
        """
        def rule(column: str, args: list) -> str:
            if rule_id is None:
                return ""
            args.append(rule_id)
            return f" AND {column} = ${len(args)}"

        bucket = timedelta(seconds=bucket_seconds)
        args = [start, end, bucket, slow_tick_ms]
        ticks = await self._fetch_optional(
            f"""
            SELECT {_BUCKET.format("tick_at")} AS t,
                   count(*) AS ticks,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY milliseconds::float8) AS tick_p50_ms,
                   max(milliseconds) AS tick_max_ms,
                   count(*) FILTER (WHERE milliseconds > $4 AND {_STEADY}) AS slow,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY {_MS.format("tick_at - covered_until")}) AS feed_lag_p50_ms,
                   max({_MS.format("tick_at - covered_until")}) AS feed_lag_max_ms,
                   max(live_rss_mb) AS live_rss_mb, max(shadow_rss_mb) AS shadow_rss_mb, max(engine_rss_mb) AS engine_rss_mb,
                   max(memory_used_mb) AS memory_used_mb, max(memory_limit_mb) AS memory_limit_mb,
                   bool_or(restarted) AS restarted,
                   min(runner_starts) AS runner_starts_min, max(runner_starts) AS runner_starts_max,
                   sum(cardinality(deferred_rules)) AS deferred
            FROM ta_engine_tick
            WHERE tick_at >= $1 AND tick_at < $2
            GROUP BY 1
            ORDER BY 1
            """,
            *args,
        )
        args = [start, end, bucket]
        runs_by_bucket = await self._fetch_optional(
            f"""
            SELECT {_BUCKET.format("tick_at")} AS t,
                   count(*) FILTER (WHERE kind = 'append') AS runs,
                   count(*) FILTER (WHERE kind = 'load') AS loads,
                   count(*) FILTER (WHERE outcome <> 'ok') AS failed,
                   percentile_cont(ARRAY[0.5, 0.95]) WITHIN GROUP (ORDER BY milliseconds::float8)
                       FILTER (WHERE kind = 'append') AS run_ms,
                   max(milliseconds) FILTER (WHERE kind = 'append') AS run_max_ms,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY {_MS.format("done_at - bar_closed_at")})
                       FILTER (WHERE kind = 'append') AS delay_p50_ms,
                   max({_MS.format("done_at - bar_closed_at")}) FILTER (WHERE kind = 'append') AS delay_max_ms
            FROM ta_rule_run
            WHERE tick_at >= $1 AND tick_at < $2{rule("rule_id", args)}
            GROUP BY 1
            ORDER BY 1
            """,
            *args,
        )
        args = [start, end, bucket, CLOSE_WITH_ENTRY]
        signals_by_bucket = await self._fetch_optional(
            f"""
            SELECT {_BUCKET.format("raised_at")} AS t,
                   count(*) FILTER (WHERE outcome = 'sent') AS sent,
                   count(*) FILTER (
                       WHERE mode = 'live' AND outcome IN ('refused', 'failed', 'sending') AND detail IS DISTINCT FROM $4
                   ) AS not_sent,
                   count(*) FILTER (WHERE mode = 'shadow') AS shadow
            FROM ta_signal
            WHERE raised_at >= $1 AND raised_at < $2{rule("rule_id", args)}
            GROUP BY 1
            ORDER BY 1
            """,
            *args,
        )
        args = [start, end]
        rules = await self._fetch_optional(
            f"""
            SELECT rule_id,
                   count(*) FILTER (WHERE kind = 'append') AS runs,
                   count(*) FILTER (WHERE kind = 'load') AS loads,
                   count(*) FILTER (WHERE kind = 'append' AND figures->>'continued' = 'true') AS continued,
                   count(*) FILTER (WHERE outcome = 'failed') AS failed,
                   count(*) FILTER (WHERE outcome = 'ended') AS ended,
                   percentile_cont(ARRAY[0.5, 0.95]) WITHIN GROUP (ORDER BY milliseconds::float8)
                       FILTER (WHERE kind = 'append') AS run_ms,
                   max(milliseconds) FILTER (WHERE kind = 'append') AS run_max_ms,
                   max(milliseconds) FILTER (WHERE kind = 'load') AS load_max_ms,
                   avg(pinets_ms::float8) FILTER (WHERE kind = 'append') AS pinets_avg_ms,
                   max(bars_held) AS bars_max,
                   sum(asks) AS asks,
                   percentile_cont(ARRAY[0.5, 0.95]) WITHIN GROUP (ORDER BY {_MS.format("done_at - bar_closed_at")})
                       FILTER (WHERE kind = 'append') AS delay_ms,
                   max({_MS.format("done_at - bar_closed_at")}) FILTER (WHERE kind = 'append') AS delay_max_ms,
                   max({_MS.format("tick_at - bar_closed_at")}) FILTER (WHERE kind = 'append') AS age_max_ms,
                   max(tick_at) AS last_run_at,
                   (array_agg(detail ORDER BY tick_at DESC) FILTER (WHERE outcome <> 'ok'))[1] AS last_failure
            FROM ta_rule_run
            WHERE tick_at >= $1 AND tick_at < $2{rule("rule_id", args)}
            GROUP BY rule_id
            ORDER BY rule_id
            """,
            *args,
        )
        args = [start, end]
        deferred = await self._fetch_optional(
            f"""
            SELECT waited.rule_id, count(*) AS ticks
            FROM ta_engine_tick, unnest(deferred_rules) AS waited(rule_id)
            WHERE tick_at >= $1 AND tick_at < $2{rule("waited.rule_id", args)}
            GROUP BY waited.rule_id
            ORDER BY waited.rule_id
            """,
            *args,
        )
        args = [start, end]
        of_rule = rule("c->>'rule_id'", args)
        checks = await self._fetch_optional(
            f"SELECT {_CHECKS} FROM ta_engine_tick, jsonb_array_elements(figures->'checks') AS c "
            f"WHERE tick_at >= $1 AND tick_at < $2{of_rule} GROUP BY 1, 2, 3 ORDER BY 1, 3",
            *args,
        )
        args = [start, end, float(signal_age_seconds)]
        signals = await self._fetch_optional(
            f"""
            SELECT rule_id, mode, outcome, CASE WHEN outcome IN ('refused', 'failed') THEN detail END AS detail,
                   count(*) AS signals,
                   count(*) FILTER (WHERE raised_at - bar_closed_at > make_interval(secs => $3)) AS late
            FROM ta_signal
            WHERE raised_at >= $1 AND raised_at < $2{rule("rule_id", args)}
            GROUP BY 1, 2, 3, 4
            ORDER BY 1, 2, 3, 4
            LIMIT {MAX_ROWS}
            """,
            *args,
        )
        args = [start, end]
        steps = ",\n".join(f"('{key}', {_MS.format('(' + moments + ')')})" for key, _, moments in DELIVERY_STEPS)
        delivery = await self._fetch_optional(
            f"""
            SELECT step, count(ms) AS signals,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY ms) AS p50,
                   percentile_cont(0.95) WITHIN GROUP (ORDER BY ms) AS p95,
                   max(ms) AS max
            FROM ta_signal s
            LEFT JOIN ta_rule_run r ON r.rule_id = s.rule_id AND r.tick_at = s.raised_at
            LEFT JOIN signal_journal j ON j.signal_uuid = s.signal_uuid
            LEFT JOIN order_execution e ON e.order_id = j.order_id
            CROSS JOIN LATERAL (VALUES
                {steps}
            ) AS steps(step, ms)
            WHERE s.mode = 'live' AND s.outcome = 'sent' AND s.raised_at >= $1 AND s.raised_at < $2{rule("s.rule_id", args)}
            GROUP BY step
            """,
            *args,
        )
        args = [start, end]
        trader = await self._fetch_optional(
            f"""
            SELECT j.outcome, CASE WHEN j.outcome IN ('refused', 'failed') THEN j.reason END AS reason, count(*) AS signals
            FROM ta_signal s
            LEFT JOIN signal_journal j ON j.signal_uuid = s.signal_uuid
            WHERE s.mode = 'live' AND s.outcome = 'sent' AND s.raised_at >= $1 AND s.raised_at < $2{rule("s.rule_id", args)}
            GROUP BY 1, 2
            ORDER BY 3 DESC
            LIMIT 50
            """,
            *args,
        )
        args = [start, end]
        results = await self._fetch_optional(
            f"""
            SELECT s.rule_id,
                   count(*) AS positions,
                   count(*) FILTER (WHERE p.position_status = 'Open') AS open,
                   count(*) FILTER (WHERE p.position_status = 'Closed' AND p.position_profit_loss > 0) AS won,
                   count(*) FILTER (WHERE p.position_status = 'Closed' AND p.position_profit_loss < 0) AS lost,
                   sum(p.position_profit_loss) FILTER (WHERE p.position_status = 'Closed') AS profit_loss,
                   avg(p.position_total_performance_percent) FILTER (WHERE p.position_status = 'Closed') AS average_percent
            FROM ta_signal s
            JOIN signal_journal j ON j.signal_uuid = s.signal_uuid
            JOIN turbo_data_position p ON p.position_id = j.position_id
            WHERE s.mode = 'live' AND s.raised_at >= $1 AND s.raised_at < $2{rule("s.rule_id", args)}
            GROUP BY s.rule_id
            ORDER BY s.rule_id
            """,
            *args,
        )
        kept = await self._fetch_optional("SELECT min(tick_at) AS kept_since FROM ta_engine_tick")
        return {
            "kept_since": kept[0]["kept_since"] if kept else None,
            "ticks": [_clean_row(row) for row in ticks],
            "runs_by_bucket": [_percentiles(_clean_row(row), "run") for row in runs_by_bucket],
            "signals_by_bucket": [_clean_row(row) for row in signals_by_bucket],
            "rules": [_percentiles(_percentiles(_clean_row(row), "run"), "delay") for row in rules],
            "deferred": [_clean_row(row) for row in deferred],
            "checks": [_clean_row(row) for row in checks],
            "signals": [_clean_row(row) for row in signals],
            "steps": [_clean_row(row) for row in delivery],
            "trader": [_clean_row(row) for row in trader],
            "results": [_clean_row(row) for row in results],
        }

    # ── live values, journal, balance (written by the trading services) ──

    async def live_values(self) -> dict[str, dict]:
        rows = await self._fetch_optional(
            "SELECT position_id, bid, performance_percent, observed_at FROM position_live_value"
        )
        return {row["position_id"]: _clean_row(row) for row in rows}

    async def open_positions(self) -> list[dict]:
        """
        Open positions with the price and performance the position monitor
        last saw, and the sells of each that Saxo did not take.
        """
        positions = (await self.list_positions(status="Open"))["items"]
        live = await self.live_values() if positions else {}
        refused = await self.sell_refusals([position["position_id"] for position in positions]) if positions else {}
        for position in positions:
            value = live.get(position["position_id"], {})
            position["live_bid"] = value.get("bid")
            position["live_performance_percent"] = value.get("performance_percent")
            position["live_observed_at"] = value.get("observed_at")
            position["sell_refusals"] = refused.get(position["position_id"])
        return positions

    async def sell_refusals(self, position_ids: list[str]) -> dict[str, dict]:
        """
        For each of these positions whose sell Saxo did not take: how many
        times, since when, until when, and its latest answer.
        """
        rows = await self._fetch_optional(
            """
            SELECT position_id,
                   COUNT(*) AS count,
                   MIN(requested_at) AS first_at,
                   MAX(requested_at) AS last_at,
                   (ARRAY_AGG(outcome ORDER BY requested_at DESC))[1] AS outcome,
                   (ARRAY_AGG(http_status ORDER BY requested_at DESC))[1] AS http_status,
                   (ARRAY_AGG(error_code ORDER BY requested_at DESC))[1] AS error_code,
                   (ARRAY_AGG(error_message ORDER BY requested_at DESC))[1] AS error_message
            FROM order_request
            WHERE position_id = ANY($1::text[])
              AND operation = 'place' AND purpose = 'close' AND outcome <> 'accepted'
            GROUP BY position_id
            """,
            [str(position_id) for position_id in position_ids],
        )
        return {row["position_id"]: _clean_row(row) for row in rows}

    async def signal_journal(self, start: date | None = None, end: date | None = None, limit: int = 200) -> list[dict]:
        args: list[Any] = []
        conditions = self._date_range_conditions("decided_at", start, end, args)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = await self._fetch_optional(
            f"""
            SELECT signal_uuid, action, indice, confidence, signal_timestamp, received_timestamp,
                   decided_at, outcome, reason, position_id, order_id
            FROM signal_journal
            {where}
            ORDER BY decided_at DESC NULLS LAST, signal_uuid DESC
            LIMIT {min(max(limit, 1), MAX_ROWS)}
            """,
            *args,
        )
        return [_clean_row(row) for row in rows]

    async def journal_between(self, start: date | None = None, end: date | None = None) -> list[dict]:
        """Every signal the trader dealt with between two days (both included), oldest first: what a backtest replays."""
        args: list[Any] = []
        conditions = self._date_range_conditions("decided_at", start, end, args)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = await self._fetch_optional(
            f"""
            SELECT signal_uuid, action, indice, confidence, signal_timestamp, received_timestamp,
                   decided_at, outcome, reason, position_id
            FROM signal_journal
            {where}
            ORDER BY decided_at ASC NULLS LAST, signal_uuid ASC
            LIMIT {MAX_REPLAYED_ROWS}
            """,
            *args,
        )
        return [_clean_row(row) for row in rows]

    async def execution_rows(self, start: datetime, end: datetime) -> dict:
        """What WATA's orders took and cost between two moments: the rows src/backtest/execution_calibration.py measures."""
        from src.backtest.execution_calibration import read_rows

        return await read_rows(self._fetch_optional, start, end)

    async def closed_positions(self, start: date | None = None, end: date | None = None) -> list[dict]:
        """The closed positions opened between two days (both included), oldest first, with what each was bought and sold at."""
        args: list[Any] = []
        conditions = ["position_status = 'Closed'", "execution_time_open IS NOT NULL", "execution_time_close IS NOT NULL"]
        conditions += self._date_range_conditions("execution_time_open", start, end, args)
        rows = await self.db.fetch(
            f"""
            SELECT position_id, action, execution_time_open, execution_time_close,
                   position_open_price, position_close_price, position_total_performance_percent,
                   position_max_performance_percent, {_WORST}, position_close_reason,
                   instrument_name, instrument_uic
            FROM turbo_data_position
            WHERE {' AND '.join(conditions)}
            ORDER BY execution_time_open ASC, position_id ASC
            LIMIT {MAX_REPLAYED_ROWS}
            """,
            *args,
        )
        return [_clean_row(row) for row in rows]

    async def balance_history(self) -> list[dict]:
        rows = await self._fetch_optional(
            "SELECT date_day, total_value, recorded_at FROM account_balance_history ORDER BY date_day ASC"
        )
        return [_clean_row(row) for row in rows]

    # ── positions and orders ──

    def _date_range_conditions(self, column: str, start: date | None, end: date | None, args: list) -> list[str]:
        conditions = []
        if start is not None or end is not None:
            args.append(self.timezone)
            local_day = f"({column} AT TIME ZONE ${len(args)})::date"
            if start is not None:
                args.append(start)
                conditions.append(f"{local_day} >= ${len(args)}::date")
            if end is not None:
                args.append(end)
                conditions.append(f"{local_day} <= ${len(args)}::date")
        return conditions

    async def list_positions(
        self,
        status: str | None = None,
        start: date | None = None,
        end: date | None = None,
        limit: int = 500,
    ) -> dict:
        args: list[Any] = []
        conditions = self._date_range_conditions(_POSITION_ACTIVITY_TIME, start, end, args)
        if status is not None:
            args.append(status)
            conditions.append(f"position_status = ${len(args)}")
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        total = await self.db.fetchval(f"SELECT COUNT(*) FROM turbo_data_position {where}", *args)
        rows = await self.db.fetch(
            f"""
            SELECT {_POSITION_COLUMNS}
            FROM turbo_data_position
            {where}
            ORDER BY {_POSITION_ACTIVITY_TIME} DESC NULLS LAST, position_id DESC
            LIMIT {min(max(limit, 1), MAX_ROWS)}
            """,
            *args,
        )
        return {"items": [_clean_row(row) for row in rows], "total": total or 0}

    async def position_chart(self, position_id: str) -> dict | None:
        """
        What a position is drawn on: the recorder's id of its turbo (by its Uic, the newest the recorder named), and
        the signal that opened it (the journal's), with its moments. None for a position not recorded.
        """
        row = await self.db.fetchrow(
            "SELECT instrument_uic, execution_time_open, execution_time_close FROM turbo_data_position WHERE position_id = $1",
            position_id,
        )
        if row is None:
            return None
        instruments = await self._fetch_optional(
            "SELECT id FROM price_instrument WHERE uic = $1 ORDER BY last_seen_at DESC NULLS LAST, id DESC LIMIT 1",
            row["instrument_uic"],
        ) if row["instrument_uic"] is not None else []
        signals = await self._fetch_optional(
            """
            SELECT action, signal_timestamp, received_timestamp, decided_at FROM signal_journal
            WHERE position_id = $1 ORDER BY decided_at ASC NULLS LAST LIMIT 1
            """,
            position_id,
        )
        return {
            "price_instrument_id": instruments[0]["id"] if instruments else None,
            "opened_at": row["execution_time_open"],
            "closed_at": row["execution_time_close"],
            "signal": _clean_row(signals[0]) if signals else None,
        }

    async def get_position(self, position_id: str) -> dict | None:
        position = await self.db.fetchrow(
            f"SELECT {_POSITION_COLUMNS} FROM turbo_data_position WHERE position_id = $1",
            position_id,
        )
        if position is None:
            return None
        orders = await self._linked_orders(
            """
            WHERE o.position_id = $1 OR o.order_id = $2 OR o.order_id = ANY($3::text[])
            ORDER BY o.order_time ASC NULLS LAST, o.order_id ASC
            """,
            position_id,
            position["order_id"],
            position["related_order_id"] or [],
        )
        # Every request about it: its sells, the stops and cancellations on it, and the buy that opened it.
        where = "WHERE position_id = $1 OR order_id = $2"
        rows = await self._fetch_optional(
            f"""
            SELECT {", ".join(ORDER_REQUEST_COLUMNS)} FROM order_request {where}
            ORDER BY requested_at DESC NULLS LAST, request_id DESC
            LIMIT {MAX_POSITION_REQUESTS}
            """,
            position_id,
            position["order_id"],
        )
        requests = [_clean_row(row) for row in reversed(rows)]
        total = len(requests)
        if total >= MAX_POSITION_REQUESTS:
            total = await self.db.fetchval(f"SELECT COUNT(*) FROM order_request {where}", position_id, position["order_id"])
        attached = requests_by_order(requests, [order["order_id"] for order in orders])
        for order in orders:
            order["requests"] = attached.get(order["order_id"], [])
        return {
            "position": _clean_row(position),
            "orders": orders,
            "requests": requests,
            "requests_total": total or 0,
        }

    async def _linked_orders(self, selection: str, *args) -> list[dict]:
        """
        Orders, each with what became of it, the position it gave or closed and
        the signal it came from. ``selection`` is what follows the tables
        (WHERE, ORDER BY, LIMIT), the orders being named ``o``.

        The tables that say so are created by the trading services at their
        first start with the version that writes them: until then the orders
        are read alone, as they always were.
        """
        try:
            rows = await self.db.fetch(f"SELECT {_LINKED_ORDER_COLUMNS} FROM {_LINKED_ORDER_TABLES} {selection}", *args)
        except DatabaseUnavailable as e:
            if not _is_missing_table(e):
                raise
            rows = await self.db.fetch(f"SELECT {_ORDER_COLUMNS} FROM turbo_data_order o {selection}", *args)
            return [_unlinked_order(row) for row in rows]
        return [_linked_order(row) for row in rows]

    async def list_orders(self, start: date | None = None, end: date | None = None, limit: int = 500) -> dict:
        args: list[Any] = []
        conditions = self._date_range_conditions("o.order_time", start, end, args)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        total = await self.db.fetchval(f"SELECT COUNT(*) FROM turbo_data_order o {where}", *args)
        orders = await self._linked_orders(
            f"""
            {where}
            ORDER BY o.order_time DESC NULLS LAST, o.order_id DESC
            LIMIT {min(max(limit, 1), MAX_ROWS)}
            """,
            *args,
        )
        requests = await self.order_requests(start, end)
        attached = requests_by_order(requests, [order["order_id"] for order in orders])
        for order in orders:
            order["requests"] = attached.get(order["order_id"], [])
        refusals = refusal_runs(requests)
        await self._link_refusals(refusals)
        return {"items": orders, "total": total or 0, "refusals": refusals}

    async def order_requests(self, start: date | None = None, end: date | None = None) -> list[dict]:
        """
        The requests sent to Saxo's order endpoint between two days (both
        included), oldest first: the most recent ``MAX_REPLAYED_ROWS``. Empty
        until the trading services have created their table.
        """
        args: list[Any] = []
        conditions = self._date_range_conditions("requested_at", start, end, args)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = await self._fetch_optional(
            f"""
            SELECT {", ".join(ORDER_REQUEST_COLUMNS)} FROM order_request
            {where}
            ORDER BY requested_at DESC NULLS LAST, request_id DESC
            LIMIT {MAX_REPLAYED_ROWS}
            """,
            *args,
        )
        return [_clean_row(row) for row in reversed(rows)]

    async def order_attempts(self, start: date | None = None, end: date | None = None) -> list[dict]:
        """The requests to Saxo's order endpoint between two days, as order_requests reads them, without their bodies."""
        args: list[Any] = []
        conditions = self._date_range_conditions("requested_at", start, end, args)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = await self._fetch_optional(
            f"""
            SELECT {", ".join(_ATTEMPT_COLUMNS)} FROM order_request
            {where}
            ORDER BY requested_at DESC NULLS LAST, request_id DESC
            LIMIT {MAX_REPLAYED_ROWS}
            """,
            *args,
        )
        return [_clean_row(row) for row in reversed(rows)]

    async def position_record(self, position_id: str) -> dict | None:
        """One position as get_position reads it, its requests to Saxo without their bodies: the research tools'."""
        found = await self.get_position(position_id)
        if found is None:
            return None

        def bare(request: dict) -> dict:
            return {key: value for key, value in request.items() if key not in REQUEST_BODIES}

        found["requests"] = [bare(request) for request in found["requests"]]
        for order in found["orders"]:
            order["requests"] = [bare(request) for request in order.get("requests") or []]
        return found

    async def _link_refusals(self, runs: list[dict]) -> None:
        """Each run of refusals with its position as it is now, or the signal behind it and what became of it."""
        position_ids = sorted({run["position_id"] for run in runs if run.get("position_id")})
        signal_ids = sorted({run["signal_uuid"] for run in runs if run.get("signal_uuid")})
        positions, signals = {}, {}
        if position_ids:
            rows = await self._fetch_optional(
                f"SELECT {', '.join(_ORDER_POSITION_COLUMNS)} FROM turbo_data_position WHERE position_id = ANY($1::text[])",
                position_ids,
            )
            positions = {row["position_id"]: _clean_row(row) for row in rows}
        if signal_ids:
            rows = await self._fetch_optional(
                f"SELECT {', '.join(_REFUSED_SIGNAL_COLUMNS)} FROM signal_journal WHERE signal_uuid = ANY($1::text[])",
                signal_ids,
            )
            signals = {row["signal_uuid"]: _clean_row(row) for row in rows}
        for run in runs:
            run["position"] = positions.get(run.get("position_id"))
            run["signal"] = signals.get(run.get("signal_uuid"))

    # ── reporting ──

    async def closed_trades(self, start: date | None = None, end: date | None = None) -> list[dict]:
        args: list[Any] = [self.timezone]
        conditions = ["position_status = 'Closed'", "execution_time_close IS NOT NULL"]
        if start is not None:
            args.append(start)
            conditions.append(f"(execution_time_close AT TIME ZONE $1)::date >= ${len(args)}::date")
        if end is not None:
            args.append(end)
            conditions.append(f"(execution_time_close AT TIME ZONE $1)::date <= ${len(args)}::date")

        rows = await self.db.fetch(
            f"""
            SELECT
                (execution_time_close AT TIME ZONE $1)::date AS day_date,
                action,
                instrument_name,
                position_total_performance_percent AS performance_percent,
                position_profit_loss AS profit_loss
            FROM turbo_data_position
            WHERE {' AND '.join(conditions)}
            ORDER BY execution_time_close ASC, position_id ASC
            """,
            *args,
        )
        return [_clean_row(row) for row in rows]

    async def profit_before(self, start: date) -> float:
        value = await self.db.fetchval(
            """
            SELECT COALESCE(SUM(position_profit_loss), 0)
            FROM turbo_data_position
            WHERE position_status = 'Closed'
              AND execution_time_close IS NOT NULL
              AND (execution_time_close AT TIME ZONE $1)::date < $2::date
            """,
            self.timezone,
            start,
        )
        return value if value is not None and math.isfinite(value) else 0.0

    async def performance(self, start: date | None = None, end: date | None = None) -> dict:
        trades = await self.closed_trades(start, end)
        profit_before = await self.profit_before(start) if start is not None else 0.0
        return summarise_trades(trades, profit_before)

    async def exits(self, start: date | None = None, end: date | None = None) -> dict:
        args: list[Any] = [self.timezone]
        conditions = ["position_status = 'Closed'", "execution_time_close IS NOT NULL"]
        if start is not None:
            args.append(start)
            conditions.append(f"(execution_time_close AT TIME ZONE $1)::date >= ${len(args)}::date")
        if end is not None:
            args.append(end)
            conditions.append(f"(execution_time_close AT TIME ZONE $1)::date <= ${len(args)}::date")

        rows = await self.db.fetch(
            f"""
            SELECT
                position_id,
                action,
                position_close_reason AS close_reason,
                position_total_performance_percent AS performance_percent,
                position_max_performance_percent AS max_performance_percent,
                position_profit_loss AS profit_loss,
                position_open_price AS open_price,
                EXTRACT(HOUR FROM execution_time_open AT TIME ZONE $1)::int AS open_hour,
                EXTRACT(ISODOW FROM execution_time_open AT TIME ZONE $1)::int AS open_weekday,
                EXTRACT(EPOCH FROM (execution_time_close - execution_time_open))::float8 AS holding_seconds
            FROM turbo_data_position
            WHERE {' AND '.join(conditions)}
            ORDER BY execution_time_close ASC, position_id ASC
            """,
            *args,
        )
        trades = [_clean_row(row) for row in rows]
        await self._with_signal_and_worst(trades)
        return {
            **summarise_exits(trades),
            "by_weekday_hour": stats_by_weekday_hour(trades, opened_weekday_hour),
            "breakdowns": live_breakdowns(trades),
        }

    async def _with_signal_and_worst(self, trades: list[dict]) -> None:
        """
        Each trade with what the journal says of its signal (confidence, its delay to the trader's decision, a
        signal rule's when one sent it) and the worst it reached: each from a table of its own, read only if
        it exists yet.
        """
        ids = [trade["position_id"] for trade in trades if trade.get("position_id")]
        journal = await self._fetch_optional(
            """
            SELECT DISTINCT ON (position_id) position_id, confidence, signal_uuid,
                   EXTRACT(EPOCH FROM (decided_at - signal_timestamp))::float8 AS signal_delay_seconds
            FROM signal_journal WHERE position_id = ANY($1::text[])
            ORDER BY position_id, decided_at ASC NULLS LAST
            """,
            ids,
        ) if ids else []
        by_position = {row["position_id"]: row for row in journal}
        uuids = [row["signal_uuid"] for row in journal if row["signal_uuid"]]
        rules = await self._fetch_optional(
            "SELECT signal_uuid, rule_name FROM ta_signal WHERE signal_uuid = ANY($1::text[])", uuids,
        ) if uuids else []
        rule_of = {row["signal_uuid"]: row["rule_name"] for row in rules}
        worsts = await self._fetch_optional(
            "SELECT position_id, worst_percent FROM position_worst WHERE position_id = ANY($1::text[])", ids,
        ) if ids else []
        worst_of = {row["position_id"]: row["worst_percent"] for row in worsts}
        for trade in trades:
            signal = by_position.get(trade.get("position_id"))
            trade["journaled"] = signal is not None
            trade["confidence"] = signal["confidence"] if signal else None
            trade["signal_delay_seconds"] = signal["signal_delay_seconds"] if signal else None
            trade["rule_name"] = rule_of.get(signal["signal_uuid"]) if signal else None
            trade["min_performance_percent"] = worst_of.get(trade.get("position_id"))

    async def report(self, milestones_eur: list[float] | None = None, today: date | None = None) -> dict:
        """
        The figures of the daily Telegram report, built by the same code, plus
        the recorded account balances.
        """
        report_date = today or date.today()
        history_start = date(report_date.year - 4, 1, 1)
        days = 7

        closed_trades = await self._positions.get_closed_trade_history(start_date=history_start, end_date=report_date)
        daily_profit_history = await self._positions.get_daily_profit_history(end_date=report_date)
        balances = await self.balance_history()
        latest_balance = balances[-1] if balances else None

        payload = build_daily_trading_report_payload(
            report_date=report_date,
            closed_trades=closed_trades,
            daily_profit_history=daily_profit_history,
            daily_real=await self._positions.get_percent_of_last_n_days(days),
            daily_best=await self._positions.get_best_percent_of_last_n_days(days),
            daily_max=await self._positions.get_theoretical_percent_of_last_n_days_on_max(days),
            current_balance=latest_balance["total_value"] if latest_balance else None,
            milestones_eur=milestones_eur or list(DEFAULT_MILESTONES_EUR),
        )
        return json_safe({
            **payload,
            "balance_recorded_on": latest_balance["date_day"] if latest_balance else None,
            "balance_history": balances,
        })
