"""
The Capital.com client against imitations of Capital.com's documented API:
REST through httpx's mock transport, the stream through a WebSocket server on
this machine. What Capital.com really answers is checked with the probe
(src/capital_feed/probe.py) once there is a key.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone

import httpx
import pytest
import websockets

from src.capital_feed.client import (
    STREAMING_URL,
    CapitalError,
    CapitalRest,
    CapitalStream,
    LoginRefused,
    NotAllowed,
    RateLimited,
    _streaming_url,
    check_allowed,
    read_candle,
    read_stream_quote,
)
from src.capital_feed.settings import Credentials

KEY = "key-SECRET-1234"
PASSWORD = "pw-SECRET-5678"
CREDENTIALS = Credentials(KEY, "me@example.com", PASSWORD)
BASE = "https://demo-api-capital.backend-capital.com"


class Clock:
    def __init__(self, at=1000.0):
        self.at = at

    def __call__(self):
        return self.at


class FakeCapital:
    """Capital.com's REST API as its documentation describes it, for one test."""

    def __init__(self, streaming_host="wss://api-streaming-capital.backend-capital.com/"):
        self.requests: list[httpx.Request] = []
        self.refuse_login: str | None = None
        self.sessions = 0
        self.expired: set[str] = set()
        self.streaming_host = streaming_host
        self.answers: dict[tuple[str, str], tuple[int, dict]] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = (request.method, request.url.path)
        if key == ("POST", "/api/v1/session"):
            if self.refuse_login:
                return httpx.Response(401, json={"errorCode": self.refuse_login})
            self.sessions += 1
            body = {"currentAccountId": "account-1", "accountType": "CFD"}
            if self.streaming_host is not None:
                body["streamingHost"] = self.streaming_host
            return httpx.Response(
                200, json=body, headers={"CST": f"cst-{self.sessions}", "X-SECURITY-TOKEN": f"xst-{self.sessions}"},
            )
        if request.headers.get("CST") in self.expired:
            return httpx.Response(401, json={"errorCode": "error.invalid.session.token"})
        if key in self.answers:
            status, body = self.answers[key]
            return httpx.Response(status, json=body)
        return httpx.Response(200, json={"status": "OK"})

    def rest(self, **options) -> CapitalRest:
        client = httpx.AsyncClient(transport=httpx.MockTransport(self.handler), base_url=BASE)

        async def no_wait(_):
            return None

        return CapitalRest(BASE, CREDENTIALS, client=client, sleep=options.pop("sleep", no_wait), **options)


def _run(scenario):
    return asyncio.run(asyncio.wait_for(scenario(), timeout=15))


# ── what may be asked ──


@pytest.mark.parametrize("method, path", [
    ("POST", "/api/v1/session"), ("DELETE", "/api/v1/session"), ("GET", "/api/v1/ping"),
    ("GET", "/api/v1/markets"), ("GET", "/api/v1/markets/US100"), ("GET", "/api/v1/prices/OIL_CRUDE"),
])
def test_what_a_feed_of_prices_needs_can_be_asked(method, path):
    check_allowed(method, path)


@pytest.mark.parametrize("method, path", [
    ("POST", "/api/v1/positions"), ("POST", "/api/v1/workingorders"), ("DELETE", "/api/v1/positions/D1"),
    ("PUT", "/api/v1/positions/D1"), ("GET", "/api/v1/accounts"), ("PUT", "/api/v1/session"),
    ("POST", "/api/v1/accounts/topUp"), ("GET", "/api/v1/markets/US100/../../positions"), ("GET", "/api/v1/prices/us100"),
])
def test_nothing_that_trades_or_touches_the_account_can_be_asked(method, path):
    with pytest.raises(NotAllowed):
        check_allowed(method, path)


def test_an_address_that_is_not_allowed_is_refused_before_anything_is_sent():
    capital = FakeCapital()

    async def scenario():
        rest = capital.rest()
        with pytest.raises(NotAllowed):
            await rest.request("POST", "/api/v1/positions", params={"epic": "US100"})
        await rest.close()

    _run(scenario)

    assert capital.requests == []


