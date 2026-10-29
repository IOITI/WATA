"""
One request of the AI agent (src/admin/agent/loop.py) and the service that
queues them (service.py), against a scripted Claude API and fake tools: what is
kept at each step, what is published, the limits, a stop, a refusal, a failure,
a request taken up again after a restart, and the requests the API is sent.
"""

import asyncio
from contextlib import asynccontextmanager

import pytest

from src.admin.agent import provider as claude
from src.admin.agent.loop import CALLS_AFTER_LIMIT, DONE, RequestRun, Used, digest_of
from src.admin.agent.service import AgentService
from src.admin.agent.settings import AgentSettings
from src.admin.agent.store import ConversationStore, stamp
from src.admin.agent.tools import ToolAnswer
from src.admin.errors import InvalidRequest, NotPossible

NOW = 1_793_174_400.0
READS = {"backtest_result", "backtest_list", "status"}
TOOLS = [{"name": name, "description": name, "input_schema": {"type": "object"}}
         for name in ("status", "backtest_result", "backtest_list", "backtest_start", "exp_new")]


def answer(*content, stop="end_turn", usage=None, model="claude-opus-5-5", **extra):
    return {"content": list(content), "stop_reason": stop, "model": model,
            "usage": usage or {"input_tokens": 1000, "output_tokens": 100}, **extra}


def text(value):
    return {"type": "text", "text": value}


def call(name, call_id, **arguments):
    return {"type": "tool_use", "id": call_id, "name": name, "input": arguments}


