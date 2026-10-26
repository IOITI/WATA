"""
Which source each index takes its signals from (``trade.config.signal_sources``):
TradingView's alerts, through the web server's webhook, unless the index is set
to the TA engine, whose live rule then trades it alone.

Both feed the web server's store, which keeps the newest signal of each index:
with a rule live beside TradingView's alerts, the two strategies' signals would
follow one another, each closing what the other opened. So an index set to the
engine takes no webhook (the web server answers it, counts it and stores nothing),
a rule goes live only on an index set to the engine, and the engine sends nothing
for one that is not. The scheduler's close and the signals sent by hand from the
admin come through ``/internal/signal`` and are taken whatever the source.

Read by the web server, the TA engine, ConfigurationManager and the admin. Pure.
"""

TRADINGVIEW = "tradingview"
ENGINE = "engine"
SOURCES = (TRADINGVIEW, ENGINE)
CONFIG_PATH = "trade.config.signal_sources"


def sources_from(value) -> dict[str, str]:
    """The sources set, by index: an index absent from them takes TradingView's signals."""
    if not isinstance(value, dict):
        return {}
    return {str(indice): source for indice, source in value.items() if source in SOURCES}


def configured(config_manager) -> dict[str, str]:
    """The sources of the configuration ``config_manager`` read."""
    return sources_from(config_manager.get_config_value(CONFIG_PATH))


def source_of(sources: dict[str, str], indice: str) -> str:
    return sources.get(indice, TRADINGVIEW)


def refusal(value, indices=None) -> str | None:
    """Why ``value`` is not a setting of the sources (None when it is, or when it is absent)."""
    if value is None:
        return None
    if not isinstance(value, dict):
        return "must be an object of indices, each with its source (tradingview or engine)"
    for indice, source in value.items():
        if indices is not None and indice not in indices:
            return f"{indice} is not one of the allowed indices"
        if source not in SOURCES:
            return f"{indice}: {source!r} is not a source (tradingview or engine)"
    return None


def unattended(sources: dict[str, str], live_indices) -> list[str]:
    """The indices set to the engine on which no rule is live: nothing sends them a signal."""
    live = set(live_indices)
    return sorted(indice for indice, source in sources.items() if source == ENGINE and indice not in live)


def unheard(sources: dict[str, str], live_indices) -> list[str]:
    """The indices a rule is live on that do not take the engine's signals: the engine sends it nothing."""
    return sorted({indice for indice in live_indices if source_of(sources, indice) != ENGINE})
