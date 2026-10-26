"""
Paper trading of the signal rules in shadow: each finished day, the signals
a rule raised that day (``ta_signal``, journaled by the TA engine) traded by
a one-day run, as WATA would have traded them. Set beside the daily range of
a run the operator chose as the rule's reference (on its page), they say
whether the rule does live what its backtest said it would.

The signals WATA receives (TradingView's alerts, the journal) are traded on
paper the same way, under the live settings and with WATA's measured fills
(fixed fills while too few orders were measured): while entries are paused,
the day the trader would have had; once trading again, the day beside the
real one. Set beside a replay of the journal chosen as the live reference,
two days in a row outside its range are said once on Telegram, and in health
(src/admin/live_drift.py).

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

from src.admin.backtest_requests import ask_backtest, measured_orders
from src.admin.backtests import LIVE_PAPER, NO_ENTRY_SIGNAL
from src.admin.downloads import market_is_open, trading_timezone
from src.admin.errors import DatabaseUnavailable, NotPossible
from src.admin.live_drift import live_days, warning
from src.backtest.clock import TradingClock
from src.backtest.execution import missing

logger = logging.getLogger(__name__)

TICK_SECONDS = 900
# The finished days a rule is caught up on, and those its paper runs are kept for.
CATCH_UP_DAYS = 5
KEEP_DAYS = 60
MAX_SIGNALS = 5000
# The real days set beside the live paper days and the reference: as many as the paper runs are kept.
REAL_DAYS = KEEP_DAYS
# Where the day the drift was last said for is kept, beside the paper index.
DRIFT_FILE = "drift.json"


def index_asset(config: dict, indice: str) -> dict:
    """
    The index an indice's days are drawn on: as the Capital.com feed records it (the TA engine's own), Dukascopy's
    history while the feed is off. The live paper days, and a real position's chart, are drawn on it.
    """
    feed = ((config.get("trade") or {}).get("config") or {}).get("capital_feed") or {}
    epic = next((instrument.get("epic") for instrument in feed.get("instruments") or []
                 if feed.get("enabled") and instrument.get("label") == indice and instrument.get("epic")), None)
    return {"provider": "capital", "key": epic} if epic else {"provider": "dukascopy", "key": indice}


class PaperTrading:
    def __init__(self, *, backtests, queries, rules, config_path: str, clock=time.time, notify=None):
        self._backtests = backtests
        self._queries = queries
        # The signal rules (src/admin/ta_rules.py RuleStore), read again at each tick.
        self._rules = rules
        self._config_path = config_path
        self._clock = clock
        # How the drift of the live settings is said (Telegram, through the admin's notifier).
        self._notify = notify

    def _config(self) -> dict:
        """Read again at each tick: the trading hours may have been edited."""
        with open(self._config_path, encoding="utf-8") as config_file:
            return json.load(config_file)

    def _market_is_open(self, now: float) -> bool:
        return market_is_open(self._config(), now)

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

    def due_live(self, now: float | None = None) -> list[date]:
        """The finished weekdays whose signals were not traded on paper under the live settings yet."""
        now = self._clock() if now is None else now
        clock = TradingClock(trading_timezone(self._config()))
        today = datetime.fromtimestamp(now, timezone.utc).astimezone(clock.zone).date()
        done = self._backtests.paper_index().get(LIVE_PAPER, {})
        days = (today - timedelta(days=back) for back in range(CATCH_UP_DAYS, 0, -1))
        return [day for day in days if day.weekday() < 5 and day.isoformat() not in done]

    async def _live_paper(self, day: date) -> bool:
        """
        One day of the signals WATA received, traded on paper under the live settings: whether a run was asked
        (a day without an entry signal is kept as such). NotPossible: the queue is full, the rest waits.
        """
        indices = self._backtests.live_settings(None).allowed_indices
        if not indices:
            return False
        execution = await measured_orders(self._queries, self._backtests)
        body = {
            "from": day.isoformat(), "to": day.isoformat(), "indice": indices[0],
            "name": f"Paper: the signals received, {day.isoformat()}", "tags": ["paper"],
            "source": {"kind": "journal"},
            "chart": {"asset": index_asset(self._config(), indices[0]), "side": "bid", "bars": {"type": "time", "timeframe": "1"}},
            # WATA's own fills when enough orders were measured, the settings' fixed ones otherwise.
            "changes": {} if missing(execution.get("samples")) else {"execution": "measured"},
            "baseline": {"repetitions": 0, "seed": 1},
            "paper": {"rule": LIVE_PAPER, "day": day.isoformat()},
        }
        try:
            await ask_backtest(self._queries, self._backtests, body)
        except NotPossible as e:
            if str(e) != NO_ENTRY_SIGNAL:
                raise
            self._backtests.mark_live_paper_without_entry(day.isoformat())
            return False
        return True

    async def drift(self) -> tuple[list[dict], str | None, dict | None]:
        """The live days against the live reference, and what is to be said of them; nothing without a reference."""
        live = (self._backtests.paper().get("live") or {})
        reference = live.get("reference")
        if not reference:
            return [], None, None
        today = datetime.fromtimestamp(self._clock(), timezone.utc).astimezone(
            TradingClock(trading_timezone(self._config())).zone).date()
        try:
            daily = (await self._queries.performance(start=today - timedelta(days=REAL_DAYS)))["daily"]
        except DatabaseUnavailable:
            daily = []
        real = {str(row["day_date"])[:10]: row["performance_percent"] for row in daily
                if row.get("trade_count") and row.get("performance_percent") is not None}
        days = live_days(live.get("days") or [], real, reference)
        return days, warning(days, reference), reference

    async def _say_drift(self) -> None:
        """The drift said once on Telegram, for the newest of the days it rests on."""
        if self._notify is None:
            return
        days, said, _ = await self.drift()
        if said is None:
            return
        newest = next(day["day"] for day in days if day["real_outside"] is not None or day["paper_outside"] is not None)
        if (self._backtests.paper_file(DRIFT_FILE) or {}).get("said_for") == newest:
            return
        await self._notify(f"⚠️ WATA: {said}. The Performance page sets each day beside the reference.")
        self._backtests.write_paper_file(DRIFT_FILE, {"said_for": newest})

    async def catch_up(self, now: float | None = None) -> int:
        """The paper runs that are due, asked for; how many."""
        now = self._clock() if now is None else now
        if self._market_is_open(now):
            return 0
        asked = 0
        for day in self.due_live(now):
            try:
                asked += await self._live_paper(day)
            except NotPossible as e:
                logger.info(f"Paper trading of the signals received waits: {e}")
                break
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
        try:
            await self._say_drift()
        except Exception as e:
            logger.warning(f"The live settings' drift could not be said: {e}")
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
