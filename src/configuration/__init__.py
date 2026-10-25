import json
import os
import logging

import pytz

from src.trade.macro_calendar import NewsRule

logger = logging.getLogger(__name__)


class ConfigurationManager:
    """
    Manages the loading and access of configuration data.
    """

    def __init__(self, config_path):
        self.config_path = config_path
        self.config_data = None
        self.load_config()
        self.validate_config()

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

    def validate_config(self):
        """
        Validates that all required configuration sections and fields are present.
        """
        # Basic required sections
        # Saxo's app and webserver.app_secret are no longer required: the apps are credentials sealed in
        # PostgreSQL (src/vault/), and config.json's app object and secret are only their old places.
        required_sections = [
            "authentication.persistant.token_path",
            "webserver.persistant.token_path",
            "logging.persistant.log_path",
            "logging.level",
            "rabbitmq.hostname",
            "rabbitmq.authentication.username",
            "rabbitmq.authentication.password",
            "duckdb.persistant.db_path",
            "trade.rules",
            "trade.config.turbo_preference.exchange_id",
            "trade.config.buying_power.max_account_funds_to_use_percentage",
            "trade.persistant.last_action_file"
        ]
        
        # Validate basic sections
        missing_sections = []
        for section in required_sections:
            if self.get_config_value(section) is None:
                missing_sections.append(section)
        
        if missing_sections:
            error_msg = f"Missing required configuration sections: {', '.join(missing_sections)}"
            logger.error(error_msg)
            raise ValueError(error_msg)

        # Saxo's app object, where it is still kept (its old place): its key and secret together, or neither
        # once the purge took them out (python -m src.vault migrate --purge-old), its addresses staying.
        saxo_app = self.get_config_value("authentication.saxo.app_config_object")
        if saxo_app is not None:
            if not isinstance(saxo_app, dict):
                error_msg = "Config field 'authentication.saxo.app_config_object' must be an object"
                logger.error(error_msg)
                raise ValueError(error_msg)
            missing_saxo_fields = [field for field in ("AppKey", "AppSecret") if not saxo_app.get(field)]
            if len(missing_saxo_fields) == 1:
                error_msg = f"Missing required Saxo authentication fields: {', '.join(missing_saxo_fields)}"
                logger.error(error_msg)
                raise ValueError(error_msg)

        # Validate webserver.app_secret
        app_secret = self.get_config_value("webserver.app_secret")
        if app_secret == "CHANGE_THIS_TO_A_STRONG_SECRET_KEY":
            logger.warning("webserver.app_secret is using the default value. For production, it should be changed to a strong secret key.")
        
        # Validate logging configuration
        logging_config = self.get_config_value("logging")
        if not logging_config.get("format"):
            logger.warning("Logging format not specified, using default format")
            logging_config["format"] = "%(asctime)s - %(levelname)s - %(name)s - %(message)s"
        
        # Validate logging level
        valid_log_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        log_level = logging_config.get("level", "").upper()
        if log_level not in valid_log_levels:
            error_msg = f"Invalid logging level: {log_level}. Must be one of {valid_log_levels}"
            logger.error(error_msg)
            raise ValueError(error_msg)

        # Validate trade rules
        trade_rules = self.get_config_value("trade.rules")
        if not isinstance(trade_rules, list):
            error_msg = "trade.rules must be a list"
            logger.error(error_msg)
            raise ValueError(error_msg)

        required_rules = ["allowed_indices", "market_closed_dates", "signal_validation", "market_hours"]
        
        # Check if day_trading rule is required based on trading_mode
        trading_mode = self.get_config_value("trade.config.trading_mode")
        if trading_mode == "day_trading":
            required_rules.append("day_trading")
        
        found_rules = [rule.get("rule_type") for rule in trade_rules]
        
        missing_rules = [rule for rule in required_rules if rule not in found_rules]
        if missing_rules:
            error_msg = f"Missing required trading rules: {', '.join(missing_rules)}"
            logger.error(error_msg)
            raise ValueError(error_msg)
            
        # Validate each rule's structure and configuration
        for rule in trade_rules:
            rule_type = rule.get("rule_type")
            rule_name = rule.get("rule_name")
            rule_config = rule.get("rule_config")
            
            if not rule_type or not rule_name or not rule_config:
                error_msg = f"Invalid rule structure. Each rule must have rule_type, rule_name, and rule_config"
                logger.error(error_msg)
                raise ValueError(error_msg)
                
            # Validate specific rule configurations
            if rule_type == "allowed_indices":
                if not isinstance(rule_config.get("indice_ids"), dict):
                    error_msg = f"Rule {rule_name}: indice_ids must be a dictionary"
                    logger.error(error_msg)
                    raise ValueError(error_msg)
                    
            elif rule_type == "market_closed_dates":
                market_dates = rule_config.get("market_closed_dates")
                if not isinstance(market_dates, list):
                    error_msg = f"Rule {rule_name}: market_closed_dates must be a list"
                    logger.error(error_msg)
                    raise ValueError(error_msg)
                    
                # Validate date format (DD/MM/YYYY)
                import re
                date_pattern = re.compile(r'^\d{2}/\d{2}/\d{4}$')
                for date in market_dates:
                    if not date_pattern.match(date):
                        error_msg = f"Rule {rule_name}: Invalid date format '{date}'. Expected format: DD/MM/YYYY"
                        logger.error(error_msg)
                        raise ValueError(error_msg)
                        
            elif rule_type == "day_trading":
                # Validate numeric fields
                numeric_fields = {
                    "percent_profit_wanted_per_days": (float, 0, 100),
                    "dont_enter_trade_if_day_profit_is_more_than": (float, 0, 100),
                    "max_day_loss_percent": (float, -100, 0)
                }
                
                for field, (field_type, min_val, max_val) in numeric_fields.items():
                    value = rule_config.get(field)
                    if value is None:
                        error_msg = f"Rule {rule_name}: Missing required field '{field}'"
                        logger.error(error_msg)
                        raise ValueError(error_msg)
                        
                    if not isinstance(value, field_type):
                        error_msg = f"Rule {rule_name}: Field '{field}' must be a {field_type.__name__}"
                        logger.error(error_msg)
                        raise ValueError(error_msg)
                        
                    if not min_val <= value <= max_val:
                        error_msg = f"Rule {rule_name}: Field '{field}' must be between {min_val} and {max_val}"
                        logger.error(error_msg)
                        raise ValueError(error_msg)
                
                # Validate time format
                close_time = rule_config.get("close_position_time")
                if not close_time:
                    error_msg = f"Rule {rule_name}: Missing required field 'close_position_time'"
                    logger.error(error_msg)
                    raise ValueError(error_msg)
                    
                time_pattern = re.compile(r'^([01]?[0-9]|2[0-3]):[0-5][0-9]$')
                if not time_pattern.match(close_time):
                    error_msg = f"Rule {rule_name}: Invalid time format '{close_time}'. Expected format: HH:MM"
                    logger.error(error_msg)
                    raise ValueError(error_msg)

            elif rule_type == "signal_validation":
                # Validate max_signal_age_seconds
                max_age = rule_config.get("max_signal_age_seconds")
                if max_age is None:
                    error_msg = f"Rule {rule_name}: Missing required field 'max_signal_age_seconds'"
                    logger.error(error_msg)
                    raise ValueError(error_msg)
                
                if not isinstance(max_age, int) or max_age <= 0:
                    error_msg = f"Rule {rule_name}: max_signal_age_seconds must be a positive integer"
                    logger.error(error_msg)
                    raise ValueError(error_msg)

            elif rule_type == "market_hours":
                # Validate market hours configuration
                required_fields = {
                    "trading_start_hour": (int, 0, 23),
                    "trading_end_hour": (int, 0, 23),
                    "risky_trading_start_hour": (int, 0, 23),
                    "risky_trading_start_minute": (int, 0, 59)
                }
                
                for field, (field_type, min_val, max_val) in required_fields.items():
                    value = rule_config.get(field)
                    if value is None:
                        error_msg = f"Rule {rule_name}: Missing required field '{field}'"
                        logger.error(error_msg)
                        raise ValueError(error_msg)
                    
                    if not isinstance(value, field_type):
                        error_msg = f"Rule {rule_name}: Field '{field}' must be a {field_type.__name__}"
                        logger.error(error_msg)
                        raise ValueError(error_msg)
                    
                    if not min_val <= value <= max_val:
                        error_msg = f"Rule {rule_name}: Field '{field}' must be between {min_val} and {max_val}"
                        logger.error(error_msg)
                        raise ValueError(error_msg)
                
                # Validate that trading hours are in correct order
                if rule_config["trading_start_hour"] >= rule_config["trading_end_hour"]:
                    error_msg = f"Rule {rule_name}: trading_start_hour must be less than trading_end_hour"
                    logger.error(error_msg)
                    raise ValueError(error_msg)
                
                if rule_config["risky_trading_start_hour"] < rule_config["trading_start_hour"] or \
                   rule_config["risky_trading_start_hour"] >= rule_config["trading_end_hour"]:
                    error_msg = f"Rule {rule_name}: risky_trading_start_hour must be between trading_start_hour and trading_end_hour"
                    logger.error(error_msg)
                    raise ValueError(error_msg)

                # Optional: the timezone of these hours (and of close_position_time), the trading one otherwise.
                market_timezone = rule_config.get("timezone")
                if market_timezone not in (None, ""):
                    try:
                        if not isinstance(market_timezone, str):
                            raise ValueError
                        pytz.timezone(market_timezone)
                    except (ValueError, pytz.UnknownTimeZoneError):
                        error_msg = f"Rule {rule_name}: timezone must be a timezone name such as America/New_York"
                        logger.error(error_msg)
                        raise ValueError(error_msg) from None

            elif rule_type == "macro_news":
                # Optional: no entry around the US releases it names (src/trade/macro_calendar.py).
                try:
                    NewsRule.from_config(rule_config)
                except ValueError as e:
                    error_msg = f"Rule {rule_name}: {e}"
                    logger.error(error_msg)
                    raise ValueError(error_msg) from None

        # Validate trade.config structure
        trade_config = self.get_config_value("trade.config")
        if trade_config:
            # Validate timezone if present
            timezone = self.get_config_value("trade.config.general.timezone")
            if timezone:
                try:
                    pytz.timezone(timezone)
                except pytz.exceptions.UnknownTimeZoneError:
                    error_msg = f"Invalid timezone '{timezone}' in trade.config.general.timezone"
                    logger.error(error_msg)
                    raise ValueError(error_msg)
            else:
                logger.warning("Timezone not specified in config, using default 'Europe/Paris'")
                # Set default timezone
                if "general" not in trade_config:
                    trade_config["general"] = {}
                if "timezone" not in trade_config["general"]:
                    trade_config["general"]["timezone"] = "Europe/Paris"
            
            # Validate turbo_preference structure
            required_configs = {
                "turbo_preference": [
                    "exchange_id",
                    "price_range.min",
                    "price_range.max"
                ],
                "buying_power": [
                    "max_account_funds_to_use_percentage",
                    "safety_margins.bid_calculation"
                ],
                "position_management": [
                    "performance_thresholds.stoploss_percent",
                    "performance_thresholds.max_profit_percent"
                ],
                "general": [
                    "api_limits.top_instruments",
                    "api_limits.top_closed_positions",
                    "retry_config.max_retries",
                ]
            }
            
            for section, fields in required_configs.items():
                missing_fields = []
                for field in fields:
                    if self.get_config_value(f"trade.config.{section}.{field}") is None:
                        missing_fields.append(field)
                
                if missing_fields:
                    error_msg = f"Missing required {section} configuration fields: {', '.join(missing_fields)}"
                    logger.error(error_msg)
                    raise ValueError(error_msg)

            # Validate numeric types and ranges
            numeric_validations = {
                "turbo_preference.price_range.min": (float, 0, None),  # min 0
                "turbo_preference.price_range.max": (float, 0, None),  # min 0
                "position_management.performance_thresholds.stoploss_percent": (float, None, None), # Can be negative
                "position_management.performance_thresholds.max_profit_percent": (float, None, None), # No specific range
                "general.api_limits.top_instruments": (int, 1, None), # min 1
                "general.api_limits.top_positions": (int, 1, None), # min 1
                "general.api_limits.top_closed_positions": (int, 1, None), # min 1
                "general.retry_config.max_retries": (int, 0, None), # min 0
                "general.retry_config.retry_sleep_seconds": (int, 0, None), # min 0
                "general.position_check.check_interval_seconds": (int, 1, None), # min 1
                "general.position_check.timeout_seconds": (int, 1, None), # min 1
                "buying_power.safety_margins.bid_calculation": (int, 0, None), # min 0
                "general.websocket.refresh_rate_ms": (int, 100, None), # min 100ms
                "buying_power.max_account_funds_to_use_percentage": (int, 1, 100) # percentage between 1 and 100
            }

            # Read by nothing any more (the Configuration page says so): checked only when still there.
            unused_fields = {
                "general.api_limits.top_positions",
                "general.retry_config.retry_sleep_seconds",
                "general.position_check.check_interval_seconds",
                "general.position_check.timeout_seconds",
                "general.websocket.refresh_rate_ms",
            }

            for field, (field_type, min_val, max_val) in numeric_validations.items():
                value = self.get_config_value(f"trade.config.{field}")
                if value is None and field in unused_fields:
                    continue
                if not isinstance(value, (int, float)):
                    # Allow int for float types
                    if field_type is float and isinstance(value, int):
                        pass # Integer is acceptable for float fields
                    else:
                        error_msg = f"Config field 'trade.config.{field}' must be numeric (expected {field_type.__name__})"
                        logger.error(error_msg)
                        raise ValueError(error_msg)

                if min_val is not None and value < min_val:
                    error_msg = f"Config field 'trade.config.{field}' ({value}) must be at least {min_val}"
                    logger.error(error_msg)
                    raise ValueError(error_msg)
                if max_val is not None and value > max_val:
                    error_msg = f"Config field 'trade.config.{field}' ({value}) must be no more than {max_val}"
                    logger.error(error_msg)
                    raise ValueError(error_msg)

            # Validate price range logic
            min_price = self.get_config_value("trade.config.turbo_preference.price_range.min")
            max_price = self.get_config_value("trade.config.turbo_preference.price_range.max")
            if min_price >= max_price:
                error_msg = f"Minimum price ({min_price}) must be less than maximum price ({max_price}) in trade.config.turbo_preference.price_range"
                logger.error(error_msg)
                raise ValueError(error_msg)

            # Validate performance threshold logic
            stoploss_percent = self.get_config_value("trade.config.position_management.performance_thresholds.stoploss_percent")
            max_profit_percent = self.get_config_value("trade.config.position_management.performance_thresholds.max_profit_percent")
            if stoploss_percent >= max_profit_percent:
                 error_msg = f"Stoploss percentage ({stoploss_percent}) must be less than max profit percentage ({max_profit_percent}) in trade.config.position_management.performance_thresholds"
                 logger.error(error_msg)
                 raise ValueError(error_msg)

            # Validate the optional broker-side protective stop
            protective_stop = self.get_config_value("trade.config.position_management.protective_stop")
            if protective_stop:
                enabled = protective_stop.get("enabled", False)
                if not isinstance(enabled, bool):
                    error_msg = "Config field 'trade.config.position_management.protective_stop.enabled' must be a boolean"
                    logger.error(error_msg)
                    raise ValueError(error_msg)

                order_type = protective_stop.get("order_type")
                if order_type is not None and not isinstance(order_type, str):
                    error_msg = "Config field 'trade.config.position_management.protective_stop.order_type' must be a string"
                    logger.error(error_msg)
                    raise ValueError(error_msg)

                if enabled:
                    percent = protective_stop.get("percent")
                    if isinstance(percent, bool) or not isinstance(percent, (int, float)):
                        error_msg = "Config field 'trade.config.position_management.protective_stop.percent' must be numeric"
                        logger.error(error_msg)
                        raise ValueError(error_msg)
                    # Wider than the monitor's stop-loss, so that it only acts when the monitor didn't
                    if not -100 < percent < stoploss_percent:
                        error_msg = (
                            f"Protective stop percentage ({percent}) must be between -100 and the stoploss percentage "
                            f"({stoploss_percent}) in trade.config.position_management"
                        )
                        logger.error(error_msg)
                        raise ValueError(error_msg)

            # The full level of Saxo's session, for real-time prices (src/trade/trader_watch.py): off unless true
            saxo_session = self.get_config_value("trade.config.saxo_session")
            if saxo_session is not None:
                if not isinstance(saxo_session, dict):
                    error_msg = "Config field 'trade.config.saxo_session' must be an object"
                    logger.error(error_msg)
                    raise ValueError(error_msg)
                if not isinstance(saxo_session.get("full_trading_level", False), bool):
                    error_msg = "Config field 'trade.config.saxo_session.full_trading_level' must be a boolean"
                    logger.error(error_msg)
                    raise ValueError(error_msg)

            # Exits wait for a turbo's first quote after its purchase (src/trade/fresh_quote.py): on unless false
            fresh_quote = self.get_config_value("trade.config.position_management.wait_for_fresh_quote")
            if fresh_quote is not None and not isinstance(fresh_quote, bool):
                error_msg = "Config field 'trade.config.position_management.wait_for_fresh_quote' must be a boolean"
                logger.error(error_msg)
                raise ValueError(error_msg)

            watchlist_config = self.get_config_value("trade.config.watchlist_manager")
            if watchlist_config:
                watchlist_numeric_validations = {
                    "api_port": (int, 1, 65535),
                    "refresh_interval_seconds": (int, 1, None),
                    "stale_after_seconds": (int, 1, None),
                    "request_timeout_seconds": ((int, float), 0, None),
                }

                for field, (field_type, min_val, max_val) in watchlist_numeric_validations.items():
                    value = watchlist_config.get(field)
                    if value is None:
                        continue

                    if not isinstance(value, field_type):
                        if isinstance(field_type, tuple):
                            expected_type = ", ".join(item.__name__ for item in field_type)
                        else:
                            expected_type = field_type.__name__
                        error_msg = f"Config field 'trade.config.watchlist_manager.{field}' must be a {expected_type}"
                        logger.error(error_msg)
                        raise ValueError(error_msg)

                    if min_val is not None and value < min_val:
                        error_msg = f"Config field 'trade.config.watchlist_manager.{field}' ({value}) must be at least {min_val}"
                        logger.error(error_msg)
                        raise ValueError(error_msg)
                    if max_val is not None and value > max_val:
                        error_msg = f"Config field 'trade.config.watchlist_manager.{field}' ({value}) must be no more than {max_val}"
                        logger.error(error_msg)
                        raise ValueError(error_msg)

                directions = watchlist_config.get("directions")
                if directions is not None:
                    if not isinstance(directions, list) or not directions:
                        error_msg = "Config field 'trade.config.watchlist_manager.directions' must be a non-empty list"
                        logger.error(error_msg)
                        raise ValueError(error_msg)

                    invalid_directions = [direction for direction in directions if direction not in ("long", "short")]
                    if invalid_directions:
                        error_msg = (
                            "Config field 'trade.config.watchlist_manager.directions' contains invalid entries: "
                            f"{', '.join(invalid_directions)}"
                        )
                        logger.error(error_msg)
                        raise ValueError(error_msg)

                api_host = watchlist_config.get("api_host")
                if api_host is not None and not isinstance(api_host, str):
                    error_msg = "Config field 'trade.config.watchlist_manager.api_host' must be a string"
                    logger.error(error_msg)
                    raise ValueError(error_msg)

                service_url = watchlist_config.get("service_url")
                if service_url is not None and not isinstance(service_url, str):
                    error_msg = "Config field 'trade.config.watchlist_manager.service_url' must be a string"
                    logger.error(error_msg)
                    raise ValueError(error_msg)

                startup_refresh = watchlist_config.get("startup_refresh")
                if startup_refresh is not None and not isinstance(startup_refresh, bool):
                    error_msg = "Config field 'trade.config.watchlist_manager.startup_refresh' must be a boolean"
                    logger.error(error_msg)
                    raise ValueError(error_msg)

                for field in ("enabled", "accept_stale"):
                    value = watchlist_config.get(field)
                    if value is not None and not isinstance(value, bool):
                        error_msg = f"Config field 'trade.config.watchlist_manager.{field}' must be a boolean"
                        logger.error(error_msg)
                        raise ValueError(error_msg)

        # The Telegram bot's token and chat id are a credential: kept in PostgreSQL, laid over this
        # file in memory by the services that read it (apply_credentials), and no longer required here.
        telegram_config = self.get_config_value("telegram")
        if telegram_config is not None and not isinstance(telegram_config, dict):
            error_msg = "Config field 'telegram' must be an object"
            logger.error(error_msg)
            raise ValueError(error_msg)

        logger.info("Configuration validation successful")

    def apply_credentials(self, values: dict) -> None:
        """
        Lays credentials read from PostgreSQL (src/vault/loader.py) over the
        file's values, in memory only: the code that reads them by their path
        reads them as before, and nothing is written to the file.
        """
        for path, value in values.items():
            node = self.config_data
            *parents, last = path.split(".")
            for key in parents:
                child = node.get(key)
                if not isinstance(child, dict):
                    child = node[key] = {}
                node = child
            node[last] = value

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
