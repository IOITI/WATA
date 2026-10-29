"""
The live settings against their reference, a replay of the signals WATA received that the operator chose
on its run's page: each finished day, newest first, WATA's real result and the paper one (that day's
signals replayed under the live settings, src/admin/paper.py), each outside the reference's daily 5 %–95 %
range or not; and a warning once the last ``OUTSIDE_IN_A_ROW`` days that have a result were outside. While
entries are paused, only the paper days have one.

Read by the paper task, which says the warning once on Telegram, by health and by the Performance page. Pure.
"""

OUTSIDE_IN_A_ROW = 2


def _outside(result: float | None, reference: dict | None) -> bool | None:
    if result is None or not reference or reference.get("low") is None:
        return None
    return result < reference["low"] or result > reference["high"]


def live_days(paper_days: list[dict], real_days: dict[str, float], reference: dict | None) -> list[dict]:
    """Each day with a paper run or a real result, newest first, each result set against the reference's range."""
    days: dict[str, dict] = {}
    for paper in paper_days:
        days.setdefault(paper["day"], {})["paper"] = paper
    for day, result in real_days.items():
        days.setdefault(day, {})["real"] = result
    listed = []
    for day in sorted(days, reverse=True):
        paper, real = days[day].get("paper"), days[day].get("real")
        paper_percent = paper.get("day_percent") if paper else None
        listed.append({
            "day": day,
            "real_percent": real,
            "real_outside": _outside(real, reference),
            "paper_percent": paper_percent,
            "paper_outside": _outside(paper_percent, reference),
            "paper_state": paper.get("state") if paper else None,
            "paper_run_id": paper.get("run_id") if paper else None,
            "paper_trades": paper.get("trades") if paper else None,
            # Made with older backtest code, and the version it was made with: made again once the market is closed.
            "paper_stale": paper.get("stale") if paper else None,
            "paper_made_with": paper.get("made_with") if paper else None,
        })
    return listed


def _said(day: dict) -> str:
    parts = []
    if day["real_outside"]:
        parts.append(f"real {day['real_percent']:+.2f} %")
    if day["paper_outside"]:
        parts.append(f"paper {day['paper_percent']:+.2f} %")
    return f"{day['day']} {', '.join(parts)}"


def warning(days: list[dict], reference: dict | None) -> str | None:
    """What is said when the last OUTSIDE_IN_A_ROW days that have a result were all outside the range; None otherwise."""
    judged = [day for day in days if day["real_outside"] is not None or day["paper_outside"] is not None]
    last = judged[:OUTSIDE_IN_A_ROW]
    if len(last) < OUTSIDE_IN_A_ROW or not all(day["real_outside"] or day["paper_outside"] for day in last):
        return None
    days_said = "; ".join(_said(day) for day in last)
    return (
        f"{OUTSIDE_IN_A_ROW} days in a row outside the live reference's daily range "
        f"({reference['low']:+.2f} to {reference['high']:+.2f} %): {days_said}"
    )
