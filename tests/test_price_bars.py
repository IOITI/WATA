import pytest

from src.price_recorder.bars import (
    BAR_SECONDS,
    FEED_SILENCE_SECONDS,
    MAX_CATCH_UP_BUCKETS,
    BarBuilder,
    Quote,
    bucket_start,
    read_quote,
)

# 12 Oct 2026, 08:00:00 UTC: on a ten-second boundary.
T0 = 1791792000


def _bars(closed):
    return {bar.key: bar for bar in closed.bars}


def _bid(bar):
    return bar.bid_open, bar.bid_high, bar.bid_low, bar.bid_close


def _ask(bar):
    return bar.ask_open, bar.ask_high, bar.ask_low, bar.ask_close


def test_buckets_are_aligned_on_the_clock():
    assert T0 % BAR_SECONDS == 0
    assert bucket_start(T0) == T0
    assert bucket_start(T0 + 9.999) == T0
    assert bucket_start(T0 + 10) == T0 + 10


# ── reading a quote ──


def _row(**quote):
    return {"Uic": 1, "Quote": {"PriceTypeBid": "Tradable", "PriceTypeAsk": "Tradable", "MarketState": "Open", **quote}}


def test_a_tradable_quote_is_read_with_its_delay():
    assert read_quote(_row(Bid=8.42, Ask=8.45)) == Quote(8.42, 8.45, 0)
    assert read_quote(_row(Bid=8.42, Ask=8.45, DelayedByMinutes=15)) == Quote(8.42, 8.45, 15)


@pytest.mark.parametrize("quote", [
    {"Bid": 8.42, "Ask": 8.45, "MarketState": "Closed"},
    {"Bid": 0.0, "Ask": 0.0, "PriceTypeBid": "NoMarket"},
    {"Bid": 8.42, "Ask": 8.45, "PriceTypeAsk": "NoMarket"},
    {"Bid": 8.42, "Ask": 8.45, "PriceTypeBid": "NoAccess"},
    {"Ask": 8.45},
    {"Bid": None, "Ask": 8.45},
    {"Bid": "n/a", "Ask": 8.45},
])
def test_what_cannot_be_traded_or_priced_is_not_a_quote(quote):
    assert read_quote(_row(**quote)) is None


def test_a_row_without_a_quote_is_not_one():
    assert read_quote({"Uic": 1}) is None
    assert read_quote({"Uic": 1, "Quote": None}) is None


def test_an_index_with_only_a_mid_uses_it_for_both_sides():
    assert read_quote({"Uic": 1, "Quote": {"Mid": 24150.5, "PriceTypeBid": "Indicative", "PriceTypeAsk": "Indicative"}}) == \
        Quote(24150.5, 24150.5, 0)


def test_a_missing_ask_leaves_the_ask_empty():
    assert read_quote(_row(Bid=8.42)) == Quote(8.42, None, 0)


# ── building bars ──


def test_a_bar_holds_the_first_highest_lowest_and_last_of_its_ten_seconds():
    builder = BarBuilder()
    builder.update("a", Quote(10.0, 10.1), T0 + 1)
    builder.update("a", Quote(10.4, 10.5), T0 + 3)
    builder.update("a", Quote(9.8, 9.9), T0 + 6)
    builder.update("a", Quote(10.1, 10.2), T0 + 9)

    (closed,) = builder.close_until(T0 + 10)

    assert closed.start == T0 and closed.feed_ok and closed.instruments == 1
    bar = _bars(closed)["a"]
    assert _bid(bar) == (10.0, 10.4, 9.8, 10.1)
    assert _ask(bar) == (10.1, 10.5, 9.9, 10.2)
    assert bar.updates == 4


