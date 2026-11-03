"""Claude's Pine tools over a stand-in study service: what they ask, and what they refuse."""

import asyncio
import base64
import json
from datetime import date, datetime, timezone

import pytest

from src.admin.mcp_server.facade import CLAUDE
from tests.test_admin_backtests import config, scripts  # noqa: F401 (fixtures)
from tests.test_admin_ta_rules import STRATEGY, fields
from tests.test_mcp_tools_read import answer, journal, refusal, research  # noqa: F401 (fixtures)

SIMPLE = "//@version=6\nindicator('Simple')\nplot(close)\n"


class Studies:
    """Stands for the study child: answers what it is told, and remembers what it was asked."""

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.asked = []

    async def study(self, mode, request):
        self.asked.append((mode, request))
        return self.answers.get(mode, {"ok": True, "kind": "indicator", "inputs": [], "plots": ["Close"]})


@pytest.fixture
def studies(research):
    research.studies = Studies()
    return research.studies


def test_a_check_is_of_a_source_or_of_a_saved_script(research, studies, scripts):
    answer(research, "pine_check", {"source": SIMPLE, "name": "Simple"})
    script_id = next(meta["id"] for meta in scripts.scripts() if meta["name"] == "Cross")
    answer(research, "pine_check", {"script_id": script_id, "version": 1})

    assert studies.asked[0] == ("check", {"source": SIMPLE, "name": "Simple"})
    assert studies.asked[1] == ("check", {"script": {"id": script_id, "version": 1}})
    assert "one of them" in refusal(research, "pine_check", {})


def test_a_run_is_asked_with_the_trading_timezone_and_its_days(research, studies):
    answer(research, "pine_run", {"asset": "capital/US100", "start_day": "2026-10-13", "end_day": "2026-10-14",
                                  "source": SIMPLE, "bars": "45S", "inputs": {"in_0": 9}, "points": 50})

    mode, request = studies.asked[0]
    assert mode == "run"
    assert (request["from"], request["to"], request["timezone"], request["bars"]) == ("2026-10-13", "2026-10-14", "Europe/Paris", "45S")
    assert (request["inputs"], request["points"], request["name"]) == ({"in_0": 9}, 50, "Draft")


def test_a_study_never_looks_at_the_lock_box(research, studies, journal):
    journal.set_lockbox(date(2026, 10, 14), date(2026, 10, 20))

    assert "days from 2026-10-14 to 2026-10-20 are the lock box" in refusal(
        research, "pine_run", {"asset": "capital/US100", "start_day": "2026-10-13", "end_day": "2026-10-14", "source": SIMPLE})
    journal.set_lockbox(date(2026, 10, 14))
    assert "days from 2026-10-14 on are the lock box" in refusal(
        research, "pine_run", {"asset": "capital/US100", "start_day": "2026-10-15", "source": SIMPLE})
    assert studies.asked == []


def test_a_scripts_signals_are_set_beside_tradingviews_day_by_day(research):
    research.studies = Studies({"signals": {"ok": True, "signals_total": 2, "signals": [
        {"bar_time": "2026-10-13T07:00:00Z", "action": "long"},
        {"bar_time": "2026-10-13T08:00:00Z", "action": "short"},
    ]}})

    found = answer(research, "pine_compare", {"asset": "capital/US100", "start_day": "2026-10-13", "source": SIMPLE,
                                              "alerts": {"Up": "long", "Down": "short"}, "tolerance_seconds": 120})

    # TradingView's long came at 07:01 (tests/test_mcp_tools_read.py): paired; the short is the script's only.
    assert found["days"] == [{"day": "2026-10-13", "paired": 1, "only_script": 1, "only_tradingview": 0}]
    assert found["rows"][0]["delta_seconds"] == 60 and found["tolerance_seconds"] == 120
    # Its days and totals alone, without a row.
    bare = answer(research, "pine_compare", {"asset": "capital/US100", "start_day": "2026-10-13", "source": SIMPLE,
                                             "alerts": {"Up": "long", "Down": "short"}, "rows": 0})
    assert bare["rows"] == [] and bare["rows_total"] == 2 and bare["paired"] == 1


