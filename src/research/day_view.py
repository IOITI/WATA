"""
A day of an asset with a script applied, as Claude reads it (chart_view, the
``view`` study of src/research/study.py): the script's bars of a window
gathered into rows, each with its prices, the script's plots, the alerts it
raised and the trades a run made in it; the day's figures and its swings; and
what price did after each alert. Pure: the study hands it the bars and the
series the replay ran, the asset's ten-second rows and a run's trades.

Not part of a backtest's code (``_CODE`` of src/backtest/figures.py): nothing
here changes what a run found. The moves after an alert are the edge's own
(``moves_after`` of src/backtest/edge.py), the day's figures the regimes'
(``day_features`` of src/backtest/regimes.py).
"""

import csv
import io
import math
from bisect import bisect_left, bisect_right
from datetime import datetime, timezone
from statistics import fmean, median
from zoneinfo import ZoneInfo

from src.backtest.data import BAR_SECONDS, AssetSeries
from src.backtest.edge import moves_after

# What an alert becomes when it is an entry (the simulator's words).
LONG, SHORT = "long", "short"

# What a row may gather, the shortest that keeps the window within the rows asked: seconds, then minutes, then hours.
STEPS_SECONDS = (10, 20, 30, 60, 120, 180, 300, 600, 900, 1800, 3600, 7200)
# What price did after an alert, read at these minutes (src/backtest/edge.py measures 1 to 60).
AFTER_MINUTES = (5, 15, 30, 60)
# Its best and worst on the way, within these minutes.
WAY_MINUTES = 30
# A day's swings: a move of this share of the open at least, the threshold raised until they are this many at most.
SWING_PERCENT = 0.2
SWINGS_MOST = 20
# What a plot's values are written with in the table.
PLOT_DIGITS = 6
# A price older than this at a moment is no price there (as the edge and the regimes read one).
STALE_SECONDS = 120


