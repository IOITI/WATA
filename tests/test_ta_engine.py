"""
The TA engine (src/ta_engine): its bars against the admin's charts', the rules
it reads, what an alert becomes, the runner's protocol, and the engine's guards:
a shadow rule never sends, history never sends, a stale bar or feed never sends.
"""

import asyncio
import hashlib
import json
import os
import random
import shutil
import sys
import textwrap
import types
from datetime import datetime, timedelta, timezone

import pytest

asyncpg_stub = types.ModuleType("asyncpg")
asyncpg_stub.Pool = object
asyncpg_stub.Record = dict
asyncpg_stub.create_pool = None
sys.modules.setdefault("asyncpg", asyncpg_stub)

from src.admin.data import aggregate  # noqa: E402
from src.admin.prices import range_bars, range_rows, range_volumes  # noqa: E402
from src.admin.ta_rules import RuleStore  # noqa: E402
from src.admin.ta_scripts import ScriptStore  # noqa: E402
from src.ta_engine.bars import RangeBars, TimeBars  # noqa: E402
from src.ta_engine.engine import Engine  # noqa: E402
from src.ta_engine.rules import read_rules  # noqa: E402
from src.ta_engine.runner import NodeRunner, RunnerDown, RunnerFailed, find_node, runner_command  # noqa: E402
from src.ta_engine.sender import Sent  # noqa: E402
from src.ta_engine.settings import TaEngineSettings  # noqa: E402
from src.ta_engine.signals import interpret, iso_seconds, payload  # noqa: E402
from src.ta_engine.tickers import Ticker, parse_ticker  # noqa: E402

# Thursday 15 Oct 2026, 08:00:00 UTC.
T0 = 1792051200
SCRIPT = """//@version=6
indicator("Cross")
fast = input.int(5, "Fast")
plot(ta.ema(close, fast), "Fast")
plot(close / 10000, "Confidence")
alertcondition(ta.crossover(ta.ema(close, fast), ta.ema(close, 13)), title = "Up", message = '{"action": "long", "indice": "us100"}')
alertcondition(ta.crossunder(ta.ema(close, fast), ta.ema(close, 13)), title = "Down", message = '{"action": "short"}')
"""


def ten_second_rows(start: int, count: int, seed: int = 7, *, with_ask: bool = True) -> list[tuple]:
    rng = random.Random(seed)
    price = 25_000.0
    rows = []
    for index in range(count):
        opened = price
        closed = round(opened + rng.uniform(-6, 6), 1)
        high = round(max(opened, closed) + rng.uniform(0, 2), 1)
        low = round(min(opened, closed) - rng.uniform(0, 2), 1)
        price = closed
        ask = (round(opened + 1.2, 1), round(high + 1.2, 1), round(low + 1.2, 1), round(closed + 1.2, 1))
        if not with_ask or index % 17 == 5:
            ask = (None, None, None, None)
        rows.append((start + 10 * index, opened, high, low, closed, *ask, rng.randint(1, 30)))
    return rows


# ── tickers ──


@pytest.mark.parametrize("text, expected", [
    ("CAPITAL_US100", Ticker("capital", "US100")),
    ("CAPITAL:capital_us100.ask", Ticker("capital", "US100", "ask")),
    ("DUKASCOPY_US100.R60.ASK", Ticker("dukascopy", "us100", "ask", 60)),
    ("SAXO_12.R5", Ticker("saxo", "12", "bid", 5)),
    ("CAPITAL_US100.R0", None),
    ("SKILLING:US100", None),
    ("SAXO_0", None),
])
def test_tickers_are_read_as_the_pages_read_them(text, expected):
    assert parse_ticker(text) == expected
    if expected is not None:
        assert parse_ticker(expected.text()) == expected


# ── bars, as the admin's charts make them ──


@pytest.mark.parametrize("seconds", [60, 300, 900, 3600])
@pytest.mark.parametrize("side", ["bid", "ask"])
def test_bars_of_minutes_are_the_charts_bars(seconds, side):
    rows = ten_second_rows(T0 - 37 * 60, 2000)
    covered = rows[-1][0] + 10

    built = []
    builder = TimeBars(seconds, side)
    for start in range(0, len(rows), 37):
        built += builder.add(rows[start:start + 37], rows[min(start + 37, len(rows)) - 1][0] + 10)

    expected = []
    for bucket in aggregate(rows, seconds, 100_000):
        prices = bucket[1:5] if side == "bid" else bucket[5:9]
        if None in prices or bucket[0] + seconds > covered:
            continue
        expected.append([bucket[0] * 1000, *prices, bucket[9], (bucket[0] + seconds) * 1000])
    assert built == expected and len(built) > 1


