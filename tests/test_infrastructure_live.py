"""
The database and the message queue for real: what the other tests replace with
fakes. Skipped unless an address is given, so that ``pytest tests/`` stays
what it was:

    WATA_TEST_POSTGRES_DSN=postgresql://wata:wata@localhost:5432/wata
    WATA_TEST_RABBITMQ=wata:wata@localhost            (user:password@host)

CI gives them a PostgreSQL and a RabbitMQ of its own on each Python version
(and sets WATA_TEST_REQUIRE_LIVE, which turns a missing address into a
failure). It is what shows, before a deploy, that the libraries that talk to
them (asyncpg, aio-pika, pika) still do after an upgrade.

Run this file on its own (``pytest tests/test_infrastructure_live.py``): in a
run of all the tests, other files have already put stand-ins in the place of
asyncpg and aio-pika, and these tests are skipped.

Only for a database and a broker that can be thrown away. The tables are made
in a schema of the test's own, dropped afterwards; the messages go to the
queues the services really read.
"""

import asyncio
import json
import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

POSTGRES_DSN = os.environ.get("WATA_TEST_POSTGRES_DSN")
RABBITMQ = os.environ.get("WATA_TEST_RABBITMQ")


def _cannot_run(why: str):
    if os.environ.get("WATA_TEST_REQUIRE_LIVE"):
        pytest.fail(f"{why}, and this run is meant to use it.")
    pytest.skip(f"{why}.")


def _needs(address, what, library):
    if not address:
        _cannot_run(f"No {what} given")
    # Other test files put an empty module in the place of the library they do not want to need.
    loaded = sys.modules.get(library)
    if loaded is not None and getattr(loaded, "__file__", None) is None:
        _cannot_run(f"{library} was replaced by a stand-in earlier in this run")


# ── PostgreSQL, through the managers the services use ──

T0 = datetime(2026, 10, 13, 7, 31, 2, 120000, tzinfo=timezone.utc)


def _order(order_id, side, position_id, sent, **more):
    return {
        "action": "long", "buy_sell": side, "order_id": order_id, "order_amount": 120,
        "order_type": "Market", "order_kind": "main" if side == "Buy" else "close",
        "order_submit_time": sent, "related_order_id": [], "position_id": position_id,
        "instrument_name": "TURBO LONG NASDAQ-100 KO 24150 SG", "instrument_symbol": "SG-NDX-L1",
        "instrument_uic": 40000002, "instrument_price": 9.31, "instrument_currency": "EUR", "order_cost": 1.12,
        **more,
    }


def _position(position_id, order_id):
    return {
        "action": "long", "position_id": position_id, "position_amount": 120, "position_open_price": 9.31,
        "position_total_open_price": 1117.2, "position_status": "Open", "position_kind": "main",
        # As Saxo writes a moment: a string, to the microsecond.
        "execution_time_open": "2026-10-13T07:31:02.297000Z", "order_id": order_id, "related_order_id": [],
        "instrument_name": "TURBO LONG NASDAQ-100 KO 24150 SG", "instrument_symbol": "SG-NDX-L1",
        "instrument_uic": 40000002, "instrument_currency": "EUR",
    }


@pytest.fixture
def in_a_database_of_its_own():
    """What runs ``scenario(writer, reader)`` on tables made in a new schema, dropped afterwards."""
    _needs(POSTGRES_DSN, "PostgreSQL", "asyncpg")
    return _in_a_database_of_its_own


def _in_a_database_of_its_own(scenario):
    from src.database.postgres import PostgresConnectionManager, init_schema

    schema = f"wata_test_{uuid.uuid4().hex[:12]}"

    async def run():
        setup = PostgresConnectionManager(POSTGRES_DSN, min_size=1, max_size=1)
        await setup.connect()
        await setup.execute(f'CREATE SCHEMA "{schema}"')
        writer = PostgresConnectionManager(
            POSTGRES_DSN, min_size=1, max_size=4, server_settings={"search_path": schema},
        )
        # The admin's way in: a pool that cannot write.
        reader = PostgresConnectionManager(
            POSTGRES_DSN, min_size=1, max_size=2,
            server_settings={"search_path": schema, "default_transaction_read_only": "on"},
        )
        try:
            await writer.connect()
            await init_schema(writer)
            return await scenario(writer, reader)
        finally:
            await writer.close()
            await reader.close()
            await setup.execute(f'DROP SCHEMA "{schema}" CASCADE')
            await setup.close()

    return asyncio.run(asyncio.wait_for(run(), timeout=60))


def test_the_schema_is_created_and_creating_it_again_changes_nothing(in_a_database_of_its_own):
    from src.database.postgres import init_schema

    async def scenario(writer, reader):
        await init_schema(writer)  # every start of the trader and of the monitor runs it again
        return await writer.fetch(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()"
        )

    tables = {row["table_name"] for row in in_a_database_of_its_own(scenario)}

    assert {"turbo_data_order", "turbo_data_position", "trade_performance", "trade_risk_state",
            "position_live_value", "signal_journal", "order_execution", "order_request",
            "account_balance_history"} <= tables


def test_a_trade_is_written_followed_and_closed_as_the_services_do(in_a_database_of_its_own):
    from src.database.postgres import AsyncDbOrderManager, AsyncDbPositionManager

    async def scenario(writer, reader):
        orders, positions = AsyncDbOrderManager(writer), AsyncDbPositionManager(writer)
        await asyncio.gather(
            orders.insert_turbo_order_data(_order("5012", "Buy", "5098", "2026-10-13T07:31:02Z")),
            positions.insert_turbo_open_position_data(_position("5098", "5012")),
        )
        # Written twice by mistake: the first one stays.
        await positions.insert_turbo_open_position_data({**_position("5098", "5012"), "position_amount": 1})
        open_now = await positions.get_open_positions_ids_actions()
        await positions.update_turbo_position_data("5098", {"position_max_performance_percent": 3.4})
        best = await positions.get_max_position_percent("5098")
        await positions.update_turbo_position_data("5098", {
            "position_close_price": 9.52, "position_profit_loss": 25.2, "position_total_close_price": 1142.4,
            "position_status": "Closed", "position_total_performance_percent": 2.26,
            "position_close_reason": "Performance (Trailing Stop)",
            "execution_time_close": "2026-10-13T08:02:11.270000Z",
        })
        row = await writer.fetchrow("SELECT * FROM turbo_data_position WHERE position_id = '5098'")
        return open_now, best, dict(row), await positions.get_open_positions_ids()

    open_now, best, row, open_after = in_a_database_of_its_own(scenario)

    assert open_now == [{"position_id": "5098", "action": "long"}]
    assert best == 3.4
    assert (row["position_amount"], row["position_status"], row["position_profit_loss"]) == (120, "Closed", 25.2)
    assert row["execution_time_open"] == datetime(2026, 10, 13, 7, 31, 2, 297000, tzinfo=timezone.utc)
    assert row["execution_time_close"] == datetime(2026, 10, 13, 8, 2, 11, 270000, tzinfo=timezone.utc)
    assert open_after == []


