import asyncio
import logging
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from src.capital_feed.client import CapitalError, LoginRefused, MinuteCandle
from src.capital_feed.recorder import (
    DAY_FILE_MARGIN_SECONDS,
    PENDING_BUCKETS_LIMIT,
    STATUS_VALID_SECONDS,
    CapitalRecorder,
)
from src.capital_feed.settings import CapitalInstrument, CapitalSettings

# Thursday 15 Oct 2026, 08:00:00 UTC.
T0 = 1792051200
SETTINGS = CapitalSettings(
    enabled=True,
    instruments=(CapitalInstrument("US100", "us100"), CapitalInstrument("US500", "us500")),
    alert_after_seconds=300,
)
WEEK_HOURS = {
    **{day: ["00:00 - 21:59", "23:05 - 00:00"] for day in ("mon", "tue", "wed", "thu")},
    "fri": ["00:00 - 21:59"], "sat": [], "sun": ["23:05 - 00:00"], "zone": "UTC",
}


def _details(epic, status="TRADEABLE"):
    return {
        "instrument": {"epic": epic, "name": f"{epic} name", "type": "INDICES", "currency": "USD", "openingHours": WEEK_HOURS},
        "snapshot": {"marketStatus": status, "decimalPlacesFactor": 1, "bid": 25000.0, "offer": 25001.0},
    }


class Clock:
    def __init__(self, at=T0):
        self.at = float(at)

    def __call__(self):
        return self.at


class Store:
    def __init__(self):
        self.failing: Exception | None = None
        self.instruments = []
        self.inserts = []
        self.coverage = []
        self.minutes = []
        self.states = []
        self.partitions = []
        self.dropped = []
        self.first_bars = {}
        self._ids = {}

    def _check(self):
        if self.failing:
            raise self.failing

    async def upsert_instrument(self, instrument, seen_at):
        self._check()
        self.instruments.append(instrument)
        return self._ids.setdefault(instrument["epic"], len(self._ids) + 1)

    async def insert_bars(self, rows):
        self._check()
        self.inserts.append(list(rows))

    async def record_coverage(self, ts, feed_ok, instruments, bars):
        self._check()
        self.coverage.append((ts, feed_ok, instruments, bars))

    async def upsert_minutes(self, rows):
        self._check()
        self.minutes.append(list(rows))

    async def write_state(self, state):
        self._check()
        self.states.append(state)

    async def ensure_partitions(self, today, months_ahead=1):
        self._check()
        self.partitions.append(today)
        return []

    async def drop_partitions_older_than(self, today, months):
        self.dropped.append((today, months))
        return []

    def id_of(self, epic):
        return self._ids[epic]


class Rest:
    def __init__(self):
        self.details = {"US100": _details("US100"), "US500": _details("US500")}
        self.statuses = {"US100": "TRADEABLE", "US500": "TRADEABLE"}
        self.candles = {}
        self.asked_prices = []
        self.last_refusal = None
        self.failing: Exception | None = None

    async def market(self, epic):
        if self.failing:
            raise self.failing
        return self.details[epic]

    async def markets(self, epics):
        if self.failing:
            raise self.failing
        return {epic: {"epic": epic, "marketStatus": self.statuses[epic]} for epic in epics if epic in self.statuses}

    async def prices(self, epic, start, end, *, resolution="MINUTE", maximum=1000):
        self.asked_prices.append((epic, start, end, maximum))
        return list(self.candles.get(epic, []))


class Publisher:
    def __init__(self):
        self.quotes = []
        self.bars = []
        self.states = []
        self.sent = 0
        self.dropped = 0
        self.failing = False
        self.last_error = None

    def quote(self, epic, **message):
        self.quotes.append((epic, message))

    def bar(self, epic, bar):
        self.bars.append((epic, bar.start, bar.bid_close, bar.updates))

    def state(self, epic, **state):
        self.states.append((epic, state))


