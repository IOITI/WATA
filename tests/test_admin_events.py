import asyncio
from unittest.mock import patch

from src.admin.events import EventHub, StateWatcher


class Settings:
    log_path = "/nonexistent"
    config_path = __file__
    timezone = "Europe/Paris"


class Queries:
    def __init__(self):
        self.open = {}
        self.closed = {}
        self.journal = []
        self.paused_since = None
        self.failing = False

    async def open_positions(self):
        if self.failing:
            raise RuntimeError("database is down")
        return list(self.open.values())

    async def get_position(self, position_id):
        position = self.closed.get(position_id)
        return {"position": position, "orders": []} if position else None

    async def signal_journal(self, limit=20):
        return self.journal[:limit]

    async def entries_paused_since(self):
        return self.paused_since

    async def ping(self):
        return True


class Config:
    """A configuration with nothing optional switched on."""

    def get_config_value(self, key, default=None):
        return default


class Services:
    watchlist_enabled = False

    def __init__(self):
        self.consumers = 1

    async def latest_signals(self):
        return []

    async def queue_stats(self):
        return [
            {"name": "trading-ops", "declared": True, "messages": 0, "consumers": self.consumers},
            {"name": "telegram_channel", "declared": True, "messages": 0, "consumers": 1},
        ]


class Saxo:
    def __init__(self):
        self.expired = False

    def status(self):
        return {"has_token": True, "refresh_token_expired": self.expired}


class Ctx:
    version = "test"

    def __init__(self):
        self.settings = Settings()
        self.queries = Queries()
        self.services = Services()
        self.saxo = Saxo()
        self.config_manager = Config()


def _position(position_id, **extra):
    return {"position_id": position_id, "action": "long", "instrument_name": "TURBO", **extra}


def _drain(queue):
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    return events


def _watch(scenario):
    """Run ``scenario(ctx, watcher, look)``; ``look()`` returns the events of one look."""
    async def wrapper():
        ctx = Ctx()
        hub = EventHub()
        queue = hub.subscribe()
        watcher = StateWatcher(ctx, hub)

        async def look():
            await watcher.look()
            return _drain(queue)

        return await scenario(ctx, watcher, look)

    return asyncio.run(wrapper())


def test_first_look_announces_nothing():
    async def scenario(ctx, watcher, look):
        ctx.queries.open = {"p1": _position("p1")}
        ctx.queries.journal = [{"signal_uuid": "s1", "action": "long", "outcome": "executed"}]
        ctx.queries.paused_since = "2026-10-12"
        return await look()

    assert _watch(scenario) == []


def test_position_opening_and_closing_are_announced_with_the_result():
    async def scenario(ctx, watcher, look):
        await look()
        ctx.queries.open = {"p1": _position("p1")}
        opened = await look()
        ctx.queries.open = {}
        ctx.queries.closed = {"p1": _position(
            "p1", position_profit_loss=-42.5, position_total_performance_percent=-3.1,
            position_close_reason="Performance (Stoploss ...)",
        )}
        closed = await look()
        return opened, closed, await look()

    opened, closed, quiet = _watch(scenario)

    assert opened == [{"type": "position_opened", "position_id": "p1", "action": "long", "instrument_name": "TURBO"}]
    assert closed[0]["type"] == "position_closed"
    assert (closed[0]["profit_loss"], closed[0]["performance_percent"]) == (-42.5, -3.1)
    assert closed[0]["close_reason"] == "Performance (Stoploss ...)"
    assert quiet == []


def test_closed_position_is_announced_even_before_its_result_is_recorded():
    async def scenario(ctx, watcher, look):
        ctx.queries.open = {"p1": _position("p1")}
        await look()
        ctx.queries.open = {}
        return await look()

    closed = _watch(scenario)

    assert closed[0]["type"] == "position_closed"
    assert closed[0]["instrument_name"] == "TURBO"
    assert closed[0]["profit_loss"] is None


def test_new_signal_decisions_are_announced_oldest_first_and_once():
    async def scenario(ctx, watcher, look):
        ctx.queries.journal = [{"signal_uuid": "s1", "action": "long", "outcome": "executed"}]
        await look()
        ctx.queries.journal = [
            {"signal_uuid": "s3", "action": "close-position", "outcome": "closed", "reason": "Closed 1"},
            {"signal_uuid": "s2", "action": "short", "outcome": "refused", "reason": "Cooldown active"},
            {"signal_uuid": "s1", "action": "long", "outcome": "executed"},
        ]
        return await look(), await look()

    announced, quiet = _watch(scenario)

    assert [(event["signal_uuid"], event["outcome"]) for event in announced] == [("s2", "refused"), ("s3", "closed")]
    assert announced[0]["reason"] == "Cooldown active"
    assert quiet == []


def test_first_decision_ever_is_announced():
    async def scenario(ctx, watcher, look):
        await look()
        ctx.queries.journal = [{"signal_uuid": "s1", "action": "long", "outcome": "executed"}]
        return await look()

    assert [event["signal_uuid"] for event in _watch(scenario)] == ["s1"]


def test_pause_and_resume_are_announced():
    async def scenario(ctx, watcher, look):
        await look()
        ctx.queries.paused_since = "2026-10-12"
        paused = await look()
        ctx.queries.paused_since = None
        return paused, await look()

    paused, resumed = _watch(scenario)

    assert [event["type"] for event in paused] == ["entries_paused"]
    assert [event["type"] for event in resumed] == ["entries_resumed"]


