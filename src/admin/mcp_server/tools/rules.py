"""
Signal rules: what the TA engine runs (each rule a script on an asset's bars,
its alerts mapped to signals), live or in shadow, and each shadow rule's days
traded on paper. Read here; Claude's own shadow rules are made through
shadow.py, once the operator switched them on. Going live is the operator's
alone.
"""

from datetime import date

from starlette.concurrency import run_in_threadpool

from src.admin.mcp_server.budget import research_tool
from src.admin.mcp_server.shadow import ShadowRules
from src.admin.mcp_server.shape import compacted
from src.admin.ta_compare import compare, default_tolerance
from src.admin.ta_rules import bars_label


def rule_line(rule: dict, scripts_by_id: dict, state: dict | None, paper: dict | None) -> dict:
    script = scripts_by_id.get(rule["script"]["id"]) or {}
    return {
        "id": rule["id"], "name": rule["name"], "mode": rule["mode"], "made_by": rule.get("made_by"),
        "indice": rule["indice"], "asset": f"{rule['asset']['provider']}/{rule['asset']['key']}", "side": rule["side"],
        "bars": bars_label(rule["bars"]),
        "script": {"id": rule["script"]["id"], "version": rule["script"]["version"], "name": script.get("name"),
                   "latest_version": script.get("version")},
        "inputs": {entry["id"]: entry.get("value") for entry in rule.get("inputs") or []},
        "alerts": {entry["title"]: entry["action"] for entry in rule.get("alerts") or []},
        "updated_at": rule.get("updated_at"), "mode_changed_at": rule.get("mode_changed_at"),
        "engine": {key: state.get(key) for key in ("state", "detail", "last_bar_time", "last_alert_at", "updated_at")}
        if state else None,
        "paper": paper,
    }


def register(server, research) -> None:
    calls = research.calls

    @research_tool(server, calls, name="rule_list", title="Signal rules", read_only=True)
    async def rule_list() -> dict:
        """Every signal rule the TA engine runs: its mode (live sends signals to the trader, shadow only journals
        them, off), who made it (made_by "claude" for Claude's), the script version it pins, its asset and bars, its
        inputs, what each alert becomes, how the engine runs it now, and for a shadow rule its days traded on paper
        beside the daily range (5 % to 95 %) of its reference run."""
        def read():
            paper = research.backtests.paper() if research.backtests is not None else {}
            return research.rules.load()["rules"], {meta["id"]: meta for meta in research.scripts.scripts()}, paper

        rules, scripts_by_id, paper = await run_in_threadpool(read)
        states = await research.queries.ta_rule_states()
        papers = paper.get("rules") or {}
        return compacted({
            "rules": [rule_line(rule, scripts_by_id, states.get(rule["id"]), papers.get(rule["id"])) for rule in rules],
            "shadow_rules_switch": research.limits.shadow_rules,
        })

    @research_tool(server, calls, name="rule_compare", title="A rule's signals beside TradingView's", read_only=True)
    async def rule_compare(rule_id: str, day: date, tolerance_seconds: int = 0) -> dict:
        """A rule's signals of a day (what the TA engine raised, live or shadow) beside those the trader received
        for its indice from TradingView, paired by action within tolerance_seconds (default: two bars of the rule,
        five minutes for range bars): paired, WATA's only, TradingView's only, and the time between each pair."""
        rule = await run_in_threadpool(research.rules.rule, rule_id)
        wata = await research.queries.ta_signals(start=day, end=day, rule_id=rule_id, limit=2000)
        others = await research.queries.ta_other_signals(rule["indice"], day)
        tolerance = tolerance_seconds if 1 <= tolerance_seconds <= 3600 else default_tolerance(rule["bars"])
        found = compare(wata, others, tolerance)
        return compacted({
            "day": day.isoformat(), "indice": rule["indice"], "tolerance_seconds": found["tolerance_seconds"],
            "paired": found["paired"], "only_wata": found["only_wata"], "only_tradingview": found["only_tradingview"],
            "rows": [
                {"at": row["at"], "action": row["action"], "delta_seconds": row["delta_seconds"],
                 "wata": bool(row["wata"]), "tradingview": bool(row["tradingview"])}
                for row in found["rows"]
            ],
        })


SAVE_DESCRIPTION = """Make one of Claude's own shadow rules, or change one (rule_id): the TA engine runs it on the bars
the Capital.com feed records and journals its signals, never sends them; each finished day is traded on paper
(rule_list shows those days beside its reference run). A new rule needs name (Claude · is put first), script_id (an
indicator or strategy; version: its current one by default), asset (capital/<EPIC>), indice (us100...) and alerts by
title ({"Long": "long", "Short": "short", "Exit": "close-position"}, or ignore, message); bars 1, 5, 15, 60 (minutes)
or R<ticks>, side bid or ask, inputs by id ({"in_0": 9}), alert_calls ignore or message. A change sets only what is
given: inputs and alerts given are set by id and title, the others kept (all dropped for another script). mode shadow
or off. Before it runs, it is run as the engine would on the last finished day of its epic: it is saved only if it ran
without failing, each engine day in at most 2 s and on at most 20,000 bars (what comes back is its bars and time,
no signal). Going live is the operator's alone, on the Signal rules page. Only once the operator switched Claude's
shadow rules on."""


def register_shadow(server, research) -> None:
    calls = research.calls
    shadow = ShadowRules(research)
    seconds = research.limits.study_timeout_seconds + 25

    @research_tool(server, calls, name="rule_shadow_save", title="Make or change one of Claude's shadow rules",
                   read_only=False, seconds=seconds, description=SAVE_DESCRIPTION)
    async def rule_shadow_save(rule_id: str = "", name: str = "", script_id: str = "", version: int = 0,
                               asset: str = "", indice: str = "", alerts: dict | None = None, bars: str = "",
                               side: str = "", inputs: dict | None = None, alert_calls: str = "",
                               mode: str = "shadow") -> dict:
        given = {"name": name, "script_id": script_id, "version": version, "asset": asset, "indice": indice,
                 "alerts": alerts, "bars": bars, "side": side, "inputs": inputs, "alert_calls": alert_calls}
        return compacted(await shadow.save({key: value for key, value in given.items() if value}, rule_id, mode))

    @research_tool(server, calls, name="rule_shadow_remove", title="Delete one of Claude's shadow rules", read_only=False,
                   destructive=True)
    async def rule_shadow_remove(rule_id: str) -> dict:
        """Delete one of Claude's own rules that is not live: the TA engine stops running it at its next tick. Its
        signals stay in the journal, its paper days until they age out. Never the operator's rules, never a live one.
        Only once the operator switched Claude's shadow rules on."""
        return await shadow.remove(rule_id)

    @research_tool(server, calls, name="rule_set_reference", title="Set the reference run of one of Claude's rules",
                   read_only=False, idempotent=True)
    async def rule_set_reference(run_id: str) -> dict:
        """Make a finished run the reference of the rule it was made from (one of Claude's own: backtest_start with
        source.from_rule {"id": <rule id>}): the rule's paper days are then set beside that run's daily range, 5 % to
        95 %, and a day outside it is marked. Only once the operator switched Claude's shadow rules on."""
        return compacted(await shadow.set_reference(run_id))
