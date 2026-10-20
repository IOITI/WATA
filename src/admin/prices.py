"""
What the Prices page needs beside the bars the recorder wrote: the stretches
of trading time for which nothing was recorded, the instruments Saxo left
without a quote meanwhile, and the same prices as range bars.

A missing bar means two different things: the instrument was not quotable
(market closed, turbo knocked out), or the recorder was not recording. Only
the second is a gap, and only the recorder's own coverage can tell them apart.
There is a third case, between the two: the recorder was receiving quotes and
Saxo sent none for one instrument, which had one before. That one is read
from the instrument's own bars (``availability``).

Pure logic: no clock, no I/O.
"""

import math
from bisect import bisect_right
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Callable, Iterable, Mapping, NamedTuple, Sequence

from src.price_recorder.bars import BAR_SECONDS
from src.price_recorder.range_bars import RANGE_PAUSE_SECONDS, BuiltBar, RangeBarBuilder

# One bar as the Prices page receives it: the start of its bucket (epoch seconds), then bid, ask and activity.
BAR_COLUMNS = (
    "t", "bid_open", "bid_high", "bid_low", "bid_close", "ask_open", "ask_high", "ask_low", "ask_close", "updates",
)
# Bar lengths the page can ask for, in seconds. Above the first one, bars are aggregated.
INTERVALS = (10, 30, 60, 300, 900, 3600)
# Any other whole number of seconds from the first one to a day is served too: the charts
# and backtests of the Technical Analysis pages make bars of any length (45 s, 90 s, 2 min).
MAX_INTERVAL = 86_400
# What one chart is given at most: a full day at ten seconds is 8,640.
MAX_BUCKETS = 12_000
MAX_PERIOD_DAYS = 366
# Gaps are read from one row per ten seconds: not worked out for longer periods.
GAPS_MAX_DAYS = 31

# One range bar as the Prices page receives it: the time the chart is given for it,
# its prices, when it really began and ended (epoch seconds), whether it reached its
# range, whether it is the first after a pause in the data, and the index at its end
# (under a turbo).
RANGE_COLUMNS = ("t", "open", "high", "low", "close", "began", "ended", "complete", "after_pause", "index", "volume")
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

# One instrument without a quote while the recorder was receiving others.
UNQUOTED = "unquoted"   # between two of its bars of the same day
STOPPED = "stopped"     # after its last bar of a day, while other turbos of its indice went on
# Every bar of the period is read to find them: not worked out for longer periods.
AVAILABILITY_MAX_DAYS = 7
# Stretches of consecutive bars read at most: an instrument quoted without a break is one a day.
MAX_RUNS = 50_000
# A bar or two missed where the recorder comes back is not an incident.
MIN_UNQUOTED_SECONDS = 30
# Issuers do not all stop quoting at the same minute of the evening.
STOPPED_AFTER_SECONDS = 15 * 60
# A turbo is worth its distance to the knock-out level: one whose last bid was
# under this share of its highest of the day was knocked out, or about to be.
KNOCK_OUT_RESIDUAL = 0.25
# Stretches given for one instrument, the longest ones.
STRETCHES_KEPT = 20

_BAR = timedelta(seconds=BAR_SECONDS)
# Whether a moment is trading time changes on the hour, in the trading timezone.
# Every timezone is a multiple of fifteen minutes away from UTC.
_STEP = timedelta(minutes=15)


class TooManyBars(Exception):
    """The period holds more buckets than one chart is given at this interval."""


def interval_served(interval) -> bool:
    """
    Whether bars of ``interval`` seconds are served: a whole number from ten seconds to
    a day. A length that is no multiple of ten seconds merges the ten-second bars that
    begin in each bucket: a 45-second bar holds 50 or 40 seconds of prices by turns.
    """
    return isinstance(interval, int) and not isinstance(interval, bool) and INTERVALS[0] <= interval <= MAX_INTERVAL


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


# ── what Saxo did not quote ──


