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


def _close_dependencies(closed=1):
    performance_monitor = MagicMock()
    performance_monitor.close_managed_positions_by_criteria = AsyncMock(
        return_value={"closed_initiated_count": closed, "errors_count": 0, "closed_position_ids": ["p1"] * closed}
    )
    telegram = MagicMock()
    telegram.send = AsyncMock()
    return performance_monitor, telegram


def test_fresh_close_request_closes_that_position_only():
    performance_monitor, telegram = _close_dependencies()

    asyncio.run(handle_close_position_request(_close_request(), performance_monitor, telegram))

    performance_monitor.close_managed_positions_by_criteria.assert_awaited_once_with(
        only_position_id="p1", defer_bookkeeping=True,
    )
    telegram.send.assert_not_awaited()


def test_close_request_that_waited_in_the_queue_is_ignored_and_reported():
    performance_monitor, telegram = _close_dependencies()
    stale = _close_request(age_seconds=position_monitor.CLOSE_REQUEST_MAX_AGE_SECONDS + 30)

    asyncio.run(handle_close_position_request(stale, performance_monitor, telegram))

    performance_monitor.close_managed_positions_by_criteria.assert_not_awaited()
    assert "IGNORED" in telegram.send.await_args.args[0]


def test_close_request_without_a_readable_time_or_from_the_future_is_ignored():
    for request in (
        _close_request(requested_at=None),
        _close_request(requested_at="yesterday"),
        _close_request(age_seconds=-3600),
    ):
        performance_monitor, telegram = _close_dependencies()

        asyncio.run(handle_close_position_request(request, performance_monitor, telegram))

        performance_monitor.close_managed_positions_by_criteria.assert_not_awaited()


def test_close_request_without_a_position_does_nothing():
    performance_monitor, telegram = _close_dependencies()

    asyncio.run(handle_close_position_request(_close_request(position_id=None), performance_monitor, telegram))

    performance_monitor.close_managed_positions_by_criteria.assert_not_awaited()
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

    performance_monitor.close_managed_positions_by_criteria.assert_awaited_once()


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
