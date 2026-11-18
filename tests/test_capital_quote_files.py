"""
The quotes of the Capital.com feed kept as files (src/capital_feed/quote_files.py): one gzip CSV per instrument
and UTC day, appended a member at a time, put right after a feed stopped while writing, finished as one member.
"""

import asyncio
import gzip
import os
from datetime import date, datetime, timezone

import pytest

from src.capital_feed import quote_files
from src.capital_feed.quote_files import HEADER, QuoteFiles, day_path, encode, read_quotes, whole_members
from src.price_recorder.bars import bucket_start

DAY = date(2026, 11, 18)
NOON = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc).timestamp()
MIDNIGHT = datetime(2026, 11, 19, 0, 0, tzinfo=timezone.utc).timestamp()


class Clock:
    def __init__(self, at: float):
        self.at = at

    def __call__(self) -> float:
        return self.at


def _files(root, clock, **settings) -> QuoteFiles:
    return QuoteFiles(str(root), clock=clock, **settings)


def _write(files: QuoteFiles) -> None:
    asyncio.run(files.write())


def _stop(files: QuoteFiles) -> None:
    asyncio.run(files.stop())


def _part(root, label="us100", day=DAY) -> str:
    return day_path(str(root), label, day) + ".part"


def _quote(files, at, bid=25000.5, ask=25001.5, stamp=None, label="us100"):
    files.take(label, at, stamp if stamp is not None else int(at * 1000) - 40, bid, ask)


def test_a_quote_is_its_receipt_time_capital_coms_time_and_prices_and_a_close_has_no_price(tmp_path):
    clock = Clock(NOON)
    files = _files(tmp_path, clock)
    files.take("us100", NOON + 0.123, 1795521600080, 25000.5, 25001.5)
    files.take("us100", NOON + 0.5, None, 25000.6, 25001.6)
    files.closed("us100", NOON + 1)
    _write(files)

    raw = gzip.open(_part(tmp_path), "rb").read()
    assert raw.startswith(HEADER) and raw.count(HEADER) == 1
    assert list(read_quotes(_part(tmp_path))) == [
        (int((NOON + 0.123) * 1000), 1795521600080, 25000.5, 25001.5),
        (int((NOON + 0.5) * 1000), None, 25000.6, 25001.6),
        (int((NOON + 1) * 1000), None, None, None),
    ]
    _stop(files)


def test_rx_falls_in_the_ten_seconds_the_bars_put_the_quote_in():
    for received_at in (NOON - 1e-7, NOON, NOON + 1e-4, NOON + 9.9999, NOON + 9.99999999, NOON + 10, 1794351609.9995):
        rx, _ = encode(received_at, None, 1.0, 1.0)
        assert rx // 10_000 * 10 == bucket_start(received_at), received_at


def test_quotes_on_both_sides_of_midnight_go_to_two_days(tmp_path):
    clock = Clock(MIDNIGHT + 5)
    files = _files(tmp_path, clock)
    _quote(files, MIDNIGHT - 1)
    _quote(files, MIDNIGHT + 1)
    _write(files)

    assert len(list(read_quotes(_part(tmp_path, day=DAY)))) == 1
    assert len(list(read_quotes(_part(tmp_path, day=date(2026, 11, 19))))) == 1
    _stop(files)


def test_a_day_is_finished_once_it_ended_long_enough_ago_as_one_member_of_the_same_lines(tmp_path):
    clock = Clock(MIDNIGHT - 30)
    files = _files(tmp_path, clock)
    for second in range(20):
        _quote(files, MIDNIGHT - 60 + second)
        _write(files)
    final = day_path(str(tmp_path), "us100", DAY)
    lines = list(read_quotes(_part(tmp_path)))

    clock.at = MIDNIGHT + quote_files.FINAL_AFTER_SECONDS - 1
    _write(files)
    assert not os.path.exists(final)

    clock.at = MIDNIGHT + quote_files.FINAL_AFTER_SECONDS + 1
    _write(files)
    assert os.path.exists(final) and not os.path.exists(final + ".part")
    assert list(read_quotes(final)) == lines
    # One member, readable as a plain gzip file.
    members = []
    assert whole_members(final, members.append) == os.path.getsize(final) and len(members) == 1
    assert gzip.open(final, "rb").read().count(b"\n") == 21
    assert files.figures()["last_finished"] == "2026-11-18"
    _stop(files)


