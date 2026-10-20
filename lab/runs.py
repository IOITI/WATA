"""
Runs and sweeps from a spec, asked of the lab's admin, and what they found,
read from its folders in a few lines.

A spec is the request of the Backtests form (``BacktestRequestBody`` in
admin_ui/src/lib/types.ts) as JSON, with the lab's own keys:

- ``script_file``: a ``.pine`` file (from the spec's folder), saved in the
  lab's store as the version its content is, and run;
- ``script``: a script by name, id or ``id@version``, in place of ``source.script``;
- ``experiment``: the experiment the run belongs to (``python -m lab exp new``).

What the command line adds (``--set``, ``--input``, ``--from``...) changes
the spec for that run only.
"""

import copy
import json
from datetime import date
from pathlib import Path

from lab import experiments, paths, scripts, text

LAB_KEYS = ("script_file", "script", "experiment")


def parse_value(raw: str):
    """A value typed on the command line, as JSON reads it, else as text."""
    lowered = raw.strip().lower()
    if lowered in ("true", "on", "yes"):
        return True
    if lowered in ("false", "off", "no"):
        return False
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def load_spec(path: str) -> tuple[dict, Path]:
    spec_path = Path(path)
    return json.loads(spec_path.read_text(encoding="utf-8")), spec_path.parent


def request_of(
    spec: dict,
    *,
    base_dir: Path = Path("."),
    sets: list[str] = (),
    inputs: list[str] = (),
    first: str | None = None,
    last: str | None = None,
    name: str | None = None,
    experiment: str | None = None,
    allow_lockbox: bool = False,
) -> tuple[dict, dict]:
    """The request a spec makes, and what the lab noted of it (the script's version, the experiment, the lock box)."""
    body = copy.deepcopy({key: value for key, value in spec.items() if key not in LAB_KEYS})
    noted: dict = {"script": None, "experiment": experiment or spec.get("experiment"), "lockbox": False}
    source = body.setdefault("source", {"kind": "pine"})
    if spec.get("script_file"):
        found = scripts.version_of_file((base_dir / spec["script_file"]).resolve())
        source["script"] = {"id": found["id"], "version": found["version"]}
        noted["script"] = found
    elif isinstance(spec.get("script"), str):
        script_id, version = scripts.resolve(spec["script"])
        source["script"] = {"id": script_id, "version": version}
        noted["script"] = {"id": script_id, "version": version}
    for item in sets:
        setting, _, value = item.partition("=")
        if not setting or not _:
            raise ValueError(f"--set is name=value, not {item!r}")
        body.setdefault("changes", {})[setting.strip()] = parse_value(value)
    for item in inputs:
        input_id, _, value = item.partition("=")
        if not input_id or not _:
            raise ValueError(f"--input is in_N=value, not {item!r}")
        kept = [entry for entry in source.get("inputs") or [] if entry.get("id") != input_id.strip()]
        source["inputs"] = [*kept, {"id": input_id.strip(), "value": parse_value(value)}]
    if first:
        body["from"] = first
    if last:
        body["to"] = last
    if name:
        body["name"] = name
    if noted["experiment"]:
        experiment_found = experiments.get(noted["experiment"])
        body["tags"] = sorted({*body.get("tags", []), f"exp:{experiment_found['id']}", f"family:{experiment_found['family']}"})[:8]
    locked = experiments.lockbox_from()
    if locked is not None and body.get("to") and date.fromisoformat(body["to"]) >= locked:
        if not allow_lockbox:
            raise ValueError(
                f"The days from {locked.isoformat()} are the lock box: end the period before, "
                "or pass --lockbox for a candidate's final check (it is written in the journal)"
            )
        noted["lockbox"] = True
    return body, noted


def axes_of(texts: list[str]) -> list[dict]:
    """``setting:stoploss_percent=-5,-10,-15`` or ``input:in_0=8,12`` as the axes of a sweep."""
    axes = []
    for item in texts:
        kind, _, rest = item.partition(":")
        name, _, values = rest.partition("=")
        if kind not in ("setting", "input") or not name or not values:
            raise ValueError(f"--axis is setting:<name>=v1,v2,... or input:<id>=v1,v2,..., not {item!r}")
        axes.append({"kind": kind, "name": name, "values": [parse_value(value) for value in values.split(",")]})
    return axes


