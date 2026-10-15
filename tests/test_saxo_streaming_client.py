import asyncio
import json
import logging
import struct
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import websockets

from src.saxo_streaming.client import SaxoStreamClient


def _position_client(callback) -> SaxoStreamClient:
    client = SaxoStreamClient(
        api_client=MagicMock(),
        account_key="account-key",
        client_key="client-key",
        on_positions_update=callback,
    )
    client._positions["position-1"] = {
        "PositionId": "position-1",
        "PositionBase": {"Status": "Open"},
        "PositionView": {"Bid": 10.0},
    }
    return client


def _bid_update(bid: float) -> dict:
    return {"PositionId": "position-1", "PositionView": {"Bid": bid}}


def test_saxo_stream_client_position_updates_still_work():
    delivered = []

    async def scenario():
        received = asyncio.Event()

        async def callback(positions):
            delivered.append(positions)
            received.set()

        client = _position_client(callback)
        delivery = asyncio.create_task(client._deliver_position_updates())
        await client._handle_position_update(_bid_update(9.8))
        await asyncio.wait_for(received.wait(), timeout=1)
        delivery.cancel()

    asyncio.run(scenario())

    assert len(delivered) == 1
    assert delivered[0]["position-1"]["PositionView"]["Bid"] == 9.8


def test_updates_during_a_slow_callback_do_not_wait_for_it_and_are_coalesced():
    async def scenario():
        bids_seen = []
        first_call_started = asyncio.Event()
        let_first_call_finish = asyncio.Event()
        second_call_done = asyncio.Event()

        async def callback(positions):
            bids_seen.append(positions["position-1"]["PositionView"]["Bid"])
            if len(bids_seen) == 1:
                first_call_started.set()
                await let_first_call_finish.wait()
            else:
                second_call_done.set()

        client = _position_client(callback)
        delivery = asyncio.create_task(client._deliver_position_updates())

        await client._handle_position_update(_bid_update(9.8))
        await asyncio.wait_for(first_call_started.wait(), timeout=1)

        # The callback is still busy with 9.8: reading these must not wait for it
        for bid in (9.7, 9.6, 9.5):
            await asyncio.wait_for(client._handle_position_update(_bid_update(bid)), timeout=1)

        let_first_call_finish.set()
        await asyncio.wait_for(second_call_done.wait(), timeout=1)
        await asyncio.sleep(0.01)  # a third call would show up here
        delivery.cancel()
        return bids_seen

    assert asyncio.run(scenario()) == [9.8, 9.5]


def test_callback_error_does_not_stop_later_deliveries():
    async def scenario():
        bids_seen = []
        second_call_done = asyncio.Event()

        async def callback(positions):
            bids_seen.append(positions["position-1"]["PositionView"]["Bid"])
            if len(bids_seen) == 1:
                raise RuntimeError("boom")
            second_call_done.set()

        client = _position_client(callback)
        delivery = asyncio.create_task(client._deliver_position_updates())

        await client._handle_position_update(_bid_update(9.8))
        await asyncio.sleep(0.01)
        await client._handle_position_update(_bid_update(9.7))
        await asyncio.wait_for(second_call_done.wait(), timeout=1)
        delivery.cancel()
        return bids_seen

    assert asyncio.run(scenario()) == [9.8, 9.7]


def test_start_delivers_position_updates_and_stops_delivering_when_it_ends():
    delivered = []

    async def scenario():
        received = asyncio.Event()

        async def callback(positions):
            delivered.append(positions)
            received.set()

        client = _position_client(callback)

        async def connect_and_subscribe():
            client._fire_callback()  # what seeding the snapshot does

        async def receive_loop():
            await asyncio.wait_for(received.wait(), timeout=1)
            client._running = False

        client._connect_and_subscribe = connect_and_subscribe
        client._receive_loop = receive_loop

        await client.start()
        await asyncio.sleep(0)  # let the cancellation land
        return client._positions_delivery_task

    delivery_task = asyncio.run(scenario())

    assert delivered[0]["position-1"]["PositionView"]["Bid"] == 10.0
    assert delivery_task.cancelled()


