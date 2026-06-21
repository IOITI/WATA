import logging

import httpx

from src.configuration import ConfigurationManager

logger = logging.getLogger(__name__)


class AsyncWatchlistClient:
    """Fetch best-turbo cache entries from the background watchlist manager."""

    def __init__(self, config_manager: ConfigurationManager):
        config = config_manager.get_config_value("trade.config.watchlist_manager", {})
        self.enabled = config.get("enabled", True)
        self.service_url = config.get("service_url", "http://watchlist_manager1:8081").rstrip("/")
        self.request_timeout_seconds = config.get("request_timeout_seconds", 1)
        self.accept_stale = config.get("accept_stale", False)

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
            async with httpx.AsyncClient(timeout=self.request_timeout_seconds) as client:
                response = await client.get(f"{self.service_url}/watchlist/best", params=params)
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