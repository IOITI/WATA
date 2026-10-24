"""
An alert does not depend on the queue whose failure it may be about: what
RabbitMQ does not take is sent to Telegram directly (src/mq_telegram/async_tools.py).
"""

import asyncio
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

aio_pika_stub = sys.modules.setdefault("aio_pika", types.ModuleType("aio_pika"))
if not hasattr(aio_pika_stub, "Message"):
    aio_pika_stub.Message = lambda body: body

from src.mq_telegram import async_tools  # noqa: E402
from src.mq_telegram.async_tools import AsyncTelegramSender  # noqa: E402

TOKEN = "123456:secret-bot-token"


def _sender(publish_error=None):
    config = MagicMock()
    config.get_config_value.side_effect = {"telegram.bot_token": TOKEN, "telegram.chat_id": "42"}.get
    sender = AsyncTelegramSender(config)
    sender._channel = MagicMock(is_closed=False)
    sender._channel.default_exchange.publish = AsyncMock(side_effect=publish_error)
    return sender


class Telegram:
    def __init__(self, status=200):
        self.posted = []
        self.status = status

    def __call__(self, timeout):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def post(self, url, json):
        self.posted.append((url, json))
        response = MagicMock()
        if self.status >= 400:
            response.raise_for_status.side_effect = RuntimeError(f"{self.status} for {url}")
        return response


def test_a_message_goes_through_the_queue_when_it_takes_it():
    sender = _sender()
    telegram = Telegram()

    with patch.object(async_tools.httpx, "AsyncClient", telegram):
        asyncio.run(sender.send("Position p1 sold"))

    sender._channel.default_exchange.publish.assert_awaited_once()
    assert telegram.posted == []


def test_what_rabbitmq_does_not_take_is_sent_to_telegram_directly(caplog):
    sender = _sender(publish_error=ConnectionError("rabbitmq is down"))
    telegram = Telegram()

    with patch.object(async_tools.httpx, "AsyncClient", telegram), caplog.at_level("WARNING"):
        asyncio.run(sender.send("No price for position p1 since 14:30:40"))

    assert telegram.posted == [(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                                {"chat_id": "42", "text": "No price for position p1 since 14:30:40"})]
    assert TOKEN not in caplog.text


def test_a_message_neither_could_take_is_an_error_without_the_token(caplog):
    sender = _sender(publish_error=ConnectionError("rabbitmq is down"))

    with patch.object(async_tools.httpx, "AsyncClient", Telegram(status=502)), caplog.at_level("WARNING"):
        with pytest.raises(ConnectionError):
            asyncio.run(sender.send("hello"))

    assert TOKEN not in caplog.text
