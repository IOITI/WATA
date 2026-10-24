"""
Settings of the Capital.com feed (``trade.config.capital_feed``), and its
login, which comes from the environment of its container alone.

The section is read and checked here, by the feed, as the price recorder's
is: the shared validator runs in every service, and a mistake in this section
must not keep the trader from starting.

The login is not in the configuration: Capital.com has no key that can only
read prices, and the configuration file is one the admin, reachable from the
web, reads and writes. ``.env`` hands the three values to this container only.
"""

import os
import re
from dataclasses import dataclass

from src.logging_helper.redaction import register_secret

SECTION = "trade.config.capital_feed"

# Where Capital.com's REST API is, for each kind of account. Not a setting:
# whoever could change it would receive the key.
REST_URLS = {
    "demo": "https://demo-api-capital.backend-capital.com",
    "live": "https://api-capital.backend-capital.com",
}
# The stream, unless the session names another host.
STREAMING_URL = "wss://api-streaming-capital.backend-capital.com/connect"

# Instruments in one subscription of the stream, Capital.com's limit.
MAX_INSTRUMENTS = 40
EPIC_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9_.]{0,63}$")
# The name WATA gives the instrument: the indice of the rules ("us100") and the
# folder of its day files. Same form as the labels of the downloaded history.
LABEL_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

# Where the history of prices is kept, as the admin knows it (admin.persistant.price_history_path).
DEFAULT_PRICES_ROOT = "/app/var/lib/price_history"

API_KEY_VARIABLE = "CAPITAL_API_KEY"
IDENTIFIER_VARIABLE = "CAPITAL_IDENTIFIER"
PASSWORD_VARIABLE = "CAPITAL_PASSWORD"
CREDENTIAL_VARIABLES = (API_KEY_VARIABLE, IDENTIFIER_VARIABLE, PASSWORD_VARIABLE)


@dataclass(frozen=True)
class CapitalInstrument:
    """An instrument of Capital.com that is recorded: its epic there, and WATA's name for it."""

    epic: str
    label: str


DEFAULT_INSTRUMENTS = (CapitalInstrument("US100", "us100"),)


def _flag(section: dict, key: str, default: bool) -> bool:
    value = section.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"Config field '{SECTION}.{key}' must be true or false")
    return value


def _whole(section: dict, key: str, default: int, *, minimum: int) -> int:
    value = section.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"Config field '{SECTION}.{key}' must be a whole number of at least {minimum}")
    return value


def _instruments(section: dict) -> tuple[CapitalInstrument, ...]:
    if "instruments" not in section:
        return DEFAULT_INSTRUMENTS
    entries = section["instruments"]
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"Config field '{SECTION}.instruments' must be a list of at least one instrument")
    if len(entries) > MAX_INSTRUMENTS:
        raise ValueError(f"Config field '{SECTION}.instruments' holds more than {MAX_INSTRUMENTS} instruments, Capital.com's limit")
    instruments, epics, labels = [], set(), set()
    for index, entry in enumerate(entries):
        where = f"'{SECTION}.instruments[{index}]'"
        if not isinstance(entry, dict):
            raise ValueError(f"Config field {where} must be an object with epic and label")
        epic, label = entry.get("epic"), entry.get("label")
        if not isinstance(epic, str) or not EPIC_PATTERN.match(epic):
            raise ValueError(f"Config field {where} needs an 'epic', Capital.com's name of the instrument (e.g. US100)")
        if not isinstance(label, str) or not LABEL_PATTERN.match(label):
            raise ValueError(
                f"Config field {where} needs a 'label': up to 32 lower-case letters, digits, '-' or '_' (e.g. us100)"
            )
        if epic in epics:
            raise ValueError(f"Config field {where}: {epic} is listed twice")
        if label in labels:
            raise ValueError(f"Config field {where}: two instruments are labelled {label!r}")
        epics.add(epic)
        labels.add(label)
        instruments.append(CapitalInstrument(epic=epic, label=label))
    return tuple(instruments)


