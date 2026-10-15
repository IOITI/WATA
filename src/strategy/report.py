"""
What a backtest shows: one summary per run, and the trades and the days as
CSV files.

A summary also says how far its own figures can be trusted: how many trades
and days they rest on, and the range the total would have been in with other
days of the same kind (the days drawn again, with replacement).

Percentages count every trade as the whole stake, as WATA counts a day. For a
starting capital, ``sized`` gives what WATA would have put on each trade and
what it brought, in money: the same trades, each at the size its time of day
and its confidence give it.
"""

import csv
import math
import os
import random
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
from statistics import median, pstdev

from src.strategy.engine import LocalClock
from src.strategy.execution import DayResult, Refusal, Simulator, Trade, TurboModel, WataRules, compounded

TRADE_COLUMNS = (
    "entry_time", "exit_time", "direction", "entry_reason", "confidence", "entry_index", "exit_index",
    "index_points", "performance_percent", "max_performance_percent", "exit_reason", "min_performance_percent",
    "scale_percent", "stake", "profit_loss",
)
DAY_COLUMNS = ("day", "entries", "closed", "day_percent", "profit_loss", "equity")
REFUSAL_COLUMNS = ("time", "direction", "reason")


def utc(at: int) -> str:
    return datetime.fromtimestamp(at, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def worst_drawdown(days: list[DayResult]) -> float:
    """The deepest fall from a peak of the days' results put end to end, in percent (0 or below)."""
    value = peak = 1.0
    worst = 0.0
    for day in days:
        value *= 1 + day.percent / 100.0
        peak = max(peak, value)
        worst = min(worst, value / peak - 1)
    return round(worst * 100, 2)


def market_days(days: list[DayResult]) -> list[DayResult]:
    """
    The days that had prices inside the market hours. A Sunday evening of
    quotes, or the first hours of a day whose prices were not fetched, is not
    a day that was tested.
    """
    return [day for day in days if day.market_bars]


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


@dataclass(frozen=True)
class Sized:
    """A trade in money: what was put on it, what it brought, and the account after it."""

    stake: float
    profit_loss: float
    equity: float


def sized(trades: list[Trade], rules: WataRules, turbo: TurboModel, capital: float) -> list[Sized]:
    """
    Each trade, in order, as WATA would have sized it on an account that
    started with ``capital``: a share of the account by the time of day and
    the confidence of the entry, in whole turbos, one of them left unbought
    (``_calculate_bid_amount``). Neither fees nor taxes.
    """
    account = capital
    rows = []
    for trade in trades:
        available = account * rules.funds_percent / 100.0 * trade.scale
        units = 0
        if available >= turbo.entry_price * (1 + rules.safety_units):
            units = max(0, math.floor(available / turbo.entry_price - rules.safety_units))
        stake = round(units * turbo.entry_price, 2)
        profit_loss = round(stake * trade.performance / 100.0, 2)
        account = round(account + profit_loss, 2)
        rows.append(Sized(stake, profit_loss, account))
    return rows


def money(trades: list[Trade], amounts: list[Sized], capital: float) -> dict | None:
    """The trades in money, for an account that started with ``capital``. None without a capital."""
    if capital <= 0:
        return None
    peak = capital
    deepest = deepest_percent = 0.0
    for row in amounts:
        peak = max(peak, row.equity)
        deepest = min(deepest, row.equity - peak)
        deepest_percent = min(deepest_percent, (row.equity / peak - 1) * 100)
    final = amounts[-1].equity if amounts else capital
    return {
        "capital": capital,
        "final": final,
        "profit_loss": round(final - capital, 2),
        "return_percent": round((final / capital - 1) * 100, 2),
        "worst_drawdown": round(deepest, 2),
        "worst_drawdown_percent": round(deepest_percent, 2),
        # What WATA's sizing put on a trade on average, of what an order may use.
        "average_scale_percent": round(sum(trade.scale for trade in trades) * 100 / len(trades), 1) if trades else None,
        # Entries the account could not buy a single turbo for.
        "unfunded": sum(1 for row in amounts if row.stake == 0),
    }


def profit_by_day(trades: list[Trade], amounts: list[Sized], clock: LocalClock) -> dict[date, float]:
    """What each day brought in money: a trade counts on the day it was entered, as in the day's result."""
    days: dict[date, float] = {}
    for trade, row in zip(trades, amounts):
        day, _ = clock.at(trade.entry_at)
        days[day] = round(days.get(day, 0.0) + row.profit_loss, 2)
    return days


def summarize(simulator: Simulator) -> dict:
    """The figures of everything a simulator traded."""
    return summarize_parts(simulator.trades, market_days(simulator.days), simulator.refusals)


def summarize_parts(trades: list[Trade], days: list[DayResult], refusals: list[Refusal], *, with_range: bool = True) -> dict:
    """The figures of some trades, of the days they were made on, and of the entries refused on those."""
    performances = [trade.performance for trade in trades]
    wins = [performance for performance in performances if performance > 0]
    losses = [performance for performance in performances if performance <= 0]
    day_percents = [day.percent for day in days]
    # What a stop has to leave room for, and what a target could have taken.
    winners_lowest = [trade.min_performance for trade in trades if trade.performance > 0]
    losers_highest = [trade.max_performance for trade in trades if trade.performance <= 0]
    return {
        "days": len(days),
        "days_with_an_entry": sum(1 for day in days if day.entries),
        "trades": len(trades),
        "sample": sample_size(len(trades), len(days)),
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
        "worst_drawdown": worst_drawdown(days),
        # Half of the winning trades never fell below ``median``, nine in ten never below ``tenth``.
        "winners_lowest": {
            "median": _rounded(quantile(winners_lowest, 0.5)), "tenth": _rounded(quantile(winners_lowest, 0.1)),
        } if winners_lowest else None,
        # Half of the losing trades had been above ``median``, one in ten above ``tenth``.
        "losers_highest": {
            "median": _rounded(quantile(losers_highest, 0.5)), "tenth": _rounded(quantile(losers_highest, 0.9)),
        } if losers_highest else None,
        "index_points": round(sum(trade.points for trade in trades), 1),
        "exits": dict(Counter(trade.exit_reason for trade in trades).most_common()),
        "entries": dict(Counter(trade.entry_reason for trade in trades).most_common()),
        "refusals": dict(Counter(refusal.reason for refusal in refusals).most_common()),
    }


def _cell(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _shown(key: str, value):
    """A figure made of several, as the one cell a table has for it."""
    if value is None:
        return None
    if key == "total_range":
        return f"{value['low']:.2f} to {value['high']:.2f}"
    if key == "against_random":
        return value["beaten"]
    if key == "money":
        return f"{value['profit_loss']:+.2f} ({value['return_percent']:+.2f} %)"
    return value


def format_summaries(summaries: list[tuple[str, dict]]) -> str:
    """The runs side by side, one line per measure."""
    measures = (
        ("days", "Days"),
        ("days_with_an_entry", "Days with an entry"),
        ("trades", "Trades"),
        ("win_rate", "Winning trades %"),
        ("average_win", "Average win %"),
        ("average_loss", "Average loss %"),
        ("expectancy", "Per trade on average %"),
        ("profit_factor", "Won for 1 lost"),
        ("longest_losing_streak", "Longest losing streak"),
        ("total_percent", "All days compounded %"),
        ("total_range", f"  {RANGE_LEVEL} % of redrawn days %"),
        ("best_day", "Best day %"),
        ("worst_day", "Worst day %"),
        ("median_day", "Median day %"),
        ("day_spread", "Spread of the days %"),
        ("worst_drawdown", "Worst drawdown %"),
        ("index_points", "Index points"),
        ("against_random", "Random entries beaten %"),
        ("money", "In money, as WATA sizes"),
    )
    shown = [
        (key, title, [_cell(_shown(key, summary.get(key))) for _, summary in summaries]) for key, title in measures
    ]
    width = max(len(title) for _, title, _ in shown)
    columns = [
        max(len(label), 10, *(len(cells[index]) for _, _, cells in shown)) for index, (label, _) in enumerate(summaries)
    ]
    lines = [" " * width + "  " + "  ".join(label.rjust(size) for (label, _), size in zip(summaries, columns))]
    for _, title, cells in shown:
        lines.append(f"{title.ljust(width)}  " + "  ".join(cell.rjust(size) for cell, size in zip(cells, columns)))
    thin = [(label, summary) for label, summary in summaries if summary["sample"] == TOO_FEW]
    if thin:
        counts = sorted({(summary["trades"], summary["days"]) for _, summary in thin})
        lines.append(
            "Too few to conclude: "
            + ", ".join(f"{trades} trade(s) over {days} day(s)" for trades, days in counts)
            + ("" if len(thin) == len(summaries) else f" ({', '.join(label for label, _ in thin)})")
            + f". Under {TOO_FEW_TRADES} trades or {TOO_FEW_DAYS} days, these figures describe those days and nothing else."
        )
    for key, title in (("entries", "Entries"), ("exits", "Exits"), ("refusals", "Refused entries")):
        for label, summary in summaries:
            counts = ", ".join(f"{reason} {count}" for reason, count in summary[key].items()) or "none"
            lines.append(f"{title} ({label}): {counts}")
    return "\n".join(lines)


def _write(path: str, columns: tuple, rows) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as out_file:
        writer = csv.writer(out_file)
        writer.writerow(columns)
        writer.writerows(rows)


def write_trades(path: str, trades: list[Trade], amounts: list[Sized] | None = None) -> None:
    """Every trade, with what was put on it and what it brought in money when ``amounts`` (``sized``) are given."""
    _write(path, TRADE_COLUMNS, (
        (
            utc(trade.entry_at), utc(trade.exit_at), trade.direction, trade.entry_reason,
            "" if trade.confidence is None else trade.confidence,
            round(trade.entry_index, 2), round(trade.exit_index, 2), round(trade.points, 2),
            trade.performance, trade.max_performance, trade.exit_reason, trade.min_performance,
            round(trade.scale * 100, 1),
            "" if amounts is None else amounts[number].stake,
            "" if amounts is None else amounts[number].profit_loss,
        )
        for number, trade in enumerate(trades)
    ))


def write_refusals(path: str, refusals: list[Refusal]) -> None:
    _write(path, REFUSAL_COLUMNS, ((utc(refusal.at), refusal.direction, refusal.reason) for refusal in refusals))


def write_days(path: str, days: list[DayResult], profits: dict[date, float] | None = None, capital: float = 0.0) -> None:
    """Every day, with what it brought in money and the account after it when ``profits`` (``profit_by_day``) are given."""
    account = capital
    rows = []
    for day in days:
        profit = "" if profits is None else profits.get(day.day, 0.0)
        if profits is not None:
            account = round(account + profit, 2)
        rows.append((
            day.day.isoformat(), day.entries, len(day.performances), day.percent, profit, "" if profits is None else account,
        ))
    _write(path, DAY_COLUMNS, rows)
