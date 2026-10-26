"""
The research lab (lab/): what a spec asks, the experiment journal and its
lock box, the configuration the lab's admin runs with, the scripts it keeps,
SQL over the research files, and the report of an experiment. Nothing here
needs a database: tests/test_lab_research_roundtrip.py is the one that does.
"""

import gzip
import json
import os
import sys
import types
from datetime import date

import pytest

asyncpg_stub = types.ModuleType("asyncpg")
asyncpg_stub.Pool = type("Pool", (), {})
asyncpg_stub.Record = dict
sys.modules.setdefault("asyncpg", asyncpg_stub)

from lab import config, experiments, paths, report, runs, scripts  # noqa: E402
from lab import sql as lab_sql  # noqa: E402
from lab.__main__ import main  # noqa: E402

STRATEGY = """//@version=6
indicator("EMA cross", overlay = true)
fastLength = input.int(9, "Fast length", minval = 1)
fast = ta.ema(close, fastLength)
alertcondition(ta.crossover(close, fast), "Cross up", '{"action": "long"}')
"""

SPEC = {
    "name": "EMA cross",
    "script_file": "ema_cross.pine",
    "from": "2026-10-13",
    "to": "2026-10-16",
    "indice": "us100",
    "source": {
        "kind": "pine", "asset": {"provider": "capital", "key": "US100"}, "side": "bid", "bars": {"type": "time", "timeframe": "1"},
        "inputs": [{"id": "in_0", "value": 9}], "alerts": [{"title": "Cross up", "action": "long"}],
    },
    "changes": {},
}


@pytest.fixture(autouse=True)
def lab_root(tmp_path, monkeypatch):
    monkeypatch.setenv("WATA_LAB_ROOT", str(tmp_path / "lab"))
    return tmp_path / "lab"


@pytest.fixture
def strategy(tmp_path):
    path = tmp_path / "ema_cross.pine"
    path.write_text(STRATEGY)
    return path


# ── a spec ──


def test_a_spec_runs_its_file_as_a_version_of_the_lab_s_script_and_the_command_line_changes_it(tmp_path, strategy):
    made = experiments.new("EMA", "Crosses point the way", "ema-cross")

    body, noted = runs.request_of(
        SPEC, base_dir=tmp_path, sets=["stoploss_percent=-10", "trailing_enabled=off"], inputs=["in_0=12", "in_1=30"],
        first="2026-10-14", experiment=made["experiment"],
    )

    assert "script_file" not in body and "experiment" not in body
    assert body["source"]["script"] == {"id": noted["script"]["id"], "version": 1}
    assert body["changes"] == {"stoploss_percent": -10, "trailing_enabled": False}
    assert body["source"]["inputs"] == [{"id": "in_0", "value": 12}, {"id": "in_1", "value": 30}]
    assert body["from"] == "2026-10-14" and body["to"] == "2026-10-16"
    assert body["tags"] == [f"exp:{made['experiment']}", "family:ema-cross"]
    # The spec itself is left as it was.
    assert SPEC["source"]["inputs"] == [{"id": "in_0", "value": 9}] and SPEC["changes"] == {}

    # The file changed: its next version; unchanged again: the same version.
    strategy.write_text(STRATEGY.replace("9", "10"))
    again, _ = runs.request_of(SPEC, base_dir=tmp_path)
    same, _ = runs.request_of(SPEC, base_dir=tmp_path)
    assert again["source"]["script"]["version"] == 2 and same["source"]["script"]["version"] == 2


def test_the_lock_box_keeps_its_days_out_unless_asked_for_and_says_so(tmp_path, strategy):
    experiments.set_lockbox(date(2026, 10, 16))

    with pytest.raises(ValueError, match="lock box"):
        runs.request_of(SPEC, base_dir=tmp_path)
    before, noted = runs.request_of(SPEC, base_dir=tmp_path, last="2026-10-15")
    assert before["to"] == "2026-10-15" and noted["lockbox"] is False
    _, noted = runs.request_of(SPEC, base_dir=tmp_path, allow_lockbox=True)
    assert noted["lockbox"] is True

    experiments.clear_lockbox()
    assert experiments.lockbox_from() is None


