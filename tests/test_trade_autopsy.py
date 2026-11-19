"""A trade autopsy (src/admin/trade_autopsy.py): what separated winners from losers, known at entry, held later."""

from datetime import date, datetime, timedelta, timezone

from src.admin.trade_autopsy import LEFT_OUT, autopsy, exit_hints, index_features, trade_features

# Each made-up day's bars start at 13:00 UTC, a minute each, 240 of them.
OPEN_HOUR = 13


def weekdays(first: date, count: int) -> list[str]:
    found, day = [], first
    while len(found) < count:
        if day.weekday() < 5:
            found.append(day.isoformat())
        day += timedelta(days=1)
    return found


def start_of(day: str) -> float:
    return datetime.fromisoformat(day).replace(hour=OPEN_HOUR, tzinfo=timezone.utc).timestamp()


def bars_for(day: str) -> list[list]:
    """The index rises from minute 40 to 60, falls from 130 to 150, and stays flat otherwise."""
    start, level, bars = start_of(day), 20_000.0, []
    for minute in range(240):
        if 40 <= minute < 60:
            level += 4
        elif 130 <= minute < 150:
            level -= 4
        bars.append([start + minute * 60, level, level + 1, level - 1, level, None, None, None, None, 5])
    return bars


def iso(moment: float) -> str:
    return datetime.fromtimestamp(moment, timezone.utc).isoformat().replace("+00:00", "Z")


def made_up(count_days: int):
    days = weekdays(date(2026, 10, 5), count_days)
    trades, signals = [], []
    for day in days:
        start = start_of(day)
        # A long after the rise wins; a long after the fall loses.
        for number, (minute, result) in enumerate(((60, 3.0), (150, -2.0))):
            signal = f"{day}-{number}"
            raised = start + minute * 60
            signals.append({"id": signal, "arrived_at": iso(raised)})
            trades.append({
                "day": day, "signal_id": signal, "direction": "long", "performance_percent": result,
                "max_performance_percent": 4.0 if result > 0 else 1.5, "min_performance_percent": -0.5 if result > 0 else -3.0,
                "minutes_after_open": float(minute - 30), "day_percent_before": 0.0 if number == 0 else 3.0,
                "entry": {"decided_at": iso(raised + 1), "ask": 5.0, "bid": 4.95},
                "exit": {"filled_at": iso(raised + 600)}, "turbo": {"barrier": 19_000.0},
            })
    tested = [{"day": day, "status": "tested", "regime": {"gap_percent": 0.2, "news": None}} for day in days]
    return trades, signals, tested


def test_what_held_on_later_days_is_said_as_a_hypothesis():
    trades, signals, days = made_up(24)

    found = autopsy(trades, signals, days, bars_for, signal_delay=0.0, seed=7)

    held = {entry["name"] for entry in found["held"]}
    assert "move_15" in held
    entry = next(entry for entry in found["features"] if entry["name"] == "move_15")
    assert entry["tested"]["difference"] == 5.0 and entry["q"] <= 0.1
    assert "points a trade on the later days" in found["held"][0]["sentence"]
    assert found["choose_days"][0] == "2026-10-05" and found["test_days"][-1] == days[-1]["day"]
    # Nothing every trade shares (the gap, the spread) is said to separate anything.
    assert "spread_percent" not in held and "gap" not in held
    assert found["left_out"] == LEFT_OUT


def test_a_run_of_few_trades_says_so():
    trades, signals, days = made_up(6)

    found = autopsy(trades, signals, days, bars_for, signal_delay=0.0, seed=7)

    assert found["too_few"].startswith("12 trades over 6 days:") and found["held"] == []


def test_only_the_bars_ended_before_the_signal_are_read():
    bars = bars_for("2026-10-05")
    raised = start_of("2026-10-05") + 60 * 60
    before = index_features(bars, raised, "long")
    # A spike from the moment the signal was raised on (the minute that ended then is before it): nothing changes.
    spiked = [list(bar) for bar in bars]
    for bar in spiked:
        if bar[0] >= raised:
            bar[2], bar[3], bar[4] = bar[2] + 500, bar[3] - 500, bar[4] + 300
    assert index_features(spiked, raised, "long") == {**before}
    assert before["move_15"] > 0 and index_features(bars, raised, "short")["move_15"] == -before["move_15"]


def test_the_previous_trade_counts_only_once_it_had_closed():
    trades, _, _ = made_up(1)
    first, second = trades
    second_entry = {**second, "entry": {**second["entry"], "decided_at": first["entry"]["decided_at"].replace("14:00:01", "14:05:00")}}

    closed = trade_features(second, None, [], [first, second], {})
    still_open = trade_features(second_entry, None, [], [first, second_entry], {})

    assert closed["previous_result"] == 3.0 and closed["trade_of_day"] == 2.0
    assert still_open["previous_result"] is None


def test_the_exits_are_read_in_sample():
    trades, _, _ = made_up(2)

    found = exit_hints(trades)

    assert found["losers"] == 2 and found["losers_up_first"]["1"] == 100.0 and found["losers_up_first"]["2"] == 0.0
    assert found["winners_down_first"]["3"] == 0.0 and found["in_sample"] is True
