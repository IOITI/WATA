"""
The Data pages: the providers of the prices WATA keeps, and their assets
(src/admin/data.py says what each provider is).

Saxo's assets are read from the price recorder's tables through AdminQueries,
Capital.com's from the Capital.com feed's tables the same way, Dukascopy's
from the files its downloader wrote, in the pool's threads (each view parses
its days). All answer in the same shapes, so that one page shows any of them:
bars, range bars, what goes on the chart beside them, statistics.

A provider and an asset key are matched against what they can be before any
query is run or any path is built (saxo_instrument_id, capital_epic,
history_label).
"""

import os
import asyncio
import json
import time
from array import array
from dataclasses import fields
from datetime import date, datetime, timedelta, timezone
from itertools import batched

import pytz
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from src.admin import asset_bars, data
from src.admin.auth import require_session, session_cookie_name
from src.admin.config_editor import ConfigConflict, ConfigInvalid
from src.admin.downloads import KEEP_DEFAULT_DAYS
from src.admin.data import (
    CAPITAL,
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
    capital_asset,
    capital_epic,
    capital_status,
    checked_provider,
    history_label,
    ref_of,
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
    MAX_INTERVAL,
    MAX_PERIOD_DAYS,
    MAX_RUNS,
    RANGE_COLUMNS,
    RANGE_MAX_DAYS,
    RANGE_MAX_ROWS,
    SIDES,
    TooManyBars,
    auto_range_ticks,
    availability,
    interval_served,
    price_decimals,
    range_bars,
    range_rows,
    range_volumes,
    recording_gaps,
    sample_at,
)
from src.admin.routers.config import require_config_edit_enabled
from src.admin.routers.session import restart_required
from src.capital_feed import day_files as capital_day_files
from src.capital_feed.hours import parse_opening_hours
from src.capital_feed.settings import CREDENTIAL_VARIABLES, REST_URLS, CapitalSettings
from src.price_recorder.export import day_bounds
from src.price_recorder.settings import RecorderSettings
from src.price_history import dukascopy

router = APIRouter(prefix="/api/data", dependencies=[Depends(require_session)])

RECORDER_SECTION = "trade.config.price_recorder"
# What the recorder's dialog edits: every setting of its section (src/price_recorder/settings.py).
RECORDER_KEYS = (
    "enabled", "record_volume", "max_instruments", "refresh_interval_seconds", "refresh_rate_ms",
    "retention_months", "alert_after_seconds", "instruments",
)
CAPITAL_SECTION = "trade.config.capital_feed"
# What the Capital.com feed's dialog edits: every setting of its section (src/capital_feed/settings.py).
CAPITAL_KEYS = (
    "enabled", "environment", "instruments", "minute_volume", "day_files", "retention_months", "alert_after_seconds",
)
# Signals that name no indice (the scheduler's end-of-day close) concern every chart.
SIGNALS_WITHOUT_INDICE = (None, "", "n/a")
SIGNAL_LIMIT = 500
# The live stream of an asset: how long it waits for a message before looking whether the browser left, and how
# often it writes something when the prices are quiet (a closed market), so that no proxy takes it for dead.
LIVE_POLL_SECONDS = 1.0
LIVE_KEEP_ALIVE_SECONDS = 15.0
# Ten-second bars read at a time from the database, to build range bars or statistics.
SERIES_PAGE = 20_000
# What the history panel shows when it is not told a period.
DEFAULT_COVERAGE_DAYS = 120


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _today(ctx) -> date:
    return _now().astimezone(pytz.timezone(ctx.settings.timezone)).date()


def _period(ctx, first_day: date | None, last_day: date | None):
    """The days asked for (today by default, both included) and their bounds in the trading timezone."""
    return asset_bars.period(ctx.settings.timezone, first_day, last_day, _today(ctx))


def _at_most(first_day: date, last_day: date, days: int, refusal: str) -> None:
    if (last_day - first_day).days >= days:
        raise HTTPException(status_code=422, detail=refusal)


