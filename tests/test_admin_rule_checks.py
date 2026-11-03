"""
The rule checks of the Technical Analysis pages (src/admin/rule_checks.py): the
reports kept by what runs, what the source says made by the admin's checker,
the run tests asked one at a time behind the studies, and what going live and
the lists read of them. The checker and the studies are fakes here: the real
ones are tests/test_research_study.py's.
"""

import asyncio
import json
import os

import pytest

from src.admin import rule_checks as module
from src.admin.errors import NotPossible
from src.admin.luxalgo_compat import CompatChecker, CompatStore
from src.admin.rule_checks import RuleCheckStore, RuleChecks, readiness_check, settings_of, summary
from src.admin.ta_rules import RuleStore
from src.admin.ta_scripts import ScriptStore
from src.research import rule_check as checks
from src.admin.luxalgo import LuxAlgo
from tests.test_admin_luxalgo import FakeLuxAlgo

CROSS = '//@version=6\nindicator("Cross")\nalertcondition(ta.crossover(close, ta.ema(close, 9)), "Long", "Long")\n'
GATE = '//@version=6\nlibrary("Gate")\nexport gate(bool cond) => cond and barstate.isrealtime\n'
GATED = '//@version=6\nindicator("Gated")\nimport user/Gate/1 as g\nalertcondition(g.gate(close > open), "Up", "Up")\n'


def check_of(verdict="ready", findings=None, version=1) -> dict:
    return {"check": {"version": version, "kind": "indicator", "alerts": [{"title": "Long", "message": "Long", "line": 3}],
                      "alertCalls": [], "candidates": [], "plots": [], "wholeBecause": None,
                      "findings": findings or [], "verdict": verdict},
            "tradingview_rules": []}


class FakeChecker:
    """The checker's client: each source answered as ``answers`` says, every call noted."""

    def __init__(self, answers=None, version=1):
        self.asked = []
        self.answers = answers or {}
        self.versions = {"rule_check": version}

    async def rule_check(self, source, libraries):
        self.asked.append((source, [library["name"] for library in libraries]))
        return self.answers.get(source) or check_of()

    async def stop(self):
        pass


class FakeStudies:
    def __init__(self, answer=None):
        self.asked = []
        self.busy = False
        self.answer = answer

    async def study(self, mode, request):
        self.asked.append((mode, request))
        return self.answer or {"ok": True, "static": check_of(), "tested": {
            "settings": {"asset": request.get("asset"), "side": request.get("side"), "bars": {"type": "time", "timeframe": request.get("bars")},
                         "inputs": request.get("inputs") or {}, "history_days": 1, "rule": request.get("rule")},
            "days": [{"bars": 840, "milliseconds": 20}], "spoken": "13 and 14 Oct",
            "alerts": [{"title": "Long", "count": 3, "repeats": 0, "longest": 1, "share": 0.002}],
            "checkpoints": {"run": 4, "at_the_bar": [], "earlier": [], "plots_differ": []}}}


@pytest.fixture
def scripts(tmp_path):
    store = ScriptStore(str(tmp_path / "ta"))
    store.create("Cross", CROSS)
    store.create("Gate", GATE)
    store.create("Gated", GATED)
    return store


def by_name(scripts, name):
    return next(meta for meta in scripts.scripts() if meta["name"] == name)


def service(scripts, checker=None, studies=None, **more) -> RuleChecks:
    return RuleChecks(RuleCheckStore(scripts.root), scripts, studies, checker=checker or FakeChecker(),
                      memory=lambda: None, **more)


def test_a_report_is_kept_by_its_digest_alone_and_a_luxalgo_one_by_its_slug_too(tmp_path):
    store = RuleCheckStore(str(tmp_path))
    digest = "a" * 64

    store.put({"digest": digest, "about": {"luxalgo": "tri-star"}, "static": check_of(), "tests": []})

    assert store.get(digest)["about"] == {"luxalgo": "tri-star"}
    assert store.of_luxalgo("tri-star")["digest"] == digest and store.luxalgo_slugs() == ["tri-star"]
    assert store.get("b" * 64) is None
    # A digest is 64 lower-case hex before any path is built: another reads as nothing, and is never written.
    for wrong in ("../x", "A" * 64, "a" * 63):
        assert store.get(wrong) is None
        with pytest.raises(ValueError):
            store.put({"digest": wrong, "about": {}, "static": {}, "tests": []})


