"""
In-memory "latest signal per indice" store for the web_server.

Replaces the RabbitMQ ``trading-signals`` queue as the handoff mechanism between
the webhook (producer) and the async trader (consumer, via HTTP polling).
Only the most recent signal (by ``signal_timestamp``, not arrival order) is kept
for each ``indice``.
"""

import asyncio
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class SignalStore:
    """Async-safe store keeping only the latest signal per indice."""

    def __init__(self):
        self._lock = asyncio.Lock()
        self._signals: dict[str, dict] = {}

    @staticmethod
    def _parse_signal_timestamp(signal_timestamp: str) -> datetime:
        return datetime.strptime(signal_timestamp, "%Y-%m-%dT%H:%M:%SZ")

    async def upsert(self, signal: dict) -> None:
        """
        Store ``signal`` under ``signal["indice"]`` if it's newer (or equal — so a
        resubmission still refreshes signal_uuid/received_timestamp) than whatever
        is currently stored for that indice, comparing by signal_timestamp.
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
                    return
            self._signals[indice] = signal

    async def get_all(self) -> list[dict]:
        """Return a snapshot list of the latest signal for each indice."""
        async with self._lock:
            return list(self._signals.values())