def test_the_axes_of_a_sweep_and_the_trials_they_make():
    axes = runs.axes_of(["setting:stoploss_percent=-5,-10,-15", "input:in_0=8,12"])
    assert axes == [
        {"kind": "setting", "name": "stoploss_percent", "values": [-5, -10, -15]},
        {"kind": "input", "name": "in_0", "values": [8, 12]},
    ]
    assert runs.trials_of({"sweep": {"axes": axes}}) == 6 and runs.trials_of({}) == 1
    with pytest.raises(ValueError):
        runs.axes_of(["stoploss_percent=-5"])


FINISHED = {
    "id": "20261020-100000-aaaaaa", "kind": "run", "name": "EMA cross", "tags": ["exp:abc123", "family:ema-cross"],
    "from": "2026-10-13", "to": "2026-10-16", "indice": "us100", "status": {"state": "done"},
    "request": {
        "kind": "run", "name": "EMA cross", "tags": ["exp:abc123", "family:ema-cross"], "from": "2026-10-13", "to": "2026-10-16",
        "indice": "us100", "changes": {"stoploss_percent": -10},
        "source": {"kind": "pine", "from_rule": None, "rule": {
            "id": "replay", "name": "EMA cross", "asset": {"provider": "capital", "key": "US100"}, "side": "bid",
            "bars": {"type": "time", "timeframe": "1"}, "script": {"id": "0123456789ab", "version": 3, "sha256": "f" * 64},
            "libraries": [], "inputs": [{"id": "in_0", "var": "fastLength", "title": "Fast length", "value": 9}],
            "alerts": [{"title": "Cross up", "action": "long"}], "alert_calls": "ignore", "confidence_plot": None,
            "indice": "us100", "mode": "shadow",
        }},
    },
    "settings": {"settings": {"stoploss_percent": -10.0, "max_trades_per_day": 5}},
}


def test_a_finished_run_is_asked_again_as_the_form_asks_and_its_values_have_neighbours():
    body = runs.body_of(FINISHED)
    assert body["source"] == {
        "kind": "pine", "script": {"id": "0123456789ab", "version": 3}, "asset": {"provider": "capital", "key": "US100"},
        "side": "bid", "bars": {"type": "time", "timeframe": "1"},
        "inputs": [{"id": "in_0", "var": "fastLength", "title": "Fast length", "value": 9}],
        "alerts": [{"title": "Cross up", "action": "long"}], "alert_calls": "ignore", "confidence_plot": None,
    }
    assert (body["changes"], body["from"], body["indice"]) == ({"stoploss_percent": -10}, "2026-10-13", "us100")
    assert runs.experiment_of(FINISHED) == "abc123"

    assert runs.value_of(FINISHED, "in_0") == 9 and runs.value_of(FINISHED, "stoploss_percent") == -10.0
    with pytest.raises(ValueError):
        runs.value_of(FINISHED, "in_4")
    # A whole number moves by whole steps, a tenth of it at least one; a decimal by a tenth of it.
    assert runs.neighbour_values(9) == [7, 8, 9, 10, 11]
    assert runs.neighbour_values(-10.0) == [-12.0, -11.0, -10.0, -9.0, -8.0]
    assert runs.neighbour_values(5, step=2, count=1) == [3, 5, 7]
    # Kept within the setting's limits.
    assert runs.neighbour_values(1, count=2, least=0, most=1000) == [0, 1, 2, 3]
    with pytest.raises(ValueError):
        runs.neighbour_values(True)


def figures(total: float, trades: int = 4) -> dict:
    return {"total_percent": total, "trades": trades, "expectancy": total / trades, "worst_drawdown": -1.0}


