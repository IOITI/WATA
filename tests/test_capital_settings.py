from unittest.mock import MagicMock

import pytest

from src.capital_feed.settings import (
    DEFAULT_PRICES_ROOT,
    MAX_INSTRUMENTS,
    REST_URLS,
    CapitalInstrument,
    CapitalSettings,
    Credentials,
)


def _config(section=None, admin=None, timezone_name="Europe/Paris"):
    values = {
        "trade.config.capital_feed": section,
        "trade.config.general.timezone": timezone_name,
        "admin": admin,
    }
    manager = MagicMock()
    manager.get_config_value.side_effect = lambda key, default=None: values.get(key) if values.get(key) is not None else default
    return manager


def test_the_feed_is_off_on_the_demo_account_and_follows_the_nasdaq_unless_told_otherwise():
    settings = CapitalSettings.from_config(_config())

    assert settings.enabled is False
    assert settings.environment == "demo"
    assert settings.rest_url == "https://demo-api-capital.backend-capital.com"
    assert settings.instruments == (CapitalInstrument("US100", "us100"),)
    assert (settings.minute_volume, settings.day_files) == (True, True)
    assert (settings.retention_months, settings.alert_after_seconds) == (0, 300)
    assert settings.prices_root == DEFAULT_PRICES_ROOT
    assert settings.timezone == "Europe/Paris"


def test_settings_follow_the_section():
    settings = CapitalSettings.from_config(_config({
        "enabled": True,
        "environment": "live",
        "instruments": [{"epic": "US100", "label": "us100"}, {"epic": "US500", "label": "us500"}],
        "minute_volume": False,
        "day_files": False,
        "retention_months": 12,
        "alert_after_seconds": 120,
    }, admin={"persistant": {"price_history_path": "/data/prices"}}))

    assert settings.enabled is True
    assert settings.rest_url == REST_URLS["live"] == "https://api-capital.backend-capital.com"
    assert settings.epics == ("US100", "US500")
    assert settings.label_of("US500") == "us500" and settings.label_of("DE40") is None
    assert (settings.minute_volume, settings.day_files) == (False, False)
    assert (settings.retention_months, settings.alert_after_seconds) == (12, 120)
    # Where the admin reads the history of prices: the feed writes its days there too.
    assert settings.prices_root == "/data/prices"


@pytest.mark.parametrize("section, words", [
    ({"enabled": "yes"}, "enabled"),
    ({"environment": "paper"}, "environment"),
    ({"minute_volume": 1}, "minute_volume"),
    ({"day_files": None}, "day_files"),
    ({"retention_months": -1}, "retention_months"),
    ({"alert_after_seconds": 10}, "alert_after_seconds"),
    ({"instruments": []}, "at least one"),
    ({"instruments": "US100"}, "instruments"),
    ({"instruments": [{"epic": "us100", "label": "us100"}]}, "epic"),
    ({"instruments": [{"epic": "US100"}]}, "label"),
    ({"instruments": [{"epic": "US100", "label": "US 100"}]}, "label"),
    ({"instruments": [{"epic": "US100", "label": "a"}, {"epic": "US100", "label": "b"}]}, "twice"),
    ({"instruments": [{"epic": "US100", "label": "x"}, {"epic": "US500", "label": "x"}]}, "labelled"),
    ({"instruments": [{"epic": f"E{index}", "label": f"e{index}"} for index in range(MAX_INSTRUMENTS + 1)]}, "limit"),
])
def test_a_mistake_in_the_section_is_refused_with_its_place(section, words):
    with pytest.raises(ValueError) as refused:
        CapitalSettings.from_config(_config(section))

    assert words in str(refused.value)
    assert "trade.config.capital_feed" in str(refused.value)


def test_the_address_of_capital_com_is_not_a_setting():
    # Whoever could change it would be sent the key.
    settings = CapitalSettings.from_config(_config({"rest_url": "https://elsewhere.example"}))

    assert settings.rest_url == REST_URLS["demo"]


def test_the_login_comes_from_three_variables_of_the_environment():
    environ = {"CAPITAL_API_KEY": " key-1 ", "CAPITAL_IDENTIFIER": "me@example.com", "CAPITAL_PASSWORD": "pass word"}

    credentials = Credentials.from_environment(environ)

    assert credentials == Credentials("key-1", "me@example.com", "pass word")
    assert credentials.secrets() == ("key-1", "pass word")


@pytest.mark.parametrize("missing", ["CAPITAL_API_KEY", "CAPITAL_IDENTIFIER", "CAPITAL_PASSWORD"])
def test_without_any_of_the_three_there_is_no_login_and_it_is_said_which(missing):
    environ = {"CAPITAL_API_KEY": "key-1", "CAPITAL_IDENTIFIER": "me@example.com", "CAPITAL_PASSWORD": "pw"}
    environ[missing] = "  "

    assert Credentials.from_environment(environ) is None
    assert Credentials.missing(environ) == [missing]


def test_the_key_and_the_password_never_show_in_a_message():
    credentials = Credentials("key-1234", "me@example.com", "secret-pw")

    for text in (repr(credentials), str(credentials), f"{credentials}"):
        assert "key-1234" not in text and "secret-pw" not in text
        assert "me@example.com" in text
