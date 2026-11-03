"""
A study of a Pine script, run as a child process of the admin for Claude's
pine_check, pine_run and pine_signals (src/admin/studies.py):

    python -m src.research.study check   --request <file.json> --out <file.json>
    python -m src.research.study run     --request <file.json> --out <file.json>
    python -m src.research.study signals --request <file.json> --out <file.json>
    python -m src.research.study rule    --request <file.json> --out <file.json>
    python -m src.research.study view    --request <file.json> --out <file.json>

(``--data files:<folder>``: Capital.com's and Saxo's bars from recorded day
files, as a backtest reads them, instead of the database.)

A request names a saved script (``"script": {"id", "version"}``) or carries a
source not saved yet (``"source"``), with the asset (``capital/US100``...), the
side, the bars (``"1"``, ``"45S"``, ``"R60"``), the inputs (``{"in_0": 9}``)
and, for signals, what each alert becomes (``{"Long": "long"}``). Nothing is
sent and nothing is written but the answer (``--out``, written whole).

- check: compiled and run once on made-up bars, no data read: where it fails
  (the file and line, the library's when it is one's), or its inputs, plots,
  alerts and runtime warnings. A library is checked through an indicator that
  imports it.
- run: one continuous run over the days asked (days of the trading timezone),
  on their bars and ``warmup_days`` of bars before: each plot summarised and
  downsampled, the alerts, PineTS's runtime warnings.
- signals: the engine's days, as a signal rule would raise them
  (src/ta_engine/replay.py), the runner's answers shared with the backtests
  (``--signal-cache``).
- rule: the rule check of a script (src/research/rule_check.py): what its
  source says, then its run test on the engine's days, each run as a backtest
  runs it and again ended at chosen bars, as live had them.
- view: one engine day as a backtest ran it, the hours asked of it as Claude
  reads them (chart_view, src/research/day_view.py): its bars gathered into
  rows with the script's plots, alerts and a run's trades, the day's figures,
  what price did after each alert, and its picture (src/research/chart_picture.py,
  a PNG in base64). Without a script, the asset's prices alone.

It gives way to the trading services (nice 10, ahead of the backtests at 19)
and is the first the kernel stops when the container runs out of memory.
"""

import argparse
import asyncio
import base64
import dataclasses
import hashlib
import json
import math
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from src.admin.ta_rules import (
    ALERT_CALL_ACTIONS,
    IGNORE,
    InvalidRule,
    RuleStore,
    bars_label,
    checked_alerts,
    checked_bars,
    checked_history_days,
    checked_inputs,
    timeframe_seconds,
)
from src.admin.ta_scripts import LIBRARY, InvalidScript, ScriptNotFound, ScriptStore, pine_facts
from src.backtest import regimes
from src.backtest.clock import TradingClock
from src.price_history.process import lower_priority
from src.research import day_view
from src.research import rule_check as checks
from src.research.rule_check import CHECKER_PATH, Checker, CheckerMissing
from src.ta_engine.replay import (
    DAY,
    REPLAY_RULE_ID,
    ReplaySession,
    _builder,
    _decimals,
    asset_rows_for,
    built_bars,
    engine_rule_of,
    engine_windows,
    restart_minute_of,
    ta_settings,
    too_large,
)
from src.ta_engine.rules import EngineRule
from src.ta_engine.runner import RUNNER_PATH, NodeRunner, RunnerDown, RunnerFailed, find_node, runner_command
from src.ta_engine.signals import iso_seconds
from src.ta_engine.tickers import parse_ticker

STUDY_NICE = 10
MODES = ("check", "run", "signals", "rule", "view")
# A script not saved yet runs under this id: it pins nothing that is on disk.
DRAFT_ID = "draft0000000"
DEFAULT_MEMORY_MB = 768
DEFAULT_TIMEOUT_SECONDS = 90
# What one run holds at most: about six days of ten-second bars, ten weeks of minutes.
MAX_RUN_BARS = 50_000
DEFAULT_POINTS = 300
MAX_POINTS = 2000
MAX_PLOTS = 20
MAX_ALERTS = 500
MAX_SIGNALS = 500
MAX_WARMUP_DAYS = 30
MAX_STUDY_DAYS = 31
# Made-up minutes for a check: no data is read.
CHECK_BARS = [
    [index * 60_000, 100.0 + index % 7, 101.0 + index % 7, 99.0 + index % 7, 100.0 + (index * 3) % 7, 1, (index + 1) * 60_000]
    for index in range(300)
]
LIBRARY_CHECK = """//@version=6
indicator("Library check")
import user/{name}/1 as checked
plot(close)
"""


class StudyRefused(Exception):
    """A study that cannot be made as asked, said in a sentence."""


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def bars_of(text: str) -> dict:
    text = str(text or "1").strip()
    if text[:1] in ("R", "r"):
        return checked_bars({"type": "range", "ticks": int(text[1:]) if text[1:].isdigit() else 0})
    return checked_bars({"type": "time", "timeframe": text})


def _asset(text: str) -> dict:
    provider, _, key = str(text or "").partition("/")
    if provider not in ("capital", "dukascopy", "saxo") or not key:
        raise StudyRefused("The asset is capital/<EPIC>, saxo/<id> or dukascopy/<label>")
    return {"provider": provider, "key": key}


def _libraries_with_sources(scripts: ScriptStore, pins: list[dict]) -> tuple:
    return tuple({**pin, "source": scripts.source(pin["id"], pin["version"])} for pin in pins)


def study_rule(request: dict, scripts: ScriptStore) -> tuple[dict, EngineRule]:
    """The rule a study runs, as the engine runs one: of a saved script, or of a source not saved yet."""
    store = RuleStore(scripts)
    side = request.get("side", "bid")
    if side not in ("bid", "ask"):
        raise StudyRefused("The side is bid or ask")
    alert_calls = request.get("alert_calls", IGNORE)
    if alert_calls not in ALERT_CALL_ACTIONS:
        raise StudyRefused("alert() calls are ignored or read as messages")
    inputs = request.get("inputs") or {}
    if not isinstance(inputs, dict):
        raise StudyRefused('The inputs are given by their id: {"in_0": 9}')
    mapping = request.get("alerts") or {}
    if not isinstance(mapping, dict):
        raise StudyRefused('What each alert becomes is given by its title: {"Long": "long"}')
    rule = {
        "id": REPLAY_RULE_ID,
        "asset": _asset(request.get("asset")),
        "side": side,
        "bars": bars_of(request.get("bars", "1")),
        "inputs": checked_inputs([{"id": input_id, "value": value} for input_id, value in inputs.items()]),
        "alerts": checked_alerts([{"title": title, "action": action} for title, action in mapping.items()]),
        "alert_calls": alert_calls,
        "confidence_plot": None,
        "indice": request.get("indice") or "us100",
        "mode": "shadow",
    }
    if request.get("source") is not None:
        source = request["source"]
        facts = pine_facts(source)
        if facts.kind == LIBRARY:
            raise StudyRefused("A library does not run on its own: pine_check checks it through an indicator")
        name = request.get("name") or "Draft"
        libraries = store.libraries_of(facts, name)
        rule.update(name=f"{name} (draft)", script={"id": DRAFT_ID, "version": 0, "sha256": _sha256(source)}, libraries=libraries)
        return rule, EngineRule(rule=rule, source=source, libraries=_libraries_with_sources(scripts, libraries))
    script = request.get("script") or {}
    meta = scripts.script(script.get("id"))
    version = script.get("version") or meta["version"]
    pinned, libraries = store.pin(meta["id"], version)
    rule.update(name=f"{meta['name']} v{version}", script=pinned, libraries=libraries)
    store.verify(rule)
    return rule, engine_rule_of(rule, scripts)


