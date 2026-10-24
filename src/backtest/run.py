"""
A backtest, run from its folder (what the admin's service prepared):

    python -m src.backtest.run --run <folder> [--data database | --data files:<root>]
        [--prices <price history>] [--node node] [--low-priority]

The folder holds ``request.json`` (what was asked), ``settings.json`` (the
rules and settings it runs with), the sources of its Pine script under
``sources/`` and, for a replay of the journal, ``journal.json``. The command
writes beside them, each file whole or not at all:

- ``progress.json`` while it runs;
- ``signals.json``, ``trades.json``, ``days.json`` (a run) or ``sweep.json``
  (a sweep), ``baseline.json``;
- for a model check (``model_check_of``: made-up turbos beside a run on the
  recorded ones, whose trades the service kept as ``recorded_trades.json``),
  ``model_check.json``: the two runs' trades paired by signal;
- ``summary.json`` last: its presence says the run is done.

Three phases, one after the other, so that the Pine runner and the recorded
turbos are never in memory together: the signals; the days, one at a time,
each read, traded by the run and by its random repetitions (or by every
combination of a sweep), then let go; the report.

The recorded turbos are read from PostgreSQL (the configuration's, read-only)
or from files (src/backtest/data.py). Exit code 0: done; 1: failed; 2: the
folder does not hold a run.
"""

import argparse
import asyncio
import json
import os
import sys
import time
import traceback
from collections import Counter
from datetime import date, datetime, timedelta, timezone

from src.backtest import figures, regimes, robustness
from src.backtest.edge import EdgeMeter, headline as edge_headline
from src.backtest.reconciliation import reconcile, sentence as reconciliation_sentence
from src.backtest.turbo_model import (
    MODEL_CHECK_FILE, RECORDED_TRADES_FILE, ModelData, NoCalibration, compare_trades, read_calibration,
)
from src.backtest.baseline import DEFAULT_SEED, Baseline, entry_hours
from src.backtest.clock import TradingClock, days_between
from src.backtest.rules import compounded
from src.backtest.data import AssetSeries, DatabaseData, FileData
from src.backtest.execution import EXECUTION_FILE, MEASURED, fills_for
from src.backtest.settings import BacktestSettings
from src.backtest.signals import by_day, closing_signals, journal_signals, pine_signals, raised_signals
from src.backtest.simulator import ENTRY_ACTIONS, Simulator, us_market_calendar
from src.backtest.sweep import Axis, cell_inputs, cell_settings, cells_of, held_out_from, with_inputs
from src.price_history.process import lower_priority
from src.ta_engine.replay import AssetRows
from src.ta_engine.rules import EngineRule

FORMAT = 1
PROGRESS_EVERY_SECONDS = 1.0
TESTED = "tested"
NO_TURBOS = "no_recorded_turbos"
NO_BARS = "no_strategy_bars"
CLOSED = "closed"


class NotARun(ValueError):
    """The folder does not hold what a run needs."""


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m src.backtest.run", description=__doc__.split("\n\n")[0])
    parser.add_argument("--run", required=True, help="the run's folder")
    parser.add_argument(
        "--data", default="database",
        help="database (the configuration's), files:<folder>, or model:<calibration.json> (turbos made up from an index)",
    )
    parser.add_argument("--files", help="with model:, the folder of recorded files the index is read from (else the database)")
    parser.add_argument("--prices", help="the price history folder (Dukascopy's days); the admin's by default")
    parser.add_argument("--node", default="node")
    parser.add_argument("--signal-cache", help="a folder where the runner's answers are kept, and read back by later runs")
    parser.add_argument("--runner", help=argparse.SUPPRESS)
    parser.add_argument("--low-priority", action="store_true", help="give way to everything else on the machine")
    return parser.parse_args(argv)


def _read(path: str):
    with open(path, encoding="utf-8") as in_file:
        return json.load(in_file)


def _write(path: str, content) -> None:
    partial = f"{path}.part"
    with open(partial, "w", encoding="utf-8") as out_file:
        json.dump(content, out_file, separators=(",", ":"), default=str)
    os.replace(partial, path)


