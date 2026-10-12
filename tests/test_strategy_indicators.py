import pytest

from src.price_recorder.range_bars import RangeBarBuilder
from src.strategy.indicators import KernelRegression, SessionVwap, kernel_weights, wave


# ── kernel ──


def test_the_wave_kernel_is_one_at_no_distance_and_nothing_beyond_the_bandwidth():
    assert wave(0) == 1.0
    assert wave(1) == 0.0
    assert wave(1.5) == 0.0
    # Past half the bandwidth the cosine turns negative: old values pull the other way.
    assert wave(0.75) < 0


def test_the_weights_are_those_of_the_pine_library_at_bandwidth_fifteen():
    # KernelFunctions.pine: (1 - d) * cos(pi * d) with d = (bars back)^2 / bandwidth^2.
    weights = kernel_weights(15)

    assert len(weights) == 15
    assert [round(weight, 6) for weight in weights[:4]] == [1.0, 0.995459, 0.980691, 0.95243]
    assert round(weights[10], 6) == 0.096471
    assert [round(weight, 6) for weight in weights[11:]] == [-0.054729, -0.153281, -0.176604, -0.118466]


def test_an_unknown_kernel_or_no_bandwidth_is_refused():
    with pytest.raises(ValueError, match="Unknown kernel"):
        kernel_weights(15, "gaussien")
    with pytest.raises(ValueError, match="bandwidth"):
        kernel_weights(0)


def test_the_regression_waits_for_as_many_values_as_its_bandwidth():
    regression = KernelRegression(3)

    assert regression.push(10) is None
    assert regression.push(10) is None
    assert regression.push(10) == pytest.approx(10)


def test_the_regression_of_a_flat_series_is_that_value_and_it_leans_on_the_newest():
    regression = KernelRegression(3)
    for value in (10, 10, 10):
        regression.push(value)

    # Weights 1, 0.835282 and 0.096471, the newest first.
    assert regression.push(13) == pytest.approx((13 * 1.0 + 10 * 0.835282 + 10 * 0.096471) / 1.931753, abs=1e-5)


# ── session VWAP ──


def test_the_vwap_is_the_average_weighted_by_what_each_price_weighs():
    vwap = SessionVwap()
    assert vwap.value is None and vwap.deviation is None

    vwap.add(30000, 1)
    vwap.add(30002, 3)
    vwap.add(30004, 2)

    assert vwap.value == pytest.approx(180014 / 6)
    assert vwap.deviation == pytest.approx(1.374369, abs=1e-6)


def test_a_price_that_weighs_nothing_does_not_count():
    vwap = SessionVwap()
    vwap.add(100, 2)
    vwap.add(900, 0)

    assert vwap.value == 100
    assert vwap.deviation == 0


def test_a_reset_forgets_the_session_before():
    vwap = SessionVwap()
    vwap.add(100, 1)
    vwap.reset()
    assert vwap.value is None

    vwap.add(200, 1)
    assert vwap.value == 200


def test_the_spread_of_prices_is_exact_at_the_height_of_an_index():
    # A million prices around 30,000: summing their squares as they are would lose the last digits.
    vwap = SessionVwap()
    for _ in range(500_000):
        vwap.add(29999.5, 1)
        vwap.add(30000.5, 1)

    assert vwap.value == pytest.approx(30000)
    assert vwap.deviation == pytest.approx(0.5, abs=1e-9)


# ── range bars, one ten-second bar at a time ──


def test_a_range_bar_comes_out_with_the_ten_seconds_that_ended_it_and_only_once():
    builder = RangeBarBuilder(ticks=10)

    assert builder.add(100, 500, 505, 500, 505) == []
    ended = builder.add(110, 505, 512, 505, 512)
    assert [(bar.open, bar.high, bar.low, bar.close, bar.complete) for bar in ended] == [(500, 510, 500, 510, True)]
    assert (ended[0].began, ended[0].ended) == (100, 120)
    # The next bar opened where that one closed, and nothing is given twice.
    assert builder.add(120, 512, 514, 512, 514) == []
    assert builder.in_progress().open == 510


def test_looking_at_the_bar_in_progress_does_not_end_it():
    builder = RangeBarBuilder(ticks=10)
    builder.add(100, 500, 503, 500, 503)

    assert builder.in_progress() == builder.in_progress()
    assert builder.in_progress().complete is False
    assert builder.add(110, 503, 504, 503, 504) == []
    assert builder.in_progress().high == 504


def test_a_fast_move_ends_several_range_bars_in_the_same_ten_seconds():
    builder = RangeBarBuilder(ticks=10)
    builder.add(100, 500, 500, 500, 500)

    ended = builder.add(110, 500, 535, 500, 535)

    assert [(bar.open, bar.close) for bar in ended] == [(500, 510), (510, 520), (520, 530)]
    assert all(bar.ended == 120 for bar in ended)


def test_after_a_pause_the_bar_in_progress_ends_as_it_is_and_the_next_one_says_so():
    builder = RangeBarBuilder(ticks=10)
    builder.add(100, 500, 504, 500, 504)

    ended = builder.add(1000, 560, 562, 560, 562)

    assert [(bar.close, bar.complete) for bar in ended] == [(504, False)]
    assert builder.in_progress().after_pause is True
    assert builder.in_progress().open == 560
