"""
watadisk: an hourly sample of the server's disk and what WATA takes of it.

    watadisk now     measure, add the sample to the 14 days kept, and say it in short
    watadisk show    the newest sample kept, in short

Run as root on the server by ``watadisk.timer`` (made by deploy/install_package.sh, at the lowest priority of
processor and disk), on the server's own python3 with nothing WATA installs. It measures:

- the filesystems holding /app/wata, Docker's folder and / (``os.statvfs``);
- the folders of /app/wata, once, by the blocks their files take (``st_blocks``), links not followed, other
  disks not entered; each release's folder; /var/log and Docker's container folders;
- the dumps of var/lib/backups, the day files of Dukascopy and Capital.com (their first and last day);
- Docker's images, build cache and containers (``docker system df``);
- PostgreSQL's tables and their monthly partitions (``pg_total_relation_size``, through ``docker exec``).

A source that cannot be read is said in the sample's ``errors`` and the rest is measured all the same. The
samples are kept in /app/wata/var/lib/disk: ``samples.jsonl`` (14 days, oldest first) and ``latest.json``, the
newest with the pace of the free space (``trend``), which the admin and the scheduler read. Nothing else is
written, and nothing is removed but samples older than 14 days.
"""

import argparse
import fcntl
import json
import os
import re
import stat
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

from src.disk.files import LATEST, SAMPLES, read_samples
from src.disk.kinds import KINDS, LOG_FOLDER, VERSION_FOLDER, families, partition_month, sizes, wata_filesystem
from src.disk.trend import moment, per_day

BASE = "/app/wata"
FOLDER = "var/lib/disk"
LOCK = ".lock"
KEEP_DAYS = 14
VERSION = 1
DATABASE_CONTAINER = "postgres"
# The free space's pace is said from a day of samples on.
TREND_LEAST_DAYS = 1.0
COMMAND_SECONDS = 60

# Folders of /app/wata always kept in a sample (0 when absent), whatever their size.
NAMED = (
    "",
    "var/lib/postgresql",
    "var/log",
    "var/lib/backups",
    "var/lib/admin",
    "var/lib/admin/ta",
    "var/lib/admin/agent",
    "var/lib/backtests",
    "var/lib/backtests/runs",
    "var/lib/backtests/_signal_cache",
    "var/lib/price_history",
    "var/lib/price_history/dukascopy",
    "var/lib/price_history/dukascopy/bars",
    "var/lib/price_history/dukascopy/raw",
    "var/lib/price_history/capital",
    "var/lib/price_history/research",
    "var/lib/price_export",
    "var/lib/rabbitmq",
    "release",
)
# Other folders down to this depth are kept when they take this much.
RECORDED_DEPTH = 3
RECORDED_LEAST_BYTES = 1024 * 1024
# The day files: a file per day, ``<root>/bars/<label>/<YYYY-MM-DD>.csv.gz``.
DAY_FILES = {
    "dukascopy": "var/lib/price_history/dukascopy/bars",
    "capital_day_files": "var/lib/price_history/capital/bars",
}
DAY_FILE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.csv\.gz$")
DUMP = re.compile(r"^wata-(\d{4}-\d{2}-\d{2})(-full)?\.dump$")

TABLES_SQL = (
    "SELECT coalesce(parent.relname, c.relname), c.relname, pg_total_relation_size(c.oid) "
    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
    "LEFT JOIN pg_inherits i ON i.inhrelid = c.oid LEFT JOIN pg_class parent ON parent.oid = i.inhparent "
    "WHERE n.nspname = 'public' AND c.relkind = 'r'"
)
DATABASE_SQL = "SELECT pg_database_size(current_database())"

_UNITS = {"": 1, "k": 1e3, "m": 1e6, "g": 1e9, "t": 1e12, "p": 1e15}
_BINARY = {"ki": 2**10, "mi": 2**20, "gi": 2**30, "ti": 2**40, "pi": 2**50}


def docker_bytes(text: str) -> int | None:
    """A size as Docker writes it (``4.035GB``, ``4.857kB (0%)``, ``0B``): decimal units, binary ones read too."""
    match = re.match(r"^\s*([\d.]+)\s*([kKmMgGtTpP]i?)?B", str(text))
    if not match:
        return None
    unit = (match.group(2) or "").lower()
    factor = _BINARY.get(unit) or _UNITS.get(unit)
    if factor is None:
        return None
    return int(float(match.group(1)) * factor)


