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

Each of them also has its report: one run of its days since it began (a rule
since it was made, the signals received since their first day on paper), held
to the longest period a run takes, with every trade and the account carried
from day to day. It is made again after each finished day, and the older one
let go once the newer is done (BacktestService.settle_paper_reports). No
random entries: it is made every night.

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

from src.admin.backtest_requests import ask_backtest, local_day, measured_orders
from src.admin.backtests import LIVE_PAPER, MAX_RAISED_SIGNALS, NO_ENTRY_SIGNAL
from src.admin.downloads import market_is_open, trading_timezone
from src.admin.errors import DatabaseUnavailable, InvalidRequest, NotPossible
from src.admin.live_drift import live_days, warning
from src.admin.processes import QUEUED, RUNNING
from src.backtest.clock import TradingClock
from src.backtest.execution import missing

logger = logging.getLogger(__name__)

TICK_SECONDS = 900
# The finished days a rule is caught up on, and those its paper runs are kept for.
CATCH_UP_DAYS = 5
KEEP_DAYS = 60
# The real days set beside the live paper days and the reference: as many as the paper runs are kept.
REAL_DAYS = KEEP_DAYS
# Where the day the drift was last said for is kept, beside the paper index.
DRIFT_FILE = "drift.json"
# A report waiting its turn or running: nothing more is asked for its owner meanwhile.
UNDER_WAY = (QUEUED, RUNNING)


def index_asset(config: dict, indice: str) -> dict:
    """
    The index an indice's days are drawn on: as the Capital.com feed records it (the TA engine's own), Dukascopy's
    history while the feed is off. The live paper days, and a real position's chart, are drawn on it.
    """
    feed = ((config.get("trade") or {}).get("config") or {}).get("capital_feed") or {}
    epic = next((instrument.get("epic") for instrument in feed.get("instruments") or []
                 if feed.get("enabled") and instrument.get("label") == indice and instrument.get("epic")), None)
    return {"provider": "capital", "key": epic} if epic else {"provider": "dukascopy", "key": indice}


