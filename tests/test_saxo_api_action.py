import pytest
from unittest.mock import patch, MagicMock, call
import src.trade.api_actions as api_actions
from src.trade.api_actions import (
    TradingOrchestrator,
    InstrumentService,
    OrderService,
    PositionService,
    SaxoApiClient,
    parse_saxo_turbo_description,
    SaxoApiError,
    InsufficientFundsException,
    OrderPlacementError,
    TokenAuthenticationException,
    NoTurbosAvailableException,
    NoMarketAvailableException,
    PositionNotFoundException,
    ApiRequestException,
    PerformanceMonitor
)
from src.configuration import ConfigurationManager
from src.database import DbOrderManager, DbPositionManager
from src.trade.rules import TradingRule
from src.saxo_authen import SaxoAuth
from src.saxo_openapi.exceptions import OpenAPIError as SaxoOpenApiLibError
import requests
import json

# region Fixtures

@pytest.fixture
def mock_config_manager():
    """A mock for ConfigurationManager with a flexible side_effect."""
    manager = MagicMock(spec=ConfigurationManager)

    def get_config_value(key, default=None):
        configs = {
            "saxo_auth.env": "simulation",
            "trade.config.general.api_limits": {"top_instruments": 200, "top_positions": 200, "top_closed_positions": 500},
            "trade.config.turbo_preference.price_range": {"min": 4, "max": 15},
            "trade.config.general.retry_config": {"max_retries": 3, "retry_sleep_seconds": 1},
            "trade.config.general.websocket": {"refresh_rate_ms": 10000},
            "trade.config.buying_power": {"safety_margins": {"bid_calculation": 1}, "max_account_funds_to_use_percentage": 100},
            "trade.config.position_management": {"performance_thresholds": {"stoploss_percent": -20, "max_profit_percent": 60}},
            "trade.config.general": {"timezone": "Europe/Paris"},
            "logging.persistant": {"log_path": "/tmp/logs"}
        }
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
    """A mock for SaxoApiClient that bypasses its internal SaxoOpenApiLib."""
    client = MagicMock(spec=SaxoApiClient)
    return client

# endregion

# region Test Utility Functions

def test_parse_saxo_turbo_description_valid():
    description = "TURBO LONG DAX 12345.67 CITI"
    expected = {
        "name": "TURBO", "kind": "LONG", "buysell": "DAX",
        "price": "12345.67", "from": "CITI"
    }
    assert parse_saxo_turbo_description(description) == expected

def test_parse_saxo_turbo_description_invalid():
    description = "This is not a valid turbo description"
    assert parse_saxo_turbo_description(description) is None

# endregion

# region Test SaxoApiClient

@patch('src.trade.api_actions.SaxoOpenApiLib')
def test_saxo_api_client_init_and_token_refresh(mock_saxo_lib, mock_config_manager):
    """Test that the client initializes and refreshes the token correctly."""
    mock_auth = MagicMock(spec=SaxoAuth)
    # This simulates the token changing on the third call to get_token
    mock_auth.get_token.side_effect = ["token1", "token1", "token2"]

    # Initialization of the client calls _ensure_valid_token_and_api_instance once
    client = SaxoApiClient(mock_config_manager, mock_auth)
    mock_saxo_lib.assert_called_once_with(access_token="token1", environment="simulation", request_params={"timeout": 30})

    # Calling it again with the same token should not trigger a refresh
    client._ensure_valid_token_and_api_instance()
    mock_saxo_lib.assert_called_once()

    # Calling it again after the token has "changed" should trigger a refresh
    client._ensure_valid_token_and_api_instance()
    mock_saxo_lib.assert_called_with(access_token="token2", environment="simulation", request_params={"timeout": 30})
    assert mock_saxo_lib.call_count == 2

@patch('src.trade.api_actions.SaxoOpenApiLib')
def test_saxo_api_client_request_success(mock_saxo_lib, mock_config_manager, mock_saxo_auth):
    mock_api_instance = mock_saxo_lib.return_value
    mock_api_instance.request.return_value = {"status": "success"}

    client = SaxoApiClient(mock_config_manager, mock_saxo_auth)

    response = client.request("some_endpoint_request_obj")

    assert response == {"status": "success"}
    mock_api_instance.request.assert_called_once_with("some_endpoint_request_obj")

