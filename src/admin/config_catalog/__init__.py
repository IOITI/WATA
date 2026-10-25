"""
The catalog of config.json for the Configuration page (model.py says what an
entry holds), and what the page asks of it: the file read as a flat list of
parameters, the changes between two versions, and what a change reaches.
"""

from difflib import get_close_matches

from src.admin.config_catalog import feeds, system, trading
from src.admin.config_catalog.model import (
    ACCESSES, CREDENTIAL, EDITABLE, KINDS, NONE, RISKS, SECTIONS, SERVER, SERVICE, TRADING, UNUSED, UNUSED_BLOCK,
    Block, Catalog, Consumer, Group, Param, Service,
)

SERVICES = (
    Service("web_server", "Web server", "web_server1"),
    Service("trader", "Trader", "trader1"),
    Service("position_monitor", "Position monitor", "position_monitor1"),
    Service("scheduler", "Scheduler", "scheduler1"),
    Service("telegram", "Telegram", "telegram1"),
    Service("watchlist_manager", "Watchlist manager", "watchlist_manager1"),
    Service("admin", "Admin", "admin1", profile="admin"),
    Service("price_recorder", "Price recorder", "price_recorder1", profile="recorder"),
    Service("capital_feed", "Capital.com feed", "capital_feed1", profile="capital"),
    Service("ta_engine", "TA engine", "ta_engine1", profile="ta"),
)

CONSUMERS = (
    Consumer("dashboard", "The dashboard's limits and its Next entry tile", live=False),
    Consumer("backtests", "A new backtest's settings"),
    Consumer("paper", "Shadow rules' paper days"),
    Consumer("research_export", "The research export"),
    Consumer("downloads", "Dukascopy's keeper"),
)

GROUPS = (
    Group("day", "Day targets and limits", "When the day is done: its target, its losses, its pauses and its trades.",
          "Trading"),
    Group("hours", "Market hours and calendar", "When WATA trades, closes, and stays out around US releases.", "Trading"),
    Group("signals", "Signals", "Which signals are taken, and how the trader gets them.", "Trading"),
    Group("sizing", "Size and turbo", "How much is bought, and which turbo.", "Trading"),
    Group("exits", "Exits", "When the position monitor sells a position.", "Trading"),
    Group("watchlist", "Watchlist manager", "The turbos kept ready for the next entry.", "Trading"),
    Group("report", "Daily report", "What the daily report and the Report page project the account to.", "Analysis"),
    Group("backtests", "Backtests", "Strategies replayed from the admin: how many, how long, where they are kept.",
          "Analysis"),
    Group("saxo", "Saxo session and streaming", "How the services talk to Saxo.", "Data"),
    Group("recorder", "Price recorder", "Saxo's prices recorded for backtests.", "Data"),
    Group("capital", "Capital.com feed", "The Nasdaq 100 from Capital.com, for live charts and WATA's own signals.",
          "Data"),
    Group("ta", "TA engine", "WATA's own signals from the Pine scripts of the signal rules.", "Technical Analysis"),
    Group("admin", "Admin interface", "This interface: its sessions, its switches, where it listens and keeps files.",
          "Operations"),
    Group("claude", "Claude (MCP)", "What Claude may do through the admin's endpoint: runs, Pine studies, shadow rules.",
          "Operations"),
    Group("logs", "Logs", "What the services write in their logs, and where.", "Operations"),
    Group("infrastructure", "Infrastructure", "Where the services connect and keep their files: set on the server.",
          "Operations"),
    Group("credentials", "Credentials", "The logins and secrets WATA uses, and where each is kept.", "Operations"),
)

_POSITIONS = "trade.config.position_management"
_SIZING = "trade.config.position_sizing"
_WATCHLIST = "trade.config.watchlist_manager"
_RECORDER = "trade.config.price_recorder"
_CAPITAL = "trade.config.capital_feed"
_TA = "trade.config.ta_engine"
_MCP = "admin.mcp"
_SAXO_APP = "authentication.saxo.app_config_object"

