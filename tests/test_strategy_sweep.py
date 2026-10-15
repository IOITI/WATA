import json
import os
from dataclasses import replace
from datetime import date

import pytest

from src.price_recorder.bars import Bar
from src.strategy import backtest, sweep
from src.strategy.engine import LocalClock
from src.strategy.execution import WataRules
from src.strategy.history.dukascopy import write_day_file
from src.strategy.sweep import Axis, held_out_from, rule_value

CLOCK = LocalClock("Europe/Paris")
CONFIG = os.path.join("etc", "config_example.json")
DAYS = [date(2026, 10, 12), date(2026, 10, 13), date(2026, 10, 14), date(2026, 10, 15)]

with open(CONFIG, encoding="utf-8") as config_file:
    RULES = WataRules.from_config(json.load(config_file))


def leg(first: float, last: float, step: float = 2) -> list[float]:
    step = step if last > first else -step
    return [first + step * index for index in range(1, int(abs(last - first) / abs(step)) + 1)]


def recorded(start: int, prices: list[float]) -> list[Bar]:
    bars = []
    for index, (opened, closed) in enumerate(zip(prices, prices[1:])):
        high, low = max(opened, closed), min(opened, closed)
        bars.append(Bar("index", start + 10 * index, opened, high, low, closed, opened + 1, high + 1, low + 1, closed + 1, 5, 0))
    return bars


@pytest.fixture
def prices(tmp_path) -> str:
    """
    Four days of the index, each a little wider than the one before: back and
    forth before the session, then down from eight o'clock and twice as far up.
    The kernel crosses the session's VWAP on the way back up, as in the tests
    of a backtest.
    """
    directory = tmp_path / "prices"
    for number, day in enumerate(DAYS):
        fall = 200 + 40 * number
        night = [30000.0] + leg(30000, 30060) + leg(30060, 30000) + leg(30000, 30060) + leg(30060, 30000)
        session = [30000.0] + leg(30000, 30000 - fall) + leg(30000 - fall, 30000 + fall) + [30000.0 + fall] * 6
        bars = recorded(CLOCK.epoch(day, "07:00"), night) + recorded(CLOCK.epoch(day, "08:00"), session)
        write_day_file(str(directory / f"{day.isoformat()}.csv.gz"), bars, symbol="TEST", label="us100")
    return str(directory)


def swept(*arguments: str) -> int:
    return sweep.run(sweep.parse_arguments(["--config", CONFIG, "--from", "2026-10-12", "--to", "2026-10-15", *arguments]))


def result(out: str) -> dict:
    with open(os.path.join(out, "sweep.json"), encoding="utf-8") as in_file:
        return json.load(in_file)


# ── what is asked ──


def test_an_axis_is_a_setting_and_the_values_to_try():
    assert Axis.parse("rule:day_profit_target=1, 1.7,3") == Axis("rule", "day_profit_target", ("1", "1.7", "3"))
    assert Axis.parse("parameter:entry_mode=cross,cross_catchup").kind == "parameter"
    for text in ("day_profit_target=1,2", "rule:day_profit_target", "rule:=1,2", "turbo:spread=1,2", "rule:x="):
        with pytest.raises(ValueError, match="An axis is"):
            Axis.parse(text)


def test_a_value_is_read_as_the_rule_takes_it():
    # The stop is written -15 in the configuration: it is a number all the same, and can be tried at -12.5.
    assert rule_value(RULES, "stoploss_percent", "-12.5") == -12.5
    assert rule_value(RULES, "max_trades_per_day", "3") == 3
    assert (rule_value(RULES, "trailing_enabled", "off"), rule_value(RULES, "trailing_enabled", "True"), rule_value(RULES, "trailing_enabled", False)) == (False, True, False)
    for name, value, message in [
        ("max_trades_per_day", "2.5", "needs a whole number"),
        ("stoploss_percent", "low", "needs a number"),
        ("trailing_enabled", "maybe", "is on or off"),
        ("timezone", "UTC", "cannot be given several values"),
        ("closed_dates", "25/12/2026", "cannot be given several values"),
        ("stop_loss", "-10", "cannot be given several values"),
    ]:
        with pytest.raises(ValueError, match=message):
            rule_value(RULES, name, value)


