"""
The Engine monitor (Technical Analysis › Engine monitor): how fast the TA engine
runs each rule, how late its signals are, and what became of them. Pure: what
the engine measured of itself (ta_engine_tick, ta_rule_run, kept
``monitoring_days``), its journal (ta_signal) and the trader's (signal_journal,
order_execution, turbo_data_position) as AdminQueries reads them, shaped for
the page; and the checks of the last minutes, the first failed one of which
health says.

The limits are WATA's own, and a check fails at half of one, so that it says so
before a signal is lost: a bar older than ``max_signal_age_seconds`` sends
nothing, a run longer than ``run_timeout_seconds`` ends its runner, a tick that
outlasts ten seconds leaves new bars waiting, a feed behind by
``stale_feed_seconds`` stops every signal; a live rule's continued run that a
whole run gave otherwise had its signals from it until then. Health says only
what can cost a live signal: the live runner, the live signals, the ticks, the
feed, memory, the live rules' continued runs.
The shadow runner's troubles, a script that fails and the shadow rules waiting
their turn are said on the page alone, as a rule in error is.
"""

import math
from datetime import datetime, timedelta, timezone

from src.admin.ta_rules import RuleStore, timeframe_seconds
from src.ta_engine.engine import NO_SENDER, NOT_ALONE, NOT_SOURCE, NOT_TAKEN, STALE_FEED, TICK_SECONDS, TOO_OLD
from src.ta_engine.signals import CLOSE_WITH_ENTRY

# The minutes the checks look back over: a trouble is said for this long after it ends, never on and off.
WINDOW_MINUTES = 15
# The periods the page offers, in hours, up to the days the engine keeps; each one's bars of time.
PERIOD_HOURS = (1, 6, 24, 72, 168, 336, 720)
BUCKET_SECONDS = {1: 60, 6: 300, 24: 600, 72: 1800, 168: 3600, 336: 7200, 720: 21600}
DEFAULT_HOURS = 24
# A check fails at this share of the limit it watches.
SHARE_OF_LIMIT = 0.5
# Ticks over TICK_SECONDS in the window that fail the check (the daily restart's and loads' left out).
SLOW_TICKS = 3
MEMORY_SHARE = 0.85
LIVE_RUNNER, SHADOW_RUNNER = "live", "shadow"

# The steps of a live signal sent, each between two moments the journals keep: its signal (s), the run
# that raised it (r: its rule's whose tick_at is the signal's raised_at), the trader's journal (j) and
# its order (e). All are read on the server's clock. queries.py makes a percentile of each.
DELIVERY_STEPS = (
    ("close_to_tick", "Bar close → the engine's tick", "s.raised_at - s.bar_closed_at"),
    ("tick_to_run", "Tick → its run began (rules before it)", "r.started_at - s.raised_at"),
    ("run", "The script's run", "r.ended_at - r.started_at"),
    ("run_to_sent", "Run done → sent (journal, web server)", "s.sent_at - r.ended_at"),
    ("sent_to_order", "Sent → the order left (the trader)", "e.sent_at - s.sent_at"),
    ("order_to_saxo", "Order left → Saxo accepted it", "e.accepted_at - e.sent_at"),
    ("saxo_to_position", "Accepted → position confirmed", "e.confirmed_at - e.accepted_at"),
    ("sent_to_decided", "Sent → the trader's outcome, without an order", "CASE WHEN j.outcome <> 'executed' THEN j.decided_at - s.sent_at END"),
    ("close_to_sent", "Bar close → sent", "s.sent_at - s.bar_closed_at"),
    ("close_to_position", "Bar close → position confirmed", "e.confirmed_at - s.bar_closed_at"),
)
TOTALS = ("close_to_sent", "close_to_position")


def _start_of(template: str) -> str:
    return template.split("{")[0]


# Why a live signal was not sent, by the start of what its journal says (the engine's own texts).
REFUSALS = (
    ("not_alone", "Another rule is live on its indice", _start_of(NOT_ALONE)),
    ("not_source", "Its indice takes TradingView's signals", _start_of(NOT_SOURCE)),
    ("too_old", "Its bar closed too long before", _start_of(TOO_OLD)),
    ("stale_feed", "The Capital.com feed was stale", STALE_FEED),
    ("no_sender", "No way to send signals", NO_SENDER),
    ("not_taken", "Not a signal the web server takes", _start_of(NOT_TAKEN)),
    ("message", "Its message is no WATA signal", "The message"),
)
# A close of a bar that also raised an entry: by design, the entry sells the other side first.
CLOSE_WITH_ENTRY_KEY = "close_with_entry"
NOT_SENT_OUTCOMES = ("refused", "failed")

