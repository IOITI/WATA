"""
Where the disk goes: each kind's bytes, and the free space, at future days under given retentions.

Each kind grows at its pace: the slope of its bytes over the samples kept (14 days at most, from 3 days on);
before that, for the monthly partitions, the newest month's bytes over its days, and for the days kept as files,
their bytes over the days they span. Then, by its model (src/disk/kinds.py):

- ``months``: whole monthly partitions are dropped once their month began before ``M`` months before the
  current one (``drop_month_partitions``): at a date, the days since that month's first are kept;
- ``days``: what is older than ``D`` days is removed: ``D`` days are kept;
- ``cap``: removed beyond a size;
- ``backups``: the nightly dumps leave the bars out, the Sunday ones hold them: those grow with the bars;
- ``trend``: at its pace, nothing removes it.

A retention of 0 keeps everything. A shorter retention than what is held is applied at the next day. What a
kind's pace is not known for yet stays as it is. The free space falls by what the kinds grow, the disk's size
unchanged. Nothing here reads a file: the admin gives the samples and the retentions.
"""

from datetime import date, datetime, timedelta, timezone

from src.disk.kinds import (
    CAP,
    DAYS,
    FAMILIES,
    KINDS,
    MONTHS,
    PARTITIONED,
    log_cap,
    partition_month,
    sizes,
    wata_filesystem,
)
from src.disk.levels import Thresholds
from src.disk.trend import moment, per_day

# The retentions a projection may change, and what they are when nothing says.
RETENTIONS = {
    "saxo_retention_months": 0,
    "capital_retention_months": 0,
    "capital_day_files_days": 0,
    "dukascopy_retain_days": 0,
    "signal_cache_mb": 1024,
    "research_keep_days": 30,
    "ta_monitoring_days": 7,
}
# Bounds of what may be asked, as the settings take them.
RETENTION_BOUNDS = {
    "saxo_retention_months": (0, 120),
    "capital_retention_months": (0, 120),
    "capital_day_files_days": (0, 3650),
    "dukascopy_retain_days": (0, 3650),
    "signal_cache_mb": (64, 8192),
    "research_keep_days": (1, 365),
    "ta_monitoring_days": (1, 30),
}
HORIZONS = (30, 90, 180, 365)
MAX_HORIZON_DAYS = 730
# How far the levels are looked for.
LOOK_DAYS = 1825
# The samples' pace is believed from this span on.
LEAST_DAYS = 3
MONTH_DAYS = 30.44

# Where a kind's pace was read from.
FROM_SAMPLES = "samples"
FROM_PARTITIONS = "partitions"
FROM_DAYS_HELD = "days held"


