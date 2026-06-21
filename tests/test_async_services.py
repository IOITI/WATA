import asyncio
import sys
import types

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.configuration import ConfigurationManager

asyncpg_stub = types.ModuleType("asyncpg")
asyncpg_stub.Pool = type("Pool", (), {})
asyncpg_stub.Record = dict
asyncpg_stub.create_pool = AsyncMock()
sys.modules.setdefault("asyncpg", asyncpg_stub)

from src.trade.async_services import AsyncInstrumentService


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