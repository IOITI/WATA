"""
Capital.com's API as the feed uses it: a session over REST, and the stream of
quotes over a WebSocket.

Only what a feed of prices needs can be asked for (``ALLOWED``). The key that
streams prices can also open positions, and nothing here may ever do so:
asking for any other address raises before anything is sent.

Neither the key, the password nor the tokens of a session are ever written to
a log or put in an error: errors carry Capital.com's code
(``error.invalid.details``) and the HTTP status, nothing of what was sent.
"""

import asyncio
import json
import logging
import math
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable
from urllib.parse import urlsplit

import httpx
import websockets

from src.capital_feed.settings import STREAMING_URL, Credentials

logger = logging.getLogger(__name__)

SESSION_PATH = "/api/v1/session"
_EPIC = r"[A-Z0-9][A-Z0-9_.]{0,63}"
# What the feed may ask Capital.com for: a session, keeping it alive, the
# description and state of markets, and their past prices. No address of an
# order, a position or an account.
ALLOWED = (
    ("POST", re.compile(r"^/api/v1/session$")),
    ("DELETE", re.compile(r"^/api/v1/session$")),
    ("GET", re.compile(r"^/api/v1/ping$")),
    ("GET", re.compile(r"^/api/v1/markets$")),
    ("GET", re.compile(rf"^/api/v1/markets/{_EPIC}$")),
    ("GET", re.compile(rf"^/api/v1/prices/{_EPIC}$")),
)
REQUEST_TIMEOUT_SECONDS = 10.0
# Capital.com takes one new session a second for a key.
SESSION_INTERVAL_SECONDS = 1.1
# After a refused login, the next try waits this long, doubled each time up to the most:
# a key or a password that is wrong is not made right by asking again.
REFUSED_FIRST_WAIT_SECONDS = 60.0
REFUSED_LONGEST_WAIT_SECONDS = 1800.0
# The most past values one request for prices returns.
MAX_PRICES = 1000
# Capital.com closes a stream that sends nothing for ten minutes.
STREAM_PING_SECONDS = 240.0
# A connection that lasted this long was not part of a run of failures.
HEALTHY_CONNECTION_SECONDS = 60.0
_LOOPBACK = ("127.0.0.1", "localhost", "::1")


