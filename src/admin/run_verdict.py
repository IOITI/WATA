"""
Whether a run can be believed, check by check: the bar the research is held to, in Python.

Three places say it and are kept in step: the pages (admin_ui/src/features/backtests/verdict.ts, ``listedChecks``),
this module (the MCP tools, the AI agent, a conclusion of the experiment journal), and the MCP's instructions
(``DO_NOT_BELIEVE`` in src/admin/mcp_server/instructions.py). A fixture both test suites read holds the first two to
the same answers (admin_ui/src/features/backtests/verdictParity.json): each check's id and state. Each side words
its own details.

A run is read as the list of runs describes it (``BacktestService.listed``): its headline, which the server fills
with what the checks read, and the research figures kept beside it (``research``, src/admin/run_research.py). The
runs made from it, or for it, that a check reads (its newest stress, the newest model check) are found among the
runs given, newest first, as the list gives them.

Pure: nothing here reads a file or changes a run.
"""

import math

# Fewer trades or days than these describe those days and nothing else.
MIN_TRADES = 30
MIN_DAYS = 20
# The share of runs at random entries (%) a run must do better than.
MIN_BEATEN = 95
# The edge of the entries: the strongest horizon of five is picked, so 1 % for each (t of 2.6), with 95 % of random
# moments beaten, and 10 signals with a price at least to say anything.
EDGE_T = 2.6
EDGE_BEATEN = 95
LEAST_SIGNALS = 10
# A run whose trades rest on what was not recorded for more than this share (%) is not believed.
MAX_PRICED_OFF_QUOTES = 10
# A sweep's choice: the probability it was fitted, and its deflated Sharpe ratio.
MAX_OVERFITTING = 0.5
MIN_DEFLATED_SHARPE = 0.95

PASS, OPEN, FAIL = "pass", "open", "fail"
DONE = "done"

# The figures an experiment's criteria may bound (src/research/experiments.py ``checked_criteria``), each read from a
# finished run (``figure_of``).
CRITERIA_FIGURES = (
    "total_percent", "trades", "days", "win_rate", "profit_factor", "worst_drawdown", "sharpe", "beaten", "edge_t",
    "held_out_percent", "walk_forward_percent", "pbo", "deflated_sharpe", "family_deflated",
)


