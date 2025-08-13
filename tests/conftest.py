import pytest
from unittest.mock import MagicMock

from src.configuration import ConfigurationManager
from src.database import DbOrderManager, DbPositionManager
from src.trade.rules import TradingRule
from src.saxo_authen import SaxoAuth
from src.trade.api_actions import SaxoApiClient

@pytest.fixture
def mock_config_manager():
    """A mock for ConfigurationManager with a flexible side_effect."""
    manager = MagicMock(spec=ConfigurationManager)

    def get_config_value(key, default=None):
        configs = {
            "saxo_auth.env": "simulation",
            "trade.config.general.api_limits": {"top_instruments": 200, "top_positions": 200, "top_closed_positions": 500},
            "trade.config.turbo_preference.price_range": {"min": 4, "max": 15},
            "trade.config.general.retry_config": {"max_retries": 5, "retry_sleep_seconds": 2},
            "trade.config.general.websocket": {"refresh_rate_ms": 10000},
            "trade.config.buying_power": {"safety_margins": {"bid_calculation": 1}, "max_account_funds_to_use_percentage": 100},
            "trade.config.position_management": {"performance_thresholds": {"stoploss_percent": -20, "max_profit_percent": 60}},
            "trade.config.general": {"timezone": "Europe/Paris"},
            "logging.persistant": {"log_path": "/tmp/logs"}
        }
        # Return specific value if key exists, otherwise the provided default
        return configs.get(key, default)

    manager.get_config_value.side_effect = get_config_value
    manager.get_logging_config.return_value = {"persistant": {"log_path": "/tmp/logs"}}
    return manager

@pytest.fixture
def mock_saxo_auth():
    """A mock for SaxoAuth."""
    auth = MagicMock(spec=SaxoAuth)
    auth.get_token.return_value = "test_token"
    return auth

@pytest.fixture
def mock_db_order_manager():
    """A mock for DbOrderManager."""
    return MagicMock(spec=DbOrderManager)

@pytest.fixture
def mock_db_position_manager():
    """A mock for DbPositionManager."""
    return MagicMock(spec=DbPositionManager)

@pytest.fixture
def mock_trading_rule():
    """A mock for TradingRule."""
    rule = MagicMock(spec=TradingRule)
    rule.get_rule_config.return_value = {"percent_profit_wanted_per_days": 1.0}
    return rule

@pytest.fixture
def mock_api_client():
    """
    A mock for the low-level SaxoApiClient.
    This fixture mocks the *wrapper* client, not the underlying SaxoOpenApiLib.
    This is useful for testing the services that *use* the client.
    """
    client = MagicMock(spec=SaxoApiClient)
    return client
