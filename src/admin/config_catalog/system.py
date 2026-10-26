"""Reporting and logs, the admin's own settings, the infrastructure, and the credentials still kept in the file."""

from src.admin.config_catalog.model import CREDENTIAL, NONE, SERVER, SERVICE, UNUSED, Param

ALL_SERVICES = (
    "web_server", "trader", "position_monitor", "scheduler", "telegram", "watchlist_manager",
    "admin", "price_recorder", "capital_feed", "ta_engine",
)
ADMIN = "admin"
MCP = "admin.mcp"

ON_THE_SERVER = "Edit /app/wata/etc/config.json on the server, then restart the services that read it."
INFRASTRUCTURE = ("The services connect with it before they read anything else: it is set at install, beside "
                  "/app/wata/.env, and changed on the server.")
SECRET_IN_FILE = ("A credential, kept in config.json until it moves to PostgreSQL. Shown masked, and never sent to "
                  "the page.")
OLD_PLACE = ("The old place of a credential now sealed in PostgreSQL (Credentials, above): read only while that has "
             "no row, and cleared by the migration's purge. Shown masked, and never sent to the page.")
SET_ABOVE = "Set it in the Credentials card above: the services read it from PostgreSQL."
KILL_SWITCH = ("A switch the operator sets on the server to restrict the admin itself: the page that it restricts must "
               "not be able to undo it.")
WHERE_FILES_ARE = "Where the admin keeps its files, inside the folders its container mounts."

REPORTING = (
    Param(
        path="reporting.milestones_eur", group="report", label="Milestones", kind="numbers", unit="€",
        optional=True, default=[100000, 500000, 1000000, 5000000, 10000000],
        help="The balances the daily report and the Report page project the account to reach.",
        readers=("trader", "position_monitor", ADMIN), risk=NONE,
    ),
    Param(
        path="reporting.telegram_commands", group="report", label="Answer the bot's commands", kind="bool",
        optional=True, default=False,
        help="The bot answers /status, /positions, /day, /signals and /help in its chat. It only reads: nothing it "
             "answers pauses, resumes, closes, restarts or changes anything.",
        readers=("telegram",), risk=NONE,
        effects=("The telegram service reads the bot's updates (getUpdates) beside the queue's messages, and answers "
                 "each command from a read-only connection to PostgreSQL.",
                 "Only the chat the bot sends to is answered: a message from any other is passed over without a word.",
                 "A command older than 2 minutes, sent while the service was stopped, is not answered, nor those over "
                 "6 a minute."),
        caveats=("Another program reading the same bot's updates, or a webhook set on the bot, keeps the commands "
                 "from being read: the telegram service says so in its log.",),
    ),
    Param(
        path="logging.level", group="logs", label="Log level", kind="enum",
        choices=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
        help="What every service writes in its log: DEBUG writes the most.",
        readers=ALL_SERVICES, risk=NONE,
        caveats=("The HTTP and WebSocket libraries stay at WARNING whatever it says: they name whole addresses.",),
    ),
    Param(
        path="logging.format", group="logs", label="Log line format", kind="text", access=SERVER, optional=True,
        default="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
        reason="The admin's Logs page reads the lines in this format: another would show them as unknown.",
        how=ON_THE_SERVER, help="Python's format of each log line.", readers=ALL_SERVICES,
    ),
    Param(
        path="logging.persistant.log_path", group="logs", label="Folder of the logs", kind="text", access=SERVER,
        reason="It is the folder the containers mount for their logs.", how=ON_THE_SERVER,
        help="Each service writes <folder>/<service>/<service>.log.", readers=ALL_SERVICES,
    ),
    Param(
        path="telegram.bot_name", group="logs", label="Bot's name", kind="text", access=UNUSED,
        reason="Required by the validator beside the bot's token, but nothing reads it.",
        help="The name of WATA's Telegram bot.",
    ),
)

# The admin's folders, each with the group of what it keeps.
PERSISTANT_GROUPS = {"mcp_tokens_path": "claude", "backtests_path": "backtests", "price_history_path": "backtests"}

