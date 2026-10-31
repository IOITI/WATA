from datetime import date, datetime, timedelta

import pytz
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from src.admin.auth import require_session
from src.admin.paper import index_asset
from src.heartbeat import HEARTBEAT_STALE_SECONDS
from src.trade.account_guard import AccountGuard
from src.trade.day_breakers import DayBreakers, best_and_now, losses_in_a_row
from src.trade.macro_calendar import NewsRule, next_window, read_calendar
from src.trade.market_calendar import entries_end_moment, read_market_calendar, sale_moment

router = APIRouter(prefix="/api", dependencies=[Depends(require_session)])

RECENT_DAYS = 30
POSITION_STATUSES = {"open": "Open", "closed": "Closed"}


def _rule_config(config_manager, rule_type: str) -> dict:
    for rule in config_manager.get_config_value("trade.rules", []):
        if rule.get("rule_type") == rule_type:
            return rule.get("rule_config", {})
    return {}


def _limits(config_manager) -> dict:
    day_trading = _rule_config(config_manager, "day_trading")
    risk_management = _rule_config(config_manager, "risk_management")
    market_hours = _rule_config(config_manager, "market_hours")
    return {
        "profit_target_percent": day_trading.get("percent_profit_wanted_per_days"),
        "stop_entries_above_percent": day_trading.get("dont_enter_trade_if_day_profit_is_more_than"),
        "max_day_loss_percent": day_trading.get("max_day_loss_percent"),
        "close_position_time": day_trading.get("close_position_time"),
        "max_trades_per_day": risk_management.get("max_trades_per_day", 0),
        "cooldown_after_loss_minutes": risk_management.get("cooldown_after_loss_minutes", 0),
        # The day's breakers and the account guard (src/trade/day_breakers.py, account_guard.py): 0 is off.
        "max_consecutive_losses": DayBreakers.from_config(risk_management).max_losses,
        "max_drawdown_from_day_best_percent": DayBreakers.from_config(risk_management).max_drawdown,
        "pause_below_balance_eur": AccountGuard.from_config(risk_management).below_eur,
        "pause_after_drawdown_percent": AccountGuard.from_config(risk_management).drawdown_percent,
        "trading_start_hour": market_hours.get("trading_start_hour"),
        "trading_end_hour": market_hours.get("trading_end_hour"),
        "risky_trading_start_hour": market_hours.get("risky_trading_start_hour"),
        "risky_trading_start_minute": market_hours.get("risky_trading_start_minute"),
        # The timezone of these hours and of close_position_time; None: the trading one.
        "market_timezone": market_hours.get("timezone") or None,
    }


def _market_closed_on(config_manager, day: date) -> bool:
    """Whether ``day`` is one of the configured closed dates, written DD/MM/YYYY as the trader reads them."""
    closed_dates = _rule_config(config_manager, "market_closed_dates").get("market_closed_dates") or []
    return day.strftime("%d/%m/%Y") in closed_dates


def _market_day(config_manager, trading_timezone: str, now: datetime) -> dict | None:
    """
    Today's US market day when it is not an ordinary one (src/trade/market_calendar.py),
    as the trader reads it: closed, or closing early, with when entries end and
    when what is held is sold. None on an ordinary day or without the calendar.
    """
    market_hours = _rule_config(config_manager, "market_hours")
    day_trading = _rule_config(config_manager, "day_trading")
    zone = market_hours.get("timezone") or trading_timezone
    try:
        calendar = read_market_calendar()
    except (OSError, ValueError, KeyError):
        return None
    day = now.astimezone(pytz.timezone(zone)).date()
    closed = calendar.closed_on(day)
    if closed is not None:
        return {"kind": "closed", "name": closed.name}
    early = calendar.early_close_on(day)
    if early is None:
        return None
    found = {"kind": "early_close", "name": early.name, "closes_at": _iso(early.close_moment())}
    end_hour = market_hours.get("trading_end_hour")
    risky = (market_hours.get("risky_trading_start_hour"), market_hours.get("risky_trading_start_minute"))
    if isinstance(end_hour, int) and None not in risky:
        found["entries_end_at"] = _iso(entries_end_moment(early, end_hour, risky))
        if day_trading.get("close_position_time"):
            found["sale_at"] = _iso(sale_moment(day, day_trading["close_position_time"], zone, calendar, end_hour))
    return found


