"""
What a service reads of its credentials when it starts.

The rows it is a reader of are read through one short connection (or the
service's pool), opened with its own key, laid over the configuration in
memory (so that the code reading ``telegram.bot_token`` there reads it as
before) and given to the log redaction. A row absent is read at its place of
before, said once in the log. A row that cannot be opened is said as an error
and the credential taken as missing, never read elsewhere: a value changed in
PostgreSQL must not be bypassed by an older one. A service missing one does
without it and says so where it would use it (the web server refuses, the
trader's polling is refused and its watch says so, the feed idles), rather
than stopping and starting again.

The database not reachable at a start is waited for, said at each try; the
services read their credentials once, at their start: a credential changed
applies at their next start (the admin restarts them).
"""

import asyncio
import logging
import os
import time

from src.logging_helper.redaction import register_secret
from src.vault import credentials as catalog
from src.vault import keys, sealed, store

logger = logging.getLogger(__name__)

DATABASE, OLD_PLACE, MISSING, UNREADABLE = "database", "old place", "missing", "unreadable"
# The waits between tries while the database cannot be read at a start, then the last one again.
RETRY_SECONDS = (2, 5, 10, 20, 30)
CONNECT_TIMEOUT_SECONDS = 10

_loaded: dict[str, dict] = {}
_sources: dict[str, str] = {}


class CredentialsUnavailable(Exception):
    """PostgreSQL could not be read: tried again."""


def credential(name: str) -> dict | None:
    """The value of ``name`` as this service read it at its start (or last asked), None when it has none."""
    return _loaded.get(name)


def source(name: str) -> str | None:
    """Where ``name`` was read: the database, its old place, or missing."""
    return _sources.get(name)


def forget() -> None:
    """For the tests."""
    _loaded.clear()
    _sources.clear()


async def _connect(dsn: str):
    import asyncpg

    return await asyncpg.connect(dsn, timeout=CONNECT_TIMEOUT_SECONDS)


def _register(credential: catalog.Credential, value: dict) -> None:
    for part in credential.parts:
        if part.secret:
            register_secret(f"{credential.name}.{part.key}", value.get(part.key))
    # A token rotated: the one before, still accepted for a while, is a secret too.
    register_secret(f"{credential.name}.previous", value.get("previous"))


async def _rows(names: list[str], dsn: str, db, connect) -> dict[str, dict]:
    if db is not None:
        try:
            return await store.read(db, names)
        except Exception as e:
            raise CredentialsUnavailable(type(e).__name__) from None
    try:
        connection = await (connect or _connect)(dsn)
    except Exception as e:
        raise CredentialsUnavailable(type(e).__name__) from None
    try:
        return await store.read(connection, names)
    except Exception as e:
        raise CredentialsUnavailable(type(e).__name__) from None
    finally:
        try:
            await connection.close()
        except Exception:
            pass


async def load(config_manager, role: str | None = None, *, names=None, db=None, connect=None,
               keys_dir: str = keys.KEYS_DIR, environ=None) -> dict[str, dict]:
    """
    The credentials of ``role`` (WATA_APP_ROLE when not given), or of
    ``names`` among them, by name; read through ``db`` (the service's pool)
    when given, else a connection of their own.
    """
    role = role or os.environ.get("WATA_APP_ROLE", "")
    wanted = [credential for credential in catalog.of_role(role) if names is None or credential.name in names]
    if not wanted:
        return {}
    rows = await _rows([credential.name for credential in wanted], config_manager.get_config_value("postgresql.dsn"), db,
                       connect)
    private = None
    found: dict[str, dict] = {}
    said = []
    for credential in wanted:
        row = rows.get(credential.name)
        if row is not None:
            try:
                if private is None:
                    private = keys.private_key(keys_dir)
                value = sealed.open_sealed(credential.name, row["sealed"], role, private)
                where = DATABASE
            except (keys.KeyProblem, sealed.SealError) as e:
                logger.error("%s cannot be opened by the %s service: %s. It does without it until it can.",
                             credential.label, role, e)
                value, where = None, UNREADABLE
        else:
            value = catalog.old_value(credential.name, config_manager, environ)
            where = OLD_PLACE if value else MISSING
            if value:
                logger.warning("%s is not in PostgreSQL yet: read from %s", credential.label, credential.old_place)
        _sources[credential.name] = where
        said.append(f"{credential.name} from the {where}" if where in (DATABASE, OLD_PLACE) else f"{credential.name} {where}")
        if not value:
            _loaded.pop(credential.name, None)
            continue
        _register(credential, value)
        overlay = {path: value[key] for key, path in credential.overlay.items() if key in value}
        if overlay:
            config_manager.apply_credentials(overlay)
        found[credential.name] = value
    _loaded.update(found)
    logger.info("Credentials of the %s service: %s", role, ", ".join(said))
    return found


async def load_waiting(config_manager, role: str | None = None, *, sleep=asyncio.sleep, give_up_after: float | None = None,
                       stopped=None, **kwargs) -> dict[str, dict]:
    """
    ``load``, tried again while the database cannot be read; after
    ``give_up_after`` seconds of waits, given up (said), for a service whose
    other work must not wait for it; at once when ``stopped()`` (Docker's
    stop), which is asked every half second of a wait.
    """
    attempt, waited = 0, 0.0
    while True:
        try:
            return await load(config_manager, role, **kwargs)
        except CredentialsUnavailable as e:
            wait = RETRY_SECONDS[min(attempt, len(RETRY_SECONDS) - 1)]
            if give_up_after is not None and waited + wait > give_up_after:
                logger.error("PostgreSQL could not be read for the credentials (%s): going on without them", e)
                return {}
            logger.warning("PostgreSQL cannot be read for the credentials (%s): trying again in %d s", e, wait)
            attempt += 1
            waited += wait
            for _ in range(int(wait * 2)):
                if stopped is not None and stopped():
                    return {}
                await sleep(0.5)


def load_blocking(config_manager, role: str | None = None, **kwargs) -> dict[str, dict]:
    """``load_waiting``, for the services that are not asynchronous (the scheduler, the telegram service)."""
    return asyncio.run(load_waiting(config_manager, role, **kwargs))


class CheckReporter:
    """
    What a service saw when it used a credential, written to ``credential_check``
    when it changes and at most every ``every_seconds`` otherwise. In the
    background and never raising: a check that cannot be written costs nothing.
    """

    def __init__(self, write, role: str | None = None, every_seconds: float = 600.0, clock=time.monotonic):
        # write(name, role, ok, detail): awaitable, e.g. store.record_check over the service's pool.
        self._write = write
        self._role = role or os.environ.get("WATA_APP_ROLE", "")
        self._every = every_seconds
        self._clock = clock
        self._last: dict[str, tuple[bool, str | None, float]] = {}
        self._tasks: set = set()

    def due(self, name: str, ok: bool, detail: str | None) -> bool:
        last = self._last.get(name)
        return last is None or (last[0], last[1]) != (ok, detail) or self._clock() - last[2] >= self._every

    def report(self, name: str, ok: bool, detail: str | None = None) -> None:
        if not self.due(name, ok, detail):
            return
        self._last[name] = (ok, detail, self._clock())
        try:
            task = asyncio.get_running_loop().create_task(self._safely(name, ok, detail))
        except RuntimeError:
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _safely(self, name: str, ok: bool, detail: str | None) -> None:
        try:
            await asyncio.wait_for(self._write(name, self._role, ok, detail), 10)
        except Exception as e:
            logger.debug("Could not write the check of %s: %r", name, e)
