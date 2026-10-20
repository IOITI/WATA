"""
Each finished day of what WATA recorded and did, written as files beside the
price history, for a research machine to pull (``lab/sync.sh``: rsync over
SSH) and load into a database of its own. The backtests and the analyses run
there read the same tables as here, and ask nothing of the server while it
trades.

Under ``<price_history_path>/research/days/<YYYY-MM-DD>/``, a local day of the
trading timezone:

- one gzip CSV per table, with its header, as ``COPY ... TO STDOUT`` writes the
  rows of that day (``TABLES``): the recorded prices and coverage, the
  Capital.com feed's, the signal journal, the TA engine's signals, the
  positions, the orders and what Saxo answered to each request (never the
  bodies of the requests and answers), the account's daily balance; and the
  whole of the two tables of instruments;
- ``settings.json``: the backtest settings of the configuration as it is when
  the day is written (``from_config``), and its ``trade`` section without the
  addresses of the services (``PROTECTED_PATHS``) and with every secret masked;
- ``manifest.json``, written last: each file's size and sha256, and what made it.

A day is written once it is over, only while the market is closed by the live
rules, one table at a time through the admin's read-only connection: nothing
here competes with the trading services for the database while they trade.
It is written into ``<day>.part`` and renamed whole. The gzip headers carry
neither time nor name, so the same rows give the same bytes.

Off until the operator switches it on (``research/settings.json``). Days older
than ``keep_days`` are removed: the research machine keeps what it pulled.
None of this acts on the trading services, so it is neither in the audit
trail nor announced on Telegram.
"""

import asyncio
import copy
import gzip
import hashlib
import json
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Awaitable, Callable

import pytz

from src.admin.config_editor import PROTECTED_PATHS, mask_secrets
from src.admin.downloads import trading_hours
from src.admin.errors import InvalidRequest
from src.admin.processes import folder_bytes, read_json, utc, write_json
from src.backtest.clock import TradingClock
from src.backtest.settings import from_config

logger = logging.getLogger(__name__)

FORMAT = 1
RESEARCH_FOLDER = "research"
DAYS_FOLDER = "days"
SETTINGS_FILE = "settings.json"
MANIFEST_FILE = "manifest.json"

DEFAULT_KEEP_DAYS = 30
MAX_KEEP_DAYS = 365
TICK_SECONDS = 15 * 60
# A day of every table, through one connection: generous, but not without end.
COPY_TIMEOUT_SECONDS = 15 * 60

DAY_NAME = re.compile(r"\d{4}-\d{2}-\d{2}")

OFF = "off"
WAITING_FOR_CLOSE = "waiting_for_close"
UP_TO_DATE = "up_to_date"
DUE = "due"
EXPORTING = "exporting"


@dataclass(frozen=True)
class ExportTable:
    """A table written each day: its rows of the day by ``moment``, or all of them when it has none."""

    name: str
    # The SQL expression of the moment a row belongs to; "" for a table written whole each day.
    moment: str = ""
    # The columns written, in order; empty: all of them.
    columns: tuple[str, ...] = ()
    # The moment is a DATE, compared with the day itself.
    by_date: bool = False

    def query(self, start: datetime, end: datetime, day: date) -> tuple[str, tuple]:
        selected = ", ".join(self.columns) if self.columns else "*"
        if not self.moment:
            return f"SELECT {selected} FROM {self.name}", ()
        if self.by_date:
            return f"SELECT {selected} FROM {self.name} WHERE {self.moment} = $1", (day,)
        return f"SELECT {selected} FROM {self.name} WHERE {self.moment} >= $1 AND {self.moment} < $2", (start, end)


# What Saxo was sent and answered stays on the server: the rest of a request is what research needs.
# The columns of src/database/postgres.py (ORDER_REQUEST_COLUMNS) less the two bodies, which a test holds
# to it: that module needs asyncpg, which this one does without.
ORDER_REQUEST_KEPT = (
    "request_id", "requested_at", "answered_at", "recorded_by", "operation", "purpose", "position_id",
    "signal_uuid", "action", "reason", "instrument_uic", "asset_type", "instrument_name", "instrument_symbol",
    "instrument_currency", "buy_sell", "amount", "order_type", "price_seen", "outcome", "order_id",
    "http_status", "error_code", "error_message",
)

TABLES = (
    ExportTable("price_instrument"),
    ExportTable("price_bar_10s", "ts"),
    ExportTable("price_recorder_coverage", "ts"),
    ExportTable("price_volume", "ts"),
    ExportTable("capital_instrument"),
    ExportTable("capital_bar_10s", "ts"),
    ExportTable("capital_coverage", "ts"),
    ExportTable("signal_journal", "COALESCE(received_timestamp, decided_at, signal_timestamp)"),
    ExportTable("ta_signal", "COALESCE(raised_at, bar_closed_at)"),
    ExportTable("turbo_data_position", "execution_time_open"),
    ExportTable("turbo_data_order", "order_time"),
    ExportTable("order_execution", "sent_at"),
    ExportTable("order_request", "requested_at", columns=ORDER_REQUEST_KEPT),
    ExportTable("account_balance_history", "date_day", by_date=True),
)


