"""
What a rule's runs cost in the Pine runner, on a recorded day. Nothing is sent, journaled or written.

    PYTHONPATH=./:src/ python3 -m src.ta_engine.bench --rule <id> --day 2026-11-25 --files <recorded days>
    PYTHONPATH=./:src/ python3 -m src.ta_engine.bench --script <id> --asset capital/US100 --bars R60 \
        --map "Enter Long=long" --day 2026-11-25

One engine day (the one holding the day's noon, UTC), its bars built as a replay builds them, given to the
real runner the two ways WATA gives them:

- as a backtest does: one load of all its bars, a whole run (``whole``, ``--repeat`` times: the first
  compiles the script, the others are what each further day of a backtest costs);
- as the engine does: a load held of all the bars but the last ``--appends``, those added one at a time,
  each continuing the run, then a check (``continued``).

For each: the runner's own milliseconds (the run), the seconds it took with the request written and the
answer read, the size of both, the alerts answered against those the rule maps to a signal, and the
runner's memory. ``--full`` asks as WATA asked before v0.104.0: every alert and every plot kept and answered.

``--against <script id>`` runs another script under the same rule (a copy cut down by hand, say) and sets
its signals against the first's, bar for bar.

``--profile`` runs one more whole load in a runner started with V8's profiler (unconfined: it writes its
profile to a temporary folder) and says where the time went: the script's own compiled code, PineTS and the
runner around it, garbage collection, and the functions that took the most, each with the start of its code.
"""

import argparse
import asyncio
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, time as day_time, timezone

from src.admin.ta_rules import ALERT_ACTIONS, InvalidRule, RuleStore, bars_label
from src.admin.ta_scripts import ScriptNotFound, ScriptStore
from src.price_history.process import process_memory_mb
from src.ta_engine.replay import (
    ReplaySession, _config, _script_rule, asset_rows_for, engine_rule_of, engine_windows, restart_minute_of, ta_settings,
)
from src.ta_engine.runner import RUNNER_PATH, NodeRunner, RunnerFailed, find_node
from src.ta_engine.signals import interpret, iso_seconds, settled

# The rule a held run is loaded under: a replay's own id holds the whole runs.
HELD_RULE_ID = "bench000000b"
# Where a profile's time went.
SCRIPT, RUNTIME, COLLECTION, NODE, WAITING = (
    "the script's compiled code", "PineTS and the runner", "garbage collection", "Node itself", "waiting",
)


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m src.ta_engine.bench", description=__doc__.split("\n\n")[0])
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--rule", help="a signal rule, by its id")
    which.add_argument("--script", help="a script, by its id")
    parser.add_argument("--day", required=True, type=date.fromisoformat, help="the day, YYYY-MM-DD: the engine day holding its noon")
    parser.add_argument("--version", type=int, help="the script's version (its current one by default)")
    parser.add_argument("--asset", help="capital/<EPIC>, saxo/<id> or dukascopy/<label>")
    parser.add_argument("--side", default="bid", choices=("bid", "ask"))
    parser.add_argument("--bars", default="1", help="minutes (1, 5), seconds (10S), or R<ticks> for range bars")
    parser.add_argument("--inputs", default="{}", help='the inputs set, as JSON by their id: {"in_0": 12}')
    parser.add_argument("--map", action="append", default=[], help="TITLE=ACTION for an alertcondition (repeatable)")
    parser.add_argument("--messages", action="store_true", help="read every alert, and every alert() call, as a message")
    parser.add_argument("--indice", default="us100")
    parser.add_argument("--history-days", type=int, help="the days of bars before the day (the rule's own, else the engine's)")
    parser.add_argument("--restart-time", help="the engine's restart_time, HH:MM UTC (its configuration by default)")
    parser.add_argument("--root", help="the Technical Analysis folder")
    parser.add_argument("--prices", help="the price history folder (Dukascopy's day files)")
    parser.add_argument("--files", help="a folder of recorded days (capital/bars/<label>/<day>.csv.gz), instead of the database")
    parser.add_argument("--node", default="node")
    parser.add_argument("--memory-mb", type=int, default=1024, help="the runner's heap")
    parser.add_argument("--repeat", type=int, default=3, help="how many whole loads")
    parser.add_argument("--appends", type=int, default=200, help="how many of the day's last bars are added one at a time")
    parser.add_argument("--full", action="store_true", help="every alert and every plot kept and answered, as before v0.104.0")
    parser.add_argument("--against", help="another script's id: its signals set against the first's")
    parser.add_argument("--profile", action="store_true", help="where a whole run's time goes")
    return parser.parse_args(argv)


