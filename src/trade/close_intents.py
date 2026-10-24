"""
What the position monitor keeps of a position it has to sell, until Saxo no
longer lists it.

A sale can fail and work a little later: no quote around a release (on
2026-10-14 the issuer quoted the turbo held through CPI for none of 250
seconds), Saxo saying the position cannot be closed now, a refused request, a
sell order accepted that waits for a quote. Before, the next price update
tried again at once and said so on Telegram each time, and a close asked by a
signal was not tried again at all.

An intent spaces the attempts (``retry_after``), says the first failure and
then less and less often (``ALERT_GAPS``), does not send a sell while one Saxo
accepted may still be filled (``WORKING_SELL_SECONDS``), and ends when the
position leaves the stream: sold, by WATA or by anyone else.

Pure: it is given the time, does no I/O and never raises.
"""

from dataclasses import dataclass

# The wait after a failed attempt doubles from FIRST_RETRY_SECONDS up to MAX_RETRY_SECONDS, then,
# once an intent is older than SLOW_AFTER_SECONDS (a market closed, a turbo no longer quoted),
# SLOW_RETRY_SECONDS, and after an hour VERY_SLOW_RETRY_SECONDS: a weekend of attempts every 15 s
# would be thousands of refused requests. A price that moves again brings the next try within
# MAX_RETRY_SECONDS (``quoted``): the market is back.
FIRST_RETRY_SECONDS = 1.0
MAX_RETRY_SECONDS = 15.0
SLOW_AFTER_SECONDS = 600.0
SLOW_RETRY_SECONDS = 60.0
VERY_SLOW_AFTER_SECONDS = 3600.0
VERY_SLOW_RETRY_SECONDS = 300.0
# A sell Saxo accepted is given this long to be filled before the position is looked at again.
WORKING_SELL_SECONDS = 20.0
# When a failing intent is said again: after its first failure, 5, 10 and 15 minutes later, then
# every half hour, then every hour.
ALERT_GAPS = (300.0, 300.0, 300.0, 1800.0)
LATER_ALERT_GAP = 3600.0


def retry_after(attempts: int, age_seconds: float) -> float:
    """How long to wait after the ``attempts``-th failure of an intent ``age_seconds`` old."""
    if age_seconds >= VERY_SLOW_AFTER_SECONDS:
        return VERY_SLOW_RETRY_SECONDS
    if age_seconds >= SLOW_AFTER_SECONDS:
        return SLOW_RETRY_SECONDS
    return min(MAX_RETRY_SECONDS, FIRST_RETRY_SECONDS * 2 ** max(0, attempts - 1))


def alert_gap(alerts: int) -> float:
    """How long after its ``alerts``-th alert a failing intent is said again."""
    return ALERT_GAPS[alerts - 1] if 0 < alerts <= len(ALERT_GAPS) else LATER_ALERT_GAP


@dataclass
class CloseIntent:
    position_id: str
    reason: str
    since: float
    action: str | None = None
    # A threshold just hit: its first attempt asks nothing of Saxo before the sale.
    urgent: bool = False
    # The attempts that failed, the last one's error, and when the next is due.
    attempts: int = 0
    last_error: str | None = None
    next_at: float = 0.0
    # Alerts said of its failures, and when the last one was.
    alerts: int = 0
    alerted_at: float | None = None
    # When Saxo last accepted a sell for it while the position is still listed.
    sell_accepted_at: float | None = None
    # The last price seen of it, to know when it is quoted again.
    last_bid: float | None = None

    def is_due(self, now: float) -> bool:
        return now >= self.next_at


class CloseIntents:
    """The intents of one position monitor, by position."""

    def __init__(self):
        self._by_position: dict[str, CloseIntent] = {}

    def __contains__(self, position_id) -> bool:
        return str(position_id) in self._by_position

    def __len__(self) -> int:
        return len(self._by_position)

    def get(self, position_id) -> CloseIntent | None:
        return self._by_position.get(str(position_id))

    def all(self) -> list[CloseIntent]:
        return list(self._by_position.values())

    def want(self, position_id, reason: str, now: float, action: str | None = None, urgent: bool = False) -> CloseIntent:
        """The position is to be sold: a new intent, due now, or the one it has (whose reason stays the first)."""
        position_id = str(position_id)
        intent = self._by_position.get(position_id)
        if intent is None:
            intent = CloseIntent(position_id, reason, since=now, action=action, urgent=urgent, next_at=now)
            self._by_position[position_id] = intent
        elif intent.action is None:
            intent.action = action
        return intent

    def due(self, now: float, open_ids) -> list[CloseIntent]:
        """The intents to try now: their position still listed, their wait over, the oldest first."""
        listed = {str(position_id) for position_id in open_ids}
        return sorted(
            (intent for intent in self._by_position.values() if intent.position_id in listed and intent.is_due(now)),
            key=lambda intent: intent.since,
        )

    def accepted(self, position_id, now: float) -> None:
        """Saxo accepted a sell: nothing more is sent before it had time to be filled."""
        intent = self.get(position_id)
        if intent is not None:
            intent.sell_accepted_at = now
            intent.next_at = now + WORKING_SELL_SECONDS

    def quoted(self, position_id, bid, now: float) -> None:
        """A price of the position: when it moved, the next try is within MAX_RETRY_SECONDS at most."""
        intent = self.get(position_id)
        if intent is None or bid is None:
            return
        if intent.last_bid is not None and bid != intent.last_bid:
            intent.next_at = min(intent.next_at, now + MAX_RETRY_SECONDS)
        intent.last_bid = bid

    def failed(self, position_id, error: str, now: float) -> bool:
        """An attempt did not sell it: when to try again, and whether to say so now."""
        intent = self.get(position_id)
        if intent is None:
            return False
        intent.attempts += 1
        intent.last_error = str(error)
        intent.next_at = now + retry_after(intent.attempts, now - intent.since)
        if intent.alerted_at is not None and now - intent.alerted_at < alert_gap(intent.alerts):
            return False
        intent.alerts += 1
        intent.alerted_at = now
        return True

    def gone(self, open_ids) -> list[CloseIntent]:
        """Forget the intents whose position is no longer listed, and return them."""
        listed = {str(position_id) for position_id in open_ids}
        ended = [intent for position_id, intent in self._by_position.items() if position_id not in listed]
        for intent in ended:
            del self._by_position[intent.position_id]
        return ended

    def drop(self, position_id) -> None:
        self._by_position.pop(str(position_id), None)
