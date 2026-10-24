"""
The US macro news that moves the Nasdaq 100: FOMC statements, CPI, the jobs
report (NFP) and the PCE price index, as ``data/macro_calendar.csv`` keeps
them. Each row is a release's day and New York time, and the official page it
was read from.

The trader keeps entries out of a window around each release when the
``macro_news`` rule of ``trade.rules`` says so (``NewsRule``, ``window_at``),
and the position monitor sells what is held from ``flatten_minutes_before``
a release when the rule gives it (``flat_window_at``): around a release the
issuer may stop quoting its turbos for minutes, when no stop can be sold. The
backtests read the same calendar to know each day's news and to repeat
the trader and the monitor. The file is not code: a backtest notes its digest, and it must be
kept ahead (``python -m lab calendar check``). Past its last day nothing is
known, so no window is found there: the trader goes on trading, and says the
calendar needs updating.
"""

import csv
import hashlib
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pytz

CALENDAR_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "macro_calendar.csv")
NEW_YORK = pytz.timezone("America/New_York")
# The news a day is known by, the strongest first.
MACRO_EVENTS = ("FOMC", "CPI", "NFP", "PCE")
# What the macro_news rule may say, and its defaults: from 5 minutes before a release to 15 after,
# until 45 after an FOMC statement, whose press conference starts 30 minutes later.
MAX_MINUTES = 120
MAX_MINUTES_AFTER_FOMC = 180
# From how many days before the calendar's end its upkeep is said.
WARN_DAYS = 30


@dataclass(frozen=True)
class MacroEvent:
    day: date
    time_new_york: str
    event: str
    covers: str

    def moment(self) -> float:
        """When it comes out, in epoch seconds: New York's hour, whatever the clocks of either side."""
        hour, minute = (int(part) for part in self.time_new_york.split(":"))
        return NEW_YORK.localize(datetime(self.day.year, self.day.month, self.day.day, hour, minute)).timestamp()


@dataclass(frozen=True)
class Calendar:
    by_day: dict
    digest: str
    # The last day each kind of news is known to: past it, a day is not known to have none.
    until: dict

    def on(self, day: date) -> list[MacroEvent]:
        return self.by_day.get(day, [])

    def known_until(self) -> date | None:
        return min(self.until.values()) if self.until else None


def read_calendar(path: str = CALENDAR_PATH) -> Calendar:
    """The macro calendar: lines starting with # are comments."""
    with open(path, "rb") as in_file:
        content = in_file.read()
    rows = csv.DictReader(line for line in content.decode("utf-8").splitlines() if line and not line.startswith("#"))
    by_day: dict = {}
    until: dict = {}
    for row in rows:
        event = MacroEvent(date.fromisoformat(row["date"]), row["time_new_york"], row["event"], row["covers"])
        by_day.setdefault(event.day, []).append(event)
        until[event.event] = max(until.get(event.event, event.day), event.day)
    return Calendar(by_day, hashlib.sha256(content).hexdigest()[:16], until)


@dataclass(frozen=True)
class NewsWindow:
    """The moments around a release when no entry is made: from ``start``, until ``end``."""

    event: MacroEvent
    start: float
    end: float


