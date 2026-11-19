"""
Backtest configurations: the runs whose result is decided by the same things, all but the days they cover.

The Backtests page lists them, each with its runs, and a configuration may be made again after each trading day
(src/admin/backtest_nightly.py), its period stretched to the day just over (``nightly_window``), the nightly run
before it replaced.

A configuration is known by a digest (``configuration_id``) of what decides a run's result besides its period: its
strategy (``strategy_parts``: the source's defining parts, the asset its days are drawn on, the turbos, a sweep's axes)
and its settings as the run resolved them (settings.json). A setting still at its ``BacktestSettings()`` default is
left out, so that a setting added later, at a default that changes nothing, re-keys no configuration. Numbers are read
as floats: 2 and 2.0 are one value (config.json and the form write either). Left out too: the period, the name and
tags, the favourite, the random entries, who asked and why (made_by, experiment, the lock box), the run it was made
again from, the rule it was started from, the counts of its signals, the turbo model and the measured fills (data, as
the prices are). Runs of one strategy whose settings differ share a ``strategy_id``: "same strategy, other settings".
A run made from another (a stress, a fill-noise one, a model check) belongs to the configuration of its base.

What the operator gives a configuration (its name, its nightly re-run) is kept beside the runs (``configurations/
index.json``), and what each nightly run said once it ended (``configurations/<id>.json``, the newest first): the
runs themselves are replaced each evening, their figures stay.

Kept out of src/backtest: nothing here changes a run, nor the code digest of any (``_CODE``).
"""

import copy
import hashlib
import json
import os
import re
import threading
from datetime import date, timedelta
from typing import Callable

from src.admin.processes import read_json, write_json
from src.backtest.settings import BacktestSettings

# Part of every digest: changed, every configuration is keyed again (each entry keeps the run it was first keyed from).
DIGEST_VERSION = 1
CONFIGURATION_ID = re.compile(r"[0-9a-f]{12}")
FOLDER = "configurations"
INDEX_FILE = "index.json"
# What a configuration's nightly runs said, kept at most.
HISTORY_LINES = 400
# A nightly run covers a year of days at most, then its first day moves on.
NIGHTLY_MOST_DAYS = 365
# The longest name a run takes (src/admin/backtests.py MAX_NAME_LENGTH): "Run again with changes" cuts there.
NAME_LENGTH = 120
# A run made from another names it by one of these.
BASE_KEYS = ("stress_of", "noise_of", "model_check_of")

# What "Run again with changes" adds to a run's name (draftOfRun), once per run made again.
_AGAIN_AT_END = re.compile(r"(?:\s*\(again\))+\s*$")
# What is left of " (again)" in a name cut at its longest.
_CUT_AGAIN = re.compile(r"\s*\((?:a(?:g(?:a(?:i(?:n)?)?)?)?)?$")


class UnknownConfiguration(LookupError):
    """No configuration has that id."""


