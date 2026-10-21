"""
The tools of the MCP endpoint, one module per group. Each module's
``register(server, research)`` adds its tools through ``budget.research_tool``.
A test holds the server to the exact list of tools.
"""

from src.admin.mcp_server.tools import backtests, data, experiments, jobs, pine, rules, status

GROUPS = (status, data, pine, backtests, rules, experiments, jobs)


def register_all(server, research) -> None:
    for group in GROUPS:
        group.register(server, research)
    backtests.register_writing(server, research)
    pine.register_studies(server, research)
    rules.register_shadow(server, research)
