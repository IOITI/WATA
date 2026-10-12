"""
Replay a strategy on prices of the past, with WATA's own rules.

    python -m src.strategy.backtest --from 2026-09-14 --to 2026-10-12
    python -m src.strategy.backtest --from 2026-09-14 --to 2026-10-12 --set entry_mode=cross_catchup --out var/lib/backtests/catchup

Days are those of the trading timezone, both included. The prices are the
index fetched from Dukascopy (src/strategy/history/dukascopy.py) unless
``--source`` names a file or a directory of ``wataprices`` exports. The rules
are those of the configuration given (``--config``, else ``WATA_CONFIG_PATH``,
else the example configuration).

The turbo is a model whose leverage is an assumption (see ``TurboModel``), so
the backtest is run once per value of ``--points-per-unit`` and the results
are shown side by side.
"""

import argparse
import csv
import json
import os
import sys
import time
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone

from src.price_recorder.bars import BAR_SECONDS
from src.strategy import engine, report
from src.strategy.execution import Simulator, TurboModel, WataRules
from src.strategy.history.dukascopy import DEFAULT_ROOT, SOURCE as DUKASCOPY, bars_directory
from src.strategy.kernel_vwap import TRACE_COLUMNS, KernelVwap, KernelVwapParams
from src.strategy.sources import FileBars

EXAMPLE_CONFIG = os.path.join("etc", "config_example.json")
DEFAULT_POINTS_PER_UNIT = "80,110,140"
STRATEGIES = {"kernel_vwap": (KernelVwapParams, KernelVwap)}
# Under this share of the bars its market hours should hold, a day's prices are called incomplete.
COMPLETE_DAY_SHARE = 0.9


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
    parser.add_argument("--indice", default="us100")
    parser.add_argument("--asset-type", help="which instrument of the indice, when the files hold several")
    parser.add_argument("--config", help="WATA configuration whose rules are applied")
    parser.add_argument(
        "--points-per-unit", default=DEFAULT_POINTS_PER_UNIT,
        help=f"index points that move the turbo by 1.00, one run per value (default: {DEFAULT_POINTS_PER_UNIT})",
    )
    parser.add_argument("--entry-price", type=float, default=TurboModel.entry_price, help="price paid for a turbo")
    parser.add_argument("--spread", type=float, default=TurboModel.spread, help="ask minus bid of the turbo")
    parser.add_argument("--out", help="directory to write the trades, the days and the summary to")
    parser.add_argument("--trace", help="file to write one line per range bar to (kernel, VWAP, band)")
    arguments = parser.parse_args(argv)
    arguments.last_day = arguments.last_day or arguments.first_day
    if arguments.last_day < arguments.first_day:
        parser.error("--to is before --from")
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


def run(arguments: argparse.Namespace) -> int:
    config_path = arguments.config or os.getenv("WATA_CONFIG_PATH") or EXAMPLE_CONFIG
    with open(config_path, encoding="utf-8") as config_file:
        rules = WataRules.from_config(json.load(config_file))
    clock = engine.LocalClock(rules.timezone)

    params_type, strategy_type = STRATEGIES[arguments.strategy]
    try:
        params = params_type().with_overrides(arguments.parameters)
        turbos = [
            TurboModel(entry_price=arguments.entry_price, points_per_unit=leverage, spread=arguments.spread)
            for leverage in arguments.leverages
        ]
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2

    location = arguments.source
    if location == DUKASCOPY:
        location = bars_directory(arguments.root, arguments.indice)
    if not os.path.exists(location):
        print(f"No prices at {location}. Fetch some with: python -m src.strategy.history.dukascopy --from ... --to ...", file=sys.stderr)
        return 2

    start = clock.zone.localize(datetime.combine(arguments.first_day, datetime.min.time()))
    end = clock.zone.localize(datetime.combine(arguments.last_day + timedelta(days=1), datetime.min.time()))
    bars = FileBars(location, arguments.indice, arguments.asset_type).bars(start, end)

    trace_rows: list[dict] = []
    strategy = strategy_type(params, clock, trace=trace_rows.append if arguments.trace else None)
    simulators = [Simulator(rules, turbo, clock) for turbo in turbos]
    bar_count = 0

    def on_bar(bar) -> None:
        nonlocal bar_count
        bar_count += 1
        for simulator in simulators:
            simulator.on_bar(bar)

    def on_decision(decision) -> None:
        for simulator in simulators:
            simulator.decide(decision)

    started = time.monotonic()
    decisions = engine.run(strategy, bars, clock, on_bar=on_bar, on_decision=on_decision)
    for simulator in simulators:
        simulator.finish()
    if bar_count == 0:
        print(f"No bar of {arguments.indice} between {arguments.first_day} and {arguments.last_day} in {location}.", file=sys.stderr)
        return 2

    summaries = [(_label(turbo.points_per_unit), report.summarize(simulator)) for turbo, simulator in zip(turbos, simulators)]
    changed = {name: value for name, value in asdict(params).items() if value != getattr(params_type(), name)}
    print(f"{arguments.strategy} from {arguments.first_day} to {arguments.last_day} ({rules.timezone}), prices: {location}")
    print(f"Rules: {config_path}. Parameters changed: {changed or 'none'}.")
    print(
        f"{bar_count} bars, {len(decisions)} change(s) of target, {time.monotonic() - started:.0f} s. "
        f"Turbo bought at {arguments.entry_price:g}, spread {arguments.spread:g}; one column per leverage assumed "
        f"(index points for 1.00 of turbo)."
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
        for turbo, simulator in zip(turbos, simulators):
            name = f"{turbo.points_per_unit:g}"
            report.write_trades(os.path.join(arguments.out, f"trades_{name}.csv"), simulator.trades)
            report.write_days(os.path.join(arguments.out, f"days_{name}.csv"), report.market_days(simulator.days))
        with open(os.path.join(arguments.out, "summary.json"), "w", encoding="utf-8") as out_file:
            json.dump({
                "strategy": arguments.strategy,
                "from": arguments.first_day.isoformat(),
                "to": arguments.last_day.isoformat(),
                "prices": location,
                "config": config_path,
                "parameters": asdict(params),
                "rules": {**asdict(rules), "closed_dates": sorted(rules.closed_dates)},
                "runs": [
                    {"turbo": asdict(turbo), "summary": summary} for turbo, (_, summary) in zip(turbos, summaries)
                ],
            }, out_file, indent=2)
        print(f"\nTrades, days and summary written to {arguments.out}")

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
