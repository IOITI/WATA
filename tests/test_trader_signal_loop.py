import asyncio
import sys
import types
from datetime import datetime, timedelta

import pytest
import pytz
from unittest.mock import AsyncMock, MagicMock, patch

asyncpg_stub = types.ModuleType("asyncpg")
asyncpg_stub.Pool = type("Pool", (), {})
asyncpg_stub.Record = dict
asyncpg_stub.create_pool = AsyncMock()
sys.modules.setdefault("asyncpg", asyncpg_stub)
sys.modules.setdefault("aio_pika", types.ModuleType("aio_pika"))

import src.trader as trader
from src.trader import (
    handle_trading_signal,
    keep_saxo_session_warm,
    poll_and_dispatch_signals,
    select_signals_to_dispatch,
)


@pytest.fixture(autouse=True)
def forget_processed_signals():
    trader._processed_signal_uuids.clear()
    yield
    trader._processed_signal_uuids.clear()


def _timestamp(seconds_ago: float = 0) -> str:
    moment = datetime.now(pytz.utc) - timedelta(seconds=seconds_ago)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _signal(signal_uuid: str, action: str = "long", indice: str = "us100", received_seconds_ago: float = 0) -> dict:
    return {
        "action": action,
        "indice": indice,
        "signal_uuid": signal_uuid,
        "signal_timestamp": "2026-07-16T08:00:00Z",
        "alert_timestamp": "2026-07-16T08:00:00Z",
        "received_timestamp": _timestamp(received_seconds_ago),
    }


class FakeSignalClient:
    """Serves one scripted answer per poll, then cancels the loop."""

    def __init__(self, polls, *, long_poll_wait_ms=1000):
        self._polls = iter(polls)
        self.interval_ms = 0
        self.long_poll_wait_ms = long_poll_wait_ms
        self.startup_replay_max_age_seconds = 10
        self.get_new_signals = AsyncMock(side_effect=self._next_poll)
        self.get_latest_signals = AsyncMock(side_effect=self._next_poll)

    async def _next_poll(self):
        try:
            answer = next(self._polls)
        except StopIteration:
            raise asyncio.CancelledError
        if isinstance(answer, Exception):
            raise answer
        return answer


def _run_loop(client, dispatch):
    async def scenario():
        try:
            await poll_and_dispatch_signals(client, dispatch)
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())


def _dispatched_uuids(dispatch: AsyncMock) -> list[str]:
    return [call.args[0]["signal_uuid"] for call in dispatch.await_args_list]


# ── select_signals_to_dispatch ──

def test_signal_is_selected_only_the_first_time_it_is_seen():
    signal = _signal("uuid-1")

    first = select_signals_to_dispatch([signal], first_poll=False, startup_replay_max_age_seconds=10)
    second = select_signals_to_dispatch([signal], first_poll=False, startup_replay_max_age_seconds=10)

    assert first == [signal]
    assert second == []


def test_signal_still_being_served_is_not_evicted_from_the_seen_set():
    standing = _signal("standing")
    select_signals_to_dispatch([standing], first_poll=False, startup_replay_max_age_seconds=10)

    with patch.object(trader, "_MAX_PROCESSED_SIGNAL_UUIDS", 3):
        for index in range(10):
            polled = [standing, _signal(f"new-{index}")]
            selected = select_signals_to_dispatch(polled, first_poll=False, startup_replay_max_age_seconds=10)
            assert [s["signal_uuid"] for s in selected] == [f"new-{index}"]


# ── poll_and_dispatch_signals ──

def test_signal_returned_by_every_poll_is_dispatched_once():
    signal = _signal("uuid-1")
    client = FakeSignalClient([[signal], [signal], [signal]], long_poll_wait_ms=0)
    dispatch = AsyncMock()

    _run_loop(client, dispatch)

    assert _dispatched_uuids(dispatch) == ["uuid-1"]
    assert client.get_latest_signals.await_count == 4
    client.get_new_signals.assert_not_awaited()


def test_stored_close_position_is_not_dispatched_again_after_a_new_long():
    eod_close = _signal("eod-close", action="close-position", indice="n/a")
    next_day_long = _signal("next-day-long")
    client = FakeSignalClient(
        [[eod_close], [eod_close, next_day_long], [eod_close, next_day_long]],
        long_poll_wait_ms=0,
    )
    dispatch = AsyncMock()

    _run_loop(client, dispatch)

    assert _dispatched_uuids(dispatch) == ["eod-close", "next-day-long"]


def test_signals_stored_before_startup_are_not_replayed():
    old_close = _signal("old-close", action="close-position", indice="n/a", received_seconds_ago=3600)
    fresh_long = _signal("fresh-long", received_seconds_ago=1)
    client = FakeSignalClient([[old_close, fresh_long], [old_close, fresh_long]], long_poll_wait_ms=0)
    dispatch = AsyncMock()

    _run_loop(client, dispatch)

    assert _dispatched_uuids(dispatch) == ["fresh-long"]


def test_startup_guard_only_applies_to_the_first_poll():
    slow_delivery = _signal("slow-delivery", received_seconds_ago=3600)
    client = FakeSignalClient([[], [slow_delivery]])
    dispatch = AsyncMock()

    _run_loop(client, dispatch)

    assert _dispatched_uuids(dispatch) == ["slow-delivery"]
    client.get_latest_signals.assert_not_awaited()


def test_poll_error_does_not_stop_the_loop_nor_end_the_startup_guard():
    old_close = _signal("old-close", action="close-position", indice="n/a", received_seconds_ago=3600)
    client = FakeSignalClient([RuntimeError("web_server down"), [old_close], [_signal("uuid-1")]])
    dispatch = AsyncMock()

    _run_loop(client, dispatch)

    assert _dispatched_uuids(dispatch) == ["uuid-1"]


