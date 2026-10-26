"""
The figures of WATA's closed trades grouped as the trading style asks for them, beside the Exits page's own
(src/admin/analysis.py, whose stats_by makes each group's figures, so that a group reads as the page's others):

- by the signal's confidence, and by the turbo's price at entry, in the backtests' own buckets, so that a live
  trade and a backtest's are grouped alike (src/admin/backtests.py);
- by where the signal came from: one of WATA's signal rules (a ``ta_signal`` sent with the same ``signal_uuid``),
  or TradingView's alerts and the signals sent by hand, which the journal does not tell apart;
- by the signal's delay, from its own time to the trader's decision;
- by the worst the position reached (``position_worst``, since v0.48.0).

Kept out of analysis.py, which every backtest's code digest holds. Pure.
"""

from src.admin.analysis import stats_by
from src.admin.backtests import _CONFIDENCES, _PRICES, NO_CONFIDENCE, _confidence_group, _price_group

NOT_JOURNALED = "Not journaled"
TRADINGVIEW_OR_HAND = "TradingView or by hand"
DELAYS = ((5.0, "Under 5 s"), (15.0, "5 to 15 s"), (30.0, "15 to 30 s"), (float("inf"), "30 s and more"))
DELAY_UNKNOWN = "Not known"
WORSTS = ((-10.0, "Under −10 %"), (-5.0, "−10 to −5 %"), (-2.0, "−5 to −2 %"), (0.0, "−2 to 0 %"))
NEVER_UNDER = "Never under its open"
WORST_UNKNOWN = "Not recorded"


def source_of(trade: dict) -> str:
    if not trade.get("journaled"):
        return NOT_JOURNALED
    rule = trade.get("rule_name")
    return f"Rule: {rule}" if rule else TRADINGVIEW_OR_HAND


def delay_of(trade: dict) -> str:
    delay = trade.get("signal_delay_seconds")
    if delay is None:
        return DELAY_UNKNOWN
    return next(label for under, label in DELAYS if delay < under)


def worst_of(trade: dict) -> str:
    worst = trade.get("min_performance_percent")
    if worst is None:
        return WORST_UNKNOWN
    if worst >= 0:
        return NEVER_UNDER
    return next(label for under, label in WORSTS if worst < under)


def live_breakdowns(trades: list[dict]) -> dict:
    """
    ``trades``: the Exits page's closed trades, each with ``confidence``, ``open_price``, ``journaled``,
    ``rule_name``, ``signal_delay_seconds`` and ``min_performance_percent`` where they are known.
    """
    return {
        "by_confidence": stats_by(trades, lambda trade: _confidence_group(trade.get("confidence")),
                                  [label for _, label in _CONFIDENCES] + [NO_CONFIDENCE]),
        "by_source": stats_by(trades, source_of),
        "by_open_price": stats_by(trades, lambda trade: _price_group(trade.get("open_price")), [label for _, label in _PRICES]),
        "by_signal_delay": stats_by(trades, delay_of, [label for _, label in DELAYS] + [DELAY_UNKNOWN]),
        "by_worst": stats_by(trades, worst_of, [label for _, label in WORSTS] + [NEVER_UNDER, WORST_UNKNOWN]),
    }
