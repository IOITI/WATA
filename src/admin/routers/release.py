"""
The release each container runs, what waits for the newest one and why, and
the operator's request to apply it (src/admin/release.py). Reading is the
session's; asking is an operator action, audited and announced, refused while
something holds a container back unless forced. watadeploy reads the guards
again on the server before each container, whatever was asked.
"""

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from src.admin.auth import client_ip, require_session
from src.admin.release import ReleaseRefused
from src.admin.routers.actions import require_actions_enabled
from src.admin.service_restart import situation_of
from src.release.guards import ORDER, deploy_refusal

router = APIRouter(prefix="/api/system/release", dependencies=[Depends(require_session)])

NO_RELEASE = "watadeploy has said nothing yet on this server: run watadeploy status there"


class ApplyRequest(BaseModel):
    containers: list[str] = Field(min_length=1, max_length=20)
    force: bool = False


def _release(request: Request):
    release = request.app.state.ctx.release
    if release is None:
        raise HTTPException(status_code=503, detail=NO_RELEASE)
    return release


async def held_now(ctx, status: dict) -> dict[str, str]:
    """What holds each waiting container back now: what the AI agent reads of a deploy (src/admin/agent_ops.py)."""
    return (await _held(ctx, status))[0]


async def _held(ctx, status: dict) -> tuple[dict[str, str], dict]:
    """What holds each waiting container back now, as the admin sees it (watadeploy reads it again)."""
    situation = await situation_of(ctx)
    held = {}
    for name, entry in ctx.release.waiting(status).items():
        role = entry.get("role")
        reason = deploy_refusal(role, situation) if role in ORDER else None
        if reason:
            held[name] = reason
    return held, situation


@router.get("")
async def release(request: Request):
    """The newest release installed, each container's, what waits and why, what holds it back, the last apply."""
    ctx = request.app.state.ctx
    found = _release(request)
    status = await run_in_threadpool(found.status)
    held, situation = await _held(ctx, status) if status else ({}, None)
    return {
        "available": bool(status),
        "error": None if status else NO_RELEASE,
        "actions_enabled": ctx.settings.actions_enabled,
        "status": status,
        "held": held,
        "applying": found.applying(status) if status else None,
        "situation": {**asdict(situation), "close_intents": list(situation.close_intents)} if situation else None,
    }


@router.post("/apply", status_code=202, dependencies=[Depends(require_actions_enabled)])
async def apply(body: ApplyRequest, request: Request):
    """Ask the server to apply the newest release to ``containers``: the trading path at the close, unless forced."""
    ctx = request.app.state.ctx
    found = _release(request)
    status = await run_in_threadpool(found.status)
    held, _ = await _held(ctx, status) if status else ({}, None)
    said = "; ".join(f"{name}: {reason}" for name, reason in held.items() if name in body.containers)
    if said and not body.force:
        raise HTTPException(status_code=409,
                            detail=f"Held back: {said}. Apply anyway to go ahead all the same: that is recorded")
    by = client_ip(request, ctx.trusted_proxies)
    try:
        asked = await run_in_threadpool(found.ask, body.containers, body.force, f"the System page, {by}")
    except ReleaseRefused as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    action = f"apply {asked['version']} to {', '.join(asked['containers'])}"
    if said:
        action += f" ANYWAY ({said})"
    ctx.audit(request, action, "The admin goes last: its sessions end" if "admin1" in asked["containers"] else "")
    return {"request": asked}
