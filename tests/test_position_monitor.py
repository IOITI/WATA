import asyncio
import sys
import types

from unittest.mock import AsyncMock, MagicMock

asyncpg_stub = types.ModuleType("asyncpg")
asyncpg_stub.Pool = type("Pool", (), {})
asyncpg_stub.Record = dict
asyncpg_stub.create_pool = AsyncMock()
sys.modules.setdefault("asyncpg", asyncpg_stub)
aio_pika_module = sys.modules.setdefault("aio_pika", types.ModuleType("aio_pika"))
if not hasattr(aio_pika_module, "IncomingMessage"):
    aio_pika_module.IncomingMessage = type("IncomingMessage", (), {})

from src.position_monitor import StreamPositionHandler


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