def test_a_bucket_is_final_once_all_its_ten_seconds_are_recorded():
    rows = ten_second_rows(T0, 9)
    builder = TimeBars(60, "bid")

    assert builder.add(rows[:6], T0 + 50) == []
    assert [bar[0] for bar in builder.add([], T0 + 60)] == [T0 * 1000]
    # The next minute is not over before the feed says so.
    assert builder.add(rows[6:], T0 + 90) == []


@pytest.mark.parametrize("side", ["bid", "ask"])
@pytest.mark.parametrize("ticks", [20, 60, 150])
def test_range_bars_are_the_charts_range_bars(side, ticks):
    rows = ten_second_rows(T0, 3000, seed=11)
    # A pause in the data: the bar in progress ends there.
    rows = rows[:1500] + [(row[0] + 3600, *row[1:]) for row in rows[1500:]]

    builder = RangeBars(ticks, 1, side)
    built = []
    for start in range(0, len(rows), 50):
        built += builder.add(rows[start:start + 50])

    present = [row for row in rows if side == "bid" or row[5] is not None]
    columns = slice(1, 5) if side == "bid" else slice(5, 9)
    times = [row[0] for row in present]
    charts = range_bars(
        times, *zip(*[row[columns] for row in present]), ticks=ticks, decimals=1, limit=100_000,
    )
    volumes = range_volumes(charts, times, [row[9] for row in present])
    ended = [row for row, bar in zip(range_rows(charts, 1, volumes=volumes), charts) if bar is not charts[-1] or bar.complete]
    expected = [[row[0] * 1000, row[1], row[2], row[3], row[4], row[10], row[6] * 1000] for row in ended]
    # The chart's last bar is still growing: the engine waits for it to end.
    assert built == expected[:len(built)] and len(expected) - len(built) <= 1 and len(built) > 10
    assert any(not bar.complete for bar in charts[:-1])


# ── settings ──


def test_the_engine_is_off_unless_switched_on():
    class Config:
        def __init__(self, values):
            self.values = values

        def get_config_value(self, key, default=None):
            return self.values.get(key, default)

    assert TaEngineSettings.from_config(Config({})) == TaEngineSettings()
    assert TaEngineSettings().enabled is False and TaEngineSettings().history_days == 1
    settings = TaEngineSettings.from_config(Config({
        "trade.config.ta_engine": {"enabled": True, "history_days": 3, "restart_time": "22:05"},
        "trade.config.signal_polling": {"service_url": "http://web_server1:80/"},
    }))
    assert (settings.enabled, settings.history_days, settings.restart_minute) == (True, 3, 22 * 60 + 5)
    assert settings.service_url == "http://web_server1:80"
    for wrong in ({"enabled": "yes"}, {"history_days": 31}, {"restart_time": "9:15"}, {"max_signal_age_seconds": 1}):
        with pytest.raises(ValueError, match="trade.config.ta_engine"):
            TaEngineSettings.from_config(Config({"trade.config.ta_engine": wrong}))


# ── the rules, as the engine reads them ──


@pytest.fixture
def scripts(tmp_path):
    return ScriptStore(str(tmp_path / "ta"))


def make_rule(scripts, *, mode="shadow", name="Cross on US100", **changes):
    script = next((meta for meta in scripts.scripts() if meta["name"] == "Cross"), None) or scripts.create("Cross", SCRIPT)
    rules = RuleStore(scripts)
    fields = {
        "name": name,
        "asset": {"provider": "capital", "key": "US100"},
        "side": "bid",
        "bars": {"type": "time", "timeframe": "1"},
        "script": {"id": script["id"], "version": script["version"]},
        "inputs": [{"id": "in_0", "value": 8}],
        "alerts": [{"title": "Up", "action": "long"}, {"title": "Down", "action": "message"}],
        "alert_calls": "ignore",
        "confidence_plot": None,
        "indice": "us100",
        **changes,
    }
    created = rules.create(fields, rules.load()["revision"])["rule"]
    if mode != "shadow":
        rules.set_mode(created["id"], mode, rules.load()["revision"])
    return rules.rule(created["id"])


