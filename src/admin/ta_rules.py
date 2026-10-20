"""
The signal rules of the Technical Analysis pages: a script run on the bars of an
asset, with its inputs, and what its alerts become. They are kept in one file
beside the scripts, <ta root>/rules.json, which the admin writes and the TA
engine (src/ta_engine) only reads:

    {"rules": [<rule>, ...], "saved_at": "..."}

A rule runs one version of its script, and one of each library the script
imports, recorded with their sha256 when the rule is saved: a script saved again
changes nothing that runs until the rule is updated to it.

The file is saved whole. Every change names the revision it was made on (the
sha256 of the file it read), and a change made meanwhile is not overwritten, as
in the configuration editor.

A rule's mode says what becomes of its signals:

    off     the engine does not run it;
    shadow  they are journaled, never sent (a new rule's mode);
    live    they are journaled and sent to the trader.

Only set_mode changes it. The routes make going live an operator action
(src/admin/routers/ta.py); the store keeps one live rule per indice, since the
web server keeps the last signal of each indice, whoever sent it.
"""

import hashlib
import json
import math
import os
import re
import secrets
from datetime import datetime, timezone

from src.admin.prices import MAX_INTERVAL
from src.admin.ta_scripts import (
    ID_PATTERN,
    LIBRARY,
    InvalidScript,
    ScriptConflict,
    ScriptNotFound,
    ScriptStore,
    pine_facts,
)
from src.capital_feed.settings import EPIC_PATTERN, LABEL_PATTERN
from src.price_recorder.bars import BAR_SECONDS

OFF = "off"
SHADOW = "shadow"
LIVE = "live"
MODES = (OFF, SHADOW, LIVE)

# What an alert of the script becomes. "message" reads the action in the alert's
# message, WATA's webhook JSON as TradingView's alerts send it.
IGNORE = "ignore"
MESSAGE = "message"
SIGNAL_ACTIONS = ("long", "short", "close-position")
ALERT_ACTIONS = (IGNORE, *SIGNAL_ACTIONS, MESSAGE)
# The alert() calls of a script have no title: they are all ignored, or all read as messages.
ALERT_CALL_ACTIONS = (IGNORE, MESSAGE)

# The assets a rule may run on: those with live bars in WATA, Capital.com's.
PROVIDERS = ("capital",)
SIDES = ("bid", "ask")
# A rule runs on bars of whole minutes, as Pine names its timeframes, or range bars of a
# number of ticks. The engine runs a rule's script over all its bars at each new one.
TIMEFRAMES = ("1", "5", "15", "60")
TIME_BARS = "time"
RANGE_BARS = "range"
MAX_RANGE_TICKS = 100_000
# A backtest or a replay runs on bars of any length made of the ten-second bars WATA
# records, from ten seconds to a day, named as Pine names a timeframe: whole minutes
# as a number ("1", "1440"), any other length in seconds ("10S", "45S", "90S").
MIN_BAR_SECONDS = BAR_SECONDS
MAX_BAR_SECONDS = MAX_INTERVAL
_TIMEFRAME = re.compile(r"([1-9][0-9]{0,5})([Ss]?)")

MAX_RULES = 50
MAX_NAME_LENGTH = 80
MAX_TITLE_LENGTH = 120
MAX_INPUTS = 200
MAX_ALERTS = 100
MAX_INPUT_TEXT = 1000
INPUT_ID_PATTERN = re.compile(r"^in_[0-9]{1,4}$")
VARIABLE_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,79}$")


class RuleNotFound(ScriptNotFound):
    """No rule of that id."""


class InvalidRule(InvalidScript):
    """A rule that cannot be kept, or run."""


