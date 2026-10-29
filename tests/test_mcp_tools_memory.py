"""
The AI agent's memory as Claude Code reads it through the MCP endpoint
(src/admin/mcp_server/tools/memory.py), in process through the SDK's client:
the entries listed without their text, by kind, read whole with the reader
noted on them, and nothing of the memory ever changed.
"""

import asyncio
import dataclasses
import json

import pytest
from mcp import Client

from src.admin.agent_memory import Author, MemoryStore
from src.admin.mcp_server.gate import CALLER
from src.admin.mcp_server.server import build_server
from src.admin.mcp_server.tokens import Caller
from tests.test_admin_backtests import NOW, config, scripts  # noqa: F401 (fixtures)
from tests.test_mcp_tools_read import journal, research  # noqa: F401 (fixtures)

OPERATOR = Author.operator("10.8.0.2")


@pytest.fixture
def memory(tmp_path):
    memory = MemoryStore(str(tmp_path / "agent" / "memory"), clock=lambda: NOW)
    memory.save(OPERATOR, "answer-short", kind="preference", description="The verdict first, five lines at most",
                body="The operator reads on a phone.", locked=True)
    memory.save(Author.agent("20261028-080000-a1b2c3", 0), "range-ticks", kind="lesson",
                description="R60 is 6 points on capital/US100, 0.06 on dukascopy/us100",
                body="Dukascopy's files hold thousandths: 6 points there are R6000.")
    return memory


def called(research, tool: str, arguments: dict, caller: str = "claude-mac"):
    async def calling():
        token = CALLER.set(Caller(name=caller, token_id="t1", expires_at=""))
        try:
            async with Client(build_server(research)) as client:
                result = await client.call_tool(tool, arguments)
                return result.content[0].text, result.is_error
        finally:
            CALLER.reset(token)

    return asyncio.run(calling())


def test_the_entries_are_listed_without_their_text_kind_by_kind(research, memory):
    research = dataclasses.replace(research, memory=memory)

    listed, error = called(research, "memory_list", {})
    lessons, _ = called(research, "memory_list", {"kind": "lesson"})
    wrong, refused = called(research, "memory_list", {"kind": "rumour"})

    assert not error
    said = json.loads(listed)
    assert [entry["name"] for entry in said["entries"]] == ["answer-short", "range-ticks"]
    assert said["entries"][0]["locked"] is True and said["entries"][0]["created_by"] == "operator"
    assert "body" not in said["entries"][0] and said["count"] == 2 and said["paused"] is None
    assert [entry["name"] for entry in json.loads(lessons)["entries"]] == ["range-ticks"]
    assert refused and "preference, lesson, finding, reference" in wrong


def test_an_entry_is_read_whole_and_who_read_it_is_noted_without_a_change(research, memory):
    research = dataclasses.replace(research, memory=memory)
    seq = memory.seq()

    text, error = called(research, "memory_read", {"names": ["range-ticks", "nothing-here", "range-ticks"]})

    assert not error
    said = json.loads(text)
    assert [entry["name"] for entry in said["entries"]] == ["range-ticks"]
    assert said["entries"][0]["body"].startswith("Dukascopy's files") and said["entries"][0]["revision"] == 1
    assert said["unknown"] == ["nothing-here"]
    read = memory.entry("range-ticks")
    assert read["read_by"] == "claude-mac" and read["read_at"] and read["revision"] == 1
    assert memory.seq() == seq


def test_too_many_or_no_entries_at_once_and_a_server_without_a_memory_are_refused(research, memory):
    none, refused = called(research, "memory_list", {})
    many, too_many = called(dataclasses.replace(research, memory=memory), "memory_read",
                            {"names": [f"entry-{index}" for index in range(11)]})

    assert refused and "not kept on this server" in none
    assert too_many and "1 to 10" in many


class WritesForbidden(MemoryStore):
    """The memory, whose changes the endpoint must never reach."""

    def _forbidden(self, *args, **kwargs):
        raise AssertionError("The MCP endpoint changed the AI agent's memory")

    save = forget = restore = set_lock = set_pause = prune = _forbidden


def test_the_endpoint_never_changes_the_memory(research, memory):
    guarded = WritesForbidden(memory.root, clock=lambda: NOW)
    research = dataclasses.replace(research, memory=guarded)

    for tool, arguments in (("memory_list", {}), ("memory_list", {"kind": "preference"}),
                            ("memory_read", {"names": ["answer-short", "range-ticks"]})):
        text, error = called(research, tool, arguments)
        assert not error, text
