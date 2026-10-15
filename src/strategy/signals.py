"""
Signals WATA really received, replayed as the trader was given them.

A backtest of a strategy says what its signals would have done. This one takes
the signals there really were (the trader's journal, ``signal_journal``) and
runs them through the same model of WATA: what comes out can be set against
what WATA really did, trade by trade. It is the one test the model itself can
be put to.

A signal is an order to the trader, not a position wanted: each one is passed
on, the second "long" in a row as well, and the trader's rules make of it what
they would (``Simulator(entries_are_orders=True)``).

Pure logic but for ``read``: no clock of its own.
"""

import json
from datetime import datetime, timezone
from typing import Callable, Iterable

from src.price_recorder.bars import BAR_SECONDS
from src.strategy.bars import IndexBar
from src.strategy.engine import FLAT, LONG, SHORT, Decision

SIGNAL = "signal"
# What the trader does with each action it is sent: the position asked for, and for a close the side it ends.
ACTIONS = {
    "long": (LONG, None),
    "short": (SHORT, None),
    "close-position": (FLAT, None),
    "close-long": (FLAT, LONG),
    "close-short": (FLAT, SHORT),
}
# A signal that came while the prices paused for less than this is acted on at the next one.
MAX_WAIT_SECONDS = 60


def _epoch(moment) -> int:
    if isinstance(moment, (int, float)):
        return int(moment)
    text = str(moment).replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def decisions_of(signals: Iterable[dict]) -> list[Decision]:
    """
    Signals as decisions, oldest first. Each has ``at`` (epoch seconds, or a
    time written with its offset) and ``action``, and may have ``confidence``.
    """
    decisions = []
    for signal in signals:
        action = signal.get("action")
        if action not in ACTIONS:
            raise ValueError(f"Not an action the trader takes: '{action}'")
        target, closes = ACTIONS[action]
        confidence = signal.get("confidence")
        decisions.append(Decision(
            at=_epoch(signal["at"]),
            target=target,
            reason=SIGNAL if target != FLAT else action,
            confidence=None if confidence is None else float(confidence),
            closes=closes,
        ))
    return sorted(decisions, key=lambda decision: decision.at)


def read(path: str) -> list[Decision]:
    """The signals written in a file: a JSON list (see ``decisions_of``)."""
    with open(path, encoding="utf-8") as in_file:
        content = json.load(in_file)
    if not isinstance(content, list):
        raise ValueError(f"{path} is not a list of signals")
    try:
        return decisions_of(content)
    except (KeyError, TypeError) as e:
        raise ValueError(f"{path} holds a signal without its time or its action: {e}") from e


class Replay:
    """
    Signals handed over a bar at a time: what ``run`` does, for whoever gives
    the same signals to several executions at once. ``begin`` before the bar
    is acted on, ``end`` after, ``finish`` when there are no more bars.
    """

    def __init__(self, signals: list[Decision], on_decision: Callable[[Decision], None]):
        self._signals = signals
        self._on_decision = on_decision
        self._place = 0
        self.passed: list[Decision] = []
        self.without_prices: list[Decision] = []

    def begin(self, bar: IndexBar) -> None:
        while self._place < len(self._signals) and self._signals[self._place].at < bar.start:
            signal = self._signals[self._place]
            self._place += 1
            if bar.start - signal.at <= MAX_WAIT_SECONDS:
                # Acted on at the open of this bar: the first price there is after it.
                self.passed.append(signal)
                self._on_decision(signal)
            else:
                self.without_prices.append(signal)

    def end(self, bar: IndexBar) -> None:
        end = bar.start + BAR_SECONDS
        while self._place < len(self._signals) and self._signals[self._place].at < end:
            self.passed.append(self._signals[self._place])
            self._on_decision(self._signals[self._place])
            self._place += 1

    def finish(self) -> None:
        self.without_prices += self._signals[self._place:]
        self._place = len(self._signals)


def run(
    signals: list[Decision],
    bars: Iterable[IndexBar],
    *,
    on_bar: Callable[[IndexBar], None],
    on_decision: Callable[[Decision], None],
) -> tuple[list[Decision], list[Decision]]:
    """
    Give each signal to ``on_decision`` once the bar it came in is over, as a
    strategy's decision is: whoever executes acts at the open of the next bar.

    Returns the signals passed on, and those that came when there were no
    prices (more than a minute before the next bar, or after the last one):
    nothing can be made of those.
    """
    replay = Replay(signals, on_decision)
    for bar in bars:
        replay.begin(bar)
        on_bar(bar)
        replay.end(bar)
    replay.finish()
    return replay.passed, replay.without_prices
