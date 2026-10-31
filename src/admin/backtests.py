"""
Backtests asked for from the admin interface (src/backtest).

A run is the command ``python -m src.backtest.run`` run as a child process: it
can be stopped, it cannot slow the admin's own answers, and it gives way to
everything else on a machine that trades (lowest priority, one at a time).
It reads the recorded turbos through one read-only connection of its own.

Each run is a folder under ``<backtests_path>/runs``: what was asked
(``request.json``), the rules and settings it runs with (``settings.json``,
with the live value of each one it changed), the sources of its Pine script as
they were when it was asked (``sources/``), the signals of the journal it
replays (``journal.json``, read by the route), its state, and what the command
wrote. Nothing goes to the database. A run id is made here and matched against
``RUN_ID`` before any path is built.

None of this acts on the trading services, so it is neither in the audit
trail nor announced on Telegram.

A run asked for by an agent (Claude, through the MCP endpoint: ``made_by``)
gives way to the others: the worker takes the operator's runs and the paper
runs first, whenever they were asked for.
"""

import asyncio
import json
import logging
import os
import re
import secrets
import shutil
import time
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Callable

import pytz

from src.admin import backtest_indicator
from src.admin.analysis import HOLDING_LABELS, exit_group, given_back, holding_bucket, stats_by
from src.admin.data import AssetNotFound, capital_epic, history_label, saxo_instrument_id
from src.admin.errors import InvalidRequest, NotPossible, UnknownRun
from src.admin.processes import (
    CANCELLED,
    DONE,
    FAILED,
    INTERRUPTED,
    QUEUED,
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
from src.admin.ta_rules import (
    ALERT_CALL_ACTIONS,
    IGNORE,
    InvalidRule,
    RuleStore,
    acts,
    checked_alerts,
    checked_bars,
    checked_inputs,
)
from src.admin.ta_scripts import ScriptNotFound, ScriptStore
from src.admin.weekday_hour import stats_by_weekday_hour
from src.price_history.process import cgroup_memory, exit_said, oom_kills
from src.backtest.baseline import DEFAULT_REPETITIONS, DEFAULT_SEED, MAX_REPETITIONS
from src.backtest.execution import EXECUTION_FILE, MEASURED, missing
from src.backtest.figures import code_digest
from src.backtest.regimes import DAY_TYPES, GAP_LABELS, MACRO_LABELS, OPEN_LABELS, SESSION_PARTS, VOLATILITY_LABELS
from src.backtest.settings import (
    DEFAULT_SIGNAL_DELAY,
    GROUPS,
    BacktestSettings,
    InvalidSettings,
    from_config,
    options as setting_options,
    with_changes,
)
from src.backtest.turbo_model import MODEL_CHECK_FILE, RECORDED_TRADES_FILE
from src.backtest.sweep import (
    DEFAULT_HELD_OUT,
    LEAST_HELD_OUT,
    MAX_AXES,
    MAX_CELLS,
    MAX_INPUT_SETS,
    MAX_VALUES,
    MOST_HELD_OUT,
    SETTING,
    InvalidSweep,
    checked_axes,
)
from src.ta_engine.replay import REPLAY_RULE_ID, AssetRows

logger = logging.getLogger(__name__)

# 20261019-213045-a1b2c3: made here, and the only shape a folder is ever looked for with.
RUN_ID = re.compile(r"\d{8}-\d{6}-[0-9a-f]{6}")
# What an admin feature keeps beside a run's results: its check against funded challenges (src/admin/propfirm_check.py).
KEPT_FILES = ("propfirms.json",)
RUNS_FOLDER = "runs"
# What the former backtester left at the root of the folder: its runs, its measures of the turbo.
FORMER_FOLDERS = ("_calibration", "_fetch")

PINE, JOURNAL = "pine", "journal"
# The signals a rule raised live, journaled by the TA engine (``ta_signal``): paper trading.
TA_SIGNALS = "ta_signals"
RAISED_FILE = "ta_signals.json"
# Each shadow rule's day made a run, every finished day (src/admin/paper.py): kept apart from the list of runs.
PAPER_FOLDER = "paper"
# The paper days of the signals WATA receives, under the live settings, kept beside the rules' (src/admin/paper.py).
LIVE_PAPER = "live"
# A day of theirs on which no entry signal came: nothing to trade, kept so that it is not asked again.
NO_ENTRY = ""
NO_ENTRY_SIGNAL = "WATA received no signal of entry in that period"
# Each one's report, one run of its days on paper since it began, made again after each finished day: kept beside.
PAPER_REPORTS = "reports.json"
# The signals a rule raised that one run replays at most (AdminQueries.rule_signals keeps the newest).
MAX_RAISED_SIGNALS = 20_000
SIDES = ("bid", "ask")
CHART_PROVIDERS = ("capital", "dukascopy", "saxo")
MAX_PERIOD_DAYS = 92
SIGNAL_CACHE_FOLDER = "_signal_cache"
MAX_WINDOW_DAYS = 365
MAX_QUEUED = 10
MAX_LISTED = 200
MAX_NAME_LENGTH = 120
MAX_TAGS = 6
MAX_TAG_LENGTH = 24
# Who asked for a run through an agent, and for what: set by the caller, never read from the body.
RESEARCH_KEYS = ("made_by", "made_via", "experiment", "lockbox")
# The runner's answers kept are pruned at most this often, oldest first.
CACHE_PRUNE_SECONDS = 600
# Days of a year of recorded turbos, at the lowest priority on a small machine, with the random repetitions.
RUN_TIMEOUT_SECONDS = 3 * 3600
# A run made again with every order filled later and at a worse price (``stress``): seconds
# added to its orders' time, and euros taken from each fill of a turbo (a tick is 0.01).
STRESS_LATER_SECONDS = (0, 2, 5, 15)
STRESS_WORSE_PRICE = (0, 0.01, 0.02, 0.05)
# Turbos made up from an index (src/backtest/turbo_model.py): measured on the recorded ones, kept here.
TURBO_MODEL_FOLDER = "_turbo_model"
MODEL_FILE = "turbo_model.json"
# A replay of the journal keeps WATA's real positions of its days beside it.
POSITIONS_FILE = "positions.json"
MODEL_UNDERLYING = "dukascopy/us100"
RECORDED, MODELLED = "recorded", "modelled"
# Two years of an index: the turbos made up cost a few tenths of a second a day.
MODELLED_MAX_PERIOD_DAYS = 731
# The recorded days measured at most at once, and the time it may take.
MEASURE_MAX_DAYS = 92
MEASURE_TIMEOUT_SECONDS = 1800

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
# The confidences an entry is put in: (under, label). Without one: "Not given".
_CONFIDENCES = ((0.5, "Under 0.5"), (1.0, "0.5 to 1"), (1.5, "1 to 1.5"), (float("inf"), "1.5 and above"))
NO_CONFIDENCE = "Not given"
# The turbo's price at entry: (under, label).
_PRICES = ((5.0, "Under 5"), (7.0, "5 to 7"), (10.0, "7 to 10"), (15.0, "10 to 15"), (float("inf"), "15 and above"))


def _confidence_group(confidence) -> str:
    if confidence is None:
        return NO_CONFIDENCE
    return next(label for under, label in _CONFIDENCES if confidence < under)


def _price_group(price) -> str | None:
    if price is None:
        return None
    return next(label for under, label in _PRICES if price < under)


def _text(value, what: str, maximum: int, *, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise InvalidRequest(f"{what} is a text")
    value = value.strip()
    if required and not value:
        raise InvalidRequest(f"{what} is needed")
    if len(value) > maximum:
        raise InvalidRequest(f"{what} is {maximum} characters at most")
    return value


def checked_tags(tags) -> list[str]:
    if tags is None:
        return []
    if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
        raise InvalidRequest("Tags are texts")
    cleaned = [tag.strip() for tag in tags if tag.strip()]
    if len(cleaned) > MAX_TAGS or any(len(tag) > MAX_TAG_LENGTH for tag in cleaned):
        raise InvalidRequest(f"{MAX_TAGS} tags at most, of {MAX_TAG_LENGTH} characters at most each")
    # Each once, in the order they were given.
    return list(dict.fromkeys(cleaned))


def checked_asset(asset) -> dict:
    """An asset of the Data pages a strategy can run on: Capital.com, Dukascopy, or one the recorder kept."""
    if not isinstance(asset, dict) or asset.get("provider") not in CHART_PROVIDERS or not isinstance(asset.get("key"), str):
        raise InvalidRequest("The asset is one of the Data pages': capital/<epic>, dukascopy/<label> or saxo/<id>")
    provider, key = asset["provider"], asset["key"]
    try:
        {"capital": capital_epic, "dukascopy": history_label, "saxo": saxo_instrument_id}[provider](key)
    except AssetNotFound as e:
        raise InvalidRequest(str(e)) from None
    return {"provider": provider, "key": key}


# What a run was doing, by the phase its progress says.
_PHASES = {"signals": "running its script", "days": "trading its days", "report": "writing its report"}


def failure_of(code: int | None, progress: dict | None, memory: tuple[int, int] | None,
               kills_before: int | None, kills_after: int | None) -> dict:
    """What is known of a run that ended badly: how it ended, where it was, the container's memory then."""
    progress = progress or {}
    found = {"exit": code, "said": exit_said(code)}
    for name in ("phase", "day", "done", "total", "engine_day", "bars", "runner_peak_mb"):
        if progress.get(name) is not None:
            found[name] = progress[name]
    if memory is not None:
        found["memory_mb"] = {"used": round(memory[0] / 1024 ** 2), "limit": round(memory[1] / 1024 ** 2)}
    if kills_before is not None and kills_after is not None:
        found["oom_kills"] = max(0, kills_after - kills_before)
    return found


def failure_sentence(failure: dict) -> str:
    """A run's failure in a sentence, for its error."""
    said = [f"The backtest was {failure['said']}" if failure.get("exit", 0) < 0 else f"The backtest {failure['said']}"]
    if failure.get("phase"):
        where = f"while {_PHASES.get(failure['phase'], failure['phase'])}"
        if failure.get("total"):
            where += f" ({failure.get('done', 0)} of {failure['total']}"
            where += f", {failure['day']})" if failure.get("day") else ")"
        said.append(where)
    if failure.get("engine_day") and failure.get("phase") == "signals":
        engine = f"on the engine day from {failure['engine_day']}"
        if failure.get("bars") is not None:
            engine += f", {failure['bars']:,} bars"
        if failure.get("runner_peak_mb") is not None:
            engine += f", the runner having held {failure['runner_peak_mb']:,.0f} MB at most"
        said.append(engine)
    if failure.get("memory_mb"):
        said.append(f"the admin used {failure['memory_mb']['used']:,} of its {failure['memory_mb']['limit']:,} MB then")
    if failure.get("oom_kills"):
        said.append(f"the kernel's OOM killer stopped {failure['oom_kills']} process(es) during the run")
    return ", ".join(said) + "."


def wants_measured(request: dict, settings) -> bool:
    """Whether a run, or one cell of its sweep, fills its orders by drawing from WATA's own."""
    if settings.execution == MEASURED:
        return True
    axes = (request.get("sweep") or {}).get("axes") or []
    return any(axis.get("name") == "execution" and MEASURED in axis.get("values", []) for axis in axes)


def checked_chart(chart) -> dict:
    if not isinstance(chart, dict):
        raise InvalidRequest("The run needs the asset its days are drawn on")
    side = chart.get("side", "bid")
    if side not in SIDES:
        raise InvalidRequest("The price side is bid or ask")
    try:
        bars = checked_bars(chart.get("bars"))
    except InvalidRule as e:
        raise InvalidRequest(str(e)) from None
    return {"asset": checked_asset(chart.get("asset")), "side": side, "bars": bars}


def _date(value, what: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise InvalidRequest(f"{what} is a day, YYYY-MM-DD") from None


def _plain(value):
    """A value of the journal as a JSON file keeps it: a moment as a time of UTC, a decimal as a number."""
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _checked_windows(asked) -> dict | None:
    """The windows of a walk-forward: days to choose on, days to judge on."""
    if asked in (None, {}):
        return None
    if not isinstance(asked, dict):
        raise InvalidRequest("A walk-forward is its days to choose on and its days to judge on")
    windows = {}
    for name in ("train_days", "test_days"):
        value = asked.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_WINDOW_DAYS:
            raise InvalidRequest(f"The walk-forward's {name.replace('_', ' ')} are 1 to {MAX_WINDOW_DAYS}")
        windows[name] = value
    return windows


def _research_fields(made_by, made_via, experiment, lockbox) -> dict:
    """What a run says of who asked for it; nothing for the operator's own runs."""
    fields = {"made_by": made_by, "made_via": made_via, "experiment": experiment, "lockbox": lockbox}
    return {key: value for key, value in fields.items() if value is not None}


def _derived(request: dict) -> dict:
    """
    What a run made from another (a stress sweep, a sweep's combination, a model check) keeps of its request: it
    says by itself who asked for it, and it is an ordinary run, never a paper day or report of its own.
    """
    return {key: value for key, value in request.items() if key not in RESEARCH_KEYS and key != "paper"}


def _checked_count(value, what: str) -> int:
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 1_000_000:
        raise InvalidRequest(f"{what} is a whole number")
    return value


class BacktestService:
    def __init__(
        self,
        *,
        root: str,
        prices_root: str,
        config_path: str,
        scripts: ScriptStore,
        launcher: Launcher = launch,
        publish: Callable[..., None] | None = None,
        clock: Callable[[], float] = time.time,
        data: str = "database",
        node: str = "node",
        workers: int = 1,
        max_queued: int = MAX_QUEUED,
        max_period_days: int = MAX_PERIOD_DAYS,
        signal_cache_bytes: int | None = None,
    ):
        self._root = root
        self._runs_root = os.path.join(root, RUNS_FOLDER)
        self._prices_root = prices_root
        self._config_path = config_path
        self._scripts = scripts
        self._launcher = launcher
        self._publish = publish
        self._clock = clock
        # Where the command reads the recorded turbos: the database of the configuration, or files:<folder>.
        self._data = data
        self._node = node
        # On the server one at a time, beside the trading services; a research machine runs several.
        self._workers = max(1, workers)
        self.max_queued = max_queued
        self.max_period_days = max_period_days
        # The runs waiting, in the order they were asked for; who asked through an agent, for those that were.
        self._waiting: list[str] = []
        self._owners: dict[str, str] = {}
        self._wakeup = asyncio.Event()
        self._cancelled: set[str] = set()
        # The runner's answers kept for the runs asked again: pruned, oldest first, past this size (None: never).
        self._signal_cache_bytes = signal_cache_bytes
        self._cache_pruned_at = 0.0
        # The runs going on, and their command.
        self._running: dict[str, Process] = {}
        # The turbos being measured, when they are.
        self._measuring: Process | None = None
        self._measure_task: asyncio.Task | None = None
        # What a run made now is made with: read once, the code does not change under a running admin.
        self._code = code_digest()

    # ── the live settings ──

    def live_settings(self, capital: float | None = None) -> BacktestSettings:
        try:
            with open(self._config_path, encoding="utf-8") as config_file:
                return from_config(json.load(config_file), capital=capital)
        except (OSError, ValueError, KeyError, TypeError) as e:
            raise NotPossible(f"The live configuration cannot be read: {e}") from e

    def options(self, balance: float | None = None) -> dict:
        """
        What the form offers: every setting with its live value, the limits of a
        run, and the account a run starts with beside the live one (``balance``,
        the account's value at the last daily report), which it may start from.
        """
        settings = self.live_settings()
        return {
            "fields": setting_options(settings),
            "groups": list(GROUPS),
            "timezone": settings.timezone,
            "indices": list(settings.allowed_indices),
            "capital": {"value": settings.capital, "balance": balance},
            "signal_delay": DEFAULT_SIGNAL_DELAY,
            # What a run shows but does not change: they are the configuration's own.
            "fixed": {
                "time_periods": [vars(period) for period in settings.time_periods],
                "confidence_bands": [vars(band) for band in settings.confidence_bands],
                "default_confidence": settings.default_confidence,
                "closed_dates": len(settings.closed_dates),
            },
            "baseline": {"repetitions": DEFAULT_REPETITIONS, "most": MAX_REPETITIONS, "seed": DEFAULT_SEED},
            "sweep": {
                "axes": MAX_AXES, "cells": MAX_CELLS, "values": MAX_VALUES, "input_sets": MAX_INPUT_SETS,
                "held_out": DEFAULT_HELD_OUT, "least": LEAST_HELD_OUT, "most": MOST_HELD_OUT,
            },
            "limits": {
                "period_days": self.max_period_days, "queued": self.max_queued, "name_length": MAX_NAME_LENGTH,
                "tags": MAX_TAGS, "tag_length": MAX_TAG_LENGTH, "modelled_period_days": self.modelled_period_days,
            },
            # Turbos made up from an index, once measured: what they were measured on, and how well.
            "turbo_model": self._model_summary(read_json(self._model_path())),
        }

    @property
    def modelled_period_days(self) -> int:
        return max(self.max_period_days, MODELLED_MAX_PERIOD_DAYS)

    # ── turbos made up from an index ──

    def _model_path(self, name: str = "calibration.json") -> str:
        return os.path.join(self._root, TURBO_MODEL_FOLDER, name)

    @staticmethod
    def _model_summary(calibration: dict | None) -> dict | None:
        if not calibration or not calibration.get("directions"):
            return None
        days = calibration.get("days") or []
        return {
            "made_at": calibration.get("made_at"), "underlying": calibration.get("underlying"), "indice": calibration.get("indice"),
            "days": len(days), "first": days[0] if days else None, "last": days[-1] if days else None,
            "few_days": bool(calibration.get("few_days")), "directions": calibration["directions"],
            "quoting": calibration.get("quoting"), "issuer": calibration.get("issuer"),
        }

    def turbo_model(self) -> dict:
        """The measure of the turbos the model makes up, and whether one is being made."""
        status = read_json(self._model_path("status.json")) or {}
        state = status.get("state") or "idle"
        if state == RUNNING and self._measuring is None and self._measure_task is None:
            state = INTERRUPTED
        return {
            "state": state, "error": status.get("error"), "asked": status.get("asked"),
            "finished_at": status.get("finished_at"),
            "measure": self._model_summary(read_json(self._model_path())),
            "underlying": MODEL_UNDERLYING, "max_days": MEASURE_MAX_DAYS,
        }

    def measure_turbos(self, first_day: date, last_day: date) -> dict:
        """Measure the recorded turbos of a period against the index, in a command of its own; one at a time."""
        if self._measuring is not None or self._measure_task is not None:
            raise NotPossible("The turbos are being measured already")
        if last_day < first_day:
            raise InvalidRequest("The period ends before it starts")
        if (last_day - first_day).days >= MEASURE_MAX_DAYS:
            raise InvalidRequest(f"{MEASURE_MAX_DAYS} days at most")
        if first_day > datetime.fromtimestamp(self._clock(), timezone.utc).date():
            raise InvalidRequest("Nothing was recorded in the future")
        os.makedirs(self._model_path("").rstrip(os.sep), exist_ok=True)
        write_json(self._model_path("status.json"), {
            "state": RUNNING, "asked": {"from": first_day.isoformat(), "to": last_day.isoformat()}, "started_at": utc(self._clock()),
        })
        self._measure_task = asyncio.get_running_loop().create_task(self._measure(first_day, last_day))
        return self.turbo_model()

    async def _measure(self, first_day: date, last_day: date) -> None:
        live = self.live_settings()
        command = [
            "src.backtest.turbo_model", "calibrate", "--from", first_day.isoformat(), "--to", last_day.isoformat(),
            "--indice", (list(live.allowed_indices) or ["us100"])[0], "--underlying", MODEL_UNDERLYING,
            "--timezone", live.timezone, "--data", self._data, "--prices", self._prices_root,
            "--out", self._model_path(), "--low-priority",
        ]
        error, code = None, None
        try:
            try:
                self._measuring = await self._launcher(command, self._model_path("output.log"))
            except Exception as e:
                error = f"The measure could not be started: {e}"
            else:
                try:
                    code = await asyncio.wait_for(self._measuring.wait(), MEASURE_TIMEOUT_SECONDS)
                except asyncio.TimeoutError:
                    self._measuring.kill()
                    await self._measuring.wait()
                    error = f"Stopped after {MEASURE_TIMEOUT_SECONDS // 60} minutes"
            if error is None and code != 0:
                error = "\n".join(tail(self._model_path("output.log"), 5)) or f"The command ended with code {code}"
            state = FAILED if error else DONE
            status = read_json(self._model_path("status.json")) or {}
            write_json(self._model_path("status.json"), {**status, "state": state, "error": error, "finished_at": utc(self._clock())})
            self._announce("turbos_measured", state=state)
        finally:
            self._measuring = None
            self._measure_task = None

    # ── asking for one ──

    def _pine_rule(self, source: dict, indice: str) -> tuple[dict, list[tuple[dict, str]], str]:
        """The rule a Pine source runs as, the sources to keep with the run, and the script's name."""
        script = source.get("script")
        if not isinstance(script, dict):
            raise InvalidRequest("A Pine run names one of the scripts and its version")
        store = RuleStore(self._scripts)
        try:
            pinned, libraries = store.pin(script.get("id"), script.get("version"))
            meta = self._scripts.script(pinned["id"])
            rule = {
                "id": REPLAY_RULE_ID,
                "name": meta["name"],
                "asset": checked_asset(source.get("asset")),
                "side": source.get("side", "bid"),
                "bars": checked_bars(source.get("bars")),
                "script": pinned,
                "libraries": libraries,
                "inputs": checked_inputs(source.get("inputs")),
                "alerts": checked_alerts(source.get("alerts")),
                "alert_calls": source.get("alert_calls", IGNORE),
                "confidence_plot": source.get("confidence_plot") or None,
                "indice": indice,
                "mode": "shadow",
            }
            if rule["side"] not in SIDES:
                raise InvalidRequest("The price side is bid or ask")
            if rule["alert_calls"] not in ALERT_CALL_ACTIONS:
                raise InvalidRequest("alert() calls are ignored or read as messages")
            if rule["confidence_plot"] is not None and (
                not isinstance(rule["confidence_plot"], str) or len(rule["confidence_plot"]) > 120
            ):
                raise InvalidRequest("The confidence plot is named by its title")
            store.verify(rule)
            sources = [(pinned, self._scripts.source(pinned["id"], pinned["version"]))]
            sources += [(library, self._scripts.source(library["id"], library["version"])) for library in libraries]
        except (InvalidRule, ScriptNotFound) as e:
            raise InvalidRequest(str(e)) from None
        if not acts(rule):
            raise InvalidRequest("No alert of the script is made a signal: map one to an action")
        return rule, sources, meta["name"]

    def _checked(self, body: dict, journal: list[dict] | None, capital: float | None,
                 execution: dict | None = None) -> tuple[dict, dict, list, list | None]:
        if not isinstance(body, dict):
            raise InvalidRequest("A backtest is asked for with its source, period and settings")
        first_day, last_day = _date(body.get("from"), "The first day"), _date(body.get("to"), "The last day")
        if last_day < first_day:
            raise InvalidRequest("The period ends before it starts")
        turbos = body.get("turbos") or RECORDED
        if turbos not in (RECORDED, MODELLED):
            raise InvalidRequest("The turbos are the recorded ones or made up from the index")
        model = None
        if turbos == MODELLED:
            model = read_json(self._model_path())
            if not self._model_summary(model):
                raise NotPossible("The turbos have not been measured yet: measure them on the recorded days first")
        limit = self.modelled_period_days if turbos == MODELLED else self.max_period_days
        if (last_day - first_day).days >= limit:
            raise InvalidRequest(f"A period of {limit} days at most")
        live = self.live_settings(capital)
        today = datetime.fromtimestamp(self._clock(), timezone.utc).date()
        if first_day > today:
            raise InvalidRequest("Nothing was recorded in the future")
        indice = body.get("indice")
        if indice not in live.allowed_indices:
            raise InvalidRequest(f"The indice is one the trader takes: {', '.join(live.allowed_indices) or 'none'}")
        if model is not None and model.get("indice") != indice:
            raise InvalidRequest(f"The turbos were measured for {model.get('indice')}, not {indice}")

        source = body.get("source")
        if not isinstance(source, dict) or source.get("kind") not in (PINE, JOURNAL, TA_SIGNALS):
            raise InvalidRequest("The signals are a Pine script's, those WATA received, or those a rule raised")
        sources: list = []
        kept_journal = None
        if source["kind"] == PINE:
            rule, sources, script_name = self._pine_rule(source, indice)
            from_rule = source.get("from_rule")
            kept_source = {
                "kind": PINE, "rule": rule,
                "from_rule": {"id": str(from_rule.get("id"))[:12], "name": _text(from_rule.get("name"), "The rule's name", 80)}
                if isinstance(from_rule, dict) else None,
            }
            chart = {"asset": rule["asset"], "side": rule["side"], "bars": rule["bars"]}
            default_name = f"{script_name} on {rule['asset']['key']}"
        elif source["kind"] == TA_SIGNALS:
            rule_ref = source.get("rule") if isinstance(source.get("rule"), dict) else {}
            if not rule_ref.get("id"):
                raise InvalidRequest("The signals a rule raised are named by the rule")
            kept_journal = [{key: _plain(value) for key, value in row.items()} for row in journal or []]
            kept_source = {
                "kind": TA_SIGNALS, "signals": len(kept_journal),
                "rule": {"id": str(rule_ref["id"])[:12], "name": _text(rule_ref.get("name"), "The rule's name", 80)},
            }
            # The versions of the rule's script that raised them: a rule changed during the period raised them by several.
            versions = sorted({row["script_version"] for row in kept_journal
                               if isinstance(row.get("script_version"), int) and not isinstance(row["script_version"], bool)})
            if versions:
                kept_source["versions"] = versions
            chart = checked_chart(body.get("chart"))
            default_name = f"Paper: {kept_source['rule']['name'] or kept_source['rule']['id']}"
        else:
            entries = [row for row in journal or [] if row.get("action") in ("long", "short")]
            if not entries:
                raise NotPossible(NO_ENTRY_SIGNAL)
            kept_journal = [{key: _plain(value) for key, value in row.items()} for row in journal or []]
            kept_source = {"kind": JOURNAL, "signals": len(journal or []), "entries": len(entries)}
            chart = checked_chart(body.get("chart"))
            default_name = "Signals WATA received"

        changes = body.get("changes") or {}
        if not isinstance(changes, dict):
            raise InvalidRequest("The changes are given by setting name")
        # A rule's raised signals have had the engine's time already: what remains is the journal's.
        defaults = {"signal_delay_seconds": DEFAULT_SIGNAL_DELAY.get(source["kind"], DEFAULT_SIGNAL_DELAY[JOURNAL])}
        try:
            base, _, _ = with_changes(live, defaults)
            settings, kept, before = with_changes(base, changes)
        except InvalidSettings as e:
            raise InvalidRequest(str(e)) from None

        sweep = body.get("sweep")
        baseline = None
        if sweep:
            if not isinstance(sweep, dict):
                raise InvalidRequest("A sweep names its axes")
            known_inputs = None
            if source["kind"] == PINE:
                # Any input of the script: PineTS names them in_0, in_1... in their order.
                known_inputs = {axis.get("name") for axis in sweep.get("axes") or [] if isinstance(axis, dict)}
            try:
                axes = checked_axes(sweep.get("axes"), inputs_known=known_inputs)
                for axis in axes:
                    for value in axis.values:
                        if axis.kind == SETTING:
                            with_changes(settings, {axis.name: value})
                        else:
                            checked_inputs([{"id": axis.name, "value": value}])
            except (InvalidSweep, InvalidSettings, InvalidRule) as e:
                raise InvalidRequest(str(e)) from None
            held_out = sweep.get("held_out", DEFAULT_HELD_OUT)
            if isinstance(held_out, bool) or not isinstance(held_out, (int, float)) or not LEAST_HELD_OUT <= held_out <= MOST_HELD_OUT:
                raise InvalidRequest(f"The days held out are {LEAST_HELD_OUT:.0%} to {MOST_HELD_OUT:.0%} of them")
            sweep = {
                "axes": [{"kind": axis.kind, "name": axis.name, "values": list(axis.values)} for axis in axes],
                "held_out": float(held_out),
                "walk_forward": _checked_windows(sweep.get("walk_forward")),
                "trials_before": _checked_count(sweep.get("trials_before"), "The trials made before"),
            }
        else:
            asked = body.get("baseline") or {}
            repetitions = asked.get("repetitions", DEFAULT_REPETITIONS)
            seed = asked.get("seed", DEFAULT_SEED)
            if isinstance(repetitions, bool) or not isinstance(repetitions, int) or not 0 <= repetitions <= MAX_REPETITIONS:
                raise InvalidRequest(f"0 to {MAX_REPETITIONS} repetitions with entries at random")
            if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
                raise InvalidRequest("The seed of the random entries is a whole number")
            baseline = {"repetitions": repetitions, "seed": seed}

        request = {
            "format": 1,
            "kind": "sweep" if sweep else "run",
            "name": _text(body.get("name"), "The name", MAX_NAME_LENGTH) or default_name,
            "tags": checked_tags(body.get("tags")),
            "from": first_day.isoformat(),
            "to": last_day.isoformat(),
            "indice": indice,
            "source": kept_source,
            "chart": chart,
            "changes": kept,
            "baseline": baseline,
            "sweep": sweep,
            "turbos": turbos,
            "asked_at": utc(self._clock()),
        }
        paper = body.get("paper")
        if paper is not None:
            # One day, or the report of the days since it began: of the signals one rule raised, or of those WATA
            # received under the live settings.
            owner = kept_source["rule"]["id"] if source["kind"] == TA_SIGNALS else LIVE_PAPER if source["kind"] == JOURNAL else None
            if owner is None or not isinstance(paper, dict) or paper.get("rule") != owner:
                raise InvalidRequest("A paper run is one day of the signals one rule raised, or of those WATA received")
            if paper.get("report") is True:
                request["paper"] = {"rule": owner, "report": True}
            elif paper.get("day") == first_day.isoformat() and first_day == last_day:
                request["paper"] = {"rule": owner, "day": first_day.isoformat()}
            else:
                raise InvalidRequest("A paper run is one day of the signals one rule raised, or of those WATA received")
        settings_doc = {"settings": settings.to_dict(), "changed": kept, "live_before": before}
        if model is not None:
            # The measure the run is made with goes with it: a later one does not change what it found.
            settings_doc["turbo_model"] = model
        if wants_measured(request, settings):
            # So do WATA's orders it draws its fills from.
            short = missing((execution or {}).get("samples"))
            if short:
                raise NotPossible(
                    f"A run on measured fills draws each order from WATA's own, and too few were measured "
                    f"in the last months ({', '.join(short)}; 10 of each at least): use fixed fills"
                )
            settings_doc["execution"] = execution
        return request, settings_doc, sources, kept_journal

    def _new_run_id(self) -> str:
        moment = datetime.fromtimestamp(self._clock(), timezone.utc)
        return f"{moment:%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"

    def _check_room(self) -> None:
        if len(self._waiting) + len(self._running) >= self.max_queued:
            raise NotPossible(f"{self.max_queued} backtests are waiting already: let some finish")

    @property
    def code(self) -> str:
        """The digest of the code a run made now is made with: a run made with another is stale."""
        return self._code

    def queue(self) -> dict:
        """What runs now, and what waits in the order it will start."""
        return {"running": list(self._running), "waiting": self._in_turn()}

    @property
    def under_way(self) -> int:
        """How many children run now (runs, a measure of the turbos): a restart of the admin stops them."""
        return len(self._running) + (1 if self._measuring is not None else 0)

    def waiting_of(self, made_by: str) -> int:
        """How many runs asked for by ``made_by`` wait their turn."""
        return sum(1 for run_id in self._waiting if self._owners.get(run_id) == made_by)

    def create(self, body: dict, *, journal: list[dict] | None = None, positions: list[dict] | None = None,
               capital: float | None = None, made_by: str | None = None, made_via: str | None = None,
               experiment: str | None = None, lockbox: dict | None = None, execution: dict | None = None) -> dict:
        """
        Ask for a run (or a sweep): checked, written to its folder, and queued.
        A replay of the journal keeps WATA's real positions of the period
        (``positions``) beside it, to be set against them. ``made_by`` and the
        rest say who asked through an agent, and for which experiment.
        ``execution``: WATA's orders measured, which a run on measured fills draws from.
        """
        self._check_room()
        request, settings_doc, sources, kept_journal = self._checked(body, journal, capital, execution)
        request.update(_research_fields(made_by, made_via, experiment, lockbox))
        kept_positions = [{key: _plain(value) for key, value in row.items()} for row in positions] \
            if positions is not None and kept_journal is not None and request["source"]["kind"] == JOURNAL else None
        if request["source"]["kind"] == TA_SIGNALS:
            made = self._queue_run(request, settings_doc, sources, None, raised=kept_journal)
        else:
            made = self._queue_run(request, settings_doc, sources, kept_journal, positions=kept_positions)
        paper = request.get("paper")
        if paper and paper.get("report"):
            self._index_paper_report(paper["rule"], made["id"])
        elif paper:
            self._index_paper(paper["rule"], paper["day"], made["id"])
        return made

    def _index_paper(self, owner: str, day: str, run_id: str) -> None:
        index = self.paper_index()
        index.setdefault(owner, {})[day] = run_id
        self._write_paper("index.json", index)

    def mark_live_paper_without_entry(self, day: str) -> None:
        """A day on which WATA received no entry signal: a paper day with nothing to trade, not asked again."""
        self._index_paper(LIVE_PAPER, day, NO_ENTRY)

    def _queue_run(self, request: dict, settings_doc: dict, sources: list, journal: list | None, copy_from: str | None = None,
                   *, positions: list | None = None, raised: list | None = None,
                   recorded_trades: list | None = None) -> dict:
        run_id = self._new_run_id()
        folder = os.path.join(self._runs_root, run_id)
        os.makedirs(os.path.join(folder, "sources"))
        settings_doc = dict(settings_doc)
        model = settings_doc.pop("turbo_model", None)
        if model is None and copy_from is not None:
            model = read_json(os.path.join(copy_from, MODEL_FILE))
        execution = settings_doc.pop("execution", None)
        if execution is None and copy_from is not None:
            execution = read_json(os.path.join(copy_from, EXECUTION_FILE))
        if positions is None and copy_from is not None:
            positions = read_json(os.path.join(copy_from, POSITIONS_FILE))
        if raised is None and copy_from is not None:
            raised = read_json(os.path.join(copy_from, RAISED_FILE))
        try:
            if model is not None:
                write_json(os.path.join(folder, MODEL_FILE), model)
            if execution is not None:
                write_json(os.path.join(folder, EXECUTION_FILE), execution)
            for pin, text in sources:
                with open(os.path.join(folder, "sources", f"{pin['id']}-v{pin['version']}.pine"), "w", encoding="utf-8") as out_file:
                    out_file.write(text)
            if copy_from is not None:
                for name in os.listdir(os.path.join(copy_from, "sources")):
                    shutil.copyfile(os.path.join(copy_from, "sources", name), os.path.join(folder, "sources", name))
            if journal is not None:
                write_json(os.path.join(folder, "journal.json"), journal)
            if positions is not None:
                write_json(os.path.join(folder, POSITIONS_FILE), positions)
            if raised is not None:
                write_json(os.path.join(folder, RAISED_FILE), raised)
            if recorded_trades is not None:
                write_json(os.path.join(folder, RECORDED_TRADES_FILE), recorded_trades)
            write_json(os.path.join(folder, "settings.json"), settings_doc)
            write_json(os.path.join(folder, "status.json"), {"state": QUEUED, "created_at": utc(self._clock())})
            # Last: a folder without its request is not a run.
            write_json(os.path.join(folder, "request.json"), request)
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        self._waiting.append(run_id)
        if request.get("made_by"):
            self._owners[run_id] = request["made_by"]
        self._wakeup.set()
        return self.get(run_id)

    def run_cell(self, run_id: str, cell: int, *, made_by: str | None = None, made_via: str | None = None,
                 experiment: str | None = None, lockbox: dict | None = None) -> dict:
        """A combination of a sweep made a run of its own, with its random repetitions."""
        self._check_room()
        folder = self._directory(run_id)
        request = read_json(os.path.join(folder, "request.json")) or {}
        sweep = read_json(os.path.join(folder, "sweep.json"))
        settings_doc = read_json(os.path.join(folder, "settings.json"))
        if request.get("kind") != "sweep" or sweep is None or settings_doc is None:
            raise NotPossible("Only a finished sweep has combinations to run")
        if not isinstance(cell, int) or not 0 <= cell < len(sweep["cells"]):
            raise InvalidRequest("No such combination")
        values = sweep["cells"][cell]["values"]
        axes = request["sweep"]["axes"]
        changes = dict(request.get("changes") or {})
        source = json.loads(json.dumps(request["source"]))
        for axis, value in zip(axes, values):
            if axis["kind"] == SETTING:
                changes[axis["name"]] = value
            else:
                inputs = [entry for entry in source["rule"]["inputs"] if entry["id"] != axis["name"]]
                source["rule"]["inputs"] = sorted(
                    inputs + [{"id": axis["name"], "var": None, "title": None, "value": value}],
                    key=lambda entry: int(entry["id"][3:]),
                )
        settings = BacktestSettings.from_dict(settings_doc["settings"])
        try:
            settings, _, _ = with_changes(settings, {name: value for name, value in changes.items()})
        except InvalidSettings as e:
            raise InvalidRequest(str(e)) from None
        described = ", ".join(f"{axis['name']} {value}" for axis, value in zip(axes, values))
        new_request = {
            **_derived(request), "kind": "run", "sweep": None, "changes": changes, "source": source,
            "name": f"{request['name']} ({described})"[:MAX_NAME_LENGTH],
            "baseline": {"repetitions": DEFAULT_REPETITIONS, "seed": DEFAULT_SEED},
            "asked_at": utc(self._clock()), "from_sweep": {"id": run_id, "cell": cell},
            **_research_fields(made_by, made_via, experiment, lockbox),
        }
        new_settings = {**settings_doc, "settings": settings.to_dict(), "changed": changes}
        journal = read_json(os.path.join(folder, "journal.json"))
        return self._queue_run(new_request, new_settings, [], journal, copy_from=folder)

    def model_check(self, run_id: str, *, made_by: str | None = None, made_via: str | None = None,
                    experiment: str | None = None, lockbox: dict | None = None) -> dict:
        """
        A finished run on the recorded turbos made again on turbos made up from
        the index, by the measure of now: the same signals on the same days,
        without random entries. The run it checks keeps its trades in the new
        folder, which pairs its own with them by signal (``model_check.json``):
        how far the made-up turbos are from the recorded ones.
        """
        self._check_room()
        folder = self._directory(run_id)
        request = read_json(os.path.join(folder, "request.json")) or {}
        settings_doc = read_json(os.path.join(folder, "settings.json"))
        trades = read_json(os.path.join(folder, "trades.json"))
        if (request.get("kind") != "run" or (request.get("turbos") or RECORDED) != RECORDED
                or self._status(run_id).get("state") != DONE or settings_doc is None or trades is None):
            raise NotPossible("Only a finished run on the recorded turbos can be set beside made-up ones")
        model = read_json(self._model_path())
        if not self._model_summary(model):
            raise NotPossible("The turbos have not been measured yet: measure them on the recorded days first")
        if model.get("indice") != request.get("indice"):
            raise NotPossible(f"The turbos were measured for {model.get('indice')}, not {request.get('indice')}")
        new_request = {
            **{key: value for key, value in _derived(request).items() if key not in ("from_sweep", "stress_of")},
            "turbos": MODELLED, "baseline": {**(request.get("baseline") or {}), "repetitions": 0},
            "name": f"Model check: {request.get('name') or run_id}"[:MAX_NAME_LENGTH],
            "asked_at": utc(self._clock()), "model_check_of": run_id,
            **_research_fields(made_by, made_via, experiment, lockbox),
        }
        journal = read_json(os.path.join(folder, "journal.json"))
        return self._queue_run(new_request, {**settings_doc, "turbo_model": model}, [], journal, copy_from=folder,
                               recorded_trades=trades)

    def stress(self, run_id: str, *, made_by: str | None = None, made_via: str | None = None,
               experiment: str | None = None, lockbox: dict | None = None) -> dict:
        """
        A finished run made again as a sweep, with each order filled later and
        at a worse price than it was: the same signals on the same days, the
        run's own fills first. What it says is how much of the result its
        fills hold.
        """
        self._check_room()
        folder = self._directory(run_id)
        request = read_json(os.path.join(folder, "request.json")) or {}
        settings_doc = read_json(os.path.join(folder, "settings.json"))
        if request.get("kind") != "run" or self._status(run_id).get("state") != DONE or settings_doc is None:
            raise NotPossible("Only a finished run can be stressed")
        settings = BacktestSettings.from_dict(settings_doc["settings"])
        axes = [
            {"kind": SETTING, "name": "order_seconds",
             "values": [round(settings.order_seconds + later, 6) for later in STRESS_LATER_SECONDS]},
            {"kind": SETTING, "name": "slippage",
             "values": [round(settings.slippage + worse, 6) for worse in STRESS_WORSE_PRICE]},
        ]
        try:
            for axis in checked_axes(axes, inputs_known=None):
                for value in axis.values:
                    with_changes(settings, {axis.name: value})
        except (InvalidSweep, InvalidSettings) as e:
            raise NotPossible(f"This run cannot be stressed: {e}") from None
        new_request = {
            **_derived(request), "kind": "sweep", "baseline": None,
            "name": f"Stress: {request.get('name') or run_id}"[:MAX_NAME_LENGTH],
            "sweep": {"axes": axes, "held_out": DEFAULT_HELD_OUT, "walk_forward": None, "trials_before": 0},
            "asked_at": utc(self._clock()), "stress_of": run_id,
            **_research_fields(made_by, made_via, experiment, lockbox),
        }
        journal = read_json(os.path.join(folder, "journal.json"))
        return self._queue_run(new_request, settings_doc, [], journal, copy_from=folder)

    # ── folders ──

    def _directory(self, run_id: str) -> str:
        if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
            raise UnknownRun(run_id)
        path = os.path.join(self._runs_root, run_id)
        if not os.path.isfile(os.path.join(path, "request.json")):
            raise UnknownRun(run_id)
        return path

    def _file(self, run_id: str, name: str) -> str:
        return os.path.join(self._directory(run_id), name)

    def _status(self, run_id: str) -> dict:
        return read_json(self._file(run_id, "status.json")) or {"state": FAILED, "error": "Its state cannot be read"}

    def _set_status(self, run_id: str, **changes) -> dict:
        status = {**self._status(run_id), **changes}
        write_json(self._file(run_id, "status.json"), status)
        return status

    def _run_folders(self) -> list[str]:
        try:
            names = os.listdir(self._runs_root)
        except OSError:
            return []
        return [name for name in names if RUN_ID.fullmatch(name)]

    def _run_ids(self) -> list[str]:
        """The runs, newest first: the folders that hold their request (it is written last)."""
        return sorted(
            (name for name in self._run_folders() if os.path.isfile(os.path.join(self._runs_root, name, "request.json"))),
            reverse=True,
        )

    # ── running them, one at a time ──

    def recover(self) -> None:
        """
        Runs a previous admin left waiting or running were never finished: say
        so. A folder left without its request (the admin stopped while making
        it) was never a run: it goes.
        """
        for name in self._run_folders():
            if not os.path.isfile(os.path.join(self._runs_root, name, "request.json")):
                shutil.rmtree(os.path.join(self._runs_root, name), ignore_errors=True)
        for run_id in self._run_ids():
            if self._status(run_id).get("state") in (QUEUED, RUNNING) and run_id not in self._waiting:
                self._set_status(run_id, state=INTERRUPTED, error="The admin stopped before this run was finished")

    def _in_turn(self) -> list[str]:
        """The waiting runs in the order they will start: those an agent asked for give way to the others."""
        return [run_id for run_id in self._waiting if run_id not in self._owners] + \
            [run_id for run_id in self._waiting if run_id in self._owners]

    async def _next(self) -> str:
        """The run to start now, taken out of the line."""
        while not self._waiting:
            self._wakeup.clear()
            await self._wakeup.wait()
        run_id = self._in_turn()[0]
        self._waiting.remove(run_id)
        self._owners.pop(run_id, None)
        return run_id

    async def run(self) -> None:
        """Take the runs asked for in turn, ``workers`` at a time, until cancelled."""
        self.recover()
        slots = asyncio.Semaphore(self._workers)
        going: set[asyncio.Task] = set()
        try:
            while True:
                # A run leaves the line only when it can start: its place in it stays true meanwhile.
                await slots.acquire()
                run_id = await self._next()
                task = asyncio.create_task(self._execute_in(run_id, slots))
                going.add(task)
                task.add_done_callback(going.discard)
        finally:
            for task in going:
                task.cancel()

    async def _execute_in(self, run_id: str, slots: asyncio.Semaphore) -> None:
        try:
            await self._execute(run_id)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Backtest {run_id} failed outside the command itself: {e}", exc_info=True)
            try:
                self._set_status(run_id, state=FAILED, error=str(e), finished_at=utc(self._clock()))
            except Exception:
                pass
        finally:
            slots.release()

    def _command(self, run_id: str) -> list[str]:
        folder = self._directory(run_id)
        data = ["--data", self._data]
        if (read_json(os.path.join(folder, "request.json")) or {}).get("turbos") == MODELLED:
            # Turbos made up from the index, by the measure the run keeps; the index read where the recorded days are.
            data = ["--data", f"model:{os.path.join(folder, MODEL_FILE)}"]
            if self._data.startswith("files:"):
                data += ["--files", self._data.removeprefix("files:")]
        return [
            "src.backtest.run", "--run", folder, *data,
            "--prices", self._prices_root, "--node", self._node, "--low-priority",
            # The runner's answers, read back by the runs of the same script on the same days.
            "--signal-cache", os.path.join(self._root, SIGNAL_CACHE_FOLDER),
        ]

    async def _execute(self, run_id: str) -> None:
        if run_id in self._waiting:
            self._waiting.remove(run_id)
        if run_id in self._cancelled:
            self._cancelled.discard(run_id)
            return
        try:
            request = read_json(self._file(run_id, "request.json"))
        except UnknownRun:
            return
        if request is None:
            self._set_status(run_id, state=FAILED, error="What was asked cannot be read")
            return
        self._set_status(run_id, state=RUNNING, started_at=utc(self._clock()))
        error, code, failure = None, None, None
        kills_before = oom_kills()
        try:
            process = await self._launcher(self._command(run_id), self._file(run_id, "output.log"))
        except Exception as e:
            error = f"The backtest could not be started: {e}"
        else:
            self._running[run_id] = process
            try:
                code = await asyncio.wait_for(process.wait(), RUN_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                error = f"Stopped after {RUN_TIMEOUT_SECONDS // 60} minutes"
            finally:
                self._running.pop(run_id, None)
        if run_id in self._cancelled:
            self._cancelled.discard(run_id)
            state, error = CANCELLED, None
        elif error is None and code == 0 and os.path.exists(self._file(run_id, "summary.json")):
            state = DONE
        else:
            state = FAILED
            if error is None:
                # It ended badly by itself: what is known of where it was and of the memory then.
                failure = failure_of(code, read_json(self._file(run_id, "progress.json")), cgroup_memory(), kills_before, oom_kills())
                lines = "\n".join(tail(self._file(run_id, "output.log"), 5))
                if code is not None and code < 0:
                    error = failure_sentence(failure) + (f"\n{lines}" if lines else "")
                else:
                    error = lines or failure_sentence(failure)
        self._set_status(run_id, state=state, error=error, finished_at=utc(self._clock()),
                         **({"failure": failure} if failure is not None else {}))
        self._announce("backtest_finished", run_id=run_id, state=state, name=request.get("name") or "")
        if self._signal_cache_bytes is not None and self._clock() - self._cache_pruned_at >= CACHE_PRUNE_SECONDS:
            self._cache_pruned_at = self._clock()
            try:
                await asyncio.to_thread(self.prune_signal_cache)
            except OSError as e:
                logger.warning(f"Could not prune the runner's answers kept: {e}")

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
            self._owners.pop(run_id, None)
            self._cancelled.add(run_id)
            self._set_status(run_id, state=CANCELLED, finished_at=utc(self._clock()))
        elif state == RUNNING and run_id in self._running:
            self._cancelled.add(run_id)
            self._running[run_id].terminate()
        else:
            raise NotPossible("Only a backtest that is waiting or running can be stopped")
        return self.get(run_id)

    def delete(self, run_id: str) -> None:
        directory = self._directory(run_id)
        if run_id in self._waiting or run_id in self._running:
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

    def update(self, run_id: str, *, name: str | None = None, tags: list[str] | None = None) -> dict:
        """Name a run again, or tag it: what it is called, not what it was asked."""
        path = self._file(run_id, "request.json")
        request = read_json(path)
        if request is None:
            raise NotPossible("What was asked of this backtest cannot be read")
        if name is not None:
            request["name"] = _text(name, "The name", MAX_NAME_LENGTH, required=True)
        if tags is not None:
            request["tags"] = checked_tags(tags)
        write_json(path, request)
        return self.get(run_id)

    async def close(self) -> None:
        """The admin is stopping: the runs, and a measure of the turbos, stop with it."""
        for process in [*self._running.values(), *([self._measuring] if self._measuring is not None else [])]:
            if process.returncode is None:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass

    # ── disk ──

    def _former(self) -> list[str]:
        try:
            names = os.listdir(self._root)
        except OSError:
            return []
        return [name for name in names if RUN_ID.fullmatch(name) or name in FORMER_FOLDERS]

    def _cache_files(self) -> list[tuple[float, int, str]]:
        """The runner's answers kept: (written at, size, path)."""
        found = []
        for folder, _, names in os.walk(os.path.join(self._root, SIGNAL_CACHE_FOLDER)):
            for name in names:
                path = os.path.join(folder, name)
                try:
                    stat = os.stat(path)
                except OSError:
                    continue
                found.append((stat.st_mtime, stat.st_size, path))
        return found

    def prune_signal_cache(self) -> int:
        """The oldest answers removed until the cache holds ``signal_cache_bytes`` at most; how many went."""
        if self._signal_cache_bytes is None:
            return 0
        files = sorted(self._cache_files())
        total = sum(size for _, size, _ in files)
        removed = 0
        for _, size, path in files:
            if total <= self._signal_cache_bytes:
                break
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            total -= size
            removed += 1
        return removed

    def storage(self) -> dict:
        """The disk the runs take, the runner's answers kept, and what the former backtester left."""
        runs = [folder_bytes(os.path.join(self._runs_root, run_id)) for run_id in self._run_ids()]
        former = self._former()
        cache = self._cache_files()
        return {
            "runs": {"count": len(runs), "bytes": sum(runs)},
            "signal_cache": {"count": len(cache), "bytes": sum(size for _, size, _ in cache),
                             "limit": self._signal_cache_bytes},
            "former": {
                "count": sum(1 for name in former if RUN_ID.fullmatch(name)),
                "bytes": sum(folder_bytes(os.path.join(self._root, name)) for name in former),
            },
        }

    def delete_former(self) -> dict:
        """Remove what the former backtester left: its runs, its measures of the turbo."""
        removed = 0
        for name in self._former():
            shutil.rmtree(os.path.join(self._root, name), ignore_errors=True)
            removed += 1 if RUN_ID.fullmatch(name) else 0
        return {"removed": removed}

    # ── reading them ──

    def _headline(self, summary: dict | None) -> dict | None:
        if not summary:
            return None
        figures = summary.get("figures") or {}
        against = summary.get("against_random") or {}
        return {
            "total_percent": figures.get("total_percent"),
            "trades": figures.get("trades"),
            "win_rate": figures.get("win_rate"),
            "worst_drawdown": figures.get("worst_drawdown"),
            "sample": figures.get("sample"),
            "beaten": against.get("beaten"),
            "days_tested": (summary.get("days") or {}).get("tested"),
            "profit_loss": (summary.get("money") or {}).get("profit_loss"),
            "cells": summary.get("cells"),
        }

    def _described(self, run_id: str, *, whole: bool) -> dict:
        request = read_json(self._file(run_id, "request.json")) or {}
        status = self._status(run_id)
        if status.get("state") == RUNNING and run_id not in self._running:
            status = {**status, "state": INTERRUPTED}
        summary = read_json(self._file(run_id, "summary.json")) if status.get("state") == DONE else None
        source = request.get("source") or {}
        rule = source.get("rule") or {}
        described = {
            "id": run_id,
            "kind": request.get("kind"),
            "name": request.get("name"),
            "tags": request.get("tags") or [],
            "from": request.get("from"),
            "to": request.get("to"),
            "indice": request.get("indice"),
            "source": {
                "kind": source.get("kind"),
                "script": {"id": rule["script"]["id"], "version": rule["script"]["version"], "name": rule.get("name")}
                if rule.get("script") else None,
                # A paper run is the rule's own signals.
                "from_rule": source.get("from_rule") or (rule if source.get("kind") == TA_SIGNALS else None),
                "signals": source.get("signals"),
            },
            "chart": request.get("chart"),
            "changes": request.get("changes") or {},
            # The run a stress sweep made again with worse fills.
            "stress_of": request.get("stress_of"),
            # The run on the recorded turbos a model check made again on made-up ones.
            "model_check_of": request.get("model_check_of"),
            "turbos": request.get("turbos") or RECORDED,
            "paper": request.get("paper"),
            # Who asked for it through an agent, through what (the MCP endpoint's token, or "agent:<conversation>"
            # for the admin's AI agent), and for which experiment of its journal.
            "made_by": request.get("made_by"),
            "made_via": request.get("made_via"),
            "experiment": request.get("experiment"),
            "status": status,
            "headline": self._headline(summary),
            "position": self._in_turn().index(run_id) + 1 if run_id in self._waiting else None,
        }
        if summary is not None:
            described["stale"] = (summary.get("made_with") or {}).get("code") != self._code
        if whole:
            settings = read_json(self._file(run_id, "settings.json")) or {}
            described.update(
                request=request,
                settings=settings,
                summary=summary,
                progress=read_json(self._file(run_id, "progress.json")) if status.get("state") == RUNNING else None,
                lines=tail(self._file(run_id, "output.log")) if status.get("state") in (FAILED, RUNNING) else [],
            )
        return described

    def get(self, run_id: str) -> dict:
        return self._described(run_id, whole=True)

    def state(self, run_id: str) -> dict:
        """Where a run stands, without its results: its state, place in line, and progress while it runs."""
        status = self._status(run_id)
        if status.get("state") == RUNNING and run_id not in self._running:
            status = {**status, "state": INTERRUPTED}
        return {
            "id": run_id, "state": status.get("state"), "error": status.get("error"),
            "position": self._in_turn().index(run_id) + 1 if run_id in self._waiting else None,
            "progress": read_json(self._file(run_id, "progress.json")) if status.get("state") == RUNNING else None,
        }

    def made_by(self, run_id: str) -> str | None:
        """Who asked for a run through an agent; None for the operator's."""
        return (read_json(self._file(run_id, "request.json")) or {}).get("made_by")

    def rule_of(self, run_id: str) -> str | None:
        """The id of the signal rule a run was made from; None for any other run."""
        request = read_json(self._file(run_id, "request.json")) or {}
        return ((request.get("source") or {}).get("from_rule") or {}).get("id")

    def run_ids(self, *, made_by: str | None = None, experiment: str | None = None) -> list[str]:
        """The runs, newest first, without the paper runs; only those of ``made_by`` or ``experiment`` when given."""
        paper = self._paper_ids()
        chosen = []
        for run_id in self._run_ids():
            if run_id in paper:
                continue
            if made_by is not None or experiment is not None:
                request = read_json(os.path.join(self._runs_root, run_id, "request.json")) or {}
                if (made_by is not None and request.get("made_by") != made_by) or \
                        (experiment is not None and request.get("experiment") != experiment):
                    continue
            chosen.append(run_id)
        return chosen

    def list_runs(self, *, made_by: str | None = None, experiment: str | None = None, offset: int = 0,
                  limit: int = MAX_LISTED) -> list[dict]:
        """The runs, newest first; the paper runs of the signal rules have their own place."""
        chosen = self.run_ids(made_by=made_by, experiment=experiment)[offset:offset + limit]
        return [self._described(run_id, whole=False) for run_id in chosen]

    # ── paper trading of the signal rules ──

    def _paper_path(self, name: str) -> str:
        return os.path.join(self._root, PAPER_FOLDER, name)

    def _write_paper(self, name: str, content) -> None:
        os.makedirs(os.path.join(self._root, PAPER_FOLDER), exist_ok=True)
        write_json(self._paper_path(name), content)

    def paper_file(self, name: str):
        """A file kept beside the paper index (the drift said last, say); None when there is none."""
        return read_json(self._paper_path(name))

    def write_paper_file(self, name: str, content) -> None:
        self._write_paper(name, content)

    def paper_index(self) -> dict:
        """By rule, by day: the run made of the signals the rule raised that day."""
        found = read_json(self._paper_path("index.json"))
        return found if isinstance(found, dict) else {}

    def paper_reports(self) -> dict:
        """By rule (or ``live``): its reports' runs, newest first (``runs``), and for ``live`` the day it began (``since``)."""
        found = read_json(self._paper_path(PAPER_REPORTS))
        return found if isinstance(found, dict) else {}

    def _paper_ids(self) -> set[str]:
        days = {run_id for days in self.paper_index().values() for run_id in days.values() if run_id != NO_ENTRY}
        return days | {run_id for entry in self.paper_reports().values() for run_id in entry.get("runs") or []}

    def _index_paper_report(self, owner: str, run_id: str) -> None:
        reports = self.paper_reports()
        entry = reports.setdefault(owner, {})
        entry["runs"] = [run_id, *(other for other in entry.get("runs") or [] if other != run_id)]
        self._write_paper(PAPER_REPORTS, reports)

    def paper_since(self, owner: str) -> str | None:
        """The day an owner's report starts at when nothing else says it (the signals received's first day on paper)."""
        return (self.paper_reports().get(owner) or {}).get("since")

    def keep_paper_since(self, owner: str, day: str) -> None:
        reports = self.paper_reports()
        reports.setdefault(owner, {})["since"] = day
        self._write_paper(PAPER_REPORTS, reports)

    def nothing_to_report(self, owner: str) -> str | None:
        """The last day up to which an owner had nothing to make a report of (no entry signal): not asked again for it."""
        return (self.paper_reports().get(owner) or {}).get("nothing_to")

    def mark_nothing_to_report(self, owner: str, day: str) -> None:
        reports = self.paper_reports()
        reports.setdefault(owner, {})["nothing_to"] = day
        self._write_paper(PAPER_REPORTS, reports)

    def _report_runs(self, owner: str) -> list[dict]:
        """An owner's reports whose run is still there, newest first: each run's request and state."""
        found = []
        for run_id in (self.paper_reports().get(owner) or {}).get("runs") or []:
            try:
                request = read_json(self._file(run_id, "request.json")) or {}
                status = self._status(run_id)
            except UnknownRun:
                continue
            if status.get("state") == RUNNING and run_id not in self._running:
                status = {**status, "state": INTERRUPTED}
            found.append({"run_id": run_id, "request": request, "status": status})
        return found

    def newest_paper_report(self, owner: str) -> dict | None:
        """The newest report asked for an owner, whatever its state: its run, period and state."""
        runs = self._report_runs(owner)
        if not runs:
            return None
        newest = runs[0]
        return {"run_id": newest["run_id"], "from": newest["request"].get("from"), "to": newest["request"].get("to"),
                "state": newest["status"].get("state")}

    def _reports_of(self, owner: str) -> dict:
        """
        An owner's report to show, the newest finished one (``report``), and a newer one that waits, runs or did not
        finish (``next_report``), with its place in line or what stopped it.
        """
        report, next_report = None, None
        for found in self._report_runs(owner):
            request, status = found["request"], found["status"]
            if status.get("state") == DONE:
                source = request.get("source") or {}
                report = {
                    "run_id": found["run_id"], "from": request.get("from"), "to": request.get("to"),
                    "made_at": status.get("finished_at"), "signals": source.get("signals"),
                    "versions": source.get("versions") or [],
                    "headline": self._headline(self._summary_of(found["run_id"])),
                }
                break
            if next_report is None:
                waiting = self._in_turn()
                next_report = {
                    "run_id": found["run_id"], "from": request.get("from"), "to": request.get("to"),
                    "state": status.get("state"), "error": status.get("error"),
                    "position": waiting.index(found["run_id"]) + 1 if found["run_id"] in waiting else None,
                }
        return {"report": report, "next_report": next_report}

    def _summary_of(self, run_id: str) -> dict | None:
        """A finished run's summary; None when it is gone meanwhile (read from the routes' threads)."""
        try:
            return read_json(self._file(run_id, "summary.json"))
        except UnknownRun:
            return None

    def settle_paper_reports(self) -> int:
        """
        Each owner's reports down to the newest finished one and the newest of all (a report waiting, running, or
        one that did not finish, said beside it until the next is asked): the others removed, and the ids whose
        run is gone forgotten. How many runs were removed.
        """
        reports, removed, changed = self.paper_reports(), 0, False
        for entry in reports.values():
            listed = entry.get("runs") or []
            states = {}
            for run_id in listed:
                try:
                    states[run_id] = self._status(run_id).get("state")
                except UnknownRun:
                    continue
            present = [run_id for run_id in listed if run_id in states]
            finished = next((run_id for run_id in present if states[run_id] == DONE), None)
            kept = []
            for place, run_id in enumerate(present):
                if place == 0 or run_id == finished or run_id in self._waiting or run_id in self._running:
                    kept.append(run_id)
                    continue
                try:
                    self.delete(run_id)
                    removed += 1
                except (UnknownRun, NotPossible):
                    kept.append(run_id)
            if kept != listed:
                entry["runs"] = kept
                changed = True
        if changed:
            self._write_paper(PAPER_REPORTS, reports)
        return removed

    def references(self) -> dict:
        found = read_json(self._paper_path("references.json"))
        return found if isinstance(found, dict) else {}

    def set_reference(self, run_id: str) -> dict:
        """
        A finished run made from a rule becomes the one its paper days are set against; a replay of the
        signals WATA received, the one the live paper days and WATA's real days are set against.
        """
        request = read_json(self._file(run_id, "request.json")) or {}
        source = request.get("source") or {}
        rule = (source.get("from_rule") or {}).get("id")
        if request.get("kind") != "run" or self._status(run_id).get("state") != DONE:
            raise NotPossible("Only a finished run can be a reference")
        if source.get("kind") == JOURNAL and not request.get("paper"):
            rule = LIVE_PAPER
        if not rule:
            raise NotPossible(
                "This run was not made from a signal rule nor of the signals WATA received: start one from the rule to "
                "make it its reference"
            )
        references = self.references()
        references[rule] = run_id
        self._write_paper("references.json", references)
        return self.paper()

    def _reference_range(self, run_id: str) -> dict | None:
        try:
            days = read_json(self._file(run_id, "days.json")) or []
            name = (read_json(self._file(run_id, "request.json")) or {}).get("name")
        except UnknownRun:
            return None
        results = sorted(day["day_percent"] for day in days if day.get("status") == "tested" and day.get("day_percent") is not None)
        if len(results) < 5:
            return {"run_id": run_id, "name": name, "days": len(results), "low": None, "median": None, "high": None}
        pick = lambda share: results[min(len(results) - 1, max(0, round(share * (len(results) - 1))))]  # noqa: E731
        return {"run_id": run_id, "name": name, "days": len(results), "low": pick(0.05), "median": pick(0.5), "high": pick(0.95)}

    def paper(self, *, with_reports: bool = True) -> dict:
        """
        Each rule's paper days, newest first, beside the daily range of its reference run, and its report since it
        began; and apart (``live``), the same of the signals WATA received, beside the range of the live reference.
        ``with_reports`` False: the days and references alone (the drift, read at each look at health).
        """
        references = self.references()
        index = self.paper_index()
        rules = {}
        for rule_id in sorted(set(index) | (set(self.paper_reports()) if with_reports else set())):
            days = index.get(rule_id) or {}
            reference = self._reference_range(references[rule_id]) if rule_id in references else None
            listed = []
            for day, run_id in sorted(days.items(), reverse=True):
                if run_id == NO_ENTRY:
                    listed.append({"day": day, "run_id": None, "state": "no_entry", "day_percent": 0.0, "trades": 0,
                                   "outside": None, "stale": None, "made_with": None})
                    continue
                try:
                    status = self._status(run_id)
                    done = status.get("state") == DONE
                    record = next(iter(read_json(self._file(run_id, "days.json")) or []), None) if done else None
                    made_with = self._made_with(run_id) if done else {}
                except UnknownRun:
                    continue
                result = record.get("day_percent") if record and record.get("status") == "tested" else None
                outside = None
                if result is not None and reference and reference["low"] is not None:
                    outside = result < reference["low"] or result > reference["high"]
                listed.append({
                    "day": day, "run_id": run_id, "state": status.get("state"), "day_percent": result,
                    "trades": record.get("trades") if record else None, "outside": outside,
                    # Made with other backtest code than the server's: paper trading makes it again (stale_paper_days).
                    "stale": self._stale(made_with), "made_with": made_with.get("version"),
                })
            rules[rule_id] = {"days": listed, "reference": reference,
                              **(self._reports_of(rule_id) if with_reports else {})}
        live = rules.pop(LIVE_PAPER, None)
        if live is None and LIVE_PAPER in references:
            live = {"days": [], "reference": self._reference_range(references[LIVE_PAPER]),
                    **({"report": None, "next_report": None} if with_reports else {})}
        return {"rules": rules, "live": live}

    def _made_with(self, run_id: str) -> dict:
        """What a finished run was made with: the digest of its code and WATA's version; empty when its summary says none."""
        return (read_json(self._file(run_id, "summary.json")) or {}).get("made_with") or {}

    def _stale(self, made_with: dict) -> bool | None:
        """Whether a run was made with other code than the server's; None when it names none."""
        code = made_with.get("code")
        return None if code is None else code != self._code

    def stale_paper_days(self) -> list[tuple[str, str, str]]:
        """
        The days on paper whose run is done and was made with other backtest code than the server's, newest first:
        each owner, day and run. Paper trading makes them again, so that its days, its reports and the go-live check
        rest on the same code (a day of 23 Oct 2026 made by v0.33.0 said +0.51 %, the report -11.30 %).
        """
        found = []
        for owner, days in self.paper_index().items():
            for day, run_id in days.items():
                if run_id == NO_ENTRY:
                    continue
                try:
                    if self._status(run_id).get("state") != DONE or not self._stale(self._made_with(run_id)):
                        continue
                except UnknownRun:
                    continue
                found.append((owner, day, run_id))
        return sorted(found, key=lambda stale: (stale[1], stale[0]), reverse=True)

    def prune_paper(self, keep_days: int, today: date) -> int:
        """
        The paper runs of days older than ``keep_days`` removed, with their place in the index; and the reports
        that end on such a day (a rule's last, once it left shadow), with theirs.
        """
        index, removed = self.paper_index(), 0
        for days in index.values():
            for day in [day for day in days if (today - date.fromisoformat(day)).days > keep_days]:
                run_id = days.pop(day)
                if run_id != NO_ENTRY:
                    try:
                        self.delete(run_id)
                    except (UnknownRun, NotPossible):
                        pass
                removed += 1
        if removed:
            self._write_paper("index.json", index)
        reports, dropped = self.paper_reports(), 0
        for entry in reports.values():
            for run_id in list(entry.get("runs") or []):
                try:
                    ends = (read_json(self._file(run_id, "request.json")) or {}).get("to")
                except UnknownRun:
                    continue
                if not ends or (today - date.fromisoformat(ends)).days <= keep_days:
                    continue
                entry["runs"].remove(run_id)
                try:
                    self.delete(run_id)
                except (UnknownRun, NotPossible):
                    pass
                dropped += 1
        if dropped:
            self._write_paper(PAPER_REPORTS, reports)
        return removed + dropped

    def _done_file(self, run_id: str, name: str):
        content = read_json(self._file(run_id, name))
        if content is None:
            raise NotPossible("This backtest has no results yet")
        return content

    def days(self, run_id: str) -> list[dict]:
        return self._done_file(run_id, "days.json")

    def settings_of(self, run_id: str) -> dict:
        """The settings a run was made with (those of its settings.json)."""
        return (read_json(self._file(run_id, "settings.json")) or {}).get("settings") or {}

    def kept(self, run_id: str, name: str):
        """A file an admin feature keeps beside a run's results (KEPT_FILES), None while it has none."""
        if name not in KEPT_FILES:
            raise InvalidRequest(f"A run keeps no {name}")
        return read_json(self._file(run_id, name))

    def keep(self, run_id: str, name: str, content) -> None:
        if name not in KEPT_FILES:
            raise InvalidRequest(f"A run keeps no {name}")
        write_json(self._file(run_id, name), content)

    def signals(self, run_id: str, day: date | None = None, fate: str | None = None) -> list[dict]:
        rows = self._done_file(run_id, "signals.json")
        if day is not None:
            rows = [row for row in rows if row.get("day") == day.isoformat()]
        if fate is not None:
            rows = [row for row in rows if row.get("fate") == fate]
        return rows

    def trades(self, run_id: str, day: date | None = None) -> list[dict]:
        rows = self._done_file(run_id, "trades.json")
        if day is not None:
            rows = [row for row in rows if row.get("day") == day.isoformat()]
        return rows

    async def day_indicator(self, run_id: str, day: date, *, db=None, rows=None) -> dict:
        """
        A day of a run with the script that made its signals, as it ran, and the bars it ran on
        (src/admin/backtest_indicator.py). The asset's rows are read where the runs read them: through
        ``db``, the admin's read-only database, or from the recorded days' files (``files:<folder>``).
        """
        files = self._data.removeprefix("files:") if self._data.startswith("files:") else None
        return await backtest_indicator.day_indicator(
            self._directory(run_id), day, scripts=self._scripts,
            rows=rows or AssetRows(self._prices_root, None if files else db, files_root=files),
        )

    def edge(self, run_id: str) -> dict | None:
        """How the asset moved after the run's entry signals; None for a run made before it was measured."""
        found = read_json(self._file(run_id, "edge.json"))
        if found is not None:
            # The signals one by one are for the lab: the pages read the figures.
            found = {key: value for key, value in found.items() if key != "signals"}
        return found

    def reconciliation(self, run_id: str) -> dict | None:
        """A replay of the journal beside WATA's real positions; None for any other run, or one made before."""
        return read_json(self._file(run_id, "reconciliation.json"))

    def model_check_result(self, run_id: str) -> dict | None:
        """A model check's trades paired with those of the recorded run it checks; None for any other run."""
        return read_json(self._file(run_id, MODEL_CHECK_FILE))

    def baseline(self, run_id: str) -> dict | None:
        return read_json(self._file(run_id, "baseline.json"))

    def sweep(self, run_id: str) -> dict:
        return self._done_file(run_id, "sweep.json")

    def breakdowns(self, run_id: str) -> dict:
        """The trades of a run by what could explain them, with the figures of the Exits page."""
        days = {row["day"]: row for row in self.days(run_id)}
        trades = [trade for trade in self.trades(run_id) if (days.get(trade["day"]) or {}).get("status") == "tested"]
        settings = (read_json(self._file(run_id, "settings.json")) or {}).get("settings") or {}
        local = pytz.timezone(settings.get("timezone", "Europe/Paris"))

        def opened(trade):
            moment = datetime.strptime(trade["entry"]["filled_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            return moment.astimezone(local)

        groups = {
            "hour": stats_by(trades, lambda trade: opened(trade).hour, name="hour"),
            "weekday": stats_by(trades, lambda trade: WEEKDAYS[opened(trade).weekday()], list(WEEKDAYS[:5]), name="weekday"),
            "weekday_hour": stats_by_weekday_hour(trades, lambda trade: (opened(trade).isoweekday(), opened(trade).hour)),
            "direction": stats_by(trades, lambda trade: trade["direction"], ["long", "short"], name="direction"),
            "exit": stats_by(trades, lambda trade: exit_group(trade["exit"]["reason"]), name="exit"),
            "confidence": stats_by(
                trades, lambda trade: _confidence_group(trade.get("confidence")),
                [label for _, label in _CONFIDENCES] + [NO_CONFIDENCE], name="confidence",
            ),
            "holding": stats_by(trades, lambda trade: holding_bucket(trade.get("holding_seconds")), list(HOLDING_LABELS), name="holding"),
            "turbo_price": stats_by(
                trades, lambda trade: _price_group(trade["entry"].get("price")), [label for _, label in _PRICES], name="price",
            ),
            "issuer": stats_by(trades, lambda trade: trade["turbo"].get("issuer") or "Unknown", name="issuer"),
        }

        # The kind of day a trade was made on, and its place against the US open: runs made before have neither.
        def label(kind):
            return lambda trade: ((days[trade["day"]].get("regime") or {}).get("labels") or {}).get(kind)

        for kind, order in (("volatility", VOLATILITY_LABELS), ("gap", GAP_LABELS), ("day_type", DAY_TYPES),
                            ("us_open", OPEN_LABELS), ("news", MACRO_LABELS)):
            groups[kind] = stats_by(trades, label(kind), list(order), name="group")
        groups["session_part"] = stats_by(trades, lambda trade: trade.get("session_part"), list(SESSION_PARTS), name="group")
        return {"trades": len(trades), "groups": groups, "given_back": given_back(trades)}