def _size(message: dict) -> int:
    return len(json.dumps(message, separators=(",", ":")))


def _memory_mb(runner: NodeRunner) -> float | None:
    """What the runner's process holds now, in MB: from /proc on Linux, from ps elsewhere."""
    process = runner._process
    if process is None:
        return None
    held, _ = process_memory_mb(process.pid)
    if held is not None:
        return held
    try:
        said = subprocess.run(["ps", "-o", "rss=", "-p", str(process.pid)], capture_output=True, text=True, timeout=5).stdout
        return round(int(said.strip()) / 1024, 1)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


async def _timed(runner: NodeRunner, message: dict) -> tuple[dict, dict]:
    """The runner's answer, and what it cost: its own milliseconds, the seconds with both ways, the sizes."""
    started = time.perf_counter()
    answer = await runner.request(message)
    seconds = time.perf_counter() - started
    return answer, {
        "milliseconds": answer.get("milliseconds"), "seconds": round(seconds, 4),
        "sent_bytes": _size(message), "answered_bytes": _size(answer),
    }


def _signals(session: ReplaySession, bars: list, answer: dict, begin: int, end: int) -> list[tuple]:
    """The signals an answer gives on the engine day, as a replay reads them: (bar, title, action, confidence)."""
    rule, engine_rule = session.rule, session.engine_rule
    by_time = {bar[0]: bar for bar in bars}
    by_bar: dict[int, list] = {}
    for alert in answer.get("alerts") or []:
        bar = by_time.get(alert.get("time"))
        if bar is None or not begin * 1000 < bar[6] <= end * 1000:
            continue
        candidate = interpret(
            rule, alert, bar=bar, ticker=engine_rule.ticker().text(), timeframe=engine_rule.timeframe(),
            plots=answer.get("plots") or {}, now=bar[6] / 1000,
        )
        if candidate is not None:
            by_bar.setdefault(bar[0], []).append(candidate)
    return [
        (iso_seconds(time_ms / 1000), candidate.title, candidate.action, candidate.confidence)
        for time_ms, candidates in sorted(by_bar.items())
        for candidate in settled(candidates)
    ]


def _spread(values: list[float]) -> dict:
    ordered = sorted(values)
    return {
        "median": round(statistics.median(ordered), 3), "p95": round(ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))], 3),
        "max": round(ordered[-1], 3),
    }


async def _measured(session: ReplaySession, begin: int, end: int, args) -> tuple[dict, list[tuple]]:
    """A script's runs on the engine day, and the signals its whole run gives."""
    bars, anchor = await session.day_bars(begin, end)
    if len(bars) <= args.appends:
        raise ValueError(f"The day has {len(bars)} bars: fewer than the {args.appends} to add one at a time")
    await session.start()
    runner = session.runner
    try:
        request = session.load_request(bars)
        started = _memory_mb(runner)
        whole, answer = [], {}
        for _ in range(max(1, args.repeat)):
            answer, cost = await _timed(runner, request)
            whole.append({**cost, "memory_mb": _memory_mb(runner)})
        signals = _signals(session, bars, answer, begin, end)
        loads = {
            "runs": whole, "alerts_answered": len(answer.get("alerts") or []), "signals": len(signals),
            "plots_answered": sum(len(values) for values in (answer.get("plots") or {}).values()),
            "signal_list": [list(signal) for signal in signals],
        }

        # In a runner of its own: what a held run takes is read from the process, which the whole runs left large.
        await session.start()
        runner = session.runner
        held_bars, added = bars[:-args.appends], bars[-args.appends:]
        before = _memory_mb(runner)
        answer, load = await _timed(runner, {**request, "rule": HELD_RULE_ID, "bars": held_bars, "continued": True})
        mode = {"continued": answer.get("continued"), "whole": answer.get("whole")}
        after_load = _memory_mb(runner)
        costs = []
        for bar in added:
            _, cost = await _timed(runner, {"op": "append", "rule": HELD_RULE_ID, "bars": [bar]})
            costs.append(cost)
        checked, check = await _timed(runner, {"op": "check", "rule": HELD_RULE_ID})
        continued = {
            **mode, "load": load,
            "held_mb": None if before is None or after_load is None else round(after_load - before, 1),
            "appends": {
                "bars": len(costs),
                "milliseconds": _spread([cost["milliseconds"] or 0 for cost in costs]),
                "seconds": _spread([cost["seconds"] for cost in costs]),
                "answered_bytes": _spread([cost["answered_bytes"] for cost in costs]),
            },
            "check": {**check, "compared": checked.get("compared"), "same": checked.get("same"), "memory_mb": _memory_mb(runner)},
        }
    finally:
        await session.stop()
    return {
        "bars": len(bars), "history_from": iso_seconds(anchor), "runner": session.versions, "memory_at_start_mb": started,
        "whole": loads, "continued": continued,
    }, signals