def test_what_became_of_an_order_is_kept_whichever_of_its_two_writes_lands_first(in_a_database_of_its_own):
    from src.database.postgres import AsyncDbOrderExecutionManager
    from src.trade.order_tracker import OPEN, OrderTrace

    async def scenario(writer, reader):
        executions = AsyncDbOrderExecutionManager(writer)
        trace = OrderTrace(OPEN)
        trace.sent()
        trace.accepted("5030")
        accepted_only = trace.as_entry("trader")
        trace.looked()
        trace.confirmed("5099")
        await executions.record(trace.as_entry("trader"))
        await executions.record(accepted_only)  # the earlier write, landing last
        await executions.record({**trace.as_entry("trader"), "outcome": "gave_up", "position_id": None})
        return trace, dict(await writer.fetchrow("SELECT * FROM order_execution WHERE order_id = '5030'"))

    trace, row = in_a_database_of_its_own(scenario)

    assert (row["outcome"], row["position_id"], row["lookups"]) == ("confirmed", "5099", 1)
    assert (row["sent_at"], row["confirmed_at"]) == (trace.sent_at, trace.confirmed_at)
    assert (row["purpose"], row["recorded_by"]) == ("open", "trader")


def test_the_tracker_and_the_admin_agree_on_an_order_from_its_signal_to_its_position(in_a_database_of_its_own):
    from src.admin.queries import AdminQueries, LazyDatabase
    from src.database.postgres import (
        AsyncDbOrderExecutionManager, AsyncDbOrderManager, AsyncDbPositionManager, AsyncDbSignalJournalManager,
    )
    from src.trade.order_tracker import CLOSE, OPEN, OrderTrace, OrderTracker

    def trace_of(purpose, order_id, sent, position_id=None):
        trace = OrderTrace(purpose, position_id=position_id)
        trace.sent_at, trace.accepted_at, trace.order_id = sent, sent + timedelta(milliseconds=190), order_id
        return trace

    async def scenario(writer, reader):
        orders, positions = AsyncDbOrderManager(writer), AsyncDbPositionManager(writer)
        tracker = OrderTracker(AsyncDbOrderExecutionManager(writer), orders, recorded_by="trader")

        # An entry that gave a position: the trade writes its order, the tracker what became of it.
        buy = trace_of(OPEN, "5012", T0)
        tracker.record(buy)
        buy.lookups = 2
        buy.confirmed("5098")
        tracker.record(buy)
        await orders.insert_turbo_order_data(_order("5012", "Buy", "5098", "2026-10-13T07:31:02Z"))
        await positions.insert_turbo_open_position_data(_position("5098", "5012"))
        await AsyncDbSignalJournalManager(writer).record_decision({
            "signal_uuid": "uuid-1", "action": "long", "indice": "us100", "confidence": 0.9,
            "signal_timestamp": "2026-10-13T07:31:01.500Z", "received_timestamp": "2026-10-13T07:31:01.950Z",
            "decided_at": T0 + timedelta(seconds=1), "outcome": "executed", "position_id": "5098", "order_id": "5012",
        })

        # Its sale: the order itself is the tracker's to write, with what the stream did not say left empty.
        sold_at = T0 + timedelta(minutes=31)
        sell = trace_of(CLOSE, "5013", sold_at, position_id="5098")
        tracker.record(sell, _order("5013", "Sell", "5098", sold_at, action=None, instrument_name=None,
                                    instrument_symbol=None, instrument_currency=None, order_cost=None))
        sell.lookups = 1
        sell.confirmed()
        tracker.record(sell)

        # An entry that gave no position, its cancellation refused.
        lost = trace_of(OPEN, "5020", T0 + timedelta(hours=2))
        lost.lookups = 14
        lost.gave_up(detail="SaxoApiError: Order not found", cancel_accepted=False)
        tracker.record(lost, _order("5020", "Buy", None, "2026-10-13T09:31:02Z"))
        await tracker.wait_for_pending()

        queries = AdminQueries(LazyDatabase(reader), "Europe/Paris")
        listing = await queries.list_orders()
        day = await queries.list_orders(start=date(2026, 10, 13), end=date(2026, 10, 13), limit=2)
        none = await queries.list_orders(start=date(2026, 10, 14))
        detail = await queries.get_position("5098")
        json.dumps(listing, default=str)  # what the route sends must be writable as JSON
        return listing, day, none, detail

    listing, day, none, detail = in_a_database_of_its_own(scenario)
    by_id = {order["order_id"]: order for order in listing["items"]}

    assert [order["order_id"] for order in listing["items"]] == ["5020", "5013", "5012"]
    assert (listing["total"], day["total"], len(day["items"]), none["total"]) == (3, 3, 2, 0)

    buy = by_id["5012"]
    assert (buy["purpose"], buy["outcome"], buy["lookups"], buy["sent_at"]) == ("open", "confirmed", 2, T0)
    assert buy["position"]["execution_time_open"] == datetime(2026, 10, 13, 7, 31, 2, 297000, tzinfo=timezone.utc)
    assert buy["signal"]["signal_uuid"] == "uuid-1"

    sell = by_id["5013"]
    assert (sell["purpose"], sell["outcome"], sell["signal"]) == ("close", "confirmed", None)
    # What the sell order could not say of itself comes from the position it closed.
    assert (sell["action"], sell["instrument_name"]) == ("long", "TURBO LONG NASDAQ-100 KO 24150 SG")

    lost = by_id["5020"]
    assert (lost["position"], lost["outcome"], lost["cancel_accepted"]) == (None, "gave_up", False)
    assert lost["detail"] == "SaxoApiError: Order not found"

    assert [(o["order_id"], o["purpose"]) for o in detail["orders"]] == [("5012", "open"), ("5013", "close")]


def test_the_requests_saxo_refused_are_read_back_as_the_pages_show_them(in_a_database_of_its_own):
    from src.admin.queries import AdminQueries, LazyDatabase
    from src.database.postgres import (
        AsyncDbOrderExecutionManager, AsyncDbOrderManager, AsyncDbOrderRequestManager, AsyncDbPositionManager,
    )
    from src.saxo_openapi.exceptions import OpenAPIError
    from src.trade.order_tracker import CLOSE, PLACE, OrderContext, OrderRequest, OrderTracker

    limit_only = {"ErrorInfo": {"ErrorCode": "OtherError", "Message": "À ce stade, seuls des ordres limites sont autorisés"}}
    sell = {"Uic": 40000002, "AssetType": "WarrantOpenEndKnockOut", "Amount": 120, "BuySell": "Sell",
            "OrderType": "Market", "AccountKey": "account-secret"}
    context = OrderContext(purpose=CLOSE, position_id="5098", action="long", reason="Performance (Stoploss)",
                           instrument_name="TURBO LONG NASDAQ-100 KO 24150 SG", price_seen=8.66)

    def request_at(offset_seconds):
        request = OrderRequest(PLACE, sell, context)
        request.requested_at = T0 + timedelta(seconds=offset_seconds)
        return request

    async def scenario(writer, reader):
        orders, positions = AsyncDbOrderManager(writer), AsyncDbPositionManager(writer)
        tracker = OrderTracker(
            AsyncDbOrderExecutionManager(writer), orders, recorded_by="position_monitor",
            db_request_manager=AsyncDbOrderRequestManager(writer),
        )
        await positions.insert_turbo_open_position_data(_position("5098", "5012"))
        for offset in range(3):
            refused = request_at(offset)
            refused.failed(OpenAPIError(400, "Bad Request", json.dumps(limit_only)))
            tracker.record_request(refused)
        await tracker.wait_for_pending()

        queries = AdminQueries(LazyDatabase(reader), "Europe/Paris")
        still_open = await queries.open_positions()

        sold = request_at(4)
        sold.accepted({"OrderId": "5013"}, order_id="5013")
        tracker.record_request(sold)
        tracker.record_request(sold)  # written twice: the first one stays
        await orders.insert_turbo_order_data(_order("5013", "Sell", "5098", T0 + timedelta(seconds=4)))
        await tracker.wait_for_pending()

        listing = await queries.list_orders()
        detail = await queries.get_position("5098")
        json.dumps(listing, default=str)
        stored = await writer.fetchval("SELECT request_body FROM order_request WHERE order_id = '5013'")
        return still_open, listing, detail, stored

    still_open, listing, detail, stored = in_a_database_of_its_own(scenario)

    refusals = still_open[0]["sell_refusals"]
    assert (refusals["count"], refusals["outcome"], refusals["http_status"], refusals["error_code"]) == (3, "refused", 400, "OtherError")
    assert refusals["error_message"] == "À ce stade, seuls des ordres limites sont autorisés"
    assert (refusals["first_at"], refusals["last_at"]) == (T0, T0 + timedelta(seconds=2))

    [run] = listing["refusals"]
    assert (run["count"], run["position_id"], run["followed_by"]["order_id"]) == (3, "5098", "5013")
    assert (run["instrument_price"], run["reason"], run["position"]["position_status"]) == (8.66, "Performance (Stoploss)", "Open")
    [order] = listing["items"]
    assert [(request["outcome"], request["order_id"]) for request in order["requests"]] == [("accepted", "5013")]

    assert [request["outcome"] for request in detail["requests"]] == ["refused", "refused", "refused", "accepted"]
    assert detail["requests_total"] == 4
    assert "account-secret" not in stored


