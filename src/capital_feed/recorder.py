"""
The Capital.com feed: the quotes of the stream become ten-second bars in
PostgreSQL, each quote goes out at once to the live chart, Capital.com's own
minutes are kept beside the bars, and each finished day becomes a file for
backtests.

It works as the price recorder does (src/price_recorder/recorder.py), whose
code it leaves as it is:
- the stream callback only works in memory: anything slow there would hold up
  the reading of the socket, and the live chart with it;
- a writer wakes at each ten-second boundary, takes the buckets that ended and
  writes them; while the database does not answer they wait, up to an hour;
- a feed that stops must be heard: after a while without a bar while a market
  is open, Telegram is told, and again once it records again.

Bars are bucketed by the time a quote was received, as the price recorder's
are: what is recorded is what a strategy reading the stream would have seen.
Capital.com's own time of a quote only measures how late it arrived.
"""

import asyncio
import logging
import time
from collections import deque
from datetime import date, datetime, timedelta, timezone
from typing import Awaitable, Callable

from src.capital_feed.client import CapitalError, LoginRefused, RateLimited, market_status
from src.capital_feed.hours import OpeningHours, parse_opening_hours
from src.capital_feed.live import LiveBar
from src.capital_feed.settings import CapitalSettings
from src.price_recorder.bars import BAR_SECONDS, Bar, BarBuilder, ClosedBucket, Quote, bucket_start

logger = logging.getLogger(__name__)

# Buckets kept in memory while the database is unavailable: one hour.
PENDING_BUCKETS_LIMIT = 3600 // BAR_SECONDS
BUCKETS_PER_FLUSH = 60
LOST_BUCKETS_LOG_EVERY = 600 // BAR_SECONDS
DB_TIMEOUT_SECONDS = 8.0
NOTIFY_TIMEOUT_SECONDS = 5.0
REST_TIMEOUT_SECONDS = 15.0
# Bars are being written again once one is this recent.
RESUMED_WITHIN_SECONDS = 3 * BAR_SECONDS
# How often the state of each market is asked for, and its description again.
MARKETS_EVERY_SECONDS = 60.0
DETAILS_EVERY_SECONDS = 6 * 3600.0
# A market state older than this is not believed: the opening hours decide.
STATUS_VALID_SECONDS = 300.0
# Capital.com's minutes: asked for each minute, the last ones again until they settle.
MINUTES_EVERY_SECONDS = 60.0
MINUTES_LOOKED_BACK = 10
# Upkeep of the tables (partitions, retention) and of the day files.
UPKEEP_EVERY_SECONDS = 3600.0
# A day is written to a file once everything before this long after its end is in the database.
DAY_FILE_MARGIN_SECONDS = 120.0
# The figures of the quotes (rate, delays) are of the last minute.
FIGURES_WINDOW_SECONDS = 60.0
OPEN = "TRADEABLE"


