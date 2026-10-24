import asyncio
import json
import sys
import types
from datetime import datetime, timedelta, timezone

from unittest.mock import AsyncMock, MagicMock, patch

asyncpg_stub = types.ModuleType("asyncpg")
asyncpg_stub.Pool = type("Pool", (), {})
asyncpg_stub.Record = dict
asyncpg_stub.create_pool = AsyncMock()
sys.modules.setdefault("asyncpg", asyncpg_stub)
aio_pika_module = sys.modules.setdefault("aio_pika", types.ModuleType("aio_pika"))
if not hasattr(aio_pika_module, "IncomingMessage"):
    aio_pika_module.IncomingMessage = type("IncomingMessage", (), {})

import src.position_monitor as position_monitor
from src.position_monitor import (
    StreamPositionHandler,
    dispatch_ops_message,
    handle_close_position_request,
    handle_daily_stats,
    publish_live_values_periodically,
)


def _handler(*, updates_for_db=None, settle_seconds=0):
    performance_monitor = MagicMock()
    performance_monitor.check_positions_from_stream = AsyncMock(
        return_value={"closed_positions_processed": [], "db_updates": [], "errors": 0}
    )
    performance_monitor.sync_db_positions_with_api = AsyncMock(
        return_value={"updates_for_db": updates_for_db or []}
    )
    db_position_manager = MagicMock()
    db_position_manager.update_turbo_position_data = AsyncMock()
    telegram = MagicMock()
    telegram.send = AsyncMock()
    handler = StreamPositionHandler(
        performance_monitor, db_position_manager, telegram, reconcile_settle_seconds=settle_seconds,
    )
    return handler, performance_monitor, db_position_manager, telegram


def _snapshot(*position_ids: str) -> dict:
    return {pid: {"PositionId": pid, "PositionView": {"Bid": 10.0}} for pid in position_ids}


async def _planned_reconciliation(handler):
    if handler._pending_reconcile is not None:
        await handler._pending_reconcile


def test_price_updates_check_positions_without_reconciling():
    handler, performance_monitor, _, _ = _handler()

    async def scenario():
        for _ in range(3):
            await handler.on_positions_update(_snapshot("p1"))
        await _planned_reconciliation(handler)

    asyncio.run(scenario())

    assert performance_monitor.check_positions_from_stream.await_count == 3
    performance_monitor.sync_db_positions_with_api.assert_not_awaited()


def test_position_leaving_the_stream_is_reconciled_from_the_snapshot():
    closure = {"position_status": "Closed", "position_close_reason": "SaxoAPI"}
    handler, performance_monitor, db_position_manager, _ = _handler(updates_for_db=[("p1", closure)])

    async def scenario():
        await handler.on_positions_update(_snapshot("p1", "p2"))
        await handler.on_positions_update(_snapshot("p2"))
        await _planned_reconciliation(handler)

    asyncio.run(scenario())

    performance_monitor.sync_db_positions_with_api.assert_awaited_once_with({"p2"})
    db_position_manager.update_turbo_position_data.assert_awaited_once_with("p1", closure)


def test_last_position_leaving_the_stream_is_reconciled_too():
    handler, performance_monitor, _, _ = _handler()

    async def scenario():
        await handler.on_positions_update(_snapshot("p1"))
        await handler.on_positions_update({})
        await _planned_reconciliation(handler)

    asyncio.run(scenario())

    performance_monitor.sync_db_positions_with_api.assert_awaited_once_with(set())
    performance_monitor.check_positions_from_stream.assert_awaited_once()


def test_positions_leaving_in_quick_succession_share_one_reconciliation():
    handler, performance_monitor, _, _ = _handler(settle_seconds=0.02)

    async def scenario():
        await handler.on_positions_update(_snapshot("p1", "p2", "p3"))
        await handler.on_positions_update(_snapshot("p2", "p3"))
        await handler.on_positions_update(_snapshot("p3"))
        await _planned_reconciliation(handler)

    asyncio.run(scenario())

    performance_monitor.sync_db_positions_with_api.assert_awaited_once_with({"p3"})


def test_position_leaving_while_a_reconciliation_runs_gets_its_own():
    handler, performance_monitor, _, _ = _handler()

    async def scenario():
        reconciling = asyncio.Event()
        let_it_finish = asyncio.Event()
        seen = []

        async def sync(open_position_ids):
            seen.append(set(open_position_ids))
            if len(seen) == 1:
                reconciling.set()
                await let_it_finish.wait()
            return {"updates_for_db": []}

        performance_monitor.sync_db_positions_with_api = AsyncMock(side_effect=sync)

        await handler.on_positions_update(_snapshot("p1", "p2"))
        await handler.on_positions_update(_snapshot("p2"))
        await asyncio.wait_for(reconciling.wait(), timeout=1)
        await handler.on_positions_update({})
        let_it_finish.set()
        await _planned_reconciliation(handler)
        return seen

    assert asyncio.run(scenario()) == [{"p2"}, set()]


