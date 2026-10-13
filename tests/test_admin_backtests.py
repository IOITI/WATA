import asyncio
import json
import os
from datetime import date, datetime, timezone

import pytest

from src.admin.backtests import (
    CANCELLED,
    DONE,
    FAILED,
    FETCH_MAX_DAYS,
    INCOMPLETE,
    INTERRUPTED,
    KEEP_AFTER_STOP_SECONDS,
    KEEP_BACKOFF_MAX_SECONDS,
    KEEP_BACKOFF_SECONDS,
    KEEP_MAX_DAYS,
    KEEP_PAUSE_SECONDS,
    MAX_PERIOD_DAYS,
    MAX_QUEUED,
    QUEUED,
    RUN_ID,
    RUNNING,
    STOPPED,
    BacktestService,
    InvalidRequest,
    NotPossible,
    UnknownRun,
    checked_rules,
    real_trades_of,
    signals_to_replay,
)
from src.price_recorder.bars import Bar
from src.strategy.engine import LocalClock
from src.strategy.execution import WataRules
from src.strategy.history import dukascopy
from src.strategy.history.dukascopy import write_day_file

with open("etc/config_example.json", encoding="utf-8") as config_file:
    RULES = WataRules.from_config(json.load(config_file))

CLOCK = LocalClock("Europe/Paris")
DAY = date(2026, 10, 12)
# Monday 12 October 2026, 21:30:45 UTC.
NOW = datetime(2026, 10, 12, 21, 30, 45, tzinfo=timezone.utc).timestamp()


class FakeProcess:
    """A child process that ends when it is told to, or when it is terminated."""

    def __init__(self):
        self.returncode = None
        self.terminated = False
        self._ended = asyncio.Event()

    def end(self, code: int) -> None:
        self.returncode = code
        self._ended.set()

    async def wait(self) -> int:
        await self._ended.wait()
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.end(-15)

    def kill(self) -> None:
        self.end(-9)


def _option(arguments: list[str], name: str) -> str:
    return arguments[arguments.index(name) + 1]


def _number(text: str):
    for kind in (int, float):
        try:
            return kind(text)
        except ValueError:
            pass
    return text


class FakeLauncher:
    """Stands for the commands: remembers what it was asked to run, and writes what a backtest would."""

    def __init__(self, ends_with: int | None = 0, days: list[dict] | None = None, measured: dict | None = None):
        # None: the command keeps running until the test ends it.
        self.ends_with = ends_with
        # What the downloader reports of each day, and what the measure of the turbo finds.
        self.days = days
        self.measured = measured
        self.commands: list[list[str]] = []
        self.processes: list[FakeProcess] = []

    async def __call__(self, arguments: list[str], log_path: str) -> FakeProcess:
        self.commands.append(arguments)
        process = FakeProcess()
        self.processes.append(process)
        with open(log_path, "a", encoding="utf-8") as log:
            log.write("started\n")
        if self.ends_with is not None:
            self.finish(len(self.processes) - 1, self.ends_with)
        return process

    def finish(self, number: int, code: int = 0) -> None:
        arguments = self.commands[number]
        if arguments[0] == "src.strategy.backtest" and code == 0:
            out = _option(arguments, "--out")
            leverages = [float(value) for value in _option(arguments, "--points-per-unit").split(",")]
            with open(os.path.join(out, "summary.json"), "w", encoding="utf-8") as out_file:
                json.dump({"runs": [
                    {
                        "turbo": {"points_per_unit": leverage},
                        "summary": {
                            "days": 20, "days_with_an_entry": 15, "trades": 18, "win_rate": 61.1,
                            "total_percent": leverage / 100, "worst_drawdown": -15.0,
                            "sample": "too_few", "against_random": {"beaten": 72.5},
                        },
                    }
                    for leverage in leverages
                ]}, out_file)
        if arguments[0] == "src.strategy.sweep" and code == 0:
            figures = {"days": 10, "trades": 9, "total_percent": 4.0}
            axes = [text.partition("=") for text in (arguments[place + 1] for place, option in enumerate(arguments) if option == "--axis")]
            # As the command writes them: a number where the value is one.
            values = [[_number(value) for value in listed.split(",")] for _, _, listed in axes]
            cells = [[first] for first in values[0]] if len(values) == 1 else [[first, second] for first in values[0] for second in values[1]]
            with open(os.path.join(_option(arguments, "--out"), "sweep.json"), "w", encoding="utf-8") as out_file:
                json.dump({
                    "cells": [{"values": cell, "tuning": figures, "held_out": figures, "whole": figures} for cell in cells],
                }, out_file)
        if arguments[0] == "src.strategy.history.dukascopy" and self.days is not None:
            with open(_option(arguments, "--report"), "w", encoding="utf-8") as out_file:
                json.dump({"days": [
                    {"bars": 0, "unpublished_hours": 0, "unanswered_hours": 0, **day} for day in self.days
                ]}, out_file)
        if arguments[0] == "src.strategy.calibration" and code == 0 and self.measured is not None:
            with open(_option(arguments, "--out"), "w", encoding="utf-8") as out_file:
                json.dump(self.measured, out_file)
        self.processes[number].end(code)


def service(tmp_path, launcher=None, published=None, rules=lambda: RULES, clock=lambda: NOW) -> BacktestService:
    os.makedirs(tmp_path / "runs", exist_ok=True)
    return BacktestService(
        runs_root=str(tmp_path / "runs"),
        prices_root=str(tmp_path / "prices"),
        rules=rules,
        launcher=launcher or FakeLauncher(),
        publish=(lambda event, **data: published.append((event, data))) if published is not None else None,
        clock=clock,
    )


def ask(backtests: BacktestService, **changes) -> dict:
    return backtests.create(**{"first_day": date(2026, 9, 14), "last_day": date(2026, 10, 9), **changes})


async def until(condition, seconds: float = 2.0) -> None:
    """Let the service work until ``condition`` holds."""
    deadline = asyncio.get_running_loop().time() + seconds
    while not condition():
        assert asyncio.get_running_loop().time() < deadline, "the service did not get there"
        await asyncio.sleep(0.001)


def worked(scenario):
    """Run ``scenario(...)`` beside the service's own loop, as in the admin."""

    async def both(backtests: BacktestService, *arguments):
        worker = asyncio.create_task(backtests.run())
        try:
            return await scenario(backtests, *arguments)
        finally:
            worker.cancel()
            await backtests.close()

    return lambda *arguments: asyncio.run(both(*arguments))


def state_of(backtests: BacktestService, run_id: str) -> str:
    return backtests.get(run_id)["status"]["state"]


# ── asking ──


def test_a_run_asked_for_is_a_folder_holding_what_was_asked_and_the_rules_it_will_run_with(tmp_path):
    backtests = service(tmp_path)

    run = ask(backtests, parameters={"entry_mode": "cross_catchup"}, rules={"day_profit_target": 3.0}, note="higher target")

    assert RUN_ID.fullmatch(run["id"]) and run["id"].startswith("20261012-213045-")
    assert run["status"] == {"state": QUEUED, "created_at": "2026-10-12T21:30:45Z"}
    assert (run["from"], run["to"], run["strategy"], run["note"]) == ("2026-09-14", "2026-10-09", "kernel_vwap", "higher target")
    assert run["parameters"] == {"entry_mode": "cross_catchup"}
    assert (run["rules"], run["rules_before"]) == ({"day_profit_target": 3.0}, {"day_profit_target": 1.7})
    assert run["turbo"] == {"entry_price": 4.4, "spread": 0.03, "leverages": [80.0, 110.0, 140.0]}
    # Fills as kind as the bars and 10,000 to count the money on, unless asked otherwise.
    assert run["execution"] == {"capital": 10000.0, "fill_delay": 0, "slippage": 0.0}
    assert ask(backtests, capital=2500, fill_delay=30, slippage=1.5)["execution"] == {"capital": 2500.0, "fill_delay": 30, "slippage": 1.5}
    # The whole set of rules of the run: the live ones, with the one that was changed.
    assert run["run_rules"] == {**RULES.to_dict(), "day_profit_target": 3.0}
    assert sorted(os.listdir(tmp_path / "runs" / run["id"])) == ["request.json", "rules.json", "status.json"]
    # Nothing to compare with yet, and it weighs what it was asked.
    assert run["stale"] is None and 0 < run["bytes"] < 20000


def test_only_what_differs_from_the_defaults_and_from_the_live_rules_is_kept(tmp_path):
    backtests = service(tmp_path)

    run = ask(
        backtests,
        parameters={"entry_mode": "cross", "kernel_bandwidth": "8", "min_band_width": 20},
        rules={"stoploss_percent": -15, "max_trades_per_day": 3},
    )

    assert run["parameters"] == {"kernel_bandwidth": 8}
    assert run["rules"] == {"max_trades_per_day": 3}


