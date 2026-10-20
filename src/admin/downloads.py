"""
The Dukascopy history the Data pages offer: which days of an instrument are
there, their download, and keeping the newest days fetched without anyone
asking.

A download is the command of src/price_history/dukascopy.py run as a child
process (src/admin/processes.py): one at a time whatever the instrument,
which is also all Dukascopy's server tolerates, at the lowest priority. Asked
for from the page, or by this service itself when it is told to keep the
prices up to date (``keep_prices``): then it fetches what is missing a few
days at a time, only while the market is closed by the live rules, and waits
longer each time the server stops answering.

What it remembers is kept as files beside the prices, under
``<price_history_path>/dukascopy/_fetch``: the download in progress and its
output, the switch (``keep.json``) and what the keeper learnt (``keeper.json``:
when it may ask again, and the days the server has no more of, which are
never asked for again).

None of this acts on the trading services, so it is neither in the audit
trail nor announced on Telegram.
"""

import asyncio
import json
import logging
import os
import shutil
import time
from datetime import date, datetime, timedelta, timezone
from typing import Awaitable, Callable

import pytz

from src.admin.data import PriceHistory
from src.admin.errors import InvalidRequest, NotPossible
from src.admin.processes import (
    DONE,
    FAILED,
    IDLE,
    INTERRUPTED,
    RUNNING,
    Launcher,
    Process,
    folder_bytes,
    launch,
    read_json,
    tail,
    utc,
    write_json,
)
from src.price_history import dukascopy

logger = logging.getLogger(__name__)

# A download that ended with days still missing: the usual end of one.
INCOMPLETE = "incomplete"
STOPPED = "stopped"

COVERAGE_MAX_DAYS = 366
FETCH_MAX_DAYS = 92
FETCH_TIMEOUT_SECONDS = 6 * 3600
FETCH_FOLDER = "_fetch"

# Keeping the prices up to date without anyone asking.
KEEP_DEFAULT_DAYS = 120
KEEP_MAX_DAYS = 730
# Days asked for in one download: the newest that are missing.
KEEP_CHUNK_DAYS = 5
KEEP_TICK_SECONDS = 60
# Between two downloads that went well: nothing here is in a hurry.
KEEP_PAUSE_SECONDS = 300
# After the operator stopped a download: not started again behind their back for a while.
KEEP_AFTER_STOP_SECONDS = 3600
# After the server stopped answering, and twice as long each time it does again.
KEEP_BACKOFF_SECONDS = 1800
KEEP_BACKOFF_MAX_SECONDS = 12 * 3600
# A day older than this for which the server has nothing is not asked for again.
KEEP_GIVE_UP_AFTER_DAYS = 3

# What the keeper remembers, copied once from where the former backtester kept it.
_REMEMBERED = ("keep.json", "keeper.json")


def trading_hours(config: dict) -> tuple[str, int, int]:
    """The trading timezone and the hours WATA trades in, as the live rules say (``market_hours``)."""
    zone = ((config.get("trade") or {}).get("config") or {}).get("general", {}).get("timezone", "Europe/Paris")
    for rule in (config.get("trade") or {}).get("rules") or []:
        if isinstance(rule, dict) and rule.get("rule_type") == "market_hours":
            hours = rule.get("rule_config") or {}
            return zone, int(hours["trading_start_hour"]), int(hours["trading_end_hour"])
    raise KeyError("market_hours")


def config_hours(config_path: str) -> Callable[[], tuple[str, int, int]]:
    """Read again each time: the configuration may have been edited since the admin started."""

    def hours() -> tuple[str, int, int]:
        with open(config_path, encoding="utf-8") as config_file:
            return trading_hours(json.load(config_file))

    return hours


