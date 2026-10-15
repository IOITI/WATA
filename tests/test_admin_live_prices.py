import asyncio
import json
import time

import pytest

from src.admin.live_prices import IDLE_CLOSE_SECONDS, SUBSCRIBER_QUEUE_SIZE, LivePrices
from tests.test_admin_api import (  # noqa: F401 (fixtures)
    RunningAdmin,
    _eventually,
    anonymous,
    client,
    config_path,
    ctx,
    make_ctx,
)

REF = "capital/US100"


def _quote(bid, ref=REF):
    return {"type": "quote", "ref": ref, "t": 1, "rx": 2, "bid": bid, "ask": bid + 1, "bar": [0, bid, bid, bid, bid, bid + 1, bid + 1, bid + 1, bid + 1, 1]}


class Clock:
    def __init__(self):
        self.at = 1000.0

    def __call__(self):
        return self.at


class Source:
    """Stands for RabbitMQ: counts the connections, and hands messages over as the real one does."""

    def __init__(self):
        self.opened = 0
        self.closed = 0
        self.failing: Exception | None = None
        self.on_message = None

    async def __call__(self, on_message):
        if self.failing:
            raise self.failing
        self.opened += 1
        self.on_message = on_message
        return self

    async def close(self):
        self.closed += 1


def test_each_message_reaches_those_who_watch_its_asset_and_the_last_quote_is_kept_for_the_next():
    async def scenario():
        prices = LivePrices(Source())
        watching, other = prices.subscribe(REF), prices.subscribe("capital/US500")
        prices.dispatch(_quote(25000.0))
        prices.dispatch({"type": "state", "ref": REF, "market": "closed"})
        prices.dispatch({"type": "order", "ref": REF})
        assert [watching.get_nowait()["type"], watching.get_nowait()["type"]] == ["quote", "state"]
        assert watching.empty() and other.empty()
        snapshot = prices.snapshot(REF)
        assert snapshot["quote"]["bid"] == 25000.0 and snapshot["state"]["market"] == "closed"
        assert snapshot["live"] is False and "Connecting" in snapshot["detail"]

    asyncio.run(scenario())


def test_a_quote_that_does_not_fit_is_dropped_and_a_bar_empties_the_queue_for_a_resync():
    async def scenario():
        prices = LivePrices(Source())
        queue = prices.subscribe(REF)
        for index in range(SUBSCRIBER_QUEUE_SIZE + 5):
            prices.dispatch(_quote(25000.0 + index))
        assert queue.qsize() == SUBSCRIBER_QUEUE_SIZE
        prices.dispatch({"type": "bar", "ref": REF, "bar": [0] * 10})
        assert queue.qsize() == 1 and queue.get_nowait() == {"type": "resync", "ref": REF}

    asyncio.run(scenario())


def test_rabbitmq_is_listened_to_only_while_a_chart_is_and_let_go_a_minute_after_the_last_one():
    async def scenario():
        source, clock = Source(), Clock()
        prices = LivePrices(source, clock=clock)
        running = asyncio.create_task(prices.run())
        await asyncio.sleep(0.05)
        assert source.opened == 0
        queue = prices.subscribe(REF)
        for _ in range(100):
            if prices.connected:
                break
            await asyncio.sleep(0.01)
        assert source.opened == 1 and prices.connected
        # The chart is told it is live.
        assert queue.get_nowait() == {"type": "live", "ref": REF, "live": True, "detail": None}
        prices.unsubscribe(REF, queue)
        clock.at += IDLE_CLOSE_SECONDS + 1
        for _ in range(300):
            if source.closed:
                break
            await asyncio.sleep(0.01)
        assert source.closed == 1 and not prices.connected
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)

    asyncio.run(scenario())


def test_rabbitmq_out_of_reach_is_said_to_the_charts_and_tried_again():
    async def scenario():
        source = Source()
        source.failing = ConnectionError("rabbitmq1 refused")
        prices = LivePrices(source)
        queue = prices.subscribe(REF)
        running = asyncio.create_task(prices.run())
        message = await asyncio.wait_for(queue.get(), 2)
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        return message, prices

    message, prices = asyncio.run(scenario())

    assert message == {"type": "live", "ref": REF, "live": False, "detail": "Live prices unavailable (ConnectionError)"}
    assert prices.snapshot(REF)["detail"] == "Live prices unavailable (ConnectionError)"


# ── the stream of an asset, on a real socket ──


class LoopSource(Source):
    """The source, keeping the loop of the server so that the test can hand it a message as RabbitMQ would."""

    async def __call__(self, on_message):
        self.loop = asyncio.get_running_loop()
        return await super().__call__(on_message)

    def send(self, message):
        self.loop.call_soon_threadsafe(self.on_message, message)


@pytest.fixture
def live_ctx(config_path, tmp_path):
    ctx = make_ctx(config_path, tmp_path, cookie_secure=False)
    ctx.live_prices = LivePrices(LoopSource())
    return ctx


def _events(lines):
    """(event, data) pairs from the lines of a stream."""
    kind = None
    for line in lines:
        if line.startswith("event: "):
            kind = line[len("event: "):]
        elif line.startswith("data: ") and kind is not None:
            yield kind, json.loads(line[len("data: "):])
            kind = None


def test_a_quote_reaches_the_browser_at_once_with_the_times_of_each_hop(live_ctx, tmp_path):
    from unittest.mock import patch

    ctx, source = live_ctx, live_ctx.live_prices._source
    with patch("src.admin.routers.data.LIVE_POLL_SECONDS", 0.05), \
            RunningAdmin(ctx, tmp_path / "no-interface") as admin, admin.signed_in_client() as client:
        with client.stream("GET", "/api/data/assets/capital/US100/live") as stream:
            assert stream.headers["content-type"].startswith("text/event-stream")
            assert stream.headers["x-accel-buffering"] == "no"
            events = _events(stream.iter_lines())
            kind, snapshot = next(events)
            assert kind == "snapshot" and abs(snapshot["server_ms"] - time.time() * 1000) < 5000
            assert next(events) == ("live", {"type": "live", "ref": REF, "live": True, "detail": None, "sx": pytest.approx(time.time() * 1000, abs=5000)})
            sent = time.monotonic()
            source.send(_quote(25000.5))
            kind, quote = next(events)
            took = time.monotonic() - sent
    print(f"from the hub to this client: {took * 1000:.1f} ms")
    assert kind == "quote" and quote["bid"] == 25000.5 and quote["rx"] == 2 and "sx" in quote
    # Nothing waits on the way: well under the time between two quotes.
    assert took < 0.25
    assert _eventually(lambda: ctx.live_prices.listeners == 0), "the closed stream is still subscribed"


def test_only_capital_coms_assets_are_followed_live(client, ctx):
    assert client.get("/api/data/assets/saxo/2/live").status_code == 404
    assert client.get("/api/data/assets/capital/us100/live").status_code == 404
    # Without the hub (the dev server's or a test's context), the stream is not offered.
    assert client.get("/api/data/assets/capital/US100/live").status_code == 503
