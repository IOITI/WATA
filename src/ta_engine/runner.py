"""
The Pine runner (src/ta_engine/runner/runner.mjs, built from
admin_ui/src/features/ta/engine/runner.ts) as the engine drives it: one Node
process, in lines of JSON over its stdin and stdout.

It is started confined: Node's permissions let it read no file but its own,
start no process or worker, load no addon; it gets no environment at all, so no
secret; its memory is capped; and a request that takes too long ends it. The
runner itself refuses every import once started and has no network globals.
What it answers is data for the engine to check, never trusted as more.
"""

import asyncio
import json
import logging
import os
import shutil
from collections.abc import Awaitable, Callable

from src.price_history.process import give_up_memory_first

logger = logging.getLogger(__name__)

RUNNER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runner", "runner.mjs")
# The longest line either side may write: a load answers with every alert of the history.
LINE_LIMIT = 64 * 1024 * 1024
# What the runner writes on stderr is logged, a line at a time, up to this many characters.
STDERR_LINE_CHARACTERS = 500


class RunnerFailed(Exception):
    """The runner refused a request: a script that does not compile or run, bars out of order..."""

    def __init__(self, error: dict):
        self.error = error if isinstance(error, dict) else {"message": str(error)}
        location = ""
        if self.error.get("line"):
            location = f" ({self.error.get('file') or 'script'}, line {self.error['line']})"
        super().__init__(f"{self.error.get('message') or 'The runner failed'}{location}")


class RunnerDown(Exception):
    """The runner is not running, ended, or took too long: it is started again."""


# Answers a request.security of the script: the ask, then the bars to hand back.
SeriesAnswer = Callable[[dict], Awaitable[list]]


def runner_command(node: str, runner_path: str, memory_mb: int) -> list[str]:
    folder = os.path.dirname(runner_path)
    return [
        node,
        f"--max-old-space-size={memory_mb}",
        "--permission",
        f"--allow-fs-read={folder}",
        runner_path,
    ]


def find_node(node: str) -> str | None:
    """The absolute path of ``node`` (the runner gets no PATH to find it by)."""
    return node if os.path.isabs(node) and os.access(node, os.X_OK) else shutil.which(node)


