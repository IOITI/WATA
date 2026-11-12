"""The execution ledger (src/admin/execution_ledger.py): what each real trade lost between its signal and its fills."""

import asyncio
from datetime import date, datetime, timedelta, timezone

import pytest

from src.admin.execution_ledger import (
    ALERTS_SQL,
    ENGINE,
    ENGINE_SQL,
    ON_SAXOS_QUOTE,
    ON_THE_INDEX,
    POSITIONS_SQL,
    TRADINGVIEW,
    UNKNOWN_SOURCE,
    LedgerPeriodRefused,
    decided_price,
    ledger,
    period_ledger,
    read_rows,
)
from src.backtest.execution_calibration import QUOTES_SQL

T0 = datetime(2026, 10, 16, 7, 0, tzinfo=timezone.utc)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def position(position_id="p1", **extra) -> dict:
    """A long stopped out: signal at T0, received 14 s later, bought at 10.10 on an ask of 10.00, sold at 9.20."""
    row = {
        "position_id": position_id, "action": "long", "instrument_uic": 61025835,
        "instrument_name": "NASDAQ 100 TurboXL Long", "position_open_price": 10.10, "position_close_price": 9.20,
        "position_total_performance_percent": -8.91, "position_close_reason": "Performance (Stoploss (-7%) hit at -7.4%)",
        "execution_time_open": at(15.0), "execution_time_close": at(600.0),
        "buy_sent_at": at(14.5), "buy_accepted_at": at(14.75), "buy_price_seen": 10.00,
        "signal_uuid": f"uuid-{position_id}", "signal_timestamp": at(-50.0), "received_timestamp": at(14.0),
        "first_sale_at": at(590.0), "sale_requested_at": at(598.0), "sale_price_seen": 9.40,
        "sale_reason": "Performance (Stoploss (-7%) hit at -7.4%)", "sale_accepted_at": at(598.3),
    }
    row.update(extra)
    return row


def rows(*positions, alerts=(), engine=(), quotes=()) -> dict:
    return {"positions": list(positions), "alerts": list(alerts), "engine": list(engine), "quotes": list(quotes)}


def test_a_trade_says_what_it_lost_to_each_cause_in_points_of_its_result():
    found = ledger(rows(
        position(),
        alerts=[{"signal_uuid": "uuid-p1", "alert_timestamp": T0}],
        quotes=[{"order_id": "p1|signal", "bid_close": 9.80, "ask_close": 9.90},
                {"order_id": "p1|buy", "bid_close": 9.95, "ask_close": 10.00}],
    ))

    trade = found["trades"][0]
    assert trade["source"] == TRADINGVIEW and trade["exit_group"] == "Stop-loss"
    # TradingView's alert at T0, received 14 s later; the purchase left 0.5 s after, Saxo took it in 0.25 s.
    assert (trade["signal_to_received"], trade["received_to_order"], trade["order_to_accepted"]) == (14.0, 0.5, 0.25)
    assert trade["accepted_to_fill"] == 0.25
    # The sale was first tried at 590 s and taken at 598 s; Saxo closed it 2 s after.
    assert (trade["sale_attempts"], trade["sale_to_fill"]) == (8.0, 2.0)
    # The ask rose from 9.90 to 10.00 while the signal travelled: 1.01 %.
    assert trade["delay_percent"] == pytest.approx(1.010, abs=0.001)
    # Paid 10.10 on an ask of 10.00: 1 %.
    assert trade["entry_slippage_percent"] == pytest.approx(1.0)
    # Decided on a bid of 9.40, sold at 9.20, over the 10.10 paid: 1.98 points.
    assert trade["exit_slippage_percent"] == pytest.approx(1.980, abs=0.001)
    assert trade["spread_percent"] == pytest.approx(0.5)
    assert trade["cost_percent"] == pytest.approx(1.010 + 1.0 + 1.980, abs=0.002)
    assert trade["decided_on"] == ON_SAXOS_QUOTE

    summary = found["summary"]
    assert summary["trades"] == 1 and summary["cost_percent"]["count"] == 1
    assert found["by_exit"][0]["group"] == "Stop-loss" and found["by_exit"][0]["trades"] == 1
    assert found["by_source"][0]["group"] == TRADINGVIEW


def test_an_engine_signal_is_timed_from_its_bar_close():
    found = ledger(rows(position(), engine=[{"signal_uuid": "uuid-p1", "bar_closed_at": at(13.0)}],
                        alerts=[{"signal_uuid": "uuid-p1", "alert_timestamp": at(13.5)}]))

    trade = found["trades"][0]
    assert trade["source"] == ENGINE and trade["signal_to_received"] == 1.0


def test_a_signal_whose_way_was_not_recorded_is_not_timed():
    trade = ledger(rows(position()))["trades"][0]

    assert trade["source"] == UNKNOWN_SOURCE and trade["signal_to_received"] is None
    # Without the recorded quotes, the delay and the spread are not measured; the slippages still are.
    assert trade["delay_percent"] is None and trade["spread_percent"] is None
    assert trade["cost_percent"] == pytest.approx(1.0 + 1.980, abs=0.002)