def finite(value) -> float | None:
    """A plot's value when it is a finite number; None for na, a colour or a boolean."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _rounded(value: float | None, digits: int = 2) -> float | None:
    return None if value is None or not math.isfinite(value) else round(value, digits)


def clock_text(moment_ms: float, zone: ZoneInfo, seconds: bool = False) -> str:
    """A moment as the table writes it: HH:MM (HH:MM:SS for rows under a minute) in ``zone``."""
    local = datetime.fromtimestamp(moment_ms / 1000, timezone.utc).astimezone(zone)
    return local.strftime("%H:%M:%S" if seconds else "%H:%M")


def price_text(value: float | None, decimals: int | None) -> str:
    if value is None:
        return ""
    return f"{value:.{decimals}f}" if decimals is not None else f"{value:.{PLOT_DIGITS}g}"


def plot_text(value: float | None) -> str:
    return "" if value is None else f"{value:.{PLOT_DIGITS}g}"


def panes(plots: dict[str, list], low: float, high: float) -> tuple[list[str], list[str]]:
    """
    The plots that are prices (their median among the window's prices, half
    its range around them) and those that are not, drawn in a pane of their own.
    """
    over, under = [], []
    margin = (high - low) * 0.5
    for name, values in plots.items():
        numbers = sorted(value for value in map(finite, values) if value is not None)
        if not numbers:
            continue
        middle = numbers[len(numbers) // 2]
        (over if low - margin <= middle <= high + margin else under).append(name)
    return over, under


# ── the rows ──


def step_for(opens_ms: list[int], rows: int, bar_seconds: int | None) -> int | None:
    """
    The seconds one row gathers so that the bars opening at ``opens_ms`` fill
    ``rows`` rows at most (a bucket from 1970, empty ones taking none); None:
    a row a bar.
    """
    if len(opens_ms) <= rows:
        return None
    for step in STEPS_SECONDS:
        if bar_seconds and step <= bar_seconds:
            continue
        if len({opening // (step * 1000) for opening in opens_ms}) <= rows:
            return step
    return STEPS_SECONDS[-1]


def step_said(step: int | None) -> str:
    """What one row of the table holds: a bar, or the seconds, minutes or hours it gathers."""
    if step is None:
        return "a bar"
    if step < 60:
        return f"{step} s"
    return f"{step // 60} min" if step < 3600 else f"{step // 3600} h"


def gathered(bars: list, plots: dict[str, list], step: int | None) -> list[dict]:
    """
    ``bars`` ([open ms, open, high, low, close, volume, close ms]) in rows of
    ``step`` seconds (a row a bar for None): each row's open, high, low and
    close, its bars (``n``, the first and last place in ``bars``), and each
    plot at the last bar of the row where it had a value. ``plots``: each plot's
    values, one a bar.
    """
    rows: list[dict] = []
    for index, bar in enumerate(bars):
        key = bar[0] if step is None else bar[0] // (step * 1000) * step * 1000
        if step is None or not rows or rows[-1]["t"] != key:
            rows.append({"t": key, "o": bar[1], "h": bar[2], "l": bar[3], "c": bar[4], "n": 0, "first": index,
                         "last": index, "end": bar[6], "plots": {}})
        row = rows[-1]
        row.update(h=max(row["h"], bar[2]), l=min(row["l"], bar[3]), c=bar[4], n=row["n"] + 1, last=index, end=bar[6])
        for name, values in plots.items():
            value = finite(values[index]) if index < len(values) else None
            if value is not None:
                row["plots"][name] = value
    return rows


def row_of_moment(rows: list[dict], moment_ms: float) -> int | None:
    """The row a moment falls in: the last that began by then (None before the first, or after the last ended)."""
    place = bisect_right([row["t"] for row in rows], moment_ms) - 1
    if place < 0 or moment_ms > rows[place]["end"] and place == len(rows) - 1:
        return None
    return place


def table(rows: list[dict], plot_names: list[str], alerts_by_row: dict[int, list[str]], trades_by_row: dict[int, list[str]],
          zone: ZoneInfo, decimals: int | None, seconds: bool) -> tuple[list[str], str]:
    """The rows as CSV: their time, prices, bars, plots, the alerts raised and the trades in each."""
    columns = ["time", "open", "high", "low", "close", "bars", *plot_names, "alerts", "trades"]
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(columns)
    for place, row in enumerate(rows):
        writer.writerow([
            clock_text(row["t"], zone, seconds), *(price_text(row[key], decimals) for key in ("o", "h", "l", "c")), row["n"],
            *(plot_text(row["plots"].get(name)) for name in plot_names),
            " ".join(_counted(alerts_by_row.get(place, []))), " ".join(trades_by_row.get(place, [])),
        ])
    return columns, out.getvalue()


def _counted(titles: list[str]) -> list[str]:
    """Titles in their order, one raised twice in a row written once with its count: Long×2."""
    counts: dict[str, int] = {}
    for title in titles:
        counts[title] = counts.get(title, 0) + 1
    return [title if count == 1 else f"{title}×{count}" for title, count in counts.items()]


# ── what price did after each alert ──


def asset_series(rows: list, side: str) -> AssetSeries:
    """
    The asset's ten-second closes (rows: ts, bid open, high, low, close, ask
    open, high, low, close, updates), on ``side``, as a run fills them.
    """
    series = AssetSeries()
    column = 8 if side == "ask" else 4
    for row in rows:
        value = row[column] if row[column] is not None else row[4]
        if value is not None:
            series.ts.append(int(row[0]))
            series.close.append(float(value))
    return series


def moved(series: AssetSeries, moment: float, action: str | None) -> dict | None:
    """
    What price did from ``moment`` (an alert's bar close), in basis points:
    signed by the alert's direction when it is an entry (positive: the way it
    pointed), else price up positive. At AFTER_MINUTES, and the best and worst
    within WAY_MINUTES. None where the asset had no price then.
    """
    direction = SHORT if action == SHORT else LONG
    found = moves_after(series, moment, direction)
    if found is None:
        return None
    after = {str(minutes): _rounded(found[minutes][0], 1) for minutes in AFTER_MINUTES if minutes in found}
    way = found.get(WAY_MINUTES)
    return {"after": after, "best": _rounded(way[1], 1) if way else None, "worst": _rounded(way[2], 1) if way else None}


def alert_moves(series: AssetSeries, alerts: list[dict]) -> dict:
    """
    Each alert's title (``alerts``: title, action, at the bar's close in
    seconds) with how many were raised, what each becomes, and price after
    them: the mean move and the share that went its way (up, for one that is no
    entry) at each of AFTER_MINUTES, and the median best and worst within
    WAY_MINUTES.
    """
    by_title: dict[str, dict] = {}
    for alert in alerts:
        entry = by_title.setdefault(alert["title"], {"count": 0, "action": alert.get("action"), "moves": []})
        entry["count"] += 1
        if alert.get("moves") is not None:
            entry["moves"].append(alert["moves"])
    said = {}
    for title, entry in by_title.items():
        moves = entry.pop("moves")
        signed = entry["action"] in (LONG, SHORT)
        after = {}
        for minutes in map(str, AFTER_MINUTES):
            values = [move["after"][minutes] for move in moves if move["after"].get(minutes) is not None]
            if values:
                after[minutes] = {"mean": _rounded(fmean(values), 1), "its_way" if signed else "up": _rounded(
                    sum(value > 0 for value in values) / len(values), 2), "of": len(values)}
        bests = [move["best"] for move in moves if move.get("best") is not None]
        worsts = [move["worst"] for move in moves if move.get("worst") is not None]
        said[title] = {**entry, "signed": signed, "after_bp": after,
                       f"best_{WAY_MINUTES}m_median": _rounded(median(bests), 1) if bests else None,
                       f"worst_{WAY_MINUTES}m_median": _rounded(median(worsts), 1) if worsts else None}
    return said


# ── the day ──


def _inside(series: AssetSeries, begin: float, end: float) -> tuple[int, int]:
    return bisect_left(series.ts, begin), bisect_right(series.ts, end - BAR_SECONDS)


def day_prices(series: AssetSeries, begin: float, end: float, zone: ZoneInfo, decimals: int | None) -> dict | None:
    """The window's open, high and low with their times, close, range and move, from the ten-second closes."""
    first, last = _inside(series, begin, end)
    if last <= first:
        return None
    closes = series.close[first:last]
    high = max(range(len(closes)), key=closes.__getitem__)
    low = min(range(len(closes)), key=closes.__getitem__)
    opened = closes[0]

    def at(place: int) -> str:
        return clock_text((series.ts[first + place] + BAR_SECONDS) * 1000, zone)

    def price(value: float) -> float:
        return round(value, decimals) if decimals is not None else value

    return {
        "open": price(opened), "high": price(closes[high]), "high_at": at(high), "low": price(closes[low]),
        "low_at": at(low), "close": price(closes[-1]),
        "range_percent": _rounded((closes[high] - closes[low]) / opened * 100, 3),
        "move_percent": _rounded((closes[-1] - opened) / opened * 100, 3),
    }