class FakeProvider:
    """The Claude API, answering what it was given in turn: an answer, or an error to raise."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.asked = []

    async def stream(self, params, on_event):
        self.asked.append(params)
        if not self.answers:
            raise AssertionError("the API was called once more than expected")
        given = self.answers.pop(0)
        if isinstance(given, BaseException):
            raise given
        for index, block in enumerate(given["content"]):
            if block["type"] == "text":
                on_event({"kind": "block", "index": index, "type": "text", "name": None})
                on_event({"kind": "text", "index": index, "text": block["text"]})
            elif block["type"] == "tool_use":
                on_event({"kind": "block", "index": index, "type": "tool_use", "name": block["name"]})
                on_event({"kind": "input", "index": index, "length": 12})
        return given

    async def close(self):
        pass


class FakeSession:
    def __init__(self, tools):
        self.tools = tools

    async def call(self, name, arguments, request, progress=lambda detail: None, stopping=lambda: False):
        tools = self.tools
        tools.calls.append(name)
        tools.running += 1
        tools.most_at_once = max(tools.most_at_once, tools.running)
        try:
            await asyncio.sleep(0.01)
            if tools.on_call is not None:
                tools.on_call(name)
            if name == "backtest_start":
                return ToolAnswer('{"id":"20261028-080000-aaaaaa"}', runs=["20261028-080000-aaaaaa"])
            if name == "exp_new":
                return ToolAnswer('{"id":"e1"}', experiment="e1")
            if name == "task_done":
                return ToolAnswer('{"noted":true}', task_done={"conclusion": arguments.get("conclusion"),
                                                               "runs_kept": arguments.get("runs_kept") or [],
                                                               "next_steps": arguments.get("next_steps")})
            return ToolAnswer(f'{{"tool":"{name}"}}')
        finally:
            tools.running -= 1


class FakeTools:
    def __init__(self, on_call=None):
        self.calls = []
        self.running = 0
        self.most_at_once = 0
        self.on_call = on_call
        self.sessions = 0

    async def start(self):
        pass

    def definitions(self):
        return TOOLS

    def is_read(self, name):
        return name in READS

    @asynccontextmanager
    async def session(self, conversation_id, task=False):
        self.sessions += 1
        yield FakeSession(self)


@pytest.fixture
def store(tmp_path):
    return ConversationStore(str(tmp_path / "agent"))


def conversation(store, **changes):
    meta = {"id": store.new_id(NOW), "title": "Analyse", "status": "running", "request": 0,
            "digest": digest_of(TOOLS), "updated_at": stamp(NOW), **changes}
    return store.create(meta)["id"]


def run_request(store, conversation_id, provider, tools=None, settings=None, opening="Analyse this run",
                stopping=lambda: False, published=None):
    events = [] if published is None else published
    run = RequestRun(
        conversation_id=conversation_id, request=0, store=store, provider=provider, tools=tools or FakeTools(),
        settings=settings or AgentSettings(enabled=True), publish=events.append, stopping=stopping,
        clock=lambda: NOW, used=Used(),
        opening=None if opening is None else {"prompt": opening, "subject": None, "limits": "Limits note",
                                              "shared": None},
    )
    return run, asyncio.run(run.run())


def roles(store, conversation_id):
    return [line["role"] for line in store.read(conversation_id, "messages")]


def events(store, conversation_id):
    return [event["event"] for event in store.read(conversation_id, "activity")]


def test_a_request_reads_then_answers_and_each_step_is_kept(store):
    conversation_id = conversation(store)
    provider = FakeProvider(
        answer({"type": "thinking", "thinking": "I read the run first.", "signature": "s"},
               call("backtest_result", "t1", run_id="r"), stop="tool_use"),
        answer(text("It can be believed: 42 trades on 21 days.")),
    )
    published = []

    run, outcome = run_request(store, conversation_id, provider, published=published)

    assert outcome.status == "done" and outcome.usd > 0
    assert roles(store, conversation_id) == ["user", "system", "assistant", "user", "assistant"]
    results = store.read(conversation_id, "messages")[3]["content"]
    assert results == [{"type": "tool_result", "tool_use_id": "t1", "content": '{"tool":"backtest_result"}'}]
    assert events(store, conversation_id) == ["started", "note", "tool", "tool_done", "answer", "ended"]
    assert store.read(conversation_id, "activity")[-2]["text"] == "It can be believed: 42 trades on 21 days."
    assert {"type": "delta", "request": 0, "part": "text", "block": "0", "offset": 0,
            "text": "It can be believed: 42 trades on 21 days."} in published
    assert any(event["type"] == "writing" and event["tool"] == "backtest_result" for event in published)
    # What each answer cost is written with it, and in the month's spending.
    assert store.spent("2026-10") == pytest.approx(outcome.usd)
    assert run.used.tokens == {"input": 2000, "output": 200, "cache_read": 0, "cache_write": 0}


def test_the_api_is_asked_with_the_prompt_and_tools_cached_and_its_safeguards_fallback(store):
    conversation_id = conversation(store)
    provider = FakeProvider(answer(text("Done.")))

    run_request(store, conversation_id, provider)

    params = provider.asked[0]
    assert params["model"] == "claude-opus-5-5" and params["output_config"] == {"effort": "high"}
    assert params["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert params["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert all(tool["eager_input_streaming"] for tool in params["tools"])
    assert params["thinking"] == {"type": "adaptive", "display": "updates"}
    assert params["fallbacks"] == "default"
    assert set(params["betas"]) == {claude.CONTEXT_BETA, claude.UPDATES_BETA, claude.FALLBACK_BETA}
    assert [message["role"] for message in params["messages"]] == ["user", "system"]
    assert params["messages"][0]["content"] == [{"type": "text", "text": "Analyse this run"}]


def test_haiku_has_no_fallback_nor_updates_and_a_conversation_of_another_digest_drops_its_old_thinking(store):
    conversation_id = conversation(store, digest="made-before")
    provider = FakeProvider(answer(text("Done."), model="claude-haiku-5-5"))

    run_request(store, conversation_id, provider, settings=AgentSettings(enabled=True, model="claude-haiku-5-5"))

    params = provider.asked[0]
    assert "fallbacks" not in params and params["thinking"]["display"] == "summarized"
    assert params["thinking"]["block_binding"] == {"prefix_mismatch_behavior": "drop_block"}
    assert set(params["betas"]) == {claude.CONTEXT_BETA, claude.BINDING_BETA}


def test_reads_run_at_once_and_the_others_in_their_order_their_results_in_one_message(store):
    conversation_id = conversation(store)
    tools = FakeTools()
    provider = FakeProvider(
        answer(call("backtest_result", "t1"), call("exp_new", "t2"), call("backtest_list", "t3"),
               call("backtest_start", "t4"), stop="tool_use"),
        answer(text("Done.")),
    )

    run, outcome = run_request(store, conversation_id, provider, tools=tools)

    assert tools.most_at_once == 2 and tools.calls[-2:] == ["exp_new", "backtest_start"]
    results = store.read(conversation_id, "messages")[3]["content"]
    assert [result["tool_use_id"] for result in results] == ["t1", "t2", "t3", "t4"]
    assert run.used.backtests == 1 and "run" in events(store, conversation_id)
    assert [line["kind"] for line in store.artefacts()] == ["experiment", "run"]


def test_a_request_is_told_near_its_limits_held_to_them_and_ended_there(store):
    conversation_id = conversation(store)
    busy = answer(call("status", "a"), call("backtest_start", "b"), call("backtest_start", "c"), stop="tool_use")
    # Five calls are made in four answers (two, then one each: a second backtest is refused), then the answers asked for.
    provider = FakeProvider(*[busy] * (4 + CALLS_AFTER_LIMIT))
    settings = AgentSettings(enabled=True, max_tool_calls=5, max_backtests=1)

    run, outcome = run_request(store, conversation_id, provider, settings=settings)

    assert outcome.status == "limit"
    assert run.used.tool_calls == 5 and run.used.backtests == 1
    lines = store.read(conversation_id, "messages")
    first_results = lines[3]["content"]
    assert first_results[2]["is_error"] and "no more backtests (1)" in first_results[2]["content"]
    notes = [line["content"] for line in lines if line["role"] == "system"]
    assert notes[0] == "Limits note" and "answer now" in notes[1] and len(notes) == 2
    later = lines[-1]["content"]
    assert all(result["is_error"] and "5 tool calls are used" in result["content"] for result in later)


def test_a_request_stops_at_its_cost(store):
    conversation_id = conversation(store)
    dear = {"input_tokens": 0, "output_tokens": 300_000}
    provider = FakeProvider(answer(call("status", "a"), stop="tool_use", usage=dear))

    _, outcome = run_request(store, conversation_id, provider, settings=AgentSettings(enabled=True, max_request_usd=5))

    assert outcome.status == "limit" and "cost limit ($5.00)" in outcome.detail and outcome.usd == pytest.approx(6.0)


def test_a_stop_before_the_start_calls_nothing_and_one_during_the_tools_runs_no_more_writes(store):
    conversation_id = conversation(store)
    provider = FakeProvider()
    _, outcome = run_request(store, conversation_id, provider, stopping=lambda: True)
    assert outcome.status == "stopped" and provider.asked == [] and roles(store, conversation_id) == []

    conversation_id = conversation(store)
    stopped = []
    tools = FakeTools(on_call=lambda name: stopped.append(name))
    provider = FakeProvider(answer(call("exp_new", "a"), call("backtest_start", "b"), stop="tool_use"))
    _, outcome = run_request(store, conversation_id, provider, tools=tools, stopping=lambda: bool(stopped))
    results = store.read(conversation_id, "messages")[-1]["content"]
    assert outcome.status == "stopped" and tools.calls == ["exp_new"]
    assert results[1]["is_error"] and "stopped the request" in results[1]["content"]


def test_a_refusal_is_said_with_its_category_and_a_failure_with_what_to_do(store):
    conversation_id = conversation(store)
    provider = FakeProvider(answer(stop="refusal", stop_details={"type": "refusal", "category": "cyber"}))
    _, outcome = run_request(store, conversation_id, provider)
    assert outcome.status == "declined" and outcome.detail.endswith("(cyber)")

    conversation_id = conversation(store)
    _, outcome = run_request(store, conversation_id, FakeProvider(claude.NoKey()))
    assert outcome.status == "failed" and not outcome.retry and "Configuration › AI agent" in outcome.detail


def test_an_answer_that_could_not_be_read_is_asked_again(store):
    conversation_id = conversation(store)
    provider = FakeProvider(ValueError("cut JSON"), answer(text("Done.")))
    _, outcome = run_request(store, conversation_id, provider)
    assert outcome.status == "done" and len(provider.asked) == 2


def test_after_a_fallback_only_the_calls_of_the_model_that_answered_run(store):
    conversation_id = conversation(store)
    tools = FakeTools()
    fallback = {"type": "fallback", "from": {"model": "claude-opus-5-5"}, "to": {"model": "claude-opus-5"}}
    provider = FakeProvider(answer(call("backtest_start", "before"), fallback, call("status", "after"), stop="tool_use"),
                            answer(text("Done.")))

    run_request(store, conversation_id, provider, tools=tools)

    assert tools.calls == ["status"]
    sent_back = provider.asked[1]["messages"][2]["content"]
    assert [block["type"] for block in sent_back] == ["fallback", "tool_use"]


def test_a_request_cut_by_a_restart_is_taken_up_again_its_writes_never_made_twice(store):
    conversation_id = conversation(store)
    store.append(conversation_id, "messages", {"request": 0, "role": "user", "content": [text("Go")]})
    store.append(conversation_id, "messages", {"request": 0, "role": "assistant", "usd": 0.5,
                                               "content": [call("status", "r"), call("backtest_start", "w")]})
    store.append(conversation_id, "activity", {"event": "tool", "request": 0, "id": "w", "tool": "backtest_start"})
    tools = FakeTools()
    provider = FakeProvider(answer(text("Done.")))
    run = RequestRun(conversation_id=conversation_id, request=0, store=store, provider=provider, tools=tools,
                     settings=AgentSettings(enabled=True), publish=lambda event: None, stopping=lambda: False,
                     clock=lambda: NOW)
    run.resume_counts()
    outcome = asyncio.run(run.run())

    assert outcome.status == "done" and tools.calls == ["status"]
    results = store.read(conversation_id, "messages")[2]["content"]
    assert "Interrupted by a restart" in results[1]["content"]
    assert run.used.usd == pytest.approx(0.5 + claude.cost_of({"input_tokens": 1000, "output_tokens": 100},
                                                               "claude-opus-5-5"))

    answered = conversation(store)
    store.append(answered, "messages", {"request": 0, "role": "user", "content": [text("Go")]})
    store.append(answered, "messages", {"request": 0, "role": "assistant", "content": [text("All done.")]})
    _, outcome = run_request(store, answered, FakeProvider(), opening=None)
    assert outcome.status == "done" and store.read(answered, "activity")[-2]["text"] == "All done."


# ── the service ──


class FakeBacktests:
    def get(self, run_id):
        return {"id": run_id, "name": "Cross R60"}

    def paper(self, *, with_reports=True):
        return {"rules": {}, "live": None}


class FakeScripts:
    def script(self, script_id):
        return {"id": script_id, "name": "Kernel VWAP", "version": 4}


class Published:
    def __init__(self):
        self.events = []

    def __call__(self, event_type, **data):
        self.events.append({"type": event_type, **data})


def service_of(store, provider, *, key="sk-ant-x", settings=None, tools=None):
    published = Published()

    async def the_key():
        return key

    agent = AgentService(settings=settings or AgentSettings(enabled=True), store=store, provider=provider,
                         tools=tools or FakeTools(), proposals=None, backtests=FakeBacktests(), scripts=FakeScripts(),
                         key=the_key, publish=published, clock=lambda: NOW)
    return agent, published


async def settled(agent, conversation_id, seconds=5.0):
    for _ in range(int(seconds / 0.01)):
        if agent.store.meta(conversation_id)["status"] not in ("queued", "running", "waiting"):
            return agent.store.meta(conversation_id)
        await asyncio.sleep(0.01)
    raise AssertionError("the request did not end")


def test_a_request_asked_while_the_start_recovers_is_queued_once_and_asked_once(store):
    """
    The start's recovery reads the conversations in a thread and queues again those under way. One written queued
    while it reads, before it was in the queue, was queued twice and its request asked, and paid for, twice (seen on
    CI on 2026-10-29: a daily debrief asked twice, its follow-up getting the second answer). Here the recovery reads
    the store at that worst moment, for a new conversation and for a question asked after.
    """
    provider = FakeProvider(answer(text("Once.")), answer(text("And once more.")))
    agent, _ = service_of(store, provider)
    written, updated = store.create, store.update_meta
    recovering = []

    def recover_now():
        # Once at a time: the recovery writes "queued" itself.
        if not recovering:
            recovering.append(True)
            try:
                agent._recover()
            finally:
                recovering.pop()

    def create_then_recover(meta):
        found = written(meta)
        recover_now()
        return found

    def update_then_recover(conversation_id, **changes):
        found = updated(conversation_id, **changes)
        if changes.get("status") == "queued":
            recover_now()
        return found

    store.create, store.update_meta = create_then_recover, update_then_recover

    async def scenario():
        meta = await agent.create(None, "Analyse this run", "10.8.0.2")
        assert agent._queue.qsize() == 1
        worker = asyncio.create_task(agent.run())
        await settled(agent, meta["id"])
        await agent.ask(meta["id"], "And then?", "10.8.0.2")
        assert agent._queue.qsize() == 1
        await settled(agent, meta["id"])
        worker.cancel()
        return meta

    meta = asyncio.run(scenario())

    assert len(provider.asked) == 2
    assert "And then?" in str(provider.asked[1]["messages"])
    found = store.meta(meta["id"])
    assert found["status"] == "idle" and found["outcome"] == DONE and found["request"] == 1


def test_the_agent_is_ready_once_what_a_stop_left_is_queued_again(store):
    """A conversation may be created (the daily debrief waits for ready) only once the start's recovery is done."""
    provider = FakeProvider(answer(text("Taken up again.")))
    agent, _ = service_of(store, provider)
    left = conversation(store, status="queued")
    seen = []
    recover = agent._recover

    def recovering():
        seen.append(agent.ready)
        recover()

    agent._recover = recovering

    async def scenario():
        worker = asyncio.create_task(agent.run())
        await settled(agent, left)
        worker.cancel()

    asyncio.run(scenario())

    assert seen == [False] and agent.ready
    assert len(provider.asked) == 1


