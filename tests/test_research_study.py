"""
A study of a Pine script (python -m src.research.study) with the real Pine
runner, on Dukascopy day files written as the downloader writes them; and
Claude's Pine tools on top of it. Skipped where the runner is not built or no
Node 22+ is on the PATH.
"""

import asyncio
import json
import math
import os
import subprocess
import sys
from datetime import date, datetime, timezone

import pytest
from mcp import Client

from src.admin.mcp_server.server import build_server
from src.admin.processes import launch
from src.admin.studies import StudyService
from src.admin.ta_scripts import ScriptStore
from tests.test_admin_data import _bar, write_day
from tests.test_admin_ta_rules import BANDS, MATHS, STRATEGY
from tests.test_ta_engine import RUNNER, _node_major

pytestmark = pytest.mark.skipif(not os.path.isfile(RUNNER) or _node_major() < 22,
                                reason="the runner is not built, or no Node 22+ is here")

DAYS = (date(2026, 10, 13), date(2026, 10, 14))


@pytest.fixture
def scripts(tmp_path):
    store = ScriptStore(str(tmp_path / "ta"))
    store.create("Maths", MATHS)
    store.create("Bands", BANDS)
    store.create("Cross", STRATEGY)
    return store


@pytest.fixture
def prices(tmp_path):
    root = tmp_path / "prices"
    for day in DAYS:
        start = int(datetime(day.year, day.month, day.day, 6, tzinfo=timezone.utc).timestamp())
        bars = [_bar(start + 10 * n, 25000.0 + 40 * math.sin(n / 90), 25001.0 + 40 * math.sin(n / 90), high=1.0, low=1.0)
                for n in range(5040)]
        write_day(str(root), "us100", day, bars)
    return str(root)


def cross(scripts) -> dict:
    return next(meta for meta in scripts.scripts() if meta["name"] == "Cross")


def study(tmp_path, scripts, prices, mode: str, request: dict, *more: str) -> dict:
    request_path, out_path = tmp_path / f"{mode}.json", tmp_path / f"{mode}-out.json"
    request_path.write_text(json.dumps(request))
    subprocess.run(
        [sys.executable, "-m", "src.research.study", mode, "--request", str(request_path), "--out", str(out_path),
         "--root", scripts.root, "--prices", prices, "--signal-cache", str(tmp_path / "cache"), *more],
        capture_output=True, text=True, check=False, env={**os.environ, "PYTHONPATH": "./:src/"},
    )
    return json.loads(out_path.read_text())


def test_a_saved_script_a_library_and_a_draft_are_checked_on_made_up_bars(tmp_path, scripts, prices):
    saved = study(tmp_path, scripts, prices, "check", {"script": {"id": cross(scripts)["id"]}})
    bands = next(meta for meta in scripts.scripts() if meta["name"] == "Bands")
    library = study(tmp_path, scripts, prices, "check", {"script": {"id": bands["id"]}})
    broken = study(tmp_path, scripts, prices, "check", {"source": "//@version=6\nindicator('x')\nplot(close +)\n"})

    assert saved["ok"] and saved["kind"] == "indicator" and saved["inputs"][0]["id"] == "in_0"
    assert saved["plots"] == ["Mid"] and set(saved["alerts_on_made_up_bars"]) == {"Enter Long", "Exit"}
    assert library["ok"] and library["kind"] == "library"
    assert not broken["ok"] and (broken["error"]["file"], broken["error"]["line"]) == ("script", 3)


def test_a_run_gives_each_plot_summarised_and_the_alerts_of_the_days_asked(tmp_path, scripts, prices):
    found = study(tmp_path, scripts, prices, "run", {
        "script": {"id": cross(scripts)["id"]}, "asset": "dukascopy/us100", "bars": "1",
        "from": "2026-10-14", "to": "2026-10-14", "warmup_days": 1, "points": 10, "timezone": "UTC",
    })

    assert found["ok"] and found["bars"] == 840 and found["warmup_bars"] == 840
    mid = found["plots"]["Mid"]
    assert mid["na_share"] == 0.0 and mid["min"] < mid["mean"] < mid["max"]
    assert 2 <= len(mid["points"]) <= 11 and mid["points"][0][0] == "2026-10-14T06:00:00Z"
    assert found["alerts_by_title"]["Enter Long"] == found["alerts_total"] // 2
    assert all(alert["bar_time"].startswith("2026-10-14") for alert in found["alerts"])


