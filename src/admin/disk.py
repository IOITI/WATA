"""
The server's disk, as the hourly samples of watadisk say it (src/disk/): the Disk page, its health entry, its
projection, and Telegram when it turns low.

The samples are written on the host by the collector, as root, into /app/wata/var/lib/disk, which the admin
mounts read-only at /app/var/lib/disk: it never measures anything itself, and holds no Docker nor any folder
outside its mounts. What it keeps is what it said last (``disk/said.json`` in its own folder), so that a level is
said on Telegram once when it comes and once when it goes, whatever restarts in between.

The projection is read-only: the retentions asked are only used to compute it, and are set where they always
were (the providers' pages, the Configuration page).
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone

from src.admin.processes import read_json, write_json
from src.disk import kinds as disk_kinds
from src.disk import levels
from src.disk.files import LATEST, SAMPLES, read_latest, read_samples
from src.disk.projection import (
    HORIZONS,
    MAX_HORIZON_DAYS,
    RETENTION_BOUNDS,
    RETENTIONS,
    checked_retentions,
    paces,
    project,
)
from src.disk.trend import moment

logger = logging.getLogger(__name__)

# The collector's folder, mounted read-only (deploy/docker-compose.yml).
DISK_PATH = "/app/var/lib/disk"
SAID_FILE = "said.json"
# How often the level is judged: the samples come every hour.
TICK_SECONDS = 300
# The levels said on Telegram, when they come and when they go.
NOTABLE = (levels.WARNING, levels.CRITICAL, levels.STALE)
# The points of the 14 days drawn on the page: every sample of 14 days of hours.
SERIES_POINTS = 14 * 24
TOP_TABLES = 40


def _number(value, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _section(config: dict, *path: str) -> dict:
    found = config
    for key in path:
        found = found.get(key) if isinstance(found, dict) else None
    return found if isinstance(found, dict) else {}


class DiskWatch:
    def __init__(self, *, config_path: str, prices_root: str, state_folder: str, root: str = DISK_PATH,
                 notify=None, publish=None, clock=time.time):
        self._root = root
        self._config_path = config_path
        self._prices_root = prices_root
        self._state_folder = state_folder
        self._notify = notify
        self._publish = publish
        self._clock = clock
        # The samples read last, kept while their file is the same.
        self._cached: tuple | None = None
        self._samples: list[dict] = []

    # ── reading ──

    def _config(self) -> dict:
        try:
            with open(self._config_path, encoding="utf-8") as file:
                found = json.load(file)
        except (OSError, ValueError):
            return {}
        return found if isinstance(found, dict) else {}

    def _now(self) -> datetime:
        return datetime.fromtimestamp(self._clock(), timezone.utc)

    def latest(self) -> dict | None:
        return read_latest(os.path.join(self._root, LATEST))

    def samples(self) -> list[dict]:
        path = os.path.join(self._root, SAMPLES)
        try:
            status = os.stat(path)
        except OSError:
            return []
        key = (status.st_mtime_ns, status.st_size)
        if self._cached != key:
            self._samples = read_samples(path)
            self._cached = key
        return self._samples

    def thresholds(self, config: dict | None = None) -> levels.Thresholds:
        return levels.Thresholds.from_config(config if config is not None else self._config())

    def judged(self) -> dict:
        """The disk's level now (src/disk/levels.py), from the newest sample."""
        return levels.judge(self.latest(), self.thresholds(), self._now())

    def retentions(self, config: dict | None = None) -> dict:
        """The retentions as they are set now: in config.json, and beside the prices for those kept there."""
        config = config if config is not None else self._config()
        trade = _section(config, "trade", "config")
        recorder, capital, engine = trade.get("price_recorder"), trade.get("capital_feed"), trade.get("ta_engine")
        recorder, capital, engine = (part if isinstance(part, dict) else {} for part in (recorder, capital, engine))
        keep = read_json(os.path.join(self._prices_root, "dukascopy", "_fetch", "keep.json")) or {}
        research = read_json(os.path.join(self._prices_root, "research", "settings.json")) or {}
        return {
            "saxo_retention_months": _number(recorder.get("retention_months"), 0),
            "capital_retention_months": _number(capital.get("retention_months"), 0),
            "capital_day_files_days": _number(capital.get("day_files_retention_days"), 0),
            "dukascopy_retain_days": _number(keep.get("retain_days"), 0),
            "signal_cache_mb": _number(_section(config, "admin").get("backtests_signal_cache_mb"),
                                       RETENTIONS["signal_cache_mb"]),
            "research_keep_days": _number(research.get("keep_days"), RETENTIONS["research_keep_days"]),
            "ta_monitoring_days": _number(engine.get("monitoring_days"), RETENTIONS["ta_monitoring_days"]),
        }

    # ── the page ──

    def overview(self) -> dict:
        """What the Disk page shows: the level, each kind, the 14 days, the details of the newest sample."""
        config = self._config()
        thresholds = self.thresholds(config)
        samples = self.samples()
        latest = self.latest() or (samples[-1] if samples else None)
        judged = levels.judge(latest, thresholds, self._now())
        retentions = self.retentions(config)
        found = {
            "judged": judged,
            "retentions": {"values": retentions, "bounds": {key: list(value) for key, value in RETENTION_BOUNDS.items()}},
            "horizons": list(HORIZONS),
            "max_horizon_days": MAX_HORIZON_DAYS,
            "sample": None,
            "kinds": [],
            "families": [],
            "series": [],
            "details": {},
        }
        if latest is None:
            return found
        sizes = disk_kinds.sizes(latest)
        at = moment(latest.get("at")) or self._now()
        found_paces = paces(samples or [latest], latest, at.date())
        week_ago = _sample_near(samples, at - timedelta(days=7))
        before = disk_kinds.sizes(week_ago) if week_ago is not None else {}
        used = sum(value or 0 for value in sizes.values())
        found["sample"] = {
            "at": latest.get("at"),
            "took_seconds": latest.get("took_seconds"),
            "errors": latest.get("errors") or {},
            "filesystems": latest.get("filesystems") or [],
            "trend": latest.get("trend"),
            "samples": len(samples),
            "first_at": samples[0].get("at") if samples else latest.get("at"),
        }
        found["kinds"] = [
            {
                "id": kind.id, "label": kind.label, "family": kind.family, "model": kind.model, "knob": kind.knob,
                "link": kind.link, "note": kind.note, "bytes": sizes.get(kind.id),
                "share": round((sizes.get(kind.id) or 0) / used * 100, 2) if used else None,
                "per_day": None if found_paces[kind.id]["per_day"] is None else round(found_paces[kind.id]["per_day"]),
                "pace_from": found_paces[kind.id]["from"],
                "week": (None if week_ago is None or sizes.get(kind.id) is None or before.get(kind.id) is None
                         else sizes[kind.id] - before[kind.id]),
                "week_since": None if week_ago is None else week_ago.get("at"),
                "retention": retentions.get(kind.knob) if kind.knob else None,
            }
            for kind in disk_kinds.KINDS
        ]
        found["families"] = [{"name": name, "bytes": value} for name, value in disk_kinds.families(sizes).items()]
        found["series"] = [_point(sample) for sample in _thinned(samples, SERIES_POINTS)]
        found["details"] = _details(latest)
        return found

    def projection(self, asked: dict | None, horizons: list[int] | None = None) -> dict:
        """The projection of the newest samples under the retentions asked over those set now (ValueError: refused)."""
        samples = self.samples()
        if not samples:
            raise ValueError("No sample of the disk yet: the hourly timer (watadisk) is made by the install")
        horizons = sorted(set(horizons or HORIZONS))
        if not horizons or any(not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= MAX_HORIZON_DAYS
                               for days in horizons) or len(horizons) > 8:
            raise ValueError(f"Up to 8 horizons, each between 1 and {MAX_HORIZON_DAYS} days")
        config = self._config()
        current = self.retentions(config)
        retentions = checked_retentions(asked, current)
        found = project(samples, retentions, self.thresholds(config), horizons=horizons)
        return {**found, "current": current, "asked": {key: value for key, value in retentions.items()
                                                       if current.get(key) != value}}

    # ── what follows ──

    def _said(self) -> dict:
        found = read_json(os.path.join(self._state_folder, SAID_FILE))
        return found if isinstance(found, dict) else {}

    async def tick(self) -> dict:
        """Judge the disk; a new level is told to the pages, and said on Telegram when it comes to or leaves a low one."""
        judged = await asyncio.to_thread(self.judged)
        level, before = judged["level"], self._said().get("level")
        if level == before:
            return judged
        if self._publish is not None:
            self._publish("disk_changed", level=level, previous=before, reason=judged.get("reason"))
        if self._notify is not None and (level in NOTABLE or (before in NOTABLE and level == levels.OK)):
            mark = "✅" if level == levels.OK else "🛑" if level == levels.CRITICAL else "⚠️"
            again = " (fine again)" if level == levels.OK else ""
            try:
                await self._notify(f"{mark} WATA: {levels.said(judged)}{again}")
            except Exception as e:
                logger.warning(f"The disk's level could not be said on Telegram: {e}")
        os.makedirs(self._state_folder, exist_ok=True)
        write_json(os.path.join(self._state_folder, SAID_FILE), {"level": level, "at": self._now().isoformat()})
        return judged

    async def keep(self) -> None:
        """Until cancelled: judge the disk every five minutes."""
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"The disk could not be judged: {e}")
            await asyncio.sleep(TICK_SECONDS)


