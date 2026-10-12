from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.price_recorder.settings import ExtraInstrument, RecorderSettings


def _config(section=None, rules=None, timezone_name="Europe/Paris"):
    values = {
        "trade.config.price_recorder": section,
        "trade.config.general.timezone": timezone_name,
        "trade.rules": rules if rules is not None else [
            {"rule_type": "market_hours", "rule_config": {"trading_start_hour": 8, "trading_end_hour": 22}},
            {"rule_type": "market_closed_dates", "rule_config": {"market_closed_dates": ["25/12/2026"]}},
        ],
    }
    manager = MagicMock()
    manager.get_config_value.side_effect = lambda key, default=None: values.get(key) if values.get(key) is not None else default
    return manager


def test_the_recorder_is_off_and_keeps_everything_unless_told_otherwise():
    settings = RecorderSettings.from_config(_config())

    assert settings.enabled is False
    assert settings.refresh_interval_seconds == 300
    assert settings.max_instruments == 150
    assert settings.refresh_rate_ms == 1000
    assert settings.instruments == ()
    assert settings.retention_months == 0
    assert settings.alert_after_seconds == 300
    assert settings.timezone == "Europe/Paris"


def test_settings_follow_the_section():
    settings = RecorderSettings.from_config(_config({
        "enabled": True,
        "refresh_interval_seconds": 120,
        "max_instruments": 80,
        "refresh_rate_ms": 500,
        "instruments": [{"label": "us100", "uic": 4913, "asset_type": "CfdOnIndex"}],
        "retention_months": 18,
        "alert_after_seconds": 600,
    }))

    assert settings.enabled is True
    assert (settings.refresh_interval_seconds, settings.max_instruments, settings.refresh_rate_ms) == (120, 80, 500)
    assert settings.instruments == (ExtraInstrument("us100", 4913, "CfdOnIndex"),)
    assert (settings.retention_months, settings.alert_after_seconds) == (18, 600)


@pytest.mark.parametrize("section, field", [
    ({"enabled": "yes"}, "enabled"),
    ({"refresh_interval_seconds": 5}, "refresh_interval_seconds"),
    ({"refresh_interval_seconds": "300"}, "refresh_interval_seconds"),
    ({"max_instruments": 0}, "max_instruments"),
    ({"max_instruments": True}, "max_instruments"),
    ({"refresh_rate_ms": 10}, "refresh_rate_ms"),
    ({"retention_months": -1}, "retention_months"),
    ({"alert_after_seconds": 1}, "alert_after_seconds"),
    ({"instruments": {"uic": 1}}, "instruments"),
    ({"instruments": ["us100"]}, "instruments[0]"),
    ({"instruments": [{"uic": 4913, "asset_type": "CfdOnIndex"}]}, "instruments[0]"),
    ({"instruments": [{"label": "us100", "uic": "4913", "asset_type": "CfdOnIndex"}]}, "instruments[0]"),
    ({"instruments": [{"label": "us100", "uic": 4913}]}, "instruments[0]"),
])
def test_a_malformed_section_is_refused_naming_the_field(section, field):
    with pytest.raises(ValueError, match=rf"trade\.config\.price_recorder\.{field.replace('[', r'\[').replace(']', r'\]')}"):
        RecorderSettings.from_config(_config(section))


def test_a_section_that_is_not_an_object_is_refused():
    with pytest.raises(ValueError, match="must be an object"):
        RecorderSettings.from_config(_config(["enabled"]))


def _at(text):
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def test_trading_time_is_a_weekday_inside_the_hours_of_the_trading_timezone():
    settings = RecorderSettings.from_config(_config())

    # Monday 12 Oct 2026. Paris is UTC+2.
    assert settings.is_trading_time(_at("2026-10-12T06:00:00")) is True     # 08:00
    assert settings.is_trading_time(_at("2026-10-12T05:59:59")) is False    # 07:59
    assert settings.is_trading_time(_at("2026-10-12T19:59:59")) is True     # 21:59
    assert settings.is_trading_time(_at("2026-10-12T20:00:00")) is False    # 22:00


def test_weekends_and_closed_dates_are_not_trading_time():
    settings = RecorderSettings.from_config(_config())

    assert settings.is_trading_time(_at("2026-10-10T10:00:00")) is False    # Saturday
    assert settings.is_trading_time(_at("2026-10-11T10:00:00")) is False    # Sunday
    assert settings.is_trading_time(_at("2026-12-25T10:00:00")) is False    # closed date, a Friday
    assert settings.is_trading_time(_at("2026-12-24T10:00:00")) is True


def test_without_configured_hours_every_weekday_hour_counts():
    settings = RecorderSettings.from_config(_config(rules=[]))

    assert settings.is_trading_time(_at("2026-10-12T02:00:00")) is True
    assert settings.is_trading_time(_at("2026-10-10T10:00:00")) is False
