"""
The catalog of config.json for the Configuration page (model.py says what an
entry holds), and what the page asks of it: the file read as a flat list of
parameters, the changes between two versions, and what a change reaches.
"""

from difflib import get_close_matches

from src.admin.config_catalog import feeds, system, trading
from src.admin.config_catalog.model import (
    ACCESSES, CREDENTIAL, EDITABLE, KINDS, NONE, RISKS, SERVER, SERVICE, TRADING, UNUSED,
    Catalog, Consumer, Group, Param, Service,
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
    Group("day", "Day targets and limits", "When the day is done: its target, its losses, its pauses and its trades."),
    Group("hours", "Market hours and calendar", "When WATA trades, closes, and stays out around US releases."),
    Group("exits", "Exits", "When the position monitor sells a position."),
    Group("sizing", "Size and turbo", "How much is bought, and which turbo."),
    Group("signals", "Signals", "Which signals are taken, and how the trader gets them."),
    Group("watchlist", "Watchlist manager", "The turbos kept ready for the next entry."),
    Group("saxo", "Saxo session and streaming", "How the services talk to Saxo."),
    Group("recorder", "Price recorder", "Saxo's prices recorded for backtests."),
    Group("capital", "Capital.com feed", "The Nasdaq 100 from Capital.com, for live charts and WATA's own signals."),
    Group("ta", "TA engine", "WATA's own signals from the Pine scripts of the signal rules."),
    Group("reporting", "Reporting and logs", "The daily report and what the services write in their logs."),
    Group("admin", "Admin and Claude", "This interface, its backtests, and what Claude may do through it."),
    Group("infrastructure", "Infrastructure", "Where the services connect and keep their files: set on the server."),
    Group("credentials", "Credentials", "The logins and secrets WATA uses, and where each is kept."),
)

CATALOG = Catalog(
    services=SERVICES,
    consumers=CONSUMERS,
    groups=GROUPS,
    params=trading.PARAMS + feeds.PARAMS + system.PARAMS,
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
