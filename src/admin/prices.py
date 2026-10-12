"""
What the Prices page needs beside the bars the recorder wrote: the stretches
of trading time for which nothing was recorded, and the same prices as range
bars.

A missing bar means two different things: the instrument was not quotable
(market closed, turbo knocked out), or the recorder was not recording. Only
the second is a gap, and only the recorder's own coverage can tell them apart.

Pure logic: no clock, no I/O.
"""

import math
from bisect import bisect_right
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, NamedTuple, Sequence

from src.price_recorder.bars import BAR_SECONDS
from src.price_recorder.range_bars import RANGE_PAUSE_SECONDS, BuiltBar, RangeBarBuilder

# One bar as the Prices page receives it: the start of its bucket (epoch seconds), then bid, ask and activity.
BAR_COLUMNS = (
    "t", "bid_open", "bid_high", "bid_low", "bid_close", "ask_open", "ask_high", "ask_low", "ask_close", "updates",
)
# Bar lengths the page can ask for, in seconds. Above the first one, bars are aggregated.
INTERVALS = (10, 30, 60, 300, 900, 3600)
# What one chart is given at most: a full day at ten seconds is 8,640.
MAX_BUCKETS = 12_000
MAX_PERIOD_DAYS = 366
# Gaps are read from one row per ten seconds: not worked out for longer periods.
GAPS_MAX_DAYS = 31

# One range bar as the Prices page receives it: the time the chart is given for it,
# its prices, when it really began and ended (epoch seconds), whether it reached its
# range, whether it is the first after a pause in the data, and the index at its end
# (under a turbo).
RANGE_COLUMNS = ("t", "open", "high", "low", "close", "began", "ended", "complete", "after_pause", "index")
SIDES = ("bid", "ask")
RANGE_MAX_DAYS = 31
# Ten-second bars read to build them: a month recorded around the clock is 270,000.
RANGE_MAX_ROWS = 300_000
# An automatic range is about this fraction of what the price covered in the period.
AUTO_RANGE_DIVISOR = 25
# Prices are quoted with two decimals at least; beyond five it is noise.
MIN_DECIMALS = 2
MAX_DECIMALS = 5

DOWN = "down"       # no coverage row: the recorder was not running
SILENT = "silent"   # the recorder was running, no quote was arriving

_BAR = timedelta(seconds=BAR_SECONDS)
# Whether a moment is trading time changes on the hour, in the trading timezone.
# Every timezone is a multiple of fifteen minutes away from UTC.
_STEP = timedelta(minutes=15)


class TooManyBars(Exception):
    """The period holds more buckets than one chart is given at this interval."""


def _merged(intervals: list[tuple[datetime, datetime, str]]) -> list[tuple[datetime, datetime, str]]:
    merged: list[tuple[datetime, datetime, str]] = []
    for start, end, reason in intervals:
        if merged and merged[-1][1] == start and merged[-1][2] == reason:
            merged[-1] = (merged[-1][0], end, reason)
        else:
            merged.append((start, end, reason))
    return merged


def _next_step(moment: datetime) -> datetime:
    """The next quarter of an hour strictly after ``moment``."""
    floored = moment.replace(minute=moment.minute - moment.minute % 15, second=0, microsecond=0)
    return floored + _STEP


def _within_trading_time(
    start: datetime, end: datetime, reason: str, is_trading_time: Callable[[datetime], bool],
) -> list[tuple[datetime, datetime, str]]:
    """The parts of [start, end) that fall in trading time."""
    kept = []
    cursor = start
    while cursor < end:
        segment_end = min(_next_step(cursor), end)
        if is_trading_time(cursor):
            kept.append((cursor, segment_end, reason))
        cursor = segment_end
    return _merged(kept)


def recording_gaps(
    coverage: list[dict],
    start: datetime,
    end: datetime,
    is_trading_time: Callable[[datetime], bool],
    now: datetime,
) -> list[dict]:
    """
    The stretches of [start, end) that were trading time and for which nothing
    was recorded, oldest first. ``coverage`` holds the recorder's rows of the
    period (``ts``, ``feed_ok``), in time order.

    Nights and weekends are left out: nothing quotes then, and listing them
    would bury the real incidents. The last buckets before ``now`` are left
    out too: their rows may simply not be written yet.
    """
    limit = min(end, now.astimezone(timezone.utc) - 2 * _BAR)
    if limit <= start:
        return []

    unrecorded: list[tuple[datetime, datetime, str]] = []
    cursor = start
    for row in coverage:
        ts = row["ts"]
        if ts >= limit:
            break
        if ts > cursor:
            unrecorded.append((cursor, ts, DOWN))
        if not row["feed_ok"]:
            unrecorded.append((ts, min(ts + _BAR, limit), SILENT))
        cursor = max(cursor, ts + _BAR)
    if cursor < limit:
        unrecorded.append((cursor, limit, DOWN))

    gaps = []
    for gap_start, gap_end, reason in _merged(unrecorded):
        gaps += _within_trading_time(gap_start, gap_end, reason, is_trading_time)
    return [
        {"start": gap_start, "end": gap_end, "seconds": int((gap_end - gap_start).total_seconds()), "reason": reason}
        for gap_start, gap_end, reason in _merged(gaps)
    ]


