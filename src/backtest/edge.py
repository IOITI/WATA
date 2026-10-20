"""
Whether a strategy's entries point the right way before WATA's exits have a
say: after each entry signal, taken or not, how its asset moved 1 to 60
minutes after the trader saw it, signed by the signal's direction, in basis
points of the asset's price; beside the same measure at moments drawn at
random in the same hours of the same days. Pure: fed a day at a time.

What a trade made depends on the turbo, the stop, the target and the day's
rules. What the asset did after the signal depends on the signal alone: a
strategy whose signals move the asset no further than random moments do has
no edge for the exits to keep, whatever its trades made.

The t-statistic is the mean move of every signal over its spread, with only
the signals whose windows do not overlap counted as observations (taken in
order, each at least the horizon after the one kept before): two signals a
minute apart measure the same move twice.
"""

import math
import random
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass, field
from statistics import fmean, median, stdev

from src.backtest.clock import TradingClock
from src.backtest.data import BAR_SECONDS, AssetSeries
from src.backtest.simulator import ENTRY_ACTIONS, LONG, SignalEvent

HORIZONS_MINUTES = (1, 5, 15, 30, 60)
# Random moments drawn for each signal, in its hour of its day.
RANDOM_DRAWS = 20
EDGE_SEED = 20261020
# A last price older than this at a moment is no price there: the asset was not quoted.
STALE_SECONDS = 120


