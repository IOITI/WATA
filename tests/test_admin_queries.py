import asyncio
import sys
import types
from datetime import date, datetime, timedelta, timezone

import pytest

asyncpg_stub = types.ModuleType("asyncpg")
asyncpg_stub.Pool = object
asyncpg_stub.Record = dict
asyncpg_stub.create_pool = None
sys.modules.setdefault("asyncpg", asyncpg_stub)

from src.admin.errors import DatabaseUnavailable
from src.admin.queries import MAX_REPLAYED_ROWS, MAX_ROWS, AdminQueries, LazyDatabase, summarise_trades
from src.admin.ta_monitor import DELIVERY_STEPS
from src.ta_engine.signals import CLOSE_WITH_ENTRY


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


def test_the_journal_of_a_period_is_read_oldest_first_for_a_replay():
    db = FakeDb(rows=[{"signal_uuid": "s1", "confidence": float("nan")}])
    queries = AdminQueries(db, "Europe/Paris")

    rows = asyncio.run(queries.journal_between(DAY_1, DAY_2))

    query, args = db.calls[0]
    assert args == ("Europe/Paris", DAY_1, DAY_2)
    assert "FROM signal_journal" in query and "(decided_at AT TIME ZONE $1)::date >= $2::date" in query
    assert "ORDER BY decided_at ASC" in query and f"LIMIT {MAX_REPLAYED_ROWS}" in query
    assert rows == [{"signal_uuid": "s1", "confidence": None}]

    asyncio.run(queries.journal_between())
    assert "WHERE" not in db.calls[1][0] and db.calls[1][1] == ()


def test_a_journal_that_does_not_exist_yet_reads_as_empty():
    class NoJournal(FakeDb):
        async def fetch(self, query, *args):
            raise DatabaseUnavailable('relation "signal_journal" does not exist')

    assert asyncio.run(AdminQueries(NoJournal(), "Europe/Paris").journal_between()) == []


def test_closed_positions_are_read_with_what_each_was_bought_and_sold_at():
    db = FakeDb(rows=[{"position_id": "1", "position_open_price": 4.4}])
    queries = AdminQueries(db, "Europe/Paris")

    rows = asyncio.run(queries.closed_positions(DAY_1, DAY_2))

    query, args = db.calls[0]
    assert args == ("Europe/Paris", DAY_1, DAY_2)
    assert "position_status = 'Closed'" in query and "execution_time_close IS NOT NULL" in query
    # A position belongs to the day it was opened on: the day of the signal it came from.
    assert "(execution_time_open AT TIME ZONE $1)::date >= $2::date" in query
    assert "position_open_price, position_close_price" in query
    assert "ORDER BY execution_time_open ASC" in query and f"LIMIT {MAX_REPLAYED_ROWS}" in query
    assert rows == [{"position_id": "1", "position_open_price": 4.4}]

    asyncio.run(queries.closed_positions())
    assert db.calls[1][1] == ()


def test_position_detail_includes_its_related_orders():
    db = FakeDb(rows=[{"position_id": "5001", "order_id": "7001", "related_order_id": ["7002"]}])
    queries = AdminQueries(db, "Europe/Paris")

    result = asyncio.run(queries.get_position("5001"))

    orders_query, orders_args = db.calls[1]
    assert orders_args == ("5001", "7001", ["7002"])
    assert "turbo_data_order" in orders_query
    assert result["position"]["position_id"] == "5001"


LINKED_ORDER_ROW = {
    "order_id": "7001", "buy_sell": "Buy", "position_id": "5001", "instrument_price": float("nan"),
    "purpose": "open", "sent_at": "sent", "accepted_at": "accepted", "confirmed_at": "confirmed",
    "gave_up_at": None, "lookups": 2, "outcome": "confirmed", "cancel_accepted": None, "detail": None,
    "recorded_by": "trader",
    "position__position_id": "5001", "position__position_status": "Closed",
    "position__execution_time_open": "filled", "position__position_profit_loss": float("inf"),
    "signal__signal_uuid": "uuid-1", "signal__received_timestamp": "received",
}


