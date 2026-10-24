"""
Paper trading of the signal rules in shadow: each finished day, the signals
a rule raised that day (``ta_signal``, journaled by the TA engine) traded by
a one-day run, as WATA would have traded them. Set beside the daily range of
a run the operator chose as the rule's reference (on its page), they say
whether the rule does live what its backtest said it would.

A task of the admin application. It starts runs only while the market is
closed by the live rules, for the last few finished days after the rule was
made, one run per rule and day (the service's index keeps which), and lets
the oldest go. The runs are the service's own: same folders, same command,
at the lowest priority, one at a time.
"""

import asyncio
import json
import logging
import time
from datetime import date, datetime, timedelta, timezone

from src.admin.downloads import trading_hours, trading_timezone
from src.admin.errors import NotPossible
from src.backtest.clock import TradingClock

logger = logging.getLogger(__name__)

TICK_SECONDS = 900
# The finished days a rule is caught up on, and those its paper runs are kept for.
CATCH_UP_DAYS = 5
KEEP_DAYS = 60
MAX_SIGNALS = 5000


class PaperTrading:
    def __init__(self, *, backtests, queries, rules, config_path: str, clock=time.time):
        self._backtests = backtests
        self._queries = queries
        # The signal rules (src/admin/ta_rules.py RuleStore), read again at each tick.
        self._rules = rules
        self._config_path = config_path
        self._clock = clock

    def _config(self) -> dict:
        """Read again at each tick: the trading hours may have been edited."""
        with open(self._config_path, encoding="utf-8") as config_file:
            return json.load(config_file)

    def _market_is_open(self, now: float) -> bool:
        zone, start_hour, end_hour = trading_hours(self._config())
        local = datetime.fromtimestamp(now, timezone.utc).astimezone(TradingClock(zone).zone)
        return local.weekday() < 5 and start_hour <= local.hour < end_hour

    def due(self, now: float | None = None) -> list[tuple[dict, date]]:
        """The rules in shadow and the finished days of theirs not traded on paper yet."""
        now = self._clock() if now is None else now
        clock = TradingClock(trading_timezone(self._config()))
        today = datetime.fromtimestamp(now, timezone.utc).astimezone(clock.zone).date()
        index = self._backtests.paper_index()
        found = []
        for rule in self._rules.load()["rules"]:
            if rule.get("mode") != "shadow":
                continue
            created = rule.get("created_at")
            made = datetime.fromisoformat(str(created).replace("Z", "+00:00")).astimezone(clock.zone).date() if created else None
            for back in range(CATCH_UP_DAYS, 0, -1):
                day = today - timedelta(days=back)
                if day.weekday() >= 5 or (made is not None and day < made) or day.isoformat() in index.get(rule["id"], {}):
                    continue
                found.append((rule, day))
        return found

    async def catch_up(self, now: float | None = None) -> int:
        """The paper runs that are due, asked for; how many."""
        now = self._clock() if now is None else now
        if self._market_is_open(now):
            return 0
        asked = 0
        for rule, day in self.due(now):
            rows = await self._queries.ta_signals(day, day, rule_id=rule["id"], limit=MAX_SIGNALS)
            body = {
                "from": day.isoformat(), "to": day.isoformat(), "indice": rule["indice"],
                "name": f"Paper: {rule['name']}, {day.isoformat()}", "tags": ["paper"],
                "source": {"kind": "ta_signals", "rule": {"id": rule["id"], "name": rule["name"]}},
                "chart": {"asset": rule["asset"], "side": rule.get("side", "bid"), "bars": rule["bars"]},
                "baseline": {"repetitions": 0, "seed": 1},
                "paper": {"rule": rule["id"], "day": day.isoformat()},
            }
            try:
                self._backtests.create(body, journal=rows)
            except NotPossible as e:
                # The queue is full: the rest waits for the next tick.
                logger.info(f"Paper trading waits: {e}")
                break
            asked += 1
        today = datetime.fromtimestamp(now, timezone.utc).astimezone(TradingClock(trading_timezone(self._config())).zone).date()
        self._backtests.prune_paper(KEEP_DAYS, today)
        return asked

    async def keep(self) -> None:
        """Until cancelled: catch up every quarter of an hour."""
        while True:
            try:
                await self.catch_up()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"Paper trading of the signal rules could not catch up: {e}")
            await asyncio.sleep(TICK_SECONDS)
