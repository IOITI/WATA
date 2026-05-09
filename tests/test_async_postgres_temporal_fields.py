import asyncio
import sys
import types
from datetime import date, datetime, timezone

import pytest


asyncpg_stub = types.ModuleType("asyncpg")
asyncpg_stub.Pool = object
asyncpg_stub.Record = dict
asyncpg_stub.create_pool = None
sys.modules.setdefault("asyncpg", asyncpg_stub)

from src.database.postgres import (
    AsyncDbOrderManager,
    AsyncDbPositionManager,
    AsyncDbTradePerformanceManager,
)


class FakeConnMgr:
    def __init__(self):
        self.calls = []

    async def execute(self, query, *args):
        self.calls.append((query, args))
        return "OK"


def test_async_order_manager_normalizes_iso_datetime_strings():
    conn_mgr = FakeConnMgr()
    manager = AsyncDbOrderManager(conn_mgr)

    asyncio.run(manager.insert_turbo_order_data({
        "action": "short",
        "buy_sell": "Buy",
        "order_id": "5397119987",
        "order_amount": 6,
        "order_type": "Market",
        "order_kind": "main",
        "order_submit_time": "2026-04-27T19:42:58Z",
        "related_order_id": [],
        "position_id": "pos_123",
        "instrument_name": "MiniFuture",
        "instrument_symbol": "MF-123",
        "instrument_uic": 55341056,
        "instrument_price": 4.255,
        "instrument_currency": "EUR",
        "order_cost": 0.25,
    }))

    _, args = conn_mgr.calls[0]
    assert args[6] == datetime(2026, 4, 27, 19, 42, 58, tzinfo=timezone.utc)


def test_async_position_manager_normalizes_iso_datetime_strings_for_insert_and_update():
    conn_mgr = FakeConnMgr()
    manager = AsyncDbPositionManager(conn_mgr)

    asyncio.run(manager.insert_turbo_open_position_data({
        "action": "short",
        "position_id": "pos_123",
        "position_amount": 6,
        "position_open_price": 4.255,
        "position_total_open_price": 25.53,
        "position_status": "Open",
        "position_kind": "main",
        "execution_time_open": "2026-04-27T19:42:58Z",
        "order_id": "5397119987",
        "related_order_id": [],
        "instrument_name": "MiniFuture",
        "instrument_symbol": "MF-123",
        "instrument_uic": 55341056,
        "instrument_currency": "EUR",
    }))

    _, insert_args = conn_mgr.calls[0]
    assert insert_args[7] == datetime(2026, 4, 27, 19, 42, 58, tzinfo=timezone.utc)

    asyncio.run(manager.update_turbo_position_data("pos_123", {
        "position_status": "Closed",
        "execution_time_close": "2026-04-27T20:01:10Z",
    }))

    _, update_args = conn_mgr.calls[1]
    assert update_args[1] == datetime(2026, 4, 27, 20, 1, 10, tzinfo=timezone.utc)


def test_async_trade_performance_manager_normalizes_date_strings():
    conn_mgr = FakeConnMgr()
    manager = AsyncDbTradePerformanceManager(conn_mgr)

    asyncio.run(manager.insert_trade_performance_data({
        "date_day": "2026-04-27T19:42:58Z",
        "perf_day_real": 1.5,
        "money_made_real": 12.0,
        "trade_number_real": 3,
        "max_perf_day_simulated": 2.1,
    }))

    _, args = conn_mgr.calls[0]
    assert args[0] == date(2026, 4, 27)