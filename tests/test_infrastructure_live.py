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
