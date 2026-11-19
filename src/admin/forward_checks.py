"""
Forward checks: a kept conclusion judged on the days after it, which no run of its family had seen.

An experiment of the journal concluded "kept" names its candidate run (src/research/experiments.py). From then on,
each evening once the market is closed by the live rules, the candidate is asked again (``BacktestService.extend``)
over the days from the first weekday after the latest day any run of its family had covered, to the day just over
(``debriefed_day``): its request, settings, script and libraries as they were, without random entries. Its trades
are judged by Edge Watch's model of the candidate (src/admin/edge_watch.py, unchanged): the expectation its own days
give, a sequential test against random entries with at most 5 % false alarms within its horizon, and how unusual its
result, drawdown and losing streak are. The verdict is that of Edge Watch: too_early, as_expected, watch, broken,
better_than_expected.

A check ends at its model's horizon, after ``MOST_TESTED_DAYS`` days, when it turns broken (its place is freed), when
its candidate is gone, or when the operator stops it; its start and its end are written in the journal (``forward``
events), which is what the track record reads (src/admin/track_record.py). ``MAX_ACTIVE`` run at once: a conclusion
past them is kept, its check refused in words. A candidate made again on newer code is followed (``newest_of``), its
model made again, its start kept. Lock-box days are never judged: a lock box that holds the next days ends the window
before them, an open-ended one makes the check wait.

The forward runs are runs made from the candidate (``forward_of``): of its configuration, never a nightly run's
template, never judged themselves, never made again by a remake; the one before is removed once the next is done,
unless something rests on it. One is asked at a time, only while no run at all waits or runs, after the nightly runs:
never in the way of the operator's work nor of the agents'. One Telegram line when a check turns watch or broken, and
one when it ends. Nothing here trades: no audit entry, as for the nightly runs.

What is kept: ``<backtests_path>/_research/forward/checks.json`` (written whole, under one lock) and the models,
``models/<run>-v<edge watch's version>.json`` (Edge Watch prunes its own folder of every model no reference names).
"""

import asyncio
import json
import logging
import os
import re
import secrets
import threading
import time
from datetime import date, datetime, timedelta, timezone

from src.admin import edge_watch as ew
from src.admin.backtest_nightly import read_sources
from src.admin.backtests import FORWARD, MAX_NAME_LENGTH
from src.admin.downloads import market_is_open
from src.admin.errors import DatabaseUnavailable, InvalidRequest, LineFull, NotPossible, UnknownRun
from src.admin.paper import debriefed_day
from src.admin.processes import CANCELLED, DONE, FAILED, INTERRUPTED, QUEUED, RUNNING, read_json, utc, write_json
from src.research.experiments import SERVER_FOLDER, JournalError, UnknownExperiment

logger = logging.getLogger(__name__)

FOLDER = "forward"
CHECKS_FILE = "checks.json"
CHECK_ID = re.compile(r"f[0-9a-f]{8}")
TICK_SECONDS = 300
# The checks that run at once, at most.
MAX_ACTIVE = 10
# A check ends once its forward run has tested this many days.
MOST_TESTED_DAYS = 60
# What each evening's forward run said, kept at most per check, newest first.
HISTORY_LINES = 120
ACTIVE, ENDED = "active", "ended"
# Why a check ended.
ENDED_HORIZON, ENDED_DAYS, ENDED_BROKEN, ENDED_GONE, ENDED_STOPPED = "horizon", "days", "broken", "gone", "stopped"
ENDED_SAID = {
    ENDED_HORIZON: "past the trades its candidate was drawn for",
    ENDED_DAYS: f"{MOST_TESTED_DAYS} days judged",
    ENDED_BROKEN: "judged broken",
    ENDED_GONE: "its candidate is gone",
    ENDED_STOPPED: "stopped by the operator",
}
# The verdicts said on Telegram when a check comes to one.
TOLD = (ew.WATCH, ew.BROKEN)


class UnknownCheck(LookupError):
    """No forward check has that id."""


def checked_id(check_id) -> str:
    if not isinstance(check_id, str) or not CHECK_ID.fullmatch(check_id):
        raise UnknownCheck(check_id)
    return check_id


