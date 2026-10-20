"""
Backtests: what can be asked, the runs and what they found, read in parts;
and, for Claude's experiments, runs and sweeps asked for, waited on and
removed (the writing tools come with the experiment journal).
"""

import asyncio
from datetime import date, datetime

from starlette.concurrency import run_in_threadpool

from src.admin import backtest_requests
from src.admin.mcp_server import guards
from src.admin.mcp_server.budget import Refused, research_tool
from src.admin.mcp_server.facade import CLAUDE
from src.admin.mcp_server.gate import current_caller
from src.admin.mcp_server.shape import DEFAULT_PAGE, compacted, csv_text, page
from src.research.requests import body_of, neighbour_values, trials_of, value_of

PARTS = ("summary", "edge", "breakdowns", "baseline", "sweep", "reconciliation", "settings")
TRADE_ORDERS = ("time", "worst", "best")
FATES = ("executed", "refused", "failed", "unpriced", "closed", "ignored", "not_a_signal")
FORMATS = ("json", "csv")
# The figures a run or a sweep's combination is told by in a list.
HEADLINE_FIGURES = ("total_percent", "trades", "win_rate", "worst_drawdown", "profit_factor", "expectancy", "sample")
MAX_COMPARED = 4
# The turbo model is measured again by Claude only when it is older than this.
MODEL_FRESH_DAYS = 7


def _format(value: str) -> str:
    if value not in FORMATS:
        raise Refused("format is json or csv")
    return value


def _source_line(source: dict) -> dict:
    """What the signals of a run are, in a few fields."""
    kind = source.get("kind")
    if kind != "pine":
        return {"kind": kind, "signals": source.get("signals"), "rule": source.get("rule")}
    rule = source.get("rule") or {}
    return {
        "kind": kind,
        "script": {**(rule.get("script") or {}), "name": rule.get("name")},
        "libraries": [{"name": entry.get("name"), "version": entry.get("version")} for entry in rule.get("libraries") or []],
        "asset": rule.get("asset"), "side": rule.get("side"), "bars": rule.get("bars"),
        "inputs": {entry["id"]: entry.get("value") for entry in rule.get("inputs") or []},
        "alerts": {entry["title"]: entry["action"] for entry in rule.get("alerts") or []},
        "alert_calls": rule.get("alert_calls"), "confidence_plot": rule.get("confidence_plot"),
        "from_rule": source.get("from_rule"),
    }


def _run_line(run: dict) -> dict:
    """A run in a list: what it was asked, its state, and its headline."""
    status = run.get("status") or {}
    source = run.get("source") or {}
    return {
        "id": run["id"], "kind": run.get("kind"), "name": run.get("name"), "from": run.get("from"), "to": run.get("to"),
        "source": {"kind": source.get("kind"), "script": source.get("script")},
        "turbos": run.get("turbos"), "state": status.get("state"), "error": (status.get("error") or "")[:300] or None,
        "position": run.get("position"), "headline": run.get("headline"), "stale": run.get("stale"),
        "made_by": run.get("made_by"), "experiment": run.get("experiment"), "stress_of": run.get("stress_of"),
        "tags": run.get("tags") or [],
    }


def _figures_line(figures: dict | None) -> dict | None:
    if not figures:
        return None
    line = {name: figures.get(name) for name in HEADLINE_FIGURES}
    risk = figures.get("risk") or {}
    line["sharpe"] = risk.get("sharpe")
    return line


def _without_curves(value):
    """A summary without its long daily curves: the days tool reads them."""
    if isinstance(value, dict):
        return {key: _without_curves(item) for key, item in value.items() if key not in ("curve", "cells_curves")}
    if isinstance(value, list):
        return [_without_curves(item) for item in value]
    return value


def _summary_part(run: dict) -> dict:
    request = run.get("request") or {}
    settings = run.get("settings") or {}
    summary = run.get("summary") or {}
    curve = summary.get("curve") or []
    answer = {
        **_run_line(run),
        "asked": {
            "indice": request.get("indice"), "source": _source_line(request.get("source") or {}),
            "chart": request.get("chart"), "changes": request.get("changes") or {},
            "baseline": request.get("baseline"), "sweep": request.get("sweep"), "lockbox": request.get("lockbox"),
        },
        "settings_changed": settings.get("changed"), "live_before": settings.get("live_before"),
        "result": _without_curves(summary) if summary else None,
        # Each day's result and the running total, in %: the shape of the run.
        "curve": [[point.get("day"), point.get("percent"), point.get("cumulative")] for point in curve] or None,
    }
    if run.get("progress"):
        answer["progress"] = run["progress"]
    if run.get("lines"):
        answer["log"] = run["lines"][-20:]
    return answer


