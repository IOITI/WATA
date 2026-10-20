"""
What WATA's own orders took and cost (src/backtest/execution_calibration.py):
measured on built rows, then read for real from a PostgreSQL 16 of its own.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from src.backtest.clock import TradingClock
from src.backtest.execution_calibration import measure, read_rows

CLOCK = TradingClock("Europe/Paris")
SEVEN = datetime(2026, 10, 14, 7, 0, tzinfo=timezone.utc)


def at(seconds: float) -> datetime:
    return SEVEN + timedelta(seconds=seconds)


def rows(count: int = 12) -> dict:
    signals = [{"signal_timestamp": at(60 * n), "received_timestamp": at(60 * n + 1.5), "decided_at": at(60 * n + 2 + n % 3),
                "outcome": "executed"} for n in range(count)]
    orders, quotes = [], []
    for n in range(count):
        opened = {"order_id": f"o{n}", "purpose": "open", "sent_at": at(600 + n), "accepted_at": at(600 + n + 0.4 + n / 100),
                  "outcome": "confirmed", "instrument_uic": 40_000_001, "position_open_price": 5.02 + (0.01 if n % 4 == 0 else 0.0),
                  "position_close_price": None}
        closed = {"order_id": f"c{n}", "purpose": "close", "sent_at": at(900 + n), "accepted_at": at(900 + n + 0.5),
                  "outcome": "confirmed", "instrument_uic": 40_000_001, "position_open_price": 5.0, "position_close_price": 5.48}
        orders += [opened, closed]
        quotes += [{"order_id": f"o{n}", "bid_close": 5.0, "ask_close": 5.02}, {"order_id": f"c{n}", "bid_close": 5.5, "ask_close": 5.52}]
    # An order the turbo had no quote for, and one Saxo never showed the position of.
    orders.append({"order_id": "x", "purpose": "open", "sent_at": at(1200), "accepted_at": at(1201), "outcome": "confirmed",
                   "instrument_uic": 1, "position_open_price": 9.0, "position_close_price": None})
    orders.append({"order_id": "y", "purpose": "open", "sent_at": at(1300), "accepted_at": None, "outcome": "gave_up",
                   "instrument_uic": 40_000_001, "position_open_price": None, "position_close_price": None})
    quotes.append({"order_id": "y", "bid_close": 5.0, "ask_close": 5.02})
    requests = [{"requested_at": at(600 + n), "purpose": "open", "outcome": "accepted" if n % 6 else "refused",
                 "error_code": None if n % 6 else "TooFarFromEntryOrderPrice"} for n in range(count)]
    return {"signals": signals, "orders": orders, "quotes": quotes, "requests": requests}


def test_the_delays_and_the_prices_of_wata_s_orders_give_the_values_of_a_backtest():
    found = measure(rows(), CLOCK)

    assert found["signals"] == 12 and found["orders"] == 26 and found["requests"] == 12
    # Two to four seconds from the signal's own time to the trader's decision.
    assert found["signal_delay_seconds"]["from_signal"] == {"count": 12, "median": 3.0, "p90": 4.0}
    assert found["signal_delay_seconds"]["from_receipt"]["median"] == 1.5
    assert found["order_seconds"]["count"] == 25 and found["order_seconds"]["median"] == pytest.approx(0.5, abs=0.06)
    # Bought at the ask but one time in four a cent above; sold two cents under the bid.
    assert found["slippage"]["buy"]["count"] == 12 and found["slippage"]["buy"]["median"] == 0.0
    assert found["slippage"]["sell"]["median"] == pytest.approx(0.02) and found["slippage"]["sell"]["median_ticks"] == pytest.approx(2.0)
    assert found["refusals"]["share_percent"] == pytest.approx(16.7)
    assert found["refusals"]["by_hour"] == [{"hour": 9, "requests": 12, "refused": 2}]
    assert found["refusals"]["codes"] == {"TooFarFromEntryOrderPrice": 2}
    # The middle of every fill, bought or sold: nine at nothing, three a cent worse, twelve two cents.
    assert found["slippage"]["all"]["median"] == pytest.approx(0.015)
    assert found["suggested"] == {"signal_delay_seconds": 3.0, "order_seconds": 0.5, "slippage": pytest.approx(0.015)}


def test_too_few_measures_suggest_nothing():
    found = measure(rows(4), CLOCK)
    assert found["suggested"] == {}
    assert measure({}, CLOCK)["refusals"]["share_percent"] is None


def test_the_rows_are_read_for_real_from_the_tables_wata_writes(tmp_path):
    pgserver = pytest.importorskip("pgserver")
    import asyncpg

    if not hasattr(asyncpg, "connect"):
        pytest.skip("asyncpg is a stand-in in this run of the tests")
    from datetime import date

    from lab import db as lab_db
    from src.database.postgres import AsyncDbPriceHistoryManager, PostgresConnectionManager

    server = pgserver.get_server(tmp_path / "pg", cleanup_mode="delete")

    async def scenario():
        connection = await asyncpg.connect(server.get_uri())
        try:
            await lab_db.ensure_schema(connection)
            await connection.execute(
                "INSERT INTO signal_journal (signal_uuid, action, indice, signal_timestamp, received_timestamp, decided_at, outcome) "
                "VALUES ('s-1', 'long', 'us100', $1, $2, $3, 'executed')", at(0), at(1), at(3),
            )
            await connection.execute(
                "INSERT INTO turbo_data_position (action, position_id, position_amount, position_open_price, position_status, "
                "execution_time_open, instrument_uic) VALUES ('long', 'p-1', 100, 5.07, 'Open', $1, 40000001)", at(25),
            )
            await connection.execute(
                "INSERT INTO order_execution (order_id, purpose, position_id, sent_at, accepted_at, outcome) "
                "VALUES ('o-1', 'open', 'p-1', $1, $2, 'confirmed')", at(24), at(24.6),
            )
            await connection.execute(
                "INSERT INTO order_request (request_id, requested_at, operation, purpose, outcome, error_code) "
                "VALUES ('r-1', $1, 'place', 'open', 'refused', 'TooFar'), ('r-2', $2, 'place', 'open', 'accepted', NULL)",
                at(20), at(24),
            )
        finally:
            await connection.close()
        writer = PostgresConnectionManager(dsn=server.get_uri(), min_size=1, max_size=1)
        await writer.connect()
        try:
            history = AsyncDbPriceHistoryManager(writer)
            await history.ensure_partitions(date(2026, 10, 1))
            turbo_id = await history.upsert_instrument({
                "uic": 40000001, "asset_type": "WarrantOpenEndKnockOut", "role": "turbo", "indice": "us100", "symbol": "S1",
                "description": "NASDAQ 100 Turbo long 24800 SG", "currency": "EUR", "direction": "long", "barrier": 24800.0,
                "issuer": "SG",
            }, SEVEN)
            # The bar of 07:00:00 ended at 07:00:10, the one of 07:00:10 at 07:00:20: the order left at 07:00:24.
            await history.insert_bars([
                (at(0), turbo_id, 5.0, 5.0, 5.0, 5.0, 5.02, 5.02, 5.02, 5.02, 1, 0),
                (at(10), turbo_id, 5.03, 5.03, 5.03, 5.03, 5.05, 5.05, 5.05, 5.05, 1, 0),
                (at(30), turbo_id, 6.0, 6.0, 6.0, 6.0, 6.02, 6.02, 6.02, 6.02, 1, 0),
            ])
            connection = await asyncpg.connect(server.get_uri())
            try:
                return await read_rows(connection.fetch, at(-3600), at(3600))
            finally:
                await connection.close()
        finally:
            await writer.close()

    try:
        found = asyncio.run(scenario())
    finally:
        server.cleanup()
    assert [row["order_id"] for row in found["quotes"]] == ["o-1"]
    assert found["quotes"][0]["ask_close"] == 5.05
    measured = measure(found, CLOCK)
    assert measured["slippage"]["buy"]["median"] == pytest.approx(0.02)
    assert measured["order_seconds"]["median"] == pytest.approx(0.6)
    assert measured["refusals"]["share_percent"] == 50.0
