"""
Whether a result holds, beyond the days it was found on. Pure: each takes the
days' results (a day's result in percent, as WATA counts a day) and answers
in plain figures.

- ``risk_figures``: what the daily results say of the risk taken (Sharpe,
  Sortino, Calmar, time under water, the worst days).
- ``walk_forward``: the combinations of a sweep chosen window after window on
  the days before, and judged on the days after, stitched.
- ``deflated_sharpe``: the probability that a Sharpe ratio is above zero once
  the number of configurations tried is accounted for (Bailey and López de
  Prado, 2014).
- ``pbo``: the probability that choosing the best combination of a sweep
  chose one that does worse than half of them on other days (combinatorially
  symmetric cross-validation, Bailey, Borwein, López de Prado and Zhu, 2015).
- ``monte_carlo_drawdown`` and ``without_best_days``: the drawdown the same
  days give in another order, and the total without its best days.
"""

import math
import random
from itertools import combinations
from statistics import NormalDist, fmean, pstdev, stdev

from src.backtest.rules import compounded

TRADING_DAYS = 252
# Fewer days than a month of trading, a figure brought to a year says nothing: it is not given.
ANNUALISED_FROM_DAYS = 20
EULER_GAMMA = 0.5772156649015329
MONTE_CARLO_DRAWS = 1000
MONTE_CARLO_SEED = 20261020
# Splits of the days for the cross-validation: C(8, 4) = 70 ways of halving them.
PBO_SPLITS = 8
PBO_LEAST_DAYS = 8

_NORMAL = NormalDist()


def _rounded(value: float | None, digits: int = 2) -> float | None:
    return None if value is None or not math.isfinite(value) else round(value, digits)


def _drawdown(day_percents: list[float]) -> float:
    """The deepest fall from a peak of the days applied one on the other, in percent (0 or less)."""
    level = peak = 1.0
    deepest = 0.0
    for percent in day_percents:
        level *= 1 + percent / 100
        peak = max(peak, level)
        deepest = min(deepest, (level / peak - 1) * 100)
    return deepest


def _moments(returns: list[float]) -> tuple[float, float]:
    """Skewness and kurtosis (not in excess: 3 for a normal law) of some returns."""
    if len(returns) < 3:
        return 0.0, 3.0
    mean, deviation = fmean(returns), pstdev(returns)
    if deviation == 0:
        return 0.0, 3.0
    skew = fmean(((value - mean) / deviation) ** 3 for value in returns)
    kurtosis = fmean(((value - mean) / deviation) ** 4 for value in returns)
    return skew, kurtosis


def daily_sharpe(day_percents: list[float]) -> float | None:
    """The mean daily result over its standard deviation; None without two days that differ."""
    if len(day_percents) < 2:
        return None
    deviation = stdev(day_percents)
    return fmean(day_percents) / deviation if deviation > 0 else None


def risk_figures(day_percents: list[float], trades: list[dict]) -> dict:
    """
    What the days' results and the trades say of the risk taken. Annualised
    over 252 trading days, and only from ``ANNUALISED_FROM_DAYS`` days.
    """
    days = len(day_percents)
    annualised = days >= ANNUALISED_FROM_DAYS
    sharpe = daily_sharpe(day_percents) if annualised else None
    downside = math.sqrt(fmean(min(percent, 0.0) ** 2 for percent in day_percents)) if annualised else 0.0
    drawdown = _drawdown(day_percents)
    total = compounded(day_percents)
    yearly = ((1 + total / 100) ** (TRADING_DAYS / days) - 1) * 100 if annualised and total > -100 else None
    under = longest = 0
    level = peak = 1.0
    for percent in day_percents:
        level *= 1 + percent / 100
        if level >= peak:
            peak, under = level, 0
        else:
            under += 1
            longest = max(longest, under)
    worst = sorted(day_percents)[: max(1, math.ceil(days * 0.05))] if day_percents else []
    skew, kurtosis = _moments(day_percents)
    wins = [trade["performance_percent"] for trade in trades if trade["performance_percent"] > 0]
    losses = [trade["performance_percent"] for trade in trades if trade["performance_percent"] < 0]
    # Winners only: a loser's best is often a few hundredths, and its share kept would swamp the rest.
    kept = [
        trade["performance_percent"] / trade["max_performance_percent"]
        for trade in trades if trade["performance_percent"] > 0 and (trade.get("max_performance_percent") or 0) > 0
    ]
    return {
        "sharpe": _rounded(sharpe * math.sqrt(TRADING_DAYS) if sharpe is not None else None),
        "sortino": _rounded(fmean(day_percents) / downside * math.sqrt(TRADING_DAYS) if downside > 0 else None),
        "calmar": _rounded(yearly / -drawdown if yearly is not None and drawdown < 0 else None),
        "yearly_percent": _rounded(yearly),
        "longest_under_water_days": longest,
        "worst_5_percent_days": _rounded(fmean(worst) if worst else None),
        "skew": _rounded(skew),
        "kurtosis": _rounded(kurtosis),
        # What a winning trade won for what a losing one lost.
        "payoff": _rounded(fmean(wins) / -fmean(losses) if wins and losses else None),
        # Of the best a winning trade reached, the share it kept when it was sold.
        "exit_efficiency": _rounded(fmean(kept) * 100 if kept else None, 1),
        "trades_per_day": _rounded(len(trades) / days if days else None),
        "hours_held_per_day": _rounded(sum(trade.get("holding_seconds") or 0 for trade in trades) / 3600 / days if days else None),
    }


