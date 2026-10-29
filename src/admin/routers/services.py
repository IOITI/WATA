"""
WATA's services as the control service sees them (src/service_control/), with
the saved changes each does not run with yet, and their restarts: one, or
several in order (src/admin/service_restart.py). Reading is the session's; a
restart is an operator action, audited and announced, refused while something
holds it back unless forced.
"""

from dataclasses import asdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from src.admin.auth import client_ip, require_session
from src.admin.errors import ServiceUnavailable
from src.admin.routers.actions import require_actions_enabled
from src.admin.service_restart import (
    COSTS,
    ORDER,
    SERVICE_NAMES,
    TRADING_PATH,
    Readiness,
    RestartPlans,
    journal_now,
    moment,
    ordered,
    pending_changes,
    refusal,
    situation_of,
)

router = APIRouter(prefix="/api/system/services", dependencies=[Depends(require_session)])

# What the control service says of a container, passed on as it is.
_STATE_KEYS = (
    "container", "state", "running", "started_at", "finished_at", "exit_code", "oom_killed", "restart_count", "image",
    "restarting", "last_restart",
)
NO_CONTROL = "No control service runs beside this admin: restart the services on the server (watastart)"


class RestartRequest(BaseModel):
    services: list[str] = Field(min_length=1, max_length=len(ORDER))
    force: bool = False


class OneRestart(BaseModel):
    force: bool = False


def plans(ctx) -> RestartPlans | None:
    """The restarts, made the first time they are asked for; none without a control service."""
    if ctx.service_control is None or not ctx.service_control.available:
        return None
    if ctx.restarts is None:
        ctx.restarts = RestartPlans(ctx.service_control, Readiness(ctx), lambda: situation_of(ctx), ctx.events,
                                    notify=ctx.notifier)
        ctx.closers.append(ctx.restarts.close)
    return ctx.restarts


@router.get("")
async def services(request: Request):
    """
    Each service: its container as Docker has it, the saved changes it does
    not run with, what holds its restart back now and what a restart costs.
    Without the control service, the admin's own start stands for every
    service's, as before it.
    """
    ctx = request.app.state.ctx
    found: dict[str, dict] = {}
    error = None
    if ctx.service_control is None or not ctx.service_control.available:
        error = NO_CONTROL
    else:
        try:
            found = {entry["service"]: entry for entry in await ctx.service_control.services()}
        except ServiceUnavailable as e:
            error = f"The control service cannot be reached ({e})"
    situation = await situation_of(ctx)
    history, current, modified_at = await run_in_threadpool(journal_now, ctx)
    admin_start = datetime.fromtimestamp(ctx.started_at, timezone.utc)
    items = []
    for service in ORDER:
        state = found.get(service) or {}
        if found:
            started = moment(state.get("started_at")) if state.get("running") else None
        else:
            started = admin_start
        items.append({
            "id": service,
            "name": SERVICE_NAMES[service],
            "trading_path": service in TRADING_PATH,
            **{key: state.get(key) for key in _STATE_KEYS},
            "pending": pending_changes(service, started, history, current, modified_at),
            "refusal": refusal(service, situation),
            "cost": COSTS[service],
        })
    restarts = ctx.restarts
    return {
        "available": error is None,
        "error": error,
        # Without the control service, the changes are counted from the admin's start.
        "starts_known": bool(found),
        "actions_enabled": ctx.settings.actions_enabled,
        "services": items,
        "plan": restarts.plan.as_dict() if restarts is not None and restarts.plan is not None else None,
        "situation": {**asdict(situation), "close_intents": list(situation.close_intents)},
    }


async def _start(request: Request, services: list[str], force: bool) -> dict:
    ctx = request.app.state.ctx
    try:
        wanted = ordered(services)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    restarts = plans(ctx)
    if restarts is None:
        raise HTTPException(status_code=503, detail=NO_CONTROL)
    if restarts.busy:
        raise HTTPException(status_code=409, detail="A restart is already under way: wait for it to end")
    if ctx.release is not None and await run_in_threadpool(ctx.release.applying):
        raise HTTPException(status_code=409, detail="A deploy is applying a release: wait for it to end")
    situation = await situation_of(ctx)
    held = {service: reason for service in wanted if (reason := refusal(service, situation))}
    said = "; ".join(f"{SERVICE_NAMES[service]}: {reason}" for service, reason in held.items())
    if held and not force:
        raise HTTPException(
            status_code=409, detail=f"Held back: {said}. Restart anyway to go ahead all the same: that is recorded",
        )
    plan = restarts.start(wanted, forced=force, by=client_ip(request, ctx.trusted_proxies))
    action = f"restart {', '.join(SERVICE_NAMES[service] for service in plan.services)}"
    if held:
        action += f" ANYWAY ({said})"
    ctx.audit(request, action, "The admin restarts last: its sessions end" if "admin" in plan.services else "")
    return {"plan": plan.as_dict()}


@router.post("/restart", status_code=202, dependencies=[Depends(require_actions_enabled)])
async def restart_services(body: RestartRequest, request: Request):
    """Restart ``services`` one after the other, each once the one before works again; the admin last."""
    return await _start(request, body.services, body.force)


@router.post("/{service}/restart", status_code=202, dependencies=[Depends(require_actions_enabled)])
async def restart_service(service: str, body: OneRestart, request: Request):
    if service not in SERVICE_NAMES:
        raise HTTPException(status_code=404, detail=f"{service} is not one of WATA's services")
    return await _start(request, [service], body.force)