# ── check ──


def _check_load(request: dict, scripts: ScriptStore) -> tuple[dict, str, str]:
    """
    The load a check asks of the runner, what it checks (a script, a library
    through an indicator), and the source TradingView's rules are read on.
    """
    store = RuleStore(scripts)
    if request.get("source") is not None:
        source = request["source"]
        facts = pine_facts(source)
        name = request.get("name") or "Draft"
        own = {"id": DRAFT_ID, "name": facts.library_name, "version": 0, "source": source, "sha256": _sha256(source)}
    else:
        script = request.get("script") or {}
        meta = scripts.script(script.get("id"))
        version = script.get("version") or meta["version"]
        source = scripts.source(meta["id"], version)
        facts = pine_facts(source)
        name = meta["name"]
        own = {"id": meta["id"], "name": facts.library_name, "version": version, "source": source, "sha256": _sha256(source)}
    libraries = [dict(pin) for pin in _libraries_with_sources(scripts, store.libraries_of(facts, name))]
    if facts.kind == LIBRARY:
        # A library runs through an indicator that imports it, its own dependencies beside it.
        checker = LIBRARY_CHECK.format(name=facts.library_name)
        return {"script": {"source": checker, "sha256": _sha256(checker)},
                "libraries": [*libraries, own]}, "library", source
    return {"script": {"source": source, "sha256": _sha256(source)}, "libraries": libraries}, facts.kind, source


async def checker_answers(source: str, libraries: list[dict], node: str, probe: bool = False) -> tuple[list | None, dict | None]:
    """
    What the checker finds in a source: TradingView's rules (v6) and WATA's tickers, and its rule check (with the
    run test's probe, asked); None for each where the checker is not built, or for the rules of a source without
    its version.
    """
    checker = Checker(node, path=CHECKER_PATH)
    try:
        rules = await checker.validate(source)
        return rules, await checker.rule_check(source, libraries, probe=probe)
    except (CheckerMissing, RunnerFailed, RunnerDown):
        return None, None
    finally:
        await checker.stop()


def made_up_series(ask: dict) -> list:
    """
    A check reads no data: another series asked is made up, in bars of the length asked, over the made-up
    minutes. A ticker WATA has not is refused, as a run would refuse it.
    """
    ticker = parse_ticker(str(ask.get("ticker") or ""))
    if ticker is None:
        raise ValueError(f"No such asset in WATA: {ask.get('ticker')!r}")
    seconds = timeframe_seconds(str(ask.get("timeframe") or "")) or 60
    span = CHECK_BARS[-1][6]
    bars = [
        [index * seconds * 1000, 100.0 + index % 5, 101.0 + index % 5, 99.0 + index % 5, 100.0 + (index * 2) % 5, 1,
         (index + 1) * seconds * 1000]
        for index in range(math.ceil(span / (seconds * 1000)))
    ]
    start, end = ask.get("from"), ask.get("to")
    return [bar for bar in bars if (start is None or bar[0] >= start) and (end is None or bar[0] <= end)]


async def check(request: dict, scripts: ScriptStore, node: str, memory_mb: int, timeout: int) -> dict:
    found = find_node(node)
    if found is None or not os.path.isfile(RUNNER_PATH):
        raise StudyRefused("The Pine runner is not there: Node 24 and runner.mjs (npm run build:runner)")
    load, kind, checked_source = _check_load(request, scripts)
    rules, static = await checker_answers(checked_source, load["libraries"] if kind != LIBRARY else [], node)

    async def no_series(ask: dict) -> list:
        return made_up_series(ask)

    runner = NodeRunner(runner_command(found, RUNNER_PATH, memory_mb), no_series, timeout_seconds=timeout)
    try:
        await runner.start()
        answer = await runner.request({
            "op": "load", "rule": "check0000000", **load, "inputs": {}, "ticker": "CAPITAL_US100", "timeframe": "1",
            "bars": CHECK_BARS, "detail": True,
        })
    except RunnerFailed as e:
        return {"ok": False, "error": e.error, "tradingview_rules": rules, "rule_check": static}
    finally:
        await runner.stop()
    alerts: dict[str, int] = {}
    for alert in answer.get("alerts") or []:
        title = alert.get("title") or alert.get("id") or alert.get("type")
        alerts[title] = alerts.get(title, 0) + 1
    plots = (answer.get("series") or {}).get("plots") or {}
    return {
        "ok": True, "kind": kind, "title": answer.get("title"),
        "inputs": [
            {key: entry.get(key) for key in ("id", "title", "type", "defval", "options", "minval", "maxval", "step", "tooltip")
             if entry.get(key) is not None}
            for entry in answer.get("inputs") or []
        ],
        "plots": sorted(plots), "alerts_on_made_up_bars": alerts,
        "warnings": answer.get("warnings") or [], "runtime_warnings": answer.get("runtimeWarnings") or [],
        # TradingView's rules (v6 only) and WATA's tickers: None where the checker is not built here.
        "tradingview_rules": rules,
        # What keeps it from being a signal rule as written: the source's part of the rule check (``rule``).
        "rule_check": static,
        "milliseconds": answer.get("milliseconds"),
        "note": "Made-up bars: what the alerts do on real ones is pine_run's",
    }


# ── run ──


def _days(request: dict) -> tuple[date, date]:
    try:
        first = date.fromisoformat(request.get("from"))
        last = date.fromisoformat(request.get("to") or request.get("from"))
    except (TypeError, ValueError):
        raise StudyRefused('The days are "from" and "to", YYYY-MM-DD') from None
    if last < first or (last - first).days >= MAX_STUDY_DAYS:
        raise StudyRefused(f"A study covers 1 to {MAX_STUDY_DAYS} days")
    return first, last