def _day(text) -> date | None:
    try:
        return date.fromisoformat(str(text)[:10]) if text else None
    except ValueError:
        return None


def next_weekday(day: date) -> date:
    following = day + timedelta(days=1)
    while following.weekday() >= 5:
        following += timedelta(days=1)
    return following


def results_of(trades: list[dict]) -> tuple[list[float], list[dict]]:
    """A run's trades in the order they closed: their results (% of the turbo), and the path Edge Watch draws."""
    closed = []
    for trade in trades:
        at = (trade.get("exit") or {}).get("filled_at")
        if trade.get("performance_percent") is not None and at:
            closed.append((str(at), float(trade["performance_percent"])))
    closed.sort()
    level, path = 1.0, []
    for number, (at, result) in enumerate(closed, start=1):
        level *= max(0.0, 1 + result / 100)
        path.append({"n": number, "at": at, "result": result, "cumulative": round((level - 1) * 100, 2)})
    return [result for _, result in closed], path


def said_line(entry: dict, judgement: dict | None = None, *, ended: str | None = None) -> str | None:
    """What a check says on Telegram: its turn to watch or broken, or its end."""
    title = (entry.get("title") or entry.get("experiment") or "")[:80]
    judged = judgement or entry.get("judgement") or {}
    n, cumulative = judged.get("n") or 0, judged.get("cumulative") or 0.0
    if ended is not None:
        verdict = (judged.get("verdict") or "none").replace("_", " ")
        return (f"Forward check of '{title}' ended ({ENDED_SAID.get(ended, ended)}): {verdict} after {n} trade(s), "
                f"{cumulative:+.2f} % on days no run had seen.")
    verdict = judged.get("verdict")
    if verdict not in TOLD:
        return None
    reason = (judged.get("reasons") or [""])[0]
    return (f"Forward check of '{title}': {verdict.replace('_', ' ')} after {n} trade(s), {cumulative:+.2f} %"
            + (f" — {reason}." if reason else "."))


