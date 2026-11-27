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
from src.trade.account_guard import AccountGuard
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


def test_a_stop_cuts_off_the_wait_for_signals_at_once():
    async def scenario():
        stopping = asyncio.Event()
        waiting = asyncio.Event()
        cancelled = []

        async def long_poll():
            waiting.set()
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                cancelled.append(True)
                raise

        client = MagicMock(interval_ms=0, long_poll_wait_ms=1000, startup_replay_max_age_seconds=10)
        client.get_new_signals = long_poll
        dispatch = AsyncMock()
        loop_task = asyncio.create_task(poll_and_dispatch_signals(client, dispatch, stopping=stopping))
        await waiting.wait()
        stopping.set()
        await asyncio.wait_for(loop_task, 1)
        return cancelled, dispatch

    cancelled, dispatch = asyncio.run(scenario())

    assert cancelled == [True]
    dispatch.assert_not_awaited()


def test_a_stop_never_cuts_off_the_signal_in_hand_and_no_poll_follows_it():
    async def scenario():
        stopping = asyncio.Event()
        order_sent = asyncio.Event()
        finished = []
        polls = []

        async def poll():
            polls.append(True)
            return [_signal("uuid-1")]

        async def dispatch(signal):
            order_sent.set()
            # Saxo answering the order, while the stop is asked.
            await asyncio.sleep(0.05)
            finished.append(signal["signal_uuid"])

        client = MagicMock(interval_ms=0, long_poll_wait_ms=1000, startup_replay_max_age_seconds=10)
        client.get_new_signals = poll
        loop_task = asyncio.create_task(poll_and_dispatch_signals(client, dispatch, stopping=stopping))
        await order_sent.wait()
        stopping.set()
        await asyncio.wait_for(loop_task, 1)
        return finished, polls

    finished, polls = asyncio.run(scenario())

    assert finished == ["uuid-1"]
    assert polls == [True]


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
    trading_rule.check_day_breakers = AsyncMock()
    # Off, as in a configuration without its settings (src/trade/account_guard.py).
    trading_rule.account_guard = AccountGuard()

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


# ── the balance an entry is sized on: read again, unless the last reading is known to be the cash there is ──

from src.trade.flat_balance import FlatBalance  # noqa: E402
from src.trader import SignalInHand, dispatch_message, nothing_closed, rehearsal_steps  # noqa: E402

NOTHING_TO_CLOSE = {"closed_initiated_count": 0, "errors_count": 0, "closed_position_ids": [], "not_closed": [], "not_listed": []}


def _proven() -> FlatBalance:
    flat = FlatBalance()
    flat.read(flat.reading(), True, True)
    return flat


def _run_handler_with(flat, *, reserve_cash_percent=0, close_result=NOTHING_TO_CLOSE, open_positions=(), execute=None):
    calls, orchestrator, monitor, trading_rule, db_position_manager, telegram = _handler_dependencies(reserve_cash_percent)
    monitor.close_managed_positions_by_criteria.return_value = close_result
    db_position_manager.get_open_positions_ids_actions.return_value = list(open_positions)
    if execute is not None:
        orchestrator.execute_trade_signal.side_effect = execute
    with patch.object(trader, "TelegramMessageComposer"):
        asyncio.run(
            handle_trading_signal(
                _signal("uuid-1", action="short"),
                orchestrator, monitor, trading_rule, db_position_manager, "exchange1", telegram,
                flat_balance=flat,
            )
        )
    return [call[0] for call in calls.mock_calls]


def test_an_entry_while_nothing_is_held_is_sized_on_the_balance_last_read_when_it_stands():
    flat = _proven()

    order = _run_handler_with(flat)

    # No balance forgotten before the order: the orchestrator may use the one it holds.
    assert order == ["close", "wait_closed", "execute", "protective_stop"]
    # And once the order went, that reading stands no more.
    assert not flat.usable(True)


