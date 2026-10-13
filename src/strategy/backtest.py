"""
Replay a strategy on prices of the past, with WATA's own rules.

    python -m src.strategy.backtest --from 2026-09-14 --to 2026-10-12
    python -m src.strategy.backtest --from 2026-09-14 --to 2026-10-12 --set entry_mode=cross_catchup --out var/lib/backtests/catchup

Days are those of the trading timezone, both included. The prices are the
index fetched from Dukascopy (src/strategy/history/dukascopy.py) unless
``--source`` names a file or a directory of ``wataprices`` exports. The rules
are those of the configuration given (``--config``, else ``WATA_CONFIG_PATH``,
else the example configuration), or a set of rules written out (``--rules``).

The turbo is a model whose leverage is an assumption (see ``TurboModel``), so
the backtest is run once per value of ``--points-per-unit`` and the results
are shown side by side. ``python -m src.strategy.calibration`` measures it on
real trades.

Every backtest is set against entries taken at random under the same rules
(src/strategy/baseline.py): ``--baseline 0`` leaves that out.

``--signals`` replays signals WATA really received, in place of a strategy
(src/strategy/signals.py).

The admin interface runs this same command for each backtest it is asked for
(src/admin/backtests.py), and reads what ``--out`` wrote.
"""

import argparse
import csv
import json
import os
import sys
import time
from dataclasses import asdict, fields
from datetime import date, datetime, timedelta, timezone

from src.price_recorder.bars import BAR_SECONDS
from src.strategy import engine, report, signals
from src.strategy.baseline import DEFAULT_REPETITIONS, DEFAULT_SEED, Baseline
from src.strategy.execution import Execution, Simulator, TurboModel, WataRules
from src.strategy.fingerprint import code_fingerprint, prices_fingerprint
from src.strategy.history.dukascopy import DEFAULT_ROOT, SOURCE as DUKASCOPY, bars_directory
from src.strategy.kernel_vwap import TRACE_COLUMNS, KernelVwap, KernelVwapParams
from src.strategy.process import lower_priority
from src.strategy.sources import FileBars

EXAMPLE_CONFIG = os.path.join("etc", "config_example.json")
DEFAULT_POINTS_PER_UNIT = "80,110,140"
DEFAULT_INDICE = "us100"
STRATEGIES = {"kernel_vwap": (KernelVwapParams, KernelVwap)}
# The account the result is given in money for, unless another is named.
DEFAULT_CAPITAL = 10000.0
# What a run of real signals is called where a strategy's name would be.
SIGNALS = "signals"
_DAY_SECONDS = 86400
# Under this share of the bars its market hours should hold, a day's prices are called incomplete.
COMPLETE_DAY_SHARE = 0.9


def describe_parameters(strategy: str) -> list[dict]:
    """The parameters of a strategy as a form needs them: name, kind, default, bounds or choices, and help."""
    params_type, _ = STRATEGIES[strategy]
    defaults = params_type()
    return [
        {
            "name": spec.name,
            "kind": {int: "integer", float: "number"}.get(type(getattr(defaults, spec.name)), "text"),
            "default": getattr(defaults, spec.name),
            "help": spec.metadata["help"],
            "least": spec.metadata["least"],
            "most": spec.metadata["most"],
            "choices": list(spec.metadata["choices"]),
            "advanced": spec.metadata["advanced"],
        }
        for spec in fields(params_type)
    ]


def price_location(source: str, root: str, indice: str) -> str:
    """Where the bars are: what was fetched from Dukascopy under ``root``, or the file or directory named."""
    return bars_directory(root, indice) if source == DUKASCOPY else source


def day_bounds(clock: engine.LocalClock, first_day: date, last_day: date) -> tuple[datetime, datetime]:
    """From the start of ``first_day`` to the end of ``last_day``, in the trading timezone."""
    start = clock.zone.localize(datetime.combine(first_day, datetime.min.time()))
    end = clock.zone.localize(datetime.combine(last_day + timedelta(days=1), datetime.min.time()))
    return start, end