def trials_of(body: dict) -> int:
    """The configurations a request tries: one, or each combination of its sweep."""
    sweep = body.get("sweep")
    if not sweep:
        return 1
    count = 1
    for axis in sweep.get("axes") or []:
        count *= len(axis.get("values") or [])
    return count


def experiment_of(run: dict) -> str | None:
    """The experiment a run was asked for, from its tags."""
    return next((tag[4:] for tag in run.get("tags") or [] if tag.startswith("exp:")), None)


def body_of(run: dict) -> dict:
    """The request a finished run was asked with, as the Backtests form would ask it again."""
    request = run["request"]
    source = request["source"]
    body = {
        key: request[key] for key in ("name", "tags", "from", "to", "indice") if key in request
    }
    body["changes"] = dict(request.get("changes") or {})
    if source["kind"] == "pine":
        rule = source["rule"]
        body["source"] = {
            "kind": "pine", "script": {"id": rule["script"]["id"], "version": rule["script"]["version"]},
            **{key: copy.deepcopy(rule[key]) for key in ("asset", "side", "bars", "inputs", "alerts", "alert_calls", "confidence_plot") if key in rule},
        }
    else:
        body["source"] = {"kind": "journal"}
        body["chart"] = copy.deepcopy(request["chart"])
    return body


def value_of(run: dict, parameter: str):
    """What a run had for a setting or for an input of its script (``in_N``)."""
    if parameter.startswith("in_"):
        rule = (run["request"]["source"] or {}).get("rule") or {}
        found = [entry["value"] for entry in rule.get("inputs") or [] if entry["id"] == parameter]
        if not found:
            raise ValueError(f"{parameter} is not an input the run gave its script: give its values with --values")
        return found[0]
    settings = (run.get("settings") or {}).get("settings") or {}
    if parameter not in settings:
        raise ValueError(f"{parameter} is not a setting of a backtest")
    return settings[parameter]


def neighbour_values(value, *, step=None, count: int = 2, least=None, most=None) -> list:
    """
    The run's value with ``count`` steps on each side: a tenth of the value
    by default, a whole step for a whole number. Kept within the setting's limits.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{value!r} has no neighbours: give the values with --values")
    whole = isinstance(value, int) and (step is None or float(step).is_integer())
    if step is None:
        step = max(1, round(abs(value) * 0.1)) if whole else (abs(value) * 0.1 or 0.1)
    found = []
    for k in range(-count, count + 1):
        candidate = value + k * step
        candidate = int(round(candidate)) if whole else round(candidate, 6)
        if (least is not None and candidate < least) or (most is not None and candidate > most):
            continue
        if candidate not in found:
            found.append(candidate)
    if len(found) < 2:
        raise ValueError("The setting's limits leave no neighbour: give the values with --values")
    return found


# ── reading what runs found ──


def reader():
    """The lab's runs, read as the admin reads them."""
    from src.admin.backtests import BacktestService

    return BacktestService(
        root=str(paths.backtests()), prices_root=str(paths.price_history()), config_path=str(paths.config()),
        scripts=scripts.store(),
    )


