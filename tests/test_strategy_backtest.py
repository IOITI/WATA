import csv
import json
import os
from datetime import date

import pytest

from src.price_recorder.bars import Bar
from src.strategy import backtest
from src.strategy.engine import LocalClock
from src.strategy.execution import WataRules
from src.strategy.fingerprint import code_fingerprint, file_digest, prices_fingerprint
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
        "days_110.csv", "days_140.csv", "days_80.csv",
        "refusals_110.csv", "refusals_140.csv", "refusals_80.csv",
        "summary.json", "trades_110.csv", "trades_140.csv", "trades_80.csv",
    ]

    (trade,) = rows(os.path.join(out, "trades_110.csv"))
    # The kernel passes above the VWAP at 08:25:50 on the way back up; WATA's daily target sells.
    assert (trade["entry_time"], trade["direction"], trade["entry_reason"]) == ("2026-10-12T06:25:50Z", "long", "cross")
    assert (trade["exit_reason"], trade["performance_percent"]) == ("Daily profit target", "1.7")
    # In money: on 10,000, at the size WATA gives an entry of that hour and that confidence.
    assert (trade["scale_percent"], trade["stake"], trade["profit_loss"]) == ("100.0", "9992.4", "169.87")
    assert rows(os.path.join(out, "days_110.csv")) == [
        {"day": "2026-10-12", "entries": "1", "closed": "1", "day_percent": "1.7", "profit_loss": "169.87", "equity": "10169.87"},
    ]

    with open(os.path.join(out, "summary.json"), encoding="utf-8") as in_file:
        summary = json.load(in_file)
    assert summary["parameters"]["entry_mode"] == "cross"
    assert summary["parameters_changed"] == {}
    assert summary["rules"]["day_profit_target"] == 1.7
    assert summary["rules_from"] == CONFIG
    assert (summary["bars"], summary["decisions"]) == (426, 1)
    # The day's prices stop at 08:51: it is tested, and said to be incomplete.
    assert (summary["days_without_prices"], summary["days_incomplete"]) == ([], ["2026-10-12"])
    assert [run["turbo"]["points_per_unit"] for run in summary["runs"]] == [80.0, 110.0, 140.0]
    assert [run["summary"]["trades"] for run in summary["runs"]] == [1, 1, 1]
    # The same 1.7 % is fewer index points on a turbo with more leverage.
    assert [run["summary"]["index_points"] for run in summary["runs"]] == [8.4, 11.5, 14.7]
    assert summary["execution"] == {"fill_delay": 0, "slippage": 0.0, "capital": 10000.0}
    assert summary["runs"][1]["summary"]["money"] == {
        "capital": 10000.0, "final": 10169.87, "profit_loss": 169.87, "return_percent": 1.7,
        "worst_drawdown": 0.0, "worst_drawdown_percent": 0.0, "average_scale_percent": 100.0, "unfunded": 0,
    }
    # What it was made with: the code, and the one file of prices it read.
    assert summary["code"] == code_fingerprint() and len(summary["code"]["digest"]) == 16
    assert summary["price_files"] == prices_fingerprint([os.path.join(prices, "2026-10-12.csv.gz")])
    assert summary["price_files"]["files"] == 1
    # What a figure rests on is said with it.
    figures = summary["runs"][1]["summary"]
    assert (figures["sample"], figures["expectancy"], figures["profit_factor"], figures["total_range"]) == ("too_few", 1.7, None, None)
    # The worst level of the trade is kept: bought at the ask, it starts under what was paid.
    assert trade["min_performance_percent"] == "-0.68"


def test_a_backtest_is_set_against_entries_taken_at_random_under_the_same_rules(prices, tmp_path, capsys):
    out = str(tmp_path / "out")

    assert run("--from", "2026-10-12", "--source", prices, "--out", out, "--points-per-unit", "110") == 0

    printed = capsys.readouterr().out
    assert 'Set against 200 repetitions of entries at random under the same rules' in printed
    assert [line for line in printed.splitlines() if line.startswith("Random entries beaten")]
    with open(os.path.join(out, "summary.json"), encoding="utf-8") as in_file:
        (only_run,) = json.load(in_file)["runs"]
    found = only_run["summary"]["against_random"]
    # One entry was asked for on that day: each repetition draws one.
    assert (found["repetitions"], found["seed"], found["entries"], len(found["totals"])) == (200, 1, 1, 200)
    assert found["low"] <= found["median"] <= found["high"] and 0 <= found["beaten"] <= 100
    # The strategy made the day's target, which a random entry cannot beat: the ones that made it too count for half.
    assert only_run["summary"]["total_percent"] == found["best"] == 1.7
    at_target = sum(1 for total in found["totals"] if total == 1.7)
    assert found["beaten"] == round((200 - at_target / 2) * 100 / 200, 1)


