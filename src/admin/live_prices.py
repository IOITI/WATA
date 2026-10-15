"""
Live prices for the charts of the Data pages: what the Capital.com feed sends
to RabbitMQ (exchange ``wata.prices``, src/capital_feed/live.py), handed to
each browser watching an asset as soon as it arrives.

The admin listens only while a chart does: the first subscriber opens a queue
of its own on RabbitMQ (exclusive, gone with the connection, its oldest
messages dropped beyond a thousand), and it is closed a minute after the last
one left. Nothing is acknowledged or kept: these are the prices of now.

Each subscriber has a bounded queue. A quote that does not fit is dropped:
the next one carries the whole bar of its ten seconds anyway. A bar or a state
that does not fit empties the queue for ``resync``, which tells the browser to
read the bars again rather than show a chart with a hole in it.
"""

import asyncio
import json
import logging
import time
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

EXCHANGE = "wata.prices"
BINDING = "capital.#"
SUBSCRIBER_QUEUE_SIZE = 256
BROKER_QUEUE_LENGTH = 1000
# Without a listener for this long, the queue on RabbitMQ is closed.
IDLE_CLOSE_SECONDS = 60.0
RETRY_SECONDS = 5.0
_TICK_SECONDS = 1.0

QUOTE, BAR, STATE, RESYNC, LIVE = "quote", "bar", "state", "resync", "live"

# Opens the stream of messages: given the function that takes each one, returns what closes it.
Source = Callable[[Callable[[dict], None]], Awaitable[Any]]


class LivePrices:
    def __init__(self, source: Source | None, *, clock: Callable[[], float] = time.monotonic):
        self._source = source
        self._clock = clock
        self._subscribers: dict[str, set[asyncio.Queue]] = {}
        self._latest_quote: dict[str, dict] = {}
        self._latest_state: dict[str, dict] = {}
        self._someone_listening = asyncio.Event()
        self._idle_since: float | None = None
        self.connected = False
        self.last_error: str | None = None
        self.received = 0

    @property
    def available(self) -> bool:
        return self._source is not None

    # ── browsers ──

    def subscribe(self, ref: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE)
        self._subscribers.setdefault(ref, set()).add(queue)
        self._someone_listening.set()
        self._idle_since = None
        return queue

    def unsubscribe(self, ref: str, queue: asyncio.Queue) -> None:
        listeners = self._subscribers.get(ref)
        if listeners is not None:
            listeners.discard(queue)
            if not listeners:
                del self._subscribers[ref]
        if not self._subscribers:
            self._someone_listening.clear()
            self._idle_since = self._clock()

    @property
    def listeners(self) -> int:
        return sum(len(queues) for queues in self._subscribers.values())

    def snapshot(self, ref: str) -> dict:
        """What a chart that opens needs at once: the last quote and state known of the asset, and whether it is live."""
        return {
            "live": self.connected,
            "detail": None if self.connected else (self.last_error or "Connecting to the live prices"),
            "quote": self._latest_quote.get(ref),
            "state": self._latest_state.get(ref),
        }

    # ── messages ──

    def dispatch(self, message: dict) -> None:
        """One message of the feed, handed to whoever watches its asset."""
        kind, ref = message.get("type"), message.get("ref")
        if kind not in (QUOTE, BAR, STATE) or not isinstance(ref, str):
            return
        self.received += 1
        if kind == QUOTE:
            self._latest_quote[ref] = message
        elif kind == STATE:
            self._latest_state[ref] = message
        for queue in self._subscribers.get(ref, ()):
            self._put(queue, message)

    @staticmethod
    def _put(queue: asyncio.Queue, message: dict) -> None:
        try:
            queue.put_nowait(message)
        except asyncio.QueueFull:
            if message.get("type") == QUOTE:
                return
            # A bar would be missing from the chart: everything is read again instead.
            while not queue.empty():
                queue.get_nowait()
            queue.put_nowait({"type": RESYNC, "ref": message.get("ref")})

    def _tell_everyone(self, live: bool, detail: str | None) -> None:
        for ref, queues in self._subscribers.items():
            for queue in queues:
                self._put(queue, {"type": LIVE, "ref": ref, "live": live, "detail": detail})

    # ── the connection to RabbitMQ ──

    async def run(self) -> None:
        """Listen while someone watches. Never raises: a failure is said to the charts and tried again."""
        if self._source is None:
            return
        while True:
            await self._someone_listening.wait()
            try:
                closer = await self._source(self.dispatch)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.last_error = f"Live prices unavailable ({type(e).__name__})"
                logger.warning("Live prices: RabbitMQ could not be reached: %s", e)
                self._tell_everyone(False, self.last_error)
                await asyncio.sleep(RETRY_SECONDS)
                continue
            self.connected, self.last_error = True, None
            self._tell_everyone(True, None)
            try:
                while not (self._idle_since is not None and self._clock() - self._idle_since >= IDLE_CLOSE_SECONDS):
                    await asyncio.sleep(_TICK_SECONDS)
            finally:
                self.connected = False
                try:
                    await closer.close()
                except Exception as e:
                    logger.debug("Live prices: closing the connection: %s", e)
                self._latest_quote.clear()
                self._latest_state.clear()


def rabbitmq_source(url: str) -> Source:
    """The feed's exchange on RabbitMQ, read through a queue of the admin's own, without acknowledgements."""

    async def open_source(on_message: Callable[[dict], None]):
        import aio_pika

        connection = await aio_pika.connect_robust(url)
        channel = await connection.channel()
        exchange = await channel.declare_exchange(EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=False)
        queue = await channel.declare_queue(
            exclusive=True, auto_delete=True,
            arguments={"x-max-length": BROKER_QUEUE_LENGTH, "x-overflow": "drop-head"},
        )
        await queue.bind(exchange, routing_key=BINDING)

        async def handle(message) -> None:
            try:
                decoded = json.loads(message.body)
            except ValueError:
                return
            if isinstance(decoded, dict):
                on_message(decoded)

        await queue.consume(handle, no_ack=True)
        logger.info("Live prices: listening to RabbitMQ.")
        return connection

    return open_source