def _month_start(day: date, months: int) -> date:
    index = day.year * 12 + (day.month - 1) + months
    return date(index // 12, index % 12 + 1, 1)


def _days_in_month(month: date) -> int:
    return (_month_start(month, 1) - month).days


def kept_days(model: str, value: int, day: date) -> int | None:
    """How many calendar days a retention keeps at ``day``; None when it keeps everything."""
    if not value or value <= 0:
        return None
    if model == MONTHS:
        return (day - _month_start(day, -value)).days + 1
    return value


def _partition_pace(latest: dict, kind_id: str, today: date) -> float | None:
    """A partitioned kind's bytes per day from its newest months: the larger of the current and the last one."""
    table = PARTITIONED[kind_id]
    months = (latest.get("partitions") or {}).get(table) or {}
    by_month = {}
    for name, value in months.items():
        month = partition_month(name)
        if month:
            by_month[month] = int(value or 0)
    current = today.replace(day=1)
    paces = []
    this_month = by_month.get(current.strftime("%Y-%m"))
    if this_month and today.day >= LEAST_DAYS:
        paces.append(this_month / today.day)
    previous = _month_start(today, -1)
    last_month = by_month.get(previous.strftime("%Y-%m"))
    if last_month:
        paces.append(last_month / _days_in_month(previous))
    if not paces:
        return None
    table_bytes = sum(by_month.values())
    kind_bytes = sizes(latest).get(kind_id) or table_bytes
    # The tables beside the bars (coverage, volume) grow with them.
    scale = kind_bytes / table_bytes if table_bytes else 1
    return max(paces) * scale


def _days_held(latest: dict, kind_id: str) -> int | None:
    """The calendar days the files of a kind kept by days span, first to last."""
    held = (latest.get("day_files") or {}).get(kind_id) or {}
    try:
        first = date.fromisoformat(held["first"])
        last = date.fromisoformat(held["last"])
    except (KeyError, TypeError, ValueError):
        return None
    return (last - first).days + 1


def paces(samples: list[dict], latest: dict, today: date) -> dict[str, dict]:
    """Each kind's bytes per day and where it was read from (None when not known yet)."""
    series: dict[str, list] = {kind.id: [] for kind in KINDS}
    for sample in samples:
        at = moment(sample.get("at"))
        if at is None:
            continue
        for kind_id, value in sizes(sample).items():
            if value is not None:
                series[kind_id].append((at, value))
    found = {}
    for kind in KINDS:
        pace, source = per_day(series[kind.id], LEAST_DAYS), FROM_SAMPLES
        if pace is None and kind.id in PARTITIONED:
            pace, source = _partition_pace(latest, kind.id, today), FROM_PARTITIONS
        if pace is None and kind.model == DAYS:
            held = _days_held(latest, kind.id)
            now = sizes(latest).get(kind.id)
            if held and now:
                pace, source = now / held, FROM_DAYS_HELD
        found[kind.id] = {"per_day": pace, "from": source if pace is not None else None}
    return found


def checked_retentions(asked: dict | None, current: dict) -> dict:
    """The retentions to project with: those asked, within their bounds, over the current ones."""
    found = {**RETENTIONS, **{key: value for key, value in current.items() if key in RETENTIONS}}
    for key, value in (asked or {}).items():
        if key not in RETENTIONS:
            raise ValueError(f"No retention named {key}")
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{key} must be a whole number")
        low, high = RETENTION_BOUNDS[key]
        if not low <= value <= high:
            raise ValueError(f"{key} must be between {low} and {high}")
        found[key] = value
    return found


class _Kind:
    """One kind's bytes at a future day."""

    def __init__(self, kind, now: int, pace: float | None, value: int | None, latest: dict):
        self.kind = kind
        self.now = now
        # Growth only: a kind that shrank over the samples (a prune, a removal) is not taken to keep shrinking.
        self.pace = max(pace or 0.0, 0.0)
        self.known = pace is not None
        self.value = value
        self.cap = None
        self.per_day = self.pace
        if kind.model == CAP:
            if kind.id == "wata_logs":
                self.cap = max(log_cap(latest), now)
            elif value:
                self.cap = value * 1024 * 1024
        if kind.model == DAYS:
            held = _days_held(latest, kind.id)
            if held and now:
                self.per_day = now / held

    def at(self, days: int, day: date) -> float:
        if days <= 0 or not self.known:
            return float(self.now)
        grown = self.now + self.pace * days
        if self.cap is not None:
            return min(grown, self.cap)
        if self.kind.model in (MONTHS, DAYS):
            window = kept_days(self.kind.model, self.value or 0, day)
            if window is not None and self.per_day > 0:
                return min(grown, self.per_day * window)
        return grown

    def steady(self) -> list[float] | None:
        """What it settles at: [least, most] over a month for monthly partitions; None when it grows."""
        if self.cap is not None:
            return [float(self.cap), float(self.cap)]
        if not self.known or not self.value or self.per_day <= 0:
            return None
        if self.kind.model == MONTHS:
            return [self.per_day * self.value * MONTH_DAYS, self.per_day * (self.value + 1) * MONTH_DAYS]
        if self.kind.model == DAYS:
            return [self.per_day * self.value, self.per_day * self.value]
        return None


def _full_dumps(latest: dict) -> tuple[int, int]:
    """How many dumps hold the bars, and the newest one's bytes."""
    dumps = [dump for dump in latest.get("dumps") or [] if dump.get("full")]
    if not dumps:
        return 0, 0
    newest = max(dumps, key=lambda dump: str(dump.get("file")))
    return len(dumps), int(newest.get("bytes") or 0)


def project(samples: list[dict], retentions: dict, thresholds: Thresholds,
            horizons=HORIZONS, today: date | None = None) -> dict:
    """
    The projection of the newest of ``samples`` (oldest first): each kind and family now and at each horizon,
    the free space day by day up to the farthest horizon, and the days until each level.
    """
    if not samples:
        raise ValueError("No sample of the disk yet")
    latest = samples[-1]
    at = moment(latest.get("at")) or datetime.now(timezone.utc)
    today = today or at.date()
    system = wata_filesystem(latest) or {}
    total = int(system.get("total") or 0)
    available = int(system.get("available") or 0)
    now_sizes = sizes(latest)
    found_paces = paces(samples, latest, today)
    models = {
        kind.id: _Kind(kind, now_sizes.get(kind.id) or 0, found_paces[kind.id]["per_day"],
                       retentions.get(kind.knob) if kind.knob else None, latest)
        for kind in KINDS
    }
    full_count, full_bytes = _full_dumps(latest)
    bars_now = (now_sizes.get("saxo_bars") or 0) + (now_sizes.get("capital_bars") or 0)

    def kinds_at(days: int) -> dict[str, float]:
        day = today + timedelta(days=days)
        found = {kind_id: model.at(days, day) for kind_id, model in models.items()}
        backups = models["backups"]
        if full_count and bars_now and days > 0:
            # Each Sunday dump holds the bars as they are then.
            bars = found["saxo_bars"] + found["capital_bars"]
            found["backups"] = max(0.0, backups.now + full_count * full_bytes / bars_now * (bars - bars_now))
        return found

    def free_at(found: dict[str, float]) -> float:
        return available - sum(found[kind.id] - (now_sizes.get(kind.id) or 0) for kind in KINDS)

    farthest = min(max(horizons), MAX_HORIZON_DAYS)
    series = []
    reached = {"warning": None, "critical": None}
    levels = {"warning": thresholds.warning_free_percent, "critical": thresholds.critical_free_percent}
    for days in range(0, LOOK_DAYS + 1):
        found = kinds_at(days)
        free = free_at(found)
        percent = free / total * 100 if total else 0.0
        for level, limit in levels.items():
            if reached[level] is None and percent < limit:
                reached[level] = days
        if days <= farthest:
            by_family = {family: 0.0 for family in FAMILIES}
            for kind in KINDS:
                by_family[kind.family] += found[kind.id]
            series.append({"days": days, "date": (today + timedelta(days=days)).isoformat(),
                           "available": round(free), "free_percent": round(percent, 2),
                           "families": {family: round(value) for family, value in by_family.items()}})
        elif all(value is not None for value in reached.values()):
            break

    def at_horizon(days: int) -> dict:
        found = kinds_at(days)
        free = free_at(found)
        return {"days": days, "date": (today + timedelta(days=days)).isoformat(), "available": round(free),
                "free_percent": round(free / total * 100, 2) if total else None,
                "kinds": {kind_id: round(value) for kind_id, value in found.items()}}

    return {
        "at": latest.get("at"),
        "today": today.isoformat(),
        "total": total,
        "available": available,
        "retentions": retentions,
        "thresholds": {"warning_free_percent": thresholds.warning_free_percent,
                       "critical_free_percent": thresholds.critical_free_percent},
        "kinds": [
            {
                "id": kind.id,
                "now": now_sizes.get(kind.id),
                "per_day": None if found_paces[kind.id]["per_day"] is None else round(found_paces[kind.id]["per_day"]),
                "pace_from": found_paces[kind.id]["from"],
                "retention": models[kind.id].value,
                "steady": None if (steady := models[kind.id].steady()) is None else [round(value) for value in steady],
            }
            for kind in KINDS
        ],
        "horizons": [at_horizon(days) for days in horizons],
        "series": series,
        "days_to_warning": reached["warning"],
        "days_to_critical": reached["critical"],
        "look_days": LOOK_DAYS,
    }
