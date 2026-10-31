"""
A research task of the AI agent (src/admin/agent/service.py): a conversation the
agent works on alone, its requests chained, each within what is left of the
task's budget, until the agent says it is done (task_done), its dollars or its
hours are spent, the operator stops it, or nothing new may start.
"""

import asyncio
import json
import threading

import pytest

from src.admin.agent.service import AgentService
from src.admin.agent.settings import AgentSettings
from src.admin.agent.store import ConversationStore, stamp
from src.admin.errors import InvalidRequest, NotPossible
from tests.test_agent_loop import (
    NOW,
    FakeBacktests,
    FakeProvider,
    FakeScripts,
    FakeTools,
    Published,
    answer,
    call,
    settled,
    text,
)

BUDGET = {"usd": 2.0, "backtests": 10, "hours": 4}


class Clock:
    def __init__(self, now=NOW):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def store(tmp_path):
    return ConversationStore(str(tmp_path / "agent"))


def agent_of(store, provider, *, clock=None, tools=None, settings=None):
    published = Published()

    async def the_key():
        return "sk-ant-x"

    agent = AgentService(settings=settings or AgentSettings(enabled=True), store=store, provider=provider,
                         tools=tools or FakeTools(), proposals=None, backtests=FakeBacktests(), scripts=FakeScripts(),
                         key=the_key, publish=published, clock=clock or Clock())
    return agent, published


async def until_the_task_ends(agent, conversation_id, seconds=5.0):
    for _ in range(int(seconds / 0.01)):
        meta = agent.store.meta(conversation_id)
        if meta["task"]["state"] != "working" and meta["status"] not in ("queued", "running", "waiting"):
            return meta
        await asyncio.sleep(0.01)
    raise AssertionError("the task did not end")


def asked_text(params) -> str:
    """The text of what the operator's side said in a request's call: its prompt, and its limits (a system note)."""
    said = []
    for message in params["messages"]:
        if message["role"] not in ("user", "system"):
            continue
        content = message["content"]
        said.extend([content] if isinstance(content, str) else
                    [block.get("text", "") for block in content if block.get("type") == "text"])
    return " ".join(said)


def test_a_task_goes_on_request_after_request_until_the_agent_says_it_is_done(store):
    provider = FakeProvider(
        answer(text("An experiment opened; two runs to read next.")),
        answer(call("task_done", "d", conclusion="No edge on 22 days.", runs_kept=["20261028-080000-aaaaaa"]),
               stop="tool_use"),
        answer(text("No edge on 22 days: nothing to propose.")),
    )
    agent, published = agent_of(store, provider)

    async def scenario():
        worker = asyncio.create_task(agent.run())
        meta = await agent.create_task("Find whether a slower line holds", None, "10.8.0.2", BUDGET)
        ended = await until_the_task_ends(agent, meta["id"])
        worker.cancel()
        return meta, ended

    meta, ended = asyncio.run(scenario())

    assert meta["title"] == "Find whether a slower line holds"
    task = ended["task"]
    assert task["state"] == "done" and task["requests"] == 2 and ended["request"] == 1
    assert task["conclusion"] == {"conclusion": "No edge on 22 days.", "runs_kept": ["20261028-080000-aaaaaa"],
                                  "next_steps": None}
    assert task["spent_usd"] == pytest.approx(ended["cost_usd"]) and task["spent_usd"] > 0
    # Each request within what is left of the task: $2 of it, under the agent's $5 a request.
    first, second = asked_text(provider.asked[0]), asked_text(provider.asked[1])
    assert "Find whether a slower line holds" in first and "$2.00 and 10 backtests" in first
    assert "cost up to $2.00" in first
    assert "The research task goes on. Spent so far: $" in second
    said = agent.conversation(meta["id"])
    assert [request["by"] for request in said["requests"]] == ["10.8.0.2", "task"]
    assert said["task"]["state"] == "done"
    assert [(event["type"], event.get("task")) for event in published.events] == [
        ("agent_finished", "working"), ("agent_finished", "done")]


