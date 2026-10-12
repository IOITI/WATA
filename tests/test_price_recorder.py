import asyncio
import sys
import types
from dataclasses import replace
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock

import pytest

asyncpg_stub = types.ModuleType("asyncpg")
asyncpg_stub.Pool = type("Pool", (), {})
asyncpg_stub.Record = dict
asyncpg_stub.create_pool = AsyncMock()
sys.modules.setdefault("asyncpg", asyncpg_stub)

from src.price_recorder.bars import BAR_SECONDS
from src.price_recorder.instruments import TURBO, UNDERLYING, Instrument, RecordingSet
from src.price_recorder.recorder import PENDING_BUCKETS_LIMIT, PriceRecorder
from src.price_recorder.settings import RecorderSettings

# Monday 12 Oct 2026, 08:00:00 UTC: 10:00 in Paris, inside trading hours.
T0 = 1791792000
WARRANT = "WarrantOpenEndKnockOut"
SETTINGS = RecorderSettings(
    timezone="Europe/Paris", enabled=True, alert_after_seconds=300, trading_start_hour=8, trading_end_hour=22,
)


def _turbo(uic, direction="long"):
    return Instrument(uic=uic, asset_type=WARRANT, role=TURBO, indice="us100", direction=direction, barrier=24000.0 + uic)


INDEX = Instrument(uic=1909050, asset_type="StockIndex", role=UNDERLYING, indice="us100", description="NASDAQ 100")


def _rows(**bids):
    """Streamed rows: uic=bid, with an ask five cents above."""
    return [
        {"Uic": int(uic[1:]), "Quote": {"Bid": bid, "Ask": round(bid + 0.05, 4), "PriceTypeBid": "Tradable",
                                         "PriceTypeAsk": "Tradable", "MarketState": "Open"}}
        for uic, bid in bids.items()
    ]


class Clock:
    def __init__(self, at=T0):
        self.at = float(at)

    def __call__(self):
        return self.at


class History:
    def __init__(self):
        self.failing: Exception | None = None
        self.partitions = []
        self.dropped_calls = []
        self.upserts = []
        self.inserts = []
        self.coverage = []
        self.recorded_today = []
        self._next_id = 0
        self._ids = {}

    def _check(self):
        if self.failing:
            raise self.failing

    async def ensure_partitions(self, today, months_ahead=1):
        self._check()
        self.partitions.append(today)
        return []

    async def drop_partitions_older_than(self, today, months):
        self._check()
        self.dropped_calls.append((today, months))
        return ["price_bar_10s_y2020m01"]

    async def upsert_instrument(self, instrument, seen_at):
        self._check()
        self.upserts.append(instrument)
        key = (instrument["uic"], instrument["asset_type"])
        if key not in self._ids:
            self._next_id += 1
            self._ids[key] = self._next_id
        return self._ids[key]

    async def insert_bars(self, bars):
        self._check()
        self.inserts.append(list(bars))

    async def record_coverage(self, ts, feed_ok, instruments, bars):
        self._check()
        self.coverage.append((ts, feed_ok, instruments, bars))

    async def instruments_recorded_since(self, since):
        self._check()
        self.since = since
        return self.recorded_today

    def id_of(self, uic, asset_type=WARRANT):
        return self._ids[(uic, asset_type)]


class Stream:
    def __init__(self):
        self.subscriptions = []

    async def set_subscriptions(self, definitions):
        self.subscriptions.append(definitions)


class Setup:
    def __init__(self, settings=SETTINGS, turbos=(101, 102), underlyings=(INDEX,), max_instruments=150):
        self.clock = Clock()
        self.history = History()
        self.stream = Stream()
        self.messages = []
        self.found = [_turbo(uic) for uic in turbos]
        self.day = date(2026, 10, 12)
        self.recording_set = RecordingSet(max_instruments, today=lambda: self.day)
        self.recording_set.set_underlyings(list(underlyings))

        async def discover():
            if isinstance(self.found, Exception):
                raise self.found
            return list(self.found)

        async def notify(message):
            self.messages.append(message)

        self.recorder = PriceRecorder(
            settings=settings, recording_set=self.recording_set, history=self.history, stream=self.stream,
            discover=discover, notify=notify, clock=self.clock,
        )

    async def quotes(self, at, asset_type=WARRANT, **bids):
        self.clock.at = T0 + at
        await self.recorder.on_quotes({"asset_type": asset_type}, _rows(**bids), "ctx")

    async def tick(self, at):
        self.clock.at = T0 + at
        await self.recorder.write_closed_buckets()


