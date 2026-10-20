"""
Claude's research on the server, as the operator watches it: the MCP endpoint
(on or off in the configuration, paused or not here), its tokens, the last
calls Claude made and what its share of the server holds; the experiments of
the journal Claude keeps, and the lock box.

Pausing the endpoint and revoking a token are operator actions: recorded in
the audit trail and announced on Telegram. A token is made on the server
alone (watamcptoken), where it is printed once. Moving or clearing the lock
box is the operator's own research and changes nothing that trades: no audit.
"""

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from src.admin.auth import require_session
from src.admin.mcp_server.facade import CLAUDE, journal_of
from src.admin.mcp_server.tokens import TokenError
from src.admin.ta_rules import LIVE, RuleStore

router = APIRouter(prefix="/api", dependencies=[Depends(require_session)])

RECENT_CALLS = 100


def _claudes_share(ctx) -> dict:
    """What Claude's work holds of the server now: its runs, scripts and rules, and the hour's studies."""
    share = {"waiting_runs": None, "runs_today": None, "scripts": None, "rules": None, "live_rules": None,
             "study_seconds_last_hour": None}
    if ctx.backtests is not None:
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        share["waiting_runs"] = ctx.backtests.waiting_of(CLAUDE)
        share["runs_today"] = sum(1 for run_id in ctx.backtests.run_ids(made_by=CLAUDE) if run_id.startswith(today))
    if ctx.ta_scripts is not None:
        share["scripts"] = ctx.ta_scripts.count_made_by(CLAUDE)
        rules = [rule for rule in RuleStore(ctx.ta_scripts).load()["rules"] if rule.get("made_by") == CLAUDE]
        share["rules"] = sum(1 for rule in rules if rule["mode"] != LIVE)
        share["live_rules"] = sum(1 for rule in rules if rule["mode"] == LIVE)
    if ctx.studies is not None:
        share["study_seconds_last_hour"] = round(ctx.studies.spent_seconds())
    return share


@router.get("/mcp")
async def mcp_state(request: Request):
    ctx = request.app.state.ctx
    settings = ctx.settings
    tokens = await run_in_threadpool(ctx.mcp_tokens.entries) if ctx.mcp_tokens is not None else []
    return {
        "enabled": settings.mcp_enabled,
        "paused": ctx.mcp_pause.state() if ctx.mcp_pause is not None else {"paused": False, "changed_at": None},
        "tokens_readable": ctx.mcp_tokens.readable() if ctx.mcp_tokens is not None else True,
        "tokens": tokens,
        "limits": settings.mcp_limits.as_dict(),
        "share": await run_in_threadpool(_claudes_share, ctx),
        "calls": ctx.mcp_calls.recent(RECENT_CALLS) if ctx.mcp_calls is not None else [],
    }


@router.post("/mcp/tokens/{token_id}/revoke")
async def revoke_token(token_id: str, request: Request):
    ctx = request.app.state.ctx
    if ctx.mcp_tokens is None:
        raise HTTPException(status_code=503, detail="The MCP endpoint's tokens are not available")
    known = {entry["id"]: entry for entry in await run_in_threadpool(ctx.mcp_tokens.entries)}
    if token_id not in known:
        # By id only: a name could revoke several tokens at once.
        raise HTTPException(status_code=404, detail="No such token")
    try:
        revoked = await run_in_threadpool(ctx.mcp_tokens.revoke, token_id)
    except TokenError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    ctx.audit(request, "MCP token revoked", f"name={known[token_id]['name']} id={token_id}")
    return {"revoked": revoked}


class Pause(BaseModel):
    paused: bool


@router.put("/mcp/paused")
async def pause(body: Pause, request: Request):
    ctx = request.app.state.ctx
    if ctx.mcp_pause is None:
        raise HTTPException(status_code=503, detail="The MCP endpoint's switch is not available")
    before = ctx.mcp_pause.paused()
    state = await run_in_threadpool(ctx.mcp_pause.set, body.paused)
    if before != body.paused:
        ctx.audit(request, "MCP endpoint paused" if body.paused else "MCP endpoint resumed")
    return state


# ── the experiment journal and the lock box ──


def _journal(request: Request):
    return journal_of(request.app.state.ctx.settings.backtests_path)


def _lockbox(found: dict | None) -> dict | None:
    if found is None:
        return None
    return {**found, "from": found["from"].isoformat(), "to": found["to"].isoformat() if found["to"] else None}


@router.get("/research/experiments")
async def experiments(request: Request):
    journal = _journal(request)

    def read():
        return journal.experiments(), journal.lockbox()

    found, box = await run_in_threadpool(read)
    return {"experiments": list(reversed(found)), "lockbox": _lockbox(box)}


class Lockbox(BaseModel):
    # None for both: no lock box any more. ``to`` None: every day after ``from``.
    start: date | None = None
    end: date | None = None


@router.put("/research/lockbox")
async def lockbox(body: Lockbox, request: Request):
    journal = _journal(request)
    if body.start is None:
        if body.end is not None:
            raise HTTPException(status_code=422, detail="A lock box starts on a day")
        await run_in_threadpool(journal.clear_lockbox)
        return {"lockbox": None}
    if body.end is not None and body.end < body.start:
        raise HTTPException(status_code=422, detail="The lock box ends before it starts")
    found = await run_in_threadpool(lambda: journal.set_lockbox(body.start, body.end, set_by="operator"))
    return {"lockbox": _lockbox(found)}
