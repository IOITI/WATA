"""
A chart the agent draws under its answer (show_chart), from figures its tools
gave: a line over time or bars by category, four series at most (the compare
colours the pages validated for colour-blind readers), drawn by the admin's own
charts. Checked here, pure: numbers finite, moments ISO, each point's keys its
series', the whole small enough to keep with the request.
"""

import json
import math
import re
from datetime import datetime

LINE, BAR = "line", "bar"
MAX_SERIES = 4
MAX_POINTS = 2000
MAX_CATEGORIES = 200
MAX_REFERENCES = 4
# What one request may draw, and the size of one chart as kept with it.
MAX_CHARTS = 4
MAX_BYTES = 64_000
KEY = re.compile(r"^[a-z][a-z0-9_]{0,23}$")


class ChartRefused(Exception):
    """A chart that cannot be drawn, said in a sentence for the agent."""


def _number(value, where: str):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ChartRefused(f"{where} is not a number: give finite numbers, or null for a gap")
    return value


def _moment(value, where: str) -> str:
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise ChartRefused(f"{where} is not a moment: give YYYY-MM-DD or an ISO date and time") from None
    return value if isinstance(value, str) else moment.isoformat()


def checked_chart(arguments: dict) -> dict:
    """The chart as the pages draw it, or ChartRefused saying why not."""
    kind = arguments["kind"]
    series = arguments["series"]
    keys = [entry["key"] for entry in series]
    if len(set(keys)) != len(keys) or "t" in keys or "label" in keys:
        raise ChartRefused("Each series has a key of its own, other than t and label")
    chart = {"kind": kind, "title": arguments["title"].strip(), "unit": (arguments.get("unit") or "").strip() or None,
             "series": [{"key": entry["key"], "label": entry["label"].strip()} for entry in series],
             "source": arguments["source"].strip()}
    if kind == LINE:
        points = arguments.get("points") or []
        if len(points) < 2:
            raise ChartRefused("A line needs points: two at least, each {t, <series key>: value}")
        drawn = []
        for index, point in enumerate(points):
            extra = set(point) - {"t", *keys}
            if extra:
                raise ChartRefused(f"Point {index} has keys no series has: {', '.join(sorted(extra))}")
            if "t" not in point:
                raise ChartRefused(f"Point {index} has no moment t")
            drawn.append({"t": _moment(point["t"], f"t of point {index}"),
                          **{key: _number(point.get(key), f"{key} of point {index}") for key in keys}})
        chart["points"] = sorted(drawn, key=lambda point: point["t"])
        chart["zero"] = bool(arguments.get("zero", False))
    else:
        categories = arguments.get("categories") or []
        if not categories:
            raise ChartRefused("Bars need categories: each {label, <series key>: value}")
        drawn = []
        for index, category in enumerate(categories):
            extra = set(category) - {"label", *keys}
            if extra:
                raise ChartRefused(f"Category {index} has keys no series has: {', '.join(sorted(extra))}")
            if not isinstance(category.get("label"), str) or not category["label"].strip():
                raise ChartRefused(f"Category {index} has no label")
            drawn.append({"label": category["label"].strip(),
                          **{key: _number(category.get(key), f"{key} of {category['label']!r}") for key in keys}})
        chart["categories"] = drawn
        if arguments.get("references"):
            raise ChartRefused("References are lines across a line chart: say them in words beside bars")
        tone = arguments.get("tone", "series")
        if tone == "sign" and len(series) != 1:
            raise ChartRefused("Bars coloured by their sign are one series")
        chart["tone"] = tone
    chart["references"] = [{"value": _number(entry["value"], "A reference"), "label": entry.get("label")}
                           for entry in arguments.get("references") or []]
    if len(json.dumps(chart)) > MAX_BYTES:
        raise ChartRefused(f"The chart is over {MAX_BYTES // 1000} kB: fewer points, or a coarser step")
    return chart
