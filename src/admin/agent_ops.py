"""
What the AI agent reads of the admin itself (``OpsReads`` of
src/admin/agent/tools.py), built here over the admin's context, outside the
agent's package, which reaches no service (tests/test_agent_boundary.py):

- its health, as System shows it, and a service's log lines (cleaned of
  secrets as the Logs page shows them) while admin.ai_agent.read_logs allows;
- trading now, as the dashboard's overview reads it: the market's day, the
  window around a US release, the pause, Saxo's login, the day's figures
  against their limits, the open positions;
- what changed: config.json's saves key by key (masked as its journal writes
  them), the operator's actions from the audit, the last deploy;
- the TA engine's last minutes, as the Engine monitor's Now panel reads them;
- what waits to be deployed, and why each container is held;
- LuxAlgo's MCP tools, through the admin's client (src/admin/luxalgo.py: its
  allowlist, its fixed context, its limits), and the origin of an indicator
  it brings in, kept beside the scripts (src/admin/script_origins.py).

What must not reach Anthropic is left out here: an operator's address, a
credential's last characters, a sign-in. The values config.json's journal
keeps are masked already where they are secrets.
"""

import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from starlette.concurrency import run_in_threadpool

# What one look at the changes covers at most, and the lines it gives.
CHANGES_MAX_HOURS = 168
CHANGES_MAX_LINES = 200
# The journal's and the audit's newest entries read, of which the window keeps its own.
_READ = 1000
# Actions of the audit said elsewhere (config.json's saves, key by key) or that change nothing.
_NOT_CHANGES = ("configuration saved", "login", "login locked", "webhook address shown", "daily stats requested")
# A credential's last characters, as the audit names it ("…, ending …a1b2").
_ENDING = re.compile(r",? ending …[^\s;,]+")
# A deploy's steps as the agent reads them, and the open positions' figures.
_STEP = ("container", "state", "detail")
_POSITION = (
    "position_id", "action", "instrument_name", "execution_time_open", "position_amount", "position_open_price",
    "position_max_performance_percent", "position_min_performance_percent", "live_bid", "live_performance_percent",
    "live_observed_at", "sell_refusals",
)
_HOURS = ("trading_start_hour", "trading_end_hour", "risky_trading_start_hour", "risky_trading_start_minute",
          "close_position_time", "market_timezone")
_TODAY = ("trade_count", "closed_count", "performance_percent", "profit_loss", "best_percent", "worst_percent")


def _moment(value) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        found = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return found if found.tzinfo else found.replace(tzinfo=timezone.utc)


def trading_now(overview: dict, now: datetime) -> dict:
    """The dashboard's overview (src/admin/routers/trading.py overview_of) as the agent reads it."""
    limits = overview.get("limits") or {}
    day = overview.get("day") or {}
    zone = limits.get("market_timezone") or overview.get("timezone")
    return {
        "now": now.isoformat(timespec="seconds"),
        "timezone": overview.get("timezone"),
        "trading_mode": overview.get("trading_mode"),
        "market": {
            "closed_today": overview.get("market_closed_today"),
            # The day of the week in the market's timezone: closed_today knows only the closed dates and US holidays,
            # and a Saturday or a Sunday is closed all the same (read as open on a Saturday by Haiku 5.5, 2026-10-31).
            "weekday": now.astimezone(ZoneInfo(zone)).strftime("%A") if zone else None,
            "day": overview.get("market_day"),
            "hours": {key: limits.get(key) for key in _HOURS},
        },
        "news": overview.get("news"),
        "entries_paused_since": overview.get("entries_paused_since"),
        "entries_paused_by_account_guard": overview.get("entries_paused_by_account_guard"),
        "saxo_login": overview.get("saxo_login"),
        "today": {key: day.get(key) for key in _TODAY},
        "limits": {key: value for key, value in limits.items() if key not in _HOURS},
        "cooldown": overview.get("cooldown"),
        "breakers": overview.get("breakers"),
        "open_positions": [{key: position.get(key) for key in _POSITION if key in position}
                           for position in overview.get("open_positions") or []],
    }


def changes_between(config_history: list[dict], audit: list[dict], last_deploy: dict | None, since: datetime,
                    until: datetime) -> dict:
    """
    What changed between ``since`` and ``until``, newest first: config.json's
    saves key by key, the operator's actions, the last deploy; who did it is
    never said (an address), a credential by its name alone.
    """
    lines = []
    for entry in config_history:
        at = _moment(entry.get("at"))
        if at is None or not since <= at <= until:
            continue
        lines.append({
            "at": at.isoformat(timespec="seconds"), "what": "config.json", "how": entry.get("action"),
            "changes": [{key: change.get(key) for key in ("path", "before", "after", "added", "removed")}
                        for change in entry.get("changes") or []],
        })
    for entry in audit:
        at = _moment(entry.get("at"))
        action = str(entry.get("action") or "")
        if at is None or not since <= at <= until or action in _NOT_CHANGES:
            continue
        line = {"at": at.isoformat(timespec="seconds"), "what": "operator action", "action": action}
        detail = _ENDING.sub("", str(entry.get("detail") or "")).strip()
        if detail:
            line["detail"] = detail
        lines.append(line)
    if last_deploy:
        at = _moment(last_deploy.get("finished_at") or last_deploy.get("started_at"))
        if at is not None and since <= at <= until:
            lines.append({
                "at": at.isoformat(timespec="seconds"), "what": "deploy", "kind": last_deploy.get("kind"),
                "version": last_deploy.get("version"), "forced": bool(last_deploy.get("force")),
                "steps": [{key: step.get(key) for key in _STEP} for step in last_deploy.get("steps") or []],
                "error": last_deploy.get("error"),
            })
    lines.sort(key=lambda line: line["at"], reverse=True)
    kept = lines[:CHANGES_MAX_LINES]
    said = {"from": since.isoformat(timespec="seconds"), "to": until.isoformat(timespec="seconds"),
            "lines": len(lines), "changes": kept}
    if len(kept) < len(lines):
        said["cut"] = "the oldest lines of the window were left out: ask for fewer hours"
    return said