def test_the_random_entries_are_the_same_from_one_run_to_the_next_and_can_be_left_out(prices, tmp_path, capsys):
    def against_random(*arguments: str):
        out = str(tmp_path / f"out{len(os.listdir(tmp_path))}")
        assert run("--from", "2026-10-12", "--source", prices, "--out", out, "--points-per-unit", "110", *arguments) == 0
        with open(os.path.join(out, "summary.json"), encoding="utf-8") as in_file:
            return json.load(in_file)["runs"][0]["summary"]["against_random"]

    first, again, other = against_random("--baseline", "20"), against_random("--baseline", "20"), against_random("--baseline", "20", "--baseline-seed", "7")

    assert first == again and first["repetitions"] == 20
    assert (other["seed"], other["totals"] != first["totals"]) == (7, True)
    capsys.readouterr()
    assert against_random("--baseline", "0") is None
    assert "Set against" not in capsys.readouterr().out


def test_fills_can_be_made_less_kind_and_the_account_another(prices, tmp_path, capsys):
    out = str(tmp_path / "out")

    code = run(
        "--from", "2026-10-12", "--source", prices, "--out", out, "--points-per-unit", "110", "--baseline", "0",
        "--fill-delay", "30", "--slippage", "1.5", "--capital", "5000",
    )

    assert code == 0
    assert "Fills 30 s after the next ten seconds, 1.5 index point(s) lost on each." in capsys.readouterr().out
    (trade,) = rows(os.path.join(out, "trades_110.csv"))
    # Decided at 08:25:50 and filled thirty seconds later; the target of 1.7 % is sold 1.5 points lower.
    assert (trade["entry_time"], trade["performance_percent"], trade["profit_loss"]) == ("2026-10-12T06:26:20Z", "1.39", "69.42")
    with open(os.path.join(out, "summary.json"), encoding="utf-8") as in_file:
        summary = json.load(in_file)
    assert summary["execution"] == {"fill_delay": 30, "slippage": 1.5, "capital": 5000.0}


def test_no_money_is_given_without_a_capital(prices, tmp_path, capsys):
    out = str(tmp_path / "out")

    assert run("--from", "2026-10-12", "--source", prices, "--out", out, "--points-per-unit", "110", "--baseline", "0", "--capital", "0") == 0

    printed = capsys.readouterr().out
    assert [line for line in printed.splitlines() if line.startswith("In money, as WATA sizes")][0].split()[-1] == "-"
    (trade,) = rows(os.path.join(out, "trades_110.csv"))
    assert (trade["scale_percent"], trade["stake"], trade["profit_loss"]) == ("100.0", "", "")
    with open(os.path.join(out, "summary.json"), encoding="utf-8") as in_file:
        assert json.load(in_file)["runs"][0]["summary"]["money"] is None


def test_the_fingerprint_of_the_prices_follows_what_the_files_hold(prices, tmp_path):
    path = os.path.join(prices, "2026-10-12.csv.gz")
    before = prices_fingerprint([path])

    # The same bars written again: the same file, to the byte.
    with_the_same = recorded(CLOCK.epoch(DAY, "07:00"), [30000.0, 30002.0])
    other = tmp_path / "other" / "2026-10-12.csv.gz"
    write_day_file(str(other), with_the_same, symbol="TEST", label="us100")
    first = file_digest(str(other))
    write_day_file(str(other), with_the_same, symbol="TEST", label="us100")
    assert file_digest(str(other)) == first

    # Other bars under the same name: another fingerprint. Kept digests are asked for instead of read again.
    assert prices_fingerprint([str(other)]) != before
    assert prices_fingerprint([path], digest_of=lambda asked: "kept")["digest"] != before["digest"]
    assert prices_fingerprint([]) == {"files": 0, "digest": prices_fingerprint([])["digest"]}


def _signals(tmp_path, entries: list[dict]) -> str:
    path = tmp_path / "signals.json"
    path.write_text(json.dumps(entries))
    return str(path)


