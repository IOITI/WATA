"""
LuxAlgo's Library on the Technical Analysis pages (src/admin/luxalgo.py, while
admin.luxalgo_enabled): its families, indicators and concepts read through the
admin's client, an indicator's source shown as LuxAlgo gives it, and taken as
one of the operator's scripts, whole, its origin kept beside it
(src/admin/script_origins.py). What WATA knows of each indicator
(src/admin/luxalgo_knowledge.py) and whether it runs on WATA
(src/admin/luxalgo_compat.py: a source read whole is checked once). A script
and a check change nothing that trades: no audit entry for them, as for the
other scripts.

The admin's sign-in to a LuxAlgo account (src/admin/luxalgo_auth.py), for whole
sources, is a credential: begun, pasted and ended with config_edit_enabled, and
audited. LuxAlgo sends the browser back to ``/api/luxalgo/callback`` from its
own site, which the session cookie (SameSite strict) never comes with: the
state admits it, made by a signed-in operator, kept hashed ten minutes, used
once, as Saxo's callback is admitted (src/admin/routers/saxo.py).
"""

import logging
import time
import urllib.parse

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from src.admin.auth import client_ip, require_session
from src.admin.errors import InvalidRequest, NotPossible
from src.admin.luxalgo import SLUG, LuxAlgoRefused, LuxAlgoUnknown, licence_of
from src.admin.luxalgo_knowledge import known as known_of
from src.admin.routers.config import require_config_edit_enabled
from src.admin.script_origins import luxalgo_origin
from src.admin.ta_scripts import MAX_NAME_LENGTH

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/luxalgo", dependencies=[Depends(require_session)])
callback_router = APIRouter(prefix="/api/luxalgo")

# Where the browser lands back: the LuxAlgo Library page, which says what came of it.
PAGE = "/ta/luxalgo"
# A page of the indicators' list, at most.
MAX_PAGE_SIZE = 50
FAMILIES = (
    "trend", "momentum", "volatility", "volume-orderflow", "market-structure", "smc-ict", "wyckoff",
    "elliott-harmonics", "patterns", "levels", "statistics", "machine-learning", "time-seasonality",
    "sentiment-breadth", "risk-exits", "meta-composition", "validation",
)


class ImportRequest(BaseModel):
    name: str | None = Field(default=None, max_length=MAX_NAME_LENGTH)


class PastedAddress(BaseModel):
    address: str = Field(max_length=4096)


class CompatAsked(BaseModel):
    slugs: list[str] = Field(min_length=1, max_length=50)


def _luxalgo(request: Request):
    ctx = request.app.state.ctx
    if ctx.luxalgo is None:
        raise NotPossible("LuxAlgo's tools are switched off (admin.luxalgo_enabled)")
    return ctx.luxalgo


def _slug(slug: str) -> str:
    if not SLUG.match(slug):
        raise InvalidRequest("A slug is lowercase letters, digits and dashes")
    return slug


def _family(family: str | None) -> str | None:
    if family and family not in FAMILIES:
        raise InvalidRequest(f"A family is one of {', '.join(FAMILIES)}")
    return family or None


async def _read(request: Request, tool: str, arguments: dict):
    try:
        return await _luxalgo(request).read(tool, {key: value for key, value in arguments.items() if value is not None})
    except LuxAlgoUnknown as e:
        # The pages' 404: their address names nothing LuxAlgo has.
        raise HTTPException(status_code=404, detail=str(e)) from None
    except LuxAlgoRefused as e:
        raise NotPossible(str(e)) from None


def _noticed(ctx, slug: str, found: dict) -> None:
    """A source read whole: checked for running on WATA, once per source."""
    if ctx.luxalgo_checks is not None and isinstance(found, dict) and found.get("complete") is not False:
        if found.get("source"):
            ctx.luxalgo_checks.noticed(slug, found["source"])


