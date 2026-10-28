import json
import threading
from datetime import date

import pytest

from src.price_history.process import OOM_SCORE_ADJ, give_up_memory_first
from src.research.experiments import ExperimentJournal, UnknownExperiment
from src.research.requests import body_of, neighbour_values, trials_of


@pytest.fixture
def journal(tmp_path):
    return ExperimentJournal(tmp_path / "_research" / "experiments.jsonl", tmp_path / "_research" / "lockbox.json",
                             clock=lambda: 1_790_000_000.0)


def test_an_experiment_keeps_its_plan_and_who_opened_it(journal):
    opened = journal.new("EMA 9/21", "Crosses point the way", "ema-cross", plan="Sweep the lengths", made_by="claude")

    found = journal.get(opened["experiment"])
    assert (found["plan"], found["made_by"], found["trials"]) == ("Sweep the lengths", "claude", 0)


def test_the_operators_experiments_say_nothing_of_an_agent(journal):
    found = journal.get(journal.new("Gap", "Gaps close", "gap")["experiment"])

    assert found["made_by"] is None and found["plan"] is None


def test_trials_are_counted_by_family(journal):
    first = journal.new("A", "a", "ema")["experiment"]
    second = journal.new("B", "b", "ema")["experiment"]
    journal.record_run(first, "20261020-100000-aaaaaa", kind="sweep", trials=trials_of(
        {"sweep": {"axes": [{"values": [1, 2, 3]}, {"values": [4, 5]}]}}))
    journal.record_run(second, "20261020-100100-bbbbbb", kind="stress", trials=0)
    journal.record_run(second, "20261020-100200-cccccc", kind="sweep", trials=len(neighbour_values(9)) - 1)

    assert journal.family_trials("ema") == 6 + 0 + 4


def test_trials_made_elsewhere_count_in_the_family_and_are_said_apart(journal):
    first = journal.new("A", "a", "kernel")["experiment"]
    journal.record_run(first, "20261021-100000-aaaaaa", kind="run", trials=1)
    journal.record_trials(first, 35, "  A Python port of v2 screened on the Mac, 12 Jun to 16 Oct  ")

    found = journal.get(first)
    assert found["trials"] == 36 and journal.family_trials("kernel") == 36
    assert [(entry["trials"], entry["note"]) for entry in found["elsewhere"]] == [
        (35, "A Python port of v2 screened on the Mac, 12 Jun to 16 Oct"),
    ]
    # Not a run: nothing to open.
    assert [entry["run"] for entry in found["runs"]] == ["20261021-100000-aaaaaa"]


@pytest.mark.parametrize("trials, note, refused", [
    (0, "n", "from 1 to 100,000"), (100_001, "n", "from 1 to 100,000"), (True, "n", "whole number"),
    (2.5, "n", "whole number"), (3, " ", "Say what was tried"), (3, "n" * 2001, "Say what was tried"),
])
def test_trials_made_elsewhere_say_how_many_and_what(journal, trials, note, refused):
    found = journal.new("A", "a", "kernel")["experiment"]

    with pytest.raises(ValueError, match=refused):
        journal.record_trials(found, trials, note)


def test_a_concluded_experiment_takes_no_more_trials(journal):
    found = journal.new("A", "a", "kernel")["experiment"]
    journal.conclude(found, "rejected", "No edge")

    with pytest.raises(ValueError, match="concluded"):
        journal.record_trials(found, 3, "More variants")
    with pytest.raises(UnknownExperiment):
        journal.record_trials("nothere", 3, "Variants")


def test_an_unknown_experiment_is_said_so(journal):
    with pytest.raises(UnknownExperiment):
        journal.record_run("nothere", "20261020-100000-aaaaaa", kind="run", trials=1)


@pytest.mark.parametrize("texts", [("", "h", "f"), ("t", " ", "f"), ("t", "h", ""), ("t" * 2001, "h", "f")])
def test_an_experiment_has_a_title_a_hypothesis_and_a_family(journal, texts):
    with pytest.raises(ValueError):
        journal.new(*texts)


def test_a_verdict_is_one_of_three(journal):
    found = journal.new("A", "a", "ema")["experiment"]

    with pytest.raises(ValueError):
        journal.conclude(found, "promising", "")
    journal.conclude(found, "kept", "Survived the stress")
    assert journal.get(found)["verdict"] == "kept"


