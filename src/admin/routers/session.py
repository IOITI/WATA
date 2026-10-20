import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from src.admin.auth import check_same_origin, client_ip, require_session, session_cookie_name

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/session")


class LoginRequest(BaseModel):
    password: str
    # The one-time code of the authenticator app, when the second factor is on.
    code: str | None = None


def restart_required(ctx) -> bool:
    """
    Whether config.json was saved after the services last started. They all
    start together (docker compose up), so the admin's own start time stands for
    theirs.
    """
    try:
        return ctx.config_editor.modified_at() > ctx.started_at
    except OSError:
        return False


@router.post("")
async def login(body: LoginRequest, request: Request):
    ctx = request.app.state.ctx
    check_same_origin(request)
    ip = client_ip(request, ctx.trusted_proxies)

    retry_after = ctx.throttle.retry_after(ip)
    if retry_after:
        return JSONResponse(
            content={"detail": "Too many failed attempts", "retry_after": retry_after},
            status_code=429,
            headers={"Retry-After": str(retry_after)},
        )

    if not ctx.credentials.is_configured():
        logger.warning(f"Login attempt from {ip} while no admin password is set")
        raise HTTPException(status_code=503, detail="No admin password is set. Run wataadminpasswd on the server.")

    totp_enabled = ctx.credentials.totp_enabled()
    # scrypt is deliberately slow: keep it off the event loop.
    authenticated = await run_in_threadpool(ctx.credentials.verify, body.password)
    if authenticated and totp_enabled:
        # Only looked at once the password is right: a code is consumed when accepted.
        authenticated = body.code is not None and ctx.credentials.verify_totp(body.code)
    if not authenticated:
        ctx.throttle.record_failure(ip)
        logger.warning(f"Failed admin login from {ip}")
        if ctx.throttle.retry_after(ip):
            ctx.audit(request, "login locked", "Too many failed attempts")
        # The same answer whichever of the two was wrong.
        raise HTTPException(status_code=401, detail="Invalid password or code" if totp_enabled else "Invalid password")

    ctx.throttle.record_success(ip)
    response = Response(status_code=204)
    response.set_cookie(
        session_cookie_name(ctx.settings.cookie_secure),
        ctx.sessions.create(ctx.credentials.version()),
        max_age=ctx.settings.session_max_age_minutes * 60,
        httponly=True,
        secure=ctx.settings.cookie_secure,
        samesite="strict",
        path="/",
    )
    ctx.audit(request, "login")
    return response


@router.get("", dependencies=[Depends(require_session)])
async def session_info(request: Request):
    ctx = request.app.state.ctx
    return {
        "authenticated": True,
        "version": ctx.version,
        "timezone": ctx.settings.timezone,
        "actions_enabled": ctx.settings.actions_enabled,
        "config_edit_enabled": ctx.settings.config_edit_enabled,
        # The switch of the backtests and of the downloads of history alike.
        "backtests_enabled": ctx.settings.backtests_enabled and (ctx.backtests is not None or ctx.downloads is not None),
        "totp_enabled": ctx.credentials.totp_enabled(),
        "restart_required": restart_required(ctx),
    }


@router.delete("", dependencies=[Depends(require_session)])
async def logout(request: Request):
    ctx = request.app.state.ctx
    cookie_name = session_cookie_name(ctx.settings.cookie_secure)
    ctx.sessions.revoke(request.cookies[cookie_name])
    response = Response(status_code=204)
    # With the attributes it was set with, or a browser ignores the removal of a __Host- cookie.
    response.delete_cookie(
        cookie_name, path="/", secure=ctx.settings.cookie_secure, httponly=True, samesite="strict",
    )
    return response
