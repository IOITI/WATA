"""
WATA's rules for a clock that is not the present: what the trader checks
before an entry (``handle_trading_signal``, ``TradingRule``), how it sizes an
order (``AsyncTradingOrchestrator._calculate_position_scale``,
``_calculate_bid_amount``), and what makes the position monitor sell
(``AsyncPerformanceMonitor._evaluate_positions``).

Pure functions of the moment and the state they are given. Their words are
the live ones, so that the admin reads a backtest's refusals and exits as it
reads the real ones. tests/test_backtest_parity.py runs the live code beside
them on the same inputs: a change to one of those must be made here too.
"""

import math
from dataclasses import dataclass
from functools import lru_cache

from src.backtest.clock import TradingClock
from src.backtest.settings import BacktestSettings
from src.trade.macro_calendar import Calendar, refusal_text, window_at
from src.trade.market_calendar import MarketCalendar, closed_text, early_close_text, entries_end_moment

# Stable names of what refused an entry, for tallies; the text beside each is the live one.
SIGNAL_AGE = "signal_age"
CLOSED_DATE = "closed_date"
MARKET_HOURS = "market_hours"
RISKY_HOURS = "risky_hours"
MACRO_NEWS = "macro_news"
INDICE = "indice"
DUPLICATE = "duplicate"
DAY_PROFIT = "day_profit"
DAY_LOSS = "day_loss"
COOLDOWN = "cooldown"
MAX_TRADES = "max_trades"
CONFIDENCE = "confidence"

# What made the position monitor sell, as the admin groups the real closes (src/admin/analysis.py).
STOP_LOSS = "stop_loss"
TAKE_PROFIT = "take_profit"
TRAILING = "trailing"
DAY_TARGET = "day_target"


@dataclass(frozen=True)
class Refusal:
    rule: str
    reason: str


@dataclass(frozen=True)
class ExitDecision:
    kind: str
    # As the monitor writes it in the position's close reason, without "Performance (...)".
    reason: str
    # The turbo's price at which the threshold is crossed.
    level: float


def compounded(percents) -> float:
    """The result of trades one after the other, each on the whole stake (``_calculate_final_percentage``)."""
    value = 1.0
    for percent in percents:
        value *= 1 + (percent if percent is not None else 0) / 100.0
    return round((value - 1) * 100, 2)


def market_clock(settings: BacktestSettings, clock: TradingClock) -> TradingClock:
    """The clock of the trading hours: the trading one, or the timezone market_hours names."""
    zone = settings.market_zone()
    return clock if zone == clock.name else _clock_of(zone)


@lru_cache(maxsize=4)
def _clock_of(zone: str) -> TradingClock:
    return TradingClock(zone)


