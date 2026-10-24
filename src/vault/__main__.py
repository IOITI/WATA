"""
``python -m src.vault keygen [--root /keys]``: the services' key pairs that are
missing, never one replaced (the install script runs it at each install).
``python -m src.vault migrate [--dry-run | --purge-old]``, ``python -m src.vault
export-saxo``: see migrate.py.
"""

import argparse
import os
import sys


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["migrate"]:
        from src.vault.migrate import main as migrate

        return migrate(argv[1:])
    if argv[:1] == ["export-saxo"]:
        from src.configuration import ConfigurationManager
        from src.vault import loader
        from src.vault.migrate import export_saxo

        config_manager = ConfigurationManager(os.environ["WATA_CONFIG_PATH"])
        loader.load_blocking(config_manager)
        return export_saxo(config_manager)
    parser = argparse.ArgumentParser(prog="python -m src.vault")
    parser.add_argument("command", choices=["keygen"])
    parser.add_argument("--root", default="/keys", help="Where the keys are kept (mounted from /app/wata/keys)")
    args = parser.parse_args(argv)
    from src.vault.keys import ROLES, generate

    made = generate(args.root)
    print(f"Keys made for: {', '.join(made)}" if made else f"Every service has its key already ({len(ROLES)}).")
    print(f"Keep a copy of {os.path.abspath(args.root)}: without it, the credentials must be entered again.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
