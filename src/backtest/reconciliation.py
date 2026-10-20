"""
A replay of the signals WATA received, set beside what WATA really did with
them: each backtest trade paired with the real position of the same signal
(``signal_uuid``, through the journal's ``position_id``), and the trades only
one side made, with what the other side did of their signal. Pure.

How far a replay's results are from the real ones on the same signals is
what to discount every other backtest by.
"""

import math
from datetime import datetime, timezone
from statistics import fmean, median

from src.admin.analysis import exit_group


def _moment(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, datetime):
        return value.timestamp()
    text = str(value).replace("Z", "+00:00")
    try:
        found = datetime.fromisoformat(text)
    except ValueError:
        return None
    return (found if found.tzinfo else found.replace(tzinfo=timezone.utc)).timestamp()


def _rounded(value: float | None, digits: int = 3) -> float | None:
    return None if value is None or not math.isfinite(value) else round(value, digits)


def _difference(first, second, digits: int = 3) -> float | None:
    return _rounded(first - second, digits) if first is not None and second is not None else None


def _pair(trade: dict, position: dict, uuid: str) -> dict:
    entry = trade.get("entry") or {}
    exit_ = trade.get("exit") or {}
    real_reason = position.get("position_close_reason") or ""
    reason = exit_.get("reason") or ""
    return {
        "signal_uuid": uuid,
        "trade_id": trade["id"],
        "position_id": position["position_id"],
        "day": trade.get("day"),
        "direction": trade.get("direction"),
        # Seconds the backtest bought and sold after (positive) or before WATA.
        "entry_seconds": _difference(_moment(entry.get("filled_at")), _moment(position.get("execution_time_open")), 1),
        "exit_seconds": _difference(_moment(exit_.get("filled_at")), _moment(position.get("execution_time_close")), 1),
        "same_turbo": (trade.get("turbo") or {}).get("uic") == position.get("instrument_uic")
        if position.get("instrument_uic") is not None else None,
        "entry_price": {"backtest": entry.get("price"), "real": position.get("position_open_price")},
        "exit_price": {"backtest": exit_.get("price"), "real": position.get("position_close_price")},
        "result": {"backtest": _rounded(trade.get("performance_percent"), 2),
                   "real": _rounded(position.get("position_total_performance_percent"), 2)},
        "exit": {"backtest": reason, "real": real_reason},
        "same_exit": exit_group(reason) == exit_group(real_reason),
    }


def reconcile(trades: list[dict], signals: list[dict], journal: list[dict], positions: list[dict]) -> dict:
    """The run's trades beside WATA's real positions of the same signals."""
    received = [signal for signal in signals if signal.get("source") == "journal" and signal.get("signal_uuid")]
    uuid_of_signal = {signal["id"]: signal["signal_uuid"] for signal in received}
    fate_of = {signal["signal_uuid"]: signal.get("fate") for signal in received}
    live_outcome_of = {row.get("signal_uuid"): row.get("outcome") for row in journal}
    position_of = {row["signal_uuid"]: row["position_id"] for row in journal if row.get("signal_uuid") and row.get("position_id")}
    positions_by_id = {str(position["position_id"]): position for position in positions}

    pairs, backtest_only = [], []
    for trade in trades:
        uuid = uuid_of_signal.get(trade.get("signal_id"))
        position = positions_by_id.get(str(position_of.get(uuid))) if uuid else None
        if position is None:
            backtest_only.append({"trade_id": trade["id"], "signal_uuid": uuid, "live_outcome": live_outcome_of.get(uuid)})
            continue
        pairs.append(_pair(trade, position, uuid))
    paired = {pair["signal_uuid"] for pair in pairs}
    in_replay = set(fate_of)
    live_only = [
        {"signal_uuid": uuid, "position_id": position_id, "backtest_fate": fate_of.get(uuid) if uuid in in_replay else "not_replayed"}
        for uuid, position_id in position_of.items()
        if str(position_id) in positions_by_id and uuid not in paired
    ]

    differences = [pair["result"]["backtest"] - pair["result"]["real"] for pair in pairs
                   if pair["result"]["backtest"] is not None and pair["result"]["real"] is not None]
    backtest_results = [pair["result"]["backtest"] for pair in pairs if pair["result"]["real"] is not None and pair["result"]["backtest"] is not None]
    real_results = [pair["result"]["real"] for pair in pairs if pair["result"]["real"] is not None and pair["result"]["backtest"] is not None]
    correlation = None
    if len(backtest_results) >= 3:
        mean_b, mean_r = fmean(backtest_results), fmean(real_results)
        spread_b = math.sqrt(sum((value - mean_b) ** 2 for value in backtest_results))
        spread_r = math.sqrt(sum((value - mean_r) ** 2 for value in real_results))
        if spread_b > 0 and spread_r > 0:
            correlation = round(sum((b - mean_b) * (r - mean_r) for b, r in zip(backtest_results, real_results)) / (spread_b * spread_r), 3)
    turbos = [pair["same_turbo"] for pair in pairs if pair["same_turbo"] is not None]
    entry_delays = [pair["entry_seconds"] for pair in pairs if pair["entry_seconds"] is not None]

    def tally(rows: list[dict], key: str) -> dict:
        found: dict = {}
        for row in rows:
            found[row.get(key) or "unknown"] = found.get(row.get(key) or "unknown", 0) + 1
        return dict(sorted(found.items(), key=lambda item: -item[1]))

    return {
        "summary": {
            "paired": len(pairs),
            "backtest_only": len(backtest_only),
            "live_only": len(live_only),
            # What WATA did of the signals of the trades only the backtest made, and the backtest of those only WATA made.
            "backtest_only_live_outcomes": tally(backtest_only, "live_outcome"),
            "live_only_backtest_fates": tally(live_only, "backtest_fate"),
            "mean_result_difference": _rounded(fmean(differences)) if differences else None,
            "median_result_difference": _rounded(median(differences)) if differences else None,
            "correlation": correlation,
            "same_exit_percent": _rounded(sum(1 for pair in pairs if pair["same_exit"]) / len(pairs) * 100, 1) if pairs else None,
            "same_turbo_percent": _rounded(sum(1 for same in turbos if same) / len(turbos) * 100, 1) if turbos else None,
            "median_entry_seconds": _rounded(median(entry_delays), 1) if entry_delays else None,
            "backtest_total_percent": _rounded(sum(backtest_results), 2) if backtest_results else None,
            "real_total_percent": _rounded(sum(real_results), 2) if real_results else None,
        },
        "pairs": pairs,
        "backtest_only": backtest_only,
        "live_only": live_only,
    }


def sentence(summary: dict) -> str:
    """What the comparison says, for the run's summary and the lab."""
    if not summary["paired"]:
        return "No backtest trade could be set beside a real position of the same signal."
    return (
        f"{summary['paired']} trades set beside WATA's positions of the same signals: the backtest made "
        f"{summary['backtest_total_percent']:+.2f} % where WATA made {summary['real_total_percent']:+.2f} %, "
        f"{summary['median_result_difference']:+.2f} points a trade in the middle; the same exit "
        f"{summary['same_exit_percent']:.0f} % of the time."
    )
