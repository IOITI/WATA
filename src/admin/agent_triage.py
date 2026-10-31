"""
An alert triaged by the AI agent as it fires (``admin.ai_agent.triage_*``, off
unless triage_enabled, read at the admin's start): a sale Saxo keeps refusing,
a service of the trading path down in trading hours (the TA engine and the
Capital.com feed too while a rule is live), the live trading leaving its paper
reference's range, Saxo's login to authorise again, the account guard's pause.

It listens to the admin's live events (src/admin/events.py): while it does,
the watcher looks at the system whether a page is open or not, as it does while
one is; its first look is a baseline, so nothing already wrong at the start is
triaged. A conversation is asked about what fired (a position, an incident of
the health) by the triage's model within its own cost and without a backtest,
its diagnosis said on Telegram (triage_telegram) by the agent's teller, with
the one action it may propose.

Bounded: one alert once an hour (the same firing again meanwhile counted, and
said in its next triage), ``triage_max_per_day`` in all, one at a time (three
more wait at most). A service is triaged in trading hours only, never during a
deploy or a restart, nor in the two minutes after one. It lives outside
src/admin/agent/, which never reaches the notifier nor the services.
"""

import asyncio
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from starlette.concurrency import run_in_threadpool

from src.admin.agent.prompts import triage_prompt
from src.admin.agent.store import UnknownConversation, stamp
from src.admin.agent.transcript import UNDER_WAY
from src.admin.agent_jobs import read_config
from src.admin.agent_telegram import TRIAGE
from src.admin.errors import InvalidRequest, NotPossible
from src.release.guards import trading_hours, within_hours

logger = logging.getLogger(__name__)

FILE = "triage.json"
# A look at the triage under way (and what waits), between two events.
LOOK_SECONDS = 30
ONCE_EVERY_SECONDS = 3600
WAITING_AT_MOST = 3
# After a deploy or a restart, a service's state is not judged for this long.
QUIET_AFTER_SECONDS = 120
KEPT_DAYS = 30
# The services of the trading path, triaged when they go down in trading hours; those a live rule needs.
TRADING_PATH = ("trader", "position_monitor", "web_server", "scheduler", "postgres", "rabbitmq", "watchlist_manager")
LIVE_RULES_NEED = ("ta_engine", "capital_feed")


@dataclass(frozen=True)
class Alert:
    # One per thing that fires: triaged once an hour at most.
    key: str
    # What fired, in words.
    what: str
    # What the conversation is about (src/admin/agent/service.py subjects).
    subject: dict
    # What the admin saw.
    facts: dict = field(default_factory=dict)
    # A service's state: triaged in trading hours only, outside deploys and restarts.
    service: bool = False
    # A pause of new entries: triaged only when it is the account guard's.
    guard: bool = False


def alert_of(event: dict, at: str, live_rules: bool) -> Alert | None:
    """What an event of the admin says that is worth a triage, or None. ``at``: now, YYYY-MM-DD HH:MM (UTC)."""
    kind = event.get("type")
    if kind == "order_refused" and event.get("purpose", "close") == "close" and event.get("position_id"):
        position = str(event["position_id"])
        named = event.get("instrument_name") or "its turbo"
        return Alert(
            key=f"refused:{position}", what=f"Saxo refused the sale of position {position} ({named})",
            subject={"kind": "position", "id": position},
            facts={"refused since the last alert": event.get("count"), "refused in all": event.get("total"),
                   "Saxo's answer": event.get("outcome"), "HTTP status": event.get("http_status"),
                   "error code": event.get("error_code"), "error message": event.get("error_message"),
                   "last refusal at": event.get("last_at")})
    if kind == "service_status":
        service, status, name = event.get("id"), event.get("status"), event.get("name") or event.get("id")
        facts = {"status": status, "before": event.get("previous"), "the health says": event.get("detail"), "at": at}
        if service == "live_drift" and status in ("warning", "down"):
            return Alert(key="service:live_drift", what="the live trading left its paper reference's range",
                         subject={"kind": "incident", "id": service, "at": at}, facts=facts)
        if status == "down" and (service in TRADING_PATH or (service in LIVE_RULES_NEED and live_rules)):
            return Alert(key=f"service:{service}", what=f"{name} is down",
                         subject={"kind": "incident", "id": service, "at": at}, facts=facts, service=True)
        return None
    if kind == "saxo_authorisation_required":
        return Alert(key="saxo", what="Saxo's login needs to be authorised again",
                     subject={"kind": "incident", "id": "saxo", "at": at}, facts={"at": at})
    if kind == "entries_paused":
        return Alert(key="account_guard", what="the account guard paused new entries",
                     subject={"kind": "incident", "id": "account_guard", "at": at}, facts={"at": at}, guard=True)
    return None


class TriageState:
    """What was triaged and when (``triage.json``): each alert's last triage, and the triages of each day."""

    def __init__(self, path: str):
        self._path = path
        self._lock = threading.Lock()

    def read(self) -> dict:
        try:
            with open(self._path, encoding="utf-8") as kept:
                found = json.load(kept)
        except (OSError, ValueError):
            found = {}
        return {"alerts": found.get("alerts") or {}, "days": found.get("days") or {}} if isinstance(found, dict) \
            else {"alerts": {}, "days": {}}

    def write(self, state: dict) -> None:
        days = dict(sorted(state["days"].items())[-KEPT_DAYS:])
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        partial = f"{self._path}.part"
        with self._lock:
            with open(partial, "w", encoding="utf-8") as out_file:
                json.dump({"alerts": state["alerts"], "days": days}, out_file, ensure_ascii=False)
            os.replace(partial, self._path)


