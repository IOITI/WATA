"""
The async Saxo client against Saxo's rate limits: a refusal (429) is waited as
long as the limit that ran out says, and orders leave a second apart.
"""

import asyncio

import httpx
import pytest

import src.saxo_openapi.async_client as async_client
from src.saxo_openapi.async_client import (
    ORDER_SPACING_SECONDS,
    AsyncAPI,
    OrderPacer,
    is_order_request,
    wait_after_refusal,
)
from src.saxo_openapi.endpoints.portfolio.orders import GetOpenOrdersMe
from src.saxo_openapi.endpoints.trading.orders import CancelOrders, Order
from src.saxo_openapi.exceptions import OpenAPIError


class FakeTime:
    """A clock that moves only when the client sleeps, with every sleep recorded."""

    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture
def fake_time(monkeypatch):
    time = FakeTime()
    monkeypatch.setattr(async_client.asyncio, "sleep", time.sleep)
    return time


def _api(handler, fake_time: FakeTime) -> AsyncAPI:
    api = AsyncAPI(access_token="t" * 20)
    api._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    api.order_pacer = OrderPacer(clock=fake_time.clock)
    return api


ORDER_LIMIT_REFUSAL = {
    # As Saxo's documentation shows a refusal for the order limit: no Session-* header.
    "X-RateLimit-AppDay-Limit": "10000000",
    "X-RateLimit-AppDay-Remaining": "9999123",
    "X-RateLimit-AppDay-Reset": "82332",
    "X-RateLimit-SessionOrders-Limit": "1",
    "X-RateLimit-SessionOrders-Remaining": "0",
    "X-RateLimit-SessionOrders-Reset": "1",
}


@pytest.mark.parametrize(
    "headers, ordering, wait",
    [
        (ORDER_LIMIT_REFUSAL, True, 1.0),
        ({**ORDER_LIMIT_REFUSAL, "X-RateLimit-SessionOrders-Reset": "0"}, True, 1.0),
        ({"X-RateLimit-SessionOrders-Remaining": "0"}, True, 1.0),
        ({"X-RateLimit-Session-Remaining": "0", "X-RateLimit-Session-Reset": "37"}, False, 37.0),
        ({"X-RateLimit-Session-Remaining": "0", "X-RateLimit-Session-Reset": "37"}, True, 37.0),
        ({"X-RateLimit-AppDay-Remaining": "0", "X-RateLimit-AppDay-Reset": "82332"}, False, None),
        ({"X-RateLimit-SessionOrders-Reset": "2"}, True, 2.0),
        ({"X-RateLimit-Session-Reset": "12"}, False, 12.0),
        ({}, True, 1.0),
        ({}, False, 60.0),
        ({"X-RateLimit-SessionOrders-Remaining": "x"}, True, 1.0),
    ],
)
def test_a_refusal_is_waited_as_long_as_the_limit_that_ran_out_says(headers, ordering, wait):
    assert wait_after_refusal(httpx.Headers(headers), ordering) == wait


def test_placing_changing_and_cancelling_an_order_count_as_orders():
    assert is_order_request("POST", "openapi/trade/v2/orders")
    assert is_order_request("PATCH", "openapi/trade/v2/orders")
    assert is_order_request("DELETE", "openapi/trade/v2/orders/123,456")
    assert is_order_request("post", "openapi/trade/v1/orders")
    assert not is_order_request("POST", "openapi/trade/v2/orders/precheck")
    assert not is_order_request("GET", "openapi/trade/v2/orders")
    assert not is_order_request("GET", "openapi/port/v1/orders/me/")
    assert not is_order_request("POST", "openapi/trade/v1/infoprices/list")


def test_an_order_refused_for_the_order_limit_is_asked_again_a_second_later_with_its_request_id(fake_time):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("x-request-id"))
        if len(seen) == 1:
            return httpx.Response(429, headers=ORDER_LIMIT_REFUSAL, text="{}")
        return httpx.Response(200, json={"OrderId": "77"})

    api = _api(handler, fake_time)
    order = Order(data={"Uic": 1, "Amount": 3})
    order.HEADERS = {"x-request-id": "req-1"}

    answer = asyncio.run(api.request(order))

    assert answer == {"OrderId": "77"}
    assert seen == ["req-1", "req-1"]
    # Until v0.48.0 this sale waited 60 s: the refusal carries no Session-Reset.
    assert sum(fake_time.slept) == pytest.approx(ORDER_SPACING_SECONDS)
    assert fake_time.slept[0] == 1.0


def test_a_refusal_for_the_apps_day_is_not_waited_for(fake_time):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(429, headers={"X-RateLimit-AppDay-Remaining": "0", "X-RateLimit-AppDay-Reset": "82332"}, text="{}")

    api = _api(handler, fake_time)
    with pytest.raises(OpenAPIError) as raised:
        asyncio.run(api.request(GetOpenOrdersMe()))

    assert raised.value.code == 429
    assert len(calls) == 1
    assert fake_time.slept == []


def test_another_request_refused_waits_the_sessions_reset_and_takes_no_turn_among_orders(fake_time):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"X-RateLimit-Session-Remaining": "0", "X-RateLimit-Session-Reset": "5"}, text="{}")
        return httpx.Response(200, json={"Data": []})

    api = _api(handler, fake_time)
    assert asyncio.run(api.request(GetOpenOrdersMe())) == {"Data": []}
    assert fake_time.slept == [5.0]
    assert api.order_pacer._last is None


def test_orders_leave_a_little_over_a_second_apart(fake_time):
    pacer = OrderPacer(clock=fake_time.clock)

    async def three_orders():
        first = await pacer.wait_turn()
        fake_time.now += 0.3
        second = await pacer.wait_turn()
        fake_time.now += 2.0
        third = await pacer.wait_turn()
        return first, second, third

    first, second, third = asyncio.run(three_orders())
    assert first == 0.0
    assert second == pytest.approx(ORDER_SPACING_SECONDS - 0.3)
    assert third == 0.0
    assert fake_time.slept == [pytest.approx(ORDER_SPACING_SECONDS - 0.3)]


def test_two_orders_sent_together_go_one_after_the_other(fake_time):
    sent_at = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent_at.append(fake_time.now)
        return httpx.Response(200, json={"OrderId": str(len(sent_at))})

    api = _api(handler, fake_time)

    async def a_sale_and_a_cancel():
        return await asyncio.gather(
            api.request(Order(data={"Uic": 1, "Amount": -3})),
            api.request(CancelOrders(OrderIds="9", params={"AccountKey": "k"})),
        )

    answers = asyncio.run(a_sale_and_a_cancel())
    assert len(answers) == 2
    # The second waited its turn instead of being refused by Saxo.
    assert sent_at[1] - sent_at[0] == pytest.approx(ORDER_SPACING_SECONDS)


def test_requests_that_are_not_orders_are_never_spaced(fake_time):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"Data": []})

    api = _api(handler, fake_time)

    async def two_reads():
        await api.request(GetOpenOrdersMe())
        await api.request(GetOpenOrdersMe())

    asyncio.run(two_reads())
    assert fake_time.slept == []
