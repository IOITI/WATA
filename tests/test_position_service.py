import pytest
from unittest.mock import patch, MagicMock

from src.trade.api_actions import PositionService, OrderService
from src.trade.exceptions import PositionNotFoundException, SaxoApiError
from tests.test_data_factory import TestDataFactory

@pytest.fixture
def order_service(mock_api_client):
    """Fixture for a mocked OrderService."""
    return MagicMock(spec=OrderService)

@pytest.fixture
def position_service(mock_api_client, order_service, mock_config_manager):
    """Fixture for PositionService."""
    return PositionService(mock_api_client, order_service, mock_config_manager, "account_key", "client_key")

class TestPositionService:

    def test_get_open_positions_success(self, position_service, mock_api_client):
        # Arrange
        position_data = TestDataFactory.create_saxo_position(position_id="pos1")
        mock_api_client.request.return_value = {"Data": [position_data]}

        # Act
        result = position_service.get_open_positions()

        # Assert
        assert result["__count"] == 1
        assert result["Data"][0]["PositionId"] == "pos1"

    def test_get_open_positions_empty(self, position_service, mock_api_client):
        # Arrange
        mock_api_client.request.return_value = {"Data": []}

        # Act
        result = position_service.get_open_positions()

        # Assert
        assert result["__count"] == 0
        assert result["Data"] == []

    def test_get_spending_power_success(self, position_service, mock_api_client):
        # Arrange
        mock_api_client.request.return_value = {"SpendingPower": 50000.0}

        # Act
        result = position_service.get_spending_power()

        # Assert
        assert result == 50000.0

    def test_get_spending_power_missing_key(self, position_service, mock_api_client):
        # Arrange
        mock_api_client.request.return_value = {"SomeOtherKey": 123}

        # Act & Assert
        with pytest.raises(SaxoApiError, match="Invalid balance response received, missing SpendingPower"):
            position_service.get_spending_power()

    def test_get_spending_power_invalid_value(self, position_service, mock_api_client):
        # Arrange
        mock_api_client.request.return_value = {"SpendingPower": "not-a-number"}

        # Act & Assert
        with pytest.raises(SaxoApiError, match="Invalid SpendingPower value received"):
            position_service.get_spending_power()

    def test_get_spending_power_handles_string_value(self, position_service, mock_api_client):
        """Test that spending power is correctly parsed even if returned as a string."""
        # Arrange
        mock_api_client.request.return_value = {"SpendingPower": "50000.0"}

        # Act
        result = position_service.get_spending_power()

        # Assert
        assert result == 50000.0
        assert isinstance(result, float)

    @patch.object(PositionService, 'get_open_positions')
    def test_find_position_by_order_id_with_retry_found_first_try(self, mock_get_open_positions, position_service):
        # Arrange
        position_data = TestDataFactory.create_saxo_position(order_id="order1", position_id="pos1")
        mock_get_open_positions.return_value = {"Data": [position_data]}

        # Act
        result = position_service.find_position_by_order_id_with_retry("order1")

        # Assert
        assert result["PositionId"] == "pos1"
        mock_get_open_positions.assert_called_once()

    @patch('time.sleep', return_value=None)
    @patch.object(PositionService, 'get_open_positions')
    def test_should_cleanup_orphaned_order_on_position_confirmation_timeout(self, mock_get_open_positions, mock_sleep, position_service, order_service):
        """
        This test covers the critical scenario where a position is not found after retries,
        and an order cancellation should be attempted.
        """
        # Arrange
        # Simulate get_open_positions always returning an empty list
        mock_get_open_positions.return_value = {"Data": []}
        # Simulate that the order cancellation is successful
        order_service.cancel_order.return_value = True
        # The retry decorator uses a module-level constant (5), so we can't override it here.
        # The test must assert against the actual behavior.

        # Act & Assert
        with pytest.raises(PositionNotFoundException) as excinfo:
            position_service.find_position_by_order_id_with_retry("orphan_order1")

        # Assert on the exception details
        assert "Position not found after 5 retries" in str(excinfo.value)
        assert "Successfully cancelled potentially orphan order" in str(excinfo.value)
        assert excinfo.value.cancellation_attempted is True
        assert excinfo.value.cancellation_succeeded is True

        # Assert that the mocks were called as expected
        assert mock_get_open_positions.call_count == 5 # DEFAULT_RETRY_ATTEMPTS is 5
        order_service.cancel_order.assert_called_once_with("orphan_order1")

    @patch('time.sleep', return_value=None)
    @patch.object(PositionService, 'get_open_positions')
    def test_find_position_by_order_id_with_retry_not_found_and_cancel_fail(self, mock_get_open_positions, mock_sleep, position_service, order_service):
        # Arrange
        mock_get_open_positions.return_value = {"Data": []}
        order_service.cancel_order.return_value = False # Simulate cancellation failure
        # The retry decorator uses a module-level constant (5), so we can't override it here.

        # Act & Assert
        with pytest.raises(PositionNotFoundException) as excinfo:
            position_service.find_position_by_order_id_with_retry("order1")

        assert "Failed to cancel potentially orphan order" in str(excinfo.value)
        assert excinfo.value.cancellation_succeeded is False
        assert mock_get_open_positions.call_count == 5 # DEFAULT_RETRY_ATTEMPTS is 5
        order_service.cancel_order.assert_called_once_with("order1")
