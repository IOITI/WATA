"""
The experiment journal: what was tried, why, what came of it, and how many
configurations each family of ideas was tried with.

The count matters: the best of two hundred configurations looks good by
chance alone. The overfitting figures (deflated Sharpe, src/backtest/robustness.py)
are worked out with the number of trials of the family, every run and every
combination of a sweep counting as one, a stress sweep none (it chooses
nothing), the neighbours of a value one less than their count (the value
itself was tried already). Configurations tried outside the runs (a port of
a script screened on the Mac, by hand) are written down too, with what they
were (``record_trials``), and count as much.

One event per line, never rewritten: an experiment is opened, gets runs, and
is concluded. The lab keeps one on the Mac (``var/lib/lab/experiments.jsonl``),
the server another for Claude's work (``<backtests_path>/_research/``).

The lock box: days kept out of every run until a candidate's final check,
from a first day to a last one (or every day after the first, as the lab
sets it). A run on them is allowed, and written down. An agent may only make
it larger: the days it holds are never handed back to be fitted on, unless
the operator says so.

What an experiment may say beyond its words, all of it optional (a journal
written before reads as it did): who asked through what (``made_via``: an MCP
token's name, or ``agent:<conversation>`` for the admin's AI agent), the days
its idea came from (``motivated_by``: a result that needs them proves nothing),
the criteria it set itself before any run (``criteria``), and once concluded
the run it keeps (``candidate``) with how that run stood against the bar then
(``checks``). A kept candidate is then judged on the days after it, which no
run had seen (src/admin/forward_checks.py): its start and its end are written
here too (``forward`` events), so that the journal says what held.
"""

import difflib
import json
import os
import re
import secrets
import threading
import time
from datetime import date, datetime, timedelta, timezone

VERDICTS = ("kept", "rejected", "inconclusive")
MAX_TEXT = 2000
# The most configurations written down at once as tried elsewhere.
MAX_TRIALS_ELSEWHERE = 100_000
# The days an experiment's idea came from, at most: a hypothesis drawn from a few losing days names them.
MAX_MOTIVATING_DAYS = 20
# The criteria an experiment sets itself before any run, at most, each a figure bounded one way.
MAX_CRITERIA = 6
BOUNDS = ("at_least", "at_most")
CRITERION_FIGURE = re.compile(r"[a-z][a-z_]{0,39}")
# A forward check of a kept candidate is written when it starts and when it ends.
FORWARD_STATES = ("started", "ended")
# Two family names this alike are taken for one spelt twice (a family's trials correct its sweeps).
SIMILAR_FAMILY = 0.8


class JournalError(ValueError):
    """A request the journal refuses, said in a sentence."""


class UnknownExperiment(JournalError):
    pass


