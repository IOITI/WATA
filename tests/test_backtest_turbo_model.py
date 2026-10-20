"""
Turbos made up from an index (src/backtest/turbo_model.py): measured on
recorded turbos priced by a known rule, then made up again by it.
"""

import json
import math
from datetime import date, datetime

import pytest

from src.backtest.clock import TradingClock
from src.backtest.data import LONG, SHORT, DayData, TurboRef, TurboSeries
from src.backtest.turbo_model import (
    DirectionModel,
    Measure,
    ModelData,
    NoCalibration,
    compare_trades,
    fit_turbo,
    index_closes,
    ladder,
    made_up_day,
    read_calibration,
)

CLOCK = TradingClock("Europe/Paris")
DAY = date(2026, 10, 14)
# The rule the recorded turbos are priced by: 0.0086 € a point, from 20 points beyond the knock-out level.
FACTOR, OFFSET = 0.0086, -20.0


def at(clock_time: str) -> int:
    return int(datetime.fromisoformat(f"{DAY.isoformat()}T{clock_time}+00:00").timestamp())


def index_level(step: int) -> float:
    return 25_000 + 120 * math.sin(step / 300)


def index_rows(first: str = "06:00:00", last: str = "20:00:00") -> list[list]:
    """The index's rows: ts, bid open, high, low, close, ask open, high, low, close, updates."""
    rows, stamp, step = [], at(first), 0
    while stamp < at(last):
        level = index_level(step)
        rows.append([stamp, level, level + 1, level - 1, level, level + 1, level + 2, level, level + 1, 5])
        stamp += 10
        step += 1
    return rows


def recorded_turbo(number: int, direction: str, barrier: float, spread_at_15: float = 0.02) -> TurboSeries:
    """A turbo quoted from 08:00 to 22:00 in Paris (06:00 to 20:00 UTC), priced by the rule."""
    series = TurboSeries(TurboRef(number, 40_000_000 + number, "WarrantOpenEndKnockOut", f"T{number}", None, "SG", barrier, direction))
    model = DirectionModel(FACTOR, OFFSET, 100.0, 0, 0)
    for row in index_rows():
        bid = model.price(row[4], barrier, direction)
        spread = spread_at_15 if CLOCK.local(row[0]).hour == 15 else 0.01
        series.append(row[0], bid, bid, bid, bid, bid + spread, bid + spread)
    return series


def recorded_day() -> DayData:
    return DayData(day=DAY, start=at("00:00:00") - 7200, end=at("00:00:00") + 79_200, indice="us100", turbos={
        LONG: [recorded_turbo(2, LONG, 24_600.0), recorded_turbo(1, LONG, 24_500.0)],
        SHORT: [recorded_turbo(3, SHORT, 25_400.0), recorded_turbo(4, SHORT, 25_500.0)],
    }, covered=None)


def test_a_recorded_turbo_gives_its_euros_a_point_and_the_level_its_price_would_be_nothing_at():
    index = index_closes(index_rows())
    fit = fit_turbo(recorded_turbo(1, LONG, 24_500.0), index)
    assert fit.factor == pytest.approx(FACTOR, rel=1e-6)
    assert fit.level == pytest.approx(24_500.0 + OFFSET, abs=1e-3)
    assert fit.r2 == pytest.approx(1.0)
    short = fit_turbo(recorded_turbo(3, SHORT, 25_400.0), index)
    assert (short.factor, short.level) == (pytest.approx(FACTOR, rel=1e-6), pytest.approx(25_400.0 + OFFSET, abs=1e-3))
    # Too few bars beside the index: nothing.
    assert fit_turbo(recorded_turbo(1, LONG, 24_500.0), dict(list(index.items())[:30])) is None


def test_the_recorded_days_make_a_calibration_of_each_direction():
    measure = Measure(CLOCK)
    measure.add_day(recorded_day(), index_closes(index_rows()))

    found = measure.calibration(indice="us100", underlying={"provider": "dukascopy", "key": "us100", "side": "bid"}, made_at="now")

    long = found["directions"][LONG]
    assert (long["factor"], long["offset"], long["spacing"]) == (pytest.approx(FACTOR), pytest.approx(OFFSET), 100.0)
    assert long["turbos"] == 2 and long["r2"] == 1.0 and long["error_eur"] == 0.0
    assert found["spread_by_hour"]["15"] == 0.02 and found["spread_by_hour"]["9"] == 0.01
    assert found["quoting"] == {"first_minute": 8 * 60, "last_minute": 22 * 60}
    assert found["days"] == ["2026-10-14"] and found["few_days"] is True and found["issuer"] == "SG"
    with pytest.raises(NoCalibration):
        Measure(CLOCK).calibration(indice="us100", underlying={}, made_at="now")


