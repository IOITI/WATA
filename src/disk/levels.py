"""
Whether the server's disk is low: the free space of the filesystem holding /app/wata against the alarm levels.

The levels are ``reporting.disk_warning_free_percent`` (15), ``reporting.disk_critical_free_percent`` (5) and
``reporting.disk_warning_days_left`` (14, 0 for none): a warning below the first, critical below the second, and a
warning too when the free space, at the pace of the last 14 days, reaches the critical level within that many
days. A sample older than ``STALE_HOURS`` is a warning of its own: the hourly timer stopped. Read by the admin
(health, Telegram) and the scheduler (the morning line).
"""

from dataclasses import dataclass
from datetime import datetime

from src.disk.kinds import wata_filesystem
from src.disk.trend import moment

WARNING_FREE_PERCENT = 15
CRITICAL_FREE_PERCENT = 5
WARNING_DAYS_LEFT = 14
# The timer samples every hour: three missed is no longer a delay.
STALE_HOURS = 3

OK = "ok"
WARNING = "warning"
CRITICAL = "critical"
STALE = "stale"
NONE = "none"


# Bounds of the levels, as ConfigurationManager and the Configuration page take them.
PERCENT_BOUNDS = (1, 50)
DAYS_LEFT_BOUNDS = (0, 365)
KEYS = ("disk_warning_free_percent", "disk_critical_free_percent", "disk_warning_days_left")


def thresholds_refusal(reporting: dict | None) -> str | None:
    """Why the levels of ``reporting`` cannot be taken; None when they can (each absent is its default)."""
    reporting = reporting if isinstance(reporting, dict) else {}
    for key in KEYS:
        value = reporting.get(key)
        if value is None:
            continue
        low, high = DAYS_LEFT_BOUNDS if key == "disk_warning_days_left" else PERCENT_BOUNDS
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            return f"Config field 'reporting.{key}' must be a whole number between {low} and {high}"
    # A critical level at or over the warning one is taken as it is: the disk is then critical before it warns.
    return None


@dataclass(frozen=True)
class Thresholds:
    warning_free_percent: float = WARNING_FREE_PERCENT
    critical_free_percent: float = CRITICAL_FREE_PERCENT
    warning_days_left: int = WARNING_DAYS_LEFT

    @classmethod
    def from_config(cls, config: dict | None) -> "Thresholds":
        reporting = (config or {}).get("reporting") or {}

        def number(key: str, default):
            value = reporting.get(key)
            return value if isinstance(value, (int, float)) and not isinstance(value, bool) else default

        return cls(
            warning_free_percent=number("disk_warning_free_percent", WARNING_FREE_PERCENT),
            critical_free_percent=number("disk_critical_free_percent", CRITICAL_FREE_PERCENT),
            warning_days_left=int(number("disk_warning_days_left", WARNING_DAYS_LEFT)),
        )


def _gb(value: float) -> str:
    return f"{value / 1e9:.1f} GB"


def judge(latest: dict | None, thresholds: Thresholds, now: datetime) -> dict:
    """
    The disk's level, its figures and why: ``level`` is ok, warning, critical, stale (no sample for
    ``STALE_HOURS``) or none (no sample yet).
    """
    if not latest:
        return {"level": NONE, "reason": "No sample of the disk yet: the hourly timer (watadisk) is made by the install"}
    at = moment(latest.get("at"))
    system = wata_filesystem(latest)
    if at is None or system is None or not system.get("total"):
        return {"level": STALE, "reason": "The last sample of the disk cannot be read"}
    total = int(system["total"])
    available = int(system.get("available") or 0)
    free_percent = available / total * 100
    slope = (latest.get("trend") or {}).get("available_per_day")
    critical_bytes = total * thresholds.critical_free_percent / 100
    days_to_critical = None
    if available <= critical_bytes:
        days_to_critical = 0.0
    elif isinstance(slope, (int, float)) and slope < 0:
        days_to_critical = (available - critical_bytes) / -slope
    age_hours = (now - at).total_seconds() / 3600
    judged = {
        "at": latest.get("at"),
        "age_hours": round(age_hours, 2),
        "total": total,
        "available": available,
        "free_percent": round(free_percent, 2),
        "available_per_day": slope,
        "days_to_critical": None if days_to_critical is None else round(days_to_critical, 1),
        "thresholds": {
            "warning_free_percent": thresholds.warning_free_percent,
            "critical_free_percent": thresholds.critical_free_percent,
            "warning_days_left": thresholds.warning_days_left,
        },
    }
    free = f"{_gb(available)} free of {_gb(total)} ({free_percent:.1f} %)"
    if age_hours > STALE_HOURS:
        return {**judged, "level": STALE,
                "reason": f"No sample of the disk for {age_hours:.0f} h (the hourly timer, watadisk); last: {free}"}
    if free_percent < thresholds.critical_free_percent:
        return {**judged, "level": CRITICAL,
                "reason": f"{free}: under the critical level of {thresholds.critical_free_percent:g} %"}
    if free_percent < thresholds.warning_free_percent:
        return {**judged, "level": WARNING, "reason": f"{free}: under {thresholds.warning_free_percent:g} %"
                                                      f"{_pace(days_to_critical)}"}
    if (thresholds.warning_days_left > 0 and days_to_critical is not None
            and days_to_critical <= thresholds.warning_days_left):
        return {**judged, "level": WARNING,
                "reason": f"{free}{_pace(days_to_critical)}, within {thresholds.warning_days_left} days"}
    return {**judged, "level": OK, "reason": f"{free}{_pace(days_to_critical)}"}


def _pace(days_to_critical: float | None) -> str:
    if days_to_critical is None:
        return ""
    return f"; {days_to_critical:.0f} days to the critical level at the pace of the last days"


def said(judged: dict) -> str:
    """One line for Telegram."""
    return f"Disk: {judged.get('reason')}"


def morning_line(latest: dict | None, config: dict | None, now: datetime) -> str | None:
    """
    What the scheduler says each morning while the disk is not fine (warning, critical, no sample for hours);
    None when it is, or when no sample was ever taken (a server without the timer yet).
    """
    judged = judge(latest, Thresholds.from_config(config), now)
    if judged["level"] in (OK, NONE):
        return None
    mark = "🛑" if judged["level"] == CRITICAL else "⚠️"
    return f"{mark} WATA: {said(judged)}."