def test_the_assets_the_recorder_kept_are_listed_as_the_data_pages_read_them(in_a_database_of_its_own):
    from src.admin.queries import AdminQueries, LazyDatabase
    from src.database.postgres import AsyncDbPriceHistoryManager, init_price_history_schema

    async def scenario(writer, reader):
        await init_price_history_schema(writer)
        history = AsyncDbPriceHistoryManager(writer)
        # Two months of partitions: the index's bars are in both.
        await history.ensure_partitions(date(2026, 9, 1), months_ahead=1)
        index = await history.upsert_instrument(
            {"uic": 1909050, "asset_type": "StockIndex", "role": "underlying", "indice": "us100"},
            datetime(2026, 9, 14, 6, tzinfo=timezone.utc),
        )
        await history.upsert_instrument({"uic": 1909050, "asset_type": "StockIndex", "role": "underlying"}, T0)
        turbo = await history.upsert_instrument(
            {"uic": 40000002, "asset_type": "WarrantOpenEndKnockOut", "role": "turbo", "indice": "us100",
             "direction": "long", "barrier": 24150.0, "issuer": "SG"},
            datetime(2026, 10, 12, 6, tzinfo=timezone.utc),
        )
        # Named by the recorder once, then nothing kept of it: retention dropped its month.
        await history.upsert_instrument(
            {"uic": 40000003, "asset_type": "WarrantOpenEndKnockOut", "role": "turbo", "indice": "us100"},
            datetime(2026, 8, 3, 6, tzinfo=timezone.utc),
        )
        bar = lambda ts, instrument, bid: (ts, instrument, bid, bid, bid, bid, bid + 0.05, bid + 0.05, bid + 0.05, bid + 0.05, 1, 0)
        await history.insert_bars([
            bar(datetime(2026, 9, 14, 6, 0, 10, tzinfo=timezone.utc), index, 24800.0),
            bar(datetime(2026, 10, 13, 7, 30, tzinfo=timezone.utc), index, 25010.0),
            # A turbo's first bar can come before the recorder names it at its next refresh.
            bar(datetime(2026, 10, 12, 5, 59, 50, tzinfo=timezone.utc), turbo, 8.40),
            bar(datetime(2026, 10, 12, 19, 59, 50, tzinfo=timezone.utc), turbo, 8.65),
        ])
        await history.record_volumes([(turbo, datetime(2026, 10, 12, 9, tzinfo=timezone.utc), 1200.0),
                                      (turbo, datetime(2026, 10, 12, 15, tzinfo=timezone.utc), 1500.0)])

        queries = AdminQueries(LazyDatabase(reader), "Europe/Paris")
        listed = await queries.price_assets(100)
        recent = await queries.price_assets(100, since=datetime(2026, 10, 13, tzinfo=timezone.utc))
        one = await queries.price_assets(1, instrument_id=turbo)
        index_of = await queries.price_index_of(
            "us100", datetime(2026, 10, 12, 22, tzinfo=timezone.utc), datetime(2026, 10, 13, 22, tzinfo=timezone.utc),
        )
        volumes = await queries.price_volume_days(
            turbo, datetime(2026, 10, 11, 22, tzinfo=timezone.utc), datetime(2026, 10, 12, 22, tzinfo=timezone.utc),
        )
        return index, turbo, listed, recent, one, index_of, volumes

    index, turbo, listed, recent, one, index_of, volumes = in_a_database_of_its_own(scenario)

    # The index first, then the turbo; the one without a bar left is not listed.
    assert [row["id"] for row in listed] == [index, turbo]
    assert (listed[0]["first_t"], listed[0]["last_t"], listed[0]["last_bid"]) == (
        int(datetime(2026, 9, 14, 6, 0, 10, tzinfo=timezone.utc).timestamp()),
        int(datetime(2026, 10, 13, 7, 30, tzinfo=timezone.utc).timestamp()),
        25010.0,
    )
    assert listed[1]["first_t"] == int(datetime(2026, 10, 12, 5, 59, 50, tzinfo=timezone.utc).timestamp())
    assert (listed[1]["last_bid"], listed[1]["last_ask"]) == (8.65, pytest.approx(8.70))
    assert [row["id"] for row in recent] == [index]
    assert [row["id"] for row in one] == [turbo]
    assert index_of["id"] == index
    assert volumes == {"2026-10-12": 1500.0}


