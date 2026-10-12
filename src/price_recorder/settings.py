"""
Settings of the price recorder (``trade.config.price_recorder``).

Read and checked here, by the recorder alone: the shared configuration
validator runs in every service, and a mistake in this section must not keep
the trader from starting.
"""

from dataclasses import dataclass
from datetime import datetime

import pytz

SECTION = "trade.config.price_recorder"
WEEKDAYS_TRADED = 5  # Monday to Friday


@dataclass(frozen=True)
class ExtraInstrument:
    """An instrument recorded besides the turbos, e.g. the one quoted for an index."""

    label: str
    uic: int
    asset_type: str


def _positive_int(section: dict, key: str, default: int, *, minimum: int = 1) -> int:
    value = section.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"Config field '{SECTION}.{key}' must be a whole number of at least {minimum}")
    return value


def _extra_instruments(section: dict) -> tuple[ExtraInstrument, ...]:
    entries = section.get("instruments", [])
    if not isinstance(entries, list):
        raise ValueError(f"Config field '{SECTION}.instruments' must be a list")
    instruments = []
    for index, entry in enumerate(entries):
        where = f"'{SECTION}.instruments[{index}]'"
        if not isinstance(entry, dict):
            raise ValueError(f"Config field {where} must be an object with label, uic and asset_type")
        label, uic, asset_type = entry.get("label"), entry.get("uic"), entry.get("asset_type")
        if not isinstance(label, str) or not label:
            raise ValueError(f"Config field {where} needs a 'label' (text)")
        if isinstance(uic, bool) or not isinstance(uic, int):
            raise ValueError(f"Config field {where} needs a 'uic' (whole number)")
        if not isinstance(asset_type, str) or not asset_type:
            raise ValueError(f"Config field {where} needs an 'asset_type' (text, e.g. CfdOnIndex)")
        instruments.append(ExtraInstrument(label=label, uic=uic, asset_type=asset_type))
    return tuple(instruments)


def _rule_config(config_manager, rule_type: str) -> dict:
    for rule in config_manager.get_config_value("trade.rules", []) or []:
        if rule.get("rule_type") == rule_type:
            return rule.get("rule_config", {}) or {}
    return {}


@dataclass(frozen=True)
class RecorderSettings:
    timezone: str
    enabled: bool = False
    refresh_interval_seconds: int = 300
    max_instruments: int = 150
    refresh_rate_ms: int = 1000
    instruments: tuple[ExtraInstrument, ...] = ()
    # 0 keeps everything.
    retention_months: int = 0
    alert_after_seconds: int = 300
    # When the turbos are expected to quote: outside it, silence is not an incident.
    trading_start_hour: int | None = None
    trading_end_hour: int | None = None
    market_closed_dates: frozenset[str] = frozenset()

    @classmethod
    def from_config(cls, config_manager) -> "RecorderSettings":
        section = config_manager.get_config_value(SECTION, {}) or {}
        if not isinstance(section, dict):
            raise ValueError(f"Config field '{SECTION}' must be an object")
        enabled = section.get("enabled", False)
        if not isinstance(enabled, bool):
            raise ValueError(f"Config field '{SECTION}.enabled' must be true or false")

        market_hours = _rule_config(config_manager, "market_hours")
        closed_dates = _rule_config(config_manager, "market_closed_dates").get("market_closed_dates") or []
        return cls(
            timezone=config_manager.get_config_value("trade.config.general.timezone", "Europe/Paris"),
            enabled=enabled,
            refresh_interval_seconds=_positive_int(section, "refresh_interval_seconds", cls.refresh_interval_seconds, minimum=30),
            max_instruments=_positive_int(section, "max_instruments", cls.max_instruments),
            refresh_rate_ms=_positive_int(section, "refresh_rate_ms", cls.refresh_rate_ms, minimum=100),
            instruments=_extra_instruments(section),
            retention_months=_positive_int(section, "retention_months", cls.retention_months, minimum=0),
            alert_after_seconds=_positive_int(section, "alert_after_seconds", cls.alert_after_seconds, minimum=30),
            trading_start_hour=market_hours.get("trading_start_hour"),
            trading_end_hour=market_hours.get("trading_end_hour"),
            market_closed_dates=frozenset(closed_dates),
        )

    def is_trading_time(self, now: datetime) -> bool:
        """
        Whether the turbos should be quoting at ``now``: a weekday that is not a
        closed date, inside the trading hours of the configuration (the trader's
        own test, src/trade/rules.py, plus the weekend).
        """
        local = now.astimezone(pytz.timezone(self.timezone))
        if local.weekday() >= WEEKDAYS_TRADED:
            return False
        if local.strftime("%d/%m/%Y") in self.market_closed_dates:
            return False
        if self.trading_start_hour is None or self.trading_end_hour is None:
            return True
        return self.trading_start_hour <= local.hour < self.trading_end_hour
