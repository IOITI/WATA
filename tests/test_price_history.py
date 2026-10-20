import gzip
import json
import lzma
import os
import struct
from datetime import date, datetime, timezone

import pytest

from src.price_recorder.export import COLUMNS
from src.price_history import dukascopy
from src.price_history.dukascopy import (
    CACHED,
    CLOSED,
    FAILED,
    FETCHED,
    UNPUBLISHED,
    CorruptHour,
    Tick,
    bars_from_ticks,
    day_path,
    decode_hour,
    fetch_hour,
    hour_url,
    market_is_closed,
    raw_path,
    sync,
)
from src.price_history.files import FileBars, SeveralInstruments

SYMBOL = "USATECHIDXUSD"
# Monday 12 October 2026.
MONDAY = date(2026, 10, 12)


def _hour(day: date, hour: int) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc)


def _file(ticks) -> bytes:
    """An hourly file as Dukascopy serves it: (milliseconds, bid, ask) per tick, prices in points."""
    records = b"".join(
        struct.pack(">IIIff", ms, round(ask * 1000), round(bid * 1000), 1.0, 1.0) for ms, bid, ask in ticks
    )
    return lzma.compress(records, format=lzma.FORMAT_ALONE)


def _one_tick_per_hour(url: str) -> bytes:
    hour = int(url.rsplit("/", 1)[1][:2])
    return _file([(0, 30000.0 + hour, 30001.0 + hour)])


class Server:
    """Answers like the datafeed, and counts what it is asked."""

    def __init__(self, answer=_one_tick_per_hour):
        self.answer = answer
        self.asked: list[str] = []

    def __call__(self, url: str):
        self.asked.append(url)
        result = self.answer(url)
        if isinstance(result, Exception):
            raise result
        return result


# ── one hourly file ──


def test_months_are_counted_from_zero_in_the_address():
    assert hour_url(SYMBOL, _hour(MONDAY, 7)) == (
        "https://datafeed.dukascopy.com/datafeed/USATECHIDXUSD/2026/09/12/07h_ticks.bi5"
    )
    assert hour_url(SYMBOL, _hour(date(2026, 1, 5), 23)).endswith("/2026/00/05/23h_ticks.bi5")


def test_a_file_gives_its_ticks_with_their_time_bid_and_ask():
    hour = _hour(MONDAY, 7)

    ticks = decode_hour(_file([(0, 30955.5, 30957.0), (1500, 30956.0, 30957.5)]), hour)

    assert ticks == [
        Tick(hour.timestamp(), 30955.5, 30957.0),
        Tick(hour.timestamp() + 1.5, 30956.0, 30957.5),
    ]


def test_an_empty_file_is_an_hour_without_a_tick_and_anything_else_is_refused():
    hour = _hour(MONDAY, 7)

    assert decode_hour(b"", hour) == []
    with pytest.raises(CorruptHour):
        decode_hour(b"<html><body><h1>503 Service Unavailable</h1></body></html>", hour)
    with pytest.raises(CorruptHour):
        decode_hour(lzma.compress(b"seven..", format=lzma.FORMAT_ALONE), hour)


def test_nothing_is_asked_for_the_weekend():
    saturday, sunday = date(2026, 10, 10), date(2026, 10, 11)

    assert market_is_closed(_hour(saturday, 12))
    assert market_is_closed(_hour(sunday, 19))
    assert not market_is_closed(_hour(sunday, 20))
    assert not market_is_closed(_hour(MONDAY, 0))


# ── fetching ──


def test_an_hour_fetched_is_kept_and_not_asked_for_again(tmp_path):
    server = Server()
    hour = _hour(MONDAY, 7)

    assert fetch_hour(str(tmp_path), SYMBOL, hour, server) == FETCHED
    assert fetch_hour(str(tmp_path), SYMBOL, hour, server) == CACHED

    assert len(server.asked) == 1
    assert os.path.exists(raw_path(str(tmp_path), SYMBOL, hour))


def test_a_refusal_is_tried_again_and_the_wait_grows(tmp_path):
    answers = iter([RuntimeError("503"), RuntimeError("timeout"), _file([(0, 1.0, 2.0)])])
    server = Server(lambda url: next(answers))
    waits = []

    outcome = fetch_hour(str(tmp_path), SYMBOL, _hour(MONDAY, 7), server, pause=2.0, sleep=waits.append)

    assert outcome == FETCHED
    assert waits == [2.0, 4.0]