@patch('src.trade.api_actions.SaxoOpenApiLib')
@pytest.mark.parametrize("status_code, error_code, error_content_str, expected_exception, is_order_endpoint", [
    (400, "InsufficientFunds", '{"Message": "Not enough money"}', InsufficientFundsException, False),
    (400, "SomeError", '{"Message": "Bad request"}', OrderPlacementError, True),
    (401, "AuthError", '{"Message": "Unauthorized"}', TokenAuthenticationException, False),
    (429, "RateLimit", '{"Message": "Too many requests"}', SaxoApiError, False),
    (500, "ServerError", 'Internal Server Error', SaxoApiError, False),
])
def test_saxo_api_client_request_saxo_error_mapping(mock_saxo_lib, mock_config_manager, mock_saxo_auth, status_code, error_code, error_content_str, expected_exception, is_order_endpoint):
    """Test that SaxoOpenApiLibError is correctly mapped to custom exceptions."""
    try:
        content_json = json.loads(error_content_str)
        content_json['ErrorCode'] = error_code
        final_content = json.dumps(content_json)
    except json.JSONDecodeError:
        final_content = error_content_str

    mock_api_instance = mock_saxo_lib.return_value
    mock_api_instance.request.side_effect = SaxoOpenApiLibError(code=status_code, content=final_content, reason="Some Reason")

    client = SaxoApiClient(mock_config_manager, mock_saxo_auth)

    mock_endpoint = MagicMock()
    mock_endpoint.path = "/trade/v2/orders" if is_order_endpoint else "/some/other/endpoint"

    with pytest.raises(expected_exception):
        client.request(mock_endpoint)

@patch('src.trade.api_actions.SaxoOpenApiLib')
def test_saxo_api_client_request_connection_error(mock_saxo_lib, mock_config_manager, mock_saxo_auth):
    mock_api_instance = mock_saxo_lib.return_value
    mock_api_instance.request.side_effect = requests.RequestException("Connection failed")
    client = SaxoApiClient(mock_config_manager, mock_saxo_auth)
    with pytest.raises(ApiRequestException, match="Underlying request failed: Connection failed"):
        client.request("some_endpoint")

@patch('src.trade.api_actions.SaxoOpenApiLib')
def test_saxo_api_client_request_non_string_error_content(mock_saxo_lib, mock_config_manager, mock_saxo_auth):
    """Test error handling when error content is not a string."""
    mock_api_instance = mock_saxo_lib.return_value
    # Simulate error content being a dictionary instead of a string
    mock_api_instance.request.side_effect = SaxoOpenApiLibError(code=500, content={"error": "detail"}, reason="Server Error")
    client = SaxoApiClient(mock_config_manager, mock_saxo_auth)
    with pytest.raises(SaxoApiError) as excinfo:
        client.request("some_endpoint")
    assert str({"error": "detail"}) in str(excinfo.value)

@patch('src.trade.api_actions.SaxoOpenApiLib')
def test_saxo_api_client_request_invalid_json_error(mock_saxo_lib, mock_config_manager, mock_saxo_auth):
    """Test error handling with invalid JSON content."""
    mock_api_instance = mock_saxo_lib.return_value
    mock_api_instance.request.side_effect = SaxoOpenApiLibError(code=500, content="Not a valid JSON", reason="Server Error")
    client = SaxoApiClient(mock_config_manager, mock_saxo_auth)
    with pytest.raises(SaxoApiError, match="Not a valid JSON"):
        client.request("some_endpoint")

@patch('src.trade.api_actions.SaxoOpenApiLib')
def test_saxo_api_client_request_token_auth_exception_reraised(mock_saxo_lib, mock_config_manager, mock_saxo_auth):
    """Test that TokenAuthenticationException is re-raised."""
    mock_saxo_auth.get_token.side_effect = TokenAuthenticationException("Token machine broke")
    with pytest.raises(TokenAuthenticationException):
        SaxoApiClient(mock_config_manager, mock_saxo_auth)