def test_a_backtest_reads_a_day_of_recorded_turbos_as_the_recorder_wrote_it(in_a_database_of_its_own):
    from src.backtest.clock import TradingClock
    from src.backtest.data import DatabaseData
    from src.database.postgres import AsyncDbPriceHistoryManager, init_price_history_schema

    day = date(2026, 10, 14)
    start, end = TradingClock("Europe/Paris").day_bounds(day)
    seven = datetime(2026, 10, 14, 7, 0, tzinfo=timezone.utc)

    async def scenario(writer, reader):
        await init_price_history_schema(writer)
        history = AsyncDbPriceHistoryManager(writer)
        await history.ensure_partitions(date(2026, 10, 1))
        turbo = lambda uic, direction, barrier: {  # noqa: E731
            "uic": uic, "asset_type": "WarrantOpenEndKnockOut", "role": "turbo", "indice": "us100", "symbol": f"S{uic}",
            "description": f"NASDAQ 100 Turbo {direction} {barrier} SG", "currency": "EUR", "direction": direction,
            "barrier": barrier, "issuer": "SG",
        }
        index = await history.upsert_instrument(
            {"uic": 1909050, "asset_type": "StockIndex", "role": "underlying", "indice": "us100"}, seven,
        )
        long_id = await history.upsert_instrument(turbo(40000001, "long", 24800.0), seven - timedelta(days=1))
        short_id = await history.upsert_instrument(turbo(40000002, "short", 25200.0), seven)
        # Seen at a refresh of that day, but nothing kept of it.
        await history.upsert_instrument(turbo(40000003, "long", 24900.0), seven)
        bar = lambda ts, instrument, bid, ask: (ts, instrument, bid, bid + 0.1, bid - 0.1, bid, ask, ask, ask, ask, 1, 0)  # noqa: E731
        await history.insert_bars([
            bar(seven - timedelta(days=1), long_id, 4.0, 4.05),
            bar(seven, long_id, 5.0, 5.05),
            bar(seven + timedelta(seconds=10), long_id, 5.1, None),
            bar(seven, short_id, 6.0, 6.05),
            bar(seven, index, 25010.0, 25010.0),
        ])
        await history.record_coverage(seven, True, 3, 3)
        await history.record_coverage(seven + timedelta(seconds=10), False, 3, 1)
        # As the backtest's command reads: a connection that cannot write.
        await reader.connect()
        recorded = await DatabaseData(reader).day("us100", day, start, end)
        # What the form reads of the same days, through the admin's queries.
        from src.admin.queries import AdminQueries, LazyDatabase

        queries = AdminQueries(LazyDatabase(reader), "Europe/Paris")
        coverage = (
            await queries.backtest_turbo_days("us100", date(2026, 10, 13), day),
            await queries.backtest_recorder_days(date(2026, 10, 13), day),
            await queries.backtest_asset_days("saxo", index, date(2026, 10, 13), day),
            await queries.last_balance(),
        )
        return recorded, coverage

    recorded, (turbo_days, recorder_days, index_days, balance) = in_a_database_of_its_own(scenario)

    (long_series,) = recorded.turbos["long"]
    (short_series,) = recorded.turbos["short"]
    first = int(seven.timestamp())
    assert list(long_series.ts) == [first, first + 10]
    assert list(long_series.bid_open) == [5.0, 5.1] and long_series.bid_high[0] == pytest.approx(5.1)
    assert long_series.ask_open[0] == pytest.approx(5.05) and long_series.ask_open[1] != long_series.ask_open[1]
    assert (long_series.ref.barrier, long_series.ref.issuer, long_series.ref.uic) == (24800.0, "SG", 40000001)
    assert list(short_series.ts) == [first]
    assert recorded.covered == {first}
    # The day before held a bar of the long turbo only; the turbo seen without a bar is not counted.
    assert [(row["day"].isoformat(), row["direction"], row["turbos"]) for row in turbo_days] == [
        ("2026-10-13", "long", 1), ("2026-10-14", "long", 1), ("2026-10-14", "short", 1),
    ]
    assert turbo_days[1]["first_ts"] == seven and turbo_days[1]["last_ts"] == seven + timedelta(seconds=10)
    assert [(row["day"].isoformat(), row["listened"]) for row in recorder_days] == [("2026-10-14", 1)]
    assert [(row["day"].isoformat(), row["bars"]) for row in index_days] == [("2026-10-14", 1)]
    assert balance is None


def test_what_the_capital_com_feed_writes_is_what_the_data_pages_read(in_a_database_of_its_own):
    from src.admin.queries import AdminQueries, LazyDatabase
    from src.database.postgres import AsyncDbCapitalManager, init_capital_schema

    start = datetime(2026, 10, 13, 6, 0, tzinfo=timezone.utc)

    async def scenario(writer, reader):
        await init_capital_schema(writer)
        await init_capital_schema(writer)  # each start of the feed runs it again
        store = AsyncDbCapitalManager(writer)
        await store.ensure_partitions(start.date())
        us100 = await store.upsert_instrument(
            {"epic": "US100", "label": "us100", "name": "US Tech 100", "instrument_type": "INDICES",
             "currency": "USD", "decimals": 1, "opening_hours": {"mon": ["00:00 - 21:59"], "zone": "UTC"}},
            start,
        )
        # Described again without its details: what was known is kept.
        assert await store.upsert_instrument({"epic": "US100", "label": "us100"}, start + timedelta(hours=1)) == us100
        await store.insert_bars([
            (start + timedelta(seconds=10 * n), us100, 25000.0 + n, 25001.0 + n, 24999.0 + n, 25000.5 + n,
             25001.8 + n, 25002.8 + n, 25000.8 + n, 25002.3 + n, 3, 0)
            for n in range(12)
        ])
        await store.record_coverage(start, True, 1, 1)
        await store.upsert_minutes([(us100, start, 1, 2, 0.5, 1.5, 1.1, 2.1, 0.6, 1.6, 168.0)])
        await store.upsert_minutes([(us100, start, 1, 2, 0.5, 1.5, 1.1, 2.1, 0.6, 1.6, 187.0)])
        await store.write_state({
            "updated_at": start, "started_at": start, "version": "test", "environment": "demo", "state": "streaming",
            "detail": "Streaming US100", "figures": {"quotes_per_minute": 240, "delay_ms": {"p50": 38.0}},
        })

        queries = AdminQueries(LazyDatabase(reader), "Europe/Paris")
        listed = await queries.capital_assets()
        bars, through = await queries.capital_bars(us100, start, start + timedelta(hours=1), 60, 100)
        state = await queries.capital_feed_state()
        volume = await queries.capital_volume_days(us100, start - timedelta(days=1), start + timedelta(days=1))
        coverage = await queries.capital_coverage(start, start + timedelta(minutes=1))
        hours = await queries.capital_opening_hours("US100")
        return us100, listed, bars, through, state, volume, coverage, hours

    us100, listed, bars, through, state, volume, coverage, hours = in_a_database_of_its_own(scenario)

    assert [(row["id"], row["epic"], row["name"], row["decimals"]) for row in listed] == [(us100, "US100", "US Tech 100", 1)]
    assert listed[0]["last_t"] == int((start + timedelta(seconds=110)).timestamp())
    # A minute of six ten-second bars, then the next: and the last ten seconds they hold.
    assert [(bar[0], bar[9]) for bar in bars] == [(int(start.timestamp()), 18), (int(start.timestamp()) + 60, 18)]
    assert through == int((start + timedelta(seconds=110)).timestamp())
    assert (state["state"], state["figures"]["delay_ms"]["p50"]) == ("streaming", 38.0)
    assert volume == {"2026-10-13": 187.0}
    assert coverage == [{"ts": start, "feed_ok": True}]
    assert hours == {"mon": ["00:00 - 21:59"], "zone": "UTC"}


