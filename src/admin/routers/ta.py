"""
Technical Analysis: the Pine scripts written in the admin, the chart layouts of
its Vela charts (src/admin/ta_scripts.py keeps them as files), the signal rules
that run scripts on WATA's live bars (src/admin/ta_rules.py), and what the TA
engine did with them (read from its tables).

A script changes nothing that trades, so its changes are not operator actions:
no audit entry, as for backtests. Only native Pine Script is accepted. A rule
goes live, or a live rule changes, as an operator action does: allowed only
when actions are, recorded in the audit trail and announced on Telegram.
"""

import time
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from src.admin.auth import require_session
from src.admin.health import DOWN, ta_engine_health
from src.admin.routers.actions import _allowed_indices, require_actions_enabled
from src.admin.ta_compare import compare, default_tolerance
from src.admin.ta_monitor import (
    DEFAULT_HOURS,
    TICK_SECONDS,
    WINDOW_MINUTES,
    bucket_of,
    kept_days,
    now_answer,
    period_answer,
    period_bounds,
    period_of,
    rules_by_id,
)
from src.admin.rule_history import RecordedRuleStore
from src.admin.ta_history import DAY, HistoryCount, last_restart
from src.admin.ta_rules import (
    LIVE,
    MAX_ALERTS,
    MAX_HISTORY_DAYS,
    MAX_INPUTS,
    MAX_TITLE_LENGTH,
    RANGE_BARS,
    SHADOW,
    SIDES,
    TIME_BARS,
    RuleConflict,
    RuleStore,
    bars_label,
    checked_asset,
    checked_bars,
    checked_history_days,
)
from src.admin.ta_readiness import unmet_readiness
from src.admin.ta_scripts import MAX_DESCRIPTION_LENGTH, MAX_NAME_LENGTH
from src.admin.errors import ServiceUnavailable
from src.capital_feed.settings import CapitalSettings
from src.ta_engine.settings import TaEngineSettings
from src.trade.signal_sources import ENGINE, source_of, sources_from

router = APIRouter(prefix="/api/ta", dependencies=[Depends(require_session)])

# The Capital.com feed's ten-second rows a count of a rule's bars reads at a time (8,640 a day at most).
HISTORY_PAGE = 20_000


def scripts(request: Request):
    """The script store, when the admin has one."""
    store = request.app.state.ctx.ta_scripts
    if store is None:
        raise HTTPException(status_code=503, detail="Technical Analysis is not available")
    return store


class ScriptCreate(BaseModel):
    name: str = Field(max_length=MAX_NAME_LENGTH * 2)
    source: str
    description: str = Field("", max_length=MAX_DESCRIPTION_LENGTH * 2)


class ScriptUpdate(BaseModel):
    # The version the change was made on: a save made meanwhile is not overwritten.
    base_version: int = Field(ge=1)
    source: str | None = None
    name: str | None = Field(None, max_length=MAX_NAME_LENGTH * 2)
    description: str | None = Field(None, max_length=MAX_DESCRIPTION_LENGTH * 2)


class ScriptCopy(BaseModel):
    name: str = Field(max_length=MAX_NAME_LENGTH * 2)


class Workspace(BaseModel):
    value: str


def rules(store=Depends(scripts)) -> RuleStore:
    """The signal rules, kept beside the scripts they run, each change of what one runs kept in their history."""
    return RecordedRuleStore(store)


class AssetRef(BaseModel):
    provider: str = Field(max_length=16)
    key: str = Field(max_length=64)


class Bars(BaseModel):
    type: str = Field(max_length=8)
    timeframe: str | None = Field(None, max_length=4)
    ticks: int | None = None


class ScriptPin(BaseModel):
    id: str = Field(max_length=12)
    version: int = Field(ge=1)


class RuleInput(BaseModel):
    id: str = Field(max_length=8)
    var: str | None = Field(None, max_length=80)
    title: str | None = Field(None, max_length=MAX_TITLE_LENGTH * 2)
    value: bool | int | float | str