def test_an_entry_reads_the_balance_again_when_no_reading_stands():
    order = _run_handler_with(FlatBalance())

    assert order == ["close", "wait_closed", "invalidate_balance", "execute", "protective_stop"]


@pytest.mark.parametrize("close_result", [
    {**NOTHING_TO_CLOSE, "closed_initiated_count": 1, "closed_position_ids": ["old-position"]},
    {**NOTHING_TO_CLOSE, "errors_count": 1, "not_closed": [{"position_id": "old", "reason": "r", "why": "refused"}]},
    {**NOTHING_TO_CLOSE, "not_listed": ["old-position"]},
])
def test_an_entry_that_sold_or_tried_to_sell_something_reads_the_balance_again(close_result):
    flat = _proven()

    order = _run_handler_with(flat, close_result=close_result)

    assert order[:4] == ["close", "wait_closed", "invalidate_balance", "execute"]
    assert not nothing_closed(close_result) and nothing_closed(NOTHING_TO_CLOSE)


def test_an_entry_against_an_open_position_reads_the_balance_again_whatever_the_close_says():
    flat = _proven()

    order = _run_handler_with(flat, open_positions=[{"position_id": "old-position", "action": "long"}])

    assert order[:4] == ["close", "wait_closed", "invalidate_balance", "execute"]


def test_with_a_cash_reserve_the_entry_reads_the_balance_again_unless_the_last_reading_stands():
    assert _run_handler_with(FlatBalance(), reserve_cash_percent=20) == [
        "invalidate_balance", "execute", "close", "invalidate_balance", "protective_stop",
    ]
    assert _run_handler_with(_proven(), reserve_cash_percent=20) == [
        "execute", "close", "invalidate_balance", "protective_stop",
    ]


def test_no_reading_stands_after_an_entry_that_failed():
    from src.trade.exceptions import InsufficientFundsException

    flat = _proven()

    _run_handler_with(flat, execute=InsufficientFundsException("Insufficient funds @ 9.7"))

    assert not flat.usable(True)


def test_without_the_rule_an_entry_reads_the_balance_again_as_it_always_did():
    order = _run_handler_with(None)

    assert order == ["close", "wait_closed", "invalidate_balance", "execute", "protective_stop"]


def test_a_close_signal_leaves_no_reading_standing():
    flat = _proven()
    orchestrator, monitor, telegram = MagicMock(), MagicMock(), MagicMock()
    monitor.close_managed_positions_by_criteria = AsyncMock(
        return_value={**NOTHING_TO_CLOSE, "closed_initiated_count": 1, "closed_position_ids": ["old-position"]}
    )
    telegram.send = AsyncMock()

    with patch.object(trader, "TelegramMessageComposer"):
        asyncio.run(dispatch_message(
            _signal("uuid-2", action="close-position"),
            trading_orchestrator=orchestrator, performance_monitor=monitor, trading_rule=MagicMock(),
            db_position_manager=MagicMock(), db_perf_manager=MagicMock(), trade_turbo_exchange_id="exchange1",
            telegram=telegram, position_service=MagicMock(), milestones_eur=[], flat_balance=flat,
        ))

    orchestrator.invalidate_spending_power.assert_called_once_with()
    assert not flat.usable(True)


# ── the keep-warm timer: the reading that may stand, and a signal's way rehearsed ──

def _keep_warm(orchestrator, balances, **more):
    """The timer run once per balance: each is what Saxo answers (an exception is raised, a function called)."""
    answers = iter(balances)

    async def read():
        try:
            answer = next(answers)
        except StopIteration:
            raise asyncio.CancelledError from None
        if callable(answer):
            answer = answer()
        if isinstance(answer, Exception):
            raise answer
        return answer

    orchestrator.refresh_spending_power = AsyncMock(side_effect=read)

    async def scenario():
        try:
            await keep_saxo_session_warm(orchestrator, 0, **more)
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())


def _positions(*answers):
    manager = MagicMock()
    manager.get_open_positions_ids_actions = AsyncMock(side_effect=list(answers))
    return manager


