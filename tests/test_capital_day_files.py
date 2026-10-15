import asyncio
import csv
import gzip
import os
from datetime import date, datetime, timedelta, timezone

from src.capital_feed.day_files import ASSET_TYPE, DayFiles, bars_directory, day_path, write_day_file, written_days
from src.price_recorder.export import COLUMNS
from src.strategy import backtest
from src.strategy.history.dukascopy import write_day_file as write_dukascopy_day
from src.strategy.sources import FileBars

from tests.test_strategy_backtest import CLOCK, CONFIG, DAY, leg, recorded

T0 = datetime(2026, 10, 12, 7, 0, tzinfo=timezone.utc)


def _rows(bars):
    """Bars as the database gives them to the writer."""
    return [
        (datetime.fromtimestamp(bar.start, timezone.utc), bar.bid_open, bar.bid_high, bar.bid_low, bar.bid_close,
         bar.ask_open, bar.ask_high, bar.ask_low, bar.ask_close, bar.updates)
        for bar in bars
    ]


def _session_bars():
    """The day of tests/test_strategy_backtest.py: back and forth before the session, then down 200 points and up 400."""
    night = [30000.0] + leg(30000, 30060) + leg(30060, 30000) + leg(30000, 30060) + leg(30060, 30000)
    session = [30000.0] + leg(30000, 29800) + leg(29800, 30200) + [30200.0] * 6
    return recorded(CLOCK.epoch(DAY, "07:00"), night) + recorded(CLOCK.epoch(DAY, "08:00"), session)


def test_a_day_file_has_the_columns_of_wataprices_and_says_where_it_comes_from(tmp_path):
    path = str(tmp_path / "2026-10-12.csv.gz")
    write_day_file(path, [
        (T0, 25000.0, 25004.0, 24998.0, 24998.0, 25001.0, 25005.0, 24999.0, 24999.0, 3),
        (T0 + timedelta(seconds=10), 24998.0, 24998.0, 24998.0, 24998.0, None, None, None, None, 0),
    ], epic="US100", label="us100", name="US Tech 100")

    with gzip.open(path, "rt", newline="", encoding="utf-8") as in_file:
        rows = list(csv.DictReader(in_file))

    assert list(rows[0]) == list(COLUMNS)
    assert {key: rows[0][key] for key in ("ts", "uic", "asset_type", "symbol", "description", "role", "indice")} == {
        "ts": "2026-10-12T07:00:00Z", "uic": "", "asset_type": ASSET_TYPE, "symbol": "US100",
        "description": "US Tech 100 (Capital.com)", "role": "underlying", "indice": "us100",
    }
    assert (rows[0]["bid_high"], rows[0]["ask_close"], rows[0]["updates"]) == ("25004.0", "24999.0", "3")
    # A bar without an ask leaves it empty, as wataprices does.
    assert rows[1]["ask_open"] == ""


def test_the_same_bars_give_the_same_bytes(tmp_path):
    rows = _rows(_session_bars())
    first, second = str(tmp_path / "a.csv.gz"), str(tmp_path / "b.csv.gz")

    write_day_file(first, rows, epic="US100", label="us100", name="US Tech 100")
    write_day_file(second, rows, epic="US100", label="us100", name="US Tech 100")

    with open(first, "rb") as one, open(second, "rb") as other:
        assert one.read() == other.read()


def test_backtests_read_a_capital_com_day_as_they_read_a_dukascopy_one(tmp_path, capsys):
    """The same bars, in a file of each: the same trades, the same days, the same bars counted."""
    bars = _session_bars()
    capital = bars_directory(str(tmp_path), "us100")
    write_day_file(day_path(str(tmp_path), "us100", DAY), _rows(bars), epic="US100", label="us100", name="US Tech 100")
    dukascopy = str(tmp_path / "dukascopy")
    write_dukascopy_day(os.path.join(dukascopy, "2026-10-12.csv.gz"), bars, symbol="TEST", label="us100")

    def run(source, out):
        code = backtest.run(backtest.parse_arguments([
            "--config", CONFIG, "--from", "2026-10-12", "--source", source, "--indice", "us100",
            "--baseline", "0", "--out", out,
        ]))
        assert code == 0
        return {name: open(os.path.join(out, name), encoding="utf-8").read() for name in ("trades_110.csv", "days_110.csv")}

    from_capital = run(capital, str(tmp_path / "out-capital"))
    from_dukascopy = run(dukascopy, str(tmp_path / "out-dukascopy"))

    assert from_capital == from_dukascopy
    assert from_capital["trades_110.csv"].count("\n") == 2
    whole_day = datetime(2026, 10, 12, tzinfo=timezone.utc)
    read = list(FileBars(capital, "us100").bars(whole_day, whole_day + timedelta(days=1)))
    assert [(bar.start, bar.bid_close, bar.ask_close, bar.updates) for bar in read] == [
        (bar.start, bar.bid_close, bar.ask_close, bar.updates) for bar in bars
    ]


class Store:
    def __init__(self, bars_by_day):
        self.bars_by_day = bars_by_day
        self.asked = []

    async def first_bar_at(self, instrument_id):
        days = sorted(self.bars_by_day)
        return datetime.combine(days[0], datetime.min.time(), timezone.utc) + timedelta(hours=1) if days else None

    async def bars_between(self, instrument_id, start, end):
        self.asked.append(start.date())
        return self.bars_by_day.get(start.date(), [])


def _day_rows(day: date):
    start = datetime.combine(day, datetime.min.time(), timezone.utc) + timedelta(hours=1)
    return [(start, 1.0, 2.0, 0.5, 1.5, 1.1, 2.1, 0.6, 1.6, 4)]


def test_the_days_that_are_over_and_have_no_file_are_written_and_the_others_left_alone(tmp_path):
    root = str(tmp_path)
    # Friday 9 to Tuesday 13 October: nothing on the weekend, the 12th already written, the 13th still going.
    store = Store({day: _day_rows(day) for day in (date(2026, 10, 9), date(2026, 10, 12), date(2026, 10, 13))})
    write_day_file(day_path(root, "us100", date(2026, 10, 12)), _day_rows(date(2026, 10, 12)), epic="US100", label="us100", name=None)
    files = DayFiles(root, store)

    written = asyncio.run(files.write_finished_days(
        [(1, "US100", "us100", "US Tech 100")], datetime(2026, 10, 13, 23, 58, tzinfo=timezone.utc),
    ))

    assert written == [("us100", date(2026, 10, 9))]
    assert written_days(root, "us100") == {date(2026, 10, 9), date(2026, 10, 12)}
    assert (files.written, files.last_written) == (1, ("us100", date(2026, 10, 9)))

    # Asked again later: the weekend found empty is not asked for again, the 13th is now over.
    store.asked.clear()
    written = asyncio.run(files.write_finished_days(
        [(1, "US100", "us100", "US Tech 100")], datetime(2026, 10, 14, 0, 5, tzinfo=timezone.utc),
    ))
    assert written == [("us100", date(2026, 10, 13))]
    assert store.asked == [date(2026, 10, 13)]


def test_an_instrument_without_a_bar_has_no_file(tmp_path):
    files = DayFiles(str(tmp_path), Store({}))

    assert asyncio.run(files.write_finished_days([(1, "US100", "us100", None)], datetime.now(timezone.utc))) == []
    assert written_days(str(tmp_path), "us100") == set()
