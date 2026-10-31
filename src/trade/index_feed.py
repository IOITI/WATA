"""
The newest quote of the index the position monitor judges positions on between Saxo's quotes
(src/trade/index_estimate.py, ``index_between_quotes``).

The Capital.com feed publishes each quote it receives on RabbitMQ (exchange ``wata.prices``,
src/capital_feed/live.py); the monitor reads its index's through a queue of its own, without acknowledgements,
into this holder alone: a quote replaces the one before in memory, and nothing waits on it.

The exchange's name and the quotes' routing keys are written here, held to the feed's by
tests/test_index_feed.py: importing the feed's module would make its code, and the price recorder's bars it
imports, part of the position monitor's.
"""

import json
import time

EXCHANGE = "wata.prices"
PROVIDER = "capital"
# The monitor's queue keeps the newest quotes alone, the oldest dropped first.
QUEUE_LENGTH = 50


def quote_key(epic: str) -> str:
    """The routing key of the feed's quotes of ``epic``."""
    return f"{PROVIDER}.{epic}.quote"


class IndexQuotes:
    """The newest quote of one index: its middle price, and when the feed received it."""

    def __init__(self, epic: str, *, clock=time.time):
        self.epic = epic
        self._ref = f"{PROVIDER}/{epic}"
        self._clock = clock
        self.mid: float | None = None
        self.at: float | None = None
        self.received = 0

    def take(self, body: bytes) -> bool:
        """One of the feed's messages: kept when it is a newer quote of this index. Never raises."""
        try:
            message = json.loads(body)
        except (ValueError, TypeError):
            return False
        if not isinstance(message, dict) or message.get("type") != "quote" or message.get("ref") != self._ref:
            return False
        bid, ask = message.get("bid"), message.get("ask")
        if not all(isinstance(price, (int, float)) and not isinstance(price, bool) and price > 0 for price in (bid, ask)):
            return False
        now = self._clock()
        # When the feed received it, on the same server's clock (never later than now), else when it came here.
        received = message.get("rx")
        at = received / 1000 if isinstance(received, (int, float)) and not isinstance(received, bool) else now
        at = min(at, now)
        if self.at is not None and at < self.at:
            return False
        self.mid, self.at = (bid + ask) / 2, at
        self.received += 1
        return True

    def latest(self) -> tuple[float | None, float | None]:
        """The newest middle price and when it was received; (None, None) before the first."""
        return self.mid, self.at
