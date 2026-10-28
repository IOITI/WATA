"""
The AI agent's files, under ``admin.persistant.agent_path``:

    conversations/<id>/meta.json        what the pages list of it, written whole
    conversations/<id>/messages.jsonl   the Claude API transcript, only ever appended to
    conversations/<id>/activity.jsonl   each step with its moments and outcome, only ever appended to
    conversations/<id>/proposals.json   what it proposed and what became of it, written whole
    artefacts.jsonl                     what conversations made: runs, scripts, experiments, proposals
    spend.jsonl                         what each answer of the API cost: the month's total
    pause.json                          the operator's pause of new requests

The transcript is never rewritten: the Claude API keeps a thinking block valid
only while everything before it is sent again as it was, and its cache is a
prefix too. Ids are matched against their pattern before any path is built.
A line cut short by a stop is skipped when read back.
"""

import json
import logging
import os
import re
import secrets
import shutil
import threading
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

CONVERSATION_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")
PROPOSAL_ID = re.compile(r"^p[0-9a-f]{8}$")
CONVERSATIONS = "conversations"
JOURNALS = ("messages", "activity")


class UnknownConversation(Exception):
    """No such conversation: never made, removed, or a malformed id."""


def stamp(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _line(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"


def _write_json(path: str, value) -> None:
    partial = f"{path}.part"
    with open(partial, "w", encoding="utf-8") as out_file:
        json.dump(value, out_file, ensure_ascii=False, indent=1)
    os.replace(partial, path)


def _read_json(path: str):
    try:
        with open(path, encoding="utf-8") as in_file:
            return json.load(in_file)
    except FileNotFoundError:
        return None


def _read_lines(path: str) -> list[dict]:
    try:
        with open(path, encoding="utf-8") as in_file:
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
            # A line the admin was stopped in the middle of: what follows it was written after a restart.
            logger.warning(f"{path}: line {number} is not whole, skipped")
    return values


class ConversationStore:
    def __init__(self, root: str):
        self._root = root
        # The service and the routes write the same files from the event loop and the thread pool.
        self._lock = threading.RLock()

    @property
    def root(self) -> str:
        return self._root

    @staticmethod
    def new_id(now: float) -> str:
        return f"{datetime.fromtimestamp(now, timezone.utc):%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"

    def _folder(self, conversation_id: str) -> str:
        if not isinstance(conversation_id, str) or not CONVERSATION_ID.match(conversation_id):
            raise UnknownConversation(f"No conversation {conversation_id!r}")
        return os.path.join(self._root, CONVERSATIONS, conversation_id)

    def _existing(self, conversation_id: str) -> str:
        folder = self._folder(conversation_id)
        if not os.path.isfile(os.path.join(folder, "meta.json")):
            raise UnknownConversation(f"No conversation {conversation_id!r}")
        return folder

    # ── a conversation ──

    def create(self, meta: dict) -> dict:
        folder = self._folder(meta["id"])
        with self._lock:
            os.makedirs(folder)
            _write_json(os.path.join(folder, "meta.json"), meta)
        return meta

    def meta(self, conversation_id: str) -> dict:
        meta = _read_json(os.path.join(self._existing(conversation_id), "meta.json"))
        if meta is None:
            raise UnknownConversation(f"No conversation {conversation_id!r}")
        return meta

    def update_meta(self, conversation_id: str, **changes) -> dict:
        with self._lock:
            meta = {**self.meta(conversation_id), **changes}
            _write_json(os.path.join(self._existing(conversation_id), "meta.json"), meta)
        return meta

    def ids(self) -> list[str]:
        try:
            names = os.listdir(os.path.join(self._root, CONVERSATIONS))
        except OSError:
            return []
        return sorted(name for name in names if CONVERSATION_ID.match(name))

    def metas(self) -> list[dict]:
        """Every conversation, the last changed first."""
        metas = []
        for conversation_id in self.ids():
            try:
                metas.append(self.meta(conversation_id))
            except UnknownConversation:
                continue
        metas.sort(key=lambda meta: meta.get("updated_at") or "", reverse=True)
        return metas

    def append(self, conversation_id: str, journal: str, value: dict) -> None:
        if journal not in JOURNALS:
            raise ValueError(f"No journal {journal!r}")
        with self._lock:
            with open(os.path.join(self._existing(conversation_id), f"{journal}.jsonl"), "a", encoding="utf-8") as out:
                out.write(_line(value))

    def read(self, conversation_id: str, journal: str) -> list[dict]:
        if journal not in JOURNALS:
            raise ValueError(f"No journal {journal!r}")
        return _read_lines(os.path.join(self._existing(conversation_id), f"{journal}.jsonl"))

    def proposals(self, conversation_id: str) -> dict:
        return _read_json(os.path.join(self._existing(conversation_id), "proposals.json")) or {}

    def write_proposals(self, conversation_id: str, proposals: dict) -> None:
        with self._lock:
            _write_json(os.path.join(self._existing(conversation_id), "proposals.json"), proposals)

    def remove(self, conversation_id: str) -> None:
        folder = self._existing(conversation_id)
        with self._lock:
            shutil.rmtree(folder)

    # ── across conversations ──

    def add_artefact(self, value: dict) -> None:
        with self._lock:
            os.makedirs(self._root, exist_ok=True)
            with open(os.path.join(self._root, "artefacts.jsonl"), "a", encoding="utf-8") as out_file:
                out_file.write(_line(value))

    def artefacts(self) -> list[dict]:
        return _read_lines(os.path.join(self._root, "artefacts.jsonl"))

    def add_spend(self, value: dict) -> None:
        with self._lock:
            os.makedirs(self._root, exist_ok=True)
            with open(os.path.join(self._root, "spend.jsonl"), "a", encoding="utf-8") as out_file:
                out_file.write(_line(value))

    def spent(self, month: str) -> float:
        """What the answers of a month (``YYYY-MM``, UTC) cost, as estimated when they came."""
        return sum(float(line.get("usd") or 0) for line in _read_lines(os.path.join(self._root, "spend.jsonl"))
                   if str(line.get("at", "")).startswith(month))

    def pause(self) -> dict | None:
        """The operator's pause of new requests: since when and from where, or None."""
        return _read_json(os.path.join(self._root, "pause.json"))

    def set_pause(self, paused: bool, by: str | None, now: float) -> dict | None:
        path = os.path.join(self._root, "pause.json")
        with self._lock:
            if not paused:
                try:
                    os.remove(path)
                except FileNotFoundError:
                    pass
                return None
            os.makedirs(self._root, exist_ok=True)
            value = {"since": stamp(now), "by": by}
            _write_json(path, value)
        return value

    def prune(self, now: float, keep_days: int) -> list[str]:
        """The conversations left unchanged for ``keep_days``, removed with their lines of spending and artefacts."""
        limit = stamp(now - keep_days * 86400)
        removed = []
        for meta in self.metas():
            if (meta.get("updated_at") or "") < limit and meta.get("status") not in ("queued", "running", "waiting"):
                self.remove(meta["id"])
                removed.append(meta["id"])
        with self._lock:
            for name in ("artefacts.jsonl", "spend.jsonl"):
                path = os.path.join(self._root, name)
                lines = _read_lines(path)
                kept = [line for line in lines if (line.get("at") or "") >= limit]
                if len(kept) != len(lines):
                    partial = f"{path}.part"
                    with open(partial, "w", encoding="utf-8") as out_file:
                        out_file.writelines(_line(line) for line in kept)
                    os.replace(partial, path)
        return removed


def month_of(now: float) -> str:
    return f"{datetime.fromtimestamp(now, timezone.utc):%Y-%m}"

