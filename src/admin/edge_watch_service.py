"""
Edge Watch as the admin runs it (the model is src/admin/edge_watch.py, pure): each owner's trades since its reference
run set against it, the verdicts said once on Telegram and to the pages, and the edge guard.

- ``live``: WATA's real closed positions since the day after the live reference's last day (a replay of the signals
  WATA received, chosen on its run's page), and beside them the live paper days' trades: while entries are paused,
  what the trader would have had. The live test restarts each time the operator resumes entries.
- each signal rule with a reference: its paper days' trades since the day after its reference's last day, and not
  before the last change of what it runs, nor before it went into shadow.

Nothing is stored but the models (one per reference run, under ``edge_watch/models``) and the verdicts said last
(``edge_watch/said.json``): the start of each track is derived, so a restart judges the same trades again.

The edge guard (``risk_management.pause_when_edge_broken``, off unless true) pauses new entries as the operator does,
said to be its own, when the live trades are judged broken (``should_pause``). Only the operator resumes them. It is
the admin's: it works while the admin runs, and health says when it cannot judge.

A task of the admin application, every 30 s while the market is open and every 15 minutes otherwise.
"""

import asyncio
import json
import logging
import os
import time
from datetime import date, datetime, timedelta, timezone

from src.admin import edge_watch as ew
from src.admin.backtests import LIVE_PAPER, NO_ENTRY
from src.admin.downloads import market_is_open, trading_timezone
from src.admin.errors import DatabaseUnavailable, NotPossible, UnknownRun
from src.admin.processes import DONE, read_json, write_json
from src.backtest.clock import TradingClock
from src.backtest.settings import from_config

logger = logging.getLogger(__name__)

FOLDER = "edge_watch"
SAID_FILE = "said.json"
OPEN_TICK_SECONDS = 30
CLOSED_TICK_SECONDS = 900
# Older than this, the judgements are made again when asked.
FRESH_SECONDS = 30
# The guard is said unable to judge once it has not judged for this long while it is on.
GUARD_SILENT_SECONDS = 300
REAL, PAPER = "real", "paper"
# The verdicts said on Telegram when a track comes to one, and when it leaves one.
NOTABLE = (ew.BROKEN, ew.WATCH)
LIVE_NAME = "the live trades"


def _day(moment) -> date | None:
    if not moment:
        return None
    return datetime.fromisoformat(str(moment).replace("Z", "+00:00")).date()


def _named(entry: dict, track: str) -> str:
    """How Telegram names a track."""
    if entry["owner"] != LIVE_PAPER:
        return f"rule {entry['name']!r} on paper"
    return LIVE_NAME if track == REAL else "the signals received, on paper"


def _later(*days: date | None) -> date | None:
    known = [day for day in days if day is not None]
    return max(known) if known else None


