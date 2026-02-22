"""
DuckDB → PostgreSQL migration tool.

Reads all data from the existing DuckDB database and inserts it into PostgreSQL.
Designed to be run once during the migration from the old stack to the new async stack.

Usage:
    WATA_CONFIG_PATH=/app/etc/config.json python -m src.database.migration
"""

import asyncio
import json
import logging
import os
import sys

import duckdb

from src.configuration import ConfigurationManager
from src.database.postgres import PostgresConnectionManager, init_schema
from src.logging_helper import setup_logging

logger = logging.getLogger(__name__)


def read_duckdb_table(conn: duckdb.DuckDBPyConnection, table_name: str) -> list[dict]:
    """Read all rows from a DuckDB table as a list of dicts."""
    try:
        result = conn.execute(f"SELECT * FROM {table_name}").fetchdf()
        records = result.to_dict(orient="records")
        logger.info("Read %d rows from DuckDB table '%s'", len(records), table_name)
        return records
    except Exception as e:
        logger.warning("Could not read DuckDB table '%s': %s", table_name, e)
        return []


async def migrate_orders(pg: PostgresConnectionManager, rows: list[dict]):
    """Migrate turbo_data_order rows to PostgreSQL."""
    for row in rows:
        try:
            await pg.execute(
                """INSERT INTO turbo_data_order
                   (action, buy_sell, order_id, order_amount, order_type, order_kind,
                    order_submit_time, related_order_id, position_id,
                    instrument_name, instrument_symbol, instrument_uic,
                    instrument_price, instrument_currency, order_cost)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
                   ON CONFLICT (order_id) DO NOTHING""",
                row.get("action"), row.get("buy_sell"), row.get("order_id"),
                row.get("order_amount"), row.get("order_type"), row.get("order_kind"),
                row.get("order_submit_time"),
                row.get("related_order_id", []),
                row.get("position_id"),
                row.get("instrument_name"), row.get("instrument_symbol"),
                row.get("instrument_uic"), row.get("instrument_price"),
                row.get("instrument_currency"), row.get("order_cost"),
            )
        except Exception as e:
            logger.error("Failed to migrate order row %s: %s", row.get("order_id"), e)


async def migrate_positions(pg: PostgresConnectionManager, rows: list[dict]):
    """Migrate turbo_data_position rows to PostgreSQL."""
    for row in rows:
        try:
            await pg.execute(
                """INSERT INTO turbo_data_position
                   (action, position_id, position_amount, position_open_price,
                    position_total_open_price, position_close_price,
                    position_total_close_price, position_profit_loss,
                    position_total_performance_percent,
                    position_max_performance_percent,
                    position_status, position_kind, position_close_reason,
                    execution_time_open, execution_time_close,
                    order_id, related_order_id,
                    instrument_name, instrument_symbol,
                    instrument_uic, instrument_currency)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21)
                   ON CONFLICT (position_id) DO NOTHING""",
                row.get("action"), row.get("position_id"),
                row.get("position_amount"), row.get("position_open_price"),
                row.get("position_total_open_price"), row.get("position_close_price"),
                row.get("position_total_close_price"), row.get("position_profit_loss"),
                row.get("position_total_performance_percent"),
                row.get("position_max_performance_percent"),
                row.get("position_status"), row.get("position_kind"),
                row.get("position_close_reason"),
                row.get("execution_time_open"), row.get("execution_time_close"),
                row.get("order_id"),
                row.get("related_order_id", []),
                row.get("instrument_name"), row.get("instrument_symbol"),
                row.get("instrument_uic"), row.get("instrument_currency"),
            )
        except Exception as e:
            logger.error("Failed to migrate position row %s: %s", row.get("position_id"), e)


async def migrate_trade_performance(pg: PostgresConnectionManager, rows: list[dict]):
    """Migrate trade_performance rows to PostgreSQL."""
    for row in rows:
        try:
            await pg.execute(
                """INSERT INTO trade_performance
                   (date, total_performance_percent, total_performance_percent_on_max,
                    best_performance_percent, best_performance_percent_on_max,
                    trade_count, stoploss_count, takeprofit_count, trailing_stop_count)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                   ON CONFLICT (date) DO NOTHING""",
                row.get("date"), row.get("total_performance_percent"),
                row.get("total_performance_percent_on_max"),
                row.get("best_performance_percent"),
                row.get("best_performance_percent_on_max"),
                row.get("trade_count"), row.get("stoploss_count"),
                row.get("takeprofit_count"), row.get("trailing_stop_count"),
            )
        except Exception as e:
            logger.error("Failed to migrate trade_performance row %s: %s", row.get("date"), e)


