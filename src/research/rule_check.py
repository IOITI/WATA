"""
The rule check of a Pine script: whether it can become a signal rule as it is.

- What its source says, read without running it by the checker process
  (src/ta_engine/checker/check.mjs, built from admin_ui/src/features/ta/engine/
  ruleCheck.ts): the alerts a rule maps, the events it only draws, what may make
  its signals differ live and in a backtest, what WATA serves otherwise. Its
  findings are said there, in TypeScript, where the editor marks them too.
- What a run test on recorded bars found (the study's mode ``rule``,
  src/research/study.py): a backtest runs a whole engine day at once and takes
  every bar's alerts from that run, while live each new bar is the last of its
  run. A run ending at a bar is what live had then, its series included: its
  alerts there, and at the bars before, are set against the day's run's. Then
  how often each alert fires, whether more history changes them, what a run
  costs. Those findings are said here.

Pure, but for the checker's client: the study runs the windows, this module
says what came of them.
"""

import hashlib
import json
import os
from datetime import datetime, timezone

from src.ta_engine.runner import NodeRunner, RunnerFailed, find_node, runner_command

# The checker: TradingView's rules, WATA's tickers and the rule check, in a confined Node process of its own.
CHECKER_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ta_engine", "checker", "check.mjs")
CHECKER_MEMORY_MB = 256
CHECKER_TIMEOUT_SECONDS = 20
# Raised when what the run test keeps or finds changes: a report made before says it was made by an older check.
RUN_FORMAT = 1

LEVELS = ("blocks", "repaints", "risk", "change", "note")
NEEDS_ALERTS = frozenset({"no-alerts", "alert-messages", "strategy"})
# What may repaint that a run ending at a bar shows: timenow and math.random read the test's own clock in both runs.
SEEN_BY_THE_TEST = frozenset({"bar-state", "bar-end", "other-series", "visible-range", "strategy-state"})
# Of an alert that fires again on the next bar this share of the times or more, at least this many times.
REPEATS_SHARE = 0.5
REPEATS_LEAST = 5
# Whole runs of an engine day: Claude's shadow rules are refused over 2 s (admin.mcp.shadow_day_ms); the engine
# evaluates its rules every 10 s.
SLOW_NOTE_MS = 2000
SLOW_CHANGE_MS = 8000
EXAMPLES = 3


class CheckerMissing(Exception):
    """No Node, or no built checker (npm run build:checker): the rule check of a source cannot be made here."""


def _no_series(ask: dict) -> list:
    raise ValueError("The checker reads no series")


class Checker:
    """
    One checker process, started when first asked, one request at a time: a
    service that checks many sources keeps it (the admin), a study starts it for
    its own.
    """

    def __init__(self, node: str = "node", *, path: str = CHECKER_PATH):
        self._node = node
        self._path = path
        self._runner: NodeRunner | None = None
        self.versions: dict = {}

    async def _started(self) -> NodeRunner:
        if self._runner is not None and self._runner.running:
            return self._runner
        found = find_node(self._node)
        if found is None or not os.path.isfile(self._path):
            raise CheckerMissing("The checker is not there: Node 24 and check.mjs (npm run build:checker)")
        self._runner = NodeRunner(runner_command(found, self._path, CHECKER_MEMORY_MB), _no_series,
                                  timeout_seconds=CHECKER_TIMEOUT_SECONDS)
        await self._runner.start()
        hello = await self._runner.request({"op": "hello"})
        self.versions = {"validator": hello.get("validator"), "rule_check": hello.get("rule_check"), "node": hello.get("node")}
        return self._runner

    async def rule_check(self, source: str, libraries: list[dict], probe: bool = False) -> dict:
        """
        What the checker reads in ``source`` with ``libraries`` (each {id, name,
        version, source}, the names they declare): ``check`` and TradingView's
        rules (``tradingview_rules``); with ``probe``, the run test's probe too
        (``liveProbe`` of ruleCheck.ts: never kept in a report).
        """
        runner = await self._started()
        answer = await runner.request({"op": "rule_check", "source": source, "libraries": [
            {key: library.get(key) for key in ("id", "name", "version", "source")} for library in libraries]})
        found = {"check": answer.get("check") or {}, "tradingview_rules": answer.get("tradingview_rules") or []}
        return {**found, "probe": answer.get("probe")} if probe else found

    async def validate(self, source: str) -> list | None:
        """TradingView's rules (v6) and WATA's tickers; None for a source without its version."""
        runner = await self._started()
        try:
            return (await runner.request({"op": "validate", "source": source})).get("diagnostics") or []
        except RunnerFailed:
            return None

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.stop()
            self._runner = None


