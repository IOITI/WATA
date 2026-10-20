"""
The experiment journal: what was tried, why, what came of it, and how many
configurations were tried in each family of ideas (``var/lib/lab/experiments.jsonl``).

The count matters: the best of two hundred configurations looks good by
chance alone. The overfitting figures (deflated Sharpe, src/backtest/robustness.py)
are worked out with the number of trials of the family, every run and every
combination of a sweep counting as one.

The lock box (``lockbox.json``): the last days, kept out of every run until
a candidate's final check. Using them is allowed, and written down.

One event per line, never rewritten: an experiment is opened, gets runs,
and is concluded.
"""

import json
import secrets
from datetime import date, datetime, timezone

from lab import paths

VERDICTS = ("kept", "rejected", "inconclusive")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _append(event: dict) -> dict:
    path = paths.experiments()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as out_file:
        out_file.write(json.dumps({**event, "at": _now()}) + "\n")
    return event


def _events() -> list[dict]:
    path = paths.experiments()
    if not path.is_file():
        return []
    events = []
    with open(path, encoding="utf-8") as in_file:
        for line in in_file:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def new(title: str, hypothesis: str, family: str) -> dict:
    if not title.strip() or not hypothesis.strip() or not family.strip():
        raise ValueError("An experiment has a title, a hypothesis and a family")
    return _append({
        "event": "opened", "experiment": secrets.token_hex(3), "title": title.strip(),
        "hypothesis": hypothesis.strip(), "family": family.strip(),
    })


def record_run(experiment_id: str, run_id: str, *, kind: str, trials: int, lockbox: bool = False, note: str = "") -> dict:
    get(experiment_id)
    return _append({
        "event": "run", "experiment": experiment_id, "run": run_id, "kind": kind, "trials": trials,
        "lockbox": lockbox, "note": note,
    })


def conclude(experiment_id: str, verdict: str, notes: str) -> dict:
    if verdict not in VERDICTS:
        raise ValueError(f"A verdict is one of {', '.join(VERDICTS)}")
    get(experiment_id)
    return _append({"event": "concluded", "experiment": experiment_id, "verdict": verdict, "notes": notes.strip()})


def experiments() -> list[dict]:
    """Every experiment, its runs and its conclusion, oldest first."""
    found: dict[str, dict] = {}
    for event in _events():
        key = event.get("experiment")
        if event["event"] == "opened":
            found[key] = {
                "id": key, "title": event["title"], "hypothesis": event["hypothesis"], "family": event["family"],
                "opened_at": event["at"], "runs": [], "trials": 0, "verdict": None, "notes": None, "concluded_at": None,
            }
        elif key in found and event["event"] == "run":
            found[key]["runs"].append({
                "run": event["run"], "kind": event["kind"], "trials": event["trials"], "lockbox": event.get("lockbox", False),
                "note": event.get("note", ""), "at": event["at"],
            })
            found[key]["trials"] += event["trials"]
        elif key in found and event["event"] == "concluded":
            found[key].update(verdict=event["verdict"], notes=event["notes"], concluded_at=event["at"])
    return list(found.values())


def get(experiment_id: str) -> dict:
    for experiment in experiments():
        if experiment["id"] == experiment_id:
            return experiment
    raise ValueError(f"No experiment {experiment_id!r}: `python -m lab exp list`")


def family_trials(family: str) -> int:
    """How many configurations the family of ideas was tried with, every experiment of it together."""
    return sum(experiment["trials"] for experiment in experiments() if experiment["family"] == family)


# ── the lock box ──


def _lockbox_path():
    return paths.lab_root() / "lockbox.json"


def lockbox_from() -> date | None:
    try:
        return date.fromisoformat(json.loads(_lockbox_path().read_text(encoding="utf-8"))["from"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def set_lockbox(first_day: date) -> None:
    _lockbox_path().parent.mkdir(parents=True, exist_ok=True)
    _lockbox_path().write_text(json.dumps({"from": first_day.isoformat(), "set_at": _now()}), encoding="utf-8")


def clear_lockbox() -> None:
    try:
        _lockbox_path().unlink()
    except OSError:
        pass
