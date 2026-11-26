import asyncio
import os

# Importing anything under src.web_server loads the app, which needs a config
os.environ["WATA_CONFIG_PATH"] = "tests/test_config.json"

from src.web_server.signal_store import SIGNAL_KEY_HEADER, SignalStore, TakenKeys


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


def test_a_close_of_the_moment_of_the_entry_kept_does_not_replace_it():
    async def scenario():
        store = SignalStore()
        stored = [
            await store.upsert(_signal("us100", "2026-07-16T08:00:00Z", action="long")),
            # The same bar's close: the entry wins.
            await store.upsert(_signal("us100", "2026-07-16T08:00:00Z", action="close-position")),
            await store.upsert(_signal("dax", "2026-07-16T08:00:00Z", action="close-position")),
            # Close first, then the entry of the same moment: the entry replaces it.
            await store.upsert(_signal("dax", "2026-07-16T08:00:00Z", action="short")),
        ]
        kept = {signal["indice"]: (signal["action"], signal["seq"]) for signal in await store.get_all()}
        later = await store.upsert(_signal("us100", "2026-07-16T08:01:00Z", action="close-position"))
        return stored, kept, later, (await store.get_all())[0]["action"]

    stored, kept, later, after = asyncio.run(scenario())

    assert stored == [True, False, True, True]
    assert kept == {"us100": ("long", 1), "dax": ("short", 3)}
    # A close of a later moment is stored as any signal.
    assert later is True and after == "close-position"


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


def test_a_rehearsal_keeps_nothing_counts_nothing_and_no_poll_returns_it():
    async def scenario():
        store = SignalStore()
        await store.upsert(_signal("us100", "2026-07-16T08:00:00Z"))
        before = await store.get_all()

        newer = await store.rehearse(_signal("us100", "2026-07-16T08:05:00Z", action="short"))
        older = await store.rehearse(_signal("us100", "2026-07-16T07:00:00Z"))
        unknown = await store.rehearse(_signal("dax", "2026-07-16T08:05:00Z"))

        # The next signal is counted as if no rehearsal had come.
        await store.upsert(_signal("dax", "2026-07-16T08:06:00Z"))
        return before, (newer, older, unknown), await store.get_newer(1), store._seq

    before, said, after_first, seq = asyncio.run(scenario())

    assert [(signal["action"], signal["seq"]) for signal in before] == [("long", 1)]
    # What upsert would have made of each, said and not done.
    assert said == (True, False, True)
    assert [(signal["indice"], signal["seq"]) for signal in after_first] == [("dax", 2)] and seq == 2


def test_a_rehearsal_wakes_the_waiting_poll_which_finds_nothing_and_waits_on_for_the_signal():
    async def scenario():
        store = SignalStore()
        await store.upsert(_signal("us100", "2026-07-16T08:00:00Z"))
        woken = []
        waiting = store._changed.wait_for

        async def wait_for(predicate):
            def seen():
                woken.append(store._seq)
                return predicate()
            return await waiting(seen)

        store._changed.wait_for = wait_for
        waiter = asyncio.create_task(store.get_newer(1, timeout=5))
        await asyncio.sleep(0)

        await store.rehearse(_signal("us100", "2026-07-16T08:05:00Z"))
        await asyncio.sleep(0.01)
        still_waiting = not waiter.done()

        await store.upsert(_signal("us100", "2026-07-16T08:06:00Z", action="short"))
        signals = await asyncio.wait_for(waiter, timeout=1)
        return still_waiting, len(woken), signals

    still_waiting, looks, signals = asyncio.run(scenario())

    # Looked once as it began to wait, once more at the rehearsal, a last time at the signal.
    assert still_waiting and looks == 3
    assert [(signal["action"], signal["seq"]) for signal in signals] == [("short", 2)]


def test_a_poll_woken_by_rehearsals_still_ends_at_its_own_time():
    async def scenario():
        store = SignalStore()

        async def rehearsals():
            for _ in range(5):
                await asyncio.sleep(0.01)
                await store.rehearse(_signal("us100", "2026-07-16T08:05:00Z"))

        loop = asyncio.get_running_loop()
        started = loop.time()
        rehearsing = asyncio.ensure_future(rehearsals())
        signals = await store.get_newer(0, timeout=0.08)
        await rehearsing
        return signals, loop.time() - started

    signals, waited = asyncio.run(scenario())

    assert signals == [] and 0.07 <= waited < 0.5


def test_get_newer_with_cursor_from_before_a_restart_returns_everything():
    async def scenario():
        store = SignalStore()  # fresh store: its seq restarted from 0
        await store.upsert(_signal("us100", "2026-07-16T08:00:00Z"))
        return await store.get_newer(57, timeout=5)

    signals = asyncio.run(scenario())

    assert [signal["seq"] for signal in signals] == [1]


def test_a_key_stands_for_the_signal_it_was_first_taken_as():
    keys = TakenKeys(most=3)

    assert keys.claim("rule:794", "uuid-1") == "uuid-1"
    # Sent again under its key: the signal of the first time, whatever it would be stored as now.
    assert keys.claim("rule:794", "uuid-2") == "uuid-1"
    assert keys.claim("rule:795", "uuid-3") == "uuid-3"

    # Only the latest are kept, a key asked for again among them.
    keys.claim("rule:796", "uuid-4")
    keys.claim("rule:794", "uuid-5")
    keys.claim("rule:797", "uuid-6")
    assert keys.claim("rule:794", "uuid-7") == "uuid-1"
    assert keys.claim("rule:795", "uuid-8") == "uuid-8"


def test_a_key_is_a_short_text_and_the_engine_names_its_header_the_same():
    from src.ta_engine import sender

    assert TakenKeys.valid("f13986be5e9b:794")
    assert not TakenKeys.valid(None) and not TakenKeys.valid("") and not TakenKeys.valid("k" * 201)
    assert sender.SIGNAL_KEY_HEADER == SIGNAL_KEY_HEADER
