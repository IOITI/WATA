"""
What WATA cannot sell at once, it goes on selling until Saxo no longer lists
the position (src/trade/close_intents.py, AsyncPerformanceMonitor): a refused
sale is tried again, spaced and said without flooding Telegram; nothing is
held after the day's close nor into a US release the macro_news rule names; a
position WATA bought but lost is managed again; a close that could not sell
says why. Scenarios from the record of 2026-07-25 → 2026-10-24.
"""

import asyncio
import sys
import types
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytz

asyncpg_stub = types.ModuleType("asyncpg")
asyncpg_stub.Pool = type("Pool", (), {})
asyncpg_stub.Record = dict
asyncpg_stub.create_pool = AsyncMock()
sys.modules.setdefault("asyncpg", asyncpg_stub)
sys.modules.setdefault("aio_pika", types.ModuleType("aio_pika"))

from src.configuration import ConfigurationManager  # noqa: E402
from src.trade.async_services import (  # noqa: E402
    ADOPT_AFTER_SECONDS,
    END_OF_DAY_REASON,
    MISSING_PRICE_ALERT_SECONDS,
    AsyncOrderService,
    AsyncPerformanceMonitor,
)
from src.trade.exceptions import OrderPlacementError  # noqa: E402
from src.trade.macro_calendar import NewsRule, read_calendar  # noqa: E402

PARIS = pytz.timezone("Europe/Paris")


def paris(*moment) -> float:
    return PARIS.localize(datetime(*moment)).timestamp()


def listed(position_id="p1", *, bid=10.0, can_be_closed=True, source_order="buy-1", uic=101) -> dict:
    """A position as the stream gives it, opened before any clock of these tests: judged on its first bid."""
    return {position_id: {
        "PositionId": position_id,
        "PositionBase": {
            "Amount": 10, "Uic": uic, "AssetType": "WarrantKnockOut", "CanBeClosed": can_be_closed, "Status": "Open",
            "OpenPrice": 10.0, "SourceOrderId": source_order, "ExecutionTimeOpen": "1970-01-01T00:00:00Z",
            "RelatedOpenOrders": [],
        },
        "PositionView": {"Bid": bid},
        "DisplayAndFormat": {"Description": "NASDAQ 100 TurboXL Long 30094.7541 Saxo", "Symbol": "T1", "Currency": "EUR"},
    }}


class Rows:
    """WATA's position rows: what is open, and what the monitor writes."""

    def __init__(self, *open_rows):
        self.open = {position_id: action for position_id, action in open_rows}
        self.inserted = []
        self.updated = []

    async def get_open_positions_ids_actions(self):
        return [{"position_id": position_id, "action": action} for position_id, action in self.open.items()]

    async def get_open_positions_ids(self):
        return list(self.open)

    async def get_percent_of_the_day(self):
        return 0.0

    async def get_max_position_percent(self, position_id):
        return 0.0

    async def update_turbo_position_data(self, position_id, data):
        self.updated.append((position_id, data))

    async def insert_turbo_open_position_data(self, data):
        self.inserted.append(data)
        self.open[data["position_id"]] = data["action"]


def monitor_for(tmp_path, *, rows=None, open_positions=(), sales=None, working=(), clock=None,
                day_trading=None, news_rule=None, order_requests=None, monitor_class=AsyncPerformanceMonitor):
    config = {
        "trade.config.position_management": {"performance_thresholds": {"stoploss_percent": -7, "max_profit_percent": 60}},
    }
    config_manager = MagicMock(spec=ConfigurationManager)
    config_manager.get_config_value.side_effect = lambda key, default=None: config.get(key, default)
    config_manager.get_logging_config.return_value = {"persistant": {"log_path": str(tmp_path)}}

    position_service = MagicMock()
    position_service.get_open_positions = AsyncMock(return_value={"Data": list(open_positions)})
    position_service.get_open_orders = AsyncMock(return_value=list(working))
    order_service = MagicMock()
    order_service.place_market_order = AsyncMock(**(sales or {"return_value": {"OrderId": "sell-1"}}))
    order_service.cancel_order = AsyncMock(return_value=True)

    trading_rule = MagicMock()
    trading_rule.get_rule_config.return_value = day_trading if day_trading is not None else {}
    trading_rule.market_hours_config = {"trading_start_hour": 8, "trading_end_hour": 22}
    trading_rule.news_rule = news_rule
    trading_rule.news_calendar = read_calendar() if news_rule is not None else None
    trading_rule.record_loss = AsyncMock()

    monitor = monitor_class(
        position_service, order_service, config_manager, rows or Rows(("p1", "long")), trading_rule, AsyncMock(),
        order_requests=order_requests,
    )
    monitor._record_closed_position_in_background = MagicMock()
    clock = clock if clock is not None else {"now": 1000.0}
    monitor._now = lambda: clock["now"]
    return monitor, clock


