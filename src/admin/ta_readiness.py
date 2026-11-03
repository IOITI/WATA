"""
Whether a signal rule is ready to go live, as the server holds it: what the
Signal rules page shows check by check (admin_ui/src/features/ta/signals/readiness.ts,
a change to one is made in the other) is no longer advice alone. A rule not
ready does not go live unless the operator goes live anyway, which the audit
trail records with what was missing: the switch stays the operator's.

Held: a week of weekdays in shadow (its signals journaled, never sent), a
signal journaled, no day on paper outside the range of its reference run, the
engine running it without fault, and its script as a rule (its rule check,
src/admin/rule_checks.py): not a script that cannot be one as it is, nor one
that repaints, nor one that may and was never tested as the rule runs. A newer
version of its script is only said. The week counts from the later of its going into shadow and the last
change of what it runs (``run_changed_at``: a script version, its inputs...),
whoever made it. Pure.
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


def _day(moment, zone) -> date:
    return datetime.fromisoformat(str(moment).replace("Z", "+00:00")).astimezone(zone).date()


# What keeps a script from being a rule as it is, by the rule check's worst finding.
CANNOT_BE = {
    "no-alerts": "no alert", "alert-messages": "no alert that makes a signal", "strategy": "a strategy with no alert",
    "ticker": "a series WATA does not have", "live-ticker": "a series WATA reads only in backtests",
    "no-data": "data WATA does not have", "fails-on-bars": "it fails on WATA's bars",
    "version": "it does not compile here", "library-missing": "a library WATA does not have",
}


def script_unmet(check: dict | None) -> str | None:
    """
    What a rule's script holds back, from its rule check's summary (``summary_of_rule`` of rule_checks.py):
    None when it holds nothing, or when there is no check to read (the checker not built here).
    """
    if not check:
        return None
    verdict, worst = check.get("verdict"), check.get("worst") or {}
    if verdict in ("blocked", "needs_alerts"):
        return f"its script cannot be a rule as it is ({CANNOT_BE.get(worst.get('id'), worst.get('id') or 'see its rule check')})"
    if verdict == "repaints":
        return "its script repaints: its alerts differ live and in a backtest"
    if verdict == "may_repaint" and check.get("as_rule"):
        # Tested as it runs, and no alert came there to compare, live or in a backtest.
        return "its script may repaint: its run test as this rule runs it had no alert to compare"
    if verdict == "may_repaint":
        return "its script may repaint, and was never tested on recorded bars as this rule runs it"
    return None


def unmet_readiness(rule: dict, state: dict | None, paper: dict | None, now: datetime, timezone_name: str,
                    rule_check: dict | None = None) -> list[str]:
    """What keeps ``rule`` from being ready to go live, in words; empty once it is."""
    zone = pytz.timezone(timezone_name)
    unmet = []
    if rule.get("mode") != "shadow":
        unmet.append("it is not in shadow: what it would send is not being journaled")
    else:
        moments = [moment for moment in (rule.get("mode_changed_at"), rule.get("run_changed_at")) if moment]
        since = max(_day(moment, zone) for moment in moments) if moments else None
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
    said = script_unmet(rule_check)
    if said:
        unmet.append(said)
    return unmet