class CapitalError(Exception):
    """Capital.com did not give what was asked, or could not be reached."""

    def __init__(self, message: str, *, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code


class NotAllowed(CapitalError):
    """An address the feed has no business asking for: refused before anything is sent."""


class LoginRefused(CapitalError):
    """Capital.com refused the login: the key, the email or the key's password."""


class RateLimited(CapitalError):
    """Capital.com asked to slow down (HTTP 429)."""


def check_allowed(method: str, path: str) -> None:
    if not any(method == allowed_method and pattern.match(path) for allowed_method, pattern in ALLOWED):
        raise NotAllowed(f"The Capital.com feed does not ask for {method} {path}")


def _error_code(response: httpx.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    code = body.get("errorCode") if isinstance(body, dict) else None
    return code if isinstance(code, str) else None


def _streaming_url(host) -> str:
    """
    Where the stream of a session is: the host it names, when it is one of
    Capital.com's (``wss``), or this machine's in tests. Anything else would be
    given the tokens: the address of the documentation then.
    """
    if not isinstance(host, str) or not host:
        return STREAMING_URL
    parts = urlsplit(host)
    if parts.scheme == "wss" or (parts.scheme == "ws" and parts.hostname in _LOOPBACK):
        return host.rstrip("/") + "/connect"
    return STREAMING_URL


@dataclass(frozen=True)
class Session:
    cst: str
    security_token: str
    streaming_url: str
    account_id: str | None
    started_at: float


@dataclass(frozen=True)
class MinuteCandle:
    """One minute of an instrument as Capital.com gives it, with the volume it counts."""

    ts: datetime
    bid_open: float | None
    bid_high: float | None
    bid_low: float | None
    bid_close: float | None
    ask_open: float | None
    ask_high: float | None
    ask_low: float | None
    ask_close: float | None
    volume: float | None


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def read_candle(item: dict) -> MinuteCandle | None:
    """A price of ``GET /prices``; None when it has no time in UTC."""
    stamp = item.get("snapshotTimeUTC")
    if not isinstance(stamp, str):
        return None
    try:
        ts = datetime.fromisoformat(stamp.removesuffix("Z")).replace(tzinfo=timezone.utc)
    except ValueError:
        return None

    def side(name: str, which: str) -> float | None:
        price = item.get(name)
        return _number(price.get(which)) if isinstance(price, dict) else None

    return MinuteCandle(
        ts=ts,
        bid_open=side("openPrice", "bid"), bid_high=side("highPrice", "bid"),
        bid_low=side("lowPrice", "bid"), bid_close=side("closePrice", "bid"),
        ask_open=side("openPrice", "ask"), ask_high=side("highPrice", "ask"),
        ask_low=side("lowPrice", "ask"), ask_close=side("closePrice", "ask"),
        volume=_number(item.get("lastTradedVolume")),
    )


def _utc_text(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


class CapitalRest:
    """
    The REST side: one session at a time, opened when first needed and again
    when Capital.com says its tokens are no longer valid.
    """

    def __init__(
        self,
        base_url: str,
        credentials: Credentials,
        *,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep=asyncio.sleep,
    ):
        self._base = base_url.rstrip("/")
        self._credentials = credentials
        self._client = client or httpx.AsyncClient(base_url=self._base, timeout=REQUEST_TIMEOUT_SECONDS)
        self._owns_client = client is None
        self._clock = clock
        self._sleep = sleep
        self._session: Session | None = None
        self._login_lock = asyncio.Lock()
        self._last_login_at: float | None = None
        self._refused_until: float | None = None
        self._refused_wait = REFUSED_FIRST_WAIT_SECONDS
        self.last_refusal: str | None = None
        self.logins = 0

    @property
    def session(self) -> Session | None:
        return self._session

    def retry_in(self) -> float:
        """Seconds before a refused login is tried again; 0 when nothing holds it back."""
        if self._refused_until is None:
            return 0.0
        return max(0.0, self._refused_until - self._clock())

    def forget_session(self, session: Session | None = None) -> None:
        """Capital.com no longer takes these tokens: the next request opens a new session."""
        if session is None or self._session is session:
            self._session = None

    async def _send(self, method: str, path: str, **kwargs) -> httpx.Response:
        check_allowed(method, path)
        try:
            return await self._client.request(method, path, **kwargs)
        except httpx.HTTPError as e:
            # The type of the failure only: its text can quote the request.
            raise CapitalError(f"Capital.com did not answer ({type(e).__name__})") from None

    async def login(self) -> Session:
        async with self._login_lock:
            wait = self.retry_in()
            if wait > 0:
                raise LoginRefused(
                    f"Capital.com refused the login ({self.last_refusal}); next try in {int(wait)} s", code=self.last_refusal,
                )
            if self._last_login_at is not None:
                pause = SESSION_INTERVAL_SECONDS - (self._clock() - self._last_login_at)
                if pause > 0:
                    await self._sleep(pause)
            self._last_login_at = self._clock()
            response = await self._send(
                "POST", SESSION_PATH,
                headers={"X-CAP-API-KEY": self._credentials.api_key},
                json={"identifier": self._credentials.identifier, "password": self._credentials.password,
                      "encryptedPassword": False},
            )
            cst, security_token = response.headers.get("CST"), response.headers.get("X-SECURITY-TOKEN")
            if response.status_code == 429:
                raise RateLimited("Capital.com asked to slow down (429) while logging in", status=429, code=_error_code(response))
            if response.status_code >= 400 or not cst or not security_token:
                code = _error_code(response) or f"HTTP {response.status_code}"
                self.last_refusal = code
                self._refused_until = self._clock() + self._refused_wait
                self._refused_wait = min(self._refused_wait * 2, REFUSED_LONGEST_WAIT_SECONDS)
                raise LoginRefused(f"Capital.com refused the login ({code})", status=response.status_code, code=code)
            try:
                body = response.json()
            except ValueError:
                body = {}
            body = body if isinstance(body, dict) else {}
            self._refused_until = None
            self._refused_wait = REFUSED_FIRST_WAIT_SECONDS
            self.last_refusal = None
            self.logins += 1
            account = body.get("currentAccountId")
            self._session = Session(
                cst=cst, security_token=security_token,
                streaming_url=_streaming_url(body.get("streamingHost")),
                account_id=account if isinstance(account, str) else None,
                started_at=time.time(),
            )
            return self._session

    async def ensure_session(self) -> Session:
        return self._session or await self.login()

    def _answer(self, response: httpx.Response, what: str):
        if response.status_code == 429:
            raise RateLimited(f"Capital.com asked to slow down (429) for {what}", status=429, code=_error_code(response))
        if response.status_code >= 400:
            code = _error_code(response)
            raise CapitalError(
                f"Capital.com answered {response.status_code} for {what}" + (f" ({code})" if code else ""),
                status=response.status_code, code=code,
            )
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            raise CapitalError(f"Capital.com's answer for {what} is not JSON", status=response.status_code) from None

    async def request(self, method: str, path: str, params: dict | None = None):
        """A request in the session, opened again once if Capital.com no longer takes its tokens."""
        check_allowed(method, path)
        for attempt in (1, 2):
            session = await self.ensure_session()
            response = await self._send(
                method, path, params=params,
                headers={"CST": session.cst, "X-SECURITY-TOKEN": session.security_token},
            )
            if response.status_code == 401 and attempt == 1:
                self.forget_session(session)
                continue
            return self._answer(response, f"{method} {path}")
        raise AssertionError("unreachable")

    async def ping(self) -> None:
        await self.request("GET", "/api/v1/ping")

    async def market(self, epic: str) -> dict:
        """An instrument's details: its name, type, currency, opening hours, and the state of its market."""
        answer = await self.request("GET", f"/api/v1/markets/{epic}")
        return answer if isinstance(answer, dict) else {}

    async def markets(self, epics: Iterable[str]) -> dict[str, dict]:
        """The markets of several instruments at once, by epic: each with its status (TRADEABLE, CLOSED...)."""
        answer = await self.request("GET", "/api/v1/markets", params={"epics": ",".join(epics)})
        return {epic: entry for epic, entry in _market_entries(answer)}

    async def search(self, term: str) -> list[dict]:
        answer = await self.request("GET", "/api/v1/markets", params={"searchTerm": term})
        return [entry for _, entry in _market_entries(answer)]

    async def prices(self, epic: str, start: datetime, end: datetime, *, resolution: str = "MINUTE",
                     maximum: int = MAX_PRICES) -> list[MinuteCandle]:
        """The candles of [start, end] at ``resolution``, oldest first, as Capital.com counts them."""
        answer = await self.request(
            "GET", f"/api/v1/prices/{epic}",
            params={"resolution": resolution, "from": _utc_text(start), "to": _utc_text(end), "max": min(maximum, MAX_PRICES)},
        )
        items = answer.get("prices") if isinstance(answer, dict) else None
        candles = [read_candle(item) for item in items or [] if isinstance(item, dict)]
        return sorted((candle for candle in candles if candle is not None), key=lambda candle: candle.ts)

    async def logout(self) -> None:
        session = self._session
        if session is None:
            return
        self._session = None
        try:
            response = await self._send(
                "DELETE", SESSION_PATH, headers={"CST": session.cst, "X-SECURITY-TOKEN": session.security_token},
            )
            if response.status_code >= 400:
                logger.info("Capital.com answered %s to the end of the session", response.status_code)
        except CapitalError as e:
            logger.info("The session could not be ended: %s", e)

    async def close(self) -> None:
        await self.logout()
        if self._owns_client:
            await self._client.aclose()


def market_epic(entry: dict) -> str | None:
    epic = entry.get("epic") or (entry.get("instrument") or {}).get("epic")
    return epic if isinstance(epic, str) else None


def market_status(entry: dict) -> str | None:
    """TRADEABLE, CLOSED... from either form Capital.com describes a market in."""
    status = entry.get("marketStatus") or (entry.get("snapshot") or {}).get("marketStatus")
    return status if isinstance(status, str) else None


def _market_entries(answer) -> list[tuple[str, dict]]:
    """The markets of a ``GET /markets`` answer, whichever list Capital.com puts them in."""
    if not isinstance(answer, dict):
        return []
    entries = []
    for key in ("marketDetails", "markets"):
        for entry in answer.get(key) or []:
            if isinstance(entry, dict) and market_epic(entry):
                entries.append((market_epic(entry), entry))
    return entries


# ── the stream ──

# Called for each quote, from the loop that reads the socket: memory only.
# (epic, bid, ask, Capital.com's time in milliseconds or None, time of receipt in epoch seconds)
QuoteCallback = Callable[[str, float, float, int | None, float], None]


def read_stream_quote(payload) -> tuple[str, float, float, int | None] | None:
    """A quote of the stream as (epic, bid, ask, time in ms); None when it lacks a price."""
    if not isinstance(payload, dict):
        return None
    epic = payload.get("epic")
    bid, ask = _number(payload.get("bid")), _number(payload.get("ofr"))
    if not isinstance(epic, str) or bid is None or ask is None:
        return None
    stamp = payload.get("timestamp")
    stamp = int(stamp) if isinstance(stamp, (int, float)) and not isinstance(stamp, bool) else None
    return epic, bid, ask, stamp


class StreamEnded(Exception):
    """The connection ended for a reason that calls for a new one (silence, tokens refused)."""


class CapitalStream:
    """
    One WebSocket with the quotes of the epics, kept open: subscribed at each
    connection, pinged so that Capital.com keeps it, opened again after a
    break with a growing wait. A connection that stays silent while quotes are
    expected (``expects_quotes``) is not trusted to be alive: it is replaced.
    """

    def __init__(
        self,
        rest: CapitalRest,
        epics: Iterable[str],
        on_quote: QuoteCallback,
        *,
        clock: Callable[[], float] = time.time,
        reconnect_delay: float = 1.0,
        max_reconnect_delay: float = 30.0,
        ping_seconds: float = STREAM_PING_SECONDS,
        silence_seconds: float = 90.0,
        expects_quotes: Callable[[], bool] = lambda: True,
        connect=None,
    ):
        self._rest = rest
        self._epics = list(epics)
        self._on_quote = on_quote
        self._clock = clock
        self._reconnect_delay = reconnect_delay
        self._max_reconnect_delay = max_reconnect_delay
        self._ping_seconds = ping_seconds
        self._silence_seconds = silence_seconds
        self._expects_quotes = expects_quotes
        self._connect = connect or (lambda url: websockets.connect(
            url,
            # Capital.com's stream directly: a proxy found in the environment is not for it.
            proxy=None,
            open_timeout=REQUEST_TIMEOUT_SECONDS,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=5,
            max_size=2 ** 20,
        ))
        self._stopping = asyncio.Event()
        self._ws = None
        self._closing = None
        self._correlation = 0

        self.connected_since: float | None = None
        self.last_message_at: float | None = None
        self.subscribed: dict[str, str] = {}
        self.connections = 0
        self.last_error: str | None = None

    @property
    def connected(self) -> bool:
        return self.connected_since is not None

    def stop(self) -> None:
        self._stopping.set()
        if self._ws is not None:
            try:
                # Wakes the reading of the socket at once.
                self._closing = asyncio.ensure_future(self._ws.close())
            except RuntimeError:
                pass

    async def _sleep_or_stop(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stopping.wait(), max(seconds, 0.0))
        except asyncio.TimeoutError:
            pass

    def _message(self, destination: str, session: Session, payload: dict | None = None) -> str:
        self._correlation += 1
        message = {
            "destination": destination,
            "correlationId": str(self._correlation),
            "cst": session.cst,
            "securityToken": session.security_token,
        }
        if payload is not None:
            message["payload"] = payload
        return json.dumps(message)

    async def run(self) -> None:
        """Stream until ``stop()``. Never raises: every failure ends in a wait and a new connection."""
        delay = self._reconnect_delay
        while not self._stopping.is_set():
            wait = None
            try:
                session = await self._rest.ensure_session()
                await self._stream(session)
            except asyncio.CancelledError:
                raise
            except LoginRefused as e:
                self.last_error = str(e)
                logger.warning("%s", e)
                wait = max(self._rest.retry_in(), self._reconnect_delay)
            except Exception as e:
                if not self._stopping.is_set():
                    # Closing it to stop is not a break.
                    self.last_error = str(e) or type(e).__name__
                    logger.warning("Capital.com stream interrupted: %s", self.last_error)
            finally:
                lasted = self._clock() - self.connected_since if self.connected_since is not None else 0.0
                self.connected_since = None
                self._ws = None
            if self._stopping.is_set():
                break
            # A connection that held is followed by a quick one; breaks in a row wait longer each time.
            if lasted >= HEALTHY_CONNECTION_SECONDS:
                delay = self._reconnect_delay
            await self._sleep_or_stop(wait if wait is not None else delay)
            if wait is None:
                delay = min(delay * 2, self._max_reconnect_delay)

    async def _stream(self, session: Session) -> None:
        connection = await self._connect(session.streaming_url)
        self._ws = connection
        try:
            self.connections += 1
            self.connected_since = self._clock()
            self.last_message_at = self.connected_since
            self.subscribed = {}
            await connection.send(self._message("marketData.subscribe", session, {"epics": self._epics}))
            logger.info("Capital.com stream open, asking for %s.", ", ".join(self._epics))
            await self._read(connection, session)
        finally:
            await connection.close()

    async def _read(self, connection, session: Session) -> None:
        next_ping = self._clock() + self._ping_seconds
        while not self._stopping.is_set():
            now = self._clock()
            silent_until = (self.last_message_at or now) + self._silence_seconds
            wait = max(0.05, min(next_ping - now, silent_until - now, 1.0))
            try:
                # Cancelling recv loses nothing: the message waits for the next one.
                async with asyncio.timeout(wait):
                    raw = await connection.recv()
            except TimeoutError:
                now = self._clock()
                if now >= next_ping:
                    await connection.send(self._message("ping", session))
                    next_ping = now + self._ping_seconds
                if self._expects_quotes() and now - (self.last_message_at or now) > self._silence_seconds:
                    raise StreamEnded(f"nothing received for {int(now - self.last_message_at)} s while the market is open")
                continue
            received_at = self._clock()
            self.last_message_at = received_at
            self._handle(raw, received_at, session)

    def _handle(self, raw, received_at: float, session: Session) -> None:
        try:
            message = json.loads(raw)
        except (TypeError, ValueError):
            logger.debug("Capital.com stream: a message that is not JSON")
            return
        if not isinstance(message, dict):
            return
        destination = message.get("destination")
        if destination == "quote":
            quote = read_stream_quote(message.get("payload"))
            if quote is None:
                return
            epic, bid, ask, stamp = quote
            try:
                self._on_quote(epic, bid, ask, stamp, received_at)
            except Exception as e:
                # A fault of whoever records must not end the stream.
                logger.error("Recording a quote failed: %s", e, exc_info=True)
            return
        if message.get("status") not in (None, "OK"):
            payload = message.get("payload") if isinstance(message.get("payload"), dict) else {}
            code = payload.get("errorCode") or message.get("status")
            if isinstance(code, str) and ("session" in code or "token" in code):
                self._rest.forget_session(session)
                raise StreamEnded(f"Capital.com no longer takes the session's tokens ({code})")
            logger.warning("Capital.com stream: %s answered %s", destination, code)
            return
        if destination == "marketData.subscribe":
            subscriptions = (message.get("payload") or {}).get("subscriptions") or {}
            if isinstance(subscriptions, dict):
                self.subscribed = {str(epic): str(state) for epic, state in subscriptions.items()}
                refused = {epic: state for epic, state in self.subscribed.items() if state != "PROCESSED"}
                if refused:
                    logger.warning("Capital.com did not subscribe to: %s", ", ".join(f"{e} ({s})" for e, s in refused.items()))
