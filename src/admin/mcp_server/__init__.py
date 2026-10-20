"""
The admin's MCP endpoint (``/mcp``): research tools for Claude Code on the
operator's Mac, over the admin's own port and the VPN, opened with a token the
operator makes on the server (``watamcptoken``).

The tools call the admin's own services (the backtest queue, the script and
rule stores, the replay, the read-only pool) and nothing that trades, edits the
configuration, holds a credential or takes a rule live.

The SDK (``mcp``) is imported by ``server.py`` alone, and only when
``admin.mcp_enabled`` is true: the tokens, the gate and the call log never need
it, so the command line that makes a token and an admin with the endpoint off
run without it. The package is not named ``mcp``: ``src/`` is on PYTHONPATH,
and a top-level ``mcp`` there would stand in for the SDK in every service.
"""
