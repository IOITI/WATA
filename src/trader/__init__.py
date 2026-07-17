"""
Async Trader Service — consumes from ``trading-signals`` queue via aio-pika.

Replaces the synchronous ``src/main.py`` Trader for signal processing.
Position monitoring is now handled by the separate Position Monitor service.
"""

import asyncio
import json
import logging
import os
import sys
import traceback
from datetime import date, datetime, timedelta

import aio_pika
import jsonschema
import pytz

# --- Configuration & Logging ---
from src.configuration import ConfigurationManager
from src.logging_helper import setup_logging

# --- Async services ---
from src.trade.async_services import (
    AsyncSaxoApiClient,
    AsyncInstrumentService,
    AsyncOrderService,
    AsyncPositionService,
    AsyncTradingOrchestrator,
    AsyncPerformanceMonitor,
)
from src.trade.watchlist_client import AsyncWatchlistClient
from src.database.postgres import (
    PostgresConnectionManager,
    AsyncDbOrderManager,
    AsyncDbPositionManager,
    AsyncDbTradePerformanceManager,
    init_schema,
)
from src.mq_telegram.async_tools import AsyncTelegramSender
from src.saxo_authen import SaxoAuth
from src.trade.rules import TradingRule
from src.schema import SchemaLoader
from src.message_helper import (
    TelegramMessageComposer,
    build_daily_trading_report_message,
    DEFAULT_MILESTONES_EUR,
)
from src.trade.exceptions import (
    TradingRuleViolation,
    NoMarketAvailableException,
    NoTurbosAvailableException,
    PositionNotFoundException,
    InsufficientFundsException,
    ApiRequestException,
    TokenAuthenticationException,
    DatabaseOperationException,
    PositionCloseException,
    SaxoApiError,
    OrderPlacementError,
    ConfigurationError,
)

logger = logging.getLogger(__name__)

# --- Global version ---
APP_VERSION = "unknown"


def get_version() -> str:
    try:
        vf = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "VERSION")
        with open(vf, "r") as f:
            return f.read().strip()
    except Exception:
        return "unknown"


# ─────────────────────────────────────────────────────
#  Timing helpers
# ─────────────────────────────────────────────────────

