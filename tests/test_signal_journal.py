import asyncio
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

asyncpg_stub = types.ModuleType("asyncpg")
asyncpg_stub.Pool = type("Pool", (), {})
asyncpg_stub.Record = dict
asyncpg_stub.create_pool = AsyncMock()
sys.modules.setdefault("asyncpg", asyncpg_stub)
sys.modules.setdefault("aio_pika", types.ModuleType("aio_pika"))

import src.trader as trader
from src.trade.account_guard import AccountGuard
from src.trade.exceptions import (
    DatabaseOperationException,
    NoTurbosAvailableException,
    PositionNotFoundException,
    TradingRuleViolation,
)
from src.trade.signal_journal import SignalJournal
from src.trader import dispatch_message, handle_close_signal, handle_trading_signal, select_signals_to_dispatch


@pytest.fixture(autouse=True)
def forget_processed_signals():
    trader._processed_signal_uuids.clear()
    yield
    trader._processed_signal_uuids.clear()


def _signal(signal_uuid="uuid-1", action="long", **extra):
    return {
        "action": action,
        "indice": "us100",
        "signal_uuid": signal_uuid,
        "signal_timestamp": "2026-10-12T08:00:00Z",
        "alert_timestamp": "2026-10-12T08:00:00Z",
        "received_timestamp": "2026-10-12T08:00:00.500Z",
        **extra,
    }


class RecordingDb:
    def __init__(self, error=None, delay=0):
        self.entries = []
        self.error = error
        self.delay = delay

    async def record_decision(self, entry):
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        self.entries.append(entry)


def _dependencies():
    orchestrator = MagicMock()
    orchestrator.reserve_cash_percent = 0
    orchestrator.execute_trade_signal = AsyncMock(return_value={
        "order_details": {"order_id": "order-1"},
        "position_details": {"position_id": "position-1"},
        "selected_turbo_info": {},
    })
    orchestrator.place_protective_stop = AsyncMock(return_value=None)

    monitor = MagicMock()
    monitor.close_managed_positions_by_criteria = AsyncMock(
        return_value={"closed_initiated_count": 0, "errors_count": 0, "closed_position_ids": []}
    )
    monitor.wait_for_positions_closed = AsyncMock(return_value=True)

    trading_rule = MagicMock()
    trading_rule.get_allowed_indice_id.return_value = "1909050"
    for check in ("check_entries_paused", "check_profit_per_day", "check_cooldown_after_loss", "check_max_trades_per_day",
                  "check_day_breakers"):
        setattr(trading_rule, check, AsyncMock())
    # Off, as in a configuration without its settings (src/trade/account_guard.py).
    trading_rule.account_guard = AccountGuard()

    db_position_manager = MagicMock()
    db_position_manager.get_open_positions_ids_actions = AsyncMock(return_value=[])
    telegram = MagicMock()
    telegram.send = AsyncMock()
    return orchestrator, monitor, trading_rule, db_position_manager, telegram


async def _handle(signal, deps, journal):
    orchestrator, monitor, trading_rule, db_position_manager, telegram = deps
    with patch.object(trader, "TelegramMessageComposer"):
        await handle_trading_signal(
            signal, orchestrator, monitor, trading_rule, db_position_manager, "exchange1", telegram,
            journal=journal,
        )


def _run(scenario):
    """Run ``scenario(db, journal)`` and wait for the journal's background writes."""
    async def wrapper():
        db = RecordingDb()
        journal = SignalJournal(db)
        try:
            await scenario(db, journal)
        finally:
            await journal.wait_for_pending()
        return db

    return asyncio.run(wrapper())


# ── the journal itself ──


def test_journal_writes_in_the_background_and_keeps_the_signal_details():
    async def scenario(db, journal):
        journal.record(_signal(confidence=0.8), "executed", position_id=42, order_id="order-1")
        assert db.entries == []

    db = _run(scenario)

    entry = db.entries[0]
    assert entry["signal_uuid"] == "uuid-1"
    assert (entry["action"], entry["indice"], entry["confidence"]) == ("long", "us100", 0.8)
    assert entry["outcome"] == "executed"
    assert (entry["position_id"], entry["order_id"]) == ("42", "order-1")
    assert entry["received_timestamp"] == "2026-10-12T08:00:00.500Z"
    assert entry["decided_at"].tzinfo is not None


