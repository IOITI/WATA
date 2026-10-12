import csv
import json
import os
from datetime import date

import pytest

from src.price_recorder.bars import Bar
from src.strategy import backtest
from src.strategy.engine import LocalClock
from src.strategy.history.dukascopy import write_day_file

CLOCK = LocalClock("Europe/Paris")
DAY = date(2026, 10, 12)
CONFIG = os.path.join("etc", "config_example.json")


def leg(first: float, last: float, step: float = 2) -> list[float]:
    """The prices after ``first`` on the way to ``last``, two points at a time."""
    step = step if last > first else -step
    return [first + step * index for index in range(1, int(abs(last - first) / abs(step)) + 1)]


def recorded(start: int, prices: list[float]) -> list[Bar]:
    """Ten-second bars going from each price to the next, the ask one point above the bid."""
    bars = []
    for index, (opened, closed) in enumerate(zip(prices, prices[1:])):
        high, low = max(opened, closed), min(opened, closed)
        bars.append(Bar("index", start + 10 * index, opened, high, low, closed, opened + 1, high + 1, low + 1, closed + 1, 5, 0))
    return bars


@pytest.fixture
def prices(tmp_path) -> str:
    """
    A directory holding one day of the index: back and forth over 60 points
    before the session, then 200 points down from eight o'clock and 400 up.
    The kernel is under the session's VWAP when its band gets wide enough, and
    crosses it on the way back up.
    """
    night = [30000.0] + leg(30000, 30060) + leg(30060, 30000) + leg(30000, 30060) + leg(30060, 30000)
    session = [30000.0] + leg(30000, 29800) + leg(29800, 30200) + [30200.0] * 6
    bars = recorded(CLOCK.epoch(DAY, "07:00"), night) + recorded(CLOCK.epoch(DAY, "08:00"), session)
    directory = tmp_path / "prices"
    write_day_file(str(directory / "2026-10-12.csv.gz"), bars, symbol="TEST", label="us100")
    return str(directory)


def run(*arguments: str) -> int:
    return backtest.run(backtest.parse_arguments(["--config", CONFIG, *arguments]))