def summary(run: dict) -> str:
    """A run in a few lines: what it was, what it made, how far to trust it."""
    lines = [f"{run['name']}  [{run['status']['state']}]  {run['id']}"]
    request = run.get("request") or {}
    source = request.get("source") or {}
    if source.get("kind") == "pine":
        rule = source["rule"]
        inputs = ", ".join(f"{entry.get('title') or entry['id']}={entry['value']}" for entry in rule.get("inputs") or [])
        lines.append(
            f"script {rule['name']} v{rule['script']['version']} on {rule['asset']['provider']}/{rule['asset']['key']} "
            f"{rule['bars']}{f' · inputs {inputs}' if inputs else ''}"
        )
    elif source.get("kind") == "journal":
        lines.append(f"the signals WATA received ({source.get('signals')})")
    lines.append(f"{run['from']} to {run['to']} · turbos of {run['indice']}")
    changed = (run.get("settings") or {}).get("changed") or {}
    before = (run.get("settings") or {}).get("live_before") or {}
    if changed:
        lines.append("changed: " + ", ".join(f"{name} {before.get(name)} → {value}" for name, value in changed.items()))
    if run["status"]["state"] == "failed":
        lines.append(f"failed: {run['status'].get('error')}")
    data = run.get("summary") or {}
    figures = data.get("figures")
    if figures:
        days = data.get("days") or {}
        lines.append(
            f"days {days.get('tested')} of {days.get('asked')} traded · trades {figures['trades']} "
            f"({text.percent(figures.get('win_rate'), False)} won) · per trade {text.percent(figures.get('expectancy'))} "
            f"· won for 1 lost {text.cell(figures.get('profit_factor'))} · longest losing run {figures.get('longest_losing_streak')}"
        )
        spread = figures.get("total_range")
        redrawn = (
            f" ({spread['level']}% of redrawn days: {text.percent(spread['low'])} to {text.percent(spread['high'])})" if spread else ""
        )
        lines.append(
            f"total {text.percent(figures['total_percent'])}{redrawn}"
            f" · best day {text.percent(figures.get('best_day'))} · worst {text.percent(figures.get('worst_day'))} "
            f"· median {text.percent(figures.get('median_day'))} · worst drawdown {text.percent(figures.get('worst_drawdown'))}"
        )
        risk = figures.get("risk")
        if risk:
            lines.append(
                "risk: " + " · ".join(f"{name} {text.cell(risk.get(name))}" for name in (
                    "sharpe", "sortino", "calmar", "longest_under_water_days", "worst_5_percent_days", "exit_efficiency",
                ) if name in risk)
            )
        exits = figures.get("exits") or {}
        if exits:
            lines.append("sold on: " + ", ".join(f"{group} {count}" for group, count in sorted(exits.items(), key=lambda item: -item[1])))
    money = data.get("money")
    if money:
        lines.append(
            f"money: {text.money(money['capital'])} → {text.money(money['final'])} ({text.percent(money.get('return_percent'))}) "
            f"· average stake {text.money(money.get('average_stake'))}"
        )
    random = data.get("against_random")
    if random:
        lines.append(
            f"against random entries: beats {random['beaten']:.0f}% of {random['repetitions']} runs "
            f"(median {text.percent(random['median'])}, {random['level']}% between {text.percent(random['low'])} and {text.percent(random['high'])})"
        )
    signals = data.get("signals")
    if signals:
        fates = ", ".join(f"{fate} {count}" for fate, count in sorted(signals["by_fate"].items(), key=lambda item: -item[1]))
        refusals = ", ".join(f"{rule} {count}" for rule, count in sorted(signals.get("refusals", {}).items(), key=lambda item: -item[1]))
        lines.append(f"signals {signals['total']}: {fates}{f' · refused by {refusals}' if refusals else ''}")
    edge = data.get("edge")
    if edge and any(row.get("signals") for row in edge.get("horizons", [])):
        lines.append("edge of the entries: " + " · ".join(
            f"{row['minutes']} min {text.cell(row.get('mean_bp'))}bp (t {text.cell(row.get('t'))}, beats random {text.cell(row.get('beaten'))}%)"
            for row in edge["horizons"] if row.get("signals")
        ))
    if figures and figures.get("sample") in ("too_few", "few"):
        lines.append(f"sample: {figures['sample'].replace('_', ' ')} ({figures['trades']} trades over {figures['days']} days)")
    for warning in data.get("warnings") or []:
        lines.append(f"warning: {warning}")
    return "\n".join(lines)