@patch('src.trade.api_actions.SaxoOpenApiLib')
def test_saxo_api_client_unexpected_exception(mock_saxo_lib, mock_config_manager, mock_saxo_auth):
    """Test wrapping of unexpected exceptions."""
    mock_api_instance = mock_saxo_lib.return_value
    mock_api_instance.request.side_effect = Exception("Something totally unexpected")
    client = SaxoApiClient(mock_config_manager, mock_saxo_auth)
    with pytest.raises(ApiRequestException, match="Unexpected wrapper error: Something totally unexpected"):
        client.request("some_endpoint")

# endregion

# region Test InstrumentService

class TestInstrumentService:

    @pytest.fixture
    def instrument_service(self, mock_api_client, mock_config_manager):
        return InstrumentService(mock_api_client, mock_config_manager, "account_key")

    @patch('src.trade.api_actions.tr.infoprices.InfoPrices')
    def test_get_infoprices_for_asset_type_success(self, mock_infoprices_req, instrument_service, mock_api_client):
        mock_api_client.request.return_value = {"Data": ["price_info"]}
        result = instrument_service._get_infoprices_for_asset_type("123,456", "Exchange1", "AssetType1")
        assert result == {"Data": ["price_info"]}
        mock_api_client.request.assert_called_once()

    @patch('time.sleep', return_value=None)
    @patch('src.trade.api_actions.rd.instruments.Instruments')
    @patch('src.trade.api_actions.tr.infoprices.InfoPrices')
    @patch('src.trade.api_actions.tr.prices.CreatePriceSubscription')
    def test_find_turbos_happy_path(self, mock_price_sub_req, mock_infoprices_req, mock_instruments_req, mock_sleep, instrument_service, mock_api_client):
        mock_api_client.request.side_effect = [
            {"Data": [{"Identifier": 1, "Description": "TURBO LONG DAX 15000 CITI", "AssetType": "WarrantKnockOut"}]},
            {"Data": [{"Uic": 101, "Identifier": 1, "AssetType": "WarrantKnockOut", "Quote": {"Bid": 10, "Ask": 10.1, "PriceTypeAsk": "Tradable", "PriceTypeBid": "Tradable", "MarketState": "Open"}}]},
            {"Snapshot": {"Uic": 101, "DisplayAndFormat": {"Description": "Final TURBO LONG DAX 15000 CITI"}, "Quote": {"Ask": 10.05, "Bid": 9.95}}}
        ]
        result = instrument_service.find_turbos("exchange1", "underlying1", "long")
        assert result['selected_instrument']['uic'] == 101
        assert result['selected_instrument']['latest_ask'] == 10.05
        assert mock_api_client.request.call_count == 3

    def test_find_turbos_no_initial_instruments(self, instrument_service, mock_api_client):
        mock_api_client.request.return_value = {"Data": []}
        with pytest.raises(NoTurbosAvailableException):
            instrument_service.find_turbos("e1", "u1", "long")

    @patch('src.trade.api_actions.parse_saxo_turbo_description')
    def test_find_turbos_sorting_error(self, mock_parse_description, instrument_service, mock_api_client):
        # Simulate data that will cause a sorting error
        mock_api_client.request.return_value = {"Data": [{"Identifier": 1, "Description": "A valid description"}]}
        # Mock the parsing result to have a non-numeric price
        mock_parse_description.return_value = {"price": "not_a_number"}
        with pytest.raises(ValueError, match="Could not sort instruments by parsed price"):
            instrument_service.find_turbos("e1", "u1", "long")

    @patch('time.sleep', return_value=None)
    def test_find_turbos_price_subscription_fails(self, mock_sleep, instrument_service, mock_api_client):
        # Happy path until the last step (price subscription)
        mock_api_client.request.side_effect = [
            {"Data": [{"Identifier": 1, "Description": "TURBO LONG DAX 15000 CITI", "AssetType": "WarrantKnockOut"}]},
            {"Data": [{"Uic": 101, "Identifier": 1, "AssetType": "WarrantKnockOut", "Quote": {"Bid": 10, "Ask": 10.1, "PriceTypeAsk": "Tradable", "PriceTypeBid": "Tradable", "MarketState": "Open"}}]},
            # This time, the subscription fails
            ApiRequestException("Subscription failed")
        ]

        result = instrument_service.find_turbos("e1", "u1", "long")
        # Should fall back to using the InfoPrice data
        assert result is not None
        assert result['selected_instrument']['uic'] == 101
        # latest_ask should be from the InfoPrice response, not the (failed) subscription
        assert result['selected_instrument']['latest_ask'] == 10.1
        assert result['selected_instrument']['subscription_context_id'] is None # Should be None on failure

    @patch('time.sleep', return_value=None)
    def test_find_turbos_no_infoprice_data(self, mock_sleep, instrument_service, mock_api_client):
        # First call to get instruments succeeds
        mock_api_client.request.side_effect = [
            {"Data": [{"Identifier": 1, "Description": "TURBO LONG DAX 15000 CITI", "AssetType": "WarrantKnockOut"}]},
            # Subsequent calls to get infoprices fail
            None, None, None
        ]
        with pytest.raises(NoMarketAvailableException, match="Failed to obtain valid InfoPrice data"):
            instrument_service.find_turbos("e1", "u1", "long")

    @patch('time.sleep', return_value=None)
    def test_find_turbos_no_quote_in_infoprice_data(self, mock_sleep, instrument_service, mock_api_client):
        # The bid check loop should exit gracefully if no items have a "Quote" field
        mock_api_client.request.side_effect = [
            {"Data": [{"Identifier": 1, "Description": "TURBO LONG DAX 15000 CITI", "AssetType": "WarrantKnockOut"}]},
            # InfoPrice data is missing the "Quote" field
            {"Data": [{"Uic": 101, "Identifier": 1, "AssetType": "WarrantKnockOut"}]},
        ]
        with pytest.raises(NoMarketAvailableException, match="No instruments with Bid data available after retries and final filtering."):
            instrument_service.find_turbos("e1", "u1", "long")