class ForwardChecks:
    def __init__(self, *, root: str, backtests, queries, config_path: str, nightly=None, notify=None, publish=None,
                 clock=time.time):
        self._folder = os.path.join(root, SERVER_FOLDER, FOLDER)
        self._backtests = backtests
        self._journal = backtests.journal
        self._queries = queries
        self._config_path = config_path
        # The configurations' nightly runs, which go first.
        self._nightly = nightly
        # async (text) -> said on Telegram; None: nothing is said.
        self._notify = notify
        # (event, **data) -> told to the pages; None: nothing is told.
        self._publish = publish
        self._clock = clock
        self._lock = threading.Lock()
        self._models: dict[str, dict] = {}
        self._asking: set[str] = set()
        self._wakeup = asyncio.Event()
        self._telling: set[asyncio.Task] = set()
        backtests.on_finished(self._finished)

    # ── what it keeps ──

    def _path(self) -> str:
        return os.path.join(self._folder, CHECKS_FILE)

    def _read(self) -> dict:
        found = read_json(self._path())
        return found if isinstance(found, dict) and isinstance(found.get("checks"), dict) else {"checks": {}}

    def _change(self, check_id: str, change) -> dict:
        """One check changed under the lock, the file written whole; the check as it now is."""
        with self._lock:
            kept = self._read()
            entry = kept["checks"].get(check_id)
            if entry is None:
                raise UnknownCheck(check_id)
            change(entry)
            os.makedirs(self._folder, exist_ok=True)
            write_json(self._path(), kept)
            return dict(entry)

    def _config(self) -> dict:
        with open(self._config_path, encoding="utf-8") as config_file:
            return json.load(config_file)

    # ── the model of a candidate ──

    def model(self, run_id: str) -> dict | None:
        """A candidate's model (Edge Watch's): in memory, else as kept, else made and kept. None for a run not done."""
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
            found = ew.build_model(ew.day_blocks(days, trades), baseline, run_id=run_id)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            write_json(path, found)
        self._models[run_id] = found
        return found

    # ── starting one ──

    def _belongs(self, experiment: dict, run_id: str) -> bool:
        """Whether a run is one of the experiment's: written down in it, or asked for it (a sweep's cell made a run)."""
        if any(run.get("run") == run_id for run in experiment.get("runs") or []):
            return True
        return self._backtests.request_of(run_id).get("experiment") == experiment["id"]

    def _family_last_day(self, experiment: dict, experiments: list[dict]) -> date | None:
        """The latest day any run of the experiment's family had covered: the days after it no run had seen."""
        latest = None
        for other in experiments:
            if other.get("family") != experiment.get("family"):
                continue
            for run in other.get("runs") or []:
                try:
                    last = _day(self._backtests.request_of(run["run"]).get("to"))
                except UnknownRun:
                    continue
                if last is not None and (latest is None or last > latest):
                    latest = last
        return latest

    def candidate_refusal(self, run_id: str) -> str | None:
        """Why a run cannot be a forward check's candidate; None when it can."""
        try:
            request = self._backtests.request_of(run_id)
            state = self._backtests.state(run_id)["state"]
        except UnknownRun:
            return f"No backtest {run_id}"
        if request.get("kind") != "run" or request.get("paper") or request.get("forward_of"):
            return "A forward check judges a finished run of one combination: a sweep's cell made a run of its own first"
        if any(request.get(key) for key in ("stress_of", "noise_of", "model_check_of")):
            return "A stress or a model check judges another run: name the run it was made from"
        if state != DONE:
            return "Only a finished run can be judged on the days after it"
        return None

    def start(self, experiment_id: str, candidate: str, *, by: str | None = None) -> dict:
        """
        A forward check of a concluded experiment's candidate started: refused in words (NotPossible) when ten run
        already, the experiment has one under way, the candidate is not one of its finished runs of one combination,
        its model cannot be drawn from, or it makes no more than random entries.
        """
        try:
            experiment = self._journal.get(experiment_id)
        except UnknownExperiment:
            raise NotPossible(f"No experiment {experiment_id}") from None
        if experiment.get("verdict") is None:
            raise NotPossible("Only a concluded experiment's candidate is judged on the days after it")
        refusal = self.candidate_refusal(candidate)
        if refusal:
            raise NotPossible(refusal)
        if not self._belongs(experiment, candidate):
            raise NotPossible(f"Backtest {candidate} is not one of experiment {experiment_id}'s runs")
        kept = self._read()["checks"]
        if any(entry["experiment"] == experiment_id and entry["state"] == ACTIVE for entry in kept.values()):
            raise NotPossible(f"Experiment {experiment_id}'s candidate is being judged already")
        if sum(1 for entry in kept.values() if entry["state"] == ACTIVE) >= MAX_ACTIVE:
            raise NotPossible(f"{MAX_ACTIVE} forward checks run already: one must end, or be stopped, first")
        model = self.model(candidate)
        if model is None:
            raise NotPossible("Its candidate's results cannot be read")
        if not model.get("usable"):
            raise NotPossible(f"Its candidate cannot be judged on later days: {model.get('why_not')}")
        if not model.get("test"):
            reference = model.get("reference") or {}
            raise NotPossible(
                f"Its candidate makes {reference.get('mean_clipped', 0):+.2f} % a trade, no more than random entries "
                f"({reference.get('h1_mean', 0):+.2f} %): later days have no edge to judge"
            )
        family_last = self._family_last_day(experiment, self._journal.experiments())
        candidate_last = _day((self._backtests.reference_of(candidate) or {}).get("last_day"))
        seen = max(day for day in (family_last, candidate_last) if day is not None) if (family_last or candidate_last) else None
        start = next_weekday(seen) if seen else next_weekday(_day(utc(self._clock())) or date.today())
        check_id = "f" + secrets.token_hex(4)
        entry = {
            "id": check_id, "experiment": experiment_id, "title": experiment.get("title"), "family": experiment.get("family"),
            "made_via": experiment.get("made_via"), "candidate": candidate, "first_candidate": candidate,
            "start": start.isoformat(), "state": ACTIVE, "started_at": utc(self._clock()), "by": by,
            "ended_at": None, "why": None, "last": None, "judgement": None, "path": [], "days": [], "said": None,
            "history": [],
        }
        with self._lock:
            kept = self._read()
            kept["checks"][check_id] = entry
            os.makedirs(self._folder, exist_ok=True)
            write_json(self._path(), kept)
        try:
            self._journal.record_forward(experiment_id, check_id, "started", candidate=candidate, start=start.isoformat())
        except JournalError as e:
            logger.warning(f"The start of forward check {check_id} could not be written in the journal: {e}")
        self._announce(entry)
        self.wake()
        return dict(entry)

    def stop(self, check_id: str) -> dict:
        """A check ended by the operator: its run under way, if any, goes on and is let be."""
        checked_id(check_id)
        entry = self._read()["checks"].get(check_id)
        if entry is None:
            raise UnknownCheck(check_id)
        if entry["state"] != ACTIVE:
            raise NotPossible("This forward check has ended already")
        return self._end(check_id, ENDED_STOPPED)

    def _end(self, check_id: str, why: str) -> dict:
        def change(entry: dict) -> None:
            entry.update(state=ENDED, ended_at=utc(self._clock()), why=why)

        entry = self._change(check_id, change)
        judged = entry.get("judgement") or {}
        try:
            self._journal.record_forward(
                entry["experiment"], check_id, "ended", why=why, candidate=entry["candidate"],
                verdict=judged.get("verdict"), trades=judged.get("n") or 0, cumulative=judged.get("cumulative"),
                days_tested=len(entry.get("days") or []),
            )
        except JournalError as e:
            logger.warning(f"The end of forward check {check_id} could not be written in the journal: {e}")
        self._tell(said_line(entry, ended=why))
        self._announce(entry)
        return entry

    # ── asking its run each evening ──

    def _window(self, entry: dict, day: date) -> tuple[date, date] | None:
        """The days a check's run covers tonight, lock-box days left out; None while there is none to judge."""
        first, last = _day(entry["start"]), day
        box = self._journal.lockbox()
        if box is not None and last >= box["from"] and (box["to"] is None or first <= box["to"]):
            # The lock box holds days of the window: judged up to the day before it only.
            last = min(last, box["from"] - timedelta(days=1))
        if first is None or last < first:
            return None
        return first, last

    def _state_of(self, run_id: str | None) -> str | None:
        if not run_id:
            return None
        try:
            return self._backtests.state(run_id)["state"]
        except UnknownRun:
            return None

    def due(self, now: float | None = None) -> list[dict]:
        """
        The active checks whose run is to be asked tonight: their window holds a day, no run of theirs covers it, and
        none was asked for it already (one cut by a restart of the admin, or removed, is asked again). The oldest
        asked first.
        """
        day = debriefed_day(self._config(), self._clock() if now is None else now)
        found = []
        for entry in self._read()["checks"].values():
            if entry["state"] != ACTIVE or entry["id"] in self._asking:
                continue
            window = self._window(entry, day)
            if window is None:
                continue
            last = entry.get("last") or {}
            if last.get("to") == window[1].isoformat():
                state = self._state_of(last.get("run"))
                if last.get("run") is None or state in (QUEUED, RUNNING, FAILED, CANCELLED, DONE):
                    # Asked for these days already (refused, waiting, ended): the next day's, or never.
                    continue
            found.append((last.get("asked_at") or "", entry))
        return [entry for _, entry in sorted(found, key=lambda item: item[0])]

    async def _ask(self, entry: dict, now: float) -> dict | None:
        """A check's run asked over its window; a refusal kept for that day. LineFull and DatabaseUnavailable raised."""
        window = self._window(entry, debriefed_day(self._config(), now))
        if window is None:
            return None
        first, last = window
        candidate = self._follow(entry)
        request = self._backtests.request_of(candidate)
        self._asking.add(entry["id"])
        try:
            first, journal, positions, raised = await read_sources(self._queries, request.get("source") or {}, first, last,
                                                                   self._config())
            name = f"Forward · {entry.get('title') or entry['experiment']}"[:MAX_NAME_LENGTH]
            made = self._backtests.extend(candidate, first, last, name=name, forward={"check": entry["id"]},
                                          journal=journal, positions=positions, raised=raised)
        except LineFull:
            raise
        except (InvalidRequest, NotPossible, UnknownRun) as e:
            self._keep_last(entry["id"], last, None, error=str(e))
            logger.info(f"Forward check {entry['id']} is not asked for {last.isoformat()}: {e}")
            return None
        finally:
            self._asking.discard(entry["id"])
        self._keep_last(entry["id"], last, made["id"])
        return made

    def _follow(self, entry: dict) -> str:
        """The candidate a check judges now: the run that made it again on newer code, once done, else itself."""
        newest = self._backtests.newest_of(entry["candidate"])
        if newest != entry["candidate"] and self._state_of(newest) == DONE:
            self._change(entry["id"], lambda found: found.update(candidate=newest))
            return newest
        return entry["candidate"]

    def _keep_last(self, check_id: str, to: date, run_id: str | None, *, error: str | None = None) -> None:
        last = {"to": to.isoformat(), "run": run_id, "asked_at": utc(self._clock()), "error": error,
                "settled": run_id is None}
        self._change(check_id, lambda entry: entry.update(last=last))

    async def catch_up(self, now: float | None = None) -> int:
        """
        What ended settled; then, the market closed, nothing at all in the backtests' line and no nightly run due, one
        check's run asked when one is due. How many were asked (0 or 1).
        """
        now = self._clock() if now is None else now
        self.settle()
        if self._asking or market_is_open(self._config(), now) or not self._backtests.idle():
            return 0
        if self._nightly is not None and self._nightly.due(now):
            return 0
        for entry in self.due(now):
            try:
                made = await self._ask(entry, now)
            except LineFull as e:
                logger.info(f"The forward checks wait: {e}")
                return 0
            except DatabaseUnavailable:
                logger.info("The forward checks wait for the database")
                return 0
            if made is not None:
                return 1
        return 0

    # ── once its run ended ──

    def _finished(self, run_id: str, state: str, request: dict) -> None:
        """Told by the service once a run ends, on the event loop: a forward check's run is settled at once."""
        if isinstance(request.get(FORWARD), dict):
            try:
                self.settle()
            finally:
                self.wake()

    def settle(self) -> int:
        """
        Each active check whose last run ended and was not settled yet: done, judged, its line kept and the run before
        it removed (unless kept); a check whose candidate is gone ended. Idempotent. How many were settled.
        """
        settled = 0
        for entry in list(self._read()["checks"].values()):
            if entry["state"] != ACTIVE:
                continue
            if not self._backtests.exists(entry["candidate"]):
                self._end(entry["id"], ENDED_GONE)
                continue
            last = entry.get("last") or {}
            if not last.get("run") or last.get("settled"):
                continue
            state = self._state_of(last["run"])
            if state in (QUEUED, RUNNING):
                continue
            if state == INTERRUPTED or state is None:
                # Cut by a restart of the admin, or removed: asked again (``due``).
                self._change(entry["id"], lambda found: found.update(last={**(found.get("last") or {}), "run": None,
                                                                             "to": None, "settled": True}))
                continue
            try:
                self._settle(entry, last, state)
            except Exception as e:
                logger.warning(f"Forward check {entry['id']} could not be settled: {e}", exc_info=True)
                continue
            settled += 1
        return settled

    def _settle(self, entry: dict, last: dict, state: str) -> None:
        if state != DONE:
            self._change(entry["id"], lambda found: (found.get("last") or {}).update(settled=True))
            return
        run_id = last["run"]
        results, path = results_of(self._backtests.trades(run_id))
        days = [[row.get("day"), row.get("day_percent"), row.get("trades")]
                for row in self._backtests.days(run_id) if row.get("status") == "tested"]
        model = self.model(entry["candidate"])
        judgement = ew.judge(model, results)
        line = {
            "to": last.get("to"), "run": run_id, "n": judgement["n"], "verdict": judgement["verdict"],
            "cumulative": judgement["cumulative"], "rank": judgement["rank"], "cusum": judgement["cusum"],
            "h": judgement["h"], "days_tested": len(days), "at": utc(self._clock()),
        }
        before = entry.get("last_run_settled")

        def change(found: dict) -> None:
            found.update(judgement=judgement, path=path, days=days, last_run_settled=run_id)
            found["history"] = [line, *(found.get("history") or [])][:HISTORY_LINES]
            (found.get("last") or {}).update(settled=True)

        entry = self._change(entry["id"], change)
        if before and before != run_id:
            self._remove(before)
        verdict = judgement["verdict"]
        if verdict in TOLD and entry.get("said") != verdict:
            self._tell(said_line(entry, judgement))
            self._change(entry["id"], lambda found: found.update(said=verdict))
        elif verdict not in TOLD and entry.get("said") is not None:
            self._change(entry["id"], lambda found: found.update(said=None))
        self._announce(entry)
        if verdict == ew.BROKEN:
            self._end(entry["id"], ENDED_BROKEN)
        elif judgement.get("past_horizon"):
            self._end(entry["id"], ENDED_HORIZON)
        elif len(days) >= MOST_TESTED_DAYS:
            self._end(entry["id"], ENDED_DAYS)

    def _remove(self, run_id: str) -> None:
        """A check's run before the newest let go, unless something rests on it (a favourite, a reference)."""
        try:
            listed = self._backtests.listed(run_id)
            if listed.get("favourite") or run_id in set(self._backtests.references().values()):
                return
            self._backtests.delete(run_id)
        except (UnknownRun, NotPossible):
            pass

    # ── reading them ──

    def checks(self) -> list[dict]:
        """Every check, the active first, the newest first, without its path and history."""
        found = [
            {key: value for key, value in entry.items() if key not in ("path", "history", "days")}
            | {"days_tested": len(entry.get("days") or [])}
            for entry in self._read()["checks"].values()
        ]
        newest_first = sorted(found, key=lambda entry: entry.get("started_at") or "", reverse=True)
        return sorted(newest_first, key=lambda entry: entry["state"] != ACTIVE)

    def check(self, check_id: str) -> dict:
        """One check whole: its judgement with its path, the cone of its candidate's model, its history."""
        checked_id(check_id)
        entry = self._read()["checks"].get(check_id)
        if entry is None:
            raise UnknownCheck(check_id)
        model = self._models.get(entry["candidate"]) or read_json(
            os.path.join(self._folder, "models", f"{entry['candidate']}-v{ew.VERSION}.json"))
        return {
            **entry,
            "model": {key: (model or {}).get(key) for key in ("usable", "why_not", "reference", "test", "horizon")}
            if model else None,
            "cone": (model or {}).get("cone"),
        }

    def of_experiments(self) -> dict[str, dict]:
        """The newest check of each experiment, in short: what the journal's pages and tools show beside it."""
        found: dict[str, dict] = {}
        for entry in self.checks():
            known = found.get(entry["experiment"])
            if known is None or (entry.get("started_at") or "") > (known.get("started_at") or ""):
                judged = entry.get("judgement") or {}
                found[entry["experiment"]] = {
                    "id": entry["id"], "state": entry["state"], "why": entry.get("why"), "candidate": entry["candidate"],
                    "start": entry["start"], "verdict": judged.get("verdict"), "trades": judged.get("n") or 0,
                    "cumulative": judged.get("cumulative"), "days_tested": entry.get("days_tested", 0),
                }
        return found

    def active(self) -> int:
        return sum(1 for entry in self._read()["checks"].values() if entry["state"] == ACTIVE)

    # ── telling ──

    def _announce(self, entry: dict) -> None:
        if self._publish is None:
            return
        try:
            self._publish("forward_check_changed", check=entry["id"], experiment=entry["experiment"],
                          state=entry["state"], verdict=(entry.get("judgement") or {}).get("verdict"))
        except Exception as e:
            logger.warning(f"Forward check {entry['id']} could not be told to the pages: {e}")

    def _tell(self, text: str | None) -> None:
        if self._notify is None or not text:
            return
        try:
            task = asyncio.get_running_loop().create_task(self._said(text))
        except RuntimeError:
            return
        self._telling.add(task)
        task.add_done_callback(self._telling.discard)

    async def _said(self, text: str) -> None:
        try:
            await self._notify(text)
        except Exception as e:
            logger.warning(f"A forward check's line could not be said on Telegram: {e}")

    # ── the task ──

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
                logger.warning(f"The forward checks could not catch up: {e}")
            try:
                await asyncio.wait_for(self._wakeup.wait(), TICK_SECONDS)
            except asyncio.TimeoutError:
                pass
