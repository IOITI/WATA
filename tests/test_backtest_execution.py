"""
How a run's orders are filled (src/backtest/execution.py): at the settings'
fixed values, or drawn from WATA's own orders kept with the run; and the
service that keeps those orders with a run that asks for them.
"""

import asyncio
import json
from dataclasses import replace

import pytest

from src.admin.errors import NotPossible
from src.backtest.execution import EXECUTION_FILE, FixedFills, MeasuredFills, fills_for, missing
from src.backtest.execution_calibration import samples
from src.backtest.settings import BacktestSettings
from tests.test_admin_backtests import config, folder_of, pine, scripts, service  # noqa: F401 (fixtures)

# A tenth of the orders took a minute, as WATA's did.
SAMPLES = {
    "order_seconds": [0.8] * 18 + [61.0, 59.0],
    "slippage_buy": [0.03, 0.04] * 6,
    "slippage_sell": [0.02, 0.05] * 6,
    "signal_delay_seconds": [2.5] * 12,
}
MEASURED = replace(BacktestSettings(), execution="measured")


def test_fixed_fills_are_the_settings_own():
    fills = FixedFills(replace(BacktestSettings(), order_seconds=1.5, slippage=0.02))

    assert (fills.order_seconds(), fills.exit_seconds()) == (1.5, 0.0)
    # At the quote of the fill, a slippage worse; the quote when the order left plays no part.
    assert (round(fills.purchase(5.0, 5.1), 6), round(fills.sale(5.0, 4.9), 6)) == (5.12, 4.88)


def test_measured_fills_are_drawn_from_wata_s_orders_the_same_way_each_time():
    def day_of_draws(seed: str) -> list:
        fills = fills_for(MEASURED, SAMPLES, seed)
        fills.start_day("2026-10-14")
        return [(fills.order_seconds(), fills.purchase(5.0, 9.9), fills.sale(5.0, 0.1)) for _ in range(200)]

    first, again, other = day_of_draws("1:run"), day_of_draws("1:run"), day_of_draws("1:random:0")

    assert first == again and first != other
    # Priced from the quote when the order left, whatever the quote at the fill.
    assert {round(price, 2) for _, price, _ in first} == {5.03, 5.04}
    assert {round(price, 2) for _, _, price in first} == {4.98, 4.95}
    # The minute-long orders come back about a tenth of the time.
    late = sum(1 for seconds, _, _ in first if seconds > 50)
    assert 8 <= late <= 35


def test_a_cells_later_and_dearer_fills_add_to_the_draws():
    stressed = replace(MEASURED, order_seconds=MEASURED.order_seconds + 5, slippage=MEASURED.slippage + 0.02)

    fills = fills_for(stressed, SAMPLES, "1:run", base=MEASURED)

    assert isinstance(fills, MeasuredFills) and (fills.later_seconds, fills.worse_price) == (5, 0.02)
    assert fills.order_seconds() >= 5.8 and round(fills.purchase(5.0, 0), 2) in (5.05, 5.06)


def test_too_few_measures_to_draw_from_are_said():
    few = {**SAMPLES, "order_seconds": [1.0] * 3, "slippage_sell": []}

    assert missing(few) == ["3 order times", "0 sale prices"]
    assert missing(SAMPLES) == []
    with pytest.raises(ValueError, match="3 order times, 0 sale prices"):
        fills_for(MEASURED, few, "1:run")
    assert isinstance(fills_for(BacktestSettings(), None, "1:run"), FixedFills)


def test_the_measure_keeps_each_order_one_by_one():
    from datetime import datetime, timedelta, timezone

    sent = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
    rows = {
        "signals": [{"signal_timestamp": sent, "received_timestamp": sent, "decided_at": sent + timedelta(seconds=3), "outcome": "executed"}],
        "orders": [
            {"order_id": "1", "purpose": "open", "sent_at": sent, "accepted_at": sent + timedelta(seconds=60), "outcome": "confirmed",
             "instrument_uic": 7, "position_open_price": 5.05, "position_close_price": None},
            {"order_id": "2", "purpose": "close", "sent_at": sent, "accepted_at": sent + timedelta(seconds=1), "outcome": "confirmed",
             "instrument_uic": 7, "position_open_price": None, "position_close_price": 4.9},
        ],
        "quotes": [{"order_id": "1", "bid_close": 4.98, "ask_close": 5.0}, {"order_id": "2", "bid_close": 4.95, "ask_close": 4.97}],
        "requests": [],
    }

    found = samples(rows)

    assert found["order_seconds"] == [60.0, 1.0] and found["signal_delay_seconds"] == [3.0]
    assert [round(value, 4) for value in found["slippage_buy"]] == [0.05]
    assert [round(value, 4) for value in found["slippage_sell"]] == [0.05]


# ── the service keeps the orders with the run ──


def test_a_run_on_measured_fills_keeps_the_orders_it_draws_from_and_its_stress_too(tmp_path, scripts, config):
    backtests = service(tmp_path, scripts, config)
    execution = {"from": "2026-07-22", "to": "2026-10-21", "samples": SAMPLES}

    async def ask():
        made = backtests.create(pine(scripts, changes={"execution": "measured"}), execution=execution)
        fixed = backtests.create(pine(scripts), execution=execution)
        refused = None
        try:
            backtests.create(pine(scripts, changes={"execution": "measured"}),
                             execution={**execution, "samples": {**SAMPLES, "slippage_buy": [0.01]}})
        except NotPossible as e:
            refused = str(e)
        return made, fixed, refused

    made, fixed, refused = asyncio.run(ask())

    assert json.loads((folder_of(tmp_path, made["id"]) / EXECUTION_FILE).read_text()) == execution
    # A run on fixed fills keeps none: it draws nothing.
    assert not (folder_of(tmp_path, fixed["id"]) / EXECUTION_FILE).exists()
    assert "1 purchase prices" in refused and "use fixed fills" in refused
