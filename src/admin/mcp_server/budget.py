"""
What every tool of the MCP endpoint goes through (``research_tool``):

- its answer as compact JSON, held to a size: what goes into Claude's context
  stays under Claude Code's warning (about 10,000 tokens); an export (CSV) may
  be larger, and Claude Code saves an answer over 50,000 characters to a file
  on the Mac, where it is analysed (it refuses one over 16 MiB);
- a time limit under the 60 s after which Claude Code ends an HTTP call:
  TOOL_SECONDS; a tool whose work may outlast it (a Pine study) answers within
  ANSWER_SECONDS with its answer or a job, which ``job_wait`` answers later
  (src/admin/mcp_server/jobs.py);
- WATA's own refusals passed on as the sentence they are (``ToolError``):
  any other exception reaches Claude as "Error executing tool" alone;
- one line in the call log, whatever the outcome.
"""

import asyncio
import functools
import inspect
import json
import logging
import time

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from src.admin.data import AssetNotFound, InvalidAsset
from src.admin.errors import DatabaseUnavailable, InvalidRequest, NotPossible, UnknownRun
from src.admin.mcp_server.gate import current_caller
from src.admin.mcp_server.jobs import ANSWER_SECONDS, UnknownJob
from src.admin.ta_scripts import InvalidScript, ScriptConflict, ScriptNotFound
from src.research.experiments import JournalError

logger = logging.getLogger(__name__)

# What a tool answers into Claude's context, at most.
CONTEXT_CHARS = 32_000
# What an export (CSV) answers, at most: Claude Code saves it to a file on the Mac.
EXPORT_CHARS = 2_000_000
# Claude Code ends an HTTP call after 60 s: every tool answers before.
TOOL_SECONDS = 50


class Refused(Exception):
    """A request a tool will not carry out, said in a sentence for Claude."""


# Errors whose message says what was wrong with the request, meant for whoever asked.
_SAID = (Refused, InvalidRequest, NotPossible, UnknownRun, AssetNotFound, InvalidAsset, ScriptNotFound, InvalidScript,
         ScriptConflict, JournalError, UnknownJob)


def compact(value) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, default=str)


def said(name: str, error: BaseException, running=None) -> tuple[str, str, ToolError]:
    """
    How a tool's error is logged (outcome, detail) and what Claude is told of
    it. ``running``: a job of the caller still at work, named in a refusal.
    """
    if isinstance(error, UnknownRun):
        detail = f"No such backtest: {error}"
        return "refused", detail, ToolError(detail)
    if isinstance(error, _SAID):
        detail = str(error)
        if running is not None and isinstance(error, NotPossible):
            detail += f" (your job {running.id} of {running.tool} is at work: job_wait it)"
        return "refused", detail, ToolError(detail)
    if isinstance(error, DatabaseUnavailable):
        return "failed", "database unavailable", ToolError("The database is unavailable: try again in a while")
    if isinstance(error, ToolError):
        return "refused", str(error), error
    logger.error(f"MCP tool {name} failed", exc_info=(type(error), error, error.__traceback__))
    return "failed", type(error).__name__, ToolError(
        f"Unexpected error ({type(error).__name__}) in {name}: the admin's log has the details"
    )


def research_tool(server, calls, *, name: str, title: str, read_only: bool, destructive: bool = False,
                  idempotent: bool = False, export: bool = False, seconds: float = TOOL_SECONDS,
                  description: str | None = None, jobs=None, export_chars: int = EXPORT_CHARS):
    """
    Register ``fn`` (async, returning JSON-able data or a CSV string) as the tool
    ``name``, described by its docstring unless ``description`` says otherwise.
    With ``jobs`` (src/admin/mcp_server/jobs.py), its work may outlast a call: it
    answers within ANSWER_SECONDS, with its answer or with a job ``job_wait`` answers.
    """

    def register(fn):
        limit = export_chars if export else CONTEXT_CHARS

        async def answer(arguments: dict) -> str:
            result = await fn(**arguments)
            text = result if isinstance(result, str) else compact(result)
            if len(text) > limit:
                raise Refused(
                    f"The answer would be {len(text):,} characters, over the {limit:,} this tool gives: "
                    "ask for fewer rows (limit, offset), a shorter period or less detail"
                )
            return text

        def ended(job, who, arguments: dict, task) -> None:
            """A job's end, logged as the call it answers."""
            outcome, detail = "ok", f"job {job.id}"
            if task.cancelled():
                outcome = "cancelled"
            elif task.exception() is not None:
                outcome, detail, _ = said(name, task.exception())
                detail = f"job {job.id}: {detail}"
            calls.record(caller=who, tool=name, arguments=arguments, outcome=outcome,
                         milliseconds=int((job.ended - job.started) * 1000), detail=detail)

        @functools.wraps(fn)
        async def call(**arguments):
            started = time.monotonic()
            caller = current_caller()
            who = caller.name if caller else None
            outcome, detail = "ok", ""
            try:
                if jobs is None:
                    try:
                        return await asyncio.wait_for(answer(arguments), seconds)
                    except TimeoutError:
                        raise Refused(f"No answer within {seconds:.0f} s: ask for less (a shorter period, fewer rows)") from None
                job = jobs.start(name, who, answer(arguments))
                await asyncio.wait({job.task}, timeout=ANSWER_SECONDS)
                if job.task.done():
                    jobs.forget(job)
                    return job.task.result()
                # Not done yet: it goes on, and job_wait gives its answer.
                job.task.add_done_callback(lambda task: ended(job, who, arguments, task))
                outcome, detail = "job", f"job {job.id}"
                return compact(jobs.said(job))
            except asyncio.CancelledError:
                outcome = "cancelled"
                raise
            except Exception as e:
                outcome, detail, error = said(name, e, jobs.running(who) if jobs is not None else None)
                raise error from None
            finally:
                calls.record(
                    caller=who, tool=name, arguments=arguments, outcome=outcome,
                    milliseconds=int((time.monotonic() - started) * 1000), detail=detail,
                )

        # The SDK builds the tool's input schema from the signature: the wrapper shows the tool's own.
        call.__signature__ = inspect.signature(fn).replace(return_annotation=str)
        call.__annotations__ = {**fn.__annotations__, "return": str}
        server.add_tool(
            call,
            name=name,
            title=title,
            description=description or inspect.getdoc(fn),
            annotations=ToolAnnotations(
                title=title,
                read_only_hint=read_only,
                destructive_hint=None if read_only else destructive,
                idempotent_hint=None if read_only else idempotent,
                open_world_hint=False,
            ),
            structured_output=False,
        )
        return fn

    return register