class AlertMapping(BaseModel):
    title: str = Field(max_length=MAX_TITLE_LENGTH)
    action: str = Field(max_length=16)


class RuleFields(BaseModel):
    # The revision of the rules the change was made on: a change made meanwhile is not overwritten.
    revision: str = Field(max_length=64)
    name: str = Field(max_length=MAX_NAME_LENGTH * 2)
    asset: AssetRef
    side: str = Field("bid", max_length=4)
    bars: Bars
    script: ScriptPin
    inputs: list[RuleInput] = Field(default_factory=list, max_length=MAX_INPUTS)
    alerts: list[AlertMapping] = Field(default_factory=list, max_length=MAX_ALERTS)
    alert_calls: str = Field("ignore", max_length=16)
    confidence_plot: str | None = Field(None, max_length=MAX_TITLE_LENGTH)
    indice: str = Field(max_length=32)
    # Its own days of bars before each engine day; None: the engine's history_days.
    history_days: int | None = Field(None, ge=0, le=MAX_HISTORY_DAYS)

    def fields(self) -> dict:
        return self.model_dump(exclude={"revision", "mode", "refresh_libraries"})


class RuleCreate(RuleFields):
    mode: Literal["off", "shadow"] = SHADOW


class RuleUpdate(RuleFields):
    # Run the current versions of the libraries the script imports.
    refresh_libraries: bool = False


class RuleMode(BaseModel):
    revision: str = Field(max_length=64)
    mode: Literal["off", "shadow", "live"]
    # Go live though the rule is not ready (src/admin/ta_readiness.py): recorded with what was missing.
    force: bool = False


# What the audit trail says changed in a live rule.
_RULE_FIELDS = (
    "name", "asset", "side", "bars", "script", "libraries", "inputs", "alerts", "alert_calls",
    "confidence_plot", "indice", "history_days",
)


@router.get("/scripts")
async def list_scripts(store=Depends(scripts)):
    return {"items": await run_in_threadpool(store.scripts)}


@router.post("/scripts", status_code=201)
async def create_script(body: ScriptCreate, store=Depends(scripts)):
    return await run_in_threadpool(store.create, body.name, body.source, body.description)


@router.get("/scripts/{script_id}")
async def get_script(script_id: str, store=Depends(scripts)):
    return await run_in_threadpool(store.script, script_id)


@router.put("/scripts/{script_id}")
async def update_script(script_id: str, body: ScriptUpdate, store=Depends(scripts)):
    return await run_in_threadpool(
        store.save, script_id, body.base_version, body.source, body.name, body.description,
    )


@router.delete("/scripts/{script_id}", status_code=204)
async def delete_script(script_id: str, store=Depends(scripts), rule_store: RuleStore = Depends(rules)):
    def users(meta: dict) -> list[str]:
        return [f"the signal rule {name!r}" for name in rule_store.users_of(meta["id"])]

    await run_in_threadpool(store.delete, script_id, users)
    return Response(status_code=204)


@router.post("/scripts/{script_id}/duplicate", status_code=201)
async def duplicate_script(script_id: str, body: ScriptCopy, store=Depends(scripts)):
    return await run_in_threadpool(store.duplicate, script_id, body.name)


@router.get("/scripts/{script_id}/versions/{number}")
async def get_version(script_id: str, number: int, store=Depends(scripts)):
    return await run_in_threadpool(store.version, script_id, number)


@router.get("/scripts/{script_id}/source.pine")
async def download_source(script_id: str, store=Depends(scripts)):
    script = await run_in_threadpool(store.script, script_id)
    file_name = "".join(char if char.isalnum() or char in "-_" else "-" for char in script["name"]).strip("-")
    return Response(
        content=script["source"],
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{file_name or script_id}.pine"'},
    )


