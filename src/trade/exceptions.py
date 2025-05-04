# src/trade/exceptions.py

class NoTurbosAvailableException(Exception):
    """Raised when no turbos are available matching the search criteria."""
    # Changed 'context' to 'search_context' for clarity and consistency with usage
    def __init__(self, message, search_context=None):
        super().__init__(message)
        self.search_context = search_context # Store the search parameters/context

    def __str__(self):
        base = super().__str__()
        context_info = f" (Search Context: {self.search_context})" if self.search_context else ""
        return f"{base}{context_info}"

class NoMarketAvailableException(Exception):
    """Raised when no market is available after cleaning NoMarket and Closed Market items."""
    pass

class TradingRuleViolation(Exception):
    """Exception raised for violations of trading rules."""
    def __init__(self, message="Trading rule violation"):
        self.message = message
        super().__init__(self.message)

class PositionNotFoundException(Exception):
     """Raised when a position cannot be found after an order, potentially after retries."""
     # Added cancellation_attempted and cancellation_succeeded
     def __init__(self, message, order_id=None, cancellation_attempted=None, cancellation_succeeded=None):
        super().__init__(message)
        self.order_id = order_id
        self.cancellation_attempted = cancellation_attempted # Boolean flag
        self.cancellation_succeeded = cancellation_succeeded # Boolean flag or None if not attempted

     def __str__(self):
        base = super().__str__()
        order_info = f" (Order ID: {self.order_id})" if self.order_id else ""
        cancel_info = ""
        if self.cancellation_attempted is not None:
            status = "succeeded" if self.cancellation_succeeded else "failed"
            cancel_info = f" (Order cancellation attempt: {status})"
        return f"{base}{order_info}{cancel_info}"


class InsufficientFundsException(Exception):
    """Raised when available funds are insufficient for the intended trade."""
    # Added saxo_error_details
    def __init__(self, message, available_funds=None, required_price=None, calculated_amount=None, saxo_error_details=None):
        super().__init__(message)
        self.available_funds = available_funds
        self.required_price = required_price
        self.calculated_amount = calculated_amount # The amount calculated (<= 0)
        self.saxo_error_details = saxo_error_details # Store Saxo API error details if available

    def __str__(self):
        details = ""
        if self.available_funds is not None and self.required_price is not None:
             details = f" (Available: {self.available_funds:.2f}, Required per unit: {self.required_price})"
        saxo_info = ""
        if self.saxo_error_details:
            # Try to extract core message if details are dict, otherwise show raw
            if isinstance(self.saxo_error_details, dict):
                 saxo_msg = self.saxo_error_details.get('Message', str(self.saxo_error_details))
                 saxo_code = self.saxo_error_details.get('ErrorCode')
                 saxo_info = f" (Saxo: {saxo_msg}" + (f" [{saxo_code}])" if saxo_code else ")")
            else:
                 saxo_info = f" (Saxo Details: {self.saxo_error_details})"

        return f"{super().__str__()}{details}{saxo_info}"

class ApiRequestException(Exception):
    """Raised when a request to the Saxo API fails (e.g., connection, timeout)."""
    def __init__(self, message, endpoint=None, params=None, status_code=None):
        super().__init__(message)
        self.endpoint = endpoint
        self.params = params
        self.status_code = status_code # Usually None for connection errors, may be set if parsed

    def __str__(self):
        base_message = super().__str__()
        endpoint_info = f", Endpoint: {self.endpoint}" if self.endpoint else ""
        status_info = f", Status: {self.status_code}" if self.status_code else ""
        return f"{base_message}{endpoint_info}{status_info}"

class TokenAuthenticationException(Exception):
    """Raised when there's an issue with token authentication or authorization."""
    # Added saxo_error_details
    def __init__(self, message, refresh_attempt=False, saxo_error_details=None):
        super().__init__(message)
        self.refresh_attempt = refresh_attempt
        self.saxo_error_details = saxo_error_details # Store Saxo API error details if available (e.g., from 401 response)

    def __str__(self):
        base = super().__str__()
        refresh_info = " (during token refresh)" if self.refresh_attempt else ""
        saxo_info = ""
        if self.saxo_error_details:
            saxo_info = f" (Saxo Details: {self.saxo_error_details})"
        return f"{base}{refresh_info}{saxo_info}"

