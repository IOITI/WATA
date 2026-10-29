"""
The AI agent's memory (``ctx.agent_memory``): entries kept between its
conversations, each one thing worth knowing in a later one. The agent saves
and forgets them (src/admin/agent/tools.py), the operator does the same from
the Memory page (src/admin/routers/agent.py), and Claude Code reads them
through the MCP endpoint (src/admin/mcp_server/tools/memory.py), which only
reads: outside src/admin/agent/ so that the endpoint imports this and never
the agent.

Its files, under ``admin.persistant.agent_path``:

    memory/entries.json   every entry and the number of the last change (seq), written whole
    memory/journal.jsonl  each change, with the entry before and after it: only ever appended to
    memory/pause.json     the operator's pause of the agent's changes

A name never builds a path. What an entry says goes to Anthropic with every
conversation, so text that looks like a secret is refused, whoever wrote it.
The agent changes neither a locked entry nor any while its changes are
paused: both are the operator's, who may change anything. Every change is
kept in the journal with the entry as it was, so that any version comes back.
"""

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from src.admin.errors import InvalidRequest, NotPossible
from src.logging_helper.redaction import redact

logger = logging.getLogger(__name__)

FOLDER = "memory"
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,59}$")
# preference: what the operator wants or refuses; lesson: how WATA, its data and its tools behave; finding: what a
# study concluded, with the runs, trades and days it rests on; reference: where something worth going back to is.
KINDS = ("preference", "lesson", "finding", "reference")
MAX_ENTRIES = 200
MAX_DESCRIPTION = 200
MAX_BODY = 4000
OPERATOR, AGENT = "operator", "agent"
# What a change is, as the journal says it.
CREATED, CHANGED, RENAMED, FORGOTTEN, RESTORED, LOCKED, UNLOCKED, PAUSED, RESUMED = (
    "created", "changed", "renamed", "forgotten", "restored", "locked", "unlocked", "paused", "resumed")
# A version of an entry: what the journal keeps before and after a change, and what is brought back.
VERSION = ("kind", "description", "body", "locked")
# The lines of the journal a page reads at once, the newest.
HISTORY_LINES = 500
READER_CHARS = 80


class UnknownEntry(LookupError):
    """No entry has that name: never saved, forgotten since, or a malformed name."""


@dataclass(frozen=True)
class Author:
    """Who changes the memory: the operator, from an address, or the agent, in one of its conversations."""

    who: str
    address: str | None = None
    conversation: str | None = None
    request: int | None = None

    @classmethod
    def operator(cls, address: str | None) -> "Author":
        return cls(OPERATOR, address=address)

    @classmethod
    def agent(cls, conversation: str, request: int | None = None) -> "Author":
        return cls(AGENT, conversation=conversation, request=request)

    @property
    def is_agent(self) -> bool:
        return self.who == AGENT

    def said(self) -> dict:
        return {"by": self.who, "address": self.address, "conversation": self.conversation, "request": self.request}