def rows(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as in_file:
        return list(csv.DictReader(in_file))


def test_a_backtest_trades_the_cross_with_watas_rules_and_writes_what_it_did(prices, tmp_path, capsys):
    out = str(tmp_path / "out")

    code = run("--from", "2026-10-12", "--source", prices, "--out", out)

    assert code == 0
    printed = capsys.readouterr().out
    assert "kernel_vwap from 2026-10-12 to 2026-10-12 (Europe/Paris)" in printed
    assert "Parameters changed: none." in printed
    # One run per leverage assumed for the turbo.
    assert sorted(os.listdir(out)) == [
        "days_110.csv", "days_140.csv", "days_80.csv", "summary.json", "trades_110.csv", "trades_140.csv", "trades_80.csv",
    ]

    (trade,) = rows(os.path.join(out, "trades_110.csv"))
    # The kernel passes above the VWAP at 08:25:50 on the way back up; WATA's daily target sells.
    assert (trade["entry_time"], trade["direction"], trade["entry_reason"]) == ("2026-10-12T06:25:50Z", "long", "cross")
    assert (trade["exit_reason"], trade["performance_percent"]) == ("Daily profit target", "1.7")
    assert rows(os.path.join(out, "days_110.csv")) == [{"day": "2026-10-12", "entries": "1", "closed": "1", "day_percent": "1.7"}]

    with open(os.path.join(out, "summary.json"), encoding="utf-8") as in_file:
        summary = json.load(in_file)
    assert summary["parameters"]["entry_mode"] == "cross"
    assert summary["rules"]["day_profit_target"] == 1.7
    assert [run["turbo"]["points_per_unit"] for run in summary["runs"]] == [80.0, 110.0, 140.0]
    assert [run["summary"]["trades"] for run in summary["runs"]] == [1, 1, 1]
    # The same 1.7 % is fewer index points on a turbo with more leverage.
    assert [run["summary"]["index_points"] for run in summary["runs"]] == [8.4, 11.5, 14.7]


def test_a_parameter_of_the_strategy_changes_what_is_traded(prices, tmp_path, capsys):
    out = str(tmp_path / "out")

    code = run("--from", "2026-10-12", "--source", prices, "--out", out, "--set", "entry_mode=cross_catchup", "--points-per-unit", "110")

    assert code == 0
    assert "Parameters changed: {'entry_mode': 'cross_catchup'}." in capsys.readouterr().out
    (trade,) = rows(os.path.join(out, "trades_110.csv"))
    # Short as soon as the band is wide enough, twenty minutes before the cross.
    assert (trade["entry_time"], trade["direction"], trade["entry_reason"]) == ("2026-10-12T06:06:10Z", "short", "catch-up")
    with open(os.path.join(out, "summary.json"), encoding="utf-8") as in_file:
        (only_run,) = json.load(in_file)["runs"]
    # The cross that came later is refused: the day was already won.
    assert only_run["summary"]["refusals"] == {"Day profit reached": 1}


def test_a_trace_gives_one_line_per_range_bar_with_what_the_strategy_saw(prices, tmp_path):
    trace = str(tmp_path / "trace.csv")

    assert run("--from", "2026-10-12", "--source", prices, "--trace", trace) == 0

    traced = rows(trace)
    assert list(traced[0]) == ["ended_utc", "ended_local", "close", "kernel", "vwap", "band", "entries_allowed", "target"]
    before, during = traced[0], traced[-1]
    # Before the session there is no VWAP; at the end of the day the position is the long one.
    assert (before["ended_local"][:2], before["vwap"], before["entries_allowed"]) == ("07", "", "False")
    assert (during["entries_allowed"], during["target"]) == ("True", "long")
    assert float(during["band"]) > 20


def test_weekdays_of_the_period_that_have_no_prices_are_named(prices, capsys):
    # Friday 9 to Tuesday 13 October: only the Monday was fetched.
    assert run("--from", "2026-10-09", "--to", "2026-10-13", "--source", prices) == 0

    printed = capsys.readouterr().out
    assert "No prices for 2 weekday(s) of the period (not fetched, or the market was closed): 2026-10-09, 2026-10-13" in printed
    assert [line for line in printed.splitlines() if line.startswith("Days  ")][0].split() == ["Days", "1", "1", "1"]
    # The day that is there only has prices until 08:51.
    assert "Prices incomplete for 1 day(s), whose results cover only part of the market hours: 2026-10-12" in printed


def test_a_period_without_any_price_is_said_so(prices, capsys):
    assert run("--from", "2026-10-14", "--source", prices) == 2
    assert "No bar of us100 between 2026-10-14 and 2026-10-14" in capsys.readouterr().err


def test_prices_that_were_never_fetched_are_said_so_with_the_command_that_fetches_them(tmp_path, capsys):
    assert run("--from", "2026-10-12", "--root", str(tmp_path)) == 2
    assert "python -m src.strategy.history.dukascopy" in capsys.readouterr().err


def test_an_unknown_parameter_is_refused_before_anything_is_read(prices, capsys):
    assert run("--from", "2026-10-12", "--source", prices, "--set", "entry_mod=cross") == 2
    assert "Unknown parameter 'entry_mod'" in capsys.readouterr().err


@pytest.mark.parametrize("arguments", [
    ["--from", "2026-10-12", "--to", "2026-10-11"],
    ["--from", "2026-10-12", "--set", "entry_mode"],
    ["--from", "2026-10-12", "--points-per-unit", "a lot"],
])
def test_arguments_that_make_no_sense_stop_the_command(arguments):
    with pytest.raises(SystemExit):
        backtest.parse_arguments(arguments)