# ── range bars ──


def price_decimals(*series: Iterable[float]) -> int:
    """
    Decimals the prices are quoted with. One unit of the last of them is the
    tick: the smallest step the price takes, and what "1R" is in TradingView.
    """
    decimals = MIN_DECIMALS
    for values in series:
        for value in values:
            text = repr(float(value))
            if "e" in text or "n" in text:      # 1e-07, nan, inf: no decimals to read
                continue
            decimals = max(decimals, len(text) - text.index(".") - 1)
            if decimals >= MAX_DECIMALS:
                return MAX_DECIMALS
    return decimals


def auto_range_ticks(low: int, high: int) -> int:
    """
    A range that makes a period readable: about a twenty-fifth of what the
    price covered, as 1, 2 or 5 times a power of ten. ``low`` and ``high`` in ticks.
    """
    target = max((high - low) / AUTO_RANGE_DIVISOR, 1)
    magnitude = 10 ** math.floor(math.log10(target))
    # The nearest in proportion: 1,200 is nearer 1,000 than 2,000.
    return int(min((factor * magnitude for factor in (1, 2, 5, 10)), key=lambda size: abs(math.log(target / size))))


class RangeBar(NamedTuple):
    # The time the chart is given for it: when it began, made later where
    # needed so that no two bars share a time (several can begin in the same
    # ten seconds). A place on an axis, not a time to show: that is ``began``.
    t: int
    # Prices in ticks.
    open: int
    high: int
    low: int
    close: int
    # Start of the ten seconds its first price fell in, and end of the ten
    # seconds its last price fell in: as fine as what was recorded allows.
    began: int
    ended: int
    # It reached its range. False for the bar in progress and for one cut by a pause.
    complete: bool
    after_pause: bool


def range_bars(
    times: Sequence[int],
    opens: Sequence[float],
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    *,
    ticks: int,
    decimals: int,
    limit: int,
    bar_seconds: int = BAR_SECONDS,
    pause_seconds: int = RANGE_PAUSE_SECONDS,
) -> list[RangeBar]:
    """
    Range bars from ten-second bars, oldest first; the last one is the bar
    still in progress.

    How a bar grows and ends is ``RangeBarBuilder``'s
    (src/price_recorder/range_bars.py), which the strategies use as well. Here
    the prices become whole ticks, each bar gets its place on the chart's
    axis, and a period that makes more than ``limit`` bars is refused.
    """
    builder = RangeBarBuilder(ticks, bar_seconds=bar_seconds, pause_seconds=pause_seconds)
    scale = 10 ** decimals
    bars: list[RangeBar] = []

    def keep(built: BuiltBar) -> None:
        if len(bars) >= limit:
            raise TooManyBars()
        bars.append(RangeBar(
            t=built.began if not bars else max(built.began, bars[-1].t + 1),
            open=built.open, high=built.high, low=built.low, close=built.close,
            began=built.began,
            ended=built.ended,
            complete=built.complete,
            after_pause=built.after_pause,
        ))

    for index, time in enumerate(times):
        ended = builder.add(
            time,
            round(opens[index] * scale), round(highs[index] * scale),
            round(lows[index] * scale), round(closes[index] * scale),
        )
        for built in ended:
            keep(built)

    in_progress = builder.in_progress()
    if in_progress is not None:
        keep(in_progress)
    return bars


def sample_at(times: Sequence[int], values: Sequence[float], moments: Iterable[int]) -> list[float | None]:
    """For each moment, the last value at or before it (None before the first)."""
    sampled: list[float | None] = []
    for moment in moments:
        position = bisect_right(times, moment)
        sampled.append(values[position - 1] if position else None)
    return sampled


def range_rows(bars: Sequence[RangeBar], decimals: int, index: Sequence[float | None] | None = None) -> list[list]:
    """The bars as rows of RANGE_COLUMNS, prices back in the instrument's own unit."""
    scale = 10 ** decimals
    return [
        [
            bar.t,
            round(bar.open / scale, decimals), round(bar.high / scale, decimals),
            round(bar.low / scale, decimals), round(bar.close / scale, decimals),
            bar.began, bar.ended, bar.complete, bar.after_pause,
            None if index is None else index[position],
        ]
        for position, bar in enumerate(bars)
    ]
