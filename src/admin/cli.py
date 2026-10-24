import argparse
import asyncio
import getpass
import logging
import os
import sys

from src.admin.audit import AuditLog
from src.admin.auth import MIN_PASSWORD_LENGTH, CredentialStore, totp_uri
from src.admin.context import AdminSettings
from src.admin.mcp_server.tokens import DEFAULT_DAYS, TokenError
from src.configuration import ConfigurationManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def parse_arguments():
    parser = argparse.ArgumentParser(description="WATA Admin CLI")
    parser.add_argument("--set-password", action="store_true", help="Set the password of the admin interface")
    parser.add_argument(
        "--enable-totp", action="store_true",
        help="Require a one-time code from an authenticator app at login (replaces the secret if already enabled)",
    )
    parser.add_argument("--disable-totp", action="store_true", help="Stop requiring a one-time code at login")
    parser.add_argument(
        "--mcp-token", nargs="+", metavar="ACTION",
        help="Tokens of the MCP endpoint for Claude: 'create NAME', 'list' or 'revoke NAME'",
    )
    parser.add_argument(
        "--days", type=int, default=DEFAULT_DAYS, help=f"How long a token made by --mcp-token create lasts (default {DEFAULT_DAYS})",
    )
    return parser.parse_args()


def set_password(credentials: CredentialStore):
    print(f"New admin password (at least {MIN_PASSWORD_LENGTH} characters, will not be shown in terminal):")
    password = getpass.getpass("")
    print("Repeat the password:")
    if password != getpass.getpass(""):
        print("Error: The two passwords do not match")
        sys.exit(1)

    try:
        credentials.set_password(password)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)

    print("Admin password set. Sessions opened with the previous password are closed.")


def enable_totp(credentials: CredentialStore):
    try:
        secret = credentials.enable_totp()
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)

    print("One-time codes are now required at login, after the password.")
    print("Add this account to an authenticator app, by typing the key or with the address below:")
    print(f"  Key:     {secret}")
    print(f"  Address: {totp_uri(secret)}")
    print("Keep the key somewhere safe: without the app, --disable-totp on the server is the way back in.")
    print("Open sessions are closed.")


def mcp_token(settings: AdminSettings, words: list[str], days: int, dsn: str | None = None, connect=None):
    """
    Make, list or revoke a token of the MCP endpoint, kept in PostgreSQL. A
    token is shown once, here, and never again.
    """

    async def run():
        from src.admin.mcp_token_store import DbTokenStore

        if connect is None:
            import asyncpg

            connection = await asyncpg.connect(dsn, timeout=10)
        else:
            connection = await connect()
        try:
            tokens = DbTokenStore(reader=connection, writer=connection, legacy_path=settings.mcp_tokens_path)
            await tokens.refresh()
            await _mcp_token(tokens, settings, words, days)
        finally:
            await connection.close()

    asyncio.run(run())


async def _mcp_token(tokens, settings: AdminSettings, words: list[str], days: int):
    audit = AuditLog(os.path.join(settings.log_path, "wata-admin", "audit.jsonl"))
    action, names = words[0], words[1:]
    try:
        if action == "create" and len(names) == 1:
            token, entry = await tokens.create(names[0], days)
            audit.append("console", "MCP token created", f"{entry['name']} ({entry['id']}), until {entry['expires_at']}")
            print(f"Token {entry['name']} ({entry['id']}), valid until {entry['expires_at']}:")
            print(f"  {token}")
            print("It is shown this once: keep it in the Mac's Keychain (README, 'Claude on the server').")
            if not settings.mcp_enabled:
                print("The endpoint is off: it answers once admin.mcp_enabled is true in config.json and the admin restarted.")
        elif action == "revoke" and len(names) == 1:
            for entry in await tokens.revoke(names[0]):
                audit.append("console", "MCP token revoked", f"{entry['name']} ({entry['id']})")
                print(f"Token {entry['name']} ({entry['id']}) revoked: it no longer opens the endpoint.")
        elif action == "list" and not names:
            listed = tokens.entries()
            if not tokens.readable():
                print("The tokens cannot be read from PostgreSQL: no token is accepted.")
            for entry in listed:
                print(f"{entry['name']:<20} {entry['id']}  {entry['state']:<8} created {entry['created_at']}  expires {entry['expires_at']}")
            if not listed:
                print("No token yet: --mcp-token create NAME")
            print(f"The endpoint is {'on' if settings.mcp_enabled else 'off'} (admin.mcp_enabled).")
        else:
            print("Use --mcp-token create NAME [--days N], --mcp-token list or --mcp-token revoke NAME")
            sys.exit(2)
    except TokenError as e:
        print(f"Error: {e}")
        sys.exit(1)


def main():
    args = parse_arguments()

    # Get configuration path from environment variable
    config_path = os.getenv("WATA_CONFIG_PATH")
    if not config_path:
        logger.error("WATA_CONFIG_PATH environment variable is not set")
        sys.exit(1)

    try:
        config_manager = ConfigurationManager(config_path)
        settings = AdminSettings.from_config(config_manager)
        credentials = CredentialStore(settings.credentials_path)

        if args.mcp_token:
            mcp_token(settings, args.mcp_token, args.days, dsn=config_manager.get_config_value("postgresql.dsn"))
        elif args.set_password:
            set_password(credentials)
        elif args.enable_totp:
            enable_totp(credentials)
        elif args.disable_totp:
            credentials.disable_totp()
            print("One-time codes are no longer required at login. Open sessions are closed.")
        else:
            state = "set" if credentials.is_configured() else "not set"
            print(f"Admin password: {state}")
            print(f"One-time codes at login: {'required' if credentials.totp_enabled() else 'not required'}")
            print("Use --set-password, --enable-totp or --disable-totp to change them.")
    except KeyboardInterrupt:
        print("\nOperation cancelled by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
