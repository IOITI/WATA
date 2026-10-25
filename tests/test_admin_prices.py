import asyncio
import sys
import types
from datetime import datetime, timedelta, timezone

import pytest
import pytz

asyncpg_stub = types.ModuleType("asyncpg")
asyncpg_stub.Pool = object
asyncpg_stub.Record = dict
asyncpg_stub.create_pool = None
sys.modules.setdefault("asyncpg", asyncpg_stub)

from src.admin.errors import DatabaseUnavailable
from src.admin.prices import (
    DOWN,
    INTERVALS,
    MAX_BUCKETS,
    RANGE_COLUMNS,
    SILENT,
    STOPPED,
    STRETCHES_KEPT,
    UNQUOTED,
    Run,
    TooManyBars,
    auto_range_ticks,
    availability,
    price_decimals,
    range_bars,
    range_points,
    range_rows,
    range_volumes,
    recording_gaps,
    sample_at,
    tick_of,
)
from src.admin.queries import PRICE_BAR_COLUMNS, AdminQueries
from src.price_recorder.settings import RecorderSettings

# Monday 12 Oct 2026. Paris is UTC+2: trading from 06:00 to 20:00 UTC.
SETTINGS = RecorderSettings(timezone="Europe/Paris", trading_start_hour=8, trading_end_hour=22)
DAY = datetime(2026, 10, 11, 22, 0, tzinfo=timezone.utc)      # Monday 00:00 in Paris
NEXT_DAY = DAY + timedelta(days=1)
LATER = datetime(2026, 10, 20, tzinfo=timezone.utc)           # "now", well after the period


def at(hour, minute=0, second=0, day=12):
    """A UTC time of the given October day."""
    return datetime(2026, 10, day, hour, minute, second, tzinfo=timezone.utc)


def recorded(start, end, feed_ok=True):
    """Coverage rows, one per ten seconds, for [start, end)."""
    rows = []
    moment = start
    while moment < end:
        rows.append({"ts": moment, "feed_ok": feed_ok})
        moment += timedelta(seconds=10)
    return rows


def gaps_of(coverage, start=DAY, end=NEXT_DAY, now=LATER):
    return [
        (gap["start"], gap["end"], gap["reason"])
        for gap in recording_gaps(coverage, start, end, SETTINGS.is_trading_time, now)
    ]


# ── gaps ──


def test_a_day_recorded_throughout_has_no_gap():
    assert gaps_of(recorded(at(5, 50), at(20, 10))) == []


def test_minutes_without_a_recorder_are_a_gap():
    coverage = recorded(at(5, 50), at(8, 0)) + recorded(at(8, 5), at(20, 10))

    assert gaps_of(coverage) == [(at(8, 0), at(8, 5), DOWN)]


def test_minutes_without_any_quote_are_a_gap_of_another_kind():
    coverage = recorded(at(5, 50), at(9, 0)) + recorded(at(9, 0), at(9, 1), feed_ok=False) + recorded(at(9, 1), at(20, 10))

    (gap,) = recording_gaps(coverage, DAY, NEXT_DAY, SETTINGS.is_trading_time, LATER)

    assert (gap["start"], gap["end"], gap["reason"], gap["seconds"]) == (at(9, 0), at(9, 1), SILENT, 60)


def test_a_recorder_that_stopped_after_going_silent_gives_two_gaps_side_by_side():
    coverage = (
        recorded(at(5, 50), at(9, 0))
        + recorded(at(9, 0), at(9, 2), feed_ok=False)
        + recorded(at(9, 10), at(20, 10))
    )

    assert gaps_of(coverage) == [(at(9, 0), at(9, 2), SILENT), (at(9, 2), at(9, 10), DOWN)]


def test_the_night_is_not_a_gap():
    # Running all night with nothing quoting, as on any night.
    coverage = (
        recorded(at(18, 0, day=11), DAY + timedelta(hours=8), feed_ok=False)
        + recorded(at(6, 0), at(20, 0))
        + recorded(at(20, 0), NEXT_DAY, feed_ok=False)
    )

    assert gaps_of(coverage) == []


def test_a_stop_that_runs_into_the_trading_day_counts_from_the_open():
    coverage = recorded(DAY, at(5, 30)) + recorded(at(6, 30), at(20, 10))

    assert gaps_of(coverage) == [(at(6, 0), at(6, 30), DOWN)]


def test_a_stop_that_runs_past_the_close_counts_until_the_close():
    coverage = recorded(at(5, 50), at(19, 45))

    assert gaps_of(coverage) == [(at(19, 45), at(20, 0), DOWN)]


def test_a_day_with_nothing_recorded_is_one_gap_the_length_of_the_trading_day():
    (gap,) = recording_gaps([], DAY, NEXT_DAY, SETTINGS.is_trading_time, LATER)

    assert (gap["start"], gap["end"], gap["reason"]) == (at(6, 0), at(20, 0), DOWN)
    assert gap["seconds"] == 14 * 3600


def test_a_weekend_with_nothing_recorded_is_not_a_gap():
    saturday = datetime(2026, 10, 9, 22, 0, tzinfo=timezone.utc)

    assert gaps_of([], start=saturday, end=saturday + timedelta(days=2)) == []


def test_several_days_give_one_gap_per_trading_day_missed():
    monday_and_tuesday = gaps_of([], end=DAY + timedelta(days=2))

    assert monday_and_tuesday == [(at(6, 0), at(20, 0), DOWN), (at(6, 0, day=13), at(20, 0, day=13), DOWN)]


