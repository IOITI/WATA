"""What every MCP tool goes through: errors said or hidden, time and size limits, the call log."""

import asyncio
import json

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from src.admin.errors import DatabaseUnavailable, InvalidRequest, NotPossible
from src.admin.mcp_server import budget
from src.admin.mcp_server.budget import Refused, research_tool
from src.admin.mcp_server.calls import CallLog
from src.admin.mcp_server.gate import CALLER
from src.admin.mcp_server.tokens import Caller


class FakeServer:
    def __init__(self):
        self.tools = {}

    def add_tool(self, fn, **options):
        self.tools[options["name"]] = (fn, options)


@pytest.fixture
def calls(tmp_path):
    return CallLog(str(tmp_path / "mcp.jsonl"))


def registered(calls, fn, **options):
    server = FakeServer()
    research_tool(server, calls, name="probe", title="Probe", read_only=True, **options)(fn)
    return server.tools["probe"]


def run(tool, **arguments):
    token = CALLER.set(Caller(name="claude-mac", token_id="0123abcd", expires_at="2026-12-31T00:00:00Z"))
    try:
        return asyncio.run(tool(**arguments))
    finally:
        CALLER.reset(token)


def test_an_answer_is_compact_json_and_the_call_is_logged(calls):
    async def probe(day: str = "2026-10-15") -> dict:
        """Probe."""
        return {"day": day, "values": [1, 2]}

    tool, options = registered(calls, probe)

    assert run(tool, day="2026-10-16") == '{"day":"2026-10-16","values":[1,2]}'
    [entry] = calls.recent()
    assert (entry["caller"], entry["tool"], entry["outcome"], entry["arguments"]) == (
        "claude-mac", "probe", "ok", {"day": "2026-10-16"},
    )
    assert options["description"] == "Probe."
    assert options["structured_output"] is False
    assert options["annotations"].read_only_hint is True
    assert options["annotations"].open_world_hint is False


def test_the_tool_shows_its_own_signature_to_the_sdk(calls):
    async def probe(day: str, limit: int = 50) -> dict:
        """Probe."""
        return {}

    tool, _ = registered(calls, probe)

    import inspect
    signature = inspect.signature(tool)
    assert list(signature.parameters) == ["day", "limit"]
    assert signature.return_annotation is str


@pytest.mark.parametrize("error", [
    Refused("Open an experiment first"), InvalidRequest("The period is two days, YYYY-MM-DD"),
    NotPossible("10 backtests are waiting already: let some finish"),
])
def test_a_refusal_reaches_claude_as_its_sentence(calls, error):
    async def probe() -> dict:
        """Probe."""
        raise error

    tool, _ = registered(calls, probe)

    with pytest.raises(ToolError, match=str(error).split(":")[0]):
        run(tool)
    assert calls.recent()[0]["outcome"] == "refused"


def test_an_unexpected_error_names_only_its_kind(calls):
    async def probe() -> dict:
        """Probe."""
        raise RuntimeError("password=hunter2 in a path")

    tool, _ = registered(calls, probe)

    with pytest.raises(ToolError) as raised:
        run(tool)
    assert "RuntimeError" in str(raised.value)
    assert "hunter2" not in str(raised.value)
    assert calls.recent()[0]["outcome"] == "failed"
    assert "hunter2" not in json.dumps(calls.recent())


def test_the_database_down_is_said_plainly(calls):
    async def probe() -> dict:
        """Probe."""
        raise DatabaseUnavailable("connection refused at 10.0.0.5")

    tool, _ = registered(calls, probe)

    with pytest.raises(ToolError, match="The database is unavailable"):
        run(tool)


def test_a_tool_that_takes_too_long_is_stopped(calls):
    async def probe() -> dict:
        """Probe."""
        await asyncio.sleep(5)
        return {}

    tool, _ = registered(calls, probe, seconds=0.05)

    with pytest.raises(ToolError, match="No answer within"):
        run(tool)


def test_an_answer_too_large_for_the_context_asks_for_less(calls, monkeypatch):
    monkeypatch.setattr(budget, "CONTEXT_CHARS", 100)

    async def probe() -> dict:
        """Probe."""
        return {"rows": list(range(100))}

    tool, _ = registered(calls, probe)

    with pytest.raises(ToolError, match="fewer rows"):
        run(tool)


def test_an_export_may_be_larger_than_what_goes_into_the_context(calls, monkeypatch):
    monkeypatch.setattr(budget, "CONTEXT_CHARS", 100)

    async def probe() -> str:
        """Probe."""
        return "t,o,h,l,c\n" + "1,2,3,4,5\n" * 100

    tool, _ = registered(calls, probe, export=True)

    assert run(tool).count("\n") == 101


def test_a_long_argument_is_logged_as_its_digest(calls):
    async def probe(source: str) -> dict:
        """Probe."""
        return {"chars": len(source)}

    tool, _ = registered(calls, probe)
    source = "//@version=6\nindicator('x')\n" + "plot(close)\n" * 100

    run(tool, source=source)

    logged = calls.recent()[0]["arguments"]["source"]
    assert set(logged) == {"sha256", "chars"}
    assert "plot(close)" not in open(calls.path).read()


def test_a_writing_tool_says_whether_it_destroys_and_repeats(calls):
    async def probe() -> dict:
        """Probe."""
        return {}

    server = FakeServer()
    research_tool(server, calls, name="remove", title="Remove", read_only=False, destructive=True)(probe)
    annotations = server.tools["remove"][1]["annotations"]

    assert (annotations.read_only_hint, annotations.destructive_hint, annotations.idempotent_hint) == (False, True, False)


def test_the_call_log_is_kept_to_a_few_files(tmp_path):
    calls = CallLog(str(tmp_path / "mcp.jsonl"), max_bytes=400, backups=2, keep=5)
    for number in range(40):
        calls.record(caller="claude-mac", tool="status", arguments={"n": number}, outcome="ok", milliseconds=1)

    names = sorted(path.name for path in tmp_path.iterdir())
    assert names == ["mcp.jsonl", "mcp.jsonl.1", "mcp.jsonl.2"]
    assert [entry["arguments"]["n"] for entry in calls.recent()] == [39, 38, 37, 36, 35]
