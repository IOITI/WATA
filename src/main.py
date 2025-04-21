import os
import traceback
from time import sleep
from configuration import ConfigurationManager
from trade.api_actions import SaxoService
from trade.rules import TradingRule
from trade.exceptions import TradingRuleViolation
from schema import SchemaLoader
from database import DbOrderManager, DbPositionManager, DbTradePerformanceManager
from mq_telegram.tools import send_message_to_mq_for_telegram
from message_helper import generate_daily_stats_message, generate_performance_stats_message
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
    try:
        print(" [x] Received %r" % body)

        logging.info(f"Received action {body}")
        # Convert the JSON string in 'body' to a Python dictionary
        data_from_mq = json.loads(body)
        try:
            # Validate the data against the schema
            jsonschema.validate(
                instance=data_from_mq, schema=SchemaLoader.get_trading_action_schema()
            )
        except jsonschema.exceptions.ValidationError as e:
            logging.error(f"Invalid data received: {e}")
            # Acknowledge the message
            ch.basic_ack(delivery_tag=method.delivery_tag)
            raise e

        if data_from_mq["action"] == "check_positions_on_saxo_api":
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
        elif data_from_mq["action"] == "daily_stats":
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
        else:
            # For all other actions that require SaxoService
            try:
                # Get the singleton instance of SaxoService
                service = initialize_saxo_service()
            except Exception as e:
                logging.critical(f"Failed to create SaxoService : {e}")
                send_message_to_mq_for_telegram(
                    rabbit_connection, f"CRITICAL: Failed to create SaxoService : {e}"
                )
                exit(1)

        try:
            if data_from_mq["action"] == "long" or data_from_mq["action"] == "short":
                decoded_body = body.decode('utf-8')
                with open(last_action_persistant_file, 'w') as file:
                    file.write(decoded_body)
        except IOError as e:
            logging.error(f"Error writing to file: {e}")

        try:
            if data_from_mq["action"] == "long" or data_from_mq["action"] == "short" or data_from_mq["action"] == "check_positions_on_saxo_api" :
                # Check if the signal_timestamp is too old
                trading_rule.check_signal_timestamp(data_from_mq.get("action"), data_from_mq.get("alert_timestamp"))
            elif data_from_mq["action"] == "long" or data_from_mq["action"] == "short":
                # Check if the current time is within the allowed market hours, or dates
                trading_rule.check_market_hours(data_from_mq.get("signal_timestamp"))
                # Check if the indice exists in the indices dictionary and get its ID
                indice_id = trading_rule.get_allowed_indice_id(data_from_mq.get("indice"))
                # Check if there is an open position with the same signal
                TradingRule.check_if_open_position_is_same_signal(data_from_mq.get("action"), db_position_manager)
                # Check if profit per day is done
                trading_rule.check_profit_per_day()

        except TradingRuleViolation as trv:
            # Handle trading rule violations (these are expected and should not exit)
            message = f"BREAKING RULE: {trv}"
            logging.error(message)
            ch.basic_ack(delivery_tag=method.delivery_tag)
            raise TradingRuleViolation(message)

        if data_from_mq["action"] == "long" or data_from_mq["action"] == "short":
            try:
                signal = data_from_mq["action"]

                all_positions = service.get_user_open_positions()

                service.check_and_act_close_on_current_positions(all_positions)

                founded_turbo = service.find_turbos(
                    trade_turbo_exchange_id, indice_id, signal
                )

                buy_details = service.buy_turbo_instrument(founded_turbo)

                message = f"""From the signal "{signal}"
--- FOUND ---
Founds this {founded_turbo["price"]["DisplayAndFormat"]["Description"]}
Symbol : {founded_turbo["price"]["DisplayAndFormat"]["Symbol"]}
Price info: {founded_turbo["price"]["Quote"]["Ask"]} {founded_turbo["price"]["DisplayAndFormat"]["Currency"]}
Price Ask timestamp {founded_turbo["price"]["Timestamps"]["AskTime"]}
Cost BUY/SELL : {founded_turbo["price"]["Commissions"]["CostBuy"]}/{founded_turbo["price"]["Commissions"]["CostSell"]}
--- POSITION ---
Instrument : {buy_details["position"]["instrument_name"]}
Open Price : {buy_details["position"]["position_open_price"]} {buy_details["position"]["instrument_currency"]}
Amount : {buy_details["position"]["position_amount"]}
Total price : {buy_details["position"]["position_total_open_price"]}
Time : {buy_details["position"]["execution_time_open"]}
Position ID : {buy_details["position"]["position_id"]}
"""
                try:
                    send_message_to_mq_for_telegram(rabbit_connection, message)
                except Exception as e:
                    raise e

                ch.basic_ack(delivery_tag=method.delivery_tag)
                sleep(1)
            except Exception as e:
                message = f"CRITICAL: Buying action error, will exit: {e}"
                logging.critical(message)
                send_message_to_mq_for_telegram(
                    rabbit_connection, message
                )
                print(message)
                raise ValueError(message)


        elif data_from_mq["action"] == "close-long" or data_from_mq["action"] == "close-short":
            try:
                if data_from_mq["action"] == "close-long":
                    action = "long"
                elif data_from_mq["action"] == "close-short":
                    action = "short"
                else:
                    raise ValueError(f"Unknown action : {data_from_mq["action"]}")
                all_positions = service.get_user_open_positions()
                service.check_and_act_close_on_current_positions(all_positions, action)
                ch.basic_ack(delivery_tag=method.delivery_tag)
                sleep(1)
            except Exception as e:
                message = f"CRITICAL: Closing action error, will exit: {e}"
                logging.critical(message)
                send_message_to_mq_for_telegram(
                    rabbit_connection, message
                )
                print(message)
                raise ValueError(message)

        elif data_from_mq["action"] == "close-position":
            try:
                all_positions = service.get_user_open_positions()
                service.check_and_act_close_on_current_positions(all_positions)
                ch.basic_ack(delivery_tag=method.delivery_tag)
                sleep(1)
            except Exception as e:
                message = f"CRITICAL: Closing all action error, will exit: {e}"
                logging.critical(message)
                send_message_to_mq_for_telegram(
                    rabbit_connection, message
                )
                print(message)
                raise ValueError(message)
        else:
            ch.basic_ack(delivery_tag=method.delivery_tag)
            sleep(1)
            raise ValueError(f"Unknown action : {data_from_mq["action"]}")

    except TradingRuleViolation as trv:
        print(f"Trading rule violation: {trv}")
    except Exception as e:
        logging.error(f"General error: {e}")
        logging.error(traceback.format_exc())  # Log the full traceback
        send_message_to_mq_for_telegram(
            rabbit_connection, f"CRITICAL: General error: {e}"
        )
        print(f"General error: {e}")
        exit(1)


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
