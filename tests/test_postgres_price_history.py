import asyncio
import sys
import types
from datetime import date, datetime, timezone

import pytest

asyncpg_stub = types.ModuleType("asyncpg")
asyncpg_stub.Pool = object
asyncpg_stub.Record = dict
asyncpg_stub.create_pool = None
sys.modules.setdefault("asyncpg", asyncpg_stub)

from src.database.postgres import (
    PRICE_HISTORY_SCHEMA_SQL,
    SCHEMA_SQL,
    AsyncDbPriceHistoryManager,
    init_price_history_schema,
    price_bar_partition,
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


NOW = datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc)


def test_the_price_tables_are_not_part_of_what_every_service_creates():
    for table in ("price_instrument", "price_bar_10s", "price_recorder_coverage", "price_volume"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in PRICE_HISTORY_SCHEMA_SQL
        assert table not in SCHEMA_SQL
    assert "PARTITION BY RANGE (ts)" in PRICE_HISTORY_SCHEMA_SQL
    assert "UNIQUE (uic, asset_type)" in PRICE_HISTORY_SCHEMA_SQL
    assert "DROP " not in PRICE_HISTORY_SCHEMA_SQL


def test_the_bars_of_a_period_are_found_by_a_brin_index_on_their_time():
    statement = " ".join(PRICE_HISTORY_SCHEMA_SQL.split())

    assert (
        "CREATE INDEX IF NOT EXISTS idx_price_bar_10s_ts ON price_bar_10s USING brin (ts) WITH (autosummarize = on)"
        in statement
    )


def test_the_schema_is_created_in_one_call():
    db = FakeDb()

    asyncio.run(init_price_history_schema(db))

    assert len(db.executed) == 1 and "price_bar_10s" in db.executed[0][0]


@pytest.mark.parametrize("day, expected", [
    (date(2026, 10, 12), ("price_bar_10s_y2026m10", date(2026, 10, 1), date(2026, 11, 1))),
    (date(2026, 12, 31), ("price_bar_10s_y2026m12", date(2026, 12, 1), date(2027, 1, 1))),
    (date(2027, 1, 1), ("price_bar_10s_y2027m01", date(2027, 1, 1), date(2027, 2, 1))),
    (date(2028, 2, 29), ("price_bar_10s_y2028m02", date(2028, 2, 1), date(2028, 3, 1))),
])
def test_a_partition_holds_one_calendar_month(day, expected):
    assert price_bar_partition(day) == expected


def test_this_month_and_the_next_get_their_partition_with_bounds_in_utc():
    db = FakeDb()

    names = asyncio.run(AsyncDbPriceHistoryManager(db).ensure_partitions(date(2026, 12, 15)))

    assert names == ["price_bar_10s_y2026m12", "price_bar_10s_y2027m01"]
    assert [statement for statement, _ in db.executed] == [
        "CREATE TABLE IF NOT EXISTS price_bar_10s_y2026m12 PARTITION OF price_bar_10s "
        "FOR VALUES FROM ('2026-12-01 00:00:00+00') TO ('2027-01-01 00:00:00+00')",
        "CREATE TABLE IF NOT EXISTS price_bar_10s_y2027m01 PARTITION OF price_bar_10s "
        "FOR VALUES FROM ('2027-01-01 00:00:00+00') TO ('2027-02-01 00:00:00+00')",
    ]


def test_nothing_is_ever_dropped_without_a_retention():
    db = FakeDb(rows=[{"name": "price_bar_10s_y2020m01"}])

    assert asyncio.run(AsyncDbPriceHistoryManager(db).drop_partitions_older_than(date(2026, 10, 12), 0)) == []
    assert db.executed == [] and db.fetched == []


def test_a_retention_removes_only_whole_months_that_are_old_enough():
    db = FakeDb(rows=[
        {"name": "price_bar_10s_y2026m03"},
        {"name": "price_bar_10s_y2026m04"},
        {"name": "price_bar_10s_y2026m10"},
        # Not one of ours, whatever it is: left alone.
        {"name": "price_bar_10s_archive"},
    ])

    dropped = asyncio.run(AsyncDbPriceHistoryManager(db).drop_partitions_older_than(date(2026, 10, 12), 6))

    # Six months before October: April is kept, March goes.
    assert dropped == ["price_bar_10s_y2026m03"]
    assert db.executed[0] == ("DROP TABLE IF EXISTS price_bar_10s_y2026m03", ())
    assert db.executed[1] == (
        "DELETE FROM price_recorder_coverage WHERE ts < $1", (datetime(2026, 4, 1, tzinfo=timezone.utc),),
    )
    assert db.executed[2] == ("DELETE FROM price_volume WHERE ts < $1", (datetime(2026, 4, 1, tzinfo=timezone.utc),))
    assert len(db.executed) == 3


def test_an_instrument_is_recorded_once_and_its_description_kept_when_a_later_sighting_has_less():
    db = FakeDb(value=42)
    instrument = {
        "uic": 101, "asset_type": "WarrantOpenEndKnockOut", "symbol": "SG-NDX-L1", "description": "TURBO LONG …",
        "currency": "EUR", "role": "turbo", "indice": "us100", "underlying_uic": 1909050, "direction": "long",
        "barrier": 24150.0, "issuer": "SG",
    }

    instrument_id = asyncio.run(AsyncDbPriceHistoryManager(db).upsert_instrument(instrument, NOW))

    assert instrument_id == 42
    statement, args = db.fetched[0]
    assert "ON CONFLICT (uic, asset_type) DO UPDATE" in statement and "RETURNING id" in statement
    assert "description = COALESCE(EXCLUDED.description, price_instrument.description)" in statement
    assert "first_seen_at" not in statement.split("DO UPDATE SET")[1]
    assert args == (101, "WarrantOpenEndKnockOut", "SG-NDX-L1", "TURBO LONG …", "EUR", "turbo", "us100",
                    1909050, "long", 24150.0, "SG", NOW)


def test_bars_are_written_in_one_statement_and_never_overwritten():
    db = FakeDb()
    bars = [
        (NOW, 1, 8.40, 8.45, 8.38, 8.42, 8.43, 8.48, 8.41, 8.45, 7, 0),
        (NOW, 2, 9.10, 9.10, 9.10, 9.10, None, None, None, None, 0, 15),
    ]

    asyncio.run(AsyncDbPriceHistoryManager(db).insert_bars(bars))

    assert len(db.executed) == 1
    statement, args = db.executed[0]
    assert statement.startswith("INSERT INTO price_bar_10s (ts, instrument_id, bid_open, bid_high, bid_low, bid_close,")
    assert "ON CONFLICT (instrument_id, ts) DO NOTHING" in statement
    assert args[0] == [NOW, NOW] and args[1] == [1, 2]
    assert args[2] == [8.40, 9.10] and args[6] == [8.43, None]
    assert args[10] == [7, 0] and args[11] == [0, 15]
    assert len(args) == len(AsyncDbPriceHistoryManager.BAR_COLUMNS) == 12


def test_no_statement_for_no_bar():
    db = FakeDb()

    asyncio.run(AsyncDbPriceHistoryManager(db).insert_bars([]))

    assert db.executed == []


def test_the_volume_is_kept_beside_the_bars_not_in_them():
    bars = PRICE_HISTORY_SCHEMA_SQL.split("CREATE TABLE IF NOT EXISTS price_bar_10s")[1].split(";")[0]

    # A column there would change the bars every backtest is made from.
    assert "volume" not in bars
    assert len(AsyncDbPriceHistoryManager.BAR_COLUMNS) == 12


def test_the_totals_that_changed_are_written_in_one_statement_the_latest_of_ten_seconds_winning():
    db = FakeDb()

    asyncio.run(AsyncDbPriceHistoryManager(db).record_volumes([(1, NOW, 1500.0), (2, NOW, 40.0)]))

    assert len(db.executed) == 1
    statement, args = db.executed[0]
    assert statement.startswith("INSERT INTO price_volume (instrument_id, ts, day_volume) SELECT * FROM unnest(")
    assert statement.endswith("ON CONFLICT (instrument_id, ts) DO UPDATE SET day_volume = EXCLUDED.day_volume")
    assert args == ([1, 2], [NOW, NOW], [1500.0, 40.0])


def test_no_statement_for_no_volume():
    db = FakeDb()

    asyncio.run(AsyncDbPriceHistoryManager(db).record_volumes([]))

    assert db.executed == []


def test_coverage_is_one_row_per_bucket():
    db = FakeDb()

    asyncio.run(AsyncDbPriceHistoryManager(db).record_coverage(NOW, True, 41, 41))

    statement, args = db.executed[0]
    assert "INSERT INTO price_recorder_coverage" in statement and "ON CONFLICT (ts) DO NOTHING" in statement
    assert args == (NOW, True, 41, 41)


def test_the_instruments_of_today_are_those_with_a_bar_since_midnight():
    db = FakeDb(rows=[{"id": 1, "uic": 101, "asset_type": "WarrantOpenEndKnockOut", "role": "turbo"}])

    rows = asyncio.run(AsyncDbPriceHistoryManager(db).instruments_recorded_since(NOW))

    assert rows == [{"id": 1, "uic": 101, "asset_type": "WarrantOpenEndKnockOut", "role": "turbo"}]
    statement, args = db.fetched[0]
    assert "b.instrument_id = i.id AND b.ts >= $1" in statement and args == (NOW,)


def test_a_period_is_read_page_by_page_in_time_order():
    db = FakeDb(rows=[{"ts": NOW, "instrument_id": 1}])
    history = AsyncDbPriceHistoryManager(db)
    end = datetime(2026, 10, 13, tzinfo=timezone.utc)

    asyncio.run(history.bars_page(NOW, end, indice="us100", limit=500))
    asyncio.run(history.bars_page(NOW, end, after=(NOW, 7)))

    first, following = db.fetched
    assert "ORDER BY b.ts, b.instrument_id LIMIT $6" in first[0]
    assert "(b.ts, b.instrument_id) > ($4::timestamptz, $5::int4)" in first[0]
    assert first[1] == (NOW, end, "us100", NOW, -1, 500)
    assert following[1] == (NOW, end, None, NOW, 7, 20000)