@pytest.mark.parametrize("changes, message", [
    ({"first_day": date(2026, 10, 9), "last_day": date(2026, 10, 8)}, "ends before it starts"),
    ({"first_day": date(2025, 1, 1), "last_day": date(2026, 10, 9)}, f"longer than {MAX_PERIOD_DAYS} days"),
    ({"strategy": "martingale"}, "Unknown strategy 'martingale'"),
    ({"parameters": {"entry_mod": "cross"}}, "Unknown parameter 'entry_mod'"),
    ({"parameters": {"kernel_bandwidth": 100000}}, "kernel_bandwidth must be between 2 and 500"),
    ({"parameters": {"kernel_bandwidth": "many"}}, "needs a int"),
    ({"rules": {"trading_end_hour": 23}}, "The rule 'trading_end_hour' cannot be changed"),
    ({"rules": {"stoploss_percent": "low"}}, "The rule 'stoploss_percent' needs a number"),
    ({"rules": {"stoploss_percent": 5}}, "must be between -100 and -0.1"),
    ({"rules": {"max_trades_per_day": 2.5}}, "needs a whole number"),
    ({"rules": {"trailing_enabled": 1}}, "is on or off"),
    ({"leverages": []}, None),
    ({"leverages": [80, 80]}, "each once"),
    ({"leverages": [10, 20, 30, 40, 50, 60]}, "one to 5 leverages"),
    ({"entry_price": 0.01}, "price above its spread"),
    ({"note": "x" * 201}, "longer than 200 characters"),
    ({"capital": -1}, "The capital is an amount of money"),
    ({"fill_delay": 601}, "A fill waits between 0 and 600 seconds"),
    ({"slippage": 101}, "The slippage is between 0 and 100 index points"),
])
def test_what_makes_no_sense_is_refused_saying_why_and_leaves_no_folder(tmp_path, changes, message):
    backtests = service(tmp_path)

    if message is None:
        # No leverage given at all is the default three, not an error.
        assert ask(backtests, **changes)["turbo"]["leverages"] == [80.0, 110.0, 140.0]
        return
    with pytest.raises(InvalidRequest, match=message):
        ask(backtests, **changes)
    assert os.listdir(tmp_path / "runs") == []


def test_the_rules_that_can_be_changed_are_checked_one_by_one():
    assert checked_rules({"stoploss_percent": -10, "trailing_enabled": False, "cooldown_after_loss_minutes": 30.0}) == {
        "stoploss_percent": -10.0, "trailing_enabled": False, "cooldown_after_loss_minutes": 30,
    }
    with pytest.raises(InvalidRequest, match="needs a number"):
        checked_rules({"stoploss_percent": True})


def test_no_more_than_a_few_runs_wait_at_once(tmp_path):
    backtests = service(tmp_path)
    for _ in range(MAX_QUEUED):
        ask(backtests)

    with pytest.raises(NotPossible, match="already waiting"):
        ask(backtests)


def test_rules_that_cannot_be_read_are_said_so_instead_of_guessed(tmp_path):
    def broken():
        raise ValueError("Rule with type 'day_trading' not found in the configuration.")

    backtests = service(tmp_path, rules=broken)

    with pytest.raises(NotPossible, match="cannot be read: Rule with type 'day_trading' not found"):
        ask(backtests)
    with pytest.raises(NotPossible):
        backtests.options()


# ── running ──


def test_a_run_is_the_backtest_command_with_what_was_asked(tmp_path):
    launcher, published = FakeLauncher(), []
    backtests = service(tmp_path, launcher, published)

    @worked
    async def scenario(backtests):
        run = ask(backtests, parameters={"entry_mode": "cross_catchup", "kernel_bandwidth": 8}, note="catch-up")
        await until(lambda: state_of(backtests, run["id"]) == DONE)
        return backtests.get(run["id"])

    run = scenario(backtests)
    directory = str(tmp_path / "runs" / run["id"])

    assert launcher.commands == [[
        "src.strategy.backtest",
        "--from", "2026-09-14", "--to", "2026-10-09",
        "--root", str(tmp_path / "prices"), "--indice", "us100",
        "--rules", os.path.join(directory, "rules.json"),
        "--points-per-unit", "80,110,140",
        "--entry-price", "4.4", "--spread", "0.03",
        "--out", directory, "--progress", os.path.join(directory, "progress.json"),
        "--low-priority",
        "--capital", "10000", "--fill-delay", "0", "--slippage", "0",
        "--strategy", "kernel_vwap",
        "--set", "entry_mode=cross_catchup", "--set", "kernel_bandwidth=8",
    ]]
    assert run["status"] == {
        "state": DONE, "created_at": "2026-10-12T21:30:45Z", "started_at": "2026-10-12T21:30:45Z",
        "error": None, "finished_at": "2026-10-12T21:30:45Z",
    }
    # A list shows the figures of the middle leverage.
    assert run["headline"] == {
        "leverage": 110.0, "days": 20, "days_with_an_entry": 15, "trades": 18, "win_rate": 61.1,
        "total_percent": 1.1, "worst_drawdown": -15.0, "sample": "too_few", "beaten": 72.5,
    }
    assert [entry["turbo"]["points_per_unit"] for entry in run["summary"]["runs"]] == [80.0, 110.0, 140.0]
    assert published == [("backtest_finished", {"run_id": run["id"], "state": DONE, "note": "catch-up"})]


def test_runs_are_taken_one_at_a_time_in_the_order_they_were_asked(tmp_path):
    launcher = FakeLauncher(ends_with=None)
    backtests = service(tmp_path, launcher)

    @worked
    async def scenario(backtests):
        first, second = ask(backtests, note="first"), ask(backtests, note="second")
        await until(lambda: state_of(backtests, first["id"]) == RUNNING)
        waiting = backtests.get(second["id"])
        launcher.finish(0)
        await until(lambda: state_of(backtests, second["id"]) == RUNNING)
        launcher.finish(1)
        await until(lambda: state_of(backtests, second["id"]) == DONE)
        return first, waiting

    first, waiting = scenario(backtests)

    assert (waiting["status"]["state"], waiting["waiting_before"]) == (QUEUED, 0)
    assert [_option(command, "--out").rsplit(os.sep, 1)[1] for command in launcher.commands] == [first["id"], waiting["id"]]


def test_the_list_gives_the_newest_run_first_with_its_state_and_figures(tmp_path):
    seconds = iter(range(1000))
    backtests = service(tmp_path, clock=lambda: NOW + next(seconds))

    @worked
    async def scenario(backtests):
        earlier, later = ask(backtests, note="earlier"), ask(backtests, note="later")
        await until(lambda: state_of(backtests, later["id"]) == DONE)

    scenario(backtests)
    listed = backtests.list_runs()

    assert [run["note"] for run in listed] == ["later", "earlier"]
    assert [run["status"]["state"] for run in listed] == [DONE, DONE]
    assert listed[0]["headline"]["trades"] == 18
    # The list carries the headline only; the whole summary is read with the run.
    assert "summary" not in listed[0]


def test_a_command_that_fails_leaves_what_it_said(tmp_path):
    launcher, published = FakeLauncher(ends_with=2), []
    backtests = service(tmp_path, launcher, published)

    @worked
    async def scenario(backtests):
        run = ask(backtests)
        await until(lambda: state_of(backtests, run["id"]) == FAILED)
        return backtests.get(run["id"])

    run = scenario(backtests)

    assert run["status"]["error"] == "started"
    assert run["output"] == ["started"]
    assert (run["summary"], run["headline"]) == (None, None)
    assert published[0][1]["state"] == FAILED


def test_a_command_that_cannot_be_started_is_a_failed_run_not_a_stuck_queue(tmp_path):
    async def refusing(arguments, log_path):
        raise OSError("no such interpreter")

    backtests = service(tmp_path, refusing)

    @worked
    async def scenario(backtests):
        run, after = ask(backtests), ask(backtests)
        await until(lambda: state_of(backtests, after["id"]) == FAILED)
        return backtests.get(run["id"])

    assert scenario(backtests)["status"]["error"] == "The backtest could not be started: no such interpreter"


def test_a_waiting_run_that_is_stopped_is_never_started(tmp_path):
    launcher = FakeLauncher(ends_with=None)
    backtests = service(tmp_path, launcher)

    @worked
    async def scenario(backtests):
        first, second = ask(backtests), ask(backtests)
        await until(lambda: state_of(backtests, first["id"]) == RUNNING)
        stopped = backtests.cancel(second["id"])
        launcher.finish(0)
        await until(lambda: state_of(backtests, first["id"]) == DONE)
        await asyncio.sleep(0.01)
        return stopped

    assert scenario(backtests)["status"]["state"] == CANCELLED
    assert len(launcher.commands) == 1


def test_a_running_run_that_is_stopped_has_its_process_terminated(tmp_path):
    launcher = FakeLauncher(ends_with=None)
    backtests = service(tmp_path, launcher)

    @worked
    async def scenario(backtests):
        run = ask(backtests)
        await until(lambda: state_of(backtests, run["id"]) == RUNNING)
        backtests.cancel(run["id"])
        await until(lambda: state_of(backtests, run["id"]) == CANCELLED)
        with pytest.raises(NotPossible, match="waiting or running"):
            backtests.cancel(run["id"])
        return backtests.get(run["id"])

    run = scenario(backtests)

    assert launcher.processes[0].terminated
    assert run["status"]["error"] is None


def test_a_run_left_unfinished_by_an_admin_that_stopped_is_said_to_be_interrupted(tmp_path):
    earlier = service(tmp_path)
    waiting = ask(earlier)
    running = ask(earlier)
    earlier._set_status(running["id"], state=RUNNING)

    restarted = service(tmp_path)

    @worked
    async def scenario(backtests):
        await until(lambda: state_of(backtests, running["id"]) == INTERRUPTED)

    scenario(restarted)

    assert state_of(restarted, waiting["id"]) == INTERRUPTED
    assert "stopped before this run was finished" in restarted.get(running["id"])["status"]["error"]


# ── removing ──


def test_a_finished_run_can_be_removed_and_one_in_progress_cannot(tmp_path):
    launcher = FakeLauncher(ends_with=None)
    backtests = service(tmp_path, launcher)

    @worked
    async def scenario(backtests):
        running, waiting = ask(backtests), ask(backtests)
        await until(lambda: state_of(backtests, running["id"]) == RUNNING)
        for run in (running, waiting):
            with pytest.raises(NotPossible, match="Stop this backtest"):
                backtests.delete(run["id"])
        launcher.finish(0)
        await until(lambda: state_of(backtests, running["id"]) == DONE)
        backtests.delete(running["id"])
        return running["id"]

    removed = scenario(backtests)

    assert not os.path.exists(tmp_path / "runs" / removed)
    with pytest.raises(UnknownRun):
        backtests.get(removed)


