"""
History of an index from Dukascopy's public tick files, as ten-second bars
(the US Tech 100 by default), for the admin's Data pages and the strategies'
charts and replays.

    python -m src.price_history.dukascopy --from 2026-09-14 --to 2026-10-12

One file per hour is fetched (bid and ask of every tick) and kept as it came
under ``var/lib/price_history/dukascopy/raw``. Each UTC day whose hours are all
there becomes one file of ten-second bars, built with the recorder's own
``BarBuilder`` and written in the columns of ``wataprices``.

The server refuses many requests, and stops answering altogether for a while
when it is asked too much. A run fetches what it can, a few hours at a time,
and says what is missing; the next run only asks for that. When nothing is
answered any more it stops instead of insisting. A day with a hole in it is
never written.

These are another broker's prices than the ones WATA trades on, reached
through an address Dukascopy does not document: for research, not for live
decisions.
"""

import argparse
import csv
import gzip
import io
import json
import lzma
import os
import struct
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable, NamedTuple

from src.price_recorder.bars import Bar, BarBuilder, Quote
from src.price_recorder.export import COLUMNS
from src.price_history.process import lower_priority

BASE_URL = "https://datafeed.dukascopy.com/datafeed"
SYMBOL = "USATECHIDXUSD"
# Prices are whole numbers in the files: thousandths of a point for this index.
PRICE_SCALE = 1000
SOURCE = "dukascopy"
ASSET_TYPE = "Dukascopy"
DEFAULT_ROOT = os.path.join("var", "lib", "price_history")
DEFAULT_LABEL = "us100"
DEFAULT_WORKERS = 2
DEFAULT_ATTEMPTS = 5
RETRY_PAUSE_SECONDS = 3.0
REQUEST_TIMEOUT_SECONDS = 15.0
# A day in which this many hours were asked for and none was answered: the
# server is not answering this address any more, and asking on makes it last.
UNANSWERED_HOURS_TO_STOP = 3

# One tick: milliseconds into the hour, ask, bid, then the two volumes.
_TICK = struct.Struct(">IIIff")
_KEY = "index"

CACHED = "cached"
FETCHED = "fetched"
CLOSED = "closed"
# Not published yet: the hour is not over, or only just.
UNPUBLISHED = "unpublished"
FAILED = "failed"

# A fetch answers with the bytes of the file (empty for an hour without a tick),
# or None when the server has no such file.
Fetch = Callable[[str], bytes | None]


class CorruptHour(Exception):
    """What was received is not a tick file."""


class Tick(NamedTuple):
    at: float
    bid: float
    ask: float


def hour_url(symbol: str, hour: datetime) -> str:
    # Months are counted from zero in the address.
    return f"{BASE_URL}/{symbol}/{hour.year}/{hour.month - 1:02d}/{hour.day:02d}/{hour.hour:02d}h_ticks.bi5"


def decode_hour(raw: bytes, hour: datetime, price_scale: int = PRICE_SCALE) -> list[Tick]:
    """The ticks of one hourly file, oldest first. An empty file is an hour without a tick."""
    if not raw:
        return []
    try:
        data = lzma.decompress(raw)
        records = list(_TICK.iter_unpack(data))
    except (lzma.LZMAError, struct.error) as e:
        raise CorruptHour(str(e)) from e
    start = hour.timestamp()
    return [Tick(start + ms / 1000, bid / price_scale, ask / price_scale) for ms, ask, bid, _, _ in records]


def market_is_closed(hour: datetime) -> bool:
    """Saturday, and Sunday before the evening opening (UTC): there is nothing to ask for."""
    weekday = hour.weekday()
    return weekday == 5 or (weekday == 6 and hour.hour < 20)


def day_hours(day: date) -> list[datetime]:
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    return [start + timedelta(hours=hour) for hour in range(24)]


def raw_path(root: str, symbol: str, hour: datetime) -> str:
    return os.path.join(
        root, SOURCE, "raw", symbol, f"{hour:%Y}", f"{hour:%m}", f"{hour:%d}", f"{hour:%H}h_ticks.bi5",
    )


def bars_directory(root: str, label: str) -> str:
    return os.path.join(root, SOURCE, "bars", label)


def day_path(root: str, label: str, day: date) -> str:
    return os.path.join(bars_directory(root, label), f"{day.isoformat()}.csv.gz")