class Run(NamedTuple):
    """Bars of one instrument that follow one another without a break, ten seconds apart."""

    instrument_id: int
    # Start of its first and of its last bar, epoch seconds.
    first: int
    last: int
    bars: int
    # Quote changes received over the run.
    updates: int
    without_ask: int
    delayed_minutes: int
    # Highest bid of the run, and the bid it ended on.
    high: float | None
    last_bid: float | None


RUN_COLUMNS = Run._fields


def _moment(seconds: int) -> datetime:
    return datetime.fromtimestamp(seconds, timezone.utc)


def _outside(
    start: datetime, end: datetime, gaps: Sequence[tuple[datetime, datetime]], gap_ends: Sequence[datetime],
) -> list[tuple[datetime, datetime]]:
    """The parts of [start, end) that are in none of ``gaps`` (in time order, not overlapping)."""
    kept = []
    cursor = start
    for gap_start, gap_end in gaps[bisect_right(gap_ends, start):]:
        if gap_start >= end:
            break
        if gap_start > cursor:
            kept.append((cursor, gap_start))
        cursor = max(cursor, gap_end)
    if cursor < end:
        kept.append((cursor, end))
    return kept


def _stretches(parts: Iterable[tuple[datetime, datetime]], reason: str) -> list[dict]:
    return [
        {"start": start, "end": end, "seconds": int((end - start).total_seconds()), "reason": reason}
        for start, end in parts
    ]


