"""
Backtest configurations (src/admin/backtest_configurations.py): which runs are one configuration, what it is named,
the days a nightly run covers, and what is kept of them beside the runs.

The settings below are those of the operator's four "(again)" runs of 16-19 Nov 2026 (read over the MCP endpoint):
the same strategy, settings equal once resolved but for the capital (1,000 € for the first two, 100 € after), written
as ``2`` in the first and ``2.0`` in the others.
"""

import json
import threading
from datetime import date

import pytest

from src.admin.backtest_configurations import (
    HISTORY_LINES,
    ConfigurationStore,
    UnknownConfiguration,
    base_name,
    base_of,
    configuration_id,
    differences,
    nightly_window,
    strategy_id,
)

SETTINGS = {
    "timezone": "Europe/Paris", "market_timezone": "America/New_York", "max_signal_age_seconds": 7200,
    "trading_start_hour": 2, "trading_end_hour": 16, "risky_start_hour": 15, "risky_start_minute": 54,
    "closed_dates": [], "allowed_indices": ["us100"], "cooldown_after_loss_minutes": 0, "max_trades_per_day": 40,
    "max_consecutive_losses": 0, "max_drawdown_from_day_best_percent": 0.0, "pause_below_balance_eur": 0.0,
    "pause_after_drawdown_percent": 0.0, "min_confidence": 0.3, "news_window": False, "news_minutes_before": 5,
    "news_minutes_after": 15, "news_minutes_after_fomc": 45, "news_fomc": True, "news_cpi": True, "news_nfp": True,
    "news_pce": True, "news_ppi": False, "news_retail": False, "news_gdp": False, "news_jolts": False,
    "news_flatten_minutes_before": 0, "no_entry_above_day_profit": 2.0, "max_day_loss": -25.0, "day_profit_target": 3.0,
    "close_position_time": "15:55", "stoploss_percent": -7, "max_profit_percent": 60, "trailing_enabled": True,
    "trailing_activation_percent": 6, "trailing_drawdown_percent": 1.5, "wait_for_fresh_quote": True,
    "index_between_quotes": False, "index_epic": "US100", "turbo_min_price": 4, "turbo_max_price": 15,
    "max_spread_percent": 0.0, "funds_percent": 100, "reserve_cash_percent": 0, "safety_units": 1,
    "funds_retry_percent": 4.0, "funds_retries": 2, "time_scaling_enabled": True,
    "time_periods": [
        {"start_minute": 480, "end_minute": 870, "scale_percent": 100, "label": "European Session"},
        {"start_minute": 870, "end_minute": 960, "scale_percent": 100, "label": "US Open (volatile)"},
        {"start_minute": 960, "end_minute": 1320, "scale_percent": 100, "label": "US Session"},
    ],
    "confidence_enabled": True, "default_confidence": 1.0,
    "confidence_bands": [
        {"least": 0.0, "below": 0.5, "scale_percent": 50, "label": "Low confidence"},
        {"least": 0.5, "below": 0.8, "scale_percent": 75, "label": "Medium confidence"},
        {"least": 0.8, "below": 2.0, "scale_percent": 100, "label": "High confidence"},
    ],
    "capital": 100, "execution": "fixed", "signal_delay_seconds": 3.0, "order_seconds": 1.0, "close_wait_seconds": 0.5,
    "stop_fill": "worst", "fill_price": "next", "slippage": 0.005, "exit_slippage_percent": 0.1,
    "fill_noise_percent": 0.0, "fill_noise_seed": 1, "commission_per_order": 0.0, "commission_percent": 0.0,
    "history_days": 1, "restart_time": "21:15",
}


