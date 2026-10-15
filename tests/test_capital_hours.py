from datetime import datetime, timezone

import pytest

from src.capital_feed.hours import parse_opening_hours

# As Capital.com gives them in an instrument's details (its documentation's example).
WEEK = {
    "mon": ["00:00 - 21:59", "23:05 - 00:00"],
    "tue": ["00:00 - 21:59", "23:05 - 00:00"],
    "wed": ["00:00 - 21:59", "23:05 - 00:00"],
    "thu": ["00:00 - 21:59", "23:05 - 00:00"],
    "fri": ["00:00 - 21:59"],
    "sat": [],
    "sun": ["23:05 - 00:00"],
    "zone": "UTC",
}


def _at(text):
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


@pytest.mark.parametrize("moment, is_open", [
    # Thursday 15 Oct 2026.
    ("2026-10-15T08:00:00", True),
    # The last minute named is open...
    ("2026-10-15T21:59:30", True),
    # ... and the break follows it.
    ("2026-10-15T22:00:00", False),
    ("2026-10-15T23:04:59", False),
    ("2026-10-15T23:05:00", True),
    # Through midnight.
    ("2026-10-15T23:59:59", True),
    ("2026-10-16T00:00:00", True),
    # Friday evening, the weekend, Sunday night.
    ("2026-10-16T22:30:00", False),
    ("2026-10-17T12:00:00", False),
    ("2026-10-18T23:10:00", True),
])
def test_the_market_is_open_in_the_stretches_given(moment, is_open):
    assert parse_opening_hours(WEEK).is_open(_at(moment)) is is_open


def test_the_next_opening_is_the_end_of_a_break_and_never_midnight_inside_a_stretch():
    hours = parse_opening_hours(WEEK)

    assert hours.next_opening(_at("2026-10-15T22:30:00")) == _at("2026-10-15T23:05:00")
    # Friday night: Sunday evening, not the 00:00 that continues Sunday's stretch.
    assert hours.next_opening(_at("2026-10-16T22:30:00")) == _at("2026-10-18T23:05:00")


def test_hours_of_another_zone_are_read_in_it():
    hours = parse_opening_hours({**{day: ["09:00 - 17:29"] for day in ("mon", "tue", "wed", "thu", "fri")},
                                 "sat": [], "sun": [], "zone": "Europe/London"})

    # 08:30 UTC is 09:30 in London in October (summer time).
    assert hours.is_open(_at("2026-10-15T08:30:00")) is True
    assert hours.is_open(_at("2026-10-15T16:45:00")) is False


@pytest.mark.parametrize("value", [None, "always", {"mon": "00:00 - 21:59", "zone": "UTC"},
                                   {"mon": ["25:00 - 26:00"], "zone": "UTC"}, {"mon": ["10:00 - 09:00"], "zone": "UTC"},
                                   {"mon": ["10:00 - 11:00"], "zone": "Mars/Olympus"}])
def test_hours_in_another_form_are_not_guessed(value):
    assert parse_opening_hours(value) is None
