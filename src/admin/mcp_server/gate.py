"""
The gate in front of the MCP endpoint: what a request to /mcp has to get past
before the SDK sees it.

- POST only: the endpoint answers in JSON (no stream), so nothing is ever held open.
- No ``Origin`` header at all: Claude Code sends none, and a browser always does
  on such a request, so a page (DNS rebinding, a page of the VPN) is refused here.
- A throttle per client address, of its own: a misconfigured Claude locks
  itself out, never the operator's login from the same VPN address.
- A bearer token of the operator's file (``tokens.py``). Its 401 names no
  authorisation server: Claude Code then never starts an OAuth flow.
- A few calls at once per token, the rest waiting their turn.

Traefik's allowlist (the VPN) stays in front of all this; the gate is needed
because the other containers of the network reach the admin directly. The path
is read from ``scope["path"]``, never from a URL rebuilt from the Host header.
"""

import asyncio
import contextvars
import json
import logging

from starlette.datastructures import Headers
from starlette.requests import Request

from src.admin.auth import LoginThrottle, client_ip

logger = logging.getLogger(__name__)

MCP_PATH = "/mcp"
MAX_CONCURRENT_CALLS = 4

# The caller of the request being answered (a tokens.Caller), for the tools and the call log.
CALLER: contextvars.ContextVar = contextvars.ContextVar("wata_mcp_caller", default=None)


def current_caller():
    return CALLER.get()


async def _answer(send, status: int, error: str, headers: dict | None = None) -> None:
    body = json.dumps({"error": error}).encode()
    raw_headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
    raw_headers += [(name.lower().encode(), value.encode()) for name, value in (headers or {}).items()]
    await send({"type": "http.response.start", "status": status, "headers": raw_headers})
    await send({"type": "http.response.body", "body": body})


class McpGate:
    def __init__(self, *, endpoint, tokens, trusted_proxies, throttle: LoginThrottle | None = None,
                 paused=lambda: False, max_concurrent: int = MAX_CONCURRENT_CALLS):
        self._endpoint = endpoint
        self._tokens = tokens
        self._trusted_proxies = trusted_proxies
        # Its own throttle: 10 wrong tokens in a row from one address, then 15 minutes of refusal.
        self._throttle = throttle or LoginThrottle(max_failures=10, lockout_seconds=900)
        self._paused = paused
        self._max_concurrent = max_concurrent
        self._in_flight: dict[str, asyncio.Semaphore] = {}

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return
        if scope["method"] != "POST":
            await _answer(send, 405, "The MCP endpoint takes POST requests only", {"Allow": "POST"})
            return
        headers = Headers(scope=scope)
        caller_address = client_ip(Request(scope), self._trusted_proxies)
        if "origin" in headers:
            logger.warning(f"MCP request with an Origin header refused, from {caller_address}")
            await _answer(send, 403, "Requests from a web page are refused")
            return
        wait = self._throttle.retry_after(caller_address)
        if wait:
            await _answer(send, 429, "Too many refused tokens from this address", {"Retry-After": str(wait)})
            return
        scheme, _, presented = (headers.get("authorization") or "").partition(" ")
        caller = self._tokens.verify(presented.strip()) if scheme.lower() == "bearer" else None
        if caller is None:
            self._throttle.record_failure(caller_address)
            logger.warning(f"MCP request with no valid token refused, from {caller_address}")
            await _answer(send, 401, "A valid token is needed (watamcptoken on the server)",
                          {"WWW-Authenticate": 'Bearer realm="wata"'})
            return
        self._throttle.record_success(caller_address)
        if self._paused():
            await _answer(send, 503, "The operator has switched the MCP endpoint off")
            return
        if scope["path"] != MCP_PATH:
            # /mcp/ is the same endpoint; the SDK only knows /mcp.
            scope = {**scope, "path": MCP_PATH, "raw_path": MCP_PATH.encode()}
        semaphore = self._in_flight.setdefault(caller.token_id, asyncio.Semaphore(self._max_concurrent))
        async with semaphore:
            token = CALLER.set(caller)
            try:
                await self._endpoint(scope, receive, send)
            finally:
                CALLER.reset(token)
