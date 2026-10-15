"""Backtests on the days the Capital.com feed recorded, chosen in place of Dukascopy's history."""

import json
import os
from datetime import date, datetime, timedelta, timezone

import pytest

from src.admin.backtests import DONE, FAILED, BacktestService, InvalidRequest, NotPossible
from src.capital_feed.day_files import bars_directory, day_path, write_day_file
from src.strategy.history import dukascopy
from tests.test_admin_backtests import (
    CLOCK,
    DAY,
    RULES,
    FakeLauncher,
    _leg,
    _option,
    _recorded,
    service,
    state_of,
    until,
    worked,
)


def _rows(bars):
    return [
        (datetime.fromtimestamp(bar.start, timezone.utc), bar.bid_open, bar.bid_high, bar.bid_low, bar.bid_close,
         bar.ask_open, bar.ask_high, bar.ask_low, bar.ask_close, bar.updates)
        for bar in bars
    ]


def _recorded_day(prices_root, day: date = DAY) -> None:
    """The day of the other tests, as the Capital.com feed would have written it once over."""
    night = [30000.0] + _leg(30000, 30060) + _leg(30060, 30000) + _leg(30000, 30060) + _leg(30060, 30000)
    session = [30000.0] + _leg(30000, 29800) + _leg(29800, 30200) + [30200.0] * 6
    bars = _recorded(CLOCK.epoch(day, "07:00"), night) + _recorded(CLOCK.epoch(day, "08:00"), session)
    write_day_file(day_path(str(prices_root), "us100", day), _rows(bars), epic="US100", label="us100", name="US Tech 100")


def test_a_run_on_the_recorded_days_reads_their_folder_and_says_so(tmp_path):
    _recorded_day(tmp_path / "prices")
    launcher = FakeLauncher(ends_with=None)
    backtests = service(tmp_path, launcher)

    @worked
    async def scenario(backtests):
        run = backtests.create(first_day=DAY, last_day=DAY, prices="capital")
        await until(lambda: launcher.commands)
        return run

    run = scenario(backtests)

    assert run["prices"] == "capital"
    (command,) = launcher.commands
    assert _option(command, "--source") == bars_directory(str(tmp_path / "prices"), "us100")
    assert _option(command, "--indice") == "us100" and "--root" not in command
    assert json.load(open(tmp_path / "runs" / run["id"] / "request.json"))["prices"] == "capital"


def test_a_period_without_a_recorded_day_is_refused_before_it_is_queued(tmp_path):
    _recorded_day(tmp_path / "prices")
    backtests = service(tmp_path)

    with pytest.raises(NotPossible, match="No day of that period was recorded from Capital.com"):
        backtests.create(first_day=date(2026, 9, 1), last_day=date(2026, 9, 30), prices="capital")
    with pytest.raises(InvalidRequest, match="dukascopy or capital"):
        backtests.create(first_day=DAY, last_day=DAY, prices="saxo")
    assert os.listdir(tmp_path / "runs") == []


def test_a_run_asked_for_before_there_was_a_choice_is_one_of_dukascopy(tmp_path):
    launcher = FakeLauncher(ends_with=None)
    backtests = service(tmp_path, launcher)

    @worked
    async def scenario(backtests):
        run = backtests.create(first_day=DAY, last_day=DAY)
        request_path = tmp_path / "runs" / run["id"] / "request.json"
        request = json.load(open(request_path))
        del request["prices"]
        json.dump(request, open(request_path, "w"))
        await until(lambda: launcher.commands)
        return backtests.get(run["id"])

    run = scenario(backtests)

    assert run["prices"] == "dukascopy"
    (command,) = launcher.commands
    assert _option(command, "--root") == str(tmp_path / "prices") and "--source" not in command


