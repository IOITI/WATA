"""
Pine scripts: the scripts of the Technical Analysis pages (indicators,
strategies, libraries), read and, through the study child and the store,
checked, run and saved.
"""

from datetime import date, datetime, timedelta

from starlette.concurrency import run_in_threadpool

from src.admin.mcp_server.budget import Refused, research_tool
from src.admin.mcp_server.facade import CLAUDE
from src.admin.mcp_server.shape import DEFAULT_PAGE, compacted, page, period, refuse_lockbox
from src.admin.ta_compare import compare, default_tolerance
from src.admin.ta_rules import rule_users
from src.backtest.clock import TradingClock
from src.research.study import MAX_STUDY_DAYS, bars_of

KINDS = ("", "indicator", "strategy", "library")
MAX_COMPARED_ROWS = 300


def _script_line(meta: dict, users: list[str]) -> dict:
    return {
        "id": meta["id"], "name": meta["name"], "kind": meta["kind"], "version": meta["version"],
        "updated_at": meta["updated_at"], "made_by": meta.get("made_by"),
        "description": (meta.get("description") or "")[:200],
        "imports": [entry["name"] for entry in meta.get("imports") or []],
        "used_by_rules": users,
    }


def register(server, research) -> None:
    calls = research.calls

    def loaded():
        rules = research.rules.load()["rules"] if research.rules is not None else []
        return research.scripts.scripts(), rules

    @research_tool(server, calls, name="pine_list", title="Pine scripts", read_only=True)
    async def pine_list(kind: str = "", made_by: str = "", offset: int = 0, limit: int = DEFAULT_PAGE) -> dict:
        """The Pine scripts WATA keeps (the Technical Analysis pages' Indicators), the last saved first: id, name,
        kind (indicator, strategy, library), current version, who made it (made_by "claude" for Claude's), the
        libraries it imports, and the signal rules that run it. made_by="claude" lists Claude's own."""
        if kind not in KINDS:
            raise Refused("kind is indicator, strategy or library (or empty for all)")
        scripts, rules = await run_in_threadpool(loaded)
        chosen = [
            _script_line(meta, rule_users(rules, meta["id"])) for meta in scripts
            if (not kind or meta["kind"] == kind) and (not made_by or meta.get("made_by") == made_by)
        ]
        return compacted(page(chosen, offset, limit))

    @research_tool(server, calls, name="pine_get", title="A Pine script's source", read_only=True, export=True)
    async def pine_get(script_id: str, version: int = 0) -> dict:
        """One script: its source (the current version, or ``version``), its versions (when, who saved them), the
        libraries it imports and the rules that run it. A long source is saved to a file on the Mac by Claude Code:
        read it there."""
        def read():
            meta = research.scripts.script(script_id)
            number = version or meta["version"]
            source = meta["source"] if number == meta["version"] else research.scripts.version(script_id, number)["source"]
            rules = research.rules.load()["rules"] if research.rules is not None else []
            return meta, number, source, rule_users(rules, script_id)

        meta, number, source, users = await run_in_threadpool(read)
        return compacted({
            **_script_line(meta, users),
            "description": meta.get("description") or "",
            "pine_version": meta.get("pine_version"),
            "library_name": meta.get("library_name"),
            "version": number,
            "current_version": meta["version"],
            "versions": [
                {"number": entry["number"], "saved_at": entry["saved_at"], "size": entry["size"],
                 "made_by": entry.get("made_by")}
                for entry in meta["versions"]
            ],
            "source": source,
        })


# ── studies: checked, run and replayed on the server ──

ALERT_CALLS = ("ignore", "message")


def _study_request(research, *, script_id: str, version: int, source: str, name: str, asset: str, bars: str, side: str,
                   inputs: dict, start_day: date, end_day: date | None, alerts: dict | None = None,
                   alert_calls: str = "ignore") -> dict:
    if bool(source) == bool(script_id):
        raise Refused("Name a saved script (script_id, version) or give a source not saved yet, one of them")
    first, last = period(start_day, end_day, MAX_STUDY_DAYS)
    refuse_lockbox(research.journal, first, last)
    if alert_calls not in ALERT_CALLS:
        raise Refused("alert_calls is ignore or message")
    request = {
        "asset": asset, "bars": bars, "side": side, "inputs": inputs or {}, "from": first.isoformat(),
        "to": last.isoformat(), "timezone": research.timezone, "alerts": alerts or {}, "alert_calls": alert_calls,
    }
    if source:
        request.update(source=source, name=name or "Draft")
    else:
        request["script"] = {"id": script_id, "version": version or None}
    return request


