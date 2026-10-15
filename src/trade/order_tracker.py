"""
What became of each order sent to Saxo, and when, and every request made to
Saxo's order endpoint with what Saxo answered.

It exists for the admin interface ("did Saxo take long to turn this order into
a position, or was it given up and cancelled?", "why did Saxo refuse this
sell?"). The marks of an order, and a request with its answer, are set in
memory while the order is being placed. Writing them happens in the background
and is allowed to fail: nothing on the trade path waits for it or depends on it.
"""

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime

import httpx
import pytz

from src.saxo_openapi.exceptions import OpenAPIError
from src.trade.exceptions import saxo_error_parts

logger = logging.getLogger(__name__)

OPEN = "open"    # a buy order, meant to give a position
CLOSE = "close"  # a sell order, meant to close one
STOP = "stop"    # the protective stop left resting at Saxo on a position

CONFIRMED = "confirmed"  # Saxo showed the position open (or closed)
GAVE_UP = "gave_up"      # it was not shown, after every look
ERROR = "error"          # something else stopped it once Saxo had the order

# What was asked of Saxo's order endpoint.
PLACE = "place"
CANCEL = "cancel"

# What came of a request. ERROR above: anything else stopped it.
ACCEPTED = "accepted"    # Saxo said yes
REFUSED = "refused"      # Saxo answered with an error
NO_ANSWER = "no_answer"  # no answer came back: Saxo may have taken the order all the same

WRITE_TIMEOUT_SECONDS = 5.0
MAX_DETAIL_LENGTH = 2000
# Never kept or shown: they are the account's.
ACCOUNT_KEYS = frozenset({"AccountKey", "ClientKey"})


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


def without_account_keys(value):
    """``value`` without the account's keys, at any depth: what may be kept and shown of a request."""
    if isinstance(value, dict):
        return {key: without_account_keys(item) for key, item in value.items() if key not in ACCOUNT_KEYS}
    if isinstance(value, (list, tuple)):
        return [without_account_keys(item) for item in value]
    return value


def _as_text(value) -> str | None:
    """A body as it can be kept: JSON when it is not text already, cut at ``MAX_DETAIL_LENGTH``."""
    if value is None:
        return None
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if not isinstance(value, str):
        try:
            value = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            value = str(value)
    return value[:MAX_DETAIL_LENGTH]


