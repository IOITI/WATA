"""
What a backtest needs read before it is asked for, and what the Backtests form
shows beside it: one implementation for the admin's routes
(src/admin/routers/backtests.py) and Claude's tools (src/admin/mcp_server/).

The service never reads the database: the signals WATA received (for a run
that replays them), its real positions of those days (to set the run beside
them) and the account's last value (the run's money starts from it) are read
here and handed over.
"""

from datetime import date, datetime, timedelta, timezone

import pytz
from starlette.concurrency import run_in_threadpool

from src.admin.data import AssetNotFound, capital_epic, history_label, saxo_instrument_id
from src.admin.errors import DatabaseUnavailable, InvalidRequest

# The orders measured by default: the three months before today.
EXECUTION_DAYS = 92


async def last_balance(queries) -> float | None:
    """The account's value at the last daily report, None when the database cannot say it."""
    try:
        return await queries.last_balance()
    except DatabaseUnavailable:
        return None


async def ask_backtest(queries, service, body: dict, *, made_by: str | None = None, made_via: str | None = None,
                       experiment: str | None = None, lockbox: dict | None = None) -> dict:
    """
    Ask for a run, or a sweep. Of the signals WATA received, the journal of the
    period (and a day either side) is read first, with WATA's closed positions
    of the same days.
    """
    journal = positions = None
    source = body.get("source") if isinstance(body, dict) else None
    if isinstance(source, dict) and source.get("kind") == "journal":
        try:
            first, last = date.fromisoformat(body.get("from")), date.fromisoformat(body.get("to"))
        except (TypeError, ValueError):
            raise InvalidRequest("The period is two days, YYYY-MM-DD") from None
        journal = await queries.journal_between(first - timedelta(days=1), last + timedelta(days=1))
        # What WATA really did with those signals, for the run to be set beside it.
        try:
            positions = await queries.closed_positions(first - timedelta(days=1), last + timedelta(days=1))
        except DatabaseUnavailable:
            positions = None
    capital = await last_balance(queries)
    # On the event loop: the run joins the service's line, which is not to be touched from another thread.
    return service.create(
        body, journal=journal, positions=positions, capital=capital,
        made_by=made_by, made_via=made_via, experiment=experiment, lockbox=lockbox,
    )


def _local_day(moment, zone) -> str | None:
    if moment is None:
        return None
    if isinstance(moment, str):
        moment = datetime.fromisoformat(moment.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(zone).date().isoformat()


async def coverage(queries, history, timezone_name: str, max_period_days: int, first_day: date, last_day: date,
                   indice: str = "us100", asset: str | None = None, source: str = "pine") -> dict:
    """
    What was recorded on each day of a period: the turbos of the indice by
    direction and the hours of their bars, how long the recorder listened,
    the bars of the asset a strategy would run on, the signals WATA received.
    """
    if last_day < first_day or (last_day - first_day).days >= max_period_days:
        raise InvalidRequest(f"A period of {max_period_days} days at most")
    zone = pytz.timezone(timezone_name)
    days = {
        (first_day + timedelta(days=offset)).isoformat(): {
            "day": (first_day + timedelta(days=offset)).isoformat(),
            "turbos": {"long": 0, "short": 0},
            "first": None, "last": None, "recorded_minutes": 0, "asset_bars": None, "signals": None,
        }
        for offset in range((last_day - first_day).days + 1)
    }
    database = True
    try:
        for row in await queries.backtest_turbo_days(indice, first_day, last_day):
            entry = days.get(str(row["day"]))
            if entry is None or row["direction"] not in ("long", "short"):
                continue
            entry["turbos"][row["direction"]] = row["turbos"]
            for name, key, pick in (("first", "first_ts", min), ("last", "last_ts", max)):
                value = row[key].astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if row[key] else None
                entry[name] = value if entry[name] is None else pick(entry[name], value) if value else entry[name]
        for row in await queries.backtest_recorder_days(first_day, last_day):
            entry = days.get(str(row["day"]))
            if entry is not None:
                entry["recorded_minutes"] = int(row["listened"] or 0) * 10 // 60
    except DatabaseUnavailable:
        database = False

    if asset:
        provider, _, key = asset.partition("/")
        try:
            if provider == "dukascopy":
                label = history_label(key)
                fetched = set(await run_in_threadpool(history.days, label)) if history is not None else set()
                for entry in days.values():
                    entry["asset_bars"] = 1 if date.fromisoformat(entry["day"]) in fetched else 0
            elif provider in ("saxo", "capital") and database:
                if provider == "saxo":
                    instrument_id = saxo_instrument_id(key)
                else:
                    found = await queries.capital_instrument(capital_epic(key))
                    instrument_id = found["id"] if found else None
                rows = await queries.backtest_asset_days(provider, instrument_id, first_day, last_day) if instrument_id else []
                counted = {str(row["day"]): row["bars"] for row in rows}
                for entry in days.values():
                    entry["asset_bars"] = counted.get(entry["day"], 0)
            elif provider not in ("saxo", "capital"):
                raise InvalidRequest("The asset is capital/<epic>, dukascopy/<label> or saxo/<id>")
        except AssetNotFound as e:
            raise InvalidRequest(str(e)) from None
        except DatabaseUnavailable:
            database = False

    if source == "journal" and database:
        try:
            rows = await queries.journal_between(first_day, last_day)
        except DatabaseUnavailable:
            rows, database = [], False
        for entry in days.values():
            entry["signals"] = 0
        for row in rows:
            if row.get("action") not in ("long", "short") or row.get("indice") != indice:
                continue
            day = _local_day(row.get("received_timestamp") or row.get("decided_at"), zone)
            if day in days:
                days[day]["signals"] += 1
    return {"days": list(days.values()), "database": database, "timezone": timezone_name}


async def execution(queries, timezone_name: str, first_day: date | None = None, last_day: date | None = None) -> dict:
    """
    What WATA's own orders took and cost (src/backtest/execution_calibration.py):
    the delays from a signal to the trader and from an order to Saxo, the price
    paid against the recorded quote, the refusals; and the values a run would take from them.
    """
    from src.backtest.clock import TradingClock
    from src.backtest.execution_calibration import measure

    clock = TradingClock(timezone_name)
    last_day = last_day or datetime.now(timezone.utc).astimezone(clock.zone).date()
    first_day = first_day or last_day - timedelta(days=EXECUTION_DAYS - 1)
    if last_day < first_day or (last_day - first_day).days >= 366:
        raise InvalidRequest("A period of a year at most")
    start, end = clock.day_bounds(first_day)[0], clock.day_bounds(last_day)[1]
    rows = await queries.execution_rows(datetime.fromtimestamp(start, timezone.utc), datetime.fromtimestamp(end, timezone.utc))
    return {"from": first_day.isoformat(), "to": last_day.isoformat(), **measure(rows, clock)}
