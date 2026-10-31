"""
What WATA knows of LuxAlgo's indicators, by slug, for the LuxAlgo Library's
pages and the AI agent's luxalgo_known: the scripts taken from each
(src/admin/script_origins.py, those still there), the backtests of those
scripts, the signal rules that run them with their days on paper, the agent's
verdicts on the indicator and on those runs and rules, and what WATA's check
found (src/admin/luxalgo_compat.py). Read from the admin's own files alone:
nothing here asks LuxAlgo.
"""

from src.admin.agent.verdicts import VerdictStore
from src.admin.ta_rules import RuleStore

# The runs and paper days said of each indicator at most.
RUNS_SAID = 10
PAPER_DAYS_SAID = 5


def _short(verdict: dict | None) -> dict | None:
    return {key: verdict.get(key) for key in ("tone", "line", "at", "conversation")} if verdict else None


def _compat(entry: dict | None) -> dict | None:
    if not entry:
        return None
    return {key: entry.get(key) for key in ("state", "checked_at", "compiles", "error", "rules_errors",
                                            "alertconditions", "alert_calls", "strategy", "plots", "inputs")
            if entry.get(key) is not None}


def known(ctx, slugs: list[str] | None = None, *, whole: bool = True) -> dict[str, dict]:
    """
    By slug, what WATA knows: every slug it touched when ``slugs`` is None.
    ``whole`` False: what the Library's cards say (counts, the verdict, the
    check), without reading the runs one by one.
    """
    origins = ctx.script_origins.all() if getattr(ctx, "script_origins", None) is not None else {}
    scripts = {meta["id"]: meta for meta in ctx.ta_scripts.scripts()} if ctx.ta_scripts is not None else {}
    taken: dict[str, list[dict]] = {}
    for script_id, origin in origins.items():
        slug = origin.get("slug")
        if origin.get("provider") != "luxalgo" or not slug or script_id not in scripts:
            continue
        meta = scripts[script_id]
        taken.setdefault(slug, []).append({
            "id": script_id, "name": meta["name"], "made_by": meta.get("made_by"), "version": meta["version"],
            "taken_at": origin.get("taken_at"), "by": origin.get("by"),
        })
    compat = ctx.luxalgo_compat.all() if getattr(ctx, "luxalgo_compat", None) is not None else {}
    verdicts = VerdictStore(ctx.settings.agent_path)
    judged = {about.split(":", 1)[1]: verdict for about, verdict in verdicts.all().items()
              if about.startswith("luxalgo:")}
    wanted = list(dict.fromkeys(slugs if slugs is not None else [*taken, *judged, *compat]))
    script_ids = {script["id"]: slug for slug in wanted for script in taken.get(slug, [])}

    rules = RuleStore(ctx.ta_scripts).load()["rules"] if ctx.ta_scripts is not None and script_ids else []
    running = [rule for rule in rules if rule["script"]["id"] in script_ids]
    runs = []
    if script_ids and getattr(ctx, "backtests", None) is not None and ctx.settings.backtests_enabled:
        runs = [run for run in ctx.backtests.list_runs()
                if ((run.get("source") or {}).get("script") or {}).get("id") in script_ids]
    paper = (ctx.backtests.paper(with_reports=False).get("rules") or {}) if running and whole and getattr(
        ctx, "backtests", None) is not None else {}
    said = verdicts.of([f"run:{run['id']}" for run in runs] + [f"rule:{rule['id']}" for rule in running])

    answer = {}
    for slug in wanted:
        mine = {script["id"] for script in taken.get(slug, [])}
        its_runs = [run for run in runs if run["source"]["script"]["id"] in mine]
        its_rules = [rule for rule in running if rule["script"]["id"] in mine]
        entry = {
            "slug": slug, "scripts": taken.get(slug, []), "verdict": _short(judged.get(slug)),
            "compat": _compat(compat.get(slug)),
        }
        if not whole:
            entry.update(runs=len(its_runs), rules=[{"id": rule["id"], "mode": rule["mode"]} for rule in its_rules])
            answer[slug] = entry
            continue
        entry["runs"] = [{
            "id": run["id"], "name": run.get("name"), "script_id": run["source"]["script"]["id"],
            "state": (run.get("status") or {}).get("state"), "headline": run.get("headline"),
            "made_by": run.get("made_by"), "verdict": _short(said.get(f"run:{run['id']}")),
        } for run in its_runs[:RUNS_SAID]]
        entry["more_runs"] = max(0, len(its_runs) - RUNS_SAID)
        entry["rules"] = []
        for rule in its_rules:
            days = (paper.get(rule["id"]) or {}).get("days") or []
            entry["rules"].append({
                "id": rule["id"], "name": rule["name"], "mode": rule["mode"], "made_by": rule.get("made_by"),
                "script_id": rule["script"]["id"], "verdict": _short(said.get(f"rule:{rule['id']}")),
                "paper": {"days": len(days), "outside": sum(1 for day in days if day.get("outside")),
                          "last": [{key: day.get(key) for key in ("day", "day_percent", "trades", "outside")}
                                   for day in days[:PAPER_DAYS_SAID]]},
            })
        answer[slug] = entry
    return answer