# endregion

# region Test OrderService

class TestOrderService:
    @pytest.fixture
    def order_service(self, mock_api_client):
        return OrderService(mock_api_client, "account_key", "client_key")

    @patch('src.trade.api_actions.tr.orders.Order')
    def test_place_market_order_success(self, mock_order_req, order_service, mock_api_client):
        mock_api_client.request.return_value = {"OrderId": "12345"}
        result = order_service.place_market_order(uic=1, asset_type="FxSpot", amount=100, buy_sell="Buy")
        assert result == {"OrderId": "12345"}
        mock_api_client.request.assert_called_once()

    def test_place_market_order_api_error(self, order_service, mock_api_client):
        mock_api_client.request.side_effect = OrderPlacementError("API rejected order")
        with pytest.raises(OrderPlacementError):
            order_service.place_market_order(uic=1, asset_type="FxSpot", amount=100, buy_sell="Buy")

    @patch('src.trade.api_actions.tr.orders.CancelOrders')
    def test_cancel_order_unexpected_exception(self, mock_cancel_req, order_service, mock_api_client):
        mock_api_client.request.side_effect = Exception("Unexpected error")
        result = order_service.cancel_order("123")
        assert result is False

# endregion

# region Test PositionService

class TestPositionService:
    @pytest.fixture
    def order_service(self, mock_api_client):
        return OrderService(mock_api_client, "account_key", "client_key")

    @pytest.fixture
    def position_service(self, mock_api_client, order_service, mock_config_manager):
        return PositionService(mock_api_client, order_service, mock_config_manager, "account_key", "client_key")

    @patch('src.trade.api_actions.pf.positions.PositionsMe')
    def test_get_open_positions_success(self, mock_positions_req, position_service, mock_api_client):
        mock_api_client.request.return_value = {"Data": [{"PositionId": "pos1"}]}
        result = position_service.get_open_positions()
        assert result["__count"] == 1
        assert result["Data"][0]["PositionId"] == "pos1"

    @patch('src.trade.api_actions.pf.closedpositions.ClosedPositionsMe')
    def test_get_closed_positions_success(self, mock_closed_positions_req, position_service, mock_api_client):
        mock_api_client.request.return_value = {"Data": [{"PositionId": "pos1"}]}
        result = position_service.get_closed_positions()
        assert result["Data"][0]["PositionId"] == "pos1"

    @patch('src.trade.api_actions.pf.positions.SinglePosition')
    def test_get_single_position_success(self, mock_single_position_req, position_service, mock_api_client):
        mock_api_client.request.return_value = {"PositionId": "pos1"}
        result = position_service.get_single_position("pos1")
        assert result["PositionId"] == "pos1"

    @patch.object(PositionService, 'get_open_positions')
    def test_find_position_by_order_id_with_retry_found_first_try(self, mock_get_open_positions, position_service):
        mock_get_open_positions.return_value = {"Data": [{"PositionBase": {"SourceOrderId": "order1"}, "PositionId": "pos1"}]}
        result = position_service.find_position_by_order_id_with_retry("order1")
        assert result["PositionId"] == "pos1"
        mock_get_open_positions.assert_called_once()

    @patch('time.sleep', return_value=None)
    @patch.object(PositionService, 'get_open_positions')
    @patch.object(OrderService, 'cancel_order')
    def test_find_position_by_order_id_with_retry_not_found_and_cancel_success(self, mock_cancel_order, mock_get_open_positions, mock_sleep, position_service):
        mock_get_open_positions.return_value = {"Data": []}
        mock_cancel_order.return_value = True

        with pytest.raises(PositionNotFoundException) as excinfo:
            position_service.find_position_by_order_id_with_retry("order1")

        assert "Successfully cancelled" in str(excinfo.value)
        assert excinfo.value.cancellation_succeeded is True
        assert mock_get_open_positions.call_count == 5
        mock_cancel_order.assert_called_once_with("order1")

    @patch('time.sleep', return_value=None)
    @patch.object(PositionService, 'get_open_positions')
    @patch.object(OrderService, 'cancel_order')
    def test_find_position_by_order_id_with_retry_not_found_and_cancel_fail(self, mock_cancel_order, mock_get_open_positions, mock_sleep, position_service):
        mock_get_open_positions.return_value = {"Data": []}
        mock_cancel_order.return_value = False

        with pytest.raises(PositionNotFoundException) as excinfo:
            position_service.find_position_by_order_id_with_retry("order1")

        assert "Failed to cancel" in str(excinfo.value)
        assert excinfo.value.cancellation_succeeded is False

    @patch('src.trade.api_actions.pf.balances.AccountBalances')
    def test_get_spending_power_invalid_value(self, mock_balances_req, position_service, mock_api_client):
        mock_api_client.request.return_value = {"SpendingPower": "not a number"}
        with pytest.raises(SaxoApiError, match="Invalid SpendingPower value received"):
            position_service.get_spending_power()

