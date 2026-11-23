"""
The signals of live rules, posted to the web server's /internal/signal with its
Bearer token, as the scheduler posts its close.

A signal is stored once: it goes with its key (its rule and its row of the
engine's journal), and the web server answers a key it has already taken with
the signal it stored then. So a signal that got no answer (the web server
starting again, a connection that broke) is sent again after RETRY_PAUSES, for
as long as the engine allows (``patience``: never past the age a signal may
have), then given up. Until v0.100.0 it was sent once: a web server restarted
during a deploy lost the signal of that moment, a close as well as an entry.

An answer is final, whatever it says, but a refused token: it is read again
(it may have been rotated) and the signal sent once more. A request that timed
out is not sent again either: it has already waited ``timeout_seconds``.

``prepare`` reads the token before a signal needs it: until v0.100.0 the first
signal of a start paid for it (PostgreSQL, the key unsealed), 0.9 s on the
first live entry of 23 November 2026.
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

# The header a signal's key goes in: the web server stores a key once (src/web_server/signal_store.py ``TakenKeys``).
SIGNAL_KEY_HEADER = "X-WATA-Signal-Key"
# After a send that got no answer: again after these pauses, 5 s in all (a container starts again in less), then not at all.
RETRY_PAUSES = (0.25, 0.75, 1.5, 2.5)
# What is no answer: no connection, or one that broke before the answer. Whether the web server took the signal is
# not known then, which its key settles when it is sent again.
NO_ANSWER = (httpx.NetworkError, httpx.RemoteProtocolError)


@dataclass(frozen=True)
class Sent:
    ok: bool
    http_status: int | None = None
    signal_uuid: str | None = None
    error: str | None = None
    # How many times it was sent before this answer, or before it was given up.
    attempts: int = 1


class SignalSender:
    # token_getter: a function, run in a thread (the token file of before is slow to decrypt), or a
    # coroutine function, awaited (the token read again from PostgreSQL).
    def __init__(self, service_url: str, token_getter: Callable, http: httpx.AsyncClient, timeout_seconds: float,
                 sleep: Callable = asyncio.sleep):
        self._url = f"{service_url.rstrip('/')}/internal/signal"
        self._token_getter = token_getter
        self._http = http
        self._timeout = timeout_seconds
        self._sleep = sleep
        self._token: str | None = None

    async def _read_token(self) -> str | None:
        """Why the token could not be read; None once it is held."""
        if self._token is not None:
            return None
        try:
            if asyncio.iscoroutinefunction(self._token_getter):
                self._token = await self._token_getter()
            else:
                self._token = await asyncio.to_thread(self._token_getter)
        except Exception as e:
            return f"The web server's token could not be read: {type(e).__name__}"
        return None

    async def prepare(self) -> str | None:
        """
        The token read ahead of the first signal, so that it does not wait for
        it. Why it could not be, which is said and tried again at the signal
        itself; None when it is held.
        """
        return await self._read_token()

    async def send(self, body: dict, key: str | None = None, patience: float | None = None) -> Sent:
        """
        ``body`` posted under ``key``. ``patience``: how long it may be tried
        again after sends that got no answer (all of RETRY_PAUSES when not said).
        """
        headers = {SIGNAL_KEY_HEADER: key} if key else {}
        pauses = list(RETRY_PAUSES)
        left = sum(pauses) if patience is None else max(0.0, patience)
        attempts = 0
        refused = False
        while True:
            error = await self._read_token()
            if error is not None:
                return Sent(False, error=error, attempts=max(attempts, 1))
            attempts += 1
            try:
                response = await self._http.post(
                    self._url, json=body, headers={**headers, "Authorization": f"Bearer {self._token}"}, timeout=self._timeout,
                )
            except NO_ANSWER as e:
                said = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
                if not pauses or pauses[0] > left:
                    return Sent(False, error=said, attempts=attempts)
                pause = pauses.pop(0)
                left -= pause
                logger.warning("The web server gave no answer to a signal (%s): sent again in %.2f s.", said, pause)
                await self._sleep(pause)
                continue
            except httpx.HTTPError as e:
                return Sent(False, error=f"{type(e).__name__}: {e}" if str(e) else type(e).__name__, attempts=attempts)
            if response.status_code == 401 and not refused:
                # Refused, so not stored: read the token again (it may have been rotated) and send it once more.
                refused = True
                self._token = None
                continue
            if response.status_code == 401:
                return Sent(False, http_status=401, error="The web server refused the token", attempts=attempts)
            if response.status_code != 200:
                return Sent(False, http_status=response.status_code, error=response.text[:300], attempts=attempts)
            try:
                signal_uuid = response.json().get("signal_uuid")
            except ValueError:
                signal_uuid = None
            return Sent(True, http_status=200, signal_uuid=signal_uuid if isinstance(signal_uuid, str) else None,
                        attempts=attempts)
