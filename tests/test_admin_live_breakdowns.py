"""WATA's closed trades grouped as the trading style asks (src/admin/live_breakdowns.py)."""

from src.admin.live_breakdowns import live_breakdowns


def trade(percent: float, **more) -> dict:
    return {"performance_percent": percent, "max_performance_percent": max(percent, 0.5), "profit_loss": percent * 2,
            **more}


TRADES = [
    trade(2.0, confidence=0.9, open_price=4.5, journaled=True, signal_delay_seconds=3.2, min_performance_percent=0.0),
    trade(-6.0, confidence=0.3, open_price=8.0, journaled=True, signal_delay_seconds=22.0, rule_name="Kernel VWAP",
          min_performance_percent=-7.5),
    trade(-1.0, confidence=None, open_price=12.0, journaled=True, signal_delay_seconds=55.0, min_performance_percent=-1.4),
    trade(0.5, open_price=None, journaled=False),
]


def group(groups: list[dict], name: str) -> dict:
    return next(entry for entry in groups if entry["group"] == name)


def test_each_trade_is_in_the_backtests_own_buckets_of_confidence_and_price():
    found = live_breakdowns(TRADES)

    assert [entry["group"] for entry in found["by_confidence"]] == ["Under 0.5", "0.5 to 1", "1 to 1.5", "1.5 and above", "Not given"]
    assert group(found["by_confidence"], "Not given")["trade_count"] == 2
    assert group(found["by_confidence"], "Under 0.5")["average_percent"] == -6.0
    assert [entry["trade_count"] for entry in found["by_open_price"]] == [1, 0, 1, 1, 0]


def test_the_source_the_delay_and_the_worst_are_grouped_as_said():
    found = live_breakdowns(TRADES)

    assert {entry["group"]: entry["trade_count"] for entry in found["by_source"]} == {
        "Not journaled": 1, "Rule: Kernel VWAP": 1, "TradingView or by hand": 2,
    }
    assert [entry["trade_count"] for entry in found["by_signal_delay"]] == [1, 0, 1, 1, 1]
    assert {entry["group"]: entry["trade_count"] for entry in found["by_worst"]} == {
        "Under −10 %": 0, "−10 to −5 %": 1, "−5 to −2 %": 0, "−2 to 0 %": 1, "Never under its open": 1, "Not recorded": 1,
    }


def test_a_price_exit_is_told_decided_on_saxos_quote_or_on_the_index_between_its_quotes():
    found = live_breakdowns([
        trade(-15.9, close_reason="Performance (Stoploss (-15%) hit at -15.9%; US100 between quotes: 8.452 from 10, 10 s)"),
        trade(-15.4, close_reason="Performance (Stoploss (-15%) hit at -15.4%)"),
        trade(1.8, close_reason="Performance (Daily profit target (1.7%) potentially met (1.8%); US100 between quotes)"),
        trade(0.4, close_reason="Explicit Close (All)"),
        trade(0.1, close_reason=None),
    ])

    assert {entry["group"]: entry["trade_count"] for entry in found["by_decided_on"]} == {
        "Saxo's quote": 1, "The index, between Saxo's quotes": 2, "Not a price exit": 2,
    }
    assert group(found["by_decided_on"], "The index, between Saxo's quotes")["average_percent"] == -7.05
