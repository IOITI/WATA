import asyncio
import sys
import types
from datetime import date

import pytest

asyncpg_stub = types.ModuleType("asyncpg")
asyncpg_stub.Pool = object
asyncpg_stub.Record = dict
asyncpg_stub.create_pool = None
sys.modules.setdefault("asyncpg", asyncpg_stub)

from src.admin.errors import DatabaseUnavailable
from src.admin.queries import MAX_ROWS, AdminQueries, LazyDatabase, summarise_trades


def _trade(day, action, percent, profit, instrument="TURBO A"):
    return {
        "day_date": day,
        "action": action,
        "instrument_name": instrument,
        "performance_percent": percent,
        "profit_loss": profit,
    }


DAY_1 = date(2026, 10, 8)
DAY_2 = date(2026, 10, 9)


class FakeDb:
    def __init__(self, rows=None, value=0):
        self.rows = rows or []
        self.value = value
        self.calls = []

    async def fetch(self, query, *args):
        self.calls.append((query, args))
        return self.rows

    async def fetchrow(self, query, *args):
        self.calls.append((query, args))
        return self.rows[0] if self.rows else None

    async def fetchval(self, query, *args):
        self.calls.append((query, args))
        return self.value


def test_summary_of_no_trades_is_empty():
    result = summarise_trades([])

    assert result["daily"] == []
    assert result["by_action"] == []
    assert result["summary"]["trade_count"] == 0
    assert result["summary"]["win_rate"] is None
    assert result["summary"]["best_day"] is None


def test_daily_figures_are_grouped_and_compounded_per_day():
    trades = [
        _trade(DAY_1, "long", 10.0, 100.0),
        _trade(DAY_1, "short", -5.0, -40.0),
        _trade(DAY_2, "long", 2.0, 20.0),
    ]

    daily = summarise_trades(trades)["daily"]

    assert [day["day_date"] for day in daily] == [DAY_1, DAY_2]
    assert daily[0]["trade_count"] == 2
    assert daily[0]["win_count"] == 1
    assert daily[0]["loss_count"] == 1
    assert daily[0]["profit_loss"] == 60.0
    # 1.10 * 0.95 = 1.045, as the trader computes its day performance.
    assert daily[0]["performance_percent"] == 4.5
    assert daily[1]["performance_percent"] == 2.0


def test_cumulative_profit_continues_from_the_profit_made_before_the_range():
    trades = [_trade(DAY_1, "long", 1.0, 100.0), _trade(DAY_2, "long", -1.0, -30.0)]

    daily = summarise_trades(trades, profit_before=1000.0)["daily"]

    assert [day["cumulative_profit_loss"] for day in daily] == [1100.0, 1070.0]


def test_summary_and_breakdowns():
    trades = [
        _trade(DAY_1, "long", 10.0, 100.0, "TURBO A"),
        _trade(DAY_1, "short", -5.0, -40.0, "TURBO B"),
        _trade(DAY_2, "long", 2.0, 20.0, "TURBO A"),
        _trade(DAY_2, "long", 0.0, 0.0, "TURBO A"),
    ]

    result = summarise_trades(trades)

    summary = result["summary"]
    assert summary["trade_count"] == 4
    assert summary["win_count"] == 2
    assert summary["loss_count"] == 1
    assert summary["win_rate"] == 50.0
    assert summary["profit_loss"] == 80.0
    assert summary["trading_days"] == 2
    assert summary["winning_days"] == 2
    assert summary["best_day"] == {"day_date": DAY_1, "profit_loss": 60.0}
    assert summary["worst_day"] == {"day_date": DAY_2, "profit_loss": 20.0}

    by_action = {entry["action"]: entry for entry in result["by_action"]}
    assert by_action["long"]["trade_count"] == 3
    assert by_action["long"]["profit_loss"] == 120.0
    assert by_action["long"]["average_percent"] == 4.0
    assert by_action["short"]["loss_count"] == 1

    assert [(entry["instrument_name"], entry["profit_loss"]) for entry in result["by_instrument"]] == [
        ("TURBO B", -40.0),
        ("TURBO A", 120.0),
    ]


def test_missing_figures_count_as_zero():
    trades = [_trade(DAY_1, None, None, None, None)]

    result = summarise_trades(trades)

    assert result["summary"]["profit_loss"] == 0
    assert result["daily"][0]["performance_percent"] == 0.0
    assert result["by_action"][0]["action"] == "unknown"