def run(arguments: list[str], timeout: float = COMMAND_SECONDS) -> str:
    """A command's output; raises with its last words when it fails."""
    try:
        done = subprocess.run(arguments, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError as e:
        raise RuntimeError(f"{arguments[0]} is not installed") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"{' '.join(arguments[:3])} gave no answer within {timeout:.0f} s") from e
    if done.returncode != 0:
        said = (done.stderr or done.stdout or "").strip().splitlines()
        raise RuntimeError(f"{' '.join(arguments[:3])} failed: {said[-1] if said else done.returncode}")
    return done.stdout


class Collector:
    def __init__(self, base: str = BASE, docker: str | None = None, runner=None, clock=None):
        self.base = base
        self.docker = docker or os.environ.get("WATA_DOCKER", "docker")
        self._run = runner or run
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.errors: dict[str, str] = {}

    # Each source alone: what fails is said, the rest measured.
    def _source(self, name: str, read, default=None):
        try:
            return read()
        except Exception as e:  # noqa: BLE001 - a sample is never lost for one source
            self.errors[name] = str(e)[:300]
            return default

    def sample(self) -> dict:
        self.errors = {}
        started = time.monotonic()
        at = self._clock()
        docker_root = self._source("docker_root", self._docker_root)
        found = {
            "v": VERSION,
            "at": at.isoformat(),
            "base": self.base,
            "filesystems": self._source("filesystems", lambda: self._filesystems(docker_root), []),
        }
        walked = self._source("folders", self._walk, {})
        found["folders"] = self._kept_folders(walked)
        found["releases"] = {name: size for name, size in walked.items() if "/" not in name
                             and VERSION_FOLDER.match(name)} if walked else None
        found["host_folders"] = self._source("host_folders", lambda: self._host_folders(docker_root), {})
        found["dumps"] = self._source("dumps", self._dumps, [])
        found["day_files"] = self._source("day_files", self._day_files, {})
        found["docker"] = self._source("docker", self._docker_df)
        tables = self._source("database", self._tables)
        if tables is not None:
            found["tables"], found["partitions"], found["database_bytes"] = tables
        found["took_seconds"] = round(time.monotonic() - started, 2)
        found["errors"] = dict(self.errors)
        return found

    def _docker_root(self) -> str:
        return self._run([self.docker, "info", "--format", "{{.DockerRootDir}}"]).strip() or "/var/lib/docker"

    def _filesystems(self, docker_root: str | None) -> list[dict]:
        mounts = _mount_points()
        by_device: dict[int, dict] = {}
        for path in (self.base, docker_root, "/"):
            if not path or not os.path.exists(path):
                continue
            device = os.stat(path).st_dev
            if device in by_device:
                by_device[device]["paths"].append(path)
                continue
            vfs = os.statvfs(path)
            by_device[device] = {
                "mount": _mount_of(os.path.realpath(path), mounts),
                "paths": [path],
                "total": vfs.f_blocks * vfs.f_frsize,
                "used": (vfs.f_blocks - vfs.f_bfree) * vfs.f_frsize,
                "free": vfs.f_bfree * vfs.f_frsize,
                "available": vfs.f_bavail * vfs.f_frsize,
                "inodes_total": vfs.f_files,
                "inodes_free": vfs.f_ffree,
            }
        systems = list(by_device.values())
        for system in systems:
            system["wata"] = self.base in system["paths"]
        return systems

    def _walk(self) -> dict[str, int]:
        return folder_sizes(self.base)

    def _kept_folders(self, walked: dict[str, int]) -> dict[str, int]:
        if not walked:
            return {}
        kept = {path: walked.get(path, 0) for path in NAMED}
        for path, size in walked.items():
            depth = path.count("/") + 1 if path else 0
            if path.startswith(f"{LOG_FOLDER}/") and depth == 3:
                kept[path] = size
            # A release's folders are said in ``releases``, each whole.
            elif depth <= RECORDED_DEPTH and size >= RECORDED_LEAST_BYTES \
                    and not VERSION_FOLDER.match(path.split("/", 1)[0]):
                kept[path] = size
        return kept

    def _host_folders(self, docker_root: str | None) -> dict[str, int]:
        """The system's logs and Docker's container folders (their logs), under the names kinds.py reads."""
        found = {}
        places = {"/var/log": "/var/log",
                  "/var/lib/docker/containers": os.path.join(docker_root or "/var/lib/docker", "containers")}
        for name, path in places.items():
            if os.path.isdir(path):
                found[name] = folder_sizes(path).get("", 0)
        return found

    def _dumps(self) -> list[dict]:
        folder = os.path.join(self.base, "var/lib/backups")
        found = []
        for entry in sorted(os.scandir(folder), key=lambda entry: entry.name) if os.path.isdir(folder) else []:
            match = DUMP.match(entry.name)
            if match and entry.is_file(follow_symlinks=False):
                found.append({"file": entry.name, "day": match.group(1), "full": bool(match.group(2)),
                              "bytes": entry.stat(follow_symlinks=False).st_blocks * 512})
        return found

    def _day_files(self) -> dict:
        found = {}
        for kind_id, relative in DAY_FILES.items():
            root = os.path.join(self.base, relative)
            days: list[str] = []
            labels = {}
            for label in sorted(os.listdir(root)) if os.path.isdir(root) else []:
                folder = os.path.join(root, label)
                if not os.path.isdir(folder):
                    continue
                held = sorted(match.group(1) for name in os.listdir(folder) if (match := DAY_FILE.match(name)))
                if held:
                    labels[label] = {"days": len(held), "first": held[0], "last": held[-1]}
                    days += held
            if days:
                found[kind_id] = {"days": len(days), "first": min(days), "last": max(days), "labels": labels}
        return found

    def _docker_df(self) -> dict:
        names = {"Images": "images", "Containers": "containers", "Local Volumes": "volumes",
                 "Build Cache": "build_cache"}
        found: dict = {"reclaimable": {}}
        for line in self._run([self.docker, "system", "df", "--format", "{{json .}}"]).splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            name = names.get(row.get("Type"))
            if name:
                found[name] = docker_bytes(row.get("Size", ""))
                found["reclaimable"][name] = docker_bytes(row.get("Reclaimable", ""))
        return found

    def _psql(self, sql: str) -> str:
        return self._run([self.docker, "exec", DATABASE_CONTAINER, "psql", "-X", "-q", "-U", "wata", "-d", "wata",
                          "-tA", "-F", "\t", "-v", "ON_ERROR_STOP=1", "-c", sql])

    def _tables(self) -> tuple[dict, dict, int]:
        tables: dict[str, int] = {}
        partitions: dict[str, dict[str, int]] = {}
        for line in self._psql(TABLES_SQL).splitlines():
            parts = line.split("\t")
            if len(parts) != 3 or not parts[2].strip().isdigit():
                continue
            parent, name, size = parts[0], parts[1], int(parts[2])
            tables[parent] = tables.get(parent, 0) + size
            if parent != name and partition_month(name):
                partitions.setdefault(parent, {})[name] = size
        database = self._psql(DATABASE_SQL).strip()
        return tables, partitions, int(database) if database.isdigit() else sum(tables.values())


