import argparse
import getpass
import logging
import os
import sys

from src.admin.auth import MIN_PASSWORD_LENGTH, CredentialStore, totp_uri
from src.admin.context import AdminSettings
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


def main():
    args = parse_arguments()

    # Get configuration path from environment variable
    config_path = os.getenv("WATA_CONFIG_PATH")
    if not config_path:
        logger.error("WATA_CONFIG_PATH environment variable is not set")
        sys.exit(1)

    try:
        config_manager = ConfigurationManager(config_path)
        credentials = CredentialStore(AdminSettings.from_config(config_manager).credentials_path)

        if args.set_password:
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
