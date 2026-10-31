"""
Operator actions. Every one of them acts on the live trading system, is
recorded in the audit log and announced on Telegram, and can be switched off
as a whole with ``admin.actions_enabled``.
"""

import logging
from datetime import datetime

import jsonschema
import pytz
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from src.admin import proposed_actions
from src.admin.agent.proposals import CLOSE, PAUSE, RESUME
from src.admin.auth import client_ip, require_session
from src.admin.errors import DatabaseUnavailable
from src.admin.services import OPS_QUEUE
from src.schema import SchemaLoader

logger = logging.getLogger(__name__)

OPEN_ACTIONS = {"long", "short"}
CLOSE_ACTIONS = {"close-long", "close-short", "close-position"}
# Close signals apply to every managed position, whatever the indice (as the scheduler's do).
NO_INDICE = "n/a"


def require_actions_enabled(request: Request) -> None:
    if not request.app.state.ctx.settings.actions_enabled:
        raise HTTPException(status_code=403, detail="Operator actions are disabled in the configuration")


router = APIRouter(prefix="/api", dependencies=[Depends(require_session), Depends(require_actions_enabled)])


class SignalRequest(BaseModel):
    action: str
    indice: str | None = None
    confidence: float | None = Field(None, ge=0, le=2.0)


class SaxoAuthCodeRequest(BaseModel):
    code: str = Field(min_length=1, max_length=2048)
    # The state of the link the code came back from, taken from the address pasted: checked when given.
    state: str | None = Field(None, max_length=256)


class SaxoAuthorizeRequest(BaseModel):
    environment: str | None = Field(None, max_length=16)


class ProposedBy(BaseModel):
    # The AI agent's proposal this action applies, from its card (src/admin/proposed_actions.py).
    proposal: str | None = Field(None, max_length=proposed_actions.MAX_REF)


class ClosePositionRequest(ProposedBy):
    position_id: str = Field(min_length=1, max_length=64)


def _allowed_indices(config_manager) -> dict:
    for rule in config_manager.get_config_value("trade.rules", []):
        if rule.get("rule_type") == "allowed_indices":
            return rule.get("rule_config", {}).get("indice_ids", {})
    return {}