def test_the_engine_reads_each_rule_with_its_pinned_sources(scripts):
    rule = make_rule(scripts)

    snapshot = read_rules(scripts.root)

    assert [engine_rule.id for engine_rule in snapshot.rules] == [rule["id"]]
    engine_rule = snapshot.rules[0]
    assert engine_rule.source == SCRIPT and engine_rule.inputs() == {"in_0": 8}
    assert engine_rule.ticker().text() == "CAPITAL_US100" and engine_rule.timeframe() == "1"
    assert snapshot.errors == {} and snapshot.contested == frozenset()


def test_a_source_changed_on_disk_is_not_run(scripts):
    rule = make_rule(scripts)
    with open(os.path.join(scripts.root, "scripts", rule["script"]["id"], "v0001.pine"), "a") as source:
        source.write("// edited\n")

    snapshot = read_rules(scripts.root)

    assert snapshot.rules == () and "changed on disk" in snapshot.errors[rule["id"]]


def test_a_rule_edited_by_hand_into_something_wrong_is_not_run(scripts):
    rule = make_rule(scripts)
    path = os.path.join(scripts.root, "rules.json")
    stored = json.load(open(path))
    stored["rules"][0]["bars"] = {"type": "time", "timeframe": "240"}
    json.dump(stored, open(path, "w"))

    snapshot = read_rules(scripts.root)

    assert snapshot.rules == () and "timeframe" in snapshot.errors[rule["id"]]


def test_two_live_rules_on_one_indice_contest_it(scripts):
    make_rule(scripts, mode="live")
    second = make_rule(scripts, name="Second", indice="de40")
    path = os.path.join(scripts.root, "rules.json")
    stored = json.load(open(path))
    for entry in stored["rules"]:
        entry["mode"], entry["indice"] = "live", "us100"
    json.dump(stored, open(path, "w"))

    assert read_rules(scripts.root).contested == frozenset({"us100"})
    assert second["id"]


# ── what an alert becomes ──

BAR = [(T0 - 60) * 1000, 25_000.0, 25_010.5, 24_990.0, 25_004.2, 42, T0 * 1000]
RULE = {
    "id": "0123456789ab", "indice": "us100", "alert_calls": "ignore", "confidence_plot": None,
    "alerts": [{"title": "Up", "action": "long"}, {"title": "Down", "action": "message"}, {"title": "Quiet", "action": "ignore"}],
}


def alert(title, message="", kind="alertcondition", **more):
    return {"type": kind, "id": f"{kind}_0", "title": title, "message": message, "time": BAR[0], **more}


def test_an_alert_is_mapped_by_its_title_or_ignored():
    long = interpret(RULE, alert("Up"), bar=BAR, ticker="CAPITAL_US100", timeframe="1", plots={}, now=T0 + 2)

    assert (long.action, long.key, long.confidence, long.detail) == ("long", "alertcondition:Up", None, None)
    assert interpret(RULE, alert("Quiet"), bar=BAR, ticker="X", timeframe="1", plots={}, now=T0) is None
    assert interpret(RULE, alert("Unknown"), bar=BAR, ticker="X", timeframe="1", plots={}, now=T0) is None
    assert interpret(RULE, alert(None, "x", kind="alert"), bar=BAR, ticker="X", timeframe="1", plots={}, now=T0) is None


def test_a_message_is_read_as_webhook_json_with_tradingviews_placeholders():
    message = '{"action": "short", "indice": "us100", "confidence": {{plot("Conf")}}, "at": "{{time}}", "close": {{close}}}'
    plots = {"Fast": {str(BAR[0]): 1.0}, "Conf": {str(BAR[0]): 0.75}}

    candidate = interpret(RULE, alert("Down", message), bar=BAR, ticker="CAPITAL_US100", timeframe="1", plots=plots, now=T0 + 2)

    assert (candidate.action, candidate.confidence, candidate.detail) == ("short", 0.75, None)
    assert json.loads(candidate.message)["at"] == iso_seconds(T0 - 60)
    assert json.loads(candidate.message)["close"] == 25_004.2


