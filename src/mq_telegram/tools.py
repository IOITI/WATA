import json
import logging
import pika
from pika.exceptions import AMQPConnectionError


def send_message_to_mq_for_telegram(rabbit_connection, message_telegram):
    try:
        telegram_channel = rabbit_connection.channel()
        telegram_channel.queue_declare(queue="telegram_channel")

        message = json.dumps(
            {
                "message": message_telegram,
            }
        )
        telegram_channel.basic_publish(
            exchange="", routing_key="telegram_channel", body=message
        )
        logging.info(f"Send message to channel telegram_channel, message {message}")
    except pika.exceptions.AMQPConnectionError as e:
        logging.error(f"Failed to connect to RabbitMQ: {e}")
    except Exception as e:
        logging.error(f"An unexpected error occurred while sending the message: {e}")
    finally:
        # Ensure the connection is closed even if an error occurs
        if "connection" in locals():
            rabbit_connection.close()
