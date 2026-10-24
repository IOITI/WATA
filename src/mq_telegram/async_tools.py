"""
Async Telegram message publishing via aio-pika.

Usage:
    sender = AsyncTelegramSender(config_manager)
    await sender.connect()
    await sender.send("Hello from WATA")
    await sender.close()
"""

import json
import logging

import aio_pika
import httpx


logger = logging.getLogger(__name__)

DIRECT_TIMEOUT_SECONDS = 10


class AsyncTelegramSender:
    """Publishes messages to the ``telegram_channel`` RabbitMQ queue via aio-pika."""

    def __init__(self, config_manager):
        self.config_manager = config_manager
        self._connection: aio_pika.abc.AbstractRobustConnection | None = None
        self._channel: aio_pika.abc.AbstractChannel | None = None

    async def connect(self):
        """Establish a persistent connection to RabbitMQ."""
        if self._connection and not self._connection.is_closed:
            return

        rabbitmq_config = self.config_manager.get_rabbitmq_config()
        host = rabbitmq_config["hostname"]
        user = rabbitmq_config["authentication"]["username"]
        password = rabbitmq_config["authentication"]["password"]

        url = f"amqp://{user}:{password}@{host}/"
        self._connection = await aio_pika.connect_robust(url)
        self._channel = await self._connection.channel()
        await self._channel.declare_queue("telegram_channel", durable=False)
        logger.info("AsyncTelegramSender connected to RabbitMQ.")

    async def send(self, message: str):
        """
        Publish a message to the telegram queue, or, when RabbitMQ does not take
        it, send it to Telegram directly: an alert must not depend on the queue
        whose failure it may be about. Raises only when neither went through.
        """
        try:
            if not self._channel or self._channel.is_closed:
                await self.connect()
            body = json.dumps({"message": message}).encode()
            await self._channel.default_exchange.publish(
                aio_pika.Message(body=body),
                routing_key="telegram_channel",
            )
            logger.debug("Telegram message published (%d chars)", len(message))
        except Exception as queue_error:
            logger.warning("RabbitMQ did not take a Telegram message (%s): sent to Telegram directly.",
                           type(queue_error).__name__)
            if not await self._send_directly(message):
                raise

    async def _send_directly(self, message: str) -> bool:
        """The Bot API, as the telegram service calls it. The token is in its address: never logged."""
        token = self.config_manager.get_config_value("telegram.bot_token")
        chat_id = self.config_manager.get_config_value("telegram.chat_id")
        if not token or not chat_id:
            return False
        try:
            async with httpx.AsyncClient(timeout=DIRECT_TIMEOUT_SECONDS) as client:
                response = await client.post(f"https://api.telegram.org/bot{token}/sendMessage",
                                             json={"chat_id": chat_id, "text": message})
            response.raise_for_status()
            return True
        except Exception as e:
            logger.error("Telegram did not take the message sent directly either: %s", type(e).__name__)
            return False

    async def close(self):
        if self._connection and not self._connection.is_closed:
            await self._connection.close()
            logger.info("AsyncTelegramSender connection closed.")
