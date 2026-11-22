"""
A signal rule's protections (src/admin/live_safety.py), for the rule page's "Protections" and the go-live dialog: what
keeps the account safe while the rule trades live, each with where it is set. Read only: nothing here changes a
setting. Each fact is read on its own, and one that cannot be read is said so rather than guessed.
"""

import json
import logging
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from src.admin import live_safety as ls
from src.admin.auth import require_session
from src.admin.backtests import NO_ENTRY
from src.admin.errors import NotPossible, UnknownRun
from src.admin.processes import DONE
from src.admin.saxo import called
from src.admin.ta_rules import RuleNotFound, RuleStore
from src.admin.vault import telegram_bot_set
from src.backtest.clock import TradingClock
from src.backtest.settings import from_config
from src.trade.macro_calendar import NewsRule, read_calendar

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ta/rules", dependencies=[Depends(require_session)])


def _rule(ctx, rule_id: str) -> dict:
    if ctx.ta_scripts is None:
        raise HTTPException(status_code=503, detail="Technical Analysis is not available")
    try:
        return RuleStore(ctx.ta_scripts).rule(rule_id)
    except RuleNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


def _config(ctx) -> dict:
    with open(ctx.settings.config_path, encoding="utf-8") as config_file:
        return json.load(config_file)


def _said(error: Exception) -> str:
    return str(error) or type(error).__name__


def _run_entries(backtests, run_id: str) -> list[dict]:
    return ls.entries_of(backtests.trades(run_id), backtests.signals(run_id))


def _entries(ctx, rule: dict) -> tuple[list[dict], dict | None]:
    """
    The entries the account is set against: the newest run of the backtest configuration linked to the rule, else its
    days on paper of the last ``PAPER_DAYS``; and what they are.
    """
    backtests = ctx.backtests
    if backtests is None or not ctx.settings.backtests_enabled:
        return [], None
    check = getattr(ctx, "live_check", None)
    linked = check.linked_run(rule["id"]) if check is not None else None
    if linked is not None:
        entries = _run_entries(backtests, linked["id"])
        return entries, {"kind": "backtest", "run_id": linked["id"], "name": linked.get("name"),
                         "first_day": linked.get("from"), "last_day": linked.get("to")}
    days = sorted((backtests.paper_index().get(rule["id"]) or {}).items(), reverse=True)[:ls.PAPER_DAYS]
    entries, read = [], []
    for day, run_id in days:
        if run_id == NO_ENTRY:
            read.append(day)
            continue
        try:
            if backtests.state(run_id).get("state") != DONE:
                continue
            entries += _run_entries(backtests, run_id)
        except (UnknownRun, NotPossible):
            continue
        read.append(day)
    if not read:
        return [], None
    return sorted(entries, key=lambda entry: entry["at"]), {
        "kind": "paper", "name": f"its {len(read)} day{'s' if len(read) != 1 else ''} on paper",
        "first_day": min(read), "last_day": max(read),
    }


def _funds(ctx, config: dict, rule: dict, value: float | None) -> dict:
    entries, source = _entries(ctx, rule)
    return ls.funds_what_if(from_config(config), value, entries, source)


def _calendar(config: dict) -> tuple[date | None, bool]:
    """The release calendar's last known day for the releases the rule names, and whether it could be read."""
    found = ls.rule_config(config, ls.MACRO)
    if found is None:
        return None, True
    try:
        return read_calendar().known_until(NewsRule.from_config(found).events), True
    except (OSError, ValueError, KeyError):
        return None, False


@router.get("/{rule_id}/protections")
async def protections(rule_id: str, request: Request):
    """
    Each protection of a rule going, or gone, live: the signal source, Saxo, the pause, what the account buys, the
    day's limits and breakers, the account guard, the edge guard, the window around US releases, Telegram; and the
    figures of what the account buys.
    """
    ctx = request.app.state.ctx
    rule = await run_in_threadpool(_rule, ctx, rule_id)
    try:
        config = await run_in_threadpool(_config, ctx)
    except (OSError, ValueError) as e:
        raise HTTPException(status_code=503, detail=f"The configuration cannot be read: {_said(e)}") from e
    zone = TradingClock(ctx.settings.timezone).zone
    today = datetime.now(zone).date()
    checks: dict[str, dict] = {}

    try:
        checks["signal_source"] = ls.signal_source(rule, await ctx.services.webhook_state())
    except Exception as e:
        checks["signal_source"] = ls.not_read("signal_source", "Signal source", _said(e))

    try:
        status = await called(ctx.saxo.status)
        beat = (await ctx.queries.service_heartbeats()).get("trader") or {}
        session = (beat.get("figures") or {}).get("saxo_session")
        wanted = ((config.get("trade") or {}).get("config") or {}).get("saxo_session", {}).get("full_trading_level") is True
        checks["saxo"] = ls.saxo(status, session, wanted)
    except Exception as e:
        checks["saxo"] = ls.not_read("saxo", "Saxo login and session", _said(e))

    try:
        checks["paused"] = ls.paused(await ctx.queries.entries_paused_since(), await ctx.queries.entries_paused_by_guard(),
                                     await ctx.queries.entries_paused_by_edge_guard(), zone)
    except Exception as e:
        checks["paused"] = ls.not_read("paused", "New entries", _said(e))

    value = None
    try:
        value = await ctx.queries.last_balance()
    except Exception as e:
        logger.warning(f"The protections could not read the account's value: {e}")
    what_if = None
    try:
        what_if = await run_in_threadpool(_funds, ctx, config, rule, value)
        checks["funds"] = ls.funds(what_if)
    except Exception as e:
        checks["funds"] = ls.not_read("funds", "What the account buys", _said(e))

    checks["day_limits"] = ls.day_limits(config)
    checks["breakers"] = ls.breakers(config)
    checks["account_guard"] = ls.account_guard(config, value)

    guard_on = (ls.rule_config(config, ls.RISK) or {}).get("pause_when_edge_broken") is True
    watch = getattr(ctx, "edge_watch", None)
    enabled = watch is not None and ctx.settings.backtests_enabled
    try:
        judgements = await watch.judgements() if enabled else {}
        checks["edge_guard"] = ls.edge_guard(
            rule, enabled=enabled, guard_on=guard_on, entry=(judgements.get("rules") or {}).get(rule_id),
            live_reference=judgements.get("live") is not None,
            least_trades=(judgements.get("guard") or {}).get("least_trades") or 20,
            risk_rule=ls.rule_config(config, ls.RISK) is not None,
        )
    except Exception as e:
        checks["edge_guard"] = ls.not_read("edge_guard", "Edge guard", _said(e))

    known_until, readable = await run_in_threadpool(_calendar, config)
    checks["release_window"] = ls.release_window(config, known_until, readable, today)

    try:
        checks["telegram"] = ls.telegram(await telegram_bot_set(ctx.vault))
    except Exception as e:
        checks["telegram"] = ls.not_read("telegram", "Telegram", _said(e))

    return {
        "rule": rule_id,
        "checks": ls.protections(checks),
        "funds": what_if,
        "read_at": datetime.now(timezone.utc).isoformat(),
    }