class Stream:
    def __init__(self):
        self.connected = True
        self.connected_since = T0
        self.connections = 1
        self.last_error = None
        self.subscribed = {"US100": "PROCESSED", "US500": "PROCESSED"}


class DayFiles:
    def __init__(self):
        self.calls = []
        self.written = 0
        self.last_written = None

    async def write_finished_days(self, instruments, written_until):
        self.calls.append((list(instruments), written_until))
        return []


class Setup:
    def __init__(self, settings=SETTINGS, secrets=()):
        self.clock = Clock()
        self.store = Store()
        self.rest = Rest()
        self.publisher = Publisher()
        self.messages = []
        self.day_files = DayFiles()

        async def notify(message):
            self.messages.append(message)

        self.recorder = CapitalRecorder(
            settings=settings, store=self.store, rest=self.rest, publisher=self.publisher, notify=notify,
            day_files=self.day_files, clock=self.clock, version="0.20.0", secrets=secrets,
        )
        self.stream = Stream()
        self.recorder.stream = self.stream

    def quote(self, at, epic="US100", bid=25000.0, ask=None, quoted_ms=None):
        self.clock.at = T0 + at
        self.recorder.on_quote(epic, bid, bid + 1.0 if ask is None else ask, quoted_ms, T0 + at)

    async def tick(self, at):
        self.clock.at = T0 + at
        await self.recorder.write_closed_buckets()


def _run(scenario, setup=None):
    setup = setup or Setup()
    asyncio.run(scenario(setup))
    return setup


def _ts(seconds):
    return datetime.fromtimestamp(T0 + seconds, timezone.utc)


# ── from quotes to rows ──


def test_the_stream_callback_writes_nothing_and_sends_each_quote_with_its_bar_at_once():
    async def scenario(s):
        s.quote(1, bid=25000.0, quoted_ms=(T0 + 1) * 1000 - 40)
        s.quote(2, bid=25002.5)

    s = _run(scenario)

    assert s.store.inserts == [] and s.store.coverage == []
    (epic, first), (_, second) = s.publisher.quotes
    assert epic == "US100" and first["quoted_ms"] == (T0 + 1) * 1000 - 40 and first["received_at"] == T0 + 1
    assert (second["bar"].bid_open, second["bar"].bid_close, second["bar"].updates) == (25000.0, 25002.5, 2)


def test_each_ten_seconds_become_one_statement_with_one_row_per_instrument_and_go_out_live_first():
    async def scenario(s):
        await s.recorder.refresh_markets()
        s.quote(1, "US100", 25000.0)
        s.quote(4, "US100", 25004.0)
        s.quote(7, "US100", 24998.0)
        s.quote(2, "US500", 7000.0)
        await s.tick(10.05)

    s = _run(scenario)

    (rows,) = s.store.inserts
    by_id = {row[1]: row for row in rows}
    assert by_id[s.store.id_of("US100")] == (
        _ts(0), s.store.id_of("US100"), 25000.0, 25004.0, 24998.0, 24998.0, 25001.0, 25005.0, 24999.0, 24999.0, 3, 0,
    )
    assert by_id[s.store.id_of("US500")][2:6] == (7000.0, 7000.0, 7000.0, 7000.0)
    assert s.store.coverage == [(_ts(0), True, 2, 2)]
    assert sorted(s.publisher.bars) == [("US100", T0, 24998.0, 3), ("US500", T0, 7000.0, 1)]


def test_quotes_of_an_instrument_that_is_not_followed_are_ignored():
    async def scenario(s):
        s.quote(1, "DE40", 19000.0)
        await s.tick(10.05)

    s = _run(scenario)

    assert s.publisher.quotes == [] and s.store.inserts == []


