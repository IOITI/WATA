import pytest
from unittest.mock import patch, MagicMock, call, ANY, mock_open

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

    @patch.object(PerformanceMonitor, '_log_performance_detail')
    def test_should_calculate_daily_profit_with_multiple_open_positions(self, mock_log_perf, performance_monitor, mock_db_position_manager, mock_position_service, mock_order_service):
        """
        Tests the daily profit calculation with multiple positions.
        NOTE: This test characterizes the *current* logic, which is known to be
        a simplistic and potentially incorrect way to calculate portfolio profit.
        It serves as a baseline for future refactoring.
        """
        # Arrange
        # Daily profit target is 5%
        performance_monitor.percent_profit_wanted_per_days = 5.0
        # DB reports 2% of realized profit for the day so far
        mock_db_position_manager.get_percent_of_the_day.return_value = 2.0

        db_positions = [{"position_id": "pos1"}, {"position_id": "pos2"}]
        # Both positions are up 2%
        pos1 = TestDataFactory.create_saxo_position(position_id="pos1", open_price=100, current_bid=102)
        pos2 = TestDataFactory.create_saxo_position(position_id="pos2", open_price=100, current_bid=102)

        mock_db_position_manager.get_open_positions_ids_actions.return_value = db_positions
        mock_position_service.get_open_positions.return_value = {"Data": [pos1, pos2]}
        mock_db_position_manager.get_max_position_percent.return_value = 2.0

        # Act
        result = performance_monitor.check_all_positions_performance()

        # Assert
        # The current flawed logic is: (1 + realized_profit) * (1 + position_profit) - 1
        # (1.02 * 1.02) - 1 = 0.0404, or 4.04%, which is less than the 5% target.
        # Therefore, no positions should be closed.
        assert len(result["closed_positions_processed"]) == 0
        mock_order_service.place_market_order.assert_not_called()

    @patch('src.trade.api_actions.send_message_to_mq_for_telegram')
    def test_should_handle_api_position_sync_race_conditions(self, mock_send_message, performance_monitor, mock_db_position_manager, mock_position_service):
        """
        Tests the sync logic when a position is closed on the API while the sync
        is in progress.
        """
        # Arrange
        # DB thinks pos1 and pos2 are open
        mock_db_position_manager.get_open_positions_ids.return_value = ["pos1", "pos2"]

        # By the time we check, the API only reports pos2 as open (pos1 was just closed)
        mock_position_service.get_open_positions.return_value = {
            "Data": [TestDataFactory.create_saxo_position("pos2")]
        }
        # And pos1 now appears in the list of recently closed positions
        closed_pos1 = TestDataFactory.create_saxo_closed_position(opening_position_id="pos1")
        mock_position_service.get_closed_positions.return_value = {"Data": [closed_pos1]}

        # Act
        result = performance_monitor.sync_db_positions_with_api()

        # Assert
        # The sync logic should have found one position to update
        assert len(result["updates_for_db"]) == 1
        position_id, update_data = result["updates_for_db"][0]

        # It should be pos1
        assert position_id == "pos1"
        # It should be marked as Closed
        assert update_data["position_status"] == "Closed"
        assert update_data["position_close_reason"] == "SaxoAPI"
        # A notification should have been sent
        mock_send_message.assert_called_once()
        assert "SYNC CLOSE: Position pos1" in mock_send_message.call_args[0][1]

    @patch.object(PerformanceMonitor, '_log_performance_detail')
    def test_should_skip_closing_if_canbeclosed_is_false(self, mock_log_perf, performance_monitor, mock_db_position_manager, mock_position_service, mock_order_service):
        """Test that a position is not closed if it hits a threshold but CanBeClosed is False."""
        # Arrange
        mock_db_position_manager.get_open_positions_ids_actions.return_value = [{"position_id": "pos1"}]
        # Position hits stoploss, but is marked as not closable
        position = TestDataFactory.create_saxo_position(position_id="pos1", open_price=100, current_bid=70, can_be_closed=False)
        mock_position_service.get_open_positions.return_value = {"Data": [position]}
        mock_db_position_manager.get_max_position_percent.return_value = 1.0

        # Act
        result = performance_monitor.check_all_positions_performance()

        # Assert
        # No close order should have been attempted
        assert len(result["closed_positions_processed"]) == 1
        assert result["closed_positions_processed"][0]['status'] == "Skipped (Cannot Be Closed)"
        mock_order_service.place_market_order.assert_not_called()

    def test_sync_anomaly_position_is_not_in_open_or_closed(self, performance_monitor, mock_db_position_manager, mock_position_service):
        """Test the sync anomaly case where a position from the DB is not found anywhere in the API."""
        # Arrange
        # DB thinks pos_vanished is open
        mock_db_position_manager.get_open_positions_ids.return_value = ["pos_vanished"]
        # API returns no open positions
        mock_position_service.get_open_positions.return_value = {"Data": []}
        # And the position is also not in the recently closed list
        mock_position_service.get_closed_positions.return_value = {"Data": []}

        # Act
        result = performance_monitor.sync_db_positions_with_api()

        # Assert
        # No updates should be generated for the DB
        assert len(result["updates_for_db"]) == 0