def test_only_the_first_outcome_of_a_signal_is_kept():
    async def scenario(db, journal):
        journal.record(_signal(), "refused", "Cooldown active")
        journal.record(_signal(), "failed", "something later")

    db = _run(scenario)

    assert [(entry["outcome"], entry["reason"]) for entry in db.entries] == [("refused", "Cooldown active")]


def test_signal_without_an_identifier_is_not_journaled():
    async def scenario(db, journal):
        journal.record({"action": "daily_stats", "indice": "n/a"}, "ignored")

    assert _run(scenario).entries == []


def test_failing_database_never_reaches_the_caller():
    async def scenario():
        journal = SignalJournal(RecordingDb(error=ConnectionError("database is down")))
        journal.record(_signal(), "executed")
        await journal.wait_for_pending()

    asyncio.run(scenario())


def test_slow_database_write_is_abandoned():
    async def scenario():
        db = RecordingDb(delay=10)
        journal = SignalJournal(db)
        with patch("src.trade.signal_journal.WRITE_TIMEOUT_SECONDS", 0.01):
            journal.record(_signal(), "executed")
            await journal.wait_for_pending(timeout=1)
        return db

    assert asyncio.run(scenario()).entries == []


def test_recording_outside_an_event_loop_does_not_raise():
    SignalJournal(RecordingDb()).record(_signal(), "executed")


# ── what the trader journals ──


def test_executed_trade_is_journaled_with_its_order_and_position():
    deps = _dependencies()

    db = _run(lambda db, journal: _handle(_signal(), deps, journal))

    assert [(e["outcome"], e["position_id"], e["order_id"]) for e in db.entries] == [
        ("executed", "position-1", "order-1")
    ]


def test_trade_is_journaled_even_when_its_notification_fails():
    deps = _dependencies()
    deps[4].send = AsyncMock(side_effect=ConnectionError("rabbitmq is down"))

    async def scenario(db, journal):
        with pytest.raises(ConnectionError):
            await _handle(_signal(), deps, journal)

    assert [entry["outcome"] for entry in _run(scenario).entries] == ["executed"]


def test_signal_refused_by_a_rule_is_journaled_with_the_reason():
    deps = _dependencies()
    deps[2].check_cooldown_after_loss = AsyncMock(
        side_effect=TradingRuleViolation("Breaking trading rule: Cooldown active after last loss.")
    )

    db = _run(lambda db, journal: _handle(_signal(), deps, journal))

    assert [(e["outcome"], e["reason"]) for e in db.entries] == [
        ("refused", "Breaking trading rule: Cooldown active after last loss.")
    ]
    deps[0].execute_trade_signal.assert_not_awaited()


def test_paused_entries_refuse_the_signal_before_anything_is_closed_or_opened():
    orchestrator, monitor, trading_rule, db_position_manager, telegram = deps = _dependencies()
    trading_rule.check_entries_paused = AsyncMock(
        side_effect=TradingRuleViolation("Breaking trading rule: New entries are paused by the operator since ...")
    )

    db = _run(lambda db, journal: _handle(_signal(), deps, journal))

    monitor.close_managed_positions_by_criteria.assert_not_awaited()
    orchestrator.execute_trade_signal.assert_not_awaited()
    db_position_manager.get_open_positions_ids_actions.assert_not_awaited()
    assert db.entries[0]["outcome"] == "refused"
    assert "paused by the operator" in db.entries[0]["reason"]


def test_trade_that_could_not_be_set_up_is_journaled_as_failed():
    deps = _dependencies()
    deps[0].execute_trade_signal = AsyncMock(side_effect=NoTurbosAvailableException("No turbo in the price range"))

    db = _run(lambda db, journal: _handle(_signal(), deps, journal))

    assert db.entries[0]["outcome"] == "failed"
    assert "NoTurbosAvailableException" in db.entries[0]["reason"]