class DbWithoutOrderLinks(FakeDb):
    """A database the trading services have not started on with this version: one table is not there yet."""

    def __init__(self, rows=None, missing="order_execution", error=None):
        super().__init__(rows)
        self.missing = missing
        self.error = error or f'relation "{missing}" does not exist'

    async def fetch(self, query, *args):
        if self.missing in query:
            self.calls.append((query, args))
            raise DatabaseUnavailable(self.error)
        return await super().fetch(query, *args)


def test_an_order_comes_with_what_became_of_it_its_position_and_its_signal():
    queries = AdminQueries(FakeDb(rows=[LINKED_ORDER_ROW], value=1), "Europe/Paris")

    result = asyncio.run(queries.list_orders())

    order = result["items"][0]
    assert (order["order_id"], order["outcome"], order["lookups"]) == ("7001", "confirmed", 2)
    assert (order["sent_at"], order["accepted_at"], order["confirmed_at"]) == ("sent", "accepted", "confirmed")
    assert order["position"] == {
        "position_id": "5001", "position_status": "Closed", "execution_time_open": "filled",
        # Infinity cannot be serialised to JSON, in a linked row either.
        "position_profit_loss": None,
    }
    assert order["signal"] == {"signal_uuid": "uuid-1", "received_timestamp": "received"}
    assert order["instrument_price"] is None
    assert not [key for key in order if "__" in key]


def test_an_order_with_no_position_and_no_signal_says_so():
    without_links = {**LINKED_ORDER_ROW, "position_id": None, "outcome": "gave_up"}
    without_links.update({key: None for key in LINKED_ORDER_ROW if "__" in key})
    queries = AdminQueries(FakeDb(rows=[without_links], value=1), "Europe/Paris")

    order = asyncio.run(queries.list_orders())["items"][0]

    assert (order["position"], order["signal"]) == (None, None)
    assert order["outcome"] == "gave_up"


def test_orders_are_read_with_their_links_filtered_by_local_day_and_bounded():
    db = FakeDb(value=3)
    queries = AdminQueries(db, "Europe/Paris")

    result = asyncio.run(queries.list_orders(start=DAY_1, end=DAY_2, limit=10 ** 9))

    count_query, count_args = db.calls[0]
    list_query, list_args = db.calls[1]
    assert count_args == list_args == ("Europe/Paris", DAY_1, DAY_2)
    assert "COUNT(*) FROM turbo_data_order o" in count_query
    assert "(o.order_time AT TIME ZONE $1)::date >= $2::date" in list_query
    for table in ("order_execution e", "turbo_data_position p", "signal_journal"):
        assert table in list_query
    # One signal at most per order, or an order would be listed twice.
    assert "DISTINCT ON (order_id)" in list_query
    assert f"LIMIT {MAX_ROWS}" in list_query
    # The requests sent to Saxo in the same days, the most recent first and bounded.
    requests_query, requests_args = db.calls[2]
    assert requests_args == ("Europe/Paris", DAY_1, DAY_2)
    assert "FROM order_request" in requests_query
    assert "(requested_at AT TIME ZONE $1)::date >= $2::date" in requests_query
    assert f"LIMIT {MAX_REPLAYED_ROWS}" in requests_query
    assert result == {"items": [], "total": 3, "refusals": []}


@pytest.mark.parametrize("missing", ["order_execution", "signal_journal"])
def test_orders_are_read_alone_until_the_trading_services_have_created_what_links_them(missing):
    db = DbWithoutOrderLinks(rows=[{"order_id": "7001", "position_id": "5001"}], missing=missing)
    queries = AdminQueries(db, "Europe/Paris")

    result = asyncio.run(queries.list_orders(start=DAY_1))

    plain_query, plain_args = next(
        call for call in db.calls if "FROM turbo_data_order o" in call[0] and "COUNT" not in call[0] and "JOIN" not in call[0]
    )
    assert "order_execution" not in plain_query and "signal_journal" not in plain_query
    assert "FROM turbo_data_order o" in plain_query
    assert plain_args == ("Europe/Paris", DAY_1)
    order = result["items"][0]
    assert (order["order_id"], order["position_id"]) == ("7001", "5001")
    # The shape of a linked order, with nothing known.
    assert (order["sent_at"], order["outcome"], order["position"], order["signal"]) == (None, None, None, None)


