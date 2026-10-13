"""
The same backtest for several values of one or two of its settings, in one
pass over the prices.

    python -m src.strategy.sweep --from 2026-06-01 --to 2026-10-09 \\
        --axis rule:day_profit_target=1,1.7,2.5,4 --axis parameter:kernel_bandwidth=8,15,25

Trying values one backtest at a time is slow, and it has a trap: the value
that did best on a period was chosen because it did best there. So the last
part of the period is **held out**: every value is replayed on all of it, but
its figures are given apart for the days before (where a value may be chosen)
and for the days held out (which had no say in the choice). A value that
shines on the first and not on the second was fitted to the first.

An axis is a parameter of the strategy or one of WATA's rules, with the values
to try. Two axes give every pair. A strategy is computed once for each set of
its parameters, whatever the number of rules tried on it: its decisions do not
depend on them. ``--signals`` sweeps rules over the signals WATA received.

One turbo, no random entries: a sweep is for finding a value, which is then
made a backtest of its own (src/strategy/backtest.py) to be read with all it
says.
"""

import argparse
import itertools
import json
import math
import os
import sys
import time
from dataclasses import asdict, dataclass, fields, replace
from datetime import date, timedelta

from src.strategy import backtest, engine, report, signals
from src.strategy.execution import DayResult, Execution, Simulator, TurboModel, WataRules
from src.strategy.fingerprint import code_fingerprint, prices_fingerprint
from src.strategy.process import lower_priority
from src.strategy.sources import FileBars

PARAMETER, RULE = "parameter", "rule"
DEFAULT_HELD_OUT = 0.3
# The share of the days that may be held out: enough to mean something, and enough left to choose on.
LEAST_HELD_OUT, MOST_HELD_OUT = 0.1, 0.5
MAX_AXES = 2
MAX_CELLS = 100
_YES, _NO = ("on", "true", "yes"), ("off", "false", "no")


@dataclass(frozen=True)
class Axis:
    # A parameter of the strategy, or a rule of WATA.
    kind: str
    name: str
    values: tuple

    @classmethod
    def parse(cls, text: str) -> "Axis":
        """ "rule:day_profit_target=1,1.7,3" """
        head, separator, listed = text.partition("=")
        kind, colon, name = head.partition(":")
        values = [value.strip() for value in listed.split(",") if value.strip()]
        if not separator or not colon or kind not in (PARAMETER, RULE) or not name or not values:
            raise ValueError(f"An axis is 'parameter:NAME=V1,V2' or 'rule:NAME=V1,V2', not '{text}'")
        return cls(kind, name, tuple(values))


def rule_value(rules: WataRules, name: str, value):
    """``value`` as that rule takes it: a number, or on and off."""
    # As the rule is declared, not as the configuration happened to write it: a stop of -15 may be tried at -12.5.
    kinds = {spec.name: spec.type for spec in fields(rules)}
    if kinds.get(name) not in (bool, int, float):
        raise ValueError(f"The rule '{name}' cannot be given several values")
    if kinds[name] is bool:
        if isinstance(value, bool):
            return value
        if str(value).lower() in _YES + _NO:
            return str(value).lower() in _YES
        raise ValueError(f"The rule '{name}' is on or off, not '{value}'")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"The rule '{name}' needs a number, not '{value}'") from None
    if kinds[name] is int:
        if number != int(number):
            raise ValueError(f"The rule '{name}' needs a whole number, not '{value}'")
        return int(number)
    return number


@dataclass
class Cell:
    """One combination of values: the rules it is replayed with, and the execution that replays it."""

    values: tuple
    rules: WataRules
    simulator: Simulator


def held_out_from(days: list[date], share: float) -> date | None:
    """The first of the days held out: the last ``share`` of the days there are. None when they cannot be split."""
    if len(days) < 2:
        return None
    count = min(len(days) - 1, max(1, math.ceil(len(days) * share)))
    return days[-count]


