"""
The AI agent at work in the admin (``ctx.agent``): conversations asked from the
pages, their requests queued and worked on ``max_running`` at a time by an
application task, so that a request goes on when its page is closed. A request
left under way by a restart (a deploy, a configuration saved) is taken up again
at the next start, from what its files kept.

What it says: on the conversation's own stream, each step and each piece of
text as it comes (for the page that follows it); on the admin's live events,
``agent_finished`` once a request is over, whatever its end. Nothing new starts
while the operator's pause is on, or once the month's estimated spending
reached its budget.

A research task is a conversation the agent works on alone: once one of its
requests ends, the next is queued with what is spent and what is left, each
within the task's budget, until the agent says it is done (task_done), its
dollars or hours are spent, it was stopped, or nothing new may start.

With its memory (src/admin/agent_memory.py), a conversation's first request
opens with the memory's list, and each later one with what the operator or
other conversations changed in it since (``memory_seq`` of its meta).
"""

import asyncio
import difflib
import json
import logging
import os
import re
import time
from contextlib import contextmanager
from dataclasses import replace
from datetime import date, datetime, timezone

from starlette.concurrency import run_in_threadpool

from src.admin.agent import provider as claude
from src.admin.agent import transcript
from src.admin.agent.loop import (
    CLEAR_FROM_TOKENS, DECLINED, DONE, FAILED, KEEP_TOOL_USES, STOPPED, RequestRun, Used, counted_params, digest_of,
    request_params,
)
from src.admin.agent.prompts import (
    limits_note, memory_changes, memory_index, subject_text, task_going_on, task_opening,
)
from src.admin.agent.proposals import Proposals
from src.admin.agent.settings import EFFORTS
from src.admin.agent.settings import MODELS as CHOICES
from src.admin.agent.store import ConversationStore, UnknownConversation, month_of, stamp
from src.admin.agent.tools import AgentTools
from src.admin.agent.uploads import IMAGE, PDF, UnknownFile, Uploads
from src.admin.errors import InvalidRequest, NotPossible
from src.admin.ta_rules import RuleNotFound, RuleStore

logger = logging.getLogger(__name__)

MAX_PROMPT = 8000
MAX_TITLE = 120
# A page's stream that stopped reading: its events are dropped, then it is told to read the conversation again.
STREAM_QUEUE_SIZE = 500
# How often old conversations are looked for, and the Models API asked again of each model, besides the start.
PRUNE_EVERY_SECONDS = 6 * 3600
# Token counts kept for the context window's view: one per conversation, model and length of its transcript.
COUNTS_KEPT = 64
# What a token count is given to end on: a request is counted as the operator's next one would be sent.
_NEXT = {"type": "text", "text": "."}
SUBJECT_KINDS = ("run", "script", "position", "signal", "day", "incident", "briefing", "rule", "change", "draft",
                 "luxalgo")
RUN, SCRIPT, POSITION, SIGNAL, DAY, INCIDENT, BRIEFING, RULE, CHANGE, DRAFT, LUXALGO = SUBJECT_KINDS
# What the operator attaches to a request: a file of their computer (src/admin/agent/uploads.py), or one of these
# (the day ahead, a change and a draft are the admin's own subjects); five at most in a conversation, the subject of
# the page it was asked from counted.
FILE = "file"
ATTACHABLE = (RUN, SCRIPT, POSITION, SIGNAL, DAY, INCIDENT, RULE, LUXALGO)
MAX_ATTACHED = 5
# What a change reviewed before it is saved may hold, as shared; a draft's diff from its version, as shared.
MAX_CHANGE_CHARS = 64_000
MAX_DIFF_CHARS = 30_000
_REVIEW_ID = re.compile(r"^[0-9a-f]{8}$")
# The log an incident of each entry of the health is read in (src/admin/logs.py LOG_SERVICES).
LOG_OF = {
    "trader": "wata-trader-async", "saxo": "wata-trader-async", "position_monitor": "wata-position-monitor",
    "web_server": "wata-api", "scheduler": "wata-scheduler", "telegram": "wata-telegram",
    "watchlist_manager": "wata-watchlist-manager", "capital_feed": "wata-capital-feed", "ta_engine": "wata-ta-engine",
    "admin": "wata-admin", "live_drift": "wata-admin", "backups": "wata-admin", "account_guard": "wata-trader-async",
}
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MOMENT = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
_HEALTH_ID = re.compile(r"^[a-z_]{2,32}$")
# An indicator of LuxAlgo's Library, by its slug (src/admin/luxalgo.py SLUG).
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,120}$")
# What a LuxAlgo indicator's write-up shares at most: the rest is LuxAlgo's to give again.
LUXALGO_WRITE_UP_CHARS = 6_000
# What a conversation about a day or a signal reads of the day's signals and trades at most.
SHARED_ROWS = 200
# What an incident is shared of the changes before its moment.
INCIDENT_CHANGES_HOURS = 2
# What a morning briefing is shared of what changed since the day before, and of the day before's debrief.
BRIEFING_CHANGES_HOURS = 18
BRIEFING_DEBRIEF_CHARS = 3000
FEEDBACK = ("like", "dislike", None)
# A research task: working, done by the agent's word (task_done), or ended for another reason (said with it).
TASK_WORKING, TASK_DONE, TASK_ENDED = "working", "done", "ended"
# Requests one task makes at most, whatever is left of its budget: a task that never says it is done ends there.
MAX_TASK_REQUESTS = 30
# Less than this left of a task's dollars: no request is started, it could do nothing worth its cost.
TASK_LEAST_USD = 0.10
# A question a task asks the operator (ask_operator): its wait does not count in the task's hours, two days at most,
# looked at this often.
QUESTION_WAIT_HOURS = 48
QUESTION_LOOK_SECONDS = 300
MAX_ANSWER = 2000


class AgentUnavailable(Exception):
    """The agent cannot work: its tools could not be started."""


