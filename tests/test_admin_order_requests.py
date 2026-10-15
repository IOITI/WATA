from datetime import datetime, timedelta, timezone

from src.admin.order_requests import MAX_RUN_ATTEMPTS, RUN_GAP, refusal_runs, requests_by_order

START = datetime(2026, 10, 14, 12, 2, 11, tzinfo=timezone.utc)
LIMIT_ONLY = "À ce stade, seuls des ordres limites sont autorisés"
_ids = iter(range(10 ** 6))


def _request(seconds, outcome="refused", operation="place", purpose="close", position_id="7779123400", **extra):
    refused = outcome == "refused"
    return {
        "request_id": f"request-{next(_ids):06d}",
        "requested_at": START + timedelta(seconds=seconds),
        "answered_at": START + timedelta(seconds=seconds, milliseconds=120),
        "recorded_by": "position_monitor",
        "operation": operation,
        "purpose": purpose,
        "position_id": position_id,
        "signal_uuid": None,
        "action": "long",
        "reason": "Performance (Stoploss (-7%) hit at -7.4%)",
        "instrument_uic": 61252187,
        "instrument_name": "TURBO LONG NASDAQ-100",
        "instrument_currency": "EUR",
        "buy_sell": "Sell",
        "amount": 6.0,
        "order_type": "Market",
        "price_seen": 1.23,
        "outcome": outcome,
        "order_id": None,
        "http_status": 400 if refused else None,
        "error_code": "OtherError" if refused else None,
        "error_message": LIMIT_ONLY if refused else None,
        **extra,
    }


def test_a_sell_refused_again_and_again_is_one_run_ended_by_the_order_that_went_through():
    refusals = [_request(second) for second in range(0, 139, 1)][:37]
    sold = _request(140, outcome="accepted", order_id="5012")

    [run] = refusal_runs([*refusals, sold])

    assert (run["count"], run["first_at"], run["last_at"]) == (37, refusals[0]["requested_at"], refusals[-1]["requested_at"])
    assert run["run_id"] == refusals[0]["request_id"]
    assert run["followed_by"] == {"order_id": "5012", "requested_at": sold["requested_at"]}
    assert run["answers"] == [{
        "outcome": "refused", "http_status": 400, "error_code": "OtherError", "error_message": LIMIT_ONLY,
        "count": 37, "first_at": refusals[0]["requested_at"], "last_at": refusals[-1]["requested_at"],
    }]
    # Shown as an order is: the same names.
    assert (run["position_id"], run["action"], run["buy_sell"], run["order_amount"], run["instrument_price"]) == (
        "7779123400", "long", "Sell", 6.0, 1.23,
    )
    assert (run["outcome"], run["http_status"], run["error_code"], run["error_message"]) == (
        "refused", 400, "OtherError", LIMIT_ONLY,
    )
    assert run["recorded_by"] == ["position_monitor"]
    assert [request["request_id"] for request in run["requests"]] == [request["request_id"] for request in refusals]


def test_a_run_still_going_has_no_order_after_it():
    [run] = refusal_runs([_request(0), _request(1)])

    assert (run["count"], run["followed_by"]) == (2, None)


def test_attempts_further_apart_than_the_gap_are_two_runs_newest_first():
    gap = RUN_GAP.total_seconds()
    morning = [_request(0), _request(1)]
    later = [_request(gap + 60)]

    runs = refusal_runs([*later, *morning])

    assert [run["count"] for run in runs] == [1, 2]
    assert runs[0]["first_at"] > runs[1]["first_at"]


def test_each_position_and_each_signal_has_runs_of_its_own():
    other_position = _request(2, position_id="7779123401")
    buy = _request(3, purpose="open", position_id=None, signal_uuid="uuid-1", buy_sell="Buy")
    lonely = _request(4, purpose="open", position_id=None)

    runs = refusal_runs([_request(0), other_position, buy, lonely, _request(5)])

    assert {(run["position_id"], run["signal_uuid"], run["count"]) for run in runs} == {
        ("7779123400", None, 2), ("7779123401", None, 1), (None, "uuid-1", 1), (None, None, 1),
    }


def test_answers_are_counted_and_the_run_is_refused_when_saxo_refused_any():
    timeout = _request(1, outcome="no_answer", error_message="ApiRequestException: timed out")

    [run] = refusal_runs([_request(0), timeout, _request(2)])

    assert [(answer["outcome"], answer["count"]) for answer in run["answers"]] == [("refused", 2), ("no_answer", 1)]
    assert run["outcome"] == "refused"
    assert refusal_runs([timeout])[0]["outcome"] == "no_answer"


def test_a_long_run_carries_its_latest_attempts_and_counts_them_all():
    attempts = [_request(second) for second in range(MAX_RUN_ATTEMPTS + 20)]

    [run] = refusal_runs(attempts)

    assert (run["count"], run["requests_total"], len(run["requests"])) == (MAX_RUN_ATTEMPTS + 20, MAX_RUN_ATTEMPTS + 20, MAX_RUN_ATTEMPTS)
    assert run["requests"][-1]["request_id"] == attempts[-1]["request_id"]
    assert run["answers"][0]["count"] == MAX_RUN_ATTEMPTS + 20


def test_resting_orders_cancelled_on_the_way_to_the_sells_are_shown_with_them():
    cancel = _request(-1, outcome="accepted", operation="cancel", order_id="stop-1", buy_sell=None)

    [run] = refusal_runs([cancel, _request(0), _request(1)])

    assert run["count"] == 2
    assert [request["operation"] for request in run["requests"]] == ["cancel", "place", "place"]


def test_orders_saxo_accepted_and_cancellations_are_not_runs():
    assert refusal_runs([_request(0, outcome="accepted", order_id="5012"), _request(1, operation="cancel")]) == []
    assert refusal_runs([{"request_id": "no time", "operation": "place", "outcome": "refused"}]) == []


def test_an_order_carries_the_request_that_placed_it_and_the_cancellations_about_it():
    resting = _request(0, outcome="accepted", operation="cancel", order_id="stop-1,tp-1", buy_sell=None)
    sold = _request(1, outcome="accepted", order_id="5012")
    orphan_buy = _request(10, outcome="accepted", purpose="open", position_id=None, order_id="5020")
    orphan_cancel = _request(25, outcome="refused", operation="cancel", purpose="open", position_id=None, order_id="5020")
    unrelated = _request(400, outcome="accepted", operation="cancel", order_id="stop-9", buy_sell=None)

    attached = requests_by_order([unrelated, orphan_cancel, orphan_buy, sold, resting], ["5012", "5020", "9999"])

    assert [request["request_id"] for request in attached["5012"]] == [resting["request_id"], sold["request_id"]]
    assert [request["request_id"] for request in attached["5020"]] == [orphan_buy["request_id"], orphan_cancel["request_id"]]
    assert "9999" not in attached
