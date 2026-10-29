"""``status``: where things stand, the first call of a session."""

import os
from datetime import timedelta

from starlette.concurrency import run_in_threadpool

from src.admin.errors import DatabaseUnavailable
from src.admin.mcp_server.budget import research_tool
from src.admin.mcp_server.facade import CLAUDE
from src.admin.mcp_server.gate import current_caller
from src.admin.mcp_server.shape import compacted, iso_moment
from src.ta_engine.runner import RUNNER_PATH, find_node

# The days looked back at for the turbos the recorder kept.
RECORDED_LOOKBACK_DAYS = 30


async def _data(research) -> dict:
    """What there is to work on, in a few figures: the days recorded lately, the feeds, the long history."""
    today = research.today()
    found: dict = {}
    try:
        days = await research.queries.backtest_recorder_days(today - timedelta(days=RECORDED_LOOKBACK_DAYS), today)
        recorded = sorted(str(row["day"]) for row in days if row.get("listened"))
        found["saxo_recorder"] = {
            "days_recorded_in_last_30": len(recorded), "first": recorded[0] if recorded else None,
            "last": recorded[-1] if recorded else None,
        }
        found["capital_feed"] = [
            {"ref": f"capital/{row['epic']}", "indice": row.get("label"), "first": iso_moment(row.get("first_t")),
             "last": iso_moment(row.get("last_t"))}
            for row in await research.queries.capital_assets()
        ]
    except DatabaseUnavailable:
        found["database"] = "unavailable"
    if research.history is not None:
        history = research.history
        days = await run_in_threadpool(history.days, "us100")
        found["dukascopy_us100"] = {"days": len(days), "first": days[0].isoformat() if days else None,
                                    "last": days[-1].isoformat() if days else None}
    return found


def _journal(research) -> dict:
    experiments = research.journal.experiments()
    families: dict[str, int] = {}
    for experiment in experiments:
        families[experiment["family"]] = families.get(experiment["family"], 0) + experiment["trials"]
    box = research.journal.lockbox()
    return {
        "open_experiments": [
            {"id": experiment["id"], "title": experiment["title"], "family": experiment["family"],
             "trials": experiment["trials"], "runs": len(experiment["runs"])}
            for experiment in experiments if experiment["verdict"] is None
        ],
        "concluded": sum(1 for experiment in experiments if experiment["verdict"] is not None),
        "trials_by_family": families,
        "lockbox": {"from": box["from"], "to": box["to"]} if box else None,
    }


def register(server, research) -> None:
    @research_tool(server, research.calls, name="status", title="Where things stand", read_only=True)
    async def status() -> dict:
        """Start here. The server's version and time, and the release each container runs (the admin's is the
        server's version; the others may run an older one with the same code); whether the market is open by WATA's
        live rules; the data to work on (days the recorder kept lately, the Capital.com feed, Dukascopy's history);
        the backtest line (what runs, what waits, Claude's share) and the code digest runs are made with; the
        experiment journal (open experiments, trials by family, lock box); whether the Pine runner is there; free
        disk; the token in use; and what Claude may use of the server (limits)."""
        caller = current_caller()
        service = research.backtests
        export = await run_in_threadpool(research.research_export.status) if research.research_export else None
        return compacted({
            "server": {"version": research.version, "time": research.now_iso(), "timezone": research.timezone,
                       "release": await run_in_threadpool(research.release) if research.release else None},
            "market": research.config.market(research.clock()),
            "indices": research.config.allowed_indices(),
            "data": await _data(research),
            "backtests": {
                "running": service.queue()["running"], "waiting": len(service.queue()["waiting"]),
                "claude_waiting": service.waiting_of(CLAUDE), "claude_may_wait": research.limits.max_waiting_runs,
                "code_digest": service.code,
            } if service is not None else "switched off in the configuration (admin.backtests_enabled)",
            "journal": await run_in_threadpool(_journal, research),
            "research_export": {key: export.get(key) for key in ("enabled", "state", "days", "last")} if export else None,
            "pine_runner": bool(os.path.isfile(RUNNER_PATH) and find_node("node")),
            "free_disk_gb": research.free_disk_gb(),
            "token": {"name": caller.name, "expires_at": caller.expires_at} if caller else None,
            "limits": research.limits.as_dict(),
        })