@pytest.mark.parametrize("run_id", [
    "", ".", "..", "../outside", "20261012-213045-zzzzzz", "20261012-213045-a1b2c3/../../outside", "_fetch",
    "20261012-213045-a1b2c3",
])
def test_an_id_that_is_not_one_of_a_run_reaches_no_folder(tmp_path, run_id):
    backtests = service(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "status.json").write_text("{}")

    for read in (backtests.get, backtests.cancel, backtests.delete):
        with pytest.raises(UnknownRun):
            read(run_id)
    assert outside.exists()


# ── reading ──


def test_rows_come_back_with_numbers_as_numbers(tmp_path):
    backtests = service(tmp_path)
    run = ask(backtests, leverages=[110])
    directory = tmp_path / "runs" / run["id"]
    (directory / "trades_110.csv").write_text(
        "entry_time,exit_time,direction,entry_reason,confidence,entry_index,exit_index,index_points,"
        "performance_percent,max_performance_percent,exit_reason\n"
        "2026-10-12T06:25:50Z,2026-10-12T06:26:40Z,long,cross,1.47,29910.5,29922.03,11.53,1.7,1.7,Daily profit target\n"
        "2026-10-12T08:00:00Z,2026-10-12T08:10:00Z,short,catch-up,,29900.0,29960.0,-60.0,-15.0,0.0,Stoploss\n"
    )
    (directory / "days_110.csv").write_text("day,entries,closed,day_percent\n2026-10-12,2,2,-13.56\n")

    trades = backtests.rows(run["id"], "trades", 110.0)

    assert trades[0] == {
        "entry_time": "2026-10-12T06:25:50Z", "exit_time": "2026-10-12T06:26:40Z", "direction": "long",
        "entry_reason": "cross", "confidence": 1.47, "entry_index": 29910.5, "exit_index": 29922.03,
        "index_points": 11.53, "performance_percent": 1.7, "max_performance_percent": 1.7,
        "exit_reason": "Daily profit target",
        # Written before the worst level of a trade and its money were kept: read as not known.
        "min_performance_percent": None, "scale_percent": None, "stake": None, "profit_loss": None,
    }
    assert trades[1]["confidence"] is None
    assert backtests.rows(run["id"], "days", 110.0) == [
        {"day": "2026-10-12", "entries": 2, "closed": 2, "day_percent": -13.56, "profit_loss": None, "equity": None},
    ]
    # Nothing written yet for these: no rows, not an error.
    assert backtests.rows(run["id"], "refusals", 110.0) == []
    with pytest.raises(InvalidRequest, match="not run with that leverage"):
        backtests.rows(run["id"], "trades", 80.0)
    with pytest.raises(InvalidRequest, match="Unknown kind"):
        backtests.rows(run["id"], "secrets", 110.0)


def test_the_form_is_told_the_strategies_the_turbo_assumed_and_the_rules_in_force(tmp_path):
    options = service(tmp_path).options()

    (strategy,) = options["strategies"]
    assert strategy["name"] == "kernel_vwap"
    assert "entry_mode" in [parameter["name"] for parameter in strategy["parameters"]]
    assert options["turbo"] == {"entry_price": 4.4, "spread": 0.03, "leverages": [80.0, 110.0, 140.0], "measured": False}
    assert options["rules"]["live"]["day_profit_target"] == 1.7
    assert {rule["name"]: rule["kind"] for rule in options["rules"]["editable"]}["trailing_enabled"] == "boolean"
    # Every rule offered for a change is one of the rules a run is given.
    assert {rule["name"] for rule in options["rules"]["editable"]} <= set(options["rules"]["live"])
    assert (options["timezone"], options["limits"]["period_days"]) == ("Europe/Paris", MAX_PERIOD_DAYS)


# ── a real run, through a real child process ──


def _leg(first: float, last: float, step: float = 2) -> list[float]:
    step = step if last > first else -step
    return [first + step * index for index in range(1, int(abs(last - first) / abs(step)) + 1)]


def _recorded(start: int, prices: list[float]) -> list[Bar]:
    bars = []
    for index, (opened, closed) in enumerate(zip(prices, prices[1:])):
        high, low = max(opened, closed), min(opened, closed)
        bars.append(Bar("index", start + 10 * index, opened, high, low, closed, opened + 1, high + 1, low + 1, closed + 1, 5, 0))
    return bars


def _one_day_of_prices(prices_root) -> None:
    """Back and forth before the session, then 200 points down from eight o'clock and 400 up."""
    night = [30000.0] + _leg(30000, 30060) + _leg(30060, 30000) + _leg(30000, 30060) + _leg(30060, 30000)
    session = [30000.0] + _leg(30000, 29800) + _leg(29800, 30200) + [30200.0] * 6
    bars = _recorded(CLOCK.epoch(DAY, "07:00"), night) + _recorded(CLOCK.epoch(DAY, "08:00"), session)
    write_day_file(dukascopy.day_path(str(prices_root), "us100", DAY), bars, symbol="TEST", label="us100")


def test_a_run_through_the_real_command_gives_its_figures_its_trades_and_its_day_chart(tmp_path):
    _one_day_of_prices(tmp_path / "prices")
    backtests = BacktestService(
        runs_root=str(tmp_path / "runs"), prices_root=str(tmp_path / "prices"), rules=lambda: RULES,
    )
    os.makedirs(tmp_path / "runs")

    @worked
    async def scenario(backtests):
        run = backtests.create(
            first_day=DAY, last_day=DAY, parameters={"entry_mode": "cross_catchup"}, leverages=[110],
        )
        await until(lambda: state_of(backtests, run["id"]) in (DONE, FAILED), seconds=30)
        return backtests.get(run["id"])

    run = scenario(backtests)

    assert run["status"]["state"] == DONE, run["output"]
    assert run["headline"]["trades"] == 1
    assert run["summary"]["parameters_changed"] == {"entry_mode": "cross_catchup"}
    assert run["summary"]["days_incomplete"] == ["2026-10-12"]

    (trade,) = backtests.rows(run["id"], "trades", 110.0)
    assert (trade["direction"], trade["entry_reason"], trade["exit_reason"]) == ("short", "catch-up", "Daily profit target")
    assert [refusal["reason"] for refusal in backtests.rows(run["id"], "refusals", 110.0)] == ["Day profit reached"]

    # Made with the code and the prices there are: it would come out the same today.
    assert run["stale"] == {"code": False, "prices": False, "version": run["summary"]["code"]["version"]}
    assert backtests.list_runs()[0]["stale"] == run["stale"]
    # In money too: 10,000 at the size WATA gives that entry.
    assert run["summary"]["runs"][0]["summary"]["money"]["profit_loss"] == trade["profit_loss"] == 169.87
    assert backtests.rows(run["id"], "days", 110.0)[0]["equity"] == 10169.87

    path = backtests.trade_path(run["id"], 110.0, trade["entry_time"])
    assert (path["trade"], path["timezone"], path["day_before_percent"]) == (trade, "Europe/Paris", 0.0)
    assert path["levels"] == {"stop": -15, "take_profit": 60, "day_target": 1.7, "trailing_activation": 5, "trailing_drawdown": 8}
    # Every ten seconds from the entry to the exit: when, the result at their end, at their worst, at their best,
    # and no trailing stop for a trade that never gained the 5 % that arm it.
    first, last = path["points"][0], path["points"][-1]
    assert first[0] == CLOCK.epoch(DAY, "08:06") + 10 and first[2] <= first[1] <= first[3] and first[4] is None
    assert last[0] == first[0] + 10 * (len(path["points"]) - 1)
    # Sold in its last ten seconds, at the target: what they did after is not the trade's.
    assert (last[1], last[3]) == (1.7, 1.7)
    with pytest.raises(InvalidRequest, match="No trade of this backtest was entered at that moment"):
        backtests.trade_path(run["id"], 110.0, "2026-10-12T00:00:00Z")

    broken = backtests.breakdowns(run["id"], 110.0)
    assert (broken["summary"]["trade_count"], broken["summary"]["win_rate"], broken["summary"]["total_percent"]) == (1, 100.0, 1.7)
    assert [(group["group"], group["trade_count"], group["average_percent"]) for group in broken["groups"]["hour"]] == [(8, 1, 1.7)]
    assert [group["group"] for group in broken["groups"]["weekday"]] == ["Monday"]

    chart = backtests.day_chart(run["id"], DAY, 110.0)
    assert (chart["day"], chart["timezone"], chart["size"], chart["tick"], chart["decimals"]) == ("2026-10-12", "Europe/Paris", 6.0, 0.1, 1)
    assert len(chart["bars"]) == len(chart["kernel"]) == len(chart["vwap"]) == len(chart["upper"]) == len(chart["entries_allowed"]) == 136
    # The first bar as the chart of the Prices page takes it: place, prices, began, ended, complete, after a pause, no index.
    assert chart["bars"][0] == [CLOCK.epoch(DAY, "07:00"), 30000.5, 30006.5, 30000.5, 30006.5, CLOCK.epoch(DAY, "07:00"), CLOCK.epoch(DAY, "07:00") + 40, True, False, None]
    places = [bar[0] for bar in chart["bars"]]
    assert places == sorted(set(places))
    # No VWAP before the session; a band around it once there is one.
    assert chart["vwap"][0] is None and chart["upper"][0] is None
    assert chart["upper"][-1] > chart["vwap"][-1] > chart["lower"][-1]
    assert chart["session"] == {"start": CLOCK.epoch(DAY, "08:00"), "end": CLOCK.epoch(DAY, "21:55")}
    assert [(decision["target"], decision["reason"]) for decision in chart["decisions"]] == [("short", "catch-up"), ("long", "cross")]
    assert chart["trades"] == [trade]
    assert len(chart["refusals"]) == 1

    with pytest.raises(InvalidRequest, match="not in the period"):
        backtests.day_chart(run["id"], date(2026, 10, 13), 110.0)

    # The day of prices is written again with other bars, and the code is another: the run says so.
    write_day_file(dukascopy.day_path(str(tmp_path / "prices"), "us100", DAY), [], symbol="TEST", label="us100")
    assert backtests.get(run["id"])["stale"]["prices"] is True
    backtests._code = {"version": "0.13.0", "digest": "another"}
    assert backtests.list_runs()[0]["stale"] == {"code": True, "prices": True, "version": run["summary"]["code"]["version"]}