def test_a_task_ends_once_its_dollars_are_spent(store):
    dear = {"input_tokens": 300_000, "output_tokens": 20_000}
    provider = FakeProvider(answer(text("A long first look."), usage=dear))
    agent, published = agent_of(store, provider)

    async def scenario():
        worker = asyncio.create_task(agent.run())
        meta = await agent.create_task("Look at the exits", None, None, {"usd": 0.5, "backtests": 0, "hours": 1})
        ended = await until_the_task_ends(agent, meta["id"])
        worker.cancel()
        return ended

    ended = asyncio.run(scenario())

    assert ended["task"]["state"] == "ended" and ended["task"]["ended_because"] == "its budget of $0.50 is spent"
    assert ended["request"] == 0 and len(provider.asked) == 1
    # Its request was told it may start no backtest: the task's are none.
    assert "start no backtest" in asked_text(provider.asked[0])
    assert published.events[-1]["task"] == "ended"


def test_a_task_created_while_the_start_recovers_asks_its_requests_one_after_the_other(store):
    """
    The start's recovery reads the conversations in a thread, and asks the next request of a task stopped between two
    of its requests. A task created while it read, queued for its first request, was taken for one of those: its next
    request was asked at once, before its first had run (seen on CI on 2026-10-29: a task over its budget asked a
    second request). Here the recovery reads the store right after the task is written.
    """
    dear = {"input_tokens": 300_000, "output_tokens": 20_000}
    provider = FakeProvider(answer(text("A long first look."), usage=dear), answer(text("Never asked.")))
    agent, _ = agent_of(store, provider)
    written = store.create

    def create_then_recover(meta):
        found = written(meta)
        agent._recover()
        return found

    store.create = create_then_recover

    async def scenario():
        meta = await agent.create_task("Look at the exits", None, None, {"usd": 0.5, "backtests": 0, "hours": 1})
        assert agent._queue.qsize() == 1
        worker = asyncio.create_task(agent.run())
        ended = await until_the_task_ends(agent, meta["id"])
        worker.cancel()
        return ended

    ended = asyncio.run(scenario())

    assert ended["task"]["ended_because"] == "its budget of $0.50 is spent"
    assert ended["request"] == 0 and ended["task"]["requests"] == 1 and len(provider.asked) == 1


def test_a_task_stopped_between_two_requests_asks_its_next_at_the_start(store):
    """What the recovery is for: a task left idle between two of its requests goes on once the agent starts."""
    provider = FakeProvider(
        answer(call("task_done", "d", conclusion="Nothing more to find.", runs_kept=[]), stop="tool_use"),
        answer(text("Nothing more to find.")),
    )
    agent, _ = agent_of(store, provider)
    task = {"goal": "Look at the exits", "budget": {"usd": 2.0, "backtests": 0, "hours": 4}, "state": "working",
            "started_at": stamp(NOW), "deadline": stamp(NOW + 4 * 3600), "spent_usd": 0.1, "backtests": 0,
            "requests": 1, "by": None, "ended_at": None, "ended_because": None, "conclusion": None}
    store.create({"id": "20261029-200000-a1b2c3", "title": "Look at the exits", "subject": None, "status": "idle",
                  "model": None, "effort": None, "created_at": stamp(NOW), "updated_at": stamp(NOW), "by": None,
                  "request": 0, "requests": 1, "cost_usd": 0.1, "usage": {}, "outcome": "done", "error": None,
                  "retry": False, "task": task})

    async def scenario():
        worker = asyncio.create_task(agent.run())
        ended = await until_the_task_ends(agent, "20261029-200000-a1b2c3")
        worker.cancel()
        return ended

    ended = asyncio.run(scenario())

    assert ended["task"]["state"] == "done" and ended["request"] == 1 and ended["task"]["requests"] == 2
    assert "The research task goes on" in asked_text(provider.asked[0])


