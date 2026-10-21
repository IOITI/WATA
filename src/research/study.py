"""
A study of a Pine script, run as a child process of the admin for Claude's
pine_check, pine_run and pine_signals (src/admin/studies.py):

    python -m src.research.study check   --request <file.json> --out <file.json>
    python -m src.research.study run     --request <file.json> --out <file.json>
    python -m src.research.study signals --request <file.json> --out <file.json>

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

It gives way to the trading services (nice 10, ahead of the backtests at 19)
and is the first the kernel stops when the container runs out of memory.
"""

import argparse
import asyncio
import hashlib
import json
import math
import os
import sys
from datetime import date

from src.admin.ta_rules import (
    ALERT_CALL_ACTIONS,
    IGNORE,
    InvalidRule,
    RuleStore,
    checked_alerts,
    checked_bars,
    checked_history_days,
    checked_inputs,
    timeframe_seconds,
)
from src.admin.ta_scripts import LIBRARY, InvalidScript, ScriptNotFound, ScriptStore, pine_facts
from src.backtest.clock import TradingClock
from src.price_history.process import lower_priority
from src.ta_engine.replay import (
    DAY,
    REPLAY_RULE_ID,
    ReplaySession,
    asset_rows_for,
    engine_rule_of,
    engine_windows,
    restart_minute_of,
    ta_settings,
)
from src.ta_engine.rules import EngineRule
from src.ta_engine.runner import RUNNER_PATH, NodeRunner, RunnerDown, RunnerFailed, find_node, runner_command
from src.ta_engine.signals import iso_seconds

STUDY_NICE = 10
MODES = ("check", "run", "signals")
# A script not saved yet runs under this id: it pins nothing that is on disk.
DRAFT_ID = "draft0000000"
DEFAULT_MEMORY_MB = 384
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
# TradingView's rules and WATA's tickers, checked by their own confined Node process (npm run build:checker).
CHECKER_PATH = os.path.join(os.path.dirname(os.path.dirname(RUNNER_PATH)), "checker", "check.mjs")
CHECKER_MEMORY_MB = 256
CHECKER_TIMEOUT_SECONDS = 20
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
    return checked_bars({"type": "time", "timeframe": text}, any_length=True)


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


async def tradingview_rules(source: str, node: str) -> list | None:
    """What TradingView's rules (v6) and WATA's tickers find in a source; None where the checker is not built."""
    found = find_node(node)
    if found is None or not os.path.isfile(CHECKER_PATH):
        return None

    async def no_series(ask: dict) -> list:
        raise ValueError("The checker reads no series")

    checker = NodeRunner(runner_command(found, CHECKER_PATH, CHECKER_MEMORY_MB), no_series,
                         timeout_seconds=CHECKER_TIMEOUT_SECONDS)
    try:
        await checker.start()
        return (await checker.request({"op": "validate", "source": source})).get("diagnostics") or []
    except (RunnerFailed, RunnerDown):
        # A source without its version: PineTS's compile says so in its own words.
        return None
    finally:
        await checker.stop()


async def check(request: dict, scripts: ScriptStore, node: str, memory_mb: int, timeout: int) -> dict:
    found = find_node(node)
    if found is None or not os.path.isfile(RUNNER_PATH):
        raise StudyRefused("The Pine runner is not there: Node 24 and runner.mjs (npm run build:runner)")
    load, kind, checked_source = _check_load(request, scripts)
    rules = await tradingview_rules(checked_source, node)

    async def no_series(ask: dict) -> list:
        raise ValueError("A check reads no other series")

    runner = NodeRunner(runner_command(found, RUNNER_PATH, memory_mb), no_series, timeout_seconds=timeout)
    try:
        await runner.start()
        answer = await runner.request({
            "op": "load", "rule": "check0000000", **load, "inputs": {}, "ticker": "CAPITAL_US100", "timeframe": "1",
            "bars": CHECK_BARS, "detail": True,
        })
    except RunnerFailed as e:
        return {"ok": False, "error": e.error, "tradingview_rules": rules}
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
    return {
        "ok": True, "rule": {"name": rule["name"], "asset": f"{rule['asset']['provider']}/{rule['asset']['key']}",
                             "side": rule["side"], "bars": request.get("bars", "1")},
        "from": first.isoformat(), "to": last.isoformat(), "bars": len(studied_times),
        "warmup_bars": len(times) - len(studied_times), "milliseconds": window.milliseconds,
        "plots": plots, "alerts_by_title": by_title, "alerts": alerts[:MAX_ALERTS], "alerts_total": len(alerts),
        "warnings": window.warnings, "runtime_warnings": window.runtime_warnings,
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
