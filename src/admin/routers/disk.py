"""
The Disk page (Operations › Disk): what the hourly samples of watadisk say of the server's disk, the projection
of where it goes under other retentions (only computed with, never saved), and the cleanups it may ask
(src/admin/disk_cleanups.py): an operator action each, the server's audited and announced.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from src.admin.auth import client_ip, require_session
from src.admin.disk_cleanups import SERVER
from src.admin.errors import InvalidRequest
from src.admin.routers.actions import require_actions_enabled

router = APIRouter(prefix="/api/system/disk", dependencies=[Depends(require_session)])


def _disk(request: Request):
    disk = getattr(request.app.state.ctx, "disk", None)
    if disk is None:
        raise HTTPException(status_code=404, detail="The disk is not watched by this admin")
    return disk


class ProjectionRequest(BaseModel):
    # Retention name -> value, over those set now (src/disk/projection.py RETENTIONS).
    retentions: dict[str, int] = Field(default_factory=dict)
    horizons: list[int] | None = None


@router.get("")
async def disk_overview(disk=Depends(_disk)):
    return await run_in_threadpool(disk.overview)


@router.post("/projection")
async def disk_projection(body: ProjectionRequest, disk=Depends(_disk)):
    try:
        return await run_in_threadpool(disk.projection, body.retentions, body.horizons)
    except ValueError as e:
        raise InvalidRequest(str(e)) from e


def _cleanups(request: Request):
    cleanups = getattr(request.app.state.ctx, "disk_cleanups", None)
    if cleanups is None:
        raise HTTPException(status_code=404, detail="No cleanup is offered by this admin")
    return cleanups


@router.get("/cleanups")
async def disk_cleanups(request: Request, cleanups=Depends(_cleanups)):
    """What each cleanup would give back, and whether it may be asked now."""
    return {"items": await run_in_threadpool(cleanups.offers),
            "actions_enabled": request.app.state.ctx.settings.actions_enabled}


@router.post("/cleanups/{action}", dependencies=[Depends(require_actions_enabled)])
async def disk_cleanup(action: str, request: Request, cleanups=Depends(_cleanups)):
    """Do a cleanup; the server's is asked of watadeploy, which says when it is done (the status, Telegram)."""
    ctx = request.app.state.ctx
    by = client_ip(request, ctx.trusted_proxies)
    done = await run_in_threadpool(cleanups.run, action, f"the Disk page, {by}")
    if action == SERVER:
        # The server's own folders and Docker: an operator action, in the audit trail and on Telegram.
        ctx.audit(request, "disk cleanup on the server", "Older releases, untagged images, Docker's build cache "
                                                         "no image holds, unused for a day")
    return done
