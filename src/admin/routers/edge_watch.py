"""
Edge Watch (src/admin/edge_watch.py, src/admin/edge_watch_service.py): the live trades and each signal rule's paper
trades set against their reference run. Read only: the edge guard acts in the service's task, never from a route.
"""

import re

from fastapi import APIRouter, Depends, HTTPException, Request

from src.admin.auth import require_session
from src.admin.backtests import LIVE_PAPER

router = APIRouter(prefix="/api/edge-watch", dependencies=[Depends(require_session)])

_RULE_ID = re.compile(r"^[0-9a-f]{12}$")


def edge_watch(request: Request):
    """The service, when the backtests it reads are switched on."""
    ctx = request.app.state.ctx
    if not ctx.settings.backtests_enabled:
        raise HTTPException(status_code=403, detail="Backtests are disabled in the configuration: Edge Watch reads them")
    watch = getattr(ctx, "edge_watch", None)
    if watch is None:
        raise HTTPException(status_code=503, detail="Edge Watch is not available")
    return watch


@router.get("")
async def judgements(watch=Depends(edge_watch)):
    """Every owner's verdict, without the cones and the paths: the live trades (``live``) and the rules (``rules``)."""
    return watch.listed(await watch.judgements())


@router.get("/{owner}")
async def owner_of(owner: str, watch=Depends(edge_watch)):
    """One owner whole (``live`` or a rule's id): its reference, its model, its cone and each track with its path."""
    if owner != LIVE_PAPER and not _RULE_ID.fullmatch(owner):
        raise HTTPException(status_code=404, detail=f"No owner {owner!r}: 'live' or a signal rule's id")
    found = await watch.judgements()
    entry = found.get("live") if owner == LIVE_PAPER else (found.get("rules") or {}).get(owner)
    if entry is None:
        raise HTTPException(status_code=404, detail=(
            "No live reference: choose one (a replay of the signals WATA received) on its run's page"
            if owner == LIVE_PAPER else "This rule has no reference run: set one from a run made of the rule"
        ))
    return {**entry, "guard": found.get("guard"), "judged_at": found.get("judged_at")}
