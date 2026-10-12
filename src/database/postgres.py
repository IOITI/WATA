# src/database/postgres.py
"""
Async PostgreSQL database layer for WATA.
Replaces DuckDB for the write path (Trader, Position Monitor).
Uses asyncpg connection pool for high-performance async I/O.
"""

import asyncio
import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

import asyncpg

from src.configuration import ConfigurationManager

logger = logging.getLogger(__name__)

_DATETIME_FIELDS = frozenset({
    "order_submit_time",
    "order_time",
    "execution_time_open",
    "execution_time_close",
})
_DATE_FIELDS = frozenset({"date_day"})


def _parse_datetime_value(value: Any, field_name: str) -> Any:
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    if not isinstance(value, str):
        return value

    candidate = value.strip()
    if not candidate:
        return None
    if candidate.endswith("Z"):
        candidate = f"{candidate[:-1]}+00:00"

    try:
        return datetime.fromisoformat(candidate)
    except ValueError:
        pass

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(candidate, fmt)
        except ValueError:
            continue

    raise ValueError(f"Unsupported datetime value for {field_name}: {value!r}")


def _parse_date_value(value: Any, field_name: str) -> Any:
    if value is None:
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return value

    candidate = value.strip()
    if not candidate:
        return None

    try:
        return date.fromisoformat(candidate)
    except ValueError:
        parsed_datetime = _parse_datetime_value(candidate, field_name)
        if isinstance(parsed_datetime, datetime):
            return parsed_datetime.date()
        raise ValueError(f"Unsupported date value for {field_name}: {value!r}")


