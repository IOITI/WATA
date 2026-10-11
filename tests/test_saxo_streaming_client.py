import asyncio
from unittest.mock import AsyncMock, MagicMock

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
