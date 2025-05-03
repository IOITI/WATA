import os
import traceback
from time import sleep
from configuration import ConfigurationManager
from trade.api_actions import SaxoService
from trade.rules import TradingRule
# Import specific exceptions directly
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
from schema import SchemaLoader
from database import DbOrderManager, DbPositionManager, DbTradePerformanceManager
from mq_telegram.tools import send_message_to_mq_for_telegram
from message_helper import (
    generate_daily_stats_message,
    generate_performance_stats_message,
    TelegramMessageComposer
)
import jsonschema
import pika
import json
import logging
from logging_helper import setup_logging

# Global instance of SaxoService
saxo_service = None

def initialize_saxo_service():
    global saxo_service
    if saxo_service is None:
        saxo_service = SaxoService(
            config_manager,
            db_order_manager,
            db_position_manager,
            rabbit_connection,
            trading_rule
        )
    return saxo_service

def get_version():
    version_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'VERSION')
    with open(version_file, 'r') as file:
        version = file.read().strip()
    return version

def callback(ch, method, properties, body):
    global data_from_mq
    composer = None
    try:
        print(" [x] Received %r" % body)
        logging.info(f"Received action {body}")
        # Convert the JSON string in 'body' to a Python dictionary
        data_from_mq = json.loads(body)

        # --- Schema Validation ---
        try:
            jsonschema.validate(
                instance=data_from_mq, schema=SchemaLoader.get_trading_action_schema()
            )
        except jsonschema.exceptions.ValidationError as e:
            logging.error(f"Invalid data received: {e}")
            # Create composer *just* for the error message
            composer = TelegramMessageComposer(data_from_mq if isinstance(data_from_mq, dict) else {"raw_body": body.decode()})
            composer.add_generic_error("Schema Validation", e)
            send_message_to_mq_for_telegram(rabbit_connection, composer.get_message())
            ch.basic_ack(delivery_tag=method.delivery_tag)
            raise e

        # --- Initialize Composer Properly Now ---
        composer = TelegramMessageComposer(data_from_mq)

        # Log the signal_id if present
        if "signal_id" in data_from_mq:
            logging.info(f"Processing signal with ID: {data_from_mq['signal_id']}")

        # --- Handle non-trading actions ---
        action = data_from_mq.get("action")
        if action == "check_positions_on_saxo_api":
            try:
                # Get the singleton instance of SaxoService
                service = initialize_saxo_service()

                service.check_positions_performance()
                # Check if opened positions are closed on Saxo API
                service.check_if_db_open_position_are_closed_on_api()

            except Exception as e:
                logging.error(
                    f"Failed to check if opened positions are closed by Saxo during downtime : {e}"
                )
                ch.basic_ack(delivery_tag=method.delivery_tag)
                raise e
            logging.info(
                f"Check if opened positions are closed by Saxo during downtime is successful"
            )
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return
        elif action == "daily_stats":
            try:
                # Number of days for performance stats
                days = 7

                # Get the stats of the day
                stats_of_the_day = db_position_manager.get_stats_of_the_day()

                # Generate the daily stats message
                message = generate_daily_stats_message(stats_of_the_day)

                # Get last n days' percentages
                last_days_percentages = db_position_manager.get_percent_of_last_n_days(days)
                last_best_days_percentages = db_position_manager.get_best_percent_of_last_n_days(days)
                last_days_percentages_on_max = db_position_manager.get_theoretical_percent_of_last_n_days_on_max(days)
                last_best_days_percentages_on_max = db_position_manager.get_best_theoretical_percent_of_last_n_days_on_max(days)

                # Generate and append the performance stats to the message
                message = generate_performance_stats_message(
                    message,
                    days,
                    last_days_percentages,
                    last_best_days_percentages,
                    last_days_percentages_on_max,
                    last_best_days_percentages_on_max
                )

                # Send the message
                send_message_to_mq_for_telegram(rabbit_connection, message)

                try:
                    table_trade_performance_manager.create_last_day_trade_performance_data()
                except Exception as e:
                    logging.error(f"Failed to insert daily stats in table trade_performance: {e}")
                    send_message_to_mq_for_telegram(
                        rabbit_connection, f"ERROR: Failed to insert daily stats in table trade_performance: {e}"
                    )
                    raise e

            except Exception as e:
                logging.error(f"Failed to process daily stats : {e}")
                send_message_to_mq_for_telegram(
                    rabbit_connection, f"ERROR: Failed to process daily stats : {e}"
                )
                ch.basic_ack(delivery_tag=method.delivery_tag)
                raise e

            logging.info(
                f"Daily stats processed successfully and send to telegram"
            )
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return

        # --- Trading Actions Logic ---

        # Initialize Saxo service if needed
        try:
            service = initialize_saxo_service()
        except Exception as e:
            logging.critical(f"Failed to create SaxoService : {e}")
            # Use composer if initialized, otherwise send raw error
            error_msg = f"CRITICAL: Failed to create SaxoService : {e}"
            if composer:
                 composer.add_generic_error("Service Initialization", e)
                 send_message_to_mq_for_telegram(rabbit_connection, composer.get_message())
            else:
                 send_message_to_mq_for_telegram(rabbit_connection, error_msg)
            exit(1) # Critical failure


        indice_id = None

        try:
            if action in ["long", "short", "check_positions_on_saxo_api"]:
                # Check if the signal_timestamp is too old
                trading_rule.check_signal_timestamp(data_from_mq.get("action"), data_from_mq.get("alert_timestamp"))
            if action in ["long", "short"]:
                # Check if the current time is within the allowed market hours, or dates
                trading_rule.check_market_hours(data_from_mq.get("signal_timestamp"))
                # Check if the indice exists in the indices dictionary and get its ID
                indice_id = trading_rule.get_allowed_indice_id(data_from_mq.get("indice"))
                # Check if there is an open position with the same signal
                TradingRule.check_if_open_position_is_same_signal(action, db_position_manager)
                # Check if profit per day is done
                trading_rule.check_profit_per_day()

        except TradingRuleViolation as trv:
            # Handle trading rule violations (these are expected and should not exit)
            message = f"BREAKING RULE: {trv}"
            logging.error(message)
            composer.add_rule_violation(trv) # Add specific violation details
            send_message_to_mq_for_telegram(rabbit_connection, composer.get_message())
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return # Stop processing rule violation

        # --- Main Trading Logic (Buy/Sell) ---
        if action in ["long", "short"]:
            founded_turbo = None
            buy_details = None
            search_context = None # To store search params for error reporting

            try:
                # 1. Close existing positions (if any)
                all_positions = service.get_user_open_positions()
                service.check_and_act_close_on_current_positions(all_positions)

                # 2. Find Turbo
                signal = action
                logging.info(f"Searching Turbo: Exchange {trade_turbo_exchange_id}, action {signal}, indice {indice_id}")
                search_context = { # Store context for potential error reporting
                    'Keywords': signal,
                    'min_price': service.turbo_price_range.get("min"),
                    'max_price': service.turbo_price_range.get("max")
                }
                founded_turbo = service.find_turbos(
                    trade_turbo_exchange_id, indice_id, signal
                )
                composer.add_turbo_search_result(founded_turbo=founded_turbo) # Add success details

                # 3. Buy Turbo
                buy_details = service.buy_turbo_instrument(founded_turbo)
                composer.add_position_result(buy_details=buy_details) # Add success details

                # 4. Send final success message
                send_message_to_mq_for_telegram(rabbit_connection, composer.get_message())
                ch.basic_ack(delivery_tag=method.delivery_tag)
                sleep(1)

            except (NoMarketAvailableException, NoTurbosAvailableException) as e:
                # Specific, expected errors during search
                logging.warning(f"Turbo search failed: {e}")
                # Pass search_context only if it's relevant (NoTurbosAvailableException)
                context_to_pass = search_context if isinstance(e, NoTurbosAvailableException) else None
                composer.add_turbo_search_result(error=e, search_context=context_to_pass)
                send_message_to_mq_for_telegram(rabbit_connection, composer.get_message())
                ch.basic_ack(delivery_tag=method.delivery_tag)
                return # Stop processing for this signal

            except InsufficientFundsException as e:
                logging.warning(f"Trade action '{action}' skipped due to insufficient funds: {e}")
                # This error happens when trying to calculate the amount *before* placing the order.
                # It's part of the "position placement" attempt.
                if founded_turbo:  # Make sure we have turbo details to associate with the error
                    composer.add_position_result(error=e)  # Add the specific error context
                    send_message_to_mq_for_telegram(rabbit_connection, composer.get_message())
                else:
                    # Should not happen if find_turbos succeeded, but handle defensively
                    composer.add_generic_error("Insufficient Funds (Pre-Buy)", e)
                    send_message_to_mq_for_telegram(rabbit_connection, composer.get_message())

                ch.basic_ack(delivery_tag=method.delivery_tag)  # Acknowledge the message, it's not a system error
                return  # Stop processing this signal

            except PositionNotFoundException as e: # Catch specific exception
                logging.error(f"Position confirmation failed: {e}")
                 # Error happened during buy_turbo_instrument's call to find_position...
                composer.add_position_result(error=e, order_id=e.order_id) # Use order_id from exception
                send_message_to_mq_for_telegram(rabbit_connection, composer.get_message())
                # This is critical, state is inconsistent.
                raise ValueError(f"CRITICAL: Position not confirmed after order: {e}") # Raise ValueError to exit

            except ValueError as e:
                 # Catch other ValueErrors (e.g., from calculate_bid_amount)
                 logging.error(f"ValueError during trading action '{action}': {e}")
                 if founded_turbo and not buy_details:
                     # Error likely happened during buy_turbo_instrument before position check
                     composer.add_position_result(error=e)
                 elif not founded_turbo:
                     # Error happened before or during find_turbos
                     composer.add_turbo_search_result(error=e, search_context=search_context)
                 else: # Unclear context
                      composer.add_generic_error(f"ValueError in {action}", e)
                 send_message_to_mq_for_telegram(rabbit_connection, composer.get_message())
                 # Decide if this ValueError is critical enough to exit
                 # For now, acknowledge and continue unless it's the insufficient funds one etc.
                 ch.basic_ack(delivery_tag=method.delivery_tag)
                 return

            # --- General Exception Handling for Buy/Sell ---
            except Exception as e:
                # Catch-all for unexpected errors during buy/sell flow
                message = f"CRITICAL: Unexpected error during trading action '{action}': {e}"
                logging.critical(message)
                logging.critical(traceback.format_exc())
                # Add error context to the composer
                if founded_turbo and not buy_details:  # Error likely during buy/position check
                    composer.add_position_result(error=e)
                elif not founded_turbo:  # Error likely during search
                    composer.add_turbo_search_result(error=e, search_context=search_context)
                else:  # Add generic error if context is unclear
                    composer.add_generic_error("Trading Action", e)

                send_message_to_mq_for_telegram(rabbit_connection,
                                                composer.get_message() + f"\n\nCRITICAL FAILURE: {e}")
                # Raise to exit for critical unknown errors
                raise ValueError(message)


        # --- Close Actions ---
        elif action in ["close-long", "close-short", "close-position"]:
            try:
                close_action_param = None
                if action == "close-long":
                    close_action_param = "long"
                elif action == "close-short":
                    close_action_param = "short"

                all_positions = service.get_user_open_positions()
                service.check_and_act_close_on_current_positions(all_positions, close_action_param)

                # TODO : Maybe not send a summary message
                composer.add_text_section("--- CLOSE ACTION ---", f"Successfully processed '{action}' signal.")
                send_message_to_mq_for_telegram(rabbit_connection, composer.get_message())

                ch.basic_ack(delivery_tag=method.delivery_tag)
                sleep(1)

            except Exception as e:
                message = f"CRITICAL: Closing action '{action}' error: {e}"
                logging.critical(message)
                logging.critical(traceback.format_exc())
                composer.add_generic_error(f"Closing Action {action}", e)
                send_message_to_mq_for_telegram(rabbit_connection, composer.get_message() + f"\n\nCRITICAL FAILURE: {e}")
                raise ValueError(message) # Raise to exit for critical closing errors
        else:
             # Unknown action
             message = f"Unknown action received: {action}"
             logging.error(message)
             composer.add_generic_error("Unknown Action", ValueError(message))
             send_message_to_mq_for_telegram(rabbit_connection, composer.get_message())
             ch.basic_ack(delivery_tag=method.delivery_tag)
             sleep(1)


    # --- Global Exception Handling (outside specific action logic) ---
    except TradingRuleViolation as trv:
        # Already handled within the rule check block, message sent. Just log.
        print(f"Trading rule violation caught at outer level: {trv}")
    except (NoMarketAvailableException, NoTurbosAvailableException) as e:
         # Already handled within the buy/sell block, message sent. Just log.
        print(f"Market/Turbo availability issue caught at outer level: {e}")
    except InsufficientFundsException as e:
        # Already handled in buy block, message sent. Just log.
        print(f"Insufficient funds issue caught at outer level: {e}")
    except PositionNotFoundException as e:
        # Already handled in position check block, message sent. Log and exit.
        logging.error(f"Position not found exception at outer level for order ID {e.order_id}: {e}")
        logging.error(traceback.format_exc())
        print(f"Terminating due to position not found: {e}")
        exit(1)  # Critical error, exit
    except OrderPlacementError as e:
        # Saxo rejected an order explicitly
        logging.error(f"Order placement rejected by Saxo: {e}")
        logging.error(traceback.format_exc())
        
        if composer:
            composer.add_generic_error("Order Placement Rejected", e)
            send_message_to_mq_for_telegram(rabbit_connection, composer.get_message())
        else:
            error_details = ""
            if e.saxo_error_details:
                if isinstance(e.saxo_error_details, dict):
                    error_code = e.saxo_error_details.get('ErrorCode', '')
                    error_msg = e.saxo_error_details.get('Message', '')
                    error_details = f" (Code: {error_code}, Message: {error_msg})"
                else:
                    error_details = f" (Details: {e.saxo_error_details})"
            send_message_to_mq_for_telegram(rabbit_connection, f"ORDER REJECTED: {e}{error_details}")
        
        # Usually just acknowledge the message, don't exit
        ch.basic_ack(delivery_tag=method.delivery_tag)
        return
    except SaxoApiError as e:
        # General Saxo API errors
        logging.error(f"Saxo API error (status {e.status_code}): {e}")
        logging.error(traceback.format_exc())
        
        if composer:
            composer.add_generic_error("Saxo API Error", e)
            send_message_to_mq_for_telegram(rabbit_connection, composer.get_message())
        else:
            send_message_to_mq_for_telegram(rabbit_connection, f"SAXO API ERROR: {e}")
        
        print(f"Terminating due to Saxo API error: {e}")
        exit(1)  # Consider this critical
    except ConfigurationError as e:
        # Configuration errors
        logging.error(f"Configuration error: {e}")
        logging.error(traceback.format_exc())
        
        if composer:
            composer.add_generic_error("Configuration Error", e)
            send_message_to_mq_for_telegram(rabbit_connection, composer.get_message())
        else:
            missing_key = f" (missing key: {e.missing_key})" if hasattr(e, 'missing_key') and e.missing_key else ""
            send_message_to_mq_for_telegram(rabbit_connection, f"CONFIGURATION ERROR: {e}{missing_key}")
        
        print(f"Terminating due to configuration error: {e}")
        exit(1)  # Configuration errors are critical
    except ApiRequestException as e:
        # API request errors might be recoverable or critical
        logging.error(f"API request exception for endpoint {e.endpoint}: {e}")
        logging.error(traceback.format_exc())
        
        if composer:
            composer.add_generic_error("API Request Error", e)
            send_message_to_mq_for_telegram(rabbit_connection, composer.get_message())
        else:
            send_message_to_mq_for_telegram(rabbit_connection, f"CRITICAL API ERROR: {e}")
            
        print(f"Terminating due to API request error: {e}")
        exit(1)  # Consider this critical
    except TokenAuthenticationException as e:
        # Token issues are almost always critical
        logging.error(f"Token authentication error{' during refresh' if e.refresh_attempt else ''}: {e}")
        logging.error(traceback.format_exc())
        
        if composer:
            composer.add_generic_error("Authentication Error", e)
            send_message_to_mq_for_telegram(rabbit_connection, composer.get_message())
        else:
            send_message_to_mq_for_telegram(rabbit_connection, f"CRITICAL AUTH ERROR: {e}")
            
        print(f"Terminating due to authentication error: {e}")
        exit(1)  # Critical error, exit
    except DatabaseOperationException as e:
        # Database errors are critical
        logging.error(f"Database operation error on {e.operation} for entity {e.entity_id}: {e}")
        logging.error(traceback.format_exc())
        
        if composer:
            composer.add_generic_error("Database Error", e)
            send_message_to_mq_for_telegram(rabbit_connection, composer.get_message())
        else:
            send_message_to_mq_for_telegram(rabbit_connection, f"CRITICAL DATABASE ERROR: {e}")
            
        print(f"Terminating due to database error: {e}")
        exit(1)  # Critical error, exit
    except PositionCloseException as e:
        # Position close errors might be handled differently
        logging.error(f"Failed to close position {e.position_id}: {e}")
        logging.error(traceback.format_exc())
        
        if composer:
            composer.add_position_result(error=e)
            send_message_to_mq_for_telegram(rabbit_connection, composer.get_message())
        else:
            send_message_to_mq_for_telegram(rabbit_connection, f"POSITION CLOSE ERROR: {e}")
            
        # This might not always require termination, but let's exit for safety
        print(f"Terminating due to position close error: {e}")
        exit(1)
    except WebSocketConnectionException as e:
        # WebSocket errors might be recoverable
        logging.error(f"WebSocket connection error for context {e.context_id}: {e}")
        logging.error(traceback.format_exc())
        
        if composer:
            composer.add_generic_error("WebSocket Error", e)
            send_message_to_mq_for_telegram(rabbit_connection, composer.get_message())
        else:
            send_message_to_mq_for_telegram(rabbit_connection, f"WEBSOCKET ERROR: {e}")
            
        # Might want to take different action depending on context
        print(f"WebSocket error occurred: {e}")
        # Don't exit, might be recoverable
        ch.basic_ack(delivery_tag=method.delivery_tag)  # Acknowledge the message
    except ValueError as e:
        # Catches ValueErrors raised explicitly (like critical buy/close failures)
        # Message should have been composed and sent before raising
        logging.error(f"ValueError resulted in callback termination: {e}")
        logging.error(traceback.format_exc())
        # No need to send another message here, composer was used before raising
        print(f"Terminating due to ValueError: {e}")
        exit(1) # Exit as intended for critical ValueErrors
    except Exception as e:
        # Catch-all for unexpected errors *outside* the main trading try-blocks
        logging.error(f"Unhandled general error in callback: {e}")
        logging.error(traceback.format_exc())
        # Try to use composer if it was initialized, otherwise send raw error
        error_msg = f"CRITICAL: Unhandled general error in callback: {e}"
        if composer:
            composer.add_generic_error("General Callback Error", e)
            # Add traceback details if possible/desired
            composer.add_text_section("Traceback", traceback.format_exc())
            send_message_to_mq_for_telegram(rabbit_connection, composer.get_message())
        else:
            # Fallback if composer failed very early
            send_message_to_mq_for_telegram(rabbit_connection, error_msg)

        print(f"Terminating due to unhandled general error: {e}")
        exit(1) # Exit for unhandled errors

