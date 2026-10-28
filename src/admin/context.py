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

from src.admin.agent.settings import AgentSettings
from src.admin.auth import CredentialStore, LoginThrottle, SessionStore, TrustedProxies, client_ip
from src.admin.events import EventHub
from src.admin.mcp_server.limits import McpLimits

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
    # One run at a time beside the trading services; a research machine (lab/) runs several, over longer periods.
    backtests_workers: int = 1
    backtests_max_queued: int = 10
    backtests_max_period_days: int = 92
    # The Pine runner's answers kept for the runs asked again (_signal_cache): pruned, oldest first, past this.
    backtests_signal_cache_mb: int = 1024
    price_history_path: str = "/app/var/lib/price_history"
    # The database's dumps (pg_backup1), only read: health says the last one.
    backups_path: str = "/app/var/lib/backups"
    # The Pine scripts and chart layouts of the Technical Analysis pages, inside the
    # admin's own folder: the signal engine mounts this subfolder alone, read-only.
    ta_path: str = "/app/var/lib/admin/ta"
    session_idle_minutes: int = 60
    session_max_age_minutes: int = 720
    cookie_secure: bool = True
    actions_enabled: bool = True
    config_edit_enabled: bool = True
    # Whose X-Forwarded-For header says where a request comes from: host names,
    # addresses or networks. Anything else is known by its own address.
    trusted_proxy_hosts: tuple[str, ...] = DEFAULT_TRUSTED_PROXY_HOSTS
    # The MCP endpoint for Claude (src/admin/mcp_server/): off unless asked for, and then
    # opened only with a token of this file (watamcptoken on the server).
    mcp_enabled: bool = False
    mcp_tokens_path: str = "/app/var/lib/admin/mcp_tokens.json"
    mcp_limits: McpLimits = field(default_factory=McpLimits)
    # The AI agent of the pages (src/admin/agent/): off unless asked for, its conversations kept as files
    # beside the scripts' folder (which the signal engine mounts alone).
    ai_agent: AgentSettings = field(default_factory=AgentSettings)
    agent_path: str = "/app/var/lib/admin/agent"

    @classmethod
    def from_config(cls, config_manager) -> "AdminSettings":
        admin_config = config_manager.get_config_value("admin", {})
        mcp_enabled = admin_config.get("mcp_enabled", cls.mcp_enabled)
        if not isinstance(mcp_enabled, bool):
            raise ValueError("Config field 'admin.mcp_enabled' must be true or false")
        return cls(
            config_path=config_manager.config_path,
            log_path=config_manager.get_logging_config()["persistant"]["log_path"],
            timezone=config_manager.get_config_value("trade.config.general.timezone", "Europe/Paris"),
            api_host=admin_config.get("api_host", cls.api_host),
            api_port=admin_config.get("api_port", cls.api_port),
            credentials_path=admin_config.get("persistant", {}).get("credentials_path", cls.credentials_path),
            backtests_enabled=admin_config.get("backtests_enabled", cls.backtests_enabled),
            backtests_workers=int(admin_config.get("backtests_workers", cls.backtests_workers)),
            backtests_max_queued=int(admin_config.get("backtests_max_queued", cls.backtests_max_queued)),
            backtests_max_period_days=int(admin_config.get("backtests_max_period_days", cls.backtests_max_period_days)),
            backtests_signal_cache_mb=int(admin_config.get("backtests_signal_cache_mb", cls.backtests_signal_cache_mb)),
            backtests_path=admin_config.get("persistant", {}).get("backtests_path", cls.backtests_path),
            price_history_path=admin_config.get("persistant", {}).get("price_history_path", cls.price_history_path),
            backups_path=admin_config.get("persistant", {}).get("backups_path", cls.backups_path),
            ta_path=admin_config.get("persistant", {}).get("ta_path", cls.ta_path),
            session_idle_minutes=admin_config.get("session_idle_minutes", cls.session_idle_minutes),
            session_max_age_minutes=admin_config.get("session_max_age_minutes", cls.session_max_age_minutes),
            cookie_secure=admin_config.get("cookie_secure", cls.cookie_secure),
            actions_enabled=admin_config.get("actions_enabled", cls.actions_enabled),
            config_edit_enabled=admin_config.get("config_edit_enabled", cls.config_edit_enabled),
            trusted_proxy_hosts=_as_host_list(admin_config.get("trusted_proxy_hosts", cls.trusted_proxy_hosts)),
            mcp_enabled=mcp_enabled,
            mcp_tokens_path=admin_config.get("persistant", {}).get("mcp_tokens_path", cls.mcp_tokens_path),
            mcp_limits=McpLimits.from_config(admin_config.get("mcp")),
            ai_agent=AgentSettings.from_config(admin_config.get("ai_agent")),
            agent_path=admin_config.get("persistant", {}).get("agent_path", cls.agent_path),
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
    # The downloads of Dukascopy's history and keeping it up to date (src/admin/downloads.py).
    downloads: Any = None
    # Each finished day written as files for a research machine (src/admin/research_export.py).
    research: Any = None
    # Each shadow rule's finished days traded on paper (src/admin/paper.py).
    paper: Any = None
    # The prices fetched from Dukascopy, read as files for the Data pages (src/admin/data.py).
    history: Any = None
    # The live prices of the Capital.com feed, for the charts that follow them (src/admin/live_prices.py).
    live_prices: Any = None
    # The Pine scripts of the Technical Analysis pages, kept as files (src/admin/ta_scripts.py).
    ta_scripts: Any = None
    # The tokens the MCP endpoint is opened with, and what was asked of it (src/admin/mcp_server/).
    mcp_tokens: Any = None
    mcp_calls: Any = None
    # The operator's pause of the endpoint, from the System page (src/admin/mcp_server/pause.py).
    mcp_pause: Any = None
    # Claude's Pine studies, child processes one at a time (src/admin/studies.py): with the endpoint on.
    studies: Any = None
    # The control service, which restarts WATA's containers for the admin (src/admin/service_control.py),
    # and the restarts asked of it, one plan at a time (src/admin/service_restart.py).
    service_control: Any = None
    restarts: Any = None
    # The credentials sealed in PostgreSQL (src/admin/vault.py): listed, set, rotated; never read back.
    vault: Any = None
    # The AI agent of the pages (src/admin/agent/service.py): with admin.ai_agent.enabled only.
    agent: Any = None
    # Its daily debrief (src/admin/agent_debrief.py): with the agent, acting only while debrief_enabled.
    debrief: Any = None
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


def agent_of(ctx):
    """
    The AI agent over the admin's services, given what it may read of the admin
    itself: its health, and its services' logs when admin.ai_agent.read_logs
    allows (never a service: src/admin/agent/ reaches none, tests/test_agent_boundary.py).
    """
    from functools import partial

    from src.admin.agent.service import build_agent
    from src.admin.agent.tools import OpsReads
    from src.admin.health import collect_health
    from src.admin.logs import read_log

    settings = ctx.settings

    async def health() -> dict:
        return await collect_health(ctx)

    logs = partial(read_log, settings.log_path) if settings.ai_agent.read_logs else None
    return build_agent(ctx, ops=OpsReads(health=health, logs=logs))


def build_context(config_manager) -> AdminContext:
    """The context used in production, wired to the real services."""
    import httpx

    from src.admin.audit import AuditLog
    from src.admin.backtests import BacktestService
    from src.admin.config_editor import ConfigEditor
    from src.admin.data import PriceHistory
    from src.admin.downloads import HistoryDownloads, config_hours
    from src.admin.live_prices import LivePrices, rabbitmq_source
    from src.admin.mcp_server.calls import CallLog
    from src.admin.mcp_server.pause import McpPause
    from src.admin.queries import AdminQueries, LazyDatabase
    from src.admin.paper import PaperTrading
    from src.admin.research_export import ResearchExport
    from src.admin.errors import ServiceUnavailable
    from src.admin.mcp_token_store import DbTokenStore
    from src.admin.service_control import ServiceControl
    from src.admin.vault import AdminVault
    from src.admin.ta_rules import RuleStore
    from src.admin.saxo import AdminSaxo
    from src.admin.services import WataServices
    from src.admin.ta_scripts import ScriptStore
    from src.database.postgres import AsyncDbRiskStateManager, PostgresConnectionManager
    from src.mq_telegram.async_tools import AsyncTelegramSender

    settings = AdminSettings.from_config(config_manager)
    events = EventHub()

    downloads = HistoryDownloads(
        prices_root=settings.price_history_path,
        hours=config_hours(settings.config_path),
        publish=events.publish,
        # The switch and the days settled, kept there by the former backtester.
        legacy_state=os.path.join(settings.backtests_path, "_fetch"),
    )
    ta_scripts = ScriptStore(settings.ta_path)
    backtests = BacktestService(
        root=settings.backtests_path,
        prices_root=settings.price_history_path,
        config_path=settings.config_path,
        scripts=ta_scripts,
        publish=events.publish,
        workers=settings.backtests_workers,
        max_queued=settings.backtests_max_queued,
        max_period_days=settings.backtests_max_period_days,
        signal_cache_bytes=settings.backtests_signal_cache_mb * 1024 * 1024,
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

    queries = AdminQueries(database, settings.timezone)
    version = read_version()
    service_control = ServiceControl()
    vault = AdminVault(reader=database, writer=pause_database, config_manager=config_manager,
                       feed_state=queries.capital_feed_state)
    mcp_tokens = DbTokenStore(reader=database, writer=pause_database, legacy_path=settings.mcp_tokens_path)

    async def internal_token() -> str:
        # Opened again at the first signal sent from the pages and after a refusal: rotated since.
        value = await vault.open("internal.token")
        if not value:
            raise ServiceUnavailable("web_server: no internal token is set (Credentials)")
        return value["token"]
    studies = None
    # Claude Code's endpoint and the AI agent run the same Pine studies.
    if settings.mcp_enabled or settings.ai_agent.enabled:
        from src.admin.backtests import SIGNAL_CACHE_FOLDER
        from src.admin.studies import StudyService

        studies = StudyService(
            root=os.path.join(settings.backtests_path, "_studies"), ta_root=settings.ta_path,
            prices_root=settings.price_history_path,
            # The runner's answers the backtests keep: a study and a run of the same days share them.
            signal_cache=os.path.join(settings.backtests_path, SIGNAL_CACHE_FOLDER),
            memory_mb=settings.mcp_limits.study_memory_mb, timeout_seconds=settings.mcp_limits.study_timeout_seconds,
            minutes_per_hour=settings.mcp_limits.study_minutes_per_hour,
        )

    ctx = AdminContext(
        config_manager=config_manager,
        settings=settings,
        credentials=CredentialStore(settings.credentials_path),
        sessions=SessionStore(
            idle_seconds=settings.session_idle_minutes * 60,
            max_age_seconds=settings.session_max_age_minutes * 60,
        ),
        throttle=LoginThrottle(),
        queries=queries,
        services=WataServices(
            config_manager,
            http_client,
            web_server_token_getter=internal_token,
        ),
        saxo=AdminSaxo(reader=database, writer=pause_database, config_manager=config_manager),
        config_editor=ConfigEditor(settings.config_path),
        risk_state=AsyncDbRiskStateManager(pause_database),
        audit_log=AuditLog(os.path.join(settings.log_path, "wata-admin", "audit.jsonl")),
        backtests=backtests,
        downloads=downloads,
        research=ResearchExport(
            prices_root=settings.price_history_path, queries=queries, config_path=settings.config_path, version=version,
        ),
        paper=PaperTrading(
            backtests=backtests, queries=queries, rules=RuleStore(ta_scripts),
            config_path=settings.config_path, notify=telegram_sender.send,
        ),
        history=PriceHistory(settings.price_history_path),
        live_prices=LivePrices(rabbitmq_source(rabbitmq_url)),
        ta_scripts=ta_scripts,
        mcp_tokens=mcp_tokens,
        mcp_calls=CallLog(os.path.join(settings.log_path, "wata-admin", "mcp.jsonl")),
        mcp_pause=McpPause(os.path.join(os.path.dirname(settings.mcp_tokens_path), "mcp_pause.json")),
        studies=studies,
        service_control=service_control,
        vault=vault,
        trusted_proxies=TrustedProxies(settings.trusted_proxy_hosts),
        events=events,
        notifier=telegram_sender.send,
        version=version,
        closers=[backtests.close, downloads.close, database.close, pause_database.close, http_client.aclose, telegram_sender.close,
                 service_control.close, *([studies.close] if studies is not None else [])],
    )
    if settings.ai_agent.enabled:
        from src.admin.agent_debrief import FILE, DailyDebrief

        ctx.agent = agent_of(ctx)
        ctx.closers.insert(0, ctx.agent.close)
        ctx.debrief = DailyDebrief(agent=ctx.agent, queries=queries, config_path=settings.config_path,
                                   path=os.path.join(settings.agent_path, FILE), notify=telegram_sender.send)
    return ctx