def test_a_stress_reads_as_a_table_of_later_and_dearer_fills_and_neighbours_as_a_plateau_or_a_peak():
    stressed = {"id": "20261020-110000-bbbbbb", "name": "Stress: EMA cross", "status": {"state": "done"}, "stress_of": FINISHED["id"]}
    later, dearer = [1.0, 3.0, 6.0, 16.0], [0.0, 0.01, 0.02, 0.05]
    totals = [round(4.0 - row - 30 * price, 2) for row in range(4) for price in dearer]
    sweep = {"axes": [{"kind": "setting", "name": "order_seconds", "values": later},
                      {"kind": "setting", "name": "slippage", "values": dearer}],
             "cells": [{"values": [row, price], "whole": figures(total)} for (row, price), total in
                       zip([(row, price) for row in later for price in dearer], totals)]}
    shown = runs.stress_summary(stressed, sweep)
    assert "of 20261020-100000-aaaaaa" in shown and "+0.05 €" in shown and "16 s" in shown
    assert f"above zero in {sum(1 for total in totals if total > 0)} of 16" in shown
    assert "one step worse on both: +2.70" in shown

    def around(values_totals):
        return {"axes": [{"kind": "input", "name": "in_0", "values": [value for value, _ in values_totals]}],
                "cells": [{"values": [value], "whole": figures(total)} for value, total in values_totals]}

    plateau = runs.neighbours_summary(FINISHED, around([(7, 4.0), (8, 4.5), (9, 5.0), (10, 4.6), (11, 4.2)]), 9)
    assert "← the run" in plateau and "a plateau" in plateau
    peak = runs.neighbours_summary(FINISHED, around([(7, -1.0), (8, 0.5), (9, 5.0), (10, 0.2), (11, -2.0)]), 9)
    assert "a peak: the value is fitted" in peak and "2 of them nothing or a loss" in peak


def test_the_edge_of_a_run_reads_as_a_table_by_horizon_or_split():
    def horizon(minutes, mean):
        return {"minutes": minutes, "signals": 40, "mean_bp": mean, "median_bp": mean, "hit_rate": 55.0, "best_bp": 9.0,
                "worst_bp": -7.0, "independent": 30, "t": 2.1, "random": {"mean_bp": 0.2, "hit_rate": 50.0, "beaten": 97.5, "draws": 20}}

    def group(name, value):
        return {name: value, **{str(minutes): {"signals": 20, "mean_bp": 1.5, "hit_rate": 60.0} for minutes in (1, 5, 15, 30, 60)}}

    edge = {"horizons": [horizon(minutes, minutes / 10) for minutes in (1, 5, 15, 30, 60)], "draws": 20, "unpriced": 2,
            "by_hour": [group("hour", 15), group("hour", 16)], "by_direction": [group("direction", "long")], "by_fate": []}
    shown = runs.edge_summary(FINISHED, edge)
    assert "beats %" in shown and "97.5" in shown and "2 signals without a price" in shown
    by_hour = runs.edge_summary(FINISHED, edge, "hour")
    assert "15 min" in by_hour and "right % 15 min" in by_hour
    assert "edge of the entries: 1 min 0.1bp (t 2.1, beats random 97.5%)" in runs.summary({
        **FINISHED, "summary": {"edge": {"horizons": [{"minutes": 1, "signals": 4, "mean_bp": 0.1, "t": 2.1, "beaten": 97.5}]}},
    })


def test_the_days_of_a_run_read_with_their_kind_and_the_calendar_says_how_far_it_knows():
    days = [
        {"day": "2026-10-13", "status": "tested", "day_percent": 1.2, "trades": 2, "regime": {
            "weekday": "Tuesday", "news": [], "gap_percent": 0.4, "range_percent": 1.1, "volatility_percent": 0.9,
            "day_type": "Trend up", "us_open_move_percent": 0.3, "labels": {"volatility": "Agitated"}}},
        {"day": "2026-10-14", "status": "tested", "day_percent": -0.5, "trades": 1, "regime": {
            "weekday": "Wednesday", "news": ["CPI"], "gap_percent": -0.1, "range_percent": 0.6, "volatility_percent": 0.5,
            "day_type": "Back and forth", "us_open_move_percent": None, "labels": {"volatility": "Calm"}}},
    ]
    breakdowns = {"groups": {"news": [{"group": "CPI", "trade_count": 1, "win_rate": 0, "average_percent": -0.5, "profit_loss": -12.0},
                                      {"group": "PCE", "trade_count": 0, "win_rate": None, "average_percent": None, "profit_loss": 0}]}}
    shown = runs.regimes_summary(FINISHED, days, breakdowns)
    assert "Trend up" in shown and "Wed" in shown and "trades by news:" in shown and "PCE" not in shown

    ok, said = runs.calendar_check(date(2026, 10, 20))
    assert ok and "PCE    known from 2024-01-26 until 2027-12-22" in said and "PPI    known from 2026-11-13" in said
    # JOLTS runs out first, but a rule names it only by choice: said, judged when named.
    ok, said = runs.calendar_check(date(2026, 11, 15))
    assert ok and "JOLTS  known from 2026-11-03 until 2026-12-01  ← short, judged when named" in said
    assert not runs.calendar_check(date(2026, 11, 15), events=("CPI", "JOLTS"))[0]
    late, said = runs.calendar_check(date(2027, 11, 20))
    assert not late and "add the next dates" in said


