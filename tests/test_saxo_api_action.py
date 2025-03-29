import pytest
from unittest.mock import patch, MagicMock
from src.trade.api_actions import SaxoService
from src.configuration import ConfigurationManager
from src.database import DbOrderManager, DbPositionManager, DbTradePerformanceManager, DbStrategySignalStatsManager
from src.rabbit_connection import RabbitConnection
from src.trading_rule import TradingRule

@pytest.fixture
def saxo_service():
    config_manager = MagicMock(spec=ConfigurationManager)
    db_order_manager = MagicMock(spec=DbOrderManager)
    db_position_manager = MagicMock(spec=DbPositionManager)
    rabbit_connection = MagicMock(spec=RabbitConnection)
    trading_rule = MagicMock(spec=TradingRule)
    return SaxoService(config_manager, db_order_manager, db_position_manager, rabbit_connection, trading_rule)

@patch('src.trade.api_actions.pf.balances.AccountBalances')
@patch('src.trade.api_actions.SaxoService.saxo_client')
def test_calcul_bid_amount(mock_saxo_client, mock_account_balances, saxo_service):
    # Mock the response from the Saxo API
    mock_saxo_client.request.return_value = {"SpendingPower": 1000}
    founded_turbo = {
        "price": {
            "Quote": {"Ask": 10}
        }
    }

    # Call the method
    amount = saxo_service.calcul_bid_amount(founded_turbo)

    # Assert the expected amount
    assert amount == 99  # (1000 / 10) - 1 = 99