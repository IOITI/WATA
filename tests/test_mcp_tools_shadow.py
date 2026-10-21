"""
Claude's own shadow rules through its tools (src/admin/mcp_server/shadow.py):
made, changed, switched and removed only while the operator switched them on,
only Claude's own and never live, each run first as the engine would.
"""

import ast
import asyncio
import json
import pathlib

import pytest

from src.admin.mcp_server.facade import CLAUDE, ReadQueries
from src.admin.mcp_server.limits import McpLimits
from src.admin.processes import DONE
from src.admin.ta_rules import RuleStore
from tests.test_admin_backtests import NOW, config, folder_of, pine, scripts  # noqa: F401 (fixtures)
from tests.test_admin_ta_rules import fields
from tests.test_mcp_tools_read import CAPITAL_ROW, FakeQueries, answer, journal, refusal, research  # noqa: F401 (fixtures)

ALERTS = {"Enter Long": "long", "Exit": "close-position"}


class Feed(FakeQueries):
    """The feed recorded US100 an hour ago; the TA engine runs."""

    def __init__(self, last_t=NOW - 3600):
        super().__init__()
        self.last_t = last_t

    async def capital_instrument(self, epic):
        return {**CAPITAL_ROW, "last_t": self.last_t} if epic == "US100" and self.last_t is not None else None

    async def ta_engine_state(self):
        return {"state": "running", "detail": "1 rule(s) on"}


class Studies:
    """Stands for the study child: one engine day of each window, as asked."""

    def __init__(self, ok=True, bars=2880, milliseconds=180, error=None, during=None):
        self.answer = {"ok": ok, "error": error, "days": [
            {"from": "2026-10-15T21:15:00Z", "to": "2026-10-16T21:15:00Z", "bars": bars, "alerts": 4, "signals": 2,
             "milliseconds": milliseconds},
        ], "signals": [{"bar_time": "2026-10-16T07:00:00Z", "action": "long"}], "warnings": []}
        self.asked = []
        self.during = during

    async def study(self, mode, request):
        self.asked.append((mode, request))
        if self.during is not None:
            self.during()
        return self.answer


@pytest.fixture
def shadow(research):
    research.limits = McpLimits(shadow_rules=True)
    research.queries = ReadQueries(Feed())
    research.studies = Studies()
    return research


def cross(scripts) -> dict:
    return next(meta for meta in scripts.scripts() if meta["name"] == "Cross")


def new_rule(research, scripts, **changes) -> dict:
    return answer(research, "rule_shadow_save", {
        "name": "cross", "script_id": cross(scripts)["id"], "asset": "capital/US100", "indice": "us100",
        "alerts": ALERTS, "inputs": {"in_0": 12}, **changes,
    })


def operators_rule(research, scripts) -> dict:
    rules = research.rules
    return rules.create(fields(cross(scripts), name="Cross on US100"), rules.load()["revision"])["rule"]


def stored(research) -> list[dict]:
    return research.rules.load()["rules"]


def test_until_the_operator_switches_them_on_the_tools_say_so_and_write_nothing(research, scripts):
    research.studies = Studies()

    for tool, arguments in (
        ("rule_shadow_save", {"name": "cross", "script_id": cross(scripts)["id"], "asset": "capital/US100",
                              "indice": "us100", "alerts": ALERTS}),
        ("rule_shadow_remove", {"rule_id": "0123456789ab"}),
        ("rule_set_reference", {"run_id": "20261019-200000-abcdef"}),
    ):
        assert "switched off" in refusal(research, tool, arguments)
    assert stored(research) == [] and research.studies.asked == []


def test_a_rule_is_run_first_as_the_engine_would_on_the_last_finished_day_then_saved_in_shadow(shadow, scripts):
    saved = new_rule(shadow, scripts)

    [(mode, request)] = shadow.studies.asked
    # Monday 19 October, recorded today: the day before that was traded, Friday 16 October.
    assert mode == "signals" and (request["from"], request["to"]) == ("2026-10-16", "2026-10-16")
    assert request["script"] == {"id": cross(scripts)["id"], "version": 1}
    assert (request["asset"], request["bars"], request["alerts"], request["inputs"]) == ("capital/US100", "1", ALERTS, {"in_0": 12})
    assert saved["saved"] and saved["name"] == "Claude · cross" and saved["mode"] == "shadow"
    assert saved["measured"] == {"day": "2026-10-16", "ran": True, "most_bars": 2880, "most_milliseconds": 180, "warnings": []}
    assert "signals" not in json.dumps(saved["measured"])
    [rule] = stored(shadow)
    assert (rule["made_by"], rule["mode"], rule["name"]) == (CLAUDE, "shadow", "Claude · cross")


