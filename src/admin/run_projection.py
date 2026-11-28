"""
What a backtest run would make at its own pace over the trading days to come, up to two years: what the run page's
"At this pace" draws (``GET /api/backtests/{run_id}/projection``). This module gives the inputs; the page draws the
paths (admin_ui/src/features/backtests/projection.ts), so that the start, a cap on the stake and the horizon change
them at once.

**The pace** is the account's, day by day: each tested day's growth ``A_i / A_(i-1)``, the account ``A`` moved by the
``profit_loss`` of the trades opened that day (after both commissions), from the run's capital. A position held
overnight thus stays whole on the day it was opened, and the sales the run's end books count. It is the money's
percent (``money.return_percent``), not the turbo percent of the run's other figures, which counts each trade as the
whole stake.

**An early close** keeps the morning of a day: the trades decided before its end of entries (13:00 in New York less
the risky minutes before a normal end, as the trader cuts it), their growth one of the early-close draws. A trade
still held past the early sale moment counts as it ended, and is counted.

**What the pace leaves out**: a tested day the US market was closed (turbos are recorded then, and every entry
refused: a 0 % day the days to come would count twice, since they skip the holidays already), a closed date of the
run's own, and once the account guard paused entries or the account was emptied, the days after (nothing resumes in
a run). A day of a year the US calendar file does not hold is read by NYSE's rules (src/admin/us_market_rules.py).

**The days to come** start on the weekday after today in the market's timezone and run two years, without the US
market's closed days (the file's, then the rules', said to be estimated) nor the live configuration's closed dates.

Pure but for reading the US calendar file; outside src/backtest/ and ``_CODE``: no run turns stale for it.
"""

import hashlib
import math
from datetime import date, datetime, timedelta, timezone

import pytz

from src.admin.blocked_days import config_closed_dates, market_zone
from src.admin.us_market_rules import calendar_day, days_to_come
from src.backtest.figures import sample_size
from src.backtest.rules import ACCOUNT_GUARD
from src.backtest.settings import BacktestSettings
from src.trade.market_calendar import CLOSED, EARLY_CLOSE, MarketCalendar, MarketDay, minutes_before_end, \
    read_market_calendar

TESTED = "tested"
# Fewer days than this give no drawing worth showing (the summary's own total range waits for as many).
LEAST_DAYS = 5
YEARS = 2
EARLY_CLOSE_TIME = "13:00"
# Why a tested day is left out of the pace.
US_CLOSED, OWN_CLOSED, AFTER_GUARD, AFTER_EMPTY = "us_closed", "closed_date", "after_guard", "after_empty"


class NotProjectable(ValueError):
    """Why a run has no projection, in words."""


def seed_of(run_id: str) -> int:
    """The paths' seed: the same run draws the same paths. 32 bits, which a browser's numbers hold exactly."""
    return int.from_bytes(hashlib.sha256(f"projection:{run_id}".encode()).digest()[:4], "big")


def _moment(text) -> float | None:
    if not isinstance(text, str):
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def _profit(trade: dict) -> float:
    value = trade.get("profit_loss")
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def _closed_date(text) -> date | None:
    try:
        return datetime.strptime(text, "%d/%m/%Y").date()
    except (TypeError, ValueError):
        return None


def _early_moments(day: date, settings: BacktestSettings) -> tuple[float, float]:
    """On ``day`` closing early at 13:00 New York, when entries end and when what is held is sold, as the trader."""
    early = MarketDay(day, EARLY_CLOSE, EARLY_CLOSE_TIME, "")
    close = early.close_moment()
    entries_end = close - minutes_before_end(settings.trading_end_hour,
                                             (settings.risky_start_hour, settings.risky_start_minute)) * 60
    sale = close - minutes_before_end(settings.trading_end_hour, settings.close_position_time) * 60
    return entries_end, sale


def _guard_day(signals: list[dict]) -> str | None:
    """The first day the account guard refused an entry: entries stayed paused from then to the run's end."""
    days = [signal.get("day") for signal in signals if signal.get("rule") == ACCOUNT_GUARD and signal.get("day")]
    return min(days) if days else None


