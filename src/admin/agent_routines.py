"""
The AI agent's routines: research tasks that come back by themselves, each
trading day once its hours are over or once a week at a day and time of the
trading timezone, with a goal, a budget (each part at most the settings'
max_task_*), a model and an effort. Kept in ``routines.json`` beside the
conversations, written whole with a revision (a change made meanwhile is never
overwritten), each with its last runs.

They start only while the market is closed by the live rules, one a look,
never while their last task still works; the agent's pause, its key and the
month's budget hold them back as they hold a task asked by hand, and they are
asked again at the next look. "Run now" starts one at any hour. A routine's
task is told on Telegram at its end, when it says so, by the agent's teller.
It lives outside src/admin/agent/, which never reaches the notifier.

The research autopilot (kind ``autopilot``, a template the operator saves once): each turn it takes the research
queue's next hypothesis (src/admin/research_queue.py) and asks a task to work it as an experiment of its own, the
hypothesis named in its goal and kept with the task; with the queue empty, a cheaper task that runs nothing and files
up to three hypotheses from the forward checks and the newest autopsies. Each task that ends (``task_ended``, told by
the agent: AgentService.on_task_ended) settles the hypothesis it took, with the experiment it made, and files the next
hypotheses it left; a task refused at its start puts its hypothesis back; what a restart left taken is settled at the
next look.
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
import threading
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from starlette.concurrency import run_in_threadpool

from src.admin.agent.store import UnknownConversation, stamp
from src.admin.agent.transcript import UNDER_WAY
from src.admin.agent_jobs import read_config
from src.admin.agent_telegram import ROUTINE
from src.admin.downloads import market_is_open
from src.admin.errors import InvalidRequest, NotPossible
from src.release.guards import trading_hours, trading_timezone

logger = logging.getLogger(__name__)

FILE = "routines.json"
TICK_SECONDS = 5 * 60
READY_SECONDS = 10
MAX_NAME = 80
MAX_GOAL = 8000
# Each routine's last runs kept.
KEPT_RUNS = 10
ROUTINE_ID = re.compile(r"^r[0-9a-f]{8}$")
TRADING_DAY, WEEK = "trading_day", "week"
_TIME = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


# What a routine is besides a goal on a schedule: the research autopilot works the research queue.
AUTOPILOT = "autopilot"
KINDS = (AUTOPILOT,)

# Routines ready to add (GET /api/agent/routines/templates): the operator changes what they like before saving.
# Their goals name the agent's tools, so that a template is offered only while those tools are given.
AUTOPILOT_GOAL = """\
Work the research queue's next hypothesis, given below, as an experiment of its own.
1. Read what the research did so far, given before this goal: its family's trials, its kept conclusions and how they \
held on later days. A family tried long with nothing that held needs a reason that is new, said before any run.
2. exp_new with the hypothesis, its family, its criteria and the days it came from, as given: change them only to \
make it refutable, and say why.
3. Test it within the task's budget: a day on chart_view before a change, backtest_start against random entries, a \
sweep only with walk-forward on.
4. Judge it by the bar: neighbours and stress before keeping anything, a sweep's plateau, the autopsy before a change \
made for losses.
5. exp_conclude: kept (naming its candidate: a forward check then judges it on later days), rejected or \
inconclusive, with what it rests on.
6. End with task_done: your conclusion, and up to three next hypotheses the results point to, each refutable, with \
its family, its criteria and the days it came from. Never propose to take a rule live."""
# With the queue empty: a cheaper look that runs nothing and fills it.
FALLBACK_GOAL = """\
The research queue is empty: find what deserves an experiment next, running nothing.
1. exp_get: the forward checks (which kept conclusions held on later days, which broke), each family's track record \
and the challenger board (each kept conclusion against the live configuration on the same days).
2. The two or three most traded runs of the newest experiments (backtest_list, then backtest_result part autopsy): \
what separated their winners from their losers, and held on their later days.
3. End with task_done: up to three next hypotheses, each refutable, with its family, the criteria a run must meet \
and the days it came from; none rather than a weak one."""
FALLBACK_BUDGET = {"usd": 0.5, "backtests": 0, "hours": 1.0}
LUXALGO_SCOUT = "luxalgo_scout"
SCOUT_GOAL = """\
Scout LuxAlgo's Library for indicators worth testing on WATA.
1. Read the 24 newest indicators (luxalgo_call library_list_indicators, newest first), then what WATA already \
knows of them (luxalgo_known, 20 slugs a call). Leave out those with a verdict of the last 60 days, those WATA's \
check found not to compile, strategies, and those that cannot suit intraday bars of the Nasdaq 100 (daily or \
weekly only, made for another market's session, crypto only).
2. Shortlist three at most for the operator's style, as your memory's preferences say.
3. Take the best one or two whole with luxalgo_save_script (refused when LuxAlgo gives a part of it: say so and go \
on), and count their signals on the last five recorded days with pine_signals, adding alertcondition() lines in a \
version of your own when they have none.
4. Backtest the most promising within the task's budget, as an experiment of the family luxalgo-scout, and judge it \
by the bar.
5. Set a verdict on each indicator you judged (luxalgo:<slug>): negative when it cannot work on WATA or fails \
the bar, mixed when it deserves more days, positive only when it clears the bar. None on one you could not take \
whole: the next scout would leave it out for 60 days.
6. End with task_done, and answer in eight lines at most: what you looked at, what you took, what its runs said, \
with links. Never propose to take a rule live."""
TEMPLATES = {
    AUTOPILOT: {
        "name": "Research autopilot", "kind": AUTOPILOT, "goal": AUTOPILOT_GOAL,
        # Each trading day once its hours are over: the day's results in, the market closed.
        "schedule": {"every": TRADING_DAY},
        "budget": {"usd": 1.5, "backtests": 4, "hours": 3.0}, "telegram": True,
    },
    LUXALGO_SCOUT: {
        "name": "LuxAlgo scout", "goal": SCOUT_GOAL,
        # Saturday morning: the week's indicators, the market closed.
        "schedule": {"every": WEEK, "weekday": 5, "time": "10:00"},
        "budget": {"usd": 2.0, "backtests": 2, "hours": 1.5}, "telegram": True,
    },
}


def templates_for(settings, *, luxalgo: bool) -> list[dict]:
    """The templates this admin's agent can run (the LuxAlgo scout with LuxAlgo's tools), each budget held to what a
    task may be given."""
    found = []
    for key, template in TEMPLATES.items():
        if key == LUXALGO_SCOUT and not luxalgo:
            continue
        budget = template["budget"]
        found.append({**template, "key": key, "budget": {
            "usd": min(budget["usd"], settings.max_task_usd), "backtests": min(budget["backtests"],
                                                                                settings.max_task_backtests),
            "hours": min(budget["hours"], settings.max_task_hours)}})
    return found


class RoutineConflict(Exception):
    """The routines changed since they were read."""


def checked_schedule(schedule) -> dict:
    """Each trading day once its hours are over, or a weekday (0 Monday) at HH:MM of the trading timezone."""
    if not isinstance(schedule, dict) or schedule.get("every") not in (TRADING_DAY, WEEK):
        raise InvalidRequest("A routine comes back each trading day, or each week")
    if schedule["every"] == TRADING_DAY:
        return {"every": TRADING_DAY}
    weekday, at = schedule.get("weekday"), schedule.get("time")
    if isinstance(weekday, bool) or not isinstance(weekday, int) or not 0 <= weekday <= 6:
        raise InvalidRequest("A weekly routine names its weekday, 0 for Monday to 6 for Sunday")
    if not isinstance(at, str) or not _TIME.match(at):
        raise InvalidRequest("A weekly routine names its time, HH:MM")
    return {"every": WEEK, "weekday": weekday, "time": at}


def due_moment(schedule: dict, config: dict, now: float) -> float | None:
    """
    When the routine's latest turn came, at or before ``now``: the end of the
    newest weekday's trading hours that is over, or this week's (else last
    week's) weekday and time in the trading timezone. Pure.
    """
    if schedule["every"] == TRADING_DAY:
        zone, _, end_hour = trading_hours(config)
        local = datetime.fromtimestamp(now, ZoneInfo(zone))
        day = local.date()
        for _ in range(8):
            end = datetime(day.year, day.month, day.day, end_hour, tzinfo=ZoneInfo(zone))
            if day.weekday() < 5 and end.timestamp() <= now:
                return end.timestamp()
            day -= timedelta(days=1)
        return None
    zone = ZoneInfo(trading_timezone(config))
    local = datetime.fromtimestamp(now, zone)
    hour, minute = (int(part) for part in schedule["time"].split(":"))
    day = local.date() - timedelta(days=(local.weekday() - schedule["weekday"]) % 7)
    moment = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone)
    if moment.timestamp() > now:
        moment -= timedelta(days=7)
    return moment.timestamp()


class RoutineStore:
    def __init__(self, root: str, clock=time.time):
        self._path = os.path.join(root, FILE)
        self._clock = clock
        self._lock = threading.Lock()

    def _read(self) -> list[dict]:
        try:
            with open(self._path, encoding="utf-8") as kept:
                found = json.load(kept).get("routines")
        except (OSError, ValueError, AttributeError):
            return []
        return found if isinstance(found, list) else []

    @staticmethod
    def _revision(routines: list[dict]) -> str:
        return hashlib.sha256(json.dumps(routines, sort_keys=True).encode()).hexdigest()[:16]

    def load(self) -> dict:
        routines = self._read()
        return {"revision": self._revision(routines), "routines": routines}

    def _write(self, routines: list[dict]) -> str:
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        partial = f"{self._path}.part"
        with open(partial, "w", encoding="utf-8") as out_file:
            json.dump({"routines": routines}, out_file, ensure_ascii=False, indent=1)
        os.replace(partial, self._path)
        return self._revision(routines)

    def _changed(self, revision: str | None, change) -> dict:
        with self._lock:
            routines = self._read()
            if revision is not None and revision != self._revision(routines):
                raise RoutineConflict("The routines changed since this page read them: read them again")
            result = change(routines)
            revision = self._write(routines)
        return {"revision": revision, **result}

    @staticmethod
    def find(routines: list[dict], routine_id: str) -> dict:
        if not isinstance(routine_id, str) or not ROUTINE_ID.match(routine_id):
            raise InvalidRequest(f"No routine {routine_id!r}")
        found = next((routine for routine in routines if routine["id"] == routine_id), None)
        if found is None:
            raise InvalidRequest(f"No routine {routine_id!r}")
        return found

    def save(self, fields: dict, revision: str, by: str | None, routine_id: str | None = None,
             most: int | None = None) -> dict:
        """A routine made, or changed (``routine_id``), its fields checked by the caller (checked_fields)."""
        def change(routines: list[dict]) -> dict:
            if routine_id is None:
                if most is not None and len(routines) >= most:
                    raise NotPossible(f"WATA keeps {most} routines at most (max_routines): remove one first")
                routine = {"id": f"r{secrets.token_hex(4)}", **fields, "created_at": stamp(self._clock()),
                           "created_by": by, "runs": []}
                routines.append(routine)
            else:
                routine = self.find(routines, routine_id)
                routine.update(fields, updated_at=stamp(self._clock()))
            return {"routine": routine}
        return self._changed(revision, change)

    def remove(self, routine_id: str, revision: str) -> dict:
        def change(routines: list[dict]) -> dict:
            routines.remove(self.find(routines, routine_id))
            return {}
        return self._changed(revision, change)

    def ran(self, routine_id: str, run: dict) -> None:
        """A routine's task started (or why it was not): kept with it, the newest first."""
        def change(routines: list[dict]) -> dict:
            routine = self.find(routines, routine_id)
            routine["runs"] = [run, *(routine.get("runs") or [])][:KEPT_RUNS]
            return {}
        self._changed(None, change)


def checked_fields(given: dict, settings) -> dict:
    """What a routine is made of, as the page sends it, bounded as a task asked by hand is."""
    name = " ".join(str(given.get("name") or "").split())
    goal = str(given.get("goal") or "").strip()
    if not name or len(name) > MAX_NAME:
        raise InvalidRequest(f"A routine has a name, {MAX_NAME} characters at most")
    if not goal or len(goal) > MAX_GOAL:
        raise InvalidRequest(f"A routine has a goal, {MAX_GOAL:,} characters at most")
    budget = given.get("budget")
    if not isinstance(budget, dict):
        raise InvalidRequest("A routine's budget is its dollars, backtests and hours")
    usd, backtests, hours = budget.get("usd"), budget.get("backtests"), budget.get("hours")
    if isinstance(usd, bool) or not isinstance(usd, (int, float)) or not 0.5 <= usd <= settings.max_task_usd:
        raise InvalidRequest(f"A routine's dollars are from $0.50 to ${settings.max_task_usd:.2f} (max_task_usd)")
    if isinstance(backtests, bool) or not isinstance(backtests, int) or not 0 <= backtests <= settings.max_task_backtests:
        raise InvalidRequest(f"A routine's backtests are from 0 to {settings.max_task_backtests} (max_task_backtests)")
    if isinstance(hours, bool) or not isinstance(hours, (int, float)) or not 0.5 <= hours <= settings.max_task_hours:
        raise InvalidRequest(f"A routine's hours are from 0.5 to {settings.max_task_hours:g} (max_task_hours)")
    kind = given.get("kind") or None
    if kind is not None and kind not in KINDS:
        raise InvalidRequest(f"A routine is a goal on a schedule, or one of {', '.join(KINDS)}")
    return {
        "name": name, "goal": goal, "schedule": checked_schedule(given.get("schedule")), "kind": kind,
        "budget": {"usd": round(float(usd), 2), "backtests": backtests, "hours": float(hours)},
        "model": given.get("model") or None, "effort": given.get("effort") or None,
        "telegram": bool(given.get("telegram", True)), "enabled": bool(given.get("enabled", True)),
    }


def hypothesis_said(item: dict) -> str:
    """The research queue's hypothesis, as an autopilot task's goal names it."""
    said = [f"The hypothesis ({'the operator' if item.get('by') == 'operator' else 'filed by an earlier task'}, "
            f"{item['id']}): {item['title']}", item["hypothesis"], f"Its family: {item['family']}"]
    if item.get("rationale"):
        said.append(f"What it comes from: {item['rationale']}")
    if item.get("criteria"):
        said.append("What a run must show: " + "; ".join(
            f"{criterion['figure']} {'at least' if criterion['bound'] == 'at_least' else 'at most'} {criterion['value']}"
            for criterion in item["criteria"]))
    if item.get("motivated_by"):
        said.append(f"The days it came from: {', '.join(item['motivated_by'])}")
    return "\n".join(said)


def fallback_budget(budget: dict) -> dict:
    """An empty queue's look: no backtest, half a dollar and an hour at most of the routine's own."""
    return {"usd": min(FALLBACK_BUDGET["usd"], budget["usd"]), "backtests": 0,
            "hours": min(FALLBACK_BUDGET["hours"], budget["hours"])}


class Routines:
    def __init__(self, *, agent, store: RoutineStore, config_path: str, teller=None, clock=time.time, queue=None,
                 journal=None, tired=None):
        self._agent = agent
        self.store = store
        self._config_path = config_path
        self._teller = teller
        self._clock = clock
        # The research queue the autopilot works (src/admin/research_queue.py); the experiment journal its tasks write
        # (src/research/experiments.py); () -> the families tried long with nothing that held, which wait.
        self._queue = queue
        self._journal = journal
        self._tired = tired

    async def keep(self) -> None:
        """Until cancelled: a look every five minutes, once the agent's tools started."""
        while True:
            if not self._agent.ready:
                await asyncio.sleep(READY_SECONDS)
                continue
            try:
                await self.catch_up()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"The AI agent's routines could not catch up: {e}")
            await asyncio.sleep(TICK_SECONDS)

    def _working(self, routine: dict) -> bool:
        last = (routine.get("runs") or [{}])[0].get("conversation")
        if not last:
            return False
        try:
            said = self._agent.conversation(last)
        except Exception:
            return False
        return (said.get("task") or {}).get("state") == "working" or said.get("status") in UNDER_WAY

    async def catch_up(self, now: float | None = None) -> str | None:
        """What a restart left taken settled; then the first routine due started, the market closed: its conversation,
        or None."""
        try:
            await run_in_threadpool(self.settle_taken)
        except Exception as e:
            logger.warning(f"The research queue's hypotheses taken could not be settled: {e}")
        now = self._clock() if now is None else now
        config = await run_in_threadpool(read_config, self._config_path)
        if market_is_open(config, now):
            return None
        for routine in (await run_in_threadpool(self.store.load))["routines"]:
            if not routine.get("enabled", True):
                continue
            moment = due_moment(routine["schedule"], config, now)
            last = (routine.get("runs") or [{}])[0]
            if moment is None or (last.get("turn") or "") >= stamp(moment):
                continue
            if await run_in_threadpool(self._working, routine):
                continue
            started = await self.start(routine, turn=stamp(moment))
            if started:
                return started
        return None

    async def start(self, routine: dict, turn: str | None = None) -> str | None:
        """
        A routine's task started now (its turn, or Run now): its conversation, or None when held back. The autopilot's
        works the research queue's next hypothesis, taken before the task is asked and put back if it is refused; with
        the queue empty, a look that runs nothing and fills it.
        """
        goal, budget, item = routine["goal"], routine["budget"], None
        autopilot = routine.get("kind") == AUTOPILOT and self._queue is not None
        if autopilot:
            tired = await run_in_threadpool(self._tired) if self._tired is not None else set()
            item = await run_in_threadpool(self._queue.take_next, tired)
            # Its first line names the hypothesis: the task's title, on Activity and on Telegram.
            goal, budget = ((f"The research queue's next hypothesis: {item['title']}\n\n{routine['goal']}\n\n"
                             f"{hypothesis_said(item)}", budget) if item is not None
                            else (FALLBACK_GOAL, fallback_budget(budget)))
        try:
            meta = await self._agent.create_task(goal, None, f"routine:{routine['id']}", budget, routine.get("model"),
                                                 routine.get("effort"), **({"hypothesis": item} if item else {}))
        except NotPossible as e:
            # Paused, without a key, the month spent: asked again at the next look, its hypothesis waiting again.
            logger.info(f"The routine {routine['name']} waits: {e}")
            if item is not None:
                await run_in_threadpool(self._queue.put_back, item["id"])
            return None
        except BaseException:
            if item is not None:
                await run_in_threadpool(self._queue.put_back, item["id"])
            raise
        if item is not None:
            await run_in_threadpool(self._queue.started, item["id"], meta["id"])
        run = {"at": stamp(self._clock()), "conversation": meta["id"], "turn": turn or stamp(self._clock())}
        if autopilot:
            run["hypothesis"] = {"id": item["id"], "title": item["title"]} if item is not None else None
        await run_in_threadpool(self.store.ran, routine["id"], run)
        if routine.get("telegram", True) and self._teller is not None:
            await run_in_threadpool(self._teller.add, meta["id"], ROUTINE, label=routine["name"])
        return meta["id"]

    # ── the research queue ──

    def _experiment_of(self, conversation_id: str) -> dict | None:
        """The newest experiment a task opened (``made_via`` its conversation), or None."""
        if self._journal is None:
            return None
        made = [experiment for experiment in self._journal.experiments()
                if experiment.get("made_via") == f"agent:{conversation_id}"]
        return made[-1] if made else None

    def task_ended(self, conversation_id: str, task: dict) -> None:
        """
        A research task ended (AgentService.on_task_ended, or seen ended at a look): the queue's hypothesis it took
        settled with the experiment it made and what it concluded, and the hypotheses it left filed as its own.
        Idempotent: a hypothesis is settled once, one filed again by the same conversation is the one already there.
        """
        if self._queue is None:
            return
        conclusion = task.get("conclusion") or {}
        experiment = self._experiment_of(conversation_id)
        said = (conclusion.get("conclusion") if task.get("state") == "done"
                else f"The task ended before a conclusion: {task.get('ended_because') or 'ended'}")
        self._queue.settle(conversation_id, done=True, experiment=(experiment or {}).get("id"), conclusion=said)
        today = datetime.fromtimestamp(self._clock(), ZoneInfo("UTC")).date() + timedelta(days=1)
        for hypothesis in conclusion.get("next_hypotheses") or []:
            try:
                self._queue.add(hypothesis, f"agent:{conversation_id}", today=today, conversation=conversation_id)
            except (InvalidRequest, NotPossible) as e:
                logger.info(f"A hypothesis of task {conversation_id} was not filed: {e}")

    def settle_taken(self) -> None:
        """What a restart left: a hypothesis taken whose task never started waits again; one whose task ended (or was
        removed) is settled."""
        if self._queue is None:
            return
        self._queue.stale_starts()
        for item in self._queue.taken():
            conversation = item.get("conversation")
            if not conversation:
                continue
            try:
                said = self._agent.conversation(conversation)
            except UnknownConversation:
                # Removed with its conversation: its work is gone, it waits again.
                self._queue.put_back(item["id"])
                continue
            task = said.get("task") or {}
            if task.get("state") in ("done", "ended"):
                self.task_ended(conversation, task)

    async def run_now(self, routine_id: str) -> str:
        routine = self.store.find((await run_in_threadpool(self.store.load))["routines"], routine_id)
        if await run_in_threadpool(self._working, routine):
            raise NotPossible("Its last task still works: stop it first, or wait for its end")
        started = await self.start(routine)
        if started is None:
            raise NotPossible("The agent cannot start a task now: paused, without a key, or the month's budget spent")
        return started


