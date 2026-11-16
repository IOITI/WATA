"""Edge Watch's model (src/admin/edge_watch.py): the reference's days, the cone, the test and its verdicts."""

import random

import pytest

from src.admin import edge_watch as ew


def _gauss_days(seed: int, days: int, mean: float, spread: float) -> list[list[float]]:
    draw = random.Random(seed)
    return [[round(draw.gauss(mean, spread), 3) for _ in range(draw.choice([0, 1, 1, 2, 2, 3]))] for _ in range(days)]


def _target_days(seed: int, days: int, win: float = 0.62) -> list[list[float]]:
    """Days shaped as a 0.5 % day target shapes them: a win makes the day up to +0.5 % and ends it."""
    draw = random.Random(seed)
    blocks = []
    for _ in range(days):
        block, level = [], 1.0
        for _ in range(draw.choice([0, 1, 2, 3])):
            if draw.random() < win:
                result = (1.005 / level - 1) * 100
                block.append(round(result, 3))
                break
            result = -draw.uniform(3, 8.3)
            block.append(round(result, 3))
            level *= 1 + result / 100
        blocks.append(block)
    return blocks


def _baseline(per_trade: float, trades: int = 80, repetitions: int = 50) -> dict:
    total = ((1 + per_trade / 100) ** trades - 1) * 100
    return {"totals": [total] * repetitions, "trades": [trades] * repetitions}


def _track(blocks: list[list[float]], length: int, draw: random.Random, shift: float = 0.0) -> list[float]:
    path: list[float] = []
    while len(path) < length:
        path.extend(result + shift for result in draw.choice(blocks))
    return path[:length]


# ── the reference ──


def test_day_blocks_keep_tested_days_in_order_with_their_trades_in_the_order_they_closed():
    days = [
        {"day": "2026-11-02", "status": "tested"},
        {"day": "2026-11-03", "status": "no_bars"},
        {"day": "2026-11-04", "status": "tested"},
        {"day": "2026-11-05", "status": "tested"},
    ]
    trades = [
        {"day": "2026-11-02", "performance_percent": 2.0, "exit": {"filled_at": "2026-11-02T10:00:00Z"}},
        {"day": "2026-11-02", "performance_percent": -1.0, "exit": {"filled_at": "2026-11-02T09:00:00Z"}},
        {"day": "2026-11-03", "performance_percent": 5.0, "exit": {"filled_at": "2026-11-03T09:00:00Z"}},
        {"day": "2026-11-05", "performance_percent": None, "exit": {"filled_at": "2026-11-05T09:00:00Z"}},
        {"day": "2026-11-05", "performance_percent": 1.5, "exit": {}},
    ]
    # The day not tested is left out with its trade; the tested day without a trade is an empty block.
    assert ew.day_blocks(days, trades) == [[-1.0, 2.0], [], []]


def test_random_mean_is_the_median_geometric_mean_per_trade_with_the_drag_added_back():
    baseline = {"totals": [10.0, -10.0, None, 5.0], "trades": [10, 10, 4, 0]}
    up = (1.1 ** 0.1 - 1) * 100
    down = (0.9 ** 0.1 - 1) * 100
    assert ew.random_mean(baseline, 2.0) == pytest.approx((up + down) / 2 + 4 / 200)
    assert ew.random_mean(None, 2.0) is None
    assert ew.random_mean({"totals": [3.0], "trades": [0]}, 2.0) is None
    # A repetition wiped out counts as -100 % a trade, not as an error.
    assert ew.random_mean({"totals": [-100.0], "trades": [5]}, 0.0) == pytest.approx(-100.0)


def test_geometric_mean_compounds_back_to_the_total():
    results = [10.0, -5.0, 3.0]
    per_trade = ew.geometric_mean(results)
    assert ((1 + per_trade / 100) ** 3 - 1) * 100 == pytest.approx(((1.1 * 0.95 * 1.03) - 1) * 100)


def test_the_grid_is_dense_first_and_ends_at_the_horizon():
    points = ew.grid(237)
    assert points[:50] == list(range(1, 51))
    assert points[50:53] == [55, 60, 65]
    assert 200 in points and 210 in points and points[-2:] == [230, 237]
    assert ew.grid(60)[-1] == 60


