"""
The US market's holidays and early closes (src/trade/market_calendar.py,
src/trade/data/us_market_calendar.csv): read from New York's hours, kept by
the trader, said by the scheduler and health before they run out.
"""

from datetime import date, datetime

import pytest
import pytz

from src.admin.health import OK, WARNING, market_calendar_entry
from src.trade.market_calendar import (
    CLOSED,
    EARLY_CLOSE,
    entries_end_moment,
    minutes_before_end,
    read_market_calendar,
    sale_moment,
    upkeep,
)

CALENDAR = read_market_calendar()
PARIS = pytz.timezone("Europe/Paris")


def test_the_file_holds_the_days_nyse_announced_for_2026_and_2027():
    closed = {day for day, found in CALENDAR.by_day.items() if found.kind == CLOSED}
    early = {day: found.close_new_york for day, found in CALENDAR.by_day.items() if found.kind == EARLY_CLOSE}

    assert len(closed) == 20
    assert date(2026, 9, 7) in closed  # Labor Day, traded by WATA in 2026
    assert date(2026, 11, 26) in closed and date(2027, 12, 24) in closed
    assert early == {date(2026, 11, 27): "13:00", date(2026, 12, 24): "13:00", date(2027, 11, 26): "13:00"}
    assert all(day.weekday() < 5 for day in CALENDAR.by_day)
    assert CALENDAR.until == date(2027, 12, 31)


def test_an_early_close_is_new_yorks_one_pm_whichever_side_changed_its_clocks():
    early = CALENDAR.early_close_on(date(2026, 11, 27))

    assert datetime.fromtimestamp(early.close_moment(), PARIS).strftime("%H:%M") == "19:00"
    assert CALENDAR.closed_on(date(2026, 11, 27)) is None
    assert CALENDAR.early_close_on(date(2026, 11, 26)) is None


def test_on_an_early_close_entries_and_sales_keep_the_minutes_of_a_normal_end():
    early = CALENDAR.early_close_on(date(2026, 11, 27))

    # Risky from 21:54 and sold at 21:55 before a 22h end: 6 and 5 minutes before 19:00 in Paris.
    assert minutes_before_end(22, (21, 54)) == 6
    assert datetime.fromtimestamp(entries_end_moment(early, 22, (21, 54)), PARIS).strftime("%H:%M") == "18:54"
    sold = sale_moment(date(2026, 11, 27), "21:55", "Europe/Paris", CALENDAR, 22)
    assert datetime.fromtimestamp(sold, PARIS).strftime("%H:%M") == "18:55"
    ordinary = sale_moment(date(2026, 11, 25), "21:55", "Europe/Paris", CALENDAR, 22)
    assert datetime.fromtimestamp(ordinary, PARIS).strftime("%H:%M") == "21:55"


def test_its_upkeep_is_said_from_thirty_days_before_it_ends():
    assert upkeep(CALENDAR, date(2027, 11, 30)) is None
    assert upkeep(CALENDAR, date(2027, 12, 1)) == (
        "The calendar of US market days ends on 2027-12-31, in 30 day(s): add the next year to "
        "src/trade/data/us_market_calendar.csv, then deploy"
    )
    assert upkeep(CALENDAR, date(2028, 1, 3)).startswith("The calendar of US market days ended on 2027-12-31")


@pytest.mark.parametrize("line, refusal", [
    ("2026-11-27,early,13:00,x,src", "neither closed nor early_close"),
    ("2026-11-27,early_close,,x,src", "an early close has a time"),
    ("2026-11-26,closed,13:00,x,src", "an early close has a time"),
])
def test_a_row_the_trader_could_not_read_is_refused(tmp_path, line, refusal):
    path = tmp_path / "calendar.csv"
    path.write_text("date,kind,close_new_york,name,source\n" + line + "\n")

    with pytest.raises(ValueError, match=refusal):
        read_market_calendar(str(path))


def test_the_health_says_the_calendar_and_when_it_runs_out():
    assert market_calendar_entry(CALENDAR, date(2026, 10, 24)) == [
        {"id": "market_calendar", "name": "US market days calendar", "status": OK, "detail": "Known until 2027-12-31"},
    ]
    assert market_calendar_entry(CALENDAR, date(2027, 12, 20))[0]["status"] == WARNING
    assert market_calendar_entry(None, date(2026, 10, 24))[0]["status"] == WARNING
