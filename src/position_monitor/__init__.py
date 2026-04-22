"""
Position Monitor Service — real-time WebSocket streaming + ``trading-ops`` queue.

Uses Saxo's WebSocket Streaming API for instant position monitoring
(replaces the 7-second polling approach).  Still consumes ``trading-ops``
for time-triggered events like ``daily_stats``.

Runs as a separate Docker container (WATA_APP_ROLE=position_monitor).
"""

import asyncio
import json
import logging
import os
import sys

import aio_pika

from src.configuration import ConfigurationManager
from src.logging_helper import setup_logging
from src.trade.async_services import (
    AsyncSaxoApiClient,
    AsyncOrderService,
    AsyncPositionService,
    AsyncPerformanceMonitor,
)
from src.database.postgres import (
    PostgresConnectionManager,
    AsyncDbPositionManager,
    AsyncDbTradePerformanceManager,
    init_schema,
)
from src.mq_telegram.async_tools import AsyncTelegramSender
from src.saxo_authen import SaxoAuth
from src.saxo_streaming.client import SaxoStreamClient
from src.trade.rules import TradingRule
from src.message_helper import generate_daily_stats_message, generate_performance_stats_message

logger = logging.getLogger(__name__)

APP_VERSION = "unknown"


def get_version() -> str:
    try:
        vf = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "VERSION")
        with open(vf, "r") as f:
            return f.read().strip()
    except Exception:
        return "unknown"


# ─────────────────────────────────────────────────────
#  Streaming callback — replaces the old 7-second poll
# ─────────────────────────────────────────────────────

async def on_positions_update(
    positions: dict[str, dict],
    performance_monitor: AsyncPerformanceMonitor,
    db_position_manager: AsyncDbPositionManager,
    telegram: AsyncTelegramSender,
):
    """
    Called by :class:`SaxoStreamClient` every time the position snapshot
    changes.  Runs the same SL/TP/trailing-stop checks that the old
    ``handle_check_positions`` did, but without any API polling.
    """
    if not positions:
        return

    try:
        perf_result = await performance_monitor.check_positions_from_stream(positions)
        perf_errors = perf_result.get("errors", 0)
        perf_closed = len(perf_result.get("closed_positions_processed", []))

        # DB sync is less critical with streaming (we see closures in real-time)
        # but we still reconcile to catch anything done externally
        sync_result = await performance_monitor.sync_db_positions_with_api()
        updates = sync_result.get("updates_for_db", [])

        sync_applied = 0
        sync_errors = 0
        for position_id, update_data in updates:
            try:
                await db_position_manager.update_turbo_position_data(position_id, update_data)
                sync_applied += 1
            except Exception as e:
                sync_errors += 1
                logger.critical("SYNC ERROR: Failed DB update for Pos %s: %s", position_id, e, exc_info=True)
                await telegram.send(f"CRITICAL SYNC ERROR: Failed DB update for Pos {position_id}: {e}")

        total_errors = perf_errors + sync_errors
        if total_errors > 0:
            logger.warning("Stream position check: closed=%d, synced=%d, errors=%d", perf_closed, sync_applied, total_errors)
        else:
            logger.debug("Stream position check: closed=%d, synced=%d, errors=%d", perf_closed, sync_applied, total_errors)

    except Exception as e:
        logger.error("Error in streaming position callback: %s", e, exc_info=True)


async def handle_daily_stats(
    db_position_manager: AsyncDbPositionManager,
    db_perf_manager: AsyncDbTradePerformanceManager,
    telegram: AsyncTelegramSender,
):
    """Generate and send daily performance stats."""
    days = 7
    stats = await db_position_manager.get_stats_of_the_day()
    message = generate_daily_stats_message(stats)

    results = await asyncio.gather(
        db_position_manager.get_percent_of_last_n_days(days),
        db_position_manager.get_best_percent_of_last_n_days(days),
        db_position_manager.get_theoretical_percent_of_last_n_days_on_max(days),
        db_position_manager.get_best_theoretical_percent_of_last_n_days_on_max(days),
    )
    message = generate_performance_stats_message(message, days, *results)
    await telegram.send(message)
    await db_perf_manager.create_last_day_trade_performance_data()
    logger.info("Daily stats sent.")


# ─────────────────────────────────────────────────────
#  Message dispatcher
# ─────────────────────────────────────────────────────