def test_a_run_lists_the_alerts_asked_for(research, studies):
    answer(research, "pine_run", {"asset": "capital/US100", "start_day": "2026-10-13", "source": SIMPLE})
    answer(research, "pine_run", {"asset": "capital/US100", "start_day": "2026-10-13", "source": SIMPLE,
                                  "alert_titles": ["Enter Long"], "max_alerts": 0})

    assert (studies.asked[0][1]["alert_titles"], studies.asked[0][1]["max_alerts"]) == ([], 100)
    assert (studies.asked[1][1]["alert_titles"], studies.asked[1][1]["max_alerts"]) == (["Enter Long"], 0)


def _run_answer(plots: int, alerts: int) -> dict:
    """A run's answer as the study gives it: plots of 300 points, alerts listed as an every-bar script raises them."""
    points = [[f"2026-10-13T{7 + index // 360:02d}:{index // 6 % 60:02d}:{index % 6 * 10:02d}Z", 25_000.5 + index]
              for index in range(300)]
    return {
        "ok": True, "bars": 16_500,
        "plots": {f"Plot {number}": {"bars": 16_500, "na_share": 0.0, "min": 24_728.1, "max": 25_273.1, "points": points}
                  for number in range(plots)},
        "alerts_by_title": {"Every": 16_500},
        "alerts": [{"bar_time": "2026-10-13T07:00:00Z", "closed_at": "2026-10-13T07:00:10Z", "type": "alertcondition",
                    "title": "Every", "message": '{"action": "long"}'}] * alerts,
        "alerts_listed_of": 16_500, "alerts_total": 16_500, "warnings": [], "runtime_warnings": [],
    }


@pytest.mark.parametrize("plots, alerts, points, listed", [
    (2, 100, 255, 100),   # 34,717 characters: the plots thinned, every alert asked for kept
    (12, 100, 40, 100),
    (12, 500, 0, 210),    # with no point left, the alerts listed cut
])
def test_a_run_too_large_for_claudes_context_is_thinned_and_says_so(research, plots, alerts, points, listed):
    research.studies = Studies({"run": _run_answer(plots, alerts)})

    found = answer(research, "pine_run", {"asset": "capital/US100", "start_day": "2026-10-13", "source": SIMPLE})

    assert len(json.dumps(found, separators=(",", ":"), ensure_ascii=False)) <= 32_000
    assert found["fitted"]["points_per_plot"] == points and found["fitted"]["alerts_listed"] == listed
    assert "fewer plots (plots)" in found["fitted"]["said"]
    # What is summarised stays whole.
    assert found["plots"]["Plot 0"]["min"] == 24_728.1 and found["alerts_by_title"] == {"Every": 16_500}
    first, last = _run_answer(1, 0)["plots"]["Plot 0"]["points"][::299]
    if points >= 2:
        assert found["plots"]["Plot 0"]["points"][0] == first and found["plots"]["Plot 0"]["points"][-1] == last


def test_a_run_that_fits_is_given_whole(research):
    research.studies = Studies({"run": _run_answer(1, 10)})

    found = answer(research, "pine_run", {"asset": "capital/US100", "start_day": "2026-10-13", "source": SIMPLE})

    assert "fitted" not in found and len(found["plots"]["Plot 0"]["points"]) == 300 and len(found["alerts"]) == 10


def test_a_script_that_does_not_compile_is_not_saved_and_its_diagnostics_come_back(research, scripts):
    research.studies = Studies({"check": {"ok": False, "error": {"message": "Unexpected token", "line": 3}}})

    found = answer(research, "pine_save", {"source": SIMPLE + "plot(close +)\n", "name": "Broken"})

    assert found == {"saved": False, "check": {"ok": False, "error": {"message": "Unexpected token", "line": 3}}}
    assert "Broken" not in {meta["name"] for meta in scripts.scripts()}


def test_claude_saves_its_own_scripts_and_a_copy_of_the_operators(research, studies, scripts):
    made = answer(research, "pine_save", {"source": SIMPLE, "name": "Claude simple", "description": "A try"})
    again = answer(research, "pine_save", {"source": SIMPLE + "// two\n", "script_id": made["id"]})
    operators = next(meta for meta in scripts.scripts() if meta["name"] == "Cross")

    assert (made["saved"], made["version"], again["version"]) == (True, 1, 2)
    assert scripts.script(made["id"])["made_by"] == CLAUDE
    assert "save the change as a new script" in refusal(research, "pine_save", {"source": STRATEGY + "// mine\n",
                                                                                 "script_id": operators["id"]})
    assert scripts.script(operators["id"])["version"] == 1
    assert "needs a name" in refusal(research, "pine_save", {"source": SIMPLE})


