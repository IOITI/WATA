"""
What kind of day each day was, read from the asset a run's chart is of, in
the rules' trading hours: the gap from the day before, the range, the
realised volatility, whether the day trended or went back and forth, how the
half hour after the US open moved, its weekday, and the US macro news it had
(the trader's calendar, ``src/trade/macro_calendar.py``). Then where a moment
stands against the US open. Pure, but for reading the calendar.

A day is put in a group by these figures (``label_days``) so that trades and
signals can be split by the kind of day they were made on: an edge that lives
on agitated days only, or only after the open, is a different strategy.
"""

import math
from bisect import bisect_left, bisect_right
from datetime import date, datetime, time
from statistics import quantiles

from src.backtest.clock import TradingClock
from src.backtest.data import BAR_SECONDS, AssetSeries
from src.trade.macro_calendar import (  # noqa: F401 (the backtests' names for them)
    CALENDAR_PATH,
    DEFAULT_EVENTS,
    MACRO_EVENTS,
    NEW_YORK,
    Calendar,
    MacroEvent,
    read_calendar,
)

US_OPEN = time(9, 30)
# From the US open to its close: six hours and a half.
US_SESSION_MINUTES = 390

# Five-minute closes make the realised volatility: sqrt of the sum of their squared log returns.
VOLATILITY_STEP_SECONDS = 300
# The share of its range a day's move from open to close took: from TREND it trended, up to RANGE it went back and forth.
TREND_EFFICIENCY = 0.6
RANGE_EFFICIENCY = 0.3
# A move from the day before beyond this, in percent, is a gap; the same for the US open's half hour.
GAP_PERCENT = 0.3
OPEN_MOVE_PERCENT = 0.2
# Fewer bars than this in the trading hours (five minutes of them): no figures for the day.
LEAST_BARS = 30
# A price older than this at a moment is no price there.
STALE_SECONDS = 120

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
VOLATILITY_LABELS = ("Calm", "Ordinary", "Agitated")
GAP_LABELS = ("Gap down", "No gap", "Gap up")
DAY_TYPES = ("Trend up", "Trend down", "Back and forth", "Mixed")
OPEN_LABELS = ("Open fell", "Open flat", "Open rose")
NO_NEWS = "No news"
# A day is known by the releases the calendar holds since 2024: the others, from late 2026 only, would split the
# days of a run in two.
MACRO_LABELS = (*DEFAULT_EVENTS, NO_NEWS)
SESSION_PARTS = (
    "Before the US open", "Hour before the US open", "US open, first hour", "US midday", "US afternoon", "After the US close",
)


def us_open(day: date) -> float:
    """The US open of ``day`` (9:30 in New York) as a moment."""
    return NEW_YORK.localize(datetime.combine(day, US_OPEN)).timestamp()


def session(day: date, clock: TradingClock, start_hour: int, end_hour: int) -> tuple[float, float]:
    """The rules' trading hours of ``day``, as moments."""
    start = clock.epoch(day, time(start_hour))
    end = clock.day_bounds(day)[1] if end_hour >= 24 else clock.epoch(day, time(end_hour))
    return start, end


def _fresh(series: AssetSeries, moment: float) -> float | None:
    place = bisect_right(series.ts, moment - BAR_SECONDS) - 1
    if place < 0 or moment - (series.ts[place] + BAR_SECONDS) > STALE_SECONDS:
        return None
    return series.close[place]


def _rounded(value: float | None, digits: int = 3) -> float | None:
    return None if value is None or not math.isfinite(value) else round(value, digits)


def session_close(series: AssetSeries | None, bounds: tuple[float, float]) -> float | None:
    """The last price of the trading hours, if the asset had one in them."""
    if series is None:
        return None
    start, end = bounds
    first, last = bisect_left(series.ts, start), bisect_right(series.ts, end - BAR_SECONDS)
    return series.close[last - 1] if last > first else None