def test_a_database_that_fails_for_another_reason_is_not_read_as_orders_without_links():
    db = DbWithoutOrderLinks(error="connection refused")
    queries = AdminQueries(db, "Europe/Paris")

    with pytest.raises(DatabaseUnavailable, match="connection refused"):
        asyncio.run(queries.list_orders())


def test_position_detail_gives_its_orders_with_what_became_of_them():
    position = {"position_id": "5001", "order_id": "7001", "related_order_id": None}

    class Db(FakeDb):
        async def fetch(self, query, *args):
            self.calls.append((query, args))
            return [LINKED_ORDER_ROW, {**LINKED_ORDER_ROW, "order_id": "7002", "buy_sell": "Sell", "purpose": "close"}]

    db = Db(rows=[position])
    result = asyncio.run(AdminQueries(db, "Europe/Paris").get_position("5001"))

    orders_query, orders_args = db.calls[1]
    assert orders_args == ("5001", "7001", [])
    assert "o.position_id = $1 OR o.order_id = $2 OR o.order_id = ANY($3::text[])" in orders_query
    assert "ORDER BY o.order_time ASC" in orders_query
    assert [(order["order_id"], order["purpose"]) for order in result["orders"]] == [("7001", "open"), ("7002", "close")]
    assert result["orders"][0]["position"]["position_status"] == "Closed"


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


# ── the requests sent to Saxo's order endpoint ──


SENT = datetime(2026, 10, 14, 12, 2, 11, tzinfo=timezone.utc)
LIMIT_ONLY = "À ce stade, seuls des ordres limites sont autorisés"


def _sent(seconds, outcome="refused", **extra):
    refused = outcome == "refused"
    return {
        "request_id": f"request-{seconds}", "requested_at": SENT + timedelta(seconds=seconds),
        "operation": "place", "purpose": "close", "position_id": "5001", "outcome": outcome,
        "http_status": 400 if refused else None, "error_code": "OtherError" if refused else None,
        "error_message": LIMIT_ONLY if refused else None, "order_id": None, **extra,
    }


class TablesDb(FakeDb):
    """Rows by table: the first table of ``tables`` named in a query gives its rows."""

    def __init__(self, tables, value=0):
        super().__init__(value=value)
        self.tables = tables

    async def fetch(self, query, *args):
        self.calls.append((query, args))
        return next((rows for table, rows in self.tables.items() if table in query), [])

    async def fetchrow(self, query, *args):
        self.calls.append((query, args))
        rows = next((rows for table, rows in self.tables.items() if table in query), [])
        return rows[0] if rows else None


def test_orders_come_with_the_runs_saxo_refused_and_the_requests_that_belong_to_them():
    sale = {**LINKED_ORDER_ROW, "order_id": "7002", "buy_sell": "Sell", "purpose": "close"}
    requests = [_sent(0), _sent(1), _sent(2, outcome="accepted", order_id="7002")]
    db = TablesDb({
        "order_execution": [sale],
        # Newest first, as the query asks: read back oldest first.
        "order_request": list(reversed(requests)),
        "turbo_data_position": [{"position_id": "5001", "position_status": "Closed"}],
    }, value=1)

    result = asyncio.run(AdminQueries(db, "Europe/Paris").list_orders())

    [order] = result["items"]
    assert [request["request_id"] for request in order["requests"]] == ["request-2"]
    [run] = result["refusals"]
    assert (run["count"], run["error_message"], run["followed_by"]["order_id"]) == (2, LIMIT_ONLY, "7002")
    assert run["position"] == {"position_id": "5001", "position_status": "Closed"}
    assert run["signal"] is None
    position_query, position_args = next(call for call in db.calls if "FROM turbo_data_position WHERE" in call[0])
    assert position_args == (["5001"],)


