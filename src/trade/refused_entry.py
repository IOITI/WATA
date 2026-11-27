"""
An entry the trader refuses while a position is held the other way
(``position_management.close_on_refused_entry``, on unless false). Shared by
the trader and the backtests' simulator. Pure.

An entry taken sells the other side before it buys, and the TA engine sends
only the entry of a bar that also raised the strategy's exit: that exit is
then lost with an entry the trader refuses. Of the 19 exits the live rule
raised from 22 to 27 November 2026, 10 came on a bar that also raised an entry,
and 8 of those entries were refused or failed. Until v0.105.0 a refused entry
closed nothing, and the position stayed open against the strategy.

So when a refusal is about WATA (its hours, a US release, the pause, a guard,
Saxo's login, the day's limits, the cooldown, the day's trades, the breakers),
the positions of the other direction are sold, as a close signal sells them.
When the signal itself is turned down (too old, under the confidence wanted,
an index WATA does not trade), nothing is sold: such a signal is not believed,
whatever it says. A position of the signal's own direction is never sold for it.
"""

LONG, SHORT = "long", "short"
# The direction an entry turns against: the positions it would have sold before buying.
OTHER_SIDE = {LONG: SHORT, SHORT: LONG}


def close_on_refused_entry(position_management: dict | None) -> bool:
    """Whether a refused entry still sells the other side: yes unless the configuration says false."""
    value = (position_management or {}).get("close_on_refused_entry", True)
    return value if isinstance(value, bool) else True


def said(reason: str, side: str, closed: int, unsold: str | None = None) -> str:
    """
    What the journal says of a refused entry that sold the other ``side``: the
    refusal (without its full stop), then the sales. ``unsold`` is what could
    not be sold, in words.
    """
    sold = f"{reason.rstrip('.')}; closed {closed} {side} position(s) the signal turned against"
    return sold if unsold is None else f"{sold}. {unsold}"


def not_sold(reason: str, side: str, error: str) -> str:
    """What the journal says when the other ``side`` could not even be tried."""
    return f"{reason.rstrip('.')}; the {side} position the signal turned against could not be sold: {error}"