def test_a_sweep_on_the_recorded_days_reads_them_and_its_combinations_too(tmp_path):
    _recorded_day(tmp_path / "prices")
    launcher = FakeLauncher()
    backtests = service(tmp_path, launcher)

    @worked
    async def scenario(backtests):
        swept = backtests.create_sweep(
            first_day=DAY, last_day=DAY, axes=[{"kind": "rule", "name": "day_profit_target", "values": [1, 2]}],
            prices="capital",
        )
        await until(lambda: state_of(backtests, swept["id"]) == DONE)
        return backtests.run_cell(swept["id"], 1)

    cell = scenario(backtests)

    assert cell["prices"] == "capital"
    sweep_command = launcher.commands[0]
    assert sweep_command[0] == "src.strategy.sweep" and _option(sweep_command, "--source").endswith(os.path.join("capital", "bars", "us100"))


def test_the_days_recorded_are_said_ready_and_the_others_missing(tmp_path):
    _recorded_day(tmp_path / "prices")
    _recorded_day(tmp_path / "prices", DAY + timedelta(days=2))

    coverage = service(tmp_path).recorded_coverage(DAY - timedelta(days=2), DAY + timedelta(days=2))

    assert [(day["day"], day["state"]) for day in coverage["days"]] == [
        ("2026-10-10", "closed"), ("2026-10-11", "missing"), ("2026-10-12", "ready"), ("2026-10-13", "missing"),
        ("2026-10-14", "ready"),
    ]
    assert coverage["ready"] == {"count": 2, "first": "2026-10-12", "last": "2026-10-14"}
    assert coverage["prices"] == "capital"
    with pytest.raises(InvalidRequest):
        service(tmp_path).recorded_coverage(DAY, DAY - timedelta(days=1))


def test_a_run_through_the_real_command_on_the_recorded_days_gives_what_the_same_bars_give(tmp_path):
    """With no Dukascopy day at all: the run, its trade, its path and its day are read from Capital.com's days."""
    _recorded_day(tmp_path / "prices")
    backtests = BacktestService(runs_root=str(tmp_path / "runs"), prices_root=str(tmp_path / "prices"), rules=lambda: RULES)
    os.makedirs(tmp_path / "runs")

    @worked
    async def scenario(backtests):
        run = backtests.create(
            first_day=DAY, last_day=DAY, parameters={"entry_mode": "cross_catchup"}, leverages=[110], prices="capital",
        )
        await until(lambda: state_of(backtests, run["id"]) in (DONE, FAILED), seconds=30)
        return backtests.get(run["id"])

    run = scenario(backtests)

    assert run["status"]["state"] == DONE, run["output"]
    # The same bars as the Dukascopy day of tests/test_admin_backtests.py: the same trade.
    (trade,) = backtests.rows(run["id"], "trades", 110.0)
    assert (trade["direction"], trade["entry_reason"], trade["exit_reason"]) == ("short", "catch-up", "Daily profit target")
    path = backtests.trade_path(run["id"], 110.0, trade["entry_time"])
    assert path["points"] and path["points"][-1][1] == 1.7
    chart = backtests.day_chart(run["id"], DAY, 110.0)
    assert len(chart["bars"]) == 136 and chart["trades"] == [trade]
    assert run["stale"] == {"code": False, "prices": False, "version": run["summary"]["code"]["version"]}

    # A Dukascopy day of the same date changes nothing of it; its own day written again does.
    write_day_file_dukascopy = dukascopy.write_day_file
    write_day_file_dukascopy(dukascopy.day_path(str(tmp_path / "prices"), "us100", DAY), [], symbol="TEST", label="us100")
    assert backtests.get(run["id"])["stale"]["prices"] is False
    assert backtests.list_runs()[0]["stale"]["prices"] is False
    write_day_file(day_path(str(tmp_path / "prices"), "us100", DAY), [], epic="US100", label="us100", name=None)
    assert backtests.get(run["id"])["stale"]["prices"] is True
    assert backtests.list_runs()[0]["stale"]["prices"] is True
