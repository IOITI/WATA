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

A rule's page reads them with its switches into and out of live, as the audit
trail says them (``rule_timeline``).
"""

import ast
import json
import logging
import os
from datetime import datetime, timezone

from src.admin.ta_rules import RUN_FIELDS, SHADOW, RuleStore

logger = logging.getLogger(__name__)

HISTORY_FILE = "rules.history.jsonl"

# What the TA routes write in the audit trail of a rule (src/admin/routers/ta.py, its mode and a live rule's change),
# read back for its page: its id in parentheses in the detail, the switch said in the action.
WENT_LIVE = "signal rule live"
LEFT_LIVE = "signal rule no longer live ("
CHANGED_LIVE = "live signal rule changed"
_WITHOUT = " WITHOUT its readiness ("
_LICENCE = ", the licence of its script ("
_CHECKED = ") checked by the operator"
_PROPOSED = "Proposed by the AI agent: "
_CHANGED = " changed="
# A live rule's change writes its line here and its audit entry in one request: one change, said once.
SAME_CHANGE_SECONDS = 10


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


# ── a rule's page: what it ran over time ──


def _pin(pin, names: dict[str, str]) -> dict | None:
    """A script's pin as people read it: its name (the script's now), its version."""
    if not isinstance(pin, dict):
        return None
    return {"id": pin.get("id"), "name": names.get(pin.get("id")), "version": pin.get("version")}


def _libraries(pins) -> list[dict]:
    return [{"name": pin.get("name"), "version": pin.get("version")} for pin in pins or [] if isinstance(pin, dict)]


def runs_of(fields: dict, names: dict[str, str]) -> dict:
    """What a rule ran, as its history keeps it, with its script's name: its inputs counted."""
    return {
        "script": _pin(fields.get("script"), names),
        "libraries": _libraries(fields.get("libraries")),
        "asset": fields.get("asset"),
        "side": fields.get("side"),
        "bars": fields.get("bars"),
        "history_days": fields.get("history_days"),
        "inputs": len(fields.get("inputs") or []),
    }


def changes_between(before: dict, after: dict, names: dict[str, str]) -> list[dict]:
    """
    What a change of what a rule runs changed, a field at a time (its script by id and version,
    not its digest), and its inputs one by one, each named as the rule's dialog names it.
    """
    changes = []
    old, new = before.get("script") or {}, after.get("script") or {}
    if (old.get("id"), old.get("version")) != (new.get("id"), new.get("version")):
        changes.append({"what": "script", "before": _pin(old or None, names), "after": _pin(new or None, names)})
    if _libraries(before.get("libraries")) != _libraries(after.get("libraries")):
        changes.append({"what": "libraries", "before": _libraries(before.get("libraries")),
                        "after": _libraries(after.get("libraries"))})
    for field in ("asset", "side", "bars", "history_days"):
        if before.get(field) != after.get(field):
            changes.append({"what": field, "before": before.get(field), "after": after.get(field)})
    old_inputs = {entry.get("id"): entry for entry in before.get("inputs") or [] if isinstance(entry, dict)}
    new_inputs = {entry.get("id"): entry for entry in after.get("inputs") or [] if isinstance(entry, dict)}
    for input_id in sorted(set(old_inputs) | set(new_inputs), key=lambda key: (len(str(key)), str(key))):
        was, now = old_inputs.get(input_id), new_inputs.get(input_id)
        if (was or {}).get("value") == (now or {}).get("value") and (was is None) == (now is None):
            continue
        named = now or was
        changes.append({
            "what": "input", "id": input_id, "name": named.get("title") or named.get("var") or input_id,
            "before": None if was is None else was.get("value"), "after": None if now is None else now.get("value"),
        })
    return changes


def _proposal(detail: str) -> str | None:
    """The title of the AI agent's proposal an audit entry applied (src/admin/proposed_actions.py said)."""
    if _PROPOSED not in detail:
        return None
    said = detail.split(_PROPOSED, 1)[1].strip()
    try:
        title = ast.literal_eval(said)
    except (ValueError, SyntaxError):
        return said
    return title if isinstance(title, str) else said