def _group(frame: dict) -> str:
    name, url = frame.get("functionName") or "", frame.get("url") or ""
    if name == "(garbage collector)":
        return COLLECTION
    if name == "(idle)":
        return WAITING
    if url.endswith(os.path.basename(RUNNER_PATH)):
        return RUNTIME
    if url.startswith("node:") or name in ("(program)", "(root)"):
        return NODE
    # What ``new Function`` made has no file: the script as PineTS compiled it, and its functions.
    return SCRIPT


def profile_said(profile: dict, bundle: str = "", most: int = 15) -> dict:
    """Where a V8 profile's time went, by group, and the functions that took the most themselves."""
    nodes = {node["id"]: node for node in profile.get("nodes") or []}
    taken: dict[int, int] = {}
    for node_id, micros in zip(profile.get("samples") or [], profile.get("timeDeltas") or []):
        taken[node_id] = taken.get(node_id, 0) + max(0, micros)
    groups: dict[str, float] = {}
    functions: dict[tuple, float] = {}
    for node_id, micros in taken.items():
        frame = nodes.get(node_id, {}).get("callFrame") or {}
        group = _group(frame)
        groups[group] = groups.get(group, 0.0) + micros / 1000
        key = (group, frame.get("functionName") or "(anonymous)", frame.get("lineNumber", -1), frame.get("columnNumber", -1))
        functions[key] = functions.get(key, 0.0) + micros / 1000
    lines = bundle.split("\n") if bundle else []

    def excerpt(group: str, line: int, column: int) -> str | None:
        if group != RUNTIME or not 0 <= line < len(lines):
            return None
        return lines[line][max(0, column):column + 90]

    total = sum(groups.values()) or 1.0
    busy = total - groups.get(WAITING, 0.0)
    return {
        "milliseconds": round(busy),
        "groups": {
            group: {"milliseconds": round(ms), "share_percent": round(100 * ms / busy, 1)}
            for group, ms in sorted(groups.items(), key=lambda item: -item[1]) if group != WAITING
        },
        "functions": [
            {"group": group, "function": name, "milliseconds": round(ms), "share_percent": round(100 * ms / busy, 1),
             "code": excerpt(group, line, column)}
            for (group, name, line, column), ms in sorted(functions.items(), key=lambda item: -item[1])[:most]
            if group != WAITING
        ],
    }


