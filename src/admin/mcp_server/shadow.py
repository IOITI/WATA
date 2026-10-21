"""
Claude's own shadow rules: the only signal rules the MCP endpoint writes, and
only once the operator has switched them on (``admin.mcp.shadow_rules``).

A shadow rule's signals are journaled by the TA engine, never sent, and each
finished day of them is traded on paper (src/admin/paper.py): Claude sees
whether a strategy does live what its backtest said it would. Going live is
the operator's alone, on the Signal rules page. Nothing here sets a rule live,
and once the operator has taken one live it is no longer Claude's to change.

Claude may, each time checked under the rules' lock together with the write:

- make a rule, in shadow or off, named "Claude · …" and made_by "claude": at
  most ``admin.mcp.max_shadow_rules`` of its rules that are not live;
- change one of its own rules that is not live, or switch it between shadow
  and off;
- delete one of its own rules that is not live;
- make a finished run, made from one of its rules, that rule's reference.

Before a rule runs (made in shadow, switched to shadow, or its script, bars,
inputs or history changed), it is run as the engine would run it on the last
finished day of its epic, a study's signals (src/research/study.py). It is
saved only if it ran without failing, each engine day in at most
``admin.mcp.shadow_day_ms`` (2 s by default) and on at most 20,000 bars, on an
epic the Capital.com feed records now. What comes back of that run is
its bars and its time, never a signal or a price: it may be a day of the lock
box. The engine runs the shadow rules on a runner of their own, after the live
rules (src/ta_engine/engine.py): even a rule that turns slow later never holds
back a live one.
"""

from datetime import date, timedelta

from starlette.concurrency import run_in_threadpool

from src.admin.mcp_server.budget import Refused
from src.admin.mcp_server.facade import CLAUDE
from src.admin.ta_rules import (
    LIVE,
    OFF,
    SHADOW,
    RuleStore,
    bars_label,
    checked_alerts,
    checked_asset,
    checked_bars,
    checked_history_days,
    checked_inputs,
)
from src.backtest.clock import TradingClock

PREFIX = "Claude · "
MODES = (SHADOW, OFF)
# The bars of a day of the engine a shadow rule may run on (its time is admin.mcp.shadow_day_ms).
MAX_DAY_BARS = 20_000
# A rule's history_days given as this: back to the engine's.
ENGINE_HISTORY = -1
# The feed records an epic now when its last bar is this recent (a weekend, a holiday).
RECORDED_WITHIN_DAYS = 4
SWITCHED_OFF = (
    "Claude's shadow rules are switched off: the operator turns them on (admin.mcp.shadow_rules in config.json) "
    "after watching the TA engine. Until then, study a rule with pine_signals and backtest it"
)
# A rule's fields, as RuleStore takes them.
FIELDS = ("name", "asset", "side", "bars", "script", "inputs", "alerts", "alert_calls", "confidence_plot", "indice")


def named(name: str) -> str:
    """A rule's name, "Claude · " first: the operator tells Claude's rules apart at a glance."""
    name = " ".join(str(name or "").split())
    if name.startswith(PREFIX.strip()):
        name = name[len(PREFIX.strip()):].strip()
    if not name:
        raise Refused("A rule needs a name")
    return PREFIX + name


def epic_of(asset: str) -> str:
    provider, _, key = str(asset or "").partition("/")
    if provider != "capital" or not key:
        raise Refused("A rule runs on the bars the Capital.com feed records: asset is capital/<EPIC>, as data_assets names it")
    return key


def bars_of(text: str) -> dict:
    text = str(text or "").strip()
    if text[:1] in ("R", "r"):
        return {"type": "range", "ticks": int(text[1:]) if text[1:].isdigit() else 0}
    return {"type": "time", "timeframe": text}


def previous_weekday(day: date) -> date:
    day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def _what_runs(rule: dict) -> tuple:
    return (
        rule["asset"], rule["side"], rule["bars"], (rule["script"]["id"], rule["script"]["version"]),
        sorted((entry["id"], repr(entry["value"])) for entry in rule["inputs"]), rule.get("history_days"),
    )