def request(**changes) -> dict:
    """The request of a run of the Kernel VWAP script, as the service writes it."""
    rule = {
        "id": "replay00000a", "name": "Kernel + Avg multi VWAP Context light - resume (Claude)",
        "asset": {"provider": "capital", "key": "US100"}, "side": "bid", "bars": {"type": "range", "ticks": 60},
        "script": {"id": "8655b16df3ef", "version": 4, "sha256": "cbfc887276e33aaa44818e43cd99fa255ffbf943c3626596926f1a24f0ac2dc7"},
        "libraries": [{"name": "KernelFunctions", "id": "1a2b3c4d5e6f", "version": 1, "sha256": "ab" * 32}],
        "inputs": [{"id": "in_18", "var": "a", "title": "A", "value": 1}, {"id": "in_36", "var": "b", "title": "B", "value": 1.25}],
        "alerts": [{"title": "Enter Long", "action": "long"}, {"title": "Global Exit", "action": "close-position"}],
        "alert_calls": "ignore", "confidence_plot": None, "indice": "us100", "mode": "shadow",
    }
    found = {
        "format": 1, "kind": "run", "name": "v4: room to the session extreme", "tags": [], "from": "2026-10-15",
        "to": "2026-11-18", "indice": "us100",
        "source": {"kind": "pine", "rule": rule, "from_rule": None},
        "chart": {"asset": rule["asset"], "side": "bid", "bars": rule["bars"]},
        "changes": {"slippage": 0.005, "capital": 100}, "baseline": {"repetitions": 100, "seed": 1}, "sweep": None,
        "turbos": "recorded", "asked_at": "2026-11-19T05:08:28Z",
    }
    found.update(changes)
    return found


def settings(**changes) -> dict:
    return {"settings": {**SETTINGS, **changes}, "changed": {}, "live_before": {}}


# ── which runs are one configuration ──


def test_the_operators_four_runs_again_make_two_configurations_of_one_strategy():
    first = configuration_id(request(name="v4 (again)", to="2026-11-16"),
                             settings(capital=1000.0, no_entry_above_day_profit=2, day_profit_target=3))
    second = configuration_id(request(name="v4 (again) (again)", to="2026-11-17"), settings(capital=1000.0))
    third = configuration_id(request(name="v4 (again) (again) (again)", to="2026-11-17"), settings(capital=100))
    fourth = configuration_id(request(name="v4 (again) (again) (again) (again)"), settings())

    assert first == second and third == fourth and first != third
    assert strategy_id(request(to="2026-11-16")) == strategy_id(request())


def test_a_number_is_one_value_whether_written_whole_or_as_a_float():
    assert configuration_id(request(), settings(capital=100)) == configuration_id(request(), settings(capital=100.0))
    integral = request()
    integral["source"]["rule"]["inputs"][0]["value"] = 1.0
    assert configuration_id(integral, settings()) == configuration_id(request(), settings())


def test_a_setting_at_its_default_re_keys_nothing():
    """A setting added later at a default that changes nothing, in the newer runs alone, leaves them with the older."""
    older = {key: value for key, value in SETTINGS.items() if key != "commission_percent"}

    assert configuration_id(request(), {"settings": older}) == configuration_id(request(), settings())


@pytest.mark.parametrize("change", [
    {"name": "Another name"}, {"tags": ["kept"]}, {"from": "2026-10-01", "to": "2026-11-19"},
    {"baseline": {"repetitions": 300, "seed": 7}}, {"favourite": True}, {"made_by": "claude", "made_via": "mac"},
    {"experiment": "a4b020"}, {"lockbox": {"final": True, "from": "2026-11-10", "to": "2026-11-18"}},
    {"remade_from": "20261118-061227-368d38"}, {"nightly": {"configuration": "0123456789ab"}},
    {"asked_at": "2026-11-20T21:30:00Z"}, {"changes": {}},
])
def test_what_does_not_decide_a_result_is_no_part_of_a_configuration(change):
    assert configuration_id(request(**change), settings()) == configuration_id(request(), settings())


def test_the_rule_a_run_started_from_and_its_inputs_names_are_no_part_of_it():
    named = request()
    named["source"]["from_rule"] = {"id": "f13986be5e9b", "name": "Kernel R60"}
    named["source"]["rule"]["name"] = "Renamed script"
    named["source"]["rule"]["inputs"][0]["title"] = "Another title"

    assert configuration_id(named, settings()) == configuration_id(request(), settings())


def _changed(path: list, value) -> dict:
    found = request()
    node = found
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return found


