"""
The AI agent's work said on Telegram once it is written (the teller): a request
the operator asked with "Tell me on Telegram", a research task's end, the daily
debrief, the morning briefing, an alert's triage, and a line that needs no
conversation (a day the US market is closed).

Each is noted in ``tell.json`` beside the conversations, and said once its
request, or its task, ended: its first lines, links as their words and marks
left out, cleaned as log lines are, with the way to the whole of it on the
admin. A failure to send is tried again at the next tick; a note older than
KEPT_DAYS, or whose conversation is gone, goes unsaid. It lives outside
src/admin/agent/, which never reaches the notifier (tests/test_agent_boundary.py).
"""

import asyncio
import json
import logging
import os
import re
import secrets
import threading
import time
from datetime import date

from starlette.concurrency import run_in_threadpool

from src.admin.agent.store import UnknownConversation, stamp
from src.admin.agent.transcript import UNDER_WAY
from src.logging_helper.redaction import redact

logger = logging.getLogger(__name__)

FILE = "tell.json"
TICK_SECONDS = 20
KEPT_DAYS = 7
# The questions told, the newest kept: a question is told once.
KEPT_QUESTIONS = 200
# What Telegram is given of an answer: its first lines, at most.
SAID_CHARS = 700
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MARKS = re.compile(r"(\*\*|__|`)")
_HEADING = re.compile(r"^#{1,6}\s+")
# A table's rule under its header, or a rule between parts: nothing to say on Telegram.
_RULE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
# What is told: the answer to one request, a research task's end, the daily debrief of a day, the morning briefing
# of a day, an alert's triage, and a line that needs no conversation (a day the US market is closed).
ANSWER, TASK, DEBRIEF, BRIEFING, TRIAGE, LINE = "answer", "task", "debrief", "briefing", "triage", "line"
# A signal rule's go-live dossier, asked once its readiness is met; a routine's research task, at its end.
DOSSIER, ROUTINE = "dossier", "routine"
KINDS = (ANSWER, TASK, DEBRIEF, BRIEFING, TRIAGE, LINE, DOSSIER, ROUTINE)
# How a day's account is said: its mark, and what it is.
_DAY_KINDS = {DEBRIEF: ("📝", "debrief of"), BRIEFING: ("☀️", "briefing for")}


def _plain_lines(text: str) -> str:
    """Markdown in plain lines: a heading as its words, a table's row as its cells, its rule left out."""
    lines = []
    for line in text.splitlines():
        if _RULE.match(line):
            continue
        row = line.strip()
        if len(row) > 1 and row.startswith("|") and row.endswith("|"):
            line = " · ".join(cell.strip() for cell in row[1:-1].split("|"))
        lines.append(_HEADING.sub("", line))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines))


