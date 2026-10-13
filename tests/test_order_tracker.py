import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from src.trade.order_tracker import (
    CLOSE,
    CONFIRMED,
    ERROR,
    GAVE_UP,
    MAX_DETAIL_LENGTH,
    OPEN,
    OrderTrace,
    OrderTracker,
    track_order,
)

START = datetime(2026, 10, 13, 7, 31, 2, 120000, tzinfo=timezone.utc)


def _clock(*milliseconds):
    """Moments for the marks of a trace, in the order they are made."""
    return patch.object(
        OrderTrace, "_now", side_effect=[START + timedelta(milliseconds=offset) for offset in milliseconds],
    )


class RecordingDb:
    def __init__(self, error=None, delay=0):
        self.entries = []
        self.error = error
        self.delay = delay

    async def record(self, entry):
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        self.entries.append(entry)


class RecordingOrders:
    def __init__(self, error=None):
        self.rows = []
        self.error = error

    async def insert_turbo_order_data(self, row):
        if self.error:
            raise self.error
        self.rows.append(row)


def _accepted(order_id="order-1", purpose=OPEN, **kwargs) -> OrderTrace:
    trace = OrderTrace(purpose, **kwargs)
    trace.sent()
    trace.accepted(order_id)
    return trace


def _run(scenario, **tracker_arguments):
    """Run ``scenario(tracker)`` and wait for the tracker's background writes."""
    async def wrapper():
        db, orders = RecordingDb(), RecordingOrders()
        tracker = OrderTracker(db, orders, **tracker_arguments)
        try:
            await scenario(tracker)
        finally:
            await tracker.wait_for_pending()
        return db, orders

    return asyncio.run(wrapper())


# ── the marks of an order ──


def test_an_order_that_gave_a_position_keeps_each_moment():
    with _clock(0, 190, 520):
        trace = OrderTrace(OPEN)
        trace.sent()
        trace.accepted(5012345678)
        trace.looked()
        trace.looked()
        trace.confirmed(5098765432)

    entry = trace.as_entry("trader")

    assert (entry["order_id"], entry["position_id"]) == ("5012345678", "5098765432")
    assert entry["sent_at"] == START
    assert entry["accepted_at"] == START + timedelta(milliseconds=190)
    assert entry["confirmed_at"] == START + timedelta(milliseconds=520)
    assert (entry["purpose"], entry["outcome"], entry["lookups"]) == (OPEN, CONFIRMED, 2)
    assert (entry["gave_up_at"], entry["cancel_accepted"], entry["detail"]) == (None, None, None)
    assert entry["recorded_by"] == "trader"


def test_an_order_given_up_keeps_what_saxo_answered_to_its_cancellation():
    with _clock(0, 190, 10990):
        trace = _accepted()
        trace.gave_up(detail="SaxoApiError: order not found", cancel_accepted=False)

    entry = trace.as_entry()

    assert entry["outcome"] == GAVE_UP
    assert entry["gave_up_at"] == START + timedelta(milliseconds=10990)
    assert (entry["cancel_accepted"], entry["detail"]) == (False, "SaxoApiError: order not found")
    assert entry["confirmed_at"] is None


def test_the_first_outcome_of_an_order_is_the_one_that_counts():
    trace = _accepted()
    trace.confirmed("position-1")
    seen_at = trace.confirmed_at

    trace.failed(detail="the database went away afterwards")
    trace.gave_up()
    trace.confirmed("position-2")

    assert (trace.outcome, trace.position_id, trace.detail) == (CONFIRMED, "position-1", None)
    assert (trace.confirmed_at, trace.gave_up_at) == (seen_at, None)


def test_a_sell_order_knows_the_position_it_closes_from_the_start():
    trace = OrderTrace(CLOSE, position_id=42)
    trace.confirmed()

    assert (trace.purpose, trace.position_id, trace.outcome) == (CLOSE, "42", CONFIRMED)


