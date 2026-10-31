"""
An alert triaged by the AI agent as it fires (src/admin/agent_triage.py): which
events are worth a triage and what each is about; a service only in trading
hours, never during a deploy or a restart nor in the two minutes after; the
account guard's pause alone; one alert once an hour (counted meanwhile, and said
in its next triage), so many a day, one at a time; told on Telegram; what holds
the agent back neither triages nor counts. It listens to the admin's events
while it runs, and lets them go when it stops.
"""

import asyncio
import json
from datetime import datetime

import pytest
import pytz

from src.admin.agent.settings import AgentSettings
from src.admin.agent_telegram import TRIAGE
from src.admin.agent_triage import AlertTriage, alert_of
from src.admin.errors import NotPossible
from src.admin.events import EventHub

PARIS = pytz.timezone("Europe/Paris")
CONFIG = {"trade": {"config": {"general": {"timezone": "Europe/Paris"}}, "rules": [
    {"rule_type": "market_hours", "rule_name": "market_hours",
     "rule_config": {"trading_start_hour": 9, "trading_end_hour": 22}}]}}
REFUSED = {"type": "order_refused", "position_id": "5001", "action": "long", "instrument_name": "Turbo long",
           "purpose": "close", "count": 4, "total": 9, "outcome": "refused", "http_status": 400,
           "error_code": "TooFarFromMarket", "error_message": "Price too far", "last_at": "2026-10-30T14:00:00Z"}
DOWN = {"type": "service_status", "id": "position_monitor", "name": "Position monitor", "status": "down",
        "previous": "ok", "detail": "No beat for 95 s"}


def at(text_time: str) -> float:
    return PARIS.localize(datetime.fromisoformat(text_time)).timestamp()


def test_what_is_worth_a_triage_and_what_it_is_about():
    refused = alert_of(REFUSED, "2026-10-30 14:00", live_rules=False)
    down = alert_of(DOWN, "2026-10-30 14:00", live_rules=False)

    assert (refused.key, refused.subject) == ("refused:5001", {"kind": "position", "id": "5001"})
    assert refused.facts["error code"] == "TooFarFromMarket" and not refused.service
    assert (down.key, down.subject, down.service) == (
        "service:position_monitor", {"kind": "incident", "id": "position_monitor", "at": "2026-10-30 14:00"}, True)
    # The TA engine and the feed only while a rule is live; a warning of a trading service is not an alert.
    engine = {**DOWN, "id": "ta_engine", "name": "TA engine"}
    assert alert_of(engine, "x", live_rules=False) is None and alert_of(engine, "x", live_rules=True) is not None
    assert alert_of({**DOWN, "status": "warning"}, "x", live_rules=True) is None
    assert alert_of({"type": "service_status", "id": "live_drift", "status": "warning"}, "x", False).key == "service:live_drift"
    assert alert_of({"type": "saxo_authorisation_required"}, "x", False).subject["id"] == "saxo"
    assert alert_of({"type": "entries_paused"}, "x", False).guard
    assert alert_of({"type": "position_opened", "position_id": "1"}, "x", False) is None


class FakeAgent:
    def __init__(self, **settings):
        self.settings = AgentSettings(**{"enabled": True, "triage_enabled": True, **settings})
        self.ready = True
        self.created = []
        self.statuses = {}
        self.refusal = None

    async def create(self, subject, prompt, by, model=None, effort=None, *, task=None, debrief=None, job=None):
        if self.refusal:
            raise NotPossible(self.refusal)
        conversation = f"20261030-1400{len(self.created):02d}-abcdef"
        self.created.append({"subject": subject, "prompt": prompt, "by": by, "job": job, "id": conversation})
        self.statuses[conversation] = "running"
        return {"id": conversation}

    def conversation(self, conversation_id):
        return {"id": conversation_id, "status": self.statuses.get(conversation_id, "idle")}


class Teller:
    def __init__(self):
        self.notes = []

    def add(self, conversation_id, kind, *, request=None, label=None, text=None):
        self.notes.append((conversation_id, kind, label))


@pytest.fixture
def config_path(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(CONFIG))
    return str(path)


def triage_of(agent, config_path, tmp_path, teller=None, busy=lambda: False, guard=False):
    async def paused_by_guard():
        return guard

    return AlertTriage(agent=agent, events=EventHub(), config_path=config_path, path=str(tmp_path / "triage.json"),
                       teller=teller, paused_by_guard=paused_by_guard, busy=busy)


def look(triage, event, moment):
    return asyncio.run(triage.look(event, now=at(moment)))