# ── prices ──


def _keep_hours(prices_root, day: date, hours) -> None:
    for hour in hours:
        path = dukascopy.raw_path(str(prices_root), dukascopy.SYMBOL, dukascopy.day_hours(day)[hour])
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "wb").close()


def _day_file(prices_root, day: date) -> None:
    """A file of bars for the day, as the downloader writes one."""
    write_day_file(dukascopy.day_path(str(prices_root), "us100", day), [], symbol="TEST", label="us100")


def test_each_day_is_ready_partial_incomplete_missing_or_closed(tmp_path):
    _one_day_of_prices(tmp_path / "prices")
    _keep_hours(tmp_path / "prices", DAY, range(24))
    _keep_hours(tmp_path / "prices", date(2026, 10, 13), range(5))
    # Written while its last hours were not published yet.
    _day_file(tmp_path / "prices", date(2026, 10, 15))
    _keep_hours(tmp_path / "prices", date(2026, 10, 15), range(20))
    backtests = service(tmp_path)

    coverage = backtests.coverage(date(2026, 10, 10), date(2026, 10, 15))

    assert [(day["day"], day["state"], day["hours"], day["hours_expected"]) for day in coverage["days"]] == [
        ("2026-10-10", "closed", 0, 0),          # a Saturday
        ("2026-10-11", "missing", 0, 4),         # a Sunday: the evening only
        ("2026-10-12", "ready", 24, 24),
        ("2026-10-13", "incomplete", 5, 24),
        ("2026-10-14", "missing", 0, 24),
        ("2026-10-15", "partial", 20, 24),
    ]
    assert coverage["ready"] == {"count": 2, "first": "2026-10-12", "last": "2026-10-15"}
    assert coverage["fetch"] == {"state": "idle", "lines": []}
    assert (coverage["keep"]["enabled"], coverage["keep"]["state"]) == (False, "off")
    with pytest.raises(InvalidRequest):
        backtests.coverage(date(2026, 10, 14), date(2026, 10, 10))


def test_a_download_is_the_downloader_command_and_ends_with_days_missing_more_often_than_not(tmp_path):
    launcher, published = FakeLauncher(ends_with=None), []
    backtests = service(tmp_path, launcher, published)

    @worked
    async def scenario(backtests):
        started = backtests.start_fetch(date(2026, 10, 5), date(2026, 10, 9))
        await until(lambda: len(launcher.commands) == 1)
        with pytest.raises(NotPossible, match="already running"):
            backtests.start_fetch(date(2026, 10, 5), date(2026, 10, 9))
        launcher.finish(0, code=1)
        await until(lambda: backtests.fetch_status()["state"] != RUNNING)
        return started, backtests.fetch_status()

    started, ended = scenario(backtests)

    assert launcher.commands == [[
        "src.strategy.history.dukascopy", "--from", "2026-10-05", "--to", "2026-10-09",
        "--root", str(tmp_path / "prices"), "--label", "us100", "--low-priority",
        "--report", str(tmp_path / "runs" / "_fetch" / "report.json"),
    ]]
    assert (started["state"], started["from"], started["to"], started["automatic"]) == (RUNNING, "2026-10-05", "2026-10-09", False)
    assert (ended["state"], ended["exit_code"], ended["lines"]) == (INCOMPLETE, 1, ["started"])
    assert published == [("prices_fetch_finished", {"state": INCOMPLETE, "automatic": False})]


def test_a_download_can_be_stopped(tmp_path):
    launcher = FakeLauncher(ends_with=None)
    backtests = service(tmp_path, launcher)

    @worked
    async def scenario(backtests):
        with pytest.raises(NotPossible, match="No download is running"):
            backtests.stop_fetch()
        backtests.start_fetch(date(2026, 10, 5), date(2026, 10, 9))
        await until(lambda: len(launcher.processes) == 1)
        backtests.stop_fetch()
        await until(lambda: backtests.fetch_status()["state"] != RUNNING)
        return backtests.fetch_status()["state"]

    assert scenario(backtests) == STOPPED
    assert launcher.processes[0].terminated


@pytest.mark.parametrize("first_day, last_day, message", [
    (date(2026, 10, 9), date(2026, 10, 5), "ends before it starts"),
    (date(2026, 6, 1), date(2026, 10, 9), f"{FETCH_MAX_DAYS} days at most"),
    (date(2026, 10, 12), date(2026, 10, 13), "Prices of the future"),
])
def test_a_download_that_makes_no_sense_is_refused(tmp_path, first_day, last_day, message):
    backtests = service(tmp_path)

    async def scenario():
        with pytest.raises(InvalidRequest, match=message):
            backtests.start_fetch(first_day, last_day)

    asyncio.run(scenario())


def test_a_download_an_earlier_admin_left_running_is_said_to_be_interrupted(tmp_path):
    os.makedirs(tmp_path / "runs" / "_fetch")
    (tmp_path / "runs" / "_fetch" / "status.json").write_text(json.dumps({"state": RUNNING, "from": "2026-10-05"}))

    assert service(tmp_path).fetch_status()["state"] == INTERRUPTED


# ── prices kept up to date without anyone asking ──

# Monday 12 October 2026 at 23:30 in Paris: the market is closed. The day before is a Sunday.
MARKET_OPEN = datetime(2026, 10, 12, 10, 0, tzinfo=timezone.utc).timestamp()


class Clock:
    def __init__(self, now: float = NOW):
        self.now = now

    def __call__(self) -> float:
        return self.now


def _whole_day(prices_root, day: date) -> None:
    _day_file(prices_root, day)
    _keep_hours(prices_root, day, range(24))


def test_nothing_is_fetched_by_itself_until_it_is_asked_for(tmp_path):
    backtests = service(tmp_path)

    assert backtests.keep_once() == "off"
    assert backtests.keep_status() == {
        # Of the last 120 days: the weekdays and the Sunday evenings. The Saturdays are closed.
        "enabled": False, "days": 120, "state": "off", "missing": 103, "settled": 0, "failures": 0, "next_at": None,
        "limits": {"days": KEEP_MAX_DAYS},
    }
    for days in (0, KEEP_MAX_DAYS + 1):
        with pytest.raises(InvalidRequest, match=f"Between 1 and {KEEP_MAX_DAYS} days"):
            backtests.set_keep(True, days)


def test_the_newest_days_that_are_missing_are_fetched_first_a_few_at_a_time(tmp_path):
    launcher = FakeLauncher(ends_with=None)
    backtests = service(tmp_path, launcher)
    # Friday is there already: nothing is asked for a second time.
    _whole_day(tmp_path / "prices", date(2026, 10, 9))

    @worked
    async def scenario(backtests):
        status = backtests.set_keep(True, 10)
        found = backtests.keep_once()
        await until(lambda: len(launcher.commands) == 1)
        return status, found, backtests.keep_once(), backtests.fetch_status()

    status, found, again, fetch = scenario(backtests)

    # From the 2nd to the 11th: six weekdays and the Sunday evening are missing, the Saturdays are closed.
    assert (status["enabled"], status["days"], status["state"], status["missing"]) == (True, 10, "due", 7)
    assert (found, again) == ("fetching", "fetching")
    # Five days ending at the newest one that is missing, yesterday. Today is left to tomorrow.
    assert launcher.commands[0][1:5] == ["--from", "2026-10-07", "--to", "2026-10-11"]
    assert (fetch["state"], fetch["automatic"]) == (RUNNING, True)


def test_nothing_is_fetched_by_itself_while_the_market_is_open(tmp_path):
    clock = Clock(MARKET_OPEN)
    backtests = service(tmp_path, clock=clock)
    backtests.set_keep(True, 10)

    assert backtests.keep_once() == "waiting_for_close"
    # A rule that cannot be read: the market is taken to be open.
    unreadable = service(tmp_path, rules=lambda: (_ for _ in ()).throw(ValueError("no rule")), clock=Clock(NOW))
    assert unreadable.keep_once() == "waiting_for_close"


def test_a_download_that_went_well_is_followed_by_the_next_after_a_pause(tmp_path):
    clock = Clock()
    launcher = FakeLauncher(days=[{"day": "2026-10-09", "status": "written"}])
    backtests = service(tmp_path, launcher, clock=clock)

    @worked
    async def scenario(backtests):
        backtests.set_keep(True, 10)
        backtests.keep_once()
        await until(lambda: backtests.fetch_status()["state"] == DONE)
        waiting = backtests.keep_status()
        once = backtests.keep_once()
        clock.now += KEEP_PAUSE_SECONDS
        later = backtests.keep_once()
        await until(lambda: len(launcher.commands) == 2)
        return waiting, once, later

    waiting, once, later = scenario(backtests)

    assert (waiting["state"], waiting["failures"], waiting["next_at"]) == ("pausing", 0, "2026-10-12T21:35:45Z")
    assert (once, later) == ("pausing", "fetching")
    assert len(launcher.commands) == 2


