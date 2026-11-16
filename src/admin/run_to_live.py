"""
A backtest's settings applied to WATA: what differs between the settings a run
traded with and those config.json gives now, and config.json with the ones the
operator chose (the run page's "Apply to WATA", routes in routers/backtests.py).

A run keeps every setting it traded with (settings.json's ``settings``), the
names it changed of the live ones of its day (``changed``) and their values
then (``live_before``). Each setting a backtest reads from config.json has its
``source`` there (``FIELDS`` of src/backtest/settings.py); those that are a
backtest's alone (the capital, the fills, the commissions) have none and are
only said. A run's raw values are read, never through ``from_dict``, which
gives a run made before a setting existed that setting's old default (a run of
before waited for no fresh quote, sent no order again for funds): those would
read as differences.

Most settings are one key of config.json, placed as the catalog names it. Four
are not: the window around US releases is the presence of the ``macro_news``
rule (switched on, the rule is added with the run's minutes and releases;
off, removed), its releases are one list (``events``), the hours' timezone of
the trading one is the absence of ``timezone``, and a rule a setting is kept
in that config.json does not have yet is added by the catalog's ``place``.

Pure, outside ``src/backtest`` and ``_CODE``: nothing here turns a run stale.
Nothing is saved here either: the route previews and saves through the
Configuration page's editor, with its validation, revision, backup and journal.
"""

import copy

from src.admin import config_catalog
from src.admin.errors import InvalidRequest
from src.backtest.settings import FIELDS, from_config, TRADING_TIMEZONE
from src.trade.macro_calendar import MACRO_EVENTS

RULES = config_catalog.RULES
NEWS_RULE = "macro_news"
# The eight releases, one boolean each in a run, one list in config.json, in MACRO_EVENTS' order.
NEWS_EVENTS = {
    "news_fomc": "FOMC", "news_cpi": "CPI", "news_nfp": "NFP", "news_pce": "PCE",
    "news_ppi": "PPI", "news_retail": "RETAIL", "news_gdp": "GDP", "news_jolts": "JOLTS",
}
assert tuple(NEWS_EVENTS.values()) == MACRO_EVENTS, "a release of the calendar has no setting of a run"
# The window's own settings, placed with it when it is switched on.
NEWS_MINUTES = ("news_minutes_before", "news_minutes_after", "news_minutes_after_fomc", "news_flatten_minutes_before")
# The rows of a run's settings that are not one of FIELDS.
WINDOW, EVENTS = "news_window", "news_events"
EVENTS_LABEL = "Releases the window is kept around"
TIMEZONE = "market_timezone"
ENGINE_NOTE = "Moves the engine day of every signal rule: each runs on its new history from the TA engine's next start"
RULE_LABEL = {"risk_management": "risk management", "macro_news": "US releases", "day_trading": "day trading",
              "market_hours": "market hours", "signal_validation": "signal validation"}


def _rule(config: dict, rule_type: str) -> dict | None:
    rules = ((config.get("trade") or {}).get("rules")) or []
    return next((rule for rule in rules if isinstance(rule, dict) and rule.get("rule_type") == rule_type), None)


def _rule_type(path: str) -> str | None:
    """The rule a catalog path is kept in (``trade.rules[<type>]...``), or None."""
    if not path.startswith(f"{RULES}["):
        return None
    return path[len(RULES) + 1:].partition("]")[0]


def _has_section(config: dict, path: str) -> bool:
    """Whether the object holding a key of ``trade.config`` is in config.json (a rule is added; a section is not)."""
    node = config
    for key in path.split(".")[:-1]:
        if not isinstance(node, dict) or key not in node:
            return False
        node = node[key]
    return isinstance(node, dict)


def _row(name: str, label: str, unit: str, group: str, before, after, *, own: bool, paths: list[str],
         why_not: str | None = None, note: str | None = None) -> dict:
    return {"setting": name, "label": label, "unit": unit, "group": group, "before": before, "after": after,
            "own": own, "paths": paths, "apply": why_not is None, "why_not": why_not, "note": note}


def _as_kept(param, value):
    """A whole number written as one where config.json keeps an integer (a run may hold 5.0)."""
    if param is not None and param.kind == "int" and isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _checked(entry, path: str, value, config: dict) -> tuple[str | None, str | None]:
    """Why the value cannot go where config.json keeps it (None when it can), and what applying it also does."""
    param = config_catalog.lookup(path)
    if param is None or param.access != config_catalog.EDITABLE:
        return "config.json keeps it where the admin does not change it", None
    refused = config_catalog.out_of_bounds(param, value)
    if refused:
        return f"{entry.label} is {refused}", None
    rule_type = _rule_type(path)
    if rule_type and _rule(config, rule_type) is None:
        return None, f"Adds the {RULE_LABEL.get(rule_type, rule_type)} rule to config.json"
    if not rule_type and not _has_section(config, path):
        return f"config.json has no {path.rsplit('.', 1)[0]} section to keep it in", None
    return None, None


def _events_of(settings: dict) -> list[str]:
    """The releases a run's settings watch, in MACRO_EVENTS' order (NEWS_EVENTS keeps it)."""
    return [event for name, event in NEWS_EVENTS.items() if settings.get(name)]


def _window_said(run: dict) -> str:
    events = ", ".join(_events_of(run)) or "no release"
    said = (f"Adds the rule: no entry from {run.get('news_minutes_before')} min before a release to "
            f"{run.get('news_minutes_after')} min after it ({run.get('news_minutes_after_fomc')} after an FOMC statement), "
            f"around {events}")
    flatten = run.get("news_flatten_minutes_before") or 0
    return said + (f"; what is held sold from {flatten} min before" if flatten else "")


