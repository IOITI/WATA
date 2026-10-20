import asyncio
import csv
import gzip
import math
import os
from datetime import date, datetime, timezone

from src.backtest.clock import TradingClock
from src.backtest.data import DatabaseData, FileData
from src.price_recorder.export import COLUMNS

CLOCK = TradingClock("Europe/Paris")
DAY = date(2026, 10, 14)


def utc(hour: int, minute: int, second: int = 0) -> str:
    return datetime(2026, 10, 14, hour, minute, second, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def row(ts: str, *, role="turbo", indice="us100", uic="40000001", direction="long", barrier="24800", bid=5.0, ask="5.05",
        instrument_id=None, symbol="SG1", asset_type="WarrantOpenEndKnockOut") -> dict:
    values = {
        "ts": ts, "uic": uic, "asset_type": asset_type, "symbol": symbol, "description": f"Turbo {direction} {barrier}",
        "role": role, "indice": indice, "direction": direction, "barrier": barrier, "issuer": "SG",
        "bid_open": bid, "bid_high": bid, "bid_low": bid, "bid_close": bid,
        "ask_open": ask, "ask_high": ask, "ask_low": ask, "ask_close": ask, "updates": 1, "delayed_minutes": 0,
    }
    if instrument_id is not None:
        values["instrument_id"] = instrument_id
    return values


def write_export(path, rows, with_ids: bool = True) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    columns = list(COLUMNS) + (["instrument_id"] if with_ids else [])
    with gzip.open(path, "wt", newline="", encoding="utf-8") as out_file:
        writer = csv.DictWriter(out_file, fieldnames=columns)
        writer.writeheader()
        for values in rows:
            writer.writerow({column: values.get(column, "") for column in columns})


def test_a_day_of_files_gives_each_turbo_its_bars_and_the_directions_in_the_traders_order(tmp_path):
    write_export(str(tmp_path / "saxo" / "prices_2026-10-14.csv.gz"), [
        # The index of the indice: not a turbo.
        row(utc(7, 0), role="underlying", uic="1909050", direction="", barrier="", bid=25010.0, ask="", instrument_id=1),
        # Out of time order in the file, as two exports put together can be.
        row(utc(7, 0, 10), uic="40000002", barrier="24900", bid=4.2, ask="4.25", instrument_id=12),
        row(utc(7, 0), uic="40000002", barrier="24900", bid=4.1, ask="", instrument_id=12),
        row(utc(7, 0), uic="40000001", barrier="24800", bid=5.0, instrument_id=11),
        row(utc(7, 0), uic="40000003", direction="short", barrier="25300", bid=6.0, instrument_id=13),
        row(utc(7, 0), uic="40000004", direction="short", barrier="25200", bid=5.5, instrument_id=14),
        # Another indice, and the day before.
        row(utc(7, 0), indice="de40", uic="40000009", instrument_id=19),
        row("2026-10-13T07:00:00Z", uic="40000001", instrument_id=11),
    ])
    with open(tmp_path / "saxo" / "coverage-2026-10-14.csv", "w", encoding="utf-8") as coverage:
        coverage.write(f"ts,feed_ok\n{utc(7, 0)},true\n{utc(7, 0, 10)},false\n")
    start, end = CLOCK.day_bounds(DAY)

    day = asyncio.run(FileData(str(tmp_path)).day("us100", DAY, start, end))

    assert [series.ref.instrument_id for series in day.turbos["long"]] == [12, 11]
    assert [series.ref.instrument_id for series in day.turbos["short"]] == [14, 13]
    nearest = day.turbos["long"][0]
    assert list(nearest.ts) == [int(datetime(2026, 10, 14, 7, 0, tzinfo=timezone.utc).timestamp()), nearest.ts[0] + 10]
    assert list(nearest.bid_open) == [4.1, 4.2]
    assert math.isnan(nearest.ask_open[0]) and nearest.ask_open[1] == 4.25
    assert (nearest.ref.barrier, nearest.ref.direction, nearest.ref.issuer, nearest.ref.uic) == (24900.0, "long", "SG", 40000002)
    assert nearest.quote_at(nearest.ts[0]) == (4.1, None)
    assert day.covered == {nearest.ts[0]}
    assert day.is_covered(nearest.ts[0]) and not day.is_covered(nearest.ts[1])


def test_files_without_instrument_ids_give_ids_of_their_own_below_the_admins(tmp_path):
    write_export(str(tmp_path / "saxo" / "a.csv.gz"), [row(utc(7, 0)), row(utc(7, 0), uic="40000002", barrier="24900")],
                 with_ids=False)
    start, end = CLOCK.day_bounds(DAY)

    day = asyncio.run(FileData(str(tmp_path)).day("us100", DAY, start, end))

    assert sorted(series.ref.instrument_id for series in day.turbos["long"]) == [-2, -1]
    # Without the recorder's own account: listened to where a turbo has a bar.
    assert day.covered is None and day.is_covered(day.turbos["long"][0].ts[0])


class RecordingDb:
    """Answers the queries of DatabaseData from rows given by instrument, and keeps what it was asked."""

    def __init__(self, instruments, bars, coverage):
        self.instruments, self.bars, self.coverage = instruments, bars, coverage
        self.asked = []
        self.closed = False

    async def fetch(self, query, *args):
        self.asked.append((" ".join(query.split()), args))
        if "FROM price_instrument" in query:
            return self.instruments
        if "FROM price_bar_10s" in query:
            return self.bars.get(args[0], [])
        if "FROM price_recorder_coverage" in query:
            return self.coverage
        raise AssertionError(query)

    async def close(self):
        self.closed = True


def test_a_day_from_the_database_is_read_by_instrument_and_period_only():
    seven = int(datetime(2026, 10, 14, 7, 0, tzinfo=timezone.utc).timestamp())
    instrument = lambda id_, direction, barrier: {  # noqa: E731
        "id": id_, "uic": 40_000_000 + id_, "asset_type": "WarrantOpenEndKnockOut", "symbol": f"S{id_}",
        "description": "Turbo", "currency": "EUR", "direction": direction, "barrier": barrier, "issuer": "BNP",
    }
    bar = lambda t, bid: {"t": t, "bid_open": bid, "bid_high": bid, "bid_low": bid, "bid_close": bid,  # noqa: E731
                          "ask_open": bid + 0.04, "ask_close": None}
    db = RecordingDb(
        [instrument(3, "long", 24_800.0), instrument(4, "short", 25_100.0), instrument(5, "long", 24_900.0),
         instrument(6, None, 24_000.0)],
        {3: [bar(seven, 5.0), bar(seven + 10, 5.1)], 4: [bar(seven, 6.0)]},
        [{"t": seven, "feed_ok": True}, {"t": seven + 10, "feed_ok": False}],
    )
    start, end = CLOCK.day_bounds(DAY)

    async def read():
        data = DatabaseData(db)
        day = await data.day("us100", DAY, start, end)
        await data.close()
        return day

    day = asyncio.run(read())

    # The turbo without a bar that day is left out, as is one without a direction.
    assert [series.ref.instrument_id for series in day.turbos["long"]] == [3]
    assert [series.ref.instrument_id for series in day.turbos["short"]] == [4]
    assert list(day.turbos["long"][0].bid_close) == [5.0, 5.1] and math.isnan(day.turbos["long"][0].ask_close[0])
    assert day.covered == {seven}
    instruments_query, *bar_queries, coverage_query = db.asked
    assert instruments_query[1][0] == "us100"
    assert all("WHERE instrument_id = $1 AND ts >= $2 AND ts < $3" in query for query, _ in bar_queries)
    assert [args[0] for _, args in bar_queries] == [3, 4, 5]
    assert coverage_query[1] == (datetime.fromtimestamp(start, timezone.utc), datetime.fromtimestamp(end, timezone.utc))
    assert db.closed
