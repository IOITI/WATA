"""
The live check of a signal rule (src/admin/live_check.py, src/admin/live_check_service.py): its real trades, its paper
days, the backtest configuration linked to it and that configuration's calibrated run, side by side. Read only, but for
the link of a configuration to the rule, which changes nothing live (no audit entry, as a configuration's own switches).
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from src.admin.auth import require_session

router = APIRouter(prefix="/api/ta/rules", dependencies=[Depends(require_session)])


class LinkBody(BaseModel):
    configuration: str


def live_check(request: Request):
    """The service, when the backtests it reads are switched on."""
    ctx = request.app.state.ctx
    if not ctx.settings.backtests_enabled:
        raise HTTPException(status_code=403, detail="Backtests are disabled in the configuration: the live check reads them")
    check = getattr(ctx, "live_check", None)
    if check is None:
        raise HTTPException(status_code=503, detail="The live check is not available")
    return check


@router.get("/{rule_id}/live-check")
async def summary(rule_id: str, first: date | None = Query(None, alias="from"), last: date | None = Query(None, alias="to"),
                  check=Depends(live_check)):
    """
    A rule's days side by side over a period (the last 92 days by default), newest first: each track's figures, the
    gap, the bridge in all, the replay's agreement with the engine, the calibrated run and what the live trades measure.
    """
    return await check.summary(rule_id, first, last)


@router.get("/{rule_id}/live-check/days/{day}")
async def day_of(rule_id: str, day: date, check=Depends(live_check)):
    """One day whole: each signal and each trade of the backtest, the calibrated run, paper and live, and the bridge."""
    return await check.day(rule_id, day)


@router.get("/{rule_id}/live-check/candidates")
async def candidates(rule_id: str, check=Depends(live_check)):
    """The configurations whose runs play the rule's strategy: what may be linked to it as its backtest."""
    return {"items": await run_in_threadpool(check.candidates, rule_id)}


@router.put("/{rule_id}/live-check/backtest")
async def link(rule_id: str, body: LinkBody, check=Depends(live_check)):
    """A configuration linked to the rule as its backtest: refused when its runs play another strategy."""
    return await run_in_threadpool(check.link, rule_id, body.configuration)


@router.delete("/{rule_id}/live-check/backtest")
async def unlink(rule_id: str, check=Depends(live_check)):
    await run_in_threadpool(check.unlink, rule_id)
    return {"ok": True}