def _parts(cell: Cell, calendar: list[DayResult], clock: engine.LocalClock, cut: date | None) -> dict:
    """The figures of a cell on the days before ``cut``, on the days from it, and on all of them."""
    simulator = cell.simulator
    # A cell is only given the bars that matter to it: a day on which it traded nothing is a day all the same.
    own = {day.day: day for day in simulator.days}
    days = [own.get(day.day) or DayResult(day.day, market_bars=day.market_bars) for day in calendar]
    for day, reference in zip(days, calendar):
        day.market_bars = reference.market_bars

    def figures(wanted) -> dict:
        return report.summarize_parts(
            [trade for trade in simulator.trades if wanted(clock.at(trade.entry_at)[0])],
            [day for day in days if wanted(day.day)],
            [refusal for refusal in simulator.refusals if wanted(clock.at(refusal.at)[0])],
            with_range=False,
        )

    return {
        "tuning": figures(lambda day: cut is None or day < cut),
        "held_out": figures(lambda day: day >= cut) if cut is not None else None,
        "whole": figures(lambda day: True),
    }


def parse_arguments(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m src.strategy.sweep",
        description="The same backtest for several values of one or two settings, the last part of the period held out.",
    )
    parser.add_argument("--from", dest="first_day", required=True, type=date.fromisoformat, help="first day, YYYY-MM-DD")
    parser.add_argument("--to", dest="last_day", required=True, type=date.fromisoformat, help="last day included")
    parser.add_argument(
        "--axis", dest="axes", action="append", default=[], metavar="KIND:NAME=V1,V2",
        help="a parameter of the strategy or a rule, with the values to try; once or twice",
    )
    parser.add_argument(
        "--held-out", type=float, default=DEFAULT_HELD_OUT, metavar="SHARE",
        help=f"share of the days, the last ones, that have no say in the choice (default: {DEFAULT_HELD_OUT})",
    )
    parser.add_argument("--strategy", default="kernel_vwap", choices=sorted(backtest.STRATEGIES))
    parser.add_argument("--set", dest="overrides", action="append", default=[], metavar="NAME=VALUE", help="a parameter kept the same in every cell")
    parser.add_argument("--signals", help="sweep rules over the signals of this file instead of a strategy")
    parser.add_argument("--source", default=backtest.DUKASCOPY)
    parser.add_argument("--root", default=backtest.DEFAULT_ROOT)
    parser.add_argument("--indice", default=backtest.DEFAULT_INDICE)
    parser.add_argument("--asset-type")
    rules = parser.add_mutually_exclusive_group()
    rules.add_argument("--config", help="WATA configuration whose rules are applied")
    rules.add_argument("--rules", help="rules written out as JSON (WataRules.to_dict), instead of a configuration")
    parser.add_argument("--points-per-unit", type=float, default=110.0, help="index points that move the turbo by 1.00")
    parser.add_argument("--entry-price", type=float, default=TurboModel.entry_price)
    parser.add_argument("--spread", type=float, default=TurboModel.spread)
    parser.add_argument("--fill-delay", type=int, default=0, metavar="SECONDS")
    parser.add_argument("--slippage", type=float, default=0.0, metavar="POINTS")
    parser.add_argument("--out", help="directory to write sweep.json to")
    parser.add_argument("--progress", help="file kept up to date with the day being replayed")
    parser.add_argument("--low-priority", action="store_true")
    arguments = parser.parse_args(argv)
    if arguments.last_day < arguments.first_day:
        parser.error("--to is before --from")
    if not 1 <= len(arguments.axes) <= MAX_AXES:
        parser.error(f"give one or two --axis, not {len(arguments.axes)}")
    if not LEAST_HELD_OUT <= arguments.held_out <= MOST_HELD_OUT:
        parser.error(f"--held-out is a share between {LEAST_HELD_OUT} and {MOST_HELD_OUT}")
    try:
        arguments.axes = [Axis.parse(text) for text in arguments.axes]
    except ValueError as e:
        parser.error(str(e))
    if len({(axis.kind, axis.name) for axis in arguments.axes}) != len(arguments.axes):
        parser.error("the two axes are the same setting")
    arguments.parameters = {}
    for override in arguments.overrides:
        name, separator, value = override.partition("=")
        if not separator or not name:
            parser.error(f"--set needs NAME=VALUE, not '{override}'")
        arguments.parameters[name] = value
    return arguments