class AgentService:
    def __init__(self, *, settings, store, provider, tools, proposals, backtests, scripts, key, publish,
                 clock=time.time, queries=None, memory=None, verdicts=None, uploads=None):
        self.settings = settings
        self.store = store
        # The files chosen and not yet sent, staged beside the conversations.
        self.uploads = uploads if uploads is not None else Uploads(os.path.join(store.root, "uploads"), clock=clock)
        # Its verdicts shown on the pages (src/admin/agent/verdicts.py): they go with their conversations.
        self.verdicts = verdicts
        # Its memory (src/admin/agent_memory.py): None without it (admin.ai_agent.memory).
        self.memory = memory
        # The research tools' named queries (src/admin/mcp_server/facade.py ReadQueries): a position, a day's journal.
        self._queries = queries
        self._provider = provider
        self._tools = tools
        self.proposals = proposals
        self._backtests = backtests
        self._scripts = scripts
        # Whether a Claude API key is set: opened from the credentials, never kept.
        self._key = key
        self._publish = publish
        self._clock = clock
        self._queue: asyncio.Queue = asyncio.Queue()
        # The conversations in the queue: one is never queued twice (a start's recovery, a request asked meanwhile).
        self._queued: set[str] = set()
        self._stopping: set[str] = set()
        self._working: set[str] = set()
        self._pending: dict[str, dict] = {}
        self._subscribers: dict[str, set[asyncio.Queue]] = {}
        self._started = False
        self.broken: str | None = None
        # What the Models API said of each model for the key (window, efforts, whether the key may use it).
        self._models: dict[str, dict] = {}
        # Token counts: of the system prompt and tools per model, of each conversation as it stands.
        self._base_counts: dict[tuple, dict] = {}
        self._counts: dict[tuple, dict] = {}
        self._refreshing: asyncio.Task | None = None
        # The research tasks that wait for the operator's answer to their question.
        self._questions: set[str] = set()

    @property
    def ready(self) -> bool:
        """Its tools started (at the admin's start, in run): a conversation may be created."""
        return self._started and not self.broken

    # ── the application task ──

    async def run(self) -> None:
        try:
            await self._tools.start()
        except Exception as e:
            self.broken = f"The research tools could not be started: {e}"
            logger.exception("The AI agent could not start its tools")
            return
        try:
            await run_in_threadpool(self._recover)
        except Exception:
            logger.exception("The AI agent could not take up again what its last stop left under way")
        # Ready (a conversation may be created: the daily debrief waits for it) once what a stop left is queued again.
        self._started = True
        workers = [asyncio.create_task(self._worker()) for _ in range(self.settings.max_running)]
        workers.append(asyncio.create_task(self._questions_kept()))
        try:
            while True:
                await run_in_threadpool(self._prune)
                await self.refresh_models()
                await asyncio.sleep(PRUNE_EVERY_SECONDS)
        finally:
            for worker in workers:
                worker.cancel()

    def _recover(self) -> None:
        """
        The requests a stop left queued or under way: queued again, to go on from
        what was kept; and a research task stopped between two of its requests,
        its next one asked.
        """
        for meta in reversed(self.store.metas()):
            if (meta.get("task") or {}).get("question") and meta["task"].get("state") == TASK_WORKING:
                # Waiting for the operator's answer: nothing to take up.
                self._questions.add(meta["id"])
                continue
            if meta.get("status") in transcript.UNDER_WAY and meta["id"] not in self._queued:
                self.store.update_meta(meta["id"], status="queued")
                self._enqueue(meta["id"], True)
            elif ((meta.get("task") or {}).get("state") == TASK_WORKING
                  and meta.get("status") not in (FAILED, *transcript.UNDER_WAY)):
                # Between two of its requests only: one queued or under way goes on from its own end. A task created
                # while this reads was taken for one stopped between two, and its next request asked before its first.
                model, effort = self._kept_choice(meta)
                self._task_went_on(meta["id"], meta["request"], DONE, model, effort)

    def _enqueue(self, conversation_id: str, resumed: bool) -> None:
        self._queued.add(conversation_id)
        self._queue.put_nowait((conversation_id, resumed))

    @contextmanager
    def _queuing(self, conversation_id: str):
        """
        Around writing that a conversation is queued: it is counted as queued
        before its meta says so. The start's recovery reads the metas in a
        thread; one queued without being counted yet was queued a second time
        there, and its request asked, and paid for, twice.
        """
        self._queued.add(conversation_id)
        try:
            yield
        except BaseException:
            self._queued.discard(conversation_id)
            raise

    def _prune(self) -> None:
        try:
            self.uploads.prune()
        except OSError as e:
            logger.warning(f"The AI agent's files waiting to be sent could not be pruned: {e}")
        if self.memory is not None:
            try:
                self.memory.prune(self.settings.keep_days)
            except OSError as e:
                logger.warning(f"The AI agent's memory history could not be pruned: {e}")
        try:
            removed = self.store.prune(self._clock(), self.settings.keep_days)
            if self.verdicts is not None:
                self.verdicts.forget(set(removed))
        except OSError as e:
            logger.warning(f"The AI agent's old conversations could not be removed: {e}")
            return
        if removed:
            logger.info(f"The AI agent removed {len(removed)} conversation(s) older than {self.settings.keep_days} days")

    async def close(self) -> None:
        await self._provider.close()

    async def _worker(self) -> None:
        while True:
            conversation_id, resumed = await self._queue.get()
            self._queued.discard(conversation_id)
            try:
                await self._work(conversation_id, resumed)
            except asyncio.CancelledError:
                raise
            except UnknownConversation:
                continue
            except Exception:
                logger.exception(f"The AI agent failed on conversation {conversation_id}")
                try:
                    self.store.update_meta(conversation_id, status=FAILED, error="Unexpected error: the admin's log has "
                                           "the details", updated_at=stamp(self._clock()))
                except UnknownConversation:
                    pass
            finally:
                self._working.discard(conversation_id)
                self._stopping.discard(conversation_id)
                self._pending.pop(conversation_id, None)
                self._changed(conversation_id)

    async def _work(self, conversation_id: str, resumed: bool) -> None:
        meta = self.store.meta(conversation_id)
        request = meta["request"]
        model, effort = self._kept_choice(meta)
        task = meta.get("task") or {}
        in_task = task.get("state") == TASK_WORKING
        settings = self._task_settings(task) if in_task else self.settings
        capped = meta.get("debrief") or meta.get("job")
        if capped and request == 0:
            # A daily debrief, a briefing, a triage: its own cost, and its backtests (none unless said). A question
            # asked after it has the agent's limits.
            settings = replace(settings, max_request_usd=min(settings.max_request_usd, capped["max_usd"]),
                               max_backtests=min(settings.max_backtests, capped.get("backtests", 0)))
        self._working.add(conversation_id)
        self.store.update_meta(conversation_id, status="running", model=model, effort=effort)
        pending = self._pending.setdefault(conversation_id, {})
        run = RequestRun(
            conversation_id=conversation_id, request=request, store=self.store, provider=self._provider,
            tools=self._tools, settings=settings, publish=lambda event: self._send(conversation_id, event),
            stopping=lambda: conversation_id in self._stopping, clock=self._clock, used=Used(), pending=pending,
            opening=None if self._asked(conversation_id, request) else self._opening(meta, request, settings),
            model_id=model, effort=effort, task=in_task,
        )
        if resumed:
            run.resume_counts()
            self.store.append(conversation_id, "activity", {"at": stamp(self._clock()), "event": "resumed",
                                                             "request": request})
        outcome = await run.run()
        meta = self.store.meta(conversation_id)
        usage = dict(meta.get("usage") or {})
        for key, value in run.used.tokens.items():
            usage[key] = usage.get(key, 0) + value
        self.store.update_meta(
            conversation_id, status=FAILED if outcome.status == FAILED else "idle", outcome=outcome.status,
            error=outcome.detail if outcome.status == FAILED else None, retry=outcome.retry,
            cost_usd=round((meta.get("cost_usd") or 0) + outcome.usd, 6), usage=usage, updated_at=stamp(self._clock()),
        )
        # A task's next request is asked here, with nothing awaited before the worker lets the conversation go.
        task = self._task_went_on(conversation_id, request, outcome.status, model, effort) if in_task else None
        said = {"task": task["state"], "task_detail": task.get("ended_because")} if task else {}
        self._publish("agent_finished", conversation_id=conversation_id, title=meta.get("title"), outcome=outcome.status,
                      detail=outcome.detail, cost_usd=round(outcome.usd, 4), **said)

    # ── a research task ──

    def _task_settings(self, task: dict):
        """A request of a task: the agent's own limits, within what is left of the task's."""
        budget = task["budget"]
        usd = max(0.0, budget["usd"] - (task.get("spent_usd") or 0.0))
        backtests = max(0, budget["backtests"] - (task.get("backtests") or 0))
        return replace(self.settings, max_request_usd=round(min(self.settings.max_request_usd, usd), 2),
                       max_backtests=min(self.settings.max_backtests, backtests))

    def _task_went_on(self, conversation_id: str, request: int, status: str, model: str, effort: str) -> dict | None:
        """
        After one of a working task's requests: its spending counted, then its
        next request asked, or the task ended with why. A request that failed
        waits for the operator's retry, after which the task goes on.
        """
        meta = self.store.meta(conversation_id)
        task = dict(meta.get("task") or {})
        if task.get("state") != TASK_WORKING or task.get("question"):
            # Ended, or waiting for the operator's answer to its question (a restart's recovery included).
            return task or None
        activity = self.store.read(conversation_id, "activity")
        task["spent_usd"] = round(meta.get("cost_usd") or 0.0, 6)
        task["backtests"] = sum(1 for event in activity if event.get("event") == "run")
        if status == FAILED:
            return self.store.update_meta(conversation_id, task=task)["task"]
        now = self._clock()
        done = next((event for event in reversed(activity)
                     if event.get("event") == "task_done" and event.get("request") == request), None)
        if done is not None:
            task.update(state=TASK_DONE, ended_at=stamp(now), conclusion={
                key: done.get(key) for key in ("conclusion", "runs_kept", "next_steps")})
            return self.store.update_meta(conversation_id, task=task)["task"]
        why = self._task_ends(task, status, now)
        if why is not None:
            task.update(state=TASK_ENDED, ended_at=stamp(now), ended_because=why)
            return self.store.update_meta(conversation_id, task=task)["task"]
        asked = next((event for event in reversed(activity)
                      if event.get("event") == "question" and event.get("request") == request), None)
        if asked is not None:
            # It waits for the operator's answer: said on the pages (and on Telegram by the teller), no request asked.
            task["question"] = {key: asked.get(key) for key in ("question", "choices", "why")}
            task["question"].update(asked_at=stamp(now), request=request)
            meta = self.store.update_meta(conversation_id, task=task)
            self._questions.add(conversation_id)
            self._publish("agent_question", conversation_id=conversation_id, title=meta.get("title"),
                          question=task["question"]["question"])
            return meta["task"]
        task["requests"] = (task.get("requests") or 1) + 1
        budget = task["budget"]
        prompt = task_going_on(task["spent_usd"], budget["usd"], task["backtests"], budget["backtests"], task["deadline"])
        with self._queuing(conversation_id):
            meta = self.store.update_meta(conversation_id, task=task, status="queued", request=request + 1,
                                          requests=(meta.get("requests") or request + 1) + 1, updated_at=stamp(now),
                                          outcome=None, error=None, retry=False)
        self._asked_now(conversation_id, request + 1, prompt, None, "task", model, effort)
        return meta["task"]

    def waiting_questions(self) -> list[dict]:
        """The research tasks that wait for the operator's answer: their conversation, title and question."""
        found = []
        for conversation_id in list(self._questions):
            try:
                meta = self.store.meta(conversation_id)
            except UnknownConversation:
                self._questions.discard(conversation_id)
                continue
            question = (meta.get("task") or {}).get("question")
            if question:
                found.append({"conversation": conversation_id, "title": meta.get("title"), **question})
        return found

    def answer_question(self, conversation_id: str, answer, by: str | None) -> dict:
        """
        The operator's answer to a task's question: the task goes on, its next
        request starting with it, its deadline moved by the time it waited.
        """
        self._check_room()
        if not isinstance(answer, str) or not answer.strip() or len(answer) > MAX_ANSWER:
            raise InvalidRequest(f"An answer is a few words, {MAX_ANSWER:,} characters at most")
        meta = self.store.meta(conversation_id)
        task = dict(meta.get("task") or {})
        question = task.get("question")
        if task.get("state") != TASK_WORKING or not question:
            raise NotPossible("No question of this task waits for an answer")
        now = self._clock()
        waited = max(0.0, now - _seconds(question["asked_at"]))
        task["deadline"] = stamp(_seconds(task["deadline"]) + waited)
        task["question"] = None
        task["answers"] = [*(task.get("answers") or []),
                           {**question, "answer": answer.strip(), "answered_at": stamp(now), "by": by}]
        task["requests"] = (task.get("requests") or 1) + 1
        budget = task["budget"]
        prompt = (task_going_on(task.get("spent_usd") or 0.0, budget["usd"], task.get("backtests") or 0,
                                budget["backtests"], task["deadline"])
                  + f"\n\nThe operator answered your question ({question['question']}): {answer.strip()}")
        model, effort = self._kept_choice(meta)
        request = meta["request"] + 1
        with self._queuing(conversation_id):
            meta = self.store.update_meta(conversation_id, task=task, status="queued", request=request,
                                          requests=(meta.get("requests") or request) + 1, updated_at=stamp(now),
                                          outcome=None, error=None, retry=False)
        self._questions.discard(conversation_id)
        self._asked_now(conversation_id, request, prompt, None, "task", model, effort)
        return meta

    async def _questions_kept(self) -> None:
        """Until cancelled: a question unanswered for QUESTION_WAIT_HOURS ends its task."""
        while True:
            await asyncio.sleep(QUESTION_LOOK_SECONDS)
            try:
                await run_in_threadpool(self.expire_questions)
            except Exception:
                logger.exception("The AI agent could not look at the questions its tasks ask")

    def expire_questions(self) -> list[str]:
        """The tasks whose question waited too long, ended; their conversations."""
        ended = []
        limit = stamp(self._clock() - QUESTION_WAIT_HOURS * 3600)
        for waiting in self.waiting_questions():
            if waiting["asked_at"] > limit:
                continue
            meta = self.store.meta(waiting["conversation"])
            why = f"your answer to its question did not come in {QUESTION_WAIT_HOURS} hours"
            task = {**meta["task"], "state": TASK_ENDED, "ended_at": stamp(self._clock()), "ended_because": why}
            self.store.update_meta(waiting["conversation"], task=task)
            self._questions.discard(waiting["conversation"])
            self._publish("agent_finished", conversation_id=waiting["conversation"], title=meta.get("title"),
                          outcome="done", detail=None, cost_usd=0.0, task=TASK_ENDED, task_detail=why)
            ended.append(waiting["conversation"])
        return ended

    def _task_ends(self, task: dict, status: str, now: float) -> str | None:
        """Why a working task makes no next request, or None."""
        budget = task["budget"]
        if status == STOPPED:
            return "stopped by the operator"
        if status == DECLINED:
            return "the model declined to go on"
        if task["spent_usd"] >= budget["usd"] - TASK_LEAST_USD:
            return f"its budget of ${budget['usd']:.2f} is spent"
        if stamp(now) >= task["deadline"]:
            return f"its {budget['hours']:g} hours are over"
        if (task.get("requests") or 1) >= MAX_TASK_REQUESTS:
            return f"it made {MAX_TASK_REQUESTS} requests, the most a task makes"
        try:
            self._check_room()
        except NotPossible as e:
            return str(e)
        return None

    def _asked(self, conversation_id: str, request: int) -> bool:
        return any(line.get("request") == request and line.get("role") == "user"
                   for line in self.store.read(conversation_id, "messages"))

    def _opening(self, meta: dict, request: int, settings) -> dict:
        prompt = next((event.get("prompt", "") for event in reversed(self.store.read(meta["id"], "activity"))
                       if event.get("event") == "request" and event.get("request") == request), "")
        attached = [entry for entry in meta.get("attached") or [] if entry.get("request") == request]
        if "attached" not in meta and request == 0 and meta.get("subject"):
            # A conversation begun before things could be attached: its subject alone.
            attached = [meta["subject"]]
        return {
            "prompt": prompt,
            "attached": [{key: value for key, value in entry.items() if key != "request"} for entry in attached],
            "limits": limits_note(settings.max_tool_calls, settings.max_backtests, settings.max_request_usd),
            "shared": self._shared,
            "memory": (lambda: self._memory_said(meta["id"], request)) if self.memory is not None else None,
        }

    def _memory_said(self, conversation_id: str, request: int) -> tuple[str | None, int]:
        """
        What a request is told of the memory as it starts, and the change it saw
        last: the list at a conversation's first request (or at the first since
        it has a memory), then what the operator or other conversations changed.
        """
        memory = self.memory
        seen, seq = self.store.meta(conversation_id).get("memory_seq"), memory.seq()
        if request == 0 or seen is None:
            return memory_index(memory.entries(), memory.pause()), seq
        return memory_changes(memory.changes_since(seen, conversation_id)), seq

    async def _luxalgo_subject(self, slug: str) -> dict:
        """An indicator of LuxAlgo's Library, as LuxAlgo has it now, while the agent has LuxAlgo's tools."""
        luxalgo = getattr(self._tools, "luxalgo", None)
        if luxalgo is None or luxalgo.indicator is None:
            raise NotPossible("LuxAlgo's tools are switched off (admin.luxalgo_enabled)")
        if not _SLUG.match(slug):
            raise InvalidRequest("A LuxAlgo indicator is named by its slug: lowercase letters, digits and dashes")
        try:
            found = await luxalgo.indicator(slug)
        except Exception as e:
            raise NotPossible(f"LuxAlgo could not be asked for {slug}: {e}"[:300]) from None
        if found is None:
            raise InvalidRequest(f"LuxAlgo's Library has no indicator {slug}")
        return {"kind": LUXALGO, "id": slug, "label": f"LuxAlgo's {found.get('name') or slug}"}

    async def _shared(self, session, subject: dict, request: int) -> str:
        """What the operator shares at a conversation's start, as the tools read it."""
        kind = subject["kind"]
        if kind == LUXALGO:
            return await _shared_luxalgo(session, subject, request)
        if kind == BRIEFING:
            return await self._shared_briefing(session, subject, request)
        if kind == CHANGE:
            return subject_text("a change, to review before it is saved:", subject["label"],
                                json.dumps(subject["change"], ensure_ascii=False, default=str))
        if kind == DRAFT:
            return await self._shared_draft(session, subject, request)
        if kind == RULE:
            listed = await session.call("rule_list", {}, request)
            try:
                line = next(rule for rule in json.loads(listed.text).get("rules") or [] if rule.get("id") == subject["id"])
                shared = json.dumps(line, ensure_ascii=False, default=str)
            except (TypeError, ValueError, StopIteration, AttributeError):
                shared = f"(not read: {listed.text[:500]}; read it with rule_list)"
            return subject_text("the signal rule", f"{subject.get('label') or subject['id']} ({subject['id']})", shared)
        if kind in (POSITION, SIGNAL, DAY, INCIDENT):
            paper = await self._paper_day(subject["id"]) if kind == DAY else None
            return await _shared_trading(session, subject, request, paper)
        if kind == RUN:
            settings = await session.call("backtest_result", {"run_id": subject["id"], "part": "settings"}, request)
            summary = await session.call("backtest_result", {"run_id": subject["id"], "part": "summary"}, request)
            shared = f"Its settings:\n{settings.text}\n\nIts summary:\n" + (
                summary.text if not summary.is_error else f"(not shared: {summary.text}; read it with backtest_result)")
            return subject_text("the backtest run", f"{subject.get('label') or subject['id']} ({subject['id']})", shared)
        arguments = {"script_id": subject["id"]}
        if subject.get("version"):
            arguments["version"] = subject["version"]
        answer = await session.call("pine_get", arguments, request)
        return subject_text("the Pine script", f"{subject.get('label') or subject['id']} ({subject['id']})", answer.text)

    async def _shared_draft(self, session, subject: dict, request: int) -> str:
        """An unsaved draft of the operator's script: what changed from its version, and whether it compiles."""
        def read() -> tuple[str, str]:
            draft = self.store.read_file(session.conversation_id, "draft.pine") or ""
            return draft, self._scripts.source(subject["script_id"], subject["base_version"])

        draft, base = await run_in_threadpool(read)
        diff = "\n".join(difflib.unified_diff(base.splitlines(), draft.splitlines(), f"version {subject['base_version']}",
                                              "the draft", lineterm=""))
        if len(diff) > MAX_DIFF_CHARS:
            diff = diff[:MAX_DIFF_CHARS] + "\n… (cut: read the whole draft below)"
        checked = await session.call("pine_check", {"source": draft}, request) if draft else None
        parts = [f"What changed from version {subject['base_version']} of script {subject['script_id']}:\n"
                 f"{diff or '(nothing: the draft is that version)'}",
                 f"Its check:\n{checked.text if checked is not None else '(the draft is empty)'}",
                 f"The whole draft:\n{draft}"]
        return subject_text("an unsaved draft of the script,", subject["label"], "\n\n".join(parts))

    async def _shared_briefing(self, session, subject: dict, request: int) -> str:
        """
        The day ahead, before its trading starts: trading now as the dashboard
        reads it, the health, the rules, what changed since the day before's
        close, and the day before as its debrief told it.
        """
        async def read(tool: str, arguments: dict) -> str | None:
            if not session.offers(tool):
                return None
            answer = await session.call(tool, arguments, request)
            return answer.text if not answer.is_error else f"(not read: {answer.text})"

        parts = [
            ("Trading now", await read("ops_trading_now", {})),
            ("The admin's health", await read("ops_health", {})),
            ("The signal rules", await read("rule_list", {})),
            (f"What changed in the last {BRIEFING_CHANGES_HOURS} hours",
             await read("ops_changes", {"hours": BRIEFING_CHANGES_HOURS})),
            ("The day before, as its debrief told it", await run_in_threadpool(self._debrief_before, subject["id"])),
        ]
        return subject_text("the day ahead", subject["id"],
                            "\n\n".join(f"{name}:\n{text}" for name, text in parts if text is not None))

    def _debrief_before(self, day: str) -> str:
        """The answer of the newest daily debrief of a day before ``day``, or why there is none."""
        found = [meta for meta in self.store.metas()
                 if (meta.get("debrief") or {}).get("day", "") < day and meta.get("status") not in transcript.UNDER_WAY]
        if not found:
            return "No debrief of a day before it."
        newest = max(found, key=lambda meta: meta["debrief"]["day"])
        said = transcript.project(newest, self.store.read(newest["id"], "activity"), {})
        answer = next((entry.get("answer") for entry in said["requests"] if entry.get("answer")), None)
        text = (answer or {}).get("text") or "It was not written."
        return f"The debrief of {newest['debrief']['day']}:\n{text[:BRIEFING_DEBRIEF_CHARS]}"

    async def _paper_day(self, day: str) -> str:
        """The signals received that day traded on paper under the live settings, as the Performance page shows it."""
        if self._backtests is None:
            return "Not known: backtests are switched off."
        live = (await run_in_threadpool(self._backtests.paper, with_reports=False)).get("live") or {}
        found = next((entry for entry in live.get("days") or [] if entry.get("day") == day), None)
        if found is None:
            return "Not traded on paper: a day is, once it is over and the market closed."
        if found.get("state") == "no_entry":
            return "No entry signal that day: nothing was traded on paper."
        said = f"Run {found.get('run_id')} ({found.get('state')})"
        if found.get("day_percent") is not None:
            said += f": {found['day_percent']:+.2f} % on {found.get('trades') or 0} trades"
        reference = live.get("reference") or {}
        if reference.get("low") is not None:
            said += (f"; the live reference's daily range is {reference['low']:+.2f} % to {reference['high']:+.2f} %"
                     f"{', and the day is outside it' if found.get('outside') else ''}")
        return f"{said}. Its trades: backtest_trades of that run."

    # ── the conversation's stream ──

    def subscribe(self, conversation_id: str) -> asyncio.Queue:
        self.store.meta(conversation_id)
        queue: asyncio.Queue = asyncio.Queue(maxsize=STREAM_QUEUE_SIZE)
        self._subscribers.setdefault(conversation_id, set()).add(queue)
        return queue

    def unsubscribe(self, conversation_id: str, queue: asyncio.Queue) -> None:
        queues = self._subscribers.get(conversation_id)
        if queues is not None:
            queues.discard(queue)
            if not queues:
                self._subscribers.pop(conversation_id, None)

    def _send(self, conversation_id: str, event: dict) -> None:
        for queue in self._subscribers.get(conversation_id, ()):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # A page that stopped reading: it reads the whole conversation again once it reads its stream again.
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait({"type": "changed"})

    def _changed(self, conversation_id: str) -> None:
        self._send(conversation_id, {"type": "changed"})

    # ── what the routes ask ──

    def _check_room(self) -> None:
        if self.broken:
            raise NotPossible(self.broken)
        pause = self.store.pause()
        if pause:
            raise NotPossible(f"The AI agent is paused (since {pause['since']}): resume it from its Activity page")
        spent, budget = self.month_spent(), self.settings.monthly_budget_usd
        if spent >= budget:
            raise NotPossible(f"This month's budget of ${budget:.2f} is spent (${spent:.2f}): raise it in "
                              "Configuration › AI agent, or wait for the next month")

    async def _check_key(self) -> None:
        if not await self._key():
            raise NotPossible("No Claude API key is set: set it in Configuration › AI agent")

    async def has_key(self) -> bool | None:
        """Whether a Claude API key is set; None when it cannot be told (the database down)."""
        try:
            return bool(await self._key())
        except Exception:
            return None

    def month_spent(self) -> float:
        return self.store.spent(month_of(self._clock()))

    def state(self) -> dict:
        settings = self.settings
        metas = self.store.metas()
        return {
            "enabled": True, "started": self._started, "broken": self.broken,
            "provider": settings.provider, "model": settings.model, "model_label": claude.label_of(settings.model),
            "effort": settings.effort, "pause": self.store.pause(),
            "month": {"month": month_of(self._clock()), "spent_usd": round(self.month_spent(), 4),
                      "budget_usd": settings.monthly_budget_usd},
            "limits": {"tool_calls": settings.max_tool_calls, "backtests": settings.max_backtests,
                       "request_usd": settings.max_request_usd, "running": settings.max_running},
            "under_way": [meta["id"] for meta in metas if meta.get("status") in transcript.UNDER_WAY],
            "conversations": len(metas), "prices_of": claude.PRICES_OF,
            "models": [self._model_entry(model_id) for model_id in CHOICES], "efforts": list(EFFORTS),
            "tasks": {"usd": settings.max_task_usd, "backtests": settings.max_task_backtests,
                      "hours": settings.max_task_hours},
            "debrief": {"enabled": settings.debrief_enabled, "model": settings.debrief_model,
                        "effort": settings.debrief_effort, "max_usd": settings.debrief_max_usd,
                        "telegram": settings.debrief_telegram},
            "memory": self.memory is not None,
            # Whether it has LuxAlgo's tools (admin.luxalgo_enabled): the pages offer what they make possible.
            "luxalgo": getattr(self._tools, "luxalgo", None) is not None,
        }

    # ── the models a request may choose ──

    def _model_entry(self, model_id: str) -> dict:
        model, said = claude.MODELS[model_id], self._models.get(model_id) or {}
        return {
            "id": model_id, "label": model.label, "window": said.get("window") or model.window,
            "max_output": said.get("max_output") or model.max_output,
            "prices": {"input": model.input, "output": model.output, "cache_read": model.cache_read},
            "efforts": said.get("efforts") or list(EFFORTS),
            # None until the Models API answered for the key.
            "available": said.get("available"),
            # Whose notes it reads: the pages say which a switch to it leaves out.
            "reads": list(model.reads),
            # Whether it reads the images and the PDFs attached: None until the Models API says.
            "images": said.get("images"), "pdfs": said.get("pdfs"),
        }

    async def refresh_models(self) -> None:
        """What the Models API says of each model for the key: its window, its efforts, whether the key may use it."""
        for model_id in CHOICES:
            try:
                said = await self._provider.model_info(model_id)
            except claude.NoKey:
                return
            except asyncio.CancelledError:
                raise
            except Exception as e:
                # Not found, or refused this key: no request is sent to it.
                if getattr(e, "status_code", None) in (403, 404):
                    self._models[model_id] = {"available": False}
                else:
                    logger.info(f"The AI agent could not read {model_id} from the Models API: {e!r}")
                continue
            self._models[model_id] = {**{key: value for key, value in said.items() if value is not None},
                                      "available": True}

    def _refresh_soon(self) -> None:
        """The Models API asked in the background once a key is there: it may have been set since the start."""
        if not self._models and (self._refreshing is None or self._refreshing.done()):
            self._refreshing = asyncio.create_task(self.refresh_models())

    def _choice(self, model: str | None, effort: str | None, meta: dict | None = None) -> tuple[str, str]:
        """A request's model and effort: those asked, else the conversation's last, else the settings'."""
        kept_model, kept_effort = self._kept_choice(meta or {})
        model, effort = model or kept_model, effort or kept_effort
        if model not in CHOICES:
            raise InvalidRequest(f"The model is one of {', '.join(CHOICES)}")
        entry = self._model_entry(model)
        if entry["available"] is False:
            raise NotPossible(f"{entry['label']} cannot be used with this Claude API key: choose another model")
        if effort not in entry["efforts"]:
            raise InvalidRequest(f"{entry['label']} takes the effort {', '.join(entry['efforts'])}")
        return model, effort

    def _kept_choice(self, meta: dict) -> tuple[str, str]:
        """The model and effort a conversation goes on with: its last, or the settings' for one begun before."""
        model = meta.get("model") if meta.get("model") in CHOICES else self.settings.model
        effort = meta.get("effort") if meta.get("effort") in EFFORTS else self.settings.effort
        return model, effort

    def under_way(self) -> int:
        """Requests queued or worked on: an admin restart would take them up again at its start."""
        return len(self._working) + self._queue.qsize()

    async def _subject(self, subject: dict | None) -> dict | None:
        if not subject:
            return None
        kind, subject_id = subject.get("kind"), subject.get("id")
        if kind not in SUBJECT_KINDS or not isinstance(subject_id, str) or not subject_id:
            raise InvalidRequest("A conversation is about a run, a script, a position, a signal, a day, an incident, "
                                 "the day ahead, a signal rule, a change reviewed before it is saved or an "
                                 "indicator of LuxAlgo's Library")
        if kind == LUXALGO:
            return await self._luxalgo_subject(subject_id)
        if kind in (RUN, SCRIPT, RULE):
            return await run_in_threadpool(self._stored_subject, kind, subject_id, subject.get("version"))
        if kind in (CHANGE, DRAFT):
            return await run_in_threadpool(self._reviewed, subject)
        if kind == INCIDENT:
            at = subject.get("at")
            if not _HEALTH_ID.match(subject_id) or not isinstance(at, str) or not _MOMENT.match(at):
                raise InvalidRequest("An incident is an entry of the health and a moment: YYYY-MM-DD HH:MM (UTC)")
            return {"kind": INCIDENT, "id": subject_id, "at": at, "label": f"{subject_id.replace('_', ' ')} at {at} UTC"}
        if kind in (DAY, BRIEFING):
            if not _DAY.match(subject_id):
                raise InvalidRequest("A day is YYYY-MM-DD")
            label = f"the day of {subject_id}" if kind == DAY else f"the day ahead, {subject_id}"
            return {"kind": kind, "id": subject_id, "label": label}
        if self._queries is None:
            raise NotPossible("WATA's positions and signals cannot be read here")
        if kind == POSITION:
            found = await self._queries.position_record(subject_id)
            if found is None:
                raise InvalidRequest(f"WATA has no position {subject_id}")
            position = found["position"]
            said = " ".join(str(part) for part in (position.get("action"), position.get("instrument_name")) if part)
            return {"kind": POSITION, "id": subject_id, "label": said or f"position {subject_id}"}
        day = subject.get("day")
        if not isinstance(day, str) or not _DAY.match(day):
            raise InvalidRequest("A signal is named with its day: YYYY-MM-DD")
        rows = await self._queries.journal_between(date.fromisoformat(day), date.fromisoformat(day))
        row = next((row for row in rows if row.get("signal_uuid") == subject_id), None)
        if row is None:
            raise InvalidRequest(f"No signal {subject_id} on {day}")
        return {"kind": SIGNAL, "id": subject_id, "day": day, "label": f"{row.get('action') or 'the'} signal of {day}"}

    async def _attached(self, attach, held: list[dict]) -> list[dict]:
        """
        What a request attaches, checked before anything is kept: each subject
        named as one a conversation is about, each file as it was staged; five
        at most in a conversation with what it holds (``held``), none twice.
        """
        if not attach:
            return []
        if not isinstance(attach, list):
            raise InvalidRequest("What is attached is a list")
        if len(held) + len(attach) > MAX_ATTACHED:
            raise InvalidRequest(f"A conversation has {MAX_ATTACHED} things attached at most")
        seen = {_identity(entry) for entry in held}
        checked = []
        for entry in attach:
            kind = entry.get("kind") if isinstance(entry, dict) else None
            if kind == FILE:
                try:
                    found = {"kind": FILE, **await run_in_threadpool(self.uploads.describe, entry.get("id"))}
                except UnknownFile:
                    raise InvalidRequest("A file attached is no longer waiting to be sent: attach it again") from None
            elif kind in ATTACHABLE:
                found = await self._subject(entry)
            else:
                raise InvalidRequest("Attach a run, a script, a signal rule, a position, a signal, a day, an incident "
                                     "or a file")
            if _identity(found) in seen:
                raise InvalidRequest(f"{found.get('label') or found.get('name')} is attached already")
            seen.add(_identity(found))
            checked.append(found)
        return checked

    async def _taken(self, attached: list[dict]) -> dict[str, bytes]:
        """The bytes of the files attached, no longer staged: kept with the conversation they are sent in."""
        taken = {}
        for entry in attached:
            if entry["kind"] == FILE:
                try:
                    _, taken[entry["id"]] = await run_in_threadpool(self.uploads.take, entry["id"])
                except UnknownFile:
                    raise InvalidRequest(f"{entry['name']} is no longer waiting to be sent: attach it again") from None
        return taken

    def _check_readable(self, model: str, attached: list[dict]) -> None:
        """No image is sent to a model the Models API says reads none, nor a PDF to one that reads no PDF."""
        entry = self._model_entry(model)
        for kind, key, said in ((IMAGE, "images", "images"), (PDF, "pdfs", "PDFs")):
            if entry.get(key) is False and any(found.get("kind") == FILE and found.get("type") == kind
                                               for found in attached):
                raise InvalidRequest(f"{entry['label']} does not read {said}: choose another model")

    def attachment(self, conversation_id: str, file_id: str) -> tuple[dict, bytes]:
        """A file attached to a conversation, as the operator gave it (a text cleaned), and what is said of it."""
        meta = self.store.meta(conversation_id)
        entry = next((found for found in meta.get("attached") or []
                      if found.get("kind") == FILE and found.get("id") == file_id), None)
        data = self.store.read_attachment(conversation_id, file_id) if entry is not None else None
        if data is None:
            raise UnknownFile(f"No file {file_id!r} in {conversation_id}")
        return entry, data

    def _reviewed(self, subject: dict) -> dict:
        """
        A change reviewed before it is saved, as the review's route made it
        (src/admin/routers/agent.py): config.json's or a rule's changes, or a
        script's draft, its source given as the conversation's file.
        """
        if not isinstance(subject.get("id"), str) or not _REVIEW_ID.match(subject["id"]):
            raise InvalidRequest("A review is asked from the page that edits what it reviews")
        label = str(subject.get("label") or "")[:MAX_TITLE]
        if subject["kind"] == CHANGE:
            change = subject.get("change")
            if not isinstance(change, dict) or change.get("of") not in ("config", "rule"):
                raise InvalidRequest("A review is asked from the page that edits what it reviews")
            if len(json.dumps(change, default=str)) > MAX_CHANGE_CHARS:
                raise InvalidRequest("This change is too large to review at once: review it in parts")
            return {"kind": CHANGE, "id": subject["id"], "label": label, "change": change}
        script = self._scripts.script(subject.get("script_id") or "")
        base = subject.get("base_version")
        if not isinstance(base, int) or not 1 <= base <= script["version"]:
            raise InvalidRequest(f"{script['name']!r} has no version {base}")
        return {"kind": DRAFT, "id": subject["id"], "label": label or f"a draft of {script['name']}",
                "script_id": script["id"], "base_version": base}

    def _stored_subject(self, kind: str, subject_id: str, version) -> dict:
        if kind == RULE:
            try:
                rule = RuleStore(self._scripts).rule(subject_id)
            except RuleNotFound:
                raise InvalidRequest(f"WATA has no signal rule {subject_id}") from None
            return {"kind": RULE, "id": subject_id, "label": rule["name"]}
        if kind == RUN:
            if self._backtests is None:
                raise NotPossible("Backtests are switched off in the configuration (admin.backtests_enabled)")
            run = self._backtests.get(subject_id)
            return {"kind": RUN, "id": subject_id, "label": run.get("name") or subject_id}
        script = self._scripts.script(subject_id)
        if version is not None and (not isinstance(version, int) or not 1 <= version <= script["version"]):
            raise InvalidRequest(f"{script['name']!r} has no version {version}")
        return {"kind": SCRIPT, "id": subject_id, "label": script["name"], "version": version or script["version"]}

    async def create(self, subject: dict | None, prompt: str, by: str | None, model: str | None = None,
                     effort: str | None = None, *, task: dict | None = None, debrief: dict | None = None,
                     job: dict | None = None, files: dict[str, str] | None = None, attach: list | None = None) -> dict:
        """
        A conversation, its first request queued. ``job``: one the admin asks
        by itself (a briefing, a triage, a dossier): its kind, title, what its
        first request may cost (``max_usd``) and its backtests (none unless
        said). ``files``: kept with it before its first request starts (the
        draft a review is about). ``attach``: what the operator attached to it
        besides the page's subject (``_attached``); it is about the first.
        """
        self._check_room()
        await self._check_key()
        prompt = _prompt(prompt)
        self._refresh_soon()
        model, effort = self._choice(model, effort)
        subject = await self._subject(subject)
        given = [subject] if subject is not None else []
        attached = await self._attached(attach, given)
        everything = given + attached
        self._check_readable(model, everything)
        subject = subject or next((entry for entry in attached if entry["kind"] != FILE), None)
        taken = await self._taken(attached)
        now = self._clock()
        conversation_id = self.store.new_id(now)
        if debrief is not None:
            title = f"Debrief of {date.fromisoformat(debrief['day']):%a %d %b %Y}"
        elif job is not None and job.get("title"):
            title = job["title"][:MAX_TITLE]
        else:
            title = _title(task["goal"] if task else prompt)
        meta = {
            "id": conversation_id, "title": title, "subject": subject, "status": "queued", "model": model,
            "effort": effort, "created_at": stamp(now), "updated_at": stamp(now), "by": by, "request": 0, "requests": 1,
            "cost_usd": 0.0, "usage": {},
            "digest": digest_of(self._tools.definitions()), "outcome": None, "error": None, "retry": False,
        }
        if task is not None:
            meta["task"] = task
        if debrief is not None:
            # A daily debrief (src/admin/agent_debrief.py): its day, and what its request may cost.
            meta["debrief"] = debrief
        if job is not None:
            meta["job"] = job
        if attached:
            # What it holds, each with the request it came with: it is listed beside each (conversations). Without
            # anything attached, its subject alone, as before.
            meta["attached"] = [{**entry, "request": 0} for entry in everything]
        with self._queuing(conversation_id):
            await run_in_threadpool(self.store.create, meta)
            for name, text in (files or {}).items():
                await run_in_threadpool(self.store.write_file, conversation_id, name, text)
            for file_id, data in taken.items():
                await run_in_threadpool(self.store.write_attachment, conversation_id, file_id, data)
        self._asked_now(conversation_id, 0, prompt, subject, by, model, effort, everything if attached else [])
        return meta

    async def create_task(self, goal: str, subject: dict | None, by: str | None, budget: dict,
                          model: str | None = None, effort: str | None = None, attach: list | None = None) -> dict:
        """
        A research task: a conversation the agent works on alone, request after
        request, within its budget (dollars, backtests, hours: each at most the
        settings' ``max_task_*``) and the month's.
        """
        goal = _prompt(goal)
        usd, backtests, hours = self._task_budget(budget)
        now = self._clock()
        task = {
            "goal": goal, "budget": {"usd": usd, "backtests": backtests, "hours": hours}, "state": TASK_WORKING,
            "started_at": stamp(now), "deadline": stamp(now + hours * 3600), "spent_usd": 0.0, "backtests": 0,
            "requests": 1, "by": by, "ended_at": None, "ended_because": None, "conclusion": None,
        }
        return await self.create(subject, task_opening(goal, usd, backtests, task["deadline"]), by, model, effort,
                                 task=task, attach=attach)

    def _task_budget(self, budget) -> tuple[float, int, float]:
        settings = self.settings
        if not isinstance(budget, dict):
            raise InvalidRequest("A task's budget is its dollars, backtests and hours")
        usd, backtests, hours = budget.get("usd"), budget.get("backtests"), budget.get("hours")
        if isinstance(usd, bool) or not isinstance(usd, (int, float)) or not 0.5 <= usd <= settings.max_task_usd:
            raise InvalidRequest(f"A task's dollars are from $0.50 to ${settings.max_task_usd:.2f} (max_task_usd)")
        if isinstance(backtests, bool) or not isinstance(backtests, int) or not 0 <= backtests <= settings.max_task_backtests:
            raise InvalidRequest(f"A task's backtests are from 0 to {settings.max_task_backtests} (max_task_backtests)")
        if isinstance(hours, bool) or not isinstance(hours, (int, float)) or not 0.5 <= hours <= settings.max_task_hours:
            raise InvalidRequest(f"A task's hours are from 0.5 to {settings.max_task_hours:g} (max_task_hours)")
        return round(float(usd), 2), backtests, float(hours)

    def stop_task(self, conversation_id: str, by: str | None) -> dict:
        """A task ended by the operator: no request follows; the one under way stops between two tool calls."""
        meta = self.store.meta(conversation_id)
        task = meta.get("task")
        if not task:
            raise InvalidRequest("This conversation is not a research task")
        if task.get("state") != TASK_WORKING:
            raise NotPossible("This task has ended already")
        task = {**task, "state": TASK_ENDED, "ended_at": stamp(self._clock()), "ended_because": "stopped by the operator",
                "stopped_by": by, "question": None}
        meta = self.store.update_meta(conversation_id, task=task)
        self._questions.discard(conversation_id)
        if meta.get("status") in transcript.UNDER_WAY:
            self.stop(conversation_id)
        self._changed(conversation_id)
        return meta

    async def ask(self, conversation_id: str, prompt: str, by: str | None, model: str | None = None,
                  effort: str | None = None, attach: list | None = None) -> dict:
        self._check_room()
        await self._check_key()
        prompt = _prompt(prompt)
        meta = self.store.meta(conversation_id)
        if meta.get("status") in transcript.UNDER_WAY:
            raise NotPossible("The agent is still working on this conversation: wait for its answer, or stop it")
        model, effort = self._choice(model, effort, meta)
        # What it holds already: a conversation begun before things could be attached, its subject alone.
        held = meta.get("attached") or ([{**meta["subject"], "request": 0}] if meta.get("subject") else [])
        attached = await self._attached(attach, held)
        self._check_readable(model, held + attached)
        taken = await self._taken(attached)
        request = meta["request"] + 1
        more = {"attached": [*held, *({**entry, "request": request} for entry in attached)]} if attached else {}
        with self._queuing(conversation_id):
            for file_id, data in taken.items():
                await run_in_threadpool(self.store.write_attachment, conversation_id, file_id, data)
            meta = self.store.update_meta(conversation_id, status="queued", request=request,
                                          requests=(meta.get("requests") or request) + 1,
                                          updated_at=stamp(self._clock()), outcome=None, error=None, retry=False,
                                          model=model, effort=effort, **more)
        self._asked_now(conversation_id, request, prompt, None, by, model, effort, attached)
        return meta

    def _asked_now(self, conversation_id: str, request: int, prompt: str, subject: dict | None, by: str | None,
                   model: str, effort: str, attached: list[dict] | None = None) -> None:
        # shared: the conversation's subject, as the pages of before read it; attached: all the request shares.
        self.store.append(conversation_id, "activity", {"at": stamp(self._clock()), "event": "request",
                                                         "request": request, "prompt": prompt, "shared": subject,
                                                         "attached": attached or [], "by": by, "model": model,
                                                         "effort": effort})
        self._enqueue(conversation_id, False)
        self._changed(conversation_id)

    def retry(self, conversation_id: str) -> dict:
        """A request that failed (the API out of reach, no key then), taken up again from where it stopped."""
        self._check_room()
        meta = self.store.meta(conversation_id)
        if meta.get("status") != FAILED:
            raise NotPossible("Only a request that failed is taken up again: ask again instead")
        with self._queuing(conversation_id):
            meta = self.store.update_meta(conversation_id, status="queued", error=None, updated_at=stamp(self._clock()))
        self._enqueue(conversation_id, True)
        self._changed(conversation_id)
        return meta

    def stop(self, conversation_id: str) -> dict:
        meta = self.store.meta(conversation_id)
        if meta.get("status") not in transcript.UNDER_WAY:
            raise NotPossible("Nothing is under way in this conversation")
        self._stopping.add(conversation_id)
        if conversation_id not in self._working:
            # Still waiting its turn: the worker that takes it up ends it at once.
            self._changed(conversation_id)
        return meta

    def conversation(self, conversation_id: str) -> dict:
        meta = self.store.meta(conversation_id)
        pending = self._pending.get(conversation_id)
        snapshot = None
        if pending and pending.get("request") is not None:
            snapshot = {"request": pending["request"], "text": dict(pending.get("text") or {}),
                        "note": dict(pending.get("note") or {}), "writing": pending.get("writing")}
        return transcript.project(meta, self.store.read(conversation_id, "activity"),
                                  self.store.proposals(conversation_id), snapshot)

    # ── the context window ──

    async def context(self, conversation_id: str | None, model_id: str | None = None) -> dict:
        """
        What the next request of a conversation (or of a new one) would put in the
        model's context window, part by part: the system prompt, the tools, the
        conversation's thinking counted by the API, the rest split by its length;
        what context editing leaves out; and what the last answer used.
        """
        meta = await run_in_threadpool(self.store.meta, conversation_id) if conversation_id else {}
        if model_id is not None and model_id not in CHOICES:
            raise InvalidRequest(f"The model is one of {', '.join(CHOICES)}")
        kept_model, effort = self._kept_choice(meta)
        model = claude.MODELS[model_id or kept_model]
        entry = self._model_entry(model.id)
        lines = await run_in_threadpool(self.store.read, conversation_id, "messages") if conversation_id else []
        tools = self._tools.definitions()
        drop_block = meta.get("digest") not in (None, digest_of(tools))
        messages = await run_in_threadpool(
            transcript.api_messages, lines, lambda file_id: self.store.read_attachment(conversation_id, file_id))
        self._refresh_soon()
        try:
            counted = await self._counted(model, effort, tools, messages, drop_block, (conversation_id, len(lines)))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if not isinstance(e, claude.NoKey):
                logger.info(f"The AI agent could not count the tokens of {conversation_id or 'a new conversation'}: {e!r}")
            counted = None
        last = transcript.last_call(lines)
        return transcript.context_view(
            model=model.id, label=model.label, window=entry["window"], answer_room=claude.MAX_OUTPUT,
            tools=len(tools), counted=counted, base=self._base_counts.get((model.id, digest_of(tools))),
            composition=transcript.composition(messages), last=last,
            thinking_written=transcript.thinking_written(lines, model.reads),
            unread=[claude.label_of(writer) for writer in claude.unread_by(model.id, transcript.thinking_writers(lines))],
            clearing={"from_tokens": CLEAR_FROM_TOKENS, "keep_tool_uses": KEEP_TOOL_USES},
            files=transcript.files_in(lines),
        )

    async def _counted(self, model, effort: str, tools: list[dict], messages: list[dict], drop_block: bool,
                       key: tuple) -> dict:
        """The API's counts: the system prompt and the tools once per model, then the conversation as it stands."""
        base_key = (model.id, digest_of(tools))
        base = self._base_counts.get(base_key)
        if base is None:
            asked = counted_params(request_params(model, effort, tools, [{"role": "user", "content": [_NEXT]}], False))
            alone = await self._count({key: value for key, value in asked.items() if key not in ("system", "tools")})
            with_system = await self._count({key: value for key, value in asked.items() if key != "tools"})
            whole = await self._count(asked)
            base = {"system": with_system["input_tokens"] - alone["input_tokens"],
                    "tools": whole["input_tokens"] - with_system["input_tokens"],
                    "empty": alone["input_tokens"], "with_tools": whole["input_tokens"]}
            self._base_counts[base_key] = base
        if not messages:
            return {"conversation": 0, "thinking": 0, "cleared": 0}
        count_key = (*key, model.id)
        if count_key not in self._counts:
            ending = transcript.ending_with_operator(messages, _NEXT)
            counted = await self._count(counted_params(request_params(model, effort, tools, ending, drop_block)))
            unthought = await self._count(counted_params(request_params(
                model, effort, tools, transcript.without_thinking(ending), False)))
            # The files attached, counted without the thinking as well: a thinking block holds only with what was
            # sent before it.
            in_files = 0
            if transcript.without_files(ending) != ending:
                unfiled = await self._count(counted_params(request_params(
                    model, effort, tools, transcript.without_thinking(transcript.without_files(ending)), False)))
                in_files = max(0, unthought["input_tokens"] - unfiled["input_tokens"])
            original = (counted.get("context_management") or {}).get("original_input_tokens")
            self._counts[count_key] = {
                "conversation": counted["input_tokens"] - base["with_tools"],
                "thinking": max(0, counted["input_tokens"] - unthought["input_tokens"]),
                "cleared": max(0, (original or counted["input_tokens"]) - counted["input_tokens"]),
                "files": in_files,
            }
            while len(self._counts) > COUNTS_KEPT:
                self._counts.pop(next(iter(self._counts)))
        return self._counts[count_key]

    async def _count(self, params: dict) -> dict:
        return await self._provider.count_tokens(params)

    def conversations(self, subject: str | None = None) -> list[dict]:
        metas = self.store.metas()
        if subject:
            kind, _, subject_id = subject.partition(":")
            metas = [meta for meta in metas
                     if any(held.get("kind") == kind and held.get("id") == subject_id
                            for held in [meta.get("subject") or {}, *(meta.get("attached") or [])])]
        return [{**{key: meta.get(key) for key in (
            "id", "title", "subject", "status", "model", "created_at", "updated_at", "requests", "cost_usd",
            "outcome", "error", "by",
        )}, "attached": len(meta.get("attached") or []), "task": _task_line(meta.get("task"))} for meta in metas]

    def rename(self, conversation_id: str, title: str) -> dict:
        if not isinstance(title, str) or not title.strip():
            raise InvalidRequest("A conversation's title is a few words")
        return self.store.update_meta(conversation_id, title=title.strip()[:MAX_TITLE])

    def remove(self, conversation_id: str) -> None:
        meta = self.store.meta(conversation_id)
        if meta.get("status") in transcript.UNDER_WAY:
            raise NotPossible("The agent is working on this conversation: stop it first")
        self.store.remove(conversation_id)
        if self.verdicts is not None:
            self.verdicts.forget({conversation_id})

    def feedback(self, conversation_id: str, request: int, value: str | None) -> None:
        meta = self.store.meta(conversation_id)
        if value not in FEEDBACK or not isinstance(request, int) or not 0 <= request <= meta["request"]:
            raise InvalidRequest("Feedback is like, dislike or none, on one of the conversation's requests")
        self.store.append(conversation_id, "activity", {"at": stamp(self._clock()), "event": "feedback",
                                                         "request": request, "value": value})
        self._changed(conversation_id)

    def settle(self, conversation_id: str, proposal_id: str, apply: bool, by: str | None) -> dict:
        settle = self.proposals.apply if apply else self.proposals.dismiss
        proposal = settle(conversation_id, proposal_id, by)
        self._changed(conversation_id)
        return proposal

    def into_editor(self, conversation_id: str, proposal_id: str, by: str | None) -> dict:
        """A script's change, proposed on a draft reviewed, put in the operator's editor: applied, nothing saved."""
        if (self.store.meta(conversation_id).get("subject") or {}).get("kind") != DRAFT:
            raise InvalidRequest("Only a change proposed on a draft reviewed is put in the editor")
        proposal = self.proposals.into_editor(conversation_id, proposal_id, by)
        self._changed(conversation_id)
        return proposal

    def settle_action(self, conversation_id: str, proposal_id: str, by: str | None, result: dict) -> dict:
        """An action proposed, done by the page's route (src/admin/proposed_actions.py): applied."""
        proposal = self.proposals.action_applied(conversation_id, proposal_id, by, result)
        self._changed(conversation_id)
        return proposal

    def settings_proposal(self, conversation_id: str, proposal_id: str) -> dict:
        """A change of settings not applied yet: the agent's route previews and saves it with the editor."""
        self.store.meta(conversation_id)
        return self.proposals.pending_settings(conversation_id, proposal_id)

    def settings_saved(self, conversation_id: str, proposal_id: str, by: str | None, saved: dict) -> dict:
        proposal = self.proposals.settings_saved(conversation_id, proposal_id, by, saved)
        self._changed(conversation_id)
        return proposal

    def set_pause(self, paused: bool, by: str | None) -> dict | None:
        return self.store.set_pause(paused, by, self._clock())

    def artefacts(self) -> list[dict]:
        """What conversations made, newest first, with the title of the conversation each comes from."""
        titles = {meta["id"]: meta.get("title") for meta in self.store.metas()}
        return [{**line, "title_of": titles.get(line.get("conversation"))}
                for line in reversed(self.store.artefacts()) if line.get("conversation") in titles]


