"""
Health of the WATA services, as far as it can be observed from outside them.

The admin does not hold Docker's socket: what Docker says of each container
(running, since when, restarts) is the control service's, on the System page's
services table (src/admin/routers/services.py). What it reports here comes
from talking to each service where there is something to talk to, from
RabbitMQ's consumer counts (position_monitor and telegram are the consumers of
their queues), from what the trader and the position monitor say of
themselves every fifteen seconds (``service_heartbeat``: the position stream,
the polling of signals, Saxo's login, the sales waiting), and, for the
scheduler and a trader of before the heartbeat, from the start/stop lines of
their logs.
"""

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone

import pytz

from starlette.concurrency import run_in_threadpool

from src.admin.data import DISABLED as PROVIDER_DISABLED
from src.admin.data import capital_provider_state
from src.admin.errors import DatabaseUnavailable, ServiceUnavailable
from src.admin.logs import last_lifecycle_event
from src.admin.saxo import called
from src.admin.services import OPS_QUEUE, TELEGRAM_QUEUE
from src.admin.ta_monitor import TICK_SECONDS, WINDOW_MINUTES, checks, health_warning, rules_by_id
from src.capital_feed.settings import CapitalSettings
from src.price_recorder.bars import BAR_SECONDS
from src.price_recorder.settings import RecorderSettings
from src.ta_engine.settings import TaEngineSettings
from src.trade.macro_calendar import read_calendar, upkeep
from src.trade.market_calendar import read_market_calendar, upkeep as market_upkeep
from src.heartbeat import HEARTBEAT_STALE_SECONDS

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
            "stopped": "WATA scheduler stopped",
        },
    },
}


SAXO_SERVICE_ID = "saxo"
# The recorder writes one line every ten seconds: past this, it is not running.
PRICE_RECORDER_SILENT_AFTER_SECONDS = 60
# The TA engine writes its state every ten seconds, every minute while idle.
TA_ENGINE_SILENT_AFTER_SECONDS = 180


def _entry(service_id: str, name: str, status: str, detail: str) -> dict:
    return {"id": service_id, "name": name, "status": status, "detail": detail}


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def needs_saxo_authorisation(status: dict) -> bool:
    """
    Whether the services can no longer renew their Saxo token by themselves. Not
    known while the login has no row in PostgreSQL (the token file of before).
    """
    if status.get("where") == "none":
        return False
    return not status.get("has_token") or bool(status.get("refresh_token_expired"))


async def _saxo(ctx) -> list[dict]:
    # Not a service, but the one condition under which every service is up and
    # nothing can be traded: it belongs where the operator looks first.
    name = "Saxo connection"
    try:
        status = await called(ctx.saxo.status)
    except Exception as e:
        return [_entry(SAXO_SERVICE_ID, name, UNKNOWN, f"Token status unreadable: {e}")]
    if status.get("where") == "none":
        return [_entry(SAXO_SERVICE_ID, name, UNKNOWN,
                       "Saxo's login is not in PostgreSQL yet: the services read its file of before (migration)")]
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


async def _capital_feed(ctx) -> list[dict]:
    """
    The Capital.com feed, when it is switched on: like the price recorder, what
    it misses cannot be fetched afterwards. Down without a login or after a
    refused one, said by the state it writes itself.
    """
    name = "Capital.com feed"
    try:
        settings = CapitalSettings.from_config(ctx.config_manager)
    except ValueError as e:
        return [_entry("capital_feed", name, DOWN, f"Invalid configuration: {e}")]
    if not settings.enabled:
        return []
    try:
        state = await ctx.queries.capital_feed_state()
    except DatabaseUnavailable:
        return [_entry("capital_feed", name, UNKNOWN, "The database is unavailable")]
    status, detail = capital_provider_state(True, state, datetime.now(timezone.utc))
    return [_entry("capital_feed", name, DOWN if status == PROVIDER_DISABLED else status, detail)]