def test_reconciliation_reports_a_closure_it_could_not_save():
    handler, _, db_position_manager, telegram = _handler(updates_for_db=[("p1", {"position_status": "Closed"})])
    db_position_manager.update_turbo_position_data.side_effect = RuntimeError("db down")

    asyncio.run(handler.reconcile())

    telegram.send.assert_awaited_once()
    assert "CRITICAL SYNC ERROR" in telegram.send.await_args.args[0]


def test_reconciliation_failure_is_contained():
    handler, performance_monitor, db_position_manager, _ = _handler()
    performance_monitor.sync_db_positions_with_api.side_effect = RuntimeError("saxo unavailable")

    asyncio.run(handler.reconcile())

    db_position_manager.update_turbo_position_data.assert_not_awaited()


def test_periodic_reconciliation_runs_at_startup_then_on_the_interval_by_asking_saxo():
    handler, performance_monitor, _, _ = _handler()
    performance_monitor.sync_db_positions_with_api.side_effect = [
        {"updates_for_db": []},
        RuntimeError("saxo unavailable"),
        {"updates_for_db": []},
        asyncio.CancelledError,
    ]

    async def scenario():
        try:
            await handler.reconcile_periodically(0)
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())

    assert performance_monitor.sync_db_positions_with_api.await_count == 4
    # None = "ask Saxo what is open", independently of the stream
    assert all(call.args == (None,) for call in performance_monitor.sync_db_positions_with_api.await_args_list)


def test_position_check_error_does_not_reach_the_stream_client():
    handler, performance_monitor, _, _ = _handler()
    performance_monitor.check_positions_from_stream.side_effect = RuntimeError("boom")

    asyncio.run(handler.on_positions_update(_snapshot("p1")))


# ── what the admin interface relies on ──


def test_positions_that_left_the_stream_are_forgotten_even_when_none_is_left():
    handler, performance_monitor, _, _ = _handler()

    async def scenario():
        await handler.on_positions_update(_snapshot("p1"))
        await handler.on_positions_update({})
        await _planned_reconciliation(handler)

    asyncio.run(scenario())

    assert [call.args[0] for call in performance_monitor.forget_live_values_except.call_args_list] == [{"p1"}, set()]


class _StopPublishing(Exception):
    pass


def _publish(live_values_by_round, db):
    """Run the publisher for one round per entry of ``live_values_by_round``."""
    performance_monitor = MagicMock()
    performance_monitor.live_values.side_effect = [*live_values_by_round, _StopPublishing()]

    async def scenario():
        with patch("src.position_monitor.asyncio.sleep", new=AsyncMock()):
            try:
                await publish_live_values_periodically(performance_monitor, db, interval_seconds=5)
            except _StopPublishing:
                pass

    asyncio.run(scenario())


def test_live_values_are_published_only_when_they_changed():
    observed = datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc)
    first = {"p1": (10.05, 0.5, observed)}
    second = {"p1": (10.08, 0.8, observed + timedelta(seconds=5))}
    db = MagicMock()
    db.replace_live_values = AsyncMock()

    _publish([first, first, second, {}, {}], db)

    assert [call.args[0] for call in db.replace_live_values.await_args_list] == [first, second, {}]


def test_first_round_clears_what_a_previous_run_left_behind():
    db = MagicMock()
    db.replace_live_values = AsyncMock()

    _publish([{}], db)

    db.replace_live_values.assert_awaited_once_with({})


def test_failed_publication_is_retried_and_never_stops_the_publisher():
    values = {"p1": (10.05, 0.5, datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc))}
    db = MagicMock()
    db.replace_live_values = AsyncMock(side_effect=[ConnectionError("database is down"), None])

    _publish([values, values], db)

    assert db.replace_live_values.await_count == 2


def _close_request(age_seconds=1, **overrides):
    requested_at = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    return {
        "action": "close_position",
        "position_id": "p1",
        "requested_at": requested_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        **overrides,
    }


