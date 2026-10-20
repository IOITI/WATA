"""
Where the lab keeps what it pulled, made and ran: ``var/lib/lab`` of the
checkout (git-ignored), or ``WATA_LAB_ROOT``.

    price_history/      what lab/sync.sh pulls of the server's price history:
        research/days/      each finished day's research files
        dukascopy/, capital/  the day files of Dukascopy's history and of the Capital.com feed
    server_ta/          the server's Pine scripts and signal rules, as pulled
    ta/                 the lab's own script store (the server's scripts, and those written here)
    backtests/          the runs, in the admin's own folders
    pg/                 the lab's PostgreSQL
    config.json         the configuration the lab's admin and backtests run with
    experiments.jsonl   the experiment journal
"""

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def lab_root() -> Path:
    return Path(os.environ.get("WATA_LAB_ROOT") or REPO_ROOT / "var" / "lib" / "lab")


def price_history() -> Path:
    return lab_root() / "price_history"


def research_days() -> Path:
    return price_history() / "research" / "days"


def server_ta() -> Path:
    return lab_root() / "server_ta"


def scripts() -> Path:
    return lab_root() / "ta"


def backtests() -> Path:
    return lab_root() / "backtests"


def pgdata() -> Path:
    return lab_root() / "pg"


def config() -> Path:
    return lab_root() / "config.json"


def credentials() -> Path:
    return lab_root() / "admin" / "credentials.json"


def password_file() -> Path:
    return lab_root() / "admin" / "password.txt"


def logs() -> Path:
    return lab_root() / "log"


def experiments() -> Path:
    return lab_root() / "experiments.jsonl"
