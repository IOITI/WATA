"""
What a signal rule, or a script, would have raised on recorded days: their bars
run through the real Pine runner as the engine runs them, its daily restart
included, and each alert set out with what the engine would have made of it.
Nothing is sent and nothing is written. Prints JSON.

    python -m src.ta_engine.replay --rule <id> --from 2026-09-15 [--to 2026-09-17]
    python -m src.ta_engine.replay --script <id> [--version 3] --asset dukascopy/us100 --bars R60 \\
        --from 2026-09-15 [--side ask] [--inputs '{"in_16": "DUKASCOPY_US100"}'] \\
        [--map 'Enter Long=long' --map 'Exit Long=close-position'] [--messages] [--indice us100]

The bars of capital/<EPIC> and saxo/<id> are read from the database of the
configuration (WATA_CONFIG_PATH), those of dukascopy/<label> from the day
files of the admin's price history (--prices to read others). The rules and
scripts are read from the admin's Technical Analysis folder (--root for
another).

Each day of the engine runs from one restart_time to the next, on its bars from
history_days before: an alert counts when its bar closed in that day.

The backtests run the same way (src/backtest/signals.py): ``AssetRows`` reads
the rows, ``ReplaySession`` gives the alerts of each of the engine's days.
"""

import argparse
import asyncio
import csv
import glob
import gzip
import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from src.admin.prices import price_decimals, range_points, tick_of
from src.admin.ta_rules import (
    ALERT_ACTIONS,
    IGNORE,
    MESSAGE,
    InvalidRule,
    RuleStore,
    bars_label,
    checked_alerts,
    checked_bars,
    checked_inputs,
    timeframe_seconds,
)
from src.admin.ta_scripts import ScriptNotFound, ScriptStore
from src.ta_engine.bars import RangeBars, TimeBars
from src.ta_engine.rules import EngineRule
from src.ta_engine.runner import RUNNER_PATH, NodeRunner, RunnerFailed, find_node, runner_command
from src.ta_engine.signals import Candidate, interpret, iso_seconds, settled
from src.ta_engine.tickers import Ticker, parse_ticker

DAY = 86_400
# The one rule the runner holds during a replay: each day is loaded again under it.
REPLAY_RULE_ID = "replay00000a"
# The bars of an engine day the runner is given at most. It held 590 MB at 49,107 bars of a
# timezone-heavy script (measured 2026-10-21), in an admin of 1.5 GB shared with the backtest's
# child: ten-second bars with two days of history (25,920) pass, a range far below the asset's
# tick (R600 on Dukascopy: 0.6 points, about 100,000 bars with a day of history) does not.
MAX_WINDOW_BARS = 40_000
# The ten-second rows given to a builder at a time, so that a day of too many bars is refused
# before they are all built: R1 on Dukascopy's thousandths makes thousands of bars of each row.
_BUILD_ROWS = 64
_ROW_COLUMNS = ("bid_open", "bid_high", "bid_low", "bid_close", "ask_open", "ask_high", "ask_low", "ask_close", "updates")


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m src.ta_engine.replay", description=__doc__.split("\n\n")[0])
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--rule", help="a signal rule, by its id")
    which.add_argument("--script", help="a script, by its id")
    parser.add_argument("--from", dest="first", required=True, type=date.fromisoformat, help="first day, YYYY-MM-DD")
    parser.add_argument("--to", dest="last", type=date.fromisoformat, help="last day (the first by default)")
    parser.add_argument("--version", type=int, help="the script's version (its current one by default)")
    parser.add_argument("--asset", help="capital/<EPIC>, saxo/<id> or dukascopy/<label>")
    parser.add_argument("--side", default="bid", choices=("bid", "ask"))
    parser.add_argument(
        "--bars", default="1",
        help="minutes (1, 5, 60), seconds (10S, 45S, 90S), from 10 seconds to a day, or R<ticks> for range bars",
    )
    parser.add_argument("--inputs", default="{}", help='the inputs set, as JSON by their id: {"in_0": 12}')
    parser.add_argument("--map", action="append", default=[], help="TITLE=ACTION for an alertcondition (repeatable)")
    parser.add_argument("--messages", action="store_true", help="read every alert, and every alert() call, as a message")
    parser.add_argument("--indice", default="us100")
    parser.add_argument("--history-days", type=int,
                        help="the days of bars before each day (the rule's own, else the engine's configuration)")
    parser.add_argument("--restart-time", help="the engine's restart_time, HH:MM UTC (its configuration by default)")
    parser.add_argument("--root", help="the Technical Analysis folder")
    parser.add_argument("--prices", help="the price history folder (day files)")
    parser.add_argument("--node", default="node")
    return parser.parse_args(argv)


