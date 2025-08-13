import pytest
from unittest.mock import patch, MagicMock, call, ANY

from src.trade.api_actions import PerformanceMonitor, PositionService, OrderService
from src.database import DbPositionManager
from src.trade.rules import TradingRule
from src.trade.exceptions import OrderPlacementError, ApiRequestException
from tests.test_data_factory import TestDataFactory

@pytest.fixture
def mock_position_service():
    return MagicMock(spec=PositionService)

@pytest.fixture
def mock_order_service():
    return MagicMock(spec=OrderService)

@pytest.fixture
def mock_rabbit_connection():
    return MagicMock()

@pytest.fixture
def performance_monitor(
    mock_position_service,
    mock_order_service,
    mock_config_manager,
    mock_db_position_manager,
    mock_trading_rule,
    mock_rabbit_connection
):
    """Fixture for PerformanceMonitor."""
    return PerformanceMonitor(
        position_service=mock_position_service,
        order_service=mock_order_service,
        config_manager=mock_config_manager,
        db_position_manager=mock_db_position_manager,
        trading_rule=mock_trading_rule,
        rabbit_connection=mock_rabbit_connection,
    )

class TestPerformanceMonitor:

    @patch('time.sleep', return_value=None)
    @patch('src.trade.api_actions.send_message_to_mq_for_telegram')
    def test_fetch_and_update_closed_position_in_db_success(self, mock_send_message, mock_sleep, performance_monitor, mock_db_position_manager, mock_position_service):
        # Arrange
        closed_position = TestDataFactory.create_saxo_closed_position(opening_position_id="pos1")
        mock_position_service.get_closed_positions.return_value = {"Data": [closed_position]}
        mock_db_position_manager.get_max_position_percent.return_value = 5.0
        mock_db_position_manager.get_percent_of_the_day.return_value = 1.5

        # Act
        result = performance_monitor._fetch_and_update_closed_position_in_db("pos1", "Test Close")

        # Assert
        assert result is True
        mock_db_position_manager.update_turbo_position_data.assert_called_once_with("pos1", ANY)
        mock_send_message.assert_called_once()
        # Check that the message contains key info. The message is the second argument.
        message_arg = mock_send_message.call_args[0][1]
        assert "CLOSED POSITION" in message_arg
        assert "pos1" in message_arg

    @patch.object(PerformanceMonitor, '_log_performance_detail')
    @patch.object(PerformanceMonitor, '_fetch_and_update_closed_position_in_db')
    def test_check_all_positions_performance_triggers_stoploss(self, mock_update_db, mock_log_perf, performance_monitor, mock_db_position_manager, mock_position_service, mock_order_service):
        # Arrange
        mock_db_position_manager.get_open_positions_ids_actions.return_value = [{"position_id": "pos1"}]
        # Position has lost 21%, and stoploss is at -20%
        position = TestDataFactory.create_saxo_position(position_id="pos1", open_price=100, current_bid=79, can_be_closed=True)
        mock_position_service.get_open_positions.return_value = {"Data": [position]}
        mock_db_position_manager.get_max_position_percent.return_value = 1.0
        mock_update_db.return_value = True

        # Act
        result = performance_monitor.check_all_positions_performance()

        # Assert
        assert len(result["closed_positions_processed"]) == 1
        assert result["closed_positions_processed"][0]["status"] == "Closed"
        mock_order_service.place_market_order.assert_called_once()
        mock_update_db.assert_called_once_with("pos1", ANY)

    def test_check_all_positions_performance_no_positions(self, performance_monitor, mock_db_position_manager):
        mock_db_position_manager.get_open_positions_ids_actions.return_value = []
        result = performance_monitor.check_all_positions_performance()
        assert result == {"closed_positions_processed": [], "db_updates": [], "errors": 0}

    @patch.object(PerformanceMonitor, '_fetch_and_update_closed_position_in_db')
    def test_should_handle_multiple_positions_hitting_thresholds_simultaneously(self, mock_update_db, performance_monitor, mock_db_position_manager, mock_position_service, mock_order_service):
        # Arrange
        db_positions = [{"position_id": "pos_stoploss"}, {"position_id": "pos_takeprofit"}]
        # pos1 hits stoploss (-25%), pos2 hits takeprofit (+65%)
        pos1 = TestDataFactory.create_saxo_position(position_id="pos_stoploss", open_price=100, current_bid=75)
        pos2 = TestDataFactory.create_saxo_position(position_id="pos_takeprofit", open_price=100, current_bid=165)
        mock_db_position_manager.get_open_positions_ids_actions.return_value = db_positions
        mock_position_service.get_open_positions.return_value = {"Data": [pos1, pos2]}
        mock_db_position_manager.get_max_position_percent.return_value = 1.0
        mock_update_db.return_value = True

        # Act
        result = performance_monitor.check_all_positions_performance()

        # Assert
        assert len(result["closed_positions_processed"]) == 2
        assert mock_order_service.place_market_order.call_count == 2
        assert mock_update_db.call_count == 2
        # Check that different reasons were passed
        assert "Stoploss" in mock_update_db.call_args_list[0][0][1]
        assert "Takeprofit" in mock_update_db.call_args_list[1][0][1]

    @patch('src.trade.api_actions.send_message_to_mq_for_telegram')
    @patch.object(PerformanceMonitor, '_fetch_and_update_closed_position_in_db')
    def test_should_handle_partial_close_order_failures(self, mock_update_db, mock_send_message, performance_monitor, mock_db_position_manager, mock_position_service, mock_order_service):
        # Arrange
        db_positions = [{"position_id": "pos_ok"}, {"position_id": "pos_fail"}]
        pos_ok = TestDataFactory.create_saxo_position(position_id="pos_ok", open_price=100, current_bid=70) # Hits stoploss
        pos_fail = TestDataFactory.create_saxo_position(position_id="pos_fail", open_price=100, current_bid=70) # Hits stoploss
        mock_db_position_manager.get_open_positions_ids_actions.return_value = db_positions
        mock_position_service.get_open_positions.return_value = {"Data": [pos_ok, pos_fail]}
        mock_db_position_manager.get_max_position_percent.return_value = 1.0

        # The first order succeeds, the second fails
        mock_order_service.place_market_order.side_effect = [
            TestDataFactory.create_order_response(),
            OrderPlacementError("Order failed")
        ]
        mock_update_db.return_value = True

        # Act
        result = performance_monitor.check_all_positions_performance()

        # Assert
        assert len(result["closed_positions_processed"]) == 2
        assert result["errors"] == 1
        assert result["closed_positions_processed"][0]["status"] == "Closed"
        assert result["closed_positions_processed"][1]["status"] == "Close Order Failed"

        # One order placed, one failed
        assert mock_order_service.place_market_order.call_count == 2
        # Only one position was successfully updated in the DB
        mock_update_db.assert_called_once_with("pos_ok", ANY)
        # A notification should be sent for the failure
        mock_send_message.assert_called_once()
        assert "ERROR: Failed closing pos_fail" in mock_send_message.call_args[0][1]

    @patch('src.trade.api_actions.send_message_to_mq_for_telegram')
    def test_sync_db_positions_with_api_success(self, mock_send_message, performance_monitor, mock_db_position_manager, mock_position_service):
        # Arrange
        mock_db_position_manager.get_open_positions_ids.return_value = ["pos1_closed", "pos2_open"]
        mock_position_service.get_open_positions.return_value = {"Data": [TestDataFactory.create_saxo_position("pos2_open")]}
        closed_pos = TestDataFactory.create_saxo_closed_position(opening_position_id="pos1_closed")
        mock_position_service.get_closed_positions.return_value = {"Data": [closed_pos]}

        # Act
        result = performance_monitor.sync_db_positions_with_api()

        # Assert
        assert len(result["updates_for_db"]) == 1
        update_tuple = result["updates_for_db"][0]
        assert update_tuple[0] == "pos1_closed"
        assert update_tuple[1]["position_status"] == "Closed"
        assert update_tuple[1]["position_close_reason"] == "SaxoAPI"
        # Check for notification
        mock_send_message.assert_called_once()
        assert "SYNC CLOSE: Position pos1_closed" in mock_send_message.call_args[0][1]
