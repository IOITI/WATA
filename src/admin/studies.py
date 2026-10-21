"""
Pine studies for Claude's tools (pine_check, pine_run, pine_signals): the
command ``python -m src.research.study`` run as a child process of the admin,
one at a time, with its own Node heap and time limit, so that a script that
loops or a long run never holds the admin's own answers.

A study waits a little for the one before; it is refused when the container
is short of memory (the backtest beside it, its runner) or when the hour's
minutes of studies are spent (an agent that loops all night). The child gives
way to the trading services (nice 10) and is the first the kernel stops when
memory runs out.
"""

import asyncio
import collections
import json
import os
import secrets
import time

from src.admin.errors import NotPossible
from src.admin.processes import launch, tail
from src.price_history.process import exit_said, oom_kills

MODES = ("check", "run", "signals")
# How long a study waits for the one before it.
WAIT_SECONDS = 15
# Beyond the study's own time limit: Python's and Node's start, reading the bars.
GRACE_SECONDS = 20
# Free memory a study needs beside its Node heap.
MEMORY_MARGIN_MB = 128
CGROUP_ROOT = "/sys/fs/cgroup"


def cgroup_memory(root: str = CGROUP_ROOT) -> tuple[int, int] | None:
    """
    (used, limit) of the container's memory in bytes, or None where there is no limit to read. The
    file cache the kernel takes back when memory is asked for (``inactive_file``) is not counted as
    used: the price files and the runner's day cache the admin reads fill the container with it.
    """
    try:
        with open(os.path.join(root, "memory.max"), encoding="utf-8") as limit_file:
            limit = limit_file.read().strip()
        with open(os.path.join(root, "memory.current"), encoding="utf-8") as current_file:
            current = int(current_file.read().strip())
    except (OSError, ValueError):
        return None
    if limit == "max":
        return None
    return max(0, current - _inactive_file(root)), int(limit)


def _inactive_file(root: str) -> int:
    """The container's inactive file cache in bytes (``memory.stat``), 0 where it cannot be read."""
    try:
        with open(os.path.join(root, "memory.stat"), encoding="utf-8") as stat_file:
            for line in stat_file:
                name, _, value = line.strip().partition(" ")
                if name == "inactive_file":
                    return int(value)
    except (OSError, ValueError):
        return 0
    return 0


class StudyService:
    def __init__(self, *, root: str, ta_root: str, prices_root: str, signal_cache: str, memory_mb: int,
                 timeout_seconds: int, minutes_per_hour: int, node: str = "node", launcher=launch,
                 memory=cgroup_memory, clock=time.monotonic, wait_seconds: float = WAIT_SECONDS,
                 data: str = "database", kills=oom_kills):
        self._root = root
        # Where Capital.com's and Saxo's bars are read: the database of the configuration, or files:<folder>.
        self._data = data
        self._ta_root = ta_root
        self._prices_root = prices_root
        self._signal_cache = signal_cache
        self.memory_mb = memory_mb
        self.timeout_seconds = timeout_seconds
        self.minutes_per_hour = minutes_per_hour
        self._node = node
        self._launcher = launcher
        self._memory = memory
        self._kills = kills
        self._clock = clock
        self._wait_seconds = wait_seconds
        self._one = asyncio.Lock()
        # When each study of the last hour ended, and how long it took.
        self._spent: collections.deque = collections.deque()
        self._process = None

    def _ending(self, kills_before: int | None) -> str:
        """How the study's child ended, and the memory then: why a study gave no answer."""
        code = self._process.returncode if self._process is not None else None
        said = [f"it was {exit_said(code)}" if code is not None and code < 0 else f"it {exit_said(code)}"]
        found = self._memory()
        if found is not None:
            said.append(f"the admin used {found[0] / 1024 ** 2:,.0f} of its {found[1] / 1024 ** 2:,.0f} MB then")
        kills_after = self._kills()
        if kills_before is not None and kills_after is not None and kills_after > kills_before:
            said.append(f"the kernel's OOM killer stopped {kills_after - kills_before} process(es) meanwhile")
        return ", ".join(said)

    def spent_seconds(self) -> float:
        hour_ago = self._clock() - 3600
        while self._spent and self._spent[0][0] <= hour_ago:
            self._spent.popleft()
        return sum(seconds for _, seconds in self._spent)

    def _check_room(self) -> None:
        if self.spent_seconds() >= self.minutes_per_hour * 60:
            raise NotPossible(
                f"The studies of the last hour took {self.minutes_per_hour} minutes, all they may "
                "(admin.mcp.study_minutes_per_hour): wait, or read what was studied"
            )
        found = self._memory()
        if found is not None:
            used, limit = found
            free_mb = (limit - used) / 1024 ** 2
            if free_mb < self.memory_mb + MEMORY_MARGIN_MB:
                raise NotPossible(
                    f"The admin has {free_mb:.0f} MB of memory free, a study needs {self.memory_mb + MEMORY_MARGIN_MB}: "
                    "a backtest is running, try again once it is done"
                )

    async def study(self, mode: str, request: dict) -> dict:
        """The study's answer: ``ok`` and its findings, or ``ok`` false and where the script fails."""
        if mode not in MODES:
            raise NotPossible(f"A study is one of {', '.join(MODES)}")
        try:
            await asyncio.wait_for(self._one.acquire(), self._wait_seconds)
        except TimeoutError:
            raise NotPossible("Another study is running: try again in a minute") from None
        try:
            self._check_room()
            return await self._run(mode, request)
        finally:
            self._one.release()

    async def _run(self, mode: str, request: dict) -> dict:
        os.makedirs(self._root, exist_ok=True)
        name = f"{mode}-{secrets.token_hex(4)}"
        request_path, out_path, log_path = (os.path.join(self._root, f"{name}.{ending}") for ending in ("request.json", "out.json", "log"))
        with open(request_path, "w", encoding="utf-8") as request_file:
            json.dump(request, request_file)
        started = self._clock()
        kills_before = self._kills()
        try:
            self._process = await self._launcher([
                "src.research.study", mode, "--request", request_path, "--out", out_path,
                "--memory-mb", str(self.memory_mb), "--timeout", str(self.timeout_seconds), "--node", self._node,
                "--root", self._ta_root, "--prices", self._prices_root, "--signal-cache", self._signal_cache,
                "--data", self._data,
            ], log_path)
            try:
                await asyncio.wait_for(self._process.wait(), self.timeout_seconds + GRACE_SECONDS)
            except TimeoutError:
                self._process.kill()
                await self._process.wait()
                raise NotPossible(f"The study did not finish in {self.timeout_seconds + GRACE_SECONDS} s: fewer days, or longer bars") from None
            except asyncio.CancelledError:
                self._process.kill()
                raise
            try:
                with open(out_path, encoding="utf-8") as out_file:
                    return json.load(out_file)
            except (OSError, ValueError):
                said = " | ".join(tail(log_path, 3)) or "nothing"
                raise NotPossible(f"The study gave no answer: {self._ending(kills_before)} ({said})") from None
        finally:
            self._process = None
            self._spent.append((self._clock(), self._clock() - started))
            for path in (request_path, out_path, log_path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    async def close(self) -> None:
        """The admin is stopping: a study running stops with it."""
        if self._process is not None and self._process.returncode is None:
            try:
                self._process.kill()
            except ProcessLookupError:
                pass