def _quantiles(values: list[float]) -> dict | None:
    if not values:
        return None
    ordered = sorted(values)
    pick = lambda share: ordered[min(len(ordered) - 1, max(0, round(share * (len(ordered) - 1))))]  # noqa: E731
    return {"min": ordered[0], "p5": pick(0.05), "p25": pick(0.25), "median": pick(0.5), "p75": pick(0.75),
            "p95": pick(0.95), "max": ordered[-1], "count": len(ordered)}


def _trade_line(trade: dict) -> dict:
    entry, exit_ = trade.get("entry") or {}, trade.get("exit") or {}
    turbo = trade.get("turbo") or {}
    return {
        "id": trade.get("id"), "day": trade.get("day"), "direction": trade.get("direction"),
        "turbo": {key: turbo.get(key) for key in ("description", "issuer", "barrier", "uic") if turbo.get(key) is not None},
        "decided_at": entry.get("decided_at"), "filled_at": entry.get("filled_at"), "entry_price": entry.get("price"),
        "entry_asset_price": entry.get("asset_price"), "candidates": entry.get("candidates"),
        "exit_at": exit_.get("filled_at"), "exit_bid": exit_.get("bid"), "exit_reason": exit_.get("reason"),
        "exit_group": exit_.get("group"), "exit_asset_price": exit_.get("asset_price"),
        "performance": trade.get("performance_percent"), "max_performance": trade.get("max_performance_percent"),
        "min_performance": trade.get("min_performance_percent"), "holding_seconds": trade.get("holding_seconds"),
        "profit_loss": trade.get("profit_loss"), "confidence": trade.get("confidence"),
        "session_part": trade.get("session_part"), "minutes_after_open": trade.get("minutes_after_open"),
        "signal_id": trade.get("signal_id"), "notes": trade.get("notes") or None,
    }


def _day_line(day: dict) -> dict:
    regime = day.get("regime") or {}
    recorded = day.get("recorded") or {}
    return {
        "day": day.get("day"), "status": day.get("status"), "entries": day.get("entries"), "trades": day.get("trades"),
        "day_percent": day.get("day_percent"), "profit_loss": day.get("profit_loss"), "signals": day.get("signals"),
        "recorded_minutes": recorded.get("minutes"), "turbos": {"long": recorded.get("long"), "short": recorded.get("short")},
        "regime": {key: regime.get(key) for key in ("gap", "range", "volatility", "efficiency", "day_type", "us_open", "news")
                   if key in regime} or None,
        "labels": regime.get("labels"),
    }


def _flat(row: dict, prefix: str = "") -> dict:
    """A row of nested fields as columns (turbo.issuer...): for a CSV."""
    flat = {}
    for key, value in row.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(_flat(value, f"{name}."))
        elif isinstance(value, list):
            flat[name] = ";".join(str(item) for item in value)
        else:
            flat[name] = value
    return flat


def _table(rows: list[dict]) -> str:
    flat = [_flat(row) for row in rows]
    columns = list(dict.fromkeys(column for row in flat for column in row))
    return csv_text(columns or ["id"], ([row.get(column) for column in columns] for row in flat))


