"""The TA engine's tables (TA_SCHEMA_SQL) and what AsyncDbTaManager writes there, as SQL text."""

import asyncio
import sys
import types
from datetime import datetime, timedelta, timezone

asyncpg_stub = types.ModuleType("asyncpg")
asyncpg_stub.Pool = object
asyncpg_stub.Record = dict
asyncpg_stub.create_pool = None
sys.modules.setdefault("asyncpg", asyncpg_stub)

from src.database.postgres import TA_RUN_COLUMNS, TA_SCHEMA_SQL, TA_TICK_COLUMNS, AsyncDbTaManager  # noqa: E402

AT = datetime(2026, 10, 22, 8, 0, tzinfo=timezone.utc)


class FakeConnMgr:
    def __init__(self, oldest=None, deleted=4):
        self.calls = []
        self.oldest = oldest
        self.deleted = deleted

    async def execute(self, query, *args):
        self.calls.append((" ".join(query.split()), args))
        return f"DELETE {self.deleted}" if query.startswith("DELETE") else "INSERT 0 1"

    async def fetchval(self, query, *args):
        self.calls.append((" ".join(query.split()), args))
        return self.oldest


def test_the_engines_measures_have_tables_of_their_own_made_without_touching_others():
    for table in ("ta_signal", "ta_engine_state", "ta_rule_state", "ta_engine_tick", "ta_rule_run"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in TA_SCHEMA_SQL
    assert "PRIMARY KEY (rule_id, tick_at)" in TA_SCHEMA_SQL
    assert "ALTER " not in TA_SCHEMA_SQL and "DROP " not in TA_SCHEMA_SQL


def test_each_tick_is_a_statement_and_the_runs_of_all_of_them_are_one():
    db = FakeConnMgr()
    ticks = [
        {"tick_at": AT, "milliseconds": 182, "deferred_rules": ["0123456789ab"], "figures": {"first": True}},
        {"tick_at": AT + timedelta(seconds=10)},
    ]
    runs = [{"rule_id": "0123456789ab", "tick_at": AT, "kind": "append"}, {"rule_id": "ba9876543210", "tick_at": AT, "kind": "load"}]

    asyncio.run(AsyncDbTaManager(db).write_monitoring(ticks, runs))

    first, second, all_runs = db.calls
    assert first[0].startswith("INSERT INTO ta_engine_tick (tick_at, ended_at, milliseconds,")
    assert ", oom_kills, figures) VALUES (" in first[0]
    assert first[0].endswith(f"VALUES ({', '.join(f'${index}' for index in range(1, len(TA_TICK_COLUMNS) + 1))}, "
                             f"${len(TA_TICK_COLUMNS) + 1}::jsonb) ON CONFLICT (tick_at) DO NOTHING")
    assert first[1][0] == AT and first[1][2] == 182 and first[1][TA_TICK_COLUMNS.index("deferred_rules")] == ["0123456789ab"]
    assert first[1][-1] == '{"first": true}'
    assert second[1][0] == AT + timedelta(seconds=10) and second[1][-1] == "{}"
    assert "SELECT * FROM unnest($1::text[], $2::timestamptz[], $3::text[]" in all_runs[0]
    assert all_runs[0].endswith("ON CONFLICT (rule_id, tick_at) DO NOTHING")
    columns = [name for name, _ in TA_RUN_COLUMNS]
    assert all_runs[1][columns.index("rule_id")] == ["0123456789ab", "ba9876543210"]
    assert all_runs[1][columns.index("kind")] == ["append", "load"] and all_runs[1][columns.index("detail")] == [None, None]


def test_without_a_run_only_the_ticks_are_written():
    db = FakeConnMgr()

    asyncio.run(AsyncDbTaManager(db).write_monitoring([{"tick_at": AT}], []))

    assert [query.split(" (")[0] for query, _ in db.calls] == ["INSERT INTO ta_engine_tick"]


def test_older_measures_are_removed_a_day_at_a_time_and_counted():
    before = AT
    db = FakeConnMgr(oldest=before - timedelta(days=2, hours=12), deleted=4)

    removed = asyncio.run(AsyncDbTaManager(db).purge_monitoring(before))

    oldest, *deletes = db.calls
    assert oldest[0] == "SELECT least((SELECT min(tick_at) FROM ta_engine_tick), (SELECT min(tick_at) FROM ta_rule_run))"
    assert [(query, args[0]) for query, args in deletes] == [
        ("DELETE FROM ta_rule_run WHERE tick_at < $1", before - timedelta(days=1, hours=12)),
        ("DELETE FROM ta_engine_tick WHERE tick_at < $1", before - timedelta(days=1, hours=12)),
        ("DELETE FROM ta_rule_run WHERE tick_at < $1", before - timedelta(hours=12)),
        ("DELETE FROM ta_engine_tick WHERE tick_at < $1", before - timedelta(hours=12)),
        ("DELETE FROM ta_rule_run WHERE tick_at < $1", before),
        ("DELETE FROM ta_engine_tick WHERE tick_at < $1", before),
    ]
    assert removed == 24


def test_nothing_is_removed_when_nothing_is_old_enough():
    for oldest in (None, AT + timedelta(minutes=1)):
        db = FakeConnMgr(oldest=oldest)

        assert asyncio.run(AsyncDbTaManager(db).purge_monitoring(AT)) == 0
        assert len(db.calls) == 1
