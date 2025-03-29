from schedule import every, repeat, run_pending
from src.configuration import ConfigurationManager
import pika
from pika.exceptions import AMQPConnectionError
import time
import logging
import json
from datetime import datetime, timedelta
import pytz
import os
from src.logging_helper import setup_logging


# Function to send 'ping_saxo_api' every 1 minutes
@repeat(every(1).minutes)
def job_check_positions_on_saxo_api():
    # Get the current time in UTC
    now_utc = datetime.now(pytz.utc)
    message = {
        "action": "check_positions_on_saxo_api",
        "indice": "n/a",
        "signal_timestamp": "2024-05-09T12:26:00Z",
        "alert_timestamp": "2024-05-09T12:26:00Z",
        "mqsend_timestamp": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    send_message_to_trading(message)


@repeat(every().day.at(time_str="22:00", tz="Europe/Paris"))
def job_daily_stats():
    # Get the current time in UTC
    now_utc = datetime.now(pytz.utc)
    message = {
        "action": "daily_stats",
        "indice": "n/a",
        "signal_timestamp": "2024-05-09T12:26:00Z",
        "alert_timestamp": "2024-05-09T12:26:00Z",
        "mqsend_timestamp": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    send_message_to_trading(message)


# @repeat(every().day.at(time_str="08:10", tz="Europe/Paris"))
# def job_try_repeat_last_action_at_the_open():
#     # Get the current time in UTC
#     now_utc = datetime.now(pytz.utc)
#
#     try:
#         # Open the file in read mode ('r')
#         with open(last_action_persistant_file, 'r') as file:
#             # Read the entire content of the file
#             body = file.read()
#     except FileNotFoundError:
#         logging.error("The last action file does not exist.")
#         raise Exception("The last action file does not exist")
#     except Exception as e:
#         logging.error(f"An error occurred while reading the file: {e}")
#         raise e
#
#     # Convert the JSON string in 'body' to a Python dictionary
#     data = json.loads(body)
#
#     alert_timestamp = data.get("alert_timestamp")
#
#     # Parse the signal_timestamp string into a datetime object
#     alert_time = datetime.strptime(alert_timestamp, "%Y-%m-%dT%H:%M:%SZ")
#     alert_time = alert_time.replace(tzinfo=pytz.UTC)  # Ensure it's in UTC
#
#     # Calculate the difference between the current time and the signal_timestamp
#     time_difference = now_utc - alert_time
#
#     # Check if the difference is more than 9 minutes
#     if time_difference > timedelta(minutes=9):
#         logging.info(f"Replay the signal because Current time: {now_utc}, alert time: {alert_time}")
#
#         message = {
#             "action": data["action"],
#             "indice": data["indice"],
#             "signal_timestamp": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
#             "alert_timestamp": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
#             "mqsend_timestamp": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
#         }
#         send_message_to_trading(message)
#     else:
#         logging.warning(f"Do not replay the signal because Current time: {now_utc}, alert time: {alert_time}")


# Function to send 'close-position' every day at 21:55
@repeat(every().day.at(time_str="21:55", tz="Europe/Paris"))
def job_close_position():
    # Get the current time in UTC
    now_utc = datetime.now(pytz.utc)
    message = {
        "action": "close-position",
        "indice": "n/a",
        "signal_timestamp": "2024-05-09T12:26:00Z",
        "alert_timestamp": "2024-05-09T12:26:00Z",
        "mqsend_timestamp": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    send_message_to_trading(message)


def send_message_to_trading(message):
    try:
        # Retrieve RabbitMQ credentials from the configuration
        rabbitmq_config = config_manager.get_rabbitmq_config()
        rabbitmq_hostname = rabbitmq_config["hostname"]
        rabbitmq_username = rabbitmq_config["authentication"]["username"]
        rabbitmq_password = rabbitmq_config["authentication"]["password"]

        # Establish a connection to RabbitMQ with the provided credentials
        connection = pika.BlockingConnection(
            pika.ConnectionParameters(
                host=rabbitmq_hostname,
                credentials=pika.PlainCredentials(rabbitmq_username, rabbitmq_password),
            )
        )
        channel = connection.channel()
        channel.queue_declare(queue="trading-action")

        body = json.dumps(message)
        channel.basic_publish(exchange="", routing_key="trading-action", body=body)
        logging.info(f"Send message to channel trading-action, message {body}")
    except pika.exceptions.AMQPConnectionError as e:
        logging.error(f"Failed to connect to RabbitMQ: {e}")
    except Exception as e:
        logging.error(f"An unexpected error occurred while sending the message: {e}")
    finally:
        # Ensure the connection is closed even if an error occurs
        if "connection" in locals():
            connection.close()


config_path = os.getenv("WATA_CONFIG_PATH")

config_manager = ConfigurationManager(config_path)

# Retrieve logging configuration
logging_config = config_manager.get_logging_config()

# Use the logging utility to set up logging for the scheduler application
setup_logging(config_manager, "wata-scheduler")

last_action_persistant_file = config_manager.get_config_value(
    "trade.persistant.last_action_file"
)

logging.info("WATA scheduler is running")

print("-------------------------------- Started scheduler application")
# Keep the script running
while True:
    run_pending()
    time.sleep(1)
