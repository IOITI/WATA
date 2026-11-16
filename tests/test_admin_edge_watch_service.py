"""Edge Watch as the admin runs it (src/admin/edge_watch_service.py): tracks, verdicts said once, the edge guard."""

import asyncio
import functools
import json
import random
from datetime import date, datetime, timedelta, timezone

import pytest

from src.admin import edge_watch as ew
from src.admin import edge_watch_service as service
from src.admin.backtests import LIVE_PAPER, NO_ENTRY

REFERENCE = "20261101-120000-aaaaaa"
RULE_REFERENCE = "20261101-120000-bbbbbb"
RULE = "0123456789ab"
LOSING = [-1.5, -4.0, 2.0, -3.0, -6.0] * 8


def _reference_files(seed: int, last: date, days: int = 30) -> tuple[list[dict], list[dict]]:
    draw = random.Random(seed)
    listed, trades = [], []
    day = last - timedelta(days=days - 1)
    for _ in range(days):
        listed.append({"day": day.isoformat(), "status": "tested"})
        for number in range(draw.choice([0, 1, 2, 2, 3])):
            trades.append({"day": day.isoformat(), "performance_percent": round(draw.gauss(1.5, 3.0), 3),
                           "exit": {"filled_at": f"{day.isoformat()}T1{number}:00:00Z"}})
        day += timedelta(days=1)
    return listed, trades


class FakeBacktests:
    def __init__(self):
        self.runs = {}
        self.refs = {}
        self.index = {}
        self.read = []

    def add(self, run_id: str, last: date, seed: int, settings: dict | None = None):
        days, trades = _reference_files(seed, last)
        self.runs[run_id] = {"days": days, "trades": trades, "settings": settings or {"stoploss_percent": -7.0},
                             "baseline": {"totals": [((0.985 ** 60) - 1) * 100] * 20, "trades": [60] * 20}}

    def add_paper(self, owner: str, day: str, results: list[float], *, stale: bool = False):
        run_id = f"20261110-0000{len(self.runs):02d}-cccccc"
        self.runs[run_id] = {"days": [], "stale": stale, "trades": [
            {"day": day, "performance_percent": result, "exit": {"filled_at": f"{day}T1{number}:00:00Z"}}
            for number, result in enumerate(results)
        ]}
        self.index.setdefault(owner, {})[day] = run_id

    def references(self):
        return dict(self.refs)

    def reference_of(self, run_id):
        run = self.runs.get(run_id)
        if run is None:
            return None
        tested = sorted(day["day"] for day in run["days"])
        return {"run_id": run_id, "name": f"Run {run_id[-6:]}", "done": True, "days": len(tested),
                "first_day": tested[0] if tested else None, "last_day": tested[-1] if tested else None,
                "stale": run.get("stale", False), "made_with": "0.85.0"}

    def days(self, run_id):
        self.read.append(run_id)
        return self.runs[run_id]["days"]

    def trades(self, run_id):
        return self.runs[run_id]["trades"]

    def baseline(self, run_id):
        return self.runs[run_id].get("baseline")

    def settings_of(self, run_id):
        return self.runs[run_id].get("settings", {})

    def paper_index(self):
        return self.index

    def state(self, run_id):
        return {"id": run_id, "state": "done"}


class FakeQueries:
    def __init__(self):
        self.closed: list[dict] = []
        self.resumed_at = None

    async def closed_results_since(self, moment):
        return [row for row in self.closed if row["execution_time_close"] >= moment]

    async def entries_resumed_at(self):
        return self.resumed_at


class FakeRules:
    def __init__(self, rules):
        self.rules = rules

    def load(self):
        return {"rules": self.rules, "revision": "r"}


class FakeRiskState:
    def __init__(self):
        self.paused = None
        self.asked = []

    async def pause_entries_by_edge_guard(self, ts, judged_after):
        self.asked.append(judged_after)
        if self.paused is not None:
            return False
        self.paused = ts
        return True


class FakeAudit:
    def __init__(self):
        self.entries = []

    def append(self, ip, action, detail=""):
        self.entries.append((ip, action, detail))


