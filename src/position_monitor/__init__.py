"""
Position Monitor Service — real-time WebSocket streaming + ``trading-ops`` queue.

Uses Saxo's WebSocket Streaming API for instant position monitoring
(replaces the 7-second polling approach).  Still consumes ``trading-ops``
for time-triggered events like ``daily_stats``.

Each streamed position update is checked against the exit rules. Reconciling
the DB with Saxo is separate and rarer: it runs when a position leaves the
stream and on a timer.

Runs as a separate Docker container (WATA_APP_ROLE=position_monitor).
"""

import asyncio
import json
import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone

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
    AsyncDbRiskStateManager,
    AsyncDbLiveValueManager,
    AsyncDbBalanceHistoryManager,
    init_schema,
)
from src.mq_telegram.async_tools import AsyncTelegramSender
from src.saxo_authen import SaxoAuth
from src.saxo_streaming.client import SaxoStreamClient
from src.trade.rules import TradingRule
from src.message_helper import build_daily_trading_report_message, DEFAULT_MILESTONES_EUR

logger = logging.getLogger(__name__)

APP_VERSION = "unknown"


def get_version() -> str:
    try:
        vf = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "VERSION")
        with open(vf, "r") as f:
            return f.read().strip()
    except Exception:
        return "unknown"


# How long after a position leaves the stream the DB is reconciled. Whoever
# closed it (this monitor or the trader) records the closure itself within
# moments; what is still open in the DB after that was closed from outside.
RECONCILE_SETTLE_SECONDS = 3.0

# A request to close one position (from the admin interface) is only honoured
# while it is fresh: the ops queue is durable, and a request that waited there
# while this service was down must not sell anything once it is back.
CLOSE_REQUEST_MAX_AGE_SECONDS = 60

LIVE_VALUE_DB_TIMEOUT_SECONDS = 5.0


# ─────────────────────────────────────────────────────
#  Streaming callback
# ─────────────────────────────────────────────────────

class StreamPositionHandler:
    """
    Handles the position snapshots pushed by :class:`SaxoStreamClient`.

    Every snapshot goes through the SL/TP/trailing-stop checks, without any API
    polling. The DB is reconciled with Saxo (to catch closures done outside
    WATA, e.g. a broker-side stop) only when a position leaves the stream, and
    periodically as a safety net.
    """

    def __init__(
        self,
        performance_monitor: AsyncPerformanceMonitor,
        db_position_manager: AsyncDbPositionManager,
        telegram: AsyncTelegramSender,
        reconcile_settle_seconds: float = RECONCILE_SETTLE_SECONDS,
    ):
        self.performance_monitor = performance_monitor
        self.db_position_manager = db_position_manager
        self.telegram = telegram
        self.reconcile_settle_seconds = reconcile_settle_seconds
        self._last_position_ids: set[str] = set()
        self._reconcile_requested = False
        self._pending_reconcile: asyncio.Task | None = None

    async def on_positions_update(self, positions: dict[str, dict]):
        """Called by the stream client with the latest position snapshot."""
        position_ids = set(positions)
        if self._last_position_ids - position_ids:
            self._reconcile_soon()
        self._last_position_ids = position_ids
        try:
            # Bookkeeping for the admin interface: it must never stand in the way of the checks below.
            self.performance_monitor.forget_live_values_except(position_ids)
        except Exception as e:
            logger.debug("Could not prune the live position values: %r", e)

        if not positions:
            return

        try:
            perf_result = await self.performance_monitor.check_positions_from_stream(positions)
            perf_errors = perf_result.get("errors", 0)
            perf_closed = len(perf_result.get("closed_positions_processed", []))
            if perf_errors > 0:
                logger.warning("Stream position check: closed=%d, errors=%d", perf_closed, perf_errors)
            else:
                logger.debug("Stream position check: closed=%d, errors=%d", perf_closed, perf_errors)
        except Exception as e:
            logger.error("Error in streaming position callback: %s", e, exc_info=True)

    def _reconcile_soon(self):
        """Plan a reconciliation for a position that just left the stream."""
        self._reconcile_requested = True
        if self._pending_reconcile is None or self._pending_reconcile.done():
            self._pending_reconcile = asyncio.create_task(self._reconcile_after_settling())

    async def _reconcile_after_settling(self):
        while self._reconcile_requested:
            self._reconcile_requested = False
            await asyncio.sleep(self.reconcile_settle_seconds)
            # The stream already says what is open at Saxo: no API call needed for that.
            await self.reconcile(open_position_ids=self._last_position_ids)

    async def reconcile(self, open_position_ids: set[str] | None = None):
        """Mark as closed in the DB the positions that were closed at Saxo behind WATA's back."""
        try:
            sync_result = await self.performance_monitor.sync_db_positions_with_api(open_position_ids)
        except Exception as e:
            logger.error("Position reconciliation failed: %s", e, exc_info=True)
            return

        for position_id, update_data in sync_result.get("updates_for_db", []):
            try:
                await self.db_position_manager.update_turbo_position_data(position_id, update_data)
                logger.info("Reconciled position %s as closed at Saxo.", position_id)
            except Exception as e:
                logger.critical("SYNC ERROR: Failed DB update for Pos %s: %s", position_id, e, exc_info=True)
                await self.telegram.send(f"CRITICAL SYNC ERROR: Failed DB update for Pos {position_id}: {e}")

    async def reconcile_periodically(self, interval_seconds: float):
        """
        Reconcile at startup and then every ``interval_seconds``, asking Saxo's
        REST API what is open so that it doesn't rely on the stream being healthy.
        """
        while True:
            await self.reconcile()
            await asyncio.sleep(interval_seconds)

    def stop(self):
        if self._pending_reconcile is not None and not self._pending_reconcile.done():
            self._pending_reconcile.cancel()


