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
    trading_rule.check_entries_paused = AsyncMock()
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


# ── what the trader could not sell ──


def test_with_a_cash_reserve_an_entry_that_fails_still_sells_the_other_side():
    # 14 October 2026: the short after CPI found 4.86 EUR for a turbo of 8.63, and the long it was to replace was kept.
    calls, orchestrator, monitor, trading_rule, db_position_manager, telegram = _handler_dependencies(20)
    orchestrator.execute_trade_signal = AsyncMock(side_effect=trader.InsufficientFundsException("Insufficient funds @ 8.633"))
    calls.attach_mock(orchestrator.execute_trade_signal, "execute")

    with patch.object(trader, "TelegramMessageComposer") as composer_cls, patch.object(trader, "journal_signal") as journal_signal:
        asyncio.run(handle_trading_signal(
            _signal("uuid-1", action="short"), orchestrator, monitor, trading_rule, db_position_manager, "exchange1",
            telegram,
        ))

    assert [call[0] for call in calls.mock_calls] == ["execute", "close", "invalidate_balance"]
    assert calls.close.await_args.kwargs == {"action_filter": None, "defer_bookkeeping": True}
    sections = {call.args[0]: call.args[1] for call in composer_cls.return_value.add_text_section.call_args_list}
    assert sections["Closure Without Entry"] == "Closed existing positions. Initiated: 1, Errors: 0"
    # The entry is still the signal's outcome.
    _, _, outcome, reason = journal_signal.call_args.args
    assert (outcome, reason) == (trader.FAILED, "InsufficientFundsException: Insufficient funds @ 8.633")
    telegram.send.assert_awaited_once()


def test_what_a_flip_could_not_sell_is_handed_to_the_position_monitor():
    calls, orchestrator, monitor, trading_rule, db_position_manager, telegram = _handler_dependencies(0)
    monitor.close_managed_positions_by_criteria = AsyncMock(return_value={
        "closed_initiated_count": 0, "errors_count": 1, "closed_position_ids": [],
        "not_closed": [{"position_id": "old-position", "action": "long", "reason": "Explicit Close (All)",
                        "why": "OrderPlacementError: no quote"}],
        "not_listed": [],
    })
    handed = []

    async def hand_over(position_id, reason):
        handed.append((position_id, reason))
        return True

    with patch.object(trader, "TelegramMessageComposer") as composer_cls:
        asyncio.run(handle_trading_signal(
            _signal("uuid-1", action="short"), orchestrator, monitor, trading_rule, db_position_manager, "exchange1",
            telegram, hand_over=hand_over,
        ))

    assert handed == [("old-position", "Explicit Close (All)")]
    sections = {call.args[0]: call.args[1] for call in composer_cls.return_value.add_text_section.call_args_list}
    assert sections["Pre-Trade Closure"].endswith(
        "\nNot sold: old-position (OrderPlacementError: no quote), handed to the position monitor."
    )


def _close_monitor(result):
    monitor = MagicMock()
    monitor.close_managed_positions_by_criteria = AsyncMock(return_value={
        "closed_initiated_count": 0, "errors_count": 0, "closed_position_ids": [], "not_closed": [], "not_listed": [],
        **result,
    })
    telegram = MagicMock()
    telegram.send = AsyncMock()
    return monitor, telegram


def test_a_close_signal_that_could_not_sell_is_failed_with_why_and_handed_over():
    monitor, telegram = _close_monitor({"not_closed": [{
        "position_id": "p1", "action": "long", "reason": "Explicit Close (All)", "why": "Saxo says it cannot be closed now",
    }]})
    handed = []

    async def hand_over(position_id, reason):
        handed.append(position_id)
        return False

    with patch.object(trader, "journal_signal") as journal_signal:
        asyncio.run(trader.handle_close_signal(
            {"action": "close-position", "indice": "n/a"}, monitor, telegram, hand_over=hand_over,
        ))

    assert handed == ["p1"]
    _, _, outcome, reason = journal_signal.call_args.args
    assert outcome == trader.FAILED
    assert reason == "Closed 0 position(s). Not sold: p1 (Saxo says it cannot be closed now), NOT handed to the position monitor."
    telegram.send.assert_awaited_once()


def test_a_position_open_in_wata_but_not_at_saxo_is_not_called_nothing_to_close():
    monitor, telegram = _close_monitor({"not_listed": ["p1"]})

    with patch.object(trader, "journal_signal") as journal_signal:
        asyncio.run(trader.handle_close_signal({"action": "close-position", "indice": "n/a"}, monitor, telegram))

    _, _, outcome, reason = journal_signal.call_args.args
    assert outcome == trader.IGNORED
    assert reason == "Open in WATA's records but not at Saxo: p1 (left to the reconciliation)"


def test_no_new_position_is_taken_while_saxo_refuses_to_renew_the_login():
    calls, orchestrator, monitor, trading_rule, db_position_manager, telegram = _handler_dependencies(0)
    watch = MagicMock()
    watch.login_refusal.return_value = "Breaking trading rule: Saxo refuses to renew WATA's login since 20:05: ..."

    with patch.object(trader, "TelegramMessageComposer"), patch.object(trader, "journal_signal") as journal_signal:
        asyncio.run(handle_trading_signal(
            _signal("uuid-1", action="short"), orchestrator, monitor, trading_rule, db_position_manager, "exchange1",
            telegram, watch=watch,
        ))

    # Refused before anything is closed or bought, as the operator's pause is.
    assert calls.mock_calls == []
    _, _, outcome, reason = journal_signal.call_args.args
    assert (outcome, reason) == (trader.REFUSED, watch.login_refusal.return_value)


def test_the_polling_loop_tells_the_watch_what_it_polled_and_received():
    from src.trader import poll_and_dispatch_signals

    signals = [[{"signal_uuid": "u1", "action": "long", "indice": "us100"},
                {"signal_uuid": "u2", "action": "close-position", "indice": "n/a"}]]
    client = MagicMock()
    client.interval_ms = 1
    client.long_poll_wait_ms = 1
    client.startup_replay_max_age_seconds = 3600

    async def get_new_signals():
        if signals:
            return signals.pop()
        raise asyncio.CancelledError

    client.get_new_signals = get_new_signals
    watch = MagicMock()
    watch.polled = AsyncMock()
    dispatch = AsyncMock()

    with patch.object(trader, "_received_before_startup_window", return_value=False):
        try:
            asyncio.run(poll_and_dispatch_signals(client, dispatch, watch=watch))
        except asyncio.CancelledError:
            pass

    watch.polled.assert_awaited_with(True)
    # The scheduler's close (indice n/a) says nothing of TradingView's alerts.
    watch.signal_received.assert_called_once_with()
    assert dispatch.await_count == 2
