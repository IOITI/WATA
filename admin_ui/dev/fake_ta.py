"""
The Technical Analysis scripts of the fake admin server: the real store
(src/admin/ta_scripts.py) on the server's temporary folder, with a few examples
covering what the pages handle: an overlay with inputs and alert conditions,
an oscillator in its own pane, and a library with a script importing it. Two
signal rules run the first, one live and one of Claude's in shadow on ten-second
bars, and a made-up TA engine says what it did and what it measured of itself
(``FAKE_TA_TROUBLE=slow`` makes a live run slow now, for the Engine monitor's
warning and health's).
"""

import hashlib
import json
import math
import os
import random
from datetime import datetime, timedelta, timezone

from src.admin.ta_rules import LIVE, OFF, RuleStore, bars_label
from src.admin.ta_scripts import ScriptStore

EMA_CROSS = """//@version=6
indicator("EMA cross", overlay = true)

fastLength = input.int(9, "Fast length", minval = 1)
slowLength = input.int(21, "Slow length", minval = 1)

fast = ta.ema(close, fastLength)
slow = ta.ema(close, slowLength)
crossUp = ta.crossover(fast, slow)
crossDown = ta.crossunder(fast, slow)

plot(fast, "Fast EMA", color.teal, 2)
plot(slow, "Slow EMA", color.orange, 2)
plotshape(crossUp, "Cross up", shape.triangleup, location.belowbar, color.teal, size = size.small)
plotshape(crossDown, "Cross down", shape.triangledown, location.abovebar, color.red, size = size.small)

alertcondition(crossUp, "Cross up", '{"action": "long", "indice": "us100", "signal_timestamp": "{{time}}", "alert_timestamp": "{{timenow}}"}')
alertcondition(crossDown, "Cross down", '{"action": "short", "indice": "us100", "signal_timestamp": "{{time}}", "alert_timestamp": "{{timenow}}"}')
"""

RSI = """//@version=6
indicator("RSI", overlay = false)

length = input.int(14, "Length", minval = 2)
rsi = ta.rsi(close, length)

plot(rsi, "RSI", color.purple, 2)
hline(70, "Overbought", color.red)
hline(30, "Oversold", color.teal)
"""

BANDS_LIBRARY = """//@version=6
// @description Price bands around a moving average.
library("Bands")

// The width of a band, as a share of its basis.
width(float basis, float percent) =>
    basis * percent / 100

// @function The basis, upper and lower band around a simple moving average.
export envelope(series float source, simple int length, simple float percent) =>
    basis = ta.sma(source, length)
    [basis, basis + width(basis, percent), basis - width(basis, percent)]
"""

ENVELOPE = """//@version=6
indicator("Envelope", overlay = true)
import wata/Bands/1 as bands

length = input.int(20, "Length", minval = 2)
percent = input.float(0.2, "Percent", step = 0.05)

[basis, upper, lower] = bands.envelope(close, length, percent)
plot(basis, "Basis", color.gray)
upperPlot = plot(upper, "Upper", color.blue)
lowerPlot = plot(lower, "Lower", color.blue)
fill(upperPlot, lowerPlot, color.new(color.blue, 90), "Band")
"""

EXAMPLES = (
    ("EMA cross", EMA_CROSS, "Two moving averages, their crosses, and an alert for each."),
    ("RSI", RSI, "The relative strength index, in a pane of its own."),
    ("Bands", BANDS_LIBRARY, "A library: the envelope around a moving average."),
    ("Envelope", ENVELOPE, "The envelope of the Bands library, drawn on the price."),
)


def seeded_store(root: str) -> ScriptStore:
    store = ScriptStore(root)
    if not store.scripts():
        for name, source, description in EXAMPLES:
            store.create(name, source, description)
        seed_rule(store)
    return store


# ── a signal rule, and what a made-up TA engine did with the rules ──


# The rule traded on paper: the same id at every start, so that its days and reports, kept with the fake's
# backtests (var/lib/backtests), are not made again each time.
PAPER_RULE_ID = "00000000a1a1"
PAPER_RULE_DAYS = 10


