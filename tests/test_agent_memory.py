"""
The AI agent's memory (src/admin/agent_memory.py) and how the agent uses it:
an entry saved, changed only from the revision its author read, forgotten,
renamed and brought back, every change journaled; what an entry says checked,
a secret refused; the operator's lock and pause, which the agent never goes
past; the agent's own tools (src/admin/agent/tools.py) beside the endpoint's
reads; a conversation opening with the memory's list and each later request
with what others changed; the memory's rules in the system prompt only with
its tools; and the Memory page's routes.
"""

import asyncio
import dataclasses
import json

import pytest

from src.admin import agent_memory
from src.admin.agent.loop import digest_of, prompt_for, request_params
from src.admin.agent.prompts import MEMORY, SYSTEM_PROMPT
from src.admin.agent.proposals import Proposals
from src.admin.agent.store import ConversationStore
from src.admin.agent.tools import AGENT_TOOLS, MEMORY_WRITES, OWN_TOOLS, AgentTools
from src.admin.agent_memory import Author, MemoryStore, UnknownEntry
from src.admin.errors import InvalidRequest, NotPossible
from src.logging_helper.redaction import forget_secrets, register_secret
from tests.test_admin_api import config_path, make_ctx  # noqa: F401 (fixtures)
from tests.test_admin_backtests import NOW, config, scripts  # noqa: F401 (fixtures)
from tests.test_agent_api import SAME_ORIGIN, signed_in
from tests.test_agent_loop import FakeProvider, answer, service_of, settled, text
from tests.test_mcp_tools_read import journal, research  # noqa: F401 (fixtures)

OPERATOR = Author.operator("10.8.0.2")
CONVERSATION = "20261028-080000-a1b2c3"
AGENT = Author.agent(CONVERSATION, 0)
LESSON = {"kind": "lesson", "description": "R60 is 6 points on capital/US100, 0.06 on dukascopy/us100",
          "body": "Dukascopy's files hold thousandths: 6 points there are R6000."}


@pytest.fixture
def memory(tmp_path):
    clock = iter(range(int(NOW), int(NOW) + 100_000, 60))
    return MemoryStore(str(tmp_path / "agent" / "memory"), clock=lambda: float(next(clock)))


def actions(memory):
    return [(line["action"], line["name"], line["by"]) for line in reversed(memory.history())]


# ── the store ──

def test_an_entry_is_saved_changed_from_the_revision_read_and_forgotten_each_change_journaled(memory):
    saved, created = memory.save(AGENT, "range-ticks", **LESSON)
    with pytest.raises(InvalidRequest, match="read it first"):
        memory.save(AGENT, "range-ticks", **{**LESSON, "body": "Changed unread."})
    with pytest.raises(NotPossible, match="changed since it was read"):
        memory.save(AGENT, "range-ticks", **{**LESSON, "body": "From an old read."}, revision=7)
    same, unchanged = memory.save(AGENT, "range-ticks", **LESSON, revision=1)
    changed, action = memory.save(OPERATOR, "range-ticks", **{**LESSON, "body": "Thousandths: R6000."}, revision=1)
    with pytest.raises(NotPossible, match="changed since"):
        memory.forget(AGENT, "range-ticks", revision=1, why="Wrong")
    forgotten = memory.forget(AGENT, "range-ticks", revision=2, why="Said twice")

    assert (created, saved["revision"], saved["created_by"], saved["created_in"]) == ("created", 1, "agent", CONVERSATION)
    assert unchanged is None and same["revision"] == 1
    assert action == "changed" and changed["revision"] == 2 and changed["updated_by"] == "operator"
    assert forgotten["body"] == "Thousandths: R6000." and memory.entries() == []
    assert actions(memory) == [("created", "range-ticks", "agent"), ("changed", "range-ticks", "operator"),
                               ("forgotten", "range-ticks", "agent")]
    last = memory.history()[0]
    assert last["why"] == "Said twice" and last["before"]["body"] == "Thousandths: R6000." and last["after"] is None
    assert last["conversation"] == CONVERSATION and memory.seq() == 3
    with pytest.raises(UnknownEntry):
        memory.entry("range-ticks")


