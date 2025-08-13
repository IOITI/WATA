import datetime
import os
from datetime import datetime

import pytest
from src.database import DbPositionManager
from src.message_helper import append_performance_message
import tempfile
import json


class ConfigurationManager:
    """Basic mock ConfigurationManager to provide config values."""

    def __init__(self, config_path):
        with open(config_path, 'r') as file:
            self.config = json.load(file)

    def get_config_value(self, key):
        return self.config.get(key)


@pytest.fixture
def setup_temp_db():
    """Setup a temporary directory and return the ConfigurationManager pointing to that directory."""
    temp_dir = tempfile.TemporaryDirectory()
    config_path = os.path.join(temp_dir.name, 'test_config.json')

    # Create a basic configuration file for the test
    config = {
        "duckdb.persistant.db_path": os.path.join(temp_dir.name, 'test_db.duckdb')
    }

    with open(config_path, 'w') as config_file:
        json.dump(config, config_file)

    config_manager = ConfigurationManager(config_path)

    yield config_manager

    temp_dir.cleanup()


def test_trading_data_db_initialization(setup_temp_db):
    """Test the initialization of the TradingDataDB and DbPositionManager."""
    config_manager = setup_temp_db
    db_position_manager = DbPositionManager(config_manager)

    # Test if the connection was successfully established and schema created
    result = db_position_manager.conn.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'turbo_data_position';"
    ).fetchone()

    assert result[0] == 1, "Table turbo_data_position should have been created."


def test_insert_turbo_open_position_data(setup_temp_db):
    """Test inserting turbo open position data."""
    config_manager = setup_temp_db
    db_position_manager = DbPositionManager(config_manager)

    # Insert dummy data
    dummy_data = {
        "action": "long",
        "position_id": "pos_123",
        "position_amount": 100,
        "position_open_price": 120.5,
        "position_total_open_price": 12050.0,
        "position_status": "Open",
        "position_kind": "main",
        "execution_time_open": "2024-09-12 10:00:00",
        "order_id": "order_456",
        "related_order_id": ['order_789'],
        "instrument_name": "Tesla",
        "instrument_symbol": "TSLA",
        "instrument_uic": 101,
        "instrument_currency": "USD"
    }

    db_position_manager.insert_turbo_open_position_data(dummy_data)

    # Verify the data was inserted correctly
    result = db_position_manager.conn.execute(
        "SELECT * FROM turbo_data_position WHERE position_id = 'pos_123';"
    ).fetchone()

    assert result is not None, "Data should have been inserted."
    assert result[1] == "pos_123", "Position ID should match the inserted data."


def test_update_turbo_position_data(setup_temp_db):
    """Test updating turbo position data."""
    config_manager = setup_temp_db
    db_position_manager = DbPositionManager(config_manager)

    # Insert dummy data
    initial_data = {
        "action": "long",
        "position_id": "pos_123",
        "position_amount": 100,
        "position_open_price": 120.5,
        "position_total_open_price": 12050.0,
        "position_status": "Open",
        "position_kind": "main",
        "execution_time_open": "2024-09-12 10:00:00",
        "order_id": "order_456",
        "related_order_id": ['order_789'],
        "instrument_name": "Tesla",
        "instrument_symbol": "TSLA",
        "instrument_uic": 101,
        "instrument_currency": "USD"
    }

    db_position_manager.insert_turbo_open_position_data(initial_data)

    # Update data
    update_data = {
        "position_amount": 200,
        "position_open_price": 130.5,
        "position_total_open_price": 13050.0
    }

    db_position_manager.update_turbo_position_data("pos_123", update_data)

    # Verify the data was updated correctly
    result = db_position_manager.conn.execute(
        "SELECT position_amount, position_open_price, position_total_open_price FROM turbo_data_position WHERE position_id = 'pos_123';"
    ).fetchone()

    assert result is not None, "Data should exist after update."
    assert result[0] == 200, "Position amount should be updated."
    assert result[1] == 130.5, "Position open price should be updated."
    assert result[2] == 13050.0, "Position total open price should be updated."


