import asyncio
import sys
import threading
import time
import types

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.configuration import ConfigurationManager

asyncpg_stub = types.ModuleType("asyncpg")
asyncpg_stub.Pool = type("Pool", (), {})
asyncpg_stub.Record = dict
asyncpg_stub.create_pool = AsyncMock()
sys.modules.setdefault("asyncpg", asyncpg_stub)

from src.trade.async_services import (
    AsyncInstrumentService,
    AsyncOrderService,
    AsyncPerformanceMonitor,
    AsyncPositionService,
    AsyncSaxoApiClient,
    AsyncTradingOrchestrator,
    backoff_gaps,
    round_down_to_tick,
    stop_order_tick_size,
)
from src.trade.exceptions import OrderPlacementError, PositionNotFoundException, TokenAuthenticationException
from src.saxo_openapi.exceptions import OpenAPIError


@pytest.fixture
def mock_config_manager():
    manager = MagicMock(spec=ConfigurationManager)

    def get_config_value(key, default=None):
        configs = {
            "trade.config.general.api_limits": {"top_instruments": 200},
            "trade.config.turbo_preference.price_range": {"min": 4, "max": 15},
            "trade.config.general.retry_config": {"max_retries": 3, "retry_sleep_seconds": 1},
            "trade.config.turbo_cache": {"enabled": False, "ttl_seconds": 30},
        }
        return configs.get(key, default)

    manager.get_config_value.side_effect = get_config_value
    return manager


@pytest.fixture
def mock_api_client():
    client = MagicMock()
    client.request = AsyncMock()
    return client


def test_get_current_account_balance_returns_total_value(mock_config_manager, mock_api_client):
    mock_api_client.request = AsyncMock(return_value={"TotalValue": 27.54, "SpendingPower": 20.0})
    position_service = AsyncPositionService(
        mock_api_client, MagicMock(), mock_config_manager, "account-1", "client-1",
    )

    balance = asyncio.run(position_service.get_current_account_balance())

    assert balance == 27.54


@pytest.fixture
def instrument_service(mock_api_client, mock_config_manager):
    return AsyncInstrumentService(mock_api_client, mock_config_manager, "account_key")


def test_find_turbos_uses_two_stage_infoprices_funnel(instrument_service, mock_api_client):
    mock_api_client.request.side_effect = [
        {
            "Data": [
                {
                    "Identifier": 1,
                    "Description": "TURBO LONG DAX 15000 CITI",
                    "AssetType": "WarrantKnockOut",
                }
            ]
        },
        {
            "Data": [
                {
                    "Uic": 101,
                    "Identifier": 1,
                    "AssetType": "WarrantKnockOut",
                    "Quote": {
                        "Bid": 10.0,
                        "Ask": 10.1,
                        "PriceTypeAsk": "Tradable",
                        "PriceTypeBid": "Tradable",
                        "MarketState": "Open",
                    },
                }
            ]
        },
        {
            "Data": [
                {
                    "Uic": 101,
                    "DisplayAndFormat": {
                        "Description": "Final TURBO LONG DAX 15000 CITI",
                        "Symbol": "DAXL",
                        "Currency": "EUR",
                        "OrderDecimals": 2,
                    },
                    "Commissions": {"CostBuy": 0.25},
                    "InstrumentPriceDetails": {"LotSize": 1},
                }
            ]
        },
    ]

    result = asyncio.run(instrument_service.find_turbos("exchange1", "underlying1", "long"))

    assert result["selected_instrument"]["uic"] == 101
    assert result["selected_instrument"]["latest_ask"] == 10.1
    assert result["selected_instrument"]["description"] == "Final TURBO LONG DAX 15000 CITI"
    assert result["selected_instrument"]["commissions"]["CostBuy"] == 0.25
    assert result["selected_instrument"]["subscription_context_id"] is None
    assert result["selected_instrument"]["subscription_reference_id"] is None
    assert mock_api_client.request.await_count == 3

    initial_search_req = mock_api_client.request.await_args_list[0].args[0]
    stage_one_req = mock_api_client.request.await_args_list[1].args[0]
    stage_two_req = mock_api_client.request.await_args_list[2].args[0]

    assert initial_search_req.params["$top"] == 60
    assert stage_one_req.params["FieldGroups"] == "Quote"
    assert stage_one_req.params["$top"] == 1
    assert stage_two_req.params["FieldGroups"] == "Commissions,DisplayAndFormat,InstrumentPriceDetails"
    assert stage_two_req.params["$top"] == 1


