"""
The recorded days a backtest trades on: the turbos the price recorder kept for
an indice, their ten-second bars, and whether the recorder was listening.

A day is read whole before it is traded (``DayData``) and let go after: one
day of a hundred turbos is a few tens of megabytes in arrays. Two sources give
the same days:

- ``DatabaseData``: the recorder's tables, through one read-only connection
  and their primary keys only (``price_bar_10s`` is keyed by instrument and
  time, and partitioned by month).
- ``FileData``: files in the columns of ``wataprices``
  (src/price_recorder/export.py), with an ``instrument_id`` column when they
  are to be linked to the admin's charts. Tests and the development server
  use them, and so can an export of the server's days.
"""

import csv
import glob
import gzip
import math
import os
from array import array
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from src.backtest.clock import BAR_SECONDS

NAN = float("nan")
LONG, SHORT = "long", "short"
# Buckets of the recorder's coverage for a day that has no row of its own: told by the bars themselves.
UNKNOWN_COVERAGE = None


@dataclass(frozen=True)
class TurboRef:
    instrument_id: int
    uic: int | None
    asset_type: str
    symbol: str | None
    description: str | None
    issuer: str | None
    barrier: float
    direction: str
    currency: str | None = None

    def to_dict(self) -> dict:
        return {
            "instrument_id": self.instrument_id, "uic": self.uic, "asset_type": self.asset_type,
            "symbol": self.symbol, "description": self.description, "issuer": self.issuer,
            "barrier": self.barrier, "direction": self.direction, "currency": self.currency,
        }


@dataclass
class TurboSeries:
    """The ten-second bars of one turbo for one day, in columns, oldest first."""

    ref: TurboRef
    ts: array = field(default_factory=lambda: array("q"))
    bid_open: array = field(default_factory=lambda: array("d"))
    bid_high: array = field(default_factory=lambda: array("d"))
    bid_low: array = field(default_factory=lambda: array("d"))
    bid_close: array = field(default_factory=lambda: array("d"))
    # NaN where the turbo had no ask.
    ask_open: array = field(default_factory=lambda: array("d"))
    ask_close: array = field(default_factory=lambda: array("d"))

    def append(self, ts: int, bid_open, bid_high, bid_low, bid_close, ask_open, ask_close) -> None:
        self.ts.append(int(ts))
        self.bid_open.append(float(bid_open))
        self.bid_high.append(float(bid_high))
        self.bid_low.append(float(bid_low))
        self.bid_close.append(float(bid_close))
        self.ask_open.append(NAN if ask_open is None or ask_open == "" else float(ask_open))
        self.ask_close.append(NAN if ask_close is None or ask_close == "" else float(ask_close))

    def __len__(self) -> int:
        return len(self.ts)

    @property
    def first(self) -> int | None:
        return self.ts[0] if self.ts else None

    @property
    def last(self) -> int | None:
        return self.ts[-1] if self.ts else None

    def index_at(self, boundary: int) -> int | None:
        """The bar that begins at ``boundary``, if the turbo had one (it was quoted then)."""
        place = bisect_left(self.ts, boundary)
        return place if place < len(self.ts) and self.ts[place] == boundary else None

    def first_from(self, moment: float) -> int:
        """The first bar that begins at or after ``moment``."""
        return bisect_left(self.ts, math.ceil(moment))

    def last_before(self, moment: float) -> int | None:
        """The last bar that began before ``moment``."""
        place = bisect_left(self.ts, math.ceil(moment)) - 1
        return place if place >= 0 else None

    def quote_at(self, boundary: int) -> tuple[float, float | None] | None:
        """The bid and ask standing at ``boundary``: the open of the bar that begins there."""
        index = self.index_at(boundary)
        if index is None:
            return None
        ask = self.ask_open[index]
        return self.bid_open[index], None if math.isnan(ask) else ask

    def alive_at(self, boundary: int) -> bool:
        """Recorded around ``boundary``: its first bar is not after it, its last not before it."""
        return bool(self.ts) and self.ts[0] <= boundary <= self.ts[-1]

    def day_high(self) -> float:
        return max(self.bid_high) if self.bid_high else NAN

    @property
    def decimals(self) -> int:
        """
        The decimals its quotes have, read from its first bids: two at least (a
        turbo is quoted to the cent), more when its bids show more.
        """
        if not hasattr(self, "_decimals"):
            sample = list(self.bid_close[:200]) + list(self.bid_open[:200])
            places = [len(f"{value:.6f}".rstrip("0").split(".")[1]) for value in sample if not math.isnan(value)]
            self._decimals = min(max([2, *places]), 4)
        return self._decimals

    def tick_at_or_above(self, price: float) -> float:
        """The first price it can be quoted at that is not below ``price``."""
        step = 10 ** self.decimals
        return math.ceil(round(price * step, 6)) / step

    def tick_at_or_below(self, price: float) -> float:
        """The last price it can be quoted at that is not above ``price``."""
        step = 10 ** self.decimals
        return math.floor(round(price * step, 6)) / step