def test_what_an_entry_says_is_checked_and_a_secret_is_refused_whoever_writes_it(memory):
    register_secret("test.internal", "Zq9-internal-token-value-0042")
    try:
        refusals = []
        for name, fields in (
            ("Range Ticks", LESSON), ("r", LESSON), ("range-ticks", {**LESSON, "kind": "rumour"}),
            ("range-ticks", {**LESSON, "description": "Two\nlines"}),
            ("range-ticks", {**LESSON, "description": "x" * 201}), ("range-ticks", {**LESSON, "body": " "}),
            ("range-ticks", {**LESSON, "body": "x" * 4001}),
            ("range-ticks", {**LESSON, "body": "The webhook is https://wata.example/webhook?token=abc123def456"}),
            ("range-ticks", {**LESSON, "body": "Its key: sk-ant-api03-abcdefghijkl"}),
            ("range-ticks", {**LESSON, "description": "The token is Zq9-internal-token-value-0042"}),
        ):
            with pytest.raises(InvalidRequest) as refused:
                memory.save(OPERATOR, name, **fields)
            refusals.append(str(refused.value))
    finally:
        forget_secrets()

    assert "lowercase letters, digits and dashes" in refusals[0] and "lowercase" in refusals[1]
    assert "one of preference, lesson, finding, reference" in refusals[2]
    assert "one line of 200" in refusals[3] and "one line of 200" in refusals[4]
    assert "empty" in refusals[5] and "4,000 characters" in refusals[6]
    assert all("looks like a secret" in said for said in refusals[7:])
    assert memory.entries() == [] and memory.history() == []


def test_the_agent_changes_neither_a_locked_entry_nor_any_while_paused_and_the_operator_does(memory):
    memory.save(OPERATOR, "answer-short", kind="preference", description="The verdict first",
                body="Five lines at most.", locked=True)
    memory.save(AGENT, "range-ticks", **LESSON, locked=True)
    with pytest.raises(NotPossible, match="locked by the operator"):
        memory.save(AGENT, "answer-short", kind="preference", description="Longer", body="Ten lines.", revision=1)
    with pytest.raises(NotPossible, match="locked by the operator"):
        memory.forget(AGENT, "answer-short", revision=1, why="Stale")
    memory.set_pause(OPERATOR, True)
    with pytest.raises(NotPossible, match="paused your memory's changes"):
        memory.save(AGENT, "new-lesson", **LESSON)
    with pytest.raises(NotPossible, match="Only the operator"):
        memory.set_pause(AGENT, False)
    operator_change, _ = memory.save(OPERATOR, "answer-short", kind="preference", description="The verdict first",
                                     body="Five lines, on a phone.", revision=1)
    memory.set_pause(OPERATOR, False)
    unlocked = memory.set_lock(OPERATOR, "answer-short", False)
    agent_change, _ = memory.save(AGENT, "answer-short", kind="preference", description="The verdict first",
                                  body="Five lines, on a phone, figures first.", revision=unlocked["revision"])

    assert memory.entry("range-ticks")["locked"] is False  # the agent sets no lock
    assert operator_change["revision"] == 2 and agent_change["revision"] == 4 and agent_change["locked"] is False
    assert [line["action"] for line in reversed(memory.history())] == [
        "created", "created", "paused", "changed", "resumed", "unlocked", "changed"]
    assert memory.pause() is None


def test_the_memory_holds_at_most_its_entries(memory, monkeypatch):
    monkeypatch.setattr(agent_memory, "MAX_ENTRIES", 2)
    memory.save(AGENT, "one", **LESSON)
    memory.save(AGENT, "two", **LESSON)

    with pytest.raises(NotPossible, match="2 entries, the most it keeps"):
        memory.save(AGENT, "three", **LESSON)
    assert [entry["name"] for entry in memory.entries()] == ["one", "two"]


def test_an_entry_is_renamed_and_any_version_comes_back_a_forgotten_one_too(memory):
    memory.save(OPERATOR, "range-ticks", **LESSON)
    renamed, action = memory.save(OPERATOR, "range-ticks", **LESSON, revision=1, new_name="tick-sizes")
    memory.save(AGENT, "tick-sizes", **{**LESSON, "body": "A worse text."}, revision=2)
    first = next(line for line in memory.history() if line["action"] == "renamed")
    back = memory.restore(OPERATOR, first["seq"])
    memory.forget(OPERATOR, "tick-sizes", revision=back["revision"])
    forgot = memory.history()[0]
    again = memory.restore(OPERATOR, forgot["seq"])
    with pytest.raises(NotPossible, match="Only the operator"):
        memory.restore(AGENT, forgot["seq"])
    with pytest.raises(NotPossible, match="no version"):
        memory.restore(OPERATOR, 999)

    assert action == "renamed" and renamed["name"] == "tick-sizes" and first["from"] == "range-ticks"
    assert back["body"] == LESSON["body"] and back["revision"] == 4
    assert again["body"] == LESSON["body"] and again["revision"] == 1
    assert [entry["name"] for entry in memory.entries()] == ["tick-sizes"]
    # The history of an entry follows it under the name it had before.
    assert [line["action"] for line in memory.history("range-ticks")] == ["renamed", "created"]


