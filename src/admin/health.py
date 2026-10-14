"""
Health of the WATA services, as far as it can be observed from outside them.

The admin has no access to Docker. What it reports comes from talking to each
service where there is something to talk to, from RabbitMQ's consumer counts
(position_monitor and telegram are the consumers of their queues), and, for
the trader and the scheduler, from the start/stop lines of their logs.
"""

import asyncio
import os
from datetime import datetime, timezone

from starlette.concurrency import run_in_threadpool

from src.admin.errors import DatabaseUnavailable, ServiceUnavailable
from src.admin.logs import last_lifecycle_event
from src.admin.services import OPS_QUEUE, TELEGRAM_QUEUE
from src.price_recorder.bars import BAR_SECONDS
from src.price_recorder.settings import RecorderSettings

OK = "ok"
WARNING = "warning"
DOWN = "down"
UNKNOWN = "unknown"
DISABLED = "disabled"

# Services with no interface to probe: log file -> lines marking their lifecycle.
LOG_OBSERVED_SERVICES = {
    "trader": {
        "name": "Trader",
        "log": "wata-trader-async",
        "markers": {
            "ready": "Trader startup complete",
            "starting": "Starting WATA Async Trader",
            "stopped": "Shutting down WATA Async Trader",
        },
    },
    "scheduler": {
        "name": "Scheduler",
        "log": "wata-scheduler",
        "markers": {
            "ready": "WATA scheduler is running",
            "starting": "Started wata-scheduler application",
        },
    },
}


SAXO_SERVICE_ID = "saxo"
# The recorder writes one line every ten seconds: past this, it is not running.
PRICE_RECORDER_SILENT_AFTER_SECONDS = 60


def _entry(service_id: str, name: str, status: str, detail: str) -> dict:
    return {"id": service_id, "name": name, "status": status, "detail": detail}


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def needs_saxo_authorisation(status: dict) -> bool:
    """Whether the services can no longer renew their Saxo token by themselves."""
    return not status.get("has_token") or bool(status.get("refresh_token_expired"))


async def _saxo(ctx) -> list[dict]:
    # Not a service, but the one condition under which every service is up and
    # nothing can be traded: it belongs where the operator looks first.
    name = "Saxo connection"
    try:
        status = await run_in_threadpool(ctx.saxo.status)
    except Exception as e:
        return [_entry(SAXO_SERVICE_ID, name, UNKNOWN, f"Token status unreadable: {e}")]
    if needs_saxo_authorisation(status):
        return [_entry(SAXO_SERVICE_ID, name, DOWN, "Authorisation required: nothing can be traded until it is renewed")]
    return [_entry(SAXO_SERVICE_ID, name, OK, "Authorised")]


async def _postgres(ctx) -> list[dict]:
    try:
        await ctx.queries.ping()
    except DatabaseUnavailable as e:
        return [_entry("postgres", "PostgreSQL", DOWN, str(e))]
    return [_entry("postgres", "PostgreSQL", OK, "Answering queries")]


async def _web_server(ctx) -> list[dict]:
    try:
        signals = await ctx.services.latest_signals()
    except ServiceUnavailable as e:
        return [_entry("web_server", "Web server", DOWN, str(e))]
    return [_entry("web_server", "Web server", OK, f"{_count(len(signals), 'signal')} in store")]


async def _watchlist(ctx) -> tuple[list[dict], dict | None]:
    name = "Watchlist manager"
    if not ctx.services.watchlist_enabled:
        return [_entry("watchlist_manager", name, DISABLED, "Disabled in the configuration")], None
    try:
        snapshot = await ctx.services.watchlist_health()
    except ServiceUnavailable as e:
        return [_entry("watchlist_manager", name, DOWN, str(e))], None

    warm = f"{snapshot.get('warm_entries', 0)}/{snapshot.get('configured_targets', 0)} turbos cached"
    if not snapshot["ready"]:
        entry = _entry("watchlist_manager", name, WARNING, f"Warming up, {warm}")
    elif snapshot.get("stale_entries") or snapshot.get("last_refresh_errors"):
        refresh_errors = _count(len(snapshot.get("last_refresh_errors") or []), "refresh error")
        problems = f"{snapshot.get('stale_entries', 0)} stale, {refresh_errors}"
        entry = _entry("watchlist_manager", name, WARNING, f"{warm}, {problems}")
    else:
        entry = _entry("watchlist_manager", name, OK, warm)
    return [entry], snapshot


def _silent_for(status: dict, now: datetime) -> float:
    # The line of a bucket is written when it ends, ten seconds after its start.
    return (now - status["ts"]).total_seconds() - BAR_SECONDS