@pytest.mark.parametrize("count, share, first_held_out", [
    (10, 0.3, 7), (10, 0.31, 6), (4, 0.3, 2), (3, 0.5, 1), (2, 0.1, 1), (2, 0.5, 1),
])
def test_the_last_days_are_held_out_one_at_least_and_never_all_of_them(count, share, first_held_out):
    days = [date(2026, 10, 1 + number) for number in range(count)]

    assert held_out_from(days, share) == days[first_held_out]


def test_a_single_day_cannot_be_split():
    assert held_out_from([DAYS[0]], 0.3) is None
    assert held_out_from([], 0.3) is None


@pytest.mark.parametrize("arguments", [
    [],
    ["--axis", "rule:a=1,2", "--axis", "rule:b=1,2", "--axis", "rule:c=1,2"],
    ["--axis", "rule:day_profit_target=1,2", "--axis", "rule:day_profit_target=3,4"],
    ["--axis", "day_profit_target=1,2"],
    ["--axis", "rule:day_profit_target=1,2", "--held-out", "0.9"],
    ["--axis", "rule:day_profit_target=1,2", "--set", "entry_mode"],
])
def test_a_sweep_that_makes_no_sense_stops_the_command(arguments):
    with pytest.raises(SystemExit):
        sweep.parse_arguments(["--from", "2026-10-12", "--to", "2026-10-15", *arguments])


# ── what is found ──


def test_every_combination_is_replayed_and_its_figures_given_apart_for_the_days_held_out(prices, tmp_path, capsys):
    out = str(tmp_path / "out")

    code = swept(
        "--source", prices, "--out", out, "--held-out", "0.5",
        "--axis", "rule:day_profit_target=1.7,3", "--axis", "parameter:entry_mode=cross,cross_catchup",
    )

    assert code == 0
    printed = capsys.readouterr().out
    assert "4 combination(s) of day_profit_target (2 values) and entry_mode (2 values)" in printed
    assert "2 day(s) to choose on, then 2 held out from 2026-10-14: those had no say in the choice." in printed
    found = result(out)
    assert found["axes"] == [
        {"kind": "rule", "name": "day_profit_target", "values": [1.7, 3.0]},
        {"kind": "parameter", "name": "entry_mode", "values": ["cross", "cross_catchup"]},
    ]
    assert found["held_out"] == {"share": 0.5, "from": "2026-10-14", "days": 2, "tuning_days": 2}
    assert [cell["values"] for cell in found["cells"]] == [[1.7, "cross"], [1.7, "cross_catchup"], [3.0, "cross"], [3.0, "cross_catchup"]]
    cross = found["cells"][0]
    # One trade a day, sold at the day's target: two days on each side of the cut.
    assert (cross["tuning"]["days"], cross["tuning"]["trades"], cross["tuning"]["total_percent"]) == (2, 2, 3.43)
    assert (cross["held_out"]["days"], cross["held_out"]["trades"], cross["held_out"]["total_percent"]) == (2, 2, 3.43)
    assert (cross["whole"]["days"], cross["whole"]["trades"], cross["whole"]["total_percent"]) == (4, 4, 6.98)
    # A target of 3 % is 3 % a day.
    assert found["cells"][2]["tuning"]["total_percent"] == 6.09
    # Each part has the entries refused on its own days, and no range is drawn in a sweep.
    assert cross["tuning"]["refusals"] == cross["held_out"]["refusals"] == {}
    assert found["cells"][1]["tuning"]["refusals"] == {"Day profit reached": 2}
    assert cross["tuning"]["total_range"] is None
    assert (found["strategy"], found["turbo"]["points_per_unit"], found["code"]["version"]) == ("kernel_vwap", 110.0, found["code"]["version"])
    assert found["price_files"]["files"] == 4