# ── a sweep, window after window ──


def walk_forward(curves: list[dict[str, float]], train_days: int, test_days: int, *, values: list[list] | None = None) -> dict | None:
    """
    Choose, at the start of each window, the combination with the best total
    on the ``train_days`` before it, then keep its result on the
    ``test_days`` that follow; stitched, those days are what choosing the
    best would have given, every day chosen without knowing it.

    ``curves`` holds, for each combination, its result by day (the same
    days for all). None when there are too few days for one window.
    """
    if not curves or train_days < 1 or test_days < 1:
        return None
    days = sorted(set().union(*curves))
    if len(days) < train_days + 1:
        return None
    windows, stitched, chosen_before, changes = [], [], None, 0
    start = 0
    while start + train_days < len(days):
        train = days[start:start + train_days]
        test = days[start + train_days:start + train_days + test_days]
        scores = [compounded([curve.get(day, 0.0) for day in train]) for curve in curves]
        best = max(range(len(curves)), key=lambda index: (scores[index], -index))
        outcomes = [compounded([curve.get(day, 0.0) for day in test]) for curve in curves]
        rank = 1 + sum(1 for outcome in outcomes if outcome > outcomes[best])
        if chosen_before is not None and best != chosen_before:
            changes += 1
        chosen_before = best
        stitched += [(day, curves[best].get(day, 0.0)) for day in test]
        windows.append({
            "train": [train[0], train[-1]], "test": [test[0], test[-1]], "cell": best,
            "values": values[best] if values else None,
            "train_percent": _rounded(scores[best]), "test_percent": _rounded(outcomes[best]),
            "test_rank": rank, "cells": len(curves),
        })
        start += test_days
    percents = [percent for _, percent in stitched]
    tested = [day for day, _ in stitched]
    hindsight = max(compounded([curve.get(day, 0.0) for day in tested]) for curve in curves)
    cumulative, running = [], 1.0
    for day, percent in stitched:
        running *= 1 + percent / 100
        cumulative.append([day, _rounded((running - 1) * 100)])
    return {
        "train_days": train_days,
        "test_days": test_days,
        "windows": windows,
        "days": len(stitched),
        "total_percent": _rounded(compounded(percents)),
        "worst_day": _rounded(min(percents)) if percents else None,
        "worst_drawdown": _rounded(_drawdown(percents)),
        "changes": changes,
        # The best single combination over the same days, known only afterwards.
        "best_in_hindsight_percent": _rounded(hindsight),
        "curve": cumulative,
    }


# ── how many were tried ──


def expected_maximum_sharpe(trials: int, variance: float) -> float:
    """The best Sharpe ratio ``trials`` configurations without any edge give, on average."""
    if trials <= 1 or variance <= 0:
        return 0.0
    return math.sqrt(variance) * (
        (1 - EULER_GAMMA) * _NORMAL.inv_cdf(1 - 1 / trials)
        + EULER_GAMMA * _NORMAL.inv_cdf(1 - 1 / (trials * math.e))
    )


def deflated_sharpe_probability(sharpe: float, days: int, trials: int, variance: float, skew: float, kurtosis: float) -> float | None:
    """
    The deflated Sharpe ratio itself: the probability that the true ratio is
    above the best one ``trials`` configurations without an edge would show,
    for a ratio ``sharpe`` measured on ``days`` returns of that ``skew`` and
    ``kurtosis`` (not in excess). Ratios and variance per return, not annualised.
    """
    spread = 1 - skew * sharpe + (kurtosis - 1) / 4 * sharpe ** 2
    if days < 2 or spread <= 0:
        return None
    expected = expected_maximum_sharpe(trials, variance)
    return _NORMAL.cdf((sharpe - expected) * math.sqrt(days - 1) / math.sqrt(spread))


