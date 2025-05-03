import os
import traceback
import json
import logging
import jsonschema
import pika
from functools import partial
import sys # For exit

# --- Configuration and Core Components ---
from configuration import ConfigurationManager
from trade.api_actions import SaxoService # Assuming still used, or import specific services later
from trade.rules import TradingRule
from schema import SchemaLoader
from database import DbOrderManager, DbPositionManager, DbTradePerformanceManager
from mq_telegram.tools import send_message_to_mq_for_telegram
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
    detail_message = f"{log_message}\n{traceback.format_exc()}" # Include traceback for critical/general errors

    logging.log(log_level, detail_message)

    # Try to compose a message, fall back to raw error if composer failed early
    if composer:
        composer.add_generic_error(error_type, e, is_critical=is_critical)
        # Optionally add traceback details for critical errors
        if is_critical or log_level >= logging.ERROR:
             composer.add_text_section("Traceback Snippet", traceback.format_exc(limit=5))
        telegram_message = composer.get_message()
    else:
        # If composer isn't available (e.g., error during JSON parsing/validation)
        raw_body_str = body.decode() if isinstance(body, bytes) else str(body)
        telegram_message = f"CRITICAL ERROR ({error_type}) processing raw message: {raw_body_str}\n\nError: {e}\n\n{traceback.format_exc()}"

    try:
        # Ensure rabbit_connection is accessible if needed here, or pass it in
        # For simplicity, assume rabbit_connection is globally accessible in this scope or passed
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
        except Exception as ack_err:
             logging.error(f"Failed to ACK message {method.delivery_tag} after handling error: {ack_err}")

    # Exit for critical errors
    if is_critical and exit_code is not None:
        logging.critical(f"Terminating service due to critical error ({error_type}). Exit code: {exit_code}")
        sys.exit(exit_code)

def handle_validation_error(e, body, ch, method):
    """Handles JSON schema validation errors."""
    logging.error(f"Schema Validation Error: {e}")
    raw_body_str = body.decode() if isinstance(body, bytes) else str(body)
    error_msg = f"SCHEMA ERROR: Invalid message format received.\n\nError: {e}\n\nRaw Body:\n{raw_body_str}"
    send_message_to_mq_for_telegram(rabbit_connection, error_msg)
    ch.basic_ack(delivery_tag=method.delivery_tag) # Ack invalid message

def handle_rule_violation(trv, composer, ch, method):
    """Handles expected trading rule violations."""
    logging.warning(f"Trading Rule Violation: {trv}") # Use warning for expected violations
    composer.add_rule_violation(trv)
    send_message_to_mq_for_telegram(rabbit_connection, composer.get_message())
    ch.basic_ack(delivery_tag=method.delivery_tag)

def handle_trade_setup_issue(trade_issue, composer, ch, method):
    """Handles issues like NoTurbos, NoMarket, InsufficientFunds (pre-order)."""
    logging.warning(f"Trade Setup Issue: {trade_issue}") # Use warning
    if isinstance(trade_issue, (NoMarketAvailableException, NoTurbosAvailableException)):
         # Pass search context if available (might need modification in exception)
        composer.add_turbo_search_result(error=trade_issue, search_context=getattr(trade_issue, 'search_context', None))
    elif isinstance(trade_issue, InsufficientFundsException):
         composer.add_position_result(error=trade_issue) # Or a specific pre-buy error context
    else:
         composer.add_generic_error(type(trade_issue).__name__, trade_issue)

    send_message_to_mq_for_telegram(rabbit_connection, composer.get_message())
    ch.basic_ack(delivery_tag=method.delivery_tag)

def handle_order_placement_error(ope, composer, ch, method):
    """Handles specific order rejection errors from Saxo."""
    logging.error(f"Order Placement Rejected by Saxo: {ope}")
    composer.add_generic_error("Order Placement Rejected", ope)
    # Add more specific details from ope if needed (e.g., ope.saxo_error_details)
    send_message_to_mq_for_telegram(rabbit_connection, composer.get_message())
    ch.basic_ack(delivery_tag=method.delivery_tag) # Ack, Saxo rejected it, not a system fault

def handle_unknown_action(action, composer, ch, method):
    """Handles messages with unrecognized actions."""
    logging.error(f"Unknown action received: {action}")
    composer.add_generic_error("Unknown Action", ValueError(f"Action '{action}' not recognized."))
    send_message_to_mq_for_telegram(rabbit_connection, composer.get_message())
    ch.basic_ack(delivery_tag=method.delivery_tag)


