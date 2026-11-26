"""
The benchmark of the Pine runner (src/ta_engine/bench.py): what a profile says, two scripts' signals set side
by side, and a whole benchmark on made-up days with the real runner, when it is built and Node is here.
"""

import csv
import gzip
import json
import math
import os
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from src.admin.ta_rules import RuleStore
from src.admin.ta_scripts import ScriptStore
from src.ta_engine import bench
from src.ta_engine.bench import COLLECTION, NODE, RUNTIME, SCRIPT, profile_said, signals_compared
from src.ta_engine.runner import RUNNER_PATH, find_node


def _frame(name, url="", line=0, column=0):
    return {"functionName": name, "url": url, "lineNumber": line, "columnNumber": column}


def test_a_profile_says_where_the_time_went_and_which_functions_took_it():
    runner = f"file:///app/src/ta_engine/runner/{os.path.basename(RUNNER_PATH)}"
    bundle = "// first line\nfunction plotValuesAt(plots, times) { return {} }\nconst other = 1\n"
    profile = {
        "nodes": [
            {"id": 1, "callFrame": _frame("(root)")},
            {"id": 2, "callFrame": _frame("_r")},                                  # what new Function made: no file
            {"id": 3, "callFrame": _frame("plotValuesAt", runner, 1, 9)},
            {"id": 4, "callFrame": _frame("(garbage collector)")},
            {"id": 5, "callFrame": _frame("(idle)")},
            {"id": 6, "callFrame": _frame("emit", "node:events", 10, 2)},
        ],
        "samples": [2, 2, 3, 4, 5, 6, 3],
        "timeDeltas": [1000, 2000, 4000, 500, 9000, 500, 2000],
    }

    said = profile_said(profile, bundle)

    # Waiting is left out of everything: 10 ms of work.
    assert said["milliseconds"] == 10
    assert said["groups"] == {
        RUNTIME: {"milliseconds": 6, "share_percent": 60.0}, SCRIPT: {"milliseconds": 3, "share_percent": 30.0},
        COLLECTION: {"milliseconds": 0, "share_percent": 5.0}, NODE: {"milliseconds": 0, "share_percent": 5.0},
    }
    first, second = said["functions"][:2]
    # A function of the runner comes with the start of its code, as the built file has it: its name may be minified.
    assert (first["function"], first["group"], first["milliseconds"], first["code"]) == (
        "plotValuesAt", RUNTIME, 6, "plotValuesAt(plots, times) { return {} }",
    )
    assert (second["function"], second["group"], second["code"]) == ("_r", SCRIPT, None)


def test_two_scripts_signals_are_the_same_or_each_says_what_it_has_alone():
    first = [("2026-11-25T08:32:21Z", "Enter Short", "short", None), ("2026-11-25T18:17:10Z", "Global Exit", "close-position", None)]

    assert signals_compared(first, list(reversed(first))) == {"same": True, "first": 2, "second": 2, "only_first": [], "only_second": []}
    compared = signals_compared(first, [first[0], ("2026-11-25T18:17:20Z", "Global Exit", "close-position", None)])
    assert compared["same"] is False
    assert compared["only_first"] == [list(first[1])] and compared["only_second"] == [["2026-11-25T18:17:20Z", "Global Exit", "close-position", None]]


# ── a whole benchmark, with the real runner ──

CROSS = """//@version=6
indicator("Cross")
fast = ta.ema(close, input.int(5, "Fast"))
slow = ta.ema(close, 21)
paris = hour(time, "Europe/Paris")
plot(fast, "Fast")
plot(slow, "Slow")
plotshape(ta.crossover(fast, slow), title = "Up shape")
bgcolor(fast > slow ? color.new(color.green, 90) : na)
alertcondition(ta.crossover(fast, slow), title = "Up", message = '{"action": "long"}')
alertcondition(ta.crossunder(fast, slow), title = "Down", message = '{"action": "short"}')
alertcondition(fast > slow, title = "Above", message = 'above')
"""
# The same with nothing drawn and the alert no rule maps left out: what reaches the two alerts mapped.
CUT_DOWN = "\n".join(
    line for line in CROSS.split("\n") if not line.startswith(("plot", "bgcolor", "paris")) and '"Above"' not in line
)


def _node_major() -> int:
    node = find_node("node")
    if node is None:
        return 0
    version = subprocess.run([node, "--version"], capture_output=True, text=True, check=False).stdout.strip()
    return int(version.lstrip("v").split(".")[0]) if version.startswith("v") else 0


def _recorded(folder, first: datetime, hours: int) -> None:
    """Ten-second rows of US100 as the Capital.com feed's day files hold them, a price that swings."""
    by_day: dict[str, list] = {}
    for index in range(hours * 360):
        moment = first + timedelta(seconds=10 * index)
        price = round(25_000 + 60 * math.sin(index / 400) + 12 * math.sin(index / 37), 1)
        by_day.setdefault(moment.strftime("%Y-%m-%d"), []).append({
            "ts": moment.strftime("%Y-%m-%dT%H:%M:%SZ"), "symbol": "US100",
            "bid_open": price, "bid_high": price + 1.5, "bid_low": price - 1.5, "bid_close": price + 0.4,
            "ask_open": price + 2, "ask_high": price + 3.5, "ask_low": price + 0.5, "ask_close": price + 2.4, "updates": 3,
        })
    for day, rows in by_day.items():
        path = os.path.join(str(folder), "capital", "bars", "us100", f"{day}.csv.gz")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with gzip.open(path, "wt", newline="", encoding="utf-8") as out_file:
            writer = csv.DictWriter(out_file, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)