def test_check_position_ids_exist(setup_temp_db):
    """Test checking if position IDs exist."""
    config_manager = setup_temp_db
    db_position_manager = DbPositionManager(config_manager)

    # Insert dummy data
    data1 = {
        "action": "long",
        "position_id": "pos_123",
        "position_amount": 100,
        "position_open_price": 120.5,
        "position_total_open_price": 12050.0,
        "position_status": "Open",
        "position_kind": "main",
        "execution_time_open": "2024-09-12 10:00:00",
        "order_id": "order_456",
        "related_order_id": ['order_789'],
        "instrument_name": "Tesla",
        "instrument_symbol": "TSLA",
        "instrument_uic": 101,
        "instrument_currency": "USD"
    }

    data2 = {
        "action": "short",
        "position_id": "pos_456",
        "position_amount": 50,
        "position_open_price": 110.5,
        "position_total_open_price": 5525.0,
        "position_status": "Closed",
        "position_kind": "main",
        "execution_time_open": "2024-09-12 11:00:00",
        "order_id": "order_789",
        "related_order_id": ['order_456'],
        "instrument_name": "Apple",
        "instrument_symbol": "AAPL",
        "instrument_uic": 102,
        "instrument_currency": "USD"
    }

    db_position_manager.insert_turbo_open_position_data(data1)
    db_position_manager.insert_turbo_open_position_data(data2)

    # Check for existing and non-existing position IDs
    result = db_position_manager.check_position_ids_exist(["pos_123", "pos_999"])

    assert len(result["position_ids_in_db"]) == 1, "Should find one position ID in the database."
    assert len(result["position_ids_not_found"]) == 1, "Should not find one position ID in the database."
    assert result["position_ids_in_db"][0]["position_id"] == "pos_123", "The position ID in the database should match."
    assert "pos_999" in result["position_ids_not_found"], "The position ID not found list should contain 'pos_999'."


def test_get_open_positions_ids(setup_temp_db):
    """Test retrieving open position IDs."""
    config_manager = setup_temp_db
    db_position_manager = DbPositionManager(config_manager)

    # Insert dummy data
    open_data = {
        "action": "long",
        "position_id": "pos_123",
        "position_amount": 100,
        "position_open_price": 120.5,
        "position_total_open_price": 12050.0,
        "position_status": "Open",
        "position_kind": "main",
        "execution_time_open": "2024-09-12 10:00:00",
        "order_id": "order_456",
        "related_order_id": ['order_789'],
        "instrument_name": "Tesla",
        "instrument_symbol": "TSLA",
        "instrument_uic": 101,
        "instrument_currency": "USD"
    }

    closed_data = {
        "action": "short",
        "position_id": "pos_456",
        "position_amount": 50,
        "position_open_price": 110.5,
        "position_total_open_price": 5525.0,
        "position_status": "Closed",
        "position_kind": "main",
        "execution_time_open": "2024-09-12 11:00:00",
        "order_id": "order_789",
        "related_order_id": ['order_456'],
        "instrument_name": "Apple",
        "instrument_symbol": "AAPL",
        "instrument_uic": 102,
        "instrument_currency": "USD"
    }

    db_position_manager.insert_turbo_open_position_data(open_data)
    db_position_manager.insert_turbo_open_position_data(closed_data)

    # Retrieve open positions IDs
    open_positions = db_position_manager.get_open_positions_ids()

    assert len(open_positions) == 1, "Should retrieve one open position ID."
    assert open_positions[0] == "pos_123", "The retrieved open position ID should match the inserted open position ID."

