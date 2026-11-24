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


# ── A position Saxo no longer lists (2026-11-23) ──
#
# Saxo removes an item from a list subscription with its key and "__meta_deleted": true (its own client
# library, openapi-clientlib-js: OPENAPI_DELETE_PROPERTY). The client merged it as a plain field: position
# 7829220002, sold at 08:42:53 UTC, stayed in the snapshot with its last values, and the position monitor
# tried to sell it again 100 times (NotOwned) and "adopted" it every 90 s.


def _deleted(position_id: str = "position-1") -> dict:
    """What Saxo sends when a position leaves its list."""
    return {"PositionId": position_id, "__meta_deleted": True}


def test_a_position_saxo_deletes_leaves_the_snapshot():
    async def scenario():
        received = asyncio.Event()
        delivered = []

        async def callback(positions):
            delivered.append(positions)
            received.set()

        client = _position_client(callback)
        delivery = asyncio.create_task(client._deliver_position_updates())
        await client._handle_position_update([_deleted()])
        await asyncio.wait_for(received.wait(), timeout=1)
        delivery.cancel()
        return client, delivered

    client, delivered = asyncio.run(scenario())

    assert client.positions == {}
    assert delivered == [{}]


def test_a_deletion_is_read_in_saxo_s_data_wrapper_and_alone_too():
    async def scenario(payload):
        client = _position_client(AsyncMock())
        await client._handle_position_update(payload)
        return client.positions, client._positions_changed.is_set()

    for payload in ({"Data": [_deleted()]}, _deleted()):
        assert asyncio.run(scenario(payload)) == ({}, True)


def test_a_deletion_of_a_position_the_snapshot_does_not_hold_changes_nothing():
    async def scenario():
        client = _position_client(AsyncMock())
        await client._handle_position_update([_deleted("position-9")])
        return client.positions, client._positions_changed.is_set()

    positions, changed = asyncio.run(scenario())

    assert set(positions) == {"position-1"}
    assert changed is False


def test_positions_saxo_sold_are_forgotten_on_the_monitor_s_word_and_delivered_without_them():
    async def scenario():
        client = _position_client(AsyncMock())
        client._positions["position-2"] = {"PositionId": "position-2", "PositionBase": {"Status": "Open"}}
        gone = client.forget_positions({"position-1", "position-9"})
        return gone, client.positions, client._positions_changed.is_set()

    gone, positions, changed = asyncio.run(scenario())

    assert gone == ["position-1"]
    assert set(positions) == {"position-2"}
    assert changed is True


def test_forgetting_positions_the_stream_does_not_list_delivers_nothing():
    async def scenario():
        client = _position_client(AsyncMock())
        return client.forget_positions({"position-9"}), client._positions_changed.is_set()

    assert asyncio.run(scenario()) == ([], False)


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


def test_stream_delivers_the_removal_of_a_position_saxo_sold():
    async def scenario():
        delivered = []

        async def callback(positions):
            delivered.append(positions)

        async def saxo(connection, number):
            await connection.send(_frame(1, client._pos_ref_id, {"PositionId": "position-1", "PositionView": {"Bid": 9.8}}))
            await connection.send(_frame(2, client._pos_ref_id, [_deleted()]))
            await connection.wait_closed()

        async with LocalSaxoStream(saxo) as stream:
            client = _connected_client(stream, callback)
            running = asyncio.create_task(client.start())
            await _until(lambda: client._last_message_id == 2 and delivered and delivered[-1] == {})
            await client.stop()
            await _ends(running)
        return client, delivered

    client, delivered = _run(scenario)

    assert client.positions == {}
    assert delivered[-1] == {}


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


def test_a_slow_token_renewal_does_not_hold_the_position_monitors_loop():
    # The token getter is SaxoAuth.get_token: a request to Saxo when the token is renewed, a wait of minutes for
    # an authorisation code when the login lapsed. The monitor's stops run on the same loop.
    import time as clock

    def slow_token():
        clock.sleep(0.3)
        return "token-2"

    client = SaxoStreamClient(
        api_client=MagicMock(), account_key="account-key", client_key="client-key", on_positions_update=None,
        access_token_getter=slow_token,
    )

    async def scenario():
        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        running = asyncio.create_task(ticker())
        token = await client._current_token()
        running.cancel()
        return token, ticks

    token, ticks = asyncio.run(scenario())

    assert token == "token-2"
    assert ticks >= 10


