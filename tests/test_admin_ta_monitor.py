"""
The Engine monitor (src/admin/ta_monitor.py and its routes): periods, limits, the checks of the last
minutes at their boundaries, what health says of them, and the answers the page reads.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.admin.mcp_server.limits import McpLimits
from src.admin.ta_monitor import (
    CLOSE_WITH_ENTRY,
    DELIVERY_STEPS,
    _marks,
    bucket_of,
    checks,
    health_warning,
    notes,
    now_answer,
    period_answer,
    period_bounds,
    period_of,
    periods,
    refusal_reason,
    rule_limit_ms,
)
from src.ta_engine.engine import NOT_TAKEN, Engine
from src.ta_engine.settings import TaEngineSettings
from src.ta_engine.signals import interpret
from tests.test_admin_ta_rules import api, feed_config_path, scripts  # noqa: F401 (fixtures)
from tests.test_admin_api import config_path  # noqa: F401 (fixtures)

LIVE_RULE, SHADOW_RULE = "0123456789ab", "ba9876543210"
SETTINGS = TaEngineSettings(enabled=True)
RULES = {
    LIVE_RULE: {"id": LIVE_RULE, "name": "Live cross", "mode": "live", "bars": {"type": "time", "timeframe": "1"}},
    SHADOW_RULE: {"id": SHADOW_RULE, "name": "Claude · cross", "mode": "shadow", "made_by": "claude",
                  "bars": {"type": "time", "timeframe": "10S"}},
}
NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)


def run_row(rule_id=LIVE_RULE, runner="live", mode="live", **figures):
    return {"rule_id": rule_id, "mode": mode, "runner": runner, "runs": 90, "loads": 0, "append_max_ms": 40,
            "load_max_ms": None, "failed": 0, "ended": 0, "last_failure": None, "age_max_ms": 1500.0,
            "delay_max_ms": 1560.0, **figures}


def recent(**parts):
    return {
        "last_tick": None, "kept_since": None,
        "ticks": {"ticks": 90, "tick_p50_ms": 60.0, "tick_max_ms": 900, "steady_tick_max_ms": 400, "slow_ticks": 0,
                  "feed_lag_max_ms": 11_500.0, "memory_used_max_mb": 520.0, "memory_limit_mb": 1024.0, "oom_kills": 0,
                  "deferred": 0, **parts.pop("ticks", {})},
        "runs": parts.pop("runs", [run_row()]),
        "deferred": parts.pop("deferred", []),
        "checks": parts.pop("checks", [check_row()]),
        "signals": parts.pop("signals", []),
    }


def check_row(rule_id=LIVE_RULE, runner="live", mode="live", **figures):
    """A rule's continued runs checked against a whole run in the window (the ticks' figures.checks)."""
    return {"rule_id": rule_id, "mode": mode, "runner": runner, "checks": 2, "differed": 0, "check_max_ms": 2_700,
            "last_difference": None, **figures}


def states(found):
    return {check["id"]: check["state"] for check in found}


# ── periods and limits ──


def test_the_periods_offered_go_as_far_as_the_days_kept():
    assert periods(1) == [1, 6, 24]
    assert periods(7) == [1, 6, 24, 72, 168]
    assert periods(10) == [1, 6, 24, 72, 168, 240]
    assert periods(30) == [1, 6, 24, 72, 168, 336, 720]


def test_a_period_beyond_the_days_kept_is_brought_down_and_one_never_offered_refused():
    assert period_of(24, 7) == 24 and period_of(720, 7) == 168 and period_of(240, 10) == 240
    with pytest.raises(ValueError, match="No period of 5 h"):
        period_of(5, 7)


def test_each_period_has_60_to_170_bars_of_time_whole_from_the_first():
    assert [bucket_of(hours) for hours in (1, 6, 24, 72, 168, 240, 336, 720)] == [60, 300, 600, 1800, 3600, 7200, 7200, 21600]
    for hours in (1, 6, 24, 72, 168, 240, 336, 720):
        assert 60 <= hours * 3600 / bucket_of(hours) <= 170
    start, end = period_bounds(datetime(2026, 10, 22, 12, 7, 31, tzinfo=timezone.utc), 24, 600)
    assert (start, end) == (datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc), datetime(2026, 10, 22, 12, 7, 31, tzinfo=timezone.utc))


def test_a_run_may_take_the_runners_time_limit_or_its_bar_when_shorter():
    assert rule_limit_ms(RULES[LIVE_RULE], SETTINGS) == 30_000
    assert rule_limit_ms(RULES[SHADOW_RULE], SETTINGS) == 10_000
    assert rule_limit_ms({"bars": {"type": "range", "ticks": 60}}, SETTINGS) == 30_000
    assert rule_limit_ms(None, TaEngineSettings(run_timeout_seconds=45)) == 45_000


# ── why a live signal was not sent: by the engine's own texts ──


def _refusal(contested=frozenset(), covered=None, sender=object(), closed_ago=5.0):
    engine = Engine.__new__(Engine)
    engine._settings, engine._contested, engine._sender = SETTINGS, contested, sender
    engine._covered = covered if covered is not None else NOW.timestamp()
    run = SimpleNamespace(rule=SimpleNamespace(rule={"indice": "us100"}))
    return engine._refusal(run, [0, 0, 0, 0, 0, 0, (NOW.timestamp() - closed_ago) * 1000], NOW.timestamp())


def test_the_reasons_of_a_live_signal_not_sent_are_read_from_what_the_engine_writes():
    rule = {"alerts": [{"title": "Up", "action": "message"}], "alert_calls": "ignore", "indice": "us100"}
    candidate = interpret(rule, {"type": "alertcondition", "title": "Up", "message": "not json"}, bar=[0, 1, 1, 1, 1, 0, 60_000],
                          ticker="CAPITAL_US100", timeframe="1", plots={}, now=NOW.timestamp())
    said = {
        "not_alone": _refusal(contested=frozenset({"us100"})),
        "too_old": _refusal(closed_ago=75),
        "stale_feed": _refusal(covered=NOW.timestamp() - 600),
        "no_sender": _refusal(sender=None),
        "not_taken": NOT_TAKEN.format(error="'confidence' is not a number"),
        "message": candidate.detail,
        "close_with_entry": CLOSE_WITH_ENTRY,
        "other": "Something the engine never says",
    }

    assert {key: refusal_reason(text) for key, text in said.items()} == {key: key for key in said}
    assert _refusal() is None


# ── the checks of the last minutes ──


def test_a_quiet_engine_passes_every_check_it_can_judge():
    found = checks(recent(), SETTINGS, RULES)

    assert states(found) == {
        "sent": "pass", "age": "pass", "runs": "pass", "ticks": "pass", "feed": "pass", "memory": "pass", "continued": "pass",
    }
    assert found[-1]["detail"] == "2 checks of the live rules' continued runs against a whole run: the same."
    assert health_warning(found) is None


def test_a_live_signal_not_sent_fails_unless_it_is_the_close_of_a_bar_that_raised_an_entry():
    by_design = [{"rule_id": LIVE_RULE, "outcome": "refused", "detail": CLOSE_WITH_ENTRY, "signals": 1},
                 {"rule_id": LIVE_RULE, "outcome": "sent", "detail": None, "signals": 2}]
    refused = [*by_design, {"rule_id": LIVE_RULE, "outcome": "refused", "detail": "The bar closed 75 s ago, more than 60 s", "signals": 1}]

    assert checks(recent(signals=by_design), SETTINGS, RULES)[0] == {
        "id": "sent", "label": "Live signals sent", "state": "pass", "detail": "3 live signals, none refused.",
    }
    assert checks(recent(signals=refused), SETTINGS, RULES)[0]["detail"] == (
        "1 live signal of “Live cross” not sent: The bar closed 75 s ago, more than 60 s."
    )


@pytest.mark.parametrize("age, state", [(30_000.0, "pass"), (30_001.0, "fail")])
def test_a_live_bar_late_by_half_of_the_signals_age_limit_fails(age, state):
    found = checks(recent(runs=[run_row(age_max_ms=age)]), SETTINGS, RULES)

    assert states(found)["age"] == state
    if state == "fail":
        assert found[1]["detail"] == "A bar of “Live cross” reached the engine 30 s after it closed: its signal is refused past 60 s."


def test_only_live_rules_bars_count_for_their_age():
    assert states(checks(recent(runs=[run_row(SHADOW_RULE, "shadow", "shadow", age_max_ms=59_000.0)]), SETTINGS, RULES))["age"] == "open"


@pytest.mark.parametrize("rule_id, ms, state", [
    (LIVE_RULE, 15_000, "pass"), (LIVE_RULE, 15_001, "fail"),
    # A rule on ten-second bars falls behind past its bar: half of it is 5 s.
    (SHADOW_RULE, 5_000, "pass"), (SHADOW_RULE, 5_001, "fail"),
])
def test_a_run_on_the_live_runner_over_half_of_its_limit_fails(rule_id, ms, state):
    found = checks(recent(runs=[run_row(rule_id, append_max_ms=ms)]), SETTINGS, RULES)

    assert states(found)["runs"] == state


def test_a_load_counts_against_the_runners_time_limit_and_an_ended_live_runner_fails():
    slow_load = checks(recent(runs=[run_row(load_max_ms=15_001, loads=1)]), SETTINGS, RULES)
    ended = checks(recent(runs=[run_row(ended=1, last_failure="The Pine runner ended (exit code 9)")]), SETTINGS, RULES)

    assert slow_load[2]["detail"] == "Loading the history of “Live cross” took 15 s, over half of the runner's time limit (30 s)."
    assert ended[2]["state"] == "fail" and ended[2]["detail"].startswith(
        "The live runner ended while it ran “Live cross”: The Pine runner ended (exit code 9)."
    )


def test_the_shadow_runners_troubles_are_the_pages_alone():
    runs = [run_row(), run_row(SHADOW_RULE, "shadow", "shadow", append_max_ms=9_000, ended=1, last_failure="took longer than 30 s")]

    found = checks(recent(runs=runs), SETTINGS, RULES)

    assert states(found)["runs"] == "pass" and health_warning(found) is None
    assert any("The shadow rules' runner ended while it ran “Claude · cross”" in note for note in notes(recent(runs=runs), SETTINGS, RULES))


@pytest.mark.parametrize("slow, state", [(2, "pass"), (3, "fail")])
def test_three_ticks_over_ten_seconds_fail(slow, state):
    assert states(checks(recent(ticks={"slow_ticks": slow}), SETTINGS, RULES))["ticks"] == state


@pytest.mark.parametrize("lag, state", [(45_000.0, "pass"), (45_001.0, "fail"), (None, "open")])
def test_a_feed_behind_by_half_of_the_stale_limit_fails(lag, state):
    assert states(checks(recent(ticks={"feed_lag_max_ms": lag}), SETTINGS, RULES))["feed"] == state


@pytest.mark.parametrize("used, kills, state", [(870.0, 0, "pass"), (871.0, 0, "fail"), (500.0, 1, "fail"), (None, 0, "open")])
def test_memory_over_85_percent_of_the_container_or_a_process_stopped_for_it_fails(used, kills, state):
    found = checks(recent(ticks={"memory_used_max_mb": used, "oom_kills": kills}), SETTINGS, RULES)

    assert states(found)["memory"] == state


def test_a_live_rules_continued_run_a_whole_run_gave_otherwise_fails_a_shadow_rules_is_the_pages_alone():
    difference = 'alertcondition "Up" at 2026-10-22T11:58:10Z only in the whole run'
    live = checks(recent(checks=[check_row(differed=1, last_difference=difference)]), SETTINGS, RULES)
    shadow_rows = [check_row(), check_row(SHADOW_RULE, "shadow", "shadow", differed=1, last_difference=difference)]
    shadow = recent(checks=shadow_rows)
    unchecked = checks(recent(checks=[check_row(SHADOW_RULE, "shadow", "shadow")]), SETTINGS, RULES)

    assert states(live)["continued"] == "fail"
    assert health_warning(live) == (
        f"A whole run of “Live cross” gave otherwise than its continued run ({difference}): it runs whole at each new bar "
        "from now on, and its signals since its last check were the continued run's."
    )
    assert states(checks(shadow, SETTINGS, RULES))["continued"] == "pass" and health_warning(checks(shadow, SETTINGS, RULES)) is None
    assert notes(shadow, SETTINGS, RULES) == [
        f"A whole run of “Claude · cross” gave otherwise than its continued run ({difference}): "
        "it runs whole at each new bar until the engine starts again.",
    ]
    assert states(unchecked)["continued"] == "open"


def test_health_says_the_first_failed_check_and_how_many_more():
    found = checks(recent(ticks={"slow_ticks": 4, "feed_lag_max_ms": 80_000.0}), SETTINGS, RULES)

    assert health_warning(found) == "4 ticks took longer than 10 s: new bars waited for the next one. (1 more on the Engine monitor)"


def test_beside_the_checks_the_page_says_what_waits_fails_or_was_not_written():
    runs = [run_row(SHADOW_RULE, "shadow", "shadow", append_max_ms=2_500, failed=2, last_failure="Undeclared identifier")]
    said = notes(recent(runs=runs, deferred=[{"rule_id": SHADOW_RULE, "ticks": 4}]), SETTINGS, RULES, McpLimits(),
                 {"dropped": 6, "error": "ConnectionError: refused"})

    assert said == [
        "The script of “Claude · cross” failed 2 times: Undeclared identifier.",
        "“Claude · cross” took 2.5 s a run, over the 2.0 s an engine day of it was held to when it was saved (admin.mcp.shadow_day_ms).",
        "“Claude · cross” waited 4 ticks for the shadow rules' share of a tick (5 s): its bars waited with it, none was lost.",
        "6 ticks of measures could not be written since the engine started: ConnectionError: refused.",
    ]


# ── the answers ──


def test_before_the_engine_measured_anything_the_answer_says_so_and_nothing_fails():
    empty = {"last_tick": None, "kept_since": None, "ticks": {}, "runs": [], "deferred": [], "signals": []}

    answer = now_answer(state=None, settings=SETTINGS, mcp_limits=McpLimits(), recent=empty, rules=RULES, now=NOW)

    assert answer["recording"] == {"days": 7, "configured_days": 7, "since": None, "written_at": None, "waiting": 0, "dropped": 0, "error": None}
    assert answer["last_tick"] is None and answer["slowest_run"] is None and answer["notes"] == []
    assert health_warning(answer["checks"]) is None and states(answer["checks"])["ticks"] == "open"


def test_the_days_the_engine_applies_are_said_beside_those_configured():
    state = {"figures": {"monitoring": {"days": 7, "written_at": NOW.timestamp(), "dropped": 0}}}
    answer = now_answer(state=state, settings=TaEngineSettings(monitoring_days=14), mcp_limits=McpLimits(), recent=recent(), rules=RULES, now=NOW)

    assert (answer["recording"]["days"], answer["recording"]["configured_days"], answer["recording"]["written_at"]) == (7, 14, NOW.isoformat())
    assert answer["slowest_run"] == {"rule_id": LIVE_RULE, "name": "Live cross", "ms": 40, "limit_ms": 30_000}


def test_a_period_lists_the_rules_on_with_what_they_did_and_every_step_of_the_delivery():
    rows = {
        "kept_since": NOW - timedelta(days=2),
        "rules": [{"rule_id": LIVE_RULE, "runs": 1380, "loads": 1, "continued": 1379, "run_p50_ms": 41.0, "run_p95_ms": 80.0,
                   "run_max_ms": 312}],
        # The live rule was checked on both runners: shadow before it went live.
        "checks": [
            {"rule_id": LIVE_RULE, "mode": "shadow", "runner": "shadow", "checks": 30, "differed": 0, "check_max_ms": 2_100,
             "last_difference": None},
            {"rule_id": LIVE_RULE, "mode": "live", "runner": "live", "checks": 114, "differed": 1, "check_max_ms": 3_400,
             "last_difference": "plot \"plot_0\" at 2026-10-22T09:12:30Z: 1.5 continued, 1.25 whole"},
        ],
        "signals": [
            {"rule_id": LIVE_RULE, "mode": "live", "outcome": "sent", "detail": None, "signals": 4, "late": 0},
            {"rule_id": LIVE_RULE, "mode": "live", "outcome": "refused", "detail": "The Capital.com feed is stale", "signals": 1, "late": 0},
            {"rule_id": LIVE_RULE, "mode": "live", "outcome": "refused", "detail": CLOSE_WITH_ENTRY, "signals": 2, "late": 0},
            {"rule_id": "ffffffffffff", "mode": "shadow", "outcome": "shadow", "detail": None, "signals": 3, "late": 1},
        ],
        "steps": [{"step": "close_to_sent", "signals": 4, "p50": 1550.0, "p95": 1720.0, "max": 3200.0}],
        "trader": [{"outcome": "executed", "reason": None, "signals": 3}, {"outcome": "refused", "reason": "Outside trading hours", "signals": 1}],
        "results": [{"rule_id": LIVE_RULE, "positions": 3, "open": 1, "won": 1, "lost": 1, "profit_loss": 12.456, "average_percent": 0.81234}],
    }

    answer = period_answer(hours=24, days=7, bucket=600, start=NOW - timedelta(hours=24), end=NOW, rule_id=None, state=None,
                           settings=SETTINGS, mcp_limits=McpLimits(), rows=rows, rules=RULES)

    # The rules on in the order of their file, then one measured since that is gone from it.
    assert [entry["rule_id"] for entry in answer["rules"]] == [LIVE_RULE, SHADOW_RULE, "ffffffffffff"]
    live = answer["rules"][0]
    assert (live["name"], live["bars"], live["limit_ms"]) == ("Live cross", {"type": "time", "timeframe": "1"}, 30_000)
    assert live["run_ms"] == {"p50": 41.0, "p95": 80.0, "max": 312.0}
    assert (live["continued"], live["checks"], live["differed"], live["check_ms_max"]) == (1379, 144, 1, 3_400)
    assert live["last_difference"] == 'plot "plot_0" at 2026-10-22T09:12:30Z: 1.5 continued, 1.25 whole'
    assert (answer["rules"][1]["checks"], answer["rules"][1]["check_ms_max"]) == (0, None)
    assert live["signals"] == {"raised": 7, "sent": 4, "not_sent": 1, "shadow": 0}
    assert answer["rules"][1]["runs"] == 0 and answer["rules"][2]["name"] is None
    delivery = answer["delivery"]
    assert [step["step"] for step in delivery["steps"]] == [key for key, _, _ in DELIVERY_STEPS]
    assert {entry["key"]: entry["count"] for entry in delivery["engine"]} == {"sent": 4, "not_sent": 1, "sending": 0, "close_with_entry": 2, "shadow": 3}
    assert delivery["refusals"] == [{"key": "stale_feed", "count": 1, "example": "The Capital.com feed is stale", "label": "The Capital.com feed was stale"}]
    assert (delivery["shadow_late"], [entry["count"] for entry in delivery["trader"]]) == (1, [3, 1])
    assert answer["results"] == [{"rule_id": LIVE_RULE, "positions": 3, "open": 1, "won": 1, "lost": 1, "profit_loss": 12.46, "average_percent": 0.812}]
    assert answer["recording"]["since"] == (NOW - timedelta(days=2)).isoformat()


def test_the_charts_mark_the_daily_restart_a_runner_started_again_and_the_engine_started_again():
    points = [
        {"t": 0, "runner_starts_min": 1, "runner_starts_max": 1, "restarted": False},
        {"t": 600, "runner_starts_min": 1, "runner_starts_max": 2, "restarted": False},
        {"t": 1200, "runner_starts_min": 2, "runner_starts_max": 3, "restarted": True},
        {"t": 1800, "runner_starts_min": 1, "runner_starts_max": 1, "restarted": False},
        {"t": 2400, "runner_starts_min": None, "runner_starts_max": None, "restarted": False},
    ]

    assert [(mark["t"], mark["kind"]) for mark in _marks(points)] == [(600, "runner_started"), (1200, "daily_restart"), (1800, "engine_started")]


# ── the routes ──


def test_the_last_minutes_are_read_with_the_checks(api):
    client, ctx = api
    ctx.queries.ta_monitor_recent_rows = recent(ticks={"slow_ticks": 5})

    body = client.get("/api/ta/monitor/now").json()

    assert body["window_minutes"] == 15 and states(body["checks"])["ticks"] == "fail"
    name, since, slow_tick_ms = ctx.queries.calls[-1]
    assert name == "ta_monitor_recent" and slow_tick_ms == 10_000
    assert timedelta(minutes=14) < datetime.now(timezone.utc) - since < timedelta(minutes=16)


def test_a_period_is_read_for_one_rule_in_bars_of_time_of_its_length(api):
    client, ctx = api
    ctx.queries.ta_monitor_period_rows = {}

    body = client.get(f"/api/ta/monitor?hours=24&rule={LIVE_RULE}").json()

    assert (body["hours"], body["bucket_seconds"], body["periods"], body["rule"]) == (24, 600, [1, 6, 24, 72, 168], LIVE_RULE)
    name, start, end, bucket, rule_id, signal_age, slow_tick_ms = ctx.queries.calls[-1]
    assert (name, bucket, rule_id, signal_age, slow_tick_ms) == ("ta_monitor_period", 600, LIVE_RULE, 60, 10_000)
    assert start.timestamp() % 600 == 0 and timedelta(hours=24) <= end - start < timedelta(hours=24, minutes=10)


def test_a_period_beyond_the_days_kept_is_brought_down_and_wrong_ones_refused(api):
    client, ctx = api
    ctx.queries.ta_monitor_period_rows = {}

    assert client.get("/api/ta/monitor?hours=720").json()["hours"] == 168
    assert client.get("/api/ta/monitor?hours=5").status_code == 422
    assert client.get("/api/ta/monitor?rule=../x").status_code == 422
    assert client.get("/api/ta/monitor?hours=0").status_code == 422


def test_the_monitor_says_when_the_database_is_down(api):
    client, ctx = api
    ctx.queries.failing = True

    assert client.get("/api/ta/monitor/now").status_code == 503
    assert client.get("/api/ta/monitor").status_code == 503


def _running(ctx):
    ctx.config_manager.config_data["trade"]["config"]["ta_engine"] = {"enabled": True}
    ctx.queries.ta_engine = {"updated_at": datetime.now(timezone.utc), "state": "running", "detail": "2 rule(s) on", "figures": {}}


def test_health_warns_of_what_can_cost_a_live_signal_and_the_signal_rules_page_says_the_same(api):
    client, ctx = api
    _running(ctx)
    ctx.queries.ta_monitor_recent_rows = recent(runs=[run_row("aaaaaaaaaaaa", append_max_ms=16_000)])

    services = {service["id"]: service for service in client.get("/api/system/health").json()["services"]}
    engine = client.get("/api/ta/engine").json()

    said = "A run of a rule no longer there (aaaaaaaaaaaa) took 16 s, over half of its limit (30 s)."
    assert (services["ta_engine"]["status"], services["ta_engine"]["detail"]) == ("warning", said)
    assert (engine["status"], engine["detail"]) == ("warning", said)


def test_a_slow_run_on_the_shadow_runner_leaves_health_as_it_is(api):
    client, ctx = api
    _running(ctx)
    ctx.queries.ta_monitor_recent_rows = recent(runs=[run_row(), run_row("aaaaaaaaaaaa", "shadow", "shadow", append_max_ms=29_000)])

    services = {service["id"]: service for service in client.get("/api/system/health").json()["services"]}

    assert (services["ta_engine"]["status"], services["ta_engine"]["detail"]) == ("ok", "2 rule(s) on")