@pytest.mark.parametrize("message, detail", [
    ("not json", "not JSON"),
    ('["long"]', "not a JSON object"),
    ('{"action": "close-long"}', "'close-long' is not one WATA sends"),
    ('{"action": "long", "indice": "de40"}', "for 'de40', the rule for 'us100'"),
])
def test_a_message_that_is_not_a_signal_says_why(message, detail):
    candidate = interpret(RULE, alert("Down", message), bar=BAR, ticker="X", timeframe="1", plots={}, now=T0)

    assert candidate.action is None and detail in candidate.detail


def test_a_confidence_out_of_range_is_left_out_and_said():
    rule = {**RULE, "confidence_plot": "Conf"}
    plots = {"Conf": {str(BAR[0]): 7.5}}

    candidate = interpret(rule, alert("Up"), bar=BAR, ticker="X", timeframe="1", plots=plots, now=T0)

    assert candidate.action == "long" and candidate.confidence is None and "outside 0 to 2" in candidate.detail


def test_wata_stamps_its_signals_with_the_bars_close_in_whole_seconds():
    candidate = interpret(RULE, alert("Up"), bar=BAR, ticker="X", timeframe="1", plots={}, now=T0 + 2.7)

    body = payload(RULE, candidate, bar_closed_at=BAR[6] / 1000, now=T0 + 2.7)

    assert body == {
        "action": "long", "indice": "us100",
        "signal_timestamp": iso_seconds(T0), "alert_timestamp": iso_seconds(T0 + 2),
    }


# ── the runner's protocol, with a stand-in for Node ──

FAKE_RUNNER = textwrap.dedent("""
    import json, sys, time
    for line in sys.stdin:
        message = json.loads(line)
        if "answer" in message:
            continue
        op = message.get("op")
        if op == "hello":
            print(json.dumps({"id": message["id"], "ok": True, "op": "hello", "pinets": "x", "node": "y"}), flush=True)
        elif op == "load":
            print(json.dumps({"ask": 1, "ticker": "CAPITAL_DE40", "timeframe": "1", "from": None, "to": None, "limit": None}), flush=True)
            answer = json.loads(sys.stdin.readline())
            print(json.dumps({"id": message["id"], "ok": True, "op": "load", "bars": len(answer.get("bars", [])),
                              "milliseconds": 1, "alerts": [], "plots": {}, "error_seen": answer.get("error")}), flush=True)
        elif op == "fail":
            print(json.dumps({"id": message["id"], "ok": False, "error": {"message": "Bad", "file": "script", "line": 3, "column": 1}}), flush=True)
        elif op == "slow":
            time.sleep(5)
        elif op == "die":
            sys.exit(3)
""")


@pytest.fixture
def fake_runner(tmp_path):
    path = tmp_path / "fake_runner.py"
    path.write_text(FAKE_RUNNER)
    return [sys.executable, str(path)]


def test_the_runner_client_answers_the_series_a_script_asks_for(fake_runner):
    asked = []

    async def answer(ask):
        asked.append(ask["ticker"])
        return [[1, 2, 3, 1, 2, 4, 5]]

    async def scenario():
        runner = NodeRunner(fake_runner, answer, timeout_seconds=2)
        versions = await runner.start()
        loaded = await runner.request({"op": "load"})
        await runner.stop()
        return versions, loaded

    versions, loaded = asyncio.run(scenario())

    assert versions == {"pinets": "x", "node": "y"}
    assert loaded["bars"] == 1 and asked == ["CAPITAL_DE40"]


def test_a_refusal_a_timeout_and_an_end_are_told_apart(fake_runner):
    async def answer(ask):
        raise ValueError("WATA has no live bars of it")

    async def scenario():
        runner = NodeRunner(fake_runner, answer, timeout_seconds=0.5)
        await runner.start()
        loaded = await runner.request({"op": "load"})
        with pytest.raises(RunnerFailed, match=r"Bad \(script, line 3\)"):
            await runner.request({"op": "fail"})
        with pytest.raises(RunnerDown, match="longer than 0.5 s"):
            await runner.request({"op": "slow"})
        stopped_after_timeout = not runner.running
        await runner.start()
        with pytest.raises(RunnerDown, match="ended"):
            await runner.request({"op": "die"})
        with pytest.raises(RunnerDown, match="not running"):
            await runner.request({"op": "hello"})
        return loaded, stopped_after_timeout, runner.starts

    loaded, stopped_after_timeout, starts = asyncio.run(scenario())

    assert loaded["error_seen"] == "WATA has no live bars of it"
    assert stopped_after_timeout and starts == 2


