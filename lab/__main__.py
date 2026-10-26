"""
The lab's commands:

    python -m lab status                    what is there: research days, database, admin, runner
    python -m lab db start|stop|status      the lab's PostgreSQL
    python -m lab db load [--day D ...]     the research days not loaded yet
    python -m lab db prune --keep-months N  drop the oldest months of bars from the database
    python -m lab serve                     the lab's admin, on http://127.0.0.1:8083
    python -m lab stop                      the lab's admin and its database
    python -m lab sql "SELECT ..."          SQL over the research files (DuckDB)

    python -m lab script list|import|add|update|check   the Pine scripts the lab runs
    python -m lab run SPEC [--set k=v] [--input in_N=v] [--exp ID]   a backtest, through the lab's admin
    python -m lab sweep SPEC --axis setting:k=a,b [--axis input:in_N=a,b]   several values at once
    python -m lab edge RUN [--by hour]      how the asset moved after the run's entry signals
    python -m lab regimes RUN               each day's kind (volatility, gap, trend, news) and the trades by it
    python -m lab calendar check            whether the macro calendar knows the news ahead
    python -m lab backup check [--offsite]  the newest dump restored here, its trading tables counted
    python -m lab model calibrate|show      turbos made up from the index, measured on the recorded ones
    python -m lab model validate SPEC       the same signals on recorded and on made-up turbos
    python -m lab reconcile RUN             a replay of the journal beside WATA's real positions
    python -m lab calibrate execution       what WATA's own orders took and cost
    python -m lab paper [RULE]              each shadow rule's days traded on paper, beside its reference
    python -m lab stress RUN                the run again with later and dearer fills
    python -m lab neighbours RUN --param P  the values around the run's own, alone
    python -m lab runs | show RUN | compare RUN RUN... | trades RUN | days RUN | signals RUN
    python -m lab exp new|list|show|trials|conclude|report   the experiment journal
    python -m lab lockbox show|set DAY|clear  the days kept for a candidate's final check

Run from the checkout's root, in the lab's environment (lab/setup.sh):

    .venv-lab/bin/python -m lab status
"""

import argparse
import asyncio
import json
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from lab import admin, config, db, experiments, paths, runs, scripts, text


def _print(value, as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, indent=2, default=str))
    elif isinstance(value, dict):
        width = max((len(key) for key in value), default=0)
        for key, item in value.items():
            print(f"{key.ljust(width)}  {item if not isinstance(item, (dict, list)) else json.dumps(item, default=str)}")
    else:
        print(value)


def _runner_built() -> bool:
    return (paths.REPO_ROOT / "src" / "ta_engine" / "runner" / "runner.mjs").is_file()


def command_status(arguments) -> int:
    days = db.research_days()
    found = {
        "research days": f"{len(days)} ({days[0][0].isoformat()} to {days[-1][0].isoformat()})" if days else "none: run lab/sync.sh",
        "database": "running" if db.is_running() else "stopped (python -m lab db start)",
        "admin": admin.url() if admin.running_pid() else "stopped (python -m lab serve)",
        "pine runner": "built" if _runner_built() else "not built: npm run build:runner in admin_ui/",
        "node": admin.prefer_node() or f"none of {admin.LEAST_NODE} or later: nvm install {admin.LEAST_NODE}",
        "lab folder": str(paths.lab_root()),
    }
    if db.is_running():
        loaded = asyncio.run(db.status())
        found["database"] = f"running, {loaded['days']} days loaded ({loaded['first']} to {loaded['last']}), {loaded['bytes'] // 1_000_000} MB"
        missing = len(days) - loaded["days"]
        if missing > 0:
            found["to load"] = f"{missing} research days (python -m lab db load)"
    _print(found, arguments.json)
    return 0


def command_db(arguments) -> int:
    if arguments.action == "start":
        print(f"The lab's database: {db.dsn()}")
    elif arguments.action == "stop":
        print("Stopped." if db.stop() else "It was not running.")
    elif arguments.action == "status":
        _print(asyncio.run(db.status()), arguments.json)
    elif arguments.action == "load":
        only = {date.fromisoformat(day) for day in arguments.day} if arguments.day else None
        was_running = db.is_running()
        try:
            loaded = asyncio.run(db.load(only=only))
        finally:
            # Started for this alone (the morning's sync): stopped again.
            if not was_running and not admin.running_pid():
                db.stop()
        print(f"{len(loaded)} day{'s' if len(loaded) != 1 else ''} loaded." if loaded else "Nothing new to load.")
    elif arguments.action == "prune":
        if arguments.keep_months is None:
            sys.exit("--keep-months is needed")
        dropped = asyncio.run(db.prune(arguments.keep_months))
        print(f"Dropped: {', '.join(dropped)}" if dropped else "Nothing older to drop.")
    return 0


