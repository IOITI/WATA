import os
import traceback
import json
import logging
import jsonschema
import pika
from functools import partial
import sys

# --- Configuration and Core Components ---
from configuration import ConfigurationManager
# --- Import Saxo Services ---
from trade.api_actions import (
    SaxoApiClient,
    InstrumentService,
    OrderService,
    PositionService,
    TradingOrchestrator,
    PerformanceMonitor
)
from src.saxo_authen import SaxoAuth
# --- Other necessary imports ---
from trade.rules import TradingRule
from schema import SchemaLoader
from database import DbOrderManager, DbPositionManager, DbTradePerformanceManager
from mq_telegram.tools import send_message_to_mq_for_telegram
# --- Use the Updated Message Helper ---
from message_helper import (
    generate_daily_stats_message,
    generate_performance_stats_message,
    TelegramMessageComposer
)
from logging_helper import setup_logging

# --- Specific Exceptions ---
from trade.exceptions import (
    TradingRuleViolation,
    NoMarketAvailableException,
    NoTurbosAvailableException,
    PositionNotFoundException,
    InsufficientFundsException,
    ApiRequestException,
    TokenAuthenticationException,
    DatabaseOperationException,
    PositionCloseException,
    WebSocketConnectionException,
    SaxoApiError,
    OrderPlacementError,
    ConfigurationError
)

# --- Global for Version ---
APP_VERSION = "unknown"

# --- Utility Functions ---
def get_version():
    """Reads the application version from the VERSION file."""
    try:
        version_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'VERSION')
        with open(version_file, 'r') as file:
            return file.read().strip()
    except Exception as e:
        logging.error(f"Could not read VERSION file: {e}")
        return "unknown"

# --- Error Handling Helpers ---
def handle_exception(
    e, composer, ch, method, body, is_critical=False, exit_code=None, log_level=logging.ERROR
):
    """Centralized function to handle exceptions."""
    error_type = type(e).__name__
    log_message = f"{'CRITICAL' if is_critical else 'ERROR'} ({error_type}): {e}"
    detail_message = f"{log_message}\n{traceback.format_exc()}" # Include traceback

    logging.log(log_level, detail_message)

    # Try to compose a message using the updated composer
    if composer:
        # Let the composer handle detailed formatting based on exception type
        composer.add_generic_error(error_type, e, is_critical=is_critical)
        # Optionally add traceback snippet for severe errors
        if is_critical or log_level >= logging.ERROR:
             # Add more specific details if available from custom exceptions
             extra_details = {}
             if hasattr(e, 'status_code'): extra_details['Status'] = e.status_code
             if hasattr(e, 'saxo_error_details'): extra_details['Saxo Details'] = json.dumps(e.saxo_error_details, indent=2)
             if hasattr(e, 'order_details'): extra_details['Order Payload'] = json.dumps(e.order_details, indent=2)
             if hasattr(e, 'request_details'): extra_details['Request Details'] = json.dumps(e.request_details, indent=2)
             if hasattr(e, 'order_id'): extra_details['Order ID'] = e.order_id
             if extra_details:
                  composer.add_dict_section("Error Details", extra_details)
             composer.add_text_section("Traceback Snippet", traceback.format_exc(limit=5))
        telegram_message = composer.get_message()
    else:
        # Fallback if composer failed early
        raw_body_str = body.decode(errors='ignore')
        telegram_message = f"CRITICAL ERROR ({error_type}) processing raw message: {raw_body_str}\n\nError: {e}\n\n{traceback.format_exc()}"

    try:
        send_message_to_mq_for_telegram(rabbit_connection, telegram_message)
    except Exception as mq_err:
        logging.error(f"Failed to send error notification to Telegram MQ: {mq_err}")

    # Acknowledge non-critical messages to avoid reprocessing loops for recoverable errors
    # or errors specific to the message content (like rule violations).
    # Critical errors might lead to exit *without* acking, depending on infrastructure setup
    # (e.g., dead-letter queue). Current logic acks most handled errors.
    if not is_critical:
        try:
            ch.basic_ack(delivery_tag=method.delivery_tag)
            logging.debug(f"Non-critical error ({error_type}) handled, message {method.delivery_tag} ACKed.")
        except Exception as ack_err:
             logging.error(f"Failed to ACK message {method.delivery_tag} after handling error: {ack_err}")

    # Exit for critical errors
    if is_critical and exit_code is not None:
        logging.critical(f"Terminating service due to critical error ({error_type}). Exit code: {exit_code}")
        sys.exit(exit_code)

