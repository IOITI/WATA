"""
What the MCP endpoint can never reach: nothing that trades, speaks for the
operator or holds a credential. Tested by what the tools are given (the
context's attributes that matter are tripwires, and every tool is called) and
by what their code may import. The admin already imports the Saxo client
through the backtest code, so "never imported" could not be the test.
"""

import ast
import asyncio
import pathlib

import pytest
from mcp import Client

from src.admin.mcp_server.facade import ResearchServices
from src.admin.mcp_server.limits import McpLimits
from src.admin.mcp_server.server import build_server
from src.admin.ta_scripts import ScriptStore
from tests.test_admin_api import config_path, make_ctx  # noqa: F401 (fixtures)
from tests.test_admin_ta_rules import BANDS, MATHS, STRATEGY
from tests.test_mcp_gate import mcp_ctx
from tests.test_mcp_tools_read import FakeQueries

FORBIDDEN_CONTEXT = (
    "services", "saxo", "config_editor", "risk_state", "notifier", "audit_log", "live_prices", "credentials",
    "sessions", "throttle", "config_manager", "downloads", "paper",
)

# What the MCP endpoint's code and the research method may import of WATA's own.
ALLOWED_IMPORTS = (
    "src.admin.mcp_server", "src.research",
    "src.admin.asset_bars", "src.admin.backtest_requests", "src.admin.data", "src.admin.downloads",
    "src.admin.errors", "src.admin.prices", "src.admin.processes", "src.admin.studies", "src.admin.ta_compare",
    "src.admin.ta_rules", "src.admin.ta_scripts", "src.admin.auth",
    "src.backtest.clock", "src.ta_engine.runner", "src.ta_engine.replay", "src.ta_engine.bars", "src.ta_engine.rules",
    "src.ta_engine.signals", "src.price_history.process",
    # The study child reads the configuration for the database's address, as a backtest's child does.
    "src.configuration",
)
NEVER = (
    "src.admin.routers", "src.admin.services", "src.admin.config_editor", "src.admin.live_prices", "src.admin.queries",
    "src.admin.context", "src.admin.actions", "src.trade", "src.trader", "src.web_server", "src.mq_telegram",
    "src.saxo_openapi", "src.saxo_authen", "src.saxo_streaming", "src.position_monitor", "src.scheduler",
    "src.capital_feed", "src.database", "src.watchlist_manager",
)


class Tripwire:
    """Stands for something no tool may use: any use is written down."""

    touched: list = []

    def __init__(self, name):
        object.__setattr__(self, "_name", name)

    def __getattr__(self, attribute):
        Tripwire.touched.append(f"{self._name}.{attribute}")
        raise AssertionError(f"An MCP tool used {self._name}.{attribute}")

    def __call__(self, *args, **kwargs):
        Tripwire.touched.append(f"{self._name}()")
        raise AssertionError(f"An MCP tool called {self._name}")


