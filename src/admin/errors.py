class DatabaseUnavailable(Exception):
    """PostgreSQL could not be reached or refused the query."""


class ServiceUnavailable(Exception):
    """Another WATA service could not be reached or refused the request."""