def sweep_summary(run: dict, sweep: dict) -> str:
    """The combinations of a sweep: on the days a value may be chosen on, then on those held out."""
    axes = sweep["axes"]
    rows = []
    for index, cell in enumerate(sweep["cells"]):
        tuning, held, whole = cell.get("tuning") or {}, cell.get("held_out") or {}, cell.get("whole") or {}
        rows.append([
            index, *[str(value) for value in cell["values"]],
            tuning.get("total_percent"), tuning.get("trades"), held.get("total_percent"), held.get("trades"),
            whole.get("total_percent"), (cell.get("money") or {}).get("profit_loss"),
        ])
    rows.sort(key=lambda row: -(row[len(axes) + 1] if row[len(axes) + 1] is not None else -1e9))
    columns = ["cell", *[axis["name"] for axis in axes], "chosen-on %", "trades", "held-out %", "trades", "all %", "P/L €"]
    numeric = set(range(len(axes) + 1, len(columns)))
    held_from = sweep.get("held_out_from")
    head = f"{run['name']}  [{run['status']['state']}]  {run['id']}\n{len(sweep['cells'])} combinations"
    head += f", days from {held_from} held out" if held_from else ", too few days to hold any out"
    walk = (run.get("summary") or {}).get("walk_forward")
    tail = ""
    if walk:
        tail = (
            f"\nwalk-forward ({walk['train_days']} days to choose, {walk['test_days']} to test): "
            f"out of sample {text.percent(walk['total_percent'])} over {walk['days']} days "
            f"(the best single combination, known afterwards: {text.percent(walk.get('best_in_hindsight_percent'))}), "
            f"worst drawdown {text.percent(walk.get('worst_drawdown'))}, the choice changed {walk['changes']} times\n"
        )
        tail += text.table(
            ["chosen on", "kept on", *[axis["name"] for axis in axes], "chosen-on %", "kept-on %", "rank"],
            [[f"{window['train'][0]}..{window['train'][1]}", f"{window['test'][0]}..{window['test'][1]}",
              *[str(value) for value in window.get("values") or []], window["train_percent"], window["test_percent"],
              f"{window['test_rank']}/{window['cells']}"] for window in walk["windows"]],
            {len(axes) + 2, len(axes) + 3},
        )
    elif (run.get("summary") or {}).get("walk_forward", 0) is None:
        tail += "\nwalk-forward: too few days for one window"
    for name in ("deflated_sharpe", "pbo"):
        value = (run.get("summary") or {}).get(name)
        if value:
            tail += f"\n{name.replace('_', ' ')}: {value.get('sentence')}"
    return f"{head}\n{text.table(columns, rows, numeric)}{tail}"


def edge_summary(run: dict, edge: dict, by: str | None = None) -> str:
    """The moves after a run's entry signals, horizon by horizon, against random moments; or split by hour, direction or fate."""
    lines = [f"{run['name']}  [{run['status']['state']}]  {run['id']}"]
    if by is None:
        rows = [[
            row["minutes"], row["signals"], row["mean_bp"], row["median_bp"], row["hit_rate"], row["best_bp"], row["worst_bp"],
            row["independent"], row["t"], row["random"]["mean_bp"], row["random"]["beaten"],
        ] for row in edge["horizons"]]
        lines.append("the asset's move after each entry signal, in basis points in its direction:")
        lines.append(text.table(
            ["min", "signals", "mean", "median", "right %", "best", "worst", "apart", "t", "random", "beats %"], rows, set(range(11)),
        ))
        lines.append(
            "best and worst: on the way, on average · apart: the signals whose windows do not overlap, which t counts · "
            f"random: {edge['draws']} moments per signal in its hour"
        )
    else:
        if by in ("hour", "direction", "fate"):
            groups, name = edge[f"by_{by}"], by
        else:
            groups, name = (edge.get("by_day") or {}).get(by) or [], "group"
        horizons = [str(row["minutes"]) for row in edge["horizons"]]
        rows = [[group[name], *[group[minutes]["signals"] for minutes in horizons[:1]],
                 *[group[minutes]["mean_bp"] for minutes in horizons], *[group[minutes]["hit_rate"] for minutes in horizons[2:3]]]
                for group in groups]
        lines.append(f"mean move in basis points by {by}, after each horizon (minutes):")
        lines.append(text.table([by, "signals", *[f"{minutes} min" for minutes in horizons], f"right % {horizons[2]} min"], rows,
                                set(range(1, len(horizons) + 3))))
    if edge.get("unpriced"):
        lines.append(f"{edge['unpriced']} signal{'s' if edge['unpriced'] != 1 else ''} without a price of the asset, left out")
    return "\n".join(lines)


