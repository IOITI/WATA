"""
The account guard (src/trade/account_guard.py) and the day's breakers
(src/trade/day_breakers.py), alone and as the trader keeps them (TradingRule).
"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from src.trade.account_guard import AccountGuard, refusal as guard_refusal
from src.trade.day_breakers import DAY_DRAWDOWN, LOSS_STREAK, DayBreakers, best_and_now, losses_in_a_row
from src.trade.day_breakers import refusal as breakers_refusal
from src.trade.exceptions import TradingRuleViolation
from src.trade.rules import TradingRule

NOW = datetime(2026, 10, 27, 10, 0, tzinfo=timezone.utc)


# ── the guard alone ──


def test_the_guard_is_off_unless_one_of_its_settings_is_above_zero():
    assert not AccountGuard.from_config(None).on
    assert not AccountGuard.from_config({"cooldown_after_loss_minutes": 10}).on
    assert AccountGuard.from_config({"pause_below_balance_eur": 50}).on
    assert AccountGuard.from_config({"pause_after_drawdown_percent": 30}).on
    assert AccountGuard.from_config({"pause_below_balance_eur": True}) == AccountGuard()


def test_the_guard_trips_under_its_floor_and_that_far_under_the_highest():
    floor = AccountGuard(below_eur=50)
    fall = AccountGuard(drawdown_percent=30)

    assert floor.trip(49.99, None) == "The account is worth 49.99 €, under the account guard's 50 €"
    assert floor.trip(50, None) is None
    assert fall.trip(70, 100) == (
        "The account is worth 70.00 €, 30.0 % under its highest since entries were last resumed (100.00 €): "
        "the account guard pauses at 30 %"
    )
    assert fall.trip(70.01, 100) is None
    assert fall.trip(None, 100) is None and fall.trip(10, None) is None


@pytest.mark.parametrize("config, problem", [
    ({}, None),
    ({"pause_below_balance_eur": 0, "pause_after_drawdown_percent": 0}, None),
    ({"pause_below_balance_eur": -1}, "pause_below_balance_eur"),
    ({"pause_below_balance_eur": "50"}, "pause_below_balance_eur"),
    ({"pause_after_drawdown_percent": 101}, "pause_after_drawdown_percent"),
])
def test_the_guards_settings_are_checked(config, problem):
    found = guard_refusal(config)
    assert (found is None) if problem is None else (problem in found)


# ── the breakers alone ──


def test_the_losses_in_a_row_are_those_at_the_end_of_the_day():
    assert losses_in_a_row([]) == 0
    assert losses_in_a_row([-1, -2, 3, -1, -0.5]) == 2
    assert losses_in_a_row([-1, -2, 0.0]) == 0


def test_the_days_best_is_compounded_and_starts_at_zero():
    assert best_and_now([]) == (0.0, 0.0)
    assert best_and_now([-5]) == (0.0, -5.0)
    assert best_and_now([10, -10]) == (10.0, -1.0)


def test_the_breakers_refuse_after_that_many_losses_in_a_row_or_that_far_under_the_best():
    breakers = DayBreakers(max_losses=3, max_drawdown=8)

    assert breakers.refusal([-1, -1]) is None
    kind, text = breakers.refusal([2, -1, -1, -1])
    assert kind == LOSS_STREAK and text.startswith("Breaking trading rule: 3 losing trades in a row today (3 at most)")
    kind, text = breakers.refusal([10, -10])
    assert kind == DAY_DRAWDOWN and "is 11.0 points under its best (10.0%)" in text
    assert DayBreakers().refusal([-50, -50, -50]) is None


@pytest.mark.parametrize("config, problem", [
    ({}, None),
    ({"max_consecutive_losses": 3, "max_drawdown_from_day_best_percent": 5.5}, None),
    ({"max_consecutive_losses": 2.5}, "max_consecutive_losses"),
    ({"max_consecutive_losses": -1}, "max_consecutive_losses"),
    ({"max_drawdown_from_day_best_percent": -1}, "max_drawdown_from_day_best_percent"),
])
def test_the_breakers_settings_are_checked(config, problem):
    found = breakers_refusal(config)
    assert (found is None) if problem is None else (problem in found)


# ── as the trader keeps them ──


class RiskState:
    def __init__(self, resumed_at=None):
        self.paused_since = None
        self.by_guard = None
        self.resumed_at = resumed_at

    async def get_entries_paused_since(self):
        return self.paused_since

    async def get_entries_paused_by_guard(self):
        return self.by_guard

    async def get_entries_resumed_at(self):
        return self.resumed_at

    async def pause_entries_by_guard(self, ts):
        if self.paused_since is None:
            self.paused_since = ts
        if self.by_guard is None:
            self.by_guard = ts


class Balances:
    def __init__(self, highest=None):
        self.highest = highest
        self.asked = []

    async def highest_since(self, moment):
        self.asked.append(moment)
        return self.highest


class Positions:
    def __init__(self, results):
        self.results = results

    async def get_today_closed_results(self):
        return self.results


def _rule(risk_config: dict, *, risk_state=None, balances=None, positions=None) -> TradingRule:
    rules = {
        "allowed_indices": {"indice_ids": {"us100": "1909050"}},
        "market_closed_dates": {"market_closed_dates": []},
        "day_trading": {"dont_enter_trade_if_day_profit_is_more_than": 1.25, "max_day_loss_percent": -25.0},
        "signal_validation": {"max_signal_age_seconds": 8},
        "market_hours": {},
        "risk_management": risk_config,
    }
    config_manager = MagicMock()
    config_manager.get_config_value.side_effect = lambda key, default=None: (
        [{"rule_type": rule_type, "rule_config": rule_config} for rule_type, rule_config in rules.items()]
        if key == "trade.rules"
        else default
    )
    return TradingRule(config_manager, positions or Positions([]), risk_state, balances)


def test_the_trader_pauses_entries_once_said_once_when_the_account_falls_under_the_floor():
    risk_state, said = RiskState(), []

    async def notify(message):
        said.append(message)

    rule = _rule({"pause_below_balance_eur": 20}, risk_state=risk_state, balances=Balances())
    assert asyncio.run(rule.guard_account(25.0, notify)) is None

    with pytest.raises(TradingRuleViolation, match="under the account guard's 20 €: new entries are paused until"):
        asyncio.run(rule.check_account_guard(19.5, notify))

    assert risk_state.paused_since is not None and risk_state.by_guard is not None
    # Paused already: nothing more is said or set.
    assert asyncio.run(rule.guard_account(10.0, notify)) is None
    assert len(said) == 1 and said[0].startswith("⏸️ WATA: new entries paused by the account guard.")


def test_the_highest_is_the_days_recorded_since_the_resume_and_what_the_trader_read_since():
    resumed = datetime.now(timezone.utc) + timedelta(seconds=1)
    balances = Balances(highest=100.0)
    rule = _rule({"pause_after_drawdown_percent": 30}, risk_state=RiskState(resumed_at=resumed), balances=balances)

    # The recorded 100 € since the resume: 75 is 25 % under it.
    assert asyncio.run(rule.guard_account(75.0)) is None
    assert balances.asked == [resumed]
    # Read since: 120 € is the highest now, and 80 is a third under it.
    balances.highest = None
    assert asyncio.run(rule.guard_account(120.0)) is None
    assert "33.3 % under its highest" in asyncio.run(rule.guard_account(80.0))


def test_a_resume_after_the_values_were_read_starts_the_highest_afresh():
    risk_state = RiskState()
    rule = _rule({"pause_after_drawdown_percent": 30}, risk_state=risk_state, balances=Balances())
    asyncio.run(rule.guard_account(200.0))

    risk_state.resumed_at = datetime.now(timezone.utc) + timedelta(seconds=1)
    # 100 € was 50 % under the 200 € read before the resume, which count no more.
    assert asyncio.run(rule.guard_account(100.0)) is None


def test_an_off_guard_or_an_unknown_value_decides_nothing():
    risk_state = RiskState()
    assert asyncio.run(_rule({}, risk_state=risk_state).guard_account(0.0)) is None
    assert asyncio.run(_rule({"pause_below_balance_eur": 50}, risk_state=risk_state).guard_account(None)) is None
    assert risk_state.paused_since is None


def test_the_trader_takes_no_more_entry_today_after_the_breakers_trip():
    asyncio.run(_rule({"max_consecutive_losses": 3}, positions=Positions([-1, -1])).check_day_breakers())
    with pytest.raises(TradingRuleViolation, match="3 losing trades in a row today"):
        asyncio.run(_rule({"max_consecutive_losses": 3}, positions=Positions([-1, -1, -1])).check_day_breakers())
    with pytest.raises(TradingRuleViolation, match="points under its best"):
        asyncio.run(_rule({"max_drawdown_from_day_best_percent": 5}, positions=Positions([4, -6])).check_day_breakers())
    # Off: nothing refuses, whatever the day.
    asyncio.run(_rule({}, positions=Positions([-1, -1, -1, -1])).check_day_breakers())