def test_an_error_page_is_never_kept_as_an_hour(tmp_path):
    server = Server(lambda url: b"<html>503 Service Unavailable</html>")
    hour = _hour(MONDAY, 7)

    outcome = fetch_hour(str(tmp_path), SYMBOL, hour, server, attempts=3, sleep=lambda seconds: None)

    assert outcome == FAILED
    assert len(server.asked) == 3
    assert not os.path.exists(raw_path(str(tmp_path), SYMBOL, hour))


def test_an_hour_the_server_does_not_have_yet_is_not_kept_and_not_insisted_on(tmp_path):
    server = Server(lambda url: None)
    hour = _hour(MONDAY, 22)

    assert fetch_hour(str(tmp_path), SYMBOL, hour, server) == UNPUBLISHED
    assert len(server.asked) == 1
    assert not os.path.exists(raw_path(str(tmp_path), SYMBOL, hour))


def test_a_closed_hour_is_not_asked_for(tmp_path):
    server = Server()

    assert fetch_hour(str(tmp_path), SYMBOL, _hour(date(2026, 10, 10), 12), server) == CLOSED
    assert server.asked == []


# ── ticks to ten-second bars ──


def test_ticks_become_the_recorders_ten_second_bars():
    start = _hour(MONDAY, 7).timestamp()
    ticks = [
        Tick(start + 1, 100.0, 101.0),
        Tick(start + 4, 102.0, 103.0),
        Tick(start + 8, 99.0, 100.0),
        Tick(start + 12, 99.5, 100.5),
    ]

    first, second = bars_from_ticks(ticks, until=start + 20)

    assert (first.start, first.bid_open, first.bid_high, first.bid_low, first.bid_close) == (start, 100.0, 102.0, 99.0, 99.0)
    assert (first.ask_open, first.ask_high, first.ask_low, first.ask_close) == (101.0, 103.0, 100.0, 100.0)
    assert first.updates == 3
    # The next bar opens on the quote that was standing, as the recorder's do.
    assert (second.start, second.bid_open, second.bid_close, second.updates) == (start + 10, 99.0, 99.5, 1)


def test_no_bar_is_made_up_for_a_time_without_any_tick():
    start = _hour(MONDAY, 7).timestamp()
    ticks = [Tick(start + 1, 100.0, 101.0), Tick(start + 301, 105.0, 106.0)]

    bars = bars_from_ticks(ticks, until=start + 310)

    # The quote is carried while it can be believed (thirty seconds), not for five minutes.
    assert [bar.start - start for bar in bars] == [0, 10, 20, 300]


# ── whole days ──


def test_a_day_whose_hours_are_all_there_is_written_and_reads_back_as_index_bars(tmp_path):
    root = str(tmp_path)

    reports = sync(MONDAY, MONDAY, fetch=Server(), root=root, pause=0)

    assert [(report.day, report.status) for report in reports] == [(MONDAY, "written")]
    path = day_path(root, "us100", MONDAY)
    with gzip.open(path, "rt") as in_file:
        assert in_file.readline().strip().split(",") == list(COLUMNS)

    start, end = _hour(MONDAY, 0), _hour(date(2026, 10, 13), 0)
    bars = list(FileBars(os.path.dirname(path), "us100").bars(start, end))
    assert bars[0].start == start.timestamp()
    assert (bars[0].bid_open, bars[0].ask_open, bars[0].updates) == (30000.0, 30001.0, 1)
    assert bars[0].mid_close == 30000.5
    # One tick an hour: three bars carry it, then the feed is taken for silent.
    assert len(bars) == 24 * 3


def test_a_day_with_a_hole_is_not_written_and_the_next_run_only_asks_for_what_is_missing(tmp_path):
    root = str(tmp_path)
    refusing = Server(lambda url: RuntimeError("503") if "/07h_" in url else _one_tick_per_hour(url))

    first = sync(MONDAY, MONDAY, fetch=refusing, root=root, attempts=2, pause=0)

    assert first[0].status == "incomplete"
    assert first[0].failed_hours == [_hour(MONDAY, 7)]
    assert not os.path.exists(day_path(root, "us100", MONDAY))

    answering = Server()
    second = sync(MONDAY, MONDAY, fetch=answering, root=root, pause=0)

    assert second[0].status == "written"
    assert [url.rsplit("/", 1)[1] for url in answering.asked] == ["07h_ticks.bi5"]