@pytest.mark.parametrize("before, after, balance, stands", [
    ([], [], 128.61, True),
    ([{"position_id": "p", "action": "long"}], [], 3.02, False),      # read while a position was open
    ([], [{"position_id": "p", "action": "long"}], 3.02, False),      # one was bought meanwhile
    ([], [], RuntimeError("Saxo unavailable"), False),                # no balance was read
    (RuntimeError("the database is away"), [], 128.61, False),        # what is open is not known
])
def test_the_timer_s_reading_stands_only_when_nothing_was_open_before_and_after_it(before, after, balance, stands):
    flat = FlatBalance()

    _keep_warm(MagicMock(), [balance], flat_balance=flat, db_position_manager=_positions(before, after))

    assert flat.usable(True) is stands


def test_a_reading_begun_before_a_signal_s_order_does_not_stand():
    flat = FlatBalance()

    def read():
        # A signal is handled while Saxo answers: its order goes.
        flat.moved()
        return 128.61

    _keep_warm(MagicMock(), [read], flat_balance=flat, db_position_manager=_positions([], []))

    assert not flat.usable(True)


def test_the_timer_rehearses_a_signal_s_way_with_the_balance_it_read_unless_a_signal_is_in_hand():
    orchestrator = MagicMock()
    in_hand = SignalInHand()
    ran, given = [], []

    def rehearsal(spending_power):
        given.append(spending_power)
        return [("a check", lambda: ran.append("check")), ("a read", AsyncMock(side_effect=lambda: ran.append("read")))]

    _keep_warm(orchestrator, [128.61], rehearsal=rehearsal, busy=in_hand)
    assert (given, ran) == ([128.61], ["check", "read"])

    with in_hand:
        assert in_hand()
        _keep_warm(orchestrator, [131.0], rehearsal=rehearsal, busy=in_hand)
    assert not in_hand() and given == [128.61]

    _keep_warm(orchestrator, [131.0], rehearsal=rehearsal, busy=in_hand)
    assert given == [128.61, 131.0]


def test_a_rehearsal_that_cannot_be_made_does_not_stop_the_timer():
    orchestrator = MagicMock()

    def rehearsal(_spending_power):
        raise RuntimeError("no rule to read")

    _keep_warm(orchestrator, [1.0, 2.0], rehearsal=rehearsal)

    # Both balances were read, and the timer went on to the turn that ended the test.
    assert orchestrator.refresh_spending_power.await_count == 3


def _rehearsed_trader(spending_power=128.61, turbo=None):
    trading_rule = MagicMock()
    trading_rule.allowed_indices_rule_config = {"indice_ids": {"us100": "1909050"}}
    for check in ("check_entries_paused", "check_profit_per_day", "check_cooldown_after_loss",
                  "check_max_trades_per_day", "check_day_breakers", "check_account_guard", "guard_account"):
        setattr(trading_rule, check, AsyncMock())
    db_position_manager = MagicMock()
    db_position_manager.get_open_positions_ids_actions = AsyncMock(return_value=[])
    orchestrator = MagicMock()
    orchestrator.execute_trade_signal = AsyncMock()
    orchestrator.refresh_spending_power = AsyncMock()
    watchlist = MagicMock()
    watchlist.get_best_turbo = AsyncMock(return_value=turbo)
    steps = rehearsal_steps(trading_rule, db_position_manager, orchestrator, watchlist, spending_power)
    return steps, trading_rule, db_position_manager, orchestrator, watchlist


