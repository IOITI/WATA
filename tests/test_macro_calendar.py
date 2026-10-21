"""
The calendar of US releases and the window the trader keeps around them
(src/trade/macro_calendar.py, the macro_news rule): read from New York's hours
whichever side changed its clocks, checked by the configuration, refused by the
trader, and said by the admin's health when it runs out.
"""

import json
from datetime import date, datetime
from unittest.mock import MagicMock

import pytest
import pytz

from src.admin.health import OK, WARNING, macro_calendar_entry
from src.configuration import ConfigurationManager
from src.trade import rules as trade_rules
from src.trade.exceptions import TradingRuleViolation
from src.trade.macro_calendar import NewsRule, next_window, read_calendar, refusal_text, upkeep, window_at
from src.trade.rules import TradingRule

PARIS = pytz.timezone("Europe/Paris")
CALENDAR = read_calendar()
RULE = NewsRule()


def paris(*moment) -> float:
    return PARIS.localize(datetime(*moment)).timestamp()


def said(moment: float, rule: NewsRule = RULE) -> str | None:
    window = window_at(CALENDAR, rule, moment)
    return None if window is None else refusal_text(window, "Europe/Paris")


# ── the window ──


def test_a_release_is_kept_out_from_five_minutes_before_to_fifteen_after():
    # CPI of 14 October 2026, 08:30 in New York: 14:30 in Paris.
    assert said(paris(2026, 10, 14, 14, 24, 59)) is None
    assert said(paris(2026, 10, 14, 14, 25)) == "Breaking trading rule: no entry around CPI (14:30) from 14:25 to 14:45."
    assert said(paris(2026, 10, 14, 14, 44, 59)) is not None
    assert said(paris(2026, 10, 14, 14, 45)) is None


def test_new_yorks_hour_holds_whichever_side_changed_its_clocks():
    # 11 March 2026: New York on summer time since the 8th, Paris not before the 29th: CPI at 13:30 in Paris.
    assert said(paris(2026, 3, 11, 13, 30)) == "Breaking trading rule: no entry around CPI (13:30) from 13:25 to 13:45."
    assert said(paris(2026, 3, 11, 14, 30)) is None


def test_an_fomc_statement_is_kept_out_until_its_press_conference_is_over():
    # 28 October 2026, 14:00 in New York: 19:00 in Paris, back on winter time since the 25th.
    assert said(paris(2026, 10, 28, 19, 44)) == "Breaking trading rule: no entry around FOMC (19:00) from 18:55 to 19:45."
    assert said(paris(2026, 10, 28, 19, 45)) is None
    # Only the releases the rule names.
    assert said(paris(2026, 10, 28, 19, 44), NewsRule(events=("CPI",))) is None


def test_the_next_window_of_the_day_is_known_before_it_starts():
    upcoming = next_window(CALENDAR, RULE, paris(2026, 10, 14, 9, 0))

    assert (upcoming.event.event, upcoming.start, upcoming.end) == ("CPI", paris(2026, 10, 14, 14, 25), paris(2026, 10, 14, 14, 45))
    assert next_window(CALENDAR, RULE, paris(2026, 10, 14, 15, 0)) is None


def test_the_rule_says_its_defaults_and_what_it_was_given():
    assert NewsRule.from_config({}) == NewsRule(("FOMC", "CPI", "NFP", "PCE"), 5, 15, 45)
    assert NewsRule.from_config({"events": ["CPI", "FOMC"], "minutes_before": 0, "minutes_after": 30, "minutes_after_fomc": 60}) == \
        NewsRule(("FOMC", "CPI"), 0, 30, 60)


@pytest.mark.parametrize("config, refusal", [
    ({"events": ["CPI", "GDP"]}, "events is a list of some of FOMC, CPI, NFP, PCE"),
    ({"events": []}, "events is a list"),
    ({"minutes_before": -1}, "minutes_before is a whole number of minutes from 0 to 120"),
    ({"minutes_after": 121}, "minutes_after is a whole number"),
    ({"minutes_after_fomc": 181}, "from 0 to 180"),
    ({"minutes_after": True}, "minutes_after is a whole number"),
    ({"minutes_after": 15.5}, "minutes_after is a whole number"),
    ({"before": 5}, "unknown field"),
    ([], "rule_config must be an object"),
])
def test_a_rule_the_trader_could_not_follow_is_refused(config, refusal):
    with pytest.raises(ValueError, match=refusal):
        NewsRule.from_config(config)


def test_its_upkeep_is_said_from_thirty_days_before_the_calendar_ends(tmp_path):
    # PCE is known to 23 December 2026, the others to December 2027.
    assert CALENDAR.known_until() == date(2026, 12, 23)
    assert upkeep(CALENDAR, date(2026, 11, 22)) is None
    assert "ends on 2026-12-23, in 30 day(s)" in upkeep(CALENDAR, date(2026, 11, 23))
    assert "ended on 2026-12-23" in upkeep(CALENDAR, date(2026, 12, 24))
    # Past its last day nothing is known: no window, the trader trades on.
    path = tmp_path / "calendar.csv"
    path.write_text("# one release\ndate,time_new_york,event,covers,source\n2026-10-14,08:30,CPI,2026-09,bls.gov\n")
    alone = read_calendar(str(path))
    assert window_at(alone, RULE, paris(2026, 11, 13, 14, 30)) is None
    assert window_at(alone, RULE, paris(2026, 10, 14, 14, 30)) is not None