def folder_sizes(root: str) -> dict[str, int]:
    """
    Every folder under ``root`` (relative, ``""`` for itself) with the bytes its files and folders take on the
    disk, links not followed and other disks not entered.
    """
    try:
        device = os.stat(root).st_dev
    except OSError:
        return {}
    own: dict[str, int] = {}
    stack = [""]
    while stack:
        relative = stack.pop()
        size = 0
        try:
            with os.scandir(os.path.join(root, relative) if relative else root) as entries:
                for entry in entries:
                    try:
                        status = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if status.st_dev != device:
                        continue
                    size += status.st_blocks * 512
                    if stat.S_ISDIR(status.st_mode):
                        stack.append(f"{relative}/{entry.name}" if relative else entry.name)
        except OSError:
            pass
        own[relative] = size
    totals: dict[str, int] = {}
    for relative, size in own.items():
        parts = relative.split("/") if relative else []
        for depth in range(len(parts) + 1):
            key = "/".join(parts[:depth])
            totals[key] = totals.get(key, 0) + size
    return totals


def _mount_points() -> list[str]:
    try:
        with open("/proc/self/mounts", encoding="utf-8") as mounts:
            return [line.split()[1] for line in mounts if len(line.split()) > 1]
    except OSError:
        return ["/"]


def _mount_of(path: str, mounts: list[str]) -> str:
    best = "/"
    for mount in mounts:
        if (path == mount or path.startswith(mount.rstrip("/") + "/")) and len(mount) > len(best):
            best = mount
    return best


