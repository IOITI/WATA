"""
The backtest's command from its folder to its files, on recorded days kept as
files and a stand-in for the Pine runner (tests/test_ta_replay.py).
"""

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
sys.modules.setdefault("aio_pika", types.ModuleType("aio_pika"))

from src.backtest import run as backtest_run  # noqa: E402
from src.backtest.settings import from_config  # noqa: E402
from tests.test_ta_replay import fake_runner, index_rows, rule_on, write_rows  # noqa: E402

with open("etc/config_example.json", encoding="utf-8") as config_file:
    EXAMPLE = json.load(config_file)

# Tuesday 13 and Wednesday 14 October 2026; the 12th is the Monday before, recorded too.
DAYS = (date(2026, 10, 12), date(2026, 10, 13), date(2026, 10, 14))


def turbo_rows(day: date, instrument_id: int, direction: str, barrier: float) -> list[dict]:
    """A turbo following the index of ``index_rows``: a point of index for a hundredth of its price."""
    rows = []
    for index in index_rows(day, 6, 20):
        level = index["bid_close"]
        price = round((level - barrier if direction == "long" else barrier - level) / 100, 2)
        rows.append({
            **index, "uic": 40_000_000 + instrument_id, "asset_type": "WarrantOpenEndKnockOut", "symbol": f"T{instrument_id}",
            "description": f"Turbo {direction} {barrier:g}", "role": "turbo", "direction": direction, "barrier": barrier,
            "issuer": "SG", "bid_open": price, "bid_high": price, "bid_low": price, "bid_close": price,
            "ask_open": price + 0.02, "ask_high": price + 0.02, "ask_low": price + 0.02, "ask_close": price + 0.02,
            "instrument_id": instrument_id,
        })
    return rows


@pytest.fixture
def recorded(tmp_path):
    files = tmp_path / "recorded"
    rows = []
    for day in DAYS[1:]:
        rows += index_rows(day, 6, 20) + turbo_rows(day, 11, "long", 24_500) + turbo_rows(day, 12, "short", 25_500)
    # The Monday: the index only, the recorder had no turbo yet.
    rows += index_rows(DAYS[0], 6, 20)
    write_rows(str(files / "saxo" / "recorded.csv.gz"), rows)
    return str(files)


def prepare(tmp_path, request: dict, settings_changes: dict | None = None, journal: list | None = None) -> str:
    folder = tmp_path / "run"
    (folder / "sources").mkdir(parents=True)
    (folder / "sources" / "aaaaaaaaaaaa-v1.pine").write_text("//@version=6\nindicator(\"Hours\")\n")
    settings = from_config(EXAMPLE).to_dict()
    settings.update({"trailing_enabled": False, "time_scaling_enabled": False, "confidence_enabled": False,
                     "day_profit_target": 50.0, "no_entry_above_day_profit": 50.0, "cooldown_after_loss_minutes": 0,
                     "max_trades_per_day": 0, "capital": 10_000.0, **(settings_changes or {})})
    (folder / "settings.json").write_text(json.dumps({"settings": settings, "changed": {}, "live_before": {}}))
    (folder / "request.json").write_text(json.dumps({
        "format": 1, "kind": "run", "name": "Hours", "from": DAYS[0].isoformat(), "to": DAYS[-1].isoformat(),
        "indice": "us100", "chart": {"asset": {"provider": "saxo", "key": "1"}, "side": "bid", "bars": {"type": "time", "timeframe": "1"}},
        **request,
    }))
    if journal is not None:
        (folder / "journal.json").write_text(json.dumps(journal))
    return str(folder)


def run_command(tmp_path, folder: str, recorded: str) -> int:
    command, _ = fake_runner(tmp_path)
    return backtest_run.main([
        "--run", folder, "--data", f"files:{recorded}", "--prices", str(tmp_path / "prices"),
        "--runner", "\x1f".join(command),
    ])


def read(folder: str, name: str):
    with open(os.path.join(folder, name), encoding="utf-8") as in_file:
        return json.load(in_file)


