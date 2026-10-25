"""
The catalog of the Configuration page (src/admin/config_catalog/) against the
code and the files: every key described, nothing described that nothing
reads, what the page may change being what the editor takes, and the bounds
it offers accepted by the validators.
"""

import copy
import dataclasses
import json
import pathlib
import re

import pytest

from src.admin.config_catalog import (
    ACCESSES, CATALOG, CONSUMERS, GROUPS, KINDS, RISKS, SERVICES, changes, flatten, impact, lookup, place,
    unknown_paths,
)
from src.admin.config_catalog.checks import warnings
from src.admin.config_catalog.model import CREDENTIAL, EDITABLE, SECTIONS, SERVER, UNUSED, UNUSED_BLOCK
from src.admin.config_catalog.trading import CLOSE_TIME, END_HOUR, RISKY_HOUR, START_HOUR
from src.admin.config_editor import EDITABLE_PATHS, PROTECTED_PATHS, SECRET_PATHS, check_admin_limits
from src.admin.context import AdminSettings
from src.admin.mcp_server.limits import McpLimits
from src.capital_feed.settings import CapitalSettings
from src.configuration import ConfigurationManager
from src.price_recorder.settings import RecorderSettings
from src.ta_engine.settings import TaEngineSettings
from src.trade.macro_calendar import NewsRule

with open("etc/config_example.json") as example_file:
    EXAMPLE = json.load(example_file)

SOURCE = "\n".join(
    path.read_text() for path in pathlib.Path("src").rglob("*.py") if "config_catalog" not in path.parts
)


def test_every_key_of_the_example_is_described():
    assert unknown_paths(EXAMPLE) == []


def test_every_setting_the_sections_check_themselves_is_described():
    sections = (
        (RecorderSettings, "trade.config.price_recorder",
         {"timezone", "trading_start_hour", "trading_end_hour", "market_closed_dates", "market_timezone"}),
        (CapitalSettings, "trade.config.capital_feed", {"timezone", "prices_root"}),
        (TaEngineSettings, "trade.config.ta_engine", {"rules_path", "node_path", "service_url"}),
        (McpLimits, "admin.mcp", set()),
        (NewsRule, "trade.rules[macro_news].rule_config", set()),
    )
    expected = {
        f"{section}.{field.name}"
        for settings, section, derived in sections
        for field in dataclasses.fields(settings)
        if field.name not in derived
    }

    assert sorted(path for path in expected if lookup(path) is None) == []


def test_every_setting_of_the_admin_is_described():
    elsewhere = {"config_path", "log_path", "timezone", "mcp_limits"}
    in_persistant = {"credentials_path", "backtests_path", "price_history_path", "backups_path", "ta_path",
                     "mcp_tokens_path"}
    expected = {
        f"admin.persistant.{field.name}" if field.name in in_persistant else f"admin.{field.name}"
        for field in dataclasses.fields(AdminSettings)
        if field.name not in elsewhere
    }

    assert sorted(path for path in expected if lookup(path) is None) == []


def test_every_path_the_code_reads_is_described():
    described = [param.path for param in CATALOG.params]
    read = set(re.findall(r'get_config_value\(\s*"([^"]+)"', SOURCE))
    # A section read whole is described by its keys.
    undescribed = sorted(
        path for path in read
        if lookup(path) is None and not any(known.startswith(f"{path}.") or known.startswith(f"{path}[") for known in described)
    )

    assert undescribed == []


def test_each_entry_is_in_the_example_or_read_by_the_code():
    in_example = set(flatten(EXAMPLE))
    stale = []
    for param in CATALOG.params:
        if param.path in in_example or param.kind == "rule" or "[*]" in param.path:
            continue
        key = re.split(r"[.\]]", param.path)[-1]
        # Absent from the example: the code must read it, and the services work without it.
        if not re.search(rf'["\.]{re.escape(key)}"', SOURCE) or not param.optional:
            stale.append(param.path)

    assert stale == []


