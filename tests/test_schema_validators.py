"""
The signal schemas' validators built once (src/schema/validators.py): they
take and refuse what ``jsonschema.validate`` took and refused, with its error.
"""

import jsonschema
import pytest

from src.schema import trading_action_schema, webhook_schema
from src.schema.validators import check_trading_action, check_webhook

SIGNAL = {"action": "long", "indice": "us100", "signal_timestamp": "2026-11-25T11:25:50Z",
          "alert_timestamp": "2026-11-25T11:25:50Z"}

REFUSED = [
    {},
    {**SIGNAL, "action": "buy"},
    {**SIGNAL, "action": None},
    {**SIGNAL, "indice": 100},
    {**SIGNAL, "signal_timestamp": "25/11/2026 11:25"},
    {**SIGNAL, "alert_timestamp": 1764069950},
    {**SIGNAL, "confidence": 2.5},
    {**SIGNAL, "confidence": "high"},
    {key: value for key, value in SIGNAL.items() if key != "indice"},
    # Several faults at once: the one said is the one jsonschema.validate chose.
    {"action": "buy", "indice": 7, "signal_timestamp": "now"},
    "long", None, [SIGNAL],
]


def _said_by_jsonschema(instance, schema):
    try:
        jsonschema.validate(instance=instance, schema=schema)
    except jsonschema.exceptions.ValidationError as error:
        return error.message, list(error.absolute_path), error.validator
    return None


def _said(check, instance):
    try:
        check(instance)
    except jsonschema.exceptions.ValidationError as error:
        return error.message, list(error.absolute_path), error.validator
    return None


@pytest.mark.parametrize("check, schema", [(check_webhook, webhook_schema), (check_trading_action, trading_action_schema)])
def test_a_signal_is_taken_as_jsonschema_validate_took_it(check, schema):
    for action in ("long", "short", "close-position", "daily_stats"):
        assert _said(check, {**SIGNAL, "action": action}) is None
    assert _said(check, {**SIGNAL, "confidence": 1.5}) is None


@pytest.mark.parametrize("instance", REFUSED)
@pytest.mark.parametrize("check, schema", [(check_webhook, webhook_schema), (check_trading_action, trading_action_schema)])
def test_what_is_refused_is_refused_with_the_error_jsonschema_validate_raised(check, schema, instance):
    said = _said(check, instance)

    assert said is not None
    assert said == _said_by_jsonschema(instance, schema)


def test_the_trader_takes_what_the_web_server_adds_and_the_messages_of_its_other_queue():
    stored = {**SIGNAL, "signal_uuid": "f7991931-f7d9-4f84-908a-3ddc1c5ee42a", "received_timestamp": "2026-11-25T11:25:50.197Z"}

    assert _said(check_trading_action, stored) is None
    assert _said(check_trading_action, {**stored, "action": "check_positions_on_saxo_api"}) is None
    # The webhook takes no such action.
    assert _said(check_webhook, {**SIGNAL, "action": "check_positions_on_saxo_api"}) is not None