def test_the_measure_of_the_turbos_and_its_check_read_in_a_few_lines():
    model = {"state": "done", "underlying": "dukascopy/us100", "error": None, "measure": {
        "made_at": "2026-10-20T01:00:00Z", "days": 6, "first": "2026-10-13", "last": "2026-10-20", "few_days": False,
        "directions": {"long": {"factor": 0.0086, "offset": -2.0, "spacing": 50.0, "turbos": 40, "r2": 0.998,
                                "error_eur": 0.02, "error_percent": 1.2}}}}
    shown = runs.measure_summary(model)
    assert "6 recorded days (2026-10-13 to 2026-10-20)" in shown and "0.0086" in shown
    assert "not measured yet" in runs.measure_summary({"state": "idle", "underlying": "dukascopy/us100", "measure": None})

    from src.backtest.turbo_model import compare_trades

    trade = lambda signal, result: {"signal_id": signal, "performance_percent": result, "exit": {"reason": "x"}, "entry": {"price": 5.0}}  # noqa: E731
    checked = runs.validation_summary(compare_trades([trade(1, 1.0), trade(2, -1.0), trade(3, 2.0)],
                                                     [trade(1, 1.5), trade(2, -0.5), trade(3, 1.0)]))
    assert "3 trades paired by signal" in checked and "the same exit: 100%" in checked


def test_a_replay_beside_the_real_trades_and_wata_s_orders_read_in_a_few_lines():
    from src.backtest.reconciliation import reconcile

    trade = {"id": 1, "signal_id": 1, "day": "2026-10-14", "direction": "long", "performance_percent": 2.0, "turbo": {"uic": 7},
             "entry": {"filled_at": "2026-10-14T07:00:05Z", "price": 5.02}, "exit": {"filled_at": "2026-10-14T08:00:00Z", "reason": "x"}}
    found = reconcile([trade], [{"id": 1, "source": "journal", "signal_uuid": "abcdef123", "fate": "executed"}],
                      [{"signal_uuid": "abcdef123", "position_id": "p", "outcome": "executed"}],
                      [{"position_id": "p", "execution_time_open": "2026-10-14T07:00:02Z", "position_open_price": 5.0,
                        "position_total_performance_percent": 1.5, "position_close_reason": "x", "instrument_uic": 7}])
    shown = runs.reconciliation_summary(FINISHED, found)
    assert "1 trades set beside" in shown and "abcdef12" in shown and "5.02/5.0" in shown

    spread = {"median": 0.5, "p90": 0.9, "count": 40}
    measured = {"from": "2026-07-21", "to": "2026-10-20", "signals": 40, "orders": 80, "requests": 82,
                "signal_delay_seconds": {"from_signal": spread, "from_receipt": spread}, "order_seconds": spread,
                "slippage": {side: {**spread, "median_ticks": 50} for side in ("buy", "sell", "all")},
                "refusals": {"share_percent": 2.4, "codes": {"TooFar": 2}, "by_hour": []},
                "suggested": {"order_seconds": 0.5, "slippage": 0.005}, "least": 10}
    said = runs.execution_summary(measured)
    assert "--set order_seconds=0.5, --set slippage=0.005" in said and "2 TooFar" in said


def test_the_paper_days_of_the_rules_read_in_a_few_lines():
    rules = {"r00000000001": {"reference": {"run_id": "20261012-010000-cccccc", "name": "Cross", "days": 60, "low": -1.5, "high": 2.75},
                              "days": [{"day": "2026-10-19", "state": "done", "day_percent": 0.4, "trades": 2, "outside": False,
                                        "run_id": "20261020-010000-aaaaaa"}]}}
    shown = runs.paper_summary(rules)
    assert "r00000000001: 1 days, +0.40%" in shown and "-1.5% to 2.75%" in shown and "20261020-010000-aaaaaa" in shown
    assert "no rule traded on paper yet" in runs.paper_summary({})