def _write_atomically(path: str, write: Callable[[str], None]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    partial = f"{path}.part"
    write(partial)
    os.replace(partial, path)


def _save_bytes(path: str, content: bytes) -> None:
    def write(partial: str) -> None:
        with open(partial, "wb") as out_file:
            out_file.write(content)

    _write_atomically(path, write)


def http_fetch(timeout: float = REQUEST_TIMEOUT_SECONDS) -> Fetch:
    import httpx

    client = httpx.Client(timeout=timeout, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True)

    def fetch(url: str) -> bytes | None:
        response = client.get(url)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.content

    return fetch


def fetch_hour(
    root: str,
    symbol: str,
    hour: datetime,
    fetch: Fetch,
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    pause: float = RETRY_PAUSE_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Make sure the hour's file is kept locally; says how it went."""
    if market_is_closed(hour):
        return CLOSED
    path = raw_path(root, symbol, hour)
    if os.path.exists(path):
        return CACHED
    url = hour_url(symbol, hour)
    for attempt in range(attempts):
        if attempt:
            sleep(pause * attempt)
        try:
            content = fetch(url)
            if content is None:
                return UNPUBLISHED
            # Checked before it is kept: an error page saved as an hour would never be asked for again.
            decode_hour(content, hour)
        except Exception:
            continue
        _save_bytes(path, content)
        return FETCHED
    return FAILED


def ticks_of_day(root: str, symbol: str, day: date, price_scale: int = PRICE_SCALE) -> tuple[list[Tick], list[datetime]]:
    """The ticks kept for the day, and the hours that are not there (closed hours apart)."""
    ticks: list[Tick] = []
    absent: list[datetime] = []
    for hour in day_hours(day):
        if market_is_closed(hour):
            continue
        path = raw_path(root, symbol, hour)
        if not os.path.exists(path):
            absent.append(hour)
            continue
        with open(path, "rb") as in_file:
            ticks += decode_hour(in_file.read(), hour, price_scale)
    return ticks, absent


def bars_from_ticks(ticks: list[Tick], until: float) -> list[Bar]:
    """Ten-second bars of the ticks, by the recorder's rules, every bucket before ``until`` closed."""
    builder = BarBuilder()
    for tick in ticks:
        builder.update(_KEY, Quote(bid=tick.bid, ask=tick.ask), tick.at)
    return [bar for bucket in builder.close_until(until) for bar in bucket.bars]


def write_day_file(path: str, bars: list[Bar], *, symbol: str, label: str) -> None:
    described = {
        "uic": "", "asset_type": ASSET_TYPE, "symbol": symbol, "description": f"{label} ({SOURCE})",
        "role": "underlying", "indice": label, "direction": "", "barrier": "", "issuer": "", "delayed_minutes": 0,
    }

    def write(partial: str) -> None:
        # Without the time of writing in its header: the same bars give the same file, to the byte,
        # so that whoever reads a file can tell from its bytes whether its prices changed.
        with gzip.GzipFile(partial, "wb", mtime=0) as packed, io.TextIOWrapper(packed, newline="", encoding="utf-8") as out_file:
            writer = csv.writer(out_file)
            writer.writerow(COLUMNS)
            for bar in bars:
                row = {
                    **described,
                    "ts": datetime.fromtimestamp(bar.start, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "bid_open": bar.bid_open, "bid_high": bar.bid_high, "bid_low": bar.bid_low, "bid_close": bar.bid_close,
                    "ask_open": bar.ask_open, "ask_high": bar.ask_high, "ask_low": bar.ask_low, "ask_close": bar.ask_close,
                    "updates": bar.updates,
                }
                writer.writerow([row[name] for name in COLUMNS])

    _write_atomically(path, write)


@dataclass
class DayReport:
    day: date
    # "written", "kept" (already complete), "partial" (the day is not over), "incomplete",
    # "empty", or "not tried" (the run stopped before it: the server was not answering).
    status: str
    bars: int = 0
    failed_hours: list[datetime] = field(default_factory=list)
    # Hours the server answered for, to say it has no such file: asking again will not bring them.
    unpublished_hours: int = 0
    # Hours it did not answer for at all.
    unanswered_hours: int = 0


def sync(
    first_day: date,
    last_day: date,
    *,
    fetch: Fetch,
    root: str = DEFAULT_ROOT,
    symbol: str = SYMBOL,
    label: str = DEFAULT_LABEL,
    workers: int = DEFAULT_WORKERS,
    attempts: int = DEFAULT_ATTEMPTS,
    pause: float = RETRY_PAUSE_SECONDS,
    progress: Callable[[str], None] = lambda message: None,
) -> list[DayReport]:
    """Fetch what is missing between the two days (included) and write the days that can be."""
    days = [first_day + timedelta(days=offset) for offset in range((last_day - first_day).days + 1)]
    reports: list[DayReport] = []
    unanswered = False
    for day in days:
        if unanswered:
            reports.append(DayReport(day, "not tried"))
            continue
        hours = day_hours(day)
        if all(market_is_closed(hour) for hour in hours):
            reports.append(DayReport(day, "empty"))
            continue
        path = day_path(root, label, day)
        open_hours = [hour for hour in hours if not market_is_closed(hour)]
        if os.path.exists(path) and all(os.path.exists(raw_path(root, symbol, hour)) for hour in open_hours):
            reports.append(DayReport(day, "kept"))
            continue

        with ThreadPoolExecutor(max_workers=workers) as pool:
            outcomes = list(pool.map(
                lambda hour: fetch_hour(root, symbol, hour, fetch, attempts=attempts, pause=pause), open_hours,
            ))
        failed = [hour for hour, outcome in zip(open_hours, outcomes) if outcome == FAILED]
        unpublished = [hour for hour, outcome in zip(open_hours, outcomes) if outcome == UNPUBLISHED]
        # A hole is an hour missing before one that is there. Hours not published
        # yet at the end of the day are only a day that is not over.
        present = [hour for hour, outcome in zip(open_hours, outcomes) if outcome in (CACHED, FETCHED)]
        hole = bool(failed) or (bool(present) and any(hour < present[-1] for hour in unpublished))
        if hole:
            reports.append(DayReport(
                day, "incomplete", failed_hours=failed or unpublished,
                unpublished_hours=len(unpublished), unanswered_hours=len(failed),
            ))
            progress(f"{day}: {len(failed or unpublished)} hour(s) could not be fetched")
            answered = outcomes.count(FETCHED) + outcomes.count(UNPUBLISHED)
            if not answered and len(failed) >= UNANSWERED_HOURS_TO_STOP:
                unanswered = True
                progress("The server answers nothing any more: stopping here. Try again later.")
            continue
        if not present:
            reports.append(DayReport(day, "empty", unpublished_hours=len(unpublished)))
            continue

        ticks, _ = ticks_of_day(root, symbol, day)
        until = (present[-1] + timedelta(hours=1)).timestamp()
        bars = bars_from_ticks(ticks, until)
        write_day_file(path, bars, symbol=symbol, label=label)
        status = "partial" if unpublished else "written"
        reports.append(DayReport(day, status, bars=len(bars), unpublished_hours=len(unpublished)))
        progress(f"{day}: {len(bars)} bars" + (" (the day is not over)" if unpublished else ""))
    return reports


def parse_arguments(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m src.price_history.dukascopy",
        description="Fetch the US Tech 100 index from Dukascopy as ten-second bars.",
    )
    parser.add_argument("--from", dest="first_day", required=True, type=date.fromisoformat, help="first day (UTC), YYYY-MM-DD")
    parser.add_argument("--to", dest="last_day", type=date.fromisoformat, help="last day included (default: the first day)")
    parser.add_argument("--root", default=DEFAULT_ROOT, help=f"where the history is kept (default: {DEFAULT_ROOT})")
    parser.add_argument("--symbol", default=SYMBOL, help=f"Dukascopy's name of the instrument (default: {SYMBOL})")
    parser.add_argument("--label", default=DEFAULT_LABEL, help=f"the indice it stands for (default: {DEFAULT_LABEL})")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help="hours fetched at the same time")
    parser.add_argument("--attempts", type=int, default=DEFAULT_ATTEMPTS, help="tries per hour before giving up for this run")
    parser.add_argument("--low-priority", action="store_true", help="give way to everything else on the machine")
    parser.add_argument("--report", help="file to write what became of each day to, as JSON")
    arguments = parser.parse_args(argv)
    arguments.last_day = arguments.last_day or arguments.first_day
    if arguments.last_day < arguments.first_day:
        parser.error("--to is before --from")
    return arguments


def write_report(path: str, reports: list[DayReport]) -> None:
    """What became of each day, for whoever ran the command without reading what it printed."""
    content = {
        "days": [
            {
                "day": report.day.isoformat(), "status": report.status, "bars": report.bars,
                "unpublished_hours": report.unpublished_hours, "unanswered_hours": report.unanswered_hours,
            }
            for report in reports
        ],
    }

    def write(partial: str) -> None:
        with open(partial, "w", encoding="utf-8") as out_file:
            json.dump(content, out_file, indent=2)

    _write_atomically(os.path.abspath(path), write)


def main(argv=None) -> int:
    arguments = parse_arguments(argv)
    if arguments.low_priority:
        lower_priority()
    reports = sync(
        arguments.first_day, arguments.last_day,
        fetch=http_fetch(), root=arguments.root, symbol=arguments.symbol, label=arguments.label,
        workers=arguments.workers, attempts=arguments.attempts,
        progress=lambda message: print(message, flush=True),
    )
    if arguments.report:
        write_report(arguments.report, reports)
    incomplete = [report for report in reports if report.status in ("incomplete", "not tried")]
    ready = [report for report in reports if report.status in ("written", "kept", "partial")]
    print(f"{len(ready)} day(s) ready in {bars_directory(arguments.root, arguments.label)}, {len(incomplete)} incomplete.")
    if incomplete:
        print("Run the same command again to fetch what is missing: " + ", ".join(str(report.day) for report in incomplete))
    return 1 if incomplete else 0


if __name__ == "__main__":
    sys.exit(main())