async def publish_live_values_periodically(
    performance_monitor: AsyncPerformanceMonitor,
    db_live_value_manager: AsyncDbLiveValueManager,
    interval_seconds: float,
):
    """
    Copy the latest price and performance of the open positions to the DB every
    ``interval_seconds``, for the admin interface.

    It runs beside the position checks and never inside them: a slow or failing
    DB only makes the published values old.
    """
    last_published = None
    while True:
        await asyncio.sleep(interval_seconds)
        values = performance_monitor.live_values()
        if values == last_published:
            continue
        try:
            await asyncio.wait_for(
                db_live_value_manager.replace_live_values(values),
                LIVE_VALUE_DB_TIMEOUT_SECONDS,
            )
            last_published = values
        except Exception as e:
            logger.warning("Could not publish the live position values: %r", e)


async def handle_close_position_request(
    body: dict,
    performance_monitor: AsyncPerformanceMonitor,
    telegram: AsyncTelegramSender,
):
    """Close one position at the operator's request (sent by the admin interface)."""
    position_id = body.get("position_id")
    if not position_id:
        logger.error("Close request without a position_id: %s", body)
        return

    try:
        requested_at = datetime.fromisoformat(str(body.get("requested_at")).replace("Z", "+00:00"))
        age_seconds = (datetime.now(timezone.utc) - requested_at).total_seconds()
    except (TypeError, ValueError):
        age_seconds = None
    if age_seconds is None or not 0 <= age_seconds <= CLOSE_REQUEST_MAX_AGE_SECONDS:
        logger.warning("Ignoring a close request for %s that is not fresh: %s", position_id, body.get("requested_at"))
        await telegram.send(
            f"Close of position {position_id} requested from the admin was IGNORED: "
            f"the request was not fresh (sent {body.get('requested_at')})."
        )
        return

    result = await performance_monitor.close_managed_positions_by_criteria(
        only_position_id=str(position_id),
        defer_bookkeeping=True,
    )
    logger.info("Close of position %s requested from the admin: %s", position_id, result)
    if result["closed_initiated_count"] == 0 and result["errors_count"] == 0:
        await telegram.send(
            f"Close of position {position_id} requested from the admin: nothing done, "
            f"it is not open or cannot be closed."
        )