def _checks(request: Request):
    ctx = request.app.state.ctx
    _luxalgo(request)
    if ctx.luxalgo_checks is None or not ctx.luxalgo_checks.available:
        raise NotPossible("The checks need the Pine studies, which this admin does not run")
    return ctx.luxalgo_checks


@router.get("/status")
async def status(request: Request):
    """Whether LuxAlgo's tools are on, and the admin signed in to a LuxAlgo account (never a token)."""
    ctx = request.app.state.ctx
    if ctx.luxalgo is None:
        return {"enabled": False, "signed_in": False, "account": None, "expires_at": None, "refused": None}
    signed = await ctx.luxalgo_sign_in.status() if ctx.luxalgo_sign_in is not None else {"signed_in": False}
    return {"enabled": True, "account": None, "expires_at": None, "refused": None, **signed}


@router.get("/families")
async def families(request: Request):
    return await _read(request, "library_list_families", {})


@router.get("/indicators")
async def indicators(
    request: Request,
    family: str | None = None,
    text: str | None = Query(default=None, max_length=200),
    concept: str | None = Query(default=None, max_length=120),
    sort: str = Query(default="date", pattern="^(name|date|family)$"),
    page: int = Query(default=0, ge=0, le=1000),
    page_size: int = Query(default=24, ge=1, le=MAX_PAGE_SIZE),
):
    return await _read(request, "library_list_indicators", {
        "family": _family(family), "text": text or None, "concept": _slug(concept) if concept else None,
        "sort": sort, "direction": "asc" if sort == "name" else "desc", "page": page, "page_size": page_size})


@router.get("/search")
async def search(
    request: Request,
    query: str = Query(min_length=1, max_length=200),
    type: str = Query(default="all", pattern="^(all|concepts|indicators)$"),
    family: str | None = None,
    limit: int = Query(default=20, ge=1, le=50),
):
    return await _read(request, "library_search", {"query": query, "type": type, "family": _family(family),
                                                   "limit": limit})


@router.get("/known")
async def known(request: Request):
    """Every indicator WATA touched, in short: what the Library's cards say of each."""
    ctx = request.app.state.ctx
    _luxalgo(request)
    return {"items": list((await run_in_threadpool(known_of, ctx, None, whole=False)).values())}


@router.get("/indicators/{slug}")
async def indicator(slug: str, request: Request):
    found = await _read(request, "library_get_indicator", {"slug": _slug(slug)})
    knowledge = await run_in_threadpool(known_of, request.app.state.ctx, [slug])
    return {**found, "known": knowledge[slug]} if isinstance(found, dict) else found


@router.get("/indicators/{slug}/source")
async def indicator_source(slug: str, request: Request):
    """The source as LuxAlgo gives it (its first lines alone signed out), and what its header says of its licence."""
    found = await _read(request, "library_get_source_code", {"slug": _slug(slug)})
    if isinstance(found, dict) and found.get("source"):
        found = {**found, "source": found["source"].replace("\r\n", "\n"), **licence_of(found["source"])}
        _noticed(request.app.state.ctx, slug, found)
    return found


@router.get("/concepts/{slug}")
async def concept(slug: str, request: Request):
    return await _read(request, "library_get_concept", {"slug": _slug(slug)})


@router.post("/indicators/{slug}/import", status_code=201)
async def import_indicator(slug: str, body: ImportRequest, request: Request):
    """The indicator's whole source made one of the operator's scripts, its header kept and its origin recorded."""
    ctx = request.app.state.ctx
    try:
        found = await _luxalgo(request).source(_slug(slug))
    except LuxAlgoUnknown as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    except LuxAlgoRefused as e:
        raise NotPossible(str(e)) from None
    _noticed(ctx, slug, found)
    owner = f", © {found['owner']}" if found.get("owner") else ""
    origin = luxalgo_origin(found, "operator", time.time())
    description = f"LuxAlgo's {found['name']}, from its Library ({origin['url']}): {found['licence']}{owner}."[:500]

    def made() -> dict:
        script = ctx.ta_scripts.create(body.name or found["name"], found["source"], description)
        ctx.script_origins.record(script["id"], origin)
        return script

    script = await run_in_threadpool(made)
    return {"id": script["id"], "name": script["name"], "version": script["version"], "kind": script["kind"],
            "origin": origin}


