"""
Export recorded bars to a compressed CSV file, for a backtest outside WATA.

    python -m src.price_recorder.export --from 2026-10-12 --to 2026-10-16 --indice us100

Days are those of the trading timezone, both ends included. One line per
instrument and ten-second bucket, with the instrument's description, direction
and knock-out level beside its prices. ``ts`` is the start of the bucket, in UTC.
"""

import argparse
import asyncio
import csv
import gzip
import os
import sys
from datetime import date, datetime, timedelta, timezone

DEFAULT_DIRECTORY = os.path.join("var", "lib", "price_export")
PAGE_SIZE = 20000

COLUMNS = (
    "ts", "uic", "asset_type", "symbol", "description", "role", "indice", "direction", "barrier", "issuer",
    "bid_open", "bid_high", "bid_low", "bid_close", "ask_open", "ask_high", "ask_low", "ask_close",
    "updates", "delayed_minutes",
)


def _cell(name: str, value):
    if value is None:
        return ""
    if name == "ts":
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return value


async def export_bars(history, start: datetime, end: datetime, out_path: str, indice: str | None = None) -> int:
    """Write the bars of [start, end) to ``out_path`` (gzip CSV); returns how many."""
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    count = 0
    after = None
    with gzip.open(out_path, "wt", newline="", encoding="utf-8") as out_file:
        writer = csv.writer(out_file)
        writer.writerow(COLUMNS)
        while True:
            page = await history.bars_page(start, end, indice=indice, after=after, limit=PAGE_SIZE)
            for row in page:
                writer.writerow([_cell(name, row.get(name)) for name in COLUMNS])
            count += len(page)
            if len(page) < PAGE_SIZE:
                return count
            after = (page[-1]["ts"], page[-1]["instrument_id"])


def day_bounds(first_day: date, last_day: date, timezone_name: str) -> tuple[datetime, datetime]:
    """From the start of ``first_day`` to the end of ``last_day``, in the trading timezone."""
    import pytz

    zone = pytz.timezone(timezone_name)
    start = zone.localize(datetime.combine(first_day, datetime.min.time()))
    end = zone.localize(datetime.combine(last_day + timedelta(days=1), datetime.min.time()))
    return start, end


def parse_arguments(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="wataprices", description="Export recorded ten-second bars to a csv.gz file.")
    parser.add_argument("--from", dest="first_day", required=True, type=date.fromisoformat, help="first day, YYYY-MM-DD")
    parser.add_argument("--to", dest="last_day", type=date.fromisoformat, help="last day included (default: the first day)")
    parser.add_argument("--indice", help="only the instruments of this indice, e.g. us100")
    parser.add_argument("--out", help=f"file to write (default: {DEFAULT_DIRECTORY}/prices_<from>_<to>.csv.gz)")
    arguments = parser.parse_args(argv)
    arguments.last_day = arguments.last_day or arguments.first_day
    if arguments.last_day < arguments.first_day:
        parser.error("--to is before --from")
    return arguments


async def run(arguments: argparse.Namespace) -> int:
    from src.configuration import ConfigurationManager
    from src.database.postgres import AsyncDbPriceHistoryManager, PostgresConnectionManager

    config_path = os.getenv("WATA_CONFIG_PATH")
    if not config_path:
        print("WATA_CONFIG_PATH not set", file=sys.stderr)
        return 10
    config_manager = ConfigurationManager(config_path)
    timezone_name = config_manager.get_config_value("trade.config.general.timezone", "Europe/Paris")
    start, end = day_bounds(arguments.first_day, arguments.last_day, timezone_name)
    out_path = arguments.out or os.path.join(
        DEFAULT_DIRECTORY, f"prices_{arguments.first_day.isoformat()}_{arguments.last_day.isoformat()}.csv.gz",
    )

    dsn = config_manager.get_config_value("postgresql", {}).get("dsn", "postgresql://wata:wata@localhost:5432/wata")
    pg = PostgresConnectionManager(dsn=dsn, min_size=1, max_size=1)
    await pg.connect()
    try:
        count = await export_bars(AsyncDbPriceHistoryManager(pg), start, end, out_path, arguments.indice)
    finally:
        await pg.close()
    print(f"{count} bars from {arguments.first_day} to {arguments.last_day} ({timezone_name}) written to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run(parse_arguments())))
