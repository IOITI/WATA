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
        # The rule whose signal opened a position, by position (none: TradingView's, or a hand-sent one).
        self.rule_of: dict[str, str] = {}
        self.left_out: list[list[str]] = []

    async def closed_results_since(self, moment, leaving_out=()):
        self.left_out.append(list(leaving_out))
        return [row for row in self.closed if row["execution_time_close"] >= moment
                and self.rule_of.get(row["position_id"]) not in leaving_out]

    async def rule_closed_results_since(self, rule_ids, moment):
        return [{**row, "rule_id": self.rule_of[row["position_id"]]} for row in self.closed
                if row["execution_time_close"] >= moment and self.rule_of.get(row["position_id"]) in rule_ids]

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


# ── a live rule's real trades (v0.99.0) ──

WENT_LIVE = datetime(2026, 11, 3, 14, tzinfo=timezone.utc)


def _live_rule(watch, backtests, *, mode: str = "live", reference: bool = True, name: str = "Kernel VWAP"):
    if reference:
        backtests.add(RULE_REFERENCE, date(2026, 10, 24), seed=2)
        backtests.refs[RULE] = RULE_REFERENCE
    watch._rules = FakeRules([{"id": RULE, "name": name, "mode": mode, "run_changed_at": "2026-10-28T10:00:00Z",
                               "mode_changed_at": WENT_LIVE.isoformat()}])


def _rule_closed(queries, results, first, *, rule=RULE, prefix="r"):
    for number, result in enumerate(results):
        position_id = f"{prefix}{number}"
        queries.closed.append({"position_id": position_id, "execution_time_close": first + timedelta(hours=number),
                               "position_total_performance_percent": result})
        if rule is not None:
            queries.rule_of[position_id] = rule


def test_a_live_rule_s_real_trades_are_judged_against_its_own_reference_from_when_it_went_live(watched):
    watch, backtests, queries, _, _, said, published = watched
    _live_rule(watch, backtests)
    # Its positions of the morning it went live (an earlier stint, say) are not its live trades.
    _rule_closed(queries, [30.0, 30.0], WENT_LIVE - timedelta(hours=3), prefix="early")
    _rule_closed(queries, LOSING, WENT_LIVE + timedelta(minutes=30))
    # TradingView's, or a hand-sent signal's: the live trades', never the rule's.
    _rule_closed(queries, [1.0, 2.0], WENT_LIVE + timedelta(minutes=45), rule=None, prefix="tv")

    found = asyncio.run(watch.tick())

    entry = found["rules"][RULE]
    assert entry["main"] == service.REAL and entry["verdict"] == ew.BROKEN
    real = entry["tracks"][service.REAL]
    assert real["n"] == 40 and real["since"] == "2026-11-03" and real["started_at"] == WENT_LIVE.isoformat()
    assert real["test_since"] == WENT_LIVE.isoformat()
    # Its paper track is still judged beside it, from its last change of what it runs.
    assert entry["tracks"][service.PAPER]["n"] == 0 and entry["since"] == "2026-10-28"
    # Each real trade once: the live trades leave the rule's positions out.
    assert queries.left_out[-1] == [RULE]
    assert [step["result"] for step in found["live"]["tracks"][service.REAL]["path"]] == [1.0, 2.0]
    assert found["unwatched"] == []
    assert "📐 WATA Edge Watch, the real trades of rule 'Kernel VWAP': Broken after 40 trade(s)" in " ".join(said)
    assert ("edge_watch_changed", {"owner": RULE, "track": "real", "verdict": "broken"}) in published


def test_the_edge_guard_pauses_on_a_live_rule_s_broken_real_trades_and_names_the_rule(watched, tmp_path):
    watch, backtests, queries, risk, audit, said, _ = watched
    watch._config_path = _config(tmp_path, guard=True)
    _live_rule(watch, backtests)
    _rule_closed(queries, LOSING, WENT_LIVE + timedelta(minutes=30))

    asyncio.run(watch.tick())

    assert risk.paused is not None and risk.asked == [WENT_LIVE]
    assert audit.entries[0][1] == "new entries paused by the edge guard"
    assert audit.entries[0][2].startswith("the real trades of rule 'Kernel VWAP' since 2026-11-03: 40 trade(s)")
    paused = [message for message in said if message.startswith("⏸️ WATA: new entries paused by the edge guard")]
    assert len(paused) == 1 and "the rule's page shows the trades" in paused[0]