def test_what_the_ta_engine_journals_is_what_the_signals_page_reads(in_a_database_of_its_own):
    from src.admin.queries import AdminQueries, LazyDatabase
    from src.database.postgres import AsyncDbSignalJournalManager, AsyncDbTaManager, init_ta_schema

    bar = datetime(2026, 10, 13, 13, 30, tzinfo=timezone.utc)

    def signal(rule_id, alert_key, action, mode, outcome, at):
        return {
            "rule_id": rule_id, "rule_name": f"Rule {rule_id[:2]}", "script_id": "0123456789ab", "script_version": 3,
            "asset": "capital/US100", "side": "bid", "bars": "1", "bar_time": at, "bar_closed_at": at + timedelta(minutes=1),
            "alert_key": alert_key, "alert_title": alert_key, "alert_message": '{"action": "long"}', "action": action,
            "indice": "us100", "confidence": None, "mode": mode, "raised_at": at + timedelta(minutes=1, seconds=1),
            "outcome": outcome, "detail": None,
        }

    async def scenario(writer, reader):
        await init_ta_schema(writer)
        await init_ta_schema(writer)  # each start of the engine runs it again
        store = AsyncDbTaManager(writer)
        live = await store.journal_signal(signal("aaaaaaaaaaaa", "Enter Long", "long", "live", "sending", bar))
        # The same alert of the same bar, evaluated again after a restart: not journaled, so not sent.
        again = await store.journal_signal(signal("aaaaaaaaaaaa", "Enter Long", "long", "live", "sending", bar))
        await store.complete_signal(
            live, "sent", sent_at=bar + timedelta(minutes=1, seconds=1), http_status=200,
            signal_uuid="5e1f2a3b-0000-4000-8000-000000000001",
        )
        shadow = await store.journal_signal(signal("bbbbbbbbbbbb", "Exit", "close-position", "shadow", "shadow", bar))
        await AsyncDbSignalJournalManager(writer).record_decision({
            "signal_uuid": "5e1f2a3b-0000-4000-8000-000000000001", "action": "long", "indice": "us100",
            "signal_timestamp": "2026-10-13T13:30:00Z", "received_timestamp": "2026-10-13T13:31:01.204Z",
            "decided_at": bar + timedelta(minutes=1, seconds=2), "outcome": "executed", "position_id": "5025001",
        })
        await store.write_state({
            "updated_at": bar, "started_at": bar, "version": "test", "state": "running",
            "detail": "2 rules", "figures": {"rules": 2},
        })
        await store.write_rule_states([
            {"rule_id": "aaaaaaaaaaaa", "updated_at": bar, "state": "running", "mode": "live", "script_version": 3,
             "bars": 900, "last_bar_time": bar, "last_alert_at": bar + timedelta(minutes=1), "figures": {"ms": 12}},
            {"rule_id": "cccccccccccc", "updated_at": bar, "state": "error", "detail": "No bars", "mode": "shadow"},
        ])
        # A rule removed from the file: its row goes.
        await store.write_rule_states([
            {"rule_id": "aaaaaaaaaaaa", "updated_at": bar, "state": "running", "mode": "live", "script_version": 3},
        ])

        await AsyncDbSignalJournalManager(writer).record_decision({
            "signal_uuid": "tradingview-0001", "action": "long", "indice": "us100",
            "signal_timestamp": "2026-10-13T13:30:00Z", "decided_at": bar + timedelta(minutes=1, seconds=4),
            "outcome": "refused", "reason": "Already in a position",
        })

        queries = AdminQueries(LazyDatabase(reader), "Europe/Paris")
        listed = await queries.ta_signals(start=date(2026, 10, 13), end=date(2026, 10, 13))
        of_one_rule = await queries.ta_signals(rule_id="bbbbbbbbbbbb")
        others = await queries.ta_other_signals("us100", date(2026, 10, 13))
        return live, again, shadow, listed, of_one_rule, await queries.ta_engine_state(), await queries.ta_rule_states(), others

    live, again, shadow, listed, of_one_rule, engine, rules, others = in_a_database_of_its_own(scenario)

    assert isinstance(live, int) and again is None and isinstance(shadow, int)
    sent = next(row for row in listed if row["id"] == live)
    assert (sent["outcome"], sent["http_status"], sent["signal_uuid"]) == ("sent", 200, "5e1f2a3b-0000-4000-8000-000000000001")
    # What the trader made of it, read through the uuid the web server gave.
    assert sent["trader"]["outcome"] == "executed" and sent["trader"]["position_id"] == "5025001"
    assert [row["id"] for row in of_one_rule] == [shadow] and of_one_rule[0]["trader"] is None
    assert (engine["state"], engine["figures"]) == ("running", {"rules": 2})
    assert list(rules) == ["aaaaaaaaaaaa"] and rules["aaaaaaaaaaaa"]["state"] == "running"
    # What the trader received for us100 from elsewhere than the engine: TradingView's.
    assert [row["signal_uuid"] for row in others] == ["tradingview-0001"]


def test_the_signals_from_elsewhere_are_read_before_the_ta_engine_ever_ran(in_a_database_of_its_own):
    from src.admin.queries import AdminQueries, LazyDatabase
    from src.database.postgres import AsyncDbSignalJournalManager

    async def scenario(writer, reader):
        await AsyncDbSignalJournalManager(writer).record_decision({
            "signal_uuid": "tradingview-0002", "action": "short", "indice": "us100",
            "signal_timestamp": "2026-10-13T14:00:00Z", "decided_at": datetime(2026, 10, 13, 14, 0, 2, tzinfo=timezone.utc),
            "outcome": "executed",
        })
        return await AdminQueries(LazyDatabase(reader), "Europe/Paris").ta_other_signals("us100", date(2026, 10, 13))

    assert [row["signal_uuid"] for row in in_a_database_of_its_own(scenario)] == ["tradingview-0002"]


def test_a_paper_run_reads_the_signals_a_rule_raised_with_an_action_the_newest_kept_oldest_first(in_a_database_of_its_own):
    from src.admin.queries import AdminQueries, LazyDatabase
    from src.database.postgres import AsyncDbTaManager, init_ta_schema

    def raised(rule_id, at, action, key):
        return {
            "rule_id": rule_id, "rule_name": "EMA", "script_id": "0123456789ab", "script_version": 4,
            "asset": "capital/US100", "side": "bid", "bars": "1", "bar_time": at - timedelta(minutes=1), "bar_closed_at": at,
            "alert_key": key, "alert_title": key, "alert_message": "{}", "action": action, "indice": "us100",
            "confidence": None, "mode": "shadow", "raised_at": at, "outcome": "shadow" if action else "refused", "detail": None,
        }

    async def scenario(writer, reader):
        await init_ta_schema(writer)
        store = AsyncDbTaManager(writer)
        rows = (
            # 23:30 on Monday 12 in Paris, then 00:30 on Tuesday 13 there.
            ("aaaaaaaaaaaa", datetime(2026, 10, 12, 21, 30, tzinfo=timezone.utc), "long", "a"),
            ("aaaaaaaaaaaa", datetime(2026, 10, 12, 22, 30, tzinfo=timezone.utc), "short", "b"),
            ("aaaaaaaaaaaa", datetime(2026, 10, 13, 13, 31, tzinfo=timezone.utc), "long", "c"),
            # A close raised with an entry: journaled without an action, never traded.
            ("aaaaaaaaaaaa", datetime(2026, 10, 13, 13, 41, tzinfo=timezone.utc), None, "d"),
            ("aaaaaaaaaaaa", datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc), "close-position", "e"),
            ("aaaaaaaaaaaa", datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc), "long", "f"),
            ("bbbbbbbbbbbb", datetime(2026, 10, 13, 10, 0, tzinfo=timezone.utc), "long", "g"),
        )
        for rule_id, at, action, key in rows:
            await store.journal_signal(raised(rule_id, at, action, key))
        queries = AdminQueries(LazyDatabase(reader), "Europe/Paris")
        return (
            await queries.rule_signals("aaaaaaaaaaaa", date(2026, 10, 13), date(2026, 10, 14)),
            await queries.rule_signals("aaaaaaaaaaaa", date(2026, 10, 13), date(2026, 10, 14), limit=2),
        )

    period, newest = in_a_database_of_its_own(scenario)

    assert [row["alert_key"] for row in period] == ["b", "c", "e"]
    assert [row["alert_key"] for row in newest] == ["c", "e"]
    assert period[0]["script_version"] == 4 and period[0]["trader"] is None