def _summary(values: list) -> dict:
    numbers = [value for value in values if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)]
    given = [value for value in values if value is not None]
    return {
        "bars": len(values), "na_share": round(1 - len(given) / len(values), 4) if values else None,
        "min": min(numbers) if numbers else None, "max": max(numbers) if numbers else None,
        "mean": sum(numbers) / len(numbers) if numbers else None,
        "first": next((value for value in values if value is not None), None),
        "last": next((value for value in reversed(values) if value is not None), None),
    }


def _sampled(times: list[int], values: list, points: int) -> list:
    if not times:
        return []
    step = max(1, math.ceil(len(times) / points))
    picked = list(range(0, len(times), step))
    if picked[-1] != len(times) - 1:
        picked.append(len(times) - 1)
    return [[iso_seconds(times[index] / 1000), values[index]] for index in picked]


def _bar_seconds(bars: dict) -> int | None:
    return timeframe_seconds(bars["timeframe"]) if bars["type"] == "time" else None


async def run(request: dict, scripts: ScriptStore, config, prices_root: str, node: str, memory_mb: int, timeout: int,
              files_root: str | None = None) -> dict:
    rule, engine_rule = study_rule(request, scripts)
    first, last = _days(request)
    warmup = request.get("warmup_days", 1)
    if isinstance(warmup, bool) or not isinstance(warmup, int) or not 0 <= warmup <= MAX_WARMUP_DAYS:
        raise StudyRefused(f"warmup_days is 0 to {MAX_WARMUP_DAYS}")
    points = request.get("points", DEFAULT_POINTS)
    if isinstance(points, bool) or not isinstance(points, int) or not 2 <= points <= MAX_POINTS:
        raise StudyRefused(f"points is 2 to {MAX_POINTS}")
    clock = TradingClock(request.get("timezone") or "Europe/Paris")
    begin, end = clock.day_bounds(first)[0], clock.day_bounds(last)[1]
    seconds = _bar_seconds(rule["bars"])
    if seconds and (end - begin + warmup * DAY) // seconds > MAX_RUN_BARS:
        raise StudyRefused(f"More than {MAX_RUN_BARS} bars: fewer days, fewer days of warm-up, or longer bars")
    rows, pg = await asset_rows_for(config, prices_root, rule["asset"]["provider"], files_root)
    session = ReplaySession(rule, engine_rule, rows, node=node, history_days=warmup, memory_mb=memory_mb,
                            timeout_seconds=timeout)
    try:
        await session.start()
        window = await session.window(begin, end, detail=True)
    finally:
        await session.stop()
        if pg is not None:
            await pg.close()
    series = window.series or {"times": [], "plots": {}}
    times = series["times"]
    inside = [index for index, moment in enumerate(times) if moment >= begin * 1000]
    studied_times = [times[index] for index in inside]
    wanted = request.get("plots") or []
    plots = {}
    for title, values in series["plots"].items():
        if wanted and title not in wanted:
            continue
        if len(plots) >= MAX_PLOTS:
            break
        studied = [values[index] for index in inside]
        plots[title] = {**_summary(studied), "points": _sampled(studied_times, studied, points)}
    alerts = [
        {"bar_time": iso_seconds(replayed.bar[0] / 1000), "closed_at": iso_seconds(replayed.bar[6] / 1000),
         "type": replayed.alert["type"], "title": replayed.alert.get("title"), "message": replayed.alert.get("message")}
        for replayed in window.alerts
    ]
    by_title: dict[str, int] = {}
    for alert in alerts:
        by_title[alert["title"] or alert["type"]] = by_title.get(alert["title"] or alert["type"], 0) + 1
    # Every one counted by its title; listed, only those asked for, and as many as asked.
    titles = request.get("alert_titles") or []
    listed = [alert for alert in alerts if not titles or alert["title"] in titles]
    most = request.get("max_alerts", MAX_ALERTS)
    most = MAX_ALERTS if isinstance(most, bool) or not isinstance(most, int) else max(0, min(most, MAX_ALERTS))
    return {
        "ok": True, "rule": {"name": rule["name"], "asset": f"{rule['asset']['provider']}/{rule['asset']['key']}",
                             "side": rule["side"], "bars": request.get("bars", "1")},
        "from": first.isoformat(), "to": last.isoformat(), "bars": len(studied_times),
        "warmup_bars": len(times) - len(studied_times), "milliseconds": window.milliseconds,
        "plots": plots, "alerts_by_title": by_title, "alerts": listed[:most], "alerts_listed_of": len(listed),
        "alerts_total": len(alerts), "warnings": window.warnings, "runtime_warnings": window.runtime_warnings,
    }


# ── signals ──


async def signals(request: dict, scripts: ScriptStore, config, prices_root: str, node: str, memory_mb: int, timeout: int,
                  cache: str | None, files_root: str | None = None) -> dict:
    rule, engine_rule = study_rule(request, scripts)
    first, last = _days(request)
    _, _, history_days, restart = ta_settings(config)
    # A rule's own history, when it has one (a shadow rule measured before it is saved).
    own = checked_history_days(request.get("history_days"))
    history_days = history_days if own is None else own
    restart = request.get("restart_time") or restart
    clock = TradingClock(request.get("timezone") or "Europe/Paris")
    period = (clock.day_bounds(first)[0], clock.day_bounds(last)[1])
    rows, pg = await asset_rows_for(config, prices_root, rule["asset"]["provider"], files_root)
    session = ReplaySession(rule, engine_rule, rows, node=node, history_days=history_days,
                            restart_minute=restart_minute_of(restart), memory_mb=memory_mb, timeout_seconds=timeout,
                            cache=cache)
    days, found, warnings = [], [], []
    try:
        await session.start()
        for begin, end in engine_windows(period[0], period[1], session.restart_minute):
            window = await session.window(begin, end, period=period)
            warnings += [warning for warning in window.warnings if warning not in warnings]
            raised = [replayed for replayed in window.alerts if replayed.candidate and replayed.candidate.action]
            days.append({"from": iso_seconds(begin), "to": iso_seconds(end), "bars": window.bars,
                         "alerts": len(window.alerts), "signals": len(raised),
                         # The runner's time for the whole day: about what each of its bars costs the engine at its end.
                         "milliseconds": window.milliseconds})
            for replayed in raised:
                found.append({
                    "bar_time": iso_seconds(replayed.bar[0] / 1000), "closed_at": iso_seconds(replayed.bar[6] / 1000),
                    "action": replayed.candidate.action, "confidence": replayed.candidate.confidence,
                    "title": replayed.alert.get("title"),
                })
    finally:
        await session.stop()
        if pg is not None:
            await pg.close()
    by_action: dict[str, int] = {}
    for signal in found:
        by_action[signal["action"]] = by_action.get(signal["action"], 0) + 1
    return {
        "ok": True, "rule": {"name": rule["name"], "asset": f"{rule['asset']['provider']}/{rule['asset']['key']}",
                             "indice": rule["indice"], "alerts": {entry["title"]: entry["action"] for entry in rule["alerts"]}},
        "engine": {"history_days": history_days, "restart_time": restart},
        "from": first.isoformat(), "to": last.isoformat(), "days": days, "signals_by_action": by_action,
        "signals": found[:MAX_SIGNALS], "signals_total": len(found), "warnings": warnings,
        "windows_run": session.run_windows, "windows_read_back": session.cached_windows,
    }