def test_signals_are_raised_as_a_rule_would_and_read_back_the_second_time(tmp_path, scripts, prices):
    request = {"script": {"id": cross(scripts)["id"]}, "asset": "dukascopy/us100", "bars": "1", "from": "2026-10-14",
               "alerts": {"Enter Long": "long", "Exit": "close-position"}, "timezone": "UTC"}

    first = study(tmp_path, scripts, prices, "signals", request)
    again = study(tmp_path, scripts, prices, "signals", request)

    assert first["ok"] and set(first["signals_by_action"]) == {"long", "close-position"}
    assert first["windows_run"] >= 1 and again["windows_run"] == 0 and again["signals"] == first["signals"]


def test_a_rules_own_history_is_the_days_of_bars_given_before_each_engine_day(tmp_path, scripts, prices):
    request = {"script": {"id": cross(scripts)["id"]}, "asset": "dukascopy/us100", "bars": "1", "from": "2026-10-14",
               "alerts": {"Enter Long": "long", "Exit": "close-position"}, "timezone": "UTC"}

    engines = study(tmp_path, scripts, prices, "signals", request)
    none = study(tmp_path, scripts, prices, "signals", {**request, "history_days": 0})
    too_long = study(tmp_path, scripts, prices, "signals", {**request, "history_days": 31})

    # The engine's days from 13 October 21:15 UTC: 840 minutes recorded on each of the 13th and the 14th.
    assert engines["engine"]["history_days"] == 1 and [day["bars"] for day in engines["days"]] == [1680, 840]
    assert none["engine"]["history_days"] == 0 and [day["bars"] for day in none["days"]] == [840, 0]
    assert not too_long["ok"] and "history is 0 to 30 days" in too_long["error"]["message"]


def test_range_bars_far_below_the_assets_tick_are_refused_saying_what_one_point_is(tmp_path, scripts):
    # Prices in thousandths, as Dukascopy's: R100 is a tenth of a point, some forty bars each ten seconds here.
    root = tmp_path / "thousandths"
    for day in DAYS:
        start = int(datetime(day.year, day.month, day.day, 6, tzinfo=timezone.utc).timestamp())
        price = [round(25000.0 + 40 * math.sin(n / 90), 3) for n in range(5040)]
        write_day(str(root), "us100", day, [_bar(start + 10 * n, price[n], price[n] + 1.0, high=1.0, low=1.0) for n in range(5040)])
    request = {"script": {"id": cross(scripts)["id"]}, "asset": "dukascopy/us100", "bars": "R100", "from": "2026-10-14",
               "alerts": {"Enter Long": "long"}, "timezone": "UTC"}

    found = study(tmp_path, scripts, str(root), "signals", request)

    assert not found["ok"] and found["error"]["message"] == (
        "This engine day has more than 40,000 bars of R100 on dukascopy/us100: a tick of this asset is 0.001, "
        "so R100 is 0.1 points; one point is R1000 here"
    )


def test_capital_coms_bars_are_read_from_recorded_day_files_as_a_backtest_reads_them(tmp_path, scripts, prices):
    from src.capital_feed.day_files import day_path, write_day_file

    files = tmp_path / "recorded"
    for day in DAYS:
        start = datetime(day.year, day.month, day.day, 6, tzinfo=timezone.utc)
        rows = [(datetime.fromtimestamp(start.timestamp() + 10 * n, timezone.utc), *([25000.0 + 40 * math.sin(n / 90)] * 4),
                 *([25001.0 + 40 * math.sin(n / 90)] * 4), 3) for n in range(5040)]
        write_day_file(day_path(str(files), "us100", day), rows, epic="US100", label="us100", name="US Tech 100")

    found = study(tmp_path, scripts, prices, "run", {
        "script": {"id": cross(scripts)["id"]}, "asset": "capital/US100", "bars": "1", "from": "2026-10-14",
        "warmup_days": 1, "points": 10, "timezone": "UTC",
    }, "--data", f"files:{files}")

    assert found["ok"] and found["bars"] == 840 and found["warmup_bars"] == 840


