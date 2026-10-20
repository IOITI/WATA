"""
The experiment journal: what was tried, why, what came of it, and how many
configurations each family of ideas was tried with.

The count matters: the best of two hundred configurations looks good by
chance alone. The overfitting figures (deflated Sharpe, src/backtest/robustness.py)
are worked out with the number of trials of the family, every run and every
combination of a sweep counting as one, a stress sweep none (it chooses
nothing), the neighbours of a value one less than their count (the value
itself was tried already).

One event per line, never rewritten: an experiment is opened, gets runs, and
is concluded. The lab keeps one on the Mac (``var/lib/lab/experiments.jsonl``),
the server another for Claude's work (``<backtests_path>/_research/``).

The lock box: days kept out of every run until a candidate's final check,
from a first day to a last one (or every day after the first, as the lab
sets it). A run on them is allowed, and written down. An agent may only make
it larger: the days it holds are never handed back to be fitted on, unless
the operator says so.
"""

import json
import os
import secrets
import threading
import time
from datetime import date, datetime, timezone

VERDICTS = ("kept", "rejected", "inconclusive")
MAX_TEXT = 2000


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
        try:
            with open(self.journal_path, encoding="utf-8") as in_file:
                lines = in_file.readlines()
        except FileNotFoundError:
            return []
        return [json.loads(line) for line in lines if line.strip()]

    def new(self, title: str, hypothesis: str, family: str, *, plan: str = "", made_by: str | None = None) -> dict:
        """An experiment opened: the event written, its id under ``experiment``."""
        texts = [(title or "").strip(), (hypothesis or "").strip(), (family or "").strip()]
        if not all(texts):
            raise JournalError("An experiment has a title, a hypothesis and a family")
        if any(len(text) > MAX_TEXT for text in [*texts, plan or ""]):
            raise JournalError(f"An experiment's texts are {MAX_TEXT} characters at most")
        event = {
            "event": "opened", "experiment": secrets.token_hex(3), "title": texts[0], "hypothesis": texts[1],
            "family": texts[2],
        }
        if plan:
            event["plan"] = plan.strip()
        if made_by is not None:
            event["made_by"] = made_by
        return self._append(event)

    def record_run(self, experiment_id: str, run_id: str, *, kind: str, trials: int, lockbox: bool = False,
                   note: str = "") -> dict:
        self.get(experiment_id)
        return self._append({
            "event": "run", "experiment": experiment_id, "run": run_id, "kind": kind, "trials": trials,
            "lockbox": lockbox, "note": note,
        })

    def conclude(self, experiment_id: str, verdict: str, notes: str) -> dict:
        if verdict not in VERDICTS:
            raise JournalError(f"A verdict is one of {', '.join(VERDICTS)}")
        if len(notes or "") > MAX_TEXT:
            raise JournalError(f"A conclusion is {MAX_TEXT} characters at most")
        self.get(experiment_id)
        return self._append({"event": "concluded", "experiment": experiment_id, "verdict": verdict, "notes": (notes or "").strip()})

    def experiments(self) -> list[dict]:
        """Every experiment, its runs and its conclusion, oldest first."""
        found: dict[str, dict] = {}
        for event in self._events():
            key = event.get("experiment")
            if event["event"] == "opened":
                found[key] = {
                    "id": key, "title": event["title"], "hypothesis": event["hypothesis"], "family": event["family"],
                    "plan": event.get("plan"), "made_by": event.get("made_by"), "opened_at": event["at"], "runs": [],
                    "trials": 0, "verdict": None, "notes": None, "concluded_at": None,
                }
            elif key in found and event["event"] == "run":
                found[key]["runs"].append({
                    "run": event["run"], "kind": event["kind"], "trials": event["trials"],
                    "lockbox": event.get("lockbox", False), "note": event.get("note", ""), "at": event["at"],
                })
                found[key]["trials"] += event["trials"]
            elif key in found and event["event"] == "concluded":
                found[key].update(verdict=event["verdict"], notes=event["notes"], concluded_at=event["at"])
        return list(found.values())

    def get(self, experiment_id: str) -> dict:
        for experiment in self.experiments():
            if experiment["id"] == experiment_id:
                return experiment
        raise UnknownExperiment(f"No experiment {experiment_id!r}")

    def family_trials(self, family: str) -> int:
        """How many configurations the family of ideas was tried with, every experiment of it together."""
        return sum(experiment["trials"] for experiment in self.experiments() if experiment["family"] == family)

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
