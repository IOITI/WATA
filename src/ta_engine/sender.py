"""
The signals of live rules, posted to the web server's /internal/signal with its
Bearer token, as the scheduler posts its close. Each is sent once: one that
could not be is not tried again, since it would arrive late.
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Sent:
    ok: bool
    http_status: int | None = None
    signal_uuid: str | None = None
    error: str | None = None


class SignalSender:
    # token_getter: a function, run in a thread (the token file of before is slow to decrypt), or a
    # coroutine function, awaited (the token read again from PostgreSQL).
    def __init__(self, service_url: str, token_getter: Callable, http: httpx.AsyncClient, timeout_seconds: float):
        self._url = f"{service_url.rstrip('/')}/internal/signal"
        self._token_getter = token_getter
        self._http = http
        self._timeout = timeout_seconds
        self._token: str | None = None

    async def send(self, body: dict) -> Sent:
        for attempt in range(2):
            if self._token is None:
                try:
                    if asyncio.iscoroutinefunction(self._token_getter):
                        self._token = await self._token_getter()
                    else:
                        self._token = await asyncio.to_thread(self._token_getter)
                except Exception as e:
                    return Sent(False, error=f"The web server's token could not be read: {type(e).__name__}")
            try:
                response = await self._http.post(
                    self._url, json=body, headers={"Authorization": f"Bearer {self._token}"}, timeout=self._timeout,
                )
            except httpx.HTTPError as e:
                return Sent(False, error=f"{type(e).__name__}: {e}" if str(e) else type(e).__name__)
            if response.status_code == 401 and attempt == 0:
                # Refused, so not stored: read the token again (it may have been rotated) and send it once more.
                self._token = None
                continue
            if response.status_code != 200:
                return Sent(False, http_status=response.status_code, error=response.text[:300])
            try:
                signal_uuid = response.json().get("signal_uuid")
            except ValueError:
                signal_uuid = None
            return Sent(True, http_status=200, signal_uuid=signal_uuid if isinstance(signal_uuid, str) else None)
        return Sent(False, http_status=401, error="The web server refused the token")
