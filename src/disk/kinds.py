"""
The kinds of data that take the server's disk, read from a sample of ``collect``.

A sample records what was measured (folders, tables, Docker, dumps); a kind says what it is for the operator
(Saxo's prices, the backtests' runs, the dumps...), which family it belongs to, how it grows (``model``) and
which retention bounds it (``knob``). Kinds are read from the measures each time, so that a kind added or
changed here applies to the samples already kept.
"""

import re
from dataclasses import dataclass

# How a kind grows, for the projection (src/disk/projection.py).
TREND = "trend"  # at the pace measured, nothing removes it
MONTHS = "months"  # monthly partitions, dropped once older than a number of months
DAYS = "days"  # days removed once older than a number of days
CAP = "cap"  # removed beyond a size
BACKUPS = "backups"  # the dumps: the Sunday ones hold the bars

PRICES = "Prices"
BACKTESTS = "Backtests and research"
DATABASE = "Database"
DUMPS = "Backups"
LOGS = "Logs"
RELEASES = "Releases and Docker"
ADMIN = "Admin"
OTHER = "Other WATA files"
SYSTEM = "System"

FAMILIES = (PRICES, BACKTESTS, DATABASE, DUMPS, LOGS, RELEASES, ADMIN, OTHER, SYSTEM)

# Each WATA service's log rotates at 2 MiB, 31 files kept besides the one written (src/logging_helper).
LOG_CAP_BYTES = 32 * 2 * 1024 * 1024

SAXO_TABLES = ("price_bar_10s", "price_recorder_coverage", "price_volume", "price_instrument")
CAPITAL_TABLES = ("capital_bar_10s", "capital_coverage", "capital_minute", "capital_instrument", "capital_feed_state")
TA_TABLES = ("ta_engine_tick", "ta_rule_run")
# The tables partitioned by month, and the kind each belongs to.
PARTITIONED = {"saxo_bars": "price_bar_10s", "capital_bars": "capital_bar_10s"}

DATABASE_FOLDER = "var/lib/postgresql"
LOG_FOLDER = "var/log"
RELEASE_FOLDER = "release"
VERSION_FOLDER = re.compile(r"^\d+\.\d+\.\d+$")


@dataclass(frozen=True)
class Kind:
    id: str
    label: str
    family: str
    model: str
    # The retention the projection may change (src/disk/projection.py RETENTIONS), None when there is none.
    knob: str | None = None
    # Where it is set or seen in the admin.
    link: str | None = None
    note: str = ""


KINDS = (
    Kind("saxo_bars", "Saxo's recorded prices", PRICES, MONTHS, "saxo_retention_months", "/data/providers/saxo",
         "The price recorder's ten-second bars of the turbos and the index, in PostgreSQL."),
    Kind("capital_bars", "Capital.com's prices", PRICES, MONTHS, "capital_retention_months",
         "/data/providers/capital", "The Capital.com feed's ten-second bars and minutes, in PostgreSQL."),
    Kind("capital_day_files", "Capital.com's day files", PRICES, DAYS, "capital_day_files_days",
         "/data/providers/capital", "The same bars written as a file per day, for backtests on files."),
    Kind("dukascopy", "Dukascopy's history", PRICES, DAYS, "dukascopy_retain_days", "/data/providers/dukascopy",
         "The hours downloaded and the bars made of them."),
    Kind("price_export", "Price exports", PRICES, TREND, None, None,
         "The files wataprices export writes on the server; never removed."),
    Kind("backtest_runs", "Backtest runs", BACKTESTS, TREND, None, "/backtests",
         "Each run's folder, paper days included (kept 60 days); a run is removed by hand."),
    Kind("signal_cache", "Pine answers kept", BACKTESTS, CAP, "signal_cache_mb", "/config",
         "What the Pine runner answered, kept so that a script run again on the same days costs nothing."),
    Kind("research_export", "Research export", BACKTESTS, DAYS, "research_keep_days", "/backtests",
         "The finished days written for the lab on the Mac."),
    Kind("backtests_other", "Other backtest files", BACKTESTS, TREND, None, "/backtests",
         "Paper trading's index, Edge Watch's models, the turbo model, the former backtester's runs."),
    Kind("ta_monitoring", "TA engine monitoring", DATABASE, DAYS, "ta_monitoring_days", "/ta/monitor",
         "The engine's ticks and rule runs, in PostgreSQL."),
    Kind("trading_records", "Trading records", DATABASE, TREND, None, None,
         "Positions, orders, requests to Saxo, signals and the other tables; never removed."),
    Kind("database_other", "Database's own files", DATABASE, TREND, None, None,
         "The write-ahead log, the catalog and the free space inside the tables' files."),
    Kind("backups", "Database dumps", DUMPS, BACKUPS, None, "/system",
         "14 nightly dumps without the bars, 4 Sunday ones with them."),
    Kind("wata_logs", "WATA's logs", LOGS, CAP, None, "/logs", "Each service's log, rotated at 64 MiB."),
    Kind("container_logs", "Docker's container logs", LOGS, TREND, None, None,
         "What each container writes to its standard output, kept by Docker."),
    Kind("system_logs", "System logs", LOGS, TREND, None, None, "/var/log, the system journal included."),
    Kind("releases", "Release folders", RELEASES, TREND, None, "/system#release",
         "A folder per release installed; watadeploy prune keeps the newest three and those in use."),
    Kind("docker_images", "Docker images", RELEASES, TREND, None, "/system#release",
         "WATA's images per release, PostgreSQL's, RabbitMQ's, Traefik's."),
    Kind("docker_build_cache", "Docker build cache", RELEASES, TREND, None, None,
         "What Docker kept of each image's build; never pruned by WATA."),
    Kind("docker_containers", "Containers' own files", RELEASES, TREND, None, None,
         "What each container wrote outside its mounts."),
    Kind("admin_files", "Admin's files", ADMIN, TREND, None, "/ta/indicators",
         "Pine scripts and their versions, signal rules, the AI agent's conversations and memory."),
    Kind("wata_other", "Other WATA files", OTHER, TREND, None, None,
         "RabbitMQ's data, certificates, settings, keys and what older versions left."),
    Kind("system", "System", SYSTEM, TREND, None, None,
         "Everything else on the disk: the system, its packages, other users' files."),
)

