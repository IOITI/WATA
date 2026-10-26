"""
What a configuration the validators accept may still get wrong, said before it
is saved: parameters that work against each other. Nothing here refuses a
save; the validators do that.
"""

from src.admin.config_catalog import flatten
from src.admin.config_catalog.trading import CLOSE_TIME, END_HOUR, MAX_PROFIT, TRAILING
from src.trade.signal_sources import CONFIG_PATH as SIGNAL_SOURCES, sources_from, unattended, unheard

ADMIN_IDLE = "admin.session_idle_minutes"
ADMIN_MAX_AGE = "admin.session_max_age_minutes"


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _minutes(clock) -> int | None:
    try:
        hour, minute = (int(part) for part in str(clock).split(":"))
    except (TypeError, ValueError):
        return None
    return hour * 60 + minute


def warnings(config: dict, live_indices=()) -> list[dict]:
    """
    Each parameter that works against another, with what it means: ``{"path", "message"}``. ``live_indices``:
    the indices a signal rule is live on now (rules.json).
    """
    values = flatten(config)
    found = []

    sources = sources_from(values.get(SIGNAL_SOURCES))
    for indice in unheard(sources, live_indices):
        found.append({"path": SIGNAL_SOURCES, "message": (
            f"A signal rule is live on {indice}, which would take TradingView's signals: once the TA engine starts "
            "again it sends that rule's signals no more, and TradingView's alerts trade it."
        )})
    for indice in unattended(sources, live_indices):
        found.append({"path": SIGNAL_SOURCES, "message": (
            f"{indice} would take the TA engine's signals and no rule is live on it: once the web server starts again "
            "TradingView's alerts are ignored and nothing trades it, until a rule goes live."
        )})

    end_hour, close = _number(values.get(END_HOUR)), _minutes(values.get(CLOSE_TIME))
    if end_hour is not None and close is not None and close >= end_hour * 60:
        found.append({"path": CLOSE_TIME, "message": (
            f"The close of the day ({values.get(CLOSE_TIME)}) comes at or after the end of trading ({end_hour}:00): "
            "from the end to the close, positions are held while no entry is taken."
        )})

    trailing_on = values.get(f"{TRAILING}.enabled") is True
    activation, take_profit = _number(values.get(f"{TRAILING}.activation_percent", 5)), _number(values.get(MAX_PROFIT))
    if trailing_on and activation is not None and take_profit is not None and activation >= take_profit:
        found.append({"path": f"{TRAILING}.activation_percent", "message": (
            f"The trailing stop arms at {activation} %, at or above the take-profit ({take_profit} %): a position is "
            "sold before it ever arms."
        )})

    idle, max_age = _number(values.get(ADMIN_IDLE, 60)), _number(values.get(ADMIN_MAX_AGE, 720))
    if idle is not None and max_age is not None and max_age < idle:
        found.append({"path": ADMIN_MAX_AGE, "message": (
            f"A session is closed after {max_age} min whatever its use, before it could be idle for {idle} min."
        )})

    if values.get("trade.config.ta_engine.enabled") is True and values.get("trade.config.capital_feed.enabled") is not True:
        found.append({"path": "trade.config.ta_engine.enabled", "message": (
            "The TA engine runs the signal rules on the bars the Capital.com feed records, and the feed is off: no "
            "rule will have a bar."
        )})

    if values.get("trade.config.position_management.wait_for_fresh_quote") is False:
        found.append({"path": "trade.config.position_management.wait_for_fresh_quote", "message": (
            "A position just bought will be judged on a quote older than its purchase: on 19 October 2026 that sold "
            "a position at a stop it had not reached."
        )})
    return found
