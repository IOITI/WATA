"""
What holds a restart of a WATA service back, for the admin's restarts
(src/admin/service_restart.py) and for a deploy (src/release/host.py) alike:
one set of rules, read on facts each gathers its own way (the admin through
its context, the server through Docker, psql and the files).

Standard library only: the server's python3 imports it from a release's
folder, beside nothing WATA installs.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# What trades, or decides when to: not restarted with a position open or the market open, unless forced.
TRADING_PATH = frozenset({"trader", "position_monitor", "watchlist_manager", "web_server", "scheduler"})
# Restarted in this order: what the others call first, the trading pair after, the admin last.
ORDER = (
    "web_server", "telegram", "watchlist_manager", "price_recorder", "capital_feed", "ta_engine",
    "scheduler", "position_monitor", "trader", "admin",
)
# A deploy also holds these back while the market is open: the bars they miss are never fetched again, and
# the TA engine's live rules ride on the Capital.com feed's (the operator's choice, 2026-10-29). A restart from
# the admin keeps its own rule and lets them go.
RECORDERS = frozenset({"price_recorder", "capital_feed"})
# How long a scheduled job keeps the scheduler from being restarted, before and after it: its
# schedule is worked out when it starts, so a restart just past a job misses that day's.
SCHEDULER_JOB_MARGIN_SECONDS = 120


@dataclass(frozen=True)
class Situation:
    """What the guards read, gathered at the moment of the request and again before each step of a plan."""

    # None: the database could not say.
    positions_open: int | None = 0
    # What the position monitor's heartbeat says Saxo lists: a position bought and not in a row yet counts.
    positions_listed: int = 0
    close_intents: tuple[str, ...] = ()
    market_open: bool = False
    live_rules: int = 0
    backtests_running: int = 0
    study_running: bool = False
    # The scheduler's job due within its margin, said ("the close of the day at 15:55, America/New_York").
    scheduler_job_near: str | None = None
    # The AI agent's requests queued or worked on: taken up again after the restart, a study or a wait cut short.
    agent_requests: int = 0


def count(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def refusal(service: str, situation: Situation) -> str | None:
    """Why ``service`` should not be restarted now, or None. A forced restart goes ahead, said in the audit."""
    reasons = []
    if service in TRADING_PATH:
        if situation.positions_open is None:
            reasons.append("the open positions cannot be read")
        held = max(situation.positions_open or 0, situation.positions_listed)
        if held:
            reasons.append(f"{count(held, 'position')} open")
        if situation.market_open:
            reasons.append("the market is open by WATA's hours")
    if service == "position_monitor" and situation.close_intents:
        reasons.append(f"{count(len(situation.close_intents), 'sale')} still being tried "
                       f"(position {', '.join(situation.close_intents)}), which the restart drops")
    if service == "scheduler" and situation.scheduler_job_near:
        reasons.append(f"{situation.scheduler_job_near} is within {SCHEDULER_JOB_MARGIN_SECONDS // 60} minutes")
    if service == "ta_engine" and situation.live_rules and situation.market_open:
        reasons.append(f"{count(situation.live_rules, 'live rule')} with the market open: a signal could be missed")
    if service == "admin":
        if situation.backtests_running:
            reasons.append(f"{count(situation.backtests_running, 'backtest')} running")
        if situation.study_running:
            reasons.append("one of Claude's studies is running")
        if situation.agent_requests:
            reasons.append(f"the AI agent is working on {count(situation.agent_requests, 'request')}")
    return "; ".join(reasons) or None


def deploy_refusal(service: str, situation: Situation) -> str | None:
    """Why a deploy should not recreate ``service`` now: a restart's guards, and the recorders' bars."""
    reasons = [found] if (found := refusal(service, situation)) else []
    if service in RECORDERS and situation.market_open:
        reasons.append("the market is open by WATA's hours, and the bars it would miss are never fetched again")
    return "; ".join(reasons) or None


def ordered(services) -> list[str]:
    """``services`` in the order a plan restarts them, each once; anything else is refused."""
    wanted = set(services)
    unknown = wanted - set(ORDER)
    if unknown:
        raise ValueError(f"Not WATA services: {', '.join(sorted(unknown))}")
    return [service for service in ORDER if service in wanted]


def moment(value) -> datetime | None:
    """An instant as Docker, the journal or the database gives it (Docker's has nanoseconds), or None."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value or value.startswith("0001-"):
        return None
    text = value.replace("Z", "+00:00")
    if "." in text:
        head, _, rest = text.partition(".")
        digits = "".join(character for character in rest if character.isdigit())
        text = f"{head}.{digits[:6]}{rest[len(digits):]}"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def rule_config(config: dict, rule_type: str) -> dict:
    for rule in (config.get("trade") or {}).get("rules") or []:
        if isinstance(rule, dict) and rule.get("rule_type") == rule_type:
            return rule.get("rule_config") or {}
    return {}


def trading_timezone(config: dict) -> str:
    """The trading timezone (``trade.config.general.timezone``): the one WATA's days are counted in."""
    return ((config.get("trade") or {}).get("config") or {}).get("general", {}).get("timezone", "Europe/Paris")


def trading_hours(config: dict) -> tuple[str, int, int]:
    """
    The hours WATA trades in, as the live rules say (``market_hours``), and the
    timezone they are written in: theirs (``market_hours.timezone``, New York's
    say), the trading one unless they give one. Days are counted in the
    trading timezone (``trading_timezone``).
    """
    for rule in (config.get("trade") or {}).get("rules") or []:
        if isinstance(rule, dict) and rule.get("rule_type") == "market_hours":
            hours = rule.get("rule_config") or {}
            zone = hours.get("timezone") or trading_timezone(config)
            return zone, int(hours["trading_start_hour"]), int(hours["trading_end_hour"])
    raise KeyError("market_hours")


def within_hours(hours: tuple[str, int, int], now: float) -> bool:
    """Whether ``now`` falls in ``(timezone, start hour, end hour)`` on a weekday."""
    zone, start_hour, end_hour = hours
    local = datetime.fromtimestamp(now, ZoneInfo(zone))
    return local.weekday() < 5 and start_hour <= local.hour < end_hour


def market_is_open(config: dict, now: float) -> bool:
    """
    Whether WATA may be trading at ``now``, as the deploy judges it: its hours
    (``market_hours``, in their timezone) on a weekday. Unknown counts as open.
    The downloads, the research export, the paper days and the restarts of the
    trading path wait for it to be closed.
    """
    try:
        return within_hours(trading_hours(config), now)
    except (ValueError, KeyError, TypeError):
        # An unknown zone is a KeyError (ZoneInfoNotFoundError), a malformed one a ValueError.
        return True


def scheduler_jobs(config: dict) -> list[tuple[str, str, str]]:
    """The scheduler's daily jobs, as it schedules them: (what, HH:MM, timezone)."""
    zone = trading_timezone(config)
    jobs = [("the daily report", "22:00", zone), ("the calendars' check", "09:00", zone)]
    close = rule_config(config, "day_trading").get("close_position_time")
    if close:
        jobs.append(("the close of the day", close, rule_config(config, "market_hours").get("timezone") or zone))
    return jobs


def scheduler_job_near(config: dict, now: float, margin: int = SCHEDULER_JOB_MARGIN_SECONDS) -> str | None:
    """The scheduler's job due within ``margin`` seconds of ``now``, before or after, said; None if none."""
    for what, at, zone in scheduler_jobs(config):
        try:
            hour, minute = (int(part) for part in str(at).split(":")[:2])
            local_zone = ZoneInfo(zone)
        except (ValueError, KeyError, TypeError):
            continue
        today = datetime.fromtimestamp(now, local_zone).date()
        for day in (today - timedelta(days=1), today, today + timedelta(days=1)):
            due = datetime.combine(day, datetime.min.time().replace(hour=hour, minute=minute), tzinfo=local_zone)
            if abs(due.timestamp() - now) <= margin:
                return f"{what} at {hour:02d}:{minute:02d} ({zone})"
    return None