# The headings of each group's page, in their order; a parameter goes under the first that holds its path, and a
# key nothing reads under "Read by nothing" (model.UNUSED_BLOCK). tests/test_config_catalog.py holds every
# parameter to one.
BLOCKS = (
    Block("day", "result", "The day's result", ("trade.rules[day_trading]",),
          "When the day's result stops new entries."),
    Block("day", "pace", "Pauses and number of trades", ("trade.rules[risk_management]",),
          "How often WATA may enter, whatever the day's result."),
    Block("hours", "day", "The trading day", (
        "trade.config.general.timezone", "trade.rules[market_hours]", "trade.rules[day_trading].rule_config",
        "trade.rules[*].rule_name"), "When entries are taken, and when what is held is sold."),
    Block("hours", "closed", "Closed days", ("trade.rules[market_closed_dates]",),
          "Days without trading, beside the US market's holidays (src/trade/data/us_market_calendar.csv)."),
    Block("hours", "news", "US releases", ("trade.rules[macro_news]",),
          "No entry around the releases that move the Nasdaq, and nothing held into them."),
    Block("signals", "traded", "What is traded", ("trade.rules[allowed_indices]", "trade.config.trading_mode")),
    Block("signals", "taken", "Which signals are taken", (
        "trade.rules[signal_validation]", "reporting.signal_silence_minutes"),
        "How old a signal may be, and when silence is said."),
    Block("signals", "polling", "How the trader gets them", ("trade.config.signal_polling",),
          "The trader asks the web server for the newest signals."),
    Block("sizing", "power", "Buying power", ("trade.config.buying_power",), "How much of the account an order uses."),
    Block("sizing", "time", "Size by time of day", (f"{_SIZING}.time_of_day_scaling",)),
    Block("sizing", "confidence", "Size by the signal's confidence", (f"{_SIZING}.confidence_scaling",)),
    Block("sizing", "turbo", "Which turbo", ("trade.config.turbo_preference",)),
    Block("sizing", "search", "Turbo search", ("trade.config.turbo_cache",)),
    Block("exits", "thresholds", "Stop-loss, take-profit and trailing stop", (f"{_POSITIONS}.performance_thresholds",),
          "Checked by the position monitor on each quote."),
    Block("exits", "saxo", "Stop placed at Saxo", (f"{_POSITIONS}.protective_stop",),
          "A stop order Saxo holds itself, should WATA not be there to sell."),
    Block("exits", "fresh", "A position just bought", (f"{_POSITIONS}.wait_for_fresh_quote",)),
    Block("watchlist", "kept", "What it keeps", (
        f"{_WATCHLIST}.enabled", f"{_WATCHLIST}.directions", f"{_WATCHLIST}.startup_refresh",
        f"{_WATCHLIST}.accept_stale")),
    Block("watchlist", "timing", "How often", (
        f"{_WATCHLIST}.request_timeout_seconds", f"{_WATCHLIST}.refresh_interval_seconds",
        f"{_WATCHLIST}.stale_after_seconds")),
    Block("watchlist", "where", "Where it runs", (
        f"{_WATCHLIST}.service_url", f"{_WATCHLIST}.api_host", f"{_WATCHLIST}.api_port")),
    Block("report", "milestones", "Milestones", ("reporting.milestones_eur",)),
    Block("backtests", "runs", "Runs", (
        "admin.backtests_enabled", "admin.backtests_max_queued", "admin.backtests_max_period_days",
        "admin.backtests_workers"), "How many runs, how long each may be, how many at once."),
    Block("backtests", "files", "Files and cache", (
        "admin.backtests_signal_cache_mb", "admin.persistant.backtests_path", "admin.persistant.price_history_path")),
    Block("saxo", "session", "Session", ("trade.config.saxo_session", "saxo_auth.env"),
          "The account WATA trades on, and the level Saxo serves it at."),
    Block("saxo", "stream", "Stream", ("trade.config.general.streaming",),
          "The positions and prices Saxo pushes to the services."),
    Block("saxo", "api", "Calls to Saxo's API", (
        "trade.config.general.keep_warm_interval_seconds", "trade.config.general.api_limits",
        "trade.config.general.retry_config")),
    Block("recorder", "what", "What is recorded", (
        f"{_RECORDER}.enabled", f"{_RECORDER}.instruments", f"{_RECORDER}.max_instruments", f"{_RECORDER}.record_volume")),
    Block("recorder", "timing", "How often", (f"{_RECORDER}.refresh_interval_seconds", f"{_RECORDER}.refresh_rate_ms")),
    Block("recorder", "kept", "Kept and watched", (f"{_RECORDER}.retention_months", f"{_RECORDER}.alert_after_seconds")),
    Block("capital", "feed", "Feed", (f"{_CAPITAL}.enabled", f"{_CAPITAL}.environment", f"{_CAPITAL}.instruments")),
    Block("capital", "kept", "Kept and watched", (
        f"{_CAPITAL}.minute_volume", f"{_CAPITAL}.day_files", f"{_CAPITAL}.retention_months",
        f"{_CAPITAL}.alert_after_seconds")),
    Block("ta", "engine", "Engine", (
        f"{_TA}.enabled", f"{_TA}.history_days", f"{_TA}.restart_time", f"{_TA}.monitoring_days")),
    Block("ta", "signals", "Signals sent", (
        f"{_TA}.max_signal_age_seconds", f"{_TA}.stale_feed_seconds", f"{_TA}.request_timeout_seconds"),
        "What a live rule's signal must be to reach the trader."),
    Block("ta", "runners", "Pine runners", (
        f"{_TA}.run_timeout_seconds", f"{_TA}.runner_memory_mb", f"{_TA}.shadow_runner_memory_mb",
        f"{_TA}.shadow_seconds_per_tick"), "The confined Node processes that run the scripts, live and shadow apart."),
    Block("admin", "sessions", "Sessions", (
        "admin.session_idle_minutes", "admin.session_max_age_minutes", "admin.cookie_secure")),
    Block("admin", "switches", "Switches", ("admin.actions_enabled", "admin.config_edit_enabled"),
          "What this interface may do: set on the server, so that the page cannot undo them."),
    Block("admin", "network", "Where it listens", ("admin.trusted_proxy_hosts", "admin.api_host", "admin.api_port")),
    Block("admin", "files", "Files", (
        "admin.persistant.credentials_path", "admin.persistant.backups_path", "admin.persistant.ta_path")),
    Block("claude", "endpoint", "Endpoint", ("admin.mcp_enabled", "admin.persistant.mcp_tokens_path")),
    Block("claude", "runs", "Runs", (
        f"{_MCP}.max_waiting_runs", f"{_MCP}.runs_per_day", f"{_MCP}.max_day_simulations", f"{_MCP}.min_free_disk_gb"),
        "Claude's backtests wait behind yours."),
    Block("claude", "studies", "Pine studies", (
        f"{_MCP}.study_minutes_per_hour", f"{_MCP}.study_memory_mb", f"{_MCP}.study_timeout_seconds",
        f"{_MCP}.max_scripts")),
    Block("claude", "shadow", "Shadow rules", (
        f"{_MCP}.shadow_rules", f"{_MCP}.max_shadow_rules", f"{_MCP}.shadow_day_ms"),
        "Claude's own signal rules: journaled, never sent."),
    Block("logs", "logs", "Logs", ("logging",)),
    Block("infrastructure", "postgresql", "PostgreSQL", ("postgresql",)),
    Block("infrastructure", "rabbitmq", "RabbitMQ", ("rabbitmq",)),
    Block("infrastructure", "files", "Token files", (
        "webserver.persistant.token_path", "authentication.persistant.token_path"),
        "Where the tokens were kept before PostgreSQL: read while a token has no row."),
    Block("credentials", "saxo", "Saxo's app in config.json", (_SAXO_APP,),
          "Its old place, read while the app has no row; its redirect addresses while SAXO_REDIRECT_URL is not set."),
    Block("credentials", "telegram", "Telegram's bot in config.json", ("telegram.bot_token", "telegram.chat_id"),
          "Its old place, read while the bot has no row."),
    Block("credentials", "webserver", "Web server", ("webserver.app_secret",),
          "The key of the token file of before."),
)

