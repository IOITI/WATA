"""
What failed in the admin's interface, written to the admin's log (since v0.102.0).

ioiti-ui's error page and the shell's boundary tell ``IoitiProvider``'s ``onError``, which posts it to
``POST /api/client-errors`` (``admin_ui/src/lib/clientErrors.ts``): the Logs page then shows, under Admin, what failed
for the operator. A line of the log and nothing else: no table, no Telegram, no audit.

Each text is bounded, the lines a minute too (``MOST_A_MINUTE``); every line of a stack is indented, so that none
reads as an entry of its own on the Logs page (``_ENTRY_PATTERN`` of ``src/admin/logs.py`` starts at a line's first
character); the log's handlers clean it of secrets as they clean every line (``setup_logging``'s
``RedactingFilter``).
"""

import logging
import time
from typing import Callable, Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# More in a minute is a loop of errors, not news: the first left out is said, then how many were.
MOST_A_MINUTE = 10
WINDOW_SECONDS = 60.0
# A stack's first lines say where it failed; the rest is the framework's.
STACK_LINES = 12


class ClientError(BaseModel):
    source: Literal["boundary", "route"]
    path: str = Field(min_length=1, max_length=500, pattern=r"^/")
    name: str = Field(default="Error", max_length=200)
    message: str = Field(default="", max_length=2000)
    stack: str | None = Field(default=None, max_length=8000)
    component_stack: str | None = Field(default=None, max_length=8000)


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _indented(text: str) -> str:
    lines = [_one_line(line) for line in text.splitlines() if line.strip()]
    said = "\n".join(f"    {line}" for line in lines[:STACK_LINES])
    if len(lines) > STACK_LINES:
        said += f"\n    … {len(lines) - STACK_LINES} more"
    return said


def describe(error: ClientError) -> str:
    """The log's line: where and what, then the stack and the components it was thrown in, indented."""
    caught = "its route" if error.source == "route" else "the shell"
    text = (
        f"The interface failed at {_one_line(error.path)} (caught by {caught}): "
        f"{_one_line(error.name) or 'Error'}: {_one_line(error.message) or 'no message'}"
    )
    if error.stack and error.stack.strip():
        text += "\n  Stack:\n" + _indented(error.stack)
    if error.component_stack and error.component_stack.strip():
        text += "\n  Components:\n" + _indented(error.component_stack)
    return text


class ClientErrorLog:
    """The lines written, ``MOST_A_MINUTE`` at most a minute. Called on the event loop alone: no lock."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._since: float | None = None
        self._written = 0
        self._left_out = 0

    def record(self, error: ClientError) -> bool:
        """Writes the error's line, or leaves it out past the minute's lines; says whether it was written."""
        now = self._clock()
        if self._since is None or now - self._since >= WINDOW_SECONDS:
            if self._left_out:
                logger.warning("%d more errors of the interface were left out in the minute before", self._left_out)
            self._since, self._written, self._left_out = now, 0, 0
        if self._written >= MOST_A_MINUTE:
            if not self._left_out:
                logger.warning("More than %d errors of the interface in a minute: the next ones are left out", MOST_A_MINUTE)
            self._left_out += 1
            return False
        self._written += 1
        logger.warning("%s", describe(error))
        return True