def test_a_server_that_stops_answering_is_left_alone_longer_each_time(tmp_path):
    # A Saturday morning: the market stays closed for all the waiting there is here.
    clock = Clock(datetime(2026, 10, 10, 0, 30, tzinfo=timezone.utc).timestamp())
    # Hours it never answered for, and days it did not even try after that.
    launcher = FakeLauncher(ends_with=1, days=[
        {"day": "2026-10-08", "status": "incomplete", "unanswered_hours": 24},
        {"day": "2026-10-09", "status": "not tried"},
    ])
    backtests = service(tmp_path, launcher, clock=clock)

    @worked
    async def scenario(backtests):
        backtests.set_keep(True, 10)
        waits = []
        for attempt in range(1, 7):
            assert backtests.keep_once() == "fetching"
            await until(lambda: len(launcher.commands) == attempt and backtests.fetch_status()["state"] == INCOMPLETE)
            status = backtests.keep_status()
            assert (status["state"], status["failures"]) == ("backing_off", attempt)
            waits.append(backtests._keeper()["next_at"] - clock.now)
            clock.now += waits[-1] - 1
            assert backtests.keep_once() == "backing_off"
            clock.now += 1
        return waits

    waits = scenario(backtests)

    assert waits[:3] == [KEEP_BACKOFF_SECONDS, KEEP_BACKOFF_SECONDS * 2, KEEP_BACKOFF_SECONDS * 4]
    assert waits[-1] == KEEP_BACKOFF_MAX_SECONDS


def test_a_day_the_source_has_no_more_of_is_not_asked_for_again(tmp_path):
    clock = Clock()
    launcher = FakeLauncher(days=[
        # Answered for every hour, with nothing for any: a day the source does not have.
        {"day": "2026-10-05", "status": "empty", "unpublished_hours": 24},
        # Written without its last hours, a week ago: they will not come.
        {"day": "2026-10-06", "status": "partial", "unpublished_hours": 2},
        # Yesterday: its last hours may still be published.
        {"day": "2026-10-11", "status": "partial", "unpublished_hours": 1},
        # A Saturday is never asked for anyway.
        {"day": "2026-10-10", "status": "empty"},
    ])
    backtests = service(tmp_path, launcher, clock=clock)
    _day_file(tmp_path / "prices", date(2026, 10, 6))
    _day_file(tmp_path / "prices", date(2026, 10, 11))

    @worked
    async def scenario(backtests):
        backtests.set_keep(True, 10)
        backtests.keep_once()
        await until(lambda: backtests.fetch_status()["state"] == DONE)
        return backtests.coverage(date(2026, 10, 5), date(2026, 10, 11))

    coverage = scenario(backtests)

    assert backtests._keeper()["settled"] == {"2026-10-05": "empty", "2026-10-06": "partial"}
    assert [(day["day"][-2:], day["state"]) for day in coverage["days"]] == [
        ("05", "unavailable"), ("06", "ready"), ("07", "missing"), ("08", "missing"), ("09", "missing"),
        ("10", "closed"), ("11", "partial"),
    ]
    # From the 2nd: that Friday, the evening of Sunday the 4th, the 7th, 8th and 9th, and yesterday.
    assert (coverage["keep"]["missing"], coverage["keep"]["settled"]) == (6, 2)


def test_a_download_the_operator_stopped_is_not_started_again_at_once(tmp_path):
    clock = Clock()
    launcher = FakeLauncher(ends_with=None)
    backtests = service(tmp_path, launcher, clock=clock)

    @worked
    async def scenario(backtests):
        backtests.set_keep(True, 10)
        backtests.keep_once()
        await until(lambda: len(launcher.processes) == 1)
        backtests.stop_fetch()
        await until(lambda: backtests.fetch_status()["state"] == STOPPED)
        return backtests.keep_once(), backtests._keeper()["next_at"] - clock.now

    assert scenario(backtests) == ("pausing", KEEP_AFTER_STOP_SECONDS)
    # Switching it on again is asking: the wait is over.
    assert backtests.set_keep(True, 10)["state"] == "due"


def test_the_service_looks_by_itself_whether_a_download_is_due(tmp_path):
    launcher = FakeLauncher(ends_with=None)
    backtests = service(tmp_path, launcher)
    backtests.set_keep(True, 10)
    ticks = []

    async def scenario():
        async def tick(seconds: float) -> None:
            ticks.append(seconds)
            await asyncio.sleep(0)

        keeper = asyncio.create_task(backtests.keep_prices(tick))
        try:
            await until(lambda: len(launcher.commands) == 1)
        finally:
            keeper.cancel()
            await backtests.close()

    asyncio.run(scenario())

    assert ticks[0] == 60
    assert launcher.commands[0][0] == "src.strategy.history.dukascopy"


# ── what WATA really did: its signals and its trades ──


def at(clock_time: str, day: date = DAY) -> datetime:
    return datetime.fromtimestamp(CLOCK.epoch(day, clock_time[:5]) + int(clock_time[6:] or 0), timezone.utc)


def signal(action: str, decided: str, outcome: str = "executed", **more) -> dict:
    return {
        "signal_uuid": f"{action}-{decided}", "action": action, "indice": "us100", "confidence": 1.2,
        "signal_timestamp": at(decided), "received_timestamp": at(decided), "decided_at": at(decided),
        "outcome": outcome, "reason": None, "position_id": None, **more,
    }


def position(position_id: str, action: str, opened: str, closed: str, bought: float, sold: float, reason: str, **more) -> dict:
    return {
        "position_id": position_id, "action": action,
        "execution_time_open": at(opened), "execution_time_close": at(closed),
        "position_open_price": bought, "position_close_price": sold,
        "position_total_performance_percent": None if sold is None else round(sold * 100 / bought - 100, 2),
        "position_max_performance_percent": None, "position_close_reason": reason, "instrument_name": "TURBO", **more,
    }


JOURNAL = [
    signal("short", "08:01:05", position_id="p1"),
    signal("long", "08:20:00", "refused", reason="Day profit reached"),
    # The scheduler's end of day names no indice.
    signal("close-position", "21:55:00", "ignored", indice="n/a", confidence=None),
    # Another indice, and what is not an order to the trader.
    signal("long", "09:00:00", indice="germany40", position_id="p9"),
    signal("daily_stats", "22:00:00", indice="n/a"),
]
POSITIONS = [
    position("p1", "short", "08:01:06", "08:02:30", 4.40, 4.48, "Performance (Daily profit target reached)"),
    position("p9", "long", "09:00:01", "09:30:00", 5.00, 5.10, "Performance (Daily profit target reached)"),
    # Before there was a journal: kept, the indice cannot be told.
    position("p0", "long", "08:00:00", "08:30:00", 4.00, 3.40, "Performance (Stoploss (-15%) hit at -15.0%)"),
    position("open", "long", "10:00:00", "10:30:00", 4.00, None, None, execution_time_close=None),
]


def test_the_journal_becomes_the_signals_to_replay():
    replayed = signals_to_replay(JOURNAL, "us100")

    assert [(entry["at"], entry["action"], entry["outcome"]) for entry in replayed] == [
        ("2026-10-12T06:01:05Z", "short", "executed"),
        ("2026-10-12T06:20:00Z", "long", "refused"),
        ("2026-10-12T19:55:00Z", "close-position", "ignored"),
    ]
    assert (replayed[0]["confidence"], replayed[0]["position_id"], replayed[1]["reason"]) == (1.2, "p1", "Day profit reached")
    # Dealt with at no known moment: the time it was received, then the one it was sent with.
    late = signal("long", "08:00:00", decided_at=None, received_timestamp="2026-10-12T06:00:03Z")
    assert signals_to_replay([late], "US100")[0]["at"] == "2026-10-12T06:00:03Z"
    assert signals_to_replay([{**late, "received_timestamp": None, "signal_timestamp": None}], "us100") == []


def test_closed_positions_become_the_real_trades_of_the_indice():
    trades = real_trades_of(POSITIONS, JOURNAL, "us100")

    assert [trade["position_id"] for trade in trades] == ["p0", "p1"]
    assert trades[1] == {
        "position_id": "p1", "direction": "short", "opened_at": "2026-10-12T06:01:06Z", "closed_at": "2026-10-12T06:02:30Z",
        "open_price": 4.4, "close_price": 4.48, "performance_percent": 1.82, "max_performance_percent": None,
        "close_reason": "Performance (Daily profit target reached)", "instrument_name": "TURBO",
    }


def test_a_replay_of_real_signals_is_a_run_kept_with_the_signals_and_the_real_trades(tmp_path):
    launcher = FakeLauncher()
    backtests = service(tmp_path, launcher)

    @worked
    async def scenario(backtests):
        run = backtests.create_from_signals(
            first_day=DAY, last_day=DAY, journal=JOURNAL, positions=POSITIONS, rules={"day_profit_target": 3.0}, note="real",
        )
        await until(lambda: state_of(backtests, run["id"]) == DONE)
        return run

    run = scenario(backtests)
    directory = tmp_path / "runs" / run["id"]

    assert (run["kind"], run["strategy"], run["parameters"], run["note"]) == ("signals", "signals", {}, "real")
    assert (run["signals"], run["real_trades"], run["rules"]) == (3, 2, {"day_profit_target": 3.0})
    assert [entry["action"] for entry in json.loads((directory / "signals.json").read_text())] == ["short", "long", "close-position"]
    assert [trade["position_id"] for trade in json.loads((directory / "real_trades.json").read_text())] == ["p0", "p1"]
    command = launcher.commands[0]
    assert command[-2:] == ["--signals", str(directory / "signals.json")]
    assert "--strategy" not in command and "--set" not in command


def test_a_period_without_an_entry_signal_has_nothing_to_replay(tmp_path):
    backtests = service(tmp_path)

    with pytest.raises(NotPossible, match="no entry signal in that period"):
        backtests.create_from_signals(first_day=DAY, last_day=DAY, journal=JOURNAL[2:], positions=POSITIONS)
    with pytest.raises(InvalidRequest, match="ends before it starts"):
        backtests.create_from_signals(first_day=DAY, last_day=date(2026, 10, 11), journal=JOURNAL, positions=POSITIONS)
    assert os.listdir(tmp_path / "runs") == []