def test_a_long_error_is_cut_before_it_is_kept():
    trace = _accepted()
    trace.failed(detail="x" * (MAX_DETAIL_LENGTH + 500))

    assert (trace.outcome, len(trace.detail)) == (ERROR, MAX_DETAIL_LENGTH)


# ── writing them ──


def test_tracker_writes_in_the_background_what_was_known_when_asked():
    trace = _accepted()

    async def scenario(tracker):
        tracker.record(trace)
        # Marked after the first record: the row written first must not have it.
        trace.confirmed("position-1")
        tracker.record(trace)

    db, orders = _run(scenario, recorded_by="trader")

    assert [(entry["order_id"], entry["outcome"], entry["position_id"]) for entry in db.entries] == [
        ("order-1", None, None),
        ("order-1", CONFIRMED, "position-1"),
    ]
    assert {entry["recorded_by"] for entry in db.entries} == {"trader"}
    assert orders.rows == []


def test_tracker_returns_before_anything_is_written():
    async def scenario():
        db = RecordingDb()
        tracker = OrderTracker(db)
        tracker.record(_accepted())
        written_when_returned = list(db.entries)
        await tracker.wait_for_pending()
        return written_when_returned, db.entries

    written_when_returned, entries = asyncio.run(scenario())

    assert written_when_returned == []
    assert len(entries) == 1


def test_the_order_itself_is_written_when_nothing_else_records_it():
    order_row = {"order_id": "sell-1", "buy_sell": "Sell", "position_id": "p1"}

    db, orders = _run(lambda tracker: _record(tracker, _accepted("sell-1", CLOSE, position_id="p1"), order_row))

    assert orders.rows == [order_row]
    assert [(entry["order_id"], entry["purpose"], entry["position_id"]) for entry in db.entries] == [
        ("sell-1", CLOSE, "p1")
    ]


async def _record(tracker, trace, order_row=None):
    tracker.record(trace, order_row)


def test_an_order_row_that_cannot_be_written_does_not_lose_what_became_of_the_order():
    async def scenario():
        db = RecordingDb()
        tracker = OrderTracker(db, RecordingOrders(error=ConnectionError("database is down")))
        tracker.record(_accepted(), {"order_id": "order-1"})
        await tracker.wait_for_pending()
        return db.entries

    assert [entry["order_id"] for entry in asyncio.run(scenario())] == ["order-1"]


def test_an_order_saxo_never_had_is_not_recorded():
    never_accepted = OrderTrace(OPEN)
    never_accepted.sent()

    db, orders = _run(lambda tracker: _record(tracker, never_accepted, {"order_id": None}))

    assert (db.entries, orders.rows) == ([], [])


def test_failing_database_never_reaches_the_caller():
    async def scenario():
        tracker = OrderTracker(RecordingDb(error=ConnectionError("database is down")))
        tracker.record(_accepted())
        await tracker.wait_for_pending()

    asyncio.run(scenario())


def test_slow_database_write_is_abandoned():
    async def scenario():
        db = RecordingDb(delay=10)
        tracker = OrderTracker(db)
        with patch("src.trade.order_tracker.WRITE_TIMEOUT_SECONDS", 0.01):
            tracker.record(_accepted())
            await tracker.wait_for_pending(timeout=1)
        return db

    assert asyncio.run(scenario()).entries == []


def test_recording_outside_an_event_loop_does_not_raise():
    OrderTracker(RecordingDb()).record(_accepted())


def test_recording_something_that_is_not_a_trace_does_nothing():
    db, _ = _run(lambda tracker: _record(tracker, object()))

    assert db.entries == []


def test_callers_may_run_without_a_tracker_and_never_see_it_fail():
    class BrokenTracker:
        def record(self, trace, order_row=None):
            raise RuntimeError("tracker is broken")

    track_order(None, _accepted())
    track_order(BrokenTracker(), _accepted())
