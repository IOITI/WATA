# src/saxo_openapi/async_client.py
"""
Async Saxo OpenAPI client using httpx.AsyncClient.
Drop-in replacement for the synchronous requests-based client,
designed for the async Trader and Position Monitor services.
"""

import json
import logging
import re
import time
import asyncio
from threading import Lock

import httpx

from .exceptions import OpenAPIError

logger = logging.getLogger(__name__)

# Saxo's limits (developer.saxo/openapi/learn/rate-limiting): 120 requests a
# minute per session and service group ("Session"), one order a second per
# session ("SessionOrders"), and the app's requests of the day ("AppDay"). A
# refusal (429) says which ran out in its headers: an order refused for the
# order limit carries X-RateLimit-SessionOrders-*, and no Session-Reset.
ORDER_ENDPOINT = re.compile(r"^openapi/trade/v[12]/orders(/(?!precheck$)[^/]+)?$")
ORDER_METHODS = frozenset({"post", "patch", "delete"})
# A little over Saxo's second, so that two orders in a row are never refused.
ORDER_SPACING_SECONDS = 1.05
# What a refusal is waited when no header says when its limit resets.
ORDER_REFUSAL_WAIT_SECONDS = 1.0
SESSION_REFUSAL_WAIT_SECONDS = 60.0


def is_order_request(method: str, endpoint) -> bool:
    """A request Saxo counts against its order a second: placing, changing or cancelling an order."""
    return method.lower() in ORDER_METHODS and bool(ORDER_ENDPOINT.match(str(endpoint)))


def _int_header(headers, name: str) -> int | None:
    try:
        return int(headers[name]) if name in headers else None
    except (TypeError, ValueError):
        return None


def wait_after_refusal(headers, ordering: bool) -> float | None:
    """
    How long to wait after Saxo's 429 before asking once more: the reset of the
    limit that ran out (about a second for the order limit), or None when it is
    the app's limit of the day, which is not worth waiting for. Until v0.48.0 an
    order refused for the order limit waited 60 s, the default for a missing
    Session-Reset: a sale a minute late.
    """
    for dimension in ("SessionOrders", "Session", "AppDay"):
        remaining = _int_header(headers, f"X-RateLimit-{dimension}-Remaining")
        if remaining is None or remaining > 0:
            continue
        if dimension == "AppDay":
            return None
        reset = _int_header(headers, f"X-RateLimit-{dimension}-Reset")
        if reset is None:
            return ORDER_REFUSAL_WAIT_SECONDS if dimension == "SessionOrders" else SESSION_REFUSAL_WAIT_SECONDS
        return float(max(reset, 1))
    reset = _int_header(headers, "X-RateLimit-SessionOrders-Reset" if ordering else "X-RateLimit-Session-Reset")
    if reset is not None:
        return float(max(reset, 1))
    return ORDER_REFUSAL_WAIT_SECONDS if ordering else SESSION_REFUSAL_WAIT_SECONDS


class OrderPacer:
    """
    Order requests spaced ``ORDER_SPACING_SECONDS`` apart, since Saxo refuses a
    second order within a second of the session's last (429). A position sold
    while another is (the monitor sells three at a time), or the other side sold
    right after a purchase, waits under a second instead of being refused.
    The services share one session at Saxo: two of them ordering in the same
    second can still be refused, and are then waited as ``wait_after_refusal`` says.
    """

    def __init__(self, spacing: float = ORDER_SPACING_SECONDS, clock=time.monotonic):
        self._spacing = spacing
        self._clock = clock
        self._lock = asyncio.Lock()
        self._last: float | None = None

    async def wait_turn(self) -> float:
        """Wait until the next order may leave, mark it as leaving, and say how long was waited."""
        async with self._lock:
            waited = 0.0
            if self._last is not None:
                waited = max(0.0, self._spacing - (self._clock() - self._last))
                if waited > 0:
                    await asyncio.sleep(waited)
            self._last = self._clock()
            return waited

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
        self.order_pacer = OrderPacer()

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

        ordering = is_order_request(method, endpoint)
        await self.rate_limiter.wait_if_needed()
        if ordering:
            await self.order_pacer.wait_turn()
        logger.debug("AsyncAPI: %s %s", method.upper(), url)

        try:
            response = await self._client.request(method, url, headers=ep_headers, **kwargs)
        except httpx.RequestError as err:
            logger.error("AsyncAPI: request to %s failed: %s", url, err)
            raise err

        self.rate_limiter.update_limits(response.headers)

        # Handle 429 rate-limit with one retry, after the reset of the limit that ran out
        if response.status_code == 429:
            wait = wait_after_refusal(response.headers, ordering)
            if wait is not None:
                logger.warning("Rate limit exceeded (429). Waiting %.0f seconds…", wait)
                await asyncio.sleep(wait)
                if ordering:
                    await self.order_pacer.wait_turn()
                response = await self._client.request(method, url, headers=ep_headers, **kwargs)
                self.rate_limiter.update_limits(response.headers)
            else:
                logger.warning("Rate limit of the app's day exceeded (429): not asked again")

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