def availability(
    runs: Iterable[Run],
    turbos: Mapping[int, str | None],
    gaps: Sequence[dict],
    is_trading_time: Callable[[datetime], bool],
    trading_timezone: tzinfo,
) -> dict[int, dict]:
    """
    What was recorded of each instrument, and the times Saxo left it without a
    quote while the recorder was receiving others. ``runs`` are the stretches
    of consecutive bars of the period, by instrument and in time order;
    ``turbos`` gives the indice of each instrument that is a turbo; ``gaps``
    are the recorder's own (``recording_gaps``).

    Two things are looked for, both only in trading time and outside the
    recorder's gaps:

    - a hole between two bars of the same day of the trading timezone. The
      night says nothing, and nor does the morning before a turbo's first
      bar: the turbos followed are found again each day;
    - a turbo that got no quote for the rest of a day while another of its
      indice still did. One whose bid had fallen to a fraction of its highest
      of the day is taken to have been knocked out, which is not a fault of
      the data. Nothing in a quote says "knocked out": the price is the only
      trace of it.
    """
    gap_spans = [(gap["start"], gap["end"]) for gap in gaps]
    gap_ends = [end for _, end in gap_spans]

    def day_of(seconds: int):
        return datetime.fromtimestamp(seconds, trading_timezone).date()

    def unquoted(start: int, end: int) -> list[tuple[datetime, datetime]]:
        """The parts of [start, end) in which the instrument could have been quoted."""
        if end <= start:
            return []
        parts = []
        for part_start, part_end, _ in _within_trading_time(_moment(start), _moment(end), UNQUOTED, is_trading_time):
            parts += _outside(part_start, part_end, gap_spans, gap_ends)
        return parts

    by_instrument: dict[int, list[Run]] = {}
    for run in runs:
        by_instrument.setdefault(run.instrument_id, []).append(run)

    # The last bar of each turbo on each day, and the two latest of them per indice:
    # a turbo is compared with the others, so the latest one with the one before it.
    last_of_day: dict[int, dict] = {}
    latest: dict[tuple, list[tuple[int, int]]] = {}
    for instrument_id, own in by_instrument.items():
        if instrument_id not in turbos:
            continue
        days = last_of_day[instrument_id] = {}
        for run in own:
            days[day_of(run.last)] = run
        for day, run in days.items():
            best = latest.setdefault((turbos[instrument_id], day), [])
            best.append((run.last, instrument_id))
            best.sort(reverse=True)
            del best[2:]

    found: dict[int, dict] = {}
    for instrument_id, own in by_instrument.items():
        stretches: list[dict] = []
        for before, after in zip(own, own[1:]):
            if day_of(before.last) != day_of(after.first):
                continue
            stretches += _stretches(
                [
                    (start, end) for start, end in unquoted(before.last + BAR_SECONDS, after.first)
                    if (end - start).total_seconds() >= MIN_UNQUOTED_SECONDS
                ],
                UNQUOTED,
            )

        holes = len(stretches)
        hole_seconds = sum(stretch["seconds"] for stretch in stretches)

        stops: list[datetime] = []
        knocked_out_at = None
        for day, run in last_of_day.get(instrument_id, {}).items():
            others = [last for last, other in latest[(turbos[instrument_id], day)] if other != instrument_id]
            if not others:
                continue
            parts = unquoted(run.last + BAR_SECONDS, others[0] + BAR_SECONDS)
            if sum((end - start).total_seconds() for start, end in parts) < STOPPED_AFTER_SECONDS:
                continue
            highs = [other.high for other in own if day_of(other.last) == day and other.high is not None]
            if run.last_bid is not None and highs and run.last_bid < KNOCK_OUT_RESIDUAL * max(highs):
                knocked_out_at = _moment(run.last + BAR_SECONDS)
            else:
                # One stop, in as many stretches as the recorder's own gaps cut it into.
                stops.append(_moment(run.last + BAR_SECONDS))
                stretches += _stretches(parts, STOPPED)

        bars = sum(run.bars for run in own)
        longest = sorted(stretches, key=lambda stretch: stretch["seconds"], reverse=True)[:STRETCHES_KEPT]
        found[instrument_id] = {
            "bars": bars,
            "updates": sum(run.updates for run in own),
            "first_bar": _moment(own[0].first),
            "last_bar": _moment(own[-1].last),
            "quoted_seconds": bars * BAR_SECONDS,
            # All the time it had no quote and could have had one: its holes, and what followed a stop.
            "missing_seconds": sum(stretch["seconds"] for stretch in stretches),
            "holes": holes,
            "hole_seconds": hole_seconds,
            # When it stopped, for each day it did.
            "stops": sorted(stops),
            # The longest stretches of both kinds, in the order they happened, of ``stretch_count`` in all.
            "stretches": sorted(longest, key=lambda stretch: stretch["start"]),
            "stretch_count": len(stretches),
            "knocked_out_at": knocked_out_at,
            "without_ask": sum(run.without_ask for run in own),
            "delayed_minutes": max(run.delayed_minutes for run in own),
            # What flags the instrument on the page.
            "issues": [issue for issue, present in (("holes", holes), ("stopped", stops)) if present],
        }
    return found


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


def range_volumes(bars: Sequence[RangeBar], times: Sequence[int], updates: Sequence[int]) -> list[int]:
    """
    The quote changes of each range bar, what stands for its volume: each ten
    seconds counted once, in the first bar running at their start. Several bars
    can end in the same ten seconds: the first of them takes those quotes.
    """
    endeds = [bar.ended for bar in bars]
    volumes = [0] * len(bars)
    for moment, count in zip(times, updates):
        position = bisect_right(endeds, moment)
        if position < len(bars) and bars[position].began <= moment:
            volumes[position] += count
    return volumes


def range_rows(
    bars: Sequence[RangeBar],
    decimals: int,
    index: Sequence[float | None] | None = None,
    volumes: Sequence[int] | None = None,
) -> list[list]:
    """The bars as rows of RANGE_COLUMNS, prices back in the instrument's own unit."""
    scale = 10 ** decimals
    return [
        [
            bar.t,
            round(bar.open / scale, decimals), round(bar.high / scale, decimals),
            round(bar.low / scale, decimals), round(bar.close / scale, decimals),
            bar.began, bar.ended, bar.complete, bar.after_pause,
            None if index is None else index[position],
            None if volumes is None else volumes[position],
        ]
        for position, bar in enumerate(bars)
    ]
