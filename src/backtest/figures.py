"""
What a run's trades and days add up to, and how far that can be trusted:
how many trades and days the figures rest on, and the range the total would
have been in with other days of the same kind (the days drawn again, with
replacement).

Percentages count every trade as the whole stake, as WATA counts a day
(``compounded``). The money is the account's: what each order really bought
with what the account held, and what each sale gave.
"""

import hashlib
import os
import random
from collections import Counter
from statistics import median, pstdev

from src.backtest.robustness import risk_figures
from src.backtest.rules import compounded

# Rules of thumb, not thresholds of a test: under thirty trades a win rate is
# known to twenty points or so, and a month of days says little about the next.
TOO_FEW_TRADES, TOO_FEW_DAYS = 30, 20
FEW_TRADES, FEW_DAYS = 100, 60
TOO_FEW, FEW, FAIR = "too_few", "few", "fair"

RESAMPLES = 2000
RANGE_LEVEL = 90
# Under this many days, drawing them again says nothing a look at them does not.
RESAMPLE_LEAST_DAYS = 5
RESAMPLE_SEED = 20261012


def quantile(values: list[float], share: float) -> float | None:
    """The value under which ``share`` (0 to 1) of ``values`` fall, between two of them when it has to be."""
    if not values:
        return None
    ordered = sorted(values)
    place = share * (len(ordered) - 1)
    under = int(place)
    over = min(under + 1, len(ordered) - 1)
    return ordered[under] + (ordered[over] - ordered[under]) * (place - under)