class EdgeWatch:
    def __init__(self, *, root: str, backtests, queries, rules, risk_state, config_path: str, clock=time.time,
                 notify=None, publish=None, audit_log=None):
        self._folder = os.path.join(root, FOLDER)
        self._backtests = backtests
        self._queries = queries
        # The signal rules (src/admin/ta_rules.py RuleStore), read again each time.
        self._rules = rules
        self._risk_state = risk_state
        self._config_path = config_path
        self._clock = clock
        self._notify = notify
        self._publish = publish
        self._audit_log = audit_log
        self._models: dict[str, dict] = {}
        # A finished paper run's trades never change: read once.
        self._paper_trades: dict[str, list[dict]] = {}
        self._last: dict | None = None
        self._judged_at: float | None = None
        self._building = asyncio.Lock()
        self._judging = asyncio.Lock()

    # ── what it reads ──

    def _config(self) -> dict:
        with open(self._config_path, encoding="utf-8") as config_file:
            return json.load(config_file)

    def guard_on(self, config: dict | None = None) -> bool:
        """Whether the edge guard may pause entries: ``risk_management.pause_when_edge_broken`` set to true."""
        config = self._config() if config is None else config
        for rule in (config.get("trade") or {}).get("rules") or []:
            if rule.get("rule_type") == "risk_management":
                return (rule.get("rule_config") or {}).get("pause_when_edge_broken") is True
        return False

    def _live_settings(self, config: dict) -> dict:
        try:
            return from_config(config).to_dict()
        except Exception as e:
            logger.warning(f"Edge Watch could not read the live settings: {e}")
            return {}

    async def model(self, run_id: str) -> dict | None:
        """A reference run's model: in memory, else as kept, else made (in a thread) and kept. None for a run not done."""
        found = self._models.get(run_id)
        if found is not None:
            return found
        async with self._building:
            found = self._models.get(run_id)
            if found is not None:
                return found
            path = os.path.join(self._folder, "models", f"{run_id}-v{ew.VERSION}.json")
            found = read_json(path)
            if not isinstance(found, dict) or found.get("run_id") != run_id:
                try:
                    days, trades, baseline = (self._backtests.days(run_id), self._backtests.trades(run_id),
                                              self._backtests.baseline(run_id))
                except (UnknownRun, NotPossible):
                    return None
                found = await asyncio.to_thread(ew.build_model, ew.day_blocks(days, trades), baseline, run_id=run_id)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                write_json(path, found)
            self._models[run_id] = found
            return found

    def _prune(self, kept: set[str]) -> None:
        """The models no reference names any more go."""
        for run_id in list(self._models):
            if run_id not in kept:
                del self._models[run_id]
        folder = os.path.join(self._folder, "models")
        try:
            names = os.listdir(folder)
        except OSError:
            return
        for name in names:
            run_id, _, version = name.removesuffix(".json").rpartition("-v")
            if run_id not in kept or version != str(ew.VERSION):
                try:
                    os.remove(os.path.join(folder, name))
                except OSError:
                    pass

    def _paper_results(self, owner: str, since: date | None) -> tuple[list[float], list[dict], list[str]]:
        """An owner's paper trades since ``since`` in the order they closed, their path, and the days made with older code."""
        trades: list[tuple[str, float]] = []
        stale = []
        for day, run_id in sorted((self._backtests.paper_index().get(owner) or {}).items()):
            if run_id == NO_ENTRY or (since is not None and date.fromisoformat(day) < since):
                continue
            rows = self._paper_trades.get(run_id)
            if rows is None:
                try:
                    if self._backtests.state(run_id).get("state") != DONE:
                        continue
                    rows = self._backtests.trades(run_id)
                    reference = self._backtests.reference_of(run_id) or {}
                except (UnknownRun, NotPossible):
                    continue
                self._paper_trades[run_id] = rows = [
                    {**row, "_stale": reference.get("stale")} for row in rows
                ]
            if rows and rows[0].get("_stale"):
                stale.append(day)
            for row in rows:
                closed = (row.get("exit") or {}).get("filled_at")
                if row.get("performance_percent") is not None and closed:
                    trades.append((str(closed), float(row["performance_percent"])))
        trades.sort()
        return [result for _, result in trades], [{"at": at, "result": result} for at, result in trades], stale

    async def _real_results(self, since: datetime, test_since: datetime) -> tuple[list[float], list[float], list[dict]]:
        rows = await self._queries.closed_results_since(since)
        results, tested, path = [], [], []
        for row in rows:
            result = float(row["position_total_performance_percent"])
            closed = row["execution_time_close"]
            results.append(result)
            moment = datetime.fromisoformat(str(closed).replace("Z", "+00:00"))
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
            if moment >= test_since:
                tested.append(result)
            path.append({"at": str(closed), "result": result})
        return results, tested, path

    # ── judging ──

    @staticmethod
    def _with_path(judgement: dict, path: list[dict]) -> dict:
        level = 1.0
        walked = []
        for number, step in enumerate(path, start=1):
            level *= max(0.0, 1 + step["result"] / 100)
            walked.append({"n": number, "at": step["at"], "result": step["result"],
                           "cumulative": round((level - 1) * 100, 2)})
        return {**judgement, "path": walked}

    async def _owner(self, owner: str, run_id: str, rule: dict | None, zone, live_settings: dict,
                     resumed_at: datetime | None) -> dict:
        reference = self._backtests.reference_of(run_id)
        model = await self.model(run_id) if reference and reference.get("done") else None
        last_day = date.fromisoformat(reference["last_day"]) if reference and reference.get("last_day") else None
        since = last_day + timedelta(days=1) if last_day else None
        if rule is not None:
            since = _later(since, _day(rule.get("run_changed_at")),
                           _day(rule.get("mode_changed_at")) if rule.get("mode") == "shadow" else None)
        differ = ew.settings_differ(self._backtests.settings_of(run_id), live_settings) if reference else []
        entry = {
            "owner": owner,
            "name": LIVE_NAME if owner == LIVE_PAPER else (rule or {}).get("name"),
            "mode": (rule or {}).get("mode"),
            "reference": reference,
            "since": since.isoformat() if since else None,
            "model": {key: (model or {}).get(key) for key in ("usable", "why_not", "reference", "test", "horizon")}
            if model else None,
            "cone": (model or {}).get("cone"),
            "tracks": {},
        }
        paper, paper_path, stale = self._paper_results(owner, since) if since else ([], [], [])
        judged = self._with_path(ew.judge(model, paper, settings_differ=differ), paper_path)
        judged["stale_days"] = stale
        entry["tracks"][PAPER] = judged
        if owner == LIVE_PAPER and since is not None:
            start = datetime.combine(since, datetime.min.time(), zone).astimezone(timezone.utc)
            test_since = max(start, resumed_at) if resumed_at else start
            try:
                results, tested, path = await self._real_results(start, test_since)
            except DatabaseUnavailable:
                results, tested, path = None, None, None
            if results is not None:
                real = self._with_path(ew.judge(model, results, tested=tested, settings_differ=differ), path)
                real["test_since"] = test_since.isoformat()
                entry["tracks"][REAL] = real
        main = entry["tracks"].get(REAL) if owner == LIVE_PAPER else entry["tracks"][PAPER]
        entry["verdict"] = (main or entry["tracks"][PAPER])["verdict"]
        entry["main"] = REAL if owner == LIVE_PAPER and REAL in entry["tracks"] else PAPER
        return entry

    async def judgements(self, *, max_age: float = FRESH_SECONDS) -> dict:
        """
        Every owner's judgement: ``live`` (None without a live reference) and each rule with a reference
        (``rules``), with the guard's state. Made again once older than ``max_age`` seconds.
        """
        now = self._clock()
        if self._last is not None and self._judged_at is not None and now - self._judged_at < max_age:
            return self._last
        async with self._judging:
            if self._last is not None and self._judged_at is not None and self._clock() - self._judged_at < max_age:
                return self._last
            config = self._config()
            zone = TradingClock(trading_timezone(config)).zone
            live_settings = self._live_settings(config)
            references = self._backtests.references()
            rules = {rule["id"]: rule for rule in self._rules.load()["rules"] if rule.get("id")}
            try:
                resumed_at = await self._queries.entries_resumed_at()
            except DatabaseUnavailable:
                resumed_at = None
            live, judged_rules = None, {}
            for owner, run_id in sorted(references.items()):
                if owner != LIVE_PAPER and owner not in rules:
                    continue
                try:
                    entry = await self._owner(owner, run_id, rules.get(owner), zone, live_settings, resumed_at)
                except Exception as e:
                    logger.warning(f"Edge Watch could not judge {owner}: {e}", exc_info=True)
                    continue
                if owner == LIVE_PAPER:
                    live = entry
                else:
                    judged_rules[owner] = entry
            self._prune(set(references.values()))
            self._last = {
                "live": live, "rules": judged_rules, "judged_at": datetime.fromtimestamp(self._clock(), timezone.utc).isoformat(),
                "guard": {"on": self.guard_on(config), "least_trades": ew.GUARD_LEAST_TRADES},
            }
            self._judged_at = self._clock()
            return self._last

    @staticmethod
    def listed(judgements: dict) -> dict:
        """The judgements without their cones and paths: for the lists and health."""
        def short(entry: dict | None) -> dict | None:
            if entry is None:
                return None
            return {
                **{key: value for key, value in entry.items() if key not in ("cone", "tracks")},
                "tracks": {name: ew.summary(track) | {"stale_days": track.get("stale_days", [])}
                           for name, track in entry["tracks"].items()},
            }
        return {**judgements, "live": short(judgements.get("live")),
                "rules": {owner: short(entry) for owner, entry in (judgements.get("rules") or {}).items()}}

    def state(self) -> dict:
        """What health reads: the last judgements, and how long ago they were made; never judges."""
        return {"judgements": self._last, "age": None if self._judged_at is None else self._clock() - self._judged_at}

    # ── what follows ──

    def _said(self) -> dict:
        found = read_json(os.path.join(self._folder, SAID_FILE))
        return found if isinstance(found, dict) else {}

    async def _say(self, judgements: dict) -> None:
        """Each track's new verdict said once when it comes to, or leaves, a notable one; and to the pages."""
        said = self._said()
        changed = False
        entries = [judgements.get("live")] + list((judgements.get("rules") or {}).values())
        for entry in entries:
            if entry is None:
                continue
            for track_name, track in entry["tracks"].items():
                key = f"{entry['owner']}:{track_name}"
                verdict, before = track["verdict"], said.get(key)
                if verdict == before:
                    continue
                said[key] = verdict
                changed = True
                if self._publish is not None:
                    self._publish("edge_watch_changed", owner=entry["owner"], track=track_name, verdict=verdict)
                if self._notify is not None and (verdict in NOTABLE or before in NOTABLE):
                    name = _named(entry, track_name)
                    try:
                        await self._notify(ew.said(name, track))
                    except Exception as e:
                        logger.warning(f"Edge Watch could not say {key}'s verdict: {e}")
        if changed:
            os.makedirs(self._folder, exist_ok=True)
            write_json(os.path.join(self._folder, SAID_FILE), said)

    async def _guard(self, judgements: dict) -> bool:
        """The live trades broken and the guard on: new entries paused, said once. True when it paused them."""
        live = judgements.get("live") or {}
        real = (live.get("tracks") or {}).get(REAL)
        if not judgements.get("guard", {}).get("on") or real is None or not ew.should_pause(real):
            return False
        judged_after = datetime.fromisoformat(real["test_since"])
        try:
            paused = await self._risk_state.pause_entries_by_edge_guard(datetime.now(timezone.utc), judged_after)
        except DatabaseUnavailable:
            logger.warning("The edge guard could not pause new entries: the database is unavailable")
            return False
        if not paused:
            return False
        reason = "; ".join(real["reasons"][:2])
        detail = f"the live trades since {live.get('since')}: {real['n']} trade(s), {real['cumulative']:+.2f} %; {reason}"
        logger.warning(f"The edge guard paused new entries: {detail}")
        if self._audit_log is not None:
            self._audit_log.append("edge-guard", "new entries paused by the edge guard", detail)
        if self._publish is not None:
            self._publish("entries_paused")
        if self._notify is not None:
            try:
                await self._notify(
                    f"⏸️ WATA: new entries paused by the edge guard. {detail[:1].upper()}{detail[1:]}. They stay paused "
                    f"until you resume them from the admin's dashboard; the Performance page shows the trades against "
                    f"their reference."
                )
            except Exception as e:
                logger.warning(f"The edge guard's pause could not be said: {e}")
        return True

    async def tick(self) -> dict:
        judgements = await self.judgements(max_age=0)
        await self._say(judgements)
        await self._guard(judgements)
        return judgements

    async def keep(self) -> None:
        """Until cancelled: judge every 30 s while the market is open, every quarter of an hour otherwise."""
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"Edge Watch could not judge: {e}", exc_info=True)
            try:
                open_now = market_is_open(self._config(), self._clock())
            except Exception:
                open_now = False
            await asyncio.sleep(OPEN_TICK_SECONDS if open_now else CLOSED_TICK_SECONDS)


def guard_silent(state: dict, guard_on: bool) -> bool:
    """The guard is on and has not judged for GUARD_SILENT_SECONDS: it cannot pause anything."""
    return guard_on and (state.get("age") is None or state["age"] > GUARD_SILENT_SECONDS)