def said(monitor) -> list[str]:
    return [call.args[0] for call in monitor.send_telegram.await_args_list]


def sale_reasons(monitor) -> list[str]:
    return [call.kwargs["context"].reason for call in monitor.order_service.place_market_order.await_args_list]


# ── a sale that did not go through ──


def test_a_refused_sale_is_tried_again_after_its_wait_and_said_once(tmp_path):
    monitor, clock = monitor_for(tmp_path, sales={"side_effect": [OrderPlacementError("no quote"), {"OrderId": "sell-2"}]})

    async def scenario():
        await monitor.check_positions_from_stream(listed(bid=9.0))  # -10 %: the stop, refused
        clock["now"] = 1000.5
        await monitor.check_positions_from_stream(listed(bid=8.9))  # too soon to try again
        clock["now"] = 1001.0
        await monitor.check_positions_from_stream(listed(bid=8.8))  # sold
        clock["now"] = 1003.0
        await monitor.check_positions_from_stream({})  # Saxo no longer lists it
        await monitor.wait_for_background_tasks()

    asyncio.run(scenario())

    assert monitor.order_service.place_market_order.await_count == 2
    # The decision's own reason, kept from the first attempt.
    assert sale_reasons(monitor) == ["Performance (Stoploss (-7%) hit at -10.0%)"] * 2
    # The retry looked for a sale already working at Saxo; the first, urgent one asked nothing before selling.
    monitor.position_service.get_open_orders.assert_awaited_once()
    messages = said(monitor)
    assert len(messages) == 2
    assert messages[0].startswith("Could not sell position p1 (Performance (Stoploss (-7%) hit at -10.0%))")
    assert messages[1] == ("Position p1 is no longer open at Saxo, after 1 failed attempt(s) to sell it "
                           "(Performance (Stoploss (-7%) hit at -10.0%)).")
    assert len(monitor.close_intents) == 0


def test_a_position_saxo_says_cannot_be_closed_is_kept_to_be_sold_and_said(tmp_path):
    monitor, clock = monitor_for(tmp_path)

    async def scenario():
        first = await monitor.check_positions_from_stream(listed(bid=9.0, can_be_closed=False))
        clock["now"] = 1001.0
        second = await monitor.check_positions_from_stream(listed(bid=8.9, can_be_closed=True))
        await monitor.wait_for_background_tasks()
        return first, second

    first, second = asyncio.run(scenario())

    assert first["closed_positions_processed"] == [{"id": "p1", "status": "Skipped (Cannot Be Closed)", "error": True}]
    assert second["closed_positions_processed"] == [{"id": "p1", "status": "Close order sent"}]
    assert said(monitor)[0].startswith("Could not sell position p1")
    assert "Saxo says it cannot be closed now" in said(monitor)[0]


def test_a_sale_working_at_saxo_is_waited_for_rather_than_sent_again(tmp_path):
    # SellOrdersAlreadyExistForOwnedContracts, three times in the record: a sell already working blocks the next.
    working = [{"OrderId": "sell-1", "Uic": 101, "BuySell": "Sell"}, {"OrderId": "buy-7", "Uic": 101, "BuySell": "Buy"}]
    monitor, _ = monitor_for(tmp_path, open_positions=listed().values(), working=working)

    result = asyncio.run(monitor.request_close("p1", "Explicit Close (position p1)"))

    monitor.order_service.place_market_order.assert_not_awaited()
    assert result["pending"] is True
    assert monitor.close_intents.get("p1").last_error.startswith("A sale is still working at Saxo (order sell-1)")


# ── what comes with time ──


