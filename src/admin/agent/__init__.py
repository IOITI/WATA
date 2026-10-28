"""
The admin's AI agent: a backtest run or a Pine script handed to Claude from the
pages, which analyses it with the research tools of the MCP endpoint (called in
process), starts backtests within the limits of a request, and proposes changes
the operator applies. It runs in the admin (``ctx.agent``, an application task)
and keeps going when the page is closed; its conversations are files under
``admin.persistant.agent_path``.

Nothing here trades, edits the configuration, takes a rule live or changes a
rule's mode. The Anthropic and MCP SDKs are imported inside functions only: with
the agent off, the admin loads neither.
"""
