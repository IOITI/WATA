"""
The lab's PostgreSQL: a server of its own in ``var/lib/lab/pg`` (pgserver),
with WATA's tables, filled from the research files the server writes each
finished day (src/admin/research_export.py).

A day is loaded in one transaction: each table's file is copied into a
temporary table, the day's rows are removed (by the moment the export chose
them by, so a day written again replaces itself), then inserted; the two
tables of instruments are taken whole, the newest copy winning. What was
loaded is noted with the manifest's digest (``lab_loaded_day``): a day is
loaded again only when the server wrote it again.

The backtests and the lab's admin read this database as the server's read
theirs, through the same code (``DatabaseData``, ``AdminQueries``).
"""

import csv
import gzip
import hashlib
import io
import json
from datetime import date, datetime, timedelta
from pathlib import Path

from lab import paths
from src.admin.research_export import MANIFEST_FILE, TABLES

LOADED_DAY_SQL = """
CREATE TABLE IF NOT EXISTS lab_loaded_day (
    day             DATE PRIMARY KEY,
    manifest_sha256 TEXT NOT NULL,
    loaded_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""
# Partitioned by month: the partitions of a day are made before its bars arrive.
PARTITIONED = ("price_bar_10s", "capital_bar_10s")
# The two tables written whole each day: the newest copy of each row wins.
SNAPSHOT_KEY = "id"


# ── the server ──


def server():
    """The lab's PostgreSQL, started when it is not running. It keeps running after this process ends."""
    import pgserver

    paths.pgdata().mkdir(parents=True, exist_ok=True)
    return pgserver.get_server(paths.pgdata(), cleanup_mode=None)


def is_running() -> bool:
    return (paths.pgdata() / "postmaster.pid").exists()


def dsn() -> str:
    return server().get_uri()


def stop() -> bool:
    """
    Stop the lab's PostgreSQL; False when it was not running. Whoever else
    still reads it (the lab's admin) is stopped first by the caller.
    """
    if not is_running():
        return False
    from pgserver._commands import pg_ctl

    # pg_ctl itself: pgserver only stops a server when the last process that opened it lets go,
    # and keeps one handle per data folder in a process, made with whatever it was first asked.
    pg_ctl(["-w", "stop"], pgdata=paths.pgdata())
    return True


# ── the research files ──


def research_days(root: Path | None = None) -> list[tuple[date, Path]]:
    """The days written whole (with their manifest), oldest first."""
    root = root or paths.research_days()
    if not root.is_dir():
        return []
    days = []
    for folder in root.iterdir():
        try:
            day = date.fromisoformat(folder.name)
        except ValueError:
            continue
        if (folder / MANIFEST_FILE).is_file():
            days.append((day, folder))
    return sorted(days)


def manifest_digest(folder: Path) -> str:
    return hashlib.sha256((folder / MANIFEST_FILE).read_bytes()).hexdigest()


def header(path: Path) -> list[str]:
    """The columns of a research file, from its first line."""
    with gzip.open(path, "rt", encoding="utf-8", newline="") as in_file:
        first = in_file.readline()
    return next(csv.reader(io.StringIO(first)), []) if first.strip() else []


class _Executor:
    """What ``ensure_month_partitions`` asks of a database, on one connection."""

    def __init__(self, connection):
        self._connection = connection

    async def execute(self, query: str, *args):
        return await self._connection.execute(query, *args)

    async def fetch(self, query: str, *args):
        return await self._connection.fetch(query, *args)


async def ensure_schema(connection) -> None:
    from src.database.postgres import CAPITAL_SCHEMA_SQL, PRICE_HISTORY_SCHEMA_SQL, SCHEMA_SQL, TA_SCHEMA_SQL

    for sql in (SCHEMA_SQL, PRICE_HISTORY_SCHEMA_SQL, CAPITAL_SCHEMA_SQL, TA_SCHEMA_SQL, LOADED_DAY_SQL):
        await connection.execute(sql)


