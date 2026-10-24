"""
The tokens of Claude's MCP endpoint, kept in PostgreSQL (table ``mcp_token``):
their sha256, never a token, as the file of before kept them
(src/admin/mcp_server/tokens.py, whose format and checks are the same here).

The gate checks a token against what was last read, every few seconds
(``keep_fresh``): a token revoked from the page counts at once, one revoked
from the command line within seconds. A table that cannot be read lets no
token in. When a token opens the endpoint, its last use is written at most
once a minute. The file of before is imported the first time the table is
empty, then renamed ``.imported``.

Outside src/admin/mcp_server on purpose: the endpoint's code never imports the
database (tests/test_mcp_boundary.py); it is handed this object.
"""

import asyncio
import hmac
import json
import logging
import os
import secrets
import time
from datetime import datetime, timezone

from src.admin.mcp_server.tokens import (
    ACTIVE,
    DEFAULT_DAYS,
    EXPIRED,
    MAX_ACTIVE,
    MAX_DAYS,
    NAME_FORMAT,
    REVOKED,
    TOKEN_FORMAT,
    Caller,
    TokenError,
    _checked,
    digest,
)

logger = logging.getLogger(__name__)

REFRESH_SECONDS = 5
LAST_USE_WRITE_SECONDS = 60
_STAMP = "%Y-%m-%dT%H:%M:%SZ"


def _stamp(moment) -> str | None:
    if moment is None:
        return None
    if isinstance(moment, (int, float)):
        moment = datetime.fromtimestamp(moment, timezone.utc)
    return moment.astimezone(timezone.utc).strftime(_STAMP)


def _instant(stamp: str) -> datetime:
    return datetime.strptime(stamp, _STAMP).replace(tzinfo=timezone.utc)


