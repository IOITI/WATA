"""
The live check of each signal rule (src/admin/live_check.py): its real trades once it is live, its paper days, the
backtest configuration the operator linked to it and that configuration's calibrated run, set side by side day by day.

What is kept beside the runs (``<backtests_path>/live_check/``), written whole under one lock, ids matched first:
- ``links.json``: each rule's configuration, ``{rule_id: {"configuration", "set_at"}}``. Not in ``rules.json``, which
  is the TA engine's (src/admin/ta_rules.py).
- ``calibrated.json``: each rule's last calibrated run, ``{rule_id: {"run", "template", "values", "asked_at", "error",
  "settled"}}``.
- ``<rule_id>/calibration.jsonl``: what each evening measured (values, figures, what was kept and why), kept at most
  ``HISTORY_LINES``.

The calibrated run: each evening, the market closed by the live rules, after the configurations' nightly runs and only
while nothing at all waits or runs in the backtests' line (as the forward checks), the newest finished run of the
linked configuration made by itself, made again over its own days with what WATA's live trades measured (the signal's
delay, fills drawn from WATA's orders, the account WATA holds: ``live_check.calibration``). Asked only when the
template or the values changed since the last one; the one before removed once the next is done, unless something
rests on it (a favourite, a reference). Nothing here trades: no audit entry, as for the nightly runs.

The comparison is read on demand: the database for the live side (``rule_signals``, ``closed_positions``, the
execution ledger, the engine's starts), the runs' files for the others, in the pool's threads.
"""

import asyncio
import json
import logging
import os
import threading
import time
from datetime import date, datetime, timedelta, timezone

from starlette.concurrency import run_in_threadpool

from src.admin import live_check as lc
from src.admin.backtest_configurations import BASE_KEYS, UnknownConfiguration, checked_id
from src.admin.backtest_requests import EXECUTION_DAYS, measured_orders
from src.admin.backtests import CALIBRATED, MAX_NAME_LENGTH, MAX_RAISED_SIGNALS, NO_ENTRY
from src.admin.downloads import market_is_open, trading_timezone
from src.admin.errors import DatabaseUnavailable, InvalidRequest, LineFull, NotPossible, UnknownRun
from src.admin.execution_ledger import period_ledger
from src.admin.paper import debriefed_day
from src.admin.processes import CANCELLED, DONE, FAILED, INTERRUPTED, QUEUED, RUNNING, read_json, utc, write_json
from src.admin.ta_compare import default_tolerance
from src.backtest.clock import TradingClock
from src.backtest.execution import missing

logger = logging.getLogger(__name__)

FOLDER = "live_check"
LINKS_FILE = "links.json"
CALIBRATED_FILE = "calibrated.json"
HISTORY_FILE = "calibration.jsonl"
HISTORY_LINES = 400
TICK_SECONDS = 300
# The days the comparison reads when none are asked, and the most it reads.
SUMMARY_DAYS = 92
MOST_DAYS = 366
# The calibrations a rule's page shows, newest first.
SHOWN_HISTORY = 30


def _day(text) -> date | None:
    try:
        return date.fromisoformat(str(text)[:10]) if text else None
    except ValueError:
        return None


def _derived(run: dict) -> bool:
    return any(run.get(key) for key in BASE_KEYS)


def _done(run: dict) -> bool:
    return (run.get("status") or {}).get("state") == DONE


def template_of(runs: list[dict]) -> dict | None:
    """The run a configuration is compared and calibrated with: its newest finished run of one combination made by itself."""
    return next((run for run in runs if run["kind"] == "run" and _done(run) and not _derived(run) and not run.get("paper")), None)