def _config():
    path = os.getenv("WATA_CONFIG_PATH")
    if not path:
        return None
    from src.configuration import ConfigurationManager

    return ConfigurationManager(path)


def _bars_of(text: str) -> dict:
    text = text.strip()
    if text[:1] in ("R", "r"):
        return checked_bars({"type": "range", "ticks": int(text[1:]) if text[1:].isdigit() else 0})
    return checked_bars({"type": "time", "timeframe": text}, any_length=True)


def _script_rule(args, scripts: ScriptStore) -> dict:
    """A rule made of the command's arguments, run as the engine runs a rule."""
    if not args.asset or "/" not in args.asset:
        raise InvalidRule("--asset is capital/<EPIC>, saxo/<id> or dukascopy/<label>")
    provider, key = args.asset.split("/", 1)
    if provider not in ("capital", "dukascopy", "saxo"):
        raise InvalidRule("Replays read capital/<EPIC> and saxo/<id> from the database, dukascopy/<label> from day files")
    script = scripts.script(args.script)
    version = args.version or script["version"]
    store = RuleStore(scripts)
    pinned, libraries = store.pin(args.script, version)
    try:
        inputs = json.loads(args.inputs)
    except ValueError:
        raise InvalidRule("--inputs is JSON: {\"in_0\": 12}") from None
    if not isinstance(inputs, dict):
        raise InvalidRule("--inputs is a JSON object")
    alerts = []
    for mapping in args.map:
        title, _, action = mapping.rpartition("=")
        alerts.append({"title": title, "action": action.strip()})
    return {
        "id": REPLAY_RULE_ID,
        "name": f"{script['name']} (replay)",
        "asset": {"provider": provider, "key": key},
        "side": args.side,
        "bars": _bars_of(args.bars),
        "script": pinned,
        "libraries": libraries,
        "inputs": checked_inputs([{"id": input_id, "value": value} for input_id, value in inputs.items()]),
        "alerts": checked_alerts(alerts),
        "alert_calls": MESSAGE if args.messages else IGNORE,
        "confidence_plot": None,
        "indice": args.indice,
        "mode": "shadow",
    }


def engine_rule_of(rule: dict, scripts: ScriptStore) -> EngineRule:
    """A rule with the sources of its script and libraries, as the engine loads it."""
    return EngineRule(
        rule=rule,
        source=scripts.source(rule["script"]["id"], rule["script"]["version"]),
        libraries=tuple({**pin, "source": scripts.source(pin["id"], pin["version"])} for pin in rule["libraries"]),
    )


# ── the rows of an asset ──


def _epoch(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())


def _file_row(row: dict) -> tuple:
    def number(name):
        value = row.get(name)
        return None if value in (None, "") else float(value)

    return (_epoch(row["ts"]), *(number(name) for name in _ROW_COLUMNS[:-1]), int(float(row.get("updates") or 0)))