def _stamp(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _day(value) -> date | None:
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(value)


def _normal_family(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")


def close_family(family: str, families) -> str | None:
    """
    An existing family a new name looks like a misspelling of (the same once its case and separators are left
    aside, or nearly the same), None when it is one of them or like none. A family spelt twice would start its
    trials again at nothing, and its sweeps would be corrected for fewer than were tried.
    """
    known = [name for name in families if name]
    if family in known:
        return None
    wanted = _normal_family(family)
    best, score = None, 0.0
    for name in known:
        if _normal_family(name) == wanted:
            return name
        ratio = difflib.SequenceMatcher(None, wanted, _normal_family(name)).ratio()
        if ratio > score:
            best, score = name, ratio
    return best if score >= SIMILAR_FAMILY else None


def checked_days(days, today: date) -> list[str]:
    """The days an idea came from: distinct days of the past (today counts), in order, as ISO dates."""
    if days is None:
        return []
    if not isinstance(days, (list, tuple)):
        raise JournalError("The days an idea came from are a list of days")
    found = set()
    for value in days:
        try:
            day = value if isinstance(value, date) else date.fromisoformat(str(value))
        except ValueError:
            raise JournalError(f"{value!r} is not a day (YYYY-MM-DD)") from None
        if day > today:
            raise JournalError(f"{day.isoformat()} is still to come: an idea comes from days already seen")
        found.add(day)
    if len(found) > MAX_MOTIVATING_DAYS:
        raise JournalError(f"{MAX_MOTIVATING_DAYS} days at most: name the days the idea came from, not a period")
    return [day.isoformat() for day in sorted(found)]


def checked_criteria(criteria) -> list[dict]:
    """
    What an experiment sets itself before any run: each a figure, bounded ``at_least`` or ``at_most`` a value. Their
    shape only: which figures there are is the caller's (src/admin/run_verdict.py ``CRITERIA_FIGURES``).
    """
    if criteria is None:
        return []
    if not isinstance(criteria, (list, tuple)):
        raise JournalError("Criteria are a list of {figure, bound, value}")
    if len(criteria) > MAX_CRITERIA:
        raise JournalError(f"{MAX_CRITERIA} criteria at most")
    found = []
    for criterion in criteria:
        if not isinstance(criterion, dict) or set(criterion) - {"figure", "bound", "value"}:
            raise JournalError("A criterion is {figure, bound, value}")
        figure, bound, value = criterion.get("figure"), criterion.get("bound"), criterion.get("value")
        if not isinstance(figure, str) or not CRITERION_FIGURE.fullmatch(figure):
            raise JournalError(f"{figure!r} is not a figure's name")
        if bound not in BOUNDS:
            raise JournalError(f"A criterion's bound is {' or '.join(BOUNDS)}")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or abs(value) == float("inf"):
            raise JournalError(f"The value of {figure} is a number")
        found.append({"figure": figure, "bound": bound, "value": value})
    return found


# Where the server keeps its journal and lock box: beside the backtests, in this folder.
SERVER_FOLDER = "_research"


def server_journal(backtests_path: str) -> "ExperimentJournal":
    """The journal and the lock box the server keeps for the research of its agents, beside the backtests."""
    root = os.path.join(backtests_path, SERVER_FOLDER)
    return ExperimentJournal(os.path.join(root, "experiments.jsonl"), os.path.join(root, "lockbox.json"))


class ExperimentJournal:
    def __init__(self, journal_path, lockbox_path, clock=time.time):
        self.journal_path = str(journal_path)
        self.lockbox_path = str(lockbox_path)
        self._clock = clock
        self._lock = threading.Lock()

    # ── the journal ──

    def _append(self, event: dict) -> dict:
        os.makedirs(os.path.dirname(self.journal_path) or ".", exist_ok=True)
        with self._lock, open(self.journal_path, "a", encoding="utf-8") as out_file:
            out_file.write(json.dumps({**event, "at": _stamp(self._clock())}) + "\n")
        return event

    def _events(self) -> list[dict]:
        # Read under the lock the writes take: a line being appended by another thread could otherwise be read in
        # part, and the read fail (CI, 2026-11-19: one thread of eight stopped, 159 trials counted of 160). A last
        # line without its end is another process's still being written: left for the next read.
        try:
            with self._lock, open(self.journal_path, encoding="utf-8") as in_file:
                lines = in_file.readlines()
        except FileNotFoundError:
            return []
        if lines and not lines[-1].endswith("\n"):
            lines = lines[:-1]
        return [json.loads(line) for line in lines if line.strip()]

    def new(self, title: str, hypothesis: str, family: str, *, plan: str = "", made_by: str | None = None,
            made_via: str | None = None, motivated_by=None, criteria=None) -> dict:
        """
        An experiment opened: the event written, its id under ``experiment``. ``made_via``: who asked through what;
        ``motivated_by``: the days its idea came from; ``criteria``: what it sets itself before any run.
        """
        texts = [(title or "").strip(), (hypothesis or "").strip(), (family or "").strip()]
        if not all(texts):
            raise JournalError("An experiment has a title, a hypothesis and a family")
        if any(len(text) > MAX_TEXT for text in [*texts, plan or ""]):
            raise JournalError(f"An experiment's texts are {MAX_TEXT} characters at most")
        # A day ahead of UTC's: the trading timezone's day may have begun already.
        days = checked_days(motivated_by, datetime.fromtimestamp(self._clock(), timezone.utc).date() + timedelta(days=1))
        bounds = checked_criteria(criteria)
        event = {
            "event": "opened", "experiment": secrets.token_hex(3), "title": texts[0], "hypothesis": texts[1],
            "family": texts[2],
        }
        if plan:
            event["plan"] = plan.strip()
        if made_by is not None:
            event["made_by"] = made_by
        if made_via:
            event["made_via"] = str(made_via)[:120]
        if days:
            event["motivated_by"] = days
        if bounds:
            event["criteria"] = bounds
        return self._append(event)

    def record_run(self, experiment_id: str, run_id: str, *, kind: str, trials: int, lockbox: bool = False,
                   note: str = "") -> dict:
        self.get(experiment_id)
        return self._append({
            "event": "run", "experiment": experiment_id, "run": run_id, "kind": kind, "trials": trials,
            "lockbox": lockbox, "note": note,
        })

    def record_trials(self, experiment_id: str, trials: int, note: str) -> dict:
        """
        Configurations of an open experiment tried outside its runs: counted in
        the family's trials as a run's are, and listed apart with what they were.
        """
        if isinstance(trials, bool) or not isinstance(trials, int) or not 1 <= trials <= MAX_TRIALS_ELSEWHERE:
            raise JournalError(f"Trials made elsewhere are a whole number from 1 to {MAX_TRIALS_ELSEWHERE:,}")
        text = (note or "").strip()
        if not text or len(text) > MAX_TEXT:
            raise JournalError(f"Say what was tried, where and on which days, in {MAX_TEXT} characters at most")
        found = self.get(experiment_id)
        if found["verdict"] is not None:
            raise JournalError(
                f"Experiment {experiment_id} is concluded ({found['verdict']}): write them in an open experiment of "
                "its family"
            )
        return self._append({"event": "trials", "experiment": experiment_id, "trials": trials, "note": text})

    def conclude(self, experiment_id: str, verdict: str, notes: str, *, candidate: str | None = None,
                 checks: dict | None = None) -> dict:
        """
        An experiment concluded: kept, rejected or inconclusive, with what it rests on in ``notes``. ``candidate``:
        the run it keeps; ``checks``: how that run stood against the bar, and against its own criteria, then.
        """
        if verdict not in VERDICTS:
            raise JournalError(f"A verdict is one of {', '.join(VERDICTS)}")
        if len(notes or "") > MAX_TEXT:
            raise JournalError(f"A conclusion is {MAX_TEXT} characters at most")
        if checks is not None and not isinstance(checks, dict):
            raise JournalError("A conclusion's checks are a mapping")
        self.get(experiment_id)
        event = {"event": "concluded", "experiment": experiment_id, "verdict": verdict, "notes": (notes or "").strip()}
        if candidate:
            event["candidate"] = candidate
        if checks:
            event["checks"] = checks
        return self._append(event)

    def record_forward(self, experiment_id: str, check: str, state: str, **facts) -> dict:
        """
        A forward check of the experiment's candidate started or ended (src/admin/forward_checks.py), with what it
        found then (``facts``: its verdict, trades and result, why it ended): the journal's own record of what held.
        """
        if state not in FORWARD_STATES:
            raise JournalError(f"A forward check is {' or '.join(FORWARD_STATES)}")
        self.get(experiment_id)
        return self._append({**facts, "event": "forward", "experiment": experiment_id, "check": check, "state": state})

    def experiments(self) -> list[dict]:
        """Every experiment, its runs and its conclusion, oldest first."""
        found: dict[str, dict] = {}
        for event in self._events():
            key = event.get("experiment")
            if event["event"] == "opened":
                found[key] = {
                    "id": key, "title": event["title"], "hypothesis": event["hypothesis"], "family": event["family"],
                    "plan": event.get("plan"), "made_by": event.get("made_by"), "made_via": event.get("made_via"),
                    "motivated_by": list(event.get("motivated_by") or []), "criteria": list(event.get("criteria") or []),
                    "opened_at": event["at"], "runs": [], "elsewhere": [], "trials": 0, "verdict": None, "notes": None,
                    "concluded_at": None, "candidate": None, "checks": None, "forward": None,
                }
            elif key in found and event["event"] == "run":
                found[key]["runs"].append({
                    "run": event["run"], "kind": event["kind"], "trials": event["trials"],
                    "lockbox": event.get("lockbox", False), "note": event.get("note", ""), "at": event["at"],
                })
                found[key]["trials"] += event["trials"]
            elif key in found and event["event"] == "trials":
                found[key]["elsewhere"].append({"trials": event["trials"], "note": event["note"], "at": event["at"]})
                found[key]["trials"] += event["trials"]
            elif key in found and event["event"] == "concluded":
                found[key].update(verdict=event["verdict"], notes=event["notes"], concluded_at=event["at"],
                                  candidate=event.get("candidate"), checks=event.get("checks"))
            elif key in found and event["event"] == "forward":
                # The check's newest word: its start, then its end with what it found.
                facts = {name: value for name, value in event.items() if name not in ("event", "experiment", "at")}
                before = found[key]["forward"] or {}
                started = event["at"] if event.get("state") == "started" else before.get("started_at")
                found[key]["forward"] = {**facts, "started_at": started,
                                         "ended_at": event["at"] if event.get("state") == "ended" else None}
            # Any other event (written by a newer admin) is left aside: an older one still reads the journal.
        return list(found.values())

    def get(self, experiment_id: str) -> dict:
        for experiment in self.experiments():
            if experiment["id"] == experiment_id:
                return experiment
        raise UnknownExperiment(f"No experiment {experiment_id!r}")

    def family_trials(self, family: str) -> int:
        """How many configurations the family of ideas was tried with, every experiment of it together."""
        return sum(experiment["trials"] for experiment in self.experiments() if experiment["family"] == family)

    def families(self) -> dict[str, int]:
        """Every family of ideas with its trials, the most tried first."""
        counted: dict[str, int] = {}
        for experiment in self.experiments():
            counted[experiment["family"]] = counted.get(experiment["family"], 0) + experiment["trials"]
        return dict(sorted(counted.items(), key=lambda item: (-item[1], item[0])))

    # ── the lock box ──

    def lockbox(self) -> dict | None:
        """The days kept out: ``from`` and ``to`` (None: every day after ``from``), or None when there is no lock box."""
        try:
            with open(self.lockbox_path, encoding="utf-8") as in_file:
                found = json.load(in_file)
            first, last = _day(found["from"]), _day(found.get("to"))
        except (OSError, ValueError, KeyError, TypeError):
            return None
        return {"from": first, "to": last, "set_at": found.get("set_at"), "set_by": found.get("set_by")}

    def set_lockbox(self, first: date, last: date | None = None, *, set_by: str | None = None,
                    only_wider: bool = False) -> dict:
        """
        Keep the days from ``first`` to ``last`` out. ``only_wider``: the lock box
        may only grow (an agent's change), never give back a day it held.
        """
        if last is not None and last < first:
            raise JournalError("The lock box ends before it starts")
        current = self.lockbox()
        if only_wider and current is not None:
            if current["to"] is None:
                keeps_end = last is None
            else:
                keeps_end = last is None or last >= current["to"]
            if first > current["from"] or not keeps_end:
                ending = current["to"].isoformat() if current["to"] else "every day after"
                raise JournalError(
                    f"The lock box holds {current['from'].isoformat()} to {ending}: it may only grow, "
                    "the operator alone can move or open it"
                )
        record = {"from": first.isoformat(), "to": last.isoformat() if last else None, "set_at": _stamp(self._clock())}
        if set_by is not None:
            record["set_by"] = set_by
        os.makedirs(os.path.dirname(self.lockbox_path) or ".", exist_ok=True)
        temp_path = f"{self.lockbox_path}.part"
        with open(temp_path, "w", encoding="utf-8") as out_file:
            json.dump(record, out_file)
        os.replace(temp_path, self.lockbox_path)
        return self.lockbox()

    def clear_lockbox(self) -> None:
        try:
            os.remove(self.lockbox_path)
        except OSError:
            pass

    def in_lockbox(self, first: date, last: date) -> bool:
        """Whether a period holds a day of the lock box."""
        box = self.lockbox()
        if box is None:
            return False
        return last >= box["from"] and (box["to"] is None or first <= box["to"])


def trials_until(experiments: list[dict], family: str, moment: str) -> int:
    """
    The trials a family had been tried with by ``moment`` (a time of the journal, ``…Z``): its runs and its trials
    made elsewhere written down until then. What a run's figures are corrected with: the configurations tried
    before it and with it, not those tried after.
    """
    total = 0
    for experiment in experiments:
        if experiment["family"] != family:
            continue
        total += sum(run["trials"] for run in experiment["runs"] if run["at"] <= moment)
        total += sum(entry["trials"] for entry in experiment["elsewhere"] if entry["at"] <= moment)
    return total
