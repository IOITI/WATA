"""A purchase Saxo refuses for funds is sent again with fewer turbos: the pure part (src/trade/funds_retry.py)."""

import pytest

from src.trade.exceptions import (
    InsufficientFundsException,
    OrderPlacementError,
    SaxoApiError,
    refused_for_funds,
)
from src.trade.funds_retry import (
    DEFAULT_PERCENT,
    DEFAULT_RETRIES,
    FundsRetry,
    funds_retry,
    next_amount,
    refusal,
    sizing_ask,
)


def test_an_order_refused_for_funds_is_sent_again_twice_at_4_percent_unless_set():
    assert (DEFAULT_PERCENT, DEFAULT_RETRIES) == (4.0, 2)
    assert funds_retry(None) == FundsRetry(4.0, 2)
    assert funds_retry({"safety_margins": {"bid_calculation": 1}}) == FundsRetry(4.0, 2)
    assert funds_retry({"safety_margins": {"funds_retry_percent": 2.5, "funds_retries": 3}}) == FundsRetry(2.5, 3)


@pytest.mark.parametrize("margins", [{"funds_retry_percent": 0}, {"funds_retries": 0}, {"funds_retry_percent": None}])
def test_zero_sends_nothing_again(margins):
    assert not funds_retry({"safety_margins": margins}).on


def test_each_order_sent_again_is_sized_further_above_the_ask():
    retry = FundsRetry(4.0, 2)

    assert [retry.margin(attempt) for attempt in (1, 2)] == [4.0, 8.0]
    assert sizing_ask(10.0, 4.0) == pytest.approx(10.4)
    assert sizing_ask(10.0, 8.0) == pytest.approx(10.8)
    # Without a margin the ask is the turbo's own, to the bit: the first order is sized as before.
    assert sizing_ask(10.1, 0.0) == 10.1


@pytest.mark.parametrize("previous, sized, expected", [
    (98, 94, 94),
    (98, 98, 97),   # never as many as the order Saxo refused
    (98, 120, 97),  # nor more
    (1, 1, 0),      # nothing smaller left: not sent again
    (5, 0, 0),
])
def test_an_order_sent_again_asks_for_fewer_turbos(previous, sized, expected):
    assert next_amount(previous, sized) == expected


@pytest.mark.parametrize("margins, said", [
    (None, None),
    ({}, None),
    ({"funds_retry_percent": 25, "funds_retries": 3}, None),
    ({"funds_retry_percent": 0, "funds_retries": 0}, None),
    ({"funds_retry_percent": 25.5}, "funds_retry_percent"),
    ({"funds_retry_percent": True}, "funds_retry_percent"),
    ({"funds_retries": 2.5}, "funds_retries"),
    ({"funds_retries": -1}, "funds_retries"),
])
def test_settings_out_of_their_bounds_are_refused(margins, said):
    answer = refusal(margins)
    assert (answer is None) if said is None else (said in answer)


@pytest.mark.parametrize("error, refused", [
    # How Saxo refuses an order: its code under ErrorInfo.
    (OrderPlacementError("API error placing order", status_code=400,
                         saxo_error_details={"ErrorInfo": {"ErrorCode": "InsufficientFunds", "Message": "No"}}), True),
    # At the top level, as the client raises it.
    (InsufficientFundsException("No", saxo_error_details={"ErrorCode": "InsufficientFunds", "Message": "No"}), True),
    # WATA's own, raised before any order: Saxo said nothing.
    (InsufficientFundsException("Insufficient funds @ 5.05", available_funds=4.0, required_price=5.05), False),
    (OrderPlacementError("Conflict", status_code=409, saxo_error_details={"Message": "Conflict"}), False),
    (SaxoApiError("Bad", status_code=400, saxo_error_details="<html>InsufficientFunds</html>"), False),
    (RuntimeError("no answer"), False),
])
def test_only_saxos_refusal_for_funds_is_one(error, refused):
    assert refused_for_funds(error) is refused