def test_a_pine_script_is_traded_on_the_recorded_turbos_day_by_day(tmp_path, recorded):
    folder = prepare(tmp_path, {"source": {"kind": "pine", "rule": rule_on()}, "baseline": {"repetitions": 5, "seed": 3}})

    assert run_command(tmp_path, folder, recorded) == 0

    days = read(folder, "days.json")
    signals = read(folder, "signals.json")
    trades = read(folder, "trades.json")
    summary = read(folder, "summary.json")
    assert [(day["day"], day["status"]) for day in days] == [
        ("2026-10-12", "no_recorded_turbos"), ("2026-10-13", "tested"), ("2026-10-14", "tested"),
    ]
    # Monday's signals had nothing to be traded on.
    monday = [signal for signal in signals if signal["day"] == "2026-10-12" and signal["source"] == "pine"]
    assert monday and all(signal["fate"] in ("unpriced", "ignored", "refused") for signal in monday)
    assert any(signal["rule"] == "no_recorded_turbo" for signal in monday)
    # A long at each even hour (UTC), a short at each odd one, everything closed at a quarter to.
    tuesday = [signal for signal in signals if signal["day"] == "2026-10-13" and signal["source"] == "pine"]
    first = tuesday[0]
    assert (first["action"], first["bar_time"], first["fate"]) == ("long", "2026-10-13T06:00:00Z", "executed")
    assert first["arrived_at"] == "2026-10-13T06:01:03Z"
    traded = next(trade for trade in trades if trade["id"] == first["trade_id"])
    assert traded["turbo"]["instrument_id"] == 11 and traded["exit"]["reason"] == "Explicit Close (All)"
    assert traded["entry"]["asset_price"] is not None
    # The trading day ends at the scheduler's close.
    assert any(signal["source"] == "schedule" for signal in signals)
    assert summary["days"]["tested"] == 2 and summary["figures"]["trades"] == len(
        [trade for trade in trades if trade["day"] != "2026-10-12"]
    )
    assert summary["figures"]["sample"] == "too_few"
    assert summary["money"]["capital"] == 10_000.0
    assert [point["day"] for point in summary["curve"]] == ["2026-10-13", "2026-10-14"]
    assert summary["against_random"]["repetitions"] == 5
    assert summary["made_with"]["engine"]["runner"] == {"pinets": "fake", "node": "fake"}
    assert summary["warnings"] == ["made up"]
    # Two days: the risk figures that need more say nothing, the totals without the best day do.
    risk = summary["figures"]["risk"]
    assert risk["trades_per_day"] == round(summary["figures"]["trades"] / 2, 2)
    assert risk["sharpe"] is None and risk["calmar"] is None
    assert summary["stress"]["monte_carlo"] is None
    assert [kept["removed"] for kept in summary["stress"]["without_best_days"]] == [1]
    # How the index moved after each entry signal, the Monday's included: they are signals all the same.
    edge = read(folder, "edge.json")
    entries = [signal for signal in signals if signal["source"] == "pine" and signal["action"] in ("long", "short")]
    assert len(edge["signals"]) + edge["unpriced"] == len(entries)
    assert [row["minutes"] for row in summary["edge"]["horizons"]] == [1, 5, 15, 30, 60]
    assert {row["fate"] for row in edge["by_fate"]} <= {"executed", "not_taken"}
    # Each day's kind, from the index in the trading hours; the Tuesday had a CPI.
    tuesday = next(day for day in days if day["day"] == "2026-10-13")
    assert tuesday["regime"]["weekday"] == "Tuesday" and tuesday["regime"]["bars"] > 0
    assert days[0]["regime"]["gap_percent"] is None and tuesday["regime"]["gap_percent"] is not None
    assert next(day for day in days if day["day"] == "2026-10-14")["regime"]["labels"]["news"] == "CPI"
    assert set(edge["by_day"]) == {"day_type", "gap", "news", "us_open", "volatility"}
    assert [row["group"] for row in edge["by_day"]["news"]] == ["CPI", "No news"]
    assert all(trade["session_part"] and isinstance(trade["minutes_after_open"], int) for trade in trades)
    assert summary["made_with"]["calendar"]["until"] == "2026-12-23"
    baseline = read(folder, "baseline.json")
    assert len(baseline["totals"]) == 5 and baseline["seed"] == 3
    assert read(folder, "progress.json")["phase"] == "done"