def test_claude_keeps_a_bounded_number_of_scripts(research, studies):
    research.limits = type(research.limits)(max_scripts=1)
    answer(research, "pine_save", {"source": SIMPLE, "name": "One"})

    assert "pine_delete one first" in refusal(research, "pine_save", {"source": SIMPLE + "// b\n", "name": "Two"})


def test_claude_removes_only_its_own_scripts_and_none_a_rule_runs(research, studies, scripts):
    mine = answer(research, "pine_save", {"source": SIMPLE, "name": "Mine"})
    pinned = answer(research, "pine_save", {"source": STRATEGY.replace('"Cross"', '"Claude cross"'), "name": "Claude cross"})
    research.rules.create(fields(scripts.script(pinned["id"]), name="Claude · cross"), research.rules.load()["revision"], made_by=CLAUDE)
    operators = next(meta for meta in scripts.scripts() if meta["name"] == "Cross")

    assert answer(research, "pine_delete", {"script_id": mine["id"]}) == {"removed": mine["id"], "name": "Mine"}
    assert "is used by" in refusal(research, "pine_delete", {"script_id": pinned["id"]})
    assert "only its own" in refusal(research, "pine_delete", {"script_id": operators["id"]})


def test_without_the_study_service_the_studies_say_so(research):
    assert "not available" in refusal(research, "pine_check", {"source": SIMPLE})


# ── a study that outlasts its call ──


class SlowStudies(Studies):
    """A study that goes on until the test lets it end."""

    def __init__(self):
        super().__init__()
        self.release = asyncio.Event()

    async def study(self, mode, request):
        self.asked.append((mode, request))
        await self.release.wait()
        return {"ok": True, "days": [], "signals": [], "signals_total": 0, "signals_by_action": {}}


def test_a_study_that_outlasts_its_call_is_handed_back_as_a_job_and_answered_later(research, monkeypatch):
    from types import SimpleNamespace

    from mcp import Client

    from src.admin.mcp_server import budget, jobs
    from src.admin.mcp_server.gate import CALLER
    from src.admin.mcp_server.server import build_server

    monkeypatch.setattr(budget, "ANSWER_SECONDS", 0.2)
    monkeypatch.setattr(jobs, "ANSWER_SECONDS", 0.2)
    research.studies = SlowStudies()
    asked = {"asset": "capital/US100", "start_day": "2026-10-13", "alerts": {"Enter Long": "long"}, "source": SIMPLE}

    async def session():
        CALLER.set(SimpleNamespace(name="claude-mac"))
        async with Client(build_server(research)) as client:
            async def tool(name, arguments):
                result = await client.call_tool(name, arguments)
                return result.content[0].text, result.is_error

            handed, _ = await tool("pine_signals", asked)
            job = json.loads(handed)["job"]
            still, _ = await tool("job_wait", {"job_id": job, "seconds": 0})
            behind, refused = await tool("pine_signals", asked)
            research.studies.release.set()
            done, _ = await tool("job_wait", {"job_id": job, "seconds": 5})
            again, _ = await tool("job_wait", {"job_id": job})
            CALLER.set(SimpleNamespace(name="someone-else"))
            theirs, unknown = await tool("job_wait", {"job_id": job})
            return json.loads(handed), json.loads(still), behind, refused, json.loads(done), json.loads(again), theirs, unknown

    handed, still, behind, refused, done, again, theirs, unknown = asyncio.run(session())

    assert (handed["state"], handed["tool"], handed["next"][:8]) == ("running", "pine_signals", "job_wait")
    assert still["state"] == "running" and still["job"] == handed["job"]
    # The second study was started too, as a job of its own, ended with the first.
    assert not refused and json.loads(behind)["state"] == "running"
    assert done["ok"] is True and done == again
    assert unknown and f"No job {handed['job']}" in theirs


# ── a day as chart_view reads it ──

