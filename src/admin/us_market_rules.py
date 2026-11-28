"""
The days the US market will close, or close early, by NYSE's own rules, for the years
src/trade/data/us_market_calendar.csv does not hold yet; and every weekday to come, as the trader will take them.

The file is what the trader reads, from NYSE's announcements; these rules only stand in for a year it does not
have, and what they give is said to be estimated. ``tests/test_admin_us_market_rules.py`` holds them to every row
of the file: a rule NYSE changes shows there first, and a holiday NYSE adds is added here and to the file together.

Pure, stdlib but the trader's calendar types; outside src/backtest/ and ``_CODE``: no run turns stale for it.
"""

from datetime import date, timedelta

from src.trade.market_calendar import CLOSED, EARLY_CLOSE, MarketCalendar, MarketDay

# An early close is at one in the afternoon in New York, every one NYSE announced so far.
EARLY_CLOSE_TIME = "13:00"
FULL = "full"
US_CLOSED, CONFIG_CLOSED = "us_closed", "config_closed"


def easter(year: int) -> date:
    """Easter Sunday of ``year`` (the anonymous Gregorian algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    el = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * el) // 451
    month = (h + el - 7 * m + 114) // 31
    day = (h + el - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The ``n``th ``weekday`` (0 Monday) of a month; ``n`` -1 for its last."""
    if n > 0:
        first = date(year, month, 1)
        return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
    last = date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(day: date) -> date:
    """A holiday on a Saturday is kept the Friday before, one on a Sunday the Monday after."""
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def nyse_days(year: int) -> dict[date, MarketDay]:
    """NYSE's closed days and early closes of ``year``, by its rules (Rule 7.2 of its trading rules)."""
    days: dict[date, MarketDay] = {}

    def closed(day: date, name: str, observed: bool = True) -> None:
        kept = _observed(day) if observed else day
        said = name if kept == day else f"{name} (observed)"
        days[kept] = MarketDay(kept, CLOSED, None, said)

    new_year = date(year, 1, 1)
    # A New Year's Day on a Saturday is not kept on the Friday before: that Friday closes the year before's books.
    if new_year.weekday() != 5:
        closed(new_year, "New Year's Day")
    closed(_nth_weekday(year, 1, 0, 3), "Martin Luther King, Jr. Day", observed=False)
    closed(_nth_weekday(year, 2, 0, 3), "Washington's Birthday", observed=False)
    closed(easter(year) - timedelta(days=2), "Good Friday", observed=False)
    closed(_nth_weekday(year, 5, 0, -1), "Memorial Day", observed=False)
    if year >= 2022:  # a market holiday since 2022
        closed(date(year, 6, 19), "Juneteenth National Independence Day")
    closed(date(year, 7, 4), "Independence Day")
    closed(_nth_weekday(year, 9, 0, 1), "Labor Day", observed=False)
    thanksgiving = _nth_weekday(year, 11, 3, 4)
    closed(thanksgiving, "Thanksgiving Day", observed=False)
    closed(date(year, 12, 25), "Christmas Day")

    def early(day: date, name: str) -> None:
        if day.weekday() < 5 and day not in days:
            days[day] = MarketDay(day, EARLY_CLOSE, EARLY_CLOSE_TIME, name)

    early(thanksgiving + timedelta(days=1), "The day after Thanksgiving")
    # The day before Independence Day and Christmas Eve close early when they fall Monday to Thursday: on a Friday
    # the holiday itself falls on a Saturday and is kept that Friday.
    for day, name in ((date(year, 7, 3), "The day before Independence Day"), (date(year, 12, 24), "Christmas Eve")):
        if day.weekday() <= 3:
            early(day, name)
    return dict(sorted(days.items()))


def _known_years(calendar: MarketCalendar | None) -> set[int]:
    return {day.year for day in calendar.by_day} if calendar is not None else set()


def calendar_day(day: date, calendar: MarketCalendar | None, *, known: set[int] | None = None,
                 rules: dict | None = None) -> tuple[MarketDay | None, bool]:
    """
    What the US market does on ``day``: the file's day when it holds that year (None for an ordinary day), else
    the rules' day; and whether it was estimated (the rules').
    """
    years = known if known is not None else _known_years(calendar)
    if day.year in years:
        return calendar.by_day.get(day), False
    by_rules = rules.get(day.year) if rules is not None else None
    if by_rules is None:
        by_rules = nyse_days(day.year)
        if rules is not None:
            rules[day.year] = by_rules
    return by_rules.get(day), True


def days_to_come(start: date, end: date, calendar: MarketCalendar | None,
                 closed_dates: set[date] | frozenset = frozenset()) -> tuple[list[dict], list[dict]]:
    """
    Every weekday from ``start`` to ``end`` (both counted) the trader takes entries on, each ``full`` or
    ``early_close``, and those it takes none on, each with why: the US market closed (the file's, else the rules',
    ``estimated``), or a closed date of the configuration, which wins over an early close as the trader reads it.
    """
    known, rules = _known_years(calendar), {}
    days, skipped = [], []
    day = start
    while day <= end:
        if day.weekday() < 5:
            found, estimated = calendar_day(day, calendar, known=known, rules=rules)
            if day in closed_dates:
                skipped.append({"day": day.isoformat(), "why": CONFIG_CLOSED, "name": "A closed date of WATA's",
                                "estimated": False})
            elif found is not None and found.kind == CLOSED:
                skipped.append({"day": day.isoformat(), "why": US_CLOSED, "name": found.name, "estimated": estimated})
            elif found is not None:
                days.append({"day": day.isoformat(), "kind": EARLY_CLOSE, "close_new_york": found.close_new_york,
                             "name": found.name, "estimated": estimated})
            else:
                days.append({"day": day.isoformat(), "kind": FULL, "estimated": estimated})
        day += timedelta(days=1)
    return days, skipped
