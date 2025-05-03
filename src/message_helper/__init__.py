import logging

import textwrap
from datetime import datetime
import pytz

# Add this import if you haven't already
from src.trade import exceptions as trade_exceptions
from src.trade.exceptions import (
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
        self._add_signal_section()  # Always add the signal section first

    def _format_timestamp(self, timestamp_str: str | None) -> str:
        """Helper to format timestamps consistently, handling None."""
        if not timestamp_str:
            return "N/A"
        try:
            # Attempt to parse common ISO formats
            dt_obj = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            # Convert to a user-friendly format (e.g., Europe/Paris if configured)
            # Replace 'Europe/Paris' with your configured timezone if available
            tz = pytz.timezone('Europe/Paris')
            dt_local = dt_obj.astimezone(tz)
            return dt_local.strftime('%Y-%m-%d %H:%M:%S %Z')
        except (ValueError, TypeError):
            return timestamp_str  # Return original if parsing fails

    def _add_signal_section(self):
        """Adds the initial signal information section."""
        signal = self.signal_data.get("action", "N/A")
        signal_id = self.signal_data.get("signal_id", "N/A")
        # Use alert_timestamp if available, otherwise signal_timestamp
        signal_timestamp_raw = self.signal_data.get("alert_timestamp") or self.signal_data.get("signal_timestamp")
        signal_timestamp = self._format_timestamp(signal_timestamp_raw)

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

        Args:
            founded_turbo: The dictionary result from successful SaxoService.find_turbos.
            error: An exception object if the search failed.
            search_context: Optional dictionary with details about the search attempt (e.g., keywords, price range).
        """
        section_title = "--- Turbo search ---"
        message_body = ""

        if founded_turbo and not error:
            try:
                description = founded_turbo.get("price", {}).get("DisplayAndFormat", {}).get("Description", "N/A")
                symbol = founded_turbo.get("price", {}).get("DisplayAndFormat", {}).get("Symbol", "N/A")
                ask_price = founded_turbo.get("price", {}).get("Quote", {}).get("Ask", "N/A")
                currency = founded_turbo.get("price", {}).get("DisplayAndFormat", {}).get("Currency", "")
                ask_time_raw = founded_turbo.get("price", {}).get("Timestamps", {}).get("AskTime")
                ask_time = self._format_timestamp(ask_time_raw)
                cost_buy = founded_turbo.get("price", {}).get("Commissions", {}).get("CostBuy", "N/A")
                cost_sell = founded_turbo.get("price", {}).get("Commissions", {}).get("CostSell", "N/A")

                message_body = f"""
                Found this: {description}
                Symbol: {symbol}
                Price info: {ask_price} {currency}
                Price Ask timestamp: {ask_time}
                Cost BUY/SELL: {cost_buy}/{cost_sell}
                """
            except Exception as e:
                # Catch potential errors accessing nested dicts, though .get should prevent most
                message_body = f"Error formatting successful search result: {e}\nRaw data: {founded_turbo}"

        elif error:
            error_type = type(error).__name__
            if isinstance(error, trade_exceptions.NoTurbosAvailableException):
                # Extract details specifically from NoTurbosAvailableException
                message_body = f"Error: {error}"  # The exception likely formats itself well
            elif isinstance(error, trade_exceptions.NoMarketAvailableException):
                message_body = f"Error: {error}"  # The exception likely formats itself well
            else:
                # Generic error message
                message_body = f"Error during turbo search: {error_type}: {error}"

            # Add search context if provided
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
                            order_id: str | int | None = None):
        """
        Adds the position result section (success or error).

        Args:
            buy_details: The dictionary result from successful SaxoService.buy_turbo_instrument.
            error: An exception object if the buy/position check failed.
            order_id: The order ID, especially relevant if an error occurred after order placement.
        """
        section_title = "--- POSITION ---"
        message_body = ""

        if buy_details and not error:
            try:
                position = buy_details.get("position", {})
                instrument_name = position.get("instrument_name", "N/A")
                open_price = position.get("position_open_price", "N/A")
                currency = position.get("instrument_currency", "")
                amount = position.get("position_amount", "N/A")
                total_price = position.get("position_total_open_price", "N/A")
                exec_time_raw = position.get("execution_time_open")
                exec_time = self._format_timestamp(exec_time_raw)
                position_id = position.get("position_id", "N/A")
                # Re-fetch signal_id from original data for consistency
                signal_id = self.signal_data.get("signal_id", "N/A")

                message_body = f"""
                Instrument: {instrument_name}
                Open Price: {open_price} {currency}
                Amount: {amount}
                Total price: {total_price}
                Time: {exec_time}
                Position ID: {position_id}
                Signal ID: {signal_id}
                """
            except Exception as e:
                message_body = f"Error formatting successful position result: {e}\nRaw data: {buy_details}"

        elif error:
            error_type = type(error).__name__

            if isinstance(error, InsufficientFundsException):
                 # Format a specific message using context from the exception
                 details = ""
                 # Use hasattr for safer access to optional attributes
                 if hasattr(error, 'available_funds') and error.available_funds is not None and \
                    hasattr(error, 'required_price') and error.required_price is not None:
                      details = f" (Available: {error.available_funds:.2f}, Price per unit: {error.required_price})"

                 message_body = f"Error: Insufficient Funds. Cannot calculate valid amount for order.{details}\nDetails: {error}"
            # Check for the specific "Position not found" error message
            # This is brittle; ideally, `find_position_with_validated_order` should raise a custom exception
            elif isinstance(error, PositionNotFoundException):  # Use the specific exception type
                # Use the order_id stored within the exception object
                order_id_from_exception = error.order_id if hasattr(error, 'order_id') else 'Unknown'
                message_body = f"Error: {str(error)}"  # Exception message should be descriptive

            elif isinstance(error, PositionCloseException):
                position_id = getattr(error, 'position_id', 'Unknown')
                reason = getattr(error, 'reason', 'Unknown reason')
                message_body = f"Error: Failed to close position {position_id}.\nReason: {reason}\nDetails: {error}"

            # Keep the generic ValueError check if needed for other value errors
            elif isinstance(error, ValueError):
                # Handle other value errors if necessary, otherwise fall through to generic
                message_body = f"Error during position placement/check: {error_type}: {error}"

            # Generic fallback
            else:
                context_info = f" related to order {order_id}" if order_id else ""
                message_body = f"Error during position placement/check{context_info}: {error_type}: {error}"

        else:
            # This case might occur if buy_turbo_instrument returns None without error (shouldn't happen ideally)
            message_body = "Position status unknown (no details or error provided)."

        full_section = f"{section_title}\n{textwrap.dedent(message_body)}"
        self.sections.append(full_section)

    def add_generic_error(self, context: str, error: Exception):
        """Adds a generic error section with specific formatting for known exception types."""
        section_title = f"--- ERROR ({context}) ---"
        error_type = type(error).__name__
        
        # Format specific exception types with more details
        if isinstance(error, ApiRequestException):
            endpoint = getattr(error, 'endpoint', 'N/A')
            status_code = getattr(error, 'status_code', 'N/A')
            message_body = f"{error_type}: {error}\nEndpoint: {endpoint}\nStatus: {status_code}"
            
        elif isinstance(error, TokenAuthenticationException):
            refresh_attempt = getattr(error, 'refresh_attempt', False)
            attempt_info = "during token refresh" if refresh_attempt else "during initial authentication"
            message_body = f"{error_type}: {error}\nOccurred {attempt_info}"
            
        elif isinstance(error, DatabaseOperationException):
            operation = getattr(error, 'operation', 'N/A')
            entity_id = getattr(error, 'entity_id', 'N/A')
            message_body = f"{error_type}: {error}\nOperation: {operation}\nEntity ID: {entity_id}"
            
        elif isinstance(error, PositionCloseException):
            position_id = getattr(error, 'position_id', 'N/A')
            reason = getattr(error, 'reason', 'N/A')
            message_body = f"{error_type}: {error}\nPosition ID: {position_id}\nReason: {reason}"
            
        elif isinstance(error, WebSocketConnectionException):
            context_id = getattr(error, 'context_id', 'N/A')
            reference_id = getattr(error, 'reference_id', 'N/A')
            message_body = f"{error_type}: {error}\nContext ID: {context_id}\nReference ID: {reference_id}"
            
        elif isinstance(error, SaxoApiError):
            status_code = getattr(error, 'status_code', 'N/A')
            saxo_error_details = getattr(error, 'saxo_error_details', None)
            message_body = f"{error_type}: {error}"
            if saxo_error_details:
                # Format saxo_error_details nicely if it's a dict
                if isinstance(saxo_error_details, dict):
                    error_code = saxo_error_details.get('ErrorCode', 'N/A')
                    error_msg = saxo_error_details.get('Message', 'N/A')
                    message_body += f"\nSaxo Error Code: {error_code}\nSaxo Message: {error_msg}"
                else:
                    message_body += f"\nSaxo Details: {saxo_error_details}"
            
        elif isinstance(error, OrderPlacementError):
            # OrderPlacementError is a subclass of SaxoApiError, so we need additional formatting
            order_details = getattr(error, 'order_details', None)
            if order_details and isinstance(order_details, dict):
                uic = order_details.get('Uic', 'N/A')
                amount = order_details.get('Amount', 'N/A')
                buy_sell = order_details.get('BuySell', 'N/A')
                message_body = f"{error_type}: {error}\nOrder: {buy_sell} {amount} units of instrument {uic}"
            else:
                message_body = f"{error_type}: {error}"
                
        elif isinstance(error, ConfigurationError):
            config_path = getattr(error, 'config_path', 'N/A')
            missing_key = getattr(error, 'missing_key', 'N/A')
            message_body = f"{error_type}: {error}"
            if missing_key != 'N/A':
                message_body += f"\nMissing Key: {missing_key}"
            if config_path != 'N/A':
                message_body += f"\nConfiguration Path: {config_path}"
            
        else:
            # Default formatting for other exceptions
            message_body = f"{error_type}: {error}"

        full_section = f"{section_title}\n{textwrap.dedent(message_body)}"
        self.sections.append(full_section)

    def add_rule_violation(self, error: trade_exceptions.TradingRuleViolation):
        """Adds a specific section for TradingRuleViolation."""
        section_title = "--- RULE VIOLATION ---"
        message_body = f"{error}"  # The exception likely formats itself well
        full_section = f"{section_title}\n{textwrap.dedent(message_body)}"
        self.sections.append(full_section)

    def add_text_section(self, title: str, text: str):
        """
        Adds a custom text section with the given title and content.
        
        Args:
            title: The title of the section.
            text: The text content to display in the section.
        """
        section_title = f"--- {title} ---"
        # Dedent the text to ensure consistent formatting
        message_body = textwrap.dedent(text)
        full_section = f"{section_title}\n{message_body}"
        self.sections.append(full_section)

    def get_message(self) -> str:
        """
        Composes the final message string from all added sections.
        """
        return "\n".join(self.sections)

def append_performance_message(p_message, title, percentages):
    """Helper function to append performance data to the message."""
    p_message += f"\n--- {title} ---\n"
    for day, percentage in percentages.items():
        # Handle cases where percentage is None
        percentage_value = percentage if percentage is not None else "N/A"
        p_message += f"{day}: {percentage_value}%\n"
    return p_message


def format_general_stats(general_stats):
    """Formats the general stats section of the message."""
    if not general_stats:
        return "No daily stats available for today"

    general_message = ""
    for general in general_stats:
        # Handle NoneType values in the stats (default to "N/A" or 0 as needed)
        general_message +=  f"""
--- Stats of the day {general.get("day_date", "N/A")} ---

Position count : {general.get("position_count", "N/A")}
Average %: {general.get("avg_percent", "N/A")}
Max %: {general.get("max_percent", "N/A")}
Min %: {general.get("min_percent", "N/A")}
Sum profit : {general.get("sum_profit", "N/A")} €
"""
    return general_message


def format_detail_stats(detail_stats):
    """Formats the detailed stats section of the message."""
    if not detail_stats:
        return ""

    details_message = "--- Detail stats ---\n"
    for detail in detail_stats:
        # Handle NoneType values in the detail stats
        details_message += f"""
Type : {detail.get("action", "N/A")}
Position count : {detail.get("position_count", "N/A")}
Average %: {detail.get("avg_percent", "N/A")}
Max %: {detail.get("max_percent", "N/A")}
Min %: {detail.get("min_percent", "N/A")}
-------
"""
    return details_message


def generate_daily_stats_message(stats_of_the_day):
    """Generates the daily stats part of the message."""
    # Safely access 'general' and 'detail_stats'
    general_stats = stats_of_the_day.get("general", [])
    detail_stats = stats_of_the_day.get("detail_stats", [])

    general_stats_message = format_general_stats(general_stats)
    detail_stats_message = format_detail_stats(detail_stats)

    return general_stats_message + detail_stats_message


def generate_performance_stats_message(message, days, last_days_percentages, last_best_days_percentages,
                                       last_days_percentages_on_max, last_best_days_percentages_on_max):
    """Appends performance statistics for the last 'days' to the message."""
    message = append_performance_message(message, f"Last {days} Days Performance real", last_days_percentages)
    message = append_performance_message(message, f"Last {days} Days Performance best", last_best_days_percentages)
    message = append_performance_message(message, f"Last {days} Days Performance, on max", last_days_percentages_on_max)
    message = append_performance_message(message, f"Last {days} Days Performance, best on max", last_best_days_percentages_on_max)

    return message