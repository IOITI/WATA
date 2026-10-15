"""
Live events for the interface (positions opening and closing, Saxo refusing to
sell one, a service going down, a signal being decided, the Saxo token needing
a new authorisation).

The admin has nothing pushing to it: a watcher looks at the same sources the
pages read, on a timer, and publishes what changed since its previous look. It
only runs while a browser is listening.
"""

import asyncio
import logging
import time

from starlette.concurrency import run_in_threadpool

from src.admin.health import SAXO_SERVICE_ID, collect_health, needs_saxo_authorisation

logger = logging.getLogger(__name__)

SUBSCRIBER_QUEUE_SIZE = 100
WATCH_INTERVAL_SECONDS = 3.0
# Health asks every service: less often than the database.
HEALTH_EVERY_N_ROUNDS = 5
WATCH_ROUND_TIMEOUT_SECONDS = 20.0
# A sell Saxo keeps refusing is retried about once a second: announced at most this often, with the count since.
REFUSAL_ALERT_EVERY_SECONDS = 60.0


class EventHub:
    def __init__(self):
        self._subscribers: set[asyncio.Queue] = set()
        self._someone_listening = asyncio.Event()

    @property
    def has_subscribers(self) -> bool:
        return bool(self._subscribers)

    async def wait_for_subscriber(self) -> None:
        await self._someone_listening.wait()

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE)
        self._subscribers.add(queue)
        self._someone_listening.set()
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)
        if not self._subscribers:
            self._someone_listening.clear()

    def publish(self, event_type: str, **data) -> None:
        event = {"type": event_type, **data}
        for queue in self._subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # A browser that stopped reading: its page refreshes by polling anyway.
                pass


def _position_label(position: dict) -> dict:
    return {
        "position_id": position["position_id"],
        "action": position.get("action"),
        "instrument_name": position.get("instrument_name"),
    }


def _refusals(position: dict) -> dict:
    """What Saxo did not take of a position's sells (``AdminQueries.open_positions``), empty when nothing."""
    refusals = position.get("sell_refusals")
    return refusals if isinstance(refusals, dict) else {}


