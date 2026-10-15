"""
What the model of WATA did with the real signals, set against what WATA did.

Each real trade is paired with the model's trade entered in the same direction
at about the same moment. A pair says how far the model's result is from the
real one, and whether both ended the same way; a trade without its pair says
that one of the two entered where the other did not.

The gaps found here are what every backtest carries: they are the margin its
figures are to be read with.

Pure logic: no clock of its own, no I/O.
"""

from dataclasses import asdict, dataclass
from datetime import date
from statistics import mean, median

from src.strategy.engine import LocalClock
from src.strategy.execution import compounded
from src.strategy.report import quantile

# The model enters at the first bar after the signal, WATA when its order is filled.
ENTRY_TOLERANCE_SECONDS = 120


@dataclass(frozen=True)
class Leg:
    """A trade, real or of the model, in what the two have in common."""

    direction: str
    entered_at: int
    left_at: int
    # Percent on the turbo.
    performance: float
    # How it ended, in the words both sides are given (see ``exit_group`` of the admin).
    exit: str
    # The position at Saxo, for a real trade.
    reference: str = ""


def _pairs(real: list[Leg], model: list[Leg], tolerance: int) -> tuple[list[tuple[Leg, Leg]], list[Leg], list[Leg]]:
    free = sorted(model, key=lambda leg: leg.entered_at)
    pairs: list[tuple[Leg, Leg]] = []
    alone: list[Leg] = []
    for leg in sorted(real, key=lambda leg: leg.entered_at):
        candidates = [
            other for other in free
            if other.direction == leg.direction and abs(other.entered_at - leg.entered_at) <= tolerance
        ]
        if not candidates:
            alone.append(leg)
            continue
        nearest = min(candidates, key=lambda other: abs(other.entered_at - leg.entered_at))
        free.remove(nearest)
        pairs.append((leg, nearest))
    return pairs, alone, free


def _days(legs: list[Leg], clock: LocalClock) -> dict[date, list[float]]:
    """The results of each day, in the order they were closed: a trade belongs to the day it ended on."""
    days: dict[date, list[float]] = {}
    for leg in sorted(legs, key=lambda leg: leg.left_at):
        days.setdefault(clock.at(leg.left_at)[0], []).append(leg.performance)
    return days


def compare(
    real: list[Leg], model: list[Leg], clock: LocalClock, *, tolerance: int = ENTRY_TOLERANCE_SECONDS,
) -> dict:
    pairs, only_real, only_model = _pairs(real, model, tolerance)
    gaps = [round(theirs.performance - ours.performance, 2) for ours, theirs in pairs]
    apart = [abs(gap) for gap in gaps]

    real_days, model_days = _days(real, clock), _days(model, clock)
    days = []
    for day in sorted(set(real_days) | set(model_days)):
        ours, theirs = compounded(real_days.get(day, [])), compounded(model_days.get(day, []))
        days.append({
            "day": day.isoformat(),
            "real_percent": ours,
            "model_percent": theirs,
            "gap": round(theirs - ours, 2),
            "real_trades": len(real_days.get(day, [])),
            "model_trades": len(model_days.get(day, [])),
        })
    day_gaps = [abs(day["gap"]) for day in days]

    return {
        "summary": {
            "real_trades": len(real),
            "model_trades": len(model),
            "paired": len(pairs),
            # Model minus real, in points of percent: above zero, the model flatters.
            "average_gap": round(mean(gaps), 2) if gaps else None,
            # Half of the pairs are closer than this, nine in ten closer than ``ninth_gap``.
            "median_gap": round(median(apart), 2) if apart else None,
            "ninth_gap": round(quantile(apart, 0.9), 2) if apart else None,
            "same_exit": sum(1 for ours, theirs in pairs if ours.exit == theirs.exit),
            "real_total": compounded([day["real_percent"] for day in days]),
            "model_total": compounded([day["model_percent"] for day in days]),
            "days": len(days),
            "median_day_gap": round(median(day_gaps), 2) if day_gaps else None,
        },
        "pairs": [
            {
                "real": asdict(ours),
                "model": asdict(theirs),
                "gap": gap,
                "same_exit": ours.exit == theirs.exit,
                # Seconds the model held its trade longer than WATA did (negative: shorter).
                "left_later": theirs.left_at - ours.left_at,
            }
            for (ours, theirs), gap in zip(pairs, gaps)
        ],
        # Entered by WATA and not by the model, and the other way round.
        "only_real": [asdict(leg) for leg in only_real],
        "only_model": [asdict(leg) for leg in only_model],
        "days": days,
    }
