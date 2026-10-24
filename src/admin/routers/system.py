import asyncio
import json
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from src.admin.auth import require_session, session_cookie_name
from src.admin.errors import ServiceUnavailable
from src.admin.health import collect_health
from src.admin.logs import LEVELS, LOG_SERVICES, MAX_LINES, read_log
from src.admin.saxo import called
from src.admin.timing import read_execution_timings

# How long the event stream stays silent before a comment line keeps the connection open.
EVENT_KEEP_ALIVE_SECONDS = 15.0
# How often an idle stream checks that its browser and its session are still there.
EVENT_POLL_SECONDS = 1.0

router = APIRouter(prefix="/api", dependencies=[Depends(require_session)])


@router.get("/signals")
async def latest_signals(request: Request):
    signals = await request.app.state.ctx.services.latest_signals()
    return {"items": sorted(signals, key=lambda signal: signal.get("seq", 0), reverse=True)}


@router.get("/system/health")
async def health(request: Request):
    return await collect_health(request.app.state.ctx)


@router.get("/system/saxo")
async def saxo_status(request: Request):
    return await called(request.app.state.ctx.saxo.status)


def _allowed_indices(config_manager) -> list[str]:
    for rule in config_manager.get_config_value("trade.rules", []):
        if rule.get("rule_type") == "allowed_indices":
            return list(rule.get("rule_config", {}).get("indice_ids", {}))
    return []


def _watchlist_entry(indice: str, direction: str, payload: dict | None) -> dict:
    entry = {"indice": indice, "direction": direction, "available": payload is not None}
    if payload is None:
        return entry
    instrument = payload.get("selected_instrument") or {}
    watchlist = payload.get("watchlist") or {}
    return {
        **entry,
        "description": instrument.get("description"),
        "symbol": instrument.get("symbol"),
        "currency": instrument.get("currency"),
        "latest_bid": instrument.get("latest_bid"),
        "latest_ask": instrument.get("latest_ask"),
        "price_updated_at": watchlist.get("price_last_updated_at"),
        "search_refreshed_at": watchlist.get("search_refreshed_at"),
        "stale": watchlist.get("stale"),
    }


@router.get("/watchlist")
async def watchlist(request: Request):
    """The turbo the trader would buy now, for each allowed indice and direction."""
    ctx = request.app.state.ctx
    if not ctx.services.watchlist_enabled:
        return {"enabled": False, "items": []}

    targets = [
        (indice, direction)
        for indice in _allowed_indices(ctx.config_manager)
        for direction in ctx.services.watchlist_directions
    ]
    try:
        payloads = await asyncio.gather(*(ctx.services.watchlist_best(*target) for target in targets))
    except ServiceUnavailable as e:
        return {"enabled": True, "items": [], "error": str(e)}
    return {
        "enabled": True,
        "items": [_watchlist_entry(indice, direction, payload) for (indice, direction), payload in zip(targets, payloads)],
    }


@router.get("/execution-timing")
async def execution_timing(request: Request, days: int = Query(30, ge=1, le=365)):
    """How long the trader took over each step of its recent trades."""
    return await run_in_threadpool(read_execution_timings, request.app.state.ctx.settings.log_path, days)


@router.get("/audit")
async def audit(request: Request, limit: int = Query(200, ge=1, le=1000)):
    """What was done through this interface, newest first."""
    audit_log = request.app.state.ctx.audit_log
    if audit_log is None:
        return {"items": []}
    return {"items": await run_in_threadpool(audit_log.read, limit)}


@router.get("/events")
async def events(request: Request):
    """
    Server-sent events: what changed (a position opened or closed, a service
    changed state...), so the interface can refresh and notify at once.
    """
    ctx = request.app.state.ctx
    token = request.cookies.get(session_cookie_name(ctx.settings.cookie_secure))
    queue = ctx.events.subscribe()

    async def stream():
        try:
            # Reconnection delay asked of the browser, then one frame so it knows the stream is open.
            yield "retry: 5000\n\n"
            yield "event: connected\ndata: {}\n\n"
            quiet_since = time.monotonic()
            # The server is not told when a browser leaves, and writing to one
            # that left does not fail: asked here, or the stream would stay
            # subscribed, and the watcher at work, until the session ends.
            while ctx.sessions.is_alive(token, ctx.credentials.version()) and not await request.is_disconnected():
                try:
                    event = await asyncio.wait_for(queue.get(), EVENT_POLL_SECONDS)
                except asyncio.TimeoutError:
                    if time.monotonic() - quiet_since >= EVENT_KEEP_ALIVE_SECONDS:
                        quiet_since = time.monotonic()
                        yield ": keep-alive\n\n"
                    continue
                quiet_since = time.monotonic()
                data = json.dumps(jsonable_encoder(event))
                yield f"event: {event['type']}\ndata: {data}\n\n"
        finally:
            ctx.events.unsubscribe(queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        # Not to be buffered by a proxy on the way.
        headers={"X-Accel-Buffering": "no"},
    )


@router.get("/logs")
async def log_services():
    return {
        "services": [{"id": service, "name": name} for service, name in LOG_SERVICES.items()],
        "levels": list(LEVELS),
    }


@router.get("/logs/{service}")
async def log_tail(
    service: str,
    request: Request,
    lines: int = Query(200, ge=1, le=MAX_LINES),
    level: str | None = Query(None, pattern=f"^({'|'.join(LEVELS)})$"),
    q: str | None = Query(None, max_length=200),
):
    # Only the known log files can be read: the name never reaches the filesystem otherwise.
    if service not in LOG_SERVICES:
        raise HTTPException(status_code=404, detail="Unknown service")
    return await run_in_threadpool(
        read_log,
        request.app.state.ctx.settings.log_path,
        service,
        lines,
        level,
        q,
    )