def test_positions_are_filtered_by_status_and_local_day():
    db = FakeDb(rows=[{"position_id": "1", "position_profit_loss": float("nan")}], value=1)
    queries = AdminQueries(db, "Europe/Paris")

    result = asyncio.run(queries.list_positions(status="Closed", start=DAY_1, end=DAY_2, limit=50))

    count_query, count_args = db.calls[0]
    list_query, list_args = db.calls[1]
    assert count_args == list_args == ("Europe/Paris", DAY_1, DAY_2, "Closed")
    assert "AT TIME ZONE $1" in list_query
    assert "position_status = $4" in list_query
    assert "LIMIT 50" in list_query
    assert "COUNT(*)" in count_query
    assert result["total"] == 1
    # NaN cannot be serialised to JSON.
    assert result["items"][0]["position_profit_loss"] is None


def test_positions_without_filters_have_no_where_clause_and_a_bounded_limit():
    db = FakeDb()
    queries = AdminQueries(db, "Europe/Paris")

    asyncio.run(queries.list_positions(limit=10 ** 9))

    list_query, list_args = db.calls[1]
    assert "WHERE" not in list_query
    assert list_args == ()
    assert f"LIMIT {MAX_ROWS}" in list_query


def test_filter_values_are_bound_parameters_never_part_of_the_sql():
    db = FakeDb()
    queries = AdminQueries(db, "Europe/Paris")
    hostile = "Closed'; DROP TABLE turbo_data_position; --"

    asyncio.run(queries.list_positions(status=hostile))

    for query, args in db.calls:
        assert "DROP TABLE" not in query
        assert hostile in args


def test_position_detail_includes_its_related_orders():
    db = FakeDb(rows=[{"position_id": "5001", "order_id": "7001", "related_order_id": ["7002"]}])
    queries = AdminQueries(db, "Europe/Paris")

    result = asyncio.run(queries.get_position("5001"))

    orders_query, orders_args = db.calls[1]
    assert orders_args == ("5001", "7001", ["7002"])
    assert "turbo_data_order" in orders_query
    assert result["position"]["position_id"] == "5001"


def test_unknown_position_detail_is_none():
    queries = AdminQueries(FakeDb(), "Europe/Paris")

    assert asyncio.run(queries.get_position("nope")) is None


def test_performance_of_a_range_starts_from_the_profit_made_before_it():
    db = FakeDb(rows=[_trade(DAY_1, "long", 1.0, 100.0)], value=500.0)
    queries = AdminQueries(db, "Europe/Paris")

    result = asyncio.run(queries.performance(start=DAY_1, end=DAY_2))

    trades_query, trades_args = db.calls[0]
    assert trades_args == ("Europe/Paris", DAY_1, DAY_2)
    assert "position_status = 'Closed'" in trades_query
    assert result["daily"][0]["cumulative_profit_loss"] == 600.0


def test_all_time_performance_starts_from_zero():
    db = FakeDb(rows=[_trade(DAY_1, "long", 1.0, 100.0)], value=500.0)
    queries = AdminQueries(db, "Europe/Paris")

    result = asyncio.run(queries.performance())

    assert len(db.calls) == 1
    assert result["daily"][0]["cumulative_profit_loss"] == 100.0


class FakeConnMgr:
    def __init__(self, fail_connect=False):
        self.fail_connect = fail_connect
        self.connect_count = 0
        self.closed = False

    async def connect(self):
        self.connect_count += 1
        if self.fail_connect:
            raise OSError("connection refused")

    async def fetchval(self, query, *args):
        return 1

    async def close(self):
        self.closed = True


def test_lazy_database_connects_on_first_use():
    conn_mgr = FakeConnMgr()
    database = LazyDatabase(conn_mgr)
    assert conn_mgr.connect_count == 0

    assert asyncio.run(database.fetchval("SELECT 1")) == 1
    assert conn_mgr.connect_count == 1


def test_lazy_database_reports_an_unreachable_server():
    database = LazyDatabase(FakeConnMgr(fail_connect=True))

    with pytest.raises(DatabaseUnavailable):
        asyncio.run(database.fetchval("SELECT 1"))


def test_lazy_database_recovers_once_the_server_is_back():
    conn_mgr = FakeConnMgr(fail_connect=True)
    database = LazyDatabase(conn_mgr)
    with pytest.raises(DatabaseUnavailable):
        asyncio.run(database.fetchval("SELECT 1"))

    conn_mgr.fail_connect = False

    assert asyncio.run(database.fetchval("SELECT 1")) == 1
