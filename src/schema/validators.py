"""
The signal schemas' validators, built once.

``jsonschema.validate(instance, schema)`` checks the schema itself against its
metaschema at every call, then builds a validator and throws it away: 0.77 of
the 0.81 ms a signal's validation took on a Mac, about 4 ms on the server, and
most of the memory a call touches. A signal is validated on its way to an order
by the web server, then by the trader, each in a process that had not done it
for hours: measured on the server (23 to 26 November 2026), those two steps
took 60 to 230 ms after a quiet hour where they take 5 to 9 ms.

Here each schema is checked and its validator built when the module is loaded.
A signal is then only read against it, and what is raised is what
``jsonschema.validate`` raised (``best_match`` of its errors).
"""

from jsonschema.exceptions import best_match
from jsonschema.validators import validator_for

from src.schema import trading_action_schema, webhook_schema


def _built(schema: dict):
    cls = validator_for(schema)
    cls.check_schema(schema)
    return cls(schema)


_WEBHOOK = _built(webhook_schema)
_TRADING_ACTION = _built(trading_action_schema)


def _check(validator, instance) -> None:
    error = best_match(validator.iter_errors(instance))
    if error is not None:
        raise error


def check_webhook(instance) -> None:
    """Raises ``jsonschema.exceptions.ValidationError`` unless ``instance`` is a signal as the web server takes one."""
    _check(_WEBHOOK, instance)


def check_trading_action(instance) -> None:
    """Raises ``jsonschema.exceptions.ValidationError`` unless ``instance`` is a message as the trader handles one."""
    _check(_TRADING_ACTION, instance)
