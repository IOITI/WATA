import json

import pytest

from src.backtest.settings import (
    DEFAULT_CAPITAL,
    FIELDS,
    BacktestSettings,
    InvalidSettings,
    from_config,
    options,
    with_changes,
)

with open("etc/config_example.json", encoding="utf-8") as config_file:
    EXAMPLE = json.load(config_file)


def test_the_live_settings_are_read_where_the_services_read_them():
    settings = from_config(EXAMPLE, capital=25_000)

    assert (settings.timezone, settings.trading_start_hour, settings.trading_end_hour) == ("Europe/Paris", 8, 22)
    assert (settings.risky_start_hour, settings.risky_start_minute) == (21, 54)
    assert (settings.stoploss_percent, settings.max_profit_percent, settings.trailing_enabled) == (-15, 60, True)
    assert (settings.trailing_activation_percent, settings.trailing_drawdown_percent) == (5, 8)
    assert (settings.day_profit_target, settings.no_entry_above_day_profit, settings.max_day_loss) == (1.7, 1.25, -25.0)
    assert (settings.close_position_time, settings.cooldown_after_loss_minutes, settings.max_trades_per_day) == ("21:55", 10, 8)
    assert (settings.turbo_min_price, settings.turbo_max_price) == (4, 15)
    assert (settings.funds_percent, settings.reserve_cash_percent, settings.safety_units) == (100, 0, 1)
    assert settings.allowed_indices == ("us100",)
    assert "01/01/2026" in settings.closed_dates
    assert [(period.start_minute, period.end_minute, period.scale_percent) for period in settings.time_periods] == [
        (480, 870, 100), (870, 960, 50), (960, 1320, 75),
    ]
    assert (settings.confidence_enabled, settings.min_confidence, settings.default_confidence) == (True, 0.3, 1.0)
    assert [(band.least, band.below, band.scale_percent) for band in settings.confidence_bands][0] == (0.0, 0.5, 50)
    assert (settings.history_days, settings.restart_time) == (1, "21:15")
    assert settings.capital == 25_000


def test_what_the_configuration_leaves_out_takes_the_services_defaults():
    settings = from_config({"trade": {"rules": [], "config": {}}})

    assert (settings.cooldown_after_loss_minutes, settings.max_trades_per_day, settings.min_confidence) == (0, 0, 0.0)
    assert (settings.trailing_enabled, settings.time_scaling_enabled, settings.confidence_enabled) == (False, False, False)
    assert (settings.funds_percent, settings.safety_units, settings.capital) == (100, 1, DEFAULT_CAPITAL)
    assert settings.allowed_indices == ()


def test_a_run_changes_only_what_it_says_each_change_checked():
    live = from_config(EXAMPLE)

    changed, kept, before = with_changes(live, {"stoploss_percent": -12, "trailing_enabled": True, "order_seconds": 2})

    assert (changed.stoploss_percent, changed.order_seconds) == (-12, 2)
    # On already: not a change.
    assert kept == {"stoploss_percent": -12, "order_seconds": 2}
    assert before == {"stoploss_percent": -15, "order_seconds": 1.0}


@pytest.mark.parametrize("changes, message", [
    ({"stoploss_percent": 5}, "between -100 and -0.1"),
    ({"max_trades_per_day": 2.5}, "whole number"),
    ({"trailing_enabled": "yes"}, "on or off"),
    ({"close_position_time": "25:00"}, "time of day"),
    ({"stop_fill": "best"}, "one of worst, level"),
    ({"allowed_indices": ["de40"]}, "not a setting a backtest can change"),
    ({"trading_start_hour": 23}, "end before they start"),
    ({"turbo_min_price": 20}, "lowest price is above"),
])
def test_a_change_that_cannot_be_made_is_refused_saying_why(changes, message):
    with pytest.raises(InvalidSettings, match=message):
        with_changes(from_config(EXAMPLE), changes)


def test_a_new_run_waits_for_a_fresh_quote_and_is_priced_at_the_next_one_while_a_run_made_before_keeps_what_it_had():
    live = from_config(EXAMPLE)
    switched_off = json.loads(json.dumps(EXAMPLE))
    switched_off["trade"]["config"]["position_management"]["wait_for_fresh_quote"] = False
    # A run's settings.json of before: neither key.
    before = {name: value for name, value in live.to_dict().items() if name not in ("wait_for_fresh_quote", "fill_price")}

    assert (live.wait_for_fresh_quote, live.fill_price) == (True, "next")
    assert from_config(switched_off).wait_for_fresh_quote is False
    assert (BacktestSettings.from_dict(before).wait_for_fresh_quote, BacktestSettings.from_dict(before).fill_price) == (
        False, "standing",
    )
    changed, _, _ = with_changes(live, {"fill_price": "standing", "wait_for_fresh_quote": False})
    assert (changed.fill_price, changed.wait_for_fresh_quote) == ("standing", False)
    with pytest.raises(InvalidSettings, match="one of standing, next"):
        with_changes(live, {"fill_price": "later"})


def test_a_run_keeps_its_settings_and_reads_them_back():
    settings = from_config(EXAMPLE, capital=12_345)

    again = BacktestSettings.from_dict(json.loads(json.dumps(settings.to_dict())))

    assert again == settings


def test_the_form_is_told_every_setting_with_its_live_value():
    listed = options(from_config(EXAMPLE))

    assert [entry["name"] for entry in listed] == [entry.name for entry in FIELDS]
    stop = next(entry for entry in listed if entry["name"] == "stoploss_percent")
    assert (stop["value"], stop["group"], stop["least"], stop["most"]) == (-15, "exits", -100, -0.1)