@pytest.mark.parametrize("changed", [
    _changed(["source", "rule", "script", "version"], 5),
    _changed(["source", "rule", "script", "sha256"], "cd" * 32),
    _changed(["source", "rule", "libraries"], [{"name": "KernelFunctions", "id": "1a2b3c4d5e6f", "version": 2, "sha256": "ef" * 32}]),
    _changed(["source", "rule", "bars"], {"type": "range", "ticks": 80}),
    _changed(["source", "rule", "side"], "ask"),
    _changed(["source", "rule", "inputs"], [{"id": "in_18", "value": 2}, {"id": "in_36", "value": 1.25}]),
    _changed(["source", "rule", "alerts"], [{"title": "Enter Long", "action": "short"}, {"title": "Global Exit", "action": "close-position"}]),
    _changed(["source", "rule", "alert_calls"], "message"),
    _changed(["turbos"], "modelled"),
    _changed(["chart"], {"asset": {"provider": "dukascopy", "key": "us100"}, "side": "bid", "bars": {"type": "range", "ticks": 60}}),
])
def test_another_strategy_is_another_configuration(changed):
    assert strategy_id(changed) != strategy_id(request())
    assert configuration_id(changed, settings()) != configuration_id(request(), settings())


def test_other_settings_make_another_configuration_of_the_same_strategy():
    assert configuration_id(request(), settings(stoploss_percent=-8)) != configuration_id(request(), settings())


def test_a_sweep_is_known_by_its_axes():
    def sweep(values):
        return request(kind="sweep", baseline=None, sweep={
            "axes": [{"kind": "input", "name": "in_36", "values": values}], "held_out": 0.3, "walk_forward": None,
            "trials_before": 4,
        })

    assert configuration_id(sweep([1.1, 1.2]), settings()) != configuration_id(sweep([1.1, 1.3]), settings())
    assert configuration_id(sweep([1.1, 1.2]), settings()) != configuration_id(request(), settings())
    trials = sweep([1.1, 1.2])
    trials["sweep"]["trials_before"] = 9
    assert configuration_id(trials, settings()) == configuration_id(sweep([1.1, 1.2]), settings())


def test_signals_received_and_a_rules_signals_are_known_by_their_kind_and_rule():
    journal = {"kind": "journal", "signals": 12, "entries": 4}
    raised = {"kind": "ta_signals", "signals": 30, "rule": {"id": "f13986be5e9b", "name": "R60"}, "versions": [3]}

    assert configuration_id(request(source=journal), settings()) == \
        configuration_id(request(source={**journal, "signals": 40, "entries": 9}), settings())
    assert configuration_id(request(source=raised), settings()) == \
        configuration_id(request(source={**raised, "signals": 99, "versions": [3, 4]}), settings())
    assert configuration_id(request(source=raised), settings()) != \
        configuration_id(request(source={**raised, "rule": {"id": "0123456789ab"}}), settings())


@pytest.mark.parametrize("found", [None, {}, {"settings": None}, {"settings": {"time_periods": [{"start": 1}]}}])
def test_settings_that_cannot_be_read_make_no_configuration(found):
    assert configuration_id(request(), found) is None


def test_a_run_made_from_another_names_its_base():
    assert base_of(request(stress_of="20261119-050828-c18c49")) == "20261119-050828-c18c49"
    assert base_of(request(model_check_of="20261119-050828-c18c49")) == "20261119-050828-c18c49"
    assert base_of(request(noise_of="20261119-050828-c18c49")) == "20261119-050828-c18c49"
    assert base_of(request()) is None


def test_the_settings_two_configurations_differ_in_are_said_with_their_values():
    assert differences(SETTINGS, {**SETTINGS, "capital": 1000.0, "no_entry_above_day_profit": 2}) == [
        {"name": "capital", "here": 100, "there": 1000.0},
    ]


# ── names ──


@pytest.mark.parametrize("name, base", [
    ("v4: room (in_36 1.25) (again) (again) (again) (again)", "v4: room (in_36 1.25)"),
    ("v4: room (again)", "v4: room"),
    ("v4: room", "v4: room"),
    ("Run (a)", "Run (a)"),
    ("Plan (a", "Plan (a"),
    ("  spaced   name (again)  ", "spaced name"),
    ("(again)", "(again)"),
])
def test_a_configurations_name_is_its_runs_without_again(name, base):
    assert base_name(name) == base


