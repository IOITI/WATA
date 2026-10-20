"""
Backtests: asking for one, following it, reading what it found
(src/admin/backtests.py does the work, src/backtest the trading).

They act on nothing that trades, so they are not operator actions: no audit
entry, and their own switch (``admin.backtests_enabled``).

What a run needs from the database is read before it is asked for, by
src/admin/backtest_requests.py, which Claude's tools share: the signals WATA
received, for a run that replays them, and the account's last value, which the
money of a run starts from. The service never reads the database; the command
reads the recorded turbos through a read-only connection of its own.
"""

from datetime import date

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from src.admin import backtest_requests
from src.admin.auth import require_session
from src.admin.errors import InvalidRequest

router = APIRouter(prefix="/api/backtests", dependencies=[Depends(require_session)])

FATES = ("executed", "refused", "failed", "unpriced", "closed", "ignored", "not_a_signal")


def backtests(request: Request):
    """The service, when the feature is switched on."""
    ctx = request.app.state.ctx
    if not ctx.settings.backtests_enabled:
        raise HTTPException(status_code=403, detail="Backtests are disabled in the configuration")
    if ctx.backtests is None:
        raise HTTPException(status_code=503, detail="Backtests are not available")
    return ctx.backtests


class Renaming(BaseModel):
    name: str | None = None
    tags: list[str] | None = None


class Removal(BaseModel):
    ids: list[str] = Field(max_length=200)


class ResearchSwitch(BaseModel):
    enabled: bool | None = None
    keep_days: int | None = None


class TurboMeasure(BaseModel):
    first: date = Field(alias="from")
    last: date = Field(alias="to")


def research(request: Request):
    """The writing of each finished day for a research machine, with the backtests' switch."""
    backtests(request)
    ctx = request.app.state.ctx
    if ctx.research is None:
        raise HTTPException(status_code=503, detail="The research files are not available")
    return ctx.research


@router.get("/options")
async def options(request: Request, service=Depends(backtests)):
    """What the form offers: every setting with its live value, the limits of a run, the account to start from."""
    return await run_in_threadpool(service.options, await backtest_requests.last_balance(request.app.state.ctx.queries))


@router.get("/coverage")
async def coverage(
    request: Request,
    first_day: date = Query(..., alias="from"),
    last_day: date = Query(..., alias="to"),
    indice: str = Query("us100", max_length=32),
    asset: str | None = Query(None, max_length=80),
    source: str = Query("pine"),
    service=Depends(backtests),
):
    """
    What was recorded on each day of a period: the turbos of the indice by
    direction and the hours of their bars, how long the recorder listened,
    the bars of the asset a strategy would run on, the signals WATA received.
    """
    ctx = request.app.state.ctx
    return await backtest_requests.coverage(
        ctx.queries, ctx.history, ctx.settings.timezone, service.max_period_days, first_day, last_day,
        indice=indice, asset=asset, source=source,
    )


@router.get("/execution")
async def execution(
    request: Request,
    first_day: date | None = Query(None, alias="from"),
    last_day: date | None = Query(None, alias="to"),
    service=Depends(backtests),
):
    """
    What WATA's own orders took and cost (src/backtest/execution_calibration.py):
    the delays from a signal to the trader and from an order to Saxo, the price
    paid against the recorded quote, the refusals; and the values a run would take from them.
    """
    ctx = request.app.state.ctx
    return await backtest_requests.execution(ctx.queries, ctx.settings.timezone, first_day, last_day)


@router.get("")
async def runs(service=Depends(backtests)):
    items = await run_in_threadpool(service.list_runs)
    return {"items": items, "storage": await run_in_threadpool(service.storage)}


@router.post("", status_code=202)
async def create(request: Request, body: dict = Body(...), service=Depends(backtests)):
    """
    Ask for a run, or a sweep. It waits its turn: one runs at a time. Of the
    signals WATA received, the journal of the period (and a day either side)
    is read first.
    """
    return await backtest_requests.ask_backtest(request.app.state.ctx.queries, service, body)


@router.post("/remove")
async def remove_several(body: Removal, service=Depends(backtests)):
    """Remove several runs at once: those that wait or run are left, and named."""
    return await run_in_threadpool(service.delete_many, body.ids)


@router.delete("/former")
async def remove_former(service=Depends(backtests)):
    """Remove what the former backtester left on the disk."""
    return await run_in_threadpool(service.delete_former)


