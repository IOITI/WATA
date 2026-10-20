"""
The research method shared by the lab on a Mac (lab/) and Claude's tools on
the server (src/admin/mcp_server/): the experiment journal and its lock box,
how many configurations a request tries, and how a run is asked for again.

Nothing here decides a backtest's result: it stays out of the code a run's
digest covers (src/backtest/figures.py), and a change here marks no run stale.
"""
