class NoTurbosAvailableException(Exception):
    """Raised when no turbos are available for the asked price."""
    def __init__(self, message, context=None):
        super().__init__(message)
        self.context = context

class NoMarketAvailableException(Exception):
    """Raised when no market is available after cleaning NoMarket and Closed Market items."""
    pass

class TradingRuleViolation(Exception):
    """Exception raised for violations of trading rules."""
    def __init__(self, message="Trading rule violation"):
        self.message = message
        super().__init__(self.message)

class PositionNotFoundException(Exception):
     """Raised when a position cannot be found after an order."""
     def __init__(self, message, order_id=None):
        super().__init__(message)
        self.order_id = order_id

class InsufficientFundsException(Exception):
    """Raised when available funds are insufficient for the intended trade."""
    def __init__(self, message, available_funds=None, required_price=None, calculated_amount=None):
        super().__init__(message)
        self.available_funds = available_funds
        self.required_price = required_price
        self.calculated_amount = calculated_amount # The amount calculated (<= 0)

    # Optional: Make it format itself nicely if printed directly
    def __str__(self):
        details = ""
        if self.available_funds is not None and self.required_price is not None:
             details = f" (Available: {self.available_funds:.2f}, Required per unit: {self.required_price})"
        # Use super().__str__() to get the original message passed to __init__
        return f"{super().__str__()}{details}"

class ApiRequestException(Exception):
    """Raised when a request to the Saxo API fails."""
    def __init__(self, message, endpoint=None, params=None, status_code=None):
        super().__init__(message)
        self.endpoint = endpoint
        self.params = params
        self.status_code = status_code
        
    def __str__(self):
        base_message = super().__str__()
        endpoint_info = f", Endpoint: {self.endpoint}" if self.endpoint else ""
        status_info = f", Status: {self.status_code}" if self.status_code else ""
        return f"{base_message}{endpoint_info}{status_info}"

class TokenAuthenticationException(Exception):
    """Raised when there's an issue with token authentication."""
    def __init__(self, message, refresh_attempt=False):
        super().__init__(message)
        self.refresh_attempt = refresh_attempt
        
    def __str__(self):
        refresh_info = " (during token refresh)" if self.refresh_attempt else ""
        return f"{super().__str__()}{refresh_info}"

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
    """Raised when a position cannot be closed."""
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
    """Raised for general errors reported by the Saxo API."""
    def __init__(self, message, status_code=None, saxo_error_details=None, request_details=None):
        super().__init__(message)
        self.status_code = status_code # e.g., 400, 401, 429, 500
        self.saxo_error_details = saxo_error_details # Dict/string from Saxo response body
        self.request_details = request_details # Info about the request made
    
    def __str__(self):
        # Improve default string representation
        base = super().__str__()
        details = f" (Status: {self.status_code})" if self.status_code else ""
        if self.saxo_error_details:
            details += f" (Saxo Details: {self.saxo_error_details})"
        return f"{base}{details}"

class OrderPlacementError(SaxoApiError): 
    """Raised when Saxo explicitly rejects an order placement request."""
    def __init__(self, message, status_code=None, saxo_error_details=None, order_details=None):
        super().__init__(message, status_code, saxo_error_details)
        self.order_details = order_details # The order dict that was rejected
        
    def __str__(self):
        base = super().__str__()
        order_info = ""
        if self.order_details:
            # Include key order details but avoid overly verbose output
            uic = self.order_details.get("Uic", "Unknown")
            amount = self.order_details.get("Amount", "Unknown")
            buy_sell = self.order_details.get("BuySell", "Unknown")
            order_info = f" (Order: {buy_sell} {amount} units of {uic})"
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