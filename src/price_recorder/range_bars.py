"""
Range bars built from ten-second bars, one at a time.

A bar grows with the price and is complete when the price leaves its range:
it then closes at its high or at its low, one range from the other end, and
the next bar opens where it closed. A move of several ranges gives several
bars.

What is inside ten seconds is not recorded. The order assumed is the usual
one: the low before the high when the ten seconds rose, the high first when
they fell.

After a pause in the data (a night, a recording gap) the price in between is
not known: the bar in progress ends as it is and no bar is made up for the
jump.

Prices are whole numbers of ticks, so a complete bar is its range exactly.

The Prices page of the admin (``range_bars`` in ``src/admin/prices.py``) and
the strategies read the same bars from here. Pure logic: no clock, no I/O.
"""

from typing import NamedTuple

from src.price_recorder.bars import BAR_SECONDS

# Longer than this without a bar and the price in between is not known.
RANGE_PAUSE_SECONDS = 60


class BuiltBar(NamedTuple):
    # Prices in ticks.
    open: int
    high: int
    low: int
    close: int
    # Start of the ten seconds its first price fell in, and end of the ten
    # seconds its last price fell in: as fine as what was recorded allows.
    began: int
    ended: int
    # It reached its range. False for one cut by a pause, or still in progress.
    complete: bool
    after_pause: bool


class _Growing:
    __slots__ = ("start", "open", "high", "low", "close", "end", "after_pause")

    def __init__(self, start: int, price: int, after_pause: bool):
        self.start = self.end = start
        self.open = self.high = self.low = self.close = price
        self.after_pause = after_pause


class RangeBarBuilder:
    def __init__(self, ticks: int, *, bar_seconds: int = BAR_SECONDS, pause_seconds: int = RANGE_PAUSE_SECONDS):
        if ticks < 1:
            raise ValueError("A range is one tick at least")
        self._ticks = ticks
        self._bar_seconds = bar_seconds
        self._pause_seconds = pause_seconds
        self._growing: _Growing | None = None
        self._paused = False
        self._previous_time: int | None = None

    def _built(self, bar: _Growing, complete: bool) -> BuiltBar:
        return BuiltBar(
            open=bar.open, high=bar.high, low=bar.low, close=bar.close,
            began=bar.start, ended=bar.end + self._bar_seconds,
            complete=complete, after_pause=bar.after_pause,
        )

    def add(self, time: int, first: int, high: int, low: int, last: int) -> list[BuiltBar]:
        """
        One ten-second bar (its start, and its prices in ticks), after the
        previous one. Returns the range bars it ended, oldest first.
        """
        ended: list[BuiltBar] = []
        ticks = self._ticks
        growing = self._growing

        if self._previous_time is not None and time - self._previous_time > self._pause_seconds:
            if growing is not None:
                ended.append(self._built(growing, complete=False))
                growing = None
            self._paused = True
        self._previous_time = time

        for price in ((first, low, high, last) if last >= first else (first, high, low, last)):
            if growing is None:
                growing = _Growing(time, price, self._paused)
                self._paused = False
                continue
            growing.end = time
            while price > growing.low + ticks:
                growing.high = growing.close = growing.low + ticks
                ended.append(self._built(growing, complete=True))
                growing = _Growing(time, growing.close, False)
            while price < growing.high - ticks:
                growing.low = growing.close = growing.high - ticks
                ended.append(self._built(growing, complete=True))
                growing = _Growing(time, growing.close, False)
            growing.high = max(growing.high, price)
            growing.low = min(growing.low, price)
            growing.close = price

        self._growing = growing
        return ended

    def in_progress(self) -> BuiltBar | None:
        """The bar still growing, as it stands."""
        return None if self._growing is None else self._built(self._growing, complete=False)
