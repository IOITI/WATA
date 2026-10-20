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

from datetime import date

from lab import paths
from src.research.experiments import VERDICTS, ExperimentJournal, UnknownExperiment  # noqa: F401 (VERDICTS: the lab's)


def _journal() -> ExperimentJournal:
    return ExperimentJournal(paths.experiments(), paths.lab_root() / "lockbox.json")


def new(title: str, hypothesis: str, family: str) -> dict:
    return _journal().new(title, hypothesis, family)


def record_run(experiment_id: str, run_id: str, *, kind: str, trials: int, lockbox: bool = False, note: str = "") -> dict:
    return _journal().record_run(experiment_id, run_id, kind=kind, trials=trials, lockbox=lockbox, note=note)


def conclude(experiment_id: str, verdict: str, notes: str) -> dict:
    return _journal().conclude(experiment_id, verdict, notes)


def experiments() -> list[dict]:
    """Every experiment, its runs and its conclusion, oldest first."""
    return _journal().experiments()


def get(experiment_id: str) -> dict:
    try:
        return _journal().get(experiment_id)
    except UnknownExperiment as e:
        raise UnknownExperiment(f"{e}: `python -m lab exp list`") from None


def family_trials(family: str) -> int:
    """How many configurations the family of ideas was tried with, every experiment of it together."""
    return _journal().family_trials(family)


# ── the lock box: every day from a first one, as the lab sets it ──


def lockbox_from() -> date | None:
    found = _journal().lockbox()
    return found["from"] if found else None


def set_lockbox(first_day: date) -> None:
    _journal().set_lockbox(first_day)


def clear_lockbox() -> None:
    _journal().clear_lockbox()