def test_a_rehearsal_runs_what_an_entry_runs_before_its_order_and_nothing_that_acts():
    from src.trade.rehearsal import rehearse

    turbo = {"selected_instrument": {"uic": 62423559, "latest_ask": 9.733}}
    steps, trading_rule, db_position_manager, orchestrator, watchlist = _rehearsed_trader(turbo=turbo)

    with patch.object(trader, "TelegramMessageComposer") as composer:
        took = asyncio.run(rehearse(steps))

    assert took.steps == len(steps) == 14
    # The trader's checks, in the handler's order.
    checks = [call[0] for call in trading_rule.mock_calls if call[0].startswith("check_")]
    assert checks == [
        "check_signal_timestamp", "check_market_hours", "check_macro_news", "check_entries_paused",
        "check_profit_per_day", "check_cooldown_after_loss", "check_max_trades_per_day", "check_day_breakers",
    ]
    db_position_manager.get_open_positions_ids_actions.assert_awaited_once_with()
    composer.assert_called_once()
    # The turbo the watchlist would give each way, and the order's size on it with the balance just read.
    assert [call.kwargs for call in watchlist.get_best_turbo.await_args_list] == [
        {"underlying_uic": "1909050", "direction": "long"}, {"underlying_uic": "1909050", "direction": "short"},
    ]
    orchestrator._calculate_position_scale.assert_called_once_with(None)
    assert [call.args for call in orchestrator._calculate_bid_amount.call_args_list] == [(turbo, 128.61, 1.0)] * 2
    # Nothing that acts: no order, no balance read of its own, and never the account guard, which can pause entries.
    orchestrator.execute_trade_signal.assert_not_called()
    orchestrator.refresh_spending_power.assert_not_called()
    orchestrator.invalidate_spending_power.assert_not_called()
    trading_rule.check_account_guard.assert_not_called()
    trading_rule.guard_account.assert_not_called()


def test_a_rehearsal_sizes_no_order_without_a_turbo_or_a_balance_and_asks_no_watchlist_that_is_not_there():
    from src.trade.rehearsal import rehearse

    steps, _, _, orchestrator, _ = _rehearsed_trader(turbo=None)
    with patch.object(trader, "TelegramMessageComposer"):
        asyncio.run(rehearse(steps))
    orchestrator._calculate_bid_amount.assert_not_called()

    steps, _, _, orchestrator, _ = _rehearsed_trader(spending_power=None, turbo={"selected_instrument": {"latest_ask": 9.7}})
    with patch.object(trader, "TelegramMessageComposer"):
        asyncio.run(rehearse(steps))
    orchestrator._calculate_bid_amount.assert_not_called()

    trading_rule = MagicMock()
    trading_rule.allowed_indices_rule_config = {"indice_ids": {"us100": "1909050"}}
    assert len(rehearsal_steps(trading_rule, MagicMock(), MagicMock(), None, 128.61)) == 12


def test_the_message_a_rehearsal_checks_is_one_the_trader_takes():
    from src.schema.validators import check_trading_action

    seen = []
    with patch.object(trader, "check_trading_action", side_effect=lambda body: (seen.append(body), check_trading_action(body))):
        steps, *_ = _rehearsed_trader()
        dict(steps)["the message's form"]()

    assert [body["action"] for body in seen] == ["long", "close-position"] and seen[0]["indice"] == "us100"


# ── an entry refused while a position is held the other way (src/trade/refused_entry.py) ──

from src.trade.exceptions import TradingRuleViolation  # noqa: E402
from src.trade.refused_entry import not_sold, said  # noqa: E402

PAUSED = "Breaking trading rule: New entries are paused by the operator since 2026-11-27 10:00:00+00:00."
SOLD_ONE = {**NOTHING_TO_CLOSE, "closed_initiated_count": 1, "closed_position_ids": ["p1"]}


