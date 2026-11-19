"""
Where a run's result comes from, and the tail the day's caps hide.

A day's caps (a target that sells everything, no entry above a level, none under a loss, the breakers, a cooldown, a
number of entries) shape a day's trades: a run that buys once and stops at +3 % shows 21 days out of 21 positive while
its entries have no edge. Two runs on the same days and signals tell them apart: the run, and its twin made without
the day's caps (``BacktestService.without_caps``, its request ``decomposes`` the run), each beside the median of its
own random entries.

- ``decomposition`` says it as a 2×2: with the caps and without them, the run's entries and random ones.
- ``tail`` draws the twin's days whole (a day's trades as they came, in their order: what a day without caps did) and
  replays the run's day rules on them, as the trader refuses entries (src/backtest/rules.py ``entry_refusal``, its
  day rules in the same order: the day's result, the cooldown, the entries, the breakers of src/trade/day_breakers.py)
  and as the monitor sells everything once the day would reach its target. It gives how often a day ends at its
  target, at or under -5, -10 and -25 %, beside what the run's own days did. A model: days are drawn as if independent,
  a trade refused changes nothing of the trades after it, and whether a trade's best point came before the day's
  target is not known, so the target is replayed two ways: with each trade's best point first (it may end the day
  sooner) and on the results alone.

Kept out of src/backtest: nothing here changes a run nor the code digest of any (``_CODE``). Pure.
"""

import random
from datetime import datetime
from statistics import median

from src.backtest.rules import DAY_TARGET, compounded
from src.trade.day_breakers import DayBreakers

TAIL_DRAWS = 10_000
LEVELS = (-5.0, -10.0, -25.0)
BEST_FIRST, RESULTS = "best_first", "results"


def _moment(text) -> float | None:
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def day_sequences(days: list[dict], trades: list[dict]) -> list[list[dict]]:
    """A run's tested days, each its trades in the order they were opened: result, best point, when opened and closed."""
    tested = [day["day"] for day in days if day.get("status") == "tested"]
    of_day: dict[str, list[dict]] = {day: [] for day in tested}
    for trade in trades:
        result = trade.get("performance_percent")
        if trade.get("day") not in of_day or result is None:
            continue
        of_day[trade["day"]].append({
            "result": float(result),
            "best": float(trade.get("max_performance_percent") or 0.0),
            "opened": _moment((trade.get("entry") or {}).get("decided_at")),
            "closed": _moment((trade.get("exit") or {}).get("filled_at")),
        })
    return [sorted(of_day[day], key=lambda entry: entry["opened"] or 0.0) for day in tested]


def replay(day: list[dict], settings, bound: str) -> tuple[float, bool]:
    """
    A day's trades under the run's day rules: the day's result in percent, and whether it ended at its target.
    ``settings``: the run's BacktestSettings (its day caps).
    """
    results: list[float] = []
    breakers = DayBreakers(settings.max_consecutive_losses, settings.max_drawdown_from_day_best_percent)
    last_loss_at = None
    entries = 0
    target = settings.day_profit_target
    for trade in day:
        level = compounded(results)
        if level >= settings.no_entry_above_day_profit or level <= settings.max_day_loss:
            break
        if (settings.cooldown_after_loss_minutes > 0 and last_loss_at is not None and trade["opened"] is not None
                and trade["opened"] < last_loss_at + settings.cooldown_after_loss_minutes * 60):
            continue
        if settings.max_trades_per_day > 0 and entries >= settings.max_trades_per_day:
            break
        if breakers.on and breakers.refusal(results) is not None:
            break
        entries += 1
        if bound == BEST_FIRST and compounded([*results, trade["best"]]) >= target:
            # Its best point came first: everything is sold once the day would reach its target.
            needed = ((1 + target / 100) / (1 + level / 100) - 1) * 100
            return round(compounded([*results, needed]), 2), True
        results.append(trade["result"])
        if trade["result"] < 0:
            last_loss_at = trade["closed"]
        if compounded(results) >= target:
            return compounded(results), True
    return compounded(results), False


def _figures(results: list[float], at_target: int | None = None) -> dict:
    ordered = sorted(results)
    count = len(ordered)

    def quantile(share: float) -> float | None:
        return round(ordered[min(count - 1, max(0, round(share * (count - 1))))], 2) if count else None

    return {
        "days": count,
        "losing_share": round(sum(1 for value in ordered if value < 0) / count * 100, 1) if count else None,
        "at_or_under": {f"{level:g}": round(sum(1 for value in ordered if value <= level) / count * 100, 2) if count else None
                        for level in LEVELS},
        "p1": quantile(0.01), "p5": quantile(0.05), "median": quantile(0.5),
        "worst": round(ordered[0], 2) if count else None,
        "at_target_share": round(at_target / count * 100, 1) if count and at_target is not None else None,
    }


def tail(run_days: list[dict], twin_days: list[dict], twin_trades: list[dict], settings, *, seed: int,
         run_trades: list[dict] | None = None) -> dict:
    """
    The run's day rules replayed on its twin's days drawn whole, both ways, beside the run's own days (which ended at
    their target when one of their trades was sold for it, its exit's kind ``day_target``: known only given
    ``run_trades``).
    """
    sequences = [sequence for sequence in day_sequences(twin_days, twin_trades)]
    tested = [day for day in run_days if day.get("status") == "tested"]
    observed = [float(day.get("day_percent") or 0.0) for day in tested]
    at_target = None
    if run_trades is not None:
        ended = {trade.get("day") for trade in run_trades if (trade.get("exit") or {}).get("kind") == DAY_TARGET}
        at_target = sum(1 for day in tested if day.get("day") in ended)
    found = {"draws": TAIL_DRAWS, "days_drawn_from": len(sequences), "levels": list(LEVELS),
             "observed": _figures(observed, at_target), "bounds": {}}
    if not sequences:
        return found
    for bound in (BEST_FIRST, RESULTS):
        draw = random.Random(seed)
        results, at_target = [], 0
        for _ in range(TAIL_DRAWS):
            result, ended = replay(draw.choice(sequences), settings, bound)
            results.append(result)
            at_target += 1 if ended else 0
        found["bounds"][bound] = _figures(results, at_target)
    return found


def _random_median(baseline: dict | None) -> float | None:
    totals = [total for total in (baseline or {}).get("totals") or [] if isinstance(total, (int, float))]
    return round(median(totals), 2) if totals else None


def decomposition(run_summary: dict | None, run_baseline: dict | None, twin_summary: dict | None,
                  twin_baseline: dict | None) -> dict:
    """
    The run and its twin without the day's caps, each beside the median of its random entries: what the caps add
    (the run less its twin) and what the entries add (each less its random entries).
    """
    def total(summary):
        value = ((summary or {}).get("figures") or {}).get("total_percent")
        return round(float(value), 2) if isinstance(value, (int, float)) else None

    def trades(summary):
        return ((summary or {}).get("figures") or {}).get("trades")

    run_total, twin_total = total(run_summary), total(twin_summary)
    run_random, twin_random = _random_median(run_baseline), _random_median(twin_baseline)

    def less(first, second):
        return round(first - second, 2) if first is not None and second is not None else None

    return {
        "with_caps": {"entries": run_total, "random": run_random, "trades": trades(run_summary)},
        "without_caps": {"entries": twin_total, "random": twin_random, "trades": trades(twin_summary)},
        "caps_add": less(run_total, twin_total),
        "entries_add_with_caps": less(run_total, run_random),
        "entries_add_without_caps": less(twin_total, twin_random),
    }