def with_trend(sample: dict, samples: list[dict]) -> dict:
    """The newest sample with the pace of the free space over the samples kept (None under a day of them)."""
    points = []
    for kept in samples:
        system = wata_filesystem(kept)
        at = moment(kept.get("at"))
        if system and at and isinstance(system.get("available"), (int, float)):
            points.append((at, float(system["available"])))
    pace = per_day(points, TREND_LEAST_DAYS)
    first = moment(samples[0]["at"]) if samples else None
    last = moment(sample.get("at"))
    over = (last - first).total_seconds() / 86_400 if first and last else 0
    return {**sample, "trend": {"available_per_day": None if pace is None else round(pace),
                                "over_days": round(over, 2), "samples": len(samples)}}


def _write(path: str, text: str) -> None:
    partial = f"{path}.part"
    with open(partial, "w", encoding="utf-8") as file:
        file.write(text)
    os.replace(partial, path)


def keep(folder: str, sample: dict) -> dict:
    """The sample added to those kept, those older than 14 days removed; the newest returned with its trend."""
    os.makedirs(folder, mode=0o755, exist_ok=True)
    at = moment(sample["at"])
    since = at - timedelta(days=KEEP_DAYS)
    samples = [kept for kept in read_samples(os.path.join(folder, SAMPLES)) if moment(kept["at"]) >= since]
    samples.append(sample)
    _write(os.path.join(folder, SAMPLES), "".join(json.dumps(kept, separators=(",", ":")) + "\n" for kept in samples))
    latest = with_trend(sample, samples)
    _write(os.path.join(folder, LATEST), json.dumps(latest, separators=(",", ":")))
    return latest


def summary(latest: dict) -> str:
    """The sample in a few lines: the disk, each family, what failed."""
    lines = []
    system = wata_filesystem(latest)
    if system:
        lines.append(f"{latest.get('at')}  {system.get('mount')}: {system['available'] / 1e9:.1f} GB free of "
                     f"{system['total'] / 1e9:.1f} GB ({system['available'] / system['total'] * 100:.1f} %), "
                     f"measured in {latest.get('took_seconds')} s")
    pace = (latest.get("trend") or {}).get("available_per_day")
    if pace is not None:
        lines.append(f"Free space: {pace / 1e6:+.1f} MB a day over {latest['trend']['over_days']} days")
    kind_sizes = sizes(latest)
    for family, total in families(kind_sizes).items():
        lines.append(f"  {family:<24} {total / 1e9:8.2f} GB")
        for kind in KINDS:
            if kind.family == family and kind_sizes.get(kind.id):
                lines.append(f"    {kind.label:<30} {kind_sizes[kind.id] / 1e6:10.1f} MB")
    for source, error in (latest.get("errors") or {}).items():
        lines.append(f"Not measured: {source}: {error}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="watadisk", description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=("now", "show"))
    parser.add_argument("--base", default=BASE, help="WATA's folder on the server (/app/wata)")
    arguments = parser.parse_args(argv)
    os.umask(0o022)
    folder = os.path.join(arguments.base, FOLDER)
    if arguments.command == "show":
        try:
            with open(os.path.join(folder, LATEST), encoding="utf-8") as file:
                print(summary(json.load(file)))
        except (OSError, ValueError) as e:
            print(f"No sample to show: {e}", file=sys.stderr)
            return 1
        return 0
    os.makedirs(folder, mode=0o755, exist_ok=True)
    with open(os.path.join(folder, LOCK), "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("A sample is being taken already", file=sys.stderr)
            return 0
        latest = keep(folder, Collector(arguments.base).sample())
    print(summary(latest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