if __name__ == "__main__":
    # Initialize rabbit_connection as None to handle potential initialization errors
    rabbit_connection = None
    
    try:
        config_path = os.getenv("WATA_CONFIG_PATH")
        if not config_path:
            print("WATA_CONFIG_PATH environment variable not set")
            exit(1)

        # Get the application version
        app_version = get_version()

        # Create an instance of ConfigurationManager with validation
        try:
            config_manager = ConfigurationManager(config_path)
            print("Configuration validated successfully")
        except Exception as e:
            print(f"Configuration validation failed: {e}")
            print(traceback.format_exc())  # Print the full traceback for debugging
            exit(1)

        # Use the logging utility to set up logging for the trader application
        setup_logging(config_manager, "wata-trader")

        logging.info(f"Running WATA Trader version {app_version}")

        # Initialize the database connection, configure settings, and create schema
        db_order_manager = DbOrderManager(config_manager)
        db_position_manager = DbPositionManager(config_manager)

        try:
            table_trade_performance_manager = DbTradePerformanceManager(config_manager)
        except Exception as e:
            logging.critical(f"Bootstrap error: Failed to init `table_trade_performance_manager`: {e}")
            logging.critical(traceback.format_exc())  # Log the full traceback
            raise f"Bootstrap error: Failed to init `table_trade_performance_manager`: {e}"

        # Create an instance of TradingRule
        trading_rule = TradingRule(config_manager, db_position_manager)

        trade_turbo_exchange_id = config_manager.get_config_value(
            "trade.config.turbo_preference.exchange_id"
        )
        logging.info(f"Preferred exchange id for turbo is {trade_turbo_exchange_id}")
        last_action_persistant_file = config_manager.get_config_value(
            "trade.persistant.last_action_file"
        )

        # Retrieve RabbitMQ credentials from the configuration
        rabbitmq_config = config_manager.get_rabbitmq_config()
        rabbitmq_hostname = rabbitmq_config["hostname"]
        rabbitmq_username = rabbitmq_config["authentication"]["username"]
        rabbitmq_password = rabbitmq_config["authentication"]["password"]

        # Establish a connection to RabbitMQ with the provided credentials
        rabbit_connection = pika.BlockingConnection(
            pika.ConnectionParameters(
                host=rabbitmq_hostname,
                credentials=pika.PlainCredentials(rabbitmq_username, rabbitmq_password),
            )
        )
        channel = rabbit_connection.channel()
        channel.queue_declare(queue="trading-action")

        channel.basic_consume(
            queue="trading-action", on_message_callback=callback, auto_ack=False
        )

    except Exception as e:
        error_msg = f"General startup error: {e}"
        logging.error(error_msg)
        print(error_msg)
        if rabbit_connection:
            try:
                send_message_to_mq_for_telegram(rabbit_connection, f"Trader general startup error: {e}")
            except:
                pass  # If sending the message fails, we still want to exit
        exit(1)

    print(" [*] Waiting for messages in MQ. To exit press CTRL+C")
    send_message_to_mq_for_telegram(rabbit_connection, f"📈 Yeah! WATA Trader v{app_version} is ready to receive orders")
    channel.start_consuming()
