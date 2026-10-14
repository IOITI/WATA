"""
The Data pages: the providers of the prices WATA keeps, and their assets
(src/admin/data.py says what each provider is).

Saxo's assets are read from the price recorder's tables through AdminQueries;
Dukascopy's from the files its downloader wrote, in the pool's threads (each
view parses its days). Both answer in the same shapes, so that one page shows
either: bars, range bars, what goes on the chart beside them, statistics.

A provider and an asset key are matched against what they can be before any
query is run or any path is built (saxo_instrument_id, history_label).
"""

from array import array
from dataclasses import fields
from datetime import date, datetime, timedelta, timezone
from itertools import batched

import pytz
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from src.admin import data
from src.admin.auth import require_session
from src.admin.config_editor import ConfigConflict, ConfigInvalid
from src.admin.data import (
    CATALOG,
    DUKASCOPY,
    HISTORY_MAX_DAYS,
    MAX_ASSETS,
    SAXO,
    SEEN_WINDOWS,
    STATISTICS_MAX_DAYS,
    AssetNotFound,
    DailyStatistics,
    InvalidAsset,
    SpreadByTime,
    aggregate,
    checked_provider,
    history_label,
    saxo_asset,
    saxo_instrument_id,
    saxo_status,
)
from src.admin.errors import DatabaseUnavailable
from src.admin.health import UNKNOWN, recorder_running, recorder_state
from src.admin.prices import (
    AVAILABILITY_MAX_DAYS,
    BAR_COLUMNS,
    GAPS_MAX_DAYS,
    INTERVALS,
    MAX_BUCKETS,
    MAX_PERIOD_DAYS,
    MAX_RUNS,
    RANGE_COLUMNS,
    RANGE_MAX_DAYS,
    RANGE_MAX_ROWS,
    SIDES,
    TooManyBars,
    auto_range_ticks,
    availability,
    price_decimals,
    range_bars,
    range_rows,
    recording_gaps,
    sample_at,
)
from src.admin.routers.config import require_config_edit_enabled
from src.admin.routers.session import restart_required
from src.price_recorder.export import day_bounds
from src.price_recorder.settings import RecorderSettings
from src.strategy.history import dukascopy

router = APIRouter(prefix="/api/data", dependencies=[Depends(require_session)])

RECORDER_SECTION = "trade.config.price_recorder"
# What the recorder's dialog edits: every setting of its section (src/price_recorder/settings.py).
RECORDER_KEYS = (
    "enabled", "record_volume", "max_instruments", "refresh_interval_seconds", "refresh_rate_ms",
    "retention_months", "alert_after_seconds", "instruments",
)
# Signals that name no indice (the scheduler's end-of-day close) concern every chart.
SIGNALS_WITHOUT_INDICE = (None, "", "n/a")
SIGNAL_LIMIT = 500
# Ten-second bars read at a time from the database, to build range bars or statistics.
SERIES_PAGE = 20_000


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _today(ctx) -> date:
    return _now().astimezone(pytz.timezone(ctx.settings.timezone)).date()


def _period(ctx, first_day: date | None, last_day: date | None):
    """The days asked for (today by default, both included) and their bounds in the trading timezone."""
    first_day = first_day or last_day or _today(ctx)
    last_day = last_day or first_day
    if last_day < first_day:
        raise HTTPException(status_code=422, detail="The period ends before it starts")
    if (last_day - first_day).days >= MAX_PERIOD_DAYS:
        raise HTTPException(status_code=422, detail=f"The period is longer than {MAX_PERIOD_DAYS} days")
    start, end = day_bounds(first_day, last_day, ctx.settings.timezone)
    return first_day, last_day, start, end


def _at_most(first_day: date, last_day: date, days: int, refusal: str) -> None:
    if (last_day - first_day).days >= days:
        raise HTTPException(status_code=422, detail=refusal)


def _history_period(provider: str, first_day: date, last_day: date) -> None:
    """Dukascopy's days are parsed again for each view: a month at a time."""
    if provider == DUKASCOPY:
        _at_most(first_day, last_day, HISTORY_MAX_DAYS, f"The Dukascopy history is read {HISTORY_MAX_DAYS} days at a time at most")