def test_the_guard_waits_for_twenty_real_trades_of_the_rule(watched, tmp_path):
    watch, backtests, queries, risk, _, _, _ = watched
    watch._config_path = _config(tmp_path, guard=True)
    _live_rule(watch, backtests)
    _rule_closed(queries, [-6.0] * 12, WENT_LIVE + timedelta(minutes=30))

    real = asyncio.run(watch.tick())["rules"][RULE]["tracks"][service.REAL]

    assert real["n"] == 12 and real["verdict"] == ew.BROKEN and risk.asked == []
    assert ew.guard_refusal(real) == "12 real trade(s) since the test started, of the 20 it waits for"


def test_a_live_rule_s_real_test_starts_again_when_entries_are_resumed(watched):
    watch, backtests, queries, _, _, _, _ = watched
    _live_rule(watch, backtests)
    _rule_closed(queries, LOSING + [1.0, 2.0], WENT_LIVE + timedelta(minutes=30))
    queries.resumed_at = WENT_LIVE + timedelta(minutes=30) + timedelta(hours=39, minutes=30)

    real = asyncio.run(watch.judgements())["rules"][RULE]["tracks"][service.REAL]

    assert real["n"] == 42 and real["tested"] == 2 and real["test_since"] == queries.resumed_at.isoformat()


def test_a_shadow_rule_has_no_real_track_and_a_live_rule_without_a_reference_is_unwatched(watched):
    watch, backtests, queries, _, _, _, _ = watched
    _live_rule(watch, backtests, mode="shadow")
    shadow = asyncio.run(watch.judgements(max_age=0))["rules"][RULE]
    assert service.REAL not in shadow["tracks"] and shadow["main"] == service.PAPER

    del backtests.refs[RULE]
    _live_rule(watch, backtests, reference=False)
    _rule_closed(queries, [1.0, -1.0], WENT_LIVE + timedelta(minutes=30))
    found = asyncio.run(watch.judgements(max_age=0))

    assert found["rules"] == {} and found["unwatched"] == [{"id": RULE, "name": "Kernel VWAP"}]
    # Its positions stay among the live trades'.
    assert queries.left_out[-1] == [] and found["live"]["tracks"][service.REAL]["n"] == 2
    assert watch.listed(found)["unwatched"] == [{"id": RULE, "name": "Kernel VWAP"}]


def test_a_live_rule_s_real_track_is_left_out_while_its_positions_cannot_be_read(watched, monkeypatch):
    watch, backtests, queries, _, _, _, _ = watched
    _live_rule(watch, backtests)

    async def down(rule_ids, moment):
        raise service.DatabaseUnavailable("down")

    monkeypatch.setattr(queries, "rule_closed_results_since", down)
    entry = asyncio.run(watch.judgements())["rules"][RULE]

    assert service.REAL not in entry["tracks"] and entry["main"] == service.PAPER


def test_a_live_rule_whose_reference_is_not_finished_has_its_real_trades_said_not_watched(watched):
    watch, backtests, queries, _, _, _, _ = watched
    _live_rule(watch, backtests)
    # Its reference is still being made: no tested day, no model.
    backtests.runs[RULE_REFERENCE]["days"] = []
    _rule_closed(queries, [1.0, -2.0], WENT_LIVE + timedelta(minutes=30))

    found = asyncio.run(watch.judgements())

    real = found["rules"][RULE]["tracks"][service.REAL]
    assert real["n"] == 2 and real["verdict"] == ew.NO_MODEL and real["started_at"] == WENT_LIVE.isoformat()
    # Never judged twice: still left out of the live trades', and the guard never pauses on it.
    assert queries.left_out[-1] == [RULE] and found["unwatched"] == []
    assert ew.should_pause(real) is False


def test_real_start_is_the_latest_of_the_reference_s_end_its_last_change_and_its_going_live():
    zone = service.TradingClock("Europe/Paris").zone
    rule = {"mode": "live", "run_changed_at": "2026-11-05T09:00:00Z", "mode_changed_at": "2026-11-03T14:00:00Z"}
    assert service.real_start(rule, date(2026, 11, 1), zone) == datetime(2026, 11, 5, 9, tzinfo=timezone.utc)
    # The day after the reference's last day starts at midnight in the trading timezone, not at pytz's LMT.
    assert service.real_start({**rule, "run_changed_at": None, "mode_changed_at": None, "created_at": None},
                              date(2026, 11, 10), zone) == datetime(2026, 11, 9, 23, tzinfo=timezone.utc)
    assert service.went_live_at({"mode": "shadow", "mode_changed_at": "2026-11-03T14:00:00Z"}) is None
    assert service.real_start(rule, None, zone) == datetime(2026, 11, 5, 9, tzinfo=timezone.utc)
    assert service.real_start({"mode": "shadow"}, None, zone) is None