def handle_validation_error(e, body, ch, method):
    """Handles JSON schema validation errors."""
    logging.error(f"Schema Validation Error: {e}")
    raw_body_str = body.decode(errors='ignore')
    error_msg = f"SCHEMA ERROR: Invalid message format received.\n\nError: {e}\n\nRaw Body:\n{raw_body_str}"
    send_message_to_mq_for_telegram(rabbit_connection, error_msg)
    ch.basic_ack(delivery_tag=method.delivery_tag) # Ack invalid message

def handle_rule_violation(trv, composer, ch, method):
    """Handles expected trading rule violations."""
    logging.warning(f"Trading Rule Violation: {trv}")
    # TODO: Add a config param for notification delivery
    #composer.add_rule_violation(trv) # Uses updated composer method
    #send_message_to_mq_for_telegram(rabbit_connection, composer.get_message())
    ch.basic_ack(delivery_tag=method.delivery_tag)

def handle_trade_setup_issue(trade_issue, composer, ch, method):
    """Handles issues like NoTurbos, NoMarket, InsufficientFunds (pre-order)."""
    logging.warning(f"Trade Setup Issue ({type(trade_issue).__name__}): {trade_issue}")
    # Use composer methods which now handle specific exceptions better
    if isinstance(trade_issue, (NoMarketAvailableException, NoTurbosAvailableException)):
        composer.add_turbo_search_result(error=trade_issue, search_context=getattr(trade_issue, 'search_context', None))
    elif isinstance(trade_issue, InsufficientFundsException):
         composer.add_position_result(error=trade_issue) # Composer formats this now
    else:
         composer.add_generic_error(type(trade_issue).__name__, trade_issue)

    send_message_to_mq_for_telegram(rabbit_connection, composer.get_message())
    ch.basic_ack(delivery_tag=method.delivery_tag)

def handle_order_placement_error(ope, composer, ch, method):
    """Handles specific order rejection errors from Saxo."""
    logging.error(f"Order Placement Rejected by Saxo: {ope}")
    # Composer's add_position_result or add_generic_error now handles detailed formatting
    composer.add_position_result(error=ope) # Let composer format it
    send_message_to_mq_for_telegram(rabbit_connection, composer.get_message())
    ch.basic_ack(delivery_tag=method.delivery_tag) # Ack, Saxo rejected it

def handle_unknown_action(action, composer, ch, method):
    """Handles messages with unrecognized actions."""
    logging.error(f"Unknown action received: {action}")
    composer.add_generic_error("Unknown Action", ValueError(f"Action '{action}' not recognized."))
    send_message_to_mq_for_telegram(rabbit_connection, composer.get_message())
    ch.basic_ack(delivery_tag=method.delivery_tag)


# --- Action Handlers ---

