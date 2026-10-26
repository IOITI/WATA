"""
The admin's access to the other WATA services, through the interfaces they
already expose: web_server's internal HTTP endpoints, the watchlist manager's
health endpoint, RabbitMQ, and the Saxo token files.
"""

import asyncio
import json
import logging

import httpx
from starlette.concurrency import run_in_threadpool

from src.admin.errors import ServiceUnavailable

logger = logging.getLogger(__name__)

OPS_QUEUE = "trading-ops"
TELEGRAM_QUEUE = "telegram_channel"
RABBITMQ_MANAGEMENT_PORT = 15672


class WataServices:
    def __init__(self, config_manager, http_client: httpx.AsyncClient, web_server_token_getter):
        self._config_manager = config_manager
        self._http = http_client
        self._web_server_token_getter = web_server_token_getter
        self._web_server_token = None

        signal_polling_config = config_manager.get_config_value("trade.config.signal_polling", {})
        self._web_server_url = signal_polling_config.get("service_url", "http://web_server1:80").rstrip("/")
        watchlist_config = config_manager.get_config_value("trade.config.watchlist_manager", {})
        # On unless false, as the trader reads it (src/trade/watchlist_client.py).
        self._watchlist_enabled = watchlist_config.get("enabled", True)
        self._watchlist_url = watchlist_config.get("service_url", "http://watchlist_manager1:8081").rstrip("/")

    # ── web_server ──

    async def _web_server_request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """Call web_server with its Bearer token, re-reading the token once if it was rotated."""
        try:
            for attempt in range(2):
                if self._web_server_token is None:
                    if asyncio.iscoroutinefunction(self._web_server_token_getter):
                        self._web_server_token = await self._web_server_token_getter()
                    else:
                        self._web_server_token = await run_in_threadpool(self._web_server_token_getter)
                response = await self._http.request(
                    method,
                    f"{self._web_server_url}{path}",
                    headers={"Authorization": f"Bearer {self._web_server_token}"},
                    **kwargs,
                )
                if response.status_code == 401 and attempt == 0:
                    self._web_server_token = None
                    continue
                response.raise_for_status()
                return response
        except httpx.HTTPError as e:
            raise ServiceUnavailable(f"web_server: {e}") from e

    async def latest_signals(self) -> list[dict]:
        response = await self._web_server_request("GET", "/latest-signals")
        return response.json()

    async def send_signal(self, payload: dict) -> str:
        response = await self._web_server_request("POST", "/internal/signal", json=payload)
        return response.json()["signal_uuid"]

    async def webhook_state(self) -> dict | None:
        """
        The source each index takes its signals from as the web server runs (it read them at its start), and
        the webhooks it answered without storing them since then; None from a web server before v0.48.0.
        """
        try:
            response = await self._web_server_request("GET", "/internal/webhook")
        except ServiceUnavailable as e:
            cause = e.__cause__
            if isinstance(cause, httpx.HTTPStatusError) and cause.response.status_code == 404:
                return None
            raise
        return response.json()

    # ── watchlist manager ──

    @property
    def watchlist_enabled(self) -> bool:
        return self._watchlist_enabled

    async def watchlist_health(self) -> dict:
        """The watchlist manager's own health snapshot (it answers 503 with one while warming up)."""
        try:
            response = await self._http.get(f"{self._watchlist_url}/healthz")
            snapshot = response.json()
        except (httpx.HTTPError, ValueError) as e:
            raise ServiceUnavailable(f"watchlist_manager: {e}") from e
        if not isinstance(snapshot, dict) or "ready" not in snapshot:
            raise ServiceUnavailable(f"watchlist_manager: unexpected answer (HTTP {response.status_code})")
        return snapshot

    async def watchlist_best(self, indice: str, direction: str) -> dict | None:
        """The turbo the trader would buy now for ``indice`` and ``direction`` (None when nothing is cached)."""
        try:
            response = await self._http.get(
                f"{self._watchlist_url}/watchlist/best",
                params={"indice": indice, "direction": direction},
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as e:
            raise ServiceUnavailable(f"watchlist_manager: {e}") from e

    @property
    def watchlist_directions(self) -> list[str]:
        watchlist_config = self._config_manager.get_config_value("trade.config.watchlist_manager", {})
        return list(watchlist_config.get("directions", ["long", "short"]))

    # ── RabbitMQ ──

    def _rabbitmq_config(self) -> tuple[str, str, str]:
        rabbitmq_config = self._config_manager.get_rabbitmq_config()
        return (
            rabbitmq_config["hostname"],
            rabbitmq_config["authentication"]["username"],
            rabbitmq_config["authentication"]["password"],
        )

    async def queue_stats(self) -> list[dict]:
        """Depth and consumer count of the queues, from the RabbitMQ management API."""
        hostname, username, password = self._rabbitmq_config()
        queues = []
        for queue_name in (OPS_QUEUE, TELEGRAM_QUEUE):
            try:
                response = await self._http.get(
                    f"http://{hostname}:{RABBITMQ_MANAGEMENT_PORT}/api/queues/%2F/{queue_name}",
                    auth=(username, password),
                )
            except httpx.HTTPError as e:
                raise ServiceUnavailable(f"rabbitmq: {e}") from e
            if response.status_code == 404:
                # Not declared yet: nothing has published to or consumed from it.
                queues.append({"name": queue_name, "declared": False, "messages": 0, "consumers": 0})
                continue
            if response.status_code != 200:
                raise ServiceUnavailable(f"rabbitmq: HTTP {response.status_code}")
            queue = response.json()
            queues.append({
                "name": queue_name,
                "declared": True,
                "messages": queue.get("messages", 0),
                "consumers": queue.get("consumers", 0),
            })
        return queues

    def _publish_ops_blocking(self, message: dict) -> None:
        import pika

        hostname, username, password = self._rabbitmq_config()
        connection = pika.BlockingConnection(
            pika.ConnectionParameters(
                host=hostname,
                credentials=pika.PlainCredentials(username, password),
                socket_timeout=5,
                blocked_connection_timeout=5,
            )
        )
        try:
            channel = connection.channel()
            channel.queue_declare(queue=OPS_QUEUE, durable=True)
            channel.basic_publish(exchange="", routing_key=OPS_QUEUE, body=json.dumps(message))
        finally:
            connection.close()

    async def publish_ops(self, message: dict) -> None:
        """Publish an operational message to trading-ops, as the scheduler does."""
        try:
            await run_in_threadpool(self._publish_ops_blocking, message)
        except Exception as e:
            raise ServiceUnavailable(f"rabbitmq: {e}") from e
        logger.info(f"Send message to channel {OPS_QUEUE}, message {message}")