def _plural(count, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _check(check_id: str, label: str, state: str, detail: str) -> dict:
    return {"id": check_id, "label": label, "state": state, "detail": detail}


# ── what the checks read ──

def is_judged(run: dict) -> bool:
    """
    Whether a run is judged at all: one not done, a stress sweep (of either kind), a model check or a forward check's
    run (they judge another run) is not.
    """
    return ((run.get("status") or {}).get("state") == DONE and not run.get("stress_of") and not run.get("noise_of")
            and not run.get("model_check_of") and not run.get("forward_of"))


def _evidence(run: dict) -> dict | None:
    headline = run.get("headline")
    if not isinstance(headline, dict):
        return None
    days = headline.get("days")
    beaten = headline.get("beaten")
    return {
        "kind": run.get("kind"),
        "turbos": run.get("turbos"),
        "stale": bool(run.get("stale")),
        "remade": bool(run.get("remade_by")),
        "figures": {"trades": headline.get("trades") or 0, "days": days, "sample": headline.get("sample")}
        if days is not None else None,
        "days_tested": headline.get("days_tested") or 0,
        "random": {"beaten": beaten, "repetitions": headline.get("repetitions")} if beaten is not None else None,
        "edge": headline.get("edge"),
        "pbo": headline.get("pbo"),
        "deflated": headline.get("deflated_sharpe"),
        "rests_on": headline.get("rests_on"),
        "research": run.get("research") if isinstance(run.get("research"), dict) else None,
    }


def newest_stress(runs: list[dict], run_id: str) -> dict | None:
    """The newest stress sweep of a run (the list is newest first)."""
    return next((entry for entry in runs if entry.get("stress_of") == run_id), None)


def newest_model_check(runs: list[dict]) -> dict | None:
    """The newest model check that is done, whichever run it checks."""
    return next((entry for entry in runs
                 if entry.get("model_check_of") and (entry.get("status") or {}).get("state") == DONE), None)


def derived_of(run: dict, runs: list[dict]) -> dict:
    """The runs a run's checks read, from the runs listed with it: its newest stress, the newest model check, its grid."""
    stress = newest_stress(runs, run.get("id"))
    check = newest_model_check(runs) if run.get("turbos") == "modelled" else None
    return {
        "stress": {"run": stress, "sweep": (stress.get("headline") or {}).get("grid")} if stress else None,
        "model_check": {"run": check, "found": (check.get("headline") or {}).get("model_check")} if check else None,
        "sweep": (run.get("headline") or {}).get("grid"),
    }


# ── the helpers the checks share with the pages ──

def headline_edge(edge: dict) -> dict:
    """
    The horizon that beats the random draws most, the t-statistic deciding between equals (``strongestOf`` of
    edge.ts), and whether it is an edge: 95 % of the draws beaten and a t of 2.6.
    """
    counted = [row for row in edge.get("horizons") or []
               if (row.get("signals") or 0) >= LEAST_SIGNALS and row.get("beaten") is not None]
    if not counted:
        return {"tone": "unknown", "minutes": None, "t": None, "beaten": None}
    best = counted[0]
    for row in counted[1:]:
        beaten, kept = row.get("beaten") or 0, best.get("beaten") or 0
        t = row.get("t") if row.get("t") is not None else -math.inf
        kept_t = best.get("t") if best.get("t") is not None else -math.inf
        if beaten > kept or (beaten == kept and t > kept_t):
            best = row
    holds = (best.get("beaten") or 0) >= EDGE_BEATEN and (best.get("t") or 0) >= EDGE_T
    return {"tone": "edge" if holds else "none", "minutes": best.get("minutes"), "t": best.get("t"),
            "beaten": best.get("beaten")}


def stress_holds(sweep: dict | None) -> bool | None:
    """
    Whether a stress sweep's run stays above zero with each order a step later and dearer (``stressVerdict`` of
    robustness.ts): its cell one step along both axes; None when it has no figure there.
    """
    if not isinstance(sweep, dict):
        return None
    axes = sweep.get("axes") or []
    if len(axes) < 2:
        return None
    width = len(axes[1].get("values") or [])
    totals = [((cell or {}).get("whole") or {}).get("total_percent") for cell in sweep.get("cells") or []]
    if not totals or totals[0] is None:
        return None
    step = totals[width + 1] if width + 1 < len(totals) else None
    return None if step is None else step > 0


def sweep_held(sweep: dict | None) -> bool | None:
    """
    Whether a sweep's best combination on the days it may be chosen on stayed in the better half on the days held out
    (``sweepVerdict`` of sweep.ts); None without days held out, or without its figures there.
    """
    if not isinstance(sweep, dict):
        return None

    def order(part: str) -> list[tuple[int, float]]:
        found = []
        for index, cell in enumerate(sweep.get("cells") or []):
            total = ((cell or {}).get(part) or {}).get("total_percent")
            if total is not None:
                found.append((index, total))
        # Stable, as the pages' sort is: equals keep their order.
        return sorted(found, key=lambda entry: -entry[1])

    tuning = order("tuning")
    if not tuning:
        return None
    best = tuning[0][0]
    held_out = order("held_out")
    if not held_out:
        return None
    rank = next((place for place, (index, _) in enumerate(held_out, start=1) if index == best), 0)
    if rank == 0:
        return None
    return rank <= math.ceil(len(held_out) / 2)


# ── the checks ──

def _sample_check(trades: int | None, days: int) -> dict:
    enough = (trades is None or trades >= MIN_TRADES) and days >= MIN_DAYS
    counted = _plural(days, "day") if trades is None else f"{_plural(trades, 'trade')} over {_plural(days, 'day')}"
    return _check("sample", "Enough trades and days", PASS if enough else FAIL,
                  f"{counted}." if enough else f"{counted}: under {MIN_TRADES} trades or {MIN_DAYS} days, the figures "
                                               "describe those days and nothing else.")


def _code_check(evidence: dict) -> dict:
    if not evidence["stale"]:
        return _check("code", "Made with today's code", PASS, "Made with the code the admin runs now.")
    return _check("code", "Made with today's code", OPEN,
                  "Made with other code than the admin runs now, and made again on it since: the run that did says "
                  "what holds." if evidence["remade"] else
                  "Made with other code than the admin runs now: make it again to see whether it still holds.")


def _rests_on_check(rests_on) -> dict:
    if not isinstance(rests_on, dict):
        return _check("data", "Priced at recorded quotes", OPEN,
                      "Not counted: the run was made before what its trades rest on was.")
    share = rests_on.get("share_percent") or 0
    if share > MAX_PRICED_OFF_QUOTES:
        return _check("data", "Priced at recorded quotes", FAIL,
                      f"{share:g} % of its trades rest on what was not recorded: over {MAX_PRICED_OFF_QUOTES} %.")
    return _check("data", "Priced at recorded quotes", PASS, f"{share:g} % of its trades rest on what was not recorded.")


def _family_check(research: dict | None) -> dict | None:
    """Its daily results' Sharpe ratio, deflated by every trial its family of ideas had been tried with until it."""
    family = (research or {}).get("family")
    if not isinstance(family, dict):
        return None
    probability = _number(family.get("probability"))
    trials = family.get("trials") or 1
    name = family.get("name") or "its family"
    if probability is None:
        return _check("family", "Survives its family's trials", OPEN,
                      f"Not measured: it takes {MIN_DAYS} days of results to correct for the {_plural(trials, 'trial')} "
                      f"of {name}.")
    state = PASS if probability >= MIN_DEFLATED_SHARPE else FAIL
    return _check("family", "Survives its family's trials", state,
                  f"{round(probability * 100)} % likely to be real after the {_plural(trials, 'trial')} of {name}"
                  + ("." if state == PASS else f" ({round(MIN_DEFLATED_SHARPE * 100)} % is the bar): the best of so many "
                                               "tries looks this good by chance."))


def _motivated_check(research: dict | None) -> dict | None:
    """Whether its result needs the days its experiment's idea came from."""
    motivated = (research or {}).get("motivated")
    if not isinstance(motivated, dict):
        return None
    inside = motivated.get("in_run") or 0
    days = motivated.get("days") or []
    if inside == 0:
        return _check("motivated", "Holds without the days its idea came from", PASS,
                      f"None of the {_plural(len(days), 'day')} its idea came from is among its days.")
    without = _number(motivated.get("without_percent"))
    if without is None:
        return _check("motivated", "Holds without the days its idea came from", FAIL,
                      "Every day it traded is one its idea came from: nothing else judges it.")
    state = PASS if without > 0 else FAIL
    return _check("motivated", "Holds without the days its idea came from", state,
                  f"Without the {_plural(inside, 'day')} its idea came from: {without:+.2f} %"
                  + ("." if state == PASS else ": the result needs the days that suggested it."))


def _single_checks(evidence: dict, derived: dict) -> list[dict]:
    figures = evidence["figures"]
    if not figures:
        return []
    checks = [_sample_check(figures["trades"], figures["days"]), _rests_on_check(evidence["rests_on"])]

    random = evidence["random"]
    if random is None:
        checks.append(_check("random", "Beats random entries", OPEN,
                             "No random entries were set against it: nothing tells its entries from the exit rules."))
    else:
        beaten = random["beaten"]
        state = PASS if beaten >= MIN_BEATEN else FAIL
        checks.append(_check("random", "Beats random entries", state,
                             f"Better than {beaten:g} % of {random.get('repetitions') or 'its'} runs at random entries"
                             + ("." if state == PASS else f": {MIN_BEATEN} % is the bar.")))

    edge = headline_edge(evidence["edge"]) if isinstance(evidence["edge"], dict) else None
    if edge is None:
        checks.append(_check("edge", "An edge in its entries", OPEN, "Not measured: the run was made before entries were."))
    elif edge["tone"] == "unknown":
        checks.append(_check("edge", "An edge in its entries", OPEN,
                             f"Too few entry signals with a price to tell (fewer than {LEAST_SIGNALS} at every horizon)."))
    else:
        state = PASS if edge["tone"] == "edge" else FAIL
        checks.append(_check("edge", "An edge in its entries", state,
                             f"At its strongest, after {edge['minutes']} min: t {edge['t'] or 0:.1f}, "
                             f"{edge['beaten'] or 0:g} % of random moments beaten"
                             + ("." if state == PASS else f" (t {EDGE_T} and {EDGE_BEATEN} % are the bar).")))

    for check in (_family_check(evidence["research"]), _motivated_check(evidence["research"])):
        if check is not None:
            checks.append(check)

    stress = derived.get("stress")
    held = stress_holds(stress.get("sweep")) if stress else None
    if not stress:
        detail = "Not stressed yet."
    elif ((stress["run"].get("status") or {}).get("state")) in ("queued", "running"):
        detail = "Being stressed."
    elif ((stress["run"].get("status") or {}).get("state")) != DONE:
        detail = "The stress did not finish: ask for it again."
    elif held is True:
        detail = "Still above zero with each order a step later and dearer."
    elif held is False:
        detail = "Below zero with each order a step later and dearer: it lives on its own fills."
    else:
        detail = "The stress gave no figure a step later and dearer."
    checks.append(_check("stress", "Survives later and dearer fills", PASS if held is True else FAIL if held is False else OPEN,
                         detail))

    if evidence["turbos"] == "modelled":
        found = derived.get("model_check")
        checks.append(_check("model", "Made-up turbos checked", PASS if found else OPEN,
                             "Checked on a run of the recorded turbos." if found else
                             "No run on recorded turbos has been made again on made-up ones: how far these are from "
                             "real turbos is not known."))
    checks.append(_code_check(evidence))
    return checks


def _sweep_checks(evidence: dict, derived: dict) -> list[dict]:
    checks = [_sample_check(None, evidence["days_tested"])]
    held = sweep_held(derived.get("sweep"))
    checks.append(_check(
        "held-out", "Holds on days held out", PASS if held is True else FAIL if held is False else OPEN,
        "The best combination stayed in the better half on days that had no say in the choice." if held is True else
        "The best combination fell to the worse half on days that had no say in the choice." if held is False else
        "No days were held out to tell."))
    pbo = evidence["pbo"]
    probability = _number((pbo or {}).get("probability"))
    if probability is None:
        checks.append(_check("overfitting", "Little chance of overfitting", OPEN, "Not measured: it takes 8 days."))
    else:
        state = PASS if probability <= MAX_OVERFITTING else FAIL
        checks.append(_check("overfitting", "Little chance of overfitting", state,
                             f"A probability of {probability:.2f} that the best was fitted"
                             + ("." if state == PASS else f" ({MAX_OVERFITTING} at most).")))
    deflated = evidence["deflated"]
    probability = _number((deflated or {}).get("probability"))
    if probability is None:
        checks.append(_check("deflated", "Its Sharpe ratio, deflated", OPEN, "Not measured: it takes 20 days to choose on."))
    else:
        state = PASS if probability >= MIN_DEFLATED_SHARPE else FAIL
        checks.append(_check("deflated", "Its Sharpe ratio, deflated", state,
                             f"{round(probability * 100)} % likely to be real after "
                             f"{_plural((deflated or {}).get('trials') or 1, 'trial')}"
                             + ("." if state == PASS else f" ({round(MIN_DEFLATED_SHARPE * 100)} % is the bar).")))
    checks.append(_code_check(evidence))
    return checks


def checks(run: dict, runs: list[dict] | None = None) -> list[dict]:
    """
    Every check a done run is held to (``listedChecks`` of verdict.ts), read from the run as the list describes it and
    the runs listed with it, newest first; none for what is not judged so.
    """
    if not is_judged(run):
        return []
    evidence = _evidence(run)
    if evidence is None:
        return []
    derived = derived_of(run, runs or [])
    return _sweep_checks(evidence, derived) if evidence["kind"] == "sweep" else _single_checks(evidence, derived)


def verdict_of(found: list[dict]) -> dict:
    """What the checks say together: a fail outweighs everything, a check left open is said."""
    def named(items):
        return ", ".join(item["label"].lower() for item in items)

    failed = [check for check in found if check["state"] == FAIL]
    open_ = [check for check in found if check["state"] == OPEN]
    if failed:
        return {"tone": FAIL, "sentence": f"Not to be believed yet: {named(failed)}."}
    if open_:
        said = "one check is" if len(open_) == 1 else f"{len(open_)} checks are"
        return {"tone": OPEN, "sentence": f"Nothing against it so far; {said} still open: {named(open_)}."}
    return {"tone": PASS, "sentence": "It passes every check the research is held to."}


def judged(run: dict, runs: list[dict] | None = None) -> dict | None:
    """A run's checks and what they say together; None for a run not judged."""
    found = checks(run, runs)
    return {"checks": found, "verdict": verdict_of(found)} if found else None


# ── an experiment's own criteria ──

def figure_of(figure: str, run: dict) -> float | None:
    """
    A figure an experiment's criteria may bound, read from a finished run as the list and its page describe it
    (``run`` with its ``summary`` when whole, its ``headline`` and ``research`` always); None when the run has none.
    """
    headline = run.get("headline") or {}
    summary = run.get("summary") or {}
    figures = summary.get("figures") or {}
    risk = summary.get("risk") or {}
    research = run.get("research") or {}
    if figure in ("total_percent", "trades", "win_rate", "worst_drawdown"):
        return _number(headline.get(figure) if headline.get(figure) is not None else figures.get(figure))
    if figure == "days":
        return _number(headline.get("days"))
    if figure == "profit_factor":
        return _number(figures.get("profit_factor"))
    if figure == "sharpe":
        return _number(risk.get("sharpe"))
    if figure == "beaten":
        return _number(headline.get("beaten"))
    if figure == "edge_t":
        edge = headline.get("edge")
        return _number(headline_edge(edge)["t"]) if isinstance(edge, dict) else None
    if figure == "held_out_percent":
        held = (summary.get("held_out") or {}) if isinstance(summary.get("held_out"), dict) else {}
        return _number(held.get("total_percent"))
    if figure == "walk_forward_percent":
        walk = summary.get("walk_forward") or {}
        return _number(walk.get("total_percent")) if isinstance(walk, dict) else None
    if figure == "pbo":
        return _number((headline.get("pbo") or {}).get("probability"))
    if figure == "deflated_sharpe":
        return _number((headline.get("deflated_sharpe") or {}).get("probability"))
    if figure == "family_deflated":
        return _number((research.get("family") or {}).get("probability"))
    return None


def criteria_met(criteria: list[dict], run: dict) -> list[dict]:
    """Each criterion an experiment set itself, with the run's figure and whether it was met (None: no figure)."""
    found = []
    for criterion in criteria or []:
        value = figure_of(criterion.get("figure"), run) if criterion.get("figure") in CRITERIA_FIGURES else None
        bound = criterion.get("bound")
        met = None if value is None else value >= criterion["value"] if bound == "at_least" else value <= criterion["value"]
        found.append({**criterion, "figure_value": value, "met": met})
    return found