ADMIN_PARAMS = (
    Param(
        path="admin.session_idle_minutes", group="admin", label="Signed out after idle", kind="int", unit="min",
        minimum=5, maximum=1440, optional=True, default=60,
        help="A session left without a request for this long is closed.",
        readers=(ADMIN,), risk=NONE,
    ),
    Param(
        path="admin.session_max_age_minutes", group="admin", label="Signed out after at most", kind="int", unit="min",
        minimum=15, maximum=10080, optional=True, default=720,
        help="A session is closed this long after it was opened, whatever its use.",
        readers=(ADMIN,), risk=NONE,
        related=("admin.session_idle_minutes",),
    ),
    Param(
        path="admin.backtests_enabled", group="backtests", label="Backtests", kind="bool", optional=True, default=True,
        help="Backtests, Dukascopy's downloads, the research export and paper days.",
        readers=(ADMIN,), risk=NONE,
    ),
    Param(
        path="admin.backtests_max_queued", group="backtests", label="Backtests waiting at most", kind="int", minimum=1,
        maximum=500, optional=True, default=10,
        help="Runs asked for beyond this are refused until one is done.",
        readers=(ADMIN,), risk=NONE,
    ),
    Param(
        path="admin.backtests_max_period_days", group="backtests", label="Days of a backtest at most", kind="int",
        unit="days", minimum=1, maximum=731, optional=True, default=92,
        help="The longest period a run may cover (731 on made-up turbos).",
        readers=(ADMIN,), risk=NONE,
    ),
    Param(
        path="admin.backtests_signal_cache_mb", group="backtests", label="Pine answers kept up to", kind="int", unit="MB",
        minimum=64, maximum=8192, optional=True, default=1024,
        help="The Pine runner's answers kept for runs asked again, pruned oldest first past this.",
        readers=(ADMIN,), risk=NONE,
    ),
    Param(
        path="admin.mcp_enabled", group="claude", label="Claude's endpoint (MCP)", kind="bool", optional=True,
        default=False,
        help="Opens /mcp on the admin's address, for Claude Code on your Mac with a token.",
        readers=(ADMIN,), risk=NONE,
        effects=("Claude can then run backtests and Pine studies inside the limits below; nothing it does trades.",),
        also_needs=("A token (watamcptoken on the server).",),
    ),
    Param(
        path=f"{MCP}.shadow_rules", group="claude", label="Claude's shadow rules", kind="bool", optional=True,
        default=False,
        help="Claude may keep signal rules in shadow: the TA engine runs them and journals their signals, never sends "
             "them.",
        readers=(ADMIN,), risk=NONE,
    ),
    Param(
        path=f"{MCP}.max_waiting_runs", group="claude", label="Claude's runs waiting at most", kind="int", minimum=1,
        maximum=10, optional=True, default=3,
        help="Your runs and the nightly paper runs always find room.", readers=(ADMIN,), risk=NONE,
    ),
    Param(
        path=f"{MCP}.runs_per_day", group="claude", label="Claude's runs a day", kind="int", minimum=1, maximum=1000,
        optional=True, default=60, help="Runs and sweeps Claude may start in a day (UTC).", readers=(ADMIN,), risk=NONE,
    ),
    Param(
        path=f"{MCP}.max_day_simulations", group="claude", label="Days simulated by one of Claude's requests",
        kind="int", minimum=100, maximum=1_000_000, optional=True, default=10_000,
        help="Days × the combinations of a sweep, or 1 + the random repetitions.", readers=(ADMIN,), risk=NONE,
    ),
    Param(
        path=f"{MCP}.study_minutes_per_hour", group="claude", label="Pine study minutes an hour", kind="int",
        unit="min", minimum=1, maximum=60, optional=True, default=20,
        help="Minutes of Pine studies Claude may use in any hour.", readers=(ADMIN,), risk=NONE,
    ),
    Param(
        path=f"{MCP}.study_memory_mb", group="claude", label="A study's heap", kind="int", unit="MB", minimum=128,
        maximum=1024, optional=True, default=768,
        help="The Node heap of one of Claude's Pine studies, beside a backtest in the admin's 3 GB.",
        readers=(ADMIN,), risk=NONE,
    ),
    Param(
        path=f"{MCP}.study_timeout_seconds", group="claude", label="A study may last", kind="int", unit="s", minimum=10,
        maximum=300, optional=True, default=90, help="Longer, the study is stopped.", readers=(ADMIN,), risk=NONE,
    ),
    Param(
        path=f"{MCP}.max_scripts", group="claude", label="Claude's scripts at most", kind="int", minimum=1, maximum=500,
        optional=True, default=100, help="Pine scripts Claude keeps at once.", readers=(ADMIN,), risk=NONE,
    ),
    Param(
        path=f"{MCP}.max_shadow_rules", group="claude", label="Claude's shadow rules at most", kind="int", minimum=0,
        maximum=10, optional=True, default=3, help="Shadow rules Claude keeps at once.", readers=(ADMIN,), risk=NONE,
    ),
    Param(
        path=f"{MCP}.shadow_day_ms", group="claude", label="Cost of a shadow rule's day at most", kind="int", unit="ms",
        minimum=500, maximum=10_000, optional=True, default=2000,
        help="A rule of Claude's whose engine day costs more is refused: the engine runs it at each new bar.",
        readers=(ADMIN,), risk=NONE,
    ),
    Param(
        path=f"{MCP}.min_free_disk_gb", group="claude", label="Free disk needed", kind="int", unit="GB", minimum=1,
        maximum=1000, optional=True, default=5,
        help="Nothing of Claude's is started when the disk has less free.", readers=(ADMIN,), risk=NONE,
    ),
    Param(
        path="admin.actions_enabled", group="admin", label="Operator actions", kind="bool", access=SERVER,
        optional=True, default=True, reason=KILL_SWITCH, how=ON_THE_SERVER,
        help="Closing a position, sending a signal, pausing entries, authorising Saxo from the page.",
        readers=(ADMIN,),
    ),
    Param(
        path="admin.config_edit_enabled", group="admin", label="Editing the configuration", kind="bool", access=SERVER,
        optional=True, default=True, reason=KILL_SWITCH, how=ON_THE_SERVER,
        help="Saving this page, restoring a version, and the Data pages' settings.", readers=(ADMIN,),
    ),
    Param(
        path="admin.trusted_proxy_hosts", group="admin", label="Proxies believed", kind="strings", access=SERVER,
        optional=True, default=["traefik"],
        reason="Whose X-Forwarded-For says where a request comes from: changed from the page, it could hide who "
               "signs in from the lockout and the audit trail.",
        how=ON_THE_SERVER, help="Host names or addresses.", readers=(ADMIN,),
    ),
    Param(
        path="admin.cookie_secure", group="admin", label="Secure session cookie", kind="bool", access=SERVER,
        optional=True, default=True, reason="Off, the session cookie would be sent over plain HTTP.",
        how=ON_THE_SERVER, help="Only for a local admin without HTTPS (the lab).", readers=(ADMIN,),
    ),
    Param(
        path="admin.api_host", group="admin", label="Admin listens on", kind="text", access=SERVER, optional=True,
        default="0.0.0.0", reason="Traefik reaches the admin there.", how=ON_THE_SERVER,
        help="The address the admin listens on in its container.", readers=(ADMIN,),
    ),
    Param(
        path="admin.api_port", group="admin", label="Admin's port", kind="int", access=SERVER, optional=True,
        default=8082, reason="Traefik reaches the admin there.", how=ON_THE_SERVER,
        help="The port the admin listens on in its container.", readers=(ADMIN,),
    ),
    Param(
        path="admin.backtests_workers", group="backtests", label="Backtests at once", kind="int", access=SERVER,
        optional=True, default=1, reason="Sized with the admin container's memory and processor (3 GB, one processor).",
        how=ON_THE_SERVER, help="Runs at the same time.", readers=(ADMIN,),
    ),
) + tuple(
    Param(
        path=f"admin.persistant.{key}", group=PERSISTANT_GROUPS.get(key, "admin"), label=label, kind="text",
        access=SERVER, optional=True,
        default=default, reason=WHERE_FILES_ARE, how=ON_THE_SERVER, help=help_text, readers=readers,
    )
    for key, label, default, help_text, readers in (
        ("credentials_path", "Admin's password file", "/app/var/lib/admin/credentials.json",
         "Your password's hash and TOTP key.", (ADMIN,)),
        ("mcp_tokens_path", "Claude's tokens file", "/app/var/lib/admin/mcp_tokens.json",
         "The sha256 of each MCP token.", (ADMIN,)),
        ("backtests_path", "Backtests' folder", "/app/var/lib/backtests", "The runs and their results.", (ADMIN,)),
        ("price_history_path", "Downloaded prices' folder", "/app/var/lib/price_history",
         "Dukascopy's and Capital.com's day files.", (ADMIN, "capital_feed")),
        ("backups_path", "Database dumps' folder", "/app/var/lib/backups", "Read only, for health.", (ADMIN,)),
        ("ta_path", "Pine scripts' folder", "/app/var/lib/admin/ta", "The scripts and the signal rules.", (ADMIN,)),
    )
)