BY_ID = {kind.id: kind for kind in KINDS}

# The kinds inside /app/wata: what is left of it is "Other WATA files".
_IN_WATA = (
    "capital_day_files", "dukascopy", "price_export", "backtest_runs", "signal_cache", "research_export",
    "backtests_other", "backups", "wata_logs", "admin_files", "releases",
)


def _folder(sample: dict, path: str) -> int | None:
    value = (sample.get("folders") or {}).get(path)
    return int(value) if isinstance(value, (int, float)) else None


def _tables(sample: dict, names) -> int | None:
    tables = sample.get("tables")
    if not isinstance(tables, dict) or not tables:
        return None
    return sum(int(tables.get(name) or 0) for name in names)


def _docker(sample: dict, field: str) -> int | None:
    value = (sample.get("docker") or {}).get(field)
    return int(value) if isinstance(value, (int, float)) else None


def _host(sample: dict, path: str) -> int | None:
    value = (sample.get("host_folders") or {}).get(path)
    return int(value) if isinstance(value, (int, float)) else None


def _less(total: int | None, *parts: int | None) -> int | None:
    if total is None:
        return None
    return max(0, total - sum(part or 0 for part in parts))


def wata_filesystem(sample: dict) -> dict | None:
    """The filesystem holding /app/wata: the one whose free space the alarms judge."""
    systems = sample.get("filesystems") or []
    for system in systems:
        if system.get("wata"):
            return system
    return systems[0] if systems else None


def log_cap(sample: dict) -> int:
    """The most WATA's logs may take: each service's rotation, and what is there besides."""
    folders = sample.get("folders") or {}
    services = [path for path in folders if path.startswith(f"{LOG_FOLDER}/wata-")]
    return max(len(services), 1) * LOG_CAP_BYTES


def sizes(sample: dict) -> dict[str, int | None]:
    """Each kind's bytes in a sample; None for what was not measured."""
    tables = sample.get("tables") if isinstance(sample.get("tables"), dict) else {}
    known = set(SAXO_TABLES) | set(CAPITAL_TABLES) | set(TA_TABLES)
    found: dict[str, int | None] = {
        "saxo_bars": _tables(sample, SAXO_TABLES),
        "capital_bars": _tables(sample, CAPITAL_TABLES),
        "ta_monitoring": _tables(sample, TA_TABLES),
        "trading_records": _tables(sample, [name for name in tables if name not in known]),
        "capital_day_files": _folder(sample, "var/lib/price_history/capital"),
        "dukascopy": _folder(sample, "var/lib/price_history/dukascopy"),
        "research_export": _folder(sample, "var/lib/price_history/research"),
        "price_export": _folder(sample, "var/lib/price_export"),
        "backtest_runs": _folder(sample, "var/lib/backtests/runs"),
        "signal_cache": _folder(sample, "var/lib/backtests/_signal_cache"),
        "backups": _folder(sample, "var/lib/backups"),
        "wata_logs": _folder(sample, LOG_FOLDER),
        "admin_files": _folder(sample, "var/lib/admin"),
        "container_logs": _host(sample, "/var/lib/docker/containers"),
        "system_logs": _host(sample, "/var/log"),
        "docker_images": _docker(sample, "images"),
        "docker_build_cache": _docker(sample, "build_cache"),
        "docker_containers": _docker(sample, "containers"),
    }
    found["backtests_other"] = _less(_folder(sample, "var/lib/backtests"), found["backtest_runs"],
                                     found["signal_cache"])
    releases = sample.get("releases")
    if isinstance(releases, dict):
        found["releases"] = sum(int(value or 0) for value in releases.values()) + (
            _folder(sample, RELEASE_FOLDER) or 0)
    else:
        found["releases"] = None
    database = _folder(sample, DATABASE_FOLDER)
    found["database_other"] = _less(database, found["saxo_bars"], found["capital_bars"], found["ta_monitoring"],
                                    found["trading_records"]) if found["saxo_bars"] is not None else None
    in_database = database if database is not None else sum(
        found[name] or 0 for name in ("saxo_bars", "capital_bars", "ta_monitoring", "trading_records"))
    found["wata_other"] = _less(_folder(sample, ""), in_database, *(found[name] for name in _IN_WATA))
    system = wata_filesystem(sample)
    if system is None:
        found["system"] = None
    else:
        # The kinds outside /app/wata are on Docker's or the system's folders: all of them on one disk here.
        measured = sum(value or 0 for name, value in found.items() if name != "system")
        found["system"] = max(0, int(system.get("used") or 0) - measured) if len(
            sample.get("filesystems") or []) == 1 else None
    return {kind.id: found.get(kind.id) for kind in KINDS}


def families(kind_sizes: dict[str, int | None]) -> dict[str, int]:
    totals = {family: 0 for family in FAMILIES}
    for kind in KINDS:
        totals[kind.family] += kind_sizes.get(kind.id) or 0
    return totals


def partition_month(name: str) -> str | None:
    """The month of a partition named ``<table>_yYYYYmMM``, as ``YYYY-MM``."""
    match = re.search(r"_y(\d{4})m(\d{2})$", name)
    return f"{match.group(1)}-{match.group(2)}" if match else None