def test_the_runner_is_started_confined():
    command = runner_command("/usr/bin/node", "/app/src/ta_engine/runner/runner.mjs", 512)

    assert command == [
        "/usr/bin/node", "--max-old-space-size=512", "--permission",
        "--allow-fs-read=/app/src/ta_engine/runner", "/app/src/ta_engine/runner/runner.mjs",
    ]


# ── the engine, with stand-ins for the database, the runner and the web server ──


class Clock:
    def __init__(self, at):
        self.at = float(at)

    def __call__(self):
        return self.at


class Source:
    def __init__(self, rows, covered):
        self.rows_by_time = list(rows)
        self.covered = covered
        self.reads = []

    async def instrument(self, epic):
        return {"id": 1, "decimals": 1} if epic == "US100" else None

    async def rows(self, instrument_id, start, end):
        self.reads.append((start, end))
        return [row for row in self.rows_by_time if start <= row[0] < end]

    async def covered_until(self):
        return self.covered


class Runner:
    """The runner as the engine sees it: an alert "Up" on each bar a test names."""

    def __init__(self):
        self.running = False
        self.requests = []
        self.alerting = set()
        self.failing = None
        self.versions = {"pinets": "0.11.1", "node": "v24"}
        self.starts = 0

    async def start(self):
        self.running, self.starts = True, self.starts + 1
        return self.versions

    async def stop(self):
        self.running = False

    async def request(self, message):
        self.requests.append(message)
        if self.failing is not None and message["op"] == self.failing[0]:
            raise self.failing[1]
        if message["op"] == "unload":
            return {"ok": True}
        bars = message["bars"]
        alerts = [
            {"type": "alertcondition", "id": "alertcondition_0", "title": "Up", "message": "", "time": bar[0]}
            for bar in bars if bar[0] in self.alerting
        ]
        return {"ok": True, "bars": len(bars), "milliseconds": 3, "alerts": alerts, "plots": {}, "warnings": []}


class Journal:
    def __init__(self):
        self.signals = []
        self.completed = []
        self.states = []
        self.rule_states = []
        self._keys = set()

    async def journal_signal(self, signal):
        key = (signal["rule_id"], signal["bar_time"], signal["alert_key"])
        if key in self._keys:
            return None
        self._keys.add(key)
        self.signals.append(signal)
        return len(self.signals)

    async def complete_signal(self, signal_id, outcome, **details):
        self.completed.append((signal_id, outcome, details))

    async def write_state(self, state):
        self.states.append(state)

    async def write_rule_states(self, states):
        self.rule_states.append(states)


class Sender:
    def __init__(self):
        self.sent = []
        self.answer = Sent(True, 200, "5e1f2a3b-0000-4000-8000-000000000001")

    async def send(self, body):
        self.sent.append(body)
        return self.answer


class Telegram:
    def __init__(self):
        self.messages = []

    async def __call__(self, message):
        self.messages.append(message)


def engine_with(scripts, *, mode="shadow", now=T0 + 1.5, settings=None, **rule_changes):
    rule = make_rule(scripts, mode=mode, **rule_changes)
    clock = Clock(now)
    # From the restart before (14 Oct, 21:15 UTC) to now.
    rows = ten_second_rows(T0 - 11 * 3600, 11 * 360 + 30)
    source = Source(rows, covered=T0)
    runner, journal, sender, telegram = Runner(), Journal(), Sender(), Telegram()
    engine = Engine(
        settings=settings or TaEngineSettings(enabled=True, history_days=0),
        rules=lambda: read_rules(scripts.root), source=source, runner=runner, journal=journal, sender=sender,
        notify=telegram, clock=clock, version="test",
    )
    return engine, rule, clock, source, runner, journal, sender, telegram


def run(coroutine):
    return asyncio.run(coroutine)


def test_a_rule_is_loaded_on_its_history_whose_alerts_are_never_signals(scripts):
    engine, rule, clock, source, runner, journal, sender, _ = engine_with(scripts, mode="live")
    runner.alerting = {(T0 - 120) * 1000}

    run(engine.tick())

    load = runner.requests[0]
    assert load["op"] == "load" and load["rule"] == rule["id"] and load["ticker"] == "CAPITAL_US100"
    # History: from the restart before (21:15 UTC, history_days 0) to the last final minute.
    assert load["bars"][0][0] == (T0 - 10 * 3600 - 45 * 60) * 1000
    assert load["bars"][-1][6] == T0 * 1000
    assert journal.signals == [] and sender.sent == []
    assert journal.rule_states[-1][0]["state"] == "running"


