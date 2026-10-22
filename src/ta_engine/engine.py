"""
The TA engine: WATA's signal rules (src/admin/ta_rules.py) run on the bars the
Capital.com feed records, each bar evaluated by the Pine runner once it is
final, and what the alerts of a bar become journaled in ta_signal, then sent to
the trader when the rule is live.

Every ten seconds, shortly after the feed has written its bars (``tick``):

1. rules.json is read again if it changed. A rule switched on is loaded: its
   history (the days before, history_days, the rule's own when it has one) runs
   once, and its alerts are never sent. A rule switched off or deleted is
   unloaded; one whose script, inputs, bars or history changed is loaded again.
2. The ten-second bars written since are read, up to the last ten seconds the
   feed says it has written.
3. Each rule makes its bars of them. Those that became final go to the runner,
   and the alerts they raise become signals.

Each day at restart_time every rule starts again, its bars beginning
history_days before that moment (its own, or the engine's): each new bar runs
the script over all of a rule's bars, which this bounds to between history_days
and one day more. The ten-second bars of an epic are kept from the earliest any
of its rules needs. Best at a time the market is closed, as 21:15 UTC for the
Nasdaq 100's daily break.

Before anything is sent: the rule is live in rules.json as it is now, alone on
its indice; the action is long, short or close-position; the bar closed less
than max_signal_age_seconds ago; the feed is not stale. A signal is journaled
before it is sent, under its rule, bar and alert: one evaluated again is
journaled, and sent, once at most. A shadow rule's signals are only journaled.

Live rules and shadow rules run on two runners (two Node processes), the live
ones first in each tick: a shadow rule that fails, hangs or ends its runner
reloads the shadow rules alone, never a live one (a reload sends nothing, so a
live alert would be lost). The shadow rules get ``shadow_seconds_per_tick`` of
each tick; a rule left for the next one keeps its bars, none is lost. A rule
moved between shadow and live is loaded again on its new runner, and the
shadow rule given its bars longest ago goes first: none waits for ever behind
a slow one. The shadow runner is stopped while no shadow rule is on, and the
kernel stops it before anything else when memory runs short. Given one runner
only, the engine runs every rule on it, as it always did.

What each tick and each run took is measured as it goes and written once the
tick is done, in the background (src/ta_engine/monitoring.py): the admin's
Engine monitor reads it.
"""

import asyncio
import bisect
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from src.admin.prices import price_decimals
from src.admin.ta_rules import LIVE, OFF, SHADOW, bars_label, timeframe_seconds
from src.ta_engine.bars import RangeBars, TimeBars
from src.ta_engine.monitoring import APPEND, LOAD, RUN_ENDED, RUN_FAILED, Monitoring, RunMark, TickMark
from src.ta_engine.rules import EngineRule, RulesSnapshot
from src.ta_engine.runner import RunnerDown, RunnerFailed
from src.ta_engine.signals import Candidate, interpret, payload, settled
from src.ta_engine.tickers import parse_ticker

logger = logging.getLogger(__name__)

TICK_SECONDS = 10
# How long after a ten-second boundary the engine looks: the feed writes its bars just after it.
SETTLE_SECONDS = 1.5
# A rule that failed is tried again after this long, unless it changed meanwhile.
RETRY_SECONDS = 300
# A runner that keeps failing to start is tried again after these pauses, the last one repeated.
RUNNER_RETRY_SECONDS = (5, 15, 60, 300)
# A repeated message about the same rule is said on Telegram at most this often.
NOTIFY_EVERY_SECONDS = 600
# ...and about a shadow rule an agent made (Claude, through the admin's MCP endpoint): once a day.
AGENT_NOTIFY_EVERY_SECONDS = 86_400
MESSAGE_CHARACTERS = 2000

LOADING, WAITING, RUNNING, ERROR = "loading", "waiting", "running", "error"

# Why a live rule's signal is not sent (_refusal, _send), the start of what its journal says: the
# admin's Engine monitor counts the signals not sent by them (src/admin/ta_monitor.py, tested together).
NOT_ALONE = "More than one rule is live on {indice}: none of them sends"
TOO_OLD = "The bar closed {age:.0f} s ago, more than {limit} s"
STALE_FEED = "The Capital.com feed is stale"
NO_SENDER = "No way to send signals"
NOT_TAKEN = "Not a signal the web server takes: {error}"


