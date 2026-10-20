"""
What the backtests of the fake admin server trade on: the made-up index and
turbos of fake_prices.py, with the recorder's coverage, written at start as
the files the backtest's command reads (src/backtest/data.py ``FileData``),
and Capital.com's made-up bars as the feed's day files. The same functions
make what the Data pages show, so a trade's chart is the turbo it traded.

The last trading days only, today's up to the moment the server started.
"""

import csv
import gzip
import os
from datetime import date, datetime, timedelta, timezone

import fake_capital
import fake_prices
from src.capital_feed.day_files import day_path as capital_day_path
from src.capital_feed.day_files import write_day_file as write_capital_day
from src.price_recorder.export import COLUMNS

DAYS = 8
BAR_SECONDS = 10


def recorded_days(now: datetime, count: int = DAYS) -> list[date]:
    """The last ``count`` weekdays up to today, oldest first."""
    day = now.astimezone(fake_prices.TRADING_TIMEZONE).date()
    days = []
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    return sorted(days)


def day_bounds(day: date) -> tuple[datetime, datetime]:
    zone = fake_prices.TRADING_TIMEZONE
    start = zone.localize(datetime.combine(day, datetime.min.time()))
    end = zone.localize(datetime.combine(day + timedelta(days=1), datetime.min.time()))
    return start, end


def _utc(seconds: int) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write(root: str, now: datetime) -> list[date]:
    """Write the recorded days under ``root`` (``saxo/`` and ``capital/``); the days written."""
    days = recorded_days(now)
    saxo = os.path.join(root, "saxo")
    os.makedirs(saxo, exist_ok=True)
    capital_days: dict[date, list] = {}
    for day in days:
        start, end = day_bounds(day)
        path = os.path.join(saxo, f"{day.isoformat()}.csv.gz")
        with gzip.open(path, "wt", newline="", encoding="utf-8") as out_file:
            writer = csv.writer(out_file)
            writer.writerow([*COLUMNS, "instrument_id"])
            for instrument in fake_prices.INSTRUMENTS:
                for row in fake_prices.bars(instrument["id"], start, end, BAR_SECONDS, 100_000, now):
                    writer.writerow([
                        _utc(row[0]), instrument["uic"], instrument["asset_type"], instrument["symbol"],
                        instrument["description"], instrument["role"], instrument["indice"],
                        instrument["direction"] or "", "" if instrument["barrier"] is None else instrument["barrier"],
                        instrument["issuer"] or "", *row[1:9], row[9], 0, instrument["id"],
                    ])
        with open(os.path.join(saxo, f"coverage-{day.isoformat()}.csv"), "w", encoding="utf-8") as out_file:
            out_file.write("ts,feed_ok\n")
            for row in fake_prices.coverage(start, end, now):
                out_file.write(f"{row['ts'].strftime('%Y-%m-%dT%H:%M:%SZ')},{'true' if row['feed_ok'] else 'false'}\n")
        for row in fake_capital.ten_second_bars(start, end, now):
            moment = datetime.fromtimestamp(row[0], timezone.utc)
            capital_days.setdefault(moment.date(), []).append((moment, *row[1:]))
    # The feed's files are days of UTC.
    for utc_day, rows in capital_days.items():
        write_capital_day(
            capital_day_path(root, fake_capital.INSTRUMENT["label"], utc_day), sorted(rows, key=lambda row: row[0]),
            epic=fake_capital.EPIC, label=fake_capital.INSTRUMENT["label"], name=fake_capital.INSTRUMENT["name"],
        )
    return days


def turbo_days(days: list[date], indice: str, first_day: date, last_day: date, now: datetime) -> list[dict]:
    """``AdminQueries.backtest_turbo_days`` of the days written."""
    rows = []
    for day in days:
        if not first_day <= day <= last_day or indice != "us100":
            continue
        start, end = day_bounds(day)
        for direction in ("long", "short"):
            turbos = [instrument for instrument in fake_prices.INSTRUMENTS if instrument["direction"] == direction]
            firsts, lasts = [], []
            for instrument in turbos:
                bars = fake_prices.bars(instrument["id"], start, end, 600, 1000, now)
                if bars:
                    firsts.append(bars[0][0])
                    lasts.append(bars[-1][0] + 590)
            if firsts:
                rows.append({
                    "day": day, "direction": direction, "turbos": len(firsts),
                    "first_ts": datetime.fromtimestamp(min(firsts), timezone.utc),
                    "last_ts": datetime.fromtimestamp(max(lasts), timezone.utc),
                })
    return rows


def recorder_days(days: list[date], first_day: date, last_day: date, now: datetime) -> list[dict]:
    """``AdminQueries.backtest_recorder_days`` of the days written."""
    rows = []
    for day in days:
        if first_day <= day <= last_day:
            start, end = day_bounds(day)
            listened = sum(1 for row in fake_prices.coverage(start, end, now) if row["feed_ok"])
            rows.append({"day": day, "listened": listened})
    return rows


def asset_days(provider: str, instrument_id: int, first_day: date, last_day: date, now: datetime) -> list[dict]:
    """``AdminQueries.backtest_asset_days``: the ten-second bars of an instrument by day."""
    rows = []
    day = first_day
    while day <= last_day:
        start, end = day_bounds(day)
        if provider == "capital":
            count = len(fake_capital.ten_second_bars(start, end, now))
        else:
            count = len(fake_prices.bars(instrument_id, start, end, BAR_SECONDS, 100_000, now))
        if count:
            rows.append({"day": day, "bars": count})
        day += timedelta(days=1)
    return rows
