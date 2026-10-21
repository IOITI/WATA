"""
The MCP tools that write: the experiment journal and its lock box, and
Claude's backtests (asked for, made from another, waited for, removed) with
what they must stay within. The command is a stand-in that finishes at once.
"""

import asyncio
import json
from datetime import date

import pytest
from mcp import Client

from src.admin.mcp_server.facade import CLAUDE
from src.admin.mcp_server.server import build_server
from src.admin.processes import CANCELLED, DONE, QUEUED
from tests.test_admin_backtests import (  # noqa: F401 (fixtures)
    NOW,
    FakeLauncher,
    config,
    cross_id,
    folder_of,
    pine,
    scripts,
)
from tests.test_admin_ta_rules import fields
from tests.test_mcp_tools_read import SUMMARY, TRADES, answer, call, finished_run, journal, refusal, research  # noqa: F401


def request_of(scripts, **changes) -> dict:
    return pine(scripts, **changes)


@pytest.fixture
def experiment(research):
    return answer(research, "exp_new", {"title": "EMA 9/21", "hypothesis": "Crosses point the way",
                                        "family": "ema-cross", "plan": "Sweep the stop"})["id"]


def worked(research, steps):
    """Steps of tool calls (name, arguments) with the backtest service's worker running beside them."""

    async def calling():
        worker = asyncio.create_task(research.backtests.run())
        try:
            found = []
            async with Client(build_server(research)) as client:
                for name, arguments in steps:
                    result = await client.call_tool(name, arguments)
                    found.append((json.loads(result.content[0].text) if not result.is_error else result.content[0].text,
                                  result.is_error))
            return found
        finally:
            worker.cancel()
            await research.backtests.close()

    return asyncio.run(calling())


# ── the journal ──


def test_an_experiment_is_opened_listed_and_concluded(research, experiment):
    one = answer(research, "exp_get", {"experiment_id": experiment})
    concluded = answer(research, "exp_conclude", {"experiment_id": experiment, "verdict": "rejected",
                                                  "notes": "Random entries do as well, on 5 days."})
    listed = answer(research, "exp_get")

    assert (one["made_by"], one["plan"], one["family_trials"]) == (CLAUDE, "Sweep the stop", 0)
    assert concluded["verdict"] == "rejected"
    assert listed["experiments"][0]["id"] == experiment and listed["lockbox"] is None
    assert "verdict is one of" in refusal(research, "exp_conclude", {"experiment_id": experiment, "verdict": "great", "notes": ""})
    assert "No experiment" in refusal(research, "exp_get", {"experiment_id": "nothere"})


def test_trials_made_elsewhere_count_in_the_family_and_its_next_sweep(tmp_path, research, scripts, experiment):
    noted = answer(research, "exp_trials", {"experiment_id": experiment, "trials": 35,
                                            "note": "A Python port screened on the Mac, 12 Jun to 16 Oct"})
    sweep = {"axes": [{"kind": "setting", "name": "stoploss_percent", "values": [-5, -10]}]}
    made = answer(research, "backtest_start", {"experiment_id": experiment, "request": request_of(scripts, sweep=sweep)})

    assert (noted["trials"], noted["family_trials"], noted["elsewhere"][0]["trials"]) == (35, 35, 35)
    request = json.loads((folder_of(tmp_path, made["id"]) / "request.json").read_text())
    assert request["sweep"]["trials_before"] == 35 and made["family_trials"] == 37
    assert "whole number" in refusal(research, "exp_trials", {"experiment_id": experiment, "trials": 0, "note": "x"})
    assert "No experiment" in refusal(research, "exp_trials", {"experiment_id": "nothere", "trials": 3, "note": "x"})


def test_claude_may_only_make_the_lock_box_larger(research):
    answer(research, "lockbox_set", {"start_day": "2026-10-19", "end_day": "2026-10-23"})

    assert "may only grow" in refusal(research, "lockbox_set", {"start_day": "2026-10-20", "end_day": "2026-10-23"})
    grown = answer(research, "lockbox_set", {"start_day": "2026-10-18", "end_day": "2026-10-25"})
    assert grown["lockbox"]["from"] == "2026-10-18" and grown["lockbox"]["set_by"] == CLAUDE


