import json
import os
from datetime import date, datetime, timedelta, timezone

import pytest

from src.admin import data
from src.admin.data import (
    BUILT_IN,
    DISABLED,
    DOWNLOADED,
    ENDED,
    RECORDING,
    AssetNotFound,
    DailyStatistics,
    InvalidAsset,
    PriceHistory,
    SpreadByTime,
    aggregate,
    history_label,
    history_provider_state,
    recorder_provider_state,
    saxo_asset,
    saxo_instrument_id,
    saxo_status,
)
from src.admin.health import DOWN, OK, WARNING, recorder_running, recorder_state
from src.price_recorder.bars import Bar
from src.price_recorder.settings import RecorderSettings
from src.price_history import dukascopy

UTC = timezone.utc
# Monday 12 October 2026: 08:00 in Paris is 06:00 UTC.
MONDAY = date(2026, 10, 12)
SETTINGS = RecorderSettings(timezone="Europe/Paris", trading_start_hour=8, trading_end_hour=22)


def _epoch(text: str) -> int:
    return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())


def _bar(start: int, bid: float, ask: float | None, updates: int = 2, high: float = 0.0, low: float = 0.0) -> Bar:
    return Bar(
        key="index", start=start,
        bid_open=bid, bid_high=bid + high, bid_low=bid - low, bid_close=bid,
        ask_open=ask, ask_high=None if ask is None else ask + high, ask_low=None if ask is None else ask - low,
        ask_close=ask, updates=updates, delayed_minutes=0,
    )


def write_day(root, label: str, day: date, bars: list[Bar], symbol: str = dukascopy.SYMBOL) -> str:
    """A day of bars written as the downloader writes it."""
    path = dukascopy.day_path(str(root), label, day)
    dukascopy.write_day_file(path, bars, symbol=symbol, label=label)
    return path


def a_day_of_bars(day: date, first_hour: int = 6, count: int = 6, start_price: float = 25000.0) -> list[Bar]:
    start = int(datetime(day.year, day.month, day.day, first_hour, tzinfo=UTC).timestamp())
    return [_bar(start + 10 * n, start_price + n, start_price + n + 1.5) for n in range(count)]


# ── the recorder's state, as health and the Providers page say it ──


def _heartbeat(seconds_ago: float, bars: int = 43, now: datetime | None = None) -> dict:
    now = now or datetime(2026, 10, 12, 12, tzinfo=UTC)
    return {"ts": now - timedelta(seconds=seconds_ago), "feed_ok": True, "instruments": 43, "bars": bars}


@pytest.mark.parametrize("status, expected", [
    (None, (DOWN, "Nothing recorded yet")),
    (_heartbeat(14), (OK, "Recording 43 instruments")),
    (_heartbeat(14, bars=1), (OK, "Recording 1 instrument")),
    (_heartbeat(10 + 5 * 60), (DOWN, "No sign of it for 5 min")),
    # Monday noon in Paris: trading time, so nothing recorded is a fault.
    (_heartbeat(14, bars=0), (DOWN, "Running, but no price is being recorded")),
])
def test_the_recorder_is_described_by_its_last_line_of_coverage(status, expected):
    assert recorder_state(SETTINGS, status, datetime(2026, 10, 12, 12, tzinfo=UTC)) == expected


def test_a_recorder_with_nothing_to_record_while_the_market_is_closed_is_well():
    # Sunday.
    now = datetime(2026, 10, 11, 12, tzinfo=UTC)

    assert recorder_state(SETTINGS, _heartbeat(14, bars=0, now=now), now) == (OK, "Running; the markets are closed")


def test_a_recorder_is_running_while_it_writes_a_line_a_minute_at_least():
    now = datetime(2026, 10, 12, 12, tzinfo=UTC)

    assert recorder_running(_heartbeat(70), now)
    assert not recorder_running(_heartbeat(71), now)
    assert not recorder_running(None, now)