def test_the_last_message_from_saxo_is_known():
    async def scenario():
        async def saxo(connection, number):
            await connection.send(_frame(1, "_heartbeat", {"Heartbeats": []}))
            await connection.wait_closed()

        async with LocalSaxoStream(saxo) as stream:
            client = _connected_client(stream)
            running = asyncio.create_task(client.start())
            await _until(lambda: client.last_message_at is not None)
            connected = client.connected
            await client.stop()
            await _ends(running)
        return connected, client.connected

    assert _run(scenario) == (True, False)


# ── Price subscriptions Saxo asks to have made again ──
#
# Saxo sends _resetsubscriptions, often twice in a row. Each subscription made
# again is two requests, and one Saxo refuses is waited for up to a minute:
# awaited in the receive loop (until v0.100.0), no quote was read meanwhile.

REFS = ["wlp-1", "wlp-2", "wlp-3", "wlp-4"]


class SaxoRest:
    """
    What answers the client's REST requests, each noted as (endpoint, reference).
    A request named in ``held`` waits until the test lets it go (Saxo's refusal,
    waited for), and one named in ``refused`` raises.
    """

    def __init__(self):
        self.asked = []
        self.held: dict[tuple, asyncio.Event] = {}
        self.arrived: dict[tuple, asyncio.Event] = {}
        self.refused: set[tuple] = set()
        self.bids: dict[str, float] = {}

    def hold(self, endpoint: str, reference: str) -> asyncio.Event:
        self.arrived[(endpoint, reference)] = asyncio.Event()
        self.held[(endpoint, reference)] = asyncio.Event()
        return self.held[(endpoint, reference)]

    async def request(self, endpoint):
        name = type(endpoint).__name__
        if name == "CreateInfoPriceSubscription":
            reference = endpoint.data["ReferenceId"]
        else:
            reference = str(endpoint).rsplit("/", 1)[-1]
        key = (name.replace("InfoPriceSubscription", "").replace("ById", ""), reference)
        self.asked.append(key)
        if key in self.held:
            self.arrived[key].set()
            await self.held.pop(key).wait()
        if key in self.refused:
            raise RuntimeError("Saxo refused")
        if key[0] == "Create":
            return {"Snapshot": {"Data": [{"Uic": 101, "Quote": {"Bid": self.bids.get(reference, 10.0)}}]}}
        return None


def _price_client(rest: SaxoRest, updates: list | None = None) -> SaxoStreamClient:
    """A client holding four price subscriptions, as the watchlist manager's does while it streams."""
    async def on_update(definition, rows, context_id):
        if updates is not None:
            updates.append((definition["reference_id"], rows[0]["Quote"]["Bid"]))

    api = MagicMock()
    api.access_token = "token-1"
    api.request = rest.request
    client = SaxoStreamClient(
        api_client=api, account_key="account-key", client_key="", on_positions_update=None,
        access_token_getter=lambda: "token-1", on_info_price_subscription_update=on_update,
    )
    client._context_id = "ctx-1"
    client._running = True
    for reference in REFS:
        definition = {"reference_id": reference, "asset_type": "WarrantKnockOut", "uics": [101], "field_groups": ["Quote"]}
        client._desired_info_price_subscriptions[reference] = definition
        client._active_info_price_subscriptions[reference] = {
            "definition": dict(definition), "snapshot_by_uic": {101: {"Uic": 101, "Quote": {"Bid": 10.0}}},
        }
    return client


async def _reset_asked(client: SaxoStreamClient, *references: str) -> None:
    await asyncio.wait_for(
        client._handle_control_message("_resetsubscriptions", {"TargetReferenceIds": list(references)}), timeout=0.5,
    )


async def _resets_done(client: SaxoStreamClient) -> None:
    await asyncio.wait_for(client._reset_task, timeout=3)