def test_a_task_ends_once_its_hours_are_over(store):
    clock = Clock()
    provider = FakeProvider(answer(text("First look.")))
    tools = FakeTools()
    agent, _ = agent_of(store, provider, clock=clock, tools=tools)

    async def scenario():
        worker = asyncio.create_task(agent.run())
        meta = await agent.create_task("Look at the entries", None, None, {"usd": 5, "backtests": 4, "hours": 1})
        # The first request ends after its hour.
        clock.now = NOW + 3601
        ended = await until_the_task_ends(agent, meta["id"])
        worker.cancel()
        return ended

    ended = asyncio.run(scenario())

    assert ended["task"]["ended_because"] == "its 1 hours are over"
    assert ended["task"]["deadline"] == stamp(NOW + 3600)


def test_the_operator_stops_a_task_and_no_request_follows(store):
    agent = None
    asked = {}

    def during(name):
        agent.stop_task(asked["id"], "10.8.0.2")

    provider = FakeProvider(answer(call("status", "a"), stop="tool_use"))
    agent, _ = agent_of(store, provider, tools=FakeTools(on_call=during))

    async def scenario():
        worker = asyncio.create_task(agent.run())
        meta = await agent.create_task("Tune the stop", None, None, BUDGET)
        asked["id"] = meta["id"]
        ended = await until_the_task_ends(agent, meta["id"])
        worker.cancel()
        return ended

    ended = asyncio.run(scenario())

    assert ended["task"]["state"] == "ended" and ended["task"]["ended_because"] == "stopped by the operator"
    assert ended["task"]["stopped_by"] == "10.8.0.2" and ended["outcome"] == "stopped"
    assert len(provider.asked) == 1
    with pytest.raises(NotPossible, match="ended already"):
        agent.stop_task(ended["id"], None)


def test_a_stop_that_comes_as_a_request_ends_is_kept(store):
    """
    The operator's stop runs in a thread of the routes, while the worker, on the event loop, reads the task whose
    request ended and writes it back with its next request asked. A stop between the two was written over: the task
    went on, its stop answered (seen on CI on 2026-10-31). Here the stop comes while the worker reads the activity.
    """
    provider = FakeProvider(answer(text("Never asked.")))
    agent, _ = agent_of(store, provider)
    conversation_id = store.new_id(NOW)
    task = {"goal": "Tune", "budget": BUDGET, "state": "working", "started_at": stamp(NOW),
            "deadline": stamp(NOW + 4 * 3600), "spent_usd": 0.1, "backtests": 0, "requests": 1, "by": None,
            "ended_at": None, "ended_because": None, "conclusion": None}
    store.create({"id": conversation_id, "title": "Tune", "status": "idle", "request": 0, "requests": 1,
                  "cost_usd": 0.1, "updated_at": stamp(NOW), "task": task})
    read, stops = store.read, []

    def read_as_the_operator_stops(conversation, journal):
        if journal == "activity" and not stops:
            stops.append(threading.Thread(target=agent.stop_task, args=(conversation, "10.8.0.2")))
            stops[0].start()
            stops[0].join(0.5)
        return read(conversation, journal)

    store.read = read_as_the_operator_stops
    agent._task_went_on(conversation_id, 0, "done", agent.settings.model, agent.settings.effort)
    stops[0].join(5)

    stopped = store.meta(conversation_id)
    assert stopped["task"]["state"] == "ended" and stopped["task"]["ended_because"] == "stopped by the operator"

    async def scenario():
        worker = asyncio.create_task(agent.run())
        ended = await until_the_task_ends(agent, conversation_id)
        worker.cancel()
        return ended

    ended = asyncio.run(scenario())

    # The request it had asked waited its turn, stopped: it ended there, nothing asked of the model.
    assert ended["request"] == 1 and ended["outcome"] == "stopped" and provider.asked == []


