"""
The experiment journal on the server: what Claude tried, why, how many
configurations each family of ideas was tried with, and what came of it; and
the lock box, the days kept out of every run until a candidate's final check.
"""

from datetime import date

from starlette.concurrency import run_in_threadpool

from src.admin.mcp_server.budget import research_tool
from src.admin.mcp_server.facade import CLAUDE
from src.admin.mcp_server.shape import compacted


def _experiment_line(experiment: dict) -> dict:
    return {
        "id": experiment["id"], "title": experiment["title"], "family": experiment["family"],
        "verdict": experiment["verdict"], "trials": experiment["trials"], "runs": len(experiment["runs"]),
        "opened_at": experiment["opened_at"], "concluded_at": experiment["concluded_at"],
    }


def register(server, research) -> None:
    calls = research.calls
    journal = research.journal

    @research_tool(server, calls, name="exp_new", title="Open an experiment", read_only=False, idempotent=False)
    async def exp_new(title: str, hypothesis: str, family: str, plan: str = "") -> dict:
        """Open an experiment before running anything: a title, a hypothesis the data can refute, and its family
        (the idea it belongs to: experiments of one idea share a family, and the overfitting figures of its sweeps
        are corrected with every trial of the family). plan: what will be run. Gives the experiment's id, which every
        backtest_start and backtest_derive names."""
        opened = await run_in_threadpool(lambda: journal.new(title, hypothesis, family, plan=plan, made_by=CLAUDE))
        return compacted({
            "id": opened["experiment"], "family": opened["family"],
            "family_trials_so_far": await run_in_threadpool(journal.family_trials, opened["family"]),
        })

    @research_tool(server, calls, name="exp_get", title="The experiment journal", read_only=True)
    async def exp_get(experiment_id: str = "") -> dict:
        """With no id: every experiment, open ones first, each with its family, verdict, trials and runs count, the
        trials by family, and the lock box. With an id: that experiment whole, its hypothesis, plan, each run (kind,
        trials, whether it used the lock box) and its conclusion."""
        if experiment_id:
            found = await run_in_threadpool(journal.get, experiment_id)
            return compacted({**found, "family_trials": await run_in_threadpool(journal.family_trials, found["family"])})
        experiments = await run_in_threadpool(journal.experiments)
        families: dict[str, int] = {}
        for experiment in experiments:
            families[experiment["family"]] = families.get(experiment["family"], 0) + experiment["trials"]
        box = await run_in_threadpool(journal.lockbox)
        ordered = sorted(experiments, key=lambda experiment: (experiment["verdict"] is not None, experiment["opened_at"]))
        return compacted({
            "experiments": [_experiment_line(experiment) for experiment in ordered],
            "trials_by_family": families,
            "lockbox": box,
        })

    @research_tool(server, calls, name="exp_trials", title="Count trials made elsewhere", read_only=False,
                   idempotent=False)
    async def exp_trials(experiment_id: str, trials: int, note: str) -> dict:
        """Configurations of an open experiment tried outside WATA's runs (a port of the script screened on the Mac,
        a grid in pandas, by hand): ``trials`` of them, counted in the family's trials as a run's are, so that the
        overfitting figures of the family's later sweeps are corrected for them too. note: what was tried, where,
        and on which days."""
        await run_in_threadpool(journal.record_trials, experiment_id, trials, note)
        found = await run_in_threadpool(journal.get, experiment_id)
        return compacted({
            **_experiment_line(found), "elsewhere": found["elsewhere"],
            "family_trials": await run_in_threadpool(journal.family_trials, found["family"]),
        })

    @research_tool(server, calls, name="exp_conclude", title="Conclude an experiment", read_only=False, idempotent=False)
    async def exp_conclude(experiment_id: str, verdict: str, notes: str) -> dict:
        """Conclude an experiment: verdict kept, rejected or inconclusive, and in notes what it rests on (days and
        trades it saw, random entries beaten, held-out and walk-forward results, neighbours, stress). Recorded turbos
        only start on 2026-10-13: say on how few days it rests."""
        await run_in_threadpool(journal.conclude, experiment_id, verdict, notes)
        return compacted(_experiment_line(await run_in_threadpool(journal.get, experiment_id)))

    @research_tool(server, calls, name="lockbox_set", title="Keep days out of every run", read_only=False,
                   idempotent=True)
    async def lockbox_set(start_day: date, end_day: date | None = None) -> dict:
        """The lock box: days kept out of every run, every data and Pine study, until a candidate's final check
        (backtest_start with lockbox_final, written in the journal). From start_day to end_day (none: every day
        after start_day). Claude may only make it larger; the operator alone can move or open it."""
        box = await run_in_threadpool(
            lambda: journal.set_lockbox(start_day, end_day, set_by=CLAUDE, only_wider=True),
        )
        return compacted({"lockbox": box})