def _utc(moment: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(moment, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _place(series: AssetSeries, moment: float) -> int | None:
    """The bar whose close is the price at ``moment``: the last one ended by then, if recent."""
    place = bisect_right(series.ts, moment - BAR_SECONDS) - 1
    if place < 0 or moment - (series.ts[place] + BAR_SECONDS) > STALE_SECONDS:
        return None
    return place


def move_at(series: AssetSeries, moment: float, direction: str, minutes: int) -> float | None:
    """The asset's move from ``moment`` to ``minutes`` later, in basis points, positive in ``direction``."""
    start = _place(series, moment)
    end = _place(series, moment + minutes * 60)
    if start is None or end is None or end <= start:
        return None
    sign = 1 if direction == LONG else -1
    return sign * (series.close[end] - series.close[start]) / series.close[start] * 10_000


def moves_after(series: AssetSeries, moment: float, direction: str) -> dict[int, tuple[float, float, float]] | None:
    """
    For each horizon: the move at its end, the best and the worst on the way
    (from the closes of ten-second bars), in basis points in ``direction``.
    None when the asset had no price at ``moment``.
    """
    start = _place(series, moment)
    if start is None:
        return None
    price = series.close[start]
    sign = 1 if direction == LONG else -1
    found = {}
    for minutes in HORIZONS_MINUTES:
        end = _place(series, moment + minutes * 60)
        if end is None or end <= start:
            continue
        path = [sign * (value - price) / price * 10_000 for value in series.close[start + 1:end + 1]]
        found[minutes] = (path[-1], max(path), min(path))
    return found


def _rounded(value: float | None, digits: int = 2) -> float | None:
    return None if value is None or not math.isfinite(value) else round(value, digits)


def _independent(signals: list[dict], minutes: int) -> int:
    """How many signals have windows that do not overlap, taken in time order."""
    kept, last = 0, None
    for signal in sorted(signals, key=lambda found: found["moment"]):
        if minutes not in signal["moves"]:
            continue
        if last is None or signal["moment"] >= last + minutes * 60:
            kept += 1
            last = signal["moment"]
    return kept


def _t(values: list[float], observations: int) -> float | None:
    """The mean over its standard error, ``observations`` of the values counted as independent."""
    if observations < 3 or len(values) < 2:
        return None
    spread = stdev(values)
    return fmean(values) / (spread / math.sqrt(observations)) if spread > 0 else None


def _summary(signals: list[dict], minutes: int) -> dict:
    values = [signal["moves"][minutes][0] for signal in signals if minutes in signal["moves"]]
    return {
        "signals": len(values),
        "mean_bp": _rounded(fmean(values)) if values else None,
        "hit_rate": _rounded(sum(1 for value in values if value > 0) / len(values) * 100, 1) if values else None,
    }


@dataclass
class EdgeMeter:
    """The moves after a run's entry signals, fed a day at a time, and after random moments of the same hours."""

    clock: TradingClock
    seed: int = EDGE_SEED
    draws: int = RANDOM_DRAWS
    signals: list[dict] = field(default_factory=list)
    # By horizon, by draw: the moves after the random moments.
    random: dict[int, list[list[float]]] = field(
        default_factory=lambda: {minutes: [[] for _ in range(RANDOM_DRAWS)] for minutes in HORIZONS_MINUTES}
    )
    unpriced: int = 0

    def add_day(self, day: str, series: AssetSeries | None, events: list[SignalEvent]) -> None:
        entries = [event for event in events if event.action in ENTRY_ACTIONS]
        if not entries:
            return
        if series is None or len(series.ts) == 0:
            self.unpriced += len(entries)
            return
        for event in entries:
            found = moves_after(series, event.arrived_at, event.action)
            if found is None:
                self.unpriced += 1
                continue
            local = self.clock.local(event.arrived_at)
            self.signals.append({
                "id": event.id, "day": day, "action": event.action, "moment": event.arrived_at,
                "hour": local.hour, "moves": found,
            })
            hour_start = event.arrived_at - (local.minute * 60 + local.second + local.microsecond / 1e6)
            draw = random.Random(f"{self.seed}:{day}:{event.id}")
            for repetition in range(self.draws):
                moment = hour_start + draw.random() * 3600
                for minutes in HORIZONS_MINUTES:
                    value = move_at(series, moment, event.action, minutes)
                    if value is not None:
                        self.random[minutes][repetition].append(value)

    def report(self, fates: dict[int, str] | None = None, day_labels: dict[str, dict] | None = None) -> dict:
        """
        What the entries' moves say, horizon by horizon, against the random
        moments; then split by hour, direction, whether the trader took them,
        and by the kind of day (``day_labels``: a day's groups, src/backtest/regimes.py).
        """
        fates = fates or {}
        day_labels = day_labels or {}
        horizons = []
        for minutes in HORIZONS_MINUTES:
            values = [signal["moves"][minutes][0] for signal in self.signals if minutes in signal["moves"]]
            best = [signal["moves"][minutes][1] for signal in self.signals if minutes in signal["moves"]]
            worst = [signal["moves"][minutes][2] for signal in self.signals if minutes in signal["moves"]]
            independent = _independent(self.signals, minutes)
            mean = fmean(values) if values else None
            drawn = [fmean(repetition) for repetition in self.random[minutes] if repetition]
            pooled = [value for repetition in self.random[minutes] for value in repetition]
            horizons.append({
                "minutes": minutes,
                "signals": len(values),
                "mean_bp": _rounded(mean),
                "median_bp": _rounded(median(values)) if values else None,
                "hit_rate": _rounded(sum(1 for value in values if value > 0) / len(values) * 100, 1) if values else None,
                "best_bp": _rounded(fmean(best)) if best else None,
                "worst_bp": _rounded(fmean(worst)) if worst else None,
                "independent": independent,
                "t": _rounded(_t(values, min(independent, len(values)))),
                "random": {
                    "mean_bp": _rounded(fmean(pooled)) if pooled else None,
                    "hit_rate": _rounded(sum(1 for value in pooled if value > 0) / len(pooled) * 100, 1) if pooled else None,
                    # The share of the draws whose mean the signals' beat.
                    "beaten": _rounded(sum(1 for value in drawn if value < mean) / len(drawn) * 100, 1)
                    if drawn and mean is not None else None,
                    "draws": len(drawn),
                },
            })
        hours: dict[int, list[dict]] = defaultdict(list)
        directions: dict[str, list[dict]] = defaultdict(list)
        taken: dict[str, list[dict]] = defaultdict(list)
        for signal in self.signals:
            hours[signal["hour"]].append(signal)
            directions[signal["action"]].append(signal)
            taken["executed" if fates.get(signal["id"]) == "executed" else "not_taken"].append(signal)

        def split(groups: dict, name: str) -> list[dict]:
            return [
                {name: key, **{str(minutes): _summary(group, minutes) for minutes in HORIZONS_MINUTES}}
                for key, group in sorted(groups.items())
            ]

        kinds: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
        for signal in self.signals:
            for kind, label in (day_labels.get(signal["day"]) or {}).items():
                if label is not None:
                    kinds[kind][label].append(signal)

        return {
            "horizons": horizons,
            "by_hour": split(hours, "hour"),
            "by_direction": split(directions, "direction"),
            "by_fate": split(taken, "fate"),
            "by_day": {kind: split(groups, "group") for kind, groups in sorted(kinds.items())},
            "unpriced": self.unpriced,
            "draws": self.draws,
            "seed": self.seed,
            "signals": [
                {
                    "id": signal["id"], "day": signal["day"], "action": signal["action"], "at": _utc(signal["moment"]),
                    "fate": fates.get(signal["id"]),
                    **{key: {str(minutes): _rounded(found[place]) for minutes, found in signal["moves"].items()}
                       for place, key in enumerate(("move_bp", "best_bp", "worst_bp"))},
                }
                for signal in self.signals
            ],
        }


def headline(report: dict) -> dict:
    """What a run's summary keeps of its edge."""
    return {
        "horizons": [
            {key: row[key] for key in ("minutes", "signals", "mean_bp", "hit_rate", "t", "independent")}
            | {"random_mean_bp": row["random"]["mean_bp"], "beaten": row["random"]["beaten"]}
            for row in report["horizons"]
        ],
        "unpriced": report["unpriced"],
    }
