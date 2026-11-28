"""
Every day the trader takes no entry, or fewer, as the Market calendar page lists them (src/admin/blocked_days.py):
the US market's holidays and early closes, config.json's closed dates, and the windows around US releases while the
macro_news rule is there. Read from the files and rules the trader reads, an early close's moments as the dashboard's
overview says them.
"""

import json
from datetime import datetime, timezone

import pytest

from src.admin.blocked_days import blocked_days
from src.admin.routers.trading import _market_day
from src.trade import macro_calendar, market_calendar

NOW = datetime(2026, 11, 27, 10, 0, tzinfo=timezone.utc)
MARKET_HOURS = {"trading_start_hour": 8, "trading_end_hour": 22, "risky_trading_start_hour": 21,
                "risky_trading_start_minute": 30}


def rules(*extra, closed_dates=()):
    return [
        {"rule_type": "market_closed_dates", "rule_config": {"market_closed_dates": list(closed_dates)}},
        {"rule_type": "market_hours", "rule_config": dict(MARKET_HOURS)},
        {"rule_type": "day_trading", "rule_config": {"close_position_time": "21:55"}},
        *extra,
    ]


def news(**config):
    return {"rule_type": "macro_news", "rule_config": config}


def by_day(answer):
    return {day["day"]: day for day in answer["days"]}


def test_thanksgiving_is_closed_all_day_and_the_day_after_closes_early():
    days = by_day(blocked_days(rules(), "Europe/Paris", NOW))

    assert days["2026-11-26"] == {"day": "2026-11-26", "weekday": 4, "effect": "closed",
                                  "blocks": [{"kind": "us_closed", "name": "Thanksgiving Day"}]}
    day_after = days["2026-11-27"]
    assert day_after["effect"] == "early_close"
    assert day_after["blocks"] == [{
        "kind": "us_early_close", "name": "The day after Thanksgiving", "close_new_york": "13:00",
        # 13:00 in New York; entries end as long before it as 21:30 before 22:00, the sale as 21:55 before 22:00.
        "closes_at": "2026-11-27T18:00:00+00:00", "entries_end_at": "2026-11-27T17:30:00+00:00",
        "sale_at": "2026-11-27T17:55:00+00:00",
    }]


def test_an_early_close_says_the_moments_the_dashboard_says_that_day():
    class Config:
        def get_config_value(self, path, default=None):
            return rules() if path == "trade.rules" else default

    on_the_day = _market_day(Config(), "Europe/Paris", datetime(2026, 11, 27, 9, 0, tzinfo=timezone.utc))
    listed = by_day(blocked_days(rules(), "Europe/Paris", NOW))["2026-11-27"]["blocks"][0]

    assert on_the_day["kind"] == "early_close"
    assert {key: listed[key] for key in ("closes_at", "entries_end_at", "sale_at")} == {
        key: on_the_day[key] for key in ("closes_at", "entries_end_at", "sale_at")}


def test_every_us_day_of_the_file_is_listed_and_no_weekend():
    answer = blocked_days(rules(), "Europe/Paris", NOW)
    listed = [day["day"] for day in answer["days"]]
    blocking = {day["day"] for day in answer["days"] if day["effect"] in ("closed", "early_close")}

    assert blocking == {day.isoformat() for day in market_calendar.read_market_calendar().by_day}
    assert all(day["weekday"] <= 5 for day in answer["days"])
    assert listed == sorted(set(listed))
    assert answer["us"] == {"readable": True, "until": "2027-12-31", "upkeep": None}
    assert answer["today"] == "2026-11-27"


def test_a_closed_date_of_the_configuration_is_listed_and_said_when_the_us_calendar_has_it_already():
    answer = blocked_days(rules(closed_dates=["26/11/2026", "02/12/2026", "31/02/2026"]), "Europe/Paris", NOW)
    days = by_day(answer)

    assert days["2026-11-26"]["blocks"] == [
        {"kind": "us_closed", "name": "Thanksgiving Day"},
        {"kind": "config_closed", "written": "26/11/2026", "also_us": True},
    ]
    assert days["2026-12-02"] == {"day": "2026-12-02", "weekday": 3, "effect": "closed",
                                  "blocks": [{"kind": "config_closed", "written": "02/12/2026", "also_us": False}]}
    # A date the validator lets through but no calendar has is said, never guessed.
    assert answer["closed_dates"]["unreadable"] == ["31/02/2026"]
    assert answer["closed_dates"]["count"] == 3
    assert answer["closed_dates"]["link"] == {"config": {
        "group": "hours", "path": "trade.rules[market_closed_dates].rule_config.market_closed_dates", "block": "closed"}}


def test_a_closed_date_on_an_early_close_closes_the_day():
    day = by_day(blocked_days(rules(closed_dates=["24/12/2026"]), "Europe/Paris", NOW))["2026-12-24"]

    assert day["effect"] == "closed"
    assert [block["kind"] for block in day["blocks"]] == ["config_closed", "us_early_close"]