def test_real_signals_through_the_real_command_are_set_beside_what_wata_did(tmp_path):
    _one_day_of_prices(tmp_path / "prices")
    backtests = BacktestService(
        runs_root=str(tmp_path / "runs"), prices_root=str(tmp_path / "prices"), rules=lambda: RULES,
    )
    os.makedirs(tmp_path / "runs")

    @worked
    async def scenario(backtests):
        run = backtests.create_from_signals(first_day=DAY, last_day=DAY, journal=JOURNAL, positions=POSITIONS, leverages=[110])
        await until(lambda: state_of(backtests, run["id"]) in (DONE, FAILED), seconds=30)
        return backtests.get(run["id"])

    run = scenario(backtests)

    assert run["status"]["state"] == DONE, run["output"]
    assert run["summary"]["strategy"] == "signals"
    # The end of day came after the prices of this day stop.
    assert run["summary"]["signals"] == {"replayed": 2, "without_prices": ["2026-10-12T19:55:00Z"]}
    (trade,) = backtests.rows(run["id"], "trades", 110.0)
    # The short is entered at the first ten seconds after the signal, and the daily target sells it.
    assert (trade["entry_time"], trade["direction"], trade["entry_reason"], trade["exit_reason"]) == (
        "2026-10-12T06:01:10Z", "short", "signal", "Daily profit target",
    )
    assert [refusal["reason"] for refusal in backtests.rows(run["id"], "refusals", 110.0)] == ["Day profit reached"]

    compared = backtests.comparison(run["id"], 110.0)

    assert compared["journal"] == {"executed": 1, "refused": 1, "ignored": 1}
    assert compared["summary"] == {
        "real_trades": 2, "model_trades": 1, "paired": 1,
        # The model sold at its target of 1.7 %, WATA at 1.82 %.
        "average_gap": -0.12, "median_gap": 0.12, "ninth_gap": 0.12, "same_exit": 1,
        "real_total": -13.45, "model_total": 1.7, "days": 1, "median_day_gap": 15.15,
    }
    (pair,) = compared["pairs"]
    assert (pair["real"]["reference"], pair["real"]["exit"], pair["model"]["exit"], pair["gap"]) == (
        "p1", "Daily profit target", "Daily profit target", -0.12,
    )
    # The trade from before the journal has no signal to be replayed from.
    assert [leg["reference"] for leg in compared["only_real"]] == ["p0"]
    assert compared["only_model"] == []

    chart = backtests.day_chart(run["id"], DAY, 110.0)
    # The signals themselves, on the bars of the strategy TradingView runs.
    assert [(decision["target"], decision["reason"]) for decision in chart["decisions"]] == [
        ("short", "signal"), ("long", "signal"), ("flat", "close-position"),
    ]
    assert len(chart["bars"]) == 136 and chart["trades"] == [trade]


def test_a_backtest_of_a_strategy_has_no_real_trades_to_be_compared_with(tmp_path):
    backtests = service(tmp_path)
    run = ask(backtests, leverages=[110])

    with pytest.raises(NotPossible, match="did not replay real signals"):
        backtests.comparison(run["id"], 110.0)


# ── the turbo measured on real trades ──

MEASURED = {
    "trades": 2, "used": 2, "without_prices": 0, "unreadable": 0, "set_aside": 0, "fitted": True, "reason": None,
    "sample": "too_few", "entry_price": 5.1, "entry_price_low": 4.2, "entry_price_high": 7.9,
    "points_per_unit": 104.2, "points_per_unit_low": 96.5, "points_per_unit_high": 113.3, "level": 90,
    "spread": 0.04, "median_error": 0.3, "ninth_error": 1.1,
}


def test_the_turbo_is_measured_on_the_closed_trades_by_a_command_of_its_own(tmp_path):
    launcher, published = FakeLauncher(ends_with=None, measured=MEASURED), []
    backtests = service(tmp_path, launcher, published)

    @worked
    async def scenario(backtests):
        before = backtests.calibration()
        started = backtests.start_calibration(POSITIONS, JOURNAL)
        await until(lambda: len(launcher.commands) == 1)
        with pytest.raises(NotPossible, match="already being measured"):
            backtests.start_calibration(POSITIONS, JOURNAL)
        launcher.finish(0)
        await until(lambda: backtests.calibration()["status"]["state"] != RUNNING)
        return before, started, backtests.calibration()

    before, started, ended = scenario(backtests)
    folder = tmp_path / "runs" / "_calibration"

    assert before == {
        "status": {"state": "idle"}, "result": None, "lines": [],
        "turbo": {"entry_price": 4.4, "spread": 0.03, "leverages": [80.0, 110.0, 140.0], "measured": False},
    }
    assert started["status"] == {"state": RUNNING, "trades": 2, "started_at": "2026-10-12T21:30:45Z"}
    assert launcher.commands == [[
        "src.strategy.calibration", "--trades", str(folder / "trades.json"),
        "--root", str(tmp_path / "prices"), "--indice", "us100",
        "--out", str(folder / "measured.json"), "--low-priority",
    ]]
    # The trades of the indice that were bought and sold: what the command reads.
    assert [(trade["position_id"], trade["open_price"], trade["close_price"]) for trade in json.loads((folder / "trades.json").read_text())] == [
        ("p0", 4.0, 3.4), ("p1", 4.4, 4.48),
    ]
    assert (ended["status"]["state"], ended["status"]["error"]) == (DONE, None)
    assert ended["result"] == {**MEASURED, "measured_at": "2026-10-12T21:30:45Z"}
    assert published == [("calibration_finished", {"state": DONE})]


def test_backtests_are_run_with_the_turbo_that_was_measured(tmp_path):
    launcher = FakeLauncher(measured=MEASURED)
    backtests = service(tmp_path, launcher)

    @worked
    async def scenario(backtests):
        backtests.start_calibration(POSITIONS, JOURNAL)
        await until(lambda: backtests.calibration()["status"]["state"] == DONE)
        return backtests.options()["turbo"], ask(backtests)["turbo"], ask(backtests, leverages=[90], entry_price=6)["turbo"]

    offered, by_default, chosen = scenario(backtests)

    # The leverage measured, between the two ends of the range it could be in.
    assert offered == {"entry_price": 5.1, "spread": 0.04, "leverages": [96.5, 104.2, 113.3], "measured": True}
    assert by_default == {"entry_price": 5.1, "spread": 0.04, "leverages": [96.5, 104.2, 113.3]}
    assert chosen == {"entry_price": 6, "spread": 0.04, "leverages": [90.0]}


@pytest.mark.parametrize("found, leverages", [
    # Pinned down by the trades: one column is enough.
    ({"points_per_unit_low": 103.0, "points_per_unit_high": 105.0}, [104.2]),
    # Not bounded above by the trades there are.
    ({"points_per_unit_high": None}, [96.5, 104.2]),
])
def test_the_leverages_offered_follow_what_the_trades_can_tell(tmp_path, found, leverages):
    os.makedirs(tmp_path / "runs" / "_calibration")
    (tmp_path / "runs" / "_calibration" / "result.json").write_text(json.dumps({**MEASURED, **found}))

    assert service(tmp_path).default_turbo()["leverages"] == leverages


def test_a_measure_that_finds_no_turbo_leaves_the_one_assumed(tmp_path):
    launcher = FakeLauncher(measured={**MEASURED, "fitted": False, "reason": "2 trade(s) with prices: 5 at least are needed"})
    backtests = service(tmp_path, launcher)

    @worked
    async def scenario(backtests):
        backtests.start_calibration(POSITIONS, JOURNAL)
        await until(lambda: backtests.calibration()["status"]["state"] == DONE)
        return backtests.calibration()

    measured = scenario(backtests)

    assert measured["result"]["reason"] == "2 trade(s) with prices: 5 at least are needed"
    assert measured["turbo"] == {"entry_price": 4.4, "spread": 0.03, "leverages": [80.0, 110.0, 140.0], "measured": False}


def test_a_measure_that_fails_says_so_and_keeps_the_last_one(tmp_path):
    launcher = FakeLauncher(measured=MEASURED)
    backtests = service(tmp_path, launcher)

    @worked
    async def scenario(backtests):
        backtests.start_calibration(POSITIONS, JOURNAL)
        await until(lambda: backtests.calibration()["status"]["state"] == DONE)
        launcher.ends_with = 2
        backtests.start_calibration(POSITIONS, JOURNAL)
        await until(lambda: backtests.calibration()["status"]["state"] == FAILED)
        return backtests.calibration()

    measured = scenario(backtests)

    assert (measured["status"]["error"], measured["lines"]) == ("started", ["started"])
    assert measured["result"]["points_per_unit"] == 104.2 and measured["turbo"]["measured"] is True


def test_without_a_closed_trade_there_is_nothing_to_measure_the_turbo_on(tmp_path):
    backtests = service(tmp_path)

    async def scenario():
        with pytest.raises(NotPossible, match="no closed trade yet"):
            backtests.start_calibration(POSITIONS[3:], JOURNAL)

    asyncio.run(scenario())
    assert backtests.calibration()["status"] == {"state": "idle"}


# ── breakdowns, and one trade followed ──


def _run_with_trades(tmp_path, lines: list[str]) -> tuple[BacktestService, str]:
    backtests = service(tmp_path)
    run = ask(backtests, leverages=[100], entry_price=4.0, spread=0)
    (tmp_path / "runs" / run["id"] / "trades_100.csv").write_text(
        "entry_time,exit_time,direction,entry_reason,confidence,entry_index,exit_index,index_points,"
        "performance_percent,max_performance_percent,exit_reason,min_performance_percent,scale_percent,stake,profit_loss\n"
        + "\n".join(lines) + "\n"
    )
    return backtests, run["id"]


