from datetime import datetime, timedelta
import pytz
import logging
from .exceptions import TradingRuleViolation

class TradingRule:
    def __init__(self, config_manager, db_position_manager):
        self.config_manager = config_manager
        self.db_position_manager = db_position_manager
        self.allowed_indices_rule_config = self.get_rule_config("allowed_indices")
        self.market_closed_dates_list = self.get_rule_config("market_closed_dates")["market_closed_dates"]
        day_trading_config = self.get_rule_config("day_trading")
        self.dont_enter_trade_if_day_profit_is_more_than = day_trading_config["dont_enter_trade_if_day_profit_is_more_than"]
        self.max_day_loss_percent = day_trading_config["max_day_loss_percent"]
        self.signal_validation_config = self.get_rule_config("signal_validation")
        self.market_hours_config = self.get_rule_config("market_hours")
        self.timezone = self.config_manager.get_config_value("trade.config.general.timezone", "Europe/Paris")
        # Risk management config (optional rule)
        self.risk_management_config = self.get_rule_config_safe("risk_management")
        self.cooldown_after_loss_minutes = self.risk_management_config.get("cooldown_after_loss_minutes", 0) if self.risk_management_config else 0
        self.max_trades_per_day = self.risk_management_config.get("max_trades_per_day", 0) if self.risk_management_config else 0
        # Confidence scaling config (optional)
        self.confidence_config = self.config_manager.get_config_value("trade.config.position_sizing.confidence_scaling", {})
        self.min_confidence_threshold = self.confidence_config.get("min_confidence_threshold", 0.0)
        # Track last loss timestamp for cooldown
        self._last_loss_timestamp = None
        logging.info(f"Trading rules using timezone: {self.timezone}")
        if self.cooldown_after_loss_minutes > 0:
            logging.info(f"Cooldown after loss: {self.cooldown_after_loss_minutes} minutes")
        if self.max_trades_per_day > 0:
            logging.info(f"Max trades per day: {self.max_trades_per_day}")

    def get_rule_config(self, rule_type):
        """
        Retrieves the rule_config for a given rule_type from the configuration.
        """
        trade_rules = self.config_manager.get_config_value("trade.rules", [])
        for rule in trade_rules:
            if rule.get("rule_type") == rule_type:
                return rule.get("rule_config", {})
        raise TradingRuleViolation(f"Rule with type '{rule_type}' not found in the configuration.")

    def get_rule_config_safe(self, rule_type):
        """
        Retrieves the rule_config for a given rule_type, returning None if not found.
        """
        trade_rules = self.config_manager.get_config_value("trade.rules", [])
        for rule in trade_rules:
            if rule.get("rule_type") == rule_type:
                return rule.get("rule_config", {})
        return None

    def check_signal_timestamp(self, signal_action, signal_timestamp):
        # Parse the signal_timestamp string into a datetime object
        signal_time = datetime.strptime(signal_timestamp, "%Y-%m-%dT%H:%M:%SZ")
        signal_time = signal_time.replace(tzinfo=pytz.UTC)  # Ensure it's in UTC

        # Get the current time in UTC
        current_time = datetime.now(pytz.utc)

        # Calculate the difference between the current time and the signal_timestamp
        time_difference = current_time - signal_time

        if signal_action == "check_positions_on_saxo_api":
            if time_difference > timedelta(seconds=30):
                logging.error(f"The check_positions_on_saxo_api signal is too old. Current time: {current_time}, Signal time: {signal_time}")
                raise TradingRuleViolation("Signal timestamp is too old")
        else:
            # Check if the difference is more than max_signal_age_minutes
            max_age_minutes = self.signal_validation_config["max_signal_age_minutes"]
            if time_difference > timedelta(minutes=max_age_minutes):
                logging.error(f"The signal is too old. Current time: {current_time}, Signal time: {signal_time}")
                raise TradingRuleViolation("Signal timestamp is too old")

    def get_allowed_indice_id(self, indice):
        """
        Check if the given indice exists in the indices dictionary and return its ID.
        Raises a KeyError if the indice does not exist.
        """
        try:
            return self.allowed_indices_rule_config["indice_ids"][indice]
        except KeyError:
            logging.error(f"Breaking trading rule : Indice '{indice}' does not exist in the provided dictionary.")
            raise TradingRuleViolation(f"Breaking trading rule : Indice '{indice}' does not exist in the provided dictionary.")

    def check_market_hours(self, signal_timestamp):
        # Parse the signal_timestamp string into a datetime object
        signal_time = datetime.strptime(signal_timestamp, "%Y-%m-%dT%H:%M:%SZ")
        signal_time = signal_time.replace(tzinfo=pytz.UTC)  # Ensure it's in UTC

        # Get the current time in configured timezone
        current_time = datetime.now(pytz.timezone(self.timezone))

        # Format the current date to match the format of the list
        today_date_string = current_time.strftime("%d/%m/%Y")

        if today_date_string in self.market_closed_dates_list:
            message = f"Breaking trading rule : Today, {current_time}, is a market closed date."
            logging.error(message)
            raise TradingRuleViolation(message)

        # Check if the current time is within the allowed range
        trading_start_hour = self.market_hours_config["trading_start_hour"]
        trading_end_hour = self.market_hours_config["trading_end_hour"]
        if not (trading_start_hour <= current_time.hour < trading_end_hour):
            logging.error(
                f"Breaking trading rule : The signal is outside of market hours. Current time: {current_time}, Signal time: {signal_time}")
            raise TradingRuleViolation("Signal is outside of market hours.")

        # Check if the current time is within the refused range
        risky_start_hour = self.market_hours_config["risky_trading_start_hour"]
        risky_start_minute = self.market_hours_config["risky_trading_start_minute"]
        if risky_start_hour <= current_time.hour < trading_end_hour and current_time.minute >= risky_start_minute:
            logging.error(
                f"Breaking trading rule: The signal is refused due to risky market hours. Current time: {current_time}, Signal time: {signal_time}")
            raise TradingRuleViolation("Signal is refused due to risky market hours.")

    def check_profit_per_day(self):
        today_percent = self.db_position_manager.get_percent_of_the_day()
        if today_percent >= self.dont_enter_trade_if_day_profit_is_more_than:
            message = (f"Breaking trading rule : The current profit percentage ({today_percent}) is more than the "
                       f"allowed percentage ({self.dont_enter_trade_if_day_profit_is_more_than}), "
                       f"so no more trade are allowed for today.")
            logging.info(message)
            raise TradingRuleViolation(message)
        
        if today_percent <= self.max_day_loss_percent:
            message = (f"Breaking trading rule : The current loss percentage ({today_percent}) has reached the "
                       f"maximum allowed loss ({self.max_day_loss_percent}), "
                       f"so no more trade are allowed for today.")
            logging.error(message)
            raise TradingRuleViolation(message)

    @staticmethod
    def check_if_open_position_is_same_signal(action, db_position_manager):
        if action == "long" or action == "short":
            db_open_position_ids_actions = db_position_manager.get_open_positions_ids_actions()
            for db_position_info in db_open_position_ids_actions:
                db_position_id = db_position_info['position_id']
                db_action = db_position_info['action']
                if db_action == action:
                    message = (f"Breaking trading rule: The signal is refused due to an open position {db_position_id}"
                               f" with the same action {action}.")
                    logging.info(message)
                    raise TradingRuleViolation(message)

    def check_cooldown_after_loss(self):
        """
        Checks if we are still in a cooldown period after the last losing trade.
        Prevents entering trades too quickly after a loss.
        """
        if self.cooldown_after_loss_minutes <= 0:
            return  # Cooldown disabled

        if self._last_loss_timestamp is None:
            return  # No loss recorded yet

        current_time = datetime.now(pytz.utc)
        cooldown_end = self._last_loss_timestamp + timedelta(minutes=self.cooldown_after_loss_minutes)

        if current_time < cooldown_end:
            remaining = (cooldown_end - current_time).total_seconds()
            message = (f"Breaking trading rule: Cooldown active after last loss. "
                       f"Remaining: {remaining:.0f}s (cooldown: {self.cooldown_after_loss_minutes}min)")
            logging.info(message)
            raise TradingRuleViolation(message)

    def record_loss(self):
        """Records the timestamp of a losing trade for cooldown tracking."""
        self._last_loss_timestamp = datetime.now(pytz.utc)
        logging.info(f"Loss recorded at {self._last_loss_timestamp}. Cooldown of {self.cooldown_after_loss_minutes} minutes activated.")

    def check_max_trades_per_day(self):
        """
        Checks if the maximum number of trades per day has been reached.
        """
        if self.max_trades_per_day <= 0:
            return  # Limit disabled

        if self.db_position_manager is None:
            return  # No DB manager available

        today_trades = self.db_position_manager.get_today_trade_count()
        if today_trades >= self.max_trades_per_day:
            message = (f"Breaking trading rule: Maximum trades per day reached "
                       f"({today_trades}/{self.max_trades_per_day}).")
            logging.info(message)
            raise TradingRuleViolation(message)

    def check_confidence_threshold(self, confidence):
        """
        Checks if the signal confidence meets the minimum threshold.
        """
        if not self.confidence_config.get("enabled", False):
            return  # Confidence checking disabled

        if confidence is not None and confidence < self.min_confidence_threshold:
            message = (f"Breaking trading rule: Signal confidence ({confidence:.2f}) is below "
                       f"minimum threshold ({self.min_confidence_threshold:.2f}).")
            logging.info(message)
            raise TradingRuleViolation(message)