# --- Action Handlers ---

def handle_trading_action(data, composer, ch, method, service, trading_rule, db_position_manager, trade_turbo_exchange_id, rabbit_connection):
    """Handles 'long' and 'short' trading actions."""
    action = data.get("action")
    logging.info(f"Processing trading action: {action} for {data.get('indice')}")

    founded_turbo = None
    buy_details = None
    search_context = None

    try:
        # 1. Rule Checks
        logging.debug("Checking trading rules...")
        trading_rule.check_signal_timestamp(action, data.get("alert_timestamp"))
        trading_rule.check_market_hours(data.get("signal_timestamp"))
        indice_id = trading_rule.get_allowed_indice_id(data.get("indice"))
        TradingRule.check_if_open_position_is_same_signal(action, db_position_manager)
        trading_rule.check_profit_per_day()
        logging.debug("Trading rules passed.")

        # 2. Close existing positions (if any) - Moved from inside try/except block below
        logging.debug("Checking for existing positions to close...")
        all_positions = service.get_user_open_positions()
        service.check_and_act_close_on_current_positions(all_positions)
        logging.debug("Existing positions handled.")

        # 3. Find Turbo
        logging.info(f"Searching Turbo: Exchange {trade_turbo_exchange_id}, action {action}, indice {indice_id}")
        search_context = { # Store context for potential error reporting
            'Keywords': action,
            'min_price': service.turbo_price_range.get("min"),
            'max_price': service.turbo_price_range.get("max")
        }
        founded_turbo = service.find_turbos(
            trade_turbo_exchange_id, indice_id, action
        )
        composer.add_turbo_search_result(founded_turbo=founded_turbo)
        logging.info(f"Found Turbo: {founded_turbo['appParsedData'].get('name', 'N/A')}")

        # 4. Buy Turbo
        logging.info("Attempting to buy turbo...")
        buy_details = service.buy_turbo_instrument(founded_turbo)
        composer.add_position_result(buy_details=buy_details)
        logging.info(f"Successfully placed buy order {buy_details['orders_list'][0]['order_id']} and confirmed position {buy_details['position']['position_id']}")

        # 5. Final Success Message & Ack
        send_message_to_mq_for_telegram(rabbit_connection, composer.get_message())
        ch.basic_ack(delivery_tag=method.delivery_tag)
        logging.info(f"Successfully processed trading action: {action}")

    # --- Expected/Handled Errors during Trading ---
    except TradingRuleViolation as trv:
        handle_rule_violation(trv, composer, ch, method) # Logs, sends message, acks
    except (NoMarketAvailableException, NoTurbosAvailableException) as e:
        # Update composer based on where the error happened
        if not founded_turbo:
            composer.add_turbo_search_result(error=e, search_context=search_context)
        else: # Should not happen if find_turbos succeeded, but defensive
             composer.add_generic_error(f"Turbo Search Issue ({type(e).__name__})", e)
        handle_trade_setup_issue(e, composer, ch, method) # Logs, sends message, acks
    except InsufficientFundsException as e:
        # Error during calculate_bid_amount or similar pre-order check
        if founded_turbo:
             composer.add_position_result(error=e) # Associate error with the position attempt
        else:
             composer.add_turbo_search_result(error=e, search_context=search_context) # If somehow funds checked before search
        handle_trade_setup_issue(e, composer, ch, method) # Logs, sends message, acks
    except OrderPlacementError as ope:
        # Saxo explicitly rejected the order placement attempt
        if founded_turbo:
            composer.add_position_result(error=ope, order_details=ope.order_details)
        else: # Should not happen
            composer.add_generic_error("Order Placement Failed (Pre-Search?)", ope)
        handle_order_placement_error(ope, composer, ch, method) # Logs specific details, sends msg, acks

    # --- Critical Errors during Trading (Need to Re-raise for main handler) ---
    except PositionNotFoundException as e:
         # Critical: Order placed but position confirmation failed
        logging.critical(f"CRITICAL: Position not found after placing order {e.order_id}: {e}")
        composer.add_position_result(error=e, order_id=e.order_id) # Add details before raising
        raise # Re-raise to be caught by the main callback's critical handler

    except ValueError as ve:
        # Catch other ValueErrors potentially raised by SaxoService (e.g., calculation issues)
        # Or critical errors raised explicitly within this handler
        logging.error(f"ValueError during trading action '{action}': {ve}")
        if founded_turbo and not buy_details:
            composer.add_position_result(error=ve)
        elif not founded_turbo:
            composer.add_turbo_search_result(error=ve, search_context=search_context)
        else:
            composer.add_generic_error(f"ValueError in {action}", ve)
        # Decide if this specific ValueError is critical. If raised explicitly for critical condition:
        if "CRITICAL" in str(ve):
            raise # Re-raise critical ValueErrors
        else:
            # Treat as non-critical for now, send message and ack
            send_message_to_mq_for_telegram(rabbit_connection, composer.get_message())
            ch.basic_ack(delivery_tag=method.delivery_tag)

    # Let other unexpected exceptions bubble up to the main handler