def test_the_pine_runner_gets_a_recent_node_from_nvm_when_the_path_has_an_old_one(tmp_path, monkeypatch):
    from lab import admin

    def node(version: str) -> None:
        folder = tmp_path / ".nvm" / "versions" / "node" / f"v{version}" / "bin"
        folder.mkdir(parents=True)
        (folder / "node").write_text(f"#!/bin/sh\necho v{version}\n")
        (folder / "node").chmod(0o755)

    for version in ("20.18.0", "24.2.0", "24.21.0"):
        node(version)
    monkeypatch.setenv("PATH", f"{tmp_path / '.nvm/versions/node/v20.18.0/bin'}{os.pathsep}/usr/bin:/bin")

    found = admin.prefer_node(home=tmp_path)

    assert found == str(tmp_path / ".nvm/versions/node/v24.21.0/bin/node")
    assert os.environ["PATH"].startswith(str(tmp_path / ".nvm/versions/node/v24.21.0/bin"))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    assert admin.prefer_node(home=tmp_path / "nothing") is None


def test_a_sweep_says_how_choosing_again_each_window_did():
    sweep = {"axes": [{"kind": "input", "name": "in_0", "values": [8, 12]}], "held_out_from": "2026-10-15",
             "cells": [{"values": [8], "tuning": figures(2.0), "held_out": figures(-1.0), "whole": figures(1.0)},
                       {"values": [12], "tuning": figures(1.0), "held_out": figures(0.5), "whole": figures(1.5)}]}
    run = {"id": "20261020-120000-cccccc", "name": "EMA sweep", "status": {"state": "done"}, "summary": {
        "walk_forward": {"train_days": 5, "test_days": 2, "days": 4, "total_percent": 1.2, "best_in_hindsight_percent": 2.0,
                         "worst_drawdown": -0.8, "changes": 1, "windows": [
                             {"train": ["2026-10-01", "2026-10-07"], "test": ["2026-10-08", "2026-10-09"], "values": [8],
                              "train_percent": 2.0, "test_percent": -0.5, "test_rank": 2, "cells": 2}]},
        "pbo": {"sentence": "Choosing the best overfits in 40 % of the splits."},
    }}
    shown = runs.sweep_summary(run, sweep)
    assert "out of sample +1.20% over 4 days" in shown and "known afterwards: +2.00%" in shown
    assert "2026-10-01..2026-10-07" in shown and "2/2" in shown and "overfits in 40 %" in shown
    run["summary"] = {"walk_forward": None}
    assert "too few days for one window" in runs.sweep_summary(run, sweep)


# ── the journal ──


def test_every_trial_of_a_family_is_counted_and_an_experiment_is_concluded():
    first = experiments.new("EMA 9/21", "Crosses point the way", "ema-cross")["experiment"]
    second = experiments.new("EMA on range bars", "Range bars filter the noise", "ema-cross")["experiment"]
    other = experiments.new("Opening gap", "Gaps close in the first hour", "gap")["experiment"]
    experiments.record_run(first, "20261020-100000-aaaaaa", kind="run", trials=1)
    experiments.record_run(first, "20261020-100100-bbbbbb", kind="sweep", trials=6)
    experiments.record_run(second, "20261020-100200-cccccc", kind="run", trials=1, lockbox=True)
    experiments.record_run(other, "20261020-100300-dddddd", kind="run", trials=1)
    experiments.conclude(first, "rejected", "Random entries do as well.")

    assert experiments.family_trials("ema-cross") == 8 and experiments.family_trials("gap") == 1
    found = experiments.get(first)
    assert (found["verdict"], found["notes"], found["trials"], len(found["runs"])) == ("rejected", "Random entries do as well.", 7, 2)
    assert experiments.get(second)["runs"][0]["lockbox"] is True
    with pytest.raises(ValueError):
        experiments.conclude(first, "great", "")
    with pytest.raises(ValueError):
        experiments.get("nothere")
    with pytest.raises(ValueError):
        experiments.new("", "x", "y")


# ── the lab's admin ──


