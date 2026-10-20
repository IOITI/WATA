"""
Reading the tail of the services' log files (written by logging_helper.setup_logging
as ``<log_path>/<app_name>/<app_name>.log``).
"""

import os
import re
from datetime import datetime, timezone

# app_name passed to setup_logging by each service -> label shown in the interface.
LOG_SERVICES = {
    "wata-api": "Web server",
    "wata-trader-async": "Trader",
    "wata-position-monitor": "Position monitor",
    "wata-scheduler": "Scheduler",
    "wata-telegram": "Telegram",
    "wata-watchlist-manager": "Watchlist manager",
    "wata-admin": "Admin",
    "wata-capital-feed": "Capital.com feed",
    "wata-ta-engine": "TA engine",
}

LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
MAX_LINES = 2000
# Log files rotate at 2 MiB; the tail is enough for MAX_LINES entries.
TAIL_BYTES = 1024 * 1024

# The default logging.format: "%(asctime)s - %(levelname)s - %(name)s - %(message)s".
_ENTRY_PATTERN = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:,\d{3})?) - "
    r"(?P<level>[A-Z]+) - (?P<logger>\S+) - (?P<message>.*)$"
)


def log_file_path(log_path: str, service: str) -> str:
    """Path of a service's log file. ``service`` must be a key of LOG_SERVICES."""
    if service not in LOG_SERVICES:
        raise KeyError(service)
    return os.path.join(log_path, service, f"{service}.log")


def read_tail(path: str, max_bytes: int = TAIL_BYTES) -> str:
    with open(path, "rb") as log_file:
        log_file.seek(0, os.SEEK_END)
        size = log_file.tell()
        log_file.seek(max(size - max_bytes, 0))
        data = log_file.read()
    text = data.decode("utf-8", errors="replace")
    if size > max_bytes:
        # The read started in the middle of a line: drop that partial line.
        text = text.split("\n", 1)[1] if "\n" in text else ""
    return text


def parse_entries(text: str) -> list[dict]:
    """
    Split log text into entries. A line that doesn't start a new entry (a
    traceback, a multi-line message) belongs to the entry before it.
    """
    entries: list[dict] = []
    for line in text.splitlines():
        match = _ENTRY_PATTERN.match(line)
        if match:
            entries.append(match.groupdict())
        elif entries:
            entries[-1]["message"] += f"\n{line}"
        elif line.strip():
            entries.append({"timestamp": None, "level": None, "logger": None, "message": line})
    return entries


def read_log(log_path: str, service: str, lines: int = 200, level: str | None = None, query: str | None = None) -> dict:
    path = log_file_path(log_path, service)
    if not os.path.exists(path):
        return {"service": service, "exists": False, "modified_at": None, "entries": []}

    entries = parse_entries(read_tail(path))
    if level in LEVELS:
        minimum = LEVELS.index(level)
        entries = [
            entry for entry in entries
            if entry["level"] in LEVELS and LEVELS.index(entry["level"]) >= minimum
        ]
    if query:
        needle = query.casefold()
        entries = [
            entry for entry in entries
            if needle in entry["message"].casefold() or needle in (entry["logger"] or "").casefold()
        ]

    modified_at = datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc).isoformat()
    return {
        "service": service,
        "exists": True,
        "modified_at": modified_at,
        "entries": entries[-min(max(lines, 1), MAX_LINES):],
    }


def last_lifecycle_event(log_path: str, service: str, markers: dict[str, str]) -> dict | None:
    """
    The most recent entry of a service's log containing one of ``markers``
    (event name -> text to look for), or None when the current file has none.
    """
    path = log_file_path(log_path, service)
    if not os.path.exists(path):
        return None
    for entry in reversed(parse_entries(read_tail(path))):
        for event, text in markers.items():
            if text in entry["message"]:
                # Without the milliseconds, which nobody reads in a status line.
                return {"event": event, "timestamp": (entry["timestamp"] or "").split(",")[0]}
    return None
