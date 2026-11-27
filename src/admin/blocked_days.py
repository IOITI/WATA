"""
Every day the trader takes no entry, or takes fewer, as the Market calendar page lists them
(``GET /api/market-calendar``, and the dashboard's next day off): the US market's holidays and early closes
(src/trade/data/us_market_calendar.csv), the dates of config.json's ``market_closed_dates``, and, while the
``macro_news`` rule is in ``trade.rules``, the window around each US release it names
(src/trade/data/macro_calendar.csv). The trader decides with the same files and rules (``check_market_hours``,
``check_macro_news`` of src/trade/rules.py): this module only lists what they say, day by day, and changes nothing.

A day's blocks are each reason it is blocked; its ``effect`` the strongest of them: ``closed`` (no entry all day),
``early_close`` (entries end early, what is held is sold before the US close), ``window`` (no entry for minutes
around a release). Weekends are not listed: the trader's hours never open on them.

An early close's moments are those the dashboard's overview says on that day (``early_close_times``, which
src/admin/routers/trading.py's ``_market_day`` calls): the two must never differ. A kind of block added here is
added to the page's model too (admin_ui/src/features/market/marketCalendarModel.ts).
"""

from datetime import date, datetime

import pytz

from src.admin.live_safety import config_link
from src.trade import macro_calendar, market_calendar
from src.trade.market_calendar import entries_end_moment, sale_moment

CLOSED, EARLY_CLOSE, WINDOW = "closed", "early_close", "window"
# The strongest first: a day closed all day is closed whatever else it holds.
EFFECTS = (CLOSED, EARLY_CLOSE, WINDOW)
US_CLOSED, US_EARLY_CLOSE, CONFIG_CLOSED, RELEASE = "us_closed", "us_early_close", "config_closed", "release"
EFFECT_OF = {US_CLOSED: CLOSED, CONFIG_CLOSED: CLOSED, US_EARLY_CLOSE: EARLY_CLOSE, RELEASE: WINDOW}
# Where each is set on the Configuration page: the blocks' headings, shown even while a rule is not in config.json.
CLOSED_DATES = "trade.rules[market_closed_dates].rule_config.market_closed_dates"
RELEASES = "trade.rules[macro_news].rule_config.minutes_before"


def _rule(rules: list, rule_type: str) -> dict | None:
    return next((rule for rule in rules or [] if isinstance(rule, dict) and rule.get("rule_type") == rule_type), None)


def _rule_config(rules: list, rule_type: str) -> dict:
    rule = _rule(rules, rule_type)
    config = rule.get("rule_config") if rule else None
    return config if isinstance(config, dict) else {}


def _iso(moment: float) -> str:
    return datetime.fromtimestamp(moment, pytz.utc).isoformat()


def market_zone(rules: list, trading_timezone: str) -> str:
    """The timezone the trader reads its hours and its closed dates in: ``market_hours.timezone``, else the trading one."""
    return _rule_config(rules, "market_hours").get("timezone") or trading_timezone


def early_close_times(early, calendar, market_hours: dict, day_trading: dict, zone: str) -> dict:
    """
    A day the US market closes early, as the trader and the monitor read it: when it closes, when entries end (as
    long before the close as the risky minutes before a normal end) and when what is held is sold (as long before
    it as ``close_position_time`` before a normal end), each when the settings say it.
    """
    found = {"closes_at": _iso(early.close_moment())}
    end_hour = market_hours.get("trading_end_hour")
    risky = (market_hours.get("risky_trading_start_hour"), market_hours.get("risky_trading_start_minute"))
    if isinstance(end_hour, int) and None not in risky:
        found["entries_end_at"] = _iso(entries_end_moment(early, end_hour, risky))
        if day_trading.get("close_position_time"):
            found["sale_at"] = _iso(sale_moment(early.day, day_trading["close_position_time"], zone, calendar, end_hour))
    return found


def _config_date(text) -> date | None:
    try:
        return datetime.strptime(text, "%d/%m/%Y").date()
    except (TypeError, ValueError):
        return None


