"""
How many bars the TA engine runs a signal rule's script on, which the rule's
dialog says under "Days of bars before each day".

Each day at restart_time (UTC) the engine starts every rule again on the bars
of its days of history before that moment: the rule's own, or the engine's
history_days, each of 24 hours (a weekend or a holiday counts, and brings no
bar). It runs the script over them once, its alerts never sent, then adds each
new bar until the next restart: a script runs on its days of bars just after
the restart, and on one day more by the next one (src/ta_engine/engine.py).

The bars are made as the engine makes them, by its own builders
(src/ta_engine/bars.py) on the Capital.com feed's ten-second rows, from the
rule's own start: what is counted is what the engine would load. The last
engine days are counted too, restart to restart, each bar in the day it closed
in: how many bars a day of these makes, a weekend's included.
"""

from src.admin.prices import price_decimals
from src.admin.ta_rules import TIME_BARS, timeframe_seconds
from src.ta_engine.bars import RangeBars, TimeBars

DAY = 86_400
# The engine days counted before the last restart: a week, its weekend included.
SHOWN_DAYS = 7
# The rows the engine reads an instrument's decimals from when the feed has none (Engine._builder).
DECIMALS_ROWS = 2000


def last_restart(now: float, restart_minute: int) -> int:
    """The last restart_time at or before ``now``, epoch seconds, as the engine finds it (Engine.last_restart)."""
    restart = int(now) - int(now) % DAY + restart_minute * 60
    return restart if restart <= now else restart - DAY


def bar_builder(bars: dict, side: str, decimals: int):
    """The builder of a rule's bars, the engine's (Engine._builder)."""
    if bars["type"] == TIME_BARS:
        return TimeBars(timeframe_seconds(bars["timeframe"]), side)
    return RangeBars(bars["ticks"], decimals, side)


class HistoryCount:
    """
    A rule's bars counted as the rows of its epic go by, oldest first, from
    ``first``: those the engine makes from the rule's start (its days before
    the last restart), and those of each of the last engine days.
    """

    def __init__(self, bars: dict, side: str, decimals: int | None, *, start: int, restart: int, now: float,
                 shown_days: int = SHOWN_DAYS):
        self._bars, self._side, self._decimals = bars, side, decimals
        self.start, self.restart, self.now = start, restart, now
        self.shown_days = shown_days
        # Where the rows are read from: the rule's start, or the first engine day counted.
        self.first = min(start, restart - shown_days * DAY)
        self._rule = None
        self._days = None
        # Bars of the rule closed by the last restart (what it was loaded with), and by now.
        self.at_restart = 0
        self.loaded = 0
        # Bars closed in each engine day before the restart, the oldest first.
        self.day_bars = [0] * shown_days

    def add(self, rows) -> None:
        """The next rows, in time order."""
        if not rows:
            return
        if self._rule is None:
            decimals = self._decimals
            if decimals is None:
                decimals = price_decimals([row[4] for row in rows[:DECIMALS_ROWS]])
            self._rule = bar_builder(self._bars, self._side, decimals)
            # From the same moment, the same bars.
            self._days = self._rule if self.first == self.start else bar_builder(self._bars, self._side, decimals)
        if self._days is self._rule:
            self._count(self._rule.add(rows), rule=True, days=True)
            return
        own = rows if rows[0][0] >= self.start else [row for row in rows if row[0] >= self.start]
        self._count(self._rule.add(own), rule=True, days=False)
        self._count(self._days.add(rows), rule=False, days=True)

    def finish(self) -> None:
        """The bar of time still open, once every ten seconds of it before now is in."""
        if self._rule is None:
            return
        if self._days is self._rule:
            self._count(self._rule.add([], self.now), rule=True, days=True)
            return
        self._count(self._rule.add([], self.now), rule=True, days=False)
        self._count(self._days.add([], self.now), rule=False, days=True)

    def _count(self, bars: list, *, rule: bool, days: bool) -> None:
        for bar in bars:
            # A bar for the runner: [open ms, open, high, low, close, volume, close ms].
            closed = bar[6] / 1000
            if rule:
                self.loaded += 1
                if closed <= self.restart:
                    self.at_restart += 1
            if days and closed <= self.restart:
                # The engine day (restart to restart) it closed in, if it is one of those counted.
                back = int((self.restart - closed) // DAY)
                if back < self.shown_days:
                    self.day_bars[self.shown_days - 1 - back] += 1

    def engine_days(self) -> list[dict]:
        """Each engine day counted, the oldest first: its start and end (epoch seconds) and its bars."""
        return [
            {"start": self.restart - (self.shown_days - index) * DAY,
             "end": self.restart - (self.shown_days - 1 - index) * DAY,
             "bars": count}
            for index, count in enumerate(self.day_bars)
        ]
