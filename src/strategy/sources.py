"""
Where the bars of a backtest come from: files of ten-second bars in the
columns of ``wataprices`` (src/price_recorder/export.py).

Two kinds of files are read the same way: the days fetched from Dukascopy
(src/strategy/history/dukascopy.py) and what ``wataprices`` exports from the
recorder's tables, where the index is one instrument among the turbos.
"""

import calendar
import csv
import gzip
import os
from datetime import date, datetime, timedelta, timezone
from typing import Iterator

from src.strategy.bars import IndexBar

UNDERLYING = "underlying"
_SUFFIX = ".csv.gz"


class SeveralInstruments(ValueError):
    """The files hold more than one instrument for the indice: one has to be chosen."""


def _epoch(text: str) -> int:
    # "2026-10-12T08:00:00Z", always UTC: read by position, a hundred times faster than strptime.
    return calendar.timegm((
        int(text[0:4]), int(text[5:7]), int(text[8:10]), int(text[11:13]), int(text[14:16]), int(text[17:19]),
    ))


def _day_of_file(name: str) -> date | None:
    """The day a file is named after ("2026-10-12.csv.gz"), when it is."""
    try:
        return date.fromisoformat(name[: -len(_SUFFIX)])
    except ValueError:
        return None


class FileBars:
    """The index bars of ``indice`` found in one file, or in every file of a directory."""

    def __init__(self, location: str, indice: str, asset_type: str | None = None):
        self._location = location
        self._indice = indice
        self._asset_type = asset_type

    def _files(self, start: datetime, end: datetime) -> list[str]:
        if not os.path.isdir(self._location):
            return [self._location]
        first, last = start.astimezone(timezone.utc).date(), (end - timedelta(seconds=1)).astimezone(timezone.utc).date()
        files = []
        for name in sorted(os.listdir(self._location)):
            if not name.endswith(_SUFFIX):
                continue
            day = _day_of_file(name)
            # A file named after a day holds that UTC day only: the others need not be opened.
            if day is None or first <= day <= last:
                files.append(os.path.join(self._location, name))
        return files

    def bars(self, start: datetime, end: datetime) -> Iterator[IndexBar]:
        """The bars that begin in [start, end), oldest first."""
        first, last = int(start.timestamp()), int(end.timestamp())
        instrument = None
        previous = None
        for path in self._files(start, end):
            with gzip.open(path, "rt", newline="", encoding="utf-8") as in_file:
                for row in csv.DictReader(in_file):
                    if row["role"] != UNDERLYING or row["indice"] != self._indice:
                        continue
                    if self._asset_type is not None and row["asset_type"] != self._asset_type:
                        continue
                    at = _epoch(row["ts"])
                    if at < first or at >= last:
                        continue
                    found = (row["uic"], row["asset_type"])
                    if instrument is None:
                        instrument = found
                    elif found != instrument:
                        raise SeveralInstruments(
                            f"{self._location} holds several instruments for {self._indice} "
                            f"({instrument[1]} and {found[1]}): choose one with its asset type"
                        )
                    if previous is not None and at <= previous:
                        raise ValueError(f"{path}: bars are not in time order at {row['ts']}")
                    previous = at

                    bid_open, bid_high = float(row["bid_open"]), float(row["bid_high"])
                    bid_low, bid_close = float(row["bid_low"]), float(row["bid_close"])
                    # An instrument quoted with one price has no ask of its own.
                    yield IndexBar(
                        start=at,
                        bid_open=bid_open, bid_high=bid_high, bid_low=bid_low, bid_close=bid_close,
                        ask_open=float(row["ask_open"]) if row["ask_open"] else bid_open,
                        ask_high=float(row["ask_high"]) if row["ask_high"] else bid_high,
                        ask_low=float(row["ask_low"]) if row["ask_low"] else bid_low,
                        ask_close=float(row["ask_close"]) if row["ask_close"] else bid_close,
                        updates=int(row["updates"] or 0),
                    )