def test_a_conversation_is_worked_on_in_the_background_and_its_end_announced(store):
    provider = FakeProvider(answer(text("Read it.")), answer(text("Again.")))
    agent, published = service_of(store, provider)

    async def scenario():
        worker = asyncio.create_task(agent.run())
        meta = await agent.create({"kind": "run", "id": "20261027-101010-abcdef"}, "Analyse this run", "10.8.0.2")
        with pytest.raises(NotPossible, match="still working"):
            await agent.ask(meta["id"], "And?", "10.8.0.2")
        first = await settled(agent, meta["id"])
        await agent.ask(meta["id"], "And now?", "10.8.0.2")
        second = await settled(agent, meta["id"])
        worker.cancel()
        return meta, first, second

    meta, first, second = asyncio.run(scenario())

    assert meta["subject"] == {"kind": "run", "id": "20261027-101010-abcdef", "label": "Cross R60"}
    assert first["status"] == "idle" and first["outcome"] == "done" and second["request"] == 1
    assert [event["type"] for event in published.events] == ["agent_finished", "agent_finished"]
    said = agent.conversation(meta["id"])
    assert [request["status"] for request in said["requests"]] == ["done", "done"]
    assert said["requests"][1]["answer"]["text"] == "Again."
    assert second["cost_usd"] > first["cost_usd"] > 0


