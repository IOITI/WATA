"""
The pictures the AI agent's tools give (chart_view's day): the agent's tools
take them from the MCP endpoint's image block, through a job too, at most
MAX_PICTURES a request; the request keeps each as a file of the conversation,
its tool's result holding it, and sends the model its bytes with each later
answer; the activity says it under its step, and the conversation's files
serve it. A day shared at a conversation's start may bring one too.
"""

import asyncio
import base64
import json
from types import SimpleNamespace

import pytest

from src.admin.agent import tools as agent_tools
from src.admin.agent import transcript
from src.admin.agent.proposals import Proposals
from src.admin.agent.service import AgentService
from src.admin.agent.store import ConversationStore
from src.admin.agent.tools import MAX_PICTURES, AgentTools, ToolAnswer
from tests.test_admin_backtests import config, scripts  # noqa: F401 (fixtures)
from tests.test_agent_loop import TOOLS, FakeProvider, FakeSession, FakeTools, answer, call, conversation, run_request, text
from tests.test_mcp_tools_pine import PNG, VIEWED, Studies
from tests.test_mcp_tools_read import journal, research  # noqa: F401 (fixtures)

CONVERSATION = "20261103-080000-a1b2c3"
VIEW = {"day": "2026-10-14", "asset": "capital/US100"}


class Views(Studies):
    """A view's study, its answer new each time as the child's is."""

    def __init__(self):
        super().__init__()

    async def study(self, mode, request):
        self.asked.append((mode, request))
        found = json.loads(json.dumps(VIEWED))
        if not request.get("picture", True):
            found.pop("picture")
        return found


class PicturedSession(FakeSession):
    """Its chart_view answers with a picture, as the agent's tools give it."""

    async def call(self, name, arguments, request, progress=lambda detail: None, stopping=lambda: False):
        if name == "chart_view":
            self.tools.calls.append(name)
            return ToolAnswer('{"ok":true,"view":{"rows":168}}', picture=PNG, picture_title="Cross v1 · 14 Oct")
        return await super().call(name, arguments, request, progress, stopping)


class PicturedTools(FakeTools):
    def definitions(self):
        return [*TOOLS, {"name": "chart_view", "description": "chart_view", "input_schema": {"type": "object"}}]

    def session(self, conversation_id, task=False):
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def opened():
            yield PicturedSession(self, conversation_id)

        return opened()


@pytest.fixture
def store(tmp_path):
    return ConversationStore(str(tmp_path / "agent"))


def test_a_tool_s_picture_is_kept_with_the_conversation_and_sent_the_model_in_its_result(store):
    conversation_id = conversation(store)
    provider = FakeProvider(answer(call("chart_view", "t1", day="2026-10-14"), stop="tool_use"),
                            answer(text("It lost at 15:30: the long came at the top of the swing.")))

    _, outcome = run_request(store, conversation_id, provider, tools=PicturedTools())

    assert outcome.status == "done"
    (kept,) = store.meta(conversation_id)["pictures"]
    assert (kept["type"], kept["media_type"], kept["tool"], kept["title"]) == ("image", "image/png", "chart_view",
                                                                              "Cross v1 · 14 Oct")
    assert store.read_attachment(conversation_id, kept["id"]) == PNG
    # The transcript keeps a reference to it, never its bytes.
    (result,) = store.read(conversation_id, "messages")[3]["content"]
    assert result["content"] == [{"type": "text", "text": '{"ok":true,"view":{"rows":168}}'}, transcript.file_block(kept)]
    # The model is sent its bytes, inside the tool's result.
    (sent,) = provider.asked[1]["messages"][3]["content"]
    assert sent["content"][1] == {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                             "data": base64.b64encode(PNG).decode("ascii")}}
    # The activity says it with its step, before the step ends.
    activity = store.read(conversation_id, "activity")
    assert [event["event"] for event in activity] == ["started", "tool", "picture", "tool_done", "answer", "ended"]
    said = transcript.project(store.meta(conversation_id), activity, {})["requests"][0]
    assert said["pictures"] == [{"file": kept["id"], "title": "Cross v1 · 14 Oct", "size": len(PNG), "tool": "chart_view",
                                 "step": "t1"}]
    # The conversation's files serve it, as those the operator attached.
    entry, data = AgentService.attachment(SimpleNamespace(store=store), conversation_id, kept["id"])
    assert data == PNG and entry["media_type"] == "image/png"


