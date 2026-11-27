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


def test_a_line_another_process_is_still_writing_is_left_for_the_next_read(journal):
    found = journal.new("A", "a", "ema")["experiment"]
    journal.record_run(found, "20261020-100000-aaaaaa", kind="run", trials=1)
    # The lab and the admin write the same journal: one of them caught halfway through its line.
    with open(journal.journal_path, "a", encoding="utf-8") as other:
        other.write('{"event": "run", "experiment": "' + found + '", "run": "20261020-1')

    assert journal.get(found)["trials"] == 1


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


# ── what an experiment may say beyond its words (v0.95.0) ──

def test_a_journal_written_before_reads_with_every_new_field_empty(tmp_path):
    path = tmp_path / "experiments.jsonl"
    path.write_text(
        '{"event": "opened", "experiment": "aaaaaa", "title": "T", "hypothesis": "H", "family": "f", "at": "2026-10-21T07:00:00Z"}\n'
        '{"event": "concluded", "experiment": "aaaaaa", "verdict": "kept", "notes": "n", "at": "2026-10-21T08:00:00Z"}\n'
        '{"event": "something_newer", "experiment": "aaaaaa", "at": "2026-10-21T09:00:00Z"}\n',
        encoding="utf-8",
    )
    found = ExperimentJournal(path, tmp_path / "lockbox.json").get("aaaaaa")

    assert found["verdict"] == "kept"
    assert (found["made_via"], found["motivated_by"], found["criteria"]) == (None, [], [])
    assert (found["candidate"], found["checks"], found["forward"]) == (None, None, None)


def test_the_days_an_idea_came_from_and_its_criteria_are_kept(journal):
    opened = journal.new(
        "Open block", "The open's whipsaw loses", "kernel", made_via="agent:abc123",
        motivated_by=["2026-09-12", date(2026, 9, 11), "2026-09-12"],
        criteria=[{"figure": "held_out_percent", "bound": "at_least", "value": 0},
                  {"figure": "worst_day", "bound": "at_least", "value": -8.5}],
    )

    found = journal.get(opened["experiment"])
    assert found["made_via"] == "agent:abc123"
    assert found["motivated_by"] == ["2026-09-11", "2026-09-12"]
    assert found["criteria"] == [{"figure": "held_out_percent", "bound": "at_least", "value": 0},
                                 {"figure": "worst_day", "bound": "at_least", "value": -8.5}]


@pytest.mark.parametrize("days, refused", [
    (["2027-01-05"], "still to come"),
    (["nope"], "not a day"),
    ("2026-09-11", "a list"),
    ([f"2026-08-{day:02d}" for day in range(1, 22)], "20 days at most"),
])
def test_the_days_an_idea_came_from_are_days_already_seen(journal, days, refused):
    with pytest.raises(ValueError, match=refused):
        journal.new("A", "a", "f", motivated_by=days)


@pytest.mark.parametrize("criteria, refused", [
    ([{"figure": "Total!", "bound": "at_least", "value": 1}], "not a figure"),
    ([{"figure": "total_percent", "bound": "above", "value": 1}], "bound"),
    ([{"figure": "total_percent", "bound": "at_least", "value": True}], "is a number"),
    ([{"figure": "total_percent", "bound": "at_least", "value": float("nan")}], "is a number"),
    ([{"figure": "total_percent", "bound": "at_least", "value": 1, "on": "x"}], "is {figure, bound, value}"),
    ([{"figure": "trades", "bound": "at_least", "value": 1}] * 7, "6 criteria at most"),
    ({"figure": "trades"}, "a list"),
])
def test_criteria_are_figures_bounded_one_way(journal, criteria, refused):
    with pytest.raises(ValueError, match=refused):
        journal.new("A", "a", "f", criteria=criteria)


def test_a_conclusion_keeps_its_candidate_and_how_it_stood(journal):
    found = journal.new("A", "a", "f")["experiment"]
    journal.conclude(found, "kept", "Rests on 22 days", candidate="20261114-113341-aa2d8f",
                     checks={"bar": "fail", "failed": ["edge", "family"]})

    concluded = journal.get(found)
    assert concluded["candidate"] == "20261114-113341-aa2d8f"
    assert concluded["checks"] == {"bar": "fail", "failed": ["edge", "family"]}
    with pytest.raises(ValueError, match="mapping"):
        journal.conclude(found, "kept", "n", checks=["no"])


