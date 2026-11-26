"""
Whether the balance the trader last read may size an order without being read again.

An entry reads Saxo's balance just before it sizes its order: 44 to 49 ms into
the trade on the server (24 and 25 November 2026), 29 to 35 ms of it after the
turbo was known. The trader also reads it every thirty seconds between signals
(its keep-warm). That reading may stand in for the entry's own only when nothing
can have moved cash since it was made, and this says when:

- it was read while WATA held nothing: no open position in WATA's records
  before the read and none after it. The position monitor sells only what is
  open, and a position stays open in the records until its sale is recorded, so
  a sale of its own cannot have moved cash around such a reading;
- no order of the trader's has gone since (``moved``, called around everything
  of the trader's that buys or sells, and whenever it forgets the balance);
- and nothing is open now.

What it cannot know of is cash moved from outside WATA within those thirty
seconds (a deposit, a withdrawal, an order placed by hand): the order is then a
little small, or refused for funds and sent again smaller as any order is
(src/trade/funds_retry.py). The reading is used only where the operator set
``trade.config.buying_power.balance_cache_max_age_seconds`` above zero: the
orchestrator's own condition (src/trade/async_services.py), unchanged.

Pure: no clock, no I/O. One instance per trader, used from its one event loop.
"""


class FlatBalance:
    def __init__(self) -> None:
        # How many times the trader's own orders may have moved cash.
        self._moves = 0
        # The count at the reading that was proven flat; None while none stands.
        self._proven_at: int | None = None

    def moved(self) -> None:
        """An order of the trader's is about to go, or went, or the balance was forgotten: no reading stands."""
        self._moves += 1
        self._proven_at = None

    def reading(self) -> int:
        """Before a balance is read: what ``read`` is to be given back."""
        return self._moves

    def read(self, token: int, flat_before: bool, flat_after: bool) -> bool:
        """
        A balance was read, ``token`` taken before it. It stands when nothing
        was open before and after, and no order of the trader's went meanwhile.
        True when it stands.
        """
        self._proven_at = self._moves if flat_before and flat_after and token == self._moves else None
        return self._proven_at is not None

    def usable(self, nothing_open: bool) -> bool:
        """Whether the balance last read may size an order now, nothing being open (``nothing_open``) as far as known."""
        return nothing_open and self._proven_at is not None and self._proven_at == self._moves
