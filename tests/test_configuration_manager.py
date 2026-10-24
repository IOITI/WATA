import pytest
import json
import os
from unittest.mock import patch, mock_open
from src.configuration import ConfigurationManager

class TestConfigurationManager:

    @patch("os.path.exists", return_value=True)
    @patch("builtins.open", new_callable=mock_open, read_data='{"logging": {"level": "DEBUG"}, "rabbitmq": {"host": "localhost"}}')
    def test_load_config_valid(self, mock_file, mock_exists):
        with patch.object(ConfigurationManager, 'validate_config', return_value=None):
            config_manager = ConfigurationManager("dummy_path")
            assert config_manager.config_data["logging"]["level"] == "DEBUG"
            assert config_manager.config_data["rabbitmq"]["host"] == "localhost"

    @patch("os.path.exists", return_value=False)
    def test_load_config_file_not_found(self, mock_exists):
        with pytest.raises(FileNotFoundError):
            ConfigurationManager("dummy_path")

    @patch("os.path.exists", return_value=True)
    @patch("builtins.open", new_callable=mock_open, read_data='{"logging": {"level": "DEBUG" "rabbitmq": {"host": "localhost"}}')  # Malformed JSON
    def test_load_config_malformed_json(self, mock_file, mock_exists):
        with pytest.raises(json.JSONDecodeError):
            ConfigurationManager("dummy_path")

    @patch("os.path.exists", return_value=True)
    @patch("builtins.open", new_callable=mock_open, read_data='{"logging": {"level": "DEBUG"}, "rabbitmq": {"host": "localhost"}}')
    def test_get_config_value_existing_key(self, mock_file, mock_exists):
        with patch.object(ConfigurationManager, 'validate_config', return_value=None):
            config_manager = ConfigurationManager("dummy_path")
            assert config_manager.get_config_value("logging.level") == "DEBUG"

    @patch("os.path.exists", return_value=True)
    @patch("builtins.open", new_callable=mock_open, read_data='{"logging": {"level": "DEBUG"}, "rabbitmq": {"host": "localhost"}}')
    def test_get_config_value_non_existing_key(self, mock_file, mock_exists):
        with patch.object(ConfigurationManager, 'validate_config', return_value=None):
            config_manager = ConfigurationManager("dummy_path")
            assert config_manager.get_config_value("non.existing.key", default="default_value") == "default_value"

    @patch("os.path.exists", return_value=True)
    @patch("builtins.open", new_callable=mock_open, read_data='{"logging": {"level": "DEBUG"}, "rabbitmq": {"host": "localhost"}}')
    def test_get_logging_config(self, mock_file, mock_exists):
        with patch.object(ConfigurationManager, 'validate_config', return_value=None):
            config_manager = ConfigurationManager("dummy_path")
            assert config_manager.get_logging_config() == {"level": "DEBUG"}

    @patch("os.path.exists", return_value=True)
    @patch("builtins.open", new_callable=mock_open, read_data='{"logging": {"level": "DEBUG"}, "rabbitmq": {"host": "localhost"}}')
    def test_get_rabbitmq_config(self, mock_file, mock_exists):
        with patch.object(ConfigurationManager, 'validate_config', return_value=None):
            config_manager = ConfigurationManager("dummy_path")
            assert config_manager.get_rabbitmq_config() == {"host": "localhost"}


def _config_with_protective_stop(tmp_path, protective_stop):
    with open("tests/test_config.json") as config_file:
        config = json.load(config_file)
    # tests/test_config.json sets the monitor's stop-loss at -20%
    config["trade"]["config"]["position_management"]["protective_stop"] = protective_stop
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    return str(path)


class TestProtectiveStopValidation:

    def test_wider_than_the_stoploss_is_accepted(self, tmp_path):
        path = _config_with_protective_stop(tmp_path, {"enabled": True, "percent": -30, "order_type": "StopIfTraded"})

        config_manager = ConfigurationManager(path)

        assert config_manager.get_config_value("trade.config.position_management.protective_stop.percent") == -30

    @pytest.mark.parametrize("percent", [-20, -10, 5, -100, -150])
    def test_not_wider_than_the_stoploss_or_impossible_is_rejected(self, tmp_path, percent):
        path = _config_with_protective_stop(tmp_path, {"enabled": True, "percent": percent})

        with pytest.raises(ValueError, match="Protective stop percentage"):
            ConfigurationManager(path)

    @pytest.mark.parametrize("percent", [None, "-30", True])
    def test_enabled_without_a_numeric_percent_is_rejected(self, tmp_path, percent):
        path = _config_with_protective_stop(tmp_path, {"enabled": True, "percent": percent})

        with pytest.raises(ValueError, match="protective_stop.percent"):
            ConfigurationManager(path)

    def test_disabled_does_not_need_a_percent(self, tmp_path):
        path = _config_with_protective_stop(tmp_path, {"enabled": False})

        ConfigurationManager(path)

    def test_enabled_must_be_a_boolean(self, tmp_path):
        path = _config_with_protective_stop(tmp_path, {"enabled": "yes", "percent": -30})

        with pytest.raises(ValueError, match="protective_stop.enabled"):
            ConfigurationManager(path)

    def test_order_type_must_be_a_string(self, tmp_path):
        path = _config_with_protective_stop(tmp_path, {"enabled": True, "percent": -30, "order_type": 3})

        with pytest.raises(ValueError, match="protective_stop.order_type"):
            ConfigurationManager(path)


def _config_with_market_timezone(tmp_path, timezone_name):
    with open("etc/config_example.json", encoding="utf-8") as config_file:
        config = json.load(config_file)
    for rule in config["trade"]["rules"]:
        if rule["rule_type"] == "market_hours":
            rule["rule_config"]["timezone"] = timezone_name
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    return str(path)


@pytest.mark.parametrize("timezone_name", ["America/New_York", "Europe/Paris", ""])
def test_the_market_hours_may_name_their_timezone(tmp_path, timezone_name):
    ConfigurationManager(_config_with_market_timezone(tmp_path, timezone_name))


@pytest.mark.parametrize("timezone_name", ["New York", "UTC+1", 5])
def test_a_timezone_the_trader_could_not_read_is_refused(tmp_path, timezone_name):
    with pytest.raises(ValueError, match="timezone must be a timezone name such as America/New_York"):
        ConfigurationManager(_config_with_market_timezone(tmp_path, timezone_name))
