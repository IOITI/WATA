from src.configuration import ConfigurationManager
from src.logging_helper import setup_logging

import pika
import logging
import json
import requests
import os


def send_telegram_message(token, chat_id, message):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message}
    headers = {"Content-Type": "application/json"}

    try:
        response = requests.post(url, data=json.dumps(payload), headers=headers)
        response.raise_for_status()  # Raises a HTTPError if the response status code is 4XX/5XX
        logging.info(f"Sent message to {chat_id} : {message}")
    except Exception as e:
        logging.error(f"Error sending message: {e}")
        raise e


def callback(ch, method, properties, body):
    try:
        print(" [x] Received %r" % body)
        # Convert the JSON string in 'body' to a Python dictionary
        data = json.loads(body)

        try:
            # Send the message
            send_telegram_message(
                chat_id=user_chat_id, token=bot_token, message=data["message"]
            )
        except Exception as e:
            raise e

        # Acknowledge the message
        ch.basic_ack(delivery_tag=method.delivery_tag)
    except Exception as e:
        logging.error(f"General error: {e}")
        raise e


if __name__ == "__main__":
    config_path = os.getenv("WATA_CONFIG_PATH")

    # Create an instance of ConfigurationManager
    config_manager = ConfigurationManager(config_path)

    # Retrieve logging configuration
    logging_config = config_manager.get_logging_config()

    # Use the logging utility to set up logging for the web server application
    setup_logging(config_manager, "wata-telegram")

    logging.info("WATA telegram sender is running")

    # Retrieve RabbitMQ credentials from the configuration
    rabbitmq_config = config_manager.get_rabbitmq_config()
    rabbitmq_hostname = rabbitmq_config["hostname"]
    rabbitmq_username = rabbitmq_config["authentication"]["username"]
    rabbitmq_password = rabbitmq_config["authentication"]["password"]

    bot_token = config_manager.get_config_value("telegram.bot_token")
    user_chat_id = config_manager.get_config_value("telegram.chat_id")

    # Establish a connection to RabbitMQ with the provided credentials
    rabbit_connection = pika.BlockingConnection(
        pika.ConnectionParameters(
            host=rabbitmq_hostname,
            credentials=pika.PlainCredentials(rabbitmq_username, rabbitmq_password),
        )
    )
    channel = rabbit_connection.channel()
    channel.queue_declare(queue="telegram_channel")

    channel.basic_consume(
        queue="telegram_channel", on_message_callback=callback, auto_ack=False
    )

    print(" [*] Waiting for messages in MQ. To exit press CTRL+C")
    channel.start_consuming()