@router.get("/libraries/{name}")
async def get_library(name: str, store=Depends(scripts)):
    return await run_in_threadpool(store.library, name)


@router.get("/workspaces/{key}")
async def get_workspace(key: str, store=Depends(scripts)):
    return {"value": await run_in_threadpool(store.workspace, key)}


@router.put("/workspaces/{key}", status_code=204)
async def put_workspace(key: str, body: Workspace, store=Depends(scripts)):
    await run_in_threadpool(store.save_workspace, key, body.value)
    return Response(status_code=204)


# ── signal rules ──


def _described(rule: dict, scripts_by_id: dict) -> dict:
    """A rule with the name of its script, and the versions it could be updated to."""
    script = scripts_by_id.get(rule["script"]["id"])
    return {
        **rule,
        "script_name": script["name"] if script else None,
        "latest": {
            "script": script["version"] if script else None,
            "libraries": {
                entry["name"]: (scripts_by_id.get(entry["id"]) or {}).get("version") for entry in rule["libraries"]
            },
        },
    }


def _rule_line(rule: dict, script_name: str | None) -> str:
    asset = rule["asset"]
    return (
        f"rule {rule['name']!r} ({rule['id']}): indice={rule['indice']} asset={asset['provider']}/{asset['key']} "
        f"{rule['side']} bars={bars_label(rule['bars'])} script={script_name!r} v{rule['script']['version']}"
    )


async def _answer(result: dict, store) -> dict:
    scripts_by_id = {meta["id"]: meta for meta in await run_in_threadpool(store.scripts)}
    return {"rule": _described(result["rule"], scripts_by_id), "revision": result["revision"]}


def _feed_instruments(config_manager) -> list[dict]:
    """The instruments the Capital.com feed records, as configured: what a rule can run on."""
    try:
        feed = CapitalSettings.from_config(config_manager)
    except ValueError:
        return []
    return [{"epic": instrument.epic, "label": instrument.label} for instrument in feed.instruments]


@router.get("/rules")
async def list_rules(request: Request, store=Depends(scripts), rule_store: RuleStore = Depends(rules)):
    """
    The rules, with what a rule can be made of: the indices the trader accepts,
    the instruments the Capital.com feed records, the engine's history_days (the
    days of bars before each day a rule without its own is given) and the time
    (UTC) it starts every rule again.
    """
    loaded = await run_in_threadpool(rule_store.load)
    scripts_by_id = {meta["id"]: meta for meta in await run_in_threadpool(store.scripts)}
    config_manager = request.app.state.ctx.config_manager
    engine = _engine_settings(config_manager)
    return {
        "revision": loaded["revision"],
        "rules": [_described(rule, scripts_by_id) for rule in loaded["rules"]],
        "indices": sorted(_allowed_indices(config_manager)),
        "instruments": _feed_instruments(config_manager),
        "engine_history_days": engine.history_days,
        "engine_restart_time": engine.restart_time,
    }


@router.post("/rules", status_code=201)
async def create_rule(body: RuleCreate, store=Depends(scripts), rule_store: RuleStore = Depends(rules)):
    result = await run_in_threadpool(rule_store.create, body.fields(), body.revision, body.mode)
    return await _answer(result, store)


@router.get("/rules/{rule_id}")
async def get_rule(rule_id: str, store=Depends(scripts), rule_store: RuleStore = Depends(rules)):
    loaded = await run_in_threadpool(rule_store.load)
    rule = RuleStore.find(loaded["rules"], rule_id)
    return await _answer({"rule": rule, "revision": loaded["revision"]}, store)


