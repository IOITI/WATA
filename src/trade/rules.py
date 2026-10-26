from datetime import datetime, timedelta
import pytz
import logging
from .account_guard import AccountGuard
from .day_breakers import DayBreakers
from .exceptions import TradingRuleViolation
from .macro_calendar import NewsRule, read_calendar, refusal_text, window_at
from .market_calendar import (
    closed_text, early_close_text, entries_end_moment, read_market_calendar, sale_moment,
)

class TradingRule:
    def __init__(self, config_manager, db_position_manager, db_risk_state_manager=None, db_balance_manager=None):
        self.config_manager = config_manager
        self.db_position_manager = db_position_manager
        self.db_risk_state_manager = db_risk_state_manager
        # The daily values the report records: the account guard's highest since the last resume.
        self.db_balance_manager = db_balance_manager
        self.allowed_indices_rule_config = self.get_rule_config("allowed_indices")
        self.market_closed_dates_list = self.get_rule_config("market_closed_dates")["market_closed_dates"]
        day_trading_config = self.get_rule_config("day_trading")
        self.dont_enter_trade_if_day_profit_is_more_than = day_trading_config["dont_enter_trade_if_day_profit_is_more_than"]
        self.max_day_loss_percent = day_trading_config["max_day_loss_percent"]
        self.signal_validation_config = self.get_rule_config("signal_validation")
        self.market_hours_config = self.get_rule_config("market_hours")
        self.timezone = self.config_manager.get_config_value("trade.config.general.timezone", "Europe/Paris")
        # The hours of market_hours and close_position_time are of this timezone: the trading one unless it says
        # another (America/New_York: the US session's hours whichever side changed its clocks).
        self.market_timezone = self.market_hours_config.get("timezone") or self.timezone
        # The US market's holidays and early closes (src/trade/market_calendar.py); unreadable, none are kept.
        try:
            self.market_calendar = read_market_calendar()
        except (OSError, ValueError, KeyError) as e:
            self.market_calendar = None
            logging.error(f"The calendar of US market days cannot be read, no holiday nor early close is kept: {e}")
        # Risk management config (optional rule)
        self.risk_management_config = self.get_rule_config_safe("risk_management")
        self.cooldown_after_loss_minutes = self.risk_management_config.get("cooldown_after_loss_minutes", 0) if self.risk_management_config else 0
        self.max_trades_per_day = self.risk_management_config.get("max_trades_per_day", 0) if self.risk_management_config else 0
        # The account guard and the day's breakers (src/trade/account_guard.py, day_breakers.py): off unless set.
        self.account_guard = AccountGuard.from_config(self.risk_management_config)
        self.day_breakers = DayBreakers.from_config(self.risk_management_config)
        # The highest account value read since the start, or since entries were last resumed after it.
        self._seen_peak: float | None = None
        self._seen_since = datetime.now(pytz.utc)
        # Confidence scaling config (optional)
        self.confidence_config = self.config_manager.get_config_value("trade.config.position_sizing.confidence_scaling", {})
        self.min_confidence_threshold = self.confidence_config.get("min_confidence_threshold", 0.0)
        # Track last loss timestamp for cooldown
        self._last_loss_timestamp = None
        # No entry around the US releases (optional rule); the calendar only changes with a deploy.
        news_config = self.get_rule_config_safe("macro_news")
        self.news_rule = NewsRule.from_config(news_config) if news_config is not None else None
        self.news_calendar = None
        if self.news_rule is not None:
            try:
                self.news_calendar = read_calendar()
            except (OSError, ValueError, KeyError) as e:
                # Never a reason not to trade: the window is a filter, not a safeguard.
                logging.error(f"The calendar of US releases cannot be read, no window is kept around them: {e}")
        logging.info(f"Trading rules using timezone: {self.timezone}")
        if self.cooldown_after_loss_minutes > 0:
            logging.info(f"Cooldown after loss: {self.cooldown_after_loss_minutes} minutes")
        if self.max_trades_per_day > 0:
            logging.info(f"Max trades per day: {self.max_trades_per_day}")
        if self.account_guard.on:
            logging.info(f"Account guard: pause below {self.account_guard.below_eur:g} € or "
                         f"{self.account_guard.drawdown_percent:g} % under the highest (0: off)")
        if self.day_breakers.on:
            logging.info(f"Day breakers: {self.day_breakers.max_losses} losses in a row, "
                         f"{self.day_breakers.max_drawdown:g} points under the day's best (0: off)")

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
            # Check if the difference is more than max_signal_age_seconds
            max_age_seconds = self.signal_validation_config["max_signal_age_seconds"]
            if time_difference > timedelta(seconds=max_age_seconds):
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

    def risky_start(self) -> tuple[int, int]:
        return self.market_hours_config["risky_trading_start_hour"], self.market_hours_config["risky_trading_start_minute"]

    def check_market_hours(self, signal_timestamp):
        # Parse the signal_timestamp string into a datetime object
        signal_time = datetime.strptime(signal_timestamp, "%Y-%m-%dT%H:%M:%SZ")
        signal_time = signal_time.replace(tzinfo=pytz.UTC)  # Ensure it's in UTC

        # Get the current time in the timezone of the market hours
        current_time = datetime.now(pytz.timezone(self.market_timezone))

        # Format the current date to match the format of the list
        today_date_string = current_time.strftime("%d/%m/%Y")

        if today_date_string in self.market_closed_dates_list:
            message = f"Breaking trading rule : Today, {current_time}, is a market closed date."
            logging.error(message)
            raise TradingRuleViolation(message)

        # A US holiday of the market calendar
        closed = self.market_calendar.closed_on(current_time.date()) if self.market_calendar else None
        if closed is not None:
            message = closed_text(closed)
            logging.info(message)
            raise TradingRuleViolation(message)

        # Check if the current time is within the allowed range
        trading_start_hour = self.market_hours_config["trading_start_hour"]
        trading_end_hour = self.market_hours_config["trading_end_hour"]
        if not (trading_start_hour <= current_time.hour < trading_end_hour):
            logging.error(
                f"Breaking trading rule : The signal is outside of market hours. Current time: {current_time}, Signal time: {signal_time}")
            raise TradingRuleViolation("Signal is outside of market hours.")

        # Check if the current time is within the refused range: from its hour and minute to the end of the day
        if (current_time.hour, current_time.minute) >= self.risky_start():
            logging.error(
                f"Breaking trading rule: The signal is refused due to risky market hours. Current time: {current_time}, Signal time: {signal_time}")
            raise TradingRuleViolation("Signal is refused due to risky market hours.")

        # A day the US market closes early: no entry as long before its close as the risky minutes before a normal end
        early = self.market_calendar.early_close_on(current_time.date()) if self.market_calendar else None
        if early is not None:
            entries_end = entries_end_moment(early, trading_end_hour, self.risky_start())
            if current_time.timestamp() >= entries_end:
                message = early_close_text(early, entries_end, self.timezone)
                logging.info(message)
                raise TradingRuleViolation(message)

    def sale_moment(self, moment: float | None = None) -> float | None:
        """
        When what is held is sold on the day of ``moment`` (the scheduler's close):
        ``close_position_time``, earlier on a day the US market closes early.
        None without a ``close_position_time``.
        """
        close_time = self.get_rule_config_safe("day_trading") or {}
        close_time = close_time.get("close_position_time")
        if not close_time:
            return None
        day = datetime.fromtimestamp(moment or datetime.now(pytz.utc).timestamp(), pytz.timezone(self.market_timezone)).date()
        return sale_moment(day, close_time, self.market_timezone, self.market_calendar,
                           self.market_hours_config["trading_end_hour"])

    def in_trading_hours(self, now: datetime | None = None) -> bool:
        """
        Whether ``now`` is in the trading hours of a weekday that is not a closed
        date, as ``check_market_hours`` reads them, without its log line: what
        the watches of the trader and the position monitor ask every few seconds.
        """
        now = (now or datetime.now(pytz.utc)).astimezone(pytz.timezone(self.market_timezone))
        return (
            now.weekday() < 5
            and now.strftime("%d/%m/%Y") not in self.market_closed_dates_list
            and (self.market_calendar is None or self.market_calendar.closed_on(now.date()) is None)
            and self.market_hours_config["trading_start_hour"] <= now.hour < self.market_hours_config["trading_end_hour"]
        )

    def check_macro_news(self, now: datetime | None = None):
        """
        Refuses an entry in the window around a US release the macro_news rule
        names (src/trade/macro_calendar.py). Past the calendar's last day no
        release is known, and entries go on.
        """
        if self.news_rule is None or self.news_calendar is None:
            return
        moment = (now or datetime.now(pytz.utc)).timestamp()
        window = window_at(self.news_calendar, self.news_rule, moment)
        if window is not None:
            message = refusal_text(window, self.timezone)
            logging.info(message)
            raise TradingRuleViolation(message)

    async def check_profit_per_day(self):
        try:
            today_percent = await self.db_position_manager.get_percent_of_the_day()
        except TradingRuleViolation:
            raise
        except Exception as e:
            logging.error(f"Error checking daily profit: {e}")
            return

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

    async def check_cooldown_after_loss(self):
        """
        Checks if we are still in a cooldown period after the last losing trade.
        Prevents entering trades too quickly after a loss. Backed by a shared DB
        table when available, since the loss may have been recorded by the
        position_monitor process rather than this one.
        """
        if self.cooldown_after_loss_minutes <= 0:
            return  # Cooldown disabled

        if self.db_risk_state_manager is not None:
            last_loss_timestamp = await self.db_risk_state_manager.get_last_loss_timestamp()
        else:
            last_loss_timestamp = self._last_loss_timestamp

        if last_loss_timestamp is None:
            return  # No loss recorded yet

        current_time = datetime.now(pytz.utc)
        cooldown_end = last_loss_timestamp + timedelta(minutes=self.cooldown_after_loss_minutes)

        if current_time < cooldown_end:
            remaining = (cooldown_end - current_time).total_seconds()
            message = (f"Breaking trading rule: Cooldown active after last loss. "
                       f"Remaining: {remaining:.0f}s (cooldown: {self.cooldown_after_loss_minutes}min)")
            logging.info(message)
            raise TradingRuleViolation(message)

    async def check_entries_paused(self):
        """
        Refuses the signal while the operator has paused new entries (from the
        admin interface). Closing positions is not affected.
        """
        if self.db_risk_state_manager is None:
            return

        paused_since = await self.db_risk_state_manager.get_entries_paused_since()
        if paused_since is not None:
            by_guard = await self.db_risk_state_manager.get_entries_paused_by_guard()
            who = "the account guard" if by_guard is not None else "the operator"
            message = f"Breaking trading rule: New entries are paused by {who} since {paused_since}."
            logging.info(message)
            raise TradingRuleViolation(message)

    async def _account_peak(self, value: float) -> float:
        """The account's highest value since entries were last resumed: recorded each day, and read since the start."""
        resumed_at = await self.db_risk_state_manager.get_entries_resumed_at()
        if resumed_at is not None and resumed_at > self._seen_since:
            # Resumed since the values read were counted: they count no more.
            self._seen_peak, self._seen_since = None, resumed_at
        self._seen_peak = value if self._seen_peak is None else max(self._seen_peak, value)
        recorded = await self.db_balance_manager.highest_since(resumed_at) if self.db_balance_manager is not None else None
        return self._seen_peak if recorded is None else max(self._seen_peak, recorded)

    async def guard_account(self, value: float | None, notify=None) -> str | None:
        """
        The account guard with the account worth ``value`` (Saxo's TotalValue; None when unknown, which
        decides nothing): when it trips, new entries are paused, as the operator pauses them but said to be
        the guard's, and it is said once. Why, when it paused them now; None otherwise.
        """
        if not self.account_guard.on or value is None or self.db_risk_state_manager is None:
            return None
        if await self.db_risk_state_manager.get_entries_paused_since() is not None:
            return None
        reason = self.account_guard.trip(value, await self._account_peak(value))
        if reason is None:
            return None
        await self.db_risk_state_manager.pause_entries_by_guard(datetime.now(pytz.utc))
        logging.warning(f"New entries paused by the account guard: {reason}.")
        if notify is not None:
            try:
                await notify(
                    f"⏸️ WATA: new entries paused by the account guard. {reason}. They stay paused until you resume "
                    f"them from the admin's dashboard."
                )
            except Exception as e:
                logging.error(f"The account guard's pause could not be said on Telegram: {e}")
        return reason

    async def check_account_guard(self, value: float | None, notify=None):
        """Refuses the entry when the account guard pauses entries at ``value`` (and pauses them)."""
        reason = await self.guard_account(value, notify)
        if reason is not None:
            message = f"Breaking trading rule: {reason}: new entries are paused until the operator resumes them."
            logging.info(message)
            raise TradingRuleViolation(message)

    async def check_day_breakers(self):
        """No more entry today after that many losses in a row, or that far under the day's best (day_breakers.py)."""
        if not self.day_breakers.on or self.db_position_manager is None:
            return
        refused = self.day_breakers.refusal(await self.db_position_manager.get_today_closed_results())
        if refused is not None:
            message = refused[1]
            logging.info(message)
            raise TradingRuleViolation(message)

    async def record_loss(self):
        """Records the timestamp of a losing trade for cooldown tracking."""
        now = datetime.now(pytz.utc)
        if self.db_risk_state_manager is not None:
            await self.db_risk_state_manager.record_loss_timestamp(now)
        else:
            self._last_loss_timestamp = now
        logging.info(f"Loss recorded at {now}. Cooldown of {self.cooldown_after_loss_minutes} minutes activated.")

    async def check_max_trades_per_day(self):
        """
        Checks if the maximum number of trades per day has been reached.
        """
        if self.max_trades_per_day <= 0:
            return  # Limit disabled

        if self.db_position_manager is None:
            return  # No DB manager available

        today_trades = await self.db_position_manager.get_today_trade_count()
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
