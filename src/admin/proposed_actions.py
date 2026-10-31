"""
An action the AI agent proposed (src/admin/agent/proposals.py, kind "action"),
applied by the operator from its card through the page's own route, as their
click on that page would: the route checks the proposal still waits, is of its
kind and is fresh before it acts, keeps its own checks (actions enabled, its
409s and their "anyway", readiness), says the proposal in its audit entry, and
marks it applied once done. A route asked without a proposal is unchanged.
"""

import json
import logging
import re

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

from src.admin.agent.store import UnknownConversation
from src.admin.errors import InvalidRequest, NotPossible

logger = logging.getLogger(__name__)

# <conversation>/<proposal>, as the card names it.
REF = re.compile(r"^(\d{8}-\d{6}-[0-9a-f]{6})/(p[0-9a-f]{8})$")
MAX_REF = 40


async def pending(ctx, ref: str | None, kind: str) -> dict | None:
    """The proposal this action applies, still waiting and fresh, or None when the action is the page's own."""
    if ref is None:
        return None
    found = REF.match(ref)
    if found is None:
        raise HTTPException(status_code=422, detail="A proposal is named <conversation>/<proposal>")
    if ctx.agent is None:
        raise HTTPException(status_code=409, detail="The AI agent is off: act from the page itself")
    try:
        proposal = await run_in_threadpool(ctx.agent.proposals.pending_action, found.group(1), found.group(2), kind)
    except UnknownConversation as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    except InvalidRequest as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    except NotPossible as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    return {"conversation": found.group(1), "proposal": proposal}


def said(found: dict | None) -> str:
    """What the audit entry adds when the action applies one of the agent's proposals."""
    return f"Proposed by the AI agent: {found['proposal']['title']!r}" if found else ""


def joined(*parts: str) -> str:
    return " ".join(part for part in parts if part)


async def settled(ctx, found: dict | None, by: str | None, result: dict) -> None:
    """The proposal applied, with what the route answered; the action is done whatever becomes of this note."""
    if not found:
        return
    # Kept with the proposal as JSON: a moment the route answered with is written as its text.
    result = json.loads(json.dumps(result, default=str))
    try:
        await run_in_threadpool(ctx.agent.settle_action, found["conversation"], found["proposal"]["id"], by, result)
    except Exception as e:
        logger.warning(f"The AI agent's proposal {found['proposal']['id']} was applied, but could not be marked so: {e}")