def _geometric_percent(growths: list[float]) -> float | None:
    if not growths:
        return None
    if any(growth <= 0 for growth in growths):
        return -100.0
    return round((math.exp(sum(math.log(growth) for growth in growths) / len(growths)) - 1) * 100, 4)


def pace(days: list[dict], trades: list[dict], settings: BacktestSettings, capital: float,
         calendar: MarketCalendar | None, signals: list[dict] | None = None) -> dict:
    """The run's day growths as the paths draw them: full days, early-close mornings, and what was left out."""
    by_day: dict[str, list[dict]] = {}
    for trade in trades:
        if isinstance(trade.get("day"), str):
            by_day.setdefault(trade["day"], []).append(trade)
    own_closed = {found for found in (_closed_date(text) for text in settings.closed_dates) if found is not None}
    guard_day = _guard_day(signals or []) if settings.pause_below_balance_eur > 0 or \
        settings.pause_after_drawdown_percent > 0 else None

    account = float(capital)
    growths, early_growths, used, early_used, left_out = [], [], [], [], []
    partial, held_past_early_sale, trades_used, emptied, rules = 0, 0, 0, False, {}
    for record in sorted((day for day in days if day.get("status") == TESTED), key=lambda day: day.get("day") or ""):
        text = record.get("day")
        try:
            day = date.fromisoformat(text)
        except (TypeError, ValueError):
            continue
        found, estimated = calendar_day(day, calendar, rules=rules)
        if emptied:
            left_out.append({"day": text, "why": AFTER_EMPTY, "name": None, "estimated": False})
            continue
        if guard_day is not None and text > guard_day:
            left_out.append({"day": text, "why": AFTER_GUARD, "name": None, "estimated": False})
            continue
        if found is not None and found.kind == CLOSED:
            left_out.append({"day": text, "why": US_CLOSED, "name": found.name, "estimated": estimated})
            continue
        if day in own_closed:
            left_out.append({"day": text, "why": OWN_CLOSED, "name": None, "estimated": False})
            continue
        opened = by_day.get(text, [])
        before = account
        account = max(0.0, account + sum(_profit(trade) for trade in opened))
        growth = account / before if before > 0 else 0.0
        trades_used += len(opened)
        partial += 1 if record.get("partial") else 0
        if found is not None and found.kind == EARLY_CLOSE:
            # A day that closed early was traded as one: it is a morning as it stands.
            early_growths.append(round(growth, 8))
            early_used.append(text)
        else:
            growths.append(round(growth, 8))
            used.append(text)
            entries_end, sale = _early_moments(day, settings)
            morning = [trade for trade in opened if (_moment((trade.get("entry") or {}).get("decided_at")) or 0)
                       < entries_end]
            held_past_early_sale += sum(1 for trade in morning
                                        if (_moment((trade.get("exit") or {}).get("filled_at")) or 0) > sale)
            early_growths.append(round(max(0.0, before + sum(_profit(trade) for trade in morning)) / before, 8)
                                 if before > 0 else 0.0)
            early_used.append(text)
        if account <= 0:
            emptied = True
    return {
        "days_used": used,
        "growths": growths,
        "early_days_used": early_used,
        "early_growths": early_growths,
        "left_out": left_out,
        "trades_used": trades_used,
        "partial_days": partial,
        "held_past_early_sale": held_past_early_sale,
        "geometric_mean_percent": _geometric_percent(growths),
        "early_geometric_mean_percent": _geometric_percent(early_growths),
        "account_return_percent": round((account / capital - 1) * 100, 4) if capital > 0 else None,
        "sample": sample_size(trades_used, len(used)),
        "guard_day": guard_day,
        "emptied": emptied,
    }


def _first_day(now: datetime, zone: str) -> date:
    """The weekday after today in ``zone``."""
    day = now.astimezone(pytz.timezone(zone)).date() + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def _last_day(first: date) -> date:
    try:
        return first.replace(year=first.year + YEARS) - timedelta(days=1)
    except ValueError:  # 29 February
        return first.replace(year=first.year + YEARS, day=28)