def test_what_has_not_happened_yet_is_not_a_gap():
    coverage = recorded(at(5, 50), at(10, 0))

    # At 10:00:15 the last rows may simply not be written yet.
    assert gaps_of(coverage, now=at(10, 0, 15)) == []
    assert gaps_of(coverage, now=at(10, 30)) == [(at(10, 0), at(10, 29, 40), DOWN)]
    assert gaps_of([], now=at(5, 0)) == []


def test_a_single_missed_line_is_reported():
    coverage = recorded(at(5, 50), at(12, 0)) + recorded(at(12, 0, 10), at(20, 10))

    assert gaps_of(coverage) == [(at(12, 0), at(12, 0, 10), DOWN)]


def test_a_period_before_now_starting_in_trading_hours_is_measured_from_its_start():
    coverage = recorded(at(10, 5), at(11, 0))

    assert gaps_of(coverage, start=at(10, 0), end=at(11, 0)) == [(at(10, 0), at(10, 5), DOWN)]


# ── queries ──


class FakeDb:
    def __init__(self, rows=None, error=None):
        self.rows = rows or []
        self.error = error
        self.calls = []

    async def fetch(self, query, *args):
        self.calls.append((" ".join(query.split()), args))
        if self.error:
            raise self.error
        return self.rows


START = datetime(2026, 10, 11, 22, 0, tzinfo=timezone.utc)
END = datetime(2026, 10, 12, 22, 0, tzinfo=timezone.utc)
BAR_ROW = dict(zip(PRICE_BAR_COLUMNS, (1791792000, 8.40, 8.52, 8.36, 8.45, 8.45, 8.57, 8.41, 8.50, 3)))


def test_ten_second_bars_are_read_as_recorded_oldest_first():
    db = FakeDb(rows=[BAR_ROW])

    rows = asyncio.run(AdminQueries(db, "Europe/Paris").price_bars(7, START, END, 10, 500))

    assert rows == [[1791792000, 8.40, 8.52, 8.36, 8.45, 8.45, 8.57, 8.41, 8.50, 3]]
    statement, args = db.calls[0]
    assert "FROM price_bar_10s WHERE instrument_id = $1 AND ts >= $2 AND ts < $3 ORDER BY ts LIMIT $4" in statement
    assert "GROUP BY" not in statement
    assert args == (7, START, END, 500)


@pytest.mark.parametrize("interval", [*(interval for interval in INTERVALS if interval != 10), 20, 45, 90, 86_400])
def test_longer_bars_merge_the_ten_second_ones_of_each_bucket(interval):
    db = FakeDb(rows=[BAR_ROW])

    asyncio.run(AdminQueries(db, "Europe/Paris").price_bars(7, START, END, interval, MAX_BUCKETS + 1))

    statement, args = db.calls[0]
    assert f"(floor(extract(epoch FROM ts) / {interval}) * {interval})::bigint AS t" in statement
    assert "(array_agg(bid_open ORDER BY ts ASC))[1] AS bid_open" in statement
    assert "max(bid_high) AS bid_high" in statement and "min(bid_low) AS bid_low" in statement
    assert "(array_agg(bid_close ORDER BY ts DESC))[1] AS bid_close" in statement
    # A bucket whose first bar has no ask opens on the first ask there is.
    assert "(array_agg(ask_open ORDER BY ts ASC) FILTER (WHERE ask_open IS NOT NULL))[1] AS ask_open" in statement
    assert "sum(updates)::int AS updates" in statement
    assert statement.endswith("GROUP BY 1 ORDER BY 1 LIMIT $4")
    assert args == (7, START, END, MAX_BUCKETS + 1)


def test_an_interval_that_is_not_offered_never_reaches_the_statement():
    db = FakeDb()

    for interval in (5, 86_401, True, 45.5, "60; DROP TABLE price_bar_10s"):
        with pytest.raises(ValueError):
            asyncio.run(AdminQueries(db, "Europe/Paris").price_bars(7, START, END, interval, 100))

    assert db.calls == []


def test_a_bar_with_a_value_that_is_not_a_number_is_still_served():
    db = FakeDb(rows=[{**BAR_ROW, "ask_high": float("nan")}])

    (row,) = asyncio.run(AdminQueries(db, "Europe/Paris").price_bars(7, START, END, 10, 500))

    assert row[6] is None


def test_instruments_of_a_period_are_found_without_scanning_the_bars():
    db = FakeDb(rows=[{"id": 1, "uic": 1909050, "role": "underlying"}])

    rows = asyncio.run(AdminQueries(db, "Europe/Paris").price_instruments(START, END))

    assert rows == [{"id": 1, "uic": 1909050, "role": "underlying"}]
    statement, args = db.calls[0]
    assert "i.last_seen_at >= $1 AND i.first_seen_at < $2" in statement
    # One probe of the bars' primary key per instrument, from the latest: it finds the last quote on the way.
    assert (
        "CROSS JOIN LATERAL ( SELECT b.ts, b.bid_close, b.ask_close FROM price_bar_10s b "
        "WHERE b.instrument_id = i.id AND b.ts >= $1 AND b.ts < $2 ORDER BY b.ts DESC LIMIT 1 ) latest"
    ) in statement
    assert "extract(epoch FROM latest.ts)::bigint AS last_t, latest.bid_close AS last_bid, latest.ask_close AS last_ask" in statement
    assert "ORDER BY (i.role <> 'underlying'), i.indice, i.direction, i.barrier, i.id" in statement
    assert args == (START, END)


