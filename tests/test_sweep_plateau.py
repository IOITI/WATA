"""Plateaus instead of peaks (src/admin/sweep_plateau.py): which combination stands on high ground."""

from src.admin.sweep_plateau import neighbours, plateau, plateau_of, robust_walk_forward


def grid(tuning: list[float], first=(15, 18, 20, 22), second=None, held_out=None):
    axes = [{"kind": "input", "name": "in_23", "values": list(first)}]
    if second is not None:
        axes.append({"kind": "input", "name": "in_36", "values": list(second)})
    cells = []
    for index, total in enumerate(tuning):
        cells.append({"values": [index], "tuning": {"total_percent": total, "days": 15},
                      "held_out": {"total_percent": (held_out or tuning)[index], "days": 7}, "whole": None})
    return {"axes": axes, "cells": cells}


def test_neighbours_are_the_next_values_of_each_numeric_axis():
    axes = [{"values": [1, 2, 3]}, {"values": [10, 20]}]

    assert sorted(neighbours(axes, 0)) == [1, 2]       # (1, 10): (1, 20) and (2, 10)
    assert sorted(neighbours(axes, 3)) == [1, 2, 5]    # (2, 20): (1, 20), (2, 10), (3, 20)
    # A categorical axis has no neighbours; values out of order are taken in their order.
    assert neighbours([{"values": [True, False]}], 0) == []
    assert sorted(neighbours([{"values": [30, 10, 20]}], 0)) == [2]


def test_a_lone_peak_is_a_knife_edge_and_the_robust_pick_stands_on_a_plateau():
    # in_23 20 makes +112 %, 22 makes +0.8 %, 18 +43 %: the peak's neighbours fall away.
    found = plateau(grid([43.15, 43.67, 112.4, 0.81, 50.0], first=(15, 18, 20, 22, 30)))

    assert found["best"] == 2 and found["knife_edge"] is True
    assert found["best_neighbours_median"] == 22.24 and found["grid_median"] == 43.67
    lone = plateau(grid([5.0, 4.0, 112.4, -20.0], first=(15, 18, 20, 22)))
    assert lone["best"] == 2 and lone["knife_edge"] is True
    assert lone["best_neighbours_median"] == -8.0


def test_a_broad_plateau_is_no_knife_edge():
    found = plateau(grid([30.0, 32.0, 34.0, 33.0, 10.0], first=(1, 2, 3, 4, 5)))

    assert found["best"] == 2 and found["knife_edge"] is False and found["robust"] == 2


def test_the_robust_pick_may_be_another_cell_than_the_peak():
    found = plateau(grid([10.0, 40.0, 12.0, 30.0, 31.0, 32.0], first=(1, 2, 3, 4, 5, 6)))

    # The peak (40) stands between 10 and 12; 30, 31, 32 stand together.
    assert found["best"] == 1 and found["knife_edge"] is True
    # The last cell (32, beside 31) has the best median, 31.5.
    assert found["robust"] == 5 and found["scores"][5] == 31.5 and found["scores"][4] == 31.0


def test_a_grid_without_a_numeric_axis_has_no_plateau():
    assert plateau({"axes": [{"values": [True, False]}], "cells": [{"tuning": {"total_percent": 1}}] * 2}) is None
    assert plateau(None) is None


def test_the_robust_walk_forward_chooses_on_neighbourhoods_and_keeps_the_cells_own_days():
    days = [f"2026-10-{day:02d}" for day in range(13, 23)]
    # Cell 1 is a lone spike on the first days, its neighbours lose; cell 3 and its neighbours steadily win.
    curves = [
        [[day, -1.0, 1] for day in days],
        [[day, 5.0 if index < 5 else -3.0, 1] for index, day in enumerate(days)],
        [[day, 0.5, 1] for day in days],
        [[day, 1.0, 1] for day in days],
        [[day, 0.8, 1] for day in days],
    ]
    sweep = {"axes": [{"values": [1, 2, 3, 4, 5]}], "cells": [{"values": [n], "curve": curve} for n, curve in enumerate(curves)]}

    walked = robust_walk_forward(sweep, 5, 5)

    # On the first days cell 1 is the peak (+5 % a day), cell 2's neighbourhood the best (2.17 % a day): cell 2, which
    # keeps +0.5 % a day after, where cell 1 loses 3 % a day.
    assert walked["windows"][0]["cell"] == 2 and walked["days"] == 5 and walked["total_percent"] > 0
    found = plateau_of({**sweep, "cells": [{**cell, "tuning": {"total_percent": 1.0}} for cell in sweep["cells"]]},
                       {"train_days": 5, "test_days": 5, "total_percent": -14.1})
    assert found["robust_walk_forward"]["total_percent"] == walked["total_percent"]
    assert found["walk_forward_percent"] == -14.1
