"""
HTTP client the async trader uses to poll the web_server's in-memory signal
store, replacing the old RabbitMQ ``trading-signals`` consumption.

This client keeps a persistent ``httpx.AsyncClient`` since it's called
continuously: by default it long-polls, i.e. the web_server holds each request
open until a new signal arrives (or ``long_poll_wait_ms`` passes), so a signal
reaches the trader as soon as it is stored.
"""

import logging
import time

import httpx

from src.configuration import ConfigurationManager
from src.web_server_token import WebServerToken

logger = logging.getLogger(__name__)


class SignalPollingClient:
    """Polls ``GET /latest-signals`` on the web_server service."""

    def __init__(self, config_manager: ConfigurationManager):
        config = config_manager.get_config_value("trade.config.signal_polling", {})
        self.service_url = config.get("service_url", "http://web_server1:80").rstrip("/")
        self.request_timeout_seconds = config.get("request_timeout_seconds", 2)
        self.interval_ms = config.get("interval_ms", 500)
        # > 0: long-poll for up to this long per request. 0: fetch everything
        # every ``interval_ms`` instead.
        self.long_poll_wait_ms = config.get("long_poll_wait_ms", 1000)
        # On trader startup, signals received longer ago than this are not replayed.
        self.startup_replay_max_age_seconds = config.get("startup_replay_max_age_seconds", 10)
        self._token = WebServerToken(config_manager).get_token()
        self._client: httpx.AsyncClient | None = None
        # ``seq`` of the newest signal seen so far (see SignalStore).
        self._cursor = 0

    async def start(self):
        """Open the persistent HTTP client. Call once before polling begins."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                # A long-poll request legitimately stays open for long_poll_wait_ms.
                timeout=self.request_timeout_seconds + self.long_poll_wait_ms / 1000,
                headers={"Authorization": f"Bearer {self._token}"},
            )

    async def get_latest_signals(self) -> list[dict]:
        """Fetch the latest signal per indice from the web_server."""
        if self._client is None:
            await self.start()
        response = await self._client.get(f"{self.service_url}/latest-signals")
        response.raise_for_status()
        return response.json()

    async def get_new_signals(self) -> list[dict]:
        """
        Fetch the signals stored since the previous call, waiting up to
        ``long_poll_wait_ms`` on the web_server for one to arrive.
        """
        if self._client is None:
            await self.start()
        started_at = time.monotonic()
        response = await self._client.get(
            f"{self.service_url}/latest-signals",
            params={"after": self._cursor, "wait_ms": self.long_poll_wait_ms},
        )
        response.raise_for_status()
        signals = response.json()

        # A web_server that predates long-polling ignores the query params: it
        # answers at once, with every stored signal and no ``seq``. Polling it
        # back-to-back would spin, so drop to fixed-interval polling.
        answered_empty_without_waiting = (
            not signals and time.monotonic() - started_at < self.long_poll_wait_ms / 2000
        )
        if answered_empty_without_waiting or any("seq" not in signal for signal in signals):
            logger.warning(
                "web_server does not support long-polling; falling back to polling every %sms.",
                self.interval_ms,
            )
            self.long_poll_wait_ms = 0
            return signals

        if signals:
            # Not max(cursor, ...): the web_server's seq restarts from 0 when it restarts.
            self._cursor = max(signal["seq"] for signal in signals)
        return signals

    async def close(self):
        if self._client is not None:
            await self._client.aclose()
            self._client = None
