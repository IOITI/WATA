"""
What each signal rule ran, and since when. rules.json keeps a rule as it is
now; a day of the past is drawn as the engine ran it (a paper day's indicator,
src/admin/backtest_indicator.py) from <ta root>/rules.history.jsonl, a line
each time what a rule runs changes (RUN_FIELDS: its asset, side, bars, script,
libraries, inputs and own history_days):

    {"rule_id", "at", "after"}                                a rule made
    {"rule_id", "at", "before", "before_since", "after"}      what it runs changed
    {"rule_id", "at", "before", "before_since", "deleted"}    a rule deleted

``before_since`` is when what ``before`` held began to run: the rule's last
change of it, or its making. A change of its name, its alerts or its mode
leaves no line: the same script runs on the same bars.

Kept out of src/admin/ta_rules.py, which the TA engine imports: the engine
never reads this, and its image stays as it is. Every writer of the admin
builds its rule store from here (the TA routes, the AI agent's proposals, the
MCP endpoint's shadow rules); a reader may use either.
"""

import json
import logging
import os
from datetime import datetime, timezone

from src.admin.ta_rules import RUN_FIELDS, SHADOW, RuleStore

logger = logging.getLogger(__name__)

HISTORY_FILE = "rules.history.jsonl"


def run_fields(rule: dict) -> dict:
    """What a rule runs: those of RUN_FIELDS it has (a rule that runs on the engine's history_days has none)."""
    return {name: rule[name] for name in RUN_FIELDS if name in rule}


def running_since(rule: dict) -> str | None:
    """When what a rule runs began to run: its last change of it, or its making."""
    return rule.get("run_changed_at") or rule.get("created_at")


def _instant(value) -> float | None:
    """An ISO time (the rule store's) or epoch seconds, as epoch seconds; None when it is neither."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def definition_at(lines: list[dict], rule: dict | None, rule_id: str, moment) -> tuple[dict | None, bool]:
    """
    What a rule ran at ``moment`` (epoch seconds or ISO), and whether that is
    known: the last line at or before it says what came after it; else the
    first line after it says what came before, when that had begun by then;
    with no line, the rule as it is, when it ran so since before the moment.
    Otherwise the rule as it is (None when it is gone), not known to be what ran.
    """
    at = _instant(moment)
    own = sorted((line for line in lines if line.get("rule_id") == rule_id and _instant(line.get("at")) is not None),
                 key=lambda line: _instant(line["at"]))
    now = run_fields(rule) if rule is not None else None
    if at is None:
        return now, False
    earlier = [line for line in own if _instant(line["at"]) <= at]
    if earlier:
        last = earlier[-1]
        if last.get("deleted"):
            return None, False
        return last.get("after"), last.get("after") is not None
    if own:
        first = own[0]
        began = _instant(first.get("before_since"))
        if first.get("before") is not None and began is not None and began <= at:
            return first["before"], True
        return (first.get("before") if first.get("before") is not None else now), False
    began = _instant(running_since(rule)) if rule is not None else None
    return now, now is not None and began is not None and began <= at


class RuleHistory:
    """The lines of ``<root>/rules.history.jsonl``, appended one at a time."""

    def __init__(self, root: str):
        self._path = os.path.join(root, HISTORY_FILE)

    @property
    def path(self) -> str:
        return self._path

    def lines(self) -> list[dict]:
        """Every line kept, oldest first; one that cannot be read is passed over."""
        try:
            with open(self._path, encoding="utf-8") as history_file:
                content = history_file.read()
        except FileNotFoundError:
            return []
        found = []
        for text in content.splitlines():
            try:
                line = json.loads(text)
            except ValueError:
                continue
            if isinstance(line, dict):
                found.append(line)
        return found

    def append(self, line: dict) -> None:
        """One line more; a line that cannot be written is said in the log, and what was saved stands."""
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            with open(self._path, "a", encoding="utf-8") as history_file:
                history_file.write(json.dumps(line, separators=(",", ":")) + "\n")
        except OSError as e:
            logger.error("The history of the signal rules could not be written (%s): %s", self._path, e)

    def definition_at(self, rule: dict | None, rule_id: str, moment) -> tuple[dict | None, bool]:
        return definition_at(self.lines(), rule, rule_id, moment)


class RecordedRuleStore(RuleStore):
    """The rule store whose every change of what a rule runs leaves its line in the rules' history."""

    def __init__(self, scripts):
        super().__init__(scripts)
        self.history = RuleHistory(scripts.root)

    def create(self, fields: dict, revision: str, mode: str = SHADOW, made_by: str | None = None) -> dict:
        with self._scripts.lock:
            result = super().create(fields, revision, mode, made_by)
            rule = result["rule"]
            self.history.append({"rule_id": rule["id"], "at": rule["created_at"], "after": run_fields(rule)})
        return result

    def update(self, rule_id: str, fields: dict, revision: str, refresh_libraries: bool = False) -> dict:
        with self._scripts.lock:
            result = super().update(rule_id, fields, revision, refresh_libraries)
            rule, before = result["rule"], result["before"]
            if run_fields(rule) != run_fields(before):
                self.history.append({
                    "rule_id": rule_id, "at": rule["updated_at"], "before": run_fields(before),
                    "before_since": running_since(before), "after": run_fields(rule),
                })
        return result

    def delete(self, rule_id: str, revision: str) -> dict:
        with self._scripts.lock:
            result = super().delete(rule_id, revision)
            rule = result["rule"]
            self.history.append({
                "rule_id": rule_id, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "before": run_fields(rule), "before_since": running_since(rule), "deleted": True,
            })
        return result
