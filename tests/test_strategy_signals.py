import json

import pytest

from src.strategy import signals
from src.strategy.bars import IndexBar
from src.strategy.engine import FLAT, LONG, SHORT, Decision

# Monday 12 October 2026, 06:00:00 UTC.
START = 1791784800


def bar(seconds: int) -> IndexBar:
    return IndexBar(START + seconds, 100, 100, 100, 100, 100, 100, 100, 100, 1)


def replayed(recorded: list[Decision], bars: list[IndexBar]):
    """What ``run`` did, in order: each bar given, and each signal passed on."""
    happened: list[tuple] = []
    passed, without_prices = signals.run(
        recorded, bars,
        on_bar=lambda given: happened.append(("bar", given.start - START)),
        on_decision=lambda decision: happened.append(("signal", decision.at - START, decision.target)),
    )
    return happened, passed, without_prices


def test_each_action_the_trader_takes_is_the_position_it_asks_for():
    decisions = signals.decisions_of([
        {"at": "2026-10-12T06:00:20Z", "action": "close-long"},
        {"at": "2026-10-12T06:00:00Z", "action": "long", "confidence": 1.4},
        {"at": START + 5, "action": "short", "confidence": None},
        {"at": "2026-10-12T08:00:10+02:00", "action": "close-position"},
        {"at": "2026-10-12T06:00:30", "action": "close-short"},
    ])

    # Oldest first, whatever the way their time was written.
    assert [(decision.at - START, decision.target, decision.reason, decision.confidence, decision.closes) for decision in decisions] == [
        (0, LONG, "signal", 1.4, None),
        (5, SHORT, "signal", None, None),
        (10, FLAT, "close-position", None, None),
        (20, FLAT, "close-long", None, LONG),
        (30, FLAT, "close-short", None, SHORT),
    ]


def test_what_is_not_an_order_to_the_trader_is_refused():
    with pytest.raises(ValueError, match="Not an action the trader takes: 'daily_stats'"):
        signals.decisions_of([{"at": START, "action": "daily_stats"}])


def test_signals_are_read_from_a_file(tmp_path):
    path = tmp_path / "signals.json"
    path.write_text(json.dumps([{"at": "2026-10-12T06:00:00Z", "action": "long"}]))

    assert signals.read(str(path)) == [Decision(START, LONG, "signal")]

    path.write_text(json.dumps({"signals": []}))
    with pytest.raises(ValueError, match="is not a list of signals"):
        signals.read(str(path))
    path.write_text(json.dumps([{"action": "long"}]))
    with pytest.raises(ValueError, match="without its time or its action"):
        signals.read(str(path))


def test_a_signal_is_passed_on_once_the_bar_it_came_in_is_over():
    recorded = [Decision(START + 13, LONG, "signal")]

    happened, passed, without_prices = replayed(recorded, [bar(0), bar(10), bar(20)])

    # Whoever executes acts at the open of the bar that follows.
    assert happened == [("bar", 0), ("bar", 10), ("signal", 13, LONG), ("bar", 20)]
    assert (passed, without_prices) == (recorded, [])


def test_every_signal_is_passed_on_the_second_of_a_kind_too():
    recorded = [
        Decision(START + 1, LONG, "signal"), Decision(START + 4, FLAT, "close-position"), Decision(START + 12, LONG, "signal"),
    ]

    happened, passed, _ = replayed(recorded, [bar(0), bar(10), bar(20)])

    assert happened == [
        ("bar", 0), ("signal", 1, LONG), ("signal", 4, FLAT), ("bar", 10), ("signal", 12, LONG), ("bar", 20),
    ]
    assert passed == recorded


def test_a_signal_that_came_in_a_short_pause_of_the_prices_is_acted_on_at_the_next_one():
    recorded = [Decision(START + 25, SHORT, "signal")]

    happened, passed, without_prices = replayed(recorded, [bar(0), bar(80)])

    assert happened == [("bar", 0), ("signal", 25, SHORT), ("bar", 80)]
    assert (passed, without_prices) == (recorded, [])


def test_nothing_is_made_of_a_signal_that_came_when_there_were_no_prices():
    long_before, in_a_hole, after = (
        Decision(START - 600, LONG, "signal"), Decision(START + 15, SHORT, "signal"), Decision(START + 500, LONG, "signal"),
    )

    happened, passed, without_prices = replayed([long_before, in_a_hole, after], [bar(0), bar(200)])

    assert happened == [("bar", 0), ("bar", 200)]
    assert (passed, without_prices) == ([], [long_before, in_a_hole, after])


def test_without_a_bar_no_signal_is_replayed():
    recorded = [Decision(START, LONG, "signal")]

    assert replayed(recorded, []) == ([], [], recorded)