CATALOG = Catalog(
    services=SERVICES,
    consumers=CONSUMERS,
    groups=GROUPS,
    params=trading.PARAMS + feeds.PARAMS + system.PARAMS,
    blocks=BLOCKS,
)

RULES = "trade.rules"
# The kinds whose value is read whole: a list or an object the page edits as one field.
WHOLE_KINDS = ("map", "rows", "strings", "numbers", "dates", "json")
_RISK_ORDER = {TRADING: 2, SERVICE: 1, NONE: 0}


def lookup(path: str) -> Param | None:
    """The parameter at ``path``, the rule name of any rule included."""
    param = CATALOG.get(path)
    if param is None and path.startswith(f"{RULES}[") and path.endswith("].rule_name"):
        param = CATALOG.get(f"{RULES}[*].rule_name")
    return param


def flatten(config) -> dict[str, object]:
    """
    Each parameter of ``config`` with its value, by the catalog's paths. A rule
    is found by its type; a second rule of the same type, which the services
    never read, by its place (``trade.rules[3]``). A list, or an object the
    catalog reads whole, is one value.
    """
    leaves: dict[str, object] = {}

    def walk(node, path: str) -> None:
        param = lookup(path) if path else None
        if param is not None and param.kind in WHOLE_KINDS:
            leaves[path] = node
        elif path == RULES and isinstance(node, list):
            seen = set()
            for index, item in enumerate(node):
                rule_type = item.get("rule_type") if isinstance(item, dict) else None
                if not isinstance(rule_type, str) or rule_type in seen:
                    for key, value in (item.items() if isinstance(item, dict) else ()):
                        walk(value, f"{RULES}[{index}].{key}")
                    if not isinstance(item, dict):
                        leaves[f"{RULES}[{index}]"] = item
                    continue
                seen.add(rule_type)
                for key, value in item.items():
                    if key != "rule_type":
                        walk(value, f"{RULES}[{rule_type}].{key}")
        elif isinstance(node, dict) and node:
            for key, value in node.items():
                walk(value, f"{path}.{key}" if path else str(key))
        elif path:
            leaves[path] = node

    walk(config, "")
    return leaves


