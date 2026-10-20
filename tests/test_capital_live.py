import asyncio
import json
import random
import sys
import types

import pytest

from src.capital_feed.live import STALE_SECONDS, LiveBar, LivePublisher, RabbitExchange, bar_row, routing_key
from src.price_recorder.bars import BAR_SECONDS, BarBuilder, Quote, bucket_start

T0 = 1792051200  # Thursday 15 Oct 2026, 08:00:00 UTC: a bucket starts there.


def _quotes(seed: int, count: int = 400):
    """Quotes of one instrument at increasing times, a few repeated, a few stopping it, sometimes a pause."""
    rng = random.Random(seed)
    at, bid = float(T0), 25000.0
    for _ in range(count):
        at += rng.choice((0.05, 0.3, 1.2, 4.0, 9.9, 0.01))
        roll = rng.random()
        if roll < 0.05:
            yield at, None
            continue
        if roll > 0.85:
            # The same quote again: not a change.
            yield at, Quote(bid=bid, ask=bid + 1.0)
            continue
        bid = round(bid + rng.choice((-0.5, -0.25, 0.25, 0.5, 1.0)), 2)
        yield at, Quote(bid=bid, ask=bid + rng.choice((0.8, 1.0, 1.2)))


@pytest.mark.parametrize("seed", range(25))
def test_the_bar_the_chart_sees_forming_is_the_bar_recorded_once_its_ten_seconds_end(seed):
    builder, live = BarBuilder(), LiveBar("US100")
    seen: dict[int, list] = {}
    for at, quote in _quotes(seed):
        builder.update("US100", quote, at)
        bar = live.update(quote, at)
        if bar is not None:
            seen[bar.start] = bar_row(bar)
        # The chart's last word on a bucket that ended without a quote is the quote that stood through it.
        current = live.current(at)
        if current is not None:
            seen.setdefault(current.start, bar_row(current))

    recorded = {}
    for bucket in builder.close_until(at + BAR_SECONDS):
        for bar in bucket.bars:
            recorded[bar.start] = bar_row(bar)

    # Every bar the chart was shown is the bar recorded for those ten seconds.
    compared = [start for start in recorded if start in seen]
    assert len(compared) > 50, "the sequence compared too few buckets to say anything"
    for start in compared:
        assert seen[start] == recorded[start], f"bucket {start - T0}"


def test_a_quiet_ten_seconds_shows_the_quote_that_stood_through_them():
    live = LiveBar("US100")
    live.update(Quote(bid=25000.0, ask=25001.0), T0 + 1)

    quiet = live.current(T0 + 15)

    assert bar_row(quiet) == [T0 + 10, 25000.0, 25000.0, 25000.0, 25000.0, 25001.0, 25001.0, 25001.0, 25001.0, 0]


def test_a_market_that_closes_ends_its_bars():
    live = LiveBar("US100")
    live.update(Quote(bid=25000.0, ask=25001.0), T0 + 1)
    live.update(None, T0 + 12)

    assert live.current(T0 + 25) is None


def test_a_bar_travels_in_the_columns_of_the_admin_charts():
    live = LiveBar("US100")
    bar = live.update(Quote(bid=25000.0, ask=25001.0), T0 + 3)
    bar = live.update(Quote(bid=25002.5, ask=25003.5), T0 + 4)

    assert bar_row(bar) == [bucket_start(T0 + 3), 25000.0, 25002.5, 25000.0, 25002.5, 25001.0, 25003.5, 25001.0, 25003.5, 2]


# ── the publisher ──


class Exchange:
    def __init__(self):
        self.published: list[tuple[str, dict]] = []
        self.failing: Exception | None = None
        self.opened = 0

    async def open(self):
        self.opened += 1
        if self.failing:
            raise self.failing
        return self

    async def publish(self, body: bytes, key: str):
        if self.failing:
            raise self.failing
        self.published.append((key, json.loads(body)))


class Clock:
    def __init__(self, at=float(T0)):
        self.at = at

    def __call__(self):
        return self.at


async def _drain(publisher: LivePublisher, until):
    running = asyncio.create_task(publisher.run())
    try:
        for _ in range(200):
            if until():
                return
            await asyncio.sleep(0.005)
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)


