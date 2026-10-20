"""
The TA engine: WATA's signal rules (src/admin/ta_rules.py) run on the bars the
Capital.com feed records, each bar evaluated by the Pine runner once it is
final, and what the alerts of a bar become journaled in ta_signal, then sent to
the trader when the rule is live.

Every ten seconds, shortly after the feed has written its bars (``tick``):

1. rules.json is read again if it changed. A rule switched on is loaded: its
   history (the days before, history_days) runs once, and its alerts are never
   sent. A rule switched off or deleted is unloaded; one whose script, inputs or
   bars changed is loaded again.
2. The ten-second bars written since are read, up to the last ten seconds the
   feed says it has written.
3. Each rule makes its bars of them. Those that became final go to the runner,
   and the alerts they raise become signals.

Each day at restart_time every rule starts again, its bars beginning
history_days before that moment: each new bar runs the script over all of a
rule's bars, which this bounds to between history_days and one day more. Best
at a time the market is closed, as 21:15 UTC for the Nasdaq 100's daily break.

Before anything is sent: the rule is live in rules.json as it is now, alone on
its indice; the action is long, short or close-position; the bar closed less
than max_signal_age_seconds ago; the feed is not stale. A signal is journaled
before it is sent, under its rule, bar and alert: one evaluated again is
journaled, and sent, once at most. A shadow rule's signals are only journaled.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from src.admin.prices import price_decimals
from src.admin.ta_rules import LIVE, OFF, SHADOW, bars_label
from src.ta_engine.bars import RangeBars, TimeBars
from src.ta_engine.rules import EngineRule, RulesSnapshot
from src.ta_engine.runner import RunnerDown, RunnerFailed
from src.ta_engine.signals import Candidate, interpret, payload
from src.ta_engine.tickers import parse_ticker

logger = logging.getLogger(__name__)

TICK_SECONDS = 10
# How long after a ten-second boundary the engine looks: the feed writes its bars just after it.
SETTLE_SECONDS = 1.5
TIMEFRAME_SECONDS = {"1": 60, "5": 300, "15": 900, "60": 3600}
# A rule that failed is tried again after this long, unless it changed meanwhile.
RETRY_SECONDS = 300
# A runner that keeps failing to start is tried again after these pauses, the last one repeated.
RUNNER_RETRY_SECONDS = (5, 15, 60, 300)
# A repeated message about the same rule is said on Telegram at most this often.
NOTIFY_EVERY_SECONDS = 600
MESSAGE_CHARACTERS = 2000

LOADING, WAITING, RUNNING, ERROR = "loading", "waiting", "running", "error"


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


@dataclass
class EpicRows:
    """The ten-second bars of one epic since the engine's anchor."""

    instrument_id: int
    decimals: int | None
    rows: list = field(default_factory=list)
    read_until: int = 0


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
    ):
        self._settings = settings
        self._rules = rules
        self._source = source
        self._runner = runner
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
        self._anchor: int | None = None
        self._covered: int | None = None
        self._restarted_at: int | None = None
        self._runner_failures = 0
        self._runner_retry_at = 0.0
        self._runner_error: str | None = None
        self._notified: dict[str, float] = {}
        self._stopping = asyncio.Event()

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
        await self._apply(self._rules())
        self._covered = await self._source.covered_until()
        if self._restart_due(now):
            await self._restart(now)
        if not await self._runner_ready(now):
            await self._write_states(now)
            return
        active = [run for run in self._runs.values() if run.rule.mode != OFF and run.retry_at <= now]
        for epic in {run.rule.epic for run in active}:
            await self._read_rows(epic, now)
        for run in active:
            try:
                await self._advance(run, now)
            except RunnerDown as e:
                await self._runner_lost(run, str(e), now)
                break
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
            if rule.run_key != run.run_key or run.rule.mode == OFF:
                await self._unload(run)
                self._runs[rule_id] = RuleRun(rule=rule)
            else:
                # A new name, mode or mapping of the alerts: what runs stays as it is.
                run.rule = rule

    async def _unload(self, run: RuleRun) -> None:
        if run.loaded and self._runner.running:
            try:
                await self._runner.request({"op": "unload", "rule": run.rule.id})
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
        """Every rule starts again, from history_days before now."""
        logger.info("TA engine: each rule starts again from %d day(s) before now.", self._settings.history_days)
        self._restarted_at = self.last_restart(now)
        self._anchor = None
        self._epics.clear()
        # A new process gives the memory of the old one back.
        await self._runner.stop()
        for rule_id, run in list(self._runs.items()):
            if run.rule.mode != OFF:
                self._runs[rule_id] = RuleRun(rule=run.rule)

    # ── the runner ──

    async def _runner_ready(self, now: float) -> bool:
        if self._runner.running:
            return True
        if now < self._runner_retry_at:
            return False
        try:
            await self._runner.start()
        except Exception as e:
            self._runner_failures += 1
            pause = RUNNER_RETRY_SECONDS[min(self._runner_failures, len(RUNNER_RETRY_SECONDS)) - 1]
            self._runner_retry_at = now + pause
            self._runner_error = f"The Pine runner does not start: {e}"
            logger.error("%s (again in %d s)", self._runner_error, pause)
            await self._say("runner", f"⚠️ WATA TA engine: {self._runner_error}", now)
            return False
        self._runner_failures, self._runner_error = 0, None
        # Nothing the old process held is there any more.
        for run in self._runs.values():
            if run.loaded:
                self._runs[run.rule.id] = RuleRun(rule=run.rule, state=OFF if run.rule.mode == OFF else LOADING)
        return True

    async def _runner_lost(self, run: RuleRun, why: str, now: float) -> None:
        """Nothing the runner held is left: every rule loads again, the one it was running after a pause."""
        logger.error("TA engine: %s (rule %s)", why, run.rule.id)
        for rule_id, other in list(self._runs.items()):
            fresh = RuleRun(rule=other.rule, state=OFF if other.rule.mode == OFF else LOADING)
            if other is run:
                fresh.state, fresh.detail, fresh.retry_at = ERROR, why, now + RETRY_SECONDS
            self._runs[rule_id] = fresh
        await self._say(run.rule.id, f"⚠️ WATA TA engine: {why}, while running {run.rule.name!r}.", now)

    # ── bars ──

    def anchor(self, now: float) -> int:
        """Where the bars the rules run on begin: history_days before the last restart_time."""
        if self._anchor is None:
            self._anchor = self.last_restart(now) - self._settings.history_days * 86_400
        return self._anchor

    async def _epic(self, epic: str, now: float) -> EpicRows | None:
        found = self._epics.get(epic)
        if found is not None:
            return found
        instrument = await self._source.instrument(epic)
        if instrument is None:
            return None
        found = EpicRows(instrument_id=instrument["id"], decimals=instrument["decimals"], read_until=self.anchor(now))
        self._epics[epic] = found
        return found

    async def _read_rows(self, epic: str, now: float) -> None:
        found = await self._epic(epic, now)
        covered = self._covered
        if found is None or covered is None or covered <= found.read_until:
            return
        found.rows.extend(await self._source.rows(found.instrument_id, found.read_until, covered))
        found.read_until = covered

    def _builder(self, run: RuleRun, epic: EpicRows):
        bars = run.rule.rule["bars"]
        if bars["type"] == "time":
            return TimeBars(TIMEFRAME_SECONDS[bars["timeframe"]], run.rule.rule["side"])
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
            run.builder, run.cursor, run.pending = self._builder(run, epic), 0, []
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

    async def _load(self, run: RuleRun, bars: list, now: float) -> None:
        run.state, run.detail = LOADING, None
        try:
            answer = await self._runner.request(self._load_request(run, bars))
        except RunnerFailed as e:
            await self._failed(run, f"The script does not run: {e}", now)
            return
        run.loaded, run.state = True, RUNNING
        run.detail = "; ".join(answer.get("warnings") or []) or None
        self._ran(run, answer, bars)
        logger.info("Rule %s (%s) loaded on %d bars in %d ms.", run.rule.id, run.rule.name, answer.get("bars", 0), answer.get("milliseconds", 0))

    async def _append(self, run: RuleRun, bars: list, now: float) -> None:
        try:
            answer = await self._runner.request({"op": "append", "rule": run.rule.id, "bars": bars})
        except RunnerFailed as e:
            await self._failed(run, f"The script failed on a new bar: {e}", now)
            return
        run.state, run.detail = RUNNING, run.detail if run.state == RUNNING else None
        self._ran(run, answer, bars)
        await self._signals(run, answer, bars, now)

    def _ran(self, run: RuleRun, answer: dict, bars: list) -> None:
        run.bars = answer.get("bars", run.bars)
        run.milliseconds = answer.get("milliseconds")
        run.last_bar_time = _moment(bars[-1][0] / 1000)

    async def _failed(self, run: RuleRun, why: str, now: float) -> None:
        logger.warning("Rule %s (%s): %s", run.rule.id, run.rule.name, why)
        run.state, run.detail, run.retry_at = ERROR, why, now + RETRY_SECONDS
        # Loaded again from its history when it is tried again.
        run.loaded, run.builder, run.pending = False, None, []
        await self._say(run.rule.id, f"⚠️ WATA TA engine, rule {run.rule.name!r}: {why}", now)

    # ── signals ──

    def _refusal(self, run: RuleRun, bar: list, now: float) -> str | None:
        rule = run.rule.rule
        if rule["indice"] in self._contested:
            return f"More than one rule is live on {rule['indice']}: none of them sends"
        age = now - bar[6] / 1000
        if age > self._settings.max_signal_age_seconds:
            return f"The bar closed {age:.0f} s ago, more than {self._settings.max_signal_age_seconds} s"
        if self._covered is None or now - self._covered > self._settings.stale_feed_seconds:
            return "The Capital.com feed is stale"
        if self._sender is None:
            return "No way to send signals"
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
            if candidate is None:
                continue
            run.last_alert_at = _moment(now)
            mode = LIVE if run.rule.mode == LIVE else SHADOW
            if candidate.action is None:
                outcome = "refused"
            else:
                outcome = "sending" if mode == LIVE else "shadow"
            signal_id = await self._journal.journal_signal(self._journal_row(run, candidate, bar, mode, outcome, now))
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
                refusal = f"Not a signal the web server takes: {e}"
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

    async def _say(self, about: str, message: str, now: float) -> None:
        if self._notify is None or now - self._notified.get(about, float("-inf")) < NOTIFY_EVERY_SECONDS:
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
        if self._runner_error:
            return "runner_down", self._runner_error
        if "*" in self._file_errors:
            return "error", self._file_errors["*"]
        active = [run for run in self._runs.values() if run.rule.mode != OFF]
        if not active:
            return "idle", "No rule is on"
        if self._covered is None or now - self._covered > self._settings.stale_feed_seconds:
            return "stale_feed", "The Capital.com feed is not recording: nothing is sent"
        failing = sum(1 for run in active if run.state == ERROR)
        detail = f"{len(active)} rule(s) on" + (f", {failing} failing" if failing else "")
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
                },
            })
        except Exception as e:
            logger.warning("The TA engine's state could not be written: %s", e)

    async def _write_states(self, now: float) -> None:
        state, detail = self.engine_state(now)
        await self._write_state(state, detail)
        try:
            await self._journal.write_rule_states(self.rule_states(now))
        except Exception as e:
            logger.warning("The rules' state could not be written: %s", e)

    # ── what a script asks for (request.security) ──

    async def answer_series(self, ask: dict) -> list:
        """The final bars of another series in [from, to], from the same feed and the same anchor."""
        ticker = parse_ticker(str(ask.get("ticker") or ""))
        if ticker is None or ticker.provider != "capital":
            raise ValueError(f"WATA has no live bars of {ask.get('ticker')!r}: only Capital.com's (CAPITAL_<EPIC>)")
        timeframe = str(ask.get("timeframe") or "")
        if ticker.range_ticks is None and timeframe not in TIMEFRAME_SECONDS:
            raise ValueError(f"No bars of {timeframe!r}: 1, 5, 15 or 60 minutes")
        now = self._clock()
        epic = await self._epic(ticker.key, now)
        if epic is None:
            raise ValueError(f"The Capital.com feed has not recorded {ticker.key}")
        await self._read_rows(ticker.key, now)
        if ticker.range_ticks is not None:
            decimals = epic.decimals if epic.decimals is not None else price_decimals([row[4] for row in epic.rows[:2000]])
            builder = RangeBars(ticker.range_ticks, decimals, ticker.side)
        else:
            builder = TimeBars(TIMEFRAME_SECONDS[timeframe], ticker.side)
        bars = builder.add(epic.rows, self._covered)
        start, end = ask.get("from"), ask.get("to")
        return [
            bar for bar in bars
            if (not isinstance(start, (int, float)) or bar[0] >= start) and (not isinstance(end, (int, float)) or bar[0] <= end)
        ]