def place(config: dict, path: str, value) -> None:
    """Set ``value`` at the catalog's ``path`` in ``config``; a rule not there yet is added, named by its type."""
    if path.startswith(f"{RULES}["):
        rule_type, _, rest = path[len(RULES) + 1:].partition("].")
        rules = config.setdefault("trade", {}).setdefault("rules", [])
        rule = next((item for item in rules if isinstance(item, dict) and item.get("rule_type") == rule_type), None)
        if rule is None:
            rule = {"rule_type": rule_type, "rule_name": rule_type, "rule_config": {}}
            rules.append(rule)
        node, keys = rule, rest.split(".")
    else:
        node, keys = config, path.split(".")
    for key in keys[:-1]:
        node = node.setdefault(key, {})
    node[keys[-1]] = value


def unknown_paths(config) -> list[dict]:
    """The keys of ``config`` that no parameter of the catalog describes, each with the nearest known one."""
    known = list(CATALOG._by_path)
    unknown = []
    for path in flatten(config):
        if lookup(path) is None:
            nearest = get_close_matches(path, known, n=1, cutoff=0.8)
            unknown.append({"path": path, "nearest": nearest[0] if nearest else None})
    return unknown


def changes(before: dict, after: dict) -> list[dict]:
    """What differs between two versions of the file, parameter by parameter (both masked alike)."""
    old, new = flatten(before), flatten(after)
    found = []
    for path in sorted(set(old) | set(new)):
        if path in old and path in new and old[path] == new[path]:
            continue
        found.append({
            "path": path,
            "before": old.get(path),
            "after": new.get(path),
            "added": path not in old,
            "removed": path not in new,
        })
    return found


def impact(found: list[dict]) -> dict:
    """What ``changes`` reach: the services to start again, the pages and runs that read them, the highest risk."""
    readers, consumers, unknown = set(), set(), []
    risk = NONE
    for change in found:
        param = lookup(change["path"])
        if param is None:
            unknown.append(change["path"])
            continue
        readers.update(param.readers)
        consumers.update(param.consumers)
        if _RISK_ORDER[param.risk] > _RISK_ORDER[risk]:
            risk = param.risk
    return {
        "services": [service.id for service in SERVICES if service.id in readers],
        "consumers": [consumer.id for consumer in CONSUMERS if consumer.id in consumers],
        "risk": risk,
        "unknown": unknown,
    }


__all__ = [
    "ACCESSES", "CATALOG", "CONSUMERS", "CREDENTIAL", "EDITABLE", "GROUPS", "KINDS", "RISKS", "SERVER", "SERVICES",
    "UNUSED", "changes", "flatten", "impact", "lookup", "place", "unknown_paths",
]
