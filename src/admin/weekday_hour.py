"""
Closed trades by the weekday and hour they were opened, for the heatmaps of
the Exits page and of a backtest's breakdowns, with the figures of their other
groups (``stats_by``).

Kept out of ``analysis.py``, which is part of every backtest's code digest
(``_CODE`` in ``src/backtest/figures.py``): a change there marks every run as
made with other code, while this decides nothing a run finds.
"""

from src.admin.analysis import stats_by


def stats_by_weekday_hour(trades: list[dict], opened) -> list[dict]:
    """
    The figures of the trades of each weekday and hour that had some, Monday
    first: ``opened`` gives a trade's ISO weekday (1 is Monday) and hour, or
    None to leave it out.
    """
    rows = stats_by(trades, opened, name="opened")
    return [{"weekday": row["opened"][0], "hour": row["opened"][1], **_without(row, "opened")} for row in rows]


def opened_weekday_hour(trade: dict) -> tuple[int, int] | None:
    """A closed position's ISO weekday and hour of opening, as the Exits query reads them."""
    weekday, hour = trade.get("open_weekday"), trade.get("open_hour")
    return None if weekday is None or hour is None else (weekday, hour)


def _without(row: dict, key: str) -> dict:
    return {name: value for name, value in row.items() if name != key}
