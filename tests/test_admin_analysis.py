import asyncio
import json
import os
import sys
import types
from datetime import date, datetime, timezone

import pytest

asyncpg_stub = types.ModuleType("asyncpg")
asyncpg_stub.Pool = object
asyncpg_stub.Record = dict
asyncpg_stub.create_pool = None
sys.modules.setdefault("asyncpg", asyncpg_stub)

from src.admin.analysis import exit_group, holding_bucket, summarise_exits
from src.admin.audit import AuditLog
from src.admin.errors import DatabaseUnavailable
from src.admin.queries import AdminQueries, json_safe
from src.admin.timing import read_execution_timings


def _trade(reason, percent, best, profit, hour=9, holding=600):
    return {
        "action": "long",
        "close_reason": reason,
        "performance_percent": percent,
        "max_performance_percent": best,
        "profit_loss": profit,
        "open_hour": hour,
        "holding_seconds": holding,
    }


# ── exits ──


@pytest.mark.parametrize("reason,group", [
    ("Performance (Stoploss (-15%) hit at -16.2%)", "Stop-loss"),
    ("Performance (Takeprofit (60%) hit at 61%)", "Take-profit"),
    ("Performance (Trailing Stop: peak 9.1%, now 0.8% (dd 8.3% >= 8%))", "Trailing stop"),
    ("Performance (Daily profit target (1.7%) potentially met (1.9%))", "Daily profit target"),
    ("Explicit Close (All)", "Signal or end of day"),
    ("Explicit Close (long)", "Signal or end of day"),
    ("Explicit Close (position 5001)", "Operator (one position)"),
    ("SaxoAPI", "Closed at Saxo"),
    ("something new", "Other"),
    (None, "Not recorded"),
])
def test_close_reasons_are_grouped(reason, group):
    assert exit_group(reason) == group


@pytest.mark.parametrize("seconds,bucket", [
    (0, "Under 5 min"), (299, "Under 5 min"), (300, "5 to 15 min"), (3599, "15 min to 1 h"),
    (3600, "1 to 4 h"), (5 * 3600, "Over 4 h"), (None, None), (-5, None),
])
def test_holding_times_are_bucketed(seconds, bucket):
    assert holding_bucket(seconds) == bucket


def test_exit_summary_measures_what_was_given_back():
    trades = [
        _trade("Performance (Trailing Stop: ...)", 2.0, 10.0, 40.0, hour=9, holding=1200),
        _trade("Performance (Stoploss (-15%) hit at -16%)", -16.0, 3.0, -160.0, hour=15, holding=120),
        _trade("Explicit Close (All)", 1.0, 1.0, 10.0, hour=9, holding=7200),
    ]

    result = summarise_exits(trades)

    summary = result["summary"]
    assert summary["trade_count"] == 3
    assert summary["profit_loss"] == -110.0
    # (10 - 2) + (3 - -16) + (1 - 1), over three trades.
    assert summary["average_given_back"] == 9.0
    # One trade was in profit at some point and closed at a loss.
    assert summary["turned_negative_count"] == 1
    assert summary["turned_negative_percent"] == 33.33

    assert [entry["reason"] for entry in result["by_reason"]] == ["Trailing stop", "Stop-loss", "Signal or end of day"]
    assert [(entry["hour"], entry["trade_count"]) for entry in result["by_hour"]] == [(9, 2), (15, 1)]
    by_hour_nine = result["by_hour"][0]
    assert (by_hour_nine["win_rate"], by_hour_nine["profit_loss"], by_hour_nine["average_percent"]) == (100.0, 50.0, 1.5)

    buckets = {entry["bucket"]: entry["trade_count"] for entry in result["by_holding_time"]}
    assert buckets == {"Under 5 min": 1, "5 to 15 min": 0, "15 min to 1 h": 1, "1 to 4 h": 1, "Over 4 h": 0}


def test_exit_summary_of_nothing_and_of_incomplete_trades():
    empty = summarise_exits([])
    assert empty["summary"]["trade_count"] == 0
    assert empty["summary"]["average_given_back"] is None
    assert empty["by_reason"] == [] and empty["by_hour"] == []

    incomplete = summarise_exits([_trade(None, None, None, None, hour=None, holding=None)])
    assert incomplete["summary"]["average_percent"] == 0
    assert incomplete["summary"]["average_best_percent"] is None
    assert incomplete["by_reason"][0]["reason"] == "Not recorded"
    assert incomplete["by_hour"] == []


