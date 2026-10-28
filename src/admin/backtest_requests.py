"""
What a backtest needs read before it is asked for, and what the Backtests form
shows beside it: one implementation for the admin's routes
(src/admin/routers/backtests.py) and Claude's tools (src/admin/mcp_server/).

The service never reads the database: the signals WATA received (for a run
that replays them), its real positions of those days (to set the run beside
them), the signals a rule raised (for a run of them), and the account's last
value (the run's money starts from it) are read here and handed over.
"""

from datetime import date, datetime, timedelta, timezone

import pytz
from starlette.concurrency import run_in_threadpool

from src.admin.data import AssetNotFound, capital_epic, history_label, saxo_instrument_id
from src.admin.errors import DatabaseUnavailable, InvalidRequest, NotPossible
from src.admin.ta_scripts import ID_PATTERN

# The orders measured by default: the three months before today.
EXECUTION_DAYS = 92


async def last_balance(queries) -> float | None:
    """The account's value at the last daily report, None when the database cannot say it."""
    try:
        return await queries.last_balance()
    except DatabaseUnavailable:
        return None


def _period(body: dict) -> tuple[date, date]:
    try:
        return date.fromisoformat(body.get("from")), date.fromisoformat(body.get("to"))
    except (TypeError, ValueError):
        raise InvalidRequest("The period is two days, YYYY-MM-DD") from None


async def raised_signals(queries, rule_id: str, first: date, last: date) -> list[dict]:
    """
    The signals a rule raised in a period, as a run of them replays them: refused when there are more than a run
    takes, which a shorter period may not have.
    """
    from src.admin.backtests import MAX_RAISED_SIGNALS

    rows = await queries.rule_signals(rule_id, first, last, limit=MAX_RAISED_SIGNALS)
    if len(rows) >= MAX_RAISED_SIGNALS:
        raise NotPossible(f"The rule raised more than {MAX_RAISED_SIGNALS:,} signals in that period: ask for a shorter one")
    return rows


async def ask_backtest(queries, service, body: dict, *, made_by: str | None = None, made_via: str | None = None,
                       experiment: str | None = None, lockbox: dict | None = None) -> dict:
    """
    Ask for a run, or a sweep. Of the signals WATA received, the journal of the
    period (and a day either side) is read first, with WATA's closed positions
    of the same days; of the signals a rule raised, the rule's of the period.
    """
    journal = positions = None
    source = body.get("source") if isinstance(body, dict) else None
    if isinstance(source, dict) and source.get("kind") == "ta_signals":
        rule = source.get("rule") if isinstance(source.get("rule"), dict) else {}
        if not isinstance(rule.get("id"), str) or not ID_PATTERN.match(rule["id"]):
            raise InvalidRequest("The signals a rule raised are named by the rule")
        first, last = _period(body)
        journal = await raised_signals(queries, rule["id"], first, last)
    if isinstance(source, dict) and source.get("kind") == "journal":
        first, last = _period(body)
        journal = await queries.journal_between(first - timedelta(days=1), last + timedelta(days=1))
        # What WATA really did with those signals, for the run to be set beside it.
        try:
            positions = await queries.closed_positions(first - timedelta(days=1), last + timedelta(days=1))
        except DatabaseUnavailable:
            positions = None
    execution = await measured_orders(queries, service) if asks_measured(body) else None
    # On the event loop: the run joins the service's line, which is not to be touched from another thread.
    # It starts with the backtests' own account (DEFAULT_CAPITAL) unless its changes say another.
    return service.create(
        body, journal=journal, positions=positions,
        made_by=made_by, made_via=made_via, experiment=experiment, lockbox=lockbox, execution=execution,
    )


def asks_measured(body) -> bool:
    """Whether a request may fill orders by drawing from WATA's own (its changes, or a sweep's axis)."""
    if not isinstance(body, dict):
        return False
    if (body.get("changes") or {}).get("execution") == "measured":
        return True
    axes = (body.get("sweep") or {}).get("axes") or [] if isinstance(body.get("sweep"), dict) else []
    return any(isinstance(axis, dict) and axis.get("name") == "execution" and "measured" in (axis.get("values") or [])
               for axis in axes)


async def measured_orders(queries, service) -> dict:
    """WATA's orders of the last EXECUTION_DAYS, one measure at a time: what a run on measured fills draws from."""
    from src.backtest.clock import TradingClock
    from src.backtest.execution_calibration import samples

    clock = TradingClock(service.live_settings(None).timezone)
    last_day = datetime.now(timezone.utc).astimezone(clock.zone).date()
    first_day = last_day - timedelta(days=EXECUTION_DAYS - 1)
    start, end = clock.day_bounds(first_day)[0], clock.day_bounds(last_day)[1]
    try:
        rows = await queries.execution_rows(datetime.fromtimestamp(start, timezone.utc), datetime.fromtimestamp(end, timezone.utc))
    except DatabaseUnavailable:
        rows = {}
    return {"from": first_day.isoformat(), "to": last_day.isoformat(), "samples": samples(rows)}


def local_day(moment, zone) -> str | None:
    """The day a moment (a datetime, or ISO text: UTC when it names no zone) falls on in ``zone``: YYYY-MM-DD."""
    if moment is None:
        return None
    if isinstance(moment, str):
        moment = datetime.fromisoformat(moment.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(zone).date().isoformat()


async def coverage(queries, history, timezone_name: str, max_period_days: int, first_day: date, last_day: date,
                   indice: str = "us100", asset: str | None = None, source: str = "pine", rule: str | None = None) -> dict:
    """
    What was recorded on each day of a period: the turbos of the indice by
    direction and the hours of their bars, how long the recorder listened,
    the bars of the asset a strategy would run on, the signals WATA received
    or those a rule raised (its entries).
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
            day = local_day(row.get("received_timestamp") or row.get("decided_at"), zone)
            if day in days:
                days[day]["signals"] += 1
    if source == "ta_signals" and rule is not None and database:
        if not ID_PATTERN.match(rule):
            raise InvalidRequest("The signals a rule raised are named by the rule")
        from src.admin.backtests import MAX_RAISED_SIGNALS

        try:
            rows = await queries.rule_signals(rule, first_day, last_day, limit=MAX_RAISED_SIGNALS)
        except DatabaseUnavailable:
            rows, database = [], False
        for entry in days.values():
            entry["signals"] = 0
        for row in rows:
            day = local_day(row.get("raised_at"), zone)
            if row.get("action") in ("long", "short") and day in days:
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