def test_what_the_ta_engine_measures_of_itself_is_what_its_monitor_reads_and_older_rows_go(in_a_database_of_its_own):
    from src.admin.queries import AdminQueries, LazyDatabase
    from src.database.postgres import AsyncDbSignalJournalManager, AsyncDbTaManager, init_ta_schema
    from src.ta_engine.monitoring import APPEND, LOAD, CheckMark, RunMark, TickMark

    now = datetime.now(timezone.utc).replace(microsecond=0)
    # A moment with a fraction of a second: a signal finds its run by it, to the microsecond.
    at = (now - timedelta(minutes=5)).timestamp() + 0.5

    difference = 'plot "plot_0" at 2026-10-22T09:12:30Z: 1.5 continued, 1.25 whole'

    def measured(moment, kind, milliseconds, *, checked=False):
        run = RunMark(rule_id="aaaaaaaaaaaa", mode="live", runner="live", kind=kind, tick_at=moment, started_at=moment + 0.01,
                      bars_given=1, bar_closed_at=moment - 1.5)
        run.answered(moment + 0.01 + milliseconds / 1000, {"milliseconds": milliseconds - 5, "bars": 900, "alerts": [], "continued": checked}, set())
        run.done_at = run.ended_at + 0.02
        tick = TickMark(moment, covered_until=int(moment - 1.5), rules_on=2, deferred=["bbbbbbbbbbbb"], runs=[run])
        if checked:
            # Its continued run checked against a whole run, which gave otherwise.
            check = CheckMark(rule_id="aaaaaaaaaaaa", mode="live", runner="live", started_at=run.done_at)
            check.answered(run.done_at + 2.4, {"milliseconds": 2390, "bars": 900, "compared": 12, "same": False, "differences": [difference]})
            tick.checks.append(check)
        ended = moment + 0.2 + (2.4 if checked else 0)
        return tick.row(ended, {"memory_used_mb": 500.0, "memory_limit_mb": 1024.0, "oom_kills": 0}), run.row()

    old_tick, old_run = measured(at - 9 * 86_400, LOAD, 400)
    # The engine's first tick after a start, the feed stopped with it 99 s before.
    started = TickMark(at - 30, covered_until=int(at - 129), first=True).row(at - 29.9, {})
    tick, run = measured(at, APPEND, 60, checked=True)

    async def scenario(writer, reader):
        await init_ta_schema(writer)
        store = AsyncDbTaManager(writer)
        await store.write_monitoring([old_tick, started, tick], [old_run, run])
        await store.write_monitoring([tick], [run])  # written again: kept as it was
        signal_id = await store.journal_signal({
            "rule_id": "aaaaaaaaaaaa", "rule_name": "Rule aa", "script_id": "0123456789ab", "script_version": 3,
            "asset": "capital/US100", "side": "bid", "bars": "1", "bar_time": run["bar_closed_at"] - timedelta(minutes=1),
            "bar_closed_at": run["bar_closed_at"], "alert_key": "Enter Long", "alert_title": "Enter Long",
            "alert_message": "", "action": "long", "indice": "us100", "confidence": None, "mode": "live",
            "raised_at": run["tick_at"], "outcome": "sending", "detail": None,
        })
        await store.complete_signal(signal_id, "sent", sent_at=run["done_at"], http_status=200, signal_uuid="5e1f2a3b-0000-4000-8000-000000000002")
        await AsyncDbSignalJournalManager(writer).record_decision({
            "signal_uuid": "5e1f2a3b-0000-4000-8000-000000000002", "action": "long", "indice": "us100",
            "decided_at": run["done_at"] + timedelta(seconds=2), "outcome": "refused", "reason": "Outside trading hours",
        })
        queries = AdminQueries(LazyDatabase(reader), "Europe/Paris")
        recent = await queries.ta_monitor_recent(now - timedelta(minutes=15), slow_tick_ms=10_000)
        period = await queries.ta_monitor_period(now - timedelta(days=30), now, 3600, None, signal_age_seconds=60, slow_tick_ms=10_000)
        of_another = await queries.ta_monitor_period(
            now - timedelta(days=30), now, 3600, "bbbbbbbbbbbb", signal_age_seconds=60, slow_tick_ms=10_000,
        )
        removed = await store.purge_monitoring(now - timedelta(days=7))
        left = await queries.ta_monitor_period(now - timedelta(days=30), now, 3600, None, signal_age_seconds=60, slow_tick_ms=10_000)
        return recent, period, of_another, removed, left

    recent, period, of_another, removed, left = in_a_database_of_its_own(scenario)

    assert recent["last_tick"]["tick_at"] == tick["tick_at"] and recent["kept_since"] == old_tick["tick_at"]
    assert [(row["rule_id"], row["runner"], row["append_max_ms"], row["load_max_ms"]) for row in recent["runs"]] == [
        ("aaaaaaaaaaaa", "live", 60, None),
    ]
    assert recent["deferred"] == [{"rule_id": "bbbbbbbbbbbb", "ticks": 1}]
    assert recent["signals"] == [{"rule_id": "aaaaaaaaaaaa", "outcome": "sent", "detail": None, "signals": 1}]
    # The first tick after a start counts as a tick, but not for how far behind the feed was.
    assert recent["ticks"]["ticks"] == 2 and recent["ticks"]["feed_lag_max_ms"] == pytest.approx(1500, abs=1000)
    # A tick that checked a continued run is no steady tick; the check is read back from its figures.
    assert recent["ticks"]["steady_tick_max_ms"] == 100 and recent["ticks"]["tick_max_ms"] == 2600
    checked = {"rule_id": "aaaaaaaaaaaa", "mode": "live", "runner": "live", "checks": 1, "differed": 1, "check_max_ms": 2400,
               "last_difference": difference}
    assert recent["checks"] == [checked] and period["checks"] == [checked] and of_another["checks"] == []
    assert period["rules"][0]["continued"] == 1
    # The steps of the signal, its run's among them: the join of the signal's raised_at and the run's tick_at held.
    steps = {row["step"]: row for row in period["steps"]}
    assert steps["close_to_tick"]["p50"] == pytest.approx(1500)
    assert (steps["run"]["p50"], steps["run_to_sent"]["p50"]) == (pytest.approx(60, abs=0.01), pytest.approx(20, abs=0.01))
    assert steps["sent_to_decided"]["p50"] == pytest.approx(2000, abs=0.01) and steps["sent_to_order"]["signals"] == 0
    assert period["trader"] == [{"outcome": "refused", "reason": "Outside trading hours", "signals": 1}]
    assert [row["rule_id"] for row in period["rules"]] == ["aaaaaaaaaaaa"] and period["rules"][0]["loads"] == 1
    assert of_another["rules"] == [] and of_another["deferred"] == [{"rule_id": "bbbbbbbbbbbb", "ticks": 2}]
    # The tick of nine days ago and its run, gone; the rest kept.
    assert removed == 2 and left["kept_since"] == started["tick_at"] and left["rules"][0]["loads"] == 0


