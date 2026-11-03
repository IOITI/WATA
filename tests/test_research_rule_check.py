"""
The rule check's run test, said (src/research/rule_check.py): what it compares
between a run ending at a bar and the day's run, how often each alert fires,
and the findings and verdict it gives. The runs themselves are
tests/test_research_study.py's, with the real Pine runner.
"""

import pytest

from src.research import rule_check as checks

MINUTE = 60_000


def bar(index: int) -> list:
    return [index * MINUTE, 1.0, 1.0, 1.0, 1.0, 0, (index + 1) * MINUTE]


def alert(title: str, call: str = "alertcondition_0", message: str = "x") -> dict:
    return {"type": "alertcondition", "id": call, "title": title, "message": message}


def test_a_report_is_kept_by_its_source_and_its_libraries_whatever_their_order():
    gate = {"name": "Gate", "version": 2, "source": "//@version=6\nlibrary('Gate')\n"}
    other = {"name": "Other", "version": 1, "source": "//@version=6\nlibrary('Other')\n"}

    assert checks.digest_of("a", [gate, other]) == checks.digest_of("a", [other, gate])
    assert checks.digest_of("a", [gate]) != checks.digest_of("a", [{**gate, "source": gate["source"] + "\n"}])
    assert checks.digest_of("a", []) != checks.digest_of("b", [])
    assert len(checks.digest_of("a", [])) == 64


def test_runs_are_ended_half_where_the_day_alerted_the_rest_spread_never_at_its_last_bar():
    bars = [bar(index) for index in range(100)]

    chosen = checks.plan_checkpoints(bars, {bars[10][0], bars[11][0], bars[99][0]}, 6)

    assert len(chosen) == 6 and 99 not in chosen and {10, 11} <= set(chosen)
    assert chosen == sorted(chosen)
    assert checks.plan_checkpoints(bars[:1], set(), 6) == [] and checks.plan_checkpoints(bars, set(), 0) == []


def test_runs_are_ended_first_where_live_may_alert():
    bars = [bar(index) for index in range(100)]
    # An alert only live raises: the day's run never alerted, the probe (every bar its run's last) did at 40 and 70.
    chosen = checks.plan_checkpoints(bars, set(), 6, live={bars[40][0], bars[70][0], bars[99][0]})

    assert len(chosen) == 6 and {40, 70} <= set(chosen) and 99 not in chosen
    # Half where live may alert, then half of the rest where the day alerted, then spread.
    both = checks.plan_checkpoints(bars, {bars[10][0], bars[11][0]}, 8, live={bars[40][0], bars[70][0]})
    assert {10, 11, 40, 70} <= set(both) and len(both) == 8


def test_a_run_ending_at_a_bar_is_set_against_the_days_run_there_and_before():
    day = {"alerts": [(bar(3), alert("Long")), (bar(5), alert("Long")), (bar(8), alert("Long"))]}
    prefix = {"alerts": [(bar(3), alert("Long")), (bar(4), alert("Short")), (bar(5), alert("Short"))]}

    compared = checks.compare_at(day, prefix, bar(5)[6])

    assert [entry[2] for entry in compared["live_only"]] == ["Short"]
    assert [entry[2] for entry in compared["backtest_only"]] == ["Long"]
    # Before the bar: the Short of bar 4 only the run ending at 5 gave; bar 8 is after it.
    assert [(entry[0], entry[2]) for entry in compared["earlier"]] == [(4 * MINUTE, "Short")]
    assert checks.said_difference(compared["live_only"][0], "live") == "“Short” at 1 Jan 00:05 UTC only live"


def test_each_alert_is_counted_with_how_often_it_fired_on_the_bar_after_another():
    bars = [bar(index) for index in range(10)]
    day = {"bars": bars, "alerts": [(bars[index], alert("Above")) for index in (1, 2, 3, 4, 7)] + [(bars[4], alert("Long", "alertcondition_1"))]}

    stats = checks.alert_stats([day])

    assert stats[0] == {"title": "Above", "count": 5, "repeats": 3, "longest": 4, "share": 0.5}
    assert stats[1]["title"] == "Long" and stats[1]["count"] == 1
    assert checks.same_bar([day]) == [{"titles": ["Above", "Long"], "bars": 1}]
    assert checks.marked([True, False, None, 101.5, 0, None]) == 3


TESTED = {"days": [{"bars": 840, "milliseconds": 30}], "spoken": "13 and 14 Oct",
          "checkpoints": {"run": 12, "at_the_bar": [], "earlier": [], "plots_differ": []}}
CHECK = {"alerts": [{"title": "Long"}, {"title": "Exit"}], "wholeBecause": None,
         "findings": [{"id": "bar-state", "area": "repaint", "level": "risk", "message": "Its alerts read barstate.islast…"},
                      {"id": "clock", "area": "repaint", "level": "repaints", "message": "Its alerts read timenow…"}]}


