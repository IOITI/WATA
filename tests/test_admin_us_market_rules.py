"""NYSE's rules for the years the US calendar file does not hold yet, held to every year it does."""

from datetime import date

import pytest

from src.admin.us_market_rules import calendar_day, days_to_come, easter, nyse_days
from src.trade.market_calendar import CLOSED, EARLY_CLOSE, MarketCalendar, read_market_calendar

CALENDAR = read_market_calendar()


def shape(days: dict) -> dict:
    return {day: (found.kind, found.close_new_york) for day, found in days.items()}


@pytest.mark.parametrize("year", sorted({day.year for day in CALENDAR.by_day}))
def test_the_rules_give_every_day_of_the_file_for_each_year_it_holds(year):
    in_file = {day: found for day, found in CALENDAR.by_day.items() if day.year == year}

    assert shape(nyse_days(year)) == shape(in_file)


def test_the_rules_name_the_days_as_the_file_does():
    in_file = {day: found.name for day, found in CALENDAR.by_day.items()}
    by_rules = {day: found.name for year in (2026, 2027, 2028) for day, found in nyse_days(year).items()}

    assert {day: (in_file[day], by_rules[day]) for day in in_file if in_file[day] != by_rules[day]} == {}


def test_easter_falls_where_the_churches_put_it():
    assert [easter(year) for year in (2024, 2025, 2026, 2027, 2028, 2029, 2038)] == [
        date(2024, 3, 31), date(2025, 4, 20), date(2026, 4, 5), date(2027, 3, 28), date(2028, 4, 16),
        date(2029, 4, 1), date(2038, 4, 25),
    ]


def test_a_holiday_on_a_weekend_is_kept_on_the_nearest_weekday_but_new_years_day_on_a_saturday():
    days_2028 = nyse_days(2028)
    days_2033 = nyse_days(2033)  # Christmas on a Sunday, 1 January on a Saturday

    assert date(2027, 12, 31) not in nyse_days(2027) and date(2028, 1, 1) not in days_2028
    assert days_2033[date(2033, 12, 26)].name == "Christmas Day (observed)"
    assert date(2033, 12, 23) not in days_2033
    assert nyse_days(2032)[date(2032, 7, 5)].name == "Independence Day (observed)"  # 4 July on a Sunday
    assert nyse_days(2021).get(date(2021, 6, 18)) is None  # Juneteenth only from 2022


def test_the_days_before_independence_day_and_christmas_close_early_only_monday_to_thursday():
    early_2029 = {day for day, found in nyse_days(2029).items() if found.kind == EARLY_CLOSE}
    early_2027 = {day for day, found in nyse_days(2027).items() if found.kind == EARLY_CLOSE}

    assert early_2029 == {date(2029, 7, 3), date(2029, 11, 23), date(2029, 12, 24)}
    assert early_2027 == {date(2027, 11, 26)}  # 3 July a Saturday, 24 December the observed Christmas
    assert all(found.close_new_york == "13:00" for found in nyse_days(2029).values() if found.kind == EARLY_CLOSE)


def test_a_day_of_a_year_the_file_holds_comes_from_it_and_one_of_another_year_from_the_rules():
    assert calendar_day(date(2026, 11, 26), CALENDAR) == (CALENDAR.by_day[date(2026, 11, 26)], False)
    assert calendar_day(date(2026, 11, 25), CALENDAR) == (None, False)
    found, estimated = calendar_day(date(2025, 11, 27), CALENDAR)
    assert (found.kind, found.name, estimated) == (CLOSED, "Thanksgiving Day", True)
    assert calendar_day(date(2025, 11, 26), CALENDAR) == (None, True)
    found, estimated = calendar_day(date(2026, 11, 26), None)  # the file unreadable: the rules everywhere
    assert (found.kind, estimated) == (CLOSED, True)


def test_the_days_to_come_skip_weekends_holidays_and_closed_dates_and_say_the_early_closes():
    days, skipped = days_to_come(date(2026, 11, 23), date(2026, 12, 31), CALENDAR, {date(2026, 12, 24)})
    listed = {day["day"] for day in days}

    assert all(date.fromisoformat(day).weekday() < 5 for day in listed)
    assert "2026-11-26" not in listed and "2026-12-25" not in listed
    assert {day["day"]: day["kind"] for day in days if day["kind"] != "full"} == {"2026-11-27": "early_close"}
    assert [(day["day"], day["why"], day["estimated"]) for day in skipped] == [
        ("2026-11-26", "us_closed", False), ("2026-12-24", "config_closed", False),
        ("2026-12-25", "us_closed", False),
    ]
    assert len(days) == 29 - 3  # 29 weekdays, Christmas Eve's early close closed by the configuration instead
    assert not any(day["estimated"] for day in days)


def test_past_the_file_the_days_are_estimated_by_the_rules():
    days, skipped = days_to_come(date(2028, 12, 20), date(2029, 1, 5), CALENDAR)

    assert [day["estimated"] for day in days if day["day"] <= "2028-12-29"] == [False] * 7
    assert all(day["estimated"] for day in days if day["day"] >= "2029-01-01")
    assert [(day["day"], day["name"], day["estimated"]) for day in skipped] == [
        ("2028-12-25", "Christmas Day", False), ("2029-01-01", "New Year's Day", True),
    ]


def test_an_empty_file_leaves_every_year_to_the_rules():
    empty = MarketCalendar({}, "x", None)

    days, skipped = days_to_come(date(2026, 11, 26), date(2026, 11, 27), empty)

    assert skipped == [{"day": "2026-11-26", "why": "us_closed", "name": "Thanksgiving Day", "estimated": True}]
    assert days == [{"day": "2026-11-27", "kind": "early_close", "close_new_york": "13:00",
                     "name": "The day after Thanksgiving", "estimated": True}]
