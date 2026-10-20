"""
The configuration the lab's admin and backtests run with (``var/lib/lab/config.json``):
the example configuration, with the server's trading rules and settings as the
newest research day carries them, the lab's database, and the lab's folders.

The server's settings come without the addresses of its services and with
their secrets masked (src/admin/research_export.py): what is missing is taken
from the example, which reaches nothing here. The lab's admin acts on nothing
(no operator action, no editing of this file) and runs several backtests at
once, over longer periods than the server allows.
"""

import copy
import json
import os
import secrets

from lab import paths
from lab.db import research_days

DEFAULT_PORT = 8083
WORKERS = 4
MAX_QUEUED = 500
MAX_PERIOD_DAYS = 731


def server_settings() -> dict | None:
    """What the newest research day says of the server's configuration: its ``trade`` rules and settings."""
    for _, folder in reversed(research_days()):
        path = folder / "settings.json"
        if path.is_file():
            snapshot = json.loads(path.read_text(encoding="utf-8"))
            return (snapshot.get("config") or {}).get("trade")
    return None


def _merged(base: dict, over: dict) -> dict:
    merged = copy.deepcopy(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merged(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _moved(node, root: str):
    """Every path of the server's (under /app) moved into the lab's folder: nothing here writes outside it."""
    if isinstance(node, dict):
        return {key: _moved(value, root) for key, value in node.items()}
    if isinstance(node, list):
        return [_moved(value, root) for value in node]
    if isinstance(node, str) and node.startswith("/app/"):
        return os.path.join(root, node.removeprefix("/app/"))
    return node


def build(dsn: str, port: int = DEFAULT_PORT, workers: int = WORKERS) -> dict:
    with open(paths.REPO_ROOT / "etc" / "config_example.json", encoding="utf-8") as in_file:
        config = json.load(in_file)
    trade = server_settings()
    if trade:
        if trade.get("rules"):
            config["trade"]["rules"] = copy.deepcopy(trade["rules"])
        if trade.get("config"):
            config["trade"]["config"] = _merged(config["trade"]["config"], trade["config"])
    config = _moved(config, str(paths.lab_root() / "app"))
    config["postgresql"]["dsn"] = dsn
    config["logging"]["persistant"]["log_path"] = f"{paths.logs()}{os.sep}"
    admin = config.setdefault("admin", {})
    admin.update({
        "api_host": "127.0.0.1",
        "api_port": port,
        "actions_enabled": False,
        "config_edit_enabled": False,
        "backtests_enabled": True,
        "backtests_workers": workers,
        "backtests_max_queued": MAX_QUEUED,
        "backtests_max_period_days": MAX_PERIOD_DAYS,
        "trusted_proxy_hosts": [],
        # Plain HTTP on this machine alone: a cookie marked Secure would not come back over it.
        "cookie_secure": False,
        "persistant": {
            "credentials_path": str(paths.credentials()),
            "backtests_path": str(paths.backtests()),
            "price_history_path": str(paths.price_history()),
            "ta_path": str(paths.scripts()),
        },
    })
    return config


def write(dsn: str, port: int = DEFAULT_PORT, workers: int = WORKERS) -> str:
    """Write the lab's configuration; its path."""
    path = paths.config()
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".json.part")
    partial.write_text(json.dumps(build(dsn, port, workers), indent=2), encoding="utf-8")
    os.replace(partial, path)
    return str(path)


def password() -> str:
    """The lab admin's password: made once, kept beside its credentials for whoever uses this machine."""
    path = paths.password_file()
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    made = secrets.token_urlsafe(12)
    path.write_text(made + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    return made