def _notes(run: dict) -> list[str]:
    notes = []
    if run.get("stale"):
        notes.append("Made with older backtest code: its days may be traded otherwise today")
    if run.get("forward_of"):
        notes.append("A forward check's run: the days after its candidate's, at the candidate's settings")
    if run.get("calibrated_of"):
        notes.append("A run made again with what WATA's live trades measured")
    if run.get("decomposes"):
        notes.append("The run without the day's caps: its days are not WATA's as configured")
    if (run.get("paper") or {}).get("report"):
        notes.append("A paper report: the signals the rule raised, traded on the recorded turbos")
    return notes


def check_projectable(run: dict) -> None:
    """Refuses, in words, a run that has no days of its own to draw (before its files are read)."""
    if run.get("kind") != "run":
        raise NotProjectable("A sweep has no days of its own to draw: make one of its combinations a run")
    if (run.get("status") or {}).get("state") != "done" or not (run.get("summary") or {}).get("figures"):
        raise NotProjectable("The run is not finished")


def projection(run: dict, days: list[dict], trades: list[dict], settings: dict, rules: list, trading_timezone: str,
               now: datetime, *, signals_of=None, calendar: MarketCalendar | None = None,
               calendar_error: str | None = None) -> dict:
    """
    A finished run's projection inputs (the docstring above). ``signals_of`` reads the run's signals, only when its
    account guard was on. Raises NotProjectable for a run that has none at all (a sweep, a run not finished).
    """
    check_projectable(run)
    summary = run.get("summary") or {}
    chosen = BacktestSettings.from_dict(settings or {})
    money = summary.get("money") or {}
    capital = money.get("capital") if isinstance(money.get("capital"), (int, float)) else chosen.capital
    guard_on = chosen.pause_below_balance_eur > 0 or chosen.pause_after_drawdown_percent > 0
    signals = signals_of() if guard_on and signals_of is not None else []
    run_pace = pace(days, trades, chosen, float(capital), calendar, signals)

    zone = market_zone(rules, trading_timezone)
    first = _first_day(now, zone)
    last = _last_day(first)
    live_closed, unreadable = config_closed_dates(rules)
    to_come, skipped = days_to_come(first, last, calendar, live_closed)
    estimated = [day["day"] for day in to_come + skipped if day["estimated"]]

    notes = _notes(run)
    if run_pace["partial_days"]:
        notes.append(f"{run_pace['partial_days']} of its days were recorded for less than their whole hours")
    if run_pace["guard_day"]:
        notes.append(f"The account guard paused its entries on {run_pace['guard_day']}: the days after are left "
                     "out, and the paths never pause")
    if run_pace["emptied"]:
        notes.append("The account was emptied: the days after are left out, and a path can end at nothing")

    why_not = None
    if run_pace["trades_used"] == 0:
        why_not = "The run made no trade: there is no pace to draw"
    elif len(run_pace["growths"]) < LEAST_DAYS:
        why_not = (f"{len(run_pace['growths'])} day(s) to draw from: {LEAST_DAYS} at least are needed, and many "
                   "more to believe it")
    return {
        "usable": why_not is None,
        "why_not": why_not,
        "run_id": run.get("id"),
        "from": first.isoformat(),
        "to": last.isoformat(),
        "capital": float(capital),
        "seed": seed_of(str(run.get("id"))),
        "market_timezone": zone,
        "calendar": {
            "until": calendar.until.isoformat() if calendar is not None and calendar.until else None,
            "estimated_from": estimated[0] if estimated else None,
            "error": calendar_error,
            "config_unreadable": unreadable,
        },
        "days": to_come,
        "skipped": skipped,
        "pace": run_pace,
        "notes": notes,
    }


def run_projection(run: dict, days: list[dict], trades: list[dict], settings: dict, rules: list,
                   trading_timezone: str, now: datetime, *, signals_of=None) -> dict:
    """``projection`` with the US calendar file read; an unreadable one leaves every year to NYSE's rules."""
    try:
        calendar, error = read_market_calendar(), None
    except (OSError, ValueError, KeyError) as e:
        calendar, error = None, str(e)
    return projection(run, days, trades, settings, rules, trading_timezone, now, signals_of=signals_of,
                      calendar=calendar, calendar_error=error)
