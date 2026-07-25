"""
HTTP client the async trader uses to poll the web_server's in-memory signal
store, replacing the old RabbitMQ ``trading-signals`` consumption.

Unlike :class:`~src.trade.watchlist_client.AsyncWatchlistClient` (which opens a
new connection per lookup), this client keeps a persistent ``httpx.AsyncClient``
since it's called on a tight interval (every ``interval_ms``, default 500ms).
"""

import logging

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
        self._token = WebServerToken(config_manager).get_token()
        self._client: httpx.AsyncClient | None = None

    async def start(self):
        """Open the persistent HTTP client. Call once before polling begins."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.request_timeout_seconds,
                headers={"Authorization": f"Bearer {self._token}"},
            )

    async def get_latest_signals(self) -> list[dict]:
        """Fetch the latest signal per indice from the web_server."""
        if self._client is None:
            await self.start()
        response = await self._client.get(f"{self.service_url}/latest-signals")
        response.raise_for_status()
        return response.json()

    async def close(self):
        if self._client is not None:
            await self._client.aclose()
            self._client = None