def command_serve(arguments) -> int:
    if admin.running_pid():
        sys.exit(f"The lab's admin is running already: {admin.url()}")
    admin.serve(arguments.port, arguments.workers)
    return 0


def command_stop(_arguments) -> int:
    print("The lab's admin stopped." if admin.stop() else "The lab's admin was not running.")
    print("The lab's database stopped." if db.stop() else "The lab's database was not running.")
    return 0


def command_sql(arguments) -> int:
    from lab import sql

    result = sql.query(arguments.query, limit=arguments.limit)
    print(sql.as_json(result) if arguments.json else sql.as_text(result))
    return 0


def command_script(arguments) -> int:
    action = arguments.action
    if action == "list":
        found = scripts.listed()
        if arguments.json:
            _print(found, True)
        else:
            print(text.table(["id", "name", "kind", "version", "from"], [[s["id"], s["name"], s["kind"], s["version"], s["from"]] for s in found]))
    elif action == "import":
        _print(scripts.import_server(), arguments.json)
    elif action == "add":
        made = scripts.add(Path(arguments.target), arguments.name)
        print(f"{made['name']}: {made['id']} version {made['version']}")
    elif action == "update":
        if not arguments.file:
            sys.exit("update needs the script and the file: python -m lab script update <id> <file.pine>")
        saved = scripts.update(arguments.target, Path(arguments.file))
        print(f"{saved['name']}: {saved['id']} version {saved['version']}")
    elif action == "check":
        target = Path(arguments.target)
        if target.suffix == ".pine" and target.is_file():
            found = scripts.version_of_file(target, arguments.name)
            script_id, version = found["id"], found["version"]
        else:
            script_id, version = scripts.resolve(arguments.target)
        result = scripts.check_now(script_id, version)
        _print(result, arguments.json)
        return 0 if result["ok"] else 1
    return 0


def _submit(arguments, sweep_axes: list | None = None) -> int:
    spec, base_dir = runs.load_spec(arguments.spec)
    body, noted = runs.request_of(
        spec, base_dir=base_dir, sets=arguments.set or [], inputs=arguments.input or [], first=arguments.first,
        last=arguments.last, name=arguments.name, experiment=arguments.exp, allow_lockbox=arguments.lockbox,
    )
    if sweep_axes is not None:
        body["sweep"] = {"axes": sweep_axes, "held_out": arguments.held_out}
        if arguments.walk_forward:
            train, _, test = arguments.walk_forward.partition("/")
            body["sweep"]["walk_forward"] = {"train_days": int(train), "test_days": int(test or train)}
        if noted["experiment"]:
            body["sweep"]["trials_before"] = experiments.family_trials(experiments.get(noted["experiment"])["family"])
        body.pop("baseline", None)
    if noted["script"]:
        script = noted["script"]
        print(f"script {script.get('name', script['id'])} version {script['version']}{' (new version)' if script.get('new') else ''}")
    with admin.Client(arguments.port) as client:
        created = client.create(body)
        print(f"asked: {created['id']} ({created['name']})", flush=True)
        if noted["experiment"]:
            experiments.record_run(
                noted["experiment"], created["id"], kind="sweep" if sweep_axes is not None else "run",
                trials=runs.trials_of(body), lockbox=noted["lockbox"],
            )
        if arguments.no_wait:
            return 0
        (finished,) = client.wait([created["id"]])
    return _show(finished["id"], arguments.json)


def command_run(arguments) -> int:
    return _submit(arguments)


def command_sweep(arguments) -> int:
    if not arguments.axis:
        sys.exit("A sweep needs one axis at least: --axis setting:stoploss_percent=-5,-10,-15")
    return _submit(arguments, runs.axes_of(arguments.axis))


def _show(run_id: str, as_json: bool) -> int:
    reader = runs.reader()
    found = reader.get(run_id)
    if as_json:
        _print(found, True)
        return 0
    if found["kind"] == "sweep" and found["status"]["state"] == "done":
        sweep = reader.sweep(run_id)
        print(runs.stress_summary(found, sweep) if found.get("stress_of") else runs.sweep_summary(found, sweep))
    else:
        print(runs.summary(found))
    return 0 if found["status"]["state"] == "done" else 1


