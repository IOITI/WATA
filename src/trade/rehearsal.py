"""
A signal's way to an order, rehearsed between signals.

A signal comes every few hours, and what it runs through in the trader had gone
cold by then. Measured on the server (23 to 26 November 2026, 28 signals): the
trader started on a signal 58 to 171 ms after the web server stored it and its
checks took 35 to 94 ms when the signal before was an hour old or more, against
5 to 9 ms and 3 to 12 ms when it was minutes old. Two of the three live entries
of those days lost about 0.3 s to it before their trade began. The server has
no swap and no memory pressure: what goes cold is not known (most likely its
host taking back memory left alone), which is why each rehearsal is measured.

So the trader runs that way on a timer, with what a signal would run and nothing
that acts: its checks and their reads, the turbo it would buy, the size of the
order. ``rehearse`` runs the steps it is given, one after the other:

- quietly: what the trader's checks, its sizing and its watchlist lookup log
  (QUIET_LOGGERS) is left out of every handler (``quiet_while_rehearsing``
  installs the filter once), for this task alone. A check that refuses logs it,
  and a day's target reached would be said every thirty seconds. What anything
  else logs meanwhile (the database, asyncio, the HTTP client) is kept: a
  connection opened during a rehearsal carries its context for as long as it
  lives, and its faults must be seen;
- a step that raises is passed over, a refusal being one;
- it stops between two steps once ``busy`` says a signal is in hand;
- and it says what it took, with the processor time and the page faults of the
  process meanwhile: a rehearsal slower than SLOW_SECONDS is worth a line, which
  tells waiting (wall time far above processor time) from work, and reads from
  the disk (major faults) from neither.
"""

import contextvars
import inspect
import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

try:  # not on Windows, where nothing of WATA runs but a developer's tests
    import resource
except ImportError:  # pragma: no cover
    resource = None

# A rehearsal that took longer than this is said in the log.
SLOW_SECONDS = 0.05
# The loggers left out during a rehearsal: the trading rules' (they log through the root logger), the
# orchestrator's (the position's scale, the order's size) and the watchlist client's (a stale entry).
QUIET_LOGGERS = frozenset({"root", "src.trade.async_services", "src.trade.watchlist_client"})

_rehearsing: contextvars.ContextVar[bool] = contextvars.ContextVar("wata_rehearsing", default=False)


class _Quiet(logging.Filter):
    """Leaves out what the rehearsed code logs from inside a rehearsal."""

    def filter(self, record: logging.LogRecord) -> bool:
        return not (_rehearsing.get() and record.name in QUIET_LOGGERS)


def quiet_while_rehearsing() -> None:
    """Every handler of the root logger leaves out what a rehearsal logs. Once per handler, whenever it is called."""
    for handler in logging.getLogger().handlers:
        if not any(isinstance(known, _Quiet) for known in handler.filters):
            handler.addFilter(_Quiet())


@dataclass(frozen=True)
class Rehearsal:
    steps: int                  # how many were run (all of them unless a signal came)
    seconds: float              # by the clock
    cpu_seconds: float          # the process's, every thread of it
    major_faults: int           # pages read back from the disk meanwhile
    slowest: tuple[str, float] | None

    @property
    def slow(self) -> bool:
        return self.seconds > SLOW_SECONDS

    def said(self) -> str:
        slowest = f"; slowest: {self.slowest[0]}, {self.slowest[1] * 1000:.0f} ms" if self.slowest else ""
        return (
            f"{self.seconds * 1000:.0f} ms for {self.steps} step(s) (processor {self.cpu_seconds * 1000:.0f} ms, "
            f"{self.major_faults} page(s) read from the disk{slowest})"
        )


def _major_faults() -> int:
    return resource.getrusage(resource.RUSAGE_SELF).ru_majflt if resource is not None else 0


async def rehearse(steps: Iterable[tuple[str, Callable]], busy: Callable[[], bool] | None = None) -> Rehearsal:
    """
    Each of ``steps`` (a name, and a function that may return something to
    await) run in turn, quietly; what one raises is passed over. Stops between
    two steps once ``busy()`` is true. Never raises.
    """
    started, cpu, faults = time.perf_counter(), time.process_time(), _major_faults()
    done, slowest = 0, None
    token = _rehearsing.set(True)
    try:
        for name, step in steps:
            if busy is not None and busy():
                break
            began = time.perf_counter()
            try:
                result = step()
                if inspect.isawaitable(result):
                    await result
            except Exception:
                # A refusal (the day's target reached, the market closed) or a read that failed: the next signal's
                # to say, not a rehearsal's.
                pass
            done += 1
            took = time.perf_counter() - began
            if slowest is None or took > slowest[1]:
                slowest = (name, took)
    finally:
        _rehearsing.reset(token)
    return Rehearsal(done, time.perf_counter() - started, time.process_time() - cpu, _major_faults() - faults, slowest)