def test_saxo_stream_client_info_price_updates_fire_callback():
    callback = AsyncMock()
    client = SaxoStreamClient(
        api_client=MagicMock(),
        account_key="account-key",
        client_key="",
        on_positions_update=None,
        on_info_price_subscription_update=callback,
    )
    definition = {
        "reference_id": "ref-1",
        "asset_type": "WarrantKnockOut",
        "uics": [101],
        "field_groups": ["Quote"],
    }

    asyncio.run(client.set_subscriptions([definition]))
    client._active_info_price_subscriptions["ref-1"] = {
        "definition": definition,
        "snapshot_by_uic": {
            101: {
                "Uic": 101,
                "Quote": {"Bid": 10.0, "Ask": 10.1},
            }
        },
    }

    asyncio.run(
        client._handle_info_price_subscription_update(
            "ref-1",
            {"Uic": 101, "Quote": {"Bid": 9.7}},
        )
    )

    callback.assert_awaited_once()
    definition_arg, snapshot_rows_arg, context_id_arg = callback.await_args.args
    assert definition_arg["reference_id"] == "ref-1"
    assert snapshot_rows_arg[0]["Quote"]["Bid"] == 9.7
    assert snapshot_rows_arg[0]["Quote"]["Ask"] == 10.1
    assert context_id_arg is None


# ── The connection itself, against a WebSocket server on this machine ──
#
# What stands for Saxo here only speaks its framing; it shows what the client
# does with a connection (open it, read it, lose it, come back, leave), which
# is what must stay the same when the WebSocket library or Python changes.

POSITION = {
    "PositionId": "position-1",
    "PositionBase": {"Status": "Open"},
    "PositionView": {"Bid": 10.0},
}


def _frame(message_id: int, reference_id: str, payload) -> bytes:
    """A message as Saxo sends it (what ``decode_ws_msg`` reads back)."""
    reference = reference_id.encode()
    body = json.dumps(payload).encode()
    return (
        struct.pack("Q", message_id) + b"\x00\x00"
        + struct.pack("B", len(reference)) + reference
        + struct.pack("B", 0) + struct.pack("i", len(body)) + body
    )


def _asked_for(connection) -> dict:
    """What a connection asked for in its address: {name: value}."""
    return {name: values[0] for name, values in parse_qs(urlsplit(connection.request.path).query).items()}


class LocalSaxoStream:
    """
    A WebSocket server on this machine, for the time of a test. ``script`` is
    run for each connection with its number (1 for the first); the connection
    stays open until the script returns.
    """

    def __init__(self, script):
        self._script = script
        self.asked = []       # what each connection asked for, in order
        self.left = []        # an event per connection, set once it is over

    async def __aenter__(self):
        self._server = await websockets.serve(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *_):
        self._server.close()
        await self._server.wait_closed()

    async def _handle(self, connection):
        self.asked.append(_asked_for(connection))
        over = asyncio.Event()
        self.left.append(over)
        try:
            await self._script(connection, len(self.asked))
        finally:
            over.set()


def _connected_client(stream: LocalSaxoStream, callback=None, **settings) -> SaxoStreamClient:
    api = MagicMock()
    api.access_token = "token-1"
    api.request = AsyncMock(return_value={"Snapshot": {"Data": [json.loads(json.dumps(POSITION))]}})
    client = SaxoStreamClient(
        api_client=api,
        account_key="account-key",
        client_key="client-key",
        on_positions_update=callback or AsyncMock(),
        access_token_getter=lambda: "token-1",
        reconnect_delay=0.01,
        max_reconnect_delay=0.02,
        **settings,
    )
    saxo_address = client._build_ws_url

    def local_address(token):
        # The real address with its real parameters, on this machine.
        return saxo_address(token).replace("wss://live-streaming.saxobank.com", f"ws://127.0.0.1:{stream.port}")

    client._build_ws_url = local_address

    # How many times the client has finished connecting and subscribing. The
    # server sees a connection a moment before the client is handed it: a test
    # that acts on the client waits for the client.
    client.connections_made = 0
    connect_and_subscribe = client._connect_and_subscribe

    async def counted():
        await connect_and_subscribe()
        client.connections_made += 1

    client._connect_and_subscribe = counted
    return client