def write_research_day(root, day: str, trade: dict | None = None) -> None:
    folder = root / "price_history" / "research" / "days" / day
    folder.mkdir(parents=True)
    (folder / "settings.json").write_text(json.dumps({"config": {"trade": trade or {}}, "backtest": {}}))
    (folder / "manifest.json").write_text(json.dumps({"day": day}))


def test_the_lab_s_admin_runs_with_the_server_s_rules_its_own_folders_and_nothing_under_app(lab_root):
    rules = [{"rule_type": "market_hours", "rule_config": {"trading_start_hour": 9, "trading_end_hour": 21}}]
    write_research_day(lab_root, "2026-10-15", {"rules": [], "config": {}})
    write_research_day(lab_root, "2026-10-16", {"rules": rules, "config": {"general": {"timezone": "Europe/Paris"}, "ta_engine": {"history_days": 2}}})

    built = config.build("postgresql://postgres:@/postgres?host=/tmp/x", port=8099, workers=3)

    assert built["postgresql"]["dsn"] == "postgresql://postgres:@/postgres?host=/tmp/x"
    assert built["trade"]["rules"] == rules
    # What the server's section does not say is the example's.
    assert built["trade"]["config"]["ta_engine"]["history_days"] == 2
    assert "buying_power" in built["trade"]["config"]
    admin = built["admin"]
    assert (admin["api_host"], admin["api_port"], admin["backtests_workers"], admin["cookie_secure"]) == ("127.0.0.1", 8099, 3, False)
    assert (admin["actions_enabled"], admin["config_edit_enabled"]) == (False, False)
    assert admin["persistant"]["backtests_path"] == str(paths.backtests())
    assert '"/app/' not in json.dumps(built)

    path = config.write("postgresql://x")
    assert json.loads(open(path).read())["admin"]["persistant"]["ta_path"] == str(paths.scripts())
    password = config.password()
    assert config.password() == password and len(password) >= 12
    assert oct(os.stat(paths.password_file()).st_mode & 0o777) == "0o600"


# ── scripts ──


def test_the_server_s_scripts_are_mirrored_and_never_changed_here(lab_root, strategy):
    from src.admin.ta_scripts import ScriptStore

    server = ScriptStore(str(lab_root / "server_ta"))
    made = server.create("Server cross", STRATEGY)
    (lab_root / "server_ta" / "rules.json").write_text(json.dumps({"rules": []}))

    assert scripts.import_server() == {"scripts": 1, "versions": 1, "rules": True}
    assert scripts.import_server() == {"scripts": 0, "versions": 0, "rules": True}
    listed = {entry["name"]: entry for entry in scripts.listed()}
    assert listed["Server cross"]["from"] == "server"
    with pytest.raises(ValueError, match="not changed here"):
        scripts.update(made["id"], strategy)
    assert scripts.resolve("Server cross") == (made["id"], 1)
    assert scripts.resolve(f"{made['id']}@1") == (made["id"], 1)
    with pytest.raises(ValueError):
        scripts.resolve("Nothing")

    added = scripts.add(strategy, "Lab cross")
    strategy.write_text(STRATEGY.replace("9", "11"))
    assert scripts.update(added["id"], strategy)["version"] == 2
    assert {entry["name"]: entry["from"] for entry in scripts.listed()}["Lab cross"] == "lab"


# ── SQL over the files ──


def write_table(folder, name: str, text: str) -> None:
    with gzip.open(folder / f"{name}.csv.gz", "wt") as out_file:
        out_file.write(text)


def test_sql_reads_every_day_of_a_table_at_once_and_the_newest_copy_of_the_instruments(lab_root):
    days = lab_root / "price_history" / "research" / "days"
    for day, close, symbol in (("2026-10-15", 5.0, "OLD"), ("2026-10-16", 6.0, "NEW")):
        folder = days / day
        folder.mkdir(parents=True)
        write_table(folder, "price_bar_10s", f"ts,instrument_id,bid_close\n{day} 08:00:00+00,2,{close}\n{day} 08:00:10+00,2,{close + 1}\n")
        write_table(folder, "price_instrument", f"id,symbol\n2,{symbol}\n")

    result = lab_sql.query(
        "SELECT i.symbol, count(*) AS bars, max(b.bid_close) AS best FROM price_bar_10s b JOIN price_instrument i ON i.id = b.instrument_id GROUP BY 1"
    )

    assert result == {"columns": ["symbol", "bars", "best"], "rows": [["NEW", 4, 7.0]], "more": False}
    assert "symbol" in lab_sql.as_text(result) and json.loads(lab_sql.as_json(result))["rows"] == [["NEW", 4, 7.0]]


