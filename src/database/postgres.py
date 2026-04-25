# src/database/postgres.py
"""
Async PostgreSQL database layer for WATA.
Replaces DuckDB for the write path (Trader, Position Monitor).
Uses asyncpg connection pool for high-performance async I/O.
"""

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Any

import asyncpg

from src.configuration import ConfigurationManager

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
#  Connection Pool Manager
# ──────────────────────────────────────────────

class PostgresConnectionManager:
    """Manages an asyncpg connection pool (singleton-friendly)."""

    def __init__(self, dsn: str, min_size: int = 2, max_size: int = 10):
        self._dsn = dsn
        self._min_size = min_size
        self._max_size = max_size
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

-- Indexes for common queries
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
            data["action"],
            data["buy_sell"],
            data["order_id"],
            data["order_amount"],
            data["order_type"],
            data["order_kind"],
            data.get("order_submit_time") or data.get("order_time"),
            data.get("related_order_id", []),
            data["position_id"],
            data["instrument_name"],
            data["instrument_symbol"],
            data["instrument_uic"],
            data["instrument_price"],
            data["instrument_currency"],
            data.get("order_cost"),
        )


class AsyncDbPositionManager:
    """Async position persistence using PostgreSQL."""

    def __init__(self, conn_mgr: PostgresConnectionManager):
        self.db = conn_mgr

    @staticmethod
    def _today() -> date:
        return date.today()

    async def insert_turbo_open_position_data(self, data: dict):
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
            data["action"],
            data["position_id"],
            data["position_amount"],
            data["position_open_price"],
            data["position_total_open_price"],
            data.get("position_status", "Open"),
            data["position_kind"],
            data.get("execution_time_open"),
            data["order_id"],
            data.get("related_order_id", []),
            data["instrument_name"],
            data["instrument_symbol"],
            data["instrument_uic"],
            data["instrument_currency"],
        )

    async def update_turbo_position_data(self, position_id: str, update_data: dict):
        if not update_data:
            return
        set_parts = []
        values = []
        for idx, (key, val) in enumerate(update_data.items(), start=1):
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
        await self.db.execute(
            """
            INSERT INTO trade_performance (date_day, perf_day_real, money_made_real, trade_number_real, max_perf_day_simulated)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (date_day) DO NOTHING
            """,
            data["date_day"],
            data["perf_day_real"],
            data["money_made_real"],
            data["trade_number_real"],
            data.get("max_perf_day_simulated"),
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