def _us_days(rules: list, zone: str, today: date, add) -> tuple[dict, object]:
    try:
        calendar = market_calendar.read_market_calendar()
    except (OSError, ValueError, KeyError) as error:
        return {"readable": False, "until": None, "upkeep": None, "error": str(error)}, None
    market_hours = _rule_config(rules, "market_hours")
    day_trading = _rule_config(rules, "day_trading")
    for day, found in calendar.by_day.items():
        if found.kind == market_calendar.CLOSED:
            add(day, {"kind": US_CLOSED, "name": found.name})
        else:
            add(day, {"kind": US_EARLY_CLOSE, "name": found.name, "close_new_york": found.close_new_york,
                      **early_close_times(found, calendar, market_hours, day_trading, zone)})
    said = {
        "readable": True,
        "until": calendar.until.isoformat() if calendar.until else None,
        "upkeep": market_calendar.upkeep(calendar, today),
    }
    return said, calendar


def _config_days(rules: list, calendar, add) -> dict:
    written = _rule_config(rules, "market_closed_dates").get("market_closed_dates") or []
    unreadable = []
    for text in written if isinstance(written, list) else []:
        day = _config_date(text)
        if day is None:
            unreadable.append(str(text))
            continue
        # The catalog's caveat: list there only what the US calendar lacks.
        also_us = calendar is not None and calendar.closed_on(day) is not None
        add(day, {"kind": CONFIG_CLOSED, "written": text, "also_us": also_us})
    return {"count": len(written) if isinstance(written, list) else 0, "unreadable": unreadable}


def _release_days(rules: list, today: date, add) -> dict:
    rule = _rule(rules, "macro_news")
    if rule is None:
        return {"on": False, "readable": True, "events": [], "known_from": None, "known_until": None, "upkeep": None}
    try:
        news_rule = macro_calendar.NewsRule.from_config(rule.get("rule_config"))
        calendar = macro_calendar.read_calendar()
    except (OSError, ValueError, KeyError) as error:
        return {"on": True, "readable": False, "events": [], "known_from": None, "known_until": None, "upkeep": None,
                "error": str(error)}
    for day, events in calendar.by_day.items():
        for event in events:
            if event.event not in news_rule.events:
                continue
            window = news_rule.window(event)
            block = {
                "kind": RELEASE, "event": event.event, "covers": event.covers, "time_new_york": event.time_new_york,
                "released_at": _iso(event.moment()), "start": _iso(window.start), "end": _iso(window.end),
            }
            if news_rule.flatten_minutes_before > 0:
                # From then the position monitor sells what is held, until the window's end.
                block["flat_from"] = _iso(event.moment() - news_rule.flatten_minutes_before * 60)
            add(day, block)
    known_from, known_until = calendar.known_from(news_rule.events), calendar.known_until(news_rule.events)
    return {
        "on": True, "readable": True, "events": list(news_rule.events),
        "known_from": known_from.isoformat() if known_from else None,
        "known_until": known_until.isoformat() if known_until else None,
        "upkeep": macro_calendar.upkeep(calendar, today, news_rule.events),
    }


def _ordered(blocks: list) -> list:
    """The strongest first, a day's releases in the order they come."""
    return sorted(blocks, key=lambda block: (EFFECTS.index(EFFECT_OF[block["kind"]]), block.get("start", "")))


def blocked_days(rules: list, trading_timezone: str, now: datetime) -> dict:
    """Every blocked day known, oldest first, with where each kind comes from; never raises for a file it cannot read."""
    zone = market_zone(rules, trading_timezone)
    today = now.astimezone(pytz.timezone(zone)).date()
    by_day: dict = {}

    def add(day: date, block: dict) -> None:
        by_day.setdefault(day, []).append(block)

    us, calendar = _us_days(rules, zone, today, add)
    config = _config_days(rules, calendar, add)
    news = _release_days(rules, today, add)
    days = []
    for day in sorted(by_day):
        blocks = _ordered(by_day[day])
        days.append({
            "day": day.isoformat(),
            "weekday": day.isoweekday(),
            "effect": EFFECT_OF[blocks[0]["kind"]],
            "blocks": blocks,
        })
    return {
        "today": today.isoformat(),
        "timezone": trading_timezone,
        "market_timezone": zone,
        "us": us,
        "closed_dates": {**config, "link": config_link(CLOSED_DATES, whole_block=True)},
        "news": {**news, "link": config_link(RELEASES, whole_block=True)},
        "days": days,
    }