def _history_period(provider: str, first_day: date, last_day: date) -> None:
    """Dukascopy's days are parsed again for each view: a month at a time."""
    asset_bars.check_history_period(provider, first_day, last_day)


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


async def _history_asset(ctx, key: str) -> dict:
    return await asset_bars.history_asset(_history(ctx), key)


async def _instrument(ctx, provider: str, key: str) -> dict:
    """The asset an address names, as the chart's answers describe it."""
    if checked_provider(provider) == DUKASCOPY:
        _history(ctx)
    return await asset_bars.instrument(ctx.queries, ctx.history, provider, key)


def _capital_settings(ctx) -> CapitalSettings | None:
    """The feed's settings as the services started with them; None when they are not valid."""
    try:
        return CapitalSettings.from_config(ctx.config_manager)
    except ValueError:
        return None


def _always(_moment: datetime) -> bool:
    return True


async def _capital_trading_time(ctx, epics):
    """
    When the markets of ``epics`` are open, by the opening hours Capital.com
    gave the feed; any time when none are known.
    """
    hours = [parse_opening_hours(await ctx.queries.capital_opening_hours(epic)) for epic in epics]
    known = [entry for entry in hours if entry is not None]
    if not known:
        return _always
    return lambda moment: any(entry.is_open(moment) for entry in known)


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


def _downloads_enabled(ctx) -> bool:
    return bool(ctx.settings.backtests_enabled and ctx.downloads is not None)


def _download_service(request: Request):
    """The downloads of Dukascopy's history (src/admin/downloads.py), when they are switched on (with the backtests)."""
    ctx = request.app.state.ctx
    if not ctx.settings.backtests_enabled:
        raise HTTPException(status_code=403, detail="Downloads are disabled in the configuration (admin.backtests_enabled)")
    if ctx.downloads is None:
        raise HTTPException(status_code=503, detail="Downloads are not available")
    return ctx.downloads


async def _dukascopy_provider(ctx) -> dict:
    enabled = _downloads_enabled(ctx)
    assets = await run_in_threadpool(_history_assets, ctx.history) if ctx.history is not None else []
    fetch = keep = None
    if enabled:
        fetch = ctx.downloads.fetch_status()
        # Looks for every hour of the days it keeps on the disk.
        keep = await run_in_threadpool(ctx.downloads.keep_status)
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


async def _capital_section(ctx) -> dict:
    loaded = await run_in_threadpool(ctx.config_editor.load_section, CAPITAL_SECTION)
    return {**loaded, "section": loaded["section"] if isinstance(loaded["section"], dict) else {}}


async def _capital_provider(ctx) -> dict:
    section = (await _capital_section(ctx))["section"]
    enabled = section.get("enabled") is True
    status, detail = UNKNOWN, "The database is unavailable"
    state, assets = None, None
    try:
        state = await ctx.queries.capital_feed_state()
        assets = await ctx.queries.capital_assets()
    except DatabaseUnavailable:
        pass
    else:
        status, detail = data.capital_provider_state(enabled, state, _now())
    first_t, last_t = _span(assets or [])
    return {
        **_catalog_entry(CAPITAL),
        "status": status,
        "detail": detail,
        "enabled": enabled,
        # Switched on, or with something recorded: on the Providers page rather than in what can be added.
        "active": enabled or bool(assets) or state is not None,
        "assets": None if assets is None else len(assets),
        "first_t": first_t,
        "last_t": last_t,
        "first_day": _day_of(ctx, first_t),
        "last_day": _day_of(ctx, last_t),
        # What the feed last wrote of itself: no secret is ever in it (src/capital_feed/recorder.py).
        "feed": state,
        "configurable": ctx.settings.config_edit_enabled,
    }


@router.get("/providers")
async def providers(request: Request):
    """The providers WATA has code for, and what each is doing."""
    ctx = request.app.state.ctx
    return {
        "items": [await _saxo_provider(ctx), await _dukascopy_provider(ctx), await _capital_provider(ctx)],
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
        "storage": await run_in_threadpool(ctx.downloads.storage) if provider["enabled"] else None,
        "source": dukascopy.BASE_URL,
    }