def register(server, research) -> None:
    calls = research.calls

    @research_tool(server, calls, name="backtest_options", title="What a backtest can be asked", read_only=True)
    async def backtest_options() -> dict:
        """Everything a backtest request (backtest_start) can say: every setting of WATA's rules with its live value
        and bounds (change them by name in ``changes``), the indices, the account it starts from, the default signal
        delays, the random-entry and sweep limits, the longest period, and the turbo model's state. Read it before
        writing a request."""
        capital = await backtest_requests.last_balance(research.queries)
        return compacted(await run_in_threadpool(research.service().options, capital))

    @research_tool(server, calls, name="backtest_list", title="Backtest runs", read_only=True)
    async def backtest_list(made_by: str = "", experiment: str = "", offset: int = 0, limit: int = 20) -> dict:
        """The runs and sweeps, newest first: what each was asked, its state (queued, running, done, failed,
        cancelled, interrupted), place in line, headline figures, and whether it is stale (made with other code than
        the server's now). made_by="claude" for Claude's, experiment=<id> for one experiment's."""
        ids = await run_in_threadpool(research.service().run_ids, made_by=made_by or None, experiment=experiment or None)
        listed = page(ids, offset, limit)
        runs = await run_in_threadpool(
            research.service().list_runs, made_by=made_by or None, experiment=experiment or None, offset=offset, limit=limit,
        )
        return compacted({**listed, "items": [_run_line(run) for run in runs]})

    @research_tool(server, calls, name="backtest_result", title="What a backtest found", read_only=True)
    async def backtest_result(run_id: str, part: str = "summary", group: str = "") -> dict:
        """A run or sweep, by part. summary: what was asked, its state, figures (risk on daily results, money),
        against random entries, the edge's headline, stress, warnings, and the daily curve. edge: how the asset moved
        1 to 60 minutes after each entry signal against random moments (read it first). breakdowns: the trades by
        hour, weekday, direction, exit, holding time, turbo price, regime (group= one of them). baseline: the random
        entries' totals. sweep: each combination's figures on the tuning and held-out days, with the walk-forward,
        the probability of overfitting and the deflated Sharpe in summary. reconciliation: a journal replay set
        beside WATA's real trades. settings: every setting the run used."""
        if part not in PARTS:
            raise Refused(f"part is one of {', '.join(PARTS)}")
        if part == "summary":
            return compacted(_summary_part(await run_in_threadpool(research.service().get, run_id)))
        if part == "edge":
            found = await run_in_threadpool(research.service().edge, run_id)
            return compacted(found or {"note": "This run has no edge: a sweep, or a run made before it was measured"})
        if part == "breakdowns":
            found = await run_in_threadpool(research.service().breakdowns, run_id)
            groups = {
                name: [row for row in rows if row.get("trade_count")]
                for name, rows in found["groups"].items() if not group or name == group
            }
            if group and not groups:
                raise Refused(f"group is one of {', '.join(found['groups'])}")
            return compacted({"trades": found["trades"], "given_back": found["given_back"], "groups": groups})
        if part == "baseline":
            found = await run_in_threadpool(research.service().baseline, run_id)
            if not found:
                return {"note": "This run has no random entries"}
            return compacted({
                key: found.get(key) for key in ("repetitions", "seed", "entries", "hours", "histogram")
            } | {"totals": _quantiles(found.get("totals") or []), "trades": _quantiles(found.get("trades") or [])})
        if part == "sweep":
            found = await run_in_threadpool(research.service().sweep, run_id)
            summary = (await run_in_threadpool(research.service().get, run_id)).get("summary") or {}
            return compacted({
                "axes": found.get("axes"), "held_out": found.get("held_out"), "held_out_from": found.get("held_out_from"),
                "cells": [
                    {"cell": number, "values": cell.get("values"), "tuning": _figures_line(cell.get("tuning")),
                     "held_out": _figures_line(cell.get("held_out")), "whole": _figures_line(cell.get("whole"))}
                    for number, cell in enumerate(found.get("cells") or [])
                ],
                "walk_forward": _without_curves(summary.get("walk_forward")),
                "pbo": summary.get("pbo"), "deflated_sharpe": summary.get("deflated_sharpe"),
            })
        if part == "reconciliation":
            found = await run_in_threadpool(research.service().reconciliation, run_id)
            if not found:
                return {"note": "Only a replay of the journal is set beside WATA's real trades"}
            return compacted({
                "summary": found.get("summary"), "pairs": (found.get("pairs") or [])[:50],
                "backtest_only": (found.get("backtest_only") or [])[:25], "live_only": (found.get("live_only") or [])[:25],
            })
        run = await run_in_threadpool(research.service().get, run_id)
        return compacted(run.get("settings") or {})

    @research_tool(server, calls, name="backtest_trades", title="A backtest's trades", read_only=True, export=True)
    async def backtest_trades(run_id: str, day: str = "", order: str = "time", direction: str = "",
                              exit_group: str = "", offset: int = 0, limit: int = DEFAULT_PAGE,
                              format: str = "json") -> dict | str:
        """A run's trades: the turbo, when it was decided and filled at what price, how it ended (exit reason and
        group), its result and the best and worst it reached, how long it was held, where in the session.
        order="worst" or "best" sorts by result; day, direction and exit_group narrow it."""
        _format(format)
        if order not in TRADE_ORDERS:
            raise Refused(f"order is one of {', '.join(TRADE_ORDERS)}")
        rows = [_trade_line(trade) for trade in await run_in_threadpool(research.service().trades, run_id)]
        rows = [row for row in rows if (not day or row["day"] == day) and (not direction or row["direction"] == direction)
                and (not exit_group or row["exit_group"] == exit_group)]
        if order != "time":
            rows.sort(key=lambda row: row["performance"] if row["performance"] is not None else 0, reverse=order == "best")
        if format == "csv":
            return _table(compacted(rows))
        return compacted(page(rows, offset, limit))

    @research_tool(server, calls, name="backtest_days", title="A backtest's days", read_only=True, export=True)
    async def backtest_days(run_id: str, offset: int = 0, limit: int = 100, format: str = "json") -> dict | str:
        """A run's days: whether each was traded (or had no recorded turbo, no bars, a closed market), its signals by
        fate, entries, trades and result, the turbos recorded, and what kind of day it was (gap, range, volatility,
        trend or back and forth, the US open's first half hour, macro news)."""
        _format(format)
        rows = [_day_line(day) for day in await run_in_threadpool(research.service().days, run_id)]
        if format == "csv":
            return _table(compacted(rows))
        return compacted(page(rows, offset, limit))

    @research_tool(server, calls, name="backtest_signals", title="A backtest's signals and their fate", read_only=True,
                   export=True)
    async def backtest_signals(run_id: str, day: str = "", fate: str = "", offset: int = 0, limit: int = DEFAULT_PAGE,
                               format: str = "json") -> dict | str:
        """A run's signals and what became of each: executed, refused (and by which of WATA's rules, with the live
        reason), failed, unpriced (no recorded turbo), closed, ignored, not a signal. Narrow by day or fate."""
        _format(format)
        if fate and fate not in FATES:
            raise Refused(f"fate is one of {', '.join(FATES)}")
        rows = await run_in_threadpool(research.service().signals, run_id, None, fate or None)
        rows = [row for row in rows if not day or row.get("day") == day]
        if format == "csv":
            return _table(compacted(rows))
        return compacted(page(rows, offset, limit))

    @research_tool(server, calls, name="backtest_compare", title="Runs side by side", read_only=True)
    async def backtest_compare(run_ids: list[str]) -> dict:
        """Two to four finished runs side by side: their headline figures, risk, random entries beaten, the edge's
        headline, and each day's result, day by day."""
        if not 2 <= len(run_ids) <= MAX_COMPARED or len(set(run_ids)) != len(run_ids):
            raise Refused(f"Compare 2 to {MAX_COMPARED} different runs")
        compared, days = [], {}
        for run_id in run_ids:
            run = await run_in_threadpool(research.service().get, run_id)
            summary = run.get("summary") or {}
            compared.append({
                **_run_line(run), "figures": _figures_line(summary.get("figures")),
                "risk": (summary.get("figures") or {}).get("risk"), "against_random": summary.get("against_random"),
                "edge": summary.get("edge"), "money": summary.get("money"),
            })
            for point in summary.get("curve") or []:
                days.setdefault(point.get("day"), {})[run_id] = point.get("percent")
        return compacted({
            "runs": compared,
            "days": [{"day": day, **values} for day, values in sorted(days.items())],
        })

    @research_tool(server, calls, name="turbo_model", title="Turbos made up from the index", read_only=True)
    async def turbo_model() -> dict:
        """The measure the made-up turbos of long backtests are built with (turbos: "modelled" in a request): when
        and on how many recorded days it was measured, its fit by direction, the spread by hour, and whether a
        measure is being made. Few days measured (few_days) means a model to distrust."""
        return compacted(await run_in_threadpool(research.service().turbo_model))


