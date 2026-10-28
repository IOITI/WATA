"""
The AI agent's daily debrief (src/admin/agent_debrief.py): once the market is
closed by the live rules, the day's conversation is asked once, a quiet day is
noted without a call to the API, what holds the agent back is tried again at
the next tick, and the debrief written is said on Telegram once.
"""

import asyncio
import json
from datetime import date, datetime

import pytest
import pytz

from src.admin.agent.prompts import DEBRIEF_PROMPT
from src.admin.agent.settings import AgentSettings
from src.admin.agent.store import ConversationStore
from src.admin.agent_debrief import DailyDebrief, debriefed_day, said_of
from src.admin.errors import NotPossible
from tests.test_agent_loop import FakeProvider, answer, settled, text
from tests.test_agent_tasks import agent_of, asked_text

PARIS = pytz.timezone("Europe/Paris")
CONFIG = {"trade": {"config": {"general": {"timezone": "Europe/Paris"}}, "rules": [
    {"rule_type": "market_hours", "rule_name": "market_hours",
     "rule_config": {"trading_start_hour": 8, "trading_end_hour": 22}}]}}


def at(text_time: str) -> float:
    """A moment of Paris, as seconds."""
    return PARIS.localize(datetime.fromisoformat(text_time)).timestamp()


@pytest.mark.parametrize("moment, day", [
    ("2026-10-28 22:30", "2026-10-28"),  # Wednesday, its hours over
    ("2026-10-29 07:00", "2026-10-28"),  # Thursday before its hours
    ("2026-10-31 12:00", "2026-10-30"),  # Saturday: the Friday
    ("2026-11-02 07:59", "2026-10-30"),  # Monday morning: the Friday
])
def test_the_day_debriefed_is_the_newest_whose_hours_are_over(moment, day):
    assert debriefed_day(CONFIG, at(moment)).isoformat() == day


def test_telegram_is_given_the_first_lines_without_links_or_marks():
    assert said_of("**Two trades**, both [closed](/positions?find=1) at a stop.") == "Two trades, both closed at a stop."
    long = "\n".join(f"Line {number} says something about the day." for number in range(40))
    assert said_of(long).endswith(" …") and len(said_of(long)) <= 702


class Queries:
    def __init__(self, signals=(), trades=(), requests=()):
        self.signals, self.trades, self.requests = list(signals), list(trades), list(requests)

    async def journal_between(self, first, last):
        return self.signals

    async def closed_positions(self, first, last):
        return self.trades

    async def order_attempts(self, first, last):
        return self.requests


class FakeAgent:
    """The agent as the debrief asks it: create a conversation, read it back."""

    def __init__(self, **settings):
        self.settings = AgentSettings(**{"enabled": True, "debrief_enabled": True, **settings})
        self.ready = True
        self.created = []
        self.refusal = None
        self.status = "running"
        self.answer = "A quiet day: one long, sold at its target."

    async def create(self, subject, prompt, by, model=None, effort=None, *, task=None, debrief=None):
        if self.refusal:
            raise NotPossible(self.refusal)
        self.created.append({"subject": subject, "prompt": prompt, "by": by, "model": model, "effort": effort,
                             "debrief": debrief})
        return {"id": "20261028-210000-abcdef"}

    def conversation(self, conversation_id):
        return {"id": conversation_id, "status": self.status, "error": None,
                "requests": [{"answer": {"text": self.answer} if self.answer else None}]}


@pytest.fixture
def config_path(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(CONFIG))
    return str(path)


def debrief_of(agent, config_path, tmp_path, queries=None, said=None):
    async def notify(message):
        said.append(message)

    return DailyDebrief(agent=agent, queries=queries or Queries(signals=[{"signal_uuid": "a"}]), config_path=config_path,
                        path=str(tmp_path / "debriefs.json"), notify=notify if said is not None else None)


def test_once_the_market_is_closed_the_day_is_debriefed_once_and_said_once(config_path, tmp_path):
    agent, said = FakeAgent(), []
    debrief = debrief_of(agent, config_path, tmp_path, said=said)

    assert asyncio.run(debrief.catch_up(at("2026-10-28 15:00"))) is None, "the market is open"
    first = asyncio.run(debrief.catch_up(at("2026-10-28 22:30")))
    again = asyncio.run(debrief.catch_up(at("2026-10-28 22:45")))
    agent.status = "idle"
    written = asyncio.run(debrief.catch_up(at("2026-10-28 23:00")))
    asyncio.run(debrief.catch_up(at("2026-10-28 23:15")))

    assert agent.created == [{"subject": {"kind": "day", "id": "2026-10-28"}, "prompt": DEBRIEF_PROMPT, "by": "debrief",
                              "model": "claude-sonnet-5-5", "effort": "medium",
                              "debrief": {"day": "2026-10-28", "max_usd": 0.5}}]
    assert first["conversation"] == "20261028-210000-abcdef" and not first["said"]
    assert again["said"] is False, "said only once written"
    assert written["said"] is True
    assert said == ["📝 WATA's debrief of Wed 28 Oct:\n\nA quiet day: one long, sold at its target.\n\n"
                    "The whole of it: AI agent › Activity."]
    assert list(debrief.days()) == ["2026-10-28"]