def test_what_is_still_held_after_the_days_close_is_sold_and_said(tmp_path):
    clock = {"now": paris(2026, 10, 9, 21, 55, 30)}
    monitor, _ = monitor_for(tmp_path, clock=clock, day_trading={"close_position_time": "21:55"})

    async def scenario():
        await monitor.keep_flat(listed())  # within a minute of the scheduler's close: left to it
        before = monitor.order_service.place_market_order.await_count
        clock["now"] = paris(2026, 10, 9, 21, 56, 1)
        await monitor.keep_flat(listed())
        clock["now"] = paris(2026, 10, 9, 21, 56, 2)
        await monitor.keep_flat(listed())
        await monitor.wait_for_background_tasks()
        return before

    before = asyncio.run(scenario())

    assert before == 0
    assert sale_reasons(monitor) == [END_OF_DAY_REASON]
    assert said(monitor) == ["Position p1 was still open after the day's close (21:55): WATA sells it."]


def test_the_night_and_the_morning_before_the_start_count_as_after_the_close(tmp_path):
    monitor, clock = monitor_for(tmp_path, day_trading={"close_position_time": "21:55"})

    clock["now"] = paris(2026, 10, 12, 7, 59)
    assert monitor._after_the_close(clock["now"]) is True
    clock["now"] = paris(2026, 10, 12, 8, 0)
    assert monitor._after_the_close(clock["now"]) is False
    clock["now"] = paris(2026, 10, 12, 21, 54)
    assert monitor._after_the_close(clock["now"]) is False


def test_nothing_is_held_into_a_us_release_when_the_rule_sells_before_it(tmp_path):
    # 14 October 2026: a long bought 3 minutes before CPI was sold at -30 % once quotes came back.
    clock = {"now": paris(2026, 10, 14, 14, 19, 50)}
    monitor, _ = monitor_for(tmp_path, clock=clock, news_rule=NewsRule.from_config({"flatten_minutes_before": 10}))

    async def scenario():
        await monitor.keep_flat(listed())
        held = monitor.order_service.place_market_order.await_count
        clock["now"] = paris(2026, 10, 14, 14, 20, 1)
        await monitor.keep_flat(listed())
        return held

    assert asyncio.run(scenario()) == 0
    assert sale_reasons(monitor) == ["Performance (Flat before CPI (14:30))"]


def test_without_the_rule_a_release_changes_nothing(tmp_path):
    clock = {"now": paris(2026, 10, 14, 14, 29)}
    monitor, _ = monitor_for(tmp_path, clock=clock, news_rule=NewsRule.from_config({}))

    asyncio.run(monitor.keep_flat(listed()))

    monitor.order_service.place_market_order.assert_not_awaited()


# ── asked by the admin or handed over by the trader ──


def test_a_close_asked_of_the_monitor_sells_a_managed_position_listed_at_saxo(tmp_path):
    monitor, _ = monitor_for(tmp_path, open_positions=listed().values())

    sold = asyncio.run(monitor.request_close("p1", "Explicit Close (All)"))
    not_ours = asyncio.run(monitor.request_close("p9", "Explicit Close (All)"))

    assert sold == {"closed_initiated_count": 1, "errors_count": 0, "closed_position_ids": ["p1"], "pending": False}
    assert sale_reasons(monitor) == ["Explicit Close (All)"]
    assert not_ours == {"closed_initiated_count": 0, "errors_count": 0, "closed_position_ids": [], "pending": False}


def test_a_close_asked_that_cannot_sell_yet_stays_pending(tmp_path):
    monitor, _ = monitor_for(tmp_path, open_positions=listed().values(), sales={"side_effect": OrderPlacementError("no quote")})

    result = asyncio.run(monitor.request_close("p1", "Explicit Close (All)"))

    assert result["pending"] is True and result["errors_count"] == 1
    assert "p1" in monitor.close_intents


# ── what a close could not sell ──


def test_a_close_says_what_it_could_not_sell_and_what_saxo_does_not_list(tmp_path):
    rows = Rows(("p1", "long"), ("p2", "short"))
    monitor, _ = monitor_for(tmp_path, rows=rows, open_positions=listed("p1", can_be_closed=False).values())

    result = asyncio.run(monitor.close_managed_positions_by_criteria())

    assert result["closed_initiated_count"] == 0
    assert result["not_closed"] == [{"position_id": "p1", "action": "long", "reason": "Explicit Close (All)",
                                     "why": "Saxo says it cannot be closed now"}]
    assert result["not_listed"] == ["p2"]


