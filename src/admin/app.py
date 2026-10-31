"""
The admin FastAPI application: JSON API under /api, the MCP endpoint for Claude
on /mcp when it is switched on (src/admin/mcp_server/), and, when it has been
built, the React interface (admin_ui/, built into src/admin/static/) on every
other path.
"""

import asyncio
import logging
import os
import re
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import Headers, MutableHeaders
from starlette.middleware.gzip import GZipMiddleware

from src.admin.agent.store import UnknownConversation
from src.admin.agent_memory import UnknownEntry
from src.admin.context import AdminContext
from src.admin.data import AssetNotFound, InvalidAsset
from src.admin.errors import DatabaseUnavailable, InvalidRequest, NotPossible, ServiceUnavailable, UnknownRun
from src.admin.events import StateWatcher
from src.admin.routers import (
    actions, agent, backtests, config, credentials, data, luxalgo, release, research, saxo, services, session, system,
    ta, trading,
)
from src.admin.ta_scripts import InvalidScript, ScriptConflict, ScriptNotFound

logger = logging.getLogger(__name__)

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# How long a stop request waits for the requests in progress. A page left open
# holds a live-events stream that would otherwise keep the server from stopping.
SHUTDOWN_GRACE_SECONDS = 5

# LuxAlgo's preview pictures of its indicators (the LuxAlgo Library's cards and an
# indicator's page), which the browser loads from LuxAlgo's image host: the one
# thing of another origin the interface loads (the operator's choice, 2026-10-31).
# Referrer-Policy keeps the admin's addresses out of those requests.
LUXALGO_IMAGES = "https://luxalgo-production.s3.amazonaws.com"

# The interface loads nothing else from other origins. Inline styles are allowed
# because the animation library sets them on elements.
CONTENT_SECURITY_POLICY = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    f"img-src 'self' data: {LUXALGO_IMAGES}",
    "font-src 'self'",
    "connect-src 'self'",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
])

# Pine scripts run as the JavaScript PineTS generates from them, which needs
# 'unsafe-eval'. Only the two worker files of the Technical Analysis pages get it,
# and they can open no connection (connect-src falls back to 'none'): a script that
# compiles can neither call the API with the session cookie nor send anything out.
# A worker loaded from its own address follows the policy of its own response.
WORKER_CONTENT_SECURITY_POLICY = "; ".join([
    "default-src 'none'",
    "script-src 'self' 'unsafe-eval'",
])
WORKER_PATH = re.compile(r"^/assets/(?:vela-)?pine-worker-[A-Za-z0-9_-]+\.js$")

# Answers this big or more go out compressed when the browser accepts it: a day of range bars is 0.8 MB of
# JSON, a sixth of that compressed. The smaller ones (the sign-in, a credential) go out as they are.
COMPRESS_FROM_BYTES = 4096
COMPRESS_LEVEL = 5


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
        policy = WORKER_CONTENT_SECURITY_POLICY if WORKER_PATH.match(path) else CONTENT_SECURITY_POLICY

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Content-Security-Policy"] = policy
                headers["X-Frame-Options"] = "DENY"
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Referrer-Policy"] = "no-referrer"
                if path.startswith("/api/") or path in ("/mcp", "/mcp/"):
                    headers["Cache-Control"] = "no-store"
            await send(message)

        if "\x00" in path:
            not_found = JSONResponse(content={"detail": "Not found"}, status_code=404)
            await not_found(scope, receive, send_with_headers)
            return
        await self.app(scope, receive, send_with_headers)


def _streamed(scope) -> bool:
    """A stream of events (asked for as such), or the MCP endpoint, whose answers can be streams."""
    path = scope["path"]
    if path == "/mcp" or path.startswith("/mcp/"):
        return True
    return "text/event-stream" in Headers(scope=scope).get("accept", "")


class CompressedAnswers:
    """
    gzip for the API's answers and the interface's files, when the browser
    accepts it. Never for a stream: the compressing middleware holds an answer's
    headers back until its first bytes, and a stream may say nothing for a
    while. A plain ASGI middleware, as SecurityHeadersMiddleware, for the same
    reason as it.
    """

    def __init__(self, app):
        self.app = app
        self.compressing = GZipMiddleware(app, minimum_size=COMPRESS_FROM_BYTES, compresslevel=COMPRESS_LEVEL)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or _streamed(scope):
            await self.app(scope, receive, send)
            return
        await self.compressing(scope, receive, send)