@pytest.mark.parametrize("enabled, state, running, expected", [
    (True, (OK, "Recording 43 instruments"), True, (OK, "Recording 43 instruments")),
    (True, (DOWN, "No sign of it for 5 min"), False, (DOWN, "No sign of it for 5 min")),
    (False, (OK, "Recording 43 instruments"), True, (WARNING, "Switched off in the configuration: it records until it is restarted")),
    (False, (DOWN, "Nothing recorded yet"), False, (DISABLED, "Switched off in the configuration")),
])
def test_the_recorder_as_a_provider_says_whether_it_is_switched_on(enabled, state, running, expected):
    assert recorder_provider_state(enabled, state, running) == expected


@pytest.mark.parametrize("enabled, fetch, keep, expected", [
    (False, None, None, (DISABLED, "Switched off in the configuration (admin.backtests_enabled)")),
    (True, {"state": "running", "from": "2026-10-01", "to": "2026-10-05", "label": "de40"}, {"state": "off"},
     (OK, "Downloading 2026-10-01 to 2026-10-05 (de40)")),
    (True, {"state": "running", "from": "2026-10-01", "to": "2026-10-05"}, {"state": "fetching"},
     (OK, "Downloading 2026-10-01 to 2026-10-05 (us100)")),
    (True, {"state": "done"}, {"state": "backing_off"}, (WARNING, "Dukascopy stopped answering: the next download waits")),
    (True, {"state": "done"}, {"state": "up_to_date"}, (OK, "Kept up to date")),
    (True, {"state": "idle"}, {"state": "waiting_for_close", "missing": 2},
     (OK, "Kept up to date: 2 day(s) to fetch when the market is closed")),
    (True, {"state": "idle"}, {"state": "off"}, (OK, "Fetched on demand")),
])
def test_the_dukascopy_downloads_as_a_provider(enabled, fetch, keep, expected):
    assert history_provider_state(enabled, fetch, keep) == expected


# ── assets and their keys ──


def test_a_recorded_instrument_is_an_asset_named_by_its_provider_and_id():
    asset = saxo_asset({"id": 12, "uic": 40000012, "role": "turbo"})

    assert (asset["provider"], asset["key"], asset["ref"]) == ("saxo", "12", "saxo/12")
    assert asset["uic"] == 40000012


def test_an_instrument_is_recording_while_the_recorder_still_names_it():
    now = datetime(2026, 10, 12, 12, tzinfo=UTC)

    assert saxo_status({"last_seen_at": now - timedelta(seconds=300)}, now, 300) == RECORDING
    assert saxo_status({"last_seen_at": now - timedelta(seconds=700)}, now, 300) == ENDED
    assert saxo_status({"last_seen_at": None}, now, 300) == ENDED


@pytest.mark.parametrize("key", ["0", "-1", "12a", "1.5", "", "99999999999", "../12"])
def test_a_saxo_key_that_is_not_an_id_is_not_found_before_any_query(key):
    with pytest.raises(AssetNotFound):
        saxo_instrument_id(key)


@pytest.mark.parametrize("key", ["", "US100", "../us100", "us100/..", "-x", "a" * 33, "us 100", ".hidden"])
def test_a_dukascopy_key_that_is_not_a_label_is_not_found_before_any_path(key):
    with pytest.raises(AssetNotFound):
        history_label(key)


def test_a_label_is_taken_as_it_is():
    assert saxo_instrument_id("12") == 12
    assert history_label("de40") == "de40"


# ── the Dukascopy instruments that can be fetched ──


def test_the_nasdaq_history_is_always_there_and_first(tmp_path):
    history = PriceHistory(str(tmp_path))

    assert history.instruments() == [
        {"symbol": "USATECHIDXUSD", "label": "us100", "name": "US Tech 100", "currency": "USD", "built_in": True},
    ]
    assert history.symbol_of("us100") == "USATECHIDXUSD"
    assert history.symbol_of("de40") is None


def test_an_instrument_added_is_kept_as_a_file_beside_the_prices(tmp_path):
    history = PriceHistory(str(tmp_path))

    added = history.add_instrument("DEUIDXEUR", "de40")

    assert added == {"symbol": "DEUIDXEUR", "label": "de40", "name": "Germany 40", "currency": "EUR", "built_in": False}
    with open(tmp_path / "dukascopy" / "instruments.json", encoding="utf-8") as kept:
        assert json.load(kept) == {"instruments": [{"symbol": "DEUIDXEUR", "label": "de40"}]}
    # Read again by another service object: the file is what counts.
    assert PriceHistory(str(tmp_path)).symbol_of("de40") == "DEUIDXEUR"
    assert [entry["symbol"] for entry in history.offered()] == ["USA500IDXUSD"]