def _iso(moment: float) -> str:
    return datetime.fromtimestamp(moment, pytz.utc).isoformat()


def _news(config_manager, now: datetime) -> dict | None:
    """
    While the trader keeps entries out of a window around US releases (the
    macro_news rule): the window in force, or the next to start within a day,
    and the calendar's last known day.
    """
    rule = next((rule for rule in config_manager.get_config_value("trade.rules", []) or []
                 if isinstance(rule, dict) and rule.get("rule_type") == "macro_news"), None)
    if rule is None:
        return None
    try:
        news_rule = NewsRule.from_config(rule.get("rule_config"))
        calendar = read_calendar()
    except (OSError, ValueError, KeyError):
        return {"window": None, "known_until": None, "readable": False}
    window = next_window(calendar, news_rule, now.timestamp())
    return {
        "window": None if window is None else {
            "event": window.event.event, "released_at": _iso(window.event.moment()),
            "start": _iso(window.start), "end": _iso(window.end),
        },
        "known_until": calendar.known_until(news_rule.events),
        "readable": True,
    }


def _saxo_login(beats: dict) -> dict | None:
    """
    Saxo's login as the trader last said it (its heartbeat): whether Saxo
    refuses to renew it, which keeps entries out. None while the trader is not
    heard from.
    """
    beat = beats.get("trader")
    if beat is None or (datetime.now(pytz.utc) - beat["beat_at"]).total_seconds() > HEARTBEAT_STALE_SECONDS:
        return None
    login = (beat.get("figures") or {}).get("saxo_login") or {}
    return {
        "renewal_failing_since": login.get("renewal_failing_since"),
        "error": login.get("error"),
        "lapses_at": login.get("lapses_at"),
    }


async def _breakers(ctx, limits: dict) -> dict | None:
    """The day's figures the breakers are counted on, as the trader counts them; None while both are off."""
    if not (limits["max_consecutive_losses"] or limits["max_drawdown_from_day_best_percent"]):
        return None
    results = await ctx.queries.today_closed_results()
    best, now = best_and_now(results)
    return {"losses_in_a_row": losses_in_a_row(results), "day_best_percent": best, "day_percent": now}


def _cooldown(last_loss_at: datetime | None, cooldown_minutes: int) -> dict:
    if last_loss_at is None or cooldown_minutes <= 0:
        return {"last_loss_at": last_loss_at, "ends_at": None, "active": False}
    ends_at = last_loss_at + timedelta(minutes=cooldown_minutes)
    return {"last_loss_at": last_loss_at, "ends_at": ends_at, "active": datetime.now(pytz.utc) < ends_at}


@router.get("/overview")
async def overview(request: Request):
    return await overview_of(request.app.state.ctx)


async def overview_of(ctx) -> dict:
    """The dashboard's overview: the route's answer, and trading now as the AI agent reads it (src/admin/agent_ops.py)."""
    today = datetime.now(pytz.timezone(ctx.settings.timezone)).date()
    limits = _limits(ctx.config_manager)
    market_day = _market_day(ctx.config_manager, ctx.settings.timezone, datetime.now(pytz.utc))
    recent = await ctx.queries.performance(start=today - timedelta(days=RECENT_DAYS - 1))
    return {
        "date": today,
        "timezone": ctx.settings.timezone,
        "trading_mode": ctx.config_manager.get_config_value("trade.config.trading_mode"),
        "day": await ctx.queries.day_overview(),
        "limits": limits,
        "market_closed_today": _market_closed_on(ctx.config_manager, today) or (market_day or {}).get("kind") == "closed",
        # Today's US market day when it is not an ordinary one: a holiday, an early close.
        "market_day": market_day,
        "cooldown": _cooldown(await ctx.queries.last_loss_timestamp(), limits["cooldown_after_loss_minutes"]),
        # No entry around US releases (the macro_news rule); None while the rule is off.
        "news": _news(ctx.config_manager, datetime.now(pytz.utc)),
        "entries_paused_since": await ctx.queries.entries_paused_since(),
        # Set while the pause is the account guard's own (src/trade/account_guard.py).
        "entries_paused_by_account_guard": await ctx.queries.entries_paused_by_guard(),
        # The day's breakers' figures (losses in a row, the day's best and now); None while they are off.
        "breakers": await _breakers(ctx, limits),
        # Saxo refusing to renew the login keeps entries out (the trader's heartbeat); None while not heard from.
        "saxo_login": _saxo_login(await ctx.queries.service_heartbeats()),
        "open_positions": await ctx.queries.open_positions(),
        "recent_days": recent["daily"],
    }