# ── asking for runs ──


def test_a_run_is_asked_for_an_experiment_and_counted_as_one_trial(tmp_path, research, scripts, experiment):
    made = answer(research, "backtest_start", {"experiment_id": experiment, "request": request_of(scripts)})

    request = json.loads((folder_of(tmp_path, made["id"]) / "request.json").read_text())
    assert (request["made_by"], request["experiment"]) == (CLAUDE, experiment)
    assert made["state"] == QUEUED and made["trials_recorded"] == 1 and made["family_trials"] == 1
    assert research.journal.get(experiment)["runs"][0]["run"] == made["id"]


def test_a_sweep_counts_each_combination_and_is_told_the_familys_trials(tmp_path, research, scripts, experiment):
    answer(research, "backtest_start", {"experiment_id": experiment, "request": request_of(scripts)})
    sweep = {"axes": [{"kind": "setting", "name": "stoploss_percent", "values": [-5, -10, -15]}], "trials_before": 999}

    made = answer(research, "backtest_start", {"experiment_id": experiment, "request": request_of(scripts, sweep=sweep)})

    request = json.loads((folder_of(tmp_path, made["id"]) / "request.json").read_text())
    assert request["sweep"]["trials_before"] == 1
    assert made["trials_recorded"] == 3 and made["family_trials"] == 4


@pytest.mark.parametrize("changes,refused", [
    ({"source": {"kind": "ta_signals", "rule": {"id": "x"}}}, "Pine script"),
    ({"paper": {"rule": "x", "day": "2026-10-13"}}, "Paper runs"),
    ({"from": "monday"}, "names its period"),
    ({"from": "2026-03-02", "to": "2026-10-16"}, "simulates"),
])
def test_a_request_outside_what_claude_may_ask_is_refused(research, scripts, experiment, changes, refused):
    assert refused in refusal(research, "backtest_start", {"experiment_id": experiment, "request": request_of(scripts, **changes)})


def test_the_budget_counts_the_trading_days_of_a_period(research):
    from src.admin.mcp_server import guards

    # 120 calendar days from Monday 15 June 2026: 86 weekdays, 100 random repetitions within the 10,000.
    days = guards.trading_days(research, date(2026, 6, 15), date(2026, 10, 12))
    assert days == 86 and guards.cost_of({}, days) == 8_686
    guards.check_room(research, guards.cost_of({}, days))
    # Christmas Eve, Christmas and New Year's Day are closed dates of the configuration, the 26th not.
    assert guards.trading_days(research, date(2025, 12, 22), date(2026, 1, 2)) == 10 - 3


def test_a_run_needs_an_open_experiment(research, scripts, experiment):
    assert "No experiment" in refusal(research, "backtest_start", {"experiment_id": "nothere", "request": request_of(scripts)})
    answer(research, "exp_conclude", {"experiment_id": experiment, "verdict": "inconclusive", "notes": "Too few days"})

    assert "concluded" in refusal(research, "backtest_start", {"experiment_id": experiment, "request": request_of(scripts)})


def test_a_run_from_a_rule_is_made_from_one_of_claudes_only(research, scripts, experiment):
    cross = next(meta for meta in scripts.scripts() if meta["name"] == "Cross")
    operators = research.rules.create(fields(cross), research.rules.load()["revision"])["rule"]
    claudes = research.rules.create(fields(cross, name="Claude · cross"), research.rules.load()["revision"], made_by=CLAUDE)["rule"]
    asked = request_of(scripts)

    asked["source"]["from_rule"] = {"id": operators["id"], "name": operators["name"]}
    assert "Claude's own rules" in refusal(research, "backtest_start", {"experiment_id": experiment, "request": asked})
    asked["source"]["from_rule"] = {"id": claudes["id"], "name": claudes["name"]}
    assert answer(research, "backtest_start", {"experiment_id": experiment, "request": asked})["state"] == QUEUED


