"""
What a backtest shows: one summary per run, and the trades and the days as
CSV files.
"""

import csv
import os
from collections import Counter
from datetime import datetime, timezone

from src.strategy.execution import DayResult, Simulator, Trade, compounded

TRADE_COLUMNS = (
    "entry_time", "exit_time", "direction", "entry_reason", "confidence", "entry_index", "exit_index",
    "index_points", "performance_percent", "max_performance_percent", "exit_reason",
)
DAY_COLUMNS = ("day", "entries", "closed", "day_percent")


def _utc(at: int) -> str:
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


def summarize(simulator: Simulator) -> dict:
    trades, days = simulator.trades, market_days(simulator.days)
    wins = [trade.performance for trade in trades if trade.performance > 0]
    losses = [trade.performance for trade in trades if trade.performance <= 0]
    day_percents = [day.percent for day in days]
    return {
        "days": len(days),
        "days_with_an_entry": sum(1 for day in days if day.entries),
        "trades": len(trades),
        "win_rate": round(len(wins) * 100 / len(trades), 1) if trades else None,
        "average_win": round(sum(wins) / len(wins), 2) if wins else None,
        "average_loss": round(sum(losses) / len(losses), 2) if losses else None,
        # Each day's result applied on the day before, as WATA counts a day: every trade as the whole stake.
        "total_percent": compounded(day_percents),
        "best_day": max(day_percents, default=None),
        "worst_day": min(day_percents, default=None),
        "worst_drawdown": worst_drawdown(days),
        "index_points": round(sum(trade.points for trade in trades), 1),
        "exits": dict(Counter(trade.exit_reason for trade in trades).most_common()),
        "entries": dict(Counter(trade.entry_reason for trade in trades).most_common()),
        "refusals": dict(Counter(refusal.reason for refusal in simulator.refusals).most_common()),
    }


def _cell(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def format_summaries(summaries: list[tuple[str, dict]]) -> str:
    """The runs side by side, one line per measure."""
    measures = (
        ("days", "Days"),
        ("days_with_an_entry", "Days with an entry"),
        ("trades", "Trades"),
        ("win_rate", "Winning trades %"),
        ("average_win", "Average win %"),
        ("average_loss", "Average loss %"),
        ("total_percent", "All days compounded %"),
        ("best_day", "Best day %"),
        ("worst_day", "Worst day %"),
        ("worst_drawdown", "Worst drawdown %"),
        ("index_points", "Index points"),
    )
    width = max(len(title) for _, title in measures)
    columns = [max(len(label), 10) for label, _ in summaries]
    lines = [" " * width + "  " + "  ".join(label.rjust(size) for (label, _), size in zip(summaries, columns))]
    for key, title in measures:
        cells = "  ".join(_cell(summary[key]).rjust(size) for (_, summary), size in zip(summaries, columns))
        lines.append(f"{title.ljust(width)}  {cells}")
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


def write_trades(path: str, trades: list[Trade]) -> None:
    _write(path, TRADE_COLUMNS, (
        (
            _utc(trade.entry_at), _utc(trade.exit_at), trade.direction, trade.entry_reason,
            "" if trade.confidence is None else trade.confidence,
            round(trade.entry_index, 2), round(trade.exit_index, 2), round(trade.points, 2),
            trade.performance, trade.max_performance, trade.exit_reason,
        )
        for trade in trades
    ))


def write_days(path: str, days: list[DayResult]) -> None:
    _write(path, DAY_COLUMNS, (
        (day.day.isoformat(), day.entries, len(day.performances), day.percent) for day in days
    ))