def test_get_open_positions_ids_actions(setup_temp_db):
    """Test retrieving open positions IDs and their actions."""
    config_manager = setup_temp_db
    db_position_manager = DbPositionManager(config_manager)

    # Insert dummy data
    open_data1 = {
        "action": "long",
        "position_id": "pos_123",
        "position_amount": 100,
        "position_open_price": 120.5,
        "position_total_open_price": 12050.0,
        "position_status": "Open",
        "position_kind": "main",
        "execution_time_open": "2024-09-12 10:00:00",
        "order_id": "order_456",
        "related_order_id": ['order_789'],
        "instrument_name": "Tesla",
        "instrument_symbol": "TSLA",
        "instrument_uic": 101,
        "instrument_currency": "USD"
    }

    open_data2 = {
        "action": "short",
        "position_id": "pos_789",
        "position_amount": 150,
        "position_open_price": 130.5,
        "position_total_open_price": 19575.0,
        "position_status": "Open",
        "position_kind": "main",
        "execution_time_open": "2024-09-12 12:00:00",
        "order_id": "order_789",
        "related_order_id": ['order_456'],
        "instrument_name": "Amazon",
        "instrument_symbol": "AMZN",
        "instrument_uic": 103,
        "instrument_currency": "USD"
    }

    closed_data = {
        "action": "short",
        "position_id": "pos_456",
        "position_amount": 50,
        "position_open_price": 110.5,
        "position_total_open_price": 5525.0,
        "position_status": "Closed",
        "position_kind": "main",
        "execution_time_open": "2024-09-12 11:00:00",
        "order_id": "order_789",
        "related_order_id": ['order_456'],
        "instrument_name": "Apple",
        "instrument_symbol": "AAPL",
        "instrument_uic": 102,
        "instrument_currency": "USD"
    }

    db_position_manager.insert_turbo_open_position_data(open_data1)
    db_position_manager.insert_turbo_open_position_data(open_data2)
    db_position_manager.insert_turbo_open_position_data(closed_data)

    # Retrieve open positions IDs and actions
    open_positions_actions = db_position_manager.get_open_positions_ids_actions()

    assert len(open_positions_actions) == 2, "Should retrieve two open positions."
    assert open_positions_actions[0]["position_id"] == "pos_123", "The first open position ID should be 'pos_123'."
    assert open_positions_actions[0]["action"] == "long", "The action for position ID 'pos_123' should be 'long'."
    assert open_positions_actions[1]["position_id"] == "pos_789", "The second open position ID should be 'pos_789'."
    assert open_positions_actions[1]["action"] == "short", "The action for position ID 'pos_789' should be 'short'."

def test_get_max_position_percent(setup_temp_db):
    """Test retrieving the maximum position percentage for a given position ID."""
    config_manager = setup_temp_db
    db_position_manager = DbPositionManager(config_manager)

    # Insert dummy data with maximum performance percent
    open_data = {
        "action": "long",
        "position_id": "pos_max",
        "position_amount": 100,
        "position_open_price": 120.5,
        "position_total_open_price": 12050.0,
        "position_status": "Open",
        "position_kind": "main",
        "execution_time_open": "2024-09-12 10:00:00",
        "order_id": "order_456",
        "related_order_id": ['order_789'],
        "instrument_name": "Tesla",
        "instrument_symbol": "TSLA",
        "instrument_uic": 101,
        "instrument_currency": "USD"
    }

    # Update data
    update_data = {
        "position_max_performance_percent": 15.5  # Adding the new field for testing
    }

    db_position_manager.insert_turbo_open_position_data(open_data)
    db_position_manager.update_turbo_position_data("pos_max", update_data)

    # Retrieve max position percent
    max_position_percent = db_position_manager.get_max_position_percent("pos_max")

    assert max_position_percent == 15.5, "The maximum position percentage should be 15.5."

    # Test for a position ID that does not exist
    max_position_percent_nonexistent = db_position_manager.get_max_position_percent("non_existent_pos")

    assert max_position_percent_nonexistent == 0.0, "The maximum position percentage for a non-existent position should be 0.0."

