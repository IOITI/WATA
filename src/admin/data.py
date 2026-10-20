"""
The providers of the prices WATA keeps and the assets they hold, for the Data
pages of the admin (src/admin/routers/data.py).

- ``saxo``: what the price recorder keeps in PostgreSQL (src/price_recorder),
  one asset per recorded instrument, read through AdminQueries.
- ``dukascopy``: the days of ten-second bars fetched from Dukascopy
  (src/price_history/dukascopy.py), one asset per folder of bars, read
  here as files.
- ``capital``: what the Capital.com feed keeps in PostgreSQL
  (src/capital_feed), one asset per instrument of Capital.com, read through
  AdminQueries as the price recorder's are, and live through LivePrices.

The downloader decodes every symbol in thousandths: that is why only the
symbols checked against a fetched day can be added (``HISTORY_SYMBOLS``).
"""

import csv
import gzip
import json
import os
import re
import threading
from array import array
from bisect import bisect_left
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Iterable, Iterator, Sequence

import pytz

from src.admin.prices import interval_served
from src.price_recorder.bars import BAR_SECONDS
from src.price_history import dukascopy
from src.price_history.files import FileBars, files_of_period

SAXO = "saxo"
DUKASCOPY = dukascopy.SOURCE
CAPITAL = "capital"

LIVE = "live"
DOWNLOAD = "download"

# What became of an asset.
RECORDING = "recording"     # in the recorder's set at one of its last refreshes
ENDED = "ended"             # no longer recorded: knocked out, dropped from the set, or the recorder stopped
DOWNLOADED = "downloaded"   # days fetched, as many as there are files

# The statuses of a provider, as those of a service on the System page.
OK = "ok"
WARNING = "warning"
DOWN = "down"
UNKNOWN = "unknown"
DISABLED = "disabled"

CATALOG = (
    {
        "id": SAXO, "name": "Saxo Bank", "kind": LIVE, "available": True,
        "description": "The turbos and their index, recorded from Saxo's stream every ten seconds by the price recorder.",
    },
    {
        "id": DUKASCOPY, "name": "Dukascopy", "kind": DOWNLOAD, "available": True,
        "description": "Index history downloaded from Dukascopy's public tick files, as ten-second bars: what backtests run on.",
    },
    {
        "id": CAPITAL, "name": "Capital.com", "kind": LIVE, "available": True,
        "description": (
            "The Nasdaq 100 as TradingView's Capital.com chart shows it: streamed live, recorded every ten seconds "
            "with Capital.com's own volume of each minute, and kept a file a day for backtests."
        ),
    },
)
PROVIDERS = tuple(entry["id"] for entry in CATALOG if entry["available"])

# The most assets listed at once: the recorder keeps every turbo it ever followed.
MAX_ASSETS = 5000
# How far back the list goes ("seen"), in days of the trading timezone; None: all time.
SEEN_WINDOWS = {"all": None, "30d": 30, "7d": 7, "today": 0}
# Periods read from Dukascopy's files at once: each view parses its days again, about 8,000 rows a day.
HISTORY_MAX_DAYS = 31
# Statistics read every ten-second bar of the period.
STATISTICS_MAX_DAYS = 31
# Days of Dukascopy bars kept parsed in memory, a few hundred kilobytes each.
DAY_CACHE_SIZE = 31
# The spread is looked at by slots of this many minutes of the day.
SPREAD_SLOT_MINUTES = 30

# What an index can be at Saxo: the asset types the recorder looks for one under
# (UNDERLYING_ASSET_TYPES of src/price_recorder/instruments.py, which this module
# does not import: it brings the trader's services with it).
INDEX_ASSET_TYPES = ("StockIndex", "CfdOnIndex")