class AlertTriage:
    def __init__(self, *, agent, events, config_path: str, path: str, teller=None, paused_by_guard=None,
                 busy=lambda: False, live_rules=lambda: False, clock=time.time):
        self._agent = agent
        self._events = events
        self._config_path = config_path
        self._state = TriageState(path)
        self._teller = teller
        # async () -> whether the pause of new entries is the account guard's.
        self._paused_by_guard = paused_by_guard
        # () -> a deploy applies or a restart runs; () -> a signal rule is live.
        self._busy = busy
        self._live_rules = live_rules
        self._clock = clock
        self._quiet_until = 0.0
        self._working: str | None = None
        self._waiting: list[Alert] = []

    async def keep(self) -> None:
        """Until cancelled: each event looked at as it comes, and the triage under way every LOOK_SECONDS."""
        queue = self._events.subscribe()
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), LOOK_SECONDS)
                except asyncio.TimeoutError:
                    event = None
                try:
                    await self.look(event)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.warning(f"The AI agent's triage could not look at {event and event.get('type')}: {e}")
        finally:
            self._events.unsubscribe(queue)

    async def look(self, event: dict | None, now: float | None = None) -> str | None:
        """An event looked at (or none: the tick), and what waits started once nothing is under way; what began."""
        now = self._clock() if now is None else now
        if await run_in_threadpool(self._busy):
            self._quiet_until = now + QUIET_AFTER_SECONDS
        if not self._agent.ready or not self._agent.settings.triage_enabled:
            return None
        if event is not None:
            at = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d %H:%M")
            alert = alert_of(event, at, await run_in_threadpool(self._live_rules))
            if alert is not None and await self._worth(alert, now):
                self._wait(alert)
        if not self._waiting or await self._under_way():
            return None
        return await self._start(self._waiting.pop(0), now)

    async def _worth(self, alert: Alert, now: float) -> bool:
        if alert.guard and not (self._paused_by_guard is not None and await self._paused_by_guard()):
            return False
        if alert.service:
            if now < self._quiet_until:
                return False
            config = await run_in_threadpool(read_config, self._config_path)
            if not within_hours(trading_hours(config), now):
                return False
        return True

    def _wait(self, alert: Alert) -> None:
        if any(waiting.key == alert.key for waiting in self._waiting):
            return
        if len(self._waiting) >= WAITING_AT_MOST:
            logger.info(f"The AI agent's triage leaves out {alert.key}: {WAITING_AT_MOST} alerts wait already")
            return
        self._waiting.append(alert)

    async def _under_way(self) -> bool:
        if self._working is None:
            return False
        try:
            said = await run_in_threadpool(self._agent.conversation, self._working)
        except UnknownConversation:
            said = {}
        if said.get("status") in UNDER_WAY:
            return True
        self._working = None
        return False

    async def _start(self, alert: Alert, now: float) -> str | None:
        settings = self._agent.settings
        state = await run_in_threadpool(self._state.read)
        last = state["alerts"].get(alert.key) or {}
        if last.get("last_at") and last["last_at"] > stamp(now - ONCE_EVERY_SECONDS):
            last["repeats"] = (last.get("repeats") or 0) + 1
            await run_in_threadpool(self._state.write, state)
            return None
        day = stamp(now)[:10]
        if state["days"].get(day, 0) >= settings.triage_max_per_day:
            logger.info(f"The AI agent's triage leaves out {alert.key}: {settings.triage_max_per_day} triaged today")
            return None
        try:
            meta = await self._agent.create(
                alert.subject, triage_prompt(alert.what, alert.facts, last.get("repeats") or 0), "triage",
                settings.triage_model, settings.triage_effort,
                job={"kind": "triage", "alert": alert.key, "max_usd": settings.triage_max_usd, "backtests": 0,
                     "title": f"Triage: {alert.what}"})
        except (NotPossible, InvalidRequest) as e:
            # Paused, without a key, the month spent, or what it is about gone: not triaged, and not counted.
            logger.info(f"The AI agent's triage of {alert.key} was not asked: {e}")
            return None
        self._working = meta["id"]
        state["alerts"][alert.key] = {"last_at": stamp(now), "conversation": meta["id"], "repeats": 0}
        state["days"][day] = state["days"].get(day, 0) + 1
        await run_in_threadpool(self._state.write, state)
        if settings.triage_telegram and self._teller is not None:
            await run_in_threadpool(self._teller.add, meta["id"], TRIAGE, request=0, label=alert.what)
        return meta["id"]


def triage_of(ctx, teller) -> AlertTriage:
    """The triage over the admin's context: its events, its deploys and restarts, its rules, the pause's owner."""
    from src.admin.ta_rules import LIVE, RuleStore

    def busy() -> bool:
        applying = ctx.release is not None and ctx.release.applying() is not None
        return applying or (ctx.restarts is not None and ctx.restarts.busy)

    def live_rules() -> bool:
        if ctx.ta_scripts is None:
            return False
        return any(rule.get("mode") == LIVE for rule in RuleStore(ctx.ta_scripts).load()["rules"])

    async def paused_by_guard() -> bool:
        return bool(await ctx.queries.entries_paused_by_guard())

    return AlertTriage(agent=ctx.agent, events=ctx.events, config_path=ctx.settings.config_path,
                       path=os.path.join(ctx.settings.agent_path, FILE), teller=teller,
                       paused_by_guard=paused_by_guard, busy=busy, live_rules=live_rules)
