"""
What the TA engine measures of itself, for the admin's Engine monitor: each tick
(how long it took, what it read and wrote, how far the feed had come, the
memory of the runners and of the container) and each run of a rule's script
(how long the runner took, the bars it was given, the bar's close, the series
it asked for, the signals it raised). Kept ``monitoring_days`` in ta_engine_tick
and ta_rule_run; older rows are removed at the engine's start and at each
restart_time.

Nothing here may hold back or fail a signal. The marks are plain values set by
the loop as it goes, and handed over once the tick is done, every signal sent.
One task writes them in the background, a batch at a time: when the database is
slow the rows wait, an hour of them at most, then the oldest go, counted. A
failed write is counted too, and said once.
"""

import asyncio
import collections
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from src.price_history.process import CGROUP_ROOT, cgroup_memory, oom_kills, process_memory_mb

logger = logging.getLogger(__name__)

LOAD, APPEND = "load", "append"
# How a run ended: answered; refused by the runner (the script); the runner ended or took too long.
RUN_OK, RUN_FAILED, RUN_ENDED = "ok", "failed", "ended"
WRITE_TIMEOUT_SECONDS = 5.0
PURGE_TIMEOUT_SECONDS = 120.0
# An hour of ticks waits for the database at most; past it, the oldest go first.
MAX_WAITING_TICKS = 360
TICKS_PER_WRITE = 60
DETAIL_CHARACTERS = 1000
MEMORY_FIELDS = (
    "live_rss_mb", "live_peak_mb", "shadow_rss_mb", "shadow_peak_mb", "engine_rss_mb", "memory_used_mb",
    "memory_limit_mb", "oom_kills",
)


def _moment(seconds: float) -> datetime:
    # As the engine's: a run's tick_at is its signals' raised_at, the same float made the same moment.
    return datetime.fromtimestamp(seconds, timezone.utc)


def _ms(seconds: float) -> int:
    """Milliseconds of a duration, never below 0 (the clock is the system's, which may step back)."""
    return max(0, round(seconds * 1000))


@dataclass
class RunMark:
    """One request of a rule to the runner: its history loaded, or its new bars."""

    rule_id: str
    # Live or shadow, as the rule was when it ran, and the runner it ran on (one runner may run both).
    mode: str
    runner: str
    kind: str
    tick_at: float
    started_at: float
    bars_given: int
    # The close of the newest bar given, in seconds.
    bar_closed_at: float | None
    ended_at: float | None = None
    # When its signals were journaled and sent: its end, without any.
    done_at: float | None = None
    pinets_ms: int | None = None
    bars_held: int | None = None
    alerts: int = 0
    asks: int = 0
    asks_seconds: float = 0.0
    signals: int = 0
    outcome: str = RUN_OK
    detail: str | None = None

    def answered(self, at: float, answer: dict, given: set) -> None:
        self.ended_at = at
        milliseconds = answer.get("milliseconds")
        self.pinets_ms = milliseconds if isinstance(milliseconds, int) and not isinstance(milliseconds, bool) else None
        held = answer.get("bars")
        self.bars_held = held if isinstance(held, int) and not isinstance(held, bool) else None
        self.alerts = sum(1 for alert in answer.get("alerts") or [] if isinstance(alert, dict) and alert.get("time") in given)

    def ended_with(self, at: float, outcome: str, detail: str) -> None:
        self.ended_at, self.outcome, self.detail = at, outcome, detail[:DETAIL_CHARACTERS]

    def row(self) -> dict:
        """Its row of ta_rule_run."""
        ended = self.ended_at if self.ended_at is not None else self.started_at
        done = self.done_at if self.done_at is not None else ended
        return {
            "rule_id": self.rule_id,
            "tick_at": _moment(self.tick_at),
            "mode": self.mode,
            "runner": self.runner,
            "kind": self.kind,
            "started_at": _moment(self.started_at),
            "ended_at": _moment(ended),
            "done_at": _moment(max(done, ended)),
            "milliseconds": _ms(ended - self.started_at),
            "pinets_ms": self.pinets_ms,
            "bars_given": self.bars_given,
            "bars_held": self.bars_held,
            "bar_closed_at": _moment(self.bar_closed_at) if self.bar_closed_at is not None else None,
            "asks": self.asks,
            "asks_ms": _ms(self.asks_seconds),
            "alerts": self.alerts,
            "signals": self.signals,
            "outcome": self.outcome,
            "detail": self.detail,
        }


@dataclass
class TickMark:
    """One tick of the engine, as it went."""

    at: float
    read_seconds: float = 0.0
    rows_read: int = 0
    write_seconds: float = 0.0
    # The last ten seconds the feed had written (epoch seconds), as the tick read it.
    covered_until: int | None = None
    rules_on: int = 0
    # The shadow rules left for the next tick, their share of this one spent.
    deferred: list[str] = field(default_factory=list)
    # The rules not run: their runner would not start, or ended earlier in the tick.
    skipped: int = 0
    restarted: bool = False
    # The engine's first tick since it started: the feed it read may have stopped with it (a deploy).
    first: bool = False
    error: str | None = None
    runner_starts: int | None = None
    shadow_runner_starts: int | None = None
    runs: list[RunMark] = field(default_factory=list)

    def row(self, ended: float, memory: dict) -> dict:
        """Its row of ta_engine_tick, with the memory read as it ended."""
        return {
            "tick_at": _moment(self.at),
            "ended_at": _moment(max(ended, self.at)),
            "milliseconds": _ms(ended - self.at),
            "read_ms": _ms(self.read_seconds),
            "rows_read": self.rows_read,
            "write_ms": _ms(self.write_seconds),
            "covered_until": _moment(self.covered_until) if self.covered_until is not None else None,
            "rules_on": self.rules_on,
            "runs": len(self.runs),
            "loads": sum(1 for run in self.runs if run.kind == LOAD),
            "failed": sum(1 for run in self.runs if run.outcome != RUN_OK),
            "deferred_rules": list(self.deferred),
            "skipped": self.skipped,
            "restarted": self.restarted,
            "error": self.error[:DETAIL_CHARACTERS] if self.error else None,
            "runner_starts": self.runner_starts,
            "shadow_runner_starts": self.shadow_runner_starts,
            **{name: memory.get(name) for name in MEMORY_FIELDS},
            "figures": {"first": True} if self.first else {},
        }