def _run(scenario):
    setup = Setup() if not isinstance(scenario, tuple) else scenario[0]
    function = scenario if not isinstance(scenario, tuple) else scenario[1]
    asyncio.run(function(setup))
    return setup


def _ts(seconds):
    return datetime.fromtimestamp(T0 + seconds, timezone.utc)


# ── instruments ──


def test_a_refresh_subscribes_to_the_turbos_found_and_to_the_underlying():
    async def scenario(s):
        await s.recorder.refresh()

    s = _run(scenario)

    assert s.stream.subscriptions == [[
        {"reference_id": "pr-StockIndex-0", "asset_type": "StockIndex", "uics": [1909050], "field_groups": ["Quote"]},
        {"reference_id": f"pr-{WARRANT}-0", "asset_type": WARRANT, "uics": [101, 102], "field_groups": ["Quote"]},
    ]]
    assert sorted(item["uic"] for item in s.history.upserts) == [101, 102, 1909050]
    assert {item["role"] for item in s.history.upserts} == {"turbo", "underlying"}
    assert s.history.partitions == [date(2026, 10, 12)]


def test_a_search_that_breaks_keeps_what_is_already_recorded():
    async def scenario(s):
        await s.recorder.refresh()
        s.found = RuntimeError("Saxo is unreachable")
        await s.recorder.refresh()

    s = _run(scenario)

    assert s.stream.subscriptions[-1] == s.stream.subscriptions[0]


def test_nothing_is_dropped_unless_a_retention_is_set_and_then_once_a_day():
    async def twice(s):
        await s.recorder.refresh()
        await s.recorder.refresh()

    assert _run(twice).history.dropped_calls == []

    kept_18_months = Setup(settings=replace(SETTINGS, retention_months=18))
    _run((kept_18_months, twice))
    assert kept_18_months.history.dropped_calls == [(date(2026, 10, 12), 18)]


def test_after_a_restart_the_turbos_already_recorded_today_are_followed_again():
    async def scenario(s):
        s.history.recorded_today = [
            {"id": 7, "uic": 150, "asset_type": WARRANT, "role": "turbo", "indice": "us100", "direction": "long",
             "symbol": None, "description": None, "currency": None, "underlying_uic": None, "barrier": 24150.0, "issuer": "SG"},
            {"id": 3, "uic": 1909050, "asset_type": "StockIndex", "role": "underlying", "indice": "us100",
             "symbol": None, "description": None, "currency": None, "underlying_uic": None, "direction": None,
             "barrier": None, "issuer": None},
        ]
        assert await s.recorder.restore() == 1
        await s.recorder.refresh()

    s = _run(scenario)

    # 150 is no longer a candidate, but it may be the turbo of an open position.
    assert s.stream.subscriptions[-1][1]["uics"] == [101, 102, 150]
    # From the start of the day in the trading timezone: midnight in Paris is 22:00 UTC.
    assert s.history.since == datetime(2026, 10, 11, 22, 0, tzinfo=timezone.utc)


def test_at_the_change_of_day_the_turbos_of_the_day_before_are_no_longer_recorded():
    async def scenario(s):
        await s.recorder.refresh()
        await s.quotes(1, u101=8.40, u102=9.10)
        s.day = date(2026, 10, 13)
        s.found = [_turbo(103)]
        await s.recorder.refresh()
        await s.quotes(12, u103=7.00)
        await s.tick(20)

    s = _run(scenario)

    assert s.stream.subscriptions[-1][1]["uics"] == [103]
    first, second = s.history.inserts
    assert {row[1] for row in first} == {s.history.id_of(101), s.history.id_of(102)}
    assert [row[1] for row in second] == [s.history.id_of(103)]


# ── from quotes to rows ──


def test_the_stream_callback_writes_nothing_itself():
    async def scenario(s):
        await s.recorder.refresh()
        await s.quotes(1, u101=8.40)
        await s.quotes(2, u101=8.45)

    s = _run(scenario)

    assert s.history.inserts == [] and s.history.coverage == []