def test_a_combination_of_a_sweep_is_the_backtest_made_with_those_values(prices, tmp_path):
    """The figures of a cell are those a backtest of its own gives: a sweep is no other way of counting."""
    out = str(tmp_path / "out")
    assert swept(
        "--source", prices, "--out", out, "--points-per-unit", "110", "--slippage", "1", "--fill-delay", "10",
        "--axis", "rule:day_profit_target=1.2,2.5", "--axis", "parameter:kernel_bandwidth=8,15",
    ) == 0

    for cell in result(out)["cells"]:
        target, bandwidth = cell["values"]
        rules_path = tmp_path / "rules.json"
        rules_path.write_text(json.dumps(replace(RULES, day_profit_target=target).to_dict()))
        alone = str(tmp_path / f"alone-{target}-{bandwidth}")
        assert backtest.run(backtest.parse_arguments([
            "--from", "2026-10-12", "--to", "2026-10-15", "--source", prices, "--rules", str(rules_path),
            "--set", f"kernel_bandwidth={bandwidth}", "--points-per-unit", "110", "--slippage", "1", "--fill-delay", "10",
            "--baseline", "0", "--capital", "0", "--out", alone,
        ])) == 0
        with open(os.path.join(alone, "summary.json"), encoding="utf-8") as in_file:
            figures = json.load(in_file)["runs"][0]["summary"]
        for name in ("days", "days_with_an_entry", "trades", "win_rate", "total_percent", "expectancy", "worst_drawdown", "exits", "entries", "refusals", "index_points"):
            assert cell["whole"][name] == figures[name], (cell["values"], name)


def test_too_few_days_to_hold_any_out_is_said_and_no_part_is_made_up(prices, tmp_path, capsys):
    out = str(tmp_path / "out")

    code = sweep.run(sweep.parse_arguments([
        "--config", CONFIG, "--from", "2026-10-12", "--to", "2026-10-12", "--source", prices, "--out", out,
        "--axis", "rule:day_profit_target=1.7,3",
    ]))

    assert code == 0
    assert "1 day(s) with prices: too few to hold any out." in capsys.readouterr().out
    found = result(out)
    assert found["held_out"] == {"share": 0.3, "from": None, "days": 0, "tuning_days": 1}
    assert [cell["held_out"] for cell in found["cells"]] == [None, None]
    assert found["cells"][0]["tuning"] == found["cells"][0]["whole"]


def test_rules_are_swept_over_the_signals_wata_received(prices, tmp_path, capsys):
    recorded_signals = tmp_path / "signals.json"
    recorded_signals.write_text(json.dumps([
        {"at": f"2026-10-{day}T06:01:05Z", "action": "short", "confidence": 1.2} for day in (12, 13, 14, 15)
    ]))
    out = str(tmp_path / "out")

    code = swept("--source", prices, "--out", out, "--signals", str(recorded_signals), "--axis", "rule:day_profit_target=1,1.7,2.5")

    assert code == 0
    assert f"Signals of {recorded_signals}" in capsys.readouterr().out
    found = result(out)
    assert (found["strategy"], found["parameters_changed"]) == ("signals", {})
    # A short on the way down each day, sold at whatever the day's target is.
    assert [cell["whole"]["total_percent"] for cell in found["cells"]] == [4.06, 6.98, 10.38]
    assert [cell["whole"]["entries"] for cell in found["cells"]] == [{"signal": 4}] * 3


@pytest.mark.parametrize("arguments, message", [
    (["--axis", "parameter:entry_mod=cross,cross_catchup"], "Unknown parameter 'entry_mod'"),
    (["--axis", "rule:stop_loss=-10,-5"], "cannot be given several values"),
    (["--axis", "rule:day_profit_target=1,lots"], "needs a number"),
    (["--axis", "rule:day_profit_target=" + ",".join(str(value) for value in range(1, 12)), "--axis", "rule:stoploss_percent=" + ",".join(str(-value) for value in range(1, 11))], "110 combinations: 100 at most"),
])
def test_a_value_that_cannot_be_used_stops_the_sweep_before_anything_is_read(prices, arguments, message, capsys):
    assert swept("--source", prices, *arguments) == 2
    assert message in capsys.readouterr().err


def test_a_parameter_cannot_be_swept_over_signals_which_have_no_strategy(prices, tmp_path, capsys):
    recorded_signals = tmp_path / "signals.json"
    recorded_signals.write_text("[]")

    assert swept("--source", prices, "--signals", str(recorded_signals), "--axis", "parameter:kernel_bandwidth=8,15") == 2
    assert "only rules can be given several values" in capsys.readouterr().err


def test_a_period_without_prices_is_said_so(prices, tmp_path, capsys):
    assert sweep.run(sweep.parse_arguments([
        "--config", CONFIG, "--from", "2026-11-02", "--to", "2026-11-03", "--source", prices, "--axis", "rule:day_profit_target=1,2",
    ])) == 2
    assert "No bar of us100 between 2026-11-02 and 2026-11-03" in capsys.readouterr().err
    assert swept("--root", str(tmp_path), "--axis", "rule:day_profit_target=1,2") == 2
