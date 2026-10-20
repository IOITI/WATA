"""
Backtests asked for by an agent (Claude, through the MCP endpoint): who asked
is written with the run and never taken from its body, their runs give way to
the operator's and the paper runs, and the runner's answers kept stay within
their size.
"""

import asyncio
import json
import os

import pytest

from src.admin import backtest_requests
from src.admin.errors import NotPossible
from src.admin.processes import DONE, QUEUED
from tests.test_admin_backtests import (  # noqa: F401 (fixtures)
    NOW,
    FakeLauncher,
    config,
    folder_of,
    pine,
    scripts,
    service,
    until,
    worked,
)

CLAUDE = {"made_by": "claude", "made_via": "claude-mac", "experiment": "a1b2c3", "lockbox": None}


def asked(backtests, body=None, **research):
    async def ask():
        return backtests.create(body or {}, **research)

    return asyncio.run(ask())


def test_who_asked_is_written_with_the_run_and_shown(tmp_path, scripts, config):
    backtests = service(tmp_path, scripts, config)

    run = asked(backtests, pine(scripts), **CLAUDE)

    request = json.loads((folder_of(tmp_path, run["id"]) / "request.json").read_text())
    assert (request["made_by"], request["made_via"], request["experiment"]) == ("claude", "claude-mac", "a1b2c3")
    assert "lockbox" not in request
    assert (run["made_by"], run["experiment"]) == ("claude", "a1b2c3")


def test_the_body_cannot_say_who_asked(tmp_path, scripts, config):
    backtests = service(tmp_path, scripts, config)

    run = asked(backtests, pine(scripts, made_by="operator", experiment="forged"))

    request = json.loads((folder_of(tmp_path, run["id"]) / "request.json").read_text())
    assert "made_by" not in request and "experiment" not in request
    assert run["made_by"] is None


def test_the_operators_runs_and_the_paper_runs_go_before_the_agents(tmp_path, scripts, config):
    launcher = FakeLauncher(ends_with=None)
    backtests = service(tmp_path, scripts, config, launcher)

    @worked
    async def scenario(backtests):
        first = backtests.create(pine(scripts), **CLAUDE)
        await until(lambda: len(launcher.commands) == 1)
        claude_waiting = backtests.create(pine(scripts), **CLAUDE)
        operator_waiting = backtests.create(pine(scripts))
        positions = (backtests.get(operator_waiting["id"])["position"], backtests.get(claude_waiting["id"])["position"])
        waiting_of = backtests.waiting_of("claude")
        launcher.finish(0)
        await until(lambda: len(launcher.commands) == 2)
        started_second = launcher.commands[1][launcher.commands[1].index("--run") + 1]
        launcher.finish(1)
        await until(lambda: len(launcher.commands) == 3)
        launcher.finish(2)
        await until(lambda: backtests.get(claude_waiting["id"])["status"]["state"] == DONE)
        return first, operator_waiting, positions, waiting_of, started_second

    first, operator_waiting, positions, waiting_of, started_second = scenario(backtests)

    assert positions == (1, 2)
    assert waiting_of == 1
    assert started_second.endswith(operator_waiting["id"])


def test_a_sweeps_combination_waits_its_turn_like_any_run(tmp_path, scripts, config):
    backtests = service(tmp_path, scripts, config)
    backtests.max_queued = 2
    sweep = asked(backtests, pine(scripts, sweep={"axes": [{"kind": "setting", "name": "stoploss_percent", "values": [-10, -15]}]}))
    folder = folder_of(tmp_path, sweep["id"])
    (folder / "sweep.json").write_text(json.dumps({"cells": [{"values": [-10]}, {"values": [-15]}]}))
    (folder / "status.json").write_text(json.dumps({"state": DONE}))
    backtests._waiting.clear()
    asked(backtests, pine(scripts))
    asked(backtests, pine(scripts))

    with pytest.raises(NotPossible, match="waiting already"):
        backtests.run_cell(sweep["id"], 0)