def _not_the_interface(path: str) -> bool:
    """Paths the single-page application never answers: the API, the MCP endpoint, and the
    well-known addresses a client reads to find an authorisation server (there is none)."""
    return any(path == prefix or path.startswith(f"{prefix}/") for prefix in ("api", "mcp", ".well-known"))


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
        if _not_the_interface(path):
            raise HTTPException(status_code=404, detail="Not found")
        # A file of the build (favicon...) is served as such; any other path is a
        # route of the single-page application.
        # Asked again at each load: kept by a browser, a page of before would ask for the
        # assets of its own build, which a deploy replaced. The assets' names change with their content.
        fresh = {"Cache-Control": "no-cache"}
        candidate = os.path.realpath(os.path.join(static_root, path))
        if path and candidate.startswith(static_root + os.sep) and os.path.isfile(candidate):
            return FileResponse(candidate, headers=fresh)
        return FileResponse(index_path, headers=fresh)


async def _load_own_credentials(ctx: AdminContext) -> None:
    """In the background: the admin starts, and answers, while PostgreSQL is down."""
    from src.vault import loader as vault

    try:
        await vault.load_waiting(ctx.config_manager, "admin", names={"telegram.bot"}, db=ctx.vault.reader,
                                 keys_dir=ctx.vault.keys_dir)
    except Exception as e:
        logger.warning("The admin's own credentials could not be read: %r", e)