def _recorder_settings(ctx) -> RecorderSettings | None:
    """The recorder's settings as the services started with them; None when they are not valid."""
    try:
        return RecorderSettings.from_config(ctx.config_manager)
    except ValueError:
        return None


def _history(ctx):
    if ctx.history is None:
        raise HTTPException(status_code=503, detail="The Dukascopy history is not available")
    return ctx.history


def _day_of(ctx, epoch: int | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, timezone.utc).astimezone(pytz.timezone(ctx.settings.timezone)).date().isoformat()


async def _saxo_instrument(ctx, key: str) -> dict:
    instrument = await ctx.queries.price_instrument(saxo_instrument_id(key))
    if instrument is None:
        raise AssetNotFound(f"No recorded instrument {key!r}")
    return saxo_asset(instrument)


async def _history_asset(ctx, key: str) -> dict:
    asset = await run_in_threadpool(_history(ctx).asset, history_label(key))
    if asset is None:
        raise AssetNotFound(f"No day of Dukascopy history under {key!r}")
    return asset


async def _instrument(ctx, provider: str, key: str) -> dict:
    """The asset an address names, as the chart's answers describe it."""
    if checked_provider(provider) == SAXO:
        return await _saxo_instrument(ctx, key)
    return await _history_asset(ctx, key)


# ── providers ──


def _span(assets: list[dict], first: str = "first_t", last: str = "last_t") -> tuple:
    firsts = [asset[first] for asset in assets if asset.get(first) is not None]
    lasts = [asset[last] for asset in assets if asset.get(last) is not None]
    return (min(firsts) if firsts else None, max(lasts) if lasts else None)


def _catalog_entry(provider: str) -> dict:
    return next(entry for entry in CATALOG if entry["id"] == provider)


async def _recorder_section(ctx) -> dict:
    loaded = await run_in_threadpool(ctx.config_editor.load_section, RECORDER_SECTION)
    return {**loaded, "section": loaded["section"] if isinstance(loaded["section"], dict) else {}}


async def _saxo_provider(ctx) -> dict:
    section = (await _recorder_section(ctx))["section"]
    enabled = section.get("enabled") is True
    settings = _recorder_settings(ctx)
    now = _now()
    status, detail = UNKNOWN, "The database is unavailable"
    heartbeat, assets = None, None
    try:
        heartbeat = await ctx.queries.price_recorder_status()
        assets = await ctx.queries.price_assets(MAX_ASSETS + 1)
    except DatabaseUnavailable:
        pass
    else:
        if settings is None:
            status, detail = data.DOWN, "Invalid configuration of the price recorder"
        else:
            status, detail = data.recorder_provider_state(
                enabled, recorder_state(settings, heartbeat, now), recorder_running(heartbeat, now),
            )
    first_t, last_t = _span(assets or [])
    return {
        **_catalog_entry(SAXO),
        "status": status,
        "detail": detail,
        "enabled": enabled,
        # Switched on, or with something recorded: on the Providers page rather than in what can be added.
        "active": enabled or bool(assets) or heartbeat is not None,
        "assets": None if assets is None else min(len(assets), MAX_ASSETS),
        "first_t": first_t,
        "last_t": last_t,
        "first_day": _day_of(ctx, first_t),
        "last_day": _day_of(ctx, last_t),
        "heartbeat": heartbeat,
        "configurable": ctx.settings.config_edit_enabled,
    }


def _history_assets(history) -> list[dict]:
    return [asset for asset in (history.asset(label) for label in history.labels()) if asset is not None]