def test_a_bar_opens_on_the_quote_that_was_standing_when_it_began():
    builder = BarBuilder()
    builder.update("a", Quote(10.0, 10.1), T0 + 8)
    builder.update("a", Quote(10.6, 10.7), T0 + 14)

    first, second = builder.close_until(T0 + 20)

    assert _bid(_bars(first)["a"]) == (10.0, 10.0, 10.0, 10.0)
    # The price was 10.0 from :10 to :14: the bar starts there, and its low is 10.0.
    bar = _bars(second)["a"]
    assert _bid(bar) == (10.0, 10.6, 10.0, 10.6)
    assert bar.updates == 1


def test_a_quote_that_did_not_move_gives_a_flat_bar_with_no_update():
    builder = BarBuilder()
    builder.update("a", Quote(10.0, 10.1), T0 + 1)
    builder.update("b", Quote(5.0, 5.1), T0 + 2)
    builder.update("b", Quote(5.2, 5.3), T0 + 12)

    first, second = builder.close_until(T0 + 20)

    assert set(_bars(first)) == {"a", "b"}
    still = _bars(second)["a"]
    assert _bid(still) == (10.0, 10.0, 10.0, 10.0) and _ask(still) == (10.1, 10.1, 10.1, 10.1)
    assert still.updates == 0 and still.start == T0 + 10
    assert _bars(second)["b"].updates == 1


def test_the_same_quote_received_again_is_not_an_update():
    builder = BarBuilder()
    builder.update("a", Quote(10.0, 10.1), T0 + 1)
    # The stream hands over every row of a subscription whenever one of them changes.
    builder.update("a", Quote(10.0, 10.1), T0 + 2)
    builder.update("a", Quote(10.0, 10.1), T0 + 3)

    (closed,) = builder.close_until(T0 + 10)

    assert _bars(closed)["a"].updates == 1


def test_a_change_of_one_side_only_is_an_update():
    builder = BarBuilder()
    builder.update("a", Quote(10.0, 10.1), T0 + 1)
    builder.update("a", Quote(10.0, 10.3), T0 + 5)

    (closed,) = builder.close_until(T0 + 10)

    bar = _bars(closed)["a"]
    assert _bid(bar) == (10.0, 10.0, 10.0, 10.0)
    assert _ask(bar) == (10.1, 10.3, 10.1, 10.3)
    assert bar.updates == 2


def test_an_instrument_that_stops_quoting_keeps_its_last_bar_and_gets_no_more():
    builder = BarBuilder()
    builder.update("a", Quote(1.20, 1.25), T0 + 1)
    builder.update("a", Quote(0.40, 0.45), T0 + 4)
    # Knocked out.
    builder.update("a", None, T0 + 6)
    builder.update("b", Quote(5.0, 5.1), T0 + 7)
    builder.update("b", Quote(5.1, 5.2), T0 + 15)

    first, second = builder.close_until(T0 + 20)

    assert _bid(_bars(first)["a"]) == (1.20, 1.20, 0.40, 0.40)
    assert set(_bars(second)) == {"b"}
    assert second.instruments == 1


def test_an_instrument_that_stops_quoting_without_having_moved_still_has_the_bar_of_that_bucket():
    builder = BarBuilder()
    builder.update("a", Quote(10.0, 10.1), T0 + 1)
    builder.update("a", None, T0 + 13)

    first, second, third = builder.close_until(T0 + 30)

    assert _bars(second)["a"].updates == 0
    assert "a" not in _bars(third)


def test_quoting_again_after_a_stop_starts_from_the_new_quote():
    builder = BarBuilder()
    builder.update("a", Quote(10.0, 10.1), T0 + 1)
    builder.update("a", None, T0 + 3)
    builder.update("a", Quote(7.0, 7.1), T0 + 12)

    _, second = builder.close_until(T0 + 20)

    assert _bid(_bars(second)["a"]) == (7.0, 7.0, 7.0, 7.0)