def test_a_rule_on_ten_second_bars_is_run_first_on_them_then_saved(shadow, scripts):
    saved = new_rule(shadow, scripts, bars="10S")

    [(_, request)] = shadow.studies.asked
    assert request["bars"] == "10S" and saved["saved"]
    [rule] = stored(shadow)
    assert rule["bars"] == {"type": "time", "timeframe": "10S"}


def test_a_name_given_with_claudes_mark_is_not_marked_twice(shadow, scripts):
    assert new_rule(shadow, scripts, name="Claude · trend")["name"] == "Claude · trend"


@pytest.mark.parametrize("studies, why", [
    (Studies(ok=False, error={"message": "Undeclared identifier", "line": 4}), "does not run as the engine would"),
    (Studies(milliseconds=3500), "takes it 3500 ms"),
    (Studies(bars=25_000), "25000 of its bars"),
    (Studies(bars=0), "no bar on 2026-10-16"),
])
def test_a_rule_that_fails_is_slow_or_too_long_is_not_saved(shadow, scripts, studies, why):
    shadow.studies = studies

    found = new_rule(shadow, scripts)

    assert found["saved"] is False and why in found["why"]
    assert stored(shadow) == []


def test_the_time_a_day_may_take_is_the_operators_to_raise(shadow, scripts):
    shadow.studies = Studies(milliseconds=3500)

    refused = new_rule(shadow, scripts)
    shadow.limits = McpLimits(shadow_rules=True, shadow_day_ms=4000)
    saved = new_rule(shadow, scripts)

    assert refused["saved"] is False and "over 2000 (admin.mcp.shadow_day_ms)" in refused["why"]
    assert saved["saved"] and saved["measured"]["most_milliseconds"] == 3500


def test_a_rule_with_its_own_history_is_run_first_on_it(shadow, scripts):
    made = new_rule(shadow, scripts, history_days=0)
    renamed = answer(shadow, "rule_shadow_save", {"rule_id": made["id"], "name": "cross from the restart"})
    kept = stored(shadow)[0]
    engines = answer(shadow, "rule_shadow_save", {"rule_id": made["id"], "history_days": -1})

    (_, first), (_, again) = shadow.studies.asked
    # No day before, as the engine will run it; a new name runs nothing; the engine's history again runs it first.
    assert first["history_days"] == 0 and kept["history_days"] == 0 and renamed["measured"] is None
    assert "history_days" not in again and engines["measured"]["ran"]
    assert "history_days" not in stored(shadow)[0]
    assert "history is 0 to 30 days" in refusal(shadow, "rule_shadow_save", {"rule_id": made["id"], "history_days": 31})


@pytest.mark.parametrize("last_t, said", [(None, "has not recorded US100"), (NOW - 6 * 86400, "last recorded US100 on 2026-10-13")])
def test_a_rule_runs_only_on_an_epic_the_feed_records_now(shadow, scripts, last_t, said):
    shadow.queries = ReadQueries(Feed(last_t))

    assert said in refusal(shadow, "rule_shadow_save", {
        "name": "cross", "script_id": cross(scripts)["id"], "asset": "capital/US100", "indice": "us100", "alerts": ALERTS,
    })
    assert shadow.studies.asked == [] and stored(shadow) == []


@pytest.mark.parametrize("changes, said", [
    ({"asset": "dukascopy/us100"}, "capital/<EPIC>"),
    ({"bars": "5S"}, "10 seconds to a day"),
    ({"indice": "dax"}, "one WATA trades"),
    ({"alerts": {"Enter Long": "buy"}}, "must be one of"),
])
def test_a_rule_the_store_would_refuse_is_refused_before_it_is_run(shadow, scripts, changes, said):
    assert said in refusal(shadow, "rule_shadow_save", {
        "name": "cross", "script_id": cross(scripts)["id"], "asset": "capital/US100", "indice": "us100", "alerts": ALERTS,
        **changes,
    })
    assert shadow.studies.asked == []


