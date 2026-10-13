"""
The prices the recorder kept, for the Prices page: which instruments have
bars in a period, the bars of one of them (by time, or as range bars), and
what gives them context (the trades taken on it, the signals received, the
stretches that were not recorded, the instruments Saxo left without a quote).
"""

from array import array
from datetime import date, datetime, timezone

import pytz
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from src.admin.auth import require_session
from src.admin.prices import (
    AVAILABILITY_MAX_DAYS,
    BAR_COLUMNS,
    GAPS_MAX_DAYS,
    INTERVALS,
    MAX_BUCKETS,
    MAX_PERIOD_DAYS,
    MAX_RUNS,
    RANGE_COLUMNS,
    RANGE_MAX_DAYS,
    RANGE_MAX_ROWS,
    SIDES,
    TooManyBars,
    auto_range_ticks,
    availability,
    price_decimals,
    range_bars,
    range_rows,
    recording_gaps,
    sample_at,
)
from src.price_recorder.export import day_bounds
from src.price_recorder.settings import RecorderSettings

router = APIRouter(prefix="/api/prices", dependencies=[Depends(require_session)])

# Signals that name no indice (the scheduler's end-of-day close) concern every chart.
SIGNALS_WITHOUT_INDICE = (None, "", "n/a")
SIGNAL_LIMIT = 500
# Ten-second bars read at a time to build range bars.
SERIES_PAGE = 20_000


def _period(ctx, first_day: date | None, last_day: date | None) -> tuple[date, date, datetime, datetime]:
    """The days asked for (today by default, both included) and their bounds in the trading timezone."""
    today = datetime.now(pytz.timezone(ctx.settings.timezone)).date()
    first_day = first_day or last_day or today
    last_day = last_day or first_day
    if last_day < first_day:
        raise HTTPException(status_code=422, detail="The period ends before it starts")
    if (last_day - first_day).days >= MAX_PERIOD_DAYS:
        raise HTTPException(status_code=422, detail=f"The period is longer than {MAX_PERIOD_DAYS} days")
    start, end = day_bounds(first_day, last_day, ctx.settings.timezone)
    return first_day, last_day, start, end


async def _instrument(ctx, instrument_id: int) -> dict:
    instrument = await ctx.queries.price_instrument(instrument_id)
    if instrument is None:
        raise HTTPException(status_code=404, detail="No such recorded instrument")
    return instrument


@router.get("/instruments")
async def instruments(
    request: Request,
    first_day: date | None = Query(None, alias="from"),
    last_day: date | None = Query(None, alias="to"),
):
    ctx = request.app.state.ctx
    first_day, last_day, start, end = _period(ctx, first_day, last_day)
    items = await ctx.queries.price_instruments(start, end)
    # Read apart from the instruments: while the recorder keeps no volume, the list is the same without it.
    volumes = await ctx.queries.price_volumes(start, end) if items else {}
    return {
        "from": first_day,
        "to": last_day,
        "timezone": ctx.settings.timezone,
        # volume None: Saxo's was not recorded for the instrument in the period.
        "items": [{**item, "volume": volumes.get(item["id"])} for item in items],
    }


def _recording_gaps(settings: RecorderSettings, coverage: list[dict], start: datetime, end: datetime) -> list[dict]:
    return recording_gaps(
        coverage,
        start.astimezone(timezone.utc),
        end.astimezone(timezone.utc),
        settings.is_trading_time,
        datetime.now(timezone.utc),
    )


@router.get("/availability")
async def availability_of(
    request: Request,
    first_day: date | None = Query(None, alias="from"),
    last_day: date | None = Query(None, alias="to"),
):
    """
    What was recorded of each instrument of the period, and when Saxo left it
    without a quote while the recorder was receiving others.
    """
    ctx = request.app.state.ctx
    first_day, last_day, start, end = _period(ctx, first_day, last_day)
    answer = {
        "from": first_day,
        "to": last_day,
        "timezone": ctx.settings.timezone,
        "max_days": AVAILABILITY_MAX_DAYS,
        # None: the period is too long for every one of its bars to be read.
        "items": None,
    }
    if (last_day - first_day).days >= AVAILABILITY_MAX_DAYS:
        return answer

    runs = await ctx.queries.price_runs(start, end, MAX_RUNS + 1)
    if len(runs) > MAX_RUNS:
        raise HTTPException(
            status_code=422,
            detail="Too many interruptions in the bars of this period to work them out: choose a shorter one",
        )
    turbos = {
        instrument["id"]: instrument.get("indice")
        for instrument in await ctx.queries.price_instruments(start, end)
        if instrument.get("role") == "turbo"
    }
    settings = RecorderSettings.from_config(ctx.config_manager)
    answer["items"] = availability(
        runs,
        turbos,
        _recording_gaps(settings, await ctx.queries.price_coverage(start, end), start, end),
        settings.is_trading_time,
        pytz.timezone(ctx.settings.timezone),
    )
    return answer


@router.get("/bars")
async def bars(
    request: Request,
    instrument_id: int = Query(..., ge=1),
    first_day: date | None = Query(None, alias="from"),
    last_day: date | None = Query(None, alias="to"),
    interval: int = Query(INTERVALS[0]),
):
    ctx = request.app.state.ctx
    if interval not in INTERVALS:
        raise HTTPException(
            status_code=422, detail=f"The interval must be one of {', '.join(map(str, INTERVALS))} seconds",
        )
    first_day, last_day, start, end = _period(ctx, first_day, last_day)
    instrument = await _instrument(ctx, instrument_id)

    # One more than the limit: enough to know that there is more, without reading it.
    rows = await ctx.queries.price_bars(instrument_id, start, end, interval, MAX_BUCKETS + 1)
    if len(rows) > MAX_BUCKETS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"More than {MAX_BUCKETS} bars in this period at {interval} seconds: "
                "choose a longer interval or a shorter period"
            ),
        )
    return {
        "instrument": instrument,
        "from": first_day,
        "to": last_day,
        "timezone": ctx.settings.timezone,
        "interval": interval,
        "columns": list(BAR_COLUMNS),
        "bars": rows,
    }