def handle_trading_action(
    data, composer: TelegramMessageComposer, ch, method,
    # --- Injected Services ---
    trading_orchestrator: TradingOrchestrator,
    performance_monitor: PerformanceMonitor,
    trading_rule: TradingRule,
    db_position_manager: DbPositionManager,
    # --- Other Args ---
    trade_turbo_exchange_id: str,
    rabbit_connection
):
    """Handles 'long' and 'short' trading actions using new services."""
    action = data.get("action")
    indice = data.get("indice")
    logging.info(f"Processing trading action: {action} for {indice}")

    execution_result = None

    try:
        # 1. Rule Checks
        logging.debug("Checking trading rules...")
        trading_rule.check_signal_timestamp(action, data.get("alert_timestamp"))
        trading_rule.check_market_hours(data.get("signal_timestamp"))
        indice_id = trading_rule.get_allowed_indice_id(indice)
        TradingRule.check_if_open_position_is_same_signal(action, db_position_manager)
        trading_rule.check_profit_per_day()
        logging.debug("Trading rules passed.")

        # 2. Close existing positions
        logging.info("Attempting to close any existing managed positions before opening new one...")
        close_result = performance_monitor.close_managed_positions_by_criteria(action_filter=None) # None closes all
        logging.info(f"Attempted closure of existing positions. Initiated: {close_result.get('closed_initiated_count', 0)}, Errors: {close_result.get('errors_count', 0)}")
        # Optionally add summary (consider if monitor's own messages are sufficient)
        composer.add_text_section("Pre-Trade Closure", f"Attempted closing existing positions. Initiated: {close_result['closed_initiated_count']}, Errors: {close_result['errors_count']}")


        # 3. Execute Trade Signal
        logging.info(f"Executing trade signal: Exchange {trade_turbo_exchange_id}, IndiceID {indice_id}, Action {action}")
        execution_result = trading_orchestrator.execute_trade_signal(
            exchange_id=trade_turbo_exchange_id,
            underlying_uics=indice_id,
            keywords=action
        )
        # execution_result contains: {'order_details': {...}, 'position_details': {...}, 'selected_turbo_info': {...}, 'message': '...'}

        # 4. Compose Success Message using updated composer methods
        composer.add_turbo_search_result(founded_turbo=execution_result['selected_turbo_info'])
        composer.add_position_result(buy_details=execution_result) # Pass the whole result

        logging.info(f"Successfully executed and recorded trade action: {action}. OrderID: {execution_result['order_details']['order_id']}, PositionID: {execution_result['position_details']['position_id']}")

        # 5. Final Success Message & Ack
        send_message_to_mq_for_telegram(rabbit_connection, composer.get_message())
        ch.basic_ack(delivery_tag=method.delivery_tag)
        logging.info(f"Message {method.delivery_tag} ACKed for successful trade action: {action}")

    # --- Expected/Handled Errors during Trading ---
    except TradingRuleViolation as trv:
        handle_rule_violation(trv, composer, ch, method)
    except (NoMarketAvailableException, NoTurbosAvailableException, InsufficientFundsException) as setup_err:
        # Let the specific handler format the message via the composer
        handle_trade_setup_issue(setup_err, composer, ch, method)
    except OrderPlacementError as ope:
        # Let the specific handler format the message via the composer
        handle_order_placement_error(ope, composer, ch, method)

    # --- Critical Errors (Need to bubble up for main handler) ---
    except PositionNotFoundException as pnfe:
        logging.critical(f"CRITICAL: Position not found after placing order {pnfe.order_id}: {pnfe}")
        # Composer updated by handle_exception before exit
        raise
    except DatabaseOperationException as dbe:
        logging.critical(f"CRITICAL DB ERROR during trade action '{action}': {dbe}")
        # Composer updated by handle_exception before exit
        raise
    except ValueError as ve:
        logging.error(f"ValueError during trading action '{action}': {ve}", exc_info=True)
        if "CRITICAL" in str(ve).upper():
            raise # Re-raise critical ValueErrors for main handler
        else:
            # Treat as non-critical, let composer format and ACK
            composer.add_generic_error(f"ValueError in {action}", ve)
            send_message_to_mq_for_telegram(rabbit_connection, composer.get_message())
            ch.basic_ack(delivery_tag=method.delivery_tag)

    # Let other unexpected API/Config/Token errors bubble up

