"""
Backtests: asking for one, following it, reading what it found
(src/admin/backtests.py does the work, src/backtest the trading).

They act on nothing that trades, so they are not operator actions: no audit
entry, and their own switch (``admin.backtests_enabled``).

What a run needs from the database is read before it is asked for, by
src/admin/backtest_requests.py, which Claude's tools share: the signals WATA
received, or those a rule raised, for a run that replays them, and the
account's last value, which the money of a run starts from. The service never reads the database; the command
reads the recorded turbos through a read-only connection of its own.
"""

import time
from datetime import date

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, Request, Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from src.admin import backtest_requests, propfirm_check
from src.admin.auth import require_session
from src.admin.errors import InvalidRequest
from src.admin.live_drift import live_days

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


class PropFirmAsked(BaseModel):
    # The share of the balance risked on a trade, the product the challenges trade, and the accounts' sizes.
    risk_percent: float = Field(default=0.5, ge=0.1, le=5.0)
    product_type: str = Field(default="futures", pattern="^(futures|cfd)$")
    account_min: float | None = Field(default=None, ge=1_000, le=1_000_000)
    account_max: float | None = Field(default=None, ge=1_000, le=1_000_000)


def research(request: Request):
    """The writing of each finished day for a research machine, with the backtests' switch."""
    backtests(request)
    ctx = request.app.state.ctx
    if ctx.research is None:
        raise HTTPException(status_code=503, detail="The research files are not available")
    return ctx.research


@router.get("/options")
async def options(request: Request, service=Depends(backtests)):
    """What the form offers: every setting with its live value, the limits of a run, the account to start from and the live one."""
    return await run_in_threadpool(service.options, await backtest_requests.last_balance(request.app.state.ctx.queries))


@router.get("/coverage")
async def coverage(
    request: Request,
    first_day: date = Query(..., alias="from"),
    last_day: date = Query(..., alias="to"),
    indice: str = Query("us100", max_length=32),
    asset: str | None = Query(None, max_length=80),
    source: str = Query("pine"),
    rule: str | None = Query(None, pattern=r"^[0-9a-f]{12}$"),
    service=Depends(backtests),
):
    """
    What was recorded on each day of a period: the turbos of the indice by
    direction and the hours of their bars, how long the recorder listened,
    the bars of the asset a strategy would run on, the signals WATA received
    or those a rule raised.
    """
    ctx = request.app.state.ctx
    return await backtest_requests.coverage(
        ctx.queries, ctx.history, ctx.settings.timezone, service.max_period_days, first_day, last_day,
        indice=indice, asset=asset, source=source, rule=rule,
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
    """Each shadow rule's days traded on paper, beside the daily range of its reference run, and its report."""
    return await run_in_threadpool(service.paper)


def paper_task(request: Request):
    """What trades the rules and the signals received on paper, with the backtests."""
    paper_trading = getattr(request.app.state.ctx, "paper", None)
    if paper_trading is None:
        raise HTTPException(status_code=503, detail="Paper trading is not available")
    return paper_trading


@router.post("/paper/{owner}/report", status_code=202)
async def paper_report(
    owner: str = Path(pattern=r"^([0-9a-f]{12}|live)$"), service=Depends(backtests), paper_trading=Depends(paper_task),
):
    """
    A report of a shadow rule's days on paper since it began (or of the signals WATA received, ``live``), made
    now rather than after the next finished day. On the event loop: the run joins the service's line.
    """
    await paper_trading.ask_report(owner)
    return await run_in_threadpool(service.paper)


@router.get("/paper/live")
async def live_paper(request: Request, service=Depends(backtests)):
    """
    The signals WATA received traded on paper under the live settings, each day beside the real one and the
    daily range of the live reference, with the warning said once two days in a row were outside it.
    """
    paper_trading = getattr(request.app.state.ctx, "paper", None)
    if paper_trading is None:
        return {"days": [], "reference": None, "warning": None, "report": None, "next_report": None}
    days, warning, reference = await paper_trading.drift()
    live = (await run_in_threadpool(service.paper)).get("live") or {}
    if reference is None:
        # No reference yet: the paper days alone.
        days = live_days(live.get("days") or [], {}, None)
    return {"days": days, "reference": reference, "warning": warning,
            "report": live.get("report"), "next_report": live.get("next_report")}


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


@router.get("/{run_id}/days/{day}/indicator")
async def day_indicator(run_id: str, day: date, request: Request, service=Depends(backtests)):
    """
    A day of a run with the script that made its signals: its source, libraries and inputs as it ran,
    and the bars it ran on, from the engine's restart and its days of history before it.
    """
    # The admin's read-only database, which the asset's rows are read through (none: Dukascopy's files alone).
    return await service.day_indicator(run_id, day, db=getattr(request.app.state.ctx.queries, "db", None))


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


@router.get("/{run_id}/propfirms")
async def propfirms(run_id: str, service=Depends(backtests)):
    """The run's last check against funded challenges (LuxAlgo's simulator), and its figures as it would send them."""
    def read() -> dict:
        kept = service.kept(run_id, propfirm_check.FILE)
        try:
            said = propfirm_check.figures(service.trades(run_id), service.days(run_id), service.settings_of(run_id))
        except propfirm_check.NotCheckable as e:
            return {"kept": kept, "figures": None, "refused": str(e)}
        return {"kept": kept, "figures": said, "refused": None}

    return await run_in_threadpool(read)


@router.post("/{run_id}/propfirms")
async def check_propfirms(run_id: str, body: PropFirmAsked, request: Request, service=Depends(backtests)):
    """
    The run's trades as R-multiples, with its trades a day and the risk asked, sent to LuxAlgo's prop-firm
    simulator: which funded challenges such a strategy would pass, against the bar of a pass per attempt of one
    in two. Its answer is kept in the run's folder. Only those figures leave.
    """
    ctx = request.app.state.ctx
    if ctx.luxalgo is None:
        raise HTTPException(status_code=409, detail="LuxAlgo's tools are switched off (admin.luxalgo_enabled)")

    def figures() -> dict:
        return propfirm_check.figures(service.trades(run_id), service.days(run_id), service.settings_of(run_id))

    try:
        said = await run_in_threadpool(figures)
        arguments = propfirm_check.request_of(said, risk_percent=body.risk_percent, product_type=body.product_type,
                                              account_min=body.account_min, account_max=body.account_max)
    except propfirm_check.NotCheckable as e:
        raise InvalidRequest(str(e)) from None
    from src.admin.luxalgo import LuxAlgoRefused

    try:
        answer = await ctx.luxalgo.read("propfirms_validate_strategy", arguments, structured=True)
    except LuxAlgoRefused as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    kept = {"asked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "bar": propfirm_check.BAR,
            "asked": {key: value for key, value in arguments.items() if key != "rSeries"},
            "figures": {key: value for key, value in said.items() if key != "r_series"}, "answer": answer}
    await run_in_threadpool(service.keep, run_id, propfirm_check.FILE, kept)
    return kept


@router.post("/{run_id}/model-check", status_code=202)
async def model_check(run_id: str, service=Depends(backtests)):
    """The run made again on turbos made up from the index, its trades set beside the recorded ones."""
    return service.model_check(run_id)


@router.post("/{run_id}/cells/{cell}/run", status_code=202)
async def run_cell(run_id: str, cell: int, service=Depends(backtests)):
    """Make a run of its own of one combination of a sweep."""
    return service.run_cell(run_id, cell)