def entry_refusal(
    settings: BacktestSettings,
    clock: TradingClock,
    *,
    action: str,
    indice: str,
    decided_at: float,
    alert_at: float,
    open_directions: list[str],
    day_percent: float,
    last_loss_at: float | None,
    entries_today: int,
    confidence: float | None,
    calendar: Calendar | None = None,
    market_calendar: MarketCalendar | None = None,
) -> Refusal | None:
    """
    What refuses an entry, in the trader's order, or None. Nothing is closed
    when an entry is refused. The operator's pause is not part of a backtest,
    nor Saxo's login. The window around US releases needs the ``calendar``
    they are read from, the US holidays and early closes ``market_calendar``.
    """
    # 1. The signal's age, by the clock of the decision (``check_signal_timestamp``).
    if decided_at - int(alert_at) > settings.max_signal_age_seconds:
        return Refusal(SIGNAL_AGE, "Signal timestamp is too old")

    # 2. The market, by the clock of the decision, not the signal's time (``check_market_hours``), in the
    #    timezone of its hours.
    local = market_clock(settings, clock).local(decided_at)
    if local.strftime("%d/%m/%Y") in settings.closed_dates:
        return Refusal(CLOSED_DATE, f"Breaking trading rule : Today, {local}, is a market closed date.")
    closed = market_calendar.closed_on(local.date()) if market_calendar is not None else None
    if closed is not None:
        return Refusal(CLOSED_DATE, closed_text(closed))
    if not (settings.trading_start_hour <= local.hour < settings.trading_end_hour):
        return Refusal(MARKET_HOURS, "Signal is outside of market hours.")
    if (local.hour, local.minute) >= (settings.risky_start_hour, settings.risky_start_minute):
        return Refusal(RISKY_HOURS, "Signal is refused due to risky market hours.")
    early = market_calendar.early_close_on(local.date()) if market_calendar is not None else None
    if early is not None:
        entries_end = entries_end_moment(early, settings.trading_end_hour, (settings.risky_start_hour, settings.risky_start_minute))
        if decided_at >= entries_end:
            return Refusal(RISKY_HOURS, early_close_text(early, entries_end, settings.timezone))

    # 2b. The window around a US release (``check_macro_news``), by the clock of the decision.
    news = settings.news_rule()
    if news is not None and calendar is not None:
        window = window_at(calendar, news, decided_at)
        if window is not None:
            return Refusal(MACRO_NEWS, refusal_text(window, settings.timezone))

    # 3. The indice (``get_allowed_indice_id``).
    if indice not in settings.allowed_indices:
        return Refusal(
            INDICE, f"Breaking trading rule : Indice '{indice}' does not exist in the provided dictionary.",
        )

    # 4. A position of the same direction already open.
    if action in open_directions:
        return Refusal(DUPLICATE, f"Duplicate signal: {action} position already open")

    # 5. The day's result (``check_profit_per_day``).
    if day_percent >= settings.no_entry_above_day_profit:
        return Refusal(DAY_PROFIT, (
            f"Breaking trading rule : The current profit percentage ({day_percent}) is more than the "
            f"allowed percentage ({settings.no_entry_above_day_profit}), so no more trade are allowed for today."
        ))
    if day_percent <= settings.max_day_loss:
        return Refusal(DAY_LOSS, (
            f"Breaking trading rule : The current loss percentage ({day_percent}) has reached the "
            f"maximum allowed loss ({settings.max_day_loss}), so no more trade are allowed for today."
        ))

    # 6. The cooldown after a losing trade (``check_cooldown_after_loss``).
    if settings.cooldown_after_loss_minutes > 0 and last_loss_at is not None:
        cooldown_end = last_loss_at + settings.cooldown_after_loss_minutes * 60
        if decided_at < cooldown_end:
            remaining = cooldown_end - decided_at
            return Refusal(COOLDOWN, (
                f"Breaking trading rule: Cooldown active after last loss. "
                f"Remaining: {remaining:.0f}s (cooldown: {settings.cooldown_after_loss_minutes}min)"
            ))

    # 7. The entries of the day (``check_max_trades_per_day``).
    if settings.max_trades_per_day > 0 and entries_today >= settings.max_trades_per_day:
        return Refusal(MAX_TRADES, (
            f"Breaking trading rule: Maximum trades per day reached ({entries_today}/{settings.max_trades_per_day})."
        ))

    # 8. The signal's confidence (``check_confidence_threshold``).
    if settings.confidence_enabled and confidence is not None and confidence < settings.min_confidence:
        return Refusal(CONFIDENCE, (
            f"Breaking trading rule: Signal confidence ({confidence:.2f}) is below "
            f"minimum threshold ({settings.min_confidence:.2f})."
        ))
    return None


def time_of_day_scale(settings: BacktestSettings, minute_of_day: int) -> float:
    """The share of an order at that local minute, in percent (``_get_time_of_day_scale``)."""
    if not settings.time_scaling_enabled:
        return 100.0
    for period in settings.time_periods:
        if period.start_minute <= minute_of_day < period.end_minute:
            return period.scale_percent
    return 100.0


