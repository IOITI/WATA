"""Claude's Pine tools over a stand-in study service: what they ask, and what they refuse."""

from datetime import date

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