def test_the_newest_reports_are_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "KEPT", 3)
    store = RuleCheckStore(str(tmp_path))
    for index in range(5):
        store.put({"digest": f"{index:064x}", "about": {}, "static": {}, "tests": []})
        os.utime(store._path(f"{index:064x}"), (index, index))

    assert [store.get(f"{index:064x}") is not None for index in range(5)] == [False, False, True, True, True]


def test_what_a_version_says_is_made_once_with_its_libraries_and_again_for_a_newer_check(scripts):
    checker = FakeChecker()
    checks_ = service(scripts, checker)
    gated = by_name(scripts, "Gated")

    first = asyncio.run(checks_.of_version(gated["id"]))
    again = asyncio.run(checks_.of_version(gated["id"]))
    checker.versions = {"rule_check": 2}
    newer = asyncio.run(checks_.of_version(gated["id"]))

    assert checker.asked == [(GATED, ["Gate"])] * 2
    assert first["verdict"] == again["verdict"] == newer["verdict"] == "ready"
    assert first["about"] == {"script": {"id": gated["id"], "version": 1}}
    # A library is checked through the scripts that import it.
    assert asyncio.run(checks_.of_version(by_name(scripts, "Gate")["id"])) is None


def test_a_script_whose_library_is_missing_is_checked_on_its_own(tmp_path):
    scripts = ScriptStore(str(tmp_path / "ta"))
    lonely = scripts.create("Lonely", GATED)
    checker = FakeChecker()

    asyncio.run(service(scripts, checker).of_version(lonely["id"]))

    assert checker.asked == [(GATED, [])]


def test_a_list_reads_what_is_kept_and_asks_the_rest_in_the_background(scripts):
    checker = FakeChecker({CROSS: check_of("needs_alerts", [{"id": "no-alerts", "level": "blocks", "message": "Nothing…"}])})
    checks_ = service(scripts, checker)
    cross = by_name(scripts, "Cross")

    listed = asyncio.run(checks_.summaries())
    assert listed["scripts"] == {cross["id"]: None, by_name(scripts, "Gated")["id"]: None}
    assert listed["state"]["checking"] == 2 and checker.asked == []

    asyncio.run(checks_.of_version(cross["id"]))
    again = asyncio.run(checks_.summaries())["scripts"][cross["id"]]
    assert again["verdict"] == "needs_alerts" and again["worst"]["id"] == "no-alerts" and again["version"] == 1


def run_the_queue(checks_, studies, until):
    async def run():
        task = asyncio.create_task(checks_.run())
        for _ in range(300):
            if until():
                break
            await asyncio.sleep(0.01)
        task.cancel()

    asyncio.run(run())


def test_a_run_test_waits_while_a_study_runs_then_is_kept_with_its_settings(scripts, monkeypatch):
    monkeypatch.setattr(module, "IDLE_SECONDS", 0.01)
    studies = FakeStudies()
    checks_ = service(scripts, studies=studies, lockbox=lambda: None)
    cross = by_name(scripts, "Cross")

    studies.busy = True
    asked = checks_.ask({"script": {"id": cross["id"], "version": 1}}, settings_of("5"))
    again = checks_.ask({"script": {"id": cross["id"], "version": 1}}, settings_of("5"))
    assert len(asked["queued"]) == len(again["queued"]) == 1

    async def wait_then_free():
        task = asyncio.create_task(checks_.run())
        await asyncio.sleep(0.1)
        waited = list(studies.asked)
        studies.busy = False
        for _ in range(300):
            if studies.asked and not checks_.state()["queued"] and checks_.state()["testing"] is None:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        return waited

    assert asyncio.run(wait_then_free()) == []
    mode, request = studies.asked[0]
    assert mode == "rule" and request["script"] == {"id": cross["id"], "version": 1} and request["bars"] == "5"
    report = asyncio.run(checks_.of_version(cross["id"]))
    assert report["tested"]["settings"]["bars"] == {"type": "time", "timeframe": "5"}
    assert [finding["id"] for finding in report["findings"]] == ["same-live-and-backtest"]


def test_a_run_test_asked_without_its_days_leaves_out_the_lock_box(scripts):
    from datetime import date

    checks_ = service(scripts, studies=FakeStudies(), lockbox=lambda: {"from": date(2026, 10, 20), "to": None})
    job = {"about": {"script": {"id": "x", "version": 1}}, "settings": settings_of("1"), "source": None}

    assert checks_._request(job)["avoid"] == [["2026-10-20", None]]


