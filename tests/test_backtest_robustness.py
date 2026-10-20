"""
Whether a result holds beyond the days it was found on (src/backtest/robustness.py):
risk figures, walk-forward, the deflated Sharpe ratio, the probability of
backtest overfitting, and the same days drawn again.
"""

import math
from statistics import fmean, stdev

import pytest

from src.backtest.robustness import (
    deflated_sharpe,
    deflated_sharpe_probability,
    expected_maximum_sharpe,
    monte_carlo_drawdown,
    pbo,
    risk_figures,
    walk_forward,
    without_best_days,
)
from src.backtest.rules import compounded


def trade(performance: float, best: float, held: int = 600) -> dict:
    return {"performance_percent": performance, "max_performance_percent": best, "holding_seconds": held}


def test_the_risk_of_the_days_annualised_and_what_the_trades_kept_of_their_best():
    days = [1.0, -2.0, 1.5, 0.0, 2.0, -0.5] * 4
    figures = risk_figures(days, [trade(2.0, 4.0), trade(-1.0, 0.5), trade(3.0, 3.0, 1800)])

    assert figures["sharpe"] == round(fmean(days) / stdev(days) * math.sqrt(252), 2)
    downside = math.sqrt(fmean(min(day, 0.0) ** 2 for day in days))
    assert figures["sortino"] == round(fmean(days) / downside * math.sqrt(252), 2)
    # Under water from the second day to the fourth, after the -2 %: three days.
    assert figures["longest_under_water_days"] == 3
    # The worst 5 % of 24 days: the two worst.
    assert figures["worst_5_percent_days"] == -2.0
    # A winner made 2.5 on average, the loser lost 1.
    assert figures["payoff"] == 2.5
    # The winners kept half of 4 and all of 3; the loser, which had reached 0.5, is not counted.
    assert figures["exit_efficiency"] == 75.0
    assert figures["trades_per_day"] == round(3 / 24, 2) and figures["hours_held_per_day"] == round(3000 / 3600 / 24, 2)
    assert figures["calmar"] is not None and figures["yearly_percent"] > 0


def test_risk_figures_of_too_few_days_say_nothing_rather_than_something_wrong():
    figures = risk_figures([1.0], [])
    assert (figures["sharpe"], figures["sortino"], figures["calmar"], figures["payoff"], figures["exit_efficiency"]) == (
        None, None, None, None, None,
    )
    assert risk_figures([], [])["trades_per_day"] is None
    # Under a month of days, nothing is brought to a year; what was seen still is.
    short = risk_figures([1.0, -2.0, 1.5] * 6, [trade(2.0, 4.0)])
    assert (short["sharpe"], short["sortino"], short["calmar"], short["yearly_percent"]) == (None, None, None, None)
    assert short["longest_under_water_days"] == 2 and short["worst_5_percent_days"] == -2.0


def test_walk_forward_chooses_on_the_days_before_and_keeps_the_days_after():
    days = [f"2026-10-{day:02d}" for day in range(12, 18)]
    # The first combination does well on the first days, the second later.
    early = dict(zip(days, [2.0, 2.0, -1.0, -1.0, -1.0, -1.0]))
    late = dict(zip(days, [-1.0, -1.0, 1.0, 3.0, 2.0, 2.0]))
    steady = dict(zip(days, [0.5] * 6))

    found = walk_forward([early, late, steady], train_days=2, test_days=2, values=[["early"], ["late"], ["steady"]])

    assert [window["values"] for window in found["windows"]] == [["early"], ["late"]]
    assert [window["test"] for window in found["windows"]] == [["2026-10-14", "2026-10-15"], ["2026-10-16", "2026-10-17"]]
    # Chosen on the 12th and 13th, the early one lost on the 14th and 15th; then the late one won.
    assert found["windows"][0]["test_percent"] == round(compounded([-1.0, -1.0]), 2)
    assert found["windows"][0]["test_rank"] == 3
    assert found["total_percent"] == round(compounded([-1.0, -1.0, 2.0, 2.0]), 2)
    assert found["days"] == 4 and found["changes"] == 1
    assert found["best_in_hindsight_percent"] == round(compounded([1.0, 3.0, 2.0, 2.0]), 2)
    assert found["curve"][-1][0] == "2026-10-17"
    assert walk_forward([early], train_days=6, test_days=1) is None


def test_the_deflated_sharpe_ratio_of_bailey_and_lopez_de_prado():
    # Their example: 2.5 a year over 1,250 days, 100 trials whose ratios vary by 0.5 a year, skew -3, kurtosis 10.
    assert deflated_sharpe_probability(2.5 / math.sqrt(250), 1250, 100, 0.5 / 250, -3, 10) == pytest.approx(0.9004, abs=1e-4)
    # One configuration: nothing to deflate.
    assert expected_maximum_sharpe(1, 0.01) == 0.0
    assert expected_maximum_sharpe(100, 0.01) > expected_maximum_sharpe(10, 0.01) > 0


def test_the_more_configurations_were_tried_the_less_a_sharpe_ratio_says():
    days = [0.6, -0.3, 0.9, 0.2, -0.4, 0.8, 0.1, 0.5, -0.2, 0.7] * 6

    alone = deflated_sharpe(days, 1)
    many = deflated_sharpe(days, 500)

    assert alone["probability"] > many["probability"]
    assert many["trials"] == 500 and many["days"] == 60
    assert "500 configurations tried" in many["sentence"]
    assert deflated_sharpe([1.0, 1.0], 3) is None
    assert deflated_sharpe(days[:19], 3) is None and deflated_sharpe(days[:20], 3) is not None


def test_the_probability_of_overfitting_is_nothing_for_a_steady_winner_and_all_for_a_fitted_one():
    days = [f"d{day:02d}" for day in range(16)]
    winner = {day: 1.0 for day in days}
    loser = {day: -1.0 for day in days}
    assert pbo([winner, loser])["probability"] == 0.0

    # Good on the first half of the days only: whichever half it is chosen on, the other half undoes it.
    fitted = {day: (2.0 if index < 8 else -2.0) for index, day in enumerate(days)}
    flat = {day: 0.0 for day in days}
    found = pbo([fitted, flat])
    assert found["probability"] == 1.0 and found["combinations"] == 70 and found["splits"] == 8
    assert pbo([winner]) is None and pbo([winner, loser], splits=8) is not None
    assert pbo([{day: 1.0 for day in days[:5]}, {day: 0.0 for day in days[:5]}]) is None


def test_the_same_days_in_another_order_and_without_the_best_of_them():
    days = [1.0, -2.0, 1.5, 0.5, -1.0, 2.0, -0.5, 1.0]

    drawn = monte_carlo_drawdown(days)
    again = monte_carlo_drawdown(days)

    assert drawn == again
    assert drawn["worst_5_percent"] <= drawn["median"] <= 0 and drawn["draws"] == 1000
    assert monte_carlo_drawdown(days[:4]) is None
    assert without_best_days([1.0, 5.0, -2.0, 3.0]) == [
        {"removed": 1, "total_percent": round(compounded([1.0, -2.0, 3.0]), 2)},
        {"removed": 2, "total_percent": round(compounded([1.0, -2.0]), 2)},
        {"removed": 3, "total_percent": round(compounded([-2.0]), 2)},
    ]