class _Series:
    """The ten-second bars of one side, in columns: a month of them is a few megabytes this way."""

    def __init__(self):
        self.times = array("q")
        self.opens, self.highs, self.lows, self.closes = array("d"), array("d"), array("d"), array("d")

    def add(self, rows: list[tuple]) -> None:
        for time, opened, high, low, closed in rows:
            self.times.append(time)
            self.opens.append(opened)
            self.highs.append(high)
            self.lows.append(low)
            self.closes.append(closed)


async def _series(ctx, instrument_id: int, start: datetime, end: datetime, side: str) -> _Series:
    series = _Series()
    page_start = start
    while True:
        rows = await ctx.queries.price_series(instrument_id, page_start, end, side, SERIES_PAGE)
        series.add(rows)
        if len(series.times) > RANGE_MAX_ROWS:
            raise HTTPException(
                status_code=422, detail="Too many recorded bars in this period to build range bars: choose a shorter one",
            )
        if len(rows) < SERIES_PAGE:
            return series
        page_start = datetime.fromtimestamp(series.times[-1] + 1, timezone.utc)


@router.get("/range-bars")
async def range_bars_of(
    request: Request,
    instrument_id: int = Query(..., ge=1),
    first_day: date | None = Query(None, alias="from"),
    last_day: date | None = Query(None, alias="to"),
    side: str = Query(SIDES[0]),
    size: float | None = Query(None, gt=0),
    under: int | None = Query(None, ge=1),
):
    """
    The recorded prices of one side as range bars: a bar ends when the price
    has left a range of ``size``, however long that took. Without ``size``, a
    range that suits the period is chosen. ``under`` names the index shown
    under a turbo: its bid at the end of each bar comes with the bar.
    """
    ctx = request.app.state.ctx
    if side not in SIDES:
        raise HTTPException(status_code=422, detail=f"The side must be one of {', '.join(SIDES)}")
    first_day, last_day, start, end = _period(ctx, first_day, last_day)
    if (last_day - first_day).days >= RANGE_MAX_DAYS:
        raise HTTPException(status_code=422, detail=f"Range bars are built for periods of {RANGE_MAX_DAYS} days at most")
    instrument = await _instrument(ctx, instrument_id)
    index_instrument = await _instrument(ctx, under) if under is not None else None

    series = await _series(ctx, instrument_id, start, end, side)
    decimals = price_decimals(series.opens, series.highs, series.lows, series.closes)
    tick = round(10 ** -decimals, decimals)
    if size is None:
        ticks = auto_range_ticks(
            round(min(series.lows, default=0) * 10 ** decimals), round(max(series.highs, default=0) * 10 ** decimals),
        )
    else:
        ticks = round(size * 10 ** decimals)
        if ticks < 1:
            raise HTTPException(status_code=422, detail=f"The range is smaller than one step of these prices ({tick:.{decimals}f})")

    try:
        bars = range_bars(
            series.times, series.opens, series.highs, series.lows, series.closes,
            ticks=ticks, decimals=decimals, limit=MAX_BUCKETS,
        )
    except TooManyBars:
        raise HTTPException(
            status_code=422,
            detail=f"More than {MAX_BUCKETS} range bars in this period: choose a larger range or a shorter period",
        ) from None

    index = None
    if index_instrument is not None:
        # The index has no spread to speak of: its bid is the level.
        index_series = await _series(ctx, under, start, end, SIDES[0])
        # ``ended`` is the end of the bar's last ten seconds: the index of those ten seconds is the one before it.
        index = sample_at(index_series.times, index_series.closes, [bar.ended - 1 for bar in bars])

    return {
        "instrument": instrument,
        "under": index_instrument,
        "from": first_day,
        "to": last_day,
        "timezone": ctx.settings.timezone,
        "side": side,
        # One step of these prices, and the range in steps (TradingView's "R") and as a price.
        "tick": tick,
        "decimals": decimals,
        "ticks": ticks,
        "size": round(ticks * tick, decimals),
        "auto": size is None,
        "columns": list(RANGE_COLUMNS),
        "bars": range_rows(bars, decimals, index),
    }


@router.get("/context")
async def context(
    request: Request,
    instrument_id: int = Query(..., ge=1),
    first_day: date | None = Query(None, alias="from"),
    last_day: date | None = Query(None, alias="to"),
):
    """What goes on the chart beside the prices."""
    ctx = request.app.state.ctx
    first_day, last_day, start, end = _period(ctx, first_day, last_day)
    instrument = await _instrument(ctx, instrument_id)

    trades = await ctx.queries.price_trades(instrument, start, end)
    journal = await ctx.queries.signal_journal(start=first_day, end=last_day, limit=SIGNAL_LIMIT)
    signals = [
        entry for entry in journal
        if entry.get("indice") == instrument.get("indice") or entry.get("indice") in SIGNALS_WITHOUT_INDICE
    ]
    # Oldest first, like the bars they are drawn on.
    signals.sort(key=lambda entry: str(entry.get("decided_at") or ""))

    gaps = None
    if (last_day - first_day).days < GAPS_MAX_DAYS:
        settings = RecorderSettings.from_config(ctx.config_manager)
        gaps = _recording_gaps(settings, await ctx.queries.price_coverage(start, end), start, end)
    return {
        "instrument": instrument,
        "trades": trades,
        "signals": signals,
        # None: the period is too long for the gaps to be worked out.
        "gaps": gaps,
    }