def test_each_ten_seconds_become_one_statement_with_one_row_per_instrument():
    async def scenario(s):
        await s.recorder.refresh()
        await s.quotes(1, u101=8.40, u102=9.10)
        await s.quotes(4, u101=8.52, u102=9.10)
        await s.quotes(7, u101=8.36, u102=9.10)
        await s.tick(10.05)

    s = _run(scenario)

    (rows,) = s.history.inserts
    by_id = {row[1]: row for row in rows}
    moved = by_id[s.history.id_of(101)]
    assert moved == (_ts(0), s.history.id_of(101), 8.40, 8.52, 8.36, 8.36, 8.45, 8.57, 8.41, 8.41, 3, 0)
    still = by_id[s.history.id_of(102)]
    assert still[2:6] == (9.10, 9.10, 9.10, 9.10) and still[10] == 1
    assert s.history.coverage == [(_ts(0), True, 2, 2)]


def test_quotes_of_instruments_that_are_not_recorded_are_ignored():
    async def scenario(s):
        await s.recorder.refresh()
        await s.quotes(1, u101=8.40, u999=1.00)
        # The same number under another asset type is another instrument.
        await s.quotes(2, asset_type="MiniFuture", u102=5.00)
        await s.tick(10.05)

    s = _run(scenario)

    assert [row[1] for row in s.history.inserts[0]] == [s.history.id_of(101)]


def test_the_underlying_is_recorded_under_its_own_asset_type():
    async def scenario(s):
        await s.recorder.refresh()
        await s.quotes(1, asset_type="StockIndex", u1909050=24150.5)
        await s.tick(10.05)

    s = _run(scenario)

    (row,) = s.history.inserts[0]
    assert row[1] == s.history.id_of(1909050, "StockIndex") and row[2] == 24150.5


# ── when the database does not answer ──


def test_bars_wait_while_the_database_is_down_and_are_written_afterwards_in_order():
    async def scenario(s):
        await s.recorder.refresh()
        await s.quotes(1, u101=8.40)
        s.history.failing = ConnectionError("connection refused")
        await s.tick(10.05)
        await s.quotes(12, u101=8.50)
        await s.tick(20.05)
        assert s.history.inserts == []
        s.history.failing = None
        await s.quotes(25, u101=8.60)
        await s.tick(30.05)

    s = _run(scenario)

    assert [rows[0][0] for rows in s.history.inserts] == [_ts(0), _ts(10), _ts(20)]
    assert [rows[0][5] for rows in s.history.inserts] == [8.40, 8.50, 8.60]
    assert [entry[0] for entry in s.history.coverage] == [_ts(0), _ts(10), _ts(20)]


def test_a_failed_write_is_tried_again_whole_so_no_bucket_is_half_written():
    async def scenario(s):
        await s.recorder.refresh()
        await s.quotes(1, u101=8.40)

        async def coverage_fails(*args):
            raise ConnectionError("lost")

        working = s.history.record_coverage
        s.history.record_coverage = coverage_fails
        await s.tick(10.05)
        s.history.record_coverage = working
        await s.tick(20.05)

    s = _run(scenario)

    # The bars of the first bucket were sent twice (the statement leaves an existing bar alone), its coverage once.
    assert [rows[0][0] for rows in s.history.inserts] == [_ts(0), _ts(0), _ts(10)]
    assert [entry[0] for entry in s.history.coverage] == [_ts(0), _ts(10)]


def test_after_an_hour_without_database_the_oldest_buckets_are_given_up(caplog):
    async def scenario(s):
        await s.recorder.refresh()
        s.history.failing = ConnectionError("down")
        for bucket in range(PENDING_BUCKETS_LIMIT + 5):
            await s.quotes(bucket * BAR_SECONDS + 1, u101=8.0 + bucket / 1000)
            await s.tick((bucket + 1) * BAR_SECONDS + 0.05)
        s.history.failing = None
        for extra in range(1, 12):
            await s.tick((PENDING_BUCKETS_LIMIT + 5 + extra) * BAR_SECONDS + 0.05)

    with caplog.at_level("ERROR"):
        s = _run(scenario)

    written = [rows[0][0] for rows in s.history.inserts if rows]
    # Five were given up while it was down, and one more by the bucket that closed as it came back.
    assert written[0] == _ts(6 * BAR_SECONDS)
    assert written == sorted(written)
    lost = [record for record in caplog.records if "bars are being lost" in record.getMessage()]
    assert len(lost) == 1, "said once, not at every bucket"


# ── being heard ──