def test_a_reset_of_the_price_subscriptions_does_not_hold_the_reading_of_the_stream():
    async def scenario():
        rest, updates = SaxoRest(), []
        client = _price_client(rest, updates)
        refusal = rest.hold("Remove", "wlp-1")

        # Answered at once, though Saxo keeps the first request waiting (a 429 is waited for up to a minute).
        await _reset_asked(client)
        await rest.arrived[("Remove", "wlp-1")].wait()
        # Meanwhile the other subscriptions' quotes are read and delivered.
        await client._handle_info_price_subscription_update("wlp-3", {"Uic": 101, "Quote": {"Bid": 9.7}})
        read_meanwhile = list(updates)

        refusal.set()
        await _resets_done(client)
        return read_meanwhile, rest.asked, client

    read_meanwhile, asked, client = _run(scenario)

    assert read_meanwhile == [("wlp-3", 9.7)]
    # Each made again, one after the other: its old subscription removed, then the new one created.
    assert asked == [(step, reference) for reference in REFS for step in ("Remove", "Create")]
    assert sorted(client._active_info_price_subscriptions) == REFS and client._resetting is None


def test_a_reset_saxo_asks_twice_in_a_row_is_made_once():
    async def scenario():
        rest = SaxoRest()
        client = _price_client(rest)
        waiting = rest.hold("Remove", "wlp-1")

        await _reset_asked(client)
        await rest.arrived[("Remove", "wlp-1")].wait()
        # The second request, read while the first subscription's removal is not answered yet: it can only be about
        # the subscriptions being replaced.
        await _reset_asked(client)
        waiting.set()
        await _resets_done(client)
        once = list(rest.asked)

        # Asked again once they are made: made again.
        await _reset_asked(client, "wlp-2")
        await _resets_done(client)
        return once, rest.asked[len(once):]

    once, later = _run(scenario)

    # Eight requests, where the client of before made sixteen in under a second and was refused one.
    assert once == [(step, reference) for reference in REFS for step in ("Remove", "Create")]
    assert later == [("Remove", "wlp-2"), ("Create", "wlp-2")]


def test_a_reset_asked_while_the_new_subscription_is_being_created_makes_it_again():
    async def scenario():
        rest = SaxoRest()
        client = _price_client(rest)
        creating = rest.hold("Create", "wlp-1")

        await _reset_asked(client, "wlp-1")
        await rest.arrived[("Create", "wlp-1")].wait()
        # Its old subscription is gone: this request may be about the new one.
        await _reset_asked(client, "wlp-1")
        creating.set()
        await _resets_done(client)
        return rest.asked

    assert _run(scenario) == [("Remove", "wlp-1"), ("Create", "wlp-1")] * 2


def test_what_saxo_sends_of_a_subscription_being_made_again_is_read_after_its_snapshot():
    async def scenario():
        rest, updates = SaxoRest(), []
        client = _price_client(rest, updates)
        rest.bids["wlp-1"] = 11.0
        removing, creating = rest.hold("Remove", "wlp-1"), rest.hold("Create", "wlp-1")

        async def received(reference, bid):
            # As the receive loop dispatches a message.
            payload = {"Uic": 101, "Quote": {"Bid": bid}}
            if reference == client._resetting:
                if client._reset_removed:
                    client._reset_held.append(payload)
            elif reference in client._active_info_price_subscriptions:
                await client._handle_info_price_subscription_update(reference, payload)

        await _reset_asked(client, "wlp-1")
        await rest.arrived[("Remove", "wlp-1")].wait()
        # The old subscription's last words: the snapshot to come is newer.
        await received("wlp-1", 9.0)
        removing.set()
        await rest.arrived[("Create", "wlp-1")].wait()
        # The new subscription's first changes, sent before its snapshot was read.
        await received("wlp-1", 11.2)
        await received("wlp-1", 11.4)
        creating.set()
        await _resets_done(client)
        await received("wlp-1", 11.6)
        return updates

    # The snapshot, then what followed it, in the order Saxo sent it; never the old 9.0.
    assert _run(scenario) == [("wlp-1", 11.0), ("wlp-1", 11.2), ("wlp-1", 11.4), ("wlp-1", 11.6)]