def handle_close_action(
    data, composer: TelegramMessageComposer, ch, method,
    # --- Injected Services ---
    performance_monitor: PerformanceMonitor,
    # --- Other Args ---
    rabbit_connection
):
    """Handles 'close-long', 'close-short', 'close-position' using PerformanceMonitor."""
    action = data.get("action")
    logging.info(f"Processing close action: {action}")

    try:
        close_action_filter = None
        if action == "close-long": close_action_filter = "long"
        elif action == "close-short": close_action_filter = "short"

        logging.info(f"Attempting closure via Performance Monitor. Filter: {close_action_filter}")
        close_result = performance_monitor.close_managed_positions_by_criteria(
            action_filter=close_action_filter
        )
        closed_count = close_result.get('closed_initiated_count', 0)
        error_count = close_result.get('errors_count', 0)
        logging.info(f"Close action '{action}' processed. Positions closed/attempted: {closed_count}, Errors: {error_count}")

        composer.add_text_section(
            f"{action.upper()} ACTION", # Simpler title
            f"Processed signal. Attempted closure for {closed_count} position(s)."
            f"{f' Encountered {error_count} error(s).' if error_count > 0 else ''}"
            f" Check other messages for details."
        )

        if closed_count > 0:
            send_message_to_mq_for_telegram(rabbit_connection, composer.get_message())

        ch.basic_ack(delivery_tag=method.delivery_tag)

    except (PositionCloseException, DatabaseOperationException, ApiRequestException, SaxoApiError) as e:
         # Catch errors from monitor/services if they bubble up critically
         logging.error(f"Error during '{action}' execution: {e}", exc_info=True)
         # Let main callback handler manage critical error message and exit
         raise
    except Exception as e:
        logging.error(f"Unexpected error during '{action}': {e}", exc_info=True)
        raise # Let main callback handler manage critical error message and exit


def handle_check_positions(
    data, composer: TelegramMessageComposer, ch, method,
    # --- Injected Services ---
    performance_monitor: PerformanceMonitor,
    db_position_manager: DbPositionManager, # Still needed to apply sync updates
    # --- Other Args ---
    rabbit_connection
):
    """Handles 'check_positions_on_saxo_api' using PerformanceMonitor."""
    action = "check_positions_on_saxo_api"
    logging.info(f"Processing action: {action}")
    sync_updates_applied = 0
    sync_errors = 0
    perf_check_errors = 0
    perf_closed_count = 0

    try:
        # 1. Check Performance
        logging.info("Checking positions performance...")
        perf_result = performance_monitor.check_all_positions_performance()
        perf_check_errors = perf_result.get('errors', 0)
        perf_closed_count = len(perf_result.get('closed_positions_processed', []))
        logging.info(f"Performance check results: Closed={perf_closed_count}, DB Updates={len(perf_result.get('db_updates',[]))}, Errors={perf_check_errors}")

        # 2. Sync DB state with API
        logging.info("Syncing DB positions with API closed positions...")
        sync_result = performance_monitor.sync_db_positions_with_api()
        updates_to_apply = sync_result.get("updates_for_db", [])

        # 3. Apply DB updates from sync result
        if updates_to_apply:
            logging.info(f"Applying {len(updates_to_apply)} DB updates from API sync...")
            for position_id, update_data in updates_to_apply:
                try:
                    db_position_manager.update_turbo_position_data(position_id, update_data)
                    sync_updates_applied += 1
                except Exception as db_err:
                    sync_errors += 1
                    logging.critical(f"CRITICAL SYNC ERROR: Failed DB update for Pos {position_id}: {db_err}", exc_info=True)
                    # Send critical notification directly
                    send_message_to_mq_for_telegram(rabbit_connection, f"🚨 CRITICAL SYNC ERROR: Failed DB update for Pos {position_id}: {db_err}")
                    # This might warrant raising DatabaseOperationException if critical
                    # raise DatabaseOperationException(f"Failed sync update for {position_id}", operation="sync_update", entity_id=position_id) from db_err
            logging.info(f"Sync DB updates applied: {sync_updates_applied}, Errors: {sync_errors}")
        else:
            logging.info("No DB updates required from API sync.")

        total_errors = perf_check_errors + sync_errors
        logging.info(f"{action}: Completed. Perf Closed={perf_closed_count}, Sync Updates={sync_updates_applied}, Total Errors={total_errors}")

        # Acknowledge ONLY if sync DB updates were successful (or none needed)
        # If perf check had non-critical errors, we might still ACK.
        if sync_errors == 0:
             ch.basic_ack(delivery_tag=method.delivery_tag)
             logging.debug(f"{action}: Message {method.delivery_tag} ack'd successfully.")
        else:
             # Do not ACK if sync failed, message might need retry or dead-lettering
             logging.error(f"{action}: Sync DB update errors occurred. Message {method.delivery_tag} NOT ACKed.")
             # Optionally NACK: ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)


    except (DatabaseOperationException, ApiRequestException, SaxoApiError) as e:
        logging.error(f"Critical Error during '{action}': {e}", exc_info=True)
        # Let main handler format and potentially exit
        raise
    except Exception as e:
        logging.error(f"Unexpected error during '{action}': {e}", exc_info=True)
        raise # Let main handler deal with unexpected


