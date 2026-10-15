from datetime import datetime, timezone

import pytest

from src.capital_feed.client import MinuteCandle
from src.capital_feed.probe import ProbeQuote, delays_of, minutes_of, parse_arguments, report, volume_verdict

T0 = 1792051200  # Thursday 15 Oct 2026, 08:00:00 UTC


def _quotes(per_minute, repeat_every=0):
    """``per_minute[i]`` quotes in minute i, each 40 ms late, every ``repeat_every``-th one repeating the price before."""
    quotes, bid = [], 25000.0
    for minute, count in enumerate(per_minute):
        for index in range(count):
            at = T0 + minute * 60 + (index + 1) * 59 / (count + 1)
            if not (repeat_every and index % repeat_every == repeat_every - 1):
                bid += 0.5
            quotes.append(ProbeQuote(received_at=at + 0.04, quoted_ms=int(at * 1000), bid=bid, ask=bid + 1))
    return quotes


def _candle(minute, volume):
    return MinuteCandle(datetime.fromtimestamp(T0 + minute * 60, timezone.utc), 1, 2, 0.5, 1.5, 1.1, 2.1, 0.6, 1.6, volume)


def test_quotes_are_counted_minute_by_minute_on_capital_coms_clock():
    minutes = minutes_of(_quotes([3, 5], repeat_every=2))

    assert [(m.minute.minute, m.messages, m.changes) for m in minutes] == [(0, 3, 2), (1, 5, 3)]
    first = minutes[0]
    assert (first.open, first.high, first.low, first.close) == (25000.5, 25001.0, 25000.5, 25001.0)


def test_delays_are_from_capital_coms_time_to_the_receipt():
    assert delays_of(_quotes([2])) == [pytest.approx(40, abs=1)] * 2
    assert delays_of([ProbeQuote(T0, None, 1.0, 2.0)]) == []


def test_the_verdict_says_which_count_capital_coms_volume_matches_on_whole_minutes():
    quotes = _quotes([4, 7, 9, 2], repeat_every=3)
    minutes = minutes_of(quotes)
    # Whole minutes are the ones between the first and the last.
    by_messages = {candle.ts: candle for candle in (_candle(1, 7), _candle(2, 9))}
    by_changes = {candle.ts: candle for candle in (_candle(1, minutes[1].changes), _candle(2, minutes[2].changes))}
    neither = {candle.ts: candle for candle in (_candle(1, 70), _candle(2, 90))}

    assert "quotes received in 2 of 2 whole minute(s), the changes of price in 0" in volume_verdict(minutes, by_messages)
    assert "the changes of price in 2" in volume_verdict(minutes, by_changes)
    assert "Neither: volume / quotes received is 10.00 to 10.00." in volume_verdict(minutes, neither)
    assert "No whole minute" in volume_verdict(minutes[:2], by_messages)


def test_the_report_lists_the_instrument_its_hours_the_stream_and_each_minute():
    details = {
        "instrument": {"epic": "US100", "name": "US Tech 100", "type": "INDICES", "currency": "USD", "streamingPricesAvailable": True,
                       "openingHours": {"mon": ["00:00 - 21:59"], "sat": [], "zone": "UTC"}},
        "snapshot": {"marketStatus": "TRADEABLE", "decimalPlacesFactor": 1, "delayTime": 0},
    }

    text = report(details, "wss://api-streaming-capital.backend-capital.com/connect", _quotes([2, 3, 1]), 180.0,
                  [_candle(1, 3)])

    assert "US100: US Tech 100 (INDICES, USD)" in text
    assert "market: TRADEABLE, decimals: 1, delayed by: 0 min" in text
    assert "mon: 00:00 - 21:59" in text and "sat: closed" in text
    assert "6 quote(s) in 180 s" in text and "median 40 ms" in text
    assert "08:01" in text and "quotes received in 1 of 1" in text


def test_a_search_or_an_instrument_must_be_named():
    with pytest.raises(SystemExit):
        parse_arguments([])
    arguments = parse_arguments(["--epic", "US100"])
    assert (arguments.environment, arguments.minutes) == ("demo", 3.0)
