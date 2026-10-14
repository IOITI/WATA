"""
Backtests asked for from the admin interface.

A backtest is the command of src/strategy/backtest.py run as a child process:
it can be stopped, it cannot slow the admin's own answers, and it gives way to
everything else on a machine that trades (lowest priority, one at a time).

Each run is a folder of files under the backtests directory: what was asked,
the rules it ran with, its state, and what the command wrote. Nothing goes to
the database.

Prices come from the folder the downloader fills
(src/strategy/history/dukascopy.py), which is run the same way: one download
at a time, which is also what its server tolerates. Asked for from the page,
or by the service itself when it is told to keep the prices up to date
(``keep_prices``): then it fetches what is missing a few days at a time, only
while the market is closed, and waits longer each time the server stops
answering.

Two things are checked against what WATA really did, with rows the routes read
from the database and hand over (this module never reads it): the signals WATA
received are replayed as a backtest and its trades set beside the real ones
(``create_from_signals``, ``comparison``), and the turbo the backtests assume
is measured on the closed trades (``start_calibration``).

None of this acts on the trading services, so it is neither in the audit
trail nor announced on Telegram.
"""

import asyncio
import csv
import json
import logging
import os
import re
import secrets
import shutil
import sys
import time
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timedelta, timezone
from typing import Awaitable, Callable, Protocol

from src.admin.analysis import HOLDING_LABELS, exit_group, given_back, holding_bucket, stats_by
from src.admin.data import PriceHistory
from src.price_recorder.bars import BAR_SECONDS
from src.strategy import backtest, signals, sweep
from src.strategy.comparison import Leg, compare
from src.strategy.engine import LONG, SHORT, LocalClock
from src.strategy.execution import TurboModel, WataRules, compounded
from src.strategy.fingerprint import code_fingerprint, file_digest, prices_fingerprint
from src.strategy.history import dukascopy
from src.strategy.sources import FileBars, files_of_period

logger = logging.getLogger(__name__)

APP_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"
# The admin stopped while the run was waiting or running.
INTERRUPTED = "interrupted"

# A download that ended with days still missing: the usual end of one.
INCOMPLETE = "incomplete"
STOPPED = "stopped"
IDLE = "idle"

MAX_PERIOD_DAYS = 366
MAX_LEVERAGES = 5
MAX_QUEUED = 10
MAX_NOTE_LENGTH = 200
MAX_LISTED = 200
# A year in which the rules hold every position for the day, on a small machine at the lowest priority.
RUN_TIMEOUT_SECONDS = 3 * 3600
FETCH_MAX_DAYS = 92
FETCH_TIMEOUT_SECONDS = 6 * 3600
OUTPUT_TAIL_LINES = 40
DEFAULT_LEVERAGES = (80.0, 110.0, 140.0)
CALIBRATION_TIMEOUT_SECONDS = 1800
# How unkind the fills of a run may be asked to be, and the account its money is counted on.
MAX_FILL_DELAY_SECONDS = 600
MAX_SLIPPAGE_POINTS = 100.0
MAX_CAPITAL = 1e9
# A measured leverage whose range is narrower than this share of it is used alone.
NARROW_RANGE = 0.05

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

# 20261012-213045-a1b2c3: made here, and the only shape a folder is ever looked for with.
RUN_ID = re.compile(r"\d{8}-\d{6}-[0-9a-f]{6}")
FETCH_FOLDER = "_fetch"
CALIBRATION_FOLDER = "_calibration"

KIND_STRATEGY, KIND_SIGNALS, KIND_SWEEP = "strategy", "signals", "sweep"
# Combinations one sweep may try: each is a backtest's worth of trading, in one pass over the prices.
MAX_SWEEP_CELLS = 64
MAX_SWEEP_VALUES = 12
MAX_TAGS = 6
MAX_TAG_LENGTH = 24
# The strategy whose range bars and lines are drawn behind replayed signals: the one TradingView runs.
CHART_STRATEGY = "kernel_vwap"

TRADES, DAYS, REFUSALS = "trades", "days", "refusals"
_NUMBERS = {
    TRADES: (
        "confidence", "entry_index", "exit_index", "index_points", "performance_percent", "max_performance_percent",
        "min_performance_percent", "scale_percent", "stake", "profit_loss",
    ),
    DAYS: ("entries", "closed", "day_percent", "profit_loss", "equity"),
    REFUSALS: (),
}
_WHOLE = ("entries", "closed")

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
# The confidences an entry is put in: (under, label). Without one: "Not given".
_CONFIDENCES = ((0.5, "Under 0.5"), (1.0, "0.5 to 1"), (1.5, "1 to 1.5"), (float("inf"), "1.5 and above"))
NO_CONFIDENCE = "Not given"
# The points of a trade's path a chart is given at most: beyond, they are put together.
MAX_PATH_POINTS = 1500

# How the model says a trade ended, in the words the admin has for real ones (``exit_group``).
_MODEL_EXITS = (
    ("Stoploss", "Stop-loss"),
    ("Knocked out", "Stop-loss"),
    ("Takeprofit", "Take-profit"),
    ("Trailing stop", "Trailing stop"),
    ("Daily profit target", "Daily profit target"),
    ("End of day", "Signal or end of day"),
    ("Signal", "Signal or end of day"),
)


class InvalidRequest(ValueError):
    """What was asked makes no sense; the message says why, for whoever asked."""


class UnknownRun(LookupError):
    pass


class NotPossible(RuntimeError):
    """Not in the state the request needs."""


@dataclass(frozen=True)
class EditableRule:
    """A rule of WATA that one backtest may be run with another value of."""

    name: str
    kind: str
    label: str
    least: float | None = None
    most: float | None = None


EDITABLE_RULES = (
    EditableRule("stoploss_percent", "number", "Stop-loss, in percent of the turbo", -100.0, -0.1),
    EditableRule("max_profit_percent", "number", "Take-profit, in percent", 0.1, 10000.0),
    EditableRule("trailing_enabled", "boolean", "Trailing stop"),
    EditableRule("trailing_activation_percent", "number", "Gain that arms the trailing stop, in percent", 0.0, 1000.0),
    EditableRule("trailing_drawdown_percent", "number", "Fall from the peak that sells, in percent", 0.1, 1000.0),
    EditableRule("day_profit_target", "number", "Day's result at which the position is sold, in percent", 0.01, 10000.0),
    EditableRule("no_entry_above_day_profit", "number", "No new entry once the day is above, in percent", 0.01, 10000.0),
    EditableRule("max_day_loss", "number", "No new entry once the day is below, in percent", -100.0, -0.01),
    EditableRule("cooldown_after_loss_minutes", "integer", "Minutes without entry after a loss", 0, 1440),
    EditableRule("max_trades_per_day", "integer", "Entries per day at most (0: no limit)", 0, 1000),
    EditableRule("min_confidence", "number", "Entries under this confidence are refused (0: none is)", 0.0, 100.0),
)
_EDITABLE = {rule.name: rule for rule in EDITABLE_RULES}


def checked_rules(changes: dict) -> dict:
    """The rules changed for a run, each of the kind and within the bounds it must have."""
    checked = {}
    for name, value in changes.items():
        rule = _EDITABLE.get(name)
        if rule is None:
            raise InvalidRequest(f"The rule '{name}' cannot be changed for a backtest")
        if rule.kind == "boolean":
            if not isinstance(value, bool):
                raise InvalidRequest(f"The rule '{name}' is on or off")
            checked[name] = value
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise InvalidRequest(f"The rule '{name}' needs a number")
        if rule.kind == "integer" and value != int(value):
            raise InvalidRequest(f"The rule '{name}' needs a whole number")
        if not rule.least <= value <= rule.most:
            raise InvalidRequest(f"The rule '{name}' must be between {rule.least:g} and {rule.most:g}")
        checked[name] = int(value) if rule.kind == "integer" else float(value)
    return checked


class Process(Protocol):
    returncode: int | None

    async def wait(self) -> int: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


# Runs ``python -m <arguments>`` with its output kept in the file named.
Launcher = Callable[[list[str], str], Awaitable[Process]]