def stamp(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _line(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"


def _read_json(path: str):
    try:
        with open(path, encoding="utf-8") as in_file:
            return json.load(in_file)
    except FileNotFoundError:
        return None


def _write_json(path: str, value) -> None:
    partial = f"{path}.part"
    with open(partial, "w", encoding="utf-8") as out_file:
        json.dump(value, out_file, ensure_ascii=False, indent=1)
    os.replace(partial, path)


def _version(entry: dict | None) -> dict | None:
    return None if entry is None else {key: entry.get(key) for key in VERSION}


def listed(entry: dict) -> dict:
    """An entry as a list says it: what it is and who keeps it, without its text."""
    return {key: entry.get(key) for key in (
        "name", "kind", "description", "locked", "created_at", "created_by", "updated_at", "updated_by",
    )}


def _kind_order(kind) -> int:
    return KINDS.index(kind) if kind in KINDS else len(KINDS)


def _existing_name(name) -> str:
    if not isinstance(name, str) or not NAME.match(name):
        raise UnknownEntry(f"No memory entry {name!r}")
    return name


def _new_name(name) -> str:
    if not isinstance(name, str) or not NAME.match(name):
        raise InvalidRequest("A memory entry's name is 2 to 60 lowercase letters, digits and dashes, starting with a "
                             "letter or a digit")
    return name


def checked(kind, description, body) -> dict:
    """What an entry says, refused with a sentence when it is not one, or holds what looks like a secret."""
    if kind not in KINDS:
        raise InvalidRequest(f"A memory entry's kind is one of {', '.join(KINDS)}")
    if not isinstance(description, str) or not description.strip():
        raise InvalidRequest("A memory entry's description is a line that says when it matters")
    description = description.strip()
    if len(description.splitlines()) > 1 or len(description) > MAX_DESCRIPTION:
        raise InvalidRequest(f"A memory entry's description is one line of {MAX_DESCRIPTION} characters at most")
    if not isinstance(body, str) or not body.strip():
        raise InvalidRequest("A memory entry's text is empty")
    body = body.strip()
    if len(body) > MAX_BODY:
        raise InvalidRequest(f"A memory entry's text is {MAX_BODY:,} characters at most: split it, or keep only what a "
                             "later conversation needs")
    for part, text in (("description", description), ("text", body)):
        if redact(text) != text:
            raise InvalidRequest(f"The entry's {part} holds what looks like a secret (a key, a token or a password): "
                                 "the memory goes to Anthropic with every conversation, so it keeps none")
    return {"kind": kind, "description": description, "body": body}


def _check_revision(current: dict, revision) -> None:
    if revision is None:
        raise InvalidRequest(f"{current['name']!r} exists: read it first, then give the revision you read to change it")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision != current["revision"]:
        raise NotPossible(f"{current['name']!r} changed since it was read (it is at revision {current['revision']}): "
                          "read it again")


class MemoryStore:
    def __init__(self, root: str, clock=time.time):
        self._root = root
        self._clock = clock
        # The agent's requests, the MCP endpoint and the routes reach it from the event loop and the thread pool.
        self._lock = threading.RLock()

    @property
    def root(self) -> str:
        return self._root

    def _path(self, name: str) -> str:
        return os.path.join(self._root, name)

    # ── what it holds ──

    def _state(self) -> dict:
        state = _read_json(self._path("entries.json"))
        if not isinstance(state, dict):
            return {"seq": 0, "entries": {}}
        return {"seq": int(state.get("seq") or 0), "entries": dict(state.get("entries") or {})}

    def entries(self, kind: str | None = None) -> list[dict]:
        """Every entry, whole: by kind, in the order of KINDS, then by name."""
        entries = [entry for entry in self._state()["entries"].values() if kind is None or entry.get("kind") == kind]
        return sorted(entries, key=lambda entry: (_kind_order(entry.get("kind")), entry["name"]))

    def entry(self, name: str) -> dict:
        found = self._state()["entries"].get(_existing_name(name))
        if found is None:
            raise UnknownEntry(f"No memory entry {name!r}")
        return found

    def seq(self) -> int:
        """The number of the last change: a conversation is told what changed after the one it last saw."""
        return self._state()["seq"]

    def pause(self) -> dict | None:
        """The operator's pause of the agent's changes: since when and from where, or None."""
        return _read_json(self._path("pause.json"))

    def _journal(self) -> list[dict]:
        try:
            with open(self._path("journal.jsonl"), encoding="utf-8") as in_file:
                lines = in_file.read().splitlines()
        except FileNotFoundError:
            return []
        values = []
        for number, text in enumerate(lines, 1):
            if not text.strip():
                continue
            try:
                values.append(json.loads(text))
            except ValueError:
                # A line the admin was stopped in the middle of.
                logger.warning(f"The AI agent's memory journal: line {number} is not whole, skipped")
        return values

    def history(self, name: str | None = None, limit: int = HISTORY_LINES) -> list[dict]:
        """The changes, newest first; only those of the entry ``name`` (under it, or renamed from it) when given."""
        lines = self._journal()
        if name is not None:
            lines = [line for line in lines if name in (line.get("name"), line.get("from"))]
        return list(reversed(lines))[:limit]

    def changes_since(self, seq: int, conversation: str | None = None) -> list[dict]:
        """What changed after ``seq``, but what the agent changed itself in ``conversation``."""
        return [line for line in self._journal() if (line.get("seq") or 0) > seq
                and not (line.get("by") == AGENT and conversation and line.get("conversation") == conversation)]

    def export(self) -> dict:
        """Every entry and the pause, as the page downloads them."""
        return {"exported_at": stamp(self._clock()), "entries": self.entries(), "pause": self.pause()}

    # ── its changes ──

    def _commit(self, state: dict, author: Author, action: str, name: str | None, *, before: dict | None = None,
                after: dict | None = None, **said) -> dict:
        state["seq"] += 1
        line = {"seq": state["seq"], "at": stamp(self._clock()), "action": action, "name": name, **author.said(),
                "before": before, "after": after, **said}
        os.makedirs(self._root, exist_ok=True)
        # The entries first: stopped in between, a change misses its line of history, never the other way round.
        _write_json(self._path("entries.json"), state)
        with open(self._path("journal.jsonl"), "a", encoding="utf-8") as out_file:
            out_file.write(_line(line))
        return line

    def _check_agent(self, current: dict | None) -> None:
        pause = self.pause()
        if pause:
            raise NotPossible(f"The operator paused your memory's changes (since {pause.get('since')}): say in your "
                              "answer what you would save or forget")
        if current is not None and current.get("locked"):
            raise NotPossible(f"{current['name']!r} is locked by the operator: read it, never change it, and say in "
                              "your answer what you would change")

    def save(self, author: Author, name: str, *, kind: str, description: str, body: str, revision: int | None = None,
             locked: bool | None = None, new_name: str | None = None, create: bool = False) -> tuple[dict, str | None]:
        """
        An entry created, or changed from the revision its author read (a change
        without it, or from an older one, is refused: nothing written since is
        lost unread). ``create``: a new name only, as the page's form asks.
        ``locked`` and ``new_name`` are the operator's: the agent sets no lock
        and renames nothing. Gives the entry and what was done, None when it
        already said that.
        """
        version = checked(kind, description, body)
        name = _new_name(name)
        renamed = _new_name(new_name) if new_name is not None and new_name != name and not author.is_agent else None
        now = stamp(self._clock())
        with self._lock:
            state = self._state()
            current = state["entries"].get(name)
            if author.is_agent:
                self._check_agent(current)
            if current is None:
                if revision is not None:
                    raise UnknownEntry(f"No memory entry {name!r}: forgotten since it was read")
                if len(state["entries"]) >= MAX_ENTRIES:
                    raise NotPossible(f"The memory holds {MAX_ENTRIES} entries, the most it keeps: merge or forget "
                                      "some first")
                entry = {"name": name, **version, "locked": bool(locked) and not author.is_agent, "revision": 1,
                         "created_at": now, "created_by": author.who, "created_in": author.conversation,
                         "updated_at": now, "updated_by": author.who, "updated_in": author.conversation,
                         "read_at": None, "read_by": None}
                state["entries"][name] = entry
                self._commit(state, author, CREATED, name, after=_version(entry))
                return entry, CREATED
            if create:
                raise NotPossible(f"An entry is named {name!r} already")
            _check_revision(current, revision)
            if renamed is not None and renamed in state["entries"]:
                raise NotPossible(f"An entry is named {renamed!r} already")
            lock = current.get("locked", False) if locked is None or author.is_agent else bool(locked)
            if renamed is None and _version(current) == {**version, "locked": lock}:
                return current, None
            entry = {**current, **version, "name": renamed or name, "locked": lock, "revision": current["revision"] + 1,
                     "updated_at": now, "updated_by": author.who, "updated_in": author.conversation}
            del state["entries"][name]
            state["entries"][entry["name"]] = entry
            action = RENAMED if renamed else CHANGED
            said = {"from": name} if renamed else {}
            self._commit(state, author, action, entry["name"], before=_version(current), after=_version(entry), **said)
            return entry, action

    def forget(self, author: Author, name: str, *, revision: int | None, why: str | None = None) -> dict:
        """An entry forgotten from the revision its author read: it stays in the journal, and may come back."""
        name = _existing_name(name)
        with self._lock:
            state = self._state()
            current = state["entries"].get(name)
            if current is None:
                raise UnknownEntry(f"No memory entry {name!r}")
            if author.is_agent:
                self._check_agent(current)
            _check_revision(current, revision)
            del state["entries"][name]
            said = {"why": why.strip()[:500]} if isinstance(why, str) and why.strip() else {}
            self._commit(state, author, FORGOTTEN, name, before=_version(current), **said)
            return current

    def restore(self, author: Author, seq: int) -> dict:
        """The entry as the journal's line ``seq`` left it, back (as it was before, for the line that forgot it)."""
        if author.is_agent:
            raise NotPossible("Only the operator brings back a version of an entry")
        with self._lock:
            lines = self._journal()
            line = next((line for line in lines if line.get("seq") == seq), None)
            version = (line or {}).get("after") or (line or {}).get("before")
            if not line or not line.get("name") or not version:
                raise NotPossible("That line of the history holds no version of an entry to bring back")
            name = line["name"]
            checked(version.get("kind"), version.get("description"), version.get("body"))
            state = self._state()
            current = state["entries"].get(name)
            now = stamp(self._clock())
            if current is None:
                if len(state["entries"]) >= MAX_ENTRIES:
                    raise NotPossible(f"The memory holds {MAX_ENTRIES} entries, the most it keeps: forget one first")
                # A forgotten entry comes back as whoever wrote it: its creation, while the history still has it.
                born = next((old for old in reversed(lines) if old.get("name") == name and old.get("action") == CREATED),
                            None) or {}
                entry = {"name": name, **version, "locked": bool(version.get("locked")), "revision": 1,
                         "created_at": born.get("at") or now, "created_by": born.get("by") or author.who,
                         "created_in": born.get("conversation"), "read_at": None, "read_by": None}
            else:
                entry = {**current, **version, "locked": bool(version.get("locked")), "revision": current["revision"] + 1}
            entry.update(updated_at=now, updated_by=author.who, updated_in=None)
            state["entries"][name] = entry
            self._commit(state, author, RESTORED, name, before=_version(current), after=_version(entry), restored=seq)
            return entry

    def set_lock(self, author: Author, name: str, locked: bool) -> dict:
        """The operator's lock: the agent reads a locked entry and never changes it."""
        if author.is_agent:
            raise NotPossible("Only the operator locks an entry")
        name = _existing_name(name)
        with self._lock:
            state = self._state()
            current = state["entries"].get(name)
            if current is None:
                raise UnknownEntry(f"No memory entry {name!r}")
            if bool(current.get("locked")) == bool(locked):
                return current
            entry = {**current, "locked": bool(locked), "revision": current["revision"] + 1,
                     "updated_at": stamp(self._clock()), "updated_by": author.who, "updated_in": None}
            state["entries"][name] = entry
            self._commit(state, author, LOCKED if locked else UNLOCKED, name, before=_version(current),
                         after=_version(entry))
            return entry

    def set_pause(self, author: Author, paused: bool) -> dict | None:
        """The operator's pause of the agent's changes, at once: it reads its memory meanwhile."""
        if author.is_agent:
            raise NotPossible("Only the operator pauses the memory's changes")
        with self._lock:
            current = self.pause()
            if bool(current) == bool(paused):
                return current
            path = self._path("pause.json")
            value = None
            os.makedirs(self._root, exist_ok=True)
            if paused:
                value = {"since": stamp(self._clock()), "by": author.address}
                _write_json(path, value)
            else:
                try:
                    os.remove(path)
                except FileNotFoundError:
                    pass
            self._commit(self._state(), author, PAUSED if paused else RESUMED, None)
            return value

    def mark_read(self, names, reader: str) -> None:
        """The entries read, when and by whom: not a change (no revision, no line of the journal)."""
        with self._lock:
            state = self._state()
            read = [state["entries"][name] for name in names if name in state["entries"]]
            if not read:
                return
            now = stamp(self._clock())
            for entry in read:
                entry.update(read_at=now, read_by=str(reader)[:READER_CHARS])
            os.makedirs(self._root, exist_ok=True)
            _write_json(self._path("entries.json"), state)

    def prune(self, keep_days: int) -> int:
        """The journal's lines older than ``keep_days`` removed: the entries stay as they are."""
        limit = stamp(self._clock() - keep_days * 86400)
        with self._lock:
            lines = self._journal()
            kept = [line for line in lines if (line.get("at") or "") >= limit]
            if len(kept) == len(lines):
                return 0
            path = self._path("journal.jsonl")
            partial = f"{path}.part"
            with open(partial, "w", encoding="utf-8") as out_file:
                out_file.writelines(_line(line) for line in kept)
            os.replace(partial, path)
            return len(lines) - len(kept)


def memory_of(agent_path: str) -> MemoryStore:
    """The agent's memory beside its conversations."""
    return MemoryStore(os.path.join(agent_path, FOLDER))