class DbTokenStore:
    def __init__(self, *, reader, writer, legacy_path: str | None = None, clock=time.time):
        self._reader = reader
        self._writer = writer
        self._legacy_path = legacy_path
        self._clock = clock
        # What was last read; None when the table could not be read (then no token is valid).
        self._entries: list[dict] | None = []
        self._last_used: dict[str, float] = {}
        self._last_written: dict[str, float] = {}
        self._tasks: set = set()
        self._imported = False
        self._schema_ready = False

    # ── reading ──

    async def refresh(self) -> None:
        try:
            if not self._schema_ready:
                from src.database.postgres import VAULT_SCHEMA_SQL

                # The trading services create it too; the admin may start first.
                await self._writer.execute(VAULT_SCHEMA_SQL)
                self._schema_ready = True
            await self._import_legacy()
            rows = await self._reader.fetch(
                "SELECT id, name, sha256, created_at, expires_at, revoked_at, last_used_at FROM mcp_token ORDER BY created_at"
            )
        except Exception as e:
            if getattr(e, "sqlstate", None) == "42P01":
                self._entries = []
                return
            if self._entries is not None:
                logger.error("The MCP tokens cannot be read (%s): no token is accepted", type(e).__name__)
            self._entries = None
            return
        entries = []
        for row in rows:
            entry = {
                "id": row["id"], "name": row["name"], "sha256": row["sha256"].strip(),
                "created_at": _stamp(row["created_at"]), "expires_at": _stamp(row["expires_at"]),
                "revoked_at": _stamp(row["revoked_at"]),
            }
            if row["last_used_at"] is not None:
                self._last_used.setdefault(row["id"], row["last_used_at"].timestamp())
            entries.append(entry)
        self._entries = entries

    async def keep_fresh(self, every: float = REFRESH_SECONDS) -> None:
        """Until cancelled: read the tokens again every ``every`` seconds."""
        while True:
            await self.refresh()
            await asyncio.sleep(every)

    async def _import_legacy(self) -> None:
        """The file of before, written into the empty table once, then put aside."""
        if self._imported or not self._legacy_path or not os.path.exists(self._legacy_path):
            self._imported = True
            return
        if await self._reader.fetchval("SELECT count(*) FROM mcp_token"):
            self._imported = True
            return
        with open(self._legacy_path, encoding="utf-8") as token_file:
            entries = _checked(json.load(token_file))
        for entry in entries:
            await self._writer.execute(
                "INSERT INTO mcp_token (id, name, sha256, created_at, expires_at, revoked_at) "
                "VALUES ($1, $2, $3, $4, $5, $6) ON CONFLICT (id) DO NOTHING",
                entry["id"], entry["name"], entry["sha256"], _instant(entry["created_at"]), _instant(entry["expires_at"]),
                _instant(entry["revoked_at"]) if entry["revoked_at"] else None,
            )
        os.replace(self._legacy_path, f"{self._legacy_path}.imported")
        logger.info("%d MCP token(s) of %s moved into PostgreSQL", len(entries), self._legacy_path)
        self._imported = True

    def readable(self) -> bool:
        return self._entries is not None

    def _state(self, entry: dict, now: float) -> str:
        if entry["revoked_at"] is not None:
            return REVOKED
        return ACTIVE if now < _instant(entry["expires_at"]).timestamp() else EXPIRED

    def verify(self, presented: str | None) -> Caller | None:
        """The caller a presented token stands for, or None. Nothing is said of why."""
        match = TOKEN_FORMAT.match(presented or "")
        if match is None or not self._entries:
            return None
        wanted = digest(presented)
        now = self._clock()
        for entry in self._entries:
            if entry["id"] != match.group(1):
                continue
            if not hmac.compare_digest(entry["sha256"], wanted) or self._state(entry, now) != ACTIVE:
                return None
            self._last_used[entry["id"]] = now
            self._write_last_use(entry["id"], now)
            return Caller(name=entry["name"], token_id=entry["id"], expires_at=entry["expires_at"])
        return None

    def _write_last_use(self, token_id: str, now: float) -> None:
        if now - self._last_written.get(token_id, 0) < LAST_USE_WRITE_SECONDS:
            return
        self._last_written[token_id] = now

        async def write():
            try:
                await self._writer.execute(
                    "UPDATE mcp_token SET last_used_at = $2 WHERE id = $1", token_id, datetime.fromtimestamp(now, timezone.utc),
                )
            except Exception as e:
                logger.debug("Could not write a token's last use: %r", e)

        try:
            task = asyncio.get_running_loop().create_task(write())
        except RuntimeError:
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def entries(self) -> list[dict]:
        """Every token as the operator sees it: never its digest."""
        if self._entries is None:
            return []
        now = self._clock()
        return [
            {
                "id": entry["id"], "name": entry["name"], "created_at": entry["created_at"],
                "expires_at": entry["expires_at"], "revoked_at": entry["revoked_at"], "state": self._state(entry, now),
                "last_used_at": _stamp(self._last_used.get(entry["id"])),
            }
            for entry in self._entries
        ]

    # ── writing (the page, the command line) ──

    async def create(self, name: str, days: int = DEFAULT_DAYS) -> tuple[str, dict]:
        """A new token for ``name``, valid ``days`` days: (the token, to show once; its entry)."""
        if not isinstance(name, str) or not NAME_FORMAT.match(name):
            raise TokenError("A token's name is 1 to 40 lowercase letters, digits or dashes, such as claude-mac")
        if not isinstance(days, int) or not 1 <= days <= MAX_DAYS:
            raise TokenError(f"A token lasts 1 to {MAX_DAYS} days")
        await self.refresh()
        if self._entries is None:
            raise TokenError("The tokens cannot be read now: try again in a moment")
        now = self._clock()
        active = [entry for entry in self._entries if self._state(entry, now) == ACTIVE]
        if any(entry["name"] == name for entry in active):
            raise TokenError(f"A token named {name} is still valid: revoke it first")
        if len(active) >= MAX_ACTIVE:
            raise TokenError(f"{MAX_ACTIVE} tokens are valid already: revoke one first")
        known = {entry["id"] for entry in self._entries}
        token_id = secrets.token_hex(4)
        while token_id in known:
            token_id = secrets.token_hex(4)
        token = f"wata_mcp_{token_id}_{secrets.token_urlsafe(32)}"
        created, expires = datetime.fromtimestamp(now, timezone.utc), datetime.fromtimestamp(now + days * 86_400, timezone.utc)
        await self._writer.execute(
            "INSERT INTO mcp_token (id, name, sha256, created_at, expires_at) VALUES ($1, $2, $3, $4, $5)",
            token_id, name, digest(token), created, expires,
        )
        await self.refresh()
        return token, {
            "id": token_id, "name": name, "created_at": _stamp(created), "expires_at": _stamp(expires), "revoked_at": None,
        }

    async def revoke(self, name_or_id: str) -> list[dict]:
        """Revoke the valid tokens of that name, or the one of that id; the entries revoked."""
        now = datetime.fromtimestamp(self._clock(), timezone.utc)
        rows = await self._writer.fetch(
            "UPDATE mcp_token SET revoked_at = $2 WHERE ($1 = name OR $1 = id) AND revoked_at IS NULL AND expires_at > $2 "
            "RETURNING id, name, created_at, expires_at, revoked_at",
            name_or_id, now,
        )
        if not rows:
            raise TokenError(f"No valid token is named {name_or_id} or has that id")
        await self.refresh()
        return [
            {"id": row["id"], "name": row["name"], "created_at": _stamp(row["created_at"]),
             "expires_at": _stamp(row["expires_at"]), "revoked_at": _stamp(row["revoked_at"])}
            for row in rows
        ]