def test_order_that_gave_no_position_is_journaled_with_its_id():
    deps = _dependencies()
    deps[0].execute_trade_signal = AsyncMock(side_effect=PositionNotFoundException(
        "Position not found after 14 retries for order order-9", order_id="order-9",
        cancellation_attempted=True, cancellation_succeeded=True,
    ))

    async def scenario(db, journal):
        with pytest.raises(PositionNotFoundException):
            await _handle(_signal(), deps, journal)

    db = _run(scenario)

    # The order's id is what links the signal to the order shown in the admin interface.
    assert [(e["outcome"], e["order_id"], e["position_id"]) for e in db.entries] == [("failed", "order-9", None)]
    assert "Order cancellation attempt: succeeded" in db.entries[0]["reason"]


def test_critical_error_is_journaled_once_and_still_raised():
    orchestrator, monitor, trading_rule, db_position_manager, telegram = _dependencies()
    orchestrator.execute_trade_signal = AsyncMock(side_effect=DatabaseOperationException("insert failed"))

    async def scenario(db, journal):
        with patch.object(trader, "TelegramMessageComposer"), pytest.raises(DatabaseOperationException):
            await dispatch_message(
                _signal(),
                trading_orchestrator=orchestrator, performance_monitor=monitor, trading_rule=trading_rule,
                db_position_manager=db_position_manager, db_perf_manager=MagicMock(),
                trade_turbo_exchange_id="exchange1", telegram=telegram,
                position_service=MagicMock(), milestones_eur=[], journal=journal,
            )

    db = _run(scenario)

    assert [entry["outcome"] for entry in db.entries] == ["failed"]
    assert "DatabaseOperationException" in db.entries[0]["reason"]


def test_unexpected_error_is_journaled_by_the_dispatcher():
    orchestrator, monitor, trading_rule, db_position_manager, telegram = _dependencies()
    orchestrator.execute_trade_signal = AsyncMock(side_effect=RuntimeError("boom"))

    async def scenario(db, journal):
        with patch.object(trader, "TelegramMessageComposer"):
            await dispatch_message(
                _signal(),
                trading_orchestrator=orchestrator, performance_monitor=monitor, trading_rule=trading_rule,
                db_position_manager=db_position_manager, db_perf_manager=MagicMock(),
                trade_turbo_exchange_id="exchange1", telegram=telegram,
                position_service=MagicMock(), milestones_eur=[], journal=journal,
            )

    db = _run(scenario)

    assert [(entry["outcome"], entry["reason"]) for entry in db.entries] == [("failed", "RuntimeError: boom")]


@pytest.mark.parametrize("closed,errors,outcome", [(2, 0, "closed"), (0, 1, "failed"), (0, 0, "ignored")])
def test_close_signal_is_journaled_by_what_it_did(closed, errors, outcome):
    monitor = MagicMock()
    monitor.close_managed_positions_by_criteria = AsyncMock(
        return_value={"closed_initiated_count": closed, "errors_count": errors, "closed_position_ids": []}
    )
    telegram = MagicMock()
    telegram.send = AsyncMock()

    db = _run(lambda db, journal: handle_close_signal(
        _signal(action="close-position"), monitor, telegram, journal=journal,
    ))

    assert [entry["outcome"] for entry in db.entries] == [outcome]


def test_signal_stored_before_startup_is_journaled_as_ignored():
    async def scenario(db, journal):
        to_dispatch = select_signals_to_dispatch(
            [_signal(received_timestamp="2020-01-01T00:00:00.000Z")],
            first_poll=True, startup_replay_max_age_seconds=10, journal=journal,
        )
        assert to_dispatch == []

    db = _run(scenario)

    assert [(entry["outcome"], entry["reason"]) for entry in db.entries] == [
        ("ignored", "Stored before the trader started: not replayed")
    ]


def test_handlers_work_without_a_journal():
    deps = _dependencies()

    asyncio.run(_handle(_signal(), deps, None))

    deps[0].execute_trade_signal.assert_awaited_once()