async def _shared_trading(session, subject: dict, request: int, paper: str | None = None) -> str:
    """
    A position, a signal, a day or an incident, as the agent's tools read it at
    the conversation's start; a day with how it traded on paper (``paper``,
    which no tool gives).
    """
    kind, label = subject["kind"], subject.get("label") or subject["id"]

    async def read(tool: str, arguments: dict) -> str:
        answer = await session.call(tool, arguments, request)
        return answer.text if not answer.is_error else f"(not read: {answer.text})"

    if kind == POSITION:
        return subject_text("the position", f"{label} ({subject['id']})", await read("data_position",
                                                                                     {"position_id": subject["id"]}))
    if kind == DAY:
        day = subject["id"]
        parts = [
            ("Its trades", await read("data_trades", {"start_day": day, "limit": SHARED_ROWS})),
            ("The signals it received", await read("data_signals", {"start_day": day, "kind": "received",
                                                                    "limit": SHARED_ROWS})),
            ("What Saxo refused", await read("data_orders", {"start_day": day})),
        ]
        if paper is not None:
            parts.append(("The signals it received, traded on paper under the live settings", paper))
        return subject_text("the trading day", day, "\n\n".join(f"{name}:\n{text}" for name, text in parts))
    if kind == SIGNAL:
        listed = await session.call("data_signals", {"start_day": subject["day"], "kind": "received", "limit": 500},
                                    request)
        try:
            row = next((row for row in json.loads(listed.text).get("items") or []
                        if row.get("signal_uuid") == subject["id"]), None)
        except (TypeError, ValueError, AttributeError):
            row = None
        shared = f"Its row of the journal:\n{json.dumps(row, ensure_ascii=False, default=str)}"
        if row and row.get("position_id"):
            shared += f"\n\nThe position it opened:\n{await read('data_position', {'position_id': str(row['position_id'])})}"
        return subject_text("the signal", f"{label} ({subject['id']})", shared)
    shared = f"The admin's health now:\n{await read('ops_health', {})}"
    service = LOG_OF.get(subject["id"])
    if service and session.offers("ops_logs"):
        shared += (f"\n\nThe lines of {service} around {subject['at']} (UTC):\n"
                   f"{await read('ops_logs', {'service': service, 'around': subject['at'], 'minutes': 15})}")
    if session.offers("ops_changes"):
        shared += (f"\n\nWhat changed in the {INCIDENT_CHANGES_HOURS} hours before it:\n"
                   f"{await read('ops_changes', {'until': subject['at'], 'hours': INCIDENT_CHANGES_HOURS})}")
    return subject_text("the incident", label, shared)