def last_finished_day(today: date) -> date:
    """The newest weekday before today: the last day a report goes to."""
    day = today - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def rule_body(rule: dict, first: date, last: date, *, report: bool) -> dict:
    """A run of the signals a rule raised: one day of them on paper, or its report since it began."""
    return {
        "from": first.isoformat(), "to": last.isoformat(), "indice": rule["indice"],
        "name": f"Paper: {rule['name']}, {'since ' if report else ''}{first.isoformat()}", "tags": ["paper"],
        "source": {"kind": "ta_signals", "rule": {"id": rule["id"], "name": rule["name"]}},
        "chart": {"asset": rule["asset"], "side": rule.get("side", "bid"), "bars": rule["bars"]},
        "baseline": {"repetitions": 0, "seed": 1},
        "paper": {"rule": rule["id"], "report": True} if report else {"rule": rule["id"], "day": first.isoformat()},
    }


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
        # The owners whose report is being asked (its signals read meanwhile): never asked twice at once.
        self._asking: set[str] = set()

    def _config(self) -> dict:
        """Read again at each tick: the trading hours may have been edited."""
        with open(self._config_path, encoding="utf-8") as config_file:
            return json.load(config_file)

    def _market_is_open(self, now: float) -> bool:
        return market_is_open(self._config(), now)

    def _zone(self):
        return TradingClock(trading_timezone(self._config())).zone

    def _today(self, now: float | None = None) -> date:
        now = self._clock() if now is None else now
        return datetime.fromtimestamp(now, timezone.utc).astimezone(self._zone()).date()

    @staticmethod
    def _made(rule: dict, zone) -> date | None:
        """The day a rule was made, in the trading timezone."""
        created = rule.get("created_at")
        return datetime.fromisoformat(str(created).replace("Z", "+00:00")).astimezone(zone).date() if created else None

    def due(self, now: float | None = None) -> list[tuple[dict, date]]:
        """The rules in shadow and the finished days of theirs not traded on paper yet."""
        zone = self._zone()
        today = self._today(now)
        index = self._backtests.paper_index()
        found = []
        for rule in self._rules.load()["rules"]:
            if rule.get("mode") != "shadow":
                continue
            made = self._made(rule, zone)
            for back in range(CATCH_UP_DAYS, 0, -1):
                day = today - timedelta(days=back)
                if day.weekday() >= 5 or (made is not None and day < made) or day.isoformat() in index.get(rule["id"], {}):
                    continue
                found.append((rule, day))
        return found

    def due_live(self, now: float | None = None) -> list[date]:
        """The finished weekdays whose signals were not traded on paper under the live settings yet."""
        today = self._today(now)
        done = self._backtests.paper_index().get(LIVE_PAPER, {})
        days = (today - timedelta(days=back) for back in range(CATCH_UP_DAYS, 0, -1))
        return [day for day in days if day.weekday() < 5 and day.isoformat() not in done]

    async def _live_body(self, first: date, last: date, *, report: bool) -> dict | None:
        """A run of the signals WATA received under the live settings: one day of them, or their report."""
        indices = self._backtests.live_settings(None).allowed_indices
        if not indices:
            return None
        execution = await measured_orders(self._queries, self._backtests)
        return {
            "from": first.isoformat(), "to": last.isoformat(), "indice": indices[0],
            "name": f"Paper: the signals received, {'since ' if report else ''}{first.isoformat()}", "tags": ["paper"],
            "source": {"kind": "journal"},
            "chart": {"asset": index_asset(self._config(), indices[0]), "side": "bid", "bars": {"type": "time", "timeframe": "1"}},
            # WATA's own fills when enough orders were measured, the settings' fixed ones otherwise.
            "changes": {} if missing(execution.get("samples")) else {"execution": "measured"},
            "baseline": {"repetitions": 0, "seed": 1},
            "paper": {"rule": LIVE_PAPER, "report": True} if report else {"rule": LIVE_PAPER, "day": first.isoformat()},
        }

    async def _live_paper(self, day: date) -> bool:
        """
        One day of the signals WATA received, traded on paper under the live settings: whether a run was asked
        (a day without an entry signal is kept as such). NotPossible: the queue is full, the rest waits.
        """
        body = await self._live_body(day, day, report=False)
        if body is None:
            return False
        try:
            await ask_backtest(self._queries, self._backtests, body)
        except NotPossible as e:
            if str(e) != NO_ENTRY_SIGNAL:
                raise
            self._backtests.mark_live_paper_without_entry(day.isoformat())
            return False
        return True

    # ── the reports ──

    def _first_day(self, began: date | None, last: date) -> date:
        """A report's first day: the day its owner began, held to the longest period a run takes."""
        earliest = last - timedelta(days=self._backtests.max_period_days - 1)
        return earliest if began is None else max(began, earliest)

    def _live_since(self) -> str | None:
        """The signals received's first day on paper: kept once a report was asked, the index forgetting its days."""
        return self._backtests.paper_since(LIVE_PAPER) or min(self._backtests.paper_index().get(LIVE_PAPER) or {}, default=None)

    def _report_owners(self, today: date) -> list[tuple[str, dict | None, date, date]]:
        """Whose report is kept (the signals received once they have a day on paper, each rule in shadow), and its period."""
        last = last_finished_day(today)
        owners: list[tuple[str, dict | None, date, date]] = []
        since = self._live_since()
        if since is not None:
            owners.append((LIVE_PAPER, None, self._first_day(date.fromisoformat(since), last), last))
        zone = self._zone()
        for rule in self._rules.load()["rules"]:
            if rule.get("mode") == "shadow":
                owners.append((rule["id"], rule, self._first_day(self._made(rule, zone), last), last))
        return [owner for owner in owners if owner[2] <= owner[3]]

    def _under_way(self, owner: str) -> bool:
        newest = self._backtests.newest_paper_report(owner)
        return owner in self._asking or (newest is not None and newest["state"] in UNDER_WAY)

    def reports_due(self, now: float | None = None) -> list[tuple[str, dict | None, date, date]]:
        """
        The reports to make again: each owner's whose newest does not go to the last finished day yet, and is
        not waiting or running. One that failed for that day is not asked again before the next.
        """
        found = []
        for owner, rule, first, last in self._report_owners(self._today(now)):
            newest = self._backtests.newest_paper_report(owner)
            if self._under_way(owner) or self._backtests.nothing_to_report(owner) == last.isoformat():
                continue
            if newest is None or newest["to"] != last.isoformat():
                found.append((owner, rule, first, last))
        return found

    async def _ask_report(self, owner: str, rule: dict | None, first: date, last: date) -> bool:
        """
        An owner's report asked for: whether it was (not when one is under way, nor without an entry signal
        received). NotPossible: the queue is full. InvalidRequest: the backtests cannot trade it.
        """
        if self._under_way(owner):
            return False
        self._asking.add(owner)
        try:
            if owner == LIVE_PAPER:
                body = await self._live_body(first, last, report=True)
                if body is None:
                    return False
                if self._backtests.paper_since(LIVE_PAPER) is None:
                    self._backtests.keep_paper_since(LIVE_PAPER, self._live_since())
                try:
                    await ask_backtest(self._queries, self._backtests, body)
                except NotPossible as e:
                    if str(e) != NO_ENTRY_SIGNAL:
                        raise
                    # Not asked again before the next finished day.
                    self._backtests.mark_nothing_to_report(LIVE_PAPER, last.isoformat())
                    return False
                return True
            rows = await self._queries.rule_signals(rule["id"], first, last, limit=MAX_RAISED_SIGNALS)
            if len(rows) >= MAX_RAISED_SIGNALS:
                # More than a run replays: the report starts at the first whole day of the newest, and stays current.
                zone = self._zone()
                first = date.fromisoformat(local_day(rows[0]["raised_at"], zone)) + timedelta(days=1)
                if first > last:
                    raise InvalidRequest(f"{rule['name']!r} raised more than {MAX_RAISED_SIGNALS:,} signals in a day")
                rows = [row for row in rows if local_day(row["raised_at"], zone) >= first.isoformat()]
            self._backtests.create(rule_body(rule, first, last, report=True), journal=rows)
            return True
        finally:
            self._asking.discard(owner)

    async def ask_report(self, owner: str) -> None:
        """
        A report made now rather than after the next finished day: of a rule in shadow, or of the signals WATA
        received once they have a day on paper. At any hour: the operator's own run, in line with the others.
        """
        if owner == LIVE_PAPER:
            since = self._live_since()
            if since is None:
                raise NotPossible("The signals WATA received have no day on paper yet")
            rule, began = None, date.fromisoformat(since)
        else:
            rule = self._rules.rule(owner)
            if rule.get("mode") != "shadow":
                raise NotPossible(f"{rule['name']!r} is not in shadow: its days are not traded on paper")
            began = self._made(rule, self._zone())
        last = last_finished_day(self._today())
        first = self._first_day(began, last)
        if first > last:
            raise NotPossible("No finished weekday yet: the first report comes after the first one")
        if self._under_way(owner):
            raise NotPossible("Its report is being made already")
        if not await self._ask_report(owner, rule, first, last):
            raise NotPossible(f"No signal of entry since {first.isoformat()}: nothing to make a report of")

    # ── the drift of the live settings ──

    async def drift(self) -> tuple[list[dict], str | None, dict | None]:
        """The live days against the live reference, and what is to be said of them; nothing without a reference."""
        live = (self._backtests.paper(with_reports=False).get("live") or {})
        reference = live.get("reference")
        if not reference:
            return [], None, None
        today = self._today()
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
        """The paper runs that are due, asked for, then the reports; how many."""
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
            rows = await self._queries.rule_signals(rule["id"], day, day, limit=MAX_RAISED_SIGNALS)
            try:
                self._backtests.create(rule_body(rule, day, day, report=False), journal=rows)
            except NotPossible as e:
                # The queue is full: the rest waits for the next tick.
                logger.info(f"Paper trading waits: {e}")
                break
            except InvalidRequest as e:
                # A rule the backtests cannot trade (an indice the trader does not take): the others go on.
                logger.warning(f"{rule['name']!r} cannot be traded on paper: {e}")
                continue
            asked += 1
        # The reports once the days are asked, the older let go first.
        self._backtests.settle_paper_reports()
        for owner, rule, first, last in self.reports_due(now):
            try:
                asked += await self._ask_report(owner, rule, first, last)
            except NotPossible as e:
                logger.info(f"Paper reports wait: {e}")
                break
            except InvalidRequest as e:
                logger.warning(f"The paper report of {repr(rule['name']) if rule else 'the signals received'} cannot be made: {e}")
        self._backtests.prune_paper(KEEP_DAYS, self._today(now))
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
