import json
import os
import logging

logger = logging.getLogger(__name__)


class ConfigurationManager:
    """
    Manages the loading and access of configuration data.
    """

    def __init__(self, config_path):
        self.config_path = config_path
        self.config_data = None
        self.load_config()

    def load_config(self):
        """
        Loads the configuration file.
        """
        if not os.path.exists(self.config_path):
            logger.error(f"Config file not found: {self.config_path}")
            raise FileNotFoundError(f"Config file not found: {self.config_path}")
        try:
            with open(self.config_path, "r") as file:
                self.config_data = json.load(file)
        except json.JSONDecodeError as e:
            logger.error(f"Error loading credentials: {e}")
            raise

    def get_config_value(self, key, default=None):
        """
        Retrieves a specific configuration value.
        """
        keys = key.split(".")
        config = self.config_data
        for k in keys:
            config = config.get(k, default)
            if config is default:
                return default
        return config

    def get_logging_config(self):
        """
        Retrieves the logging configuration from the config file.
        """
        logging_config = self.get_config_value("logging")
        if not logging_config:
            logger.error("Logging configuration not found in config file.")
            raise ValueError("Logging configuration not found in config file.")

        return logging_config

    def get_rabbitmq_config(self):
        """
        Retrieves the RabbitMQ configuration from the config file.
        """
        logging_config = self.get_config_value("rabbitmq")
        if not logging_config:
            logger.error("Logging configuration not found in config file.")
            raise ValueError("Logging configuration not found in config file.")

        return logging_config