class ShadowRules:
    def __init__(self, research):
        self._research = research

    def check_switch(self) -> None:
        if not self._research.limits.shadow_rules:
            raise Refused(SWITCHED_OFF)

    @staticmethod
    def own(rule: dict) -> dict:
        """The rule, if it is one of Claude's that is not live."""
        if rule.get("made_by") != CLAUDE:
            raise Refused(f"{rule['name']!r} is the operator's rule: Claude changes only its own")
        if rule["mode"] == LIVE:
            raise Refused(f"{rule['name']!r} is live: the operator took it live, it is no longer Claude's to change")
        return rule

    def _check_room(self, rules: list[dict]) -> None:
        """A new rule of Claude's fits: at most admin.mcp.max_shadow_rules of its rules that are not live."""
        limit = self._research.limits.max_shadow_rules
        if sum(1 for rule in rules if rule.get("made_by") == CLAUDE and rule["mode"] != LIVE) >= limit:
            raise Refused(f"Claude keeps {limit} rules at most (admin.mcp.max_shadow_rules): rule_shadow_remove one first")

    def _switch(self, rule_id: str, mode: str, revision: str) -> dict:
        """The only change of mode Claude makes: between shadow and off."""
        if mode not in MODES:
            raise Refused("Claude's rules are in shadow or off: going live is the operator's, on the Signal rules page")
        return self._research.rules.set_mode(rule_id, mode, revision)

    # ── what is saved ──

    def fields(self, given: dict, before: dict | None) -> dict:
        """The rule as RuleStore takes it: what is given, and the rest as the rule has it."""
        research = self._research
        if before is None:
            missing = [key for key in ("name", "script_id", "asset", "indice", "alerts") if not given.get(key)]
            if missing:
                raise Refused(f"A new rule needs {', '.join(missing)}")
        base = before or {}
        if given.get("script_id") and given["script_id"] != (base.get("script") or {}).get("id"):
            # Another script: its inputs and alerts are its own, none of the former script's.
            script_id = given["script_id"]
            version = given.get("version") or research.scripts.script(script_id)["version"]
            inputs, alerts = {}, {}
        else:
            script_id = base["script"]["id"]
            version = given.get("version") or base["script"]["version"]
            inputs = {entry["id"]: entry for entry in base.get("inputs") or []}
            alerts = {entry["title"]: entry["action"] for entry in base.get("alerts") or []}
        for input_id, value in (given.get("inputs") or {}).items():
            inputs[input_id] = {**inputs.get(input_id, {}), "id": input_id, "value": value}
        alerts.update(given.get("alerts") or {})
        indice = given.get("indice") or base["indice"]
        allowed = research.config.allowed_indices()
        if given.get("indice") and allowed and indice not in allowed:
            raise Refused(f"The indice is one WATA trades: {', '.join(allowed)}")
        fields = {
            "name": named(given["name"]) if given.get("name") else base["name"],
            "asset": {"provider": "capital", "key": epic_of(given["asset"])} if given.get("asset") else base["asset"],
            "side": given.get("side") or base.get("side", "bid"),
            "bars": bars_of(given["bars"]) if given.get("bars") else base.get("bars", {"type": "time", "timeframe": "1"}),
            "script": {"id": script_id, "version": version},
            "inputs": list(inputs.values()),
            "alerts": [{"title": title, "action": action} for title, action in alerts.items()],
            "alert_calls": given.get("alert_calls") or base.get("alert_calls", "ignore"),
            "confidence_plot": base.get("confidence_plot"),
            "indice": indice,
        }
        # Its own history when it says, or had one; -1 gives it the engine's again.
        history_days = given.get("history_days", base.get("history_days"))
        if history_days is not None and history_days != ENGINE_HISTORY:
            fields["history_days"] = history_days
        # RuleStore's own checks, before a study is spent on a rule it would refuse.
        fields.update(
            asset=checked_asset(fields["asset"]), bars=checked_bars(fields["bars"]),
            inputs=checked_inputs(fields["inputs"]), alerts=checked_alerts(fields["alerts"]),
        )
        if "history_days" in fields:
            fields["history_days"] = checked_history_days(fields["history_days"])
        research.rules.pin(script_id, version)
        return fields

    # ── run once as the engine would ──

    async def measure(self, fields: dict) -> tuple[dict, str | None]:
        """The rule run on the last finished day of its epic: what it cost, and why it may not run, if so."""
        research = self._research
        if research.studies is None or research.queries is None:
            raise Refused("Pine studies, or the database, are not available here: a rule cannot be run first")
        epic = fields["asset"]["key"]
        instrument = await research.queries.capital_instrument(epic)
        if instrument is None or instrument.get("last_t") is None:
            raise Refused(f"The Capital.com feed has not recorded {epic}: a rule runs on an epic it records (data_assets)")
        today = research.today()
        last = TradingClock(research.timezone).day_of(instrument["last_t"])
        if (today - last).days > RECORDED_WITHIN_DAYS:
            raise Refused(f"The Capital.com feed last recorded {epic} on {last.isoformat()}: a rule runs on an epic it records now")
        day = last if last < today else previous_weekday(today)
        asked = {
            "script": dict(fields["script"]), "asset": f"capital/{epic}", "bars": bars_label(fields["bars"]),
            "side": fields["side"], "inputs": {entry["id"]: entry["value"] for entry in fields["inputs"]},
            "alerts": {entry["title"]: entry["action"] for entry in fields["alerts"]},
            "alert_calls": fields["alert_calls"], "indice": fields["indice"],
            "from": day.isoformat(), "to": day.isoformat(), "timezone": research.timezone,
        }
        if fields.get("history_days") is not None:
            # As the engine will run it: on its own days of history.
            asked["history_days"] = fields["history_days"]
        found = await research.studies.study("signals", asked)
        days = found.get("days") or []
        measured = {
            "day": day.isoformat(), "ran": bool(found.get("ok")),
            "most_bars": max((entry.get("bars") or 0 for entry in days), default=0),
            "most_milliseconds": max((entry.get("milliseconds") or 0 for entry in days), default=0),
            "warnings": found.get("warnings") or [],
        }
        if not found.get("ok"):
            error = found.get("error") or {}
            measured["error"] = error
            return measured, f"It does not run as the engine would run it: {error.get('message') or 'the runner failed'}"
        if measured["most_bars"] == 0:
            return measured, f"{epic} has no bar on {day.isoformat()} to run it on: try again after a trading day"
        most = research.limits.shadow_day_ms
        if measured["most_milliseconds"] > most:
            return measured, (
                f"A day of the engine takes it {measured['most_milliseconds']} ms, over {most} (admin.mcp.shadow_day_ms): "
                "the engine runs it again at each new bar. A lighter script, longer bars, or fewer days of history"
            )
        if measured["most_bars"] > MAX_DAY_BARS:
            return measured, f"A day of the engine is {measured['most_bars']} of its bars, over {MAX_DAY_BARS}: longer bars"
        return measured, None

    # ── the tools' work ──

    async def save(self, given: dict, rule_id: str = "", mode: str = SHADOW) -> dict:
        self.check_switch()
        if mode not in MODES:
            raise Refused("Claude's rules are in shadow or off: going live is the operator's, on the Signal rules page")
        research = self._research
        before = self.own(await run_in_threadpool(research.rules.rule, rule_id)) if rule_id else None
        if before is not None and not given:
            # A change of mode alone: the rule as it is, nothing checked again.
            fields = {key: before[key] for key in FIELDS}
            if "history_days" in before:
                fields["history_days"] = before["history_days"]
        else:
            fields = await run_in_threadpool(self.fields, given, before)
        if before is None:
            # Before a study is spent on it; again with the write, under the lock.
            self._check_room((await run_in_threadpool(research.rules.load))["rules"])
        measured = None
        if mode == SHADOW and (before is None or before["mode"] == OFF or _what_runs(before) != _what_runs(fields)):
            measured, why = await self.measure(fields)
            if why is not None:
                return {"saved": False, "why": why, "measured": measured}

        def write() -> dict:
            with research.scripts.lock:
                loaded = research.rules.load()
                if before is None:
                    self._check_room(loaded["rules"])
                    return research.rules.create(fields, loaded["revision"], mode=mode, made_by=CLAUDE)["rule"]
                current = self.own(RuleStore.find(loaded["rules"], rule_id))
                if current != before:
                    raise Refused(f"{current['name']!r} changed meanwhile: read it again (rule_list), then make the change")
                saved = research.rules.update(rule_id, fields, loaded["revision"]) if given else {
                    "rule": current, "revision": loaded["revision"]}
                if mode != current["mode"]:
                    saved = self._switch(rule_id, mode, saved["revision"])
                return saved["rule"]

        rule = await run_in_threadpool(write)
        engine = await research.queries.ta_engine_state()
        return {
            "saved": True, "id": rule["id"], "name": rule["name"], "mode": rule["mode"],
            "script": {"id": rule["script"]["id"], "version": rule["script"]["version"]},
            "measured": measured,
            "engine": {"state": engine.get("state"), "detail": engine.get("detail")} if engine else None,
            "note": "The TA engine runs it from its next tick: data_signals (source engine) lists its signals, and "
                    "rule_list its days traded on paper once each day is finished",
        }

    async def remove(self, rule_id: str) -> dict:
        self.check_switch()
        research = self._research

        def delete() -> dict:
            with research.scripts.lock:
                loaded = research.rules.load()
                rule = self.own(RuleStore.find(loaded["rules"], rule_id))
                research.rules.delete(rule_id, loaded["revision"])
                return rule

        rule = await run_in_threadpool(delete)
        return {"removed": rule["id"], "name": rule["name"]}

    async def set_reference(self, run_id: str) -> dict:
        self.check_switch()
        research = self._research
        service = research.service()
        rule_id = await run_in_threadpool(service.rule_of, run_id)
        if not rule_id:
            raise Refused("This run was not made from a signal rule: backtest_start one with source.from_rule, one of Claude's rules")
        self.own(await run_in_threadpool(research.rules.rule, rule_id))
        # As the admin's route does: in the event loop, where every other change of the references is made.
        paper = service.set_reference(run_id)
        return {"rule": rule_id, "reference": run_id, "paper": (paper.get("rules") or {}).get(rule_id)}