async def load_day(connection, folder: Path, digest: str | None = None) -> dict[str, int]:
    """One research day into the database, replacing what was loaded of it before; the rows of each table."""
    from src.database.postgres import ensure_month_partitions

    manifest = json.loads((folder / MANIFEST_FILE).read_text(encoding="utf-8"))
    day = date.fromisoformat(manifest["day"])
    start = datetime.fromisoformat(manifest["start"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(manifest["end"].replace("Z", "+00:00"))
    counts: dict[str, int] = {}
    async with connection.transaction():
        for table in TABLES:
            path = folder / f"{table.name}.csv.gz"
            if not path.is_file():
                continue
            columns = header(path)
            if not columns:
                continue
            if table.name in PARTITIONED:
                # A local day can begin in one month of UTC and end in the next.
                for moment in (start, end - timedelta(seconds=1)):
                    await ensure_month_partitions(_Executor(connection), table.name, moment.date(), months_ahead=0)
            loading = f"lab_load_{table.name}"
            await connection.execute(f"CREATE TEMP TABLE {loading} (LIKE {table.name} INCLUDING DEFAULTS) ON COMMIT DROP")
            with gzip.open(path, "rb") as source:
                await connection.copy_to_table(loading, source=source, columns=columns, format="csv", header=True)
            listed = ", ".join(columns)
            if not table.moment:
                kept = ", ".join(f"{column} = EXCLUDED.{column}" for column in columns if column != SNAPSHOT_KEY)
                await connection.execute(
                    f"INSERT INTO {table.name} ({listed}) SELECT {listed} FROM {loading} "
                    f"ON CONFLICT ({SNAPSHOT_KEY}) DO UPDATE SET {kept}"
                )
            else:
                query, args = table.query(start, end, day)
                await connection.execute(f"DELETE FROM {table.name} WHERE {query.split(' WHERE ', 1)[1]}", *args)
                await connection.execute(
                    f"INSERT INTO {table.name} ({listed}) SELECT {listed} FROM {loading} ON CONFLICT DO NOTHING"
                )
            counts[table.name] = await connection.fetchval(f"SELECT count(*) FROM {loading}")
        await connection.execute(
            "INSERT INTO lab_loaded_day (day, manifest_sha256, loaded_at) VALUES ($1, $2, now()) "
            "ON CONFLICT (day) DO UPDATE SET manifest_sha256 = EXCLUDED.manifest_sha256, loaded_at = now()",
            day, digest or manifest_digest(folder),
        )
    return counts


async def load(*, database_url: str | None = None, root: Path | None = None, only: set[date] | None = None,
               report=print) -> list[dict]:
    """The research days not loaded yet, or written again since, oldest first. Returns what each brought."""
    import asyncpg

    connection = await asyncpg.connect(database_url or dsn())
    try:
        await ensure_schema(connection)
        loaded = {row["day"]: row["manifest_sha256"] for row in await connection.fetch("SELECT day, manifest_sha256 FROM lab_loaded_day")}
        results = []
        for day, folder in research_days(root):
            if only is not None and day not in only:
                continue
            digest = manifest_digest(folder)
            if loaded.get(day) == digest:
                continue
            counts = await load_day(connection, folder, digest)
            report(f"{day.isoformat()}: {sum(counts.values())} rows ({counts.get('price_bar_10s', 0)} bars of turbos and indices)")
            results.append({"day": day.isoformat(), "rows": counts})
        return results
    finally:
        await connection.close()


async def status(*, database_url: str | None = None) -> dict:
    """What the lab's database holds: the days loaded, and roughly how many rows of the large tables."""
    import asyncpg

    connection = await asyncpg.connect(database_url or dsn())
    try:
        await ensure_schema(connection)
        days = [row["day"] for row in await connection.fetch("SELECT day FROM lab_loaded_day ORDER BY day")]
        estimates = {
            row["relname"]: int(row["rows"])
            for row in await connection.fetch(
                "SELECT c.relname, COALESCE(sum(p.reltuples), 0) AS rows FROM pg_class c "
                "LEFT JOIN pg_inherits i ON i.inhparent = c.oid LEFT JOIN pg_class p ON p.oid = i.inhrelid "
                "WHERE c.relname = ANY($1::text[]) GROUP BY c.relname",
                list(PARTITIONED),
            )
        }
        size = await connection.fetchval("SELECT pg_database_size(current_database())")
    finally:
        await connection.close()
    return {
        "days": len(days),
        "first": days[0].isoformat() if days else None,
        "last": days[-1].isoformat() if days else None,
        "rows": estimates,
        "bytes": size,
    }


async def prune(keep_months: int, *, database_url: str | None = None, today: date | None = None) -> list[str]:
    """Drop the months of bars older than ``keep_months``: the research files stay, and can be loaded again."""
    import asyncpg

    from src.database.postgres import drop_month_partitions

    connection = await asyncpg.connect(database_url or dsn())
    try:
        dropped: list[str] = []
        kept_from = None
        for table in PARTITIONED:
            names, first_kept = await drop_month_partitions(_Executor(connection), table, today or date.today(), keep_months)
            dropped.extend(names)
            kept_from = first_kept or kept_from
        if kept_from is not None:
            # The days of the months dropped are no longer whole: loaded again if asked.
            await connection.execute("DELETE FROM lab_loaded_day WHERE day < $1", kept_from.date())
        return dropped
    finally:
        await connection.close()