class DatabaseOperationException(Exception):
    """Raised when a database operation fails."""
    def __init__(self, message, operation=None, entity_id=None):
        super().__init__(message)
        self.operation = operation
        self.entity_id = entity_id

    def __str__(self):
        op_info = f", Operation: {self.operation}" if self.operation else ""
        entity_info = f", Entity ID: {self.entity_id}" if self.entity_id else ""
        return f"{super().__str__()}{op_info}{entity_info}"

class PositionCloseException(Exception):
    """Raised when a position cannot be closed via API call."""
    def __init__(self, message, position_id=None, reason=None):
        super().__init__(message)
        self.position_id = position_id
        self.reason = reason

    def __str__(self):
        position_info = f", Position ID: {self.position_id}" if self.position_id else ""
        reason_info = f", Reason: {self.reason}" if self.reason else ""
        return f"{super().__str__()}{position_info}{reason_info}"

class WebSocketConnectionException(Exception):
    """Raised when there's an issue with WebSocket connections."""
    def __init__(self, message, context_id=None, reference_id=None):
        super().__init__(message)
        self.context_id = context_id
        self.reference_id = reference_id

    def __str__(self):
        context_info = f", Context ID: {self.context_id}" if self.context_id else ""
        reference_info = f", Reference ID: {self.reference_id}" if self.reference_id else ""
        return f"{super().__str__()}{context_info}{reference_info}"

class SaxoApiError(Exception):
    """Raised for general errors reported by the Saxo API (status >= 400)."""
    def __init__(self, message, status_code=None, saxo_error_details=None, request_details=None):
        super().__init__(message)
        self.status_code = status_code # e.g., 400, 401, 429, 500
        self.saxo_error_details = saxo_error_details # Dict/string from Saxo response body
        self.request_details = request_details # Info about the request made

    def __str__(self):
        base = super().__str__()
        details = f" (Status: {self.status_code})" if self.status_code else ""
        if self.saxo_error_details:
            # Try to extract core message if details are dict, otherwise show raw
            if isinstance(self.saxo_error_details, dict):
                 saxo_msg = self.saxo_error_details.get('Message', str(self.saxo_error_details))
                 saxo_code = self.saxo_error_details.get('ErrorCode')
                 details += f" (Saxo: {saxo_msg}" + (f" [{saxo_code}])" if saxo_code else ")")
            else:
                 details += f" (Saxo Details: {self.saxo_error_details})"
        return f"{base}{details}"

class OrderPlacementError(SaxoApiError):
    """Raised when Saxo explicitly rejects an order placement request (subtype of SaxoApiError)."""
    def __init__(self, message, status_code=None, saxo_error_details=None, order_details=None):
        # Pass relevant args up to SaxoApiError constructor
        super().__init__(message, status_code=status_code, saxo_error_details=saxo_error_details)
        self.order_details = order_details # The order dict that was rejected

    def __str__(self):
        # Leverage SaxoApiError's __str__ and add order specifics
        base = super().__str__()
        order_info = ""
        if self.order_details and isinstance(self.order_details, dict):
            # Include key order details but avoid overly verbose output
            uic = self.order_details.get("Uic", "Unknown")
            amount = self.order_details.get("Amount", "Unknown")
            buy_sell = self.order_details.get("BuySell", self.order_details.get("OrderType", "Unknown")) # Get direction
            order_info = f" (Order: {buy_sell} {amount} units of {uic})"
        elif self.order_details:
             order_info = f" (Order Details: {self.order_details})" # Fallback if not dict
        return f"{base}{order_info}"

class ConfigurationError(Exception):
    """Raised for errors loading or accessing application configuration."""
    def __init__(self, message, config_path=None, missing_key=None):
        super().__init__(message)
        self.config_path = config_path
        self.missing_key = missing_key

    def __str__(self):
        base = super().__str__()
        path_info = f" (Path: {self.config_path})" if self.config_path else ""
        key_info = f" (Missing key: {self.missing_key})" if self.missing_key else ""
        return f"{base}{path_info}{key_info}"