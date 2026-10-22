"""
What the TA engine measures of itself (src/ta_engine/monitoring.py): its rows, the writer that keeps
them away from the signals, and the memory it reads (Linux files, faked here).
"""

import asyncio
import logging
from datetime import datetime, timezone

from src.price_history.process import peak_memory_mb, process_memory_mb
from src.ta_engine.monitoring import (
    APPEND,
    DETAIL_CHARACTERS,
    LOAD,
    MAX_WAITING_TICKS,
    RUN_FAILED,
    Monitoring,
    RunMark,
    TickMark,
    read_memory,
)

T = 1792051200.0


def moment(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc)


def no_memory(runners):
    return {}


class Store:
    def __init__(self):
        self.ticks, self.runs, self.purged = [], [], []
        self.refuse_writes = self.refuse_purges = 0
        self.held: asyncio.Event | None = None
        self.writing = self.most_at_once = 0

    async def write_monitoring(self, ticks, runs):
        self.writing += 1
        self.most_at_once = max(self.most_at_once, self.writing)
        try:
            if self.held is not None:
                await self.held.wait()
            if self.refuse_writes:
                self.refuse_writes -= 1
                raise ConnectionError("the database refused")
            self.ticks += ticks
            self.runs += runs
        finally:
            self.writing -= 1

    async def purge_monitoring(self, before):
        if self.refuse_purges:
            self.refuse_purges -= 1
            raise ConnectionError("the purge was refused")
        self.purged.append(before)
        return 3


def tick(at, **changes):
    mark = TickMark(at)
    for name, value in changes.items():
        setattr(mark, name, value)
    return mark


# ── the rows ──


def test_a_runs_row_says_its_times_its_bars_and_what_it_raised():
    mark = RunMark(rule_id="0123456789ab", mode="live", runner="live", kind=APPEND, tick_at=T, started_at=T + 0.1,
                   bars_given=2, bar_closed_at=T - 1.5)
    mark.answered(T + 0.4, {"milliseconds": 250, "bars": 900, "alerts": [{"time": 1}, {"time": 2}, {"time": 3}, "x"]}, {1, 3})
    mark.asks, mark.asks_seconds, mark.signals, mark.done_at = 1, 0.05, 1, T + 0.45

    row = mark.row()

    assert (row["tick_at"], row["started_at"], row["ended_at"], row["done_at"]) == (moment(T), moment(T + 0.1), moment(T + 0.4), moment(T + 0.45))
    assert (row["milliseconds"], row["pinets_ms"], row["bars_given"], row["bars_held"]) == (300, 250, 2, 900)
    # Only the alerts of the bars given count: the runner hands back those of the bars before too.
    assert (row["alerts"], row["asks"], row["asks_ms"], row["signals"], row["outcome"]) == (2, 1, 50, 1, "ok")
    assert row["bar_closed_at"] == moment(T - 1.5)


def test_a_run_that_failed_keeps_why_and_a_clock_that_stepped_back_gives_no_negative_time():
    mark = RunMark(rule_id="0123456789ab", mode="shadow", runner="shadow", kind=LOAD, tick_at=T, started_at=T + 1,
                   bars_given=0, bar_closed_at=None)
    mark.ended_with(T + 0.5, RUN_FAILED, "x" * 5000)

    row = mark.row()

    assert (row["outcome"], len(row["detail"]), row["milliseconds"]) == ("failed", DETAIL_CHARACTERS, 0)
    assert row["done_at"] == row["ended_at"] == moment(T + 0.5) and row["bar_closed_at"] is None and row["pinets_ms"] is None


def test_a_ticks_row_counts_its_runs_and_takes_the_memory_read_as_it_ended():
    load = RunMark(rule_id="a" * 12, mode="live", runner="live", kind=LOAD, tick_at=T, started_at=T, bars_given=10, bar_closed_at=T)
    failed = RunMark(rule_id="b" * 12, mode="shadow", runner="shadow", kind=APPEND, tick_at=T, started_at=T, bars_given=1, bar_closed_at=T)
    failed.ended_with(T + 0.2, RUN_FAILED, "Bad")
    mark = tick(T, read_seconds=0.012, rows_read=6, write_seconds=0.003, covered_until=int(T - 1), rules_on=3,
                deferred=["c" * 12], restarted=True, runs=[load, failed])

    row = mark.row(T + 0.75, {"live_rss_mb": 214.3, "memory_used_mb": 520.0, "unknown": 1})

    assert (row["milliseconds"], row["read_ms"], row["rows_read"], row["write_ms"]) == (750, 12, 6, 3)
    assert (row["runs"], row["loads"], row["failed"], row["deferred_rules"], row["restarted"]) == (2, 1, 1, ["c" * 12], True)
    assert (row["covered_until"], row["rules_on"]) == (moment(T - 1), 3)
    assert (row["live_rss_mb"], row["memory_used_mb"], row["shadow_rss_mb"]) == (214.3, 520.0, None) and "unknown" not in row


# ── the writer ──


def test_an_hour_of_ticks_waits_for_the_database_at_most_and_the_oldest_go_first():
    async def scenario():
        store = Store()
        store.held = asyncio.Event()
        monitoring = Monitoring(store, days=7, clock=lambda: T, memory=no_memory)
        for index in range(MAX_WAITING_TICKS + 5):
            monitoring.tick_ended(tick(T + index * 10), T + index * 10 + 0.2, {})
        store.held.set()
        await monitoring.flush(1)
        return store, monitoring

    store, monitoring = asyncio.run(scenario())

    assert monitoring.dropped == 5 and len(store.ticks) == MAX_WAITING_TICKS
    assert store.ticks[0]["tick_at"] == moment(T + 50) and monitoring.figures()["waiting"] == 0