def ta_engine_status(enabled_on_disk: bool, state: dict | None, now: datetime) -> tuple[str, str]:
    """
    The TA engine as the System and Signals pages say it: switched on or off in
    the configuration as it is on disk now, and what it last wrote of itself
    (ta_engine_state).
    """
    updated_at = (state or {}).get("updated_at")
    if updated_at is None or (now - updated_at).total_seconds() > TA_ENGINE_SILENT_AFTER_SECONDS:
        if not enabled_on_disk:
            return DISABLED, "Switched off in the configuration"
        if state is None:
            return DOWN, 'Never started: add "ta" to COMPOSE_PROFILES in .env and start it'
        minutes = int((now - updated_at).total_seconds() // 60)
        return DOWN, f"No sign of it for {minutes} min"
    kind, detail = state.get("state"), state.get("detail") or ""
    if kind == "disabled":
        if enabled_on_disk:
            return WARNING, "Switched on in the configuration: it starts at its next start"
        return DISABLED, "Switched off in the configuration"
    if not enabled_on_disk:
        return WARNING, "Switched off in the configuration: it runs until it is restarted"
    if kind == "running":
        return OK, detail or "Running"
    if kind == "idle":
        return OK, detail or "No rule is on"
    if kind == "stale_feed":
        return WARNING, detail or "The Capital.com feed is not recording"
    return DOWN, detail or kind or "Unknown state"


async def ta_engine_health(ctx, enabled_on_disk: bool, settings: TaEngineSettings, state: dict | None, now: datetime) -> tuple[str, str]:
    """
    ``ta_engine_status``, and while it says the engine runs, the Engine monitor's checks of the last
    minutes (src/admin/ta_monitor.py): a warning with the first that failed, of what can cost a live
    signal. The System, Signal rules and Engine monitor pages say the same.
    """
    status, detail = ta_engine_status(enabled_on_disk, state, now)
    if status != OK:
        return status, detail
    try:
        recent = await ctx.queries.ta_monitor_recent(now - timedelta(minutes=WINDOW_MINUTES), slow_tick_ms=TICK_SECONDS * 1000)
    except DatabaseUnavailable:
        return status, detail
    rules = await run_in_threadpool(rules_by_id, getattr(ctx, "ta_scripts", None))
    warning = health_warning(checks(recent, settings, rules))
    return (WARNING, warning) if warning else (status, detail)


async def _ta_engine(ctx) -> list[dict]:
    """The TA engine, when it is switched on: a rule in error is said on its page, not here."""
    name = "TA engine"
    try:
        settings = TaEngineSettings.from_config(ctx.config_manager)
    except ValueError as e:
        return [_entry("ta_engine", name, DOWN, f"Invalid configuration: {e}")]
    if not settings.enabled:
        return []
    try:
        state = await ctx.queries.ta_engine_state()
    except DatabaseUnavailable:
        return [_entry("ta_engine", name, UNKNOWN, "The database is unavailable")]
    status, detail = await ta_engine_health(ctx, True, settings, state, datetime.now(timezone.utc))
    return [_entry("ta_engine", name, DOWN if status == DISABLED else status, detail)]


def macro_calendar_entry(rule_on: bool, calendar, today) -> list[dict]:
    """
    The calendar of US releases, while the trader keeps entries out of a window
    around them (the macro_news rule): a warning from WARN_DAYS before it runs
    out. Past it, the trader trades on without a window.
    """
    if not rule_on:
        return []
    name = "US releases calendar"
    if calendar is None:
        return [_entry("macro_calendar", name, WARNING, "Cannot be read: no window is kept around the releases")]
    said = upkeep(calendar, today)
    if said is not None:
        return [_entry("macro_calendar", name, WARNING, said)]
    return [_entry("macro_calendar", name, OK, f"Known until {calendar.known_until().isoformat()}")]


def _macro_calendar(ctx) -> list[dict]:
    rules = ctx.config_manager.get_config_value("trade.rules", []) or []
    if not any(isinstance(rule, dict) and rule.get("rule_type") == "macro_news" for rule in rules):
        return []
    try:
        calendar = read_calendar()
    except (OSError, ValueError, KeyError):
        calendar = None
    today = datetime.now(pytz.timezone(ctx.settings.timezone)).date()
    return macro_calendar_entry(True, calendar, today)


def market_calendar_entry(calendar, today) -> list[dict]:
    """
    The calendar of US market days (holidays, early closes), which the trader
    always keeps: a warning from WARN_DAYS before it runs out. Past it, the days
    are traded as ordinary ones.
    """
    name = "US market days calendar"
    if calendar is None:
        return [_entry("market_calendar", name, WARNING, "Cannot be read: no holiday nor early close is kept")]
    said = market_upkeep(calendar, today)
    if said is not None:
        return [_entry("market_calendar", name, WARNING, said)]
    return [_entry("market_calendar", name, OK, f"Known until {calendar.until.isoformat()}")]


def _market_calendar(ctx) -> list[dict]:
    try:
        calendar = read_market_calendar()
    except (OSError, ValueError, KeyError):
        calendar = None
    return market_calendar_entry(calendar, datetime.now(pytz.timezone(ctx.settings.timezone)).date())


# The dumps are made each night (pg_backup1): past this, the last one is too old.
BACKUP_STALE_HOURS = 30


def backups_entry(last: dict | None, now: datetime, timezone_name: str) -> list[dict]:
    """What the last night of pg_backup1 did (its last.json): said by health, a warning when it failed or is old."""
    name = "Database backups"
    if last is None:
        return [_entry("backups", name, UNKNOWN, "No dump made yet: the first comes at 02:17 UTC")]
    try:
        at = datetime.fromisoformat(str(last["at"]).replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return [_entry("backups", name, WARNING, "Its last report cannot be read")]
    when = at.astimezone(pytz.timezone(timezone_name)).strftime("%d/%m %H:%M")
    if not last.get("ok"):
        return [_entry("backups", name, WARNING, f"The dump of {when} failed: {last.get('detail')}")]
    if (now - at).total_seconds() > BACKUP_STALE_HOURS * 3600:
        return [_entry("backups", name, WARNING, f"No dump since {when}")]
    size = (last.get("bytes") or 0) / 1_000_000
    return [_entry("backups", name, OK, f"Last dump {when}, {size:.0f} MB ({last.get('file')})")]


def _backups(ctx) -> list[dict]:
    path = os.path.join(ctx.settings.backups_path, "last.json")
    try:
        with open(path, encoding="utf-8") as report:
            last = json.load(report)
    except FileNotFoundError:
        last = None
    except (OSError, ValueError):
        last = {}
    return backups_entry(last, datetime.now(timezone.utc), ctx.settings.timezone)


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


def _clock(iso: str | None, timezone_name: str) -> str:
    try:
        return datetime.fromisoformat(iso).astimezone(pytz.timezone(timezone_name)).strftime("%H:%M")
    except (TypeError, ValueError):
        return "?"


def _beat_age(beat: dict, now: datetime) -> float:
    return (now - beat["beat_at"]).total_seconds()


def trader_health(beat: dict, now: datetime, timezone_name: str) -> tuple[str, str]:
    """What the trader's heartbeat says of it, as (status, detail)."""
    age = _beat_age(beat, now)
    if age > HEARTBEAT_STALE_SECONDS:
        return DOWN, f"No heartbeat for {int(age // 60)} min"
    figures = beat.get("figures") or {}
    login = figures.get("saxo_login") or {}
    if login.get("renewal_failing_since"):
        return WARNING, (f"Saxo refuses to renew the login since {_clock(login['renewal_failing_since'], timezone_name)} "
                         f"({login.get('error')}): no new position")
    if figures.get("poll_failing_since"):
        return WARNING, (f"Cannot reach the web server since {_clock(figures['poll_failing_since'], timezone_name)}: "
                         f"{figures.get('poll_error')}")
    if figures.get("signals_silent"):
        return WARNING, f"No signal since {_clock(figures.get('last_signal_at'), timezone_name)}"
    detail = f"Polling the web server, last signal {_clock(figures.get('last_signal_at'), timezone_name)}"
    session = figures.get("saxo_session") or {}
    level = session.get("trade_level")
    if session.get("wanted") == "FullTradingAndChat" and level and level != "FullTradingAndChat":
        refused = f": {session['request_error']}" if session.get("request_error") else ""
        return WARNING, f"Saxo serves the session at {level}, delayed prices, though WATA asks for the full level{refused}"
    if level and level != "FullTradingAndChat":
        detail += f"; Saxo session at {level}"
    return OK, detail


def position_monitor_health(beat: dict, now: datetime, timezone_name: str) -> tuple[str, str]:
    """What the position monitor's heartbeat says of it, as (status, detail)."""
    age = _beat_age(beat, now)
    if age > HEARTBEAT_STALE_SECONDS:
        return DOWN, f"No heartbeat for {int(age // 60)} min"
    figures = beat.get("figures") or {}
    intents = figures.get("close_intents") or []
    if intents:
        first = intents[0]
        more = f", and {_count(len(intents) - 1, 'other position')}" if len(intents) > 1 else ""
        return WARNING, (f"Selling position {first['position_id']} since {_clock(first.get('since'), timezone_name)} "
                         f"({_count(first.get('attempts') or 0, 'failed attempt')}: {first.get('last_error')}){more}")
    if not figures.get("stream_connected"):
        return WARNING, "Not connected to Saxo's position stream"
    if figures.get("rest_fallback_since"):
        return WARNING, (f"The position stream is silent: positions checked through REST since "
                         f"{_clock(figures['rest_fallback_since'], timezone_name)}")
    listed = figures.get("positions_listed") or 0
    return OK, f"Streaming, {_count(listed, 'position')} at Saxo" if listed else "Streaming, no position at Saxo"


async def _heartbeats(ctx) -> dict[str, dict]:
    """The heartbeats; none when they cannot be read, which leaves each entry as it is said otherwise."""
    try:
        return await ctx.queries.service_heartbeats()
    except Exception:
        return {}


def with_heartbeats(services: list[dict], beats: dict[str, dict], now: datetime, timezone_name: str) -> list[dict]:
    """
    The trader's and the position monitor's entries as their heartbeat says,
    when they have one; a monitor without a consumer on its queue stays down
    (a close asked from the admin would be lost).
    """
    judged = {"trader": trader_health, "position_monitor": position_monitor_health}
    result = []
    for entry in services:
        judge, beat = judged.get(entry["id"]), beats.get(entry["id"])
        if judge is None or beat is None or (entry["id"] == "position_monitor" and entry["status"] == DOWN):
            result.append(entry)
            continue
        result.append(_entry(entry["id"], entry["name"], *judge(beat, now, timezone_name)))
    return result


def _overall(services: list[dict]) -> str:
    statuses = {service["status"] for service in services}
    for status in (DOWN, WARNING):
        if status in statuses:
            return status
    return OK


async def collect_health(ctx) -> dict:
    (
        postgres, web_server, (watchlist, watchlist_snapshot), (rabbitmq, queues), log_observed, saxo, price_recorder,
        capital_feed, ta_engine, macro_calendar, market_calendar, backups, beats,
    ) = await asyncio.gather(
        _postgres(ctx),
        _web_server(ctx),
        _watchlist(ctx),
        _rabbitmq(ctx),
        run_in_threadpool(_log_observed, ctx.settings.log_path),
        _saxo(ctx),
        _price_recorder(ctx),
        _capital_feed(ctx),
        _ta_engine(ctx),
        run_in_threadpool(_macro_calendar, ctx),
        run_in_threadpool(_market_calendar, ctx),
        run_in_threadpool(_backups, ctx),
        _heartbeats(ctx),
    )
    services = with_heartbeats([
        *saxo, *web_server, *log_observed, *rabbitmq[1:], *watchlist, *price_recorder, *capital_feed, *ta_engine,
        *macro_calendar, *market_calendar, *postgres, *backups, rabbitmq[0],
    ], beats, datetime.now(timezone.utc), ctx.settings.timezone)
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
