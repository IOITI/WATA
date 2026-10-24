"""
Restarting WATA's services from the admin, through the control service
(src/admin/service_control.py): what holds a restart back, the order a plan
of several follows, what each costs, which saved changes a service does not
run with yet, and what says it is at work again.

A plan restarts one service at a time and waits, before the next, until the
one restarted runs again and shows it works (``Readiness``): a heartbeat begun
after its start, a consumer on its queue, an answer, a state row of its new
start, its log's line. The guards are read again before each step. The admin
goes last and alone: its own restart ends the plan.

The functions down to ``RestartPlans`` are pure; ``situation_of`` and
``Readiness`` read the admin's context.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pytz
from starlette.concurrency import run_in_threadpool

from src.admin.config_catalog import SERVICES, flatten, lookup
from src.admin.config_editor import mask_secrets
from src.admin.downloads import market_is_open, trading_timezone

logger = logging.getLogger(__name__)

# What trades, or decides when to: not restarted with a position open or the market open, unless forced.
TRADING_PATH = frozenset({"trader", "position_monitor", "watchlist_manager", "web_server", "scheduler"})
# Restarted in this order: what the others call first, the trading pair after, the admin last.
ORDER = (
    "web_server", "telegram", "watchlist_manager", "price_recorder", "capital_feed", "ta_engine",
    "scheduler", "position_monitor", "trader", "admin",
)
SERVICE_NAMES = {service.id: service.name for service in SERVICES}
# What a restart costs each service, said before it is asked.
COSTS = {
    "web_server": "The latest signal it keeps for each indice is emptied, and a webhook arriving meanwhile is refused.",
    "trader": "A signal arriving while it starts again is taken only if it is still recent once the trader polls.",
    "position_monitor": "Sales still being tried are dropped, and no exit is decided until its stream is back (seconds).",
    "scheduler": "A job due while it starts again is missed.",
    "watchlist_manager": "Until it has found the turbos again, the trader searches Saxo itself (seconds more per entry).",
    "telegram": "Messages wait in the queue meanwhile, and are sent once it is back.",
    "admin": "You are signed out, and the backtests and studies running are stopped.",
    "price_recorder": "About ten seconds of bars are not recorded, and cannot be fetched afterwards.",
    "capital_feed": "About ten seconds of bars are not recorded; the TA engine waits for them.",
    "ta_engine": "Every rule loads its history again, seconds to minutes; no signal is sent meanwhile.",
}
# How long a scheduled job keeps the scheduler from being restarted, before and after it: its
# schedule is worked out when it starts, so a restart just past a job misses that day's.
SCHEDULER_JOB_MARGIN_SECONDS = 120

WAITING, RESTARTING, STARTING, READY, FAILED, HELD, SKIPPED = (
    "waiting", "restarting", "starting", "ready", "failed", "held", "skipped",
)


@dataclass(frozen=True)
class Situation:
    """What the guards read, gathered at the moment of the request and again before each step of a plan."""

    # None: the database could not say.
    positions_open: int | None = 0
    # What the position monitor's heartbeat says Saxo lists: a position bought and not in a row yet counts.
    positions_listed: int = 0
    close_intents: tuple[str, ...] = ()
    market_open: bool = False
    live_rules: int = 0
    backtests_running: int = 0
    study_running: bool = False
    # The scheduler's job due within its margin, said ("the close of the day at 15:55, America/New_York").
    scheduler_job_near: str | None = None


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def refusal(service: str, situation: Situation) -> str | None:
    """Why ``service`` should not be restarted now, or None. A forced restart goes ahead, said in the audit."""
    reasons = []
    if service in TRADING_PATH:
        if situation.positions_open is None:
            reasons.append("the open positions cannot be read")
        held = max(situation.positions_open or 0, situation.positions_listed)
        if held:
            reasons.append(f"{_count(held, 'position')} open")
        if situation.market_open:
            reasons.append("the market is open by WATA's hours")
    if service == "position_monitor" and situation.close_intents:
        reasons.append(f"{_count(len(situation.close_intents), 'sale')} still being tried "
                       f"(position {', '.join(situation.close_intents)}), which the restart drops")
    if service == "scheduler" and situation.scheduler_job_near:
        reasons.append(f"{situation.scheduler_job_near} is within {SCHEDULER_JOB_MARGIN_SECONDS // 60} minutes")
    if service == "ta_engine" and situation.live_rules and situation.market_open:
        reasons.append(f"{_count(situation.live_rules, 'live rule')} with the market open: a signal could be missed")
    if service == "admin":
        if situation.backtests_running:
            reasons.append(f"{_count(situation.backtests_running, 'backtest')} running")
        if situation.study_running:
            reasons.append("one of Claude's studies is running")
    return "; ".join(reasons) or None


def ordered(services) -> list[str]:
    """``services`` in the order a plan restarts them, each once; anything else is refused."""
    wanted = set(services)
    unknown = wanted - set(ORDER)
    if unknown:
        raise ValueError(f"Not WATA services: {', '.join(sorted(unknown))}")
    return [service for service in ORDER if service in wanted]


def moment(value) -> datetime | None:
    """An instant as Docker, the journal or the database gives it (Docker's has nanoseconds), or None."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value or value.startswith("0001-"):
        return None
    text = value.replace("Z", "+00:00")
    if "." in text:
        head, _, rest = text.partition(".")
        digits = "".join(character for character in rest if character.isdigit())
        text = f"{head}.{digits[:6]}{rest[len(digits):]}"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def pending_changes(service: str, started_at: datetime | None, history: list[dict], current: dict,
                    modified_at: datetime | None = None) -> dict:
    """
    The saved changes ``service`` does not run with: the keys it reads whose
    value now differs from their value when it started (a change made and
    undone since is none), from the journal of saves. ``outside``: the file was
    written after the service started by something the journal does not know
    (an edit on the server), so what changed is not known.
    """
    if started_at is None:
        return {"paths": [], "outside": False}
    now = flatten(mask_secrets(current))
    at_start: dict[str, object] = {}
    absent_at_start: set[str] = set()
    latest_save = None
    for entry in sorted(history, key=lambda item: item.get("at") or ""):
        at = moment(entry.get("at"))
        if at is None:
            continue
        latest_save = at
        if at <= started_at:
            continue
        # The first change of a key after the start says what it was then.
        for change in entry.get("changes") or []:
            path = change["path"]
            if path in at_start or path in absent_at_start:
                continue
            if change.get("added"):
                absent_at_start.add(path)
            else:
                at_start[path] = change.get("before")
    paths = []
    for path in sorted(set(at_start) | absent_at_start):
        param = lookup(path)
        if param is None or service not in param.readers:
            continue
        changed = path in now if path in absent_at_start else path not in now or at_start[path] != now[path]
        if changed:
            paths.append(path)
    outside = (
        modified_at is not None and modified_at > started_at
        and (latest_save is None or (modified_at - latest_save).total_seconds() > 5)
    )
    return {"paths": paths, "outside": outside}


def _rule_config(config: dict, rule_type: str) -> dict:
    for rule in (config.get("trade") or {}).get("rules") or []:
        if isinstance(rule, dict) and rule.get("rule_type") == rule_type:
            return rule.get("rule_config") or {}
    return {}


def scheduler_jobs(config: dict) -> list[tuple[str, str, str]]:
    """The scheduler's daily jobs, as it schedules them: (what, HH:MM, timezone)."""
    zone = trading_timezone(config)
    jobs = [("the daily report", "22:00", zone), ("the calendars' check", "09:00", zone)]
    close = _rule_config(config, "day_trading").get("close_position_time")
    if close:
        jobs.append(("the close of the day", close, _rule_config(config, "market_hours").get("timezone") or zone))
    return jobs


def scheduler_job_near(config: dict, now: float, margin: int = SCHEDULER_JOB_MARGIN_SECONDS) -> str | None:
    """The scheduler's job due within ``margin`` seconds of ``now``, before or after, said; None if none."""
    for what, at, zone in scheduler_jobs(config):
        try:
            hour, minute = (int(part) for part in str(at).split(":")[:2])
            local_zone = pytz.timezone(zone)
        except (ValueError, pytz.UnknownTimeZoneError):
            continue
        today = datetime.fromtimestamp(now, local_zone).date()
        for day in (today - timedelta(days=1), today, today + timedelta(days=1)):
            due = local_zone.localize(datetime.combine(day, datetime.min.time().replace(hour=hour, minute=minute)))
            if abs(due.timestamp() - now) <= margin:
                return f"{what} at {hour:02d}:{minute:02d} ({zone})"
    return None


@dataclass
class Plan:
    services: list[str]
    forced: bool
    by: str | None
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    # Each service's step: its state and what was seen.
    steps: dict[str, dict] = field(default_factory=dict)
    done: bool = False

    def as_dict(self) -> dict:
        return {
            "services": self.services, "forced": self.forced, "by": self.by, "started_at": self.started_at,
            "steps": [{"service": service, **self.steps[service]} for service in self.services], "done": self.done,
            "names": {service: SERVICE_NAMES[service] for service in self.services},
        }


class RestartPlans:
    """
    One plan at a time: each service asked of the control service in turn, the
    next only once the one before runs again and shows it works, so that two
    never restart together. ``situation`` reads the guards again before each
    step: a plan not forced stops at a service they hold back.
    """

    def __init__(self, control, readiness, situation, events=None, *, notify=None, poll_seconds: float = 2.0,
                 running_timeout: float = 180.0, ready_timeout: float = 180.0, clock=time.monotonic,
                 sleep=asyncio.sleep):
        self._control = control
        self._readiness = readiness
        self._situation = situation
        self._events = events
        # Telegram, for a plan that stopped: whoever asked for it may have left the page.
        self._notify = notify
        self._poll = poll_seconds
        self._running_timeout = running_timeout
        self._ready_timeout = ready_timeout
        self._clock = clock
        self._sleep = sleep
        self.plan: Plan | None = None
        self._task: asyncio.Task | None = None

    @property
    def busy(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self, services: list[str], forced: bool, by: str | None) -> Plan:
        if self.busy:
            raise RuntimeError("A restart is already under way: one at a time")
        plan = Plan(services=ordered(services), forced=forced, by=by)
        plan.steps = {service: {"state": WAITING, "detail": None} for service in plan.services}
        self.plan = plan
        self._task = asyncio.create_task(self._run(plan))
        return plan

    def _said(self, plan: Plan) -> None:
        if self._events is not None:
            self._events.publish("restart_plan", plan=plan.as_dict())

    async def _state_of(self, service: str) -> dict:
        for entry in await self._control.services():
            if entry.get("service") == service:
                return entry
        return {}

    async def _until_running_again(self, service: str, before: str | None, requested_at: str | None) -> datetime | None:
        """The container's new start, once it runs again; None if it does not within its time."""
        deadline = self._clock() + self._running_timeout
        while self._clock() < deadline:
            await self._sleep(self._poll)
            try:
                state = await self._state_of(service)
            except Exception:
                continue
            last = state.get("last_restart") or {}
            # This restart's outcome, not that of one asked before.
            if last.get("ok") is False and last.get("requested_at") == requested_at:
                raise RuntimeError(f"Docker did not restart it: {last.get('detail')}")
            if state.get("running") and state.get("started_at") != before and not state.get("restarting"):
                return moment(state.get("started_at"))
        return None

    async def _until_ready(self, service: str, started_at: datetime | None, before) -> tuple[bool, str]:
        deadline = self._clock() + self._ready_timeout
        detail = "Not checked"
        while self._clock() < deadline:
            try:
                ready, detail = await self._readiness.ready(service, started_at, before)
            except Exception as e:
                ready, detail = False, str(e)
            if ready:
                return True, detail
            await self._sleep(self._poll)
        return False, detail

    async def _step(self, plan: Plan, service: str) -> bool:
        """Restart ``service``; whether the plan goes on."""
        step = plan.steps[service]
        if not plan.forced:
            held = refusal(service, await self._situation())
            if held:
                step.update(state=HELD, detail=f"Held back: {held}")
                return False
        state = await self._state_of(service)
        if state.get("state") == "absent":
            step.update(state=SKIPPED, detail="Its container is not created: its compose profile is off")
            return True
        before = await self._readiness.before(service)
        step.update(state=RESTARTING, detail=None)
        self._said(plan)
        asked = await self._control.restart(service) or {}
        if service == "admin":
            step.update(detail="The admin restarts: sign in again in a few seconds")
            return False
        started_at = await self._until_running_again(service, state.get("started_at"), asked.get("requested_at"))
        if started_at is None:
            step.update(state=FAILED, detail=f"Not running again {self._running_timeout:.0f} s after the restart")
            return False
        step.update(state=STARTING, detail=None, started_at=started_at.isoformat())
        self._said(plan)
        ready, detail = await self._until_ready(service, started_at, before)
        if not ready:
            step.update(state=FAILED, detail=f"Running again, but not at work after {self._ready_timeout:.0f} s: {detail}")
            return False
        step.update(state=READY, detail=detail)
        self._said(plan)
        return True

    async def _run(self, plan: Plan) -> None:
        try:
            for service in plan.services:
                try:
                    if not await self._step(plan, service):
                        break
                except Exception as e:
                    plan.steps[service].update(state=FAILED, detail=getattr(e, "detail", None) or str(e))
                    break
            for service in plan.services:
                if plan.steps[service]["state"] == WAITING:
                    plan.steps[service].update(state=SKIPPED, detail="Not restarted: the plan stopped before it")
        finally:
            plan.done = True
            self._said(plan)
            logger.info("Restart plan done: %s", [(step["service"], step["state"]) for step in plan.as_dict()["steps"]])
        stopped = [f"{SERVICE_NAMES[service]}: {plan.steps[service]['detail']}" for service in plan.services
                   if plan.steps[service]["state"] in (FAILED, HELD)]
        if stopped and self._notify is not None:
            try:
                await asyncio.wait_for(self._notify("⚠️ WATA Admin: a restart stopped\n" + "\n".join(stopped)), 10)
            except Exception as e:
                logger.warning("Could not say on Telegram that a restart stopped: %r", e)

    async def close(self) -> None:
        if self.busy:
            self._task.cancel()


def journal_now(ctx) -> tuple[list[dict], dict, datetime | None]:
    """The saves written down, the file as it is (masked) and when it was last written."""
    from src.admin.config_editor import MAX_JOURNAL_ENTRIES

    editor = ctx.config_editor
    try:
        modified_at = datetime.fromtimestamp(editor.modified_at(), timezone.utc)
    except OSError:
        modified_at = None
    try:
        current = editor.load()["config"]
    except Exception:
        current = {}
    return editor.history(MAX_JOURNAL_ENTRIES), current, modified_at


async def pending_by_service(ctx, max_age: float = 0) -> dict[str, dict] | None:
    """
    Each running service's saved changes it does not run with, by the
    containers' starts the control service gives; None when it cannot say.
    """
    control = ctx.service_control
    if control is None or not control.available:
        return None
    try:
        found = await control.services(max_age=max_age)
    except Exception:
        return None
    history, current, modified_at = await run_in_threadpool(journal_now, ctx)
    return {
        entry["service"]: pending_changes(entry["service"], moment(entry.get("started_at")), history, current, modified_at)
        for entry in found if entry.get("running")
    }


def _read_config(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as config_file:
            return json.load(config_file)
    except (OSError, ValueError):
        return {}


def _intents(beat: dict | None) -> tuple[tuple[str, ...], int]:
    """The sales the position monitor is still trying and the positions Saxo lists, from a fresh heartbeat."""
    from src.heartbeat import HEARTBEAT_STALE_SECONDS

    beat_at = moment((beat or {}).get("beat_at"))
    if beat_at is None or (datetime.now(timezone.utc) - beat_at).total_seconds() > HEARTBEAT_STALE_SECONDS:
        return (), 0
    figures = beat.get("figures") or {}
    intents = tuple(str(intent.get("position_id")) for intent in figures.get("close_intents") or [])
    return intents, int(figures.get("positions_listed") or 0)


async def situation_of(ctx, now: float | None = None) -> Situation:
    """What the guards read, from the database, the heartbeats, the file on disk and the admin's own work."""
    from src.admin.ta_rules import LIVE, RuleStore

    now = time.time() if now is None else now
    config = await run_in_threadpool(_read_config, ctx.settings.config_path)
    try:
        positions_open = len((await ctx.queries.list_positions(status="Open"))["items"])
    except Exception:
        positions_open = None
    try:
        beats = await ctx.queries.service_heartbeats()
    except Exception:
        beats = {}
    intents, listed = _intents(beats.get("position_monitor"))
    live_rules = 0
    if ctx.ta_scripts is not None:
        try:
            rules = (await run_in_threadpool(RuleStore(ctx.ta_scripts).load))["rules"]
            live_rules = sum(1 for rule in rules if rule.get("mode") == LIVE)
        except Exception:
            live_rules = 0
    return Situation(
        positions_open=positions_open,
        positions_listed=listed,
        close_intents=intents,
        market_open=market_is_open(config, now) if config else True,
        live_rules=live_rules,
        backtests_running=ctx.backtests.under_way if ctx.backtests is not None else 0,
        study_running=bool(ctx.studies is not None and ctx.studies.busy),
        scheduler_job_near=scheduler_job_near(config, now) if config else None,
    )


class Readiness:
    """What says a service restarted is at work again, read through the admin's context."""

    def __init__(self, ctx):
        self._ctx = ctx

    async def before(self, service: str):
        """What is to be told apart after the restart: the scheduler's last line of its lifecycle."""
        if service != "scheduler":
            return None
        return await run_in_threadpool(self._scheduler_line)

    def _scheduler_line(self) -> dict | None:
        from src.admin.health import LOG_OBSERVED_SERVICES
        from src.admin.logs import last_lifecycle_event

        observed = LOG_OBSERVED_SERVICES["scheduler"]
        return last_lifecycle_event(self._ctx.settings.log_path, observed["log"], observed["markers"])

    async def _consumers(self, queue: str) -> int:
        stats = {found["name"]: found for found in await self._ctx.services.queue_stats()}
        return int((stats.get(queue) or {}).get("consumers") or 0)

    async def ready(self, service: str, started_at: datetime | None, before) -> tuple[bool, str]:
        """Whether ``service``, running again since ``started_at``, is at work, and what says so or not."""
        from src.admin.services import OPS_QUEUE, TELEGRAM_QUEUE

        ctx = self._ctx

        def since(value) -> bool:
            found = moment(value)
            return found is not None and (started_at is None or found >= started_at)

        if service in ("trader", "position_monitor"):
            beat = (await ctx.queries.service_heartbeats()).get(service)
            if beat is None or not since(beat.get("started_at")):
                return False, "No heartbeat of its new start yet"
            if service == "position_monitor" and not await self._consumers(OPS_QUEUE):
                return False, f"No consumer on {OPS_QUEUE} yet"
            return True, "Beating again"
        if service == "web_server":
            signals = await ctx.services.latest_signals()
            return True, f"Answering, {_count(len(signals), 'signal')} in store"
        if service == "watchlist_manager":
            if not ctx.services.watchlist_enabled:
                return True, "Running; not used by the trader (disabled in the configuration)"
            await ctx.services.watchlist_health()
            return True, "Answering"
        if service == "telegram":
            return (True, f"Consuming {TELEGRAM_QUEUE}") if await self._consumers(TELEGRAM_QUEUE) else (
                False, f"No consumer on {TELEGRAM_QUEUE} yet")
        if service == "scheduler":
            line = await run_in_threadpool(self._scheduler_line)
            if line and line.get("event") == "ready" and line != before:
                return True, f"Running since {line['timestamp']} (from its log)"
            return False, "No line of its new start in its log yet"
        if service == "price_recorder":
            status = await ctx.queries.price_recorder_status()
            return (True, "Recording") if status and since(status.get("ts")) else (False, "No bar recorded since its start yet")
        if service == "capital_feed":
            state = await ctx.queries.capital_feed_state()
            if not state or not since(state.get("started_at")):
                return False, "No state written since its start yet"
            return True, f"{state.get('state')}: {state.get('detail') or ''}".rstrip(": ")
        if service == "ta_engine":
            state = await ctx.queries.ta_engine_state()
            if not state or not since(state.get("started_at")):
                return False, "No state written since its start yet"
            return True, f"{state.get('state')}: {state.get('detail') or ''}".rstrip(": ")
        return True, "Running"
