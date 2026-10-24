"""
The days the US stock market is closed or closes early, as
``data/us_market_calendar.csv`` keeps them (each row names the official page
it was read from), in New York's hours: Nasdaq, where the Nasdaq 100 is listed,
keeps NYSE's days.

The trader takes no entry on a closed day, and none on a day that closes early
from its early close less the risky minutes it keeps before a normal end of
day; the scheduler and the position monitor sell what is held as long before
an early close as before a normal one (``sale_moment``). The backtests read
the same file and note its digest. The configuration's ``market_closed_dates``
still apply, whatever this says. The file holds whole years: past the 31
December of its last row's year nothing is known, the days are traded as
ordinary ones, and from WARN_DAYS before it the scheduler says so each
morning and health warns.
"""

import csv
import hashlib
import os
from dataclasses import dataclass
from datetime import date, datetime

import pytz

CALENDAR_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "us_market_calendar.csv")
NEW_YORK = pytz.timezone("America/New_York")
CLOSED = "closed"
EARLY_CLOSE = "early_close"
WARN_DAYS = 30


@dataclass(frozen=True)
class MarketDay:
    day: date
    kind: str
    close_new_york: str | None
    name: str

    def close_moment(self) -> float | None:
        """When an early close comes, in epoch seconds; None for a day closed all day."""
        if self.close_new_york is None:
            return None
        hour, minute = (int(part) for part in self.close_new_york.split(":"))
        return NEW_YORK.localize(datetime(self.day.year, self.day.month, self.day.day, hour, minute)).timestamp()


@dataclass(frozen=True)
class MarketCalendar:
    by_day: dict
    digest: str
    until: date | None

    def closed_on(self, day: date) -> MarketDay | None:
        found = self.by_day.get(day)
        return found if found is not None and found.kind == CLOSED else None

    def early_close_on(self, day: date) -> MarketDay | None:
        found = self.by_day.get(day)
        return found if found is not None and found.kind == EARLY_CLOSE else None


def read_market_calendar(path: str = CALENDAR_PATH) -> MarketCalendar:
    """The calendar: lines starting with # are comments."""
    with open(path, "rb") as in_file:
        content = in_file.read()
    rows = csv.DictReader(line for line in content.decode("utf-8").splitlines() if line and not line.startswith("#"))
    by_day = {}
    for row in rows:
        kind = row["kind"]
        if kind not in (CLOSED, EARLY_CLOSE):
            raise ValueError(f"us_market_calendar: {row['date']} is {kind!r}, neither {CLOSED} nor {EARLY_CLOSE}")
        close = row.get("close_new_york") or None
        if (kind == EARLY_CLOSE) != (close is not None):
            raise ValueError(f"us_market_calendar: {row['date']}: an early close has a time, a closed day none")
        day = date.fromisoformat(row["date"])
        by_day[day] = MarketDay(day, kind, close, row["name"])
    until = date(max(by_day).year, 12, 31) if by_day else None
    return MarketCalendar(by_day, hashlib.sha256(content).hexdigest()[:16], until)


def new_york_day(moment: float) -> date:
    return datetime.fromtimestamp(moment, NEW_YORK).date()


def minutes_before_end(end_hour: int, clock_time: str | tuple[int, int]) -> int:
    """How many minutes ``clock_time`` ("21:55" or (21, 55)) is before ``end_hour`` o'clock, on the same day."""
    hour, minute = (int(part) for part in clock_time.split(":")) if isinstance(clock_time, str) else clock_time
    return end_hour * 60 - (hour * 60 + minute)


def entries_end_moment(early: MarketDay, end_hour: int, risky_start: tuple[int, int]) -> float:
    """On a day that closes early, when entries stop: as long before its early close as the risky minutes before a normal end."""
    return early.close_moment() - minutes_before_end(end_hour, risky_start) * 60


def sale_moment(day: date, close_position_time: str, timezone_name: str, calendar: "MarketCalendar | None",
                end_hour: int) -> float:
    """
    When what is held is sold on ``day`` (a day of ``timezone_name``):
    ``close_position_time``, or on a day the US market closes early, as long
    before its early close as ``close_position_time`` is before ``end_hour``.
    """
    early = calendar.early_close_on(day) if calendar is not None else None
    if early is not None:
        return early.close_moment() - minutes_before_end(end_hour, close_position_time) * 60
    hour, minute = (int(part) for part in close_position_time.split(":"))
    return pytz.timezone(timezone_name).localize(datetime(day.year, day.month, day.day, hour, minute)).timestamp()


def closed_text(closed: MarketDay) -> str:
    """What the trader says of an entry on a day the US market is closed; the backtests say the same."""
    return f"Breaking trading rule : Today, {closed.day.isoformat()}, the US market is closed ({closed.name})."


def early_close_text(early: MarketDay, entries_end: float, timezone_name: str) -> str:
    """What the trader says of an entry after the end of entries of a day that closes early; the backtests too."""
    zone = pytz.timezone(timezone_name)
    at = lambda moment: datetime.fromtimestamp(moment, zone).strftime("%H:%M")  # noqa: E731
    return (f"Breaking trading rule: the US market closes early today ({early.name}, {at(early.close_moment())}): "
            f"no entry from {at(entries_end)}.")


def upkeep(calendar: MarketCalendar, today: date) -> str | None:
    """What to say of the calendar from WARN_DAYS before its last known day; None before."""
    if calendar.until is None:
        return "The calendar of US market days is empty: holidays and early closes are not known"
    left = (calendar.until - today).days
    if left > WARN_DAYS:
        return None
    if left < 0:
        return (f"The calendar of US market days ended on {calendar.until.isoformat()}: holidays and early closes "
                "are not known since. Add the next year to src/trade/data/us_market_calendar.csv, then deploy")
    return (f"The calendar of US market days ends on {calendar.until.isoformat()}, in {left} day(s): add the next "
            "year to src/trade/data/us_market_calendar.csv, then deploy")