def deflated_sharpe(day_percents: list[float], trials: int, *, trial_sharpes: list[float] | None = None) -> dict | None:
    """
    The probability that the true Sharpe ratio of the days is above zero,
    knowing that ``trials`` configurations were tried and this one kept. The
    spread of the trials' ratios is that of ``trial_sharpes`` when given (a
    sweep's combinations), else that of the ratio itself, 1 / (days - 1).
    Daily ratios, not annualised. None with fewer than ``ANNUALISED_FROM_DAYS``
    days: its skew and tails would come from a handful of numbers.
    """
    days = len(day_percents)
    sharpe = daily_sharpe(day_percents)
    if days < ANNUALISED_FROM_DAYS or sharpe is None:
        return None
    known = [value for value in trial_sharpes or [] if value is not None]
    variance = (pstdev(known) ** 2) if len(known) > 1 else 1 / (days - 1)
    expected = expected_maximum_sharpe(max(1, trials), variance)
    skew, kurtosis = _moments(day_percents)
    probability = deflated_sharpe_probability(sharpe, days, max(1, trials), variance, skew, kurtosis)
    if probability is None:
        return None
    return {
        "probability": round(probability, 3),
        "sharpe_daily": round(sharpe, 4),
        "expected_max_sharpe_daily": round(expected, 4),
        "trials": max(1, trials),
        "days": days,
        "sentence": (
            f"With {max(1, trials)} configuration{'s' if trials != 1 else ''} tried, the chance that this one's "
            f"Sharpe ratio is above zero is {probability * 100:.0f} %"
            + (": it may well be luck." if probability < 0.95 else ".")
        ),
    }


def pbo(curves: list[dict[str, float]], *, splits: int = PBO_SPLITS) -> dict | None:
    """
    The probability that the combination best on half of the days does worse
    than the middle one on the other half, over every way of halving them in
    ``splits`` blocks. None with fewer than two combinations or ``PBO_LEAST_DAYS`` days.
    """
    if len(curves) < 2:
        return None
    days = sorted(set().union(*curves))
    if len(days) < PBO_LEAST_DAYS:
        return None
    splits = min(splits, len(days) - len(days) % 2)
    size = len(days) / splits
    blocks = [days[round(index * size):round((index + 1) * size)] for index in range(splits)]
    overfitted = total = 0
    logits = []
    for chosen in combinations(range(splits), splits // 2):
        inside = [day for index in chosen for day in blocks[index]]
        outside = [day for index in range(splits) if index not in chosen for day in blocks[index]]
        in_scores = [sum(curve.get(day, 0.0) for day in inside) for curve in curves]
        out_scores = [sum(curve.get(day, 0.0) for day in outside) for curve in curves]
        best = max(range(len(curves)), key=lambda index: (in_scores[index], -index))
        # Its relative rank on the other half: 1 is the best of them, near 0 the worst.
        below = sum(1 for score in out_scores if score < out_scores[best])
        ties = sum(1 for score in out_scores if score == out_scores[best]) - 1
        rank = (below + ties / 2 + 1) / (len(curves) + 1)
        logit = math.log(rank / (1 - rank))
        logits.append(logit)
        overfitted += logit <= 0
        total += 1
    probability = overfitted / total
    return {
        "probability": round(probability, 3),
        "splits": splits,
        "combinations": total,
        "sentence": (
            f"Choosing the best on half of the days chose one that did worse than the middle on the other half "
            f"{probability * 100:.0f} % of the {total} ways of halving them"
            + (": the choice is mostly fitting." if probability > 0.5 else ".")
        ),
    }


# ── the same days, otherwise ──


def monte_carlo_drawdown(day_percents: list[float], *, draws: int = MONTE_CARLO_DRAWS, seed: int = MONTE_CARLO_SEED) -> dict | None:
    """The worst drawdown the same days give, drawn again in another order with replacement."""
    if len(day_percents) < 5:
        return None
    draw = random.Random(seed)
    depths = sorted(_drawdown(draw.choices(day_percents, k=len(day_percents))) for _ in range(draws))
    return {
        "draws": draws,
        "median": _rounded(depths[draws // 2]),
        "worst_5_percent": _rounded(depths[max(0, int(draws * 0.05) - 1)]),
        "actual": _rounded(_drawdown(day_percents)),
    }


def without_best_days(day_percents: list[float], removed: tuple[int, ...] = (1, 2, 3)) -> list[dict]:
    """The total of the days without the best one, two, three of them."""
    ordered = sorted(day_percents, reverse=True)
    kept = []
    for count in removed:
        if count >= len(day_percents):
            break
        rest = list(day_percents)
        for best in ordered[:count]:
            rest.remove(best)
        kept.append({"removed": count, "total_percent": _rounded(compounded(rest))})
    return kept