def _canonical(value):
    """A value as a digest reads it: every number a float, bools kept, keys and items as written."""
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        return {str(key): _canonical(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return str(value)


def digest(parts) -> str:
    """Twelve hexadecimal characters of the sha256 of ``parts`` as canonical JSON (sorted keys)."""
    text = json.dumps(_canonical(parts), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


_DEFAULTS = _canonical(BacktestSettings().to_dict())


def _of(found, key) -> dict:
    value = found.get(key) if isinstance(found, dict) else None
    return value if isinstance(value, dict) else {}


def strategy_parts(request: dict) -> dict:
    """What a run's strategy is, the same for every run of it whatever its days and its settings."""
    source = _of(request, "source")
    kind = source.get("kind")
    parts = {
        "kind": request.get("kind"), "indice": request.get("indice"), "turbos": request.get("turbos") or "recorded",
        "chart": request.get("chart"), "source": kind,
    }
    if kind == "pine":
        rule = _of(source, "rule")
        script = _of(rule, "script")
        libraries = [[library.get("id") or library.get("name"), library.get("version"), library.get("sha256")]
                     for library in rule.get("libraries") or [] if isinstance(library, dict)]
        parts["pine"] = {
            "script": [script.get("id"), script.get("version"), script.get("sha256")],
            "libraries": sorted(libraries, key=json.dumps),
            "asset": rule.get("asset"), "side": rule.get("side"), "bars": rule.get("bars"),
            "inputs": {str(entry.get("id")): entry.get("value") for entry in rule.get("inputs") or [] if isinstance(entry, dict)},
            "alerts": {str(entry.get("title")): entry.get("action")
                       for entry in rule.get("alerts") or [] if isinstance(entry, dict)},
            "alert_calls": rule.get("alert_calls"), "confidence_plot": rule.get("confidence_plot"),
        }
    elif kind == "ta_signals":
        parts["rule"] = _of(source, "rule").get("id")
    sweep = request.get("sweep")
    if isinstance(sweep, dict):
        parts["sweep"] = {"axes": sweep.get("axes"), "held_out": sweep.get("held_out"), "walk_forward": sweep.get("walk_forward")}
    return parts


def resolved_settings(settings) -> dict | None:
    """A run's settings as it ran them (its settings.json's), canonical; None when they cannot be read."""
    if not isinstance(settings, dict):
        return None
    try:
        return _canonical(BacktestSettings.from_dict(settings).to_dict())
    except (TypeError, ValueError, KeyError):
        return None


def settings_parts(settings) -> dict | None:
    """A run's settings as its configuration reads them: those not at their default."""
    resolved = resolved_settings(settings)
    if resolved is None:
        return None
    return {name: value for name, value in resolved.items() if value != _DEFAULTS.get(name)}


def strategy_id(request: dict) -> str:
    """What runs of one strategy share, whatever their settings: "same strategy, other settings"."""
    return digest({"version": DIGEST_VERSION, "strategy": strategy_parts(request)})


def configuration_id(request: dict, settings_doc) -> str | None:
    """A run's configuration: its strategy and its settings; None when its settings cannot be read."""
    settings = settings_parts(settings_doc.get("settings") if isinstance(settings_doc, dict) else None)
    if settings is None:
        return None
    return digest({"version": DIGEST_VERSION, "strategy": strategy_parts(request), "settings": settings})


def base_of(request: dict) -> str | None:
    """The run a stress, a fill-noise one or a model check was made from; None for any other run."""
    for key in BASE_KEYS:
        if isinstance(request.get(key), str) and request[key]:
            return request[key]
    return None


def differences(settings, other) -> list[dict]:
    """The settings two runs ran with differently, by name: each one's value."""
    mine, theirs = resolved_settings(settings) or {}, resolved_settings(other) or {}
    raw_mine = settings if isinstance(settings, dict) else {}
    raw_theirs = other if isinstance(other, dict) else {}
    return [
        {"name": name, "here": raw_mine.get(name, mine.get(name)), "there": raw_theirs.get(name, theirs.get(name))}
        for name in sorted(set(mine) | set(theirs)) if mine.get(name) != theirs.get(name)
    ]


def base_name(name: str | None) -> str:
    """A run's name without what "Run again with changes" added to it: " (again)", as often as it was, or cut."""
    text = " ".join((name or "").split())
    if len(text) >= NAME_LENGTH - 1:
        text = _CUT_AGAIN.sub("", text)
    return _AGAIN_AT_END.sub("", text).strip() or " ".join((name or "").split())


def nightly_window(anchor: date, day: date, most: int) -> tuple[date, date]:
    """A nightly run's days: from its anchor to the day just over, the first moving on once there are ``most``."""
    return max(anchor, day - timedelta(days=max(1, most) - 1)), day


def checked_id(configuration) -> str:
    """A configuration's id, matched before any path is built from it."""
    if not isinstance(configuration, str) or not CONFIGURATION_ID.fullmatch(configuration):
        raise UnknownConfiguration(configuration)
    return configuration


class ConfigurationStore:
    """
    What the operator gave each configuration (``index.json``: its name, its nightly re-run and its last ask) and what
    its nightly runs said (``<id>.json``): written whole, one change at a time, from the routes' threads and the
    nightly task alike.
    """

    def __init__(self, root: str):
        self._folder = os.path.join(root, FOLDER)
        self._lock = threading.Lock()
        # The index as last read, with the time its file was written: read for every run listed.
        self._cached: tuple[int, dict] | None = None

    def _index_path(self) -> str:
        return os.path.join(self._folder, INDEX_FILE)

    def _history_path(self, configuration: str) -> str:
        return os.path.join(self._folder, f"{checked_id(configuration)}.json")

    def _read_index(self) -> dict:
        path = self._index_path()
        try:
            written = os.stat(path).st_mtime_ns
        except OSError:
            return {}
        cached = self._cached
        if cached is not None and cached[0] == written:
            return cached[1]
        found = read_json(path)
        found = {key: value for key, value in found.items() if CONFIGURATION_ID.fullmatch(str(key)) and isinstance(value, dict)} \
            if isinstance(found, dict) else {}
        self._cached = (written, found)
        return found

    def _write_index(self, index: dict) -> None:
        os.makedirs(self._folder, exist_ok=True)
        write_json(self._index_path(), index)
        self._cached = None

    def index(self) -> dict:
        """Every configuration the operator gave something to, by id (a copy)."""
        return copy.deepcopy(self._read_index())

    def entry(self, configuration: str) -> dict:
        """What the operator gave one configuration (a copy; empty when nothing)."""
        return copy.deepcopy(self._read_index().get(checked_id(configuration)) or {})

    def nightly_on(self, configuration: str | None) -> bool:
        """Whether a configuration is made again after each trading day."""
        if configuration is None:
            return False
        return bool(((self._read_index().get(configuration) or {}).get("nightly") or {}).get("on"))

    def update(self, configuration: str, change: Callable[[dict], None]) -> dict:
        """One configuration's entry changed by ``change`` (made when it had none), and written: the entry after."""
        checked_id(configuration)
        with self._lock:
            index = copy.deepcopy(self._read_index())
            entry = index.setdefault(configuration, {})
            change(entry)
            self._write_index(index)
        return copy.deepcopy(entry)

    def forget(self, configurations: set[str]) -> int:
        """The entries of configurations that have no run any more, and what their nightly runs said: gone."""
        with self._lock:
            index = copy.deepcopy(self._read_index())
            gone = [configuration for configuration in configurations if configuration in index]
            for configuration in gone:
                del index[configuration]
                try:
                    os.remove(self._history_path(configuration))
                except FileNotFoundError:
                    pass
            if gone:
                self._write_index(index)
        return len(gone)

    def history(self, configuration: str) -> list[dict]:
        """What its nightly runs said, the newest first."""
        found = read_json(self._history_path(configuration))
        return [line for line in found if isinstance(line, dict)] if isinstance(found, list) else []

    def add_history(self, configuration: str, line: dict) -> None:
        """A nightly run's line, first; one of the same run before it replaced; HISTORY_LINES kept."""
        path = self._history_path(configuration)
        with self._lock:
            found = read_json(path)
            lines = [old for old in found if isinstance(old, dict) and old.get("run") != line.get("run")] \
                if isinstance(found, list) else []
            os.makedirs(self._folder, exist_ok=True)
            write_json(path, [line, *lines][:HISTORY_LINES])

    def replaced(self, run_id: str) -> dict | None:
        """The configuration whose nightly run ``run_id`` was, and its line, once it was replaced; None otherwise."""
        try:
            names = os.listdir(self._folder)
        except OSError:
            return None
        for name in names:
            configuration = name.removesuffix(".json")
            if name == INDEX_FILE or not CONFIGURATION_ID.fullmatch(configuration):
                continue
            line = next((line for line in self.history(configuration) if line.get("run") == run_id), None)
            if line is not None:
                return {"configuration": configuration, "name": line.get("name"), "to": line.get("to")}
        return None