def test_a_day_that_is_not_over_is_written_up_to_its_last_hour_and_completed_later(tmp_path):
    root = str(tmp_path)
    until_noon = Server(lambda url: _one_tick_per_hour(url) if int(url.rsplit("/", 1)[1][:2]) < 12 else None)

    first = sync(MONDAY, MONDAY, fetch=until_noon, root=root, pause=0)

    assert first[0].status == "partial"
    assert first[0].bars == 12 * 3

    later = sync(MONDAY, MONDAY, fetch=Server(), root=root, pause=0)
    assert (later[0].status, later[0].bars) == ("written", 24 * 3)
    assert sync(MONDAY, MONDAY, fetch=Server(), root=root, pause=0)[0].status == "kept"


def test_an_hour_missing_in_the_middle_of_a_day_is_a_hole_even_when_the_server_says_it_has_none(tmp_path):
    root = str(tmp_path)
    without_seven = Server(lambda url: None if "/07h_" in url else _one_tick_per_hour(url))

    reports = sync(MONDAY, MONDAY, fetch=without_seven, root=root, pause=0)

    assert reports[0].status == "incomplete"
    assert not os.path.exists(day_path(root, "us100", MONDAY))


def test_when_the_server_answers_nothing_the_run_stops_instead_of_insisting(tmp_path):
    silent = Server(lambda url: RuntimeError("connect timeout"))
    said = []

    reports = sync(MONDAY, date(2026, 10, 14), fetch=silent, root=str(tmp_path), attempts=1, pause=0, progress=said.append)

    assert [report.status for report in reports] == ["incomplete", "not tried", "not tried"]
    # Only the first day was asked for.
    assert len(silent.asked) == 24
    assert said[-1] == "The server answers nothing any more: stopping here. Try again later."


def test_a_few_refusals_among_answers_do_not_stop_the_run(tmp_path):
    grudging = Server(lambda url: RuntimeError("503") if "/07h_" in url or "/08h_" in url or "/09h_" in url else _one_tick_per_hour(url))

    reports = sync(MONDAY, date(2026, 10, 13), fetch=grudging, root=str(tmp_path), attempts=1, pause=0)

    assert [report.status for report in reports] == ["incomplete", "incomplete"]


def test_a_saturday_is_an_empty_day_and_costs_no_request(tmp_path):
    server = Server()

    reports = sync(date(2026, 10, 10), date(2026, 10, 10), fetch=server, root=str(tmp_path), pause=0)

    assert reports[0].status == "empty"
    assert server.asked == []


def test_the_command_says_what_is_ready_and_what_to_run_again(tmp_path, monkeypatch, capsys):
    refusing = Server(lambda url: RuntimeError("503") if "/2026/09/13/" in url else _one_tick_per_hour(url))
    monkeypatch.setattr(dukascopy, "http_fetch", lambda: refusing)
    monkeypatch.setattr(dukascopy.time, "sleep", lambda seconds: None)

    code = dukascopy.main(["--from", "2026-10-12", "--to", "2026-10-13", "--root", str(tmp_path), "--attempts", "1"])

    out = capsys.readouterr().out
    assert code == 1
    assert "1 day(s) ready" in out and "1 incomplete" in out
    assert "2026-10-13" in out.splitlines()[-1]


def test_the_command_can_report_what_became_of_each_day(tmp_path, monkeypatch):
    # Monday: an hour never answered. Tuesday: every hour answered, none of them published after noon.
    def answer(url: str):
        if "/2026/09/12/07h_" in url:
            return RuntimeError("503")
        if "/2026/09/13/" in url and int(url.rsplit("/", 1)[1][:2]) >= 12:
            return None
        return _one_tick_per_hour(url)

    monkeypatch.setattr(dukascopy, "http_fetch", lambda: Server(answer))
    monkeypatch.setattr(dukascopy.time, "sleep", lambda seconds: None)
    report = tmp_path / "out" / "report.json"

    dukascopy.main([
        "--from", "2026-10-10", "--to", "2026-10-13", "--root", str(tmp_path), "--attempts", "1", "--report", str(report),
    ])

    assert json.loads(report.read_text())["days"] == [
        {"day": "2026-10-10", "status": "empty", "bars": 0, "unpublished_hours": 0, "unanswered_hours": 0},
        {"day": "2026-10-11", "status": "written", "bars": 12, "unpublished_hours": 0, "unanswered_hours": 0},
        {"day": "2026-10-12", "status": "incomplete", "bars": 0, "unpublished_hours": 0, "unanswered_hours": 1},
        {"day": "2026-10-13", "status": "partial", "bars": 36, "unpublished_hours": 12, "unanswered_hours": 0},
    ]