def test_the_entries_are_well_formed():
    services, consumers, groups = {s.id for s in SERVICES}, {c.id for c in CONSUMERS}, {g.id for g in GROUPS}
    paths = [param.path for param in CATALOG.params]

    assert len(paths) == len(set(paths))
    for param in CATALOG.params:
        assert param.kind in KINDS and param.access in ACCESSES and param.risk in RISKS, param.path
        assert param.group in groups and param.label and param.help, param.path
        assert set(param.readers) <= services and set(param.consumers) <= consumers, param.path
        assert all(lookup(path) is not None for path in param.related), param.path
        if param.access != EDITABLE:
            assert param.reason, param.path
        if param.access in (SERVER, CREDENTIAL):
            assert param.how, param.path
        if param.kind == "rows":
            assert param.columns, param.path
        if param.kind == "rule" and param.optional:
            assert param.rule_default is not None, param.path
        if param.kind == "enum":
            assert param.choices, param.path
        if param.kind == "map":
            assert param.key_label and param.value_label, param.path
        if param.minimum is not None and param.maximum is not None:
            assert param.minimum <= param.maximum, param.path
    # Each group holds something, each service is read by something.
    assert {param.group for param in CATALOG.params} == groups
    assert {reader for param in CATALOG.params for reader in param.readers} == services


def test_each_group_is_under_a_section_of_the_menu_in_its_order():
    navigation = pathlib.Path("admin_ui/src/components/navigation.ts").read_text(encoding="utf-8")
    menu = re.search(r"MENU_SECTIONS: MenuSection\[\] = \[([^\]]*)\]", navigation).group(1)

    assert list(SECTIONS) == re.findall(r"'([^']+)'", menu)
    assert {group.section for group in GROUPS} == set(SECTIONS)
    # The groups of a section follow each other, so that the page lists them as the menu does.
    order = [SECTIONS.index(group.section) for group in GROUPS]
    assert order == sorted(order)


def test_each_parameter_is_under_one_block_of_its_group_and_each_block_holds_some():
    blocks = {(block.group, block.id): block for block in CATALOG.blocks}
    groups = {group.id for group in GROUPS}

    assert len(blocks) == len(CATALOG.blocks)
    assert {block.group for block in CATALOG.blocks} <= groups
    for param in CATALOG.params:
        assert (param.group, param.block) in blocks, param.path
        if param.access == UNUSED:
            assert param.block == UNUSED_BLOCK, param.path
        else:
            # The first block of its group that holds its path: a later one never also claims it unseen.
            holding = [block.id for block in CATALOG.blocks if block.group == param.group and block.holds(param.path)]
            assert holding and holding[0] == param.block, param.path
    used = {(param.group, param.block) for param in CATALOG.params}
    assert set(blocks) == used
    assert all(block.title for block in CATALOG.blocks)


def test_the_page_gets_the_sections_and_blocks():
    served = CATALOG.as_dict()

    assert served["sections"] == list(SECTIONS)
    assert {group["section"] for group in served["groups"]} == set(SECTIONS)
    assert {(block["group"], block["id"]) for block in served["blocks"]} == {
        (block.group, block.id) for block in CATALOG.blocks}
    assert all(param["block"] for param in served["params"])


def _plain(path: str) -> str:
    return re.sub(r"\[[^\]]*\]", "", path)


def _under(path: str, prefixes) -> bool:
    plain = _plain(path)
    return any(plain == prefix or plain.startswith(f"{prefix}.") for prefix in prefixes)


def test_what_the_page_may_change_is_what_the_editor_takes():
    for param in CATALOG.params:
        if _under(param.path, PROTECTED_PATHS):
            assert param.access == SERVER, param.path
        elif _under(param.path, EDITABLE_PATHS):
            # Taken by the editor: editable on the page, or said to be read by nothing.
            assert param.access in (EDITABLE, UNUSED), param.path
        else:
            assert param.access != EDITABLE, param.path
        if param.path in SECRET_PATHS:
            assert param.kind == "secret" and param.access in (CREDENTIAL, SERVER), param.path


