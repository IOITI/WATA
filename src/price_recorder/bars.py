"""
Ten-second bars of the bid and the ask of an instrument, built from the quotes
Saxo streams.

A quote is not a trade: it holds until the next one. So a bar opens on the
quote that was standing when its ten seconds began, and an instrument whose
quote did not move in a bucket still gets a bar (the same value four times,
``updates`` at 0). What must never happen is a bar for a time when nothing was
being received: when the whole feed has been silent for a while, no bar is
carried forward, and the bucket says so.

Pure logic, driven by the times it is given: no clock, no I/O.
"""

from dataclasses import dataclass
from typing import Hashable

BAR_SECONDS = 10
# Without any quote from Saxo for this long, the last known quotes are no
# longer taken for the current prices.
FEED_SILENCE_SECONDS = 30
# After a stall longer than this (the process was suspended), the buckets in
# between are skipped rather than closed one by one as "nothing received".
MAX_CATCH_UP_BUCKETS = 360

_UNQUOTABLE_PRICE_TYPES = frozenset({"NoMarket", "NoAccess"})


def bucket_start(timestamp: float) -> int:
    """Start (epoch seconds) of the ten-second bucket ``timestamp`` falls in: :00, :10, :20..."""
    return int(timestamp // BAR_SECONDS) * BAR_SECONDS


@dataclass(frozen=True)
class Quote:
    bid: float
    ask: float | None
    delayed_minutes: int = 0


def read_quote(row: dict) -> Quote | None:
    """
    The quote of a streamed InfoPrice row, or None when the instrument has no
    price to record right now: market closed, no market (a knocked-out turbo),
    or no access to its data. The same test as the trader's turbo selection,
    plus "no access". An index has no bid and ask of its own: its mid is both.
    """
    quote = row.get("Quote") or {}
    if quote.get("MarketState") == "Closed":
        return None
    if quote.get("PriceTypeBid") in _UNQUOTABLE_PRICE_TYPES or quote.get("PriceTypeAsk") in _UNQUOTABLE_PRICE_TYPES:
        return None
    mid = quote.get("Mid")
    bid = quote.get("Bid", mid)
    ask = quote.get("Ask", mid)
    if not isinstance(bid, (int, float)) or isinstance(bid, bool):
        return None
    if not isinstance(ask, (int, float)) or isinstance(ask, bool):
        ask = None
    delayed = quote.get("DelayedByMinutes")
    return Quote(
        bid=float(bid),
        ask=None if ask is None else float(ask),
        delayed_minutes=int(delayed) if isinstance(delayed, (int, float)) else 0,
    )


@dataclass
class Bar:
    key: Hashable
    start: int
    bid_open: float
    bid_high: float
    bid_low: float
    bid_close: float
    ask_open: float | None
    ask_high: float | None
    ask_low: float | None
    ask_close: float | None
    # Quote changes received in the bucket. 0: the quote standing before it held throughout.
    updates: int
    delayed_minutes: int

    @classmethod
    def opening_on(cls, key: Hashable, start: int, quote: Quote) -> "Bar":
        return cls(
            key=key, start=start,
            bid_open=quote.bid, bid_high=quote.bid, bid_low=quote.bid, bid_close=quote.bid,
            ask_open=quote.ask, ask_high=quote.ask, ask_low=quote.ask, ask_close=quote.ask,
            updates=0, delayed_minutes=quote.delayed_minutes,
        )

    def take(self, quote: Quote) -> None:
        self.bid_high = max(self.bid_high, quote.bid)
        self.bid_low = min(self.bid_low, quote.bid)
        self.bid_close = quote.bid
        if quote.ask is not None:
            if self.ask_open is None:
                self.ask_open = self.ask_high = self.ask_low = quote.ask
            self.ask_high = max(self.ask_high, quote.ask)
            self.ask_low = min(self.ask_low, quote.ask)
            self.ask_close = quote.ask
        self.updates += 1
        self.delayed_minutes = quote.delayed_minutes


@dataclass
class ClosedBucket:
    start: int
    # A quote arrived recently enough for the standing quotes to be believed.
    feed_ok: bool
    # Instruments with a standing quote when the bucket closed.
    instruments: int
    bars: list[Bar]


class BarBuilder:
    def __init__(self):
        self._standing: dict[Hashable, Quote] = {}
        self._bars: dict[Hashable, Bar] = {}
        # The bucket being filled; everything before it is closed.
        self._current: int | None = None
        self._last_quote_at: float | None = None
        self._closed: list[ClosedBucket] = []
        self.skipped_buckets = 0

    def has_quote(self, key: Hashable) -> bool:
        """Whether a quote is standing for the instrument: Saxo is pricing it for us."""
        return key in self._standing

    def update(self, key: Hashable, quote: Quote | None, at: float) -> None:
        """A row received from the stream at ``at``; ``quote`` None when the instrument stopped being quotable."""
        self._advance_to(bucket_start(at))
        self._last_quote_at = at

        previous = self._standing.get(key)
        if quote is None:
            # Not quotable any more (knocked out, market closed). The bucket keeps
            # the bar of what was quoted until now, often the part worth having,
            # and no later bucket gets one.
            if previous is not None:
                self._bars.setdefault(key, Bar.opening_on(key, self._current, previous))
                del self._standing[key]
            return

        if previous == quote:
            return
        bar = self._bars.get(key)
        if bar is None:
            bar = self._bars[key] = Bar.opening_on(key, self._current, previous or quote)
            if previous is None:
                # First quote ever seen for it: the bar opens on it, and it is one update.
                bar.updates = 1
                self._standing[key] = quote
                return
        bar.take(quote)
        self._standing[key] = quote

    def forget(self, key: Hashable) -> None:
        """An instrument that is no longer recorded: no further bar. What it did in the bucket in progress stays."""
        self._standing.pop(key, None)

    def retain(self, keys) -> None:
        keep = set(keys)
        for key in [key for key in self._standing if key not in keep]:
            self.forget(key)

    def close_until(self, now: float) -> list[ClosedBucket]:
        """Every bucket that ended at or before ``now`` and was not handed over yet, oldest first."""
        self._advance_to(bucket_start(now))
        closed, self._closed = self._closed, []
        return closed

    def _advance_to(self, bucket: int) -> None:
        if self._current is None:
            self._current = bucket
            return
        behind = (bucket - self._current) // BAR_SECONDS
        if behind > MAX_CATCH_UP_BUCKETS:
            # Close the bucket in progress, then jump: nothing is known of the time in between.
            self._closed.append(self._close_current())
            self.skipped_buckets += behind - 1
            self._current = bucket
            return
        while self._current < bucket:
            self._closed.append(self._close_current())
            self._current += BAR_SECONDS

    def _close_current(self) -> ClosedBucket:
        start = self._current
        end = start + BAR_SECONDS
        feed_ok = self._last_quote_at is not None and self._last_quote_at >= end - FEED_SILENCE_SECONDS
        # What moved in the bucket, then what stood still through it.
        bars = list(self._bars.values())
        if feed_ok:
            bars += [
                Bar.opening_on(key, start, quote) for key, quote in self._standing.items() if key not in self._bars
            ]
        self._bars = {}
        return ClosedBucket(start=start, feed_ok=feed_ok, instruments=len(self._standing), bars=bars)