def test_an_instrument_quoting_before_its_description_could_be_read_is_recorded_under_its_epic():
    async def scenario(s):
        s.quote(1, "US100", 25000.0)
        await s.tick(10.05)
        await s.recorder.refresh_markets()

    s = _run(scenario)

    first, *described = s.store.instruments
    assert first == {"epic": "US100", "label": "us100"}
    # Its description is read all the same, and kept under the same id.
    us100 = next(item for item in described if item["epic"] == "US100")
    assert us100["name"] == "US100 name" and us100["decimals"] == 1 and us100["opening_hours"] == WEEK_HOURS
    assert s.store.id_of("US100") == 1


# ── markets ──


def test_a_market_that_closes_ends_its_bars_and_the_chart_is_told():
    async def scenario(s):
        await s.recorder.refresh_markets()
        s.quote(1, "US100", 25000.0)
        s.quote(2, "US500", 7000.0)
        await s.tick(10.05)
        s.rest.statuses["US100"] = "CLOSED"
        s.clock.at = T0 + 15
        await s.recorder.refresh_markets()
        s.quote(25, "US500", 7001.0)
        await s.tick(30.05)

    s = _run(scenario)

    # The bucket it closed in keeps what US100 did, then nothing more of it: only US500 stands.
    later = [[row[1] for row in rows] for rows in s.store.inserts[1:]]
    assert later and all(ids == [s.store.id_of("US500")] for ids in later[1:])
    assert ("US100", {"market": "closed", "status": "CLOSED"}) in s.publisher.states


def test_whether_a_market_is_open_is_its_state_while_fresh_then_its_hours():
    async def scenario(s):
        await s.recorder.refresh_markets()
        s.rest.statuses["US100"] = "CLOSED"
        await s.recorder.refresh_markets()
        assert s.recorder.market_open("US100") is False
        # Its state is no longer known after a while: the hours (open on a Thursday at 08:00) say.
        assert s.recorder.market_open("US100", T0 + STATUS_VALID_SECONDS + 1) is True
        # 22:30 on that Thursday is in the daily break.
        assert s.recorder.market_open("US100", T0 + 14.5 * 3600) is False

    _run(scenario)


def test_silence_is_expected_only_when_every_market_followed_is_closed():
    async def scenario(s):
        # Nothing known yet: a silence would be a fault.
        assert s.recorder.expects_quotes() is True
        await s.recorder.refresh_markets()
        s.rest.statuses = {"US100": "CLOSED", "US500": "TRADEABLE"}
        await s.recorder.refresh_markets()
        assert s.recorder.expects_quotes() is True
        s.rest.statuses = {"US100": "CLOSED", "US500": "CLOSED"}
        await s.recorder.refresh_markets()
        assert s.recorder.expects_quotes() is False

    _run(scenario)


# ── when the database does not answer ──


def test_buckets_wait_while_the_database_is_down_and_are_written_once_it_answers():
    async def scenario(s):
        await s.recorder.refresh_markets()
        s.store.failing = ConnectionError("database down")
        for second in range(1, 31, 3):
            s.quote(second, "US100", 25000.0 + second)
        await s.tick(10.05)
        await s.tick(20.05)
        await s.tick(30.05)
        assert s.store.inserts == []
        # Live, though, the bars went out as they closed.
        assert len([bar for bar in s.publisher.bars if bar[0] == "US100"]) == 3
        s.store.failing = None
        await s.tick(40.05)

    s = _run(scenario)

    written = [row[0] for rows in s.store.inserts for row in rows if row[1] == s.store.id_of("US100")]
    assert written[:3] == [_ts(0), _ts(10), _ts(20)]


def test_after_an_hour_without_the_database_the_oldest_buckets_go_and_it_is_said_once(caplog):
    async def scenario(s):
        await s.recorder.refresh_markets()
        s.store.failing = ConnectionError("database down")
        for bucket in range(PENDING_BUCKETS_LIMIT + 3):
            s.quote(bucket * 10 + 1, "US100", 25000.0 + bucket)
            await s.tick(bucket * 10 + 10.05)

    with caplog.at_level(logging.ERROR):
        s = _run(scenario)

    assert len(s.recorder._pending) == PENDING_BUCKETS_LIMIT
    assert sum("bars are being lost" in record.getMessage() for record in caplog.records) == 1