def test_get_stats_of_the_day(setup_temp_db):
    """Test retrieving statistics for the current day."""
    config_manager = setup_temp_db
    db_position_manager = DbPositionManager(config_manager)

    # Define open positions
    open_data1 = {
        "action": "long",
        "position_id": "pos_001",
        "position_amount": 100,
        "position_open_price": 120.5,
        "position_total_open_price": 12050.0,
        "position_status": "Open",
        "position_kind": "main",
        "execution_time_open": "2024-09-12 10:00:00",
        "order_id": "order_456",
        "related_order_id": ['order_789'],
        "instrument_name": "Tesla",
        "instrument_symbol": "TSLA",
        "instrument_uic": 101,
        "instrument_currency": "USD"
    }

    # Define update data to mark position as closed and add performance metrics
    update_data1 = {
        "position_status": "Closed",
        "position_total_performance_percent": 5.0,
        "execution_time_close": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "position_profit_loss": 600.0
    }

    open_data2 = {
        "action": "short",
        "position_id": "pos_002",
        "position_amount": 50,
        "position_open_price": 110.0,
        "position_total_open_price": 5500.0,
        "position_status": "Open",
        "position_kind": "main",
        "execution_time_open": "2024-09-12 10:00:00",
        "order_id": "order_456",
        "related_order_id": ['order_789'],
        "instrument_name": "Tesla",
        "instrument_symbol": "TSLA",
        "instrument_uic": 101,
        "instrument_currency": "USD"
    }

    # Define update data to mark position as closed and add performance metrics
    update_data2 = {
        "position_status": "Closed",
        "position_total_performance_percent": -2.5,
        "position_profit_loss": -137.5,
        "execution_time_close": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }

    # Insert open positions into the database
    db_position_manager.insert_turbo_open_position_data(open_data1)
    db_position_manager.insert_turbo_open_position_data(open_data2)

    # Update positions to reflect their closed status and performance metrics
    db_position_manager.update_turbo_position_data("pos_001", update_data1)
    db_position_manager.update_turbo_position_data("pos_002", update_data2)

    # Retrieve stats of the day
    stats = db_position_manager.get_stats_of_the_day()

    assert len(stats["general"]) == 1, "There should be one entry in the general stats for today."
    assert stats["general"][0]["position_count"] == 2, "The position count should be 2."
    assert stats["general"][0]["avg_percent"] == (5.0 - 2.5) / 2, "The average percent should be correctly calculated."
    assert stats["general"][0]["max_percent"] == 5.0, "The maximum percent should be 5.0."
    assert stats["general"][0]["min_percent"] == -2.5, "The minimum percent should be -2.5."
    assert stats["general"][0]["sum_profit"] == 462.5, "The sum profit should be 462.5."

    assert len(stats["detail_stats"]) == 2, "There should be two entries in the detail stats for today."
    assert stats["detail_stats"][0]["action"] == "long", "The action should be 'long'."
    assert stats["detail_stats"][1]["action"] == "short", "The action should be 'short'."


def test_get_percent_of_the_day(setup_temp_db):
    """Test retrieving the percentage change for the current day."""
    config_manager = setup_temp_db
    db_position_manager = DbPositionManager(config_manager)

    # Define and insert dummy data
    open_data1 = {
        "action": "long",
        "position_id": "pos_001",
        "position_amount": 100,
        "position_open_price": 120.5,
        "position_total_open_price": 12050.0,
        "position_status": "Open",
        "position_kind": "main",
        "execution_time_open": "2024-09-12 10:00:00",
        "order_id": "order_456",
        "related_order_id": ['order_789'],
        "instrument_name": "Tesla",
        "instrument_symbol": "TSLA",
        "instrument_uic": 101,
        "instrument_currency": "USD"
    }

    update_data1 = {
        "position_status": "Closed",
        "position_total_performance_percent": 10.0,
        "execution_time_close": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "position_profit_loss": 1000.0
    }

    open_data2 = {
        "action": "short",
        "position_id": "pos_002",
        "position_amount": 50,
        "position_open_price": 110.0,
        "position_total_open_price": 5500.0,
        "position_status": "Open",
        "position_kind": "main",
        "execution_time_open": "2024-09-12 10:00:00",
        "order_id": "order_456",
        "related_order_id": ['order_789'],
        "instrument_name": "Tesla",
        "instrument_symbol": "TSLA",
        "instrument_uic": 101,
        "instrument_currency": "USD"
    }

    update_data2 = {
        "position_status": "Closed",
        "position_total_performance_percent": -5.0,
        "position_profit_loss": -250.0,
        "execution_time_close": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }

    db_position_manager.insert_turbo_open_position_data(open_data1)
    db_position_manager.insert_turbo_open_position_data(open_data2)
    db_position_manager.update_turbo_position_data("pos_001", update_data1)
    db_position_manager.update_turbo_position_data("pos_002", update_data2)

    # Retrieve percentage change of the day
    percent_of_the_day = db_position_manager.get_percent_of_the_day()

    # Calculate expected percentage change
    expected_percent = 4.5

    assert percent_of_the_day == expected_percent, "The percentage change of the day should be correctly calculated."


