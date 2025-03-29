# logging_utils.py
import logging
from logging.handlers import RotatingFileHandler


def setup_logging(config_manager, app_name):
    """
    Set up logging with the given config manager and app name.

    :param config_manager: An instance of the ConfigurationManager that provides logging config
    :param app_name: The name of the application (used for the log file name)
    """
    # Retrieve logging configuration
    logging_config = config_manager.get_logging_config()

    # Configure logging with file name based on app_name
    log_file_name = f"{logging_config['persistant']['log_path']}/{app_name}/{app_name}.log"

    # Create a RotatingFileHandler
    handler = RotatingFileHandler(log_file_name, maxBytes=2097152, backupCount=31)

    formatter = logging.Formatter(logging_config["format"])
    handler.setFormatter(formatter)

    # Get the root logger and add the handler to it
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, logging_config["level"].upper()))
    root_logger.addHandler(handler)

    logging.info(f"-------------------------------- Started {app_name} application")