def test_what_a_run_test_found_is_said():
    tested = {**TESTED, "alerts": [{"title": "Long", "count": 9, "repeats": 8, "longest": 6, "share": 0.01}],
              "history": {"history_days": 2, "compared": 9, "differing": 3}}
    said = {finding["id"]: finding for finding in checks.run_findings(tested, CHECK)}

    assert said["same-live-and-backtest"]["message"] == (
        "At the 12 bars tested on 13 and 14 Oct, its alerts were the same live and in a backtest.")
    assert said["fires-every-bar"]["message"] == (
        "“Long” fires at every bar while its condition holds: 9 times on 13 and 14 Oct, 8 of them on the bar after another, "
        "up to 6 in a row. A rule refuses a second entry while one is open, but enters again at the next bar after each exit.")
    assert said["never-fires"]["message"] == "“Exit” never fired on 13 and 14 Oct: check its inputs, or the bars it is run on."
    assert said["history-sensitive"]["fix"] == "Give the rule 2 days of history or more."
    assert {finding["level"] for finding in said.values()} == {"note", "change"}


def test_a_test_where_no_alert_came_says_nothing_was_compared_and_leaves_the_risk():
    tested = {**TESTED, "checkpoints": {"run": 12, "with_alerts": 0, "at_the_bar": [], "earlier": [], "plots_differ": []}}
    found = {finding["id"]: finding for finding in checks.run_findings(tested, CHECK)}

    assert "same-live-and-backtest" not in found
    assert found["no-alert-compared"]["message"] == (
        "At the 12 bars tested on 13 and 14 Oct, no alert came, live or in a backtest: the test could not compare them, "
        "and what may repaint stays as its source reads.")
    # So the risk is not set down: it may still repaint.
    report = checks.combined({"check": CHECK}, tested)
    assert next(finding for finding in report["findings"] if finding["id"] == "bar-state")["level"] == "risk"


def test_differences_at_the_bars_tested_say_the_script_repaints():
    tested = {**TESTED, "checkpoints": {"run": 12, "plots_differ": [], "earlier": [],
                                        "at_the_bar": [{"bar_time": "x", "said": "“Long” at 13 Oct 10:42 UTC only live"}]}}
    found = checks.run_findings(tested, CHECK)

    assert [finding["id"] for finding in found] == ["repaints-at-the-bar", "never-fires"]
    # An alert seen live at a bar tested did fire: its never firing in the days' runs is the repainting itself.
    live = {**tested, "checkpoints": {**tested["checkpoints"], "live_titles": ["Long", "Exit"]}}
    assert [finding["id"] for finding in checks.run_findings(live, CHECK)] == ["repaints-at-the-bar"]
    assert found[0]["message"] == (
        "At 1 of the 12 bars tested, a run ending there, as live has it, raised other alerts than the day's run, as a "
        "backtest has it: “Long” at 13 Oct 10:42 UTC only live.")


def test_a_run_that_failed_or_found_no_bar_says_so_alone():
    assert [finding["id"] for finding in checks.run_findings({"failure": {"message": "No bars", "line": 4, "file": "script"}}, CHECK)] == ["fails-on-bars"]
    assert checks.run_findings({"failure": {"message": "x is na", "line": 4, "file": None}}, CHECK)[0]["message"] == (
        "It fails on WATA's bars: x is na (line 4).")
    assert [finding["id"] for finding in checks.run_findings({"days": []}, CHECK)] == ["no-bars"]


def test_a_run_test_that_found_the_alerts_the_same_sets_what_it_saw_down_to_notes():
    static = {"check": CHECK}

    same = checks.combined(static, TESTED)
    different = checks.combined(static, {**TESTED, "checkpoints": {**TESTED["checkpoints"], "at_the_bar": [{"said": "x"}]}})
    untested = checks.combined(static, None)

    # The clock reads the test's own time: no run test sees it, so it still repaints.
    assert [(finding["id"], finding["level"]) for finding in same["findings"][:2]] == [("bar-state", "note"), ("clock", "repaints")]
    assert same["findings"][0]["tested"] is True
    assert different["findings"][0]["level"] == "risk" and different["verdict"] == "repaints"
    assert untested["findings"] == CHECK["findings"] and untested["verdict"] == "repaints"


@pytest.mark.parametrize("levels,verdict", [
    ([("no-alerts", "blocks"), ("clock", "repaints")], "needs_alerts"),
    ([("no-alerts", "blocks"), ("ticker", "blocks")], "blocked"),
    ([("clock", "repaints"), ("bar-end", "risk")], "repaints"),
    ([("bar-end", "risk"), ("mintick", "change")], "may_repaint"),
    ([("mintick", "change"), ("varip", "note")], "to_change"),
    ([("varip", "note")], "ready"),
])
def test_the_verdict_puts_the_worst_first_as_the_pages_do(levels, verdict):
    # The same table as ruleCheck.test.ts's verdictOf.
    assert checks.verdict_of([{"id": id_, "level": level} for id_, level in levels]) == verdict


def test_the_days_tested_are_said_as_a_person_would():
    october_13, october_14, november_3 = 1760390100, 1760476500, 1762204500

    assert checks.spoken_days([october_13, october_14]) == "13 and 14 Oct"
    assert checks.spoken_days([october_14, november_3]) == "14 Oct and 3 Nov"
    assert checks.spoken_days([]) == "no day"
