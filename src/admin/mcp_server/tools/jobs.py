"""
``job_wait``: the answer of a tool whose work outlasted its call (a Pine study,
src/admin/mcp_server/jobs.py), once it has one.
"""

from src.admin.mcp_server.budget import research_tool
from src.admin.mcp_server.gate import current_caller
from src.admin.mcp_server.jobs import ANSWER_SECONDS


def register(server, research) -> None:
    calls = research.calls

    # Export-sized: the answer is the tool's own, already held to that tool's size.
    @research_tool(server, calls, name="job_wait", title="The answer of a job", read_only=True, export=True)
    async def job_wait(job_id: str, seconds: int = 40) -> dict | str:
        """A tool that runs a Pine study (pine_check, pine_run, pine_signals, pine_compare, pine_save,
        rule_shadow_save) answers within 45 s, or hands back a job while the study goes on. job_wait waits up to
        ``seconds`` (45 at most) and gives that tool's own answer, or its refusal, once there is one; until then,
        that the job still works, and for how long. A job is kept 15 minutes after it ends."""
        caller = current_caller()
        return await research.jobs.wait(job_id, caller.name if caller else None, min(max(seconds, 0), ANSWER_SECONDS))