def test_the_same_run_gives_the_same_files(tmp_path, recorded):
    first = prepare(tmp_path, {"source": {"kind": "pine", "rule": rule_on()}, "baseline": {"repetitions": 3, "seed": 7}})
    assert run_command(tmp_path, first, recorded) == 0
    kept = {name: read(first, name) for name in ("signals.json", "trades.json", "baseline.json")}
    os.rename(first, str(tmp_path / "first"))

    second = prepare(tmp_path, {"source": {"kind": "pine", "rule": rule_on()}, "baseline": {"repetitions": 3, "seed": 7}})
    assert run_command(tmp_path, second, recorded) == 0

    assert {name: read(second, name) for name in kept} == kept


def test_the_signals_wata_received_are_replayed_with_what_it_did_of_them(tmp_path, recorded):
    journal = [
        {"signal_uuid": "a", "action": "long", "indice": "us100", "confidence": 0.9,
         "signal_timestamp": "2026-10-13T06:59:00Z", "received_timestamp": "2026-10-13T07:00:01Z",
         "decided_at": "2026-10-13T07:00:02Z", "outcome": "executed", "reason": None, "position_id": "p-a"},
        {"signal_uuid": "b", "action": "close-position", "indice": "us100", "confidence": None,
         "signal_timestamp": "2026-10-13T08:00:00Z", "received_timestamp": "2026-10-13T08:00:01Z",
         "decided_at": "2026-10-13T08:00:02Z", "outcome": "closed", "reason": "Closed 1 position(s). Errors: 0"},
        # The scheduler's close: the run closes at its own time instead.
        {"signal_uuid": "c", "action": "close-position", "indice": "n/a", "confidence": None,
         "signal_timestamp": "2026-10-13T19:55:00Z", "received_timestamp": "2026-10-13T19:55:00Z",
         "decided_at": "2026-10-13T19:55:01Z", "outcome": "ignored", "reason": None},
        # Another indice.
        {"signal_uuid": "d", "action": "long", "indice": "de40", "confidence": None,
         "signal_timestamp": "2026-10-13T09:00:00Z", "received_timestamp": "2026-10-13T09:00:01Z",
         "decided_at": "2026-10-13T09:00:02Z", "outcome": "refused", "reason": "Indice"},
    ]
    folder = prepare(tmp_path, {"source": {"kind": "journal", "signals": len(journal)}, "baseline": {"repetitions": 0}},
                     {"signal_delay_seconds": 0.5}, journal=journal)
    # What WATA did with the first: bought the same turbo, sold on the close signal.
    with open(os.path.join(folder, "positions.json"), "w", encoding="utf-8") as out_file:
        json.dump([{
            "position_id": "p-a", "execution_time_open": "2026-10-13T07:00:03Z", "execution_time_close": "2026-10-13T08:00:04Z",
            "position_open_price": 5.02, "position_close_price": 5.02, "position_total_performance_percent": 0.0,
            "position_close_reason": "Explicit Close (All)", "instrument_uic": 40_000_011,
        }], out_file)

    assert run_command(tmp_path, folder, recorded) == 0

    signals = [signal for signal in read(folder, "signals.json") if signal["source"] == "journal"]
    assert [(signal["signal_uuid"], signal["fate"], signal["live_outcome"]) for signal in signals] == [
        ("a", "executed", "executed"), ("b", "closed", "closed"),
    ]
    assert signals[0]["arrived_at"] == "2026-10-13T07:00:01Z"
    (trade,) = [trade for trade in read(folder, "trades.json") if trade["signal_id"] == signals[0]["id"]]
    assert trade["exit"]["reason"] == "Explicit Close (All)" and trade["confidence"] == 0.9
    assert read(folder, "summary.json")["against_random"] is None
    assert not os.path.exists(os.path.join(folder, "baseline.json"))
    reconciled = read(folder, "reconciliation.json")
    (pair,) = reconciled["pairs"]
    assert (pair["signal_uuid"], pair["position_id"], pair["same_turbo"], pair["same_exit"]) == ("a", "p-a", True, True)
    assert read(folder, "summary.json")["reconciliation"]["paired"] == 1