def test_the_same_quotes_give_the_same_file_whatever_the_writes(tmp_path):
    finals = []
    for name, write_each in (("once", False), ("each", True)):
        root = tmp_path / name
        clock = Clock(MIDNIGHT - 120)
        files = _files(root, clock)
        for second in range(60):
            _quote(files, MIDNIGHT - 100 + second, bid=25000 + second / 10, ask=25001 + second / 10)
            if write_each:
                _write(files)
        _write(files)
        clock.at = MIDNIGHT + quote_files.FINAL_AFTER_SECONDS + 1
        _write(files)
        _stop(files)
        finals.append(open(day_path(str(root), "us100", DAY), "rb").read())
    assert finals[0] == finals[1]


def test_a_feed_started_again_writes_on_without_a_second_header(tmp_path):
    clock = Clock(NOON)
    first = _files(tmp_path, clock)
    _quote(first, NOON)
    _write(first)
    _stop(first)

    second = _files(tmp_path, clock)
    _quote(second, NOON + 60)
    _write(second)
    raw = gzip.open(_part(tmp_path), "rb").read()
    assert raw.count(HEADER) == 1
    assert len(list(read_quotes(_part(tmp_path)))) == 2
    _stop(second)


@pytest.mark.parametrize("damage", ["cut", "zeros"])
def test_a_write_cut_off_or_the_zeros_of_a_power_cut_are_removed_and_the_lines_before_kept(tmp_path, damage):
    clock = Clock(NOON)
    first = _files(tmp_path, clock)
    _quote(first, NOON)
    _write(first)
    _quote(first, NOON + 60)
    _write(first)
    _stop(first)
    part = _part(tmp_path)
    whole = os.path.getsize(part)
    with open(part, "ab") as damaged:
        damaged.write(gzip.compress(b"123,,1.0,2.0\n", mtime=0)[:-6] if damage == "cut" else b"\0" * 4096)
    assert len(list(read_quotes(part))) == 2

    second = _files(tmp_path, clock)
    _quote(second, NOON + 120)
    _write(second)
    assert second.repaired > 0
    assert [line[0] for line in read_quotes(part)] == [int(NOON * 1000), int((NOON + 60) * 1000), int((NOON + 120) * 1000)]
    assert whole_members(part) == os.path.getsize(part) > whole
    _stop(second)