def test_the_trades_of_a_run_are_broken_down_by_what_could_explain_them(tmp_path):
    backtests, run_id = _run_with_trades(tmp_path, [
        # Monday 12 October, 09:00 in Paris: a long on a cross, three minutes, sold at the target.
        "2026-10-12T07:00:00Z,2026-10-12T07:03:00Z,long,cross,1.6,30000,30007,7,1.7,2.1,Daily profit target,-0.4,100.0,996.0,16.93",
        # The same hour on Tuesday: a short that had been at +3 %, stopped twenty minutes later.
        "2026-10-13T07:30:00Z,2026-10-13T07:50:00Z,short,catch-up,0.4,30000,30060,-60,-15.0,3.0,Stoploss,-15.0,50.0,500.0,-75.0",
        # Tuesday at 16:00: a long without a confidence, ended by the strategy after two hours.
        "2026-10-13T14:00:00Z,2026-10-13T16:00:00Z,long,cross,,30000,30012,12,3.0,4.0,Signal: reverse cross,-1.0,75.0,750.0,22.5",
    ])

    broken = backtests.breakdowns(run_id, 100.0)

    assert broken["timezone"] == "Europe/Paris"
    assert broken["summary"] == {
        "trade_count": 3, "win_count": 2, "loss_count": 1, "win_rate": 66.67, "profit_loss": -35.57,
        "average_percent": -3.43, "average_best_percent": 3.03,
        # What was lost of the best level by the close, and the one that was in profit before it lost.
        "average_given_back": 6.47, "turned_negative_count": 1, "turned_negative_percent": 33.33,
        "total_percent": -10.3,
    }
    groups = broken["groups"]
    assert [(group["group"], group["trade_count"], group["win_rate"], group["total_percent"]) for group in groups["hour"]] == [
        (9, 2, 50.0, -13.3), (16, 1, 100.0, 3.0),
    ]
    assert [(group["group"], group["trade_count"], group["average_percent"]) for group in groups["weekday"]] == [
        ("Monday", 1, 1.7), ("Tuesday", 2, -6.0),
    ]
    assert [(group["group"], group["trade_count"], group["profit_loss"]) for group in groups["direction"]] == [("long", 2, 39.43), ("short", 1, -75.0)]
    assert [(group["group"], group["trade_count"]) for group in groups["entry_reason"]] == [("catch-up", 1), ("cross", 2)]
    assert [(group["group"], group["trade_count"]) for group in groups["confidence"]] == [("Under 0.5", 1), ("1.5 and above", 1), ("Not given", 1)]
    assert [(group["group"], group["trade_count"]) for group in groups["holding_time"]] == [("Under 5 min", 1), ("15 min to 1 h", 1), ("1 to 4 h", 1)]
    assert [group["group"] for group in groups["exit"]] == ["Daily profit target", "Signal: reverse cross", "Stoploss"]


def test_a_run_without_a_trade_has_empty_breakdowns(tmp_path):
    backtests, run_id = _run_with_trades(tmp_path, [])

    broken = backtests.breakdowns(run_id, 100.0)

    assert (broken["summary"]["trade_count"], broken["summary"]["win_rate"], broken["summary"]["average_given_back"]) == (0, None, None)
    assert all(groups == [] for groups in broken["groups"].values())


def _minute_of_prices(prices_root, closes: list[float]) -> None:
    """Ten-second bars from 09:00 in Paris on the Monday, each from the close before to its own, without spread."""
    start = CLOCK.epoch(DAY, "09:00")
    bars = []
    for number, (opened, closed) in enumerate(zip([30000.0, *closes], closes)):
        high, low = max(opened, closed), min(opened, closed)
        bars.append(Bar("index", start + 10 * number, opened, high, low, closed, opened, high, low, closed, 3, 0))
    write_day_file(dukascopy.day_path(str(prices_root), "us100", DAY), bars, symbol="TEST", label="us100")


def test_a_trade_is_followed_as_the_monitor_lived_it_with_the_levels_that_could_sell_it(tmp_path):
    # A long at 30,000 on a turbo at 4.00 for 100 points: 4 points are 1 %.
    _minute_of_prices(tmp_path / "prices", [30008.0, 30024.0, 30012.0, 30016.0, 30000.0])
    backtests, run_id = _run_with_trades(tmp_path, [
        # An earlier trade of the day that lost 5 %: the day's target is further for the next one.
        "2026-10-12T06:00:00Z,2026-10-12T06:30:00Z,long,cross,,30000,29980,-20,-5.0,0.0,Signal: reverse cross,-5.0,100.0,,",
        "2026-10-12T07:00:00Z,2026-10-12T07:00:40Z,long,cross,1.2,30000,30000,0,0.0,6.0,Signal: reverse cross,0.0,100.0,,",
    ])

    path = backtests.trade_path(run_id, 100.0, "2026-10-12T07:00:00Z")

    start = CLOCK.epoch(DAY, "09:00")
    assert path["points"] == [
        [start, 2.0, 0.0, 2.0, None],
        # +6 %: the trailing stop is armed, eight points under the best level seen.
        [start + 10, 6.0, 2.0, 6.0, -2.0],
        [start + 20, 3.0, 3.0, 6.0, -2.0],
        [start + 30, 4.0, 3.0, 4.0, -2.0],
        # Sold at the open of these ten seconds, at +4 % less nothing: where they went after is not shown.
        [start + 40, 0.0, 0.0, 4.0, -2.0],
    ]
    # 0.95 x 1.0705 = 1.017: after a loss of 5 %, the day's target of 1.7 % asks 7.05 % of this trade.
    assert (path["day_before_percent"], path["levels"]["day_target"], path["levels"]["stop"]) == (-5.0, 7.05, -15)


def test_a_long_trade_is_given_in_no_more_points_than_a_chart_can_take(tmp_path, monkeypatch):
    monkeypatch.setattr("src.admin.backtests.MAX_PATH_POINTS", 3)
    _minute_of_prices(tmp_path / "prices", [30004.0, 30008.0, 29996.0, 30012.0, 30016.0, 30020.0, 30000.0])
    backtests, run_id = _run_with_trades(tmp_path, [
        "2026-10-12T07:00:00Z,2026-10-12T07:01:00Z,long,cross,1.2,30000,30000,0,0.0,5.0,Signal: reverse cross,-1.0,100.0,,",
    ])

    points = backtests.trade_path(run_id, 100.0, "2026-10-12T07:00:00Z")["points"]

    start = CLOCK.epoch(DAY, "09:00")
    # Seven bars in threes: each group keeps its first moment, its last result, its worst and its best.
    assert [(point[0], point[1], point[2], point[3]) for point in points] == [
        (start, -1.0, -1.0, 2.0), (start + 30, 5.0, -1.0, 5.0), (start + 60, 0.0, 0.0, 5.0),
    ]


# ── sweeps ──

AXES = [
    {"kind": "rule", "name": "day_profit_target", "values": [1, 1.7, 3]},
    {"kind": "parameter", "name": "kernel_bandwidth", "values": ["8", 15]},
]


def sweep_of(backtests: BacktestService, **changes) -> dict:
    return backtests.create_sweep(**{"first_day": date(2026, 9, 14), "last_day": date(2026, 10, 9), "axes": AXES, **changes})


def test_a_sweep_is_a_run_of_its_own_command_with_the_values_to_try(tmp_path):
    launcher = FakeLauncher()
    backtests = service(tmp_path, launcher)

    @worked
    async def scenario(backtests):
        run = sweep_of(backtests, parameters={"entry_mode": "cross_catchup"}, rules={"stoploss_percent": -10.0}, held_out=0.4, slippage=1, note="targets")
        await until(lambda: state_of(backtests, run["id"]) == DONE)
        return run, backtests.get(run["id"])

    asked, run = scenario(backtests)
    directory = str(tmp_path / "runs" / run["id"])

    assert (asked["kind"], asked["replays"], asked["strategy"], asked["held_out"], asked["note"]) == ("sweep", "strategy", "kernel_vwap", 0.4, "targets")
    # Each value as it will be read, and one turbo: the middle one of those a backtest is made with.
    assert asked["axes"] == [
        {"kind": "rule", "name": "day_profit_target", "values": [1.0, 1.7, 3.0]},
        {"kind": "parameter", "name": "kernel_bandwidth", "values": [8, 15]},
    ]
    assert asked["turbo"] == {"entry_price": 4.4, "spread": 0.03, "leverages": [110.0]}
    assert asked["execution"] == {"capital": 0.0, "fill_delay": 0, "slippage": 1.0}
    assert launcher.commands == [[
        "src.strategy.sweep",
        "--from", "2026-09-14", "--to", "2026-10-09",
        "--root", str(tmp_path / "prices"), "--indice", "us100",
        "--rules", os.path.join(directory, "rules.json"),
        "--points-per-unit", "110", "--entry-price", "4.4", "--spread", "0.03",
        "--fill-delay", "0", "--slippage", "1", "--held-out", "0.4",
        "--out", directory, "--progress", os.path.join(directory, "progress.json"),
        "--low-priority",
        "--axis", "rule:day_profit_target=1.0,1.7,3.0", "--axis", "parameter:kernel_bandwidth=8,15",
        "--strategy", "kernel_vwap", "--set", "entry_mode=cross_catchup",
    ]]
    # Done when the command wrote what it found; a sweep has no figures of one run to head a list with.
    assert (run["status"]["state"], run["headline"], run["summary"]) == (DONE, None, None)
    assert len(run["sweep"]["cells"]) == 6
    assert "sweep" not in backtests.list_runs()[0]