@router.get("/positions")
async def list_positions(
    request: Request,
    status: str | None = Query(None, pattern="^(open|closed)$"),
    start: date | None = Query(None, alias="from"),
    end: date | None = Query(None, alias="to"),
    limit: int = Query(500, ge=1, le=2000),
):
    queries = request.app.state.ctx.queries
    if status == "open":
        # Whatever the date range: they are open now, and come with their live value.
        positions = await queries.open_positions()
        return {"items": positions, "total": len(positions)}
    return await queries.list_positions(
        status=POSITION_STATUSES.get(status),
        start=start,
        end=end,
        limit=limit,
    )


@router.get("/positions/{position_id}/chart")
async def position_chart(position_id: str, request: Request):
    """
    What a position is drawn on: its turbo as the price recorder kept it (none when it was not recorded), the
    index of its indice below it, the day it opened, and the signal that opened it.
    """
    ctx = request.app.state.ctx
    found = await ctx.queries.position_chart(position_id)
    if found is None:
        raise HTTPException(status_code=404, detail="Position not found")
    indices = list((_rule_config(ctx.config_manager, "allowed_indices").get("indice_ids") or {}))
    index = index_asset(ctx.config_manager.config_data, indices[0]) if indices else None
    zone = pytz.timezone(ctx.settings.timezone)
    opened = found["opened_at"]
    return {
        "turbo": f"saxo/{found['price_instrument_id']}" if found["price_instrument_id"] is not None else None,
        "index": f"{index['provider']}/{index['key']}" if index else None,
        "day": opened.astimezone(zone).date().isoformat() if opened is not None else None,
        "signal": found["signal"],
    }


@router.get("/positions/{position_id}")
async def get_position(position_id: str, request: Request):
    position = await request.app.state.ctx.queries.get_position(position_id)
    if position is None:
        raise HTTPException(status_code=404, detail="Position not found")
    return position


@router.get("/orders")
async def list_orders(
    request: Request,
    start: date | None = Query(None, alias="from"),
    end: date | None = Query(None, alias="to"),
    limit: int = Query(500, ge=1, le=2000),
):
    return await request.app.state.ctx.queries.list_orders(start=start, end=end, limit=limit)


@router.get("/journal")
async def signal_journal(
    request: Request,
    start: date | None = Query(None, alias="from"),
    end: date | None = Query(None, alias="to"),
    limit: int = Query(200, ge=1, le=2000),
):
    """What the trader did with each signal it received."""
    return {"items": await request.app.state.ctx.queries.signal_journal(start=start, end=end, limit=limit)}


@router.get("/exits")
async def exits(
    request: Request,
    start: date | None = Query(None, alias="from"),
    end: date | None = Query(None, alias="to"),
):
    ctx = request.app.state.ctx
    return {
        "from": start,
        "to": end,
        "timezone": ctx.settings.timezone,
        **await ctx.queries.exits(start=start, end=end),
    }


@router.get("/report")
async def report(request: Request, day: date | None = Query(None)):
    """
    The figures of the daily Telegram report, with the recorded account balances: of today, or as of ``day``
    (the AI agent's links to a day lead there).
    """
    ctx = request.app.state.ctx
    today = datetime.now(pytz.timezone(ctx.settings.timezone)).date()
    if day is not None and day > today:
        raise HTTPException(status_code=422, detail="A report is of a day that has begun")
    return await ctx.queries.report(
        milestones_eur=ctx.config_manager.get_config_value("reporting.milestones_eur", None),
        **({"today": day} if day is not None else {}),
    )


@router.get("/performance")
async def performance(
    request: Request,
    start: date | None = Query(None, alias="from"),
    end: date | None = Query(None, alias="to"),
):
    ctx = request.app.state.ctx
    return {
        "from": start,
        "to": end,
        "timezone": ctx.settings.timezone,
        **await ctx.queries.performance(start=start, end=end),
    }