def test_a_managed_position_without_a_price_is_said_once(tmp_path):
    monitor, clock = monitor_for(tmp_path)

    async def scenario():
        for moment in (1000.0, 1000.0 + MISSING_PRICE_ALERT_SECONDS - 1, 1000.0 + MISSING_PRICE_ALERT_SECONDS, 1100.0):
            clock["now"] = moment
            await monitor.check_positions_from_stream(listed(bid=None))
        await monitor.wait_for_background_tasks()

    asyncio.run(scenario())

    assert len(said(monitor)) == 1
    assert said(monitor)[0].startswith("No price for position p1 since ")


# ── a position WATA lost ──


def test_a_position_wata_bought_but_lost_is_managed_again(tmp_path):
    rows = Rows()
    order_requests = MagicMock()
    order_requests.purchases = AsyncMock(return_value={"buy-1": {"order_id": "buy-1", "action": "long"}})
    monitor, clock = monitor_for(tmp_path, rows=rows, order_requests=order_requests)
    positions = {**listed("p1", source_order="buy-1"), **listed("p2", source_order="by-hand-9")}

    async def scenario():
        first = await monitor.adopt_lost_positions(positions)  # just listed: the trader may still be writing its row
        clock["now"] = 1000.0 + ADOPT_AFTER_SECONDS
        second = await monitor.adopt_lost_positions(positions)
        third = await monitor.adopt_lost_positions(positions)
        await monitor.wait_for_background_tasks()
        return first, second, third

    first, second, third = asyncio.run(scenario())

    assert (first, second, third) == ([], ["p1"], [])
    (row,) = rows.inserted
    assert (row["position_id"], row["action"], row["order_id"], row["position_amount"], row["related_order_id"]) == (
        "p1", "long", "buy-1", 10, [],
    )
    messages = said(monitor)
    assert any(message.startswith("Position p1 (NASDAQ 100 TurboXL Long 30094.7541 Saxo), bought by WATA (order buy-1)")
               for message in messages)
    assert sum("p2" in message and "not bought by WATA" in message for message in messages) == 1


def test_wata_s_own_sale_keeps_its_reason_when_the_reconciliation_records_it(tmp_path):
    # 43 of 245 closes read "SaxoAPI": WATA's sales, recorded by the reconciliation before their own bookkeeping.
    rows = Rows(("p1", "long"), ("p2", "short"))
    order_requests = MagicMock()
    order_requests.sale_reasons = AsyncMock(return_value={"p1": "Explicit Close (All)"})
    monitor, _ = monitor_for(tmp_path, rows=rows, order_requests=order_requests)

    def closed(position_id, perf_close):
        return {"ClosedPosition": {"OpeningPositionId": position_id, "OpenPrice": 10.0, "ClosingPrice": perf_close,
                                   "Amount": 10, "ExecutionTimeClose": "2026-10-14T12:35:46Z"},
                "DisplayAndFormat": {"Description": "Turbo"}}

    monitor.position_service.get_closed_positions = AsyncMock(return_value={"Data": [closed("p1", 9.9), closed("p2", 10.5)]})

    result = asyncio.run(monitor.sync_db_positions_with_api(set()))

    reasons = {position_id: update["position_close_reason"] for position_id, update in result["updates_for_db"]}
    assert reasons == {"p1": "Explicit Close (All)", "p2": "SaxoAPI"}
    assert [message.split(":")[0] for message in said(monitor)] == ["SYNC CLOSE"]
    assert "p2" in said(monitor)[0]
    # The loss still starts the cooldown, as WATA's own bookkeeping does.
    monitor.trading_rule.record_loss.assert_awaited_once()


# ── each order request its own id ──


def test_every_order_request_carries_its_own_id_the_one_it_is_kept_under():
    sent = []

    async def request(endpoint):
        sent.append(dict(endpoint.HEADERS))
        return {"OrderId": f"o{len(sent)}"}

    class Tracker:
        def __init__(self):
            self.requests = []

        def record_request(self, request):
            self.requests.append(request)

    api_client = MagicMock()
    api_client.request = AsyncMock(side_effect=request)
    tracker = Tracker()
    service = AsyncOrderService(api_client, "account", "client", order_tracker=tracker)

    async def scenario():
        await service.place_market_order(101, "WarrantKnockOut", 10, "Sell")
        await service.place_market_order(101, "WarrantKnockOut", 10, "Sell")
        await service.cancel_order("o1")

    asyncio.run(scenario())

    ids = [headers["x-request-id"] for headers in sent]
    assert len(set(ids)) == 3
    assert ids == [request.request_id for request in tracker.requests]

