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
        logging.info(f"Trading rules using timezone: {self.timezone}")

    def get_rule_config(self, rule_type):
        """
        Retrieves the rule_config for a given rule_type from the configuration.
        """
        trade_rules = self.config_manager.get_config_value("trade.rules", [])
        for rule in trade_rules:
            if rule.get("rule_type") == rule_type:
                return rule.get("rule_config", {})
        raise TradingRuleViolation(f"Rule with type '{rule_type}' not found in the configuration.")

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