def test_silence_during_trading_hours_is_announced_once_and_so_is_the_return():
    async def scenario(s):
        await s.recorder.refresh()
        await s.quotes(1, u101=8.40)
        await s.tick(10.05)
        # Then nothing arrives any more.
        for seconds in range(20, 420, 10):
            await s.tick(seconds + 0.05)
        assert len(s.messages) == 1
        await s.quotes(421, u101=8.10)
        await s.tick(430.05)

    s = _run(scenario)

    stopped, resumed = s.messages
    assert "nothing recorded for 5 min" in stopped and "no quote is arriving from Saxo" in stopped
    assert "cannot be fetched later" in stopped
    assert resumed == "WATA price recorder: recording again."


def test_the_message_says_when_it_is_the_database_that_does_not_take_the_bars():
    async def scenario(s):
        await s.recorder.refresh()
        s.history.failing = ConnectionError("connection refused")
        for seconds in range(0, 320, 10):
            await s.quotes(seconds + 1, u101=8.40 + seconds / 1000)
            await s.tick(seconds + 10.05)

    s = _run(scenario)

    (message,) = s.messages
    assert "the database does not take the bars (ConnectionError: connection refused)" in message
    assert "bucket(s) are waiting" in message


def test_silence_outside_trading_hours_is_not_an_incident():
    async def scenario(s):
        # Sunday 11 Oct 2026, 10:00 UTC.
        s.clock.at = T0 - 22 * 3600
        await s.recorder.refresh()
        for seconds in range(0, 1200, 10):
            s.clock.at = T0 - 22 * 3600 + seconds
            await s.recorder.write_closed_buckets()

    assert _run(scenario).messages == []


def test_the_first_minutes_of_the_day_are_not_counted_from_the_evening_before():
    async def scenario(s):
        # 07:58 in Paris: two minutes before the turbos quote.
        opening = T0 - 2 * 3600
        s.clock.at = opening - 120
        await s.recorder.refresh()
        for seconds in range(-120, 200, 10):
            s.clock.at = opening + seconds
            await s.recorder.write_closed_buckets()
        assert s.messages == []
        # Still nothing five minutes after the open: now it is one.
        for seconds in range(200, 320, 10):
            s.clock.at = opening + seconds
            await s.recorder.write_closed_buckets()

    assert len(_run(scenario).messages) == 1


def test_a_notification_that_cannot_be_sent_does_not_stop_the_recording():
    async def scenario(s):
        async def broken(message):
            raise ConnectionError("rabbitmq is down")

        s.recorder._notify = broken
        await s.recorder.refresh()
        for seconds in range(0, 330, 10):
            await s.tick(seconds + 0.05)
        await s.quotes(331, u101=8.40)
        await s.tick(340.05)

    s = _run(scenario)

    assert [rows[0][2] for rows in s.history.inserts if rows] == [8.40]


def test_an_underlying_that_saxo_does_not_quote_is_reported_once(caplog):
    async def scenario(s):
        await s.recorder.refresh()
        await s.quotes(1, u101=8.40)
        s.clock.at = T0 + 300
        await s.recorder.refresh()
        s.clock.at = T0 + 600
        await s.recorder.refresh()

    with caplog.at_level("WARNING"):
        _run(scenario)

    reports = [record for record in caplog.records if "Saxo sends no quote for" in record.getMessage()]
    assert len(reports) == 1
    assert "src.price_recorder.probe" in reports[0].getMessage() and "1909050" in reports[0].getMessage()


def test_a_quoted_underlying_is_not_reported(caplog):
    async def scenario(s):
        await s.recorder.refresh()
        await s.quotes(1, asset_type="StockIndex", u1909050=24150.5)
        s.clock.at = T0 + 300
        await s.recorder.refresh()

    with caplog.at_level("WARNING"):
        _run(scenario)

    assert "Saxo sends no quote for" not in caplog.text


# ── running ──


def test_stopping_writes_the_ten_seconds_in_progress():
    async def scenario(s):
        running = asyncio.create_task(s.recorder.run())
        await asyncio.sleep(0.05)
        await s.quotes(3, u101=8.40)
        s.recorder.stop()
        await asyncio.wait_for(running, timeout=5)

    s = _run(scenario)

    assert [rows[0][0] for rows in s.history.inserts if rows] == [_ts(0)]
    assert s.stream.subscriptions, "the instruments were looked for at start"


# ── with the real streaming client ──


