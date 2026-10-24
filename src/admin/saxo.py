"""
Saxo's login as the admin sees it and hands it over (``ctx.saxo``).

The admin is no reader of Saxo's apps nor of its tokens: it reads what the
row of ``saxo_token`` says of them (when they were saved, when they end, since
when the login lapsed), the apps' public parts (their keys), and writes a
login's code, sealed for the four services that log in to Saxo
(``saxo_auth_code``). One of them takes it within seconds and exchanges it.

A login is asked with a link the admin makes: its ``state`` is random, kept
hashed for ten minutes and used once, checked when Saxo sends the browser
back to the admin (``/api/saxo/callback``, the server's redirect) or when the
address is pasted. A code pasted alone is taken as before, without a state.
Saxo's addresses are the constants of src/saxo_authen/endpoints.py.
"""

import asyncio
import hashlib
import json
import secrets
import urllib.parse
from datetime import datetime, timedelta, timezone

from starlette.concurrency import run_in_threadpool

from src.admin.errors import InvalidRequest, NotPossible
from src.saxo_authen import endpoints
from src.saxo_authen.token_store import token_status
from src.vault import credentials as catalog
from src.vault import keys, sealed, store

STATE_SECONDS = 600
MISSING_TABLE = "42P01"


async def called(method, *args):
    """A method of ``ctx.saxo``: awaited when it is this one's, in a thread for a synchronous stand-in."""
    if asyncio.iscoroutinefunction(method):
        return await method(*args)
    return await run_in_threadpool(method, *args)


def _digest(state: str) -> str:
    return hashlib.sha256(state.encode()).hexdigest()