@pytest.mark.parametrize("symbol, label, reason", [
    ("EURUSD", "eurusd", "is not one of the Dukascopy instruments that can be fetched"),
    ("--root", "x", "is not one of the Dukascopy instruments that can be fetched"),
    ("USATECHIDXUSD", "nasdaq", "is already fetched"),
    # The Nasdaq's label: another instrument there would overwrite what every backtest reads.
    ("DEUIDXEUR", "us100", "is already taken"),
    ("DEUIDXEUR", "../de40", "A label is up to 32"),
    ("DEUIDXEUR", "DE40", "A label is up to 32"),
])
def test_an_instrument_that_cannot_be_fetched_rightly_is_refused(tmp_path, symbol, label, reason):
    history = PriceHistory(str(tmp_path))

    with pytest.raises(InvalidAsset, match=reason):
        history.add_instrument(symbol, label)

    assert not (tmp_path / "dukascopy" / "instruments.json").exists()


def test_two_instruments_cannot_share_a_label_or_a_symbol(tmp_path):
    history = PriceHistory(str(tmp_path))
    history.add_instrument("DEUIDXEUR", "de40")

    with pytest.raises(InvalidAsset, match="already taken"):
        history.add_instrument("USA500IDXUSD", "de40")
    with pytest.raises(InvalidAsset, match="already fetched"):
        history.add_instrument("DEUIDXEUR", "dax")


def test_a_label_holding_the_bars_of_another_instrument_is_refused(tmp_path):
    write_day(tmp_path, "de40", MONDAY, a_day_of_bars(MONDAY), symbol="USA500IDXUSD")

    with pytest.raises(InvalidAsset, match="another instrument"):
        PriceHistory(str(tmp_path)).add_instrument("DEUIDXEUR", "de40")


def test_an_instrument_removed_is_no_longer_offered_and_its_days_stay(tmp_path):
    history = PriceHistory(str(tmp_path))
    history.add_instrument("DEUIDXEUR", "de40")
    write_day(tmp_path, "de40", MONDAY, a_day_of_bars(MONDAY, start_price=24000), symbol="DEUIDXEUR")

    history.remove_instrument("de40")

    assert history.symbol_of("de40") is None
    assert history.days("de40") == [MONDAY]
    assert history.asset("de40")["symbol"] == "DEUIDXEUR"


def test_the_nasdaq_history_cannot_be_removed_nor_what_was_never_added(tmp_path):
    history = PriceHistory(str(tmp_path))

    with pytest.raises(InvalidAsset, match="backtests run on"):
        history.remove_instrument("us100")
    with pytest.raises(AssetNotFound):
        history.remove_instrument("de40")


def test_what_the_registry_holds_that_cannot_be_fetched_is_ignored(tmp_path):
    path = tmp_path / "dukascopy" / "instruments.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"instruments": [
        {"symbol": "DEUIDXEUR", "label": "de40"},
        {"symbol": "EURUSD", "label": "fx"},
        {"symbol": "USA500IDXUSD", "label": "us100"},
        {"symbol": "USA500IDXUSD", "label": "../up"},
        {"symbol": "USA500IDXUSD", "label": "de40"},
        {"symbol": "DEUIDXEUR", "label": "dax"},
        {"symbol": "USATECHIDXUSD", "label": "nasdaq"},
        "nonsense",
    ]}))

    assert [entry["label"] for entry in PriceHistory(str(tmp_path)).instruments()] == ["us100", "de40"]


def test_an_unreadable_registry_leaves_the_nasdaq_history(tmp_path):
    path = tmp_path / "dukascopy" / "instruments.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json")

    assert [entry["label"] for entry in PriceHistory(str(tmp_path)).instruments()] == ["us100"]


# ── the days fetched ──


