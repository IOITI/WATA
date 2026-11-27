"""
The experiment journal on the server: what Claude tried, why, how many
configurations each family of ideas was tried with, and what came of it; and
the lock box, the days kept out of every run until a candidate's final check.
"""

from datetime import date

from starlette.concurrency import run_in_threadpool

from src.admin.errors import NotPossible
from src.admin.mcp_server.budget import Refused, research_tool
from src.admin.mcp_server.facade import CLAUDE
from src.admin.mcp_server.gate import current_caller
from src.admin.mcp_server.shape import compacted
from src.admin.run_verdict import CRITERIA_FIGURES, criteria_met
from src.admin.track_record import family_record, record
from src.research.experiments import JournalError, close_family


def _experiment_line(experiment: dict, forward: dict | None = None) -> dict:
    line = {
        "id": experiment["id"], "title": experiment["title"], "family": experiment["family"],
        "verdict": experiment["verdict"], "trials": experiment["trials"], "runs": len(experiment["runs"]),
        "opened_at": experiment["opened_at"], "concluded_at": experiment["concluded_at"],
    }
    if experiment.get("candidate"):
        line["candidate"] = experiment["candidate"]
        line["bar"] = (experiment.get("checks") or {}).get("bar")
    if forward:
        line["forward"] = {key: forward.get(key) for key in ("state", "verdict", "trades", "cumulative", "days_tested", "why")}
    if experiment.get("review"):
        line["review"] = {key: experiment["review"].get(key) for key in ("tone", "line")}
    return line


def _checks_said(judged: dict | None) -> dict:
    """How a run stood against the bar, in short: the verdict's tone, and the checks it failed and left open."""
    if not judged:
        return {"bar": None, "failed": [], "open": []}
    return {
        "bar": judged["verdict"]["tone"],
        "failed": [check["id"] for check in judged["checks"] if check["state"] == "fail"],
        "open": [check["id"] for check in judged["checks"] if check["state"] == "open"],
    }


