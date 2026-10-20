"""
A sweep: the same run for every combination of the values of one or two
settings, or of a Pine script's inputs, in one pass over the recorded days.

The signals are computed once per set of the script's inputs; the settings
change only how they are traded. Each combination is its own account. Its
figures are given for the days the values are chosen on (``tuning``), the
last days held out of that choice (``held_out``), and all of them: a value
that only shines on the days it was chosen on was chosen for them.
"""

from dataclasses import dataclass
from itertools import product

from src.backtest.settings import BacktestSettings, FIELDS_BY_NAME, InvalidSettings, with_changes

MAX_AXES = 2
MAX_CELLS = 64
MAX_VALUES = 12
MAX_INPUT_SETS = 12
DEFAULT_HELD_OUT = 0.3
LEAST_HELD_OUT, MOST_HELD_OUT = 0.1, 0.5
SETTING, INPUT = "setting", "input"


class InvalidSweep(ValueError):
    """A sweep that cannot be made; the message says why."""


@dataclass(frozen=True)
class Axis:
    kind: str
    name: str
    values: tuple


def checked_axes(axes: list[dict], *, inputs_known: set[str] | None) -> list[Axis]:
    """The axes of a sweep, each of a setting a run may change or of an input of the script."""
    if not isinstance(axes, list) or not 1 <= len(axes) <= MAX_AXES:
        raise InvalidSweep(f"A sweep tries one or two settings ({MAX_AXES} at most)")
    checked, names = [], set()
    for axis in axes:
        kind, name, values = axis.get("kind"), axis.get("name"), axis.get("values")
        if kind not in (SETTING, INPUT):
            raise InvalidSweep("An axis is a setting or an input of the script")
        if not isinstance(name, str) or name in names:
            raise InvalidSweep("Each axis names a different setting")
        if kind == SETTING and name not in FIELDS_BY_NAME:
            raise InvalidSweep(f"'{name}' is not a setting a backtest can change")
        if kind == INPUT and (inputs_known is None or name not in inputs_known):
            raise InvalidSweep(f"'{name}' is not an input of the script")
        if not isinstance(values, list) or not 2 <= len(values) <= MAX_VALUES or len(set(map(repr, values))) != len(values):
            raise InvalidSweep(f"An axis tries 2 to {MAX_VALUES} different values")
        names.add(name)
        checked.append(Axis(kind, name, tuple(values)))
    cells = 1
    for axis in checked:
        cells *= len(axis.values)
    if cells > MAX_CELLS:
        raise InvalidSweep(f"{cells} combinations: {MAX_CELLS} at most")
    input_sets = 1
    for axis in checked:
        if axis.kind == INPUT:
            input_sets *= len(axis.values)
    if input_sets > MAX_INPUT_SETS:
        raise InvalidSweep(f"{input_sets} sets of the script's inputs: {MAX_INPUT_SETS} at most, each a replay of the script")
    return checked


def cells_of(axes: list[Axis]) -> list[tuple]:
    """Every combination of the axes' values, the first axis's slowest."""
    return list(product(*(axis.values for axis in axes)))


def cell_settings(settings: BacktestSettings, axes: list[Axis], values: tuple) -> BacktestSettings:
    changes = {axis.name: value for axis, value in zip(axes, values) if axis.kind == SETTING}
    try:
        changed, _, _ = with_changes(settings, changes)
    except InvalidSettings as e:
        raise InvalidSweep(str(e)) from None
    return changed


def cell_inputs(axes: list[Axis], values: tuple) -> tuple:
    """The script's inputs a combination sets, as a key: the combinations sharing it share a replay."""
    return tuple((axis.name, value) for axis, value in zip(axes, values) if axis.kind == INPUT)


def held_out_from(days: list[str], share: float) -> str | None:
    """The first day held out: the last ``share`` of the days tested, one at least, never all of them."""
    if len(days) < 2:
        return None
    held = max(1, min(len(days) - 1, round(len(days) * share)))
    return days[len(days) - held]


def with_inputs(rule: dict, inputs: tuple) -> dict:
    """The rule with the inputs of a combination set, the others as the run sets them."""
    if not inputs:
        return rule
    changed = {name: value for name, value in inputs}
    kept = [entry for entry in rule["inputs"] if entry["id"] not in changed]
    return {**rule, "inputs": kept + [{"id": name, "var": None, "title": None, "value": value} for name, value in inputs]}

