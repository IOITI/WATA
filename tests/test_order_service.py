import pytest
from unittest.mock import patch, MagicMock

from src.trade.api_actions import OrderService
from src.trade.exceptions import OrderPlacementError, ApiRequestException
from tests.test_data_factory import TestDataFactory

@pytest.fixture
def order_service(mock_api_client):
    """Fixture for OrderService."""
    return OrderService(mock_api_client, "account_key", "client_key")

class TestOrderService:

    def test_place_market_order_success(self, order_service, mock_api_client):
        # Arrange
        order_response = TestDataFactory.create_order_response(order_id="12345")
        mock_api_client.request.return_value = order_response

        # Act
        result = order_service.place_market_order(uic=1, asset_type="FxSpot", amount=100, buy_sell="Buy")

        # Assert
        assert result == order_response
        mock_api_client.request.assert_called_once()
        # Better validation: check the payload sent to the API
        call_args = mock_api_client.request.call_args[0][0]
        assert call_args.data['Uic'] == 1
        assert call_args.data['Amount'] == 100
        assert call_args.data['BuySell'] == "Buy"
        assert call_args.data['AccountKey'] == "account_key"

    def test_place_market_order_api_error(self, order_service, mock_api_client):
        # Arrange
        mock_api_client.request.side_effect = OrderPlacementError("API rejected order")

        # Act & Assert
        with pytest.raises(OrderPlacementError):
            order_service.place_market_order(uic=1, asset_type="FxSpot", amount=100, buy_sell="Buy")

    def test_place_market_order_missing_order_id(self, order_service, mock_api_client):
        """Test that an error is raised if the API response is missing the OrderId."""
        # Arrange
        # The response is successful, but malformed (missing OrderId)
        malformed_response = {"Status": "Success", "SomeOtherKey": "value"}
        mock_api_client.request.return_value = malformed_response

        # Act & Assert
        with pytest.raises(OrderPlacementError, match="Order placement response missing OrderId"):
            order_service.place_market_order(uic=1, asset_type="FxSpot", amount=100, buy_sell="Buy")

    def test_cancel_order_success(self, order_service, mock_api_client):
        # Arrange
        mock_api_client.request.return_value = {"Status": "Cancelled"} # Simulate a success response

        # Act
        result = order_service.cancel_order("123")

        # Assert
        assert result is True
        mock_api_client.request.assert_called_once()
        # The first argument of the first call to request() is the request object
        request_object = mock_api_client.request.call_args[0][0]
        # The OrderId is formatted into the endpoint URL
        assert "123" in request_object._endpoint
        assert request_object.params['AccountKey'] == "account_key"

    def test_cancel_order_failure_api_error(self, order_service, mock_api_client):
        # Arrange
        mock_api_client.request.side_effect = ApiRequestException("Failed to cancel")

        # Act
        result = order_service.cancel_order("123")

        # Assert
        assert result is False

    def test_get_single_order_success(self, order_service, mock_api_client):
        """Test that getting a single order works correctly."""
        # Arrange
        expected_order = {"OrderId": "order1", "Status": "Working"}
        mock_api_client.request.return_value = expected_order

        # Act
        result = order_service.get_single_order("order1")

        # Assert
        assert result == expected_order
        mock_api_client.request.assert_called_once()
        request_object = mock_api_client.request.call_args[0][0]
        # Both OrderId and ClientKey are part of the endpoint URL
        assert "order1" in request_object._endpoint
        assert "client_key" in request_object._endpoint

    def test_cancel_order_unexpected_exception(self, order_service, mock_api_client):
        # Arrange
        mock_api_client.request.side_effect = Exception("Unexpected error")

        # Act
        result = order_service.cancel_order("123")

        # Assert
        assert result is False
