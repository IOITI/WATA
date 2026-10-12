"""
The price recorder: the quotes Saxo streams become ten-second bars in
PostgreSQL.

Two things run side by side and never wait for each other. The stream
callback only updates the bars in memory. A writer wakes at each ten-second
boundary, takes the buckets that ended and writes them. When the database
does not answer, the buckets wait (up to an hour) and are written afterwards.

What is not recorded cannot be fetched later at this resolution, so a
recorder that stops must be heard: after a while without a bar during
trading hours it says so on Telegram, and again when it resumes.
"""

import asyncio
import logging
import time
from collections import deque
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Awaitable, Callable

import pytz

from src.price_recorder.bars import BAR_SECONDS, Bar, BarBuilder, ClosedBucket, bucket_start, read_quote
from src.price_recorder.instruments import Instrument, InstrumentKey, RecordingSet, UNDERLYING
from src.price_recorder.settings import RecorderSettings

logger = logging.getLogger(__name__)

# Buckets kept in memory while the database is unavailable: one hour.
PENDING_BUCKETS_LIMIT = 3600 // BAR_SECONDS
# Written per tick when catching up, so that one tick never runs long.
BUCKETS_PER_FLUSH = 60
LOST_BUCKETS_LOG_EVERY = 600 // BAR_SECONDS
DB_TIMEOUT_SECONDS = 8.0
NOTIFY_TIMEOUT_SECONDS = 5.0
# Bars are being written again once one is this recent.
RESUMED_WITHIN_SECONDS = 3 * BAR_SECONDS
# How soon a refresh that failed is tried again.
REFRESH_RETRY_SECONDS = 30.0
# Time given to Saxo to send a first quote before an underlying is reported as unquoted.
FIRST_QUOTE_GRACE_SECONDS = 60.0