def test_a_write_that_fails_is_cut_back_and_its_lines_written_once_later(tmp_path, monkeypatch):
    clock = Clock(NOON)
    files = _files(tmp_path, clock)
    _quote(files, NOON)
    _write(files)
    part = _part(tmp_path)
    size = os.path.getsize(part)

    real_write = os.write
    calls = {"n": 0}

    def half_then_full_disk(descriptor, data):
        calls["n"] += 1
        if calls["n"] == 1:
            real_write(descriptor, bytes(data)[: len(data) // 2])
            raise OSError(28, "No space left on device")
        return real_write(descriptor, data)

    monkeypatch.setattr(quote_files.os, "write", half_then_full_disk)
    _quote(files, NOON + 60)
    _write(files)
    assert os.path.getsize(part) == size
    assert files.figures()["error"].startswith("OSError")
    assert files.figures()["pending_bytes"] > 0

    _write(files)
    assert [line[0] for line in read_quotes(part)] == [int(NOON * 1000), int((NOON + 60) * 1000)]
    assert files.figures()["error"] is None
    _stop(files)


def test_a_day_finished_before_the_feed_stopped_keeps_its_file_and_loses_its_part(tmp_path):
    clock = Clock(MIDNIGHT - 30)
    files = _files(tmp_path, clock)
    _quote(files, MIDNIGHT - 30)
    _write(files)
    clock.at = MIDNIGHT + quote_files.FINAL_AFTER_SECONDS + 1
    _write(files)
    _stop(files)
    final = day_path(str(tmp_path), "us100", DAY)
    finished = open(final, "rb").read()
    # Stopped between putting the day in place and removing its .part: both are there.
    with open(final + ".part", "wb") as part:
        part.write(gzip.compress(HEADER + b"1,,1.0,2.0\n", mtime=0))
    with open(final + ".final.part", "wb") as temporary:
        temporary.write(b"half")

    again = _files(tmp_path, clock)
    _write(again)
    assert open(final, "rb").read() == finished
    assert not os.path.exists(final + ".part") and not os.path.exists(final + ".final.part")
    _stop(again)


def test_a_line_of_a_day_already_finished_is_counted_late_and_the_day_left_as_it_is(tmp_path):
    clock = Clock(MIDNIGHT + quote_files.FINAL_AFTER_SECONDS + 1)
    files = _files(tmp_path, clock)
    _quote(files, MIDNIGHT - 10)
    _write(files)
    _write(files)
    final = day_path(str(tmp_path), "us100", DAY)
    finished = open(final, "rb").read()

    _quote(files, MIDNIGHT - 5)   # the clock stepped back
    _write(files)
    assert files.figures()["late"] == 1
    assert open(final, "rb").read() == finished
    _stop(files)


def test_past_its_bound_memory_drops_the_newest_lines_and_counts_them(tmp_path):
    clock = Clock(NOON)
    line = len(encode(NOON, int(NOON * 1000), 25000.5, 25001.5)[1])
    files = _files(tmp_path, clock, max_pending_bytes=line * 2)
    for second in range(5):
        _quote(files, NOON + second)
    assert files.figures()["dropped"] == 3
    _write(files)
    assert [quote[0] for quote in read_quotes(_part(tmp_path))] == [int(NOON * 1000), int((NOON + 1) * 1000)]
    _stop(files)


def test_a_quote_that_cannot_be_read_is_counted_and_never_raised(tmp_path):
    files = _files(tmp_path, Clock(NOON))
    files.take("us100", NOON, None, "not a price", 1.0)
    assert files.failed_takes == 1


def test_the_retention_removes_old_quote_files_only(tmp_path):
    old = date(2026, 9, 1)
    bars = os.path.join(tmp_path, "capital", "bars", "us100", f"{old.isoformat()}.csv.gz")
    quotes = day_path(str(tmp_path), "us100", old)
    for path in (bars, quotes):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as file:
            file.write(gzip.compress(HEADER, mtime=0))
    files = _files(tmp_path, Clock(NOON), retention_days=30)
    _quote(files, NOON)
    _write(files)
    assert os.path.exists(bars) and not os.path.exists(quotes)
    assert os.path.exists(_part(tmp_path))
    _stop(files)


def test_a_second_feed_finds_the_lock_taken_and_writes_nothing(tmp_path):
    clock = Clock(NOON)
    first = _files(tmp_path, clock)
    _quote(first, NOON)
    _write(first)

    second = _files(tmp_path, clock)
    _quote(second, NOON + 1, bid=1.0, ask=2.0)
    _write(second)
    assert second.figures()["locked_out"] is True
    assert second.figures()["dropped"] == 1
    assert [quote[2] for quote in read_quotes(_part(tmp_path))] == [25000.5]
    _stop(second)
    _stop(first)


def test_what_waits_at_the_stop_is_written(tmp_path):
    clock = Clock(NOON)
    files = _files(tmp_path, clock)
    _quote(files, NOON)
    _write(files)
    _quote(files, NOON + 1)
    _stop(files)
    assert len(list(read_quotes(_part(tmp_path)))) == 2


def test_a_feed_stopped_before_its_first_write_still_writes_what_it_received(tmp_path):
    files = _files(tmp_path, Clock(NOON))
    _quote(files, NOON)
    _stop(files)
    assert len(list(read_quotes(_part(tmp_path)))) == 1


def test_the_figures_say_what_was_written_today(tmp_path):
    clock = Clock(NOON)
    files = _files(tmp_path, clock)
    for second in range(3):
        _quote(files, NOON + second)
    assert files.figures()["pending_bytes"] > 0
    _write(files)
    figures = files.figures()
    assert figures["written_today"] == 3 and figures["pending_bytes"] == 0
    assert figures["enabled"] is True and figures["locked_out"] is False and figures["error"] is None
    assert figures["last_write_at"].startswith("2026-11-18T12:00")
    _stop(files)