def _requests(client: SaxoStreamClient) -> list[str]:
    """The REST requests the client made, by the name of their endpoint."""
    return [type(call.args[0]).__name__ for call in client._api.request.await_args_list]


async def _until(condition, timeout=3.0):
    """Wait for ``condition()`` to hold, checking between two turns of the loop."""
    async def poll():
        while not condition():
            await asyncio.sleep(0.005)
    await asyncio.wait_for(poll(), timeout)


async def _ends(running: asyncio.Task, timeout=3.0):
    """
    Wait for the stream to end by itself. Not ``asyncio.wait_for``: it would
    cancel a stream that hangs, which ``start()`` takes as a request to end,
    and the test would pass three seconds late instead of failing.
    """
    done, _ = await asyncio.wait({running}, timeout=timeout)
    assert done, "the stream did not end"
    running.result()


def _run(scenario):
    return asyncio.run(asyncio.wait_for(scenario(), timeout=15))


def test_a_connection_counts_until_it_is_closed_as_it_did_with_the_client_of_before():
    from types import SimpleNamespace

    from websockets.protocol import State

    client = SaxoStreamClient(MagicMock(), "account-key", "client-key", None)
    counts = {}
    for state in State:
        client._ws = SimpleNamespace(state=state)
        counts[state.name] = client._has_connection()
    client._ws = None

    # What ``not connection.closed`` meant: one that is still closing has not gone yet.
    assert counts == {"CONNECTING": True, "OPEN": True, "CLOSING": True, "CLOSED": False}
    assert client._has_connection() is False


def test_the_address_carries_the_token_the_context_and_the_last_message_read():
    client = SaxoStreamClient(MagicMock(), "account-key", "client-key", None, environment="live")
    client._context_id = "ctx-1"

    first = urlsplit(client._build_ws_url("token-1"))
    client._last_message_id = 41
    resumed = parse_qs(urlsplit(client._build_ws_url("token-1")).query)

    assert (first.scheme, first.netloc, first.path) == ("wss", "live-streaming.saxobank.com", "/oapi/streaming/ws/connect")
    assert parse_qs(first.query) == {"authorization": ["BEARER token-1"], "contextId": ["ctx-1"]}
    assert resumed["messageid"] == ["41"]


def test_the_simulation_address_has_its_own_host_and_prefix():
    client = SaxoStreamClient(MagicMock(), "account-key", "client-key", None, environment="simulation")
    client._context_id = "ctx-1"

    address = urlsplit(client._build_ws_url("token-1"))

    assert (address.netloc, address.path) == ("sim-streaming.saxobank.com", "/sim/oapi/streaming/ws/connect")


def test_stream_connects_subscribes_and_delivers_what_saxo_sends():
    async def scenario():
        delivered = []

        async def callback(positions):
            delivered.append(positions)

        async def saxo(connection, number):
            await connection.send(_frame(1, client._pos_ref_id, {"PositionId": "position-1", "PositionView": {"Bid": 9.8}}))
            await connection.wait_closed()

        async with LocalSaxoStream(saxo) as stream:
            client = _connected_client(stream, callback)
            running = asyncio.create_task(client.start())
            await _until(lambda: delivered and delivered[-1]["position-1"]["PositionView"]["Bid"] == 9.8)
            asked = dict(stream.asked[0])
            requests = _requests(client)
            await client.stop()
            await _ends(running)
        return client, asked, requests, delivered

    client, asked, requests, delivered = _run(scenario)

    assert asked == {"authorization": "BEARER token-1", "contextId": client._context_id}
    assert requests == ["PositionListSubscription"]
    # The delta is merged into the snapshot the subscription gave.
    assert delivered[-1]["position-1"]["PositionBase"]["Status"] == "Open"
    assert client._last_message_id == 1