class LiveCheck:
    def __init__(self, *, root: str, backtests, queries, rules, config_path: str, nightly=None, clock=time.time):
        self._folder = os.path.join(root, FOLDER)
        self._backtests = backtests
        self._queries = queries
        # The signal rules (src/admin/ta_rules.py RuleStore), read again each time.
        self._rules = rules
        self._config_path = config_path
        # The configurations' nightly runs, which go first.
        self._nightly = nightly
        self._clock = clock
        self._lock = threading.Lock()
        self._asking: set[str] = set()
        self._wakeup = asyncio.Event()
        backtests.on_finished(self._finished)

    # ── what it keeps ──

    def _path(self, name: str) -> str:
        return os.path.join(self._folder, name)

    def _read(self, name: str) -> dict:
        found = read_json(self._path(name))
        return found if isinstance(found, dict) else {}

    def _change(self, name: str, change) -> dict:
        with self._lock:
            kept = self._read(name)
            change(kept)
            os.makedirs(self._folder, exist_ok=True)
            write_json(self._path(name), kept)
            return kept

    def _history_path(self, rule_id: str) -> str:
        return os.path.join(self._folder, rule_id, HISTORY_FILE)

    def history(self, rule_id: str) -> list[dict]:
        """What the evenings measured for a rule, newest first."""
        try:
            with open(self._history_path(rule_id), encoding="utf-8") as history_file:
                lines = [json.loads(line) for line in history_file if line.strip()]
        except (OSError, ValueError):
            return []
        return list(reversed(lines))

    def _add_history(self, rule_id: str, line: dict) -> None:
        with self._lock:
            kept = list(reversed(self.history(rule_id)))[-(HISTORY_LINES - 1):] + [line]
            os.makedirs(os.path.dirname(self._history_path(rule_id)), exist_ok=True)
            temporary = self._history_path(rule_id) + ".tmp"
            with open(temporary, "w", encoding="utf-8") as out_file:
                out_file.writelines(json.dumps(found) + "\n" for found in kept)
            os.replace(temporary, self._history_path(rule_id))

    def _config(self) -> dict:
        with open(self._config_path, encoding="utf-8") as config_file:
            return json.load(config_file)

    def _rule(self, rule_id: str) -> dict:
        """The rule, by its id (RuleNotFound otherwise): the id is matched before any path is built."""
        return self._rules.rule(rule_id)

    # ── the link ──

    def links(self) -> dict:
        return self._read(LINKS_FILE)

    def link_of(self, rule_id: str) -> dict | None:
        found = self.links().get(rule_id)
        return found if isinstance(found, dict) and found.get("configuration") else None

    def linked_run(self, rule_id: str) -> dict | None:
        """The run the rule's linked configuration is compared with (``template_of``); None without a link or a run."""
        link = self.link_of(rule_id)
        return template_of(self._runs_of(link["configuration"])) if link else None

    def _runs_of(self, configuration: str) -> list[dict]:
        """Every run of a configuration as the list describes it, newest first."""
        found = []
        for run_id in self._backtests.run_ids():
            try:
                if self._backtests.configuration_of(run_id) == configuration:
                    found.append(self._backtests.listed(run_id))
            except UnknownRun:
                continue
        return found

    def _engine_history_days(self) -> int:
        try:
            return self._backtests.live_settings().history_days
        except NotPossible:
            return 1

    def strategy_of(self, rule: dict, template: dict) -> dict:
        """Whether a configuration's run plays the rule's strategy (``live_check.same_strategy``)."""
        return lc.same_strategy(rule, self._backtests.request_of(template["id"]), self._backtests.settings_of(template["id"]),
                                self._engine_history_days())

    def link(self, rule_id: str, configuration: str) -> dict:
        """
        A configuration linked to a rule as its backtest: refused when it has no finished run of its own, or when its
        run plays another strategy (another script source, libraries, chart, alerts, history or inputs).
        """
        rule = self._rule(rule_id)
        checked_id(configuration)
        runs = self._runs_of(configuration)
        if not runs:
            raise UnknownConfiguration(configuration)
        template = template_of(runs)
        if template is None:
            raise NotPossible("This configuration has no finished run of its own to set beside the rule")
        strategy = self.strategy_of(rule, template)
        if not strategy["same"]:
            failed = [check["detail"] for check in strategy["checks"] if check["state"] == "fail"]
            raise NotPossible(f"Its runs do not play {rule['name']!r}: {'; '.join(failed)}")
        self._change(LINKS_FILE, lambda kept: kept.update({rule_id: {"configuration": configuration, "set_at": utc(self._clock())}}))
        self.wake()
        return {"configuration": configuration, "strategy": strategy}

    def unlink(self, rule_id: str) -> None:
        self._rule(rule_id)
        self._change(LINKS_FILE, lambda kept: kept.pop(rule_id, None))

    def candidates(self, rule_id: str) -> list[dict]:
        """The configurations whose newest run plays the rule's strategy: what the rule's backtest may be linked to."""
        rule = self._rule(rule_id)
        groups: dict[str, list[dict]] = {}
        for run_id in self._backtests.run_ids():
            try:
                configuration = self._backtests.configuration_of(run_id)
                if configuration:
                    groups.setdefault(configuration, []).append(self._backtests.listed(run_id))
            except UnknownRun:
                continue
        found = []
        for configuration, runs in groups.items():
            template = template_of(runs)
            source = (template or {}).get("source") or {}
            if template is None or source.get("kind") != "pine":
                continue
            try:
                strategy = self.strategy_of(rule, template)
            except UnknownRun:
                continue
            if strategy["same"]:
                found.append({"configuration": configuration, "run": template["id"], "name": template.get("name"),
                              "from": template.get("from"), "to": template.get("to"),
                              "headline": template.get("headline"), "strategy": strategy})
        return sorted(found, key=lambda entry: entry["run"], reverse=True)

    # ── the tracks ──

    def _zone(self):
        return TradingClock(trading_timezone(self._config())).zone

    def _last_day(self, now: float | None = None) -> date:
        return debriefed_day(self._config(), self._clock() if now is None else now)

    def _period(self, rule: dict, first: date | None, last: date | None) -> tuple[date, date]:
        asked = first is not None or last is not None
        last = min(last or self._last_day(), self._last_day())
        made = _day(rule.get("created_at"))
        # A rule made after the last finished day has none yet: its period is that day alone, and empty.
        first = first or min(last, max(last - timedelta(days=SUMMARY_DAYS - 1), made or last - timedelta(days=SUMMARY_DAYS - 1)))
        if asked and first > last:
            raise InvalidRequest("The period ends before it starts")
        first = min(first, last)
        if (last - first).days >= MOST_DAYS:
            raise InvalidRequest(f"A period of {MOST_DAYS} days at most")
        return first, last

    async def _live(self, rule: dict, first: date, last: date) -> dict:
        """What the database says of the rule's days: its raised signals, the positions they opened, the ledger, the engine."""
        zone = self._zone()
        try:
            rows = await self._queries.rule_signals(rule["id"], first, last, limit=MAX_RAISED_SIGNALS)
        except DatabaseUnavailable:
            return {"rows": None, "positions": [], "ledger": [], "starts": [], "kept_since": None}
        positions, ledger, starts, kept_since = [], [], [], None
        if any(row.get("mode") == "live" for row in rows):
            try:
                positions = await self._queries.closed_positions(first, last)
            except DatabaseUnavailable:
                positions = []
            try:
                ledger = (await period_ledger(self._queries, trading_timezone(self._config()), first, last))["trades"]
            except (DatabaseUnavailable, ValueError):
                ledger = []
        clock = TradingClock(zone.zone)
        try:
            engine = await self._queries.engine_starts(datetime.fromtimestamp(clock.day_bounds(first)[0], timezone.utc),
                                                       datetime.fromtimestamp(clock.day_bounds(last)[1], timezone.utc))
            starts = [lc.moment(start) for start in engine.get("starts") or []]
            kept_since = lc.moment(engine.get("kept_since"))
        except DatabaseUnavailable:
            pass
        return {"rows": rows, "positions": positions, "ledger": ledger, "starts": [s for s in starts if s is not None],
                "kept_since": kept_since}

    def _run_files(self, run_id: str, cache: dict) -> dict | None:
        """A finished run's signals, trades and tested days, read once."""
        if run_id not in cache:
            try:
                if self._backtests.state(run_id)["state"] != DONE:
                    cache[run_id] = None
                else:
                    cache[run_id] = {
                        "signals": self._backtests.signals(run_id), "trades": self._backtests.trades(run_id),
                        "tested": {row.get("day") for row in self._backtests.days(run_id) if row.get("status") == "tested"},
                    }
            except (UnknownRun, NotPossible):
                cache[run_id] = None
        return cache[run_id]

    def _tracks(self, rule: dict, first: date, last: date, live: dict) -> dict:
        """Each day's tracks: in the pool's threads (the runs' files are read)."""
        zone = self._zone()
        cache: dict = {}
        link = self.link_of(rule["id"])
        runs = self._runs_of(link["configuration"]) if link else []
        backtests = [run for run in runs if run["kind"] == "run" and _done(run) and not _derived(run)]
        calibrated = next((run for run in runs if _done(run) and run.get("calibrated_of")
                           and (run.get("calibrated") or {}).get("rule") == rule["id"]), None)
        paper_days = self._backtests.paper_index().get(rule["id"]) or {}
        rows_by_day: dict[str, list[dict]] = {}
        for row in live["rows"] or []:
            raised = lc.moment(row.get("raised_at"))
            if raised is not None:
                day = datetime.fromtimestamp(raised, timezone.utc).astimezone(zone).date().isoformat()
                rows_by_day.setdefault(day, []).append(row)
        days = {}
        current = first
        while current <= last:
            name = current.isoformat()
            tracks = {}
            covering = next((run for run in backtests if (run.get("from") or "9999") <= name <= (run.get("to") or "")), None)
            if covering is not None:
                files = self._run_files(covering["id"], cache)
                if files and name in files["tested"]:
                    tracks[lc.BACKTEST] = self._day_of_run(files, name)
            if calibrated is not None and (calibrated.get("from") or "9999") <= name <= (calibrated.get("to") or ""):
                files = self._run_files(calibrated["id"], cache)
                if files and name in files["tested"]:
                    tracks[lc.CALIBRATED] = self._day_of_run(files, name)
            paper = paper_days.get(name)
            if paper and paper != NO_ENTRY:
                files = self._run_files(paper, cache)
                if files and name in files["tested"]:
                    tracks[lc.PAPER] = self._day_of_run(files, name)
            rows = rows_by_day.get(name) or []
            if any(row.get("mode") == "live" for row in rows):
                tracks[lc.LIVE] = lc.live_track([row for row in rows if row.get("mode") == "live"], live["positions"],
                                                live["ledger"])
            if tracks:
                days[name] = {"tracks": tracks, "runs": {
                    lc.BACKTEST: covering["id"] if lc.BACKTEST in tracks else None,
                    lc.CALIBRATED: calibrated["id"] if lc.CALIBRATED in tracks else None,
                    lc.PAPER: paper if lc.PAPER in tracks else None,
                }}
            current += timedelta(days=1)
        return {"days": days, "runs": runs}

    @staticmethod
    def _day_of_run(files: dict, day: str) -> dict:
        return lc.run_track([row for row in files["signals"] if row.get("day") == day],
                            [row for row in files["trades"] if row.get("day") == day])

    def _compared(self, rule: dict, first: date, last: date, live: dict, only: date | None = None) -> dict:
        tracks = self._tracks(rule, only or first, only or last, live)
        tolerance = default_tolerance(rule.get("bars") or {})
        days = []
        for name, found in sorted(tracks["days"].items(), reverse=True):
            compared = lc.day_comparison(date.fromisoformat(name), found["tracks"], tolerance,
                                         engine_starts=live["starts"], kept_since=live["kept_since"])
            compared["runs"] = found["runs"]
            days.append(compared)
        return {"days": days, "tracks": tracks, "tolerance_seconds": tolerance}

    # ── reading it ──

    def _state(self, rule: dict, runs: list[dict]) -> dict:
        """The rule's link, its strategy check, its backtest and its calibrated run as they stand."""
        link = self.link_of(rule["id"])
        out = {"link": None, "strategy": None, "template": None, "calibrated": None}
        if link is None:
            return out
        template = template_of(runs)
        nightly = self._backtests.configurations.entry(link["configuration"]).get("nightly") or {}
        out["link"] = {**link, "found": bool(runs), "nightly": {"on": bool(nightly.get("on")), "from": nightly.get("from")}}
        if template is not None:
            out["template"] = {key: template.get(key) for key in ("id", "name", "from", "to", "headline", "stale")}
            try:
                out["strategy"] = self.strategy_of(rule, template)
            except UnknownRun:
                out["strategy"] = None
        last = self._read(CALIBRATED_FILE).get(rule["id"])
        if isinstance(last, dict):
            state = None
            if last.get("run"):
                try:
                    state = self._backtests.state(last["run"])["state"]
                except UnknownRun:
                    state = None
            out["calibrated"] = {**last, "state": state}
        return out

    async def summary(self, rule_id: str, first: date | None = None, last: date | None = None) -> dict:
        """
        A rule's days side by side over a period (the last SUMMARY_DAYS by default), newest first, without their signals
        and trades: each track's figures, the gap and the replay's agreement with the engine; the days together; what
        the live trades measure for the calibrated run, and what the evenings measured before.
        """
        rule = self._rule(rule_id)
        first, last = self._period(rule, first, last)
        live = await self._live(rule, first, last)
        calibration = await self.calibration(rule) if live["rows"] is not None else None
        compared = await run_in_threadpool(self._compared, rule, first, last, live)
        days = [{key: day[key] for key in ("day", "present", "figures", "agreement", "runs")}
                | {"gap": _gap(day), "bridge": _without_keys(day["bridge"])} for day in compared["days"]]
        state = await run_in_threadpool(self._state, rule, compared["tracks"]["runs"])
        return {
            "rule": {"id": rule["id"], "name": rule.get("name"), "mode": rule.get("mode"),
                     "live_since": rule.get("mode_changed_at") if rule.get("mode") == "live" else None,
                     "bars": rule.get("bars")},
            **state,
            "period": {"from": first.isoformat(), "to": last.isoformat()},
            "database": live["rows"] is not None,
            "tolerance_seconds": compared["tolerance_seconds"],
            "days": days,
            "summary": lc.summary(compared["days"]),
            "calibration": {"now": _shown(calibration), "history": self.history(rule_id)[:SHOWN_HISTORY]},
        }

    async def day(self, rule_id: str, day: date) -> dict:
        """One day whole: each signal and each trade of the tracks there that day, the bridge, and their runs."""
        rule = self._rule(rule_id)
        if day > self._last_day():
            raise NotPossible("That day is not over yet: its trading hours are not")
        live = await self._live(rule, day, day)
        compared = await run_in_threadpool(self._compared, rule, day, day, live, day)
        if not compared["days"]:
            raise NotPossible("Nothing of the rule that day: no backtest, no paper day, no live signal")
        found = compared["days"][0]
        return {"rule": {"id": rule["id"], "name": rule.get("name"), "mode": rule.get("mode"), "bars": rule.get("bars")},
                "database": live["rows"] is not None, **found}

    async def calibration(self, rule: dict) -> dict | None:
        """What the rule's live trades and WATA's orders measure now, against its configuration's newest run."""
        link = self.link_of(rule["id"])
        if link is None:
            return None
        runs = await run_in_threadpool(self._runs_of, link["configuration"])
        template = template_of(runs)
        if template is None:
            return None
        last = self._last_day()
        first = last - timedelta(days=EXECUTION_DAYS - 1)
        try:
            rows = await self._queries.rule_signals(rule["id"], first, last, limit=MAX_RAISED_SIGNALS)
            live_rows = [row for row in rows if row.get("mode") == "live"]
            positions = await self._queries.closed_positions(first, last) if live_rows else []
            ledger = (await period_ledger(self._queries, trading_timezone(self._config()), first, last))["trades"] \
                if live_rows else []
            balance = await self._queries.last_balance()
        except (DatabaseUnavailable, ValueError):
            return None
        trades = list(lc.live_track(live_rows, positions, ledger)["trades"].values())
        execution = await measured_orders(self._queries, self._backtests)
        template_settings = await run_in_threadpool(self._backtests.settings_of, template["id"])
        found = lc.calibration(trades, samples_missing=missing(execution.get("samples")), balance=balance,
                               template=template_settings)
        return {**found, "template_run": template["id"], "execution": execution}

    # ── the calibrated runs ──

    def due(self, now: float | None = None) -> list[str]:
        """The linked rules whose calibrated run may be asked tonight (whether one is needed is known once measured)."""
        found = []
        for rule_id in self.links():
            if rule_id in self._asking:
                continue
            last = self._read(CALIBRATED_FILE).get(rule_id) or {}
            if last.get("run") and not last.get("settled"):
                try:
                    if self._backtests.state(last["run"])["state"] in (QUEUED, RUNNING):
                        continue
                except UnknownRun:
                    pass
            found.append(rule_id)
        return found

    async def _ask(self, rule_id: str) -> dict | None:
        """
        A rule's calibrated run asked when its template or its values changed since the last; None otherwise. Measured
        once an evening per template: the live trades and WATA's orders change only while the market is open.
        """
        try:
            rule = self._rule(rule_id)
            link = self.link_of(rule_id)
        except Exception:
            return None
        if link is None:
            return None
        template = template_of(await run_in_threadpool(self._runs_of, link["configuration"]))
        last = self._read(CALIBRATED_FILE).get(rule_id) or {}
        evening = self._last_day().isoformat()
        if template is None or (last.get("template") == template["id"] and last.get("day") == evening):
            return None
        found = await self.calibration(rule)
        if found is None:
            return None
        changes = lc.changes_of(found)
        if last.get("template") == found["template_run"] and last.get("values") == found["values"] and (
                last.get("run") or last.get("error") or not changes):
            self._change(CALIBRATED_FILE, lambda kept: kept.get(rule_id, {}).update(day=evening))
            return None
        line = {"at": utc(self._clock()), "template": found["template_run"], "values": found["values"],
                "template_values": found["template"], "measured": found["measured"], "said": found["said"]}
        if not changes:
            self._keep(rule_id, found, None, error="Nothing measured live differs from the backtest yet")
            self._add_history(rule_id, {**line, "run": None})
            return None
        self._asking.add(rule_id)
        try:
            name = f"Calibrated · {self._backtests.listed(found['template_run']).get('name') or found['template_run']}"
            made = self._backtests.calibrate(
                found["template_run"], name=name[:MAX_NAME_LENGTH], changes=changes,
                execution=found["execution"] if found["values"].get("execution") == "measured" else None,
                calibrated={"rule": rule_id, "values": found["values"]},
            )
        except LineFull:
            raise
        except (InvalidRequest, NotPossible, UnknownRun) as e:
            self._keep(rule_id, found, None, error=str(e))
            self._add_history(rule_id, {**line, "run": None, "error": str(e)})
            logger.info(f"The calibrated run of rule {rule_id} is not asked: {e}")
            return None
        finally:
            self._asking.discard(rule_id)
        self._keep(rule_id, found, made["id"])
        self._add_history(rule_id, {**line, "run": made["id"]})
        return made

    def _keep(self, rule_id: str, found: dict, run_id: str | None, *, error: str | None = None) -> None:
        def change(kept: dict) -> None:
            kept[rule_id] = {"run": run_id, "template": found["template_run"], "values": found["values"],
                             "asked_at": utc(self._clock()), "day": self._last_day().isoformat(), "error": error,
                             "settled": run_id is None}
        self._change(CALIBRATED_FILE, change)

    async def catch_up(self, now: float | None = None) -> int:
        """
        What ended settled; then, the market closed, nothing at all in the backtests' line and no nightly run due, one
        rule's calibrated run asked when needed. How many were asked (0 or 1).
        """
        now = self._clock() if now is None else now
        self.settle()
        if self._asking or market_is_open(self._config(), now) or not self._backtests.idle():
            return 0
        if self._nightly is not None and self._nightly.due(now):
            return 0
        for rule_id in self.due(now):
            try:
                made = await self._ask(rule_id)
            except LineFull as e:
                logger.info(f"The calibrated runs wait: {e}")
                return 0
            except DatabaseUnavailable:
                logger.info("The calibrated runs wait for the database")
                return 0
            if made is not None:
                return 1
        return 0

    def _finished(self, run_id: str, state: str, request: dict) -> None:
        """Told by the service once a run ends, on the event loop: a calibrated run is settled at once."""
        if isinstance(request.get(CALIBRATED), dict):
            try:
                self.settle()
            finally:
                self.wake()

    def settle(self) -> int:
        """Each rule's last calibrated run that ended: done, the one before it removed (unless kept). Idempotent."""
        settled = 0
        for rule_id, last in list(self._read(CALIBRATED_FILE).items()):
            if not isinstance(last, dict) or not last.get("run") or last.get("settled"):
                continue
            try:
                state = self._backtests.state(last["run"])["state"]
            except UnknownRun:
                state = None
            if state in (QUEUED, RUNNING):
                continue
            if state in (INTERRUPTED, None):
                # Cut by a restart of the admin, or removed: asked again.
                self._change(CALIBRATED_FILE, lambda kept: kept.pop(rule_id, None))
                continue
            if state == DONE:
                self._remove_others(rule_id, last["run"])
            error = None if state == DONE else (f"It {'failed' if state == FAILED else 'was stopped'}"
                                                if state in (FAILED, CANCELLED) else None)
            self._change(CALIBRATED_FILE, lambda kept: kept.get(rule_id, {}).update(settled=True,
                                                                                    **({"error": error} if error else {})))
            settled += 1
        return settled

    def _remove_others(self, rule_id: str, newest: str) -> None:
        """A rule's calibrated runs but its newest let go once that one is done, unless something rests on one."""
        references = set(self._backtests.references().values())
        for run_id in self._backtests.run_ids():
            if run_id == newest:
                continue
            try:
                request = self._backtests.request_of(run_id)
                if (request.get(CALIBRATED) or {}).get("rule") != rule_id or self._backtests.is_active(run_id):
                    continue
                if request.get("favourite") or run_id in references:
                    continue
                self._backtests.delete(run_id)
            except (UnknownRun, NotPossible):
                continue

    def wake(self) -> None:
        self._wakeup.set()

    async def keep(self) -> None:
        """Until cancelled: settle what ended and ask what is due, every few minutes or once woken."""
        while True:
            self._wakeup.clear()
            try:
                await self.catch_up()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"The live check's calibrated runs could not catch up: {e}")
            try:
                await asyncio.wait_for(self._wakeup.wait(), TICK_SECONDS)
            except asyncio.TimeoutError:
                pass