# endregion

# region Test TradingOrchestrator

class TestTradingOrchestrator:

    @pytest.fixture
    def trading_orchestrator(self, mock_config_manager, mock_db_order_manager, mock_db_position_manager):
        instrument_service = MagicMock(spec=InstrumentService)
        order_service = MagicMock(spec=OrderService)
        position_service = MagicMock(spec=PositionService)

        return TradingOrchestrator(
            instrument_service,
            order_service,
            position_service,
            mock_config_manager,
            mock_db_order_manager,
            mock_db_position_manager
        )

    def test_calculate_bid_amount_success(self, trading_orchestrator):
        turbo_info = {"selected_instrument": {"latest_ask": 10, "decimals": 2}}
        amount = trading_orchestrator._calculate_bid_amount(turbo_info, 1000)
        assert amount == 99

    def test_execute_trade_signal_happy_path(self, trading_orchestrator, mock_db_order_manager, mock_db_position_manager):
        trading_orchestrator.instrument_service.find_turbos.return_value = {
            "selected_instrument": {"uic": 123, "asset_type": "TypeA", "latest_ask": 10, "decimals": 2, "description": "Desc", "symbol": "Sym", "currency": "EUR", "commissions": {}}
        }
        trading_orchestrator.position_service.get_spending_power.return_value = 1000
        trading_orchestrator.order_service.place_market_order.return_value = {"OrderId": "order1"}
        trading_orchestrator.position_service.find_position_by_order_id_with_retry.return_value = {
            "PositionId": "pos1", "PositionBase": {}, "DisplayAndFormat": {}
        }

        result = trading_orchestrator.execute_trade_signal("e1", "u1", "long")

        assert result is not None
        mock_db_order_manager.insert_turbo_order_data.assert_called_once()
        mock_db_position_manager.insert_turbo_open_position_data.assert_called_once()

    def test_calculate_bid_amount_invalid_ask_price(self, trading_orchestrator):
        turbo_info = {"selected_instrument": {"latest_ask": None, "decimals": 2}}
        with pytest.raises(ValueError, match="Invalid ask price for bid calculation"):
            trading_orchestrator._calculate_bid_amount(turbo_info, 1000)

    def test_execute_trade_signal_db_error(self, trading_orchestrator, mock_db_order_manager):
        trading_orchestrator.instrument_service.find_turbos.return_value = {
            "selected_instrument": {"uic": 123, "asset_type": "TypeA", "latest_ask": 10, "decimals": 2, "description": "Desc", "symbol": "Sym", "currency": "EUR", "commissions": {}}
        }
        trading_orchestrator.position_service.get_spending_power.return_value = 1000
        trading_orchestrator.order_service.place_market_order.return_value = {"OrderId": "order1"}
        trading_orchestrator.position_service.find_position_by_order_id_with_retry.return_value = {
            "PositionId": "pos1", "PositionBase": {}, "DisplayAndFormat": {}
        }
        mock_db_order_manager.insert_turbo_order_data.side_effect = Exception("DB Error")

        from src.trade.exceptions import DatabaseOperationException
        with pytest.raises(DatabaseOperationException):
            trading_orchestrator.execute_trade_signal("e1", "u1", "long")