def test_claude_keeps_a_bounded_number_of_rules_and_spends_no_study_on_one_too_many(shadow, scripts):
    shadow.limits = McpLimits(shadow_rules=True, max_shadow_rules=1)
    new_rule(shadow, scripts)

    assert "rules at most" in refusal(shadow, "rule_shadow_save", {
        "name": "second", "script_id": cross(scripts)["id"], "asset": "capital/US100", "indice": "us100", "alerts": ALERTS,
    })
    assert len(stored(shadow)) == 1 and len(shadow.studies.asked) == 1


def test_a_change_sets_only_what_is_given_and_runs_first_only_what_runs_differently(shadow, scripts):
    made = new_rule(shadow, scripts)
    renamed = answer(shadow, "rule_shadow_save", {"rule_id": made["id"], "name": "cross fast", "alerts": {"Exit": "ignore"}})
    asked_after_rename = len(shadow.studies.asked)
    answer(shadow, "rule_shadow_save", {"rule_id": made["id"], "inputs": {"in_1": 30}})
    [rule] = stored(shadow)

    assert renamed["name"] == "Claude · cross fast" and renamed["measured"] is None and asked_after_rename == 1
    assert len(shadow.studies.asked) == 2
    assert [(entry["id"], entry["value"]) for entry in rule["inputs"]] == [("in_0", 12), ("in_1", 30)]
    assert {entry["title"]: entry["action"] for entry in rule["alerts"]} == {"Enter Long": "long", "Exit": "ignore"}


def test_a_rule_switched_off_and_back_is_run_first_when_it_comes_back(shadow, scripts):
    made = new_rule(shadow, scripts)

    off = answer(shadow, "rule_shadow_save", {"rule_id": made["id"], "mode": "off"})
    back = answer(shadow, "rule_shadow_save", {"rule_id": made["id"], "mode": "shadow"})

    assert (off["mode"], off["measured"]) == ("off", None)
    assert back["mode"] == "shadow" and back["measured"]["ran"] and len(shadow.studies.asked) == 2


def test_a_rule_is_switched_off_as_it_is_whatever_the_configuration_says_now(shadow, scripts):
    made = new_rule(shadow, scripts)
    # The indice is no longer one WATA trades: a change of it would be refused, a switch is not.
    shadow.config.allowed_indices = lambda: ["de40"]

    off = answer(shadow, "rule_shadow_save", {"rule_id": made["id"], "mode": "off"})
    renamed = answer(shadow, "rule_shadow_save", {"rule_id": made["id"], "name": "cross, resting"})

    assert off["mode"] == "off" and renamed["name"] == "Claude · cross, resting"
    assert "one WATA trades" in refusal(shadow, "rule_shadow_save", {"rule_id": made["id"], "indice": "us100"})
    assert shadow.rules.rule(made["id"])["indice"] == "us100"


def test_another_script_brings_none_of_the_former_inputs_and_alerts(shadow, scripts):
    made = new_rule(shadow, scripts)
    other = scripts.create("Cross two", scripts.source(cross(scripts)["id"], 1).replace('indicator("Cross"', 'indicator("Cross two"'))

    answer(shadow, "rule_shadow_save", {"rule_id": made["id"], "script_id": other["id"], "alerts": {"Exit": "close-position"}})
    [rule] = stored(shadow)

    assert rule["script"]["id"] == other["id"] and rule["inputs"] == []
    assert rule["alerts"] == [{"title": "Exit", "action": "close-position"}]


def test_the_operators_rules_are_never_changed_nor_removed(shadow, scripts):
    theirs = operators_rule(shadow, scripts)

    assert "the operator's rule" in refusal(shadow, "rule_shadow_save", {"rule_id": theirs["id"], "name": "mine now"})
    assert "the operator's rule" in refusal(shadow, "rule_shadow_save", {"rule_id": theirs["id"], "mode": "off"})
    assert "the operator's rule" in refusal(shadow, "rule_shadow_remove", {"rule_id": theirs["id"]})
    assert stored(shadow) == [theirs]


def test_once_the_operator_took_a_rule_live_it_is_no_longer_claudes(shadow, scripts):
    made = new_rule(shadow, scripts)
    rules = shadow.rules
    rules.set_mode(made["id"], "live", rules.load()["revision"])
    live = rules.rule(made["id"])

    for tool, arguments in (("rule_shadow_save", {"rule_id": made["id"], "inputs": {"in_0": 3}}),
                            ("rule_shadow_save", {"rule_id": made["id"], "mode": "off"}),
                            ("rule_shadow_remove", {"rule_id": made["id"]})):
        assert "is live" in refusal(shadow, tool, arguments)
    assert rules.rule(made["id"]) == live