def _accepted(tmp_path, path: str, value) -> bool:
    config = copy.deepcopy(EXAMPLE)
    place(config, path, value)
    candidate = tmp_path / "config.json"
    candidate.write_text(json.dumps(config))
    try:
        manager = ConfigurationManager(str(candidate))
        RecorderSettings.from_config(manager)
        CapitalSettings.from_config(manager)
        TaEngineSettings.from_config(manager)
        AdminSettings.from_config(manager)
        check_admin_limits(manager.config_data)
    except ValueError:
        return False
    return True


# Bounds that another parameter narrows: the validators check them against it, not alone.
CROSS_CHECKED = {
    (START_HOUR, "maximum"), (END_HOUR, "minimum"), (RISKY_HOUR, "minimum"), (RISKY_HOUR, "maximum"),
    ("trade.config.turbo_preference.price_range.max", "minimum"),
}
# Bounds only the page holds to: the services read these without refusing other values.
THE_PAGES_ONLY = {
    "trade.rules[risk_management].rule_config.cooldown_after_loss_minutes",
    "trade.rules[risk_management].rule_config.max_trades_per_day",
    "trade.config.buying_power.reserve_cash_percent",
    "trade.config.buying_power.balance_cache_max_age_seconds",
    "trade.config.position_sizing.confidence_scaling.default_confidence",
    "trade.config.position_sizing.confidence_scaling.min_confidence_threshold",
    "trade.config.turbo_cache.ttl_seconds",
    "trade.config.signal_polling.interval_ms",
    "trade.config.signal_polling.long_poll_wait_ms",
    "trade.config.signal_polling.startup_replay_max_age_seconds",
    "trade.config.signal_polling.request_timeout_seconds",
    "trade.config.general.streaming.refresh_rate_ms",
    "trade.config.general.streaming.reconnect_delay_seconds",
    "trade.config.general.streaming.max_reconnect_delay_seconds",
    "trade.config.general.streaming.reauth_interval_seconds",
    "trade.config.general.streaming.reconcile_interval_seconds",
    "trade.config.general.streaming.live_value_interval_seconds",
    "trade.config.general.keep_warm_interval_seconds",
    "reporting.signal_silence_minutes",
}
BOUNDED = [
    param for param in CATALOG.params
    if param.access == EDITABLE and param.kind in ("int", "number")
    and (param.minimum is not None or param.maximum is not None)
]


def _value(param, bound: float):
    return int(bound) if param.kind == "int" else float(bound)


@pytest.mark.parametrize("param", BOUNDED, ids=lambda param: param.path)
def test_the_bounds_the_page_offers_are_accepted(tmp_path, param):
    for side in ("minimum", "maximum"):
        bound = getattr(param, side)
        if bound is None or (param.path, side) in CROSS_CHECKED:
            continue
        assert _accepted(tmp_path, param.path, _value(param, bound)), (param.path, side, bound)


@pytest.mark.parametrize("param", [param for param in BOUNDED if param.path not in THE_PAGES_ONLY],
                         ids=lambda param: param.path)
def test_beyond_its_bounds_a_value_is_refused_by_the_services(tmp_path, param):
    step = 1 if param.kind == "int" else 0.5
    for side, beyond in (("minimum", -step), ("maximum", step)):
        bound = getattr(param, side)
        if bound is None or (param.path, side) in CROSS_CHECKED:
            continue
        assert not _accepted(tmp_path, param.path, _value(param, bound + beyond)), (param.path, side)