def test_a_name_cut_inside_again_loses_what_is_left_of_it():
    """"Run again with changes" names a run "<name> (again)" cut at 120 characters (draftOfRun)."""
    cut = ("x" * 116 + " (again)")[:120]
    assert cut.endswith(" (ag") and base_name(cut) == "x" * 116

    cut = ("y" * 113 + " (again)")[:120]
    assert cut.endswith(" (again") and base_name(cut) == "y" * 113

    cut = ("z" * 100 + " (again) (again)" + " (again)")[:120]
    assert base_name(cut) == "z" * 100


# ── the days of a nightly run ──


def test_a_nightly_window_grows_to_its_most_then_moves_on():
    assert nightly_window(date(2026, 10, 15), date(2026, 11, 19), 365) == (date(2026, 10, 15), date(2026, 11, 19))
    assert nightly_window(date(2026, 10, 15), date(2027, 10, 20), 365) == (date(2026, 10, 21), date(2027, 10, 20))
    assert nightly_window(date(2026, 10, 15), date(2026, 11, 19), 30) == (date(2026, 10, 21), date(2026, 11, 19))


# ── what is kept beside the runs ──


def test_an_entry_is_made_on_its_first_change_and_read_back_as_a_copy(tmp_path):
    store = ConfigurationStore(str(tmp_path))
    assert store.index() == {} and store.entry("0123456789ab") == {} and not store.nightly_on("0123456789ab")

    store.update("0123456789ab", lambda entry: entry.update(name="Kernel", nightly={"on": True, "from": "2026-10-15"}))

    read = store.entry("0123456789ab")
    read["name"] = "changed in the copy"
    assert store.entry("0123456789ab")["name"] == "Kernel" and store.nightly_on("0123456789ab")
    on_disk = json.loads((tmp_path / "configurations" / "index.json").read_text())
    assert on_disk["0123456789ab"]["nightly"]["on"] is True


@pytest.mark.parametrize("asked", ["../../etc", "0123456789AB", "0123456789a", "", None])
def test_an_id_is_matched_before_any_path_is_built(tmp_path, asked):
    store = ConfigurationStore(str(tmp_path))
    with pytest.raises(UnknownConfiguration):
        store.update(asked, lambda entry: None)
    with pytest.raises(UnknownConfiguration):
        store.history(asked)


def test_changes_made_at_once_from_several_threads_are_all_kept(tmp_path):
    store = ConfigurationStore(str(tmp_path))
    ids = [f"{number:012x}" for number in range(24)]
    threads = [threading.Thread(target=store.update, args=(configuration, lambda entry: entry.update(name="n")))
               for configuration in ids]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(store.index()) == ids


def test_what_nightly_runs_said_is_kept_newest_first_one_line_a_run(tmp_path):
    store = ConfigurationStore(str(tmp_path))
    for number in range(HISTORY_LINES + 5):
        store.add_history("0123456789ab", {"run": f"run-{number}", "to": "2026-11-19", "name": "Kernel"})
    store.add_history("0123456789ab", {"run": f"run-{HISTORY_LINES + 4}", "to": "2026-11-20", "name": "Kernel"})

    lines = store.history("0123456789ab")
    assert len(lines) == HISTORY_LINES
    assert lines[0] == {"run": f"run-{HISTORY_LINES + 4}", "to": "2026-11-20", "name": "Kernel"}
    assert lines[1]["run"] == f"run-{HISTORY_LINES + 3}"
    assert store.replaced(f"run-{HISTORY_LINES + 3}") == {"configuration": "0123456789ab", "name": "Kernel", "to": "2026-11-19"}
    assert store.replaced("run-0") is None


def test_a_configuration_without_runs_is_forgotten_with_what_its_runs_said(tmp_path):
    store = ConfigurationStore(str(tmp_path))
    store.update("0123456789ab", lambda entry: entry.update(name="gone"))
    store.update("ba9876543210", lambda entry: entry.update(name="kept"))
    store.add_history("0123456789ab", {"run": "r"})

    assert store.forget({"0123456789ab", "ffffffffffff"}) == 1

    assert list(store.index()) == ["ba9876543210"]
    assert store.history("0123456789ab") == [] and not (tmp_path / "configurations" / "0123456789ab.json").exists()