def test_the_receive_loop_keeps_what_comes_for_a_subscription_being_made_again(caplog):
    async def scenario():
        rest, updates = SaxoRest(), []
        client = _price_client(rest, updates)
        rest.bids["wlp-2"] = 11.0
        creating = rest.hold("Create", "wlp-2")

        class Socket:
            """Saxo's frames, read as the receive loop reads a WebSocket: a reset, then quotes while it is under way."""

            def __aiter__(self):
                return self.frames()

            async def frames(self):
                yield _frame(1, "_resetsubscriptions", {"TargetReferenceIds": ["wlp-2"]})
                await rest.arrived[("Create", "wlp-2")].wait()
                yield _frame(2, "wlp-2", {"Uic": 101, "Quote": {"Bid": 11.3}})
                yield _frame(3, "wlp-4", {"Uic": 101, "Quote": {"Bid": 9.5}})
                creating.set()

        client._ws = Socket()
        await asyncio.wait_for(client._receive_loop(), timeout=3)
        await _resets_done(client)
        return updates

    # The other subscription's quote is delivered while Saxo is asked; the new one's change after its snapshot.
    assert _run(scenario) == [("wlp-4", 9.5), ("wlp-2", 11.0), ("wlp-2", 11.3)]


def test_a_subscription_being_made_again_is_not_created_a_second_time_by_a_change_of_subscriptions():
    async def scenario():
        rest = SaxoRest()
        client = _price_client(rest)
        client._ws = MagicMock()
        client._ws.state = None  # open, as far as the client can tell
        creating = rest.hold("Create", "wlp-1")

        await _reset_asked(client, "wlp-1")
        await rest.arrived[("Create", "wlp-1")].wait()
        # The watchlist manager sets its subscriptions again meanwhile, the same ones.
        await client.set_subscriptions([dict(client._desired_info_price_subscriptions[reference]) for reference in REFS])
        creating.set()
        await _resets_done(client)
        return rest.asked, client

    asked, client = _run(scenario)

    assert asked == [("Remove", "wlp-1"), ("Create", "wlp-1")]
    assert sorted(client._active_info_price_subscriptions) == REFS


def test_a_subscription_that_cannot_be_made_again_is_said_and_the_others_still_are(caplog):
    async def scenario():
        rest = SaxoRest()
        client = _price_client(rest)
        rest.refused.add(("Create", "wlp-2"))

        with caplog.at_level(logging.ERROR, logger="src.saxo_streaming.client"):
            await _reset_asked(client)
            await _resets_done(client)
        return rest.asked, client

    asked, client = _run(scenario)

    assert asked == [(step, reference) for reference in REFS for step in ("Remove", "Create")]
    # Left to the next synchronisation, which creates what is not active.
    assert sorted(client._active_info_price_subscriptions) == ["wlp-1", "wlp-3", "wlp-4"]
    assert "Info-price subscription wlp-2 could not be made again: Saxo refused" in caplog.text


def test_stopping_ends_a_reset_under_way():
    async def scenario():
        rest = SaxoRest()
        client = _price_client(rest)
        rest.hold("Remove", "wlp-1")

        await _reset_asked(client)
        await rest.arrived[("Remove", "wlp-1")].wait()
        task = client._reset_task
        await client.stop()
        await asyncio.wait({task}, timeout=1)
        return task.cancelled(), client._resets_asked

    assert _run(scenario) == (True, {})


def test_the_position_subscription_is_still_made_again_before_anything_else_is_read():
    async def scenario():
        api = MagicMock()
        api.access_token = "token-1"
        api.request = AsyncMock(return_value={"Snapshot": {"Data": [json.loads(json.dumps(POSITION))]}})
        client = SaxoStreamClient(
            api_client=api, account_key="account-key", client_key="client-key", on_positions_update=AsyncMock(),
            access_token_getter=lambda: "token-1",
        )
        client._context_id, client._pos_ref_id, client._running = "ctx-1", "pos-1", True

        await client._handle_control_message("_resetsubscriptions", {})
        return _requests(client), client

    asked, client = _run(scenario)

    # Done when the message's handling returns, as before: its snapshot replaces the positions held.
    assert asked == ["PositionSubscriptionRemove", "PositionListSubscription"]
    assert list(client._positions) == ["position-1"] and client._reset_task is None