def create_app(ctx: AdminContext, static_dir: str = STATIC_DIR) -> FastAPI:
    mcp = None
    if ctx.settings.mcp_enabled and ctx.mcp_tokens is not None:
        # The SDK is imported here and only here: with the endpoint off, the admin never loads it.
        from src.admin.mcp_server.server import build_endpoint

        mcp = build_endpoint(ctx, paused=ctx.mcp_pause.paused if ctx.mcp_pause is not None else (lambda: False))

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        background = [
            # Looks for changes to announce to the browsers listening on /api/events.
            asyncio.create_task(StateWatcher(ctx, ctx.events).run()),
            # Keeps the address of the reverse proxy up to date.
            asyncio.create_task(ctx.trusted_proxies.keep_refreshed()),
        ]
        if ctx.live_prices is not None:
            # Listens to the Capital.com feed's live prices while a chart follows them.
            background.append(asyncio.create_task(ctx.live_prices.run()))
        if ctx.backtests is not None and ctx.settings.backtests_enabled:
            # Runs the backtests asked for, one at a time.
            background.append(asyncio.create_task(ctx.backtests.run()))
        if ctx.downloads is not None and ctx.settings.backtests_enabled:
            # Fetches the prices that are missing, when it was told to and the market is closed.
            background.append(asyncio.create_task(ctx.downloads.keep_prices()))
        if ctx.research is not None and ctx.settings.backtests_enabled:
            # Writes each finished day for a research machine, when switched on and the market is closed.
            background.append(asyncio.create_task(ctx.research.keep()))
        if ctx.paper is not None and ctx.settings.backtests_enabled:
            # Trades each shadow rule's finished days on paper, the market closed.
            background.append(asyncio.create_task(ctx.paper.keep()))
        if ctx.mcp_tokens is not None and hasattr(ctx.mcp_tokens, "keep_fresh"):
            # The MCP endpoint's tokens, read again from PostgreSQL every few seconds for the gate.
            background.append(asyncio.create_task(ctx.mcp_tokens.keep_fresh()))
        if ctx.agent is not None:
            # The AI agent's requests, worked on whether a page follows them or not; those a stop left, taken up again.
            background.append(asyncio.create_task(ctx.agent.run()))
        if ctx.release is not None:
            # What the deploy did and what waits, said once each (src/admin/release.py).
            background.append(asyncio.create_task(ctx.release.keep(ctx)))
        if ctx.debrief is not None:
            # The agent's daily debrief, once the market is closed (while admin.ai_agent.debrief_enabled).
            background.append(asyncio.create_task(ctx.debrief.keep()))
        if ctx.teller is not None:
            # What the agent was asked to say on Telegram, once written (src/admin/agent_telegram.py).
            background.append(asyncio.create_task(ctx.teller.keep()))
        if ctx.briefing is not None:
            # The agent's morning briefing, before the trading start (while admin.ai_agent.briefing_enabled).
            background.append(asyncio.create_task(ctx.briefing.keep()))
        if ctx.triage is not None:
            # The agent's triage of the alerts as they fire: while it listens, the watcher looks without a page open.
            background.append(asyncio.create_task(ctx.triage.keep()))
        if ctx.dossiers is not None:
            # The agent's go-live dossier of a rule once ready, the market closed (while dossier_when_ready).
            background.append(asyncio.create_task(ctx.dossiers.keep()))
        if ctx.routines is not None:
            # The agent's routines, each at its turn, the market closed.
            background.append(asyncio.create_task(ctx.routines.keep()))
        if ctx.luxalgo_checks is not None and ctx.luxalgo_checks.available:
            # LuxAlgo's indicators checked for running on WATA, one at a time while no study runs.
            background.append(asyncio.create_task(ctx.luxalgo_checks.run()))
        if ctx.vault is not None:
            # Its own credentials laid over its configuration: the Telegram bot of its direct fallback.
            background.append(asyncio.create_task(_load_own_credentials(ctx)))
        try:
            async with AsyncExitStack() as running:
                if mcp is not None:
                    # A route's application gets no lifespan of its own: the SDK's runs within the admin's.
                    await running.enter_async_context(mcp.running())
                yield
        finally:
            for task in background:
                task.cancel()
            await ctx.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.ctx = ctx

    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(CompressedAnswers)

    @app.exception_handler(DatabaseUnavailable)
    async def database_unavailable(_: Request, __: DatabaseUnavailable):
        return JSONResponse(content={"detail": "The database is unavailable"}, status_code=503)

    @app.exception_handler(ServiceUnavailable)
    async def service_unavailable(_: Request, exc: ServiceUnavailable):
        logger.warning(f"Upstream service unavailable: {exc}")
        return JSONResponse(content={"detail": f"Service unavailable ({exc})"}, status_code=502)

    @app.exception_handler(UnknownConversation)
    async def unknown_conversation(_: Request, __: UnknownConversation):
        return JSONResponse(content={"detail": "No such conversation"}, status_code=404)

    @app.exception_handler(UnknownEntry)
    async def unknown_entry(_: Request, exc: UnknownEntry):
        return JSONResponse(content={"detail": str(exc)}, status_code=404)

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

    @app.exception_handler(ScriptNotFound)
    async def script_not_found(_: Request, exc: ScriptNotFound):
        return JSONResponse(content={"detail": str(exc)}, status_code=404)

    @app.exception_handler(InvalidScript)
    async def invalid_script(_: Request, exc: InvalidScript):
        return JSONResponse(content={"detail": str(exc)}, status_code=422)

    @app.exception_handler(ScriptConflict)
    async def script_conflict(_: Request, exc: ScriptConflict):
        return JSONResponse(content={"detail": str(exc)}, status_code=409)

    @app.get("/api/healthz", include_in_schema=False)
    async def healthz():
        # "totp" lets the login page ask for a one-time code without a failed attempt first.
        # Nothing else is said to a visitor who has not signed in, the version included.
        return {"service": "admin", "totp": ctx.credentials.totp_enabled()}

    for module in (session, saxo, trading, system, services, release, actions, config, credentials, data, backtests, ta,
                   research, agent, luxalgo):
        app.include_router(module.router)
    # LuxAlgo sends the browser back there from its own site: the state admits it (src/admin/routers/luxalgo.py).
    app.include_router(luxalgo.callback_router)

    if mcp is not None:
        # The gate is an ASGI application, so these routes take every method and the gate answers each.
        # Before the interface, whose catch-all would otherwise answer GET /mcp.
        app.add_route("/mcp", mcp.gate, include_in_schema=False)
        app.add_route("/mcp/", mcp.gate, include_in_schema=False)

    _mount_interface(app, static_dir)
    return app