def test_a_refused_buy_comes_with_its_signal_and_what_the_trader_made_of_it():
    buy = _sent(0, purpose="open", position_id=None, signal_uuid="uuid-1")
    journal = {"signal_uuid": "uuid-1", "outcome": "failed", "reason": "Order rejected: ..."}
    db = TablesDb({"order_execution": [], "order_request": [buy], "signal_journal WHERE signal_uuid": [journal]}, value=0)

    [run] = asyncio.run(AdminQueries(db, "Europe/Paris").list_orders())["refusals"]

    assert (run["signal_uuid"], run["position"], run["signal"]) == ("uuid-1", None, journal)
    signal_query, signal_args = next(call for call in db.calls if "FROM signal_journal WHERE signal_uuid" in call[0])
    assert "outcome, reason" in signal_query
    assert signal_args == (["uuid-1"],)


def test_orders_have_no_requests_until_the_trading_services_have_created_their_table():
    db = DbWithoutOrderLinks(rows=[LINKED_ORDER_ROW], missing="order_request")

    result = asyncio.run(AdminQueries(db, "Europe/Paris").list_orders())

    assert result["refusals"] == []
    assert result["items"][0]["requests"] == []


def test_position_detail_gives_every_request_about_it_oldest_first():
    position = {"position_id": "5001", "order_id": "7001", "related_order_id": None}
    requests = [_sent(0), _sent(1)]
    db = TablesDb({"order_execution": [], "order_request": list(reversed(requests)), "turbo_data_position": [position]})

    result = asyncio.run(AdminQueries(db, "Europe/Paris").get_position("5001"))

    requests_query, requests_args = next(call for call in db.calls if "FROM order_request" in call[0])
    assert requests_args == ("5001", "7001")
    assert "WHERE position_id = $1 OR order_id = $2" in requests_query
    assert [request["request_id"] for request in result["requests"]] == ["request-0", "request-1"]
    assert result["requests_total"] == 2


def test_position_detail_counts_the_requests_it_does_not_show(monkeypatch):
    monkeypatch.setattr("src.admin.queries.MAX_POSITION_REQUESTS", 2)
    position = {"position_id": "5001", "order_id": "7001", "related_order_id": None}
    db = TablesDb({"order_execution": [], "order_request": [_sent(1), _sent(0)], "turbo_data_position": [position]}, value=37)

    result = asyncio.run(AdminQueries(db, "Europe/Paris").get_position("5001"))

    assert (len(result["requests"]), result["requests_total"]) == (2, 37)
    count_query, count_args = db.calls[-1]
    assert "COUNT(*) FROM order_request WHERE position_id = $1 OR order_id = $2" in count_query
    assert count_args == ("5001", "7001")


def test_an_open_position_says_how_often_saxo_refused_to_sell_it():
    refused = {"position_id": "5001", "count": 37, "first_at": SENT, "last_at": SENT, "outcome": "refused",
               "http_status": 400, "error_code": "OtherError", "error_message": LIMIT_ONLY}
    db = TablesDb({
        "position_live_value": [],
        "FROM order_request": [refused],
        "turbo_data_position": [{"position_id": "5001", "position_status": "Open"}, {"position_id": "5002", "position_status": "Open"}],
    }, value=2)

    positions = asyncio.run(AdminQueries(db, "Europe/Paris").open_positions())

    assert positions[0]["sell_refusals"] == refused
    assert positions[1]["sell_refusals"] is None
    query, args = next(call for call in db.calls if "FROM order_request" in call[0])
    assert args == (["5001", "5002"],)
    assert "operation = 'place' AND purpose = 'close' AND outcome <> 'accepted'" in query


def test_open_positions_are_still_given_before_the_table_of_requests_exists():
    db = DbWithoutOrderLinks(rows=[{"position_id": "5001", "position_status": "Open"}], missing="order_request")

    positions = asyncio.run(AdminQueries(db, "Europe/Paris").open_positions())

    assert positions[0]["sell_refusals"] is None


