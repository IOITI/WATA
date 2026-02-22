# src/saxo_streaming/client.py
"""
Persistent async WebSocket client for Saxo Bank's streaming API.

This module maintains a WebSocket connection to Saxo's streaming endpoint
and manages position + price subscriptions.  When a position or price update
arrives, a user-supplied callback is invoked with the merged (snapshot +
delta) data.

Architecture
~~~~~~~~~~~~
1. ``SaxoStreamClient.start()`` opens the WS connection and creates the
   initial subscriptions via REST (the snapshot is returned in the
   subscription response).
2. Binary frames arriving over the WS are parsed with
   ``saxo_openapi.contrib.ws.stream.decode_ws_msg``.
3. Delta updates are merged into the cached snapshot (deep merge).
4. Control messages (_heartbeat, _resetsubscriptions, _disconnect) are
   handled according to the Saxo protocol.
5. Re-authorisation happens automatically before the token expires.
6. Reconnection preserves the ``last_message_id`` so the server can
   resume from where it left off.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import time
import uuid
from typing import Any, Callable, Coroutine

import websockets
import websockets.exceptions

from src.saxo_openapi.async_client import AsyncAPI, TRADING_ENVIRONMENTS
from src.saxo_openapi.contrib.ws.stream import decode_ws_msg
import src.saxo_openapi.endpoints.portfolio as pf
import src.saxo_openapi.endpoints.trading as tr

logger = logging.getLogger(__name__)

# Type alias for the user-supplied position-update callback.
# Signature: async def on_update(positions: dict[str, dict]) -> None
PositionUpdateCallback = Callable[[dict[str, dict]], Coroutine[Any, Any, None]]


def _deep_merge(base: dict, delta: dict) -> dict:
    """
    Recursively merge *delta* into *base* **in place** and return *base*.

    - Lists in *delta* replace lists in *base* entirely (Saxo convention).
    - ``__count`` fields are ignored in the delta (recalculated from Data).
    - A value of ``None`` in *delta* deletes the key from *base*.
    """
    for key, value in delta.items():
        if key == "__count":
            continue
        if value is None:
            base.pop(key, None)
        elif isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


class SaxoStreamClient:
    """
    High-level async streaming client for Saxo Bank's plain WebSocket API.

    Parameters
    ----------
    api_client : AsyncAPI
        An already-initialised async HTTP client (used for REST subscription
        calls and token refresh).
    account_key : str
        Saxo account key.
    client_key : str
        Saxo client key.
    on_positions_update : PositionUpdateCallback
        Async callback invoked with {position_id: merged_position_dict}
        every time a position or price delta arrives.
    environment : str
        ``"live"`` or ``"simulation"``.
    access_token_getter : Callable[[], str]
        Synchronous callable that returns the current bearer token (e.g.
        ``saxo_auth.get_token``).
    refresh_rate_ms : int
        Desired refresh rate for the subscriptions (milliseconds).
    reconnect_delay : float
        Base delay in seconds before attempting to reconnect after a WS drop.
    max_reconnect_delay : float
        Cap for exponential-backoff reconnection delay.
    """

    # ── Construction ──────────────────────────────────────────────

    def __init__(
        self,
        api_client: AsyncAPI,
        account_key: str,
        client_key: str,
        on_positions_update: PositionUpdateCallback,
        environment: str = "live",
        access_token_getter: Callable[[], str] | None = None,
        refresh_rate_ms: int = 1000,
        reconnect_delay: float = 1.0,
        max_reconnect_delay: float = 30.0,
    ):
        self._api = api_client
        self._account_key = account_key
        self._client_key = client_key
        self._on_positions_update = on_positions_update
        self._environment = environment
        self._get_token = access_token_getter
        self._refresh_rate_ms = refresh_rate_ms
        self._reconnect_delay = reconnect_delay
        self._max_reconnect_delay = max_reconnect_delay

        # Connection state
        self._context_id: str = ""
        self._pos_ref_id: str = ""
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._last_message_id: int | None = None
        self._running = False
        self._receive_task: asyncio.Task | None = None
        self._reauth_task: asyncio.Task | None = None

        # Snapshot cache:  {position_id: full_position_dict}
        self._positions: dict[str, dict] = {}

    # ── Public API ────────────────────────────────────────────────

    async def start(self):
        """Start the streaming loop (run forever until ``stop()`` is called)."""
        self._running = True
        backoff = self._reconnect_delay

        while self._running:
            try:
                await self._connect_and_subscribe()
                backoff = self._reconnect_delay  # reset on success
                await self._receive_loop()
            except asyncio.CancelledError:
                break
            except websockets.exceptions.ConnectionClosed as e:
                logger.warning("WebSocket closed: code=%s reason=%s", e.code, e.reason)
            except Exception as e:
                logger.error("Stream error: %s", e, exc_info=True)

            if not self._running:
                break

            logger.info("Reconnecting in %.1fs …", backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, self._max_reconnect_delay)

    async def stop(self):
        """Gracefully tear down the connection & subscriptions."""
        self._running = False
        if self._reauth_task and not self._reauth_task.done():
            self._reauth_task.cancel()
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
        await self._cleanup_subscriptions()
        if self._ws and not self._ws.closed:
            await self._ws.close()
            logger.info("WebSocket closed gracefully.")

    @property
    def positions(self) -> dict[str, dict]:
        """Return a **deep copy** of the current position snapshot cache."""
        return copy.deepcopy(self._positions)

    # ── Connection ────────────────────────────────────────────────

    async def _connect_and_subscribe(self):
        """Open WS, create position subscription, start reauth timer."""
        # 1. Generate fresh IDs
        self._context_id = _short_id("ctx")
        self._pos_ref_id = _short_id("pos")

        # 2. Build WS URL
        token = self._current_token()
        ws_url = self._build_ws_url(token)

        # 3. Open WebSocket
        logger.info("Connecting to Saxo WebSocket (contextId=%s) …", self._context_id)
        self._ws = await websockets.connect(
            ws_url,
            additional_headers={"Authorization": f"Bearer {token}"},
            ping_interval=20,
            ping_timeout=20,
            close_timeout=5,
            max_size=2 ** 22,  # 4 MiB
        )
        logger.info("WebSocket connected.")

        # 4. Position subscription (REST call, snapshot returned)
        await self._create_position_subscription()

        # 5. Periodic re-authorisation in background
        if self._reauth_task and not self._reauth_task.done():
            self._reauth_task.cancel()
        self._reauth_task = asyncio.create_task(self._reauth_loop())

    async def _create_position_subscription(self):
        """Create a position list subscription via REST and seed the cache."""
        data = {
            "Arguments": {
                "AccountKey": self._account_key,
                "ClientKey": self._client_key,
                "FieldGroups": [
                    "PositionBase",
                    "PositionView",
                    "DisplayAndFormat",
                    "ExchangeInfo",
                ],
            },
            "ContextId": self._context_id,
            "ReferenceId": self._pos_ref_id,
            "RefreshRate": self._refresh_rate_ms,
            "Format": "application/json",
        }

        req = pf.positions.PositionListSubscription(data=data)
        resp = await self._api.request(req)

        # Seed snapshot cache
        self._positions.clear()
        snapshot_data = resp.get("Snapshot", {}).get("Data", []) if resp else []
        for pos in snapshot_data:
            pid = pos.get("PositionId")
            if pid:
                self._positions[pid] = pos

        logger.info(
            "Position subscription created (refId=%s). Snapshot: %d positions.",
            self._pos_ref_id,
            len(self._positions),
        )

        # Fire initial callback with snapshot
        if self._positions:
            await self._fire_callback()

    async def _cleanup_subscriptions(self):
        """Delete active subscriptions (best-effort)."""
        if not self._context_id:
            return
        try:
            req = pf.positions.PositionSubscriptionRemoveMultiple(
                ContextId=self._context_id
            )
            await self._api.request(req)
            logger.info("Subscriptions for context %s removed.", self._context_id)
        except Exception as e:
            logger.warning("Failed to clean up subscriptions: %s", e)

    # ── Receive loop ──────────────────────────────────────────────

    async def _receive_loop(self):
        """Read binary frames from the WS, parse, dispatch."""
        assert self._ws is not None

        async for raw in self._ws:
            if not self._running:
                break

            if isinstance(raw, str):
                # Saxo should always send binary, but handle text just in case
                logger.debug("Text frame: %s", raw[:200])
                continue

            try:
                for message in decode_ws_msg(raw):
                    self._last_message_id = message.get("msgId")
                    ref_id = message.get("refid", "")
                    payload = message.get("msg")

                    if ref_id.startswith("_"):
                        await self._handle_control_message(ref_id, payload)
                    elif ref_id == self._pos_ref_id:
                        await self._handle_position_update(payload)
                    else:
                        logger.debug("Ignoring message with unknown refId=%s", ref_id)

            except Exception as e:
                logger.error("Error parsing WS frame: %s", e, exc_info=True)

    # ── Position updates ──────────────────────────────────────────

    async def _handle_position_update(self, payload: dict | list | Any):
        """
        Apply a position delta update to the snapshot cache.

        Saxo sends either:
        - A single position update dict (with ``PositionId``).
        - A list of position update dicts.
        - A wrapper with ``Data`` key containing a list.
        """
        updates: list[dict] = []

        if isinstance(payload, dict):
            if "Data" in payload:
                updates = payload["Data"]
            elif "PositionId" in payload:
                updates = [payload]
            else:
                # Could be a position-removed notification
                updates = [payload]
        elif isinstance(payload, list):
            updates = payload
        else:
            logger.warning("Unexpected position payload type: %s", type(payload))
            return

        changed = False
        for delta in updates:
            pid = delta.get("PositionId")
            if not pid:
                continue

            status = (
                delta.get("PositionBase", {}).get("Status")
                or (self._positions.get(pid, {}).get("PositionBase", {}).get("Status"))
            )

            if status in ("Closed", "Closing"):
                # Remove from cache when position is closed
                if pid in self._positions:
                    logger.info("Position %s removed from stream cache (status=%s).", pid, status)
                    del self._positions[pid]
                    changed = True
                continue

            if pid in self._positions:
                _deep_merge(self._positions[pid], delta)
            else:
                # New position appeared
                self._positions[pid] = delta

            changed = True

        if changed:
            await self._fire_callback()

    async def _fire_callback(self):
        """Invoke the user callback with the full position snapshot."""
        try:
            await self._on_positions_update(copy.deepcopy(self._positions))
        except Exception as e:
            logger.error("Error in positions-update callback: %s", e, exc_info=True)

    # ── Control messages ──────────────────────────────────────────

    async def _handle_control_message(self, ref_id: str, payload: Any):
        """
        Handle Saxo streaming control messages.

        _heartbeat            → log, no action
        _resetsubscriptions   → re-create affected subscriptions
        _disconnect           → stop (user must re-authenticate)
        """
        if ref_id == "_heartbeat":
            self._handle_heartbeat(payload)
        elif ref_id == "_resetsubscriptions":
            await self._handle_reset(payload)
        elif ref_id == "_disconnect":
            logger.critical("Received _disconnect from Saxo — service must re-login.")
            self._running = False
        else:
            logger.debug("Unknown control message: ref=%s", ref_id)

    def _handle_heartbeat(self, payload: Any):
        """Process heartbeat — check for SubscriptionTemporarilyDisabled."""
        if not isinstance(payload, dict):
            return
        heartbeats = payload.get("Heartbeats", [])
        for hb in heartbeats:
            reason = hb.get("Reason", "")
            origin = hb.get("OriginatingReferenceId", "")
            if reason == "SubscriptionTemporarilyDisabled":
                logger.warning(
                    "Heartbeat: subscription %s temporarily disabled.", origin
                )
            else:
                logger.debug("Heartbeat: ref=%s reason=%s", origin, reason)

    async def _handle_reset(self, payload: Any):
        """
        Re-create subscriptions listed in TargetReferenceIds.
        If the list is empty, reset ALL subscriptions.
        """
        target_refs = []
        if isinstance(payload, dict):
            target_refs = payload.get("TargetReferenceIds", [])

        should_reset_positions = (
            not target_refs or self._pos_ref_id in target_refs
        )

        if should_reset_positions:
            logger.warning("Resetting position subscription (requested by server).")
            # Delete old subscription, create new one
            try:
                old_ref = self._pos_ref_id
                self._pos_ref_id = _short_id("pos")

                req = pf.positions.PositionSubscriptionRemove(
                    ContextId=self._context_id,
                    ReferenceId=old_ref,
                )
                await self._api.request(req)
            except Exception as e:
                logger.warning("Failed to delete old subscription: %s", e)

            await self._create_position_subscription()

    # ── Re-authorisation loop ─────────────────────────────────────

    async def _reauth_loop(self):
        """
        Periodically re-authorise the WS connection so it survives token
        refreshes.  Saxo's access tokens typically last 20 minutes, so we
        re-authorise every 15 minutes.
        """
        reauth_interval = 15 * 60  # 15 minutes
        while self._running:
            await asyncio.sleep(reauth_interval)
            if not self._running:
                break
            try:
                token = self._current_token()
                url = self._build_reauth_url()
                headers = {"Authorization": f"Bearer {token}"}

                # PUT /streamingws/authorize?contextid={contextId}
                import httpx
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.put(url, headers=headers)
                    if resp.status_code == 202:
                        logger.info("WebSocket re-authorised successfully.")
                        # Also update the REST client's token
                        self._api.update_token(token)
                    else:
                        logger.warning(
                            "Re-auth returned %d: %s", resp.status_code, resp.text[:200]
                        )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Re-auth failed: %s", e, exc_info=True)

    # ── URL helpers ───────────────────────────────────────────────

    def _build_ws_url(self, token: str) -> str:
        """
        Build the WebSocket connection URL.

        Format:
          wss://streaming.saxotrader.com/[sim/]openapi/streamingws/connect?contextId=X
        """
        env = TRADING_ENVIRONMENTS[self._environment]
        stream_base = env["stream"].replace("https://", "wss://")

        if self._environment == "simulation":
            prefix = env.get("prefix", "sim")
            path = f"/{prefix}/openapi/streamingws/connect"
        else:
            path = "/openapi/streamingws/connect"

        url = f"{stream_base}{path}?contextId={self._context_id}"
        if self._last_message_id is not None:
            url += f"&messageid={self._last_message_id}"

        return url

    def _build_reauth_url(self) -> str:
        """Build the re-authorisation PUT URL."""
        env = TRADING_ENVIRONMENTS[self._environment]
        base = env["stream"]
        if self._environment == "simulation":
            prefix = env.get("prefix", "sim")
            return f"{base}/{prefix}/openapi/streamingws/authorize?contextid={self._context_id}"
        return f"{base}/openapi/streamingws/authorize?contextid={self._context_id}"

    def _current_token(self) -> str:
        if self._get_token:
            return self._get_token()
        return self._api.access_token


# ── Helpers ───────────────────────────────────────────────────────

def _short_id(prefix: str) -> str:
    """Generate a short alphanumeric ID suitable for Saxo context/ref IDs."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"
