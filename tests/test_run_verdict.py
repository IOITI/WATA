"""The bar the research is held to, in Python (src/admin/run_verdict.py), held to the pages' (verdict.ts)."""

import json
import pathlib

import pytest

from src.admin import run_verdict
from src.admin.run_research import family_figures, motivated_figures, research_of, trial_sharpes

FIXTURE = pathlib.Path("admin_ui/src/features/backtests/verdictParity.json")
CASES = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_the_python_bar_gives_the_pages_checks(case):
    found = [[check["id"], check["state"]] for check in run_verdict.checks(case["run"], case["runs"])]

    assert found == case["expected"]


def test_every_check_says_why_in_words():
    for case in CASES:
        for check in run_verdict.checks(case["run"], case["runs"]):
            assert check["label"] and check["detail"].strip(), (case["name"], check["id"])


@pytest.mark.parametrize("states, tone, said", [
    (["pass", "pass"], "pass", "It passes every check"),
    (["pass", "open"], "open", "one check is still open"),
    (["open", "open"], "open", "2 checks are still open"),
    (["pass", "fail", "open"], "fail", "Not to be believed yet"),
])
def test_what_the_checks_say_together(states, tone, said):
    found = run_verdict.verdict_of([{"id": str(n), "label": f"Check {n}", "state": state, "detail": "."}
                                    for n, state in enumerate(states)])

    assert found["tone"] == tone and said in found["sentence"]


def test_a_run_not_judged_has_no_verdict():
    assert run_verdict.judged({"status": {"state": "failed"}}) is None


def test_criteria_are_met_or_not_or_unknown():
    run = {
        "headline": {"total_percent": 12.0, "trades": 34, "days": 22, "beaten": 99.0,
                     "edge": {"horizons": [{"minutes": 5, "signals": 145, "t": 2.09, "beaten": 85}]}},
        "summary": {"figures": {"profit_factor": 1.8}, "risk": {"sharpe": 2.1}},
        "research": {"family": {"probability": 0.31}},
    }
    criteria = [
        {"figure": "total_percent", "bound": "at_least", "value": 10},
        {"figure": "trades", "bound": "at_least", "value": 50},
        {"figure": "edge_t", "bound": "at_least", "value": 2.6},
        {"figure": "family_deflated", "bound": "at_least", "value": 0.95},
        {"figure": "pbo", "bound": "at_most", "value": 0.5},
        {"figure": "nothing_known", "bound": "at_most", "value": 1},
    ]

    found = [(entry["figure"], entry["figure_value"], entry["met"]) for entry in run_verdict.criteria_met(criteria, run)]

    assert found == [
        ("total_percent", 12.0, True), ("trades", 34.0, False), ("edge_t", 2.09, False),
        ("family_deflated", 0.31, False), ("pbo", None, None), ("nothing_known", None, None),
    ]


# ── what the journal adds to a run (src/admin/run_research.py) ──

def _days(percents):
    return [{"day": f"2026-10-{13 + index:02d}", "percent": percent, "cumulative": 0} for index, percent in enumerate(percents)]


def test_a_run_of_few_days_is_not_deflated():
    found = family_figures([1.0, -0.5, 2.0], 40, [], "kernel")

    assert (found["probability"], found["trials"], found["days"]) == (None, 40, 3)


def test_more_trials_make_the_same_days_less_believable():
    percents = [1.5, -0.5, 2.0, 0.8, -1.0, 1.2, 0.4, 2.5, -0.3, 1.1] * 2 + [0.9, 0.7]

    few, many = family_figures(percents, 3, [], "f"), family_figures(percents, 225, [], "f")

    assert few["probability"] > many["probability"]
    assert (few["spread"], many["spread"]) == ("chance", "chance")


