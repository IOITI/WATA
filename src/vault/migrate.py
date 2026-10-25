"""
The credentials moved from their places of before into PostgreSQL, once, at
the deploy of v0.43.0, with every service stopped:

    python -m src.vault migrate --dry-run   what would be written, and from where
    python -m src.vault migrate             written, only those that have no row yet
    python -m src.vault migrate --purge-old the old places cleared, once each has its row
    python -m src.vault export-saxo         Saxo's tokens written back to their file (going back),
                                            in the trader's container, which can open them

Saxo's token file is moved in under the services' own lock, sealed for the
four services that log in to Saxo, then renamed ``.imported``: a container of
before must never use a refresh token the new ones may have renewed since (a
refresh token is used once). Going back to a version of before needs its file
again: ``export-saxo``, in a container that reads the tokens (the trader's).

Run as the compose file's ``vault_tools`` service (profile ``tools``, never
started by ``up``), which mounts the configuration, the token folders, the
public keys and ``.env``. ``.env`` is read as lines of NAME=value, never run.

The old places stay as they are until ``--purge-old``, so that going back to
v0.42 keeps working; the purge is run later, at the operator's word, after a
day of the services reading PostgreSQL. ``internal.token`` is made new: until
then the webhook's token was it, and a TradingView alert's address seen by
someone opened the internal endpoints too.
"""

import argparse
import asyncio
import glob
import json
import os
import sys

from src.configuration import ConfigurationManager
from src.vault import credentials as catalog
from src.vault import keys, sealed, store
from src.vault.tokens import new_token
from src.vault.writer import put

ENV_FILE = os.getenv("WATA_ENV_FILE", "/app/wata.env")
ENV_LINES = ("CAPITAL_API_KEY", "CAPITAL_IDENTIFIER", "CAPITAL_PASSWORD", "WATA_HEARTBEAT_URL")


def read_env(path: str) -> dict[str, str]:
    """NAME=value lines of ``path``; comments, blank lines and anything else left aside. Never run."""
    values = {}
    try:
        with open(path, encoding="utf-8") as env_file:
            lines = env_file.read().splitlines()
    except OSError:
        return values
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name, value = name.strip().removeprefix("export ").strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[name] = value
    return values


def found_values(config_manager, environ: dict) -> dict[str, tuple[dict | None, str]]:
    """Each credential's value at its place of before, and that place; the internal token made new."""
    found = {}
    for credential in catalog.CREDENTIALS:
        if credential.name == "internal.token":
            found[credential.name] = ({"token": new_token()}, "made new")
            continue
        value = catalog.old_value(credential.name, config_manager, environ)
        if value is not None:
            try:
                value = catalog.checked(credential.name, value)
            except ValueError as e:
                print(f"  {credential.name}: found at {credential.old_place}, but {e}: not moved")
                value = None
        found[credential.name] = (value, credential.old_place)
    return found


async def migrate(connection, config_manager, environ: dict, *, dry_run: bool, keys_dir: str) -> int:
    rows = await store.read(connection, [credential.name for credential in catalog.CREDENTIALS])
    written = 0
    for name, (value, place) in found_values(config_manager, environ).items():
        if name in rows:
            print(f"  {name}: in PostgreSQL already, left as it is")
        elif value is None:
            print(f"  {name}: none at {place}: set it from the admin's Credentials")
        elif dry_run:
            print(f"  {name}: would be written, from {place}")
        elif await put(connection, name, value, by="migration", keys_dir=keys_dir, only_if_missing=True):
            print(f"  {name}: written, from {place}")
            written += 1
    written += await move_saxo_tokens(connection, config_manager, dry_run=dry_run, keys_dir=keys_dir)
    return written


def _file_cipher(path: str, secret: str):
    """What encrypts the token file of before: the app's secret and the salt beside the file, as SaxoAuth of before."""
    import base64

    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    with open(os.path.join(os.path.dirname(path), ".key_salt"), "rb") as salt_file:
        salt = salt_file.read()
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=100000).derive(secret.encode())
    return Fernet(base64.urlsafe_b64encode(key))


def _saxo_file(config_manager):
    """The token file of before, with what decrypts it: config.json's app secret and the salt beside it."""
    from src.saxo_authen.token_store import FileTokenStore

    path = config_manager.get_config_value("authentication.persistant.token_path")
    secret = (config_manager.get_config_value("authentication.saxo.app_config_object") or {}).get("AppSecret")
    if not secret or not os.path.exists(os.path.join(os.path.dirname(path), ".key_salt")):
        return path, None
    return path, FileTokenStore(path, _file_cipher(path, secret))