class AssetRows:
    """
    The ten-second rows of WATA's recorded assets (in the columns of
    src/ta_engine/source.py), from the database or from files:

    - ``capital/<EPIC>``: the Capital.com feed's tables, or its day files
      (``<files>/capital/bars/<label>/<day>.csv.gz``, the rows of the epic);
    - ``saxo/<id>``: the price recorder's ``price_bar_10s``, or the files of
      src/backtest/data.py (``<files>/saxo/*.csv.gz``, the rows of the id);
    - ``dukascopy/<label>``: the downloaded days under ``prices_root``.
    """

    def __init__(self, prices_root: str, db=None, files_root: str | None = None):
        self._prices_root = prices_root
        self._db = db
        self._files_root = files_root
        self._capital = None
        if db is not None:
            from src.ta_engine.source import CapitalTables

            self._capital = CapitalTables(db)
        self._instruments: dict = {}
        self._file_rows: dict = {}

    async def rows(self, provider: str, key: str, start: int, end: int) -> tuple[list, int | None]:
        if provider == "dukascopy":
            from src.admin.data import PriceHistory

            moment = lambda seconds: datetime.fromtimestamp(seconds, timezone.utc)  # noqa: E731
            history = PriceHistory(self._prices_root)
            return list(history.rows(key, moment(start), moment(end))), None
        if provider == "capital":
            if self._capital is not None:
                if key not in self._instruments:
                    self._instruments[key] = await self._capital.instrument(key)
                instrument = self._instruments[key]
                if instrument is None:
                    raise ValueError(f"The Capital.com feed has not recorded {key}")
                return await self._capital.rows(instrument["id"], start, end), instrument["decimals"]
            if self._files_root is not None:
                return self._capital_files(key, start, end), self._capital_decimals(key)
            raise ValueError("No database to read Capital.com's bars from (WATA_CONFIG_PATH)")
        if provider == "saxo":
            if not key.isdigit():
                raise ValueError(f"No Saxo instrument {key!r}")
            if self._db is not None:
                records = await self._db.fetch(
                    """
                    SELECT extract(epoch FROM ts)::bigint AS ts, bid_open, bid_high, bid_low, bid_close,
                           ask_open, ask_high, ask_low, ask_close, updates
                    FROM price_bar_10s
                    WHERE instrument_id = $1 AND ts >= $2 AND ts < $3
                    ORDER BY ts
                    """,
                    int(key), datetime.fromtimestamp(start, timezone.utc), datetime.fromtimestamp(end, timezone.utc),
                )
                return [tuple(record[name] for name in ("ts",) + _ROW_COLUMNS) for record in records], None
            if self._files_root is not None:
                return self._saxo_files(int(key), start, end), None
            raise ValueError("No database to read Saxo's bars from (WATA_CONFIG_PATH)")
        raise ValueError(f"Replays have no bars of {provider}/{key}")

    def _from_files(self, key: str, pattern: str, keep) -> list[tuple]:
        """The rows of the files ``pattern`` names that ``keep`` keeps, read once, oldest first."""
        if key not in self._file_rows:
            found = []
            for path in sorted(glob.glob(pattern)):
                with gzip.open(path, "rt", newline="", encoding="utf-8") as in_file:
                    found += [_file_row(row) for row in csv.DictReader(in_file) if keep(row)]
            found.sort(key=lambda row: row[0])
            self._file_rows[key] = found
        return self._file_rows[key]

    def _capital_files(self, epic: str, start: int, end: int) -> list[tuple]:
        pattern = os.path.join(self._files_root, "capital", "bars", "*", "*.csv.gz")
        rows = self._from_files(f"capital:{epic}", pattern, lambda row: row.get("symbol") == epic)
        return [row for row in rows if start <= row[0] < end]

    def _capital_decimals(self, epic: str) -> int | None:
        """
        The epic's decimals, read from its day files, which name no instrument: those its
        prices carry (US100's one), as the feed's table says them. Saxo's floor of two
        would make R60 0.6 points of US100 instead of the 6 the TA engine counts.
        """
        found = self._file_rows.get(f"capital:{epic}") or []
        return price_decimals([row[4] for row in found[:2000]], minimum=0) if found else None

    def _saxo_files(self, instrument_id: int, start: int, end: int) -> list[tuple]:
        pattern = os.path.join(self._files_root, "saxo", "*.csv.gz")
        rows = self._from_files(
            f"saxo:{instrument_id}", pattern,
            lambda row: bool(row.get("instrument_id")) and int(row["instrument_id"]) == instrument_id,
        )
        return [row for row in rows if start <= row[0] < end]


class DayTooLarge(ValueError):
    """An engine day of more bars than the runner is given: a range small for the asset's tick, or a long history."""


def _decimals(rows: list, decimals: int | None) -> int:
    """The decimals of an asset's prices: its own when its feed says them (Capital.com), else read from its bids."""
    return decimals if decimals is not None else price_decimals([row[4] for row in rows[:2000]])


def _builder(bars: dict, side: str, rows: list, decimals: int | None):
    if bars["type"] == "time":
        return TimeBars(timeframe_seconds(bars["timeframe"]), side)
    return RangeBars(bars["ticks"], _decimals(rows, decimals), side)


