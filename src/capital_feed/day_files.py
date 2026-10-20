"""
Each finished day of a Capital.com instrument as a file of ten-second bars:
``<prices_root>/capital/bars/<label>/<YYYY-MM-DD>.csv.gz``.

Days of UTC, in the columns of ``wataprices`` (src/price_recorder/export.py),
as the days fetched from Dukascopy are, so that one piece of code reads both
(``FileBars``, src/price_history/files.py). A backtest reads the strategy's
asset from them when it runs on files (``python -m src.backtest.run --data
files:<folder>``, through the replay's ``AssetRows``); on the server it reads
the feed's tables. A file is written without the time in its gzip header: the
same bars give the same bytes.

A day is written once it is over and nothing of it is still waiting to be
written to the database; a day the feed was partly away for is written all
the same, with its holes (backtests call a day with too few bars incomplete).
"""

import asyncio
import csv
import gzip
import io
import logging
import os
from datetime import date, datetime, time as day_time, timedelta, timezone

from src.price_recorder.export import COLUMNS

logger = logging.getLogger(__name__)

SOURCE = "capital"
ASSET_TYPE = "Capital.com"
SUFFIX = ".csv.gz"


def bars_directory(root: str, label: str) -> str:
    return os.path.join(root, SOURCE, "bars", label)


def day_path(root: str, label: str, day: date) -> str:
    return os.path.join(bars_directory(root, label), f"{day.isoformat()}{SUFFIX}")


def written_days(root: str, label: str) -> set[date]:
    """The days of ``label`` that have a file."""
    try:
        names = os.listdir(bars_directory(root, label))
    except OSError:
        return set()
    days = set()
    for name in names:
        if not name.endswith(SUFFIX):
            continue
        try:
            days.add(date.fromisoformat(name[: -len(SUFFIX)]))
        except ValueError:
            continue
    return days


def _utc_text(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_day_file(path: str, rows: list[tuple], *, epic: str, label: str, name: str | None) -> None:
    """
    ``rows``: the day's bars, oldest first, as (ts, bid_open, bid_high, bid_low,
    bid_close, ask_open, ask_high, ask_low, ask_close, updates). Written in one
    move: whoever reads the folder never finds half a file.
    """
    described = {
        "uic": "", "asset_type": ASSET_TYPE, "symbol": epic, "description": f"{name or label} (Capital.com)",
        "role": "underlying", "indice": label, "direction": "", "barrier": "", "issuer": "", "delayed_minutes": 0,
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    partial = f"{path}.part"
    # Neither a time nor a name in the gzip header: the bytes are those of the bars alone.
    with open(partial, "wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as packed, \
            io.TextIOWrapper(packed, newline="", encoding="utf-8") as out_file:
        writer = csv.writer(out_file)
        writer.writerow(COLUMNS)
        for ts, bid_open, bid_high, bid_low, bid_close, ask_open, ask_high, ask_low, ask_close, updates in rows:
            row = {
                **described,
                "ts": _utc_text(ts),
                "bid_open": bid_open, "bid_high": bid_high, "bid_low": bid_low, "bid_close": bid_close,
                "ask_open": ask_open, "ask_high": ask_high, "ask_low": ask_low, "ask_close": ask_close,
                "updates": updates,
            }
            writer.writerow([row[column] for column in COLUMNS])
    os.replace(partial, path)


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, day_time.min, timezone.utc)
    return start, start + timedelta(days=1)


class DayFiles:
    """Writes the days that are over and have no file yet, from the bars in the database."""

    def __init__(self, root: str, store):
        self._root = root
        self._store = store
        # Days found without a bar (a weekend): not asked for again while the feed runs.
        self._empty: set[tuple[str, date]] = set()
        self.written = 0
        self.last_written: tuple[str, date] | None = None

    async def write_finished_days(self, instruments, written_until: datetime) -> list[tuple[str, date]]:
        """
        ``instruments``: (id, epic, label, name). ``written_until``: every bar
        before it is in the database. Returns the (label, day) written.
        """
        done = []
        for instrument_id, epic, label, name in instruments:
            first = await self._store.first_bar_at(instrument_id)
            if first is None:
                continue
            existing = await asyncio.to_thread(written_days, self._root, label)
            day = first.astimezone(timezone.utc).date()
            while True:
                start, end = _day_bounds(day)
                if end > written_until:
                    break
                if day not in existing and (label, day) not in self._empty:
                    rows = await self._store.bars_between(instrument_id, start, end)
                    if rows:
                        path = day_path(self._root, label, day)
                        await asyncio.to_thread(write_day_file, path, rows, epic=epic, label=label, name=name)
                        logger.info("Day file written: %s (%d bars).", path, len(rows))
                        self.written += 1
                        self.last_written = (label, day)
                        done.append((label, day))
                    else:
                        self._empty.add((label, day))
                day += timedelta(days=1)
        return done