def test_a_sale_refused_is_triaged_at_once_and_told(config_path, tmp_path):
    agent, teller = FakeAgent(), Teller()
    triage = triage_of(agent, config_path, tmp_path, teller)

    started = look(triage, REFUSED, "2026-10-30 23:30")

    assert started == agent.created[0]["id"]
    asked = agent.created[0]
    assert asked["subject"] == {"kind": "position", "id": "5001"} and asked["by"] == "triage"
    assert asked["job"] == {"kind": "triage", "alert": "refused:5001", "max_usd": 0.5, "backtests": 0,
                            "title": "Triage: Saxo refused the sale of position 5001 (Turbo long)"}
    assert "- error code: TooFarFromMarket" in asked["prompt"] and "Start no backtest" in asked["prompt"]
    assert teller.notes == [(started, TRIAGE, "Saxo refused the sale of position 5001 (Turbo long)")]


def test_a_service_is_triaged_in_trading_hours_alone_and_never_around_a_deploy(config_path, tmp_path):
    agent, busy = FakeAgent(), [False]
    triage = triage_of(agent, config_path, tmp_path, busy=lambda: busy[0])

    assert look(triage, DOWN, "2026-10-30 23:30") is None, "outside trading hours"
    busy[0] = True
    assert look(triage, DOWN, "2026-10-30 14:00") is None, "a deploy applies"
    busy[0] = False
    assert look(triage, DOWN, "2026-10-30 14:01") is None, "two minutes after it"
    assert look(triage, DOWN, "2026-10-30 14:03") is not None


def test_the_pause_is_triaged_only_when_it_is_the_account_guard_s(config_path, tmp_path):
    operator, guard = FakeAgent(), FakeAgent()
    assert look(triage_of(operator, config_path, tmp_path, guard=False), {"type": "entries_paused"}, "2026-10-30 14:00") is None
    assert look(triage_of(guard, config_path, tmp_path / "g", guard=True), {"type": "entries_paused"},
                "2026-10-30 14:00") is not None


def test_once_an_hour_an_alert_is_counted_and_said_in_its_next_triage(config_path, tmp_path):
    agent = FakeAgent()
    triage = triage_of(agent, config_path, tmp_path)

    first = look(triage, REFUSED, "2026-10-30 14:00")
    agent.statuses[first] = "idle"
    assert look(triage, REFUSED, "2026-10-30 14:20") is None
    assert look(triage, REFUSED, "2026-10-30 14:40") is None
    later = look(triage, REFUSED, "2026-10-30 15:05")

    assert len(agent.created) == 2 and later == agent.created[1]["id"]
    assert "It fired 2 more times since its last triage." in agent.created[1]["prompt"]


def test_one_at_a_time_the_others_wait_and_a_day_has_its_most(config_path, tmp_path):
    agent = FakeAgent(triage_max_per_day=2)
    triage = triage_of(agent, config_path, tmp_path)

    first = look(triage, REFUSED, "2026-10-30 14:00")
    assert look(triage, DOWN, "2026-10-30 14:00") is None, "waits for the first to end"
    agent.statuses[first] = "idle"
    second = look(triage, None, "2026-10-30 14:01")
    agent.statuses[second] = "idle"
    third = look(triage, {**DOWN, "id": "trader", "name": "Trader"}, "2026-10-30 14:02")

    assert [asked["job"]["alert"] for asked in agent.created] == ["refused:5001", "service:position_monitor"]
    assert third is None, "two a day at most"


def test_what_holds_the_agent_back_neither_triages_nor_counts(config_path, tmp_path):
    agent = FakeAgent()
    agent.refusal = "The AI agent is paused"
    triage = triage_of(agent, config_path, tmp_path)

    assert look(triage, REFUSED, "2026-10-30 14:00") is None
    agent.refusal = None
    assert look(triage, REFUSED, "2026-10-30 14:05") is not None
    assert look(triage_of(FakeAgent(triage_enabled=False), config_path, tmp_path / "off"), REFUSED,
                "2026-10-30 14:00") is None


def test_it_listens_to_the_admin_s_events_while_it_runs(config_path, tmp_path):
    agent = FakeAgent()
    triage = triage_of(agent, config_path, tmp_path)
    hub = triage._events

    async def scenario():
        task = asyncio.create_task(triage.keep())
        await asyncio.sleep(0.05)
        listening = hub.has_subscribers
        hub.publish(REFUSED["type"], **{key: value for key, value in REFUSED.items() if key != "type"})
        for _ in range(100):
            if agent.created:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return listening

    assert asyncio.run(scenario()) is True
    assert agent.created and not hub.has_subscribers
