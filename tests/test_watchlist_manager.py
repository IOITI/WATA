import asyncio
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.configuration import ConfigurationManager

asyncpg_stub = types.ModuleType("asyncpg")
asyncpg_stub.Pool = type("Pool", (), {})
asyncpg_stub.Record = dict
asyncpg_stub.create_pool = AsyncMock()
sys.modules.setdefault("asyncpg", asyncpg_stub)

from src.watchlist_manager.manager import TurboWatchlistManager, load_allowed_indices


@pytest.fixture
def mock_config_manager():
    manager = MagicMock(spec=ConfigurationManager)

    def get_config_value(key, default=None):
        configs = {
            "trade.rules": [
                {
                    "rule_type": "allowed_indices",
                    "rule_config": {"indice_ids": {"us100": "1909050"}},
                }
            ],
            "trade.config.watchlist_manager": {
                "refresh_interval_seconds": 300,
                "stale_after_seconds": 330,
                "startup_refresh": False,
                "directions": ["long", "short"],
            },
        }
        return configs.get(key, default)

    manager.get_config_value.side_effect = get_config_value
    return manager


def test_load_allowed_indices(mock_config_manager):
    assert load_allowed_indices(mock_config_manager) == {"us100": "1909050"}


def test_refresh_all_populates_cache(mock_config_manager):
    instrument_service = MagicMock()
    instrument_service.turbo_price_range = {"min": 4, "max": 15}
    instrument_service.build_watchlist_candidates = AsyncMock(
        side_effect=[
            {
                "input_criteria": {"exchange_id": "exchange1", "underlying_uics": "1909050", "keywords": "long"},
                "selected_result": {
                    "selected_instrument": {
                        "uic": 101,
                        "asset_type": "WarrantKnockOut",
                        "latest_ask": 10.1,
                    }
                },
                "selected_source_lookup": {1: {"Identifier": 1, "Description": "Turbo 1"}},
                "candidate_snapshots": [
                    {
                        "Uic": 101,
                        "Identifier": 1,
                        "AssetType": "WarrantKnockOut",
                        "DisplayAndFormat": {"Description": "Turbo 1", "Symbol": "T1", "Currency": "EUR", "OrderDecimals": 2},
                        "Commissions": {"CostBuy": 0.25},
                        "Quote": {"Bid": 10.0, "Ask": 10.1, "PriceTypeAsk": "Tradable", "PriceTypeBid": "Tradable", "MarketState": "Open"},
                    }
                ],
                "subscription_groups": [{"asset_type": "WarrantKnockOut", "uics": [101]}],
            },
            {
                "input_criteria": {"exchange_id": "exchange1", "underlying_uics": "1909050", "keywords": "short"},
                "selected_result": {
                    "selected_instrument": {
                        "uic": 102,
                        "asset_type": "WarrantKnockOut",
                        "latest_ask": 9.9,
                    }
                },
                "selected_source_lookup": {2: {"Identifier": 2, "Description": "Turbo 2"}},
                "candidate_snapshots": [
                    {
                        "Uic": 102,
                        "Identifier": 2,
                        "AssetType": "WarrantKnockOut",
                        "DisplayAndFormat": {"Description": "Turbo 2", "Symbol": "T2", "Currency": "EUR", "OrderDecimals": 2},
                        "Commissions": {"CostBuy": 0.30},
                        "Quote": {"Bid": 9.8, "Ask": 9.9, "PriceTypeAsk": "Tradable", "PriceTypeBid": "Tradable", "MarketState": "Open"},
                    }
                ],
                "subscription_groups": [{"asset_type": "WarrantKnockOut", "uics": [102]}],
            },
        ]
    )
    stream_client = MagicMock()
    stream_client.set_subscriptions = AsyncMock()
    stream_client.active_subscription_count = 0
    stream_client.desired_subscription_count = 0

    manager = TurboWatchlistManager(
        instrument_service=instrument_service,
        config_manager=mock_config_manager,
        exchange_id="exchange1",
        allowed_indices={"us100": "1909050"},
        stream_client=stream_client,
    )

    asyncio.run(manager.refresh_all())

    long_result = manager.get_cached_turbo(indice="us100", direction="long")
    short_result = manager.get_cached_turbo(underlying_uic="1909050", direction="short")

    assert long_result["selected_instrument"]["uic"] == 101
    assert short_result["selected_instrument"]["uic"] == 102
    assert long_result["watchlist"]["indice"] == "us100"
    assert long_result["watchlist"]["stale"] is False
    assert manager.get_health_snapshot()["warm_entries"] == 2
    assert instrument_service.build_watchlist_candidates.await_count == 2
    stream_client.set_subscriptions.assert_awaited_once()


