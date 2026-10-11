import asyncio
import os

# Importing anything under src.web_server loads the app, which needs a config
os.environ["WATA_CONFIG_PATH"] = "tests/test_config.json"

from src.web_server.signal_store import SignalStore


def _signal(indice: str, signal_timestamp: str, action: str = "long") -> dict:
    return {
        "action": action,
        "indice": indice,
        "signal_uuid": f"{indice}-{signal_timestamp}-{action}",
        "signal_timestamp": signal_timestamp,
        "alert_timestamp": signal_timestamp,
    }


def test_upsert_stamps_increasing_seq():
    async def scenario():
        store = SignalStore()
        await store.upsert(_signal("us100", "2026-07-16T08:00:00Z"))
        await store.upsert(_signal("dax", "2026-07-16T08:00:01Z"))
        return await store.get_all()

    signals = asyncio.run(scenario())

    assert [signal["seq"] for signal in signals] == [1, 2]


def test_ignored_older_signal_does_not_advance_seq():
    async def scenario():
        store = SignalStore()
        await store.upsert(_signal("us100", "2026-07-16T08:00:10Z"))
        await store.upsert(_signal("us100", "2026-07-16T08:00:00Z", action="short"))
        return await store.get_newer(0)

    signals = asyncio.run(scenario())

    assert len(signals) == 1
    assert signals[0]["action"] == "long"
    assert signals[0]["seq"] == 1


def test_get_newer_returns_only_signals_after_cursor():
    async def scenario():
        store = SignalStore()
        await store.upsert(_signal("us100", "2026-07-16T08:00:00Z"))
        await store.upsert(_signal("dax", "2026-07-16T08:00:01Z"))
        return await store.get_newer(1), await store.get_newer(2)

    after_first, after_second = asyncio.run(scenario())

    assert [signal["indice"] for signal in after_first] == ["dax"]
    assert after_second == []


def test_get_newer_wakes_up_as_soon_as_a_signal_is_stored():
    async def scenario():
        store = SignalStore()
        waiter = asyncio.create_task(store.get_newer(0, timeout=5))
        await asyncio.sleep(0)  # let the waiter start waiting
        assert not waiter.done()

        await store.upsert(_signal("us100", "2026-07-16T08:00:00Z"))
        return await asyncio.wait_for(waiter, timeout=1)

    signals = asyncio.run(scenario())

    assert [signal["indice"] for signal in signals] == ["us100"]


def test_get_newer_returns_empty_after_timeout():
    async def scenario():
        store = SignalStore()
        await store.upsert(_signal("us100", "2026-07-16T08:00:00Z"))
        return await store.get_newer(1, timeout=0.05)

    assert asyncio.run(scenario()) == []


def test_get_newer_with_cursor_from_before_a_restart_returns_everything():
    async def scenario():
        store = SignalStore()  # fresh store: its seq restarted from 0
        await store.upsert(_signal("us100", "2026-07-16T08:00:00Z"))
        return await store.get_newer(57, timeout=5)

    signals = asyncio.run(scenario())

    assert [signal["seq"] for signal in signals] == [1]
