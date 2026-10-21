"""
What a backtest runs with: the live configuration's rules and settings, read
where the trading services read them, and the changes a run asks for.

Each setting a run may change is a ``Field``: its name, the group the form
shows it in, its kind and bounds. A run keeps what it was made with
(``to_dict``), with the live value of each setting it changed.

The settings that are lists (the hours of the time-of-day scaling, the
confidence bands, the closed dates) are read as they are and not changed by a
run: they are shown, and changed in the configuration itself.
"""

import json
import re
from dataclasses import asdict, dataclass, fields, replace

from src.backtest.execution import EXECUTIONS, FIXED
from src.ta_engine.settings import TaEngineSettings
from src.trade.macro_calendar import NewsRule

_TIME_OF_DAY = re.compile(r"^([01][0-9]|2[0-3]):([0-5][0-9])$")

# How a run's fills are taken: at the bid that crossed a stop, or at the stop's own price.
STOP_FILLS = ("worst", "level")

# The account every run starts with unless it says another: one that pays for a turbo at
# each entry. The live balance, which may not (21.82 € paid for none of 34 entries on
# 2026-10-21), is offered beside it.
DEFAULT_CAPITAL = 1_000.0
# The time between a bar's close and the trader seeing its signal: the TA engine's
# run and its request to the web server, or the web server's own long poll.
DEFAULT_SIGNAL_DELAY = {"pine": 3.0, "journal": 0.5}


class InvalidSettings(ValueError):
    """A change a run asked for is not one it can make; the message says why."""


@dataclass(frozen=True)
class Field:
    name: str
    group: str
    kind: str  # number | integer | boolean | time | choice
    label: str
    least: float | None = None
    most: float | None = None
    choices: tuple[str, ...] = ()
    unit: str = ""
    # Where the trading services read it (none: the backtest's own).
    source: str = ""