def test_a_run_from_a_rule_replays_it_on_the_rules_own_history_unless_asked_otherwise(tmp_path, research, scripts, experiment):
    cross = next(meta for meta in scripts.scripts() if meta["name"] == "Cross")
    rule = research.rules.create(fields(cross, name="Claude · cross", history_days=0), research.rules.load()["revision"],
                                 made_by=CLAUDE)["rule"]
    asked = request_of(scripts)
    asked["source"]["from_rule"] = {"id": rule["id"], "name": rule["name"]}
    told = {**asked, "changes": {"history_days": 2}}

    own = answer(research, "backtest_start", {"experiment_id": experiment, "request": asked})
    other = answer(research, "backtest_start", {"experiment_id": experiment, "request": told})

    def changes(run_id):
        return json.loads((folder_of(tmp_path, run_id) / "request.json").read_text())["changes"]

    assert changes(own["id"])["history_days"] == 0 and changes(other["id"])["history_days"] == 2


def test_claudes_share_of_the_line_and_of_the_day_are_kept(research, scripts, experiment):
    research.limits = type(research.limits)(max_waiting_runs=2, runs_per_day=3)
    for _ in range(2):
        answer(research, "backtest_start", {"experiment_id": experiment, "request": request_of(scripts)})

    assert "wait already" in refusal(research, "backtest_start", {"experiment_id": experiment, "request": request_of(scripts)})
    research.backtests._waiting.clear()
    answer(research, "backtest_start", {"experiment_id": experiment, "request": request_of(scripts)})
    research.backtests._waiting.clear()
    assert "day's budget" in refusal(research, "backtest_start", {"experiment_id": experiment, "request": request_of(scripts)})


def test_nothing_is_started_when_the_disk_is_nearly_full(research, scripts, experiment, monkeypatch):
    monkeypatch.setattr(type(research), "free_disk_gb", lambda self: 2.5)

    assert "GB free" in refusal(research, "backtest_start", {"experiment_id": experiment, "request": request_of(scripts)})


def test_the_lock_box_is_used_only_for_a_final_check_which_the_journal_notes(tmp_path, research, scripts, experiment):
    research.journal.set_lockbox(date(2026, 10, 15), date(2026, 10, 19))

    assert "lockbox_final" in refusal(research, "backtest_start", {"experiment_id": experiment, "request": request_of(scripts)})
    made = answer(research, "backtest_start", {"experiment_id": experiment, "request": request_of(scripts), "lockbox_final": True})

    request = json.loads((folder_of(tmp_path, made["id"]) / "request.json").read_text())
    assert request["lockbox"] == {"final": True, "from": "2026-10-15", "to": "2026-10-19"}
    assert research.journal.get(experiment)["runs"][0]["lockbox"] is True


def test_the_same_request_sent_again_with_its_key_is_not_queued_twice(research, scripts, experiment):
    arguments = {"experiment_id": experiment, "request": request_of(scripts), "idempotency_key": "try-1"}

    first, again = answer(research, "backtest_start", arguments), answer(research, "backtest_start", arguments)

    assert first["id"] == again["id"]
    assert len(research.journal.get(experiment)["runs"]) == 1


# ── made from another ──


def test_stress_and_neighbours_are_made_from_a_finished_run_and_counted_as_the_lab_counts(tmp_path, research, scripts, experiment):
    run_id = finished_run(tmp_path, research, scripts, {"summary.json": SUMMARY}, made_by=CLAUDE, experiment=experiment)
    folder = folder_of(tmp_path, run_id)
    settings = json.loads((folder / "settings.json").read_text())
    assert settings["settings"]["stoploss_percent"] == -15

    stressed = answer(research, "backtest_derive", {"run_id": run_id, "kind": "stress", "experiment_id": experiment})
    research.backtests._waiting.clear()
    neighbours = answer(research, "backtest_derive", {"run_id": run_id, "kind": "neighbours", "experiment_id": experiment,
                                                      "parameter": "stoploss_percent"})
    research.backtests._waiting.clear()
    inputs = answer(research, "backtest_derive", {"run_id": run_id, "kind": "neighbours", "experiment_id": experiment,
                                                  "parameter": "in_0", "values": [10, 12, 14]})

    assert stressed["trials_recorded"] == 0 and stressed["stress_of"] == run_id
    request = json.loads((folder_of(tmp_path, neighbours["id"]) / "request.json").read_text())
    # A whole number moves by whole steps, a tenth of it at least one (the lab's rule).
    assert request["sweep"]["axes"][0] == {"kind": "setting", "name": "stoploss_percent", "values": [-19, -17, -15, -13, -11]}
    assert neighbours["trials_recorded"] == 4 and inputs["trials_recorded"] == 2
    assert request["made_by"] == CLAUDE and request["experiment"] == experiment