def test_the_connection_is_opened_with_its_keepalive_and_size_limits():
    async def scenario():
        async def saxo(connection, number):
            await connection.wait_closed()

        async with LocalSaxoStream(saxo) as stream:
            client = _connected_client(stream)
            with patch("src.saxo_streaming.client.websockets.connect", wraps=websockets.connect) as connect:
                running = asyncio.create_task(client.start())
                await _until(lambda: client.connections_made == 1)
                await client.stop()
                await _ends(running)
            return connect.call_args.kwargs

    settings = _run(scenario)

    # A ping every 20 s, 20 s for its answer: a silent connection is noticed within 40 s.
    assert (settings["ping_interval"], settings["ping_timeout"]) == (20, 20)
    assert (settings["close_timeout"], settings["max_size"]) == (5, 2 ** 22)
    # Straight to Saxo: no proxy is looked for in the environment.
    assert settings["proxy"] is None


def test_stream_comes_back_after_saxo_drops_the_connection_and_resumes_from_the_last_message(caplog):
    async def scenario():
        delivered = []

        async def callback(positions):
            delivered.append(positions["position-1"]["PositionView"]["Bid"])

        async def saxo(connection, number):
            if number == 1:
                await connection.send(_frame(7, client._pos_ref_id, {"PositionId": "position-1", "PositionView": {"Bid": 9.8}}))
                await _until(lambda: client._last_message_id == 7)
                # As Saxo does when the token runs out: gone, without a closing frame.
                connection.transport.abort()
            else:
                await connection.send(_frame(8, client._pos_ref_id, {"PositionId": "position-1", "PositionView": {"Bid": 9.7}}))
                await connection.wait_closed()

        async with LocalSaxoStream(saxo) as stream:
            client = _connected_client(stream, callback)
            running = asyncio.create_task(client.start())
            await _until(lambda: delivered and delivered[-1] == 9.7)
            asked = list(stream.asked)
            requests = _requests(client)
            await client.stop()
            await _ends(running)
        return asked, requests

    with caplog.at_level(logging.INFO, logger="src.saxo_streaming.client"):
        asked, requests = _run(scenario)

    assert len(asked) == 2
    assert "messageid" not in asked[0]
    # Saxo is told where the client was, so that nothing sent meanwhile is lost.
    assert asked[1]["messageid"] == "7"
    assert asked[1]["contextId"] == asked[0]["contextId"]
    # The subscription made on the first connection lives on in the same context.
    assert requests == ["PositionListSubscription"]
    assert "WebSocket closed: code=1006 reason=" in caplog.text
    assert "Reconnecting in" in caplog.text


def test_stream_comes_back_after_saxo_closes_the_connection_with_a_reason(caplog):
    async def scenario():
        async def saxo(connection, number):
            if number == 1:
                await connection.close(code=4001, reason="session limit")
            else:
                await connection.wait_closed()

        async with LocalSaxoStream(saxo) as stream:
            client = _connected_client(stream)
            running = asyncio.create_task(client.start())
            await _until(lambda: client.connections_made == 2)
            await client.stop()
            await _ends(running)

    with caplog.at_level(logging.INFO, logger="src.saxo_streaming.client"):
        _run(scenario)

    assert "WebSocket closed: code=4001 reason=session limit" in caplog.text


def test_stream_comes_back_after_an_ordinary_close():
    async def scenario():
        async def saxo(connection, number):
            if number == 1:
                await connection.close(code=1000)
            else:
                await connection.wait_closed()

        async with LocalSaxoStream(saxo) as stream:
            client = _connected_client(stream)
            running = asyncio.create_task(client.start())
            await _until(lambda: client.connections_made == 2)
            await client.stop()
            await _ends(running)
            return len(stream.asked)

    assert _run(scenario) == 2


