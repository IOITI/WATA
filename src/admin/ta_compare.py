"""
A signal rule's signals set beside TradingView's on one day: what WATA's engine
raised (ta_signal) against what the trader received for the rule's indice from
anywhere else (signal_journal, less the signals WATA sent itself), paired by
action and time. Pure: the admin reads both sides and hands them over.

TradingView's signal_timestamp is the open of the bar that raised the alert
({{time}} in its messages), WATA's bar_time the same of its own bar: the two
are paired when their actions agree and their times are within the tolerance.
Each signal is paired once, with the nearest one that agrees; what stays alone
is said to be WATA's only, or TradingView's only.
"""

from datetime import datetime

from src.admin.ta_rules import timeframe_seconds

# A close of TradingView that names its side is the same as WATA's close-position.
_SAME_ACTION = {"close-long": "close-position", "close-short": "close-position"}


def default_tolerance(bars: dict) -> int:
    """Two bars of a rule's length; five minutes for range bars, whose bars are not of the same time."""
    if bars.get("type") == "time":
        return 2 * (timeframe_seconds(bars["timeframe"]) or 60)
    return 300


def _action(action: str | None) -> str | None:
    return _SAME_ACTION.get(action, action)


def _seconds(moment) -> float | None:
    return moment.timestamp() if isinstance(moment, datetime) else None


def compare(wata: list[dict], tradingview: list[dict], tolerance_seconds: int) -> dict:
    """
    Pairs of (WATA's signal, TradingView's signal), either of them None when it
    stayed alone, in time order, and how many of each kind.
    """
    ours = sorted(
        (signal for signal in wata if signal.get("action") and _seconds(signal.get("bar_time")) is not None),
        key=lambda signal: signal["bar_time"],
    )
    theirs = sorted(
        (signal for signal in tradingview if _seconds(signal.get("signal_timestamp")) is not None),
        key=lambda signal: signal["signal_timestamp"],
    )
    taken: set[int] = set()
    rows = []
    for signal in ours:
        at = _seconds(signal["bar_time"])
        best, best_delta = None, None
        for index, other in enumerate(theirs):
            if index in taken or _action(other.get("action")) != _action(signal["action"]):
                continue
            delta = _seconds(other["signal_timestamp"]) - at
            if abs(delta) <= tolerance_seconds and (best_delta is None or abs(delta) < abs(best_delta)):
                best, best_delta = index, delta
        if best is None:
            rows.append({"at": signal["bar_time"], "action": signal["action"], "wata": signal, "tradingview": None, "delta_seconds": None})
            continue
        taken.add(best)
        rows.append({
            "at": signal["bar_time"], "action": signal["action"], "wata": signal, "tradingview": theirs[best],
            "delta_seconds": round(best_delta),
        })
    for index, other in enumerate(theirs):
        if index not in taken:
            rows.append({
                "at": other["signal_timestamp"], "action": other.get("action"), "wata": None, "tradingview": other,
                "delta_seconds": None,
            })
    rows.sort(key=lambda row: row["at"])
    paired = sum(1 for row in rows if row["wata"] and row["tradingview"])
    return {
        "tolerance_seconds": tolerance_seconds,
        "rows": rows,
        "paired": paired,
        "only_wata": sum(1 for row in rows if row["wata"] and not row["tradingview"]),
        "only_tradingview": sum(1 for row in rows if row["tradingview"] and not row["wata"]),
    }