async def _shared_luxalgo(session, subject: dict, request: int) -> str:
    """
    An indicator of LuxAlgo's Library: its write-up and concepts, whether its
    source comes whole and under which licence (the source itself is taken
    with luxalgo_save_script), and what WATA already knows of it.
    """
    slug = subject["id"]

    async def read(tool: str, arguments: dict) -> dict | str:
        answer = await session.call(tool, arguments, request)
        if answer.is_error:
            return f"(not read: {answer.text[:500]})"
        try:
            return json.loads(answer.text)
        except ValueError:
            return answer.text

    found = await read("luxalgo_call", {"tool": "library_get_indicator", "arguments": {"slug": slug}})
    if isinstance(found, dict):
        write_up = str(found.get("body_markdown") or "")
        if len(write_up) > LUXALGO_WRITE_UP_CHARS:
            write_up = f"{write_up[:LUXALGO_WRITE_UP_CHARS]}… (cut: read it whole with luxalgo_call)"
        found = {**{key: found.get(key) for key in ("slug", "name", "author", "family", "date_displayed", "url",
                                                    "concepts")}, "write_up": write_up}
    source = await read("luxalgo_call", {"tool": "library_get_source_code", "arguments": {"slug": slug}})
    if isinstance(source, dict):
        from src.admin.luxalgo import licence_of

        text = str(source.get("source") or "")
        source = {"available": source.get("available"), "complete": source.get("complete"),
                  "lines": len(text.splitlines()), "total_lines": source.get("total_lines") or len(text.splitlines()),
                  **licence_of(text), "chart_url": source.get("chart_url")}
    parts = [("The indicator", found), ("Its source (take it whole with luxalgo_save_script)", source)]
    if session.offers("luxalgo_known"):
        parts.append(("What WATA already knows of it", await read("luxalgo_known", {"slugs": [slug]})))
    return subject_text("the indicator of LuxAlgo's Library", f"{subject.get('label') or slug} ({slug})", "\n\n".join(
        f"{name}:\n{json.dumps(value, ensure_ascii=False, default=str) if not isinstance(value, str) else value}"
        for name, value in parts))


