"""
Every day the trader takes no entry, or takes fewer, as the Market calendar page lists them
(``GET /api/market-calendar``, and the dashboard's next day off): the US market's holidays and early closes
(src/trade/data/us_market_calendar.csv), the dates of config.json's ``market_closed_dates``, and the US releases
(src/trade/data/macro_calendar.csv): while the ``macro_news`` rule is in ``trade.rules``, the window around each it
names. The trader decides with the same files and rules (``check_market_hours``, ``check_macro_news`` of
src/trade/rules.py): this module only lists what they say, day by day, and changes nothing.

Every release of the calendar is listed, applied or not (``applies``; ``not_applied`` says why: the rule is off, does
not name it, or cannot be read), with the window the rule would keep, its defaults while it is off: the page shows
those not applied only when asked, as details that block nothing.

A day's blocks are each reason it is blocked; its ``effect`` the strongest of them: ``closed`` (no entry all day),
``early_close`` (entries end early, what is held is sold before the US close), ``window`` (no entry for minutes
around a release), ``none`` (releases not applied alone). Weekends are not listed: the trader's hours never open on
them.

An early close's moments are those the dashboard's overview says on that day (``early_close_times``, which
src/admin/routers/trading.py's ``_market_day`` calls): the two must never differ. A kind of block added here is
added to the page's model too (admin_ui/src/features/market/marketCalendarModel.ts).
"""

from datetime import date, datetime

import pytz

from src.admin.live_safety import config_link
from src.trade import macro_calendar, market_calendar
from src.trade.market_calendar import entries_end_moment, sale_moment

CLOSED, EARLY_CLOSE, WINDOW, NONE = "closed", "early_close", "window", "none"
# The strongest first: a day closed all day is closed whatever else it holds; a release not applied blocks nothing.
EFFECTS = (CLOSED, EARLY_CLOSE, WINDOW, NONE)
US_CLOSED, US_EARLY_CLOSE, CONFIG_CLOSED, RELEASE = "us_closed", "us_early_close", "config_closed", "release"
EFFECT_OF = {US_CLOSED: CLOSED, CONFIG_CLOSED: CLOSED, US_EARLY_CLOSE: EARLY_CLOSE}
# Why a release is not applied: the rule is not in config.json, it does not name the release, it cannot be read.
RULE_OFF, NOT_NAMED, RULE_ERROR = "rule_off", "not_named", "rule_error"
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


def _written_closed_dates(rules: list) -> list:
    written = _rule_config(rules, "market_closed_dates").get("market_closed_dates") or []
    return written if isinstance(written, list) else []


def config_closed_dates(rules: list) -> tuple[set[date], list[str]]:
    """The configuration's ``market_closed_dates`` (DD/MM/YYYY, as the trader reads them), and those it cannot read."""
    days, unreadable = set(), []
    for text in _written_closed_dates(rules):
        day = _config_date(text)
        if day is None:
            unreadable.append(str(text))
        else:
            days.add(day)
    return days, unreadable


def _config_days(rules: list, calendar, add) -> dict:
    written = _written_closed_dates(rules)
    for text in written:
        day = _config_date(text)
        if day is None:
            continue
        # The catalog's caveat: list there only what the US calendar lacks.
        also_us = calendar is not None and calendar.closed_on(day) is not None
        add(day, {"kind": CONFIG_CLOSED, "written": text, "also_us": also_us})
    return {"count": len(written), "unreadable": config_closed_dates(rules)[1]}


def _release_days(rules: list, today: date, add) -> dict:
    rule = _rule(rules, "macro_news")
    applied_by, rule_error = None, None
    if rule is not None:
        try:
            applied_by = macro_calendar.NewsRule.from_config(rule.get("rule_config"))
        except ValueError as error:
            rule_error = str(error)
    said = {"on": rule is not None, "rule_error": rule_error, "events": list(applied_by.events) if applied_by else [],
            "default_events": list(macro_calendar.DEFAULT_EVENTS)}
    try:
        calendar = macro_calendar.read_calendar()
    except (OSError, ValueError, KeyError) as error:
        return {**said, "readable": False, "all_events": [], "known_from": None, "known_until": None, "upkeep": None,
                "error": str(error)}
    # The window a release has, or would have once applied: the rule's, else its defaults (the catalog's rule_default).
    shown = applied_by or macro_calendar.NewsRule()
    not_applied = NOT_NAMED if applied_by else (RULE_ERROR if rule_error else RULE_OFF)
    for day, events in calendar.by_day.items():
        for event in events:
            window = shown.window(event)
            applies = applied_by is not None and event.event in applied_by.events
            block = {
                "kind": RELEASE, "event": event.event, "covers": event.covers, "time_new_york": event.time_new_york,
                "released_at": _iso(event.moment()), "start": _iso(window.start), "end": _iso(window.end),
                "applies": applies,
            }
            if not applies:
                block["not_applied"] = not_applied
            if shown.flatten_minutes_before > 0:
                # From then the position monitor sells (would sell) what is held, until the window's end.
                block["flat_from"] = _iso(event.moment() - shown.flatten_minutes_before * 60)
            add(day, block)
    events = applied_by.events if applied_by else macro_calendar.DEFAULT_EVENTS
    known_from, known_until = calendar.known_from(events), calendar.known_until(events)
    return {
        **said, "readable": True,
        "all_events": [event for event in macro_calendar.MACRO_EVENTS if event in calendar.until],
        "known_from": known_from.isoformat() if known_from else None,
        "known_until": known_until.isoformat() if known_until else None,
        # Said as health says it: only of the releases the rule keeps entries away from.
        "upkeep": macro_calendar.upkeep(calendar, today, applied_by.events) if applied_by else None,
    }


def effect_of(block: dict) -> str:
    """What a block does to its day: a release only while it is applied."""
    if block["kind"] == RELEASE:
        return WINDOW if block["applies"] else NONE
    return EFFECT_OF[block["kind"]]


def _ordered(blocks: list) -> list:
    """The strongest first, a day's releases in the order they come."""
    return sorted(blocks, key=lambda block: (EFFECTS.index(effect_of(block)), block.get("start", "")))


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
            "effect": effect_of(blocks[0]),
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