INFRASTRUCTURE_PARAMS = (
    Param(
        path="postgresql.dsn", group="infrastructure", label="Database address", kind="secret", access=SERVER,
        reason=INFRASTRUCTURE, how=ON_THE_SERVER,
        help="The PostgreSQL address with its user and password (POSTGRES_PASSWORD of /app/wata/.env).",
        readers=ALL_SERVICES, optional=True, default="postgresql://wata:wata@localhost:5432/wata",
    ),
    Param(
        path="postgresql.pool_min_size", group="infrastructure", label="Database connections kept", kind="int",
        access=SERVER, reason=INFRASTRUCTURE, how=ON_THE_SERVER, optional=True, default=2,
        help="The trader's and the position monitor's pools; the others keep small fixed ones.",
        readers=("trader", "position_monitor"),
    ),
    Param(
        path="postgresql.pool_max_size", group="infrastructure", label="Database connections at most", kind="int",
        access=SERVER, reason=INFRASTRUCTURE, how=ON_THE_SERVER, optional=True, default=10,
        help="The trader's and the position monitor's pools.", readers=("trader", "position_monitor"),
    ),
    Param(
        path="rabbitmq.hostname", group="infrastructure", label="RabbitMQ's host", kind="text", access=SERVER,
        reason=INFRASTRUCTURE, how=ON_THE_SERVER, help="Where the services reach the queues.",
        readers=("trader", "position_monitor", "scheduler", "telegram", ADMIN, "price_recorder", "capital_feed",
                 "ta_engine"),
    ),
    Param(
        path="rabbitmq.authentication.username", group="infrastructure", label="RabbitMQ's user", kind="text",
        access=SERVER, reason=INFRASTRUCTURE, how=ON_THE_SERVER, help="The broker's user, trade-app.",
        readers=("trader", "position_monitor", "scheduler", "telegram", ADMIN, "price_recorder", "capital_feed",
                 "ta_engine"),
    ),
    Param(
        path="rabbitmq.authentication.password", group="infrastructure", label="RabbitMQ's password", kind="secret",
        access=SERVER,
        reason="Written into config.json at each start by the setup service, from RABBITMQ_PASSWORD of /app/wata/.env, "
               "which the broker read when it was first created.",
        how="Set RABBITMQ_PASSWORD in /app/wata/.env, give the broker's user trade-app the same password "
            "(rabbitmqctl change_password), then watastart: the setup service writes it here.",
        help="The broker's password.",
        readers=("trader", "position_monitor", "scheduler", "telegram", ADMIN, "price_recorder", "capital_feed",
                 "ta_engine"),
    ),
    Param(
        path="webserver.persistant.token_path", group="infrastructure", label="Webhook token's file", kind="text",
        access=SERVER, reason="A file of the folders the services mount.", how=ON_THE_SERVER,
        help="The webhook and internal token, encrypted.", readers=("web_server", "trader", "scheduler", "ta_engine", ADMIN),
    ),
    Param(
        path="authentication.persistant.token_path", group="infrastructure", label="Saxo token's file", kind="text",
        access=SERVER, reason="A file of the folders the services mount.", how=ON_THE_SERVER,
        help="Saxo's tokens, encrypted, beside the lock the services renew them under.",
        readers=("trader", "position_monitor", "watchlist_manager", "price_recorder", ADMIN),
    ),
    Param(
        path="trade.persistant.last_action_file", group="infrastructure", label="Last action file", kind="text",
        access=UNUSED, reason="Read by the scheduler into a variable only commented-out code used.",
        help="Left from an earlier WATA.",
    ),
    Param(
        path="duckdb.persistant.db_path", group="infrastructure", label="DuckDB file", kind="text", access=UNUSED,
        reason="PostgreSQL replaced DuckDB in v0.7.0: only the one-time migration (src/database/migration.py) reads it.",
        help="The database of WATA before v0.7.0.",
    ),
)