def test_a_shadow_rules_signals_are_journaled_and_never_sent(scripts):
    engine, rule, clock, source, runner, journal, sender, _ = engine_with(scripts)
    run(engine.tick())
    runner.alerting = {T0 * 1000}
    source.rows_by_time += ten_second_rows(T0, 6, seed=3)
    source.covered, clock.at = T0 + 60, T0 + 61.5

    run(engine.tick())

    assert runner.requests[-1]["op"] == "append" and len(runner.requests[-1]["bars"]) == 1
    [signal] = journal.signals
    assert (signal["mode"], signal["outcome"], signal["action"], signal["alert_title"]) == ("shadow", "shadow", "long", "Up")
    assert signal["bar_time"] == datetime.fromtimestamp(T0, timezone.utc)
    assert signal["bar_closed_at"] == datetime.fromtimestamp(T0 + 60, timezone.utc)
    assert (signal["asset"], signal["bars"], signal["script_version"]) == ("capital/US100", "1", 1)
    assert sender.sent == [] and journal.completed == []


def test_a_live_rules_signal_is_sent_stamped_with_its_bars_close(scripts):
    engine, rule, clock, source, runner, journal, sender, _ = engine_with(scripts, mode="live")
    run(engine.tick())
    runner.alerting = {T0 * 1000}
    source.rows_by_time += ten_second_rows(T0, 6, seed=3)
    source.covered, clock.at = T0 + 60, T0 + 61.5

    run(engine.tick())

    assert sender.sent == [{
        "action": "long", "indice": "us100",
        "signal_timestamp": iso_seconds(T0 + 60), "alert_timestamp": iso_seconds(T0 + 61),
    }]
    [(signal_id, outcome, details)] = journal.completed
    assert (outcome, details["http_status"], details["signal_uuid"]) == ("sent", 200, "5e1f2a3b-0000-4000-8000-000000000001")
    assert journal.signals[0]["outcome"] == "sending"


def test_a_bar_evaluated_again_is_sent_once(scripts):
    engine, rule, clock, source, runner, journal, sender, _ = engine_with(scripts, mode="live")
    bar = [T0 * 1000, 1, 2, 0.5, 1.5, 3, (T0 + 60) * 1000]
    answer = {"alerts": [{"type": "alertcondition", "id": "alertcondition_0", "title": "Up", "message": "", "time": T0 * 1000}], "plots": {}}
    run(engine.tick())
    clock.at = T0 + 61.5
    engine._covered = T0 + 60
    run_ = next(iter(engine._runs.values()))

    run(engine._signals(run_, answer, [bar], clock.at))
    run(engine._signals(run_, answer, [bar], clock.at))

    assert len(sender.sent) == 1 and len(journal.signals) == 1


@pytest.mark.parametrize("late, covered_lag, refusal", [
    (75, 0, "The bar closed 75 s ago"),
    (2, 300, "feed is stale"),
])
def test_a_late_bar_or_a_stale_feed_sends_nothing(scripts, late, covered_lag, refusal):
    engine, rule, clock, source, runner, journal, sender, telegram = engine_with(scripts, mode="live")
    run(engine.tick())
    runner.alerting = {T0 * 1000}
    source.rows_by_time += ten_second_rows(T0, 6, seed=3)
    source.covered = T0 + 60
    clock.at = T0 + 60 + late + covered_lag
    if covered_lag:
        engine._settings = TaEngineSettings(enabled=True, history_days=0, max_signal_age_seconds=3600)

    run(engine.tick())

    assert sender.sent == []
    [(signal_id, outcome, details)] = journal.completed
    assert outcome == "refused" and refusal in details["detail"]
    assert any("not sent" in message for message in telegram.messages)


