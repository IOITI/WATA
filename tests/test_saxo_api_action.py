import pytest
from unittest.mock import patch, MagicMock
from src.trade.api_actions import TradingOrchestrator, InstrumentService, OrderService, PositionService, SaxoApiClient
from src.configuration import ConfigurationManager
from src.database import DbOrderManager, DbPositionManager
from src.saxo_authen import SaxoAuth


@pytest.fixture
def trading_orchestrator():
    config_manager = MagicMock(spec=ConfigurationManager)
    def config_side_effect(key, default=None):
        if key == "saxo_auth.env":
            return "simulation"
        if key == "trade.config.buying_power":
            return {"safety_margins": {"bid_calculation": 1}, "max_account_funds_to_use_percentage": 100}
        return MagicMock()

    config_manager.get_config_value.side_effect = config_side_effect
    db_order_manager = MagicMock(spec=DbOrderManager)
    db_position_manager = MagicMock(spec=DbPositionManager)
    saxo_auth = MagicMock(spec=SaxoAuth)
    api_client = SaxoApiClient(config_manager, saxo_auth)
    instrument_service = InstrumentService(api_client, config_manager, "account_key")
    order_service = OrderService(api_client, "account_key", "client_key")
    position_service = PositionService(api_client, order_service, config_manager, "account_key", "client_key")
    return TradingOrchestrator(instrument_service, order_service, position_service, config_manager, db_order_manager, db_position_manager)


@patch('src.trade.api_actions.PositionService.get_spending_power')
def test_calcul_bid_amount(mock_get_spending_power, trading_orchestrator):
    # Mock the response from the Saxo API
    mock_get_spending_power.return_value = 1000
    founded_turbo = {
        "selected_instrument": {
            "latest_ask": 10,
            "decimals": 2
        }
    }

    # Call the method
    amount = trading_orchestrator._calculate_bid_amount(founded_turbo, 1000)

    # Assert the expected amount
    assert amount == 99  # (1000 / 10) - 1 = 99