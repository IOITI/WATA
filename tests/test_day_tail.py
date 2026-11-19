"""Where a run's result comes from, and the tail its day's caps hide (src/admin/day_tail.py)."""

from src.admin.day_tail import BEST_FIRST, RESULTS, day_sequences, decomposition, replay, tail
from src.backtest.settings import BacktestSettings


def settings(**changes) -> BacktestSettings:
    found = BacktestSettings()
    for name, value in {"no_entry_above_day_profit": 2.0, "day_profit_target": 3.0, "max_day_loss": -25.0, **changes}.items():
        object.__setattr__(found, name, value)
    return found


def trade(result, best=0.0, opened=0.0, closed=None):
    return {"result": result, "best": best, "opened": opened, "closed": closed if closed is not None else opened + 60}


def test_a_day_ends_at_its_target_once_its_best_point_comes_first():
    day = [trade(-1.0, best=4.0), trade(2.0, best=2.5, opened=600)]

    assert replay(day, settings(), BEST_FIRST) == (3.0, True)
    # On its results alone, the first trade loses and the second does not reach the target.
    assert replay(day, settings(), RESULTS) == (0.98, False)


def test_no_entry_above_the_level_nor_under_the_days_loss():
    above = [trade(2.5), trade(-10.0, opened=600)]
    under = [trade(-30.0), trade(5.0, opened=600)]

    assert replay(above, settings(), RESULTS)[0] == 2.5
    assert replay(under, settings(), RESULTS)[0] == -30.0


def test_the_cooldown_the_entries_and_the_breakers_hold_entries_back():
    day = [trade(-1.0, opened=0, closed=60), trade(-1.0, opened=120), trade(1.0, opened=4000)]

    assert replay(day, settings(cooldown_after_loss_minutes=30), RESULTS)[0] == round((0.99 * 1.01 - 1) * 100, 2)
    assert replay(day, settings(max_trades_per_day=1), RESULTS)[0] == -1.0
    assert replay(day, settings(max_consecutive_losses=2), RESULTS)[0] == round((0.99 * 0.99 - 1) * 100, 2)


def test_the_tail_draws_whole_days_seeded_beside_the_runs_own_days():
    twin_days = [{"day": f"2026-10-{n:02d}", "status": "tested"} for n in (13, 14, 15, 16)]
    twin_trades = []
    for n, results in zip((13, 14, 15, 16), ([4.0], [-8.0, -8.0, -8.0, -8.0], [1.0, 1.0], [-30.0])):
        for index, result in enumerate(results):
            twin_trades.append({"day": f"2026-10-{n:02d}", "performance_percent": result,
                                "max_performance_percent": max(0.0, result),
                                "entry": {"decided_at": f"2026-10-{n:02d}T1{index}:00:00Z"},
                                "exit": {"filled_at": f"2026-10-{n:02d}T1{index}:30:00Z"}})
    run_days = [{"day": "2026-10-13", "status": "tested", "day_percent": 3.0}, {"day": "2026-10-14", "status": "tested", "day_percent": 3.0}]

    run_trades = [{"day": "2026-10-13", "exit": {"kind": "day_target"}}, {"day": "2026-10-14", "exit": {"kind": "signal"}}]
    found = tail(run_days, twin_days, twin_trades, settings(), seed=7, run_trades=run_trades)
    again = tail(run_days, twin_days, twin_trades, settings(), seed=7, run_trades=run_trades)

    assert found == again and found["days_drawn_from"] == 4
    # Its own days: none lost, one of the two sold at its target.
    assert found["observed"]["losing_share"] == 0.0 and found["observed"]["at_target_share"] == 50.0
    assert tail(run_days, twin_days, twin_trades, settings(), seed=7)["observed"]["at_target_share"] is None
    drawn = found["bounds"][RESULTS]
    # Four losses of 8 % compound to -28.36 % (the day's loss cap, -25 %, comes after the fourth), and a day of -30 %:
    # about half the draws end at -25 % or under; the +4 % day ends at its target, a quarter of them.
    assert 40 < drawn["at_or_under"]["-25"] < 60 and drawn["at_or_under"]["-5"] == drawn["at_or_under"]["-25"]
    assert 15 < drawn["at_target_share"] < 35


def test_a_day_sequence_keeps_the_trades_in_the_order_they_were_opened():
    days = [{"day": "2026-10-13", "status": "tested"}, {"day": "2026-10-14", "status": "closed"}]
    trades = [{"day": "2026-10-13", "performance_percent": 1.0, "entry": {"decided_at": "2026-10-13T12:00:00Z"}},
              {"day": "2026-10-13", "performance_percent": -1.0, "entry": {"decided_at": "2026-10-13T09:00:00Z"}},
              {"day": "2026-10-14", "performance_percent": 5.0, "entry": {"decided_at": "2026-10-14T09:00:00Z"}}]

    [only] = day_sequences(days, trades)

    assert [entry["result"] for entry in only] == [-1.0, 1.0]


def test_the_decomposition_says_what_the_caps_and_the_entries_add():
    found = decomposition({"figures": {"total_percent": 112.44, "trades": 34}}, {"totals": [10.0, 20.0, 30.0]},
                          {"figures": {"total_percent": -12.5, "trades": 90}}, {"totals": [-40.0, -20.0]})

    assert found["caps_add"] == 124.94
    assert found["entries_add_with_caps"] == 92.44 and found["entries_add_without_caps"] == 17.5
    assert found["without_caps"] == {"entries": -12.5, "random": -30.0, "trades": 90}