# ── the report of an experiment ──


def run_of(run_id: str, name: str, curve: list | None, total: float | None) -> dict:
    return {
        "id": run_id, "name": name, "from": "2026-10-13", "to": "2026-10-16", "kind": "run",
        "settings": {"changed": {"stoploss_percent": -10}},
        "summary": {
            "figures": {"trades": 4, "win_rate": 75.0, "expectancy": 1.2, "total_percent": total, "worst_drawdown": -0.5},
            "money": {"profit_loss": 120.0}, "against_random": {"beaten": 97.0},
            "curve": [{"day": day, "cumulative": value} for day, value in curve or []],
        },
    }


def test_the_report_of_an_experiment_draws_each_run_in_the_colour_of_its_row_and_escapes_what_it_shows():
    experiment = experiments.new("EMA <cross>", "Crosses & gaps", "ema-cross")
    found = experiments.get(experiment["experiment"])
    page = report.render(found, [
        run_of("20261020-100000-aaaaaa", "Without days", None, None),
        run_of("20261020-100100-bbbbbb", "Stop at 10", [("2026-10-13", 1.0), ("2026-10-14", 2.5)], 2.5),
    ])

    assert page.startswith("<title>EMA &lt;cross&gt;</title>")
    assert "Crosses &amp; gaps" in page and "<cross>" not in page
    # The second row's colour for the one run that has days.
    assert 'stroke="var(--line-2)"' in page and 'stroke="var(--line-1)"' not in page
    assert "+2.50%" in page and "97%" in page and "€120" in page
    assert "@media (prefers-color-scheme: dark)" in page and ':root[data-theme="dark"]' in page
    assert report.curves_svg([(0, [("2026-10-13", 1.0)])]) == '<p class="empty">Too few days to draw a curve.</p>'


# ── the command line ──


