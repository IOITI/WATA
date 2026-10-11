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
from urllib.parse import urlencode
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
InfoPriceSubscriptionUpdateCallback = Callable[[dict, list[dict], str | None], Coroutine[Any, Any, None]]


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
    api_client : AsyncAPI | compatible wrapper
        An already-initialised async HTTP client or wrapper exposing
        ``request()``. The position monitor passes ``AsyncSaxoApiClient`` so
        token refreshes are handled consistently for REST subscription calls.
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
    reauth_interval_seconds : int
        Interval between WebSocket re-authorisation calls.
    """

    # ── Construction ──────────────────────────────────────────────

    def __init__(
        self,
        api_client,
        account_key: str,
        client_key: str,
        on_positions_update: PositionUpdateCallback | None,
        environment: str = "live",
        access_token_getter: Callable[[], str] | None = None,
        refresh_rate_ms: int = 1000,
        reconnect_delay: float = 1.0,
        max_reconnect_delay: float = 30.0,
        reauth_interval_seconds: int = 15 * 60,
        on_info_price_subscription_update: InfoPriceSubscriptionUpdateCallback | None = None,
    ):
        self._api = api_client
        self._account_key = account_key
        self._client_key = client_key
        self._on_positions_update = on_positions_update
        self._on_info_price_subscription_update = on_info_price_subscription_update
        self._environment = environment
        self._get_token = access_token_getter
        self._refresh_rate_ms = refresh_rate_ms
        self._reconnect_delay = reconnect_delay
        self._max_reconnect_delay = max_reconnect_delay
        self._reauth_interval_seconds = reauth_interval_seconds

        # Connection state
        self._context_id: str = ""
        self._pos_ref_id: str = ""
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._last_message_id: int | None = None
        self._running = False
        self._receive_task: asyncio.Task | None = None
        self._reauth_task: asyncio.Task | None = None
        self._subscription_ready = False

        # Snapshot cache:  {position_id: full_position_dict}
        self._positions: dict[str, dict] = {}
        self._desired_info_price_subscriptions: dict[str, dict] = {}
        self._active_info_price_subscriptions: dict[str, dict] = {}
        self._info_price_subscriptions_lock = asyncio.Lock()

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

    @property
    def context_id(self) -> str | None:
        return self._context_id or None

    @property
    def active_subscription_count(self) -> int:
        return len(self._active_info_price_subscriptions)

    @property
    def desired_subscription_count(self) -> int:
        return len(self._desired_info_price_subscriptions)

    async def set_subscriptions(self, definitions: list[dict]):
        """Set grouped InfoPrice subscriptions to maintain alongside position streaming."""
        normalized = {}
        for definition in definitions:
            reference_id = definition.get("reference_id") or _short_id("wlp")
            normalized_definition = copy.deepcopy(definition)
            normalized_definition["reference_id"] = reference_id
            normalized_definition["uics"] = [int(uic) for uic in normalized_definition.get("uics", [])]
            normalized_definition["field_groups"] = normalized_definition.get(
                "field_groups",
                ["Quote", "DisplayAndFormat", "InstrumentPriceDetails", "Commissions"],
            )
            normalized[reference_id] = normalized_definition

        async with self._info_price_subscriptions_lock:
            self._desired_info_price_subscriptions = normalized

        if self._ws is not None and not self._ws.closed:
            await self._sync_desired_info_price_subscriptions()

    # ── Connection ────────────────────────────────────────────────

    async def _connect_and_subscribe(self):
        """Open WS, create position subscription, start reauth timer."""
        # 1. Generate IDs once and reuse them across reconnects.
        if not self._context_id:
            self._context_id = _short_id("ctx")
        if self._on_positions_update is not None and not self._pos_ref_id:
            self._pos_ref_id = _short_id("pos")

        # 2. Build WS URL
        token = self._current_token()
        ws_url = self._build_ws_url(token)

        # 3. Open WebSocket
        logger.info("Connecting to Saxo WebSocket (contextId=%s) …", self._context_id)
        self._ws = await websockets.connect(
            ws_url,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=5,
            max_size=2 ** 22,  # 4 MiB
        )
        logger.info("WebSocket connected.")

        # 4. Position subscription (REST call, snapshot returned) — only when needed.
        if self._on_positions_update is not None and not self._subscription_ready:
            await self._create_position_subscription()
            self._subscription_ready = True

        await self._sync_desired_info_price_subscriptions()

        # 5. Periodic re-authorisation in background
        if self._reauth_task and not self._reauth_task.done():
            self._reauth_task.cancel()
        self._reauth_task = asyncio.create_task(self._reauth_loop())

    async def _create_position_subscription(self):
        """Create a position list subscription via REST and seed the cache."""
        self._sync_api_token()

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
        resp = await self._request(req)

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

    async def _create_info_price_subscription(self, definition: dict):
        if not definition.get("uics"):
            return

        self._sync_api_token()

        request_data = {
            "Arguments": {
                "AccountKey": self._account_key,
                "AssetType": definition["asset_type"],
                "FieldGroups": definition.get("field_groups", []),
                "Uics": ",".join(str(uic) for uic in definition["uics"]),
            },
            "ContextId": self._context_id,
            "ReferenceId": definition["reference_id"],
            "RefreshRate": self._refresh_rate_ms,
            "Format": "application/json",
        }

        response = await self._request(tr.infoprices.CreateInfoPriceSubscription(data=request_data))
        snapshot_rows = (response or {}).get("Snapshot", {}).get("Data", []) or []
        snapshot_by_uic = {
            row["Uic"]: row
            for row in snapshot_rows
            if row.get("Uic") is not None
        }
        self._active_info_price_subscriptions[definition["reference_id"]] = {
            "definition": copy.deepcopy(definition),
            "snapshot_by_uic": snapshot_by_uic,
        }

        if snapshot_by_uic:
            await self._fire_info_price_callback(
                definition,
                copy.deepcopy(list(snapshot_by_uic.values())),
            )

    async def _sync_desired_info_price_subscriptions(self):
        async with self._info_price_subscriptions_lock:
            desired_subscriptions = copy.deepcopy(self._desired_info_price_subscriptions)

        active_reference_ids = list(self._active_info_price_subscriptions.keys())
        for reference_id in active_reference_ids:
            desired_definition = desired_subscriptions.get(reference_id)
            active_definition = self._active_info_price_subscriptions.get(reference_id, {}).get("definition")
            if desired_definition is None or active_definition != desired_definition:
                await self._remove_info_price_subscription(reference_id)

        for reference_id, definition in desired_subscriptions.items():
            if reference_id not in self._active_info_price_subscriptions:
                await self._create_info_price_subscription(definition)

    async def _remove_info_price_subscription(self, reference_id: str):
        if not self._context_id:
            self._active_info_price_subscriptions.pop(reference_id, None)
            return
        try:
            await self._request(
                tr.infoprices.RemoveInfoPriceSubscriptionById(
                    ContextId=self._context_id,
                    ReferenceId=reference_id,
                )
            )
        except Exception as exc:
            logger.warning("Failed to remove info price subscription %s: %s", reference_id, exc)
        finally:
            self._active_info_price_subscriptions.pop(reference_id, None)

    async def _cleanup_subscriptions(self):
        """Delete active subscriptions (best-effort)."""
        if not self._context_id:
            return
        if self._active_info_price_subscriptions:
            try:
                self._sync_api_token()
                req = tr.infoprices.RemoveInfoPriceSubscriptionsByTag(ContextId=self._context_id)
                await self._request(req)
                logger.info("InfoPrice subscriptions for context %s removed.", self._context_id)
            except Exception as e:
                logger.warning("Failed to clean up info price subscriptions: %s", e)
            finally:
                self._active_info_price_subscriptions.clear()
        try:
            if self._subscription_ready:
                self._sync_api_token()
                req = pf.positions.PositionSubscriptionRemoveMultiple(
                    ContextId=self._context_id
                )
                await self._request(req)
                logger.info("Position subscriptions for context %s removed.", self._context_id)
                self._subscription_ready = False
        except Exception as e:
            logger.warning("Failed to clean up position subscriptions: %s", e)

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
                    elif ref_id in self._active_info_price_subscriptions:
                        await self._handle_info_price_subscription_update(ref_id, payload)
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
        if self._on_positions_update is None:
            return
        try:
            await self._on_positions_update(copy.deepcopy(self._positions))
        except Exception as e:
            logger.error("Error in positions-update callback: %s", e, exc_info=True)

    async def _handle_info_price_subscription_update(self, reference_id: str, payload: dict | list | Any):
        updates: list[dict] = []

        if isinstance(payload, dict):
            if "Data" in payload:
                updates = payload["Data"]
            elif "Uic" in payload:
                updates = [payload]
            else:
                updates = [payload]
        elif isinstance(payload, list):
            updates = payload
        else:
            logger.warning("Unexpected info price payload type: %s", type(payload))
            return

        active_subscription = self._active_info_price_subscriptions.get(reference_id)
        if active_subscription is None:
            return

        changed = False
        for delta in updates:
            uic = delta.get("Uic")
            if uic is None:
                continue
            if uic in active_subscription["snapshot_by_uic"]:
                _deep_merge(active_subscription["snapshot_by_uic"][uic], delta)
            else:
                active_subscription["snapshot_by_uic"][uic] = delta
            changed = True

        if changed:
            await self._fire_info_price_callback(
                active_subscription["definition"],
                copy.deepcopy(list(active_subscription["snapshot_by_uic"].values())),
            )

    async def _fire_info_price_callback(self, definition: dict, snapshot_rows: list[dict]):
        if self._on_info_price_subscription_update is None:
            return
        try:
            await self._on_info_price_subscription_update(copy.deepcopy(definition), snapshot_rows, self.context_id)
        except Exception as exc:
            logger.error("Error in info-price subscription callback: %s", exc, exc_info=True)

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

        should_reset_positions = self._on_positions_update is not None and (
            not target_refs or self._pos_ref_id in target_refs
        )

        if should_reset_positions:
            logger.warning("Resetting position subscription (requested by server).")
            # Delete old subscription, create new one
            try:
                self._sync_api_token()
                old_ref = self._pos_ref_id
                self._pos_ref_id = _short_id("pos")
                self._subscription_ready = False

                req = pf.positions.PositionSubscriptionRemove(
                    ContextId=self._context_id,
                    ReferenceId=old_ref,
                )
                await self._request(req)
            except Exception as e:
                logger.warning("Failed to delete old subscription: %s", e)

            await self._create_position_subscription()
            self._subscription_ready = True

        refs_to_reset = list(self._active_info_price_subscriptions.keys())
        if target_refs:
            refs_to_reset = [ref_id for ref_id in refs_to_reset if ref_id in target_refs]

        if refs_to_reset:
            logger.warning("Resetting %d info-price subscription(s).", len(refs_to_reset))
            async with self._info_price_subscriptions_lock:
                desired_subscriptions = copy.deepcopy(self._desired_info_price_subscriptions)

            for ref_id in refs_to_reset:
                await self._remove_info_price_subscription(ref_id)
                definition = desired_subscriptions.get(ref_id)
                if definition is not None:
                    await self._create_info_price_subscription(definition)

    # ── Re-authorisation loop ─────────────────────────────────────

    async def _reauth_loop(self):
        """
        Periodically re-authorise the WS connection so it survives token
        refreshes.  Saxo's access tokens typically last 20 minutes, so we
        re-authorise every 15 minutes.
        """
        while self._running:
            await asyncio.sleep(self._reauth_interval_seconds)
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
                        self._update_rest_client_token(token)
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
          wss://sim-streaming.saxobank.com/sim/oapi/streaming/ws/connect?authorization=BEARER%20TOKEN&contextId=X
        """
        env = TRADING_ENVIRONMENTS[self._environment]
        stream_base = env["stream"].replace("https://", "wss://")

        if self._environment == "simulation":
            prefix = env.get("prefix", "sim")
            path = f"/{prefix}/oapi/streaming/ws/connect"
        else:
            path = "/oapi/streaming/ws/connect"

        params = {
            "authorization": f"BEARER {token}",
            "contextId": self._context_id,
        }
        if self._last_message_id is not None:
            params["messageid"] = str(self._last_message_id)

        return f"{stream_base}{path}?{urlencode(params)}"

    def _build_reauth_url(self) -> str:
        """Build the re-authorisation PUT URL."""
        env = TRADING_ENVIRONMENTS[self._environment]
        base = env["stream"]
        if self._environment == "simulation":
            prefix = env.get("prefix", "sim")
            return f"{base}/{prefix}/oapi/streaming/ws/authorize?contextid={self._context_id}"
        return f"{base}/oapi/streaming/ws/authorize?contextid={self._context_id}"

    def _current_token(self) -> str:
        token = self._get_token() if self._get_token else self._rest_client_access_token
        if token and token != self._rest_client_access_token:
            self._update_rest_client_token(token)
            logger.info("Updated Saxo streaming REST client with refreshed access token.")
        return token

    def _sync_api_token(self) -> str:
        """Ensure the underlying AsyncAPI client uses the freshest access token."""
        return self._current_token()

    async def _request(self, endpoint):
        """Dispatch a REST request through the configured async API client/wrapper."""
        return await self._api.request(endpoint)

    @property
    def _rest_client_access_token(self) -> str | None:
        if hasattr(self._api, "access_token"):
            return self._api.access_token
        nested_api = getattr(self._api, "_api", None)
        if nested_api is not None and hasattr(nested_api, "access_token"):
            return nested_api.access_token
        return None

    def _update_rest_client_token(self, token: str):
        """Update the underlying REST client's bearer token when supported."""
        if hasattr(self._api, "update_token"):
            self._api.update_token(token)
            return

        nested_api = getattr(self._api, "_api", None)
        if nested_api is not None and hasattr(nested_api, "update_token"):
            nested_api.update_token(token)


# ── Helpers ───────────────────────────────────────────────────────

def _short_id(prefix: str) -> str:
    """Generate a short alphanumeric ID suitable for Saxo context/ref IDs."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"
