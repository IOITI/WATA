"""
Backtests: asking for one, following it, reading what it found, and the
prices it runs on (src/admin/backtests.py does the work).

They act on nothing that trades, so they are not operator actions: no audit
entry, and their own switch (``admin.backtests_enabled``).

Two of them start from what WATA really did, read here from the database and
handed to the service: the replay of the signals it received, and the measure
of the turbo on its closed trades.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from src.admin.auth import require_session
from src.admin.backtests import DAYS, KEEP_DEFAULT_DAYS, REFUSALS, TRADES

# What the prices panel shows when it is not told a period.
DEFAULT_COVERAGE_DAYS = 120

router = APIRouter(prefix="/api/backtests", dependencies=[Depends(require_session)])


def backtests(request: Request):
    """The service, when the feature is switched on."""
    ctx = request.app.state.ctx
    if not ctx.settings.backtests_enabled:
        raise HTTPException(status_code=403, detail="Backtests are disabled in the configuration")
    if ctx.backtests is None:
        raise HTTPException(status_code=503, detail="Backtests are not available")
    return ctx.backtests


class Period(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    first_day: date = Field(alias="from")
    last_day: date = Field(alias="to")


class SignalsRequest(Period):
    # Only what differs from the live rules.
    rules: dict[str, bool | int | float] = Field(default_factory=dict)
    entry_price: float | None = Field(None, gt=0)
    spread: float | None = Field(None, ge=0)
    leverages: list[float] | None = None
    note: str = ""
    # The account the result is also counted in money on, and how unkind the fills are taken to be.
    capital: float | None = Field(None, ge=0)
    fill_delay: int | None = Field(None, ge=0)
    slippage: float | None = Field(None, ge=0)


class RunRequest(SignalsRequest):
    strategy: str = "kernel_vwap"
    # Only what differs from the strategy's defaults.
    parameters: dict[str, bool | int | float | str] = Field(default_factory=dict)


class Axis(BaseModel):
    kind: Literal["parameter", "rule"]
    name: str
    values: list[bool | int | float | str]


class SweepRequest(Period):
    """The same backtest for every combination of the values of one or two settings."""

    replay: Literal["strategy", "signals"] = "strategy"
    axes: list[Axis]
    # The share of the days, the last ones, that have no say in the choice.
    held_out: float = Field(0.3, gt=0, lt=1)
    strategy: str = "kernel_vwap"
    parameters: dict[str, bool | int | float | str] = Field(default_factory=dict)
    rules: dict[str, bool | int | float] = Field(default_factory=dict)
    entry_price: float | None = Field(None, gt=0)
    spread: float | None = Field(None, ge=0)
    # One turbo: a sweep already multiplies the runs.
    leverage: float | None = Field(None, gt=0)
    note: str = ""
    fill_delay: int | None = Field(None, ge=0)
    slippage: float | None = Field(None, ge=0)


class Renaming(BaseModel):
    note: str | None = None
    tags: list[str] | None = None


class Removal(BaseModel):
    ids: list[str] = Field(max_length=200)


class KeepRequest(BaseModel):
    enabled: bool
    days: int = Field(KEEP_DEFAULT_DAYS, ge=1)


class FetchRequest(Period):
    # The Dukascopy instrument (src/admin/data.py); the history backtests read when absent.
    label: str | None = None


@router.get("/options")
async def options(service=Depends(backtests)):
    """What the form offers: the strategies and their parameters, the turbo assumed, the rules in force."""
    return service.options()


@router.get("/prices")
async def prices(
    first_day: date | None = Query(None, alias="from"),
    last_day: date | None = Query(None, alias="to"),
    label: str | None = Query(None),
    service=Depends(backtests),
):
    """
    Which days have prices to backtest on, and the download in progress if
    there is one. Of another Dukascopy instrument than the one backtests read
    when ``label`` says so. Off the event loop: each day's hours are looked
    for on the disk.
    """
    last_day = last_day or datetime.now(timezone.utc).date()
    first_day = first_day or last_day - timedelta(days=DEFAULT_COVERAGE_DAYS - 1)
    return await run_in_threadpool(service.coverage, first_day, last_day, label)


@router.post("/prices/fetch", status_code=202)
async def fetch_prices(body: FetchRequest, service=Depends(backtests)):
    """Download the prices of a period in the background. One download at a time."""
    return service.start_fetch(body.first_day, body.last_day, label=body.label)


@router.post("/prices/fetch/stop")
async def stop_fetching_prices(service=Depends(backtests)):
    return service.stop_fetch()


@router.put("/prices/keep")
async def keep_prices(body: KeepRequest, service=Depends(backtests)):
    """Have the service fetch by itself what is missing of the last days, or stop doing so."""
    return service.set_keep(body.enabled, body.days)


@router.get("/calibration")
async def calibration(service=Depends(backtests)):
    """The turbo last measured on WATA's closed trades, and the measure in progress."""
    return service.calibration()


@router.post("/calibration", status_code=202)
async def calibrate(request: Request, service=Depends(backtests)):
    """Measure the turbo of the backtests on every closed trade of WATA. In the background."""
    queries = request.app.state.ctx.queries
    return service.start_calibration(await queries.closed_positions(), await queries.journal_between())


