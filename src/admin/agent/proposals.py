"""
What the agent proposes, and what becomes of it when the operator applies it.

A proposal writes nothing but itself (``proposals.json`` of its conversation):
- a new version of one of the operator's scripts, an indicator or a strategy,
  from one of Claude's own scripts that pine_save saved (it compiled). A library
  never: Claude cannot keep a copy under its name, which only one script holds;
- a change to one of the operator's signal rules that is not live: the version
  of its script it runs, the values of inputs, its days of history;
- a change to settings of config.json that a backtest reads (the FIELDS of
  src/backtest/settings.py read from it), resting on finished runs;
- a new script of the operator's, an indicator or a strategy, from one of
  Claude's own.

Applying is the operator's click, through the stores' own checks: the script's
version the change was made on (ScriptConflict, a 409, when it moved on), the
rules' revision (RuleConflict when they changed meanwhile), and a rule live
by the moment it is applied is refused. A version saved this way says Claude
wrote it (``made_by`` of its entry); the script stays the operator's.
Neither is an audited action, as the same saves made by hand are not.

Settings are applied by the agent's route (src/admin/routers/agent.py) with the
Configuration editor's preview and save, audited as the Configuration page's
saves are: this package never reaches the editor, it only says what to write.
"""

import difflib
import secrets
import time

from src.admin import config_catalog
from src.admin.agent.store import PROPOSAL_ID, stamp
from src.admin.errors import InvalidRequest, NotPossible, UnknownRun
from src.admin.mcp_server.facade import CLAUDE
from src.admin.ta_rules import LIVE, RuleNotFound, RuleStore
from src.admin.ta_scripts import (LIBRARY, MAX_DESCRIPTION_LENGTH, MAX_NAME_LENGTH, InvalidScript, ScriptConflict,
                                    ScriptNotFound)
from src.backtest.settings import FIELDS, FIELDS_BY_NAME, InvalidSettings, with_changes

PROPOSED, APPLIED, DISMISSED = "proposed", "applied", "dismissed"
SCRIPT, RULE, SETTINGS, NEW_SCRIPT = "script", "rule", "settings", "new_script"
MAX_TITLE = 160
MAX_RATIONALE = 4000
# The runs a change of settings rests on, at most.
MAX_RUNS = 6
# Read by a backtest from config.json, but said in words rather than proposed: whether the window around US
# releases is on and its events (a rule added or removed, a list), the timezone of the hours (its absence means
# the trading one), and the TA engine's days (every rule's engine day moves).
_SAID_IN_WORDS = frozenset({
    "market_timezone", "news_window", "news_fomc", "news_cpi", "news_nfp", "news_pce", "news_ppi", "news_retail",
    "news_gdp", "news_jolts", "history_days", "restart_time",
})
PROPOSED_SETTINGS = tuple(entry.name for entry in FIELDS if entry.source and entry.name not in _SAID_IN_WORDS)
_RULES = "trade.rules."


class ProposalRefused(Exception):
    """A proposal the agent may not make, said in a sentence for it."""


def _text(value, what: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProposalRefused(f"{what} is needed")
    return value.strip()[:limit]


def fields_of(rule: dict) -> dict:
    """A stored rule as the rules' route takes it back (RuleUpdate.fields): saved again, it changes nothing."""
    return {
        "name": rule["name"],
        "asset": {"provider": rule["asset"]["provider"], "key": rule["asset"]["key"]},
        "side": rule.get("side", "bid"),
        "bars": dict(rule["bars"]),
        "script": {"id": rule["script"]["id"], "version": rule["script"]["version"]},
        "inputs": [dict(entry) for entry in rule.get("inputs") or []],
        "alerts": [dict(entry) for entry in rule.get("alerts") or []],
        "alert_calls": rule.get("alert_calls", "ignore"),
        "confidence_plot": rule.get("confidence_plot"),
        "indice": rule["indice"],
        "history_days": rule.get("history_days"),
    }


def catalog_path(source: str) -> str:
    """Where config.json keeps a setting, in the catalog's words: a rule's key is in its ``rule_config``."""
    if source.startswith(_RULES):
        rule_type, _, key = source[len(_RULES):].partition(".")
        return f"trade.rules[{rule_type}].rule_config.{key}"
    return source


def _in_catalog_bounds(entry, param, value) -> None:
    """A value config.json's validator would take: the catalog's own bounds, which may be narrower than a run's."""
    if param.kind == "int" and value != int(value):
        raise ProposalRefused(f"'{entry.label}' is a whole number in config.json")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return
    if (param.minimum is not None and value < param.minimum) or (param.maximum is not None and value > param.maximum):
        least = "" if param.minimum is None else f"from {param.minimum:g}"
        most = "" if param.maximum is None else f"up to {param.maximum:g}"
        raise ProposalRefused(f"'{entry.label}' is {' '.join(part for part in (least, most) if part)} in config.json")


def _lines_changed(before: str, after: str) -> dict:
    added = removed = 0
    for line in difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=0):
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return {"added": added, "removed": removed}