def _decimal(value: float, decimals: int) -> str:
    return f"{value:.{decimals}f}".rstrip("0").rstrip(".")


def too_large(asset: str, bars: dict, decimals: int | None) -> DayTooLarge:
    """
    Why an engine day of more than MAX_WINDOW_BARS bars is not given to the
    runner, saying what the range is on this asset: a tick is the asset's own,
    so R60 is 6 points on Capital.com's US100 and 0.06 on Dukascopy's.
    """
    label = bars_label(bars)
    if bars["type"] == "range":
        named = label
    else:
        seconds = timeframe_seconds(bars["timeframe"])
        named = f"{seconds // 60} min" if seconds % 60 == 0 else f"{seconds} s"
    said = f"This engine day has more than {MAX_WINDOW_BARS:,} bars of {named} on {asset}"
    if bars["type"] == "range" and decimals is not None:
        size = range_points(bars["ticks"], decimals)
        return DayTooLarge(
            f"{said}: a tick of this asset is {_decimal(tick_of(decimals), decimals)}, so {label} is "
            f"{_decimal(size, decimals)} point{'' if size == 1 else 's'}; one point is R{10 ** decimals} here"
        )
    return DayTooLarge(f"{said}: longer bars, or fewer days of history")


def built_bars(builder, rows: list, end: int, refusal: DayTooLarge) -> list:
    """The bars of an engine day's rows, built a few rows at a time: ``refusal`` is raised once they are too many."""
    bars: list = []
    for start in range(0, len(rows), _BUILD_ROWS):
        bars += builder.add(rows[start:start + _BUILD_ROWS])
        if len(bars) > MAX_WINDOW_BARS:
            raise refusal
    bars += builder.add([], end)
    if len(bars) > MAX_WINDOW_BARS:
        raise refusal
    return bars