TRADER_OUTCOMES = {
    "executed": "Executed",
    "closed": "Positions closed",
    "refused": "Refused",
    "failed": "Failed",
    "ignored": "Nothing to do",
    None: "No outcome yet",
}


def refusal_reason(detail: str | None) -> str:
    """The kind of reason a live signal was not sent for: one of REFUSALS, CLOSE_WITH_ENTRY_KEY or "other"."""
    if detail == CLOSE_WITH_ENTRY:
        return CLOSE_WITH_ENTRY_KEY
    for key, _, start in REFUSALS:
        if detail and detail.startswith(start):
            return key
    return "other"


# ── what the page reads by ──


def kept_days(state: dict | None, settings) -> int:
    """The days the engine keeps its measures: what it said it applies, or the configured ones before it did."""
    figures = ((state or {}).get("figures") or {}).get("monitoring") or {}
    days = figures.get("days")
    return days if isinstance(days, int) and not isinstance(days, bool) and days > 0 else settings.monitoring_days


def periods(days: int) -> list[int]:
    """The periods offered, in hours, up to the days kept; those days whole when they are no period of their own."""
    offered = [hours for hours in PERIOD_HOURS if hours <= days * 24]
    if days * 24 not in offered:
        offered.append(days * 24)
    return offered


def period_of(hours: int, days: int) -> int:
    """The period asked for, brought down to the days kept; ValueError for one the page never offers."""
    offered = periods(days)
    if hours in offered:
        return hours
    if hours in PERIOD_HOURS:
        return max(offered)
    raise ValueError(f"No period of {hours} h: one of {', '.join(str(entry) for entry in offered)}")


def bucket_of(hours: int) -> int:
    """The seconds of each bar of time of a period's charts: 60 to 170 of them."""
    for period in PERIOD_HOURS:
        if hours <= period:
            return BUCKET_SECONDS[period]
    return BUCKET_SECONDS[PERIOD_HOURS[-1]]


def period_bounds(now: datetime, hours: int, bucket: int) -> tuple[datetime, datetime]:
    """From the start of the bucket ``hours`` ago, to now: whole bars of time from the first."""
    start = (now - timedelta(hours=hours)).timestamp()
    return datetime.fromtimestamp(math.floor(start / bucket) * bucket, timezone.utc), now


def limits(settings, mcp_limits=None) -> dict:
    """WATA's limits the checks watch, in ms (MB for memory), as the page draws them."""
    return {
        "tick_ms": TICK_SECONDS * 1000,
        "run_timeout_ms": settings.run_timeout_seconds * 1000,
        "max_signal_age_ms": settings.max_signal_age_seconds * 1000,
        "stale_feed_ms": settings.stale_feed_seconds * 1000,
        "share": SHARE_OF_LIMIT,
        "memory_share": MEMORY_SHARE,
        "slow_ticks": SLOW_TICKS,
        "runner_memory_mb": settings.runner_memory_mb,
        "shadow_runner_memory_mb": settings.shadow_runner_memory_mb,
        "shadow_ms_per_tick": settings.shadow_seconds_per_tick * 1000,
        "shadow_day_ms": getattr(mcp_limits, "shadow_day_ms", None),
    }


def rule_limit_ms(rule: dict | None, settings) -> int:
    """How long a run of ``rule`` may take: the runner's time limit, or its bar when shorter (it would fall behind)."""
    limit = settings.run_timeout_seconds
    bars = (rule or {}).get("bars") or {}
    if bars.get("type") == "time":
        seconds = timeframe_seconds(str(bars.get("timeframe") or ""))
        if seconds:
            limit = min(limit, seconds)
    return limit * 1000


