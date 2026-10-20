"""
What Claude asked of the MCP endpoint: one JSON line per call in
``<log_path>/wata-admin/mcp.jsonl``, kept to a few files of a few megabytes,
and the last calls in memory for the System page.

Not the audit trail: each audit entry also goes to Telegram, and an agent at
work calls the endpoint hundreds of times a day. Long arguments (a Pine
script's source) are kept as their sha256 and length, not as text.
"""

import collections
import hashlib
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

MAX_BYTES = 5 * 1024 * 1024
BACKUPS = 3
KEPT_IN_MEMORY = 200
# An argument longer than this, once written as JSON, is kept as its digest and length.
LONG_ARGUMENT = 200


def summarised(arguments: dict) -> dict:
    found = {}
    for name, value in (arguments or {}).items():
        text = json.dumps(value, ensure_ascii=False, default=str)
        if len(text) > LONG_ARGUMENT:
            found[name] = {"sha256": hashlib.sha256(text.encode()).hexdigest(), "chars": len(text)}
        else:
            found[name] = value if isinstance(value, (str, int, float, bool, type(None))) else json.loads(text)
    return found


class CallLog:
    def __init__(self, path: str, *, max_bytes: int = MAX_BYTES, backups: int = BACKUPS, keep: int = KEPT_IN_MEMORY,
                 clock=time.time):
        self.path = path
        self._max_bytes = max_bytes
        self._backups = backups
        self._recent: collections.deque = collections.deque(maxlen=keep)
        self._clock = clock
        self._lock = threading.Lock()
        self._failing = False

    def record(self, *, caller: str | None, tool: str, arguments: dict, outcome: str, milliseconds: int,
               detail: str = "") -> dict:
        entry = {
            "at": datetime.fromtimestamp(self._clock(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "caller": caller,
            "tool": tool,
            "arguments": summarised(arguments),
            "outcome": outcome,
            "milliseconds": milliseconds,
        }
        if detail:
            entry["detail"] = detail[:300]
        with self._lock:
            self._recent.append(entry)
            self._write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        return entry

    def recent(self, limit: int = 50) -> list[dict]:
        """The last calls, newest first."""
        with self._lock:
            return list(reversed(self._recent))[:limit]

    def _write(self, line: str) -> None:
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            try:
                size = os.path.getsize(self.path)
            except OSError:
                size = 0
            if size and size + len(line.encode()) > self._max_bytes:
                self._rotate()
            with open(self.path, "a", encoding="utf-8") as log_file:
                log_file.write(line)
            self._failing = False
        except OSError as e:
            # Reported once: a full disk must not fill the admin's own log with the same line.
            if not self._failing:
                self._failing = True
                logger.warning(f"Unable to write the MCP call log {self.path}: {e}")

    def _rotate(self) -> None:
        for number in range(self._backups, 0, -1):
            older = f"{self.path}.{number}"
            newer = f"{self.path}.{number - 1}" if number > 1 else self.path
            if number == self._backups and os.path.exists(older):
                os.remove(older)
            if os.path.exists(newer):
                os.replace(newer, older)