def run(arguments: argparse.Namespace) -> int:
    if arguments.low_priority:
        lower_priority()

    params_type, strategy_type = backtest.STRATEGIES[arguments.strategy]
    replaying_signals = bool(arguments.signals)
    try:
        base_rules, rules_path = backtest._read_rules(arguments)
        base_params = params_type().with_overrides(arguments.parameters)
        turbo = TurboModel(entry_price=arguments.entry_price, points_per_unit=arguments.points_per_unit, spread=arguments.spread)
        execution = Execution(fill_delay=arguments.fill_delay, slippage=arguments.slippage)
        recorded = signals.read(arguments.signals) if replaying_signals else None
        if replaying_signals and any(axis.kind == PARAMETER for axis in arguments.axes):
            raise ValueError("Signals have no strategy: only rules can be given several values")
        combinations = list(itertools.product(*(axis.values for axis in arguments.axes)))
        if len(combinations) > MAX_CELLS:
            raise ValueError(f"{len(combinations)} combinations: {MAX_CELLS} at most")
        clock = engine.LocalClock(base_rules.timezone)
        cells: list[Cell] = []
        # The cells that share a set of parameters share the strategy computed with it.
        by_params: dict = {}
        # What each value was read as: a number where it is one.
        read_axes = [[] for _ in arguments.axes]
        for combination in combinations:
            params, rules, values = base_params, base_rules, []
            for axis, value in zip(arguments.axes, combination):
                if axis.kind == PARAMETER:
                    params = params.with_overrides({axis.name: str(value)})
                    values.append(getattr(params, axis.name))
                else:
                    rules = replace(rules, **{axis.name: rule_value(rules, axis.name, value)})
                    values.append(getattr(rules, axis.name))
            cell = Cell(tuple(values), rules, Simulator(rules, turbo, clock, entries_are_orders=replaying_signals, execution=execution))
            cells.append(cell)
            by_params.setdefault(params, []).append(cell)
            for number, value in enumerate(values):
                if value not in read_axes[number]:
                    read_axes[number].append(value)
    except (OSError, ValueError, TypeError) as e:
        print(e, file=sys.stderr)
        return 2

    location = backtest.price_location(arguments.source, arguments.root, arguments.indice)
    if not os.path.exists(location):
        print(f"No prices at {location}. Fetch some with: python -m src.strategy.history.dukascopy --from ... --to ...", file=sys.stderr)
        return 2
    start, end = backtest.day_bounds(clock, arguments.first_day, arguments.last_day)
    source = FileBars(location, arguments.indice, arguments.asset_type)
    made_with = {"code": code_fingerprint(), "price_files": prices_fingerprint(source.files(start, end))}

    def to_cells(group: list[Cell]):
        def decided(decision) -> None:
            for cell in group:
                cell.simulator.decide(decision)
        return decided

    if replaying_signals:
        first_moment, last_moment = start.timestamp(), end.timestamp()
        feeds = [signals.Replay([signal for signal in recorded if first_moment <= signal.at < last_moment], to_cells(cells))]
    else:
        feeds = [engine.Feed(strategy_type(params, clock), clock, to_cells(group)) for params, group in by_params.items()]

    # Sees every bar and trades nothing: the days there were prices for.
    calendar = Simulator(base_rules, turbo, clock)
    simulators = [cell.simulator for cell in cells]
    bar_count = days_begun = 0
    replayed_day = None
    started = time.monotonic()
    for bar in source.bars(start, end):
        bar_count += 1
        if arguments.progress:
            day, _ = clock.at(bar.start)
            if day != replayed_day:
                replayed_day = day
                days_begun += 1
                backtest._write_json(arguments.progress, {"day": day.isoformat(), "days_begun": days_begun, "bars": bar_count})
        for feed in feeds:
            feed.begin(bar)
        calendar.on_bar(bar)
        for simulator in simulators:
            # The bars in which a cell has nothing open and nothing waiting change nothing for it.
            if simulator.busy:
                simulator.on_bar(bar)
        for feed in feeds:
            feed.end(bar)
    for feed in feeds:
        if replaying_signals:
            feed.finish()
    for simulator in simulators:
        simulator.finish()
    if bar_count == 0:
        print(f"No bar of {arguments.indice} between {arguments.first_day} and {arguments.last_day} in {location}.", file=sys.stderr)
        return 2
    seconds = round(time.monotonic() - started, 1)

    tested = report.market_days(calendar.days)
    cut = held_out_from([day.day for day in tested], arguments.held_out)
    results = [{"values": list(cell.values), **_parts(cell, tested, clock, cut)} for cell in cells]

    axes = [{"kind": axis.kind, "name": axis.name, "values": values} for axis, values in zip(arguments.axes, read_axes)]
    what = f"Signals of {arguments.signals}" if replaying_signals else arguments.strategy
    print(f"{what} from {arguments.first_day} to {arguments.last_day} ({base_rules.timezone}), prices: {location}")
    print(
        f"Rules: {rules_path}. {len(cells)} combination(s) of "
        + " and ".join(f"{axis['name']} ({len(axis['values'])} values)" for axis in axes)
        + f", {bar_count} bars, {seconds:.0f} s. Turbo bought at {arguments.entry_price:g}, "
        f"1.00 for {arguments.points_per_unit:g} index points, spread {arguments.spread:g}."
    )
    if cut is None:
        print(f"{len(tested)} day(s) with prices: too few to hold any out. Nothing here says which value to choose.")
    else:
        held = sum(1 for day in tested if day.day >= cut)
        print(f"{len(tested) - held} day(s) to choose on, then {held} held out from {cut}: those had no say in the choice.")
    print()
    print(format_cells(axes, results))

    period = [arguments.first_day + timedelta(days=offset) for offset in range((arguments.last_day - arguments.first_day).days + 1)]
    with_prices = {day.day for day in tested}
    if arguments.out:
        os.makedirs(arguments.out, exist_ok=True)
        backtest._write_json(os.path.join(arguments.out, "sweep.json"), {
            "strategy": backtest.SIGNALS if replaying_signals else arguments.strategy,
            "from": arguments.first_day.isoformat(),
            "to": arguments.last_day.isoformat(),
            "prices": location,
            **made_with,
            "execution": asdict(execution),
            "turbo": asdict(turbo),
            "rules_from": rules_path,
            "rules": base_rules.to_dict(),
            "parameters_changed": {} if replaying_signals else {
                name: value for name, value in asdict(base_params).items() if value != getattr(params_type(), name)
            },
            "axes": axes,
            "held_out": {
                "share": arguments.held_out,
                "from": cut.isoformat() if cut else None,
                "days": sum(1 for day in tested if cut is not None and day.day >= cut),
                "tuning_days": sum(1 for day in tested if cut is None or day.day < cut),
            },
            "days_without_prices": [day.isoformat() for day in period if day.weekday() < 5 and day not in with_prices],
            "bars": bar_count,
            "seconds": seconds,
            "cells": results,
        })
        print(f"\nWritten to {os.path.join(arguments.out, 'sweep.json')}")
    return 0


def format_cells(axes: list[dict], cells: list[dict]) -> str:
    """One line per combination: its values, then its total and its trades on each part of the period."""
    names = [axis["name"] for axis in axes]
    lines = ["  ".join(name.rjust(max(len(name), 10)) for name in names) + "  " + "  ".join(
        title.rjust(14) for title in ("chosen on %", "trades", "held out %", "trades")
    )]
    for cell in cells:
        tuning, held_out = cell["tuning"], cell["held_out"]
        lines.append(
            "  ".join(str(value).rjust(max(len(name), 10)) for name, value in zip(names, cell["values"])) + "  " + "  ".join(
                text.rjust(14) for text in (
                    f"{tuning['total_percent']:.2f}", str(tuning["trades"]),
                    "-" if held_out is None else f"{held_out['total_percent']:.2f}",
                    "-" if held_out is None else str(held_out["trades"]),
                )
            )
        )
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(run(parse_arguments()))