async def _dukascopy_provider(ctx) -> dict:
    enabled = bool(ctx.settings.backtests_enabled and ctx.backtests is not None)
    assets = await run_in_threadpool(_history_assets, ctx.history) if ctx.history is not None else []
    fetch = keep = None
    if enabled:
        fetch = ctx.backtests.fetch_status()
        # Looks for every hour of the days it keeps on the disk.
        keep = await run_in_threadpool(ctx.backtests.keep_status)
    status, detail = data.history_provider_state(enabled, fetch, keep)
    first_t, last_t = _span(assets)
    # The days of its files, as for each of its assets.
    first_day, last_day = _span(assets, "first_day", "last_day")
    return {
        **_catalog_entry(DUKASCOPY),
        "status": status,
        "detail": detail,
        "enabled": enabled,
        "active": bool(assets) or bool(keep and keep.get("enabled")),
        "assets": len(assets),
        "first_t": first_t,
        "last_t": last_t,
        "first_day": first_day,
        "last_day": last_day,
        "fetch": fetch,
        "keep": keep,
        "configurable": enabled,
    }


@router.get("/providers")
async def providers(request: Request):
    """The providers WATA has code for, what each is doing, and the one to come."""
    ctx = request.app.state.ctx
    return {
        "items": [await _saxo_provider(ctx), await _dukascopy_provider(ctx)],
        "catalog": list(CATALOG),
        "timezone": ctx.settings.timezone,
    }


def _recorder_defaults() -> dict:
    defaults = {field.name: field.default for field in fields(RecorderSettings) if field.name in RECORDER_KEYS}
    return {**defaults, "instruments": []}


def _allowed_indices(config: dict) -> list[str]:
    for rule in (config.get("trade") or {}).get("rules") or []:
        if isinstance(rule, dict) and rule.get("rule_type") == "allowed_indices":
            return sorted(((rule.get("rule_config") or {}).get("indice_ids") or {}).keys())
    return []


async def _saxo_details(ctx) -> dict:
    provider = await _saxo_provider(ctx)
    settings = _recorder_settings(ctx)
    today = _today(ctx)
    start, end = day_bounds(today, today, ctx.settings.timezone)
    gaps = None
    if settings is not None:
        try:
            coverage = await ctx.queries.price_coverage(start, end)
        except DatabaseUnavailable:
            coverage = None
        if coverage is not None:
            gaps = recording_gaps(coverage, start.astimezone(timezone.utc), end.astimezone(timezone.utc), settings.is_trading_time, _now())
    section = (await _recorder_section(ctx))["section"]
    return {
        **provider,
        "settings": {**_recorder_defaults(), **{key: value for key, value in section.items() if key in RECORDER_KEYS}},
        "gaps_today": gaps,
        "restart_required": restart_required(ctx),
    }


def _history_instruments(history) -> list[dict]:
    instruments = []
    for entry in history.instruments():
        days = history.days(entry["label"])
        instruments.append({
            **entry,
            "days": len(days),
            "first_day": days[0].isoformat() if days else None,
            "last_day": days[-1].isoformat() if days else None,
            "bytes": history.size(entry["label"]),
        })
    return instruments


async def _dukascopy_details(ctx) -> dict:
    provider = await _dukascopy_provider(ctx)
    history = _history(ctx)
    return {
        **provider,
        "instruments": await run_in_threadpool(_history_instruments, history),
        "offered": history.offered(),
        "storage": await run_in_threadpool(ctx.backtests.storage) if provider["enabled"] else None,
        "source": dukascopy.BASE_URL,
    }


@router.get("/providers/{provider}")
async def provider_details(provider: str, request: Request):
    ctx = request.app.state.ctx
    if checked_provider(provider) == SAXO:
        return await _saxo_details(ctx)
    return await _dukascopy_details(ctx)


@router.get("/providers/saxo/config")
async def recorder_config(request: Request):
    """The recorder's section as it is on disk, with what the form shows for what it leaves out."""
    ctx = request.app.state.ctx
    loaded = await _recorder_section(ctx)
    config = (await run_in_threadpool(ctx.config_editor.load))["config"]
    return {
        "section": loaded["section"],
        "defaults": _recorder_defaults(),
        "asset_types": list(data.INDEX_ASSET_TYPES),
        "indices": _allowed_indices(config),
        "revision": loaded["revision"],
        "editable": ctx.settings.config_edit_enabled,
    }