def test_a_rule_is_read_by_its_type_and_a_second_of_the_same_type_is_unknown():
    config = copy.deepcopy(EXAMPLE)
    config["trade"]["rules"].append({"rule_type": "market_hours", "rule_name": "again", "rule_config": {"x": 1}})
    config["trade"]["rules"].append({"rule_type": "a_new_rule", "rule_name": "?", "rule_config": {"y": 2}})

    unknown = [entry["path"] for entry in unknown_paths(config)]

    assert flatten(config)[START_HOUR] == 8
    assert "trade.rules[6].rule_config.x" in unknown
    assert "trade.rules[a_new_rule].rule_config.y" in unknown


def test_a_typo_is_unknown_and_the_key_it_resembles_is_named():
    config = copy.deepcopy(EXAMPLE)
    config["trade"]["config"]["position_management"]["performance_thresholds"]["trailing_stopp"] = {"enabled": True}

    unknown = unknown_paths(config)

    assert unknown == [{
        "path": "trade.config.position_management.performance_thresholds.trailing_stopp.enabled",
        "nearest": "trade.config.position_management.performance_thresholds.trailing_stop.enabled",
    }]


def test_a_change_names_its_parameter_and_reaches_the_services_that_read_it():
    after = copy.deepcopy(EXAMPLE)
    place(after, CLOSE_TIME, "21:50")
    place(after, "trade.config.ta_engine.enabled", True)

    found = changes(EXAMPLE, after)
    reached = impact(found)

    by_path = {change["path"]: change for change in found}
    assert set(by_path) == {CLOSE_TIME, "trade.config.ta_engine.enabled"}
    assert (by_path[CLOSE_TIME]["before"], by_path[CLOSE_TIME]["after"]) == ("21:55", "21:50")
    assert reached["risk"] == "trading"
    assert {"scheduler", "position_monitor", "ta_engine"} <= set(reached["services"])
    assert "dashboard" in reached["consumers"]


def test_an_added_rule_is_a_change_of_each_of_its_keys():
    after = copy.deepcopy(EXAMPLE)
    after["trade"]["rules"].append({"rule_type": "macro_news", "rule_name": "news",
                                    "rule_config": {"minutes_before": 5, "flatten_minutes_before": 10}})

    found = changes(EXAMPLE, after)

    assert {change["path"] for change in found} == {
        "trade.rules[macro_news].rule_name",
        "trade.rules[macro_news].rule_config.minutes_before",
        "trade.rules[macro_news].rule_config.flatten_minutes_before",
    }
    assert all(change["added"] for change in found)


def test_parameters_that_work_against_each_other_are_said():
    config = copy.deepcopy(EXAMPLE)
    place(config, CLOSE_TIME, "22:05")
    place(config, "trade.config.ta_engine.enabled", True)
    place(config, "trade.config.position_management.performance_thresholds.trailing_stop",
          {"enabled": True, "activation_percent": 70, "drawdown_percent": 8})

    said = {warning["path"] for warning in warnings(config)}

    assert said == {CLOSE_TIME, "trade.config.ta_engine.enabled",
                    "trade.config.position_management.performance_thresholds.trailing_stop.activation_percent"}
    assert warnings(EXAMPLE) == []


def test_the_keys_nothing_reads_can_be_taken_out_of_the_file(tmp_path):
    config = copy.deepcopy(EXAMPLE)
    general = config["trade"]["config"]["general"]
    del general["position_check"], general["websocket"]
    del general["api_limits"]["top_positions"], general["retry_config"]["retry_sleep_seconds"]
    candidate = tmp_path / "config.json"
    candidate.write_text(json.dumps(config))

    ConfigurationManager(str(candidate))

    unused = {param.path for param in CATALOG.params if param.access == UNUSED and param.path.startswith("trade.config.")}
    assert unused == {
        "trade.config.general.api_limits.top_positions", "trade.config.general.retry_config.retry_sleep_seconds",
        "trade.config.general.position_check.check_interval_seconds",
        "trade.config.general.position_check.timeout_seconds", "trade.config.general.websocket.refresh_rate_ms",
    }
