import logging

import httpx

from src.configuration import ConfigurationManager

logger = logging.getLogger(__name__)

# How long an idle connection to the watchlist manager is kept, on both sides (its server keeps it as long: its
# ``uvicorn.run``): longer than the trader's rehearsals of a signal's way are apart (30 s).
KEEP_ALIVE_SECONDS = 75.0


class AsyncWatchlistClient:
    """Fetch best-turbo cache entries from the background watchlist manager."""

    def __init__(self, config_manager: ConfigurationManager):
        config = config_manager.get_config_value("trade.config.watchlist_manager", {})
        self.enabled = config.get("enabled", True)
        self.service_url = config.get("service_url", "http://watchlist_manager1:8081").rstrip("/")
        self.request_timeout_seconds = config.get("request_timeout_seconds", 1)
        self.accept_stale = config.get("accept_stale", False)
        # Kept open between lookups: this call sits on the path of every trade.
        self._client: httpx.AsyncClient | None = None

    async def get_best_turbo(
        self,
        *,
        direction: str,
        indice: str | None = None,
        underlying_uic: str | None = None,
    ) -> dict | None:
        if not self.enabled:
            return None
        if not indice and not underlying_uic:
            return None

        params = {"direction": direction}
        if indice:
            params["indice"] = indice
        if underlying_uic:
            params["underlying_uic"] = underlying_uic

        try:
            if self._client is None:
                # Its idle connection kept longer than the trader's rehearsals are apart (httpx's 5 s unless said):
                # a trade's lookup then goes on an open connection, where it opened one each time (14 to 16 ms).
                self._client = httpx.AsyncClient(
                    timeout=self.request_timeout_seconds,
                    limits=httpx.Limits(max_connections=10, max_keepalive_connections=2, keepalive_expiry=KEEP_ALIVE_SECONDS),
                )
            response = await self._client.get(f"{self.service_url}/watchlist/best", params=params)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            logger.info("Watchlist lookup failed for params=%s: %s", params, exc)
            return None

        if payload.get("watchlist", {}).get("stale") and not self.accept_stale:
            logger.info("Ignoring stale watchlist entry for params=%s", params)
            return None
        return payload

    async def close(self):
        if self._client is not None:
            await self._client.aclose()
            self._client = None
