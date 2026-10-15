"""
The bar a strategy is given: ten seconds of the index, bid and ask, as the
price recorder writes them (``price_bar_10s``).
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class IndexBar:
    # Start of the ten seconds, epoch seconds. The bar is known ten seconds later.
    start: int
    bid_open: float
    bid_high: float
    bid_low: float
    bid_close: float
    # The bid again when the source has one price only.
    ask_open: float
    ask_high: float
    ask_low: float
    ask_close: float
    # Quote changes received in the ten seconds: what stands for volume on an index.
    updates: int

    @property
    def mid_open(self) -> float:
        return (self.bid_open + self.ask_open) / 2

    @property
    def mid_high(self) -> float:
        return (self.bid_high + self.ask_high) / 2

    @property
    def mid_low(self) -> float:
        return (self.bid_low + self.ask_low) / 2

    @property
    def mid_close(self) -> float:
        return (self.bid_close + self.ask_close) / 2