class RecorderConfigRequest(BaseModel):
    section: dict
    revision: str


def _checked_instruments(section: dict) -> None:
    """Two extra instruments under one label, or one instrument twice, would each be recorded as another index."""
    entries = section.get("instruments")
    if not isinstance(entries, list):
        return
    labels, keys = set(), set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        label, key = entry.get("label"), (entry.get("uic"), entry.get("asset_type"))
        if label in labels:
            raise HTTPException(status_code=422, detail=f"Two instruments are labelled {label!r}")
        if key in keys:
            raise HTTPException(status_code=422, detail=f"UIC {key[0]} as {key[1]} is listed twice")
        labels.add(label)
        keys.add(key)


@router.put("/providers/saxo/config", dependencies=[Depends(require_config_edit_enabled)])
async def save_recorder_config(body: RecorderConfigRequest, request: Request):
    ctx = request.app.state.ctx
    _checked_instruments(body.section)
    try:
        saved = await run_in_threadpool(ctx.config_editor.save_section, RECORDER_SECTION, body.section, body.revision)
    except ConfigInvalid as e:
        raise HTTPException(status_code=422, detail=str(e))
    except ConfigConflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    ctx.audit(
        request,
        "price recorder configuration saved",
        f"Previous version kept as {saved['backup']}. Takes effect at the recorder's next start.",
    )
    return {**saved, "restart_required": True}


@router.get("/providers/saxo/availability")
async def saxo_availability(
    request: Request,
    first_day: date | None = Query(None, alias="from"),
    last_day: date | None = Query(None, alias="to"),
):
    """
    What was recorded of each instrument of the period, and when Saxo left it
    without a quote while the recorder was receiving others.
    """
    ctx = request.app.state.ctx
    first_day, last_day, start, end = _period(ctx, first_day, last_day)
    answer = {
        "from": first_day,
        "to": last_day,
        "timezone": ctx.settings.timezone,
        "max_days": AVAILABILITY_MAX_DAYS,
        # None: the period is too long for every one of its bars to be read.
        "items": None,
    }
    if (last_day - first_day).days >= AVAILABILITY_MAX_DAYS:
        return answer

    runs = await ctx.queries.price_runs(start, end, MAX_RUNS + 1)
    if len(runs) > MAX_RUNS:
        raise HTTPException(
            status_code=422,
            detail="Too many interruptions in the bars of this period to work them out: choose a shorter one",
        )
    turbos = {
        instrument["id"]: instrument.get("indice")
        for instrument in await ctx.queries.price_instruments(start, end)
        if instrument.get("role") == "turbo"
    }
    settings = RecorderSettings.from_config(ctx.config_manager)
    answer["items"] = availability(
        runs,
        turbos,
        _recording_gaps(settings, await ctx.queries.price_coverage(start, end), start, end),
        settings.is_trading_time,
        pytz.timezone(ctx.settings.timezone),
    )
    return answer


class InstrumentRequest(BaseModel):
    symbol: str
    label: str


def _downloads(request: Request):
    """Adding a Dukascopy instrument is offering it for download: under the downloads' own switch."""
    ctx = request.app.state.ctx
    if not ctx.settings.backtests_enabled:
        raise HTTPException(status_code=403, detail="Backtests and their downloads are disabled in the configuration")
    return _history(ctx)


@router.get("/providers/dukascopy/instruments")
async def history_instruments(request: Request):
    history = _history(request.app.state.ctx)
    return {"items": await run_in_threadpool(_history_instruments, history), "offered": history.offered()}


@router.post("/providers/dukascopy/instruments", status_code=201)
async def add_history_instrument(body: InstrumentRequest, history=Depends(_downloads)):
    return await run_in_threadpool(history.add_instrument, body.symbol, body.label)


@router.delete("/providers/dukascopy/instruments/{label}", status_code=204)
async def remove_history_instrument(label: str, history=Depends(_downloads)):
    await run_in_threadpool(history.remove_instrument, history_label(label))


# ── assets ──