SAXO_APP = "authentication.saxo.app_config_object"
CREDENTIAL_PARAMS = (
    Param(
        path=f"{SAXO_APP}.AppKey", group="credentials", label="Saxo app's key (old place)", kind="secret",
        access=CREDENTIAL, reason=OLD_PLACE, how=SET_ABOVE,
        help="The app WATA logs in to Saxo with, from Saxo's developer portal: now the credential Saxo live (or "
             "simulation) app.",
        readers=("trader", "position_monitor", "watchlist_manager", "price_recorder"),
    ),
    Param(
        path=f"{SAXO_APP}.AppSecret", group="credentials", label="Saxo app's secret (old place)", kind="secret",
        access=CREDENTIAL, reason=OLD_PLACE, how=SET_ABOVE,
        help="The app's secret: now part of the credential Saxo live (or simulation) app. The token file of before is "
             "encrypted with it, read while the tokens have no row in PostgreSQL.",
        readers=("trader", "position_monitor", "watchlist_manager", "price_recorder"),
    ),
    Param(
        path=f"{SAXO_APP}.AuthorizationEndpoint", group="credentials", label="Saxo's login page", kind="text",
        access=SERVER, reason="Read only while the tokens are kept in their file of before: WATA otherwise uses "
                              "Saxo's own address for the environment (src/saxo_authen/endpoints.py).",
        how=ON_THE_SERVER, help="live.logonvalidation.net for the live account, sim.logonvalidation.net for the simulation.",
        readers=("trader", "position_monitor", "watchlist_manager", "price_recorder"),
    ),
    Param(
        path=f"{SAXO_APP}.TokenEndpoint", group="credentials", label="Saxo's token address", kind="text", access=SERVER,
        reason="Read only while the tokens are kept in their file of before: WATA otherwise uses Saxo's own address "
               "for the environment (src/saxo_authen/endpoints.py).",
        how=ON_THE_SERVER, help="Of the same environment as the login page.",
        readers=("trader", "position_monitor", "watchlist_manager", "price_recorder"),
    ),
    Param(
        path=f"{SAXO_APP}.RedirectUrls", group="credentials", label="Saxo app's redirect addresses", kind="strings",
        access=SERVER, reason="The first is used when SAXO_REDIRECT_URL is not set in .env.", how=ON_THE_SERVER,
        help="Where Saxo sends the browser back with the code after a login, as registered in the app.",
        readers=("trader", "position_monitor", "watchlist_manager", "price_recorder", ADMIN),
    ),
    Param(
        path=f"{SAXO_APP}.AppName", group="credentials", label="Saxo app's name", kind="text", access=UNUSED,
        reason="Nothing reads it.", help="The app's name in Saxo's developer portal.",
    ),
    Param(
        path=f"{SAXO_APP}.GrantType", group="credentials", label="Saxo app's grant type", kind="text", access=UNUSED,
        reason="WATA always uses the code grant.", help="Code.",
    ),
    Param(
        path=f"{SAXO_APP}.OpenApiBaseUrl", group="credentials", label="Saxo's API address", kind="text", access=UNUSED,
        reason="The services take Saxo's addresses from its environment (saxo_auth.env).",
        help="gateway.saxobank.com/openapi or /sim/openapi.",
    ),
    Param(
        path="webserver.app_secret", group="credentials", label="Token file's key", kind="secret",
        access=CREDENTIAL, reason=OLD_PLACE, how=SET_ABOVE,
        help="The token file of before (the webhook's and internal token until v0.43.0) is encrypted with it, read "
             "while the two tokens have no row.",
        readers=("web_server", "trader", "scheduler", "ta_engine", ADMIN),
    ),
    Param(
        path="telegram.bot_token", group="credentials", label="Telegram bot's token (old place)", kind="secret",
        access=CREDENTIAL, reason=OLD_PLACE, how=SET_ABOVE,
        help="The bot WATA's messages are sent with, from Telegram's BotFather: now the credential Telegram bot.",
        readers=("telegram", "trader", "position_monitor", "price_recorder", "capital_feed", "ta_engine", ADMIN),
    ),
    Param(
        path="telegram.chat_id", group="credentials", label="Telegram chat (old place)", kind="secret", access=CREDENTIAL,
        reason=OLD_PLACE, how=SET_ABOVE, help="The chat the messages go to: now part of the credential Telegram bot.",
        readers=("telegram", "trader", "position_monitor", "price_recorder", "capital_feed", "ta_engine", ADMIN),
    ),
)

PARAMS = REPORTING + ADMIN_PARAMS + INFRASTRUCTURE_PARAMS + CREDENTIAL_PARAMS