def test_each_label_with_days_of_bars_is_an_asset(tmp_path):
    write_day(tmp_path, "us100", MONDAY, a_day_of_bars(MONDAY))
    write_day(tmp_path, "us100", MONDAY + timedelta(days=1), a_day_of_bars(MONDAY + timedelta(days=1), count=3, start_price=25100))
    (tmp_path / "dukascopy" / "bars" / "empty").mkdir()
    (tmp_path / "dukascopy" / "bars" / "Not-A-Label").mkdir()
    history = PriceHistory(str(tmp_path))

    assert history.labels() == ["us100"]
    asset = history.asset("us100")
    assert asset == {
        "id": None, "uic": None, "asset_type": "Dukascopy", "symbol": "USATECHIDXUSD",
        "description": "US Tech 100 (Dukascopy)", "currency": "USD", "role": "underlying", "indice": "us100",
        "underlying_uic": None, "direction": None, "barrier": None, "issuer": None,
        "first_t": _epoch("2026-10-12T06:00:00Z"), "last_t": _epoch("2026-10-13T06:00:20Z"),
        "last_bid": 25102.0, "last_ask": 25103.5, "days": 2, "first_day": "2026-10-12", "last_day": "2026-10-13",
        "provider": "dukascopy", "key": "us100", "ref": "dukascopy/us100", "status": DOWNLOADED,
    }
    assert history.asset("de40") is None
    assert history.size("us100") == sum(
        os.path.getsize(dukascopy.day_path(str(tmp_path), "us100", day)) for day in history.days("us100")
    )


def test_the_bars_of_a_period_are_read_from_the_days_it_covers(tmp_path):
    write_day(tmp_path, "us100", MONDAY, a_day_of_bars(MONDAY, count=6))
    history = PriceHistory(str(tmp_path))
    start = datetime(2026, 10, 12, 6, 0, 10, tzinfo=UTC)

    rows = list(history.rows("us100", start, start + timedelta(seconds=30)))

    assert rows == [
        (_epoch("2026-10-12T06:00:10Z"), 25001.0, 25001.0, 25001.0, 25001.0, 25002.5, 25002.5, 25002.5, 25002.5, 2),
        (_epoch("2026-10-12T06:00:20Z"), 25002.0, 25002.0, 25002.0, 25002.0, 25003.5, 25003.5, 25003.5, 25003.5, 2),
        (_epoch("2026-10-12T06:00:30Z"), 25003.0, 25003.0, 25003.0, 25003.0, 25004.5, 25004.5, 25004.5, 25004.5, 2),
    ]
    assert list(history.rows("de40", start, start + timedelta(days=1))) == []


def test_a_day_is_parsed_once_and_again_when_it_is_fetched_again(tmp_path, monkeypatch):
    write_day(tmp_path, "us100", MONDAY, a_day_of_bars(MONDAY, count=3))
    history = PriceHistory(str(tmp_path))
    reads = []
    real_read = data._read_day
    monkeypatch.setattr(data, "_read_day", lambda *args: reads.append(args[2]) or real_read(*args))
    day_start = datetime(2026, 10, 12, tzinfo=UTC)

    for _ in range(3):
        list(history.rows("us100", day_start, day_start + timedelta(days=1)))
    assert reads == [MONDAY]

    write_day(tmp_path, "us100", MONDAY, a_day_of_bars(MONDAY, count=5))
    assert len(list(history.rows("us100", day_start, day_start + timedelta(days=1)))) == 5
    assert reads == [MONDAY, MONDAY]


def test_no_more_days_are_kept_parsed_than_asked(tmp_path):
    days = [MONDAY + timedelta(days=offset) for offset in range(4)]
    for day in days:
        write_day(tmp_path, "us100", day, a_day_of_bars(day, count=2))
    history = PriceHistory(str(tmp_path), cache_days=2)

    list(history.rows("us100", datetime(2026, 10, 12, tzinfo=UTC), datetime(2026, 10, 16, tzinfo=UTC)))

    assert len(history._days) == 2


# ── bars of a longer interval ──

ROW = (0, 10.0, 10.5, 9.5, 10.2, 10.1, 10.6, 9.6, 10.3, 3)


def _row(t, bid, ask, updates=1, high=0.0, low=0.0):
    return (t, bid, bid + high, bid - low, bid, ask, None if ask is None else ask + high, None if ask is None else ask - low, ask, updates)


