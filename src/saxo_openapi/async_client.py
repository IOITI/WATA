# src/saxo_openapi/async_client.py
"""
Async Saxo OpenAPI client using httpx.AsyncClient.
Drop-in replacement for the synchronous requests-based client,
designed for the async Trader and Position Monitor services.
"""

import json
import logging
import time
import asyncio
from threading import Lock

import httpx

from .exceptions import OpenAPIError

logger = logging.getLogger(__name__)

TRADING_ENVIRONMENTS = {
    "simulation": {
        "stream": "https://sim-streaming.saxobank.com",
        "api": "https://gateway.saxobank.com",
        "prefix": "sim",
    },
    "live": {
        "stream": "https://live-streaming.saxobank.com",
        "api": "https://gateway.saxobank.com",
    },
}

DEFAULT_HEADERS = {"Accept-Encoding": "gzip, deflate"}


def _mk_endpoint(endpoint, env: str, ep_type: str) -> str:
    if env == "live":
        path = str(endpoint)
    elif env == "simulation":
        path = f"{TRADING_ENVIRONMENTS[env]['prefix']}/{endpoint}"
    else:
        raise ValueError(f"Unknown environment: {env}")
    return f"{TRADING_ENVIRONMENTS[env][ep_type]}/{path}"


class AsyncRateLimiter:
    """Async-safe rate-limiter that reads Saxo response headers."""

    def __init__(self):
        self.session_remaining: int = 120
        self.session_reset: int = 0
        self._lock = asyncio.Lock()
        self.LOW_REQUESTS_THRESHOLD = 10

    def update_limits(self, headers: httpx.Headers):
        # No lock needed – called right after an await in the same task
        if "X-RateLimit-Session-Remaining" in headers:
            self.session_remaining = int(headers["X-RateLimit-Session-Remaining"])
        else:
            self.session_remaining = 120
        if "X-RateLimit-Session-Reset" in headers:
            self.session_reset = int(headers["X-RateLimit-Session-Reset"])
        else:
            self.session_reset = 0

    async def wait_if_needed(self):
        async with self._lock:
            if self.session_remaining <= 1:
                wait_time = max(self.session_reset, 1)
                logger.info("Rate limit near threshold. Waiting %d seconds", wait_time)
                await asyncio.sleep(wait_time)
            elif self.session_remaining <= self.LOW_REQUESTS_THRESHOLD:
                logger.info(
                    "Rate limit below %d (%d remaining). Adding 1s delay",
                    self.LOW_REQUESTS_THRESHOLD,
                    self.session_remaining,
                )
                await asyncio.sleep(1)


class AsyncAPI:
    """Async Saxo OpenAPI client powered by httpx.AsyncClient."""

    def __init__(
        self,
        access_token: str,
        environment: str = "live",
        headers: dict | None = None,
        timeout: float = 30.0,
    ):
        if environment not in TRADING_ENVIRONMENTS:
            raise ValueError(f"Unknown environment: {environment}")

        self.environment = environment
        self.access_token = access_token
        self.rate_limiter = AsyncRateLimiter()

        _headers = {**DEFAULT_HEADERS, "Authorization": f"Bearer {access_token}"}
        if headers:
            _headers.update(headers)

        client_kwargs = {
            "headers": _headers,
            "timeout": httpx.Timeout(timeout),
            "limits": httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
                keepalive_expiry=60,
            ),
        }

        try:
            self._client = httpx.AsyncClient(
                http2=True,
                **client_kwargs,
            )
        except ImportError:
            logger.warning(
                "HTTP/2 support is unavailable because the optional 'h2' dependency "
                "is not installed. Falling back to HTTP/1.1."
            )
            self._client = httpx.AsyncClient(
                http2=False,
                **client_kwargs,
            )

    def update_token(self, new_token: str):
        """Hot-swap the access token without recreating the client."""
        self.access_token = new_token
        self._client.headers["Authorization"] = f"Bearer {new_token}"

    async def close(self):
        await self._client.aclose()

    async def request(self, endpoint):
        """
        Execute an APIRequest endpoint object asynchronously.

        Mirrors the synchronous API.request() interface so that
        existing endpoint objects (rd.instruments.Instruments, etc.)
        work without modification.
        """
        method: str = endpoint.method.lower()
        params = getattr(endpoint, "params", {})
        ep_headers = getattr(endpoint, "HEADERS", {}) if hasattr(endpoint, "HEADERS") else {}

        url = _mk_endpoint(endpoint, self.environment, "api")

        kwargs: dict = {}
        if method in ("get", "delete", "patch"):
            kwargs["params"] = params
        if hasattr(endpoint, "data") and endpoint.data:
            kwargs["json"] = endpoint.data

        await self.rate_limiter.wait_if_needed()
        logger.debug("AsyncAPI: %s %s", method.upper(), url)

        try:
            response = await self._client.request(method, url, headers=ep_headers, **kwargs)
        except httpx.RequestError as err:
            logger.error("AsyncAPI: request to %s failed: %s", url, err)
            raise err

        self.rate_limiter.update_limits(response.headers)

        # Handle 429 rate-limit with one retry
        if response.status_code == 429:
            reset_time = int(response.headers.get("X-RateLimit-Session-Reset", 60))
            logger.warning("Rate limit exceeded (429). Waiting %d seconds…", reset_time)
            await asyncio.sleep(reset_time)
            response = await self._client.request(method, url, headers=ep_headers, **kwargs)
            self.rate_limiter.update_limits(response.headers)

        if response.status_code >= 400:
            content = response.text
            logger.error("AsyncAPI: %s %s → %d %s", method.upper(), url, response.status_code, content[:200])
            raise OpenAPIError(response.status_code, response.reason_phrase, content)

        # Parse response
        if hasattr(endpoint, "RESPONSE_DATA") and getattr(endpoint, "RESPONSE_DATA") is None:
            content = None
        elif hasattr(endpoint, "RESPONSE_DATA") and getattr(endpoint, "RESPONSE_DATA") == "text":
            content = response.text
        else:
            content = response.json() if response.content else None

        endpoint.response = content
        endpoint.status_code = response.status_code
        return content