def _gzip_writer(path: str):
    """A gzip file without time nor name in its header, and the running sha256 of what is written."""
    raw = open(path, "wb")
    return raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0)


def _file_digest(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as in_file:
        for chunk in iter(lambda: in_file.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _without(config: dict, paths: tuple[str, ...]) -> dict:
    """A copy of ``config`` without the values at ``paths`` (dotted)."""
    kept = copy.deepcopy(config)
    for path in paths:
        *parents, last = path.split(".")
        node = kept
        for key in parents:
            node = node.get(key) if isinstance(node, dict) else None
            if node is None:
                break
        if isinstance(node, dict):
            node.pop(last, None)
    return kept


def settings_snapshot(config: dict) -> dict:
    """What a research machine needs of the configuration: no address of a service, no secret."""
    trade = config.get("trade") or {}
    section = {"trade": {"rules": trade.get("rules"), "config": trade.get("config")}}
    snapshot = {"config": mask_secrets(_without(section, PROTECTED_PATHS)), "backtest": None, "error": None}
    try:
        snapshot["backtest"] = from_config(config).to_dict()
    except (KeyError, TypeError, ValueError) as e:
        snapshot["error"] = f"The backtest settings cannot be read from the configuration: {e}"
    return snapshot


class ResearchExport:
    def __init__(
        self,
        *,
        prices_root: str,
        queries,
        config_path: str,
        clock: Callable[[], float] = time.time,
        version: str = "",
    ):
        self._root = os.path.join(prices_root, RESEARCH_FOLDER)
        self._queries = queries
        self._config_path = config_path
        self._clock = clock
        self._version = version
        self._exporting: date | None = None
        self._last_error: str | None = None

    # ── where ──

    def _days_root(self) -> str:
        return os.path.join(self._root, DAYS_FOLDER)

    def _day_folder(self, day: date) -> str:
        return os.path.join(self._days_root(), day.isoformat())

    def written_days(self) -> list[date]:
        """The days written whole (their manifest is there), oldest first."""
        try:
            names = os.listdir(self._days_root())
        except OSError:
            return []
        return sorted(
            date.fromisoformat(name) for name in names
            if DAY_NAME.fullmatch(name) and os.path.isfile(os.path.join(self._days_root(), name, MANIFEST_FILE))
        )

    # ── the switch ──

    def settings(self) -> dict:
        saved = read_json(os.path.join(self._root, SETTINGS_FILE)) or {}
        keep_days = saved.get("keep_days")
        return {
            "enabled": saved.get("enabled") is True,
            "keep_days": keep_days if isinstance(keep_days, int) and 1 <= keep_days <= MAX_KEEP_DAYS else DEFAULT_KEEP_DAYS,
        }

    def update(self, *, enabled: bool | None = None, keep_days: int | None = None) -> dict:
        settings = self.settings()
        if enabled is not None:
            if not isinstance(enabled, bool):
                raise InvalidRequest("enabled is true or false")
            settings["enabled"] = enabled
        if keep_days is not None:
            if isinstance(keep_days, bool) or not isinstance(keep_days, int) or not 1 <= keep_days <= MAX_KEEP_DAYS:
                raise InvalidRequest(f"The days kept are 1 to {MAX_KEEP_DAYS}")
            settings["keep_days"] = keep_days
        os.makedirs(self._root, exist_ok=True)
        write_json(os.path.join(self._root, SETTINGS_FILE), settings)
        return self.status()

    # ── when ──

    def _config(self) -> dict:
        with open(self._config_path, encoding="utf-8") as config_file:
            return json.load(config_file)

    def _clock_and_hours(self) -> tuple[TradingClock, int, int]:
        zone, start_hour, end_hour = trading_hours(self._config())
        return TradingClock(zone), start_hour, end_hour

    def _market_is_open(self, now: float) -> bool:
        """Whether WATA may be trading: its hours on a weekday. Unknown counts as open."""
        try:
            clock, start_hour, end_hour = self._clock_and_hours()
        except (OSError, ValueError, KeyError, TypeError, pytz.UnknownTimeZoneError):
            return True
        local = clock.local(now)
        return local.weekday() < 5 and start_hour <= local.hour < end_hour

    def due_days(self, now: float | None = None) -> list[date]:
        """The finished days of the last ``keep_days`` not written yet, oldest first. Today is left to tomorrow."""
        now = self._clock() if now is None else now
        try:
            clock, _, _ = self._clock_and_hours()
        except (OSError, ValueError, KeyError, TypeError, pytz.UnknownTimeZoneError):
            return []
        today = clock.day_of(now)
        written = set(self.written_days())
        keep_days = self.settings()["keep_days"]
        return [
            today - timedelta(days=offset) for offset in range(keep_days, 0, -1)
            if today - timedelta(days=offset) not in written
        ]

    def status(self) -> dict:
        settings = self.settings()
        written = self.written_days()
        now = self._clock()
        if not settings["enabled"]:
            state = OFF
        elif self._exporting is not None:
            state = EXPORTING
        elif not self.due_days(now):
            state = UP_TO_DATE
        elif self._market_is_open(now):
            state = WAITING_FOR_CLOSE
        else:
            state = DUE
        return {
            **settings,
            "state": state,
            "exporting": self._exporting.isoformat() if self._exporting else None,
            "days": len(written),
            "first": written[0].isoformat() if written else None,
            "last": written[-1].isoformat() if written else None,
            "bytes": folder_bytes(self._days_root()) if written else 0,
            "error": self._last_error,
            "location": os.path.join(RESEARCH_FOLDER, DAYS_FOLDER),
            "limits": {"keep_days": MAX_KEEP_DAYS},
        }

    # ── writing a day ──

    async def _copy(self, table: ExportTable, path: str, start: datetime, end: datetime, day: date) -> dict | None:
        """One table's rows of the day into ``path``; None when the table does not exist yet."""
        query, args = table.query(start, end, day)
        raw, out_file = _gzip_writer(path)
        lines = 0

        async def write(chunk: bytes) -> None:
            nonlocal lines
            lines += chunk.count(b"\n")
            # Compressing is the admin's own work: in a thread, so that its pages keep answering meanwhile.
            await asyncio.to_thread(out_file.write, chunk)

        try:
            present = await self._queries.copy_csv(query, *args, output=write, timeout=COPY_TIMEOUT_SECONDS)
        finally:
            out_file.close()
            raw.close()
        if not present:
            os.remove(path)
            return None
        # The header is a line; a value holding a line break makes the count an upper bound.
        return {"lines": max(0, lines - 1), "bytes": os.path.getsize(path), "sha256": await asyncio.to_thread(_file_digest, path)}

    async def export_day(self, day: date) -> dict:
        """Write ``day`` whole, replacing what was written of it before. Returns its manifest."""
        clock, _, _ = self._clock_and_hours()
        start_epoch, end_epoch = clock.day_bounds(day)
        start = datetime.fromtimestamp(start_epoch, timezone.utc)
        end = datetime.fromtimestamp(end_epoch, timezone.utc)
        final = self._day_folder(day)
        part = f"{final}.part"
        shutil.rmtree(part, ignore_errors=True)
        os.makedirs(part)
        self._exporting = day
        try:
            tables: dict[str, dict] = {}
            for table in TABLES:
                written = await self._copy(table, os.path.join(part, f"{table.name}.csv.gz"), start, end, day)
                tables[table.name] = {"present": written is not None, **(written or {})}
            write_json(os.path.join(part, SETTINGS_FILE), settings_snapshot(self._config()))
            manifest = {
                "format": FORMAT,
                "day": day.isoformat(),
                "timezone": clock.name,
                "start": utc(start_epoch),
                "end": utc(end_epoch),
                "tables": tables,
                "settings_sha256": _file_digest(os.path.join(part, SETTINGS_FILE)),
                "exported_at": utc(self._clock()),
                "version": self._version,
            }
            # Last: a folder without its manifest is not a day.
            write_json(os.path.join(part, MANIFEST_FILE), manifest)
            shutil.rmtree(final, ignore_errors=True)
            os.replace(part, final)
            return manifest
        except BaseException:
            shutil.rmtree(part, ignore_errors=True)
            raise
        finally:
            self._exporting = None

    def prune(self, now: float | None = None) -> list[date]:
        """Remove the days older than ``keep_days``, and what an interrupted export left."""
        now = self._clock() if now is None else now
        try:
            clock, _, _ = self._clock_and_hours()
        except (OSError, ValueError, KeyError, TypeError, pytz.UnknownTimeZoneError):
            return []
        oldest = clock.day_of(now) - timedelta(days=self.settings()["keep_days"])
        removed = []
        for day in self.written_days():
            if day < oldest:
                shutil.rmtree(self._day_folder(day), ignore_errors=True)
                removed.append(day)
        try:
            names = os.listdir(self._days_root())
        except OSError:
            names = []
        for name in names:
            if name.endswith(".part") and self._exporting is None:
                shutil.rmtree(os.path.join(self._days_root(), name), ignore_errors=True)
        return removed

    async def export_once(self) -> str:
        """Write the days that are due while the market stays closed; says what it found (``status``'s state)."""
        if not self.settings()["enabled"]:
            return OFF
        self.prune()
        due = self.due_days()
        if not due:
            return UP_TO_DATE
        for day in due:
            if self._market_is_open(self._clock()):
                return WAITING_FOR_CLOSE
            try:
                manifest = await self.export_day(day)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._last_error = f"{day.isoformat()}: {e}"
                logger.warning(f"Could not write the research files of {day.isoformat()}: {e}")
                return DUE
            self._last_error = None
            rows = sum(table.get("lines", 0) for table in manifest["tables"].values())
            logger.info(f"Research files of {day.isoformat()} written ({rows} rows)")
        return UP_TO_DATE

    async def keep(self, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        """Until cancelled: look every quarter of an hour whether a day is due."""
        while True:
            try:
                await self.export_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"Could not write the research files: {e}")
            await sleep(TICK_SECONDS)