def test_the_familys_spread_is_used_only_from_ten_curves_and_never_under_chance():
    percents = [1.5, -0.5, 2.0, 0.8, -1.0, 1.2, 0.4, 2.5, -0.3, 1.1] * 2 + [0.9, 0.7]
    narrow = [0.30 + index * 0.0001 for index in range(12)]
    wide = [-0.9 + index * 0.18 for index in range(12)]

    assert family_figures(percents, 50, narrow[:9], "f")["spread"] == "chance"
    # Twelve ratios closer together than a ratio spreads by chance on 22 days: chance's spread stands.
    assert family_figures(percents, 50, narrow, "f")["spread"] == "chance"
    used = family_figures(percents, 50, wide, "f")
    assert used["spread"] == "family" and used["trial_curves"] == 12
    assert used["probability"] < family_figures(percents, 50, [], "f")["probability"]


def test_the_days_an_idea_came_from_are_left_out_of_a_result():
    points = [("2026-11-10", 3.0), ("2026-11-11", -10.0), ("2026-11-12", 20.0), ("2026-11-13", 1.0)]

    found = motivated_figures(points, ["2026-11-11", "2026-11-12", "2026-11-20"])

    assert found["in_run"] == 2 and found["days_left"] == 2
    assert found["without_percent"] == pytest.approx(4.03)
    assert motivated_figures(points, []) is None
    assert motivated_figures([("2026-11-11", 1.0)], ["2026-11-11"])["without_percent"] is None


def test_a_runs_research_counts_its_familys_trials_until_it():
    experiments = [
        {"id": "aaaaaa", "family": "kernel", "motivated_by": ["2026-10-14"], "elsewhere": [{"trials": 35, "at": "2026-10-21T08:00:00Z"}],
         "runs": [{"run": "20261021-090000-aaaaaa", "kind": "sweep", "trials": 12, "at": "2026-10-21T09:00:00Z"},
                  {"run": "20261021-100000-bbbbbb", "kind": "run", "trials": 1, "at": "2026-10-21T10:00:00Z"},
                  {"run": "20261021-110000-cccccc", "kind": "sweep", "trials": 9, "at": "2026-10-21T11:00:00Z"}]},
        {"id": "bbbbbb", "family": "other", "motivated_by": [], "elsewhere": [],
         "runs": [{"run": "20261021-093000-dddddd", "kind": "run", "trials": 1, "at": "2026-10-21T09:30:00Z"}]},
    ]
    read = []

    def curves_of(run_id, kind):
        read.append(run_id)
        return [[1.0, -0.5, 0.7]] * (3 if kind == "sweep" else 1)

    summary = {"curve": _days([1.0, 0.5, -0.2, 2.0])}
    request = {"experiment": "aaaaaa", "kind": "run", "asked_at": "2026-10-21T10:00:00+00:00"}
    found = research_of("20261021-100000-bbbbbb", request, summary, experiments, curves_of)

    assert found["experiment"] == "aaaaaa" and found["at"] == "2026-10-21T10:00:00Z"
    assert found["family"]["trials"] == 35 + 12 + 1
    assert found["family"]["trial_curves"] == 3 + 1
    # The family's later sweep, and the other family, are never read.
    assert sorted(read) == ["20261021-090000-aaaaaa", "20261021-100000-bbbbbb"]
    assert found["motivated"]["in_run"] == 1 and found["motivated"]["days_left"] == 3
    assert research_of("x", {"experiment": "nothere", "kind": "run"}, summary, experiments, curves_of) is None
    assert research_of("x", {"experiment": "aaaaaa", "kind": "sweep"}, summary, experiments, curves_of) is None


def test_the_trials_curves_leave_out_what_chooses_nothing():
    experiments = [{"id": "a", "family": "f", "runs": [
        {"run": "s", "kind": "sweep", "trials": 0, "at": "2026-10-21T09:00:00Z"},
        {"run": "r", "kind": "run", "trials": 1, "at": "2026-10-21T09:00:00Z"},
    ]}]

    found = trial_sharpes(experiments, "f", "2026-10-21T10:00:00Z", lambda run_id, kind: [[1.0, 2.0, 0.5]])

    assert len(found) == 1
