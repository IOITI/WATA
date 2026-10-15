import asyncio

import httpx
from unittest.mock import MagicMock, patch

from src.trade.watchlist_client import AsyncWatchlistClient

BEST_TURBO = {"selected_instrument": {"uic": 101}, "watchlist": {"stale": False}}


def _run(handler, scenario, **config):
    """Run ``scenario(client)`` against a fake watchlist manager; return (result, http clients created)."""
    config_manager = MagicMock()
    config_manager.get_config_value.return_value = {"service_url": "http://watchlist_manager1:8081", **config}
    created = []
    real_async_client = httpx.AsyncClient

    def make_client(**kwargs):
        client = real_async_client(transport=httpx.MockTransport(handler), **kwargs)
        created.append(client)
        return client

    async def run():
        client = AsyncWatchlistClient(config_manager)
        try:
            return await scenario(client)
        finally:
            await client.close()

    with patch("src.trade.watchlist_client.httpx.AsyncClient", side_effect=make_client):
        return asyncio.run(run()), created


def test_lookups_share_one_http_client():
    requests = []

    def handler(request):
        requests.append(dict(request.url.params))
        return httpx.Response(200, json=BEST_TURBO)

    async def scenario(client):
        first = await client.get_best_turbo(direction="long", underlying_uic="1909050")
        second = await client.get_best_turbo(direction="short", underlying_uic="1909050")
        return first, second

    (first, second), created = _run(handler, scenario)

    assert first == BEST_TURBO and second == BEST_TURBO
    assert requests == [
        {"direction": "long", "underlying_uic": "1909050"},
        {"direction": "short", "underlying_uic": "1909050"},
    ]
    assert len(created) == 1
    assert created[0].is_closed


def test_lookup_failure_returns_none_and_the_next_lookup_still_works():
    answers = iter([httpx.ConnectError("watchlist manager down"), httpx.Response(200, json=BEST_TURBO)])

    def handler(request):
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def scenario(client):
        failed = await client.get_best_turbo(direction="long", underlying_uic="1909050")
        recovered = await client.get_best_turbo(direction="long", underlying_uic="1909050")
        return failed, recovered

    (failed, recovered), created = _run(handler, scenario)

    assert failed is None
    assert recovered == BEST_TURBO
    assert len(created) == 1


def test_missing_and_stale_entries_return_none():
    stale = {"selected_instrument": {"uic": 101}, "watchlist": {"stale": True}}
    answers = iter([httpx.Response(404, json={"detail": "No cached turbo"}), httpx.Response(200, json=stale)])

    async def scenario(client):
        missing = await client.get_best_turbo(direction="long", underlying_uic="1909050")
        ignored_stale = await client.get_best_turbo(direction="long", underlying_uic="1909050")
        return missing, ignored_stale

    (missing, ignored_stale), _ = _run(lambda request: next(answers), scenario)

    assert missing is None
    assert ignored_stale is None


def test_disabled_client_makes_no_request():
    async def scenario(client):
        return await client.get_best_turbo(direction="long", underlying_uic="1909050")

    result, created = _run(lambda request: httpx.Response(200, json=BEST_TURBO), scenario, enabled=False)

    assert result is None
    assert created == []