@pytest.mark.parametrize("request_,refused", [
    ({"script": {"id": "000000000000"}, "asset": "dukascopy/us100", "from": "2026-10-14"}, "No script"),
    ({"source": "//@version=6\nindicator('x')\nplot(close)\n", "asset": "nasdaq", "from": "2026-10-14"}, "The asset is"),
    ({"source": "//@version=6\nindicator('x')\nplot(close)\n", "asset": "dukascopy/us100", "bars": "10S",
      "from": "2026-09-20", "to": "2026-10-14"}, "bars"),
])
def test_a_study_that_cannot_be_made_says_why(tmp_path, scripts, prices, request_, refused):
    found = study(tmp_path, scripts, prices, "run", request_)

    assert not found["ok"] and refused in found["error"]["message"]


def test_claude_saves_checks_runs_and_replays_a_script_through_its_tools(tmp_path, scripts, prices):
    from tests.test_mcp_tools_read import research as research_fixture  # noqa: F401

    from src.admin.mcp_server.facade import ConfigFacts, ReadQueries, ResearchServices
    from src.admin.mcp_server.limits import McpLimits
    from src.admin.ta_rules import RuleStore
    from src.research.experiments import ExperimentJournal
    from tests.test_mcp_tools_read import Calls, FakeQueries

    research = ResearchServices(
        version="9.9.9", timezone="UTC", limits=McpLimits(), calls=Calls(), tokens=None,
        config=ConfigFacts("etc/config_example.json"),
        journal=ExperimentJournal(tmp_path / "j.jsonl", tmp_path / "lock.json"), backtests_path=str(tmp_path / "b"),
        scripts=scripts, rules=RuleStore(scripts), queries=ReadQueries(FakeQueries()),
        studies=StudyService(root=str(tmp_path / "_studies"), ta_root=scripts.root, prices_root=prices,
                             signal_cache=str(tmp_path / "cache"), memory_mb=384, timeout_seconds=60,
                             minutes_per_hour=20, launcher=launch),
    )
    draft = STRATEGY.replace('indicator("Cross"', 'indicator("Claude cross"')

    async def calling():
        async with Client(build_server(research)) as client:
            async def tool(name, arguments):
                result = await client.call_tool(name, arguments)
                assert not result.is_error, result.content[0].text
                return json.loads(result.content[0].text)

            saved = await tool("pine_save", {"source": draft, "name": "Claude cross"})
            checked = await tool("pine_check", {"script_id": saved["id"]})
            ran = await tool("pine_run", {"asset": "dukascopy/us100", "start_day": "2026-10-14", "script_id": saved["id"],
                                          "points": 20})
            raised = await tool("pine_signals", {"asset": "dukascopy/us100", "start_day": "2026-10-14", "script_id": saved["id"],
                                                 "alerts": {"Enter Long": "long", "Exit": "close-position"}})
            broken = await tool("pine_save", {"source": draft + "\nplot(close +)\n", "script_id": saved["id"]})
            return saved, checked, ran, raised, broken

    saved, checked, ran, raised, broken = asyncio.run(calling())

    assert saved["saved"] and scripts.script(saved["id"])["made_by"] == "claude"
    assert checked["ok"] and ran["ok"] and ran["bars"] == 840
    assert raised["signals_total"] > 0
    assert broken["saved"] is False and broken["check"]["ok"] is False
    assert scripts.script(saved["id"])["version"] == 1


@pytest.mark.skipif(not os.path.isfile(os.path.join(os.path.dirname(os.path.dirname(RUNNER)), "checker", "check.mjs")),
                    reason="the checker of TradingView's rules is not built (npm run build:checker)")
def test_a_check_says_what_tradingviews_rules_and_watas_tickers_find(tmp_path, scripts, prices):
    source = '//@version=6\nindicator("x")\nx = ta.sma(close)\nplot(x)\ny = request.security("NASDAQ:QQQ", "5", close)\n'

    found = study(tmp_path, scripts, prices, "check", {"source": source})

    said = {(finding["source"], finding["line"]) for finding in found["tradingview_rules"]}
    assert ("TradingView rules", 3) in said and ("WATA", 5) in said
    assert any("ta.sma" in finding["message"] for finding in found["tradingview_rules"])
