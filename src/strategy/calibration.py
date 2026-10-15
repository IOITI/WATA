"""
The turbo of the backtests, measured on WATA's real trades.

    python -m src.strategy.calibration --trades trades.json --out calibration.json

A backtest prices a turbo from the index (``TurboModel``): what is paid for it,
how many index points move it by 1.00, and the spread between its bid and its
ask. Until now the three were guesses. A closed trade of WATA tells what they
were: it was bought at one price and sold at another while the index moved by
so many points. Over many trades,

    sold - bought  =  (index move in the trade's direction) / points_per_unit  -  spread

is a straight line: its slope gives ``points_per_unit`` and where it starts
gives the spread. The price paid is read from the trades themselves.

The trades are given as a JSON list, each with ``direction``, ``opened_at``,
``closed_at``, ``open_price`` and ``close_price``; the index is read from the
history fetched for the backtests. What is found says how good it is: how many
trades it rests on, the range ``points_per_unit`` could be in, and how far the
line is from the trades it was drawn through.

The index of the history is another broker's, in dollars, and the turbo is
quoted in euros: a part of what the line leaves unexplained is the exchange
rate moving during a trade. It is in the error shown, not corrected.
"""

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import median
from typing import Callable

from src.strategy.engine import LONG, SHORT
from src.strategy.history.dukascopy import DEFAULT_LABEL, DEFAULT_ROOT, SOURCE as DUKASCOPY, bars_directory
from src.strategy.process import lower_priority
from src.strategy.report import quantile
from src.strategy.sources import IndexLookup

# Under this many trades no line is drawn at all.
LEAST_TRADES = 5
# Rules of thumb, as for a backtest: a line through a few dozen trades is a first idea of it.
TOO_FEW_TRADES, FEW_TRADES = 30, 100
TOO_FEW, FEW, FAIR = "too_few", "few", "fair"
RANGE_LEVEL = 90
# A trade further from the line than this many times the usual distance is set aside, once.
OUTLIER_DISTANCES = 3.5
# Student's t at 95 % on one side, by degrees of freedom: the range of a slope drawn through few points.
_T = ((1, 6.31), (2, 2.92), (3, 2.35), (4, 2.13), (5, 2.02), (6, 1.94), (8, 1.86), (10, 1.81), (15, 1.75), (20, 1.72), (30, 1.70))
_T_MANY = 1.645
# Two prices closer than this are the same price.
_SAME_PRICE = 1e-9


@dataclass(frozen=True)
class Observation:
    reference: str
    # What was paid for one turbo, and what it was sold for.
    open_price: float
    close_price: float
    # Index points gained in the direction of the trade.
    move: float

    @property
    def change(self) -> float:
        return self.close_price - self.open_price


