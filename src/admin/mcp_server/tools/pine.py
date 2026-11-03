"""
Pine scripts: the scripts of the Technical Analysis pages (indicators,
strategies, libraries), read and, through the study child and the store,
checked, run and saved.
"""

import base64
import re
from datetime import date, datetime, time, timedelta

from starlette.concurrency import run_in_threadpool

from src.admin.mcp_server.budget import CONTEXT_CHARS, Pictured, Refused, compact, research_tool
from src.admin.mcp_server.facade import CLAUDE
from src.admin.errors import NotPossible
from src.admin.mcp_server.shape import DEFAULT_PAGE, compacted, page, period, refuse_lockbox
from src.admin.ta_compare import compare, default_tolerance
from src.admin.ta_rules import rule_users
from src.backtest.clock import TradingClock
from src.research.rule_check import combined
from src.research.study import MAX_STUDY_DAYS, bars_of

KINDS = ("", "indicator", "strategy", "library")
MAX_COMPARED_ROWS = 300
# The characters of a script's How it works pine_get gives (cut: true past them), beside its source.
HOW_SAID = 12_000
FITTED = ("Cut to the 32,000 characters an answer may have: ask for fewer plots (plots), fewer points, "
          "alert_titles, a lower max_alerts or fewer days")


def _thinned(points: list, count: int) -> list:
    """``count`` of ``points``, evenly spaced, with the first and the last."""
    if count >= len(points):
        return points
    if count <= 1:
        return points[:count]
    step = (len(points) - 1) / (count - 1)
    return [points[round(index * step)] for index in range(count)]


def fitted(answer, limit: int = CONTEXT_CHARS):
    """
    A run's answer within ``limit`` characters: its plots' points thinned
    first, then the alerts listed cut, as ``fitted`` says. The summaries,
    the counts and the warnings stay whole.
    """
    if not isinstance(answer, dict) or len(compact(answer)) <= limit:
        return answer
    plots = answer.get("plots") if isinstance(answer.get("plots"), dict) else {}
    alerts = answer.get("alerts") if isinstance(answer.get("alerts"), list) else []
    sampled = {name: plot for name, plot in plots.items() if isinstance(plot, dict) and isinstance(plot.get("points"), list)}
    most = max((len(plot["points"]) for plot in sampled.values()), default=0)

    def tried(points: int, listed: int) -> dict:
        trial = {**answer, "plots": {**plots, **{name: {**plot, "points": _thinned(plot["points"], points)}
                                                   for name, plot in sampled.items()}}}
        if "alerts" in answer:
            trial["alerts"] = alerts[:listed]
        trial["fitted"] = {"points_per_plot": points, "alerts_listed": min(listed, len(alerts)), "said": FITTED}
        return trial

    def largest(fits, high: int) -> int:
        low = 0
        while low < high:
            middle = (low + high + 1) // 2
            if fits(middle):
                low = middle
            else:
                high = middle - 1
        return low

    def fits(trial: dict) -> bool:
        return len(compact(trial)) <= limit

    # The most points that fit with every alert listed; with none, the most alerts that fit.
    if fits(tried(0, len(alerts))):
        return tried(largest(lambda points: fits(tried(points, len(alerts))), most), len(alerts))
    return tried(0, largest(lambda listed: fits(tried(0, listed)), len(alerts)))


def _script_line(meta: dict, users: list[str], origin: dict | None = None, details: dict | None = None) -> dict:
    line = {
        "id": meta["id"], "name": meta["name"], "kind": meta["kind"], "version": meta["version"],
        "updated_at": meta["updated_at"], "made_by": meta.get("made_by"),
        "description": (meta.get("description") or "")[:200],
        "imports": [entry["name"] for entry in meta.get("imports") or []],
        "used_by_rules": users,
    }
    if details is not None:
        # What the Indicators page says of it: among the operator's favourites, its family.
        line = {**line, "favourite": details["favourite"], "family": details["family"]}
    # Taken from LuxAlgo's Library, or derived from one of its indicators: its licence goes with it.
    return {**line, "origin": origin} if origin else line