# ── queries over the tables the trading services write ──


class FakeDb:
    def __init__(self, rows=None, missing_tables=()):
        self.rows = rows or {}
        self.missing_tables = missing_tables
        self.calls = []

    def _answer(self, query, args):
        self.calls.append((query, args))
        for table in self.missing_tables:
            if table in query:
                raise DatabaseUnavailable(f'relation "{table}" does not exist')
        for needle, rows in self.rows.items():
            if needle in query:
                return rows
        return []

    async def fetch(self, query, *args):
        return self._answer(query, args)

    async def fetchrow(self, query, *args):
        rows = self._answer(query, args)
        return rows[0] if rows else None

    async def fetchval(self, query, *args):
        rows = self._answer(query, args)
        return rows[0] if rows else None


OPEN = {"position_id": "p1", "position_status": "Open", "instrument_name": "TURBO"}
OBSERVED = datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc)


def test_open_positions_carry_the_value_the_monitor_last_saw():
    db = FakeDb(rows={
        "FROM turbo_data_position": [OPEN, {**OPEN, "position_id": "p2"}],
        "FROM position_live_value": [
            {"position_id": "p1", "bid": 10.05, "performance_percent": 0.5, "observed_at": OBSERVED},
        ],
    })

    positions = asyncio.run(AdminQueries(db, "Europe/Paris").open_positions())

    assert (positions[0]["live_bid"], positions[0]["live_performance_percent"]) == (10.05, 0.5)
    assert positions[0]["live_observed_at"] == OBSERVED
    # Not seen by the monitor yet: no value rather than a made-up one.
    assert positions[1]["live_bid"] is None


@pytest.mark.parametrize("call", ["open_positions", "signal_journal", "balance_history"])
def test_tables_not_created_yet_read_as_empty(call):
    db = FakeDb(
        rows={"FROM turbo_data_position": [OPEN]},
        missing_tables=("position_live_value", "signal_journal", "account_balance_history", "order_request"),
    )

    result = asyncio.run(getattr(AdminQueries(db, "Europe/Paris"), call)())

    assert result == ([{**OPEN, "live_bid": None, "live_performance_percent": None, "live_observed_at": None,
                        "sell_refusals": None}]
                      if call == "open_positions" else [])


def test_a_real_database_failure_is_not_mistaken_for_a_missing_table():
    class Down(FakeDb):
        async def fetch(self, query, *args):
            raise DatabaseUnavailable("connection refused")

    with pytest.raises(DatabaseUnavailable):
        asyncio.run(AdminQueries(Down(), "Europe/Paris").signal_journal())


def test_journal_is_read_newest_first_with_its_filters_bound():
    db = FakeDb()

    asyncio.run(AdminQueries(db, "Europe/Paris").signal_journal(start=date(2026, 10, 1), limit=50))

    query, args = db.calls[0]
    assert args == ("Europe/Paris", date(2026, 10, 1))
    assert "ORDER BY decided_at DESC" in query and "LIMIT 50" in query


def test_report_uses_the_telegram_report_figures_and_the_latest_recorded_balance():
    trades = [
        {"action": "long", "position_id": "1", "performance_percent": 10.0, "max_performance_percent": 12.0,
         "profit_loss": 100.0, "execution_time_close": datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc)},
    ]
    db = FakeDb(rows={
        "position_max_performance_percent AS max_performance_percent": trades,
        "GROUP BY execution_time_close::date": [{"day_date": date(2026, 10, 12), "sum_profit": 100.0, "trade_count": 1}],
        "FROM account_balance_history": [
            {"date_day": date(2026, 10, 9), "total_value": 9000.0, "recorded_at": OBSERVED},
            {"date_day": date(2026, 10, 10), "total_value": 9500.0, "recorded_at": OBSERVED},
        ],
    })

    report = asyncio.run(AdminQueries(db, "Europe/Paris").report(milestones_eur=[10000], today=date(2026, 10, 12)))

    assert report["current_balance"] == 9500.0
    assert report["balance_recorded_on"] == date(2026, 10, 10)
    assert len(report["balance_history"]) == 2
    assert report["daily_summary"]["trade_count"] == 1
    assert report["streak"]["current_streak"] == 1
    # A day with no losing trade has an infinite profit factor, which JSON cannot carry.
    assert report["daily_summary"]["profit_factor"] is None
    json.dumps(report, default=str)