def test_stream_update_reselects_best_turbo(mock_config_manager):
    instrument_service = MagicMock()
    instrument_service.turbo_price_range = {"min": 4, "max": 15}
    instrument_service.build_watchlist_candidates = AsyncMock(
        return_value={
            "input_criteria": {"exchange_id": "exchange1", "underlying_uics": "1909050", "keywords": "long"},
            "selected_result": {
                "selected_instrument": {
                    "uic": 101,
                    "asset_type": "WarrantKnockOut",
                    "description": "Turbo 1",
                    "symbol": "T1",
                    "currency": "EUR",
                    "latest_ask": 10.1,
                }
            },
            "selected_source_lookup": {
                1: {"Identifier": 1, "Description": "Turbo 1"},
                2: {"Identifier": 2, "Description": "Turbo 2"},
            },
            "candidate_snapshots": [
                {
                    "Uic": 101,
                    "Identifier": 1,
                    "AssetType": "WarrantKnockOut",
                    "DisplayAndFormat": {"Description": "Turbo 1", "Symbol": "T1", "Currency": "EUR", "OrderDecimals": 2},
                    "Commissions": {"CostBuy": 0.25},
                    "Quote": {"Bid": 10.0, "Ask": 10.1, "PriceTypeAsk": "Tradable", "PriceTypeBid": "Tradable", "MarketState": "Open"},
                },
                {
                    "Uic": 102,
                    "Identifier": 2,
                    "AssetType": "WarrantKnockOut",
                    "DisplayAndFormat": {"Description": "Turbo 2", "Symbol": "T2", "Currency": "EUR", "OrderDecimals": 2},
                    "Commissions": {"CostBuy": 0.30},
                    "Quote": {"Bid": 10.8, "Ask": 10.9, "PriceTypeAsk": "Tradable", "PriceTypeBid": "Tradable", "MarketState": "Open"},
                },
            ],
            "subscription_groups": [{"asset_type": "WarrantKnockOut", "uics": [101, 102]}],
        }
    )
    stream_client = MagicMock()
    stream_client.set_subscriptions = AsyncMock()
    stream_client.active_subscription_count = 1
    stream_client.desired_subscription_count = 1

    manager = TurboWatchlistManager(
        instrument_service=instrument_service,
        config_manager=mock_config_manager,
        exchange_id="exchange1",
        allowed_indices={"us100": "1909050"},
        stream_client=stream_client,
    )

    asyncio.run(manager.refresh_all())
    initial_result = manager.get_cached_turbo(indice="us100", direction="long")
    assert initial_result["selected_instrument"]["uic"] == 101

    reference_id = manager._build_subscription_reference_id("us100", "long", "WarrantKnockOut")
    asyncio.run(
        manager.handle_info_price_subscription_update(
            {
                "reference_id": reference_id,
                "indice": "us100",
                "underlying_uic": "1909050",
                "direction": "long",
                "asset_type": "WarrantKnockOut",
            },
            [
                {
                    "Uic": 101,
                    "Identifier": 1,
                    "AssetType": "WarrantKnockOut",
                    "DisplayAndFormat": {"Description": "Turbo 1", "Symbol": "T1", "Currency": "EUR", "OrderDecimals": 2},
                    "Commissions": {"CostBuy": 0.25},
                    "Quote": {"Bid": 10.4, "Ask": 10.5, "PriceTypeAsk": "Tradable", "PriceTypeBid": "Tradable", "MarketState": "Open"},
                },
                {
                    "Uic": 102,
                    "Identifier": 2,
                    "AssetType": "WarrantKnockOut",
                    "DisplayAndFormat": {"Description": "Turbo 2", "Symbol": "T2", "Currency": "EUR", "OrderDecimals": 2},
                    "Commissions": {"CostBuy": 0.30},
                    "Quote": {"Bid": 9.7, "Ask": 9.8, "PriceTypeAsk": "Tradable", "PriceTypeBid": "Tradable", "MarketState": "Open"},
                },
            ],
            "ctx-stream",
        )
    )

    updated_result = manager.get_cached_turbo(indice="us100", direction="long")
    assert updated_result["selected_instrument"]["uic"] == 102
    assert updated_result["selected_instrument"]["subscription_context_id"] == "ctx-stream"
    assert updated_result["selected_instrument"]["subscription_reference_id"] == reference_id