def digest_of(source: str, libraries: list[dict]) -> str:
    """What a report is kept by: the source and its libraries, as each one's name, version and source."""
    pins = sorted([library.get("name") or "", library.get("version") or 0,
                   hashlib.sha256((library.get("source") or "").encode()).hexdigest()] for library in libraries)
    return hashlib.sha256(json.dumps([source, pins]).encode()).hexdigest()


# ── what the run test compares ──


def alert_key(alert: dict, bar_time: int) -> tuple:
    """An alert as live and a backtest must both give it: its bar, its call, its title, its message, its confidence."""
    return bar_time, alert.get("id"), alert.get("title"), alert.get("message"), alert.get("confidence")


def label(alert: dict) -> str:
    return alert.get("title") or alert.get("id") or alert.get("type") or "alert"


def plan_checkpoints(bars: list, alerted: set[int], most: int, live: set[int] = frozenset()) -> list[int]:
    """
    The bars a run is ended at (their indices), ``most`` at most, never the day's
    last (the day's run ends there too), in order: half where live may alert
    (``live``, the bars the probe alerted at: a run with every bar read as its
    last), then half of the rest where the day's run alerted, then spread evenly.
    An alert only live raises, behind ``barstate.isrealtime`` say, is never where
    the day's run alerted: without the probe, only chance ends a run there.
    """
    if most <= 0 or len(bars) < 2:
        return []
    candidates = list(range(len(bars) - 1))
    chosen = set(_spread([index for index in candidates if bars[index][0] in live], most // 2)) if live else set()
    with_alerts = [index for index in candidates if bars[index][0] in alerted and index not in chosen]
    chosen.update(_spread(with_alerts, (most - len(chosen)) // 2))
    rest = [index for index in candidates if index not in chosen]
    chosen.update(_spread(rest, most - len(chosen)))
    return sorted(chosen)


def _spread(items: list[int], count: int) -> list[int]:
    if count <= 0 or not items:
        return []
    if count >= len(items):
        return list(items)
    step = len(items) / count
    return [items[min(len(items) - 1, int(step * index + step / 2))] for index in range(count)]


def compare_at(day: dict, prefix: dict, end_ms: int) -> dict:
    """
    A run ending at ``end_ms`` (``prefix``: its alerts as (bar, alert) pairs)
    against the day's run (``day``): at the bars closing then, what only live
    raised and what only the backtest did, and at the bars before, what changed.
    """
    def keys(alerts: list, at_end: bool) -> set:
        return {alert_key(alert, bar[0]) for bar, alert in alerts if (bar[6] == end_ms) == at_end and bar[6] <= end_ms}

    live, backtest = keys(prefix["alerts"], True), keys(day["alerts"], True)
    before_live, before_backtest = keys(prefix["alerts"], False), keys(day["alerts"], False)
    return {
        "live_only": sorted(live - backtest, key=str), "backtest_only": sorted(backtest - live, key=str),
        "earlier": sorted((before_live ^ before_backtest), key=str),
    }


def alert_stats(days: list[dict]) -> list[dict]:
    """By alert (its title, or its call for alert()): how often, on how many bars, and how often again on the next bar."""
    found: dict[str, dict] = {}
    for day in days:
        position = {bar[0]: index for index, bar in enumerate(day["bars"])}
        by_name: dict[str, set[int]] = {}
        for bar, alert in day["alerts"]:
            by_name.setdefault(label(alert), set()).add(position.get(bar[0], -1))
        for name, indices in by_name.items():
            entry = found.setdefault(name, {"title": name, "count": 0, "repeats": 0, "longest": 0})
            entry["count"] += len(indices)
            entry["repeats"] += sum(1 for index in indices if index - 1 in indices)
            run = longest = 0
            for index in sorted(indices):
                run = run + 1 if index - 1 in indices else 1
                longest = max(longest, run)
            entry["longest"] = max(entry["longest"], longest)
    total = sum(len(day["bars"]) for day in days)
    for entry in found.values():
        entry["share"] = round(entry["count"] / total, 4) if total else None
    return sorted(found.values(), key=lambda entry: -entry["count"])


def same_bar(days: list[dict]) -> list[dict]:
    """The alerts raised together on one bar, by pair, and how often."""
    pairs: dict[tuple, int] = {}
    for day in days:
        by_bar: dict[int, set[str]] = {}
        for bar, alert in day["alerts"]:
            by_bar.setdefault(bar[0], set()).add(label(alert))
        for names in by_bar.values():
            ordered = sorted(names)
            for index, first in enumerate(ordered):
                for second in ordered[index + 1:]:
                    pairs[(first, second)] = pairs.get((first, second), 0) + 1
    return [{"titles": list(pair), "bars": count} for pair, count in sorted(pairs.items(), key=lambda item: -item[1])]


def marked(values: list) -> int:
    """How many bars a drawn event marks: true, or a value where it draws one (``cond ? low : na``)."""
    return sum(1 for value in values if value is True or (isinstance(value, (int, float)) and not isinstance(value, bool)))


def moment_text(moment_ms: int) -> str:
    """A bar's moment as the run test says it: 30 Oct 14:02 UTC."""
    return datetime.fromtimestamp(moment_ms / 1000, timezone.utc).strftime("%-d %b %H:%M UTC")


def _listed(names: list[str]) -> str:
    unique = list(dict.fromkeys(names))
    if len(unique) <= 1:
        return unique[0] if unique else ""
    return f"{', '.join(unique[:-1])} and {unique[-1]}"


def _quoted(names: list[str]) -> str:
    return _listed([f"“{name}”" for name in names])


def _plural(count: int, one: str, many: str | None = None) -> str:
    return f"{count:,} {one if count == 1 else (many or one + 's')}"


def _finding(id_: str, area: str, level: str, message: str, fix: str | None = None, **more) -> dict:
    return {"id": id_, "area": area, "level": level, "message": message, "fix": fix, "places": [], "count": 1,
            "excerpt": None, "through": None, **more}


def run_findings(tested: dict, check: dict) -> list[dict]:
    """What a run test found, said: ``tested`` as the study keeps it, ``check`` the static check of the same source."""
    findings = []
    if tested.get("failure"):
        failure = tested["failure"]
        where = f" (line {failure['line']})" if failure.get("line") and failure.get("file") in (None, "script") else ""
        findings.append(_finding("fails-on-bars", "wata", "blocks", f"It fails on WATA's bars: {failure.get('message')}{where}."))
        return findings
    days = tested.get("days") or []
    if not days or not sum(day.get("bars") or 0 for day in days):
        findings.append(_finding("no-bars", "wata", "note", "No recorded bars on those days: nothing was run."))
        return findings
    spoken = tested.get("spoken") or "those days"
    checked = tested.get("checkpoints") or {}
    if checked.get("run"):
        at_bar = checked.get("at_the_bar") or []
        earlier = checked.get("earlier") or []
        if at_bar:
            examples = "; ".join(entry["said"] for entry in at_bar[:EXAMPLES])
            findings.append(_finding(
                "repaints-at-the-bar", "repaint", "repaints",
                f"At {len(at_bar)} of the {_plural(checked['run'], 'bar')} tested, a run ending there, as live has it, "
                f"raised other alerts than the day's run, as a backtest has it: {examples}.",
                "Find what its alerts read that a run gives otherwise at its last bar (the findings above), and decide without it.",
                examples=at_bar[:EXAMPLES]))
        if earlier:
            findings.append(_finding(
                "repaints-earlier", "repaint", "repaints",
                f"At {len(earlier)} of the {_plural(checked['run'], 'bar')} tested, its alerts at earlier bars changed once "
                f"later bars came: a backtest of the day shows other signals than live had. {earlier[0]['said']}.",
                "Find what its alerts read that a run gives otherwise at its last bar (the findings above), and decide without it.",
                examples=earlier[:EXAMPLES]))
        # The bars where an alert came, live or in the backtest: at the others, nothing was compared (a test before
        # the count was kept compared at every bar).
        compared = checked.get("with_alerts", checked["run"])
        if not at_bar and not earlier and compared:
            findings.append(_finding(
                "same-live-and-backtest", "repaint", "note",
                f"At the {_plural(checked['run'], 'bar')} tested on {spoken}, its alerts were the same live and in a backtest."))
        elif not at_bar and not earlier:
            findings.append(_finding(
                "no-alert-compared", "repaint", "note",
                f"At the {_plural(checked['run'], 'bar')} tested on {spoken}, no alert came, live or in a backtest: "
                "the test could not compare them, and what may repaint stays as its source reads.",
                "Test it on other days, or on bars where it alerts more often."))
        if checked.get("plots_differ"):
            findings.append(_finding(
                "plots-differ", "repaint", "note",
                f"Its plots {_quoted(checked['plots_differ'])} read otherwise at the last bar of a run than in the day's run: "
                "what it draws there repaints."))
    stats = tested.get("alerts") or []
    declared = [alert.get("title") for alert in check.get("alerts") or [] if alert.get("title")]
    for entry in stats:
        if entry["count"] >= REPEATS_LEAST and entry["repeats"] >= REPEATS_SHARE * entry["count"]:
            findings.append(_finding(
                "fires-every-bar", "alerts", "change",
                f"“{entry['title']}” fires at every bar while its condition holds: {_plural(entry['count'], 'time')} on "
                f"{spoken}, {entry['repeats']:,} of them on the bar after another, up to {entry['longest']:,} in a row. "
                "A rule refuses a second entry while one is open, but enters again at the next bar after each exit.",
                "Fire on the change: `cond and not cond[1]`, or keep a `var` state.", title=entry["title"]))
    # An alert raised only live fired too: never firing in the days' runs is then its repainting, said above.
    fired = {entry["title"] for entry in stats} | set(checked.get("live_titles") or [])
    silent = [title for title in declared if title not in fired]
    if silent:
        findings.append(_finding("never-fires", "alerts", "change",
                                 f"{_quoted(silent)} never fired on {spoken}: check its inputs, or the bars it is run on."))
    for pair in tested.get("same_bar") or []:
        findings.append(_finding(
            "same-bar", "alerts", "note",
            f"{_quoted(pair['titles'])} fired on the same bar {_plural(pair['bars'], 'time')}: a rule sends an entry before "
            "a close of the same bar, and both of two entries.", titles=pair["titles"]))
        break
    candidates = tested.get("candidates") or []
    if candidates:
        said = _listed([f"“{entry['title']}” {_plural(entry['marked'], 'time')}" for entry in candidates])
        findings.append(_finding("candidates-marked", "alerts", "note", f"As alerts, the events it draws would have fired: {said}, on {spoken}."))
    if tested.get("na_plots"):
        findings.append(_finding(
            "na-plots", "wata", "note",
            f"Its plots {_quoted(tested['na_plots'])} have no value on any bar of the days tested: they draw nothing there, "
            "by design (a candle with no colour of its own) or because something they read has none on WATA."))
    history = tested.get("history") or {}
    if history.get("differing"):
        findings.append(_finding(
            "history-sensitive", "wata", "note",
            f"With {_plural(history['history_days'], 'day')} of history instead of {history['history_days'] - 1}, "
            f"{history['differing']:,} of its {_plural(history['compared'], 'alert')} on {spoken} change: it needs more warm-up, "
            "and TradingView, with years of history, may show others.",
            f"Give the rule {history['history_days']} days of history or more."))
    whole = check.get("wholeBecause")
    slowest = max((day.get("milliseconds") or 0 for day in days), default=0)
    if whole and slowest > SLOW_CHANGE_MS:
        findings.append(_finding(
            "slow", "wata", "change",
            f"Each new bar runs it whole, {slowest / 1000:,.1f} s for an engine day of {_plural(max(day['bars'] for day in days), 'bar')}: "
            "near the engine's ten seconds, its signals would come late.",
            "Leave out what makes it run whole (the findings above), or give it fewer days of history."))
    elif whole and slowest > SLOW_NOTE_MS:
        findings.append(_finding(
            "slow", "wata", "note",
            f"Each new bar runs it whole, {slowest / 1000:,.1f} s for an engine day: Claude's shadow rules are refused over 2 s."))
    for warning in (tested.get("runtime_warnings") or [])[:EXAMPLES]:
        findings.append(_finding("runtime-warning", "wata", "note", f"PineTS warned: {warning.get('message')}"))
    return findings


def combined(static: dict, tested: dict | None) -> dict:
    """
    The report of a source: its static findings, those a run test answered set
    down to notes when it found its alerts the same live and in a backtest, then
    the run test's, and the verdict of them all.
    """
    check = static.get("check") or {}
    findings = [dict(finding) for finding in check.get("findings") or []]
    if tested is not None:
        found = run_findings(tested, check)
        same = any(finding["id"] == "same-live-and-backtest" for finding in found)
        for finding in findings:
            if same and finding["level"] == "risk" and finding["id"] in SEEN_BY_THE_TEST:
                finding["level"] = "note"
                finding["tested"] = True
        findings += found
    return {"findings": findings, "verdict": verdict_of(findings)}


def verdict_of(findings: list[dict]) -> str:
    """As ruleCheck.ts's verdictOf: what a rule cannot be, what repaints, may repaint, is to change, worst first."""
    blocking = [finding for finding in findings if finding["level"] == "blocks"]
    if blocking:
        return "needs_alerts" if all(finding["id"] in NEEDS_ALERTS for finding in blocking) else "blocked"
    for level, verdict in (("repaints", "repaints"), ("risk", "may_repaint"), ("change", "to_change")):
        if any(finding["level"] == level for finding in findings):
            return verdict
    return "ready"


def said_difference(entry: tuple, kind: str) -> str:
    """One difference of a checkpoint, in words: “Long” at 30 Oct 14:02 UTC only live."""
    bar_time, call, title = entry[:3]
    return f"“{title or call}” at {moment_text(bar_time)} only {'live' if kind == 'live' else 'in the backtest'}"


def spoken_days(ends: list[int]) -> str:
    """The days a test covered, by the day each engine day ends on: “29 and 30 Oct”, “31 Oct and 3 Nov”."""
    days = [datetime.fromtimestamp(end, timezone.utc) for end in ends]
    if not days:
        return "no day"
    if len({(day.year, day.month) for day in days}) == 1:
        return f"{_listed([str(day.day) for day in days])} {days[0].strftime('%b')}"
    return _listed([day.strftime("%-d %b") for day in days])