def test_an_exit_decided_on_the_index_is_set_against_its_estimate():
    reason = "Performance (Stoploss (-7%) hit at -7.1%; US100 between quotes: 9.38 from 9.95, 21 s)"
    row = position(sale_reason=reason, position_close_reason=reason)

    assert decided_price(row) == (9.38, ON_THE_INDEX)
    trade = ledger(rows(row))["trades"][0]
    assert trade["decided_on"] == ON_THE_INDEX
    assert trade["exit_slippage_percent"] == pytest.approx(100 * (9.38 - 9.20) / 10.10, abs=0.001)
    assert ledger(rows(row))["by_decided_on"][0]["group"] == ON_THE_INDEX


def test_a_reason_cut_short_falls_back_on_saxos_bid():
    row = position(sale_reason="Performance (Stoploss (-7%) hit at -7.1%; US100 between quotes")

    assert decided_price(row) == (9.40, ON_SAXOS_QUOTE)


def test_a_trade_without_its_orders_is_listed_with_what_is_known():
    row = position(buy_sent_at=None, buy_accepted_at=None, buy_price_seen=None, first_sale_at=None,
                   sale_requested_at=None, sale_price_seen=None, sale_reason=None, sale_accepted_at=None,
                   signal_uuid=None, received_timestamp=None)
    found = ledger(rows(row))

    trade = found["trades"][0]
    assert trade["cost_percent"] is None and trade["decided_on"] is None
    assert found["summary"]["cost_percent"] == {"count": 0, "median": None, "mean": None, "p90": None, "sum": None}
    assert found["by_decided_on"] == []


def test_the_figures_are_median_mean_tenth_worst_and_sum():
    trades = [position(f"p{n}", position_open_price=10.0, buy_price_seen=10.0 - n / 100) for n in range(1, 11)]
    summary = ledger(rows(*trades))["summary"]["entry_slippage_percent"]

    assert summary["count"] == 10
    assert summary["p90"] == pytest.approx(0.908, abs=0.001)
    assert summary["sum"] == pytest.approx(sum(100 * (n / 100) / (10.0 - n / 100) for n in range(1, 11)), abs=0.01)


def test_the_rows_are_read_with_the_signals_and_quotes_of_the_positions_found():
    asked = []

    async def fetch(sql, *args):
        asked.append((sql, args))
        if sql == POSITIONS_SQL:
            return [position(), position("p2", instrument_uic=None, signal_uuid=None)]
        if sql == ENGINE_SQL:
            return [{"signal_uuid": "uuid-p1", "bar_closed_at": at(13.0)}]
        return []

    found = asyncio.run(read_rows(fetch, T0, at(86_400)))

    assert [sql for sql, _ in asked] == [POSITIONS_SQL, ALERTS_SQL, ENGINE_SQL, QUOTES_SQL]
    assert asked[1][1] == (["uuid-p1"],)
    # The quotes of the turbo when the signal was raised (its bar close) and when the purchase left.
    keys, uics, moments = asked[3][1]
    assert (keys, uics, moments) == (["p1|signal", "p1|buy"], [61025835, 61025835], [at(13.0), at(14.5)])
    assert len(found["positions"]) == 2 and found["engine"][0]["signal_uuid"] == "uuid-p1"


def test_nothing_more_is_read_when_no_position_was_closed():
    asked = []

    async def fetch(sql, *args):
        asked.append(sql)
        return []

    assert asyncio.run(read_rows(fetch, T0, at(86_400))) == rows()
    assert asked == [POSITIONS_SQL]


class FakeQueries:
    def __init__(self):
        self.calls = []

    async def execution_ledger_rows(self, start, end):
        self.calls.append((start, end))
        return rows(position())


def test_a_period_is_of_days_of_the_trading_timezone_a_year_at_most():
    queries = FakeQueries()

    found = asyncio.run(period_ledger(queries, "Europe/Paris", date(2026, 10, 13), date(2026, 10, 14)))

    assert (found["from"], found["to"], found["summary"]["trades"]) == ("2026-10-13", "2026-10-14", 1)
    assert queries.calls == [(datetime(2026, 10, 12, 22, 0, tzinfo=timezone.utc), datetime(2026, 10, 14, 22, 0, tzinfo=timezone.utc))]
    with pytest.raises(LedgerPeriodRefused):
        asyncio.run(period_ledger(queries, "Europe/Paris", date(2026, 10, 14), date(2026, 10, 13)))
    with pytest.raises(LedgerPeriodRefused):
        asyncio.run(period_ledger(queries, "Europe/Paris", date(2025, 1, 1), date(2026, 10, 13)))


def test_the_ledger_is_outside_every_backtests_code_digest():
    from src.backtest.figures import _CODE

    assert not any("execution_ledger" in part for part in _CODE)