def test_the_last_quote_of_an_instrument_that_is_not_a_number_is_none():
    db = FakeDb(rows=[{"id": 2, "last_t": 1791792000, "last_bid": 8.45, "last_ask": float("nan")}])

    (row,) = asyncio.run(AdminQueries(db, "Europe/Paris").price_instruments(START, END))

    assert (row["last_bid"], row["last_ask"]) == (8.45, None)


def test_every_instrument_ever_recorded_is_listed_with_its_first_and_last_bar():
    db = FakeDb(rows=[{"id": 2, "first_t": 1791792000, "last_t": 1791842390, "last_bid": 8.45, "last_ask": float("nan")}])

    (row,) = asyncio.run(AdminQueries(db, "Europe/Paris").price_assets(5001))

    assert (row["last_bid"], row["last_ask"]) == (8.45, None)
    statement, args = db.calls[0]
    assert "i.first_seen_at, i.last_seen_at" in statement
    # Each probe stays within the days the instrument was in the recording set, give or take one:
    # one or two monthly partitions, through the primary key, for each instrument.
    assert (
        "CROSS JOIN LATERAL ( SELECT b.ts, b.bid_close, b.ask_close FROM price_bar_10s b WHERE b.instrument_id = i.id "
        "AND b.ts >= i.first_seen_at - interval '1 day' AND b.ts < i.last_seen_at + interval '1 day' "
        "ORDER BY b.ts DESC LIMIT 1 ) last_bar"
    ) in statement
    assert (
        "CROSS JOIN LATERAL ( SELECT b.ts FROM price_bar_10s b WHERE b.instrument_id = i.id "
        "AND b.ts >= i.first_seen_at - interval '1 day' AND b.ts <= last_bar.ts ORDER BY b.ts LIMIT 1 ) first_bar"
    ) in statement
    # The index first, then the instruments quoted last: what a limit leaves out is the oldest.
    assert statement.endswith(
        "ORDER BY (i.role <> 'underlying'), last_bar.ts DESC, i.indice, i.direction, i.barrier, i.id LIMIT $1"
    )
    assert "WHERE i.last_seen_at" not in statement
    assert args == (5001,)


def test_the_instruments_listed_can_be_those_recorded_since_a_moment():
    db = FakeDb()

    asyncio.run(AdminQueries(db, "Europe/Paris").price_assets(5001, since=START))

    statement, args = db.calls[0]
    assert ") first_bar WHERE i.last_seen_at >= $2 ORDER BY" in statement
    assert args == (5001, START)


def test_one_instrument_is_found_with_its_first_and_last_bar_the_same_way():
    db = FakeDb()

    asyncio.run(AdminQueries(db, "Europe/Paris").price_assets(1, instrument_id=7))

    statement, args = db.calls[0]
    assert ") first_bar WHERE i.id = $2 ORDER BY" in statement
    assert args == (1, 7)


def test_the_index_under_a_turbo_is_the_one_of_its_indice_quoted_in_the_period():
    db = FakeDb(rows=[{"id": 1, "role": "underlying", "indice": "us100"}])

    index = asyncio.run(AdminQueries(db, "Europe/Paris").price_index_of("us100", START, END))

    assert index == {"id": 1, "role": "underlying", "indice": "us100"}
    statement, args = db.calls[0]
    assert "WHERE b.instrument_id = i.id AND b.ts >= $2 AND b.ts < $3 ORDER BY b.ts DESC LIMIT 1 ) latest" in statement
    assert "WHERE i.role = 'underlying' AND i.indice = $1 AND i.last_seen_at >= $2 AND i.first_seen_at < $3" in statement
    assert statement.endswith("ORDER BY latest.ts DESC, i.id LIMIT 1")
    assert args == ("us100", START, END)


def test_an_indice_with_no_index_recorded_in_the_period_has_none():
    assert asyncio.run(AdminQueries(FakeDb(), "Europe/Paris").price_index_of("us100", START, END)) is None


def test_the_volume_of_one_instrument_is_its_last_total_of_each_day():
    db = FakeDb(rows=[
        {"day": START.date(), "volume": 15300.0},
        {"day": END.date(), "volume": float("nan")},
    ])

    volumes = asyncio.run(AdminQueries(db, "Europe/Paris").price_volume_days(2, START, END))

    assert volumes == {START.date().isoformat(): 15300.0}
    statement, args = db.calls[0]
    assert "SELECT (ts AT TIME ZONE $4)::date AS day, (array_agg(day_volume ORDER BY ts DESC))[1] AS volume" in statement
    assert "FROM price_volume WHERE instrument_id = $1 AND ts >= $2 AND ts < $3 GROUP BY 1 ORDER BY 1" in statement
    assert args == (2, START, END, "Europe/Paris")


def test_one_instrument_comes_with_when_it_was_first_and_last_recorded():
    db = FakeDb(rows=[{"id": 7, "first_seen_at": START, "last_seen_at": END}])

    instrument = asyncio.run(AdminQueries(db, "Europe/Paris").price_instrument(7))

    assert instrument == {"id": 7, "first_seen_at": START, "last_seen_at": END}
    statement, args = db.calls[0]
    assert statement.endswith("i.first_seen_at, i.last_seen_at FROM price_instrument i WHERE i.id = $1")
    assert args == (7,)


RUN_ROW = {
    "instrument_id": 2, "first": 1791792000, "last": 1791792590, "bars": 60, "updates": 412,
    "without_ask": 0, "delayed_minutes": 0, "high": 8.61, "last_bid": 8.45,
}