# ── the session ──


def test_the_login_sends_the_key_and_the_password_and_keeps_the_tokens_it_gets():
    capital = FakeCapital()

    async def scenario():
        rest = capital.rest()
        session = await rest.login()
        await rest.ping()
        return session

    session = _run(scenario)

    login, ping = capital.requests
    assert login.headers["X-CAP-API-KEY"] == KEY
    assert json.loads(login.content) == {"identifier": "me@example.com", "password": PASSWORD, "encryptedPassword": False}
    assert (session.cst, session.security_token, session.account_id) == ("cst-1", "xst-1", "account-1")
    assert session.streaming_url == "wss://api-streaming-capital.backend-capital.com/connect"
    # The tokens go with every request of the session, the key with none.
    assert (ping.headers["CST"], ping.headers["X-SECURITY-TOKEN"]) == ("cst-1", "xst-1")
    assert "X-CAP-API-KEY" not in ping.headers


def test_tokens_capital_com_no_longer_takes_open_a_new_session_once():
    capital = FakeCapital()

    async def scenario():
        rest = capital.rest()
        await rest.login()
        capital.expired.add("cst-1")
        await rest.ping()
        return rest

    rest = _run(scenario)

    assert [(r.method, r.url.path) for r in capital.requests] == [
        ("POST", "/api/v1/session"), ("GET", "/api/v1/ping"), ("POST", "/api/v1/session"), ("GET", "/api/v1/ping"),
    ]
    assert rest.session.cst == "cst-2"


def test_a_session_refused_twice_in_a_row_is_an_error_not_a_loop():
    capital = FakeCapital()

    async def scenario():
        rest = capital.rest()
        await rest.login()
        capital.expired.update({"cst-1", "cst-2"})
        with pytest.raises(CapitalError) as raised:
            await rest.ping()
        return raised.value

    error = _run(scenario)

    assert error.status == 401
    assert sum(1 for r in capital.requests if r.url.path == "/api/v1/session") == 2


def test_a_refused_login_is_not_tried_again_before_its_wait_which_doubles():
    capital = FakeCapital()
    capital.refuse_login = "error.invalid.details"
    clock = Clock()

    async def scenario():
        rest = capital.rest(clock=clock)
        with pytest.raises(LoginRefused) as first:
            await rest.login()
        assert first.value.code == "error.invalid.details"
        assert rest.retry_in() == 60.0
        # Within the wait: refused here, Capital.com is not asked.
        clock.at += 30
        with pytest.raises(LoginRefused):
            await rest.login()
        assert sum(1 for r in capital.requests if r.url.path == "/api/v1/session") == 1
        clock.at += 31
        with pytest.raises(LoginRefused):
            await rest.login()
        assert rest.retry_in() == 120.0
        # Put right: accepted at the next try, and the waits start again from the first.
        capital.refuse_login = None
        clock.at += 121
        await rest.login()
        assert rest.retry_in() == 0.0 and rest.last_refusal is None

    _run(scenario)


def test_two_logins_are_more_than_a_second_apart():
    capital = FakeCapital()
    clock = Clock()
    waits = []

    async def sleep(seconds):
        waits.append(seconds)
        clock.at += seconds

    async def scenario():
        rest = capital.rest(clock=clock, sleep=sleep)
        await rest.login()
        clock.at += 0.3
        await rest.login()

    _run(scenario)

    assert waits == [pytest.approx(0.8)]


def test_capital_com_asking_to_slow_down_is_said_as_such():
    capital = FakeCapital()
    capital.answers[("GET", "/api/v1/markets")] = (429, {"errorCode": "error.too-many.requests"})

    async def scenario():
        with pytest.raises(RateLimited) as raised:
            await capital.rest().markets(["US100"])
        return raised.value

    assert _run(scenario).code == "error.too-many.requests"


