"""
The AI agent's work said on Telegram once written (src/admin/agent_telegram.py):
a request asked with "Tell me on Telegram" is told once it ended, never while
under way, and once only; a task at its end; what its proposals wait for; a
failure to send tried again; a note whose conversation is gone, or too old,
dropped; what looks like a secret masked. The routes take the operator's word
for it, and the state says whether a Telegram bot is set.
"""

import asyncio
import json

from src.admin.agent.store import UnknownConversation, stamp
from src.admin.agent_telegram import ANSWER, DEBRIEF, KEPT_DAYS, TASK, AgentTeller, message_of
from tests.test_admin_api import SAME_ORIGIN, config_path, make_ctx  # noqa: F401 (fixtures)
from tests.test_agent_api import signed_in, until_settled, with_agent
from tests.test_agent_loop import FakeProvider, answer, text

CONVERSATION = "20261030-150000-a1b2c3"
NOW = 1_793_372_400.0  # 2026-10-30 15:00 UTC


class Agent:
    """Conversations as the pages read them, changed by the test."""

    def __init__(self):
        self.conversations = {}

    def conversation(self, conversation_id):
        if conversation_id not in self.conversations:
            raise UnknownConversation(conversation_id)
        return self.conversations[conversation_id]


def conversation(status="idle", request_status="done", answer_text="It holds on 21 days.", proposals=(), task=None):
    return {"id": CONVERSATION, "title": "Is the run believable?", "status": status, "error": None, "task": task,
            "proposals": list(proposals),
            "requests": [{"index": 0, "status": request_status, "detail": None, "proposals": [p["id"] for p in proposals],
                          "answer": {"text": answer_text} if answer_text else None}]}


def teller_of(tmp_path, agent, sent, fail=None, clock=lambda: NOW):
    async def notify(message):
        if fail and fail[0]:
            fail[0] -= 1
            raise ConnectionError("Telegram is out of reach")
        sent.append(message)

    return AgentTeller(agent=agent, path=str(tmp_path / "tell.json"), notify=notify, clock=clock)


def test_an_answer_is_told_once_ended_and_once_only(tmp_path):
    agent, sent = Agent(), []
    agent.conversations[CONVERSATION] = conversation(status="running", request_status="running", answer_text=None)
    teller = teller_of(tmp_path, agent, sent)
    teller.add(CONVERSATION, ANSWER, request=0)
    teller.add(CONVERSATION, ANSWER, request=0)

    asyncio.run(teller.tell_due())
    assert sent == [] and len(teller.notes()) == 1, "nothing while under way, one note for one request"
    agent.conversations[CONVERSATION] = conversation(answer_text="It **holds** on [21 days](/backtests/r1).")
    asyncio.run(teller.tell_due())
    asyncio.run(teller.tell_due())

    assert sent == ["💬 WATA's AI agent answered “Is the run believable?”:\n\nIt holds on 21 days.\n\n"
                    "The whole of it: AI agent › Is the run believable?"]
    assert teller.notes() == []


def test_what_its_proposals_wait_for_is_said_and_a_request_that_failed_says_why(tmp_path):
    agent, sent = Agent(), []
    proposal = {"id": "p0a1b2c3d", "title": "Pause new entries", "status": "proposed"}
    agent.conversations[CONVERSATION] = conversation(proposals=[proposal])
    teller = teller_of(tmp_path, agent, sent)
    teller.add(CONVERSATION, ANSWER, request=0)
    asyncio.run(teller.tell_due())
    assert sent[0].endswith("Waiting for you: Pause new entries.\n\nThe whole of it: AI agent › Is the run believable?")

    failed = conversation(request_status="failed", answer_text=None)
    failed["requests"][0]["detail"] = "The Claude API could not be reached"
    assert message_of({"kind": ANSWER, "request": 0}, failed) == (
        "💬 WATA's AI agent could not answer “Is the run believable?”: The Claude API could not be reached. "
        "AI agent › Is the run believable?")


def test_a_task_is_told_at_its_end_only(tmp_path):
    working = conversation(task={"state": "working"})
    done = conversation(task={"state": "done", "conclusion": {"conclusion": "No edge on 21 days."}})
    ended = conversation(task={"state": "ended", "ended_because": "its budget of $5.00 is spent"})

    assert message_of({"kind": TASK}, working) is None
    assert message_of({"kind": TASK}, done) == ("🔬 Research task “Is the run believable?” is done:\n\n"
                                                "No edge on 21 days.\n\nThe whole of it: AI agent › Is the run believable?")
    assert message_of({"kind": TASK}, ended) == ("🔬 Research task “Is the run believable?” ended: its budget of $5.00 "
                                                 "is spent. AI agent › Is the run believable?")