async def handle_daily_stats(
    db_position_manager: AsyncDbPositionManager,
    db_perf_manager: AsyncDbTradePerformanceManager,
    telegram: AsyncTelegramSender,
    position_service: AsyncPositionService,
    milestones_eur: list[float],
    db_balance_manager: AsyncDbBalanceHistoryManager | None = None,
):
    """Generate and send daily performance stats."""
    days = 7
    report_date = date.today()
    history_start = date(report_date.year - 4, 1, 1)

    closed_trades, daily_profit_history, real_daily, best_daily, max_daily = await asyncio.gather(
        db_position_manager.get_closed_trade_history(start_date=history_start, end_date=report_date),
        db_position_manager.get_daily_profit_history(end_date=report_date),
        db_position_manager.get_percent_of_last_n_days(days),
        db_position_manager.get_best_percent_of_last_n_days(days),
        db_position_manager.get_theoretical_percent_of_last_n_days_on_max(days),
    )

    try:
        current_balance = await position_service.get_current_account_balance()
    except Exception as e:
        logger.error("Failed to fetch account balance for daily report: %s", e)
        current_balance = None
    if db_balance_manager is not None:
        await db_balance_manager.record_balance_safely(report_date, current_balance)

    message = build_daily_trading_report_message(
        report_date=report_date,
        closed_trades=closed_trades,
        daily_profit_history=daily_profit_history,
        daily_real=real_daily,
        daily_best=best_daily,
        daily_max=max_daily,
        current_balance=current_balance,
        milestones_eur=milestones_eur,
    )
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
    position_service: AsyncPositionService,
    milestones_eur: list[float],
    db_balance_manager: AsyncDbBalanceHistoryManager | None = None,
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
                await handle_daily_stats(
                    db_position_manager, db_perf_manager, telegram,
                    position_service, milestones_eur,
                    db_balance_manager=db_balance_manager,
                )
            elif action == "close_position":
                await handle_close_position_request(body, performance_monitor, telegram)
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
    performance_monitor = None
    stream_handler = None
    reconcile_task = None
    live_value_task = None

    try:
        # PostgreSQL
        pg = PostgresConnectionManager.from_config(config_manager)
        await pg.connect()
        await init_schema(pg)
        db_position_manager = AsyncDbPositionManager(pg)
        db_perf_manager = AsyncDbTradePerformanceManager(pg)
        db_risk_state_manager = AsyncDbRiskStateManager(pg)
        db_live_value_manager = AsyncDbLiveValueManager(pg)
        db_balance_manager = AsyncDbBalanceHistoryManager(pg)

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
        trading_rule = TradingRule(config_manager, db_position_manager, db_risk_state_manager)
        milestones_eur = config_manager.get_config_value("reporting.milestones_eur", list(DEFAULT_MILESTONES_EUR))

        async def _trigger_daily_stats():
            await handle_daily_stats(
                db_position_manager, db_perf_manager, telegram,
                position_service, milestones_eur,
                db_balance_manager=db_balance_manager,
            )

        performance_monitor = AsyncPerformanceMonitor(
            position_service, order_service, config_manager,
            db_position_manager, trading_rule, telegram.send,
            trigger_daily_stats_fn=_trigger_daily_stats,
        )

        # ── Streaming configuration ──
        environment = config_manager.get_config_value("saxo_auth.env", "live")
        streaming_config = config_manager.get_config_value("trade.config.general.streaming", {})
        refresh_rate_ms = streaming_config.get("refresh_rate_ms", 1000)
        reconnect_delay = streaming_config.get("reconnect_delay_seconds", 1.0)
        max_reconnect_delay = streaming_config.get("max_reconnect_delay_seconds", 30.0)
        reauth_interval_seconds = streaming_config.get("reauth_interval_seconds", 900)
        reconcile_interval_seconds = streaming_config.get("reconcile_interval_seconds", 30)
        live_value_interval_seconds = streaming_config.get("live_value_interval_seconds", 5)

        stream_handler = StreamPositionHandler(performance_monitor, db_position_manager, telegram)

        # Create streaming client
        stream_client = SaxoStreamClient(
            api_client=api_client,
            account_key=account_key,
            client_key=client_key,
            on_positions_update=stream_handler.on_positions_update,
            environment=environment,
            access_token_getter=lambda: saxo_auth.get_token(),
            refresh_rate_ms=refresh_rate_ms,
            reconnect_delay=reconnect_delay,
            max_reconnect_delay=max_reconnect_delay,
            reauth_interval_seconds=reauth_interval_seconds,
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
                position_service, milestones_eur,
                db_balance_manager=db_balance_manager,
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

        reconcile_task = asyncio.create_task(stream_handler.reconcile_periodically(reconcile_interval_seconds))
        if live_value_interval_seconds > 0:
            live_value_task = asyncio.create_task(
                publish_live_values_periodically(performance_monitor, db_live_value_manager, live_value_interval_seconds)
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
        if reconcile_task is not None:
            reconcile_task.cancel()
        if live_value_task is not None:
            live_value_task.cancel()
        if stream_handler is not None:
            stream_handler.stop()
        if stream_client:
            await stream_client.stop()
        if performance_monitor is not None:
            await performance_monitor.wait_for_background_tasks()
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