@router.put("/rules/{rule_id}")
async def update_rule(
    rule_id: str,
    body: RuleUpdate,
    request: Request,
    store=Depends(scripts),
    rule_store: RuleStore = Depends(rules),
):
    """A rule changed, its mode kept. A live rule's change is an operator action."""
    if (await run_in_threadpool(rule_store.rule, rule_id))["mode"] == LIVE:
        require_actions_enabled(request)
    result = await run_in_threadpool(
        rule_store.update, rule_id, body.fields(), body.revision, body.refresh_libraries,
    )
    rule, before = result["rule"], result["before"]
    answer = await _answer(result, store)
    if rule["mode"] == LIVE and rule != before:
        changed = ",".join(name for name in _RULE_FIELDS if rule.get(name) != before.get(name))
        line = _rule_line(rule, answer["rule"]["script_name"])
        request.app.state.ctx.audit(request, "live signal rule changed", f"{line} changed={changed}")
    return answer


@router.delete("/rules/{rule_id}", status_code=204)
async def delete_rule(rule_id: str, revision: str = Query(max_length=64), rule_store: RuleStore = Depends(rules)):
    await run_in_threadpool(rule_store.delete, rule_id, revision)
    return Response(status_code=204)


async def _check_can_go_live(request: Request, rule: dict) -> None:
    """
    What only the configuration says: the indice is traded, the feed records the asset's bars, and the indice
    takes the engine's signals as the web server runs (or TradingView's alerts would trade it beside the rule).
    """
    require_actions_enabled(request)
    ctx = request.app.state.ctx
    config_manager = ctx.config_manager
    if rule["indice"] not in _allowed_indices(config_manager):
        raise RuleConflict(f"{rule['indice']} is not one of the allowed indices of the trading rules")
    try:
        feed = CapitalSettings.from_config(config_manager)
    except ValueError as e:
        raise RuleConflict(f"The Capital.com feed's settings are not valid: {e}") from None
    if not feed.enabled:
        raise RuleConflict("The Capital.com feed is off: a live rule would have no bars")
    if rule["asset"]["key"] not in {instrument.epic for instrument in feed.instruments}:
        raise RuleConflict(f"The Capital.com feed does not record {rule['asset']['key']}: a live rule would have no bars")
    try:
        webhook = await ctx.services.webhook_state()
    except ServiceUnavailable as e:
        raise RuleConflict(f"The web server does not say which signals {rule['indice']} takes: {e}") from None
    if webhook is None or source_of(sources_from(webhook.get("sources")), rule["indice"]) != ENGINE:
        raise RuleConflict(
            f"{rule['indice']} takes TradingView's signals: set it to the engine in trade.config.signal_sources "
            "(Configuration page) and restart the web server and the TA engine first, or TradingView's alerts "
            "and this rule would trade it together"
        )


async def _unmet_readiness(request: Request, rule: dict) -> list[str]:
    """What keeps ``rule`` from being ready to go live, read where the Signal rules page reads it."""
    ctx = request.app.state.ctx
    states = await ctx.queries.ta_rule_states()
    paper = None
    if getattr(ctx, "backtests", None) is not None:
        try:
            paper = (await run_in_threadpool(ctx.backtests.paper))["rules"].get(rule["id"])
        except Exception:
            paper = None
    return unmet_readiness(rule, states.get(rule["id"]), paper, datetime.now(timezone.utc), ctx.settings.timezone)


@router.post("/rules/{rule_id}/mode")
async def set_rule_mode(
    rule_id: str,
    body: RuleMode,
    request: Request,
    store=Depends(scripts),
    rule_store: RuleStore = Depends(rules),
):
    """
    Switch a rule off, to shadow or live. Going live is an operator action; leaving
    live is always allowed (it stops signals), and recorded as one.
    """
    current = await run_in_threadpool(rule_store.rule, rule_id)
    unmet = []
    if body.mode == LIVE and current["mode"] != LIVE:
        await _check_can_go_live(request, current)
        unmet = await _unmet_readiness(request, current)
        if unmet and not body.force:
            raise RuleConflict(
                f"Not ready to go live: {'; '.join(unmet)}. Go live anyway to switch it all the same: that is recorded"
            )
    result = await run_in_threadpool(rule_store.set_mode, rule_id, body.mode, body.revision)
    rule, before = result["rule"], result["before"]
    answer = await _answer(result, store)
    if rule["mode"] != before["mode"] and LIVE in (rule["mode"], before["mode"]):
        action = "signal rule live" if rule["mode"] == LIVE else f"signal rule no longer live ({rule['mode']})"
        if rule["mode"] == LIVE and unmet:
            action += f" WITHOUT its readiness ({'; '.join(unmet)})"
        request.app.state.ctx.audit(request, action, _rule_line(rule, answer["rule"]["script_name"]))
    return answer