FIELDS = (
    # ── which signals are taken ──
    Field("max_signal_age_seconds", "entries", "integer", "Signals older than this are refused", 1, 86_400, unit="s",
          source="trade.rules.signal_validation.max_signal_age_seconds"),
    Field("trading_start_hour", "entries", "integer", "No entry before", 0, 23, unit="h",
          source="trade.rules.market_hours.trading_start_hour"),
    Field("trading_end_hour", "entries", "integer", "No entry from", 1, 24, unit="h",
          source="trade.rules.market_hours.trading_end_hour"),
    Field("risky_start_hour", "entries", "integer", "Risky hour from", 0, 23, unit="h",
          source="trade.rules.market_hours.risky_trading_start_hour"),
    Field("risky_start_minute", "entries", "integer", "Risky from minute", 0, 59, unit="min",
          source="trade.rules.market_hours.risky_trading_start_minute"),
    Field("cooldown_after_loss_minutes", "entries", "integer", "No entry after a losing trade for", 0, 1440, unit="min",
          source="trade.rules.risk_management.cooldown_after_loss_minutes"),
    Field("max_trades_per_day", "entries", "integer", "Entries per day at most (0: no limit)", 0, 1000,
          source="trade.rules.risk_management.max_trades_per_day"),
    Field("min_confidence", "entries", "number", "Entries under this confidence are refused", 0, 100,
          source="trade.config.position_sizing.confidence_scaling.min_confidence_threshold"),
    # No entry around US releases: the macro_news rule, off unless it is in the configuration.
    Field("news_window", "entries", "boolean", "No entry around US releases", source="trade.rules.macro_news"),
    Field("news_minutes_before", "entries", "integer", "From this long before a release", 0, 120, unit="min",
          source="trade.rules.macro_news.minutes_before"),
    Field("news_minutes_after", "entries", "integer", "Until this long after it", 0, 120, unit="min",
          source="trade.rules.macro_news.minutes_after"),
    Field("news_minutes_after_fomc", "entries", "integer", "Until this long after an FOMC statement", 0, 180, unit="min",
          source="trade.rules.macro_news.minutes_after_fomc"),
    Field("news_fomc", "entries", "boolean", "Around FOMC statements", source="trade.rules.macro_news.events"),
    Field("news_cpi", "entries", "boolean", "Around CPI", source="trade.rules.macro_news.events"),
    Field("news_nfp", "entries", "boolean", "Around the jobs report (NFP)", source="trade.rules.macro_news.events"),
    Field("news_pce", "entries", "boolean", "Around PCE", source="trade.rules.macro_news.events"),
    # ── the day ──
    Field("no_entry_above_day_profit", "day", "number", "No new entry once the day is above", 0.01, 10_000, unit="%",
          source="trade.rules.day_trading.dont_enter_trade_if_day_profit_is_more_than"),
    Field("max_day_loss", "day", "number", "No new entry once the day is below", -100, -0.01, unit="%",
          source="trade.rules.day_trading.max_day_loss_percent"),
    Field("day_profit_target", "day", "number", "Sell once the day would reach", 0.01, 10_000, unit="%",
          source="trade.rules.day_trading.percent_profit_wanted_per_days"),
    Field("close_position_time", "day", "time", "Everything is sold at", unit="local time",
          source="trade.rules.day_trading.close_position_time"),
    # ── a position's exits ──
    Field("stoploss_percent", "exits", "number", "Stop-loss", -100, -0.1, unit="%",
          source="trade.config.position_management.performance_thresholds.stoploss_percent"),
    Field("max_profit_percent", "exits", "number", "Take-profit", 0.1, 10_000, unit="%",
          source="trade.config.position_management.performance_thresholds.max_profit_percent"),
    Field("trailing_enabled", "exits", "boolean", "Trailing stop",
          source="trade.config.position_management.performance_thresholds.trailing_stop.enabled"),
    Field("trailing_activation_percent", "exits", "number", "Gain that arms the trailing stop", 0, 1000, unit="%",
          source="trade.config.position_management.performance_thresholds.trailing_stop.activation_percent"),
    Field("trailing_drawdown_percent", "exits", "number", "Fall from the best that sells", 0.1, 1000, unit="%",
          source="trade.config.position_management.performance_thresholds.trailing_stop.drawdown_percent"),
    # ── the turbo bought ──
    Field("turbo_min_price", "turbo", "number", "Turbos priced from", 0.01, 10_000,
          source="trade.config.turbo_preference.price_range.min"),
    Field("turbo_max_price", "turbo", "number", "Turbos priced up to", 0.01, 10_000,
          source="trade.config.turbo_preference.price_range.max"),
    # ── how much is bought ──
    Field("funds_percent", "sizing", "number", "Share of the account used", 1, 100, unit="%",
          source="trade.config.buying_power.max_account_funds_to_use_percentage"),
    Field("reserve_cash_percent", "sizing", "number", "Cash kept aside (above 0: the new position is bought first)", 0, 99, unit="%",
          source="trade.config.buying_power.reserve_cash_percent"),
    Field("safety_units", "sizing", "number", "Turbos left out of each order, for safety", 0, 1000,
          source="trade.config.buying_power.safety_margins.bid_calculation"),
    Field("time_scaling_enabled", "sizing", "boolean", "Smaller orders at some hours",
          source="trade.config.position_sizing.time_of_day_scaling.enabled"),
    Field("confidence_enabled", "sizing", "boolean", "Orders sized by the signal's confidence",
          source="trade.config.position_sizing.confidence_scaling.enabled"),
    # ── the backtest's own: how its orders are filled ──
    Field("capital", "execution", "number", "Account at the start", 1, 1e9, unit="€"),
    Field("execution", "execution", "choice", "Order times and prices: fixed, or drawn from WATA's own orders",
          choices=EXECUTIONS),
    Field("signal_delay_seconds", "execution", "number", "From a signal to the trader", 0, 600, unit="s"),
    Field("order_seconds", "execution", "number", "From an order to its fill", 0, 600, unit="s"),
    Field("close_wait_seconds", "execution", "number", "From a sale to the next purchase", 0, 600, unit="s"),
    Field("stop_fill", "execution", "choice", "A stop crossed inside ten seconds sells at", choices=STOP_FILLS),
    Field("slippage", "execution", "number", "Worse price per turbo on each fill", 0, 1000),
    Field("commission_per_order", "execution", "number", "Commission per order", 0, 1000, unit="€"),
    Field("commission_percent", "execution", "number", "Commission on the amount of each order", 0, 10, unit="%"),
    # ── how a Pine script is run (the TA engine's) ──
    Field("history_days", "engine", "integer", "Days of bars before each day's run", 0, 30, unit="days",
          source="trade.config.ta_engine.history_days"),
    Field("restart_time", "engine", "time", "The engine starts each day again at", unit="UTC",
          source="trade.config.ta_engine.restart_time"),
)
FIELDS_BY_NAME = {entry.name: entry for entry in FIELDS}
GROUPS = ("entries", "day", "exits", "turbo", "sizing", "execution", "engine")