PNG = b"\x89PNG\r\n\x1a\nmade-up"
VIEWED = {"ok": True, "view": {"script": "Cross v1", "rows": 168}, "table": {"columns": ["time"], "csv": "time\n"},
          "picture": {"png": base64.b64encode(PNG).decode("ascii"), "width": 1280, "height": 720, "plots": ["Mid"],
                      "title": "Cross v1 · capital/US100"}}


def utc(*parts) -> int:
    return int(datetime(*parts, tzinfo=timezone.utc).timestamp())


def shown(research, arguments: dict):
    """chart_view's blocks as Claude gets them: its text, and the picture's bytes when it has one."""
    from mcp import Client

    from src.admin.mcp_server.server import build_server

    async def calling():
        async with Client(build_server(research)) as client:
            return await client.call_tool("chart_view", arguments)

    result = asyncio.run(calling())
    assert not result.is_error, result.content[0].text
    pictures = [base64.b64decode(block.data) for block in result.content if block.type == "image"]
    return json.loads(result.content[0].text), pictures


def test_a_day_is_viewed_in_its_trading_hours_and_its_picture_comes_as_an_image(research):
    research.studies = Studies({"view": json.loads(json.dumps(VIEWED))})

    found, pictures = shown(research, {"day": "2026-10-14", "asset": "capital/US100", "source": SIMPLE, "bars": "R60",
                                       "inputs": {"in_0": 9}, "alerts": {"Up": "long"}, "plots": ["Close"]})

    mode, request = research.studies.asked[0]
    assert mode == "view" and pictures == [PNG]
    # Its picture is said in its text, never its bytes.
    assert found["picture"] == {"width": 1280, "height": 720, "plots": ["Mid"], "title": "Cross v1 · capital/US100"}
    assert (request["asset"], request["bars"], request["side"], request["source"], request["name"]) == (
        "capital/US100", "R60", "bid", SIMPLE, "Draft")
    assert (request["inputs"], request["alerts"], request["plots"], request["rows"]) == ({"in_0": 9}, {"Up": "long"}, ["Close"], 180)
    # The live rules' hours: 8:00 to 22:00 in Paris on 14 Oct (UTC+2), the engine day worked out by the study.
    assert request["view"] == [utc(2026, 10, 14, 6), utc(2026, 10, 14, 20)] and "engine" not in request
    assert request["hours"] == {"zone": "Europe/Paris", "start_hour": 8, "end_hour": 22}


def test_a_view_zooms_on_the_hours_asked_and_refuses_the_lock_box_and_a_day_without_its_asset(research, journal):
    research.studies = Studies({"view": {"ok": True, "view": {}}})

    found, pictures = shown(research, {"day": "2026-10-14", "asset": "capital/US100", "start": "15:30", "end": "16:15",
                                       "picture": False})

    request = research.studies.asked[0][1]
    assert request["view"] == [utc(2026, 10, 14, 13, 30), utc(2026, 10, 14, 14, 15)]
    assert request["picture"] is False and pictures == []
    assert "script" not in request and "source" not in request
    assert "HH:MM" in refusal(research, "chart_view", {"day": "2026-10-14", "asset": "capital/US100", "start": "9h"})
    assert "end comes after start" in refusal(research, "chart_view", {"day": "2026-10-14", "asset": "capital/US100",
                                                                        "start": "16:00", "end": "15:00"})
    assert "Name the asset" in refusal(research, "chart_view", {"day": "2026-10-14"})
    journal.set_lockbox(date(2026, 10, 14))
    assert "lock box" in refusal(research, "chart_view", {"day": "2026-10-14", "asset": "capital/US100"})


