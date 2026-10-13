"""What the strategy commands share as processes."""

import os

LOWEST_PRIORITY = 19


def lower_priority() -> None:
    """Give way to everything else on the machine: neither a backtest nor a download is ever urgent."""
    try:
        os.nice(LOWEST_PRIORITY)
    except (AttributeError, OSError):
        pass