def _epoch(moment) -> int:
    if isinstance(moment, (int, float)):
        return int(moment)
    parsed = datetime.fromisoformat(str(moment).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def observe(trades: list[dict], index_at: Callable[[int], float | None]) -> tuple[list[Observation], int, int]:
    """
    What each trade says of the turbo. Returns the observations, how many
    trades had no index price at their open or their close, and how many
    could not be read (no price, no time, a direction that is neither).
    """
    observations: list[Observation] = []
    without_prices = unreadable = 0
    for trade in trades:
        try:
            direction = trade["direction"]
            open_price, close_price = float(trade["open_price"]), float(trade["close_price"])
            opened_at, closed_at = _epoch(trade["opened_at"]), _epoch(trade["closed_at"])
        except (KeyError, TypeError, ValueError):
            unreadable += 1
            continue
        if direction not in (LONG, SHORT) or open_price <= 0 or close_price < 0 or closed_at < opened_at:
            unreadable += 1
            continue
        before, after = index_at(opened_at), index_at(closed_at)
        if before is None or after is None:
            without_prices += 1
            continue
        move = after - before if direction == LONG else before - after
        observations.append(Observation(str(trade.get("position_id", "")), open_price, close_price, move))
    return observations, without_prices, unreadable


def _line(observations: list[Observation]) -> tuple[float, float, float] | None:
    """Least squares of the price change on the index move: slope, start, and the slope's standard error."""
    count = len(observations)
    mean_move = sum(observation.move for observation in observations) / count
    mean_change = sum(observation.change for observation in observations) / count
    spread_of_moves = sum((observation.move - mean_move) ** 2 for observation in observations)
    if spread_of_moves == 0:
        return None
    slope = sum(
        (observation.move - mean_move) * (observation.change - mean_change) for observation in observations
    ) / spread_of_moves
    start = mean_change - slope * mean_move
    left = sum((observation.change - start - slope * observation.move) ** 2 for observation in observations)
    error = (left / (count - 2) / spread_of_moves) ** 0.5 if count > 2 else float("inf")
    return slope, start, error


def _rough_line(observations: list[Observation]) -> tuple[float, float] | None:
    """
    A first line that a few trades unlike the others cannot pull to them: the
    median of the slopes between each trade and the one half the trades
    further in index move, and the median of what that leaves.
    """
    ordered = sorted(observations, key=lambda observation: observation.move)
    half = len(ordered) // 2
    slopes = [
        (far.change - near.change) / (far.move - near.move)
        for near, far in zip(ordered, ordered[half:]) if far.move != near.move
    ]
    if not slopes:
        return None
    slope = median(slopes)
    return slope, median(observation.change - slope * observation.move for observation in observations)


def _t(degrees: int) -> float:
    for limit, value in reversed(_T):
        if degrees >= limit:
            return value if degrees <= 30 else _T_MANY
    return _T[0][1]


def sample_size(trades: int) -> str:
    if trades < TOO_FEW_TRADES:
        return TOO_FEW
    return FEW if trades < FEW_TRADES else FAIR


def fit(observations: list[Observation]) -> dict:
    """
    The turbo the observations describe. ``fitted`` is False, with the reason,
    when they describe none.
    """
    prices = [observation.open_price for observation in observations]
    result = {
        "fitted": False,
        "reason": None,
        "used": len(observations),
        "set_aside": 0,
        "sample": sample_size(len(observations)),
        # What WATA really paid for a turbo: half of the trades under ``entry_price``.
        "entry_price": round(median(prices), 3) if prices else None,
        "entry_price_low": round(quantile(prices, 0.1), 3) if prices else None,
        "entry_price_high": round(quantile(prices, 0.9), 3) if prices else None,
    }
    if len(observations) < LEAST_TRADES:
        return {**result, "reason": f"{len(observations)} trade(s) with prices: {LEAST_TRADES} at least are needed"}

    rough = _rough_line(observations)
    if rough is not None:
        slope, start = rough
        distances = [abs(observation.change - start - slope * observation.move) for observation in observations]
        usual = median(distances) * 1.4826
        kept = [
            observation for observation, distance in zip(observations, distances)
            if distance <= max(OUTLIER_DISTANCES * usual, _SAME_PRICE)
        ]
        if LEAST_TRADES <= len(kept) < len(observations):
            result["set_aside"] = len(observations) - len(kept)
            result["used"] = len(kept)
            observations = kept
    line = _line(observations) if rough is not None else None
    if line is None:
        return {**result, "reason": "The index moved by the same amount in every trade: no line can be drawn"}

    slope, start, error = line
    if slope <= 0:
        return {**result, "reason": "The turbos did not follow the index in these trades: the prices are not those of their index"}

    margin = _t(len(observations) - 2) * error
    points_per_unit = 1 / slope
    spread = max(0.0, -start)
    apart = sorted(
        abs((observation.move * slope - spread - observation.change) * 100 / observation.open_price)
        for observation in observations
    )
    return {
        **result,
        "fitted": True,
        "sample": sample_size(len(observations)),
        "points_per_unit": round(points_per_unit, 1),
        # Where it is with the trades there are: None when they do not even bound it.
        "points_per_unit_low": round(1 / (slope + margin), 1),
        "points_per_unit_high": round(1 / (slope - margin), 1) if slope > margin else None,
        "level": RANGE_LEVEL,
        "spread": round(spread, 3),
        # How far the turbo so described is from each real trade, in points of percent of its result.
        "median_error": round(quantile(apart, 0.5), 2),
        "ninth_error": round(quantile(apart, 0.9), 2),
    }


def calibrate(trades: list[dict], index_at: Callable[[int], float | None]) -> dict:
    observations, without_prices, unreadable = observe(trades, index_at)
    return {
        "trades": len(trades),
        "without_prices": without_prices,
        "unreadable": unreadable,
        **fit(observations),
    }


def describe(result: dict) -> str:
    lines = [
        f"{result['trades']} closed trade(s): {result['used']} used, {result['without_prices']} without index prices "
        f"at their open or close, {result['unreadable']} unreadable, {result['set_aside']} set aside as unlike the others."
    ]
    if result["entry_price"] is not None:
        lines.append(
            f"Paid for a turbo: {result['entry_price']:g} (most between {result['entry_price_low']:g} "
            f"and {result['entry_price_high']:g})."
        )
    if not result["fitted"]:
        lines.append(f"No turbo measured: {result['reason']}.")
        return "\n".join(lines)
    high = result["points_per_unit_high"]
    lines.append(
        f"1.00 of turbo for {result['points_per_unit']:g} index points "
        f"({result['level']} %: {result['points_per_unit_low']:g} to {'no upper bound' if high is None else f'{high:g}'}), "
        f"spread {result['spread']:g}."
    )
    lines.append(
        f"Half of the trades are within {result['median_error']:g} point(s) of percent of what this turbo gives, "
        f"nine in ten within {result['ninth_error']:g}."
    )
    if result["sample"] == TOO_FEW:
        lines.append(f"Under {TOO_FEW_TRADES} trades: a first idea, to be measured again when there are more.")
    return "\n".join(lines)


def parse_arguments(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m src.strategy.calibration",
        description="Measure the turbo of the backtests on WATA's real trades.",
    )
    parser.add_argument("--trades", required=True, help="JSON list of closed trades (direction, opened_at, closed_at, open_price, close_price)")
    parser.add_argument(
        "--source", default=DUKASCOPY,
        help=f"'{DUKASCOPY}' (what was fetched under --root), or a directory of files of bars named after days",
    )
    parser.add_argument("--root", default=DEFAULT_ROOT, help=f"where fetched history is kept (default: {DEFAULT_ROOT})")
    parser.add_argument("--indice", default=DEFAULT_LABEL)
    parser.add_argument("--out", help="file to write what was measured to, as JSON")
    parser.add_argument("--low-priority", action="store_true", help="give way to everything else on the machine")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    arguments = parse_arguments(argv)
    if arguments.low_priority:
        lower_priority()
    try:
        with open(arguments.trades, encoding="utf-8") as in_file:
            trades = json.load(in_file)
        if not isinstance(trades, list):
            raise ValueError("not a list of trades")
    except (OSError, ValueError) as e:
        print(f"The trades cannot be read from {arguments.trades}: {e}", file=sys.stderr)
        return 2
    location = bars_directory(arguments.root, arguments.indice) if arguments.source == DUKASCOPY else arguments.source
    result = calibrate(trades, IndexLookup(location, arguments.indice).at)
    print(describe(result))
    if arguments.out:
        os.makedirs(os.path.dirname(os.path.abspath(arguments.out)), exist_ok=True)
        partial = f"{arguments.out}.part"
        with open(partial, "w", encoding="utf-8") as out_file:
            json.dump(result, out_file, indent=2)
        os.replace(partial, arguments.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