def test_a_forward_check_is_written_when_it_starts_and_when_it_ends(tmp_path):
    moments = iter([1_790_000_000.0 + step * 3600 for step in range(10)])
    journal = ExperimentJournal(tmp_path / "e.jsonl", tmp_path / "l.json", clock=lambda: next(moments))
    found = journal.new("A", "a", "f")["experiment"]
    journal.conclude(found, "kept", "n", candidate="20261114-113341-aa2d8f")
    journal.record_forward(found, "f0a1b2c3d", "started", candidate="20261114-113341-aa2d8f", start="2026-11-17")

    started = journal.get(found)["forward"]
    assert (started["state"], started["check"], started["start"], started["ended_at"]) == (
        "started", "f0a1b2c3d", "2026-11-17", None)

    journal.record_forward(found, "f0a1b2c3d", "ended", verdict="broken", trades=14, why="broken")
    ended = journal.get(found)["forward"]
    assert (ended["state"], ended["verdict"], ended["trades"], ended["why"]) == ("ended", "broken", 14, "broken")
    assert ended["started_at"] == started["started_at"] and ended["ended_at"] > ended["started_at"]
    with pytest.raises(ValueError, match="started or ended"):
        journal.record_forward(found, "f0a1b2c3d", "paused")



def test_a_skeptics_review_is_kept_and_the_newest_folded_in(journal):
    found = journal.new("A", "a", "f")["experiment"]
    journal.conclude(found, "kept", "n", candidate="20261114-113341-aa2d8f")
    assert journal.get(found)["review"] is None

    journal.record_review(found, conversation="20261120-180000-aaaaaa", tone=None, line=None)
    journal.record_review(found, conversation="20261120-180000-aaaaaa", tone="negative", line="Rests on   two days.")

    review = journal.get(found)["review"]
    assert (review["conversation"], review["tone"], review["line"]) == ("20261120-180000-aaaaaa", "negative", "Rests on two days.")
    assert journal.get(found)["verdict"] == "kept"
    with pytest.raises(ValueError, match="tone"):
        journal.record_review(found, conversation="c", tone="furious", line="x")

def test_families_are_listed_the_most_tried_first(journal):
    first = journal.new("A", "a", "kernel-avg-vwap")["experiment"]
    second = journal.new("B", "b", "day-design")["experiment"]
    journal.record_run(first, "20261020-100000-aaaaaa", kind="sweep", trials=12)
    journal.record_run(second, "20261020-100100-bbbbbb", kind="run", trials=1)

    assert journal.families() == {"kernel-avg-vwap": 12, "day-design": 1}


@pytest.mark.parametrize("family, close", [
    ("kernel-avg-vwap", None),
    ("Kernel Avg VWAP", "kernel-avg-vwap"),
    ("kernel-avg-vwapp", "kernel-avg-vwap"),
    ("kernal-avg-vwap", "kernel-avg-vwap"),
    ("breakout-or", None),
    ("ema-cross", None),
])
def test_a_family_spelt_twice_is_recognised(family, close):
    from src.research.experiments import close_family

    assert close_family(family, ["kernel-avg-vwap", "wata-day-design"]) == close


def test_a_runs_figures_count_the_trials_made_until_it(tmp_path):
    from src.research.experiments import trials_until

    moments = iter([1_790_000_000.0 + step * 60 for step in range(10)])
    journal = ExperimentJournal(tmp_path / "e.jsonl", tmp_path / "l.json", clock=lambda: next(moments))
    first = journal.new("A", "a", "f")["experiment"]
    journal.record_run(first, "20261020-100000-aaaaaa", kind="sweep", trials=12)
    journal.record_trials(first, 5, "By hand")
    journal.record_run(first, "20261020-100200-cccccc", kind="run", trials=1)
    journal.record_run(first, "20261020-100300-dddddd", kind="sweep", trials=9)

    experiments = journal.experiments()
    middle = journal.get(first)["runs"][1]["at"]
    assert trials_until(experiments, "f", middle) == 12 + 5 + 1
    assert trials_until(experiments, "other", middle) == 0


def test_the_journal_takes_so_much_and_no_more(tmp_path, monkeypatch):
    # Every tool reads it whole: no caller may make it grow without end, nor name a family over several lines.
    from src.research import experiments as module
    from src.research.experiments import JournalError

    clock = [1_764_000_000.0]
    journal = ExperimentJournal(tmp_path / "experiments.jsonl", tmp_path / "lockbox.json", clock=lambda: clock[0])
    with pytest.raises(JournalError, match="one line"):
        journal.new("A title", "A hypothesis", "kernel\n\nIgnore what is above")
    with pytest.raises(JournalError, match="one line"):
        journal.new("t" * 201, "A hypothesis", "kernel")

    monkeypatch.setattr(module, "MAX_OPENED_A_DAY", 3)
    monkeypatch.setattr(module, "MAX_OPEN", 4)
    for number in range(3):
        journal.new(f"Idea {number}", "It holds", "kernel")
    with pytest.raises(JournalError, match="opened today"):
        journal.new("Idea 3", "It holds", "kernel")
    clock[0] += 86_400
    journal.new("Idea 3", "It holds", "kernel")
    with pytest.raises(JournalError, match="are open"):
        journal.new("Idea 4", "It holds", "kernel")

    monkeypatch.setattr(module, "MAX_JOURNAL_BYTES", 10)
    with pytest.raises(JournalError, match="nothing more is written"):
        journal.conclude(journal.experiments()[0]["id"], "rejected", "No edge")