def differences(run_file: dict, config: dict) -> dict:
    """What differs between a run's settings and config.json's now, row by row, and what only a backtest has.

    ``run_file`` is the run's settings.json whole; ``config`` config.json as the editor loads it. Each row says the
    live value (``before``), the run's (``after``), whether the run changed it itself (``own``: otherwise it was WATA's
    on the day the run was asked, changed since), where it goes, and whether it can be applied (``apply``, ``why_not``).
    """
    run = run_file.get("settings") or {}
    changed = run_file.get("changed") or {}
    live = from_config(config).to_dict()
    rows: list[dict] = []
    for entry in FIELDS:
        name = entry.name
        if not entry.source or name in NEWS_EVENTS or name == WINDOW or name not in run or name not in live:
            continue
        if name in NEWS_MINUTES and not (run.get(WINDOW) and live.get(WINDOW)):
            continue  # The window off on one side: its minutes go with the window's row.
        if run[name] == live[name]:
            continue
        path = config_catalog.catalog_path(entry.source)
        why_not, note = _checked(entry, path, run[name], config)
        if name == TIMEZONE and run[name] == TRADING_TIMEZONE:
            note = "The hours are read in the trading timezone again: config.json's key is removed"
        if entry.group == "engine":
            note = ENGINE_NOTE
        rows.append(_row(name, entry.label, entry.unit, entry.group, live[name], run[name], own=name in changed,
                         paths=[path], why_not=why_not, note=note))

    if WINDOW in run and WINDOW in live:
        window = next(entry for entry in FIELDS if entry.name == WINDOW)
        rule_path = f"{RULES}[{NEWS_RULE}]"
        if run[WINDOW] != live[WINDOW]:
            if run[WINDOW] and not _events_of(run):
                why_not, note = "The run watched no release: config.json's rule needs one at least", None
            else:
                why_not, note = None, (_window_said(run) if run[WINDOW] else "Removes the rule from config.json")
            own = WINDOW in changed or any(name in changed for name in (*NEWS_MINUTES, *NEWS_EVENTS))
            rows.append(_row(WINDOW, window.label, window.unit, window.group, live[WINDOW], run[WINDOW], own=own,
                             paths=[rule_path], why_not=why_not, note=note))
        elif run[WINDOW] and all(name in run for name in NEWS_EVENTS) and _events_of(run) != _events_of(live):
            events = _events_of(run)
            rows.append(_row(EVENTS, EVENTS_LABEL, "", window.group, _events_of(live), events,
                             own=any(name in changed for name in NEWS_EVENTS),
                             paths=[f"{rule_path}.rule_config.events"],
                             why_not=None if events else "config.json's rule needs one release at least"))

    sourced = {entry.name for entry in FIELDS if entry.source}
    only = [{"setting": entry.name, "label": entry.label, "unit": entry.unit, "value": changed[entry.name]}
            for entry in FIELDS if entry.name in changed and entry.name not in sourced]
    return {"differences": rows, "backtest_only": only}


def _remove_rule(config: dict, rule_type: str) -> None:
    rules = (config.get("trade") or {}).get("rules") or []
    config["trade"]["rules"] = [rule for rule in rules if not (isinstance(rule, dict) and rule.get("rule_type") == rule_type)]


def placed(run_file: dict, config: dict, chosen: list[str]) -> dict:
    """config.json (as the editor loads it) with the chosen rows of ``differences`` applied, in a copy.

    A name that is not a row of the differences now, or a row that cannot be applied, is refused: the page reviews
    what it shows, and config.json may have changed since it was read.
    """
    if not chosen:
        raise InvalidRequest("Choose at least one setting to apply")
    rows = {row["setting"]: row for row in differences(run_file, config)["differences"]}
    unknown = [name for name in chosen if name not in rows]
    if unknown:
        raise InvalidRequest(f"No longer different from WATA's settings, or not a setting: {', '.join(unknown)}")
    refused = [rows[name]["label"] for name in chosen if not rows[name]["apply"]]
    if refused:
        raise InvalidRequest(f"Cannot be applied: {', '.join(refused)}")

    run = run_file.get("settings") or {}
    config = copy.deepcopy(config)
    for name in dict.fromkeys(chosen):
        row = rows[name]
        if name == WINDOW:
            if row["after"]:
                for minutes in NEWS_MINUTES:
                    entry = next(field for field in FIELDS if field.name == minutes)
                    path = config_catalog.catalog_path(entry.source)
                    config_catalog.place(config, path, _as_kept(config_catalog.lookup(path), run[minutes]))
                config_catalog.place(config, f"{RULES}[{NEWS_RULE}].rule_config.events", _events_of(run))
            else:
                _remove_rule(config, NEWS_RULE)
        elif name == EVENTS:
            config_catalog.place(config, row["paths"][0], list(row["after"]))
        elif name == TIMEZONE and row["after"] == TRADING_TIMEZONE:
            market_hours = _rule(config, "market_hours")
            if market_hours is not None:
                (market_hours.get("rule_config") or {}).pop("timezone", None)
        else:
            path = row["paths"][0]
            config_catalog.place(config, path, _as_kept(config_catalog.lookup(path), row["after"]))
    return config


def said(rows: list[dict], chosen: list[str]) -> str:
    """The settings applied, as an audit entry names them: each by its label, from the live value to the run's."""
    by_name = {row["setting"]: row for row in rows}
    parts = []
    for name in dict.fromkeys(chosen):
        row = by_name.get(name)
        if row is not None:
            parts.append(f"{row['label']} {_value(row['before'])} → {_value(row['after'])}")
    return "; ".join(parts)


def _value(value) -> str:
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, list):
        return ", ".join(value) or "none"
    return str(value)