def _rounded(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def sample_size(trades: int, days: int) -> str:
    """How much the figures of a run rest on."""
    if trades < TOO_FEW_TRADES or days < TOO_FEW_DAYS:
        return TOO_FEW
    if trades < FEW_TRADES or days < FEW_DAYS:
        return FEW
    return FAIR


def longest_losing_streak(performances: list[float]) -> int:
    """The most trades lost one after the other."""
    longest = current = 0
    for performance in performances:
        current = current + 1 if performance <= 0 else 0
        longest = max(longest, current)
    return longest


def worst_drawdown(day_percents: list[float]) -> float:
    """The deepest fall from a peak of the days' results put end to end, in percent (0 or below)."""
    value = peak = 1.0
    worst = 0.0
    for percent in day_percents:
        value *= 1 + percent / 100.0
        peak = max(peak, value)
        worst = min(worst, value / peak - 1)
    return round(worst * 100, 2)


def total_range(
    day_percents: list[float], *, resamples: int = RESAMPLES, level: int = RANGE_LEVEL, seed: int = RESAMPLE_SEED,
) -> dict | None:
    """
    Where the total falls when the same number of days is drawn again from the
    days tested, with replacement: ``level`` percent of the draws are between
    ``low`` and ``high``. It tells how much the total owes to which days there
    were; it knows nothing of days unlike any of them.
    """
    count = len(day_percents)
    if count < RESAMPLE_LEAST_DAYS:
        return None
    # The same days give the same range, run after run.
    draw = random.Random(seed)
    factors = [1 + percent / 100.0 for percent in day_percents]
    totals = []
    for _ in range(resamples):
        value = 1.0
        for factor in draw.choices(factors, k=count):
            value *= factor
        totals.append((value - 1) * 100)
    tail = (100 - level) / 200.0
    return {
        "low": _rounded(quantile(totals, tail)),
        "high": _rounded(quantile(totals, 1 - tail)),
        "level": level,
        "resamples": resamples,
        # How often the draw ended in a loss.
        "losing_share": round(sum(1 for total in totals if total < 0) * 100 / resamples, 1),
    }


def trade_figures(trades: list[dict], day_percents: list[float], days_with_an_entry: int, *, with_range: bool = True) -> dict:
    """The figures of some trades and of the days they were made on."""
    performances = [trade["performance_percent"] for trade in trades]
    wins = [performance for performance in performances if performance > 0]
    losses = [performance for performance in performances if performance <= 0]
    # What a stop has to leave room for, and what a target could have taken.
    winners_lowest = [
        trade["min_performance_percent"] for trade in trades
        if trade["performance_percent"] > 0 and trade.get("min_performance_percent") is not None
    ]
    losers_highest = [trade["max_performance_percent"] for trade in trades if trade["performance_percent"] <= 0]
    return {
        "days": len(day_percents),
        "days_with_an_entry": days_with_an_entry,
        "trades": len(trades),
        "sample": sample_size(len(trades), len(day_percents)),
        "win_rate": round(len(wins) * 100 / len(trades), 1) if trades else None,
        "average_win": round(sum(wins) / len(wins), 2) if wins else None,
        "average_loss": round(sum(losses) / len(losses), 2) if losses else None,
        # What a trade brings on average, wins and losses together.
        "expectancy": round(sum(performances) / len(trades), 2) if trades else None,
        # What was won for 1 lost. None without a loss to divide by.
        "profit_factor": round(sum(wins) / -sum(losses), 2) if sum(losses) < 0 else None,
        "longest_losing_streak": longest_losing_streak(performances),
        # Each day's result applied on the day before, as WATA counts a day: every trade as the whole stake.
        "total_percent": compounded(day_percents),
        "total_range": total_range(day_percents) if with_range else None,
        "best_day": max(day_percents, default=None),
        "worst_day": min(day_percents, default=None),
        "median_day": _rounded(median(day_percents)) if day_percents else None,
        "day_spread": _rounded(pstdev(day_percents)) if len(day_percents) > 1 else None,
        "winning_days": sum(1 for percent in day_percents if percent > 0),
        "losing_days": sum(1 for percent in day_percents if percent < 0),
        "worst_drawdown": worst_drawdown(day_percents),
        # Half of the winning trades never fell below ``median``, nine in ten never below ``tenth``.
        "winners_lowest": {
            "median": _rounded(quantile(winners_lowest, 0.5)), "tenth": _rounded(quantile(winners_lowest, 0.1)),
        } if winners_lowest else None,
        # Half of the losing trades had been above ``median``, one in ten above ``tenth``.
        "losers_highest": {
            "median": _rounded(quantile(losers_highest, 0.5)), "tenth": _rounded(quantile(losers_highest, 0.9)),
        } if losers_highest else None,
        "exits": dict(Counter(trade["exit"]["group"] for trade in trades).most_common()),
        "risk": risk_figures(day_percents, trades),
    }


def money(trades: list[dict], days: list[dict], capital: float, signals: list[dict]) -> dict:
    """The account: what the orders bought with what it held, and what each sale gave."""
    final = capital
    peak = capital
    deepest = deepest_percent = 0.0
    for day in days:
        if day.get("equity") is None:
            continue
        final = day["equity"]
        peak = max(peak, final)
        deepest = min(deepest, final - peak)
        deepest_percent = min(deepest_percent, (final / peak - 1) * 100 if peak else 0)
    stakes = [trade["cost"] for trade in trades]
    return {
        "capital": capital,
        "final": round(final, 2),
        "profit_loss": round(final - capital, 2),
        "return_percent": round((final / capital - 1) * 100, 2) if capital else None,
        "worst_drawdown": round(deepest, 2),
        "worst_drawdown_percent": round(deepest_percent, 2),
        "average_stake": round(sum(stakes) / len(stakes), 2) if stakes else None,
        # What WATA's sizing put on a trade on average, of what an order may use.
        "average_scale_percent": round(sum(trade["scale_percent"] for trade in trades) / len(trades), 1) if trades else None,
        "commissions": round(sum(trade["commission"] for trade in trades), 2),
        # Entries the account could not buy a single turbo for.
        "unfunded": sum(1 for signal in signals if signal.get("rule") == "insufficient_funds"),
    }


def signal_tallies(signals: list[dict]) -> dict:
    """What became of the signals: by fate, and what refused or failed the entries."""
    return {
        "total": len(signals),
        "by_fate": dict(Counter(signal["fate"] for signal in signals).most_common()),
        "by_source": dict(Counter(signal["source"] for signal in signals).most_common()),
        "refusals": dict(Counter(signal["rule"] for signal in signals if signal["fate"] == "refused").most_common()),
        "failures": dict(Counter(
            signal["rule"] for signal in signals if signal["fate"] in ("failed", "unpriced")
        ).most_common()),
    }


def curve(days: list[dict]) -> list[dict]:
    """Day after day, its result and the run's so far, in percent and in money."""
    points, total = [], 1.0
    for day in days:
        total *= 1 + day["day_percent"] / 100.0
        points.append({
            "day": day["day"],
            "percent": day["day_percent"],
            "cumulative": round((total - 1) * 100, 2),
            "profit_loss": day["profit_loss"],
            "equity": day["equity"],
        })
    return points


def against_random(total: float, totals: list[float], *, seed: int, entries: int) -> dict | None:
    """Where the run's total stands among the same days traded with entries at random."""
    if not totals:
        return None
    ordered = sorted(totals)
    tail = (100 - RANGE_LEVEL) / 200.0
    return {
        "repetitions": len(totals),
        "seed": seed,
        "entries": entries,
        # The share of the random runs this one did better than (ties counted half).
        "beaten": round((sum(1 for value in totals if value < total) + 0.5 * sum(1 for value in totals if value == total))
                        * 100 / len(totals), 1),
        "median": _rounded(quantile(ordered, 0.5)),
        "low": _rounded(quantile(ordered, tail)),
        "high": _rounded(quantile(ordered, 1 - tail)),
        "level": RANGE_LEVEL,
        "worst": _rounded(ordered[0]),
        "best": _rounded(ordered[-1]),
    }


def histogram(values: list[float], bins: int = 24) -> list[dict]:
    """The values in bins of equal width, for a chart."""
    if not values:
        return []
    low, high = min(values), max(values)
    if high == low:
        return [{"from": round(low, 2), "to": round(high, 2), "count": len(values)}]
    width = (high - low) / bins
    counts = [0] * bins
    for value in values:
        counts[min(int((value - low) / width), bins - 1)] += 1
    return [
        {"from": round(low + width * place, 2), "to": round(low + width * (place + 1), 2), "count": count}
        for place, count in enumerate(counts)
    ]


# Of the TA engine, the files a replay runs: its live loop (engine.py, what it measures of itself, how it
# sends, the service) decides nothing a backtest finds, and a change there leaves every run as it was.
# A file of src/ta_engine that the code listed here imports must be listed too (tests/test_backtest_code_digest.py).
_CODE = ("src/backtest", "src/ta_engine/replay.py", "src/ta_engine/rules.py", "src/ta_engine/runner.py",
         "src/ta_engine/tickers.py", "src/ta_engine/signals.py", "src/ta_engine/settings.py", "src/ta_engine/bars.py",
         "src/ta_engine/source.py", "src/price_recorder/bars.py", "src/price_recorder/range_bars.py",
         "src/trade/async_services.py", "src/trade/fresh_quote.py", "src/trade/macro_calendar.py",
         "src/trade/market_calendar.py",
         "src/admin/analysis.py")


def code_digest(root: str | None = None) -> str:
    """A digest of the code a run's result rests on: the backtest, the replay of the scripts, and the live helpers."""
    root = root or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    digest = hashlib.sha256()
    for part in _CODE:
        path = os.path.join(root, part)
        files = (
            sorted(os.path.join(folder, name) for folder, _, names in os.walk(path) for name in names if name.endswith(".py"))
            if os.path.isdir(path) else [path]
        )
        for name in files:
            try:
                with open(name, "rb") as in_file:
                    digest.update(os.path.relpath(name, root).encode())
                    digest.update(in_file.read())
            except OSError:
                continue
    return digest.hexdigest()[:16]
