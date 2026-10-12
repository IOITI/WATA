"""
Journal of what the trader did with each signal it received.

It exists for the admin interface ("why was this signal not traded?"). Writing
to it happens in the background and is allowed to fail: nothing on the trade
path waits for it or depends on it.
"""

import asyncio
import logging
from collections import OrderedDict
from datetime import datetime

import pytz

logger = logging.getLogger(__name__)

EXECUTED = "executed"  # a position was opened
CLOSED = "closed"      # positions were closed
REFUSED = "refused"    # a trading rule refused it
FAILED = "failed"      # an error prevented it
IGNORED = "ignored"    # nothing to do (stored before startup, nothing to close)

WRITE_TIMEOUT_SECONDS = 5.0
MAX_REMEMBERED_SIGNALS = 512


class SignalJournal:
    def __init__(self, db_journal_manager):
        self._db = db_journal_manager
        # Strong references: the loop only keeps weak ones to running tasks.
        self._pending: set[asyncio.Task] = set()
        # The first outcome recorded for a signal is the one that counts.
        self._recorded: OrderedDict[str, None] = OrderedDict()

    def record(
        self,
        signal: dict,
        outcome: str,
        reason: str | None = None,
        *,
        position_id: str | None = None,
        order_id: str | None = None,
    ) -> None:
        """Note the outcome of ``signal``. Returns at once and never raises."""
        try:
            signal_uuid = signal.get("signal_uuid")
            if not signal_uuid or signal_uuid in self._recorded:
                return
            self._recorded[signal_uuid] = None
            if len(self._recorded) > MAX_REMEMBERED_SIGNALS:
                self._recorded.popitem(last=False)

            entry = {
                "signal_uuid": signal_uuid,
                "action": signal.get("action"),
                "indice": signal.get("indice"),
                "confidence": signal.get("confidence"),
                "signal_timestamp": signal.get("signal_timestamp"),
                "received_timestamp": signal.get("received_timestamp"),
                "decided_at": datetime.now(pytz.utc),
                "outcome": outcome,
                "reason": reason,
                "position_id": str(position_id) if position_id is not None else None,
                "order_id": str(order_id) if order_id is not None else None,
            }
            write = self._write(entry)
            try:
                task = asyncio.create_task(write)
            except RuntimeError:
                # No running event loop to write from.
                write.close()
                return
            self._pending.add(task)
            task.add_done_callback(self._pending.discard)
        except Exception as e:
            logger.warning("Could not journal the outcome of a signal: %r", e)

    async def _write(self, entry: dict) -> None:
        try:
            await asyncio.wait_for(self._db.record_decision(entry), WRITE_TIMEOUT_SECONDS)
        except Exception as e:
            logger.warning("Could not journal the outcome of signal %s: %r", entry["signal_uuid"], e)

    async def wait_for_pending(self, timeout: float = WRITE_TIMEOUT_SECONDS) -> None:
        """Give writes still in flight a chance to finish (used at shutdown)."""
        if self._pending:
            await asyncio.wait(set(self._pending), timeout=timeout)


def journal_signal(journal: SignalJournal | None, signal: dict, outcome: str, reason: str | None = None, **ids) -> None:
    """``journal.record`` for callers that may run without a journal."""
    if journal is not None:
        journal.record(signal, outcome, reason, **ids)