def handle_close_action(data, composer, ch, method, service, rabbit_connection):
    """Handles 'close-long', 'close-short', 'close-position' actions."""
    action = data.get("action")
    logging.info(f"Processing close action: {action}")

    try:
        close_action_param = None
        if action == "close-long":
            close_action_param = "long"
        elif action == "close-short":
            close_action_param = "short"
        # "close-position" implies closing any/all managed positions (param is None)

        logging.debug("Fetching open positions to evaluate for closure...")
        all_positions = service.get_user_open_positions()
        service.check_and_act_close_on_current_positions(all_positions, close_action_param)
        logging.info(f"Successfully processed '{action}' signal and attempted closures.")

        # Optional: Send a summary message - depends on whether check_and_act sends enough detail
        composer.add_text_section(f"--- {action.upper()} ACTION ---", f"Processed '{action}' signal. Check previous messages for specific closure details.")
        send_message_to_mq_for_telegram(rabbit_connection, composer.get_message())

        ch.basic_ack(delivery_tag=method.delivery_tag)

    except PositionCloseException as pce:
         # Specific failure during closing attempt (maybe already closed, API error)
         logging.error(f"Failed to close position during '{action}': {pce}")
         composer.add_generic_error(f"Position Close Error ({action})", pce)
         # Decide if this is critical. Re-raise if it should stop the service.
         raise # Re-raise to be caught by main handler as potentially critical

    # Let other unexpected exceptions bubble up

def handle_check_positions(data, composer, ch, method, service, rabbit_connection):
    """Handles 'check_positions_on_saxo_api' action."""
    action = "check_positions_on_saxo_api" # Define action for logging/error context
    logging.info(f"Processing action: {action}")
    try:
        logging.debug("Checking positions performance...")
        # These methods might raise exceptions (API errors, DB errors, etc.)
        service.check_positions_performance() # This might close positions and send messages
        logging.debug("Checking if DB open positions are closed on API...")
        service.check_if_db_open_position_are_closed_on_api() # This might update DB and send messages

        logging.info(f"{action}: Position checks completed successfully.")
        # Optional confirmation message:
        # composer.add_text_section("Position Check", "Completed checks successfully.")
        # send_message_to_mq_for_telegram(rabbit_connection, composer.get_message())

        # Acknowledge the message ONLY if all checks were successful
        ch.basic_ack(delivery_tag=method.delivery_tag)
        logging.debug(f"{action}: Message ack'd successfully.")

    except Exception as e:
        # Log the error with context specific to this handler
        logging.error(f"Error during '{action}': {e}", exc_info=True) # exc_info=True adds traceback to log

        # Add error details to the composer *before* re-raising
        # This allows the main handler to potentially use the composer state
        if composer:
             composer.add_generic_error(f"Error in {action}", e)
        else: # Should have a composer, but defensive check
             logging.error(f"Composer object not available when handling error in {action}")
             # Send a raw message if composer failed somehow earlier
             send_message_to_mq_for_telegram(rabbit_connection, f"RAW ERROR in {action} (no composer): {e}")

        # Re-raise the exception. It will be caught by the main callback's
        raise e