@pytest.mark.parametrize("changes, message", [
    ({"axes": []}, "one setting or to two"),
    ({"axes": AXES + [{"kind": "rule", "name": "stoploss_percent", "values": [-5, -10]}]}, "one setting or to two"),
    ({"axes": [{"kind": "rule", "name": "day_profit_target", "values": [1.7]}]}, "Give 2 to 12 values for 'day_profit_target'"),
    ({"axes": [{"kind": "rule", "name": "day_profit_target", "values": list(range(1, 14))}]}, "Give 2 to 12 values"),
    ({"axes": [{"kind": "rule", "name": "trading_end_hour", "values": [21, 22]}]}, "cannot be changed for a backtest"),
    ({"axes": [{"kind": "rule", "name": "day_profit_target", "values": [1, "lots"]}]}, "needs a number"),
    ({"axes": [{"kind": "parameter", "name": "kernel_bandwidth", "values": [8, 100000]}]}, "between 2 and 500"),
    ({"axes": [{"kind": "parameter", "name": "kernel_bandwidth", "values": [8, "8"]}]}, "are not all different"),
    ({"axes": [AXES[0], AXES[0]]}, "are the same one"),
    ({"axes": [{"kind": "turbo", "name": "spread", "values": [0.01, 0.05]}]}, "a parameter of the strategy or to a rule"),
    ({"axes": [
        {"kind": "rule", "name": "day_profit_target", "values": list(range(1, 10))},
        {"kind": "rule", "name": "stoploss_percent", "values": [-value for value in range(1, 9)]},
    ]}, "72 combinations: 64 at most"),
    ({"held_out": 0.05}, "Between 10% and 50% of the days can be held out"),
    ({"held_out": 0.6}, "Between 10% and 50%"),
    ({"strategy": "martingale"}, "Unknown strategy"),
    ({"first_day": date(2026, 10, 9), "last_day": date(2026, 10, 8)}, "ends before it starts"),
])
def test_a_sweep_that_makes_no_sense_is_refused_saying_why(tmp_path, changes, message):
    backtests = service(tmp_path)

    with pytest.raises(InvalidRequest, match=message):
        sweep_of(backtests, **changes)
    assert os.listdir(tmp_path / "runs") == []


def test_one_combination_of_a_sweep_is_made_a_backtest_of_its_own(tmp_path):
    launcher = FakeLauncher()
    backtests = service(tmp_path, launcher)

    @worked
    async def scenario(backtests):
        swept = sweep_of(backtests, parameters={"entry_mode": "cross_catchup"}, rules={"stoploss_percent": -10.0}, slippage=1, note="targets")
        with pytest.raises(NotPossible, match="Only a combination of a finished sweep"):
            backtests.run_cell(swept["id"], 0)
        await until(lambda: state_of(backtests, swept["id"]) == DONE)
        with pytest.raises(InvalidRequest, match="no such combination"):
            backtests.run_cell(swept["id"], 6)
        return backtests.run_cell(swept["id"], 3)

    run = scenario(backtests)

    # The fourth combination: a target of 1.7 % with a bandwidth of 15, which is the strategy's default.
    assert (run["kind"], run["from"], run["to"], run["note"]) == ("strategy", "2026-09-14", "2026-10-09", "targets")
    assert run["parameters"] == {"entry_mode": "cross_catchup"}
    assert run["rules"] == {"stoploss_percent": -10.0}
    assert (run["turbo"]["leverages"], run["execution"]) == ([110.0], {"capital": 10000.0, "fill_delay": 0, "slippage": 1.0})
    with pytest.raises(NotPossible, match="Only a combination of a finished sweep"):
        backtests.run_cell(run["id"], 0)


def test_rules_are_swept_over_the_signals_wata_received_and_a_combination_replays_them_again(tmp_path):
    launcher = FakeLauncher()
    backtests = service(tmp_path, launcher)

    @worked
    async def scenario(backtests):
        swept = sweep_of(backtests, first_day=DAY, last_day=DAY, axes=AXES[:1], journal=JOURNAL, positions=POSITIONS)
        await until(lambda: state_of(backtests, swept["id"]) == DONE)
        return swept, backtests.run_cell(swept["id"], 2)

    swept, run = scenario(backtests)

    assert (swept["replays"], swept["strategy"], swept["parameters"]) == ("signals", "signals", {})
    assert launcher.commands[0][-2:] == ["--signals", str(tmp_path / "runs" / swept["id"] / "signals.json")]
    assert "--strategy" not in launcher.commands[0]
    # The combination replays the same signals, and keeps the same real trades to be compared with.
    assert (run["kind"], run["rules"], run["signals"], run["real_trades"]) == ("signals", {"day_profit_target": 3.0}, 3, 2)
    kept = tmp_path / "runs" / run["id"]
    assert json.loads((kept / "signals.json").read_text()) == json.loads((tmp_path / "runs" / swept["id"] / "signals.json").read_text())
    assert [trade["position_id"] for trade in json.loads((kept / "real_trades.json").read_text())] == ["p0", "p1"]

    with pytest.raises(InvalidRequest, match="Signals have no strategy"):
        sweep_of(backtests, axes=AXES, journal=JOURNAL, positions=POSITIONS)
    with pytest.raises(NotPossible, match="no entry signal in that period"):
        sweep_of(backtests, axes=AXES[:1], journal=JOURNAL[2:], positions=POSITIONS)


def test_a_sweep_through_the_real_command_holds_the_last_days_out(tmp_path):
    for day in (DAY, date(2026, 10, 13), date(2026, 10, 14)):
        night = [30000.0] + _leg(30000, 30060) + _leg(30060, 30000) + _leg(30000, 30060) + _leg(30060, 30000)
        session = [30000.0] + _leg(30000, 29800) + _leg(29800, 30200) + [30200.0] * 6
        bars = _recorded(CLOCK.epoch(day, "07:00"), night) + _recorded(CLOCK.epoch(day, "08:00"), session)
        write_day_file(dukascopy.day_path(str(tmp_path / "prices"), "us100", day), bars, symbol="TEST", label="us100")
    backtests = BacktestService(runs_root=str(tmp_path / "runs"), prices_root=str(tmp_path / "prices"), rules=lambda: RULES)
    os.makedirs(tmp_path / "runs")

    @worked
    async def scenario(backtests):
        run = backtests.create_sweep(
            first_day=DAY, last_day=date(2026, 10, 14), held_out=0.3,
            axes=[{"kind": "rule", "name": "day_profit_target", "values": [1.7, 3]}, {"kind": "rule", "name": "trailing_enabled", "values": [True, False]}],
        )
        await until(lambda: state_of(backtests, run["id"]) in (DONE, FAILED), seconds=30)
        return backtests.get(run["id"])

    run = scenario(backtests)

    assert run["status"]["state"] == DONE, run["output"]
    swept = run["sweep"]
    assert swept["held_out"] == {"share": 0.3, "from": "2026-10-14", "days": 1, "tuning_days": 2}
    assert [cell["values"] for cell in swept["cells"]] == [[1.7, True], [1.7, False], [3.0, True], [3.0, False]]
    assert [(cell["tuning"]["total_percent"], cell["held_out"]["total_percent"]) for cell in swept["cells"]] == [
        (3.43, 1.7), (3.43, 1.7), (6.09, 3.0), (6.09, 3.0),
    ]
    assert run["stale"] == {"code": False, "prices": False, "version": swept["code"]["version"]}


# ── housekeeping ──


def test_a_run_is_named_again_and_tagged_without_touching_what_it_was_asked(tmp_path):
    backtests = service(tmp_path)
    run = ask(backtests, parameters={"entry_mode": "cross_catchup"}, note="first")

    renamed = backtests.update(run["id"], note="catch-up, September", tags=[" keep ", "september", "keep", ""])

    assert (renamed["note"], renamed["tags"], renamed["parameters"]) == ("catch-up, September", ["keep", "september"], {"entry_mode": "cross_catchup"})
    # One of the two alone leaves the other as it was.
    assert backtests.update(run["id"], tags=[])["note"] == "catch-up, September"
    assert backtests.update(run["id"], note="")["tags"] == []
    for changes, message in [
        ({"note": "x" * 201}, "longer than 200 characters"),
        ({"tags": [str(number) for number in range(7)]}, "6 tags at most"),
        ({"tags": ["x" * 25]}, "of 24 characters at most"),
    ]:
        with pytest.raises(InvalidRequest, match=message):
            backtests.update(run["id"], **changes)
    with pytest.raises(UnknownRun):
        backtests.update("20261012-213045-ffffff", note="none")


def test_several_runs_are_removed_at_once_and_those_in_progress_are_left(tmp_path):
    launcher = FakeLauncher(ends_with=None)
    backtests = service(tmp_path, launcher)

    @worked
    async def scenario(backtests):
        first, second, third = ask(backtests), ask(backtests), ask(backtests)
        await until(lambda: len(launcher.processes) == 1)
        launcher.finish(0)
        await until(lambda: state_of(backtests, first["id"]) == DONE and len(launcher.processes) == 2)
        return first, second, third, backtests.delete_many([first["id"], second["id"], third["id"], "..", "20261012-213045-ffffff"])

    first, second, third, outcome = scenario(backtests)

    assert outcome["removed"] == [first["id"]]
    assert outcome["kept"] == [
        {"id": second["id"], "reason": "Stop this backtest before removing it"},
        {"id": third["id"], "reason": "Stop this backtest before removing it"},
        {"id": "..", "reason": "No such backtest"},
        {"id": "20261012-213045-ffffff", "reason": "No such backtest"},
    ]
    assert sorted(os.listdir(tmp_path / "runs")) == sorted([second["id"], third["id"]])


def test_the_disk_taken_by_the_runs_and_the_prices_is_counted(tmp_path):
    _one_day_of_prices(tmp_path / "prices")
    _keep_hours(tmp_path / "prices", DAY, range(3))
    raw = dukascopy.raw_path(str(tmp_path / "prices"), dukascopy.SYMBOL, dukascopy.day_hours(DAY)[0])
    with open(raw, "wb") as out_file:
        out_file.write(b"x" * 1000)
    backtests = service(tmp_path)
    assert backtests.storage()["runs"] == {"count": 0, "bytes": 0}
    run = ask(backtests)

    used = backtests.storage()

    assert used["runs"] == {"count": 1, "bytes": backtests.get(run["id"])["bytes"]}
    assert (used["prices"]["days"], used["prices"]["fetched_bytes"]) == (1, 1000)
    assert used["prices"]["bars_bytes"] == os.path.getsize(dukascopy.day_path(str(tmp_path / "prices"), "us100", DAY))
