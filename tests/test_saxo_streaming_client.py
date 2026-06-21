import asyncio
from unittest.mock import AsyncMock, MagicMock

from src.saxo_streaming.client import SaxoStreamClient


def test_saxo_stream_client_position_updates_still_work():
    callback = AsyncMock()
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

    asyncio.run(
        client._handle_position_update(
            {"PositionId": "position-1", "PositionView": {"Bid": 9.8}}
        )
    )

    callback.assert_awaited_once()
    updated_positions = callback.await_args.args[0]
    assert updated_positions["position-1"]["PositionView"]["Bid"] == 9.8


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