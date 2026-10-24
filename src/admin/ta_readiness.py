"""
Whether a signal rule is ready to go live, as the server holds it: what the
Signal rules page shows check by check (admin_ui/src/features/ta/signals/readiness.ts,
a change to one is made in the other) is no longer advice alone. A rule not
ready does not go live unless the operator goes live anyway, which the audit
trail records with what was missing: the switch stays the operator's.

Held: a week of weekdays in shadow (its signals journaled, never sent), a
signal journaled, no day on paper outside the range of its reference run, and
the engine running it without fault. A newer version of its script is only
said. Pure.
"""

from datetime import date, datetime, timedelta

import pytz

# Weekdays in shadow before a rule goes live: a week of the engine's signals.
SHADOW_DAYS = 5


def weekdays_between(start: date, end: date) -> int:
    """The weekdays after ``start`` up to ``end`` included."""
    count, day = 0, start + timedelta(days=1)
    while day <= end:
        if day.weekday() < 5:
            count += 1
        day += timedelta(days=1)
    return count


def unmet_readiness(rule: dict, state: dict | None, paper: dict | None, now: datetime, timezone_name: str) -> list[str]:
    """What keeps ``rule`` from being ready to go live, in words; empty once it is."""
    zone = pytz.timezone(timezone_name)
    unmet = []
    if rule.get("mode") != "shadow":
        unmet.append("it is not in shadow: what it would send is not being journaled")
    else:
        changed = rule.get("mode_changed_at")
        since = datetime.fromisoformat(str(changed).replace("Z", "+00:00")).astimezone(zone).date() if changed else None
        days = weekdays_between(since, now.astimezone(zone).date()) if since else 0
        if days < SHADOW_DAYS:
            unmet.append(f"{days} weekday(s) in shadow of {SHADOW_DAYS}")
    if not (state or {}).get("last_alert_at"):
        unmet.append("no signal journaled yet")
    outside = [day for day in (paper or {}).get("days") or [] if day.get("outside")]
    if outside:
        unmet.append(f"{len(outside)} day(s) on paper outside the range of its reference run")
    if (state or {}).get("state") == "error":
        unmet.append(f"the engine fails to run it ({(state or {}).get('detail') or 'no more said'})")
    return unmet