@dataclass
class AssetSeries:
    """The ten-second bars of the asset a run's signals and chart are of, for the prices noted beside trades."""

    ts: array = field(default_factory=lambda: array("q"))
    close: array = field(default_factory=lambda: array("d"))

    def at(self, moment: float) -> float | None:
        """The asset's price at ``moment``: the close of the last bar that ended by then."""
        place = bisect_right(self.ts, moment - BAR_SECONDS) - 1
        return self.close[place] if place >= 0 else None


@dataclass
class DayData:
    day: date
    start: int
    end: int
    indice: str
    # By direction, the turbos recorded that day, the closest to their knock-out level first
    # (long: the highest level first), as the trader sorts its candidates.
    turbos: dict[str, list[TurboSeries]] = field(default_factory=dict)
    # The buckets the recorder was listening in (a quote within its last 30 s), or None when unknown.
    covered: set[int] | None = None
    asset: AssetSeries | None = None

    def is_covered(self, boundary: int) -> bool:
        if self.covered is None:
            # Without the recorder's own account, a bucket where any turbo had a bar was listened to.
            return any(series.index_at(boundary) is not None for series in self.all_turbos())
        return boundary in self.covered

    def all_turbos(self) -> list[TurboSeries]:
        return [series for direction in (LONG, SHORT) for series in self.turbos.get(direction, [])]

    def recorded_minutes(self) -> int:
        if self.covered is not None:
            return len(self.covered) * BAR_SECONDS // 60
        stamps = {stamp for series in self.all_turbos() for stamp in series.ts}
        return len(stamps) * BAR_SECONDS // 60

    def span(self) -> tuple[int | None, int | None]:
        firsts = [series.first for series in self.all_turbos() if series.first is not None]
        lasts = [series.last for series in self.all_turbos() if series.last is not None]
        return (min(firsts) if firsts else None, max(lasts) + BAR_SECONDS if lasts else None)


def sort_turbos(turbos: dict[str, list[TurboSeries]]) -> dict[str, list[TurboSeries]]:
    """Each direction's turbos in the trader's order: long by the highest level first, short by the lowest."""
    return {
        direction: sorted(series, key=lambda one: one.ref.barrier, reverse=direction != SHORT)
        for direction, series in turbos.items()
    }


# ── from files ──


def _epoch(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())


def _number(text: str | None):
    if text is None or text == "":
        return None
    return float(text)


class FileData:
    """
    Recorded days from files: ``<root>/saxo/*.csv.gz`` in the columns of
    ``wataprices`` (the turbos and the index of an indice, an
    ``instrument_id`` column optional), and ``<root>/saxo/coverage-<day>.csv``
    (``ts``, ``feed_ok``) when the recorder's own account of the day is known.
    Every file is read once: they are a test's or a few days' worth.
    """

    def __init__(self, root: str):
        self._root = root
        self._rows: list[dict] | None = None
        self._coverage: dict[int, bool] | None = None

    def _load(self) -> None:
        if self._rows is not None:
            return
        rows, ids = [], {}
        for path in sorted(glob.glob(os.path.join(self._root, "saxo", "*.csv.gz"))):
            with gzip.open(path, "rt", newline="", encoding="utf-8") as in_file:
                for row in csv.DictReader(in_file):
                    key = (row.get("uic"), row.get("asset_type"))
                    if row.get("instrument_id"):
                        row["instrument_id"] = int(row["instrument_id"])
                    else:
                        # Not linked to the admin's instruments: an id of its own, below theirs.
                        row["instrument_id"] = ids.setdefault(key, -(len(ids) + 1))
                    rows.append(row)
        coverage: dict[int, bool] = {}
        for path in sorted(glob.glob(os.path.join(self._root, "saxo", "coverage-*.csv"))):
            with open(path, newline="", encoding="utf-8") as in_file:
                for row in csv.DictReader(in_file):
                    coverage[_epoch(row["ts"])] = row["feed_ok"].strip().lower() in ("1", "true", "t", "yes")
        self._rows = rows
        self._coverage = coverage if coverage else None

    async def day(self, indice: str, day: date, start: int, end: int) -> DayData:
        self._load()
        turbos: dict[int, TurboSeries] = {}
        for row in self._rows:
            if row.get("role") != "turbo" or row.get("indice") != indice:
                continue
            at = _epoch(row["ts"])
            if not start <= at < end:
                continue
            series = turbos.get(row["instrument_id"])
            if series is None:
                barrier = _number(row.get("barrier"))
                direction = (row.get("direction") or "").lower()
                if barrier is None or direction not in (LONG, SHORT):
                    continue
                series = turbos[row["instrument_id"]] = TurboSeries(TurboRef(
                    instrument_id=row["instrument_id"],
                    uic=int(row["uic"]) if row.get("uic") else None,
                    asset_type=row.get("asset_type") or "",
                    symbol=row.get("symbol") or None,
                    description=row.get("description") or None,
                    issuer=row.get("issuer") or None,
                    barrier=barrier,
                    direction=direction,
                    currency=row.get("currency") or None,
                ))
            series.append(at, row["bid_open"], row["bid_high"], row["bid_low"], row["bid_close"],
                          row.get("ask_open"), row.get("ask_close"))
        by_direction: dict[str, list[TurboSeries]] = {}
        for series in turbos.values():
            order = sorted(range(len(series.ts)), key=lambda index: series.ts[index])
            if any(order[place] != place for place in range(len(order))):
                series = _reordered(series, order)
            by_direction.setdefault(series.ref.direction, []).append(series)
        covered = None
        if self._coverage is not None:
            stamps = {stamp for stamp in self._coverage if start <= stamp < end}
            if stamps:
                covered = {stamp for stamp in stamps if self._coverage[stamp]}
        return DayData(day=day, start=start, end=end, indice=indice, turbos=sort_turbos(by_direction), covered=covered)

    async def close(self) -> None:
        pass