@dataclass
class RunnerSlot:
    """A Pine runner and how it fares: the live rules' own, and the shadow rules'."""

    name: str
    runner: Any
    failures: int = 0
    retry_at: float = 0.0
    error: str | None = None


def _moment(seconds: float) -> datetime:
    return datetime.fromtimestamp(seconds, timezone.utc)


@dataclass
class RuleRun:
    """A rule as the engine runs it."""

    rule: EngineRule
    state: str = LOADING
    detail: str | None = None
    builder: TimeBars | RangeBars | None = None
    loaded: bool = False
    # The next ten-second row of its epic the builder reads.
    cursor: int = 0
    # Final bars not yet given to the runner.
    pending: list = field(default_factory=list)
    bars: int = 0
    last_bar_time: datetime | None = None
    last_alert_at: datetime | None = None
    milliseconds: int | None = None
    retry_at: float = 0.0
    run_key: tuple = ()
    # The runner it was loaded on.
    slot: RunnerSlot | None = None
    # When it was last given its bars: the shadow rule given them longest ago goes first.
    given_at: float = 0.0
    # Its last request to the runner, as the monitor measures it.
    mark: RunMark | None = None


@dataclass
class EpicRows:
    """The ten-second bars of one epic since the engine's anchor."""

    instrument_id: int
    decimals: int | None
    rows: list = field(default_factory=list)
    read_until: int = 0
    # Where its rows begin: read further back when a rule wants more history than those before it.
    read_from: int = 0


