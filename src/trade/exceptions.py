class NoTurbosAvailableException(Exception):
    """Raised when no turbos are available for the asked price."""
    pass

class NoMarketAvailableException(Exception):
    """Raised when no market is available after cleaning NoMarket and Closed Market items."""
    pass

class TradingRuleViolation(Exception):
    """Exception raised for violations of trading rules."""
    def __init__(self, message="Trading rule violation"):
        self.message = message
        super().__init__(self.message)