def test_signals_wata_received_are_replayed_in_place_of_a_strategy(prices, tmp_path, capsys):
    recorded = _signals(tmp_path, [
        # On the way down: a short that makes the day's target within the minute.
        {"at": "2026-10-12T06:01:05Z", "action": "short", "confidence": 1.2},
        # The trader refuses it, the day being won: the journal has such signals too.
        {"at": "2026-10-12T06:20:00Z", "action": "long", "confidence": 1.2},
        # The scheduler's end of day, long after these prices stop.
        {"at": "2026-10-12T19:55:00Z", "action": "close-position"},
        # Another day: not in the period asked for.
        {"at": "2026-10-13T06:00:00Z", "action": "long"},
    ])
    out = str(tmp_path / "out")

    code = run("--from", "2026-10-12", "--source", prices, "--out", out, "--points-per-unit", "110", "--signals", recorded)

    assert code == 0
    printed = capsys.readouterr().out
    assert f"Signals of {recorded} from 2026-10-12 to 2026-10-12 (Europe/Paris)" in printed
    assert "3 signal(s) in the period, 1 of them at a moment without prices." in printed
    assert "426 bars, 2 signal(s) replayed" in printed
    (trade,) = rows(os.path.join(out, "trades_110.csv"))
    assert (trade["entry_time"], trade["direction"], trade["entry_reason"], trade["confidence"]) == ("2026-10-12T06:01:10Z", "short", "signal", "1.2")
    assert (trade["exit_reason"], trade["performance_percent"]) == ("Daily profit target", "1.7")
    assert rows(os.path.join(out, "refusals_110.csv")) == [
        {"time": "2026-10-12T06:20:10Z", "direction": "long", "reason": "Day profit reached"},
    ]
    with open(os.path.join(out, "summary.json"), encoding="utf-8") as in_file:
        summary = json.load(in_file)
    assert (summary["strategy"], summary["parameters"], summary["parameters_changed"]) == ("signals", {}, {})
    assert summary["signals"] == {"replayed": 2, "without_prices": ["2026-10-12T19:55:00Z"]}
    # Two entries were asked for: the random ones are drawn in the trader's hours.
    assert summary["runs"][0]["summary"]["against_random"]["entries"] == 2


def test_a_signal_under_the_confidence_asked_is_refused_as_the_trader_would(prices, tmp_path):
    recorded = _signals(tmp_path, [{"at": "2026-10-12T06:01:05Z", "action": "short", "confidence": 0.1}])
    out = str(tmp_path / "out")

    assert run("--from", "2026-10-12", "--source", prices, "--out", out, "--points-per-unit", "110", "--signals", recorded, "--baseline", "0") == 0

    assert rows(os.path.join(out, "trades_110.csv")) == []
    assert [refusal["reason"] for refusal in rows(os.path.join(out, "refusals_110.csv"))] == ["Confidence below the minimum"]


def test_signals_that_cannot_be_read_stop_the_command(prices, tmp_path, capsys):
    assert run("--from", "2026-10-12", "--source", prices, "--signals", str(tmp_path / "absent.json")) == 2
    assert run("--from", "2026-10-12", "--source", prices, "--signals", _signals(tmp_path, [{"at": 0, "action": "buy"}])) == 2
    assert "Not an action the trader takes: 'buy'" in capsys.readouterr().err


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
    assert rows(os.path.join(out, "refusals_110.csv")) == [
        {"time": "2026-10-12T06:25:50Z", "direction": "long", "reason": "Day profit reached"},
    ]


def test_a_trace_gives_one_line_per_range_bar_with_what_the_strategy_saw(prices, tmp_path):
    trace = str(tmp_path / "trace.csv")

    assert run("--from", "2026-10-12", "--source", prices, "--trace", trace) == 0

    traced = rows(trace)
    assert list(traced[0]) == [
        "ended_utc", "ended_local", "began", "open", "high", "low", "close", "complete", "after_pause",
        "kernel", "vwap", "band", "entries_allowed", "target",
    ]
    # Whole bars, as prices without a product's last digits.
    assert (traced[0]["open"], traced[0]["high"], traced[0]["low"], traced[0]["close"]) == ("30000.5", "30006.5", "30000.5", "30006.5")
    before, during = traced[0], traced[-1]
    # Before the session there is no VWAP; at the end of the day the position is the long one.
    assert (before["ended_local"][:2], before["vwap"], before["entries_allowed"]) == ("07", "", "False")
    assert (during["entries_allowed"], during["target"]) == ("True", "long")
    assert float(during["band"]) > 20