# ── being heard ──


def test_silence_while_a_market_is_open_is_said_once_and_its_end_too():
    async def scenario(s):
        await s.recorder.refresh_markets()
        s.stream.connected = False
        s.stream.last_error = "the stream was closed"
        await s.tick(10.05)
        await s.tick(310.05)
        await s.tick(320.05)
        s.stream.connected = True
        s.quote(321, "US100", 25000.0)
        await s.tick(330.05)

    s = _run(scenario)

    assert len(s.messages) == 2
    assert "nothing recorded for 5 min" in s.messages[0] and "the stream is not connected (the stream was closed)" in s.messages[0]
    assert s.messages[1] == "WATA Capital.com feed: recording again."


def test_silence_while_every_market_is_closed_is_not_an_incident():
    async def scenario(s):
        s.rest.statuses = {"US100": "CLOSED", "US500": "CLOSED"}
        for minute in range(1, 12):
            # As the feed does: the state of the markets asked each minute.
            await s.recorder.refresh_markets()
            await s.tick(minute * 60 + 0.05)

    assert _run(scenario).messages == []


def test_a_refused_login_is_said_once_with_what_to_check():
    async def scenario(s):
        s.rest.last_refusal = "error.invalid.details"
        await s.tick(10.05)
        await s.tick(20.05)

    s = _run(scenario)

    (message,) = [m for m in s.messages if "refused" in m]
    assert "error.invalid.details" in message and "CAPITAL_API_KEY" in message


# ── what the feed is doing ──


def test_the_state_says_what_the_feed_does_with_the_figures_of_the_last_minute():
    async def scenario(s):
        await s.recorder.refresh_markets()
        for second in range(1, 50):
            s.quote(second, "US100", 25000.0 + second % 3, quoted_ms=(T0 + second) * 1000 - (40 if second % 10 else 400))
        await s.tick(50.05)

    s = _run(scenario)

    state = s.store.states[-1]
    assert (state["state"], state["environment"], state["version"]) == ("streaming", "demo", "0.20.0")
    figures = state["figures"]
    assert figures["quotes_per_minute"] == 49
    assert figures["delay_ms"]["p50"] == pytest.approx(40, abs=0.5) and figures["delay_ms"]["max"] == pytest.approx(400, abs=0.5)
    us100 = figures["instruments"][0]
    assert (us100["epic"], us100["market"], us100["status"], us100["subscription"]) == ("US100", "open", "TRADEABLE", "PROCESSED")
    assert figures["last_bar_at"] == "2026-10-15T08:00:50Z"


def test_a_closed_market_says_when_it_opens_again():
    async def scenario(s):
        s.clock.at = T0 + 14.5 * 3600  # 22:30
        s.rest.statuses = {"US100": "CLOSED", "US500": "CLOSED"}
        await s.recorder.refresh_markets()

    s = _run(scenario)

    state = s.recorder.state()
    assert state["state"] == "market_closed"
    assert state["figures"]["instruments"][0]["next_opening"] == "2026-10-15T23:05:00Z"


def test_the_key_and_the_password_never_reach_the_state_or_telegram():
    setup = Setup(secrets=("key-SECRET", "pw-SECRET"))

    async def scenario(s):
        s.stream.connected = False
        s.stream.last_error = "server said key-SECRET was pw-SECRET"
        await s.tick(10.05)
        await s.tick(400.05)

    s = _run(scenario, setup)

    everything = repr(s.store.states) + "".join(s.messages)
    assert "key-SECRET" not in everything and "pw-SECRET" not in everything
    assert "server said *** was ***" in everything


# ── Capital.com's minutes ──