def _close_dependencies(closed=1, pending=False):
    performance_monitor = MagicMock()
    performance_monitor.request_close = AsyncMock(return_value={
        "closed_initiated_count": closed, "errors_count": 0 if closed or not pending else 1,
        "closed_position_ids": ["p1"] * closed, "pending": pending,
    })
    telegram = MagicMock()
    telegram.send = AsyncMock()
    return performance_monitor, telegram


def test_fresh_close_request_closes_that_position_only():
    performance_monitor, telegram = _close_dependencies()

    asyncio.run(handle_close_position_request(_close_request(), performance_monitor, telegram))

    performance_monitor.request_close.assert_awaited_once_with("p1", "Explicit Close (position p1)")
    telegram.send.assert_not_awaited()


def test_a_sale_the_trader_hands_over_keeps_its_reason():
    performance_monitor, telegram = _close_dependencies()

    asyncio.run(handle_close_position_request(
        _close_request(reason="Explicit Close (All)"), performance_monitor, telegram,
    ))

    performance_monitor.request_close.assert_awaited_once_with("p1", "Explicit Close (All)")
    telegram.send.assert_not_awaited()


def test_a_close_that_could_not_sell_yet_is_left_to_its_intent():
    # The intent says its own failures, once and then less and less often: nothing more here.
    performance_monitor, telegram = _close_dependencies(closed=0, pending=True)

    asyncio.run(handle_close_position_request(_close_request(), performance_monitor, telegram))

    telegram.send.assert_not_awaited()


def test_close_request_that_waited_in_the_queue_is_ignored_and_reported():
    performance_monitor, telegram = _close_dependencies()
    stale = _close_request(age_seconds=position_monitor.CLOSE_REQUEST_MAX_AGE_SECONDS + 30)

    asyncio.run(handle_close_position_request(stale, performance_monitor, telegram))

    performance_monitor.request_close.assert_not_awaited()
    assert "IGNORED" in telegram.send.await_args.args[0]


def test_close_request_without_a_readable_time_or_from_the_future_is_ignored():
    for request in (
        _close_request(requested_at=None),
        _close_request(requested_at="yesterday"),
        _close_request(age_seconds=-3600),
    ):
        performance_monitor, telegram = _close_dependencies()

        asyncio.run(handle_close_position_request(request, performance_monitor, telegram))

        performance_monitor.request_close.assert_not_awaited()


def test_close_request_without_a_position_does_nothing():
    performance_monitor, telegram = _close_dependencies()

    asyncio.run(handle_close_position_request(_close_request(position_id=None), performance_monitor, telegram))

    performance_monitor.request_close.assert_not_awaited()
    telegram.send.assert_not_awaited()


def test_close_request_for_a_position_that_is_not_open_is_reported():
    performance_monitor, telegram = _close_dependencies(closed=0)

    asyncio.run(handle_close_position_request(_close_request(), performance_monitor, telegram))

    assert "nothing done" in telegram.send.await_args.args[0]


class _Message:
    def __init__(self, body: dict):
        self.body = json.dumps(body).encode()

    def process(self, requeue=False):
        message = self

        class _Context:
            async def __aenter__(self):
                return message

            async def __aexit__(self, *exc_info):
                return False

        return _Context()


def test_close_request_arrives_through_the_ops_queue():
    performance_monitor, telegram = _close_dependencies()

    asyncio.run(dispatch_ops_message(
        _Message(_close_request()), performance_monitor, MagicMock(), MagicMock(), telegram, MagicMock(), [],
    ))

    performance_monitor.request_close.assert_awaited_once()


def _daily_stats_dependencies(balance):
    db_position_manager = MagicMock()
    db_position_manager.get_closed_trade_history = AsyncMock(return_value=[])
    db_position_manager.get_daily_profit_history = AsyncMock(return_value=[])
    for name in ("get_percent_of_last_n_days", "get_best_percent_of_last_n_days",
                 "get_theoretical_percent_of_last_n_days_on_max"):
        setattr(db_position_manager, name, AsyncMock(return_value={}))
    db_perf_manager = MagicMock()
    db_perf_manager.create_last_day_trade_performance_data = AsyncMock()
    telegram = MagicMock()
    telegram.send = AsyncMock()
    position_service = MagicMock()
    position_service.get_current_account_balance = AsyncMock(**balance)
    return db_position_manager, db_perf_manager, telegram, position_service


def test_daily_report_records_the_account_balance():
    dependencies = _daily_stats_dependencies({"return_value": 12345.67})
    db_balance_manager = MagicMock()
    db_balance_manager.record_balance_safely = AsyncMock()

    asyncio.run(handle_daily_stats(*dependencies, [], db_balance_manager=db_balance_manager))

    day, total_value = db_balance_manager.record_balance_safely.await_args.args
    assert total_value == 12345.67
    assert day == datetime.now().date()
    dependencies[2].send.assert_awaited_once()