def test_ten_second_rows_are_merged_into_longer_bars_as_the_recorder_s_are_in_sql():
    start = _epoch("2026-10-12T06:00:00Z")
    rows = [
        _row(start, 10.0, None, updates=2, high=0.4),
        _row(start + 10, 10.2, 10.3, updates=1, low=0.5),
        _row(start + 20, 10.1, None, updates=0),
        _row(start + 60, 11.0, 11.1, updates=4),
    ]

    bars = aggregate(rows, 60, 100)

    assert bars == [
        # The first bar had no ask: the bucket opens on the first ask there is, and closes on the last one.
        [start, 10.0, 10.4, 9.7, 10.1, 10.3, 10.3, 9.8, 10.3, 3],
        [start + 60, 11.0, 11.0, 11.0, 11.0, 11.1, 11.1, 11.1, 11.1, 4],
    ]


def test_ten_second_rows_stay_as_they_are_at_ten_seconds():
    start = _epoch("2026-10-12T06:00:00Z")
    rows = [_row(start + 10 * n, 10.0 + n, 10.5 + n) for n in range(3)]

    assert aggregate(rows, 10, 100) == [list(row) for row in rows]


def test_merging_stops_once_the_limit_of_bars_is_made():
    start = _epoch("2026-10-12T06:00:00Z")
    rows = [_row(start + 10 * n, 10.0, 10.5) for n in range(30)]

    assert len(aggregate(rows, 60, 3)) == 3


def test_an_interval_that_is_not_offered_is_refused():
    with pytest.raises(ValueError):
        aggregate([], 45, 10)


# ── statistics ──


def _session(day: date, hours: tuple[int, int], bid: float, spread: float | None, step: int = 10) -> list[tuple]:
    """Ten-second rows from the first hour to the second (UTC), the bid rising by 0.01 a bar."""
    start = int(datetime(day.year, day.month, day.day, hours[0], tzinfo=UTC).timestamp())
    end = int(datetime(day.year, day.month, day.day, hours[1], tzinfo=UTC).timestamp())
    rows = []
    for n, t in enumerate(range(start, end, step)):
        price = round(bid + 0.01 * n, 2)
        rows.append(_row(t, price, None if spread is None else round(price + spread, 2), updates=1))
    return rows


def test_each_day_of_the_trading_timezone_is_summed_up():
    statistics = DailyStatistics("Europe/Paris", SETTINGS.is_trading_time)
    # 08:00 to 10:00 in Paris on Monday, given in two pages.
    rows = _session(MONDAY, (6, 8), 8.0, 0.05)
    statistics.add(rows[:300])
    statistics.add(rows[300:])

    (day,) = statistics.result()

    assert day["day"] == "2026-10-12"
    assert (day["first"], day["last"]) == ("2026-10-12T06:00:00Z", "2026-10-12T07:59:50Z")
    assert (day["bars"], day["updates"]) == (720, 720)
    assert (day["open"], day["low"], day["close"]) == (8.0, 8.0, 15.19)
    assert day["high"] == 15.19
    assert day["change"] == pytest.approx(7.19)
    assert day["change_percent"] == pytest.approx(89.875)
    assert day["range"] == pytest.approx(7.19)
    assert day["spread_average"] == pytest.approx(0.05)
    assert day["spread_widest"] == pytest.approx(0.05)
    assert day["without_ask"] == 0
    # Fourteen hours of trading time (08:00 to 22:00 in Paris), two of them with a bar every ten seconds.
    assert day["trading_seconds"] == 14 * 3600
    assert day["covered"] == pytest.approx(2 / 14)


def test_days_are_those_of_the_trading_timezone_not_of_utc():
    statistics = DailyStatistics("Europe/Paris", SETTINGS.is_trading_time)
    # 23:30 UTC on Monday is half past one on Tuesday in Paris.
    statistics.add(_session(MONDAY, (21, 22), 8.0, 0.05) + [_row(_epoch("2026-10-12T23:30:00Z"), 9.0, 9.05)])

    assert [day["day"] for day in statistics.result()] == ["2026-10-12", "2026-10-13"]