@dataclass(frozen=True)
class TimePeriod:
    start_minute: int
    end_minute: int
    scale_percent: float
    label: str = ""


@dataclass(frozen=True)
class ConfidenceBand:
    least: float
    below: float
    scale_percent: float
    label: str = ""


@dataclass(frozen=True)
class BacktestSettings:
    timezone: str = "Europe/Paris"
    # Entries.
    max_signal_age_seconds: int = 3600
    trading_start_hour: int = 8
    trading_end_hour: int = 22
    risky_start_hour: int = 21
    risky_start_minute: int = 54
    closed_dates: tuple[str, ...] = ()
    allowed_indices: tuple[str, ...] = ("us100",)
    cooldown_after_loss_minutes: int = 0
    max_trades_per_day: int = 0
    min_confidence: float = 0.0
    # No entry around US releases (the macro_news rule): off unless the configuration has it.
    news_window: bool = False
    news_minutes_before: int = NewsRule.minutes_before
    news_minutes_after: int = NewsRule.minutes_after
    news_minutes_after_fomc: int = NewsRule.minutes_after_fomc
    news_fomc: bool = True
    news_cpi: bool = True
    news_nfp: bool = True
    news_pce: bool = True
    # The day.
    no_entry_above_day_profit: float = 1.25
    max_day_loss: float = -25.0
    day_profit_target: float = 1.7
    close_position_time: str = "21:55"
    # Exits.
    stoploss_percent: float = -15
    max_profit_percent: float = 60
    trailing_enabled: bool = False
    trailing_activation_percent: float = 5
    trailing_drawdown_percent: float = 8
    # The turbo.
    turbo_min_price: float = 4
    turbo_max_price: float = 15
    # Sizing.
    funds_percent: float = 100
    reserve_cash_percent: float = 0
    safety_units: float = 1
    time_scaling_enabled: bool = False
    time_periods: tuple[TimePeriod, ...] = ()
    confidence_enabled: bool = False
    default_confidence: float = 1.0
    confidence_bands: tuple[ConfidenceBand, ...] = ()
    # The backtest's own.
    capital: float = DEFAULT_CAPITAL
    # fixed: order_seconds and slippage for every order; measured: drawn from WATA's own (src/backtest/execution.py).
    execution: str = FIXED
    signal_delay_seconds: float = DEFAULT_SIGNAL_DELAY["pine"]
    order_seconds: float = 1.0
    close_wait_seconds: float = 0.5
    stop_fill: str = "worst"
    slippage: float = 0.0
    commission_per_order: float = 0.0
    commission_percent: float = 0.0
    # The TA engine's.
    history_days: int = 1
    restart_time: str = "21:15"

    def to_dict(self) -> dict:
        return json.loads(json.dumps(asdict(self)))

    def news_rule(self) -> NewsRule | None:
        """The window around US releases, as the trader keeps it; None while it is off."""
        if not self.news_window:
            return None
        events = tuple(event for event, on in (("FOMC", self.news_fomc), ("CPI", self.news_cpi), ("NFP", self.news_nfp),
                                               ("PCE", self.news_pce)) if on)
        return NewsRule(events, self.news_minutes_before, self.news_minutes_after, self.news_minutes_after_fomc) if events else None

    @classmethod
    def from_dict(cls, values: dict) -> "BacktestSettings":
        known = {entry.name for entry in fields(cls)}
        values = {name: value for name, value in values.items() if name in known}
        if "time_periods" in values:
            values["time_periods"] = tuple(TimePeriod(**period) for period in values["time_periods"])
        if "confidence_bands" in values:
            values["confidence_bands"] = tuple(ConfidenceBand(**band) for band in values["confidence_bands"])
        for name in ("closed_dates", "allowed_indices"):
            if name in values:
                values[name] = tuple(values[name])
        return cls(**values)