def test_the_bars_of_a_period_are_read_as_stretches_without_a_break():
    db = FakeDb(rows=[RUN_ROW])

    runs = asyncio.run(AdminQueries(db, "Europe/Paris").price_runs(START, END, 500))

    assert runs == [Run(2, 1791792000, 1791792590, 60, 412, 0, 0, 8.61, 8.45)]
    statement, args = db.calls[0]
    # Bars ten seconds apart share their time less ten seconds per bar before them: one pass over the bars.
    assert (
        "extract(epoch FROM ts)::bigint - 10 * row_number() OVER (PARTITION BY instrument_id ORDER BY ts) AS run"
    ) in statement
    assert "FROM price_bar_10s WHERE ts >= $1 AND ts < $2 ) bars GROUP BY instrument_id, run" in statement
    # The bid a stretch ended on is one row, found by its key.
    assert "JOIN price_bar_10s ending ON ending.instrument_id = runs.instrument_id AND ending.ts = runs.last_ts" in statement
    assert statement.endswith("ORDER BY runs.instrument_id, runs.first_ts LIMIT $3")
    assert args == (START, END, 500)


def test_a_stretch_with_a_price_that_is_not_a_number_is_still_read():
    db = FakeDb(rows=[{**RUN_ROW, "high": float("nan")}])

    (run,) = asyncio.run(AdminQueries(db, "Europe/Paris").price_runs(START, END, 500))

    assert run.high is None and run.last_bid == 8.45


def test_the_volume_of_a_period_is_the_last_total_of_each_day_added_up():
    db = FakeDb(rows=[{"instrument_id": 2, "volume": 15300.0}, {"instrument_id": 3, "volume": float("nan")}])

    volumes = asyncio.run(AdminQueries(db, "Europe/Paris").price_volumes(START, END))

    assert volumes == {2: 15300.0}
    statement, args = db.calls[0]
    # Saxo's total starts again each day: the last one received that day is the day's.
    assert "(array_agg(day_volume ORDER BY ts DESC))[1] AS volume" in statement
    assert "FROM price_volume WHERE ts >= $1 AND ts < $2 GROUP BY instrument_id, (ts AT TIME ZONE $3)::date" in statement
    assert statement.endswith("GROUP BY instrument_id")
    assert args == (START, END, "Europe/Paris")


def test_before_the_recorder_keeps_any_volume_there_is_none_to_read():
    db = FakeDb(error=DatabaseUnavailable('relation "price_volume" does not exist'))

    assert asyncio.run(AdminQueries(db, "Europe/Paris").price_volumes(START, END)) == {}


def test_trades_of_a_turbo_are_those_on_its_own_number():
    db = FakeDb()

    asyncio.run(AdminQueries(db, "Europe/Paris").price_trades({"role": "turbo", "uic": 101, "indice": "us100"}, START, END))

    statement, args = db.calls[0]
    assert "WHERE instrument_uic = $1 AND execution_time_open < $3 AND COALESCE(execution_time_close, now()) >= $2" in statement
    assert args == (101, START, END)


def test_trades_of_an_index_are_those_of_every_recorded_turbo_of_its_indice():
    db = FakeDb()

    asyncio.run(AdminQueries(db, "Europe/Paris").price_trades({"role": "underlying", "uic": 1909050, "indice": "us100"}, START, END))

    statement, args = db.calls[0]
    assert "instrument_uic IN (SELECT uic FROM price_instrument WHERE indice = $1 AND role = 'turbo')" in statement
    assert args == ("us100", START, END)


def test_the_bid_of_an_instrument_is_read_as_one_series_oldest_first():
    db = FakeDb(rows=[{"t": 1791792000, "open": 8.4, "high": 8.52, "low": 8.36, "close": 8.45, "updates": 7}])

    series = asyncio.run(AdminQueries(db, "Europe/Paris").price_series(2, START, END, "bid", 20000))

    # The quote changes come along: they are the range bars' volume.
    assert series == [(1791792000, 8.4, 8.52, 8.36, 8.45, 7)]
    query, args = db.calls[0]
    assert "bid_open AS open" in query and "bid_close AS close" in query and ", updates" in query
    assert "WHERE instrument_id = $1 AND ts >= $2 AND ts < $3" in query
    assert "IS NOT NULL" not in query
    assert "ORDER BY ts" in query and "LIMIT $4" in query
    assert args == (2, START, END, 20000)


def test_the_ask_is_read_without_the_bars_that_have_none():
    db = FakeDb()

    asyncio.run(AdminQueries(db, "Europe/Paris").price_series(2, START, END, "ask", 100))

    query, _ = db.calls[0]
    assert "ask_open AS open" in query and "bid_" not in query
    for column in ("ask_open", "ask_high", "ask_low", "ask_close"):
        assert f"{column} IS NOT NULL" in query


@pytest.mark.parametrize("side", ["mid", "bid_open; DROP TABLE price_bar_10s", ""])
def test_a_side_that_does_not_exist_never_reaches_the_statement(side):
    db = FakeDb()

    with pytest.raises(ValueError):
        asyncio.run(AdminQueries(db, "Europe/Paris").price_series(2, START, END, side, 100))

    assert db.calls == []


def test_coverage_is_read_in_time_order():
    db = FakeDb(rows=[{"ts": START, "feed_ok": True}])

    assert asyncio.run(AdminQueries(db, "Europe/Paris").price_coverage(START, END)) == [{"ts": START, "feed_ok": True}]
    assert db.calls[0][0].endswith("WHERE ts >= $1 AND ts < $2 ORDER BY ts")