def _iso(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _percentile(sorted_values: list[float], share: float) -> float | None:
    if not sorted_values:
        return None
    index = min(len(sorted_values) - 1, max(0, round(share * (len(sorted_values) - 1))))
    return sorted_values[index]


def _decimals(details: dict) -> int | None:
    snapshot = details.get("snapshot") or {}
    value = snapshot.get("decimalPlacesFactor")
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 10 else None


class CapitalRecorder:
    def __init__(
        self,
        *,
        settings: CapitalSettings,
        store,
        rest,
        publisher,
        notify: Callable[[str], Awaitable[None]] | None = None,
        day_files=None,
        clock: Callable[[], float] = time.time,
        version: str = "unknown",
        secrets: tuple[str, ...] = (),
    ):
        self._settings = settings
        self._store = store
        self._rest = rest
        self._publisher = publisher
        self._notify = notify
        self._day_files = day_files
        self._clock = clock
        self._version = version
        self._secrets = tuple(secret for secret in secrets if secret)
        self.stream = None

        self._builder = BarBuilder()
        self._live = {epic: LiveBar(epic) for epic in settings.epics}
        self._ids: dict[str, int] = {}
        self._names: dict[str, str | None] = {}
        self._hours: dict[str, OpeningHours | None] = {}
        self._status: dict[str, tuple[str, float]] = {}
        self._described: set[str] = set()
        self._details_at: float | None = None
        self._pending: deque[ClosedBucket] = deque()
        self._stopping = asyncio.Event()

        self._started_at = clock()
        self._last_bar_written_at: float | None = None
        self._expected_since: float | None = None
        self._alerting = False
        self._refusal_told: str | None = None
        self._db_failing = False
        self._last_db_error: str | None = None
        self._dropped_buckets = 0
        self._retention_done_on: date | None = None

        self._received: deque[float] = deque()
        self._delays: deque[tuple[float, float]] = deque()
        self._last_quote_at: dict[str, float] = {}
        self._minutes_at: float | None = None
        self._minutes_error: str | None = None
        self._rest_error: str | None = None

    # ── stream ──

    def on_quote(self, epic: str, bid: float, ask: float, quoted_ms: int | None, received_at: float) -> None:
        """Stream callback, run in the loop that reads the socket: memory only."""
        live = self._live.get(epic)
        if live is None:
            return
        quote = Quote(bid=bid, ask=ask)
        self._builder.update(epic, quote, received_at)
        bar = live.update(quote, received_at)
        self._received.append(received_at)
        if quoted_ms is not None:
            self._delays.append((received_at, received_at * 1000 - quoted_ms))
        self._last_quote_at[epic] = received_at
        self._publisher.quote(epic, bid=bid, ask=ask, quoted_ms=quoted_ms, received_at=received_at, bar=bar)

    # ── markets ──

    def market_open(self, epic: str, now: float | None = None) -> bool | None:
        """
        Whether the market of ``epic`` is open: what Capital.com said of it in
        the last minutes, else its opening hours; None when neither is known.
        """
        now = self._clock() if now is None else now
        known = self._status.get(epic)
        if known is not None and now - known[1] <= STATUS_VALID_SECONDS:
            return known[0] == OPEN
        hours = self._hours.get(epic)
        if hours is not None:
            return hours.is_open(datetime.fromtimestamp(now, timezone.utc))
        return None

    def expects_quotes(self) -> bool:
        """Whether a market is open, or might be: silence then means the stream is broken."""
        now = self._clock()
        return any(self.market_open(epic, now) is not False for epic in self._settings.epics)

    async def _describe(self, epic: str) -> None:
        """Ask for the instrument's description and record it (and learn its id)."""
        details = await asyncio.wait_for(self._rest.market(epic), REST_TIMEOUT_SECONDS)
        instrument = details.get("instrument") or {}
        opening_hours = instrument.get("openingHours")
        self._hours[epic] = parse_opening_hours(opening_hours)
        name = instrument.get("name") if isinstance(instrument.get("name"), str) else None
        self._names[epic] = name
        status = market_status(details)
        if status is not None:
            self._set_status(epic, status, self._clock())
        record = {
            "epic": epic,
            "label": self._settings.label_of(epic),
            "name": name,
            "instrument_type": instrument.get("type") if isinstance(instrument.get("type"), str) else None,
            "currency": instrument.get("currency") if isinstance(instrument.get("currency"), str) else None,
            "decimals": _decimals(details),
            "opening_hours": opening_hours if isinstance(opening_hours, dict) else None,
        }
        self._ids[epic] = await asyncio.wait_for(
            self._store.upsert_instrument(record, datetime.fromtimestamp(self._clock(), timezone.utc)), DB_TIMEOUT_SECONDS,
        )
        self._described.add(epic)

    def _set_status(self, epic: str, status: str, at: float) -> None:
        before = self._status.get(epic)
        self._status[epic] = (status, at)
        was_open = before is not None and before[0] == OPEN
        if status != OPEN and (before is None or was_open):
            # Closed: no more bars of it until it quotes again; the bar in progress keeps what it had.
            self._builder.update(epic, None, at)
            self._live[epic].update(None, at)
        if before is None or before[0] != status:
            logger.info("Capital.com market of %s: %s.", epic, status)
            self._publisher.state(epic, market="open" if status == OPEN else "closed", status=status)

    async def refresh_markets(self) -> None:
        """The state of every market, and the description of those not known yet (or not for six hours)."""
        now = self._clock()
        due = self._details_at is None or now - self._details_at >= DETAILS_EVERY_SECONDS
        described = set()
        for epic in self._settings.epics:
            if epic in self._described and not due:
                continue
            try:
                await self._describe(epic)
                described.add(epic)
            except LoginRefused:
                raise
            except Exception as e:
                logger.warning("The description of %s could not be read: %s", epic, self._scrub(str(e) or type(e).__name__))
        if due and described == set(self._settings.epics):
            self._details_at = now
        markets = await asyncio.wait_for(self._rest.markets(self._settings.epics), REST_TIMEOUT_SECONDS)
        at = self._clock()
        for epic in self._settings.epics:
            entry = markets.get(epic)
            status = market_status(entry) if entry else None
            if status is not None:
                self._set_status(epic, status, at)

    # ── Capital.com's minutes ──

    async def fetch_minutes(self) -> int:
        """Capital.com's candles of the last minutes, the one in progress left out. Returns how many were written."""
        now = datetime.fromtimestamp(self._clock(), timezone.utc).replace(second=0, microsecond=0)
        start, end = now - timedelta(minutes=MINUTES_LOOKED_BACK), now - timedelta(seconds=1)
        rows = []
        for epic in self._settings.epics:
            instrument_id = self._ids.get(epic)
            if instrument_id is None:
                continue
            candles = await asyncio.wait_for(
                self._rest.prices(epic, start, end, maximum=MINUTES_LOOKED_BACK + 2), REST_TIMEOUT_SECONDS,
            )
            rows += [
                (instrument_id, candle.ts, candle.bid_open, candle.bid_high, candle.bid_low, candle.bid_close,
                 candle.ask_open, candle.ask_high, candle.ask_low, candle.ask_close, candle.volume)
                for candle in candles if candle.ts < now
            ]
        if rows:
            await asyncio.wait_for(self._store.upsert_minutes(rows), DB_TIMEOUT_SECONDS)
        self._minutes_at = self._clock()
        return len(rows)

    # ── writing ──

    def _row(self, bar: Bar) -> tuple:
        return (
            datetime.fromtimestamp(bar.start, timezone.utc), self._ids[bar.key],
            bar.bid_open, bar.bid_high, bar.bid_low, bar.bid_close,
            bar.ask_open, bar.ask_high, bar.ask_low, bar.ask_close,
            min(bar.updates, 32767), min(bar.delayed_minutes, 32767),
        )

    async def _learn_ids(self, epics) -> None:
        """An instrument quoting before its description could be read is recorded under its name alone."""
        for epic in epics:
            self._ids[epic] = await self._store.upsert_instrument(
                {"epic": epic, "label": self._settings.label_of(epic)},
                datetime.fromtimestamp(self._clock(), timezone.utc),
            )

    async def _flush_pending(self) -> None:
        for _ in range(min(len(self._pending), BUCKETS_PER_FLUSH)):
            bucket = self._pending[0]
            missing = {bar.key for bar in bucket.bars if bar.key not in self._ids}
            if missing:
                await self._learn_ids(missing)
            rows = [self._row(bar) for bar in bucket.bars if bar.key in self._ids]
            await self._store.insert_bars(rows)
            await self._store.record_coverage(
                datetime.fromtimestamp(bucket.start, timezone.utc), bucket.feed_ok, bucket.instruments, len(rows),
            )
            self._pending.popleft()
            if rows:
                self._last_bar_written_at = max(self._last_bar_written_at or 0.0, bucket.start + BAR_SECONDS)

    def written_until(self) -> datetime:
        """Every bar before this moment is in the database: the start of the oldest bucket still waiting, or now."""
        if self._pending:
            return datetime.fromtimestamp(self._pending[0].start, timezone.utc)
        return datetime.fromtimestamp(bucket_start(self._clock()), timezone.utc)

    async def write_closed_buckets(self) -> None:
        """Close the buckets that ended, send their bars live, write what is waiting. Never raises."""
        closed = self._builder.close_until(self._clock())
        for bucket in closed:
            for bar in bucket.bars:
                self._publisher.bar(bar.key, bar)
        self._pending.extend(closed)
        overflow = len(self._pending) - PENDING_BUCKETS_LIMIT
        if overflow > 0:
            for _ in range(overflow):
                self._pending.popleft()
            before = self._dropped_buckets
            self._dropped_buckets += overflow
            if before == 0 or before // LOST_BUCKETS_LOG_EVERY != self._dropped_buckets // LOST_BUCKETS_LOG_EVERY:
                logger.error(
                    "The database has been unavailable for too long: bars are being lost (%d bucket(s) since start).",
                    self._dropped_buckets,
                )

        if self._pending:
            try:
                await asyncio.wait_for(self._flush_pending(), DB_TIMEOUT_SECONDS)
            except Exception as e:
                self._last_db_error = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
                if not self._db_failing:
                    logger.warning("Bars could not be written, they are kept for later: %s", self._last_db_error)
                self._db_failing = True
            else:
                if self._db_failing:
                    logger.info("The database answers again: the bars kept meanwhile are being written.")
                self._db_failing = False
                self._last_db_error = None

        await self._write_state()
        await self._check_alert()

    # ── what the feed is doing ──

    def _scrub(self, text: str | None) -> str | None:
        if text is None:
            return None
        for secret in self._secrets:
            text = text.replace(secret, "***")
        return text

    def _prune_figures(self, now: float) -> None:
        while self._received and now - self._received[0] > FIGURES_WINDOW_SECONDS:
            self._received.popleft()
        while self._delays and now - self._delays[0][0] > FIGURES_WINDOW_SECONDS:
            self._delays.popleft()

    def state(self) -> dict:
        """What the feed is doing, as the admin and the logs say it."""
        now = self._clock()
        self._prune_figures(now)
        stream = self.stream
        refused = getattr(self._rest, "last_refusal", None)
        if refused:
            state, detail = "refused", f"Capital.com refused the login ({refused}): check CAPITAL_API_KEY, CAPITAL_IDENTIFIER and CAPITAL_PASSWORD in .env"
        elif self._db_failing:
            state, detail = "database", f"The database does not take the bars ({self._last_db_error}); {len(self._pending)} bucket(s) are waiting"
        elif stream is not None and not stream.connected:
            state = "connecting"
            detail = "Connecting to Capital.com's stream" + (f" (last break: {stream.last_error})" if stream.last_error else "")
        elif not self.expects_quotes():
            state, detail = "market_closed", "Every market followed is closed"
        else:
            state, detail = "streaming", f"Streaming {', '.join(self._settings.epics)}"

        delays = sorted(delay for _, delay in self._delays)
        instruments = []
        for instrument in self._settings.instruments:
            epic = instrument.epic
            hours = self._hours.get(epic)
            status = self._status.get(epic)
            opening = hours.next_opening(datetime.fromtimestamp(now, timezone.utc)) if hours is not None else None
            is_open = self.market_open(epic, now)
            instruments.append({
                "epic": epic,
                "label": instrument.label,
                "name": self._names.get(epic),
                "market": "unknown" if is_open is None else ("open" if is_open else "closed"),
                "status": status[0] if status else None,
                "last_quote_at": _iso(self._last_quote_at.get(epic)),
                "next_opening": opening.strftime("%Y-%m-%dT%H:%M:%SZ") if opening is not None and not is_open else None,
                "subscription": (stream.subscribed.get(epic) if stream is not None else None),
            })
        figures = {
            "instruments": instruments,
            "quotes_per_minute": len(self._received),
            # How late quotes arrived over the last minute: Capital.com's time of the quote to the feed's receipt.
            "delay_ms": {
                "p50": _percentile(delays, 0.5), "p95": _percentile(delays, 0.95),
                "max": delays[-1] if delays else None, "count": len(delays),
            },
            "stream": {
                "connected": bool(stream and stream.connected),
                "since": _iso(stream.connected_since) if stream else None,
                "connections": stream.connections if stream else 0,
                "last_error": self._scrub(stream.last_error) if stream else None,
            },
            "live": {
                "sent": self._publisher.sent, "dropped": self._publisher.dropped,
                "failing": self._publisher.failing, "last_error": self._scrub(self._publisher.last_error),
            },
            "minutes": {"enabled": self._settings.minute_volume, "last": _iso(self._minutes_at), "error": self._scrub(self._minutes_error)},
            "day_files": {
                "enabled": self._settings.day_files,
                "written": self._day_files.written if self._day_files else 0,
                "last": (self._day_files.last_written[1].isoformat() if self._day_files and self._day_files.last_written else None),
            },
            "rest_error": self._scrub(self._rest_error),
            "pending_buckets": len(self._pending),
            "dropped_buckets": self._dropped_buckets,
            "last_bar_at": _iso(self._last_bar_written_at),
        }
        return {
            "updated_at": datetime.fromtimestamp(now, timezone.utc),
            "started_at": datetime.fromtimestamp(self._started_at, timezone.utc),
            "version": self._version,
            "environment": self._settings.environment,
            "state": state,
            "detail": self._scrub(detail),
            "figures": figures,
        }

    async def _write_state(self) -> None:
        if self._db_failing:
            return
        try:
            await asyncio.wait_for(self._store.write_state(self.state()), DB_TIMEOUT_SECONDS)
        except Exception as e:
            logger.debug("The feed's state could not be written: %s", e)

    # ── being heard when it stops ──

    def _why_nothing_is_recorded(self) -> str:
        if self._db_failing:
            return f"the database does not take the bars ({self._last_db_error}); {len(self._pending)} bucket(s) are waiting"
        refused = getattr(self._rest, "last_refusal", None)
        if refused:
            return f"Capital.com refuses the login ({refused})"
        stream = self.stream
        if stream is not None and not stream.connected:
            return "the stream is not connected" + (f" ({self._scrub(stream.last_error)})" if stream.last_error else "")
        return "no quote is arriving from Capital.com"

    async def _check_alert(self) -> None:
        at = self._clock()
        refused = getattr(self._rest, "last_refusal", None)
        if refused and refused != self._refusal_told:
            self._refusal_told = refused
            await self._say(
                f"WATA Capital.com feed: Capital.com refused the login ({refused}). "
                "Check CAPITAL_API_KEY, CAPITAL_IDENTIFIER and CAPITAL_PASSWORD in .env, then restart the feed."
            )
        if not refused:
            self._refusal_told = None

        expected = self.expects_quotes()
        if not expected:
            # A closed market is silent: nothing to count.
            self._expected_since = None
        elif self._expected_since is None:
            self._expected_since = at

        last_bar = self._last_bar_written_at
        if self._alerting:
            if last_bar is not None and at - last_bar <= RESUMED_WITHIN_SECONDS:
                self._alerting = False
                await self._say("WATA Capital.com feed: recording again.")
            return
        if not expected:
            return
        silent_for = at - max(self._expected_since, last_bar or 0.0)
        if silent_for >= self._settings.alert_after_seconds:
            self._alerting = True
            await self._say(
                f"WATA Capital.com feed: nothing recorded for {int(silent_for // 60)} min while the market is open, "
                f"{self._why_nothing_is_recorded()}. These prices cannot be fetched later."
            )

    async def _say(self, message: str) -> None:
        message = self._scrub(message)
        logger.warning(message)
        if self._notify is None:
            return
        try:
            await asyncio.wait_for(self._notify(message), NOTIFY_TIMEOUT_SECONDS)
        except Exception as e:
            logger.warning("The Telegram notification could not be sent: %s", e)

    # ── upkeep ──

    async def upkeep(self) -> None:
        """Partitions for this month and the next, the retention once a day, and the day files that are due."""
        today = datetime.fromtimestamp(self._clock(), timezone.utc).date()
        await asyncio.wait_for(self._store.ensure_partitions(today), DB_TIMEOUT_SECONDS)
        months = self._settings.retention_months
        if months > 0 and self._retention_done_on != today:
            dropped = await asyncio.wait_for(self._store.drop_partitions_older_than(today, months), DB_TIMEOUT_SECONDS)
            self._retention_done_on = today
            if dropped:
                logger.info("Retention of %d month(s): removed %s.", months, ", ".join(dropped))
        if self._day_files is not None and not self._db_failing:
            instruments = [
                (self._ids[instrument.epic], instrument.epic, instrument.label, self._names.get(instrument.epic))
                for instrument in self._settings.instruments if instrument.epic in self._ids
            ]
            safe = self.written_until() - timedelta(seconds=DAY_FILE_MARGIN_SECONDS)
            await self._day_files.write_finished_days(instruments, safe)

    # ── running ──

    async def _sleep_or_stop(self, seconds: float) -> bool:
        try:
            await asyncio.wait_for(self._stopping.wait(), max(seconds, 0.0))
        except asyncio.TimeoutError:
            return False
        return True

    async def _repeat(self, what: str, action, every: float, *, first_delay: float = 0.0) -> None:
        """Run ``action`` every ``every`` seconds until stopped; a failure is logged and tried at the next turn."""
        if first_delay and await self._sleep_or_stop(first_delay):
            return
        while not self._stopping.is_set():
            wait = every
            try:
                await action()
                if what == "markets":
                    self._rest_error = None
                elif what == "minutes":
                    self._minutes_error = None
            except asyncio.CancelledError:
                raise
            except LoginRefused as e:
                # Said by the state and the alert; the stream tries again when Capital.com allows.
                logger.info("%s: %s", what, e)
            except RateLimited as e:
                logger.warning("Capital.com asks to slow down (%s): %s waits longer.", e.code, what)
                wait = every * 3
            except (CapitalError, asyncio.TimeoutError, OSError) as e:
                message = self._scrub(str(e) or type(e).__name__)
                if what == "minutes":
                    self._minutes_error = message
                elif what == "markets":
                    self._rest_error = message
                logger.warning("Capital.com feed, %s: %s", what, message)
            except Exception as e:
                logger.error("Capital.com feed, %s failed: %s", what, self._scrub(str(e)), exc_info=True)
            if await self._sleep_or_stop(wait):
                return

    async def _write_loop(self) -> None:
        while not self._stopping.is_set():
            at = self._clock()
            next_boundary = bucket_start(at) + BAR_SECONDS
            if await self._sleep_or_stop(next_boundary - at + 0.05):
                return
            await self.write_closed_buckets()

    async def run(self) -> None:
        """Record until ``stop()`` is called."""
        try:
            # Before the first bar: one that arrives without its month's partition is kept and tried again.
            await asyncio.wait_for(
                self._store.ensure_partitions(datetime.fromtimestamp(self._clock(), timezone.utc).date()), DB_TIMEOUT_SECONDS,
            )
        except Exception as e:
            logger.warning("The partitions of the bars could not be made yet: %s", e)
        await self._write_state()
        tasks = [
            asyncio.create_task(self._write_loop()),
            asyncio.create_task(self._repeat("markets", self.refresh_markets, MARKETS_EVERY_SECONDS)),
            asyncio.create_task(self._repeat("upkeep", self.upkeep, UPKEEP_EVERY_SECONDS)),
        ]
        if self._settings.minute_volume:
            # A few seconds into each minute, the one before is over at Capital.com.
            first = 60 - self._clock() % 60 + 5
            tasks.append(asyncio.create_task(self._repeat("minutes", self.fetch_minutes, MINUTES_EVERY_SECONDS, first_delay=first)))
        try:
            await self._stopping.wait()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            # The bucket in progress is closed and written with the rest.
            self._pending.extend(self._builder.close_until(self._clock() + BAR_SECONDS))
            try:
                await asyncio.wait_for(self._flush_pending(), DB_TIMEOUT_SECONDS)
            except Exception as e:
                logger.warning("The last bars could not be written: %s", e)

    def stop(self) -> None:
        self._stopping.set()