def _seconds(moment: str) -> float:
    """A moment as the store writes it (YYYY-MM-DDTHH:MM:SSZ), in seconds."""
    return datetime.strptime(moment, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


def _task_line(task: dict | None) -> dict | None:
    """What a list of conversations says of a task: its state and budget, not its conclusion."""
    if not task:
        return None
    return {key: task.get(key) for key in ("state", "budget", "spent_usd", "backtests", "deadline", "ended_because",
                                           "question")}


def _identity(entry: dict) -> tuple:
    """What tells two things attached apart: a script's versions, an incident's moments are each their own."""
    return entry.get("kind"), entry.get("id"), entry.get("version") or entry.get("at")


def _prompt(prompt) -> str:
    if not isinstance(prompt, str) or not prompt.strip():
        raise InvalidRequest("Ask the agent something")
    if len(prompt) > MAX_PROMPT:
        raise InvalidRequest(f"A request is {MAX_PROMPT:,} characters at most")
    return prompt.strip()


def _title(prompt: str) -> str:
    first = prompt.strip().splitlines()[0]
    return first if len(first) <= MAX_TITLE else f"{first[:MAX_TITLE - 1]}…"


def build_agent(ctx, ops=None, luxalgo=None) -> AgentService:
    """
    The agent over the admin's own services: their research side (as the MCP
    endpoint has it), the key opened on demand, what the admin lets it read
    of itself (``ops``: its health, its logs; src/admin/context.py agent_of)
    and LuxAlgo's tools when they are on (``luxalgo``).
    """
    from src.admin.agent.verdicts import VerdictStore
    from src.admin.mcp_server.calls import CallLog
    from src.admin.mcp_server.facade import ResearchServices

    settings = ctx.settings
    store = ConversationStore(settings.agent_path)
    verdicts = VerdictStore(settings.agent_path)
    backtests = ctx.backtests if settings.backtests_enabled else None
    memory = getattr(ctx, "agent_memory", None) if settings.ai_agent.memory else None
    proposals = Proposals(store, ctx.ta_scripts, backtests=backtests, origins=getattr(ctx, "script_origins", None))
    # Its calls written apart: System › Claude keeps Claude Code's.
    research = replace(ResearchServices.from_context(ctx),
                       calls=CallLog(os.path.join(settings.log_path, "wata-admin", "agent.jsonl")))

    async def key() -> str | None:
        value = await ctx.vault.open("anthropic.api") if ctx.vault is not None else None
        return (value or {}).get("key")

    return AgentService(
        settings=settings.ai_agent, store=store, provider=claude.ClaudeProvider(key),
        tools=AgentTools(research, proposals, ops=ops, memory=memory, verdicts=verdicts,
                         actions=settings.ai_agent.action_proposals, luxalgo=luxalgo), proposals=proposals,
        backtests=backtests, scripts=ctx.ta_scripts, key=key,
        publish=ctx.events.publish, queries=research.queries, memory=memory, verdicts=verdicts,
    )
