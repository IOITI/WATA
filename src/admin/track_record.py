"""
The research's track record: how the conclusions of each family of ideas, and of each maker (the admin's AI agent,
an MCP token), held on the days after them (src/admin/forward_checks.py).

A kept conclusion is a claim about days not yet seen; its forward check is the only test of it that no choice was fitted
to. The record counts, for each family and each maker: the experiments and their verdicts, the kept ones whose
candidate met the bar at its conclusion and those kept against it, and how their forward checks stand: held (as
expected, or better), watch, broken, too early to tell, or nothing to judge (no model, no edge over random entries).
A share is only given from ``LEAST_FOR_SHARE`` judged checks: two of three says nothing.

Pure: it reads the journal's experiments and each one's newest forward check, as given.
"""

HELD_VERDICTS = ("as_expected", "better_than_expected")
LEAST_FOR_SHARE = 5
AGENT = "the AI agent"


def maker_of(experiment: dict) -> str:
    """Who made an experiment: the admin's AI agent (any of its conversations), an MCP token by its name, or unknown."""
    via = experiment.get("made_via") or ""
    if via.startswith("agent:"):
        return AGENT
    return via or "unknown"


def forward_kind(check: dict | None) -> str | None:
    """How a forward check stands, in one word; None without a check."""
    if not check:
        return None
    verdict = check.get("verdict")
    if verdict in HELD_VERDICTS:
        return "held"
    if verdict in ("watch", "broken"):
        return verdict
    if verdict in (None, "too_early"):
        return "too_early"
    return "nothing_to_judge"


def _empty() -> dict:
    return {
        "experiments": 0, "trials": 0, "open": 0, "kept": 0, "rejected": 0, "inconclusive": 0,
        "kept_met_bar": 0, "kept_against_bar": 0,
        "forward": {"active": 0, "held": 0, "watch": 0, "broken": 0, "too_early": 0, "nothing_to_judge": 0},
        "held_share": None,
    }


def _count(group: dict, experiment: dict, check: dict | None) -> None:
    group["experiments"] += 1
    group["trials"] += experiment.get("trials") or 0
    verdict = experiment.get("verdict")
    if verdict is None:
        group["open"] += 1
    else:
        group[verdict] = group.get(verdict, 0) + 1
    if verdict == "kept":
        bar = (experiment.get("checks") or {}).get("bar")
        if bar == "pass":
            group["kept_met_bar"] += 1
        elif bar == "fail":
            group["kept_against_bar"] += 1
    kind = forward_kind(check)
    if kind is not None:
        group["forward"][kind] += 1
        if check.get("state") == "active":
            group["forward"]["active"] += 1


def _share(group: dict) -> None:
    forward = group["forward"]
    judged = forward["held"] + forward["watch"] + forward["broken"]
    group["held_share"] = round(forward["held"] / judged * 100) if judged >= LEAST_FOR_SHARE else None


def record(experiments: list[dict], checks: dict[str, dict] | None = None) -> dict:
    """
    The record of every family and every maker, the most tried first, and of all of them together. ``checks``: each
    experiment's newest forward check, in short (``ForwardChecks.of_experiments``).
    """
    checks = checks or {}
    families: dict[str, dict] = {}
    makers: dict[str, dict] = {}
    total = _empty()
    for experiment in experiments:
        check = checks.get(experiment.get("id"))
        for group in (families.setdefault(experiment.get("family") or "", _empty()),
                      makers.setdefault(maker_of(experiment), _empty()), total):
            _count(group, experiment, check)
    for group in [*families.values(), *makers.values(), total]:
        _share(group)

    def ordered(groups: dict[str, dict], key: str) -> list[dict]:
        return [{key: name, **group} for name, group in sorted(groups.items(), key=lambda item: (-item[1]["trials"], item[0]))]

    return {"families": ordered(families, "family"), "makers": ordered(makers, "maker"), "total": total}


def family_record(experiments: list[dict], checks: dict[str, dict] | None, family: str) -> dict:
    """One family's record: what an experiment opened in it is told."""
    found = next((entry for entry in record(experiments, checks)["families"] if entry["family"] == family), None)
    return found or {"family": family, **_empty()}
