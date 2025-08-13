import pytest
from unittest.mock import MagicMock, ANY

from src.trade.api_actions import TradingOrchestrator, InstrumentService, OrderService, PositionService
from src.database import DbOrderManager, DbPositionManager
from src.trade.exceptions import InsufficientFundsException, DatabaseOperationException
from tests.test_data_factory import TestDataFactory

@pytest.fixture
def mock_instrument_service():
    return MagicMock(spec=InstrumentService)

@pytest.fixture
def mock_order_service():
    return MagicMock(spec=OrderService)

@pytest.fixture
def mock_position_service():
    return MagicMock(spec=PositionService)

@pytest.fixture
def trading_orchestrator(
    mock_instrument_service,
    mock_order_service,
    mock_position_service,
    mock_config_manager,
    mock_db_order_manager,
    mock_db_position_manager
):
    """Fixture for TradingOrchestrator."""
    return TradingOrchestrator(
        instrument_service=mock_instrument_service,
        order_service=mock_order_service,
        position_service=mock_position_service,
        config_manager=mock_config_manager,
        db_order_manager=mock_db_order_manager,
        db_position_manager=mock_db_position_manager,
    )

class TestTradingOrchestrator:

    def test_calculate_bid_amount_success(self, trading_orchestrator):
        # Arrange
        turbo_info = {"selected_instrument": {"latest_ask": 10, "decimals": 2}}
        trading_orchestrator.safety_margins = {"bid_calculation": 1}
        trading_orchestrator.buying_power_config = {"max_account_funds_to_use_percentage": 100}


        # Act
        amount = trading_orchestrator._calculate_bid_amount(turbo_info, 1000)

        # Assert
        # (1000 / 10) = 100 units. 100 - 1 (safety) = 99.
        assert amount == 99

    def test_should_calculate_bid_amount_with_zero_spending_power(self, trading_orchestrator):
        """Test bid calculation with zero or negative spending power."""
        # Arrange
        turbo_info = {"selected_instrument": {"latest_ask": 10, "decimals": 2}}

        # Act & Assert
        with pytest.raises(InsufficientFundsException):
            trading_orchestrator._calculate_bid_amount(turbo_info, 0)

        with pytest.raises(InsufficientFundsException):
            trading_orchestrator._calculate_bid_amount(turbo_info, -100)

    def test_calculate_bid_amount_invalid_ask_price(self, trading_orchestrator):
        # Arrange
        turbo_info = {"selected_instrument": {"latest_ask": None, "decimals": 2}}

        # Act & Assert
        with pytest.raises(ValueError, match="Invalid ask price for bid calculation"):
            trading_orchestrator._calculate_bid_amount(turbo_info, 1000)

    def test_execute_trade_signal_happy_path(self, trading_orchestrator, mock_instrument_service, mock_position_service, mock_order_service, mock_db_order_manager, mock_db_position_manager):
        # Arrange
        # The find_turbos function constructs a specific dictionary structure.
        # The orchestrator test must mock this structure accurately.
        turbo_info = {
            "selected_instrument": {
                "uic": 123,
                "asset_type": "WarrantKnockOut",
                "description": "Test Turbo",
                "latest_ask": 10.0, # This is the key the orchestrator uses
                "latest_bid": 9.9,
                "decimals": 2,
                "symbol": "TSTO",
                "currency": "EUR",
                "commissions": {}
            }
        }
        order_response = TestDataFactory.create_order_response(order_id="order1")
        position_data = TestDataFactory.create_saxo_position(position_id="pos1")

        mock_instrument_service.find_turbos.return_value = turbo_info
        mock_position_service.get_spending_power.return_value = 1000
        mock_order_service.place_market_order.return_value = order_response
        mock_position_service.find_position_by_order_id_with_retry.return_value = position_data

        # Act
        result = trading_orchestrator.execute_trade_signal("e1", "u1", "long")

        # Assert
        assert result is not None
        assert result['message'] == "Successfully executed and recorded trade for long."
        mock_instrument_service.find_turbos.assert_called_once_with("e1", "u1", "long")
        mock_position_service.get_spending_power.assert_called_once()
        mock_order_service.place_market_order.assert_called_once()
        mock_position_service.find_position_by_order_id_with_retry.assert_called_once_with("order1")
        mock_db_order_manager.insert_turbo_order_data.assert_called_once()
        mock_db_position_manager.insert_turbo_open_position_data.assert_called_once()

    def test_should_handle_position_found_but_db_insert_fails(self, trading_orchestrator, mock_instrument_service, mock_position_service, mock_order_service, mock_db_order_manager):
        """Test critical scenario: trade executes but DB persistence fails."""
        # Arrange
        turbo_info = {
            "selected_instrument": {
                "uic": 123,
                "asset_type": "WarrantKnockOut",
                "description": "Test Turbo",
                "latest_ask": 10.0,
                "latest_bid": 9.9,
                "decimals": 2,
                "symbol": "TSTO",
                "currency": "EUR",
                "commissions": {}
            }
        }
        order_response = TestDataFactory.create_order_response(order_id="order1")
        position_data = TestDataFactory.create_saxo_position(position_id="pos1")

        mock_instrument_service.find_turbos.return_value = turbo_info
        mock_position_service.get_spending_power.return_value = 1000
        mock_order_service.place_market_order.return_value = order_response
        mock_position_service.find_position_by_order_id_with_retry.return_value = position_data

        # Simulate a database error on the first insert
        mock_db_order_manager.insert_turbo_order_data.side_effect = Exception("DB Connection Error")

        # Act & Assert
        with pytest.raises(DatabaseOperationException) as excinfo:
            trading_orchestrator.execute_trade_signal("e1", "u1", "long")

        # Assert that the exception is correctly propagated
        assert "CRITICAL: Failed to persist executed trade" in str(excinfo.value)
        assert excinfo.value.operation == "insert_trade_data"
        assert excinfo.value.entity_id == "order1"

        # Ensure the order was placed but the DB insert was the point of failure
        mock_order_service.place_market_order.assert_called_once()
        mock_db_order_manager.insert_turbo_order_data.assert_called_once()
        # The position insert should not be called if the order insert fails
        trading_orchestrator.db_position_manager.insert_turbo_open_position_data.assert_not_called()
