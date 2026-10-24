from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from src.admin.auth import client_ip, require_session
from src.admin.config_catalog import CATALOG, SERVICES, unknown_paths
from src.admin.config_editor import BackupNotFound, ConfigConflict, ConfigInvalid
from src.admin.routers.session import restart_required

# Changed paths named in an audit entry (and so on Telegram) before the rest are counted.
NAMED_CHANGES = 8
SERVICE_NAMES = {service.id: service.name for service in SERVICES}

router = APIRouter(prefix="/api/config", dependencies=[Depends(require_session)])


class ConfigRequest(BaseModel):
    config: dict


class ConfigSaveRequest(ConfigRequest):
    revision: str


class ConfigRestoreRequest(BaseModel):
    revision: str


def require_config_edit_enabled(request: Request) -> None:
    if not request.app.state.ctx.settings.config_edit_enabled:
        raise HTTPException(status_code=403, detail="Configuration editing is disabled in the configuration")


def changes_said(saved: dict) -> str:
    """What an audit entry says of a save: the keys changed, and where they apply."""
    paths = [change["path"] for change in saved.get("changes", [])]
    if not paths:
        return "Nothing changed."
    named = ", ".join(paths[:NAMED_CHANGES]) + (f" and {len(paths) - NAMED_CHANGES} more" if len(paths) > NAMED_CHANGES else "")
    services = [SERVICE_NAMES[service] for service in saved.get("impact", {}).get("services", [])]
    applies = f" Applies at the next start of: {', '.join(services)}." if services else ""
    return f"Changed {named}.{applies}"


@router.get("/catalog")
async def get_catalog():
    """Every parameter of config.json: what it does, who reads it, what it changes. Fixed for a version."""
    return CATALOG.as_dict()


@router.get("/history")
async def get_history(request: Request, limit: int = 100):
    """The saves written down, newest first, each with the keys it changed (values masked)."""
    limit = min(max(limit, 1), 1000)
    return {"items": await run_in_threadpool(request.app.state.ctx.config_editor.history, limit)}


@router.get("")
async def get_config(request: Request):
    ctx = request.app.state.ctx
    loaded = await run_in_threadpool(ctx.config_editor.load)
    return {
        **loaded,
        # Keys of the file the catalog does not know: a typo or a leftover, which nothing reads.
        "unknown": unknown_paths(loaded["config"]),
        "editable": ctx.settings.config_edit_enabled,
        "restart_required": await restart_required(ctx),
        "modified_at": datetime.fromtimestamp(ctx.config_editor.modified_at(), tz=timezone.utc),
    }


@router.post("/validate", dependencies=[Depends(require_config_edit_enabled)])
async def validate_config(body: ConfigRequest, request: Request):
    """Checked as a save would be; when it passes, what saving it would change and where (nothing is written)."""
    try:
        preview = await run_in_threadpool(request.app.state.ctx.config_editor.preview, body.config)
    except ConfigInvalid as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"valid": True, **preview}


@router.put("", dependencies=[Depends(require_config_edit_enabled)])
async def save_config(body: ConfigSaveRequest, request: Request):
    ctx = request.app.state.ctx
    by = client_ip(request, ctx.trusted_proxies)
    try:
        saved = await run_in_threadpool(ctx.config_editor.save, body.config, body.revision, by)
    except ConfigInvalid as e:
        raise HTTPException(status_code=422, detail=str(e))
    except ConfigConflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    ctx.audit(request, "configuration saved", f"{changes_said(saved)} Previous version kept as {saved['backup']}.")
    return {**saved, "restart_required": bool(saved["impact"]["services"])}


@router.get("/backups")
async def list_backups(request: Request):
    """The previous versions kept by each save, newest first."""
    return {"items": await run_in_threadpool(request.app.state.ctx.config_editor.list_backups)}


@router.get("/backups/{name}")
async def get_backup(name: str, request: Request):
    try:
        return await run_in_threadpool(request.app.state.ctx.config_editor.load_backup, name)
    except BackupNotFound:
        raise HTTPException(status_code=404, detail="Backup not found")


@router.post("/backups/{name}/restore", dependencies=[Depends(require_config_edit_enabled)])
async def restore_backup(name: str, body: ConfigRestoreRequest, request: Request):
    ctx = request.app.state.ctx
    by = client_ip(request, ctx.trusted_proxies)
    try:
        restored = await run_in_threadpool(ctx.config_editor.restore_backup, name, body.revision, by)
    except BackupNotFound:
        raise HTTPException(status_code=404, detail="Backup not found")
    except ConfigInvalid as e:
        raise HTTPException(status_code=422, detail=str(e))
    except ConfigConflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    ctx.audit(
        request,
        "configuration restored",
        f"From {restored['restored']}; the replaced version is kept as {restored['backup']}. {changes_said(restored)}",
    )
    return {**restored, "restart_required": bool(restored["impact"]["services"])}