def test_neither_the_key_nor_the_password_is_ever_in_an_error_or_a_log_line(caplog):
    capital = FakeCapital()
    capital.refuse_login = "error.invalid.details"

    def broken(request):
        raise httpx.ConnectError(f"cannot reach {request.url} with {KEY}")

    async def scenario():
        messages = []
        with pytest.raises(LoginRefused) as refused:
            await capital.rest().login()
        messages.append(str(refused.value))
        unreachable = CapitalRest(BASE, CREDENTIALS, client=httpx.AsyncClient(transport=httpx.MockTransport(broken), base_url=BASE))
        with pytest.raises(CapitalError) as failed:
            await unreachable.login()
        messages.append(str(failed.value))
        return messages

    with caplog.at_level(logging.DEBUG):
        messages = _run(scenario)

    everything = "\n".join(messages + [record.getMessage() for record in caplog.records])
    assert KEY not in everything and PASSWORD not in everything
    assert "error.invalid.details" in messages[0] and "ConnectError" in messages[1]


def test_the_session_is_ended_at_capital_com_on_close():
    capital = FakeCapital()

    async def scenario():
        rest = capital.rest()
        await rest.login()
        await rest.close()
        assert rest.session is None

    _run(scenario)

    end = capital.requests[-1]
    assert (end.method, end.url.path, end.headers["CST"]) == ("DELETE", "/api/v1/session", "cst-1")


@pytest.mark.parametrize("host, expected", [
    ("wss://api-streaming-capital.backend-capital.com/", "wss://api-streaming-capital.backend-capital.com/connect"),
    ("wss://other-streaming.backend-capital.com", "wss://other-streaming.backend-capital.com/connect"),
    # The tokens never go out unencrypted, but to this machine in tests.
    ("ws://streaming.example.com/", STREAMING_URL),
    ("ws://127.0.0.1:8765/", "ws://127.0.0.1:8765/connect"),
    (None, STREAMING_URL),
    ("", STREAMING_URL),
])
def test_the_stream_is_where_the_session_says_when_it_is_encrypted(host, expected):
    assert _streaming_url(host) == expected


# ── what is read ──


def test_past_minutes_are_asked_for_by_utc_and_come_back_in_time_order():
    capital = FakeCapital()
    capital.answers[("GET", "/api/v1/prices/US100")] = (200, {"prices": [
        {"snapshotTime": "2026-10-15T10:31:00", "snapshotTimeUTC": "2026-10-15T08:31:00",
         "openPrice": {"bid": 25000.5, "ask": 25001.5}, "closePrice": {"bid": 25002.0, "ask": 25003.0},
         "highPrice": {"bid": 25004.0, "ask": 25005.0}, "lowPrice": {"bid": 24999.0, "ask": 25000.0},
         "lastTradedVolume": 187},
        {"snapshotTime": "2026-10-15T10:30:00", "snapshotTimeUTC": "2026-10-15T08:30:00",
         "openPrice": {"bid": 24990.0, "ask": 24991.0}, "closePrice": {"bid": 25000.5, "ask": 25001.5},
         "highPrice": {"bid": 25001.0, "ask": 25002.0}, "lowPrice": {"bid": 24989.0, "ask": 24990.0},
         "lastTradedVolume": 168},
        {"snapshotTime": "2026-10-15T10:32:00", "openPrice": {}},
    ], "instrumentType": "INDICES"})

    async def scenario():
        return await capital.rest().prices(
            "US100", datetime(2026, 10, 15, 10, 25, tzinfo=timezone.utc).astimezone(), datetime(2026, 10, 15, 8, 31, 59, tzinfo=timezone.utc),
            maximum=12,
        )

    candles = _run(scenario)

    asked = capital.requests[-1].url.params
    assert dict(asked) == {"resolution": "MINUTE", "from": "2026-10-15T10:25:00", "to": "2026-10-15T08:31:59", "max": "12"}
    assert [candle.ts.isoformat() for candle in candles] == ["2026-10-15T08:30:00+00:00", "2026-10-15T08:31:00+00:00"]
    assert (candles[1].bid_open, candles[1].ask_close, candles[1].volume) == (25000.5, 25003.0, 187.0)


