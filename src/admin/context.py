"""
Settings and shared dependencies of the admin application.

Routes reach everything outside the process (PostgreSQL, the other services,
the Saxo token files, Telegram) through the AdminContext, which is what tests
replace with fakes.
"""

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from fastapi import Request

from src.admin.auth import CredentialStore, LoginThrottle, SessionStore, TrustedProxies, client_ip
from src.admin.events import EventHub

logger = logging.getLogger(__name__)

NOTIFICATION_TIMEOUT_SECONDS = 5
# The reverse proxy in front of the admin: the Traefik container of docker-compose.yml.
DEFAULT_TRUSTED_PROXY_HOSTS = ("traefik",)


def read_version() -> str:
    try:
        version_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "VERSION")
        with open(version_file, "r") as file_handle:
            return file_handle.read().strip()
    except Exception:
        return "unknown"


def _as_host_list(value) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)) and all(isinstance(host, str) for host in value):
        return tuple(value)
    raise ValueError("Config field 'admin.trusted_proxy_hosts' must be a list of host names or addresses")


@dataclass
class AdminSettings:
    config_path: str
    log_path: str
    timezone: str
    api_host: str = "0.0.0.0"
    api_port: int = 8082
    credentials_path: str = "/app/var/lib/admin/credentials.json"
    # Backtests asked for from the interface: their results, and the prices they run on.
    backtests_enabled: bool = True
    backtests_path: str = "/app/var/lib/backtests"
    price_history_path: str = "/app/var/lib/price_history"
    session_idle_minutes: int = 60
    session_max_age_minutes: int = 720
    cookie_secure: bool = True
    actions_enabled: bool = True
    config_edit_enabled: bool = True
    # Whose X-Forwarded-For header says where a request comes from: host names,
    # addresses or networks. Anything else is known by its own address.
    trusted_proxy_hosts: tuple[str, ...] = DEFAULT_TRUSTED_PROXY_HOSTS

    @classmethod
    def from_config(cls, config_manager) -> "AdminSettings":
        admin_config = config_manager.get_config_value("admin", {})
        return cls(
            config_path=config_manager.config_path,
            log_path=config_manager.get_logging_config()["persistant"]["log_path"],
            timezone=config_manager.get_config_value("trade.config.general.timezone", "Europe/Paris"),
            api_host=admin_config.get("api_host", cls.api_host),
            api_port=admin_config.get("api_port", cls.api_port),
            credentials_path=admin_config.get("persistant", {}).get("credentials_path", cls.credentials_path),
            backtests_enabled=admin_config.get("backtests_enabled", cls.backtests_enabled),
            backtests_path=admin_config.get("persistant", {}).get("backtests_path", cls.backtests_path),
            price_history_path=admin_config.get("persistant", {}).get("price_history_path", cls.price_history_path),
            session_idle_minutes=admin_config.get("session_idle_minutes", cls.session_idle_minutes),
            session_max_age_minutes=admin_config.get("session_max_age_minutes", cls.session_max_age_minutes),
            cookie_secure=admin_config.get("cookie_secure", cls.cookie_secure),
            actions_enabled=admin_config.get("actions_enabled", cls.actions_enabled),
            config_edit_enabled=admin_config.get("config_edit_enabled", cls.config_edit_enabled),
            trusted_proxy_hosts=_as_host_list(admin_config.get("trusted_proxy_hosts", cls.trusted_proxy_hosts)),
        )


@dataclass
class AdminContext:
    config_manager: Any
    settings: AdminSettings
    credentials: CredentialStore
    sessions: SessionStore
    throttle: LoginThrottle
    queries: Any
    services: Any
    saxo: Any
    config_editor: Any
    # The one thing the admin writes to the database: the pause of new entries.
    # Through a connection of its own, the rest being read through a read-only pool.
    risk_state: Any = None
    audit_log: Any = None
    # Backtests run as child processes, and their results kept as files (src/admin/backtests.py).
    backtests: Any = None
    # The prices fetched from Dukascopy, read as files for the Data pages (src/admin/data.py).
    history: Any = None
    # The live prices of the Capital.com feed, for the charts that follow them (src/admin/live_prices.py).
    live_prices: Any = None
    # Nobody's X-Forwarded-For header is believed unless said otherwise.
    trusted_proxies: TrustedProxies = field(default_factory=TrustedProxies)
    events: EventHub = field(default_factory=EventHub)
    notifier: Callable[[str], Awaitable[None]] | None = None
    version: str = "unknown"
    started_at: float = field(default_factory=time.time)
    closers: list[Callable[[], Awaitable[None]]] = field(default_factory=list)
    _background_tasks: set = field(default_factory=set)

    async def _notify(self, message: str) -> None:
        try:
            await asyncio.wait_for(self.notifier(message), timeout=NOTIFICATION_TIMEOUT_SECONDS)
        except Exception as e:
            logger.warning(f"Unable to send the admin notification to Telegram: {e}")

    def audit(self, request: Request, action: str, detail: str = "") -> None:
        """Record an operator action in the admin log and announce it on Telegram."""
        ip = client_ip(request, self.trusted_proxies)
        logger.info(f"AUDIT ip={ip} action={action} {detail}".rstrip())
        if self.audit_log is not None:
            self.audit_log.append(ip, action, detail)
        if self.notifier is None:
            return
        lines = [f"🛠 WATA Admin: {action}", detail, f"From {ip}"]
        # In the background: a slow RabbitMQ must not hold up the operator's request.
        task = asyncio.create_task(self._notify("\n".join(line for line in lines if line)))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def close(self) -> None:
        for closer in self.closers:
            try:
                await closer()
            except Exception as e:
                logger.warning(f"Error while shutting down the admin: {e}")


