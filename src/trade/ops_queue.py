"""
The trader's hand-over to the position monitor of a sale that did not go
through: a ``close_position`` message on ``trading-ops``, the one the admin
sends for the operator, with the reason of the sale. The monitor keeps the
position to be sold until Saxo no longer lists it; the trader, which handles
one signal after the other, cannot wait for that.

The monitor ignores a request older than a minute (a request that waited in
the queue while it was down must not sell anything once it is back): the
message carries the time it was asked at.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone

import aio_pika

logger = logging.getLogger(__name__)

OPS_QUEUE = "trading-ops"
# The trader handles one signal after the other: a hand-over waits no longer than this for RabbitMQ.
HAND_OVER_TIMEOUT_SECONDS = 5.0


def close_request(position_id, reason: str, now: datetime | None = None) -> dict:
    """What the position monitor reads (``handle_close_position_request``)."""
    moment = now or datetime.now(timezone.utc)
    return {
        "action": "close_position",
        "position_id": str(position_id),
        "requested_at": moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reason": reason,
    }


class AsyncOpsPublisher:
    """Publishes to the ``trading-ops`` queue, as the scheduler and the admin do."""

    def __init__(self, config_manager):
        self.config_manager = config_manager
        self._connection = None
        self._channel = None

    async def _connect(self):
        if self._channel is not None and not self._channel.is_closed:
            return
        rabbitmq_config = self.config_manager.get_rabbitmq_config()
        user = rabbitmq_config["authentication"]["username"]
        password = rabbitmq_config["authentication"]["password"]
        self._connection = await aio_pika.connect_robust(f"amqp://{user}:{password}@{rabbitmq_config['hostname']}/")
        self._channel = await self._connection.channel()
        await self._channel.declare_queue(OPS_QUEUE, durable=True)

    async def publish(self, message: dict) -> None:
        await self._connect()
        await self._channel.default_exchange.publish(
            aio_pika.Message(body=json.dumps(message).encode()), routing_key=OPS_QUEUE,
        )
        logger.info("Sent to %s: %s", OPS_QUEUE, message)

    async def hand_over(self, position_id, reason: str) -> bool:
        """Ask the position monitor to sell ``position_id``. Never raises: whether it was sent."""
        try:
            await asyncio.wait_for(self.publish(close_request(position_id, reason)), HAND_OVER_TIMEOUT_SECONDS)
            return True
        except Exception as e:
            logger.error("Could not hand the sale of position %s to the position monitor: %r", position_id, e)
            return False

    async def close(self):
        if self._connection is not None and not self._connection.is_closed:
            await self._connection.close()