def test_a_minute_without_a_time_in_utc_or_with_a_price_missing_is_read_as_it_can_be():
    assert read_candle({"snapshotTime": "2026-10-15T10:32:00"}) is None
    candle = read_candle({"snapshotTimeUTC": "2026-10-15T08:32:00", "openPrice": {"bid": 1.5}, "lastTradedVolume": True})
    assert (candle.bid_open, candle.ask_open, candle.volume) == (1.5, None, None)


@pytest.mark.parametrize("answer", [
    {"marketDetails": [{"instrument": {"epic": "US100"}, "snapshot": {"marketStatus": "TRADEABLE"}}]},
    {"markets": [{"epic": "US100", "marketStatus": "TRADEABLE", "instrumentName": "US Tech 100"}]},
])
def test_the_state_of_markets_is_read_from_either_form(answer):
    capital = FakeCapital()
    capital.answers[("GET", "/api/v1/markets")] = (200, answer)

    async def scenario():
        return await capital.rest().markets(["US100", "US500"])

    markets = _run(scenario)

    assert list(markets) == ["US100"]
    assert capital.requests[-1].url.params["epics"] == "US100,US500"


@pytest.mark.parametrize("payload, expected", [
    ({"epic": "US100", "product": "CFD", "bid": 25000.5, "bidQty": 10.0, "ofr": 25001.5, "ofrQty": 10.0,
      "timestamp": 1792053000123}, ("US100", 25000.5, 25001.5, 1792053000123)),
    ({"epic": "US100", "bid": 25000, "ofr": 25001}, ("US100", 25000.0, 25001.0, None)),
    ({"epic": "US100", "bid": 25000.5}, None),
    ({"epic": "US100", "bid": "25000", "ofr": 25001}, None),
    ({"bid": 1, "ofr": 2}, None),
    ("quote", None),
])
def test_a_quote_of_the_stream_needs_its_epic_and_both_prices(payload, expected):
    assert read_stream_quote(payload) == expected


# ── the stream ──


class LocalCapitalStream:
    """
    Capital.com's stream on this machine for the time of a test. ``script`` is
    run for each connection with its number (1 for the first); the connection
    stays open until it returns.
    """

    def __init__(self, script):
        self._script = script
        self.received: list[list[dict]] = []
        self.left: list[asyncio.Event] = []

    async def __aenter__(self):
        self._server = await websockets.serve(self._handle, "127.0.0.1", 0)
        self.host = f"ws://127.0.0.1:{self._server.sockets[0].getsockname()[1]}/"
        return self

    async def __aexit__(self, *_):
        self._server.close()
        await self._server.wait_closed()

    async def _handle(self, connection):
        assert connection.request.path == "/connect"
        messages: list[dict] = []
        self.received.append(messages)
        over = asyncio.Event()
        self.left.append(over)
        try:
            await self._script(Connection(connection, messages), len(self.received))
        finally:
            over.set()


class Connection:
    """A connection of the local stream, as its script sees it."""

    def __init__(self, ws, messages):
        self.ws = ws
        self.messages = messages

    async def next_message(self) -> dict:
        message = json.loads(await self.ws.recv())
        self.messages.append(message)
        return message

    async def subscribed(self) -> dict:
        """Read the subscription and answer it as Capital.com does."""
        message = await self.next_message()
        assert message["destination"] == "marketData.subscribe"
        await self.ws.send(json.dumps({
            "status": "OK", "destination": "marketData.subscribe", "correlationId": message["correlationId"],
            "payload": {"subscriptions": {epic: "PROCESSED" for epic in message["payload"]["epics"]}},
        }))
        return message

    async def quote(self, bid, ofr, epic="US100", timestamp=1792053000000):
        await self.ws.send(json.dumps({
            "status": "OK", "destination": "quote",
            "payload": {"epic": epic, "product": "CFD", "bid": bid, "bidQty": 5.0, "ofr": ofr, "ofrQty": 5.0, "timestamp": timestamp},
        }))

    async def stay(self):
        """Answer pings until the client leaves."""
        try:
            while True:
                message = await self.next_message()
                if message.get("destination") == "ping":
                    await self.ws.send(json.dumps({"status": "OK", "destination": "ping", "correlationId": message["correlationId"], "payload": {}}))
        except websockets.exceptions.ConnectionClosed:
            return


