"""
The bars a rule's script runs on, made from the ten-second bars the Capital.com
feed records, by the rules of the admin's charts: a script gives in the engine
what it gives on the Charts page and in the editor's test panel.

- Bars of time (whole minutes for a rule, any length from ten seconds to a day
  for a backtest): the ten-second bars that begin in each bucket (aligned on the
  epoch, as ``aggregate`` in src/admin/data.py and the SQL of AdminQueries)
  merged into one: first open, highest high, lowest low, last close, the ask
  from the ten seconds that have one, quote changes added up. A length that is
  no multiple of ten seconds holds 50 or 40 seconds of prices by turns (45 s),
  and its bar ends with its last ten seconds.
- Range bars: RangeBarBuilder (src/price_recorder/range_bars.py, read and never
  changed) on prices in whole steps of the instrument, each bar placed a second
  after the previous one when several begin in the same ten seconds, and the
  quote changes of each ten seconds counted once (``range_bars`` and
  ``range_volumes`` in src/admin/prices.py).

A bar goes to the script once it is final: a bucket once the feed has recorded
all of its ten seconds, a range bar once the price has left its range (or a
pause in the data has cut it). A bar's close time is when it became final.

Rows are (epoch seconds, bid open, high, low, close, ask open, high, low, close,
quote changes), oldest first. A bar for the runner is [open ms, open, high, low,
close, volume, close ms].
"""

from bisect import bisect_left

from src.price_recorder.bars import BAR_SECONDS
from src.price_recorder.range_bars import BuiltBar, RangeBarBuilder

SIDES = ("bid", "ask")
_SIDE_COLUMNS = {"bid": slice(1, 5), "ask": slice(5, 9)}
# Ten-second rows kept for the volumes of range bars, before the older are dropped.
_KEPT_ROWS = 10_000


def side_prices(row, side: str) -> tuple | None:
    """The four prices of one side of a row; None for an ask that is not there."""
    prices = tuple(row[_SIDE_COLUMNS[side]])
    return None if any(price is None for price in prices) else prices


def bucket_end(start: int, seconds: int) -> int:
    """When the bucket of ``seconds`` that begins at ``start`` is over: with the last ten seconds that begin in it."""
    return -(-(start + seconds) // BAR_SECONDS) * BAR_SECONDS


class TimeBars:
    """Bars of ``seconds`` of one side, in the order their buckets end."""

    def __init__(self, seconds: int, side: str):
        if side not in SIDES:
            raise ValueError(f"Unknown side {side!r}")
        self._seconds = seconds
        self._side = side
        self._bucket: list | None = None

    def add(self, rows, covered_until: int | None = None) -> list[list]:
        """
        The bars made final by ``rows`` (after those already added) and by the feed
        having recorded every ten seconds before ``covered_until`` (epoch seconds).
        """
        done = []
        for row in rows:
            start = row[0] - row[0] % self._seconds
            bucket = self._bucket
            if bucket is not None and bucket[0] != start:
                # A later bucket began: the feed writes its ten seconds in order.
                done.append(bucket)
                bucket = None
            if bucket is None:
                self._bucket = [start, *row[1:10]]
                continue
            bucket[2] = max(bucket[2], row[2])
            bucket[3] = min(bucket[3], row[3])
            bucket[4] = row[4]
            if bucket[5] is None:
                bucket[5] = row[5]
            if row[6] is not None:
                bucket[6] = row[6] if bucket[6] is None else max(bucket[6], row[6])
            if row[7] is not None:
                bucket[7] = row[7] if bucket[7] is None else min(bucket[7], row[7])
            if row[8] is not None:
                bucket[8] = row[8]
            bucket[9] += row[9]
        if (
            self._bucket is not None and covered_until is not None
            and covered_until >= bucket_end(self._bucket[0], self._seconds)
        ):
            done.append(self._bucket)
            self._bucket = None
        bars = []
        for bucket in done:
            prices = side_prices(bucket, self._side)
            if prices is not None:
                bars.append([bucket[0] * 1000, *prices, bucket[9], bucket_end(bucket[0], self._seconds) * 1000])
        return bars


class RangeBars:
    """Range bars of ``ticks`` steps of one side, each once it has ended."""

    def __init__(self, ticks: int, decimals: int, side: str):
        if side not in SIDES:
            raise ValueError(f"Unknown side {side!r}")
        self._builder = RangeBarBuilder(ticks)
        self._decimals = decimals
        self._scale = 10 ** decimals
        self._side = side
        self._times: list[int] = []
        self._updates: list[int] = []
        self._previous_t: int | None = None
        self._previous_ended: int | None = None

    def add(self, rows, covered_until: int | None = None) -> list[list]:
        """The range bars ``rows`` ended (``covered_until`` changes nothing: a range bar ends with a price)."""
        bars = []
        scale = self._scale
        for row in rows:
            prices = side_prices(row, self._side)
            if prices is None:
                continue
            self._times.append(row[0])
            self._updates.append(row[9])
            first, high, low, last = (round(price * scale) for price in prices)
            for built in self._builder.add(row[0], first, high, low, last):
                bars.append(self._bar(built))
        if len(self._times) > _KEPT_ROWS and self._previous_ended is not None:
            cut = bisect_left(self._times, self._previous_ended)
            del self._times[:cut], self._updates[:cut]
        return bars

    def _bar(self, built: BuiltBar) -> list:
        t = built.began if self._previous_t is None else max(built.began, self._previous_t + 1)
        self._previous_t = t
        # Each ten seconds goes to the first bar running at its start.
        start = built.began if self._previous_ended is None else max(self._previous_ended, built.began)
        volume = sum(self._updates[bisect_left(self._times, start):bisect_left(self._times, built.ended)])
        self._previous_ended = built.ended
        scale, decimals = self._scale, self._decimals
        return [
            t * 1000,
            round(built.open / scale, decimals), round(built.high / scale, decimals),
            round(built.low / scale, decimals), round(built.close / scale, decimals),
            volume, built.ended * 1000,
        ]