def handle_daily_stats(data, composer: TelegramMessageComposer, ch, method, db_position_manager, table_trade_performance_manager, rabbit_connection):
    """Handles 'daily_stats' action. """
    logging.info("Processing action: daily_stats")
    try:
        days = 7 # Number of days for performance stats
        logging.debug("Fetching daily stats...")
        stats_of_the_day = db_position_manager.get_stats_of_the_day()
        message = generate_daily_stats_message(stats_of_the_day)

        logging.debug(f"Fetching performance stats for last {days} days...")
        last_days_percentages = db_position_manager.get_percent_of_last_n_days(days)
        last_best_days_percentages = db_position_manager.get_best_percent_of_last_n_days(days)
        last_days_percentages_on_max = db_position_manager.get_theoretical_percent_of_last_n_days_on_max(days)
        last_best_days_percentages_on_max = db_position_manager.get_best_theoretical_percent_of_last_n_days_on_max(days)

        message = generate_performance_stats_message(
            message, days, last_days_percentages, last_best_days_percentages,
            last_days_percentages_on_max, last_best_days_percentages_on_max
        )
        logging.debug("Sending daily stats message...")
        send_message_to_mq_for_telegram(rabbit_connection, message)

        logging.debug("Inserting daily trade performance data...")
        table_trade_performance_manager.create_last_day_trade_performance_data()

        logging.info("Daily stats processed and sent successfully.")
        ch.basic_ack(delivery_tag=method.delivery_tag)

    except DatabaseOperationException as dbe:
        logging.error(f"Database error during daily stats processing: {dbe}")
        # Let main handler manage critical error
        raise
    except Exception as e:
        logging.error(f"Failed to process daily stats: {e}", exc_info=True)
        # Send general error message and ACK (non-critical)
        composer.add_generic_error("Daily Stats Processing", e)
        send_message_to_mq_for_telegram(rabbit_connection, composer.get_message())
        ch.basic_ack(delivery_tag=method.delivery_tag)


# --- Action Dispatcher ---
ACTION_HANDLERS = {
    "long": handle_trading_action,
    "short": handle_trading_action,
    "close-long": handle_close_action,
    "close-short": handle_close_action,
    "close-position": handle_close_action,
    "check_positions_on_saxo_api": handle_check_positions,
    "daily_stats": handle_daily_stats,
}

# --- Main Callback ---