def _refused(refuse, *, action="short", held=("long",), close_result=SOLD_ONE, close_on_refused=True, confidence=None,
             hand_over=None, flat=None, watch=None):
    """A signal the rules refuse (``refuse`` sets the refusal on the mocks) while positions are held."""
    calls, orchestrator, monitor, trading_rule, db_position_manager, telegram = _handler_dependencies(0)
    monitor.close_managed_positions_by_criteria.return_value = close_result
    db_position_manager.get_open_positions_ids_actions.return_value = [
        {"position_id": f"p{number}", "action": direction} for number, direction in enumerate(held, start=1)
    ]
    refuse(trading_rule, monitor)
    signal = _signal("uuid-1", action=action)
    if confidence is not None:
        signal["confidence"] = confidence
    with patch.object(trader, "TelegramMessageComposer"), patch.object(trader, "journal_signal") as journal_signal:
        asyncio.run(handle_trading_signal(
            signal, orchestrator, monitor, trading_rule, db_position_manager, "exchange1", telegram,
            hand_over=hand_over, flat_balance=flat, watch=watch, close_on_refused=close_on_refused,
        ))
    _, _, outcome, reason = journal_signal.call_args.args
    assert journal_signal.call_count == 1 and outcome == trader.REFUSED
    orchestrator.execute_trade_signal.assert_not_awaited()
    return reason, calls, telegram


def _paused(trading_rule, monitor):
    trading_rule.check_entries_paused.side_effect = TradingRuleViolation(PAUSED)


def _raising(check, text="Breaking trading rule: no entry now"):
    def refuse(trading_rule, monitor):
        getattr(trading_rule, check).side_effect = TradingRuleViolation(text)
    return refuse


def test_an_entry_refused_while_the_other_side_is_held_still_sells_it():
    flat = _proven()

    reason, calls, telegram = _refused(_paused, flat=flat)

    # Only the other direction, as a close signal sells it, then no balance read before stands.
    assert [call[0] for call in calls.mock_calls] == ["close", "invalidate_balance"]
    assert calls.close.await_args.kwargs == {"action_filter": "long", "defer_bookkeeping": True}
    assert not flat.usable(True)
    assert reason == said(PAUSED, "long", 1) == f"{PAUSED[:-1]}; closed 1 long position(s) the signal turned against"
    telegram.send.assert_awaited_once_with(f"SHORT refused: {reason}")


@pytest.mark.parametrize("check", [
    "check_market_hours", "check_macro_news", "check_entries_paused", "check_profit_per_day",
    "check_cooldown_after_loss", "check_max_trades_per_day", "check_day_breakers",
])
def test_whatever_of_watas_own_state_refuses_the_entry_the_other_side_is_sold(check):
    reason, calls, _ = _refused(_raising(check), action="long", held=("short",))

    assert calls.close.await_args.kwargs == {"action_filter": "short", "defer_bookkeeping": True}
    assert reason == "Breaking trading rule: no entry now; closed 1 short position(s) the signal turned against"


def test_an_entry_refused_for_saxos_login_still_asks_for_the_sale():
    watch = MagicMock()
    watch.login_refusal.return_value = "Breaking trading rule: Saxo refuses to renew WATA's login since 20:05: ..."

    reason, calls, _ = _refused(lambda trading_rule, monitor: None, watch=watch)

    assert calls.close.await_args.kwargs["action_filter"] == "long"
    assert reason == said(watch.login_refusal.return_value, "long", 1)


def test_a_refused_entry_never_sells_a_position_of_its_own_direction():
    # The pause is checked before the duplicate: a refused "Resume Short" while short must sell nothing.
    reason, calls, telegram = _refused(_paused, action="short", held=("short",))

    assert calls.mock_calls == [] and reason == PAUSED
    telegram.send.assert_not_awaited()


def test_a_refused_entry_with_nothing_held_stays_a_silent_refusal():
    reason, calls, telegram = _refused(_paused, held=())

    assert calls.mock_calls == [] and reason == PAUSED
    telegram.send.assert_not_awaited()


@pytest.mark.parametrize("check", ["check_signal_timestamp", "get_allowed_indice_id"])
def test_a_signal_that_is_itself_turned_down_sells_nothing(check):
    reason, calls, telegram = _refused(_raising(check, "Signal timestamp is too old"))

    assert calls.mock_calls == [] and reason == "Signal timestamp is too old"
    telegram.send.assert_not_awaited()


