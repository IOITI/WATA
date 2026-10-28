"""
The AI agent's daily debrief (``admin.ai_agent.debrief_*``, off unless
debrief_enabled): each trading day, once the market is closed by the live
rules, a conversation about the day (a day subject of src/admin/agent/service.py:
its trades, its signals, what Saxo refused, its paper twin), written by the
debrief's model within its own cost and without a backtest, kept on AI agent ›
Activity; once written, its first lines said on Telegram (debrief_telegram). A
day without a trade, a signal or a request to Saxo is noted in a line, without
a call to the API.

It asks what the operator could ask: the agent's pause, its key and the month's
budget hold it back as they hold the operator's requests, and it tries again at
its next tick. It lives outside src/admin/agent/, which never reaches the
notifier (tests/test_agent_boundary.py), and keeps what it did in its own file
beside the conversations (``debriefs.json``).
"""

import asyncio
import json
import logging
import os
import re
import time
from datetime import date, datetime

import pytz
from starlette.concurrency import run_in_threadpool

from src.admin.agent.prompts import DEBRIEF_PROMPT
from src.admin.agent.store import UnknownConversation, stamp
from src.admin.agent.transcript import UNDER_WAY
from src.admin.downloads import market_is_open, trading_hours
from src.admin.errors import NotPossible
from src.admin.paper import last_finished_day

logger = logging.getLogger(__name__)

TICK_SECONDS = 15 * 60
# While the agent's tools start with the admin, or a debrief is being written: looked at again this often.
READY_SECONDS = 10
WRITING_SECONDS = 60
FILE = "debriefs.json"
# The days its file keeps, the newest.
KEPT_DAYS = 120
# What Telegram is given of a debrief: its first lines, at most.
SAID_CHARS = 700
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MARKS = re.compile(r"(\*\*|__|`)")


def debriefed_day(config: dict, now: float) -> date:
    """The newest weekday whose trading hours are over at ``now``: today once its end hour passed, else the one before."""
    zone, _, end_hour = trading_hours(config)
    local = datetime.fromtimestamp(now, pytz.timezone(zone))
    if local.weekday() < 5 and local.hour >= end_hour:
        return local.date()
    return last_finished_day(local.date())


def said_of(text: str) -> str:
    """A debrief's first lines for Telegram: its links as their words, its marks left out, cut at a line's end."""
    plain = _MARKS.sub("", _LINK.sub(r"\1", text)).strip()
    if len(plain) <= SAID_CHARS:
        return plain
    cut = plain[:SAID_CHARS]
    end = max(cut.rfind("\n"), cut.rfind(". "))
    return (cut[:end + 1] if end > SAID_CHARS // 2 else cut).rstrip() + " …"


class DailyDebrief:
    def __init__(self, *, agent, queries, config_path: str, path: str, clock=time.time, notify=None):
        self._agent = agent
        # The admin's named queries: whether the day has anything to say.
        self._queries = queries
        self._config_path = config_path
        self._path = path
        self._clock = clock
        # Telegram, through the admin's notifier.
        self._notify = notify

    def _config(self) -> dict:
        """Read again at each tick: the trading hours may have been edited."""
        with open(self._config_path, encoding="utf-8") as config_file:
            return json.load(config_file)

    def _market_is_open(self, config: dict, now: float) -> bool:
        return market_is_open(config, now)

    def days(self) -> dict:
        """Each day debriefed: when, its conversation (none for a quiet day), whether it was said on Telegram."""
        try:
            with open(self._path, encoding="utf-8") as kept:
                return json.load(kept).get("days") or {}
        except (OSError, ValueError):
            return {}

    def _keep(self, days: dict) -> None:
        newest = dict(sorted(days.items())[-KEPT_DAYS:])
        partial = f"{self._path}.part"
        with open(partial, "w", encoding="utf-8") as out_file:
            json.dump({"days": newest}, out_file)
        os.replace(partial, self._path)

    async def keep(self) -> None:
        """Until cancelled: a look every quarter of an hour, once the agent's tools started."""
        while True:
            if not self._agent.ready:
                await asyncio.sleep(READY_SECONDS)
                continue
            entry = None
            try:
                entry = await self.catch_up()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"The AI agent's daily debrief could not catch up: {e}")
            # One being written is said soon after it ends.
            writing = bool(entry and entry.get("conversation") and not entry.get("said"))
            await asyncio.sleep(WRITING_SECONDS if writing else TICK_SECONDS)

    async def catch_up(self, now: float | None = None) -> dict | None:
        """The day's debrief asked once the market is closed, then said once written; what is kept of the day."""
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
        if entry.get("conversation") and not entry.get("said") and await self._say(day, entry):
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

    async def _say(self, day: str, entry: dict) -> bool:
        """Its first lines on Telegram once its request ended (nothing when that is off); True once settled."""
        agent = self._agent
        try:
            said = await run_in_threadpool(agent.conversation, entry["conversation"])
        except UnknownConversation:
            return True
        if said.get("status") in UNDER_WAY:
            return False
        if not agent.settings.debrief_telegram or self._notify is None:
            return True
        answer = ((said.get("requests") or [{}])[0].get("answer") or {}).get("text")
        when = date.fromisoformat(day).strftime("%a %d %b")
        if answer:
            message = f"📝 WATA's debrief of {when}:\n\n{said_of(answer)}\n\nThe whole of it: AI agent › Activity."
        else:
            message = (f"📝 WATA's debrief of {when} could not be written: {said.get('error') or 'it ended without an answer'}. "
                       "AI agent › Activity.")
        try:
            await self._notify(message)
        except Exception as e:
            logger.warning(f"The daily debrief of {day} could not be said on Telegram: {e}")
            return False
        return True