def _shown(calibration: dict | None) -> dict | None:
    """What the live trades measure now, as the pages show it: WATA's orders counted, not listed."""
    if calibration is None:
        return None
    execution = calibration.get("execution") or {}
    samples = execution.get("samples") or {}
    return {**{key: value for key, value in calibration.items() if key != "execution"},
            "orders": {"from": execution.get("from"), "to": execution.get("to"),
                       "measures": {kind: len(found or []) for kind, found in samples.items()}}}


def _gap(day: dict) -> dict | None:
    """A day's gap in points of summed trade results: live against the backtest, else paper against it."""
    figures = day["figures"]
    base = figures.get(lc.BACKTEST)
    if base is None:
        return None
    for name in (lc.LIVE, lc.PAPER):
        if name in figures:
            return {"against": name, "points": round(figures[name]["sum"] - base["sum"], 3)}
    return None


def _without_keys(found: dict) -> dict:
    """A day's bridge without the keys of each bucket's trades (the day's own page has them)."""
    out = {}
    for name, segment in found.items():
        if isinstance(segment, dict) and "buckets" in segment:
            out[name] = {**segment, "buckets": [{key: value for key, value in bucket.items() if key != "keys"}
                                                for bucket in segment["buckets"]]}
        else:
            out[name] = segment
    return out