def replay_day(
    strategy: str,
    parameters: dict,
    clock: engine.LocalClock,
    location: str,
    day: date,
    *,
    indice: str = DEFAULT_INDICE,
    asset_type: str | None = None,
) -> tuple[list[dict], list[engine.Decision]]:
    """
    One day as the strategy saw it: every range bar with what was measured on
    it (``TRACE_COLUMNS``), and each change of target. For a chart of the day.
    """
    params_type, strategy_type = STRATEGIES[strategy]
    params = params_type().with_overrides({name: str(value) for name, value in parameters.items()})
    rows: list[dict] = []
    start, end = day_bounds(clock, day, day)
    bars = FileBars(location, indice, asset_type).bars(start, end)
    decisions = engine.run(strategy_type(params, clock, trace=rows.append), bars, clock)
    return rows, decisions


def _write_json(path: str, content: dict) -> None:
    """In one move: whoever reads the file never finds half of it."""
    partial = f"{path}.part"
    with open(partial, "w", encoding="utf-8") as out_file:
        json.dump(content, out_file, indent=2)
    os.replace(partial, path)


def parse_arguments(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m src.strategy.backtest",
        description="Replay a strategy on prices of the past, with WATA's rules.",
    )
    parser.add_argument("--from", dest="first_day", required=True, type=date.fromisoformat, help="first day, YYYY-MM-DD")
    parser.add_argument("--to", dest="last_day", type=date.fromisoformat, help="last day included (default: the first day)")
    parser.add_argument("--strategy", default="kernel_vwap", choices=sorted(STRATEGIES))
    parser.add_argument(
        "--set", dest="overrides", action="append", default=[], metavar="NAME=VALUE",
        help="a parameter of the strategy, e.g. entry_mode=cross_catchup (can be repeated)",
    )
    parser.add_argument(
        "--source", default=DUKASCOPY,
        help=f"'{DUKASCOPY}' (what was fetched under --root), or a file or directory of wataprices exports",
    )
    parser.add_argument("--root", default=DEFAULT_ROOT, help=f"where fetched history is kept (default: {DEFAULT_ROOT})")
    parser.add_argument("--indice", default=DEFAULT_INDICE)
    parser.add_argument("--asset-type", help="which instrument of the indice, when the files hold several")
    rules = parser.add_mutually_exclusive_group()
    rules.add_argument("--config", help="WATA configuration whose rules are applied")
    rules.add_argument("--rules", help="rules written out as JSON (WataRules.to_dict), instead of a configuration")
    parser.add_argument(
        "--points-per-unit", default=DEFAULT_POINTS_PER_UNIT,
        help=f"index points that move the turbo by 1.00, one run per value (default: {DEFAULT_POINTS_PER_UNIT})",
    )
    parser.add_argument(
        "--signals", help="replay the signals of this file (a JSON list: at, action, confidence) instead of a strategy",
    )
    parser.add_argument(
        "--baseline", type=int, default=DEFAULT_REPETITIONS, metavar="REPETITIONS",
        help=f"times the same days are traded with entries at random, to compare (default: {DEFAULT_REPETITIONS}; 0: not at all)",
    )
    parser.add_argument("--baseline-seed", type=int, default=DEFAULT_SEED, help="what the random entries are drawn from")
    parser.add_argument("--entry-price", type=float, default=TurboModel.entry_price, help="price paid for a turbo")
    parser.add_argument("--spread", type=float, default=TurboModel.spread, help="ask minus bid of the turbo")
    parser.add_argument(
        "--fill-delay", type=int, default=0, metavar="SECONDS",
        help="wait between a decision and its fill, on top of the next ten seconds (default: 0)",
    )
    parser.add_argument(
        "--slippage", type=float, default=0.0, metavar="POINTS",
        help="index points lost against the trade on each fill (default: 0)",
    )
    parser.add_argument(
        "--capital", type=float, default=DEFAULT_CAPITAL,
        help=f"account the result is also given in money for, with WATA's position sizing (default: {DEFAULT_CAPITAL:g}; 0: not at all)",
    )
    parser.add_argument("--out", help="directory to write the trades, the days, the refusals and the summary to")
    parser.add_argument("--trace", help="file to write one line per range bar to (prices, kernel, VWAP, band)")
    parser.add_argument("--progress", help="file kept up to date with the day being replayed")
    parser.add_argument("--low-priority", action="store_true", help="give way to everything else on the machine")
    arguments = parser.parse_args(argv)
    arguments.last_day = arguments.last_day or arguments.first_day
    if arguments.last_day < arguments.first_day:
        parser.error("--to is before --from")
    if arguments.baseline < 0:
        parser.error("--baseline is a number of repetitions, 0 or more")
    if arguments.fill_delay < 0 or arguments.slippage < 0 or arguments.capital < 0:
        parser.error("--fill-delay, --slippage and --capital are 0 or more")
    if arguments.signals and (arguments.overrides or arguments.trace):
        parser.error("--signals replays signals: there is no strategy to give a parameter to, or to trace")
    arguments.parameters = {}
    for override in arguments.overrides:
        name, separator, value = override.partition("=")
        if not separator or not name:
            parser.error(f"--set needs NAME=VALUE, not '{override}'")
        arguments.parameters[name] = value
    try:
        arguments.leverages = [float(value) for value in arguments.points_per_unit.split(",")]
    except ValueError:
        parser.error("--points-per-unit needs numbers separated by commas")
    return arguments


