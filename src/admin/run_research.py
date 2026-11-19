"""
What the experiment journal adds to a run of one of its experiments: the figures that say how much the research that
made it can be believed, kept beside the run (``research.json``) and read by the bar (src/admin/run_verdict.py) on
the pages, in the MCP tools and in a conclusion.

- ``family``: the deflated Sharpe ratio of the run's daily results (src/backtest/robustness.py, unchanged), with the
  trials its family of ideas had been tried with until it. The best of many tries looks good by chance: a run that
  stands out among two hundred is no better than the luck of two hundred, unless it stands out by more. The spread of
  the trials' ratios is that of the family's own runs and sweep cells when ten are known, never under what one
  ratio measured on as many days spreads by chance (1 / (days - 1)); otherwise that.
- ``motivated``: the run's result without the days its experiment's idea came from (``motivated_by``). A hypothesis
  drawn from a few losing days, then tested on the same days, is fitted to them: what it makes on the others says
  whether it holds.

Everything it reads is fixed once the run is done (its curve, the journal until it, the family's runs before it), so
it is worked out once and kept; a newer ``RESEARCH_VERSION`` works it out again. Pure but for reading the runs'
files and writing the one it keeps.
"""

import os
from datetime import datetime, timezone
from statistics import pstdev

from src.admin.processes import read_json, write_json
from src.backtest.robustness import daily_sharpe, deflated_sharpe
from src.backtest.rules import compounded
from src.research.experiments import trials_until

RESEARCH_FILE = "research.json"
RESEARCH_VERSION = 1
# The family's runs and sweep cells whose ratios measure how far its trials spread: fewer, and the spread a single
# ratio has by chance is taken instead.
MIN_TRIAL_CURVES = 10


def day_percents(summary: dict | None) -> list[tuple[str, float]]:
    """A finished run's tested days and their results, in order (its summary's ``curve``)."""
    found = []
    for point in (summary or {}).get("curve") or []:
        if isinstance(point, dict) and isinstance(point.get("day"), str) and isinstance(point.get("percent"), (int, float)):
            found.append((point["day"], float(point["percent"])))
    return found


def _moment_of(run_id: str, experiment: dict, request: dict) -> str:
    """When the journal wrote the run down; for a run it never named (one made again), when it was asked."""
    for run in experiment.get("runs") or []:
        if run.get("run") == run_id:
            return run["at"]
    asked = request.get("asked_at")
    try:
        moment = datetime.fromisoformat(str(asked).replace("Z", "+00:00"))
        return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def trial_sharpes(experiments: list[dict], family: str, moment: str, curves_of) -> list[float]:
    """
    The daily Sharpe ratios of the family's runs and sweep cells written down until ``moment``: what its trials looked
    like. ``curves_of(run_id, kind)`` gives a run's daily results, a list per sweep cell.
    """
    found = []
    for experiment in experiments:
        if experiment.get("family") != family:
            continue
        for run in experiment.get("runs") or []:
            # A stress or a model check chooses nothing; a cell counted in its sweep already.
            if run.get("at", "") > moment or not run.get("trials"):
                continue
            for curve in curves_of(run["run"], run.get("kind")):
                ratio = daily_sharpe(curve)
                if ratio is not None:
                    found.append(ratio)
    return found


def family_figures(percents: list[float], trials: int, sharpes: list[float], family: str) -> dict:
    """The run's deflated Sharpe ratio with its family's trials (None under 20 days), and what it rests on."""
    days = len(percents)
    floor = 1 / (days - 1) if days > 1 else None
    spread = pstdev(sharpes) ** 2 if len(sharpes) > 1 else None
    own = (len(sharpes) >= MIN_TRIAL_CURVES and floor is not None and spread is not None and spread > floor)
    found = deflated_sharpe(percents, max(1, trials), trial_sharpes=sharpes if own else None)
    return {
        "name": family,
        "trials": max(1, trials),
        "days": days,
        "probability": found["probability"] if found else None,
        "sharpe_daily": found["sharpe_daily"] if found else None,
        "expected_max_sharpe_daily": found["expected_max_sharpe_daily"] if found else None,
        # Whose spread of ratios the luck of so many trials was measured with.
        "spread": "family" if own else "chance",
        "trial_curves": len(sharpes),
    }


def motivated_figures(points: list[tuple[str, float]], motivated_by: list[str]) -> dict | None:
    """The run's result with and without the days its idea came from; None when its experiment names none."""
    if not motivated_by:
        return None
    named = set(motivated_by)
    inside = [day for day, _ in points if day in named]
    others = [percent for day, percent in points if day not in named]
    return {
        "days": sorted(named),
        "in_run": len(inside),
        "with_percent": compounded([percent for _, percent in points]),
        "without_percent": compounded(others) if others else None,
        "days_left": len(others),
    }


def research_of(run_id: str, request: dict, summary: dict | None, experiments: list[dict], curves_of) -> dict | None:
    """
    What the journal adds to a finished run of one of its experiments (see the module's docstring); None for a run of
    no experiment, or one whose experiment is not in the journal.
    """
    experiment = next((entry for entry in experiments if entry["id"] == request.get("experiment")), None)
    if experiment is None or request.get("kind") != "run":
        return None
    points = day_percents(summary)
    moment = _moment_of(run_id, experiment, request)
    family = experiment["family"]
    trials = trials_until(experiments, family, moment)
    return {
        "version": RESEARCH_VERSION,
        "experiment": experiment["id"],
        "at": moment,
        "family": family_figures([percent for _, percent in points], trials,
                                 trial_sharpes(experiments, family, moment, curves_of), family),
        "motivated": motivated_figures(points, experiment.get("motivated_by") or []),
    }


def curves_reader(runs_root: str):
    """``curves_of`` over the runs' folders: a run's daily results, or each of a sweep's cells' (``sweep.json``)."""
    def curves_of(run_id: str, kind: str | None) -> list[list[float]]:
        folder = os.path.join(runs_root, run_id)
        if kind == "sweep":
            sweep = read_json(os.path.join(folder, "sweep.json")) or {}
            curves = []
            for cell in sweep.get("cells") or []:
                rows = (cell or {}).get("curve") or []
                curves.append([float(row[1]) for row in rows
                               if isinstance(row, (list, tuple)) and len(row) > 1 and isinstance(row[1], (int, float))])
            return curves
        return [[percent for _, percent in day_percents(read_json(os.path.join(folder, "summary.json")))]]
    return curves_of


def kept(runs_root: str, run_id: str) -> dict | None:
    """The figures kept beside a run, None while there are none or they were worked out by another version."""
    found = read_json(os.path.join(runs_root, run_id, RESEARCH_FILE))
    return found if isinstance(found, dict) and found.get("version") == RESEARCH_VERSION else None


def work_out(runs_root: str, run_id: str, journal) -> dict | None:
    """The figures of a finished run of an experiment worked out and kept beside it; None for any other run."""
    folder = os.path.join(runs_root, run_id)
    request = read_json(os.path.join(folder, "request.json")) or {}
    if not request.get("experiment"):
        return None
    summary = read_json(os.path.join(folder, "summary.json"))
    if summary is None:
        return None
    found = research_of(run_id, request, summary, journal.experiments(), curves_reader(runs_root))
    if found is not None:
        write_json(os.path.join(folder, RESEARCH_FILE), found)
    return found
