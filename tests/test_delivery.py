"""
The scheduler's close of the day is tried three times before it is given up
(src/delivery.py), and the trader's hand-over of a sale it could not make is
the message the position monitor reads (src/trade/ops_queue.py).
"""

import asyncio
import sys
import types
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

sys.modules.setdefault("aio_pika", types.ModuleType("aio_pika"))

from src.delivery import deliver  # noqa: E402
from src.trade.ops_queue import AsyncOpsPublisher, close_request  # noqa: E402


def test_a_message_is_sent_again_after_each_wait_until_delivered():
    attempts, waits = [], []

    def send():
        attempts.append(len(attempts))
        if len(attempts) < 3:
            raise ConnectionError("web server restarting")

    assert deliver(send, sleep=waits.append) is None
    assert (len(attempts), waits) == (3, [2.0, 4.0])


def test_the_last_error_is_returned_when_it_never_goes_through():
    def send():
        raise TimeoutError("no answer")

    error = deliver(send, sleep=lambda wait: None)

    assert isinstance(error, TimeoutError) and str(error) == "no answer"


def test_a_message_delivered_at_once_waits_for_nothing():
    waits = []

    assert deliver(lambda: None, sleep=waits.append) is None
    assert waits == []


def test_the_hand_over_is_the_close_request_the_monitor_reads():
    moment = datetime(2026, 10, 14, 12, 30, 33, tzinfo=timezone.utc)

    assert close_request(7779123400, "Explicit Close (All)", now=moment) == {
        "action": "close_position",
        "position_id": "7779123400",
        "requested_at": "2026-10-14T12:30:33Z",
        "reason": "Explicit Close (All)",
    }


def test_a_hand_over_that_cannot_be_sent_never_raises():
    publisher = AsyncOpsPublisher(MagicMock())
    publisher.publish = AsyncMock(side_effect=ConnectionError("rabbitmq down"))

    assert asyncio.run(publisher.hand_over("p1", "Explicit Close (All)")) is False


def test_a_hand_over_waits_no_longer_than_its_timeout(monkeypatch):
    import src.trade.ops_queue as ops_queue

    monkeypatch.setattr(ops_queue, "HAND_OVER_TIMEOUT_SECONDS", 0.01)
    publisher = AsyncOpsPublisher(MagicMock())

    async def never_answers(message):
        await asyncio.sleep(10)

    publisher.publish = never_answers

    assert asyncio.run(publisher.hand_over("p1", "Explicit Close (All)")) is False