def test_one_write_at_a_time_however_many_ticks_end_meanwhile():
    async def scenario():
        store = Store()
        store.held = asyncio.Event()
        monitoring = Monitoring(store, days=7, clock=lambda: T, memory=no_memory)
        for index in range(3):
            monitoring.tick_ended(tick(T + index * 10), T + index * 10 + 0.2, {})
            await asyncio.sleep(0)
        store.held.set()
        await monitoring.flush(1)
        return store

    store = asyncio.run(scenario())

    assert store.most_at_once == 1 and [row["tick_at"] for row in store.ticks] == [moment(T), moment(T + 10), moment(T + 20)]


def test_a_write_the_database_refuses_is_counted_said_once_and_the_next_tick_writes_its_own(caplog):
    async def scenario():
        store = Store()
        store.refuse_writes = 2
        monitoring = Monitoring(store, days=7, clock=lambda: T + 99, memory=no_memory)
        figures = []
        for index in range(3):
            monitoring.tick_ended(tick(T + index * 10), T + index * 10 + 0.2, {})
            await monitoring.flush(1)
            figures.append(monitoring.figures())
        return store, figures

    with caplog.at_level(logging.WARNING, logger="src.ta_engine.monitoring"):
        store, figures = asyncio.run(scenario())

    assert [(entry["dropped"], entry["error"]) for entry in figures] == [
        (1, "ConnectionError: the database refused"), (2, "ConnectionError: the database refused"), (2, None),
    ]
    assert [row["tick_at"] for row in store.ticks] == [moment(T + 20)] and figures[-1]["written_at"] == T + 99
    assert sum("could not be written" in record.getMessage() for record in caplog.records) == 1


def test_older_measures_are_removed_at_start_again_after_a_failure_and_at_each_restart():
    async def scenario():
        store = Store()
        store.refuse_purges = 1
        clock = [T]
        monitoring = Monitoring(store, days=3, clock=lambda: clock[0], memory=no_memory)
        for index, restarted in enumerate((False, False, False, True)):
            clock[0] = T + index * 10
            monitoring.tick_ended(tick(T + index * 10, restarted=restarted), T + index * 10, {})
            await monitoring.flush(1)
        return store, monitoring

    store, monitoring = asyncio.run(scenario())

    # Refused at the first tick, removed at the second, not asked again until the restart.
    assert store.purged == [moment(T + 10 - 3 * 86_400), moment(T + 30 - 3 * 86_400)]
    assert monitoring.purged_at == T + 30 and len(store.ticks) == 4


def test_what_cannot_be_read_never_raises_into_the_engine():
    def broken(runners):
        raise OSError("no /proc here")

    async def scenario():
        monitoring = Monitoring(Store(), days=7, clock=lambda: T, memory=broken)
        monitoring.tick_ended(tick(T), T, {})
        return monitoring

    monitoring = asyncio.run(scenario())

    assert monitoring.error == "OSError: no /proc here" and monitoring.figures()["waiting"] == 0


# ── memory, from the files Linux keeps ──


class FakeRunner:
    def __init__(self, held, most):
        self.figures = (held, most)

    def memory_mb(self):
        return self.figures


def test_the_memory_of_the_runners_the_engine_and_its_container_is_read_from_proc_and_the_cgroup(tmp_path):
    proc, cgroup = tmp_path / "proc", tmp_path / "cgroup"
    (proc / "self").mkdir(parents=True)
    (proc / "self" / "status").write_text("Name:\tpython\nVmHWM:\t  131072 kB\nVmRSS:\t   92160 kB\n", encoding="utf-8")
    cgroup.mkdir()
    (cgroup / "memory.max").write_text("1073741824\n", encoding="utf-8")
    (cgroup / "memory.current").write_text("536870912\n", encoding="utf-8")
    (cgroup / "memory.stat").write_text("anon 400000000\ninactive_file 104857600\n", encoding="utf-8")
    (cgroup / "memory.events").write_text("low 0\nhigh 0\nmax 4\noom 1\noom_kill 2\n", encoding="utf-8")

    figures = read_memory({"live": FakeRunner(214.3, 260.1)}, proc=str(proc), cgroup=str(cgroup))

    assert figures == {
        "live_rss_mb": 214.3, "live_peak_mb": 260.1, "shadow_rss_mb": None, "shadow_peak_mb": None, "engine_rss_mb": 90.0,
        "memory_used_mb": 412.0, "memory_limit_mb": 1024.0, "oom_kills": 2,
    }


def test_no_memory_is_made_up_where_linux_says_none(tmp_path):
    figures = read_memory({"live": object()}, proc=str(tmp_path / "none"), cgroup=str(tmp_path / "none"))

    assert set(figures.values()) == {None}


def test_a_processs_memory_now_and_at_its_most_come_from_one_read_of_its_status(tmp_path):
    (tmp_path / "42").mkdir()
    (tmp_path / "42" / "status").write_text("VmPeak:\t 900000 kB\nVmHWM:\t  266342 kB\nVmRSS:\t  219443 kB\n", encoding="utf-8")

    assert process_memory_mb(42, str(tmp_path)) == (214.3, 260.1)
    assert peak_memory_mb(42, str(tmp_path)) == 260.1
    assert process_memory_mb(43, str(tmp_path)) == (None, None) and process_memory_mb(None) == (None, None)