class Engine:
    def __init__(
        self,
        *,
        settings,
        rules: Callable[[], RulesSnapshot],
        source,
        runner,
        journal,
        sender=None,
        notify: Callable[[str], Awaitable[None]] | None = None,
        clock: Callable[[], float] = time.time,
        version: str = "unknown",
        shadow_runner=None,
        monitoring: Monitoring | None = None,
    ):
        self._settings = settings
        self._rules = rules
        self._source = source
        self._runner = runner
        self._live_slot = RunnerSlot("live", runner)
        # Without a runner of their own, the shadow rules share the live rules' (the engine as it was).
        self._shadow_slot = RunnerSlot("shadow", shadow_runner) if shadow_runner is not None else self._live_slot
        self._journal = journal
        self._sender = sender
        self._notify = notify
        self._clock = clock
        self._version = version
        self._started_at = _moment(clock())
        self._runs: dict[str, RuleRun] = {}
        self._file_errors: dict[str, str] = {}
        self._contested: frozenset = frozenset()
        self._epics: dict[str, EpicRows] = {}
        # The rule the runner is running now: a series it asks for begins at its anchor.
        self._asking: RuleRun | None = None
        self._covered: int | None = None
        self._restarted_at: int | None = None
        self._notified: dict[str, float] = {}
        self._stopping = asyncio.Event()
        # What each tick and run took, written in the background; the tick being measured.
        self.monitoring = monitoring or Monitoring(journal, days=settings.monitoring_days, clock=clock)
        self._mark: TickMark | None = None

    # ── the loop ──

    def stop(self) -> None:
        self._stopping.set()

    async def run(self) -> None:
        while not self._stopping.is_set():
            started = self._clock()
            try:
                await self.tick()
            except Exception as e:
                logger.error("TA engine tick failed: %s", e, exc_info=True)
                await self._write_state("error", f"{type(e).__name__}: {e}")
            now = self._clock()
            wait = TICK_SECONDS - (now % TICK_SECONDS) + SETTLE_SECONDS
            if now - started > TICK_SECONDS:
                wait = SETTLE_SECONDS
            try:
                await asyncio.wait_for(self._stopping.wait(), wait)
            except asyncio.TimeoutError:
                pass

    async def tick(self) -> None:
        now = self._clock()
        mark = self._mark = TickMark(now)
        try:
            await self._tick(now, mark)
        except Exception as e:
            mark.error = f"{type(e).__name__}: {e}"
            raise
        finally:
            # Every signal of the tick is sent: what it measured goes to the writer, which never holds the next one.
            self._mark = None
            mark.runner_starts = getattr(self._live_slot.runner, "starts", None)
            if self._shadow_slot is not self._live_slot:
                mark.shadow_runner_starts = getattr(self._shadow_slot.runner, "starts", None)
            self.monitoring.tick_ended(mark, self._clock(), {slot.name: slot.runner for slot in self._slots()})

    async def _tick(self, now: float, mark: TickMark) -> None:
        await self._apply(self._rules())
        mark.rules_on = sum(1 for run in self._runs.values() if run.rule.mode != OFF)
        reading = self._clock()
        self._covered = await self._source.covered_until()
        mark.read_seconds += self._clock() - reading
        mark.covered_until = self._covered
        if self._restart_due(now):
            mark.restarted = True
            await self._restart(now)
        active = [run for run in self._runs.values() if run.rule.mode != OFF and run.retry_at <= now]
        # The live rules' runner always (as the engine always did); the shadow rules' only while one is on.
        needed = {self._live_slot.name, *(self._slot_of(run).name for run in active)}
        for slot in self._slots():
            if slot is not self._live_slot and slot.name not in needed and slot.runner.running:
                # No shadow rule to run: the memory of its runner goes back.
                await slot.runner.stop()
        ready = {slot.name: slot.name in needed and await self._slot_ready(slot, now) for slot in self._slots()}
        if not any(ready.values()):
            mark.skipped = len(active)
            await self._write_states(now)
            return
        # Read again: a runner started just now (it had ended since the last tick) holds none of
        # its rules, which _slot_ready made anew. Those of before would send it bars of rules it never loaded.
        active = [run for run in self._runs.values() if run.rule.mode != OFF and run.retry_at <= now]
        # Live rules first: a shadow rule never holds back a signal that is sent. Then the shadow
        # rule given its bars longest ago: one slow rule never keeps the others waiting tick after tick.
        active.sort(key=lambda run: (0, 0.0) if run.rule.mode == LIVE else (1, run.given_at))
        reading = self._clock()
        for epic in {run.rule.epic for run in active}:
            mark.rows_read += await self._read_rows(epic, now)
        mark.read_seconds += self._clock() - reading
        lost: set[str] = set()
        shadow_until = None
        for run in active:
            slot = self._slot_of(run)
            if not ready[slot.name] or slot.name in lost:
                mark.skipped += 1
                continue
            if self._shadow_slot is not self._live_slot and slot is self._shadow_slot:
                if shadow_until is None:
                    shadow_until = self._clock() + self._settings.shadow_seconds_per_tick
                elif self._clock() >= shadow_until:
                    # Its rows wait where they are: it reads them at the next tick, no bar lost.
                    mark.deferred.append(run.rule.id)
                    continue
            run.given_at = now
            try:
                await self._advance(run, now)
            except RunnerDown as e:
                await self._runner_lost(slot, run, str(e), now)
                lost.add(slot.name)
        await self._write_states(now)

    # ── rules ──

    async def _apply(self, snapshot: RulesSnapshot) -> None:
        self._file_errors = dict(snapshot.errors)
        self._contested = snapshot.contested
        wanted = {rule.id: rule for rule in snapshot.rules}
        for rule_id in list(self._runs):
            if rule_id not in wanted:
                await self._unload(self._runs.pop(rule_id))
        for rule_id, rule in wanted.items():
            run = self._runs.get(rule_id)
            if run is None:
                self._runs[rule_id] = RuleRun(rule=rule, state=OFF if rule.mode == OFF else LOADING)
                continue
            if rule.mode == OFF:
                if run.rule.mode != OFF or run.loaded:
                    await self._unload(run)
                run.rule, run.state, run.detail = rule, OFF, None
                continue
            moved = run.slot is not None and run.slot is not self._slot_for(rule.mode)
            if rule.run_key != run.run_key or run.rule.mode == OFF or moved:
                # Another runner (shadow to live, or back): loaded again there, from its history.
                await self._unload(run)
                self._runs[rule_id] = RuleRun(rule=rule)
            else:
                # A new name, mode or mapping of the alerts: what runs stays as it is.
                run.rule = rule

    async def _unload(self, run: RuleRun) -> None:
        runner = (run.slot or self._slot_of(run)).runner
        if run.loaded and runner.running:
            try:
                await runner.request({"op": "unload", "rule": run.rule.id})
            except (RunnerFailed, RunnerDown) as e:
                logger.warning("Rule %s could not be unloaded: %s", run.rule.id, e)
        run.loaded, run.builder, run.pending = False, None, []

    def last_restart(self, now: float) -> int:
        """The last restart_time at or before ``now``, epoch seconds."""
        moment = _moment(now)
        minutes = self._settings.restart_minute
        restart = moment.replace(hour=minutes // 60, minute=minutes % 60, second=0, microsecond=0)
        if restart > moment:
            restart -= timedelta(days=1)
        return int(restart.timestamp())

    def _restart_due(self, now: float) -> bool:
        if self._restarted_at is None:
            self._restarted_at = self.last_restart(now)
            return False
        return self.last_restart(now) != self._restarted_at

    async def _restart(self, now: float) -> None:
        """Every rule starts again, from its history_days before now."""
        logger.info("TA engine: each rule starts again from its history before now (%d day(s) unless it says).",
                    self._settings.history_days)
        self._restarted_at = self.last_restart(now)
        self._epics.clear()
        # A new process gives the memory of the old one back.
        for slot in self._slots():
            await slot.runner.stop()
        for rule_id, run in list(self._runs.items()):
            if run.rule.mode != OFF:
                self._runs[rule_id] = RuleRun(rule=run.rule)

    # ── the runners ──

    def _slots(self) -> list[RunnerSlot]:
        return [self._live_slot] if self._shadow_slot is self._live_slot else [self._live_slot, self._shadow_slot]

    def _slot_for(self, mode: str) -> RunnerSlot:
        return self._shadow_slot if mode == SHADOW else self._live_slot

    def _slot_of(self, run: RuleRun) -> RunnerSlot:
        return self._slot_for(run.rule.mode)

    async def _slot_ready(self, slot: RunnerSlot, now: float) -> bool:
        if slot.runner.running:
            return True
        if now < slot.retry_at:
            return False
        try:
            await slot.runner.start()
        except Exception as e:
            slot.failures += 1
            pause = RUNNER_RETRY_SECONDS[min(slot.failures, len(RUNNER_RETRY_SECONDS)) - 1]
            slot.retry_at = now + pause
            whose = "" if slot is self._live_slot else "shadow rules' "
            slot.error = f"The {whose}Pine runner does not start: {e}"
            logger.error("%s (again in %d s)", slot.error, pause)
            await self._say(f"runner:{slot.name}", f"⚠️ WATA TA engine: {slot.error}", now)
            return False
        slot.failures, slot.error = 0, None
        # Nothing the old process held is there any more.
        for run in list(self._runs.values()):
            if run.loaded and run.slot is slot:
                self._runs[run.rule.id] = RuleRun(rule=run.rule, state=OFF if run.rule.mode == OFF else LOADING)
        return True

    async def _runner_lost(self, slot: RunnerSlot, run: RuleRun, why: str, now: float) -> None:
        """Nothing that runner held is left: its rules load again, the one it was running after a pause. The other runner's are untouched."""
        logger.error("TA engine: %s (rule %s)", why, run.rule.id)
        for rule_id, other in list(self._runs.items()):
            if self._slot_of(other) is not slot:
                continue
            fresh = RuleRun(rule=other.rule, state=OFF if other.rule.mode == OFF else LOADING)
            if other is run:
                fresh.state, fresh.detail, fresh.retry_at = ERROR, why, now + RETRY_SECONDS
            self._runs[rule_id] = fresh
        await self._say(run.rule.id, f"⚠️ WATA TA engine: {why}, while running {run.rule.name!r}.", now, every=self._every(run))

    # ── bars ──

    def history_days(self, rule: EngineRule) -> int:
        """The days of bars a rule is given before each engine day: its own, or the engine's history_days."""
        return self._settings.history_days if rule.history_days is None else rule.history_days

    def rule_anchor(self, rule: EngineRule, now: float) -> int:
        """Where a rule's bars begin: its history_days before the last restart_time."""
        return self.last_restart(now) - self.history_days(rule) * 86_400

    def anchor(self, now: float) -> int:
        """Where the ten-second bars the rules run on begin: the earliest of their anchors (the engine's history_days with no rule)."""
        days = [self.history_days(run.rule) for run in self._runs.values() if run.rule.mode != OFF]
        return self.last_restart(now) - max(days, default=self._settings.history_days) * 86_400

    async def _epic(self, epic: str, now: float) -> EpicRows | None:
        found = self._epics.get(epic)
        anchor = self.anchor(now)
        if found is None:
            instrument = await self._source.instrument(epic)
            if instrument is None:
                return None
            found = EpicRows(instrument_id=instrument["id"], decimals=instrument["decimals"], read_until=anchor, read_from=anchor)
            self._epics[epic] = found
        elif anchor < found.read_from:
            # A rule wants more history than those before it: the rows before are read, and each
            # rule that reads them keeps its place.
            older = await self._source.rows(found.instrument_id, anchor, found.read_from)
            found.rows[:0] = older
            found.read_from = anchor
            for run in self._runs.values():
                if run.builder is not None and run.rule.epic == epic:
                    run.cursor += len(older)
        return found

    async def _read_rows(self, epic: str, now: float) -> int:
        """Read the rows of ``epic`` the feed wrote since; how many were read, older ones a rule wanted included."""
        held = len(self._epics[epic].rows) if epic in self._epics else 0
        found = await self._epic(epic, now)
        if found is None:
            return 0
        covered = self._covered
        if covered is not None and covered > found.read_until:
            found.rows.extend(await self._source.rows(found.instrument_id, found.read_until, covered))
            found.read_until = covered
        return len(found.rows) - held

    def _builder(self, run: RuleRun, epic: EpicRows):
        bars = run.rule.rule["bars"]
        if bars["type"] == "time":
            return TimeBars(timeframe_seconds(bars["timeframe"]), run.rule.rule["side"])
        decimals = epic.decimals
        if decimals is None:
            decimals = price_decimals([row[4] for row in epic.rows[:2000]])
        return RangeBars(bars["ticks"], decimals, run.rule.rule["side"])

    # ── a rule's new bars ──

    async def _advance(self, run: RuleRun, now: float) -> None:
        epic = self._epics.get(run.rule.epic)
        if epic is None:
            run.state, run.detail = WAITING, f"The Capital.com feed has not recorded {run.rule.epic}"
            return
        if run.builder is None:
            run.builder, run.pending = self._builder(run, epic), []
            # From its own anchor: the rows before are another rule's history.
            run.cursor = bisect.bisect_left(epic.rows, self.rule_anchor(run.rule, now), key=lambda row: row[0])
            run.run_key = run.rule.run_key
        new_rows = epic.rows[run.cursor:]
        run.cursor = len(epic.rows)
        run.pending.extend(run.builder.add(new_rows, self._covered))
        if not run.pending:
            if not run.loaded:
                run.state, run.detail = WAITING, f"No bar of {run.rule.epic} yet"
            return
        bars, run.pending = run.pending, []
        if not run.loaded:
            await self._load(run, bars, now)
        else:
            await self._append(run, bars, now)

    def _load_request(self, run: RuleRun, bars: list) -> dict:
        rule = run.rule
        return {
            "op": "load",
            "rule": rule.id,
            "script": {"source": rule.source, "sha256": rule.rule["script"]["sha256"]},
            "libraries": [
                {"id": entry["id"], "name": entry["name"], "version": entry["version"],
                 "source": entry["source"], "sha256": entry["sha256"]}
                for entry in rule.libraries
            ],
            "inputs": rule.inputs(),
            "ticker": rule.ticker().text(),
            "timeframe": rule.timeframe(),
            "bars": bars,
        }

    async def _request(self, run: RuleRun, kind: str, message: dict, bars: list, now: float) -> dict:
        """The runner's answer to a load or an append of ``run``, measured for the monitor (ta_rule_run)."""
        slot = run.slot or self._slot_of(run)
        mark = run.mark = RunMark(
            rule_id=run.rule.id, mode=LIVE if run.rule.mode == LIVE else SHADOW, runner=slot.name, kind=kind,
            tick_at=now, started_at=self._clock(), bars_given=len(bars), bar_closed_at=bars[-1][6] / 1000 if bars else None,
        )
        if self._mark is not None:
            self._mark.runs.append(mark)
        self._asking = run
        try:
            answer = await slot.runner.request(message)
        except RunnerFailed as e:
            mark.ended_with(self._clock(), RUN_FAILED, str(e))
            raise
        except RunnerDown as e:
            mark.ended_with(self._clock(), RUN_ENDED, str(e))
            raise
        finally:
            self._asking = None
        mark.answered(self._clock(), answer, {bar[0] for bar in bars})
        return answer

    async def _load(self, run: RuleRun, bars: list, now: float) -> None:
        run.state, run.detail = LOADING, None
        run.slot = self._slot_of(run)
        try:
            answer = await self._request(run, LOAD, self._load_request(run, bars), bars, now)
        except RunnerFailed as e:
            await self._failed(run, f"The script does not run: {e}", now)
            return
        run.loaded, run.state = True, RUNNING
        run.detail = "; ".join(answer.get("warnings") or []) or None
        self._ran(run, answer, bars)
        logger.info("Rule %s (%s) loaded on %d bars in %d ms.", run.rule.id, run.rule.name, answer.get("bars", 0), answer.get("milliseconds", 0))

    async def _append(self, run: RuleRun, bars: list, now: float) -> None:
        try:
            answer = await self._request(run, APPEND, {"op": "append", "rule": run.rule.id, "bars": bars}, bars, now)
        except RunnerFailed as e:
            await self._failed(run, f"The script failed on a new bar: {e}", now)
            return
        run.state, run.detail = RUNNING, run.detail if run.state == RUNNING else None
        self._ran(run, answer, bars)
        await self._signals(run, answer, bars, now)
        run.mark.done_at = self._clock()

    def _ran(self, run: RuleRun, answer: dict, bars: list) -> None:
        run.bars = answer.get("bars", run.bars)
        run.milliseconds = answer.get("milliseconds")
        run.last_bar_time = _moment(bars[-1][0] / 1000)

    async def _failed(self, run: RuleRun, why: str, now: float) -> None:
        logger.warning("Rule %s (%s): %s", run.rule.id, run.rule.name, why)
        run.state, run.detail, run.retry_at = ERROR, why, now + RETRY_SECONDS
        # Loaded again from its history when it is tried again.
        run.loaded, run.builder, run.pending = False, None, []
        await self._say(run.rule.id, f"⚠️ WATA TA engine, rule {run.rule.name!r}: {why}", now, every=self._every(run))

    # ── signals ──

    def _refusal(self, run: RuleRun, bar: list, now: float) -> str | None:
        rule = run.rule.rule
        if rule["indice"] in self._contested:
            return NOT_ALONE.format(indice=rule["indice"])
        age = now - bar[6] / 1000
        if age > self._settings.max_signal_age_seconds:
            return TOO_OLD.format(age=age, limit=self._settings.max_signal_age_seconds)
        if self._covered is None or now - self._covered > self._settings.stale_feed_seconds:
            return STALE_FEED
        if self._sender is None:
            return NO_SENDER
        return None

    def _journal_row(self, run: RuleRun, candidate: Candidate, bar: list, mode: str, outcome: str, now: float) -> dict:
        rule = run.rule.rule
        return {
            "rule_id": rule["id"],
            "rule_name": rule["name"],
            "script_id": rule["script"]["id"],
            "script_version": rule["script"]["version"],
            "asset": f"{rule['asset']['provider']}/{rule['asset']['key']}",
            "side": rule["side"],
            "bars": bars_label(rule["bars"]),
            "bar_time": _moment(bar[0] / 1000),
            "bar_closed_at": _moment(bar[6] / 1000),
            "alert_key": candidate.key[:500],
            "alert_title": candidate.title,
            "alert_message": candidate.message[:MESSAGE_CHARACTERS],
            "action": candidate.action,
            "indice": rule["indice"],
            "confidence": candidate.confidence,
            "mode": mode,
            "raised_at": _moment(now),
            "outcome": outcome,
            "detail": candidate.detail,
        }

    async def _signals(self, run: RuleRun, answer: dict, bars: list, now: float) -> None:
        by_time = {bar[0]: bar for bar in bars}
        plots = answer.get("plots") or {}
        rule = run.rule.rule
        raised: dict[int, list[Candidate]] = {}
        for alert in answer.get("alerts") or []:
            if not isinstance(alert, dict):
                continue
            bar = by_time.get(alert.get("time"))
            if bar is None:
                # Only the bars just given raise signals.
                continue
            candidate = interpret(
                rule, alert, bar=bar, ticker=run.rule.ticker().text(), timeframe=run.rule.timeframe(), plots=plots, now=now,
            )
            if candidate is not None:
                raised.setdefault(bar[0], []).append(candidate)
        for time_ms, candidates in raised.items():
            bar = by_time[time_ms]
            # One trade a bar: an entry wins over a close of the same bar, which is journaled as not sent.
            for candidate in settled(candidates):
                run.last_alert_at = _moment(now)
                mode = LIVE if run.rule.mode == LIVE else SHADOW
                if candidate.action is None:
                    outcome = "refused"
                else:
                    outcome = "sending" if mode == LIVE else "shadow"
                signal_id = await self._journal.journal_signal(self._journal_row(run, candidate, bar, mode, outcome, now))
                if signal_id is not None and run.mark is not None:
                    run.mark.signals += 1
                if signal_id is None or outcome != "sending":
                    continue
                await self._send(run, candidate, bar, signal_id, now)

    async def _send(self, run: RuleRun, candidate: Candidate, bar: list, signal_id: int, now: float) -> None:
        rule = run.rule.rule
        refusal = self._refusal(run, bar, now)
        if refusal is None:
            try:
                body = payload(rule, candidate, bar_closed_at=bar[6] / 1000, now=now)
            except Exception as e:
                refusal = NOT_TAKEN.format(error=e)
        if refusal is not None:
            await self._journal.complete_signal(signal_id, "refused", detail=refusal)
            await self._say(f"{rule['id']}:refused", f"⚠️ WATA TA engine, rule {rule['name']!r}: {candidate.action} not sent. {refusal}.", now)
            return
        sent = await self._sender.send(body)
        if sent.ok:
            await self._journal.complete_signal(
                signal_id, "sent", sent_at=_moment(self._clock()), http_status=sent.http_status, signal_uuid=sent.signal_uuid,
            )
            logger.info("Rule %s sent %s on %s (bar %s).", rule["id"], candidate.action, rule["indice"], body["signal_timestamp"])
            return
        await self._journal.complete_signal(signal_id, "failed", http_status=sent.http_status, detail=sent.error)
        await self._say(
            f"{rule['id']}:failed",
            f"❌ WATA TA engine, rule {rule['name']!r}: {candidate.action} on {rule['indice']} could not be sent ({sent.error}).",
            now,
        )

    @staticmethod
    def _every(run: RuleRun) -> int:
        """How often a rule's troubles are said: once a day for a shadow rule an agent made."""
        if run.rule.mode != LIVE and run.rule.rule.get("made_by"):
            return AGENT_NOTIFY_EVERY_SECONDS
        return NOTIFY_EVERY_SECONDS

    async def _say(self, about: str, message: str, now: float, every: int = NOTIFY_EVERY_SECONDS) -> None:
        if self._notify is None or now - self._notified.get(about, float("-inf")) < every:
            return
        self._notified[about] = now
        try:
            await asyncio.wait_for(self._notify(message), 5)
        except Exception as e:
            logger.warning("The TA engine's message could not be sent to Telegram: %s", e)

    # ── what the engine says of itself ──

    def rule_states(self, now: float) -> list[dict]:
        states = []
        for run in self._runs.values():
            rule = run.rule.rule
            states.append({
                "rule_id": rule["id"],
                "updated_at": _moment(now),
                "state": OFF if rule["mode"] == OFF else run.state,
                "detail": run.detail,
                "mode": rule["mode"],
                "script_version": rule["script"]["version"],
                "bars": run.bars,
                "last_bar_time": run.last_bar_time,
                "last_alert_at": run.last_alert_at,
                "figures": {"milliseconds": run.milliseconds},
            })
        for rule_id, why in self._file_errors.items():
            if rule_id not in self._runs and rule_id != "*":
                states.append({
                    "rule_id": rule_id, "updated_at": _moment(now), "state": ERROR, "detail": why, "mode": OFF,
                    "figures": {},
                })
        return states

    def engine_state(self, now: float) -> tuple[str, str]:
        if self._live_slot.error:
            return "runner_down", self._live_slot.error
        if "*" in self._file_errors:
            return "error", self._file_errors["*"]
        active = [run for run in self._runs.values() if run.rule.mode != OFF]
        if not active:
            return "idle", "No rule is on"
        if self._covered is None or now - self._covered > self._settings.stale_feed_seconds:
            return "stale_feed", "The Capital.com feed is not recording: nothing is sent"
        failing = sum(1 for run in active if run.state == ERROR)
        detail = f"{len(active)} rule(s) on" + (f", {failing} failing" if failing else "")
        if self._shadow_slot.error:
            detail += f"; {self._shadow_slot.error}"
        return "running", detail

    async def _write_state(self, state: str, detail: str | None) -> None:
        now = self._clock()
        active = [run for run in self._runs.values() if run.rule.mode != OFF]
        try:
            await self._journal.write_state({
                "updated_at": _moment(now),
                "started_at": self._started_at,
                "version": self._version,
                "state": state,
                "detail": detail,
                "figures": {
                    "rules": len(self._runs),
                    "live": sum(1 for run in active if run.rule.mode == LIVE),
                    "shadow": sum(1 for run in active if run.rule.mode == SHADOW),
                    "failing": sum(1 for run in active if run.state == ERROR),
                    "covered_until": self._covered,
                    "runner": getattr(self._runner, "versions", {}) or {},
                    "runner_starts": getattr(self._runner, "starts", 0),
                    "shadow_runner_starts": getattr(self._shadow_slot.runner, "starts", 0)
                    if self._shadow_slot is not self._live_slot else None,
                    "monitoring": self.monitoring.figures(),
                },
            })
        except Exception as e:
            logger.warning("The TA engine's state could not be written: %s", e)

    async def _write_states(self, now: float) -> None:
        writing = self._clock()
        state, detail = self.engine_state(now)
        await self._write_state(state, detail)
        try:
            await self._journal.write_rule_states(self.rule_states(now))
        except Exception as e:
            logger.warning("The rules' state could not be written: %s", e)
        if self._mark is not None:
            self._mark.write_seconds += self._clock() - writing

    # ── what a script asks for (request.security) ──

    async def answer_series(self, ask: dict) -> list:
        """The final bars of another series in [from, to], from the same feed and the same anchor."""
        asking = self._asking
        started = self._clock()
        try:
            return await self._series(ask)
        finally:
            # Part of the run that asked: its count and time, beside the runner's own.
            if asking is not None and asking.mark is not None:
                asking.mark.asks += 1
                asking.mark.asks_seconds += self._clock() - started

    async def _series(self, ask: dict) -> list:
        ticker = parse_ticker(str(ask.get("ticker") or ""))
        if ticker is None or ticker.provider != "capital":
            raise ValueError(f"WATA has no live bars of {ask.get('ticker')!r}: only Capital.com's (CAPITAL_<EPIC>)")
        timeframe = str(ask.get("timeframe") or "")
        seconds = timeframe_seconds(timeframe)
        if ticker.range_ticks is None and seconds is None:
            raise ValueError(f"No bars of {timeframe!r}: from 10 seconds (10S) to a day (1440)")
        now = self._clock()
        epic = await self._epic(ticker.key, now)
        if epic is None:
            raise ValueError(f"The Capital.com feed has not recorded {ticker.key}")
        await self._read_rows(ticker.key, now)
        if ticker.range_ticks is not None:
            decimals = epic.decimals if epic.decimals is not None else price_decimals([row[4] for row in epic.rows[:2000]])
            builder = RangeBars(ticker.range_ticks, decimals, ticker.side)
        else:
            builder = TimeBars(seconds, ticker.side)
        rows = epic.rows
        if self._asking is not None:
            # From the asking rule's own anchor, as a replay builds the series it asks for.
            rows = rows[bisect.bisect_left(rows, self.rule_anchor(self._asking.rule, now), key=lambda row: row[0]):]
        bars = builder.add(rows, self._covered)
        start, end = ask.get("from"), ask.get("to")
        return [
            bar for bar in bars
            if (not isinstance(start, (int, float)) or bar[0] >= start) and (not isinstance(end, (int, float)) or bar[0] <= end)
        ]