def test_dispatch_error_does_not_stop_the_loop():
    client = FakeSignalClient([[_signal("uuid-1"), _signal("uuid-2", indice="dax")]])
    dispatch = AsyncMock(side_effect=[RuntimeError("boom"), None])

    _run_loop(client, dispatch)

    assert _dispatched_uuids(dispatch) == ["uuid-1", "uuid-2"]


# ── keep_saxo_session_warm ──

def test_keep_warm_keeps_calling_saxo_after_a_failed_call():
    trading_orchestrator = MagicMock()
    trading_orchestrator.refresh_spending_power = AsyncMock(
        side_effect=[RuntimeError("Saxo unavailable"), 1000.0, asyncio.CancelledError]
    )

    async def scenario():
        try:
            await keep_saxo_session_warm(trading_orchestrator, 0)
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())

    assert trading_orchestrator.refresh_spending_power.await_count == 3


# ── handle_trading_signal: ordering around the close of existing positions ──

def _trade_result() -> dict:
    return {
        "order_details": {"order_id": "order-1"},
        "position_details": {"position_id": "new-position"},
        "selected_turbo_info": {},
    }


def _handler_dependencies(reserve_cash_percent: float, protective_stop=None):
    calls = MagicMock()

    trading_orchestrator = MagicMock()
    trading_orchestrator.reserve_cash_percent = reserve_cash_percent
    trading_orchestrator.execute_trade_signal = AsyncMock(return_value=_trade_result())
    trading_orchestrator.place_protective_stop = AsyncMock(return_value=protective_stop)

    performance_monitor = MagicMock()
    performance_monitor.close_managed_positions_by_criteria = AsyncMock(
        return_value={"closed_initiated_count": 1, "errors_count": 0, "closed_position_ids": ["old-position"]}
    )
    performance_monitor.wait_for_positions_closed = AsyncMock(return_value=True)

    calls.attach_mock(trading_orchestrator.execute_trade_signal, "execute")
    calls.attach_mock(trading_orchestrator.invalidate_spending_power, "invalidate_balance")
    calls.attach_mock(trading_orchestrator.place_protective_stop, "protective_stop")
    calls.attach_mock(performance_monitor.close_managed_positions_by_criteria, "close")
    calls.attach_mock(performance_monitor.wait_for_positions_closed, "wait_closed")

    trading_rule = MagicMock()
    trading_rule.get_allowed_indice_id.return_value = "1909050"
    trading_rule.check_profit_per_day = AsyncMock()
    trading_rule.check_cooldown_after_loss = AsyncMock()
    trading_rule.check_max_trades_per_day = AsyncMock()

    db_position_manager = MagicMock()
    db_position_manager.get_open_positions_ids_actions = AsyncMock(return_value=[])

    telegram = MagicMock()
    telegram.send = AsyncMock()

    return calls, trading_orchestrator, performance_monitor, trading_rule, db_position_manager, telegram


def _run_handler(reserve_cash_percent: float, protective_stop=None):
    """Run handle_trading_signal on mocks; returns (recorded calls, message sections added)."""
    calls, orchestrator, monitor, trading_rule, db_position_manager, telegram = _handler_dependencies(
        reserve_cash_percent, protective_stop
    )
    with patch.object(trader, "TelegramMessageComposer") as composer_cls:
        asyncio.run(
            handle_trading_signal(
                _signal("uuid-1", action="short"),
                orchestrator, monitor, trading_rule, db_position_manager, "exchange1", telegram,
            )
        )
    sections = {call.args[0]: call.args[1] for call in composer_cls.return_value.add_text_section.call_args_list}
    return calls, sections


def test_classic_mode_waits_for_the_sale_before_sizing_the_new_order():
    calls, _ = _run_handler(reserve_cash_percent=0)

    assert [call[0] for call in calls.mock_calls] == [
        "close", "wait_closed", "invalidate_balance", "execute", "protective_stop",
    ]
    assert calls.close.await_args.kwargs == {"action_filter": None, "defer_bookkeeping": True}
    calls.wait_closed.assert_awaited_once_with(["old-position"])


def test_reserve_mode_opens_first_then_closes_without_waiting_for_bookkeeping():
    calls, _ = _run_handler(reserve_cash_percent=20)

    assert [call[0] for call in calls.mock_calls] == ["execute", "close", "invalidate_balance", "protective_stop"]
    assert calls.close.await_args.kwargs == {
        "action_filter": None,
        "exclude_position_id": "new-position",
        "defer_bookkeeping": True,
    }


def test_protective_stop_is_requested_for_the_new_position_and_reported():
    calls, sections = _run_handler(
        reserve_cash_percent=0,
        protective_stop={"order_id": "stop-1", "price": 7.05, "percent": -30},
    )

    calls.protective_stop.assert_awaited_once_with(_trade_result())
    assert sections["Protective Stop"] == "Sell stop at 7.05 (-30%), order stop-1"


def test_protective_stop_that_could_not_be_placed_is_reported_as_such():
    _, sections = _run_handler(
        reserve_cash_percent=0,
        protective_stop={"error": "OrderPlacementError: Saxo rejected order (400)"},
    )

    assert sections["Protective Stop"].startswith("NOT PLACED")
    assert "Saxo rejected order (400)" in sections["Protective Stop"]


def test_no_protective_stop_section_when_the_feature_is_off():
    _, sections = _run_handler(reserve_cash_percent=0, protective_stop=None)

    assert "Protective Stop" not in sections
