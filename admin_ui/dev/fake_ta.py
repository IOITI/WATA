"""
The Technical Analysis scripts of the fake admin server: the real store
(src/admin/ta_scripts.py) on the server's temporary folder, with a few examples
covering what the pages handle: an overlay with inputs and alert conditions,
an oscillator in its own pane, and a library with a script importing it. A
signal rule in shadow runs the first, and a made-up TA engine says what it did.
"""

import hashlib
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


def seed_rule(store: ScriptStore) -> None:
    """A rule in shadow on the EMA cross, as one is made from the Signal rules page."""
    rules = RuleStore(store)
    if rules.load()["rules"]:
        return
    ema = next(meta for meta in store.scripts() if meta["name"] == "EMA cross")
    rules.create(
        {
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
        },
        rules.load()["revision"],
    )


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