def callback(ch, method, properties, body, # RabbitMQ args first
             # --- Injected dependencies follow: ---
             trading_orchestrator: TradingOrchestrator,
             performance_monitor: PerformanceMonitor,
             trading_rule: TradingRule,
             db_position_manager: DbPositionManager,
             table_trade_performance_manager: DbTradePerformanceManager,
             trade_turbo_exchange_id: str,
             rabbit_conn):
    """Main callback function executed for each message."""
    composer = None
    data_from_mq = None

    try:
        logging.debug(f"Received message (delivery_tag={method.delivery_tag})")
        # 1. Decode Body
        try:
            raw_body_str = body.decode('utf-8')
            data_from_mq = json.loads(raw_body_str)
            logging.info(f"Received action: {data_from_mq.get('action', 'N/A')}, Signal ID: {data_from_mq.get('signal_id', 'N/A')}")
        except (json.JSONDecodeError, TypeError, Exception) as e: # Broadened catch
            logging.error(f"Error decoding message body: {e}", exc_info=True)
            error_msg = f"CRITICAL: Error decoding message body: {e}\n\nBody:\n{body.decode(errors='ignore')}"
            # Cannot use composer here as data_from_mq might be None
            send_message_to_mq_for_telegram(rabbit_conn, error_msg)
            ch.basic_ack(delivery_tag=method.delivery_tag) # Ack invalid message
            return

        # 2. Schema Validation
        try:
            jsonschema.validate(
                instance=data_from_mq, schema=SchemaLoader.get_trading_action_schema()
            )
        except jsonschema.exceptions.ValidationError as e:
            handle_validation_error(e, body, ch, method) # Uses global rabbit_connection
            return

        # 3. Initialize Composer
        composer = TelegramMessageComposer(data_from_mq)

        # 4. Dispatch to Handler
        action = data_from_mq.get("action")
        handler = ACTION_HANDLERS.get(action)

        if handler:
            # --- Argument Construction Logic (Updated) ---
            handler_args = {
                "data": data_from_mq, "composer": composer, "ch": ch,
                "method": method, "rabbit_connection": rabbit_conn
            }
            if action in ["long", "short"]:
                handler_args.update({
                    "trading_orchestrator": trading_orchestrator,
                    "performance_monitor": performance_monitor,
                    "trading_rule": trading_rule,
                    "db_position_manager": db_position_manager,
                    "trade_turbo_exchange_id": trade_turbo_exchange_id
                })
            elif action in ["close-long", "close-short", "close-position"]:
                handler_args["performance_monitor"] = performance_monitor
            elif action == "check_positions_on_saxo_api":
                handler_args.update({
                    "performance_monitor": performance_monitor,
                    "db_position_manager": db_position_manager
                })
            elif action == "daily_stats":
                handler_args.update({
                    "db_position_manager": db_position_manager,
                    "table_trade_performance_manager": table_trade_performance_manager
                })

            logging.debug(f"Dispatching action '{action}' to handler {handler.__name__} with args: {list(handler_args.keys())}")
            handler(**handler_args)
        else:
            handle_unknown_action(action, composer, ch, method) # Uses global rabbit_connection

    # --- Outer Exception Handling ---
    except (ConfigurationError, TokenAuthenticationException) as critical_config_err:
         handle_exception(critical_config_err, composer, ch, method, body, is_critical=True, exit_code=2, log_level=logging.CRITICAL)
    except (DatabaseOperationException) as critical_db_err:
         # Specific handling for critical DB errors bubbled up
         handle_exception(critical_db_err, composer, ch, method, body, is_critical=True, exit_code=12, log_level=logging.CRITICAL)
    except PositionNotFoundException as critical_runtime_err:
         handle_exception(critical_runtime_err, composer, ch, method, body, is_critical=True, exit_code=3, log_level=logging.CRITICAL)
    except PositionCloseException as position_err:
         # If this bubbles up, treat as critical needing investigation
         handle_exception(position_err, composer, ch, method, body, is_critical=True, exit_code=4, log_level=logging.ERROR)
    except (SaxoApiError, ApiRequestException) as api_err:
         # Treat persistent API errors as critical
        handle_exception(api_err, composer, ch, method, body, is_critical=True, exit_code=5, log_level=logging.ERROR)
    except ValueError as val_err:
         # Only treat as critical if explicitly marked
         if "CRITICAL" in str(val_err).upper():
             handle_exception(val_err, composer, ch, method, body, is_critical=True, exit_code=6, log_level=logging.CRITICAL)
         else:
             # Non-critical ValueErrors should ideally be handled and ACKed lower down.
             # If one bubbles up here, log it but don't exit. Assume prior handler failed to ACK.
             handle_exception(val_err, composer, ch, method, body, is_critical=False, log_level=logging.ERROR)
    except WebSocketConnectionException as ws_err:
         # Non-critical
         handle_exception(ws_err, composer, ch, method, body, is_critical=False, log_level=logging.WARNING)
    except Exception as e:
        # Catch-all for truly unexpected errors
        handle_exception(e, composer, ch, method, body, is_critical=True, exit_code=1, log_level=logging.CRITICAL)


