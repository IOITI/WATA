"""
Fourteen days of the disk's hourly samples, made up for the development admin's Disk page, in the shape watadisk
writes them on the server (src/disk/collect.py). Sizes like the server's of 2026-11-17: one disk of 96 GB, the
recorder's bars growing by about 35 MB a trading day, Docker's images and build cache about 8 GB.
"""

import json
import os
from datetime import datetime, timedelta, timezone

from src.disk.collect import with_trend
from src.disk.files import LATEST, SAMPLES

GB = 1_000_000_000
MB = 1_000_000
DAYS = 14


def _sample(at: datetime, trading_days: int, hours_today: float) -> dict:
    grown = trading_days + hours_today
    bars = int(730 * MB + grown * 35 * MB)
    capital = int(45 * MB + grown * 1.5 * MB)
    runs = int(39 * MB + grown * 0.8 * MB)
    cache = int(min(1_100 * MB + grown * 6 * MB, 1_073 * MB))
    database = bars + capital + 37 * MB + 2 * MB + 92 * MB
    folders = {
        "": 3_200 * MB + int(grown * 45 * MB), "var/lib/postgresql": database, "var/log": 474 * MB,
        "var/lib/backups": 197 * MB + int(grown * 2 * MB), "var/lib/admin": 9 * MB,
        "var/lib/backtests": runs + cache + 2 * MB, "var/lib/backtests/runs": runs,
        "var/lib/backtests/_signal_cache": cache, "var/lib/price_history/dukascopy": 424 * MB + int(grown * 3.2 * MB),
        "var/lib/price_history/capital": 4 * MB + int(grown * 0.3 * MB), "var/lib/price_history/research": 0,
        "var/lib/price_export": 0, "var/lib/rabbitmq": 1 * MB, "release": 2 * MB,
    }
    for service, size in (("wata-trader-async", 64), ("wata-scheduler", 64), ("wata-telegram", 64),
                          ("wata-watchlist-manager", 64), ("wata-capital-feed", 63), ("wata-trader", 63),
                          ("wata-position-monitor", 52), ("wata-price-recorder", 22), ("wata-api", 13),
                          ("wata-admin", 6), ("wata-ta-engine", 1)):
        folders[f"var/log/{service}"] = size * MB
    measured = (database + folders["var/log"] + folders["var/lib/backups"] + folders["var/lib/backtests"]
                + folders["var/lib/price_history/dukascopy"] + 8_431 * MB + 31 * MB + 144 * MB + 9 * MB)
    used = measured + 3_900 * MB
    return {
        "v": 1, "at": at.isoformat(), "took_seconds": 6.4, "errors": {}, "base": "/app/wata",
        "filesystems": [{"mount": "/", "paths": ["/app/wata", "/var/lib/docker", "/"], "total": 96 * GB, "used": used,
                         "free": 96 * GB - used, "available": 96 * GB - used - 4 * GB, "inodes_total": 12_976_128,
                         "inodes_free": 12_682_513, "wata": True}],
        "folders": folders,
        "releases": {"0.86.0": 15 * MB, "0.87.0": 16 * MB, "0.88.0": 16 * MB},
        "host_folders": {"/var/log": 144 * MB, "/var/lib/docker/containers": 9 * MB},
        "dumps": [{"file": f"wata-{(at - timedelta(days=day)).date()}{'-full' if day == 2 else ''}.dump",
                   "day": str((at - timedelta(days=day)).date()), "full": day == 2,
                   "bytes": 150 * MB if day == 2 else 9 * MB} for day in range(1, 8)],
        "day_files": {"dukascopy": {"days": 120, "first": str((at - timedelta(days=170)).date()),
                                    "last": str((at - timedelta(days=1)).date())},
                      "capital_day_files": {"days": 30, "first": str((at - timedelta(days=36)).date()),
                                            "last": str((at - timedelta(days=1)).date())}},
        "docker": {"images": 4_035 * MB, "containers": 31 * MB, "volumes": 0, "build_cache": 4_396 * MB,
                   "reclaimable": {"images": 0, "containers": 3 * MB, "volumes": 0, "build_cache": 902 * MB}},
        "tables": {"price_bar_10s": bars - 21 * MB, "price_recorder_coverage": 19 * MB, "price_volume": 1 * MB,
                   "price_instrument": 1 * MB, "capital_bar_10s": capital - 24 * MB, "capital_coverage": 18 * MB,
                   "capital_minute": 6 * MB, "ta_engine_tick": 14 * MB, "ta_rule_run": 23 * MB,
                   "turbo_data_position": 1 * MB, "order_request": 1 * MB},
        "partitions": {"price_bar_10s": {"price_bar_10s_y2026m10": 462 * MB,
                                         "price_bar_10s_y2026m11": max(bars - 21 * MB - 462 * MB, 0)}},
        "database_bytes": database - 70 * MB,
    }


def write(folder: str, now: datetime | None = None) -> None:
    """The samples of the last 14 days, an hour apart, into ``folder`` (samples.jsonl, latest.json)."""
    now = (now or datetime.now(timezone.utc)).replace(minute=7, second=0, microsecond=0)
    os.makedirs(folder, exist_ok=True)
    samples, trading_days = [], 0
    first = now - timedelta(days=DAYS) + timedelta(hours=1)
    at = first
    while at <= now:
        weekday = at.weekday() < 5
        if at.hour == 0 and at != first and (at - timedelta(days=1)).weekday() < 5:
            trading_days += 1
        today = (at.hour / 24) if weekday else 0
        samples.append(_sample(at, trading_days, today))
        at += timedelta(hours=1)
    with open(os.path.join(folder, SAMPLES), "w", encoding="utf-8") as file:
        file.writelines(json.dumps(sample, separators=(",", ":")) + "\n" for sample in samples)
    with open(os.path.join(folder, LATEST), "w", encoding="utf-8") as file:
        json.dump(with_trend(samples[-1], samples), file, separators=(",", ":"))