def test_a_request_runs_on_the_model_and_effort_it_was_asked_with_and_the_next_keeps_them(store):
    provider = FakeProvider(answer(text("A."), model="claude-sonnet-5-5"), answer(text("B."), model="claude-sonnet-5-5"),
                            answer(text("C.")))
    agent, _ = service_of(store, provider)

    async def scenario():
        worker = asyncio.create_task(agent.run())
        meta = await agent.create(None, "Hello", None, model="claude-sonnet-5-5", effort="low")
        await settled(agent, meta["id"])
        # Asked again without a choice: the conversation's last.
        await agent.ask(meta["id"], "Again", None)
        await settled(agent, meta["id"])
        await agent.ask(meta["id"], "Deeper", None, model="claude-opus-5-5", effort="max")
        ended = await settled(agent, meta["id"])
        worker.cancel()
        return ended

    ended = asyncio.run(scenario())

    assert [(params["model"], params["output_config"]["effort"]) for params in provider.asked] == [
        ("claude-sonnet-5-5", "low"), ("claude-sonnet-5-5", "low"), ("claude-opus-5-5", "max")]
    assert ended["model"] == "claude-opus-5-5" and ended["effort"] == "max"
    # The conversation's size as its last answer left it, for the pages: its prompt and output.
    assert ended["context"] == {"tokens": 1100, "model": "claude-opus-5-5", "at": stamp(NOW)}
    said = agent.conversation(ended["id"])
    assert [(request["model"], request["effort"]) for request in said["requests"]] == [
        ("claude-sonnet-5-5", "low"), ("claude-sonnet-5-5", "low"), ("claude-opus-5-5", "max")]
    # Sonnet 5.5 reads its notes between tool calls as they come, as Opus does.
    assert provider.asked[0]["thinking"]["display"] == "updates"