def confidence_scale(settings: BacktestSettings, confidence: float | None) -> float:
    """The share of an order for that confidence, in percent (``_get_confidence_scale``)."""
    if not settings.confidence_enabled:
        return 100.0
    if confidence is None:
        confidence = settings.default_confidence
    for band in settings.confidence_bands:
        if band.least <= confidence < band.below:
            return band.scale_percent
    return 100.0


def position_scale(settings: BacktestSettings, minute_of_day: int, confidence: float | None) -> float:
    """The share of the funds an entry is given (``_calculate_position_scale``)."""
    return (time_of_day_scale(settings, minute_of_day) / 100.0) * (confidence_scale(settings, confidence) / 100.0)


def available_funds(settings: BacktestSettings, spending_power: float, scale: float) -> float:
    effective = max(0, settings.funds_percent - settings.reserve_cash_percent)
    return spending_power * (effective / 100.0) * scale


def units_for(settings: BacktestSettings, spending_power: float, ask: float, scale: float) -> int:
    """
    How many turbos an order buys (``_calculate_bid_amount``): 0 when the funds
    are short, which the trader takes for "Insufficient funds".
    """
    available = available_funds(settings, spending_power, scale)
    safety = settings.safety_units
    if available < ask * (1 + safety):
        return 0
    return max(0, int(math.floor(available / ask - safety)))


def performance(open_price: float, bid: float) -> float:
    """A position's result at a bid, as the monitor computes it."""
    return round(((bid * 100) / open_price) - 100, 2)


def _percent_text(value) -> str:
    # As an f-string writes the configuration's own value: -15 stays "-15", -15.0 "-15.0".
    return f"{value}"


def exit_decision(
    settings: BacktestSettings,
    *,
    open_price: float,
    perf: float,
    best_before: float,
    day_percent: float,
) -> ExitDecision | None:
    """
    Whether the monitor sells at this performance, and why
    (``_evaluate_positions``): the stop-loss, else the take-profit; then the
    trailing stop on the best seen before; then the day's target with this
    position's result added to the day's.
    """
    stop, take = settings.stoploss_percent, settings.max_profit_percent
    if perf <= stop:
        return ExitDecision(
            STOP_LOSS, f"Stoploss ({_percent_text(stop)}%) hit at {perf}%", open_price * (1 + stop / 100.0),
        )
    if perf >= take:
        return ExitDecision(
            TAKE_PROFIT, f"Takeprofit ({_percent_text(take)}%) hit at {perf}%", open_price * (1 + take / 100.0),
        )
    if settings.trailing_enabled:
        activation, drawdown_limit = settings.trailing_activation_percent, settings.trailing_drawdown_percent
        if best_before >= activation:
            drawdown = best_before - perf
            if drawdown >= drawdown_limit:
                return ExitDecision(
                    TRAILING,
                    f"Trailing Stop: peak {best_before:.1f}%, now {perf:.1f}% (dd {drawdown:.1f}% >= {_percent_text(drawdown_limit)}%)",
                    open_price * (1 + (best_before - drawdown_limit) / 100.0),
                )
    target = settings.day_profit_target
    potential = round(((1 + day_percent / 100.0) * (1 + perf / 100.0) - 1) * 100, 2)
    if potential >= target:
        needed = ((1 + target / 100.0) / (1 + day_percent / 100.0) - 1) * 100
        return ExitDecision(
            DAY_TARGET,
            f"Daily profit target ({_percent_text(target)}%) potentially met ({potential}%)",
            open_price * (1 + needed / 100.0),
        )
    return None


def target_level(settings: BacktestSettings, open_price: float, day_percent: float) -> float:
    """The turbo's price at which the day's target is met, for a chart."""
    needed = ((1 + settings.day_profit_target / 100.0) / (1 + day_percent / 100.0) - 1) * 100
    return open_price * (1 + needed / 100.0)
