import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta
import pytz
from src.trade.rules import TradingRule
from src.trade.exceptions import TradingRuleViolation

class TestTradingRule(unittest.TestCase):
    def setUp(self):
        self.config_manager = MagicMock()
        self.db_position_manager = MagicMock()

        self.mock_config = {
            "trade.rules": [
                {
                    "rule_type": "allowed_indices",
                    "rule_config": {
                        "indice_ids": {
                            "us100": 12345
                        }
                    }
                },
                {
                    "rule_type": "market_closed_dates",
                    "rule_config": {
                        "market_closed_dates": ["25/12/2023"]
                    }
                },
                {
                    "rule_type": "day_trading",
                    "rule_config": {
                        "dont_enter_trade_if_day_profit_is_more_than": 1.5,
                        "max_day_loss_percent": -2.0
                    }
                },
                {
                    "rule_type": "signal_validation",
                    "rule_config": {
                        "max_signal_age_minutes": 5
                    }
                },
                {
                    "rule_type": "market_hours",
                    "rule_config": {
                        "trading_start_hour": 9,
                        "trading_end_hour": 22,
                        "risky_trading_start_hour": 21,
                        "risky_trading_start_minute": 30
                    }
                }
            ],
            "trade.config.general.timezone": "Europe/Paris"
        }

        self.config_manager.get_config_value.side_effect = lambda key, default=None: self.mock_config.get(key, default) if key != "trade.rules" else [rule for rule in self.mock_config["trade.rules"]]

    def _get_trading_rule_instance(self):
        # This helper function allows us to re-initialize TradingRule with the current mock setup
        return TradingRule(self.config_manager, self.db_position_manager)

    def test_get_rule_config(self):
        trading_rule = self._get_trading_rule_instance()
        # Test successful retrieval
        config = trading_rule.get_rule_config("allowed_indices")
        self.assertEqual(config, {"indice_ids": {"us100": 12345}})
        # Test rule not found
        with self.assertRaises(TradingRuleViolation):
            trading_rule.get_rule_config("non_existent_rule")

    def test_check_signal_timestamp(self):
        trading_rule = self._get_trading_rule_instance()
        # Test valid timestamp
        valid_timestamp = (datetime.now(pytz.utc) - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        trading_rule.check_signal_timestamp("long", valid_timestamp) # Should not raise
        # Test old timestamp
        old_timestamp = (datetime.now(pytz.utc) - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self.assertRaises(TradingRuleViolation):
            trading_rule.check_signal_timestamp("long", old_timestamp)
        # Test special case for check_positions_on_saxo_api
        valid_check_timestamp = (datetime.now(pytz.utc) - timedelta(seconds=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
        trading_rule.check_signal_timestamp("check_positions_on_saxo_api", valid_check_timestamp) # Should not raise
        # Test old timestamp for special case
        old_check_timestamp = (datetime.now(pytz.utc) - timedelta(seconds=40)).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self.assertRaises(TradingRuleViolation):
            trading_rule.check_signal_timestamp("check_positions_on_saxo_api", old_check_timestamp)

    def test_get_allowed_indice_id(self):
        trading_rule = self._get_trading_rule_instance()
        # Test allowed indice
        indice_id = trading_rule.get_allowed_indice_id("us100")
        self.assertEqual(indice_id, 12345)
        # Test disallowed indice
        with self.assertRaises(TradingRuleViolation):
            trading_rule.get_allowed_indice_id("fr40")

    @patch('src.trade.rules.datetime')
    def test_check_market_hours(self, mock_datetime):
        trading_rule = self._get_trading_rule_instance()
        paris_tz = pytz.timezone("Europe/Paris")

        # Mock current time to be within market hours
        mock_datetime.now.return_value = paris_tz.localize(datetime(2023, 12, 26, 10, 0))
        valid_timestamp = "2023-12-26T09:00:00Z"
        trading_rule.check_market_hours(valid_timestamp) # Should not raise

        # Mock current time to be on a closed date
        mock_datetime.now.return_value = paris_tz.localize(datetime(2023, 12, 25, 10, 0))
        with self.assertRaisesRegex(TradingRuleViolation, "market closed date"):
            trading_rule.check_market_hours("2023-12-25T09:00:00Z")

        # Mock current time to be outside trading hours (too early)
        mock_datetime.now.return_value = paris_tz.localize(datetime(2023, 12, 26, 8, 0))
        with self.assertRaisesRegex(TradingRuleViolation, "outside of market hours"):
            trading_rule.check_market_hours("2023-12-26T07:00:00Z")

        # Mock current time to be outside trading hours (too late)
        mock_datetime.now.return_value = paris_tz.localize(datetime(2023, 12, 26, 23, 0))
        with self.assertRaisesRegex(TradingRuleViolation, "outside of market hours"):
            trading_rule.check_market_hours("2023-12-26T22:00:00Z")

        # Mock current time to be in the risky period
        mock_datetime.now.return_value = paris_tz.localize(datetime(2023, 12, 26, 21, 45))
        with self.assertRaisesRegex(TradingRuleViolation, "risky market hours"):
            trading_rule.check_market_hours("2023-12-26T20:45:00Z")

    def test_check_profit_per_day(self):
        trading_rule = self._get_trading_rule_instance()

        # Test within profit/loss limits
        self.db_position_manager.get_percent_of_the_day.return_value = 1.0
        trading_rule.check_profit_per_day() # Should not raise

        # Test profit limit exceeded
        self.db_position_manager.get_percent_of_the_day.return_value = 1.6
        with self.assertRaisesRegex(TradingRuleViolation, "profit percentage"):
            trading_rule.check_profit_per_day()

        # Test loss limit exceeded
        self.db_position_manager.get_percent_of_the_day.return_value = -2.5
        with self.assertRaisesRegex(TradingRuleViolation, "loss percentage"):
            trading_rule.check_profit_per_day()

    def test_check_if_open_position_is_same_signal(self):
        # Test with no open positions
        self.db_position_manager.get_open_positions_ids_actions.return_value = []
        TradingRule.check_if_open_position_is_same_signal("long", self.db_position_manager) # Should not raise

        # Test with an open position of a different action
        self.db_position_manager.get_open_positions_ids_actions.return_value = [{'position_id': '1', 'action': 'short'}]
        TradingRule.check_if_open_position_is_same_signal("long", self.db_position_manager) # Should not raise

        # Test with an open position of the same action
        self.db_position_manager.get_open_positions_ids_actions.return_value = [{'position_id': '1', 'action': 'long'}]
        with self.assertRaisesRegex(TradingRuleViolation, "open position .* with the same action"):
            TradingRule.check_if_open_position_is_same_signal("long", self.db_position_manager)

if __name__ == '__main__':
    unittest.main()
