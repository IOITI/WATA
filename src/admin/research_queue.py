"""
The research queue: hypotheses waiting to be tried, the operator's and the AI agent's, which the autopilot routine works
through one at a time (src/admin/agent_routines.py, kind "autopilot").

An item is a hypothesis the data can refute, its family of ideas, what it must show (criteria, as an experiment sets
them) and the days it came from, with who filed it: the operator (from the Research queue page, or from a run's
autopsy, which it names as its ``source``) or a research task of the agent (``agent:<conversation>``, the next
hypotheses its ``task_done`` names). Its state: waiting, taken by a task (its conversation), done (the experiment it
became, and what the task concluded), dropped.

Order (``ordered``): the operator's items first, in the operator's order; then the others, oldest first, those of a
family past many trials with no kept conclusion that held on later days put last (the track record,
src/admin/track_record.py): the next try of an idea that keeps failing waits for the others.

Kept in ``research_queue.json`` beside the agent's conversations, written whole with a revision: a change made
meanwhile is never overwritten (409). The agent may keep ``AGENT_WAITING`` items waiting at most, ``PER_CONVERSATION``
from one conversation, the same hypothesis filed again by one conversation being the one already there. Nothing
here trades, nor starts anything by itself: the autopilot asks for a task.
"""

import hashlib
import json
import os
import re
import secrets
import threading
import time

from src.admin.agent.store import stamp
from src.admin.errors import InvalidRequest, NotPossible
from src.admin.run_verdict import CRITERIA_FIGURES
from src.research.experiments import checked_criteria, checked_days

FILE = "research_queue.json"
ITEM_ID = re.compile(r"^q[0-9a-f]{8}$")
WAITING, TAKEN, DONE, DROPPED = "waiting", "taken", "done", "dropped"
STATES = (WAITING, TAKEN, DONE, DROPPED)
OPERATOR = "operator"
# What the agent may keep waiting, in all and from one conversation.
AGENT_WAITING, PER_CONVERSATION = 20, 3
# Items kept once done or dropped, the newest.
KEPT_SETTLED = 100
MAX_TITLE, MAX_TEXT = 120, 2000
# A family past this many trials with no kept conclusion that held later waits behind the others.
TIRED_FAMILY_TRIALS = 100
# A hypothesis taken whose task never started (the admin stopped in between) waits again after this long.
STARTING_SECONDS = 10 * 60
MAX_SOURCE = 80


class QueueConflict(Exception):
    """The queue changed since it was read: read it again."""


def is_operators(item: dict) -> bool:
    return item.get("by") == OPERATOR


def tired_families(record: dict | None) -> set[str]:
    """The families tried past ``TIRED_FAMILY_TRIALS`` with no kept conclusion that held on later days."""
    found = set()
    for entry in (record or {}).get("families") or []:
        if entry.get("trials", 0) >= TIRED_FAMILY_TRIALS and not (entry.get("forward") or {}).get("held"):
            found.add(entry.get("family"))
    return found


def ordered(items: list[dict], tired: set[str] | None = None) -> list[dict]:
    """The waiting items in the order they are taken (see the module's docstring), then the others, newest first."""
    tired = tired or set()
    waiting = [item for item in items if item.get("state") == WAITING]
    operators = sorted((item for item in waiting if is_operators(item)), key=lambda item: (item.get("order", 0), item["created_at"]))
    others = sorted((item for item in waiting if not is_operators(item)),
                    key=lambda item: (item.get("family") in tired, item["created_at"]))
    rest = sorted((item for item in items if item.get("state") != WAITING), key=lambda item: item.get("updated_at") or item["created_at"],
                  reverse=True)
    return [*operators, *others, *rest]


def checked_item(fields: dict, today) -> dict:
    """An item's words, criteria and days, checked as an experiment's are (src/research/experiments.py)."""
    title = str(fields.get("title") or "").strip()
    hypothesis = str(fields.get("hypothesis") or "").strip()
    family = str(fields.get("family") or "").strip()
    if not title or not hypothesis or not family:
        raise InvalidRequest("A hypothesis has a title, the hypothesis itself and its family")
    if len(title) > MAX_TITLE or any(len(str(fields.get(name) or "")) > MAX_TEXT for name in ("hypothesis", "rationale")):
        raise InvalidRequest(f"A title is {MAX_TITLE} characters at most, a hypothesis {MAX_TEXT}")
    try:
        criteria = checked_criteria(fields.get("criteria"))
        motivated_by = checked_days(fields.get("motivated_by"), today)
    except ValueError as e:
        raise InvalidRequest(str(e)) from None
    unknown = [criterion["figure"] for criterion in criteria if criterion["figure"] not in CRITERIA_FIGURES]
    if unknown:
        raise InvalidRequest(f"No figure {unknown[0]!r}: a criterion is one of {', '.join(CRITERIA_FIGURES)}")
    return {
        "title": title, "hypothesis": hypothesis, "family": family[:80],
        "rationale": str(fields.get("rationale") or "").strip() or None,
        "criteria": criteria, "motivated_by": motivated_by,
    }


