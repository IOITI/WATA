"""
What every tool of the MCP endpoint goes through (``research_tool``):

- its answer as compact JSON, held to a size: what goes into Claude's context
  stays under Claude Code's warning (about 10,000 tokens); an export (CSV) may
  be larger, and Claude Code saves an answer over 50,000 characters to a file
  on the Mac, where it is analysed;
- a time limit: Claude Code moves a call of more than two minutes to the
  background, and a tool here never needs that long;
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
from src.admin.ta_scripts import InvalidScript, ScriptConflict, ScriptNotFound
from src.research.experiments import JournalError

logger = logging.getLogger(__name__)

# What a tool answers into Claude's context, at most.
CONTEXT_CHARS = 32_000
# What an export (CSV) answers, at most: Claude Code saves it to a file on the Mac.
EXPORT_CHARS = 2_000_000
TOOL_SECONDS = 100


class Refused(Exception):
    """A request a tool will not carry out, said in a sentence for Claude."""


# Errors whose message says what was wrong with the request, meant for whoever asked.
_SAID = (Refused, InvalidRequest, NotPossible, UnknownRun, AssetNotFound, InvalidAsset, ScriptNotFound, InvalidScript,
         ScriptConflict, JournalError)


def compact(value) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, default=str)


def research_tool(server, calls, *, name: str, title: str, read_only: bool, destructive: bool = False,
                  idempotent: bool = False, export: bool = False, seconds: float = TOOL_SECONDS,
                  description: str | None = None):
    """Register ``fn`` (async, returning JSON-able data or a CSV string) as the tool ``name``, described by its
    docstring unless ``description`` says otherwise."""

    def register(fn):
        @functools.wraps(fn)
        async def call(**arguments):
            started = time.monotonic()
            caller = current_caller()
            outcome, detail = "ok", ""
            try:
                try:
                    result = await asyncio.wait_for(fn(**arguments), seconds)
                except TimeoutError:
                    raise Refused(f"No answer within {seconds:.0f} s: ask for less (a shorter period, fewer rows)") from None
                text = result if isinstance(result, str) else compact(result)
                limit = EXPORT_CHARS if export else CONTEXT_CHARS
                if len(text) > limit:
                    raise Refused(
                        f"The answer would be {len(text):,} characters, over the {limit:,} this tool gives: "
                        "ask for fewer rows (limit, offset), a shorter period or less detail"
                    )
                return text
            except UnknownRun as e:
                outcome, detail = "refused", f"No such backtest: {e}"
                raise ToolError(detail) from None
            except _SAID as e:
                outcome, detail = "refused", str(e)
                raise ToolError(str(e)) from None
            except DatabaseUnavailable:
                outcome, detail = "failed", "database unavailable"
                raise ToolError("The database is unavailable: try again in a while") from None
            except ToolError as e:
                outcome, detail = "refused", str(e)
                raise
            except asyncio.CancelledError:
                outcome = "cancelled"
                raise
            except Exception as e:
                outcome, detail = "failed", type(e).__name__
                logger.exception(f"MCP tool {name} failed")
                raise ToolError(f"Unexpected error ({type(e).__name__}) in {name}: the admin's log has the details") from None
            finally:
                calls.record(
                    caller=caller.name if caller else None, tool=name, arguments=arguments, outcome=outcome,
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