def rules_by_id(store) -> dict[str, dict]:
    """The signal rules by id as rules.json has them now; none without Technical Analysis or a readable file."""
    if store is None:
        return {}
    try:
        return {rule["id"]: rule for rule in RuleStore(store).load()["rules"]}
    except Exception:
        return {}


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        value = datetime.fromtimestamp(value, timezone.utc)
    return value.isoformat()


def recording(state: dict | None, settings, kept_since) -> dict:
    """How the engine keeps its measures: the days it applies and those configured, since when, its last write."""
    figures = ((state or {}).get("figures") or {}).get("monitoring") or {}
    return {
        "days": kept_days(state, settings),
        "configured_days": settings.monitoring_days,
        "since": _iso(kept_since),
        "written_at": _iso(figures.get("written_at")),
        "waiting": figures.get("waiting") or 0,
        "dropped": figures.get("dropped") or 0,
        "error": figures.get("error"),
    }


# ── sentences ──


def duration(ms: float | None) -> str:
    """A duration as the page says it: 820 ms, 3.4 s, 75 s."""
    if ms is None:
        return "-"
    if ms < 1000:
        return f"{round(ms)} ms"
    if ms < 10_000:
        return f"{ms / 1000:.1f} s"
    return f"{round(ms / 1000)} s"


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _name(rule_id: str, rules: dict) -> str:
    rule = rules.get(rule_id)
    return f"“{rule['name']}”" if rule else f"a rule no longer there ({rule_id})"


# ── the checks of the last minutes ──


def _check(check_id: str, label: str, state: str, detail: str) -> dict:
    return {"id": check_id, "label": label, "state": state, "detail": detail}


def _sent_check(signals: list[dict], rules: dict) -> dict:
    label = "Live signals sent"
    raised = sum(row.get("signals") or 0 for row in signals)
    not_sent = [
        row for row in signals
        if row.get("outcome") in NOT_SENT_OUTCOMES and refusal_reason(row.get("detail")) != CLOSE_WITH_ENTRY_KEY
    ]
    if not_sent:
        count = sum(row.get("signals") or 0 for row in not_sent)
        worst = max(not_sent, key=lambda row: row.get("signals") or 0)
        why = worst.get("detail") or ("not answered" if worst.get("outcome") == "failed" else "refused")
        others = len({row.get("rule_id") for row in not_sent}) - 1
        return _check("sent", label, "fail", (
            f"{_plural(count, 'live signal')} of {_name(worst['rule_id'], rules)}"
            f"{f' and {others} other rule(s)' if others > 0 else ''} not sent: {why}."
        ))
    if raised == 0:
        return _check("sent", label, "pass", f"No live signal in the last {WINDOW_MINUTES} minutes.")
    return _check("sent", label, "pass", f"{_plural(raised, 'live signal')}, none refused.")


def _age_check(runs: list[dict], rules: dict, settings) -> dict:
    label = "Live bars run in time"
    limit = settings.max_signal_age_seconds * 1000
    ages = [row for row in runs if row.get("mode") == "live" and row.get("age_max_ms") is not None]
    if not ages:
        return _check("age", label, "open", f"No live rule ran on a new bar in the last {WINDOW_MINUTES} minutes.")
    worst = max(ages, key=lambda row: row["age_max_ms"])
    if worst["age_max_ms"] > SHARE_OF_LIMIT * limit:
        return _check("age", label, "fail", (
            f"A bar of {_name(worst['rule_id'], rules)} reached the engine {duration(worst['age_max_ms'])} after it closed: "
            f"its signal is refused past {duration(limit)}."
        ))
    return _check("age", label, "pass", (
        f"Live bars reached the engine at most {duration(worst['age_max_ms'])} after they closed (refused past {duration(limit)})."
    ))


