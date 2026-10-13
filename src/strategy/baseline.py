"""
What entries taken at random would have given under the same rules.

WATA's exits decide most of a trade: the monitor sells at its stop, or as soon
as the day has reached its target. A strategy can look good because of them,
whatever it entered on. So each backtest is set against the same days traded
with a coin and a clock: on every day, as many entries as the strategy asked
for, each at a moment drawn in the hours it may enter in, long or short by a
coin, and nothing but WATA's rules to end them (a random entry has no signal
that ends it). Done many times over, this gives the range of results that owe
nothing to the entries. A strategy that lands inside that range has shown
nothing yet.

The draws are the same from one run to the next: a repetition is made from its
own number, so two backtests are compared against the same random entries
whenever they ask for as many on the same days.

Pure logic: no clock of its own, no I/O.
"""

import random
from dataclasses import dataclass, field

from src.price_recorder.bars import BAR_SECONDS
from src.strategy.bars import IndexBar
from src.strategy.engine import LONG, SHORT, Decision, LocalClock
from src.strategy.execution import Execution, Simulator, TurboModel, WataRules, compounded
from src.strategy.report import quantile

RANDOM_ENTRY = "random"
DEFAULT_REPETITIONS = 200
DEFAULT_SEED = 1
# What the range is given for: nine repetitions in ten are inside it.
RANGE_LEVEL = 90
_HOUR = 3600


@dataclass
class _Repetition:
    """One way the coin and the clock fell, for each turbo assumed."""

    draw: random.Random
    simulators: list[Simulator]
    trades: list[int] = field(default_factory=list)
    wins: list[int] = field(default_factory=list)


class Baseline:
    """
    Give it each day once it is over with ``day``: its bars, and how many
    entries the strategy asked for on it. ``finish`` when there are no more.
    """

    def __init__(
        self,
        rules: WataRules,
        turbos: list[TurboModel],
        clock: LocalClock,
        *,
        window: tuple[int, int],
        repetitions: int = DEFAULT_REPETITIONS,
        seed: int = DEFAULT_SEED,
        entries_are_orders: bool = False,
        execution: Execution = Execution(),
    ):
        self._clock = clock
        self._seed = seed
        # Where an entry can be taken at all: inside the strategy's hours, and the trader's.
        self._first = max(window[0], rules.trading_start_hour * _HOUR)
        self._last = min(window[1], rules.trading_end_hour * _HOUR)
        self._repetitions = [
            _Repetition(
                draw=random.Random(f"{seed}:{number}"),
                simulators=[
                    Simulator(rules, turbo, clock, entries_are_orders=entries_are_orders, execution=execution)
                    for turbo in turbos
                ],
                trades=[0] * len(turbos),
                wins=[0] * len(turbos),
            )
            for number in range(repetitions)
        ]
        self.entries = 0

    def day(self, bars: list[IndexBar], entries: int) -> None:
        """One day of prices, on which the strategy asked for ``entries`` entries."""
        if not bars:
            return
        # A decision is acted on at the open of the next bar: the last one has none to give it to.
        places = [
            place for place, bar in enumerate(bars[:-1]) if self._first <= self._clock.at(bar.start)[1] < self._last
        ] if entries else []
        count = min(entries, len(places))
        self.entries += count
        for repetition in self._repetitions:
            picks = sorted(
                (place, repetition.draw.choice((LONG, SHORT))) for place in repetition.draw.sample(places, count)
            )
            for number, simulator in enumerate(repetition.simulators):
                if not picks and not simulator.busy:
                    continue
                _replay(simulator, bars, picks)
                repetition.trades[number] += len(simulator.trades)
                repetition.wins[number] += sum(1 for trade in simulator.trades if trade.performance > 0)
                # Only the days' results are kept: a year of trades two hundred times over is not.
                simulator.trades.clear()
                simulator.refusals.clear()

    def finish(self) -> None:
        for repetition in self._repetitions:
            for number, simulator in enumerate(repetition.simulators):
                simulator.finish()
                repetition.trades[number] += len(simulator.trades)
                repetition.wins[number] += sum(1 for trade in simulator.trades if trade.performance > 0)
                simulator.trades.clear()

    def against(self, number: int, total_percent: float) -> dict | None:
        """
        The random entries on the turbo of that number, and where
        ``total_percent`` (the strategy's own total on it) falls among them.
        None when there was nothing to draw.
        """
        if not self._repetitions or not self.entries:
            return None
        totals = sorted(
            compounded([day.percent for day in repetition.simulators[number].days]) for repetition in self._repetitions
        )
        below = sum(1 for total in totals if total < total_percent)
        same = sum(1 for total in totals if total == total_percent)
        trades = [repetition.trades[number] for repetition in self._repetitions]
        win_rates = [
            repetition.wins[number] * 100 / repetition.trades[number]
            for repetition in self._repetitions if repetition.trades[number]
        ]
        tail = (100 - RANGE_LEVEL) / 200.0
        return {
            "repetitions": len(totals),
            "seed": self._seed,
            # Entries drawn in each repetition: as many as the strategy asked for.
            "entries": self.entries,
            # Share of the repetitions the strategy did better than; one that did exactly as well counts for half.
            "beaten": round((below + same / 2) * 100 / len(totals), 1),
            "median": round(quantile(totals, 0.5), 2),
            "low": round(quantile(totals, tail), 2),
            "high": round(quantile(totals, 1 - tail), 2),
            "level": RANGE_LEVEL,
            "worst": totals[0],
            "best": totals[-1],
            "trades": round(quantile(trades, 0.5), 1),
            "win_rate": round(quantile(win_rates, 0.5), 1) if win_rates else None,
            # Every repetition's total, lowest first: what a picture of them is drawn from.
            "totals": totals,
        }


def _replay(simulator: Simulator, bars: list[IndexBar], picks: list[tuple[int, str]]) -> None:
    """
    The day for one simulator: each entry decided at the end of its bar. The
    bars in which nothing is open and nothing waits are passed over: they
    change nothing for it.
    """
    place = 0
    for pick, direction in picks:
        while place <= pick and simulator.busy:
            simulator.on_bar(bars[place])
            place += 1
        place = pick + 1
        simulator.decide(Decision(bars[pick].start + BAR_SECONDS, direction, RANDOM_ENTRY))
    while place < len(bars) and simulator.busy:
        simulator.on_bar(bars[place])
        place += 1