def handle_daily_stats(data, composer, ch, method, db_position_manager, table_trade_performance_manager, rabbit_connection):
    """Handles 'daily_stats' action."""
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
        # Send specific error message
        error_msg = f"ERROR processing daily stats (DB): {dbe}"
        send_message_to_mq_for_telegram(rabbit_connection, error_msg)
        # Re-raise as this might be critical
        raise
    except Exception as e:
        logging.error(f"Failed to process daily stats: {e}")
        # Send general error message
        error_msg = f"ERROR processing daily stats: {e}\n{traceback.format_exc(limit=3)}"
        send_message_to_mq_for_telegram(rabbit_connection, error_msg)
        # Decide if general errors here are critical - let's assume not for now
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
             # Injected dependencies follow:
             saxo_service, trading_rule, db_position_manager,
             table_trade_performance_manager, trade_turbo_exchange_id,
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
        except json.JSONDecodeError as e:
            logging.error(f"JSON Decode Error: {e} - Raw body: {body.decode(errors='ignore')}")
            error_msg = f"CRITICAL: Failed to decode JSON message body: {e}\n\nBody:\n{body.decode(errors='ignore')}"
            send_message_to_mq_for_telegram(rabbit_conn, error_msg)
            ch.basic_ack(delivery_tag=method.delivery_tag) # Ack invalid message
            return
        except Exception as e: # Catch other potential decoding errors
            logging.error(f"Error decoding message body: {e}")
            error_msg = f"CRITICAL: Unexpected error decoding message body: {e}\n\nBody:\n{body.decode(errors='ignore')}"
            send_message_to_mq_for_telegram(rabbit_conn, error_msg)
            ch.basic_ack(delivery_tag=method.delivery_tag) # Ack invalid message
            return

        # 2. Schema Validation
        try:
            jsonschema.validate(
                instance=data_from_mq, schema=SchemaLoader.get_trading_action_schema()
            )
        except jsonschema.exceptions.ValidationError as e:
            handle_validation_error(e, body, ch, method) # Logs, sends message, acks
            return # Stop processing this invalid message

        # 3. Initialize Composer
        composer = TelegramMessageComposer(data_from_mq)

        # 4. Dispatch to Handler
        action = data_from_mq.get("action")
        handler = ACTION_HANDLERS.get(action)

        if handler:
            # --- *** Argument Construction Logic *** ---
            # Start with arguments common to *most* handlers
            handler_args = {
                "data": data_from_mq,
                "composer": composer,
                "ch": ch,
                "method": method,
                "rabbit_connection": rabbit_conn # Renamed from rabbit_conn for clarity within handlers
            }

            # Add arguments based on the specific action/handler
            if action in ["long", "short"]:
                handler_args["service"] = saxo_service
                handler_args["trading_rule"] = trading_rule
                handler_args["db_position_manager"] = db_position_manager
                handler_args["trade_turbo_exchange_id"] = trade_turbo_exchange_id
            elif action in ["close-long", "close-short", "close-position"]:
                handler_args["service"] = saxo_service
            elif action == "check_positions_on_saxo_api":
                handler_args["service"] = saxo_service
            elif action == "daily_stats":
                handler_args["db_position_manager"] = db_position_manager
                handler_args["table_trade_performance_manager"] = table_trade_performance_manager

            # --- *** Call the handler with tailored arguments *** ---
            logging.debug(f"Dispatching action '{action}' to handler {handler.__name__} with args: {list(handler_args.keys())}")
            handler(**handler_args) # Use keyword argument unpacking

        else:
            handle_unknown_action(action, composer, ch, method) # Logs, sends message, acks

    # --- Outer Exception Handling (Catching errors bubbling up from handlers) ---
    except (ConfigurationError, TokenAuthenticationException, DatabaseOperationException) as critical_config_err:
         # These often mean the service cannot function correctly.
        handle_exception(critical_config_err, composer, ch, method, body, is_critical=True, exit_code=2, log_level=logging.CRITICAL)
    except PositionNotFoundException as critical_runtime_err:
         # Specific case: Order placed but position vanished. Treat as critical.
        handle_exception(critical_runtime_err, composer, ch, method, body, is_critical=True, exit_code=3, log_level=logging.CRITICAL)
    except PositionCloseException as position_err:
         # Error during position closure - might be critical depending on context
         # For now, treat as critical error needing investigation
         handle_exception(position_err, composer, ch, method, body, is_critical=True, exit_code=4, log_level=logging.ERROR)
    except (SaxoApiError, ApiRequestException) as api_err:
         # General API errors - could be transient or persistent. Exit might be safest.
        handle_exception(api_err, composer, ch, method, body, is_critical=True, exit_code=5, log_level=logging.ERROR)
    except ValueError as val_err:
         # Catch ValueErrors raised explicitly as critical from handlers
         if "CRITICAL" in str(val_err):
             handle_exception(val_err, composer, ch, method, body, is_critical=True, exit_code=6, log_level=logging.CRITICAL)
         else:
             # Non-critical ValueError (should ideally be caught lower down)
             handle_exception(val_err, composer, ch, method, body, is_critical=False, log_level=logging.ERROR)
    except WebSocketConnectionException as ws_err:
         # Usually not critical, just log and continue
         handle_exception(ws_err, composer, ch, method, body, is_critical=False, log_level=logging.WARNING)
    except Exception as e:
        # Catch-all for truly unexpected errors
        handle_exception(e, composer, ch, method, body, is_critical=True, exit_code=1, log_level=logging.CRITICAL)


