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
        required_sections = [
            "authentication.saxo",
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

        # Validate authentication.saxo structure
        saxo_config = self.get_config_value("authentication.saxo")
        required_saxo_fields = [
            "app_config_object.AppName",
            "app_config_object.AppKey",
            "app_config_object.AuthorizationEndpoint",
            "app_config_object.TokenEndpoint",
            "app_config_object.GrantType",
            "app_config_object.OpenApiBaseUrl",
            "app_config_object.RedirectUrls",
            "app_config_object.AppSecret"
        ]
        
        missing_saxo_fields = []
        for field in required_saxo_fields:
            if self.get_config_value(f"authentication.saxo.{field}") is None:
                missing_saxo_fields.append(field)
        
        if missing_saxo_fields:
            error_msg = f"Missing required Saxo authentication fields: {', '.join(missing_saxo_fields)}"
            logger.error(error_msg)
            raise ValueError(error_msg)

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
                # Validate max_signal_age_minutes
                max_age = rule_config.get("max_signal_age_minutes")
                if max_age is None:
                    error_msg = f"Rule {rule_name}: Missing required field 'max_signal_age_minutes'"
                    logger.error(error_msg)
                    raise ValueError(error_msg)
                
                if not isinstance(max_age, int) or max_age <= 0:
                    error_msg = f"Rule {rule_name}: max_signal_age_minutes must be a positive integer"
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

        # Validate trade.config structure
        trade_config = self.get_config_value("trade.config")
        if trade_config:
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
                    "api_limits.top_positions",
                    "api_limits.top_closed_positions",
                    "retry_config.max_retries",
                    "retry_config.retry_sleep_seconds",
                    "position_check.check_interval_seconds",
                    "position_check.timeout_seconds",
                    "websocket.refresh_rate_ms"
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

            for field, (field_type, min_val, max_val) in numeric_validations.items():
                value = self.get_config_value(f"trade.config.{field}")
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

        # Validate telegram configuration if present
        telegram_config = self.get_config_value("telegram")
        if telegram_config:
            required_telegram_fields = ["bot_token", "chat_id", "bot_name"]
            missing_telegram_fields = [field for field in required_telegram_fields 
                                    if field not in telegram_config]
            if missing_telegram_fields:
                error_msg = f"Missing required telegram fields: {', '.join(missing_telegram_fields)}"
                logger.error(error_msg)
                raise ValueError(error_msg)
            
        logger.info("Configuration validation successful")

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