def test_a_signal_under_the_confidence_wanted_sells_nothing_though_the_pause_refused_it_first():
    def refuse(trading_rule, monitor):
        _paused(trading_rule, monitor)
        trading_rule.check_confidence_threshold.side_effect = TradingRuleViolation("Signal confidence (0.10) is below ...")

    reason, calls, _ = _refused(refuse, confidence=0.1)

    assert calls.mock_calls == [] and reason == PAUSED


def test_without_the_setting_a_refused_entry_closes_nothing_as_before():
    reason, calls, telegram = _refused(_paused, close_on_refused=False)

    assert calls.mock_calls == [] and reason == PAUSED
    telegram.send.assert_not_awaited()


def test_what_the_refused_entrys_sale_could_not_sell_is_handed_to_the_position_monitor_and_said():
    unsold = {**NOTHING_TO_CLOSE, "errors_count": 1, "not_closed": [
        {"position_id": "p1", "reason": "Explicit Close (long)", "why": "Saxo says it cannot be closed now"},
    ]}
    hand_over = AsyncMock(return_value=True)

    reason, _, telegram = _refused(_paused, close_result=unsold, hand_over=hand_over)

    hand_over.assert_awaited_once_with("p1", "Explicit Close (long)")
    assert reason == (f"{PAUSED[:-1]}; closed 0 long position(s) the signal turned against. "
                      "Not sold: p1 (Saxo says it cannot be closed now), handed to the position monitor.")
    telegram.send.assert_awaited_once_with(f"SHORT refused: {reason}")


def test_a_position_the_monitor_sold_meanwhile_leaves_the_refusal_as_it_was():
    reason, calls, telegram = _refused(_paused, close_result=NOTHING_TO_CLOSE)

    assert [call[0] for call in calls.mock_calls] == ["close", "invalidate_balance"] and reason == PAUSED
    telegram.send.assert_not_awaited()


def test_a_sale_that_cannot_even_be_tried_is_said_and_the_signal_stays_refused():
    def refuse(trading_rule, monitor):
        _paused(trading_rule, monitor)
        monitor.close_managed_positions_by_criteria.side_effect = RuntimeError("Saxo did not answer")

    flat = _proven()
    reason, calls, telegram = _refused(refuse, flat=flat)

    assert reason == not_sold(PAUSED, "long", "RuntimeError: Saxo did not answer")
    assert [call[0] for call in calls.mock_calls] == ["close", "invalidate_balance"] and not flat.usable(True)
    telegram.send.assert_awaited_once_with(f"SHORT refused: {reason}")


def test_positions_that_cannot_be_read_leave_the_refusal_as_it_was():
    def refuse(trading_rule, monitor):
        trading_rule.check_market_hours.side_effect = TradingRuleViolation("Signal is outside of market hours.")

    calls, orchestrator, monitor, trading_rule, db_position_manager, telegram = _handler_dependencies(0)
    refuse(trading_rule, monitor)
    db_position_manager.get_open_positions_ids_actions.side_effect = RuntimeError("database down")
    with patch.object(trader, "TelegramMessageComposer"), patch.object(trader, "journal_signal") as journal_signal:
        asyncio.run(handle_trading_signal(
            _signal("uuid-1", action="short"), orchestrator, monitor, trading_rule, db_position_manager, "exchange1",
            telegram, close_on_refused=True,
        ))

    assert journal_signal.call_args.args[2:] == (trader.REFUSED, "Signal is outside of market hours.")
    assert calls.mock_calls == []


def test_the_dispatcher_hands_the_setting_to_the_entrys_handler():
    with patch.object(trader, "handle_trading_signal", new=AsyncMock()) as handler:
        asyncio.run(dispatch_message(
            _signal("uuid-3", action="long"),
            trading_orchestrator=MagicMock(), performance_monitor=MagicMock(), trading_rule=MagicMock(),
            db_position_manager=MagicMock(), db_perf_manager=MagicMock(), trade_turbo_exchange_id="exchange1",
            telegram=MagicMock(), position_service=MagicMock(), milestones_eur=[], close_on_refused=True,
        ))

    assert handler.await_args.kwargs["close_on_refused"] is True