@dataclass(frozen=True)
class CapitalSettings:
    enabled: bool = False
    # Which of Capital.com's accounts the key logs into.
    environment: str = "demo"
    instruments: tuple[CapitalInstrument, ...] = DEFAULT_INSTRUMENTS
    # Capital.com's own candles of each minute, and the volume it gives them.
    minute_volume: bool = True
    # Each finished day of bars as a file, for backtests.
    day_files: bool = True
    # 0 keeps everything.
    retention_months: int = 0
    # Silence while the market is open, before it is said on Telegram.
    alert_after_seconds: int = 300
    timezone: str = "Europe/Paris"
    prices_root: str = DEFAULT_PRICES_ROOT

    @classmethod
    def from_config(cls, config_manager) -> "CapitalSettings":
        section = config_manager.get_config_value(SECTION, {}) or {}
        if not isinstance(section, dict):
            raise ValueError(f"Config field '{SECTION}' must be an object")
        environment = section.get("environment", cls.environment)
        if environment not in REST_URLS:
            raise ValueError(f"Config field '{SECTION}.environment' must be one of {', '.join(REST_URLS)}")
        admin = config_manager.get_config_value("admin", {}) or {}
        persistant = admin.get("persistant", {}) if isinstance(admin, dict) else {}
        prices_root = (persistant or {}).get("price_history_path", DEFAULT_PRICES_ROOT)
        return cls(
            enabled=_flag(section, "enabled", cls.enabled),
            environment=environment,
            instruments=_instruments(section),
            minute_volume=_flag(section, "minute_volume", cls.minute_volume),
            day_files=_flag(section, "day_files", cls.day_files),
            retention_months=_whole(section, "retention_months", cls.retention_months, minimum=0),
            alert_after_seconds=_whole(section, "alert_after_seconds", cls.alert_after_seconds, minimum=30),
            timezone=config_manager.get_config_value("trade.config.general.timezone", cls.timezone),
            prices_root=prices_root if isinstance(prices_root, str) and prices_root else DEFAULT_PRICES_ROOT,
        )

    @property
    def rest_url(self) -> str:
        return REST_URLS[self.environment]

    @property
    def epics(self) -> tuple[str, ...]:
        return tuple(instrument.epic for instrument in self.instruments)

    def label_of(self, epic: str) -> str | None:
        return next((instrument.label for instrument in self.instruments if instrument.epic == epic), None)


@dataclass(frozen=True)
class Credentials:
    """The login of the API: the key, the account's email and the password chosen for the key."""

    api_key: str
    identifier: str
    password: str

    @classmethod
    def from_value(cls, value: dict | None) -> "Credentials | None":
        """The login as the credential ``capital.login`` holds it (src/vault/); None when it has none."""
        values = [((value or {}).get(key) or "").strip() for key in ("api_key", "identifier", "password")]
        if not all(values):
            return None
        credentials = cls(*values)
        register_secret("capital.api_key", credentials.api_key)
        register_secret("capital.password", credentials.password)
        return credentials

    @classmethod
    def from_environment(cls, environ=None) -> "Credentials | None":
        """The three values of ``.env``; None when any is missing or blank."""
        environ = os.environ if environ is None else environ
        values = [(environ.get(name) or "").strip() for name in CREDENTIAL_VARIABLES]
        if not all(values):
            return None
        credentials = cls(*values)
        register_secret("capital.api_key", credentials.api_key)
        register_secret("capital.password", credentials.password)
        return credentials

    @staticmethod
    def missing(environ=None) -> list[str]:
        """The variables that are missing, to say which."""
        environ = os.environ if environ is None else environ
        return [name for name in CREDENTIAL_VARIABLES if not (environ.get(name) or "").strip()]

    def secrets(self) -> tuple[str, ...]:
        """What must never be written anywhere: hidden from every message the feed writes."""
        return (self.api_key, self.password)

    def __repr__(self) -> str:
        # Never the key or the password, in a log line or a traceback.
        return f"Credentials(identifier={self.identifier!r}, api_key=***, password=***)"