# ── reading files of bars ──


def _export(path, rows) -> None:
    with gzip.open(path, "wt", newline="") as out_file:
        out_file.write(",".join(COLUMNS) + "\n")
        for row in rows:
            out_file.write(",".join(str(row.get(name, "")) for name in COLUMNS) + "\n")


def _row(ts, role="underlying", indice="us100", asset_type="CfdOnIndex", uic=4913, **prices):
    row = {"ts": ts, "role": role, "indice": indice, "asset_type": asset_type, "uic": uic, "updates": 2}
    row.update({"bid_open": 100, "bid_high": 101, "bid_low": 99, "bid_close": 100.5})
    row.update(prices)
    return row


def test_an_export_of_the_recorder_gives_the_index_and_leaves_the_turbos(tmp_path):
    path = str(tmp_path / "prices_2026-10-12_2026-10-12.csv.gz")
    _export(path, [
        _row("2026-10-12T08:00:00Z", ask_open=100.4, ask_high=101.4, ask_low=99.4, ask_close=100.9),
        _row("2026-10-12T08:00:00Z", role="turbo", asset_type="WarrantKnockOut", uic=1),
        _row("2026-10-12T08:00:00Z", indice="de40"),
        _row("2026-10-12T08:00:10Z"),
    ])

    bars = list(FileBars(path, "us100").bars(_hour(MONDAY, 0), _hour(MONDAY, 23)))

    assert [bar.start for bar in bars] == [1791792000, 1791792010]
    assert (bars[0].ask_open, bars[0].mid_open) == (100.4, 100.2)
    # No ask recorded (an index quoted with one price): the bid stands for both.
    assert (bars[1].ask_high, bars[1].mid_high) == (101.0, 101.0)


def test_only_the_bars_that_begin_in_the_period_are_given(tmp_path):
    path = str(tmp_path / "export.csv.gz")
    _export(path, [_row("2026-10-12T07:59:50Z"), _row("2026-10-12T08:00:00Z"), _row("2026-10-12T09:00:00Z")])

    bars = list(FileBars(path, "us100").bars(_hour(MONDAY, 8), _hour(MONDAY, 9)))

    assert [bar.start for bar in bars] == [1791792000]


def test_two_instruments_for_the_same_indice_have_to_be_told_apart(tmp_path):
    path = str(tmp_path / "export.csv.gz")
    _export(path, [
        _row("2026-10-12T08:00:00Z"),
        _row("2026-10-12T08:00:00Z", asset_type="CapitalCom", uic=0, bid_open=200),
    ])
    start, end = _hour(MONDAY, 0), _hour(MONDAY, 23)

    with pytest.raises(SeveralInstruments, match="CfdOnIndex and CapitalCom"):
        list(FileBars(path, "us100").bars(start, end))

    chosen = list(FileBars(path, "us100", asset_type="CapitalCom").bars(start, end))
    assert [bar.bid_open for bar in chosen] == [200.0]


def test_in_a_directory_of_days_only_the_days_of_the_period_are_opened(tmp_path):
    _export(str(tmp_path / "2026-10-09.csv.gz"), [_row("2026-10-09T08:00:00Z")])
    _export(str(tmp_path / "2026-10-12.csv.gz"), [_row("2026-10-12T08:00:00Z")])
    # Not a file of bars: it must not even be opened.
    (tmp_path / "2026-10-13.csv.gz").write_bytes(b"not gzip")
    (tmp_path / "notes.txt").write_text("ignored")

    bars = list(FileBars(str(tmp_path), "us100").bars(_hour(MONDAY, 0), _hour(date(2026, 10, 13), 0)))

    assert [bar.start for bar in bars] == [1791792000]