class RuleConflict(ScriptConflict):
    """The rules changed meanwhile, or the change would break one of their invariants."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _text(value, what: str, maximum: int, *, required: bool = True) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise InvalidRule(f"{what} is missing")
    value = " ".join(value.split())
    if not value:
        if required:
            raise InvalidRule(f"{what} is missing")
        return None
    if len(value) > maximum:
        raise InvalidRule(f"{what} is {maximum} characters at most")
    return value


def _title(value, what: str, *, required: bool = True) -> str | None:
    """A title the script writes, kept as it is: the engine matches it exactly."""
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_TITLE_LENGTH:
        raise InvalidRule(f"{what} is named by its title in the script, {MAX_TITLE_LENGTH} characters at most")
    return value


def _choice(value, what: str, choices: tuple[str, ...]) -> str:
    if value not in choices:
        raise InvalidRule(f"{what} must be one of {', '.join(choices)}")
    return value


def _whole(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def checked_asset(asset) -> dict:
    if not isinstance(asset, dict):
        raise InvalidRule("A rule needs an asset")
    provider = _choice(asset.get("provider"), "The asset's provider", PROVIDERS)
    key = asset.get("key")
    if not isinstance(key, str) or not EPIC_PATTERN.match(key):
        raise InvalidRule("The asset is named by its Capital.com epic, such as US100")
    return {"provider": provider, "key": key}


def timeframe_seconds(timeframe) -> int | None:
    """The length of a timeframe WATA makes ("45S" is 45 seconds, "5" 300), or None for any other."""
    match = _TIMEFRAME.fullmatch(timeframe) if isinstance(timeframe, str) else None
    if match is None:
        return None
    seconds = int(match[1]) * (1 if match[2] else 60)
    return seconds if MIN_BAR_SECONDS <= seconds <= MAX_BAR_SECONDS else None


def timeframe_name(seconds: int) -> str:
    """A length as Pine names it: whole minutes as a number, any other length in seconds."""
    return str(seconds // 60) if seconds % 60 == 0 else f"{seconds}S"


def checked_bars(bars, *, any_length: bool = False) -> dict:
    """
    A rule's bars: whole minutes (TIMEFRAMES) or range bars. With ``any_length``, a
    backtest's: any length from ten seconds to a day, named as Pine names it ("120S" is "2").
    """
    if not isinstance(bars, dict):
        raise InvalidRule("A rule needs its bars: minutes or range")
    kind = _choice(bars.get("type"), "The bars", (TIME_BARS, RANGE_BARS))
    if kind == TIME_BARS and any_length:
        seconds = timeframe_seconds(bars.get("timeframe"))
        if seconds is None:
            raise InvalidRule(
                f"Bars of time are {MIN_BAR_SECONDS} seconds to a day, named as Pine names them: "
                "10S, 45S, 90S, 1, 5, 60"
            )
        return {"type": TIME_BARS, "timeframe": timeframe_name(seconds)}
    if kind == TIME_BARS:
        return {"type": TIME_BARS, "timeframe": _choice(bars.get("timeframe"), "The timeframe", TIMEFRAMES)}
    ticks = bars.get("ticks")
    if not _whole(ticks) or not 1 <= ticks <= MAX_RANGE_TICKS:
        raise InvalidRule(f"Range bars are 1 to {MAX_RANGE_TICKS} ticks")
    return {"type": RANGE_BARS, "ticks": ticks}


def checked_inputs(inputs) -> list[dict]:
    """
    The inputs the rule sets, keyed as PineTS reads them: the input's id in the
    script (in_0, in_1, ...), its variable and title kept to be read by people.
    """
    if inputs is None:
        return []
    if not isinstance(inputs, list) or len(inputs) > MAX_INPUTS:
        raise InvalidRule(f"A rule sets {MAX_INPUTS} inputs at most")
    checked, ids = [], set()
    for entry in inputs:
        if not isinstance(entry, dict):
            raise InvalidRule("An input is an object with its id and value")
        input_id = entry.get("id")
        if not isinstance(input_id, str) or not INPUT_ID_PATTERN.match(input_id):
            raise InvalidRule("An input is named by its id in the script, such as in_0")
        if input_id in ids:
            raise InvalidRule(f"The input {input_id} is set twice")
        ids.add(input_id)
        variable = entry.get("var")
        if variable is not None and (not isinstance(variable, str) or not VARIABLE_PATTERN.match(variable)):
            raise InvalidRule(f"The variable of the input {input_id} is not a Pine name")
        value = entry.get("value")
        if isinstance(value, float) and not math.isfinite(value):
            raise InvalidRule(f"The input {input_id} is not a number")
        if isinstance(value, str) and len(value) > MAX_INPUT_TEXT:
            raise InvalidRule(f"The input {input_id} is {MAX_INPUT_TEXT} characters at most")
        if not isinstance(value, (bool, int, float, str)):
            raise InvalidRule(f"The input {input_id} is a number, a text or true/false")
        checked.append({
            "id": input_id,
            "var": variable,
            "title": _text(entry.get("title"), f"The title of the input {input_id}", MAX_TITLE_LENGTH, required=False),
            "value": value,
        })
    return sorted(checked, key=lambda entry: int(entry["id"][3:]))


def checked_alerts(alerts) -> list[dict]:
    """What each alertcondition() of the script becomes, by its title."""
    if alerts is None:
        return []
    if not isinstance(alerts, list) or len(alerts) > MAX_ALERTS:
        raise InvalidRule(f"A rule maps {MAX_ALERTS} alerts at most")
    checked, titles = [], set()
    for entry in alerts:
        if not isinstance(entry, dict):
            raise InvalidRule("An alert is an object with its title and action")
        title = _title(entry.get("title"), "An alert")
        if title in titles:
            raise InvalidRule(f"The alert {title!r} is mapped twice")
        titles.add(title)
        checked.append({"title": title, "action": _choice(entry.get("action"), f"The action of {title!r}", ALERT_ACTIONS)})
    return checked


def acts(rule: dict) -> bool:
    """Whether any alert of the rule can become a signal."""
    return rule["alert_calls"] == MESSAGE or any(entry["action"] != IGNORE for entry in rule["alerts"])


def rule_users(rules: list[dict], script_id: str) -> list[str]:
    """The rules that run the script, or pinned it as a library."""
    return [
        rule["name"] for rule in rules
        if rule["script"]["id"] == script_id or any(entry["id"] == script_id for entry in rule["libraries"])
    ]


def bars_label(bars: dict) -> str:
    """The bars of a rule in one word: 1, 5, 15, 60 (minutes), 45S (seconds) or R60 (range of 60 ticks)."""
    return bars["timeframe"] if bars["type"] == TIME_BARS else f"R{bars['ticks']}"


class RuleStore:
    """The signal rules beside the scripts of ``scripts``. Safe to share between requests."""

    def __init__(self, scripts: ScriptStore):
        self._scripts = scripts
        self._path = os.path.join(scripts.root, "rules.json")

    @property
    def path(self) -> str:
        return self._path

    # ── reading ──

    def _read(self) -> tuple[list[dict], str]:
        try:
            with open(self._path, "rb") as rules_file:
                content = rules_file.read()
        except FileNotFoundError:
            content = b""
        revision = hashlib.sha256(content).hexdigest()
        if not content:
            return [], revision
        try:
            stored = json.loads(content)
        except ValueError:
            raise InvalidRule(f"{self._path} is not JSON: correct or remove it") from None
        rules = stored.get("rules") if isinstance(stored, dict) else None
        if not isinstance(rules, list):
            raise InvalidRule(f"{self._path} holds no list of rules: correct or remove it")
        return rules, revision

    def load(self) -> dict:
        rules, revision = self._read()
        return {"rules": rules, "revision": revision}

    def rule(self, rule_id: str) -> dict:
        rules, _ = self._read()
        return self.find(rules, rule_id)

    @staticmethod
    def find(rules: list[dict], rule_id: str) -> dict:
        if not isinstance(rule_id, str) or not ID_PATTERN.match(rule_id):
            raise RuleNotFound(f"No rule {rule_id!r}")
        rule = next((rule for rule in rules if rule.get("id") == rule_id), None)
        if rule is None:
            raise RuleNotFound(f"No rule {rule_id!r}")
        return rule

    def users_of(self, script_id: str) -> list[str]:
        """The names of the rules that run the script, or pinned it as a library."""
        rules, _ = self._read()
        return rule_users(rules, script_id)

    # ── the versions a rule runs ──

    def _pinned_version(self, script_id: str, number: int) -> dict:
        try:
            script = self._scripts.script(script_id)
        except ScriptNotFound:
            raise InvalidRule(f"No script {script_id!r}: it may have been deleted") from None
        entry = next((entry for entry in script["versions"] if entry["number"] == number), None)
        if entry is None:
            raise InvalidRule(f"{script['name']!r} has no version {number}")
        return {**script, "source": self._scripts.source(script_id, number), "entry": entry}

    def pin(self, script_id: str, number: int) -> tuple[dict, list[dict]]:
        """
        The script's version ``number`` and the libraries it imports, at their
        current versions, dependencies first, as prepare.ts inlines them.
        """
        if not isinstance(script_id, str) or not ID_PATTERN.match(script_id):
            raise InvalidRule("A rule runs one of the scripts, by its id")
        if not _whole(number) or number < 1:
            raise InvalidRule("A rule runs one version of its script")
        script = self._pinned_version(script_id, number)
        facts = pine_facts(script["source"])
        if facts.kind == LIBRARY:
            raise InvalidRule(f"{script['name']!r} is a library: a rule runs an indicator or a strategy")
        libraries = self.libraries_of(facts, script["name"])
        pinned = {"id": script_id, "version": number, "sha256": script["entry"]["sha256"]}
        return pinned, libraries

    def libraries_of(self, facts, script_name: str) -> list[dict]:
        """
        The libraries a source imports, at their current versions, dependencies
        first, as prepare.ts inlines them: of a saved script, or of a source not
        saved yet (a study of a draft).
        """
        libraries: list[dict] = []

        def visit(imports, chain: list[str]) -> None:
            for imported in imports:
                name = imported["name"]
                if name in chain:
                    raise InvalidRule(f"The libraries import each other: {' → '.join([*chain, name])}")
                if any(entry["name"] == name for entry in libraries):
                    continue
                try:
                    library = self._scripts.library(name)
                except ScriptNotFound:
                    raise InvalidRule(f'No library "{name}": {script_name!r} imports it') from None
                visit(pine_facts(library["source"]).imports, [*chain, name])
                current = next(entry for entry in library["versions"] if entry["number"] == library["version"])
                libraries.append({
                    "name": name,
                    "id": library["id"],
                    "version": library["version"],
                    "sha256": current["sha256"],
                })

        visit(facts.imports, [])
        return libraries

    def verify(self, rule: dict) -> None:
        """Refuse a rule whose pinned versions are no longer on disk as they were saved."""
        pins = [(rule["script"], "script"), *((entry, f"library {entry['name']!r}") for entry in rule["libraries"])]
        for pin, what in pins:
            try:
                source = self._scripts.source(pin["id"], pin["version"])
            except ScriptNotFound:
                raise InvalidRule(f"The {what} of {rule['name']!r} is gone (version {pin['version']})") from None
            if hashlib.sha256(source.encode("utf-8")).hexdigest() != pin["sha256"]:
                raise InvalidRule(f"The {what} of {rule['name']!r} changed on disk since the rule was saved")

    # ── writing ──

    def _write(self, rules: list[dict]) -> str:
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        content = json.dumps({"rules": rules, "saved_at": _now()}, indent=2).encode("utf-8")
        partial = f"{self._path}.part"
        with open(partial, "wb") as out_file:
            out_file.write(content)
        os.replace(partial, self._path)
        return hashlib.sha256(content).hexdigest()

    @staticmethod
    def _check_revision(revision: str, current: str) -> None:
        if revision != current:
            raise RuleConflict("The rules were changed meanwhile: reload them, then make your change again")

    def _checked(self, fields: dict, rules: list[dict], previous: dict | None, refresh_libraries: bool) -> dict:
        name = _text(fields.get("name"), "A rule's name", MAX_NAME_LENGTH)
        for rule in rules:
            if rule["id"] != (previous or {}).get("id") and rule["name"].casefold() == name.casefold():
                raise RuleConflict(f"A rule named {rule['name']!r} already exists")
        indice = fields.get("indice")
        if not isinstance(indice, str) or not LABEL_PATTERN.match(indice):
            raise InvalidRule("The indice is WATA's name of the market traded, such as us100")
        pin = fields.get("script")
        if not isinstance(pin, dict):
            raise InvalidRule("A rule runs one of the scripts, by its id")
        same_script = previous is not None and (
            pin.get("id") == previous["script"]["id"] and pin.get("version") == previous["script"]["version"]
        )
        if same_script and not refresh_libraries:
            # Nothing that runs changes unless asked: the libraries stay at their versions.
            script, libraries = previous["script"], previous["libraries"]
        else:
            script, libraries = self.pin(pin.get("id"), pin.get("version"))
        rule = {
            "name": name,
            "asset": checked_asset(fields.get("asset")),
            "side": _choice(fields.get("side", "bid"), "The price side", SIDES),
            "bars": checked_bars(fields.get("bars")),
            "script": script,
            "libraries": libraries,
            "inputs": checked_inputs(fields.get("inputs")),
            "alerts": checked_alerts(fields.get("alerts")),
            "alert_calls": _choice(fields.get("alert_calls", IGNORE), "What alert() calls become", ALERT_CALL_ACTIONS),
            "confidence_plot": _title(fields.get("confidence_plot"), "The confidence plot", required=False),
            "indice": indice,
        }
        return rule

    def create(self, fields: dict, revision: str, mode: str = SHADOW, made_by: str | None = None) -> dict:
        """A new rule, off or in shadow: going live is set_mode's alone. ``made_by``: who made it through an agent."""
        if mode not in (OFF, SHADOW):
            raise InvalidRule("A new rule is off or in shadow: it goes live once it is saved")
        with self._scripts.lock:
            rules, current = self._read()
            self._check_revision(revision, current)
            if len(rules) >= MAX_RULES:
                raise InvalidRule(f"WATA keeps {MAX_RULES} rules at most: delete one first")
            now = _now()
            rule = {
                "id": secrets.token_hex(6),
                **self._checked(fields, rules, None, refresh_libraries=False),
                "mode": mode,
                "created_at": now,
                "updated_at": now,
                "mode_changed_at": now,
            }
            if made_by is not None:
                rule["made_by"] = made_by
            new_revision = self._write([*rules, rule])
        return {"rule": rule, "revision": new_revision}

    def update(self, rule_id: str, fields: dict, revision: str, refresh_libraries: bool = False) -> dict:
        """
        A rule changed, its mode kept. ``before`` is the rule as it was, for the
        audit of a live rule's change.
        """
        with self._scripts.lock:
            rules, current = self._read()
            self._check_revision(revision, current)
            before = self.find(rules, rule_id)
            changed = self._checked(fields, rules, before, refresh_libraries)
            rule = {**before, **changed}
            if rule == before:
                return {"rule": rule, "before": before, "revision": current}
            if rule["mode"] == LIVE:
                self._check_one_live(rules, rule)
            rule["updated_at"] = _now()
            new_revision = self._write([rule if entry["id"] == rule_id else entry for entry in rules])
        return {"rule": rule, "before": before, "revision": new_revision}

    @staticmethod
    def _check_one_live(rules: list[dict], rule: dict) -> None:
        for other in rules:
            if other["id"] != rule["id"] and other["mode"] == LIVE and other["indice"] == rule["indice"]:
                raise RuleConflict(
                    f"{other['name']!r} is already live on {rule['indice']}: the trader keeps one signal per indice"
                )

    def set_mode(self, rule_id: str, mode: str, revision: str) -> dict:
        """The rule in ``mode``. A rule goes live only if it can raise a signal, alone on its indice."""
        if mode not in MODES:
            raise InvalidRule(f"A rule's mode is one of {', '.join(MODES)}")
        with self._scripts.lock:
            rules, current = self._read()
            self._check_revision(revision, current)
            before = self.find(rules, rule_id)
            if mode == before["mode"]:
                return {"rule": before, "before": before, "revision": current}
            rule = {**before, "mode": mode, "mode_changed_at": _now()}
            if mode == LIVE:
                self._check_one_live(rules, rule)
                if not acts(rule):
                    raise InvalidRule(f"No alert of {rule['name']!r} becomes a signal: map one before going live")
                self.verify(rule)
            new_revision = self._write([rule if entry["id"] == rule_id else entry for entry in rules])
        return {"rule": rule, "before": before, "revision": new_revision}

    def delete(self, rule_id: str, revision: str) -> dict:
        with self._scripts.lock:
            rules, current = self._read()
            self._check_revision(revision, current)
            rule = self.find(rules, rule_id)
            if rule["mode"] == LIVE:
                raise RuleConflict(f"{rule['name']!r} is live: switch it to shadow or off before deleting it")
            new_revision = self._write([entry for entry in rules if entry["id"] != rule_id])
        return {"rule": rule, "revision": new_revision}