def test_an_action_wata_does_not_send_is_journaled_as_refused(scripts):
    engine, rule, clock, source, runner, journal, sender, _ = engine_with(scripts, mode="live")
    answer = {
        "alerts": [{"type": "alertcondition", "id": "alertcondition_1", "title": "Down", "message": '{"action": "close-long"}', "time": T0 * 1000}],
        "plots": {},
    }
    run(engine.tick())
    run_ = next(iter(engine._runs.values()))

    run(engine._signals(run_, answer, [[T0 * 1000, 1, 2, 0.5, 1.5, 3, (T0 + 60) * 1000]], T0 + 61))

    assert sender.sent == [] and journal.signals[0]["outcome"] == "refused" and journal.signals[0]["action"] is None


def test_a_signal_the_web_server_did_not_take_is_said_on_telegram(scripts):
    engine, rule, clock, source, runner, journal, sender, telegram = engine_with(scripts, mode="live")
    sender.answer = Sent(False, 503, None, "unavailable")
    run(engine.tick())
    runner.alerting = {T0 * 1000}
    source.rows_by_time += ten_second_rows(T0, 6, seed=3)
    source.covered, clock.at = T0 + 60, T0 + 61.5

    run(engine.tick())

    assert journal.completed[0][1] == "failed" and journal.completed[0][2]["http_status"] == 503
    assert any("could not be sent (unavailable)" in message for message in telegram.messages)


def test_a_rule_switched_to_live_keeps_running_and_one_whose_inputs_changed_is_loaded_again(scripts):
    engine, rule, clock, source, runner, journal, sender, _ = engine_with(scripts)
    run(engine.tick())
    rules = RuleStore(scripts)
    rules.set_mode(rule["id"], "live", rules.load()["revision"])
    run(engine.tick())
    assert [request["op"] for request in runner.requests] == ["load"]

    changed = {key: rule[key] for key in ("name", "asset", "side", "bars", "alerts", "alert_calls", "confidence_plot", "indice")}
    rules.update(rule["id"], {**changed, "script": {"id": rule["script"]["id"], "version": 1}, "inputs": [{"id": "in_0", "value": 9}]},
                 rules.load()["revision"])
    run(engine.tick())

    assert [request["op"] for request in runner.requests] == ["load", "unload", "load"]
    assert runner.requests[-1]["inputs"] == {"in_0": 9}


def test_a_rule_switched_off_is_unloaded_and_said_off(scripts):
    engine, rule, clock, source, runner, journal, sender, _ = engine_with(scripts)
    run(engine.tick())
    rules = RuleStore(scripts)
    rules.set_mode(rule["id"], "off", rules.load()["revision"])

    run(engine.tick())

    assert runner.requests[-1] == {"op": "unload", "rule": rule["id"]}
    assert journal.rule_states[-1][0]["state"] == "off"
    assert journal.states[-1]["state"] == "idle"


def test_a_script_that_does_not_run_is_said_and_tried_again_later(scripts):
    engine, rule, clock, source, runner, journal, sender, telegram = engine_with(scripts)
    runner.failing = ("load", RunnerFailed({"message": "Undeclared identifier", "file": "script", "line": 4}))

    run(engine.tick())
    states = journal.rule_states[-1]
    runner.failing = None
    clock.at += 60
    run(engine.tick())
    still_waiting = [request["op"] for request in runner.requests]
    clock.at += 300
    run(engine.tick())

    assert states[0]["state"] == "error" and "Undeclared identifier (script, line 4)" in states[0]["detail"]
    assert any("does not run" in message for message in telegram.messages)
    assert still_waiting == ["load"] and [request["op"] for request in runner.requests] == ["load", "load"]


def test_a_runner_that_ended_is_started_again_and_every_rule_loaded_again(scripts):
    engine, rule, clock, source, runner, journal, sender, telegram = engine_with(scripts)
    run(engine.tick())
    runner.failing = ("append", RunnerDown("The Pine runner ended (exit code 9)"))
    source.rows_by_time += ten_second_rows(T0, 6, seed=3)
    source.covered, clock.at = T0 + 60, T0 + 61.5

    run(engine.tick())
    runner.failing, runner.running = None, False
    clock.at += 400
    run(engine.tick())

    assert runner.starts == 2 and runner.requests[-1]["op"] == "load"
    assert any("ended" in message for message in telegram.messages)