def _runs_check(runs: list[dict], rules: dict, settings) -> dict:
    label = "Runs within their limits"
    on_live = [row for row in runs if row.get("runner") == LIVE_RUNNER]
    if not on_live:
        return _check("runs", label, "open", f"Nothing ran on the live runner in the last {WINDOW_MINUTES} minutes.")
    ended = [row for row in on_live if row.get("ended")]
    if ended:
        row = ended[0]
        return _check("runs", label, "fail", (
            f"The live runner ended while it ran {_name(row['rule_id'], rules)}: {row.get('last_failure') or 'no reason given'}. "
            "Its rules loaded again, and the alerts of that bar were not sent."
        ))
    timeout = settings.run_timeout_seconds * 1000
    measured = []
    for row in on_live:
        limit = rule_limit_ms(rules.get(row["rule_id"]), settings)
        if row.get("append_max_ms") is not None:
            measured.append((row["append_max_ms"] / limit, row, row["append_max_ms"], limit, False))
        if row.get("load_max_ms") is not None:
            measured.append((row["load_max_ms"] / timeout, row, row["load_max_ms"], timeout, True))
    if not measured:
        return _check("runs", label, "open", "No run on the live runner was timed.")
    share, row, ms, limit, load = max(measured, key=lambda entry: entry[0])
    name = _name(row["rule_id"], rules)
    if share > SHARE_OF_LIMIT:
        if load:
            return _check("runs", label, "fail", (
                f"Loading the history of {name} took {duration(ms)}, over half of the runner's time limit ({duration(limit)})."
            ))
        return _check("runs", label, "fail", f"A run of {name} took {duration(ms)}, over half of its limit ({duration(limit)}).")
    what = "Its history's load" if load else "The slowest run"
    return _check("runs", label, "pass", f"{what} on the live runner took {duration(ms)} ({name}, its limit {duration(limit)}).")


def _ticks_check(ticks: dict) -> dict:
    label = "Ticks on time"
    if not ticks.get("ticks"):
        return _check("ticks", label, "open", f"No tick in the last {WINDOW_MINUTES} minutes.")
    slow = ticks.get("slow_ticks") or 0
    if slow >= SLOW_TICKS:
        return _check("ticks", label, "fail", (
            f"{_plural(slow, 'tick')} took longer than {TICK_SECONDS} s: new bars waited for the next one."
        ))
    steady = ticks.get("steady_tick_max_ms")
    return _check("ticks", label, "pass", (
        f"Ticks took {duration(steady)} at most, one every {TICK_SECONDS} s (the daily restart, loads and checks aside)."
        if steady is not None else "Every tick had a load, a check or the daily restart: none to judge by."
    ))


def _feed_check(ticks: dict, settings) -> dict:
    label = "Feed up to date"
    lag = ticks.get("feed_lag_max_ms")
    limit = settings.stale_feed_seconds * 1000
    if lag is None:
        return _check("feed", label, "open", f"The feed's bars were not read in the last {WINDOW_MINUTES} minutes.")
    if lag > SHARE_OF_LIMIT * limit:
        return _check("feed", label, "fail", (
            f"The Capital.com feed fell {duration(lag)} behind: nothing is sent past {duration(limit)}."
        ))
    return _check("feed", label, "pass", (
        f"The Capital.com feed was {duration(lag)} behind at most, the engine's first tick after a start aside "
        f"(nothing is sent past {duration(limit)})."
    ))


def _memory_check(ticks: dict) -> dict:
    label = "Memory"
    kills = ticks.get("oom_kills") or 0
    if kills > 0:
        return _check("memory", label, "fail", f"The kernel stopped {_plural(kills, 'process')} of the engine's container for memory.")
    used, limit = ticks.get("memory_used_max_mb"), ticks.get("memory_limit_mb")
    if used is None or not limit:
        return _check("memory", label, "open", "The container's memory cannot be read here (it is, on Linux with cgroup v2).")
    if used > MEMORY_SHARE * limit:
        return _check("memory", label, "fail", (
            f"The container used {round(used):,} MB of its {round(limit):,} MB: at its limit, the kernel stops a runner."
        ))
    return _check("memory", label, "pass", f"The container used {round(used):,} MB of its {round(limit):,} MB at most.")


def _continued_check(found: list[dict], rules: dict) -> dict:
    label = "Continued runs as whole runs"
    live = [row for row in found if row.get("mode") == "live"]
    if not live:
        return _check("continued", label, "open", f"No live rule's continued run was checked in the last {WINDOW_MINUTES} minutes.")
    differed = [row for row in live if row.get("differed")]
    if differed:
        row = differed[0]
        return _check("continued", label, "fail", (
            f"A whole run of {_name(row['rule_id'], rules)} gave otherwise than its continued run "
            f"({row.get('last_difference') or 'no detail'}): it runs whole at each new bar from now on, "
            "and its signals since its last check were the continued run's."
        ))
    count = sum(row.get("checks") or 0 for row in live)
    return _check("continued", label, "pass", f"{_plural(count, 'check')} of the live rules' continued runs against a whole run: the same.")


