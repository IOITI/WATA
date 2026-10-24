import logging
import logging.config
import os

import pytest

from src.logging_helper import QUIET_LOGGERS, setup_logging
from src.logging_helper.redaction import (
    MASK,
    AccessRedactingFilter,
    RedactingFilter,
    forget_secrets,
    redact,
    register_secret,
    uvicorn_log_config,
)


@pytest.fixture(autouse=True)
def no_secret_registered():
    forget_secrets()
    yield
    forget_secrets()


@pytest.mark.parametrize("line, secret", [
    # TradingView's webhook, as uvicorn's access log writes it.
    ('"POST /webhook?token=5f2c9a7e1b HTTP/1.1" 200', "5f2c9a7e1b"),
    # The stream's address.
    ("wss://streaming.saxobank.com/oapi/streaming/ws/connect?authorization=BEARER%20eyJhbGciOi.x.y&contextId=Z",
     "eyJhbGciOi.x.y"),
    ("Authorization: Bearer eyJhbGciOiJFUzI1NiIs", "eyJhbGciOiJFUzI1NiIs"),
    # httpx's line for the direct Telegram fallback.
    ('HTTP Request: POST https://api.telegram.org/bot123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw/sendMessage "HTTP/1.1 200 OK"',
     "AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"),
    ("GET https://gateway.saxobank.com/openapi/port/v1/positions/?ClientKey=Ab3x-Q9&AccountKey=Kz8%7C1 200",
     "Ab3x-Q9"),
    ("GET https://gateway.saxobank.com/openapi/port/v1/positions/?ClientKey=Ab3x-Q9&AccountKey=Kz8%7C1 200",
     "Kz8%7C1"),
    ("Connection to amqp://trade-app:pykvys-9nixqo@rabbitmq1:5672/ failed", "pykvys-9nixqo"),
    ("postgresql://wata:wata_secret_change_me@postgres1:5432/wata", "wata_secret_change_me"),
])
def test_the_shapes_of_secrets_are_masked(line, secret):
    cleaned = redact(line)

    assert secret not in cleaned
    assert MASK in cleaned


def test_what_is_not_a_secret_is_left_as_it_is():
    line = "Position 5123 sold at 12.34 (stop -7.00 %), order 98765, contextId=wata_pm_1"

    assert redact(line) == line


def test_a_registered_value_is_masked_wherever_it_is_and_a_new_one_replaces_it():
    register_secret("heartbeat.url", "https://hc-ping.com/0b2c7f6e-uuid")
    assert redact("pinged https://hc-ping.com/0b2c7f6e-uuid at 10:00") == f"pinged {MASK} at 10:00"

    register_secret("heartbeat.url", "https://hc-ping.com/new-uuid-1234")
    # The value before is no longer a secret of this process: the name stands for the new one.
    assert "new-uuid-1234" not in redact("https://hc-ping.com/new-uuid-1234")
    assert redact("https://hc-ping.com/0b2c7f6e-uuid") == "https://hc-ping.com/0b2c7f6e-uuid"


def test_what_does_not_look_like_a_secret_is_not_taken_for_one():
    register_secret("chat", "12345")
    register_secret("nothing", None)
    # A test configuration's RabbitMQ password: masking it would mask the word in every line.
    register_secret("rabbitmq.authentication.password", "password")

    assert redact("order 12345, no admin password is set") == "order 12345, no admin password is set"


def _record(message, *args, exc_info=None):
    return logging.LogRecord("httpx", logging.INFO, __file__, 1, message, args, exc_info)


def test_the_filter_writes_a_line_cleaned_whatever_its_arguments():
    register_secret("capital.api_key", "cap-key-0123456789")
    record = _record("login with %s to %s", "cap-key-0123456789", "https://api.telegram.org/bot42:secretpart/getMe")

    assert RedactingFilter().filter(record) is True
    assert record.getMessage() == f"login with {MASK} to https://api.telegram.org/bot{MASK}/getMe"


def test_the_filter_cleans_the_text_of_an_exception():
    try:
        raise ValueError("Client error '401 Unauthorized' for url 'https://x/?AccountKey=secretkey99'")
    except ValueError:
        import sys

        record = _record("request failed", exc_info=sys.exc_info())

    RedactingFilter().filter(record)

    assert "secretkey99" not in record.exc_text
    assert "ValueError" in record.exc_text


def test_the_filter_never_stops_a_line():
    record = _record("%d items", "not a number")

    assert RedactingFilter().filter(record) is True


def test_the_access_log_keeps_its_five_arguments_for_uvicorns_formatter():
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                               ("10.0.0.1:5000", "POST", "/webhook?token=abcdef123", "1.1", 200), None)

    AccessRedactingFilter().filter(record)

    assert record.args == ("10.0.0.1:5000", "POST", f"/webhook?token={MASK}", "1.1", 200)


def test_uvicorns_logging_is_cleaned_on_every_handler():
    config = uvicorn_log_config()

    logging.config.dictConfig(config)
    try:
        for name, handler in config["handlers"].items():
            assert handler["filters"] == ["redact_access" if name == "access" else "redact"]
        access_filters = logging.getLogger("uvicorn.access").handlers[0].filters
        assert any(isinstance(item, AccessRedactingFilter) for item in access_filters)
    finally:
        for name in config["loggers"]:
            for handler in list(logging.getLogger(name).handlers):
                logging.getLogger(name).removeHandler(handler)


class _Config:
    def __init__(self, log_path, level="DEBUG"):
        self.values = {
            "logging": {"level": level, "format": "%(levelname)s %(name)s %(message)s", "persistant": {"log_path": log_path}},
            "telegram.bot_token": "123456:ABCdefGHIjklMNO",
            "postgresql.dsn": "postgresql://wata:db-password-xyz@postgres1:5432/wata",
        }

    def get_logging_config(self):
        return self.values["logging"]

    def get_config_value(self, path, default=None):
        return self.values.get(path, default)


def test_a_service_log_holds_no_secret_and_the_chatty_libraries_stay_quiet(tmp_path):
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        setup_logging(_Config(str(tmp_path)), "wata-test")
        logging.getLogger("wata").info("bot 123456:ABCdefGHIjklMNO, base postgresql://wata:db-password-xyz@db/wata")
        logging.getLogger("httpx").info("HTTP Request: GET https://hc-ping.com/secret-uuid")
        for handler in root.handlers:
            handler.flush()
    finally:
        for handler in root.handlers[len(before):]:
            root.removeHandler(handler)
            handler.close()

    written = open(os.path.join(tmp_path, "wata-test", "wata-test.log")).read()
    assert "ABCdefGHIjklMNO" not in written and "db-password-xyz" not in written
    # DEBUG for WATA's own lines, never below WARNING for the libraries that name whole addresses.
    assert "secret-uuid" not in written
    for name in QUIET_LOGGERS:
        assert logging.getLogger(name).level == logging.WARNING


def test_the_admin_shows_no_secret_of_lines_written_before_and_cannot_be_searched_for_one(tmp_path):
    from src.admin.logs import read_log

    folder = tmp_path / "wata-api"
    folder.mkdir()
    (folder / "wata-api.log").write_text(
        "2026-10-24 10:00:00,000 - INFO - uvicorn.access - POST /webhook?token=tv-token-123456 200\n"
    )

    shown = read_log(str(tmp_path), "wata-api")
    searched = read_log(str(tmp_path), "wata-api", query="tv-token-123456")

    assert "tv-token-123456" not in shown["entries"][0]["message"]
    assert searched["entries"] == []
