from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from src.admin.auth import require_session
from src.admin.config_editor import BackupNotFound, ConfigConflict, ConfigInvalid
from src.admin.routers.session import restart_required

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


@router.get("")
async def get_config(request: Request):
    ctx = request.app.state.ctx
    loaded = await run_in_threadpool(ctx.config_editor.load)
    return {
        **loaded,
        "editable": ctx.settings.config_edit_enabled,
        "restart_required": restart_required(ctx),
        "modified_at": datetime.fromtimestamp(ctx.config_editor.modified_at(), tz=timezone.utc),
    }


@router.post("/validate", dependencies=[Depends(require_config_edit_enabled)])
async def validate_config(body: ConfigRequest, request: Request):
    try:
        await run_in_threadpool(request.app.state.ctx.config_editor.validate, body.config)
    except ConfigInvalid as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"valid": True}


@router.put("", dependencies=[Depends(require_config_edit_enabled)])
async def save_config(body: ConfigSaveRequest, request: Request):
    ctx = request.app.state.ctx
    try:
        saved = await run_in_threadpool(ctx.config_editor.save, body.config, body.revision)
    except ConfigInvalid as e:
        raise HTTPException(status_code=422, detail=str(e))
    except ConfigConflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    ctx.audit(request, "configuration saved", f"Previous version kept as {saved['backup']}. Takes effect at the next start of each service.")
    return {**saved, "restart_required": True}


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
    try:
        restored = await run_in_threadpool(ctx.config_editor.restore_backup, name, body.revision)
    except BackupNotFound:
        raise HTTPException(status_code=404, detail="Backup not found")
    except ConfigInvalid as e:
        raise HTTPException(status_code=422, detail=str(e))
    except ConfigConflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    ctx.audit(
        request,
        "configuration restored",
        f"From {restored['restored']}; the replaced version is kept as {restored['backup']}. "
        "Takes effect at the next start of each service.",
    )
    return {**restored, "restart_required": True}