def checks(recent: dict, settings, rules: dict) -> list[dict]:
    """What the last minutes say of what can cost a live signal, in health's order."""
    ticks = recent.get("ticks") or {}
    runs = recent.get("runs") or []
    return [
        _sent_check(recent.get("signals") or [], rules),
        _age_check(runs, rules, settings),
        _runs_check(runs, rules, settings),
        _ticks_check(ticks),
        _feed_check(ticks, settings),
        _memory_check(ticks),
        _continued_check(recent.get("checks") or [], rules),
    ]


def health_warning(found: list[dict]) -> str | None:
    """What health says of the TA engine when a check failed: the first, and how many more."""
    failed = [check for check in found if check["state"] == "fail"]
    if not failed:
        return None
    more = len(failed) - 1
    return failed[0]["detail"] + (f" ({more} more on the Engine monitor)" if more else "")


def notes(recent: dict, settings, rules: dict, mcp_limits=None, kept: dict | None = None) -> list[str]:
    """What the page says beside the checks: nothing a live signal waits on."""
    said = []
    runs = recent.get("runs") or []
    for row in runs:
        name = _name(row["rule_id"], rules)
        if row.get("runner") == SHADOW_RUNNER and row.get("ended"):
            said.append(
                f"The shadow rules' runner ended while it ran {name} ({row.get('last_failure') or 'no reason given'}): "
                "the shadow rules loaded again, no live rule was touched."
            )
        elif row.get("runner") == SHADOW_RUNNER and row.get("append_max_ms") is not None:
            limit = rule_limit_ms(rules.get(row["rule_id"]), settings)
            if row["append_max_ms"] > SHARE_OF_LIMIT * limit:
                said.append(f"{name} took {duration(row['append_max_ms'])} a run on the shadow runner, over half of its limit ({duration(limit)}).")
        if row.get("failed"):
            said.append(f"The script of {name} failed {_plural(row['failed'], 'time')}: {row.get('last_failure') or 'no reason given'}.")
        rule = rules.get(row["rule_id"]) or {}
        most = getattr(mcp_limits, "shadow_day_ms", None)
        if rule.get("made_by") and most and (row.get("append_max_ms") or 0) > most:
            said.append(
                f"{name} took {duration(row['append_max_ms'])} a run, over the {duration(most)} an engine day of it was held to "
                "when it was saved (admin.mcp.shadow_day_ms)."
            )
    for row in recent.get("checks") or []:
        if row.get("mode") != "live" and row.get("differed"):
            said.append(
                f"A whole run of {_name(row['rule_id'], rules)} gave otherwise than its continued run "
                f"({row.get('last_difference') or 'no detail'}): it runs whole at each new bar until the engine starts again."
            )
    for row in recent.get("deferred") or []:
        said.append(
            f"{_name(row['rule_id'], rules)} waited {_plural(row.get('ticks') or 0, 'tick')} for the shadow rules' share of a tick "
            f"({settings.shadow_seconds_per_tick} s): its bars waited with it, none was lost."
        )
    if kept and kept.get("dropped"):
        said.append(
            f"{_plural(kept['dropped'], 'tick')} of measures could not be written since the engine started"
            f"{': ' + kept['error'] if kept.get('error') else ''}."
        )
    return said


# ── the answers ──


def _ms(value) -> float | None:
    return None if value is None else round(float(value), 1)


def _last_tick(row: dict | None) -> dict | None:
    if not row:
        return None
    tick_at, covered = row.get("tick_at"), row.get("covered_until")
    return {
        **{key: value for key, value in row.items() if key != "kept_since"},
        "feed_lag_ms": _ms((tick_at - covered).total_seconds() * 1000) if tick_at and covered else None,
    }


def _slowest_run(runs: list[dict], rules: dict, settings) -> dict | None:
    """The run of the live runner closest to its limit."""
    measured = [
        (row["append_max_ms"] / rule_limit_ms(rules.get(row["rule_id"]), settings), row)
        for row in runs if row.get("runner") == LIVE_RUNNER and row.get("append_max_ms") is not None
    ]
    if not measured:
        return None
    _, row = max(measured, key=lambda entry: entry[0])
    rule = rules.get(row["rule_id"])
    return {
        "rule_id": row["rule_id"], "name": rule["name"] if rule else None, "ms": row["append_max_ms"],
        "limit_ms": rule_limit_ms(rule, settings),
    }


