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
from src.admin.agent.store import stamp
from src.admin.agent_routines import (RoutineConflict, RoutineStore, Routines, checked_fields, checked_schedule,
                                      due_moment, next_turn)
from src.admin.agent_telegram import ROUTINE
from src.admin.errors import InvalidRequest, NotPossible
from tests.test_admin_api import SAME_ORIGIN, config_path, make_ctx  # noqa: F401 (fixtures)
from tests.test_agent_api import signed_in, with_agent
from tests.test_agent_loop import FakeProvider

PARIS = pytz.timezone("Europe/Paris")
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

    async def create_task(self, goal, subject, by, budget, model=None, effort=None):
        if self.refusal:
            raise NotPossible(self.refusal)
        conversation = f"20261031-1000{len(self.tasks):02d}-abcdef"
        self.tasks.append({"goal": goal, "by": by, "budget": budget, "id": conversation})
        self.states[conversation] = "working"
        return {"id": conversation}

    def conversation(self, conversation_id):
        return {"id": conversation_id, "status": "idle", "task": {"state": self.states.get(conversation_id, "done")}}


class Teller:
    def __init__(self):
        self.notes = []

    def add(self, conversation_id, kind, *, request=None, label=None, text=None):
        self.notes.append((conversation_id, kind, label))


def routines_of(tmp_path, agent, teller=None, hours=(9, 22)):
    tmp_path.mkdir(parents=True, exist_ok=True)
    config = tmp_path / "config.json"
    rules = [{**CONFIG["trade"]["rules"][0], "rule_config": {"trading_start_hour": hours[0], "trading_end_hour": hours[1]}}]
    config.write_text(json.dumps({"trade": {**CONFIG["trade"], "rules": rules}}))
    return Routines(agent=agent, store=RoutineStore(str(tmp_path / "agent")), config_path=str(config), teller=teller)


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
        [scout] = on["templates"]
        made = client.post("/api/agent/routines", headers=SAME_ORIGIN, json={
            **{key: scout[key] for key in ("name", "goal", "schedule", "budget", "telegram")},
            "revision": listed["revision"]})
    finally:
        client.__exit__(None, None, None)

    assert off == {"templates": []}
    assert scout["key"] == "luxalgo_scout" and scout["schedule"] == {"every": "week", "weekday": 5, "time": "10:00"}
    # Its budget is held to what a task may be given.
    assert scout["budget"] == {"usd": 1.5, "backtests": 2, "hours": 1.5}
    assert "luxalgo_known" in scout["goal"] and "Never propose to take a rule live" in scout["goal"]
    assert made.status_code == 201 and made.json()["routine"]["name"] == "LuxAlgo scout"