def test_rules_written_out_replace_the_configuration_for_one_run(prices, tmp_path):
    with open(CONFIG, encoding="utf-8") as config_file:
        rules = WataRules.from_config(json.load(config_file)).to_dict()
    # What if the day's target were 3 % instead of 1.7 %?
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(json.dumps({**rules, "day_profit_target": 3.0, "no_entry_above_day_profit": 3.0}))
    out = str(tmp_path / "out")

    code = backtest.run(backtest.parse_arguments(
        ["--from", "2026-10-12", "--source", prices, "--rules", str(rules_path), "--out", out, "--points-per-unit", "110"],
    ))

    assert code == 0
    (trade,) = rows(os.path.join(out, "trades_110.csv"))
    assert (trade["exit_reason"], trade["performance_percent"]) == ("Daily profit target", "3.0")
    with open(os.path.join(out, "summary.json"), encoding="utf-8") as in_file:
        summary = json.load(in_file)
    assert summary["rules"]["day_profit_target"] == 3.0
    assert summary["rules_from"] == str(rules_path)


def test_rules_that_are_not_a_whole_set_are_refused(prices, tmp_path, capsys):
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(json.dumps({"day_profit_target": 3.0}))

    assert backtest.run(backtest.parse_arguments(["--from", "2026-10-12", "--source", prices, "--rules", str(rules_path)])) == 2
    assert "Not a set of rules: missing timezone" in capsys.readouterr().err


def test_the_day_being_replayed_is_kept_in_a_file_for_whoever_waits(prices, tmp_path):
    progress = tmp_path / "progress.json"

    assert run("--from", "2026-10-12", "--source", prices, "--progress", str(progress)) == 0

    assert json.loads(progress.read_text()) == {"day": "2026-10-12", "days_begun": 1, "bars": 1}
    assert not (tmp_path / "progress.json.part").exists()


def test_one_day_can_be_replayed_for_a_chart(prices):
    bars, decisions = backtest.replay_day("kernel_vwap", {"entry_mode": "cross_catchup"}, CLOCK, prices, DAY)

    assert len(bars) == 136
    first = bars[0]
    # Six points at two points every ten seconds: the range is left in the fourth ten seconds.
    assert (first["began"], first["ended"]) == (CLOCK.epoch(DAY, "07:00"), CLOCK.epoch(DAY, "07:00") + 40)
    assert (first["open"], first["high"], first["low"], first["close"], first["complete"]) == (30000.5, 30006.5, 30000.5, 30006.5, True)
    assert first["vwap"] is None and bars[-1]["vwap"] is not None
    assert [(decision.target, decision.reason) for decision in decisions] == [("short", "catch-up"), ("long", "cross")]


def test_the_parameters_of_a_strategy_are_described_for_a_form():
    described = {parameter["name"]: parameter for parameter in backtest.describe_parameters("kernel_vwap")}

    assert described["entry_mode"] == {
        "name": "entry_mode", "kind": "text", "default": "cross", "least": None, "most": None,
        "choices": ["cross", "cross_catchup"], "advanced": False, "help": described["entry_mode"]["help"],
    }
    assert "catch" in described["entry_mode"]["help"]
    assert (described["kernel_bandwidth"]["kind"], described["kernel_bandwidth"]["least"], described["kernel_bandwidth"]["most"]) == ("integer", 2, 500)
    assert (described["min_band_width"]["kind"], described["min_band_width"]["default"]) == ("number", 20.0)
    assert described["tick"]["advanced"] is True
    # Every parameter has something to say for itself.
    assert all(parameter["help"] for parameter in described.values())


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
    ["--from", "2026-10-12", "--config", "a.json", "--rules", "b.json"],
    ["--from", "2026-10-12", "--to", "2026-10-11"],
    ["--from", "2026-10-12", "--set", "entry_mode"],
    ["--from", "2026-10-12", "--points-per-unit", "a lot"],
    ["--from", "2026-10-12", "--baseline", "-1"],
    ["--from", "2026-10-12", "--slippage", "-1"],
    ["--from", "2026-10-12", "--fill-delay", "-10"],
    ["--from", "2026-10-12", "--capital", "-5"],
    ["--from", "2026-10-12", "--signals", "signals.json", "--set", "entry_mode=cross"],
    ["--from", "2026-10-12", "--signals", "signals.json", "--trace", "trace.csv"],
])
def test_arguments_that_make_no_sense_stop_the_command(arguments):
    with pytest.raises(SystemExit):
        backtest.parse_arguments(arguments)
