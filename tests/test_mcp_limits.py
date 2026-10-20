import json
import sys

import pytest

from src.admin.context import AdminSettings
from src.admin.mcp_server.limits import McpLimits
from src.configuration import ConfigurationManager


def test_every_limit_has_a_default():
    limits = McpLimits.from_config(None)

    assert limits == McpLimits()
    assert limits.shadow_rules is False
    assert limits.max_waiting_runs == 3


def test_the_example_configuration_states_the_defaults():
    with open("etc/config_example.json") as config_file:
        admin = json.load(config_file)["admin"]

    assert admin["mcp_enabled"] is False
    assert McpLimits.from_config(admin["mcp"]) == McpLimits()
    assert admin["persistant"]["mcp_tokens_path"] == AdminSettings.mcp_tokens_path


@pytest.mark.parametrize("section,message", [
    ({"max_waiting_runs": 0}, "max_waiting_runs"),
    ({"max_waiting_runs": 2.5}, "max_waiting_runs"),
    ({"runs_per_day": True}, "runs_per_day"),
    ({"study_memory_mb": 4096}, "study_memory_mb"),
    ({"shadow_rules": "yes"}, "shadow_rules"),
    ({"go_live": True}, "Unknown field"),
    ([], "must be an object"),
])
def test_a_limit_of_the_wrong_kind_or_out_of_bounds_is_refused(section, message):
    with pytest.raises(ValueError, match=message):
        McpLimits.from_config(section)


def _settings(tmp_path, admin):
    with open("tests/test_config.json") as source:
        config = json.load(source)
    config["admin"] = admin
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    return AdminSettings.from_config(ConfigurationManager(str(path)))


def test_the_endpoint_is_off_unless_asked_for(tmp_path):
    settings = _settings(tmp_path, {})

    assert settings.mcp_enabled is False
    assert settings.mcp_tokens_path == "/app/var/lib/admin/mcp_tokens.json"


def test_the_endpoint_and_its_limits_are_read_from_the_admin_section(tmp_path):
    settings = _settings(tmp_path, {
        "mcp_enabled": True, "mcp": {"max_waiting_runs": 5, "shadow_rules": True},
        "persistant": {"mcp_tokens_path": "/tmp/tokens.json"},
    })

    assert settings.mcp_enabled is True
    assert settings.mcp_limits.max_waiting_runs == 5
    assert settings.mcp_limits.shadow_rules is True
    assert settings.mcp_tokens_path == "/tmp/tokens.json"


def test_a_switch_that_is_not_a_boolean_is_refused(tmp_path):
    with pytest.raises(ValueError, match="mcp_enabled"):
        _settings(tmp_path, {"mcp_enabled": "true"})