def next_turn(schedule: dict, config: dict, now: float) -> str | None:
    """When a routine next comes back, for the pages (the market's close then may still hold it back): a week's
    day and time on the trading timezone's clock, whichever way it changed in between."""
    if schedule["every"] == WEEK:
        zone = ZoneInfo(trading_timezone(config))
        local = datetime.fromtimestamp(now, zone)
        hour, minute = (int(part) for part in schedule["time"].split(":"))
        day = local.date() + timedelta(days=(schedule["weekday"] - local.weekday()) % 7)
        moment = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone)
        if moment.timestamp() <= now:
            moment += timedelta(days=7)
        return stamp(moment.timestamp())
    zone, _, end_hour = trading_hours(config)
    local = datetime.fromtimestamp(now, ZoneInfo(zone))
    day: date = local.date()
    for _ in range(8):
        end = datetime(day.year, day.month, day.day, end_hour, tzinfo=ZoneInfo(zone))
        if day.weekday() < 5 and end.timestamp() > now:
            return stamp(end.timestamp())
        day += timedelta(days=1)
    return None


def routines_of(ctx, teller) -> Routines:
    """The routines over the admin's context, the research queue's settling told of each task that ends."""
    from src.admin.research_queue import tired_families
    from src.admin.track_record import record
    from src.research.experiments import server_journal

    journal = server_journal(ctx.settings.backtests_path)

    def tired() -> set[str]:
        forward = getattr(ctx, "forward", None)
        return tired_families(record(journal.experiments(), forward.of_experiments() if forward is not None else {}))

    routines = Routines(agent=ctx.agent, store=RoutineStore(ctx.settings.agent_path),
                        config_path=ctx.settings.config_path, teller=teller,
                        queue=getattr(ctx, "research_queue", None), journal=journal, tired=tired)
    ctx.agent.on_task_ended(routines.task_ended)
    return routines