def _label(points_per_unit: float) -> str:
    return f"{points_per_unit:g} pts"


def _read_rules(arguments: argparse.Namespace) -> tuple[WataRules, str]:
    """The rules to apply, and where they came from."""
    if arguments.rules:
        with open(arguments.rules, encoding="utf-8") as rules_file:
            return WataRules.from_dict(json.load(rules_file)), arguments.rules
    config_path = arguments.config or os.getenv("WATA_CONFIG_PATH") or EXAMPLE_CONFIG
    with open(config_path, encoding="utf-8") as config_file:
        return WataRules.from_config(json.load(config_file)), config_path


def run(arguments: argparse.Namespace) -> int:
    if arguments.low_priority:
        lower_priority()

    params_type, strategy_type = STRATEGIES[arguments.strategy]
    recorded = None
    try:
        rules, rules_path = _read_rules(arguments)
        params = params_type().with_overrides(arguments.parameters)
        turbos = [
            TurboModel(entry_price=arguments.entry_price, points_per_unit=leverage, spread=arguments.spread)
            for leverage in arguments.leverages
        ]
        if arguments.signals:
            recorded = signals.read(arguments.signals)
        execution = Execution(fill_delay=arguments.fill_delay, slippage=arguments.slippage)
    except (OSError, ValueError, TypeError) as e:
        print(e, file=sys.stderr)
        return 2
    clock = engine.LocalClock(rules.timezone)

    location = price_location(arguments.source, arguments.root, arguments.indice)
    if not os.path.exists(location):
        print(f"No prices at {location}. Fetch some with: python -m src.strategy.history.dukascopy --from ... --to ...", file=sys.stderr)
        return 2

    start, end = day_bounds(clock, arguments.first_day, arguments.last_day)
    source = FileBars(location, arguments.indice, arguments.asset_type)
    bars = source.bars(start, end)
    # Taken before they are read: what the result will have been made with.
    made_with = {"code": code_fingerprint(), "price_files": prices_fingerprint(source.files(start, end))}

    trace_rows: list[dict] = []
    replaying_signals = recorded is not None
    strategy = None if replaying_signals else strategy_type(params, clock, trace=trace_rows.append if arguments.trace else None)
    simulators = [
        Simulator(rules, turbo, clock, entries_are_orders=replaying_signals, execution=execution) for turbo in turbos
    ]
    baseline = None
    if arguments.baseline:
        baseline = Baseline(
            rules, turbos, clock,
            # Signals come at any time: the trader's hours alone bound a random one.
            window=(0, _DAY_SECONDS) if replaying_signals else strategy.entry_window(),
            repetitions=arguments.baseline, seed=arguments.baseline_seed, entries_are_orders=replaying_signals,
            execution=execution,
        )
    bar_count = 0
    replayed_day: date | None = None
    days_begun = 0
    # The day being replayed, kept until it is over: the random entries are drawn in it then.
    day_bars: list = []
    day_entries = 0

    def on_bar(bar) -> None:
        nonlocal bar_count, replayed_day, days_begun, day_bars, day_entries
        day, _ = clock.at(bar.start)
        if day != replayed_day:
            if baseline is not None:
                baseline.day(day_bars, day_entries)
                day_bars, day_entries = [], 0
            replayed_day = day
            days_begun += 1
            if arguments.progress:
                _write_json(arguments.progress, {"day": day.isoformat(), "days_begun": days_begun, "bars": bar_count + 1})
        bar_count += 1
        if baseline is not None:
            day_bars.append(bar)
        for simulator in simulators:
            simulator.on_bar(bar)

    def on_decision(decision) -> None:
        nonlocal day_entries
        if decision.target != engine.FLAT:
            day_entries += 1
        for simulator in simulators:
            simulator.decide(decision)

    started = time.monotonic()
    without_prices: list = []
    if replaying_signals:
        first_moment, last_moment = start.timestamp(), end.timestamp()
        in_period = [signal for signal in recorded if first_moment <= signal.at < last_moment]
        decisions, without_prices = signals.run(in_period, bars, on_bar=on_bar, on_decision=on_decision)
    else:
        decisions = engine.run(strategy, bars, clock, on_bar=on_bar, on_decision=on_decision)
    for simulator in simulators:
        simulator.finish()
    if bar_count == 0:
        print(f"No bar of {arguments.indice} between {arguments.first_day} and {arguments.last_day} in {location}.", file=sys.stderr)
        return 2
    if baseline is not None:
        baseline.day(day_bars, day_entries)
        baseline.finish()
    seconds = round(time.monotonic() - started, 1)

    summaries = []
    amounts = []
    for number, (turbo, simulator) in enumerate(zip(turbos, simulators)):
        summary = report.summarize(simulator)
        summary["against_random"] = baseline.against(number, summary["total_percent"]) if baseline is not None else None
        # The same trades at the size WATA would have given each, on an account of that capital.
        amounts.append(report.sized(simulator.trades, rules, turbo, arguments.capital) if arguments.capital else None)
        summary["money"] = report.money(simulator.trades, amounts[-1], arguments.capital) if arguments.capital else None
        summaries.append((_label(turbo.points_per_unit), summary))
    changed = {name: value for name, value in asdict(params).items() if value != getattr(params_type(), name)}
    if replaying_signals:
        print(f"Signals of {arguments.signals} from {arguments.first_day} to {arguments.last_day} ({rules.timezone}), prices: {location}")
        print(
            f"Rules: {rules_path}. {len(decisions) + len(without_prices)} signal(s) in the period, "
            f"{len(without_prices)} of them at a moment without prices."
        )
    else:
        print(f"{arguments.strategy} from {arguments.first_day} to {arguments.last_day} ({rules.timezone}), prices: {location}")
        print(f"Rules: {rules_path}. Parameters changed: {changed or 'none'}.")
    print(
        f"{bar_count} bars, {len(decisions)} {'signal(s) replayed' if replaying_signals else 'change(s) of target'}, {seconds:.0f} s. "
        f"Turbo bought at {arguments.entry_price:g}, spread {arguments.spread:g}; one column per leverage assumed "
        f"(index points for 1.00 of turbo)."
    )
    if arguments.fill_delay or arguments.slippage:
        print(
            f"Fills {arguments.fill_delay} s after the next ten seconds, "
            f"{arguments.slippage:g} index point(s) lost on each."
        )
    if baseline is not None:
        print(
            f"Set against {arguments.baseline} repetitions of entries at random under the same rules "
            f"(\"Random entries beaten\": the share of them that did worse)."
        )
    tested = report.market_days(simulators[0].days)
    with_prices = {day.day for day in tested}
    period = [arguments.first_day + timedelta(days=offset) for offset in range((arguments.last_day - arguments.first_day).days + 1)]
    without = [day for day in period if day.weekday() < 5 and day not in with_prices]
    if without:
        print(
            f"No prices for {len(without)} weekday(s) of the period (not fetched, or the market was closed): "
            + ", ".join(day.isoformat() for day in without)
        )
    full_day = (rules.trading_end_hour - rules.trading_start_hour) * 3600 // BAR_SECONDS
    thin = [day.day for day in tested if day.market_bars < COMPLETE_DAY_SHARE * full_day]
    if thin:
        print(
            f"Prices incomplete for {len(thin)} day(s), whose results cover only part of the market hours: "
            + ", ".join(day.isoformat() for day in thin)
        )
    print()
    print(report.format_summaries(summaries))

    if arguments.out:
        os.makedirs(arguments.out, exist_ok=True)
        for turbo, simulator, sized in zip(turbos, simulators, amounts):
            name = f"{turbo.points_per_unit:g}"
            report.write_trades(os.path.join(arguments.out, f"trades_{name}.csv"), simulator.trades, sized)
            report.write_days(
                os.path.join(arguments.out, f"days_{name}.csv"), report.market_days(simulator.days),
                None if sized is None else report.profit_by_day(simulator.trades, sized, clock), arguments.capital,
            )
            report.write_refusals(os.path.join(arguments.out, f"refusals_{name}.csv"), simulator.refusals)
        _write_json(os.path.join(arguments.out, "summary.json"), {
            "strategy": SIGNALS if replaying_signals else arguments.strategy,
            "signals": {
                "replayed": len(decisions),
                "without_prices": [report.utc(signal.at) for signal in without_prices],
            } if replaying_signals else None,
            "from": arguments.first_day.isoformat(),
            "to": arguments.last_day.isoformat(),
            "prices": location,
            **made_with,
            "execution": {**asdict(execution), "capital": arguments.capital},
            "rules_from": rules_path,
            "parameters": {} if replaying_signals else asdict(params),
            "parameters_changed": {} if replaying_signals else changed,
            "rules": rules.to_dict(),
            "bars": bar_count,
            "decisions": len(decisions),
            "seconds": seconds,
            "days_without_prices": [day.isoformat() for day in without],
            "days_incomplete": [day.isoformat() for day in thin],
            "runs": [
                {"turbo": asdict(turbo), "summary": summary} for turbo, (_, summary) in zip(turbos, summaries)
            ],
        })
        print(f"\nTrades, days, refusals and summary written to {arguments.out}")

    if arguments.trace:
        os.makedirs(os.path.dirname(os.path.abspath(arguments.trace)), exist_ok=True)
        with open(arguments.trace, "w", newline="", encoding="utf-8") as out_file:
            writer = csv.writer(out_file)
            writer.writerow(("ended_utc", "ended_local", *TRACE_COLUMNS[1:]))
            for row in trace_rows:
                ended = datetime.fromtimestamp(row["ended"], timezone.utc)
                writer.writerow([
                    ended.strftime("%Y-%m-%dT%H:%M:%SZ"), ended.astimezone(clock.zone).strftime("%H:%M:%S"),
                    *("" if row[name] is None else row[name] for name in TRACE_COLUMNS[1:]),
                ])
        print(f"{len(trace_rows)} range bars traced to {arguments.trace}")
    return 0


if __name__ == "__main__":
    sys.exit(run(parse_arguments()))