def register(server, research) -> None:
    calls = research.calls

    def loaded():
        rules = research.rules.load()["rules"] if research.rules is not None else []
        scripts = research.scripts.scripts()
        return (scripts, rules, {meta["id"]: research.origin_of(meta["id"], meta["version"]) for meta in scripts},
                {meta["id"]: research.details_of(meta["id"]) for meta in scripts})

    @research_tool(server, calls, name="pine_list", title="Pine scripts", read_only=True)
    async def pine_list(kind: str = "", made_by: str = "", family: str = "", offset: int = 0, limit: int = DEFAULT_PAGE) -> dict:
        """The Pine scripts WATA keeps (the Technical Analysis pages' Indicators), the last saved first: id, name,
        kind (indicator, strategy, library), current version, who made it (made_by "claude" for Claude's), the
        libraries it imports, the signal rules that run it, among the operator's favourites or not, its family, and
        where it came from when it was taken from LuxAlgo's Library or made from one of its indicators (origin, with
        its licence). made_by="claude" lists Claude's own; family lists one family's (any case)."""
        if kind not in KINDS:
            raise Refused("kind is indicator, strategy or library (or empty for all)")
        scripts, rules, origins, details = await run_in_threadpool(loaded)
        chosen = [
            _script_line(meta, rule_users(rules, meta["id"]), origins.get(meta["id"]), details.get(meta["id"]))
            for meta in scripts
            if (not kind or meta["kind"] == kind) and (not made_by or meta.get("made_by") == made_by)
            and (not family or ((details.get(meta["id"]) or {}).get("family") or "").casefold() == family.casefold())
        ]
        return compacted(page(chosen, offset, limit))

    @research_tool(server, calls, name="pine_get", title="A Pine script's source", read_only=True, export=True)
    async def pine_get(script_id: str, version: int = 0) -> dict:
        """One script: its source (the current version, or ``version``), its versions (when, who saved them), the
        libraries it imports, the rules that run it, where it came from (origin: LuxAlgo's Library, its licence), its
        family, and its How it works (how_it_works: the Markdown the Indicators page shows, the revision a change of it
        starts from, who wrote it). A long source is saved to a file on the Mac by Claude Code: read it there."""
        def read():
            meta = research.scripts.script(script_id)
            number = version or meta["version"]
            source = meta["source"] if number == meta["version"] else research.scripts.version(script_id, number)["source"]
            rules = research.rules.load()["rules"] if research.rules is not None else []
            found = research.details_of(script_id)
            how = research.details.how(script_id) if found is not None else None
            return (meta, number, source, rule_users(rules, script_id), research.origin_of(script_id, number, lambda: source),
                    found, how)

        meta, number, source, users, origin, details, how = await run_in_threadpool(read)
        checked = None
        if research.rule_checks is not None and meta.get("kind") != "library":
            try:
                found = await research.rule_checks.of_version(script_id, number)
                checked = said_rule_check(found) if found else None
            except NotPossible:
                checked = None
        said_how = None
        if details is not None:
            revision = (details["how"] or {}).get("revision", 0)
            said_how = ({"text": how["text"][:HOW_SAID], "cut": len(how["text"]) > HOW_SAID, "revision": how["revision"],
                         "by": how.get("by"), "at": how.get("at"), "from": how.get("from")}
                        if how is not None else {"text": "", "revision": revision})
        return compacted({
            **_script_line(meta, users, origin, details),
            # Whether this version can become a signal rule as it is: pine_rule_check says more, and tests it.
            "rule_check": checked,
            **({"how_it_works": said_how, "picture": details["picture"]} if details is not None else {}),
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
# What a rule check says of each finding's places, at most.
RULE_CHECK_PLACES = 5


def said_rule_check(report: dict) -> dict:
    """A rule check as Claude reads it: its verdict, each finding with where and what to change, the alerts to add."""
    tested = report.get("tested") or None
    return {
        "verdict": report.get("verdict"),
        "findings": [
            {**{key: finding.get(key) for key in ("id", "area", "level", "message", "fix", "excerpt", "through")
                if finding.get(key) is not None},
             "places": [f"{place.get('file') or 'script'}:{place.get('line')}" for place in (finding.get("places") or [])[:RULE_CHECK_PLACES]]}
            for finding in report.get("findings") or []
        ],
        "alerts": [alert.get("title") for alert in report.get("alerts") or []],
        "candidates": [{key: candidate.get(key) for key in ("title", "condition", "call", "line")}
                       for candidate in report.get("candidates") or []],
        "whole_because": report.get("whole_because"),
        "tested": {key: tested.get(key) for key in ("settings", "at", "spoken", "days", "checkpoints", "history", "alerts")
                   if tested.get(key) is not None} if tested else None,
    }


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


def _bars_word(bars: dict) -> str:
    """A rule's bars in the one word a study is asked them by: R60, or a timeframe."""
    return f"R{bars['ticks']}" if bars["type"] == "range" else bars["timeframe"]


def _studies(research):
    if research.studies is None:
        raise Refused("Pine studies are not available here (the admin's MCP endpoint is off)")
    return research.studies


# ── a day as chart_view reads it ──

CHART_VIEW = (
    "One day of an asset as a backtest ran it, with a script applied: the TA engine's day holding it (from its "
    "restart, with history_days of bars before), its bars gathered into ``rows`` rows at most (20 to 400) of the "
    "trading hours, or of ``start`` to ``end`` (HH:MM, trading timezone) to zoom; each row its open, high, low and "
    "close, the script's plots (``plots`` by title, 8 at most; its first four by default), the alerts it raised and "
    "a run's trades. Then the day's prices, levels, swings and kind; for each alert's title, what price did 5 to 60 "
    "minutes after (basis points, signed by its direction when it is an entry) and each alert listed; and a picture "
    "of the hours shown (picture=false: none). The script's own drawings (labels, boxes) are not in it. run_id: a "
    "finished run's own script, inputs, bars and alerts, and its trades and untraded signals of that day; any argument "
    "given replaces the run's (inputs by id). Without run_id: asset, bars and side, and a script (script_id, or a "
    "source not saved yet) or none for the prices alone. Look at a day before changing a strategy for it; zoom on "
    "the hours it lost."
)
_CLOCK = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
# The fates of a run's signals that made no trade, listed with why.
NOT_TRADED = ("refused", "failed", "unpriced")


def _hours_of(settings: dict, timezone: str) -> tuple[str, int, int]:
    """A run's trading hours and the timezone they are read in, as its settings say them."""
    zone = settings.get("market_timezone")
    zone = settings.get("timezone") or timezone if zone in (None, "trading") else zone
    return zone, int(settings.get("trading_start_hour", 8)), int(settings.get("trading_end_hour", 22))


def _shown_window(day: date, hours: tuple[str, int, int], timezone: str, start: str, end: str) -> list[int]:
    """The hours chart_view shows: ``start`` to ``end`` in the trading timezone, or the day's trading hours."""
    for said in (start, end):
        if said and not _CLOCK.match(said):
            raise Refused("start and end are HH:MM, in the trading timezone")
    zone, start_hour, end_hour = hours
    market = TradingClock(zone)
    begin = market.epoch(day, time(start_hour))
    finish = market.day_bounds(day)[1] if end_hour >= 24 else market.epoch(day, time(end_hour))
    local = TradingClock(timezone)
    if start:
        begin = local.epoch(day, time(int(start[:2]), int(start[3:])))
    if end:
        finish = local.epoch(day, time(int(end[:2]), int(end[3:])))
    if finish <= begin:
        raise Refused("end comes after start, on the day asked")
    return [int(begin), int(finish)]


def register_studies(server, research) -> None:
    calls = research.calls
    # A study may outlast a call: these tools answer with a job then (job_wait).
    jobs = research.jobs

    @research_tool(server, calls, name="pine_rule_check", title="Whether a script can become a signal rule",
                   read_only=True, jobs=jobs)
    async def pine_rule_check(script_id: str = "", version: int = 0, source: str = "", name: str = "", test: bool = True,
                              asset: str = "capital/US100", bars: str = "1", side: str = "bid", inputs: dict | None = None,
                              history_days: int | None = None, start_day: date | None = None,
                              end_day: date | None = None) -> dict:
        """Whether a script (saved, or a source not saved yet) can become a signal rule as it is. Its source: the
        alerts a rule maps, the events it only draws (candidates: the alertcondition() each would be), what may make
        its signals differ live and in a backtest, followed to its alerts, and what WATA serves otherwise, each at its
        line with what to change (fix). With ``test``, its run test on recorded bars: each engine day run as a backtest
        runs it, then ended at chosen bars as live had them, compared; how often each alert fires; one more day of
        history. verdict: blocked, needs_alerts, repaints, may_repaint, to_change or ready. Days: start_day and
        end_day, or the two newest finished weekdays outside the lock box. A saved version's test is kept for its
        page, beside the operator's."""
        if bool(source) == bool(script_id):
            raise Refused("Name a saved script (script_id, version) or give a source not saved yet, one of them")
        if not test:
            if script_id:
                if research.rule_checks is None:
                    raise Refused("The rule check is not available here")
                report = await research.rule_checks.of_version(script_id, version or None)
                if report is None:
                    raise Refused("A library: it is checked through the scripts that import it")
                return compacted(said_rule_check(report))
            static = (await _studies(research).study("check", {"source": source, "name": name or "Draft"})).get("rule_check")
            if not static:
                raise Refused("The checker is not built here (npm run build:checker)")
            check = static["check"]
            return compacted(said_rule_check({**check, "alert_calls": check.get("alertCalls"), "whole_because": check.get("wholeBecause"),
                                              **combined(static, None)}))
        request = {"asset": asset, "bars": bars, "side": side, "inputs": inputs or {}, "history_days": history_days}
        if start_day is not None:
            first, last = period(start_day, end_day, MAX_STUDY_DAYS)
            refuse_lockbox(research.journal, first, last)
            request.update({"from": first.isoformat(), "to": last.isoformat(), "timezone": research.timezone})
        else:
            box = research.journal.lockbox()
            request["avoid"] = [[box["from"].isoformat(), box["to"].isoformat() if box["to"] else None]] if box else []
        if source:
            request.update(source=source, name=name or "Draft")
        else:
            request["script"] = {"id": script_id, "version": version or None}
        answer = await _studies(research).study("rule", request)
        if not answer.get("ok"):
            return compacted(answer)
        if script_id and research.rule_checks is not None:
            await research.rule_checks.keep_answer(script_id, version or None, answer)
        check = (answer.get("static") or {}).get("check") or {}
        return compacted(said_rule_check({**check, "whole_because": check.get("wholeBecause"), "findings": answer["findings"],
                                          "verdict": answer["verdict"], "tested": answer.get("tested")}))

    @research_tool(server, calls, name="chart_view", title="A day's chart with a script applied", read_only=True,
                   jobs=jobs, description=CHART_VIEW)
    async def chart_view(day: date, run_id: str = "", asset: str = "", bars: str = "", side: str = "",
                         script_id: str = "", version: int = 0, source: str = "", name: str = "",
                         inputs: dict | None = None, alerts: dict | None = None, history_days: int | None = None,
                         start: str = "", end: str = "", rows: int = 180, plots: list[str] | None = None,
                         picture: bool = True) -> dict | Pictured:
        if script_id and source:
            raise Refused("Name a saved script (script_id, version) or give a source not saved yet, not both")
        refuse_lockbox(research.journal, day, day)
        if plots is not None and len(plots) > 8:
            raise Refused("8 plots at most")
        request = {"day": day.isoformat(), "timezone": research.timezone, "rows": rows, "picture": picture,
                   "plots": plots or []}
        notes, mapping, chart, given_inputs = [], alerts, None, dict(inputs or {})
        hours = research.config.hours() or (research.timezone, 8, 22)
        if run_id:
            service = research.service()
            parts = await run_in_threadpool(service.day_parts, run_id, day)
            trades = await run_in_threadpool(service.trades, run_id, day)
            signals = await run_in_threadpool(service.signals, run_id, day)
            line = next((entry for entry in await run_in_threadpool(service.days, run_id)
                         if entry.get("day") == day.isoformat()), {})
            hours = _hours_of(parts["settings"], research.timezone)
            chart = parts["chart"]
            fates: dict[str, int] = {}
            for signal in signals:
                fates[signal.get("fate")] = fates.get(signal.get("fate"), 0) + 1
            request.update(engine=[parts["begin"], parts["end"]], trades=trades, regime=line.get("regime"),
                           signals_by_fate=fates or None,
                           signals=[{"at": signal.get("decided_at") or signal.get("arrived_at"),
                                     **{key: signal.get(key) for key in ("action", "fate", "rule")},
                                     "reason": (signal.get("reason") or "")[:200] or None}
                                    for signal in signals if signal.get("fate") in NOT_TRADED])
            request["history_days"] = parts["history_days"] if history_days is None else history_days
            if mapping is None and parts.get("alerts"):
                mapping = {entry["title"]: entry["action"] for entry in parts["alerts"]
                           if isinstance(entry, dict) and entry.get("title")}
            notes += parts["notes"]
            if not (script_id or source) and parts["script"] is not None:
                own = parts["script"]
                kept = [dict(entry) for entry in parts["inputs"] or []] if parts["inputs_version"] == own["version"] else []
                if parts["inputs"] and not kept:
                    notes.append(f"The run's inputs were set for version {parts['inputs_version']} of its script: "
                                 f"version {own['version']} is shown with its own.")
                for input_id, value in given_inputs.items():
                    found = next((entry for entry in kept if entry.get("id") == input_id), None)
                    if found is None:
                        kept.append({"id": input_id, "value": value})
                    else:
                        found["value"] = value
                request["parts"] = {"script": own, "libraries": parts["libraries"], "inputs": kept}
            elif parts["script"] is None and not (script_id or source):
                notes.append("TradingView's alerts made this run's signals: its trades and signals on the prices alone.")
        elif history_days is not None:
            request["history_days"] = history_days
        if chart is None and not asset:
            raise Refused("Name the asset (a ref of data_assets), or a run whose day it is (run_id)")
        chart = {
            "asset": ({"provider": asset.partition("/")[0], "key": asset.partition("/")[2]} if asset else chart["asset"]),
            "side": side or (chart or {}).get("side") or "bid",
            "bars": bars_of(bars) if bars else (chart or {}).get("bars") or bars_of("1"),
        }
        if "parts" in request:
            request["parts"]["chart"] = chart
        else:
            request.update(asset=f"{chart['asset']['provider']}/{chart['asset']['key']}", side=chart["side"],
                           bars=_bars_word(chart["bars"]), inputs=given_inputs)
            if source:
                request.update(source=source, name=name or "Draft")
            elif script_id:
                request["script"] = {"id": script_id, "version": version or None}
        request.update(alerts=mapping or {}, view=_shown_window(day, hours, research.timezone, start, end),
                       hours={"zone": hours[0], "start_hour": hours[1], "end_hour": hours[2]})
        answer = await _studies(research).study("view", request)
        if not answer.get("ok"):
            return compacted(answer)
        drawn = answer.pop("picture", None)
        if notes:
            answer["notes"] = notes
        if drawn:
            answer["picture"] = {key: drawn[key] for key in ("width", "height", "plots", "title")}
            return Pictured(compacted(answer), base64.b64decode(drawn["png"]))
        return compacted(answer)

    @research_tool(server, calls, name="pine_check", title="Check a Pine script", read_only=True, jobs=jobs)
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
                   jobs=jobs)
    async def pine_run(asset: str, start_day: date, end_day: date | None = None, bars: str = "1", side: str = "bid",
                       script_id: str = "", version: int = 0, source: str = "", name: str = "",
                       inputs: dict | None = None, warmup_days: int = 1, points: int = 300,
                       plots: list[str] | None = None, alert_titles: list[str] | None = None,
                       max_alerts: int = 100) -> dict:
        """One continuous run of a script (saved, or a source not saved yet) on the bars WATA recorded of ``asset``
        (a ref of data_assets) over whole days, with ``warmup_days`` of bars before them: each plot summarised (na
        share, min, max, mean, first, last) and downsampled to ``points`` points, the alerts it raised counted by
        title, and listed (time, title, message): only ``alert_titles`` when given, ``max_alerts`` at most (0 lists
        none, 500 at most), and PineTS's runtime warnings. bars: minutes (1, 5), seconds (10S, 45S) or range bars
        (R60). inputs by id: {"in_0": 9}. What the script shows, not what a rule would send: that is pine_signals.
        An answer over 32,000 characters has fewer points, then fewer alerts listed, as its ``fitted`` says."""
        request = _study_request(research, script_id=script_id, version=version, source=source, name=name, asset=asset,
                                 bars=bars, side=side, inputs=inputs or {}, start_day=start_day, end_day=end_day)
        request.update(warmup_days=warmup_days, points=points, plots=plots or [], alert_titles=alert_titles or [],
                       max_alerts=max_alerts)
        return fitted(compacted(await _studies(research).study("run", request)))

    @research_tool(server, calls, name="pine_signals", title="What a rule of the script would raise", read_only=True,
                   jobs=jobs)
    async def pine_signals(asset: str, start_day: date, alerts: dict, end_day: date | None = None, bars: str = "1",
                           side: str = "bid", script_id: str = "", version: int = 0, source: str = "", name: str = "",
                           inputs: dict | None = None, alert_calls: str = "ignore", history_days: int | None = None) -> dict:
        """The signals a signal rule of this script would have raised, day by day of the TA engine (from its daily
        restart, with its history_days of bars before: the engine's, or 0 to 30 as a rule's own), each alert mapped
        by its title as ``alerts`` says: {"Long": "long", "Short": "short", "Exit": "close-position"} (or ignore,
        message). Per day: bars, alerts, signals, the runner's time; the signals themselves (bar time, close,
        action). The runner's answers are shared with the backtests: the same days run again cost nothing."""
        request = _study_request(research, script_id=script_id, version=version, source=source, name=name, asset=asset,
                                 bars=bars, side=side, inputs=inputs or {}, start_day=start_day, end_day=end_day,
                                 alerts=alerts, alert_calls=alert_calls)
        if history_days is not None:
            request["history_days"] = history_days
        return compacted(await _studies(research).study("signals", request))

    @research_tool(server, calls, name="pine_compare", title="A script's signals beside TradingView's", read_only=True,
                   jobs=jobs)
    async def pine_compare(asset: str, start_day: date, alerts: dict, end_day: date | None = None, bars: str = "1",
                           side: str = "bid", script_id: str = "", version: int = 0, source: str = "", name: str = "",
                           inputs: dict | None = None, indice: str = "us100", tolerance_seconds: int = 0,
                           rows: int = 50) -> dict:
        """The signals a rule of this script would have raised (as pine_signals) set beside those WATA received
        from TradingView for ``indice`` on the same days, paired by action within tolerance_seconds (two bars by
        default, five minutes for range bars): paired, only the script's, only TradingView's, day by day, and the
        time between each pair (``rows`` of them, 0 for the days and totals alone, 300 at most). How close a script
        comes to the strategy TradingView runs."""
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
        days, pairs = [], []
        day = first
        while day <= last:
            theirs = await research.queries.ta_other_signals(indice, day)
            mine = [signal for signal in ours if signal["bar_time"].astimezone(TradingClock(research.timezone).zone).date() == day]
            compared = compare(mine, theirs, tolerance)
            days.append({"day": day.isoformat(), "paired": compared["paired"], "only_script": compared["only_wata"],
                         "only_tradingview": compared["only_tradingview"]})
            pairs += [{"at": row["at"], "action": row["action"], "delta_seconds": row["delta_seconds"],
                       "script": bool(row["wata"]), "tradingview": bool(row["tradingview"])} for row in compared["rows"]]
            day += timedelta(days=1)
        return compacted({
            "tolerance_seconds": tolerance, "days": days,
            "paired": sum(entry["paired"] for entry in days), "only_script": sum(entry["only_script"] for entry in days),
            "only_tradingview": sum(entry["only_tradingview"] for entry in days),
            "rows": pairs[:max(0, min(rows, MAX_COMPARED_ROWS))], "rows_total": len(pairs),
            "signals_total": found.get("signals_total"),
        })

    @research_tool(server, calls, name="pine_save", title="Save a Pine script", read_only=False, jobs=jobs)
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
        if research.rule_checks is not None and saved["kind"] != "library":
            # Its rule check made in the background, as for a version the operator saves.
            research.rule_checks.noticed_version(saved["id"], saved["version"])
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
