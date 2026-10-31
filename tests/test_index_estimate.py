"""
A turbo's bid between Saxo's quotes, estimated from the index (src/trade/index_estimate.py): its sensitivity
learned from its own quotes, the estimate, when it is not to be judged on, what is kept of each position, the
index followed and the reason a sale on it is given.
"""

import pytest

from src.trade.index_estimate import (
    BETWEEN_QUOTES,
    INDEX_FRESH_SECONDS,
    LEAST_MOVES,
    MIN_MOVE_POINTS,
    PRIOR_SENSITIVITY,
    QUOTE_FRESH_SECONDS,
    BetweenQuotes,
    TurboSensitivity,
    between_quotes_reason,
    estimated_bid,
    followed_epic,
    index_between_quotes,
    index_epic,
    why_not_estimated,
)


def learned(direction: int, quotes: list[tuple[float, float]]) -> TurboSensitivity:
    sensitivity = TurboSensitivity(direction)
    for bid, index in quotes:
        sensitivity.quote(bid, index)
    return sensitivity


def test_a_short_turbo_learns_its_euros_per_point_from_its_own_quotes():
    # The index up 10 points between quotes, the short turbo's bid down 0.071 each time: 0.0071 € a point.
    quotes = [(12.0 - 0.071 * step, 29000.0 + 10 * step) for step in range(LEAST_MOVES + 1)]
    sensitivity = learned(-1, quotes)

    assert sensitivity.moves == LEAST_MOVES
    assert sensitivity.learned == pytest.approx(0.0071)
    # Learned, it stands for the prior whatever the barrier says.
    assert sensitivity.value(11.6, 29050.0, 30845.7) == pytest.approx(0.0071)


def test_small_moves_are_counted_from_the_same_quote_until_the_index_moved_enough():
    sensitivity = TurboSensitivity(1)
    sensitivity.quote(10.0, 29000.0)
    # Two quotes the index barely moved for: nothing learned, the move still counted from the first.
    sensitivity.quote(10.01, 29003.0)
    sensitivity.quote(10.015, 29006.0)
    assert sensitivity.moves == 0
    sensitivity.quote(10.08, 29000.0 + MIN_MOVE_POINTS + 1)
    assert sensitivity.moves == 1
    # A quote whose index is not known breaks the chain: the next move is counted from the quote after it.
    sensitivity.quote(10.5, None)
    sensitivity.quote(10.2, 29030.0)
    assert sensitivity.moves == 1


def test_until_learned_the_prior_stands_only_below_the_price_per_point_to_the_barrier():
    sensitivity = TurboSensitivity(-1)
    # A TurboXL short of 26-30 Oct: 12.4 € at 1,346 points from its barrier, 0.0092 € a point; the prior is below it.
    assert sensitivity.learned is None
    assert sensitivity.value(12.4, 29500.0, 30846.0) == PRIOR_SENSITIVITY
    # A turbo of a tenth of that parity: its price per point of distance is far under the prior, which waits.
    assert sensitivity.value(1.24, 29500.0, 30846.0) is None
    # Without its barrier, nothing stands for it.
    assert sensitivity.value(12.4, 29500.0, None) is None
    assert TurboSensitivity(-1, prior=None).value(12.4, 29500.0, 30846.0) is None


def test_the_estimate_moves_saxo_s_last_bid_as_the_index_moved_since():
    assert estimated_bid(12.0, 29000.0, 29010.0, 0.0071, -1) == pytest.approx(11.929)
    assert estimated_bid(5.0, 29000.0, 28990.0, 0.01, 1) == pytest.approx(4.9)


@pytest.mark.parametrize("changes, said", [
    ({"sensitivity": None}, "its sensitivity to the index is not known yet"),
    ({"index_at": 100.0 - INDEX_FRESH_SECONDS - 0.5}, "no fresh quote of the index"),
    ({"index": None}, "no fresh quote of the index"),
    ({"last_index": None}, "the index was not known at Saxo's last quote"),
    ({"quoted_at": 100.0 - QUOTE_FRESH_SECONDS - 1}, "Saxo's last quote of it is too old"),
    ({"index": 31000.0}, "the index is at or past its knock-out level"),
    ({"estimate": 7.0}, "the estimate is too far from Saxo's last bid"),
    ({"estimate": -0.1}, "the estimate is too far from Saxo's last bid"),
    ({}, None),
])
def test_an_estimate_is_judged_on_only_while_all_it_rests_on_holds(changes, said):
    facts = {
        "now": 100.0, "index_at": 99.5, "quoted_at": 80.0, "sensitivity": 0.0071, "last_bid": 10.0, "last_index": 29000.0,
        "estimate": 9.9, "index": 29010.0, "barrier": 30845.7, "direction": -1, **changes,
    }
    assert why_not_estimated(**facts) == said