DAY_KINDS = ("volatility", "gap", "day_type", "us_open", "news")


def regimes_summary(run: dict, days: list[dict], breakdowns: dict) -> str:
    """Each day of a run with what kind of day it was, then the trades by each kind."""
    lines = [f"{run['name']}  [{run['status']['state']}]  {run['id']}"]
    rows = []
    for day in days:
        regime = day.get("regime") or {}
        labels = regime.get("labels") or {}
        rows.append([
            day["day"], regime.get("weekday", "")[:3], ", ".join(regime.get("news") or []) or "-", regime.get("gap_percent"),
            regime.get("range_percent"), regime.get("volatility_percent"), labels.get("volatility"), regime.get("day_type"),
            regime.get("us_open_move_percent"), day.get("day_percent") if day.get("status") == "tested" else None, day.get("trades"),
        ])
    lines.append(text.table(
        ["day", "", "news", "gap %", "range %", "vol %", "vol", "kind", "US open %", "day %", "trades"], rows, {3, 4, 5, 8, 9, 10},
    ))
    groups = breakdowns.get("groups") or {}
    for kind in (*DAY_KINDS, "session_part"):
        found = [row for row in groups.get(kind) or [] if row["trade_count"]]
        if not found:
            continue
        lines.append(f"\ntrades by {kind.replace('_', ' ')}:")
        lines.append(text.table(
            ["group", "trades", "won %", "per trade %", "P/L €"],
            [[row["group"], row["trade_count"], row["win_rate"], row["average_percent"], row["profit_loss"]] for row in found],
            {1, 2, 3, 4},
        ))
    if not any(day.get("regime") for day in days):
        lines.append("no kind of day: the run was made before they were measured")
    return "\n".join(lines)


def calendar_check(today: date, *, ahead_days: int = 30) -> tuple[bool, str]:
    """Whether the macro calendar knows each kind of news far enough ahead, and what it holds."""
    from src.backtest.regimes import read_calendar

    calendar = read_calendar()
    lines = [f"macro calendar {calendar.digest}: src/backtest/data/macro_calendar.csv"]
    ok = True
    for event, until in sorted(calendar.until.items()):
        short = (until - today).days < ahead_days
        ok = ok and not short
        lines.append(f"{event:5} known until {until.isoformat()}{'  ← add the next dates from its source' if short else ''}")
    if not ok:
        lines.append(f"Each source is named on the calendar's rows. Under {ahead_days} days ahead, days past the end have no news marked.")
    return ok, "\n".join(lines)


def measure_summary(model: dict) -> str:
    """The measure of the turbos made up from the index, in a few lines."""
    measure = model.get("measure")
    lines = [f"turbos made up from {model.get('underlying')}: {model.get('state')}"]
    if model.get("error"):
        lines.append(f"the last measure failed: {model['error']}")
    if not measure:
        lines.append("not measured yet: python -m lab model calibrate")
        return "\n".join(lines)
    lines.append(
        f"measured {measure['made_at']} on {measure['days']} recorded days ({measure['first']} to {measure['last']})"
        f"{', few: a rough guess' if measure.get('few_days') else ''}"
    )
    rows = [[direction, found["factor"], found["offset"], found["spacing"], found["turbos"], found["r2"], found["error_eur"],
             found["error_percent"]] for direction, found in measure["directions"].items()]
    lines.append(text.table(["", "€/point", "level - KO", "spacing", "turbos", "R²", "error €", "error %"], rows, set(range(1, 8))))
    return "\n".join(lines)