def _capital_defaults() -> dict:
    defaults = {field.name: field.default for field in fields(CapitalSettings) if field.name in CAPITAL_KEYS}
    return {**defaults, "instruments": [{"epic": i.epic, "label": i.label} for i in CapitalSettings.instruments]}


def _capital_day_files(prices_root: str, labels) -> list[dict]:
    """The day files written for backtests, by label: how many, the first and last day, their size."""
    found = []
    for label in labels:
        days = sorted(capital_day_files.written_days(prices_root, label))
        size = 0
        for day in days:
            try:
                size += os.path.getsize(capital_day_files.day_path(prices_root, label, day))
            except OSError:
                continue
        found.append({
            "label": label, "days": len(days),
            "first_day": days[0].isoformat() if days else None, "last_day": days[-1].isoformat() if days else None,
            "bytes": size, "location": f"{capital_day_files.SOURCE}/bars/{label}",
        })
    return found


async def _capital_details(ctx) -> dict:
    provider = await _capital_provider(ctx)
    section = (await _capital_section(ctx))["section"]
    settings = {**_capital_defaults(), **{key: value for key, value in section.items() if key in CAPITAL_KEYS}}
    labels = [entry.get("label") for entry in settings["instruments"] if isinstance(entry, dict) and isinstance(entry.get("label"), str)]
    gaps = None
    started = _capital_settings(ctx)
    if started is not None:
        today = _today(ctx)
        start, end = day_bounds(today, today, ctx.settings.timezone)
        try:
            trading_time = await _capital_trading_time(ctx, started.epics)
            coverage = await ctx.queries.capital_coverage(start, end)
        except DatabaseUnavailable:
            coverage = None
        if coverage is not None:
            gaps = recording_gaps(coverage, start.astimezone(timezone.utc), end.astimezone(timezone.utc), trading_time, _now())
    return {
        **provider,
        "settings": settings,
        "gaps_today": gaps,
        "day_files": await run_in_threadpool(_capital_day_files, ctx.settings.price_history_path, labels),
        # Where the login is given: its names, never anything of its values.
        "credential_variables": list(CREDENTIAL_VARIABLES),
        "restart_required": restart_required(ctx),
    }


@router.get("/providers/{provider}")
async def provider_details(provider: str, request: Request):
    ctx = request.app.state.ctx
    provider = checked_provider(provider)
    if provider == SAXO:
        return await _saxo_details(ctx)
    if provider == CAPITAL:
        return await _capital_details(ctx)
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


@router.get("/providers/capital/config")
async def capital_config(request: Request):
    """The feed's section as it is on disk, with what the form shows for what it leaves out."""
    ctx = request.app.state.ctx
    loaded = await _capital_section(ctx)
    config = (await run_in_threadpool(ctx.config_editor.load))["config"]
    return {
        "section": loaded["section"],
        "defaults": _capital_defaults(),
        "environments": list(REST_URLS),
        "indices": _allowed_indices(config),
        "revision": loaded["revision"],
        "editable": ctx.settings.config_edit_enabled,
    }


def _checked_capital_instruments(section: dict) -> None:
    """An epic twice, or two under one label, would mix their bars and their day files."""
    entries = section.get("instruments")
    if not isinstance(entries, list):
        return
    epics, labels = set(), set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        epic, label = entry.get("epic"), entry.get("label")
        if epic in epics:
            raise HTTPException(status_code=422, detail=f"{epic} is listed twice")
        if label in labels:
            raise HTTPException(status_code=422, detail=f"Two instruments are labelled {label!r}")
        epics.add(epic)
        labels.add(label)