@pytest.mark.parametrize("read", [
    lambda queries: queries.price_instruments(START, END),
    lambda queries: queries.price_bars(7, START, END, 60, 100),
    lambda queries: queries.price_coverage(START, END),
    lambda queries: queries.price_series(2, START, END, "bid", 100),
    lambda queries: queries.price_trades({"role": "underlying", "indice": "us100"}, START, END),
    lambda queries: queries.price_runs(START, END, 100),
    lambda queries: queries.price_assets(100),
])
def test_before_the_recorder_ever_ran_its_tables_read_as_empty(read):
    db = FakeDb(error=DatabaseUnavailable('relation "price_instrument" does not exist'))

    assert asyncio.run(read(AdminQueries(db, "Europe/Paris"))) == []


def test_no_instrument_is_none_not_an_error():
    queries = AdminQueries(FakeDb(), "Europe/Paris")

    assert asyncio.run(queries.price_instrument(7)) is None


# ── what Saxo did not quote ──

PARIS = pytz.timezone("Europe/Paris")
TURBOS = {2: "us100", 3: "us100", 4: "us100"}


def run(instrument_id, start, end, *, high=8.0, last_bid=7.5, **figures):
    """Bars of an instrument without a break over [start, end)."""
    first, last = int(start.timestamp()), int(end.timestamp()) - 10
    return Run(
        instrument_id, first, last, bars=(last - first) // 10 + 1, updates=figures.get("updates", 0),
        without_ask=figures.get("without_ask", 0), delayed_minutes=figures.get("delayed_minutes", 0),
        high=high, last_bid=last_bid,
    )


def available(runs, gaps=(), turbos=TURBOS):
    return availability(runs, turbos, list(gaps), SETTINGS.is_trading_time, PARIS)


def stretches_of(found):
    return [(stretch["start"], stretch["end"], stretch["reason"]) for stretch in found["stretches"]]


def gap(start, end, reason=DOWN):
    return {"start": start, "end": end, "seconds": int((end - start).total_seconds()), "reason": reason}


def test_an_instrument_quoted_all_day_has_nothing_missing():
    found = available([run(2, at(6), at(20), updates=4120)])[2]

    assert (found["bars"], found["quoted_seconds"], found["updates"]) == (5040, 50400, 4120)
    assert (found["first_bar"], found["last_bar"]) == (at(6), at(19, 59, 50))
    assert (found["missing_seconds"], found["holes"], found["stretches"], found["issues"]) == (0, 0, [], [])
    assert (found["stops"], found["knocked_out_at"], found["stretch_count"]) == ([], None, 0)


def test_minutes_without_a_bar_between_two_bars_of_a_day_are_a_hole():
    found = available([run(2, at(6), at(9)), run(2, at(9, 12), at(20))])[2]

    assert stretches_of(found) == [(at(9), at(9, 12), UNQUOTED)]
    assert (found["missing_seconds"], found["holes"], found["hole_seconds"], found["issues"]) == (720, 1, 720, ["holes"])
    assert found["bars"] == 5040 - 72


def test_a_hole_while_the_recorder_itself_was_not_recording_is_not_the_instruments():
    runs = [run(2, at(6), at(9)), run(2, at(9, 12), at(20))]

    assert available(runs, [gap(at(9), at(9, 12))])[2]["issues"] == []
    assert available(runs, [gap(at(8, 50), at(9, 30), SILENT)])[2]["issues"] == []


def test_only_the_part_of_a_hole_outside_the_recorders_gap_is_counted():
    runs = [run(2, at(6), at(9)), run(2, at(9, 12), at(20))]

    found = available(runs, [gap(at(9, 2), at(9, 7))])[2]

    assert stretches_of(found) == [(at(9), at(9, 2), UNQUOTED), (at(9, 7), at(9, 12), UNQUOTED)]
    assert (found["missing_seconds"], found["holes"]) == (420, 2)


def test_a_bar_or_two_missed_is_not_a_hole():
    # Twenty seconds: where the recorder comes back, an instrument may get its first quote a bar late.
    runs = [run(2, at(6), at(9)), run(2, at(9, 0, 20), at(20))]

    assert available(runs)[2]["issues"] == []
    assert available([run(2, at(6), at(9)), run(2, at(9, 0, 30), at(20))])[2]["issues"] == ["holes"]


def test_the_night_between_two_days_is_not_a_hole():
    runs = [run(2, at(6), at(20)), run(2, at(9, day=13), at(20, day=13))]

    found = available(runs)[2]

    # Nor is the morning of the second day: a turbo is only followed from the moment it is found again.
    assert (found["stretches"], found["issues"]) == ([], [])


def test_a_hole_is_only_counted_in_trading_hours():
    # Quoted before the open at 08:00 in Paris, then again from 08:10.
    runs = [run(2, at(5, 30), at(5, 40)), run(2, at(6, 10), at(20))]

    assert stretches_of(available(runs)[2]) == [(at(6), at(6, 10), UNQUOTED)]


def test_a_turbo_with_no_more_quote_while_the_others_went_on_has_stopped():
    runs = [run(2, at(6), at(11), high=8.0, last_bid=7.4), run(3, at(6), at(20)), run(4, at(6), at(19))]

    found = available(runs)

    assert stretches_of(found[2]) == [(at(11), at(20), STOPPED)]
    assert (found[2]["issues"], found[2]["missing_seconds"], found[2]["stops"]) == (["stopped"], 9 * 3600, [at(11)])
    # A stop is not a hole: nothing is missing between its bars.
    assert (found[2]["holes"], found[2]["hole_seconds"]) == (0, 0)
    # The one that went on longest is compared with the one before it: an hour, here.
    assert found[3]["issues"] == []
    assert stretches_of(found[4]) == [(at(19), at(20), STOPPED)]


