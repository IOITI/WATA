"""
The admin FastAPI application: JSON API under /api and, when it has been built,
the React interface (admin_ui/, built into src/admin/static/) on every other path.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import MutableHeaders

from src.admin.backtests import InvalidRequest, NotPossible, UnknownRun
from src.admin.context import AdminContext
from src.admin.data import AssetNotFound, InvalidAsset
from src.admin.errors import DatabaseUnavailable, ServiceUnavailable
from src.admin.events import StateWatcher
from src.admin.routers import actions, backtests, config, data, session, system, trading

logger = logging.getLogger(__name__)

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# How long a stop request waits for the requests in progress. A page left open
# holds a live-events stream that would otherwise keep the server from stopping.
SHUTDOWN_GRACE_SECONDS = 5

# The interface loads nothing from other origins. Inline styles are allowed
# because the animation library sets them on elements.
CONTENT_SECURITY_POLICY = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data:",
    "font-src 'self'",
    "connect-src 'self'",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
])


class SecurityHeadersMiddleware:
    """
    Adds the security headers to every response and answers 404 to a path no
    file system accepts (a NUL byte in it), before the static files are looked
    up with it and fail on it.

    A plain ASGI middleware on purpose: the kind that wraps the response
    (``@app.middleware("http")``) stands between a streamed response and its
    client, and the live-events stream would not see its browser leave.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope["path"]

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
                headers["X-Frame-Options"] = "DENY"
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Referrer-Policy"] = "no-referrer"
                if path.startswith("/api/"):
                    headers["Cache-Control"] = "no-store"
            await send(message)

        if "\x00" in path:
            not_found = JSONResponse(content={"detail": "Not found"}, status_code=404)
            await not_found(scope, receive, send_with_headers)
            return
        await self.app(scope, receive, send_with_headers)


def _mount_interface(app: FastAPI, static_dir: str) -> None:
    index_path = os.path.join(static_dir, "index.html")
    if not os.path.exists(index_path):
        logger.warning(f"Admin interface not built ({index_path} is missing): serving the API only")
        return

    assets_dir = os.path.join(static_dir, "assets")
    if os.path.isdir(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    static_root = os.path.realpath(static_dir)

    @app.get("/{path:path}", include_in_schema=False)
    async def interface(path: str):
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")
        # A file of the build (favicon...) is served as such; any other path is a
        # route of the single-page application.
        candidate = os.path.realpath(os.path.join(static_root, path))
        if path and candidate.startswith(static_root + os.sep) and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(index_path)


def create_app(ctx: AdminContext, static_dir: str = STATIC_DIR) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        background = [
            # Looks for changes to announce to the browsers listening on /api/events.
            asyncio.create_task(StateWatcher(ctx, ctx.events).run()),
            # Keeps the address of the reverse proxy up to date.
            asyncio.create_task(ctx.trusted_proxies.keep_refreshed()),
        ]
        if ctx.backtests is not None and ctx.settings.backtests_enabled:
            # Runs the backtests asked for, one at a time.
            background.append(asyncio.create_task(ctx.backtests.run()))
            # Fetches the prices that are missing, when it was told to and the market is closed.
            background.append(asyncio.create_task(ctx.backtests.keep_prices()))
        try:
            yield
        finally:
            for task in background:
                task.cancel()
            await ctx.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.ctx = ctx

    app.add_middleware(SecurityHeadersMiddleware)

    @app.exception_handler(DatabaseUnavailable)
    async def database_unavailable(_: Request, __: DatabaseUnavailable):
        return JSONResponse(content={"detail": "The database is unavailable"}, status_code=503)

    @app.exception_handler(ServiceUnavailable)
    async def service_unavailable(_: Request, exc: ServiceUnavailable):
        logger.warning(f"Upstream service unavailable: {exc}")
        return JSONResponse(content={"detail": f"Service unavailable ({exc})"}, status_code=502)

    @app.exception_handler(UnknownRun)
    async def unknown_run(_: Request, __: UnknownRun):
        return JSONResponse(content={"detail": "No such backtest"}, status_code=404)

    @app.exception_handler(InvalidRequest)
    async def invalid_request(_: Request, exc: InvalidRequest):
        return JSONResponse(content={"detail": str(exc)}, status_code=422)

    @app.exception_handler(NotPossible)
    async def not_possible(_: Request, exc: NotPossible):
        return JSONResponse(content={"detail": str(exc)}, status_code=409)

    @app.exception_handler(AssetNotFound)
    async def asset_not_found(_: Request, exc: AssetNotFound):
        return JSONResponse(content={"detail": str(exc)}, status_code=404)

    @app.exception_handler(InvalidAsset)
    async def invalid_asset(_: Request, exc: InvalidAsset):
        return JSONResponse(content={"detail": str(exc)}, status_code=422)

    @app.get("/api/healthz", include_in_schema=False)
    async def healthz():
        # "totp" lets the login page ask for a one-time code without a failed attempt first.
        # Nothing else is said to a visitor who has not signed in, the version included.
        return {"service": "admin", "totp": ctx.credentials.totp_enabled()}

    for module in (session, trading, system, actions, config, data, backtests):
        app.include_router(module.router)

    _mount_interface(app, static_dir)
    return app