class NodeRunner:
    def __init__(
        self,
        command: list[str],
        answer: SeriesAnswer,
        *,
        timeout_seconds: float,
        start_timeout_seconds: float = 15.0,
        memory_first: bool = False,
    ):
        self._command = command
        self._answer = answer
        self._timeout = timeout_seconds
        self._start_timeout = start_timeout_seconds
        # The first process the kernel stops when the container runs short of memory (the shadow rules' runner).
        self._memory_first = memory_first
        self._process: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._next_id = 0
        self.versions: dict = {}
        self.starts = 0

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def start(self) -> dict:
        """Start the runner (again) and return its versions."""
        await self.stop()
        self.starts += 1
        self._process = await asyncio.create_subprocess_exec(
            *self._command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={},
            cwd=os.path.dirname(self._command[-1]),
            limit=LINE_LIMIT,
        )
        if self._memory_first:
            give_up_memory_first(f"/proc/{self._process.pid}/oom_score_adj")
        self._stderr_task = asyncio.create_task(self._log_stderr(self._process))
        hello = await self.request({"op": "hello"}, timeout_seconds=self._start_timeout)
        self.versions = {"pinets": hello.get("pinets"), "node": hello.get("node")}
        logger.info("Pine runner started: PineTS %s on Node %s.", self.versions["pinets"], self.versions["node"])
        return self.versions

    async def _log_stderr(self, process: asyncio.subprocess.Process) -> None:
        try:
            while True:
                line = await process.stderr.readline()
                if not line:
                    return
                logger.warning("Pine runner: %s", line.decode("utf-8", "replace").rstrip()[:STDERR_LINE_CHARACTERS])
        except (asyncio.CancelledError, ValueError):
            return

    async def stop(self) -> None:
        process, self._process = self._process, None
        if process is not None and process.returncode is None:
            try:
                process.stdin.close()
                await asyncio.wait_for(process.wait(), 2)
            except (asyncio.TimeoutError, OSError, RuntimeError):
                process.kill()
                await process.wait()
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            self._stderr_task = None

    async def request(self, message: dict, *, timeout_seconds: float | None = None) -> dict:
        """
        One request and its answer, the series it asks for answered meanwhile. Raises
        RunnerFailed when the runner refuses it, RunnerDown when it is gone or took
        longer than the timeout (it is then stopped: the next request starts it).
        """
        async with self._lock:
            if not self.running:
                raise RunnerDown("The Pine runner is not running")
            self._next_id += 1
            request_id = self._next_id
            try:
                return await asyncio.wait_for(
                    self._exchange({**message, "id": request_id}, request_id),
                    timeout_seconds if timeout_seconds is not None else self._timeout,
                )
            except asyncio.TimeoutError:
                await self.stop()
                raise RunnerDown(f"The Pine runner took longer than {timeout_seconds or self._timeout:g} s: it was stopped") from None
            except (OSError, ValueError, asyncio.IncompleteReadError, asyncio.LimitOverrunError) as e:
                await self.stop()
                raise RunnerDown(f"The Pine runner could not be read: {type(e).__name__}") from None

    async def _write(self, message: dict) -> None:
        self._process.stdin.write(json.dumps(message, separators=(",", ":"), allow_nan=False).encode() + b"\n")
        await self._process.stdin.drain()

    async def _exchange(self, message: dict, request_id: int) -> dict:
        await self._write(message)
        while True:
            line = await self._process.stdout.readline()
            if not line:
                code = await self._process.wait()
                raise RunnerDown(f"The Pine runner ended (exit code {code})")
            reply = json.loads(line)
            if not isinstance(reply, dict):
                raise ValueError("Not an object")
            if "ask" in reply:
                await self._write(await self._answered(reply))
                continue
            if reply.get("id") != request_id:
                raise ValueError("An answer to another request")
            if not reply.get("ok"):
                raise RunnerFailed(reply.get("error"))
            return reply

    async def _answered(self, ask: dict) -> dict:
        try:
            return {"answer": ask["ask"], "bars": await self._answer(ask)}
        except Exception as e:
            return {"answer": ask.get("ask"), "error": str(e) or type(e).__name__}


CHECK_SCRIPT = """//@version=6
indicator("Runner check")
plot(ta.sma(close, 3), "Average")
alertcondition(close > close[1], title = "Up", message = '{"action": "long"}')
"""


async def check(node: str = "node", runner_path: str = RUNNER_PATH) -> dict:
    """
    The runner of this installation run once, confined as the engine runs it,
    on made-up bars: what CI checks the image with, and what can be run on a
    server (python -m src.ta_engine.runner).
    """
    import hashlib

    found = find_node(node)
    if found is None:
        raise RuntimeError(f"No Node ({node!r}) here")
    if not os.path.isfile(runner_path):
        raise RuntimeError(f"No runner at {runner_path}: package.sh builds it (npm run build:runner in admin_ui)")

    async def no_series(ask: dict) -> list:
        raise ValueError("No other series in a check")

    runner = NodeRunner(runner_command(found, runner_path, 256), no_series, timeout_seconds=30)
    bars = [
        [index * 60_000, 100.0 + index, 101.0 + index, 99.0 + index, 100.0 + index + index % 3, 1, (index + 1) * 60_000]
        for index in range(50)
    ]
    try:
        versions = await runner.start()
        loaded = await runner.request({
            "op": "load", "rule": "runnercheck0",
            "script": {"source": CHECK_SCRIPT, "sha256": hashlib.sha256(CHECK_SCRIPT.encode()).hexdigest()},
            "libraries": [], "inputs": {}, "ticker": "CAPITAL_US100", "timeframe": "1", "bars": bars,
        })
    finally:
        await runner.stop()
    if loaded.get("bars") != len(bars) or not loaded.get("alerts"):
        raise RuntimeError(f"The runner answered, but not what the check expects: {loaded}")
    return {"runner": versions, "bars": loaded["bars"], "alerts": len(loaded["alerts"]), "milliseconds": loaded["milliseconds"]}


if __name__ == "__main__":
    import sys

    try:
        print(json.dumps(asyncio.run(check())))
    except Exception as e:
        print(f"The Pine runner does not work here: {e}", file=sys.stderr)
        sys.exit(1)