def read_memory(runners: dict, proc: str = "/proc", cgroup: str = CGROUP_ROOT) -> dict:
    """
    The memory of each runner (now, and the most since it started), of the engine itself and of its
    container, in MB, with the kernel's OOM kills there: what can be read (Linux, cgroup v2), None otherwise.
    """
    figures: dict = {}
    for name in ("live", "shadow"):
        memory_mb = getattr(runners.get(name), "memory_mb", None)
        held, most = memory_mb() if memory_mb is not None else (None, None)
        figures[f"{name}_rss_mb"], figures[f"{name}_peak_mb"] = held, most
    figures["engine_rss_mb"] = process_memory_mb("self", proc)[0]
    container = cgroup_memory(cgroup)
    figures["memory_used_mb"], figures["memory_limit_mb"] = (
        (round(container[0] / 1024 ** 2, 1), round(container[1] / 1024 ** 2, 1)) if container is not None else (None, None)
    )
    figures["oom_kills"] = oom_kills(cgroup)
    return figures


class Monitoring:
    """The ticks and runs the engine measured, written in the background and kept ``days``."""

    def __init__(self, store, *, days: int, clock=time.time, memory=read_memory):
        self._store = store
        self.days = days
        self._clock = clock
        self._memory = memory
        # (tick row, its run rows), oldest first. One task writes them, none at all while nothing waits:
        # no lock or event, which would belong to one event loop (the tests run the engine in several).
        self._waiting: collections.deque = collections.deque()
        self._task: asyncio.Task | None = None
        self._purge_due = True
        self.dropped = 0
        self.written_at: float | None = None
        self.purged_at: float | None = None
        self.error: str | None = None
        self._said: str | None = None

    def tick_ended(self, mark: TickMark, at: float, runners: dict) -> None:
        """Hand over what a tick measured, once it is done. Returns at once and never raises."""
        try:
            runs = [run.row() for run in mark.runs]
            self._waiting.append((mark.row(at, self._memory(runners)), runs))
            if mark.restarted:
                self._purge_due = True
            while len(self._waiting) > MAX_WAITING_TICKS:
                self._waiting.popleft()
                self.dropped += 1
            self._start()
        except Exception as e:
            self._failed(e)

    def figures(self) -> dict:
        """What the engine says of its measures, in ta_engine_state."""
        return {
            "days": self.days, "waiting": len(self._waiting), "dropped": self.dropped,
            "written_at": self.written_at, "purged_at": self.purged_at, "error": self.error,
        }

    async def flush(self, timeout: float) -> None:
        """Give what waits a chance to be written: at the engine's stop, and in tests."""
        self._start()
        if self._task is None or self._task.done():
            return
        try:
            await asyncio.wait_for(asyncio.shield(self._task), timeout)
        except Exception:
            pass

    def _start(self) -> None:
        if (self._waiting or self._purge_due) and (self._task is None or self._task.done()):
            self._task = asyncio.get_running_loop().create_task(self._write_all())

    async def _write_all(self) -> None:
        while self._waiting:
            batch = [self._waiting.popleft() for _ in range(min(TICKS_PER_WRITE, len(self._waiting)))]
            ticks = [tick for tick, _ in batch]
            runs = [run for _, rows in batch for run in rows]
            try:
                await asyncio.wait_for(self._store.write_monitoring(ticks, runs), WRITE_TIMEOUT_SECONDS)
            except asyncio.CancelledError:
                self.dropped += len(batch)
                raise
            except Exception as e:
                # Not written again: a database that refuses them would be asked for ever. The next tick tries its own.
                self.dropped += len(batch)
                self._failed(e)
                return
            self.written_at, self.error = self._clock(), None
        if self._purge_due:
            await self._purge()

    async def _purge(self) -> None:
        try:
            removed = await asyncio.wait_for(
                self._store.purge_monitoring(_moment(self._clock() - self.days * 86_400)), PURGE_TIMEOUT_SECONDS,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # Due still: tried again after the next tick's rows are written.
            self._failed(e)
            return
        self._purge_due, self.purged_at = False, self._clock()
        if removed:
            logger.info("TA engine: %d measure(s) older than %d day(s) removed.", removed, self.days)

    def _failed(self, e: Exception) -> None:
        said = f"{type(e).__name__}: {e}" if str(e) else f"{type(e).__name__} (no answer in time)"
        self.error = said[:DETAIL_CHARACTERS]
        if said != self._said:
            self._said = said
            logger.warning("The TA engine's measures of itself could not be written: %s", said)