def day_features(
    series: AssetSeries | None,
    previous_close: float | None,
    day: date,
    bounds: tuple[float, float],
    news: list[MacroEvent] = (),
) -> dict:
    """A day's figures in the trading hours ``bounds``; without enough bars, only its weekday and news."""
    found = {
        "weekday": WEEKDAYS[day.weekday()],
        "news": sorted({event.event for event in news if event.event in DEFAULT_EVENTS}, key=DEFAULT_EVENTS.index),
        "bars": 0, "gap_percent": None, "range_percent": None, "volatility_percent": None, "efficiency": None,
        "day_type": None, "us_open_move_percent": None,
    }
    if series is None:
        return found
    start, end = bounds
    first, last = bisect_left(series.ts, start), bisect_right(series.ts, end - BAR_SECONDS)
    closes = series.close[first:last]
    found["bars"] = len(closes)
    if len(closes) < LEAST_BARS:
        return found
    opened, closed, high, low = closes[0], closes[-1], max(closes), min(closes)
    steps = [_fresh(series, moment) for moment in range(int(start) + VOLATILITY_STEP_SECONDS, int(end) + 1, VOLATILITY_STEP_SECONDS)]
    returns = [math.log(after / before) for before, after in zip(steps, steps[1:]) if before and after]
    efficiency = abs(closed - opened) / (high - low) if high > low else 0.0
    if efficiency >= TREND_EFFICIENCY:
        day_type = DAY_TYPES[0] if closed > opened else DAY_TYPES[1]
    else:
        day_type = DAY_TYPES[2] if efficiency <= RANGE_EFFICIENCY else DAY_TYPES[3]
    opening = us_open(day)
    at_open, half_hour = _fresh(series, opening), _fresh(series, opening + 1800)
    found.update(
        gap_percent=_rounded((opened - previous_close) / previous_close * 100) if previous_close else None,
        range_percent=_rounded((high - low) / opened * 100),
        volatility_percent=_rounded(math.sqrt(sum(value * value for value in returns)) * 100) if returns else None,
        efficiency=_rounded(efficiency, 2),
        day_type=day_type,
        us_open_move_percent=_rounded((half_hour - at_open) / at_open * 100) if at_open and half_hour else None,
    )
    return found


def label_days(features: list[dict]) -> None:
    """
    Each day's groups, in place under ``labels``: its volatility among the
    run's days (thirds), its gap, its kind, its US open, its news.
    """
    known = sorted(day["volatility_percent"] for day in features if day.get("volatility_percent") is not None)
    cuts = quantiles(known, n=3) if len(known) >= 3 else None
    for day in features:
        volatility, gap, opened = day.get("volatility_percent"), day.get("gap_percent"), day.get("us_open_move_percent")
        day["labels"] = {
            "volatility": (VOLATILITY_LABELS[0] if volatility <= cuts[0] else VOLATILITY_LABELS[2] if volatility > cuts[1]
                           else VOLATILITY_LABELS[1]) if cuts and volatility is not None else None,
            "gap": None if gap is None else GAP_LABELS[0] if gap < -GAP_PERCENT else GAP_LABELS[2] if gap > GAP_PERCENT else GAP_LABELS[1],
            "day_type": day.get("day_type"),
            "us_open": None if opened is None else OPEN_LABELS[0] if opened < -OPEN_MOVE_PERCENT
            else OPEN_LABELS[2] if opened > OPEN_MOVE_PERCENT else OPEN_LABELS[1],
            "news": day["news"][0] if day.get("news") else NO_NEWS,
        }


def minutes_after_open(moment: float, day: date) -> int:
    """Whole minutes from the US open of ``day`` to ``moment``; negative before it."""
    return math.floor((moment - us_open(day)) / 60)


def session_part(minutes: int) -> str:
    """Where a moment stands against the US open, from its minutes after it."""
    if minutes < -60:
        return SESSION_PARTS[0]
    if minutes < 0:
        return SESSION_PARTS[1]
    if minutes < 60:
        return SESSION_PARTS[2]
    if minutes < 240:
        return SESSION_PARTS[3]
    if minutes < US_SESSION_MINUTES:
        return SESSION_PARTS[4]
    return SESSION_PARTS[5]
