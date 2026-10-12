"""
Read-only PostgreSQL access for the admin interface.

Figures the trading rules also compute ("today" counters, cooldown) come from
the same managers the trader uses, so the dashboard shows what the rules see.
History and reporting queries are specific to the admin and bucket days in the
configured trading timezone.
"""

import asyncio
import logging
import math
from datetime import date
from typing import Any

from src.admin.analysis import summarise_exits
from src.admin.errors import DatabaseUnavailable
from src.database.postgres import AsyncDbPositionManager, AsyncDbRiskStateManager
from src.message_helper import DEFAULT_MILESTONES_EUR, build_daily_trading_report_payload

logger = logging.getLogger(__name__)

MAX_ROWS = 2000

_POSITION_COLUMNS = """
    action, position_id, position_amount, position_open_price, position_close_price,
    position_close_reason, position_profit_loss, position_total_open_price,
    position_total_close_price, position_total_performance_percent,
    position_max_performance_percent, position_status, position_kind,
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

    async def close(self):
        await self._conn_mgr.close()


def _clean_row(record) -> dict:
    """A JSON-safe dict: NaN and infinities cannot be serialised, so they become None."""
    row = dict(record)
    for key, value in row.items():
        if isinstance(value, float) and not math.isfinite(value):
            row[key] = None
    return row


def json_safe(value):
    """``value`` with every non-finite float, at any depth, replaced by None."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value


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

    # ── live values, journal, balance (written by the trading services) ──

    async def live_values(self) -> dict[str, dict]:
        rows = await self._fetch_optional(
            "SELECT position_id, bid, performance_percent, observed_at FROM position_live_value"
        )
        return {row["position_id"]: _clean_row(row) for row in rows}

    async def open_positions(self) -> list[dict]:
        """Open positions with the price and performance the position monitor last saw."""
        positions = (await self.list_positions(status="Open"))["items"]
        live = await self.live_values() if positions else {}
        for position in positions:
            value = live.get(position["position_id"], {})
            position["live_bid"] = value.get("bid")
            position["live_performance_percent"] = value.get("performance_percent")
            position["live_observed_at"] = value.get("observed_at")
        return positions

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

    async def get_position(self, position_id: str) -> dict | None:
        position = await self.db.fetchrow(
            f"SELECT {_POSITION_COLUMNS} FROM turbo_data_position WHERE position_id = $1",
            position_id,
        )
        if position is None:
            return None
        orders = await self.db.fetch(
            f"""
            SELECT {_ORDER_COLUMNS}
            FROM turbo_data_order
            WHERE position_id = $1 OR order_id = $2 OR order_id = ANY($3::text[])
            ORDER BY order_time ASC NULLS LAST, order_id ASC
            """,
            position_id,
            position["order_id"],
            position["related_order_id"] or [],
        )
        return {"position": _clean_row(position), "orders": [_clean_row(row) for row in orders]}

    async def list_orders(self, start: date | None = None, end: date | None = None, limit: int = 500) -> dict:
        args: list[Any] = []
        conditions = self._date_range_conditions("order_time", start, end, args)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        total = await self.db.fetchval(f"SELECT COUNT(*) FROM turbo_data_order {where}", *args)
        rows = await self.db.fetch(
            f"""
            SELECT {_ORDER_COLUMNS}
            FROM turbo_data_order
            {where}
            ORDER BY order_time DESC NULLS LAST, order_id DESC
            LIMIT {min(max(limit, 1), MAX_ROWS)}
            """,
            *args,
        )
        return {"items": [_clean_row(row) for row in rows], "total": total or 0}

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
                action,
                position_close_reason AS close_reason,
                position_total_performance_percent AS performance_percent,
                position_max_performance_percent AS max_performance_percent,
                position_profit_loss AS profit_loss,
                EXTRACT(HOUR FROM execution_time_open AT TIME ZONE $1)::int AS open_hour,
                EXTRACT(EPOCH FROM (execution_time_close - execution_time_open))::float8 AS holding_seconds
            FROM turbo_data_position
            WHERE {' AND '.join(conditions)}
            ORDER BY execution_time_close ASC, position_id ASC
            """,
            *args,
        )
        return summarise_exits([_clean_row(row) for row in rows])

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