def test_a_credential_written_by_the_admin_is_opened_by_its_service_alone(in_a_database_of_its_own, tmp_path):
    from src.admin.vault import AdminVault
    from src.configuration import ConfigurationManager
    from src.vault import keys, loader, store

    keys.generate(str(tmp_path / "keys"))
    for role in ("admin", "capital_feed", "trader"):
        view = tmp_path / f"view-{role}"
        (view / "own").mkdir(parents=True)
        (view / "own" / "private.key").write_bytes((tmp_path / "keys" / role / "private.key").read_bytes())
        (view / "public").symlink_to(tmp_path / "keys" / "public")
    config = ConfigurationManager("tests/test_config.json")
    login = {"api_key": "capitalkey1234567890", "identifier": "me@example.com", "password": "pass word 1"}

    async def scenario(writer, reader):
        from src.admin.queries import LazyDatabase

        # As the admin reaches them: the read-only pool and its one writable connection, connected on first use.
        reader = LazyDatabase(reader)
        vault = AdminVault(reader=reader, writer=writer, config_manager=config, keys_dir=str(tmp_path / "view-admin"))
        await vault.set("capital.login", login, by="10.0.0.1")
        await vault.set("capital.login", {**login, "password": "another one"}, by="10.0.0.2")
        await store.record_check(writer, "capital.login", "capital_feed", True, "streaming")
        listed = await vault.listed()
        loader.forget()
        feed = await loader.load(config, "capital_feed", db=reader, keys_dir=str(tmp_path / "view-capital_feed"))
        trader = await loader.load(config, "trader", db=reader, keys_dir=str(tmp_path / "view-trader"))
        removed = await vault.remove("capital.login")
        return listed, feed, trader, removed, await store.listed(reader), await store.checks(reader)

    listed, feed, trader, removed, after, checks = in_a_database_of_its_own(scenario)

    [capital] = [item for item in listed["items"] if item["name"] == "capital.login"]
    assert capital["where"] == "database" and capital["set_by"] == "10.0.0.2" and capital["public"] == {"identifier": "me@example.com"}
    assert capital["checks"][0]["detail"] == "streaming"
    assert feed["capital.login"]["password"] == "another one"
    assert "capital.login" not in trader
    assert removed["services"] == ["capital_feed"] and after == [] and checks == []


def test_the_mcp_tokens_kept_in_postgresql_open_the_endpoint_until_revoked(in_a_database_of_its_own):
    from src.admin.mcp_token_store import DbTokenStore

    async def scenario(writer, reader):
        from src.admin.queries import LazyDatabase

        reader = LazyDatabase(reader)
        store = DbTokenStore(reader=reader, writer=writer)
        await store.refresh()
        token, entry = await store.create("claude-mac", 30)
        opened = store.verify(token)
        await asyncio.sleep(0.05)
        used = await reader.fetchval("SELECT last_used_at FROM mcp_token WHERE id = $1", entry["id"])
        revoked = await store.revoke("claude-mac")
        return opened, used, revoked, store.verify(token), store.entries()

    opened, used, revoked, after, entries = in_a_database_of_its_own(scenario)

    assert opened.name == "claude-mac" and used is not None
    assert [entry["name"] for entry in revoked] == ["claude-mac"] and after is None
    assert entries[0]["state"] == "revoked"


def test_the_bots_commands_read_what_the_services_wrote_and_cannot_write(in_a_database_of_its_own):
    import asyncpg

    from src.mq_telegram import commands
    from src.mq_telegram.commands import Calendars, Commands, CommandSettings

    async def scenario(writer, reader):
        now = datetime.now(timezone.utc)
        await writer.execute(
            "INSERT INTO turbo_data_position (position_id, action, position_status, execution_time_open, position_amount,"
            " position_open_price, position_max_performance_percent) VALUES ('p1', 'long', 'Open', $1, 220, 4.52, 2.4)",
            now - timedelta(minutes=30),
        )
        await writer.execute("INSERT INTO position_live_value VALUES ('p1', 4.61, 1.99, $1)", now)
        await writer.execute(
            "INSERT INTO signal_journal (signal_uuid, action, indice, received_timestamp, decided_at, outcome)"
            " VALUES ('u1', 'long', 'us100', $1, $1, 'executed')", now,
        )
        # A query parameter of the address is a setting of the session: the commands read the test's schema.
        schema = await writer.fetchval("SELECT current_schema()")
        address = f"{POSTGRES_DSN}{'&' if '?' in POSTGRES_DSN else '?'}search_path={schema}"
        said = Commands(CommandSettings(dsn=address), Calendars.read())
        answers = {command: await asyncio.to_thread(said.answer, command)
                   for command in ("/status", "/positions", "/day", "/signals")}
        connection = await commands._connect(address)
        try:
            await connection.execute("INSERT INTO trade_risk_state VALUES ('written', now())")
            refused = None
        except asyncpg.ReadOnlySQLTransactionError as e:
            refused = e
        finally:
            await connection.close()
        return answers, refused, await writer.fetchval("SELECT COUNT(*) FROM trade_risk_state")

    answers, refused, risk_rows = in_a_database_of_its_own(scenario)

    assert "Positions: 1 open, live +1.99 %" in answers["/status"]
    assert "Now 4.61 € (+1.99 %)" in answers["/positions"]
    assert "Positions opened today: 1" in answers["/day"]
    assert ": executed" in answers["/signals"]
    assert refused is not None and risk_rows == 0


def _saxo_services(tmp_path, schema, roles):
    """SaxoAuth of each role over the test's schema, its key and the shared token folder, as in production."""
    from unittest.mock import MagicMock

    import asyncpg

    from src.saxo_authen import SaxoAuth
    from src.saxo_authen.token_store import DatabaseThread, DbTokenStore
    from src.vault import keys

    root = tmp_path / "keys"
    if not root.exists():
        keys.generate(str(root))
    folder = tmp_path / "saxo_auth"
    folder.mkdir(exist_ok=True)
    config = MagicMock()
    values = {
        "authentication.saxo.app_config_object": {"AppKey": "k" * 32, "AppSecret": "s" * 32},
        "authentication.persistant.token_path": str(folder / "persist_token.json"),
        "saxo_auth.env": "live",
    }
    config.get_config_value.side_effect = lambda key, default=None: values.get(key, default)
    services = []
    for role in roles:
        view = tmp_path / f"view-{role}"
        if not view.exists():
            (view / "own").mkdir(parents=True)
            (view / "own" / "private.key").write_bytes((root / role / "private.key").read_bytes())
            (view / "public").symlink_to(root / "public")

        async def connect():
            return await asyncpg.connect(POSTGRES_DSN, server_settings={"search_path": schema})

        store = DbTokenStore(DatabaseThread(connect), "live", role, str(folder), keys_dir=str(view))
        services.append(SaxoAuth(config, token_store=store, environ={
            "WATA_APP_ROLE": role, "SAXO_REDIRECT_URL": "https://admin.example.com/api/saxo/callback",
        }))
    return services


def _expired_tokens():
    return {"access_token": "old", "refresh_token": "refresh-old", "expires_in": 1200, "refresh_token_expires_in": 3600,
            "date_saved": (datetime.now() - timedelta(seconds=1500)).isoformat()}


def test_two_services_renewing_saxos_token_at_once_renew_it_once(in_a_database_of_its_own, tmp_path):
    import threading
    import time
    from unittest.mock import MagicMock, patch

    asked = []
    asking = threading.Lock()

    def saxo_answers(url, data, timeout):
        with asking:
            asked.append(data["refresh_token"])
        time.sleep(0.3)
        response = MagicMock(status_code=201)
        response.json.return_value = {"access_token": "renewed", "refresh_token": "refresh-renewed", "expires_in": 1200,
                                      "refresh_token_expires_in": 3600}
        return response

    async def scenario(writer, reader):
        schema = writer._server_settings["search_path"]
        trader, monitor = _saxo_services(tmp_path, schema, ["trader", "position_monitor"])
        await asyncio.to_thread(trader.store.save, _expired_tokens(), 0, "migration")
        with patch("src.saxo_authen.requests.post", side_effect=saxo_answers):
            tokens = await asyncio.gather(asyncio.to_thread(trader.get_token), asyncio.to_thread(monitor.get_token))
        row = await writer.fetchrow("SELECT generation, saved_by, access_expires_at > now() AS fresh FROM saxo_token")
        return tokens, dict(row), await asyncio.to_thread(monitor.get_token_status)

    tokens, row, status = in_a_database_of_its_own(scenario)

    # One renewal: a refresh token is used once, and the second service read what the first wrote.
    assert asked == ["refresh-old"] and tokens == ["renewed", "renewed"]
    assert row["generation"] == 2 and row["saved_by"] in ("trader", "position_monitor") and row["fresh"]
    assert status["has_token"] and not status["access_token_expired"]