def test_a_forgotten_entry_comes_back_as_whoever_wrote_it(memory):
    memory.save(AGENT, "range-ticks", **LESSON)
    memory.forget(OPERATOR, "range-ticks", revision=1)

    back = memory.restore(OPERATOR, memory.history()[0]["seq"])

    assert (back["created_by"], back["created_in"], back["updated_by"]) == ("agent", CONVERSATION, "operator")
    assert back["created_at"] == memory.history()[-1]["at"]


def test_a_read_is_noted_without_a_change_and_old_history_is_pruned(memory):
    memory.save(AGENT, "range-ticks", **LESSON)
    seq = memory.seq()

    memory.mark_read(["range-ticks", "nothing"], "agent:" + CONVERSATION)
    read = memory.entry("range-ticks")
    removed = MemoryStore(memory.root, clock=lambda: NOW + 400 * 86400).prune(365)

    assert read["read_by"] == "agent:" + CONVERSATION and read["revision"] == 1 and memory.seq() == seq
    assert removed == 1 and memory.history() == [] and memory.entries()[0]["name"] == "range-ticks"


def test_what_changed_since_leaves_out_what_the_conversation_changed_itself(memory):
    memory.save(AGENT, "own", **LESSON)
    memory.save(Author.agent("20261028-090000-ffffff", 0), "other", **LESSON)
    memory.save(OPERATOR, "operator", **LESSON)

    since = memory.changes_since(0, CONVERSATION)

    assert [line["name"] for line in since] == ["other", "operator"]
    assert memory.changes_since(memory.seq(), CONVERSATION) == []


# ── the agent's tools ──

def started(research, store, memory):
    tools = AgentTools(research, Proposals(store, research.scripts), memory=memory)
    asyncio.run(tools.start())
    return tools


def calls(tools, *asked):
    async def calling():
        async with tools.session(CONVERSATION) as session:
            return [await session.call(name, arguments, 0) for name, arguments in asked]

    return asyncio.run(calling())


@pytest.fixture
def store(tmp_path):
    store = ConversationStore(str(tmp_path / "agent"))
    store.create({"id": CONVERSATION, "title": "Tune", "status": "running", "request": 0})
    return store


def test_with_its_memory_the_agent_reads_it_as_claude_code_does_and_changes_it_with_its_own_tools(research, store,
                                                                                                  memory):
    research = dataclasses.replace(research, memory=memory)
    tools = started(research, store, memory)
    memory.save(OPERATOR, "answer-short", kind="preference", description="The verdict first", body="Five lines.",
                locked=True)

    names = [definition["name"] for definition in tools.definitions()]
    saved, unread, read, changed, locked, forgotten = calls(
        tools,
        ("memory_save", {"name": "range-ticks", **LESSON}),
        ("memory_save", {"name": "range-ticks", **{**LESSON, "body": "Changed unread."}}),
        ("memory_read", {"names": ["range-ticks", "answer-short"]}),
        ("memory_save", {"name": "range-ticks", **{**LESSON, "body": "Thousandths: R6000."}, "revision": 1}),
        ("memory_save", {"name": "answer-short", "kind": "preference", "description": "Longer", "body": "Ten lines.",
                         "revision": 1}),
        ("memory_forget", {"name": "range-ticks", "revision": 2, "why": "Said in the instructions already"}),
    )

    assert names == [*AGENT_TOOLS, *OWN_TOOLS, *MEMORY_WRITES]
    for definition in tools.definitions():
        for words in ("Claude Code", "the Mac", "job_wait", "CSV"):
            assert words not in definition["description"], (definition["name"], words)
    assert tools.is_read("memory_list") and tools.is_read("memory_read") and not tools.is_read("memory_save")
    assert not saved.is_error and json.loads(saved.text)["created"] == "range-ticks"
    assert saved.memory == {"name": "range-ticks", "action": "created"}
    assert unread.is_error and "read it first" in unread.text and unread.memory is None
    assert [entry["revision"] for entry in json.loads(read.text)["entries"]] == [1, 1]
    assert memory.entry("answer-short")["read_by"] == f"agent:{CONVERSATION}"
    assert changed.memory == {"name": "range-ticks", "action": "changed"}
    assert locked.is_error and "locked by the operator" in locked.text
    assert forgotten.memory == {"name": "range-ticks", "action": "forgotten"}
    assert memory.history()[0]["why"] == "Said in the instructions already"
    assert {line["conversation"] for line in memory.history() if line["by"] == "agent"} == {CONVERSATION}