def now_answer(*, state: dict | None, settings, mcp_limits, recent: dict, rules: dict, now: datetime) -> dict:
    """GET /api/ta/monitor/now: the last minutes, their checks and what the page says beside them."""
    ticks = recent.get("ticks") or {}
    runs = recent.get("runs") or []
    signals = recent.get("signals") or []
    kept = recording(state, settings, recent.get("kept_since"))
    live = [row for row in runs if row.get("mode") == "live"]
    return {
        "checked_at": now.isoformat(),
        "window_minutes": WINDOW_MINUTES,
        "recording": kept,
        "limits": limits(settings, mcp_limits),
        "last_tick": _last_tick(recent.get("last_tick")),
        "window": {
            "ticks": ticks.get("ticks") or 0,
            "tick_p50_ms": _ms(ticks.get("tick_p50_ms")),
            "tick_max_ms": ticks.get("tick_max_ms"),
            "steady_tick_max_ms": ticks.get("steady_tick_max_ms"),
            "slow_ticks": ticks.get("slow_ticks") or 0,
            "feed_lag_max_ms": _ms(ticks.get("feed_lag_max_ms")),
            "memory_used_max_mb": ticks.get("memory_used_max_mb"),
            "memory_limit_mb": ticks.get("memory_limit_mb"),
            "oom_kills": ticks.get("oom_kills") or 0,
            "deferred": ticks.get("deferred") or 0,
            "runs": sum(row.get("runs") or 0 for row in runs),
            "checks": sum(row.get("checks") or 0 for row in recent.get("checks") or []),
            "checks_differed": sum(row.get("differed") or 0 for row in recent.get("checks") or []),
            "failed": sum(row.get("failed") or 0 for row in runs),
            "ended": sum(row.get("ended") or 0 for row in runs),
            "live_age_max_ms": _ms(max((row["age_max_ms"] for row in live if row.get("age_max_ms") is not None), default=None)),
            "live_delay_max_ms": _ms(max((row["delay_max_ms"] for row in live if row.get("delay_max_ms") is not None), default=None)),
            "live_signals": sum(row.get("signals") or 0 for row in signals),
            "live_not_sent": sum(
                row.get("signals") or 0 for row in signals
                if row.get("outcome") in NOT_SENT_OUTCOMES and refusal_reason(row.get("detail")) != CLOSE_WITH_ENTRY_KEY
            ),
        },
        "slowest_run": _slowest_run(runs, rules, settings),
        "checks": checks(recent, settings, rules),
        "notes": notes(recent, settings, rules, mcp_limits, kept),
    }


def _spread(row: dict, name: str) -> dict:
    return {"p50": _ms(row.get(f"{name}_p50_ms")), "p95": _ms(row.get(f"{name}_p95_ms")), "max": _ms(row.get(f"{name}_max_ms"))}


def _signal_counts(rows: list[dict]) -> dict[str, dict]:
    counts: dict[str, dict] = {}
    for row in rows:
        entry = counts.setdefault(row["rule_id"], {"raised": 0, "sent": 0, "not_sent": 0, "shadow": 0})
        signals = row.get("signals") or 0
        entry["raised"] += signals
        if row.get("mode") == "shadow":
            entry["shadow"] += signals
        elif row.get("outcome") == "sent":
            entry["sent"] += signals
        elif row.get("outcome") in (*NOT_SENT_OUTCOMES, "sending") and refusal_reason(row.get("detail")) != CLOSE_WITH_ENTRY_KEY:
            entry["not_sent"] += signals
    return counts


def _checks_of(rows: list[dict]) -> dict[str, dict]:
    """The checks of each rule's continued runs over a period, whatever runner and mode it had."""
    found: dict[str, dict] = {}
    for row in rows:
        entry = found.setdefault(row["rule_id"], {"checks": 0, "differed": 0, "check_max_ms": None, "last_difference": None})
        entry["checks"] += row.get("checks") or 0
        entry["differed"] += row.get("differed") or 0
        if row.get("check_max_ms") is not None:
            entry["check_max_ms"] = max(entry["check_max_ms"] or 0, row["check_max_ms"])
        entry["last_difference"] = entry["last_difference"] or row.get("last_difference")
    return found