def test_writes_from_several_threads_are_whole_lines(journal):
    found = journal.new("A", "a", "ema")["experiment"]

    def record(number):
        for step in range(20):
            journal.record_run(found, f"20261020-1{number:02d}{step:03d}-aaaaaa", kind="run", trials=1)

    threads = [threading.Thread(target=record, args=(number,)) for number in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert journal.get(found)["trials"] == 160
    lines = open(journal.journal_path).read().splitlines()
    assert all(json.loads(line) for line in lines)


# ── the lock box ──


def test_the_lock_box_is_a_range_of_days(journal):
    journal.set_lockbox(date(2026, 10, 19), date(2026, 10, 23), set_by="claude")

    box = journal.lockbox()
    assert (box["from"], box["to"], box["set_by"]) == (date(2026, 10, 19), date(2026, 10, 23), "claude")
    assert journal.in_lockbox(date(2026, 10, 13), date(2026, 10, 19))
    assert journal.in_lockbox(date(2026, 10, 23), date(2026, 10, 30))
    assert not journal.in_lockbox(date(2026, 10, 13), date(2026, 10, 18))
    assert not journal.in_lockbox(date(2026, 10, 24), date(2026, 10, 30))


def test_the_labs_lock_box_holds_every_day_after_its_first(journal, tmp_path):
    (tmp_path / "_research").mkdir(exist_ok=True)
    (tmp_path / "_research" / "lockbox.json").write_text(json.dumps({"from": "2026-10-19", "set_at": "x"}))

    assert journal.lockbox()["to"] is None
    assert journal.in_lockbox(date(2026, 11, 1), date(2026, 11, 2))


@pytest.mark.parametrize("first,last", [
    (date(2026, 10, 20), date(2026, 10, 23)),  # starts later
    (date(2026, 10, 19), date(2026, 10, 22)),  # ends earlier
])
def test_an_agent_may_only_make_the_lock_box_larger(journal, first, last):
    journal.set_lockbox(date(2026, 10, 19), date(2026, 10, 23))

    with pytest.raises(ValueError, match="may only grow"):
        journal.set_lockbox(first, last, only_wider=True)
    journal.set_lockbox(date(2026, 10, 18), date(2026, 10, 25), only_wider=True)
    assert journal.lockbox()["from"] == date(2026, 10, 18)


def test_an_open_lock_box_stays_open_for_an_agent(journal):
    journal.set_lockbox(date(2026, 10, 19))

    with pytest.raises(ValueError, match="may only grow"):
        journal.set_lockbox(date(2026, 10, 19), date(2026, 12, 31), only_wider=True)
    journal.set_lockbox(date(2026, 10, 1), only_wider=True)


def test_the_operator_may_move_or_open_the_lock_box(journal):
    journal.set_lockbox(date(2026, 10, 19), date(2026, 10, 23))

    journal.set_lockbox(date(2026, 10, 26), date(2026, 10, 30))
    assert journal.lockbox()["from"] == date(2026, 10, 26)
    journal.clear_lockbox()
    assert journal.lockbox() is None
    assert not journal.in_lockbox(date(2026, 1, 1), date(2027, 1, 1))


def test_a_lock_box_cannot_end_before_it_starts(journal):
    with pytest.raises(ValueError, match="ends before"):
        journal.set_lockbox(date(2026, 10, 23), date(2026, 10, 19))


# ── a child gives up its memory first ──


def test_a_child_asks_to_be_stopped_first_when_memory_runs_out(tmp_path):
    path = tmp_path / "oom_score_adj"
    path.write_text("0")

    give_up_memory_first(str(path))

    assert path.read_text() == str(OOM_SCORE_ADJ) == "1000"


def test_where_nothing_can_be_asked_nothing_fails(tmp_path):
    give_up_memory_first(str(tmp_path / "no" / "such" / "file"))


def test_a_run_of_the_signals_a_rule_raised_is_asked_again_as_one(tmp_path):
    chart = {"asset": {"provider": "capital", "key": "US100"}, "side": "bid", "bars": {"type": "range", "ticks": 40}}
    run = {"request": {"name": "Paper: EMA, since 2026-10-14", "tags": ["paper"], "from": "2026-10-14", "to": "2026-10-19",
                       "indice": "us100", "changes": {}, "chart": chart, "paper": {"rule": "a00000000001", "report": True},
                       "source": {"kind": "ta_signals", "signals": 6, "rule": {"id": "a00000000001", "name": "EMA"}}}}

    body = body_of(run)

    # Its signals read again for the period, never the journal in their place; never a paper run of its own.
    assert body["source"] == {"kind": "ta_signals", "rule": {"id": "a00000000001", "name": "EMA"}}
    assert body["chart"] == chart and "paper" not in body