def test_a_model_is_the_same_wherever_it_is_made_and_differs_by_reference():
    blocks = _gauss_days(1, 30, 1.5, 4.0)
    first = ew.build_model(blocks, _baseline(-0.8), run_id="20261101-000000-aaaaaa", paths=200, h1_paths=50)
    again = ew.build_model(blocks, _baseline(-0.8), run_id="20261101-000000-aaaaaa", paths=200, h1_paths=50)
    other = ew.build_model(blocks, _baseline(-0.8), run_id="20261101-000000-bbbbbb", paths=200, h1_paths=50)
    assert first == again
    assert first["seed"] != other["seed"] and first["cone"] != other["cone"]


def test_a_thin_reference_cannot_be_watched_and_says_why():
    model = ew.build_model(_gauss_days(2, 6, 1.0, 4.0), None, run_id="20261101-000000-cccccc")
    assert model["usable"] is False and "tested day" in model["why_not"]
    judgement = ew.judge(model, [1.0] * 12)
    assert judgement["verdict"] == ew.NO_MODEL and judgement["reasons"] == [model["why_not"]]
    assert ew.readiness_unmet(judgement)[0].startswith("its reference cannot be watched")
    assert ew.judge(None, [1.0])["verdict"] == ew.NO_MODEL


def test_a_reference_no_better_than_random_entries_has_no_test():
    blocks = _gauss_days(3, 30, -1.0, 4.0)
    model = ew.build_model(blocks, _baseline(0.5), run_id="20261101-000000-dddddd", paths=200)
    assert model["usable"] and model["test"] is None and model["cone"] is not None
    judgement = ew.judge(model, [0.5] * 15)
    assert judgement["verdict"] == ew.NO_EDGE
    assert ew.readiness_unmet(judgement) == ["its reference does no better than random entries"]


def test_without_random_entries_the_test_is_against_a_mean_of_zero():
    model = ew.build_model(_gauss_days(4, 30, 1.5, 4.0), None, run_id="20261101-000000-eeeeee", paths=200, h1_paths=50)
    assert model["reference"]["h1"] == ew.ZERO and model["reference"]["h1_mean"] == 0.0
    assert model["test"] is not None


# ── the test's calibration ──


@pytest.mark.parametrize("blocks", [_gauss_days(5, 40, 1.2, 4.0), _target_days(6, 40)], ids=["independent", "day-target"])
def test_trades_like_the_reference_s_pass_the_threshold_within_the_horizon_about_five_percent_of_the_time(blocks):
    model = ew.build_model(blocks, _baseline(-1.2), run_id="20261101-000000-ffffff", paths=1500, h1_paths=100)
    assert model["test"] is not None
    draw = random.Random(17)
    fired = sum(
        1 for _ in range(800) if ew.statistic(model, _track(blocks, model["horizon"], draw))[2] is not None
    )
    assert fired / 800 <= 0.08