def test_each_day_at_restart_time_every_rule_starts_again_from_fewer_bars(scripts):
    engine, rule, clock, source, runner, journal, sender, _ = engine_with(scripts, settings=TaEngineSettings(enabled=True, history_days=0))
    run(engine.tick())
    first = runner.requests[-1]["bars"][0][0]
    # 15 Oct, 21:15 UTC: past the next restart.
    restart = T0 + 13 * 3600 + 15 * 60
    source.rows_by_time += ten_second_rows(T0, 13 * 360 + 120, seed=5)
    source.covered, clock.at = restart + 600, restart + 601.5

    run(engine.tick())

    assert runner.requests[-1]["op"] == "load" and runner.requests[-1]["bars"][0][0] == restart * 1000 > first
    assert runner.starts == 2


def test_the_engine_says_what_it_does(scripts):
    engine, rule, clock, source, runner, journal, sender, _ = engine_with(scripts, mode="live")

    run(engine.tick())
    source.covered, clock.at = T0, T0 + 600

    run(engine.tick())

    assert journal.states[0]["state"] == "running" and journal.states[0]["figures"]["live"] == 1
    assert journal.states[0]["figures"]["runner"] == {"pinets": "0.11.1", "node": "v24"}
    assert journal.states[-1]["state"] == "stale_feed"


def test_a_series_a_script_asks_for_is_made_of_the_same_feed(scripts):
    engine, rule, clock, source, runner, journal, sender, _ = engine_with(scripts)
    run(engine.tick())

    minutes = run(engine.answer_series({"ticker": "CAPITAL_US100", "timeframe": "5", "from": (T0 - 3600) * 1000, "to": None}))
    ranged = run(engine.answer_series({"ticker": "CAPITAL_US100.ASK.R40", "timeframe": "1", "from": None, "to": None}))

    assert minutes[0][0] == (T0 - 3600) * 1000 and minutes[-1][6] == T0 * 1000
    assert all(bar[6] - bar[0] == 300_000 for bar in minutes) and len(ranged) > 5
    for wrong, refusal in (("DUKASCOPY_US100", "only Capital.com's"), ("CAPITAL_DE40", "has not recorded DE40")):
        with pytest.raises(ValueError, match=refusal):
            run(engine.answer_series({"ticker": wrong, "timeframe": "1"}))


# ── the real runner, when it is built and Node is here ──

RUNNER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "ta_engine", "runner", "runner.mjs")


def _node_major() -> int:
    """The major version of the Node on the PATH, 0 without one: --permission is Node 22's."""
    import subprocess

    node = find_node("node")
    if node is None:
        return 0
    version = subprocess.run([node, "--version"], capture_output=True, text=True, check=False).stdout.strip()
    return int(version.lstrip("v").split(".")[0]) if version.startswith("v") else 0


@pytest.mark.skipif(not os.path.isfile(RUNNER) or _node_major() < 22, reason="the runner is not built, or no Node 22+ is here")
def test_the_built_runner_runs_a_rule_in_its_confinement():
    rows = ten_second_rows(T0 - 6 * 3600, 6 * 360)
    bars = TimeBars(60, "bid").add(rows, T0)
    sha = hashlib.sha256(SCRIPT.encode()).hexdigest()

    async def answer(ask):
        return []

    async def scenario():
        runner = NodeRunner(runner_command(find_node("node"), RUNNER, 256), answer, timeout_seconds=30)
        await runner.start()
        try:
            loaded = await runner.request({
                "op": "load", "rule": "0123456789ab", "script": {"source": SCRIPT, "sha256": sha}, "libraries": [],
                "inputs": {"in_0": 8}, "ticker": "CAPITAL_US100", "timeframe": "1", "bars": bars[:-5],
            })
            appended = await runner.request({"op": "append", "rule": "0123456789ab", "bars": bars[-5:]})
            with pytest.raises(RunnerFailed, match="not the version the rule pinned"):
                await runner.request({
                    "op": "load", "rule": "0123456789ac", "script": {"source": SCRIPT, "sha256": "0" * 64}, "libraries": [],
                    "inputs": {}, "ticker": "CAPITAL_US100", "timeframe": "1", "bars": bars,
                })
            return loaded, appended
        finally:
            await runner.stop()

    loaded, appended = run(scenario())

    assert loaded["kind"] == "indicator" and loaded["bars"] == len(bars) - 5
    assert [entry["id"] for entry in loaded["inputs"]] == ["in_0"]
    assert appended["bars"] == len(bars)
    assert all(alert["time"] >= bars[-5][0] for alert in appended["alerts"])
    assert set(appended["plots"]["Fast"]) == {str(bar[0]) for bar in bars[-5:]}