def levels(series: AssetSeries, day_begin: float, view_begin: float, us_open: float, zone: ZoneInfo,
           decimals: int | None) -> dict:
    """
    Where price stood: the high and low from the engine day's start to the
    window's (the night, for the day's trading hours), the price at the US open
    and the high and low of its first hour.
    """
    def price(value: float | None) -> float | None:
        return None if value is None else round(value, decimals) if decimals is not None else value

    said = {}
    first, last = _inside(series, day_begin, view_begin)
    if last > first:
        before = series.close[first:last]
        said.update(before_high=price(max(before)), before_low=price(min(before)))
    # The bar holding the open, or the last before it when recent.
    place = bisect_right(series.ts, us_open) - 1
    if place >= 0 and us_open - series.ts[place] <= STALE_SECONDS:
        said.update(us_open=price(series.close[place]), us_open_at=clock_text(us_open * 1000, zone))
    first, last = _inside(series, us_open, us_open + 3600)
    if last > first:
        hour = series.close[first:last]
        said.update(first_hour_high=price(max(hour)), first_hour_low=price(min(hour)))
    return said


def swings(series: AssetSeries, begin: float, end: float, zone: ZoneInfo, decimals: int | None) -> list[dict]:
    """
    The day's turns: each high and low that price left by SWING_PERCENT of the
    window's open at least (more, until they are SWINGS_MOST at most), with its
    time and the move from the turn before, in percent.
    """
    first, last = _inside(series, begin, end)
    if last - first < 2:
        return []
    closes, times = series.close[first:last], series.ts[first:last]
    share = SWING_PERCENT / 100
    while True:
        found = _zigzag(closes, closes[0] * share)
        if len(found) <= SWINGS_MOST or share > 0.5:
            break
        share *= 1.5
    said, before = [], closes[0]
    for place, kind in found:
        value = closes[place]
        said.append({"at": clock_text((times[place] + BAR_SECONDS) * 1000, zone), "kind": kind,
                     "price": round(value, decimals) if decimals is not None else value,
                     "move_percent": _rounded((value - before) / before * 100, 3)})
        before = value
    return said