def test_the_real_stream_client_feeds_the_recorder_snapshot_then_changes():
    """
    The seam that cannot be tried against Saxo from a test: the client merges
    what Saxo sends (only the fields that changed) and hands over whole rows.
    """
    from unittest.mock import MagicMock

    from src.saxo_streaming.client import SaxoStreamClient

    s = Setup(underlyings=())
    quote = {"PriceTypeBid": "Tradable", "PriceTypeAsk": "Tradable", "MarketState": "Open", "DelayedByMinutes": 0}

    async def on_quotes(definition, rows, context_id):
        await s.recorder.on_quotes(definition, rows, context_id)

    stream = SaxoStreamClient(
        api_client=MagicMock(), account_key="account", client_key="", on_positions_update=None,
        on_info_price_subscription_update=on_quotes,
    )
    stream._context_id = "ctx-1"
    stream._sync_api_token = MagicMock()
    requests = []

    async def request(endpoint):
        requests.append(endpoint)
        return {"Snapshot": {"Data": [
            {"Uic": 101, "AssetType": WARRANT, "Quote": {**quote, "Bid": 8.40, "Ask": 8.45}},
            {"Uic": 102, "AssetType": WARRANT, "Quote": {**quote, "Bid": 9.10, "Ask": 9.15}},
        ]}}

    stream._request = request
    s.recorder._stream = stream

    async def scenario():
        await s.recorder.refresh()
        # What the client does once its socket is open: create what was asked for.
        s.clock.at = T0 + 1
        (definition,) = stream._desired_info_price_subscriptions.values()
        await stream._create_info_price_subscription(definition)
        reference_id = definition["reference_id"]

        s.clock.at = T0 + 4
        await stream._handle_info_price_subscription_update(reference_id, {"Data": [{"Uic": 101, "Quote": {"Bid": 8.52}}]})
        s.clock.at = T0 + 7
        await stream._handle_info_price_subscription_update(reference_id, [{"Uic": 101, "Quote": {"Bid": 8.36, "Ask": 8.41}}])
        # A knock-out: Saxo sends the price type alone.
        s.clock.at = T0 + 12
        await stream._handle_info_price_subscription_update(
            reference_id, {"Uic": 102, "Quote": {"PriceTypeBid": "NoMarket", "PriceTypeAsk": "NoMarket"}},
        )
        await s.tick(20.05)

    asyncio.run(scenario())

    subscription = requests[0].data
    assert subscription["ReferenceId"] == f"pr-{WARRANT}-0" and subscription["ContextId"] == "ctx-1"
    assert subscription["Arguments"] == {
        "AccountKey": "account", "AssetType": WARRANT, "FieldGroups": ["Quote"], "Uics": "101,102",
    }

    first, second = s.history.inserts
    by_id = {row[1]: row for row in first}
    # 101: bid 8.40 -> 8.52 -> 8.36; the ask only moved with the last change.
    assert by_id[s.history.id_of(101)][2:11] == (8.40, 8.52, 8.36, 8.36, 8.45, 8.45, 8.41, 8.41, 3)
    assert by_id[s.history.id_of(102)][2:6] == (9.10, 9.10, 9.10, 9.10)
    # Second bucket: 102 was knocked out two seconds in. It keeps the bar of what
    # it was quoted until then, and gets no other.
    by_id = {row[1]: row for row in second}
    assert by_id[s.history.id_of(102)][2:6] == (9.10, 9.10, 9.10, 9.10) and by_id[s.history.id_of(102)][10] == 0
    assert by_id[s.history.id_of(101)][10] == 0
    assert [entry[1:] for entry in s.history.coverage] == [(True, 2, 2), (True, 1, 2)]


def test_a_refresh_that_fails_is_tried_again_soon_not_five_minutes_later():
    from src.price_recorder.recorder import REFRESH_RETRY_SECONDS

    s = Setup()
    waits = []
    attempts = []

    async def refresh():
        attempts.append(len(attempts))
        if len(attempts) == 1:
            raise ConnectionError("subscription refused")

    async def sleep_or_stop(seconds):
        waits.append(seconds)
        return len(waits) == 2

    s.recorder.refresh = refresh
    s.recorder._sleep_or_stop = sleep_or_stop

    asyncio.run(s.recorder._refresh_loop())

    assert waits == [REFRESH_RETRY_SECONDS, SETTINGS.refresh_interval_seconds]
    assert len(attempts) == 2