# ── what the TA engine measured of itself (the Engine monitor) ──

SINCE = datetime(2026, 10, 22, 11, 45, tzinfo=timezone.utc)


def test_the_last_minutes_of_the_engine_are_read_in_five_statements_that_count_in_sql():
    db = FakeDb(rows=[{"tick_at": SINCE, "kept_since": SINCE - timedelta(days=3), "milliseconds": 180}])

    recent = asyncio.run(AdminQueries(db, "Europe/Paris").ta_monitor_recent(SINCE, slow_tick_ms=10_000))

    last, ticks, runs, deferred, signals = db.calls
    assert "FROM ta_engine_tick ORDER BY tick_at DESC LIMIT 1" in last[0] and last[1] == ()
    assert ticks[1] == (SINCE, 10_000) and "count(*) FILTER (WHERE milliseconds > $2 AND loads = 0 AND NOT restarted)" in ticks[0]
    # How far behind the feed was leaves out the engine's first tick after a start (a deploy stops the feed too).
    assert "FILTER (WHERE figures->'first' IS NULL) AS feed_lag_max_ms" in ticks[0]
    assert "GROUP BY rule_id, mode, runner" in runs[0] and runs[1] == (SINCE,)
    assert "unnest(deferred_rules)" in deferred[0]
    assert "WHERE mode = 'live' AND raised_at >= $1" in signals[0]
    # The oldest tick kept is said apart from the last one.
    assert recent["kept_since"] == SINCE - timedelta(days=3) and "kept_since" not in recent["last_tick"]


def test_a_period_is_read_by_bar_of_time_and_narrowed_to_one_rule_in_every_statement():
    db = FakeDb()
    start, end = SINCE - timedelta(hours=24), SINCE

    asyncio.run(AdminQueries(db, "Europe/Paris").ta_monitor_period(
        start, end, 600, "0123456789ab", signal_age_seconds=60, slow_tick_ms=10_000,
    ))

    ticks, runs, signals_by_bucket, rules, deferred, signals, delivery, trader, results, kept = db.calls
    assert "date_bin($3::interval, tick_at, TIMESTAMPTZ '2000-01-01 00:00:00+00')" in ticks[0]
    assert ticks[1] == (start, end, timedelta(seconds=600), 10_000)
    # Every statement but the ticks' (and the oldest tick's) is narrowed to the rule, as its last argument.
    for query, args in (runs, signals_by_bucket, rules, deferred, signals, delivery, trader, results):
        assert args[-1] == "0123456789ab" and f"= ${len(args)}" in query
    assert signals_by_bucket[1][3] == CLOSE_WITH_ENTRY and signals[1][2] == 60.0
    assert "LEFT JOIN ta_rule_run r ON r.rule_id = s.rule_id AND r.tick_at = s.raised_at" in delivery[0]
    for key, _, _ in DELIVERY_STEPS:
        assert f"('{key}', " in delivery[0]
    assert "JOIN turbo_data_position p ON p.position_id = j.position_id" in results[0]
    assert kept == ("SELECT min(tick_at) AS kept_since FROM ta_engine_tick", ())


def test_before_the_engine_measured_anything_its_monitor_reads_as_empty():
    class NoTables(FakeDb):
        async def fetch(self, query, *args):
            raise DatabaseUnavailable('relation "ta_rule_run" does not exist')

    queries = AdminQueries(NoTables(), "Europe/Paris")

    assert asyncio.run(queries.ta_monitor_recent(SINCE, slow_tick_ms=10_000)) == {
        "last_tick": None, "kept_since": None, "ticks": {}, "runs": [], "deferred": [], "signals": [],
    }
    period = asyncio.run(queries.ta_monitor_period(SINCE, SINCE, 600, signal_age_seconds=60, slow_tick_ms=10_000))
    assert all(value in (None, []) for value in period.values())
