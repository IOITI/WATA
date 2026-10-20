"""
An asset of the Data pages and its bars, as the pages read them and Claude's
tools too (src/admin/routers/data.py, src/admin/mcp_server/): Saxo's and
Capital.com's from their tables through AdminQueries, Dukascopy's from its
files in the pool's threads. A provider and a key are matched against what
they can be before any query is run or any path is built.
"""

from datetime import date

from starlette.concurrency import run_in_threadpool

from src.admin.data import (
    CAPITAL,
    DUKASCOPY,
    HISTORY_MAX_DAYS,
    SAXO,
    AssetNotFound,
    aggregate,
    capital_asset,
    capital_epic,
    checked_provider,
    history_label,
    saxo_asset,
    saxo_instrument_id,
)
from src.admin.errors import InvalidRequest, NotPossible
from src.admin.prices import BAR_COLUMNS, INTERVALS, MAX_BUCKETS, MAX_INTERVAL, MAX_PERIOD_DAYS, interval_served
from src.price_recorder.export import day_bounds


def history_or_refuse(history):
    if history is None:
        raise NotPossible("The Dukascopy history is not available")
    return history


async def saxo_instrument(queries, key: str) -> dict:
    instrument = await queries.price_instrument(saxo_instrument_id(key))
    if instrument is None:
        raise AssetNotFound(f"No recorded instrument {key!r}")
    return saxo_asset(instrument)


async def history_asset(history, key: str) -> dict:
    asset = await run_in_threadpool(history_or_refuse(history).asset, history_label(key))
    if asset is None:
        raise AssetNotFound(f"No day of Dukascopy history under {key!r}")
    return asset


async def capital_instrument(queries, key: str) -> dict:
    row = await queries.capital_instrument(capital_epic(key))
    if row is None:
        raise AssetNotFound(f"No Capital.com instrument {key!r} with a bar")
    return capital_asset(row)


async def instrument(queries, history, provider: str, key: str) -> dict:
    """The asset an address names, as the chart's answers describe it."""
    provider = checked_provider(provider)
    if provider == SAXO:
        return await saxo_instrument(queries, key)
    if provider == CAPITAL:
        return await capital_instrument(queries, key)
    return await history_asset(history, key)


def period(timezone_name: str, first_day: date | None, last_day: date | None, today: date):
    """The days asked for (today by default, both included) and their bounds in the trading timezone."""
    first_day = first_day or last_day or today
    last_day = last_day or first_day
    if last_day < first_day:
        raise InvalidRequest("The period ends before it starts")
    if (last_day - first_day).days >= MAX_PERIOD_DAYS:
        raise InvalidRequest(f"The period is longer than {MAX_PERIOD_DAYS} days")
    start, end = day_bounds(first_day, last_day, timezone_name)
    return first_day, last_day, start, end


def check_history_period(provider: str, first_day: date, last_day: date) -> None:
    """Dukascopy's days are parsed again for each view: a month at a time."""
    if provider == DUKASCOPY and (last_day - first_day).days >= HISTORY_MAX_DAYS:
        raise InvalidRequest(f"The Dukascopy history is read {HISTORY_MAX_DAYS} days at a time at most")


async def bars(queries, history, timezone_name: str, provider: str, key: str, first_day: date | None,
               last_day: date | None, interval: int, today: date, limit: int = MAX_BUCKETS) -> dict:
    """Bars of ``interval`` seconds of an asset over whole days; refused beyond ``limit`` bars."""
    if not interval_served(interval):
        raise InvalidRequest(f"The interval is a whole number of seconds from {INTERVALS[0]} to {MAX_INTERVAL}")
    provider = checked_provider(provider)
    first_day, last_day, start, end = period(timezone_name, first_day, last_day, today)
    check_history_period(provider, first_day, last_day)
    found = await instrument(queries, history, provider, key)

    # One more than the limit: enough to know that there is more, without reading it.
    through = None
    if provider == SAXO:
        rows = await queries.price_bars(found["id"], start, end, interval, limit + 1)
    elif provider == CAPITAL:
        rows, through = await queries.capital_bars(found["id"], start, end, interval, limit + 1)
    else:
        source = history_or_refuse(history)
        rows = await run_in_threadpool(lambda: aggregate(source.rows(found["key"], start, end), interval, limit + 1))
    if len(rows) > limit:
        raise InvalidRequest(
            f"More than {limit} bars in this period at {interval} seconds: "
            "choose a longer interval or a shorter period"
        )
    return {
        "instrument": found,
        "from": first_day,
        "to": last_day,
        "timezone": timezone_name,
        "interval": interval,
        "columns": list(BAR_COLUMNS),
        "bars": rows,
        # The start of the last ten-second bar read (Capital.com): a live chart adds only what follows it.
        "through": through,
    }