def _normalize_temporal_fields(
    data: dict[str, Any],
    *,
    datetime_fields: frozenset[str] = frozenset(),
    date_fields: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    normalized = dict(data)
    for field_name in datetime_fields:
        if field_name in normalized:
            normalized[field_name] = _parse_datetime_value(normalized[field_name], field_name)
    for field_name in date_fields:
        if field_name in normalized:
            normalized[field_name] = _parse_date_value(normalized[field_name], field_name)
    return normalized


# ──────────────────────────────────────────────
#  Connection Pool Manager
# ──────────────────────────────────────────────

class PostgresConnectionManager:
    """Manages an asyncpg connection pool (singleton-friendly)."""

    def __init__(self, dsn: str, min_size: int = 2, max_size: int = 10, server_settings: dict | None = None):
        self._dsn = dsn
        self._min_size = min_size
        self._max_size = max_size
        # Session parameters applied to every connection (e.g. a read-only pool).
        self._server_settings = server_settings
        self._pool: asyncpg.Pool | None = None

    @classmethod
    def from_config(cls, config_manager: ConfigurationManager) -> "PostgresConnectionManager":
        pg_cfg = config_manager.get_config_value("postgresql", {})
        dsn = pg_cfg.get("dsn", "postgresql://wata:wata@localhost:5432/wata")
        min_size = pg_cfg.get("pool_min_size", 2)
        max_size = pg_cfg.get("pool_max_size", 10)
        return cls(dsn=dsn, min_size=min_size, max_size=max_size)

    async def connect(self):
        if self._pool is None:
            logger.info("Creating asyncpg connection pool (min=%d, max=%d)…", self._min_size, self._max_size)
            self._pool = await asyncpg.create_pool(
                self._dsn,
                min_size=self._min_size,
                max_size=self._max_size,
                command_timeout=30,
                server_settings=self._server_settings,
            )
            logger.info("asyncpg connection pool created.")
        return self._pool

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("PostgresConnectionManager.connect() has not been awaited yet.")
        return self._pool

    async def close(self):
        if self._pool:
            await self._pool.close()
            self._pool = None
            logger.info("asyncpg connection pool closed.")

    async def execute(self, query: str, *args) -> str:
        return await self.pool.execute(query, *args)

    async def fetch(self, query: str, *args) -> list[asyncpg.Record]:
        return await self.pool.fetch(query, *args)

    async def fetchrow(self, query: str, *args) -> asyncpg.Record | None:
        return await self.pool.fetchrow(query, *args)

    async def fetchval(self, query: str, *args) -> Any:
        return await self.pool.fetchval(query, *args)


# ──────────────────────────────────────────────
#  Schema Initialization
# ──────────────────────────────────────────────

SCHEMA_SQL = """
-- Orders table
CREATE TABLE IF NOT EXISTS turbo_data_order (
    action           VARCHAR(16),
    buy_sell         VARCHAR(8),
    order_id         VARCHAR(64) PRIMARY KEY,
    order_amount     INTEGER,
    order_type       VARCHAR(32),
    order_kind       VARCHAR(32),
    order_time       TIMESTAMPTZ,
    related_order_id TEXT[],
    position_id      VARCHAR(64),
    instrument_name  TEXT,
    instrument_symbol VARCHAR(64),
    instrument_uic   INTEGER,
    instrument_price DOUBLE PRECISION,
    instrument_currency VARCHAR(8),
    order_cost       DOUBLE PRECISION
);

-- Positions table
CREATE TABLE IF NOT EXISTS turbo_data_position (
    action                              VARCHAR(16),
    position_id                         VARCHAR(64) PRIMARY KEY,
    position_amount                     INTEGER,
    position_open_price                 DOUBLE PRECISION,
    position_close_price                DOUBLE PRECISION,
    position_close_reason               VARCHAR(128),
    position_profit_loss                DOUBLE PRECISION,
    position_total_open_price           DOUBLE PRECISION,
    position_total_close_price          DOUBLE PRECISION,
    position_total_performance_percent  DOUBLE PRECISION,
    position_max_performance_percent    DOUBLE PRECISION,
    position_status                     VARCHAR(16) DEFAULT 'Open',
    position_kind                       VARCHAR(32),
    execution_time_open                 TIMESTAMPTZ,
    execution_time_close                TIMESTAMPTZ,
    order_id                            VARCHAR(64),
    related_order_id                    TEXT[],
    instrument_name                     TEXT,
    instrument_symbol                   VARCHAR(64),
    instrument_uic                      INTEGER,
    instrument_currency                 VARCHAR(8)
);

-- Trade performance (daily aggregates)
CREATE TABLE IF NOT EXISTS trade_performance (
    date_day              DATE PRIMARY KEY,
    perf_day_real         DOUBLE PRECISION,
    money_made_real       DOUBLE PRECISION,
    trade_number_real     INTEGER,
    max_perf_day_simulated DOUBLE PRECISION
);

-- Encrypted token storage
CREATE TABLE IF NOT EXISTS auth_tokens (
    token_id      VARCHAR(64) PRIMARY KEY,
    token_type    VARCHAR(32),
    encrypted_data BYTEA,
    creation_time  TIMESTAMPTZ,
    last_update    TIMESTAMPTZ,
    metadata       TEXT
);

-- Shared risk-management state (e.g. cooldown-after-loss) across the trader and
-- position_monitor processes, which run as separate containers and therefore
-- cannot share in-memory state.
CREATE TABLE IF NOT EXISTS trade_risk_state (
    key         VARCHAR(64) PRIMARY KEY,
    value_time  TIMESTAMPTZ
);

-- Latest price and performance of each open position, as seen by the position
-- monitor. Written on a timer for the admin interface; nothing trades on it.
CREATE TABLE IF NOT EXISTS position_live_value (
    position_id         VARCHAR(64) PRIMARY KEY,
    bid                 DOUBLE PRECISION,
    performance_percent DOUBLE PRECISION,
    observed_at         TIMESTAMPTZ
);

-- What the trader did with each signal it received (executed, refused, ...).
CREATE TABLE IF NOT EXISTS signal_journal (
    signal_uuid        VARCHAR(64) PRIMARY KEY,
    action             VARCHAR(32),
    indice             VARCHAR(64),
    confidence         DOUBLE PRECISION,
    signal_timestamp   TIMESTAMPTZ,
    received_timestamp TIMESTAMPTZ,
    decided_at         TIMESTAMPTZ,
    outcome            VARCHAR(16),
    reason             TEXT,
    position_id        VARCHAR(64),
    order_id           VARCHAR(64)
);

-- Total account value, recorded with each daily report.
CREATE TABLE IF NOT EXISTS account_balance_history (
    date_day     DATE PRIMARY KEY,
    total_value  DOUBLE PRECISION,
    recorded_at  TIMESTAMPTZ
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_signal_journal_decided_at ON signal_journal (decided_at);
CREATE INDEX IF NOT EXISTS idx_position_status ON turbo_data_position (position_status);
CREATE INDEX IF NOT EXISTS idx_position_close_time ON turbo_data_position (execution_time_close);
CREATE INDEX IF NOT EXISTS idx_position_open_time ON turbo_data_position (execution_time_open);
"""


async def init_schema(conn_mgr: PostgresConnectionManager):
    """Creates all tables and indexes if they don't exist."""
    logger.info("Initializing PostgreSQL schema…")
    await conn_mgr.execute(SCHEMA_SQL)
    logger.info("PostgreSQL schema initialized.")


# ──────────────────────────────────────────────
#  Async DB Managers
# ──────────────────────────────────────────────

class AsyncDbOrderManager:
    """Async order persistence using PostgreSQL."""

    def __init__(self, conn_mgr: PostgresConnectionManager):
        self.db = conn_mgr

    async def insert_turbo_order_data(self, data: dict):
        normalized_data = _normalize_temporal_fields(data, datetime_fields=_DATETIME_FIELDS)
        await self.db.execute(
            """
            INSERT INTO turbo_data_order
                (action, buy_sell, order_id, order_amount, order_type, order_kind,
                 order_time, related_order_id, position_id, instrument_name,
                 instrument_symbol, instrument_uic, instrument_price,
                 instrument_currency, order_cost)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
            ON CONFLICT (order_id) DO NOTHING
            """,
            normalized_data["action"],
            normalized_data["buy_sell"],
            normalized_data["order_id"],
            normalized_data["order_amount"],
            normalized_data["order_type"],
            normalized_data["order_kind"],
            normalized_data.get("order_submit_time") or normalized_data.get("order_time"),
            normalized_data.get("related_order_id", []),
            normalized_data["position_id"],
            normalized_data["instrument_name"],
            normalized_data["instrument_symbol"],
            normalized_data["instrument_uic"],
            normalized_data["instrument_price"],
            normalized_data["instrument_currency"],
            normalized_data.get("order_cost"),
        )


class AsyncDbPositionManager:
    """Async position persistence using PostgreSQL."""

    def __init__(self, conn_mgr: PostgresConnectionManager):
        self.db = conn_mgr

    @staticmethod
    def _today() -> date:
        return date.today()

    async def insert_turbo_open_position_data(self, data: dict):
        normalized_data = _normalize_temporal_fields(data, datetime_fields=_DATETIME_FIELDS)
        await self.db.execute(
            """
            INSERT INTO turbo_data_position
                (action, position_id, position_amount, position_open_price,
                 position_total_open_price, position_status, position_kind,
                 execution_time_open, order_id, related_order_id,
                 instrument_name, instrument_symbol, instrument_uic,
                 instrument_currency)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
            ON CONFLICT (position_id) DO NOTHING
            """,
            normalized_data["action"],
            normalized_data["position_id"],
            normalized_data["position_amount"],
            normalized_data["position_open_price"],
            normalized_data["position_total_open_price"],
            normalized_data.get("position_status", "Open"),
            normalized_data["position_kind"],
            normalized_data.get("execution_time_open"),
            normalized_data["order_id"],
            normalized_data.get("related_order_id", []),
            normalized_data["instrument_name"],
            normalized_data["instrument_symbol"],
            normalized_data["instrument_uic"],
            normalized_data["instrument_currency"],
        )

    async def update_turbo_position_data(self, position_id: str, update_data: dict):
        if not update_data:
            return
        normalized_update_data = _normalize_temporal_fields(update_data, datetime_fields=_DATETIME_FIELDS)
        set_parts = []
        values = []
        for idx, (key, val) in enumerate(normalized_update_data.items(), start=1):
            set_parts.append(f"{key} = ${idx}")
            values.append(val)
        values.append(position_id)
        query = f"UPDATE turbo_data_position SET {', '.join(set_parts)} WHERE position_id = ${len(values)}"
        await self.db.execute(query, *values)

    async def get_open_positions_ids(self) -> list[str]:
        rows = await self.db.fetch(
            "SELECT position_id FROM turbo_data_position WHERE position_status = 'Open'"
        )
        return [r["position_id"] for r in rows]

    async def get_open_positions_ids_actions(self) -> list[dict]:
        rows = await self.db.fetch(
            "SELECT position_id, action FROM turbo_data_position WHERE position_status = 'Open'"
        )
        return [{"position_id": r["position_id"], "action": r["action"]} for r in rows]

    async def get_today_trade_count(self) -> int:
        today = self._today()
        val = await self.db.fetchval(
            """
            SELECT COUNT(*) FROM turbo_data_position
            WHERE execution_time_open::date = $1::date
            """,
            today,
        )
        return val or 0

    async def get_percent_of_the_day(self) -> float:
        today = self._today()
        rows = await self.db.fetch(
            """
            SELECT position_total_performance_percent
            FROM turbo_data_position
            WHERE position_status = 'Closed'
              AND execution_time_close::date = $1::date
            """,
            today,
        )
        return self._calculate_final_percentage(rows)

    async def get_max_position_percent(self, position_id: str) -> float:
        val = await self.db.fetchval(
            "SELECT position_max_performance_percent FROM turbo_data_position WHERE position_id = $1",
            position_id,
        )
        return val if val is not None else 0.0

    async def get_last_closed_position_performance(self) -> dict | None:
        today = self._today()
        row = await self.db.fetchrow(
            """
            SELECT position_total_performance_percent, execution_time_close
            FROM turbo_data_position
            WHERE position_status = 'Closed'
              AND execution_time_close::date = $1::date
            ORDER BY execution_time_close DESC
            LIMIT 1
            """,
            today,
        )
        if row:
            return {"performance_percent": row[0], "close_time": row[1]}
        return None

    async def check_position_ids_exist(self, position_ids: list[str]) -> dict:
        result = {"position_ids_in_db": [], "position_ids_not_found": []}
        for pid in position_ids:
            row = await self.db.fetchrow(
                "SELECT position_id, order_id, action FROM turbo_data_position WHERE position_id = $1",
                pid,
            )
            if row:
                result["position_ids_in_db"].append(dict(row))
            else:
                result["position_ids_not_found"].append(pid)
        return result

    async def get_stats_of_the_day(self) -> dict:
        today = self._today()

        general = await self.db.fetch(
            """
            SELECT
                to_char(execution_time_close, 'YYYY/MM/DD') AS day_date,
                COUNT(*) AS position_count,
                AVG(position_total_performance_percent) AS avg_percent,
                MAX(position_total_performance_percent) AS max_percent,
                MIN(position_total_performance_percent) AS min_percent,
                SUM(position_profit_loss) AS sum_profit
            FROM turbo_data_position
            WHERE position_status = 'Closed'
              AND execution_time_close::date = $1::date
            GROUP BY day_date
            ORDER BY day_date DESC
            """,
            today,
        )
        detail = await self.db.fetch(
            """
            SELECT
                to_char(execution_time_close, 'YYYY/MM/DD') AS day_date,
                action,
                COUNT(*) AS position_count,
                AVG(position_total_performance_percent) AS avg_percent,
                MAX(position_total_performance_percent) AS max_percent,
                MIN(position_total_performance_percent) AS min_percent,
                SUM(position_profit_loss) AS sum_profit
            FROM turbo_data_position
            WHERE position_status = 'Closed'
              AND execution_time_close::date = $1::date
            GROUP BY day_date, action
            ORDER BY day_date DESC, action ASC
            """,
            today,
        )
        return {
            "general": [dict(r) for r in general],
            "detail_stats": [dict(r) for r in detail],
        }

    async def get_closed_trade_history(
        self,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[dict]:
        conditions = ["position_status = 'Closed'"]
        args: list[Any] = []

        if start_date is not None:
            args.append(start_date)
            conditions.append(f"execution_time_close::date >= ${len(args)}::date")
        if end_date is not None:
            args.append(end_date)
            conditions.append(f"execution_time_close::date <= ${len(args)}::date")

        rows = await self.db.fetch(
            f"""
            SELECT
                action,
                position_id,
                position_total_performance_percent AS performance_percent,
                position_max_performance_percent AS max_performance_percent,
                position_profit_loss AS profit_loss,
                execution_time_close
            FROM turbo_data_position
            WHERE {' AND '.join(conditions)}
            ORDER BY execution_time_close ASC, position_id ASC
            """,
            *args,
        )
        return [dict(r) for r in rows]

    async def get_daily_profit_history(self, end_date: date | None = None) -> list[dict]:
        args: list[Any] = []
        end_date_clause = ""
        if end_date is not None:
            args.append(end_date)
            end_date_clause = f" AND execution_time_close::date <= ${len(args)}::date"

        rows = await self.db.fetch(
            f"""
            SELECT
                execution_time_close::date AS day_date,
                SUM(position_profit_loss) AS sum_profit,
                COUNT(*) AS trade_count
            FROM turbo_data_position
            WHERE position_status = 'Closed'{end_date_clause}
            GROUP BY execution_time_close::date
            ORDER BY day_date ASC
            """,
            *args,
        )
        return [dict(r) for r in rows]

    async def get_percent_of_last_n_days(self, n: int) -> dict:
        return await self._get_percentages_for_n_days(n, "position_total_performance_percent", self._calculate_final_percentage)

    async def get_best_percent_of_last_n_days(self, n: int) -> dict:
        return await self._get_percentages_for_n_days(n, "position_total_performance_percent", self._calculate_best_percentage)

    async def get_theoretical_percent_of_last_n_days_on_max(self, n: int) -> dict:
        return await self._get_percentages_for_n_days(n, "position_max_performance_percent", self._calculate_final_percentage)

    async def get_best_theoretical_percent_of_last_n_days_on_max(self, n: int) -> dict:
        return await self._get_percentages_for_n_days(n, "position_max_performance_percent", self._calculate_best_percentage)

    # ── helpers ──

    async def _get_percentages_for_n_days(self, n: int, column: str, calc_fn) -> dict:
        results = {}
        for i in range(n):
            d = date.today() - timedelta(days=i)
            display_date = d.strftime("%Y/%m/%d")
            rows = await self.db.fetch(
                f"""
                SELECT {column}
                FROM turbo_data_position
                WHERE position_status = 'Closed'
                  AND execution_time_close::date = $1::date
                """,
                d,
            )
            results[display_date] = calc_fn(rows) if rows else 0.0
        return results

    @staticmethod
    def _calculate_final_percentage(rows) -> float:
        val = 1.0
        for r in rows:
            pct = r[0] if r[0] is not None else 0
            val *= 1 + pct / 100.0
        return round((val - 1) * 100, 2)

    @staticmethod
    def _calculate_best_percentage(rows) -> float:
        val = 1.0
        intermediates = []
        for r in rows:
            pct = r[0] if r[0] is not None else 0
            val *= 1 + pct / 100.0
            intermediates.append(val)
        return round((max(intermediates) - 1) * 100, 2) if intermediates else 0.0


class AsyncDbTradePerformanceManager:
    """Async trade performance persistence."""

    def __init__(self, conn_mgr: PostgresConnectionManager):
        self.db = conn_mgr

    async def insert_trade_performance_data(self, data: dict):
        normalized_data = _normalize_temporal_fields(data, date_fields=_DATE_FIELDS)
        await self.db.execute(
            """
            INSERT INTO trade_performance (date_day, perf_day_real, money_made_real, trade_number_real, max_perf_day_simulated)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (date_day) DO NOTHING
            """,
            normalized_data["date_day"],
            normalized_data["perf_day_real"],
            normalized_data["money_made_real"],
            normalized_data["trade_number_real"],
            normalized_data.get("max_perf_day_simulated"),
        )

    async def create_last_day_trade_performance_data(self):
        today = date.today()
        row = await self.db.fetchrow(
            """
            SELECT
                COALESCE(SUM(position_profit_loss), 0.0) AS money_made_real,
                COUNT(position_id) AS trade_number_real
            FROM turbo_data_position
            WHERE position_status = 'Closed'
              AND execution_time_close::date = $1::date
            """,
            today,
        )
        if row:
            # Calculate perf_day_real from sequential percentages
            pct_rows = await self.db.fetch(
                """
                SELECT position_total_performance_percent
                FROM turbo_data_position
                WHERE position_status = 'Closed'
                  AND execution_time_close::date = $1::date
                """,
                today,
            )
            perf = AsyncDbPositionManager._calculate_final_percentage(pct_rows) if pct_rows else 0.0
            await self.insert_trade_performance_data({
                "date_day": today,
                "perf_day_real": perf,
                "money_made_real": row["money_made_real"],
                "trade_number_real": row["trade_number_real"],
                "max_perf_day_simulated": None,
            })


class AsyncDbTokenManager:
    """Async encrypted token storage."""

    def __init__(self, conn_mgr: PostgresConnectionManager):
        self.db = conn_mgr

    async def store_token(self, token_id: str, token_type: str, encrypted_data: bytes, metadata: str | None = None):
        now = datetime.now()
        await self.db.execute(
            """
            INSERT INTO auth_tokens (token_id, token_type, encrypted_data, creation_time, last_update, metadata)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (token_id) DO UPDATE SET
                encrypted_data = EXCLUDED.encrypted_data,
                last_update = EXCLUDED.last_update,
                metadata = EXCLUDED.metadata
            """,
            token_id, token_type, encrypted_data, now, now, metadata,
        )

    async def get_token(self, token_id: str) -> bytes | None:
        return await self.db.fetchval(
            "SELECT encrypted_data FROM auth_tokens WHERE token_id = $1",
            token_id,
        )

    async def token_exists(self, token_id: str) -> bool:
        val = await self.db.fetchval(
            "SELECT COUNT(*) FROM auth_tokens WHERE token_id = $1",
            token_id,
        )
        return (val or 0) > 0

    async def delete_token(self, token_id: str):
        await self.db.execute("DELETE FROM auth_tokens WHERE token_id = $1", token_id)


class AsyncDbRiskStateManager:
    """
    Shared risk-management state across processes (trader + position_monitor run
    as separate containers, so this can't live in Python instance attributes).

    Currently backs TradingRule's cooldown-after-loss check: position_monitor
    records the loss, trader reads it back before allowing a new trade.
    """

    LAST_LOSS_KEY = "last_loss_timestamp"
    # Set (to the time of the pause) while the operator has paused new entries.
    ENTRIES_PAUSED_KEY = "entries_paused_since"

    def __init__(self, conn_mgr: PostgresConnectionManager):
        self.db = conn_mgr

    async def record_loss_timestamp(self, ts: datetime):
        await self.db.execute(
            """
            INSERT INTO trade_risk_state (key, value_time) VALUES ($1, $2)
            ON CONFLICT (key) DO UPDATE SET value_time = EXCLUDED.value_time
            """,
            self.LAST_LOSS_KEY, ts,
        )

    async def get_last_loss_timestamp(self) -> datetime | None:
        return await self.db.fetchval(
            "SELECT value_time FROM trade_risk_state WHERE key = $1",
            self.LAST_LOSS_KEY,
        )

    async def pause_entries(self, ts: datetime):
        """Pause new entries; an existing pause keeps the time it started at."""
        await self.db.execute(
            """
            INSERT INTO trade_risk_state (key, value_time) VALUES ($1, $2)
            ON CONFLICT (key) DO NOTHING
            """,
            self.ENTRIES_PAUSED_KEY, ts,
        )

    async def resume_entries(self):
        await self.db.execute("DELETE FROM trade_risk_state WHERE key = $1", self.ENTRIES_PAUSED_KEY)

    async def get_entries_paused_since(self) -> datetime | None:
        return await self.db.fetchval(
            "SELECT value_time FROM trade_risk_state WHERE key = $1",
            self.ENTRIES_PAUSED_KEY,
        )


class AsyncDbLiveValueManager:
    """Latest observed price and performance of the open positions (see position_live_value)."""

    def __init__(self, conn_mgr: PostgresConnectionManager):
        self.db = conn_mgr

    async def replace_live_values(self, values: dict[str, tuple[float, float, datetime]]):
        """
        Make the table hold exactly ``values`` ({position_id: (bid, performance
        percent, observed at)}): rows of positions that are no longer open go.
        """
        position_ids = list(values)
        await self.db.execute(
            "DELETE FROM position_live_value WHERE NOT (position_id = ANY($1::text[]))",
            position_ids,
        )
        if not position_ids:
            return
        await self.db.execute(
            """
            INSERT INTO position_live_value (position_id, bid, performance_percent, observed_at)
            SELECT * FROM unnest($1::text[], $2::float8[], $3::float8[], $4::timestamptz[])
            ON CONFLICT (position_id) DO UPDATE SET
                bid = EXCLUDED.bid,
                performance_percent = EXCLUDED.performance_percent,
                observed_at = EXCLUDED.observed_at
            """,
            position_ids,
            [values[pid][0] for pid in position_ids],
            [values[pid][1] for pid in position_ids],
            [values[pid][2] for pid in position_ids],
        )


class AsyncDbSignalJournalManager:
    """What the trader did with each signal (see signal_journal)."""

    def __init__(self, conn_mgr: PostgresConnectionManager):
        self.db = conn_mgr

    async def record_decision(self, entry: dict):
        normalized = _normalize_temporal_fields(
            entry,
            datetime_fields=frozenset({"signal_timestamp", "received_timestamp", "decided_at"}),
        )
        await self.db.execute(
            """
            INSERT INTO signal_journal
                (signal_uuid, action, indice, confidence, signal_timestamp, received_timestamp,
                 decided_at, outcome, reason, position_id, order_id)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
            ON CONFLICT (signal_uuid) DO NOTHING
            """,
            normalized["signal_uuid"],
            normalized.get("action"),
            normalized.get("indice"),
            normalized.get("confidence"),
            normalized.get("signal_timestamp"),
            normalized.get("received_timestamp"),
            normalized.get("decided_at"),
            normalized["outcome"],
            normalized.get("reason"),
            normalized.get("position_id"),
            normalized.get("order_id"),
        )


class AsyncDbBalanceHistoryManager:
    """Total account value over time (see account_balance_history)."""

    def __init__(self, conn_mgr: PostgresConnectionManager):
        self.db = conn_mgr

    async def record_balance(self, day: date, total_value: float):
        """Keep the latest value seen on ``day``."""
        await self.db.execute(
            """
            INSERT INTO account_balance_history (date_day, total_value, recorded_at)
            VALUES ($1, $2, $3)
            ON CONFLICT (date_day) DO UPDATE SET
                total_value = EXCLUDED.total_value,
                recorded_at = EXCLUDED.recorded_at
            """,
            day, total_value, datetime.now().astimezone(),
        )

    async def record_balance_safely(self, day: date, total_value: float | None) -> None:
        """``record_balance`` for callers that must not fail because of it (the daily report)."""
        if total_value is None:
            return
        try:
            await self.record_balance(day, total_value)
        except Exception as e:
            logger.error("Failed to record the account balance: %s", e)


# ──────────────────────────────────────────────
#  Price history (price recorder)
# ──────────────────────────────────────────────

# Created by the price recorder alone, not by init_schema(): the trading
# services have no use for these tables and must not depend on them.
PRICE_HISTORY_SCHEMA_SQL = """
-- Instruments whose prices are recorded. Saxo reuses a UIC across asset types,
-- so the two together identify one.
CREATE TABLE IF NOT EXISTS price_instrument (
    id              SERIAL PRIMARY KEY,
    uic             INTEGER NOT NULL,
    asset_type      VARCHAR(40) NOT NULL,
    symbol          VARCHAR(64),
    description     TEXT,
    currency        VARCHAR(8),
    role            VARCHAR(16) NOT NULL,
    indice          VARCHAR(32),
    underlying_uic  INTEGER,
    direction       VARCHAR(8),
    barrier         DOUBLE PRECISION,
    issuer          VARCHAR(32),
    first_seen_at   TIMESTAMPTZ NOT NULL,
    last_seen_at    TIMESTAMPTZ NOT NULL,
    UNIQUE (uic, asset_type)
);

-- Ten-second bars of the bid and the ask. ts is the start of the bucket.
-- updates = 0: the quote did not move in the bucket. No row: not quotable, or
-- not recorded (see price_recorder_coverage).
CREATE TABLE IF NOT EXISTS price_bar_10s (
    ts               TIMESTAMPTZ NOT NULL,
    instrument_id    INTEGER NOT NULL,
    bid_open         DOUBLE PRECISION NOT NULL,
    bid_high         DOUBLE PRECISION NOT NULL,
    bid_low          DOUBLE PRECISION NOT NULL,
    bid_close        DOUBLE PRECISION NOT NULL,
    ask_open         DOUBLE PRECISION,
    ask_high         DOUBLE PRECISION,
    ask_low          DOUBLE PRECISION,
    ask_close        DOUBLE PRECISION,
    updates          SMALLINT NOT NULL,
    delayed_minutes  SMALLINT NOT NULL DEFAULT 0,
    PRIMARY KEY (instrument_id, ts)
) PARTITION BY RANGE (ts);

-- One row per bucket the recorder lived through: whether quotes were arriving,
-- and how much it wrote. A bucket with no row here was not recorded at all.
CREATE TABLE IF NOT EXISTS price_recorder_coverage (
    ts           TIMESTAMPTZ PRIMARY KEY,
    feed_ok      BOOLEAN NOT NULL,
    instruments  SMALLINT NOT NULL,
    bars         SMALLINT NOT NULL
);
"""

PRICE_BAR_TABLE = "price_bar_10s"
_PRICE_PARTITION_NAME = re.compile(rf"^{PRICE_BAR_TABLE}_y(\d{{4}})m(\d{{2}})$")


async def init_price_history_schema(conn_mgr: PostgresConnectionManager):
    await conn_mgr.execute(PRICE_HISTORY_SCHEMA_SQL)


def _month_start(day: date, months_later: int = 0) -> date:
    months = day.year * 12 + (day.month - 1) + months_later
    return date(months // 12, months % 12 + 1, 1)


def price_bar_partition(day: date) -> tuple[str, date, date]:
    """Name of the monthly partition holding ``day``, and its bounds (first day in, first day out)."""
    start = _month_start(day)
    return f"{PRICE_BAR_TABLE}_y{start.year:04d}m{start.month:02d}", start, _month_start(day, 1)


class AsyncDbPriceHistoryManager:
    """Instruments, bars and coverage written by the price recorder (see PRICE_HISTORY_SCHEMA_SQL)."""

    BAR_COLUMNS = (
        "ts", "instrument_id", "bid_open", "bid_high", "bid_low", "bid_close",
        "ask_open", "ask_high", "ask_low", "ask_close", "updates", "delayed_minutes",
    )

    def __init__(self, conn_mgr: PostgresConnectionManager):
        self.db = conn_mgr

    async def ensure_partitions(self, today: date, months_ahead: int = 1) -> list[str]:
        """Create the partitions of this month and the next ones, so that a bar never arrives without one."""
        names = []
        for offset in range(months_ahead + 1):
            name, start, end = price_bar_partition(_month_start(today, offset))
            # Bounds in UTC whatever the session's timezone: a bucket belongs to the month it starts in, in UTC.
            await self.db.execute(
                f"CREATE TABLE IF NOT EXISTS {name} PARTITION OF {PRICE_BAR_TABLE} "
                f"FOR VALUES FROM ('{start.isoformat()} 00:00:00+00') TO ('{end.isoformat()} 00:00:00+00')"
            )
            names.append(name)
        return names

    async def drop_partitions_older_than(self, today: date, months: int) -> list[str]:
        """
        Remove the bars of the months that ended more than ``months`` months ago
        (and their coverage). Only called when a retention is configured.
        """
        if months <= 0:
            return []
        cutoff = _month_start(today, -months)
        rows = await self.db.fetch(
            "SELECT child.relname AS name FROM pg_inherits "
            "JOIN pg_class parent ON pg_inherits.inhparent = parent.oid "
            "JOIN pg_class child ON pg_inherits.inhrelid = child.oid "
            "WHERE parent.relname = $1",
            PRICE_BAR_TABLE,
        )
        dropped = []
        for row in rows:
            match = _PRICE_PARTITION_NAME.match(row["name"])
            if not match:
                continue
            if date(int(match.group(1)), int(match.group(2)), 1) < cutoff:
                await self.db.execute(f"DROP TABLE IF EXISTS {row['name']}")
                dropped.append(row["name"])
        if dropped:
            await self.db.execute(
                "DELETE FROM price_recorder_coverage WHERE ts < $1",
                datetime(cutoff.year, cutoff.month, 1, tzinfo=timezone.utc),
            )
        return dropped

    async def upsert_instrument(self, instrument: dict, seen_at: datetime) -> int:
        """Record (or refresh) an instrument's description; returns its id."""
        return await self.db.fetchval(
            """
            INSERT INTO price_instrument (
                uic, asset_type, symbol, description, currency, role, indice,
                underlying_uic, direction, barrier, issuer, first_seen_at, last_seen_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $12)
            ON CONFLICT (uic, asset_type) DO UPDATE SET
                symbol = COALESCE(EXCLUDED.symbol, price_instrument.symbol),
                description = COALESCE(EXCLUDED.description, price_instrument.description),
                currency = COALESCE(EXCLUDED.currency, price_instrument.currency),
                role = EXCLUDED.role,
                indice = COALESCE(EXCLUDED.indice, price_instrument.indice),
                underlying_uic = COALESCE(EXCLUDED.underlying_uic, price_instrument.underlying_uic),
                direction = COALESCE(EXCLUDED.direction, price_instrument.direction),
                barrier = COALESCE(EXCLUDED.barrier, price_instrument.barrier),
                issuer = COALESCE(EXCLUDED.issuer, price_instrument.issuer),
                last_seen_at = EXCLUDED.last_seen_at
            RETURNING id
            """,
            instrument["uic"], instrument["asset_type"], instrument.get("symbol"), instrument.get("description"),
            instrument.get("currency"), instrument["role"], instrument.get("indice"),
            instrument.get("underlying_uic"), instrument.get("direction"), instrument.get("barrier"),
            instrument.get("issuer"), seen_at,
        )

    async def insert_bars(self, bars: list[tuple]) -> None:
        """Write bars (tuples in BAR_COLUMNS order) in one statement. A bar already there is left as it is."""
        if not bars:
            return
        columns = list(zip(*bars))
        await self.db.execute(
            f"""
            INSERT INTO {PRICE_BAR_TABLE} ({", ".join(self.BAR_COLUMNS)})
            SELECT * FROM unnest(
                $1::timestamptz[], $2::int4[],
                $3::float8[], $4::float8[], $5::float8[], $6::float8[],
                $7::float8[], $8::float8[], $9::float8[], $10::float8[],
                $11::int2[], $12::int2[]
            )
            ON CONFLICT (instrument_id, ts) DO NOTHING
            """,
            *[list(column) for column in columns],
        )

    async def record_coverage(self, ts: datetime, feed_ok: bool, instruments: int, bars: int) -> None:
        await self.db.execute(
            """
            INSERT INTO price_recorder_coverage (ts, feed_ok, instruments, bars)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (ts) DO NOTHING
            """,
            ts, feed_ok, instruments, bars,
        )

    async def instruments_recorded_since(self, since: datetime) -> list[dict]:
        """The instruments that have a bar since ``since``: what to keep recording after a restart."""
        rows = await self.db.fetch(
            f"""
            SELECT i.id, i.uic, i.asset_type, i.symbol, i.description, i.currency, i.role, i.indice,
                   i.underlying_uic, i.direction, i.barrier, i.issuer
            FROM price_instrument i
            WHERE EXISTS (SELECT 1 FROM {PRICE_BAR_TABLE} b WHERE b.instrument_id = i.id AND b.ts >= $1)
            """,
            since,
        )
        return [dict(row) for row in rows]

    async def bars_page(
        self,
        start: datetime,
        end: datetime,
        *,
        indice: str | None = None,
        after: tuple[datetime, int] | None = None,
        limit: int = 20000,
    ) -> list[dict]:
        """
        Bars of [start, end) with their instrument, oldest first, one page at a
        time: pass the (ts, instrument_id) of the last row as ``after`` for the next.
        """
        after_ts, after_id = after if after is not None else (start, -1)
        rows = await self.db.fetch(
            f"""
            SELECT b.ts, b.instrument_id, i.uic, i.asset_type, i.symbol, i.description, i.role, i.indice,
                   i.direction, i.barrier, i.issuer,
                   b.bid_open, b.bid_high, b.bid_low, b.bid_close,
                   b.ask_open, b.ask_high, b.ask_low, b.ask_close, b.updates, b.delayed_minutes
            FROM {PRICE_BAR_TABLE} b
            JOIN price_instrument i ON i.id = b.instrument_id
            WHERE b.ts >= $1 AND b.ts < $2
              AND ($3::text IS NULL OR i.indice = $3)
              AND (b.ts, b.instrument_id) > ($4::timestamptz, $5::int4)
            ORDER BY b.ts, b.instrument_id
            LIMIT $6
            """,
            start, end, indice, after_ts, after_id, limit,
        )
        return [dict(row) for row in rows]