def test_non_finite_numbers_are_removed_at_any_depth():
    value = {"a": float("inf"), "b": [1.5, float("nan"), {"c": float("-inf")}], "d": (2.0,), "e": "text"}

    assert json_safe(value) == {"a": None, "b": [1.5, None, {"c": None}], "d": [2.0], "e": "text"}


def test_exits_query_buckets_by_local_day_and_hour():
    db = FakeDb()

    asyncio.run(AdminQueries(db, "Europe/Paris").exits(start=date(2026, 9, 1), end=date(2026, 9, 30)))

    query, args = db.calls[0]
    assert args == ("Europe/Paris", date(2026, 9, 1), date(2026, 9, 30))
    assert "execution_time_open AT TIME ZONE $1" in query
    assert "position_status = 'Closed'" in query


# ── execution timings ──


def _timing_line(action, total, timestamp):
    return json.dumps({
        "action": action, "confidence": None, "position_scale": 1.0, "timestamp": timestamp,
        "steps": {"turbo_found": total // 4, "order_placed": total // 2, "position_confirmed": total},
    }) + "\n"


def test_timings_are_read_newest_first_and_summarised(tmp_path):
    (tmp_path / "execution_timing_2026-10-11.jsonl").write_text(
        _timing_line("long", 400, "2026-10-11 09:00:00") + _timing_line("short", 800, "2026-10-11 15:00:00")
    )
    (tmp_path / "execution_timing_2026-10-12.jsonl").write_text(
        _timing_line("long", 600, "2026-10-12 10:00:00") + "not json\n" + json.dumps({"steps": "bad"}) + "\n"
    )
    (tmp_path / "execution_timing_2026-08-01.jsonl").write_text(_timing_line("long", 9999, "2026-08-01 10:00:00"))
    (tmp_path / "execution_timing_evil.jsonl").write_text("{}")

    result = read_execution_timings(str(tmp_path), days=7, today=date(2026, 10, 12))

    assert [entry["timestamp"] for entry in result["entries"]] == [
        "2026-10-12 10:00:00", "2026-10-11 15:00:00", "2026-10-11 09:00:00",
    ]
    assert result["entries"][0]["steps"] == {"turbo_found": 150, "order_placed": 300, "position_confirmed": 600}
    assert result["summary"] == {
        "trade_count": 3, "median_total_ms": 600, "p90_total_ms": 800, "slowest_total_ms": 800,
    }


def test_no_timing_file_is_an_empty_result(tmp_path):
    result = read_execution_timings(str(tmp_path / "missing"), days=7)

    assert result["entries"] == []
    assert result["summary"]["median_total_ms"] is None


# ── audit log ──


def test_audit_entries_are_appended_and_read_newest_first(tmp_path):
    audit_log = AuditLog(str(tmp_path / "wata-admin" / "audit.jsonl"))
    assert audit_log.read() == []

    audit_log.append("203.0.113.9", "login")
    audit_log.append("203.0.113.9", "manual signal long", "indice=us100")

    entries = audit_log.read()
    assert [entry["action"] for entry in entries] == ["manual signal long", "login"]
    assert entries[0]["detail"] == "indice=us100"
    assert len(audit_log.read(limit=1)) == 1


def test_audit_survives_a_damaged_line_and_an_unwritable_path(tmp_path):
    path = tmp_path / "audit.jsonl"
    path.write_text('{"action": "login", "ip": "1.2.3.4", "at": "x", "detail": ""}\nnot json\n[1, 2]\n')

    assert [entry["action"] for entry in AuditLog(str(path)).read()] == ["login"]

    blocked = tmp_path / "file"
    blocked.write_text("")
    AuditLog(str(blocked / "audit.jsonl")).append("1.2.3.4", "login")