# ── Claude's runs: asked for, made from another, waited for, removed ──

REQUEST_EXAMPLE = (
    '{"from":"2026-10-13","to":"2026-10-19","indice":"us100","source":{"kind":"pine","script":{"id":"<id>","version":3},'
    '"asset":{"provider":"capital","key":"US100"},"side":"bid","bars":{"type":"time","timeframe":"1"},'
    '"inputs":[{"id":"in_0","value":9}],"alerts":[{"title":"Long","action":"long"},{"title":"Exit","action":"close-position"}]},'
    '"changes":{"stoploss_percent":-10},"baseline":{"repetitions":100}}'
)
START_DESCRIPTION = (
    "Ask for a run, or a sweep (request.sweep), for an open experiment. The request is the Backtests form's: "
    + REQUEST_EXAMPLE + ". A sweep adds "
    '"sweep":{"axes":[{"kind":"setting","name":"stoploss_percent","values":[-5,-10,-15]}],'
    '"walk_forward":{"train_days":5,"test_days":2}} (two axes and 64 combinations at most; kind "input" for a '
    "script input in_N); its trials before are the family's, counted here. source.kind \"journal\" replays the "
    'signals WATA received (with "chart":{"asset":..,"side":..,"bars":..}); "turbos":"modelled" runs long periods on '
    "turbos made up from the index. Days of the lock box only with lockbox_final. Claude's runs wait behind the "
    "operator's, a few at most at once. Then backtest_wait."
)
DERIVED = ("stress", "cell", "neighbours")
WAIT_MOST_SECONDS = 100
WAIT_MOST_RUNS = 10
UNFINISHED = ("queued", "running")