class Proposals:
    def __init__(self, store, scripts, clock=time.time, *, backtests=None):
        self._store = store
        self._scripts = scripts
        self._rules = RuleStore(scripts)
        self._clock = clock
        # The backtests' service: the live settings and the runs a change of settings rests on (None: off).
        self._backtests = backtests

    # ── made by the agent ──

    def _record(self, conversation_id: str, proposal: dict) -> dict:
        proposals = self._store.proposals(conversation_id)
        proposals[proposal["id"]] = proposal
        self._store.write_proposals(conversation_id, proposals)
        self._store.add_artefact({"at": proposal["created_at"], "kind": "proposal", "conversation": conversation_id,
                                  "id": proposal["id"], "proposal": proposal["kind"], "title": proposal["title"]})
        return proposal

    def _new(self, kind: str, request: int, arguments: dict) -> dict:
        return {
            "id": f"p{secrets.token_hex(4)}", "kind": kind, "request": request, "created_at": stamp(self._clock()),
            "status": PROPOSED, "title": _text(arguments.get("title"), "A title", MAX_TITLE),
            "rationale": _text(arguments.get("rationale"), "A rationale", MAX_RATIONALE),
        }

    def propose_script(self, conversation_id: str, request: int, arguments: dict) -> dict:
        try:
            target = self._scripts.script(arguments["script_id"])
            draft = self._scripts.script(arguments["from_script_id"])
            source = self._scripts.source(arguments["from_script_id"], arguments["from_version"])
        except ScriptNotFound as e:
            raise ProposalRefused(str(e)) from None
        if target.get("made_by") == CLAUDE:
            raise ProposalRefused(f"{target['name']!r} is one of your own scripts: save the change with pine_save")
        if target["kind"] == LIBRARY:
            raise ProposalRefused(f"{target['name']!r} is a library, which only the operator changes: say what you "
                                  "would change in it")
        if draft.get("made_by") != CLAUDE:
            raise ProposalRefused("The change comes from one of your own scripts, saved with pine_save")
        if arguments["base_version"] != target["version"]:
            raise ProposalRefused(f"{target['name']!r} is at version {target['version']}, not "
                                  f"{arguments['base_version']}: make the change on it")
        if source == target["source"]:
            raise ProposalRefused(f"Version {arguments['from_version']} of {draft['name']!r} is "
                                  f"{target['name']!r} as it is: nothing to propose")
        proposal = self._new(SCRIPT, request, arguments)
        proposal.update(
            script={"id": target["id"], "name": target["name"], "base_version": target["version"]},
            draft={"id": draft["id"], "name": draft["name"], "version": arguments["from_version"]},
            lines=_lines_changed(target["source"], source),
        )
        return self._record(conversation_id, proposal)

    def propose_rule(self, conversation_id: str, request: int, arguments: dict) -> dict:
        loaded = self._rules.load()
        try:
            rule = RuleStore.find(loaded["rules"], arguments["rule_id"])
        except RuleNotFound as e:
            raise ProposalRefused(str(e)) from None
        if rule["mode"] == LIVE:
            raise ProposalRefused(f"{rule['name']!r} is live: changing it is the operator's alone. Say what you would "
                                  "change, and why")
        changes: dict = {}
        if arguments.get("script_version") is not None and arguments.get("after_proposal"):
            raise ProposalRefused("Either a version of its script or a proposal of yours, not both")
        if arguments.get("script_version") is not None:
            version = arguments["script_version"]
            try:
                _, libraries = self._rules.pin(rule["script"]["id"], version)
            except Exception as e:
                raise ProposalRefused(str(e)) from None
            changes["script_version"] = version
            changes["libraries"] = _libraries_moved(rule.get("libraries") or [], libraries)
        if arguments.get("after_proposal"):
            first = self._store.proposals(conversation_id).get(arguments["after_proposal"])
            if first is None or first["kind"] != SCRIPT or first["script"]["id"] != rule["script"]["id"]:
                raise ProposalRefused("after_proposal is one of your proposals of a new version of the rule's own script")
            changes["after_proposal"] = first["id"]
        if arguments.get("inputs"):
            known = {entry["id"]: entry for entry in rule.get("inputs") or []}
            changes["inputs"] = [
                {"id": entry["id"], "title": (known.get(entry["id"]) or {}).get("title"),
                 "before": (known.get(entry["id"]) or {}).get("value"), "after": entry["value"]}
                for entry in arguments["inputs"]
            ]
        if "history_days" in arguments:
            changes["history_days"] = {"before": rule.get("history_days"), "after": arguments["history_days"]}
        if not changes:
            raise ProposalRefused("Nothing to change: a script version, inputs or history days")
        proposal = self._new(RULE, request, arguments)
        proposal.update(rule={"id": rule["id"], "name": rule["name"], "mode": rule["mode"],
                              "script_id": rule["script"]["id"], "script_version": rule["script"]["version"]},
                        changes=changes)
        return self._record(conversation_id, proposal)

    def propose_settings(self, conversation_id: str, request: int, arguments: dict) -> dict:
        """
        Settings of config.json a backtest reads, each to a value checked as a run
        checks it and as config.json's catalog bounds it, resting on finished runs.
        A value that is the live one already is left out (``same`` says which).
        """
        if self._backtests is None:
            raise ProposalRefused("Backtests are switched off: a change of settings rests on runs, so say it in words")
        asked = arguments.get("changes") or []
        names = [change["setting"] for change in asked]
        if not names:
            raise ProposalRefused("Nothing to change: name each setting (backtest_options) and its value")
        if len(set(names)) != len(names):
            raise ProposalRefused("Name each setting once")
        try:
            live = self._backtests.live_settings()
        except NotPossible as e:
            raise ProposalRefused(str(e)) from None
        for name in names:
            entry = FIELDS_BY_NAME.get(name)
            if entry is None:
                raise ProposalRefused(f"{name!r} is not a setting of backtest_options")
            if not entry.source:
                raise ProposalRefused(f"'{entry.label}' is the backtest's own, not in config.json: nothing to propose")
            if name not in PROPOSED_SETTINGS:
                raise ProposalRefused(f"'{entry.label}' is the operator's to change in Configuration: say what you "
                                      "would set it to, and why")
            if name.startswith("news_") and not live.news_window:
                raise ProposalRefused("The window around US releases is off in config.json: say in words whether it "
                                      "should be on, and with which minutes")
        try:
            _, kept, before = with_changes(live, {change["setting"]: change["value"] for change in asked})
        except InvalidSettings as e:
            raise ProposalRefused(str(e)) from None
        if not kept:
            raise ProposalRefused("Each value is the live one already: nothing to propose")
        settings = []
        for name, value in kept.items():
            entry = FIELDS_BY_NAME[name]
            path = catalog_path(entry.source)
            param = config_catalog.lookup(path)
            if param is None or param.access != config_catalog.EDITABLE:
                raise ProposalRefused(f"'{entry.label}' is not changed from the admin: say it in words")
            _in_catalog_bounds(entry, param, value)
            settings.append({"setting": name, "label": entry.label, "unit": entry.unit, "group": entry.group,
                             "path": path, "before": before[name], "after": value})
        runs = self._finished_runs(arguments.get("runs") or [])
        proposal = self._new(SETTINGS, request, arguments)
        proposal.update(settings=settings, runs=runs, same=[name for name in names if name not in kept])
        return self._record(conversation_id, proposal)

    def _finished_runs(self, run_ids: list) -> list[dict]:
        if not run_ids:
            raise ProposalRefused("Name the finished runs the change rests on (backtest_start with the changes)")
        runs = []
        for run_id in dict.fromkeys(run_ids):
            try:
                run = self._backtests.get(run_id)
            except UnknownRun:
                raise ProposalRefused(f"No run {run_id}") from None
            if (run.get("status") or {}).get("state") != "done":
                raise ProposalRefused(f"Run {run_id} is not finished: wait for it (backtest_wait)")
            runs.append({"id": run_id, "name": run.get("name"), "kind": run.get("kind"), "from": run.get("from"),
                         "to": run.get("to"), "changes": run.get("changes") or {}, "headline": run.get("headline")})
        return runs

    def propose_new_script(self, conversation_id: str, request: int, arguments: dict) -> dict:
        """A new script of the operator's, an indicator or a strategy, from one of Claude's that compiled."""
        try:
            draft = self._scripts.script(arguments["from_script_id"])
            source = self._scripts.source(arguments["from_script_id"], arguments["from_version"])
        except ScriptNotFound as e:
            raise ProposalRefused(str(e)) from None
        if draft.get("made_by") != CLAUDE:
            raise ProposalRefused("A new script comes from one of your own, saved with pine_save")
        if draft["kind"] == LIBRARY:
            raise ProposalRefused("A library is the operator's to make: say what it would hold")
        name = " ".join(_text(arguments.get("name"), "A name", MAX_NAME_LENGTH).split())
        if self._name_taken(name):
            raise ProposalRefused(f"A script is named {name!r} already: choose another name")
        proposal = self._new(NEW_SCRIPT, request, arguments)
        proposal.update(
            draft={"id": draft["id"], "name": draft["name"], "version": arguments["from_version"], "kind": draft["kind"]},
            new_script={"name": name, "description": (arguments.get("description") or "").strip()[:MAX_DESCRIPTION_LENGTH]},
            lines={"added": len(source.splitlines()), "removed": 0},
        )
        return self._record(conversation_id, proposal)

    def _name_taken(self, name: str) -> bool:
        return any(script["name"].casefold() == name.casefold() for script in self._scripts.scripts())

    # ── applied, or dismissed, by the operator ──

    def _proposal(self, conversation_id: str, proposal_id: str) -> tuple[dict, dict]:
        if not isinstance(proposal_id, str) or not PROPOSAL_ID.match(proposal_id):
            raise InvalidRequest(f"No proposal {proposal_id!r}")
        proposals = self._store.proposals(conversation_id)
        proposal = proposals.get(proposal_id)
        if proposal is None:
            raise InvalidRequest(f"No proposal {proposal_id!r} in this conversation")
        if proposal["status"] != PROPOSED:
            raise NotPossible(f"This proposal was {proposal['status']} already")
        return proposals, proposal

    def _settled(self, conversation_id: str, proposals: dict, proposal: dict, status: str, by: str | None,
                 result: dict | None = None) -> dict:
        proposal.update(status=status, settled_at=stamp(self._clock()), settled_by=by)
        if result is not None:
            proposal["result"] = result
        proposals[proposal["id"]] = proposal
        self._store.write_proposals(conversation_id, proposals)
        self._store.append(conversation_id, "activity", {
            "at": proposal["settled_at"], "event": status, "proposal": proposal["id"], "result": result})
        self._store.add_artefact({"at": proposal["settled_at"], "kind": status, "conversation": conversation_id,
                                  "id": proposal["id"], "result": result})
        return proposal

    def apply(self, conversation_id: str, proposal_id: str, by: str | None) -> dict:
        proposals, proposal = self._proposal(conversation_id, proposal_id)
        if proposal["kind"] == SETTINGS:
            raise NotPossible("A change of settings is saved from its preview, as the Configuration page saves it")
        if proposal["kind"] == SCRIPT:
            result = self._apply_script(proposal)
        elif proposal["kind"] == NEW_SCRIPT:
            result = self._apply_new_script(proposal)
        else:
            result = self._apply_rule(proposals, proposal)
        return self._settled(conversation_id, proposals, proposal, APPLIED, by, result)

    def pending_settings(self, conversation_id: str, proposal_id: str) -> dict:
        """A change of settings not settled yet, for the agent's route to preview and save with the editor."""
        _, proposal = self._proposal(conversation_id, proposal_id)
        if proposal["kind"] != SETTINGS:
            raise InvalidRequest("Only a change of settings is previewed")
        return proposal

    def settings_saved(self, conversation_id: str, proposal_id: str, by: str | None, saved: dict) -> dict:
        """The change saved by the editor (its revision, its backup, the services to start again): applied."""
        proposals, proposal = self._proposal(conversation_id, proposal_id)
        result = {"revision": saved.get("revision"), "backup": saved.get("backup"),
                  "services": list((saved.get("impact") or {}).get("services") or [])}
        return self._settled(conversation_id, proposals, proposal, APPLIED, by, result)

    @staticmethod
    def settings_config(proposal: dict, config: dict) -> dict:
        """
        ``config`` (as the editor loads it, secrets masked) with the proposal's
        values: a rule that is not in it is refused, never added, since adding
        one switches on what it holds (the window around US releases).
        """
        rules = {rule.get("rule_type") for rule in (config.get("trade") or {}).get("rules") or [] if isinstance(rule, dict)}
        for change in proposal["settings"]:
            path = change["path"]
            if path.startswith("trade.rules["):
                rule_type = path[len("trade.rules["):].partition("]")[0]
                if rule_type not in rules:
                    raise NotPossible(f"config.json has no {rule_type} rule now: '{change['label']}' cannot be set")
            config_catalog.place(config, path, change["after"])
        return config

    def dismiss(self, conversation_id: str, proposal_id: str, by: str | None) -> dict:
        proposals, proposal = self._proposal(conversation_id, proposal_id)
        return self._settled(conversation_id, proposals, proposal, DISMISSED, by)

    def _apply_script(self, proposal: dict) -> dict:
        target = self._scripts.script(proposal["script"]["id"])
        if target["kind"] == LIBRARY or target.get("made_by") == CLAUDE:
            raise NotPossible(f"{target['name']!r} is no longer a script of yours the agent may change")
        source = self._scripts.source(proposal["draft"]["id"], proposal["draft"]["version"])
        # ScriptConflict (409) when it was saved since: the change was made on the version before.
        saved = self._scripts.save(target["id"], proposal["script"]["base_version"], source=source, made_by=CLAUDE)
        return {"script_id": saved["id"], "version": saved["version"]}

    def _apply_new_script(self, proposal: dict) -> dict:
        source = self._scripts.source(proposal["draft"]["id"], proposal["draft"]["version"])
        try:
            # The operator's script, its first version marked as Claude's writing.
            made = self._scripts.create(proposal["new_script"]["name"], source, proposal["new_script"]["description"],
                                        written_by=CLAUDE)
        except ScriptConflict as e:
            raise NotPossible(f"{e}: ask the agent for another name") from None
        except InvalidScript as e:
            raise NotPossible(str(e)) from None
        return {"script_id": made["id"], "version": made["version"], "name": made["name"]}

    def _apply_rule(self, proposals: dict, proposal: dict) -> dict:
        changes = proposal["changes"]
        version = changes.get("script_version")
        if changes.get("after_proposal"):
            first = proposals.get(changes["after_proposal"]) or {}
            if first.get("status") != APPLIED:
                raise NotPossible(f"Apply the new version of the script first ({first.get('title') or 'its proposal'})")
            version = first["result"]["version"]
        loaded = self._rules.load()
        rule = RuleStore.find(loaded["rules"], proposal["rule"]["id"])
        if rule["mode"] == LIVE:
            raise NotPossible(f"{rule['name']!r} is live now: changing it is yours alone, from its page")
        fields = fields_of(rule)
        if version is not None:
            fields["script"]["version"] = version
        if changes.get("inputs"):
            inputs = {entry["id"]: entry for entry in fields["inputs"]}
            for change in changes["inputs"]:
                inputs.setdefault(change["id"], {"id": change["id"], "var": None, "title": None})["value"] = change["after"]
            fields["inputs"] = list(inputs.values())
        if "history_days" in changes:
            fields["history_days"] = changes["history_days"]["after"]
        # The revision read with the mode: a rule taken live meanwhile changed it, and is refused (RuleConflict).
        updated = self._rules.update(rule["id"], fields, loaded["revision"])["rule"]
        return {"rule_id": updated["id"], "script_version": updated["script"]["version"],
                "libraries": _libraries_moved(rule.get("libraries") or [], updated.get("libraries") or [])}


def _libraries_moved(before: list[dict], after: list[dict]) -> list[dict]:
    """The libraries a re-pin moves to their current versions: a rule runs them as they are then."""
    versions = {entry["name"]: entry["version"] for entry in before}
    return [{"name": entry["name"], "before": versions.get(entry["name"]), "after": entry["version"]}
            for entry in after if versions.get(entry["name"]) != entry["version"]]