@router.get("/rules/{rule_id}/comparison")
async def compare_rule(
    rule_id: str,
    request: Request,
    day: date = Query(),
    tolerance: int | None = Query(None, ge=1, le=3600),
    rule_store: RuleStore = Depends(rules),
):
    """
    The rule's signals of ``day`` beside those the trader received for its indice
    from elsewhere (TradingView's), paired by action within ``tolerance`` seconds.
    """
    queries = request.app.state.ctx.queries
    rule = await run_in_threadpool(rule_store.rule, rule_id)
    wata = await queries.ta_signals(start=day, end=day, rule_id=rule_id, limit=2000)
    others = await queries.ta_other_signals(rule["indice"], day)
    return {
        "day": day.isoformat(),
        "indice": rule["indice"],
        **compare(wata, others, tolerance if tolerance is not None else default_tolerance(rule["bars"])),
    }


def _moment(seconds: float) -> datetime:
    return datetime.fromtimestamp(seconds, timezone.utc)


def _now() -> float:
    """Now, epoch seconds: what tests set."""
    return time.time()


@router.get("/history")
async def history_bars(
    request: Request,
    epic: str = Query(max_length=64),
    side: str = Query("bid", max_length=4),
    timeframe: str | None = Query(None, max_length=8),
    ticks: int | None = Query(None),
    days: int | None = Query(None),
):
    """
    How many bars the TA engine would run a rule's script on, given its asset, side and bars
    (a timeframe, or range bars of ``ticks``) and its own days of history (the engine's
    history_days without ``days``): those it was loaded with at the last restart_time, those
    it would run on now, and those of each of the last engine days, restart to restart.
    Made by the engine's own builders on the Capital.com feed's rows (src/admin/ta_history.py).
    """
    ctx = request.app.state.ctx
    settings = _engine_settings(ctx.config_manager)
    asset = checked_asset({"provider": "capital", "key": epic})
    bars = checked_bars({"type": RANGE_BARS, "ticks": ticks} if ticks is not None else {"type": TIME_BARS, "timeframe": timeframe})
    if side not in SIDES:
        raise HTTPException(status_code=422, detail=f"The side is one of {', '.join(SIDES)}")
    own_days = checked_history_days(days)
    history_days = settings.history_days if own_days is None else own_days
    instrument = await ctx.queries.capital_instrument(asset["key"])
    if instrument is None:
        raise HTTPException(status_code=404, detail=f"The Capital.com feed has not recorded {asset['key']}")

    now = _now()
    restart = last_restart(now, settings.restart_minute)
    start = restart - history_days * DAY
    count = HistoryCount(bars, side, instrument.get("decimals"), start=start, restart=restart, now=now)
    page_start = count.first
    while True:
        rows = await ctx.queries.capital_engine_rows(instrument["id"], _moment(page_start), _moment(now), HISTORY_PAGE)
        # A page of range bars is some tenths of a second of Python: away from the requests' loop.
        await run_in_threadpool(count.add, rows)
        if len(rows) < HISTORY_PAGE:
            break
        page_start = rows[-1][0] + 1
    count.finish()
    return {
        "asset": asset,
        "side": side,
        "bars": bars,
        "history_days": history_days,
        "restart_time": settings.restart_time,
        "restarted_at": _moment(restart),
        "starts_at": _moment(start),
        "at_restart": count.at_restart,
        "now": count.loaded,
        "engine_days": [
            {"start": _moment(day["start"]), "end": _moment(day["end"]), "bars": day["bars"]} for day in count.engine_days()
        ],
    }