def recorder_running(status: dict | None, now: datetime) -> bool:
    """Whether the recorder wrote its line of coverage lately: it does every ten seconds while it runs."""
    return status is not None and _silent_for(status, now) <= PRICE_RECORDER_SILENT_AFTER_SECONDS


def recorder_state(settings: RecorderSettings, status: dict | None, now: datetime) -> tuple[str, str]:
    """
    What the recorder's last line of coverage says of it, as (status, detail).
    ``status`` is the last ten seconds it lived through (``price_recorder_status``),
    None when it never wrote one. Health and the Providers page both say this.
    """
    if status is None:
        return DOWN, "Nothing recorded yet"
    if not recorder_running(status, now):
        return DOWN, f"No sign of it for {int(_silent_for(status, now) // 60)} min"
    if status["bars"] > 0:
        return OK, f"Recording {_count(status['bars'], 'instrument')}"
    if settings.is_trading_time(now):
        return DOWN, "Running, but no price is being recorded"
    return OK, "Running; the markets are closed"


async def _price_recorder(ctx) -> list[dict]:
    """
    The price recorder, when it is switched on. What it misses cannot be
    fetched afterwards, so it is watched like the services that trade.
    """
    name = "Price recorder"
    try:
        settings = RecorderSettings.from_config(ctx.config_manager)
    except ValueError as e:
        return [_entry("price_recorder", name, DOWN, f"Invalid configuration: {e}")]
    if not settings.enabled:
        return []
    try:
        status = await ctx.queries.price_recorder_status()
    except DatabaseUnavailable:
        return [_entry("price_recorder", name, UNKNOWN, "The database is unavailable")]
    return [_entry("price_recorder", name, *recorder_state(settings, status, datetime.now(timezone.utc)))]


def _consumer_entry(service_id: str, name: str, queue: dict) -> dict:
    if queue["consumers"] > 0:
        return _entry(service_id, name, OK, f"Consuming {queue['name']}")
    return _entry(service_id, name, DOWN, f"No consumer on {queue['name']}")


async def _rabbitmq(ctx) -> tuple[list[dict], list[dict]]:
    try:
        queues = await ctx.services.queue_stats()
    except ServiceUnavailable as e:
        return [
            _entry("rabbitmq", "RabbitMQ", DOWN, str(e)),
            _entry("position_monitor", "Position monitor", UNKNOWN, "RabbitMQ is unreachable"),
            _entry("telegram", "Telegram", UNKNOWN, "RabbitMQ is unreachable"),
        ], []

    by_name = {queue["name"]: queue for queue in queues}
    return [
        _entry("rabbitmq", "RabbitMQ", OK, "Management API answering"),
        _consumer_entry("position_monitor", "Position monitor", by_name[OPS_QUEUE]),
        _consumer_entry("telegram", "Telegram", by_name[TELEGRAM_QUEUE]),
    ], queues


def _log_observed(log_path: str) -> list[dict]:
    entries = []
    for service_id, service in LOG_OBSERVED_SERVICES.items():
        event = last_lifecycle_event(log_path, service["log"], service["markers"])
        if event is None:
            entries.append(_entry(service_id, service["name"], UNKNOWN, "No start line in its current log file"))
        elif event["event"] == "ready":
            entries.append(_entry(service_id, service["name"], OK, f"Started {event['timestamp']} (from its log)"))
        elif event["event"] == "starting":
            entries.append(_entry(service_id, service["name"], WARNING, f"Starting since {event['timestamp']} (from its log)"))
        else:
            entries.append(_entry(service_id, service["name"], DOWN, f"Stopped {event['timestamp']} (from its log)"))
    return entries


def _overall(services: list[dict]) -> str:
    statuses = {service["status"] for service in services}
    for status in (DOWN, WARNING):
        if status in statuses:
            return status
    return OK


async def collect_health(ctx) -> dict:
    (
        postgres, web_server, (watchlist, watchlist_snapshot), (rabbitmq, queues), log_observed, saxo, price_recorder,
    ) = await asyncio.gather(
        _postgres(ctx),
        _web_server(ctx),
        _watchlist(ctx),
        _rabbitmq(ctx),
        run_in_threadpool(_log_observed, ctx.settings.log_path),
        _saxo(ctx),
        _price_recorder(ctx),
    )
    services = [
        *saxo, *web_server, *log_observed, *rabbitmq[1:], *watchlist, *price_recorder, *postgres, rabbitmq[0],
    ]
    return {
        "version": ctx.version,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "status": _overall(services),
        "services": services,
        "queues": queues,
        "watchlist": watchlist_snapshot,
        "config_modified_at": datetime.fromtimestamp(
            os.path.getmtime(ctx.settings.config_path), tz=timezone.utc
        ).isoformat(),
    }
