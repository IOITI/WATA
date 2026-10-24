"""
Whether a position's quote is newer than its purchase, before the position
monitor judges it (``position_management.wait_for_fresh_quote``, on unless
false). The backtests' simulator judges its positions with the same class.

Saxo quotes a turbo about every 30 seconds (40 at the 95th percentile, on the
turbos recorded from 13 to 20 October 2026), while an order is filled at the
live price. The bid standing when a position appears is therefore often
older than its purchase, and set against the price paid it shows a gain or a
loss that never was. On 19 October 2026 a stop was "hit at -8.47 %" on a bid
17 to 27 seconds older than the purchase, and the sale filled at +0.51 %. Six
daily profit targets were "met" 1 to 6 seconds after a purchase, and sold at
a loss.

So a position first seen within ``FRESH_AFTER_SECONDS`` of its opening is not
judged on its bid until that bid changes: no stop, take-profit, trailing stop
or daily target, and its best result is not raised. A changed bid is a quote
newer than the purchase. A quote that does not change says nothing new, so
waiting for it costs no information. What comes with time (the day's close, a
US release) is sold whatever the quote. A position whose opening is not known
is judged at once, as before. Pure.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

# A position first seen this long after its opening: its quote is newer than its purchase.
FRESH_AFTER_SECONDS = 120


def wait_for_fresh_quote(position_management: dict | None) -> bool:
    """Whether the configuration's position management waits for a fresh quote: yes unless it says false."""
    value = (position_management or {}).get("wait_for_fresh_quote", True)
    return value if isinstance(value, bool) else True


def opened_at(position: dict) -> float | None:
    """When a position Saxo lists was opened (``PositionBase.ExecutionTimeOpen``), in epoch seconds."""
    text = (position.get("PositionBase") or {}).get("ExecutionTimeOpen")
    if not isinstance(text, str) or not text:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).timestamp()


@dataclass(frozen=True)
class Waiting:
    """A position not judged yet: the bid it was first seen at, and when."""

    bid: float
    since: float


class FreshQuotes:
    """The positions waiting for a quote newer than their purchase, by position."""

    def __init__(self, enabled: bool = True, *, fresh_after_seconds: float = FRESH_AFTER_SECONDS):
        self.enabled = enabled
        self.fresh_after_seconds = fresh_after_seconds
        self.waiting: dict = {}
        self._judged: set = set()

    def judged(self, position_id, bid: float, opened: float | None, now: float) -> bool:
        """Whether the position may be judged on ``bid`` at ``now``; ``opened``: its opening, if known."""
        if not self.enabled or position_id in self._judged:
            return True
        waiting = self.waiting.get(position_id)
        if waiting is None:
            if opened is None or now - opened >= self.fresh_after_seconds:
                self._judged.add(position_id)
                return True
            self.waiting[position_id] = Waiting(bid, now)
            return False
        if bid == waiting.bid:
            return False
        del self.waiting[position_id]
        self._judged.add(position_id)
        return True

    def forget_except(self, position_ids) -> None:
        """Drop what is kept of the positions that are no longer held."""
        kept = set(position_ids)
        for position_id in [one for one in self.waiting if one not in kept]:
            del self.waiting[position_id]
        self._judged &= kept