def command_edge(arguments) -> int:
    from src.admin.processes import read_json

    reader = runs.reader()
    run = reader.get(arguments.run)
    edge = read_json(reader._file(arguments.run, "edge.json"))
    if edge is None:
        raise ValueError("No edge measured: a sweep has none, and runs made before it was measured neither")
    if arguments.json:
        _print(edge, True)
        return 0
    print(runs.edge_summary(run, edge, arguments.by))
    return 0


def command_regimes(arguments) -> int:
    reader = runs.reader()
    run = reader.get(arguments.run)
    days = reader.days(arguments.run)
    breakdowns = reader.breakdowns(arguments.run) if run["kind"] == "run" else {}
    if arguments.json:
        _print({"days": [{"day": day["day"], "regime": day.get("regime")} for day in days], "groups": breakdowns.get("groups")}, True)
        return 0
    print(runs.regimes_summary(run, days, breakdowns))
    return 0


def command_calendar(arguments) -> int:
    events = tuple(event.strip().upper() for event in arguments.events.split(",") if event.strip()) if arguments.events else None
    ok, shown = runs.calendar_check(date.today(), ahead_days=arguments.ahead, events=events)
    print(shown)
    return 0 if ok else 1


def command_backup(arguments) -> int:
    from lab import backup

    try:
        ok, shown = backup.check(arguments.offsite, arguments.remote, arguments.identity)
    except backup.BackupError as e:
        print(e, file=sys.stderr)
        return 1
    print(shown)
    return 0 if ok else 1


def _measure_period(arguments) -> tuple[str, str]:
    if arguments.first and arguments.last:
        return arguments.first, arguments.last
    loaded = asyncio.run(db.status()) if db.is_running() else {}
    if not loaded.get("last"):
        raise ValueError("No day loaded in the lab's database: give --from and --to, or python -m lab db load")
    last = date.fromisoformat(str(loaded["last"]))
    first = max(date.fromisoformat(str(loaded["first"])), last - timedelta(days=91))
    return arguments.first or first.isoformat(), arguments.last or last.isoformat()


def command_model(arguments) -> int:
    from src.backtest.turbo_model import compare_trades

    with admin.Client(arguments.port) as client:
        if arguments.action == "show":
            model = client.get("/api/backtests/turbo-model")
            _print(model, True) if arguments.json else print(runs.measure_summary(model))
            return 0
        if arguments.action == "calibrate":
            first, last = _measure_period(arguments)
            client.post("/api/backtests/turbo-model", {"from": first, "to": last})
            print(f"measuring the recorded turbos of {first} to {last}…", flush=True)
            while (model := client.get("/api/backtests/turbo-model"))["state"] == "running":
                time.sleep(2)
            _print(model, True) if arguments.json else print(runs.measure_summary(model))
            return 0 if model["state"] == "done" else 1
        # validate: the spec on the recorded days, on recorded then on made-up turbos.
        if not arguments.spec:
            raise ValueError("model validate needs a spec: python -m lab model validate lab/specs/ema_cross.json")
        spec, base_dir = runs.load_spec(arguments.spec)
        body, _ = runs.request_of(spec, base_dir=base_dir, first=arguments.first, last=arguments.last, allow_lockbox=True)
        body["baseline"] = {"repetitions": 0, "seed": 1}
        body.pop("sweep", None)
        made = []
        for turbos in ("recorded", "modelled"):
            asked = client.create({**body, "turbos": turbos, "name": f"Model check, {turbos}: {body.get('name') or spec.get('name', '')}"[:120],
                                   "tags": sorted({*body.get("tags", []), "model-check"})})
            print(f"asked: {asked['id']} ({turbos})", flush=True)
            made.append(asked["id"])
        finished = client.wait(made)
    if any(run["status"]["state"] != "done" for run in finished):
        for run in finished:
            print(runs.summary(run))
        return 1
    reader = runs.reader()
    found = compare_trades(reader.trades(made[0]), reader.trades(made[1]))
    if arguments.json:
        _print(found, True)
    else:
        print(runs.validation_summary(found))
    return 0


def command_reconcile(arguments) -> int:
    reader = runs.reader()
    run = reader.get(arguments.run)
    found = reader.reconciliation(arguments.run)
    if not found:
        raise ValueError("Not set beside WATA's positions: only a replay of the journal is, made with this version")
    if arguments.json:
        _print(found, True)
    else:
        print(runs.reconciliation_summary(run, found))
    return 0


