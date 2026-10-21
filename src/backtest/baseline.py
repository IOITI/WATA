"""
A run is never read alone: the same days are traded again and again with
entries at random, under the same rules, on the same recorded turbos, and the
run says the share of those it did better than.

Each day of a repetition has as many entries as the strategy's own signals of
entry that day, at random ten-second boundaries inside the hours the strategy
entered in over the whole run, where the recorder was listening, each long or
short at the toss of a coin. Nothing closes them but what closes any position:
the monitor, the next entry, the day's close.

The draws are seeded by repetition and day, so that two runs over the same
days are set against the same random entries.
"""

import random

from src.backtest.clock import BAR_SECONDS, TradingClock
from src.backtest.data import DayData
from src.backtest.execution import fills_for
from src.backtest.rules import compounded
from src.backtest.settings import BacktestSettings
from src.backtest.simulator import ENTRY_ACTIONS, SignalEvent, Simulator

DEFAULT_REPETITIONS = 100
MAX_REPETITIONS = 500
DEFAULT_SEED = 1


def entry_hours(events: list[SignalEvent], clock: TradingClock) -> tuple[int, int] | None:
    """The earliest and latest local minute the strategy's entries reached the trader at, over the run."""
    minutes = [clock.minute_of_day(event.arrived_at) for event in events if event.action in ENTRY_ACTIONS]
    return (min(minutes), max(minutes)) if minutes else None


def entry_moments(day: DayData, hours: tuple[int, int], clock: TradingClock) -> list[int]:
    """The ten-second boundaries of ``day`` inside the strategy's hours where the recorder was listening."""
    first_minute, last_minute = hours
    start = day.start - day.start % BAR_SECONDS
    steady = clock.local(start).utcoffset() == clock.local(day.end - 1).utcoffset()
    # The day's first boundary's local time, then ten seconds at a time: counted rather than looked up, but
    # on the days the clocks change.
    first_local = clock.minute_of_day(start) * 60 + clock.local(start).second
    moments = []
    for moment in range(start, day.end, BAR_SECONDS):
        minute = ((first_local + moment - start) // 60) % 1440 if steady else clock.minute_of_day(moment)
        if first_minute <= minute <= last_minute and day.is_covered(moment):
            moments.append(moment)
    return moments


def random_entries(day: DayData, moments: list[int], count: int, seed: int, repetition: int,
                   *, first_id: int) -> list[SignalEvent]:
    """``count`` entries at moments drawn among ``moments``, each long or short at the toss of a coin."""
    if count <= 0 or not moments:
        return []
    draw = random.Random(f"{seed}:{repetition}:{day.day.isoformat()}")
    chosen = sorted(draw.sample(moments, min(count, len(moments))))
    return [
        SignalEvent(id=first_id + place, source="random", action=draw.choice(ENTRY_ACTIONS), arrived_at=float(moment),
                    alert_at=float(moment), indice=day.indice)
        for place, moment in enumerate(chosen)
    ]


class Baseline:
    """The repetitions of a run, traded a day at a time beside it."""

    def __init__(self, settings: BacktestSettings, clock: TradingClock, repetitions: int, seed: int,
                 hours: tuple[int, int] | None, samples: dict | None = None):
        self.settings = settings
        self.clock = clock
        self.seed = seed
        self.hours = hours
        # Each repetition draws its own fills when the run's are measured.
        self.simulators = [
            Simulator(settings, clock, fills=fills_for(settings, samples, f"{seed}:random:{repetition}"))
            for repetition in range(repetitions if hours is not None else 0)
        ]
        self.entries = 0

    def run_day(self, day: DayData, entries: int, closing: list[SignalEvent]) -> None:
        if not self.simulators:
            return
        self.entries += entries
        moments = entry_moments(day, self.hours, self.clock) if entries else []
        for repetition, simulator in enumerate(self.simulators):
            events = random_entries(day, moments, entries, self.seed, repetition, first_id=1_000_000)
            simulator.run_day(day, events + closing)

    def totals(self, tested: set[str]) -> list[float]:
        """The total of each repetition over the days tested, compounded as a run's total is."""
        return [
            compounded([record["day_percent"] for record in simulator.days if record["day"] in tested])
            for simulator in self.simulators
        ]

    def trades(self) -> list[int]:
        return [len(simulator.trades) for simulator in self.simulators]