def engine_windows(start: int, end: int, restart_minute: int) -> list[tuple[int, int]]:
    """The engine's days that cover [start, end): from one restart (UTC) to the next."""
    first = datetime.fromtimestamp(start, timezone.utc)
    restart = first.replace(hour=restart_minute // 60, minute=restart_minute % 60, second=0, microsecond=0)
    if restart > first:
        restart -= timedelta(days=1)
    windows = []
    moment = int(restart.timestamp())
    while moment < end:
        windows.append((moment, moment + DAY))
        moment += DAY
    return windows


# ── a replay ──


@dataclass
class ReplayedAlert:
    alert: dict
    # The bar of the strategy it was raised on: [open time ms, open, high, low, close, volume, close time ms].
    bar: list
    candidate: Candidate | None


@dataclass
class ReplayedWindow:
    begin: int
    end: int
    history_from: int
    bars: int
    milliseconds: int | None = None
    warnings: list[str] = field(default_factory=list)
    alerts: list[ReplayedAlert] = field(default_factory=list)
    # The bars of the strategy of the window, for whoever wants to keep them.
    strategy_bars: list = field(default_factory=list)
    # A window run in detail (a study): each plot at every bar, and PineTS's runtime warnings.
    series: dict | None = None
    runtime_warnings: list = field(default_factory=list)


# Raised when what is kept of a run changes: every answer kept before is then not read again.
CACHE_FORMAT = 1


def _runner_digest(command: list[str]) -> str:
    """
    What answers: the runner's built file when it is the runner, else the command (a stand-in, in tests).
    Not the heap it is given: a study (a smaller heap) and a backtest read the same answers.
    """
    digest = hashlib.sha256(json.dumps([part for part in command if not part.startswith("--max-old-space-size")]).encode())
    if os.path.isfile(RUNNER_PATH) and RUNNER_PATH in command:
        with open(RUNNER_PATH, "rb") as runner_file:
            digest.update(runner_file.read())
    return digest.hexdigest()


class ReplaySession:
    """
    One Node runner, and a rule run day by day as the engine runs it: each of
    the engine's days loaded with its bars from ``history_days`` before, its
    ``request.security`` asks answered with the bars of that same span.

    With a ``cache`` folder, the runner's answer for a day is kept, by what
    decides it: the script and its libraries, the inputs, the ticker and
    timeframe, the bars (their digest), and the runner itself. The same run
    asked again (another setting of WATA's rules, another mapping of the
    alerts) is read back instead of run. A day that asked for another series
    (``request.security``) depends on that series too: it is not kept.
    """

    def __init__(self, rule: dict, engine_rule: EngineRule, rows: AssetRows, *, node: str = "node",
                 history_days: int = 1, restart_minute: int = 21 * 60 + 15, messages: bool = False,
                 memory_mb: int = 1024, timeout_seconds: int = 600, command: list[str] | None = None,
                 cache: str | None = None):
        self.rule = rule
        self.engine_rule = engine_rule
        self.rows = rows
        self.history_days = history_days
        self.restart_minute = restart_minute
        self.messages = messages
        if command is None:
            node_path = find_node(node)
            if node_path is None or not os.path.isfile(RUNNER_PATH):
                raise ValueError("Node, or the Pine runner (npm run build:runner in admin_ui), is missing")
            command = runner_command(node_path, RUNNER_PATH, memory_mb)
        # Another command only for tests: a stand-in that answers as the runner does.
        self.runner = NodeRunner(command, self._answer, timeout_seconds=timeout_seconds)
        self._current: dict = {}
        self.cache = cache
        self._runner_digest = _runner_digest(command) if cache else ""
        self._asked_series = False
        self.cached_windows = 0
        self.run_windows = 0

    @property
    def versions(self) -> dict:
        return self.runner.versions

    async def start(self) -> None:
        await self.runner.start()

    async def stop(self) -> None:
        await self.runner.stop()

    async def _run(self, request: dict) -> dict:
        """The runner's answer to a day's load: read back from the cache when the same day was run before."""
        if not self.cache:
            self.run_windows += 1
            return await self.runner.request(request)
        described = {key: value for key, value in request.items() if key != "bars"}
        described.update(
            format=CACHE_FORMAT, runner=self._runner_digest,
            bars=hashlib.sha256(json.dumps(request["bars"]).encode()).hexdigest(),
        )
        key = hashlib.sha256(json.dumps(described, sort_keys=True, default=str).encode()).hexdigest()
        path = os.path.join(self.cache, key[:2], f"{key}.json")
        try:
            with open(path, encoding="utf-8") as in_file:
                answer = json.load(in_file)
            self.cached_windows += 1
            return answer
        except (OSError, ValueError):
            pass
        self._asked_series = False
        answer = await self.runner.request(request)
        self.run_windows += 1
        if not self._asked_series:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            partial = f"{path}.{os.getpid()}.part"
            with open(partial, "w", encoding="utf-8") as out_file:
                json.dump(answer, out_file)
            os.replace(partial, path)
        return answer

    async def _answer(self, ask: dict) -> list:
        self._asked_series = True
        ticker = parse_ticker(str(ask.get("ticker") or ""))
        if ticker is None:
            raise ValueError(f"No such asset in WATA: {ask.get('ticker')!r}")
        timeframe = str(ask.get("timeframe") or "")
        seconds = timeframe_seconds(timeframe)
        if ticker.range_ticks is None and seconds is None:
            raise ValueError(f"No bars of {timeframe!r}: from 10 seconds (10S) to a day (1440)")
        found, decimals = await self.rows.rows(ticker.provider, ticker.key, self._current["anchor"], self._current["end"])
        if ticker.range_ticks:
            decimals = _decimals(found, decimals)
            asked = {"type": "range", "ticks": ticker.range_ticks}
            builder = RangeBars(ticker.range_ticks, decimals, ticker.side)
        else:
            asked = {"type": "time", "timeframe": timeframe}
            builder = TimeBars(seconds, ticker.side)
        bars = built_bars(builder, found, self._current["end"], too_large(f"{ticker.provider}/{ticker.key}", asked, decimals))
        start, end = ask.get("from"), ask.get("to")
        return [bar for bar in bars if (start is None or bar[0] >= start) and (end is None or bar[0] <= end)]

    async def window(self, begin: int, end: int, *, period: tuple[int, int] | None = None,
                     detail: bool = False) -> ReplayedWindow:
        """
        One day of the engine. Its alerts are those whose bar closed in it, and
        began in ``period`` when one is given. Raises RunnerFailed when the
        script fails on its bars. In ``detail`` (a study of the script, never a
        backtest), each plot at every bar comes back too; such a run is never kept.
        """
        rule, engine_rule = self.rule, self.engine_rule
        anchor = begin - self.history_days * DAY
        self._current.update(anchor=anchor, end=end)
        found, decimals = await self.rows.rows(rule["asset"]["provider"], rule["asset"]["key"], anchor, end)
        if rule["bars"]["type"] == "range":
            decimals = _decimals(found, decimals)
        refusal = too_large(f"{rule['asset']['provider']}/{rule['asset']['key']}", rule["bars"], decimals)
        bars = built_bars(_builder(rule["bars"], rule["side"], found, decimals), found, end, refusal)
        window = ReplayedWindow(begin=begin, end=end, history_from=anchor, bars=len(bars), strategy_bars=bars)
        if not bars:
            return window
        request = {
            "op": "load", "rule": REPLAY_RULE_ID,
            "script": {"source": engine_rule.source, "sha256": rule["script"]["sha256"]},
            "libraries": [
                {key: entry[key] for key in ("id", "name", "version", "source", "sha256")}
                for entry in engine_rule.libraries
            ],
            "inputs": engine_rule.inputs(),
            "ticker": Ticker(rule["asset"]["provider"], rule["asset"]["key"], rule["side"], rule["bars"].get("ticks")).text(),
            "timeframe": engine_rule.timeframe(),
            "bars": bars,
        }
        if detail:
            self.run_windows += 1
            answer = await self.runner.request({**request, "detail": True})
            window.series = answer.get("series")
            window.runtime_warnings = list(answer.get("runtimeWarnings") or [])
        else:
            answer = await self._run(request)
        window.milliseconds = answer.get("milliseconds")
        window.warnings = list(answer.get("warnings") or [])
        by_time = {bar[0]: bar for bar in bars}
        for alert in answer["alerts"]:
            bar = by_time.get(alert["time"])
            # The engine's day: the bars that closed in it.
            if bar is None or not begin * 1000 < bar[6] <= end * 1000:
                continue
            if period is not None and not period[0] * 1000 <= bar[0] < period[1] * 1000:
                continue
            title = alert.get("title")
            if self.messages and alert["type"] == "alertcondition" and title and not any(
                entry["title"] == title for entry in rule["alerts"]
            ):
                # Every alertcondition the rule did not map is read as a message.
                rule["alerts"].append({"title": title, "action": MESSAGE})
            candidate = interpret(
                rule, alert, bar=bar, ticker=engine_rule.ticker().text(), timeframe=engine_rule.timeframe(),
                plots=answer.get("plots") or {}, now=bar[6] / 1000,
            )
            window.alerts.append(ReplayedAlert(alert=alert, bar=bar, candidate=candidate))
        # One trade a bar, as the engine sends them: an entry wins over a close of the same bar.
        by_bar: dict[int, list[ReplayedAlert]] = {}
        for replayed in window.alerts:
            if replayed.candidate is not None:
                by_bar.setdefault(replayed.bar[0], []).append(replayed)
        for of_bar in by_bar.values():
            for replayed, candidate in zip(of_bar, settled([entry.candidate for entry in of_bar])):
                replayed.candidate = candidate
        return window


def ta_settings(config) -> tuple[str, str, int, str]:
    """The Technical Analysis folder, the price history, and the engine's history_days and restart_time."""
    from src.admin.context import AdminSettings
    from src.ta_engine.settings import TaEngineSettings

    if config is not None:
        admin = AdminSettings.from_config(config)
        ta_root, prices_root = admin.ta_path, admin.price_history_path
    else:
        ta_root, prices_root = AdminSettings.ta_path, AdminSettings.price_history_path
    engine = TaEngineSettings.from_config(config) if config is not None else TaEngineSettings()
    return ta_root, prices_root, engine.history_days, engine.restart_time


def restart_minute_of(restart: str) -> int:
    return int(restart[:2]) * 60 + int(restart[3:5])


async def asset_rows_for(config, prices_root: str, provider: str, files_root: str | None = None):
    """
    The rows of an asset of ``provider``, and the read-only connection they are
    read through (None for Dukascopy's files, or for the recorded days of
    ``files_root``): close it once done.
    """
    pg = None
    if files_root is not None:
        return AssetRows(prices_root, None, files_root=files_root), None
    if provider in ("capital", "saxo"):
        from src.database.postgres import PostgresConnectionManager

        dsn = (config.get_config_value("postgresql", {}) or {}).get("dsn") if config is not None else None
        if not dsn:
            raise ValueError("No database to read Capital.com's or Saxo's bars from (WATA_CONFIG_PATH)")
        pg = PostgresConnectionManager(dsn=dsn, min_size=1, max_size=1, server_settings={"default_transaction_read_only": "on"})
        await pg.connect()
    return AssetRows(prices_root, pg), pg


async def replay(args: argparse.Namespace) -> dict:
    config = _config()
    ta_root, prices_root, history_days, restart = ta_settings(config)
    restart = restart if args.restart_time is None else args.restart_time
    scripts = ScriptStore(args.root or ta_root)
    if args.rule:
        rule = RuleStore(scripts).rule(args.rule)
    else:
        rule = _script_rule(args, scripts)
    RuleStore(scripts).verify(rule)
    # As the engine runs it: the rule's own history when it has one.
    history_days = rule.get("history_days", history_days) if args.history_days is None else args.history_days
    engine_rule = engine_rule_of(rule, scripts)

    rows, pg = await asset_rows_for(config, args.prices or prices_root, rule["asset"]["provider"])

    first_day = datetime.combine(args.first, datetime.min.time(), timezone.utc)
    last_day = datetime.combine(args.last or args.first, datetime.min.time(), timezone.utc) + timedelta(days=1)
    period = (int(first_day.timestamp()), int(last_day.timestamp()))
    session = ReplaySession(
        rule, engine_rule, rows, node=args.node, history_days=history_days,
        restart_minute=restart_minute_of(restart), messages=args.messages,
    )
    days, alerts, warnings = [], [], []
    try:
        await session.start()
        for begin, end in engine_windows(period[0], period[1], session.restart_minute):
            try:
                window = await session.window(begin, end, period=period)
            except RunnerFailed as e:
                return {"error": str(e), "where": e.error}
            if not window.bars:
                days.append({"from": iso_seconds(begin), "to": iso_seconds(end), "bars": 0})
                continue
            warnings += [warning for warning in window.warnings if warning not in warnings]
            for replayed in window.alerts:
                candidate, bar, alert = replayed.candidate, replayed.bar, replayed.alert
                alerts.append({
                    "bar_time": iso_seconds(bar[0] / 1000),
                    "bar_closed_at": iso_seconds(bar[6] / 1000),
                    "type": alert["type"],
                    "title": alert.get("title"),
                    "message": alert.get("message"),
                    "signal": None if candidate is None else {
                        "action": candidate.action, "confidence": candidate.confidence, "detail": candidate.detail,
                    },
                })
            days.append({
                "from": iso_seconds(begin), "to": iso_seconds(end), "bars": window.bars,
                "history_from": iso_seconds(window.history_from), "milliseconds": window.milliseconds,
                "alerts": len(window.alerts),
            })
    finally:
        await session.stop()
        if pg is not None:
            await pg.close()
    return {
        "rule": {
            "id": rule["id"], "name": rule["name"], "asset": f"{rule['asset']['provider']}/{rule['asset']['key']}",
            "side": rule["side"], "bars": bars_label(rule["bars"]), "indice": rule["indice"],
            "script": {"id": rule["script"]["id"], "version": rule["script"]["version"]},
            "libraries": [{"name": entry["name"], "version": entry["version"]} for entry in rule["libraries"]],
        },
        "engine": {"history_days": history_days, "restart_time": restart, "runner": session.versions},
        "days": days,
        "warnings": warnings,
        "alerts": alerts,
        "signals": sum(1 for alert in alerts if alert["signal"] and alert["signal"]["action"]),
    }


def main(argv: list[str] | None = None) -> int:
    args = _parse(sys.argv[1:] if argv is None else argv)
    for mapping in args.map:
        if "=" not in mapping or mapping.rpartition("=")[2].strip() not in ALERT_ACTIONS:
            print(f"--map {mapping!r}: TITLE=ACTION, the action one of {', '.join(ALERT_ACTIONS)}", file=sys.stderr)
            return 2
    try:
        result = asyncio.run(replay(args))
    except (InvalidRule, ScriptNotFound, ValueError, OSError) as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0 if "error" not in result else 1


if __name__ == "__main__":
    sys.exit(main())
