"""
What the AI agent's own schedules share (the daily debrief, the morning
briefing, an alert's triage, the go-live dossiers, the routines): config.json
read again at each look, since the trading hours may have been edited, and each
one's file beside the conversations, written whole. They live outside
src/admin/agent/, which never reaches the notifier (tests/test_agent_boundary.py).
"""

import json
import os
import threading


def read_config(path: str) -> dict:
    with open(path, encoding="utf-8") as config_file:
        return json.load(config_file)


class JobFile:
    """A schedule's file: what it did, by key (a day, an alert, a rule), the newest ``kept`` of them."""

    def __init__(self, path: str, key: str, kept: int):
        self._path = path
        self._key = key
        self._kept = kept
        self._lock = threading.Lock()

    def read(self) -> dict:
        try:
            with open(self._path, encoding="utf-8") as kept:
                found = json.load(kept).get(self._key)
        except (OSError, ValueError, AttributeError):
            return {}
        return found if isinstance(found, dict) else {}

    def write(self, entries: dict) -> None:
        newest = dict(sorted(entries.items())[-self._kept:])
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        partial = f"{self._path}.part"
        with self._lock:
            with open(partial, "w", encoding="utf-8") as out_file:
                json.dump({self._key: newest}, out_file, ensure_ascii=False)
            os.replace(partial, self._path)

    def put(self, key: str, entry: dict) -> dict:
        """One entry written, the others kept as they are."""
        entries = self.read()
        entries[key] = entry
        self.write(entries)
        return entries
