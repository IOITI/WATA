"""
The credentials' tables (``VAULT_SCHEMA_SQL`` in src/database/postgres.py):
``credential``, a sealed envelope per credential with what is shown of it,
and ``credential_check``, what each service last saw when it used one.

Each function takes anything with asyncpg's ``fetch``/``execute`` (a
connection, a pool, the admin's databases). asyncpg is never imported here: a
table not created yet is told by its SQLSTATE.
"""

import json
from datetime import datetime, timezone

UNDEFINED_TABLE = "42P01"


def _missing_table(error: Exception) -> bool:
    return getattr(error, "sqlstate", None) == UNDEFINED_TABLE


def _json(value):
    if value is None or isinstance(value, (dict, list)):
        return value
    return json.loads(value)


async def read(db, names) -> dict[str, dict]:
    """The rows of ``names`` found, by name, envelope included: none when the table is not there yet."""
    try:
        rows = await db.fetch(
            "SELECT name, public, sealed, readers, fingerprint, set_at, set_by FROM credential WHERE name = ANY($1::text[])",
            list(names),
        )
    except Exception as e:
        if _missing_table(e):
            return {}
        raise
    return {
        row["name"]: {
            "public": _json(row["public"]) or {},
            "sealed": _json(row["sealed"]),
            "readers": list(row["readers"] or []),
            "fingerprint": row["fingerprint"],
            "set_at": row["set_at"],
            "set_by": row["set_by"],
        }
        for row in rows
    }


async def listed(db) -> list[dict]:
    """Every row without its envelope: what the admin shows."""
    try:
        rows = await db.fetch("SELECT name, public, readers, fingerprint, set_at, set_by FROM credential ORDER BY name")
    except Exception as e:
        if _missing_table(e):
            return []
        raise
    return [
        {
            "name": row["name"],
            "public": _json(row["public"]) or {},
            "readers": list(row["readers"] or []),
            "fingerprint": row["fingerprint"],
            "set_at": row["set_at"],
            "set_by": row["set_by"],
        }
        for row in rows
    ]


async def write(db, name: str, *, public: dict, sealed: dict, readers, fingerprint: str | None, by: str | None) -> None:
    await db.execute(
        """
        INSERT INTO credential (name, public, sealed, readers, fingerprint, set_at, set_by)
        VALUES ($1, $2::jsonb, $3::jsonb, $4::text[], $5, $6, $7)
        ON CONFLICT (name) DO UPDATE SET public = EXCLUDED.public, sealed = EXCLUDED.sealed,
            readers = EXCLUDED.readers, fingerprint = EXCLUDED.fingerprint, set_at = EXCLUDED.set_at,
            set_by = EXCLUDED.set_by
        """,
        name, json.dumps(public), json.dumps(sealed), list(readers), fingerprint, datetime.now(timezone.utc), by,
    )


async def insert_missing(db, name: str, *, public: dict, sealed: dict, readers, fingerprint: str | None, by: str) -> bool:
    """Write ``name`` only if it has no row yet (the migration): whether it was written."""
    status = await db.execute(
        """
        INSERT INTO credential (name, public, sealed, readers, fingerprint, set_at, set_by)
        VALUES ($1, $2::jsonb, $3::jsonb, $4::text[], $5, $6, $7)
        ON CONFLICT (name) DO NOTHING
        """,
        name, json.dumps(public), json.dumps(sealed), list(readers), fingerprint, datetime.now(timezone.utc), by,
    )
    return str(status).endswith(" 1")


async def remove(db, name: str) -> bool:
    status = await db.execute("DELETE FROM credential WHERE name = $1", name)
    await db.execute("DELETE FROM credential_check WHERE name = $1", name)
    return str(status).endswith(" 1")


async def record_check(db, name: str, role: str, ok: bool, detail: str | None) -> None:
    """What ``role`` saw when it used ``name`` (a login, a message sent, a ping); never a value."""
    await db.execute(
        """
        INSERT INTO credential_check (name, role, checked_at, ok, detail) VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (name, role) DO UPDATE SET checked_at = EXCLUDED.checked_at, ok = EXCLUDED.ok, detail = EXCLUDED.detail
        """,
        name, role, datetime.now(timezone.utc), ok, (detail or "")[:500] or None,
    )


async def checks(db) -> list[dict]:
    try:
        rows = await db.fetch("SELECT name, role, checked_at, ok, detail FROM credential_check ORDER BY name, role")
    except Exception as e:
        if _missing_table(e):
            return []
        raise
    return [dict(row) for row in rows]