def _number(value, kind):
    """``value`` as ``kind``, or None when it is not one (a column must never refuse the row)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return kind(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _causes(error: BaseException) -> list[BaseException]:
    """``error`` and what it was raised from, nearest first."""
    chain: list[BaseException] = []
    while error is not None and len(chain) < 10 and all(error is not seen for seen in chain):
        chain.append(error)
        error = error.__cause__ or error.__context__
    return chain


def describe_failure(error: BaseException) -> dict:
    """
    What a request that did not succeed tells: whether Saxo answered at all,
    and what. Saxo's answer is the one the client raised first, as
    ``OpenAPIError``: the exceptions raised from it say less of it.
    """
    causes = _causes(error)
    answer = next((cause for cause in causes if isinstance(cause, OpenAPIError)), None)
    if answer is not None:
        body = answer.content
        try:
            details = json.loads(body) if isinstance(body, (str, bytes)) else body
        except ValueError:
            details = body
        code, message = saxo_error_parts(details)
        return {
            "outcome": REFUSED,
            "http_status": _number(answer.code, int),
            "error_code": code,
            "error_message": message or (str(answer.reason) if answer.reason else None),
            "response_body": _as_text(body),
        }
    if any(isinstance(cause, asyncio.CancelledError) for cause in causes):
        outcome, message = NO_ANSWER, "Stopped before Saxo answered: the service was stopping"
    elif any(isinstance(cause, httpx.RequestError) for cause in causes):
        outcome, message = NO_ANSWER, f"{type(error).__name__}: {error}"
    else:
        outcome, message = ERROR, f"{type(error).__name__}: {error}"
    return {
        "outcome": outcome,
        "http_status": None,
        "error_code": None,
        "error_message": message[:MAX_DETAIL_LENGTH],
        "response_body": None,
    }


@dataclass(frozen=True)
class OrderContext:
    """
    Why a request is made to Saxo's order endpoint, for the record: what the
    caller knows and the order service does not.
    """

    purpose: str | None = None          # OPEN, CLOSE or STOP
    position_id: str | None = None      # the position being sold, or given a stop
    signal_uuid: str | None = None      # the signal behind a buy
    action: str | None = None           # long or short
    reason: str | None = None           # why WATA sent it: the exit rule, "Explicit Close (...)"...
    instrument_name: str | None = None
    instrument_symbol: str | None = None
    instrument_currency: str | None = None
    price_seen: float | None = None     # the bid (a sell) or the ask (a buy) it was decided on


class OrderRequest:
    """
    One request to Saxo's order endpoint and its answer, by the clock of the
    service that sent it. It is described just before it leaves (plain
    assignments) and completed once Saxo answered or the request failed.
    """

    def __init__(self, operation: str, body=None, context: OrderContext | None = None, order_ids=None):
        self.request_id = str(uuid.uuid4())
        self.operation = operation
        self.context = context if isinstance(context, OrderContext) else OrderContext()
        self.body = without_account_keys(body)
        # What a cancellation names; for an order placed, the id Saxo gives it.
        self.order_id: str | None = None if order_ids is None else str(order_ids)
        self.requested_at = self._now()
        self.answered_at: datetime | None = None
        self.outcome: str | None = None
        self.http_status: int | None = None
        self.error_code: str | None = None
        self.error_message: str | None = None
        self.response_body: str | None = None

    @staticmethod
    def _now() -> datetime:
        # Its own clock: tests give the order's marks (OrderTrace._now) moments of their own.
        return datetime.now(pytz.utc)

    def accepted(self, response=None, order_id=None) -> None:
        """Saxo said yes; ``order_id`` is the order it gave, when one was placed."""
        self.answered_at = self._now()
        self.outcome = ACCEPTED
        if order_id is not None:
            self.order_id = str(order_id)
        self.response_body = _as_text(response)

    def failed(self, error: BaseException, response=None) -> None:
        """It did not succeed: refused by Saxo, left without an answer, or stopped by something else."""
        self.answered_at = self._now()
        for key, value in describe_failure(error).items():
            setattr(self, key, value)
        if self.response_body is None:
            self.response_body = _as_text(response)

    def as_entry(self, recorded_by: str | None = None) -> dict:
        """What is known of it, as a row of ``order_request``."""
        body = self.body if isinstance(self.body, dict) else {}
        order = body
        # A protective stop is an order inside the request.
        inner = body.get("Orders")
        if isinstance(inner, list) and inner and isinstance(inner[0], dict):
            order = inner[0]
        context = self.context
        position_id = context.position_id if context.position_id is not None else body.get("PositionId")
        return {
            "request_id": self.request_id,
            "requested_at": self.requested_at,
            "answered_at": self.answered_at,
            "recorded_by": recorded_by,
            "operation": self.operation,
            "purpose": context.purpose,
            "position_id": None if position_id is None else str(position_id),
            "signal_uuid": None if context.signal_uuid is None else str(context.signal_uuid),
            "action": context.action,
            "reason": None if context.reason is None else str(context.reason)[:MAX_DETAIL_LENGTH],
            "instrument_uic": _number(order.get("Uic"), int),
            "asset_type": order.get("AssetType"),
            "instrument_name": context.instrument_name,
            "instrument_symbol": context.instrument_symbol,
            "instrument_currency": context.instrument_currency,
            "buy_sell": order.get("BuySell"),
            "amount": _number(order.get("Amount"), float),
            "order_type": order.get("OrderType"),
            "price_seen": _number(context.price_seen, float),
            "outcome": self.outcome,
            "order_id": self.order_id,
            "http_status": self.http_status,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "request_body": _as_text(self.body),
            "response_body": self.response_body,
        }


def start_request(operation: str, body=None, context: OrderContext | None = None, order_ids=None) -> OrderRequest | None:
    """A request about to leave, or None when it cannot be described. Never raises."""
    try:
        return OrderRequest(operation, body, context, order_ids)
    except Exception as e:
        logger.warning("Could not describe a request to Saxo: %r", e)
        return None


def request_accepted(tracker, request: OrderRequest | None, response=None, order_id=None) -> None:
    """Saxo said yes to ``request``: keep it. Never raises."""
    if request is None:
        return
    try:
        request.accepted(response, order_id)
    except Exception as e:
        logger.warning("Could not describe Saxo's answer: %r", e)
    track_request(tracker, request)


def request_failed(tracker, request: OrderRequest | None, error: BaseException, response=None) -> None:
    """``request`` did not succeed: keep it with why. Never raises."""
    if request is None:
        return
    try:
        request.failed(error, response)
    except Exception as e:
        logger.warning("Could not describe why a request to Saxo failed: %r", e)
    track_request(tracker, request)


class OrderTracker:
    def __init__(
        self,
        db_execution_manager,
        db_order_manager=None,
        recorded_by: str | None = None,
        db_request_manager=None,
    ):
        self._db = db_execution_manager
        self._orders = db_order_manager
        self._requests = db_request_manager
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

    def record_request(self, request: OrderRequest) -> None:
        """Keep a request to Saxo's order endpoint and its answer. Returns at once and never raises."""
        try:
            if self._requests is None or not isinstance(request, OrderRequest):
                return
            write = self._write_request(request.as_entry(self._recorded_by))
            try:
                task = asyncio.create_task(write)
            except RuntimeError:
                # No running event loop to write from.
                write.close()
                return
            self._pending.add(task)
            task.add_done_callback(self._pending.discard)
        except Exception as e:
            logger.warning("Could not keep a request to Saxo: %r", e)

    async def _write_request(self, entry: dict) -> None:
        try:
            await asyncio.wait_for(self._requests.record(entry), WRITE_TIMEOUT_SECONDS)
        except Exception as e:
            logger.warning("Could not keep request %s to Saxo: %r", entry["request_id"], e)

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


def track_request(tracker: OrderTracker | None, request: OrderRequest | None) -> None:
    """``tracker.record_request`` for callers that may run without a tracker. Never raises."""
    if tracker is None or request is None:
        return
    try:
        tracker.record_request(request)
    except Exception as e:
        logger.warning("Could not keep a request to Saxo: %r", e)
