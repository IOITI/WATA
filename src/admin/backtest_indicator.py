"""
A day of a backtest with the script that made its signals, as it ran: what
the day's page draws under the run's signals and trades
(GET /api/backtests/{run_id}/days/{day}/indicator). The script itself is run
in the browser (Vela's engine), on the bars given here.

The replay (src/ta_engine/replay.py) runs a script per engine day, from one
restart_time (UTC) to the next, on bars built from the asset's ten-second rows
from ``history_days`` before that restart. Range bars change with where they
start, so the day is drawn on those same bars, built here by the replay's own
code, which nothing here changes: it is in every run's code digest.

- A Pine run keeps its script, its libraries and its inputs as it ran them
  (``sources/``, ``request.source.rule``): drawn exactly.
- A run of the signals a rule raised (paper trading) keeps the script version
  of each signal (``ta_signals.json``); the rest of what the rule ran then
  comes from its history (src/admin/rule_history.py), or from the rule as it is
  now when the history does not go back to that day, and the notes say so.
- A replay of the journal has no script of WATA: TradingView's alerts.
"""

import asyncio
import hashlib
import os
from collections import Counter
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from src.admin.errors import InvalidRequest, NotPossible
from src.admin.processes import DONE, read_json
from src.admin.rule_history import RuleHistory
from src.admin.ta_rules import InvalidRule, RuleNotFound, RuleStore, checked_bars
from src.admin.ta_scripts import ScriptNotFound, ScriptStore, pine_facts
from src.ta_engine.replay import (
    DAY,
    DayTooLarge,
    _builder,
    _decimals,
    built_bars,
    engine_windows,
    restart_minute_of,
    too_large,
)
from src.ta_engine.rules import EngineRule

PINE = "pine"
TA_SIGNALS = "ta_signals"
RAISED_FILE = "ta_signals.json"
NO_SCRIPT = "TradingView's alerts made this run's signals: no script of WATA to draw"
SWEEP = "A sweep's combinations each ran with inputs of their own: run one on its own to see its days"


def _instant(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).timestamp()


