from src.configuration import ConfigurationManager
from src.logging_helper import setup_logging

import pika
import logging
import json
import requests
import os
import time

# Telegram answers within seconds: without a limit, a request that hangs would hold every message behind it.
SEND_TIMEOUT_SECONDS = 10
# A message Telegram could not take for now (no answer, 429, 5xx) goes back to the queue after this.
RETRY_AFTER_SECONDS = 5


class MessageRefused(Exception):
    """Telegram refused the message itself (400 to 499 but 429): sent again, it would be refused again."""


class TelegramUnavailable(Exception):
    """Telegram could not take the message for now: no answer, too many requests, or its own error."""


def _said(response) -> str:
    """What Telegram said of a refusal, never the address (it holds the bot's token)."""
    try:
        return str(response.json().get("description") or "")[:200]
    except Exception:
        return ""


def send_telegram_message(token, chat_id, message, post=requests.post):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message}
    headers = {"Content-Type": "application/json"}

    try:
        response = post(url, data=json.dumps(payload), headers=headers, timeout=SEND_TIMEOUT_SECONDS)
    except requests.RequestException as e:
        # Its text names the address, and the address the bot's token: only its kind is said.
        logging.error("Telegram could not be reached: %s", type(e).__name__)
        raise TelegramUnavailable(type(e).__name__) from None
    if response.status_code >= 400:
        description = _said(response)
        logging.error("Telegram refused a message (%s): %s", response.status_code, description)
        # The bot or its chat is wrong (a token replaced or revoked, a chat left): every message would be
        # refused alike. Kept in the queue for when they are set right, never dropped.
        wrong_bot = response.status_code in (401, 403, 404) or "chat not found" in description.lower()
        if response.status_code < 500 and response.status_code != 429 and not wrong_bot:
            raise MessageRefused(f"{response.status_code} {description}")
        raise TelegramUnavailable(f"{response.status_code} {description}")
    logging.info("Sent to Telegram: %s", message)


def callback(ch, method, properties, body, send=None, wait=time.sleep, note=None):
    """
    Send one message of the queue, acknowledged once Telegram took it. One
    it refuses for what it is, or that is not a message at all, is dropped
    with a log line: given back, it would be refused again for ever. One it
    could not take for now goes back to the queue after a pause. ``note(ok,
    detail)`` hears how Telegram took it (the service's ``CheckNotes``).
    """
    send = send or (lambda message: send_telegram_message(token=bot_token, chat_id=user_chat_id, message=message))
    note = note or (notes.note if "notes" in globals() else (lambda ok, detail=None: None))
    try:
        message = json.loads(body)["message"]
    except (ValueError, KeyError, TypeError) as e:
        logging.error("A queued item that is no message was dropped: %s", type(e).__name__)
        ch.basic_ack(delivery_tag=method.delivery_tag)
        return
    try:
        send(message)
    except MessageRefused as e:
        logging.error("Dropped a message Telegram refused: %s", message)
        note(False, f"Refused a message: {e}")
        ch.basic_ack(delivery_tag=method.delivery_tag)
        return
    except TelegramUnavailable as e:
        note(False, f"Could not take a message for now: {e}")
        wait(RETRY_AFTER_SECONDS)
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
        return
    note(True, "Messages taken")
    ch.basic_ack(delivery_tag=method.delivery_tag)


class CheckNotes:
    """
    How Telegram took the bot's messages, written for the admin's Credentials
    (``credential_check``) when it changes and every ten minutes at most, with
    a connection of its own each time. Never raises: a note costs no message.
    """

    def __init__(self, config_manager, every_seconds: float = 600.0, clock=time.monotonic, write=None):
        self._dsn = config_manager.get_config_value("postgresql.dsn")
        self._every = every_seconds
        self._clock = clock
        self._last = None
        self._write = write or self._written

    def _written(self, ok: bool, detail: str | None) -> None:
        import asyncio

        async def write():
            import asyncpg

            from src.vault.store import record_check

            connection = await asyncpg.connect(self._dsn, timeout=5)
            try:
                await record_check(connection, "telegram.bot", "telegram", ok, detail)
            finally:
                await connection.close()

        asyncio.run(write())

    def note(self, ok: bool, detail: str | None = None) -> None:
        now = self._clock()
        if self._last is not None and self._last[:2] == (ok, detail) and now - self._last[2] < self._every:
            return
        self._last = (ok, detail, now)
        try:
            self._write(ok, detail)
        except Exception as e:
            logging.debug("Could not write how Telegram took the message: %r", e)


if __name__ == "__main__":
    config_path = os.getenv("WATA_CONFIG_PATH")

    # Create an instance of ConfigurationManager
    config_manager = ConfigurationManager(config_path)

    # Retrieve logging configuration
    logging_config = config_manager.get_logging_config()

    # Use the logging utility to set up logging for the web server application
    setup_logging(config_manager, "wata-telegram")

    # The bot's token and chat id, sealed for this service in PostgreSQL. Waited for while the database
    # cannot be read: the messages wait in the queue meanwhile.
    from src.vault import loader as vault

    vault.load_blocking(config_manager, "telegram")

    logging.info("WATA telegram sender is running")

    # Retrieve RabbitMQ credentials from the configuration
    rabbitmq_config = config_manager.get_rabbitmq_config()
    rabbitmq_hostname = rabbitmq_config["hostname"]
    rabbitmq_username = rabbitmq_config["authentication"]["username"]
    rabbitmq_password = rabbitmq_config["authentication"]["password"]

    bot_token = config_manager.get_config_value("telegram.bot_token")
    user_chat_id = config_manager.get_config_value("telegram.chat_id")
    while not bot_token or not user_chat_id:
        # Nothing taken from the queue: the messages wait there until the bot is set (admin's Credentials)
        # and this service restarted.
        logging.error("No Telegram bot: set it from the admin's Credentials. The messages wait in the queue.")
        time.sleep(600)
    notes = CheckNotes(config_manager)

    if config_manager.get_config_value("reporting.telegram_commands", False) is True:
        # The bot's commands, which only read (src/mq_telegram/commands.py), in a thread beside the queue's consumer.
        from src.mq_telegram.updates import start_commands

        start_commands(config_manager, bot_token, user_chat_id)

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