@dataclass(frozen=True)
class NewsRule:
    """The ``macro_news`` rule of ``trade.rules``: which releases, and how long before and after each."""

    events: tuple[str, ...] = MACRO_EVENTS
    minutes_before: int = 5
    minutes_after: int = 15
    minutes_after_fomc: int = 45
    # From how long before a release the monitor sells what is held; 0 keeps open positions, as before.
    flatten_minutes_before: int = 0

    @classmethod
    def from_config(cls, rule_config) -> "NewsRule":
        """The rule as its ``rule_config`` says it; ValueError, saying what is wrong, otherwise."""
        if not isinstance(rule_config, dict):
            raise ValueError("macro_news: rule_config must be an object")
        unknown = set(rule_config) - {"events", "minutes_before", "minutes_after", "minutes_after_fomc",
                                      "flatten_minutes_before"}
        if unknown:
            raise ValueError(f"macro_news: unknown field(s) {', '.join(sorted(unknown))}")
        events = rule_config.get("events", list(MACRO_EVENTS))
        if not isinstance(events, list) or not events or any(event not in MACRO_EVENTS for event in events):
            raise ValueError(f"macro_news: events is a list of some of {', '.join(MACRO_EVENTS)}")
        values = {}
        for name, highest in (("minutes_before", MAX_MINUTES), ("minutes_after", MAX_MINUTES),
                              ("minutes_after_fomc", MAX_MINUTES_AFTER_FOMC), ("flatten_minutes_before", MAX_MINUTES)):
            # Absent or null: the default (the admin's form saves an emptied field as null).
            value = rule_config.get(name)
            value = getattr(cls, name) if value is None else value
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= highest:
                raise ValueError(f"macro_news: {name} is a whole number of minutes from 0 to {highest}")
            values[name] = value
        return cls(events=tuple(event for event in MACRO_EVENTS if event in events), **values)

    def window(self, event: MacroEvent) -> NewsWindow:
        """No entry from the earlier of the entries' and the sales' starts: a position bought then would be sold at once."""
        released = event.moment()
        after = self.minutes_after_fomc if event.event == "FOMC" else self.minutes_after
        before = max(self.minutes_before, self.flatten_minutes_before)
        return NewsWindow(event, released - before * 60, released + after * 60)


def windows_near(calendar: Calendar, rule: NewsRule, moment: float) -> list[NewsWindow]:
    """The windows of the releases the rule names, of the New York day of ``moment`` and the days either side."""
    day = datetime.fromtimestamp(moment, NEW_YORK).date()
    return [
        rule.window(event)
        for offset in (-1, 0, 1)
        for event in calendar.on(day + timedelta(days=offset))
        if event.event in rule.events
    ]


def window_at(calendar: Calendar, rule: NewsRule, moment: float) -> NewsWindow | None:
    """The window ``moment`` is in, if any: from its start included to its end excluded."""
    return next((window for window in windows_near(calendar, rule, moment) if window.start <= moment < window.end), None)


def flat_window_at(calendar: Calendar, rule: NewsRule, moment: float) -> NewsWindow | None:
    """
    The window of the release whose position is to be sold at ``moment``: from
    ``flatten_minutes_before`` the release until the window's end. None when the
    rule keeps open positions.
    """
    if rule.flatten_minutes_before <= 0:
        return None
    return next(
        (
            window for window in windows_near(calendar, rule, moment)
            if window.event.moment() - rule.flatten_minutes_before * 60 <= moment < window.end
        ),
        None,
    )


def flat_reason(window: NewsWindow, timezone: str) -> str:
    """Why the monitor sold a position before a release, in the trading timezone; the backtests say the same."""
    released = datetime.fromtimestamp(window.event.moment(), pytz.timezone(timezone)).strftime("%H:%M")
    return f"Flat before {window.event.event} ({released})"


def next_window(calendar: Calendar, rule: NewsRule, moment: float) -> NewsWindow | None:
    """The window ``moment`` is in, or the next one to start within a day."""
    found = [window for window in windows_near(calendar, rule, moment) if window.end > moment]
    return min(found, key=lambda window: window.start) if found else None


def refusal_text(window: NewsWindow, timezone: str) -> str:
    """What the trader says of an entry refused in ``window``, in the trading timezone; the backtests say the same."""
    zone = pytz.timezone(timezone)
    at = lambda moment: datetime.fromtimestamp(moment, zone).strftime("%H:%M")  # noqa: E731
    return (
        f"Breaking trading rule: no entry around {window.event.event} ({at(window.event.moment())}) "
        f"from {at(window.start)} to {at(window.end)}."
    )


def upkeep(calendar: Calendar, today: date) -> str | None:
    """What to say of the calendar from WARN_DAYS before its last known day; None before."""
    until = calendar.known_until()
    if until is None:
        return "The calendar of US releases is empty: no window is kept around them"
    left = (until - today).days
    if left > WARN_DAYS:
        return None
    if left < 0:
        return (f"The calendar of US releases ended on {until.isoformat()}: no window is kept around them since. "
                "Update it (python -m lab calendar check), then deploy")
    return (f"The calendar of US releases ends on {until.isoformat()}, in {left} day(s): "
            "update it (python -m lab calendar check), then deploy")
