"""
The TA engine's sender (src/ta_engine/sender.py): a signal posted to the web
server under its key, sent again when it got no answer, never past the
engine's patience; the token read before the first signal.
"""

import asyncio
import json

import httpx
import pytest

from src.ta_engine.sender import RETRY_PAUSES, SIGNAL_KEY_HEADER, SignalSender

BODY = {"action": "long", "indice": "us100", "signal_timestamp": "2026-11-23T08:38:10Z", "alert_timestamp": "2026-11-23T08:38:10Z"}
TAKEN = {"status": "success", "signal_uuid": "5e1f2a3b-0000-4000-8000-000000000001"}


class WebServer:
    """The web server as the sender meets it: each request answered by the next of ``answers`` (an exception is raised)."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        status, body = answer
        return httpx.Response(status, json=body) if isinstance(body, dict) else httpx.Response(status, text=body)


def sender_for(server, *, tokens=("t1",), timeout=3):
    read, paused = [], []
    given = list(tokens)

    async def token():
        read.append(len(read))
        found = given.pop(0) if len(given) > 1 else given[0]
        if isinstance(found, Exception):
            raise found
        return found

    async def sleep(seconds):
        paused.append(seconds)

    http = httpx.AsyncClient(transport=httpx.MockTransport(server))
    return SignalSender("http://web_server1:80/", token, http, timeout, sleep=sleep), read, paused


def test_a_signal_is_posted_with_the_token_under_its_key():
    server = WebServer((200, TAKEN))
    sender, read, paused = sender_for(server)

    sent = asyncio.run(sender.send(BODY, key="f13986be5e9b:794"))

    assert (sent.ok, sent.http_status, sent.signal_uuid, sent.attempts) == (True, 200, TAKEN["signal_uuid"], 1)
    [request] = server.requests
    assert (request.method, request.url.host, request.url.path) == ("POST", "web_server1", "/internal/signal")
    assert json.loads(request.content) == BODY
    assert request.headers["Authorization"] == "Bearer t1" and request.headers[SIGNAL_KEY_HEADER] == "f13986be5e9b:794"
    assert paused == []


def test_a_signal_that_got_no_answer_is_sent_again_under_the_same_key():
    # The web server starting again: refused connections, then one that breaks before the answer, then it takes it.
    server = WebServer(httpx.ConnectError("All connection attempts failed"), httpx.ConnectError("All connection attempts failed"),
                       httpx.RemoteProtocolError("Server disconnected without sending a response."), (200, TAKEN))
    sender, read, paused = sender_for(server)

    sent = asyncio.run(sender.send(BODY, key="f13986be5e9b:794"))

    assert (sent.ok, sent.attempts, sent.signal_uuid) == (True, 4, TAKEN["signal_uuid"])
    assert paused == list(RETRY_PAUSES[:3])
    # The same key each time: the web server stores it once, whether or not it took an earlier one.
    assert {request.headers[SIGNAL_KEY_HEADER] for request in server.requests} == {"f13986be5e9b:794"}
    assert len(read) == 1


def test_a_web_server_that_stays_away_is_given_up_after_a_few_seconds():
    server = WebServer(httpx.ConnectError("All connection attempts failed"))
    sender, read, paused = sender_for(server)

    sent = asyncio.run(sender.send(BODY, key="k"))

    assert (sent.ok, sent.http_status, sent.attempts) == (False, None, len(RETRY_PAUSES) + 1)
    assert sent.error == "ConnectError: All connection attempts failed"
    assert paused == list(RETRY_PAUSES) and sum(RETRY_PAUSES) == 5


@pytest.mark.parametrize("patience, pauses", [(0.0, []), (0.3, [0.25]), (1.2, [0.25, 0.75]), (60.0, list(RETRY_PAUSES))])
def test_a_signal_is_not_sent_again_past_the_engine_s_patience(patience, pauses):
    server = WebServer(httpx.ReadError("Connection reset by peer"))
    sender, read, paused = sender_for(server)

    sent = asyncio.run(sender.send(BODY, key="k", patience=patience))

    # A bar about to be too old for a signal: what would arrive late is not sent.
    assert not sent.ok and paused == pauses and sent.attempts == len(pauses) + 1


def test_an_answer_is_final_and_so_is_a_request_that_timed_out():
    for answer, status, error in (
        ((503, "unavailable"), 503, "unavailable"),
        ((400, "Bad Request"), 400, "Bad Request"),
        (httpx.ReadTimeout("timed out"), None, "ReadTimeout: timed out"),
    ):
        server = WebServer(answer)
        sender, read, paused = sender_for(server)

        sent = asyncio.run(sender.send(BODY, key="k"))

        assert (sent.ok, sent.http_status, sent.error, sent.attempts) == (False, status, error, 1)
        assert paused == [] and len(server.requests) == 1


def test_a_refused_token_is_read_again_once():
    server = WebServer((401, {"detail": "Unauthorized"}), (200, TAKEN))
    sender, read, paused = sender_for(server, tokens=("old", "new"))

    sent = asyncio.run(sender.send(BODY, key="k"))

    assert sent.ok and sent.attempts == 2 and len(read) == 2
    assert [request.headers["Authorization"] for request in server.requests] == ["Bearer old", "Bearer new"]

    refused = WebServer((401, {"detail": "Unauthorized"}))
    sender, read, paused = sender_for(refused, tokens=("old", "new"))
    sent = asyncio.run(sender.send(BODY, key="k"))
    assert (sent.ok, sent.http_status, sent.error) == (False, 401, "The web server refused the token")
    assert len(refused.requests) == 2 and paused == []


def test_the_token_is_read_ahead_of_the_first_signal_and_once():
    server = WebServer((200, TAKEN))
    sender, read, paused = sender_for(server)

    async def prepared_then_sent():
        first = await sender.prepare()
        again = await sender.prepare()
        return first, again, await sender.send(BODY, key="k")

    first, again, sent = asyncio.run(prepared_then_sent())

    assert first is None and again is None and sent.ok
    # Read when the engine prepared it, not when the signal was raised.
    assert len(read) == 1


def test_a_token_that_cannot_be_read_is_said_and_read_again_at_the_signal():
    server = WebServer((200, TAKEN))
    sender, read, paused = sender_for(server, tokens=(RuntimeError("no row"), "t1"))

    async def prepared_then_sent():
        return await sender.prepare(), await sender.send(BODY, key="k")

    trouble, sent = asyncio.run(prepared_then_sent())

    assert trouble == "The web server's token could not be read: RuntimeError"
    assert sent.ok and len(read) == 2


def test_a_signal_without_a_token_is_not_sent():
    server = WebServer((200, TAKEN))
    sender, read, paused = sender_for(server, tokens=(RuntimeError("no row"),))

    sent = asyncio.run(sender.send(BODY, key="k"))

    assert (sent.ok, sent.error) == (False, "The web server's token could not be read: RuntimeError")
    assert server.requests == []


# ── warm: a signal's way rehearsed between signals ──

def test_a_rehearsal_is_posted_to_the_web_server_s_warm_endpoint_with_the_token():
    server = WebServer((200, {"status": "warm"}))
    sender, read, paused = sender_for(server)
    asyncio.run(sender.prepare())

    assert asyncio.run(sender.warm(BODY)) is True

    [request] = server.requests
    assert (request.method, request.url.host, request.url.path) == ("POST", "web_server1", "/internal/warm")
    assert json.loads(request.content) == BODY and request.headers["Authorization"] == "Bearer t1"
    # Never under a key: nothing of it is to be stored.
    assert SIGNAL_KEY_HEADER not in request.headers and paused == []


def test_a_rehearsal_never_reads_the_token_and_is_not_sent_without_one():
    server = WebServer((200, {"status": "warm"}))
    sender, read, paused = sender_for(server)

    assert asyncio.run(sender.warm(BODY)) is False
    assert server.requests == [] and read == []


@pytest.mark.parametrize("answer", [
    httpx.ConnectError("All connection attempts failed"),
    httpx.RemoteProtocolError("Server disconnected without sending a response."),
    httpx.ReadTimeout("timed out"),
    RuntimeError("anything else"),
    (500, "Internal Server Error"),
    (404, "Not Found"),
])
def test_a_rehearsal_that_fails_is_neither_raised_nor_sent_again(answer):
    server = WebServer(answer)
    sender, read, paused = sender_for(server)
    asyncio.run(sender.prepare())

    assert asyncio.run(sender.warm(BODY)) is False
    assert len(server.requests) == 1 and paused == []
    # The signal after it goes as any other: the token is still held.
    assert len(read) == 1


def test_a_rehearsal_refused_for_its_token_has_it_read_again_before_a_signal_needs_it():
    server = WebServer((401, "Unauthorized"), (200, {"status": "warm"}))
    sender, read, paused = sender_for(server, tokens=("rotated-away", "t2"))
    asyncio.run(sender.prepare())

    assert asyncio.run(sender.warm(BODY)) is False
    # The engine prepares at the end of each tick while a rule is live.
    asyncio.run(sender.prepare())
    assert asyncio.run(sender.warm(BODY)) is True

    assert len(read) == 2 and server.requests[-1].headers["Authorization"] == "Bearer t2"


def test_the_signals_client_keeps_its_connection_from_one_rehearsal_to_the_next():
    from src.ta_engine.engine import REHEARSE_EVERY_SECONDS
    from src.ta_engine.sender import KEEP_ALIVE_SECONDS, client

    http = client()
    try:
        kept = http._transport._pool._keepalive_expiry
    finally:
        asyncio.run(http.aclose())

    assert kept == KEEP_ALIVE_SECONDS > REHEARSE_EVERY_SECONDS