def test_a_stop_that_comes_once_the_next_request_is_queued_is_that_request_s(store):
    """
    A stop that comes once the worker has queued the task's next request, before it lets the conversation go, is that
    request's: the worker's own end dropped it, and the request was asked all the same, after the operator's stop.
    """
    provider = FakeProvider(answer(text("A first look.")), answer(text("Asked after the stop.")))
    agent, published = agent_of(store, provider)

    def stopped_as_it_goes_on(event_type, **data):
        published(event_type, **data)
        if event_type == "agent_finished" and data.get("task") == "working":
            agent.stop_task(data["conversation_id"], "10.8.0.2")

    agent._publish = stopped_as_it_goes_on

    async def scenario():
        worker = asyncio.create_task(agent.run())
        meta = await agent.create_task("Tune the stop", None, None, BUDGET)
        ended = await until_the_task_ends(agent, meta["id"])
        worker.cancel()
        return ended

    ended = asyncio.run(scenario())

    assert ended["task"]["state"] == "ended" and ended["task"]["ended_because"] == "stopped by the operator"
    assert ended["request"] == 1 and ended["outcome"] == "stopped" and len(provider.asked) == 1


def test_a_pause_ends_a_task_between_two_requests(store):
    agent = None

    def during(name):
        agent.set_pause(True, "10.8.0.2")

    provider = FakeProvider(answer(call("status", "a"), stop="tool_use"), answer(text("Read.")))
    agent, _ = agent_of(store, provider, tools=FakeTools(on_call=during))

    async def scenario():
        worker = asyncio.create_task(agent.run())
        meta = await agent.create_task("Tune the stop", None, None, BUDGET)
        ended = await until_the_task_ends(agent, meta["id"])
        worker.cancel()
        return ended

    ended = asyncio.run(scenario())

    assert ended["task"]["state"] == "ended" and "paused" in ended["task"]["ended_because"]
    assert ended["outcome"] == "done", "the request under way went on to its end"


def test_a_task_left_between_two_requests_by_a_restart_goes_on(store):
    agent, _ = agent_of(store, FakeProvider())
    conversation_id = store.new_id(NOW)
    task = {"goal": "Tune", "budget": BUDGET, "state": "working", "started_at": stamp(NOW),
            "deadline": stamp(NOW + 4 * 3600), "spent_usd": 0.1, "backtests": 0, "requests": 1, "by": None,
            "ended_at": None, "ended_because": None, "conclusion": None}
    store.create({"id": conversation_id, "title": "Tune", "status": "idle", "request": 0, "requests": 1,
                  "cost_usd": 0.1, "updated_at": stamp(NOW), "task": task})

    agent._recover()

    meta = store.meta(conversation_id)
    assert meta["status"] == "queued" and meta["request"] == 1 and meta["task"]["requests"] == 2


@pytest.mark.parametrize("budget, said", [
    ({"usd": 21, "backtests": 1, "hours": 1}, "max_task_usd"),
    ({"usd": 0.4, "backtests": 1, "hours": 1}, "from \\$0.50"),
    ({"usd": 5, "backtests": 41, "hours": 1}, "max_task_backtests"),
    ({"usd": 5, "backtests": 1, "hours": 13}, "max_task_hours"),
    ({"usd": 5, "backtests": 1.5, "hours": 1}, "max_task_backtests"),
    ([], "dollars, backtests and hours"),
])
def test_a_task_s_budget_is_bounded_by_the_settings(store, budget, said):
    agent, _ = agent_of(store, FakeProvider())

    with pytest.raises(InvalidRequest, match=said):
        asyncio.run(agent.create_task("Tune", None, None, budget))
    assert store.metas() == []


def test_a_conversation_that_is_not_a_task_cannot_be_stopped_as_one(store):
    agent, _ = agent_of(store, FakeProvider(answer(text("Hello."))))

    async def scenario():
        worker = asyncio.create_task(agent.run())
        meta = await agent.create(None, "Hello", None)
        await settled(agent, meta["id"])
        worker.cancel()
        return meta

    meta = asyncio.run(scenario())

    with pytest.raises(InvalidRequest, match="not a research task"):
        agent.stop_task(meta["id"], None)
    assert agent.conversations()[0]["task"] is None
    assert json.dumps(agent.conversation(meta["id"])["task"]) == "null"