def build_context(config_manager) -> AdminContext:
    """The context used in production, wired to the real services."""
    import httpx

    import json

    from src.admin.audit import AuditLog
    from src.admin.backtests import BacktestService
    from src.admin.config_editor import ConfigEditor
    from src.admin.data import PriceHistory
    from src.admin.live_prices import LivePrices, rabbitmq_source
    from src.admin.queries import AdminQueries, LazyDatabase
    from src.admin.services import SaxoAccess, WataServices
    from src.database.postgres import AsyncDbRiskStateManager, PostgresConnectionManager
    from src.mq_telegram.async_tools import AsyncTelegramSender
    from src.strategy.execution import WataRules
    from src.web_server_token import WebServerToken

    settings = AdminSettings.from_config(config_manager)
    events = EventHub()

    def live_rules() -> WataRules:
        # Read again each time: the configuration may have been edited since the admin started.
        with open(settings.config_path, encoding="utf-8") as config_file:
            return WataRules.from_config(json.load(config_file))

    backtests = BacktestService(
        runs_root=settings.backtests_path,
        prices_root=settings.price_history_path,
        rules=live_rules,
        publish=events.publish,
    )

    postgres_config = config_manager.get_config_value("postgresql", {})
    dsn = postgres_config.get("dsn", "postgresql://wata:wata@localhost:5432/wata")
    database = LazyDatabase(
        PostgresConnectionManager(
            dsn=dsn,
            # A pool of its own, small and read-only: the admin must neither
            # starve the trading services of connections nor write their tables.
            min_size=1,
            max_size=3,
            server_settings={"default_transaction_read_only": "on"},
        )
    )
    # Opened on first use and used for one statement at a time (pause, resume).
    pause_database = LazyDatabase(PostgresConnectionManager(dsn=dsn, min_size=0, max_size=1))
    http_client = httpx.AsyncClient(timeout=3.0)
    telegram_sender = AsyncTelegramSender(config_manager)
    rabbitmq = config_manager.get_rabbitmq_config()
    rabbitmq_url = (
        f"amqp://{rabbitmq['authentication']['username']}:{rabbitmq['authentication']['password']}@{rabbitmq['hostname']}/"
    )

    return AdminContext(
        config_manager=config_manager,
        settings=settings,
        credentials=CredentialStore(settings.credentials_path),
        sessions=SessionStore(
            idle_seconds=settings.session_idle_minutes * 60,
            max_age_seconds=settings.session_max_age_minutes * 60,
        ),
        throttle=LoginThrottle(),
        queries=AdminQueries(database, settings.timezone),
        services=WataServices(
            config_manager,
            http_client,
            web_server_token_getter=lambda: WebServerToken(config_manager).get_token(),
        ),
        saxo=SaxoAccess(config_manager),
        config_editor=ConfigEditor(settings.config_path),
        risk_state=AsyncDbRiskStateManager(pause_database),
        audit_log=AuditLog(os.path.join(settings.log_path, "wata-admin", "audit.jsonl")),
        backtests=backtests,
        history=PriceHistory(settings.price_history_path),
        live_prices=LivePrices(rabbitmq_source(rabbitmq_url)),
        trusted_proxies=TrustedProxies(settings.trusted_proxy_hosts),
        events=events,
        notifier=telegram_sender.send,
        version=read_version(),
        closers=[backtests.close, database.close, pause_database.close, http_client.aclose, telegram_sender.close],
    )