async def _profiled(session: ReplaySession, begin: int, end: int, args) -> dict:
    """One whole load in a runner started with V8's profiler, and where its time went."""
    node = find_node(args.node)
    folder = tempfile.mkdtemp(prefix="wata-bench-")
    try:
        async def answer(ask: dict) -> list:
            return await session._answer(ask)

        runner = NodeRunner(
            [node, f"--max-old-space-size={args.memory_mb}", "--cpu-prof", f"--cpu-prof-dir={folder}", RUNNER_PATH],
            answer, timeout_seconds=600,
        )
        bars, _ = await session.day_bars(begin, end)
        await runner.start()
        try:
            await runner.request(session.load_request(bars))
        finally:
            # The profile is written as the process ends: it is left the time to, where stop() would end it.
            process = runner._process
            process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), 60)
            except asyncio.TimeoutError:
                process.kill()
            await runner.stop()
        written = [name for name in os.listdir(folder) if name.endswith(".cpuprofile")]
        if not written:
            return {"error": "The runner wrote no profile"}
        with open(os.path.join(folder, written[0]), encoding="utf-8") as in_file:
            profile = json.load(in_file)
        with open(RUNNER_PATH, encoding="utf-8") as in_file:
            bundle = in_file.read()
        return profile_said(profile, bundle)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def signals_compared(first: list[tuple], second: list[tuple], most: int = 10) -> dict:
    """Two scripts' signals on the same bars: the same, or what each has alone."""
    ours, theirs = set(first), set(second)
    return {
        "same": ours == theirs, "first": len(first), "second": len(second),
        "only_first": [list(signal) for signal in sorted(ours - theirs)[:most]],
        "only_second": [list(signal) for signal in sorted(theirs - ours)[:most]],
    }


async def bench(args: argparse.Namespace) -> dict:
    config = _config()
    ta_root, prices_root, history_days, restart = ta_settings(config)
    restart = restart if args.restart_time is None else args.restart_time
    scripts = ScriptStore(args.root or ta_root)
    store = RuleStore(scripts)
    rule = store.rule(args.rule) if args.rule else _script_rule(args, scripts)
    store.verify(rule)
    history_days = rule.get("history_days", history_days) if args.history_days is None else args.history_days
    rows, pg = await asset_rows_for(config, args.prices or prices_root, rule["asset"]["provider"], files_root=args.files)
    noon = int(datetime.combine(args.day, day_time(12), timezone.utc).timestamp())
    begin, end = engine_windows(noon, noon + 1, restart_minute_of(restart))[0]

    def session_of(a_rule: dict) -> ReplaySession:
        return ReplaySession(
            a_rule, engine_rule_of(a_rule, scripts), rows, node=args.node, history_days=history_days,
            restart_minute=restart_minute_of(restart), messages=args.messages, memory_mb=args.memory_mb,
            lean=not args.full,
        )

    result = {
        "rule": {
            "id": rule["id"], "name": rule["name"], "asset": f"{rule['asset']['provider']}/{rule['asset']['key']}",
            "side": rule["side"], "bars": bars_label(rule["bars"]),
            "script": {"id": rule["script"]["id"], "version": rule["script"]["version"]},
        },
        "day": {"from": iso_seconds(begin), "to": iso_seconds(end), "history_days": history_days},
        "asked": "every alert and plot" if args.full else "what the rule reads",
    }
    try:
        result["measured"], signals = await _measured(session_of(rule), begin, end, args)
        if args.profile:
            result["profile"] = await _profiled(session_of(rule), begin, end, args)
        if args.against:
            script = scripts.script(args.against)
            pinned, libraries = store.pin(args.against, script["version"])
            other = {**rule, "script": pinned, "libraries": libraries}
            measured, other_signals = await _measured(session_of(other), begin, end, args)
            result["against"] = {
                "script": {"id": args.against, "name": script["name"], "version": script["version"]},
                "measured": measured, "signals": signals_compared(signals, other_signals),
            }
    except RunnerFailed as e:
        return {**result, "error": str(e), "where": e.error}
    finally:
        if pg is not None:
            await pg.close()
    return result


def main(argv: list[str] | None = None) -> int:
    args = _parse(sys.argv[1:] if argv is None else argv)
    for mapping in args.map:
        if "=" not in mapping or mapping.rpartition("=")[2].strip() not in ALERT_ACTIONS:
            print(f"--map {mapping!r}: TITLE=ACTION, the action one of {', '.join(ALERT_ACTIONS)}", file=sys.stderr)
            return 2
    try:
        result = asyncio.run(bench(args))
    except (InvalidRule, ScriptNotFound, ValueError, OSError) as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0 if "error" not in result else 1


if __name__ == "__main__":
    sys.exit(main())