def _config(tmp_path, *, guard: bool = False) -> str:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"trade": {
        "rules": [{"rule_type": "risk_management", "rule_config": {"pause_when_edge_broken": guard}}],
        "config": {"general": {"timezone": "Europe/Paris"}},
    }}))
    return str(path)


@pytest.fixture(autouse=True)
def quick_models(monkeypatch):
    monkeypatch.setattr(ew, "build_model", functools.partial(ew.build_model, paths=400, h1_paths=60))


@pytest.fixture
def watched(tmp_path, monkeypatch):
    backtests, queries, risk, audit = FakeBacktests(), FakeQueries(), FakeRiskState(), FakeAudit()
    backtests.add(REFERENCE, date(2026, 10, 31), seed=1)
    backtests.refs[LIVE_PAPER] = REFERENCE
    said, published = [], []

    async def notify(message):
        said.append(message)

    # The live settings are the reference's, unless a test says otherwise.
    monkeypatch.setattr(service.EdgeWatch, "_live_settings", lambda self, config: {"stoploss_percent": -7.0})
    watch = service.EdgeWatch(
        root=str(tmp_path), backtests=backtests, queries=queries, rules=FakeRules([]), risk_state=risk,
        config_path=_config(tmp_path), notify=notify, publish=lambda kind, **data: published.append((kind, data)),
        audit_log=audit, clock=lambda: datetime(2026, 11, 16, 12, tzinfo=timezone.utc).timestamp(),
    )
    return watch, backtests, queries, risk, audit, said, published


def _closed(queries, results, first=datetime(2026, 11, 2, 9, tzinfo=timezone.utc)):
    for number, result in enumerate(results):
        queries.closed.append({"position_id": f"p{number}", "execution_time_close": first + timedelta(hours=number),
                               "position_total_performance_percent": result})


def test_the_live_trades_after_the_reference_are_judged_and_a_broken_verdict_is_said_once(watched):
    watch, _, queries, risk, _, said, published = watched
    # A trade of the reference's own days is not judged: only those after its last day.
    queries.closed.append({"position_id": "old", "execution_time_close": datetime(2026, 10, 30, 9, tzinfo=timezone.utc),
                           "position_total_performance_percent": 50.0})
    _closed(queries, LOSING)

    found = asyncio.run(watch.tick())
    asyncio.run(watch.tick())

    live = found["live"]
    assert live["since"] == "2026-11-01" and live["main"] == service.REAL
    real = live["tracks"][service.REAL]
    assert real["verdict"] == ew.BROKEN and real["n"] == 40 and len(real["path"]) == 40
    assert real["path"][-1]["cumulative"] == real["cumulative"]
    assert len(said) == 1 and said[0].startswith("📐 WATA Edge Watch, the live trades: Broken after 40 trade(s)")
    assert ("edge_watch_changed", {"owner": "live", "track": "real", "verdict": "broken"}) in published
    # The guard is off: nothing paused.
    assert risk.asked == []


def test_the_edge_guard_pauses_entries_once_and_says_it(watched, tmp_path):
    watch, _, queries, risk, audit, said, published = watched
    watch._config_path = _config(tmp_path, guard=True)
    _closed(queries, LOSING)

    asyncio.run(watch.tick())
    asyncio.run(watch.tick())

    assert risk.paused is not None and len(risk.asked) == 2
    assert [entry[1] for entry in audit.entries] == ["new entries paused by the edge guard"]
    assert sum(1 for message in said if message.startswith("⏸️ WATA: new entries paused by the edge guard")) == 1
    assert ("entries_paused", {}) in published


def test_the_edge_guard_does_not_act_while_the_live_settings_are_not_the_reference_s(watched, tmp_path, monkeypatch):
    watch, _, queries, risk, _, _, _ = watched
    watch._config_path = _config(tmp_path, guard=True)
    monkeypatch.setattr(service.EdgeWatch, "_live_settings", lambda self, config: {"stoploss_percent": -5.0})
    _closed(queries, LOSING)

    found = asyncio.run(watch.tick())

    real = found["live"]["tracks"][service.REAL]
    assert real["verdict"] == ew.BROKEN and real["settings_differ"] == ["stoploss_percent"]
    assert risk.asked == [] and "live settings" in ew.guard_refusal(real)