def _parse_iso_timestamp(ts: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp (with or without fractional seconds) to a tz-aware datetime."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _format_ms(delta_seconds: float | None) -> str:
    if delta_seconds is None:
        return "N/A"
    return f"{delta_seconds * 1000:.0f}ms"


def _delta_ms(start_dt: datetime | None, end_dt: datetime | None) -> str:
    if start_dt is None or end_dt is None:
        return "N/A"
    return _format_ms((end_dt - start_dt).total_seconds())


def _delta_ms_from_epoch(start_dt: datetime | None, end_epoch: float | None) -> str:
    if start_dt is None or end_epoch is None:
        return "N/A"
    return _format_ms(end_epoch - start_dt.timestamp())


# ─────────────────────────────────────────────────────
#  Signal handler (long / short)
# ─────────────────────────────────────────────────────

async def handle_trading_signal(
    data: dict,
    trading_orchestrator: AsyncTradingOrchestrator,
    performance_monitor: AsyncPerformanceMonitor,
    trading_rule: TradingRule,
    db_position_manager: AsyncDbPositionManager,
    trade_turbo_exchange_id: str,
    telegram: AsyncTelegramSender,
):
    """Process a long/short signal end-to-end."""
    action = data["action"]
    indice = data["indice"]
    confidence = data.get("confidence")
    composer = TelegramMessageComposer(data)

    handle_start_dt = datetime.now(pytz.utc)

    try:
        # 1. Rule checks (sync — pure computation + quick DB reads via asyncio.to_thread)
        trading_rule.check_signal_timestamp(action, data.get("alert_timestamp"))
        trading_rule.check_market_hours(data.get("signal_timestamp"))
        indice_id = trading_rule.get_allowed_indice_id(indice)

        # Async-aware position duplicate check
        open_positions = await db_position_manager.get_open_positions_ids_actions()
        for pos in open_positions:
            if pos.get("action") == action:
                raise TradingRuleViolation(f"Duplicate signal: {action} position already open")

        # Async profit check
        await _async_check_profit_per_day(trading_rule, db_position_manager)

        trading_rule.check_cooldown_after_loss()
        trading_rule.check_max_trades_per_day()
        if confidence is not None:
            trading_rule.check_confidence_threshold(confidence)

        rule_check_end_dt = datetime.now(pytz.utc)

        reserve_cash_pct = trading_orchestrator.reserve_cash_percent

        # 2. Execute based on mode
        if reserve_cash_pct > 0:
            # Decoupled: new trade first, then close old positions
            result = await trading_orchestrator.execute_trade_signal(
                exchange_id=trade_turbo_exchange_id,
                underlying_uics=indice_id,
                keywords=action,
                confidence=confidence,
            )
            close_result = await performance_monitor.close_managed_positions_by_criteria(
                action_filter=None,
                exclude_position_id=result["position_details"]["position_id"],
            )
            composer.add_text_section(
                "Post-Trade Closure",
                f"Closed existing positions. Initiated: {close_result['closed_initiated_count']}, Errors: {close_result['errors_count']}",
            )
        else:
            # Classic: close old first, then open new
            close_result = await performance_monitor.close_managed_positions_by_criteria(action_filter=None)
            composer.add_text_section(
                "Pre-Trade Closure",
                f"Closed existing positions. Initiated: {close_result['closed_initiated_count']}, Errors: {close_result['errors_count']}",
            )
            result = await trading_orchestrator.execute_trade_signal(
                exchange_id=trade_turbo_exchange_id,
                underlying_uics=indice_id,
                keywords=action,
                confidence=confidence,
            )

        # 3. Compose success message
        composer.add_turbo_search_result(founded_turbo=result["selected_turbo_info"])
        composer.add_position_result(buy_details=result)
        if "execution_timing" in result:
            signal_dt = _parse_iso_timestamp(data.get("signal_timestamp"))
            received_dt = _parse_iso_timestamp(data.get("received_timestamp"))
            mqsend_dt = _parse_iso_timestamp(data.get("mqsend_timestamp"))
            position_confirmed_epoch = (result.get("raw_timestamps") or {}).get("position_confirmed")

            timing_payload = {
                "api": {
                    "signal_to_mqsend": _delta_ms(signal_dt, mqsend_dt),
                    "received_to_mqsend": _delta_ms(received_dt, mqsend_dt),
                },
                "trader": {
                    "handle_from_mqsend": _delta_ms(mqsend_dt, handle_start_dt),
                    "rule_check": _delta_ms(handle_start_dt, rule_check_end_dt),
                    "execution_details": result["execution_timing"],
                },
                "signal_to_position": _delta_ms_from_epoch(signal_dt, position_confirmed_epoch),
                "received_to_position": _delta_ms_from_epoch(received_dt, position_confirmed_epoch),
            }
            composer.add_text_section("Execution Timing", timing_payload)
        if result.get("position_scale") is not None:
            scale_info = f"Scale: {result['position_scale']}%"
            if confidence is not None:
                scale_info += f" (confidence: {confidence})"
            composer.add_text_section("Position Sizing", scale_info)

        await telegram.send(composer.get_message())
        logger.info("Trade %s executed — Order %s, Position %s",
                     action, result["order_details"]["order_id"], result["position_details"]["position_id"])

    except TradingRuleViolation as trv:
        logger.warning("Rule violation: %s", trv)
        # Don't spam telegram for rule violations
    except (NoMarketAvailableException, NoTurbosAvailableException, InsufficientFundsException) as e:
        logger.warning("Trade setup issue (%s): %s", type(e).__name__, e)
        composer.add_generic_error(type(e).__name__, e)
        await telegram.send(composer.get_message())
    except OrderPlacementError as e:
        logger.error("Order rejected: %s", e)
        composer.add_position_result(error=e)
        await telegram.send(composer.get_message())
    except PositionNotFoundException as e:
        logger.critical("Position not found after order: %s", e)
        composer.add_generic_error("PositionNotFoundException", e, is_critical=True)
        await telegram.send(composer.get_message())
        raise  # Bubble up for service-level handling
    except DatabaseOperationException as e:
        logger.critical("DB error during trade: %s", e)
        composer.add_generic_error("DatabaseOperationException", e, is_critical=True)
        await telegram.send(composer.get_message())
        raise


async def _async_check_profit_per_day(trading_rule: TradingRule, db_pm: AsyncDbPositionManager):
    """Async version of TradingRule.check_profit_per_day using async DB."""
    try:
        day_config = trading_rule.get_rule_config("day_trading")
        threshold = day_config.get("dont_enter_trade_if_day_profit_is_more_than")
        if threshold is None:
            return
        today_pct = await db_pm.get_percent_of_the_day()
        if today_pct >= threshold:
            raise TradingRuleViolation(
                f"Daily profit {today_pct}% >= limit {threshold}%"
            )
    except TradingRuleViolation:
        raise
    except Exception as e:
        logger.error("Error checking daily profit: %s", e)


# ─────────────────────────────────────────────────────
#  Close handler (close-long / close-short / close-position)
# ─────────────────────────────────────────────────────

async def handle_close_signal(
    data: dict,
    performance_monitor: AsyncPerformanceMonitor,
    telegram: AsyncTelegramSender,
):
    action = data["action"]
    action_filter = None
    if action == "close-long":
        action_filter = "long"
    elif action == "close-short":
        action_filter = "short"

    result = await performance_monitor.close_managed_positions_by_criteria(action_filter=action_filter)
    closed = result["closed_initiated_count"]
    errors = result["errors_count"]
    logger.info("Close action '%s': closed=%d, errors=%d", action, closed, errors)
    if closed > 0:
        await telegram.send(f"{action.upper()}: Closed {closed} position(s). Errors: {errors}")


# ─────────────────────────────────────────────────────
#  Daily stats handler
# ─────────────────────────────────────────────────────

async def handle_daily_stats(
    data: dict,
    db_position_manager: AsyncDbPositionManager,
    db_perf_manager: AsyncDbTradePerformanceManager,
    telegram: AsyncTelegramSender,
    position_service: AsyncPositionService,
    milestones_eur: list[float],
):
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

SIGNAL_ACTIONS = {"long", "short"}
CLOSE_ACTIONS = {"close-long", "close-short", "close-position"}
OPS_ACTIONS = {"check_positions_on_saxo_api", "daily_stats"}


async def dispatch_message(
    message: aio_pika.IncomingMessage,
    # injected dependencies
    trading_orchestrator: AsyncTradingOrchestrator,
    performance_monitor: AsyncPerformanceMonitor,
    trading_rule: TradingRule,
    db_position_manager: AsyncDbPositionManager,
    db_perf_manager: AsyncDbTradePerformanceManager,
    trade_turbo_exchange_id: str,
    telegram: AsyncTelegramSender,
    position_service: AsyncPositionService,
    milestones_eur: list[float],
):
    async with message.process(requeue=False):
        try:
            body = json.loads(message.body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.error("Cannot decode message: %s", e)
            await telegram.send(f"ERROR: Cannot decode MQ message: {e}")
            return

        # Validate schema
        try:
            jsonschema.validate(instance=body, schema=SchemaLoader.get_trading_action_schema())
        except jsonschema.exceptions.ValidationError as e:
            logger.error("Schema validation failed: %s", e.message)
            await telegram.send(f"SCHEMA ERROR: {e.message}")
            return

        action = body.get("action")
        signal_id = body.get("signal_id", "N/A")
        logger.info("Dispatching action=%s signal_id=%s", action, signal_id)

        try:
            if action in SIGNAL_ACTIONS:
                await handle_trading_signal(
                    body, trading_orchestrator, performance_monitor,
                    trading_rule, db_position_manager,
                    trade_turbo_exchange_id, telegram,
                )
            elif action in CLOSE_ACTIONS:
                await handle_close_signal(body, performance_monitor, telegram)
            elif action == "daily_stats":
                await handle_daily_stats(
                    body, db_position_manager, db_perf_manager, telegram,
                    position_service, milestones_eur,
                )
            elif action == "check_positions_on_saxo_api":
                # Position checks are handled by position_monitor service
                # If message arrives here by mistake, just log and skip
                logger.warning("check_positions_on_saxo_api should go to trading-ops queue. Skipping.")
            else:
                logger.error("Unknown action: %s", action)
                await telegram.send(f"ERROR: Unknown action '{action}'")

        except (PositionNotFoundException, DatabaseOperationException) as critical:
            logger.critical("CRITICAL error processing %s: %s", action, critical, exc_info=True)
            await telegram.send(f"CRITICAL ERROR ({type(critical).__name__}): {critical}")
            # For critical errors, we may want to restart
            raise
        except (TokenAuthenticationException, ConfigurationError) as fatal:
            logger.critical("FATAL: %s", fatal, exc_info=True)
            await telegram.send(f"FATAL ({type(fatal).__name__}): {fatal}")
            raise
        except Exception as e:
            logger.error("Error processing action %s: %s", action, e, exc_info=True)
            await telegram.send(f"ERROR processing {action}: {type(e).__name__}: {e}")


# ─────────────────────────────────────────────────────
#  Account info helper (async-compatible via to_thread)
# ─────────────────────────────────────────────────────

async def get_account_info_async(api_client: AsyncSaxoApiClient):
    """Fetch AccountKey/ClientKey using the async API client."""
    import src.saxo_openapi.endpoints.portfolio as pf
    from collections import namedtuple

    req = pf.accounts.AccountsMe()
    rv = await api_client.request(req)
    t = namedtuple("AcctInfo", "ClientId ClientKey AccountId AccountKey")
    return t(
        ClientId=rv["Data"][0]["ClientId"],
        ClientKey=rv["Data"][0]["ClientKey"],
        AccountId=rv["Data"][0]["AccountId"],
        AccountKey=rv["Data"][0]["AccountKey"],
    )


# ─────────────────────────────────────────────────────
#  Main entry point
# ─────────────────────────────────────────────────────

async def main():
    global APP_VERSION
    APP_VERSION = get_version()

    # 1. Configuration
    config_path = os.getenv("WATA_CONFIG_PATH")
    if not config_path:
        print("FATAL: WATA_CONFIG_PATH not set", file=sys.stderr)
        sys.exit(10)
    config_manager = ConfigurationManager(config_path)
    setup_logging(config_manager, "wata-trader-async")
    logger.info("--- Starting WATA Async Trader v%s ---", APP_VERSION)

    # 2. Telegram sender (connect early for startup notifications)
    telegram = AsyncTelegramSender(config_manager)
    await telegram.connect()

    pg = None
    api_client = None

    try:
        # 3. PostgreSQL
        logger.info("Connecting to PostgreSQL...")
        pg = PostgresConnectionManager.from_config(config_manager)
        await pg.connect()
        await init_schema(pg)

        db_order_manager = AsyncDbOrderManager(pg)
        db_position_manager = AsyncDbPositionManager(pg)
        db_perf_manager = AsyncDbTradePerformanceManager(pg)

        # 4. Trading rules (sync TradingRule — uses async DB wrapper below)
        trading_rule = TradingRule(config_manager, None)  # db_position_manager passed separately
        trade_turbo_exchange_id = config_manager.get_config_value("trade.config.turbo_preference.exchange_id")
        milestones_eur = config_manager.get_config_value("reporting.milestones_eur", list(DEFAULT_MILESTONES_EUR))

        # 5. Saxo auth + API client
        logger.info("Initialising Saxo API client...")
        saxo_auth = SaxoAuth(config_manager)
        api_client = AsyncSaxoApiClient(config_manager, saxo_auth)
        await api_client.ensure_ready()

        # 6. Fetch account info
        acct = await get_account_info_async(api_client)
        account_key = acct.AccountKey
        client_key = acct.ClientKey
        logger.info("Account: %s, Client: %s", account_key, client_key)

        # 7. Async services
        instrument_service = AsyncInstrumentService(api_client, config_manager, account_key)
        order_service = AsyncOrderService(api_client, account_key, client_key)
        position_service = AsyncPositionService(api_client, order_service, config_manager, account_key, client_key)
        watchlist_client = AsyncWatchlistClient(config_manager)
        trading_orchestrator = AsyncTradingOrchestrator(
            instrument_service, order_service, position_service,
            config_manager, db_order_manager, db_position_manager,
            watchlist_client=watchlist_client,
        )
        async def _trigger_daily_stats():
            await handle_daily_stats(
                {"action": "daily_stats", "indice": "n/a"},
                db_position_manager, db_perf_manager, telegram,
                position_service, milestones_eur,
            )

        performance_monitor = AsyncPerformanceMonitor(
            position_service, order_service, config_manager,
            db_position_manager, trading_rule, telegram.send,
            trigger_daily_stats_fn=_trigger_daily_stats,
        )

        # 8. RabbitMQ consumer (aio-pika)
        logger.info("Connecting to RabbitMQ...")
        rmq_config = config_manager.get_rabbitmq_config()
        rmq_url = f"amqp://{rmq_config['authentication']['username']}:{rmq_config['authentication']['password']}@{rmq_config['hostname']}/"
        connection = await aio_pika.connect_robust(rmq_url)
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=1)

        # Declare both queues — this service consumes from trading-signals
        signals_queue = await channel.declare_queue("trading-signals", durable=True)
        await channel.declare_queue("trading-ops", durable=True)

        async def on_message(msg: aio_pika.IncomingMessage):
            await dispatch_message(
                msg,
                trading_orchestrator=trading_orchestrator,
                performance_monitor=performance_monitor,
                trading_rule=trading_rule,
                db_position_manager=db_position_manager,
                db_perf_manager=db_perf_manager,
                trade_turbo_exchange_id=trade_turbo_exchange_id,
                telegram=telegram,
                position_service=position_service,
                milestones_eur=milestones_eur,
            )

        await signals_queue.consume(on_message)

        startup_msg = f"WATA Async Trader v{APP_VERSION} is running (trading-signals queue)."
        await telegram.send(startup_msg)
        logger.info("Trader startup complete. Consuming from trading-signals...")

        # Keep running
        try:
            await asyncio.Future()  # Run forever
        except asyncio.CancelledError:
            pass

    except Exception as e:
        logger.critical("Unhandled startup error: %s", e, exc_info=True)
        try:
            await telegram.send(f"CRITICAL STARTUP FAILURE: {e}")
        except Exception:
            pass
        sys.exit(1)

    finally:
        logger.info("--- Shutting down WATA Async Trader ---")
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