def _rule_entry(rule_id: str, rule: dict | None, row: dict, settings, deferred: int, signals: dict, checked: dict | None = None) -> dict:
    checked = checked or {}
    return {
        "rule_id": rule_id,
        "name": rule["name"] if rule else None,
        "mode": rule["mode"] if rule else None,
        "made_by": rule.get("made_by") if rule else None,
        "bars": rule["bars"] if rule else None,
        "limit_ms": rule_limit_ms(rule, settings),
        "runs": row.get("runs") or 0,
        "loads": row.get("loads") or 0,
        # The runs that continued the rule's run, and its checks against a whole run.
        "continued": row.get("continued") or 0,
        "checks": checked.get("checks") or 0,
        "differed": checked.get("differed") or 0,
        "check_ms_max": checked.get("check_max_ms"),
        "last_difference": checked.get("last_difference"),
        "failed": row.get("failed") or 0,
        "ended": row.get("ended") or 0,
        "deferred": deferred,
        "asks": row.get("asks") or 0,
        "run_ms": _spread(row, "run"),
        "pinets_ms_avg": _ms(row.get("pinets_avg_ms")),
        "load_ms_max": row.get("load_max_ms"),
        "bars_max": row.get("bars_max"),
        "delay_ms": _spread(row, "delay"),
        "age_max_ms": _ms(row.get("age_max_ms")),
        "last_run_at": _iso(row.get("last_run_at")),
        "last_failure": row.get("last_failure"),
        "signals": signals or {"raised": 0, "sent": 0, "not_sent": 0, "shadow": 0},
    }


def _marks(points: list[dict]) -> list[dict]:
    """The moments the charts mark: the daily restart, the live runner started again, the engine started again."""
    marks, before = [], None
    for point in points:
        started_min, started_max = point.get("runner_starts_min"), point.get("runner_starts_max")
        if point.get("restarted"):
            marks.append({"t": point["t"], "kind": "daily_restart", "label": "Daily restart"})
        elif before is not None and started_min is not None and started_min < before:
            marks.append({"t": point["t"], "kind": "engine_started", "label": "The engine started again"})
        elif started_max is not None and (
            (before is not None and started_max > before) or (started_min is not None and started_max > started_min)
        ):
            marks.append({"t": point["t"], "kind": "runner_started", "label": "The live runner started again"})
        if started_max is not None:
            before = started_max
    return marks


def _delivery(rows: dict, rule_id: str | None) -> dict:
    steps = {row["step"]: row for row in rows.get("steps") or []}
    engine = {"sent": 0, "not_sent": 0, "sending": 0, "shadow": 0, CLOSE_WITH_ENTRY_KEY: 0}
    refusals: dict[str, dict] = {}
    shadow_late = 0
    for row in rows.get("signals") or []:
        signals = row.get("signals") or 0
        if row.get("mode") == "shadow":
            engine["shadow"] += signals
            shadow_late += row.get("late") or 0
            continue
        outcome = row.get("outcome")
        if outcome == "sent":
            engine["sent"] += signals
        elif outcome == "sending":
            engine["sending"] += signals
        elif outcome in NOT_SENT_OUTCOMES:
            reason = refusal_reason(row.get("detail")) if outcome == "refused" else "failed"
            if reason == CLOSE_WITH_ENTRY_KEY:
                engine[CLOSE_WITH_ENTRY_KEY] += signals
                continue
            engine["not_sent"] += signals
            entry = refusals.setdefault(reason, {"key": reason, "count": 0, "example": row.get("detail")})
            entry["count"] += signals
    labels = {key: label for key, label, _ in REFUSALS} | {"failed": "The web server did not take it", "other": "Another reason"}
    trader: dict[tuple, dict] = {}
    for row in rows.get("trader") or []:
        key = (row.get("outcome"), row.get("reason"))
        entry = trader.setdefault(key, {
            "key": row.get("outcome") or "none", "label": TRADER_OUTCOMES.get(row.get("outcome"), row.get("outcome")),
            "reason": row.get("reason"), "count": 0,
        })
        entry["count"] += row.get("signals") or 0
    return {
        "rule": rule_id,
        "sent": engine["sent"],
        "steps": [
            {
                "step": key, "label": label, "total": key in TOTALS,
                "signals": (steps.get(key) or {}).get("signals") or 0,
                "p50": _ms((steps.get(key) or {}).get("p50")), "p95": _ms((steps.get(key) or {}).get("p95")),
                "max": _ms((steps.get(key) or {}).get("max")),
            }
            for key, label, _ in DELIVERY_STEPS
        ],
        "engine": [
            {"key": "sent", "label": "Sent", "count": engine["sent"]},
            {"key": "not_sent", "label": "Not sent", "count": engine["not_sent"]},
            {"key": "sending", "label": "Never completed (the engine stopped as it sent)", "count": engine["sending"]},
            {"key": CLOSE_WITH_ENTRY_KEY, "label": "Closes on a bar that raised an entry (by design)", "count": engine[CLOSE_WITH_ENTRY_KEY]},
            {"key": "shadow", "label": "In shadow (journaled, never sent)", "count": engine["shadow"]},
        ],
        "refusals": sorted(
            ({**entry, "label": labels.get(entry["key"], entry["key"])} for entry in refusals.values()),
            key=lambda entry: -entry["count"],
        ),
        "shadow_late": shadow_late,
        "trader": sorted(trader.values(), key=lambda entry: -entry["count"]),
    }