async def launch(arguments: list[str], log_path: str) -> Process:
    """A module of the application as a child process, from the application's directory."""
    python_path = os.pathsep.join(
        part for part in (APP_ROOT, os.path.join(APP_ROOT, "src"), os.environ.get("PYTHONPATH")) if part
    )
    with open(log_path, "ab") as log:
        return await asyncio.create_subprocess_exec(
            sys.executable, "-u", "-m", *arguments,
            stdin=asyncio.subprocess.DEVNULL, stdout=log, stderr=asyncio.subprocess.STDOUT,
            cwd=APP_ROOT, env={**os.environ, "PYTHONPATH": python_path},
        )


def _write_json(path: str, content: dict) -> None:
    partial = f"{path}.part"
    with open(partial, "w", encoding="utf-8") as out_file:
        json.dump(content, out_file, indent=2)
    os.replace(partial, path)


def _read_json(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as in_file:
            return json.load(in_file)
    except (OSError, ValueError):
        return None


def _tail(path: str, lines: int = OUTPUT_TAIL_LINES) -> list[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as in_file:
            return [line.rstrip("\n") for line in in_file.readlines()[-lines:]]
    except OSError:
        return []


def _utc(moment: float) -> str:
    return datetime.fromtimestamp(moment, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _epoch(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())


def _folder_bytes(path: str) -> int:
    """The size of everything under a folder; 0 when it is not there."""
    total = 0
    for folder, _, names in os.walk(path):
        for name in names:
            try:
                total += os.path.getsize(os.path.join(folder, name))
            except OSError:
                pass
    return total


def _moment(value) -> int | None:
    """Epoch seconds of a time as the database gives it, or as it was written to a file."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return int((value if value.tzinfo else value.replace(tzinfo=timezone.utc)).timestamp())
    if isinstance(value, (int, float)):
        return int(value)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return int((parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).timestamp())


def signals_to_replay(journal: list[dict], indice: str) -> list[dict]:
    """
    The trader's journal as the signals a backtest replays, oldest first:
    the entries asked for on the indice, and every close (the scheduler's
    end of day names no indice). What WATA made of each is kept beside it.
    """
    replayed = []
    for row in journal:
        action = row.get("action")
        if action not in signals.ACTIONS:
            continue
        entry = signals.ACTIONS[action][0] in (LONG, SHORT)
        if entry and str(row.get("indice") or "").lower() != indice.lower():
            continue
        # When the trader dealt with it: the moment its order, if any, was sent.
        at = _moment(row.get("decided_at")) or _moment(row.get("received_timestamp")) or _moment(row.get("signal_timestamp"))
        if at is None:
            continue
        replayed.append({
            "at": _utc(at),
            "action": action,
            "confidence": row.get("confidence"),
            "outcome": row.get("outcome"),
            "reason": row.get("reason"),
            "signal_uuid": row.get("signal_uuid"),
            "position_id": row.get("position_id"),
        })
    return sorted(replayed, key=lambda signal: signal["at"])


def real_trades_of(positions: list[dict], journal: list[dict], indice: str) -> list[dict]:
    """
    WATA's closed positions as trades to compare with, or to measure the turbo
    on, oldest first. One that the journal ties to another indice is left out;
    one from before there was a journal is kept.
    """
    indice_of = {row["position_id"]: str(row.get("indice") or "").lower() for row in journal if row.get("position_id")}
    trades = []
    for position in positions:
        opened_at, closed_at = _moment(position.get("execution_time_open")), _moment(position.get("execution_time_close"))
        direction = position.get("action")
        if opened_at is None or closed_at is None or direction not in (LONG, SHORT):
            continue
        if indice_of.get(position.get("position_id"), indice.lower()) != indice.lower():
            continue
        trades.append({
            "position_id": position.get("position_id"),
            "direction": direction,
            "opened_at": _utc(opened_at),
            "closed_at": _utc(closed_at),
            "open_price": position.get("position_open_price"),
            "close_price": position.get("position_close_price"),
            "performance_percent": position.get("position_total_performance_percent"),
            "max_performance_percent": position.get("position_max_performance_percent"),
            "close_reason": position.get("position_close_reason"),
            "instrument_name": position.get("instrument_name"),
        })
    return sorted(trades, key=lambda trade: trade["opened_at"])


def _model_exit(reason: str) -> str:
    for marker, label in _MODEL_EXITS:
        if reason.startswith(marker):
            return label
    return "Other"


def _confidence_group(confidence: float | None) -> str:
    if confidence is None:
        return NO_CONFIDENCE
    return next(label for under, label in _CONFIDENCES if confidence < under)


def _decimals_of(tick: float) -> int:
    """Decimals a price counted in steps of ``tick`` is written with: 1 for 0.1."""
    text = f"{tick:.10f}".rstrip("0")
    return len(text) - text.index(".") - 1


class BacktestService:
    def __init__(
        self,
        *,
        runs_root: str,
        prices_root: str,
        rules: Callable[[], WataRules],
        launcher: Launcher = launch,
        publish: Callable[..., None] | None = None,
        clock: Callable[[], float] = time.time,
        indice: str = backtest.DEFAULT_INDICE,
    ):
        self._runs_root = runs_root
        self._prices_root = prices_root
        self._rules = rules
        self._launcher = launcher
        self._publish = publish
        self._clock = clock
        self._indice = indice
        # The Dukascopy instruments that can be fetched, and the days of each (src/admin/data.py).
        self._history = PriceHistory(prices_root)

        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._waiting: list[str] = []
        self._cancelled: set[str] = set()
        self._running: str | None = None
        self._process: Process | None = None
        self._fetch_process: Process | None = None
        self._fetch_task: asyncio.Task | None = None
        self._fetch_stopped = False
        self._calibration_process: Process | None = None
        self._calibration_task: asyncio.Task | None = None
        # What a backtest made now would be made with: read once, the code does not change under a running admin.
        self._code = code_fingerprint()
        # The digest of each file of prices, kept for as long as the file is the same: path -> (size, time, digest).
        self._digests: dict[str, tuple[int, int, str]] = {}

    # ── what a form needs ──

    def live_rules(self) -> WataRules:
        try:
            return self._rules()
        except (OSError, ValueError, KeyError, TypeError) as e:
            raise NotPossible(f"The rules of the live configuration cannot be read: {e}") from e

    def options(self) -> dict:
        rules = self.live_rules()
        return {
            "strategies": [
                {"name": name, "parameters": backtest.describe_parameters(name)} for name in sorted(backtest.STRATEGIES)
            ],
            "turbo": self.default_turbo(),
            # How the fills are taken and the account the money is counted on, unless a run says otherwise.
            "execution": {"capital": backtest.DEFAULT_CAPITAL, "fill_delay": 0, "slippage": 0.0},
            "rules": {"live": rules.to_dict(), "editable": [asdict(rule) for rule in EDITABLE_RULES]},
            "limits": {
                "period_days": MAX_PERIOD_DAYS, "leverages": MAX_LEVERAGES, "queued": MAX_QUEUED,
                "fetch_days": FETCH_MAX_DAYS, "note_length": MAX_NOTE_LENGTH,
                "fill_delay": MAX_FILL_DELAY_SECONDS, "slippage": MAX_SLIPPAGE_POINTS,
                "sweep_cells": MAX_SWEEP_CELLS, "sweep_values": MAX_SWEEP_VALUES,
                "tags": MAX_TAGS, "tag_length": MAX_TAG_LENGTH,
            },
            # The share of the days a sweep holds out, and between what it can be set.
            "held_out": {"share": sweep.DEFAULT_HELD_OUT, "least": sweep.LEAST_HELD_OUT, "most": sweep.MOST_HELD_OUT},
            "timezone": rules.timezone,
        }

    def default_turbo(self) -> dict:
        """
        The turbo a backtest is run with unless it says otherwise: the one
        measured on real trades when there is one, else what is assumed.
        """
        measured = _read_json(self._calibration_file("result.json")) or {}
        if not measured.get("fitted"):
            return {
                "entry_price": TurboModel.entry_price, "spread": TurboModel.spread,
                "leverages": list(DEFAULT_LEVERAGES), "measured": False,
            }
        leverage, low, high = (measured[name] for name in ("points_per_unit", "points_per_unit_low", "points_per_unit_high"))
        # One column when the trades pin it down, the two ends of its range beside it when they do not.
        if high is None:
            leverages = [low, leverage]
        elif (high - low) / leverage < NARROW_RANGE:
            leverages = [leverage]
        else:
            leverages = [low, leverage, high]
        return {
            "entry_price": measured["entry_price"], "spread": measured["spread"],
            "leverages": sorted({round(value, 1) for value in leverages}), "measured": True,
        }

    # ── folders ──

    def _directory(self, run_id: str) -> str:
        if not RUN_ID.fullmatch(run_id):
            raise UnknownRun(run_id)
        path = os.path.join(self._runs_root, run_id)
        if not os.path.isdir(path):
            raise UnknownRun(run_id)
        return path

    def _file(self, run_id: str, name: str) -> str:
        return os.path.join(self._directory(run_id), name)

    def _status(self, run_id: str) -> dict:
        return _read_json(self._file(run_id, "status.json")) or {"state": FAILED, "error": "Its state cannot be read"}

    def _set_status(self, run_id: str, **changes) -> dict:
        status = {**(_read_json(self._file(run_id, "status.json")) or {}), **changes}
        _write_json(self._file(run_id, "status.json"), status)
        return status

    def _run_ids(self) -> list[str]:
        try:
            names = os.listdir(self._runs_root)
        except OSError:
            return []
        # Newest first: an id begins with the moment it was made.
        return sorted((name for name in names if RUN_ID.fullmatch(name)), reverse=True)

    # ── asking for a run ──

    def _checked_period(self, first_day: date, last_day: date, note: str) -> None:
        if last_day < first_day:
            raise InvalidRequest("The period ends before it starts")
        if (last_day - first_day).days >= MAX_PERIOD_DAYS:
            raise InvalidRequest(f"The period is longer than {MAX_PERIOD_DAYS} days")
        if len(note) > MAX_NOTE_LENGTH:
            raise InvalidRequest(f"The note is longer than {MAX_NOTE_LENGTH} characters")

    def _checked_rules(self, rules: dict | None) -> tuple[WataRules, dict, dict]:
        """The rules of a run, what it changes in the live ones, and what those said."""
        rule_changes = checked_rules(rules or {})
        live = self.live_rules()
        rule_changes = {name: value for name, value in rule_changes.items() if getattr(live, name) != value}
        return replace(live, **rule_changes), rule_changes, {name: getattr(live, name) for name in rule_changes}

    def _checked_turbo(self, entry_price: float | None, spread: float | None, leverages: list[float] | None) -> dict:
        defaults = self.default_turbo()
        leverages = [float(value) for value in (leverages or defaults["leverages"])]
        if not 1 <= len(leverages) <= MAX_LEVERAGES or len(set(leverages)) != len(leverages):
            raise InvalidRequest(f"Give one to {MAX_LEVERAGES} leverages, each once")
        entry_price = defaults["entry_price"] if entry_price is None else entry_price
        spread = defaults["spread"] if spread is None else spread
        try:
            for leverage in leverages:
                TurboModel(entry_price=entry_price, points_per_unit=leverage, spread=spread)
        except ValueError as e:
            raise InvalidRequest(str(e)) from e
        return {"entry_price": entry_price, "spread": spread, "leverages": leverages}

    def _checked_execution(self, capital: float | None, fill_delay: int | None, slippage: float | None) -> dict:
        capital = backtest.DEFAULT_CAPITAL if capital is None else float(capital)
        fill_delay = 0 if fill_delay is None else int(fill_delay)
        slippage = 0.0 if slippage is None else float(slippage)
        if not 0 <= capital <= MAX_CAPITAL:
            raise InvalidRequest("The capital is an amount of money, 0 for none")
        if not 0 <= fill_delay <= MAX_FILL_DELAY_SECONDS:
            raise InvalidRequest(f"A fill waits between 0 and {MAX_FILL_DELAY_SECONDS} seconds")
        if not 0 <= slippage <= MAX_SLIPPAGE_POINTS:
            raise InvalidRequest(f"The slippage is between 0 and {MAX_SLIPPAGE_POINTS:g} index points")
        return {"capital": capital, "fill_delay": fill_delay, "slippage": slippage}

    def _new_run(self, request: dict, run_rules: WataRules, files: dict[str, list] | None = None) -> dict:
        """A folder for the run, with what was asked, and its turn in the queue."""
        if len(self._waiting) >= MAX_QUEUED:
            raise NotPossible(f"{MAX_QUEUED} backtests are already waiting: let some finish first")
        now = self._clock()
        run_id = f"{datetime.fromtimestamp(now, timezone.utc):%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"
        directory = os.path.join(self._runs_root, run_id)
        os.makedirs(directory)
        for name, content in (files or {}).items():
            _write_json(os.path.join(directory, name), content)
        _write_json(os.path.join(directory, "request.json"), request)
        _write_json(os.path.join(directory, "rules.json"), run_rules.to_dict())
        _write_json(os.path.join(directory, "status.json"), {"state": QUEUED, "created_at": _utc(now)})
        self._waiting.append(run_id)
        self._queue.put_nowait(run_id)
        return self.get(run_id)

    def create(
        self,
        *,
        first_day: date,
        last_day: date,
        strategy: str = "kernel_vwap",
        parameters: dict | None = None,
        rules: dict | None = None,
        entry_price: float | None = None,
        spread: float | None = None,
        leverages: list[float] | None = None,
        note: str = "",
        capital: float | None = None,
        fill_delay: int | None = None,
        slippage: float | None = None,
    ) -> dict:
        self._checked_period(first_day, last_day, note)
        if strategy not in backtest.STRATEGIES:
            raise InvalidRequest(f"Unknown strategy '{strategy}'")

        parameters = dict(parameters or {})
        params_type, _ = backtest.STRATEGIES[strategy]
        defaults = params_type()
        try:
            resolved = defaults.with_overrides({name: str(value) for name, value in parameters.items()})
        except ValueError as e:
            raise InvalidRequest(str(e)) from e
        # Kept as the strategy reads them, and only where they differ from its defaults.
        changed = {
            name: getattr(resolved, name) for name in parameters if getattr(resolved, name) != getattr(defaults, name)
        }

        run_rules, rule_changes, rules_before = self._checked_rules(rules)
        turbo = self._checked_turbo(entry_price, spread, leverages)
        return self._new_run({
            "kind": KIND_STRATEGY,
            "from": first_day.isoformat(),
            "to": last_day.isoformat(),
            "strategy": strategy,
            "parameters": changed,
            "rules": rule_changes,
            # What the live configuration said then, to read the change against.
            "rules_before": rules_before,
            "turbo": turbo,
            "execution": self._checked_execution(capital, fill_delay, slippage),
            "note": note,
        }, run_rules)

    def create_from_signals(
        self,
        *,
        first_day: date,
        last_day: date,
        journal: list[dict],
        positions: list[dict],
        rules: dict | None = None,
        entry_price: float | None = None,
        spread: float | None = None,
        leverages: list[float] | None = None,
        note: str = "",
        capital: float | None = None,
        fill_delay: int | None = None,
        slippage: float | None = None,
    ) -> dict:
        """
        A backtest of the signals WATA really received in the period (the
        rows of its journal), kept with the trades it really made of them
        (its closed positions) to be set beside the model's.
        """
        self._checked_period(first_day, last_day, note)
        replayed = signals_to_replay(journal, self._indice)
        if not any(signals.ACTIONS[signal["action"]][0] in (LONG, SHORT) for signal in replayed):
            raise NotPossible("WATA's journal has no entry signal in that period: there is nothing to replay")
        run_rules, rule_changes, rules_before = self._checked_rules(rules)
        turbo = self._checked_turbo(entry_price, spread, leverages)
        real = real_trades_of(positions, journal, self._indice)
        return self._new_run({
            "kind": KIND_SIGNALS,
            "from": first_day.isoformat(),
            "to": last_day.isoformat(),
            "strategy": backtest.SIGNALS,
            "parameters": {},
            "rules": rule_changes,
            "rules_before": rules_before,
            "turbo": turbo,
            "execution": self._checked_execution(capital, fill_delay, slippage),
            "note": note,
            "signals": len(replayed),
            "real_trades": len(real),
        }, run_rules, {"signals.json": replayed, "real_trades.json": real})

    def _checked_axes(self, axes: list[dict], strategy: str | None) -> list[dict]:
        """The settings a sweep gives several values to, each value as it will be read."""
        if not 1 <= len(axes) <= sweep.MAX_AXES:
            raise InvalidRequest("A sweep gives several values to one setting or to two")
        checked = []
        for axis in axes:
            kind, name, values = axis.get("kind"), axis.get("name"), list(axis.get("values") or [])
            if not 2 <= len(values) <= MAX_SWEEP_VALUES:
                raise InvalidRequest(f"Give 2 to {MAX_SWEEP_VALUES} values for '{name}'")
            if kind == sweep.RULE:
                # One value at a time, as a change of that rule alone would be checked.
                read = [checked_rules({name: value})[name] for value in values]
            elif kind == sweep.PARAMETER:
                if strategy is None:
                    raise InvalidRequest("Signals have no strategy: only rules can be given several values")
                defaults = backtest.STRATEGIES[strategy][0]()
                try:
                    read = [getattr(defaults.with_overrides({name: str(value)}), name) for value in values]
                except ValueError as e:
                    raise InvalidRequest(str(e)) from e
            else:
                raise InvalidRequest("A sweep gives several values to a parameter of the strategy or to a rule")
            if len(set(read)) != len(read):
                raise InvalidRequest(f"The values of '{name}' are not all different")
            checked.append({"kind": kind, "name": name, "values": read})
        if len({(axis["kind"], axis["name"]) for axis in checked}) != len(checked):
            raise InvalidRequest("The two settings of a sweep are the same one")
        cells = 1
        for axis in checked:
            cells *= len(axis["values"])
        if cells > MAX_SWEEP_CELLS:
            raise InvalidRequest(f"{cells} combinations: {MAX_SWEEP_CELLS} at most")
        return checked

    def create_sweep(
        self,
        *,
        first_day: date,
        last_day: date,
        axes: list[dict],
        held_out: float = sweep.DEFAULT_HELD_OUT,
        strategy: str = "kernel_vwap",
        parameters: dict | None = None,
        rules: dict | None = None,
        entry_price: float | None = None,
        spread: float | None = None,
        leverage: float | None = None,
        note: str = "",
        fill_delay: int | None = None,
        slippage: float | None = None,
        journal: list[dict] | None = None,
        positions: list[dict] | None = None,
    ) -> dict:
        """
        The same backtest for every combination of the values given to one or
        two of its settings, the last part of the period held out. Of a
        strategy, or of the signals WATA received when its ``journal`` is given.
        """
        self._checked_period(first_day, last_day, note)
        if not sweep.LEAST_HELD_OUT <= held_out <= sweep.MOST_HELD_OUT:
            raise InvalidRequest(
                f"Between {sweep.LEAST_HELD_OUT:.0%} and {sweep.MOST_HELD_OUT:.0%} of the days can be held out"
            )
        replaying = journal is not None
        files: dict[str, list] = {}
        changed: dict = {}
        if replaying:
            replayed = signals_to_replay(journal, self._indice)
            if not any(signals.ACTIONS[signal["action"]][0] in (LONG, SHORT) for signal in replayed):
                raise NotPossible("WATA's journal has no entry signal in that period: there is nothing to replay")
            files = {"signals.json": replayed, "real_trades.json": real_trades_of(positions or [], journal, self._indice)}
        else:
            if strategy not in backtest.STRATEGIES:
                raise InvalidRequest(f"Unknown strategy '{strategy}'")
            defaults = backtest.STRATEGIES[strategy][0]()
            try:
                resolved = defaults.with_overrides({name: str(value) for name, value in (parameters or {}).items()})
            except ValueError as e:
                raise InvalidRequest(str(e)) from e
            changed = {
                name: getattr(resolved, name) for name in (parameters or {}) if getattr(resolved, name) != getattr(defaults, name)
            }
        run_rules, rule_changes, rules_before = self._checked_rules(rules)
        checked_axes = self._checked_axes(axes, None if replaying else strategy)
        defaults_turbo = self.default_turbo()
        # One turbo: the leverage named, else the middle one of those a backtest is made with.
        turbo = self._checked_turbo(
            entry_price, spread, [leverage if leverage is not None else defaults_turbo["leverages"][len(defaults_turbo["leverages"]) // 2]],
        )
        execution = self._checked_execution(0, fill_delay, slippage)
        return self._new_run({
            "kind": KIND_SWEEP,
            "replays": KIND_SIGNALS if replaying else KIND_STRATEGY,
            "from": first_day.isoformat(),
            "to": last_day.isoformat(),
            "strategy": backtest.SIGNALS if replaying else strategy,
            "parameters": changed,
            "rules": rule_changes,
            "rules_before": rules_before,
            "turbo": turbo,
            "execution": execution,
            "axes": checked_axes,
            "held_out": held_out,
            "note": note,
        }, run_rules, files)

    def run_cell(self, run_id: str, cell: int) -> dict:
        """One combination of a sweep as a backtest of its own, to be read with all a backtest says."""
        request = _read_json(self._file(run_id, "request.json")) or {}
        result = _read_json(self._file(run_id, "sweep.json"))
        if request.get("kind") != KIND_SWEEP or result is None:
            raise NotPossible("Only a combination of a finished sweep can be made a backtest")
        if not 0 <= cell < len(result["cells"]):
            raise InvalidRequest("This sweep has no such combination")
        values = dict(zip(((axis["kind"], axis["name"]) for axis in request["axes"]), result["cells"][cell]["values"]))
        parameters = {**request["parameters"], **{name: value for (kind, name), value in values.items() if kind == sweep.PARAMETER}}
        rules = {**request["rules"], **{name: value for (kind, name), value in values.items() if kind == sweep.RULE}}
        common = {
            "first_day": date.fromisoformat(request["from"]), "last_day": date.fromisoformat(request["to"]),
            "rules": rules, "entry_price": request["turbo"]["entry_price"], "spread": request["turbo"]["spread"],
            "leverages": request["turbo"]["leverages"],
            "fill_delay": request["execution"]["fill_delay"], "slippage": request["execution"]["slippage"],
            "note": (request.get("note") or "")[:MAX_NOTE_LENGTH],
        }
        if request.get("replays") == KIND_SIGNALS:
            # The same signals and real trades the sweep was given, kept with it.
            return self._from_kept_signals(run_id, common)
        return self.create(strategy=request["strategy"], parameters=parameters, **common)

    def _from_kept_signals(self, run_id: str, asked: dict) -> dict:
        """A replay of the signals kept with another run, with what is asked now."""
        replayed = _read_json(self._file(run_id, "signals.json")) or []
        real = _read_json(self._file(run_id, "real_trades.json")) or []
        run_rules, rule_changes, rules_before = self._checked_rules(asked["rules"])
        turbo = self._checked_turbo(asked["entry_price"], asked["spread"], asked["leverages"])
        return self._new_run({
            "kind": KIND_SIGNALS,
            "from": asked["first_day"].isoformat(),
            "to": asked["last_day"].isoformat(),
            "strategy": backtest.SIGNALS,
            "parameters": {},
            "rules": rule_changes,
            "rules_before": rules_before,
            "turbo": turbo,
            "execution": self._checked_execution(None, asked["fill_delay"], asked["slippage"]),
            "note": asked["note"],
            "signals": len(replayed),
            "real_trades": len(real),
        }, run_rules, {"signals.json": replayed, "real_trades.json": real})

    # ── running them, one at a time ──

    def recover(self) -> None:
        """Runs a previous admin left waiting or running were never finished: say so."""
        for run_id in self._run_ids():
            if self._status(run_id).get("state") in (QUEUED, RUNNING) and run_id not in self._waiting:
                self._set_status(run_id, state=INTERRUPTED, error="The admin stopped before this run was finished")

    async def run(self) -> None:
        """Take the runs asked for in turn, until cancelled."""
        self.recover()
        while True:
            run_id = await self._queue.get()
            try:
                await self._execute(run_id)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Backtest {run_id} failed outside the command itself: {e}", exc_info=True)
                try:
                    self._set_status(run_id, state=FAILED, error=str(e), finished_at=_utc(self._clock()))
                except Exception:
                    pass

    def _sweep_command(self, run_id: str, request: dict) -> list[str]:
        directory = self._directory(run_id)
        turbo, execution = request["turbo"], request["execution"]
        arguments = [
            "src.strategy.sweep",
            "--from", request["from"], "--to", request["to"],
            "--root", self._prices_root, "--indice", self._indice,
            "--rules", os.path.join(directory, "rules.json"),
            "--points-per-unit", f"{turbo['leverages'][0]:g}",
            "--entry-price", str(turbo["entry_price"]), "--spread", str(turbo["spread"]),
            "--fill-delay", str(execution["fill_delay"]), "--slippage", f"{execution['slippage']:g}",
            "--held-out", f"{request['held_out']:g}",
            "--out", directory, "--progress", os.path.join(directory, "progress.json"),
            "--low-priority",
        ]
        for axis in request["axes"]:
            arguments += ["--axis", f"{axis['kind']}:{axis['name']}=" + ",".join(str(value) for value in axis["values"])]
        if request.get("replays") == KIND_SIGNALS:
            return arguments + ["--signals", os.path.join(directory, "signals.json")]
        arguments += ["--strategy", request["strategy"]]
        for name, value in request["parameters"].items():
            arguments += ["--set", f"{name}={value}"]
        return arguments

    def _result_file(self, run_id: str, request: dict) -> str:
        """What the command of a run writes when it is done."""
        return self._file(run_id, "sweep.json" if request.get("kind") == KIND_SWEEP else "summary.json")

    def _command(self, run_id: str, request: dict) -> list[str]:
        if request.get("kind") == KIND_SWEEP:
            return self._sweep_command(run_id, request)
        directory = self._directory(run_id)
        turbo = request["turbo"]
        arguments = [
            "src.strategy.backtest",
            "--from", request["from"], "--to", request["to"],
            "--root", self._prices_root, "--indice", self._indice,
            "--rules", os.path.join(directory, "rules.json"),
            "--points-per-unit", ",".join(f"{leverage:g}" for leverage in turbo["leverages"]),
            "--entry-price", str(turbo["entry_price"]), "--spread", str(turbo["spread"]),
            "--out", directory, "--progress", os.path.join(directory, "progress.json"),
            "--low-priority",
        ]
        # A run asked for before fills could be made less kind has none of this: as it was then.
        execution = request.get("execution")
        if execution:
            arguments += [
                "--capital", f"{execution['capital']:g}", "--fill-delay", str(execution["fill_delay"]),
                "--slippage", f"{execution['slippage']:g}",
            ]
        if request.get("kind") == KIND_SIGNALS:
            return arguments + ["--signals", os.path.join(directory, "signals.json")]
        arguments += ["--strategy", request["strategy"]]
        for name, value in request["parameters"].items():
            arguments += ["--set", f"{name}={value}"]
        return arguments

    async def _execute(self, run_id: str) -> None:
        if run_id in self._waiting:
            self._waiting.remove(run_id)
        if run_id in self._cancelled:
            self._cancelled.discard(run_id)
            return
        try:
            request = _read_json(self._file(run_id, "request.json"))
        except UnknownRun:
            return
        if request is None:
            self._set_status(run_id, state=FAILED, error="What was asked cannot be read")
            return

        self._set_status(run_id, state=RUNNING, started_at=_utc(self._clock()))
        error = None
        code = None
        try:
            process = await self._launcher(self._command(run_id, request), self._file(run_id, "output.log"))
        except Exception as e:
            error = f"The backtest could not be started: {e}"
        else:
            self._running, self._process = run_id, process
            try:
                code = await asyncio.wait_for(process.wait(), RUN_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                error = f"Stopped after {RUN_TIMEOUT_SECONDS // 60} minutes"
            finally:
                self._running, self._process = None, None

        if run_id in self._cancelled:
            self._cancelled.discard(run_id)
            state, error = CANCELLED, None
        elif error is None and code == 0 and os.path.exists(self._result_file(run_id, request)):
            state = DONE
        else:
            state = FAILED
            error = error or "\n".join(_tail(self._file(run_id, "output.log"), 5)) or f"The command ended with code {code}"
        self._set_status(run_id, state=state, error=error, finished_at=_utc(self._clock()))
        self._announce("backtest_finished", run_id=run_id, state=state, note=request.get("note") or "")

    def _announce(self, event: str, **data) -> None:
        if self._publish is None:
            return
        try:
            self._publish(event, **data)
        except Exception as e:
            logger.warning(f"Could not announce {event}: {e}")

    def cancel(self, run_id: str) -> dict:
        state = self._status(run_id).get("state")
        if state == QUEUED and run_id in self._waiting:
            self._waiting.remove(run_id)
            self._cancelled.add(run_id)
            self._set_status(run_id, state=CANCELLED, finished_at=_utc(self._clock()))
        elif state == RUNNING and run_id == self._running and self._process is not None:
            self._cancelled.add(run_id)
            self._process.terminate()
        else:
            raise NotPossible("Only a backtest that is waiting or running can be stopped")
        return self.get(run_id)

    def delete(self, run_id: str) -> None:
        directory = self._directory(run_id)
        if run_id in self._waiting or run_id == self._running:
            raise NotPossible("Stop this backtest before removing it")
        shutil.rmtree(directory)

    def delete_many(self, run_ids: list[str]) -> dict:
        """Remove several runs at once. One that waits, runs or is not there is left, and said to be."""
        removed, kept = [], []
        for run_id in run_ids:
            try:
                self.delete(run_id)
                removed.append(run_id)
            except UnknownRun:
                kept.append({"id": run_id, "reason": "No such backtest"})
            except NotPossible as e:
                kept.append({"id": run_id, "reason": str(e)})
        return {"removed": removed, "kept": kept}

    def update(self, run_id: str, *, note: str | None = None, tags: list[str] | None = None) -> dict:
        """Name a run again, or tag it: what it is called, not what it was asked."""
        path = self._file(run_id, "request.json")
        request = _read_json(path)
        if request is None:
            raise NotPossible("What was asked of this backtest cannot be read")
        if note is not None:
            if len(note) > MAX_NOTE_LENGTH:
                raise InvalidRequest(f"The note is longer than {MAX_NOTE_LENGTH} characters")
            request["note"] = note
        if tags is not None:
            cleaned = [tag.strip() for tag in tags if tag.strip()]
            if len(cleaned) > MAX_TAGS or any(len(tag) > MAX_TAG_LENGTH for tag in cleaned):
                raise InvalidRequest(f"{MAX_TAGS} tags at most, of {MAX_TAG_LENGTH} characters at most each")
            # Each once, in the order they were given.
            request["tags"] = list(dict.fromkeys(cleaned))
        _write_json(path, request)
        return self.get(run_id)

    def storage(self) -> dict:
        """The disk the runs and the prices take."""
        runs = [_folder_bytes(os.path.join(self._runs_root, run_id)) for run_id in self._run_ids()]
        prices = os.path.join(self._prices_root, dukascopy.SOURCE)
        return {
            "runs": {"count": len(runs), "bytes": sum(runs)},
            "prices": {
                # Of every instrument fetched, as the bytes below.
                "days": sum(len(self._history.days(label)) for label in self._history.labels()),
                # The ten-second bars the backtests read, and the hours as they were fetched, kept to complete a day.
                "bars_bytes": _folder_bytes(os.path.join(prices, "bars")),
                "fetched_bytes": _folder_bytes(os.path.join(prices, "raw")),
            },
        }

    async def close(self) -> None:
        """The admin is stopping: its child processes stop with it."""
        for process in (self._process, self._fetch_process, self._calibration_process):
            if process is not None and process.returncode is None:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
        for task in (self._fetch_task, self._calibration_task):
            if task is not None:
                task.cancel()

    # ── reading them ──

    def _headline(self, summary: dict | None) -> dict | None:
        """The figures of the middle leverage: what a list can show of a run."""
        runs = (summary or {}).get("runs") or []
        if not runs:
            return None
        middle = runs[len(runs) // 2]
        figures = middle["summary"]
        return {
            "leverage": middle["turbo"]["points_per_unit"],
            **{name: figures.get(name) for name in (
                "days", "days_with_an_entry", "trades", "win_rate", "total_percent", "worst_drawdown", "sample",
            )},
            # The share of the random entries it did better than: what the total is worth.
            "beaten": (figures.get("against_random") or {}).get("beaten"),
        }

    def _price_files(self) -> dict[str, str]:
        """The digest of every file of prices there is, by its path; read again only for a file that changed."""
        location = backtest.price_location(dukascopy.SOURCE, self._prices_root, self._indice)
        try:
            names = os.listdir(location)
        except OSError:
            names = []
        digests = {}
        for name in names:
            path = os.path.join(location, name)
            try:
                found = os.stat(path)
            except OSError:
                continue
            kept = self._digests.get(path)
            if kept is None or kept[:2] != (found.st_size, found.st_mtime_ns):
                kept = (found.st_size, found.st_mtime_ns, file_digest(path))
                self._digests[path] = kept
            digests[path] = kept[2]
        return digests

    def _stale(self, request: dict, summary: dict | None, price_files: dict[str, str]) -> dict | None:
        """
        Whether a run would come out otherwise today: the code that replays
        changed, or the prices of its period did. None for a run that did not
        keep what it was made with.
        """
        if not summary or "code" not in summary or "price_files" not in summary:
            return None
        # The days of a run are those of the timezone it was made in.
        timezone_name = (summary.get("rules") or {}).get("timezone", "Europe/Paris")
        start, end = backtest.day_bounds(
            LocalClock(timezone_name), date.fromisoformat(request["from"]), date.fromisoformat(request["to"]),
        )
        location = backtest.price_location(dukascopy.SOURCE, self._prices_root, self._indice)
        paths = files_of_period(location, [os.path.basename(path) for path in price_files], start, end)
        today = prices_fingerprint(paths, digest_of=price_files.__getitem__)
        return {
            "code": summary["code"].get("digest") != self._code["digest"],
            "prices": summary["price_files"] != today,
            # The version it was made with, to say so when the code changed.
            "version": summary["code"].get("version"),
        }

    def _described(self, run_id: str, *, whole: bool, price_files: dict[str, str] | None = None) -> dict:
        directory = self._directory(run_id)
        request = _read_json(os.path.join(directory, "request.json")) or {}
        status = self._status(run_id)
        is_sweep = request.get("kind") == KIND_SWEEP
        done = status.get("state") == DONE
        summary = _read_json(os.path.join(directory, "summary.json")) if done and not is_sweep else None
        swept = _read_json(os.path.join(directory, "sweep.json")) if done and is_sweep else None
        described = {"id": run_id, **request, "status": status, "headline": self._headline(summary)}
        try:
            described["stale"] = self._stale(
                request, summary or swept, self._price_files() if price_files is None else price_files,
            )
        except (KeyError, ValueError, TypeError):
            described["stale"] = None
        if whole and is_sweep:
            described["sweep"] = swept
        if whole:
            described["bytes"] = _folder_bytes(directory)
        if whole:
            described["summary"] = summary
            described["progress"] = (
                _read_json(os.path.join(directory, "progress.json")) if status.get("state") == RUNNING else None
            )
            described["run_rules"] = _read_json(os.path.join(directory, "rules.json"))
            described["output"] = _tail(os.path.join(directory, "output.log")) if status.get("state") == FAILED else []
            described["waiting_before"] = self._waiting.index(run_id) if run_id in self._waiting else 0
        return described

    def get(self, run_id: str) -> dict:
        return self._described(run_id, whole=True)

    def list_runs(self) -> list[dict]:
        """Every run, the newest first, with the few figures a list shows."""
        # Looked at once for the whole list: which prices there are today.
        price_files = self._price_files()
        return [self._described(run_id, whole=False, price_files=price_files) for run_id in self._run_ids()[:MAX_LISTED]]

    def rows(self, run_id: str, kind: str, leverage: float) -> list[dict]:
        """The trades, the days or the refused entries of a run, for one of its leverages."""
        if kind not in _NUMBERS:
            raise InvalidRequest(f"Unknown kind of rows '{kind}'")
        request = _read_json(self._file(run_id, "request.json")) or {}
        if leverage not in (request.get("turbo") or {}).get("leverages", []):
            raise InvalidRequest("This backtest was not run with that leverage")
        path = self._file(run_id, f"{kind}_{leverage:g}.csv")
        if not os.path.exists(path):
            return []
        numbers = _NUMBERS[kind]
        with open(path, newline="", encoding="utf-8") as in_file:
            rows = []
            for row in csv.DictReader(in_file):
                for name in numbers:
                    # A column added since the run was made is not in its files.
                    text = row.get(name) or ""
                    row[name] = None if text == "" else (int(float(text)) if name in _WHOLE else float(text))
                rows.append(row)
        return rows

    def comparison(self, run_id: str, leverage: float) -> dict:
        """
        A run of real signals set against what WATA really did with them:
        each real trade beside the model's, and the days of both.
        """
        real = _read_json(self._file(run_id, "real_trades.json"))
        replayed = _read_json(self._file(run_id, "signals.json"))
        if real is None or replayed is None:
            raise NotPossible("This backtest did not replay real signals: there is nothing to compare it with")
        rules = WataRules.from_dict(_read_json(self._file(run_id, "rules.json")) or {})
        model = [
            Leg(
                direction=trade["direction"], entered_at=_epoch(trade["entry_time"]), left_at=_epoch(trade["exit_time"]),
                performance=trade["performance_percent"], exit=_model_exit(trade["exit_reason"]),
            )
            for trade in self.rows(run_id, TRADES, leverage)
        ]
        ours = [
            Leg(
                direction=trade["direction"], entered_at=_epoch(trade["opened_at"]), left_at=_epoch(trade["closed_at"]),
                performance=trade["performance_percent"], exit=exit_group(trade.get("close_reason")),
                reference=trade.get("position_id") or "",
            )
            for trade in real if trade.get("performance_percent") is not None
        ]
        outcomes: dict[str, int] = {}
        for signal in replayed:
            outcome = signal.get("outcome") or "unknown"
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
        return {
            "id": run_id,
            "leverage": leverage,
            "timezone": rules.timezone,
            # What the trader made of the signals, as its journal has it.
            "journal": outcomes,
            **compare(ours, model, LocalClock(rules.timezone)),
        }

    def breakdowns(self, run_id: str, leverage: float) -> dict:
        """
        The trades of a run by what could explain them: the hour and the
        weekday they were entered on, their direction, what they were entered
        and left on, their confidence, how long they were held. With the same
        figures as the trades WATA really made have on the Exits page.
        """
        rules = WataRules.from_dict(_read_json(self._file(run_id, "rules.json")) or {})
        clock = LocalClock(rules.timezone)
        trades = []
        for row in self.rows(run_id, TRADES, leverage):
            entered, left = _epoch(row["entry_time"]), _epoch(row["exit_time"])
            day, seconds = clock.at(entered)
            trades.append({
                "performance_percent": row["performance_percent"],
                "max_performance_percent": row["max_performance_percent"],
                # In money when the run was given a capital.
                "profit_loss": row.get("profit_loss"),
                "hour": seconds // 3600,
                "weekday": WEEKDAYS[day.weekday()],
                "direction": row["direction"],
                "entry_reason": row["entry_reason"],
                "exit": row["exit_reason"],
                "confidence": _confidence_group(row.get("confidence")),
                "holding_time": holding_bucket(left - entered),
            })

        def by(name: str, order: list | None = None) -> list[dict]:
            totals: dict = {}
            for trade in trades:
                totals[trade[name]] = totals.get(trade[name], 0.0) + trade["performance_percent"]
            groups = stats_by(trades, lambda trade: trade[name], order)
            # The sum of the results, in points of percent: what the group weighs, each trade as the whole stake.
            return [{**group, "total_percent": round(totals.get(group["group"], 0.0), 2)} for group in groups]

        present = {name: {trade[name] for trade in trades} for name in ("weekday", "confidence", "holding_time")}
        (whole,) = stats_by(trades, lambda trade: "all", ["all"])
        return {
            "id": run_id,
            "leverage": leverage,
            "timezone": rules.timezone,
            "summary": {
                **{name: value for name, value in whole.items() if name != "group"},
                **given_back(trades),
                "total_percent": round(sum(trade["performance_percent"] for trade in trades), 2),
            },
            "groups": {
                "hour": by("hour"),
                "weekday": by("weekday", [weekday for weekday in WEEKDAYS if weekday in present["weekday"]]),
                "direction": by("direction"),
                "entry_reason": by("entry_reason"),
                "confidence": by("confidence", [
                    label for label in (*(label for _, label in _CONFIDENCES), NO_CONFIDENCE) if label in present["confidence"]
                ]),
                "holding_time": by("holding_time", [label for label in HOLDING_LABELS if label in present["holding_time"]]),
                "exit": sorted(by("exit"), key=lambda group: group["trade_count"], reverse=True),
            },
        }

    def trade_path(self, run_id: str, leverage: float, entry_time: str) -> dict:
        """
        One trade as the position monitor lived it: the turbo's result every
        ten seconds from the entry to the exit (its last, its worst and its
        best), with the levels that could sell it. Replayed when asked.
        """
        request = _read_json(self._file(run_id, "request.json")) or {}
        rules = WataRules.from_dict(_read_json(self._file(run_id, "rules.json")) or {})
        trades = self.rows(run_id, TRADES, leverage)
        trade = next((row for row in trades if row["entry_time"] == entry_time), None)
        if trade is None:
            raise InvalidRequest("No trade of this backtest was entered at that moment")
        turbo = TurboModel(
            entry_price=request["turbo"]["entry_price"], points_per_unit=leverage, spread=request["turbo"]["spread"],
        )
        clock = LocalClock(rules.timezone)
        direction = trade["direction"]
        barrier = turbo.barrier(direction, trade["entry_index"])
        entered, left = _epoch(trade["entry_time"]), _epoch(trade["exit_time"])

        # What the day had made before it: the daily target sells where the two together reach it.
        day = clock.at(entered)[0]
        earlier = [
            row["performance_percent"] for row in trades
            if clock.at(_epoch(row["entry_time"]))[0] == day and _epoch(row["exit_time"]) <= entered
        ]
        day_before = compounded(earlier)
        day_target = ((1 + rules.day_profit_target / 100.0) / (1 + day_before / 100.0) - 1) * 100

        location = backtest.price_location(dukascopy.SOURCE, self._prices_root, self._indice)
        bars = FileBars(location, self._indice).bars(
            datetime.fromtimestamp(entered, timezone.utc), datetime.fromtimestamp(left + BAR_SECONDS, timezone.utc),
        )
        points = []
        peak = 0.0
        for bar in bars:
            against, with_it = (bar.mid_low, bar.mid_high) if direction == LONG else (bar.mid_high, bar.mid_low)
            worst = turbo.performance(direction, barrier, against)
            best = turbo.performance(direction, barrier, with_it)
            last = turbo.performance(direction, barrier, bar.mid_close)
            if bar.start >= left:
                # Sold in these ten seconds: what they did after it is not the trade's.
                opened = turbo.performance(direction, barrier, bar.mid_open)
                last = trade["performance_percent"]
                worst, best = min(opened, last), max(opened, last)
            peak = max(peak, best)
            armed = rules.trailing_enabled and peak >= rules.trailing_activation_percent
            points.append([bar.start, last, worst, best, round(peak - rules.trailing_drawdown_percent, 2) if armed else None])
        if len(points) > MAX_PATH_POINTS:
            size = -(-len(points) // MAX_PATH_POINTS)
            points = [
                [
                    group[0][0], group[-1][1], min(point[2] for point in group), max(point[3] for point in group),
                    group[-1][4],
                ]
                for group in (points[place:place + size] for place in range(0, len(points), size))
            ]
        return {
            "id": run_id,
            "leverage": leverage,
            "timezone": rules.timezone,
            "trade": trade,
            "day_before_percent": day_before,
            # Each point: when its ten seconds began, then the turbo's result at their end, at their worst and at
            # their best, and where the trailing stop stood once armed.
            "points": points,
            "levels": {
                "stop": rules.stoploss_percent,
                "take_profit": rules.max_profit_percent,
                "day_target": round(day_target, 2),
                "trailing_activation": rules.trailing_activation_percent if rules.trailing_enabled else None,
                "trailing_drawdown": rules.trailing_drawdown_percent if rules.trailing_enabled else None,
            },
        }

    def day_chart(self, run_id: str, day: date, leverage: float) -> dict:
        """
        One day of a run as the strategy saw it: its range bars, what was
        measured on each, what it decided, and what the trader and the monitor
        made of it at that leverage. Replayed when asked: a fraction of a second.
        """
        request = _read_json(self._file(run_id, "request.json")) or {}
        rules = WataRules.from_dict(_read_json(self._file(run_id, "rules.json")) or {})
        if not request.get("from", "") <= day.isoformat() <= request.get("to", ""):
            raise InvalidRequest("That day is not in the period of this backtest")
        trades = self.rows(run_id, TRADES, leverage)
        refusals = self.rows(run_id, REFUSALS, leverage)

        clock = LocalClock(rules.timezone)
        replayed_signals = request.get("kind") == KIND_SIGNALS
        # Signals have no bars of their own: they are drawn on those of the strategy TradingView runs.
        strategy = CHART_STRATEGY if replayed_signals else request["strategy"]
        params_type, _ = backtest.STRATEGIES[strategy]
        params = params_type().with_overrides({name: str(value) for name, value in request["parameters"].items()})
        location = backtest.price_location(dukascopy.SOURCE, self._prices_root, self._indice)
        traced, decisions = backtest.replay_day(strategy, request["parameters"], clock, location, day, indice=self._indice)
        if replayed_signals:
            decisions = [
                decision for decision in signals.decisions_of(_read_json(self._file(run_id, "signals.json")) or [])
                if clock.at(decision.at)[0] == day
            ]

        bars, kernel, vwap, upper, lower, allowed = [], [], [], [], [], []
        place = None
        for row in traced:
            # No two bars at the same place on the chart's axis: several can begin in the same ten seconds.
            place = row["began"] if place is None else max(row["began"], place + 1)
            bars.append([
                place, row["open"], row["high"], row["low"], row["close"],
                row["began"], row["ended"], row["complete"], row["after_pause"], None,
            ])
            kernel.append(row["kernel"])
            vwap.append(row["vwap"])
            banded = row["vwap"] is not None and row["band"] is not None
            upper.append(row["vwap"] + row["band"] if banded else None)
            lower.append(row["vwap"] - row["band"] if banded else None)
            allowed.append(row["entries_allowed"])

        def of_the_day(rows: list[dict], column: str) -> list[dict]:
            return [row for row in rows if clock.at(_epoch(row[column]))[0] == day]

        return {
            "id": run_id,
            "day": day.isoformat(),
            "timezone": rules.timezone,
            "leverage": leverage,
            "tick": params.tick,
            "decimals": _decimals_of(params.tick),
            "size": params.range_points,
            "session": {
                "start": clock.epoch(day, params.session_start), "end": clock.epoch(day, params.session_end),
            },
            "bars": bars,
            "kernel": kernel,
            "vwap": vwap,
            "upper": upper,
            "lower": lower,
            "entries_allowed": allowed,
            "decisions": [
                {"at": decision.at, "target": decision.target, "reason": decision.reason, "confidence": decision.confidence}
                for decision in decisions
            ],
            "trades": of_the_day(trades, "entry_time"),
            "refusals": of_the_day(refusals, "time"),
        }

    # ── prices ──

    def _instrument(self, label: str | None) -> tuple[str, str]:
        """
        The label asked for (the history backtests read when none is) and the
        Dukascopy symbol fetched for it. A label that is not one of the
        instruments is refused before any path or command is made of it.
        """
        label = label or self._indice
        symbol = self._history.symbol_of(label)
        if symbol is None:
            raise InvalidRequest(f"No Dukascopy instrument {label!r}")
        return label, symbol

    def _ready_days(self, label: str | None = None) -> set[date]:
        """The days that have a file of bars."""
        return set(self._history.days(label or self._indice))

    def _day_states(self, first_day: date, last_day: date, label: str | None = None) -> list[dict]:
        """
        What there is of each day: "ready", "partial" (it can be backtested on,
        but hours of it were not there yet when it was written), "incomplete"
        (some hours, not enough to write it), "missing", "closed" (the market
        was), or "unavailable" (the source has nothing for it). A day the
        source was found to have no more of is settled: not asked for again.
        Days are only settled for the history backtests read, the one kept
        up to date.
        """
        label, symbol = self._instrument(label)
        ready = self._ready_days(label)
        settled = (self._keeper().get("settled") or {}) if label == self._indice else {}
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
        """Which days of the period have prices to backtest on, of ``label`` (the history backtests read by default)."""
        if last_day < first_day or (last_day - first_day).days >= MAX_PERIOD_DAYS:
            raise InvalidRequest(f"A period of {MAX_PERIOD_DAYS} days at most")
        label, symbol = self._instrument(label)
        ready = self._ready_days(label)
        return {
            "from": first_day.isoformat(),
            "to": last_day.isoformat(),
            "label": label,
            "symbol": symbol,
            "days": self._day_states(first_day, last_day, label),
            "ready": {
                "count": len(ready),
                "first": min(ready).isoformat() if ready else None,
                "last": max(ready).isoformat() if ready else None,
            },
            "fetch": self.fetch_status(),
            "keep": self.keep_status(),
        }

    def _fetch_file(self, name: str) -> str:
        return os.path.join(self._runs_root, FETCH_FOLDER, name)

    def _fetching(self) -> bool:
        return self._fetch_process is not None or (self._fetch_task is not None and not self._fetch_task.done())

    def fetch_status(self) -> dict:
        status = _read_json(self._fetch_file("status.json")) or {"state": IDLE}
        if status.get("state") == RUNNING and (self._fetch_task is None or self._fetch_task.done()):
            # Written by an admin that is no longer there.
            status = {**status, "state": INTERRUPTED}
        return {**status, "lines": _tail(self._fetch_file("output.log")) if status.get("state") != IDLE else []}

    def start_fetch(self, first_day: date, last_day: date, *, automatic: bool = False, label: str | None = None) -> dict:
        """
        Download the days of a period, of ``label`` (the history backtests read
        by default). One download at a time, whatever the instrument: the server
        shuts out an address, not a symbol.
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

        os.makedirs(self._fetch_file(""), exist_ok=True)
        for name in ("output.log", "report.json"):
            try:
                os.remove(self._fetch_file(name))
            except OSError:
                pass
        _write_json(self._fetch_file("status.json"), {
            "state": RUNNING, "from": first_day.isoformat(), "to": last_day.isoformat(),
            "label": label, "symbol": symbol,
            "started_at": _utc(self._clock()),
            # Started by the service itself, to keep the prices up to date.
            "automatic": automatic,
        })
        self._fetch_stopped = False
        self._fetch_task = asyncio.create_task(self._fetch(first_day, last_day, label, symbol))
        return self.fetch_status()

    async def _fetch(self, first_day: date, last_day: date, label: str, symbol: str) -> None:
        arguments = [
            "src.strategy.history.dukascopy",
            "--from", first_day.isoformat(), "--to", last_day.isoformat(),
            "--root", self._prices_root, "--symbol", symbol, "--label", label, "--low-priority",
            "--report", self._fetch_file("report.json"),
        ]
        state, code = FAILED, None
        try:
            self._fetch_process = await self._launcher(arguments, self._fetch_file("output.log"))
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
        status = _read_json(self._fetch_file("status.json")) or {}
        _write_json(self._fetch_file("status.json"), {
            **status, "state": state, "exit_code": code, "finished_at": _utc(self._clock()),
        })
        try:
            self._after_fetch(state, _read_json(self._fetch_file("report.json")), label)
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

    # ── prices kept up to date without anyone asking ──

    def _keep_settings(self) -> dict:
        settings = _read_json(self._fetch_file("keep.json")) or {}
        return {"enabled": bool(settings.get("enabled", False)), "days": int(settings.get("days", KEEP_DEFAULT_DAYS))}

    def _keeper(self) -> dict:
        """What the service remembers of the downloads: when it may ask again, and the days that are settled."""
        return _read_json(self._fetch_file("keeper.json")) or {}

    def _set_keeper(self, **changes) -> None:
        os.makedirs(self._fetch_file(""), exist_ok=True)
        _write_json(self._fetch_file("keeper.json"), {**self._keeper(), **changes})

    def set_keep(self, enabled: bool, days: int = KEEP_DEFAULT_DAYS) -> dict:
        """Switch on or off the fetching of what is missing of the last ``days`` days."""
        if not 1 <= days <= KEEP_MAX_DAYS:
            raise InvalidRequest(f"Between 1 and {KEEP_MAX_DAYS} days can be kept up to date")
        os.makedirs(self._fetch_file(""), exist_ok=True)
        _write_json(self._fetch_file("keep.json"), {"enabled": enabled, "days": days})
        # Asked by the operator: whatever wait was in progress is over.
        self._set_keeper(next_at=0, failures=0)
        return self.keep_status()

    def _market_is_open(self, now: float) -> bool:
        """Whether WATA may be trading: its hours on a weekday. Unknown counts as open."""
        try:
            rules = self.live_rules()
        except NotPossible:
            return True
        local = datetime.fromtimestamp(now, LocalClock(rules.timezone).zone)
        return local.weekday() < 5 and rules.trading_start_hour <= local.hour < rules.trading_end_hour

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
            "next_at": _utc(next_at) if now < next_at else None,
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
        for day in days if (label or self._indice) == self._indice else []:
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

    # ── the turbo measured on real trades ──

    def _calibration_file(self, name: str) -> str:
        return os.path.join(self._runs_root, CALIBRATION_FOLDER, name)

    def calibration(self) -> dict:
        """The turbo last measured on WATA's closed trades, and the measure in progress if there is one."""
        status = _read_json(self._calibration_file("status.json")) or {"state": IDLE}
        if status.get("state") == RUNNING and (self._calibration_task is None or self._calibration_task.done()):
            status = {**status, "state": INTERRUPTED}
        return {
            "status": status,
            "result": _read_json(self._calibration_file("result.json")),
            "turbo": self.default_turbo(),
            "lines": _tail(self._calibration_file("output.log")) if status.get("state") == FAILED else [],
        }

    def start_calibration(self, positions: list[dict], journal: list[dict]) -> dict:
        """
        Measure the turbo on WATA's closed positions (and its journal, which
        says which indice each was on). In the background: the index is read
        from the history for the open and the close of each.
        """
        if self._calibration_task is not None and not self._calibration_task.done():
            raise NotPossible("The turbo is already being measured")
        trades = [
            trade for trade in real_trades_of(positions, journal, self._indice)
            if trade["open_price"] is not None and trade["close_price"] is not None
        ]
        if not trades:
            raise NotPossible("WATA has no closed trade yet to measure the turbo on")
        os.makedirs(self._calibration_file(""), exist_ok=True)
        try:
            os.remove(self._calibration_file("output.log"))
        except OSError:
            pass
        _write_json(self._calibration_file("trades.json"), trades)
        _write_json(self._calibration_file("status.json"), {
            "state": RUNNING, "trades": len(trades), "started_at": _utc(self._clock()),
        })
        self._calibration_task = asyncio.create_task(self._calibrate())
        return self.calibration()

    async def _calibrate(self) -> None:
        # Written beside the last one, which stays what backtests use until this one is done.
        measured = self._calibration_file("measured.json")
        arguments = [
            "src.strategy.calibration",
            "--trades", self._calibration_file("trades.json"),
            "--root", self._prices_root, "--indice", self._indice,
            "--out", measured, "--low-priority",
        ]
        state, error = FAILED, None
        try:
            self._calibration_process = await self._launcher(arguments, self._calibration_file("output.log"))
            try:
                code = await asyncio.wait_for(self._calibration_process.wait(), CALIBRATION_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                self._calibration_process.kill()
                await self._calibration_process.wait()
                error = f"Stopped after {CALIBRATION_TIMEOUT_SECONDS // 60} minutes"
            else:
                result = _read_json(measured) if code == 0 else None
                if result is not None:
                    _write_json(self._calibration_file("result.json"), {**result, "measured_at": _utc(self._clock())})
                    state = DONE
                else:
                    error = "\n".join(_tail(self._calibration_file("output.log"), 5)) or f"The command ended with code {code}"
        except asyncio.CancelledError:
            raise
        except Exception as e:
            error = f"The measure could not be run: {e}"
        finally:
            self._calibration_process = None
        status = _read_json(self._calibration_file("status.json")) or {}
        _write_json(self._calibration_file("status.json"), {
            **status, "state": state, "error": error, "finished_at": _utc(self._clock()),
        })
        self._announce("calibration_finished", state=state)