_SAXO_KEY = re.compile(r"^[1-9][0-9]{0,9}$")
# Capital.com's name of an instrument, as the feed accepts it (src/capital_feed/settings.py).
_CAPITAL_KEY = re.compile(r"^[A-Z0-9][A-Z0-9_.]{0,63}$")
CAPITAL_ASSET_TYPE = "Capital.com"
# The feed writes its state every ten seconds, and every minute while it is idle.
CAPITAL_STATE_FRESH_SECONDS = 90
LABEL_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
REGISTRY_FILE = "instruments.json"
_QUARTER = 900


class AssetNotFound(LookupError):
    """No such provider, or no such asset in it: a 404."""


class InvalidAsset(ValueError):
    """A request about assets that cannot be answered as it is: a 422, saying why."""


def ref_of(provider: str, key) -> str:
    return f"{provider}/{key}"


def saxo_instrument_id(key: str) -> int:
    """The recorder's id of an instrument, from the key of its address; checked before any query."""
    if not _SAXO_KEY.match(key):
        raise AssetNotFound(f"No recorded instrument {key!r}")
    return int(key)


def capital_epic(key: str) -> str:
    """A Capital.com epic from the key of an address; checked before any query."""
    if not _CAPITAL_KEY.match(key):
        raise AssetNotFound(f"No Capital.com instrument {key!r}")
    return key


def history_label(key: str) -> str:
    """A Dukascopy label from the key of an address; checked before any path is built from it."""
    if not LABEL_PATTERN.match(key):
        raise AssetNotFound(f"No Dukascopy history {key!r}")
    return key


def checked_provider(provider: str) -> str:
    if provider not in PROVIDERS:
        raise AssetNotFound(f"No provider {provider!r}")
    return provider


# ── assets ──


def saxo_asset(instrument: dict) -> dict:
    """A recorded instrument as an asset: its row, and the name the Data pages give it."""
    return {
        **instrument,
        "provider": SAXO,
        "key": str(instrument["id"]),
        "ref": ref_of(SAXO, instrument["id"]),
    }


def saxo_status(instrument: dict, now: datetime, refresh_seconds: int) -> str:
    """
    Recording while the recorder still names it at its refreshes (every
    ``refresh_seconds``): it notes when it last did, whatever the quotes.
    """
    seen = instrument.get("last_seen_at")
    if seen is None:
        return ENDED
    return RECORDING if (now - seen).total_seconds() <= 2 * refresh_seconds + BAR_SECONDS else ENDED


def capital_asset(row: dict) -> dict:
    """
    An instrument of the Capital.com feed as an asset: an index, as the
    Dukascopy histories are, named by its epic. Its label is the indice it
    stands for in WATA's rules.
    """
    asset = {
        "id": row["id"], "uic": None, "asset_type": CAPITAL_ASSET_TYPE,
        "symbol": row["epic"], "description": row.get("name") or row["epic"],
        "currency": row.get("currency"), "role": "underlying", "indice": row.get("label"),
        "underlying_uic": None, "direction": None, "barrier": None, "issuer": None,
        "epic": row["epic"], "instrument_type": row.get("instrument_type"), "decimals": row.get("decimals"),
        "provider": CAPITAL, "key": row["epic"], "ref": ref_of(CAPITAL, row["epic"]),
    }
    for name in ("first_t", "last_t", "last_bid", "last_ask", "first_seen_at", "last_seen_at"):
        if name in row:
            asset[name] = row[name]
    return asset


def capital_feed_running(state: dict | None, now: datetime) -> bool:
    """Whether the feed wrote its state lately: it does every ten seconds, every minute while idle."""
    if state is None or state.get("updated_at") is None:
        return False
    return (now - state["updated_at"]).total_seconds() <= CAPITAL_STATE_FRESH_SECONDS


def _figures(state: dict | None) -> dict:
    figures = (state or {}).get("figures")
    return figures if isinstance(figures, dict) else {}