@router.put("/providers/capital/config", dependencies=[Depends(require_config_edit_enabled)])
async def save_capital_config(body: RecorderConfigRequest, request: Request):
    ctx = request.app.state.ctx
    _checked_capital_instruments(body.section)
    try:
        saved = await run_in_threadpool(ctx.config_editor.save_section, CAPITAL_SECTION, body.section, body.revision)
    except ConfigInvalid as e:
        raise HTTPException(status_code=422, detail=str(e))
    except ConfigConflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    ctx.audit(
        request,
        "Capital.com feed configuration saved",
        f"Previous version kept as {saved['backup']}. Takes effect at the feed's next start.",
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


class Period(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    first_day: date = Field(alias="from")
    last_day: date = Field(alias="to")


class FetchRequest(Period):
    # The Dukascopy instrument; the history kept up to date when absent.
    label: str | None = None


class KeepRequest(BaseModel):
    enabled: bool
    days: int = Field(KEEP_DEFAULT_DAYS, ge=1)


@router.get("/providers/dukascopy/coverage")
async def history_coverage(
    first_day: date | None = Query(None, alias="from"),
    last_day: date | None = Query(None, alias="to"),
    label: str | None = Query(None),
    service=Depends(_download_service),
):
    """
    Which days of an instrument (the history kept up to date by default) are
    there, and the download in progress if there is one. Off the event loop:
    each day's hours are looked for on the disk.
    """
    last_day = last_day or _now().date()
    first_day = first_day or last_day - timedelta(days=DEFAULT_COVERAGE_DAYS - 1)
    return await run_in_threadpool(service.coverage, first_day, last_day, label)


@router.post("/providers/dukascopy/fetch", status_code=202)
async def fetch_history(body: FetchRequest, service=Depends(_download_service)):
    """Download the days of a period in the background. One download at a time."""
    return service.start_fetch(body.first_day, body.last_day, label=body.label)


@router.post("/providers/dukascopy/fetch/stop")
async def stop_fetching_history(service=Depends(_download_service)):
    return service.stop_fetch()


@router.put("/providers/dukascopy/keep")
async def keep_history(body: KeepRequest, service=Depends(_download_service)):
    """Have the service fetch by itself what is missing of the last days, or stop doing so."""
    return service.set_keep(body.enabled, body.days)


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
    saxo_error, recorded, streamed, feed = None, [], [], None
    try:
        recorded = await ctx.queries.price_assets(MAX_ASSETS + 1, since=since)
        streamed = await ctx.queries.capital_assets()
        feed = await ctx.queries.capital_feed_state()
    except DatabaseUnavailable:
        saxo_error = "The database is unavailable: the assets recorded from Saxo and Capital.com cannot be listed"
    cutoff = since.timestamp() if since is not None else None
    # Capital.com's instruments are indexes followed live: listed first, as the recorder's index is.
    items = [
        {**asset, "status": capital_status(asset, feed, now)}
        for asset in (capital_asset(row) for row in streamed)
        if cutoff is None or (asset.get("last_t") or 0) >= cutoff
    ]
    items += [{**saxo_asset(row), "status": saxo_status(row, now, refresh)} for row in recorded[:MAX_ASSETS]]

    if ctx.history is not None:
        downloaded = await run_in_threadpool(_history_assets, ctx.history)
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
    elif provider == CAPITAL:
        row = await ctx.queries.capital_instrument(capital_epic(key))
        if row is None:
            raise AssetNotFound(f"No Capital.com instrument {key!r} with a bar")
        feed = await ctx.queries.capital_feed_state()
        asset = {**capital_asset(row), "status": capital_status(capital_asset(row), feed, _now())}
        first, last = _day_of(ctx, asset["first_t"]), _day_of(ctx, asset["last_t"])
        index = None
        settings = _capital_settings(ctx)
        label = asset["indice"]
        (day_files,) = await run_in_threadpool(_capital_day_files, ctx.settings.price_history_path, [label])
        figures = (feed or {}).get("figures") or {}
        instrument_figures = next(
            (entry for entry in figures.get("instruments") or [] if isinstance(entry, dict) and entry.get("epic") == asset["epic"]),
            {},
        )
        recording = {
            "epic": asset["epic"],
            "label": label,
            "environment": (feed or {}).get("environment") or (settings.environment if settings else None),
            "feed_state": (feed or {}).get("state"),
            "feed_detail": (feed or {}).get("detail"),
            "feed_updated_at": (feed or {}).get("updated_at"),
            "market": instrument_figures.get("market"),
            "next_opening": instrument_figures.get("next_opening"),
            "opening_hours": await ctx.queries.capital_opening_hours(asset["epic"]),
            "first_seen_at": row.get("first_seen_at"),
            "last_seen_at": row.get("last_seen_at"),
            "quotes_per_minute": figures.get("quotes_per_minute"),
            "delay_ms": figures.get("delay_ms"),
            "minute_volume": settings.minute_volume if settings else None,
            "retention_months": settings.retention_months if settings else None,
            "day_files": day_files,
            # The chart of a day still going follows the stream.
            "live": True,
        }
        max_days = {"chart": MAX_PERIOD_DAYS, "range": RANGE_MAX_DAYS, "statistics": STATISTICS_MAX_DAYS,
                    "availability": 0, "gaps": GAPS_MAX_DAYS}
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
    if not interval_served(interval):
        raise HTTPException(
            status_code=422, detail=f"The interval is a whole number of seconds from {INTERVALS[0]} to {MAX_INTERVAL}",
        )
    if checked_provider(provider) == DUKASCOPY:
        _history(ctx)
    return await asset_bars.bars(
        ctx.queries, ctx.history, ctx.settings.timezone, provider, key, first_day, last_day, interval, today=_today(ctx),
    )


class _Series:
    """The ten-second bars of one side, in columns: a month of them is a few megabytes this way."""

    def __init__(self):
        self.times = array("q")
        self.opens, self.highs, self.lows, self.closes = array("d"), array("d"), array("d"), array("d")
        # The quote changes of each ten seconds: what stands for volume (0 when a reader has none).
        self.updates = array("q")

    def add(self, rows) -> None:
        for row in rows:
            moment, opened, high, low, closed = row[:5]
            self.times.append(moment)
            self.opens.append(opened)
            self.highs.append(high)
            self.lows.append(low)
            self.closes.append(closed)
            self.updates.append(int(row[5] or 0) if len(row) > 5 else 0)


def _too_many_rows() -> HTTPException:
    return HTTPException(status_code=422, detail="Too many recorded bars in this period to read them all: choose a shorter one")


async def _db_series(read, instrument_id: int, start: datetime, end: datetime, side: str) -> _Series:
    """One side of the ten-second bars of a recorded instrument, read a page at a time by ``read``."""
    series = _Series()
    page_start = start
    while True:
        rows = await read(instrument_id, page_start, end, side, SERIES_PAGE)
        series.add(rows)
        if len(series.times) > RANGE_MAX_ROWS:
            raise _too_many_rows()
        if len(rows) < SERIES_PAGE:
            return series
        page_start = datetime.fromtimestamp(series.times[-1] + 1, timezone.utc)


def _history_series(history, label: str, start: datetime, end: datetime, side: str) -> _Series:
    """One side of the ten-second bars of a Dukascopy history; the ask of a bar that has none is left out."""
    first = 1 if side == SIDES[0] else 5
    updates = BAR_COLUMNS.index("updates")
    series = _Series()
    series.add(
        (row[0], *row[first:first + 4], row[updates])
        for row in history.rows(label, start, end) if row[first] is not None
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
    ticks: int | None = Query(None, ge=1),
    under: int | None = Query(None, ge=1),
):
    """
    The prices of one side as range bars: a bar ends when the price has left a
    range of ``size``, however long that took. Or of ``ticks`` steps of the
    instrument's own price, as TradingView's "R" counts them (Capital.com says
    its step; elsewhere it is the step of the prices read). Without either, a
    range that suits the period is chosen. ``under`` names the recorded index
    shown under a turbo: its bid at the end of each bar comes with the bar.
    Each bar comes with its quote changes, what stands for its volume.
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
        index_instrument = await asset_bars.saxo_instrument(ctx.queries, str(under)) if under is not None else None
        series = await _db_series(ctx.queries.price_series, instrument["id"], start, end, side)
    elif provider == CAPITAL:
        # An index: nothing goes under it.
        index_instrument = None
        series = await _db_series(ctx.queries.capital_series, instrument["id"], start, end, side)
    else:
        # A Dukascopy history is an index: nothing goes under it.
        index_instrument = None
        series = await run_in_threadpool(_history_series, _history(ctx), instrument["key"], start, end, side)

    decimals = price_decimals(series.opens, series.highs, series.lows, series.closes)
    tick = round(10 ** -decimals, decimals)
    if size is not None and ticks is not None:
        raise HTTPException(status_code=422, detail="Give the range as a size or as ticks, not both")
    if ticks is not None:
        step_decimals = instrument.get("decimals") if provider == CAPITAL else None
        step_decimals = decimals if step_decimals is None else min(int(step_decimals), decimals)
        size = ticks * 10 ** -step_decimals
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
        index_series = await _db_series(ctx.queries.price_series, index_instrument["id"], start, end, SIDES[0])
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
        "bars": range_rows(built, decimals, index, range_volumes(built, series.times, series.updates)),
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
    elif provider == CAPITAL:
        # The positions of its indice: those its signals opened, or all of them while WATA trades it alone.
        config = (await run_in_threadpool(ctx.config_editor.load))["config"]
        trades = await ctx.queries.index_trades(
            instrument["indice"], start, end, every_position=_allowed_indices(config) == [instrument["indice"]],
        )
    else:
        # The turbos of the index the recorder followed; without the database, the chart is drawn without them.
        try:
            trades = await ctx.queries.price_trades(instrument, start, end)
        except DatabaseUnavailable:
            trades = []
    try:
        journal = await ctx.queries.signal_journal(start=first_day, end=last_day, limit=SIGNAL_LIMIT)
    except DatabaseUnavailable:
        if provider != DUKASCOPY:
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
    elif provider == CAPITAL:
        if (last_day - first_day).days < GAPS_MAX_DAYS:
            trading_time = await _capital_trading_time(ctx, [instrument["epic"]])
            gaps = recording_gaps(
                await ctx.queries.capital_coverage(start, end), start.astimezone(timezone.utc),
                end.astimezone(timezone.utc), trading_time, _now(),
            )
        days = await ctx.queries.capital_volume_days(instrument["id"], start, end)
        # Capital.com's own volume of the minutes; None when none was fetched.
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
    # Capital.com's markets have hours of their own; Saxo's and Dukascopy's are read in WATA's trading hours.
    is_trading_time = (
        await _capital_trading_time(ctx, [instrument["epic"]]) if provider == CAPITAL else settings.is_trading_time
    )
    daily = DailyStatistics(ctx.settings.timezone, is_trading_time, spread=spread)
    by_time = SpreadByTime(ctx.settings.timezone)

    def feed(rows) -> None:
        daily.add(rows)
        if spread:
            by_time.add(rows)

    volumes = {}
    if provider in (SAXO, CAPITAL):
        async def page_of(page_start):
            if provider == SAXO:
                return await ctx.queries.price_bars(instrument["id"], page_start, end, INTERVALS[0], SERIES_PAGE)
            rows, _ = await ctx.queries.capital_bars(instrument["id"], page_start, end, INTERVALS[0], SERIES_PAGE)
            return rows

        page_start, read = start, 0
        while True:
            rows = await page_of(page_start)
            read += len(rows)
            if read > RANGE_MAX_ROWS:
                raise _too_many_rows()
            await run_in_threadpool(feed, rows)
            if len(rows) < SERIES_PAGE:
                break
            page_start = datetime.fromtimestamp(rows[-1][0] + 1, timezone.utc)
        if provider == SAXO:
            volumes = await ctx.queries.price_volume_days(instrument["id"], start, end)
        else:
            volumes = await ctx.queries.capital_volume_days(instrument["id"], start, end)
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


@router.get("/assets/{provider}/{key}/volume")
async def volume(
    provider: str,
    key: str,
    request: Request,
    first_day: date | None = Query(None, alias="from"),
    last_day: date | None = Query(None, alias="to"),
    interval: int = Query(60),
):
    """
    Capital.com's own volume of each minute of the period, added up by
    ``interval``: what it gives its candles (``lastTradedVolume``), beside the
    quotes the feed counted itself (``updates`` of the bars).
    """
    ctx = request.app.state.ctx
    if checked_provider(provider) != CAPITAL:
        raise AssetNotFound("Only Capital.com gives a volume of its own for its minutes")
    if interval not in INTERVALS or interval < 60:
        raise HTTPException(status_code=422, detail="Capital.com counts its volume by minute: choose bars of a minute or longer")
    first_day, last_day, start, end = _period(ctx, first_day, last_day)
    instrument = await asset_bars.capital_instrument(ctx.queries, key)
    rows = await ctx.queries.capital_volume(instrument["id"], start, end, interval, MAX_BUCKETS + 1)
    if len(rows) > MAX_BUCKETS:
        raise HTTPException(status_code=422, detail=f"More than {MAX_BUCKETS} bars in this period at {interval} seconds")
    return {
        "instrument": instrument,
        "from": first_day,
        "to": last_day,
        "timezone": ctx.settings.timezone,
        "interval": interval,
        "columns": ["t", "volume"],
        "volume": rows,
    }


@router.get("/assets/{provider}/{key}/live")
async def live(provider: str, key: str, request: Request):
    """
    Server-sent events: the asset's prices as the Capital.com feed receives
    them. ``snapshot`` first (the server's clock in milliseconds, the last
    quote and state known), then each ``quote`` with the ten-second bar it is
    part of, each ``bar`` once closed, the ``state`` of its market, and
    ``live`` when the connection to the prices is lost or found again.
    ``resync`` asks the page to read the bars again. Each message says when it
    was quoted (``t``), received by the feed (``rx``) and sent here (``sx``).
    """
    ctx = request.app.state.ctx
    if checked_provider(provider) != CAPITAL:
        raise AssetNotFound("Only the prices of the Capital.com feed are followed live")
    epic = capital_epic(key)
    if ctx.live_prices is None or not ctx.live_prices.available:
        raise HTTPException(status_code=503, detail="Live prices are not available")
    ref = ref_of(CAPITAL, epic)
    token = request.cookies.get(session_cookie_name(ctx.settings.cookie_secure))
    queue = ctx.live_prices.subscribe(ref)

    def frame(kind: str, message: dict) -> str:
        return f"event: {kind}\ndata: {json.dumps(jsonable_encoder(message), separators=(',', ':'))}\n\n"

    async def stream():
        try:
            yield "retry: 2000\n\n"
            yield frame("snapshot", {"server_ms": int(time.time() * 1000), **ctx.live_prices.snapshot(ref)})
            quiet_since = time.monotonic()
            # As /api/events: the browser leaving is asked, never told.
            while ctx.sessions.is_alive(token, ctx.credentials.version()) and not await request.is_disconnected():
                try:
                    message = await asyncio.wait_for(queue.get(), LIVE_POLL_SECONDS)
                except asyncio.TimeoutError:
                    if time.monotonic() - quiet_since >= LIVE_KEEP_ALIVE_SECONDS:
                        quiet_since = time.monotonic()
                        yield ": keep-alive\n\n"
                    continue
                quiet_since = time.monotonic()
                yield frame(message["type"], {**message, "sx": int(time.time() * 1000)})
        finally:
            ctx.live_prices.unsubscribe(ref, queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        # Not to be buffered by a proxy on the way.
        headers={"X-Accel-Buffering": "no"},
    )