def period_answer(
    *, hours: int, days: int, bucket: int, start: datetime, end: datetime, rule_id: str | None, state: dict | None,
    settings, mcp_limits, rows: dict, rules: dict,
) -> dict:
    """GET /api/ta/monitor: a period of the engine's runs and ticks, its signals' delivery and results."""
    by_rule = {row["rule_id"]: row for row in rows.get("rules") or []}
    deferred = {row["rule_id"]: row.get("ticks") or 0 for row in rows.get("deferred") or []}
    signals = _signal_counts(rows.get("signals") or [])
    checked = _checks_of(rows.get("checks") or [])
    # The rules on now and those measured in the period, in the order of their file; then the ones gone from it.
    measured = set(by_rule) | set(signals)
    ids = [known for known, rule in rules.items() if rule.get("mode") != "off" or known in measured]
    ids += sorted(measured - set(ids))
    if rule_id is not None:
        ids = [rule_id]
    ticks = [{**point, "feed_lag_p50_ms": _ms(point.get("feed_lag_p50_ms")), "feed_lag_max_ms": _ms(point.get("feed_lag_max_ms")),
              "tick_p50_ms": _ms(point.get("tick_p50_ms"))} for point in rows.get("ticks") or []]
    results = {row["rule_id"]: row for row in rows.get("results") or []}
    return {
        "hours": hours,
        "periods": periods(days),
        "bucket_seconds": bucket,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "rule": rule_id,
        "recording": recording(state, settings, rows.get("kept_since")),
        "limits": limits(settings, mcp_limits),
        "rules": [
            _rule_entry(
                entry, rules.get(entry), by_rule.get(entry) or {}, settings, deferred.get(entry, 0), signals.get(entry),
                checked.get(entry),
            )
            for entry in dict.fromkeys(ids)
        ],
        "series": {
            "ticks": [{key: value for key, value in point.items() if not key.startswith("runner_starts")} for point in ticks],
            "runs": [
                {**point, **{name: _ms(point.get(name)) for name in ("run_p50_ms", "run_p95_ms", "delay_p50_ms", "delay_max_ms")}}
                for point in rows.get("runs_by_bucket") or []
            ],
            "signals": rows.get("signals_by_bucket") or [],
            "marks": _marks(ticks),
        },
        "delivery": _delivery(rows, rule_id),
        "results": [
            {
                "rule_id": entry, "positions": row.get("positions") or 0, "open": row.get("open") or 0,
                "won": row.get("won") or 0, "lost": row.get("lost") or 0,
                "profit_loss": None if row.get("profit_loss") is None else round(float(row["profit_loss"]), 2),
                "average_percent": None if row.get("average_percent") is None else round(float(row["average_percent"]), 3),
            }
            for entry, row in results.items()
        ],
    }