def test_a_position_is_estimated_from_saxo_s_last_quote_of_it_and_the_index_then():
    kept = BetweenQuotes()
    assert kept.estimate("p1", index=29000.0, index_at=100.0, now=100.0, barrier=30846.0) == (
        None, "no quote of Saxo's to start from",
    )
    # A TurboXL short at 12.4 €, 1,346 points from its barrier: the prior stands until its own moves are learned.
    kept.quoted("p1", 12.4, 29500.0, 100.0, -1)
    estimate, why = kept.estimate("p1", index=29510.0, index_at=110.0, now=110.5, barrier=30846.0)

    assert why is None
    assert estimate.bid == pytest.approx(12.4 - PRIOR_SENSITIVITY * 10)
    assert (estimate.anchor.bid, estimate.anchor.index, estimate.anchor.at, estimate.index) == (12.4, 29500.0, 100.0, 29510.0)
    # A quote whose index was not known starts no estimate; the index too old, neither.
    kept.quoted("p1", 12.3, None, 120.0, -1)
    assert kept.estimate("p1", index=29510.0, index_at=125.0, now=125.0, barrier=30846.0)[1] == (
        "the index was not known at Saxo's last quote"
    )
    kept.quoted("p1", 12.3, 29512.0, 130.0, -1)
    assert kept.estimate("p1", index=29515.0, index_at=131.0, now=131.0 + INDEX_FRESH_SECONDS + 1, barrier=30846.0)[1] == (
        "no fresh quote of the index"
    )
    # What is kept of a position no longer held goes.
    kept.forget_except({"p2"})
    assert kept.anchor("p1") is None and kept.sensitivity("p1") is None


def test_its_sensitivity_is_learned_across_its_quotes():
    kept = BetweenQuotes()
    for step in range(LEAST_MOVES + 1):
        kept.quoted("p1", 10.0 + 0.09 * step, 29000.0 + 10 * step, 100.0 + 30 * step, 1)

    assert kept.sensitivity("p1").learned == pytest.approx(0.009)
    estimate, _ = kept.estimate("p1", index=29070.0, index_at=260.0, now=260.0, barrier=None)
    assert estimate.bid == pytest.approx(10.45 + 0.009 * 20)


def test_the_setting_is_off_unless_it_says_true():
    assert index_between_quotes({"index_between_quotes": True}) is True
    for position_management in (None, {}, {"index_between_quotes": False}, {"index_between_quotes": "yes"}):
        assert index_between_quotes(position_management) is False


FEED = {"enabled": True, "instruments": [{"epic": "US100", "label": "us100"}, {"epic": "DE40", "label": "de40"}]}


def test_the_index_followed_is_the_feeds_of_the_one_indice_the_trader_takes():
    assert index_epic(FEED, "us100") == "US100"
    assert index_epic(FEED, "us500") is None
    assert index_epic({**FEED, "enabled": False}, "us100") is None
    assert followed_epic(FEED, {"us100": "1909050"}) == "US100"
    # Two indices: a position does not say which one it was bought for.
    assert followed_epic(FEED, {"us100": "1909050", "de40": "1"}) is None
    assert followed_epic({**FEED, "enabled": False}, {"us100": "1909050"}) is None
    assert followed_epic(None, None) is None


@pytest.mark.parametrize("reason", [
    "Stoploss (-15%) hit at -15.08%",
    "Takeprofit (60%) hit at 60.12%",
    "Trailing Stop: peak 123.4%, now 112.3% (dd 11.1% >= 10.5%)",
    "Daily profit target (1.7%) potentially met (1.72%)",
])
def test_a_reason_decided_between_quotes_keeps_its_words_and_fits_its_column(reason):
    kept = BetweenQuotes()
    kept.quoted("p1", 12.4, 29500.0, 100.0, -1)
    estimate, _ = kept.estimate("p1", index=29510.0, index_at=112.0, now=112.4, barrier=30846.0)

    said = between_quotes_reason(reason, "US100", estimate, 112.4)

    assert said.startswith(reason)
    assert f"US100 {BETWEEN_QUOTES}: {round(estimate.bid, 4):g} from 12.4, 12 s" in said
    assert len(f"Performance ({said})") <= 128


def test_a_reason_too_long_for_its_column_keeps_its_words_and_says_where_it_was_judged():
    kept = BetweenQuotes()
    kept.quoted("p1", 12.4, 29500.0, 100.0, -1)
    estimate, _ = kept.estimate("p1", index=29510.0, index_at=112.0, now=112.4, barrier=30846.0)
    reason = "Trailing Stop: " + "x" * 70

    said = between_quotes_reason(reason, "US100", estimate, 112.4)

    assert said == f"{reason}; US100 {BETWEEN_QUOTES}"
    assert len(f"Performance ({said})") <= 128