# ── what the TA engine did ──


@router.get("/signals")
async def list_signals(
    request: Request,
    start: date | None = Query(None, alias="from"),
    end: date | None = Query(None, alias="to"),
    rule: str | None = Query(None, pattern=r"^[0-9a-f]{12}$"),
    limit: int = Query(500, ge=1, le=2000),
):
    """The signals the TA engine raised, newest first, with what the trader made of the ones sent."""
    queries = request.app.state.ctx.queries
    return {"items": await queries.ta_signals(start=start, end=end, rule_id=rule, limit=limit)}


@router.get("/engine")
async def engine_state(request: Request):
    """
    Whether the TA engine is switched on, how it is doing, what it last said of
    itself and of each rule (null and empty when it never ran).
    """
    ctx = request.app.state.ctx
    engine = await ctx.queries.ta_engine_state()
    try:
        settings = TaEngineSettings.from_config(ctx.config_manager)
    except ValueError as e:
        enabled, (status, detail) = False, (DOWN, f"Invalid configuration: {e}")
    else:
        enabled = settings.enabled
        status, detail = await ta_engine_health(ctx, enabled, settings, engine, datetime.now(timezone.utc))
    return {
        "enabled": enabled,
        "status": status,
        "detail": detail,
        "engine": engine,
        "rules": await ctx.queries.ta_rule_states(),
    }


# ── the Engine monitor: how fast the engine runs, how late its signals are ──


def _engine_settings(config_manager) -> TaEngineSettings:
    """The engine's settings, its defaults while they are invalid (the System page says why)."""
    try:
        return TaEngineSettings.from_config(config_manager)
    except ValueError:
        return TaEngineSettings()


@router.get("/monitor/now")
async def monitor_now(request: Request):
    """
    The TA engine's last minutes: its last tick, what its runs and the live signals were, and the
    checks of what can cost a live signal, the first failed one of which health says.
    """
    ctx = request.app.state.ctx
    settings = _engine_settings(ctx.config_manager)
    now = datetime.now(timezone.utc)
    state = await ctx.queries.ta_engine_state()
    recent = await ctx.queries.ta_monitor_recent(now - timedelta(minutes=WINDOW_MINUTES), slow_tick_ms=TICK_SECONDS * 1000)
    rules = await run_in_threadpool(rules_by_id, ctx.ta_scripts)
    return now_answer(state=state, settings=settings, mcp_limits=ctx.settings.mcp_limits, recent=recent, rules=rules, now=now)


@router.get("/monitor")
async def monitor(
    request: Request,
    hours: int = Query(DEFAULT_HOURS, ge=1, le=720),
    rule: str | None = Query(None, pattern=r"^[0-9a-f]{12}$"),
):
    """
    A period of what the TA engine measured (at most the days it keeps them), for every rule or one:
    its runs and ticks by bar of time, its signals' delivery from the bar's close on, their results.
    """
    ctx = request.app.state.ctx
    settings = _engine_settings(ctx.config_manager)
    state = await ctx.queries.ta_engine_state()
    days = kept_days(state, settings)
    try:
        hours = period_of(hours, days)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    bucket = bucket_of(hours)
    start, end = period_bounds(datetime.now(timezone.utc), hours, bucket)
    rows = await ctx.queries.ta_monitor_period(
        start, end, bucket, rule, signal_age_seconds=settings.max_signal_age_seconds, slow_tick_ms=TICK_SECONDS * 1000,
    )
    rules = await run_in_threadpool(rules_by_id, ctx.ta_scripts)
    return period_answer(
        hours=hours, days=days, bucket=bucket, start=start, end=end, rule_id=rule, state=state, settings=settings,
        mcp_limits=ctx.settings.mcp_limits, rows=rows, rules=rules,
    )
