"""
Reading the execution timings the trader writes for each trade it places
(``execution_timing_<date>.jsonl`` in the log directory).
"""

import json
import os
import re
from datetime import date, timedelta
from statistics import median

_FILE_PATTERN = re.compile(r"^execution_timing_(\d{4})-(\d{2})-(\d{2})\.jsonl$")
MAX_ENTRIES = 1000

# The marks of a trade, in the order the trader reaches them. Each value is the
# time since the trade started, in milliseconds.
STEPS = (
    "scale_calculated",
    "turbo_found",
    "spending_power_fetched",
    "amount_calculated",
    "order_placed",
    "position_confirmed",
    "db_persisted",
)


def _timing_files(log_path: str, since: date) -> list[str]:
    try:
        names = os.listdir(log_path)
    except OSError:
        return []
    files = []
    for name in names:
        match = _FILE_PATTERN.match(name)
        if not match:
            continue
        try:
            day = date(*(int(part) for part in match.groups()))
        except ValueError:
            continue
        if day >= since:
            files.append(name)
    return sorted(files, reverse=True)


def _percentile(sorted_values: list[float], fraction: float) -> float:
    return sorted_values[min(int(len(sorted_values) * fraction), len(sorted_values) - 1)]


def read_execution_timings(log_path: str, days: int = 30, today: date | None = None) -> dict:
    """Trades of the last ``days`` days, newest first, with how long each took."""
    since = (today or date.today()) - timedelta(days=days - 1)
    entries = []
    for name in _timing_files(log_path, since):
        day_entries = []
        with open(os.path.join(log_path, name), "r", errors="replace") as timing_file:
            for line in timing_file:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                steps = record.get("steps")
                if not isinstance(record, dict) or not isinstance(steps, dict):
                    continue
                marks = {
                    step: steps[step] for step in STEPS if isinstance(steps.get(step), (int, float))
                }
                day_entries.append({
                    "timestamp": record.get("timestamp"),
                    "action": record.get("action"),
                    "confidence": record.get("confidence"),
                    "position_scale": record.get("position_scale"),
                    "steps": marks,
                    "total_ms": max(marks.values()) if marks else None,
                })
        entries.extend(reversed(day_entries))
        if len(entries) >= MAX_ENTRIES:
            break
    entries = entries[:MAX_ENTRIES]

    totals = sorted(entry["total_ms"] for entry in entries if entry["total_ms"] is not None)
    return {
        "steps": list(STEPS),
        "entries": entries,
        "summary": {
            "trade_count": len(entries),
            "median_total_ms": median(totals) if totals else None,
            "p90_total_ms": _percentile(totals, 0.9) if totals else None,
            "slowest_total_ms": totals[-1] if totals else None,
        },
    }
