import json
import logging

import pytest
import requests

from src import mq_telegram
from src.mq_telegram import (
    RETRY_AFTER_SECONDS,
    SEND_TIMEOUT_SECONDS,
    MessageRefused,
    TelegramUnavailable,
    callback,
    send_telegram_message,
)

TOKEN = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"


class Answer:
    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self._body = body if body is not None else {"ok": status_code < 400}

    def json(self):
        return self._body


class Post:
    """Telegram's API as the test scripts it; what was asked of it kept."""

    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def test_a_message_is_sent_with_a_time_limit_and_said_without_the_address(caplog):
    post = Post(Answer(200))

    with caplog.at_level(logging.INFO):
        send_telegram_message(TOKEN, 4242, "Position sold", post=post)

    url, kwargs = post.calls[0]
    assert url == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    assert kwargs["timeout"] == SEND_TIMEOUT_SECONDS
    assert json.loads(kwargs["data"]) == {"chat_id": 4242, "text": "Position sold"}
    assert "Position sold" in caplog.text
    assert TOKEN not in caplog.text and "4242" not in caplog.text


def test_a_message_telegram_refuses_for_what_it_is_is_refused_for_good(caplog):
    post = Post(Answer(400, {"ok": False, "description": "Bad Request: message is too long"}))

    with pytest.raises(MessageRefused):
        send_telegram_message(TOKEN, 1, "x" * 5000, post=post)
    assert "message is too long" in caplog.text


@pytest.mark.parametrize("status,description", [
    (401, "Unauthorized"),
    (403, "Forbidden: bot was blocked by the user"),
    (404, "Not Found"),
    (400, "Bad Request: chat not found"),
])
def test_a_wrong_bot_or_chat_keeps_the_messages_for_when_it_is_set_right(status, description):
    post = Post(Answer(status, {"ok": False, "description": description}))

    with pytest.raises(TelegramUnavailable):
        send_telegram_message(TOKEN, 1, "Position sold", post=post)


@pytest.mark.parametrize("status", [429, 500, 502])
def test_telegram_unable_to_take_a_message_for_now_is_said_so(status):
    with pytest.raises(TelegramUnavailable):
        send_telegram_message(TOKEN, 1, "hello", post=Post(Answer(status)))


def test_no_answer_from_telegram_is_said_without_the_address_that_holds_the_token(caplog):
    error = requests.ConnectionError(f"Max retries exceeded with url: /bot{TOKEN}/sendMessage")

    with pytest.raises(TelegramUnavailable):
        send_telegram_message(TOKEN, 1, "hello", post=Post(error))

    assert "ConnectionError" in caplog.text
    assert TOKEN not in caplog.text


class Channel:
    def __init__(self):
        self.acked = []
        self.nacked = []

    def basic_ack(self, delivery_tag):
        self.acked.append(delivery_tag)

    def basic_nack(self, delivery_tag, requeue):
        self.nacked.append((delivery_tag, requeue))


class Method:
    delivery_tag = 7


def _deliver(body, send):
    channel, waits = Channel(), []
    callback(channel, Method(), None, body, send=send, wait=waits.append)
    return channel, waits


def test_a_message_sent_is_acknowledged():
    sent = []

    channel, waits = _deliver(json.dumps({"message": "Bought"}), sent.append)

    assert sent == ["Bought"] and channel.acked == [7] and channel.nacked == [] and waits == []


def test_a_message_telegram_refuses_is_dropped_rather_than_given_back_for_ever():
    def refuse(message):
        raise MessageRefused("400 Bad Request")

    channel, waits = _deliver(json.dumps({"message": "x"}), refuse)

    assert channel.acked == [7] and channel.nacked == [] and waits == []


def test_a_message_telegram_could_not_take_goes_back_to_the_queue_after_a_pause():
    def unavailable(message):
        raise TelegramUnavailable("502")

    channel, waits = _deliver(json.dumps({"message": "x"}), unavailable)

    assert channel.acked == [] and channel.nacked == [(7, True)] and waits == [RETRY_AFTER_SECONDS]


@pytest.mark.parametrize("body", [b"not json", json.dumps({"text": "no message key"}), json.dumps(["a list"])])
def test_what_is_no_message_is_dropped(body):
    channel, _ = _deliver(body, lambda message: pytest.fail("nothing is sent"))

    assert channel.acked == [7]


def test_the_service_sends_with_its_own_token_and_chat_when_nothing_else_is_given(monkeypatch):
    sent = []
    monkeypatch.setattr(mq_telegram, "bot_token", TOKEN, raising=False)
    monkeypatch.setattr(mq_telegram, "user_chat_id", 99, raising=False)
    monkeypatch.setattr(mq_telegram, "send_telegram_message", lambda **kwargs: sent.append(kwargs))

    callback(Channel(), Method(), None, json.dumps({"message": "Hello"}))

    assert sent == [{"token": TOKEN, "chat_id": 99, "message": "Hello"}]