def said_of(text: str) -> str:
    """An answer's first lines for Telegram: plain lines, its links as their words, its marks left out, cut at a
    line's end."""
    plain = _MARKS.sub("", _LINK.sub(r"\1", _plain_lines(text))).strip()
    if len(plain) <= SAID_CHARS:
        return plain
    cut = plain[:SAID_CHARS]
    end = max(cut.rfind("\n"), cut.rfind(". "))
    return (cut[:end + 1] if end > SAID_CHARS // 2 else cut).rstrip() + " …"


def _ended(sentence: str) -> str:
    """``sentence`` with its full stop, unless it ends on its own (a title cut short, a question, a reason's stop)."""
    return sentence if sentence.endswith((".", "…", "?", "!")) else f"{sentence}."


def _waiting(conversation: dict, request: int) -> str:
    """The proposals of a request still waiting for the operator, said in a line."""
    asked = next((entry for entry in conversation.get("requests") or [] if entry.get("index") == request), {})
    titles = [proposal.get("title") for proposal in conversation.get("proposals") or []
              if proposal.get("id") in (asked.get("proposals") or []) and proposal.get("status") == "proposed"]
    return f"\n\nWaiting for you: {_ended('; '.join(title for title in titles if title))}" if titles else ""


def message_of(entry: dict, conversation: dict | None) -> str | None:
    """What Telegram is told of ``entry`` once there is something to tell, else None (still under way)."""
    if entry["kind"] == LINE:
        return entry.get("text")
    title = conversation.get("title") or "a conversation"
    where = _ended(f"AI agent › {title}")
    whole = _ended(f"The whole of it: AI agent › {title}")
    kind = entry["kind"]
    if kind in (TASK, ROUTINE):
        task = conversation.get("task") or {}
        if task.get("state") in (None, "working"):
            return None
        what = f"🔁 Routine “{entry.get('label') or title}”" if kind == ROUTINE else f"🔬 Research task “{title}”"
        conclusion = (task.get("conclusion") or {}).get("conclusion")
        if task.get("state") == "done" and conclusion:
            return f"{what} is done:\n\n{said_of(conclusion)}\n\n{whole}"
        return f"{what} ended: {_ended(task.get('ended_because') or 'it stopped')} {where}"
    request = entry.get("request") or 0
    if conversation.get("status") in UNDER_WAY:
        return None
    asked = next((found for found in conversation.get("requests") or [] if found.get("index") == request), None)
    if asked is None or asked.get("status") in UNDER_WAY:
        return None
    text = (asked.get("answer") or {}).get("text")
    if kind in _DAY_KINDS:
        mark, what = _DAY_KINDS[kind]
        when = date.fromisoformat(entry["label"]).strftime("%a %d %b")
        if text:
            return f"{mark} WATA's {what} {when}:\n\n{said_of(text)}\n\nThe whole of it: AI agent › Activity."
        why = conversation.get("error") or "it ended without an answer"
        return f"{mark} WATA's {what} {when} could not be written: {_ended(why)} AI agent › Activity."
    why = _ended(str(asked.get("detail") or conversation.get("error") or asked.get("status")))
    if kind == DOSSIER:
        if text:
            return (f"🧾 WATA's go-live dossier of {entry.get('label') or title}:\n\n{said_of(text)}"
                    f"{_waiting(conversation, request)}\n\n{whole}")
        return f"🧾 WATA's go-live dossier of {entry.get('label') or title} could not be written: {why} {where}"
    if kind == TRIAGE:
        if text:
            return (f"🩺 WATA's triage of {entry.get('label') or title}:\n\n{said_of(text)}"
                    f"{_waiting(conversation, request)}\n\n{whole}")
        return f"🩺 WATA's triage of {entry.get('label') or title} could not be written: {why} {where}"
    if text:
        return f"💬 WATA's AI agent answered “{title}”:\n\n{said_of(text)}{_waiting(conversation, request)}\n\n{whole}"
    return f"💬 WATA's AI agent could not answer “{title}”: {why} {where}"


class AgentTeller:
    def __init__(self, *, agent, path: str, notify, clock=time.time):
        self._agent = agent
        self._path = path
        self._notify = notify
        self._clock = clock
        # Notes are added from the routes and the agent's schedules, and read by the tick in the thread pool.
        self._lock = threading.Lock()

    def _read(self) -> list[dict]:
        try:
            with open(self._path, encoding="utf-8") as kept:
                found = json.load(kept).get("notes")
        except (OSError, ValueError, AttributeError):
            return []
        return found if isinstance(found, list) else []

    def _write(self, notes: list[dict]) -> None:
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        partial = f"{self._path}.part"
        with open(partial, "w", encoding="utf-8") as out_file:
            json.dump({"notes": notes, "questions": self._told_questions()}, out_file, ensure_ascii=False)
        os.replace(partial, self._path)

    def add(self, conversation_id: str | None, kind: str, *, request: int | None = None, label: str | None = None,
            text: str | None = None) -> dict:
        """
        Say on Telegram what becomes of this conversation's request (or task)
        once it ends; a line (``text``) needs no conversation, and is said at
        the next tick.
        """
        if kind not in KINDS or (kind == LINE) != (conversation_id is None):
            raise ValueError(f"No kind of note {kind!r} for {conversation_id!r}")
        note = {"id": secrets.token_hex(4), "conversation": conversation_id, "kind": kind, "request": request,
                "label": label, "at": stamp(self._clock())}
        if kind == LINE:
            note["text"] = text
        with self._lock:
            notes = [found for found in self._read()
                     if kind == LINE or not (found.get("conversation") == conversation_id and found.get("kind") == kind
                                             and found.get("request") == request)]
            self._write(notes + [note])
        return note

    def notes(self) -> list[dict]:
        with self._lock:
            return self._read()

    def _settle(self, told: set[str]) -> None:
        """The notes told, or gone stale, removed; those added meanwhile kept."""
        if not told:
            return
        with self._lock:
            self._write([note for note in self._read() if note.get("id") not in told])

    async def keep(self) -> None:
        """Until cancelled: what ended since the last look, told."""
        while True:
            try:
                await self.tell_due()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"The AI agent's Telegram notes could not be looked at: {e}")
            await asyncio.sleep(TICK_SECONDS)

    def _told_questions(self) -> list[str]:
        try:
            with open(self._path, encoding="utf-8") as kept:
                found = json.load(kept).get("questions")
        except (OSError, ValueError, AttributeError):
            return []
        return found if isinstance(found, list) else []

    def _question_told(self, key: str) -> None:
        with self._lock:
            notes = self._read()
            told = [*self._told_questions(), key][-KEPT_QUESTIONS:]
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            partial = f"{self._path}.part"
            with open(partial, "w", encoding="utf-8") as out_file:
                json.dump({"notes": notes, "questions": told}, out_file, ensure_ascii=False)
            os.replace(partial, self._path)

    async def tell_questions(self) -> list[str]:
        """Each question a research task asks the operator, said once."""
        waiting = getattr(self._agent, "waiting_questions", None)
        if waiting is None:
            return []
        told, sent = set(await run_in_threadpool(self._told_questions)), []
        for question in await run_in_threadpool(waiting):
            key = f"{question['conversation']}@{question.get('asked_at')}"
            if key in told:
                continue
            choices = question.get("choices") or []
            where = _ended(f"AI agent › {question.get('title')}")
            message = redact(f"❓ WATA's research task “{question.get('title')}” asks:\n\n{question.get('question')}"
                             + (f"\n\nChoices: {' / '.join(choices)}" if choices else "")
                             + f"\n\nThe task waits for your answer, in {where}")
            try:
                await self._notify(message)
            except Exception as e:
                logger.warning(f"The question of {question['conversation']} could not be said on Telegram: {e}")
                continue
            await run_in_threadpool(self._question_told, key)
            sent.append(message)
        return sent

    async def tell_due(self) -> list[str]:
        """Each note whose request or task ended, told once, and each question asked; what was sent."""
        sent = await self.tell_questions()
        return sent + await self._tell_notes()

    async def _tell_notes(self) -> list[str]:
        notes = await run_in_threadpool(self.notes)
        oldest = stamp(self._clock() - KEPT_DAYS * 86400)
        settled, sent = set(), []
        for note in notes:
            if (note.get("at") or "") < oldest:
                logger.info(f"The AI agent's note on {note.get('conversation')} was never told: dropped")
                settled.add(note["id"])
                continue
            try:
                conversation = (None if note["kind"] == LINE
                                else await run_in_threadpool(self._agent.conversation, note["conversation"]))
            except UnknownConversation:
                settled.add(note["id"])
                continue
            message = message_of(note, conversation)
            if message is None:
                continue
            message = redact(message)
            try:
                await self._notify(message)
            except Exception as e:
                logger.warning(f"The AI agent's note on {note['conversation']} could not be said on Telegram: {e}")
                continue
            settled.add(note["id"])
            sent.append(message)
        await run_in_threadpool(self._settle, settled)
        return sent
