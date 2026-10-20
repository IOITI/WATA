"""
The tokens the MCP endpoint is opened with.

The operator makes one on the server (``watamcptoken create <name>``, which runs
``python -m src.admin.cli --mcp-token create <name>`` in the admin's container).
It is printed once; the file keeps only its sha256, beside the admin's
credentials and like them readable by the owner alone. A token is
``wata_mcp_<id>_<secret>``: the id says which entry to compare with, the
secret (256 bits) is what is compared.

The admin reads the file again whenever it changes, so a token made or revoked
by the command line counts at once. A file it cannot read lets no token in, and
is never written over: the operator moves it away to start again.
"""

import fcntl
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

TOKEN_FORMAT = re.compile(r"^wata_mcp_([0-9a-f]{8})_([A-Za-z0-9_-]{43})$")
NAME_FORMAT = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
_ID_FORMAT = re.compile(r"^[0-9a-f]{8}$")
_DIGEST_FORMAT = re.compile(r"^[0-9a-f]{64}$")
_STAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

DEFAULT_DAYS = 90
MAX_DAYS = 365
# Tokens that still open the endpoint, at most: one per machine is the use.
MAX_ACTIVE = 10
FILE_FORMAT = 1

ACTIVE, EXPIRED, REVOKED = "active", "expired", "revoked"


class TokenError(ValueError):
    """A token that cannot be made or revoked as asked, said in a sentence for the operator."""


@dataclass(frozen=True)
class Caller:
    """Who a request to /mcp comes from: the token it was opened with."""

    name: str
    token_id: str
    expires_at: str


def _stamp(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).strftime(_STAMP_FORMAT)