async def dispatch_ops_message(
    message: aio_pika.IncomingMessage,
    performance_monitor: AsyncPerformanceMonitor,
    db_position_manager: AsyncDbPositionManager,
    db_perf_manager: AsyncDbTradePerformanceManager,
    telegram: AsyncTelegramSender,
):
    async with message.process(requeue=False):
        try:
            body = json.loads(message.body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.error("Cannot decode message: %s", e)
            return

        action = body.get("action")
        logger.debug("Ops dispatch: action=%s", action)

        try:
            if action == "daily_stats":
                await handle_daily_stats(db_position_manager, db_perf_manager, telegram)
            elif action == "check_positions_on_saxo_api":
                # Legacy — streaming handles this now; run a one-off check as fallback
                logger.info("Legacy check_positions_on_saxo_api received — running REST fallback.")
                result = await performance_monitor.check_all_positions_performance()
                logger.info("REST fallback completed: %s", result)
            else:
                logger.warning("Unknown ops action: %s", action)

        except Exception as e:
            logger.error("Error processing ops action %s: %s", action, e, exc_info=True)
            await telegram.send(f"Position Monitor ERROR ({action}): {type(e).__name__}: {e}")


# ─────────────────────────────────────────────────────
#  Main
# ─────────────────────────────────────────────────────

async def main():
    global APP_VERSION
    APP_VERSION = get_version()

    config_path = os.getenv("WATA_CONFIG_PATH")
    if not config_path:
        print("FATAL: WATA_CONFIG_PATH not set", file=sys.stderr)
        sys.exit(10)

    config_manager = ConfigurationManager(config_path)
    setup_logging(config_manager, "wata-position-monitor")
    logger.info("--- Starting WATA Position Monitor v%s ---", APP_VERSION)

    telegram = AsyncTelegramSender(config_manager)
    await telegram.connect()

    stream_client: SaxoStreamClient | None = None
    pg = None
    api_client = None

    try:
        # PostgreSQL
        pg = PostgresConnectionManager.from_config(config_manager)
        await pg.connect()
        await init_schema(pg)
        db_position_manager = AsyncDbPositionManager(pg)
        db_perf_manager = AsyncDbTradePerformanceManager(pg)

        # Saxo API client
        saxo_auth = SaxoAuth(config_manager)
        api_client = AsyncSaxoApiClient(config_manager, saxo_auth)
        await api_client.ensure_ready()

        # Fetch account keys
        from src.trader import get_account_info_async
        acct = await get_account_info_async(api_client)
        account_key = acct.AccountKey
        client_key = acct.ClientKey

        # Services
        order_service = AsyncOrderService(api_client, account_key, client_key)
        position_service = AsyncPositionService(api_client, order_service, config_manager, account_key, client_key)
        trading_rule = TradingRule(config_manager, None)
        performance_monitor = AsyncPerformanceMonitor(
            position_service, order_service, config_manager,
            db_position_manager, trading_rule, telegram.send,
        )

        # ── Streaming configuration ──
        environment = config_manager.get_config_value("saxo_auth.env", "live")
        streaming_config = config_manager.get_config_value("trade.config.general.streaming", {})
        refresh_rate_ms = streaming_config.get("refresh_rate_ms", 1000)
        reconnect_delay = streaming_config.get("reconnect_delay_seconds", 1.0)
        max_reconnect_delay = streaming_config.get("max_reconnect_delay_seconds", 30.0)

        # Build the streaming callback (closes over service objects)
        async def _stream_callback(positions: dict[str, dict]):
            await on_positions_update(
                positions, performance_monitor, db_position_manager, telegram,
            )

        # Create streaming client
        stream_client = SaxoStreamClient(
            api_client=api_client._api,
            account_key=account_key,
            client_key=client_key,
            on_positions_update=_stream_callback,
            environment=environment,
            access_token_getter=lambda: saxo_auth.get_token(),
            refresh_rate_ms=refresh_rate_ms,
            reconnect_delay=reconnect_delay,
            max_reconnect_delay=max_reconnect_delay,
        )

        # ── RabbitMQ — still consume trading-ops for daily_stats etc. ──
        rmq_config = config_manager.get_rabbitmq_config()
        rmq_url = f"amqp://{rmq_config['authentication']['username']}:{rmq_config['authentication']['password']}@{rmq_config['hostname']}/"
        connection = await aio_pika.connect_robust(rmq_url)
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=1)
        ops_queue = await channel.declare_queue("trading-ops", durable=True)

        async def on_message(msg: aio_pika.IncomingMessage):
            await dispatch_ops_message(
                msg, performance_monitor, db_position_manager,
                db_perf_manager, telegram,
            )

        await ops_queue.consume(on_message)

        await telegram.send(
            f"WATA Position Monitor v{APP_VERSION} is running "
            f"(WebSocket streaming + trading-ops queue)."
        )
        logger.info(
            "Position Monitor startup complete. Streaming to %s, "
            "also consuming trading-ops queue.",
            environment,
        )

        # Run the streaming client (blocks until stop() or fatal error)
        await stream_client.start()

    except Exception as e:
        logger.critical("Unhandled startup error: %s", e, exc_info=True)
        try:
            await telegram.send(f"Position Monitor CRITICAL FAILURE: {e}")
        except Exception:
            pass
        sys.exit(1)
    finally:
        logger.info("--- Shutting down WATA Position Monitor ---")
        if stream_client:
            await stream_client.stop()
        if api_client is not None:
            await api_client.close()
        if pg is not None:
            await pg.close()
        await telegram.close()


if __name__ == "__main__":
    try:
        import uvloop
        uvloop.install()
    except ImportError:
        pass
    asyncio.run(main())