def validation_summary(found: dict) -> str:
    """The same signals on recorded and on made-up turbos: how far the model is from what was recorded."""
    lines = [
        f"{found['paired']} trades paired by signal (recorded {found['recorded_trades']}, made up {found['modelled_trades']})",
        f"result per trade: made up minus recorded {text.cell(found['mean_difference_percent'])} points on average, "
        f"{text.cell(found['median_absolute_difference_percent'])} apart in the middle; correlation {text.cell(found['correlation'])}",
        f"the same exit: {text.cell(found['same_exit_percent'])}% · turbo bought at {text.cell(found['median_entry_price']['recorded'])} € "
        f"recorded, {text.cell(found['median_entry_price']['modelled'])} € made up (median)",
        f"sum of the paired results: {text.percent(found['recorded_total_percent'])} recorded, {text.percent(found['modelled_total_percent'])} made up",
    ]
    return "\n".join(lines)


def reconciliation_summary(run: dict, found: dict) -> str:
    """A replay of the journal beside WATA's real positions: the figures, the pairs, the trades of one side only."""
    from src.backtest.reconciliation import sentence

    summary = found["summary"]
    lines = [f"{run['name']}  [{run['status']['state']}]  {run['id']}", sentence(summary)]
    if summary["paired"]:
        delay = summary["median_entry_seconds"]
        when = "-" if delay is None else f"{abs(delay):g} s {'after' if delay >= 0 else 'before'}"
        lines.append(
            f"same turbo {text.cell(summary['same_turbo_percent'])}% · bought {when} WATA "
            f"in the middle · correlation of the results {text.cell(summary['correlation'])}"
        )
        lines.append(text.table(
            ["trade", "day", "signal", "bought bt/WATA", "s after", "result bt", "result WATA", "same exit", "same turbo"],
            [[pair["trade_id"], pair["day"], pair["signal_uuid"][:8], f"{pair['entry_price']['backtest']}/{pair['entry_price']['real']}",
              pair["entry_seconds"], pair["result"]["backtest"], pair["result"]["real"], pair["same_exit"], pair["same_turbo"]]
             for pair in found["pairs"]],
            {0, 4, 5, 6},
        ))
    if found["backtest_only"]:
        lines.append(f"only the backtest: {summary['backtest_only']} ({', '.join(f'{count} {name}' for name, count in summary['backtest_only_live_outcomes'].items())} at WATA)")
    if found["live_only"]:
        lines.append(f"only WATA: {summary['live_only']} ({', '.join(f'{count} {name}' for name, count in summary['live_only_backtest_fates'].items())} in the backtest)")
    return "\n".join(lines)


def execution_summary(measured: dict) -> str:
    """What WATA's own orders took and cost, and what a run would be given from it."""
    delay = measured["signal_delay_seconds"]
    lines = [
        f"WATA's orders of {measured['from']} to {measured['to']}: {measured['signals']} signals, {measured['orders']} orders, "
        f"{measured['requests']} requests",
        f"signal to the trader: from its time {delay['from_signal']['median']} s (90%: {delay['from_signal']['p90']} s, "
        f"{delay['from_signal']['count']}), from its receipt {delay['from_receipt']['median']} s",
        f"order to Saxo's acceptance: {measured['order_seconds']['median']} s (90%: {measured['order_seconds']['p90']} s)",
    ]
    for side in ("buy", "sell", "all"):
        found = measured["slippage"][side]
        lines.append(f"{side}: {found['median']} € a turbo worse than the quote ({found['median_ticks']} ticks, 90%: {found['p90']}, {found['count']} fills)")
    refusals = measured["refusals"]
    if refusals["share_percent"] is not None:
        lines.append(f"refused by Saxo: {refusals['share_percent']}% ({', '.join(f'{count} {code}' for code, count in refusals['codes'].items()) or 'none'})")
    suggested = measured["suggested"]
    lines.append("for a run: " + (", ".join(f"--set {name}={value}" for name, value in suggested.items()) or
                                   f"nothing measured {measured['least']} times yet"))
    return "\n".join(lines)