# A plausible call of each tool: what matters is that none reaches a tripwire, whatever it answers.
CALLS = {
    "status": {},
    "data_assets": {}, "data_coverage": {"start_day": "2026-10-13", "end_day": "2026-10-14"},
    "data_bars": {"asset": "capital/US100", "start_day": "2026-10-13"}, "data_signals": {"start_day": "2026-10-13"},
    "data_trades": {"start_day": "2026-10-13"}, "data_execution": {},
    "pine_list": {}, "pine_get": {"script_id": "000000000000"},
    "pine_check": {"source": "//@version=6\nindicator('x')\nplot(close)\n"},
    "pine_run": {"asset": "capital/US100", "start_day": "2026-10-13", "script_id": "000000000000"},
    "pine_signals": {"asset": "capital/US100", "start_day": "2026-10-13", "alerts": {"Up": "long"}, "script_id": "000000000000"},
    "pine_compare": {"asset": "capital/US100", "start_day": "2026-10-13", "alerts": {"Up": "long"}, "script_id": "000000000000"},
    "pine_save": {"source": "//@version=6\nindicator('x')\nplot(close)\n", "name": "X"},
    "pine_delete": {"script_id": "000000000000"},
    "backtest_options": {}, "backtest_list": {}, "backtest_result": {"run_id": "20261019-200000-abcdef"},
    "backtest_trades": {"run_id": "20261019-200000-abcdef"}, "backtest_days": {"run_id": "20261019-200000-abcdef"},
    "backtest_signals": {"run_id": "20261019-200000-abcdef"},
    "backtest_compare": {"run_ids": ["20261019-200000-abcdef", "20261019-200001-abcdef"]},
    "turbo_model": {},
    "rule_list": {}, "rule_compare": {"rule_id": "0123456789ab", "day": "2026-10-13"},
    "exp_new": {"title": "T", "hypothesis": "H", "family": "F"}, "exp_get": {},
    "exp_conclude": {"experiment_id": "nothere", "verdict": "kept", "notes": "n"},
    "exp_trials": {"experiment_id": "nothere", "trials": 1, "note": "n"},
    "lockbox_set": {"start_day": "2026-10-19"},
    "backtest_start": {"experiment_id": "nothere", "request": {"from": "2026-10-13", "to": "2026-10-13"}},
    "backtest_derive": {"run_id": "20261019-200000-abcdef", "kind": "stress", "experiment_id": "nothere"},
    "backtest_wait": {"run_ids": ["20261019-200000-abcdef"], "seconds": 1},
    "job_wait": {"job_id": "000000000000", "seconds": 0},
    "backtest_remove": {"run_id": "20261019-200000-abcdef"},
    "turbo_model_measure": {"start_day": "2026-10-13", "end_day": "2026-10-14"},
    "rule_shadow_save": {"name": "X", "script_id": "000000000000", "asset": "capital/US100", "indice": "us100",
                         "alerts": {"Up": "long"}},
    "rule_shadow_remove": {"rule_id": "0123456789ab"}, "rule_set_reference": {"run_id": "20261019-200000-abcdef"},
}


@pytest.fixture
def ctx(config_path, tmp_path):
    from src.admin.backtests import BacktestService

    ctx = mcp_ctx(config_path, tmp_path)
    scripts = ScriptStore(str(tmp_path / "ta"))
    for name, source in (("Maths", MATHS), ("Bands", BANDS), ("Cross", STRATEGY)):
        scripts.create(name, source)
    ctx.ta_scripts = scripts
    ctx.queries = FakeQueries()
    ctx.backtests = BacktestService(
        root=str(tmp_path / "backtests"), prices_root=str(tmp_path / "prices"), config_path=config_path, scripts=scripts,
    )
    # Claude's shadow rules switched on: their tools go past the switch.
    ctx.settings.mcp_limits = McpLimits(shadow_rules=True)
    for name in FORBIDDEN_CONTEXT:
        setattr(ctx, name, Tripwire(name))
    Tripwire.touched = []
    return ctx


def test_the_tools_are_given_nothing_that_trades_or_speaks_for_the_operator(ctx):
    research = ResearchServices.from_context(ctx)
    server = build_server(research)

    async def every_tool():
        async with Client(server) as client:
            listed = {tool.name for tool in (await client.list_tools()).tools}
            assert listed == set(CALLS), "a tool without a call here: add one"
            for name, arguments in CALLS.items():
                await client.call_tool(name, arguments)

    asyncio.run(every_tool())

    assert Tripwire.touched == []


def test_the_facade_takes_none_of_them_from_the_context(ctx):
    research = ResearchServices.from_context(ctx)

    held = [getattr(research, field) for field in vars(research)]
    assert not any(isinstance(value, Tripwire) for value in held)


def _imports(path: pathlib.Path) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            # ``from src.admin import backtest_requests`` imports src.admin.backtest_requests: each name counts.
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return {name for name in found if name.startswith("src")}


@pytest.mark.parametrize("root", ["src/admin/mcp_server", "src/research"])
def test_the_endpoint_imports_only_what_research_needs(root):
    for path in pathlib.Path(root).rglob("*.py"):
        for module in _imports(path):
            assert not any(module == never or module.startswith(f"{never}.") for never in NEVER), (str(path), module)
            assert any(module == allowed or module.startswith(f"{allowed}.") for allowed in ALLOWED_IMPORTS), (str(path), module)
