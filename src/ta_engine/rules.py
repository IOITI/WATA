"""
The signal rules as the engine reads them: rules.json and the pinned versions of
the scripts, from the admin's Technical Analysis folder, mounted read-only.

Every rule is checked again with the admin's own checks (src/admin/ta_rules.py),
since a file can be edited by hand, and its pinned sources are read and checked
against their sha256: what runs is what was saved, or nothing.
"""

import logging
import os
from dataclasses import dataclass, field

from src.admin.ta_rules import (
    LIVE,
    MODES,
    InvalidRule,
    RuleStore,
    checked_alerts,
    checked_asset,
    checked_bars,
    checked_history_days,
    checked_inputs,
)
from src.admin.ta_scripts import ID_PATTERN, ScriptStore
from src.capital_feed.settings import LABEL_PATTERN
from src.ta_engine.tickers import Ticker

logger = logging.getLogger(__name__)

# The fields that change what a rule runs: another value means loading it again.
RUN_FIELDS = ("asset", "side", "bars", "script", "libraries", "inputs", "history_days")


@dataclass(frozen=True)
class EngineRule:
    rule: dict
    source: str
    libraries: tuple[dict, ...] = field(default_factory=tuple)

    @property
    def id(self) -> str:
        return self.rule["id"]

    @property
    def name(self) -> str:
        return self.rule["name"]

    @property
    def mode(self) -> str:
        return self.rule["mode"]

    @property
    def epic(self) -> str:
        return self.rule["asset"]["key"]

    @property
    def run_key(self) -> tuple:
        """What the runner was given: a rule whose key changed is loaded again."""
        return tuple(repr(self.rule.get(name)) for name in RUN_FIELDS)

    @property
    def history_days(self) -> int | None:
        """Its own days of bars before each engine day; None: the engine's history_days."""
        return self.rule.get("history_days")

    def ticker(self) -> Ticker:
        bars = self.rule["bars"]
        return Ticker(
            self.rule["asset"]["provider"], self.epic, self.rule["side"],
            bars["ticks"] if bars["type"] == "range" else None,
        )

    def timeframe(self) -> str:
        """The chart's timeframe for PineTS: range bars keep a minute, as on the Charts page."""
        bars = self.rule["bars"]
        return bars["timeframe"] if bars["type"] == "time" else "1"

    def inputs(self) -> dict:
        return {entry["id"]: entry["value"] for entry in self.rule["inputs"]}


@dataclass(frozen=True)
class RulesSnapshot:
    rules: tuple[EngineRule, ...]
    # Rules that cannot run, by id: why.
    errors: dict
    # The indices on which more than one rule is live: none of them sends.
    contested: frozenset


def _checked(rule: dict) -> dict:
    """The rule as the admin would have saved it; InvalidRule otherwise."""
    if not isinstance(rule, dict) or not isinstance(rule.get("id"), str) or not ID_PATTERN.match(rule["id"]):
        raise InvalidRule("A rule without a valid id")
    if rule.get("mode") not in MODES:
        raise InvalidRule(f"Unknown mode {rule.get('mode')!r}")
    if not isinstance(rule.get("indice"), str) or not LABEL_PATTERN.match(rule["indice"]):
        raise InvalidRule("No valid indice")
    checked_asset(rule.get("asset"))
    checked_bars(rule.get("bars"))
    checked_inputs(rule.get("inputs"))
    checked_alerts(rule.get("alerts"))
    checked_history_days(rule.get("history_days"))
    if rule.get("side") not in ("bid", "ask") or rule.get("alert_calls") not in ("ignore", "message"):
        raise InvalidRule("No valid side or alert() calls")
    for pin in (rule.get("script"), *(rule.get("libraries") or [])):
        if not isinstance(pin, dict) or not all(key in pin for key in ("id", "version", "sha256")):
            raise InvalidRule("A pinned script without its id, version and sha256")
    return rule


def read_rules(root: str) -> RulesSnapshot:
    """The rules of ``root`` with their sources. A rule that cannot run is in ``errors``, not in ``rules``."""
    scripts = ScriptStore(root)
    store = RuleStore(scripts)
    try:
        listed = store.load()["rules"]
    except InvalidRule as e:
        return RulesSnapshot(rules=(), errors={"*": str(e)}, contested=frozenset())
    rules, errors = [], {}
    for rule in listed:
        rule_id = rule.get("id") if isinstance(rule, dict) else None
        try:
            checked = _checked(rule)
            store.verify(checked)
            libraries = tuple(
                {**pin, "name": pin["name"], "source": scripts.source(pin["id"], pin["version"])}
                for pin in checked["libraries"]
            )
            source = scripts.source(checked["script"]["id"], checked["script"]["version"])
        except (InvalidRule, KeyError, TypeError, ValueError, OSError) as e:
            errors[rule_id if isinstance(rule_id, str) else "*"] = str(e) or type(e).__name__
            continue
        rules.append(EngineRule(rule=checked, source=source, libraries=libraries))
    live_by_indice: dict[str, int] = {}
    for engine_rule in rules:
        if engine_rule.mode == LIVE:
            live_by_indice[engine_rule.rule["indice"]] = live_by_indice.get(engine_rule.rule["indice"], 0) + 1
    contested = frozenset(indice for indice, count in live_by_indice.items() if count > 1)
    return RulesSnapshot(rules=tuple(rules), errors=errors, contested=contested)


class RulesFile:
    """rules.json, read again only when it changed (its size or its time of writing)."""

    def __init__(self, root: str):
        self._root = root
        self._path = os.path.join(root, "rules.json")
        self._seen: tuple | None = None
        self._snapshot = RulesSnapshot(rules=(), errors={}, contested=frozenset())

    def _stamp(self) -> tuple | None:
        try:
            stat = os.stat(self._path)
        except FileNotFoundError:
            return None
        return (stat.st_size, stat.st_mtime_ns, stat.st_ino)

    def snapshot(self) -> RulesSnapshot:
        stamp = self._stamp()
        if stamp != self._seen or self._seen is None:
            self._snapshot = read_rules(self._root)
            self._seen = stamp
        return self._snapshot