@pytest.mark.skipif(not os.path.isfile(RUNNER_PATH) or _node_major() < 22, reason="the runner is not built, or no Node 22+ is here")
def test_a_days_runs_are_measured_whole_and_continued_and_a_cut_down_copy_gives_the_same_signals(tmp_path, capsys):
    scripts = ScriptStore(str(tmp_path / "ta"))
    script = scripts.create("Cross", CROSS)
    cut = scripts.create("Cross cut down", CUT_DOWN)
    rules = RuleStore(scripts)
    rule = rules.create({
        "name": "Cross on US100", "asset": {"provider": "capital", "key": "US100"}, "side": "bid",
        "bars": {"type": "time", "timeframe": "1"}, "script": {"id": script["id"], "version": 1}, "inputs": [],
        "alerts": [{"title": "Up", "action": "long"}, {"title": "Down", "action": "short"}], "indice": "us100", "history_days": 0,
    }, rules.load()["revision"])["rule"]
    _recorded(tmp_path / "recorded", datetime(2026, 11, 24, 21, 15, tzinfo=timezone.utc), 12)
    given = ["--rule", rule["id"], "--day", "2026-11-25", "--root", str(tmp_path / "ta"), "--files", str(tmp_path / "recorded"),
             "--restart-time", "21:15", "--repeat", "2", "--appends", "40", "--memory-mb", "256"]

    assert bench.main([*given, "--against", cut["id"], "--profile"]) == 0
    lean = json.loads(capsys.readouterr().out)
    assert bench.main([*given, "--full"]) == 0
    full = json.loads(capsys.readouterr().out)

    assert (lean["asked"], full["asked"]) == ("what the rule reads", "every alert and plot")
    assert lean["day"] == {"from": "2026-11-24T21:15:00Z", "to": "2026-11-25T21:15:00Z", "history_days": 0}
    measured, everything = lean["measured"], full["measured"]
    assert measured["bars"] == everything["bars"] == 12 * 60
    whole, all_of_it = measured["whole"], everything["whole"]
    # The same signals, from far less: the alert no rule maps is raised at half the bars, the plots at every alert's bar.
    assert whole["signal_list"] == all_of_it["signal_list"] and whole["signals"] > 2
    assert whole["alerts_answered"] == whole["signals"] < all_of_it["alerts_answered"] / 5
    assert whole["plots_answered"] == 0 < all_of_it["plots_answered"]
    assert whole["runs"][-1]["answered_bytes"] < all_of_it["runs"][-1]["answered_bytes"] / 10
    assert len(whole["runs"]) == 2 and all(run["milliseconds"] >= 0 and run["seconds"] > 0 for run in whole["runs"])
    # The engine's way: a run held, its last bars added one at a time, then checked against a whole run.
    for continued in (measured["continued"], everything["continued"]):
        assert continued["continued"] is True and continued["whole"] is None
        assert continued["appends"]["bars"] == 40
        assert continued["check"]["compared"] == 40 and continued["check"]["same"] is True
    # The cut-down copy: what a rule's slice would be, set against the script it was cut from.
    against = lean["against"]
    assert against["signals"]["same"] is True and against["signals"]["first"] == whole["signals"]
    assert against["measured"]["whole"]["signal_list"] == whole["signal_list"]
    # Where the time went, with the script's own code told from PineTS's.
    groups = lean["profile"]["groups"]
    assert RUNTIME in groups and SCRIPT in groups and lean["profile"]["milliseconds"] > 0
    assert lean["profile"]["functions"] and all(entry["function"] for entry in lean["profile"]["functions"])


def _short_day(tmp_path) -> list[str]:
    scripts = ScriptStore(str(tmp_path / "ta"))
    script = scripts.create("Cross", CROSS)
    rules = RuleStore(scripts)
    rule = rules.create({
        "name": "Cross on US100", "asset": {"provider": "capital", "key": "US100"}, "side": "bid",
        "bars": {"type": "time", "timeframe": "1"}, "script": {"id": script["id"], "version": 1}, "inputs": [],
        "alerts": [{"title": "Up", "action": "long"}], "indice": "us100", "history_days": 0,
    }, rules.load()["revision"])["rule"]
    _recorded(tmp_path / "recorded", datetime(2026, 11, 25, 6, tzinfo=timezone.utc), 1)
    return ["--rule", rule["id"], "--day", "2026-11-25", "--root", str(tmp_path / "ta"), "--files", str(tmp_path / "recorded"),
            "--restart-time", "21:15"]


def test_the_benchmark_says_when_there_is_no_node_to_run_the_runner_with(tmp_path, capsys):
    assert bench.main([*_short_day(tmp_path), "--node", "no-such-node"]) == 1
    assert "Node, or the Pine runner" in capsys.readouterr().err


@pytest.mark.skipif(not os.path.isfile(RUNNER_PATH) or _node_major() < 22, reason="the runner is not built, or no Node 22+ is here")
def test_the_benchmark_refuses_a_day_shorter_than_the_bars_it_is_to_add(tmp_path, capsys):
    assert bench.main(_short_day(tmp_path)) == 1
    # Sixty one-minute bars, where two hundred are to be added one at a time: said before any runner is started.
    assert "fewer than the 200 to add one at a time" in capsys.readouterr().err
