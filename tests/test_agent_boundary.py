"""
What the AI agent (src/admin/agent/) may never do, held by reading its code: it
never changes a rule's mode (going live, or leaving it, stays the operator's, as
the MCP endpoint's only call is ShadowRules._switch), never reaches the services
that trade or speak for the operator, never imports the configuration's editor
(a change of settings it proposes is saved by the agent's route, as the
Configuration page saves), and imports neither SDK at module level.
"""

import ast
import pathlib

AGENT = pathlib.Path("src/admin/agent")
# What the admin's context holds that the agent never touches: the services that trade, the operator's
# credentials and sessions, the configuration's editor, the pause of entries, the restarts.
NEVER = {"services", "saxo", "config_editor", "risk_state", "notifier", "audit_log", "credentials", "sessions",
         "service_control", "restarts", "live_prices", "downloads", "paper"}


def _trees():
    return {path.name: ast.parse(path.read_text(encoding="utf-8")) for path in sorted(AGENT.glob("*.py"))}


def test_the_agent_never_changes_a_rule_s_mode():
    calls = [(name, node.func.attr) for name, tree in _trees().items() for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "set_mode"]
    assert calls == []


def test_the_agent_reaches_none_of_the_services_that_trade_or_speak_for_the_operator():
    reached = sorted({(name, node.attr) for name, tree in _trees().items() for node in ast.walk(tree)
                      if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                      and node.value.id == "ctx" and node.attr in NEVER})
    assert reached == []


def test_the_sdks_are_imported_inside_functions_only():
    at_module_level = sorted(
        (name, alias.name if isinstance(node, ast.Import) else node.module)
        for name, tree in _trees().items() for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
        if (alias.name if isinstance(node, ast.Import) else node.module or "").split(".")[0] in ("mcp", "anthropic")
        or (isinstance(node, ast.ImportFrom) and node.module in ("src.admin.mcp_server.server", "src.admin.mcp_server.budget"))
    )
    assert at_module_level == []


def test_the_agent_never_imports_the_configuration_s_editor():
    imported = sorted(
        (name, alias.name if isinstance(node, ast.Import) else node.module)
        for name, tree in _trees().items() for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
        if "config_editor" in (alias.name if isinstance(node, ast.Import) else f"{node.module}.{alias.name}")
    )
    assert imported == []