class HistoryDownloads:
    def __init__(
        self,
        *,
        prices_root: str,
        hours: Callable[[], tuple[str, int, int]] | None = None,
        launcher: Launcher = launch,
        publish: Callable[..., None] | None = None,
        clock: Callable[[], float] = time.time,
        label: str = dukascopy.DEFAULT_LABEL,
        legacy_state: str | None = None,
    ):
        self._prices_root = prices_root
        self._hours = hours
        self._launcher = launcher
        self._publish = publish
        self._clock = clock
        # The history kept up to date, and the one meant when a request names none.
        self._label = label
        self._legacy_state = legacy_state
        # The Dukascopy instruments that can be fetched, and the days of each (src/admin/data.py).
        self._history = PriceHistory(prices_root)
        self._fetch_process: Process | None = None
        self._fetch_task: asyncio.Task | None = None
        self._fetch_stopped = False
        self._adopt_legacy_state()

    def _state_file(self, name: str) -> str:
        return os.path.join(self._prices_root, dukascopy.SOURCE, FETCH_FOLDER, name)

    def _adopt_legacy_state(self) -> None:
        """The switch and the days settled by the former backtester, once: what it learnt of the server holds."""
        if not self._legacy_state:
            return
        for name in _REMEMBERED:
            old, new = os.path.join(self._legacy_state, name), self._state_file(name)
            if os.path.exists(new) or not os.path.isfile(old):
                continue
            try:
                os.makedirs(os.path.dirname(new), exist_ok=True)
                shutil.copyfile(old, new)
            except OSError as e:
                logger.warning(f"Could not take over {old}: {e}")

    # ── what there is ──

    def _instrument(self, label: str | None) -> tuple[str, str]:
        """
        The label asked for (the history kept up to date when none is) and the
        Dukascopy symbol fetched for it. A label that is not one of the
        instruments is refused before any path or command is made of it.
        """
        label = label or self._label
        symbol = self._history.symbol_of(label)
        if symbol is None:
            raise InvalidRequest(f"No Dukascopy instrument {label!r}")
        return label, symbol

    def _day_states(self, first_day: date, last_day: date, label: str | None = None) -> list[dict]:
        """
        What there is of each day: "ready", "partial" (written, but hours of it
        were not there yet when it was), "incomplete" (some hours, not enough to
        write it), "missing", "closed" (the market was), or "unavailable" (the
        source has nothing for it). A day the source was found to have no more
        of is settled: not asked for again. Days are only settled for the
        history kept up to date.
        """
        label, symbol = self._instrument(label)
        ready = set(self._history.days(label))
        settled = (self._keeper().get("settled") or {}) if label == self._label else {}
        days = []
        for offset in range((last_day - first_day).days + 1):
            day = first_day + timedelta(days=offset)
            open_hours = [hour for hour in dukascopy.day_hours(day) if not dukascopy.market_is_closed(hour)]
            kept = sum(
                1 for hour in open_hours if os.path.exists(dukascopy.raw_path(self._prices_root, symbol, hour))
            )
            if day in ready:
                state = "ready" if kept == len(open_hours) or day.isoformat() in settled else "partial"
            elif not open_hours:
                state = "closed"
            elif day.isoformat() in settled:
                state = "unavailable"
            else:
                state = "incomplete" if kept else "missing"
            days.append({"day": day.isoformat(), "state": state, "hours": kept, "hours_expected": len(open_hours)})
        return days

    def coverage(self, first_day: date, last_day: date, label: str | None = None) -> dict:
        """Which days of the period have prices, of ``label`` (the history kept up to date by default)."""
        if last_day < first_day or (last_day - first_day).days >= COVERAGE_MAX_DAYS:
            raise InvalidRequest(f"A period of {COVERAGE_MAX_DAYS} days at most")
        label, symbol = self._instrument(label)
        ready = self._history.days(label)
        return {
            "from": first_day.isoformat(),
            "to": last_day.isoformat(),
            "label": label,
            "symbol": symbol,
            "days": self._day_states(first_day, last_day, label),
            "ready": {
                "count": len(ready),
                "first": ready[0].isoformat() if ready else None,
                "last": ready[-1].isoformat() if ready else None,
            },
            "fetch": self.fetch_status(),
            "keep": self.keep_status(),
        }

    def storage(self) -> dict:
        """The disk the prices take, of every instrument fetched."""
        prices = os.path.join(self._prices_root, dukascopy.SOURCE)
        return {
            "days": sum(len(self._history.days(label)) for label in self._history.labels()),
            # The ten-second bars that are read, and the hours as they were fetched, kept to complete a day.
            "bars_bytes": folder_bytes(os.path.join(prices, "bars")),
            "fetched_bytes": folder_bytes(os.path.join(prices, "raw")),
        }

    # ── a download ──

    def _fetching(self) -> bool:
        return self._fetch_process is not None or (self._fetch_task is not None and not self._fetch_task.done())

    def fetch_status(self) -> dict:
        status = read_json(self._state_file("status.json")) or {"state": IDLE}
        if status.get("state") == RUNNING and (self._fetch_task is None or self._fetch_task.done()):
            # Written by an admin that is no longer there.
            status = {**status, "state": INTERRUPTED}
        return {**status, "lines": tail(self._state_file("output.log")) if status.get("state") != IDLE else []}

    def start_fetch(self, first_day: date, last_day: date, *, automatic: bool = False, label: str | None = None) -> dict:
        """
        Download the days of a period, of ``label`` (the history kept up to
        date by default). One download at a time, whatever the instrument: the
        server shuts out an address, not a symbol.
        """
        label, symbol = self._instrument(label)
        today = datetime.fromtimestamp(self._clock(), timezone.utc).date()
        if last_day < first_day:
            raise InvalidRequest("The period ends before it starts")
        if (last_day - first_day).days >= FETCH_MAX_DAYS:
            raise InvalidRequest(f"{FETCH_MAX_DAYS} days at most are fetched at a time")
        if last_day > today:
            raise InvalidRequest("Prices of the future cannot be fetched")
        if self._fetching():
            raise NotPossible("A download is already running: one at a time is all the server tolerates")

        os.makedirs(self._state_file(""), exist_ok=True)
        for name in ("output.log", "report.json"):
            try:
                os.remove(self._state_file(name))
            except OSError:
                pass
        write_json(self._state_file("status.json"), {
            "state": RUNNING, "from": first_day.isoformat(), "to": last_day.isoformat(),
            "label": label, "symbol": symbol,
            "started_at": utc(self._clock()),
            # Started by the service itself, to keep the prices up to date.
            "automatic": automatic,
        })
        self._fetch_stopped = False
        self._fetch_task = asyncio.create_task(self._fetch(first_day, last_day, label, symbol))
        return self.fetch_status()

    async def _fetch(self, first_day: date, last_day: date, label: str, symbol: str) -> None:
        arguments = [
            "src.price_history.dukascopy",
            "--from", first_day.isoformat(), "--to", last_day.isoformat(),
            "--root", self._prices_root, "--symbol", symbol, "--label", label, "--low-priority",
            "--report", self._state_file("report.json"),
        ]
        state, code = FAILED, None
        try:
            self._fetch_process = await self._launcher(arguments, self._state_file("output.log"))
            try:
                code = await asyncio.wait_for(self._fetch_process.wait(), FETCH_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                self._fetch_process.kill()
                await self._fetch_process.wait()
            # The downloader ends with 1 when days are still missing: that is not a failure of it.
            state = STOPPED if self._fetch_stopped else {0: DONE, 1: INCOMPLETE}.get(code, FAILED)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"The download of prices could not be run: {e}")
        finally:
            self._fetch_process = None
        status = read_json(self._state_file("status.json")) or {}
        write_json(self._state_file("status.json"), {
            **status, "state": state, "exit_code": code, "finished_at": utc(self._clock()),
        })
        try:
            self._after_fetch(state, read_json(self._state_file("report.json")), label)
        except Exception as e:
            logger.warning(f"Could not note how the download of prices went: {e}")
        # A download the service started by itself is not news to whoever is on the page.
        self._announce("prices_fetch_finished", state=state, automatic=bool(status.get("automatic")), label=label)

    def stop_fetch(self) -> dict:
        if self._fetch_process is None:
            raise NotPossible("No download is running")
        self._fetch_stopped = True
        self._fetch_process.terminate()
        return self.fetch_status()

    def _announce(self, event: str, **data) -> None:
        if self._publish is None:
            return
        try:
            self._publish(event, **data)
        except Exception as e:
            logger.warning(f"Could not announce {event}: {e}")

    # ── prices kept up to date without anyone asking ──

    def _keep_settings(self) -> dict:
        settings = read_json(self._state_file("keep.json")) or {}
        return {"enabled": bool(settings.get("enabled", False)), "days": int(settings.get("days", KEEP_DEFAULT_DAYS))}

    def _keeper(self) -> dict:
        """What the service remembers of the downloads: when it may ask again, and the days that are settled."""
        return read_json(self._state_file("keeper.json")) or {}

    def _set_keeper(self, **changes) -> None:
        os.makedirs(self._state_file(""), exist_ok=True)
        write_json(self._state_file("keeper.json"), {**self._keeper(), **changes})

    def set_keep(self, enabled: bool, days: int = KEEP_DEFAULT_DAYS) -> dict:
        """Switch on or off the fetching of what is missing of the last ``days`` days."""
        if not 1 <= days <= KEEP_MAX_DAYS:
            raise InvalidRequest(f"Between 1 and {KEEP_MAX_DAYS} days can be kept up to date")
        os.makedirs(self._state_file(""), exist_ok=True)
        write_json(self._state_file("keep.json"), {"enabled": enabled, "days": days})
        # Asked by the operator: whatever wait was in progress is over.
        self._set_keeper(next_at=0, failures=0)
        return self.keep_status()

    def _market_is_open(self, now: float) -> bool:
        """Whether WATA may be trading: its hours on a weekday. Unknown counts as open."""
        if self._hours is None:
            return True
        try:
            zone, start_hour, end_hour = self._hours()
            local = datetime.fromtimestamp(now, pytz.timezone(zone))
        except (OSError, ValueError, KeyError, TypeError, pytz.UnknownTimeZoneError):
            return True
        return local.weekday() < 5 and start_hour <= local.hour < end_hour

    def _days_to_keep(self, now: float, days: int) -> list[date]:
        """The days of the last ``days`` that are not whole yet, oldest first. Today is left to tomorrow."""
        today = datetime.fromtimestamp(now, timezone.utc).date()
        return [
            date.fromisoformat(day["day"])
            for day in self._day_states(today - timedelta(days=days), today - timedelta(days=1))
            if day["state"] in ("missing", "incomplete", "partial")
        ]

    def keep_status(self) -> dict:
        settings = self._keep_settings()
        keeper = self._keeper()
        now = self._clock()
        missing = self._days_to_keep(now, settings["days"])
        next_at = keeper.get("next_at") or 0
        if not settings["enabled"]:
            state = "off"
        elif self._fetching():
            state = "fetching"
        elif not missing:
            state = "up_to_date"
        elif now < next_at:
            # After the server stopped answering, or between two downloads.
            state = "backing_off" if keeper.get("failures") else "pausing"
        elif self._market_is_open(now):
            state = "waiting_for_close"
        else:
            state = "due"
        return {
            **settings,
            "state": state,
            "missing": len(missing),
            # Days the source has no more of, which are not asked for again.
            "settled": len(keeper.get("settled") or {}),
            "failures": keeper.get("failures") or 0,
            "next_at": utc(next_at) if now < next_at else None,
            "limits": {"days": KEEP_MAX_DAYS},
        }

    def keep_once(self) -> str:
        """
        Start the download of the newest days that are missing, when it is
        time to. Says what it found: the state ``keep_status`` gives.
        """
        status = self.keep_status()
        if status["state"] != "due":
            return status["state"]
        missing = self._days_to_keep(self._clock(), status["days"])
        newest = missing[-1]
        first = max(missing[0], newest - timedelta(days=KEEP_CHUNK_DAYS - 1))
        self.start_fetch(first, newest, automatic=True)
        return "fetching"

    def _after_fetch(self, state: str, report: dict | None, label: str | None = None) -> None:
        """
        What a download that ended says about the next one: when, and which
        days not to ask for again. The wait follows every download, since the
        server's patience is the same whatever was asked of it; the days settled
        are those of the history kept up to date only, or another instrument's
        missing day would mark the same day of that history unavailable for good.
        """
        now = self._clock()
        keeper = self._keeper()
        today = datetime.fromtimestamp(now, timezone.utc).date()
        days = (report or {}).get("days") or []
        settled = dict(keeper.get("settled") or {})
        for day in days if (label or self._label) == self._label else []:
            asked = date.fromisoformat(day["day"])
            # The server answered for every hour it was asked, and said it has no file for some of them:
            # days after the fact, they are not hours still to be published.
            if (
                day["unpublished_hours"] and not day["unanswered_hours"]
                and (today - asked).days > KEEP_GIVE_UP_AFTER_DAYS
                and not all(dukascopy.market_is_closed(hour) for hour in dukascopy.day_hours(asked))
            ):
                settled[day["day"]] = day["status"]
        unanswered = any(day["unanswered_hours"] or day["status"] == "not tried" for day in days)
        failures = keeper.get("failures") or 0
        if state == STOPPED:
            wait = KEEP_AFTER_STOP_SECONDS
        elif unanswered or state == FAILED or report is None:
            failures += 1
            wait = min(KEEP_BACKOFF_SECONDS * 2 ** (failures - 1), KEEP_BACKOFF_MAX_SECONDS)
        else:
            failures, wait = 0, KEEP_PAUSE_SECONDS
        self._set_keeper(next_at=now + wait, failures=failures, settled=settled)

    async def keep_prices(self, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        """Until cancelled: look once a minute whether a download is due."""
        while True:
            await sleep(KEEP_TICK_SECONDS)
            try:
                self.keep_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"Could not keep the prices up to date: {e}")

    async def close(self) -> None:
        """The admin is stopping: a download stops with it."""
        if self._fetch_process is not None and self._fetch_process.returncode is None:
            try:
                self._fetch_process.terminate()
            except ProcessLookupError:
                pass
        if self._fetch_task is not None:
            self._fetch_task.cancel()