def _utc_timestamp() -> str:
    return datetime.now(pytz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@router.post("/actions/signal")
async def send_signal(body: SignalRequest, request: Request):
    """
    Send a signal to the trader through web_server, exactly as a webhook would.
    The trader applies its usual rules to it (market hours, limits, cooldown...).
    """
    ctx = request.app.state.ctx

    if body.action in CLOSE_ACTIONS:
        indice = NO_INDICE
    elif body.action in OPEN_ACTIONS:
        indice = body.indice
        if indice not in _allowed_indices(ctx.config_manager):
            raise HTTPException(status_code=422, detail=f"Indice '{indice}' is not in the allowed indices")
    else:
        raise HTTPException(status_code=422, detail=f"Unsupported action '{body.action}'")

    now = _utc_timestamp()
    payload = {"action": body.action, "indice": indice, "signal_timestamp": now, "alert_timestamp": now}
    if body.confidence is not None and body.action in OPEN_ACTIONS:
        payload["confidence"] = body.confidence
    jsonschema.validate(instance=payload, schema=SchemaLoader.get_webhook_schema())

    signal_uuid = await ctx.services.send_signal(payload)
    ctx.audit(request, f"manual signal {body.action}", f"indice={indice} signal_uuid={signal_uuid}")
    return {"signal_uuid": signal_uuid, "signal": payload}


def _risk_state(ctx):
    if ctx.risk_state is None:
        raise HTTPException(status_code=503, detail="The pause of new entries is not available")
    return ctx.risk_state


@router.post("/actions/pause-entries")
async def pause_entries(request: Request, body: ProposedBy | None = None):
    """
    Make the trader refuse every signal that would open a position. Closing
    signals, the end-of-day close and the position monitor's exits still work.
    """
    ctx = request.app.state.ctx
    found = await proposed_actions.pending(ctx, body.proposal if body else None, PAUSE)
    try:
        await _risk_state(ctx).pause_entries(datetime.now(pytz.utc))
        paused_since = await _risk_state(ctx).get_entries_paused_since()
    except DatabaseUnavailable:
        raise HTTPException(status_code=503, detail="The database is unavailable: the pause was NOT set")
    ctx.audit(request, "new entries paused", proposed_actions.said(found))
    ctx.events.publish("entries_paused")
    answer = {"entries_paused_since": paused_since}
    await proposed_actions.settled(ctx, found, client_ip(request, ctx.trusted_proxies), answer)
    return answer


@router.post("/actions/resume-entries")
async def resume_entries(request: Request, body: ProposedBy | None = None):
    ctx = request.app.state.ctx
    found = await proposed_actions.pending(ctx, body.proposal if body else None, RESUME)
    try:
        await _risk_state(ctx).resume_entries()
    except DatabaseUnavailable:
        raise HTTPException(status_code=503, detail="The database is unavailable: entries are still paused")
    ctx.audit(request, "new entries resumed", proposed_actions.said(found))
    ctx.events.publish("entries_resumed")
    answer = {"entries_paused_since": None}
    await proposed_actions.settled(ctx, found, client_ip(request, ctx.trusted_proxies), answer)
    return answer


@router.post("/actions/close-position", status_code=202)
async def close_position(body: ClosePositionRequest, request: Request):
    """
    Ask the position monitor to close one position. It is done by the monitor,
    which owns the exits and knows which closes are already in flight.
    """
    ctx = request.app.state.ctx
    found = await proposed_actions.pending(ctx, body.proposal, CLOSE)
    if found and found["proposal"]["action"].get("position_id") != body.position_id:
        raise HTTPException(status_code=422, detail="The proposal closes another position")
    detail = await ctx.queries.get_position(body.position_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Position not found")
    if detail["position"].get("position_status") != "Open":
        raise HTTPException(status_code=409, detail="This position is not open")

    # The request only lives a minute in the queue: without a consumer it would be lost silently.
    queues = {queue["name"]: queue for queue in await ctx.services.queue_stats()}
    if queues.get(OPS_QUEUE, {}).get("consumers", 0) < 1:
        raise HTTPException(status_code=503, detail="The position monitor is not running: nothing was sent")

    await ctx.services.publish_ops({
        "action": "close_position",
        "position_id": body.position_id,
        "requested_at": _utc_timestamp(),
    })
    instrument = detail["position"].get("instrument_name") or "unknown instrument"
    ctx.audit(request, "close of one position requested",
              proposed_actions.joined(f"position_id={body.position_id} ({instrument})", proposed_actions.said(found)))
    answer = {"status": "queued", "position_id": body.position_id}
    await proposed_actions.settled(ctx, found, client_ip(request, ctx.trusted_proxies), answer)
    return answer


@router.post("/actions/daily-stats", status_code=202)
async def trigger_daily_stats(request: Request):
    """Ask for the daily statistics report now (the scheduler sends the same message at 22:00)."""
    ctx = request.app.state.ctx
    now = _utc_timestamp()
    await ctx.services.publish_ops({
        "action": "daily_stats",
        "indice": NO_INDICE,
        "signal_timestamp": now,
        "alert_timestamp": now,
        "mqsend_timestamp": now,
    })
    ctx.audit(request, "daily stats requested")
    return {"status": "queued"}


@router.post("/system/saxo/authorize")
async def authorize_saxo(body: SaxoAuthorizeRequest, request: Request):
    """Saxo's login page, with a state of its own kept hashed for ten minutes: opened by the operator."""
    url = await request.app.state.ctx.saxo.authorize(body.environment)
    return {"url": url}


@router.post("/system/saxo/auth-code", status_code=204)
async def submit_saxo_auth_code(body: SaxoAuthCodeRequest, request: Request):
    """
    Hand the Saxo authorization code over, sealed for the services that log in
    to Saxo, one of which takes it within seconds (what watasaxoauth does on the
    server). With its state, from the address pasted, the state is checked.
    """
    ctx = request.app.state.ctx
    environment = await ctx.saxo.hand_over(body.code, state=body.state, by=client_ip(request, ctx.trusted_proxies))
    ctx.audit(request, "Saxo authorization code submitted", f"{environment} account")
    return Response(status_code=204)
