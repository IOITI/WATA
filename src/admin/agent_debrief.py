"""
The AI agent's daily debrief (``admin.ai_agent.debrief_*``, off unless
debrief_enabled): each trading day, once the market is closed by the live
rules, a conversation about the day (a day subject of src/admin/agent/service.py:
its trades, its signals, what Saxo refused, its paper twin), written by the
debrief's model within its own cost and without a backtest, kept on AI agent ›
Activity; once written, its first lines said on Telegram (debrief_telegram) by
the agent's teller (src/admin/agent_telegram.py). A day without a trade, a
signal or a request to Saxo is noted in a line, without a call to the API.

It asks what the operator could ask: the agent's pause, its key and the month's
budget hold it back as they hold the operator's requests, and it tries again at
its next tick. It lives outside src/admin/agent/, which never reaches the
notifier (tests/test_agent_boundary.py), and keeps what it did in its own file
beside the conversations (``debriefs.json``).
"""

import asyncio
import logging
import time
from datetime import date, datetime

import pytz
from starlette.concurrency import run_in_threadpool

from src.admin.agent.prompts import DEBRIEF_PROMPT
from src.admin.agent.store import stamp
from src.admin.agent_jobs import JobFile, read_config
from src.admin.agent_telegram import DEBRIEF
from src.admin.downloads import market_is_open, trading_hours
from src.admin.errors import NotPossible
from src.admin.paper import last_finished_day

logger = logging.getLogger(__name__)

TICK_SECONDS = 15 * 60
# While the agent's tools start with the admin: looked at again this often.
READY_SECONDS = 10
FILE = "debriefs.json"
# The days its file keeps, the newest.
KEPT_DAYS = 120


def debriefed_day(config: dict, now: float) -> date:
    """The newest weekday whose trading hours are over at ``now``: today once its end hour passed, else the one before."""
    zone, _, end_hour = trading_hours(config)
    local = datetime.fromtimestamp(now, pytz.timezone(zone))
    if local.weekday() < 5 and local.hour >= end_hour:
        return local.date()
    return last_finished_day(local.date())


class DailyDebrief:
    def __init__(self, *, agent, queries, config_path: str, path: str, clock=time.time, teller=None):
        self._agent = agent
        # The admin's named queries: whether the day has anything to say.
        self._queries = queries
        self._config_path = config_path
        self._file = JobFile(path, "days", KEPT_DAYS)
        self._clock = clock
        # Telegram, through the agent's teller (src/admin/agent_telegram.py).
        self._teller = teller

    def _config(self) -> dict:
        """Read again at each tick: the trading hours may have been edited."""
        return read_config(self._config_path)

    def _market_is_open(self, config: dict, now: float) -> bool:
        return market_is_open(config, now)

    def days(self) -> dict:
        """Each day debriefed: when, its conversation (none for a quiet day), whether it was handed to be told."""
        return self._file.read()

    def _keep(self, days: dict) -> None:
        self._file.write(days)

    async def keep(self) -> None:
        """Until cancelled: a look every quarter of an hour, once the agent's tools started."""
        while True:
            if not self._agent.ready:
                await asyncio.sleep(READY_SECONDS)
                continue
            try:
                await self.catch_up()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"The AI agent's daily debrief could not catch up: {e}")
            await asyncio.sleep(TICK_SECONDS)

    async def catch_up(self, now: float | None = None) -> dict | None:
        """The day's debrief asked once the market is closed, and handed to be told; what is kept of the day."""
        settings = self._agent.settings
        if not settings.debrief_enabled:
            return None
        now = self._clock() if now is None else now
        config = await run_in_threadpool(self._config)
        if self._market_is_open(config, now):
            return None
        day = debriefed_day(config, now).isoformat()
        days = await run_in_threadpool(self.days)
        entry = days.get(day)
        if entry is None:
            entry = await self._write(day, now)
            if entry is None:
                return None
            days[day] = entry
            await run_in_threadpool(self._keep, days)
        if entry.get("conversation") and not entry.get("said"):
            # Written before the teller told debriefs, or just now: told once it ends (nothing while that is off).
            if self._agent.settings.debrief_telegram and self._teller is not None:
                await run_in_threadpool(self._teller.add, entry["conversation"], DEBRIEF, request=0, label=day)
            entry["said"] = True
            await run_in_threadpool(self._keep, {**days, day: entry})
        return entry

    async def _quiet(self, day: str) -> bool:
        """A day without a signal, a trade or a request to Saxo: nothing to write about."""
        first = date.fromisoformat(day)
        return not (await self._queries.journal_between(first, first) or await self._queries.closed_positions(first, first)
                    or await self._queries.order_attempts(first, first))

    async def _write(self, day: str, now: float) -> dict | None:
        settings = self._agent.settings
        try:
            quiet = await self._quiet(day)
        except Exception as e:
            logger.info(f"The daily debrief of {day} waits for the database: {e}")
            return None
        if quiet:
            return {"at": stamp(now), "conversation": None, "quiet": True}
        try:
            meta = await self._agent.create(
                {"kind": "day", "id": day}, DEBRIEF_PROMPT, "debrief", settings.debrief_model, settings.debrief_effort,
                debrief={"day": day, "max_usd": settings.debrief_max_usd})
        except NotPossible as e:
            # Paused, without a key, the month spent: asked again at the next tick.
            logger.info(f"The daily debrief of {day} waits: {e}")
            return None
        return {"at": stamp(now), "conversation": meta["id"], "said": False}