def command_calibrate(arguments) -> int:
    with admin.Client(arguments.port) as client:
        measured = client.get("/api/backtests/execution", **{"from": arguments.first, "to": arguments.last})
    if arguments.json:
        _print(measured, True)
    else:
        print(runs.execution_summary(measured))
    return 0


def command_paper(arguments) -> int:
    with admin.Client(arguments.port) as client:
        rules = client.get("/api/backtests/paper")["rules"]
    if arguments.rule:
        rules = {rule_id: found for rule_id, found in rules.items() if rule_id == arguments.rule}
    if arguments.json:
        _print(rules, True)
    else:
        print(runs.paper_summary(rules))
    return 0


def command_stress(arguments) -> int:
    with admin.Client(arguments.port) as client:
        created = client.post(f"/api/backtests/{arguments.run}/stress")
        print(f"asked: {created['id']} ({created['name']})", flush=True)
        experiment = runs.experiment_of(created)
        if experiment:
            # Nothing is chosen among worse fills: not a trial.
            experiments.record_run(experiment, created["id"], kind="stress", trials=0)
        if arguments.no_wait:
            return 0
        (finished,) = client.wait([created["id"]])
    return _show(finished["id"], arguments.json)


def command_neighbours(arguments) -> int:
    from src.backtest.settings import FIELDS_BY_NAME

    run = runs.reader().get(arguments.run)
    if run["kind"] != "run" or run["status"]["state"] != "done":
        raise ValueError("Only a finished run has neighbours")
    parameter = arguments.param
    own = runs.value_of(run, parameter)
    if arguments.values:
        values = [runs.parse_value(value) for value in arguments.values.split(",")]
        if own not in values:
            values = sorted([*values, own])
    else:
        field = FIELDS_BY_NAME.get(parameter)
        values = runs.neighbour_values(
            own, step=runs.parse_value(arguments.step) if arguments.step else None, count=arguments.count,
            least=field.least if field else None, most=field.most if field else None,
        )
    body = runs.body_of(run)
    body["name"] = f"Neighbours of {parameter}: {run['name']}"[:120]
    body["sweep"] = {"axes": [{"kind": "input" if parameter.startswith("in_") else "setting", "name": parameter, "values": values}],
                     "held_out": 0.3}
    experiment = runs.experiment_of(run)
    locked = experiments.lockbox_from()
    if locked is not None and date.fromisoformat(body["to"]) >= locked and not arguments.lockbox:
        raise ValueError(f"The run reaches the lock box ({locked.isoformat()}): pass --lockbox for a candidate's final check")
    if experiment:
        body["sweep"]["trials_before"] = experiments.family_trials(experiments.get(experiment)["family"])
    with admin.Client(arguments.port) as client:
        created = client.create(body)
        print(f"asked: {created['id']} ({created['name']}): {parameter} = {', '.join(map(str, values))}", flush=True)
        if experiment:
            experiments.record_run(experiment, created["id"], kind="sweep", trials=len(values) - 1,
                                   lockbox=bool(arguments.lockbox), note=f"neighbours of {arguments.run}")
        if arguments.no_wait:
            return 0
        (finished,) = client.wait([created["id"]])
    if arguments.json or finished["status"]["state"] != "done":
        return _show(finished["id"], arguments.json)
    print(runs.neighbours_summary(finished, runs.reader().sweep(finished["id"]), own))
    return 0


def command_show(arguments) -> int:
    return _show(arguments.run, arguments.json)


def command_runs(arguments) -> int:
    listed = runs.reader().list_runs()
    if arguments.exp:
        listed = [run for run in listed if f"exp:{arguments.exp}" in run["tags"]]
    listed = listed[: arguments.limit]
    if arguments.json:
        _print(listed, True)
        return 0
    rows = []
    for run in listed:
        headline = run.get("headline") or {}
        rows.append([
            run["id"], run["name"][:40], run["kind"], run["status"]["state"], f"{run['from']}..{run['to']}",
            headline.get("total_percent"), headline.get("trades"), headline.get("beaten"),
        ])
    print(text.table(["run", "name", "kind", "state", "period", "total %", "trades", "beats random %"], rows, {5, 6, 7}))
    return 0