def test_an_answer_in_markdown_is_told_in_plain_lines_and_no_stop_is_doubled():
    answer_text = ("## Not believable yet\n\nIt rests on **too few days**.\n\n| Check | Result |\n| --- | :---: |\n"
                   "| Trades | under 30 |\n| Days | under 20 |\n\n---\n\n- Run it again over every recorded day")
    asked = conversation(answer_text=answer_text)
    asked["title"] = "Can this run be believed? Read its checks…"

    assert message_of({"kind": ANSWER, "request": 0}, asked) == (
        "💬 WATA's AI agent answered “Can this run be believed? Read its checks…”:\n\n"
        "Not believable yet\n\nIt rests on too few days.\n\nCheck · Result\nTrades · under 30\nDays · under 20\n\n"
        "- Run it again over every recorded day\n\nThe whole of it: AI agent › Can this run be believed? Read its checks…")


def test_a_failure_to_send_is_tried_again_and_what_cannot_be_told_goes(tmp_path):
    agent, sent, fail = Agent(), [], [1]
    agent.conversations[CONVERSATION] = conversation()
    teller = teller_of(tmp_path, agent, sent, fail=fail)
    teller.add(CONVERSATION, ANSWER, request=0)
    teller.add("20261030-150000-dddddd", ANSWER, request=0)

    asyncio.run(teller.tell_due())
    assert sent == [] and [note["conversation"] for note in teller.notes()] == [CONVERSATION], "the gone one dropped"
    asyncio.run(teller.tell_due())
    assert len(sent) == 1 and teller.notes() == []

    old = teller_of(tmp_path, agent, sent, clock=lambda: NOW + KEPT_DAYS * 86400 + 60)
    agent.conversations[CONVERSATION] = conversation(status="running", request_status="running")
    (tmp_path / "tell.json").write_text(json.dumps({"notes": [
        {"id": "aa", "conversation": CONVERSATION, "kind": ANSWER, "request": 0, "label": None, "at": stamp(NOW)}]}))
    asyncio.run(old.tell_due())
    assert old.notes() == []


def test_what_looks_like_a_secret_is_masked_and_a_debrief_names_its_day(tmp_path):
    agent, sent = Agent(), []
    agent.conversations[CONVERSATION] = conversation(answer_text="The key sk-ant-api03-abcdefghijklmnopqrstu was refused.")
    teller = teller_of(tmp_path, agent, sent)
    teller.add(CONVERSATION, ANSWER, request=0)
    asyncio.run(teller.tell_due())
    assert "sk-ant-api03-abcdefghijklmnopqrstu" not in sent[0]

    assert message_of({"kind": DEBRIEF, "request": 0, "label": "2026-10-28"}, conversation()).startswith(
        "📝 WATA's debrief of Wed 28 Oct:")


def test_the_routes_take_the_operator_s_word_to_tell(config_path, tmp_path):
    ctx = with_agent(make_ctx(config_path, tmp_path), tmp_path,
                     FakeProvider(answer(text("It holds.")), answer(text("Try 14."))))
    ctx.teller = AgentTeller(agent=ctx.agent, path=str(tmp_path / "tell.json"), notify=None)
    client = signed_in(ctx, tmp_path)
    try:
        made = client.post("/api/agent/conversations", json={"prompt": "Analyse", "tell": True}, headers=SAME_ORIGIN)
        until_settled(client, made.json()["id"])
        client.post(f"/api/agent/conversations/{made.json()['id']}/requests", json={"prompt": "Next?", "tell": True},
                    headers=SAME_ORIGIN)
        until_settled(client, made.json()["id"])
        client.post("/api/agent/conversations", json={"prompt": "Untold"}, headers=SAME_ORIGIN)
        state = client.get("/api/agent").json()
    finally:
        client.__exit__(None, None, None)

    assert [(note["conversation"], note["kind"], note["request"]) for note in ctx.teller.notes()] == [
        (made.json()["id"], ANSWER, 0), (made.json()["id"], ANSWER, 1)]
    # The fake context has no credentials: whether a Telegram bot is set cannot be told.
    assert state["telegram"] is None