def release_now(status: dict, held: dict[str, str], waiting: dict[str, dict], applying: dict | None) -> dict:
    """What waits for the newest release and why, whether an apply is under way, how the last one ended."""
    if not status:
        return {"available": False, "said": "watadeploy has said nothing on this server yet"}
    last = status.get("last") or None
    return {
        "available": True,
        "newest": status.get("newest"),
        "checked_at": status.get("at"),
        "waiting": [
            {"container": name, "version": entry.get("version"), "kind": entry.get("kind"), "when": entry.get("when"),
             "said": entry.get("said"), "held": held.get(name) or entry.get("held")}
            for name, entry in waiting.items()
        ],
        "applying": None if not applying else {
            "kind": applying.get("kind"), "version": applying.get("version"), "started_at": applying.get("started_at"),
            "steps": [{key: step.get(key) for key in _STEP} for step in applying.get("steps") or []],
        },
        "last": None if not last else {
            "kind": last.get("kind"), "version": last.get("version"), "forced": bool(last.get("force")),
            "started_at": last.get("started_at"), "finished_at": last.get("finished_at"),
            "steps": [{key: step.get(key) for key in _STEP} for step in last.get("steps") or []],
            "error": last.get("error"),
        },
    }


def ops_reads(ctx):
    """
    The reads the agent is given (src/admin/agent/tools.py OpsReads): its
    health always, the logs while admin.ai_agent.read_logs, the others as the
    admin has what they read.
    """
    from functools import partial

    from src.admin.agent.tools import OpsReads
    from src.admin.health import collect_health
    from src.admin.logs import read_log

    settings = ctx.settings

    async def health() -> dict:
        return await collect_health(ctx)

    async def trading() -> dict:
        from src.admin.routers.trading import overview_of

        return trading_now(await overview_of(ctx), datetime.now(timezone.utc))

    def changes(since: datetime, until: datetime) -> dict:
        history = ctx.config_editor.history(_READ) if ctx.config_editor is not None else []
        audit = ctx.audit_log.read(_READ) if ctx.audit_log is not None else []
        last = (ctx.release.status() or {}).get("last") if ctx.release is not None else None
        return changes_between(history, audit, last, since, until)

    async def engine() -> dict:
        from src.admin.routers.ta import engine_now

        return await engine_now(ctx)

    async def release() -> dict:
        from src.admin.routers.release import held_now

        status = await run_in_threadpool(ctx.release.status)
        held = await held_now(ctx, status) if status else {}
        return release_now(status, held, ctx.release.waiting(status) if status else {},
                           ctx.release.applying(status) if status else None)

    return OpsReads(
        health=health,
        logs=partial(read_log, settings.log_path) if settings.ai_agent.read_logs else None,
        trading=trading,
        changes=lambda since, until: run_in_threadpool(changes, since, until),
        engine=engine if ctx.ta_scripts is not None else None,
        release=release if ctx.release is not None else None,
    )


def luxalgo_reads(ctx):
    """LuxAlgo's tools for the agent (src/admin/agent/tools.py LuxAlgoReads), None while admin.luxalgo_enabled is off."""
    if getattr(ctx, "luxalgo", None) is None or getattr(ctx, "script_origins", None) is None:
        return None
    import time

    from src.admin.agent.tools import LuxAlgoReads
    from src.admin.script_origins import luxalgo_origin

    async def keep(script_id: str, found: dict, by: str) -> dict:
        origin = luxalgo_origin(found, by, time.time())
        return await run_in_threadpool(ctx.script_origins.record, script_id, origin)

    async def source(slug: str) -> dict:
        found = await ctx.luxalgo.source(slug)
        if getattr(ctx, "luxalgo_checks", None) is not None:
            # Read whole: checked for running on WATA, once per source.
            ctx.luxalgo_checks.noticed(slug, found["source"])
        return found

    async def indicator(slug: str) -> dict | None:
        from src.admin.luxalgo import LuxAlgoUnknown

        try:
            found = await ctx.luxalgo.read("library_get_indicator", {"slug": slug})
        except LuxAlgoUnknown:
            return None
        return found if isinstance(found, dict) else {"slug": slug}

    async def known(slugs: list[str]) -> dict:
        from src.admin.luxalgo_knowledge import known as known_of

        return await run_in_threadpool(known_of, ctx, slugs)

    return LuxAlgoReads(tools=ctx.luxalgo.tools, call=ctx.luxalgo.call, source=source, keep=keep,
                        indicator=indicator, known=known)