@router.get("/research")
async def research_status(export=Depends(research)):
    """Whether each finished day is written for a research machine, and what is there."""
    return await run_in_threadpool(export.status)


@router.put("/research")
async def research_switch(body: ResearchSwitch, export=Depends(research)):
    """Switch the research files on or off, or change how many days the server keeps."""
    return await run_in_threadpool(export.update, enabled=body.enabled, keep_days=body.keep_days)


@router.get("/paper")
async def paper(service=Depends(backtests)):
    """Each shadow rule's days traded on paper, beside the daily range of its reference run."""
    return await run_in_threadpool(service.paper)


@router.get("/turbo-model")
async def turbo_model(service=Depends(backtests)):
    """The measure of the turbos made up from the index, and whether one is being made."""
    return await run_in_threadpool(service.turbo_model)


@router.post("/turbo-model", status_code=202)
async def measure_turbos(body: TurboMeasure, service=Depends(backtests)):
    """Measure the recorded turbos of a period against the index, for the runs on made-up turbos."""
    return service.measure_turbos(body.first, body.last)


@router.get("/{run_id}")
async def run(run_id: str, service=Depends(backtests)):
    return await run_in_threadpool(service.get, run_id)


@router.patch("/{run_id}")
async def rename(run_id: str, body: Renaming, service=Depends(backtests)):
    """Name a run again, or tag it."""
    return await run_in_threadpool(lambda: service.update(run_id, name=body.name, tags=body.tags))


@router.post("/{run_id}/cancel")
async def cancel(run_id: str, service=Depends(backtests)):
    return service.cancel(run_id)


@router.delete("/{run_id}", status_code=204)
async def delete(run_id: str, service=Depends(backtests)):
    await run_in_threadpool(service.delete, run_id)
    return Response(status_code=204)


@router.get("/{run_id}/days")
async def days(run_id: str, service=Depends(backtests)):
    return {"items": await run_in_threadpool(service.days, run_id)}


@router.get("/{run_id}/signals")
async def signals(
    run_id: str, day: date | None = Query(None), fate: str | None = Query(None), service=Depends(backtests),
):
    if fate is not None and fate not in FATES:
        raise InvalidRequest(f"A signal's fate is one of {', '.join(FATES)}")
    return {"items": await run_in_threadpool(service.signals, run_id, day, fate)}


@router.get("/{run_id}/trades")
async def trades(run_id: str, day: date | None = Query(None), service=Depends(backtests)):
    return {"items": await run_in_threadpool(service.trades, run_id, day)}


@router.get("/{run_id}/breakdowns")
async def breakdowns(run_id: str, service=Depends(backtests)):
    """The trades of a run by hour, weekday, direction, exit, confidence, holding time, turbo price and issuer."""
    return await run_in_threadpool(service.breakdowns, run_id)


@router.get("/{run_id}/edge")
async def edge(run_id: str, service=Depends(backtests)):
    """How the asset moved after the run's entry signals, against random moments of the same hours."""
    return await run_in_threadpool(service.edge, run_id) or {}


@router.get("/{run_id}/reconciliation")
async def reconciliation(run_id: str, service=Depends(backtests)):
    """A replay of the journal set beside WATA's real positions of the same signals."""
    return await run_in_threadpool(service.reconciliation, run_id) or {}


@router.get("/{run_id}/baseline")
async def baseline(run_id: str, service=Depends(backtests)):
    """The totals of the run's repetitions with entries at random."""
    return await run_in_threadpool(service.baseline, run_id) or {}


@router.get("/{run_id}/sweep")
async def sweep(run_id: str, service=Depends(backtests)):
    return await run_in_threadpool(service.sweep, run_id)


@router.post("/{run_id}/reference")
async def reference(run_id: str, service=Depends(backtests)):
    """The run becomes the reference its rule's paper days are set against."""
    return service.set_reference(run_id)


@router.post("/{run_id}/stress", status_code=202)
async def stress(run_id: str, service=Depends(backtests)):
    """The run made again with each order filled later and at a worse price."""
    return service.stress(run_id)


@router.post("/{run_id}/cells/{cell}/run", status_code=202)
async def run_cell(run_id: str, cell: int, service=Depends(backtests)):
    """Make a run of its own of one combination of a sweep."""
    return service.run_cell(run_id, cell)