# ── rule: the run test of a rule check ──

RULE_DAYS = 2
# How far back the engine's days are looked for, when none are asked.
RULE_LOOK_BACK_DAYS = 12
# What an engine day must hold to be tested: about eight hours of ten-second quotes.
RULE_LEAST_ROWS = 3000
# The bars a run is ended at: a script whose run continues cannot repaint but for its series, a few say it.
CHECKPOINTS_WHOLE = 40
CHECKPOINTS_CONTINUED = 4
# Kept of the study's time limit for what follows the runs.
RULE_MARGIN_SECONDS = 10
DEFAULT_RULE_ASSET = "capital/US100"
DEFAULT_RULE_BARS = "1"
# What is said at most: the first differences, the warnings.
RULE_DIFFERENCES = 10
RULE_WARNINGS = 10


def _rule_of_request(request: dict, scripts: ScriptStore) -> tuple[dict, EngineRule, dict]:
    """
    The rule a run test runs, and what it was run with: a rule as it runs (``rule``, its own pins and settings),
    a saved version with today's libraries, or a source not saved yet. Every alert counts, read as a message.
    """
    if request.get("rule"):
        stored = RuleStore(scripts).rule(request["rule"])
        rule = {**stored, "id": REPLAY_RULE_ID, "alerts": [], "alert_calls": "message"}
        engine_rule = engine_rule_of(rule, scripts)
        settings = {"rule": stored["id"], "asset": f"{stored['asset']['provider']}/{stored['asset']['key']}",
                    "side": stored["side"], "bars": stored["bars"], "inputs": {entry["id"]: entry["value"] for entry in stored["inputs"]},
                    "history_days": stored.get("history_days")}
        return rule, engine_rule, settings
    asked = {**request, "asset": request.get("asset") or DEFAULT_RULE_ASSET, "bars": request.get("bars") or DEFAULT_RULE_BARS,
             "alerts": {}, "alert_calls": "message"}
    rule, engine_rule = study_rule(asked, scripts)
    settings = {"rule": None, "asset": asked["asset"], "side": rule["side"], "bars": rule["bars"],
                "inputs": request.get("inputs") or {}, "history_days": checked_history_days(request.get("history_days"))}
    return rule, engine_rule, settings


def _avoided(begin: int, end: int, avoid: list) -> bool:
    days = {datetime.fromtimestamp(moment, timezone.utc).date() for moment in (begin, end - 1)}
    for entry in avoid or []:
        try:
            first = date.fromisoformat(entry[0])
            last = date.fromisoformat(entry[1]) if entry[1] else date.max
        except (TypeError, ValueError, IndexError):
            continue
        if any(first <= day <= last for day in days):
            return True
    return False


async def _rule_days(request: dict, rule: dict, rows, restart_minute: int) -> list[tuple[int, int, tuple[int, int]]]:
    """
    The engine's days a test runs, each with the period its alerts count in: those asked, or the newest that
    ended on a weekday's session (Tuesday to Friday), outside the days to avoid (the research lock box), with
    the asset's quotes for most of the day.
    """
    if request.get("from"):
        first, last = _days(request)
        clock = TradingClock(request.get("timezone") or "Europe/Paris")
        period = (clock.day_bounds(first)[0], clock.day_bounds(last)[1])
        return [(begin, end, period) for begin, end in engine_windows(period[0], period[1], restart_minute)]
    now = int(time.time())
    windows = [window for window in engine_windows(now - RULE_LOOK_BACK_DAYS * DAY, now, restart_minute) if window[1] <= now]
    chosen = []
    wanted = request.get("days") or RULE_DAYS
    for begin, end in reversed(windows):
        if datetime.fromtimestamp(end, timezone.utc).weekday() not in (1, 2, 3, 4) or _avoided(begin, end, request.get("avoid")):
            continue
        found, _ = await rows.rows(rule["asset"]["provider"], rule["asset"]["key"], begin, end)
        if len(found) >= RULE_LEAST_ROWS:
            chosen.append((begin, end, (begin, end)))
        if len(chosen) >= wanted:
            break
    return list(reversed(chosen))


def _pairs(window) -> list[tuple[list, dict]]:
    """A window's alerts as (bar, alert), each with the confidence its rule would read in it."""
    return [(replayed.bar, {**replayed.alert, "confidence": replayed.candidate.confidence if replayed.candidate else None})
            for replayed in window.alerts]


def _values_at(series: dict | None, bar_time: int) -> dict:
    """Each plot's value at a bar (by its open time), from a run in detail."""
    if not series:
        return {}
    try:
        at = series["times"].index(bar_time)
    except ValueError:
        return {}
    return {title: values[at] for title, values in series["plots"].items()}