# endregion

# region Test PerformanceMonitor

class TestPerformanceMonitor:

    @pytest.fixture
    def performance_monitor(self, mock_config_manager, mock_db_position_manager, mock_trading_rule):
        position_service = MagicMock(spec=PositionService)
        order_service = MagicMock(spec=OrderService)
        rabbit_connection = MagicMock()

        return PerformanceMonitor(
            position_service,
            order_service,
            mock_config_manager,
            mock_db_position_manager,
            mock_trading_rule,
            rabbit_connection
        )

    @patch('time.sleep', return_value=None)
    @patch('src.trade.api_actions.send_message_to_mq_for_telegram')
    def test_fetch_and_update_closed_position_in_db_success(self, mock_send_message, mock_sleep, performance_monitor, mock_db_position_manager):
        performance_monitor.position_service.get_closed_positions.return_value = {
            "Data": [{"ClosedPosition": {"OpeningPositionId": "pos1", "ClosingPrice": 120, "OpenPrice": 100, "Amount": 10}, "DisplayAndFormat": {}}]
        }
        result = performance_monitor._fetch_and_update_closed_position_in_db("pos1", "Test Close")
        assert result is True
        mock_db_position_manager.update_turbo_position_data.assert_called_once()
        mock_send_message.assert_called_once()

    @patch.object(PerformanceMonitor, '_log_performance_detail')
    @patch.object(PerformanceMonitor, '_fetch_and_update_closed_position_in_db')
    def test_check_all_positions_performance_triggers_stoploss(self, mock_update_db, mock_log_perf, performance_monitor):
        performance_monitor.db_position_manager.get_open_positions_ids_actions.return_value = [{"position_id": "pos1"}]
        performance_monitor.position_service.get_open_positions.return_value = {
            "Data": [{"PositionId": "pos1", "PositionBase": {"OpenPrice": 100, "Amount": 10, "CanBeClosed": True, "Uic": 1, "AssetType": "T"}, "PositionView": {"Bid": 79}}]
        }
        performance_monitor.db_position_manager.get_max_position_percent.return_value = -10.0
        mock_update_db.return_value = True
        result = performance_monitor.check_all_positions_performance()
        performance_monitor.order_service.place_market_order.assert_called_once()
        mock_update_db.assert_called_once()

    def test_check_all_positions_performance_no_positions(self, performance_monitor):
        performance_monitor.db_position_manager.get_open_positions_ids_actions.return_value = []
        result = performance_monitor.check_all_positions_performance()
        assert result == {"closed_positions_processed": [], "db_updates": [], "errors": 0}

    @patch('src.trade.api_actions.send_message_to_mq_for_telegram')
    def test_close_managed_positions_by_criteria(self, mock_send_message, performance_monitor):
        performance_monitor.db_position_manager.get_open_positions_ids_actions.return_value = [
            {"position_id": "pos1", "action": "long"},
            {"position_id": "pos2", "action": "short"},
        ]
        performance_monitor.position_service.get_open_positions.return_value = {
            "Data": [
                {"PositionId": "pos1", "PositionBase": {"Amount": 10, "CanBeClosed": True, "Uic": 1, "AssetType": "T"}},
                {"PositionId": "pos2", "PositionBase": {"Amount": -10, "CanBeClosed": True, "Uic": 2, "AssetType": "T"}},
            ]
        }
        performance_monitor.order_service.place_market_order.return_value = {"OrderId": "close_order"}
        with patch.object(performance_monitor, '_fetch_and_update_closed_position_in_db', return_value=True):
            result = performance_monitor.close_managed_positions_by_criteria(action_filter="long")

        assert result["closed_initiated_count"] == 1
        assert performance_monitor.order_service.place_market_order.call_count == 1

    @patch('src.trade.api_actions.send_message_to_mq_for_telegram')
    def test_sync_db_positions_with_api_success(self, mock_send_message, performance_monitor):
        performance_monitor.db_position_manager.get_open_positions_ids.return_value = ["pos1_closed", "pos2_open"]
        performance_monitor.position_service.get_open_positions.return_value = {"Data": [{"PositionId": "pos2_open"}]}
        performance_monitor.position_service.get_closed_positions.return_value = {
            "Data": [{"ClosedPosition": {"OpeningPositionId": "pos1_closed"}, "DisplayAndFormat": {}}]
        }
        result = performance_monitor.sync_db_positions_with_api()
        assert len(result["updates_for_db"]) == 1
        assert result["updates_for_db"][0][0] == "pos1_closed"

    @patch('os.path.exists', return_value=True)
    @patch('builtins.open', new_callable=MagicMock)
    def test_log_performance_detail(self, mock_open, mock_path_exists, performance_monitor):
        api_pos = {
            "PositionBase": {"ExecutionTimeOpen": "2023-01-01T12:00:00Z"},
            "PositionView": {}
        }
        performance_monitor._log_performance_detail("pos1", api_pos, 1.23)
        mock_open.assert_called_once()
        handle = mock_open.return_value.__enter__()
        handle.write.assert_called_once()
        written_content = handle.write.call_args[0][0]
        import json
        log_data = json.loads(written_content)
        assert log_data["position_id"] == "pos1"
        assert log_data["performance"] == 1.23

    @patch('time.sleep', return_value=None)
    def test_fetch_and_update_closed_position_in_db_not_found(self, mock_sleep, performance_monitor):
        performance_monitor.position_service.get_closed_positions.return_value = {"Data": []}
        result = performance_monitor._fetch_and_update_closed_position_in_db("pos1", "Test Close")
        assert result is False

    def test_check_all_positions_performance_api_fail(self, performance_monitor):
        performance_monitor.db_position_manager.get_open_positions_ids_actions.return_value = [{"position_id": "pos1"}]
        performance_monitor.position_service.get_open_positions.side_effect = ApiRequestException("API Error")
        result = performance_monitor.check_all_positions_performance()
        assert result["errors"] == 1

    def test_close_managed_positions_no_filter(self, performance_monitor):
        performance_monitor.db_position_manager.get_open_positions_ids_actions.return_value = [
            {"position_id": "pos1", "action": "long"},
        ]
        performance_monitor.position_service.get_open_positions.return_value = {
            "Data": [
                {"PositionId": "pos1", "PositionBase": {"Amount": 10, "CanBeClosed": True, "Uic": 1, "AssetType": "T"}},
            ]
        }
        performance_monitor.order_service.place_market_order.return_value = {"OrderId": "close_order"}
        with patch.object(performance_monitor, '_fetch_and_update_closed_position_in_db', return_value=True):
            result = performance_monitor.close_managed_positions_by_criteria()

        assert result["closed_initiated_count"] == 1

# endregion