def test_a_sweep_trades_each_combination_and_holds_the_last_days_out(tmp_path, recorded):
    folder = prepare(tmp_path, {
        "kind": "sweep", "source": {"kind": "pine", "rule": rule_on()},
        "sweep": {"axes": [{"kind": "setting", "name": "stoploss_percent", "values": [-5, -15]},
                           {"kind": "input", "name": "in_0", "values": [12, 20]}], "held_out": 0.5,
                  "walk_forward": {"train_days": 1, "test_days": 1}, "trials_before": 6},
    })

    assert run_command(tmp_path, folder, recorded) == 0

    sweep = read(folder, "sweep.json")
    assert [cell["values"] for cell in sweep["cells"]] == [[-5, 12], [-5, 20], [-15, 12], [-15, 20]]
    assert sweep["held_out_from"] == "2026-10-14"
    assert sweep["cells"][0]["tuning"]["days"] == 1 and sweep["cells"][0]["held_out"]["days"] == 1
    assert sweep["cells"][0]["whole"]["days"] == 2
    _, log = fake_runner(tmp_path)
    with open(log, encoding="utf-8") as in_file:
        inputs = {json.dumps(json.loads(line)["inputs"]) for line in in_file}
    # One replay per value of the input, shared by the stop-losses.
    assert inputs == {json.dumps({"in_0": 12}), json.dumps({"in_0": 20})}
    summary = read(folder, "summary.json")
    assert summary["cells"] == 4
    # Each combination's days, for the windows: chosen on the 13th, kept on the 14th.
    assert [day for day, _, _ in sweep["cells"][0]["curve"]] == ["2026-10-13", "2026-10-14"]
    walk = summary["walk_forward"]
    assert [(window["train"], window["test"]) for window in walk["windows"]] == [
        (["2026-10-13", "2026-10-13"], ["2026-10-14", "2026-10-14"]),
    ]
    assert walk["windows"][0]["values"] in [cell["values"] for cell in sweep["cells"]]
    # Two days are too few to measure an overfitting or deflate a Sharpe ratio: nothing is said.
    assert summary["pbo"] is None and summary["deflated_sharpe"] is None


def test_a_stress_sweep_chooses_nothing_so_nothing_is_said_of_a_choice(tmp_path, recorded):
    folder = prepare(tmp_path, {
        "kind": "sweep", "source": {"kind": "pine", "rule": rule_on()}, "stress_of": "20261020-101010-abcdef",
        "sweep": {"axes": [{"kind": "setting", "name": "order_seconds", "values": [1.0, 16.0]},
                           {"kind": "setting", "name": "slippage", "values": [0.0, 0.05]}], "held_out": 0.3},
    })

    assert run_command(tmp_path, folder, recorded) == 0

    summary = read(folder, "summary.json")
    assert summary["cells"] == 4 and not {"pbo", "deflated_sharpe", "walk_forward"} & set(summary)
    cells = read(folder, "sweep.json")["cells"]
    # Later and dearer fills never make the same trades better.
    assert cells[3]["whole"]["total_percent"] <= cells[0]["whole"]["total_percent"]


def test_what_a_sweep_says_beyond_its_best_combination():
    from datetime import timedelta

    days = [(date(2026, 8, 3) + timedelta(days=number)).isoformat() for number in range(40)]
    steady = [0.4 if index % 3 else -0.2 for index in range(40)]
    fitted = [1.5 if index < 20 else -1.5 for index in range(40)]
    cells = [
        {"values": [value], "curve": [[day, percent, 1] for day, percent in zip(days, curve)]}
        for value, curve in ((1, steady), (2, fitted))
    ]

    found = backtest_run.sweep_robustness(cells, {"walk_forward": {"train_days": 5, "test_days": 5}, "trials_before": 8},
                                          first_held=days[30])

    assert [window["values"] for window in found["walk_forward"]["windows"]][0] == [2]
    assert found["pbo"]["combinations"] == 70
    # The best of the first thirty days is the fitted one; all ten tries are counted.
    assert found["deflated_sharpe"]["trials"] == 10 and found["deflated_sharpe"]["days"] == 30
    assert "walk_forward" not in backtest_run.sweep_robustness(cells, {}, first_held=None)
    # Under twenty days to choose on, no deflated ratio: its skew and tails would be a handful of numbers.
    assert backtest_run.sweep_robustness(cells, {}, first_held=days[15])["deflated_sharpe"] is None