@router.get("/assets")
async def assets(request: Request, seen: str = Query("all")):
    """
    Every asset of every provider, the index first and then those quoted last.
    The database down leaves the files: Dukascopy's assets are listed all the
    same, with what kept Saxo's out.
    """
    ctx = request.app.state.ctx
    if seen not in SEEN_WINDOWS:
        raise HTTPException(status_code=422, detail=f"Seen must be one of {', '.join(SEEN_WINDOWS)}")
    days = SEEN_WINDOWS[seen]
    since = None
    if days is not None:
        since, _ = day_bounds(_today(ctx) - timedelta(days=days), _today(ctx), ctx.settings.timezone)

    settings = _recorder_settings(ctx)
    refresh = settings.refresh_interval_seconds if settings else RecorderSettings.refresh_interval_seconds
    now = _now()
    saxo_error, recorded = None, []
    try:
        recorded = await ctx.queries.price_assets(MAX_ASSETS + 1, since=since)
    except DatabaseUnavailable:
        saxo_error = "The database is unavailable: the assets recorded from Saxo cannot be listed"
    items = [{**saxo_asset(row), "status": saxo_status(row, now, refresh)} for row in recorded[:MAX_ASSETS]]

    if ctx.history is not None:
        downloaded = await run_in_threadpool(_history_assets, ctx.history)
        cutoff = since.timestamp() if since is not None else None
        items += [asset for asset in downloaded if cutoff is None or asset["last_t"] >= cutoff]

    return {
        "timezone": ctx.settings.timezone,
        "seen": seen,
        "items": items,
        "truncated": len(recorded) > MAX_ASSETS,
        "max": MAX_ASSETS,
        "saxo_error": saxo_error,
    }


@router.get("/assets/{provider}/{key}")
async def asset_details(
    provider: str,
    key: str,
    request: Request,
    first_day: date | None = Query(None, alias="from"),
    last_day: date | None = Query(None, alias="to"),
):
    """
    One asset: what is known of it, the first and last days it has data (where
    its pages open), how it was recorded, and for a turbo the index recorded
    with it in the period asked (its last day by default).
    """
    ctx = request.app.state.ctx
    if checked_provider(provider) == SAXO:
        found = await ctx.queries.price_assets(1, instrument_id=saxo_instrument_id(key))
        if not found:
            raise AssetNotFound(f"No recorded instrument {key!r} with a bar left")
        row = found[0]
        settings = _recorder_settings(ctx)
        asset = {**saxo_asset(row), "status": saxo_status(row, _now(), settings.refresh_interval_seconds if settings else 300)}
        first, last = _day_of(ctx, asset["first_t"]), _day_of(ctx, asset["last_t"])
        index = None
        if asset["role"] == "turbo" and asset.get("indice"):
            period_first = first_day or date.fromisoformat(last)
            period_last = last_day or period_first
            start, end = day_bounds(period_first, max(period_first, period_last), ctx.settings.timezone)
            found_index = await ctx.queries.price_index_of(asset["indice"], start, end)
            index = saxo_asset(found_index) if found_index else None
        recording = {
            "first_seen_at": row.get("first_seen_at"),
            "last_seen_at": row.get("last_seen_at"),
            "retention_months": settings.retention_months if settings else None,
            "record_volume": settings.record_volume if settings else None,
            "refresh_interval_seconds": settings.refresh_interval_seconds if settings else None,
        }
        max_days = {"chart": MAX_PERIOD_DAYS, "range": RANGE_MAX_DAYS, "statistics": STATISTICS_MAX_DAYS,
                    "availability": AVAILABILITY_MAX_DAYS, "gaps": GAPS_MAX_DAYS}
    else:
        asset = await _history_asset(ctx, key)
        history = _history(ctx)
        # Its days are those of its files, not those of its first and last bars.
        first, last = asset["first_day"], asset["last_day"]
        index = None
        days = await run_in_threadpool(history.days, asset["key"])
        recording = {
            "symbol": asset["symbol"],
            "label": asset["key"],
            "fetchable": history.symbol_of(asset["key"]) is not None,
            "days": len(days),
            "first_file": days[0].isoformat() if days else None,
            "last_file": days[-1].isoformat() if days else None,
            "bytes": await run_in_threadpool(history.size, asset["key"]),
            "location": f"{DUKASCOPY}/bars/{asset['key']}",
            "source": dukascopy.BASE_URL,
            "price_scale": dukascopy.PRICE_SCALE,
        }
        max_days = {"chart": HISTORY_MAX_DAYS, "range": min(RANGE_MAX_DAYS, HISTORY_MAX_DAYS),
                    "statistics": STATISTICS_MAX_DAYS, "availability": 0, "gaps": 0}
    return {
        "asset": asset,
        "timezone": ctx.settings.timezone,
        "first_day": first,
        "last_day": last,
        "index": index,
        "recording": recording,
        "max_days": max_days,
    }