def test_trades_shifted_to_random_entries_are_caught_and_the_model_says_how_soon():
    blocks = _gauss_days(7, 40, 1.5, 3.0)
    model = ew.build_model(blocks, _baseline(-1.5), run_id="20261101-000000-a1a1a1", paths=800, h1_paths=200)
    test = model["test"]
    assert test["trades_to_tell"] is not None and test["detect"]["median"] <= test["trades_to_tell"]
    shift = model["reference"]["h1_mean"] - model["reference"]["mean_clipped"]
    draw = random.Random(23)
    firsts = [ew.statistic(model, _track(blocks, model["horizon"] * 4, draw, shift))[2] for _ in range(200)]
    caught = sorted(first for first in firsts if first is not None)
    assert len(caught) >= 180
    # The model's own estimate is near what fresh shifted tracks do.
    assert caught[len(caught) // 2] == pytest.approx(test["detect"]["median"], rel=0.35)


# ── verdicts ──


@pytest.fixture(scope="module")
def model():
    return ew.build_model(_gauss_days(8, 40, 1.2, 4.0), _baseline(-1.5), run_id="20261101-000000-b2b2b2", paths=1000,
                          h1_paths=100)


def test_fewer_than_ten_trades_are_too_early(model):
    judgement = ew.judge(model, [-5.0] * 9)
    assert judgement["verdict"] == ew.TOO_EARLY and judgement["n"] == 9


def test_trades_that_lose_like_random_entries_are_broken_and_say_when(model):
    judgement = ew.judge(model, [-1.5, -4.0, 2.0, -3.0, -6.0] * 8)
    assert judgement["verdict"] == ew.BROKEN
    assert judgement["alarm_at"] is not None and judgement["alarm_at"] <= 40
    assert "random entries" in judgement["reasons"][0]


def test_the_reference_s_own_kind_of_trades_are_as_expected(model):
    judgement = ew.judge(model, [1.2, -2.0, 4.0, 0.5, -1.0, 3.0, 1.0, -0.5, 2.0, 1.5, 0.8, -1.2])
    assert judgement["verdict"] == ew.AS_EXPECTED
    assert 5 <= judgement["rank"] <= 95 and judgement["rank_bound"] is None
    assert judgement["progress"] == pytest.approx(12 / model["test"]["trades_to_tell"], abs=0.01)


def test_a_result_far_above_the_reference_s_paths_is_better_than_expected(model):
    judgement = ew.judge(model, [6.0] * 15)
    assert judgement["verdict"] == ew.BETTER_THAN_EXPECTED and judgement["rank"] > 95


def test_a_losing_streak_rarer_than_five_percent_is_worth_watching_before_the_test_fires(model):
    results = [3.0] * 8 + [-1.0] * 7
    judgement = ew.judge(model, results)
    assert judgement["alarm_at"] is None
    assert judgement["verdict"] == ew.WATCH and judgement["streak_rank"] < 5


def test_the_test_restarts_where_it_is_told_while_the_cone_reads_every_trade(model):
    losing = [-1.5, -4.0, 2.0, -3.0, -6.0] * 8
    later = [1.2, -2.0, 4.0, 0.5, -1.0, 3.0]
    judgement = ew.judge(model, losing + later, tested=later)
    assert judgement["n"] == 46 and judgement["tested"] == 6 and judgement["alarm_at"] is None


def test_ranks_past_the_cone_s_outer_levels_are_bounds():
    levels = [float(value) for value in range(len(ew.LEVELS))]
    assert ew.share_at_or_below(-1.0, levels) == (ew.LEVELS[0], "at_most")
    assert ew.share_at_or_below(100.0, levels) == (ew.LEVELS[-1], "at_least")
    share, bound = ew.share_at_or_below(4.5, levels)
    assert bound is None and ew.LEVELS[4] < share < ew.LEVELS[5]


# ── what follows ──


def test_the_guard_pauses_only_on_a_broken_verdict_with_enough_trades_a_reference_not_too_few_and_the_same_settings():
    broken = {"verdict": ew.BROKEN, "tested": 25, "reference": {"sample": "few"}, "settings_differ": []}
    assert ew.should_pause(broken)
    assert "not broken" in ew.guard_refusal({**broken, "verdict": ew.WATCH})
    assert "20" in ew.guard_refusal({**broken, "tested": 12})
    assert "too few" in ew.guard_refusal({**broken, "reference": {"sample": "too_few"}})
    assert "stoploss_percent" in ew.guard_refusal({**broken, "settings_differ": ["stoploss_percent"]})


def test_readiness_wants_twenty_paper_trades_not_broken_and_not_under_the_fifth_percentile(model):
    assert ew.readiness_unmet(None)[0].startswith("no reference run")
    assert ew.readiness_unmet(ew.judge(model, [1.0] * 12)) == ["12 paper trade(s) since its reference of 20"]
    good = [1.2, -2.0, 4.0, 0.5, -1.0, 3.0, 1.0, -0.5, 2.0, 1.5, 0.8, -1.2] * 2
    assert ew.readiness_unmet(ew.judge(model, good)) == []
    assert "random entries" in ew.readiness_unmet(ew.judge(model, [-1.5, -4.0, 2.0, -3.0, -6.0] * 8))[0]


def test_settings_that_shape_a_trade_are_compared_and_sizing_is_not():
    reference = {"stoploss_percent": -7.0, "day_profit_target": 0.5, "capital": 1000.0, "funds_percent": 100.0}
    live = {"stoploss_percent": -5.0, "day_profit_target": 0.5, "capital": 21.82, "funds_percent": 50.0}
    assert ew.settings_differ(reference, live) == ["stoploss_percent"]
    assert ew.settings_differ(None, live) == []
    assert "capital" not in ew.SHAPING_FIELDS and "pause_below_balance_eur" not in ew.SHAPING_FIELDS


def test_a_new_verdict_is_said_in_one_line(model):
    judgement = ew.judge(model, [-1.5, -4.0, 2.0, -3.0, -6.0] * 8)
    text = ew.said("the live trades", judgement)
    assert text.startswith("📐 WATA Edge Watch, the live trades: Broken after 40 trade(s)")
    assert "\n" not in text


def test_a_summary_leaves_the_reference_s_details_out(model):
    summary = ew.summary(ew.judge(model, [1.0] * 12))
    assert "reference" not in summary and summary["sample"] == model["reference"]["sample"]