# ── whether its indicators run on WATA ──


@router.get("/compat")
async def compat(request: Request):
    """What WATA's check found of each indicator, newest first, and the checks waiting."""
    ctx = request.app.state.ctx
    _luxalgo(request)
    items = sorted((await run_in_threadpool(ctx.luxalgo_compat.all)).values() if ctx.luxalgo_compat else [],
                   key=lambda entry: entry.get("checked_at") or "", reverse=True)
    checks = ctx.luxalgo_checks.state() if ctx.luxalgo_checks is not None else {"available": False}
    return {"items": items, "checks": checks}


@router.post("/compat")
async def ask_compat(body: CompatAsked, request: Request):
    """Indicators to check, each once in the queue: one at a time, while no Pine study runs."""
    return _checks(request).ask([_slug(slug) for slug in body.slugs])


# ── the sign-in to a LuxAlgo account ──


def _sign_in(request: Request):
    ctx = request.app.state.ctx
    if ctx.luxalgo_sign_in is None:
        raise NotPossible("LuxAlgo's tools are switched off (admin.luxalgo_enabled)")
    return ctx.luxalgo_sign_in


@router.post("/sign-in", dependencies=[Depends(require_config_edit_enabled)])
async def begin_sign_in(request: Request):
    """LuxAlgo's sign-in page, coming back to this admin's address (its Origin, checked as the same as its own)."""
    return {"url": await _sign_in(request).begin(request.headers.get("origin") or "")}


@router.post("/sign-in/pasted", dependencies=[Depends(require_config_edit_enabled)])
async def pasted_sign_in(body: PastedAddress, request: Request):
    """The address LuxAlgo sent the browser to, pasted when it could not come back here by itself."""
    ctx = request.app.state.ctx
    signed = await _sign_in(request).hand_over(body.address, by=client_ip(request, ctx.trusted_proxies))
    ctx.audit(request, "LuxAlgo account signed in", "from the address pasted")
    if ctx.luxalgo_checks is not None:
        ctx.luxalgo_checks.signed_in()
    return signed


@router.delete("/sign-in", dependencies=[Depends(require_config_edit_enabled)])
async def sign_out(request: Request):
    ctx = request.app.state.ctx
    signed = await _sign_in(request).sign_out(by=client_ip(request, ctx.trusted_proxies))
    ctx.audit(request, "LuxAlgo account signed out")
    return signed


@callback_router.get("/callback", include_in_schema=False)
async def callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None,
                   iss: str | None = None):
    ctx = request.app.state.ctx
    if ctx.luxalgo_sign_in is None:
        return RedirectResponse(f"{PAGE}?luxalgo=refused&why=off", status_code=303)
    if error or not code or not state:
        said = error or "no code"
        logger.warning("LuxAlgo sent the browser back without a code (%s)", said[:100])
        return RedirectResponse(f"{PAGE}?luxalgo=refused&why={urllib.parse.quote(said[:100])}", status_code=303)
    try:
        await ctx.luxalgo_sign_in.finish(code, state, by=client_ip(request, ctx.trusted_proxies), issuer=iss)
        if ctx.luxalgo_checks is not None:
            # The sources found cut are asked for again, whole now.
            ctx.luxalgo_checks.signed_in()
    except (InvalidRequest, NotPossible) as e:
        return RedirectResponse(f"{PAGE}?luxalgo=refused&why={urllib.parse.quote(str(e)[:200])}", status_code=303)
    ctx.audit(request, "LuxAlgo account signed in", "through the admin's callback")
    return RedirectResponse(f"{PAGE}?luxalgo=signed-in", status_code=303)
