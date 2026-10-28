"""
What a log line must never hold, taken out before it is written.

Two kinds of secret reach the logs: those that addresses and headers carry
(the webhook's ``?token=``, a Bearer header, the stream's
``?authorization=BEARER%20…``, Telegram's ``/bot<id>:<token>/`` address,
Saxo's account keys in a query string, the password of an
``amqp://user:password@`` address), found by their shape; and the values a
service holds and registered (``register_secret``), found wherever they are.

``RedactingFilter`` goes on every handler ``setup_logging`` installs, so that
whatever logger a library writes with, its line is cleaned before the file
sees it. ``redact`` is what the admin applies to the lines it shows, which
include those written before this existed.
"""

import copy
import logging
import re
import threading

MASK = "***"
# A value is only taken for a secret when it looks like one: masking a short value, or a plain word (a test
# configuration's RabbitMQ password is "password"), would mask ordinary words and numbers everywhere.
MIN_SECRET_LENGTH = 8
PLAIN_WORD_SECRET_LENGTH = 16

_VALUE = r"[^&\s\"'<>]+"

_PATTERNS = (
    # The stream's address: ?authorization=BEARER%20<token> (before the generic Bearer below).
    (re.compile(r"(?i)\b(authorization=bearer(?:%20|\+|\s))" + _VALUE), r"\1" + MASK),
    # The webhook's address, or any other that carries a token in its query.
    (re.compile(r"(?i)\b((?:access_|refresh_)?token=)" + _VALUE), r"\1" + MASK),
    # An Authorization header, or a Bearer token said in a message.
    (re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]+"), r"\1" + MASK),
    # Telegram's address carries the bot's token: https://api.telegram.org/bot<id>:<secret>/sendMessage
    (re.compile(r"(/bot)\d+:[A-Za-z0-9_-]+"), r"\1" + MASK),
    # Saxo's account keys, which its addresses carry in their query.
    (re.compile(r"\b(AccountKey=|ClientKey=)" + _VALUE), r"\1" + MASK),
    # A password in an address: amqp://user:password@host, postgresql://user:password@host.
    (re.compile(r"\b([a-zA-Z][a-zA-Z0-9+.-]*://[^:/@\s]+:)[^@\s/]+@"), r"\1" + MASK + "@"),
    # The Claude API's key (the admin's AI agent): as a header, or as it is written anywhere else.
    (re.compile(r"(?i)\b(x-api-key[\"']?\s*[:=]\s*[\"']?)" + _VALUE), r"\1" + MASK),
    (re.compile(r"\bsk-ant-[A-Za-z0-9_-]{8,}"), "sk-ant-" + MASK),
)

_lock = threading.Lock()
# What each name stands for now: a renewed token replaces the one before it.
_values: dict[str, str] = {}
_ordered: tuple[str, ...] = ()


def register_secret(name: str, value) -> None:
    """
    Never write ``value`` in a log line from now on. ``name`` says what it is:
    registering a name again replaces the value it stood for.
    """
    global _ordered
    if not _looks_secret(value):
        value = None
    with _lock:
        if value is None:
            _values.pop(name, None)
        else:
            _values[name] = value
        # The longest first, so that a secret holding another is masked whole.
        _ordered = tuple(sorted(set(_values.values()), key=len, reverse=True))


def _looks_secret(value) -> bool:
    """Long enough, and not a plain word unless a long one: a token, a key, a password of some strength."""
    if not isinstance(value, str) or len(value) < MIN_SECRET_LENGTH:
        return False
    return len(value) >= PLAIN_WORD_SECRET_LENGTH or not value.isalpha()


def forget_secrets() -> None:
    """For the tests: no value registered."""
    global _ordered
    with _lock:
        _values.clear()
        _ordered = ()


def redact(text):
    """``text`` without any secret: the registered values, then the shapes of secrets."""
    if not isinstance(text, str) or not text:
        return text
    for value in _ordered:
        if value in text:
            text = text.replace(value, MASK)
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class RedactingFilter(logging.Filter):
    """
    Cleans each record before its handler writes it: the message (formatted
    with its arguments), and the text of an exception or a stack, whose
    messages may hold an address. It never drops a record and never raises.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
            cleaned = redact(message)
            if cleaned != message:
                record.msg = cleaned
                record.args = ()
            if record.exc_info and not record.exc_text:
                # The handler's formatter uses exc_text once it is set.
                record.exc_text = logging.Formatter().formatException(record.exc_info)
            if record.exc_text:
                record.exc_text = redact(record.exc_text)
            if record.stack_info:
                record.stack_info = redact(record.stack_info)
        except Exception:
            pass
        return True


class AccessRedactingFilter(logging.Filter):
    """
    uvicorn's access log keeps its arguments apart (client, method, path,
    version, status) for its formatter to colour: only the path is cleaned,
    in place, so that the formatter still finds all five.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.args, tuple) and len(record.args) == 5:
                client, method, path, version, status = record.args
                record.args = (client, method, redact(path), version, status)
            else:
                return RedactingFilter().filter(record)
        except Exception:
            pass
        return True


def uvicorn_log_config() -> dict:
    """uvicorn's own logging (its stdout), each line cleaned: the access log names every path, the webhook's token in its query."""
    from uvicorn.config import LOGGING_CONFIG

    config = copy.deepcopy(LOGGING_CONFIG)
    config.setdefault("filters", {}).update({
        "redact": {"()": RedactingFilter},
        "redact_access": {"()": AccessRedactingFilter},
    })
    for name, handler in config["handlers"].items():
        handler.setdefault("filters", []).append("redact_access" if name == "access" else "redact")
    return config