def test_the_levels_of_a_day_are_multiples_of_the_spacing_beyond_the_open():
    assert ladder(25_013.0, LONG, 50.0, 3) == [25_000.0, 24_950.0, 24_900.0]
    assert ladder(25_013.0, SHORT, 50.0, 3) == [25_050.0, 25_100.0, 25_150.0]
    assert ladder(25_000.0, LONG, 50.0, 2) == [24_950.0, 24_900.0]
    assert ladder(25_000.0, SHORT, 50.0, 2) == [25_050.0, 25_100.0]


def calibration(**changes) -> dict:
    return {
        "format": 1, "indice": "us100", "underlying": {"provider": "saxo", "key": "1", "side": "bid"},
        "directions": {
            LONG: {"factor": FACTOR, "offset": OFFSET, "spacing": 100.0, "turbos": 2, "bars": 100},
            SHORT: {"factor": FACTOR, "offset": OFFSET, "spacing": 100.0, "turbos": 2, "bars": 100},
        },
        "spread_by_hour": {"9": 0.01, "15": 0.02}, "quoting": {"first_minute": 9 * 60, "last_minute": 21 * 60},
        "decimals": 2, **changes,
    }


def test_a_day_of_made_up_turbos_follows_the_index_and_ends_where_the_index_crosses_a_level():
    rows = index_rows()
    # The index swings between 24,879 and 25,121; at 12:00 UTC it falls through 24,800 in one bar.
    crash = next(place for place, row in enumerate(rows) if row[0] == at("12:00:00"))
    rows[crash][3] = 24_790.0
    start, end = CLOCK.day_bounds(DAY)

    day = made_up_day(calibration(), rows, CLOCK, DAY, "us100", start, end)

    longs = day.turbos[LONG]
    assert len(longs) == 40 and len(day.turbos[SHORT]) == 40
    # Quoted from 09:00 to 21:00 in Paris: 07:00 to 19:00 UTC.
    assert day.covered == {row[0] for row in rows if at("07:00:00") <= row[0] < at("19:00:00")}
    first = rows[next(place for place, row in enumerate(rows) if row[0] == at("07:00:00"))][4]
    assert longs[0].ref.barrier == math.floor(first / 100) * 100
    crossed = next(series for series in longs if series.ref.barrier == 24_800.0)
    assert crossed.ts[-1] == at("12:00:00") and crossed.bid_close[-1] == 0.01 and math.isnan(crossed.ask_close[-1])
    standing = next(series for series in longs if series.ref.barrier == 24_700.0)
    assert standing.ts[-1] == at("18:59:50")
    place = standing.index_at(at("13:00:00"))
    assert standing.bid_close[place] == round(FACTOR * (rows[crash + 360][4] - 24_700.0 - OFFSET), 2)
    # 15:00 in Paris: the spread of that hour; the hours not measured take the nearest.
    assert standing.ask_close[place] == pytest.approx(standing.bid_close[place] + 0.02)
    assert standing.ref.instrument_id < 0 and standing.ref.issuer == "Model"


def test_model_data_reads_the_index_s_rows_and_refuses_a_missing_measure(tmp_path):
    class Rows:
        async def rows(self, provider, key, start, end):
            assert (provider, key) == ("saxo", "1")
            return [row for row in index_rows() if start <= row[0] < end], None

    import asyncio

    start, end = CLOCK.day_bounds(DAY)
    day = asyncio.run(ModelData(calibration(), Rows(), CLOCK).day("us100", DAY, start, end))
    assert len(day.turbos[LONG]) == 40
    with pytest.raises(NoCalibration):
        ModelData(calibration(format=0), Rows(), CLOCK)
    with pytest.raises(NoCalibration):
        read_calibration(str(tmp_path / "none.json"))
    (tmp_path / "calibration.json").write_text(json.dumps(calibration()))
    assert read_calibration(str(tmp_path / "calibration.json"))["decimals"] == 2


def trade(signal: int, result: float, reason: str = "Explicit Close (All)", price: float = 5.0) -> dict:
    return {"signal_id": signal, "performance_percent": result, "exit": {"reason": reason}, "entry": {"price": price}}


def test_trades_on_recorded_and_made_up_turbos_are_paired_by_signal():
    recorded = [trade(1, 2.0), trade(2, -1.0, "Stop loss"), trade(3, 0.5), trade(4, 1.0)]
    modelled = [trade(1, 2.5, price=4.6), trade(2, -1.2, "Stop loss", 4.4), trade(3, 0.4, "Stop loss", 4.8), trade(9, 3.0)]

    found = compare_trades(recorded, modelled)

    assert (found["paired"], found["recorded_trades"], found["modelled_trades"]) == (3, 4, 4)
    assert found["mean_difference_percent"] == pytest.approx((0.5 - 0.2 - 0.1) / 3, abs=1e-3)
    assert found["median_absolute_difference_percent"] == 0.2
    assert found["same_exit_percent"] == pytest.approx(66.7)
    assert found["correlation"] > 0.9
    assert found["median_entry_price"] == {"recorded": 5.0, "modelled": 4.6}
    assert compare_trades([], [])["paired"] == 0