def _sample_near(samples: list[dict], at: datetime) -> dict | None:
    """The first sample at or after ``at``; None when the samples begin after it plus a day."""
    for sample in samples:
        moment_at = moment(sample.get("at"))
        if moment_at is not None and moment_at >= at:
            return sample if moment_at - at <= timedelta(days=1) else None
    return None


def _thinned(samples: list[dict], most: int) -> list[dict]:
    if len(samples) <= most:
        return samples
    step = len(samples) / most
    picked = [samples[int(index * step)] for index in range(most)]
    return picked[:-1] + [samples[-1]]


def _point(sample: dict) -> dict:
    system = disk_kinds.wata_filesystem(sample) or {}
    return {
        "at": sample.get("at"),
        "available": system.get("available"),
        "total": system.get("total"),
        "families": disk_kinds.families(disk_kinds.sizes(sample)),
    }


def _details(latest: dict) -> dict:
    tables = latest.get("tables") or {}
    folders = latest.get("folders") or {}
    prefix = f"{disk_kinds.LOG_FOLDER}/"
    return {
        "tables": [{"name": name, "bytes": size}
                   for name, size in sorted(tables.items(), key=lambda item: -item[1])[:TOP_TABLES]],
        "partitions": {
            table: [{"name": name, "month": disk_kinds.partition_month(name), "bytes": size}
                    for name, size in sorted(months.items())]
            for table, months in (latest.get("partitions") or {}).items()
        },
        "database_bytes": latest.get("database_bytes"),
        "logs": sorted(({"service": path[len(prefix):], "bytes": size} for path, size in folders.items()
                        if path.startswith(prefix) and "/" not in path[len(prefix):]), key=lambda item: -item["bytes"]),
        "log_cap": disk_kinds.log_cap(latest),
        "releases": [{"version": version, "bytes": size} for version, size in sorted(
            (latest.get("releases") or {}).items(), key=lambda item: [int(part) for part in item[0].split(".")])],
        "dumps": latest.get("dumps") or [],
        "day_files": latest.get("day_files") or {},
        "docker": latest.get("docker"),
        "host_folders": latest.get("host_folders") or {},
    }
