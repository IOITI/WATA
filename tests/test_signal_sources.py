"""Which source each index takes its signals from (src/trade/signal_sources.py), and its checks."""

import json

import pytest

from src.configuration import ConfigurationManager
from src.trade.signal_sources import (
    ENGINE,
    TRADINGVIEW,
    refusal,
    source_of,
    sources_from,
    unattended,
    unheard,
)


def test_an_index_takes_tradingviews_signals_unless_it_is_set_to_the_engine():
    sources = sources_from({"us100": "engine", "de40": "tradingview", "junk": "nobody"})
    assert sources == {"us100": ENGINE, "de40": TRADINGVIEW}
    assert source_of(sources, "us100") == ENGINE
    assert source_of(sources, "us500") == TRADINGVIEW
    assert sources_from(None) == {} and sources_from(["us100"]) == {}


@pytest.mark.parametrize(
    "value, problem",
    [
        (None, None),
        ({}, None),
        ({"us100": "engine"}, None),
        ("engine", "must be an object"),
        ({"us500": "engine"}, "not one of the allowed indices"),
        ({"us100": "tv"}, "is not a source"),
    ],
)
def test_a_setting_of_the_sources_names_allowed_indices_and_known_sources(value, problem):
    found = refusal(value, {"us100"})
    assert (found is None) if problem is None else (problem in found)


def test_what_a_live_rule_and_the_sources_leave_unattended_or_unheard():
    sources = {"us100": ENGINE, "de40": ENGINE}
    assert unattended(sources, ["us100"]) == ["de40"]
    assert unheard({}, ["us100"]) == ["us100"]
    assert unheard(sources, ["us100"]) == [] and unattended(sources, ["us100", "de40"]) == []


def _config(tmp_path, sources):
    with open("tests/test_config.json") as source_file:
        config = json.load(source_file)
    for rule in config["trade"]["rules"]:
        if rule["rule_type"] == "allowed_indices":
            rule["rule_config"]["indice_ids"] = {"us100": "1909050"}
    config["trade"]["config"]["signal_sources"] = sources
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    return str(path)


def test_the_validator_refuses_a_source_it_does_not_know_and_an_index_not_traded(tmp_path):
    ConfigurationManager(_config(tmp_path, {"us100": "engine"}))
    with pytest.raises(ValueError, match="is not a source"):
        ConfigurationManager(_config(tmp_path, {"us100": "both"}))
    with pytest.raises(ValueError, match="not one of the allowed indices"):
        ConfigurationManager(_config(tmp_path, {"nowhere": "engine"}))