def test_the_command_line_keeps_the_journal_and_the_lock_box(capsys):
    assert main(["exp", "new", "--title", "Gap", "--hypothesis", "Gaps close", "--family", "gap"]) == 0
    assert main(["exp", "list", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out.split("\n", 1)[1])
    assert listed[0]["title"] == "Gap"
    assert main(["lockbox", "set", "2026-11-02"]) == 0
    assert "the days from 2026-11-02" in capsys.readouterr().out
    # Configurations screened outside the lab's runs count in the family.
    assert main(["exp", "trials", listed[0]["id"], "--count", "35", "--notes", "A Python port, 12 Jun to 16 Oct"]) == 0
    assert "35 in the family gap" in capsys.readouterr().out
    assert main(["exp", "trials", listed[0]["id"], "--notes", "No count"]) == 2
    assert "from 1 to 100,000" in capsys.readouterr().err
    # Only the verdicts the journal knows.
    with pytest.raises(SystemExit):
        main(["exp", "conclude", listed[0]["id"], "--verdict", "fine"])
    assert main(["exp", "conclude", listed[0]["id"], "--verdict", "rejected", "--notes", "Gaps did not close"]) == 0
    assert experiments.get(listed[0]["id"])["verdict"] == "rejected"
    assert main(["exp", "show", "nothere"]) == 2
    assert "No experiment" in capsys.readouterr().err


# ── a dump restored here ──


def test_the_lab_counts_the_tables_the_server_counts():
    from lab.backup import TRADING_TABLES

    script = open("deploy/pg_backup.sh").read()
    listed = script.split('TRADING_TABLES="', 1)[1].split('"', 1)[0].split()

    assert tuple(listed) == TRADING_TABLES


def test_the_lab_takes_the_versions_of_age_and_rclone_the_servers_image_takes():
    image = open("deploy/pg_backup.Dockerfile").read()
    tools = open("lab/backup_tools.sh").read()

    for name in ("AGE_VERSION", "RCLONE_VERSION"):
        version = image.split(f"ARG {name}=", 1)[1].split()[0]
        assert f"{name}={version}" in tools, name
    assert tools.count("shasum -a 256 -c -") == 2


def test_the_newest_dump_and_the_newest_copy_are_found_by_their_day(tmp_path):
    from lab.backup import newest_local, newest_sealed

    for name in ("wata-2026-10-24.dump", "wata-2026-10-25-full.dump", "wata-2026-10-23.dump", "notes.txt"):
        (tmp_path / name).write_text("x")

    assert newest_local(tmp_path).name == "wata-2026-10-25-full.dump"
    assert newest_local(tmp_path / "nothing") is None
    assert newest_sealed({"nightly": ["wata-2026-10-26.dump.age", "wata-2026-10-24.dump.age"],
                          "weekly": ["wata-2026-10-25-full.dump.age", "readme.txt"]}) == "nightly/wata-2026-10-26.dump.age"
    assert newest_sealed({"nightly": [], "weekly": []}) is None


class Done:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def test_the_newest_copy_is_downloaded_and_opened_with_the_key_kept_here(tmp_path, monkeypatch):
    from lab import backup

    key = tmp_path / "backup-age.key"
    key.write_text("AGE-SECRET-KEY-1...")
    ran = []

    def run(command, capture_output, text):
        ran.append(command)
        if command[1] == "lsf":
            return Done(stdout="wata-2026-10-26.dump.age\nwata-2026-10-24.dump.age\n") if "nightly" in command[-1] \
                else Done(returncode=3, stderr="directory not found")
        if command[1] == "copyto":
            with open(command[-1], "w") as sealed:
                sealed.write("sealed")
        return Done()

    monkeypatch.setattr(backup, "tool", lambda name: f"/bin/{name}")
    monkeypatch.setattr(backup, "restore_and_count", lambda dump, folder, run: {
        table: (3 if table == "turbo_data_position" else 0) for table in backup.TRADING_TABLES})

    ok, shown = backup.check(offsite=True, remote="wata-offsite:wata-backups/wata/", identity=str(key), run=run)

    assert ok and shown.splitlines()[0] == "Restored wata-2026-10-26.dump.age, from wata-offsite:wata-backups/wata:"
    assert "  turbo_data_position: 3" in shown
    assert ran[0] == ["/bin/rclone", "lsf", "--files-only", "wata-offsite:wata-backups/wata/nightly/"]
    assert ran[2][:3] == ["/bin/rclone", "copyto", "wata-offsite:wata-backups/wata/nightly/wata-2026-10-26.dump.age"]
    assert ran[3][:4] == ["/bin/age", "--decrypt", "--identity", str(key)]


def test_a_check_says_what_it_lacks(tmp_path, monkeypatch):
    from lab import backup

    monkeypatch.delenv("WATA_BACKUP_REMOTE", raising=False)
    monkeypatch.setattr(backup, "tool", lambda name: f"/bin/{name}")

    with pytest.raises(backup.BackupError, match="WATA_BACKUP_REMOTE"):
        backup.check(offsite=True)
    with pytest.raises(backup.BackupError, match="run lab/sync.sh"):
        backup.check()
    with pytest.raises(backup.BackupError, match="No age key at"):
        backup.decrypt(tmp_path / "wata-2026-10-26.dump.age", tmp_path / "missing.key", tmp_path)


def test_a_dump_is_restored_into_a_server_of_its_own_and_counted(tmp_path):
    # The lab's own library (requirements-lab.txt): imported only through importorskip, never by name.
    pgserver = pytest.importorskip("pgserver")
    import subprocess

    from lab.backup import restore_and_count

    pg_dump = pgserver._commands.POSTGRES_BIN_PATH / "pg_dump"

    source = pgserver.get_server(tmp_path / "source", cleanup_mode="delete")
    try:
        source.psql("CREATE TABLE turbo_data_position (position_id text); INSERT INTO turbo_data_position VALUES ('a'), ('b');")
        dump = tmp_path / "wata-2026-10-26.dump"
        subprocess.run([str(pg_dump), "-Fc", "-f", str(dump), source.get_uri()], check=True)
    finally:
        source.cleanup()

    counts = restore_and_count(dump, tmp_path)

    assert counts["turbo_data_position"] == 2 and counts["signal_journal"] is None
