"""
What the admin's work in the background shares: the commands it starts as
child processes (a backtest, a download of prices), and the files of state
it keeps beside them.

A command is a module of the application run as ``python -m``, from the
application's directory, its output kept in a file. Tests give the services
another launcher instead, which starts nothing.
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from typing import Awaitable, Callable, Protocol

APP_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUTPUT_TAIL_LINES = 40

# The states a run or a download is in.
QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"
# The admin stopped while it was waiting or running.
INTERRUPTED = "interrupted"
IDLE = "idle"


class Process(Protocol):
    returncode: int | None

    async def wait(self) -> int: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


# Runs ``python -m <arguments>`` with its output kept in the file named.
Launcher = Callable[[list[str], str], Awaitable[Process]]


async def launch(arguments: list[str], log_path: str) -> Process:
    """A module of the application as a child process, from the application's directory."""
    python_path = os.pathsep.join(
        part for part in (APP_ROOT, os.path.join(APP_ROOT, "src"), os.environ.get("PYTHONPATH")) if part
    )
    with open(log_path, "ab") as log:
        return await asyncio.create_subprocess_exec(
            sys.executable, "-u", "-m", *arguments,
            stdin=asyncio.subprocess.DEVNULL, stdout=log, stderr=asyncio.subprocess.STDOUT,
            cwd=APP_ROOT, env={**os.environ, "PYTHONPATH": python_path},
        )


def write_json(path: str, content) -> None:
    """Written whole or not at all: a reader never finds half a file."""
    partial = f"{path}.part"
    with open(partial, "w", encoding="utf-8") as out_file:
        json.dump(content, out_file, indent=2)
    os.replace(partial, path)


def read_json(path: str):
    """The content of a file of JSON; None when it is not there or cannot be read."""
    try:
        with open(path, encoding="utf-8") as in_file:
            return json.load(in_file)
    except (OSError, ValueError):
        return None


def tail(path: str, lines: int = OUTPUT_TAIL_LINES) -> list[str]:
    """The last lines of a file of output."""
    try:
        with open(path, encoding="utf-8", errors="replace") as in_file:
            return [line.rstrip("\n") for line in in_file.readlines()[-lines:]]
    except OSError:
        return []


def utc(moment: float) -> str:
    return datetime.fromtimestamp(moment, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def folder_bytes(path: str) -> int:
    """The size of everything under a folder; 0 when it is not there."""
    total = 0
    for folder, _, names in os.walk(path):
        for name in names:
            try:
                total += os.path.getsize(os.path.join(folder, name))
            except OSError:
                pass
    return total