def test_quotes_bars_and_states_go_out_under_their_routing_key():
    exchange, clock = Exchange(), Clock()
    live = LiveBar("US100")

    async def scenario():
        publisher = LivePublisher(exchange.open, clock=clock)
        bar = live.update(Quote(bid=25000.0, ask=25001.0), T0 + 0.25)
        publisher.quote("US100", bid=25000.0, ask=25001.0, quoted_ms=(T0 * 1000) + 200, received_at=T0 + 0.25, bar=bar)
        publisher.bar("US100", bar)
        publisher.state("US100", market="open", status="TRADEABLE")
        await _drain(publisher, lambda: len(exchange.published) == 3)
        return publisher

    publisher = asyncio.run(scenario())

    (quote_key, quote), (bar_key, bar), (state_key, state) = exchange.published
    assert (quote_key, bar_key, state_key) == (routing_key("US100", "quote"), "capital.US100.bar", "capital.US100.state")
    assert quote == {
        "type": "quote", "ref": "capital/US100", "t": T0 * 1000 + 200, "rx": T0 * 1000 + 250,
        "bid": 25000.0, "ask": 25001.0, "bar": [T0, 25000.0, 25000.0, 25000.0, 25000.0, 25001.0, 25001.0, 25001.0, 25001.0, 1],
    }
    assert bar["type"] == "bar" and bar["bar"][0] == T0
    assert state == {"type": "state", "ref": "capital/US100", "market": "open", "status": "TRADEABLE"}
    assert publisher.sent == 3 and publisher.dropped == 0


def test_putting_a_message_never_waits_and_what_does_not_fit_is_dropped():
    async def scenario():
        publisher = LivePublisher(Exchange().open, queue_size=2)
        for _ in range(5):
            publisher.state("US100", market="open")
        return publisher

    publisher = asyncio.run(scenario())

    assert publisher.dropped == 3


def test_a_message_that_waited_too_long_is_dropped_rather_than_sent_late():
    exchange, clock = Exchange(), Clock()

    async def scenario():
        publisher = LivePublisher(exchange.open, clock=clock)
        publisher.state("US100", market="open")
        clock.at += STALE_SECONDS + 1
        publisher.state("US100", market="closed")
        await _drain(publisher, lambda: publisher.sent + publisher.dropped == 2)
        return publisher

    publisher = asyncio.run(scenario())

    assert [message["market"] for _, message in exchange.published] == ["closed"]
    assert publisher.dropped == 1


def test_while_rabbitmq_is_away_nothing_waits_for_it_and_it_is_tried_again_later(caplog):
    exchange, clock = Exchange(), Clock()
    exchange.failing = ConnectionError("rabbitmq1 refused")

    async def scenario():
        publisher = LivePublisher(exchange.open, clock=clock, retry_seconds=5)
        publisher.state("US100", market="open")
        await _drain(publisher, lambda: publisher.failing)
        # Meanwhile, nothing is even queued.
        publisher.state("US100", market="open")
        assert publisher.dropped == 2
        exchange.failing = None
        clock.at += 6
        publisher.state("US100", market="closed")
        await _drain(publisher, lambda: publisher.sent == 1)
        return publisher

    publisher = asyncio.run(scenario())

    assert publisher.failing is False and exchange.opened == 2
    assert [message["market"] for _, message in exchange.published] == ["closed"]
    assert sum("cannot be sent to RabbitMQ" in record.getMessage() for record in caplog.records) == 1


def test_a_price_no_chart_listens_to_is_dropped_by_rabbitmq_not_sent_back(monkeypatch):
    # aio-pika asks for an unrouted message back unless told otherwise; nothing reads those, and
    # aiormq logged each as an error, several a second, while no chart followed the prices.
    published = []

    class Message:
        def __init__(self, **fields):
            self.fields = fields

    class Exchange:
        async def publish(self, message, routing_key, **options):
            published.append((message.fields, routing_key, options))

    fake = types.ModuleType("aio_pika")
    fake.Message = Message
    fake.DeliveryMode = types.SimpleNamespace(NOT_PERSISTENT="not persistent")
    monkeypatch.setitem(sys.modules, "aio_pika", fake)
    exchange = RabbitExchange("amqp://guest:guest@localhost/")
    exchange._exchange = Exchange()

    asyncio.run(exchange.publish(b"{}", "capital.US100.quote"))

    ((fields, key, options),) = published
    assert key == "capital.US100.quote" and options == {"mandatory": False}
    assert fields["delivery_mode"] == "not persistent" and fields["body"] == b"{}"