def paper_summary(rules: dict) -> str:
    """Each rule's days traded on paper, beside the daily range of its reference run."""
    if not rules:
        return "no rule traded on paper yet: a shadow rule's finished days are, by the lab's admin, once the market is closed"
    lines = []
    for rule_id, found in rules.items():
        reference = found.get("reference")
        if reference and reference.get("low") is not None:
            said = f"reference {reference['run_id']} ({reference.get('name')}): {reference['low']}% to {reference['high']}% on 90% of its days"
        elif reference:
            said = f"reference {reference['run_id']}: {reference['days']} days, too few for a range"
        else:
            said = "no reference run (set one from a run made from the rule)"
        days = found.get("days") or []
        results = [day["day_percent"] for day in days if day.get("day_percent") is not None]
        lines.append(f"{rule_id}: {len(results)} days, {text.percent(sum(results)) if results else '-'} · {said}")
        lines.append(text.table(
            ["day", "state", "day %", "trades", "outside", "run"],
            [[day["day"], day["state"], day.get("day_percent"), day.get("trades"), day.get("outside"), day["run_id"]] for day in days],
            {2, 3},
        ))
    return "\n".join(lines)


def stress_summary(run: dict, sweep: dict) -> str:
    """A stress sweep: the run's total with each order later (rows) and each fill dearer (columns)."""
    rows_axis, columns_axis = sweep["axes"]
    totals = [(cell.get("whole") or {}).get("total_percent") for cell in sweep["cells"]]
    width = len(columns_axis["values"])
    rows = [
        [f"{rows_axis['values'][row]:g} s", *totals[row * width:(row + 1) * width]]
        for row in range(len(rows_axis["values"]))
    ]
    known = [total for total in totals if total is not None]
    lines = [
        f"{run['name']}  [{run['status']['state']}]  {run['id']}  (of {run.get('stress_of')})",
        "the total of all days, each order filled later (rows) and dearer by so much a turbo (columns):",
        text.table(["order time", *[f"+{value - columns_axis['values'][0]:.2f} €" for value in columns_axis["values"]]],
                   rows, set(range(1, width + 1))),
    ]
    if known and totals[0] is not None:
        positive = sum(1 for total in known if total > 0)
        worst = min(range(len(totals)), key=lambda index: totals[index] if totals[index] is not None else 1e9)
        lines.append(
            f"as run {text.percent(totals[0])}; above zero in {positive} of {len(known)} combinations; "
            f"the worst {text.percent(totals[worst])} ({rows_axis['values'][worst // width]:g} s, "
            f"{columns_axis['values'][worst % width]:g} € a turbo)"
        )
        if width > 1 and len(rows_axis["values"]) > 1 and totals[width + 1] is not None:
            lines.append(f"one step worse on both: {text.percent(totals[width + 1])}")
    return "\n".join(lines)


def neighbours_summary(run: dict, sweep: dict, own) -> str:
    """A sweep of one parameter around a run's value: a plateau, or a peak among worse values."""
    (axis,) = sweep["axes"]
    rows, totals = [], {}
    for cell in sweep["cells"]:
        (value,) = cell["values"]
        whole = cell.get("whole") or {}
        totals[value] = whole.get("total_percent")
        rows.append([f"{value}{'  ← the run' if value == own else ''}", whole.get("total_percent"), whole.get("trades"),
                     whole.get("expectancy"), whole.get("worst_drawdown")])
    lines = [
        f"{run['name']}  [{run['status']['state']}]  {run['id']}",
        f"{axis['name']} around {own}, all days:",
        text.table([axis["name"], "all %", "trades", "per trade %", "worst drawdown %"], rows, {1, 2, 3, 4}),
    ]
    mine = totals.get(own)
    others = [total for value, total in totals.items() if value != own and total is not None]
    if mine is not None and others:
        if mine <= 0:
            lines.append("the run's own value made nothing to keep: no plateau to look for")
        else:
            kept = sum(others) / len(others) / mine
            losing = sum(1 for total in others if total <= 0)
            verdict = "a plateau" if kept >= 0.7 and not losing else "a peak: the value is fitted" if kept < 0.5 or losing else "a slope"
            lines.append(
                f"its neighbours made {kept:.0%} of its total on average"
                f"{f', {losing} of them nothing or a loss' if losing else ''}: {verdict}"
            )
    return "\n".join(lines)