def test_a_day_shared_at_the_start_brings_its_picture_as_the_request_s_own(store):
    conversation_id = conversation(store)
    provider = FakeProvider(answer(text("Seen.")))

    async def shared(session, entry, request):
        return "The day of the run", [(PNG, "Its day", "chart_view")]

    from src.admin.agent.loop import RequestRun, Used
    from src.admin.agent.settings import AgentSettings

    run = RequestRun(conversation_id=conversation_id, request=0, store=store, provider=provider, tools=FakeTools(),
                     settings=AgentSettings(enabled=True), publish=lambda event: None, stopping=lambda: False,
                     clock=lambda: 0, used=Used(),
                     opening={"prompt": "Why did it lose?", "subject": {"kind": "run", "id": "r", "day": "2026-10-14"},
                              "limits": "Limits", "shared": shared})
    asyncio.run(run.run())

    (kept,) = store.meta(conversation_id)["pictures"]
    opening = store.read(conversation_id, "messages")[0]["content"]
    assert opening == [text("The day of the run"), transcript.file_block(kept), text("Why did it lose?")]
    assert provider.asked[0]["messages"][0]["content"][1]["type"] == "image"
    said = transcript.project(store.meta(conversation_id), store.read(conversation_id, "activity"), {})["requests"][0]
    assert said["pictures"][0]["step"] is None


def test_the_context_view_counts_a_tool_s_picture_with_the_files_never_with_the_results():
    block = {"type": "wata_file", "file": "f0123456789", "file_type": "image", "media_type": "image/png", "name": "x.png"}
    lines = [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1",
                                           "content": [{"type": "text", "text": "abc"}, block]}]}]

    messages = transcript.api_messages(lines, lambda file_id: PNG)

    assert transcript.files_in(lines) == 1
    assert transcript.composition(messages)["results"] == 3
    stripped = transcript.without_files(messages)
    assert stripped[0]["content"][0]["content"] == [{"type": "text", "text": "abc"}]
    # A result of before, a text alone, is sent as it was.
    assert transcript.api_messages([{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": "x"}]}],
                                   None)[0]["content"][0]["content"] == "x"


# ── the agent's tools: the endpoint's image block taken, a job's, and no more than MAX_PICTURES a request ──


def started(research, store):
    tools = AgentTools(research, Proposals(store, research.scripts))
    asyncio.run(tools.start())
    return tools


def calls(tools, *asked):
    async def calling():
        async with tools.session(CONVERSATION) as session:
            return [await session.call(name, arguments, 0) for name, arguments in asked]

    return asyncio.run(calling())


def test_the_agent_takes_chart_view_s_picture_as_bytes_beside_its_answer(research, store):
    research.studies = Views()
    store.create({"id": CONVERSATION, "title": "Day", "status": "running", "request": 0})

    (found,) = calls(started(research, store), ("chart_view", VIEW))

    assert not found.is_error and found.picture == PNG and found.picture_title == "Cross v1 · capital/US100"
    assert json.loads(found.text)["picture"]["width"] == 1280 and "png" not in found.text
    assert "chart_view" in agent_tools.AGENT_TOOLS and "chart_view" in agent_tools.STUDIES


def test_a_request_keeps_its_pictures_to_a_bound_and_then_asks_for_the_table_alone(research, store):
    research.studies = Views()
    store.create({"id": CONVERSATION, "title": "Day", "status": "running", "request": 0})

    found = calls(started(research, store), *[("chart_view", VIEW)] * (MAX_PICTURES + 1))

    assert [answer.picture is not None for answer in found] == [True] * MAX_PICTURES + [False]
    assert [request["picture"] for _, request in research.studies.asked] == [True] * MAX_PICTURES + [False]
    assert f"this request had its {MAX_PICTURES}" in found[-1].text


def test_a_view_that_outlasts_its_call_brings_its_picture_through_the_job(research, store, monkeypatch):
    from src.admin.mcp_server import budget, jobs

    class SlowView(Views):
        async def study(self, mode, request):
            await asyncio.sleep(0.4)
            return await super().study(mode, request)

    monkeypatch.setattr(budget, "ANSWER_SECONDS", 0.1)
    monkeypatch.setattr(jobs, "ANSWER_SECONDS", 0.1)
    research.studies = SlowView()
    store.create({"id": CONVERSATION, "title": "Day", "status": "running", "request": 0})

    (found,) = calls(started(research, store), ("chart_view", VIEW))

    assert not found.is_error and found.picture == PNG and json.loads(found.text)["view"]["rows"] == 168
