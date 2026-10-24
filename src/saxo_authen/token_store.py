"""
Where Saxo's tokens are kept, for the environment the services trade in.

- ``FileTokenStore``: the encrypted file of before (``persist_token.json``),
  read and written as it always was. Used while that file is there: until the
  migration moves it into PostgreSQL and renames it ``.imported``, and in the
  lab and the tests.
- ``DbTokenStore``: the row of ``saxo_token`` for the environment, its tokens
  sealed for the four services that log in to Saxo (src/vault/). Written with
  a compare-and-set on its generation; when PostgreSQL cannot be written, the
  renewed tokens go to a sealed spool file beside the lock, and the newer of
  the two is read (the next service that can writes it into the row): a
  refresh token is used once, so a renewal must never be lost.

The lock between the services stays the file lock of ``var/lib/saxo_auth``,
which the four of them mount: no transaction is held while Saxo answers.
SaxoAuth is called from threads, so the database is reached through a loop of
its own (``DatabaseThread``).
"""

import asyncio
import datetime
import json
import logging
import os
import stat
import threading
from dataclasses import dataclass

from src.vault import credentials as catalog
from src.vault import keys, sealed

logger = logging.getLogger(__name__)

SPOOL_NAME = "token_spool.json"
DATABASE_TIMEOUT_SECONDS = 15
# A code is claimed only while it is fresh: Saxo's own expire within minutes.
CODE_FRESH_SECONDS = 600


class TokenStoreUnavailable(Exception):
    """PostgreSQL could not be read or written: said, never a reason to read the file of before."""


@dataclass
class Tokens:
    data: dict
    generation: int


def _seconds(data: dict, key: str) -> datetime.datetime | None:
    try:
        saved = datetime.datetime.fromisoformat(data["date_saved"])
        return (saved + datetime.timedelta(seconds=data[key])).astimezone(datetime.timezone.utc)
    except (KeyError, TypeError, ValueError):
        return None


def _iso(moment) -> str | None:
    return moment.astimezone(datetime.timezone.utc).isoformat() if moment is not None else None