class TestCloseManagedPositions:

    @patch.object(PerformanceMonitor, '_fetch_and_update_closed_position_in_db', return_value=True)
    def test_close_all_positions_no_filter(self, mock_update_db, performance_monitor, mock_db_position_manager, mock_position_service, mock_order_service):
        """Test that all managed positions are closed when no filter is provided."""
        # Arrange
        db_positions = [
            {"position_id": "pos_long", "action": "long"},
            {"position_id": "pos_short", "action": "short"},
        ]
        api_positions = [
            TestDataFactory.create_saxo_position(position_id="pos_long", amount=100),
            TestDataFactory.create_saxo_position(position_id="pos_short", amount=-100),
        ]
        mock_db_position_manager.get_open_positions_ids_actions.return_value = db_positions
        mock_position_service.get_open_positions.return_value = {"Data": api_positions}
        mock_order_service.place_market_order.return_value = TestDataFactory.create_order_response()

        # Act
        result = performance_monitor.close_managed_positions_by_criteria(action_filter=None)

        # Assert
        assert result["closed_initiated_count"] == 2
        assert mock_order_service.place_market_order.call_count == 2
        # Called once for each closed position
        assert mock_update_db.call_count == 2

    @patch.object(PerformanceMonitor, '_fetch_and_update_closed_position_in_db', return_value=True)
    def test_close_positions_with_long_filter(self, mock_update_db, performance_monitor, mock_db_position_manager, mock_position_service, mock_order_service):
        """Test that only 'long' positions are closed when the filter is 'long'."""
        # Arrange
        db_positions = [
            {"position_id": "pos_long", "action": "long"},
            {"position_id": "pos_short", "action": "short"},
        ]
        api_positions = [
            TestDataFactory.create_saxo_position(position_id="pos_long", amount=100),
            TestDataFactory.create_saxo_position(position_id="pos_short", amount=-100),
        ]
        mock_db_position_manager.get_open_positions_ids_actions.return_value = db_positions
        mock_position_service.get_open_positions.return_value = {"Data": api_positions}
        mock_order_service.place_market_order.return_value = TestDataFactory.create_order_response()

        # Act
        result = performance_monitor.close_managed_positions_by_criteria(action_filter="long")

        # Assert
        assert result["closed_initiated_count"] == 1
        # Should only be called for the 'long' position
        assert mock_order_service.place_market_order.call_count == 1
        # The argument passed to place_market_order should be for the long position (a Sell order)
        call_kwargs = mock_order_service.place_market_order.call_args.kwargs
        assert call_kwargs['uic'] == api_positions[0]['PositionBase']['Uic']
        assert call_kwargs['buy_sell'] == 'Sell'
        assert mock_update_db.call_count == 1


class TestPerformanceMonitorHelpers:

    @patch('time.sleep', return_value=None)
    def test_fetch_and_update_closed_position_in_db_api_fails(self, mock_sleep, performance_monitor, mock_position_service):
        """Test the error handling when fetching closed positions fails."""
        # Arrange
        mock_position_service.get_closed_positions.side_effect = ApiRequestException("API Error")

        # Act
        result = performance_monitor._fetch_and_update_closed_position_in_db("pos1", "Test Close")

        # Assert
        # The method should fail gracefully and return False
        assert result is False

    @patch('os.path.exists', return_value=True)
    @patch('builtins.open', new_callable=mock_open)
    def test_log_performance_detail(self, mock_open, mock_path_exists, performance_monitor):
        """Test that the performance logger writes a valid JSON line to a file."""
        # Arrange
        api_pos = TestDataFactory.create_saxo_position(
            position_id="pos_log",
            overrides={"PositionBase": {"ExecutionTimeOpen": "2023-01-01T12:00:00Z"}}
        )
        performance_percent = 5.5

        # Act
        performance_monitor._log_performance_detail("pos_log", api_pos, performance_percent)

        # Assert
        mock_open.assert_called_once()
        # The handle is the return value of the call to open()
        handle = mock_open()
        handle.write.assert_called_once()
        # Check the content that was written
        written_content = handle.write.call_args[0][0]
        import json
        log_data = json.loads(written_content)
        assert log_data["position_id"] == "pos_log"
        assert log_data["performance"] == 5.5
        assert "open_hour" in log_data