def test_the_memory_s_rules_are_in_the_system_prompt_only_with_its_tools():
    plain = [{"name": "status", "description": "status", "input_schema": {"type": "object"}}]
    remembering = [*plain, {"name": "memory_save", "description": "save", "input_schema": {"type": "object"}}]

    assert prompt_for(plain) == SYSTEM_PROMPT and MEMORY not in SYSTEM_PROMPT
    assert MEMORY in prompt_for(remembering)
    assert digest_of(plain) != digest_of(remembering)
    from src.admin.agent import provider as claude

    params = request_params(claude.MODELS["claude-haiku-5-5"], "low", remembering, [], False)
    assert MEMORY in params["system"][0]["text"]
    assert all(tool["eager_input_streaming"] for tool in params["tools"])


# ── what a request is told of it ──

def first_blocks(store, conversation_id):
    return [line["content"][0]["text"] for line in store.read(conversation_id, "messages")
            if line["role"] == "user" and isinstance(line["content"], list) and line["content"]
            and line["content"][0].get("type") == "text"]


def test_a_conversation_opens_with_the_memory_s_list_and_a_later_request_with_what_others_changed(tmp_path, memory):
    store = ConversationStore(str(tmp_path / "agent"))
    provider = FakeProvider(answer(text("Read it.")), answer(text("Again.")), answer(text("Still.")))
    agent, _ = service_of(store, provider)
    agent.memory = memory
    memory.save(OPERATOR, "answer-short", kind="preference", description="The verdict first", body="Five lines.",
                locked=True)

    async def scenario():
        worker = asyncio.create_task(agent.run())
        meta = await agent.create(None, "What do you remember?", "10.8.0.2")
        await settled(agent, meta["id"])
        memory.save(Author.agent(meta["id"], 0), "own-note", **LESSON)
        memory.save(OPERATOR, "range-ticks", **LESSON)
        memory.set_pause(OPERATOR, True)
        await agent.ask(meta["id"], "And now?", "10.8.0.2")
        await settled(agent, meta["id"])
        await agent.ask(meta["id"], "Anything new?", "10.8.0.2")
        await settled(agent, meta["id"])
        worker.cancel()
        return meta["id"]

    conversation_id = asyncio.run(scenario())
    first, second, third = (line for line in store.read(conversation_id, "messages") if line["role"] == "user"
                            and line["content"][-1].get("text") in ("What do you remember?", "And now?", "Anything new?"))

    assert first["content"][0]["text"].startswith("Your memory, 1 entry")
    assert "[preference] answer-short: The verdict first (the operator's, locked" in first["content"][0]["text"]
    said = second["content"][0]["text"]
    assert said.startswith("Your memory changed since your last request:")
    assert "the operator saved range-ticks: R60 is 6 points" in said and "paused its changes" in said
    assert "own-note" not in said
    # Nothing changed since: the request opens with its prompt alone.
    assert [block["text"] for block in third["content"]] == ["Anything new?"]
    assert store.meta(conversation_id)["memory_seq"] == memory.seq()


def test_without_its_memory_a_conversation_opens_as_before(tmp_path):
    store = ConversationStore(str(tmp_path / "agent"))
    agent, _ = service_of(store, FakeProvider(answer(text("Done."))))

    async def scenario():
        worker = asyncio.create_task(agent.run())
        meta = await agent.create(None, "Hello", "10.8.0.2")
        await settled(agent, meta["id"])
        worker.cancel()
        return meta["id"]

    conversation_id = asyncio.run(scenario())

    assert first_blocks(store, conversation_id) == ["Hello"]
    assert "memory_seq" not in store.meta(conversation_id) and agent.state()["memory"] is False