@router.get("/assets/{provider}/{key}/bars")
async def bars(
    provider: str,
    key: str,
    request: Request,
    first_day: date | None = Query(None, alias="from"),
    last_day: date | None = Query(None, alias="to"),
    interval: int = Query(INTERVALS[0]),
):
    ctx = request.app.state.ctx
    if interval not in INTERVALS:
        raise HTTPException(
            status_code=422, detail=f"The interval must be one of {', '.join(map(str, INTERVALS))} seconds",
        )
    provider = checked_provider(provider)
    first_day, last_day, start, end = _period(ctx, first_day, last_day)
    _history_period(provider, first_day, last_day)
    instrument = await _instrument(ctx, provider, key)

    # One more than the limit: enough to know that there is more, without reading it.
    if provider == SAXO:
        rows = await ctx.queries.price_bars(instrument["id"], start, end, interval, MAX_BUCKETS + 1)
    else:
        history = _history(ctx)
        rows = await run_in_threadpool(
            lambda: aggregate(history.rows(instrument["key"], start, end), interval, MAX_BUCKETS + 1)
        )
    if len(rows) > MAX_BUCKETS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"More than {MAX_BUCKETS} bars in this period at {interval} seconds: "
                "choose a longer interval or a shorter period"
            ),
        )
    return {
        "instrument": instrument,
        "from": first_day,
        "to": last_day,
        "timezone": ctx.settings.timezone,
        "interval": interval,
        "columns": list(BAR_COLUMNS),
        "bars": rows,
    }


class _Series:
    """The ten-second bars of one side, in columns: a month of them is a few megabytes this way."""

    def __init__(self):
        self.times = array("q")
        self.opens, self.highs, self.lows, self.closes = array("d"), array("d"), array("d"), array("d")

    def add(self, rows) -> None:
        for time, opened, high, low, closed in rows:
            self.times.append(time)
            self.opens.append(opened)
            self.highs.append(high)
            self.lows.append(low)
            self.closes.append(closed)


def _too_many_rows() -> HTTPException:
    return HTTPException(status_code=422, detail="Too many recorded bars in this period to read them all: choose a shorter one")


async def _saxo_series(ctx, instrument_id: int, start: datetime, end: datetime, side: str) -> _Series:
    series = _Series()
    page_start = start
    while True:
        rows = await ctx.queries.price_series(instrument_id, page_start, end, side, SERIES_PAGE)
        series.add(rows)
        if len(series.times) > RANGE_MAX_ROWS:
            raise _too_many_rows()
        if len(rows) < SERIES_PAGE:
            return series
        page_start = datetime.fromtimestamp(series.times[-1] + 1, timezone.utc)


def _history_series(history, label: str, start: datetime, end: datetime, side: str) -> _Series:
    """One side of the ten-second bars of a Dukascopy history; the ask of a bar that has none is left out."""
    first = 1 if side == SIDES[0] else 5
    series = _Series()
    series.add(
        (row[0], *row[first:first + 4]) for row in history.rows(label, start, end) if row[first] is not None
    )
    if len(series.times) > RANGE_MAX_ROWS:
        raise _too_many_rows()
    return series


