import asyncio
import json
import sys
import types
from datetime import date, datetime, timezone

asyncpg_stub = types.ModuleType("asyncpg")
asyncpg_stub.Pool = object
asyncpg_stub.Record = dict
asyncpg_stub.create_pool = None
sys.modules.setdefault("asyncpg", asyncpg_stub)

from src.database.postgres import (
    CAPITAL_SCHEMA_SQL,
    PRICE_HISTORY_SCHEMA_SQL,
    SCHEMA_SQL,
    AsyncDbCapitalManager,
    AsyncDbPriceHistoryManager,
    init_capital_schema,
)


class FakeDb:
    def __init__(self, rows=None, value=None):
        self.executed = []
        self.fetched = []
        self.rows = rows or []
        self.value = value

    async def execute(self, query, *args):
        self.executed.append((" ".join(query.split()), args))
        return "OK"

    async def fetch(self, query, *args):
        self.fetched.append((" ".join(query.split()), args))
        return self.rows

    async def fetchval(self, query, *args):
        self.fetched.append((" ".join(query.split()), args))
        return self.value


NOW = datetime(2026, 10, 15, 8, 0, tzinfo=timezone.utc)


def test_the_tables_of_the_feed_are_its_own():
    for table in ("capital_instrument", "capital_bar_10s", "capital_coverage", "capital_minute", "capital_feed_state"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in CAPITAL_SCHEMA_SQL
        assert table not in SCHEMA_SQL and table not in PRICE_HISTORY_SCHEMA_SQL
    assert "PARTITION BY RANGE (ts)" in CAPITAL_SCHEMA_SQL
    assert "DROP " not in CAPITAL_SCHEMA_SQL


def test_the_bars_have_the_columns_of_the_price_recorders():
    def columns(schema, table):
        body = schema.split(f"CREATE TABLE IF NOT EXISTS {table} (")[1].split(") PARTITION BY")[0]
        return [line.split()[0] for line in body.strip().splitlines() if line.strip() and not line.strip().startswith("PRIMARY")]

    assert columns(CAPITAL_SCHEMA_SQL, "capital_bar_10s") == columns(PRICE_HISTORY_SCHEMA_SQL, "price_bar_10s")
    assert AsyncDbCapitalManager.BAR_COLUMNS == AsyncDbPriceHistoryManager.BAR_COLUMNS


def test_the_schema_is_created_in_one_call():
    db = FakeDb()

    asyncio.run(init_capital_schema(db))

    assert len(db.executed) == 1 and "capital_bar_10s" in db.executed[0][0]


def test_partitions_are_made_and_dropped_for_the_feeds_bars_alone():
    db = FakeDb(rows=[{"name": "capital_bar_10s_y2026m01"}, {"name": "capital_bar_10s_y2026m09"}, {"name": "price_bar_10s_y2020m01"}])
    manager = AsyncDbCapitalManager(db)

    names = asyncio.run(manager.ensure_partitions(date(2026, 12, 15)))
    dropped = asyncio.run(manager.drop_partitions_older_than(date(2026, 10, 15), 6))

    assert names == ["capital_bar_10s_y2026m12", "capital_bar_10s_y2027m01"]
    assert db.executed[0][0] == (
        "CREATE TABLE IF NOT EXISTS capital_bar_10s_y2026m12 PARTITION OF capital_bar_10s "
        "FOR VALUES FROM ('2026-12-01 00:00:00+00') TO ('2027-01-01 00:00:00+00')"
    )
    assert dropped == ["capital_bar_10s_y2026m01"]
    statements = [query for query, _ in db.executed[2:]]
    assert statements == [
        "DROP TABLE IF EXISTS capital_bar_10s_y2026m01",
        "DELETE FROM capital_coverage WHERE ts < $1",
        "DELETE FROM capital_minute WHERE ts < $1",
    ]
    assert db.executed[-1][1] == (datetime(2026, 4, 1, tzinfo=timezone.utc),)


def test_an_instrument_keeps_what_capital_com_did_not_say_again():
    db = FakeDb(value=7)
    hours = {"mon": ["00:00 - 21:59"], "zone": "UTC"}

    instrument_id = asyncio.run(AsyncDbCapitalManager(db).upsert_instrument(
        {"epic": "US100", "label": "us100", "name": "US Tech 100", "decimals": 1, "opening_hours": hours}, NOW,
    ))

    query, args = db.fetched[0]
    assert instrument_id == 7
    assert "ON CONFLICT (epic) DO UPDATE" in query and "COALESCE(EXCLUDED.opening_hours, capital_instrument.opening_hours)" in query
    assert args[:6] == ("US100", "us100", "US Tech 100", None, None, 1)
    assert json.loads(args[6]) == hours and args[7] == NOW


def test_bars_go_in_one_statement_into_the_feeds_table():
    db = FakeDb()
    row = (NOW, 1, 25000.0, 25004.0, 24998.0, 24998.0, 25001.0, 25005.0, 24999.0, 24999.0, 3, 0)

    asyncio.run(AsyncDbCapitalManager(db).insert_bars([row, row]))

    query, args = db.executed[0]
    assert query.startswith("INSERT INTO capital_bar_10s (ts, instrument_id, bid_open")
    assert "ON CONFLICT (instrument_id, ts) DO NOTHING" in query
    assert len(args) == 12 and args[0] == [NOW, NOW]


def test_a_minute_fetched_again_replaces_the_one_kept():
    db = FakeDb()

    asyncio.run(AsyncDbCapitalManager(db).upsert_minutes([(1, NOW, 1.0, 2.0, 0.5, 1.5, 1.1, 2.1, 0.6, 1.6, 187.0)]))

    query, args = db.executed[0]
    assert "INSERT INTO capital_minute" in query and "ON CONFLICT (instrument_id, ts) DO UPDATE SET" in query
    assert "volume = EXCLUDED.volume" in query
    assert args[-1] == [187.0]


def test_the_state_is_one_row_written_again_and_again():
    db = FakeDb()

    asyncio.run(AsyncDbCapitalManager(db).write_state({
        "updated_at": NOW, "started_at": NOW, "version": "0.20.0", "environment": "demo",
        "state": "streaming", "detail": "Streaming US100", "figures": {"quotes_per_minute": 120},
    }))

    query, args = db.executed[0]
    assert "VALUES (1," in query and "ON CONFLICT (id) DO UPDATE" in query
    assert args[:6] == (NOW, NOW, "0.20.0", "demo", "streaming", "Streaming US100")
    assert json.loads(args[6]) == {"quotes_per_minute": 120}


def test_the_bars_of_a_day_are_read_through_the_key_in_time_order():
    row = {"ts": NOW, "bid_open": 1.0, "bid_high": 2.0, "bid_low": 0.5, "bid_close": 1.5,
           "ask_open": None, "ask_high": None, "ask_low": None, "ask_close": None, "updates": 4}
    db = FakeDb(rows=[row])

    rows = asyncio.run(AsyncDbCapitalManager(db).bars_between(1, NOW, NOW))

    query, _ = db.fetched[0]
    assert "WHERE instrument_id = $1 AND ts >= $2 AND ts < $3 ORDER BY ts" in query
    assert rows == [(NOW, 1.0, 2.0, 0.5, 1.5, None, None, None, None, 4)]