FIGURES = (
    ("days traded", lambda s: (s.get("days") or {}).get("tested")),
    ("trades", lambda s: (s.get("figures") or {}).get("trades")),
    ("won %", lambda s: (s.get("figures") or {}).get("win_rate")),
    ("per trade %", lambda s: (s.get("figures") or {}).get("expectancy")),
    ("won for 1 lost", lambda s: (s.get("figures") or {}).get("profit_factor")),
    ("all days %", lambda s: (s.get("figures") or {}).get("total_percent")),
    ("worst day %", lambda s: (s.get("figures") or {}).get("worst_day")),
    ("worst drawdown %", lambda s: (s.get("figures") or {}).get("worst_drawdown")),
    ("sharpe", lambda s: ((s.get("figures") or {}).get("risk") or {}).get("sharpe")),
    ("P/L €", lambda s: (s.get("money") or {}).get("profit_loss")),
    ("beats random %", lambda s: (s.get("against_random") or {}).get("beaten")),
)


def command_compare(arguments) -> int:
    reader = runs.reader()
    found = [reader.get(run_id) for run_id in arguments.runs]
    if arguments.json:
        _print({run["id"]: run.get("summary") for run in found}, True)
        return 0
    rows = [[label, *[read(run.get("summary") or {}) for run in found]] for label, read in FIGURES]
    changed = sorted({name for run in found for name in ((run.get("settings") or {}).get("changed") or {})})
    for name in changed:
        rows.append([name, *[((run.get("settings") or {}).get("settings") or {}).get(name) for run in found]])
    print(text.table(["", *[run["name"][:24] for run in found]], rows, set(range(1, len(found) + 1))))
    return 0


def _local(run: dict):
    from src.backtest.clock import TradingClock

    zone = ((run.get("settings") or {}).get("settings") or {}).get("timezone") or "Europe/Paris"
    clock = TradingClock(zone)
    return lambda iso: clock.local(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()).strftime("%H:%M:%S") if iso else "-"


def command_trades(arguments) -> int:
    reader = runs.reader()
    run = reader.get(arguments.run)
    if arguments.by:
        groups = reader.breakdowns(arguments.run)["groups"].get(arguments.by)
        if groups is None:
            sys.exit(f"--by is one of {', '.join(reader.breakdowns(arguments.run)['groups'])}")
        if arguments.json:
            _print(groups, True)
            return 0
        names = [key for key in groups[0] if key not in ("trade_count", "win_count", "loss_count", "win_rate", "profit_loss", "average_percent", "average_best_percent")] if groups else []
        rows = [[*[group.get(name) for name in names], group["trade_count"], group["win_rate"], group["average_percent"], group["profit_loss"]] for group in groups]
        print(text.table([*names, "trades", "won %", "average %", "P/L €"], rows, set(range(len(names), len(names) + 4))))
        return 0
    found = reader.trades(arguments.run, date.fromisoformat(arguments.day) if arguments.day else None)
    if arguments.worst:
        found = sorted(found, key=lambda trade: trade["performance_percent"])[: arguments.worst]
    elif arguments.best:
        found = sorted(found, key=lambda trade: -trade["performance_percent"])[: arguments.best]
    if arguments.json:
        _print(found, True)
        return 0
    local = _local(run)
    rows = [[
        trade["id"], trade["day"], trade["direction"], trade["turbo"].get("symbol") or trade["turbo"]["instrument_id"],
        local(trade["entry"]["filled_at"]), trade["entry"]["price"], local(trade["exit"]["filled_at"]), trade["exit"]["bid"],
        trade["performance_percent"], trade["profit_loss"], trade["exit"]["group"],
    ] for trade in found]
    print(text.table(["#", "day", "dir", "turbo", "bought", "at", "sold", "at", "%", "€", "sold on"], rows, {5, 7, 8, 9}))
    return 0


def command_days(arguments) -> int:
    found = runs.reader().days(arguments.run)
    if arguments.json:
        _print(found, True)
        return 0
    rows = [[
        day["day"], day["status"], day["trades"], day["day_percent"], day["profit_loss"],
        sum((day.get("signals") or {}).values()), (day.get("recorded") or {}).get("minutes"),
    ] for day in found]
    print(text.table(["day", "status", "trades", "day %", "P/L €", "signals", "recorder min"], rows, {2, 3, 4, 5, 6}))
    return 0