def test_get_percent_of_last_n_days(setup_temp_db):
    """Test retrieving the percentage change for the last N days."""
    config_manager = setup_temp_db
    db_position_manager = DbPositionManager(config_manager)

    # Define and insert dummy data
    today = datetime.now().strftime('%Y/%m/%d')

    open_data = {
        "action": "long",
        "position_id": "pos_001",
        "position_amount": 100,
        "position_open_price": 120.5,
        "position_total_open_price": 12050.0,
        "position_status": "Open",
        "position_kind": "main",
        "execution_time_open": f"{today} 10:00:00",
        "order_id": "order_456",
        "related_order_id": ['order_789'],
        "instrument_name": "Tesla",
        "instrument_symbol": "TSLA",
        "instrument_uic": 101,
        "instrument_currency": "USD"
    }

    update_data = {
        "position_status": "Closed",
        "position_total_performance_percent": 10.0,
        "execution_time_close": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "position_profit_loss": 1000.0
    }

    db_position_manager.insert_turbo_open_position_data(open_data)
    db_position_manager.update_turbo_position_data("pos_001", update_data)

    # Retrieve percentage change for the last N days
    n_days = 5
    percent_of_last_n_days = db_position_manager.get_percent_of_last_n_days(n_days)

    assert today in percent_of_last_n_days, "Today's percentage change should be included in the results."
    assert percent_of_last_n_days[today] == 10.0, "The percentage change for today should match the expected value."


def test_get_best_percent_of_last_n_days(setup_temp_db):
    """Test retrieving the maximum percentage change for the last N days."""
    config_manager = setup_temp_db
    db_position_manager = DbPositionManager(config_manager)

    # Define and insert dummy data
    today = datetime.now().strftime('%Y/%m/%d')

    open_data = {
        "action": "long",
        "position_id": "pos_001",
        "position_amount": 100,
        "position_open_price": 120.5,
        "position_total_open_price": 12050.0,
        "position_status": "Open",
        "position_kind": "main",
        "execution_time_open": f"{today} 10:00:00",
        "order_id": "order_456",
        "related_order_id": ['order_789'],
        "instrument_name": "Tesla",
        "instrument_symbol": "TSLA",
        "instrument_uic": 101,
        "instrument_currency": "USD"
    }

    update_data = {
        "position_status": "Closed",
        "position_total_performance_percent": 20.0,
        "execution_time_close": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "position_profit_loss": 2000.0
    }

    db_position_manager.insert_turbo_open_position_data(open_data)
    db_position_manager.update_turbo_position_data("pos_001", update_data)

    # Retrieve best percentage change for the last N days
    n_days = 5
    best_percent_of_last_n_days = db_position_manager.get_best_percent_of_last_n_days(n_days)

    assert today in best_percent_of_last_n_days, "Today's best percentage change should be included in the results."
    assert best_percent_of_last_n_days[today] == 20.0, "The best percentage change for today should match the expected value."