def capital_status(asset: dict, state: dict | None, now: datetime) -> str:
    """Recording while the feed runs and follows it; ended otherwise."""
    if not capital_feed_running(state, now) or (state or {}).get("state") in ("disabled", "no_credentials", "invalid_config"):
        return ENDED
    followed = {instrument.get("epic") for instrument in _figures(state).get("instruments") or [] if isinstance(instrument, dict)}
    return RECORDING if asset.get("epic") in followed else ENDED


def _streaming_detail(state: dict) -> str:
    figures = _figures(state)
    parts = [state.get("detail") or "Streaming"]
    per_minute = figures.get("quotes_per_minute")
    if isinstance(per_minute, (int, float)):
        parts.append(f"{per_minute / 60:.1f} quotes/s")
    delay = (figures.get("delay_ms") or {}).get("p50")
    if isinstance(delay, (int, float)):
        parts.append(f"delay {delay:.0f} ms")
    return " · ".join(parts)


def capital_provider_state(enabled_on_disk: bool, state: dict | None, now: datetime) -> tuple[str, str]:
    """
    The Capital.com feed as the Providers page and the System page say it:
    switched on or off in the configuration as it is on disk now, and what
    the feed last wrote of itself (``capital_feed_state``).
    """
    if not capital_feed_running(state, now):
        if not enabled_on_disk:
            return DISABLED, "Switched off in the configuration"
        if state is None:
            return DOWN, "Never started: add \"capital\" to COMPOSE_PROFILES in .env and start it"
        minutes = int((now - state["updated_at"]).total_seconds() // 60)
        return DOWN, f"No sign of it for {minutes} min"
    kind, detail = state.get("state"), state.get("detail") or ""
    if kind == "disabled":
        if enabled_on_disk:
            return WARNING, "Switched on in the configuration: it starts at its next start"
        return DISABLED, "Switched off in the configuration"
    if not enabled_on_disk:
        return WARNING, "Switched off in the configuration: it records until it is restarted"
    if kind == "streaming":
        return OK, _streaming_detail(state)
    if kind == "market_closed":
        openings = sorted(
            instrument["next_opening"] for instrument in _figures(state).get("instruments") or []
            if isinstance(instrument, dict) and instrument.get("next_opening")
        )
        return OK, "Markets closed" + (f": the first opens at {openings[0]}" if openings else "")
    if kind in ("connecting", "starting"):
        return WARNING, detail or "Connecting"
    return DOWN, detail or kind or "Unknown state"


@dataclass(frozen=True)
class HistorySymbol:
    """An instrument of Dukascopy that can be fetched: its name there, and the label its files are kept under."""

    symbol: str
    label: str
    name: str
    currency: str


BUILT_IN = HistorySymbol(dukascopy.SYMBOL, dukascopy.DEFAULT_LABEL, "US Tech 100", "USD")

# Index CFDs of Dukascopy whose prices the downloader decodes as they are, in
# thousandths (dukascopy.PRICE_SCALE): a symbol quoted otherwise would be written
# ten or a hundred times off, and decoding it right means changing the
# downloader, which marks every backtest stale. Each is listed once a day of it
# was written and its level checked (2026-10-14: the US 500 at about 7,730 on
# 5 and 6 October, the Germany 40 at about 25,380 on 6 October). The US 30
# (USA30IDXUSD) decoded at its level, but Dukascopy answered some of its hours
# with a 503 at every try, so that none of its days could be written.
HISTORY_SYMBOLS = (
    BUILT_IN,
    HistorySymbol("USA500IDXUSD", "us500", "US 500", "USD"),
    HistorySymbol("DEUIDXEUR", "de40", "Germany 40", "EUR"),
)
_BY_SYMBOL = {entry.symbol: entry for entry in HISTORY_SYMBOLS}


@dataclass
class DayBars:
    """The ten-second bars of one day file, in columns: a few hundred kilobytes instead of tens of megabytes."""

    times: array = field(default_factory=lambda: array("q"))
    bid_open: array = field(default_factory=lambda: array("d"))
    bid_high: array = field(default_factory=lambda: array("d"))
    bid_low: array = field(default_factory=lambda: array("d"))
    bid_close: array = field(default_factory=lambda: array("d"))
    ask_open: array = field(default_factory=lambda: array("d"))
    ask_high: array = field(default_factory=lambda: array("d"))
    ask_low: array = field(default_factory=lambda: array("d"))
    ask_close: array = field(default_factory=lambda: array("d"))
    updates: array = field(default_factory=lambda: array("l"))

    def rows(self, first: int, last: int) -> Iterator[tuple]:
        """The bars that begin in [first, last), in the columns of BAR_COLUMNS."""
        for index in range(bisect_left(self.times, first), bisect_left(self.times, last)):
            yield (
                self.times[index],
                self.bid_open[index], self.bid_high[index], self.bid_low[index], self.bid_close[index],
                self.ask_open[index], self.ask_high[index], self.ask_low[index], self.ask_close[index],
                self.updates[index],
            )


def _read_day(path: str, label: str, day: date) -> DayBars:
    columns = DayBars()
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    for bar in FileBars(path, label).bars(start, start + timedelta(days=1)):
        columns.times.append(bar.start)
        columns.bid_open.append(bar.bid_open)
        columns.bid_high.append(bar.bid_high)
        columns.bid_low.append(bar.bid_low)
        columns.bid_close.append(bar.bid_close)
        columns.ask_open.append(bar.ask_open)
        columns.ask_high.append(bar.ask_high)
        columns.ask_low.append(bar.ask_low)
        columns.ask_close.append(bar.ask_close)
        columns.updates.append(bar.updates)
    return columns


def _file_description(path: str) -> dict:
    """What a file of bars says of its instrument, in its first row."""
    with gzip.open(path, "rt", newline="", encoding="utf-8") as in_file:
        for row in csv.DictReader(in_file):
            return {"symbol": row.get("symbol") or None, "description": row.get("description") or None}
    return {"symbol": None, "description": None}


class PriceHistory:
    """
    What was fetched from Dukascopy under ``prices_root``: the instruments that
    can be fetched (the Nasdaq history, built in, and those added), and the
    days of bars of each label. Read from the threads of the pool, so its
    memory of parsed days and its registry are guarded.
    """

    def __init__(self, prices_root: str, cache_days: int = DAY_CACHE_SIZE):
        self._root = prices_root
        self._cache_days = cache_days
        self._days: OrderedDict[tuple, DayBars] = OrderedDict()
        self._lock = threading.Lock()

    # ── which instruments can be fetched ──

    def _registry_path(self) -> str:
        return os.path.join(self._root, DUKASCOPY, REGISTRY_FILE)

    def _added(self) -> list[dict]:
        """The instruments added, as kept: anything that is not one of the symbols offered is ignored."""
        try:
            with open(self._registry_path(), encoding="utf-8") as in_file:
                entries = json.load(in_file).get("instruments") or []
        except (OSError, ValueError, AttributeError):
            return []
        added, labels, symbols = [], {BUILT_IN.label}, {BUILT_IN.symbol}
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            symbol, label = entry.get("symbol"), entry.get("label")
            if symbol in _BY_SYMBOL and symbol not in symbols and isinstance(label, str) \
                    and LABEL_PATTERN.match(label) and label not in labels:
                labels.add(label)
                symbols.add(symbol)
                added.append({"symbol": symbol, "label": label})
        return added

    def instruments(self) -> list[dict]:
        """The instruments that can be fetched: the built-in one first, then those added in the order they were."""
        def described(entry: dict, built_in: bool) -> dict:
            known = _BY_SYMBOL[entry["symbol"]]
            return {**entry, "name": known.name, "currency": known.currency, "built_in": built_in}

        return [
            described({"symbol": BUILT_IN.symbol, "label": BUILT_IN.label}, True),
            *(described(entry, False) for entry in self._added()),
        ]

    def symbol_of(self, label: str) -> str | None:
        """The symbol fetched for ``label``, None when the label is not one of the instruments."""
        return next((entry["symbol"] for entry in self.instruments() if entry["label"] == label), None)

    def offered(self) -> list[dict]:
        """The symbols that can still be added."""
        taken = {entry["symbol"] for entry in self.instruments()}
        return [
            {"symbol": entry.symbol, "label": entry.label, "name": entry.name, "currency": entry.currency}
            for entry in HISTORY_SYMBOLS if entry.symbol not in taken
        ]

    def add_instrument(self, symbol: str, label: str) -> dict:
        """
        Offer ``symbol`` for fetching under ``label``. A label is where the files
        go: two instruments under one label would write over each other's days,
        and one more under the Nasdaq's would overwrite what every backtest reads.
        """
        if symbol not in _BY_SYMBOL:
            raise InvalidAsset(f"{symbol!r} is not one of the Dukascopy instruments that can be fetched")
        if not isinstance(label, str) or not LABEL_PATTERN.match(label):
            raise InvalidAsset("A label is up to 32 lower-case letters, digits, '-' or '_', starting with a letter or a digit")
        with self._lock:
            instruments = self.instruments()
            if any(entry["symbol"] == symbol for entry in instruments):
                raise InvalidAsset(f"{symbol} is already fetched")
            if any(entry["label"] == label for entry in instruments):
                raise InvalidAsset(f"The label {label!r} is already taken")
            if os.path.isdir(dukascopy.bars_directory(self._root, label)) and self._file_symbol(label) not in (None, symbol):
                raise InvalidAsset(f"Bars of another instrument are already kept under {label!r}")
            added = [*self._added(), {"symbol": symbol, "label": label}]
            self._write(added)
        return next(entry for entry in self.instruments() if entry["label"] == label)

    def remove_instrument(self, label: str) -> None:
        """Stop offering ``label`` for fetching. Its days stay, and so does its asset."""
        if label == BUILT_IN.label:
            raise InvalidAsset("The Nasdaq history is what backtests run on: it cannot be removed")
        with self._lock:
            added = self._added()
            kept = [entry for entry in added if entry["label"] != label]
            if len(kept) == len(added):
                raise AssetNotFound(f"No Dukascopy instrument {label!r}")
            self._write(kept)

    def _write(self, entries: list[dict]) -> None:
        path = self._registry_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        partial = f"{path}.part"
        with open(partial, "w", encoding="utf-8") as out_file:
            json.dump({"instruments": entries}, out_file, indent=2)
        os.replace(partial, path)

    # ── the days fetched ──

    def labels(self) -> list[str]:
        """The labels that have a folder of bars, whether or not they can still be fetched."""
        try:
            names = os.listdir(os.path.join(self._root, DUKASCOPY, "bars"))
        except OSError:
            return []
        return sorted(name for name in names if LABEL_PATTERN.match(name) and self.days(name))

    def days(self, label: str) -> list[date]:
        """The UTC days of ``label`` that have a file of bars, oldest first."""
        try:
            names = os.listdir(dukascopy.bars_directory(self._root, label))
        except OSError:
            return []
        days = []
        for name in names:
            if not name.endswith(".csv.gz"):
                continue
            try:
                days.append(date.fromisoformat(name.removesuffix(".csv.gz")))
            except ValueError:
                continue
        return sorted(days)

    def size(self, label: str) -> int:
        """The bytes of the files of bars of ``label``."""
        total = 0
        for day in self.days(label):
            try:
                total += os.path.getsize(dukascopy.day_path(self._root, label, day))
            except OSError:
                continue
        return total

    def _file_symbol(self, label: str) -> str | None:
        days = self.days(label)
        return _file_description(dukascopy.day_path(self._root, label, days[0]))["symbol"] if days else None

    def _day(self, label: str, day: date) -> DayBars:
        path = dukascopy.day_path(self._root, label, day)
        stat = os.stat(path)
        # A day fetched again is another file: its size or its time of writing differs.
        key = (path, stat.st_size, stat.st_mtime_ns)
        with self._lock:
            found = self._days.get(key)
            if found is not None:
                self._days.move_to_end(key)
                return found
        found = _read_day(path, label, day)
        with self._lock:
            self._days[key] = found
            while len(self._days) > self._cache_days:
                self._days.popitem(last=False)
        return found

    def rows(self, label: str, start: datetime, end: datetime) -> Iterator[tuple]:
        """The ten-second bars of ``label`` that begin in [start, end), oldest first, in the columns of BAR_COLUMNS."""
        location = dukascopy.bars_directory(self._root, label)
        try:
            names = os.listdir(location)
        except OSError:
            return
        first, last = int(start.timestamp()), int(end.timestamp())
        for path in files_of_period(location, names, start, end):
            day = date.fromisoformat(os.path.basename(path).removesuffix(".csv.gz"))
            yield from self._day(label, day).rows(first, last)

    def asset(self, label: str) -> dict | None:
        """
        ``label`` as an asset of the Data pages, with its first and last bar;
        None without a day of bars. Its first and last days are those of its
        files, days of UTC as they are fetched: east of UTC, the last hours of
        the last one fall on the next day of the trading timezone, which is
        not the day to open it on.
        """
        days = self.days(label)
        if not days:
            return None
        first_day, last_day = self._day(label, days[0]), self._day(label, days[-1])
        if not first_day.times or not last_day.times:
            return None
        described = _file_description(dukascopy.day_path(self._root, label, days[-1]))
        known = _BY_SYMBOL.get(described["symbol"])
        return {
            "id": None, "uic": None, "asset_type": dukascopy.ASSET_TYPE,
            "symbol": described["symbol"],
            "description": f"{known.name} (Dukascopy)" if known else described["description"],
            "currency": known.currency if known else None,
            "role": "underlying", "indice": label, "underlying_uic": None,
            "direction": None, "barrier": None, "issuer": None,
            "first_t": first_day.times[0],
            "last_t": last_day.times[-1],
            "last_bid": last_day.bid_close[-1],
            "last_ask": last_day.ask_close[-1],
            "days": len(days),
            "first_day": days[0].isoformat(),
            "last_day": days[-1].isoformat(),
            "provider": DUKASCOPY,
            "key": label,
            "ref": ref_of(DUKASCOPY, label),
            "status": DOWNLOADED,
        }


# ── bars ──


def aggregate(rows: Iterable[Sequence], interval: int, limit: int) -> list[list]:
    """
    Ten-second rows (BAR_COLUMNS) merged into bars of ``interval`` seconds as
    the recorder's are in SQL (AdminQueries.price_bars): first open, highest
    high, lowest low, last close, each side of the ask from the bars that have
    one, quote changes added up. Stops once ``limit`` bars are made.
    """
    if not interval_served(interval):
        raise ValueError(f"Unsupported interval: {interval}")
    bars: list[list] = []
    current: list | None = None
    for t, bid_open, bid_high, bid_low, bid_close, ask_open, ask_high, ask_low, ask_close, updates in rows:
        bucket = t - t % interval
        if current is None or bucket != current[0]:
            if len(bars) == limit:
                break
            current = [bucket, bid_open, bid_high, bid_low, bid_close, ask_open, ask_high, ask_low, ask_close, updates]
            bars.append(current)
            continue
        current[2] = max(current[2], bid_high)
        current[3] = min(current[3], bid_low)
        current[4] = bid_close
        if current[5] is None:
            current[5] = ask_open
        if ask_high is not None:
            current[6] = ask_high if current[6] is None else max(current[6], ask_high)
        if ask_low is not None:
            current[7] = ask_low if current[7] is None else min(current[7], ask_low)
        if ask_close is not None:
            current[8] = ask_close
        current[9] += updates
    return bars


# ── statistics ──


class _LocalTime:
    """
    Where a moment falls in the trading timezone: its day, its minute of the
    day, and whether it is trading time. Worked out once per quarter of an
    hour, the steps any timezone offset is made of, not once per bar.
    """

    def __init__(self, timezone_name: str, is_trading_time: Callable[[datetime], bool]):
        self.zone = pytz.timezone(timezone_name)
        self._is_trading_time = is_trading_time
        self._quarters: dict[int, tuple[str, int, bool]] = {}

    def at(self, t: int) -> tuple[str, int, bool]:
        quarter = t - t % _QUARTER
        known = self._quarters.get(quarter)
        if known is None:
            moment = datetime.fromtimestamp(quarter, timezone.utc)
            local = moment.astimezone(self.zone)
            known = (local.date().isoformat(), local.hour * 60 + local.minute, self._is_trading_time(moment))
            self._quarters[quarter] = known
        day, minute, trading = known
        return day, minute + (t - quarter) // 60, trading

    def trading_seconds(self, day: str) -> int:
        """How long ``day`` was trading time, a quarter of an hour at a time (23 or 25 hours on the days the clocks change)."""
        local_day = date.fromisoformat(day)
        start = self.zone.localize(datetime.combine(local_day, datetime.min.time())).astimezone(timezone.utc)
        end = self.zone.localize(datetime.combine(local_day + timedelta(days=1), datetime.min.time())).astimezone(timezone.utc)
        seconds, moment = 0, start
        while moment < end:
            if self._is_trading_time(moment):
                seconds += _QUARTER
            moment += timedelta(seconds=_QUARTER)
        return seconds


def _utc_text(t: int) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _percent(part: float, whole: float) -> float | None:
    return 100 * part / whole if whole else None


class DailyStatistics:
    """
    What the ten-second bars of a period say of each of its days (of the
    trading timezone), fed a page of rows at a time: the bars of a month are
    never all held at once. ``spread`` False: the instrument is quoted with one
    price (the recorder's index), so it has none to speak of.
    """

    def __init__(self, timezone_name: str, is_trading_time: Callable[[datetime], bool], *, spread: bool = True):
        self._time = _LocalTime(timezone_name, is_trading_time)
        self._spread = spread
        self._days: dict[str, dict] = {}

    def add(self, rows: Iterable[Sequence]) -> None:
        for t, bid_open, bid_high, bid_low, bid_close, _ao, _ah, _al, ask_close, updates in rows:
            day, _, trading = self._time.at(t)
            figures = self._days.get(day)
            if figures is None:
                figures = self._days[day] = {
                    "first": t, "open": bid_open, "high": bid_high, "low": bid_low,
                    "bars": 0, "trading_bars": 0, "updates": 0,
                    "spread_sum": 0.0, "spread_count": 0, "spread_widest": None, "without_ask": 0,
                }
            figures["last"] = t
            figures["close"] = bid_close
            figures["high"] = max(figures["high"], bid_high)
            figures["low"] = min(figures["low"], bid_low)
            figures["bars"] += 1
            figures["updates"] += updates
            if trading:
                figures["trading_bars"] += 1
            if ask_close is None:
                figures["without_ask"] += 1
            elif self._spread:
                spread = ask_close - bid_close
                figures["spread_sum"] += spread
                figures["spread_count"] += 1
                figures["spread_widest"] = spread if figures["spread_widest"] is None else max(figures["spread_widest"], spread)

    def result(self) -> list[dict]:
        days = []
        for day in sorted(self._days):
            figures = self._days[day]
            trading_seconds = self._time.trading_seconds(day)
            days.append({
                "day": day,
                "first": _utc_text(figures["first"]),
                "last": _utc_text(figures["last"]),
                "bars": figures["bars"],
                "updates": figures["updates"],
                "open": figures["open"],
                "high": figures["high"],
                "low": figures["low"],
                "close": figures["close"],
                "change": figures["close"] - figures["open"],
                "change_percent": _percent(figures["close"] - figures["open"], figures["open"]),
                "range": figures["high"] - figures["low"],
                "range_percent": _percent(figures["high"] - figures["low"], figures["open"]),
                "spread_average": figures["spread_sum"] / figures["spread_count"] if figures["spread_count"] else None,
                "spread_widest": figures["spread_widest"],
                "without_ask": figures["without_ask"],
                "trading_seconds": trading_seconds,
                # The share of the day's trading time that has a bar: a bar is ten seconds.
                "covered": min(1.0, figures["trading_bars"] * BAR_SECONDS / trading_seconds) if trading_seconds else None,
            })
        return days


class SpreadByTime:
    """
    The spread of the bars that have an ask, by slots of ``minutes`` of the day
    (trading timezone): when an instrument is dear to trade. Fed a page of rows
    at a time, as DailyStatistics.
    """

    def __init__(self, timezone_name: str, minutes: int = SPREAD_SLOT_MINUTES):
        self._time = _LocalTime(timezone_name, lambda moment: False)
        self._minutes = minutes
        self._slots: dict[int, dict] = {}

    def add(self, rows: Iterable[Sequence]) -> None:
        for t, _bo, _bh, _bl, bid_close, _ao, _ah, _al, ask_close, _updates in rows:
            _, minute, _ = self._time.at(t)
            slot = minute // self._minutes
            figures = self._slots.get(slot)
            if figures is None:
                figures = self._slots[slot] = {"bars": 0, "sum": 0.0, "count": 0, "widest": None, "without_ask": 0}
            figures["bars"] += 1
            if ask_close is None:
                figures["without_ask"] += 1
                continue
            spread = ask_close - bid_close
            figures["sum"] += spread
            figures["count"] += 1
            figures["widest"] = spread if figures["widest"] is None else max(figures["widest"], spread)

    def result(self) -> list[dict]:
        return [
            {
                "slot": f"{slot * self._minutes // 60:02d}:{slot * self._minutes % 60:02d}",
                "bars": figures["bars"],
                "spread_average": figures["sum"] / figures["count"] if figures["count"] else None,
                "spread_widest": figures["widest"],
                "without_ask": figures["without_ask"],
            }
            for slot, figures in sorted(self._slots.items())
        ]


# ── what the providers are doing ──


def recorder_provider_state(enabled_on_disk: bool, state: tuple[str, str], running: bool) -> tuple[str, str]:
    """
    The price recorder as the Providers page says it: switched on or off in
    the configuration as it is on disk now, and what its last line of coverage
    says (``state``, from health.recorder_state; ``running``: written lately).
    """
    if enabled_on_disk:
        return state
    if running:
        return WARNING, "Switched off in the configuration: it records until it is restarted"
    return DISABLED, "Switched off in the configuration"


def history_provider_state(enabled: bool, fetch: dict | None, keep: dict | None) -> tuple[str, str]:
    """The Dukascopy downloads as the Providers page says them, from what the backtest service knows of them."""
    if not enabled:
        return DISABLED, "Switched off in the configuration (admin.backtests_enabled)"
    fetch, keep = fetch or {}, keep or {}
    if fetch.get("state") == "running":
        label = fetch.get("label") or BUILT_IN.label
        return OK, f"Downloading {fetch.get('from')} to {fetch.get('to')} ({label})"
    state = keep.get("state")
    if state == "backing_off":
        return WARNING, "Dukascopy stopped answering: the next download waits"
    if state in ("up_to_date", "fetching"):
        return OK, "Kept up to date"
    if state in ("due", "waiting_for_close", "pausing"):
        return OK, f"Kept up to date: {keep.get('missing', 0)} day(s) to fetch when the market is closed"
    return OK, "Fetched on demand"