def command_signals(arguments) -> int:
    reader = runs.reader()
    run = reader.get(arguments.run)
    found = reader.signals(arguments.run, date.fromisoformat(arguments.day) if arguments.day else None, arguments.fate)
    if arguments.json:
        _print(found, True)
        return 0
    local = _local(run)
    rows = [[
        signal["id"], signal["day"], local(signal.get("bar_closed_at") or signal.get("arrived_at")), signal.get("action"),
        signal["fate"], signal.get("rule"), signal.get("trade_id") or ",".join(str(i) for i in signal.get("closed_trade_ids") or []),
    ] for signal in found[: arguments.limit]]
    print(text.table(["#", "day", "time", "action", "fate", "rule", "trade"], rows))
    if len(found) > arguments.limit:
        print(f"… {len(found) - arguments.limit} more (--limit)")
    return 0


def command_exp(arguments) -> int:
    action = arguments.action
    if action == "new":
        made = experiments.new(arguments.title or "", arguments.hypothesis or "", arguments.family or "")
        print(f"experiment {made['experiment']}: {made['title']}")
    elif action == "list":
        found = experiments.experiments()
        if arguments.json:
            _print(found, True)
        else:
            print(text.table(
                ["id", "title", "family", "runs", "trials", "verdict"],
                [[e["id"], e["title"][:48], e["family"], len(e["runs"]), e["trials"], e["verdict"] or "open"] for e in found],
            ))
    elif action == "show":
        found = experiments.get(arguments.id)
        if arguments.json:
            _print(found, True)
            return 0
        print(f"{found['id']}  {found['title']}  [{found['verdict'] or 'open'}]")
        print(f"family {found['family']} · {found['trials']} trials ({experiments.family_trials(found['family'])} in the family)")
        print(f"hypothesis: {found['hypothesis']}")
        for entry in found["elsewhere"]:
            print(f"- {entry['trials']} tried elsewhere: {entry['note']}")
        reader = runs.reader()
        for entry in found["runs"]:
            try:
                run = reader.get(entry["run"])
            except Exception:
                print(f"- {entry['run']}: removed")
                continue
            headline = run.get("headline") or {}
            locked = " (lock box)" if entry["lockbox"] else ""
            if run["status"]["state"] != "done":
                print(f"- {run['id']} {run['name']} [{run['status']['state']}]{locked}")
                continue
            if run["kind"] == "sweep":
                print(f"- {run['id']} {run['name']} [{run['status']['state']}] sweep of {headline.get('cells') or '?'} combinations{locked}")
                continue
            print(f"- {run['id']} {run['name']} [{run['status']['state']}] total {text.percent(headline.get('total_percent'))}, "
                  f"{text.cell(headline.get('trades'))} trades, beats random {text.cell(headline.get('beaten'))}%{locked}")
        if found["notes"]:
            print(f"conclusion: {found['notes']}")
    elif action == "trials":
        experiments.record_trials(arguments.id, arguments.count, arguments.notes or "")
        found = experiments.get(arguments.id)
        print(f"{arguments.count} trials made elsewhere written down: {found['trials']} in {found['id']}, "
              f"{experiments.family_trials(found['family'])} in the family {found['family']}")
    elif action == "conclude":
        experiments.conclude(arguments.id, arguments.verdict or "", arguments.notes or "")
        print("Concluded.")
    elif action == "report":
        from lab import report

        path = report.write(arguments.id, Path(arguments.out) if arguments.out else None)
        print(path)
    return 0


def command_lockbox(arguments) -> int:
    if arguments.action == "set":
        if not arguments.day:
            sys.exit("lockbox set needs its first day: python -m lab lockbox set 2026-11-02")
        experiments.set_lockbox(date.fromisoformat(arguments.day))
    elif arguments.action == "clear":
        experiments.clear_lockbox()
    locked = experiments.lockbox_from()
    print(f"The lock box: the days from {locked.isoformat()}" if locked else "No lock box.")
    return 0