class DictConfig:
    """A configuration read from its JSON, answering as ``ConfigurationManager`` does."""

    def __init__(self, config: dict):
        self._config = config

    def get_config_value(self, path: str, default=None):
        node = self._config
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node


def _rule(config: dict, rule_type: str) -> dict | None:
    for rule in (config.get("trade") or {}).get("rules") or []:
        if isinstance(rule, dict) and rule.get("rule_type") == rule_type:
            return rule.get("rule_config") or {}
    return None


def _section(config: dict, *path: str) -> dict:
    node = config
    for part in path:
        node = node.get(part) if isinstance(node, dict) else None
    return node if isinstance(node, dict) else {}


def from_config(config: dict, *, capital: float | None = None) -> BacktestSettings:
    """
    The live rules and settings, read where and as the trader, the position
    monitor, the scheduler and the TA engine read them, with their defaults.
    """
    general = _section(config, "trade", "config", "general")
    day_trading = _rule(config, "day_trading") or {}
    market_hours = _rule(config, "market_hours") or {}
    validation = _rule(config, "signal_validation") or {}
    risk = _rule(config, "risk_management") or {}
    closed = (_rule(config, "market_closed_dates") or {}).get("market_closed_dates") or []
    indices = ((_rule(config, "allowed_indices") or {}).get("indice_ids") or {}).keys()
    thresholds = _section(config, "trade", "config", "position_management", "performance_thresholds")
    trailing = thresholds.get("trailing_stop") or {}
    price_range = _section(config, "trade", "config", "turbo_preference", "price_range")
    buying = _section(config, "trade", "config", "buying_power")
    sizing = _section(config, "trade", "config", "position_sizing")
    time_scaling = sizing.get("time_of_day_scaling") or {}
    confidence = sizing.get("confidence_scaling") or {}
    try:
        engine = TaEngineSettings.from_config(DictConfig(config))
    except ValueError:
        engine = TaEngineSettings()
    news_config = _rule(config, "macro_news")
    try:
        news = NewsRule.from_config(news_config) if news_config is not None else None
    except ValueError:
        news = None
    defaults = BacktestSettings()
    return BacktestSettings(
        timezone=general.get("timezone", defaults.timezone),
        max_signal_age_seconds=validation.get("max_signal_age_seconds", defaults.max_signal_age_seconds),
        trading_start_hour=market_hours.get("trading_start_hour", defaults.trading_start_hour),
        trading_end_hour=market_hours.get("trading_end_hour", defaults.trading_end_hour),
        risky_start_hour=market_hours.get("risky_trading_start_hour", defaults.risky_start_hour),
        risky_start_minute=market_hours.get("risky_trading_start_minute", defaults.risky_start_minute),
        closed_dates=tuple(closed),
        allowed_indices=tuple(indices),
        cooldown_after_loss_minutes=risk.get("cooldown_after_loss_minutes", 0),
        max_trades_per_day=risk.get("max_trades_per_day", 0),
        min_confidence=confidence.get("min_confidence_threshold", 0.0),
        news_window=news is not None,
        news_minutes_before=(news or NewsRule()).minutes_before,
        news_minutes_after=(news or NewsRule()).minutes_after,
        news_minutes_after_fomc=(news or NewsRule()).minutes_after_fomc,
        news_fomc="FOMC" in (news or NewsRule()).events,
        news_cpi="CPI" in (news or NewsRule()).events,
        news_nfp="NFP" in (news or NewsRule()).events,
        news_pce="PCE" in (news or NewsRule()).events,
        no_entry_above_day_profit=day_trading.get(
            "dont_enter_trade_if_day_profit_is_more_than", defaults.no_entry_above_day_profit,
        ),
        max_day_loss=day_trading.get("max_day_loss_percent", defaults.max_day_loss),
        day_profit_target=day_trading.get("percent_profit_wanted_per_days", defaults.day_profit_target),
        close_position_time=day_trading.get("close_position_time", defaults.close_position_time),
        stoploss_percent=thresholds.get("stoploss_percent", defaults.stoploss_percent),
        max_profit_percent=thresholds.get("max_profit_percent", defaults.max_profit_percent),
        trailing_enabled=bool(trailing.get("enabled", False)),
        trailing_activation_percent=trailing.get("activation_percent", defaults.trailing_activation_percent),
        trailing_drawdown_percent=trailing.get("drawdown_percent", defaults.trailing_drawdown_percent),
        turbo_min_price=price_range.get("min", defaults.turbo_min_price),
        turbo_max_price=price_range.get("max", defaults.turbo_max_price),
        funds_percent=buying.get("max_account_funds_to_use_percentage", 100),
        reserve_cash_percent=buying.get("reserve_cash_percent", 0),
        safety_units=(buying.get("safety_margins") or {"bid_calculation": 1}).get("bid_calculation", 1),
        time_scaling_enabled=bool(time_scaling.get("enabled", False)),
        time_periods=tuple(
            TimePeriod(
                start_minute=period["start_hour"] * 60 + period.get("start_minute", 0),
                end_minute=period["end_hour"] * 60 + period.get("end_minute", 0),
                scale_percent=period.get("scale_percent", 100),
                label=period.get("label", ""),
            )
            for period in time_scaling.get("periods", [])
        ),
        confidence_enabled=bool(confidence.get("enabled", False)),
        default_confidence=confidence.get("default_confidence", 1.0),
        confidence_bands=tuple(
            ConfidenceBand(
                least=band["min_confidence"], below=band["max_confidence"],
                scale_percent=band.get("scale_percent", 100), label=band.get("label", ""),
            )
            for band in confidence.get("scaling_rules", [])
        ),
        capital=DEFAULT_CAPITAL if capital is None else float(capital),
        history_days=engine.history_days,
        restart_time=engine.restart_time,
    )


