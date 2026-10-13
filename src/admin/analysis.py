"""
How closed trades ended: by exit reason, by hour of entry, by holding time, and
how much of the best performance seen during a trade was kept at its close.
"""

from statistics import mean

# The position monitor and the trader write free-text close reasons, e.g.
# "Performance (Stoploss (-15%) hit at -16.2%)" or "Explicit Close (All)".
# First match wins, so the single-position close comes before the general one.
_EXIT_GROUPS = (
    ("Stop-loss", "Stoploss"),
    ("Take-profit", "Takeprofit"),
    ("Trailing stop", "Trailing Stop"),
    ("Daily profit target", "Daily profit target"),
    ("Operator (one position)", "Explicit Close (position"),
    ("Signal or end of day", "Explicit Close"),
    ("Closed at Saxo", "SaxoAPI"),
)

_HOLDING_BUCKETS = (
    (5 * 60, "Under 5 min"),
    (15 * 60, "5 to 15 min"),
    (60 * 60, "15 min to 1 h"),
    (4 * 60 * 60, "1 to 4 h"),
    (float("inf"), "Over 4 h"),
)


# The holding times a trade is put in, shortest first.
HOLDING_LABELS = tuple(label for _, label in _HOLDING_BUCKETS)


def exit_group(close_reason: str | None) -> str:
    if not close_reason:
        return "Not recorded"
    for label, marker in _EXIT_GROUPS:
        if marker in close_reason:
            return label
    return "Other"


def holding_bucket(holding_seconds: float | None) -> str | None:
    if holding_seconds is None or holding_seconds < 0:
        return None
    for limit, label in _HOLDING_BUCKETS:
        if holding_seconds < limit:
            return label
    return _HOLDING_BUCKETS[-1][1]


def _rounded_mean(values: list[float]) -> float | None:
    return round(mean(values), 2) if values else None


def _stats(trades: list[dict]) -> dict:
    percents = [trade.get("performance_percent") or 0 for trade in trades]
    best = [trade["max_performance_percent"] for trade in trades if trade.get("max_performance_percent") is not None]
    wins = sum(1 for percent in percents if percent > 0)
    return {
        "trade_count": len(trades),
        "win_count": wins,
        "loss_count": sum(1 for percent in percents if percent < 0),
        "win_rate": round(100.0 * wins / len(trades), 2) if trades else None,
        "profit_loss": round(sum(trade.get("profit_loss") or 0 for trade in trades), 2),
        "average_percent": _rounded_mean(percents),
        "average_best_percent": _rounded_mean(best),
    }


def _grouped(trades: list[dict], key) -> dict:
    groups: dict = {}
    for trade in trades:
        group = key(trade)
        if group is not None:
            groups.setdefault(group, []).append(trade)
    return groups


def stats_by(trades: list[dict], key, order: list | None = None, name: str = "group") -> list[dict]:
    """
    The figures of the trades of each group ``key`` puts them in (None: in
    none), under ``name``. In ``order`` when the groups have one, empty ones
    included; else the groups there are, sorted.
    """
    groups = _grouped(trades, key)
    return [{name: group, **_stats(groups.get(group, []))} for group in (order if order is not None else sorted(groups))]


def given_back(trades: list[dict]) -> dict:
    """How much of the best level seen during a trade was lost by its close, over the trades that have one."""
    with_best = [trade for trade in trades if trade.get("max_performance_percent") is not None]
    turned_negative = sum(
        1 for trade in with_best
        if trade["max_performance_percent"] > 0 and (trade.get("performance_percent") or 0) < 0
    )
    return {
        # Percentage points between the best level reached and the close.
        "average_given_back": _rounded_mean([
            trade["max_performance_percent"] - (trade.get("performance_percent") or 0) for trade in with_best
        ]),
        # In profit at some point, closed at a loss.
        "turned_negative_count": turned_negative,
        "turned_negative_percent": round(100.0 * turned_negative / len(with_best), 2) if with_best else None,
    }


def summarise_exits(trades: list[dict]) -> dict:
    """
    ``trades``: closed trades with ``close_reason``, ``performance_percent``,
    ``max_performance_percent``, ``profit_loss``, ``open_hour`` and ``holding_seconds``.
    """
    by_reason = _grouped(trades, lambda trade: exit_group(trade.get("close_reason")))
    by_hour = _grouped(trades, lambda trade: trade.get("open_hour"))
    by_holding = _grouped(trades, lambda trade: holding_bucket(trade.get("holding_seconds")))

    return {
        "summary": {**_stats(trades), **given_back(trades)},
        "by_reason": sorted(
            ({"reason": reason, **_stats(group)} for reason, group in by_reason.items()),
            key=lambda entry: entry["trade_count"],
            reverse=True,
        ),
        "by_hour": [{"hour": hour, **_stats(by_hour[hour])} for hour in sorted(by_hour)],
        "by_holding_time": [
            {"bucket": label, **_stats(by_holding.get(label, []))} for _, label in _HOLDING_BUCKETS
        ],
    }