def register_writing(server, research) -> None:
    """The tools that ask for runs: registered with the experiment journal's."""
    calls = research.calls
    journal = research.journal
    asked = research.asked

    def made_via() -> str | None:
        caller = current_caller()
        return caller.name if caller else None

    async def started(made: dict, experiment_id: str, trials: int, lockbox: dict | None, note: str = "") -> dict:
        await run_in_threadpool(
            journal.record_run, experiment_id, made["id"], kind=made["kind"], trials=trials, lockbox=bool(lockbox),
            note=note,
        )
        return compacted({**_run_line(made), "trials_recorded": trials,
                          "family_trials": await run_in_threadpool(journal.family_trials, journal.get(experiment_id)["family"]),
                          "next": "backtest_wait"})

    @research_tool(server, calls, name="backtest_start", title="Ask for a backtest", read_only=False,
                   description=START_DESCRIPTION)
    async def backtest_start(experiment_id: str, request: dict, lockbox_final: bool = False,
                             idempotency_key: str = "") -> dict:
        experiment = guards.open_experiment(journal, experiment_id)
        if idempotency_key and idempotency_key in asked:
            return compacted(_run_line(await run_in_threadpool(research.service().get, asked[idempotency_key])))
        body = dict(request)
        guards.check_source(research, body)
        first, last = guards.period_of(body)
        guards.check_room(research, guards.cost_of(body, (last - first).days + 1))
        lockbox = guards.lockbox_of(journal, first, last, lockbox_final)
        if isinstance(body.get("sweep"), dict):
            body["sweep"] = {**body["sweep"], "trials_before": journal.family_trials(experiment["family"])}
        made = await backtest_requests.ask_backtest(
            research.queries, research.service(), body, made_by=CLAUDE, made_via=made_via(),
            experiment=experiment_id, lockbox=lockbox,
        )
        if idempotency_key:
            asked[idempotency_key] = made["id"]
        return await started(made, experiment_id, trials_of(body), lockbox)

    @research_tool(server, calls, name="backtest_derive", title="A backtest made from another", read_only=False)
    async def backtest_derive(run_id: str, kind: str, experiment_id: str, cell: int = -1, parameter: str = "",
                              values: list[float] | None = None, step: float = 0.0, count: int = 2,
                              lockbox_final: bool = False) -> dict:
        """From a finished run or sweep, for an open experiment. kind="stress": the run again with each order later
        and each fill dearer (a sweep that chooses nothing: no trial counted). kind="cell": a sweep's combination
        ``cell`` made a run of its own, with random entries (already counted). kind="neighbours": a sweep of
        ``parameter`` (a setting, or a script input in_N) around the run's value, ``count`` steps of ``step`` each
        side (a tenth of the value by default) or the given ``values``: a plateau, or a peak among worse values?"""
        if kind not in DERIVED:
            raise Refused(f"kind is one of {', '.join(DERIVED)}")
        experiment = guards.open_experiment(journal, experiment_id)
        service = research.service()
        run = await run_in_threadpool(service.get, run_id)
        first, last = guards.period_of(run.get("request") or {})
        days = (last - first).days + 1
        lockbox = guards.lockbox_of(journal, first, last, lockbox_final)
        research_fields = {"made_by": CLAUDE, "made_via": made_via(), "experiment": experiment_id, "lockbox": lockbox}
        if kind == "stress":
            guards.check_room(research, days * 16)
            return await started(service.stress(run_id, **research_fields), experiment_id, 0, lockbox, note=f"stress of {run_id}")
        if kind == "cell":
            guards.check_room(research, days * 101)
            return await started(service.run_cell(run_id, cell, **research_fields), experiment_id, 0, lockbox,
                                 note=f"cell {cell} of {run_id}")
        if run.get("kind") != "run" or (run.get("status") or {}).get("state") != "done":
            raise Refused("Neighbours are swept around a finished run")
        if not parameter:
            raise Refused("Name the parameter: a setting (stoploss_percent...) or a script input (in_0...)")
        try:
            chosen = list(values or neighbour_values(value_of(run, parameter), step=step or None, count=count))
        except ValueError as e:
            raise Refused(str(e)) from None
        body = body_of(run)
        body["name"] = f"Neighbours of {parameter}: {run.get('name') or run_id}"[:120]
        body["sweep"] = {
            "axes": [{"kind": "input" if parameter.startswith("in_") else "setting", "name": parameter, "values": chosen}],
            "trials_before": journal.family_trials(experiment["family"]),
        }
        body.pop("baseline", None)
        guards.check_room(research, guards.cost_of(body, days))
        made = await backtest_requests.ask_backtest(research.queries, service, body, **research_fields)
        return await started(made, experiment_id, len(chosen) - 1, lockbox, note=f"neighbours of {parameter} in {run_id}")

    @research_tool(server, calls, name="backtest_wait", title="Wait for backtests", read_only=True)
    async def backtest_wait(run_ids: list[str], seconds: int = 60) -> dict:
        """Wait, up to ``seconds`` (100 at most), until none of the runs is queued or running; then each one's state,
        place in line and progress. Call it again while some are unfinished: a run of a few days takes a minute or
        two, a sweep longer, and Claude's wait behind the operator's."""
        if not 1 <= len(run_ids) <= WAIT_MOST_RUNS:
            raise Refused(f"Wait for 1 to {WAIT_MOST_RUNS} runs")
        service = research.service()
        deadline = research.clock() + min(max(seconds, 0), WAIT_MOST_SECONDS)
        while True:
            states = [service.state(run_id) for run_id in run_ids]
            if all(state["state"] not in UNFINISHED for state in states) or research.clock() >= deadline:
                break
            await asyncio.sleep(1)
        return compacted({"runs": states, "unfinished": sum(1 for state in states if state["state"] in UNFINISHED)})

    @research_tool(server, calls, name="backtest_remove", title="Stop or remove one of Claude's runs", read_only=False,
                   destructive=True, idempotent=True)
    async def backtest_remove(run_id: str) -> dict:
        """One of Claude's own runs: stopped if it waits or runs, removed with its files once it is over. The
        operator's runs are never touched."""
        service = research.service()
        if await run_in_threadpool(service.made_by, run_id) != CLAUDE:
            raise Refused("Claude removes only its own runs")
        if service.state(run_id)["state"] in UNFINISHED:
            return compacted({"stopped": service.cancel(run_id)["id"]})
        await run_in_threadpool(service.delete, run_id)
        return {"removed": run_id}

    @research_tool(server, calls, name="turbo_model_measure", title="Measure the turbo model again", read_only=False)
    async def turbo_model_measure(start_day: date, end_day: date) -> dict:
        """Measure the recorded turbos of a period against Dukascopy's index (92 days at most), for the runs on
        made-up turbos. Every modelled run shares the measure: Claude measures it only when there is none or it is
        more than a week old. Then turbo_model says when it is done."""
        service = research.service()
        current = (await run_in_threadpool(service.turbo_model)).get("measure")
        made_at = (current or {}).get("made_at")
        if made_at:
            age = research.clock() - datetime.fromisoformat(made_at.replace("Z", "+00:00")).timestamp()
            if age < MODEL_FRESH_DAYS * 86_400:
                raise Refused(f"The turbos were measured {age / 86_400:.1f} days ago: the operator measures them again "
                              "within a week (Backtests page)")
        return compacted(service.measure_turbos(start_day, end_day))