@router.get("/assets/{provider}/{key}/range-bars")
async def range_bars_of(
    provider: str,
    key: str,
    request: Request,
    first_day: date | None = Query(None, alias="from"),
    last_day: date | None = Query(None, alias="to"),
    side: str = Query(SIDES[0]),
    size: float | None = Query(None, gt=0),
    under: int | None = Query(None, ge=1),
):
    """
    The prices of one side as range bars: a bar ends when the price has left a
    range of ``size``, however long that took. Without ``size``, a range that
    suits the period is chosen. ``under`` names the recorded index shown under
    a turbo: its bid at the end of each bar comes with the bar.
    """
    ctx = request.app.state.ctx
    if side not in SIDES:
        raise HTTPException(status_code=422, detail=f"The side must be one of {', '.join(SIDES)}")
    provider = checked_provider(provider)
    first_day, last_day, start, end = _period(ctx, first_day, last_day)
    _at_most(first_day, last_day, RANGE_MAX_DAYS, f"Range bars are built for periods of {RANGE_MAX_DAYS} days at most")
    _history_period(provider, first_day, last_day)
    instrument = await _instrument(ctx, provider, key)
    if provider == SAXO:
        index_instrument = await _saxo_instrument(ctx, str(under)) if under is not None else None
        series = await _saxo_series(ctx, instrument["id"], start, end, side)
    else:
        # A Dukascopy history is an index: nothing goes under it.
        index_instrument = None
        series = await run_in_threadpool(_history_series, _history(ctx), instrument["key"], start, end, side)

    decimals = price_decimals(series.opens, series.highs, series.lows, series.closes)
    tick = round(10 ** -decimals, decimals)
    if size is None:
        ticks = auto_range_ticks(
            round(min(series.lows, default=0) * 10 ** decimals), round(max(series.highs, default=0) * 10 ** decimals),
        )
    else:
        ticks = round(size * 10 ** decimals)
        if ticks < 1:
            raise HTTPException(status_code=422, detail=f"The range is smaller than one step of these prices ({tick:.{decimals}f})")

    try:
        built = range_bars(
            series.times, series.opens, series.highs, series.lows, series.closes,
            ticks=ticks, decimals=decimals, limit=MAX_BUCKETS,
        )
    except TooManyBars:
        raise HTTPException(
            status_code=422,
            detail=f"More than {MAX_BUCKETS} range bars in this period: choose a larger range or a shorter period",
        ) from None

    index = None
    if index_instrument is not None:
        # The index has no spread to speak of: its bid is the level.
        index_series = await _saxo_series(ctx, index_instrument["id"], start, end, SIDES[0])
        # ``ended`` is the end of the bar's last ten seconds: the index of those ten seconds is the one before it.
        index = sample_at(index_series.times, index_series.closes, [bar.ended - 1 for bar in built])

    return {
        "instrument": instrument,
        "under": index_instrument,
        "from": first_day,
        "to": last_day,
        "timezone": ctx.settings.timezone,
        "side": side,
        # One step of these prices, and the range in steps (TradingView's "R") and as a price.
        "tick": tick,
        "decimals": decimals,
        "ticks": ticks,
        "size": round(ticks * tick, decimals),
        "auto": size is None,
        "columns": list(RANGE_COLUMNS),
        "bars": range_rows(built, decimals, index),
    }


def _recording_gaps(settings: RecorderSettings, coverage: list[dict], start: datetime, end: datetime) -> list[dict]:
    return recording_gaps(
        coverage,
        start.astimezone(timezone.utc),
        end.astimezone(timezone.utc),
        settings.is_trading_time,
        _now(),
    )