def _seconds(stamp) -> float | None:
    try:
        return datetime.strptime(stamp, _STAMP_FORMAT).replace(tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _checked(record) -> list[dict]:
    """The entries of the file, or ValueError: a file that is not exactly what this code writes is refused whole."""
    if not isinstance(record, dict) or record.get("format") != FILE_FORMAT or not isinstance(record.get("tokens"), list):
        raise ValueError("not a token file of this version")
    entries = []
    seen = set()
    for entry in record["tokens"]:
        if not isinstance(entry, dict):
            raise ValueError("an entry is not an object")
        if not isinstance(entry.get("id"), str) or not _ID_FORMAT.match(entry["id"]) or entry["id"] in seen:
            raise ValueError("an entry has no valid id, or shares it")
        if not isinstance(entry.get("name"), str) or not NAME_FORMAT.match(entry["name"]):
            raise ValueError(f"entry {entry['id']} has no valid name")
        if not isinstance(entry.get("sha256"), str) or not _DIGEST_FORMAT.match(entry["sha256"]):
            raise ValueError(f"entry {entry['id']} has no valid digest")
        for key in ("created_at", "expires_at"):
            if _seconds(entry.get(key)) is None:
                raise ValueError(f"entry {entry['id']} has no valid {key}")
        if entry.get("revoked_at") is not None and _seconds(entry["revoked_at"]) is None:
            raise ValueError(f"entry {entry['id']} has an invalid revoked_at")
        seen.add(entry["id"])
        entries.append({key: entry.get(key) for key in ("id", "name", "sha256", "created_at", "expires_at", "revoked_at")})
    return entries


class TokenStore:
    def __init__(self, path: str, clock=time.time):
        self.path = path
        self._clock = clock
        # What was read, and the file's (mtime, size, inode) when it was: read again when that changes.
        self._signature = None
        self._entries: list[dict] | None = []
        self._reported = None
        # When each token last opened the endpoint, as this process saw it. Not written:
        # the command line writes the file too, and the two must never race on it.
        self._last_used: dict[str, float] = {}

    # ── reading ──

    def _load(self) -> list[dict] | None:
        """The entries, or None when the file cannot be read (then no token is valid)."""
        try:
            stat = os.stat(self.path)
        except FileNotFoundError:
            self._signature, self._entries = None, []
            return self._entries
        except OSError as e:
            self._report(("stat", str(e)), f"Cannot read the MCP token file {self.path}: {e}")
            return None
        signature = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
        if signature == self._signature:
            return self._entries
        try:
            with open(self.path, encoding="utf-8") as token_file:
                entries = _checked(json.load(token_file))
        except (OSError, ValueError) as e:
            self._report(signature, f"The MCP token file {self.path} cannot be used ({e}): no token is accepted")
            self._signature, self._entries = signature, None
            return None
        self._signature, self._entries = signature, entries
        return entries

    def _report(self, key, message: str) -> None:
        if key != self._reported:
            self._reported = key
            logger.error(message)

    def readable(self) -> bool:
        return self._load() is not None

    def _state(self, entry: dict, now: float) -> str:
        if entry["revoked_at"] is not None:
            return REVOKED
        return ACTIVE if now < _seconds(entry["expires_at"]) else EXPIRED

    def verify(self, presented: str | None) -> Caller | None:
        """The caller a presented token stands for, or None. Nothing is said of why."""
        match = TOKEN_FORMAT.match(presented or "")
        if match is None:
            return None
        entries = self._load()
        if not entries:
            return None
        wanted = digest(presented)
        now = self._clock()
        for entry in entries:
            if entry["id"] != match.group(1):
                continue
            if not hmac.compare_digest(entry["sha256"], wanted) or self._state(entry, now) != ACTIVE:
                return None
            self._last_used[entry["id"]] = now
            return Caller(name=entry["name"], token_id=entry["id"], expires_at=entry["expires_at"])
        return None

    def entries(self) -> list[dict]:
        """Every token as the operator sees it: never its digest."""
        entries = self._load()
        if entries is None:
            return []
        now = self._clock()
        return [
            {
                "id": entry["id"],
                "name": entry["name"],
                "created_at": entry["created_at"],
                "expires_at": entry["expires_at"],
                "revoked_at": entry["revoked_at"],
                "state": self._state(entry, now),
                "last_used_at": _stamp(self._last_used[entry["id"]]) if entry["id"] in self._last_used else None,
            }
            for entry in entries
        ]

    # ── writing (the command line, and a revocation from the System page) ──

    def _change(self, change):
        """Read, change and write the file whole, holding a lock both processes take."""
        folder = os.path.dirname(self.path) or "."
        os.makedirs(folder, exist_ok=True)
        lock_descriptor = os.open(f"{self.path}.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
            try:
                with open(self.path, encoding="utf-8") as token_file:
                    entries = _checked(json.load(token_file))
            except FileNotFoundError:
                entries = []
            except (OSError, ValueError) as e:
                raise TokenError(f"{self.path} cannot be used ({e}): move it away to start again") from None
            result = change(entries)
            temp_path = f"{self.path}.tmp"
            descriptor = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as token_file:
                json.dump({"format": FILE_FORMAT, "tokens": entries}, token_file, indent=1)
            os.replace(temp_path, self.path)
        finally:
            fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
            os.close(lock_descriptor)
        return result

    def create(self, name: str, days: int = DEFAULT_DAYS) -> tuple[str, dict]:
        """A new token for ``name``, valid ``days`` days: (the token, to show once; its entry)."""
        if not isinstance(name, str) or not NAME_FORMAT.match(name):
            raise TokenError("A token's name is 1 to 40 lowercase letters, digits or dashes, such as claude-mac")
        if not isinstance(days, int) or not 1 <= days <= MAX_DAYS:
            raise TokenError(f"A token lasts 1 to {MAX_DAYS} days")
        now = self._clock()

        def add(entries: list[dict]):
            active = [entry for entry in entries if self._state(entry, now) == ACTIVE]
            if any(entry["name"] == name for entry in active):
                raise TokenError(f"A token named {name} is still valid: revoke it first")
            if len(active) >= MAX_ACTIVE:
                raise TokenError(f"{MAX_ACTIVE} tokens are valid already: revoke one first")
            token_id = secrets.token_hex(4)
            while any(entry["id"] == token_id for entry in entries):
                token_id = secrets.token_hex(4)
            token = f"wata_mcp_{token_id}_{secrets.token_urlsafe(32)}"
            entry = {
                "id": token_id, "name": name, "sha256": digest(token), "created_at": _stamp(now),
                "expires_at": _stamp(now + days * 86_400), "revoked_at": None,
            }
            entries.append(entry)
            return token, {key: value for key, value in entry.items() if key != "sha256"}

        return self._change(add)

    def revoke(self, name_or_id: str) -> list[dict]:
        """Revoke the valid tokens of that name, or the one of that id; the entries revoked."""
        now = self._clock()

        def mark(entries: list[dict]):
            found = [
                entry for entry in entries
                if name_or_id in (entry["name"], entry["id"]) and self._state(entry, now) == ACTIVE
            ]
            if not found:
                raise TokenError(f"No valid token is named {name_or_id} or has that id")
            for entry in found:
                entry["revoked_at"] = _stamp(now)
            return [{key: value for key, value in entry.items() if key != "sha256"} for entry in found]

        return self._change(mark)