def test_a_save_is_a_step_of_the_request_and_an_artefact_of_the_conversation(tmp_path, memory):
    from tests.test_agent_loop import FakeTools, FakeSession
    from src.admin.agent.tools import ToolAnswer

    class Remembering(FakeSession):
        async def call(self, name, arguments, request, progress=lambda detail: None, stopping=lambda: False):
            if name == "memory_save":
                return ToolAnswer('{"created":"range-ticks"}', memory={"name": "range-ticks", "action": "created"})
            return await super().call(name, arguments, request, progress, stopping)

    class Tools(FakeTools):
        def session(self, conversation_id, task=False):
            from contextlib import asynccontextmanager

            @asynccontextmanager
            async def opened():
                yield Remembering(self)

            return opened()

    store = ConversationStore(str(tmp_path / "agent"))
    saving = {"type": "tool_use", "id": "c1", "name": "memory_save", "input": {"name": "range-ticks", **LESSON}}
    provider = FakeProvider(answer(saving, stop="tool_use"), answer(text("Saved range-ticks.")))
    agent, _ = service_of(store, provider, tools=Tools())
    agent.memory = memory

    async def scenario():
        worker = asyncio.create_task(agent.run())
        meta = await agent.create(None, "Remember the ticks", "10.8.0.2")
        await settled(agent, meta["id"])
        worker.cancel()
        return meta["id"]

    conversation_id = asyncio.run(scenario())
    said = agent.conversation(conversation_id)

    assert said["requests"][0]["memory"] == [{"name": "range-ticks", "action": "created"}]
    assert [(line["kind"], line["id"], line["action"]) for line in store.artefacts()] == [
        ("memory", "range-ticks", "created")]


# ── the Memory page's routes ──

def test_the_page_reads_changes_locks_pauses_and_brings_back_entries_from_their_revision(config_path, tmp_path):
    ctx = make_ctx(config_path, tmp_path)
    ctx.agent_memory = MemoryStore(str(tmp_path / "agent" / "memory"))
    client = signed_in(ctx, tmp_path)
    entry = {"name": "answer-short", "kind": "preference", "description": "The verdict first", "body": "Five lines."}
    try:
        created = client.post("/api/agent/memory", json=entry, headers=SAME_ORIGIN)
        taken = client.post("/api/agent/memory", json=entry, headers=SAME_ORIGIN)
        secret = client.post("/api/agent/memory", json={**entry, "name": "key", "body": "sk-ant-api03-abcdefghijkl"},
                             headers=SAME_ORIGIN)
        stale = client.put("/api/agent/memory/answer-short", json={**entry, "body": "Ten.", "revision": 9},
                           headers=SAME_ORIGIN)
        renamed = client.put("/api/agent/memory/answer-short",
                             json={**entry, "name": "answers", "body": "Ten lines.", "revision": 1}, headers=SAME_ORIGIN)
        unlocked = client.post("/api/agent/memory/answers/lock", json={"locked": False}, headers=SAME_ORIGIN)
        paused = client.post("/api/agent/memory/pause", headers=SAME_ORIGIN)
        state = client.get("/api/agent/memory").json()
        forgot = client.delete("/api/agent/memory/answers?revision=3", headers=SAME_ORIGIN)
        unknown = client.delete("/api/agent/memory/answers?revision=3", headers=SAME_ORIGIN)
        history = client.get("/api/agent/memory/history").json()["items"]
        back = client.post("/api/agent/memory/restore", json={"seq": history[0]["seq"]}, headers=SAME_ORIGIN)
        resumed = client.post("/api/agent/memory/resume", headers=SAME_ORIGIN)
        of_one = client.get("/api/agent/memory/history?name=answer-short").json()["items"]
    finally:
        client.__exit__(None, None, None)

    assert created.status_code == 201 and created.json()["locked"] is True and created.json()["created_by"] == "operator"
    assert taken.status_code == 409 and secret.status_code == 422 and "secret" in secret.json()["detail"]
    assert stale.status_code == 409 and "read it again" in stale.json()["detail"]
    assert renamed.status_code == 200 and renamed.json()["name"] == "answers" and renamed.json()["revision"] == 2
    assert unlocked.json()["locked"] is False and paused.json()["pause"]["by"]
    assert [found["name"] for found in state["entries"]] == ["answers"] and state["pause"] is not None
    assert state["agent"] is False and state["used"] is False and state["limits"]["entries"] == 200
    assert forgot.status_code == 204 and unknown.status_code == 404
    assert [line["action"] for line in history] == ["forgotten", "paused", "unlocked", "renamed", "created"]
    assert history[0]["address"] and history[0]["by"] == "operator"
    assert back.status_code == 200 and back.json()["name"] == "answers" and resumed.json()["pause"] is None
    assert [line["action"] for line in of_one] == ["renamed", "created"]