def test_the_run_tests_are_refused_without_studies_and_capped_in_waiting(scripts, monkeypatch):
    with pytest.raises(NotPossible, match="Pine study"):
        service(scripts).ask({"script": {"id": "x", "version": 1}}, settings_of("1"))

    monkeypatch.setattr(module, "QUEUED_MOST", 1)
    checks_ = service(scripts, studies=FakeStudies())
    checks_.ask({"script": {"id": "x", "version": 1}}, settings_of("1"))
    with pytest.raises(NotPossible, match="wait already"):
        checks_.ask({"script": {"id": "x", "version": 1}}, settings_of("5"))


def rule_of(scripts, **more) -> dict:
    store = RuleStore(scripts)
    cross = by_name(scripts, "Cross")
    return store.create({
        "name": "Cross rule", "asset": {"provider": "capital", "key": "US100"}, "side": "bid",
        "bars": {"type": "time", "timeframe": "1"}, "script": {"id": cross["id"], "version": 1}, "inputs": [],
        "alerts": [{"title": "Long", "action": "long"}], "alert_calls": "ignore", "indice": "us100", **more,
    }, store.load()["revision"])["rule"]


def test_a_test_counts_for_a_rule_only_when_made_as_the_rule_runs(scripts):
    studies = FakeStudies()
    checks_ = service(scripts, studies=studies)
    rule = rule_of(scripts)

    # Tested from the script's page, on minutes: not as the rule runs it.
    found = checks_._version(rule["script"]["id"], 1)
    asyncio.run(checks_._keep(found, asyncio.run(studies.study("rule", {"asset": "capital/US100", "side": "bid", "bars": "1"}))))
    before = asyncio.run(checks_.summary_of_rule(rule))
    asyncio.run(checks_._keep(checks_._rule(rule), asyncio.run(studies.study("rule", {
        "asset": "capital/US100", "side": "bid", "bars": "1", "rule": rule["id"]}))))
    after = asyncio.run(checks_.summary_of_rule(rule))

    assert before["as_rule"] is False and before["tested_at"] is None
    assert after["as_rule"] is True and after["tested_at"] is not None and after["tested_bars"] == "1"


def test_going_live_reads_nothing_where_the_check_cannot_be_made():
    class Refusing:
        async def summary_of_rule(self, rule):
            raise NotPossible("no checker")

    assert asyncio.run(readiness_check(Refusing(), {"id": "r1"})) is None
    assert asyncio.run(readiness_check(None, {"id": "r1"})) is None


def test_a_summary_says_the_worst_finding_first():
    entry = {"digest": "a" * 64, "static": check_of("to_change", [
        {"id": "varip", "level": "note", "message": "varip"}, {"id": "mintick", "level": "change", "message": "mintick"}]), "tests": []}

    said = summary(entry)

    assert said["worst"] == {"id": "mintick", "level": "change", "message": "mintick"}
    assert said["counts"] == {"note": 1, "change": 1} and said["verdict"] == "to_change"


def test_what_luxalgos_check_read_of_a_source_as_a_rule_is_kept_by_its_slug(tmp_path, monkeypatch):
    scripts = ScriptStore(str(tmp_path / "ta"))
    checker = FakeChecker()
    rule_checks = service(scripts, checker)
    static = check_of("needs_alerts", [{"id": "no-alerts", "level": "blocks", "message": "Nothing…"}])

    class Studies:
        busy = False

        async def study(self, mode, request):
            return {"ok": True, "plots": [], "inputs": [], "alerts_on_made_up_bars": {}, "tradingview_rules": [],
                    "rule_check": static}

    compat = CompatChecker(CompatStore(scripts.root), LuxAlgo(server=FakeLuxAlgo().server), Studies(), rule_checks=rule_checks)

    async def check_and_settle():
        entry = await compat.check("made-up")
        await asyncio.sleep(0.05)
        return entry

    entry = asyncio.run(check_and_settle())

    # What the check study read is kept: the checker is not asked again.
    kept = rule_checks.luxalgo_summary("made-up")
    assert kept["verdict"] == "needs_alerts" and checker.asked == []
    index = json.loads(open(os.path.join(rule_checks.store.root, "luxalgo.json")).read())
    assert list(index) == ["made-up"] and rule_checks.store.get(index["made-up"])["about"] == {"luxalgo": "made-up"}
    assert entry["state"] == "runs" or entry["state"] == "no_alerts"