async def move_saxo_tokens(connection, config_manager, *, dry_run: bool, keys_dir: str) -> int:
    """Saxo's token file, under the services' lock, into the environment's row; then renamed. Whether written."""
    import fcntl

    from src.saxo_authen.token_store import _seconds

    environment = config_manager.get_config_value("saxo_auth.env", "live")
    path, file_store = _saxo_file(config_manager)
    if not os.path.exists(path):
        print(f"  saxo.{environment}.tokens: no token file (moved in already, or never made)")
        return 0
    row = await connection.fetchrow("SELECT generation FROM saxo_token WHERE environment = $1", environment) \
        if await connection.fetchval("SELECT to_regclass('saxo_token') IS NOT NULL") else None
    if row is not None:
        print(f"  saxo.{environment}.tokens: in PostgreSQL already; the token file is left as it is")
        return 0
    if dry_run:
        print(f"  saxo.{environment}.tokens: would be moved in from {path}, which is then renamed .imported")
        return 0
    if file_store is None:
        print(f"  saxo.{environment}.tokens: the token file cannot be decrypted (no app secret or salt): not moved")
        return 0
    with open(os.path.join(os.path.dirname(path), ".token_refresh.lock"), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            tokens = file_store.load()
            if tokens is None or not tokens.data.get("refresh_token"):
                print(f"  saxo.{environment}.tokens: the token file holds no token: not moved")
                return 0
            publics = keys.public_keys(keys_dir)
            envelope = sealed.seal(f"saxo.{environment}.tokens", tokens.data,
                                   {role: publics[role] for role in catalog.SAXO_READERS})
            from src.database.postgres import VAULT_SCHEMA_SQL

            await connection.execute(VAULT_SCHEMA_SQL)
            await connection.execute(
                """
                INSERT INTO saxo_token (environment, sealed, generation, saved_at, access_expires_at, refresh_expires_at,
                                        saved_by)
                VALUES ($1, $2::jsonb, 1, now(), $3, $4, 'migration') ON CONFLICT (environment) DO NOTHING
                """,
                environment, json.dumps(envelope), _seconds(tokens.data, "expires_in"),
                _seconds(tokens.data, "refresh_token_expires_in"),
            )
            os.replace(path, f"{path}.imported")
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
    print(f"  saxo.{environment}.tokens: moved in from {path}, now {os.path.basename(path)}.imported")
    return 1


def export_saxo(config_manager, saxo=None) -> int:
    """
    Saxo's tokens of the row written back to their file, encrypted as before
    with the secret of the app they were obtained with: for going back to a
    version of before, run in a container that reads them (the trader's).
    """
    from src.saxo_authen import SaxoAuth
    from src.saxo_authen.token_store import FileTokenStore

    saxo = saxo or SaxoAuth(config_manager)
    if saxo.store.kind != "database":
        print("The token file is there already: the services read it.")
        return 0
    # The app the services log in with, as they read it: the one these tokens belong to.
    app = saxo.app_data
    if not app.get("AppSecret"):
        print("No Saxo app to encrypt the token file with: set it in the admin's Credentials first.")
        return 1
    path = config_manager.get_config_value("authentication.persistant.token_path")
    with saxo._token_refresh_lock():
        tokens = saxo.store.load()
        if tokens is None:
            print("No Saxo tokens in PostgreSQL to write back.")
            return 1
        salt_path = os.path.join(os.path.dirname(path), ".key_salt")
        if not os.path.exists(salt_path):
            # The purge removed it: made as SaxoAuth of before made it.
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(salt_path, "wb") as salt_file:
                salt_file.write(os.urandom(16))
        FileTokenStore(path, _file_cipher(path, app["AppSecret"])).save(tokens.data, 0, by="export")
    print(f"Saxo's tokens written back to {path}: a version of before reads them. Stop the services of this one first.")
    config_app = config_manager.get_config_value("authentication.saxo.app_config_object") or {}
    if (config_app.get("AppKey"), config_app.get("AppSecret")) != (app.get("AppKey"), app["AppSecret"]):
        print(
            "config.json does not hold the app these tokens belong to (purged, or another app set since): a version "
            "of before reads its app there. Put that app's AppKey and AppSecret back in "
            "authentication.saxo.app_config_object (Saxo's developer portal shows them) before starting it."
        )
    return 0


# What the purge takes out of config.json and its backups: the credentials' old places.
PURGED_KEYS = (
    ("telegram", "bot_token"), ("telegram", "chat_id"), ("webserver", "app_secret"),
    ("authentication", "saxo", "app_config_object", "AppKey"), ("authentication", "saxo", "app_config_object", "AppSecret"),
)


def _purged(config: dict) -> bool:
    """The credentials' old places taken out of ``config`` (in place); whether it had any."""
    found = False
    for keys_path in PURGED_KEYS:
        node = config
        for key in keys_path[:-1]:
            node = node.get(key) if isinstance(node, dict) else None
        if isinstance(node, dict) and keys_path[-1] in node:
            del node[keys_path[-1]]
            found = True
    return found


def refusal_once_purged(config_path: str) -> str | None:
    """
    Why the services would refuse config.json once purged, None when they would
    take it: checked by their own validator before anything is written, since
    each service reads the file again at its next start.
    """
    import tempfile

    with open(config_path, encoding="utf-8") as config_file:
        config = json.load(config_file)
    _purged(config)
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "config.json")
        with open(path, "w", encoding="utf-8") as purged_file:
            json.dump(config, purged_file)
        try:
            ConfigurationManager(path)
        except (ValueError, KeyError, TypeError) as e:
            return str(e)
    return None


