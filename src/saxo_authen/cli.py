#!/usr/bin/env python3
"""
``watasaxoauth``, in the trader's container: a Saxo login's code handed over
to the services that log in to Saxo. It prints a link to Saxo's login page
(its state kept hashed for ten minutes), then takes the address Saxo sent the
browser back to, or the code alone, unseen. The code is sealed for the four
services in PostgreSQL, one of which exchanges it within seconds. While the
token file of before is there (before the migration), the code goes in a file
beside it, as it always did.
"""

import asyncio
import getpass
import logging
import os
import sys
import urllib.parse
from pathlib import Path

from src.configuration import ConfigurationManager

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("saxo_auth_cli")


def save_auth_code(auth_code, config_manager):
    """Save the authorization code to a temporary file (the services of before read it there)."""
    token_file_path = config_manager.get_config_value("authentication.persistant.token_path")
    auth_code_path = os.path.join(os.path.dirname(token_file_path), "saxo_auth_code.txt")

    # Ensure the directory exists
    Path(os.path.dirname(auth_code_path)).mkdir(parents=True, exist_ok=True)

    # Write the code to the file
    with open(auth_code_path, "w") as file:
        file.write(auth_code)

    logger.info(f"Authorization code saved to {auth_code_path}")
    print("Authorization code saved successfully! The application will now continue with the authentication process.")


def code_and_state(given: str) -> tuple[str, str | None]:
    """The code, and the state when an address was pasted, from what was typed."""
    given = given.strip()
    if "?" in given or given.startswith("http"):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(given).query)
        return (query.get("code") or [""])[0], (query.get("state") or [None])[0]
    return given, None


async def _hand_over_in_postgresql(config_manager, ask=getpass.getpass, show=print, connect=None) -> str:
    from src.admin.saxo import AdminSaxo

    if connect is None:
        import asyncpg

        connection = await asyncpg.connect(config_manager.get_config_value("postgresql.dsn"), timeout=10)
    else:
        connection = await connect()
    try:
        saxo = AdminSaxo(reader=connection, writer=connection, config_manager=config_manager)
        show("Open this page, sign in to Saxo and accept (valid ten minutes):")
        show(f"  {await saxo.authorize()}")
        show("Then paste the address Saxo sent the browser back to, or the code alone (not shown when typed):")
        code, state = code_and_state(ask(""))
        environment = await saxo.hand_over(code, state=state, by="console")
    finally:
        await connection.close()
    return environment


def main():
    try:
        config_path = os.getenv("WATA_CONFIG_PATH")
        if not config_path:
            logger.error("WATA_CONFIG_PATH environment variable not set")
            print("Error: WATA_CONFIG_PATH environment variable not set")
            sys.exit(1)
        config_manager = ConfigurationManager(config_path)
        token_file_path = config_manager.get_config_value("authentication.persistant.token_path")

        if os.path.exists(token_file_path):
            # Not moved into PostgreSQL yet: the services read the code of a file, as before.
            print("Please enter the Saxo Bank authorization code (will not be shown in terminal):")
            auth_code = getpass.getpass("")
            if not auth_code:
                logger.error("No authorization code provided")
                print("Error: Authorization code is required")
                sys.exit(1)
            save_auth_code(auth_code, config_manager)
            return

        environment = asyncio.run(_hand_over_in_postgresql(config_manager))
        print(f"Code handed over for the {environment} account: a service exchanges it within seconds "
              "and says so on Telegram.")
    except KeyboardInterrupt:
        print("\nOperation cancelled by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Error: {type(e).__name__}: {e}")
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