class PriceRecorder:
    def __init__(
        self,
        *,
        settings: RecorderSettings,
        recording_set: RecordingSet,
        history,
        stream,
        discover: Callable[[], Awaitable[list[Instrument]]],
        notify: Callable[[str], Awaitable[None]] | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self._settings = settings
        self._set = recording_set
        self._history = history
        self._stream = stream
        self._discover = discover
        self._notify = notify
        self._clock = clock

        self._builder = BarBuilder()
        self._ids: dict[InstrumentKey, int] = {}
        self._pending: deque[ClosedBucket] = deque()
        self._stopping = asyncio.Event()

        self._started_at = clock()
        self._last_bar_written_at: float | None = None
        self._expected_since: float | None = None
        self._alerting = False
        self._last_db_error: str | None = None
        self._db_failing = False
        self._dropped_buckets = 0
        self._retention_done_on = None
        self._unquoted_reported: set[InstrumentKey] = set()
        self._subscribed_since: dict[InstrumentKey, float] = {}

    # ── stream ──

    async def on_quotes(self, definition: dict, snapshot_rows: list[dict], context_id: str | None = None) -> None:
        """Stream callback. Memory only: anything slow here would hold up the reading of the socket."""
        at = self._clock()
        asset_type = definition.get("asset_type")
        for row in snapshot_rows:
            uic = row.get("Uic")
            if uic is None:
                continue
            key = (int(uic), asset_type)
            if key in self._set:
                self._builder.update(key, read_quote(row), at)

    # ── instruments ──

    def _now(self) -> datetime:
        return datetime.fromtimestamp(self._clock(), timezone.utc)

    def _start_of_today(self) -> datetime:
        local = self._now().astimezone(pytz.timezone(self._settings.timezone))
        return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)

    async def restore(self) -> int:
        """After a restart, keep recording the turbos that already have bars today."""
        rows = await self._history.instruments_recorded_since(self._start_of_today())
        turbos = []
        for row in rows:
            instrument = Instrument(**{name: row[name] for name in Instrument.__dataclass_fields__ if name in row})
            self._ids[instrument.key] = row["id"]
            if instrument.role != UNDERLYING:
                turbos.append(instrument)
        self._set.add_turbos(turbos, self._now())
        return len(turbos)

    async def refresh(self) -> None:
        """Look for the turbos to record now, and bring the stream and the database in line with the set."""
        now = self._now()
        if self._set.start_new_day_if_needed():
            logger.info("New day: the turbos seen yesterday are no longer recorded.")
            self._unquoted_reported.clear()

        try:
            found = await self._discover()
        except Exception as e:
            # Whatever went wrong, what is already recorded keeps being recorded.
            logger.warning("Looking for turbos to record failed: %s", e, exc_info=True)
            found = []
        new = self._set.add_turbos(found, now)
        if new:
            logger.info("Recording %d more instrument(s), %d in all.", len(new), len(self._set))

        keys = self._set.keys()
        self._builder.retain(keys)
        # The ids are kept: bars of an instrument that just left the set may still be waiting to be written.
        at = self._clock()
        self._subscribed_since = {key: self._subscribed_since.get(key, at) for key in keys}

        await self._stream.set_subscriptions(self._set.subscription_definitions())
        await self._maintain_database(now)
        self._report_unquoted_underlyings(now)

    async def _maintain_database(self, now: datetime) -> None:
        try:
            await asyncio.wait_for(self._history.ensure_partitions(now.date()), DB_TIMEOUT_SECONDS)
            await asyncio.wait_for(self._assign_ids(self._set.keys(), now), DB_TIMEOUT_SECONDS)
            months = self._settings.retention_months
            if months > 0 and self._retention_done_on != now.date():
                dropped = await asyncio.wait_for(
                    self._history.drop_partitions_older_than(now.date(), months), DB_TIMEOUT_SECONDS,
                )
                self._retention_done_on = now.date()
                if dropped:
                    logger.info("Retention of %d month(s): removed %s.", months, ", ".join(dropped))
        except Exception as e:
            # Retried at the next refresh, and by the writer for the ids it needs.
            logger.warning("Price history upkeep failed: %s", e)

    async def _assign_ids(self, keys, seen_at: datetime) -> None:
        """Record the description of each instrument and learn its id. Refreshes last_seen_at on the way."""
        for key in keys:
            instrument = self._set.get(key)
            if instrument is None:
                continue
            self._ids[key] = await self._history.upsert_instrument(asdict(instrument), seen_at)

    def _report_unquoted_underlyings(self, now: datetime) -> None:
        if not self._settings.is_trading_time(now):
            return
        at = self._clock()
        for instrument in self._set.instruments():
            key = instrument.key
            if instrument.role != UNDERLYING or key in self._unquoted_reported or self._builder.has_quote(key):
                continue
            if at - self._subscribed_since.get(key, at) < FIRST_QUOTE_GRACE_SECONDS:
                continue
            self._unquoted_reported.add(key)
            logger.warning(
                "Saxo sends no quote for %s (UIC %s, %s): the index of %s is not being recorded. "
                "Look for an instrument your account gets prices for with "
                "`python -m src.price_recorder.probe --keywords ...` and name it in "
                "trade.config.price_recorder.instruments.",
                instrument.description or instrument.symbol or "the underlying",
                instrument.uic, instrument.asset_type, instrument.indice,
            )

    # ── writing ──

    def _row(self, bar: Bar) -> tuple:
        return (
            datetime.fromtimestamp(bar.start, timezone.utc), self._ids[bar.key],
            bar.bid_open, bar.bid_high, bar.bid_low, bar.bid_close,
            bar.ask_open, bar.ask_high, bar.ask_low, bar.ask_close,
            min(bar.updates, 32767), min(bar.delayed_minutes, 32767),
        )

    async def _flush_pending(self) -> None:
        for _ in range(min(len(self._pending), BUCKETS_PER_FLUSH)):
            bucket = self._pending[0]
            missing = {bar.key for bar in bucket.bars if bar.key not in self._ids}
            if missing:
                await self._assign_ids(missing, self._now())
            # A bar of an instrument that left the set before its description could be recorded cannot be written.
            rows = [self._row(bar) for bar in bucket.bars if bar.key in self._ids]
            await self._history.insert_bars(rows)
            await self._history.record_coverage(
                datetime.fromtimestamp(bucket.start, timezone.utc), bucket.feed_ok, bucket.instruments, len(rows),
            )
            self._pending.popleft()
            if rows:
                self._last_bar_written_at = max(self._last_bar_written_at or 0.0, bucket.start + BAR_SECONDS)

    async def write_closed_buckets(self) -> None:
        """Close the buckets that ended and write what is waiting. Never raises: the next tick tries again."""
        self._pending.extend(self._builder.close_until(self._clock()))
        overflow = len(self._pending) - PENDING_BUCKETS_LIMIT
        if overflow > 0:
            for _ in range(overflow):
                self._pending.popleft()
            before = self._dropped_buckets
            self._dropped_buckets += overflow
            # Said when it starts, then every ten minutes of it: not once per bucket.
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

        await self._check_alert()

    # ── being heard when it stops ──

    def _why_nothing_is_recorded(self) -> str:
        if self._db_failing:
            return f"the database does not take the bars ({self._last_db_error}); {len(self._pending)} bucket(s) are waiting"
        if len(self._set) == 0:
            return "no instrument to record was found"
        return "no quote is arriving from Saxo"

    async def _check_alert(self) -> None:
        at = self._clock()
        expected = self._settings.is_trading_time(datetime.fromtimestamp(at, timezone.utc))
        if not expected:
            # Outside trading hours silence is the normal state: nothing to count.
            self._expected_since = None
        elif self._expected_since is None:
            self._expected_since = at

        last_bar = self._last_bar_written_at
        if self._alerting:
            if last_bar is not None and at - last_bar <= RESUMED_WITHIN_SECONDS:
                self._alerting = False
                await self._say("WATA price recorder: recording again.")
            return
        if not expected:
            return
        silent_since = max(self._expected_since, last_bar or 0.0)
        silent_for = at - silent_since
        if silent_for >= self._settings.alert_after_seconds:
            self._alerting = True
            await self._say(
                f"WATA price recorder: nothing recorded for {int(silent_for // 60)} min, "
                f"{self._why_nothing_is_recorded()}. These prices cannot be fetched later."
            )

    async def _say(self, message: str) -> None:
        logger.warning(message)
        if self._notify is None:
            return
        try:
            await asyncio.wait_for(self._notify(message), NOTIFY_TIMEOUT_SECONDS)
        except Exception as e:
            logger.warning("The Telegram notification could not be sent: %s", e)

    # ── running ──

    async def _sleep_or_stop(self, seconds: float) -> bool:
        """Wait; True when the recorder was asked to stop meanwhile."""
        try:
            await asyncio.wait_for(self._stopping.wait(), max(seconds, 0.0))
        except asyncio.TimeoutError:
            return False
        return True

    async def _refresh_loop(self) -> None:
        while not self._stopping.is_set():
            wait = self._settings.refresh_interval_seconds
            try:
                await self.refresh()
            except Exception as e:
                # Most likely the subscriptions could not be set: without them
                # nothing is recorded, so it is not left for the usual five minutes.
                logger.error("Refresh of the instruments failed: %s", e, exc_info=True)
                wait = min(wait, REFRESH_RETRY_SECONDS)
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
            restored = await asyncio.wait_for(self.restore(), DB_TIMEOUT_SECONDS)
            if restored:
                logger.info("Recording resumes with the %d turbo(s) already recorded today.", restored)
        except Exception as e:
            logger.warning("Could not read today's instruments back: %s", e)

        tasks = [asyncio.create_task(self._refresh_loop()), asyncio.create_task(self._write_loop())]
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
