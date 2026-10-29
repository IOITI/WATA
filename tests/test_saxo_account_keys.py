"""
Saxo's account and client keys never reach a log line (src/logging_helper/redaction.py): the services that read
them register both, through the trader's helper (which the position monitor and the price recorder use too) or the
watchlist manager's own. Until v0.59.1 the trader's start line wrote them, "Account: <key>, Client: <key>".
"""

import asyncio
import io
import logging
import sys
import types
from unittest.mock import AsyncMock

import pytest

asyncpg_stub = types.ModuleType("asyncpg")
asyncpg_stub.Pool = type("Pool", (), {})
asyncpg_stub.Record = dict
asyncpg_stub.create_pool = AsyncMock()
sys.modules.setdefault("asyncpg", asyncpg_stub)
sys.modules.setdefault("aio_pika", types.ModuleType("aio_pika"))

from src.logging_helper.redaction import MASK, RedactingFilter, forget_secrets, redact  # noqa: E402
from src.trader import get_account_info_async  # noqa: E402
from src.watchlist_manager import get_account_key_async  # noqa: E402

# Made up, in the shape of Saxo's: base64 of 16 bytes.
ACCOUNT_KEY = "Qm9ndXMtYWNjb3VudC0wMQ=="
CLIENT_KEY = "Qm9ndXMtY2xpZW50LTAwMQ=="


class AccountsMe:
    """Saxo's answer to /port/v1/accounts/me, as the services read it."""

    async def request(self, _):
        return {"Data": [{"ClientId": "9000001", "ClientKey": CLIENT_KEY, "AccountId": "9000001/EUR", "AccountKey": ACCOUNT_KEY}]}


@pytest.fixture(autouse=True)
def no_secret_registered():
    forget_secrets()
    yield
    forget_secrets()


@pytest.mark.parametrize("read", [get_account_info_async, get_account_key_async])
def test_the_keys_read_from_saxo_are_kept_out_of_every_line_whatever_names_them(read):
    asyncio.run(read(AccountsMe()))

    # An order's body, an error, a word of a message: the values themselves, not a shape.
    line = f"Order refused for {ACCOUNT_KEY} of client {CLIENT_KEY}: {{'Body': '{ACCOUNT_KEY}|{CLIENT_KEY}'}}"
    cleaned = redact(line)
    assert ACCOUNT_KEY not in cleaned and CLIENT_KEY not in cleaned
    assert cleaned.count(MASK) == 4


def test_the_trader_s_start_line_of_before_is_written_without_them():
    asyncio.run(get_account_info_async(AccountsMe()))
    written = io.StringIO()
    handler = logging.StreamHandler(written)
    handler.addFilter(RedactingFilter())
    logger = logging.getLogger("tests.saxo_account_keys")
    logger.addHandler(handler)
    try:
        logger.warning("Account: %s, Client: %s", ACCOUNT_KEY, CLIENT_KEY)
    finally:
        logger.removeHandler(handler)

    assert written.getvalue() == f"Account: {MASK}, Client: {MASK}\n"