def test_a_run_on_turbos_made_up_from_the_index_trades_them_as_recorded_ones(tmp_path, recorded):
    calibration = tmp_path / "turbo_model.json"
    calibration.write_text(json.dumps({
        "format": 1, "made_at": "2026-10-20T00:00:00Z", "indice": "us100", "days": ["2026-10-13", "2026-10-14"],
        "underlying": {"provider": "saxo", "key": "1", "side": "bid"}, "few_days": True,
        "directions": {direction: {"factor": 0.01, "offset": 0.0, "spacing": 50.0, "turbos": 2, "bars": 100, "error_percent": 1.5}
                       for direction in ("long", "short")},
        "spread_by_hour": {"9": 0.02}, "quoting": {"first_minute": 8 * 60, "last_minute": 22 * 60}, "decimals": 2,
    }))
    folder = prepare(tmp_path, {"source": {"kind": "pine", "rule": rule_on()}, "baseline": {"repetitions": 0}})
    command, _ = fake_runner(tmp_path)

    assert backtest_run.main([
        "--run", folder, "--data", f"model:{calibration}", "--files", recorded, "--prices", str(tmp_path / "prices"),
        "--runner", "\x1f".join(command),
    ]) == 0

    summary = read(folder, "summary.json")
    trades = read(folder, "trades.json")
    # The Monday had the index and no turbo recorded: made up, it is traded too.
    assert summary["days"]["tested"] == 3 and {trade["day"] for trade in trades} >= {"2026-10-12"}
    assert all(trade["turbo"]["issuer"] == "Model" and trade["turbo"]["instrument_id"] < 0 for trade in trades)
    # The cheapest turbo priced from 4 €: 400 points from its level, a level every 50.
    assert all(4 <= trade["entry"]["price"] <= 4.6 for trade in trades)
    assert summary["made_with"]["turbos"] == {
        "kind": "modelled", "made_at": "2026-10-20T00:00:00Z", "underlying": {"provider": "saxo", "key": "1", "side": "bid"},
        "days": 2, "few_days": True, "error_percent": {"long": 1.5, "short": 1.5},
    }
    calibration.write_text("{}")
    assert backtest_run.main(["--run", folder, "--data", f"model:{calibration}", "--files", recorded]) == 2


def test_the_signals_a_rule_raised_are_traded_without_its_script(tmp_path, recorded):
    raised = [
        {"id": 1, "action": "long", "mode": "shadow", "bar_time": "2026-10-13T06:59:00Z", "bar_closed_at": "2026-10-13T07:00:00Z",
         "raised_at": "2026-10-13T07:00:02Z", "alert_title": "Cross up", "alert_message": "{}", "confidence": None},
        {"id": 2, "action": "close-position", "mode": "shadow", "bar_time": "2026-10-13T07:59:00Z",
         "bar_closed_at": "2026-10-13T08:00:00Z", "raised_at": "2026-10-13T08:00:03Z", "alert_title": "Out"},
    ]
    folder = prepare(tmp_path, {"source": {"kind": "ta_signals", "signals": 2, "rule": {"id": "r00000000001", "name": "EMA"}},
                                "baseline": {"repetitions": 0}, "from": "2026-10-13", "to": "2026-10-13"},
                     {"signal_delay_seconds": 0.5})
    with open(os.path.join(folder, "ta_signals.json"), "w", encoding="utf-8") as out_file:
        json.dump(raised, out_file)

    assert run_command(tmp_path, folder, recorded) == 0

    signals = [signal for signal in read(folder, "signals.json") if signal["source"] == "pine"]
    assert [(signal["action"], signal["fate"], signal["arrived_at"]) for signal in signals] == [
        ("long", "executed", "2026-10-13T07:00:02Z"), ("close-position", "closed", "2026-10-13T08:00:03Z"),
    ]
    assert signals[0]["raised_at"] == "2026-10-13T07:00:02Z" and signals[0]["live_mode"] == "shadow"
    (trade,) = read(folder, "trades.json")
    assert trade["exit"]["reason"] == "Explicit Close (All)"


def test_a_folder_that_holds_no_run_is_said_so(tmp_path):
    (tmp_path / "empty").mkdir()

    assert backtest_run.main(["--run", str(tmp_path / "empty"), "--data", f"files:{tmp_path}"]) == 2
