"""
``watawebtoken``, in the web server's container: the TradingView webhook's
token shown, or rotated (``--new``). Kept in PostgreSQL, sealed for the web
server and the admin (src/vault/); on a server whose keys are not made yet,
the token file of before. The admin's Credentials do the same, audited.
"""

import argparse
import asyncio
import logging
import os
import sys

from src.configuration import ConfigurationManager
from src.vault import keys
from src.vault import loader as vault
from src.vault.tokens import GRACE_SECONDS, new_token, rotated
from src.web_server_token import WEBHOOK, NoWebServerToken, WebServerToken

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def parse_arguments():
    parser = argparse.ArgumentParser(description="WebServer Token CLI")
    parser.add_argument("--display", action="store_true", help="Display the TradingView webhook's token")
    parser.add_argument("--new", action="store_true", help="Rotate the TradingView webhook's token")
    return parser.parse_args()


async def _rotate(config_manager) -> str:
    import asyncpg

    from src.vault.writer import put

    current = vault.credential(WEBHOOK)
    connection = await asyncpg.connect(config_manager.get_config_value("postgresql.dsn"), timeout=10)
    try:
        value = rotated(current)
        await put(connection, WEBHOOK, value, by="console")
    finally:
        await connection.close()
    return value["token"]


def main():
    args = parse_arguments()
    config_path = os.getenv("WATA_CONFIG_PATH")
    if not config_path:
        logger.error("WATA_CONFIG_PATH environment variable is not set")
        sys.exit(1)
    try:
        config_manager = ConfigurationManager(config_path)
        vault.load_blocking(config_manager, "web_server", give_up_after=30)
        web_server_token = WebServerToken(config_manager)
        if args.new:
            if keys.public_keys():
                token = asyncio.run(_rotate(config_manager))
                print("New TradingView webhook token, kept in PostgreSQL:")
                print(f"Token: {token}")
                print(f"The one before is still accepted for {GRACE_SECONDS[WEBHOOK] // 60} minutes: change TradingView's "
                      "alerts meanwhile, then restart the web server (System page, or watastop and watastart).")
            else:
                token = new_token()
                web_server_token.save_token_data(token)
                print("New TradingView webhook token, in the token file (no keys on this server yet):")
                print(f"Token: {token}")
        else:
            token = web_server_token.webhook_token()
            print(f"TradingView webhook token: {token}")
            print("\nUse it in your webhook URL as a query parameter:")
            print("Example: https://your-server.com/webhook?token=YOUR_TOKEN")
    except NoWebServerToken as e:
        print(f"Error: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Error: {type(e).__name__}: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