def seed_rule(store: ScriptStore) -> None:
    """
    A live rule on the EMA cross, as one is made from the Signal rules page then switched, one of
    Claude's in shadow on ten-second bars, as the MCP endpoint makes them, and one in shadow on
    US100 since ten days, traded on paper (its days and its report).
    """
    rules = RuleStore(store)
    if rules.load()["rules"]:
        return
    ema = next(meta for meta in store.scripts() if meta["name"] == "EMA cross")
    fields = {
        "name": "EMA cross on US100",
        "asset": {"provider": "capital", "key": "US100"},
        "side": "bid",
        "bars": {"type": "time", "timeframe": "1"},
        "script": {"id": ema["id"], "version": ema["version"]},
        "inputs": [{"id": "in_0", "var": "fastLength", "title": "Fast length", "value": 12}],
        "alerts": [{"title": "Cross up", "action": "long"}, {"title": "Cross down", "action": "short"}],
        "alert_calls": "ignore",
        "confidence_plot": None,
        "indice": "us100",
    }
    made = rules.create(fields, rules.load()["revision"])
    rules.set_mode(made["rule"]["id"], LIVE, made["revision"])
    rules.create(
        {**fields, "name": "Claude · EMA cross 10 s", "bars": {"type": "time", "timeframe": "10S"}, "indice": "us500"},
        rules.load()["revision"], made_by="claude",
    )
    shadow = rules.create({**fields, "name": "EMA cross 9/21 in shadow", "inputs": []}, rules.load()["revision"])["rule"]
    made = (datetime.now(timezone.utc) - timedelta(days=PAPER_RULE_DAYS)).isoformat()
    with open(rules.path, encoding="utf-8") as in_file:
        listed = json.load(in_file)
    for rule in listed["rules"]:
        if rule["id"] == shadow["id"]:
            rule.update(id=PAPER_RULE_ID, created_at=made, updated_at=made, mode_changed_at=made)
    with open(rules.path, "w", encoding="utf-8") as out_file:
        json.dump(listed, out_file, indent=2)


def _rng(*parts) -> random.Random:
    return random.Random(int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:12], 16))