def test_the_last_minutes_are_asked_for_again_until_they_settle_and_the_one_in_progress_is_left_out():
    async def scenario(s):
        await s.recorder.refresh_markets()
        s.clock.at = T0 + 600 + 5  # 08:10:05
        minute = datetime(2026, 10, 15, 8, 9, tzinfo=timezone.utc)
        s.rest.candles["US100"] = [
            MinuteCandle(minute - timedelta(minutes=1), 1, 2, 0.5, 1.5, 1.1, 2.1, 0.6, 1.6, 168.0),
            MinuteCandle(minute, 1.5, 2.5, 1.0, 2.0, 1.6, 2.6, 1.1, 2.1, 187.0),
            MinuteCandle(minute + timedelta(minutes=1), 2.0, 2.0, 2.0, 2.0, 2.1, 2.1, 2.1, 2.1, 3.0),
        ]
        assert await s.recorder.fetch_minutes() == 2

    s = _run(scenario)

    epic, start, end, maximum = s.rest.asked_prices[0]
    assert (epic, start, end) == ("US100", datetime(2026, 10, 15, 8, 0, tzinfo=timezone.utc), datetime(2026, 10, 15, 8, 9, 59, tzinfo=timezone.utc))
    (rows,) = s.store.minutes
    assert [(row[0], row[1].minute, row[10]) for row in rows] == [(s.store.id_of("US100"), 8, 168.0), (s.store.id_of("US100"), 9, 187.0)]


# ── upkeep ──


def test_upkeep_makes_partitions_and_writes_the_days_that_are_safely_over():
    async def scenario(s):
        await s.recorder.refresh_markets()
        s.clock.at = T0 + 30.0
        await s.recorder.upkeep()

    s = _run(scenario)

    assert s.store.partitions == [date(2026, 10, 15)]
    assert s.store.dropped == []
    (instruments, written_until), = s.day_files.calls
    assert instruments == [(1, "US100", "us100", "US100 name"), (2, "US500", "us500", "US500 name")]
    assert written_until == _ts(30) - timedelta(seconds=DAY_FILE_MARGIN_SECONDS)


def test_while_buckets_wait_for_the_database_no_day_after_them_is_written():
    async def scenario(s):
        await s.recorder.refresh_markets()
        s.store.failing = ConnectionError("database down")
        s.quote(1, "US100", 25000.0)
        await s.tick(10.05)
        s.store.failing = None
        assert s.recorder.written_until() == _ts(0)

    _run(scenario)


def test_a_retention_drops_old_months_once_a_day():
    setup = Setup(settings=replace(SETTINGS, retention_months=6))

    async def scenario(s):
        await s.recorder.upkeep()
        await s.recorder.upkeep()

    assert _run(scenario, setup).store.dropped == [(date(2026, 10, 15), 6)]


# ── running ──


def test_a_run_makes_the_partitions_before_any_bar_and_writes_the_bucket_in_progress_when_stopped():
    async def scenario(s):
        s.recorder._settings = replace(SETTINGS, minute_volume=False)
        running = asyncio.create_task(s.recorder.run())
        await asyncio.sleep(0.05)
        assert s.store.partitions[:1] == [date(2026, 10, 15)]
        s.quote(1, "US100", 25000.0)
        s.recorder.stop()
        await asyncio.wait_for(running, 3)

    s = _run(scenario)

    assert [row[0] for rows in s.store.inserts for row in rows] == [_ts(0)]


def test_a_refused_login_or_a_failing_capital_com_does_not_end_any_loop():
    async def scenario(s):
        s.rest.failing = LoginRefused("refused", code="error.invalid.details")
        s.recorder._settings = replace(SETTINGS, minute_volume=False)
        running = asyncio.create_task(s.recorder.run())
        await asyncio.sleep(0.05)
        s.rest.failing = CapitalError("Capital.com answered 500 for GET /api/v1/markets", status=500)
        await asyncio.sleep(0.05)
        assert not running.done()
        s.recorder.stop()
        await asyncio.wait_for(running, 3)

    _run(scenario)