# ── the trader ──


def rules_config(news: dict | None) -> MagicMock:
    config = {
        "trade.rules": [
            {"rule_type": "allowed_indices", "rule_config": {"indice_ids": {"us100": 12345}}},
            {"rule_type": "market_closed_dates", "rule_config": {"market_closed_dates": []}},
            {"rule_type": "day_trading", "rule_config": {"dont_enter_trade_if_day_profit_is_more_than": 1.5, "max_day_loss_percent": -2.0}},
            {"rule_type": "signal_validation", "rule_config": {"max_signal_age_seconds": 8}},
            {"rule_type": "market_hours", "rule_config": {"trading_start_hour": 9, "trading_end_hour": 22,
                                                          "risky_trading_start_hour": 21, "risky_trading_start_minute": 30}},
            *([{"rule_type": "macro_news", "rule_name": "No entry around US releases", "rule_config": news}] if news is not None else []),
        ],
        "trade.config.general.timezone": "Europe/Paris",
    }
    manager = MagicMock()
    manager.get_config_value.side_effect = lambda key, default=None: config.get(key, default)
    return manager


def test_the_trader_refuses_an_entry_in_a_window_and_takes_one_outside():
    rule = TradingRule(rules_config({}), MagicMock())

    with pytest.raises(TradingRuleViolation, match=r"no entry around CPI \(14:30\) from 14:25 to 14:45"):
        rule.check_macro_news(now=datetime(2026, 10, 14, 12, 30, tzinfo=pytz.utc))
    rule.check_macro_news(now=datetime(2026, 10, 14, 12, 45, tzinfo=pytz.utc))


def test_without_the_rule_the_trader_keeps_no_window():
    TradingRule(rules_config(None), MagicMock()).check_macro_news(now=datetime(2026, 10, 14, 12, 30, tzinfo=pytz.utc))


def test_a_calendar_that_cannot_be_read_never_stops_the_trader(monkeypatch):
    def unreadable(path=None):
        raise OSError("gone")

    monkeypatch.setattr(trade_rules, "read_calendar", unreadable)
    rule = TradingRule(rules_config({}), MagicMock())

    rule.check_macro_news(now=datetime(2026, 10, 14, 12, 30, tzinfo=pytz.utc))
    assert rule.news_rule is not None and rule.news_calendar is None


def _config_with_news(tmp_path, rule_config) -> str:
    with open("tests/test_config.json") as config_file:
        config = json.load(config_file)
    config["trade"]["rules"].append({"rule_name": "No entry around US releases", "rule_type": "macro_news", "rule_config": rule_config})
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    return str(path)


def test_the_configuration_checks_the_rule_when_it_is_there(tmp_path):
    ConfigurationManager(_config_with_news(tmp_path, {"events": ["CPI"], "minutes_before": 5}))

    with pytest.raises(ValueError, match="Rule No entry around US releases: macro_news: minutes_before"):
        ConfigurationManager(_config_with_news(tmp_path, {"minutes_before": 500}))


# ── the admin ──


def test_the_dashboard_is_told_the_window_in_force_or_the_next():
    from src.admin.routers.trading import _news
    from src.backtest.settings import DictConfig

    on = DictConfig({"trade": {"rules": [{"rule_type": "macro_news", "rule_name": "x", "rule_config": {}}]}})
    morning = _news(on, datetime(2026, 10, 14, 7, 0, tzinfo=pytz.utc))

    assert morning["window"] == {
        "event": "CPI", "released_at": "2026-10-14T12:30:00+00:00",
        "start": "2026-10-14T12:25:00+00:00", "end": "2026-10-14T12:45:00+00:00",
    }
    assert morning["known_until"] == date(2026, 12, 23)
    assert _news(on, datetime(2026, 10, 14, 13, 0, tzinfo=pytz.utc))["window"] is None
    assert _news(DictConfig({"trade": {"rules": []}}), datetime(2026, 10, 14, 7, 0, tzinfo=pytz.utc)) is None



def test_the_health_says_the_calendar_while_the_rule_is_on():
    assert macro_calendar_entry(False, CALENDAR, date(2026, 12, 20)) == []
    [known] = macro_calendar_entry(True, CALENDAR, date(2026, 10, 21))
    [short] = macro_calendar_entry(True, CALENDAR, date(2026, 12, 20))
    [unread] = macro_calendar_entry(True, None, date(2026, 10, 21))

    assert (known["status"], known["detail"]) == (OK, "Known until 2026-12-23")
    assert short["status"] == WARNING and "in 3 day(s)" in short["detail"]
    assert unread["status"] == WARNING and "Cannot be read" in unread["detail"]