def test_get_theoretical_percent_of_last_n_days_on_max(setup_temp_db):
    """Test retrieving theoretical percentage change based on max performance for the last N days."""
    config_manager = setup_temp_db
    db_position_manager = DbPositionManager(config_manager)

    # Define and insert dummy data
    today = datetime.now().strftime('%Y/%m/%d')

    open_data = {
        "action": "long",
        "position_id": "pos_001",
        "position_amount": 100,
        "position_open_price": 120.5,
        "position_total_open_price": 12050.0,
        "position_status": "Open",
        "position_kind": "main",
        "execution_time_open": f"{today} 10:00:00",
        "order_id": "order_456",
        "related_order_id": ['order_789'],
        "instrument_name": "Tesla",
        "instrument_symbol": "TSLA",
        "instrument_uic": 101,
        "instrument_currency": "USD"
    }

    update_data = {
        "position_status": "Closed",
        "position_max_performance_percent": 30.0,
        "execution_time_close": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "position_profit_loss": 3000.0
    }

    db_position_manager.insert_turbo_open_position_data(open_data)
    db_position_manager.update_turbo_position_data("pos_001", update_data)

    # Retrieve theoretical percentage change for the last N days
    n_days = 5
    theoretical_percent_of_last_n_days_on_max = db_position_manager.get_theoretical_percent_of_last_n_days_on_max(n_days)

    assert today in theoretical_percent_of_last_n_days_on_max, "Today's theoretical percentage change based on max should be included in the results."
    assert theoretical_percent_of_last_n_days_on_max[today] == 30.0, "The theoretical percentage change based on max for today should match the expected value."


def test_get_best_theoretical_percent_of_last_n_days_on_max(setup_temp_db):
    """Test retrieving the maximum theoretical percentage change based on max performance for the last N days."""
    config_manager = setup_temp_db
    db_position_manager = DbPositionManager(config_manager)

    # Define and insert dummy data
    today = datetime.now().strftime('%Y/%m/%d')

    open_data = {
        "action": "long",
        "position_id": "pos_001",
        "position_amount": 100,
        "position_open_price": 120.5,
        "position_total_open_price": 12050.0,
        "position_status": "Open",
        "position_kind": "main",
        "execution_time_open": f"{today} 10:00:00",
        "order_id": "order_456",
        "related_order_id": ['order_789'],
        "instrument_name": "Tesla",
        "instrument_symbol": "TSLA",
        "instrument_uic": 101,
        "instrument_currency": "USD"
    }

    update_data = {
        "position_status": "Closed",
        "position_max_performance_percent": 50.0,
        "execution_time_close": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "position_profit_loss": 5000.0
    }

    db_position_manager.insert_turbo_open_position_data(open_data)
    db_position_manager.update_turbo_position_data("pos_001", update_data)

    # Retrieve best theoretical percentage change for the last N days
    n_days = 5
    best_theoretical_percent_of_last_n_days_on_max = db_position_manager.get_best_theoretical_percent_of_last_n_days_on_max(n_days)

    assert today in best_theoretical_percent_of_last_n_days_on_max, "Today's best theoretical percentage change should be included in the results."
    assert best_theoretical_percent_of_last_n_days_on_max[today] == 50.0, "The best theoretical percentage change for today should match the expected value."