async def migrate_tokens(pg: PostgresConnectionManager, rows: list[dict]):
    """Migrate auth_tokens rows to PostgreSQL."""
    for row in rows:
        try:
            await pg.execute(
                """INSERT INTO auth_tokens (token_id, encrypted_data, updated_at)
                   VALUES ($1, $2, NOW())
                   ON CONFLICT (token_id) DO UPDATE SET
                       encrypted_data = EXCLUDED.encrypted_data,
                       updated_at = NOW()""",
                row.get("token_id"),
                row.get("encrypted_data"),
            )
        except Exception as e:
            logger.error("Failed to migrate token row %s: %s", row.get("token_id"), e)


async def run_migration(config_manager: ConfigurationManager):
    """Main migration logic."""
    # Connect to DuckDB
    duckdb_path = config_manager.get_config_value("duckdb.persistant.db_path")
    if not os.path.exists(duckdb_path):
        logger.error("DuckDB file not found: %s", duckdb_path)
        print(f"ERROR: DuckDB file not found at {duckdb_path}")
        return

    logger.info("Opening DuckDB: %s", duckdb_path)
    duck = duckdb.connect(duckdb_path, read_only=True)

    # Connect to PostgreSQL
    logger.info("Connecting to PostgreSQL...")
    pg = await PostgresConnectionManager.from_config(config_manager)
    await init_schema(pg)

    try:
        # Read all DuckDB tables
        orders = read_duckdb_table(duck, "turbo_data_order")
        positions = read_duckdb_table(duck, "turbo_data_position")
        performances = read_duckdb_table(duck, "trade_performance")
        tokens = read_duckdb_table(duck, "auth_tokens")

        # Migrate to PostgreSQL
        logger.info("Migrating orders...")
        await migrate_orders(pg, orders)

        logger.info("Migrating positions...")
        await migrate_positions(pg, positions)

        logger.info("Migrating trade_performance...")
        await migrate_trade_performance(pg, performances)

        logger.info("Migrating tokens...")
        await migrate_tokens(pg, tokens)

        # Verify counts
        pg_orders = await pg.fetchval("SELECT COUNT(*) FROM turbo_data_order")
        pg_positions = await pg.fetchval("SELECT COUNT(*) FROM turbo_data_position")
        pg_perf = await pg.fetchval("SELECT COUNT(*) FROM trade_performance")
        pg_tokens = await pg.fetchval("SELECT COUNT(*) FROM auth_tokens")

        logger.info("=== Migration Complete ===")
        logger.info("Orders:       DuckDB=%d  PostgreSQL=%d", len(orders), pg_orders)
        logger.info("Positions:    DuckDB=%d  PostgreSQL=%d", len(positions), pg_positions)
        logger.info("Performance:  DuckDB=%d  PostgreSQL=%d", len(performances), pg_perf)
        logger.info("Tokens:       DuckDB=%d  PostgreSQL=%d", len(tokens), pg_tokens)

        print("\n=== Migration Complete ===")
        print(f"Orders:       DuckDB={len(orders)}  PostgreSQL={pg_orders}")
        print(f"Positions:    DuckDB={len(positions)}  PostgreSQL={pg_positions}")
        print(f"Performance:  DuckDB={len(performances)}  PostgreSQL={pg_perf}")
        print(f"Tokens:       DuckDB={len(tokens)}  PostgreSQL={pg_tokens}")

    finally:
        duck.close()
        await pg.close()


if __name__ == "__main__":
    config_path = os.getenv("WATA_CONFIG_PATH")
    if not config_path:
        print("FATAL: WATA_CONFIG_PATH not set", file=sys.stderr)
        sys.exit(1)

    config_manager = ConfigurationManager(config_path)
    setup_logging(config_manager, "wata-migration")

    print("Starting DuckDB → PostgreSQL migration...")
    asyncio.run(run_migration(config_manager))