def test_without_the_macro_news_rule_its_releases_are_listed_but_block_nothing():
    answer = blocked_days(rules(), "Europe/Paris", NOW)
    days = by_day(answer)

    assert answer["news"]["on"] is False and answer["news"]["events"] == []
    assert all(day["effect"] != "window" for day in answer["days"])
    # With the rule's default minutes, as it would be once switched on.
    assert days["2026-12-10"] == {"day": "2026-12-10", "weekday": 4, "effect": "none", "blocks": [{
        "kind": "release", "event": "CPI", "covers": "2026-11", "time_new_york": "08:30",
        "released_at": "2026-12-10T13:30:00+00:00", "start": "2026-12-10T13:25:00+00:00",
        "end": "2026-12-10T13:45:00+00:00", "applies": False, "not_applied": "rule_off",
    }]}
    # Every kind the file holds, the later ones too.
    assert days["2026-12-15"]["blocks"][0]["event"] == "PPI"
    assert answer["news"]["all_events"] == ["FOMC", "CPI", "NFP", "PCE", "PPI", "RETAIL", "GDP", "JOLTS"]
    assert answer["news"]["default_events"] == ["FOMC", "CPI", "NFP", "PCE"]
    assert answer["news"]["known_until"] == macro_calendar.read_calendar().known_until().isoformat()
    assert answer["news"]["upkeep"] is None
    assert answer["news"]["link"]["config"]["block"] == "news"


def test_each_release_the_rule_names_is_a_window_as_the_trader_keeps_it():
    answer = blocked_days(rules(news()), "Europe/Paris", NOW)
    days = by_day(answer)

    cpi = days["2026-12-10"]
    assert cpi["effect"] == "window"
    assert cpi["blocks"] == [{
        "kind": "release", "event": "CPI", "covers": "2026-11", "time_new_york": "08:30",
        # 5 minutes before to 15 after, New York's 08:30.
        "released_at": "2026-12-10T13:30:00+00:00", "start": "2026-12-10T13:25:00+00:00",
        "end": "2026-12-10T13:45:00+00:00", "applies": True,
    }]
    fomc = days["2026-12-09"]["blocks"][0]
    # 45 minutes after an FOMC statement: its press conference follows.
    assert (fomc["event"], fomc["start"], fomc["end"]) == ("FOMC", "2026-12-09T18:55:00+00:00",
                                                           "2026-12-09T19:45:00+00:00")
    # PPI is not among the four a rule that names none keeps entries away from: listed, blocking nothing.
    ppi = days["2026-12-15"]
    assert ppi["effect"] == "none"
    assert (ppi["blocks"][0]["applies"], ppi["blocks"][0]["not_applied"]) == (False, "not_named")
    assert answer["news"]["events"] == ["FOMC", "CPI", "NFP", "PCE"]
    assert answer["news"]["known_until"] == macro_calendar.read_calendar().known_until().isoformat()


def test_the_window_widens_to_the_sale_before_a_release_and_follows_the_events_named():
    days = by_day(blocked_days(rules(news(events=["PPI"], flatten_minutes_before=10)), "Europe/Paris", NOW))

    cpi = days["2026-12-10"]  # CPI not named: listed with the rule's minutes, blocking nothing
    assert cpi["effect"] == "none"
    assert (cpi["blocks"][0]["not_applied"], cpi["blocks"][0]["start"]) == ("not_named", "2026-12-10T13:20:00+00:00")
    ppi = days["2026-12-15"]["blocks"][0]
    assert days["2026-12-15"]["effect"] == "window"
    assert (ppi["event"], ppi["applies"], ppi["flat_from"], ppi["start"], ppi["end"]) == (
        "PPI", True, "2026-12-15T13:20:00+00:00", "2026-12-15T13:20:00+00:00", "2026-12-15T13:45:00+00:00")


def test_a_release_on_a_closed_day_leaves_the_day_closed():
    good_friday = by_day(blocked_days(rules(news()), "Europe/Paris", NOW))["2026-04-03"]

    assert good_friday["effect"] == "closed"
    assert [(block["kind"], block.get("event"), block.get("applies")) for block in good_friday["blocks"]] == [
        ("us_closed", None, None), ("release", "NFP", True)]


def test_today_is_the_market_timezone_s_day():
    late = datetime(2026, 11, 26, 23, 30, tzinfo=timezone.utc)
    new_york = rules()
    new_york[1]["rule_config"]["timezone"] = "America/New_York"

    assert blocked_days(rules(), "Europe/Paris", late)["today"] == "2026-11-27"
    assert blocked_days(new_york, "Europe/Paris", late)["today"] == "2026-11-26"
    assert blocked_days(new_york, "Europe/Paris", late)["market_timezone"] == "America/New_York"


def test_a_calendar_that_cannot_be_read_is_said_and_the_rest_still_listed(monkeypatch):
    def unreadable(*args, **kwargs):
        raise OSError("no such file")

    monkeypatch.setattr(market_calendar, "read_market_calendar", unreadable)
    monkeypatch.setattr(macro_calendar, "read_calendar", unreadable)
    answer = blocked_days(rules(news(), closed_dates=["02/12/2026"]), "Europe/Paris", NOW)

    assert answer["us"]["readable"] is False and answer["news"]["readable"] is False
    assert [day["day"] for day in answer["days"]] == ["2026-12-02"]


@pytest.mark.parametrize("config", [{"minutes_before": 500}, {"events": ["ISM"]}, "not an object"])
def test_a_macro_news_rule_the_trader_would_refuse_is_said(config):
    answer = blocked_days(rules({"rule_type": "macro_news", "rule_config": config}), "Europe/Paris", NOW)
    releases = [block for day in answer["days"] for block in day["blocks"] if block["kind"] == "release"]

    assert answer["news"]["on"] is True and answer["news"]["rule_error"].startswith("macro_news:")
    assert answer["news"]["readable"] is True  # the calendar itself was read
    assert releases and all(block["not_applied"] == "rule_error" for block in releases)
    assert all(day["effect"] != "window" for day in answer["days"])


def test_the_answer_is_plain_json():
    json.dumps(blocked_days(rules(news(flatten_minutes_before=5)), "Europe/Paris", NOW))
