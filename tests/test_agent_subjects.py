"""
What a conversation of the AI agent may be about besides a run or a script
(src/admin/agent/service.py): a position, a signal, a trading day, an incident
of the health. Each is checked and named when the conversation starts, and
shared with the model as its tools read it.
"""

import asyncio
import json

import pytest

from src.admin.agent.service import LOG_OF, AgentService, _shared_trading
from src.admin.agent.settings import AgentSettings
from src.admin.agent.store import ConversationStore
from src.admin.agent.tools import ToolAnswer
from src.admin.errors import InvalidRequest
from tests.test_agent_loop import NOW, FakeBacktests, FakeProvider, FakeScripts, FakeTools, Published

SIGNAL = {"signal_uuid": "c0ffee", "action": "short", "position_id": 5001, "outcome": "executed"}


class Queries:
    """The research tools' named queries the service reads a subject with."""

    async def position_record(self, position_id):
        if position_id != "5001":
            return None
        return {"position": {"position_id": "5001", "action": "long", "instrument_name": "Turbo long US100 21000"},
                "orders": [], "requests": [], "requests_total": 0}

    async def journal_between(self, first, last):
        return [SIGNAL] if first.isoformat() == "2026-10-28" else []


@pytest.fixture
def agent(tmp_path):
    async def the_key():
        return "sk-ant-x"

    return AgentService(settings=AgentSettings(enabled=True), store=ConversationStore(str(tmp_path / "agent")),
                        provider=FakeProvider(), tools=FakeTools(), proposals=None, backtests=FakeBacktests(),
                        scripts=FakeScripts(), key=the_key, publish=Published(), clock=lambda: NOW, queries=Queries())


def started(agent, subject):
    return asyncio.run(agent.create(subject, "Why?", "10.8.0.2"))["subject"]


def test_a_position_a_signal_a_day_and_an_incident_are_named_when_asked_about(agent):
    assert started(agent, {"kind": "position", "id": "5001"}) == {
        "kind": "position", "id": "5001", "label": "long Turbo long US100 21000"}
    assert started(agent, {"kind": "signal", "id": "c0ffee", "day": "2026-10-28"}) == {
        "kind": "signal", "id": "c0ffee", "day": "2026-10-28", "label": "short signal of 2026-10-28"}
    assert started(agent, {"kind": "day", "id": "2026-10-28"})["label"] == "the day of 2026-10-28"
    assert started(agent, {"kind": "incident", "id": "position_monitor", "at": "2026-10-28 15:31"}) == {
        "kind": "incident", "id": "position_monitor", "at": "2026-10-28 15:31",
        "label": "position monitor at 2026-10-28 15:31 UTC"}


@pytest.mark.parametrize("subject, said", [
    ({"kind": "position", "id": "9"}, "no position 9"),
    ({"kind": "signal", "id": "c0ffee"}, "named with its day"),
    ({"kind": "signal", "id": "c0ffee", "day": "2026-10-27"}, "No signal c0ffee"),
    ({"kind": "day", "id": "28/10/2026"}, "A day is YYYY-MM-DD"),
    ({"kind": "incident", "id": "trader", "at": "15:31"}, "a moment"),
    ({"kind": "incident", "id": "../etc", "at": "2026-10-28 15:31"}, "a moment"),
    ({"kind": "order", "id": "1"}, "A conversation is about"),
])
def test_what_it_cannot_be_about_is_refused_before_anything_is_kept(agent, subject, said):
    with pytest.raises(InvalidRequest, match=said):
        started(agent, subject)
    assert agent.store.metas() == []


class Session:
    """The tools of a request, answering each call with what it was asked; the admin's reads it is given."""

    def __init__(self, answers=None, offered=("ops_health", "ops_logs", "ops_changes")):
        self.asked = []
        self.answers = answers or {}
        self.offered = offered

    def offers(self, name):
        return name in self.offered

    async def call(self, name, arguments, request, **_):
        self.asked.append((name, arguments))
        return ToolAnswer(self.answers.get(name, json.dumps({"tool": name})))


def shared(subject, answers=None, **given):
    session = Session(answers, **given)
    return asyncio.run(_shared_trading(session, subject, 0)), session.asked


def test_each_subject_is_shared_as_the_tools_read_it():
    text, asked = shared({"kind": "position", "id": "5001", "label": "long Turbo"})
    assert asked == [("data_position", {"position_id": "5001"})] and "the position long Turbo (5001)" in text

    text, asked = shared({"kind": "day", "id": "2026-10-28", "label": "the day of 2026-10-28"})
    assert [name for name, _ in asked] == ["data_trades", "data_signals", "data_orders"]
    assert "Its trades:" in text and "What Saxo refused:" in text

    listed = json.dumps({"items": [{"signal_uuid": "other"}, SIGNAL]})
    text, asked = shared({"kind": "signal", "id": "c0ffee", "day": "2026-10-28", "label": "short signal"},
                         {"data_signals": listed})
    assert asked[1] == ("data_position", {"position_id": "5001"}) and '"signal_uuid": "c0ffee"' in text

    text, asked = shared({"kind": "incident", "id": "trader", "at": "2026-10-28 15:31", "label": "trader at 15:31"})
    before = ("ops_changes", {"until": "2026-10-28 15:31", "hours": 2})
    assert asked == [("ops_health", {}), ("ops_logs", {"service": LOG_OF["trader"], "around": "2026-10-28 15:31",
                                                       "minutes": 15}), before]
    assert "What changed in the 2 hours before it:" in text
    # An entry of the health no log is kept for: its health and what changed before it.
    _, asked = shared({"kind": "incident", "id": "postgres", "at": "2026-10-28 15:31", "label": "postgres"})
    assert asked == [("ops_health", {}), before]
    # Without leave to read the logs, nor the changes: its health alone.
    _, asked = shared({"kind": "incident", "id": "trader", "at": "2026-10-28 15:31", "label": "trader"},
                      offered=("ops_health",))
    assert asked == [("ops_health", {})]



class PaperDays:
    """The backtests' paper days as the Performance page reads them: one day traded, one without an entry."""

    def get(self, run_id):
        return {"id": run_id, "name": "Cross R60"}

    def paper(self, *, with_reports=True):
        return {"rules": {}, "live": {
            "days": [{"day": "2026-10-28", "run_id": "20261028-220000-abcdef", "state": "done", "day_percent": -3.25,
                      "trades": 4, "outside": True},
                     {"day": "2026-10-27", "run_id": None, "state": "no_entry", "day_percent": 0.0, "trades": 0,
                      "outside": None}],
            "reference": {"run_id": "20261020-090000-fedcba", "low": -2.1, "median": 0.3, "high": 2.4}}}


def test_a_day_is_shared_with_how_it_traded_on_paper(agent):
    agent._backtests = PaperDays()

    assert asyncio.run(agent._paper_day("2026-10-28")) == (
        "Run 20261028-220000-abcdef (done): -3.25 % on 4 trades; the live reference's daily range is -2.10 % to "
        "+2.40 %, and the day is outside it. Its trades: backtest_trades of that run.")
    assert asyncio.run(agent._paper_day("2026-10-27")).startswith("No entry signal that day")
    assert asyncio.run(agent._paper_day("2026-10-26")).startswith("Not traded on paper")
    agent._backtests = None
    assert asyncio.run(agent._paper_day("2026-10-28")) == "Not known: backtests are switched off."

    text = asyncio.run(_shared_trading(Session(), {"kind": "day", "id": "2026-10-28", "label": "the day"}, 0,
                                       "No entry signal that day."))
    assert text.endswith("traded on paper under the live settings:\nNo entry signal that day.")
