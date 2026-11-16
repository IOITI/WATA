"""
The live days: each finished day, newest first, WATA's real result and the paper one (that day's signals replayed
under the live settings, src/admin/paper.py). Whether they are what the live reference led to expect is Edge Watch's
(src/admin/edge_watch.py), trade by trade: a day's range of the reference said nothing under a day target (every
winning day of a 0.5 % target ends at +0.5 %).

Read by the Performance page. Pure.
"""


def live_days(paper_days: list[dict], real_days: dict[str, float]) -> list[dict]:
    """Each day with a paper run or a real result, newest first."""
    days: dict[str, dict] = {}
    for paper in paper_days:
        days.setdefault(paper["day"], {})["paper"] = paper
    for day, result in real_days.items():
        days.setdefault(day, {})["real"] = result
    listed = []
    for day in sorted(days, reverse=True):
        paper, real = days[day].get("paper"), days[day].get("real")
        listed.append({
            "day": day,
            "real_percent": real,
            "paper_percent": paper.get("day_percent") if paper else None,
            "paper_state": paper.get("state") if paper else None,
            "paper_run_id": paper.get("run_id") if paper else None,
            "paper_trades": paper.get("trades") if paper else None,
            # Made with older backtest code, and the version it was made with: made again once the market is closed.
            "paper_stale": paper.get("stale") if paper else None,
            "paper_made_with": paper.get("made_with") if paper else None,
        })
    return listed