def _checked(entry: Field, value):
    name = entry.name
    if entry.kind == "boolean":
        if not isinstance(value, bool):
            raise InvalidSettings(f"'{entry.label}' is on or off")
        return value
    if entry.kind in ("time",):
        if not isinstance(value, str) or not _TIME_OF_DAY.match(value):
            raise InvalidSettings(f"'{entry.label}' is a time of day, HH:MM")
        return value
    if entry.kind == "choice":
        if value not in entry.choices:
            raise InvalidSettings(f"'{entry.label}' is one of {', '.join(entry.choices)}")
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidSettings(f"'{entry.label}' needs a number")
    if entry.kind == "integer" and value != int(value):
        raise InvalidSettings(f"'{entry.label}' needs a whole number")
    if (entry.least is not None and value < entry.least) or (entry.most is not None and value > entry.most):
        raise InvalidSettings(f"'{entry.label}' must be between {entry.least:g} and {entry.most:g} ({name})")
    return int(value) if entry.kind == "integer" else value


def with_changes(settings: BacktestSettings, changes: dict) -> tuple[BacktestSettings, dict, dict]:
    """
    ``settings`` with the changes a run asked for, each checked: the settings
    to run with, the changes kept (only what differs), and the value each had.
    """
    if not isinstance(changes, dict):
        raise InvalidSettings("The changes are given by setting name")
    kept, before = {}, {}
    for name, value in changes.items():
        entry = FIELDS_BY_NAME.get(name)
        if entry is None:
            raise InvalidSettings(f"'{name}' is not a setting a backtest can change")
        value = _checked(entry, value)
        if value != getattr(settings, name):
            kept[name] = value
            before[name] = getattr(settings, name)
    changed = replace(settings, **kept)
    if changed.trading_start_hour >= changed.trading_end_hour:
        raise InvalidSettings("The hours of entry end before they start")
    if changed.turbo_min_price > changed.turbo_max_price:
        raise InvalidSettings("The turbos' lowest price is above their highest")
    return changed, kept, before


def options(settings: BacktestSettings) -> list[dict]:
    """Every setting a run may change, with its value in ``settings`` (the live one, for the form)."""
    return [{**asdict(entry), "value": getattr(settings, entry.name)} for entry in FIELDS]


def settings_field(name: str) -> Field | None:
    return FIELDS_BY_NAME.get(name)

