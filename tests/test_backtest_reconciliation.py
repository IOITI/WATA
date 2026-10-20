"""
A replay of the signals WATA received set beside what WATA did with them
(src/backtest/reconciliation.py).
"""

from datetime import datetime, timezone

import pytest

from src.backtest.reconciliation import reconcile, sentence


def trade(number: int, signal: int, result: float, reason: str, uic: int = 40_000_001, opened: str = "07:00:05") -> dict:
    return {
        "id": number, "signal_id": signal, "day": "2026-10-14", "direction": "long", "performance_percent": result,
        "turbo": {"uic": uic}, "entry": {"filled_at": f"2026-10-14T{opened}Z", "price": 5.02},
        "exit": {"filled_at": "2026-10-14T08:00:00Z", "price": 5.2, "reason": reason},
    }


def position(number: str, result: float, reason: str, uic: int = 40_000_001) -> dict:
    return {
        "position_id": number, "execution_time_open": datetime(2026, 10, 14, 7, 0, 2, tzinfo=timezone.utc),
        "execution_time_close": "2026-10-14T08:00:03Z", "position_open_price": 5.03, "position_close_price": 5.18,
        "position_total_performance_percent": result, "position_close_reason": reason, "instrument_uic": uic,
    }


SIGNALS = [
    {"id": 1, "source": "journal", "signal_uuid": "a", "fate": "executed"},
    {"id": 2, "source": "journal", "signal_uuid": "b", "fate": "executed"},
    {"id": 3, "source": "journal", "signal_uuid": "c", "fate": "executed"},
    {"id": 4, "source": "journal", "signal_uuid": "d", "fate": "refused"},
    {"id": 5, "source": "schedule"},
]
JOURNAL = [
    {"signal_uuid": "a", "outcome": "executed", "position_id": "p-a"},
    {"signal_uuid": "b", "outcome": "executed", "position_id": "p-b"},
    # WATA refused it; the backtest took it.
    {"signal_uuid": "c", "outcome": "refused", "position_id": None},
    # WATA took it; the backtest refused it.
    {"signal_uuid": "d", "outcome": "executed", "position_id": "p-d"},
    # Before the period replayed.
    {"signal_uuid": "e", "outcome": "executed", "position_id": "p-e"},
]
POSITIONS = [
    position("p-a", 3.0, "Performance (Takeprofit (60%) hit at 61%)"),
    position("p-b", -1.0, "Explicit Close (All)", uic=40_000_002),
    position("p-d", 2.0, "Explicit Close (All)"),
    position("p-e", 1.0, "Explicit Close (All)"),
]


def test_each_trade_is_set_beside_the_real_position_of_its_signal():
    found = reconcile(
        [trade(1, 1, 3.5, "Performance (Takeprofit (60%) hit at 62%)"), trade(2, 2, -0.5, "Explicit Close (All)"),
         trade(3, 3, 1.0, "Explicit Close (All)")],
        SIGNALS, JOURNAL, POSITIONS,
    )

    first, second = found["pairs"]
    assert (first["signal_uuid"], first["position_id"], first["entry_seconds"], first["exit_seconds"]) == ("a", "p-a", 3.0, -3.0)
    assert first["same_exit"] and first["same_turbo"] and not second["same_turbo"]
    assert first["result"] == {"backtest": 3.5, "real": 3.0}
    summary = found["summary"]
    assert (summary["paired"], summary["backtest_only"], summary["live_only"]) == (2, 1, 2)
    assert summary["backtest_only_live_outcomes"] == {"refused": 1}
    assert summary["live_only_backtest_fates"] == {"refused": 1, "not_replayed": 1}
    assert summary["mean_result_difference"] == pytest.approx(0.5)
    assert summary["same_exit_percent"] == 100.0 and summary["same_turbo_percent"] == 50.0
    assert (summary["backtest_total_percent"], summary["real_total_percent"]) == (3.0, 2.0)
    assert sentence(summary) == (
        "2 trades set beside WATA's positions of the same signals: the backtest made +3.00 % where WATA made +2.00 %, "
        "+0.50 points a trade in the middle; the same exit 100 % of the time."
    )


def test_nothing_to_set_beside_says_so():
    found = reconcile([trade(1, 9, 1.0, "x")], SIGNALS, [], [])
    assert found["summary"]["paired"] == 0 and found["summary"]["correlation"] is None
    assert sentence(found["summary"]) == "No backtest trade could be set beside a real position of the same signal."
