"""
Whether the balance the trader last read may size an order without being read
again (src/trade/flat_balance.py): only one read while nothing was open, with
no order of the trader's since, and nothing open now.
"""

from src.trade.flat_balance import FlatBalance


def _read(flat: FlatBalance, *, before=True, after=True) -> bool:
    return flat.read(flat.reading(), before, after)


def test_no_reading_stands_before_one_was_made_while_nothing_was_open():
    flat = FlatBalance()

    assert not flat.usable(True)
    assert _read(flat) and flat.usable(True)


def test_a_reading_made_while_a_position_was_open_does_not_stand():
    for before, after in ((False, True), (True, False), (False, False)):
        flat = FlatBalance()
        assert not _read(flat, before=before, after=after)
        assert not flat.usable(True)


def test_a_reading_that_stood_stands_no_more_once_an_order_of_the_trader_went():
    flat = FlatBalance()
    _read(flat)

    flat.moved()

    assert not flat.usable(True)
    # The next one read while nothing is open stands again.
    assert _read(flat) and flat.usable(True)


def test_a_reading_begun_before_an_order_and_ended_after_it_does_not_stand():
    flat = FlatBalance()
    token = flat.reading()
    # A signal is handled while Saxo answers the balance: its order goes, and the answer may be of before or after it.
    flat.moved()

    assert not flat.read(token, True, True)
    assert not flat.usable(True)


def test_a_reading_ended_while_an_order_was_on_its_way_stands_no_more_once_the_order_is_done():
    flat = FlatBalance()
    # Read between Saxo filling the order and its position being written down: nothing is open in the records yet.
    assert _read(flat)

    # The handler marks the end of everything it sent.
    flat.moved()

    assert not flat.usable(True)


def test_a_reading_that_stands_is_not_used_while_something_is_open_now():
    flat = FlatBalance()
    _read(flat)

    assert not flat.usable(False)
    assert flat.usable(True)


def test_a_later_reading_made_while_something_was_open_takes_the_standing_one_away():
    flat = FlatBalance()
    _read(flat)

    # The position monitor's sale is not recorded yet: a position is still open in the records.
    _read(flat, before=False, after=False)

    assert not flat.usable(True)