class ResearchQueue:
    def __init__(self, root: str, clock=time.time):
        self._path = os.path.join(root, FILE)
        self._clock = clock
        self._lock = threading.Lock()

    def _read(self) -> list[dict]:
        try:
            with open(self._path, encoding="utf-8") as kept:
                found = json.load(kept).get("items")
        except (OSError, ValueError, AttributeError):
            return []
        return found if isinstance(found, list) else []

    @staticmethod
    def _revision(items: list[dict]) -> str:
        return hashlib.sha256(json.dumps(items, sort_keys=True).encode()).hexdigest()[:16]

    def load(self, tired: set[str] | None = None) -> dict:
        items = self._read()
        return {"revision": self._revision(items), "items": ordered(items, tired)}

    def _write(self, items: list[dict]) -> str:
        settled = sorted((item for item in items if item.get("state") in (DONE, DROPPED)),
                         key=lambda item: item.get("updated_at") or item["created_at"], reverse=True)
        kept_ids = {item["id"] for item in settled[:KEPT_SETTLED]}
        items[:] = [item for item in items if item.get("state") not in (DONE, DROPPED) or item["id"] in kept_ids]
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        partial = f"{self._path}.part"
        with open(partial, "w", encoding="utf-8") as out_file:
            json.dump({"items": items}, out_file, ensure_ascii=False, indent=1)
        os.replace(partial, self._path)
        return self._revision(items)

    def _changed(self, revision: str | None, change) -> dict:
        with self._lock:
            items = self._read()
            if revision is not None and revision != self._revision(items):
                raise QueueConflict("The research queue changed since this page read it: read it again")
            result = change(items)
            revision = self._write(items)
        return {"revision": revision, **(result or {})}

    @staticmethod
    def find(items: list[dict], item_id: str) -> dict:
        if not isinstance(item_id, str) or not ITEM_ID.match(item_id):
            raise InvalidRequest(f"No hypothesis {item_id!r} in the queue")
        found = next((item for item in items if item["id"] == item_id), None)
        if found is None:
            raise InvalidRequest(f"No hypothesis {item_id!r} in the queue")
        return found

    def add(self, fields: dict, by: str, *, today, conversation: str | None = None, revision: str | None = None,
            source: str | None = None) -> dict:
        """
        A hypothesis filed: by the operator (first, last of theirs; ``source`` what it came from, a run's autopsy), or
        by the agent within its caps, once (filed again by the same conversation, the one already there).
        """
        checked = checked_item(fields, today)

        def change(items: list[dict]) -> dict:
            if conversation:
                filed = next((item for item in items if item.get("conversation_from") == conversation
                              and item.get("title") == checked["title"]), None)
                if filed is not None:
                    return {"item": filed}
            if by != OPERATOR:
                waiting = [item for item in items if item.get("state") == WAITING and not is_operators(item)]
                if len(waiting) >= AGENT_WAITING:
                    raise NotPossible(f"The queue holds {AGENT_WAITING} of the agent's hypotheses already: "
                                      "they are tried first")
                if conversation and sum(1 for item in items if item.get("conversation_from") == conversation) >= PER_CONVERSATION:
                    raise NotPossible(f"A conversation files {PER_CONVERSATION} hypotheses at most")
            order = 1 + max((item.get("order", 0) for item in items if is_operators(item)), default=0)
            item = {"id": f"q{secrets.token_hex(4)}", **checked, "by": by, "conversation_from": conversation,
                    "source": str(source)[:MAX_SOURCE] if source else None,
                    "state": WAITING, "order": order if by == OPERATOR else 0, "created_at": stamp(self._clock()),
                    "updated_at": None, "taken_at": None, "conversation": None, "experiment": None, "conclusion": None}
            items.append(item)
            return {"item": item}
        return self._changed(revision, change)

    def edit(self, item_id: str, revision: str, *, fields: dict | None = None, state: str | None = None,
             move: int | None = None, today=None) -> dict:
        """The operator's change: its words, its state (dropped, or waiting again), or its place among theirs."""
        def change(items: list[dict]) -> dict:
            item = self.find(items, item_id)
            if fields:
                item.update(checked_item({**item, **fields}, today))
            if state is not None:
                if state not in (WAITING, DROPPED) or item.get("state") == TAKEN:
                    raise NotPossible("A hypothesis being tried is left to its task; another is dropped or waits again")
                item["state"] = state
            if move is not None:
                # Moved among the operator's waiting items; an item of the agent's moved there becomes the operator's.
                theirs = [entry for entry in ordered(items) if entry.get("state") == WAITING and is_operators(entry)
                          and entry is not item]
                place = max(0, min(len(theirs), move))
                theirs.insert(place, item)
                item["by"] = OPERATOR
                for number, entry in enumerate(theirs, start=1):
                    entry["order"] = number
            item["updated_at"] = stamp(self._clock())
            return {"item": item}
        return self._changed(revision, change)

    def remove(self, item_id: str, revision: str) -> dict:
        def change(items: list[dict]) -> dict:
            item = self.find(items, item_id)
            if item.get("state") == TAKEN:
                raise NotPossible("A hypothesis being tried is left to its task")
            items.remove(item)
            return {}
        return self._changed(revision, change)

    def next(self, tired: set[str] | None = None) -> dict | None:
        """The hypothesis the autopilot takes next; None when none waits."""
        return next((item for item in ordered(self._read(), tired) if item.get("state") == WAITING), None)

    def take(self, item_id: str, conversation: str | None) -> dict:
        """Taken by a task (its conversation; None while it starts): it is the task's until it ends."""
        def change(items: list[dict]) -> dict:
            item = self.find(items, item_id)
            if item.get("state") != WAITING:
                raise NotPossible("This hypothesis is not waiting any more")
            now = stamp(self._clock())
            item.update(state=TAKEN, conversation=conversation, taken_at=now, updated_at=now)
            return {"item": item}
        return self._changed(None, change)

    def take_next(self, tired: set[str] | None = None) -> dict | None:
        """The next waiting hypothesis, taken at once for a task about to start (``started`` names it); None: none."""
        def change(items: list[dict]) -> dict:
            item = next((entry for entry in ordered(items, tired) if entry.get("state") == WAITING), None)
            if item is not None:
                now = stamp(self._clock())
                item.update(state=TAKEN, conversation=None, taken_at=now, updated_at=now)
            return {"item": item}
        return self._changed(None, change).get("item")

    def started(self, item_id: str, conversation: str) -> None:
        """The task that took it started: its conversation."""
        def change(items: list[dict]) -> dict:
            item = self.find(items, item_id)
            if item.get("state") == TAKEN:
                item.update(conversation=conversation, updated_at=stamp(self._clock()))
            return {}
        self._changed(None, change)

    def put_back(self, item_id: str) -> None:
        """Its task could not start (paused, without a key, the month spent): it waits again, in its place."""
        def change(items: list[dict]) -> dict:
            item = self.find(items, item_id)
            if item.get("state") == TAKEN:
                item.update(state=WAITING, conversation=None, taken_at=None, updated_at=stamp(self._clock()))
            return {}
        self._changed(None, change)

    def settle(self, conversation: str, *, done: bool, experiment: str | None = None, conclusion: str | None = None) -> dict | None:
        """Its task ended: done with what it became, or (a task that could not start or work) waiting again."""
        def change(items: list[dict]) -> dict:
            item = next((entry for entry in items if entry.get("state") == TAKEN and entry.get("conversation") == conversation), None)
            if item is None:
                return {"item": None}
            if done:
                item.update(state=DONE, experiment=experiment, conclusion=(conclusion or "")[:MAX_TEXT] or None)
            else:
                item.update(state=WAITING, conversation=None)
            item["updated_at"] = stamp(self._clock())
            return {"item": item}
        return self._changed(None, change).get("item")

    def taken(self) -> list[dict]:
        return [item for item in self._read() if item.get("state") == TAKEN]

    def stale_starts(self) -> list[str]:
        """The hypotheses taken whose task never started (no conversation for ``STARTING_SECONDS``): put back."""
        limit = stamp(self._clock() - STARTING_SECONDS)
        stale = [item["id"] for item in self.taken() if not item.get("conversation") and (item.get("taken_at") or "") < limit]
        for item_id in stale:
            self.put_back(item_id)
        return stale
