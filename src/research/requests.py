"""
A backtest's request as the research method handles it: how many
configurations it tries, the request a finished run was asked with, what a
run had for a setting or an input, and the values around it.
"""

import copy


def trials_of(body: dict) -> int:
    """The configurations a request tries: one, or each combination of its sweep."""
    sweep = body.get("sweep")
    if not sweep:
        return 1
    count = 1
    for axis in sweep.get("axes") or []:
        count *= len(axis.get("values") or [])
    return count


def body_of(run: dict) -> dict:
    """The request a finished run was asked with, as the Backtests form would ask it again."""
    request = run["request"]
    source = request["source"]
    body = {
        key: request[key] for key in ("name", "tags", "from", "to", "indice") if key in request
    }
    body["changes"] = dict(request.get("changes") or {})
    if source["kind"] == "pine":
        rule = source["rule"]
        body["source"] = {
            "kind": "pine", "script": {"id": rule["script"]["id"], "version": rule["script"]["version"]},
            **{key: copy.deepcopy(rule[key]) for key in ("asset", "side", "bars", "inputs", "alerts", "alert_calls", "confidence_plot") if key in rule},
        }
    else:
        body["source"] = {"kind": "journal"}
        body["chart"] = copy.deepcopy(request["chart"])
    return body


def value_of(run: dict, parameter: str):
    """What a run had for a setting or for an input of its script (``in_N``)."""
    if parameter.startswith("in_"):
        rule = (run["request"]["source"] or {}).get("rule") or {}
        found = [entry["value"] for entry in rule.get("inputs") or [] if entry["id"] == parameter]
        if not found:
            raise ValueError(f"{parameter} is not an input the run gave its script: give its values")
        return found[0]
    settings = (run.get("settings") or {}).get("settings") or {}
    if parameter not in settings:
        raise ValueError(f"{parameter} is not a setting of a backtest")
    return settings[parameter]


def neighbour_values(value, *, step=None, count: int = 2, least=None, most=None) -> list:
    """
    The run's value with ``count`` steps on each side: a tenth of the value
    by default, a whole step for a whole number. Kept within the setting's limits.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{value!r} has no neighbours: give the values")
    whole = isinstance(value, int) and (step is None or float(step).is_integer())
    if step is None:
        step = max(1, round(abs(value) * 0.1)) if whole else (abs(value) * 0.1 or 0.1)
    found = []
    for k in range(-count, count + 1):
        candidate = value + k * step
        candidate = int(round(candidate)) if whole else round(candidate, 6)
        if (least is not None and candidate < least) or (most is not None and candidate > most):
            continue
        if candidate not in found:
            found.append(candidate)
    if len(found) < 2:
        raise ValueError("The setting's limits leave no neighbour: give the values")
    return found