class StateWatcher:
    """Publishes on ``hub`` what changed between two looks at the system."""

    def __init__(self, ctx, hub: EventHub, clock=time.monotonic):
        self._ctx = ctx
        self._hub = hub
        self._clock = clock
        self._round = 0
        self._open_positions: dict[str, dict] | None = None
        # Per open position: how many refused sells were already announced (or there when the page opened), and when.
        self._refusals_told: dict[str, int] = {}
        self._refusals_told_at: dict[str, float] = {}
        self._last_decision: str | None = None
        self._decisions_seen = False
        self._paused: bool | None = None
        self._service_statuses: dict[str, str] | None = None
        self._saxo_needs_authorisation: bool | None = None

    def _forget(self) -> None:
        """Without a listener nothing is compared: the next one starts from what is true then."""
        self._round = 0
        self._open_positions = None
        self._refusals_told = {}
        self._refusals_told_at = {}
        self._decisions_seen = False
        self._paused = None
        self._service_statuses = None
        self._saxo_needs_authorisation = None

    async def _watch_positions(self) -> None:
        positions = {position["position_id"]: position for position in await self._ctx.queries.open_positions()}
        previous = self._open_positions
        self._open_positions = positions
        if previous is None:
            # What Saxo refused before the page opened is not news to it.
            self._refusals_told = {pid: _refusals(position).get("count") or 0 for pid, position in positions.items()}
            return
        self._watch_refusals(positions)
        for position_id in positions.keys() - previous.keys():
            self._hub.publish("position_opened", **_position_label(positions[position_id]))
        for position_id in previous.keys() - positions.keys():
            closed = await self._ctx.queries.get_position(position_id)
            details = closed["position"] if closed else previous[position_id]
            self._hub.publish(
                "position_closed",
                **_position_label(details),
                profit_loss=details.get("position_profit_loss"),
                performance_percent=details.get("position_total_performance_percent"),
                close_reason=details.get("position_close_reason"),
            )

    def _watch_refusals(self, positions: dict[str, dict]) -> None:
        """Saxo refused to sell an open position since the last look: say so, at most once a minute for each."""
        now = self._clock()
        for position_id in list(self._refusals_told):
            if position_id not in positions:
                del self._refusals_told[position_id]
                self._refusals_told_at.pop(position_id, None)
        for position_id, position in positions.items():
            refusals = _refusals(position)
            count = refusals.get("count") or 0
            told = self._refusals_told.setdefault(position_id, 0)
            if count <= told:
                continue
            told_at = self._refusals_told_at.get(position_id)
            if told_at is not None and now - told_at < REFUSAL_ALERT_EVERY_SECONDS:
                continue
            self._hub.publish(
                "order_refused",
                **_position_label(position),
                purpose="close",
                count=count - told,
                total=count,
                outcome=refusals.get("outcome"),
                http_status=refusals.get("http_status"),
                error_code=refusals.get("error_code"),
                error_message=refusals.get("error_message"),
                last_at=refusals.get("last_at"),
            )
            self._refusals_told[position_id] = count
            self._refusals_told_at[position_id] = now

    async def _watch_journal(self) -> None:
        entries = await self._ctx.queries.signal_journal(limit=20)
        newest = entries[0]["signal_uuid"] if entries else None
        if self._decisions_seen and newest != self._last_decision:
            # Newest first: everything before the last one already announced is new.
            fresh = []
            for entry in entries:
                if entry["signal_uuid"] == self._last_decision:
                    break
                fresh.append(entry)
            for entry in reversed(fresh):
                self._hub.publish(
                    "signal_decided",
                    signal_uuid=entry["signal_uuid"],
                    action=entry.get("action"),
                    indice=entry.get("indice"),
                    outcome=entry.get("outcome"),
                    reason=entry.get("reason"),
                )
        self._last_decision = newest
        self._decisions_seen = True

    async def _watch_pause(self) -> None:
        paused = await self._ctx.queries.entries_paused_since() is not None
        if self._paused is not None and paused != self._paused:
            self._hub.publish("entries_paused" if paused else "entries_resumed")
        self._paused = paused

    async def _watch_health(self) -> None:
        health = await collect_health(self._ctx)
        statuses = {service["id"]: service["status"] for service in health["services"]}
        if self._service_statuses is not None:
            for service in health["services"]:
                if service["id"] == SAXO_SERVICE_ID:
                    # Announced by its own event, which says what to do about it.
                    continue
                previous = self._service_statuses.get(service["id"])
                if previous is not None and previous != service["status"]:
                    self._hub.publish(
                        "service_status",
                        id=service["id"],
                        name=service["name"],
                        status=service["status"],
                        previous=previous,
                        detail=service["detail"],
                    )
        self._service_statuses = statuses

    async def _watch_saxo(self) -> None:
        status = await run_in_threadpool(self._ctx.saxo.status)
        needs_authorisation = needs_saxo_authorisation(status)
        if self._saxo_needs_authorisation is not None and needs_authorisation != self._saxo_needs_authorisation:
            self._hub.publish("saxo_authorisation_required" if needs_authorisation else "saxo_authorised")
        self._saxo_needs_authorisation = needs_authorisation

    async def look(self) -> None:
        """One look at every source; a source that fails does not hide the others."""
        watches = [self._watch_positions, self._watch_journal, self._watch_pause]
        if self._round % HEALTH_EVERY_N_ROUNDS == 0:
            watches += [self._watch_health, self._watch_saxo]
        self._round += 1
        for watch in watches:
            try:
                await watch()
            except Exception as e:
                logger.debug("Event watcher: %s failed: %r", watch.__name__, e)

    async def run(self, interval_seconds: float = WATCH_INTERVAL_SECONDS) -> None:
        while True:
            if self._hub.has_subscribers:
                await asyncio.sleep(interval_seconds)
            else:
                self._forget()
                # Looked at as soon as a browser connects, so that what changes
                # right after is compared with what was true at that moment.
                await self._hub.wait_for_subscriber()
            try:
                await asyncio.wait_for(self.look(), WATCH_ROUND_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                logger.warning("Event watcher: a look at the system timed out")
            except Exception as e:
                logger.error("Event watcher: unexpected error: %r", e)
