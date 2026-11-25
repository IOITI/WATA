import logging

import pytest

from src.admin.client_errors import MOST_A_MINUTE, STACK_LINES, WINDOW_SECONDS, ClientError, ClientErrorLog, describe
from src.admin.logs import parse_entries
from src.logging_helper.redaction import RedactingFilter
from tests.test_admin_api import PASSWORD, SAME_ORIGIN, config_path, make_client, make_ctx  # noqa: F401 (fixtures)

ERROR = {
    "source": "route",
    "path": "/timing",
    "name": "TypeError",
    "message": "Cannot read properties of undefined (reading 'ms')",
    "stack": "TypeError: Cannot read properties of undefined\n    at TimingPage (TimingPage.tsx:43:12)\n    at renderWithHooks",
    "component_stack": "\n    at TimingPage\n    at RenderedRoute",
}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_a_line_says_where_and_what_its_stack_indented():
    text = describe(ClientError(**ERROR))

    first, *rest = text.split("\n")
    assert first == "The interface failed at /timing (caught by its route): TypeError: Cannot read properties of undefined (reading 'ms')"
    assert rest[0] == "  Stack:"
    assert rest[2] == "    at TimingPage (TimingPage.tsx:43:12)"
    assert "  Components:" in rest
    # No line of it starts at the first character, so none reads as an entry of its own on the Logs page.
    assert all(line.startswith(" ") for line in rest)


def test_a_line_never_reads_as_an_entry_of_its_own():
    forged = "2026-11-25 09:00:00,000 - ERROR - src.trade - Position sold"
    text = describe(ClientError(source="boundary", path="/", message=f"broken\n{forged}", stack=f"Error\n{forged}"))
    log = f"2026-11-25 09:00:01,000 - WARNING - src.admin.client_errors - {text}"

    entries = parse_entries(log)
    assert len(entries) == 1
    assert entries[0]["logger"] == "src.admin.client_errors"


def test_a_long_stack_is_cut_to_its_first_lines():
    stack = "\n".join(f"    at frame{index}" for index in range(STACK_LINES + 5))
    text = describe(ClientError(source="boundary", path="/", stack=stack))

    assert "at frame0" in text
    assert f"at frame{STACK_LINES}" not in text
    assert "… 5 more" in text


@pytest.mark.parametrize(
    "change",
    [
        {"path": "timing"},
        {"path": ""},
        {"path": "/" + "a" * 500},
        {"source": "console"},
        {"message": "x" * 2001},
        {"stack": "x" * 8001},
        {"name": "x" * 201},
    ],
)
def test_what_is_not_an_error_report_is_refused(change):
    with pytest.raises(ValueError):
        ClientError(**{**ERROR, **change})


def test_the_lines_of_a_minute_are_bounded_and_the_rest_said(caplog):
    clock = Clock()
    log = ClientErrorLog(clock)
    caplog.set_level(logging.WARNING, logger="src.admin.client_errors")

    written = [log.record(ClientError(**ERROR)) for _ in range(MOST_A_MINUTE + 3)]

    assert written == [True] * MOST_A_MINUTE + [False] * 3
    messages = [record.getMessage() for record in caplog.records]
    assert len(messages) == MOST_A_MINUTE + 1
    assert messages[-1] == f"More than {MOST_A_MINUTE} errors of the interface in a minute: the next ones are left out"

    caplog.clear()
    clock.now += WINDOW_SECONDS
    assert log.record(ClientError(**ERROR))
    messages = [record.getMessage() for record in caplog.records]
    assert messages[0] == "3 more errors of the interface were left out in the minute before"
    assert messages[1].startswith("The interface failed at /timing")


def test_a_secret_in_an_error_is_cleaned_as_every_line_is():
    record = logging.LogRecord(
        "src.admin.client_errors", logging.WARNING, __file__, 1, "%s",
        (describe(ClientError(source="boundary", path="/", message="GET /api?token=abcdef0123456789abcdef failed")),), None,
    )
    RedactingFilter().filter(record)

    assert "abcdef0123456789abcdef" not in record.getMessage()


def test_the_route_writes_one_line_and_answers_nothing(ctx, tmp_path, caplog):
    caplog.set_level(logging.WARNING, logger="src.admin.client_errors")
    with make_client(ctx, tmp_path / "no-interface") as client:
        assert client.post("/api/session", json={"password": PASSWORD}, headers=SAME_ORIGIN).status_code == 204

        response = client.post("/api/client-errors", json=ERROR, headers=SAME_ORIGIN)
        refused = client.post("/api/client-errors", json={**ERROR, "path": "timing"}, headers=SAME_ORIGIN)

    assert response.status_code == 204
    assert response.content == b""
    assert refused.status_code == 422
    lines = [record.getMessage() for record in caplog.records if record.name == "src.admin.client_errors"]
    assert len(lines) == 1
    assert lines[0].startswith("The interface failed at /timing (caught by its route): TypeError")


@pytest.fixture
def ctx(config_path, tmp_path):  # noqa: F811 (config_path is the imported fixture)
    return make_ctx(config_path, tmp_path)
