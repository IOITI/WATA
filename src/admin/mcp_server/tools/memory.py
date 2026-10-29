"""
The AI agent's memory (src/admin/agent_memory.py), read: what it and the
operator kept between the agent's conversations. Nothing here changes it:
the agent saves and forgets with tools of its own, the operator from the
admin's Memory page. A read is noted on the entries (when, by whom), which
changes nothing of what they say.
"""

from starlette.concurrency import run_in_threadpool

from src.admin.agent_memory import KINDS, listed
from src.admin.mcp_server.budget import Refused, research_tool
from src.admin.mcp_server.gate import current_caller
from src.admin.mcp_server.shape import compacted

# Entries read in one call, at most.
MAX_READ = 10


def _memory(research):
    if research.memory is None:
        raise Refused("The AI agent's memory is not kept on this server")
    return research.memory


def register(server, research) -> None:
    calls = research.calls

    @research_tool(server, calls, name="memory_list", title="The AI agent's memory", read_only=True)
    async def memory_list(kind: str = "") -> dict:
        """What WATA's AI agent and the operator kept between the agent's conversations: each entry's name, kind
        (preference: what the operator wants or refuses; lesson: how WATA, its data and its tools behave; finding:
        what a study concluded and what it rests on; reference: where something worth going back to is), its line,
        who wrote it, whether the operator locked it. Read the entries that bear on the work with memory_read before
        relying on them. Only the agent and the operator change it (AI agent › Memory)."""
        if kind and kind not in KINDS:
            raise Refused(f"A kind is one of {', '.join(KINDS)}")
        memory = _memory(research)
        entries = await run_in_threadpool(memory.entries, kind or None)
        return compacted({"entries": [listed(entry) for entry in entries], "count": len(entries),
                          "paused": await run_in_threadpool(memory.pause)})

    @research_tool(server, calls, name="memory_read", title="Entries of the AI agent's memory", read_only=True)
    async def memory_read(names: list[str]) -> dict:
        """Entries of the AI agent's memory whole (names as memory_list gives them, ten at most): its text, who wrote
        and changed it and when, from which conversation, its revision, whether the operator locked it."""
        if not isinstance(names, list) or not 1 <= len(names) <= MAX_READ:
            raise Refused(f"Read 1 to {MAX_READ} entries at once")
        memory = _memory(research)
        wanted = list(dict.fromkeys(name for name in names if isinstance(name, str)))
        found = {entry["name"]: entry for entry in await run_in_threadpool(memory.entries)}
        read = [found[name] for name in wanted if name in found]
        caller = current_caller()
        if read:
            await run_in_threadpool(memory.mark_read, [entry["name"] for entry in read],
                                    caller.name if caller else "claude")
        return compacted({
            "entries": [{key: entry.get(key) for key in (
                "name", "kind", "description", "body", "locked", "revision", "created_at", "created_by", "created_in",
                "updated_at", "updated_by", "updated_in",
            )} for entry in read],
            "unknown": [name for name in wanted if name not in found],
        })
