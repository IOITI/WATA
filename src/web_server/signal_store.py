"""
In-memory "latest signal per indice" store for the web_server.

Replaces the RabbitMQ ``trading-signals`` queue as the handoff mechanism between
the webhook (producer) and the async trader (consumer, via HTTP polling).
Only the most recent signal (by ``signal_timestamp``, not arrival order) is kept
for each ``indice``. One moment, one trade: a close-position does not replace
an entry of the same signal_timestamp, as the TA engine sends only the entry of
a bar that raised both (src/ta_engine/signals.py, ``settled``).
"""

import asyncio
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

ENTRY_ACTIONS = ("long", "short")
CLOSE_ACTION = "close-position"


class SignalStore:
    """Async-safe store keeping only the latest signal per indice."""

    def __init__(self):
        self._lock = asyncio.Lock()
        self._signals: dict[str, dict] = {}
        # Stamped on every stored signal (as ``seq``) so a poller can ask only for
        # what arrived after the last one it saw, and wait for it (long-poll).
        self._seq = 0
        self._changed = asyncio.Condition(self._lock)

    @staticmethod
    def _parse_signal_timestamp(signal_timestamp: str) -> datetime:
        return datetime.strptime(signal_timestamp, "%Y-%m-%dT%H:%M:%SZ")

    async def upsert(self, signal: dict) -> bool:
        """
        Store ``signal`` under ``signal["indice"]`` if it's newer (or equal — so a
        resubmission still refreshes signal_uuid/received_timestamp) than whatever
        is currently stored for that indice, comparing by signal_timestamp. True
        when it was stored.

        A close-position of the same signal_timestamp as the entry kept does not
        replace it: the entry wins, since it sells the other side before it buys,
        and a close after it would sell the position just opened. The other way
        round, the entry replaces the close.
        """
        indice = signal["indice"]
        new_ts = self._parse_signal_timestamp(signal["signal_timestamp"])
        async with self._lock:
            existing = self._signals.get(indice)
            if existing is not None:
                existing_ts = self._parse_signal_timestamp(existing["signal_timestamp"])
                if new_ts < existing_ts:
                    logger.info(
                        "Ignoring older signal for indice=%s (new=%s < existing=%s)",
                        indice, signal["signal_timestamp"], existing["signal_timestamp"],
                    )
                    return False
                if new_ts == existing_ts and signal.get("action") == CLOSE_ACTION and existing.get("action") in ENTRY_ACTIONS:
                    logger.info(
                        "Ignoring close-position for indice=%s at %s: the %s of the same moment is kept",
                        indice, signal["signal_timestamp"], existing["action"],
                    )
                    return False
            self._seq += 1
            signal["seq"] = self._seq
            self._signals[indice] = signal
            self._changed.notify_all()
            return True

    async def get_all(self) -> list[dict]:
        """Return a snapshot list of the latest signal for each indice."""
        async with self._lock:
            return list(self._signals.values())

    async def get_newer(self, after_seq: int, timeout: float = 0) -> list[dict]:
        """
        Return the signals stored after ``after_seq``. When there is none yet, wait
        up to ``timeout`` seconds for one to arrive (long-poll) before returning [].

        An ``after_seq`` ahead of this store's counter means the caller's cursor
        predates a web_server restart, so everything stored here is new to it.
        """
        async with self._changed:
            if after_seq > self._seq:
                after_seq = 0
            if timeout > 0 and self._seq <= after_seq:
                try:
                    await asyncio.wait_for(
                        self._changed.wait_for(lambda: self._seq > after_seq),
                        timeout,
                    )
                except asyncio.TimeoutError:
                    pass
            return [signal for signal in self._signals.values() if signal["seq"] > after_seq]