def _studies(research):
    if research.studies is None:
        raise Refused("Pine studies are not available here (the admin's MCP endpoint is off)")
    return research.studies


def register_studies(server, research) -> None:
    calls = research.calls
    seconds = research.limits.study_timeout_seconds + 25

    @research_tool(server, calls, name="pine_check", title="Check a Pine script", read_only=True, seconds=seconds)
    async def pine_check(source: str = "", script_id: str = "", version: int = 0, name: str = "") -> dict:
        """Compile a script (a source not saved yet, or a saved script_id) and run it once on made-up bars, without
        reading any data: where it fails (file, line, column; a library's own lines when the fault is in one), or its
        kind, its inputs (in_0, in_1... with their titles and defaults), its plots, the alerts it raised on those
        bars and PineTS's runtime warnings. A library is checked through an indicator that imports it."""
        if bool(source) == bool(script_id):
            raise Refused("Give a source, or a script_id, one of them")
        request = {"source": source, "name": name or "Draft"} if source else {"script": {"id": script_id, "version": version or None}}
        return compacted(await _studies(research).study("check", request))

    @research_tool(server, calls, name="pine_run", title="Run a Pine script on recorded bars", read_only=True,
                   seconds=seconds)
    async def pine_run(asset: str, start_day: date, end_day: date | None = None, bars: str = "1", side: str = "bid",
                       script_id: str = "", version: int = 0, source: str = "", name: str = "",
                       inputs: dict | None = None, warmup_days: int = 1, points: int = 300,
                       plots: list[str] | None = None) -> dict:
        """One continuous run of a script (saved, or a source not saved yet) on the bars WATA recorded of ``asset``
        (a ref of data_assets) over whole days, with ``warmup_days`` of bars before them: each plot summarised (na
        share, min, max, mean, first, last) and downsampled to ``points`` points, the alerts it raised (time, title,
        message), and PineTS's runtime warnings. bars: minutes (1, 5), seconds (10S, 45S) or range bars (R60).
        inputs by id: {"in_0": 9}. What the script shows, not what a rule would send: that is pine_signals."""
        request = _study_request(research, script_id=script_id, version=version, source=source, name=name, asset=asset,
                                 bars=bars, side=side, inputs=inputs or {}, start_day=start_day, end_day=end_day)
        request.update(warmup_days=warmup_days, points=points, plots=plots or [])
        return compacted(await _studies(research).study("run", request))

    @research_tool(server, calls, name="pine_signals", title="What a rule of the script would raise", read_only=True,
                   seconds=seconds)
    async def pine_signals(asset: str, start_day: date, alerts: dict, end_day: date | None = None, bars: str = "1",
                           side: str = "bid", script_id: str = "", version: int = 0, source: str = "", name: str = "",
                           inputs: dict | None = None, alert_calls: str = "ignore") -> dict:
        """The signals a signal rule of this script would have raised, day by day of the TA engine (from its daily
        restart, with its history_days of bars before), each alert mapped by its title as ``alerts`` says:
        {"Long": "long", "Short": "short", "Exit": "close-position"} (or ignore, message). Per day: bars, alerts,
        signals; the signals themselves (bar time, close, action). The runner's answers are shared with the
        backtests: the same days run again cost nothing."""
        request = _study_request(research, script_id=script_id, version=version, source=source, name=name, asset=asset,
                                 bars=bars, side=side, inputs=inputs or {}, start_day=start_day, end_day=end_day,
                                 alerts=alerts, alert_calls=alert_calls)
        return compacted(await _studies(research).study("signals", request))

    @research_tool(server, calls, name="pine_compare", title="A script's signals beside TradingView's", read_only=True,
                   seconds=seconds)
    async def pine_compare(asset: str, start_day: date, alerts: dict, end_day: date | None = None, bars: str = "1",
                           side: str = "bid", script_id: str = "", version: int = 0, source: str = "", name: str = "",
                           inputs: dict | None = None, indice: str = "us100", tolerance_seconds: int = 0) -> dict:
        """The signals a rule of this script would have raised (as pine_signals) set beside those WATA received
        from TradingView for ``indice`` on the same days, paired by action within tolerance_seconds (two bars by
        default, five minutes for range bars): paired, only the script's, only TradingView's, day by day, and the
        time between each pair. How close a script comes to the strategy TradingView runs."""
        request = _study_request(research, script_id=script_id, version=version, source=source, name=name, asset=asset,
                                 bars=bars, side=side, inputs=inputs or {}, start_day=start_day, end_day=end_day,
                                 alerts=alerts)
        request["indice"] = indice
        found = await _studies(research).study("signals", request)
        if not found.get("ok"):
            return compacted(found)
        tolerance = tolerance_seconds if 1 <= tolerance_seconds <= 3600 else default_tolerance(bars_of(bars))
        ours = [{**signal, "bar_time": datetime.fromisoformat(signal["bar_time"].replace("Z", "+00:00"))}
                for signal in found.get("signals") or []]
        first, last = date.fromisoformat(request["from"]), date.fromisoformat(request["to"])
        days, rows = [], []
        day = first
        while day <= last:
            theirs = await research.queries.ta_other_signals(indice, day)
            mine = [signal for signal in ours if signal["bar_time"].astimezone(TradingClock(research.timezone).zone).date() == day]
            compared = compare(mine, theirs, tolerance)
            days.append({"day": day.isoformat(), "paired": compared["paired"], "only_script": compared["only_wata"],
                         "only_tradingview": compared["only_tradingview"]})
            rows += [{"at": row["at"], "action": row["action"], "delta_seconds": row["delta_seconds"],
                      "script": bool(row["wata"]), "tradingview": bool(row["tradingview"])} for row in compared["rows"]]
            day += timedelta(days=1)
        return compacted({
            "tolerance_seconds": tolerance, "days": days,
            "paired": sum(entry["paired"] for entry in days), "only_script": sum(entry["only_script"] for entry in days),
            "only_tradingview": sum(entry["only_tradingview"] for entry in days),
            "rows": rows[:MAX_COMPARED_ROWS], "rows_total": len(rows), "signals_total": found.get("signals_total"),
        })

    @research_tool(server, calls, name="pine_save", title="Save a Pine script", read_only=False, seconds=seconds)
    async def pine_save(source: str, name: str = "", script_id: str = "", description: str = "") -> dict:
        """Save a script, checked first (pine_check): a new one (``name``), or a new version of one of Claude's own
        (``script_id``). The operator's scripts are never changed: save a copy under a new name. A script that does
        not compile is not saved: its diagnostics come back instead. Versions are kept; a rule pins one."""
        checked = await _studies(research).study("check", {"source": source, "name": name or "Draft"})
        if not checked.get("ok"):
            return compacted({"saved": False, "check": checked})
        scripts = research.scripts

        def save():
            if script_id:
                meta = scripts.script(script_id)
                if meta.get("made_by") != CLAUDE:
                    raise Refused(f"{meta['name']!r} is the operator's: save the change as a new script, under a new name")
                return scripts.save(script_id, meta["version"], source=source,
                                    description=description or None, made_by=CLAUDE)
            if not name:
                raise Refused("A new script needs a name")
            if scripts.count_made_by(CLAUDE) >= research.limits.max_scripts:
                raise Refused(f"Claude keeps {research.limits.max_scripts} scripts at most: pine_delete one first")
            return scripts.create(name, source, description, made_by=CLAUDE)

        saved = await run_in_threadpool(save)
        return compacted({
            "saved": True, "id": saved["id"], "name": saved["name"], "kind": saved["kind"], "version": saved["version"],
            "inputs": checked.get("inputs"), "plots": checked.get("plots"), "warnings": checked.get("warnings"),
        })

    @research_tool(server, calls, name="pine_delete", title="Remove one of Claude's scripts", read_only=False,
                   destructive=True, idempotent=False)
    async def pine_delete(script_id: str) -> dict:
        """One of Claude's own scripts moved to the trash: never one a signal rule runs or a script imports, never
        the operator's."""
        def delete():
            meta = research.scripts.script(script_id)
            if meta.get("made_by") != CLAUDE:
                raise Refused("Claude removes only its own scripts")
            research.scripts.delete(script_id, users=lambda found: research.rules.users_of(found["id"]))
            return meta

        meta = await run_in_threadpool(delete)
        return {"removed": meta["id"], "name": meta["name"]}
