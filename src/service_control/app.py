"""
The control service's API, which only the admin reaches (on a network of their
own, with a token): what each of WATA's containers is doing, and a restart of
one. One restart at a time, and the trading path's paced (containers.py).
"""

import asyncio
import hmac
import logging
import math
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Request

from src.service_control.containers import (
    CONTAINERS,
    MAX_RESTARTS_AN_HOUR,
    OTHER_MAX_RESTARTS_AN_HOUR,
    RESTART_GAP_SECONDS,
    TRADING_PATH,
)
from src.service_control.docker import Docker, DockerError, is_watas, state_of

logger = logging.getLogger(__name__)

HOUR_SECONDS = 3600


def restart_wait(service: str, past: deque, now: float) -> int:
    """How long ``service`` must wait before it is restarted again, in seconds (0: not at all)."""
    recent = [moment for moment in past if now - moment < HOUR_SECONDS]
    waits = []
    if service in TRADING_PATH:
        if recent and now - recent[-1] < RESTART_GAP_SECONDS:
            waits.append(RESTART_GAP_SECONDS - (now - recent[-1]))
        if len(recent) >= MAX_RESTARTS_AN_HOUR:
            waits.append(HOUR_SECONDS - (now - recent[-MAX_RESTARTS_AN_HOUR]))
    elif len(recent) >= OTHER_MAX_RESTARTS_AN_HOUR:
        waits.append(HOUR_SECONDS - (now - recent[-OTHER_MAX_RESTARTS_AN_HOUR]))
    return math.ceil(max(waits, default=0))


def create_app(docker: Docker, token: str, clock=time.monotonic) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app):
        yield
        await docker.close()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    restarts: dict[str, deque] = {service: deque(maxlen=OTHER_MAX_RESTARTS_AN_HOUR) for service in CONTAINERS}
    outcomes: dict[str, dict] = {}
    under_way: dict[str, asyncio.Task] = {}

    def authorised(request: Request) -> None:
        given = request.headers.get("authorization", "")
        if not token or not hmac.compare_digest(given.encode(), f"Bearer {token}".encode()):
            raise HTTPException(status_code=401, detail="Not authorised")

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.get("/services", dependencies=[Depends(authorised)])
    async def services():
        found = []
        for service, container in CONTAINERS.items():
            try:
                state = state_of(service, container, await docker.inspect(container))
            except (DockerError, OSError) as e:
                state = {"service": service, "container": container, "state": "unknown", "detail": str(e)}
            state["restarting"] = service in under_way
            state["last_restart"] = outcomes.get(service)
            found.append(state)
        return {"services": found}

    async def _restart(service: str, container: str, requested_at: str) -> None:
        try:
            await docker.restart(container)
            outcomes[service] = {"requested_at": requested_at, "done_at": _now(), "ok": True, "detail": None}
            logger.info("Restarted %s", container)
        except Exception as e:
            outcomes[service] = {"requested_at": requested_at, "done_at": _now(), "ok": False, "detail": str(e)}
            logger.error("Could not restart %s: %s", container, e)
        finally:
            under_way.pop(service, None)

    @app.post("/services/{service}/restart", status_code=202, dependencies=[Depends(authorised)])
    async def restart(service: str):
        container = CONTAINERS.get(service)
        if container is None:
            raise HTTPException(status_code=404, detail=f"{service} is not one of WATA's services")
        if under_way:
            raise HTTPException(status_code=409, detail=f"{', '.join(under_way)} is being restarted: one at a time")
        now = clock()
        wait = restart_wait(service, restarts[service], now)
        if wait:
            raise HTTPException(
                status_code=429, detail=f"{service} was restarted lately: again in {wait} s", headers={"Retry-After": str(wait)},
            )
        try:
            inspected = await docker.inspect(container)
        except (DockerError, OSError) as e:
            raise HTTPException(status_code=503, detail=f"Docker could not be asked: {e}")
        if inspected is None:
            raise HTTPException(status_code=404, detail=f"{container} is not created: its compose profile is off")
        if not is_watas(container, inspected):
            raise HTTPException(status_code=409, detail=f"{container} is not the container of WATA's compose file")
        restarts[service].append(now)
        requested_at = _now()
        under_way[service] = asyncio.create_task(_restart(service, container, requested_at))
        logger.info("Restart of %s asked", container)
        return {"service": service, "container": container, "requested_at": requested_at}

    return app


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