async def rule(request: dict, scripts: ScriptStore, config, prices_root: str, node: str, memory_mb: int, timeout: int,
               files_root: str | None = None) -> dict:
    started = time.monotonic()
    deadline = started + max(15, timeout - RULE_MARGIN_SECONDS)
    rule_, engine_rule, settings = _rule_of_request(request, scripts)
    libraries = [dict(pin) for pin in engine_rule.libraries]
    rules, static = await checker_answers(engine_rule.source, libraries, node, probe=True)
    if static is None:
        raise StudyRefused("The checker is not there: Node 24 and check.mjs (npm run build:checker)")
    probe = static.pop("probe", None)
    static = {**static, "tradingview_rules": rules or []}
    check_ = static["check"]
    blocking = {finding["id"] for finding in check_.get("findings") or [] if finding["level"] == "blocks"}
    if blocking & {"version", "library", "library-missing"}:
        return {"ok": True, "format": checks.RUN_FORMAT, "static": static, "tested": None, **checks.combined(static, None)}

    _, _, engine_history, restart = ta_settings(config)
    history_days = settings["history_days"] if settings["history_days"] is not None else engine_history
    settings["history_days"] = history_days
    restart_minute = restart_minute_of(request.get("restart_time") or restart)
    rows, pg = await asset_rows_for(config, prices_root, rule_["asset"]["provider"], files_root)
    session = ReplaySession(rule_, engine_rule, rows, node=node, history_days=history_days, restart_minute=restart_minute,
                            messages=True, memory_mb=memory_mb, timeout_seconds=timeout)
    whole = check_.get("wholeBecause") is not None
    # A script with no alert of its own is judged on the events it draws: what it would raise once they are added.
    drawn = not check_.get("alerts") and not check_.get("alertCalls")
    candidate_titles = {candidate["title"] for candidate in check_.get("candidates") or []}
    tested = {
        "format": checks.RUN_FORMAT, "settings": settings, "days": [], "alerts": [], "same_bar": [], "candidates": [],
        "checkpoints": {"planned": 0, "run": 0, "skipped": 0, "stopped": False, "with_alerts": 0, "probed": 0, "at_the_bar": [],
                        "earlier": [], "plots_differ": [], "live_titles": []},
        "history": None, "na_plots": [], "runtime_warnings": [], "failure": None,
    }
    days = []
    try:
        await session.start()
        windows = await _rule_days(request, rule_, rows, restart_minute)
        per_day = max(2, (CHECKPOINTS_WHOLE if whole else CHECKPOINTS_CONTINUED) // max(1, len(windows)))
        # Where live may alert: only a script run whole at each new bar can alert otherwise there.
        live = await _live_bars(session, rule_, engine_rule, probe, windows, drawn, candidate_titles) if probe and whole else {}
        tested["checkpoints"]["probed"] = sum(len(bars) for bars in live.values())
        marked: dict[str, int] = {}
        na: dict[str, bool] = {}
        tested_ends: list[int] = []
        differ: set[str] = set()
        for begin, end, period in windows:
            window = await session.window(begin, end, period=period, detail=True)
            pairs = _pairs(window)
            bars = [bar for bar in window.strategy_bars if period[0] * 1000 <= bar[0] < period[1] * 1000 and begin * 1000 < bar[6] <= end * 1000]
            if not bars:
                # An engine day the days asked only touch, with no bar of theirs.
                continue
            tested_ends.append(end)
            days.append({"bars": bars, "alerts": pairs})
            tested["days"].append({"from": iso_seconds(begin), "to": iso_seconds(end), "bars": len(bars), "alerts": len(pairs),
                                   "milliseconds": window.milliseconds})
            series = window.series or {"times": [], "plots": {}}
            # The day's own bars: those of its history are the day before's, counted with it.
            own = {bar[0] for bar in bars}
            inside = [index for index, moment in enumerate(series["times"]) if moment in own]
            for title, values in series["plots"].items():
                studied = [values[index] for index in inside]
                na[title] = na.get(title, True) and all(value is None for value in studied)
                if title in candidate_titles:
                    marked[title] = marked.get(title, 0) + checks.marked(studied)
            for warning in window.runtime_warnings:
                message = warning.get("message")
                if message and message not in tested["runtime_warnings"] and len(tested["runtime_warnings"]) < RULE_WARNINGS:
                    tested["runtime_warnings"].append({"message": message})
            plan = checks.plan_checkpoints(bars, {bar[0] for bar, _ in pairs}, per_day, live=live.get(end, set()))
            tested["checkpoints"]["planned"] += len(plan)
            for index in plan:
                if time.monotonic() > deadline:
                    tested["checkpoints"]["stopped"] = True
                    break
                bar = bars[index]
                prefix = await session.window(begin, bar[6] // 1000, period=period, detail=drawn)
                # A range bar cut by a pause is made only when the next quote comes: a run ending at its close lacks it.
                if not any(built[0] == bar[0] for built in prefix.strategy_bars):
                    tested["checkpoints"]["skipped"] += 1
                    continue
                tested["checkpoints"]["run"] += 1
                ended = _pairs(prefix)
                compared = checks.compare_at({"alerts": pairs}, {"alerts": ended}, bar[6])
                alerted = any(at[6] == bar[6] for at, _ in pairs) or any(at[6] == bar[6] for at, _ in ended)
                live_titles = tested["checkpoints"]["live_titles"]
                live_titles += sorted({entry[2] for entry in compared["live_only"] if entry[2]} - set(live_titles))
                said = [checks.said_difference(entry, "live") for entry in compared["live_only"]] + [
                    checks.said_difference(entry, "backtest") for entry in compared["backtest_only"]]
                if drawn:
                    # The events it draws, as the alerts it would have once they are added.
                    mine, theirs = _values_at(prefix.series, bar[0]), _values_at(window.series, bar[0])
                    for title in sorted(candidate_titles & set(mine) & set(theirs)):
                        drawn_live, drawn_backtest = checks.marked([mine[title]]), checks.marked([theirs[title]])
                        alerted = alerted or bool(drawn_live or drawn_backtest)
                        if drawn_live != drawn_backtest:
                            said.append(f"the event “{title}” at {checks.moment_text(bar[0])} drawn only "
                                        f"{'live' if drawn_live else 'in the backtest'}")
                    differ.update(title for title in set(mine) & set(theirs) - candidate_titles if mine[title] != theirs[title])
                tested["checkpoints"]["with_alerts"] += 1 if alerted else 0
                if said and len(tested["checkpoints"]["at_the_bar"]) < RULE_DIFFERENCES:
                    tested["checkpoints"]["at_the_bar"].append({"bar_time": iso_seconds(bar[0] / 1000), "said": "; ".join(said)})
                if compared["earlier"] and len(tested["checkpoints"]["earlier"]) < RULE_DIFFERENCES:
                    first = compared["earlier"][0]
                    tested["checkpoints"]["earlier"].append({
                        "bar_time": iso_seconds(bar[0] / 1000), "count": len(compared["earlier"]),
                        "said": f"Ended at {checks.moment_text(bar[0])}, a run gave {_plural_alerts(len(compared['earlier']))} "
                                f"otherwise at the bars before, the first “{first[2] or first[1]}” at {checks.moment_text(first[0])}",
                    })
        tested["checkpoints"]["plots_differ"] = sorted(differ)[:RULE_DIFFERENCES]
        tested["alerts"] = checks.alert_stats(days)
        tested["same_bar"] = checks.same_bar(days)[:5]
        tested["candidates"] = [{"title": title, "marked": count} for title, count in marked.items()]
        tested["na_plots"] = sorted(title for title, empty in na.items() if empty)[:RULE_DIFFERENCES]
        tested["spoken"] = checks.spoken_days(tested_ends)
        # One more day of history: the same days' alerts, from an earlier start.
        if windows and time.monotonic() < deadline:
            session.history_days = history_days + 1
            ours = {checks.alert_key(alert, bar[0]) for day in days for bar, alert in day["alerts"]}
            theirs: set = set()
            for begin, end, period in windows:
                if end in tested_ends:
                    theirs |= {checks.alert_key(alert, bar[0]) for bar, alert in _pairs(await session.window(begin, end, period=period))}
            tested["history"] = {"history_days": history_days + 1, "compared": len(ours), "differing": len(ours ^ theirs)}
    except RunnerFailed as e:
        tested["failure"] = {"message": e.error.get("message"), "file": e.error.get("file"), "line": e.error.get("line")}
    finally:
        await session.stop()
        if pg is not None:
            await pg.close()
    tested["seconds"] = round(time.monotonic() - started, 1)
    return {"ok": True, "format": checks.RUN_FORMAT, "static": static, "tested": tested, **checks.combined(static, tested)}


async def _live_bars(session: ReplaySession, rule_: dict, engine_rule: EngineRule, probe: str, windows: list,
                     drawn: bool, titles: set[str]) -> dict[int, set[int]]:
    """
    Where live may alert, by engine day (its end): the bars a run of the probe (the script with every bar read as
    the last of its run, liveProbe of ruleCheck.ts) alerted at, or drew one of its events at. Only where runs are
    ended first: what a run of the script ending there raises decides. A probe the runner refuses gives nothing.
    """
    found: dict[int, set[int]] = {}
    session.engine_rule = dataclasses.replace(engine_rule, source=probe)
    # The runner runs only the source its pin names.
    session.rule = {**rule_, "script": {**rule_["script"], "sha256": hashlib.sha256(probe.encode()).hexdigest()}}
    try:
        for begin, end, period in windows:
            window = await session.window(begin, end, period=period, detail=drawn)
            bars = {bar[0] for bar, _ in _pairs(window)}
            if drawn and window.series:
                for title, values in window.series["plots"].items():
                    if title in titles:
                        bars |= {moment for moment, value in zip(window.series["times"], values) if checks.marked([value])}
            found[end] = bars
    except RunnerFailed:
        return {}
    finally:
        session.engine_rule, session.rule = engine_rule, rule_
    return found


# ── view: a day as chart_view shows it ──

# The rows a view gathers its hours into, by default and at most; the plots it tabulates and draws.
VIEW_ROWS, MAX_VIEW_ROWS = 180, 400
VIEW_PLOTS, MAX_VIEW_PLOTS = 4, 8
# The plots it lists with their figures, whatever it tabulates.
LISTED_PLOTS = 30
# What a view says at most, its picture aside: an answer's 32,000 characters, less what the tool adds to it.
VIEW_CHARS = 31_000


class _KeptRows:
    """An asset's rows, read once for a view: the replay and the view's own figures ask for the same span."""

    def __init__(self, rows):
        self._rows = rows
        self._kept: dict = {}

    async def rows(self, provider: str, key: str, start: int, end: int):
        asked = (provider, key, start, end)
        if asked not in self._kept:
            self._kept[asked] = await self._rows.rows(provider, key, start, end)
        return self._kept[asked]


def _parts_rule(request: dict) -> tuple[dict, EngineRule]:
    """A run's own script, libraries, inputs and bars as it kept them (``parts``), run as the run ran them."""
    parts = request["parts"]
    script, chart = parts["script"], parts["chart"]
    libraries = tuple({**{key: entry[key] for key in ("id", "name", "version", "source")}, "sha256": _sha256(entry["source"])}
                      for entry in parts.get("libraries") or [])
    rule = {
        "id": REPLAY_RULE_ID, "name": f"{script.get('name') or 'Its script'} v{script.get('version')}",
        "asset": chart["asset"], "side": chart["side"], "bars": checked_bars(chart["bars"]),
        "script": {"id": script["id"], "version": script["version"], "sha256": _sha256(script["source"])},
        "libraries": [{key: entry[key] for key in ("id", "name", "version", "sha256")} for entry in libraries],
        "inputs": checked_inputs(list(parts.get("inputs") or [])),
        "alerts": checked_alerts([{"title": title, "action": action} for title, action in (request.get("alerts") or {}).items()]),
        "alert_calls": request.get("alert_calls", IGNORE), "confidence_plot": None,
        "indice": request.get("indice") or "us100", "mode": "shadow",
    }
    return rule, EngineRule(rule=rule, source=script["source"], libraries=libraries)


def _span(value, what: str) -> tuple[int, int]:
    if not isinstance(value, list) or len(value) != 2 or not all(isinstance(part, int) and not isinstance(part, bool)
                                                                  for part in value) or value[0] >= value[1]:
        raise StudyRefused(f"The {what} is [begin, end], epoch seconds")
    return value[0], value[1]


def _whole_between(value, low: int, high: int, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise StudyRefused(f"{what} is {low} to {high}")
    return value


async def _previous_close(rows: _KeptRows, asset: dict, side: str, day: date, clock: TradingClock, hours: tuple[int, int],
                          series) -> float | None:
    """The last price of the trading hours of the weekday before ``day`` (its gap's), read further back when needed."""
    for back in range(1, 6):
        before = day - timedelta(days=back)
        if before.weekday() >= 5:
            continue
        bounds = regimes.session(before, clock, *hours)
        found = regimes.session_close(series, bounds)
        if found is None:
            earlier, _ = await rows.rows(asset["provider"], asset["key"], int(bounds[0]), int(bounds[1]))
            found = regimes.session_close(day_view.asset_series(earlier, side), bounds)
        if found is not None:
            return found
    return None


async def view(request: dict, scripts: ScriptStore, config, prices_root: str, node: str, memory_mb: int, timeout: int,
               files_root: str | None = None) -> dict:
    wanted_rows = _whole_between(request.get("rows", VIEW_ROWS), 20, MAX_VIEW_ROWS, "rows")
    try:
        day = date.fromisoformat(request.get("day"))
    except (TypeError, ValueError):
        raise StudyRefused("The day is YYYY-MM-DD") from None
    zone = ZoneInfo(request.get("timezone") or "Europe/Paris")
    hours = request.get("hours") or {}
    clock = TradingClock(hours.get("zone") or request.get("timezone") or "Europe/Paris")
    trading = (int(hours.get("start_hour", 8)), int(hours.get("end_hour", 22)))
    _, _, engine_history, restart = ta_settings(config)
    own = checked_history_days(request.get("history_days"))
    history_days = engine_history if own is None else own
    if request.get("engine") is not None:
        engine_begin, engine_end = _span(request["engine"], "engine day")
    else:
        # The engine day a backtest trades the day in: the one holding its noon (src/admin/backtest_indicator.py).
        noon = int(datetime(day.year, day.month, day.day, 12, tzinfo=zone).timestamp())
        engine_begin, engine_end = engine_windows(noon, noon + 1, restart_minute_of(restart))[0]
    asked_begin, asked_end = _span(request.get("view"), "window shown")
    # The hours shown, within the engine day: what the script ran on.
    view_begin, view_end = max(asked_begin, engine_begin), min(asked_end, engine_end)
    if view_begin >= view_end:
        raise StudyRefused(f"The hours asked are outside the engine day ({day_view.clock_text(engine_begin * 1000, zone)} "
                           f"the day before to {day_view.clock_text(engine_end * 1000, zone)}): the script ran on nothing then")

    if request.get("parts"):
        rule, engine_rule = _parts_rule(request)
    elif request.get("source") is not None or request.get("script"):
        rule, engine_rule = study_rule(request, scripts)
    else:
        side = request.get("side", "bid")
        if side not in ("bid", "ask"):
            raise StudyRefused("The side is bid or ask")
        rule, engine_rule = {"name": "Prices alone", "asset": _asset(request.get("asset")), "side": side,
                             "bars": bars_of(request.get("bars", "1")), "alerts": []}, None
    asset, side = rule["asset"], rule["side"]
    anchor = engine_begin - history_days * DAY
    found_rows, pg = await asset_rows_for(config, prices_root, asset["provider"], files_root)
    rows = _KeptRows(found_rows)
    window = None
    try:
        if engine_rule is not None:
            session = ReplaySession(rule, engine_rule, rows, node=node, history_days=history_days, memory_mb=memory_mb,
                                    timeout_seconds=timeout)
            try:
                await session.start()
                window = await session.window(engine_begin, engine_end, detail=True)
            finally:
                await session.stop()
        ten_seconds, decimals = await rows.rows(asset["provider"], asset["key"], anchor, engine_end)
        decimals = _decimals(ten_seconds, decimals) if ten_seconds else decimals
        if window is None:
            refusal = too_large(f"{asset['provider']}/{asset['key']}", rule["bars"], decimals)
            bars = built_bars(_builder(rule["bars"], side, ten_seconds, decimals), ten_seconds, engine_end, refusal)
        else:
            bars = window.strategy_bars
        series = day_view.asset_series(ten_seconds, side)
        previous = await _previous_close(rows, asset, side, day, clock, trading, series)
    finally:
        if pg is not None:
            await pg.close()

    inside = [bar for bar in bars if view_begin * 1000 <= bar[0] < view_end * 1000]
    if not inside:
        raise StudyRefused(f"No bar of {asset['provider']}/{asset['key']} in the hours asked: its feed recorded nothing then")
    detail = (window.series if window is not None else None) or {"times": [], "plots": {}}
    place = {moment: index for index, moment in enumerate(detail.get("times") or [])}
    all_plots = {name: [values[place[bar[0]]] if bar[0] in place and place[bar[0]] < len(values) else None for bar in inside]
                 for name, values in (detail.get("plots") or {}).items()}
    low, high = min(bar[3] for bar in inside), max(bar[2] for bar in inside)
    over, _ = day_view.panes(all_plots, low, high)
    listed_plots = []
    for name, values in list(all_plots.items())[:LISTED_PLOTS]:
        summary = _summary(values)
        listed_plots.append({"name": name, "pane": "price" if name in over else "own", "na_share": summary["na_share"],
                             "min": summary["min"], "max": summary["max"], "last": summary["last"]})
    asked = [name for name in request.get("plots") or [] if name in all_plots][:MAX_VIEW_PLOTS]
    chosen = asked or [name for name, values in all_plots.items()
                       if any(day_view.finite(value) is not None for value in values)][:VIEW_PLOTS]
    table_plots = {name: all_plots[name] for name in chosen}

    # The alerts of the hours shown, with what price did after each.
    alerts = []
    for replayed in (window.alerts if window is not None else []):
        bar = replayed.bar
        if not view_begin * 1000 <= bar[0] < view_end * 1000:
            continue
        action = replayed.candidate.action if replayed.candidate is not None else None
        alerts.append({"title": replayed.alert.get("title") or replayed.alert.get("type"), "action": action,
                       "at": bar[6], "price": round(bar[4], decimals) if decimals is not None else bar[4],
                       "moves": day_view.moved(series, bar[6] / 1000, action)})
    by_title = day_view.alert_moves(series, [{**alert, "at": alert["at"] / 1000} for alert in alerts])
    # A run's signals of the day that became no trade: why each was refused, failed or found no turbo.
    refused = [{"at": day_view.clock_text((day_view.epoch(signal.get("at")) or 0) * 1000, zone, seconds=True),
                **{key: signal.get(key) for key in ("action", "fate", "rule", "reason") if signal.get(key) is not None}}
               for signal in request.get("signals") or []]
    listed_alerts = [{"at": day_view.clock_text(alert["at"], zone, seconds=True), "title": alert["title"],
                      "action": alert["action"], "price": alert["price"],
                      **({"after_bp": alert["moves"]["after"], "best": alert["moves"]["best"], "worst": alert["moves"]["worst"]}
                         if alert["moves"] else {})}
                     for alert in alerts]

    session_bounds = regimes.session(day, clock, *trading)
    regime = request.get("regime") or regimes.day_features(series, previous, day, session_bounds,
                                                           regimes.read_calendar().on(day))
    figures = {
        **(day_view.day_prices(series, view_begin, view_end, zone, decimals) or {}),
        "previous_close": round(previous, decimals) if previous is not None and decimals is not None else previous,
        "levels": day_view.levels(series, engine_begin, view_begin, regimes.us_open(day), zone, decimals),
        "regime": {key: value for key, value in regime.items() if key != "labels" and value not in (None, [])},
    }
    all_swings = day_view.swings(series, view_begin, view_end, zone, decimals)
    trades = request.get("trades") or []
    bar_seconds = _bar_seconds(rule["bars"])
    step = day_view.step_for([bar[0] for bar in inside], wanted_rows, bar_seconds)
    coarser_steps = [candidate for candidate in day_view.STEPS_SECONDS
                     if candidate > (step or bar_seconds or 0) and (not bar_seconds or candidate > bar_seconds)]
    name = rule.get("name") or "Its script"
    # As the pages say them: a minute's bars are "1 min", others the engine's word (45S, R60, 60).
    label = bars_label(rule["bars"])
    bars_said = f"{label} min" if label.isdigit() else label

    def build(listed: int | None, plots_kept: int | None, coarser: int, swings_kept: int | None) -> dict:
        used_step = step if coarser == 0 else coarser_steps[min(coarser, len(coarser_steps)) - 1] if coarser_steps else step
        kept_plots = dict(list(table_plots.items())[:plots_kept]) if plots_kept is not None else table_plots
        gathered = day_view.gathered(inside, kept_plots, used_step)
        row_of_bar = {}
        for number, row in enumerate(gathered):
            for index in range(row["first"], row["last"] + 1):
                row_of_bar[index] = number
        alerts_by_row: dict[int, list[str]] = {}
        by_close = {bar[6]: index for index, bar in enumerate(inside)}
        for alert in alerts:
            if alert["at"] in by_close:
                alerts_by_row.setdefault(row_of_bar[by_close[alert["at"]]], []).append(alert["title"])
        trades_by_row, trade_lines = day_view.trade_marks(trades, gathered)
        seconds = used_step < 60 if used_step is not None else bar_seconds is None or bar_seconds < 60
        columns, text = day_view.table(gathered, list(kept_plots), alerts_by_row, trades_by_row, zone, decimals, seconds)
        cut = [said for said, done in zip(day_view.FITTING, (listed is not None, plots_kept is not None, coarser > 0,
                                                              swings_kept is not None)) if done]
        return {
            "ok": True,
            "view": {
                "script": name if engine_rule is not None else None, "asset": f"{asset['provider']}/{asset['key']}",
                "side": side, "bars": bars_said, "day": day.isoformat(), "timezone": str(zone),
                "from": day_view.clock_text(view_begin * 1000, zone), "to": day_view.clock_text(view_end * 1000, zone),
                "engine_day": {"from": iso_seconds(engine_begin), "to": iso_seconds(engine_end), "history_days": history_days},
                "bars_shown": len(inside), "a_row": day_view.step_said(used_step),
                "rows": len(gathered), "alerts": {entry["title"]: entry["action"] for entry in rule.get("alerts") or []} or None,
            },
            "day": {**figures, "swings": all_swings if swings_kept is None else all_swings[:swings_kept]},
            "plots": listed_plots, "plots_total": len(all_plots), "tabulated": list(kept_plots),
            "alerts": {"by_title": by_title, "listed": listed_alerts if listed is None else listed_alerts[:listed],
                       "listed_of": len(listed_alerts)},
            "trades": trade_lines,
            **({"signals": {"by_fate": request.get("signals_by_fate"),
                            "not_traded": refused if listed is None else refused[:listed]}} if request.get("signals_by_fate") else {}),
            "table": {"columns": columns, "csv": text},
            "cut": cut or None,
            "warnings": window.warnings if window is not None else [],
            "runtime_warnings": window.runtime_warnings if window is not None else [],
            "milliseconds": window.milliseconds if window is not None else None,
        }

    answer = day_view.fitted(build, VIEW_CHARS, lambda found: len(json.dumps(found, separators=(",", ":"), ensure_ascii=False)))
    if request.get("picture", True):
        from src.research import chart_picture

        drawn = {name: table_plots[name] for name in list(table_plots)[:chart_picture.MOST_PLOTS]}
        title = (f"{name if engine_rule is not None else 'Prices'} · {asset['provider']}/{asset['key']} {side} · {bars_said} · "
                 f"{day:%a %d %b %Y}, {answer['view']['from']}-{answer['view']['to']}")
        marked = [{"id": line["id"], "direction": line["direction"], "result_percent": line["result_percent"],
                   "asset_in": line["asset_in"], "asset_out": line["asset_out"],
                   "in_ms": _moment_ms(line["in"]), "out_ms": _moment_ms(line["out"])} for line in answer["trades"]]
        png = chart_picture.picture(bars=inside, plots=drawn, begin_ms=view_begin * 1000, end_ms=view_end * 1000,
                                    zone=str(zone), title=title, alerts=alerts, trades=marked, decimals=decimals)
        answer["picture"] = {"png": base64.b64encode(png).decode("ascii"), "width": chart_picture.WIDTH,
                             "height": chart_picture.HEIGHT, "plots": list(drawn), "title": title}
    return answer


def _moment_ms(value) -> float | None:
    at = day_view.epoch(value)
    return at * 1000 if at is not None else None


def _plural_alerts(count: int) -> str:
    return f"{count:,} alert{'' if count == 1 else 's'}"


# ── the command ──


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m src.research.study", description=__doc__.split("\n\n")[0])
    parser.add_argument("mode", choices=MODES)
    parser.add_argument("--request", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--memory-mb", type=int, default=DEFAULT_MEMORY_MB)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--node", default="node")
    parser.add_argument("--root", help="the Technical Analysis folder (the configuration's by default)")
    parser.add_argument("--prices", help="the price history folder (the configuration's by default)")
    parser.add_argument("--signal-cache", help="the runner's answers kept, shared with the backtests")
    parser.add_argument("--data", default="database",
                        help="where Capital.com's and Saxo's bars are read: database (the configuration's), or files:<folder>")
    return parser.parse_args(argv)


def _config():
    path = os.getenv("WATA_CONFIG_PATH")
    if not path:
        return None
    from src.configuration import ConfigurationManager

    return ConfigurationManager(path)


async def study(args: argparse.Namespace) -> dict:
    with open(args.request, encoding="utf-8") as request_file:
        request = json.load(request_file)
    config = _config()
    ta_root, prices_root, _, _ = ta_settings(config)
    scripts = ScriptStore(args.root or ta_root)
    prices = args.prices or prices_root
    files_root = args.data.removeprefix("files:") if args.data.startswith("files:") else None
    try:
        if args.data != "database" and files_root is None:
            raise StudyRefused("--data is database or files:<folder>")
        if args.mode == "check":
            return await check(request, scripts, args.node, args.memory_mb, args.timeout)
        if args.mode == "run":
            return await run(request, scripts, config, prices, args.node, args.memory_mb, args.timeout, files_root)
        if args.mode == "rule":
            return await rule(request, scripts, config, prices, args.node, args.memory_mb, args.timeout, files_root)
        if args.mode == "view":
            return await view(request, scripts, config, prices, args.node, args.memory_mb, args.timeout, files_root)
        return await signals(request, scripts, config, prices, args.node, args.memory_mb, args.timeout, args.signal_cache,
                             files_root)
    except RunnerFailed as e:
        return {"ok": False, "error": e.error}
    except RunnerDown as e:
        return {"ok": False, "error": {"message": f"The script did not finish: {e}"}}
    except (StudyRefused, InvalidRule, InvalidScript, ScriptNotFound, ValueError) as e:
        return {"ok": False, "error": {"message": str(e)}}


def _write(path: str, answer: dict) -> None:
    partial = f"{path}.part"
    with open(partial, "w", encoding="utf-8") as out_file:
        json.dump(answer, out_file)
    os.replace(partial, path)


def main(argv: list[str] | None = None) -> int:
    args = _parse(sys.argv[1:] if argv is None else argv)
    lower_priority(STUDY_NICE)
    answer = asyncio.run(study(args))
    _write(args.out, answer)
    return 0 if answer.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
