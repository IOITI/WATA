"""
What a backtest Claude asks for must stay within, checked before anything is
queued: an open experiment of the journal, Claude's share of the line, the
day's budget of runs, the days a request simulates (its trading days, which
weekends and the closed dates are not), the disk left free, and the lock box.
"""

from datetime import date, datetime, timedelta, timezone

from src.admin.mcp_server.budget import Refused
from src.admin.mcp_server.facade import CLAUDE

SOURCE_KINDS = ("pine", "journal")


def open_experiment(journal, experiment_id: str) -> dict:
    found = journal.get(experiment_id)
    if found["verdict"] is not None:
        raise Refused(f"Experiment {experiment_id} is concluded ({found['verdict']}): open a new one with exp_new")
    return found


def period_of(body: dict) -> tuple[date, date]:
    try:
        return date.fromisoformat(body.get("from")), date.fromisoformat(body.get("to"))
    except (TypeError, ValueError):
        raise Refused('The request names its period: "from" and "to", YYYY-MM-DD') from None


def trading_days(research, first: date, last: date) -> int:
    """
    The days of a period a run may trade: its weekdays, less the closed dates
    of the live configuration (DD/MM/YYYY, as the trader reads them).
    """
    closed = set(research.service().live_settings().closed_dates)
    found = (first + timedelta(days=offset) for offset in range((last - first).days + 1))
    return max(1, sum(1 for day in found if day.weekday() < 5 and day.strftime("%d/%m/%Y") not in closed))


def cost_of(body: dict, days: int) -> int:
    """The days a request simulates: each day once per combination of its sweep, or once plus its random repetitions."""
    sweep = body.get("sweep")
    if sweep:
        cells = 1
        for axis in (sweep.get("axes") or []) if isinstance(sweep, dict) else []:
            cells *= max(1, len(axis.get("values") or []) if isinstance(axis, dict) else 1)
        return days * cells
    baseline = body.get("baseline") if isinstance(body.get("baseline"), dict) else {}
    repetitions = baseline.get("repetitions", 100)
    return days * (1 + (repetitions if isinstance(repetitions, int) and not isinstance(repetitions, bool) else 100))


def runs_today(service, now: float) -> int:
    """Claude's runs asked for today (UTC): a run's id begins with the day it was asked."""
    today = datetime.fromtimestamp(now, timezone.utc).strftime("%Y%m%d")
    return sum(1 for run_id in service.run_ids(made_by=CLAUDE) if run_id.startswith(today))


def check_room(research, cost: int) -> None:
    limits = research.limits
    service = research.service()
    waiting = service.waiting_of(CLAUDE)
    if waiting >= limits.max_waiting_runs:
        raise Refused(
            f"{waiting} of Claude's runs wait already (at most {limits.max_waiting_runs}): backtest_wait for them, "
            "or backtest_remove one"
        )
    done_today = runs_today(service, research.clock())
    if done_today >= limits.runs_per_day:
        raise Refused(f"Claude asked for {done_today} runs today (UTC), the day's budget (admin.mcp.runs_per_day)")
    if cost > limits.max_day_simulations:
        raise Refused(
            f"This request simulates {cost:,} days (its trading days × combinations, or × 1 + random repetitions), "
            f"over the {limits.max_day_simulations:,} a request may: a shorter period, fewer values, or fewer repetitions"
        )
    free = research.free_disk_gb()
    if free is not None and free < limits.min_free_disk_gb:
        raise Refused(f"The server's disk has {free:.1f} GB free, under the {limits.min_free_disk_gb} GB kept free")


def lockbox_of(journal, first: date, last: date, final: bool) -> dict | None:
    """What a run says of the lock box: nothing outside it; on it, only as a candidate's final check."""
    if not journal.in_lockbox(first, last):
        return None
    box = journal.lockbox()
    ending = box["to"].isoformat() if box["to"] else None
    if not final:
        raise Refused(
            f"The period holds days of the lock box (from {box['from'].isoformat()}"
            f"{' to ' + ending if ending else ''}): end it before, or pass lockbox_final for a candidate's final "
            "check, which the journal notes"
        )
    return {"final": True, "from": box["from"].isoformat(), "to": ending}


def check_source(research, body: dict) -> None:
    source = body.get("source")
    if not isinstance(source, dict) or source.get("kind") not in SOURCE_KINDS:
        raise Refused("Claude's runs replay a Pine script (source.kind pine) or the signals WATA received (journal)")
    if body.get("paper") is not None:
        raise Refused("Paper runs are the admin's own")
    from_rule = source.get("from_rule")
    if from_rule:
        rule_id = from_rule.get("id") if isinstance(from_rule, dict) else None
        rule = research.rules.rule(rule_id)
        if rule.get("made_by") != CLAUDE:
            raise Refused("A run is made from one of Claude's own rules only")