def test_a_model_or_an_effort_not_offered_is_refused_and_so_is_one_the_key_cannot_use(store):
    agent, _ = service_of(store, FakeProvider())

    with pytest.raises(InvalidRequest, match="The model is one of"):
        asyncio.run(agent.create(None, "Hello", None, model="claude-opus-4-8"))
    with pytest.raises(InvalidRequest, match="takes the effort"):
        asyncio.run(agent.create(None, "Hello", None, effort="extreme"))
    agent._models["claude-fable-5-1"] = {"available": False}
    with pytest.raises(NotPossible, match="Claude Fable 5.1 cannot be used with this Claude API key"):
        asyncio.run(agent.create(None, "Hello", None, model="claude-fable-5-1"))
    agent._models["claude-haiku-5-5"] = {"available": True, "efforts": ["low", "medium", "high"]}
    with pytest.raises(InvalidRequest, match="Claude Haiku 5.5 takes the effort low, medium, high"):
        asyncio.run(agent.create(None, "Hello", None, model="claude-haiku-5-5", effort="max"))
    assert store.metas() == []


class ModelsProvider(FakeProvider):
    """The Models API: what it says of each model for the key, or the error it answers."""

    def __init__(self, said):
        super().__init__()
        self.said = said

    async def model_info(self, model_id):
        given = self.said[model_id]
        if isinstance(given, BaseException):
            raise given
        return given