def test_a_day_the_clocks_change_has_its_own_length_of_trading_time():
    # 25 October 2026: Paris goes back from summer time; the hours of trading stay 08:00 to 22:00.
    statistics = DailyStatistics("Europe/Paris", lambda moment: True)
    statistics.add([_row(_epoch("2026-10-25T10:00:00Z"), 9.0, 9.05)])

    (day,) = statistics.result()

    assert day["trading_seconds"] == 25 * 3600


def test_an_instrument_quoted_with_one_price_has_no_spread():
    statistics = DailyStatistics("Europe/Paris", SETTINGS.is_trading_time, spread=False)
    statistics.add(_session(MONDAY, (6, 7), 25000.0, 0.0))

    (day,) = statistics.result()

    assert (day["spread_average"], day["spread_widest"], day["without_ask"]) == (None, None, 0)


def test_bars_without_an_ask_are_counted_and_left_out_of_the_spread():
    statistics = DailyStatistics("Europe/Paris", SETTINGS.is_trading_time)
    statistics.add(_session(MONDAY, (6, 7), 8.0, 0.04) + _session(MONDAY, (7, 8), 9.0, None))

    (day,) = statistics.result()

    assert day["without_ask"] == 360
    assert day["spread_average"] == pytest.approx(0.04)


def test_a_day_with_no_trading_time_has_no_coverage():
    # Sunday.
    statistics = DailyStatistics("Europe/Paris", SETTINGS.is_trading_time)
    statistics.add([_row(_epoch("2026-10-11T10:00:00Z"), 9.0, 9.05)])

    (day,) = statistics.result()

    assert (day["trading_seconds"], day["covered"]) == (0, None)


def test_the_spread_is_followed_through_the_day_by_half_hours_of_the_trading_timezone():
    spread = SpreadByTime("Europe/Paris")
    # 08:00 to 08:30 in Paris with a spread of 0.05, 08:30 to 09:00 with 0.10 and a bar without an ask.
    spread.add(_session(MONDAY, (6, 7), 8.0, 0.05)[:180])
    spread.add(_session(MONDAY, (6, 7), 8.0, 0.10)[180:] + [_row(_epoch("2026-10-12T06:45:05Z"), 9.0, None)])

    assert spread.result() == [
        {"slot": "08:00", "bars": 180, "spread_average": pytest.approx(0.05), "spread_widest": pytest.approx(0.05), "without_ask": 0},
        {"slot": "08:30", "bars": 181, "spread_average": pytest.approx(0.10), "spread_widest": pytest.approx(0.10), "without_ask": 1},
    ]


def test_spread_slots_can_be_longer():
    spread = SpreadByTime("Europe/Paris", minutes=60)
    spread.add(_session(MONDAY, (6, 7), 8.0, 0.05))

    assert [slot["slot"] for slot in spread.result()] == ["08:00"]


def test_statistics_of_a_dukascopy_day_read_from_its_file(tmp_path):
    write_day(tmp_path, "us100", MONDAY, a_day_of_bars(MONDAY, count=6))
    history = PriceHistory(str(tmp_path))
    statistics = DailyStatistics("Europe/Paris", SETTINGS.is_trading_time)

    statistics.add(history.rows("us100", datetime(2026, 10, 12, tzinfo=UTC), datetime(2026, 10, 13, tzinfo=UTC)))

    (day,) = statistics.result()
    assert (day["open"], day["close"], day["bars"]) == (25000.0, 25005.0, 6)
    assert day["spread_average"] == pytest.approx(1.5)


def test_the_built_in_instrument_is_the_one_backtests_read():
    assert (BUILT_IN.symbol, BUILT_IN.label) == (dukascopy.SYMBOL, dukascopy.DEFAULT_LABEL)


def test_the_index_asset_types_offered_are_those_the_recorder_looks_for(monkeypatch):
    import sys
    import types

    stub = types.ModuleType("asyncpg")
    stub.Pool, stub.Record, stub.create_pool = object, dict, None
    monkeypatch.setitem(sys.modules, "asyncpg", sys.modules.get("asyncpg", stub))
    from src.price_recorder.instruments import UNDERLYING_ASSET_TYPES

    assert data.INDEX_ASSET_TYPES == UNDERLYING_ASSET_TYPES
