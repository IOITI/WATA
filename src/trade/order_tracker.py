"""
What became of each order sent to Saxo, and when.

It exists for the admin interface ("did Saxo take long to turn this order into
a position, or was it given up and cancelled?"). The marks of an order are set
in memory while it is being placed. Writing them happens in the background and
is allowed to fail: nothing on the trade path waits for it or depends on it.
"""

import asyncio
import logging
from datetime import datetime

import pytz

logger = logging.getLogger(__name__)

OPEN = "open"    # a buy order, meant to give a position
CLOSE = "close"  # a sell order, meant to close one

CONFIRMED = "confirmed"  # Saxo showed the position open (or closed)
GAVE_UP = "gave_up"      # it was not shown, after every look
ERROR = "error"          # something else stopped it once Saxo had the order

WRITE_TIMEOUT_SECONDS = 5.0
MAX_DETAIL_LENGTH = 2000


class OrderTrace:
    """
    The marks of one order, by the clock of the service that sent it. Each
    mark is a plain assignment. The first outcome marked is the one that counts.
    """

    def __init__(self, purpose: str, position_id=None):
        self.purpose = purpose
        self.order_id: str | None = None
        self.position_id: str | None = None if position_id is None else str(position_id)
        self.sent_at: datetime | None = None
        self.accepted_at: datetime | None = None
        self.confirmed_at: datetime | None = None
        self.gave_up_at: datetime | None = None
        self.lookups = 0
        self.outcome: str | None = None
        self.cancel_accepted: bool | None = None
        self.detail: str | None = None

    @staticmethod
    def _now() -> datetime:
        return datetime.now(pytz.utc)

    def sent(self) -> None:
        """The order is about to leave."""
        self.sent_at = self._now()

    def accepted(self, order_id) -> None:
        """Saxo answered with the order's id."""
        self.accepted_at = self._now()
        self.order_id = None if order_id is None else str(order_id)

    def looked(self) -> None:
        """Saxo was asked once more whether the position is open (or closed)."""
        self.lookups += 1

    def confirmed(self, position_id=None) -> None:
        """Saxo showed the position open (or closed)."""
        if self.outcome is not None:
            return
        self.confirmed_at = self._now()
        self.outcome = CONFIRMED
        if position_id is not None:
            self.position_id = str(position_id)

    def gave_up(self, detail=None, cancel_accepted: bool | None = None) -> None:
        """Every look was made and Saxo never showed it."""
        self._ended(GAVE_UP, detail, cancel_accepted)

    def failed(self, detail=None, cancel_accepted: bool | None = None) -> None:
        """Something else stopped it once Saxo had the order."""
        self._ended(ERROR, detail, cancel_accepted)

    def _ended(self, outcome: str, detail, cancel_accepted) -> None:
        if self.outcome is not None:
            return
        self.gave_up_at = self._now()
        self.outcome = outcome
        self.cancel_accepted = None if cancel_accepted is None else bool(cancel_accepted)
        self.detail = None if detail is None else str(detail)[:MAX_DETAIL_LENGTH]

    def as_entry(self, recorded_by: str | None = None) -> dict:
        """What is known now, as a row of ``order_execution``."""
        return {
            "order_id": self.order_id,
            "purpose": self.purpose,
            "position_id": self.position_id,
            "sent_at": self.sent_at,
            "accepted_at": self.accepted_at,
            "confirmed_at": self.confirmed_at,
            "gave_up_at": self.gave_up_at,
            "lookups": self.lookups,
            "outcome": self.outcome,
            "cancel_accepted": self.cancel_accepted,
            "detail": self.detail,
            "recorded_by": recorded_by,
        }


class OrderTracker:
    def __init__(self, db_execution_manager, db_order_manager=None, recorded_by: str | None = None):
        self._db = db_execution_manager
        self._orders = db_order_manager
        self._recorded_by = recorded_by
        # Strong references: the loop only keeps weak ones to running tasks.
        self._pending: set[asyncio.Task] = set()

    def record(self, trace: OrderTrace, order_row: dict | None = None) -> None:
        """
        Keep what is known of ``trace`` so far. ``order_row`` is the order
        itself (``turbo_data_order``), for the orders nothing else writes: a
        sell, or a buy that gave no position. Returns at once and never raises.
        """
        try:
            if not isinstance(trace, OrderTrace) or not trace.order_id:
                # Saxo never had it: there is no order to speak of.
                return
            write = self._write(trace.as_entry(self._recorded_by), order_row)
            try:
                task = asyncio.create_task(write)
            except RuntimeError:
                # No running event loop to write from.
                write.close()
                return
            self._pending.add(task)
            task.add_done_callback(self._pending.discard)
        except Exception as e:
            logger.warning("Could not keep what became of an order: %r", e)

    async def _write(self, entry: dict, order_row: dict | None) -> None:
        # Two rows, two attempts: one failing does not lose the other.
        if order_row is not None and self._orders is not None:
            try:
                await asyncio.wait_for(self._orders.insert_turbo_order_data(order_row), WRITE_TIMEOUT_SECONDS)
            except Exception as e:
                logger.warning("Could not record order %s: %r", entry["order_id"], e)
        try:
            await asyncio.wait_for(self._db.record(entry), WRITE_TIMEOUT_SECONDS)
        except Exception as e:
            logger.warning("Could not keep what became of order %s: %r", entry["order_id"], e)

    async def wait_for_pending(self, timeout: float = WRITE_TIMEOUT_SECONDS) -> None:
        """Give writes still in flight a chance to finish (used at shutdown)."""
        if self._pending:
            await asyncio.wait(set(self._pending), timeout=timeout)


def track_order(tracker: OrderTracker | None, trace, order_row: dict | None = None) -> None:
    """``tracker.record`` for callers that may run without a tracker. Never raises."""
    if tracker is None:
        return
    try:
        tracker.record(trace, order_row)
    except Exception as e:
        logger.warning("Could not keep what became of an order: %r", e)
