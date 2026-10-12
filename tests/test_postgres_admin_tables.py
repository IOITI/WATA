import asyncio
import sys
import types
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

asyncpg_stub = types.ModuleType("asyncpg")
asyncpg_stub.Pool = object
asyncpg_stub.Record = dict
asyncpg_stub.create_pool = None
sys.modules.setdefault("asyncpg", asyncpg_stub)

from src.database.postgres import (
    SCHEMA_SQL,
    AsyncDbBalanceHistoryManager,
    AsyncDbLiveValueManager,
    AsyncDbRiskStateManager,
    AsyncDbSignalJournalManager,
)
from src.trade.exceptions import TradingRuleViolation
from src.trade.rules import TradingRule


class FakeConnMgr:
    def __init__(self, value=None, error=None):
        self.calls = []
        self.value = value
        self.error = error

    async def execute(self, query, *args):
        if self.error:
            raise self.error
        self.calls.append((query, args))
        return "OK"

    async def fetchval(self, query, *args):
        self.calls.append((query, args))
        return self.value


NOW = datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc)


def test_schema_creates_the_tables_the_admin_reads_without_touching_existing_ones():
    for table in ("position_live_value", "signal_journal", "account_balance_history"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in SCHEMA_SQL
    assert "DROP " not in SCHEMA_SQL
    assert "ALTER " not in SCHEMA_SQL


# ── pause of new entries ──


def test_pausing_keeps_the_time_of_a_pause_already_in_place():
    db = FakeConnMgr()

    asyncio.run(AsyncDbRiskStateManager(db).pause_entries(NOW))

    query, args = db.calls[0]
    assert args == ("entries_paused_since", NOW)
    assert "ON CONFLICT (key) DO NOTHING" in query


def test_resuming_removes_the_pause_and_nothing_else():
    db = FakeConnMgr()

    asyncio.run(AsyncDbRiskStateManager(db).resume_entries())

    query, args = db.calls[0]
    assert query.startswith("DELETE FROM trade_risk_state WHERE key = $1")
    assert args == ("entries_paused_since",)


def test_pause_and_loss_cooldown_use_different_keys():
    assert AsyncDbRiskStateManager.ENTRIES_PAUSED_KEY != AsyncDbRiskStateManager.LAST_LOSS_KEY

    db = FakeConnMgr(value=NOW)
    assert asyncio.run(AsyncDbRiskStateManager(db).get_entries_paused_since()) == NOW
    assert db.calls[0][1] == ("entries_paused_since",)


def _trading_rule(risk_state_manager):
    rules = {
        "allowed_indices": {"indice_ids": {"us100": "1909050"}},
        "market_closed_dates": {"market_closed_dates": []},
        "day_trading": {"dont_enter_trade_if_day_profit_is_more_than": 1.25, "max_day_loss_percent": -25.0},
        "signal_validation": {"max_signal_age_seconds": 8},
        "market_hours": {},
    }
    config_manager = MagicMock()
    config_manager.get_config_value.side_effect = lambda key, default=None: (
        [{"rule_type": rule_type, "rule_config": rule_config} for rule_type, rule_config in rules.items()]
        if key == "trade.rules"
        else default
    )
    return TradingRule(config_manager, MagicMock(), risk_state_manager)


def test_rule_refuses_entries_while_paused_and_says_since_when():
    risk_state = MagicMock()
    risk_state.get_entries_paused_since = AsyncMock(return_value=NOW)

    with pytest.raises(TradingRuleViolation, match="paused by the operator since 2026-10-12"):
        asyncio.run(_trading_rule(risk_state).check_entries_paused())


def test_rule_lets_entries_through_when_not_paused():
    risk_state = MagicMock()
    risk_state.get_entries_paused_since = AsyncMock(return_value=None)

    asyncio.run(_trading_rule(risk_state).check_entries_paused())
    asyncio.run(_trading_rule(None).check_entries_paused())


def test_rule_does_not_hide_a_database_failure_as_not_paused():
    risk_state = MagicMock()
    risk_state.get_entries_paused_since = AsyncMock(side_effect=ConnectionError("database is down"))

    with pytest.raises(ConnectionError):
        asyncio.run(_trading_rule(risk_state).check_entries_paused())


# ── live position values ──


def test_live_values_replace_what_was_there():
    db = FakeConnMgr()
    values = {"p1": (10.05, 0.5, NOW), "p2": (8.4, -16.0, NOW)}

    asyncio.run(AsyncDbLiveValueManager(db).replace_live_values(values))

    delete_query, delete_args = db.calls[0]
    assert delete_query.startswith("DELETE FROM position_live_value")
    assert delete_args == (["p1", "p2"],)
    insert_query, insert_args = db.calls[1]
    assert "ON CONFLICT (position_id) DO UPDATE" in insert_query
    assert insert_args == (["p1", "p2"], [10.05, 8.4], [0.5, -16.0], [NOW, NOW])


def test_no_open_position_empties_the_table_without_an_insert():
    db = FakeConnMgr()

    asyncio.run(AsyncDbLiveValueManager(db).replace_live_values({}))

    assert len(db.calls) == 1
    assert db.calls[0][1] == ([],)


# ── signal journal ──


def test_journal_entry_is_stored_with_parsed_timestamps_and_kept_if_already_there():
    db = FakeConnMgr()
    entry = {
        "signal_uuid": "uuid-1", "action": "long", "indice": "us100", "confidence": 0.8,
        "signal_timestamp": "2026-10-12T08:00:00Z", "received_timestamp": "2026-10-12T08:00:00.500Z",
        "decided_at": NOW, "outcome": "refused", "reason": "Cooldown active",
        "position_id": None, "order_id": None,
    }

    asyncio.run(AsyncDbSignalJournalManager(db).record_decision(entry))

    query, args = db.calls[0]
    assert "ON CONFLICT (signal_uuid) DO NOTHING" in query
    assert args[0] == "uuid-1"
    assert args[4] == datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc)
    assert args[5].microsecond == 500000
    assert args[6:9] == (NOW, "refused", "Cooldown active")
    # The caller's dict keeps its strings.
    assert entry["signal_timestamp"] == "2026-10-12T08:00:00Z"


def test_journal_entry_with_only_the_required_fields_is_accepted():
    db = FakeConnMgr()

    asyncio.run(AsyncDbSignalJournalManager(db).record_decision({"signal_uuid": "uuid-2", "outcome": "ignored"}))

    assert db.calls[0][1][0] == "uuid-2"
    assert db.calls[0][1][7] == "ignored"


# ── account balance history ──


def test_balance_of_a_day_is_replaced_by_the_latest_one():
    db = FakeConnMgr()

    asyncio.run(AsyncDbBalanceHistoryManager(db).record_balance(date(2026, 10, 12), 12345.67))

    query, args = db.calls[0]
    assert "ON CONFLICT (date_day) DO UPDATE" in query
    assert args[:2] == (date(2026, 10, 12), 12345.67)
    assert args[2].tzinfo is not None


def test_safe_balance_record_skips_a_missing_balance_and_swallows_failures():
    db = FakeConnMgr()
    asyncio.run(AsyncDbBalanceHistoryManager(db).record_balance_safely(date(2026, 10, 12), None))
    assert db.calls == []

    failing = FakeConnMgr(error=ConnectionError("database is down"))
    asyncio.run(AsyncDbBalanceHistoryManager(failing).record_balance_safely(date(2026, 10, 12), 100.0))