class FakeEngine:
    """
    What the TA engine would have written, made up from the rules of the store:
    a crossing every hour or two on market days, the trader's decision for the
    signals a live rule sent, and TradingView's signals near them for the comparison.
    """

    def __init__(self, store: ScriptStore, started_at: datetime):
        self._rules = RuleStore(store)
        self._started_at = started_at

    def _rules_now(self) -> list[dict]:
        return self._rules.load()["rules"]

    def _signals_of(self, rule: dict, day: datetime, now: datetime) -> list[dict]:
        rng = _rng(rule["id"], day.date())
        signals = []
        moment = day.replace(hour=7, minute=rng.randint(0, 50), second=0, microsecond=0)
        action = rng.choice(["long", "short"])
        titles = {entry["action"]: entry["title"] for entry in rule["alerts"]}
        while moment.hour < 20 and moment + timedelta(minutes=1) < now:
            closed = moment + timedelta(minutes=1)
            live = rule["mode"] == LIVE and moment >= now - timedelta(days=2)
            refused = live and rng.random() < 0.1
            signal = {
                # Unique across rules and days, as the journal's are.
                "id": int(moment.timestamp()) * 1000 + int(rule["id"][:6], 16) % 1000,
                "rule_id": rule["id"], "rule_name": rule["name"],
                "script_id": rule["script"]["id"], "script_version": rule["script"]["version"],
                "asset": f"capital/{rule['asset']['key']}", "side": rule["side"], "bars": bars_label(rule["bars"]),
                "bar_time": moment, "bar_closed_at": closed,
                "alert_key": f"alertcondition:{titles.get(action, action)}", "alert_title": titles.get(action, action),
                "alert_message": '{"action": "%s", "indice": "%s"}' % (action, rule["indice"]),
                "action": action, "indice": rule["indice"], "confidence": None,
                "mode": "live" if live else "shadow", "raised_at": closed + timedelta(seconds=1.4),
                "outcome": ("refused" if refused else "sent") if live else "shadow",
                "sent_at": closed + timedelta(seconds=1.6) if live and not refused else None,
                "http_status": 200 if live and not refused else None,
                "signal_uuid": f"{rule['id']}-{int(moment.timestamp())}" if live and not refused else None,
                "detail": "The bar closed 75 s ago, more than 60 s" if refused else None,
                "trader": None,
            }
            if signal["signal_uuid"]:
                executed = rng.random() < 0.7
                signal["trader"] = {
                    "decided_at": closed + timedelta(seconds=2.1), "outcome": "executed" if executed else "refused",
                    "reason": None if executed else "Max trades per day reached", "position_id": "5025%03d" % rng.randint(0, 999) if executed else None,
                }
            signals.append(signal)
            moment += timedelta(minutes=rng.randint(35, 140))
            action = "short" if action == "long" else "long"
        return signals

    def _days(self, now: datetime, start, end) -> list[datetime]:
        days = []
        for back in range(0, 10):
            day = (now - timedelta(days=back)).replace(hour=0, minute=0, second=0, microsecond=0)
            if day.weekday() >= 5:
                continue
            if (start and day.date() < start) or (end and day.date() > end):
                continue
            days.append(day)
        return days

    def signals(self, start=None, end=None, rule_id=None, limit=500) -> list[dict]:
        now = datetime.now(timezone.utc)
        found = []
        for rule in self._rules_now():
            if rule_id and rule["id"] != rule_id:
                continue
            for day in self._days(now, start, end):
                found += self._signals_of(rule, day, now)
        found.sort(key=lambda signal: signal["raised_at"], reverse=True)
        return found[:limit]

    def others(self, indice: str, day) -> list[dict]:
        """TradingView's signals of the day: near the rules' own, a few missing and one of its own."""
        now = datetime.now(timezone.utc)
        moment = datetime.combine(day, datetime.min.time(), timezone.utc)
        found = []
        for rule in self._rules_now():
            if rule["indice"] != indice:
                continue
            rng = _rng("tv", rule["id"], day)
            for signal in self._signals_of(rule, moment, now):
                if rng.random() < 0.15:
                    continue
                at = signal["bar_time"] + timedelta(minutes=rng.choice([0, 0, 1, -1]))
                found.append({
                    "signal_uuid": f"tv-{int(at.timestamp())}", "action": signal["action"], "indice": indice,
                    "confidence": None, "signal_timestamp": at, "received_timestamp": at + timedelta(seconds=62),
                    "decided_at": at + timedelta(seconds=63), "outcome": "executed", "reason": None, "position_id": None,
                })
            if found:
                extra = moment.replace(hour=15, minute=rng.randint(0, 59))
                if extra < now:
                    found.append({**found[-1], "signal_uuid": "tv-extra", "action": "close-position", "signal_timestamp": extra})
        return sorted(found, key=lambda signal: signal["signal_timestamp"])

    def state(self) -> dict:
        now = datetime.now(timezone.utc)
        on = [rule for rule in self._rules_now() if rule["mode"] != OFF]
        return {
            "updated_at": now - timedelta(seconds=3), "started_at": self._started_at, "version": "dev",
            "state": "running" if on else "idle", "detail": f"{len(on)} rule(s) on" if on else "No rule is on",
            "figures": {
                "rules": len(self._rules_now()), "live": sum(1 for rule in on if rule["mode"] == LIVE),
                "shadow": sum(1 for rule in on if rule["mode"] != LIVE), "failing": 0,
                "covered_until": int(now.timestamp()) // 10 * 10, "runner": {"pinets": "0.12.0", "node": "v24.21.0"},
                "runner_starts": 1,
                "monitoring": {"days": 7, "waiting": 0, "dropped": 0, "written_at": now.timestamp() - 3,
                               "purged_at": self._started_at.timestamp(), "error": None},
            },
        }

    def rule_states(self) -> dict:
        now = datetime.now(timezone.utc)
        states = {}
        for rule in self._rules_now():
            if rule["mode"] == OFF:
                states[rule["id"]] = {"updated_at": now, "state": "off", "detail": None, "mode": rule["mode"],
                                      "script_version": rule["script"]["version"], "bars": 0, "last_bar_time": None,
                                      "last_alert_at": None, "figures": {}}
                continue
            last = self.signals(rule_id=rule["id"], limit=1)
            states[rule["id"]] = {
                "updated_at": now, "state": "running", "detail": None, "mode": rule["mode"],
                "script_version": rule["script"]["version"], "bars": 1380,
                "last_bar_time": now.replace(second=0, microsecond=0) - timedelta(minutes=1),
                "last_alert_at": last[0]["raised_at"] if last else None, "figures": {"milliseconds": 41},
            }
        return states

    # ── what the engine measured of itself: the Engine monitor (AdminQueries' shapes) ──

    def _on(self) -> list[dict]:
        return [rule for rule in self._rules_now() if rule["mode"] != OFF]

    @staticmethod
    def _seconds_of(rule: dict) -> int:
        return 10 if rule["bars"].get("timeframe") == "10S" else 60

    def _run_ms(self, rule: dict, at: datetime) -> tuple[float, float, float]:
        """A rule's runs about ``at``: median, 95 %, slowest; slower as its bars pile up through the engine day."""
        hours = _hours_since_restart(at)
        if self._seconds_of(rule) == 10:
            median = 520 + 12 * hours
            slowest = 7200.0 if timedelta(hours=1, minutes=55) < datetime.now(timezone.utc) - at < timedelta(hours=2, minutes=5) else median * 2.2
            return median, median * 1.7, slowest
        median = 34 + 0.6 * hours
        if os.environ.get("FAKE_TA_TROUBLE") == "slow" and datetime.now(timezone.utc) - at < timedelta(minutes=10):
            return median, median * 2, 18_400.0
        return median, median * 1.9, median * 6

    def _tick(self, at: datetime) -> dict:
        """The ticks about ``at``: the memory grows through the engine day, the restart's loads, a slow and a late moment."""
        rng, hours = _rng("tick", int(at.timestamp()) // 600), _hours_since_restart(at)
        on = self._on()
        shadow = any(rule["mode"] != LIVE for rule in on)
        median = 48 + 22 * len(on) + rng.uniform(-6, 6)
        ago = datetime.now(timezone.utc) - at
        live_rss = 180 + 4.2 * hours + rng.uniform(-3, 3)
        shadow_rss = 142 + 6.5 * hours + rng.uniform(-3, 3) if shadow else None
        return {
            "tick_p50_ms": median, "tick_max_ms": round(median * 3.4 + rng.uniform(0, 120)),
            "slow": 4 if timedelta(hours=26) <= ago < timedelta(hours=26, minutes=10) else 0,
            "feed_lag_p50_ms": 11_500 + rng.uniform(-300, 300),
            "feed_lag_max_ms": 38_000.0 if timedelta(hours=30) <= ago < timedelta(hours=30, minutes=10) else 13_200 + rng.uniform(0, 900),
            "live_rss_mb": round(live_rss, 1), "shadow_rss_mb": round(shadow_rss, 1) if shadow_rss else None,
            "engine_rss_mb": 92.4, "memory_used_mb": round(live_rss + (shadow_rss or 0) + 92.4 + 64, 1), "memory_limit_mb": 2560.0,
        }

    def monitor_recent(self, since: datetime, slow_tick_ms: int) -> dict:
        now = datetime.now(timezone.utc)
        tick, on = self._tick(now), self._on()
        runs, deferred, checks = [], [], []
        for rule in on:
            median, _, slowest = self._run_ms(rule, now - timedelta(minutes=2))
            live = rule["mode"] == LIVE
            # Its continued run checked against a whole run every ten minutes.
            checks.append({
                "rule_id": rule["id"], "mode": rule["mode"], "runner": "live" if live else "shadow", "checks": 1, "differed": 0,
                "check_max_ms": round(slowest * 40), "last_difference": None,
            })
            runs.append({
                "rule_id": rule["id"], "mode": rule["mode"], "runner": "live" if live else "shadow",
                "runs": 900 // self._seconds_of(rule), "loads": 0, "append_max_ms": round(slowest), "load_max_ms": None,
                "failed": 0, "ended": 0, "last_failure": None, "age_max_ms": 1580.0, "delay_max_ms": 1580.0 + slowest,
            })
            if not live and self._seconds_of(rule) == 10:
                deferred.append({"rule_id": rule["id"], "ticks": 4})
        signals = {}
        for signal in self.signals(since.date(), now.date()):
            if signal["mode"] == LIVE and signal["raised_at"] >= since:
                key = (signal["rule_id"], signal["outcome"], signal["detail"] if signal["outcome"] in ("refused", "failed") else None)
                signals[key] = signals.get(key, 0) + 1
        last = {
            "tick_at": now - timedelta(seconds=3), "ended_at": now - timedelta(seconds=2.8), "milliseconds": round(tick["tick_p50_ms"]),
            "read_ms": 4, "rows_read": 1, "write_ms": 3, "covered_until": now - timedelta(seconds=14.5), "rules_on": len(on),
            "runs": len(on), "loads": 0, "failed": 0, "deferred_rules": [], "skipped": 0, "restarted": False, "error": None,
            "runner_starts": 9, "shadow_runner_starts": 8, "live_rss_mb": tick["live_rss_mb"], "live_peak_mb": tick["live_rss_mb"] + 18,
            "shadow_rss_mb": tick["shadow_rss_mb"], "shadow_peak_mb": tick["shadow_rss_mb"] and tick["shadow_rss_mb"] + 12,
            "engine_rss_mb": 92.4, "memory_used_mb": tick["memory_used_mb"], "memory_limit_mb": 2560.0, "oom_kills": 0,
        }
        return {
            "last_tick": last, "kept_since": now - timedelta(days=7),
            "ticks": {
                "ticks": 90, "tick_p50_ms": tick["tick_p50_ms"], "tick_max_ms": tick["tick_max_ms"], "steady_tick_max_ms": tick["tick_max_ms"],
                "slow_ticks": 0, "feed_lag_max_ms": tick["feed_lag_max_ms"], "memory_used_max_mb": tick["memory_used_mb"] + 6,
                "memory_limit_mb": 2560.0, "oom_kills": 0, "deferred": sum(row["ticks"] for row in deferred),
            },
            "runs": runs, "deferred": deferred, "checks": checks,
            "signals": [{"rule_id": rule_id, "outcome": outcome, "detail": detail, "signals": count} for (rule_id, outcome, detail), count in signals.items()],
        }

    def monitor_period(self, start: datetime, end: datetime, bucket_seconds: int, rule_id: str | None, signal_age_seconds: int) -> dict:
        now = datetime.now(timezone.utc)
        kept_since = now - timedelta(days=7)
        on = [rule for rule in self._on() if rule_id in (None, rule["id"])]
        ticks, runs_by_bucket = [], []
        starts = 1
        moment = max(start, kept_since.replace(second=0, microsecond=0))
        moment = datetime.fromtimestamp(math.floor(moment.timestamp() / bucket_seconds) * bucket_seconds, timezone.utc)
        while moment < end:
            upto = min(moment + timedelta(seconds=bucket_seconds), now)
            seconds = (upto - moment).total_seconds()
            restart = moment.replace(hour=21, minute=15, second=0, microsecond=0)
            restarted = moment <= restart < upto
            before = starts
            if restarted:
                starts += 1
            if timedelta(hours=50) <= now - moment < timedelta(hours=50) + timedelta(seconds=bucket_seconds):
                starts += 1
            tick = self._tick(moment)
            ticks.append({
                "t": int(moment.timestamp()), "ticks": int(seconds // 10), **tick,
                "tick_max_ms": 3800 if restarted else tick["tick_max_ms"], "restarted": restarted,
                "runner_starts_min": before, "runner_starts_max": starts,
                "deferred": int(seconds // 200) if any(self._seconds_of(rule) == 10 and rule["mode"] != LIVE for rule in self._on()) else 0,
            })
            figures = [(rule, self._run_ms(rule, moment), int(seconds // self._seconds_of(rule))) for rule in on]
            if figures:
                busiest = max(figures, key=lambda entry: entry[2])
                runs_by_bucket.append({
                    "t": int(moment.timestamp()), "runs": sum(count for _, _, count in figures), "loads": len(figures) if restarted else 0,
                    "failed": 0, "run_p50_ms": busiest[1][0], "run_p95_ms": busiest[1][1],
                    "run_max_ms": round(max(ms[2] for _, ms, _ in figures)),
                    "delay_p50_ms": 1580 + busiest[1][0], "delay_max_ms": 1580 + max(ms[2] for _, ms, _ in figures),
                })
            moment += timedelta(seconds=bucket_seconds)
        rules, deferred, checks = [], [], []
        for rule in on:
            median, p95, slowest = self._run_ms(rule, end)
            per = self._seconds_of(rule)
            spanned = (end - max(start, kept_since)).total_seconds()
            # The rule on ten-second bars had a continued run a whole run gave otherwise, three days ago.
            differed = 1 if per == 10 and spanned > 3 * 86_400 else 0
            checks.append({
                "rule_id": rule["id"], "mode": rule["mode"], "runner": "live" if rule["mode"] == LIVE else "shadow",
                "checks": max(1, int(spanned // 600)), "differed": differed, "check_max_ms": 9800 if per == 10 else 2900,
                "last_difference": 'plot "plot_2" at 2026-10-20T13:41:10Z: 25012.5 continued, 25012.25 whole' if differed else None,
            })
            rules.append({
                "rule_id": rule["id"], "runs": int(spanned // per), "loads": max(1, int(spanned // 86_400)),
                "continued": int(spanned // per) - (int(spanned // per) // 3 if differed else 0),
                "failed": 1 if per == 10 and spanned > 3 * 86_400 else 0, "ended": 0,
                "run_p50_ms": median * 0.8, "run_p95_ms": p95, "run_max_ms": round(max(slowest, 7200 if per == 10 and spanned > 2 * 3600 else slowest)),
                "load_max_ms": 9800 if per == 10 else 2900, "pinets_avg_ms": median * 0.8 - 4, "bars_max": 8640 if per == 10 else 1380,
                "asks": 0, "delay_p50_ms": 1580 + median * 0.8, "delay_p95_ms": 1580 + p95, "delay_max_ms": 1580 + slowest,
                "age_max_ms": 1590.0, "last_run_at": now - timedelta(seconds=3),
                "last_failure": "Undeclared identifier 'slowLength' (script, line 12)" if per == 10 and spanned > 3 * 86_400 else None,
            })
            if per == 10 and rule["mode"] != LIVE:
                deferred.append({"rule_id": rule["id"], "ticks": int(spanned // 200)})
        signals, by_bucket, live_sent = {}, {}, []
        for signal in self.signals(start.date(), end.date(), rule_id, limit=5000):
            if not start <= signal["raised_at"] < end:
                continue
            key = (signal["rule_id"], signal["mode"], signal["outcome"], signal["detail"] if signal["outcome"] in ("refused", "failed") else None)
            signals[key] = signals.get(key, 0) + 1
            bucket = int(signal["raised_at"].timestamp()) // bucket_seconds * bucket_seconds
            counts = by_bucket.setdefault(bucket, {"t": bucket, "sent": 0, "not_sent": 0, "shadow": 0})
            if signal["mode"] != LIVE:
                counts["shadow"] += 1
            elif signal["outcome"] == "sent":
                counts["sent"] += 1
                live_sent.append(signal)
            else:
                counts["not_sent"] += 1
        return {
            "kept_since": kept_since, "ticks": ticks, "runs_by_bucket": runs_by_bucket,
            "signals_by_bucket": [by_bucket[key] for key in sorted(by_bucket)], "rules": rules, "deferred": deferred, "checks": checks,
            "signals": [{"rule_id": rule_id_, "mode": mode, "outcome": outcome, "detail": detail, "signals": count, "late": 0}
                        for (rule_id_, mode, outcome, detail), count in signals.items()],
            "steps": _steps(live_sent), "trader": _trader(live_sent), "results": _results(live_sent),
        }


def _hours_since_restart(at: datetime) -> float:
    restart = at.replace(hour=21, minute=15, second=0, microsecond=0)
    if restart > at:
        restart -= timedelta(days=1)
    return (at - restart).total_seconds() / 3600


def _spread(values: list[float]) -> dict:
    ordered = sorted(values)
    at = lambda share: ordered[min(int(len(ordered) * share), len(ordered) - 1)]  # noqa: E731
    return {"signals": len(ordered), "p50": at(0.5), "p95": at(0.95), "max": ordered[-1]}


def _steps(sent: list[dict]) -> list[dict]:
    """The delivery steps of the live signals sent, each drawn the same way for the same signal."""
    samples: dict[str, list[float]] = {}
    for signal in sent:
        rng = _rng("steps", signal["signal_uuid"])
        tick = 1400 + rng.uniform(0, 300)
        wait, run, send = rng.uniform(1, 4), rng.uniform(30, 90), rng.uniform(25, 160)
        drawn = {"close_to_tick": tick, "tick_to_run": wait, "run": run, "run_to_sent": send, "close_to_sent": tick + wait + run + send}
        if (signal.get("trader") or {}).get("outcome") == "executed":
            order, saxo, position = rng.uniform(700, 1400), rng.uniform(180, 420), rng.uniform(800, 1900)
            drawn |= {"sent_to_order": order, "order_to_saxo": saxo, "saxo_to_position": position,
                      "close_to_position": drawn["close_to_sent"] + order + saxo + position}
        elif signal.get("trader"):
            drawn["sent_to_decided"] = rng.uniform(400, 900)
        for step, value in drawn.items():
            samples.setdefault(step, []).append(value)
    return [{"step": step, **_spread(values)} for step, values in samples.items()]


def _trader(sent: list[dict]) -> list[dict]:
    counts: dict[tuple, int] = {}
    for signal in sent:
        decision = signal.get("trader") or {}
        key = (decision.get("outcome"), decision.get("reason") if decision.get("outcome") in ("refused", "failed") else None)
        counts[key] = counts.get(key, 0) + 1
    return [{"outcome": outcome, "reason": reason, "signals": count} for (outcome, reason), count in counts.items()]


def _results(sent: list[dict]) -> list[dict]:
    by_rule: dict[str, dict] = {}
    for signal in sent:
        if (signal.get("trader") or {}).get("outcome") != "executed":
            continue
        rng = _rng("result", signal["signal_uuid"])
        entry = by_rule.setdefault(signal["rule_id"], {"rule_id": signal["rule_id"], "positions": 0, "open": 0, "won": 0, "lost": 0,
                                                        "profit_loss": 0.0, "percents": []})
        entry["positions"] += 1
        if datetime.now(timezone.utc) - signal["raised_at"] < timedelta(minutes=40):
            entry["open"] += 1
            continue
        percent = rng.uniform(-2.5, 3.5)
        entry["won" if percent > 0 else "lost"] += 1
        entry["profit_loss"] += round(percent * 0.9, 2)
        entry["percents"].append(percent)
    return [
        {**{key: value for key, value in entry.items() if key != "percents"},
         "average_percent": sum(entry["percents"]) / len(entry["percents"]) if entry["percents"] else None}
        for entry in by_rule.values()
    ]
