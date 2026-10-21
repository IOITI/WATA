"""
Long work of Claude's tools as jobs. Claude Code ends an HTTP call after 60 s,
so a tool that runs a Pine study (``research_tool(..., job=True)``) answers
within ANSWER_SECONDS. When the work is not done by then, the tool hands back
a job instead, and ``job_wait`` gives the tool's own answer once there is one.

The work goes on whatever becomes of the request that started it. A job
belongs to the token that started it, is kept KEEP_SECONDS after it ends, at
most MAX_KEPT of them, and is lost if the admin restarts.
"""

import asyncio
import secrets
import time
from dataclasses import dataclass

# How long a tool waits for its work before it hands back a job, and job_wait at most.
ANSWER_SECONDS = 45
KEEP_SECONDS = 15 * 60
MAX_KEPT = 5


class UnknownJob(Exception):
    """No such job for this caller: never made, someone else's, or ended too long ago."""


@dataclass
class Job:
    id: str
    tool: str
    caller: str | None
    task: asyncio.Task
    started: float
    ended: float | None = None

    def line(self, now: float) -> dict:
        """What is said of a job still at work."""
        return {"job": self.id, "state": "running", "tool": self.tool, "seconds": round(now - self.started),
                "next": "job_wait with this job, until it gives the tool's answer"}


class Jobs:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._jobs: dict[str, Job] = {}

    def start(self, tool: str, caller: str | None, work) -> Job:
        """``work`` (a coroutine) started as a task held here: nothing that happens to the request stops it."""
        self._prune()
        task = asyncio.ensure_future(work)
        job = Job(secrets.token_hex(6), tool, caller, task, self._clock())
        task.add_done_callback(lambda _: setattr(job, "ended", self._clock()))
        self._jobs[job.id] = job
        return job

    def said(self, job: Job) -> dict:
        return job.line(self._clock())

    def forget(self, job: Job) -> None:
        """A job whose answer was given by the call that started it."""
        self._jobs.pop(job.id, None)

    def running(self, caller: str | None) -> Job | None:
        """The caller's job still at work, if any."""
        return next((job for job in self._jobs.values() if job.caller == caller and not job.task.done()), None)

    async def wait(self, job_id: str, caller: str | None, seconds: float):
        """The job's answer once it is done, waiting ``seconds`` at most; what is said of it while it works."""
        job = self._jobs.get(job_id)
        if job is None or job.caller != caller:
            raise UnknownJob(f"No job {job_id}: it was never made for you, ended more than {KEEP_SECONDS // 60} minutes ago, "
                             "or the admin restarted")
        if not job.task.done():
            await asyncio.wait({job.task}, timeout=max(0.0, min(seconds, ANSWER_SECONDS)))
        if not job.task.done():
            return job.line(self._clock())
        # Its answer, or its refusal, as the tool would have given them.
        return job.task.result()

    def _prune(self) -> None:
        now = self._clock()
        ended = sorted((job for job in self._jobs.values() if job.ended is not None), key=lambda job: job.ended)
        for job in ended:
            if now - job.ended > KEEP_SECONDS:
                self._jobs.pop(job.id, None)
        ended = [job for job in ended if job.id in self._jobs]
        for job in ended[:max(0, len(ended) - MAX_KEPT)]:
            self._jobs.pop(job.id, None)