def test_a_turbo_whose_price_had_fallen_to_almost_nothing_was_knocked_out_not_left_unquoted():
    runs = [run(2, at(6), at(11), high=8.0, last_bid=0.3), run(3, at(6), at(20))]

    found = available(runs)[2]

    assert (found["issues"], found["stretches"], found["stops"]) == ([], [], [])
    assert found["knocked_out_at"] == at(11)


def test_the_days_highest_bid_is_what_a_knock_out_is_measured_against():
    # 8.00 in the morning, then 0.30 at the end of the afternoon's run: knocked out, though that run never saw 8.00.
    runs = [
        run(2, at(6), at(9), high=8.0, last_bid=6.0), run(2, at(9, 5), at(11), high=6.0, last_bid=0.3),
        run(3, at(6), at(20)),
    ]

    found = available(runs)[2]

    assert found["knocked_out_at"] == at(11)
    assert stretches_of(found) == [(at(9), at(9, 5), UNQUOTED)]


def test_issuers_that_end_the_day_a_few_minutes_apart_have_not_stopped():
    runs = [run(2, at(6), at(19, 50)), run(3, at(6), at(20))]

    assert available(runs)[2]["issues"] == []


def test_a_stop_is_measured_outside_the_recorders_gaps_and_inside_trading_hours():
    # Ten minutes before the others, then twenty during which nothing at all was recorded.
    runs = [run(2, at(6), at(11)), run(3, at(6), at(11, 10)), run(3, at(11, 30), at(11, 31))]
    assert available(runs, [gap(at(11, 10), at(11, 30))])[2]["issues"] == []
    # The others quoted until 23:00 in Paris: what is after the close does not count.
    late = [run(2, at(6), at(19, 50)), run(3, at(6), at(21))]
    assert available(late)[2]["issues"] == []


def test_a_stop_cut_in_two_by_the_recorders_own_gap_is_still_one_stop():
    runs = [run(2, at(6), at(11)), run(3, at(6), at(20))]

    found = available(runs, [gap(at(12), at(12, 2), SILENT)])[2]

    assert stretches_of(found) == [(at(11), at(12), STOPPED), (at(12, 2), at(20), STOPPED)]
    assert (found["stops"], found["stretch_count"], found["holes"]) == ([at(11)], 2, 0)
    assert found["missing_seconds"] == 9 * 3600 - 120


def test_an_index_is_never_said_to_have_stopped():
    # Its cash hours end before the turbos': not a fault of the data.
    runs = [run(1, at(13, 30), at(16)), run(2, at(6), at(20)), run(3, at(6), at(20))]

    assert available(runs)[1]["issues"] == []


def test_a_turbo_is_compared_with_those_of_its_own_indice():
    runs = [run(2, at(6), at(11)), run(3, at(6), at(20))]

    assert available(runs, turbos={2: "germany40", 3: "us100"})[2]["issues"] == []


def test_a_turbo_alone_that_day_has_nothing_to_be_compared_with():
    assert available([run(2, at(6), at(11))])[2]["issues"] == []


def test_each_day_of_a_period_is_judged_on_its_own():
    runs = [
        run(2, at(6), at(11)), run(2, at(6, day=13), at(20, day=13)),
        run(3, at(6), at(20)), run(3, at(6, day=13), at(20, day=13)),
    ]

    found = available(runs)[2]

    assert stretches_of(found) == [(at(11), at(20), STOPPED)]
    assert found["stops"] == [at(11)]


def test_a_turbo_that_stops_on_two_days_has_two_stops():
    runs = [
        run(2, at(6), at(11)), run(2, at(6, day=13), at(9, day=13)),
        run(3, at(6), at(20)), run(3, at(6, day=13), at(20, day=13)),
    ]

    assert available(runs)[2]["stops"] == [at(11), at(9, day=13)]


def test_bars_without_an_ask_and_delayed_quotes_are_counted_not_flagged():
    runs = [run(2, at(6), at(9), without_ask=12, delayed_minutes=15), run(2, at(9), at(20), without_ask=3)]

    found = available(runs)[2]

    assert (found["without_ask"], found["delayed_minutes"], found["issues"]) == (15, 15, [])


def test_only_the_longest_stretches_are_given_in_the_order_they_happened():
    # A quote that comes and goes every other minute, all day: a minute quoted, a minute not.
    runs = [run(2, at(6) + timedelta(minutes=2 * n), at(6) + timedelta(minutes=2 * n + 1)) for n in range(60)]
    # One hole of five minutes among them.
    runs.append(run(2, at(8, 5), at(20)))

    found = available(sorted(runs, key=lambda item: item.first))[2]

    assert (found["holes"], found["stretch_count"]) == (60, 60)
    assert len(found["stretches"]) == STRETCHES_KEPT
    starts = [stretch["start"] for stretch in found["stretches"]]
    assert starts == sorted(starts)
    assert (at(7, 59), at(8, 5), UNQUOTED) in stretches_of(found)
    # What is counted is all of them, not only those given.
    assert found["missing_seconds"] == found["hole_seconds"] == 59 * 60 + 6 * 60


# ── range bars ──

T0 = 1_791_792_000      # a ten-second boundary


