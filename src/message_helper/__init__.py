import logging
import textwrap
from datetime import datetime
import pytz
import json  # Import json for formatting details

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
        self._add_signal_section()  # Always add the signal section first

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

                cost_buy = selected_instrument.get("commissions", {}).get("CostBuy", "N/A")
                cost_sell = selected_instrument.get("commissions", {}).get("CostSell", "N/A")

                message_body = f"""
                Found: {description}
                Symbol: {symbol}
                Price (Ask): {ask_price} {currency}
                Price Timestamp: {ask_time}
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

    def add_text_section(self, title: str, text: str):
        """Adds a custom text section."""
        section_title = f"--- {title.upper()} ---"  # Standardize title format
        message_body = textwrap.dedent(text)
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