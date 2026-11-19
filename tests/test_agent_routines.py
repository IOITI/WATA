"""
The AI agent's routines (src/admin/agent_routines.py): research tasks that come back each trading day once its
hours are over, or each week at a day and time; started only while the market is closed, once a turn, never while
their last task still works, held back as a task asked by hand is; kept with a revision, bounded as a task is, told
at their end; "Run now" whatever the hour. The pages read, make, change and remove them through the agent's routes.
"""

import asyncio
import json
from datetime import datetime

import pytest
import pytz

from src.admin.agent.settings import AgentSettings
from src.admin.agent.store import UnknownConversation, stamp
from src.admin.agent_routines import (AUTOPILOT, FALLBACK_GOAL, TEMPLATES, RoutineConflict, RoutineStore, Routines,
                                      checked_fields, checked_schedule, due_moment, next_turn, templates_for)
from src.admin.research_queue import DONE, OPERATOR, TAKEN, WAITING, ResearchQueue
from src.research.experiments import ExperimentJournal
from src.admin.agent_telegram import ROUTINE
from src.admin.errors import InvalidRequest, NotPossible
from tests.test_admin_api import SAME_ORIGIN, config_path, make_ctx  # noqa: F401 (fixtures)
from tests.test_agent_api import signed_in, with_agent
from tests.test_agent_loop import FakeProvider

PARIS = pytz.timezone("Europe/Paris")
TODAY = datetime(2026, 10, 31).date()
CONFIG = {"trade": {"config": {"general": {"timezone": "Europe/Paris"}}, "rules": [
    {"rule_type": "market_hours", "rule_name": "market_hours",
     "rule_config": {"trading_start_hour": 9, "trading_end_hour": 22}}]}}
SETTINGS = AgentSettings(enabled=True)
FIELDS = {"name": "Weekly edge check", "goal": "Run the references again with the week's days.",
          "schedule": {"every": "week", "weekday": 5, "time": "10:00"},
          "budget": {"usd": 3, "backtests": 6, "hours": 2}}


def at(text_time: str) -> float:
    return PARIS.localize(datetime.fromisoformat(text_time)).timestamp()


def test_a_turn_is_the_newest_end_of_trading_hours_or_the_week_s_day_and_time():
    trading_day = checked_schedule({"every": "trading_day"})
    week = checked_schedule({"every": "week", "weekday": 5, "time": "10:00"})

    assert due_moment(trading_day, CONFIG, at("2026-10-30 23:00")) == at("2026-10-30 22:00")
    assert due_moment(trading_day, CONFIG, at("2026-10-31 12:00")) == at("2026-10-30 22:00"), "a Saturday: Friday's"
    assert due_moment(week, CONFIG, at("2026-10-31 10:05")) == at("2026-10-31 10:00")
    assert due_moment(week, CONFIG, at("2026-10-31 09:55")) == at("2026-10-24 10:00")
    # The next turn on Paris's clock: the Saturday after the clocks went back is at 10:00 too.
    assert next_turn(week, CONFIG, at("2026-10-30 22:00")) == stamp(at("2026-10-31 10:00")) == "2026-10-31T09:00:00Z"
    assert next_turn(week, CONFIG, at("2026-10-24 10:00")) == stamp(at("2026-10-31 10:00")), "a turn that came: the next"
    assert next_turn(trading_day, CONFIG, at("2026-10-30 23:00")) == stamp(at("2026-11-02 22:00")), "Monday's close"
    with pytest.raises(InvalidRequest, match="weekday"):
        checked_schedule({"every": "week", "weekday": 7, "time": "10:00"})


def test_a_routine_is_bounded_as_a_task_is():
    assert checked_fields(FIELDS, SETTINGS)["budget"] == {"usd": 3.0, "backtests": 6, "hours": 2.0}
    with pytest.raises(InvalidRequest, match="max_task_usd"):
        checked_fields({**FIELDS, "budget": {"usd": 500, "backtests": 6, "hours": 2}}, SETTINGS)
    with pytest.raises(InvalidRequest, match="a goal"):
        checked_fields({**FIELDS, "goal": " "}, SETTINGS)