def parser() -> argparse.ArgumentParser:
    main = argparse.ArgumentParser(prog="python -m lab", description="WATA's research lab")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="answer in JSON")
    commands = main.add_subparsers(dest="command", required=True)

    commands.add_parser("status", parents=[common], help="what is there").set_defaults(handler=command_status)

    database = commands.add_parser("db", parents=[common], help="the lab's PostgreSQL")
    database.add_argument("action", choices=("start", "stop", "status", "load", "prune"))
    database.add_argument("--day", action="append", help="load this day only (repeatable), YYYY-MM-DD")
    database.add_argument("--keep-months", type=int, help="prune: the months of bars kept")
    database.set_defaults(handler=command_db)

    serve = commands.add_parser("serve", parents=[common], help="the lab's admin, until stopped")
    serve.add_argument("--port", type=int, default=config.DEFAULT_PORT)
    serve.add_argument("--workers", type=int, default=config.WORKERS, help="backtests run at once")
    serve.set_defaults(handler=command_serve)

    commands.add_parser("stop", parents=[common], help="the lab's admin and its database").set_defaults(handler=command_stop)

    query = commands.add_parser("sql", parents=[common], help="SQL over the research files (DuckDB)")
    query.add_argument("query")
    query.add_argument("--limit", type=int, default=50)
    query.set_defaults(handler=command_sql)

    script = commands.add_parser("script", parents=[common], help="the Pine scripts the lab runs")
    script.add_argument("action", choices=("list", "import", "add", "update", "check"))
    script.add_argument("target", nargs="?", help="add: a .pine file; update, check: a script (name, id or id@version), or a file")
    script.add_argument("file", nargs="?", help="update: the .pine file")
    script.add_argument("--name", help="add: the script's name (the file's by default)")
    script.set_defaults(handler=command_script)

    for name, handler, helped in (("run", command_run, "a backtest"), ("sweep", command_sweep, "several values at once")):
        asked = commands.add_parser(name, parents=[common], help=helped)
        asked.add_argument("spec", help="a JSON file: the Backtests form's request, and the lab's keys")
        asked.add_argument("--set", action="append", help="a setting changed: name=value (repeatable)")
        asked.add_argument("--input", action="append", help="a script input: in_N=value (repeatable)")
        asked.add_argument("--from", dest="first", help="first day, YYYY-MM-DD")
        asked.add_argument("--to", dest="last", help="last day, YYYY-MM-DD")
        asked.add_argument("--name")
        asked.add_argument("--exp", help="the experiment it belongs to")
        asked.add_argument("--lockbox", action="store_true", help="allow the lock box's days (a candidate's final check)")
        asked.add_argument("--no-wait", action="store_true", help="ask, and do not wait for the result")
        asked.add_argument("--port", type=int, default=config.DEFAULT_PORT)
        if name == "sweep":
            asked.add_argument("--axis", action="append", help="setting:<name>=v1,v2 or input:<id>=v1,v2 (two at most)")
            asked.add_argument("--held-out", type=float, default=0.3, help="the share of the last days held out (0.1 to 0.5)")
            asked.add_argument("--walk-forward", help="TRAIN/TEST days of the windows, e.g. 10/5")
        asked.set_defaults(handler=handler)

    edge = commands.add_parser("edge", parents=[common], help="how the asset moved after a run's entry signals")
    edge.add_argument("run")
    edge.add_argument("--by", choices=("hour", "direction", "fate", *runs.DAY_KINDS))
    edge.set_defaults(handler=command_edge)

    regimes = commands.add_parser("regimes", parents=[common], help="each day's kind, and the trades by it")
    regimes.add_argument("run")
    regimes.set_defaults(handler=command_regimes)

    calendar = commands.add_parser("calendar", parents=[common], help="the macro calendar the days are marked with")
    calendar.add_argument("action", choices=("check",))
    calendar.add_argument("--ahead", type=int, default=30, help="days ahead each kind of news must be known")
    calendar.add_argument("--events", help="the kinds judged, as the macro_news rule names them (FOMC,CPI,NFP,PCE by default)")
    calendar.set_defaults(handler=command_calendar)

    backups = commands.add_parser("backup", parents=[common], help="the newest dump restored here and counted")
    backups.add_argument("action", choices=("check",))
    backups.add_argument("--offsite", action="store_true", help="the newest copy in the bucket, decrypted here")
    backups.add_argument("--remote", help="the rclone path of the copies (WATA_BACKUP_REMOTE)")
    backups.add_argument("--identity", help="the file of your age key (WATA_BACKUP_AGE_IDENTITY)")
    backups.set_defaults(handler=command_backup)

    model = commands.add_parser("model", parents=[common], help="turbos made up from the index")
    model.add_argument("action", choices=("calibrate", "show", "validate"))
    model.add_argument("spec", nargs="?", help="validate: the spec run on recorded and on made-up turbos")
    model.add_argument("--from", dest="first", help="the first recorded day (the loaded ones by default)")
    model.add_argument("--to", dest="last")
    model.add_argument("--port", type=int, default=config.DEFAULT_PORT)
    model.set_defaults(handler=command_model)

    reconcile = commands.add_parser("reconcile", parents=[common], help="a replay of the journal beside WATA's real positions")
    reconcile.add_argument("run")
    reconcile.set_defaults(handler=command_reconcile)

    calibrate = commands.add_parser("calibrate", parents=[common], help="what WATA's own orders took and cost")
    calibrate.add_argument("what", choices=("execution",))
    calibrate.add_argument("--from", dest="first", help="the first day (three months before today by default)")
    calibrate.add_argument("--to", dest="last")
    calibrate.add_argument("--port", type=int, default=config.DEFAULT_PORT)
    calibrate.set_defaults(handler=command_calibrate)

    paper = commands.add_parser("paper", parents=[common], help="each shadow rule's days traded on paper")
    paper.add_argument("rule", nargs="?", help="one rule's id")
    paper.add_argument("--port", type=int, default=config.DEFAULT_PORT)
    paper.set_defaults(handler=command_paper)

    stress = commands.add_parser("stress", parents=[common], help="a finished run again with later and dearer fills")
    stress.add_argument("run")
    stress.add_argument("--no-wait", action="store_true")
    stress.add_argument("--port", type=int, default=config.DEFAULT_PORT)
    stress.set_defaults(handler=command_stress)

    neighbours = commands.add_parser("neighbours", parents=[common], help="a run's value of one parameter and those around it")
    neighbours.add_argument("run")
    neighbours.add_argument("--param", required=True, help="a setting, or an input of the script (in_N)")
    neighbours.add_argument("--values", help="the values tried, e.g. 8,10,12,14,16 (the run's own is added)")
    neighbours.add_argument("--step", help="the step between values (a tenth of the run's value by default)")
    neighbours.add_argument("--count", type=int, default=2, help="steps on each side")
    neighbours.add_argument("--lockbox", action="store_true", help="allow the lock box's days (a candidate's final check)")
    neighbours.add_argument("--no-wait", action="store_true")
    neighbours.add_argument("--port", type=int, default=config.DEFAULT_PORT)
    neighbours.set_defaults(handler=command_neighbours)

    listing = commands.add_parser("runs", parents=[common], help="the runs, newest first")
    listing.add_argument("--exp", help="those of an experiment")
    listing.add_argument("--limit", type=int, default=30)
    listing.set_defaults(handler=command_runs)

    show = commands.add_parser("show", parents=[common], help="a run in a few lines")
    show.add_argument("run")
    show.set_defaults(handler=command_show)

    compare = commands.add_parser("compare", parents=[common], help="runs side by side")
    compare.add_argument("runs", nargs="+")
    compare.set_defaults(handler=command_compare)

    trades = commands.add_parser("trades", parents=[common], help="a run's trades")
    trades.add_argument("run")
    trades.add_argument("--day")
    trades.add_argument("--worst", type=int)
    trades.add_argument("--best", type=int)
    trades.add_argument("--by", help="grouped: hour, weekday, direction, exit, confidence, holding, turbo_price, issuer...")
    trades.set_defaults(handler=command_trades)

    days = commands.add_parser("days", parents=[common], help="a run's days")
    days.add_argument("run")
    days.set_defaults(handler=command_days)

    signals = commands.add_parser("signals", parents=[common], help="a run's signals and what became of them")
    signals.add_argument("run")
    signals.add_argument("--day")
    signals.add_argument("--fate", choices=("executed", "refused", "failed", "unpriced", "closed", "ignored", "not_a_signal"))
    signals.add_argument("--limit", type=int, default=60)
    signals.set_defaults(handler=command_signals)

    exp = commands.add_parser("exp", parents=[common], help="the experiment journal")
    exp.add_argument("action", choices=("new", "list", "show", "trials", "conclude", "report"))
    exp.add_argument("id", nargs="?")
    exp.add_argument("--title")
    exp.add_argument("--hypothesis")
    exp.add_argument("--family", help="the family of ideas it belongs to: trials are counted by family")
    exp.add_argument("--count", type=int, default=0, help="trials: how many configurations were tried elsewhere")
    exp.add_argument("--verdict", choices=experiments.VERDICTS)
    exp.add_argument("--notes")
    exp.add_argument("--out", help="report: the HTML file written")
    exp.set_defaults(handler=command_exp)

    lockbox = commands.add_parser("lockbox", parents=[common], help="the days kept for a candidate's final check")
    lockbox.add_argument("action", choices=("show", "set", "clear"))
    lockbox.add_argument("day", nargs="?")
    lockbox.set_defaults(handler=command_lockbox)
    return main


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    # The Pine runner (script checks, and the runs of the lab's admin) needs a recent Node.
    admin.prefer_node()
    try:
        return arguments.handler(arguments)
    except (ValueError, RuntimeError) as e:
        print(f"lab: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
