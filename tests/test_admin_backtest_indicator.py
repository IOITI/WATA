"""
A day of a backtest with the script that made its signals, as it ran
(src/admin/backtest_indicator.py): the engine day it was traded in, its bars as
the replay built them, and the script, libraries and inputs to draw it with,
for a Pine run and for a run of the signals a rule raised (paper trading).
"""

import asyncio
import json
from datetime import date, datetime, timezone

import pytest

from src.admin.backtest_indicator import NO_SCRIPT, bars_of_label, day_version, day_window
from src.admin.errors import InvalidRequest, NotPossible
from src.admin.processes import DONE
from src.admin.rule_history import HISTORY_FILE, RecordedRuleStore, run_fields
from src.admin.ta_rules import RuleStore
from src.ta_engine.replay import _builder, _decimals, built_bars, too_large
from tests.test_admin_backtests import (  # noqa: F401 (fixtures)
    JOURNAL,
    config,
    cross_id,
    finished,
    folder_of,
    pine,
    scripts,
    service,
)
from tests.test_admin_ta_rules import BANDS, MATHS, STRATEGY, fields

DAY = date(2026, 10, 14)
# The engine day of 14 October: from the restart of the 13th (21:15 UTC) to the next.
BEGIN = int(datetime(2026, 10, 13, 21, 15, tzinfo=timezone.utc).timestamp())
END = BEGIN + 86_400


def utc(text: str) -> float:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