def test_nothing_is_carried_forward_once_the_feed_has_been_silent_too_long():
    builder = BarBuilder()
    builder.update("a", Quote(10.0, 10.1), T0 + 1)

    closed = builder.close_until(T0 + 60)

    assert [bucket.start - T0 for bucket in closed] == [0, 10, 20, 30, 40, 50]
    # The last quote arrived at +1 s: believed for 30 s, so through the bucket ending at +30.
    assert [bucket.feed_ok for bucket in closed] == [True, True, True, False, False, False]
    assert [len(bucket.bars) for bucket in closed] == [1, 1, 1, 0, 0, 0]
    assert FEED_SILENCE_SECONDS == 30


def test_any_quote_keeps_the_others_carried_forward():
    builder = BarBuilder()
    builder.update("still", Quote(10.0, 10.1), T0 + 1)
    for second in range(5, 60, 5):
        builder.update("moving", Quote(5.0 + second / 100, 5.2), T0 + second)

    closed = builder.close_until(T0 + 60)

    assert all(bucket.feed_ok for bucket in closed)
    assert all("still" in _bars(bucket) for bucket in closed)


def test_buckets_are_handed_over_once_and_in_order():
    builder = BarBuilder()
    builder.update("a", Quote(10.0, 10.1), T0 + 1)

    assert [bucket.start for bucket in builder.close_until(T0 + 10)] == [T0]
    assert builder.close_until(T0 + 15) == []
    assert [bucket.start for bucket in builder.close_until(T0 + 30.5)] == [T0 + 10, T0 + 20]


def test_a_late_writer_does_not_mix_the_next_bucket_into_the_one_it_closes():
    builder = BarBuilder()
    builder.update("a", Quote(10.0, 10.1), T0 + 1)
    builder.update("b", Quote(5.0, 5.1), T0 + 2)
    # The writer runs a moment after the boundary; quotes of the new bucket got in first.
    builder.update("a", Quote(11.0, 11.1), T0 + 10.01)

    (closed,) = builder.close_until(T0 + 10.05)

    assert _bid(_bars(closed)["a"]) == (10.0, 10.0, 10.0, 10.0)
    (following,) = builder.close_until(T0 + 20)
    assert _bid(_bars(following)["a"]) == (10.0, 11.0, 10.0, 11.0)
    assert _bars(following)["b"].updates == 0


def test_forgetting_an_instrument_stops_its_bars_and_keeps_the_one_in_progress():
    builder = BarBuilder()
    builder.update("a", Quote(10.0, 10.1), T0 + 1)
    builder.update("b", Quote(5.0, 5.1), T0 + 1)
    builder.retain({"b"})
    builder.update("b", Quote(5.1, 5.2), T0 + 11)

    first, second = builder.close_until(T0 + 20)

    # What "a" was quoted before it was let go is real, and kept.
    assert set(_bars(first)) == {"a", "b"} and set(_bars(second)) == {"b"}
    assert not builder.has_quote("a") and builder.has_quote("b")


def test_a_long_stall_is_skipped_instead_of_being_closed_bucket_by_bucket():
    builder = BarBuilder()
    builder.update("a", Quote(10.0, 10.1), T0 + 1)
    stalled_until = T0 + (MAX_CATCH_UP_BUCKETS + 50) * BAR_SECONDS

    closed = builder.close_until(stalled_until + 1)

    assert [bucket.start for bucket in closed] == [T0]
    assert builder.skipped_buckets == MAX_CATCH_UP_BUCKETS + 49
    builder.update("a", Quote(10.5, 10.6), stalled_until + 2)
    (resumed,) = builder.close_until(stalled_until + 10)
    assert resumed.start == stalled_until


def test_the_delay_of_a_bar_is_that_of_its_last_quote():
    builder = BarBuilder()
    builder.update("a", Quote(10.0, 10.1, 15), T0 + 1)

    first, second = builder.close_until(T0 + 20)

    assert _bars(first)["a"].delayed_minutes == 15
    assert _bars(second)["a"].delayed_minutes == 15