def _zigzag(closes, threshold: float) -> list[tuple[int, str]]:
    """The places of the turns of ``closes`` that price left by ``threshold`` at least, high or low."""
    turns: list[tuple[int, str]] = []
    trend, extreme = None, 0
    # Until price first leaves its range by the threshold, the lowest and highest since the start: the first turn.
    lowest = highest = 0
    for place in range(1, len(closes)):
        value = closes[place]
        if trend is None:
            lowest = place if value < closes[lowest] else lowest
            highest = place if value > closes[highest] else highest
            if value - closes[lowest] >= threshold:
                turns.append((lowest, "low"))
                trend, extreme = "up", place
            elif closes[highest] - value >= threshold:
                turns.append((highest, "high"))
                trend, extreme = "down", place
            continue
        if trend == "up":
            if value >= closes[extreme]:
                extreme = place
            elif closes[extreme] - value >= threshold:
                turns.append((extreme, "high"))
                trend, extreme = "down", place
        else:
            if value <= closes[extreme]:
                extreme = place
            elif value - closes[extreme] >= threshold:
                turns.append((extreme, "low"))
                trend, extreme = "up", place
    if trend is not None:
        turns.append((extreme, "high" if trend == "up" else "low"))
    return turns


# ── a run's trades on the day ──


def trade_marks(trades: list[dict], rows: list[dict]) -> tuple[dict[int, list[str]], list[dict]]:
    """
    A run's trades of the day as the table marks them in its rows (``#3 long
    in``, ``#3 out -0.84%``), and each in a line: its times, direction, the
    asset's price in and out, its result, best and worst, and why it ended.
    """
    by_row: dict[int, list[str]] = {}
    lines = []
    for trade in trades:
        entry, exit_ = trade.get("entry") or {}, trade.get("exit") or {}
        number, direction = trade.get("id"), trade.get("direction")
        result = trade.get("performance_percent")
        for moment, mark in ((entry.get("filled_at"), f"#{number} {direction} in"),
                             (exit_.get("filled_at"), f"#{number} out {result:+.2f}%" if finite(result) is not None
                              else f"#{number} out")):
            at = epoch(moment)
            place = row_of_moment(rows, at * 1000) if at is not None and rows else None
            if place is not None:
                by_row.setdefault(place, []).append(mark)
        lines.append({
            "id": number, "direction": direction, "in": entry.get("filled_at"), "out": exit_.get("filled_at"),
            "asset_in": entry.get("asset_price"), "asset_out": exit_.get("asset_price"),
            "result_percent": result, "best_percent": trade.get("max_performance_percent"),
            "worst_percent": trade.get("min_performance_percent"), "exit_reason": exit_.get("reason"),
            "signal_id": trade.get("signal_id"),
        })
    return by_row, lines


def epoch(value) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).timestamp()


# ── the answer, held to its size ──

# What is cut first when the answer would be too long, in this order.
FITTING = ("the alerts listed one by one", "the plots in the table beyond the first two", "the rows (longer ones)",
           "the swings beyond eight")


def fitted(build, limit: int, size) -> dict:
    """
    The answer ``build(listed, plots, coarser, swings)`` makes within ``limit``
    characters (``size`` measures one): its alerts listed one by one cut to 20
    then to none, its table's plots to two, its rows made longer a step at a
    time, its swings to eight; what was cut said in ``cut``.
    """
    attempts = [(None, None, 0, None), (20, None, 0, None), (0, None, 0, None), (0, 2, 0, None)]
    attempts += [(0, 2, coarser, None) for coarser in range(1, len(STEPS_SECONDS) + 1)]
    attempts += [(0, 2, len(STEPS_SECONDS), 8)]
    answer = None
    for listed, plots, coarser, kept_swings in attempts:
        answer = build(listed, plots, coarser, kept_swings)
        if size(answer) <= limit:
            break
    return answer