def _went_live(action: str) -> tuple[list[str], str | None]:
    """What a switch into live said of itself: what its readiness still missed, the licence the operator checked."""
    licence = None
    if _LICENCE in action:
        action, said = action.split(_LICENCE, 1)
        licence = said.removesuffix(_CHECKED)
    unmet = []
    if _WITHOUT in action:
        unmet = [part.strip() for part in action.split(_WITHOUT, 1)[1].removesuffix(")").split("; ") if part.strip()]
    return unmet, licence


def rule_timeline(lines: list[dict], audit: list[dict], rule: dict, names: dict[str, str]) -> list[dict]:
    """
    One rule's history for its page, newest first:

    - ``made``: when, by whom (``made_by``: Claude's, the AI agent's, else the
      operator's) and what it ran then (None for a rule made before its history
      was kept, of which only the moment is known);
    - ``changed``: a change of what it runs, field by field (``changes_between``),
      ``live`` when it was made to the live rule, with what else changed then
      that it does not run (its name, alerts, indice: ``also``);
    - ``live`` and ``left_live``: its switches into and out of live, what its
      readiness still missed and the licence the operator checked, the AI
      agent's proposal applied;
    - ``changed_live``: a change of the live rule that changed nothing it runs.

    Read from the history's lines and the audit trail's entries; never anyone's address.
    The history is dated to the second and the trail finer: a change of the live rule takes
    the trail's moment, and events of one moment keep the order each source wrote them in.
    """
    rule_id = rule["id"]
    # Each event with its place in its own source, oldest first: what breaks a tie of moments.
    events: list[tuple[int, dict]] = []
    for place, line in enumerate(lines):
        if line.get("rule_id") != rule_id or line.get("deleted"):
            continue
        if line.get("before") is None:
            events.append((place, {"at": line.get("at"), "kind": "made", "by": rule.get("made_by"),
                                   "runs": runs_of(line.get("after") or {}, names)}))
        else:
            events.append((place, {"at": line.get("at"), "kind": "changed",
                                   "changes": changes_between(line["before"], line.get("after") or {}, names),
                                   "live": False, "also": []}))
    if not any(event["kind"] == "made" for _, event in events) and rule.get("created_at"):
        events.append((-1, {"at": rule["created_at"], "kind": "made", "by": rule.get("made_by"), "runs": None}))

    marker = f"({rule_id})"
    for place, entry in enumerate(reversed(audit)):
        detail, action = str(entry.get("detail") or ""), str(entry.get("action") or "")
        if marker not in detail:
            continue
        at, proposed = entry.get("at"), _proposal(detail)
        if action.startswith(WENT_LIVE):
            unmet, licence = _went_live(action)
            events.append((place, {"at": at, "kind": "live", "unmet": unmet, "licence": licence, "proposed": proposed}))
        elif action.startswith(LEFT_LIVE):
            events.append((place, {"at": at, "kind": "left_live",
                                   "to": action.removeprefix(LEFT_LIVE).removesuffix(")"), "proposed": proposed}))
        elif action == CHANGED_LIVE:
            fields = [name for name in detail.rsplit(_CHANGED, 1)[1].split(",") if name] if _CHANGED in detail else []
            same = _same_change(events, _instant(at), {name for name in fields if name in RUN_FIELDS})
            if same is not None:
                same.update(at=at, live=True, also=[name for name in fields if name not in RUN_FIELDS])
            else:
                events.append((place, {"at": at, "kind": "changed_live", "fields": fields}))
    ordered = sorted(events, key=lambda pair: (_instant(pair[1].get("at")) or 0.0, pair[0]), reverse=True)
    # A line whose change shows nothing (its script's digest alone) is left out.
    return [event for _, event in ordered if event["kind"] != "changed" or event["changes"]]


def _same_change(events: list[tuple[int, dict]], moment: float | None, fields: set[str]) -> dict | None:
    """
    The line of a live rule's change its audit entry says again: a change of the same fields it
    runs within SAME_CHANGE_SECONDS, the latest of them (two changes of one second differ by them).
    """
    if moment is None or not fields:
        return None
    found = [(place, event) for place, event in events
             if event["kind"] == "changed" and not event["live"]
             and abs((_instant(event.get("at")) or 0.0) - moment) <= SAME_CHANGE_SECONDS
             and {"inputs" if change["what"] == "input" else change["what"] for change in event["changes"]} == fields]
    return max(found, key=lambda pair: pair[0])[1] if found else None


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
