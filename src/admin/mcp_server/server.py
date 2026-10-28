"""
The MCP server of the admin, built with the official SDK when
``admin.mcp_enabled`` is true (``create_app`` imports this module then), and
by the AI agent when ``admin.ai_agent.enabled`` is (``src/admin/agent/tools.py``
calls its tools in the admin's process).

Stateless and in JSON: each POST is answered whole, nothing is held open,
which suits Traefik and the admin's plain-ASGI headers middleware alike. The
SDK speaks both the 2025-11-25 protocol (initialize handshake) and the
stateless 2026-07-28 one (``server/discover``). Its own host check is off: it
would refuse the admin's public name (421), and the gate in front refuses any
request a browser could make.
"""

import contextlib
from dataclasses import dataclass
from typing import Any

from mcp.server import MCPServer
from mcp.server.streamable_http_manager import StreamableHTTPASGIApp
from mcp.server.transport_security import TransportSecuritySettings

from src.admin.mcp_server import tools
from src.admin.mcp_server.facade import ResearchServices
from src.admin.mcp_server.gate import MCP_PATH, McpGate
from src.admin.mcp_server.instructions import INSTRUCTIONS, RESEARCH_PROMPT

SERVER_NAME = "wata"


@dataclass
class McpEndpoint:
    server: Any
    gate: McpGate

    @contextlib.asynccontextmanager
    async def running(self):
        """For the admin's lifespan: a mounted application's own lifespan never runs."""
        async with self.server.session_manager.run():
            yield


def build_server(research: ResearchServices) -> MCPServer:
    server = MCPServer(
        name=SERVER_NAME,
        title="WATA research",
        version=research.version,
        instructions=INSTRUCTIONS,
        log_level="WARNING",
        # No server-to-client notifications: every answer is a whole JSON body.
        subscriptions=False,
    )
    tools.register_all(server, research)

    @server.prompt(name="research", title="Research a strategy on WATA's data")
    def research_prompt() -> str:
        """The research loop: experiment, script, backtests, sweeps, checks, conclusion."""
        return RESEARCH_PROMPT

    return server


def build_endpoint(ctx, paused=lambda: False) -> McpEndpoint:
    server = build_server(ResearchServices.from_context(ctx))
    server.streamable_http_app(
        streamable_http_path=MCP_PATH,
        json_response=True,
        stateless_http=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    gate = McpGate(
        endpoint=StreamableHTTPASGIApp(server.session_manager),
        tokens=ctx.mcp_tokens,
        trusted_proxies=ctx.trusted_proxies,
        paused=paused,
    )
    return McpEndpoint(server=server, gate=gate)