class ApiError(Exception):
    def __init__(self, status_code):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


def test_the_models_api_says_each_model_s_window_and_efforts_and_which_the_key_cannot_use(store):
    provider = ModelsProvider({
        "claude-opus-5-5": {"label": "Claude Opus 5.5", "window": 1_000_000, "max_output": 128_000,
                            "efforts": ["low", "medium", "high", "xhigh", "max"]},
        "claude-sonnet-5-5": {"label": "Claude Sonnet 5.5", "window": 500_000, "max_output": 64_000,
                              "efforts": ["low", "medium", "high"]},
        "claude-haiku-5-5": ApiError(503),
        "claude-fable-5-1": ApiError(404),
    })
    agent, _ = service_of(store, provider)

    asyncio.run(agent.refresh_models())

    models = {model["id"]: model for model in agent.state()["models"]}
    assert models["claude-opus-5-5"]["available"] is True
    assert (models["claude-sonnet-5-5"]["window"], models["claude-sonnet-5-5"]["efforts"]) == (500_000, ["low", "medium", "high"])
    # An answer that says nothing of the key: not known, the table's values kept.
    assert models["claude-haiku-5-5"]["available"] is None and models["claude-haiku-5-5"]["window"] == 1_000_000
    assert models["claude-fable-5-1"]["available"] is False
    assert "claude-sonnet-5-5" in models["claude-opus-5-5"]["reads"]
    assert "claude-opus-5-5" not in models["claude-sonnet-5-5"]["reads"]
    assert agent.state()["efforts"] == ["low", "medium", "high", "xhigh", "max"]