class AdminSaxo:
    def __init__(self, *, reader, writer, config_manager, keys_dir: str = keys.KEYS_DIR, environ=None):
        self._reader = reader
        self._writer = writer
        self._config_manager = config_manager
        self._keys_dir = keys_dir
        self._environ = environ

    def environment(self) -> str:
        return endpoints.environment_of(self._config_manager)

    def _checked_environment(self, environment: str | None) -> str:
        environment = environment or self.environment()
        if environment not in endpoints.ENVIRONMENTS:
            raise InvalidRequest(f"Saxo's environments are {', '.join(endpoints.ENVIRONMENTS)}")
        return environment

    async def _token_row(self, environment: str) -> dict | None:
        try:
            row = await self._reader.fetchrow(
                """
                SELECT sealed IS NOT NULL AS has_token, saved_at, access_expires_at, refresh_expires_at,
                       needs_authorization_since, last_refusal, saved_by
                FROM saxo_token WHERE environment = $1
                """,
                environment,
            )
        except Exception as e:
            if getattr(e, "sqlstate", None) == MISSING_TABLE:
                return None
            raise
        return dict(row) if row is not None else None

    async def _apps(self) -> dict:
        rows = {row["name"]: row for row in await store.listed(self._reader)}
        apps = {}
        for environment in endpoints.ENVIRONMENTS:
            row = rows.get(f"saxo.{environment}.app")
            apps[environment] = {
                "set": row is not None,
                "app_key": (row or {}).get("public", {}).get("app_key"),
                "fingerprint": (row or {}).get("fingerprint"),
            }
        return apps

    async def status(self) -> dict:
        """The login's state for the environment the services trade in, and what its link needs."""
        environment = self.environment()
        row = await self._token_row(environment)
        redirect = endpoints.redirect_url(self._config_manager, self._environ)
        return {
            **token_status(row),
            "environment": environment,
            # database: the row is the services'; none: no row yet, the services may still read the token file of
            # before, which the admin no longer reads.
            "where": "database" if row is not None else "none",
            "saved_by": (row or {}).get("saved_by"),
            "redirect_url": redirect,
            "callback": bool(redirect and redirect.rstrip("/").endswith(endpoints.CALLBACK_PATH)),
            "apps": await self._apps(),
            # Made on request, with a state of its own (authorize).
            "authorization_url": None,
        }

    async def _app_key(self, environment: str) -> str:
        app = (await self._apps())[environment]
        if app["app_key"]:
            return app["app_key"]
        old = catalog.old_value(f"saxo.{environment}.app", self._config_manager, {})
        if old:
            return old["app_key"]
        raise NotPossible(f"No Saxo {environment} app is set: set it in Configuration, Credentials")

    async def authorize(self, environment: str | None = None) -> str:
        """Saxo's login page for ``environment``, with a state kept hashed for ten minutes."""
        environment = self._checked_environment(environment)
        redirect = endpoints.redirect_url(self._config_manager, self._environ)
        if not redirect:
            raise NotPossible("No redirect address: SAXO_REDIRECT_URL in .env, or the app's in config.json")
        app_key = await self._app_key(environment)
        state = secrets.token_urlsafe(24)
        from src.database.postgres import VAULT_SCHEMA_SQL

        await self._writer.execute(VAULT_SCHEMA_SQL)
        await self._writer.execute(
            """
            INSERT INTO saxo_auth_code (environment, state_sha256, state_expires_at) VALUES ($1, $2, $3)
            ON CONFLICT (environment) DO UPDATE SET state_sha256 = EXCLUDED.state_sha256,
                state_expires_at = EXCLUDED.state_expires_at
            """,
            environment, _digest(state), datetime.now(timezone.utc) + timedelta(seconds=STATE_SECONDS),
        )
        query = urllib.parse.urlencode({
            "response_type": "code", "client_id": app_key, "redirect_uri": redirect, "state": state,
        })
        return f"{endpoints.ENDPOINTS[environment]['authorize']}?{query}"

    async def _state_environment(self, state: str) -> str | None:
        """The environment a state was made for, used up; None when it is unknown, used or too old."""
        row = await self._writer.fetchrow(
            """
            UPDATE saxo_auth_code SET state_sha256 = NULL, state_expires_at = NULL
            WHERE state_sha256 = $1 AND state_expires_at > $2
            RETURNING environment
            """,
            _digest(state), datetime.now(timezone.utc),
        )
        return row["environment"] if row is not None else None

    async def hand_over(self, code: str, *, state: str | None = None, environment: str | None = None,
                        by: str | None = None) -> str:
        """
        The code sealed for the services that log in to Saxo, for one of them to
        take. With a state, the state decides the environment and must be one the
        admin made, unused and fresh. The environment it was handed over for.
        """
        code = (code or "").strip()
        if not code or len(code) > 2048 or any(character.isspace() for character in code):
            raise InvalidRequest("That is not a code of Saxo's")
        if state is not None:
            found = await self._state_environment(state)
            if found is None:
                raise NotPossible("This login's link is unknown, used or older than ten minutes: authorise again")
            environment = found
        environment = self._checked_environment(environment)
        name = f"saxo.{environment}.code"
        publics = keys.public_keys(self._keys_dir)
        missing = [role for role in catalog.SAXO_READERS if role not in publics]
        if missing:
            raise NotPossible(f"No public key for {', '.join(missing)}: the install script makes them")
        envelope = sealed.seal(name, {"code": code}, {role: publics[role] for role in catalog.SAXO_READERS})
        from src.database.postgres import VAULT_SCHEMA_SQL

        await self._writer.execute(VAULT_SCHEMA_SQL)
        await self._writer.execute(
            """
            INSERT INTO saxo_auth_code (environment, sealed, submitted_at, submitted_by) VALUES ($1, $2::jsonb, $3, $4)
            ON CONFLICT (environment) DO UPDATE SET sealed = EXCLUDED.sealed, submitted_at = EXCLUDED.submitted_at,
                submitted_by = EXCLUDED.submitted_by, consumed_at = NULL, consumed_by = NULL
            """,
            environment, json.dumps(envelope), datetime.now(timezone.utc), by,
        )
        return environment