class FakeAgent:
    def __init__(self):
        self.settings = SETTINGS
        self.ready = True
        self.tasks = []
        self.refusal = None
        self.states = {}

    async def create_task(self, goal, subject, by, budget, model=None, effort=None, hypothesis=None):
        if self.refusal:
            raise NotPossible(self.refusal)
        conversation = f"20261031-1000{len(self.tasks):02d}-abcdef"
        self.tasks.append({"goal": goal, "by": by, "budget": budget, "id": conversation, "hypothesis": hypothesis})
        self.states[conversation] = "working"
        return {"id": conversation}

    def conversation(self, conversation_id):
        if self.states.get(conversation_id) == "removed":
            raise UnknownConversation(conversation_id)
        return {"id": conversation_id, "status": "idle", "task": {"state": self.states.get(conversation_id, "done")}}


class Teller:
    def __init__(self):
        self.notes = []

    def add(self, conversation_id, kind, *, request=None, label=None, text=None):
        self.notes.append((conversation_id, kind, label))


def routines_of(tmp_path, agent, teller=None, hours=(9, 22), queue=None, journal=None, tired=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    config = tmp_path / "config.json"
    rules = [{**CONFIG["trade"]["rules"][0], "rule_config": {"trading_start_hour": hours[0], "trading_end_hour": hours[1]}}]
    config.write_text(json.dumps({"trade": {**CONFIG["trade"], "rules": rules}}))
    return Routines(agent=agent, store=RoutineStore(str(tmp_path / "agent")), config_path=str(config), teller=teller,
                    queue=queue, journal=journal, tired=tired, clock=lambda: at("2026-10-30 23:00"))


def test_a_routine_starts_once_a_turn_never_over_its_last_task_and_is_told_at_its_end(tmp_path):
    agent, teller = FakeAgent(), Teller()
    routines = routines_of(tmp_path, agent, teller)
    made = routines.store.save(checked_fields(FIELDS, SETTINGS), routines.store.load()["revision"], "10.8.0.2")["routine"]

    first = asyncio.run(routines.catch_up(at("2026-10-31 10:05")))
    asyncio.run(routines.catch_up(at("2026-10-31 10:10")))
    agent.states[first] = "done"
    again = asyncio.run(routines.catch_up(at("2026-10-31 10:15")))
    next_week = asyncio.run(routines.catch_up(at("2026-11-07 10:01")))

    assert first and again is None and next_week
    assert [task["by"] for task in agent.tasks] == [f"routine:{made['id']}"] * 2
    assert agent.tasks[0]["budget"] == {"usd": 3.0, "backtests": 6, "hours": 2.0}
    assert teller.notes[0] == (first, ROUTINE, "Weekly edge check")
    runs = routines.store.load()["routines"][0]["runs"]
    assert [run["conversation"] for run in runs] == [next_week, first]


def test_nothing_starts_while_the_market_is_open_and_what_holds_the_agent_back_waits(tmp_path):
    agent = FakeAgent()
    open_market = routines_of(tmp_path / "open", agent, hours=(0, 24))
    open_market.store.save(checked_fields({**FIELDS, "schedule": {"every": "trading_day"}}, SETTINGS),
                           open_market.store.load()["revision"], None)
    assert asyncio.run(open_market.catch_up(at("2026-10-30 23:00"))) is None
    held = routines_of(tmp_path / "held", agent)
    held.store.save(checked_fields(FIELDS, SETTINGS), held.store.load()["revision"], None)
    agent.refusal = "The AI agent is paused"
    assert asyncio.run(held.catch_up(at("2026-10-31 10:05"))) is None
    assert held.store.load()["routines"][0]["runs"] == []


def test_the_routines_are_kept_with_a_revision_and_bounded_in_number(tmp_path):
    store = RoutineStore(str(tmp_path))
    first = store.save(checked_fields(FIELDS, SETTINGS), store.load()["revision"], None, most=2)
    with pytest.raises(RoutineConflict):
        store.save(checked_fields(FIELDS, SETTINGS), "stale", None)
    store.save(checked_fields(FIELDS, SETTINGS), first["revision"], None, most=2)
    with pytest.raises(NotPossible, match="2 routines at most"):
        store.save(checked_fields(FIELDS, SETTINGS), store.load()["revision"], None, most=2)
    store.remove(first["routine"]["id"], store.load()["revision"])
    assert len(store.load()["routines"]) == 1


def test_the_pages_make_change_run_and_remove_routines(config_path, tmp_path):
    ctx = with_agent(make_ctx(config_path, tmp_path), tmp_path, FakeProvider())
    agent = FakeAgent()
    ctx.routines = Routines(agent=agent, store=RoutineStore(str(tmp_path / "agent")), config_path=config_path)
    client = signed_in(ctx, tmp_path)
    try:
        listed = client.get("/api/agent/routines").json()
        made = client.post("/api/agent/routines", json={**FIELDS, "revision": listed["revision"]}, headers=SAME_ORIGIN)
        changed = client.put(f"/api/agent/routines/{made.json()['routine']['id']}",
                             json={**FIELDS, "name": "Edge check", "revision": made.json()["revision"]}, headers=SAME_ORIGIN)
        stale = client.put(f"/api/agent/routines/{made.json()['routine']['id']}",
                           json={**FIELDS, "revision": made.json()["revision"]}, headers=SAME_ORIGIN)
        ran = client.post(f"/api/agent/routines/{made.json()['routine']['id']}/run", headers=SAME_ORIGIN)
        again = client.post(f"/api/agent/routines/{made.json()['routine']['id']}/run", headers=SAME_ORIGIN)
        now = client.get("/api/agent/routines").json()
        removed = client.delete(f"/api/agent/routines/{made.json()['routine']['id']}",
                                params={"revision": now["revision"]}, headers=SAME_ORIGIN)
    finally:
        client.__exit__(None, None, None)

    assert listed["routines"] == [] and listed["max"] == 10 and made.status_code == 201
    assert changed.json()["routine"]["name"] == "Edge check" and stale.status_code == 409
    assert ran.status_code == 201 and ran.json()["conversation"] == agent.tasks[0]["id"]
    assert again.status_code == 409 and "still works" in again.json()["detail"]
    assert now["routines"][0]["next"] is not None and removed.status_code == 200


def test_the_luxalgo_scout_is_offered_as_a_template_while_luxalgo_is_on(config_path, tmp_path):
    from dataclasses import replace

    ctx = with_agent(make_ctx(config_path, tmp_path), tmp_path, FakeProvider())
    ctx.agent.settings = replace(ctx.agent.settings, max_task_usd=1.5)
    ctx.routines = Routines(agent=FakeAgent(), store=RoutineStore(str(tmp_path / "agent")), config_path=config_path)
    client = signed_in(ctx, tmp_path)
    try:
        off = client.get("/api/agent/routines/templates").json()
        ctx.luxalgo = object()
        on = client.get("/api/agent/routines/templates").json()
        listed = client.get("/api/agent/routines").json()
        scout = next(template for template in on["templates"] if template["key"] == "luxalgo_scout")
        made = client.post("/api/agent/routines", headers=SAME_ORIGIN, json={
            **{key: scout[key] for key in ("name", "goal", "schedule", "budget", "telegram")},
            "revision": listed["revision"]})
    finally:
        client.__exit__(None, None, None)

    assert [template["key"] for template in off["templates"]] == [AUTOPILOT]
    assert scout["key"] == "luxalgo_scout" and scout["schedule"] == {"every": "week", "weekday": 5, "time": "10:00"}
    # Its budget is held to what a task may be given.
    assert scout["budget"] == {"usd": 1.5, "backtests": 2, "hours": 1.5}
    assert "luxalgo_known" in scout["goal"] and "Never propose to take a rule live" in scout["goal"]
    assert made.status_code == 201 and made.json()["routine"]["name"] == "LuxAlgo scout"



# ── the research autopilot ──

GAP = {"title": "Gap fill", "hypothesis": "The open's gap fills before 10:30", "family": "gaps"}


def an_autopilot(routines) -> dict:
    template = templates_for(SETTINGS, luxalgo=False)[0]
    fields = checked_fields(template, SETTINGS)
    return routines.store.save(fields, routines.store.load()["revision"], "10.8.0.2")["routine"]


def test_the_autopilot_is_offered_whatever_else_is_on_and_keeps_its_kind():
    [autopilot] = templates_for(SETTINGS, luxalgo=False)

    assert autopilot["key"] == AUTOPILOT and autopilot["kind"] == AUTOPILOT
    assert autopilot["schedule"] == {"every": "trading_day"} and autopilot["budget"] == TEMPLATES[AUTOPILOT]["budget"]
    assert checked_fields(autopilot, SETTINGS)["kind"] == AUTOPILOT and checked_fields(FIELDS, SETTINGS)["kind"] is None
    with pytest.raises(InvalidRequest, match="autopilot"):
        checked_fields({**FIELDS, "kind": "oracle"}, SETTINGS)


def test_the_autopilot_works_the_queues_next_hypothesis_and_files_what_its_task_leaves(tmp_path):
    agent, queue = FakeAgent(), ResearchQueue(str(tmp_path / "queue"))
    journal = ExperimentJournal(tmp_path / "e.jsonl", tmp_path / "l.json")
    routines = routines_of(tmp_path, agent, queue=queue, journal=journal, tired=lambda: {"kernel"})
    tired = queue.add({**GAP, "title": "Kernel again", "family": "kernel"}, "agent:old", today=TODAY,
                      conversation="old")["item"]
    mine = queue.add({**GAP, "title": "Open block", "family": "opens", "criteria": [
        {"figure": "edge_t", "bound": "at_least", "value": 2.6}]}, OPERATOR, today=TODAY)["item"]
    made = an_autopilot(routines)

    conversation = asyncio.run(routines.catch_up(at("2026-10-30 23:00")))

    [task] = agent.tasks
    assert task["id"] == conversation and task["by"] == f"routine:{made['id']}"
    assert task["hypothesis"]["id"] == mine["id"], "the operator's first, the tired family's last"
    assert task["goal"].startswith("The research queue's next hypothesis: Open block\n")
    assert "The hypothesis (the operator" in task["goal"] and "edge_t at least 2.6" in task["goal"]
    assert task["budget"] == {"usd": 1.5, "backtests": 4, "hours": 3.0}
    assert routines.store.load()["routines"][0]["runs"][0]["hypothesis"] == {"id": mine["id"], "title": "Open block"}
    assert {item["id"]: (item["state"], item["conversation"]) for item in queue.load()["items"]}[mine["id"]] == (
        TAKEN, conversation)

    journal.new("Open block", "h", "opens", made_via=f"agent:{conversation}")
    ended = {"state": "done", "conclusion": {"conclusion": "No edge on 22 days.", "next_hypotheses": [GAP, GAP]}}
    routines.task_ended(conversation, ended)
    routines.task_ended(conversation, ended)

    items = {item["title"]: item for item in queue.load()["items"]}
    assert items["Open block"]["state"] == DONE and items["Open block"]["conclusion"] == "No edge on 22 days."
    assert items["Open block"]["experiment"] == journal.experiments()[-1]["id"]
    assert items["Gap fill"]["by"] == f"agent:{conversation}" and items["Gap fill"]["state"] == WAITING
    assert len(items) == 3 and items["Kernel again"]["id"] == tired["id"]


def test_an_empty_queue_asks_a_look_that_runs_nothing(tmp_path):
    agent = FakeAgent()
    routines = routines_of(tmp_path, agent, queue=ResearchQueue(str(tmp_path / "queue")))
    an_autopilot(routines)

    asyncio.run(routines.catch_up(at("2026-10-30 23:00")))

    [task] = agent.tasks
    assert task["goal"] == FALLBACK_GOAL and task["hypothesis"] is None
    assert task["budget"] == {"usd": 0.5, "backtests": 0, "hours": 1.0}
    assert routines.store.load()["routines"][0]["runs"][0]["hypothesis"] is None


def test_a_task_refused_puts_its_hypothesis_back(tmp_path):
    agent, queue = FakeAgent(), ResearchQueue(str(tmp_path / "queue"))
    routines = routines_of(tmp_path, agent, queue=queue)
    item = queue.add(GAP, OPERATOR, today=TODAY)["item"]
    an_autopilot(routines)
    agent.refusal = "The AI agent is paused"

    assert asyncio.run(routines.catch_up(at("2026-10-30 23:00"))) is None
    assert queue.next()["id"] == item["id"]


def test_what_a_restart_left_taken_is_settled_at_the_next_look(tmp_path):
    agent, queue = FakeAgent(), ResearchQueue(str(tmp_path / "queue"))
    routines = routines_of(tmp_path, agent, queue=queue)
    ended = queue.add({**GAP, "title": "Ended"}, OPERATOR, today=TODAY)["item"]
    gone = queue.add({**GAP, "title": "Gone"}, OPERATOR, today=TODAY)["item"]
    working = queue.add({**GAP, "title": "Working"}, OPERATOR, today=TODAY)["item"]
    for item, conversation, state in ((ended, "c-ended", "ended"), (gone, "c-gone", "removed"),
                                      (working, "c-working", "working")):
        queue.take(item["id"], conversation)
        agent.states[conversation] = state

    asyncio.run(routines.catch_up(at("2026-10-30 23:00")))

    states = {item["title"]: item["state"] for item in queue.load()["items"]}
    assert states == {"Ended": DONE, "Gone": WAITING, "Working": TAKEN}


def test_the_pages_file_reorder_drop_and_remove_hypotheses(config_path, tmp_path):
    ctx = with_agent(make_ctx(config_path, tmp_path), tmp_path, FakeProvider())
    ctx.research_queue = ResearchQueue(str(tmp_path / "agent"))
    ctx.routines = Routines(agent=FakeAgent(), store=RoutineStore(str(tmp_path / "agent")), config_path=config_path)
    client = signed_in(ctx, tmp_path)
    try:
        empty = client.get("/api/agent/queue").json()
        made = client.post("/api/agent/queue", headers=SAME_ORIGIN,
                           json={**GAP, "source": "autopsy:20261114-113341-aa2d8f"})
        second = client.post("/api/agent/queue", json={**GAP, "title": "Open block"}, headers=SAME_ORIGIN)
        refused = client.post("/api/agent/queue", json={**GAP, "motivated_by": ["2099-01-01"]}, headers=SAME_ORIGIN)
        listed = client.get("/api/agent/queue").json()
        moved = client.patch(f"/api/agent/queue/{second.json()['item']['id']}",
                             json={"revision": listed["revision"], "move": 0}, headers=SAME_ORIGIN)
        stale = client.patch(f"/api/agent/queue/{made.json()['item']['id']}",
                             json={"revision": listed["revision"], "state": "dropped"}, headers=SAME_ORIGIN)
        dropped = client.patch(f"/api/agent/queue/{made.json()['item']['id']}",
                               json={"revision": moved.json()["revision"], "state": "dropped"}, headers=SAME_ORIGIN)
        removed = client.delete(f"/api/agent/queue/{made.json()['item']['id']}",
                                params={"revision": dropped.json()["revision"]}, headers=SAME_ORIGIN)
        template = client.get("/api/agent/routines/templates").json()["templates"][0]
        autopilot = client.post("/api/agent/routines", headers=SAME_ORIGIN, json={
            **{key: template[key] for key in ("name", "goal", "schedule", "budget", "telegram", "kind")},
            "revision": client.get("/api/agent/routines").json()["revision"]})
        after = client.get("/api/agent/queue").json()
    finally:
        client.__exit__(None, None, None)

    assert empty["items"] == [] and empty["autopilot"] is None and empty["limits"]["agent_waiting"] == 20
    assert made.status_code == 201 and made.json()["item"]["source"] == "autopsy:20261114-113341-aa2d8f"
    assert refused.status_code == 422 and "still to come" in refused.json()["detail"]
    assert [item["title"] for item in listed["items"]] == ["Gap fill", "Open block"]
    assert moved.status_code == 200 and stale.status_code == 409
    assert dropped.json()["item"]["state"] == "dropped" and removed.status_code == 200
    assert autopilot.json()["routine"]["kind"] == AUTOPILOT
    assert [item["title"] for item in after["items"]] == ["Open block"]
    assert after["autopilot"] == {"id": autopilot.json()["routine"]["id"], "name": "Research autopilot", "enabled": True}