def test_service_and_saxo_changes_are_announced_on_the_slower_round():
    async def scenario(ctx, watcher, look):
        with patch("src.admin.events.HEALTH_EVERY_N_ROUNDS", 2):
            await look()                      # round 0: health looked at, nothing to compare with
            ctx.services.consumers = 0
            ctx.saxo.expired = True
            skipped = await look()            # round 1: health not looked at
            changed = await look()            # round 2
            return skipped, changed

    skipped, changed = _watch(scenario)

    assert skipped == []
    # Saxo is in the health summary too, but is announced once, by its own event.
    assert sorted(event["type"] for event in changed) == ["saxo_authorisation_required", "service_status"]
    by_type = {event["type"]: event for event in changed}
    assert by_type["service_status"]["id"] == "position_monitor"
    assert (by_type["service_status"]["previous"], by_type["service_status"]["status"]) == ("ok", "down")
    assert "saxo_authorisation_required" in by_type


def test_a_failing_source_does_not_hide_the_others():
    async def scenario(ctx, watcher, look):
        await look()
        ctx.queries.failing = True
        ctx.queries.paused_since = "2026-10-12"
        return await look()

    assert [event["type"] for event in _watch(scenario)] == ["entries_paused"]


def test_nothing_is_watched_without_a_listener_and_the_next_one_starts_fresh():
    async def scenario():
        ctx = Ctx()
        hub = EventHub()
        watcher = StateWatcher(ctx, hub)
        looks = []

        async def counting_look():
            looks.append(1)

        watcher.look = counting_look
        task = asyncio.create_task(watcher.run(interval_seconds=0.01))
        await asyncio.sleep(0.05)
        without_listener = len(looks)

        queue = hub.subscribe()
        await asyncio.sleep(0.05)
        task.cancel()
        return without_listener, len(looks), watcher._open_positions, queue

    without_listener, with_listener, baseline, _ = asyncio.run(scenario())

    assert without_listener == 0
    assert with_listener > 0
    assert baseline is None


def test_a_change_right_after_a_browser_connects_is_announced():
    async def scenario():
        ctx = Ctx()
        ctx.queries.open = {"p1": _position("p1")}
        hub = EventHub()
        task = asyncio.create_task(StateWatcher(ctx, hub).run(interval_seconds=0.05))
        await asyncio.sleep(0.01)

        queue = hub.subscribe()
        # The watcher takes its first look as the browser connects, not at its next round.
        await asyncio.sleep(0.01)
        ctx.queries.open = {}
        await asyncio.sleep(0.12)
        task.cancel()
        return _drain(queue)

    events = asyncio.run(scenario())

    assert [event["type"] for event in events] == ["position_closed"]


def test_watcher_survives_an_unexpected_error():
    async def scenario():
        hub = EventHub()
        watcher = StateWatcher(Ctx(), hub)
        looks = []

        async def failing_look():
            looks.append(1)
            raise RuntimeError("unexpected")

        watcher.look = failing_look
        task = asyncio.create_task(watcher.run(interval_seconds=0.01))
        hub.subscribe()
        await asyncio.sleep(0.06)
        task.cancel()
        return len(looks)

    assert asyncio.run(scenario()) > 1


def test_slow_listener_does_not_block_the_others():
    hub = EventHub()

    async def scenario():
        slow = hub.subscribe()
        fast = hub.subscribe()
        for index in range(150):
            hub.publish("tick", index=index)
            if not fast.empty():
                fast.get_nowait()
        return slow.qsize(), fast.qsize()

    slow_size, fast_size = asyncio.run(scenario())

    assert slow_size == 100
    assert fast_size == 0
    hub_empty = EventHub()
    hub_empty.publish("nobody listening")


# ── Saxo refusing to sell an open position ──

LIMIT_ONLY = "À ce stade, seuls des ordres limites sont autorisés"


def _refused(count):
    return {"count": count, "outcome": "refused", "http_status": 400, "error_code": "OtherError",
            "error_message": LIMIT_ONLY, "last_at": f"14:0{count % 10}"}


def test_refused_sells_are_announced_at_most_once_a_minute_with_the_count_since():
    async def scenario(ctx, watcher, look):
        now = [1000.0]
        watcher._clock = lambda: now[0]
        ctx.queries.open = {"p1": _position("p1", sell_refusals=_refused(3))}
        first = await look()
        ctx.queries.open = {"p1": _position("p1", sell_refusals=_refused(5))}
        announced = await look()
        ctx.queries.open = {"p1": _position("p1", sell_refusals=_refused(9))}
        now[0] += 30
        held_back = await look()
        now[0] += 31
        announced_again = await look()
        return first, announced, held_back, announced_again, await look()

    first, announced, held_back, announced_again, quiet = _watch(scenario)

    # Refused before the page opened: not news to it.
    assert first == []
    assert announced == [{
        "type": "order_refused", "position_id": "p1", "action": "long", "instrument_name": "TURBO",
        "purpose": "close", "count": 2, "total": 5, "outcome": "refused", "http_status": 400,
        "error_code": "OtherError", "error_message": LIMIT_ONLY, "last_at": "14:05",
    }]
    assert held_back == []
    assert [(event["count"], event["total"]) for event in announced_again] == [(4, 9)]
    assert quiet == []


def test_a_position_that_opens_already_refused_is_announced():
    async def scenario(ctx, watcher, look):
        await look()
        ctx.queries.open = {"p2": _position("p2", sell_refusals=_refused(1))}
        return await look()

    events = _watch(scenario)

    assert [event["type"] for event in events] == ["order_refused", "position_opened"]
    assert events[0]["count"] == 1


def test_a_position_sold_at_last_is_forgotten():
    async def scenario(ctx, watcher, look):
        ctx.queries.open = {"p1": _position("p1", sell_refusals=_refused(2))}
        await look()
        ctx.queries.open = {}
        await look()
        return dict(watcher._refusals_told), dict(watcher._refusals_told_at)

    assert _watch(scenario) == ({}, {})
