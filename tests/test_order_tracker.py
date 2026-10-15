import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx

from src.saxo_openapi.exceptions import OpenAPIError
from src.trade.exceptions import ApiRequestException, OrderPlacementError, SaxoApiError, saxo_error_parts
from src.trade.order_tracker import (
    ACCEPTED,
    CANCEL,
    CLOSE,
    CONFIRMED,
    ERROR,
    GAVE_UP,
    MAX_DETAIL_LENGTH,
    NO_ANSWER,
    OPEN,
    PLACE,
    REFUSED,
    OrderContext,
    OrderRequest,
    OrderTrace,
    OrderTracker,
    describe_failure,
    request_accepted,
    request_failed,
    start_request,
    track_order,
    track_request,
    without_account_keys,
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


# ── Saxo's answer, wherever Saxo put it ──

# What Saxo answered on 2026-10-14, refusing a market sell in a volatile market.
LIMIT_ONLY = {"ErrorInfo": {"ErrorCode": "OtherError", "Message": "À ce stade, seuls des ordres limites sont autorisés"}}


def test_saxo_puts_the_refusal_of_an_order_under_error_info():
    assert saxo_error_parts(LIMIT_ONLY) == ("OtherError", "À ce stade, seuls des ordres limites sont autorisés")


def test_other_refusals_say_it_at_the_top_level():
    assert saxo_error_parts({"ErrorCode": "InsufficientFunds", "Message": "Not enough cash"}) == (
        "InsufficientFunds", "Not enough cash",
    )


def test_the_fields_saxo_did_not_accept_are_part_of_the_message():
    details = {
        "ErrorCode": "InvalidModelState",
        "Message": "One or more properties of the request are invalid!",
        "ModelState": {"Amount": ["Amount must be positive"], "Uic": "Unknown instrument"},
    }

    assert saxo_error_parts(details) == (
        "InvalidModelState",
        "One or more properties of the request are invalid! (Amount: Amount must be positive; Uic: Unknown instrument)",
    )


def test_an_answer_that_is_not_json_is_its_own_message():
    assert saxo_error_parts("  Service Unavailable ") == (None, "Service Unavailable")
    assert saxo_error_parts("") == (None, None)
    assert saxo_error_parts(None) == (None, None)
    assert saxo_error_parts({"Unexpected": True}) == (None, None)


def test_the_error_s_text_says_saxo_s_answer_once_with_its_code():
    error = OrderPlacementError(
        "API error placing order",
        status_code=400,
        saxo_error_details=LIMIT_ONLY,
        order_details={"Uic": 61252187, "Amount": 6.0, "BuySell": "Sell", "AccountKey": "secret"},
    )

    assert str(error) == (
        "API error placing order (Status: 400) "
        "(Saxo: À ce stade, seuls des ordres limites sont autorisés [OtherError]) "
        "(Order: Sell 6.0 units of 61252187)"
    )


def test_an_answer_in_a_shape_saxo_does_not_document_is_shown_as_it_came():
    assert str(SaxoApiError("Saxo API Error (500)", saxo_error_details={"Odd": 1})) == (
        "Saxo API Error (500) (Saxo: {'Odd': 1})"
    )


# ── a request to Saxo and its answer ──


def _refusal(status=400, body=None, reason="Bad Request"):
    """What the services see when Saxo refuses: the client's own error, raised from Saxo's answer."""
    content = json.dumps(LIMIT_ONLY if body is None else body)
    try:
        try:
            raise OpenAPIError(status, reason, content)
        except OpenAPIError as answer:
            raise SaxoApiError(f"Saxo API Error ({status})", status_code=status, saxo_error_details=json.loads(content)) from answer
    except SaxoApiError as error:
        return error


def test_a_refusal_keeps_saxo_s_status_code_message_and_answer():
    assert describe_failure(_refusal()) == {
        "outcome": REFUSED,
        "http_status": 400,
        "error_code": "OtherError",
        "error_message": "À ce stade, seuls des ordres limites sont autorisés",
        "response_body": json.dumps(LIMIT_ONLY),
    }


def test_a_refusal_wrapped_again_is_still_read_from_saxo_s_answer():
    try:
        try:
            raise _refusal(status=409, body={"Message": "Duplicate operation"})
        except SaxoApiError as error:
            raise OrderPlacementError("API error placing order") from error
    except OrderPlacementError as wrapped:
        described = describe_failure(wrapped)

    assert (described["outcome"], described["http_status"], described["error_message"]) == (
        REFUSED, 409, "Duplicate operation",
    )


def test_a_refusal_without_a_message_says_the_http_reason():
    try:
        raise OpenAPIError(503, "Service Unavailable", "")
    except OpenAPIError as answer:
        described = describe_failure(answer)

    assert (described["outcome"], described["http_status"], described["error_message"]) == (
        REFUSED, 503, "Service Unavailable",
    )


def test_no_answer_at_all_may_mean_saxo_took_the_order():
    try:
        try:
            raise httpx.ReadTimeout("timed out")
        except httpx.ReadTimeout as timeout:
            raise ApiRequestException("Unexpected request error: timed out") from timeout
    except ApiRequestException as error:
        described = describe_failure(error)

    assert (described["outcome"], described["http_status"]) == (NO_ANSWER, None)
    assert described["error_message"] == "ApiRequestException: Unexpected request error: timed out"


def test_a_request_stopped_in_flight_is_a_request_without_answer():
    described = describe_failure(asyncio.CancelledError())

    assert described["outcome"] == NO_ANSWER
    assert described["error_message"] == "Stopped before Saxo answered: the service was stopping"


def test_anything_else_is_an_error():
    described = describe_failure(OrderPlacementError("Response missing OrderId."))

    assert (described["outcome"], described["error_message"]) == (ERROR, "OrderPlacementError: Response missing OrderId.")


def test_the_account_s_keys_are_never_kept():
    body = {"AccountKey": "a", "Orders": [{"Uic": 1, "ClientKey": "c"}], "PositionId": "p1"}

    assert without_account_keys(body) == {"Orders": [{"Uic": 1}], "PositionId": "p1"}


def _sell_request(**context):
    body = {
        "Uic": 61252187, "AssetType": "WarrantOpenEndKnockOut", "Amount": 6.0, "BuySell": "Sell",
        "OrderType": "Market", "OrderDuration": {"DurationType": "DayOrder"}, "AccountKey": "secret",
    }
    return OrderRequest(PLACE, body, OrderContext(purpose=CLOSE, position_id="7779123400", **context))


def test_a_refused_sell_is_kept_with_what_was_sent_and_why():
    request = _sell_request(action="long", reason="Performance (Stoploss (-7%) hit at -7.4%)", price_seen=1.23)
    request.failed(_refusal())

    entry = request.as_entry("position_monitor")

    assert entry["recorded_by"] == "position_monitor"
    assert (entry["operation"], entry["purpose"], entry["position_id"], entry["action"]) == (
        PLACE, CLOSE, "7779123400", "long",
    )
    assert entry["reason"] == "Performance (Stoploss (-7%) hit at -7.4%)"
    assert (entry["instrument_uic"], entry["buy_sell"], entry["amount"], entry["order_type"], entry["price_seen"]) == (
        61252187, "Sell", 6.0, "Market", 1.23,
    )
    assert (entry["outcome"], entry["http_status"], entry["error_code"], entry["order_id"]) == (
        REFUSED, 400, "OtherError", None,
    )
    assert entry["error_message"] == "À ce stade, seuls des ordres limites sont autorisés"
    assert "secret" not in entry["request_body"] and json.loads(entry["request_body"])["Uic"] == 61252187
    assert entry["requested_at"] <= entry["answered_at"]
    assert len(entry["request_id"]) == 36


def test_an_accepted_order_keeps_the_id_saxo_gave_it():
    request = _sell_request()
    request.accepted({"OrderId": "5012"}, order_id="5012")

    entry = request.as_entry()

    assert (entry["outcome"], entry["order_id"], entry["response_body"]) == (ACCEPTED, "5012", '{"OrderId": "5012"}')


def test_a_cancellation_names_the_orders_it_cancels():
    request = OrderRequest(CANCEL, {"OrderIds": "stop-1,tp-1"}, OrderContext(purpose=CLOSE, position_id="p1"), order_ids="stop-1,tp-1")
    request.accepted()

    entry = request.as_entry()

    assert (entry["operation"], entry["order_id"], entry["buy_sell"], entry["outcome"]) == (CANCEL, "stop-1,tp-1", None, ACCEPTED)


def test_a_protective_stop_is_read_from_the_order_inside_the_request():
    body = {"PositionId": "p1", "Orders": [{"Uic": 7, "BuySell": "Sell", "Amount": 98, "OrderType": "StopIfTraded"}]}

    entry = OrderRequest(PLACE, body).as_entry()

    assert (entry["position_id"], entry["instrument_uic"], entry["amount"], entry["order_type"]) == ("p1", 7, 98.0, "StopIfTraded")


def test_what_cannot_be_a_number_is_left_empty_rather_than_lose_the_row():
    entry = OrderRequest(PLACE, {"Uic": "not a number", "Amount": None}, OrderContext(price_seen="?")).as_entry()

    assert (entry["instrument_uic"], entry["amount"], entry["price_seen"]) == (None, None, None)


def test_a_long_answer_is_cut():
    request = OrderRequest(PLACE, {})
    request.failed(_refusal(body={"Message": "x" * 5000}))

    assert len(request.response_body) == MAX_DETAIL_LENGTH


class RecordingRequests:
    def __init__(self, error=None):
        self.entries = []
        self.error = error

    async def record(self, entry):
        if self.error:
            raise self.error
        self.entries.append(entry)


def test_requests_are_written_in_the_background_with_the_service_s_name():
    async def scenario():
        requests = RecordingRequests()
        tracker = OrderTracker(RecordingDb(), db_request_manager=requests, recorded_by="trader")
        request = _sell_request()
        request_failed(tracker, request, _refusal())
        written_when_returned = list(requests.entries)
        await tracker.wait_for_pending()
        return written_when_returned, requests.entries

    written_when_returned, entries = asyncio.run(scenario())

    assert written_when_returned == []
    assert [(entry["outcome"], entry["recorded_by"]) for entry in entries] == [(REFUSED, "trader")]


def test_a_request_that_cannot_be_written_never_reaches_the_caller():
    async def scenario():
        tracker = OrderTracker(RecordingDb(), db_request_manager=RecordingRequests(error=ConnectionError("down")))
        request_accepted(tracker, start_request(PLACE, {"Uic": 1}), {"OrderId": "1"}, order_id="1")
        await tracker.wait_for_pending()

    asyncio.run(scenario())


def test_requests_are_not_kept_by_a_tracker_without_their_table_nor_outside_an_event_loop():
    db, requests = RecordingDb(), RecordingRequests()

    request_failed(OrderTracker(db), _sell_request(), _refusal())
    request_failed(OrderTracker(db, db_request_manager=requests), _sell_request(), _refusal())

    assert (db.entries, requests.entries) == ([], [])


def test_a_request_is_kept_whatever_fails_around_it():
    class BrokenTracker:
        def record_request(self, request):
            raise RuntimeError("tracker is broken")

    class Unwritable(OrderRequest):
        def failed(self, error, response=None):
            raise RuntimeError("cannot describe it")

    track_request(None, _sell_request())
    track_request(BrokenTracker(), _sell_request())
    request_failed(BrokenTracker(), Unwritable(PLACE), _refusal())
    request_accepted(None, None)
    request_failed(None, None, _refusal())