def test_a_saxo_code_is_taken_once_and_a_lapse_said_once(in_a_database_of_its_own, tmp_path):
    import urllib.parse

    from src.admin.queries import LazyDatabase
    from src.admin.saxo import AdminSaxo
    from src.configuration import ConfigurationManager

    async def scenario(writer, reader):
        schema = writer._server_settings["search_path"]
        trader, recorder = _saxo_services(tmp_path, schema, ["trader", "price_recorder"])
        firsts = [await asyncio.to_thread(service.store.needs_authorization, "lapsed") for service in (trader, recorder)]
        await writer.execute("UPDATE saxo_token SET needs_authorization_since = now() - interval '2 hours'")
        reminders = [await asyncio.to_thread(service.store.reminder_due, 3600) for service in (trader, recorder)]
        admin = AdminSaxo(reader=LazyDatabase(reader), writer=writer, config_manager=ConfigurationManager("tests/test_config.json"),
                          keys_dir=str(tmp_path / "keys"), environ={"SAXO_REDIRECT_URL": "https://admin.example.com/api/saxo/callback"})
        await writer.execute(
            "INSERT INTO credential (name, public, sealed, readers, set_at) VALUES ('saxo.live.app', '{\"app_key\": \"%s\"}', '{}', '{}', now())"
            % ("a" * 32)
        )
        state = urllib.parse.parse_qs(urllib.parse.urlsplit(await admin.authorize("live")).query)["state"][0]
        await admin.hand_over("the-code", state=state, by="10.77.0.2")
        claims = await asyncio.gather(asyncio.to_thread(trader.store.claim_code), asyncio.to_thread(recorder.store.claim_code))
        status = await admin.status()
        return firsts, reminders, claims, status

    firsts, reminders, claims, status = in_a_database_of_its_own(scenario)

    assert firsts == [True, False]
    assert reminders == [True, False]
    assert sorted(claim is None for claim in claims) == [False, True]
    assert [claim for claim in claims if claim] == [{"code": "the-code"}]
    assert status["needs_authorization_since"] is not None and status["apps"]["live"]["app_key"] == "a" * 32


# ── RabbitMQ, through what the services publish and consume with ──


class _Settings:
    """What the services read of the configuration to reach RabbitMQ."""

    def __init__(self):
        credentials, self.hostname = RABBITMQ.rsplit("@", 1)
        self.username, self.password = credentials.split(":", 1)

    def get_rabbitmq_config(self):
        return {"hostname": self.hostname, "authentication": {"username": self.username, "password": self.password}}

    def get_config_value(self, key, default=None):
        return default

    @property
    def url(self):
        return f"amqp://{self.username}:{self.password}@{self.hostname}/"

    def blocking_connection(self):
        import pika

        return pika.BlockingConnection(pika.ConnectionParameters(
            host=self.hostname, credentials=pika.PlainCredentials(self.username, self.password),
        ))


@pytest.fixture
def settings() -> _Settings:
    _needs(RABBITMQ, "RabbitMQ", "aio_pika")
    return _Settings()


def _read_with_pika(settings: _Settings, queue: str, wanted: str, durable: bool = False) -> list[dict]:
    """Take from ``queue`` as the Telegram service does, until the message that holds ``wanted``."""
    connection = settings.blocking_connection()
    try:
        channel = connection.channel()
        channel.queue_declare(queue=queue, durable=durable)
        seen = []
        for _ in range(200):
            method, _properties, body = channel.basic_get(queue=queue, auto_ack=True)
            if method is None:
                connection.sleep(0.05)
                continue
            seen.append(json.loads(body))
            if wanted in body.decode():
                break
        return seen
    finally:
        connection.close()


def test_a_message_the_trading_services_send_to_telegram_reaches_its_queue(settings):
    from src.mq_telegram.async_tools import AsyncTelegramSender

    text = f"wata test {uuid.uuid4().hex}"

    async def scenario():
        sender = AsyncTelegramSender(settings)
        await sender.send(text)  # connects by itself, as on the first message of a service
        await sender.send(f"{text} again")
        await sender.close()

    asyncio.run(asyncio.wait_for(scenario(), timeout=30))

    assert {"message": text} in _read_with_pika(settings, "telegram_channel", f"{text} again")


def test_a_message_sent_to_telegram_the_blocking_way_reaches_its_queue(settings):
    from src.mq_telegram.tools import send_message_to_mq_for_telegram

    text = f"wata test {uuid.uuid4().hex}"

    send_message_to_mq_for_telegram(settings.blocking_connection(), text)

    assert {"message": text} in _read_with_pika(settings, "telegram_channel", text)


def test_a_message_the_admin_publishes_is_consumed_the_way_the_position_monitor_does(settings):
    import aio_pika

    from src.admin.services import OPS_QUEUE, WataServices

    message = {"action": "wata_test_nothing_to_do", "id": uuid.uuid4().hex}

    async def scenario():
        received = asyncio.Event()
        bodies = []

        # As the position monitor sets its consumer up.
        connection = await aio_pika.connect_robust(settings.url)
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=1)
        queue = await channel.declare_queue(OPS_QUEUE, durable=True)

        async def on_message(incoming: aio_pika.IncomingMessage):
            async with incoming.process(requeue=False):
                body = json.loads(incoming.body.decode("utf-8"))
                bodies.append(body)
                if body == message:
                    received.set()

        await queue.consume(on_message)
        await WataServices(settings, None, None).publish_ops(message)
        await asyncio.wait_for(received.wait(), timeout=15)
        # Acknowledged: nothing of it is left to be delivered again.
        left = (await channel.declare_queue(OPS_QUEUE, durable=True, passive=True)).declaration_result.message_count
        await connection.close()
        return bodies, left

    bodies, left = asyncio.run(asyncio.wait_for(scenario(), timeout=45))

    assert message in bodies
    assert left == 0


def test_a_quote_the_capital_com_feed_publishes_reaches_a_chart_through_the_admin(settings):
    from src.admin.live_prices import LivePrices, rabbitmq_source
    from src.capital_feed.live import LivePublisher, RabbitExchange

    epic = f"T{uuid.uuid4().hex[:8].upper()}"
    ref = f"capital/{epic}"

    async def scenario():
        prices = LivePrices(rabbitmq_source(settings.url))
        chart = prices.subscribe(ref)
        hub = asyncio.create_task(prices.run())
        # The admin says when it listens: only then is the quote sent.
        assert (await asyncio.wait_for(chart.get(), 15))["type"] == "live"
        exchange = RabbitExchange(settings.url)
        publisher = LivePublisher(exchange.open)
        sending = asyncio.create_task(publisher.run())
        publisher.quote(epic, bid=25000.5, ask=25002.3, quoted_ms=1792051200123, received_at=1792051200.15, bar=None)
        received = await asyncio.wait_for(chart.get(), 15)
        sending.cancel()
        hub.cancel()
        await asyncio.gather(sending, hub, return_exceptions=True)
        await exchange.close()
        return received, publisher

    received, publisher = asyncio.run(asyncio.wait_for(scenario(), timeout=45))

    assert (received["type"], received["ref"], received["bid"], received["t"]) == ("quote", ref, 25000.5, 1792051200123)
    assert publisher.sent == 1 and publisher.dropped == 0