async def _until(condition, timeout=5.0):
    async def poll():
        while not condition():
            await asyncio.sleep(0.005)
    await asyncio.wait_for(poll(), timeout)


def _stream(capital: FakeCapital, quotes: list, **options) -> CapitalStream:
    options.setdefault("reconnect_delay", 0.01)
    options.setdefault("max_reconnect_delay", 0.02)
    return CapitalStream(
        capital.rest(), ["US100", "US500"],
        lambda epic, bid, ask, stamp, received_at: quotes.append((epic, bid, ask, stamp, received_at)),
        **options,
    )


def test_the_stream_subscribes_with_the_tokens_of_the_session_and_hands_each_quote_over():
    quotes = []

    async def script(connection, number):
        await connection.subscribed()
        await connection.quote(25000.5, 25001.5, timestamp=1792053000123)
        await connection.ws.send(json.dumps({"status": "OK", "destination": "ohlc.event", "payload": {}}))
        await connection.quote(25000.75, 25001.75, epic="US500")
        await connection.stay()

    async def scenario():
        async with LocalCapitalStream(script) as server:
            capital = FakeCapital(streaming_host=server.host)
            stream = _stream(capital, quotes)
            running = asyncio.create_task(stream.run())
            await _until(lambda: len(quotes) == 2)
            subscription = server.received[0][0]
            assert stream.subscribed == {"US100": "PROCESSED", "US500": "PROCESSED"}
            assert stream.connected and stream.connections == 1
            stream.stop()
            await asyncio.wait_for(running, 3)
            return subscription

    subscription = _run(scenario)

    assert (subscription["cst"], subscription["securityToken"]) == ("cst-1", "xst-1")
    assert subscription["payload"] == {"epics": ["US100", "US500"]}
    assert [quote[:4] for quote in quotes] == [("US100", 25000.5, 25001.5, 1792053000123), ("US500", 25000.75, 25001.75, 1792053000000)]
    # Received now, on this machine's clock: Capital.com's own time is kept apart.
    assert all(abs(quote[4] - __import__("time").time()) < 10 for quote in quotes)


def test_the_stream_is_pinged_so_that_capital_com_keeps_it():
    async def script(connection, number):
        await connection.subscribed()
        await connection.stay()

    async def scenario():
        async with LocalCapitalStream(script) as server:
            stream = _stream(FakeCapital(streaming_host=server.host), [], ping_seconds=0.1, expects_quotes=lambda: False)
            running = asyncio.create_task(stream.run())
            await _until(lambda: sum(1 for m in server.received[0] if m["destination"] == "ping") >= 2 if server.received else False)
            stream.stop()
            await asyncio.wait_for(running, 3)
            return server.received[0]

    messages = _run(scenario)

    ping = next(message for message in messages if message["destination"] == "ping")
    assert (ping["cst"], ping["securityToken"]) == ("cst-1", "xst-1")


def test_a_stream_that_capital_com_ends_is_opened_and_subscribed_again():
    quotes = []

    async def script(connection, number):
        await connection.subscribed()
        await connection.quote(25000.0 + number, 25001.0 + number)
        if number == 1:
            await connection.ws.close()
            return
        await connection.stay()

    async def scenario():
        async with LocalCapitalStream(script) as server:
            stream = _stream(FakeCapital(streaming_host=server.host), quotes)
            running = asyncio.create_task(stream.run())
            await _until(lambda: len(quotes) == 2)
            stream.stop()
            await asyncio.wait_for(running, 3)
            return server, stream

    server, stream = _run(scenario)

    assert [quote[1] for quote in quotes] == [25001.0, 25002.0]
    assert len(server.received) == 2 and server.received[1][0]["destination"] == "marketData.subscribe"
    assert stream.connections == 2