def test_find_turbos_short_circuits_retry_when_one_in_range_bid_exists(instrument_service, mock_api_client):
    mock_api_client.request.side_effect = [
        {
            "Data": [
                {
                    "Identifier": 1,
                    "Description": "TURBO LONG DAX 15000 CITI",
                    "AssetType": "WarrantKnockOut",
                },
                {
                    "Identifier": 2,
                    "Description": "TURBO LONG DAX 14900 CITI",
                    "AssetType": "WarrantKnockOut",
                },
                {
                    "Identifier": 3,
                    "Description": "TURBO LONG DAX 14800 CITI",
                    "AssetType": "WarrantKnockOut",
                },
            ]
        },
        {
            "Data": [
                {
                    "Uic": 101,
                    "Identifier": 1,
                    "AssetType": "WarrantKnockOut",
                    "Quote": {
                        "Bid": 10.0,
                        "Ask": 10.1,
                        "PriceTypeAsk": "Tradable",
                        "PriceTypeBid": "Tradable",
                        "MarketState": "Open",
                    },
                },
                {
                    "Uic": 102,
                    "Identifier": 2,
                    "AssetType": "WarrantKnockOut",
                    "Quote": {
                        "Ask": 9.8,
                        "PriceTypeAsk": "Tradable",
                        "PriceTypeBid": "Tradable",
                        "MarketState": "Open",
                    },
                },
                {
                    "Uic": 103,
                    "Identifier": 3,
                    "AssetType": "WarrantKnockOut",
                    "Quote": {
                        "Ask": 9.6,
                        "PriceTypeAsk": "Tradable",
                        "PriceTypeBid": "Tradable",
                        "MarketState": "Open",
                    },
                },
            ]
        },
        {
            "Data": [
                {
                    "Uic": 101,
                    "DisplayAndFormat": {
                        "Description": "Final TURBO LONG DAX 15000 CITI",
                        "Symbol": "DAXL",
                        "Currency": "EUR",
                        "OrderDecimals": 2,
                    },
                    "Commissions": {"CostBuy": 0.25},
                }
            ]
        },
    ]

    with patch("src.trade.async_services.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        result = asyncio.run(instrument_service.find_turbos("exchange1", "underlying1", "long"))

    assert result["selected_instrument"]["uic"] == 101
    assert mock_api_client.request.await_count == 3
    mock_sleep.assert_not_awaited()


def test_find_turbos_handles_empty_stage_two_details(instrument_service, mock_api_client):
    mock_api_client.request.side_effect = [
        {
            "Data": [
                {
                    "Identifier": 1,
                    "Description": "TURBO LONG DAX 15000 CITI",
                    "AssetType": "WarrantKnockOut",
                }
            ]
        },
        {
            "Data": [
                {
                    "Uic": 101,
                    "Identifier": 1,
                    "AssetType": "WarrantKnockOut",
                    "Quote": {
                        "Bid": 10.0,
                        "Ask": 10.1,
                        "PriceTypeAsk": "Tradable",
                        "PriceTypeBid": "Tradable",
                        "MarketState": "Open",
                    },
                }
            ]
        },
        {"Data": []},
    ]

    result = asyncio.run(instrument_service.find_turbos("exchange1", "underlying1", "long"))

    assert result["selected_instrument"]["description"] == "TURBO LONG DAX 15000 CITI"
    assert result["selected_instrument"]["latest_bid"] == 10.0
    assert result["selected_instrument"]["parsed_data"]["price"] == "15000"


def test_build_watchlist_candidates_returns_grouped_snapshots(instrument_service, mock_api_client):
    mock_api_client.request.side_effect = [
        {
            "Data": [
                {
                    "Identifier": 1,
                    "Description": "TURBO LONG DAX 15000 CITI",
                    "AssetType": "WarrantKnockOut",
                },
                {
                    "Identifier": 2,
                    "Description": "TURBO LONG DAX 14900 CITI",
                    "AssetType": "WarrantKnockOut",
                },
            ]
        },
        {
            "Data": [
                {
                    "Uic": 101,
                    "Identifier": 1,
                    "AssetType": "WarrantKnockOut",
                    "Quote": {
                        "Bid": 10.0,
                        "Ask": 10.1,
                        "PriceTypeAsk": "Tradable",
                        "PriceTypeBid": "Tradable",
                        "MarketState": "Open",
                    },
                },
                {
                    "Uic": 102,
                    "Identifier": 2,
                    "AssetType": "WarrantKnockOut",
                    "Quote": {
                        "Bid": 10.5,
                        "Ask": 10.6,
                        "PriceTypeAsk": "Tradable",
                        "PriceTypeBid": "Tradable",
                        "MarketState": "Open",
                    },
                },
            ]
        },
        {
            "Data": [
                {
                    "Uic": 101,
                    "DisplayAndFormat": {
                        "Description": "Final TURBO LONG DAX 15000 CITI",
                        "Symbol": "DAXL1",
                        "Currency": "EUR",
                        "OrderDecimals": 2,
                    },
                    "Commissions": {"CostBuy": 0.25},
                    "InstrumentPriceDetails": {"LotSize": 1},
                },
                {
                    "Uic": 102,
                    "DisplayAndFormat": {
                        "Description": "Final TURBO LONG DAX 14900 CITI",
                        "Symbol": "DAXL2",
                        "Currency": "EUR",
                        "OrderDecimals": 2,
                    },
                    "Commissions": {"CostBuy": 0.30},
                    "InstrumentPriceDetails": {"LotSize": 1},
                },
            ]
        },
    ]

    result = asyncio.run(instrument_service.build_watchlist_candidates("exchange1", "underlying1", "long"))

    assert result["selected_result"]["selected_instrument"]["uic"] == 101
    assert len(result["candidate_snapshots"]) == 2
    assert result["candidate_snapshots"][0]["DisplayAndFormat"]["Symbol"] == "DAXL1"
    assert result["subscription_groups"] == [{"asset_type": "WarrantKnockOut", "uics": [101, 102]}]


def test_execute_trade_signal_uses_watchlist_before_live_search():
    config_manager = MagicMock(spec=ConfigurationManager)

    def get_config_value(key, default=None):
        configs = {
            "trade.config.buying_power": {
                "max_account_funds_to_use_percentage": 100,
                "reserve_cash_percent": 0,
                "safety_margins": {"bid_calculation": 1},
            },
            "trade.config.general.timezone": "Europe/Paris",
            "trade.config.position_sizing.time_of_day_scaling": {"enabled": False},
            "trade.config.position_sizing.confidence_scaling": {"enabled": False},
            "logging.persistant.log_path": ".",
        }
        return configs.get(key, default)

    config_manager.get_config_value.side_effect = get_config_value

    instrument_service = MagicMock()
    instrument_service.find_turbos = AsyncMock(side_effect=AssertionError("live turbo search should not run on watchlist hit"))

    order_service = MagicMock()
    order_service.place_market_order = AsyncMock(return_value={"OrderId": "order-1"})

    position_service = MagicMock()
    position_service.get_spending_power = AsyncMock(return_value=1000.0)
    position_service.find_position_by_order_id_with_retry = AsyncMock(
        return_value={
            "PositionId": "position-1",
            "PositionBase": {
                "Amount": 98,
                "OpenPrice": 10.1,
                "Status": "Open",
                "ExecutionTimeOpen": "2026-06-19T10:00:00Z",
                "SourceOrderId": "order-1",
                "RelatedOpenOrders": [],
                "Uic": 101,
            },
            "DisplayAndFormat": {
                "Description": "Cached Turbo",
                "Symbol": "CACHED",
                "Currency": "EUR",
            },
        }
    )

    db_order_manager = MagicMock()
    db_order_manager.insert_turbo_order_data = AsyncMock()

    db_position_manager = MagicMock()
    db_position_manager.insert_turbo_open_position_data = AsyncMock()

    watchlist_client = MagicMock()
    watchlist_client.get_best_turbo = AsyncMock(
        return_value={
            "input_criteria": {"exchange_id": "exchange1", "underlying_uics": "1909050", "keywords": "long"},
            "selected_instrument": {
                "uic": 101,
                "asset_type": "WarrantKnockOut",
                "description": "Cached Turbo",
                "symbol": "CACHED",
                "currency": "EUR",
                "quote": {"Ask": 10.1, "Bid": 10.0},
                "commissions": {"CostBuy": 0.25},
                "latest_ask": 10.1,
                "latest_bid": 10.0,
            },
            "watchlist": {"stale": False},
        }
    )

    orchestrator = AsyncTradingOrchestrator(
        instrument_service=instrument_service,
        order_service=order_service,
        position_service=position_service,
        config_manager=config_manager,
        db_order_manager=db_order_manager,
        db_position_manager=db_position_manager,
        watchlist_client=watchlist_client,
    )

    result = asyncio.run(
        orchestrator.execute_trade_signal(
            exchange_id="exchange1",
            underlying_uics="1909050",
            keywords="long",
        )
    )

    assert result["selected_turbo_info"]["selected_instrument"]["uic"] == 101
    watchlist_client.get_best_turbo.assert_awaited_once()
    instrument_service.find_turbos.assert_not_awaited()

# ── backoff_gaps ──

def test_backoff_gaps_double_up_to_the_cap_and_add_up_to_the_total():
    assert backoff_gaps(1.8) == pytest.approx([0.1, 0.2, 0.4, 0.8, 0.3])
    assert sum(backoff_gaps(10.8)) == pytest.approx(10.8)
    assert max(backoff_gaps(10.8)) == 1.0
    assert backoff_gaps(0) == []


# ── Position confirmation after an order ──

def _slept(mock_sleep) -> list[float]:
    return [call.args[0] for call in mock_sleep.await_args_list]


def _position_service_with_open_positions(mock_config_manager, open_positions):
    order_service = MagicMock()
    order_service.cancel_order = AsyncMock(return_value=True)
    position_service = AsyncPositionService(MagicMock(), order_service, mock_config_manager, "account-1", "client-1")
    position_service.get_open_positions = AsyncMock(**open_positions)
    return position_service, order_service


def test_position_confirmation_checks_before_sleeping(mock_config_manager):
    position = {"PositionId": "position-1", "PositionBase": {"SourceOrderId": "order-1"}}
    position_service, _ = _position_service_with_open_positions(
        mock_config_manager, {"return_value": {"Data": [position]}},
    )

    with patch("src.trade.async_services.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        found = asyncio.run(position_service.find_position_by_order_id_with_retry("order-1"))

    assert found == position
    mock_sleep.assert_not_awaited()


def test_position_confirmation_finds_a_position_listed_a_moment_later(mock_config_manager):
    position = {"PositionId": "position-1", "PositionBase": {"SourceOrderId": "order-1"}}
    position_service, order_service = _position_service_with_open_positions(
        mock_config_manager, {"side_effect": [{"Data": []}, {"Data": []}, {"Data": [position]}]},
    )

    with patch("src.trade.async_services.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        found = asyncio.run(position_service.find_position_by_order_id_with_retry("order-1"))

    assert found == position
    assert _slept(mock_sleep) == pytest.approx([0.1, 0.2])
    order_service.cancel_order.assert_not_awaited()


def test_position_confirmation_keeps_its_patience_then_cancels_the_order(mock_config_manager):
    # max_retries=3 in the fixture: 0.3 + 0.5 + 1.0 seconds of patience overall
    position_service, order_service = _position_service_with_open_positions(
        mock_config_manager, {"return_value": {"Data": []}},
    )

    with patch("src.trade.async_services.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(PositionNotFoundException):
            asyncio.run(position_service.find_position_by_order_id_with_retry("order-1"))

    assert sum(_slept(mock_sleep)) == pytest.approx(1.8)
    order_service.cancel_order.assert_awaited_once_with("order-1")


# ── AsyncSaxoApiClient: token handling ──

def _api_client(get_token):
    config_manager = MagicMock(spec=ConfigurationManager)
    config_manager.get_config_value.return_value = "simulation"
    saxo_auth = MagicMock()
    saxo_auth.get_token = MagicMock(side_effect=get_token)
    return AsyncSaxoApiClient(config_manager, saxo_auth), saxo_auth


def test_ensure_ready_swaps_a_renewed_token_into_the_existing_client():
    api_client, _ = _api_client(["token-1", "token-2"])

    async def scenario():
        await api_client.ensure_ready()
        await api_client.ensure_ready()

    with patch("src.trade.async_services.AsyncAPI") as api_cls:
        api_cls.return_value.close = AsyncMock()
        with patch("src.trade.async_services.TOKEN_RECHECK_INTERVAL_SECONDS", 0):
            asyncio.run(scenario())

    assert api_cls.call_count == 1
    api_cls.return_value.update_token.assert_called_once_with("token-2")
    api_cls.return_value.close.assert_not_awaited()


def test_ensure_ready_does_not_ask_for_the_token_on_every_request():
    api_client, saxo_auth = _api_client(["token-1"])

    async def scenario():
        for _ in range(3):
            await api_client.ensure_ready()

    with patch("src.trade.async_services.AsyncAPI"):
        asyncio.run(scenario())

    assert saxo_auth.get_token.call_count == 1


def test_concurrent_ensure_ready_calls_ask_for_the_token_once_off_the_event_loop():
    token_threads = []

    def get_token():
        token_threads.append(threading.current_thread())
        time.sleep(0.05)
        return "token-1"

    api_client, saxo_auth = _api_client(get_token)

    async def scenario():
        await asyncio.gather(*[api_client.ensure_ready() for _ in range(5)])

    with patch("src.trade.async_services.AsyncAPI") as api_cls:
        asyncio.run(scenario())

    assert saxo_auth.get_token.call_count == 1
    assert api_cls.call_count == 1
    assert token_threads[0] is not threading.main_thread()


def test_unauthorized_response_forces_a_token_recheck():
    api_client, saxo_auth = _api_client(["token-1", "token-2"])

    async def scenario():
        with pytest.raises(TokenAuthenticationException):
            await api_client.request(MagicMock())
        await api_client.ensure_ready()

    with patch("src.trade.async_services.AsyncAPI") as api_cls:
        api_cls.return_value.request = AsyncMock(
            side_effect=OpenAPIError(401, "Unauthorized", '{"Message": "Invalid token"}')
        )
        asyncio.run(scenario())

    assert saxo_auth.get_token.call_count == 2
    api_cls.return_value.update_token.assert_called_once_with("token-2")


# ── AsyncTradingOrchestrator: spending power ──

WATCHLIST_TURBO = {
    "input_criteria": {"exchange_id": "exchange1", "underlying_uics": "1909050", "keywords": "long"},
    "selected_instrument": {
        "uic": 101,
        "asset_type": "WarrantKnockOut",
        "description": "Cached Turbo",
        "symbol": "CACHED",
        "currency": "EUR",
        "quote": {"Ask": 10.1, "Bid": 10.0},
        "commissions": {"CostBuy": 0.25},
        "latest_ask": 10.1,
        "latest_bid": 10.0,
    },
    "watchlist": {"stale": False},
}

TURBO_DETAILS = {
    "SupportedOrderTypes": ["Market", "Limit", "StopIfTraded"],
    "TickSizeScheme": {
        "DefaultTickSize": 0.1,
        "Elements": [{"HighPrice": 5, "TickSize": 0.01}, {"HighPrice": 50, "TickSize": 0.05}],
    },
}

CONFIRMED_POSITION = {
    "PositionId": "position-1",
    "PositionBase": {
        "Amount": 98,
        "OpenPrice": 10.1,
        "Status": "Open",
        "ExecutionTimeOpen": "2026-06-19T10:00:00Z",
        "SourceOrderId": "order-1",
        "RelatedOpenOrders": [],
        "Uic": 101,
    },
    "DisplayAndFormat": {"Description": "Cached Turbo", "Symbol": "CACHED", "Currency": "EUR"},
}


def _orchestrator(tmp_path, *, get_spending_power=None, get_best_turbo=None, protective_stop=None, **buying_power):
    config_manager = MagicMock(spec=ConfigurationManager)

    def get_config_value(key, default=None):
        configs = {
            "trade.config.buying_power": {
                "max_account_funds_to_use_percentage": 100,
                "reserve_cash_percent": 0,
                "safety_margins": {"bid_calculation": 1},
                **buying_power,
            },
            "trade.config.general.timezone": "Europe/Paris",
            "logging.persistant.log_path": str(tmp_path),
        }
        if protective_stop is not None:
            configs["trade.config.position_management.protective_stop"] = protective_stop
        return configs.get(key, default)

    config_manager.get_config_value.side_effect = get_config_value

    order_service = MagicMock()
    order_service.place_market_order = AsyncMock(return_value={"OrderId": "order-1"})
    order_service.place_related_stop_order = AsyncMock(return_value="stop-1")

    position_service = MagicMock()
    position_service.get_spending_power = AsyncMock(side_effect=get_spending_power, return_value=1000.0)
    position_service.find_position_by_order_id_with_retry = AsyncMock(return_value=CONFIRMED_POSITION)

    instrument_service = MagicMock()
    instrument_service.get_instrument_details = AsyncMock(return_value=TURBO_DETAILS)

    db_order_manager = MagicMock()
    db_order_manager.insert_turbo_order_data = AsyncMock()
    db_position_manager = MagicMock()
    db_position_manager.insert_turbo_open_position_data = AsyncMock()
    db_position_manager.update_turbo_position_data = AsyncMock()

    watchlist_client = MagicMock()
    watchlist_client.get_best_turbo = AsyncMock(side_effect=get_best_turbo, return_value=WATCHLIST_TURBO)

    orchestrator = AsyncTradingOrchestrator(
        instrument_service=instrument_service,
        order_service=order_service,
        position_service=position_service,
        config_manager=config_manager,
        db_order_manager=db_order_manager,
        db_position_manager=db_position_manager,
        watchlist_client=watchlist_client,
    )
    return orchestrator, position_service


def _execute(orchestrator):
    return orchestrator.execute_trade_signal(exchange_id="exchange1", underlying_uics="1909050", keywords="long")


def test_balance_is_requested_while_the_watchlist_lookup_is_still_running(tmp_path):
    async def scenario():
        balance_requested = asyncio.Event()

        async def get_spending_power():
            balance_requested.set()
            return 1000.0

        async def get_best_turbo(**kwargs):
            # Times out if the balance is only requested once this lookup returns
            await asyncio.wait_for(balance_requested.wait(), timeout=1)
            return WATCHLIST_TURBO

        orchestrator, _ = _orchestrator(
            tmp_path, get_spending_power=get_spending_power, get_best_turbo=get_best_turbo,
        )
        return await _execute(orchestrator)

    result = asyncio.run(scenario())

    assert result["order_details"]["order_id"] == "order-1"


def test_spending_power_is_fetched_for_every_trade_by_default(tmp_path):
    orchestrator, position_service = _orchestrator(tmp_path)

    async def scenario():
        await orchestrator.refresh_spending_power()  # what the trader's keep-warm task does
        await _execute(orchestrator)

    asyncio.run(scenario())

    assert position_service.get_spending_power.await_count == 2


def test_cached_spending_power_is_used_within_its_max_age_and_dropped_after_an_order(tmp_path):
    orchestrator, position_service = _orchestrator(tmp_path, balance_cache_max_age_seconds=45)

    async def scenario():
        await orchestrator.refresh_spending_power()
        await _execute(orchestrator)
        fetches_after_first_trade = position_service.get_spending_power.await_count
        await _execute(orchestrator)
        return fetches_after_first_trade

    fetches_after_first_trade = asyncio.run(scenario())

    assert fetches_after_first_trade == 1
    assert position_service.get_spending_power.await_count == 2


def test_cached_spending_power_older_than_max_age_is_refetched(tmp_path):
    orchestrator, position_service = _orchestrator(tmp_path, balance_cache_max_age_seconds=45)

    async def scenario():
        await orchestrator.refresh_spending_power()
        value, fetched_at = orchestrator._cached_spending_power
        orchestrator._cached_spending_power = (value, fetched_at - 46)
        await _execute(orchestrator)

    asyncio.run(scenario())

    assert position_service.get_spending_power.await_count == 2


# ── AsyncPerformanceMonitor: closing positions ──

OPEN_POSITION = {
    "PositionId": "p1",
    "PositionBase": {
        "Amount": 10, "Uic": 101, "AssetType": "WarrantKnockOut", "CanBeClosed": True, "Status": "Open",
    },
}

CLOSED_POSITION = {
    "ClosedPosition": {
        "OpeningPositionId": "p1",
        "OpenPrice": 10.0,
        "ClosingPrice": 11.0,
        "Amount": 10,
        "ProfitLossOnTrade": 10.0,
        "ExecutionTimeClose": "2026-07-16T08:00:00Z",
    },
    "DisplayAndFormat": {"Description": "Turbo"},
}


def _performance_monitor(
    *, open_positions=None, closed_positions=None, place_market_order=None, position_management=None,
):
    config_manager = MagicMock(spec=ConfigurationManager)
    config_manager.get_config_value.side_effect = lambda key, default=None: (
        position_management
        if key == "trade.config.position_management" and position_management is not None
        else default
    )
    config_manager.get_logging_config.return_value = {"persistant": {"log_path": "."}}

    position_service = MagicMock()
    position_service.get_open_positions = AsyncMock(**(open_positions or {"return_value": {"Data": [OPEN_POSITION]}}))
    position_service.get_closed_positions = AsyncMock(
        **(closed_positions or {"return_value": {"Data": [CLOSED_POSITION]}})
    )

    order_service = MagicMock()
    order_service.place_market_order = AsyncMock(**(place_market_order or {"return_value": {"OrderId": "sell-1"}}))
    order_service.cancel_order = AsyncMock(return_value=True)

    db_position_manager = MagicMock()
    db_position_manager.get_open_positions_ids = AsyncMock(return_value=["p1"])
    db_position_manager.get_open_positions_ids_actions = AsyncMock(
        return_value=[{"position_id": "p1", "action": "long"}]
    )
    db_position_manager.update_turbo_position_data = AsyncMock()
    db_position_manager.get_max_position_percent = AsyncMock(return_value=12.0)
    db_position_manager.get_percent_of_the_day = AsyncMock(return_value=10.0)

    trading_rule = MagicMock()
    trading_rule.get_rule_config.return_value = {}
    trading_rule.record_loss = AsyncMock()

    return AsyncPerformanceMonitor(
        position_service, order_service, config_manager, db_position_manager, trading_rule, AsyncMock(),
    )


def test_deferred_close_returns_before_bookkeeping_and_never_sells_twice():
    async def scenario():
        saxo_lists_the_closure = asyncio.Event()

        async def get_closed_positions(top=None):
            await saxo_lists_the_closure.wait()
            return {"Data": [CLOSED_POSITION]}

        monitor = _performance_monitor(closed_positions={"side_effect": get_closed_positions})
        with patch("src.trade.async_services.CLOSED_POSITION_BACKGROUND_LOOKUP_DELAYS", (0,)):
            first = await monitor.close_managed_positions_by_criteria(defer_bookkeeping=True)
            db_updates_when_returned = monitor.db_position_manager.update_turbo_position_data.await_count
            second = await monitor.close_managed_positions_by_criteria(defer_bookkeeping=True)

            saxo_lists_the_closure.set()
            await asyncio.gather(*monitor._background_tasks)
        return monitor, first, second, db_updates_when_returned

    monitor, first, second, db_updates_when_returned = asyncio.run(scenario())

    assert first == {"closed_initiated_count": 1, "errors_count": 0, "closed_position_ids": ["p1"]}
    assert db_updates_when_returned == 0
    assert second == {"closed_initiated_count": 0, "errors_count": 0, "closed_position_ids": []}
    monitor.order_service.place_market_order.assert_awaited_once()

    monitor.db_position_manager.update_turbo_position_data.assert_awaited_once()
    position_id, update = monitor.db_position_manager.update_turbo_position_data.await_args.args
    assert position_id == "p1"
    assert update["position_status"] == "Closed"
    assert update["position_total_performance_percent"] == 10.0
    assert monitor._closing_position_ids == set()


def test_close_records_the_closure_before_returning_by_default():
    monitor = _performance_monitor()

    with patch("src.trade.async_services.asyncio.sleep", new_callable=AsyncMock):
        result = asyncio.run(monitor.close_managed_positions_by_criteria())

    assert result["closed_position_ids"] == ["p1"]
    monitor.db_position_manager.update_turbo_position_data.assert_awaited_once()
    assert monitor._background_tasks == set()
    assert monitor._closing_position_ids == set()


def test_closed_position_lookup_retries_until_saxo_lists_it():
    monitor = _performance_monitor(
        closed_positions={"side_effect": [{"Data": []}, {"Data": []}, {"Data": [CLOSED_POSITION]}]},
    )

    with patch("src.trade.async_services.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        asyncio.run(monitor.close_managed_positions_by_criteria())

    assert _slept(mock_sleep) == [0.2, 0.3, 0.5]
    monitor.db_position_manager.update_turbo_position_data.assert_awaited_once()


def test_closed_position_lookup_gives_up_after_its_delays_and_releases_the_position():
    monitor = _performance_monitor(closed_positions={"return_value": {"Data": []}})

    with patch("src.trade.async_services.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        result = asyncio.run(monitor.close_managed_positions_by_criteria())

    assert result["closed_initiated_count"] == 1
    assert sum(_slept(mock_sleep)) == pytest.approx(1.5)
    assert monitor.position_service.get_closed_positions.await_count == 4
    monitor.db_position_manager.update_turbo_position_data.assert_not_awaited()
    assert monitor._closing_position_ids == set()


def test_rejected_sell_order_leaves_the_position_closable():
    monitor = _performance_monitor(
        place_market_order={"side_effect": [RuntimeError("rejected"), {"OrderId": "sell-2"}]},
    )

    async def scenario():
        first = await monitor.close_managed_positions_by_criteria()
        second = await monitor.close_managed_positions_by_criteria()
        return first, second

    with patch("src.trade.async_services.asyncio.sleep", new_callable=AsyncMock):
        first, second = asyncio.run(scenario())

    assert (first["closed_initiated_count"], first["errors_count"]) == (0, 1)
    assert (second["closed_initiated_count"], second["errors_count"]) == (1, 0)


# ── AsyncPerformanceMonitor: waiting for a sale to show up ──

def test_wait_for_positions_closed_returns_at_once_when_already_gone():
    monitor = _performance_monitor(open_positions={"return_value": {"Data": []}})

    with patch("src.trade.async_services.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        closed = asyncio.run(monitor.wait_for_positions_closed(["p1"]))

    assert closed is True
    mock_sleep.assert_not_awaited()
    monitor.position_service.get_open_positions.assert_awaited_once()


def test_wait_for_positions_closed_polls_until_the_position_is_gone():
    monitor = _performance_monitor(
        open_positions={"side_effect": [{"Data": [OPEN_POSITION]}, {"Data": [OPEN_POSITION]}, {"Data": []}]},
    )

    with patch("src.trade.async_services.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        closed = asyncio.run(monitor.wait_for_positions_closed(["p1"]))

    assert closed is True
    assert _slept(mock_sleep) == pytest.approx([0.1, 0.2])


def test_wait_for_positions_closed_accepts_a_position_still_listed_as_closed():
    listed_as_closed = {"PositionId": "p1", "PositionBase": {**OPEN_POSITION["PositionBase"], "Status": "Closed"}}
    monitor = _performance_monitor(open_positions={"return_value": {"Data": [listed_as_closed]}})

    with patch("src.trade.async_services.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        closed = asyncio.run(monitor.wait_for_positions_closed(["p1"]))

    assert closed is True
    mock_sleep.assert_not_awaited()


def test_wait_for_positions_closed_gives_up_after_its_timeout():
    monitor = _performance_monitor()

    with patch("src.trade.async_services.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        closed = asyncio.run(monitor.wait_for_positions_closed(["p1"]))

    assert closed is False
    assert sum(_slept(mock_sleep)) == pytest.approx(1.5)


def test_wait_for_positions_closed_with_nothing_to_wait_for_does_not_call_saxo():
    monitor = _performance_monitor()

    closed = asyncio.run(monitor.wait_for_positions_closed([]))

    assert closed is True
    monitor.position_service.get_open_positions.assert_not_awaited()


def test_close_cancels_orders_resting_on_the_position_before_selling_it():
    with_protective_stop = {
        "PositionId": "p1",
        "PositionBase": {**OPEN_POSITION["PositionBase"], "RelatedOpenOrders": [{"OrderId": "stop-1"}, {"OrderId": "tp-1"}]},
    }
    monitor = _performance_monitor(open_positions={"return_value": {"Data": [with_protective_stop]}})
    calls = MagicMock()
    calls.attach_mock(monitor.order_service.cancel_order, "cancel")
    calls.attach_mock(monitor.order_service.place_market_order, "sell")

    with patch("src.trade.async_services.asyncio.sleep", new_callable=AsyncMock):
        asyncio.run(monitor.close_managed_positions_by_criteria())

    assert [call[0] for call in calls.mock_calls] == ["cancel", "sell"]
    monitor.order_service.cancel_order.assert_awaited_once_with("stop-1,tp-1")


def test_close_without_resting_orders_cancels_nothing():
    monitor = _performance_monitor()

    with patch("src.trade.async_services.asyncio.sleep", new_callable=AsyncMock):
        asyncio.run(monitor.close_managed_positions_by_criteria())

    monitor.order_service.cancel_order.assert_not_awaited()
    monitor.order_service.place_market_order.assert_awaited_once()


# ── AsyncPerformanceMonitor: checking streamed positions ──

def _streamed(bid: float, position_id: str = "p1") -> dict:
    return {
        position_id: {
            "PositionId": position_id,
            "PositionBase": {**OPEN_POSITION["PositionBase"], "OpenPrice": 10.0},
            "PositionView": {"Bid": bid},
        }
    }


def _checking_monitor(tmp_path, *, today_pct=0.0, **kwargs):
    """A monitor for position checks: no realized profit today, per-tick log kept out of the repo."""
    monitor = _performance_monitor(**kwargs)
    monitor.logging_config = {"persistant": {"log_path": str(tmp_path)}}
    monitor.db_position_manager.get_percent_of_the_day = AsyncMock(return_value=today_pct)
    monitor.db_position_manager.get_max_position_percent = AsyncMock(return_value=0.0)
    return monitor


def test_stop_loss_sends_the_sell_and_records_the_closure_in_the_background(tmp_path):
    async def scenario():
        saxo_lists_the_closure = asyncio.Event()

        async def get_closed_positions(top=None):
            await saxo_lists_the_closure.wait()
            return {"Data": [CLOSED_POSITION]}

        monitor = _checking_monitor(tmp_path, closed_positions={"side_effect": get_closed_positions})
        with patch("src.trade.async_services.CLOSED_POSITION_BACKGROUND_LOOKUP_DELAYS", (0,)):
            first = await monitor.check_positions_from_stream(_streamed(8.4))  # -16%
            db_updates_when_returned = monitor.db_position_manager.update_turbo_position_data.await_count
            # Next price update, the closure not being recorded yet
            second = await monitor.check_positions_from_stream(_streamed(8.3))

            saxo_lists_the_closure.set()
            await monitor.wait_for_background_tasks()
        return monitor, first, second, db_updates_when_returned

    monitor, first, second, db_updates_when_returned = asyncio.run(scenario())

    assert first["closed_positions_processed"] == [{"id": "p1", "status": "Close order sent"}]
    assert db_updates_when_returned == 0
    assert second["closed_positions_processed"] == []
    monitor.order_service.place_market_order.assert_awaited_once()
    assert monitor.order_service.place_market_order.await_args.kwargs["buy_sell"] == "Sell"
    monitor.trading_rule.record_loss.assert_awaited_once()

    position_id, update = monitor.db_position_manager.update_turbo_position_data.await_args.args
    assert (position_id, update["position_status"]) == ("p1", "Closed")
    assert update["position_close_reason"].startswith("Performance (Stoploss")
    assert monitor._closing_position_ids == set()


def test_position_check_reads_the_db_once_per_check_and_max_performance_once_per_position(tmp_path):
    monitor = _checking_monitor(tmp_path)
    monitor.db_position_manager.get_open_positions_ids_actions = AsyncMock(
        return_value=[{"position_id": "p1", "action": "long"}, {"position_id": "p2", "action": "long"}]
    )

    async def scenario():
        for bid in (10.05, 10.05, 10.08):
            await monitor.check_positions_from_stream({**_streamed(bid, "p1"), **_streamed(bid, "p2")})

    asyncio.run(scenario())

    db = monitor.db_position_manager
    assert db.get_open_positions_ids_actions.await_count == 3
    assert db.get_percent_of_the_day.await_count == 3
    assert db.get_max_position_percent.await_count == 2
    # Saved when it rises (0.5% then 0.8%), not on the unchanged middle check
    assert [call.args for call in db.update_turbo_position_data.await_args_list] == [
        ("p1", {"position_max_performance_percent": 0.5}),
        ("p2", {"position_max_performance_percent": 0.5}),
        ("p1", {"position_max_performance_percent": 0.8}),
        ("p2", {"position_max_performance_percent": 0.8}),
    ]
    monitor.order_service.place_market_order.assert_not_awaited()


def test_stop_loss_still_fires_when_the_db_stops_answering(tmp_path):
    monitor = _checking_monitor(tmp_path)

    async def scenario():
        await monitor.check_positions_from_stream(_streamed(9.9))
        monitor.db_position_manager.get_open_positions_ids_actions.side_effect = ConnectionError("db down")
        monitor.db_position_manager.get_percent_of_the_day.side_effect = ConnectionError("db down")
        with patch("src.trade.async_services.CLOSED_POSITION_BACKGROUND_LOOKUP_DELAYS", (0,)):
            result = await monitor.check_positions_from_stream(_streamed(8.4))
            await monitor.wait_for_background_tasks()
        return result

    result = asyncio.run(scenario())

    assert result["closed_positions_processed"] == [{"id": "p1", "status": "Close order sent"}]
    monitor.order_service.place_market_order.assert_awaited_once()


def test_stop_loss_still_fires_when_the_db_hangs(tmp_path):
    monitor = _checking_monitor(tmp_path)

    async def hang(*args, **kwargs):
        await asyncio.sleep(30)

    async def scenario():
        # A healthy first check, on a rising price so that a max-perf save is due later
        await monitor.check_positions_from_stream(_streamed(10.05))

        # Then every database call hangs: reads, writes, and the cooldown record
        db = monitor.db_position_manager
        for query in (db.get_open_positions_ids_actions, db.get_percent_of_the_day,
                      db.get_max_position_percent, db.update_turbo_position_data,
                      monitor.trading_rule.record_loss):
            query.side_effect = hang

        with patch("src.trade.async_services.MANAGED_STATE_DB_TIMEOUT_SECONDS", 0.05):
            still_up = await asyncio.wait_for(monitor.check_positions_from_stream(_streamed(10.08)), timeout=1)
            started = time.monotonic()
            result = await asyncio.wait_for(monitor.check_positions_from_stream(_streamed(8.4)), timeout=1)
            elapsed = time.monotonic() - started
        for task in monitor._background_tasks:
            task.cancel()
        return still_up, result, elapsed

    still_up, result, elapsed = asyncio.run(scenario())

    assert still_up["closed_positions_processed"] == []
    assert result["closed_positions_processed"] == [{"id": "p1", "status": "Close order sent"}]
    monitor.order_service.place_market_order.assert_awaited_once()
    # The DB was found hanging by the previous check: this one doesn't wait for it at all
    assert elapsed < 0.04


def test_db_is_tried_again_once_the_retry_delay_has_passed(tmp_path):
    monitor = _checking_monitor(tmp_path)
    db = monitor.db_position_manager

    async def scenario():
        await monitor.check_positions_from_stream(_streamed(9.9))
        db.get_open_positions_ids_actions.side_effect = ConnectionError("db down")
        await monitor.check_positions_from_stream(_streamed(9.9))   # finds the DB down
        reads_when_found_down = db.get_open_positions_ids_actions.await_count
        await monitor.check_positions_from_stream(_streamed(9.9))   # leaves it alone
        reads_while_backing_off = db.get_open_positions_ids_actions.await_count

        db.get_open_positions_ids_actions.side_effect = None
        with patch("src.trade.async_services.DB_RETRY_AFTER_FAILURE_SECONDS", 0):
            monitor._mark_db_unavailable()                          # the delay is over
            await monitor.check_positions_from_stream(_streamed(9.9))
        return reads_when_found_down, reads_while_backing_off

    reads_when_found_down, reads_while_backing_off = asyncio.run(scenario())

    assert reads_when_found_down == reads_while_backing_off == 2
    assert db.get_open_positions_ids_actions.await_count == 3


def test_two_concurrent_position_checks_sell_a_position_only_once(tmp_path):
    monitor = _checking_monitor(tmp_path)

    async def scenario():
        with patch("src.trade.async_services.CLOSED_POSITION_BACKGROUND_LOOKUP_DELAYS", (0,)):
            results = await asyncio.gather(
                monitor.check_positions_from_stream(_streamed(8.4)),
                monitor.check_positions_from_stream(_streamed(8.4)),
            )
            await monitor.wait_for_background_tasks()
        return results

    results = asyncio.run(scenario())

    monitor.order_service.place_market_order.assert_awaited_once()
    assert sorted(len(result["closed_positions_processed"]) for result in results) == [0, 1]


def test_first_position_check_fails_when_the_db_cannot_be_read(tmp_path):
    monitor = _checking_monitor(tmp_path)
    monitor.db_position_manager.get_open_positions_ids_actions.side_effect = ConnectionError("db down")

    with pytest.raises(ConnectionError):
        asyncio.run(monitor.check_positions_from_stream(_streamed(8.4)))

    monitor.order_service.place_market_order.assert_not_awaited()


def test_max_performance_that_could_not_be_saved_is_saved_once_the_db_is_tried_again(tmp_path):
    monitor = _checking_monitor(tmp_path)
    monitor.db_position_manager.update_turbo_position_data.side_effect = [ConnectionError("db down"), None]

    async def scenario():
        failed = await monitor.check_positions_from_stream(_streamed(10.05))
        backing_off = await monitor.check_positions_from_stream(_streamed(10.05))
        with patch("src.trade.async_services.DB_RETRY_AFTER_FAILURE_SECONDS", 0):
            monitor._mark_db_unavailable()  # the retry delay is over
            retried = await monitor.check_positions_from_stream(_streamed(10.05))
        afterwards = await monitor.check_positions_from_stream(_streamed(10.05))
        return failed, backing_off, retried, afterwards

    failed, backing_off, retried, afterwards = asyncio.run(scenario())

    assert (failed["errors"], failed["db_updates"]) == (1, [])
    assert backing_off["db_updates"] == []
    assert retried["db_updates"] == [("p1", {"position_max_performance_percent": 0.5})]
    assert afterwards["db_updates"] == []
    assert monitor.db_position_manager.update_turbo_position_data.await_count == 2


def test_trailing_stop_uses_the_peak_seen_by_earlier_checks(tmp_path):
    monitor = _checking_monitor(
        tmp_path,
        position_management={
            "performance_thresholds": {
                "stoploss_percent": -15,
                "max_profit_percent": 60,
                "trailing_stop": {"enabled": True, "activation_percent": 5, "drawdown_percent": 8},
            }
        },
    )
    monitor.percent_profit_wanted = 100  # keep the daily profit target out of the way

    async def scenario():
        at_peak = await monitor.check_positions_from_stream(_streamed(11.0))   # +10%
        with patch("src.trade.async_services.CLOSED_POSITION_BACKGROUND_LOOKUP_DELAYS", (0,)):
            after_drop = await monitor.check_positions_from_stream(_streamed(10.1))  # +1%: 9 points below the peak
            await monitor.wait_for_background_tasks()
        return at_peak, after_drop

    at_peak, after_drop = asyncio.run(scenario())

    # The DB mock keeps answering 0% as the stored peak: only the peak kept in
    # memory by the first check can reach the 5% activation level.
    assert at_peak["closed_positions_processed"] == []
    assert after_drop["closed_positions_processed"] == [{"id": "p1", "status": "Close order sent"}]
    reason = monitor.db_position_manager.update_turbo_position_data.await_args.args[1]["position_close_reason"]
    assert "Trailing Stop: peak 10.0%" in reason


def test_daily_profit_target_uses_todays_realized_percent(tmp_path):
    monitor = _checking_monitor(tmp_path, today_pct=0.7)

    async def scenario():
        with patch("src.trade.async_services.CLOSED_POSITION_BACKGROUND_LOOKUP_DELAYS", (0,)):
            below = await monitor.check_positions_from_stream(_streamed(10.02))  # 0.7% then +0.2% = 0.9%
            reached = await monitor.check_positions_from_stream(_streamed(10.04))  # 0.7% then +0.4% = 1.1%
            await monitor.wait_for_background_tasks()
        return below, reached

    below, reached = asyncio.run(scenario())

    assert below["closed_positions_processed"] == []
    assert reached["closed_positions_processed"] == [{"id": "p1", "status": "Close order sent"}]


# ── AsyncPerformanceMonitor: reconciling the DB with Saxo ──

def test_reconciliation_does_not_call_saxo_when_nothing_is_open_in_the_db():
    monitor = _performance_monitor()
    monitor.db_position_manager.get_open_positions_ids = AsyncMock(return_value=[])

    result = asyncio.run(monitor.sync_db_positions_with_api())

    assert result == {"updates_for_db": []}
    monitor.position_service.get_open_positions.assert_not_awaited()
    monitor.position_service.get_closed_positions.assert_not_awaited()


def test_reconciliation_with_known_open_positions_only_asks_saxo_about_the_missing_one():
    monitor = _performance_monitor()

    result = asyncio.run(monitor.sync_db_positions_with_api(open_position_ids=set()))

    monitor.position_service.get_open_positions.assert_not_awaited()
    monitor.position_service.get_closed_positions.assert_awaited_once()
    (position_id, update), = result["updates_for_db"]
    assert position_id == "p1"
    assert update["position_status"] == "Closed"
    assert update["position_close_reason"] == "SaxoAPI"


def test_reconciliation_still_open_position_needs_no_update():
    monitor = _performance_monitor()

    result = asyncio.run(monitor.sync_db_positions_with_api(open_position_ids={"p1"}))

    assert result == {"updates_for_db": []}
    monitor.position_service.get_closed_positions.assert_not_awaited()


def test_reconciliation_asks_saxo_for_open_positions_when_none_are_given():
    monitor = _performance_monitor(open_positions={"return_value": {"Data": []}})

    result = asyncio.run(monitor.sync_db_positions_with_api())

    monitor.position_service.get_open_positions.assert_awaited_once()
    assert [position_id for position_id, _ in result["updates_for_db"]] == ["p1"]


def test_reconciliation_leaves_a_closure_in_flight_to_its_own_bookkeeping():
    monitor = _performance_monitor()
    monitor._closing_position_ids.add("p1")

    result = asyncio.run(monitor.sync_db_positions_with_api(open_position_ids=set()))

    assert result == {"updates_for_db": []}
    monitor.position_service.get_closed_positions.assert_not_awaited()


def test_reconciliation_records_a_loss_for_a_position_closed_at_a_loss():
    closed_at_a_loss = {
        **CLOSED_POSITION,
        "ClosedPosition": {**CLOSED_POSITION["ClosedPosition"], "ClosingPrice": 7.0},
    }
    monitor = _performance_monitor(closed_positions={"return_value": {"Data": [closed_at_a_loss]}})

    result = asyncio.run(monitor.sync_db_positions_with_api(open_position_ids=set()))

    assert result["updates_for_db"][0][1]["position_total_performance_percent"] == -30.0
    monitor.trading_rule.record_loss.assert_awaited_once()


def test_reconciliation_records_no_loss_for_a_position_closed_at_a_profit():
    monitor = _performance_monitor()

    asyncio.run(monitor.sync_db_positions_with_api(open_position_ids=set()))

    monitor.trading_rule.record_loss.assert_not_awaited()


# ── Broker-side protective stop ──

def test_tick_helpers():
    assert stop_order_tick_size(TURBO_DETAILS, 4.2) == 0.01
    assert stop_order_tick_size(TURBO_DETAILS, 7.07) == 0.05
    assert stop_order_tick_size(TURBO_DETAILS, 80) == 0.1
    assert stop_order_tick_size({"TickSizeStopOrder": 0.005, "TickSize": 0.01}, 7.07) == 0.005
    assert stop_order_tick_size({}, 7.07) is None

    assert round_down_to_tick(7.07, 0.05) == 7.05
    assert round_down_to_tick(10.1 * 0.7, 0.01) == 7.07  # 7.069999999999999 in floating point
    assert round_down_to_tick(7.069999, 0.01) == 7.06
    assert round_down_to_tick(7.05, 0.05) == 7.05
    assert round_down_to_tick(0.2979, 0.001) == 0.297


def _trade_result() -> dict:
    return {
        "position_details": {"position_id": "position-1", "position_amount": 98, "position_open_price": 10.1},
        "selected_turbo_info": WATCHLIST_TURBO,
    }


def test_protective_stop_is_not_placed_unless_enabled(tmp_path):
    orchestrator, _ = _orchestrator(tmp_path)

    result = asyncio.run(orchestrator.place_protective_stop(_trade_result()))

    assert result is None
    orchestrator.instrument_service.get_instrument_details.assert_not_awaited()
    orchestrator.order_service.place_related_stop_order.assert_not_awaited()


def test_protective_stop_is_placed_below_the_open_price_on_a_valid_tick(tmp_path):
    orchestrator, _ = _orchestrator(tmp_path, protective_stop={"enabled": True, "percent": -30})

    result = asyncio.run(orchestrator.place_protective_stop(_trade_result()))

    # 10.1 - 30% = 7.07, and prices between 5 and 50 move in steps of 0.05
    assert result == {"order_id": "stop-1", "price": 7.05, "percent": -30}
    orchestrator.order_service.place_related_stop_order.assert_awaited_once_with(
        position_id="position-1",
        uic=101,
        asset_type="WarrantKnockOut",
        amount=98,
        stop_price=7.05,
        order_type="StopIfTraded",
    )
    orchestrator.db_position_manager.update_turbo_position_data.assert_awaited_once_with(
        "position-1", {"related_order_id": ["stop-1"]},
    )


def test_protective_stop_falls_back_to_the_instrument_decimals_without_tick_data(tmp_path):
    orchestrator, _ = _orchestrator(tmp_path, protective_stop={"enabled": True, "percent": -30})
    orchestrator.instrument_service.get_instrument_details.return_value = {"SupportedOrderTypes": ["StopIfTraded"]}

    result = asyncio.run(orchestrator.place_protective_stop(_trade_result()))

    assert result["price"] == 7.07


def test_protective_stop_is_not_sent_when_the_instrument_does_not_support_the_order_type(tmp_path):
    orchestrator, _ = _orchestrator(tmp_path, protective_stop={"enabled": True, "percent": -30})
    orchestrator.instrument_service.get_instrument_details.return_value = {"SupportedOrderTypes": ["Market", "Limit"]}

    result = asyncio.run(orchestrator.place_protective_stop(_trade_result()))

    assert "StopIfTraded orders are not supported" in result["error"]
    orchestrator.order_service.place_related_stop_order.assert_not_awaited()


def test_protective_stop_rejected_by_saxo_is_reported_not_raised(tmp_path):
    orchestrator, _ = _orchestrator(tmp_path, protective_stop={"enabled": True, "percent": -30})
    orchestrator.order_service.place_related_stop_order.side_effect = OrderPlacementError("Saxo rejected order (400)")

    result = asyncio.run(orchestrator.place_protective_stop(_trade_result()))

    assert result == {"error": "OrderPlacementError: Saxo rejected order (400)"}
    orchestrator.db_position_manager.update_turbo_position_data.assert_not_awaited()


def test_related_stop_order_is_attached_to_the_position():
    api_client = MagicMock()
    api_client.request = AsyncMock(return_value={"Orders": [{"OrderId": "stop-1"}]})
    order_service = AsyncOrderService(api_client, "account-1", "client-1")

    order_id = asyncio.run(
        order_service.place_related_stop_order(
            position_id="position-1", uic=101, asset_type="WarrantKnockOut",
            amount=98, stop_price=7.05, order_type="StopIfTraded",
        )
    )

    assert order_id == "stop-1"
    assert api_client.request.await_args.args[0].data == {
        "PositionId": "position-1",
        "Orders": [
            {
                "Uic": 101,
                "AssetType": "WarrantKnockOut",
                "Amount": 98,
                "BuySell": "Sell",
                "OrderType": "StopIfTraded",
                "OrderPrice": 7.05,
                "OrderDuration": {"DurationType": "GoodTillCancel"},
                "ManualOrder": False,
                "AccountKey": "account-1",
            }
        ],
    }


def test_related_stop_order_without_an_order_id_in_the_answer_is_an_error():
    api_client = MagicMock()
    api_client.request = AsyncMock(return_value={})
    order_service = AsyncOrderService(api_client, "account-1", "client-1")

    with pytest.raises(OrderPlacementError):
        asyncio.run(
            order_service.place_related_stop_order(
                position_id="position-1", uic=101, asset_type="WarrantKnockOut",
                amount=98, stop_price=7.05, order_type="StopIfTraded",
            )
        )


def test_instrument_details_are_fetched_once_per_instrument(mock_config_manager, mock_api_client):
    mock_api_client.request = AsyncMock(return_value=TURBO_DETAILS)
    service = AsyncInstrumentService(mock_api_client, mock_config_manager, "account_key")

    async def scenario():
        first = await service.get_instrument_details(101, "WarrantKnockOut")
        second = await service.get_instrument_details(101, "WarrantKnockOut")
        return first, second

    first, second = asyncio.run(scenario())

    assert first == second == TURBO_DETAILS
    mock_api_client.request.assert_awaited_once()


# ── AsyncPerformanceMonitor: what the admin interface relies on ──

def test_position_check_remembers_the_latest_price_and_performance(tmp_path):
    async def scenario():
        monitor = _checking_monitor(tmp_path)
        # Both below every exit threshold: the position stays open between the two checks.
        await monitor.check_positions_from_stream(_streamed(10.05))
        first = monitor.live_values()
        await monitor.check_positions_from_stream(_streamed(10.08))
        return monitor, first

    monitor, first = asyncio.run(scenario())

    assert first["p1"][:2] == (10.05, 0.5)
    bid, performance, observed_at = monitor.live_values()["p1"]
    assert (bid, performance) == (10.08, 0.8)
    assert observed_at.tzinfo is not None
    assert observed_at >= first["p1"][2]


def test_live_values_are_a_copy_that_callers_cannot_change(tmp_path):
    async def scenario():
        monitor = _checking_monitor(tmp_path)
        await monitor.check_positions_from_stream(_streamed(10.05))
        monitor.live_values().clear()
        return monitor

    assert "p1" in asyncio.run(scenario()).live_values()


def test_live_value_of_a_position_that_is_no_longer_managed_is_dropped(tmp_path):
    async def scenario():
        monitor = _checking_monitor(tmp_path)
        await monitor.check_positions_from_stream(_streamed(10.05))
        # Closed and recorded as such: the DB no longer lists it as open.
        monitor.db_position_manager.get_open_positions_ids_actions = AsyncMock(return_value=[])
        await monitor.check_positions_from_stream(_streamed(10.06))
        return monitor

    assert asyncio.run(scenario()).live_values() == {}


def test_live_values_are_forgotten_for_positions_that_left_the_stream(tmp_path):
    async def scenario():
        monitor = _checking_monitor(tmp_path)
        monitor.db_position_manager.get_open_positions_ids_actions = AsyncMock(
            return_value=[{"position_id": "p1", "action": "long"}, {"position_id": "p2", "action": "short"}]
        )
        await monitor.check_positions_from_stream({**_streamed(10.05, "p1"), **_streamed(10.03, "p2")})
        monitor.forget_live_values_except({"p2"})
        return monitor

    assert set(asyncio.run(scenario()).live_values()) == {"p2"}


def _two_open_positions_monitor():
    second = {"PositionId": "p2", "PositionBase": {**OPEN_POSITION["PositionBase"], "Uic": 202}}
    monitor = _performance_monitor(open_positions={"return_value": {"Data": [OPEN_POSITION, second]}})
    monitor.db_position_manager.get_open_positions_ids_actions = AsyncMock(
        return_value=[{"position_id": "p1", "action": "long"}, {"position_id": "p2", "action": "long"}]
    )
    return monitor


def test_closing_one_position_leaves_the_others_open():
    async def scenario():
        monitor = _two_open_positions_monitor()
        with patch("src.trade.async_services.CLOSED_POSITION_BACKGROUND_LOOKUP_DELAYS", (0,)):
            result = await monitor.close_managed_positions_by_criteria(only_position_id="p2", defer_bookkeeping=True)
            await asyncio.gather(*monitor._background_tasks)
        return monitor, result

    monitor, result = asyncio.run(scenario())

    assert result == {"closed_initiated_count": 1, "errors_count": 0, "closed_position_ids": ["p2"]}
    monitor.order_service.place_market_order.assert_awaited_once()
    assert monitor.order_service.place_market_order.await_args.kwargs["uic"] == 202


def test_closing_a_position_that_is_not_open_does_nothing():
    monitor = _two_open_positions_monitor()

    result = asyncio.run(monitor.close_managed_positions_by_criteria(only_position_id="unknown"))

    assert result == {"closed_initiated_count": 0, "errors_count": 0, "closed_position_ids": []}
    monitor.order_service.place_market_order.assert_not_awaited()


def test_single_position_close_is_recorded_with_its_own_reason():
    async def scenario():
        monitor = _performance_monitor()
        with patch("src.trade.async_services.asyncio.sleep", new_callable=AsyncMock):
            await monitor.close_managed_positions_by_criteria(only_position_id="p1")
        return monitor

    monitor = asyncio.run(scenario())

    _, update = monitor.db_position_manager.update_turbo_position_data.await_args.args
    assert update["position_close_reason"] == "Explicit Close (position p1)"
