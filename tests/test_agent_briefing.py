"""
The AI agent's morning briefing (src/admin/agent_briefing.py): written from its
minutes before the trading start (in the market's own timezone) to an hour
after it, once a day, never on a weekend; a day the US market is closed gets a
line on Telegram and no call to the API; what holds the agent back is tried
again while the window lasts. Its conversation is shared the day ahead as the
tools read it, and the day before as its debrief told it.
"""

import asyncio
import json
from datetime import datetime

import pytest
import pytz

from src.admin.agent.prompts import BRIEFING_PROMPT
from src.admin.agent.settings import AgentSettings
from src.admin.agent.store import ConversationStore
from src.admin.agent_briefing import MorningBriefing, briefing_window
from src.admin.agent_telegram import BRIEFING, LINE
from src.admin.errors import NotPossible
from tests.test_agent_loop import FakeProvider
from tests.test_agent_subjects import Session
from tests.test_agent_tasks import agent_of

NEW_YORK = pytz.timezone("America/New_York")
# Trading hours of New York (9 to 16), days counted in Paris.
CONFIG = {"trade": {"config": {"general": {"timezone": "Europe/Paris"}}, "rules": [
    {"rule_type": "market_hours", "rule_name": "market_hours",
     "rule_config": {"trading_start_hour": 9, "trading_end_hour": 16, "timezone": "America/New_York"}}]}}


def at(text_time: str) -> float:
    """A moment of New York, as seconds."""
    return NEW_YORK.localize(datetime.fromisoformat(text_time)).timestamp()


@pytest.mark.parametrize("moment, day", [
    ("2026-10-30 08:29", None),          # before its 30 minutes
    ("2026-10-30 08:30", "2026-10-30"),  # Friday, 30 minutes before the start
    ("2026-10-30 09:59", "2026-10-30"),  # still within its hour after the start
    ("2026-10-30 10:00", None),          # too late: the day goes without one
    ("2026-10-31 08:45", None),          # Saturday
    ("2026-10-26 08:45", "2026-10-26"),  # Monday, the clocks of Europe changed alone (Paris is 5 h ahead)
])
def test_its_window_is_before_the_start_in_the_market_s_own_hours(moment, day):
    found = briefing_window(CONFIG, at(moment), 30)
    assert (found[0] if found else None) == day


class FakeAgent:
    def __init__(self, **settings):
        self.settings = AgentSettings(**{"enabled": True, "briefing_enabled": True, **settings})
        self.ready = True
        self.created = []
        self.refusal = None

    async def create(self, subject, prompt, by, model=None, effort=None, *, task=None, debrief=None, job=None):
        if self.refusal:
            raise NotPossible(self.refusal)
        self.created.append({"subject": subject, "prompt": prompt, "by": by, "model": model, "effort": effort, "job": job})
        return {"id": "20261030-123000-abcdef"}


class Teller:
    def __init__(self):
        self.notes = []

    def add(self, conversation_id, kind, *, request=None, label=None, text=None):
        self.notes.append((conversation_id, kind, request, label, text))


@pytest.fixture
def config_path(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(CONFIG))
    return str(path)


def briefing_of(agent, config_path, tmp_path, teller=None):
    return MorningBriefing(agent=agent, config_path=config_path, path=str(tmp_path / "briefings.json"), teller=teller)


def test_the_day_is_briefed_once_in_its_window_and_told(config_path, tmp_path):
    agent, teller = FakeAgent(), Teller()
    briefing = briefing_of(agent, config_path, tmp_path, teller)

    assert asyncio.run(briefing.catch_up(at("2026-10-30 07:00"))) is None
    first = asyncio.run(briefing.catch_up(at("2026-10-30 08:31")))
    again = asyncio.run(briefing.catch_up(at("2026-10-30 08:45")))

    assert agent.created == [{"subject": {"kind": "briefing", "id": "2026-10-30"}, "prompt": BRIEFING_PROMPT,
                              "by": "briefing", "model": "claude-haiku-5-5", "effort": "low",
                              "job": {"kind": "briefing", "day": "2026-10-30", "max_usd": 0.3, "backtests": 0,
                                      "title": "Briefing of Fri 30 Oct 2026"}}]
    assert first == again and first["conversation"] == "20261030-123000-abcdef"
    assert teller.notes == [("20261030-123000-abcdef", BRIEFING, 0, "2026-10-30", None)]


def test_a_day_the_us_market_is_closed_gets_a_line_and_no_call(config_path, tmp_path):
    agent, teller = FakeAgent(), Teller()

    entry = asyncio.run(briefing_of(agent, config_path, tmp_path, teller).catch_up(at("2026-11-26 08:45")))

    assert agent.created == [] and entry["closed"] == "the US market is closed (Thanksgiving Day)"
    assert teller.notes == [(None, LINE, None, None,
                             "☀️ WATA, Thu 26 Nov: the US market is closed (Thanksgiving Day). Nothing is traded today.")]


def test_what_holds_the_agent_back_is_tried_again_while_its_window_lasts(config_path, tmp_path):
    agent = FakeAgent()
    agent.refusal = "The AI agent is paused"
    briefing = briefing_of(agent, config_path, tmp_path, Teller())

    assert asyncio.run(briefing.catch_up(at("2026-10-30 08:31"))) is None and briefing.days() == {}
    agent.refusal = None
    assert asyncio.run(briefing.catch_up(at("2026-10-30 09:15")))["conversation"] == "20261030-123000-abcdef"


def test_off_or_without_telegram(config_path, tmp_path):
    off = FakeAgent(briefing_enabled=False)
    assert asyncio.run(briefing_of(off, config_path, tmp_path).catch_up(at("2026-10-30 08:31"))) is None
    quiet, teller = FakeAgent(briefing_telegram=False), Teller()
    asyncio.run(briefing_of(quiet, config_path, tmp_path, teller).catch_up(at("2026-10-30 08:31")))
    assert len(quiet.created) == 1 and teller.notes == []


def test_the_day_ahead_is_shared_as_the_tools_read_it_with_the_day_before_s_debrief(tmp_path):
    store = ConversationStore(str(tmp_path / "agent"))
    agent, _ = agent_of(store, FakeProvider())
    store.create({"id": "20261029-213000-aaaaaa", "title": "Debrief", "status": "idle", "request": 0,
                  "debrief": {"day": "2026-10-29", "max_usd": 0.5}})
    store.append("20261029-213000-aaaaaa", "activity", {"at": "x", "event": "answer", "request": 0,
                                                         "text": "Two trades, both won."})
    session = Session(offered=("ops_trading_now", "ops_health", "rule_list", "ops_changes"))

    shared = asyncio.run(agent._shared_briefing(session, {"kind": "briefing", "id": "2026-10-30"}, 0))

    assert [name for name, _ in session.asked] == ["ops_trading_now", "ops_health", "rule_list", "ops_changes"]
    assert "The operator shares the day ahead 2026-10-30" in shared
    assert "The debrief of 2026-10-29:\nTwo trades, both won." in shared
    # Without the admin's reads, what the research tools read alone.
    bare = Session(offered=("rule_list",))
    asyncio.run(agent._shared_briefing(bare, {"kind": "briefing", "id": "2026-10-30"}, 0))
    assert [name for name, _ in bare.asked] == ["rule_list"]