def test_the_live_test_starts_again_when_the_operator_resumes_entries(watched):
    watch, _, queries, _, _, _, _ = watched
    _closed(queries, LOSING + [1.0, 2.0, -1.0])
    # Resumed after the 40 losing trades: the test reads the three after.
    queries.resumed_at = datetime(2026, 11, 2, 9, tzinfo=timezone.utc) + timedelta(hours=39, minutes=30)

    real = asyncio.run(watch.judgements())["live"]["tracks"][service.REAL]

    assert real["n"] == 43 and real["tested"] == 3 and real["alarm_at"] is None
    assert real["test_since"] == queries.resumed_at.isoformat()


def test_a_rule_is_judged_on_its_paper_trades_since_its_reference_and_its_last_change(watched):
    watch, backtests, _, _, _, _, _ = watched
    backtests.add(RULE_REFERENCE, date(2026, 10, 24), seed=2)
    backtests.refs[RULE] = RULE_REFERENCE
    watch._rules = FakeRules([{"id": RULE, "name": "EMA cross", "mode": "shadow",
                               "run_changed_at": "2026-10-28T10:00:00Z", "mode_changed_at": "2026-10-20T10:00:00Z"}])
    backtests.add_paper(RULE, "2026-10-27", [9.0, 9.0])
    backtests.add_paper(RULE, "2026-10-29", [1.0, -2.0], stale=True)
    backtests.index[RULE]["2026-10-30"] = NO_ENTRY
    backtests.add_paper(RULE, "2026-11-03", [2.0])

    entry = asyncio.run(watch.judgements())["rules"][RULE]

    assert entry["since"] == "2026-10-28" and entry["name"] == "EMA cross" and entry["main"] == service.PAPER
    paper = entry["tracks"][service.PAPER]
    assert [step["result"] for step in paper["path"]] == [1.0, -2.0, 2.0]
    assert paper["verdict"] == ew.TOO_EARLY and paper["stale_days"] == ["2026-10-29"]
    assert service.REAL not in entry["tracks"]


def test_a_reference_whose_rule_is_gone_is_passed_over_and_its_model_let_go(watched, tmp_path):
    watch, backtests, _, _, _, _, _ = watched
    backtests.add(RULE_REFERENCE, date(2026, 10, 24), seed=2)
    backtests.refs[RULE] = RULE_REFERENCE

    found = asyncio.run(watch.judgements())

    assert found["rules"] == {}
    models = tmp_path / "edge_watch" / "models"
    assert sorted(path.name for path in models.iterdir()) == [f"{REFERENCE}-v{ew.VERSION}.json"]


def test_a_model_is_made_once_and_read_back_by_a_new_admin(watched, tmp_path):
    watch, backtests, queries, risk, audit, _, _ = watched
    asyncio.run(watch.judgements())
    assert backtests.read == [REFERENCE]

    again = service.EdgeWatch(root=str(tmp_path), backtests=backtests, queries=queries, rules=FakeRules([]),
                              risk_state=risk, config_path=watch._config_path, audit_log=audit)
    asyncio.run(again.judgements())

    assert backtests.read == [REFERENCE]


def test_the_list_leaves_the_cones_and_paths_out(watched):
    watch, _, queries, _, _, _, _ = watched
    _closed(queries, LOSING)

    listed = watch.listed(asyncio.run(watch.judgements()))

    live = listed["live"]
    assert "cone" not in live and "path" not in live["tracks"][service.REAL]
    assert live["tracks"][service.REAL]["verdict"] == ew.BROKEN
    assert listed["guard"] == {"on": False, "least_trades": ew.GUARD_LEAST_TRADES}


def test_judgements_are_kept_a_while_and_health_reads_them_without_judging(watched):
    watch, _, _, _, _, _, _ = watched
    assert watch.state() == {"judgements": None, "age": None}
    first = asyncio.run(watch.judgements())

    assert asyncio.run(watch.judgements()) is first
    assert watch.state()["judgements"] is first and watch.state()["age"] == 0
    assert service.guard_silent({"age": None}, True) and not service.guard_silent({"age": 10.0}, True)
    assert not service.guard_silent({"age": None}, False)