def test_a_stream_silent_while_the_market_is_open_is_replaced():
    async def script(connection, number):
        await connection.subscribed()
        await connection.stay()

    async def scenario():
        async with LocalCapitalStream(script) as server:
            stream = _stream(FakeCapital(streaming_host=server.host), [], silence_seconds=0.2, expects_quotes=lambda: True)
            running = asyncio.create_task(stream.run())
            await _until(lambda: len(server.received) >= 2)
            assert "nothing received" in stream.last_error
            stream.stop()
            await asyncio.wait_for(running, 3)

    _run(scenario)


def test_a_silent_stream_is_kept_while_every_market_is_closed():
    async def script(connection, number):
        await connection.subscribed()
        await connection.stay()

    async def scenario():
        async with LocalCapitalStream(script) as server:
            stream = _stream(FakeCapital(streaming_host=server.host), [], silence_seconds=0.1, expects_quotes=lambda: False)
            running = asyncio.create_task(stream.run())
            await _until(lambda: len(server.received) == 1)
            await asyncio.sleep(0.4)
            assert len(server.received) == 1 and stream.connected
            stream.stop()
            await asyncio.wait_for(running, 3)

    _run(scenario)


def test_tokens_the_stream_refuses_mean_a_new_session_before_the_next_connection():
    async def script(connection, number):
        message = await connection.next_message()
        if number == 1:
            await connection.ws.send(json.dumps({
                "status": "ERROR", "destination": "marketData.subscribe", "correlationId": message["correlationId"],
                "payload": {"errorCode": "error.invalid.session.token"},
            }))
            await connection.stay()
            return
        await connection.stay()

    async def scenario():
        async with LocalCapitalStream(script) as server:
            capital = FakeCapital(streaming_host=server.host)
            stream = _stream(capital, [])
            running = asyncio.create_task(stream.run())
            await _until(lambda: len(server.received) >= 2 and len(server.received[1]) >= 1)
            stream.stop()
            await asyncio.wait_for(running, 3)
            return server.received

    received = _run(scenario)

    assert received[0][0]["cst"] == "cst-1"
    assert received[1][0]["cst"] == "cst-2"


def test_a_refused_login_holds_the_stream_back_until_capital_com_may_be_asked_again():
    async def scenario():
        capital = FakeCapital()
        capital.refuse_login = "error.invalid.api.key"
        stream = _stream(capital, [])
        running = asyncio.create_task(stream.run())
        await _until(lambda: stream.last_error is not None)
        await asyncio.sleep(0.2)
        stream.stop()
        await asyncio.wait_for(running, 3)
        return capital, stream

    capital, stream = _run(scenario)

    # One try: the next one waits a minute.
    assert sum(1 for r in capital.requests if r.url.path == "/api/v1/session") == 1
    assert "error.invalid.api.key" in stream.last_error


def test_a_fault_of_whoever_records_does_not_end_the_stream():
    quotes = []

    def on_quote(epic, bid, ask, stamp, received_at):
        quotes.append(bid)
        if len(quotes) == 1:
            raise RuntimeError("the recorder broke")

    async def script(connection, number):
        await connection.subscribed()
        await connection.quote(1.0, 2.0)
        await connection.quote(3.0, 4.0)
        await connection.stay()

    async def scenario():
        async with LocalCapitalStream(script) as server:
            stream = CapitalStream(FakeCapital(streaming_host=server.host).rest(), ["US100"], on_quote)
            running = asyncio.create_task(stream.run())
            await _until(lambda: len(quotes) == 2)
            assert stream.connections == 1
            stream.stop()
            await asyncio.wait_for(running, 3)

    _run(scenario)