def test_daily_report_is_still_sent_when_the_balance_is_unavailable():
    dependencies = _daily_stats_dependencies({"side_effect": RuntimeError("Saxo is down")})
    db_balance_manager = MagicMock()
    db_balance_manager.record_balance_safely = AsyncMock()

    asyncio.run(handle_daily_stats(*dependencies, [], db_balance_manager=db_balance_manager))

    assert db_balance_manager.record_balance_safely.await_args.args[1] is None
    dependencies[2].send.assert_awaited_once()


def test_position_checks_still_run_when_the_live_value_bookkeeping_fails():
    handler, performance_monitor, _, _ = _handler()
    performance_monitor.forget_live_values_except.side_effect = RuntimeError("unexpected")

    asyncio.run(handler.on_positions_update(_snapshot("p1")))

    performance_monitor.check_positions_from_stream.assert_awaited_once()


# ── a stream that brings nothing ──


def _watched_handler(managed=("p1",)):
    handler, performance_monitor, _, telegram = _handler()
    performance_monitor.managed_position_ids = AsyncMock(return_value=set(managed))
    performance_monitor.check_all_positions_performance = AsyncMock(
        return_value={"closed_positions_processed": [], "db_updates": [], "errors": 0}
    )
    stream = MagicMock(connected=True, last_message_at=None)
    return handler, performance_monitor, telegram, stream


def test_positions_left_without_a_price_by_a_silent_stream_are_checked_by_rest():
    handler, performance_monitor, telegram, stream = _watched_handler()

    async def scenario():
        await handler.on_positions_update(_snapshot("p1"))
        start = handler.last_update_at
        await handler.check_stream(stream, True, now=start + position_monitor.STREAM_SILENT_FALLBACK_SECONDS - 1)
        quiet = performance_monitor.check_all_positions_performance.await_count
        await handler.check_stream(stream, True, now=start + position_monitor.STREAM_SILENT_FALLBACK_SECONDS)
        await handler.check_stream(stream, True, now=start + position_monitor.STREAM_SILENT_ALERT_SECONDS)
        await handler.check_stream(stream, True, now=start + position_monitor.STREAM_SILENT_ALERT_SECONDS + 5)
        said_while_silent = [call.args[0] for call in telegram.send.await_args_list]
        await handler.on_positions_update(_snapshot("p1"))
        await handler.check_stream(stream, True)
        return quiet, said_while_silent

    quiet, said_while_silent = asyncio.run(scenario())

    assert quiet == 0
    assert performance_monitor.check_all_positions_performance.await_count == 3
    assert said_while_silent == [
        "The position stream (connected) has brought nothing for 60 s while positions are open: "
        "WATA checks their exits through Saxo's REST API every 5 s."
    ]
    assert telegram.send.await_args.args[0] == "The position stream brings prices again: the REST checks stop."
    assert handler.rest_fallback_since is None


def test_a_silent_stream_outside_trading_hours_or_without_managed_positions_is_left_alone():
    handler, performance_monitor, telegram, stream = _watched_handler(managed=())

    async def scenario():
        await handler.on_positions_update(_snapshot("p9"))  # bought by hand: not WATA's
        await handler.check_stream(stream, True, now=handler.last_update_at + 120)
        handler.performance_monitor.managed_position_ids = AsyncMock(return_value={"p9"})
        await handler.check_stream(stream, False, now=handler.last_update_at + 120)

    asyncio.run(scenario())

    performance_monitor.check_all_positions_performance.assert_not_awaited()
    telegram.send.assert_not_awaited()


def test_the_heartbeat_says_the_stream_and_the_sales_waiting():
    from src.trade.close_intents import CloseIntents

    handler, performance_monitor, _, _ = _handler()
    intents = CloseIntents()
    intents.want("p1", "Explicit Close (End of day)", now=1_760_000_000.0)
    intents.failed("p1", "Saxo says it cannot be closed now", now=1_760_000_001.0)
    performance_monitor.close_intents = intents
    stream = MagicMock(connected=False, last_message_at=1_760_000_000.0)

    figures = handler.figures(stream)

    assert figures["stream_connected"] is False
    assert figures["last_message_at"].startswith("2025-10-09T")
    assert figures["close_intents"] == [{
        "position_id": "p1", "reason": "Explicit Close (End of day)", "since": figures["close_intents"][0]["since"],
        "attempts": 1, "last_error": "Saxo says it cannot be closed now",
    }]


