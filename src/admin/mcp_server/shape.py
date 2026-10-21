"""
How the tools' answers are made small enough to read: numbers to six
significant digits but never fewer than three decimals (an index's price keeps
its thousandths), long lists a page at a time, tables as CSV when they are
meant for a file, and the lock box's days refused.
"""

import csv
import io
import math
from datetime import date, datetime, timezone

from src.admin.mcp_server.budget import Refused

DEFAULT_PAGE = 50
MAX_PAGE = 500


def compacted(value):
    """
    The same data with every float to six significant digits (a figure, a price, a
    percentage), but never fewer than three decimals: six digits of Dukascopy's
    29143.543 were 29143.5, which hid the third decimal its range bars count in.
    """
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return float(f"{value:.6g}") if abs(value) < 1000 else round(value, 3)
    if isinstance(value, dict):
        return {key: compacted(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [compacted(item) for item in value]
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, date):
        return value.isoformat()
    return value


def iso_moment(epoch) -> str | None:
    """A moment of epoch seconds as a time of UTC."""
    return None if epoch is None else datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def page(items: list, offset: int, limit: int) -> dict:
    """A page of a list, with where the next one starts (None: there is no more)."""
    if offset < 0 or not 1 <= limit <= MAX_PAGE:
        raise Refused(f"A page starts at 0 or after and holds 1 to {MAX_PAGE} rows")
    chosen = items[offset:offset + limit]
    following = offset + len(chosen)
    return {"total": len(items), "offset": offset, "items": chosen, "next_offset": following if following < len(items) else None}


def csv_text(columns: list[str], rows) -> str:
    """A table as CSV, its first line the columns: what an export answers, saved to a file on the Mac."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow(["" if cell is None else cell for cell in compacted(list(row))])
    return out.getvalue()


def period(first: date, last: date | None, longest_days: int) -> tuple[date, date]:
    last = last or first
    if last < first:
        raise Refused("The period ends before it starts")
    if (last - first).days >= longest_days:
        raise Refused(f"A period of {longest_days} days at most here")
    return first, last


def refuse_lockbox(journal, first: date, last: date) -> None:
    """The lock box's days are looked at only by a backtest asked for as a candidate's final check."""
    if journal.in_lockbox(first, last):
        box = journal.lockbox()
        days = f"from {box['from'].isoformat()} to {box['to'].isoformat()}" if box["to"] else f"from {box['from'].isoformat()} on"
        raise Refused(
            f"The days {days} are the lock box: they are looked at only by a "
            "backtest asked for as a candidate's final check (backtest_start with lockbox_final)"
        )