def _purge_config(path: str) -> bool:
    """The credentials' old places taken out of one configuration file; whether it had any."""
    with open(path, encoding="utf-8") as config_file:
        config = json.load(config_file)
    if not _purged(config):
        return False
    temporary = f"{path}.purge"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, os.stat(path).st_mode & 0o777)
    with os.fdopen(descriptor, "w", encoding="utf-8") as config_file:
        json.dump(config, config_file, indent=2)
    os.replace(temporary, path)
    return True


def _needed(config_manager) -> list[str]:
    """The credentials the services cannot do without once the old places are cleared."""
    environment = config_manager.get_config_value("saxo_auth.env", "live")
    return [
        credential.name for credential in catalog.CREDENTIALS
        if credential.name != "heartbeat.url"
        and not (credential.name.startswith("saxo.") and credential.name != f"saxo.{environment}.app")
        and not (credential.name == "capital.login" and not catalog.old_value("capital.login", config_manager, {}))
    ]


async def purge(connection, config_manager, config_path: str) -> int:
    needed = _needed(config_manager)
    rows = await store.read(connection, needed)
    missing = [name for name in needed if name not in rows]
    environment = config_manager.get_config_value("saxo_auth.env", "live")
    if not await connection.fetchval(
        "SELECT to_regclass('saxo_token') IS NOT NULL AND EXISTS (SELECT 1 FROM saxo_token WHERE environment = $1 "
        "AND sealed IS NOT NULL)", environment,
    ):
        missing.append(f"saxo.{environment}.tokens")
    if missing:
        print(f"Not purged: {', '.join(missing)} not in PostgreSQL yet. The services would find no credential.")
        return 1
    refusal = refusal_once_purged(config_path)
    if refusal:
        print(f"Not purged: without its credentials, config.json would be refused by the services at their next start "
              f"({refusal}). Nothing was changed.")
        return 1
    folder = os.path.dirname(os.path.abspath(config_path))
    for path in [config_path, *sorted(glob.glob(os.path.join(folder, "config.json.bak-*")))]:
        if _purge_config(path):
            print(f"  {os.path.basename(path)}: the credentials' old places taken out")
    for path in sorted(glob.glob(os.path.join(folder, ".config.candidate-*"))):
        os.remove(path)
        print(f"  {os.path.basename(path)}: removed (a save that never finished)")
    token_path = config_manager.get_config_value("webserver.persistant.token_path")
    saxo_path = config_manager.get_config_value("authentication.persistant.token_path")
    for path in (token_path, os.path.join(os.path.dirname(token_path), ".webserver_key_salt"),
                 f"{saxo_path}.imported", os.path.join(os.path.dirname(saxo_path), ".key_salt")):
        if path and os.path.exists(path):
            os.remove(path)
            print(f"  {path}: removed")
    admin = config_manager.get_config_value("admin", {}) or {}
    tokens_path = (admin.get("persistant") or {}).get("mcp_tokens_path", "/app/var/lib/admin/mcp_tokens.json")
    for path in (tokens_path, f"{tokens_path}.imported"):
        if os.path.exists(path):
            os.remove(path)
            print(f"  {path}: removed (the MCP tokens are in PostgreSQL)")
    print("Delete these lines of /app/wata/.env by hand, if they are there: " + ", ".join(ENV_LINES))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.vault migrate")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--purge-old", action="store_true")
    args = parser.parse_args(argv)
    config_path = os.environ["WATA_CONFIG_PATH"]
    config_manager = ConfigurationManager(config_path)
    environ = read_env(ENV_FILE)

    async def run() -> int:
        import asyncpg

        connection = await asyncpg.connect(config_manager.get_config_value("postgresql.dsn"), timeout=10)
        try:
            if args.purge_old:
                return await purge(connection, config_manager, config_path)
            missing = [role for role in keys.ROLES if role not in keys.public_keys()]
            if missing and not args.dry_run:
                print(f"No public key for {', '.join(missing)}: run python -m src.vault keygen first")
                return 1
            print("Dry run: nothing is written." if args.dry_run else "Writing what PostgreSQL does not have yet:")
            await migrate(connection, config_manager, environ, dry_run=args.dry_run, keys_dir=keys.KEYS_DIR)
            return 0
        finally:
            await connection.close()

    return asyncio.run(run())


if __name__ == "__main__":
    sys.exit(main())
