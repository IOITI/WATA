"""
The AI agent's morning briefing (``admin.ai_agent.briefing_*``, off unless
briefing_enabled): before each trading day's start, a conversation about the
day ahead (a briefing subject of src/admin/agent/service.py: trading now as the
dashboard reads it, the health, the rules, what changed since the day before,
the day before's debrief), written by the briefing's model within its own cost
and without a backtest, kept on AI agent › Activity, its first lines said on
Telegram (briefing_telegram) by the agent's teller.

It is written from ``briefing_minutes_before`` the trading start (the
market_hours rule, in its own timezone) until an hour after it: later, the
day goes without one. A day the US market is closed (its calendar, or the
configuration's closed dates) gets one Telegram line, without a call to the
API. The agent's pause, its key and the month's budget hold it back as they
hold the operator's requests, and it tries again at its next look.
"""

import asyncio
import logging
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from starlette.concurrency import run_in_threadpool

from src.admin.agent.prompts import BRIEFING_PROMPT
from src.admin.agent.store import stamp
from src.admin.agent_jobs import JobFile, read_config
from src.admin.agent_telegram import BRIEFING, LINE
from src.admin.errors import NotPossible
from src.release.guards import trading_hours, trading_timezone
from src.trade.market_calendar import read_market_calendar

logger = logging.getLogger(__name__)

TICK_SECONDS = 60
READY_SECONDS = 10
FILE = "briefings.json"
KEPT_DAYS = 120
# After the trading start, a briefing is still worth writing for this long; later, the day goes without one.
LATE_MINUTES = 60


def briefing_window(config: dict, now: float, minutes_before: int) -> tuple[str, float, float] | None:
    """
    The trading day whose briefing may be written at ``now`` (its day in the
    trading timezone, as the debriefs and the Report count days) and the
    moments it may be written between, or None (a weekend, or not that time).
    """
    zone, start_hour, _ = trading_hours(config)
    local = datetime.fromtimestamp(now, ZoneInfo(zone))
    start = local.replace(hour=start_hour, minute=0, second=0, microsecond=0)
    opens, closes = start - timedelta(minutes=minutes_before), start + timedelta(minutes=LATE_MINUTES)
    if start.weekday() >= 5 or not opens.timestamp() <= now < closes.timestamp():
        return None
    day = start.astimezone(ZoneInfo(trading_timezone(config))).date()
    return day.isoformat(), opens.timestamp(), closes.timestamp()


def closed_today(config: dict, day: str, start_moment: float) -> str | None:
    """Why the market is closed on ``day``, or None: the US market's calendar, or the configuration's closed dates."""
    closed_dates = next((rule.get("rule_config", {}).get("market_closed_dates") or []
                         for rule in (config.get("trade") or {}).get("rules") or []
                         if isinstance(rule, dict) and rule.get("rule_type") == "market_closed_dates"), [])
    if date.fromisoformat(day).strftime("%d/%m/%Y") in closed_dates:
        return "a closed date of the configuration"
    try:
        calendar = read_market_calendar()
    except (OSError, ValueError, KeyError):
        return None
    closed = calendar.closed_on(datetime.fromtimestamp(start_moment, ZoneInfo("America/New_York")).date())
    return f"the US market is closed ({closed.name})" if closed is not None else None


class MorningBriefing:
    def __init__(self, *, agent, config_path: str, path: str, teller=None, clock=time.time):
        self._agent = agent
        self._config_path = config_path
        self._file = JobFile(path, "days", KEPT_DAYS)
        self._teller = teller
        self._clock = clock

    def days(self) -> dict:
        """Each day briefed: when, its conversation, or why the market was closed."""
        return self._file.read()

    def _window(self, config: dict, now: float, minutes_before: int) -> tuple[str, float, float] | None:
        return briefing_window(config, now, minutes_before)

    async def keep(self) -> None:
        """Until cancelled: a look every minute, once the agent's tools started."""
        while True:
            if not self._agent.ready:
                await asyncio.sleep(READY_SECONDS)
                continue
            try:
                await self.catch_up()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"The AI agent's morning briefing could not catch up: {e}")
            await asyncio.sleep(TICK_SECONDS)

    async def catch_up(self, now: float | None = None) -> dict | None:
        """The day's briefing asked once in its window, and handed to be told; what is kept of the day."""
        settings = self._agent.settings
        if not settings.briefing_enabled:
            return None
        now = self._clock() if now is None else now
        config = await run_in_threadpool(read_config, self._config_path)
        window = self._window(config, now, settings.briefing_minutes_before)
        if window is None:
            return None
        day, _, closes = window
        days = await run_in_threadpool(self.days)
        if day in days:
            return days[day]
        start = closes - LATE_MINUTES * 60
        why_closed = await run_in_threadpool(closed_today, config, day, start)
        if why_closed is not None:
            entry = {"at": stamp(now), "conversation": None, "closed": why_closed}
            if settings.briefing_telegram and self._teller is not None:
                when = date.fromisoformat(day).strftime("%a %d %b")
                await run_in_threadpool(self._teller.add, None, LINE,
                                        text=f"☀️ WATA, {when}: {why_closed}. Nothing is traded today.")
        else:
            try:
                meta = await self._agent.create(
                    {"kind": "briefing", "id": day}, BRIEFING_PROMPT, "briefing", settings.briefing_model,
                    settings.briefing_effort,
                    job={"kind": "briefing", "day": day, "max_usd": settings.briefing_max_usd, "backtests": 0,
                         "title": f"Briefing of {date.fromisoformat(day):%a %d %b %Y}"})
            except NotPossible as e:
                # Paused, without a key, the month spent: asked again at the next look, while its window lasts.
                logger.info(f"The morning briefing of {day} waits: {e}")
                return None
            entry = {"at": stamp(now), "conversation": meta["id"]}
            if settings.briefing_telegram and self._teller is not None:
                await run_in_threadpool(self._teller.add, meta["id"], BRIEFING, request=0, label=day)
        await run_in_threadpool(self._file.put, day, entry)
        return entry