def test_a_run_s_day_is_viewed_with_what_the_run_ran_its_trades_and_its_untraded_signals(tmp_path, research, scripts):
    from tests.test_mcp_tools_read import DAYS, SUMMARY, TRADES, finished_run

    research.studies = Studies({"view": {"ok": True, "view": {}}})
    signals = [{"day": "2026-10-14", "fate": "executed", "action": "short", "decided_at": "2026-10-14T13:00:02Z"},
               {"day": "2026-10-14", "fate": "refused", "action": "long", "rule": "day_profit", "reason": "Daily target met",
                "decided_at": "2026-10-14T14:00:02Z"},
               {"day": "2026-10-13", "fate": "refused", "action": "long", "decided_at": "2026-10-13T08:00:00Z"}]
    days = [{**DAYS[0], "day": "2026-10-14", "regime": {"day_type": "Mixed", "range_percent": 1.2}}]
    run_id = finished_run(tmp_path, research, scripts, {"summary.json": SUMMARY, "trades.json": TRADES, "days.json": days,
                                                        "signals.json": signals})

    shown(research, {"day": "2026-10-14", "run_id": run_id, "inputs": {"in_0": 20}})
    request = research.studies.asked[0][1]
    shown(research, {"day": "2026-10-14", "run_id": run_id, "bars": "5", "source": SIMPLE})
    another = research.studies.asked[1][1]

    # The run's own script, libraries and inputs (one changed), on its bars, in its engine day.
    parts = request["parts"]
    assert parts["script"]["name"] == "Cross" and "Enter Long" in parts["script"]["source"]
    assert [library["name"] for library in parts["libraries"]] == ["Maths", "Bands"]
    assert parts["inputs"] == [{"id": "in_0", "var": "fast", "title": "Fast", "value": 20}]
    assert parts["chart"] == {"asset": {"provider": "dukascopy", "key": "us100"}, "side": "bid",
                              "bars": {"type": "range", "ticks": 60}}
    # The engine day holding the 14th's noon: from the restart of the 13th at 21:15 UTC.
    assert request["engine"] == [utc(2026, 10, 13, 21, 15), utc(2026, 10, 14, 21, 15)] and request["history_days"] == 1
    assert request["alerts"] == {"Enter Long": "long", "Exit": "close-position"}
    # Its trades and the signals of that day that made none, with why; the day's figures as the run wrote them.
    assert [trade["id"] for trade in request["trades"]] == [2]
    assert request["signals_by_fate"] == {"executed": 1, "refused": 1}
    assert request["signals"] == [{"at": "2026-10-14T14:00:02Z", "action": "long", "fate": "refused", "rule": "day_profit",
                                   "reason": "Daily target met"}]
    assert request["regime"] == {"day_type": "Mixed", "range_percent": 1.2}
    # Another script on other bars: the run's trades still drawn over it.
    assert "parts" not in another and another["source"] == SIMPLE and another["bars"] == "5"
    assert another["asset"] == "dukascopy/us100" and [trade["id"] for trade in another["trades"]] == [2]
    assert "not one of its days" in refusal(research, "chart_view", {"day": "2026-10-20", "run_id": run_id})


def test_a_view_that_outlasts_its_call_gives_its_picture_through_job_wait(research, monkeypatch):
    from types import SimpleNamespace

    from mcp import Client

    from src.admin.mcp_server import budget, jobs
    from src.admin.mcp_server.gate import CALLER
    from src.admin.mcp_server.server import build_server

    class SlowView(Studies):
        def __init__(self):
            super().__init__({"view": json.loads(json.dumps(VIEWED))})
            self.release = asyncio.Event()

        async def study(self, mode, request):
            await self.release.wait()
            return await super().study(mode, request)

    monkeypatch.setattr(budget, "ANSWER_SECONDS", 0.2)
    monkeypatch.setattr(jobs, "ANSWER_SECONDS", 0.2)
    research.studies = SlowView()

    async def session():
        CALLER.set(SimpleNamespace(name="claude-mac"))
        async with Client(build_server(research)) as client:
            handed = await client.call_tool("chart_view", {"day": "2026-10-14", "asset": "capital/US100"})
            research.studies.release.set()
            done = await client.call_tool("job_wait", {"job_id": json.loads(handed.content[0].text)["job"], "seconds": 5})
            return handed, done

    handed, done = asyncio.run(session())

    assert json.loads(handed.content[0].text)["state"] == "running" and len(handed.content) == 1
    assert [block.type for block in done.content] == ["text", "image"]
    assert base64.b64decode(done.content[1].data) == PNG and json.loads(done.content[0].text)["picture"]["width"] == 1280


def test_a_study_refused_behind_a_running_job_names_it():
    from types import SimpleNamespace

    from src.admin.errors import NotPossible
    from src.admin.mcp_server.budget import said

    outcome, _, error = said("pine_run", NotPossible("Another study is running: try again in a minute"),
                             SimpleNamespace(id="abc123", tool="pine_signals"))

    assert outcome == "refused" and str(error).endswith("(your job abc123 of pine_signals is at work: job_wait it)")
