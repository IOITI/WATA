"""
The record of what was done through the admin interface: one JSON object per
line, kept beside the admin's log.
"""

import json
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

MAX_ENTRIES = 1000
# Enough for MAX_ENTRIES entries; the file itself is never truncated.
TAIL_BYTES = 512 * 1024


class AuditLog:
    def __init__(self, path: str):
        self.path = path

    def append(self, ip: str, action: str, detail: str = "") -> None:
        """Never raises: a full disk must not stop the action being recorded in the log and on Telegram."""
        entry = {
            "at": datetime.now(timezone.utc).isoformat(),
            "ip": ip,
            "action": action,
            "detail": detail,
        }
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "a") as audit_file:
                audit_file.write(json.dumps(entry) + "\n")
        except OSError as e:
            logger.error(f"Unable to write the audit entry to {self.path}: {e}")

    def read(self, limit: int = 200) -> list[dict]:
        """The most recent entries, newest first."""
        try:
            with open(self.path, "rb") as audit_file:
                audit_file.seek(0, os.SEEK_END)
                size = audit_file.tell()
                audit_file.seek(max(size - TAIL_BYTES, 0))
                lines = audit_file.read().decode("utf-8", errors="replace").splitlines()
        except FileNotFoundError:
            return []
        if size > TAIL_BYTES:
            lines = lines[1:]  # the read started in the middle of a line

        entries = []
        for line in reversed(lines):
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict):
                entries.append(entry)
            if len(entries) >= min(max(limit, 1), MAX_ENTRIES):
                break
        return entries