def _reordered(series: TurboSeries, order: list[int]) -> TurboSeries:
    sorted_series = TurboSeries(series.ref)
    for index in order:
        sorted_series.append(
            series.ts[index], series.bid_open[index], series.bid_high[index], series.bid_low[index],
            series.bid_close[index], series.ask_open[index], series.ask_close[index],
        )
    return sorted_series


# ── from the database ──

# A turbo seen at a refresh this long before a day is still recorded on it (kept until its day ends).
_SEEN_MARGIN = timedelta(days=1)


class DatabaseData:
    """
    The recorder's tables, read through ``db`` (a PostgresConnectionManager
    opened read-only). Every query names an instrument and a period, so that
    PostgreSQL reads one or two monthly partitions by their key.
    """

    def __init__(self, db):
        self._db = db

    async def day(self, indice: str, day: date, start: int, end: int) -> DayData:
        start_at = datetime.fromtimestamp(start, timezone.utc)
        end_at = datetime.fromtimestamp(end, timezone.utc)
        instruments = await self._db.fetch(
            """
            SELECT id, uic, asset_type, symbol, description, currency, direction, barrier, issuer
            FROM price_instrument
            WHERE role = 'turbo' AND indice = $1 AND barrier IS NOT NULL
              AND first_seen_at < $3 AND last_seen_at >= $2
            ORDER BY id
            """,
            indice, start_at - _SEEN_MARGIN, end_at,
        )
        by_direction: dict[str, list[TurboSeries]] = {}
        for instrument in instruments:
            direction = (instrument["direction"] or "").lower()
            if direction not in (LONG, SHORT):
                continue
            rows = await self._db.fetch(
                """
                SELECT extract(epoch FROM ts)::bigint AS t, bid_open, bid_high, bid_low, bid_close, ask_open, ask_close
                FROM price_bar_10s
                WHERE instrument_id = $1 AND ts >= $2 AND ts < $3
                ORDER BY ts
                """,
                instrument["id"], start_at, end_at,
            )
            if not rows:
                continue
            series = TurboSeries(TurboRef(
                instrument_id=instrument["id"], uic=instrument["uic"], asset_type=instrument["asset_type"],
                symbol=instrument["symbol"], description=instrument["description"], issuer=instrument["issuer"],
                barrier=float(instrument["barrier"]), direction=direction, currency=instrument["currency"],
            ))
            for row in rows:
                series.append(row["t"], row["bid_open"], row["bid_high"], row["bid_low"], row["bid_close"],
                              row["ask_open"], row["ask_close"])
            by_direction.setdefault(direction, []).append(series)
        coverage = await self._db.fetch(
            """
            SELECT extract(epoch FROM ts)::bigint AS t, feed_ok
            FROM price_recorder_coverage
            WHERE ts >= $1 AND ts < $2
            """,
            start_at, end_at,
        )
        covered = {row["t"] for row in coverage if row["feed_ok"]} if coverage else None
        return DayData(day=day, start=start, end=end, indice=indice, turbos=sort_turbos(by_direction), covered=covered)

    async def close(self) -> None:
        await self._db.close()
