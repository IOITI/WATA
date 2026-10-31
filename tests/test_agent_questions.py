"""
A question a research task asks the operator (ask_operator, src/admin/agent/service.py): once its request ends,
the task waits, asking nothing more, said on the admin's live events and told once on Telegram; the operator's
answer starts its next request with it, its deadline moved by the time it waited; unanswered for two days, the task
ends. Outside a task, the agent asks in its answer.
"""

import asyncio

from src.admin.agent.store import stamp
from src.admin.agent.tools import AgentTools
from src.admin.agent.proposals import Proposals
from src.admin.agent_telegram import AgentTeller
from src.admin.errors import NotPossible
from tests.test_admin_backtests import config, pine, scripts  # noqa: F401 (fixtures)
from tests.test_agent_loop import NOW, FakeProvider, answer, call, settled, text
from tests.test_agent_tasks import BUDGET, Clock, agent_of, asked_text, store  # noqa: F401 (fixtures)
from tests.test_agent_tools import calls, store as tools_store  # noqa: F401 (fixtures)
from tests.test_mcp_tools_read import journal, research  # noqa: F401 (fixtures)

QUESTION = {"question": "Fast lines or slow ones first?", "why": "Each takes half the budget.",
            "choices": ["Fast", "Slow"]}


def asking_agent(store, clock):
    provider = FakeProvider(
        answer(call("ask_operator", "q", **QUESTION), stop="tool_use"),
        answer(text("I asked which first: the task waits for the answer.")),
        answer(call("task_done", "d", conclusion="Slow lines hold on 22 days."), stop="tool_use"),
        answer(text("Slow lines hold on 22 days.")),
    )
    agent, published = agent_of(store, provider, clock=clock)
    return agent, provider, published


def test_a_task_waits_for_the_operator_s_answer_and_goes_on_with_it(store):
    clock = Clock()
    agent, provider, published = asking_agent(store, clock)

    async def scenario():
        worker = asyncio.create_task(agent.run())
        meta = await agent.create_task("Find a line that holds", None, "10.8.0.2", BUDGET)
        await settled(agent, meta["id"])
        waiting = agent.store.meta(meta["id"])
        listed = agent.waiting_questions()
        clock.now += 3 * 3600
        agent.answer_question(meta["id"], "Slow", "10.8.0.2")
        for _ in range(500):
            if agent.store.meta(meta["id"])["task"]["state"] != "working":
                break
            await asyncio.sleep(0.01)
        worker.cancel()
        return meta, waiting, listed

    meta, waiting, listed = asyncio.run(scenario())

    task = waiting["task"]
    assert task["state"] == "working" and task["question"]["question"] == QUESTION["question"]
    assert task["question"]["choices"] == ["Fast", "Slow"] and len(provider.asked) == 4
    assert [entry["conversation"] for entry in listed] == [meta["id"]]
    assert ("agent_question", meta["id"]) in [(event["type"], event.get("conversation_id")) for event in published.events]
    # The answer starts the next request, the deadline moved by the three hours it waited.
    assert "The operator answered your question (Fast lines or slow ones first?): Slow" in asked_text(provider.asked[2])
    ended = agent.store.meta(meta["id"])["task"]
    assert ended["state"] == "done" and ended["question"] is None and ended["answers"][0]["answer"] == "Slow"
    assert ended["deadline"] == stamp(NOW + BUDGET["hours"] * 3600 + 3 * 3600)


def test_a_question_unanswered_for_two_days_ends_its_task_and_one_answered_twice_is_refused(store):
    clock = Clock()
    agent, _, _ = asking_agent(store, clock)

    async def scenario():
        worker = asyncio.create_task(agent.run())
        meta = await agent.create_task("Find a line that holds", None, None, BUDGET)
        await settled(agent, meta["id"])
        worker.cancel()
        return meta

    meta = asyncio.run(scenario())
    assert agent.expire_questions() == []
    clock.now += 49 * 3600
    assert agent.expire_questions() == [meta["id"]]
    task = agent.store.meta(meta["id"])["task"]
    assert task["state"] == "ended" and "did not come in 48 hours" in task["ended_because"]
    try:
        agent.answer_question(meta["id"], "Slow", None)
        raise AssertionError("an answer to no question was taken")
    except NotPossible as e:
        assert "No question" in str(e)


def test_a_question_answered_as_it_expires_keeps_its_answer(store):
    """
    The expiry lists the questions, then ends each task from the task as it reads it again: an answer that came in
    between was ended over, and the request it had asked was left to run outside its task.
    """
    clock = Clock()
    agent, _, _ = asking_agent(store, clock)

    async def scenario():
        worker = asyncio.create_task(agent.run())
        meta = await agent.create_task("Find a line that holds", None, None, BUDGET)
        await settled(agent, meta["id"])
        worker.cancel()
        return meta

    meta = asyncio.run(scenario())
    clock.now += 49 * 3600
    listed = agent.waiting_questions

    def listed_then_answered():
        waiting = listed()
        agent.answer_question(meta["id"], "Slow", "10.8.0.2")
        return waiting

    agent.waiting_questions = listed_then_answered

    assert agent.expire_questions() == []
    kept = agent.store.meta(meta["id"])
    assert kept["task"]["state"] == "working" and kept["task"]["answers"][0]["answer"] == "Slow"
    assert kept["status"] == "queued" and kept["request"] == 1


def test_a_question_is_told_once_on_telegram(store, tmp_path):
    clock = Clock()
    agent, _, _ = asking_agent(store, clock)
    sent = []

    async def notify(message):
        sent.append(message)

    async def scenario():
        worker = asyncio.create_task(agent.run())
        meta = await agent.create_task("Find a line that holds", None, None, BUDGET)
        await settled(agent, meta["id"])
        teller = AgentTeller(agent=agent, path=str(tmp_path / "tell.json"), notify=notify, clock=clock)
        await teller.tell_due()
        await teller.tell_due()
        worker.cancel()

    asyncio.run(scenario())

    assert len(sent) == 1 and sent[0].startswith("❓ WATA's research task “Find a line that holds” asks:")
    assert "Choices: Fast / Slow" in sent[0]
    assert sent[0].endswith("\n\nThe task waits for your answer, in AI agent › Find a line that holds.")


def test_outside_a_task_the_agent_asks_in_its_answer(research, tools_store):
    tools = AgentTools(research, Proposals(tools_store, research.scripts))
    asyncio.run(tools.start())

    (refused,) = calls(tools, ("ask_operator", QUESTION))

    assert refused.is_error and "ask the operator in your answer" in refused.text
