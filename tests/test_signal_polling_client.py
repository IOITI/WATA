import asyncio

import httpx
from unittest.mock import MagicMock, patch

from src.trade.signal_polling_client import SignalPollingClient


def _client(handler, **config) -> SignalPollingClient:
    config_manager = MagicMock()
    config_manager.get_config_value.return_value = {"service_url": "http://web_server1:80", **config}
    with patch("src.trade.signal_polling_client.WebServerToken") as token_cls:
        token_cls.return_value.get_token.return_value = "token"
        client = SignalPollingClient(config_manager)
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client


def test_get_new_signals_sends_and_advances_the_cursor():
    requests = []
    answers = iter([[{"signal_uuid": "a", "seq": 3}], [{"signal_uuid": "b", "seq": 4}]])

    def handler(request):
        requests.append(dict(request.url.params))
        return httpx.Response(200, json=next(answers))

    client = _client(handler, long_poll_wait_ms=1000)

    async def scenario():
        return await client.get_new_signals(), await client.get_new_signals()

    first, second = asyncio.run(scenario())

    assert requests == [{"after": "0", "wait_ms": "1000"}, {"after": "3", "wait_ms": "1000"}]
    assert first == [{"signal_uuid": "a", "seq": 3}]
    assert second == [{"signal_uuid": "b", "seq": 4}]


def test_cursor_follows_the_web_server_when_its_seq_restarts():
    requests = []
    answers = iter([[{"signal_uuid": "a", "seq": 57}], [{"signal_uuid": "b", "seq": 1}], [{"signal_uuid": "c", "seq": 2}]])

    def handler(request):
        requests.append(request.url.params["after"])
        return httpx.Response(200, json=next(answers))

    client = _client(handler, long_poll_wait_ms=1000)

    async def scenario():
        for _ in range(3):
            await client.get_new_signals()

    asyncio.run(scenario())

    assert requests == ["0", "57", "1"]


def test_falls_back_to_interval_polling_when_signals_carry_no_seq():
    client = _client(lambda request: httpx.Response(200, json=[{"signal_uuid": "a"}]), long_poll_wait_ms=1000)

    signals = asyncio.run(client.get_new_signals())

    assert signals == [{"signal_uuid": "a"}]
    assert client.long_poll_wait_ms == 0


def test_falls_back_to_interval_polling_when_an_empty_answer_comes_back_at_once():
    client = _client(lambda request: httpx.Response(200, json=[]), long_poll_wait_ms=1000)

    signals = asyncio.run(client.get_new_signals())

    assert signals == []
    assert client.long_poll_wait_ms == 0


def test_keeps_long_polling_when_an_empty_answer_comes_back_after_the_wait():
    async def handler(request):
        await asyncio.sleep(0.08)
        return httpx.Response(200, json=[])

    client = _client(handler, long_poll_wait_ms=100)

    signals = asyncio.run(client.get_new_signals())

    assert signals == []
    assert client.long_poll_wait_ms == 100


def test_http_timeout_leaves_room_for_the_long_poll_wait():
    config_manager = MagicMock()
    config_manager.get_config_value.return_value = {"long_poll_wait_ms": 1500, "request_timeout_seconds": 2}
    with patch("src.trade.signal_polling_client.WebServerToken") as token_cls:
        token_cls.return_value.get_token.return_value = "token"
        client = SignalPollingClient(config_manager)

    async def scenario():
        await client.start()
        timeout = client._client.timeout
        await client.close()
        return timeout

    assert asyncio.run(scenario()).read == 3.5