def test_nothing_starts_without_a_key_while_paused_or_once_the_month_is_spent(store):
    agent, _ = service_of(store, FakeProvider(), key=None)
    with pytest.raises(NotPossible, match="No Claude API key"):
        asyncio.run(agent.create(None, "Hello", None))

    agent, _ = service_of(store, FakeProvider())
    agent.set_pause(True, "10.8.0.2")
    with pytest.raises(NotPossible, match="paused"):
        asyncio.run(agent.create(None, "Hello", None))
    agent.set_pause(False, "10.8.0.2")

    store.add_spend({"at": stamp(NOW), "usd": 50.0})
    with pytest.raises(NotPossible, match="budget of \\$50.00 is spent"):
        asyncio.run(agent.create(None, "Hello", None))


def test_requests_a_stop_left_under_way_are_taken_up_again_at_the_start(store):
    left = conversation(store, status="running")
    store.append(left, "activity", {"event": "request", "request": 0, "prompt": "Go"})
    store.append(left, "messages", {"request": 0, "role": "user", "content": [text("Go")]})
    provider = FakeProvider(answer(text("Done after the restart.")))
    agent, published = service_of(store, provider)

    async def scenario():
        worker = asyncio.create_task(agent.run())
        meta = await settled(agent, left)
        worker.cancel()
        return meta

    meta = asyncio.run(scenario())

    assert meta["outcome"] == "done"
    assert "resumed" in events(store, left)
    # Its user message was not written twice: it was sent before the restart.
    assert roles(store, left) == ["user", "assistant"]


def test_a_request_stopped_while_it_waits_its_turn_ends_without_a_call(store):
    provider = FakeProvider()
    agent, _ = service_of(store, provider)

    async def scenario():
        meta = await agent.create(None, "Hello", None)
        agent.stop(meta["id"])
        worker = asyncio.create_task(agent.run())
        ended = await settled(agent, meta["id"])
        worker.cancel()
        return ended

    ended = asyncio.run(scenario())
    assert ended["outcome"] == "stopped" and provider.asked == []
    with pytest.raises(NotPossible, match="Only a request that failed"):
        agent.retry(ended["id"])


def test_a_page_that_stops_reading_its_stream_is_told_to_read_the_conversation_again(store):
    agent, _ = service_of(store, FakeProvider())
    conversation_id = conversation(store)

    async def scenario():
        queue = agent.subscribe(conversation_id)
        for offset in range(600):
            agent._send(conversation_id, {"type": "delta", "offset": offset})
        drained = [queue.get_nowait() for _ in range(queue.qsize())]
        agent.unsubscribe(conversation_id, queue)
        return drained

    drained = asyncio.run(scenario())
    assert {"type": "changed"} in drained and len(drained) < 600