def _iso(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _said(moment: float) -> str:
    """A day as the notes say it: 29 Oct."""
    day = datetime.fromtimestamp(moment, timezone.utc)
    return f"{day.day} {day.strftime('%b')}"


def day_window(day: date, zone: str, restart_time: str) -> tuple[int, int]:
    """
    The engine day a run's day was traded in: the one holding its noon in the run's timezone,
    from the restart at or before it to the next (``engine_windows``). The bars that close
    after that restart, later in the evening, are the next engine day's.
    """
    noon = int(datetime(day.year, day.month, day.day, 12, tzinfo=ZoneInfo(zone)).timestamp())
    return engine_windows(noon, noon + 1, restart_minute_of(restart_time))[0]


def bars_of_label(label: str) -> dict:
    """A rule's bars from their one word (bars_label): R60 for range bars, else a timeframe."""
    text = str(label or "")
    if text[:1] in ("R", "r") and text[1:].isdigit():
        return checked_bars({"type": "range", "ticks": int(text[1:])})
    return checked_bars({"type": "time", "timeframe": text})


def day_version(rows: list[dict], begin: int, end: int, fallback: int | None) -> tuple[int | None, list[int], str]:
    """
    The version of the rule's script to draw an engine day with: the one that raised most of
    its signals (the later on a tie); without a signal that day, the nearest one raised before
    it, else after it, else ``fallback``. With the versions raised that day, and how it was chosen.
    """
    def closed(row):
        return _instant(row.get("bar_closed_at")) or _instant(row.get("raised_at"))

    versioned = [row for row in rows if isinstance(row.get("script_version"), int) and not isinstance(row["script_version"], bool)
                 and closed(row) is not None]
    inside = [row for row in versioned if begin < closed(row) <= end]
    if inside:
        counts = Counter(row["script_version"] for row in inside)
        latest = {row["script_version"]: closed(row) for row in sorted(inside, key=closed)}
        version = max(counts, key=lambda number: (counts[number], latest[number]))
        return version, sorted(counts), "raised"
    before = [row for row in versioned if closed(row) <= begin]
    if before:
        return max(before, key=closed)["script_version"], [], "before"
    after = [row for row in versioned if closed(row) > end]
    if after:
        return min(after, key=closed)["script_version"], [], "after"
    return fallback, [], "rule"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _pine_parts(folder: str, rule: dict) -> tuple[dict, list[dict]]:
    """A Pine run's script and libraries as it kept them in ``sources/``, each as its pin says."""
    def kept(pin: dict, what: str) -> str:
        path = os.path.join(folder, "sources", f"{pin['id']}-v{pin['version']}.pine")
        try:
            with open(path, encoding="utf-8") as in_file:
                text = in_file.read()
        except OSError:
            raise NotPossible(f"The run kept no copy of its {what} (version {pin['version']})") from None
        if pin.get("sha256") and _sha256(text) != pin["sha256"]:
            raise NotPossible(f"The run's copy of its {what} is not the one it ran")
        return text

    script = {"id": rule["script"]["id"], "version": rule["script"]["version"], "name": rule.get("name"),
              "source": kept(rule["script"], "script")}
    libraries = [
        {"name": pin["name"], "id": pin["id"], "version": pin["version"], "source": kept(pin, f"library {pin['name']!r}")}
        for pin in rule.get("libraries") or []
    ]
    return script, libraries


def _rule_parts(scripts: ScriptStore, script_id: str, version: int,
                definition: dict | None) -> tuple[dict, list[dict], bool, str | None]:
    """
    A version of a rule's script from the store, and its libraries: the definition's pins when
    it ran that version, else as the version imports them now. Whether they are those it ran, and
    what to say when they may not be.
    """
    try:
        source = scripts.source(script_id, version)
        name = scripts.script(script_id)["name"]
    except ScriptNotFound:
        raise NotPossible(f"Version {version} of the rule's script is gone: nothing to draw") from None
    script = {"id": script_id, "version": version, "name": name, "source": source}
    pinned = definition is not None and definition.get("script", {}).get("id") == script_id \
        and definition["script"].get("version") == version
    if pinned:
        libraries = []
        for pin in definition.get("libraries") or []:
            try:
                text = scripts.source(pin["id"], pin["version"])
            except ScriptNotFound:
                raise NotPossible(f"Version {pin['version']} of the library {pin['name']!r} is gone: nothing to draw") from None
            libraries.append({"name": pin["name"], "id": pin["id"], "version": pin["version"], "source": text})
        return script, libraries, True, None
    try:
        pins = RuleStore(scripts).libraries_of(pine_facts(source), name)
        libraries = [{"name": pin["name"], "id": pin["id"], "version": pin["version"],
                      "source": scripts.source(pin["id"], pin["version"])} for pin in pins]
    except (InvalidRule, ScriptNotFound) as e:
        raise NotPossible(f"Version {version} of the rule's script cannot be drawn: {e}") from None
    said = ("Its libraries as they are now: " + ", ".join(f"{entry['name']} version {entry['version']}"
                                                         for entry in libraries) + ".") if libraries else None
    return script, libraries, False, said


async def window_bars(rows, chart: dict, anchor: int, end: int) -> tuple[list, int | None]:
    """
    The strategy's bars of [anchor, end) as ``ReplaySession.window`` builds them, with the asset's
    decimals: its rows from ``rows`` (AssetRows), the engine's builder, built in a thread.
    """
    asset = chart["asset"]
    try:
        found, decimals = await rows.rows(asset["provider"], asset["key"], anchor, end)
    except ValueError as e:
        # No database to read them from, or an asset the feed never recorded.
        raise NotPossible(str(e)) from None
    if chart["bars"]["type"] == "range":
        decimals = _decimals(found, decimals)
    refusal = too_large(f"{asset['provider']}/{asset['key']}", chart["bars"], decimals)

    def build():
        return built_bars(_builder(chart["bars"], chart["side"], found, decimals), found, end, refusal)

    try:
        bars = await asyncio.to_thread(build)
    except DayTooLarge as e:
        raise InvalidRequest(str(e)) from None
    return bars, (decimals if decimals is not None else (_decimals(found, None) if found else None))


async def day_indicator(folder: str, day: date, *, scripts: ScriptStore, rows, rules: RuleStore | None = None,
                        history: RuleHistory | None = None) -> dict:
    """What to draw a run's day with; ``folder`` is the run's, its id already checked."""
    request = read_json(os.path.join(folder, "request.json")) or {}
    if (read_json(os.path.join(folder, "status.json")) or {}).get("state") != DONE:
        raise NotPossible("This backtest has no results yet")
    if request.get("kind") == "sweep":
        raise NotPossible(SWEEP)
    days = read_json(os.path.join(folder, "days.json")) or []
    if not any(entry.get("day") == day.isoformat() for entry in days):
        raise InvalidRequest(f"The run covers {request.get('from')} to {request.get('to')}: {day.isoformat()} is not one of its days")
    settings = (read_json(os.path.join(folder, "settings.json")) or {}).get("settings") or {}
    zone, restart_time = settings.get("timezone") or "Europe/Paris", settings.get("restart_time") or "21:15"
    begin, end = day_window(day, zone, restart_time)
    source = request.get("source") or {}
    notes: list[str] = []

    if source.get("kind") == PINE:
        rule = source["rule"]
        script, libraries = _pine_parts(folder, rule)
        chart = {"asset": rule["asset"], "side": rule["side"], "bars": rule["bars"]}
        inputs, inputs_version = rule.get("inputs") or [], rule["script"]["version"]
        history_days = settings.get("history_days", 1)
        exact = True
    elif source.get("kind") == TA_SIGNALS:
        rule_id = (source.get("rule") or {}).get("id")
        raised = read_json(os.path.join(folder, RAISED_FILE)) or []
        rules = rules or RuleStore(scripts)
        history = history or RuleHistory(scripts.root)
        try:
            rule_now = rules.rule(rule_id)
        except (RuleNotFound, InvalidRule):
            rule_now = None
        definition, known = history.definition_at(rule_now, rule_id, begin)
        version, raised_versions, chosen = day_version(raised, begin, end, (definition or {}).get("script", {}).get("version"))
        if version is None:
            raise NotPossible("The rule is gone, and no signal of it says which script it ran: nothing to draw")
        own = [row for row in raised if isinstance(row.get("script_id"), str)]
        script_id = (definition or {}).get("script", {}).get("id") or (own[-1]["script_id"] if own else None)
        if chosen == "raised" and own:
            script_id = next(row["script_id"] for row in own if row.get("script_version") == version)
        if script_id is None:
            raise NotPossible("The rule is gone, and no signal of it says which script it ran: nothing to draw")
        script, libraries, pinned, libraries_said = _rule_parts(scripts, script_id, version, definition)

        of_day = [row for row in raised if begin < (_instant(row.get("bar_closed_at")) or 0) <= end]
        last = of_day[-1] if of_day else None
        try:
            if last is not None:
                provider, key = str(last["asset"]).split("/", 1)
                chart = {"asset": {"provider": provider, "key": key}, "side": last["side"], "bars": bars_of_label(last["bars"])}
            elif definition is not None:
                chart = {"asset": definition["asset"], "side": definition["side"], "bars": definition["bars"]}
            else:
                chart = request["chart"]
        except (KeyError, ValueError, InvalidRule):
            chart = request["chart"]
        inputs = (definition or {}).get("inputs") or []
        inputs_version = (definition or {}).get("script", {}).get("version")
        history_days = (definition or {}).get("history_days", settings.get("history_days", 1))
        exact = known and pinned
        if len(raised_versions) > 1:
            notes.append(f"Its signals that day were raised by versions {', '.join(map(str, raised_versions))} of the "
                         f"script: drawn with version {version}, which raised most of them.")
        elif chosen != "raised":
            notes.append(f"No signal of the rule that day says which script it ran: drawn with version {version}"
                         + (", the nearest one raised." if chosen != "rule" else ", the rule's."))
        if definition is None:
            notes.append("The rule is gone, and WATA kept no record of its inputs then: drawn with the script's own.")
        elif not known:
            changed = _instant((rule_now or {}).get("run_changed_at"))
            notes.append(
                ("The rule changed " + (f"on {_said(changed)}, " if changed else "") + "after this day, before WATA kept "
                 "what each rule ran: ")
                + f"drawn with version {version}, which raised its signals, and the rule's inputs of now, matched by name."
            )
        elif not pinned:
            notes.append(f"The rule ran version {inputs_version} when this engine day began: drawn with version {version}, "
                         "which raised its signals, and the rule's inputs of then, matched by name.")
        if libraries_said:
            notes.append(libraries_said)
    elif source.get("kind") == "journal":
        raise NotPossible(NO_SCRIPT)
    else:
        raise NotPossible("This run's signals came from nowhere WATA can draw")

    anchor = begin - int(history_days) * DAY
    bars, decimals = await window_bars(rows, chart, anchor, end)
    engine_rule = EngineRule({"asset": chart["asset"], "side": chart["side"], "bars": chart["bars"], "inputs": []}, "")
    return {
        "kind": source["kind"],
        "script": script,
        "libraries": libraries,
        "inputs": inputs,
        "inputs_version": inputs_version,
        "chart": chart,
        "ticker": engine_rule.ticker().text(),
        "timeframe": engine_rule.timeframe(),
        "decimals": decimals,
        "window": {"begin": _iso(begin), "end": _iso(end), "anchor": _iso(anchor)},
        "history_days": int(history_days),
        "restart_time": restart_time,
        "bars": bars,
        "exact": exact,
        "notes": notes,
    }