class Progress:
    def __init__(self, folder: str):
        self._path = os.path.join(folder, "progress.json")
        self._last = 0.0
        self._said: dict = {}
        # What the runner is given now: the engine day, its bars, its peak memory so far.
        self._runner: dict = {}

    def __call__(self, phase: str, done: int, total: int, day: date | str | None = None, *, force: bool = False) -> None:
        self._said = {"phase": phase, "done": done, "total": total, "day": str(day) if day else None}
        now = time.monotonic()
        if not force and now - self._last < PROGRESS_EVERY_SECONDS:
            return
        self._last = now
        _write(self._path, {**self._said, **self._runner})

    def runner_day(self, begin: int, bars: int, peak_mb: float | None) -> None:
        """The engine day the runner is about to be given: written at once, in case it is the last."""
        self._runner = {"engine_day": datetime.fromtimestamp(begin, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "bars": bars, "runner_peak_mb": peak_mb}
        _write(self._path, {**self._said, **self._runner})


def _config():
    path = os.getenv("WATA_CONFIG_PATH")
    if not path:
        return None
    from src.configuration import ConfigurationManager

    return ConfigurationManager(path)


async def _sources(arguments, config, clock: TradingClock | None = None):
    """The days of turbos (recorded, or made up from an index), the rows of the assets, and what closes them."""
    from src.admin.context import AdminSettings

    prices_root = arguments.prices or (
        AdminSettings.from_config(config).price_history_path if config is not None else AdminSettings.price_history_path
    )
    if arguments.data.startswith("model:"):
        calibration = read_calibration(arguments.data.removeprefix("model:"))
        files = getattr(arguments, "files", None)
        if files:
            rows = AssetRows(prices_root, files_root=files)
            return ModelData(calibration, rows, clock or TradingClock(calibration.get("timezone", "Europe/Paris"))), rows, None
        _, rows, database = await _sources(argparse.Namespace(**{**vars(arguments), "data": "database"}), config)
        return ModelData(calibration, rows, clock or TradingClock(calibration.get("timezone", "Europe/Paris"))), rows, database
    if arguments.data.startswith("files:"):
        root = arguments.data.removeprefix("files:")
        return FileData(root), AssetRows(prices_root, files_root=root), None
    if arguments.data != "database":
        raise NotARun("--data is database, files:<folder> or model:<calibration.json>")
    from src.database.postgres import PostgresConnectionManager

    dsn = (config.get_config_value("postgresql", {}) or {}).get("dsn") if config is not None else None
    if not dsn:
        raise NotARun("No database to read the recorded turbos from (WATA_CONFIG_PATH)")
    # One connection, which cannot write: a backtest reads what the recorder kept and nothing else.
    database = PostgresConnectionManager(dsn=dsn, min_size=1, max_size=1, server_settings={"default_transaction_read_only": "on"})
    await database.connect()
    return DatabaseData(database), AssetRows(prices_root, db=database), database


def _engine_rule(folder: str, rule: dict) -> EngineRule:
    """The rule with the sources kept in the run's folder: the script as it was when the run was asked."""

    def source(pin: dict) -> str:
        with open(os.path.join(folder, "sources", f"{pin['id']}-v{pin['version']}.pine"), encoding="utf-8") as in_file:
            return in_file.read()

    return EngineRule(
        rule=rule, source=source(rule["script"]),
        libraries=tuple({**pin, "source": source(pin)} for pin in rule["libraries"]),
    )


async def _asset_series(rows: AssetRows, chart: dict | None, start: int, end: int) -> AssetSeries | None:
    """The ten-second closes of the chart's asset over a day, noted beside each trade."""
    if not chart:
        return None
    asset = chart["asset"]
    try:
        found, _ = await rows.rows(asset["provider"], asset["key"], start, end)
    except (ValueError, OSError):
        return None
    series = AssetSeries()
    # A row: ts, bid open, high, low, close, ask open, high, low, close, updates.
    column = 8 if chart.get("side") == "ask" else 4
    for row in found:
        value = row[column] if row[column] is not None else row[4]
        if value is not None:
            series.ts.append(int(row[0]))
            series.close.append(float(value))
    return series


def _day_status(record: dict, *, pine: bool, strategy_bars: int, weekend: bool) -> str:
    recorded = record["recorded"]
    if recorded[ENTRY_ACTIONS[0]] + recorded[ENTRY_ACTIONS[1]] == 0:
        return CLOSED if weekend else NO_TURBOS
    if pine and strategy_bars == 0:
        return NO_BARS
    return TESTED


def sweep_robustness(cells: list[dict], sweep: dict, first_held: str | None) -> dict:
    """
    What a sweep says beyond its best combination: the walk-forward asked for,
    the probability that choosing the best was fitting, and the deflated
    Sharpe ratio of the one best on the days a value may be chosen on, with
    every combination and the family's earlier trials counted.
    """
    curves = [{day: percent for day, percent, _ in cell["curve"]} for cell in cells]
    found: dict = {}
    windows = sweep.get("walk_forward")
    if windows:
        found["walk_forward"] = robustness.walk_forward(
            curves, windows["train_days"], windows["test_days"], values=[cell["values"] for cell in cells],
        )
    found["pbo"] = robustness.pbo(curves)
    tuning = [{day: percent for day, percent in curve.items() if first_held is None or day < first_held} for curve in curves]
    totals = [compounded(list(curve.values())) for curve in tuning]
    if any(tuning):
        best = max(range(len(cells)), key=lambda index: (totals[index], -index))
        ordered = [tuning[best][day] for day in sorted(tuning[best])]
        sharpes = [robustness.daily_sharpe([curve[day] for day in sorted(curve)]) for curve in tuning]
        found["deflated_sharpe"] = robustness.deflated_sharpe(
            ordered, len(cells) + int(sweep.get("trials_before") or 0), trial_sharpes=sharpes,
        )
    return found


def _report(simulator: Simulator, days: list[dict], capital: float, signals: list[dict]) -> dict:
    tested = [day for day in days if day["status"] == TESTED]
    tested_days = {day["day"] for day in tested}
    trades = [trade for trade in simulator.trades if trade["day"] in tested_days]
    return {
        "figures": figures.trade_figures(
            trades, [day["day_percent"] for day in tested], sum(1 for day in tested if day["entries"]),
        ),
        "money": figures.money(trades, days, capital, signals),
        "curve": figures.curve(tested),
    }


async def run(arguments) -> int:
    folder = arguments.run
    try:
        request = _read(os.path.join(folder, "request.json"))
        resolved = _read(os.path.join(folder, "settings.json"))
    except (OSError, ValueError) as e:
        raise NotARun(f"The run's folder cannot be read: {e}") from None
    settings = BacktestSettings.from_dict(resolved["settings"])
    clock = TradingClock(settings.timezone)
    first_day, last_day = date.fromisoformat(request["from"]), date.fromisoformat(request["to"])
    days = days_between(first_day, last_day)
    start, end = clock.day_bounds(first_day)[0], clock.day_bounds(last_day)[1]
    progress = Progress(folder)
    source = request["source"]
    pine = source["kind"] == "pine"
    indice = request["indice"]
    sweep = request.get("sweep")
    axes = [Axis(axis["kind"], axis["name"], tuple(axis["values"])) for axis in sweep["axes"]] if sweep else []
    command = arguments.runner.split("\x1f") if arguments.runner else None

    config = _config()
    try:
        data, rows, database = await _sources(arguments, config, clock)
    except NoCalibration as e:
        raise NotARun(str(e)) from None
    warnings: list[str] = []
    try:
        # ── 1. the signals ──
        progress("signals", 0, 1, force=True)
        signal_sets: dict[tuple, list] = {}
        not_signals: dict[tuple, list] = {}
        strategy_bars: Counter = Counter()
        engine: dict = {}
        input_sets = sorted({cell_inputs(axes, values) for values in cells_of(axes)}, key=repr) if axes else [()]
        for number, inputs in enumerate(input_sets):
            if pine:
                rule = with_inputs(source["rule"], inputs)
                result = await pine_signals(
                    rule, _engine_rule(folder, rule), rows, settings, clock, start, end, node=arguments.node,
                    command=command, progress=lambda done, total, day: progress("signals", number * total + done,
                                                                                  len(input_sets) * total, day),
                    cache=arguments.signal_cache, on_window=progress.runner_day,
                )
                print(f"Signals of input set {number + 1}: {result.run_windows} days run, {result.cached_windows} read back", flush=True)
                signal_sets[inputs] = result.events
                not_signals[inputs] = result.not_signals
                if not strategy_bars:
                    strategy_bars = result.strategy_bars
                warnings += [warning for warning in result.warnings if warning not in warnings]
                engine = {"runner": result.versions, "milliseconds": engine.get("milliseconds", 0) + result.milliseconds,
                          "history_days": settings.history_days, "restart_time": settings.restart_time}
            elif source["kind"] == "ta_signals":
                # The signals the rule raised live, as the engine journaled them: no script to run again.
                signal_sets[inputs] = raised_signals(_read(os.path.join(folder, "ta_signals.json")), indice, settings, start, end)
                not_signals[inputs] = []
            else:
                journal = _read(os.path.join(folder, "journal.json"))
                signal_sets[inputs] = journal_signals(journal, indice, settings, start, end)
                not_signals[inputs] = []
        next_id = 1 + max((event.id for events in signal_sets.values() for event in events), default=0)
        next_id = max(next_id, 1 + max((row["id"] for rows_ in not_signals.values() for row in rows_), default=0))
        closing = closing_signals(days, settings, clock, first_id=next_id, market_calendar=us_market_calendar())
        closing_by_day = by_day(closing, clock)

        # ── 2. the days ──
        # WATA's own orders, which a run on measured fills draws each order from.
        execution_path = os.path.join(folder, EXECUTION_FILE)
        execution = _read(execution_path) if os.path.exists(execution_path) else None
        samples = (execution or {}).get("samples")
        seed = int((request.get("baseline") or {}).get("seed") or DEFAULT_SEED)
        try:
            if axes:
                cells = []
                for values in cells_of(axes):
                    cell_rules = cell_settings(settings, axes, values)
                    events = signal_sets[cell_inputs(axes, values)]
                    simulator = Simulator(cell_rules, clock, fills=fills_for(cell_rules, samples, f"{seed}:run", base=settings))
                    cells.append({"values": list(values), "simulator": simulator, "events": by_day(events, clock)})
                main, baseline = None, None
            else:
                events = signal_sets[()]
                main = Simulator(settings, clock, fills=fills_for(settings, samples, f"{seed}:run"))
                cells = [{"values": [], "simulator": main, "events": by_day(events, clock)}]
                baseline_request = request.get("baseline") or {}
                repetitions = int(baseline_request.get("repetitions") or 0)
                hours = entry_hours(events, clock)
                # Without an entry of its own, a strategy has nothing to be set against.
                baseline = Baseline(settings, clock, repetitions, seed, hours, samples=samples) \
                    if repetitions > 0 and hours is not None else None
        except ValueError as e:
            raise NotARun(str(e)) from None
        entries_by_day = Counter(
            day for day, events in cells[0]["events"].items() for event in events if event.action in ENTRY_ACTIONS
        )
        records: list[dict] = []
        cell_days: list[list[dict]] = [[] for _ in cells]
        # How the asset moved after each entry signal, taken or not: a run's, not a sweep's.
        edge = EdgeMeter(clock) if not axes else None
        # What kind of day each day was, from the chart's asset in the rules' trading hours.
        calendar = regimes.read_calendar()
        hours_of = lambda one_day: regimes.session(one_day, clock, settings.trading_start_hour, settings.trading_end_hour)  # noqa: E731
        previous_close = None
        for back in range(1, 6):
            before = first_day - timedelta(days=back)
            before_start, before_end = clock.day_bounds(before)
            previous_close = regimes.session_close(
                await _asset_series(rows, request.get("chart"), before_start, before_end), hours_of(before),
            )
            if previous_close is not None:
                break
        features: dict[str, dict] = {}
        for place, day in enumerate(days):
            progress("days", place, len(days), day)
            day_start, day_end = clock.day_bounds(day)
            recorded = await data.day(indice, day, day_start, day_end)
            recorded.asset = await _asset_series(rows, request.get("chart"), day_start, day_end)
            key = day.isoformat()
            closing_today = closing_by_day.get(key, [])
            for number, cell in enumerate(cells):
                record = cell["simulator"].run_day(recorded, cell["events"].get(key, []) + closing_today)
                record["status"] = _day_status(record, pine=pine, strategy_bars=strategy_bars.get(key, 0),
                                               weekend=day.weekday() >= 5)
                record["strategy_bars"] = strategy_bars.get(key, 0) if pine else None
                cell_days[number].append(record)
            if baseline is not None and cell_days[0][-1]["status"] == TESTED:
                baseline.run_day(recorded, entries_by_day.get(key, 0), closing_today)
            if edge is not None:
                edge.add_day(key, recorded.asset, cells[0]["events"].get(key, []))
            features[key] = regimes.day_features(recorded.asset, previous_close, day, hours_of(day), calendar.on(day))
            previous_close = regimes.session_close(recorded.asset, hours_of(day)) or previous_close
            del recorded
        records = cell_days[0]
        regimes.label_days([features[key] for key in sorted(features)])
        for cell_records in cell_days:
            for record in cell_records:
                record["regime"] = features.get(record["day"])
        known_until = calendar.known_until()
        if known_until is not None and last_day > known_until:
            warnings.append(f"The macro calendar ends on {known_until.isoformat()}: later days have no news marked")

        # ── 3. the report ──
        progress("report", 0, 1, force=True)
        made_with = {
            "format": FORMAT,
            "version": _version(),
            "code": figures.code_digest(),
            "engine": engine or None,
            # Not part of the code: the news a day was known by, and the US market's holidays and early closes.
            "calendar": {"digest": calendar.digest, "until": known_until.isoformat() if known_until else None},
            "market_calendar": {"digest": us_market_calendar().digest, "until": us_market_calendar().until.isoformat()
                                if us_market_calendar().until else None},
            # How its orders were filled: the settings' values, or drawn from WATA's own orders of a period.
            "execution": {"kind": settings.execution} if settings.execution != MEASURED or execution is None else {
                "kind": MEASURED, "from": execution.get("from"), "to": execution.get("to"),
                "orders": len((samples or {}).get("order_seconds") or []),
                "fills": {kind: len((samples or {}).get(kind) or []) for kind in ("slippage_buy", "slippage_sell")},
            },
            "turbos": {"kind": "recorded"} if not isinstance(data, ModelData) else {
                "kind": "modelled", "made_at": data.calibration.get("made_at"), "underlying": data.calibration.get("underlying"),
                "days": len(data.calibration.get("days") or []), "few_days": bool(data.calibration.get("few_days")),
                "error_percent": {direction: measured.get("error_percent")
                                  for direction, measured in data.calibration["directions"].items()},
            },
        }
        tested = [record["day"] for record in records if record["status"] == TESTED]
        statuses = Counter(record["status"] for record in records)
        if axes:
            first_held = held_out_from(tested, float(sweep.get("held_out") or 0.3))
            out_cells = []
            for cell, cell_records in zip(cells, cell_days):
                simulator = cell["simulator"]
                parts = {}
                for name, keep in (
                    ("tuning", lambda day: first_held is None or day < first_held),
                    ("held_out", lambda day: first_held is not None and day >= first_held),
                    ("whole", lambda day: True),
                ):
                    chosen = [record for record in cell_records if record["status"] == TESTED and keep(record["day"])]
                    chosen_days = {record["day"] for record in chosen}
                    trades = [trade for trade in simulator.trades if trade["day"] in chosen_days]
                    parts[name] = figures.trade_figures(
                        trades, [record["day_percent"] for record in chosen],
                        sum(1 for record in chosen if record["entries"]), with_range=False,
                    ) if chosen else None
                tested_records = [record for record in cell_records if record["status"] == TESTED]
                out_cells.append({
                    "values": cell["values"], **parts,
                    "money": figures.money(simulator.trades, cell_records, settings.capital, simulator.signals),
                    "signals": figures.signal_tallies(simulator.signals),
                    # Its result day by day, for the windows of a walk-forward and the overfitting figures.
                    "curve": [[record["day"], record["day_percent"], record["trades"]] for record in tested_records],
                })
            _write(os.path.join(folder, "sweep.json"), {
                "axes": [{"kind": axis.kind, "name": axis.name, "values": list(axis.values)} for axis in axes],
                "held_out": float(sweep.get("held_out") or 0.3),
                "held_out_from": first_held,
                "cells": out_cells,
            })
            _write(os.path.join(folder, "days.json"), records)
            summary = {
                "made_with": made_with,
                "days": {"asked": len(days), "tested": len(tested), "by_status": dict(statuses)},
                "cells": len(out_cells),
                # A stress sweep chooses nothing: there is no choice to measure the fitting of.
                **({} if request.get("stress_of") else sweep_robustness(out_cells, sweep, first_held)),
                "warnings": warnings,
            }
        else:
            signals = sorted(
                main.signals + not_signals[()],
                key=lambda record: (record["day"], record.get("arrived_at") or record.get("bar_closed_at") or "", record["id"]),
            )
            trades = sorted(main.trades, key=lambda trade: (trade["entry"]["filled_at"], trade["id"]))
            for trade in trades:
                opened = datetime.strptime(trade["entry"]["filled_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                trade["minutes_after_open"] = regimes.minutes_after_open(opened.timestamp(), date.fromisoformat(trade["day"]))
                trade["session_part"] = regimes.session_part(trade["minutes_after_open"])
            report = _report(main, records, settings.capital, signals)
            day_percents = [record["day_percent"] for record in records if record["status"] == TESTED]
            totals = baseline.totals(set(tested)) if baseline is not None else []
            against = figures.against_random(
                report["figures"]["total_percent"], totals, seed=baseline.seed, entries=baseline.entries,
            ) if baseline is not None and totals else None
            edge_report = edge.report(
                {signal["id"]: signal["fate"] for signal in signals if "fate" in signal},
                {key: found.get("labels") for key, found in features.items()},
            )
            _write(os.path.join(folder, "signals.json"), signals)
            _write(os.path.join(folder, "trades.json"), trades)
            _write(os.path.join(folder, "days.json"), records)
            _write(os.path.join(folder, "edge.json"), edge_report)
            reconciled = None
            if not pine and os.path.exists(os.path.join(folder, "positions.json")):
                # The same signals as WATA received them, beside what WATA did with them.
                reconciled = reconcile(
                    trades, signals, _read(os.path.join(folder, "journal.json")), _read(os.path.join(folder, "positions.json")),
                )
                _write(os.path.join(folder, "reconciliation.json"), reconciled)
            model_check = None
            if request.get("model_check_of") and os.path.exists(os.path.join(folder, RECORDED_TRADES_FILE)):
                # The same signals as the run it checks traded them on the recorded turbos, beside these made-up ones.
                model_check = compare_trades(_read(os.path.join(folder, RECORDED_TRADES_FILE)), trades)
                _write(os.path.join(folder, MODEL_CHECK_FILE), model_check)
            if baseline is not None:
                _write(os.path.join(folder, "baseline.json"), {
                    "repetitions": len(totals), "seed": baseline.seed, "entries": baseline.entries,
                    "hours": baseline.hours, "totals": [round(total, 2) for total in totals],
                    "trades": baseline.trades(), "histogram": figures.histogram(totals),
                })
            summary = {
                "made_with": made_with,
                "days": {
                    "asked": len(days), "tested": len(tested), "by_status": dict(statuses),
                    "with_an_entry": sum(1 for record in records if record["status"] == TESTED and record["entries"]),
                },
                "signals": figures.signal_tallies(signals),
                **report,
                "against_random": against,
                "edge": edge_headline(edge_report),
                "reconciliation": {**reconciled["summary"], "sentence": reconciliation_sentence(reconciled["summary"])}
                if reconciled else None,
                "model_check": model_check,
                "stress": {
                    "monte_carlo": robustness.monte_carlo_drawdown(day_percents),
                    "without_best_days": robustness.without_best_days(day_percents),
                },
                "warnings": warnings,
            }
        _write(os.path.join(folder, "summary.json"), summary)
        progress("done", 1, 1, force=True)
        return 0
    finally:
        await data.close()


def _version() -> str:
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "VERSION")
    try:
        with open(path, encoding="utf-8") as in_file:
            return in_file.read().strip()
    except OSError:
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    arguments = _parse(sys.argv[1:] if argv is None else argv)
    if arguments.low_priority:
        lower_priority()
    try:
        return asyncio.run(run(arguments))
    except NotARun as e:
        print(str(e), file=sys.stderr)
        return 2
    except Exception as e:
        traceback.print_exc()
        print(f"The backtest failed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