def test_a_model_check_is_made_from_a_finished_recorded_run_and_read_as_a_part(tmp_path, research, scripts, experiment):
    from tests.test_admin_backtests import MEASURE

    run_id = finished_run(tmp_path, research, scripts, {"summary.json": SUMMARY, "trades.json": TRADES})
    assert "measured yet" in refusal(research, "backtest_derive", {"run_id": run_id, "kind": "model_check", "experiment_id": experiment})
    measured = tmp_path / "backtests" / "_turbo_model" / "calibration.json"
    measured.parent.mkdir(parents=True, exist_ok=True)
    measured.write_text(json.dumps(MEASURE))

    checked = answer(research, "backtest_derive", {"run_id": run_id, "kind": "model_check", "experiment_id": experiment})

    assert checked["trials_recorded"] == 0 and checked["model_check_of"] == run_id
    assert research.journal.get(experiment)["runs"][-1]["note"] == f"model check of {run_id}"
    assert "Only a model check" in answer(research, "backtest_result", {"run_id": run_id, "part": "model_check"})["note"]
    paired = {"recorded_trades": 2, "modelled_trades": 3, "paired": 2, "correlation": 0.91}
    (folder_of(tmp_path, checked["id"]) / "model_check.json").write_text(json.dumps(paired))
    assert answer(research, "backtest_result", {"run_id": checked["id"], "part": "model_check"}) == paired


def test_neighbours_need_a_parameter_the_run_had(tmp_path, research, scripts, experiment):
    run_id = finished_run(tmp_path, research, scripts, {"summary.json": SUMMARY})

    assert "Name the parameter" in refusal(research, "backtest_derive", {"run_id": run_id, "kind": "neighbours", "experiment_id": experiment})
    assert "not an input" in refusal(research, "backtest_derive", {"run_id": run_id, "kind": "neighbours",
                                                                   "experiment_id": experiment, "parameter": "in_7"})
    assert "kind is one of" in refusal(research, "backtest_derive", {"run_id": run_id, "kind": "repeat", "experiment_id": experiment})


# ── waiting and removing ──


def test_claude_waits_for_its_runs_and_reads_what_they_found(research, scripts, experiment):
    found = worked(research, [
        ("backtest_start", {"experiment_id": experiment, "request": request_of(scripts)}),
    ])
    run_id = found[0][0]["id"]

    waited = worked(research, [("backtest_wait", {"run_ids": [run_id], "seconds": 5})])

    assert waited[0][0]["runs"][0]["state"] == DONE and waited[0][0]["unfinished"] == 0


def test_claude_stops_or_removes_its_own_runs_only(tmp_path, research, scripts, experiment):
    operators = finished_run(tmp_path, research, scripts, {"summary.json": SUMMARY})
    mine = answer(research, "backtest_start", {"experiment_id": experiment, "request": request_of(scripts)})["id"]

    stopped = answer(research, "backtest_remove", {"run_id": mine})
    removed = answer(research, "backtest_remove", {"run_id": mine})

    assert stopped == {"stopped": mine}
    assert research.backtests.state(operators)["state"] == DONE
    assert removed == {"removed": mine}
    assert not folder_of(tmp_path, mine).exists()
    assert "only its own" in refusal(research, "backtest_remove", {"run_id": operators})


def test_the_turbo_model_is_measured_again_only_when_it_is_old(tmp_path, research):
    model = tmp_path / "backtests" / "_turbo_model"
    model.mkdir(parents=True)
    (model / "calibration.json").write_text(json.dumps({"made_at": "2026-10-18T20:00:00Z", "directions": {"long": {}},
                                                        "days": ["2026-10-13"], "indice": "us100"}))

    assert "within a week" in refusal(research, "turbo_model_measure", {"start_day": "2026-10-13", "end_day": "2026-10-19"})