def token_status(row: dict | None, now: datetime.datetime | None = None) -> dict:
    """
    A row of ``saxo_token`` (or none) as the admin and the trader's watch read
    a login's state: never a token. Its margins are SaxoAuth's (2 min before
    the access token's end, 1 min before the refresh token's).
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    row = row or {}
    access, refresh = row.get("access_expires_at"), row.get("refresh_expires_at")
    return {
        "has_token": bool(row.get("has_token")),
        "saved_at": _iso(row.get("saved_at")),
        "access_token_expires_at": _iso(access),
        "refresh_token_expires_at": _iso(refresh),
        "access_token_expired": access is None or now > access - datetime.timedelta(seconds=120),
        "refresh_token_expired": refresh is None or now > refresh - datetime.timedelta(seconds=60),
        "needs_authorization_since": _iso(row.get("needs_authorization_since")),
        "last_refusal": row.get("last_refusal"),
    }


class FileTokenStore:
    """The encrypted token file of before, as SaxoAuth read and wrote it."""

    kind = "file"

    def __init__(self, token_file_path: str, cipher):
        self.token_file_path = token_file_path
        self._cipher = cipher

    def load(self) -> Tokens | None:
        try:
            with open(self.token_file_path, "rb") as token_file:
                encrypted = token_file.read()
        except FileNotFoundError:
            return None
        if not encrypted:
            return None
        try:
            return Tokens(json.loads(self._cipher.decrypt(encrypted)), 0)
        except Exception as e:
            logger.error(f"Error decrypting the token file: {type(e).__name__}")
            return None

    def save(self, data: dict, expected_generation: int, by: str) -> int:
        # Written aside then renamed: the other services never read it empty or half-written.
        temporary = f"{self.token_file_path}.tmp"
        with open(temporary, "wb") as token_file:
            token_file.write(self._cipher.encrypt(json.dumps(data).encode()))
        os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(temporary, self.token_file_path)
        return 0


class DatabaseThread:
    """A loop in a thread of its own, with one connection, for SaxoAuth's synchronous calls."""

    def __init__(self, connect):
        self._connect = connect
        self._loop: asyncio.AbstractEventLoop | None = None
        self._connection = None
        self._lock = threading.Lock()

    def _started(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is None:
                self._loop = asyncio.new_event_loop()
                threading.Thread(target=self._loop.run_forever, name="saxo-token-database", daemon=True).start()
            return self._loop

    def run(self, work, timeout: float = DATABASE_TIMEOUT_SECONDS):
        """``work(connection)``, awaited on the loop; TokenStoreUnavailable when the database fails it."""

        async def call():
            if self._connection is None or self._connection.is_closed():
                self._connection = await self._connect()
            try:
                return await work(self._connection)
            except Exception:
                try:
                    await self._connection.close()
                except Exception:
                    pass
                self._connection = None
                raise

        try:
            return asyncio.run_coroutine_threadsafe(call(), self._started()).result(timeout)
        except TokenStoreUnavailable:
            raise
        except Exception as e:
            raise TokenStoreUnavailable(type(e).__name__) from None


class DbTokenStore:
    """The row of ``saxo_token`` for ``environment``, and the authorization code of ``saxo_auth_code``."""

    kind = "database"

    def __init__(self, database: DatabaseThread, environment: str, role: str, folder: str,
                 keys_dir: str = keys.KEYS_DIR):
        self._database = database
        self.environment = environment
        self.role = role
        self._spool_path = os.path.join(folder, SPOOL_NAME)
        self._keys_dir = keys_dir

    # ── sealing ──

    def _seal(self, name: str, value: dict) -> dict:
        publics = keys.public_keys(self._keys_dir)
        missing = [role for role in catalog.SAXO_READERS if role not in publics]
        if missing:
            raise keys.KeyProblem(f"No public key for {', '.join(missing)}")
        return sealed.seal(name, value, {role: publics[role] for role in catalog.SAXO_READERS})

    def _open(self, name: str, envelope: dict) -> dict:
        return sealed.open_sealed(name, envelope, self.role, keys.private_key(self._keys_dir))

    @property
    def _token_name(self) -> str:
        return f"saxo.{self.environment}.tokens"

    # ── the spool ──

    def _spooled(self) -> Tokens | None:
        try:
            with open(self._spool_path, encoding="utf-8") as spool:
                written = json.load(spool)
        except (OSError, ValueError):
            return None
        if written.get("environment") != self.environment:
            return None
        try:
            return Tokens(self._open(self._token_name, written["sealed"]), int(written["generation"]))
        except (KeyError, TypeError, ValueError, keys.KeyProblem, sealed.SealError) as e:
            logger.error("The spooled Saxo tokens cannot be read: %s", e)
            return None

    def _spool(self, data: dict, generation: int) -> None:
        temporary = f"{self._spool_path}.tmp"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as spool:
            json.dump({"environment": self.environment, "generation": generation,
                       "sealed": self._seal(self._token_name, data)}, spool)
        os.replace(temporary, self._spool_path)

    # ── the tokens ──

    def _row(self) -> dict | None:
        async def read(connection):
            return await connection.fetchrow(
                "SELECT sealed, generation FROM saxo_token WHERE environment = $1", self.environment,
            )

        try:
            row = self._database.run(read)
        except TokenStoreUnavailable as e:
            if "UndefinedTable" in str(e):
                return None
            raise
        return dict(row) if row is not None else None

    def load(self) -> Tokens | None:
        """The newest tokens of the row and of the spool; TokenStoreUnavailable when neither can be read."""
        spooled = self._spooled()
        try:
            row = self._row()
        except TokenStoreUnavailable:
            if spooled is not None:
                return spooled
            raise
        found = None
        if row is not None and row.get("sealed"):
            envelope = row["sealed"] if isinstance(row["sealed"], dict) else json.loads(row["sealed"])
            found = Tokens(self._open(self._token_name, envelope), int(row["generation"]))
        if spooled is not None and (found is None or spooled.generation > found.generation):
            return spooled
        return found

    def save(self, data: dict, expected_generation: int, by: str) -> int:
        """
        The tokens written as the next generation, only over ``expected_generation``
        (the lock is held: another writer means something is wrong, and is said).
        The database unreachable, the spool keeps them. The generation written.
        """
        generation = expected_generation + 1
        envelope = self._seal(self._token_name, data)
        metadata = (
            datetime.datetime.now(datetime.timezone.utc), _seconds(data, "expires_in"),
            _seconds(data, "refresh_token_expires_in"),
        )

        async def write(connection):
            return await connection.execute(
                """
                INSERT INTO saxo_token (environment, sealed, generation, saved_at, access_expires_at, refresh_expires_at,
                                        saved_by, needs_authorization_since, reminded_at, last_refusal)
                VALUES ($1, $2::jsonb, $3, $4, $5, $6, $7, NULL, NULL, NULL)
                ON CONFLICT (environment) DO UPDATE SET sealed = EXCLUDED.sealed, generation = EXCLUDED.generation,
                    saved_at = EXCLUDED.saved_at, access_expires_at = EXCLUDED.access_expires_at,
                    refresh_expires_at = EXCLUDED.refresh_expires_at, saved_by = EXCLUDED.saved_by,
                    needs_authorization_since = NULL, reminded_at = NULL, last_refusal = NULL
                WHERE saxo_token.generation = $8
                """,
                self.environment, json.dumps(envelope), generation, *metadata, by, expected_generation,
            )

        try:
            status = self._database.run(write)
        except TokenStoreUnavailable as e:
            self._spool(data, generation)
            logger.error("PostgreSQL could not take the renewed Saxo tokens (%s): kept in the spool file", e)
            return generation
        if not str(status).endswith(" 1"):
            # Written by another since it was read, under the same lock: kept in the spool, said.
            self._spool(data, generation)
            logger.error("The Saxo tokens' row changed since it was read: the renewed ones are kept in the spool file")
            return generation
        try:
            os.remove(self._spool_path)
        except FileNotFoundError:
            pass
        return generation

    def flush_spool(self) -> None:
        """Under the lock: tokens left in the spool written into the row when they are the newer."""
        spooled = self._spooled()
        if spooled is None:
            return
        row = self._row()
        if row is not None and int(row["generation"]) >= spooled.generation:
            os.remove(self._spool_path)
            return
        self.save(spooled.data, spooled.generation - 1, f"{self.role} (spool)")

    # ── a login that lapsed ──

    def needs_authorization(self, refusal: str | None) -> bool:
        """Said in the row; whether this call said it first (it then tells Telegram)."""

        async def mark(connection):
            return await connection.fetchval(
                """
                INSERT INTO saxo_token (environment, generation, needs_authorization_since, last_refusal)
                VALUES ($1, 0, $2, $3)
                ON CONFLICT (environment) DO UPDATE SET
                    needs_authorization_since = COALESCE(saxo_token.needs_authorization_since, EXCLUDED.needs_authorization_since),
                    last_refusal = COALESCE(EXCLUDED.last_refusal, saxo_token.last_refusal)
                RETURNING needs_authorization_since = $2
                """,
                self.environment, datetime.datetime.now(datetime.timezone.utc), refusal,
            )

        return bool(self._database.run(mark))

    def reminder_due(self, every_seconds: float) -> bool:
        """Whether a reminder is due now (it is then taken by this call, for no other service to send it)."""
        now = datetime.datetime.now(datetime.timezone.utc)

        async def take(connection):
            return await connection.fetchval(
                """
                UPDATE saxo_token SET reminded_at = $2
                WHERE environment = $1 AND needs_authorization_since IS NOT NULL
                  AND COALESCE(reminded_at, needs_authorization_since) <= $3
                RETURNING true
                """,
                self.environment, now, now - datetime.timedelta(seconds=every_seconds),
            )

        return bool(self._database.run(take))

    def claim_code(self) -> dict | None:
        """The code handed over for this environment, taken once by one service: {code, redirect_uri}."""
        now = datetime.datetime.now(datetime.timezone.utc)

        async def claim(connection):
            return await connection.fetchrow(
                """
                UPDATE saxo_auth_code SET consumed_at = $2, consumed_by = $3
                WHERE environment = $1 AND sealed IS NOT NULL AND consumed_at IS NULL AND submitted_at >= $4
                RETURNING sealed
                """,
                self.environment, now, self.role, now - datetime.timedelta(seconds=CODE_FRESH_SECONDS),
            )

        row = self._database.run(claim)
        if row is None:
            return None
        envelope = row["sealed"] if isinstance(row["sealed"], dict) else json.loads(row["sealed"])
        return self._open(f"saxo.{self.environment}.code", envelope)

    def status(self) -> dict:
        """What the row says of the tokens, without them: the shape of SaxoAuth.get_token_status."""

        async def read(connection):
            return await connection.fetchrow(
                """
                SELECT sealed IS NOT NULL AS has_token, saved_at, access_expires_at, refresh_expires_at,
                       needs_authorization_since, last_refusal
                FROM saxo_token WHERE environment = $1
                """,
                self.environment,
            )

        try:
            row = self._database.run(read)
        except TokenStoreUnavailable as e:
            if "UndefinedTable" not in str(e):
                raise
            row = None
        return token_status(dict(row) if row is not None else None)

    def note_refusal(self, refusal: str) -> None:
        async def note(connection):
            await connection.execute(
                "UPDATE saxo_token SET last_refusal = $2 WHERE environment = $1", self.environment, refusal[:500],
            )

        try:
            self._database.run(note)
        except TokenStoreUnavailable:
            pass