# --- Main Execution Block ---

if __name__ == "__main__":
    global rabbit_connection
    rabbit_connection = None
    channel = None

    try:
        APP_VERSION = get_version()

        # --- 1. Configuration ---
        config_path = os.getenv("WATA_CONFIG_PATH")
        if not config_path:
            print("FATAL: WATA_CONFIG_PATH environment variable not set", file=sys.stderr)
            sys.exit(10)
        try:
            config_manager = ConfigurationManager(config_path)
            print("Configuration loaded and validated successfully.")
        except Exception as e:
            print(f"FATAL: Configuration validation failed: {e}", file=sys.stderr)
            print(traceback.format_exc(), file=sys.stderr)
            sys.exit(11)

        # --- 2. Logging ---
        setup_logging(config_manager, "wata-trader")
        logging.info(f"--- Starting WATA Trader v{APP_VERSION} ---")

        # --- 3. Database Initialization ---
        logging.info("Initializing database managers...")
        try:
            db_order_manager = DbOrderManager(config_manager)
            db_position_manager = DbPositionManager(config_manager)
            table_trade_performance_manager = DbTradePerformanceManager(config_manager)
            logging.info("Database managers initialized.")
        except Exception as e:
            logging.critical(f"Failed to initialize database managers: {e}", exc_info=True)
            sys.exit(12)

        # --- 4. Trading Rules ---
        logging.info("Initializing trading rules...")
        try:
            trading_rule = TradingRule(config_manager, db_position_manager)
            trade_turbo_exchange_id = config_manager.get_config_value(
                "trade.config.turbo_preference.exchange_id"
            )
            logging.info(f"Trading rules initialized. Preferred exchange: {trade_turbo_exchange_id}")
        except Exception as e:
             logging.critical(f"Failed to initialize TradingRule: {e}", exc_info=True)
             sys.exit(13)

        # --- 5. RabbitMQ Connection ---
        logging.info("Connecting to RabbitMQ...")
        try:
            rabbitmq_config = config_manager.get_rabbitmq_config()
            credentials = pika.PlainCredentials(
                rabbitmq_config["authentication"]["username"],
                rabbitmq_config["authentication"]["password"],
            )
            parameters = pika.ConnectionParameters(
                host=rabbitmq_config["hostname"],
                credentials=credentials,
                heartbeat=600,
                blocked_connection_timeout=300
            )
            rabbit_connection = pika.BlockingConnection(parameters)
            channel = rabbit_connection.channel()
            channel.queue_declare(queue="trading-action")
            logging.info("RabbitMQ connection established and queue declared.")
        except Exception as e:
            logging.critical(f"Failed to connect to RabbitMQ: {e}", exc_info=True)
            sys.exit(14)

        # --- 6. Saxo Components Initialization ---
        logging.info("Initializing Saxo components...")
        account_key = None
        client_key = None
        from src.saxo_openapi.contrib.session import account_info

        try:
            # Initialize Auth and API Client first
            saxo_auth = SaxoAuth(config_manager, rabbit_connection)
            api_client = SaxoApiClient(config_manager, saxo_auth)

            # Fetch Account/Client keys using the utility
            logging.info("Fetching AccountKey and ClientKey using account_info utility...")
            try:
                acc_info = account_info(api_client)
                account_key = acc_info.AccountKey
                client_key = acc_info.ClientKey
                logging.info(
                    f"Successfully retrieved keys. Using AccountKey: {account_key}, ClientKey: {client_key}")
            except (ApiRequestException, SaxoApiError, TokenAuthenticationException) as api_err:
                # Catch errors specifically from the API call within account_info
                logging.critical(f"Failed API call within account_info utility: {api_err}", exc_info=True)
                raise ConfigurationError(
                    f"Could not retrieve Account/Client Keys via account_info: {api_err}") from api_err
            except (IndexError, KeyError, AttributeError) as data_err:
                # Catch potential errors if the response structure is unexpected
                logging.critical(
                    f"Unexpected data structure received from account_info's underlying API call: {data_err}",
                    exc_info=True)
                raise ConfigurationError(
                    f"Failed to parse account details from API response: {data_err}") from data_err
            except Exception as e:
                # Catch any other unexpected error during the utility call
                logging.critical(f"Unexpected error calling account_info utility: {e}", exc_info=True)
                raise ConfigurationError(f"Failed to get account details via account_info: {e}") from e

            # Instantiate domain services with the retrieved keys
            instrument_service = InstrumentService(api_client, config_manager, account_key)
            order_service = OrderService(api_client, account_key, client_key)
            position_service = PositionService(api_client, order_service, config_manager, account_key, client_key)

            # Instantiate high-level orchestrators/monitors
            trading_orchestrator = TradingOrchestrator(instrument_service, order_service, position_service,
                                                       config_manager, db_order_manager, db_position_manager)
            performance_monitor = PerformanceMonitor(position_service, order_service, config_manager,
                                                     db_position_manager, trading_rule, rabbit_connection)

            logging.info("Saxo services initialized successfully.")

        # Keep the outer exception handling for critical init failures
        except (ConfigurationError, TokenAuthenticationException, SaxoApiError, ApiRequestException) as e:
            logging.critical(f"Failed to initialize Saxo components: {e}", exc_info=True)
            if rabbit_connection and rabbit_connection.is_open:
                try:
                    send_message_to_mq_for_telegram(rabbit_connection,
                                                    f"🚨 CRITICAL FAILURE: Trader failed to initialize Saxo components: {e}")
                except Exception as mq_err:
                    logging.error(f"Failed to send Saxo init failure to Telegram MQ: {mq_err}")
            sys.exit(15)
        except Exception as e:  # Catch unexpected init errors
            logging.critical(f"Unexpected error initializing Saxo components: {e}", exc_info=True)
            if rabbit_connection and rabbit_connection.is_open:
                try:
                    send_message_to_mq_for_telegram(rabbit_connection,
                                                    f"🚨 CRITICAL FAILURE: Unexpected error initializing Trader Saxo components: {e}")
                except Exception as mq_err:
                    logging.error(f"Failed to send Saxo init failure to Telegram MQ: {mq_err}")
            sys.exit(16)


        # --- 7. Setup Consumer Callback with Dependencies ---
        callback_with_deps = partial(
            callback,
            trading_orchestrator=trading_orchestrator,
            performance_monitor=performance_monitor,
            trading_rule=trading_rule,
            db_position_manager=db_position_manager,
            table_trade_performance_manager=table_trade_performance_manager,
            trade_turbo_exchange_id=trade_turbo_exchange_id,
            rabbit_conn=rabbit_connection
        )

        # --- 8. Start Consuming ---
        channel.basic_consume(
            queue="trading-action",
            on_message_callback=callback_with_deps,
            auto_ack=False
        )

        logging.info("Trader service startup complete. Waiting for messages...")
        send_message_to_mq_for_telegram(rabbit_connection, f"✅📈 WATA Trader v{APP_VERSION} is running and ready for orders.")

        channel.start_consuming()

    # --- Global Exception Handling for Startup ---
    except Exception as e:
        logging.critical(f"Unhandled exception during startup: {e}", exc_info=True)
        if rabbit_connection and rabbit_connection.is_open:
            try: send_message_to_mq_for_telegram(rabbit_connection, f"🚨 CRITICAL STARTUP FAILURE: WATA Trader failed: {e}")
            except Exception as mq_err: logging.error(f"Failed to send final startup error to Telegram MQ: {mq_err}")
        else:
             print(f"FATAL: Unhandled exception during startup: {e}", file=sys.stderr)
             print(traceback.format_exc(), file=sys.stderr)
        sys.exit(1) # General critical failure exit code

    finally:
        # --- Cleanup ---
        if rabbit_connection and rabbit_connection.is_open:
            logging.info("Closing RabbitMQ connection.")
            rabbit_connection.close()
        logging.info(f"--- WATA Trader v{APP_VERSION} Shutting Down ---")