def series(*bars, start=T0, step=10):
    """Ten-second bars as (open, high, low, close), one every ``step`` seconds; a number is a flat bar."""
    rows = [(bar, bar, bar, bar) if isinstance(bar, (int, float)) else bar for bar in bars]
    times = [start + index * step for index in range(len(rows))]
    return (times, *[[row[column] for row in rows] for column in range(4)])


def built(data, ticks=10, decimals=2, limit=1000, **options):
    return range_bars(*data, ticks=ticks, decimals=decimals, limit=limit, **options)


def shapes(bars):
    """(open, high, low, close, complete) of each bar, in ticks."""
    return [(bar.open, bar.high, bar.low, bar.close, bar.complete) for bar in bars]


def test_a_price_that_stays_within_the_range_is_one_bar_in_progress():
    bars = built(series(1.00, 1.04, 0.97, 1.02))

    assert shapes(bars) == [(100, 104, 97, 102, False)]
    # From the start of its first ten seconds to the end of its last.
    assert (bars[0].t, bars[0].began, bars[0].ended) == (T0, T0, T0 + 40)


def test_a_bar_that_is_exactly_its_range_is_not_over_yet():
    # From 1.00 up to 1.10: the range is reached, not left. The price may still come back inside.
    assert shapes(built(series(1.00, 1.10, 1.05))) == [(100, 110, 100, 105, False)]


def test_a_bar_is_complete_when_the_price_leaves_its_range_and_closes_at_its_high():
    bars = built(series(1.00, 1.04, 1.12))

    assert shapes(bars) == [(100, 110, 100, 110, True), (110, 112, 110, 112, False)]
    # The next one begins in the ten seconds the first ended in, and no two bars share a time.
    assert [(bar.t, bar.began, bar.ended) for bar in bars] == [(T0, T0, T0 + 30), (T0 + 20, T0 + 20, T0 + 30)]


def test_going_down_it_closes_at_its_low():
    assert shapes(built(series(1.00, 1.03, 0.90))) == [(100, 103, 93, 93, True), (93, 93, 90, 90, False)]


def test_the_range_is_counted_from_the_other_end_of_the_bar_not_from_its_open():
    # Down to 0.96 first: the bar then ends above 1.06, not above 1.10.
    assert shapes(built(series(1.00, 0.96, 1.08))) == [(100, 106, 96, 106, True), (106, 108, 106, 108, False)]


def test_a_move_of_several_ranges_gives_several_bars_each_one_a_full_range():
    bars = built(series(1.00, 1.35))

    assert shapes(bars) == [
        (100, 110, 100, 110, True), (110, 120, 110, 120, True), (120, 130, 120, 130, True), (130, 135, 130, 135, False),
    ]


def test_every_complete_bar_is_exactly_its_range_and_closes_at_one_end():
    wandering = [round(8 + 0.9 * ((index * 37) % 23) / 23 + 0.4 * ((index * 11) % 7) / 7, 2) for index in range(400)]

    bars = built(series(*wandering), ticks=15, limit=100_000)

    complete = [bar for bar in bars if bar.complete]
    assert len(complete) > 20
    assert all(bar.high - bar.low == 15 for bar in complete)
    assert all(bar.close in (bar.high, bar.low) for bar in complete)
    assert all(bar.low <= bar.open <= bar.high for bar in bars)
    # Each opens where the one before closed.
    assert all(after.open == before.close for before, after in zip(bars, bars[1:]))
    assert [bar.complete for bar in bars[-1:]] == [False]


def test_no_two_bars_share_a_time_even_when_born_in_the_same_ten_seconds():
    bars = built(series(1.00, 1.35, 1.36))

    times = [bar.t for bar in bars]
    assert times == [T0, T0 + 10, T0 + 11, T0 + 12]
    assert all(later > earlier for earlier, later in zip(times, times[1:]))
    # That is a place on the chart's axis. When they really began is kept beside it, untouched.
    assert [bar.began for bar in bars] == [T0, T0 + 10, T0 + 10, T0 + 10]
    assert [bar.ended for bar in bars] == [T0 + 20, T0 + 20, T0 + 20, T0 + 30]


def test_times_catch_up_with_the_clock_after_a_burst():
    bars = built(series(1.00, 1.35, 1.36, 1.37, 1.50))

    assert bars[-1].t == T0 + 40


def test_inside_ten_seconds_that_rose_the_low_comes_before_the_high():
    # 1.00 → low 0.95 → high 1.08 → 1.06: the bar is left upward, above 1.05.
    up = built(series(1.00, (1.00, 1.08, 0.95, 1.06)))

    assert shapes(up) == [(100, 105, 95, 105, True), (105, 108, 105, 106, False)]


def test_inside_ten_seconds_that_fell_the_high_comes_first():
    # 1.00 → high 1.05 → low 0.92 → 0.94: the bar is left downward, under 0.95.
    down = built(series(1.00, (1.00, 1.05, 0.92, 0.94)))

    assert shapes(down) == [(100, 105, 95, 95, True), (95, 95, 92, 94, False)]


def test_a_pause_ends_the_bar_in_progress_and_nothing_is_made_up_for_the_jump():
    times, opens, highs, lows, closes = series(1.00, 1.04)
    # Ten hours later the price is five ranges higher.
    night = ([*times, T0 + 36_000], [*opens, 1.55], [*highs, 1.55], [*lows, 1.55], [*closes, 1.55])

    bars = built(night)

    assert shapes(bars) == [(100, 104, 100, 104, False), (155, 155, 155, 155, False)]
    assert [bar.after_pause for bar in bars] == [False, True]
    assert bars[1].t == T0 + 36_000


