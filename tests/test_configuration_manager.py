import pytest
import json
import os
from unittest.mock import patch, mock_open
from src.configuration import ConfigurationManager

class TestConfigurationManager:

    @patch("builtins.open", new_callable=mock_open, read_data='{"logging": {"level": "DEBUG"}, "rabbitmq": {"host": "localhost"}}')
    def test_load_config_valid(self, mock_file):
        config_manager = ConfigurationManager("dummy_path")
        assert config_manager.config_data["logging"]["level"] == "DEBUG"
        assert config_manager.config_data["rabbitmq"]["host"] == "localhost"

    @patch("os.path.exists", return_value=False)
    def test_load_config_file_not_found(self, mock_exists):
        with pytest.raises(FileNotFoundError):
            ConfigurationManager("dummy_path")

    @patch("builtins.open", new_callable=mock_open, read_data='{"logging": {"level": "DEBUG" "rabbitmq": {"host": "localhost"}}')  # Malformed JSON
    def test_load_config_malformed_json(self, mock_file):
        with pytest.raises(json.JSONDecodeError):
            ConfigurationManager("dummy_path")

    @patch("builtins.open", new_callable=mock_open, read_data='{"logging": {"level": "DEBUG"}, "rabbitmq": {"host": "localhost"}}')
    def test_get_config_value_existing_key(self, mock_file):
        config_manager = ConfigurationManager("dummy_path")
        assert config_manager.get_config_value("logging.level") == "DEBUG"

    @patch("builtins.open", new_callable=mock_open, read_data='{"logging": {"level": "DEBUG"}, "rabbitmq": {"host": "localhost"}}')
    def test_get_config_value_non_existing_key(self, mock_file):
        config_manager = ConfigurationManager("dummy_path")
        assert config_manager.get_config_value("non.existing.key", default="default_value") == "default_value"

    @patch("builtins.open", new_callable=mock_open, read_data='{"logging": {"level": "DEBUG"}, "rabbitmq": {"host": "localhost"}}')
    def test_get_logging_config(self, mock_file):
        config_manager = ConfigurationManager("dummy_path")
        assert config_manager.get_logging_config() == {"level": "DEBUG"}

    @patch("builtins.open", new_callable=mock_open, read_data='{"logging": {"level": "DEBUG"}, "rabbitmq": {"host": "localhost"}}')
    def test_get_rabbitmq_config(self, mock_file):
        config_manager = ConfigurationManager("dummy_path")
        assert config_manager.get_rabbitmq_config() == {"host": "localhost"}
