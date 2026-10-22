# src/database/postgres.py
"""
Async PostgreSQL database layer for WATA.
Replaces DuckDB for the write path (Trader, Position Monitor).
Uses asyncpg connection pool for high-performance async I/O.
"""

import asyncio
import json
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

    async def copy_from_query(self, query: str, *args, output, **options):
        """The rows of ``query`` as COPY writes them (``format``, ``header``...) to ``output``: a path, a file or a coroutine function."""
        async with self.pool.acquire() as connection:
            return await connection.copy_from_query(query, *args, output=output, **options)


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

-- What became of each order sent to Saxo, and when, as the service that sent it
-- saw it. Written in the background for the admin interface; nothing trades on it.
CREATE TABLE IF NOT EXISTS order_execution (
    order_id        VARCHAR(64) PRIMARY KEY,
    purpose         VARCHAR(8),
    position_id     VARCHAR(64),
    sent_at         TIMESTAMPTZ,
    accepted_at     TIMESTAMPTZ,
    confirmed_at    TIMESTAMPTZ,
    gave_up_at      TIMESTAMPTZ,
    lookups         INTEGER,
    outcome         VARCHAR(16),
    cancel_accepted BOOLEAN,
    detail          TEXT,
    recorded_by     VARCHAR(32)
);

-- Every request sent to Saxo's order endpoint (an order placed, orders
-- cancelled) and what Saxo answered, accepted or not, as the service that sent
-- it saw it. One row per request, written once its answer is known, in the
-- background, for the admin interface; nothing trades on it.
CREATE TABLE IF NOT EXISTS order_request (
    request_id          VARCHAR(36) PRIMARY KEY,
    requested_at        TIMESTAMPTZ,
    answered_at         TIMESTAMPTZ,
    recorded_by         VARCHAR(32),
    operation           VARCHAR(8),
    purpose             VARCHAR(8),
    position_id         VARCHAR(64),
    signal_uuid         VARCHAR(64),
    action              TEXT,
    reason              TEXT,
    instrument_uic      INTEGER,
    asset_type          TEXT,
    instrument_name     TEXT,
    instrument_symbol   TEXT,
    instrument_currency TEXT,
    buy_sell            VARCHAR(8),
    amount              DOUBLE PRECISION,
    order_type          TEXT,
    price_seen          DOUBLE PRECISION,
    outcome             VARCHAR(16),
    order_id            TEXT,
    http_status         INTEGER,
    error_code          TEXT,
    error_message       TEXT,
    request_body        TEXT,
    response_body       TEXT
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
CREATE INDEX IF NOT EXISTS idx_order_request_requested_at ON order_request (requested_at);
CREATE INDEX IF NOT EXISTS idx_order_request_position ON order_request (position_id);
"""

# The columns of order_request, in the order of its table: the writer and the admin read the same list.
ORDER_REQUEST_COLUMNS = (
    "request_id", "requested_at", "answered_at", "recorded_by", "operation", "purpose", "position_id",
    "signal_uuid", "action", "reason", "instrument_uic", "asset_type", "instrument_name", "instrument_symbol",
    "instrument_currency", "buy_sell", "amount", "order_type", "price_seen", "outcome", "order_id",
    "http_status", "error_code", "error_message", "request_body", "response_body",
)


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


class AsyncDbOrderExecutionManager:
    """What became of each order sent to Saxo (see order_execution)."""

    def __init__(self, conn_mgr: PostgresConnectionManager):
        self.db = conn_mgr

    async def record(self, entry: dict):
        """
        Keep what is known of an order. It is written twice, once Saxo has it
        and once its outcome is known, both in the background: they may arrive
        in either order, so a row that has its outcome is never changed.
        """
        await self.db.execute(
            """
            INSERT INTO order_execution
                (order_id, purpose, position_id, sent_at, accepted_at, confirmed_at,
                 gave_up_at, lookups, outcome, cancel_accepted, detail, recorded_by)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
            ON CONFLICT (order_id) DO UPDATE SET
                position_id = EXCLUDED.position_id,
                confirmed_at = EXCLUDED.confirmed_at,
                gave_up_at = EXCLUDED.gave_up_at,
                lookups = EXCLUDED.lookups,
                outcome = EXCLUDED.outcome,
                cancel_accepted = EXCLUDED.cancel_accepted,
                detail = EXCLUDED.detail
            WHERE order_execution.outcome IS NULL
            """,
            entry["order_id"],
            entry.get("purpose"),
            entry.get("position_id"),
            entry.get("sent_at"),
            entry.get("accepted_at"),
            entry.get("confirmed_at"),
            entry.get("gave_up_at"),
            entry.get("lookups"),
            entry.get("outcome"),
            entry.get("cancel_accepted"),
            entry.get("detail"),
            entry.get("recorded_by"),
        )


class AsyncDbOrderRequestManager:
    """Every request sent to Saxo's order endpoint and its answer (see order_request)."""

    def __init__(self, conn_mgr: PostgresConnectionManager):
        self.db = conn_mgr

    async def record(self, entry: dict):
        """Keep one request. It is written once, its answer known: writing it again changes nothing."""
        placeholders = ", ".join(f"${index}" for index in range(1, len(ORDER_REQUEST_COLUMNS) + 1))
        await self.db.execute(
            f"""
            INSERT INTO order_request ({", ".join(ORDER_REQUEST_COLUMNS)})
            VALUES ({placeholders})
            ON CONFLICT (request_id) DO NOTHING
            """,
            *(entry.get(column) for column in ORDER_REQUEST_COLUMNS),
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

-- What Saxo counts as traded on an instrument: its total of the day, one row
-- each time it changes (ts is the start of the ten seconds it changed in).
-- Only filled when the recorder is asked to (record_volume), and beside the
-- bars, not in them: a turbo trades a few times a day.
CREATE TABLE IF NOT EXISTS price_volume (
    instrument_id  INTEGER NOT NULL,
    ts             TIMESTAMPTZ NOT NULL,
    day_volume     DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (instrument_id, ts)
);
CREATE INDEX IF NOT EXISTS idx_price_volume_ts ON price_volume (ts);
"""

PRICE_BAR_TABLE = "price_bar_10s"


async def init_price_history_schema(conn_mgr: PostgresConnectionManager):
    await conn_mgr.execute(PRICE_HISTORY_SCHEMA_SQL)


def _month_start(day: date, months_later: int = 0) -> date:
    months = day.year * 12 + (day.month - 1) + months_later
    return date(months // 12, months % 12 + 1, 1)


def month_partition(table: str, day: date) -> tuple[str, date, date]:
    """Name of the monthly partition of ``table`` holding ``day``, and its bounds (first day in, first day out)."""
    start = _month_start(day)
    return f"{table}_y{start.year:04d}m{start.month:02d}", start, _month_start(day, 1)


def price_bar_partition(day: date) -> tuple[str, date, date]:
    """Name of the monthly partition of the recorder's bars holding ``day``, and its bounds."""
    return month_partition(PRICE_BAR_TABLE, day)


BAR_COLUMNS = (
    "ts", "instrument_id", "bid_open", "bid_high", "bid_low", "bid_close",
    "ask_open", "ask_high", "ask_low", "ask_close", "updates", "delayed_minutes",
)


async def insert_bar_rows(db, table: str, bars: list[tuple]) -> None:
    """Write ten-second bars (tuples in BAR_COLUMNS order) in one statement. A bar already there is left as it is."""
    if not bars:
        return
    columns = list(zip(*bars))
    await db.execute(
        f"""
        INSERT INTO {table} ({", ".join(BAR_COLUMNS)})
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


async def ensure_month_partitions(db, table: str, today: date, months_ahead: int = 1) -> list[str]:
    """Create the partitions of ``table`` for this month and the next ones, so that a bar never arrives without one."""
    names = []
    for offset in range(months_ahead + 1):
        name, start, end = month_partition(table, _month_start(today, offset))
        # Bounds in UTC whatever the session's timezone: a bucket belongs to the month it starts in, in UTC.
        await db.execute(
            f"CREATE TABLE IF NOT EXISTS {name} PARTITION OF {table} "
            f"FOR VALUES FROM ('{start.isoformat()} 00:00:00+00') TO ('{end.isoformat()} 00:00:00+00')"
        )
        names.append(name)
    return names


async def drop_month_partitions(db, table: str, today: date, months: int) -> tuple[list[str], datetime | None]:
    """
    Remove the partitions of ``table`` for the months that ended more than
    ``months`` months ago. Returns their names and the first moment kept
    (None when nothing was dropped), for the tables kept beside them.
    """
    if months <= 0:
        return [], None
    cutoff = _month_start(today, -months)
    pattern = re.compile(rf"^{re.escape(table)}_y(\d{{4}})m(\d{{2}})$")
    rows = await db.fetch(
        "SELECT child.relname AS name FROM pg_inherits "
        "JOIN pg_class parent ON pg_inherits.inhparent = parent.oid "
        "JOIN pg_class child ON pg_inherits.inhrelid = child.oid "
        "WHERE parent.relname = $1",
        table,
    )
    dropped = []
    for row in rows:
        match = pattern.match(row["name"])
        if not match:
            continue
        if date(int(match.group(1)), int(match.group(2)), 1) < cutoff:
            await db.execute(f"DROP TABLE IF EXISTS {row['name']}")
            dropped.append(row["name"])
    if not dropped:
        return [], None
    return dropped, datetime(cutoff.year, cutoff.month, 1, tzinfo=timezone.utc)


class AsyncDbPriceHistoryManager:
    """Instruments, bars and coverage written by the price recorder (see PRICE_HISTORY_SCHEMA_SQL)."""

    BAR_COLUMNS = BAR_COLUMNS

    def __init__(self, conn_mgr: PostgresConnectionManager):
        self.db = conn_mgr

    async def ensure_partitions(self, today: date, months_ahead: int = 1) -> list[str]:
        """Create the partitions of this month and the next ones, so that a bar never arrives without one."""
        return await ensure_month_partitions(self.db, PRICE_BAR_TABLE, today, months_ahead)

    async def drop_partitions_older_than(self, today: date, months: int) -> list[str]:
        """
        Remove the bars of the months that ended more than ``months`` months ago
        (and their coverage). Only called when a retention is configured.
        """
        dropped, oldest_kept = await drop_month_partitions(self.db, PRICE_BAR_TABLE, today, months)
        if dropped:
            await self.db.execute("DELETE FROM price_recorder_coverage WHERE ts < $1", oldest_kept)
            await self.db.execute("DELETE FROM price_volume WHERE ts < $1", oldest_kept)
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
        await insert_bar_rows(self.db, PRICE_BAR_TABLE, bars)

    async def record_coverage(self, ts: datetime, feed_ok: bool, instruments: int, bars: int) -> None:
        await self.db.execute(
            """
            INSERT INTO price_recorder_coverage (ts, feed_ok, instruments, bars)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (ts) DO NOTHING
            """,
            ts, feed_ok, instruments, bars,
        )

    async def record_volumes(self, volumes: list[tuple]) -> None:
        """
        Write the day's totals that changed, as (instrument_id, ts, day_volume),
        in one statement. A total that changed again in the same ten seconds
        replaces the one written for them.
        """
        if not volumes:
            return
        columns = list(zip(*volumes))
        await self.db.execute(
            """
            INSERT INTO price_volume (instrument_id, ts, day_volume)
            SELECT * FROM unnest($1::int4[], $2::timestamptz[], $3::float8[])
            ON CONFLICT (instrument_id, ts) DO UPDATE SET day_volume = EXCLUDED.day_volume
            """,
            *[list(column) for column in columns],
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


# ──────────────────────────────────────────────
#  Capital.com feed
# ──────────────────────────────────────────────

# Created by the Capital.com feed alone (src/capital_feed), as the price
# recorder creates its own: no trading service uses these tables.
CAPITAL_SCHEMA_SQL = """
-- The instruments of Capital.com that are recorded. The epic is Capital.com's
-- name for one, the label WATA's (the indice of its rules, the folder of the
-- day files).
CREATE TABLE IF NOT EXISTS capital_instrument (
    id               SERIAL PRIMARY KEY,
    epic             VARCHAR(64) NOT NULL UNIQUE,
    label            VARCHAR(32) NOT NULL,
    name             TEXT,
    instrument_type  VARCHAR(32),
    currency         VARCHAR(8),
    decimals         SMALLINT,
    opening_hours    JSONB,
    first_seen_at    TIMESTAMPTZ NOT NULL,
    last_seen_at     TIMESTAMPTZ NOT NULL
);

-- Ten-second bars of the bid and the ask, in the columns of price_bar_10s and
-- made by the same rules (src/price_recorder/bars.py). ts is the start of the
-- bucket, updates the quote changes received in it.
CREATE TABLE IF NOT EXISTS capital_bar_10s (
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

-- One row per ten seconds the feed lived through, as price_recorder_coverage:
-- whether quotes were arriving, and how much it wrote. No row: not recorded.
CREATE TABLE IF NOT EXISTS capital_coverage (
    ts           TIMESTAMPTZ PRIMARY KEY,
    feed_ok      BOOLEAN NOT NULL,
    instruments  SMALLINT NOT NULL,
    bars         SMALLINT NOT NULL
);

-- Capital.com's own candle of each minute, with the volume it gives it
-- (lastTradedVolume): beside the bars, not in them.
CREATE TABLE IF NOT EXISTS capital_minute (
    instrument_id  INTEGER NOT NULL,
    ts             TIMESTAMPTZ NOT NULL,
    bid_open       DOUBLE PRECISION,
    bid_high       DOUBLE PRECISION,
    bid_low        DOUBLE PRECISION,
    bid_close      DOUBLE PRECISION,
    ask_open       DOUBLE PRECISION,
    ask_high       DOUBLE PRECISION,
    ask_low        DOUBLE PRECISION,
    ask_close      DOUBLE PRECISION,
    volume         DOUBLE PRECISION,
    PRIMARY KEY (instrument_id, ts)
);
CREATE INDEX IF NOT EXISTS idx_capital_minute_ts ON capital_minute (ts);

-- What the feed is doing, written again every ten seconds: one row, which the
-- admin reads (its age says whether the feed runs at all).
CREATE TABLE IF NOT EXISTS capital_feed_state (
    id           SMALLINT PRIMARY KEY CHECK (id = 1),
    updated_at   TIMESTAMPTZ NOT NULL,
    started_at   TIMESTAMPTZ NOT NULL,
    version      VARCHAR(32),
    environment  VARCHAR(8),
    state        VARCHAR(32) NOT NULL,
    detail       TEXT,
    figures      JSONB NOT NULL DEFAULT '{}'::jsonb
);
"""

CAPITAL_BAR_TABLE = "capital_bar_10s"


async def init_capital_schema(conn_mgr: PostgresConnectionManager):
    await conn_mgr.execute(CAPITAL_SCHEMA_SQL)


class AsyncDbCapitalManager:
    """Instruments, bars, coverage, minutes and state written by the Capital.com feed (see CAPITAL_SCHEMA_SQL)."""

    BAR_COLUMNS = BAR_COLUMNS
    MINUTE_COLUMNS = (
        "instrument_id", "ts", "bid_open", "bid_high", "bid_low", "bid_close",
        "ask_open", "ask_high", "ask_low", "ask_close", "volume",
    )

    def __init__(self, conn_mgr: PostgresConnectionManager):
        self.db = conn_mgr

    async def ensure_partitions(self, today: date, months_ahead: int = 1) -> list[str]:
        return await ensure_month_partitions(self.db, CAPITAL_BAR_TABLE, today, months_ahead)

    async def drop_partitions_older_than(self, today: date, months: int) -> list[str]:
        """Remove the bars of the months that ended more than ``months`` months ago, with their coverage and minutes."""
        dropped, oldest_kept = await drop_month_partitions(self.db, CAPITAL_BAR_TABLE, today, months)
        if dropped:
            await self.db.execute("DELETE FROM capital_coverage WHERE ts < $1", oldest_kept)
            await self.db.execute("DELETE FROM capital_minute WHERE ts < $1", oldest_kept)
        return dropped

    async def upsert_instrument(self, instrument: dict, seen_at: datetime) -> int:
        """Record (or refresh) an instrument; returns its id. What Capital.com did not say this time is kept."""
        opening_hours = instrument.get("opening_hours")
        return await self.db.fetchval(
            """
            INSERT INTO capital_instrument (
                epic, label, name, instrument_type, currency, decimals, opening_hours, first_seen_at, last_seen_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $8)
            ON CONFLICT (epic) DO UPDATE SET
                label = EXCLUDED.label,
                name = COALESCE(EXCLUDED.name, capital_instrument.name),
                instrument_type = COALESCE(EXCLUDED.instrument_type, capital_instrument.instrument_type),
                currency = COALESCE(EXCLUDED.currency, capital_instrument.currency),
                decimals = COALESCE(EXCLUDED.decimals, capital_instrument.decimals),
                opening_hours = COALESCE(EXCLUDED.opening_hours, capital_instrument.opening_hours),
                last_seen_at = EXCLUDED.last_seen_at
            RETURNING id
            """,
            instrument["epic"], instrument["label"], instrument.get("name"), instrument.get("instrument_type"),
            instrument.get("currency"), instrument.get("decimals"),
            None if opening_hours is None else json.dumps(opening_hours), seen_at,
        )

    async def insert_bars(self, bars: list[tuple]) -> None:
        await insert_bar_rows(self.db, CAPITAL_BAR_TABLE, bars)

    async def record_coverage(self, ts: datetime, feed_ok: bool, instruments: int, bars: int) -> None:
        await self.db.execute(
            """
            INSERT INTO capital_coverage (ts, feed_ok, instruments, bars)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (ts) DO NOTHING
            """,
            ts, feed_ok, instruments, bars,
        )

    async def upsert_minutes(self, minutes: list[tuple]) -> None:
        """
        Write Capital.com's candles of some minutes (tuples in MINUTE_COLUMNS
        order) in one statement. A minute fetched again replaces the one kept:
        the last minute can still change when it is first fetched.
        """
        if not minutes:
            return
        columns = list(zip(*minutes))
        await self.db.execute(
            f"""
            INSERT INTO capital_minute ({", ".join(self.MINUTE_COLUMNS)})
            SELECT * FROM unnest(
                $1::int4[], $2::timestamptz[],
                $3::float8[], $4::float8[], $5::float8[], $6::float8[],
                $7::float8[], $8::float8[], $9::float8[], $10::float8[],
                $11::float8[]
            )
            ON CONFLICT (instrument_id, ts) DO UPDATE SET
                bid_open = EXCLUDED.bid_open, bid_high = EXCLUDED.bid_high,
                bid_low = EXCLUDED.bid_low, bid_close = EXCLUDED.bid_close,
                ask_open = EXCLUDED.ask_open, ask_high = EXCLUDED.ask_high,
                ask_low = EXCLUDED.ask_low, ask_close = EXCLUDED.ask_close,
                volume = EXCLUDED.volume
            """,
            *[list(column) for column in columns],
        )

    async def write_state(self, state: dict) -> None:
        """The one row saying what the feed is doing (CAPITAL_SCHEMA_SQL)."""
        await self.db.execute(
            """
            INSERT INTO capital_feed_state (id, updated_at, started_at, version, environment, state, detail, figures)
            VALUES (1, $1, $2, $3, $4, $5, $6, $7::jsonb)
            ON CONFLICT (id) DO UPDATE SET
                updated_at = EXCLUDED.updated_at, started_at = EXCLUDED.started_at,
                version = EXCLUDED.version, environment = EXCLUDED.environment,
                state = EXCLUDED.state, detail = EXCLUDED.detail, figures = EXCLUDED.figures
            """,
            state["updated_at"], state["started_at"], state.get("version"), state.get("environment"),
            state["state"], state.get("detail"), json.dumps(state.get("figures") or {}),
        )

    async def first_bar_at(self, instrument_id: int) -> datetime | None:
        """The start of an instrument's first bar, None without one: reached through the key, never scanned."""
        return await self.db.fetchval(
            f"SELECT ts FROM {CAPITAL_BAR_TABLE} WHERE instrument_id = $1 ORDER BY ts LIMIT 1",
            instrument_id,
        )

    DAY_FILE_COLUMNS = (
        "ts", "bid_open", "bid_high", "bid_low", "bid_close", "ask_open", "ask_high", "ask_low", "ask_close", "updates",
    )

    async def bars_between(self, instrument_id: int, start: datetime, end: datetime) -> list[tuple]:
        """The bars of one instrument in [start, end), oldest first, as tuples of DAY_FILE_COLUMNS."""
        rows = await self.db.fetch(
            f"""
            SELECT {", ".join(self.DAY_FILE_COLUMNS)}
            FROM {CAPITAL_BAR_TABLE}
            WHERE instrument_id = $1 AND ts >= $2 AND ts < $3
            ORDER BY ts
            """,
            instrument_id, start, end,
        )
        return [tuple(row[name] for name in self.DAY_FILE_COLUMNS) for row in rows]



# ──────────────────────────────────────────────
#  TA engine
# ──────────────────────────────────────────────

# Created by the TA engine alone (src/ta_engine), as the Capital.com feed creates
# its own: the trading services do not use these tables, and the admin only reads them.
TA_SCHEMA_SQL = """
-- Each alert of a signal rule's script that its rule turns into a signal, as
-- the TA engine raised it: journaled before anything is sent, then completed
-- with what the web server answered when the rule is live. One row per rule,
-- bar and alert: a bar evaluated again adds nothing, and sends nothing.
CREATE TABLE IF NOT EXISTS ta_signal (
    id              BIGSERIAL PRIMARY KEY,
    rule_id         VARCHAR(12) NOT NULL,
    rule_name       TEXT NOT NULL,
    script_id       VARCHAR(12) NOT NULL,
    script_version  INTEGER NOT NULL,
    asset           VARCHAR(80) NOT NULL,
    side            VARCHAR(4) NOT NULL,
    bars            VARCHAR(16) NOT NULL,
    bar_time        TIMESTAMPTZ NOT NULL,
    bar_closed_at   TIMESTAMPTZ NOT NULL,
    alert_key       TEXT NOT NULL,
    alert_title     TEXT,
    alert_message   TEXT,
    action          VARCHAR(32),
    indice          VARCHAR(64) NOT NULL,
    confidence      DOUBLE PRECISION,
    mode            VARCHAR(8) NOT NULL,
    raised_at       TIMESTAMPTZ NOT NULL,
    outcome         VARCHAR(16) NOT NULL,
    sent_at         TIMESTAMPTZ,
    http_status     INTEGER,
    signal_uuid     VARCHAR(64),
    detail          TEXT,
    UNIQUE (rule_id, bar_time, alert_key)
);
CREATE INDEX IF NOT EXISTS idx_ta_signal_raised_at ON ta_signal (raised_at);
CREATE INDEX IF NOT EXISTS idx_ta_signal_signal_uuid ON ta_signal (signal_uuid);

-- What the engine is doing, written again every ten seconds: one row, as
-- capital_feed_state (its age says whether the engine runs at all).
CREATE TABLE IF NOT EXISTS ta_engine_state (
    id           SMALLINT PRIMARY KEY CHECK (id = 1),
    updated_at   TIMESTAMPTZ NOT NULL,
    started_at   TIMESTAMPTZ NOT NULL,
    version      VARCHAR(32),
    state        VARCHAR(32) NOT NULL,
    detail       TEXT,
    figures      JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- Each rule as the engine runs it, one row per rule of rules.json.
CREATE TABLE IF NOT EXISTS ta_rule_state (
    rule_id         VARCHAR(12) PRIMARY KEY,
    updated_at      TIMESTAMPTZ NOT NULL,
    state           VARCHAR(16) NOT NULL,
    detail          TEXT,
    mode            VARCHAR(8) NOT NULL,
    script_version  INTEGER,
    bars            INTEGER,
    last_bar_time   TIMESTAMPTZ,
    last_alert_at   TIMESTAMPTZ,
    figures         JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- Each tick of the engine as it measured it (src/ta_engine/monitoring.py), kept
-- trade.config.ta_engine.monitoring_days: the engine removes the older rows.
CREATE TABLE IF NOT EXISTS ta_engine_tick (
    tick_at               TIMESTAMPTZ PRIMARY KEY,
    ended_at              TIMESTAMPTZ NOT NULL,
    milliseconds          INTEGER NOT NULL,
    read_ms               INTEGER NOT NULL DEFAULT 0,
    rows_read             INTEGER NOT NULL DEFAULT 0,
    write_ms              INTEGER NOT NULL DEFAULT 0,
    covered_until         TIMESTAMPTZ,
    rules_on              SMALLINT NOT NULL DEFAULT 0,
    runs                  SMALLINT NOT NULL DEFAULT 0,
    loads                 SMALLINT NOT NULL DEFAULT 0,
    failed                SMALLINT NOT NULL DEFAULT 0,
    deferred_rules        TEXT[] NOT NULL DEFAULT '{}',
    skipped               SMALLINT NOT NULL DEFAULT 0,
    restarted             BOOLEAN NOT NULL DEFAULT false,
    error                 TEXT,
    runner_starts         INTEGER,
    shadow_runner_starts  INTEGER,
    live_rss_mb           REAL,
    live_peak_mb          REAL,
    shadow_rss_mb         REAL,
    shadow_peak_mb        REAL,
    engine_rss_mb         REAL,
    memory_used_mb        REAL,
    memory_limit_mb       REAL,
    oom_kills             INTEGER,
    figures               JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- Each run of a rule's script (its history loaded, or its new bars), kept as
-- ta_engine_tick. A signal's run is its rule's whose tick_at is its raised_at.
CREATE TABLE IF NOT EXISTS ta_rule_run (
    rule_id         VARCHAR(12) NOT NULL,
    tick_at         TIMESTAMPTZ NOT NULL,
    mode            VARCHAR(8) NOT NULL,
    runner          VARCHAR(8) NOT NULL,
    kind            VARCHAR(8) NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL,
    ended_at        TIMESTAMPTZ NOT NULL,
    done_at         TIMESTAMPTZ NOT NULL,
    milliseconds    INTEGER NOT NULL,
    pinets_ms       INTEGER,
    bars_given      INTEGER NOT NULL,
    bars_held       INTEGER,
    bar_closed_at   TIMESTAMPTZ,
    asks            SMALLINT NOT NULL DEFAULT 0,
    asks_ms         INTEGER NOT NULL DEFAULT 0,
    alerts          INTEGER NOT NULL DEFAULT 0,
    signals         SMALLINT NOT NULL DEFAULT 0,
    outcome         VARCHAR(8) NOT NULL,
    detail          TEXT,
    figures         JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (rule_id, tick_at)
);
CREATE INDEX IF NOT EXISTS idx_ta_rule_run_tick_at ON ta_rule_run (tick_at);
"""

# The columns of ta_signal the engine writes when it raises a signal, in the order of its table.
TA_SIGNAL_COLUMNS = (
    "rule_id", "rule_name", "script_id", "script_version", "asset", "side", "bars", "bar_time", "bar_closed_at",
    "alert_key", "alert_title", "alert_message", "action", "indice", "confidence", "mode", "raised_at", "outcome",
    "detail",
)
TA_RULE_STATE_COLUMNS = (
    "rule_id", "updated_at", "state", "detail", "mode", "script_version", "bars", "last_bar_time", "last_alert_at",
    "figures",
)
# The columns of ta_engine_tick the engine writes (src/ta_engine/monitoring.py).
TA_TICK_COLUMNS = (
    "tick_at", "ended_at", "milliseconds", "read_ms", "rows_read", "write_ms", "covered_until", "rules_on", "runs",
    "loads", "failed", "deferred_rules", "skipped", "restarted", "error", "runner_starts", "shadow_runner_starts",
    "live_rss_mb", "live_peak_mb", "shadow_rss_mb", "shadow_peak_mb", "engine_rss_mb", "memory_used_mb",
    "memory_limit_mb", "oom_kills",
)
# ...and of ta_rule_run, each with its type as an array: the runs of many ticks go in one statement.
TA_RUN_COLUMNS = (
    ("rule_id", "text"), ("tick_at", "timestamptz"), ("mode", "text"), ("runner", "text"), ("kind", "text"),
    ("started_at", "timestamptz"), ("ended_at", "timestamptz"), ("done_at", "timestamptz"), ("milliseconds", "int"),
    ("pinets_ms", "int"), ("bars_given", "int"), ("bars_held", "int"), ("bar_closed_at", "timestamptz"),
    ("asks", "smallint"), ("asks_ms", "int"), ("alerts", "int"), ("signals", "smallint"), ("outcome", "text"),
    ("detail", "text"),
)


async def init_ta_schema(conn_mgr: PostgresConnectionManager):
    await conn_mgr.execute(TA_SCHEMA_SQL)


class AsyncDbTaManager:
    """The journal of the TA engine's signals and what it says of itself (see TA_SCHEMA_SQL)."""

    def __init__(self, conn_mgr: PostgresConnectionManager):
        self.db = conn_mgr

    async def journal_signal(self, signal: dict) -> int | None:
        """
        Journal a signal (keys of TA_SIGNAL_COLUMNS) and return its id; None when
        that rule's alert of that bar is already in the journal, so that it is
        never sent twice.
        """
        placeholders = ", ".join(f"${index}" for index in range(1, len(TA_SIGNAL_COLUMNS) + 1))
        return await self.db.fetchval(
            f"""
            INSERT INTO ta_signal ({", ".join(TA_SIGNAL_COLUMNS)})
            VALUES ({placeholders})
            ON CONFLICT (rule_id, bar_time, alert_key) DO NOTHING
            RETURNING id
            """,
            *(signal.get(column) for column in TA_SIGNAL_COLUMNS),
        )

    async def complete_signal(
        self,
        signal_id: int,
        outcome: str,
        *,
        sent_at: datetime | None = None,
        http_status: int | None = None,
        signal_uuid: str | None = None,
        detail: str | None = None,
    ) -> None:
        """What became of a journaled signal: sent, refused or failed, and what the web server answered."""
        await self.db.execute(
            """
            UPDATE ta_signal
            SET outcome = $2, sent_at = $3, http_status = $4, signal_uuid = $5, detail = COALESCE($6, detail)
            WHERE id = $1
            """,
            signal_id, outcome, sent_at, http_status, signal_uuid, detail,
        )

    async def write_state(self, state: dict) -> None:
        """The one row saying what the engine is doing (TA_SCHEMA_SQL)."""
        await self.db.execute(
            """
            INSERT INTO ta_engine_state (id, updated_at, started_at, version, state, detail, figures)
            VALUES (1, $1, $2, $3, $4, $5, $6::jsonb)
            ON CONFLICT (id) DO UPDATE SET
                updated_at = EXCLUDED.updated_at, started_at = EXCLUDED.started_at,
                version = EXCLUDED.version, state = EXCLUDED.state,
                detail = EXCLUDED.detail, figures = EXCLUDED.figures
            """,
            state["updated_at"], state["started_at"], state.get("version"), state["state"], state.get("detail"),
            json.dumps(state.get("figures") or {}),
        )

    async def write_rule_states(self, states: list[dict]) -> None:
        """Each rule as the engine runs it; the rows of rules no longer in the file are removed."""
        await self.db.execute(
            "DELETE FROM ta_rule_state WHERE NOT (rule_id = ANY($1::text[]))",
            [state["rule_id"] for state in states],
        )
        for state in states:
            await self.db.execute(
                f"""
                INSERT INTO ta_rule_state ({", ".join(TA_RULE_STATE_COLUMNS)})
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb)
                ON CONFLICT (rule_id) DO UPDATE SET
                    updated_at = EXCLUDED.updated_at, state = EXCLUDED.state, detail = EXCLUDED.detail,
                    mode = EXCLUDED.mode, script_version = EXCLUDED.script_version, bars = EXCLUDED.bars,
                    last_bar_time = EXCLUDED.last_bar_time, last_alert_at = EXCLUDED.last_alert_at,
                    figures = EXCLUDED.figures
                """,
                *(state.get(column) for column in TA_RULE_STATE_COLUMNS[:-1]),
                json.dumps(state.get("figures") or {}),
            )

    async def write_monitoring(self, ticks: list[dict], runs: list[dict]) -> None:
        """
        What the engine measured of its ticks and runs (keys of TA_TICK_COLUMNS, a tick's ``figures``,
        TA_RUN_COLUMNS); a row there stays.
        """
        placeholders = ", ".join(f"${index}" for index in range(1, len(TA_TICK_COLUMNS) + 1))
        for tick in ticks:
            # One a statement: a tick's deferred rules are an array, which unnest would flatten.
            await self.db.execute(
                f"""
                INSERT INTO ta_engine_tick ({", ".join(TA_TICK_COLUMNS)}, figures)
                VALUES ({placeholders}, ${len(TA_TICK_COLUMNS) + 1}::jsonb)
                ON CONFLICT (tick_at) DO NOTHING
                """,
                *(tick.get(column) for column in TA_TICK_COLUMNS),
                json.dumps(tick.get("figures") or {}),
            )
        if not runs:
            return
        arrays = ", ".join(f"${index}::{kind}[]" for index, (_, kind) in enumerate(TA_RUN_COLUMNS, start=1))
        await self.db.execute(
            f"""
            INSERT INTO ta_rule_run ({", ".join(name for name, _ in TA_RUN_COLUMNS)})
            SELECT * FROM unnest({arrays})
            ON CONFLICT (rule_id, tick_at) DO NOTHING
            """,
            *([run.get(name) for run in runs] for name, _ in TA_RUN_COLUMNS),
        )

    async def purge_monitoring(self, before: datetime) -> int:
        """
        Remove what the engine measured before ``before``, a day at a time so that no statement holds
        for long (a month of a rule on ten-second bars is 250,000 runs); the number of rows removed.
        """
        oldest = await self.db.fetchval(
            "SELECT least((SELECT min(tick_at) FROM ta_engine_tick), (SELECT min(tick_at) FROM ta_rule_run))"
        )
        removed = 0
        while oldest is not None and oldest < before:
            step = min(oldest + timedelta(days=1), before)
            for table in ("ta_rule_run", "ta_engine_tick"):
                status = await self.db.execute(f"DELETE FROM {table} WHERE tick_at < $1", step)
                removed += _deleted(status)
            oldest = step
        return removed


def _deleted(status) -> int:
    """The rows a DELETE removed, from its status ("DELETE 12"); 0 when it says nothing."""
    try:
        return int(str(status).split()[-1])
    except (ValueError, IndexError):
        return 0