def iso(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def walk(start: int, count: int, price: float = 21_000.0, step: float = 0.021) -> list[tuple]:
    """Ten-second rows of an asset, as AssetRows gives them: up and down by ``step``, bid and ask a tenth apart."""
    rows = []
    for index in range(count):
        level = round(price + step * ((index // 7) % 5) * (1 if (index // 35) % 2 else -1), 3)
        rows.append((start + 10 * index, level, level + step, level - step, level, level + 0.1, level + 0.1 + step,
                     level + 0.1 - step, level + 0.1, 3))
    return rows


class Rows:
    """The asset's rows as AssetRows gives them, and what was asked of them."""

    def __init__(self, rows: list[tuple], decimals: int | None = None):
        self._rows, self._decimals = rows, decimals
        self.asked: list[tuple] = []

    async def rows(self, provider, key, start, end):
        self.asked.append((provider, key, start, end))
        return [row for row in self._rows if start <= row[0] < end], self._decimals


def drawn(backtests, run_id: str, rows: Rows, day: date = DAY) -> dict:
    return asyncio.run(backtests.day_indicator(run_id, day, rows=rows))


DAYS = [{"day": day, "status": "tested"} for day in ("2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16")]


# ── the engine day ──


def test_a_day_is_traded_in_the_engine_day_holding_its_noon():
    assert day_window(DAY, "Europe/Paris", "21:15") == (BEGIN, END)
    # A restart in the morning: the engine day begins on the day itself.
    morning = int(datetime(2026, 10, 14, 5, 0, tzinfo=timezone.utc).timestamp())
    assert day_window(DAY, "Europe/Paris", "05:00") == (morning, morning + 86_400)


def test_a_rule_s_bars_are_read_from_their_one_word():
    assert bars_of_label("R60") == {"type": "range", "ticks": 60}
    assert bars_of_label("10S") == {"type": "time", "timeframe": "10S"}
    assert bars_of_label("1") == {"type": "time", "timeframe": "1"}


def test_the_version_drawn_is_the_one_that_raised_most_of_the_day_s_signals():
    def raised(version, at):
        return {"script_version": version, "bar_closed_at": iso(at), "raised_at": iso(at + 1)}

    noon = BEGIN + 15 * 3600
    rows = [raised(1, BEGIN - 3600), raised(2, noon), raised(3, noon + 60), raised(3, noon + 120), raised(4, END + 600)]
    assert day_version(rows, BEGIN, END, 9) == (3, [2, 3], "raised")
    # A tie goes to the version raised last.
    assert day_version(rows[:3], BEGIN, END, 9) == (3, [2, 3], "raised")
    # No signal that day: the nearest one before it, else after it, else the rule's.
    assert day_version([rows[0], rows[4]], BEGIN, END, 9) == (1, [], "before")
    assert day_version([rows[4]], BEGIN, END, 9) == (4, [], "after")
    assert day_version([], BEGIN, END, 9) == (9, [], "rule")


# ── a Pine run ──


def test_a_pine_run_s_day_is_drawn_with_its_script_as_it_ran_on_the_bars_it_ran_on(tmp_path, scripts, config):
    backtests = service(tmp_path, scripts, config)
    run_id = finished(tmp_path, backtests, scripts, {"days.json": DAYS})
    # Its script saved again since: the run's own copy is drawn.
    scripts.save(cross_id(scripts), 1, STRATEGY + "\n// later\n")
    anchor = BEGIN - 86_400
    rows = Rows(walk(anchor - 3600, 2_000) + walk(BEGIN + 8 * 3600, 3_000))

    answer = drawn(backtests, run_id, rows)

    assert answer["kind"] == "pine" and answer["exact"] is True and answer["notes"] == []
    assert answer["script"] == {"id": cross_id(scripts), "version": 1, "name": "Cross", "source": STRATEGY}
    assert [(entry["name"], entry["version"], entry["source"]) for entry in answer["libraries"]] == [
        ("Maths", 1, MATHS), ("Bands", 1, BANDS)]
    assert answer["inputs"] == [{"id": "in_0", "var": "fast", "title": "Fast", "value": 12}]
    assert answer["inputs_version"] == 1
    # What the replay hands the runner: its ticker and timeframe ("1" for range bars).
    assert (answer["ticker"], answer["timeframe"]) == ("DUKASCOPY_US100.R60", "1")
    assert answer["window"] == {"begin": "2026-10-13T21:15:00Z", "end": "2026-10-14T21:15:00Z",
                                "anchor": "2026-10-12T21:15:00Z"}
    assert (answer["history_days"], answer["restart_time"], answer["decimals"]) == (1, "21:15", 3)

    # The rows of [anchor, end) alone, built as ReplaySession.window builds them.
    assert rows.asked == [("dukascopy", "us100", anchor, END)]
    found = [row for row in rows._rows if anchor <= row[0] < END]
    chart = {"type": "range", "ticks": 60}
    expected = built_bars(_builder(chart, "bid", found, _decimals(found, None)), found, END,
                          too_large("dukascopy/us100", chart, 3))
    assert answer["bars"] == expected and len(expected) > 100
    assert answer["bars"][0][0] >= anchor * 1000 and answer["bars"][-1][6] <= END * 1000


def test_a_pine_run_s_own_history_days_set_where_its_bars_begin(tmp_path, scripts, config):
    backtests = service(tmp_path, scripts, config)
    run_id = finished(tmp_path, backtests, scripts, {"days.json": DAYS})
    settings_path = folder_of(tmp_path, run_id) / "settings.json"
    settings = json.loads(settings_path.read_text())
    settings["settings"]["history_days"] = 3
    settings_path.write_text(json.dumps(settings))
    rows = Rows([])

    answer = drawn(backtests, run_id, rows)

    assert rows.asked[0][2] == BEGIN - 3 * 86_400 and answer["window"]["anchor"] == "2026-10-10T21:15:00Z"
    assert answer["bars"] == []


def test_a_pine_run_whose_copy_of_its_script_was_changed_is_not_drawn(tmp_path, scripts, config):
    backtests = service(tmp_path, scripts, config)
    run_id = finished(tmp_path, backtests, scripts, {"days.json": DAYS})
    copy = next((folder_of(tmp_path, run_id) / "sources").glob(f"{cross_id(scripts)}-v1.pine"))
    copy.write_text(STRATEGY + "\n// edited\n")

    with pytest.raises(NotPossible, match="not the one it ran"):
        drawn(backtests, run_id, Rows([]))


def test_a_day_too_large_for_the_runner_is_refused_as_the_replay_refuses_it(tmp_path, scripts, config, monkeypatch):
    import src.ta_engine.replay as replay

    monkeypatch.setattr(replay, "MAX_WINDOW_BARS", 5)
    backtests = service(tmp_path, scripts, config)
    run_id = finished(tmp_path, backtests, scripts, {"days.json": DAYS})

    with pytest.raises(InvalidRequest, match="more than 5 bars of R60 on dukascopy/us100"):
        drawn(backtests, run_id, Rows(walk(BEGIN + 8 * 3600, 3_000)))


def test_what_cannot_be_drawn_is_refused_saying_why(tmp_path, scripts, config):
    backtests = service(tmp_path, scripts, config)
    run_id = finished(tmp_path, backtests, scripts, {"days.json": DAYS})

    with pytest.raises(InvalidRequest, match="2026-10-19 is not one of its days"):
        drawn(backtests, run_id, Rows([]), date(2026, 10, 19))

    (folder_of(tmp_path, run_id) / "status.json").write_text(json.dumps({"state": "running"}))
    with pytest.raises(NotPossible, match="no results yet"):
        drawn(backtests, run_id, Rows([]))

    async def replay():
        return backtests.create({"from": "2026-10-13", "to": "2026-10-16", "indice": "us100", "source": {"kind": "journal"},
                                 "chart": {"asset": {"provider": "capital", "key": "US100"}, "side": "bid",
                                           "bars": {"type": "time", "timeframe": "1"}}}, journal=JOURNAL)

    journal_id = asyncio.run(replay())["id"]
    (folder_of(tmp_path, journal_id) / "days.json").write_text(json.dumps(DAYS))
    (folder_of(tmp_path, journal_id) / "status.json").write_text(json.dumps({"state": DONE}))
    with pytest.raises(NotPossible, match=NO_SCRIPT[:30]):
        drawn(backtests, journal_id, Rows([]))


# ── a run of the signals a rule raised ──


def made_rule(scripts, created_at: str = "2026-10-01T08:00:00+00:00") -> dict:
    """A rule as one made before rules had a history: no line, made on ``created_at``."""
    store = RuleStore(scripts)
    rule = store.create(fields(scripts.script(cross_id(scripts))), store.load()["revision"])["rule"]
    listed = json.loads(open(store.path, encoding="utf-8").read())
    listed["rules"][0].update(created_at=created_at, updated_at=created_at, mode_changed_at=created_at)
    open(store.path, "w", encoding="utf-8").write(json.dumps(listed))
    return store.rule(rule["id"])


def signal(rule: dict, version: int, closed: float, *, bars: str = "1") -> dict:
    return {"rule_id": rule["id"], "script_id": rule["script"]["id"], "script_version": version, "asset": "capital/US100",
            "side": "bid", "bars": bars, "bar_time": iso(closed - 60), "bar_closed_at": iso(closed),
            "raised_at": iso(closed + 1), "action": "long", "indice": "us100", "mode": "shadow"}


def paper_run(tmp_path, backtests, rule: dict, raised: list[dict]) -> str:
    async def ask():
        return backtests.create({
            "from": "2026-10-13", "to": "2026-10-16", "indice": "us100",
            "source": {"kind": "ta_signals", "rule": {"id": rule["id"], "name": rule["name"]}},
            "chart": {"asset": {"provider": "capital", "key": "US100"}, "side": "bid", "bars": {"type": "time", "timeframe": "1"}},
        }, journal=raised)

    run_id = asyncio.run(ask())["id"]
    (folder_of(tmp_path, run_id) / "days.json").write_text(json.dumps(DAYS))
    (folder_of(tmp_path, run_id) / "status.json").write_text(json.dumps({"state": DONE}))
    backtests._waiting.clear()
    return run_id


NOON = BEGIN + 15 * 3600


def test_a_paper_day_of_a_rule_unchanged_since_is_drawn_as_the_engine_ran_it(tmp_path, scripts, config):
    rule = made_rule(scripts)
    backtests = service(tmp_path, scripts, config)
    run_id = paper_run(tmp_path, backtests, rule, [signal(rule, 1, NOON), signal(rule, 1, NOON + 600)])
    rows = Rows(walk(BEGIN - 86_400, 50) + walk(NOON - 3600, 400), decimals=1)

    answer = drawn(backtests, run_id, rows)

    assert answer["kind"] == "ta_signals" and answer["exact"] is True and answer["notes"] == []
    assert (answer["script"]["version"], answer["script"]["source"]) == (1, STRATEGY)
    assert [entry["name"] for entry in answer["libraries"]] == ["Maths", "Bands"]
    assert answer["inputs"] == rule["inputs"] and answer["inputs_version"] == 1
    assert answer["chart"] == {"asset": {"provider": "capital", "key": "US100"}, "side": "bid",
                               "bars": {"type": "time", "timeframe": "1"}}
    assert (answer["ticker"], answer["timeframe"], answer["decimals"]) == ("CAPITAL_US100", "1", 1)
    # The engine's history_days, the rule having none of its own.
    assert rows.asked == [("capital", "US100", BEGIN - 86_400, END)]
    assert answer["bars"][0][0] == (BEGIN - 86_400) * 1000 and len(answer["bars"]) > 10


def test_a_paper_day_of_a_rule_changed_since_is_drawn_with_what_its_history_says_it_ran(tmp_path, scripts, config):
    rule = made_rule(scripts)
    backtests = service(tmp_path, scripts, config)
    run_id = paper_run(tmp_path, backtests, rule, [signal(rule, 1, NOON)])
    # Tuned after the day, through the store that keeps what it ran.
    recorded = RecordedRuleStore(scripts)
    recorded.update(rule["id"], fields(scripts.script(cross_id(scripts)), inputs=[
        {"id": "in_0", "var": "fast", "title": "Fast", "value": 20}], history_days=2), recorded.load()["revision"])

    answer = drawn(backtests, run_id, Rows([]))

    assert answer["exact"] is True and answer["notes"] == []
    assert answer["inputs"][0]["value"] == 12 and answer["history_days"] == 1


def test_a_paper_day_of_a_rule_changed_before_its_history_was_kept_says_so(tmp_path, scripts, config):
    rule = made_rule(scripts)
    backtests = service(tmp_path, scripts, config)
    run_id = paper_run(tmp_path, backtests, rule, [signal(rule, 1, NOON)])
    newer = scripts.save(cross_id(scripts), 1, STRATEGY + "\n// v2\n")
    store = RuleStore(scripts)
    store.update(rule["id"], fields(newer, inputs=[{"id": "in_0", "var": "fast", "title": "Fast", "value": 20}], history_days=0),
                 store.load()["revision"])

    answer = drawn(backtests, run_id, Rows([]))

    assert answer["exact"] is False
    # The version that raised the day's signals, with the rule's inputs of now (keyed by name in the page).
    assert answer["script"]["version"] == 1 and answer["script"]["source"] == STRATEGY
    assert answer["inputs"][0]["value"] == 20 and answer["inputs_version"] == 2 and answer["history_days"] == 0
    assert answer["notes"][0].startswith("The rule changed on ")
    assert "after this day, before WATA kept what each rule ran: drawn with version 1" in answer["notes"][0]
    # Version 1 is not the one the rule pins now: its libraries as they are.
    assert answer["notes"][1:] == ["Its libraries as they are now: Maths version 1, Bands version 1."]


def test_a_paper_day_raised_by_two_versions_or_none_says_which_is_drawn(tmp_path, scripts, config):
    rule = made_rule(scripts)
    scripts.save(cross_id(scripts), 1, STRATEGY + "\n// v2\n")
    backtests = service(tmp_path, scripts, config)
    two = paper_run(tmp_path, backtests, rule, [signal(rule, 1, NOON), signal(rule, 2, NOON + 60), signal(rule, 2, NOON + 120)])

    answer = drawn(backtests, two, Rows([]))

    assert answer["script"]["version"] == 2 and answer["exact"] is False
    assert answer["notes"][0] == ("Its signals that day were raised by versions 1, 2 of the script: drawn with version 2, "
                                  "which raised most of them.")
    # Version 2 is not the one the rule ran when the day began: its inputs then, and its libraries as they are now.
    assert answer["notes"][1:] == [
        "The rule ran version 1 when this engine day began: drawn with version 2, which raised its signals, and the "
        "rule's inputs of then, matched by name.",
        "Its libraries as they are now: Maths version 1, Bands version 1.",
    ]

    quiet = paper_run(tmp_path, backtests, rule, [signal(rule, 1, BEGIN - 7_200)])
    answer = drawn(backtests, quiet, Rows([]))
    assert answer["exact"] is True
    assert answer["notes"] == ["No signal of the rule that day says which script it ran: drawn with version 1, "
                               "the nearest one raised."]


def test_a_paper_day_of_a_deleted_rule_is_drawn_from_its_history(tmp_path, scripts, config):
    recorded = RecordedRuleStore(scripts)
    rule = recorded.create(fields(scripts.script(cross_id(scripts))), recorded.load()["revision"])["rule"]
    backtests = service(tmp_path, scripts, config)
    run_id = paper_run(tmp_path, backtests, rule, [signal(rule, 1, NOON)])
    # Its making dated before the day, as a line of before the day would say.
    lines = [{"rule_id": rule["id"], "at": "2026-10-01T08:00:00+00:00", "after": run_fields(rule)}]
    (tmp_path / "ta" / HISTORY_FILE).write_text("".join(json.dumps(line) + "\n" for line in lines))
    recorded.delete(rule["id"], recorded.load()["revision"])

    answer = drawn(backtests, run_id, Rows([]))

    assert answer["exact"] is True and answer["inputs"] == rule["inputs"]


def test_the_route_answers_with_the_day_and_refuses_what_is_not_a_run(tmp_path, scripts, config):
    from tests.test_admin_api import make_ctx, signed_in

    ctx = make_ctx(config, tmp_path)
    ctx.settings.backtests_enabled = True
    ctx.backtests = backtests = service(tmp_path, scripts, config)
    run_id = finished(tmp_path, backtests, scripts, {"days.json": DAYS})

    with signed_in(ctx, tmp_path) as client:
        answer = client.get(f"/api/backtests/{run_id}/days/2026-10-14/indicator")
        assert answer.status_code == 200
        # No downloaded day of Dukascopy's: no bar to draw.
        assert answer.json()["script"]["source"] == STRATEGY and answer.json()["bars"] == []
        assert client.get(f"/api/backtests/{run_id}/days/2026-10-20/indicator").status_code == 422
        assert client.get(f"/api/backtests/{run_id}/days/not-a-day/indicator").status_code == 422
        assert client.get("/api/backtests/20261019-200000-ffffff/days/2026-10-14/indicator").status_code == 404
