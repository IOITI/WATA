"""
A dump of the server's database restored on this machine and its trading tables counted, as
``pg_backup.sh check`` does on the server: a backup is only one once it was restored.

    python -m lab backup check              the newest dump lab/sync.sh pulled (var/lib/lab/backups)
    python -m lab backup check --offsite    the newest copy in the bucket, decrypted here

The copies off the server are encrypted to your age key (deploy/pg_backup.sh), whose private half
stays on this machine: WATA_BACKUP_AGE_IDENTITY, ~/.config/wata/backup-age.key by default. The
bucket is read with your own rclone configuration (.venv-lab/bin/rclone config), better with a key
of the bucket that may only read: WATA_BACKUP_REMOTE is the rclone path of the server's
BACKUP_S3_BUCKET and BACKUP_S3_PREFIX (``wata-offsite:wata-backups/wata``, say). age and rclone are
taken from .venv-lab/bin, where lab/backup_tools.sh puts them, else from the PATH.

Each check restores into a PostgreSQL of its own in a temporary folder (pgserver), deleted after:
the lab's database is never touched, and the decrypted dump never outlives the check.
"""

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from lab import paths

# The tables pg_backup.sh check counts (tests/test_lab.py holds the two lists together).
TRADING_TABLES = (
    "turbo_data_position", "turbo_data_order", "signal_journal", "order_request", "order_execution", "trade_risk_state",
)
DUMP_NAME = re.compile(r"^wata-(\d{4}-\d{2}-\d{2})(-full)?\.dump$")
SEALED_NAME = re.compile(r"^wata-(\d{4}-\d{2}-\d{2})(-full)?\.dump\.age$")
REMOTE_FOLDERS = ("nightly", "weekly")
DEFAULT_IDENTITY = Path("~/.config/wata/backup-age.key")


class BackupError(Exception):
    """What keeps a check from being made, said as the operator can act on it."""


def backups_folder() -> Path:
    return paths.lab_root() / "backups"


def _day(name: str, pattern) -> tuple[str, bool] | None:
    found = pattern.match(name)
    return (found.group(1), bool(found.group(2))) if found else None


def newest_local(folder: Path) -> Path | None:
    """The newest dump of ``folder`` by the day in its name: a Sunday's whole one is that day's."""
    dumps = [path for path in folder.glob("wata-*.dump") if _day(path.name, DUMP_NAME)]
    return max(dumps, key=lambda path: _day(path.name, DUMP_NAME)) if dumps else None


def newest_sealed(listed: dict[str, list[str]]) -> str | None:
    """The newest copy of the bucket, as ``<folder>/<name>``, from what each folder lists."""
    found = [
        (_day(name, SEALED_NAME), f"{folder}/{name}")
        for folder, names in listed.items() for name in names if _day(name, SEALED_NAME)
    ]
    return max(found)[1] if found else None


def tool(name: str) -> str:
    """age, rclone: the lab's own (lab/backup_tools.sh), else the PATH's."""
    own = paths.REPO_ROOT / ".venv-lab" / "bin" / name
    if own.is_file():
        return str(own)
    found = shutil.which(name)
    if not found:
        raise BackupError(f"No {name}: run lab/backup_tools.sh, or put {name} on the PATH")
    return found


def _run(command: list[str], what: str, run=subprocess.run) -> str:
    done = run(command, capture_output=True, text=True)
    if done.returncode != 0:
        raise BackupError(f"{what} failed: {(done.stderr or done.stdout).strip()[-300:]}")
    return done.stdout


def fetch_newest_sealed(remote: str, into: Path, run=subprocess.run) -> Path:
    """The newest copy of the bucket, downloaded into ``into``."""
    rclone = tool("rclone")
    remote = remote.rstrip("/")
    listed = {}
    for folder in REMOTE_FOLDERS:
        done = run([rclone, "lsf", "--files-only", f"{remote}/{folder}/"], capture_output=True, text=True)
        # A folder not made yet (no Sunday since the copies began) lists as an error: it holds nothing.
        listed[folder] = done.stdout.split() if done.returncode == 0 else []
    newest = newest_sealed(listed)
    if newest is None:
        raise BackupError(f"No copy in {remote}/nightly/ or {remote}/weekly/")
    target = into / newest.split("/", 1)[1]
    _run([rclone, "copyto", f"{remote}/{newest}", str(target)], f"Downloading {newest}", run)
    return target


def decrypt(sealed: Path, identity: Path, into: Path, run=subprocess.run) -> Path:
    """The dump inside ``sealed``, opened with the private key of ``identity``."""
    if not identity.is_file():
        raise BackupError(f"No age key at {identity}: set WATA_BACKUP_AGE_IDENTITY to the file of the key whose "
                          "public line is the server's BACKUP_AGE_RECIPIENT")
    dump = into / sealed.name[: -len(".age")]
    _run([tool("age"), "--decrypt", "--identity", str(identity), "--output", str(dump), str(sealed)],
         f"Decrypting {sealed.name}", run)
    return dump


def restore_and_count(dump: Path, folder: Path, run=subprocess.run) -> dict[str, int | None]:
    """``dump`` restored into a PostgreSQL made in ``folder``, and the rows of each trading table (None: absent)."""
    import pgserver
    from pgserver._commands import POSTGRES_BIN_PATH

    server = pgserver.get_server(folder / "pg", cleanup_mode="delete")
    try:
        uri = server.get_uri()
        _run([str(POSTGRES_BIN_PATH / "pg_restore"), "--no-owner", "--no-privileges", "-d", uri, str(dump)],
             f"Restoring {dump.name}", run)
        counts = {}
        for table in TRADING_TABLES:
            done = run([str(POSTGRES_BIN_PATH / "psql"), uri, "-tAc", f"SELECT count(*) FROM {table}"],
                       capture_output=True, text=True)
            counts[table] = int(done.stdout.strip()) if done.returncode == 0 and done.stdout.strip().isdigit() else None
        return counts
    finally:
        server.cleanup()


def check(offsite: bool = False, remote: str | None = None, identity: str | None = None,
          run=subprocess.run) -> tuple[bool, str]:
    """
    The newest dump restored and counted: (whether it held a position table with rows, what to show).
    From the bucket with ``offsite``, from lab/sync.sh's folder otherwise.
    """
    with tempfile.TemporaryDirectory(prefix="wata-backup-check-") as scratch:
        scratch = Path(scratch)
        if offsite:
            remote = remote or os.environ.get("WATA_BACKUP_REMOTE")
            if not remote:
                raise BackupError("No bucket: set WATA_BACKUP_REMOTE to the rclone path of the server's copies "
                                  "(wata-offsite:wata-backups/wata, say), or give --remote")
            key = Path(identity or os.environ.get("WATA_BACKUP_AGE_IDENTITY") or DEFAULT_IDENTITY).expanduser()
            sealed = fetch_newest_sealed(remote, scratch, run)
            dump = decrypt(sealed, key, scratch, run)
            source = f"{sealed.name}, from {remote.rstrip('/')}"
        else:
            dump = newest_local(backups_folder())
            if dump is None:
                raise BackupError(f"No dump in {backups_folder()}: run lab/sync.sh")
            source = f"{dump.name}, pulled by lab/sync.sh"
        counts = restore_and_count(dump, scratch, run)
    lines = [f"Restored {source}:"]
    lines += [f"  {table}: {'absent' if rows is None else rows}" for table, rows in counts.items()]
    return bool(counts.get("turbo_data_position")), "\n".join(lines)