def test_a_rule_changed_while_it_was_run_first_is_not_overwritten(shadow, scripts):
    made = new_rule(shadow, scripts)
    rules = shadow.rules

    def operator_edits():
        rule = rules.rule(made["id"])
        rules.update(made["id"], {**{key: rule[key] for key in ("asset", "side", "bars", "inputs", "alerts", "alert_calls",
                                                                "confidence_plot", "indice")},
                                  "name": "Claude · edited", "script": {"id": rule["script"]["id"], "version": 1}},
                     rules.load()["revision"])

    shadow.studies = Studies(during=operator_edits)

    assert "changed meanwhile" in refusal(shadow, "rule_shadow_save", {"rule_id": made["id"], "inputs": {"in_0": 3}})
    assert rules.rule(made["id"])["inputs"][0]["value"] == 12


def test_claude_removes_its_own_rules(shadow, scripts):
    made = new_rule(shadow, scripts)

    assert answer(shadow, "rule_shadow_remove", {"rule_id": made["id"]}) == {"removed": made["id"], "name": "Claude · cross"}
    assert stored(shadow) == []


@pytest.mark.parametrize("mode", ["live", "LIVE", "Live", " live", "on", ""])
def test_no_argument_makes_a_rule_live(shadow, scripts, monkeypatch, mode):
    made = new_rule(shadow, scripts)
    asked = []
    original = RuleStore.set_mode

    def recorded(self, rule_id, wanted, revision):
        asked.append(wanted)
        return original(self, rule_id, wanted, revision)

    monkeypatch.setattr(RuleStore, "set_mode", recorded)

    for arguments in ({"rule_id": made["id"], "mode": mode}, {"name": "other", "script_id": cross(scripts)["id"],
                                                              "asset": "capital/US100", "indice": "us100",
                                                              "alerts": ALERTS, "mode": mode}):
        text = refusal(shadow, "rule_shadow_save", arguments)
        assert "shadow or off" in text
    assert "live" not in asked
    assert {rule["mode"] for rule in stored(shadow)} == {"shadow"}


def test_the_only_change_of_mode_in_the_endpoint_is_between_shadow_and_off():
    calls = []
    for path in pathlib.Path("src/admin/mcp_server").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(function):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "set_mode":
                    calls.append((path.name, function.name))

    assert calls == [("shadow.py", "_switch")]


def run_from(research, scripts, rule: dict) -> str:
    body = pine(scripts)
    body["source"]["from_rule"] = {"id": rule["id"], "name": rule["name"]}

    async def ask():
        return research.backtests.create(body)

    return asyncio.run(ask())["id"]


def finish(tmp_path, research, run_id: str) -> None:
    folder = folder_of(tmp_path, run_id)
    (folder / "status.json").write_text(json.dumps({"state": DONE}))
    (folder / "days.json").write_text(json.dumps([]))
    research.backtests._waiting.clear()


def test_a_finished_run_of_one_of_claudes_rules_becomes_its_reference(tmp_path, shadow, scripts):
    made = new_rule(shadow, scripts)
    run_id = run_from(shadow, scripts, shadow.rules.rule(made["id"]))
    finish(tmp_path, shadow, run_id)

    found = answer(shadow, "rule_set_reference", {"run_id": run_id})

    assert (found["rule"], found["reference"]) == (made["id"], run_id)
    assert shadow.backtests.references() == {made["id"]: run_id}


def test_no_reference_is_set_for_the_operators_rules_nor_from_a_run_of_no_rule(tmp_path, shadow, scripts):
    theirs = run_from(shadow, scripts, operators_rule(shadow, scripts))
    finish(tmp_path, shadow, theirs)

    async def plain():
        return shadow.backtests.create(pine(scripts))

    no_rule = asyncio.run(plain())["id"]
    finish(tmp_path, shadow, no_rule)

    assert "the operator's rule" in refusal(shadow, "rule_set_reference", {"run_id": theirs})
    assert "not made from a signal rule" in refusal(shadow, "rule_set_reference", {"run_id": no_rule})
    assert shadow.backtests.references() == {}
