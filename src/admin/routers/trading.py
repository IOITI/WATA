from datetime import date, datetime, timedelta

import pytz
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from src.admin.auth import require_session

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
        "trading_start_hour": market_hours.get("trading_start_hour"),
        "trading_end_hour": market_hours.get("trading_end_hour"),
        "risky_trading_start_hour": market_hours.get("risky_trading_start_hour"),
        "risky_trading_start_minute": market_hours.get("risky_trading_start_minute"),
    }


def _market_closed_on(config_manager, day: date) -> bool:
    """Whether ``day`` is one of the configured closed dates, written DD/MM/YYYY as the trader reads them."""
    closed_dates = _rule_config(config_manager, "market_closed_dates").get("market_closed_dates") or []
    return day.strftime("%d/%m/%Y") in closed_dates


def _cooldown(last_loss_at: datetime | None, cooldown_minutes: int) -> dict:
    if last_loss_at is None or cooldown_minutes <= 0:
        return {"last_loss_at": last_loss_at, "ends_at": None, "active": False}
    ends_at = last_loss_at + timedelta(minutes=cooldown_minutes)
    return {"last_loss_at": last_loss_at, "ends_at": ends_at, "active": datetime.now(pytz.utc) < ends_at}


@router.get("/overview")
async def overview(request: Request):
    ctx = request.app.state.ctx
    today = datetime.now(pytz.timezone(ctx.settings.timezone)).date()
    limits = _limits(ctx.config_manager)
    recent = await ctx.queries.performance(start=today - timedelta(days=RECENT_DAYS - 1))
    return {
        "date": today,
        "timezone": ctx.settings.timezone,
        "trading_mode": ctx.config_manager.get_config_value("trade.config.trading_mode"),
        "day": await ctx.queries.day_overview(),
        "limits": limits,
        "market_closed_today": _market_closed_on(ctx.config_manager, today),
        "cooldown": _cooldown(await ctx.queries.last_loss_timestamp(), limits["cooldown_after_loss_minutes"]),
        "entries_paused_since": await ctx.queries.entries_paused_since(),
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
async def report(request: Request):
    """The figures of the daily Telegram report, with the recorded account balances."""
    ctx = request.app.state.ctx
    return await ctx.queries.report(
        milestones_eur=ctx.config_manager.get_config_value("reporting.milestones_eur", None),
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