def test_the_outside_watcher_is_pinged_only_while_saxo_is_heard():
    heard = MagicMock(connected=True, last_message_at=1000.0)

    assert position_monitor.stream_heard_lately(heard, now=1000.0 + position_monitor.HEARTBEAT_URL_HEALTHY_SECONDS - 1)
    assert not position_monitor.stream_heard_lately(heard, now=1000.0 + position_monitor.HEARTBEAT_URL_HEALTHY_SECONDS)
    assert not position_monitor.stream_heard_lately(MagicMock(connected=False, last_message_at=1000.0), now=1000.0)


def test_the_outside_ping_never_says_its_secret_address(caplog):
    secret = "https://hc-ping.example/5f2c-secret"
    pinged = []

    class Client:
        def __init__(self, timeout):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def get(self, url):
            pinged.append(url)
            if len(pinged) == 2:
                raise ConnectionError(f"cannot reach {url}")
            if len(pinged) == 3:
                raise asyncio.CancelledError

    async def scenario():
        with patch.object(position_monitor.httpx, "AsyncClient", Client):
            try:
                await position_monitor.ping_heartbeat_url_periodically(secret, lambda: True, interval_seconds=0)
            except asyncio.CancelledError:
                pass

    with caplog.at_level("WARNING"):
        asyncio.run(scenario())

    assert pinged == [secret] * 3
    assert "The heartbeat ping failed: ConnectionError" in caplog.text
    assert secret not in caplog.text


def test_a_stop_lets_the_sale_under_way_through_then_ends_the_stream():
    calls = []
    performance_monitor = MagicMock()
    performance_monitor.begin_shutdown = MagicMock(side_effect=lambda: calls.append("no new sale"))

    async def wait_for_sales_under_way(timeout):
        calls.append(f"waited {timeout}s at most")
        return True

    performance_monitor.wait_for_sales_under_way = wait_for_sales_under_way
    stream_client = MagicMock()
    stream_client.stop = AsyncMock(side_effect=lambda: calls.append("stream stopped"))

    async def scenario():
        stopping = asyncio.Event()
        task = asyncio.create_task(position_monitor.stop_when_asked(stopping, performance_monitor, stream_client, settle_seconds=3))
        await asyncio.sleep(0)
        assert calls == []
        stopping.set()
        await asyncio.wait_for(task, 1)

    asyncio.run(scenario())

    assert calls == ["no new sale", "waited 3s at most", "stream stopped"]


def test_a_monitor_asked_to_stop_starts_no_sale(tmp_path):
    from tests.test_keeping_flat import listed, monitor_for

    monitor, _ = monitor_for(tmp_path, monitor_class=position_monitor.StoppableMonitor)
    monitor.begin_shutdown()

    asyncio.run(monitor.check_positions_from_stream(listed(bid=9.0)))  # -10 %: past the stop

    # Decided again at the next start, rather than cut off between Saxo taking it and WATA writing it down.
    monitor.order_service.place_market_order.assert_not_awaited()


def test_a_stop_waits_for_the_sale_under_way_to_have_saxos_answer(tmp_path):
    from tests.test_keeping_flat import listed, monitor_for

    monitor, _ = monitor_for(tmp_path, monitor_class=position_monitor.StoppableMonitor)
    answered = asyncio.Event()

    async def place_market_order(**kwargs):
        await answered.wait()
        return {"OrderId": "sell-1"}

    monitor.order_service.place_market_order = AsyncMock(side_effect=place_market_order)

    async def scenario():
        check = asyncio.create_task(monitor.check_positions_from_stream(listed(bid=9.0)))
        while monitor._sales_under_way == 0:
            await asyncio.sleep(0)
        monitor.begin_shutdown()
        too_soon = await monitor.wait_for_sales_under_way(0.05, poll_seconds=0.01)
        answered.set()
        settled = await monitor.wait_for_sales_under_way(1, poll_seconds=0.01)
        await check
        return too_soon, settled

    too_soon, settled = asyncio.run(scenario())

    assert (too_soon, settled) == (False, True)
    monitor.order_service.place_market_order.assert_awaited_once()
    assert monitor._sales_under_way == 0


def test_the_stop_logic_stays_out_of_the_code_every_backtest_digest_holds():
    from src.backtest.figures import _CODE

    # A change to the monitor's stop must not mark every backtest run as made with other code.
    assert "src/position_monitor" not in " ".join(_CODE)
    assert "begin_shutdown" not in open("src/trade/async_services.py").read()
