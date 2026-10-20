class DatabaseUnavailable(Exception):
    """PostgreSQL could not be reached or refused the query."""


class ServiceUnavailable(Exception):
    """Another WATA service could not be reached or refused the request."""


class InvalidRequest(ValueError):
    """What was asked makes no sense; the message says why, for whoever asked."""


class UnknownRun(LookupError):
    """No backtest has that id."""


class NotPossible(RuntimeError):
    """Not in the state the request needs."""
