"""
Runs a strategy over ten-second bars, one trading day after the other.

A strategy answers each bar with the decisions it took on it. Several can fall
in the same ten seconds (a fast move ends several range bars at once): only
where they leave the strategy matters, so one change of target at most comes
out of a bar.

Pure logic: no clock of its own, no I/O.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable, Iterable, Protocol

import pytz

from src.strategy.bars import IndexBar

FLAT = "flat"
LONG = "long"
SHORT = "short"

DAY_ENDED = "day ended"

_EPOCH_DAY = date(1970, 1, 1)
_HOUR = 3600
_DAY = 86400


@dataclass(frozen=True)
class Decision:
    # When it is known, epoch seconds: the end of the ten seconds that brought it.
    at: int
    # The position wanted from then on: flat, long or short.
    target: str
    reason: str
    confidence: float | None = None
    # A flat decision that only ends one side (the trader's close-long and close-short): "long" or "short".
    closes: str | None = None


class Strategy(Protocol):
    def start_day(self, day: date) -> None:
        """A new trading day begins: nothing is carried over from the one before."""

    def on_bar(self, bar: IndexBar) -> list[Decision]:
        """The next bar of the day; the decisions it brought, in the order they were taken."""

    def entry_window(self) -> tuple[int, int]:
        """
        The part of a day in which it may enter, in seconds since midnight of
        the trading timezone (the end is not in it). What random entries are
        drawn in, to be compared with its own (src/strategy/baseline.py).
        """


class LocalClock:
    """
    The day and the time of day of a moment, in the trading timezone. Asked
    for every bar of years of history, so the timezone is only consulted once
    an hour: its offset to UTC does not change in between.
    """

    def __init__(self, timezone_name: str):
        self.zone = pytz.timezone(timezone_name)
        self._hour_start = 0
        self._hour_end = 0
        self._offset = 0
        self._day_number: int | None = None
        self._day = _EPOCH_DAY

    def at(self, timestamp: int) -> tuple[date, int]:
        """The local day of ``timestamp`` and the seconds since its midnight."""
        if not self._hour_start <= timestamp < self._hour_end:
            self._hour_start = timestamp - timestamp % _HOUR
            self._hour_end = self._hour_start + _HOUR
            self._offset = int(datetime.fromtimestamp(self._hour_start, self.zone).utcoffset().total_seconds())
        local = timestamp + self._offset
        day_number = local // _DAY
        if day_number != self._day_number:
            self._day_number = day_number
            self._day = _EPOCH_DAY + timedelta(days=day_number)
        return self._day, local % _DAY

    def epoch(self, day: date, clock_time: str) -> int:
        """Epoch seconds of ``clock_time`` ("08:00") on that local day."""
        hour, minute = (int(part) for part in clock_time.split(":"))
        return int(self.zone.localize(datetime(day.year, day.month, day.day, hour, minute)).timestamp())


def seconds_of(clock_time: str) -> int:
    """ "21:55" as seconds since midnight."""
    hour, minute = (int(part) for part in clock_time.split(":"))
    return hour * _HOUR + minute * 60


class Feed:
    """
    One strategy given the bars a bar at a time: what ``run`` does, for whoever
    has several strategies to give the same bars to (src/strategy/sweep.py).
    ``begin`` before the bar is acted on by whoever executes, ``end`` after.
    """

    def __init__(self, strategy: Strategy, clock: LocalClock, on_decision: Callable[[Decision], None] | None = None):
        self._strategy = strategy
        self._clock = clock
        self._on_decision = on_decision
        self._day: date | None = None
        self._target = FLAT
        # Each change of target the strategy made.
        self.changes: list[Decision] = []

    def _changed(self, decision: Decision) -> None:
        self._target = decision.target
        self.changes.append(decision)
        if self._on_decision is not None:
            self._on_decision(decision)

    def begin(self, bar: IndexBar) -> None:
        bar_day, _ = self._clock.at(bar.start)
        if bar_day != self._day:
            # The strategy starts the day afresh: a position it never closed
            # (the data stopped before the session did) is given back first.
            if self._day is not None and self._target != FLAT:
                self._changed(Decision(bar.start, FLAT, DAY_ENDED))
            self._day = bar_day
            self._strategy.start_day(bar_day)

    def end(self, bar: IndexBar) -> None:
        decisions = self._strategy.on_bar(bar)
        if decisions and decisions[-1].target != self._target:
            self._changed(decisions[-1])


def run(
    strategy: Strategy,
    bars: Iterable[IndexBar],
    clock: LocalClock,
    *,
    on_bar: Callable[[IndexBar], None] | None = None,
    on_decision: Callable[[Decision], None] | None = None,
) -> list[Decision]:
    """
    Feed the bars to the strategy and return each change of target it made.

    ``on_bar`` is called with a bar before the strategy sees it: whoever
    executes acts at its open on what was decided before. ``on_decision`` is
    called with the change of target a bar brought, once the bar is over.
    """
    feed = Feed(strategy, clock, on_decision)
    for bar in bars:
        feed.begin(bar)
        if on_bar is not None:
            on_bar(bar)
        feed.end(bar)
    return feed.changes