def test_a_run_made_from_another_says_by_itself_who_asked(tmp_path, scripts, config):
    backtests = service(tmp_path, scripts, config)
    claude_run = asked(backtests, pine(scripts), **CLAUDE)
    folder = folder_of(tmp_path, claude_run["id"])
    (folder / "status.json").write_text(json.dumps({"state": DONE}))
    (folder / "summary.json").write_text(json.dumps({"made_with": {"code": "x"}}))
    backtests._waiting.clear()

    by_operator = backtests.stress(claude_run["id"])
    by_claude = backtests.stress(claude_run["id"], made_by="claude", made_via="claude-mac", experiment="d4e5f6")

    operator_request = json.loads((folder_of(tmp_path, by_operator["id"]) / "request.json").read_text())
    claude_request = json.loads((folder_of(tmp_path, by_claude["id"]) / "request.json").read_text())
    assert "made_by" not in operator_request and "experiment" not in operator_request
    assert (claude_request["made_by"], claude_request["experiment"]) == ("claude", "d4e5f6")


def test_runs_are_listed_by_who_asked_and_experiment_a_page_at_a_time(tmp_path, scripts, config):
    backtests = service(tmp_path, scripts, config)
    backtests.max_queued = 20
    ticks = iter(range(1_000_000))
    backtests._clock = lambda: NOW + next(ticks)
    mine = [asked(backtests, pine(scripts), **{**CLAUDE, "experiment": "a1b2c3"})["id"] for _ in range(3)]
    other = asked(backtests, pine(scripts), **{**CLAUDE, "experiment": "ffffff"})["id"]
    operator = asked(backtests, pine(scripts))["id"]

    assert backtests.run_ids(made_by="claude", experiment="a1b2c3") == sorted(mine, reverse=True)
    assert set(backtests.run_ids(made_by="claude")) == {*mine, other}
    assert set(backtests.run_ids()) == {*mine, other, operator}
    page = backtests.list_runs(made_by="claude", experiment="a1b2c3", offset=1, limit=1)
    assert [run["id"] for run in page] == [sorted(mine, reverse=True)[1]]


def test_the_runners_answers_kept_are_pruned_oldest_first_and_counted(tmp_path, scripts, config):
    backtests = service(tmp_path, scripts, config)
    backtests._signal_cache_bytes = 250
    cache = tmp_path / "backtests" / "_signal_cache" / "ab"
    cache.mkdir(parents=True)
    for number in range(5):
        path = cache / f"{number}.json"
        path.write_text("x" * 100)
        os.utime(path, (1_000 + number, 1_000 + number))

    assert backtests.storage()["signal_cache"] == {"count": 5, "bytes": 500, "limit": 250}
    assert backtests.prune_signal_cache() == 3
    assert sorted(path.name for path in cache.iterdir()) == ["3.json", "4.json"]


def test_without_a_size_the_answers_kept_are_never_pruned(tmp_path, scripts, config):
    backtests = service(tmp_path, scripts, config)
    cache = tmp_path / "backtests" / "_signal_cache" / "ab"
    cache.mkdir(parents=True)
    (cache / "0.json").write_text("x" * 100)

    assert backtests.prune_signal_cache() == 0
    assert backtests.storage()["signal_cache"]["limit"] is None


class Queries:
    def __init__(self):
        self.calls = []

    async def last_balance(self):
        return 20_000.0

    async def journal_between(self, first, last):
        self.calls.append(("journal_between", first, last))
        return []

    async def closed_positions(self, first, last):
        return []


class Service:
    def __init__(self):
        self.asked = []

    def create(self, body, **options):
        self.asked.append((body, options))
        return {"id": "20261020-100000-aaaaaa", "status": {"state": QUEUED}}


def test_asking_for_a_backtest_hands_who_asked_to_the_service():
    service_ = Service()

    asyncio.run(backtest_requests.ask_backtest(Queries(), service_, {"from": "2026-10-13", "to": "2026-10-16",
                                                                     "source": {"kind": "pine"}}, **CLAUDE))

    body, options = service_.asked[0]
    assert options["capital"] == 20_000.0
    assert {key: options[key] for key in CLAUDE} == CLAUDE
