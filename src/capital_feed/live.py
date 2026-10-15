"""
What the live chart is sent: each quote as it arrives, with the ten-second bar
it is part of, and each bar once it is closed.

``LiveBar`` keeps the bar still forming by the rules of ``BarBuilder``
(src/price_recorder/bars.py), which this module reads and never changes: the
builder is part of every backtest's fingerprint. The bar the builder closes
is the one recorded; this one only lets the chart move before it closes, and
a test holds the two to the same result.

``LivePublisher`` hands the messages to RabbitMQ (exchange ``wata.prices``,
routing key ``capital.<epic>.quote|bar|state``) without ever making the stream
wait. They are for whoever is looking now: not kept on disk, not kept for a
consumer that is away, and dropped rather than sent late.
"""

import asyncio
import json
import logging
import time
from typing import Any, Awaitable, Callable, Hashable

from src.price_recorder.bars import Bar, Quote, bucket_start

logger = logging.getLogger(__name__)

PROVIDER = "capital"
EXCHANGE = "wata.prices"
# A message RabbitMQ could not hand over within this long is of no use any more.
MESSAGE_TTL_SECONDS = 10
QUEUE_SIZE = 2000
# A message that waited this long for RabbitMQ is dropped instead of sent late.
STALE_SECONDS = 3.0
RETRY_SECONDS = 5.0


def bar_row(bar: Bar) -> list:
    """A bar in the columns the admin's charts read (BAR_COLUMNS of src/admin/prices.py)."""
    return [
        bar.start, bar.bid_open, bar.bid_high, bar.bid_low, bar.bid_close,
        bar.ask_open, bar.ask_high, bar.ask_low, bar.ask_close, bar.updates,
    ]


def ref_of(epic: str) -> str:
    return f"{PROVIDER}/{epic}"


def routing_key(epic: str, kind: str) -> str:
    return f"{PROVIDER}.{epic}.{kind}"


class LiveBar:
    """The ten seconds in progress of one instrument, by BarBuilder's rules."""

    def __init__(self, key: Hashable):
        self._key = key
        self._standing: Quote | None = None
        self._bar: Bar | None = None

    def update(self, quote: Quote | None, at: float) -> Bar | None:
        """A quote received at ``at`` (None: no longer quoted). Returns the bar of its ten seconds."""
        bucket = bucket_start(at)
        if self._bar is not None and self._bar.start != bucket:
            self._bar = None
        previous = self._standing
        if quote is None:
            if previous is not None:
                if self._bar is None:
                    self._bar = Bar.opening_on(self._key, bucket, previous)
                self._standing = None
            return self._bar
        if previous == quote:
            return self.current(at)
        if self._bar is None:
            self._bar = Bar.opening_on(self._key, bucket, previous or quote)
            if previous is None:
                # The first quote seen: the bar opens on it, and it is one update.
                self._bar.updates = 1
                self._standing = quote
                return self._bar
        self._bar.take(quote)
        self._standing = quote
        return self._bar

    def current(self, at: float) -> Bar | None:
        """The bar of the ten seconds ``at`` falls in: what moved in them, or the quote that stood through them."""
        bucket = bucket_start(at)
        if self._bar is not None and self._bar.start == bucket:
            return self._bar
        if self._standing is not None:
            return Bar.opening_on(self._key, bucket, self._standing)
        return None


class LivePublisher:
    """
    ``quote``, ``bar`` and ``state`` only put a message in a bounded queue,
    which ``run`` empties into RabbitMQ. While RabbitMQ is away, what arrives
    is dropped and counted, and the exchange is opened again after a pause.
    """

    def __init__(
        self,
        open_exchange: Callable[[], Awaitable[Any]],
        *,
        clock: Callable[[], float] = time.time,
        queue_size: int = QUEUE_SIZE,
        retry_seconds: float = RETRY_SECONDS,
    ):
        self._open_exchange = open_exchange
        self._clock = clock
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=queue_size)
        self._retry_seconds = retry_seconds
        self._retry_at: float | None = None
        self.sent = 0
        self.dropped = 0
        self.failing = False
        self.last_error: str | None = None

    def quote(self, epic: str, *, bid: float, ask: float, quoted_ms: int | None, received_at: float, bar: Bar | None) -> None:
        self._put(routing_key(epic, "quote"), {
            "type": "quote", "ref": ref_of(epic),
            # Capital.com's time of the quote and the feed's time of receipt, in milliseconds.
            "t": quoted_ms, "rx": int(received_at * 1000),
            "bid": bid, "ask": ask,
            "bar": bar_row(bar) if bar is not None else None,
        })

    def bar(self, epic: str, bar: Bar) -> None:
        self._put(routing_key(epic, "bar"), {"type": "bar", "ref": ref_of(epic), "bar": bar_row(bar)})

    def state(self, epic: str, **state) -> None:
        self._put(routing_key(epic, "state"), {"type": "state", "ref": ref_of(epic), **state})

    def _put(self, key: str, message: dict) -> None:
        if self._retry_at is not None and self._clock() < self._retry_at:
            # RabbitMQ is away: nothing waits for it.
            self.dropped += 1
            return
        try:
            self._queue.put_nowait((self._clock(), key, message))
        except asyncio.QueueFull:
            self.dropped += 1

    def _fail(self, e: Exception) -> None:
        self.last_error = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
        if not self.failing:
            logger.warning("Live prices cannot be sent to RabbitMQ (%s): the chart is not live meanwhile.", self.last_error)
        self.failing = True
        self._retry_at = self._clock() + self._retry_seconds

    async def run(self) -> None:
        exchange = None
        while True:
            queued_at, key, message = await self._queue.get()
            if self._clock() - queued_at > STALE_SECONDS:
                self.dropped += 1
                continue
            if exchange is None:
                if self._retry_at is not None and self._clock() < self._retry_at:
                    self.dropped += 1
                    continue
                try:
                    exchange = await self._open_exchange()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self._fail(e)
                    self.dropped += 1
                    continue
            try:
                await exchange.publish(json.dumps(message, separators=(",", ":")).encode(), key)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._fail(e)
                self.dropped += 1
                exchange = None
                continue
            self.sent += 1
            if self.failing:
                logger.info("Live prices are sent to RabbitMQ again.")
                self.failing = False
                self._retry_at = None


class RabbitExchange:
    """The exchange of live prices on RabbitMQ, opened with aio-pika."""

    def __init__(self, url: str):
        self._url = url
        self._connection = None
        self._exchange = None

    async def open(self) -> "RabbitExchange":
        import aio_pika

        if self._connection is None or self._connection.is_closed:
            self._connection = await aio_pika.connect_robust(self._url)
        # No confirmation awaited per message: these are live prices, sent and forgotten.
        channel = await self._connection.channel(publisher_confirms=False)
        self._exchange = await channel.declare_exchange(EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=False)
        return self

    async def publish(self, body: bytes, key: str) -> None:
        import aio_pika

        await self._exchange.publish(
            aio_pika.Message(
                body=body,
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.NOT_PERSISTENT,
                expiration=MESSAGE_TTL_SECONDS,
            ),
            routing_key=key,
        )

    async def close(self) -> None:
        if self._connection is not None and not self._connection.is_closed:
            await self._connection.close()