def test_append_performance_message(setup_temp_db):
    """Test appending performance data to a message for the last 7 days."""
    config_manager = setup_temp_db
    db_position_manager = DbPositionManager(config_manager)

    # Insert dummy data into the database for testing
    today = datetime.now().strftime('%Y/%m/%d')
    from datetime import timedelta

    open_data = {
        "action": "long",
        "position_id": "pos_001",
        "position_amount": 100,
        "position_open_price": 120.5,
        "position_total_open_price": 12050.0,
        "position_status": "Open",
        "position_kind": "main",
        "execution_time_open": f"{today} 10:00:00",
        "order_id": "order_456",
        "related_order_id": ['order_789'],
        "instrument_name": "Tesla",
        "instrument_symbol": "TSLA",
        "instrument_uic": 101,
        "instrument_currency": "USD"
    }

    update_data = {
        "position_status": "Closed",
        "position_total_performance_percent": 10.0,
        "position_max_performance_percent": 20.0,
        "execution_time_close": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "position_profit_loss": 1000.0
    }

    # Insert and update data
    db_position_manager.insert_turbo_open_position_data(open_data)
    db_position_manager.update_turbo_position_data("pos_001", update_data)

    # Test the retrieval of performance data for the last 7 days
    last_7_days_percentages = db_position_manager.get_percent_of_last_n_days(7)
    last_best_7_days_percentages = db_position_manager.get_best_percent_of_last_n_days(7)
    last_7_days_percentages_on_max = db_position_manager.get_theoretical_percent_of_last_n_days_on_max(7)
    last_best_7_days_percentages_on_max = db_position_manager.get_best_theoretical_percent_of_last_n_days_on_max(7)

    # Build the performance part of the message
    message = ""
    message = append_performance_message(message, "Last 7 Days Performance real", last_7_days_percentages)
    message = append_performance_message(message, "Last 7 Days Performance best", last_best_7_days_percentages)
    message = append_performance_message(message, "Last 7 Days Performance, on max", last_7_days_percentages_on_max)
    message = append_performance_message(message, "Last 7 Days Performance, best on max",
                                         last_best_7_days_percentages_on_max)

    # Expected message
    expected_message = ""
    for i in range(7):
        day = (datetime.now() - timedelta(days=i)).strftime('%Y/%m/%d')
        if i == 0:
            expected_message += f"\n--- Last 7 Days Performance real ---\n"
            expected_message += f"{day}: 10.00%\n"
        else:
            expected_message += f"{day}: 0.00%\n"
    for i in range(7):
        day = (datetime.now() - timedelta(days=i)).strftime('%Y/%m/%d')
        if i == 0:
            expected_message += f"\n--- Last 7 Days Performance best ---\n"
            expected_message += f"{day}: 10.00%\n"
        else:
            expected_message += f"{day}: 0.00%\n"
    for i in range(7):
        day = (datetime.now() - timedelta(days=i)).strftime('%Y/%m/%d')
        if i == 0:
            expected_message += f"\n--- Last 7 Days Performance, on max ---\n"
            expected_message += f"{day}: 20.00%\n"
        else:
            expected_message += f"{day}: 0.00%\n"
    for i in range(7):
        day = (datetime.now() - timedelta(days=i)).strftime('%Y/%m/%d')
        if i == 0:
            expected_message += f"\n--- Last 7 Days Performance, best on max ---\n"
            expected_message += f"{day}: 20.00%\n"
        else:
            expected_message += f"{day}: 0.00%\n"

    assert message.strip() == expected_message.strip()


def test_database_marked_as_corrupted(setup_temp_db):
    """Test if the database gets marked as corrupted properly."""
    config_manager = setup_temp_db
    db_position_manager = DbPositionManager(config_manager)

    # Mark the database as corrupted
    db_position_manager.mark_database_as_corrupted("Test corruption")

    # Check if the corruption file was created
    corrupted_file_path = f"{os.path.dirname(config_manager.get_config_value('duckdb.persistant.db_path'))}/database_corrupted.txt"

    assert os.path.exists(corrupted_file_path), "The corruption file should exist."

    with open(corrupted_file_path, 'r') as file:
        content = file.read()
        assert "Test corruption" in content, "The corruption file should contain the reason."


def test_is_database_corrupted(setup_temp_db):
    """Test the check for database corruption."""
    config_manager = setup_temp_db
    db_position_manager = DbPositionManager(config_manager)

    # Initially, the database shouldn't be corrupted
    assert not db_position_manager.is_database_corrupted(), "Database should not be corrupted initially."

    # Mark the database as corrupted
    db_position_manager.mark_database_as_corrupted("Test corruption")

    # Now it should be corrupted
    assert db_position_manager.is_database_corrupted(), "Database should be marked as corrupted."