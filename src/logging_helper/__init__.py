# logging_utils.py
import logging
from logging.handlers import RotatingFileHandler
import os
from urllib.parse import urlsplit

from src.logging_helper.redaction import RedactingFilter, register_secret

# Libraries that log each request at INFO with its whole address: the outside watcher's,
# Telegram's (the bot's token in it), Saxo's (the account keys in its query); and at DEBUG,
# websockets the stream's address with its token. Nothing they say below WARNING is needed,
# so they stay there whatever logging.level says.
QUIET_LOGGERS = ("httpx", "httpcore", "websockets", "hpack")

# The secrets of the configuration file, never written in a log line by any service.
CONFIG_SECRETS = (
    "authentication.saxo.app_config_object.AppSecret",
    "webserver.app_secret",
    "telegram.bot_token",
    "rabbitmq.authentication.password",
)


def register_config_secrets(config_manager) -> None:
    """Register the configuration's secrets with the redaction, the database's password included."""
    for path in CONFIG_SECRETS:
        register_secret(path, config_manager.get_config_value(path))
    dsn = config_manager.get_config_value("postgresql.dsn")
    if isinstance(dsn, str):
        try:
            register_secret("postgresql.password", urlsplit(dsn).password)
        except ValueError:
            pass


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

    # Create directory if it doesn't exist
    os.makedirs(os.path.dirname(log_file_name), exist_ok=True)

    # Create a RotatingFileHandler
    handler = RotatingFileHandler(log_file_name, maxBytes=2097152, backupCount=31)

    formatter = logging.Formatter(logging_config["format"])
    handler.setFormatter(formatter)
    # Every line, whatever logger wrote it, cleaned of secrets before it reaches the file.
    handler.addFilter(RedactingFilter())
    register_config_secrets(config_manager)

    # Get the root logger and add the handler to it
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, logging_config["level"].upper()))
    root_logger.addHandler(handler)
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    logging.info(f"-------------------------------- Started {app_name} application")