def test_a_few_missing_bars_are_not_a_pause():
    times, opens, highs, lows, closes = series(1.00, 1.04, step=60)

    assert [bar.after_pause for bar in built((times, opens, highs, lows, closes))] == [False]


def test_more_than_a_minute_without_a_bar_is_a_pause():
    assert [bar.after_pause for bar in built(series(1.00, 1.04, step=70))] == [False, True]


def test_more_bars_than_one_chart_is_given_are_refused():
    with pytest.raises(TooManyBars):
        built(series(1.00, 9.00), ticks=1, limit=100)


def test_a_range_under_one_tick_makes_no_sense():
    with pytest.raises(ValueError):
        built(series(1.00), ticks=0)


def test_no_price_no_bar():
    assert built(series()) == []


def test_prices_are_counted_in_whole_ticks_so_no_bar_is_a_hair_off_its_range():
    # 0.1 + 0.2 and its like: a hundred steps of one cent each way.
    climbing = [round(8.45 + index * 0.01, 2) for index in range(100)]

    bars = built(series(*climbing), ticks=7)

    assert all(bar.high - bar.low == 7 for bar in bars if bar.complete)
    assert range_rows(bars, 2)[0][1:5] == [8.45, 8.52, 8.45, 8.52]


def test_rows_give_prices_back_in_the_unit_of_the_instrument_with_the_index_beside():
    bars = built(series(1.00, 1.12))

    assert range_rows(bars, 2, index=[25000.5, None], volumes=[12, 3]) == [
        [T0, 1.0, 1.1, 1.0, 1.1, T0, T0 + 20, True, False, 25000.5, 12],
        [T0 + 10, 1.1, 1.12, 1.1, 1.12, T0 + 10, T0 + 20, False, False, None, 3],
    ]
    assert len(range_rows(bars, 2)[0]) == len(RANGE_COLUMNS)
    assert range_rows(bars, 2)[0][-1] is None


def test_each_ten_seconds_of_quotes_counts_once_in_the_range_bars_volume():
    # Three bars; the second ten seconds end the first bar and start the second.
    bars = built(series(1.00, 1.12, 1.15, 1.30))
    times = [T0, T0 + 10, T0 + 20, T0 + 30]

    volumes = range_volumes(bars, times, [5, 7, 2, 4])

    assert sum(volumes) == 5 + 7 + 2 + 4
    assert volumes[0] == 5 + 7
    # Ten seconds before the first bar, or after the last, are counted nowhere.
    assert range_volumes(bars, [T0 - 10, T0 + 1000], [9, 9]) == [0] * len(bars)


@pytest.mark.parametrize("prices, decimals", [
    ([8.45, 8.5, 9.0], 2),
    ([25000.0, 25012.5], 2),
    ([0.855, 0.86], 3),
    ([1.123456789], 5),
    ([], 2),
    ([float("nan"), 8.45], 2),
])
def test_the_tick_is_read_from_the_decimals_of_the_prices(prices, decimals):
    assert price_decimals(prices) == decimals


def test_the_decimals_of_every_series_count():
    assert price_decimals([8.45], [8.455], [8.4]) == 3


def _decimals_value_by_value(*series, minimum=2):
    """price_decimals as it was, reading every value rather than each distinct one once."""
    decimals = minimum
    for values in series:
        for value in values:
            text = repr(float(value))
            if "e" in text or "n" in text:
                continue
            decimals = max(decimals, len(text) - text.index(".") - 1)
            if decimals >= 5:
                return 5
    return decimals


@pytest.mark.parametrize("seed", range(25))
def test_reading_each_distinct_price_once_gives_the_decimals_of_every_price(seed):
    import random

    rng = random.Random(seed)
    places = rng.choice([0, 1, 2, 3, 4, 6])
    series = [[round(rng.uniform(1, 30_000), places) for _ in range(rng.randint(0, 300))] for _ in range(4)]
    # Repeated prices, as a quote that does not move gives, and values that say no decimals.
    series[0] += series[0][:50]
    series[rng.randrange(4)] += [float("nan"), 1e-07, float("inf"), -0.0][: rng.randint(0, 4)]

    for minimum in (0, 2):
        assert price_decimals(*series, minimum=minimum) == _decimals_value_by_value(*series, minimum=minimum)


def test_a_range_is_counted_in_the_assets_own_tick():
    # Capital.com's US100 has one decimal, Dukascopy's three: R60 is 6 points on one, 0.06 on the other.
    assert (tick_of(1), range_points(60, 1)) == (0.1, 6.0)
    assert (tick_of(3), range_points(60, 3), range_points(6000, 3)) == (0.001, 0.06, 6.0)
    assert tick_of(None) is None


@pytest.mark.parametrize("low, high, ticks", [
    (2_480_000, 2_510_000, 1_000),   # an index over 300 points: bars of 10 points
    (700, 1_000, 10),                # a turbo between 7 and 10 euros: 10 cents
    (840, 852, 1),                   # hardly moved: one tick, never less
    (100, 100, 1),
    (0, 100_000, 5_000),
    (0, 40_000, 2_000),
])
def test_the_automatic_range_is_a_round_twenty_fifth_of_what_the_price_covered(low, high, ticks):
    assert auto_range_ticks(low, high) == ticks


def test_the_index_under_a_bar_is_its_last_value_at_that_moment():
    times, values = [100, 110, 130], [1.0, 2.0, 3.0]

    assert sample_at(times, values, [90, 100, 109, 110, 125, 130, 500]) == [None, 1.0, 1.0, 2.0, 2.0, 3.0, 3.0]
    assert sample_at([], [], [100]) == [None]