def test_stop_closes_the_connection_removes_the_subscriptions_and_does_not_come_back():
    async def scenario():
        async def saxo(connection, number):
            await connection.wait_closed()

        async with LocalSaxoStream(saxo) as stream:
            client = _connected_client(stream)
            running = asyncio.create_task(client.start())
            await _until(lambda: client.connections_made == 1)

            await client.stop()
            await _ends(running)
            await asyncio.wait_for(stream.left[0].wait(), timeout=3)
            await asyncio.sleep(0.1)  # a reconnection would show up here
            return len(stream.asked), _requests(client), client._subscription_ready

    connections, requests, subscribed = _run(scenario)

    assert connections == 1
    assert requests == ["PositionListSubscription", "PositionSubscriptionRemoveMultiple"]
    assert subscribed is False


def test_stopping_twice_or_before_any_connection_is_harmless():
    async def scenario():
        async def saxo(connection, number):
            await connection.wait_closed()

        async with LocalSaxoStream(saxo) as stream:
            never_started = _connected_client(stream)
            await never_started.stop()

            client = _connected_client(stream)
            running = asyncio.create_task(client.start())
            await _until(lambda: client.connections_made == 1)
            await client.stop()
            await client.stop()
            await _ends(running)

    _run(scenario)


def test_price_subscriptions_are_made_at_once_while_connected_and_kept_for_later_otherwise():
    definition = {"reference_id": "wlp-1", "asset_type": "WarrantKnockOut", "uics": [101, 102]}

    async def scenario():
        async def saxo(connection, number):
            await connection.wait_closed()

        async with LocalSaxoStream(saxo) as stream:
            client = _connected_client(stream)

            # Not connected yet: kept, nothing asked of Saxo.
            await client.set_subscriptions([definition])
            before = _requests(client)

            running = asyncio.create_task(client.start())
            await _until(lambda: client.connections_made == 1)
            at_connection = _requests(client)

            # Connected: a change is made at once.
            await client.set_subscriptions([{**definition, "reference_id": "wlp-2"}])
            while_connected = _requests(client)

            await client.stop()
            await _ends(running)

            # The connection is closed: kept again, nothing asked of Saxo.
            asked_before = len(_requests(client))
            await client.set_subscriptions([definition])
            return before, at_connection, while_connected, len(_requests(client)) - asked_before, client

    before, at_connection, while_connected, asked_once_closed, client = _run(scenario)

    assert before == []
    assert at_connection == ["PositionListSubscription", "CreateInfoPriceSubscription"]
    assert while_connected[2:] == ["RemoveInfoPriceSubscriptionById", "CreateInfoPriceSubscription"]
    assert asked_once_closed == 0
    assert client.desired_subscription_count == 1


def test_a_disconnect_message_from_saxo_ends_the_stream_without_a_reconnection(caplog):
    async def scenario():
        async def saxo(connection, number):
            await connection.send(_frame(1, "_disconnect", {}))
            await _until(lambda: not client._running)
            await connection.close(code=1000)

        async with LocalSaxoStream(saxo) as stream:
            client = _connected_client(stream)
            await _ends(asyncio.create_task(client.start()))
            await asyncio.sleep(0.1)  # a reconnection would show up here
            connections = len(stream.asked)
            await client.stop()
            return connections

    with caplog.at_level(logging.INFO, logger="src.saxo_streaming.client"):
        connections = _run(scenario)

    assert connections == 1
    assert "Received _disconnect from Saxo" in caplog.text


def test_a_frame_that_cannot_be_read_does_not_end_the_stream():
    async def scenario():
        delivered = []

        async def callback(positions):
            delivered.append(positions["position-1"]["PositionView"]["Bid"])

        async def saxo(connection, number):
            await connection.send(b"\x01\x02 not a Saxo frame")
            await connection.send("a text frame, which Saxo never sends")
            await connection.send(_frame(3, client._pos_ref_id, {"PositionId": "position-1", "PositionView": {"Bid": 9.6}}))
            await connection.wait_closed()

        async with LocalSaxoStream(saxo) as stream:
            client = _connected_client(stream, callback)
            running = asyncio.create_task(client.start())
            await _until(lambda: delivered and delivered[-1] == 9.6)
            connections = len(stream.asked)
            await client.stop()
            await _ends(running)
            return connections

    assert _run(scenario) == 1