def test_a_quiet_day_is_noted_without_a_call_to_the_api(config_path, tmp_path):
    agent = FakeAgent()
    debrief = debrief_of(agent, config_path, tmp_path, queries=Queries(), said=[])

    entry = asyncio.run(debrief.catch_up(at("2026-10-28 22:30")))

    assert entry["quiet"] is True and entry["conversation"] is None and agent.created == []


def test_what_holds_the_agent_back_is_tried_again_at_the_next_tick(config_path, tmp_path):
    agent = FakeAgent()
    agent.refusal = "The AI agent is paused"
    debrief = debrief_of(agent, config_path, tmp_path, said=[])

    assert asyncio.run(debrief.catch_up(at("2026-10-28 22:30"))) is None and debrief.days() == {}
    agent.refusal = None
    assert asyncio.run(debrief.catch_up(at("2026-10-28 22:45")))["conversation"] == "20261028-210000-abcdef"


def test_off_or_without_telegram_nothing_is_asked_or_said(config_path, tmp_path):
    off = FakeAgent(debrief_enabled=False)
    assert asyncio.run(debrief_of(off, config_path, tmp_path, said=[]).catch_up(at("2026-10-28 22:30"))) is None
    assert off.created == []

    quiet, said = FakeAgent(debrief_telegram=False), []
    quiet.status = "idle"
    entry = asyncio.run(debrief_of(quiet, config_path, tmp_path, said=said).catch_up(at("2026-10-28 22:30")))
    assert entry["said"] is True and said == []


def test_a_debrief_that_could_not_be_written_is_said_so(config_path, tmp_path):
    agent, said = FakeAgent(), []
    agent.status, agent.answer = "failed", None
    asyncio.run(debrief_of(agent, config_path, tmp_path, said=said).catch_up(at("2026-10-28 22:30")))
    assert said == ["📝 WATA's debrief of Wed 28 Oct could not be written: it ended without an answer. AI agent › Activity."]


def test_a_debrief_s_request_costs_its_own_and_starts_no_backtest(tmp_path):
    store = ConversationStore(str(tmp_path / "agent"))
    provider = FakeProvider(answer(text("One trade, sold at its target.")), answer(text("Because the stop was far.")))
    agent, _ = agent_of(store, provider)

    async def scenario():
        worker = asyncio.create_task(agent.run())
        meta = await agent.create({"kind": "day", "id": "2026-10-28"}, DEBRIEF_PROMPT, "debrief", "claude-sonnet-5-5",
                                  "medium", debrief={"day": "2026-10-28", "max_usd": 0.5})
        await settled(agent, meta["id"])
        await agent.ask(meta["id"], "Why there?", "10.8.0.2")
        await settled(agent, meta["id"])
        worker.cancel()
        return meta

    meta = asyncio.run(scenario())

    debriefed, asked_after = asked_text(provider.asked[0]), asked_text(provider.asked[1])
    assert "start no backtest" in debriefed and "cost up to $0.50" in debriefed
    # A question asked after it has the agent's own limits.
    assert "start up to 8 backtests" in asked_after.split("Why there?")[-1]
    assert agent.conversations()[0]["by"] == "debrief" and store.meta(meta["id"])["debrief"]["day"] == "2026-10-28"
    assert meta["title"] == "Debrief of Wed 28 Oct 2026"
    assert date.fromisoformat(store.meta(meta["id"])["debrief"]["day"]).weekday() == 2


def test_it_waits_for_the_agent_s_tools_before_its_first_look(config_path, tmp_path, monkeypatch):
    agent = FakeAgent()
    agent.ready = False
    debrief = debrief_of(agent, config_path, tmp_path, said=[])
    looked = []

    async def catch_up(now=None):
        looked.append(agent.ready)
        raise asyncio.CancelledError

    async def sleep(seconds):
        # The tools start while it waits.
        agent.ready = True

    monkeypatch.setattr(debrief, "catch_up", catch_up)
    monkeypatch.setattr("src.admin.agent_debrief.asyncio.sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(debrief.keep())

    assert looked == [True]
