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
from collections import OrderedDict
from datetime import datetime

logger = logging.getLogger(__name__)

ENTRY_ACTIONS = ("long", "short")
CLOSE_ACTION = "close-position"
# The header an internal signal's key comes in (the TA engine's sender names it the same: held by a test).
SIGNAL_KEY_HEADER = "X-WATA-Signal-Key"
MAX_KEY_LENGTH = 200


class TakenKeys:
    """
    The keys of the internal signals taken lately, each with the signal_uuid
    it was stored under. A sender that got no answer cannot tell whether its
    signal was taken, and sends it again under the same key
    (src/ta_engine/sender.py): it is answered with the signal of the first
    time, and stored once. Without it, the same signal stored twice has two
    signal_uuids, and the trader takes it twice.

    In memory, as the store is: a web server that starts again has forgotten
    both, and takes the signal sent again as its first.
    """

    def __init__(self, most: int = 256):
        self._most = most
        self._taken: OrderedDict[str, str] = OrderedDict()

    @staticmethod
    def valid(key) -> bool:
        return isinstance(key, str) and 0 < len(key) <= MAX_KEY_LENGTH

    def claim(self, key: str, signal_uuid: str) -> str:
        """
        The signal_uuid ``key`` stands for: ``signal_uuid`` when it is new, which
        is kept for it from now on (before the signal is stored: a second request
        that comes meanwhile is told the same); the one kept when it was taken.
        """
        known = self._taken.get(key)
        if known is not None:
            self._taken.move_to_end(key)
            return known
        self._taken[key] = signal_uuid
        if len(self._taken) > self._most:
            self._taken.popitem(last=False)
        return signal_uuid

    def forget(self, key: str) -> None:
        """``key`` was not stored after all: sent again, it is taken as new."""
        self._taken.pop(key, None)


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

    async def rehearse(self, signal: dict) -> bool:
        """
        ``upsert``'s steps without what it does: ``signal`` is read and set
        against the one kept for its indice, and the waiting polls are woken.
        Nothing is kept and the counter stays where it is, so a woken poll
        finds nothing newer than it had and goes on waiting. For
        /internal/warm, which keeps a signal's way warm between signals. True
        when ``upsert`` would not have passed it over for an older moment.
        """
        indice = signal["indice"]
        new_ts = self._parse_signal_timestamp(signal["signal_timestamp"])
        async with self._lock:
            existing = self._signals.get(indice)
            newer = existing is None or new_ts >= self._parse_signal_timestamp(existing["signal_timestamp"])
            self._changed.notify_all()
            return newer

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