# --- Main Execution Block ---

if __name__ == "__main__":
    # Use global here for simplicity in error reporting before consumer starts
    # In a class-based approach, this would be an instance variable.
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
            # Attempt to notify via MQ if possible before exiting
            # Note: MQ connection not yet established, might need rethink if early DB fails
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
                heartbeat=600, # Added heartbeat for robustness
                blocked_connection_timeout=300
            )
            rabbit_connection = pika.BlockingConnection(parameters)
            channel = rabbit_connection.channel()
            channel.queue_declare(queue="trading-action", durable=True) # Ensure queue is durable
            channel.basic_qos(prefetch_count=1) # Process one message at a time
            logging.info("RabbitMQ connection established and queue declared.")
        except Exception as e:
            logging.critical(f"Failed to connect to RabbitMQ: {e}", exc_info=True)
            sys.exit(14)

        # --- 6. Saxo Service Initialization ---
        logging.info("Initializing Saxo service...")
        try:
            # Pass the established rabbit_connection for notifications within SaxoService if needed
            saxo_service = SaxoService(
                config_manager,
                db_order_manager,
                db_position_manager,
                rabbit_connection, # Pass connection for internal notifications
                trading_rule
            )
            # Perform a quick check if possible (e.g., get account info)
            _ = saxo_service.account_info # Trigger initial auth/account fetch
            logging.info(f"Saxo service initialized successfully for Account: {saxo_service.account_info.AccountId}")
        except (TokenAuthenticationException, ConfigurationError, SaxoApiError, ApiRequestException) as e:
             logging.critical(f"Failed to initialize SaxoService: {e}", exc_info=True)
             # Attempt to send critical failure message
             try:
                  send_message_to_mq_for_telegram(rabbit_connection, f"CRITICAL FAILURE: Trader failed to initialize SaxoService: {e}")
             except Exception as mq_err:
                  logging.error(f"Failed to send Saxo init failure to Telegram MQ: {mq_err}")
             sys.exit(15)
        except Exception as e: # Catch any other unexpected init error
             logging.critical(f"Unexpected error initializing SaxoService: {e}", exc_info=True)
             try:
                  send_message_to_mq_for_telegram(rabbit_connection, f"CRITICAL FAILURE: Unexpected error initializing Trader SaxoService: {e}")
             except Exception as mq_err:
                  logging.error(f"Failed to send Saxo init failure to Telegram MQ: {mq_err}")
             sys.exit(16)


        # --- 7. Setup Consumer Callback with Dependencies ---
        callback_with_deps = partial(
            callback,
            # Inject dependencies here:
            saxo_service=saxo_service,
            trading_rule=trading_rule,
            db_position_manager=db_position_manager,
            table_trade_performance_manager=table_trade_performance_manager,
            trade_turbo_exchange_id=trade_turbo_exchange_id,
            rabbit_conn=rabbit_connection # Pass connection for handlers to use
        )

        # --- 8. Start Consuming ---
        channel.basic_consume(
            queue="trading-action",
            on_message_callback=callback_with_deps,
            auto_ack=False # Manual acknowledgement is handled in callback/handlers
        )

        logging.info("Trader service startup complete. Waiting for messages...")
        send_message_to_mq_for_telegram(rabbit_connection, f"✅📈 WATA Trader v{APP_VERSION} is running and ready for orders.")

        channel.start_consuming()

    # --- Global Exception Handling for Startup ---
    except Exception as e:
        logging.critical(f"Unhandled exception during startup: {e}", exc_info=True)
        # Try sending a final notification if RabbitMQ connection was established
        if rabbit_connection and rabbit_connection.is_open:
            try:
                send_message_to_mq_for_telegram(rabbit_connection, f"🚨 CRITICAL STARTUP FAILURE: WATA Trader failed: {e}")
            except Exception as mq_err:
                logging.error(f"Failed to send final startup error to Telegram MQ: {mq_err}")
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