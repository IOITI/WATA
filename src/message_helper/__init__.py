import logging
import math
import textwrap
from collections import defaultdict
from datetime import date, datetime, timedelta
import json  # Import json for formatting details

import pytz

from src.trade.exceptions import (
    TradingRuleViolation,
    NoMarketAvailableException,
    NoTurbosAvailableException,
    InsufficientFundsException,
    PositionNotFoundException,
    ApiRequestException,
    TokenAuthenticationException,
    DatabaseOperationException,
    PositionCloseException,
    WebSocketConnectionException,
    SaxoApiError,
    OrderPlacementError,
    ConfigurationError
)


class TelegramMessageComposer:
    """
    Builds contextual Telegram messages section by section, handling errors.
    """

    def __init__(self, signal_data: dict):
        """
        Initialize the composer with the initial signal data.

        Args:
            signal_data: The dictionary received from the message queue (data_from_mq).
        """
        self.signal_data = signal_data
        self.sections = []
        # Store timestamps for later calculations
        self.signal_timestamp_raw = self.signal_data.get("alert_timestamp") or self.signal_data.get("signal_timestamp")
        self.signal_timestamp_dt = self._parse_timestamp(self.signal_timestamp_raw)
        self.ask_time_dt = None
        self.exec_time_dt = None
        self._add_signal_section()  # Always add the signal section first

    def _parse_timestamp(self, timestamp_str: str | None) -> datetime | None:
        """Parse a timestamp string to datetime object, handling None."""
        if not timestamp_str:
            return None
        try:
            # Attempt to parse common ISO formats
            return datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            logging.warning(f"Could not parse timestamp: {timestamp_str}")
            return None

    def _format_timestamp(self, timestamp_str: str | None) -> str:
        """Helper to format timestamps consistently, handling None."""
        if not timestamp_str:
            return "N/A"
        try:
            # Attempt to parse common ISO formats
            dt_obj = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            # TODO: Get timezone from config
            tz = pytz.timezone('Europe/Paris')
            dt_local = dt_obj.astimezone(tz)
            return dt_local.strftime('%Y-%m-%d %H:%M:%S %Z')
        except (ValueError, TypeError):
            logging.warning(f"Could not parse timestamp: {timestamp_str}")
            return timestamp_str  # Return original if parsing fails

    def _calculate_time_diff(self, start_dt: datetime, end_dt: datetime) -> str:
        """Calculate and format the time difference between two datetimes."""
        if not start_dt or not end_dt:
            return "N/A"
        
        diff_seconds = (end_dt - start_dt).total_seconds()
        if diff_seconds < 0:
            return f"Invalid (-{abs(diff_seconds):.2f}s)"
        
        if diff_seconds < 60:
            return f"{diff_seconds:.2f}s"
        else:
            minutes = int(diff_seconds // 60)
            seconds = diff_seconds % 60
            return f"{minutes}m {seconds:.2f}s"

    def _add_signal_section(self):
        """Adds the initial signal information section."""
        signal = self.signal_data.get("action", "N/A")
        signal_id = self.signal_data.get("signal_id", "N/A")
        # Use alert_timestamp if available, otherwise signal_timestamp
        signal_timestamp = self._format_timestamp(self.signal_timestamp_raw)

        message = f"""\
        --- SIGNAL ---
        Signal kind: "{signal}"
        Signal ID: "{signal_id}"
        Signal timestamp: "{signal_timestamp}"
        """
        self.sections.append(textwrap.dedent(message))

    def add_turbo_search_result(self, founded_turbo: dict | None = None, error: Exception | None = None,
                                search_context: dict | None = None):
        """
        Adds the turbo search result section (success or error).
        Handles the structure returned by InstrumentService.find_turbos.

        Args:
            founded_turbo: The dictionary result from successful find_turbos (contains 'selected_instrument').
            error: An exception object if the search failed.
            search_context: Optional dictionary with details about the search attempt (e.g., keywords, price range).
        """
        section_title = "--- TURBO SEARCH ---"  # Changed title for clarity
        message_body = ""

        # Check if 'founded_turbo' has the expected structure
        selected_instrument = None
        if founded_turbo and isinstance(founded_turbo, dict) and 'selected_instrument' in founded_turbo:
            selected_instrument = founded_turbo['selected_instrument']

        if selected_instrument and not error:
            try:
                # Access data within 'selected_instrument'
                description = selected_instrument.get("description", "N/A")
                symbol = selected_instrument.get("symbol", "N/A")
                ask_price = selected_instrument.get("latest_ask",
                                                    selected_instrument.get("quote", {}).get("Ask", "N/A"))
                currency = selected_instrument.get("currency", "")
                # Find timestamp - logic might need adjustment based on final snapshot structure
                # Let's prioritize quote timestamp if available
                ask_time_raw = selected_instrument.get("quote", {}).get("AskTime")
                if not ask_time_raw:  # Fallback to a different timestamp if needed
                    # Example: ask_time_raw = selected_instrument.get("timestamps", {}).get("AskTime")
                    pass  # Adjust fallback as necessary based on actual structure
                ask_time = self._format_timestamp(ask_time_raw)
                
                # Store ask_time for time difference calculations
                self.ask_time_dt = self._parse_timestamp(ask_time_raw)
                
                # Calculate time difference between signal and ask
                signal_to_ask_diff = "N/A"
                if self.signal_timestamp_dt and self.ask_time_dt:
                    signal_to_ask_diff = self._calculate_time_diff(self.signal_timestamp_dt, self.ask_time_dt)

                cost_buy = selected_instrument.get("commissions", {}).get("CostBuy", "N/A")
                cost_sell = selected_instrument.get("commissions", {}).get("CostSell", "N/A")

                message_body = f"""
                Found: {description}
                Symbol: {symbol}
                Price (Ask): {ask_price} {currency}
                Price Timestamp: {ask_time}
                Signal to Ask Time: {signal_to_ask_diff}
                Est. Cost BUY/SELL: {cost_buy}/{cost_sell}
                """
            except Exception as e:
                logging.error(f"Error formatting successful turbo search result: {e}", exc_info=True)
                message_body = f"Error formatting successful search result: {e}\nRaw data: {json.dumps(founded_turbo, indent=2)}"

        elif error:
            error_type = type(error).__name__
            # Use the specific exception types directly
            if isinstance(error, NoTurbosAvailableException):
                message_body = f"Error: {error}"  # Exception should format itself
            elif isinstance(error, NoMarketAvailableException):
                message_body = f"Error: {error}"  # Exception should format itself
            else:
                # Generic error formatting
                message_body = f"Error during turbo search: {error_type}: {error}"

            # Add search context if provided (same as before)
            if search_context:
                keywords = search_context.get('Keywords', 'N/A')
                min_price = search_context.get('min_price', 'N/A')
                max_price = search_context.get('max_price', 'N/A')
                context_info = f"\nSearch Context: Type={keywords}, Price Range={min_price}-{max_price}"
                message_body += context_info

        else:
            message_body = "Turbo search status unknown (no result or error provided)."

        full_section = f"{section_title}\n{textwrap.dedent(message_body)}"
        self.sections.append(full_section)

    def add_position_result(self, buy_details: dict | None = None, error: Exception | None = None,
                            order_id: str | int | None = None,  # Keep order_id for context in some errors
                            # Add specific fields for InsufficientFunds error context:
                            available_funds: float | None = None,
                            required_price: float | None = None
                            ):
        """
        Adds the position result section (success or error).
        Handles the structure returned by TradingOrchestrator.execute_trade_signal.

        Args:
            buy_details: The dictionary result from successful execute_trade_signal.
            error: An exception object if the buy/position check failed.
            order_id: The order ID (often available within the error object now).
            available_funds: Specific context for InsufficientFundsException.
            required_price: Specific context for InsufficientFundsException.
        """
        section_title = "--- POSITION ---"
        message_body = ""

        # Check if 'buy_details' has the expected structure from orchestrator
        position_data = None
        order_data = None
        if buy_details and isinstance(buy_details, dict):
            position_data = buy_details.get("position_details")
            order_data = buy_details.get("order_details")

        if position_data and order_data and not error:
            try:
                # Extract from position_details and order_details
                instrument_name = position_data.get("instrument_name", "N/A")
                open_price = position_data.get("position_open_price", "N/A")
                currency = position_data.get("instrument_currency", "")
                amount = position_data.get("position_amount", "N/A")
                total_price = position_data.get("position_total_open_price", "N/A")
                exec_time_raw = position_data.get("execution_time_open")
                exec_time = self._format_timestamp(exec_time_raw)
                
                # Store exec_time for time difference calculations
                self.exec_time_dt = self._parse_timestamp(exec_time_raw)
                
                # Calculate time differences
                signal_to_exec_diff = "N/A"
                ask_to_exec_diff = "N/A"
                signal_to_ask_diff = "N/A"
                
                if self.signal_timestamp_dt and self.exec_time_dt:
                    signal_to_exec_diff = self._calculate_time_diff(self.signal_timestamp_dt, self.exec_time_dt)
                
                if self.ask_time_dt and self.exec_time_dt:
                    ask_to_exec_diff = self._calculate_time_diff(self.ask_time_dt, self.exec_time_dt)
                
                if self.signal_timestamp_dt and self.ask_time_dt:
                    signal_to_ask_diff = self._calculate_time_diff(self.signal_timestamp_dt, self.ask_time_dt)
                
                position_id = position_data.get("position_id", "N/A")
                actual_order_id = order_data.get("order_id", "N/A")  # Use ID from order_details
                order_cost = order_data.get("order_cost", "N/A")
                # Re-fetch signal_id from original data for consistency
                signal_id = self.signal_data.get("signal_id", "N/A")

                message_body = f"""
                ✅ Position Opened Successfully
                Instrument: {instrument_name}
                Open Price: {open_price} {currency}
                Amount: {amount}
                Total price: {total_price}
                Order Cost: {order_cost} {currency}
                Time: {exec_time}
                Signal to Ask Time: {signal_to_ask_diff}
                Ask to Exec Time: {ask_to_exec_diff}
                Total Signal to Exec Time: {signal_to_exec_diff}
                Position ID: {position_id}
                Order ID: {actual_order_id}
                Signal ID: {signal_id}
                """
            except Exception as e:
                logging.error(f"Error formatting successful position result: {e}", exc_info=True)
                message_body = f"Error formatting successful position result: {e}\nRaw data: {json.dumps(buy_details, indent=2)}"

        elif error:
            error_type = type(error).__name__

            # Use specific exception types and extract info from them
            if isinstance(error, InsufficientFundsException):
                # Use passed context or get from exception
                avail = available_funds if available_funds is not None else getattr(error, 'available_funds', 'N/A')
                req = required_price if required_price is not None else getattr(error, 'required_price', 'N/A')
                details = ""
                if avail != 'N/A' and req != 'N/A':
                    details = f" (Available: {avail:.2f}, Price/unit: {req})"
                message_body = f"❌ Error: Insufficient Funds.{details}\nDetails: {error}"

            elif isinstance(error, PositionNotFoundException):
                order_id_from_exception = getattr(error, 'order_id', 'Unknown')
                cancel_attempted = getattr(error, 'cancellation_attempted', False)
                cancel_succeeded = getattr(error, 'cancellation_succeeded', False)
                cancel_info = ""
                if cancel_attempted:
                    cancel_info = f"\nOrder Cancellation Attempted: {'Success' if cancel_succeeded else 'Failed'}"
                message_body = f"❌ CRITICAL Error: Position not found for Order ID {order_id_from_exception}.{cancel_info}\nDetails: {error}"

            elif isinstance(error, OrderPlacementError):
                # Let add_generic_error handle the detailed formatting
                # We just provide the context here
                message_body = f"❌ Error: Order placement failed.\nDetails below."
                # Call add_generic_error AFTER this section to add details
                # Or duplicate formatting here if preferred
                status = getattr(error, 'status_code', 'N/A')
                message_body += f"\nStatus Code: {status}\nReason: {error}"
                # Note: add_generic_error will add more details later

            elif isinstance(error, PositionCloseException):
                # Let add_generic_error handle formatting
                message_body = f"❌ Error: Failed to close position.\nDetails below."
                # Note: add_generic_error will add more details later

            # Keep the generic ValueError check if needed for other value errors
            elif isinstance(error, ValueError) and "CRITICAL" not in str(error).upper():
                message_body = f"❌ Error during position processing: {error_type}: {error}"

            # Use add_generic_error for most other specific exceptions (DB, API, Token, Config etc.)
            # Fallback for truly unexpected errors
            else:
                context_order_id = getattr(error, 'order_id', order_id)  # Try get order_id from error or arg
                context_info = f" related to order {context_order_id}" if context_order_id else ""
                message_body = f"❌ Error during position processing{context_info}: {error_type}: {error}"
                # Generic errors will be formatted better by add_generic_error later

        else:
            message_body = "Position status unknown (no details or error provided)."

        full_section = f"{section_title}\n{textwrap.dedent(message_body)}"
        self.sections.append(full_section)

        # If there was an error, call add_generic_error now to add detailed formatting
        if error:
            # Avoid adding duplicate info for InsufficientFunds/PositionNotFound if formatted above
            if not isinstance(error, (InsufficientFundsException, PositionNotFoundException)):
                self.add_generic_error(f"Position Processing ({error_type})", error)

    def add_generic_error(self, context: str, error: Exception, is_critical: bool = False):
        """Adds a generic error section with specific formatting for known exception types."""
        # Determine title based on criticality
        title_prefix = "CRITICAL ERROR" if is_critical else "ERROR"
        section_title = f"--- {title_prefix} ({context}) ---"
        error_type = type(error).__name__
        message_body = f"{error_type}: {str(error)}"  # Start with basic info

        # Format specific exception types with more details using getattr safely
        try:  # Wrap detail extraction in try/except to avoid breaking message generation
            if isinstance(error, ApiRequestException):
                endpoint = getattr(error, 'endpoint', 'N/A')
                status_code = getattr(error, 'status_code', 'N/A')  # May not always be present
                params = getattr(error, 'params', None)
                message_body += f"\nEndpoint: {endpoint}"
                if status_code != 'N/A': message_body += f"\nStatus: {status_code}"
                if params: message_body += f"\nParams: {json.dumps(params)}"

            elif isinstance(error, TokenAuthenticationException):
                refresh_attempt = getattr(error, 'refresh_attempt', False)
                attempt_info = "during token refresh" if refresh_attempt else "during initial authentication"
                message_body += f"\nOccurred: {attempt_info}"

            elif isinstance(error, DatabaseOperationException):
                operation = getattr(error, 'operation', 'N/A')
                entity_id = getattr(error, 'entity_id', 'N/A')
                message_body += f"\nOperation: {operation}\nEntity ID: {entity_id}"

            elif isinstance(error, PositionCloseException):
                position_id = getattr(error, 'position_id', 'N/A')
                reason = getattr(error, 'reason', 'N/A')
                message_body += f"\nPosition ID: {position_id}\nReason: {reason}"

            elif isinstance(error, WebSocketConnectionException):
                context_id = getattr(error, 'context_id', 'N/A')
                reference_id = getattr(error, 'reference_id', 'N/A')
                message_body += f"\nContext ID: {context_id}\nReference ID: {reference_id}"

            elif isinstance(error, SaxoApiError):  # Includes OrderPlacementError
                status_code = getattr(error, 'status_code', 'N/A')
                saxo_error_details = getattr(error, 'saxo_error_details', None)
                request_details = getattr(error, 'request_details', None)  # Added
                order_details = getattr(error, 'order_details', None)  # Specific to OrderPlacementError

                message_body += f"\nStatus Code: {status_code}"
                if saxo_error_details:
                    if isinstance(saxo_error_details, dict):
                        error_code = saxo_error_details.get('ErrorCode', 'N/A')
                        error_msg = saxo_error_details.get('Message', str(saxo_error_details))  # Fallback
                        message_body += f"\nSaxo Code: {error_code}\nSaxo Msg: {error_msg}"
                    else:
                        message_body += f"\nSaxo Details: {saxo_error_details}"
                if order_details:  # Specific for OrderPlacementError
                    message_body += f"\nOrder Payload: {json.dumps(order_details, indent=2)}"
                elif request_details:  # Generic request details
                    message_body += f"\nRequest Details: {json.dumps(request_details, indent=2)}"


            elif isinstance(error, ConfigurationError):
                config_path = getattr(error, 'config_path', None)
                missing_key = getattr(error, 'missing_key', None)
                if missing_key: message_body += f"\nMissing Key: {missing_key}"
                if config_path: message_body += f"\nConfig Path: {config_path}"

            # Add other custom exceptions here if needed
            # elif isinstance(error, MyOtherCustomException):
            #     detail = getattr(error, 'custom_detail', 'N/A')
            #     message_body += f"\nCustom Detail: {detail}"

        except Exception as fmt_err:
            logging.error(f"Error formatting details for exception {error_type}: {fmt_err}")
            message_body += "\n(Error retrieving additional details)"

        full_section = f"{section_title}\n{textwrap.dedent(message_body)}"
        self.sections.append(full_section)

    def add_rule_violation(self, error: TradingRuleViolation):
        """Adds a specific section for TradingRuleViolation."""
        section_title = "--- RULE VIOLATION ---"
        # Ensure the exception's __str__ provides good output
        message_body = f"{error}"
        full_section = f"{section_title}\n{textwrap.dedent(message_body)}"
        self.sections.append(full_section)

    def add_text_section(self, title: str, text):
        """Adds a custom section, coercing structured values safely."""
        if isinstance(text, dict):
            self.add_dict_section(title, text)
            return

        section_title = f"--- {title.upper()} ---"  # Standardize title format
        message_body = "" if text is None else textwrap.dedent(str(text))
        full_section = f"{section_title}\n{message_body}"
        self.sections.append(full_section)

    def add_dict_section(self, title: str, data: dict):
        """Adds a section formatting a dictionary."""
        section_title = f"--- {title.upper()} ---"
        try:
            message_body = json.dumps(data, indent=2, sort_keys=True)
        except Exception:
            message_body = str(data)  # Fallback
        full_section = f"{section_title}\n```json\n{message_body}\n```"  # Use markdown code block
        self.sections.append(full_section)

    def get_message(self) -> str:
        """Composes the final message string."""
        # Join sections, ensuring proper spacing
        return "\n\n".join(self.sections).strip()


def _coerce_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None
        try:
            return datetime.fromisoformat(candidate.replace("Z", "+00:00")).date()
        except ValueError:
            pass
        for fmt in ("%Y/%m/%d", "%Y-%m-%d"):
            try:
                return datetime.strptime(candidate, fmt).date()
            except ValueError:
                continue
    return None


def _coerce_datetime(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None
        try:
            return datetime.fromisoformat(candidate.replace("Z", "+00:00"))
        except ValueError:
            pass
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
            try:
                return datetime.strptime(candidate, fmt)
            except ValueError:
                continue
    return None


def _as_float(value, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _get_trade_value(trade: dict, *keys: str, default=None):
    for key in keys:
        if key in trade and trade[key] is not None:
            return trade[key]
    return default


def _get_trade_close_dt(trade: dict) -> datetime | None:
    return _coerce_datetime(_get_trade_value(trade, "execution_time_close"))


def _get_trade_close_date(trade: dict) -> date | None:
    close_dt = _get_trade_close_dt(trade)
    if close_dt is not None:
        return close_dt.date()
    return _coerce_date(_get_trade_value(trade, "day_date"))


def _get_trade_percent(trade: dict) -> float:
    return _as_float(
        _get_trade_value(trade, "performance_percent", "position_total_performance_percent"),
        0.0,
    )


def _get_trade_max_percent(trade: dict) -> float:
    return _as_float(
        _get_trade_value(trade, "max_performance_percent", "position_max_performance_percent"),
        0.0,
    )


def _get_trade_profit(trade: dict) -> float:
    return _as_float(_get_trade_value(trade, "profit_loss", "position_profit_loss"), 0.0)


def _sort_trades_chronologically(trades: list[dict]) -> list[dict]:
    return sorted(
        trades,
        key=lambda trade: (
            _get_trade_close_dt(trade) or datetime.min,
            str(_get_trade_value(trade, "position_id", default="")),
        ),
    )


def format_signed_percent(value: float | None) -> str:
    if value is None:
        return "N/A"
    numeric = _as_float(value)
    sign = "+" if numeric > 0 else ""
    return f"{sign}{numeric:.2f}%"


def format_percent(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{_as_float(value):.2f}%"


def format_signed_currency(value: float | None) -> str:
    if value is None:
        return "N/A"
    numeric = _as_float(value)
    sign = "+" if numeric > 0 else ""
    return f"{sign}{numeric:.2f}"


def format_profit_factor(value: float | None) -> str:
    if value is None:
        return "N/A"
    if value == float("inf"):
        return "∞"
    return f"{_as_float(value):.2f}"


def classify_trade_outcome(trade: dict) -> str:
    performance_percent = _get_trade_percent(trade)
    if performance_percent > 0:
        return "win"
    if performance_percent < 0:
        return "loss"
    return "break_even"


def calculate_win_loss_break_even_counts(trades: list[dict]) -> dict:
    counts = {
        "wins": 0,
        "losses": 0,
        "break_even": 0,
        "total": len(trades),
    }
    for trade in trades:
        outcome = classify_trade_outcome(trade)
        if outcome == "win":
            counts["wins"] += 1
        elif outcome == "loss":
            counts["losses"] += 1
        else:
            counts["break_even"] += 1

    counts["win_rate"] = (counts["wins"] / counts["total"] * 100.0) if counts["total"] else 0.0
    return counts


def calculate_profit_factor(trades: list[dict]) -> float:
    gross_profit = sum(max(_get_trade_profit(trade), 0.0) for trade in trades)
    gross_loss = abs(sum(min(_get_trade_profit(trade), 0.0) for trade in trades))
    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def calculate_average_win_vs_loss(trades: list[dict]) -> dict:
    performance_values = [_get_trade_percent(trade) for trade in trades]
    win_values = [value for value in performance_values if value > 0]
    loss_values = [value for value in performance_values if value < 0]

    return {
        "avg_trade": sum(performance_values) / len(performance_values) if performance_values else 0.0,
        "avg_win": sum(win_values) / len(win_values) if win_values else None,
        "avg_loss": sum(loss_values) / len(loss_values) if loss_values else None,
        "best_trade": max(performance_values) if performance_values else 0.0,
        "worst_trade": min(performance_values) if performance_values else 0.0,
    }


def calculate_compounded_return(trades: list[dict], *, use_max_performance: bool = False) -> float:
    equity = 1.0
    ordered_trades = _sort_trades_chronologically(trades)
    for trade in ordered_trades:
        performance_percent = _get_trade_max_percent(trade) if use_max_performance else _get_trade_percent(trade)
        multiplier = 1 + performance_percent / 100.0
        if multiplier <= 0:
            return -100.0
        equity *= multiplier
    return round((equity - 1.0) * 100.0, 2)


def calculate_best_case_return(trades: list[dict]) -> float:
    ordered_trades = _sort_trades_chronologically(trades)
    equity = 1.0
    peak_return = 0.0
    for trade in ordered_trades:
        multiplier = 1 + _get_trade_percent(trade) / 100.0
        if multiplier <= 0:
            return -100.0
        equity *= multiplier
        peak_return = max(peak_return, (equity - 1.0) * 100.0)
    return round(peak_return, 2)


def calculate_daily_max_drawdown(trades: list[dict]) -> float:
    equity = 1.0
    equity_peak = 1.0
    max_drawdown = 0.0

    for trade in _sort_trades_chronologically(trades):
        multiplier = 1 + _get_trade_percent(trade) / 100.0
        if multiplier <= 0:
            return -100.0
        equity *= multiplier
        equity_peak = max(equity_peak, equity)
        drawdown = ((equity / equity_peak) - 1.0) * 100.0 if equity_peak else 0.0
        max_drawdown = min(max_drawdown, drawdown)

    return round(max_drawdown, 2)


def calculate_winning_streak(daily_profit_history: list[dict]) -> dict:
    normalized_rows = []
    for row in daily_profit_history:
        day_date = _coerce_date(_get_trade_value(row, "day_date"))
        if day_date is None:
            continue
        profit_value = _as_float(_get_trade_value(row, "sum_profit", "net_profit"), 0.0)
        normalized_rows.append({"day_date": day_date, "sum_profit": profit_value})

    normalized_rows.sort(key=lambda row: row["day_date"])
    best_streak = 0
    running_streak = 0
    last_win_date = None

    for row in normalized_rows:
        if row["sum_profit"] > 0:
            running_streak += 1
            best_streak = max(best_streak, running_streak)
            last_win_date = row["day_date"]
        else:
            running_streak = 0

    current_streak = 0
    for row in reversed(normalized_rows):
        if row["sum_profit"] > 0:
            current_streak += 1
        else:
            break

    return {
        "current_streak": current_streak,
        "best_streak": best_streak,
        "last_win_date": last_win_date,
    }


def calculate_trade_summary(trades: list[dict]) -> dict:
    counts = calculate_win_loss_break_even_counts(trades)
    averages = calculate_average_win_vs_loss(trades)
    net_profit = round(sum(_get_trade_profit(trade) for trade in trades), 2)

    return {
        **counts,
        **averages,
        "trade_count": len(trades),
        "net_profit": net_profit,
        "profit_factor": calculate_profit_factor(trades),
        "max_drawdown": calculate_daily_max_drawdown(trades),
        "compounded_percent": calculate_compounded_return(trades),
    }


def calculate_timeframe_aggregation(trades: list[dict], report_date: date, days: int) -> dict:
    start_date = report_date - timedelta(days=days - 1)
    period_trades = [
        trade for trade in trades
        if (trade_date := _get_trade_close_date(trade)) is not None and start_date <= trade_date <= report_date
    ]
    summary = calculate_trade_summary(period_trades)
    return {
        **summary,
        "days": days,
        "start_date": start_date,
        "end_date": report_date,
    }


def calculate_weekly_aggregations(trades: list[dict], report_date: date, weeks: int = 10) -> list[dict]:
    trades_by_week: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for trade in trades:
        trade_date = _get_trade_close_date(trade)
        if trade_date is None:
            continue
        iso_year, iso_week, _ = trade_date.isocalendar()
        trades_by_week[(iso_year, iso_week)].append(trade)

    current_week_start = report_date - timedelta(days=report_date.weekday())
    weekly_rows = []
    for offset in range(weeks):
        week_start = current_week_start - timedelta(weeks=offset)
        iso_year, iso_week, _ = week_start.isocalendar()
        week_trades = trades_by_week.get((iso_year, iso_week), [])
        summary = calculate_trade_summary(week_trades)
        weekly_rows.append(
            {
                **summary,
                "week_start": week_start,
                "iso_year": iso_year,
                "iso_week": iso_week,
                "week_label": f"W{iso_week:02d}",
            }
        )
    return weekly_rows


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _shift_month(value: date, months: int) -> date:
    year = value.year + ((value.month - 1 + months) // 12)
    month = ((value.month - 1 + months) % 12) + 1
    return date(year, month, 1)


def calculate_monthly_aggregations(trades: list[dict], report_date: date, months: int = 12) -> list[dict]:
    trades_by_month: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for trade in trades:
        trade_date = _get_trade_close_date(trade)
        if trade_date is None:
            continue
        trades_by_month[(trade_date.year, trade_date.month)].append(trade)

    current_month_start = _month_start(report_date)
    monthly_rows = []
    for offset in range(months):
        month_start = _shift_month(current_month_start, -offset)
        month_trades = trades_by_month.get((month_start.year, month_start.month), [])
        if not month_trades:
            continue
        summary = calculate_trade_summary(month_trades)
        monthly_rows.append(
            {
                **summary,
                "month_start": month_start,
                "year": month_start.year,
                "month": month_start.month,
                "month_label": month_start.strftime("%B"),
            }
        )
    return monthly_rows


def calculate_yearly_aggregations(trades: list[dict], report_date: date, years: int = 5) -> list[dict]:
    trades_by_year: dict[int, list[dict]] = defaultdict(list)
    for trade in trades:
        trade_date = _get_trade_close_date(trade)
        if trade_date is None:
            continue
        trades_by_year[trade_date.year].append(trade)

    yearly_rows = []
    for year in range(report_date.year, report_date.year - years, -1):
        year_trades = trades_by_year.get(year, [])
        if not year_trades:
            continue
        summary = calculate_trade_summary(year_trades)
        yearly_rows.append(
            {
                **summary,
                "year": year,
            }
        )
    return yearly_rows


# ── Averages & Milestones ──

DEFAULT_MILESTONES_EUR = (100_000, 500_000, 1_000_000, 5_000_000, 10_000_000)
MILESTONE_WEEKLY_HORIZON_PERIODS = 260  # ~5 years of weeks
MILESTONE_MONTHLY_HORIZON_PERIODS = 60  # 5 years of months


def _week_start(value: date) -> date:
    return value - timedelta(days=value.weekday())


def calculate_average_period_return(trades: list[dict], report_date: date, period: str) -> float:
    # Average compounded return percent across every period (week/month/year)
    # from the first trade's close date through report_date, inclusive.
    # Periods without any closed trades contribute 0% to the average.
    if not trades:
        return 0.0

    ordered_trades = _sort_trades_chronologically(trades)
    first_date = _get_trade_close_date(ordered_trades[0])
    if first_date is None or first_date > report_date:
        return 0.0

    period_returns: list[float] = []

    if period == "week":
        bucketed: dict[date, list[dict]] = defaultdict(list)
        for trade in trades:
            trade_date = _get_trade_close_date(trade)
            if trade_date is not None:
                bucketed[_week_start(trade_date)].append(trade)

        cursor = _week_start(first_date)
        end = _week_start(report_date)
        while cursor <= end:
            period_returns.append(calculate_compounded_return(bucketed.get(cursor, [])))
            cursor += timedelta(weeks=1)

    elif period == "month":
        bucketed_months: dict[tuple[int, int], list[dict]] = defaultdict(list)
        for trade in trades:
            trade_date = _get_trade_close_date(trade)
            if trade_date is not None:
                bucketed_months[(trade_date.year, trade_date.month)].append(trade)

        cursor_month = _month_start(first_date)
        end_month = _month_start(report_date)
        while cursor_month <= end_month:
            period_returns.append(calculate_compounded_return(bucketed_months.get((cursor_month.year, cursor_month.month), [])))
            cursor_month = _shift_month(cursor_month, 1)

    elif period == "year":
        bucketed_years: dict[int, list[dict]] = defaultdict(list)
        for trade in trades:
            trade_date = _get_trade_close_date(trade)
            if trade_date is not None:
                bucketed_years[trade_date.year].append(trade)

        for year in range(first_date.year, report_date.year + 1):
            period_returns.append(calculate_compounded_return(bucketed_years.get(year, [])))

    else:
        raise ValueError(f"Unknown period: {period}")

    if not period_returns:
        return 0.0
    return round(sum(period_returns) / len(period_returns), 2)


def calculate_milestone_projections(
    current_balance: float | None,
    avg_period_percent: float,
    milestones: list[float],
    period_kind: str,
    report_date: date,
    horizon_periods: int,
) -> list[dict]:
    # Project the date each not-yet-reached milestone would be hit by compounding
    # current_balance at avg_period_percent per week/month. Milestones already
    # surpassed by current_balance are omitted entirely from the result.
    if current_balance is None or current_balance <= 0:
        return []

    growth_rate = avg_period_percent / 100.0
    if growth_rate <= 0:
        return []

    growth_factor = 1 + growth_rate
    projections = []
    for milestone in milestones:
        if milestone <= current_balance:
            continue

        periods_needed = max(1, math.ceil(math.log(milestone / current_balance) / math.log(growth_factor)))

        if period_kind == "week":
            projected_date = report_date + timedelta(weeks=periods_needed)
        elif period_kind == "month":
            projected_date = _shift_month(_month_start(report_date), periods_needed)
        else:
            raise ValueError(f"Unknown period_kind: {period_kind}")

        projections.append(
            {
                "milestone": milestone,
                "date": projected_date,
                "periods_needed": periods_needed,
                "within_horizon": periods_needed <= horizon_periods,
            }
        )
    return projections


def merge_daily_performance_series(
    daily_real: dict,
    daily_best: dict,
    daily_max: dict,
    *,
    report_date: date,
    days: int = 7,
) -> list[dict]:
    rows = []
    for offset in range(days):
        day = report_date - timedelta(days=offset)
        day_key = day.strftime("%Y/%m/%d")
        rows.append(
            {
                "day_date": day,
                "day_key": day_key,
                "real": _as_float(daily_real.get(day_key), 0.0),
                "best": _as_float(daily_best.get(day_key), 0.0),
                "max": _as_float(daily_max.get(day_key), 0.0),
            }
        )
    return rows


def build_daily_trading_report_payload(
    *,
    report_date: date | datetime | str | None,
    closed_trades: list[dict],
    daily_profit_history: list[dict],
    daily_real: dict,
    daily_best: dict,
    daily_max: dict,
    current_balance: float | None = None,
    milestones_eur: list[float] | None = None,
) -> dict:
    normalized_report_date = _coerce_date(report_date) or datetime.utcnow().date()
    ordered_closed_trades = _sort_trades_chronologically(closed_trades)
    daily_trades = [
        trade for trade in ordered_closed_trades
        if _get_trade_close_date(trade) == normalized_report_date
    ]
    milestones = list(milestones_eur) if milestones_eur else list(DEFAULT_MILESTONES_EUR)

    weekly_avg_percent = calculate_average_period_return(ordered_closed_trades, normalized_report_date, "week")
    monthly_avg_percent = calculate_average_period_return(ordered_closed_trades, normalized_report_date, "month")
    yearly_avg_percent = calculate_average_period_return(ordered_closed_trades, normalized_report_date, "year")

    return {
        "report_date": normalized_report_date,
        "streak": calculate_winning_streak(daily_profit_history),
        "daily_summary": calculate_trade_summary(daily_trades),
        "current_balance": current_balance,
        "by_direction": {
            "long": calculate_trade_summary([trade for trade in daily_trades if str(trade.get("action", "")).lower() == "long"]),
            "short": calculate_trade_summary([trade for trade in daily_trades if str(trade.get("action", "")).lower() == "short"]),
        },
        "cumulative": {
            days: calculate_timeframe_aggregation(ordered_closed_trades, normalized_report_date, days)
            for days in (7, 30, 60)
        },
        "last_7_days": merge_daily_performance_series(
            daily_real,
            daily_best,
            daily_max,
            report_date=normalized_report_date,
            days=7,
        ),
        "last_10_weeks": calculate_weekly_aggregations(ordered_closed_trades, normalized_report_date, weeks=10),
        "last_12_months": calculate_monthly_aggregations(ordered_closed_trades, normalized_report_date, months=12),
        "last_5_years": calculate_yearly_aggregations(ordered_closed_trades, normalized_report_date, years=5),
        "has_trade_history": bool(ordered_closed_trades),
        "averages": {
            "weekly_percent": weekly_avg_percent,
            "monthly_percent": monthly_avg_percent,
            "yearly_percent": yearly_avg_percent,
        },
        "milestones": {
            "weekly": calculate_milestone_projections(
                current_balance, weekly_avg_percent, milestones, "week",
                normalized_report_date, MILESTONE_WEEKLY_HORIZON_PERIODS,
            ),
            "monthly": calculate_milestone_projections(
                current_balance, monthly_avg_percent, milestones, "month",
                normalized_report_date, MILESTONE_MONTHLY_HORIZON_PERIODS,
            ),
        },
    }


def _format_direction_title(direction: str) -> str:
    if direction == "long":
        return "🟢 LONGS"
    return "🔴 SHORTS"


def _format_direction_counts(summary: dict) -> str:
    trade_count = summary["trade_count"]
    trade_label = "trade" if trade_count == 1 else "trades"
    base = f"{trade_count} {trade_label} | {summary['wins']}W - {summary['losses']}L"
    if summary["break_even"]:
        base += f" - {summary['break_even']}BE"
    return base


def _format_date_label(value: date | None) -> str:
    if value is None:
        return "N/A"
    return value.strftime("%Y/%m/%d")


def format_currency_amount(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{_as_float(value):,.2f} €"


def _align_label_value_rows(rows: list[tuple[str, str]]) -> list[str]:
    """Right-pad each label so values line up, matching the widest 'label value' row."""
    if not rows:
        return []
    base_lengths = [len(label) + 1 + len(value) for label, value in rows]
    max_width = max(base_lengths)
    lines = []
    for (label, value), base_len in zip(rows, base_lengths):
        spacer = " " * (1 + (max_width - base_len))
        lines.append(f"{label}{spacer}{value}")
    return lines


def _format_average_return_lines(averages: dict) -> list[str]:
    rows = [
        ("📈 Avg P/L per Week:", format_signed_percent(averages["weekly_percent"])),
        ("📈 Avg P/L per Month:", format_signed_percent(averages["monthly_percent"])),
        ("📈 Avg P/L per Year:", format_signed_percent(averages["yearly_percent"])),
    ]
    return _align_label_value_rows(rows)


def _format_milestone_rows(projections: list[dict]) -> list[str]:
    if not projections:
        return []

    entries = []
    for row in projections:
        icon = "🏆" if row["within_horizon"] else "⏳"
        label = "Reached" if row["within_horizon"] else "Proj."
        amount_text = f"{row['milestone']:,.0f}"
        date_text = row["date"].strftime("%Y-%m-%d")
        entries.append((icon, label, amount_text, date_text))

    max_width = max(len(label) + 1 + len(amount_text) for _, label, amount_text, _ in entries)
    lines = []
    for icon, label, amount_text, date_text in entries:
        base_len = len(label) + 1 + len(amount_text)
        spacer = " " * (1 + (max_width - base_len))
        lines.append(f"{icon} {label}{spacer}{amount_text} €:  {date_text}")
    return lines


def format_daily_trading_report(payload: dict) -> str:
    report_date = payload["report_date"]
    streak = payload["streak"]
    daily_summary = payload["daily_summary"]
    long_summary = payload["by_direction"]["long"]
    short_summary = payload["by_direction"]["short"]

    cumulative_lines = []
    for days in (7, 30, 60):
        summary = payload["cumulative"][days]
        period_label = f"{days:>2} Days"
        percent_text = format_signed_percent(summary["compounded_percent"]).rjust(8)
        money_text = f"{format_signed_currency(summary['net_profit'])} €".rjust(10)
        win_rate_text = format_percent(summary["win_rate"]).rjust(7)
        cumulative_lines.append(
            f"⏱ {period_label}: {percent_text} | {money_text} (🎯 {win_rate_text} WR)"
        )

    last_seven_day_lines = []
    for row in payload["last_7_days"]:
        day_label = row["day_date"].strftime("%m/%d")
        real_text = format_signed_percent(row["real"]).rjust(8)
        best_text = format_signed_percent(row["best"]).rjust(8)
        max_text = format_signed_percent(row["max"]).rjust(8)
        last_seven_day_lines.append(f"{day_label}: {real_text} | {best_text} | {max_text}")

    weekly_lines = []
    for row in payload["last_10_weeks"]:
        performance_text = format_signed_percent(row["compounded_percent"]).rjust(8)
        money_text = f"{format_signed_currency(row['net_profit'])} €".rjust(10)
        win_rate_text = format_percent(row["win_rate"]).rjust(7)
        weekly_lines.append(
            f"{row['week_label']}: {performance_text} ({money_text}) | 🎯 {win_rate_text}"
        )

    monthly_lines = []
    for row in payload["last_12_months"]:
        month_label = f"{row['month_label']}:".ljust(11)
        performance_text = format_signed_percent(row["compounded_percent"]).rjust(8)
        money_text = f"{format_signed_currency(row['net_profit'])} €".rjust(10)
        win_rate_text = format_percent(row["win_rate"]).rjust(7)
        monthly_lines.append(
            f"{month_label} {performance_text} ({money_text}) | 🎯 {win_rate_text}"
        )

    yearly_lines = []
    for row in payload["last_5_years"]:
        year_label = f"{row['year']}:"
        performance_text = format_signed_percent(row["compounded_percent"]).rjust(8)
        money_text = f"{format_signed_currency(row['net_profit'])} €".rjust(10)
        win_rate_text = format_percent(row["win_rate"]).rjust(7)
        yearly_lines.append(
            f"{year_label} {performance_text} ({money_text}) | 🎯 {win_rate_text}"
        )

    lines = [
        f"📊 Trading Report | {report_date.strftime('%Y/%m/%d')}",
        f"🔥 Winning Streak: {streak['current_streak']} Days (Last win: {_format_date_label(streak['last_win_date'])})",
        "",
        "--- 📝 DAILY SUMMARY ---",
        f"💰 Net Profit: {format_signed_currency(daily_summary['net_profit'])} €",
        (
            f"🎯 Win Rate: {format_percent(daily_summary['win_rate'])} "
            f"({daily_summary['wins']}W | {daily_summary['losses']}L | {daily_summary['break_even']}BE)"
        ),
        f"⚖️ Profit Factor: {format_profit_factor(daily_summary['profit_factor'])}",
        f"📉 Daily Max Drawdown: {format_signed_percent(daily_summary['max_drawdown'])}",
    ]

    if payload.get("current_balance") is not None:
        lines.append(f"💵 Current Account Balance: {format_currency_amount(payload['current_balance'])}")

    lines += [
        "",
        f"📊 Avg Trade: {format_signed_percent(daily_summary['avg_trade'])}",
        (
            f"🟩 Avg Win: {format_signed_percent(daily_summary['avg_win'])} | "
            f"🟥 Avg Loss: {format_signed_percent(daily_summary['avg_loss'])}"
        ),
        (
            f"📈 Best: {format_signed_percent(daily_summary['best_trade'])} | "
            f"📉 Worst: {format_signed_percent(daily_summary['worst_trade'])}"
        ),
        "",
        "--- 🔍 BY DIRECTION ---",
        f"{_format_direction_title('long')} ({_format_direction_counts(long_summary)})",
        (
            f"Avg: {format_signed_percent(long_summary['avg_trade'])} | "
            f"Max: {format_signed_percent(long_summary['best_trade'])} | "
            f"Min: {format_signed_percent(long_summary['worst_trade'])}"
        ),
        "",
        f"{_format_direction_title('short')} ({_format_direction_counts(short_summary)})",
        (
            f"Avg: {format_signed_percent(short_summary['avg_trade'])} | "
            f"Max: {format_signed_percent(short_summary['best_trade'])} | "
            f"Min: {format_signed_percent(short_summary['worst_trade'])}"
        ),
        "",
        "--- 🗓 CUMULATIVE P/L ---",
        *cumulative_lines,
        "",
        "--- 📊 LAST 7 DAYS (Real | Best | Max) ---",
        *last_seven_day_lines,
        "",
        "--- 📅 LAST 10 WEEKS ---",
        *weekly_lines,
    ]

    if monthly_lines:
        lines.extend([
            "",
            "--- 📅 LAST 12 MONTHS ---",
            *monthly_lines,
        ])

    if yearly_lines:
        lines.extend([
            "",
            "--- 📅 LAST 5 YEARS ---",
            *yearly_lines,
        ])

    averages_and_milestones_lines: list[str] = []
    if payload.get("has_trade_history"):
        averages_and_milestones_lines.extend(_format_average_return_lines(payload["averages"]))

        weekly_milestone_lines = _format_milestone_rows(payload["milestones"]["weekly"])
        if weekly_milestone_lines:
            averages_and_milestones_lines.append("")
            averages_and_milestones_lines.append("Milestones (simu per week) :")
            averages_and_milestones_lines.extend(weekly_milestone_lines)

        monthly_milestone_lines = _format_milestone_rows(payload["milestones"]["monthly"])
        if monthly_milestone_lines:
            averages_and_milestones_lines.append("")
            averages_and_milestones_lines.append("Milestones (simu per month) :")
            averages_and_milestones_lines.extend(monthly_milestone_lines)

    if averages_and_milestones_lines:
        lines.extend([
            "",
            "--- 🚀 AVERAGES & MILESTONES ---",
            *averages_and_milestones_lines,
        ])

    return "\n".join(lines).strip()


def build_daily_trading_report_message(
    *,
    report_date: date | datetime | str | None,
    closed_trades: list[dict],
    daily_profit_history: list[dict],
    daily_real: dict,
    daily_best: dict,
    daily_max: dict,
    current_balance: float | None = None,
    milestones_eur: list[float] | None = None,
) -> str:
    payload = build_daily_trading_report_payload(
        report_date=report_date,
        closed_trades=closed_trades,
        daily_profit_history=daily_profit_history,
        daily_real=daily_real,
        daily_best=daily_best,
        daily_max=daily_max,
        current_balance=current_balance,
        milestones_eur=milestones_eur,
    )
    return format_daily_trading_report(payload)


# --- Standalone Helper Functions (No changes needed for these) ---

def append_performance_message(p_message, title, percentages):
    """Helper function to append performance data to the message."""
    p_message += f"\n--- {title} ---\n"
    if not percentages:
        p_message += "No data available.\n"
        return p_message
    for day, percentage in percentages.items():
        # Handle cases where percentage is None
        percentage_value = f"{percentage:.2f}" if isinstance(percentage, (int, float)) else "N/A"
        p_message += f"{day}: {percentage_value}%\n"
    return p_message


def format_general_stats(general_stats):
    """Formats the general stats section of the message."""
    if not general_stats:
        return "No general stats available for today.\n"

    general_message = ""
    for general in general_stats:
        avg_pct = general.get("avg_percent")
        max_pct = general.get("max_percent")
        min_pct = general.get("min_percent")
        sum_prof = general.get("sum_profit")

        general_message += f"""
--- Stats of the day {general.get("day_date", "N/A")} ---
Position count : {general.get("position_count", "N/A")}
Average %: {f'{avg_pct:.2f}' if avg_pct is not None else 'N/A'}
Max %: {f'{max_pct:.2f}' if max_pct is not None else 'N/A'}
Min %: {f'{min_pct:.2f}' if min_pct is not None else 'N/A'}
Sum profit : {f'{sum_prof:.2f}' if sum_prof is not None else 'N/A'} €
"""
    return general_message.strip()


def format_detail_stats(detail_stats):
    """Formats the detailed stats section of the message."""
    if not detail_stats:
        return ""

    details_message = "\n--- Detail stats ---\n"
    for detail in detail_stats:
        avg_pct = detail.get("avg_percent")
        max_pct = detail.get("max_percent")
        min_pct = detail.get("min_percent")

        details_message += f"""
Type : {detail.get("action", "N/A")}
Position count : {detail.get("position_count", "N/A")}
Average %: {f'{avg_pct:.2f}' if avg_pct is not None else 'N/A'}
Max %: {f'{max_pct:.2f}' if max_pct is not None else 'N/A'}
Min %: {f'{min_pct:.2f}' if min_pct is not None else 'N/A'}
-------
"""
    return details_message.strip()


def generate_daily_stats_message(stats_of_the_day):
    """Generates the daily stats part of the message."""
    general_stats = stats_of_the_day.get("general", [])
    detail_stats = stats_of_the_day.get("detail_stats", [])

    general_stats_message = format_general_stats(general_stats)
    detail_stats_message = format_detail_stats(detail_stats)

    # Ensure space between sections if both exist
    separator = "\n" if general_stats_message and detail_stats_message else ""

    return general_stats_message + separator + detail_stats_message


def generate_performance_stats_message(message, days, last_days_percentages, last_best_days_percentages,
                                       last_days_percentages_on_max, last_best_days_percentages_on_max):
    """Appends performance statistics for the last 'days' to the message."""
    message = append_performance_message(message, f"Last {days} Days Performance (Real)", last_days_percentages)
    message = append_performance_message(message, f"Last {days} Days Performance (Best Case)",
                                         last_best_days_percentages)
    message = append_performance_message(message, f"Last {days} Days Performance (Theoretical Max)",
                                         last_days_percentages_on_max)
    message = append_performance_message(message, f"Last {days} Days Performance (Best Theoretical Max)",
                                         last_best_days_percentages_on_max)

    return message