@router.post("/signals", status_code=202)
async def replay_signals(body: SignalsRequest, request: Request, service=Depends(backtests)):
    """Ask for a backtest of the signals WATA received in the period, to be compared with what it did."""
    queries = request.app.state.ctx.queries
    return service.create_from_signals(
        first_day=body.first_day, last_day=body.last_day,
        journal=await queries.journal_between(body.first_day, body.last_day),
        positions=await queries.closed_positions(body.first_day, body.last_day),
        rules=body.rules, entry_price=body.entry_price, spread=body.spread, leverages=body.leverages,
        note=body.note.strip(), capital=body.capital, fill_delay=body.fill_delay, slippage=body.slippage,
    )


@router.post("/sweeps", status_code=202)
async def sweep(body: SweepRequest, request: Request, service=Depends(backtests)):
    """Ask for a sweep. Of the signals WATA received, its journal and its positions of the period are read first."""
    journal = positions = None
    if body.replay == "signals":
        queries = request.app.state.ctx.queries
        journal = await queries.journal_between(body.first_day, body.last_day)
        positions = await queries.closed_positions(body.first_day, body.last_day)
    return service.create_sweep(
        first_day=body.first_day, last_day=body.last_day, axes=[axis.model_dump() for axis in body.axes],
        held_out=body.held_out, strategy=body.strategy, parameters=body.parameters, rules=body.rules,
        entry_price=body.entry_price, spread=body.spread, leverage=body.leverage, note=body.note.strip(),
        fill_delay=body.fill_delay, slippage=body.slippage, journal=journal, positions=positions,
    )


@router.post("/remove")
async def remove_several(body: Removal, service=Depends(backtests)):
    """Remove several runs at once: those that wait or run are left, and named."""
    return service.delete_many(body.ids)


@router.get("/storage")
async def storage(service=Depends(backtests)):
    """The disk the runs and the prices take. Walks their folders: off the event loop."""
    return await run_in_threadpool(service.storage)


@router.get("")
async def runs(service=Depends(backtests)):
    return {"items": service.list_runs()}


@router.post("", status_code=202)
async def create(body: RunRequest, service=Depends(backtests)):
    """Ask for a backtest. It waits its turn: one runs at a time."""
    return service.create(
        first_day=body.first_day, last_day=body.last_day, strategy=body.strategy,
        parameters=body.parameters, rules=body.rules,
        entry_price=body.entry_price, spread=body.spread, leverages=body.leverages, note=body.note.strip(),
        capital=body.capital, fill_delay=body.fill_delay, slippage=body.slippage,
    )


@router.get("/{run_id}")
async def run(run_id: str, service=Depends(backtests)):
    return service.get(run_id)


@router.patch("/{run_id}")
async def rename(run_id: str, body: Renaming, service=Depends(backtests)):
    """Name a run again, or tag it."""
    return service.update(run_id, note=None if body.note is None else body.note.strip(), tags=body.tags)


@router.post("/{run_id}/cells/{cell}/run", status_code=202)
async def run_cell(run_id: str, cell: int, service=Depends(backtests)):
    """Make a backtest of its own of one combination of a sweep."""
    return service.run_cell(run_id, cell)


def _rows(kind: str):
    async def rows(run_id: str, leverage: float = Query(...), service=Depends(backtests)):
        return {"items": service.rows(run_id, kind, leverage)}

    return rows


router.add_api_route("/{run_id}/trades", _rows(TRADES), methods=["GET"])
router.add_api_route("/{run_id}/days", _rows(DAYS), methods=["GET"])
router.add_api_route("/{run_id}/refusals", _rows(REFUSALS), methods=["GET"])


@router.get("/{run_id}/breakdowns")
async def breakdowns(run_id: str, leverage: float = Query(...), service=Depends(backtests)):
    """The trades of a run by hour, weekday, direction, reason, confidence and holding time."""
    return service.breakdowns(run_id, leverage)


@router.get("/{run_id}/trades/path")
async def trade_path(run_id: str, leverage: float = Query(...), entry: str = Query(...), service=Depends(backtests)):
    """One trade as the position monitor lived it. Replayed for the request, off the event loop."""
    return await run_in_threadpool(service.trade_path, run_id, leverage, entry)


@router.get("/{run_id}/days/{day}/chart")
async def day_chart(run_id: str, day: date, leverage: float = Query(...), service=Depends(backtests)):
    """One day as the strategy saw it. Replayed for the request, off the event loop."""
    return await run_in_threadpool(service.day_chart, run_id, day, leverage)


@router.get("/{run_id}/comparison")
async def comparison(run_id: str, leverage: float = Query(...), service=Depends(backtests)):
    """A run of real signals beside what WATA really did with them."""
    return service.comparison(run_id, leverage)


@router.post("/{run_id}/cancel")
async def cancel(run_id: str, service=Depends(backtests)):
    return service.cancel(run_id)


@router.delete("/{run_id}", status_code=204)
async def delete(run_id: str, service=Depends(backtests)):
    service.delete(run_id)
    return Response(status_code=204)