@router.get("/assets/{provider}/{key}/context")
async def context(
    provider: str,
    key: str,
    request: Request,
    first_day: date | None = Query(None, alias="from"),
    last_day: date | None = Query(None, alias="to"),
):
    """
    What goes on the chart beside the prices: the trades taken on the turbo
    (or on the recorded turbos of the index), the signals of its indice, the
    stretches the recorder did not record, and the volume Saxo counted.
    """
    ctx = request.app.state.ctx
    provider = checked_provider(provider)
    first_day, last_day, start, end = _period(ctx, first_day, last_day)
    instrument = await _instrument(ctx, provider, key)

    if provider == SAXO:
        trades = await ctx.queries.price_trades(instrument, start, end)
    else:
        # The turbos of the index the recorder followed; without the database, the chart is drawn without them.
        try:
            trades = await ctx.queries.price_trades(instrument, start, end)
        except DatabaseUnavailable:
            trades = []
    try:
        journal = await ctx.queries.signal_journal(start=first_day, end=last_day, limit=SIGNAL_LIMIT)
    except DatabaseUnavailable:
        if provider == SAXO:
            raise
        journal = []
    signals = [
        entry for entry in journal
        if entry.get("indice") == instrument.get("indice") or entry.get("indice") in SIGNALS_WITHOUT_INDICE
    ]
    # Oldest first, like the bars they are drawn on.
    signals.sort(key=lambda entry: str(entry.get("decided_at") or ""))

    gaps, volume = None, None
    if provider == SAXO:
        if (last_day - first_day).days < GAPS_MAX_DAYS:
            settings = RecorderSettings.from_config(ctx.config_manager)
            gaps = _recording_gaps(settings, await ctx.queries.price_coverage(start, end), start, end)
        days = await ctx.queries.price_volume_days(instrument["id"], start, end)
        # None: Saxo's volume was not recorded for it in the period.
        volume = sum(days.values()) if days else None
    return {
        "instrument": instrument,
        "trades": trades,
        "signals": signals,
        # None: the period is too long for the gaps to be worked out, or there is no recorder behind it.
        "gaps": gaps,
        "volume": volume,
    }


@router.get("/assets/{provider}/{key}/statistics")
async def statistics(
    provider: str,
    key: str,
    request: Request,
    first_day: date | None = Query(None, alias="from"),
    last_day: date | None = Query(None, alias="to"),
):
    """
    Each day of the period summed up from its ten-second bars, and the spread
    through the day: read a page at a time, never held whole.
    """
    ctx = request.app.state.ctx
    provider = checked_provider(provider)
    first_day, last_day, start, end = _period(ctx, first_day, last_day)
    _at_most(
        first_day, last_day, STATISTICS_MAX_DAYS,
        f"Statistics are worked out for periods of {STATISTICS_MAX_DAYS} days at most",
    )
    instrument = await _instrument(ctx, provider, key)
    settings = _recorder_settings(ctx) or RecorderSettings(timezone=ctx.settings.timezone)
    # The recorder's index is quoted with one price: it has no spread to speak of.
    spread = not (provider == SAXO and instrument.get("role") == "underlying")
    daily = DailyStatistics(ctx.settings.timezone, settings.is_trading_time, spread=spread)
    by_time = SpreadByTime(ctx.settings.timezone)

    def feed(rows) -> None:
        daily.add(rows)
        if spread:
            by_time.add(rows)

    volumes = {}
    if provider == SAXO:
        page_start, read = start, 0
        while True:
            rows = await ctx.queries.price_bars(instrument["id"], page_start, end, INTERVALS[0], SERIES_PAGE)
            read += len(rows)
            if read > RANGE_MAX_ROWS:
                raise _too_many_rows()
            await run_in_threadpool(feed, rows)
            if len(rows) < SERIES_PAGE:
                break
            page_start = datetime.fromtimestamp(rows[-1][0] + 1, timezone.utc)
        volumes = await ctx.queries.price_volume_days(instrument["id"], start, end)
    else:
        history = _history(ctx)

        def read_files() -> None:
            # A page at a time, as from the database: each page goes to both.
            for page in batched(history.rows(instrument["key"], start, end), SERIES_PAGE):
                feed(page)

        await run_in_threadpool(read_files)

    days = [{**day, "volume": volumes.get(day["day"])} for day in await run_in_threadpool(daily.result)]
    return {
        "instrument": instrument,
        "from": first_day,
        "to": last_day,
        "timezone": ctx.settings.timezone,
        "spread": spread,
        "days": days,
        "spread_by_time": by_time.result() if spread else [],
    }
