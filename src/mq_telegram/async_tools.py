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


logger = logging.getLogger(__name__)


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
        """Publish a message to the telegram queue."""
        if not self._channel or self._channel.is_closed:
            await self.connect()

        body = json.dumps({"message": message}).encode()
        await self._channel.default_exchange.publish(
            aio_pika.Message(body=body),
            routing_key="telegram_channel",
        )
        logger.debug("Telegram message published (%d chars)", len(message))

    async def close(self):
        if self._connection and not self._connection.is_closed:
            await self._connection.close()
            logger.info("AsyncTelegramSender connection closed.")
