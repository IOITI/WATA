"""
Plateaus instead of peaks: which of a sweep's combinations stands on high ground, and which on a knife edge.

A sweep chooses the combination with the best total on the days a choice may be made on (src/backtest/run.py). On
few days the best is often a lone peak: one step away on an axis, the result falls (the kernel/VWAP research of Nov
2026: in_23 20 made +112 %, 22 made +0.8 %). A value worth keeping is one whose neighbours do well too.

- **Neighbours** are the adjacent values of each numeric axis, in the order of their values; a categorical axis (text,
  on/off) has none: its values are not near one another.
- **A cell's plateau** is the median tuning total of the cell and its neighbours. The robust pick is the cell with the
  best plateau; it may be another than the best cell.
- **A knife edge**: a best cell above zero whose neighbours have a median at or below zero, or under half of the
  best's total: one step away, most of it is gone. (Under the grid's median would not do: on two values, the other
  one is always under it.)
- **A robust walk-forward** chooses, window after window, the cell whose neighbourhood did best on the days before
  (each day the mean of the cell's and its neighbours' results), and keeps that cell's own result on the days after:
  set beside the sweep's own walk-forward, which chooses the peak.

It reads a sweep's grid as the list gives it (``compact_grid`` of src/admin/backtests.py: each cell's values and its
tuning, held-out and whole totals) and, for the walk-forward, each cell's daily ``curve`` of sweep.json. Kept out of
src/backtest: nothing here changes a run nor the code digest of any (``_CODE``). Pure.
"""

import math
from statistics import fmean, median

from src.backtest.rules import compounded


def _numeric(values) -> bool:
    return bool(values) and all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in values)


def _shape(axes: list[dict]) -> list[int]:
    return [len(axis.get("values") or []) for axis in axes] or [0]


def _place(index: int, shape: list[int]) -> list[int]:
    """A cell's position on each axis (cells are listed row by row: the last axis changes fastest)."""
    place = []
    for size in reversed(shape):
        place.append(index % size if size else 0)
        index //= size if size else 1
    return list(reversed(place))


def _index(place: list[int], shape: list[int]) -> int:
    index = 0
    for position, size in zip(place, shape):
        index = index * size + position
    return index


def neighbours(axes: list[dict], index: int) -> list[int]:
    """The cells one step away from ``index`` on each numeric axis, in the order of the axis's values."""
    shape = _shape(axes)
    place = _place(index, shape)
    found = []
    for number, axis in enumerate(axes):
        values = axis.get("values") or []
        if not _numeric(values):
            continue
        order = sorted(range(len(values)), key=lambda position: values[position])
        rank = order.index(place[number])
        for step in (-1, 1):
            if 0 <= rank + step < len(order):
                moved = list(place)
                moved[number] = order[rank + step]
                found.append(_index(moved, shape))
    return found


def _total(cell: dict | None, part: str) -> float | None:
    value = ((cell or {}).get(part) or {}).get("total_percent")
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def plateau(grid: dict | None) -> dict | None:
    """
    Each cell's plateau (the median tuning total of the cell and its neighbours), the best cell and the robust pick,
    and whether the best stands on a knife edge. None for a grid without tuning totals.
    """
    if not isinstance(grid, dict):
        return None
    axes = grid.get("axes") or []
    cells = grid.get("cells") or []
    totals = [_total(cell, "tuning") for cell in cells]
    known = [(index, total) for index, total in enumerate(totals) if total is not None]
    if not known or not any(_numeric(axis.get("values") or []) for axis in axes):
        return None
    scores: list[float | None] = []
    for index, total in enumerate(totals):
        around = [totals[other] for other in neighbours(axes, index) if totals[other] is not None]
        scores.append(round(median([total, *around]), 2) if total is not None else None)
    best = max(known, key=lambda entry: (entry[1], -entry[0]))[0]
    robust = max(((index, score) for index, score in enumerate(scores) if score is not None),
                 key=lambda entry: (entry[1], -entry[0]))[0]
    around_best = [totals[other] for other in neighbours(axes, best) if totals[other] is not None]
    grid_median = median(total for _, total in known)
    best_neighbours = median(around_best) if around_best else None
    best_total = totals[best]
    knife_edge = (best_total > 0 and best_neighbours is not None
                  and (best_neighbours <= 0 or best_neighbours < best_total / 2))
    return {
        "scores": scores,
        "best": best,
        "robust": robust,
        "best_values": (cells[best] or {}).get("values"),
        "robust_values": (cells[robust] or {}).get("values"),
        "best_total": totals[best],
        "robust_total": totals[robust],
        "best_neighbours_median": round(best_neighbours, 2) if best_neighbours is not None else None,
        "grid_median": round(grid_median, 2),
        "knife_edge": knife_edge,
        # The robust pick's own result on the days held out, beside the best's.
        "best_held_out": _total(cells[best], "held_out"),
        "robust_held_out": _total(cells[robust], "held_out"),
    }


def _curve(cell: dict) -> dict[str, float]:
    found = {}
    for row in (cell or {}).get("curve") or []:
        if isinstance(row, (list, tuple)) and len(row) > 1 and isinstance(row[1], (int, float)):
            found[str(row[0])] = float(row[1])
    return found


def robust_walk_forward(sweep: dict | None, train_days: int, test_days: int) -> dict | None:
    """
    Window after window, the cell whose neighbourhood did best on the ``train_days`` before it (each day the mean of
    the cell's and its neighbours' results), its own result kept on the ``test_days`` after; stitched, what choosing
    on plateaus would have given. None without curves or days for one window.
    """
    if not isinstance(sweep, dict) or train_days < 1 or test_days < 1:
        return None
    axes = sweep.get("axes") or []
    cells = sweep.get("cells") or []
    curves = [_curve(cell) for cell in cells]
    if not curves or not any(curves):
        return None
    days = sorted(set().union(*curves))
    if len(days) < train_days + 1:
        return None
    hoods = [[index, *neighbours(axes, index)] for index in range(len(curves))]
    averaged = [{day: fmean(curves[other].get(day, 0.0) for other in hood) for day in days} for hood in hoods]
    windows, stitched = [], []
    start = 0
    while start + train_days < len(days):
        train = days[start:start + train_days]
        test = days[start + train_days:start + train_days + test_days]
        scores = [compounded([curve[day] for day in train]) for curve in averaged]
        chosen = max(range(len(curves)), key=lambda index: (scores[index], -index))
        outcome = compounded([curves[chosen].get(day, 0.0) for day in test])
        stitched += [curves[chosen].get(day, 0.0) for day in test]
        windows.append({"train": [train[0], train[-1]], "test": [test[0], test[-1]], "cell": chosen,
                        "values": (cells[chosen] or {}).get("values"), "test_percent": outcome})
        start += test_days
    return {"train_days": train_days, "test_days": test_days, "windows": windows, "days": len(stitched),
            "total_percent": compounded(stitched)}


def plateau_of(sweep: dict | None, walk_forward: dict | None = None) -> dict | None:
    """
    What a sweep's part ``plateau`` says: the plateaus, and, when the sweep walked forward (its summary's
    ``walk_forward``), the robust walk-forward over the same windows beside the sweep's own.
    """
    found = plateau(sweep)
    if found is None:
        return None
    walk = walk_forward if isinstance(walk_forward, dict) else {}
    if walk.get("train_days") and walk.get("test_days"):
        found["robust_walk_forward"] = robust_walk_forward(sweep, int(walk["train_days"]), int(walk["test_days"]))
        found["walk_forward_percent"] = walk.get("total_percent")
    return found