def register(server, research) -> None:
    calls = research.calls
    journal = research.journal

    def made_via() -> str | None:
        caller = current_caller()
        return caller.name if caller else None

    def forward_of_experiments() -> dict:
        return research.forward.of_experiments() if research.forward is not None else {}

    @research_tool(server, calls, name="exp_new", title="Open an experiment", read_only=False, idempotent=False)
    async def exp_new(title: str, hypothesis: str, family: str, plan: str = "", motivated_by: list[str] | None = None,
                      criteria: list[dict] | None = None, new_family: bool = False) -> dict:
        """Open an experiment before running anything: a title, a hypothesis the data can refute, and its family
        (the idea it belongs to: experiments of one idea share a family, and every run's figures are corrected with
        every trial of the family). A name like an existing family's is refused unless new_family is true. plan: what
        will be run. motivated_by: the days the idea came from (YYYY-MM-DD): every run says its result without them,
        and one that needs them is not believed. criteria: what it must show, set now, before any run, each
        {figure, bound: at_least|at_most, value}, figure one of total_percent, trades, days, win_rate, profit_factor,
        worst_drawdown, sharpe, beaten, edge_t, held_out_percent, walk_forward_percent, pbo, deflated_sharpe,
        family_deflated; checked on the candidate at exp_conclude. Gives the experiment's id, which every
        backtest_start and backtest_derive names, and its family's record so far."""
        for criterion in criteria or []:
            if isinstance(criterion, dict) and criterion.get("figure") not in CRITERIA_FIGURES:
                raise Refused(f"{criterion.get('figure')!r} is not a figure criteria take: one of {', '.join(CRITERIA_FIGURES)}")
        families = await run_in_threadpool(journal.families)
        close = await run_in_threadpool(close_family, (family or "").strip(), families)
        if close is not None and not new_family:
            raise Refused(
                f"{family!r} looks like the family {close!r} ({families[close]} trials): open it in {close!r}, or say "
                "new_family true for another idea. Families: "
                + ", ".join(f"{name} ({trials})" for name, trials in list(families.items())[:12])
            )
        try:
            opened = await run_in_threadpool(lambda: journal.new(
                title, hypothesis, family, plan=plan, made_by=CLAUDE, made_via=made_via(),
                motivated_by=motivated_by, criteria=criteria,
            ))
        except JournalError as e:
            raise Refused(str(e)) from None
        experiments = await run_in_threadpool(journal.experiments)
        return compacted({
            "id": opened["experiment"], "family": opened["family"],
            "family_trials_so_far": await run_in_threadpool(journal.family_trials, opened["family"]),
            "family_record": family_record(experiments, forward_of_experiments(), opened["family"]),
        })

    @research_tool(server, calls, name="exp_get", title="The experiment journal", read_only=True)
    async def exp_get(experiment_id: str = "") -> dict:
        """With no id: every experiment, open ones first, each with its family, verdict, trials and runs count, its
        candidate, how its forward check stands (the days after it, which no run had seen) and a skeptic's review;
        the trials by family, the track record (how each family's and each maker's kept conclusions held on later
        days), the challengers (each forward check against the live configuration on the same days) and the lock box.
        With an id: that experiment whole, its hypothesis, plan, motivating days and criteria, each run (kind,
        trials, whether it used the lock box), its conclusion with its candidate's checks, and its forward check."""
        checks = forward_of_experiments()
        if experiment_id:
            found = await run_in_threadpool(journal.get, experiment_id)
            answer = {**found, "family_trials": await run_in_threadpool(journal.family_trials, found["family"])}
            if checks.get(experiment_id):
                answer["forward"] = {**(found.get("forward") or {}), **checks[experiment_id]}
            return compacted(answer)
        experiments = await run_in_threadpool(journal.experiments)
        families: dict[str, int] = {}
        for experiment in experiments:
            families[experiment["family"]] = families.get(experiment["family"], 0) + experiment["trials"]
        box = await run_in_threadpool(journal.lockbox)
        ordered = sorted(experiments, key=lambda experiment: (experiment["verdict"] is not None, experiment["opened_at"]))
        board = await run_in_threadpool(research.forward.board) if research.forward is not None else []
        return compacted({
            "experiments": [_experiment_line(experiment, checks.get(experiment["id"])) for experiment in ordered],
            "trials_by_family": families,
            "track_record": record(experiments, checks),
            "challengers": [{key: line.get(key) for key in (
                "check", "experiment", "title", "state", "days", "better_days", "worse_days", "sign_p", "interval",
                "challenger_percent", "champion_percent", "verdict")} for line in board],
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
    async def exp_conclude(experiment_id: str, verdict: str, notes: str, candidate: str = "") -> dict:
        """Conclude an experiment: verdict kept, rejected or inconclusive, and in notes what it rests on (days and
        trades it saw, random entries beaten, held-out and walk-forward results, neighbours, stress). Recorded turbos
        only start on 2026-10-13: say on how few days it rests. candidate: the run it keeps (one of its runs), needed
        for kept: its checks against the bar and the experiment's criteria are recorded with the conclusion, and a
        forward check starts, which judges it each evening on the days after it, which no run had seen (exp_get)."""
        found = await run_in_threadpool(journal.get, experiment_id)
        if verdict == "kept" and not candidate:
            raise Refused("A kept conclusion names its candidate: the run it keeps, which later days will judge")
        checks = None
        if candidate:
            service = research.service()
            belongs = any(run.get("run") == candidate for run in found["runs"]) or (
                await run_in_threadpool(service.request_of, candidate)).get("experiment") == experiment_id
            if not belongs:
                raise Refused(f"Backtest {candidate} is not one of experiment {experiment_id}'s runs")
            judged = await run_in_threadpool(service.judged, candidate)
            whole = await run_in_threadpool(service.get, candidate)
            whole["research"] = await run_in_threadpool(service.research, candidate)
            checks = {
                **_checks_said(judged),
                "criteria": criteria_met(found.get("criteria") or [], whole),
                "family": (whole["research"] or {}).get("family"),
                "motivated": (whole["research"] or {}).get("motivated"),
            }
        try:
            await run_in_threadpool(lambda: journal.conclude(experiment_id, verdict, notes, candidate=candidate or None,
                                                             checks=checks))
        except JournalError as e:
            raise Refused(str(e)) from None
        answer = {**_experiment_line(await run_in_threadpool(journal.get, experiment_id)), "checks": checks}
        if verdict == "kept" and research.forward is not None:
            try:
                started = await run_in_threadpool(
                    lambda: research.forward.start(experiment_id, candidate, by=made_via()))
                answer["forward"] = {"started": started["id"], "from": started["start"]}
            except NotPossible as e:
                answer["forward"] = {"refused": str(e)}
        return compacted(answer)

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
