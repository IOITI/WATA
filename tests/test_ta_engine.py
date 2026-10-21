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
from src.ta_engine.bars import RangeBars, TimeBars, bucket_end  # noqa: E402
from src.ta_engine.engine import Engine  # noqa: E402
from src.ta_engine.rules import read_rules  # noqa: E402
from src.ta_engine.runner import NodeRunner, RunnerDown, RunnerFailed, find_node, runner_command  # noqa: E402
from src.ta_engine.sender import Sent  # noqa: E402
from src.ta_engine.settings import TaEngineSettings  # noqa: E402
from src.ta_engine.signals import CLOSE_WITH_ENTRY, Candidate, interpret, iso_seconds, payload, settled  # noqa: E402
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


@pytest.mark.parametrize("seconds", [20, 30, 45, 60, 90, 300, 900, 3600])
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
        if None in prices or bucket_end(bucket[0], seconds) > covered:
            continue
        expected.append([bucket[0] * 1000, *prices, bucket[9], bucket_end(bucket[0], seconds) * 1000])
    assert built == expected and len(built) > 1


def test_a_bar_of_45_seconds_is_final_and_ends_with_its_last_ten_seconds():
    rows = ten_second_rows(T0, 9)
    builder = TimeBars(45, "bid")

    # The ten seconds from 40 to 50 begin in the bar of T0: it waits for them.
    assert builder.add(rows[:5], T0 + 40) == []
    (first,) = builder.add([], T0 + 50)
    assert (first[0], first[6]) == (T0 * 1000, (T0 + 50) * 1000)
    assert first[1] == rows[0][1] and first[4] == rows[4][4]
    # The next holds the ten seconds from 50 to 80, and ends at its own end.
    (second,) = builder.add(rows[5:], T0 + 90)
    assert (second[0], second[6]) == ((T0 + 45) * 1000, (T0 + 90) * 1000)
    assert second[1] == rows[5][1] and second[4] == rows[8][4]
    assert [bucket_end(start, 45) for start in (0, 45, 90, 135)] == [50, 90, 140, 180]


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
    for wrong in ({"enabled": "yes"}, {"history_days": 31}, {"restart_time": "9:15"}, {"max_signal_age_seconds": 1},
                  {"shadow_runner_memory_mb": 64}, {"shadow_seconds_per_tick": 9}, {"shadow_seconds_per_tick": 0}):
        with pytest.raises(ValueError, match="trade.config.ta_engine"):
            TaEngineSettings.from_config(Config({"trade.config.ta_engine": wrong}))
    shadow = TaEngineSettings.from_config(Config({"trade.config.ta_engine": {"shadow_runner_memory_mb": 384, "shadow_seconds_per_tick": 3}}))
    assert (shadow.shadow_runner_memory_mb, shadow.shadow_seconds_per_tick) == (384, 3)
    assert (TaEngineSettings().shadow_runner_memory_mb, TaEngineSettings().shadow_seconds_per_tick) == (256, 5)


# ── the rules, as the engine reads them ──


@pytest.fixture
def scripts(tmp_path):
    return ScriptStore(str(tmp_path / "ta"))


def make_rule(scripts, *, mode="shadow", name="Cross on US100", made_by=None, **changes):
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
    created = rules.create(fields, rules.load()["revision"], made_by=made_by)["rule"]
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
    stored["rules"][0]["bars"] = {"type": "time", "timeframe": "5S"}
    json.dump(stored, open(path, "w"))

    snapshot = read_rules(scripts.root)

    assert snapshot.rules == () and "10 seconds to a day" in snapshot.errors[rule["id"]]


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
    import json, os, sys, time
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
        elif op == "heap":
            # As Node ends when its heap is full.
            sys.stderr.write("<--- Last few GCs --->\\nFATAL ERROR: Reached heap limit Allocation failed - JavaScript heap out of memory\\n")
            sys.stderr.flush()
            os.abort()
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


def test_a_runner_that_dies_says_how_and_its_last_words(fake_runner):
    async def answer(ask):
        return []

    async def scenario():
        runner = NodeRunner(fake_runner, answer, timeout_seconds=5)
        await runner.start()
        try:
            await runner.request({"op": "heap"})
        except RunnerDown as e:
            return str(e)

    said = asyncio.run(scenario())

    assert said.startswith("The Pine runner ended (exit code -6): aborted (SIGABRT): Node does so when its heap is full.")
    assert said.endswith("It said: <--- Last few GCs ---> | FATAL ERROR: Reached heap limit Allocation failed - JavaScript heap out of memory")


def test_the_shadow_rules_runner_is_the_first_the_kernel_stops(fake_runner, monkeypatch):
    import src.ta_engine.runner as runner_module

    written = []
    monkeypatch.setattr(runner_module, "give_up_memory_first", written.append)

    async def scenario(memory_first):
        runner = NodeRunner(fake_runner, None, timeout_seconds=2, memory_first=memory_first)
        await runner.start()
        pid = runner._process.pid
        await runner.stop()
        return pid

    shadow_pid = asyncio.run(scenario(True))
    asyncio.run(scenario(False))

    assert written == [f"/proc/{shadow_pid}/oom_score_adj"]


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
    """The runner as the engine sees it: an alert "Up" on each bar a test names, and "Down" after it on others."""

    def __init__(self):
        self.running = False
        self.requests = []
        self.alerting = set()
        self.closing = set()
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
        alerts = []
        for bar in bars:
            if bar[0] in self.alerting:
                alerts.append({"type": "alertcondition", "id": "alertcondition_0", "title": "Up", "message": "", "time": bar[0]})
            if bar[0] in self.closing:
                alerts.append({"type": "alertcondition", "id": "alertcondition_1", "title": "Down", "message": "", "time": bar[0]})
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


def test_each_rule_starts_at_its_own_history_and_rows_are_read_back_for_the_longest(scripts):
    # The engine's history_days is 0; a rule made later wants a day of bars before the restart.
    engine, rule, clock, source, runner, journal, sender, _ = engine_with(scripts)
    source.rows_by_time = ten_second_rows(T0 - 36 * 3600, 36 * 360)
    restart = T0 - 10 * 3600 - 45 * 60
    run(engine.tick())
    longer = make_rule(scripts, name="Longer", history_days=1)

    run(engine.tick())

    first, second = [request for request in runner.requests if request["op"] == "load"]
    assert (first["rule"], first["bars"][0][0]) == (rule["id"], restart * 1000)
    assert (second["rule"], second["bars"][0][0], second["bars"][-1][6]) == (longer["id"], (restart - 86_400) * 1000, T0 * 1000)
    # The day before was read once, and the first rule kept its place among the rows: a new minute is one bar to each.
    assert source.reads[-1] == (restart - 86_400, restart)
    source.rows_by_time += ten_second_rows(T0, 6, seed=3)
    source.covered, clock.at = T0 + 60, T0 + 61.5
    run(engine.tick())
    appended = {request["rule"]: request["bars"] for request in runner.requests if request["op"] == "append"}
    assert {rule_id: [bar[0] for bar in bars] for rule_id, bars in appended.items()} == {
        rule["id"]: [T0 * 1000], longer["id"]: [T0 * 1000],
    }


def test_a_rule_whose_history_changed_is_loaded_again_from_its_new_start(scripts):
    engine, rule, clock, source, runner, journal, sender, _ = engine_with(scripts, history_days=2)
    source.rows_by_time = ten_second_rows(T0 - 60 * 3600, 60 * 360)
    restart = T0 - 10 * 3600 - 45 * 60
    run(engine.tick())
    store = RuleStore(scripts)
    store.update(rule["id"], {**rule, "history_days": None}, store.load()["revision"])

    run(engine.tick())

    loads = [request for request in runner.requests if request["op"] in ("load", "unload")]
    assert [request["op"] for request in loads] == ["load", "unload", "load"]
    # Two days of its own, then the engine's: none.
    assert (loads[0]["bars"][0][0], loads[2]["bars"][0][0]) == ((restart - 2 * 86_400) * 1000, restart * 1000)


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


def test_a_live_rule_on_ten_second_bars_sends_at_the_tick_after_its_bar_and_never_late(scripts):
    engine, rule, clock, source, runner, journal, sender, _ = engine_with(
        scripts, mode="live", bars={"type": "time", "timeframe": "10S"},
    )
    run(engine.tick())
    load = runner.requests[0]
    # From the restart before (21:15 UTC), every row a bar.
    assert load["timeframe"] == "10S" and len(load["bars"]) == (10 * 3600 + 45 * 60) // 10
    runner.alerting = {T0 * 1000}
    source.rows_by_time += ten_second_rows(T0, 2, seed=3)
    source.covered, clock.at = T0 + 10, T0 + 11.5

    run(engine.tick())

    assert runner.requests[-1]["op"] == "append" and len(runner.requests[-1]["bars"]) == 1
    assert sender.sent == [{
        "action": "long", "indice": "us100", "signal_timestamp": iso_seconds(T0 + 10), "alert_timestamp": iso_seconds(T0 + 11),
    }]
    # A run that comes too late sends nothing, whatever the length of its bars.
    runner.alerting = {(T0 + 10) * 1000}
    source.covered, clock.at = T0 + 20, T0 + 20 + 75
    run(engine.tick())
    assert len(sender.sent) == 1 and "The bar closed 75 s ago" in journal.completed[-1][2]["detail"]


def test_a_bar_that_raises_an_entry_and_a_close_sends_the_entry_alone(scripts):
    engine, rule, clock, source, runner, journal, sender, _ = engine_with(
        scripts, mode="live", alerts=[{"title": "Up", "action": "long"}, {"title": "Down", "action": "close-position"}],
    )
    run(engine.tick())
    runner.alerting, runner.closing = {T0 * 1000}, {T0 * 1000, (T0 + 60) * 1000}
    source.rows_by_time += ten_second_rows(T0, 12, seed=3)

    for minute in (1, 2):
        source.covered, clock.at = T0 + 60 * minute, T0 + 60 * minute + 1.5
        run(engine.tick())

    # The entry is sent; the close of its bar is journaled as not sent, the next bar's close is sent.
    assert [body["action"] for body in sender.sent] == ["long", "close-position"]
    first, second, third = journal.signals
    assert (first["action"], first["outcome"]) == ("long", "sending")
    assert (second["action"], second["outcome"], second["alert_title"]) == (None, "refused", "Down")
    assert "same bar raised an entry" in second["detail"]
    assert (third["action"], third["bar_time"]) == ("close-position", datetime.fromtimestamp(T0 + 60, timezone.utc))


def test_one_bar_makes_one_trade_an_entry_winning_over_a_close():
    long = Candidate("alertcondition:Up", "Up", "", "long", None)
    close = Candidate("alertcondition:Exit", "Exit", "", "close-position", None)
    note = Candidate("alertcondition:Note", "Note", "x", None, None, "not JSON")

    assert settled([long, close]) == [long, Candidate("alertcondition:Exit", "Exit", "", None, None, CLOSE_WITH_ENTRY)]
    assert [candidate.action for candidate in settled([close, long])] == [None, "long"]
    # A close alone, or beside an alert that is no signal, is sent as it was.
    assert settled([close]) == [close] and settled([note, close]) == [note, close]


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
    seconds = run(engine.answer_series({"ticker": "CAPITAL_US100", "timeframe": "10S", "from": (T0 - 600) * 1000, "to": None}))

    assert minutes[0][0] == (T0 - 3600) * 1000 and minutes[-1][6] == T0 * 1000
    assert all(bar[6] - bar[0] == 300_000 for bar in minutes) and len(ranged) > 5
    assert len(seconds) == 60 and all(bar[6] - bar[0] == 10_000 for bar in seconds)
    with pytest.raises(ValueError, match="No bars of '5S'"):
        run(engine.answer_series({"ticker": "CAPITAL_US100", "timeframe": "5S"}))
    for wrong, refusal in (("DUKASCOPY_US100", "only Capital.com's"), ("CAPITAL_DE40", "has not recorded DE40")):
        with pytest.raises(ValueError, match=refusal):
            run(engine.answer_series({"ticker": wrong, "timeframe": "1"}))


def test_a_series_a_script_asks_for_begins_where_its_own_rules_bars_do(scripts):
    # The engine's history_days is 0; another rule on the same epic wants a day before.
    engine, rule, clock, source, runner, journal, sender, _ = engine_with(scripts)
    longer = make_rule(scripts, name="Longer", history_days=1)
    source.rows_by_time = ten_second_rows(T0 - 36 * 3600, 36 * 360)
    asked, answer = [], runner.request

    async def request(message):
        # As the runner asks for a series a script reads (request.security) while it loads a rule.
        if message["op"] == "load":
            series = await engine.answer_series({"ticker": "CAPITAL_US100", "timeframe": "1", "from": None, "to": None})
            asked.append((message["rule"], series[0][0]))
        return await answer(message)

    runner.request = request
    run(engine.tick())

    # As a replay builds it for each rule: from its own anchor, whatever the others keep.
    restart = T0 - 10 * 3600 - 45 * 60
    assert dict(asked) == {rule["id"]: restart * 1000, longer["id"]: (restart - 86_400) * 1000}


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


ZONE_SCRIPT = """//@version=6
indicator("Paris time")
plot(hour(time, "Europe/Paris"), "Hour")
plot(minute(time, "Europe/Paris"), "Minute")
plot(timestamp("Europe/Paris", year, month, dayofmonth, 8, 0, 0), "Eight")
"""


@pytest.mark.skipif(not os.path.isfile(RUNNER) or _node_major() < 22, reason="the runner is not built, or no Node 22+ is here")
def test_the_built_runner_reads_times_in_a_time_zone_and_says_how_its_pinets_was_built():
    # Half-hour bars from noon on 24 October 2026 to noon the next day (UTC): Paris leaves summer time at 01:00 UTC.
    start = int(datetime(2026, 10, 24, 12, tzinfo=timezone.utc).timestamp())
    bars = TimeBars(1800, "bid").add(ten_second_rows(start, 24 * 360), start + 24 * 3600)
    sha = hashlib.sha256(ZONE_SCRIPT.encode()).hexdigest()

    async def answer(ask):
        return []

    async def scenario():
        runner = NodeRunner(runner_command(find_node("node"), RUNNER, 256), answer, timeout_seconds=30)
        versions = await runner.start()
        try:
            loaded = await runner.request({
                "op": "load", "rule": "0123456789ab", "script": {"source": ZONE_SCRIPT, "sha256": sha}, "libraries": [],
                "inputs": {}, "ticker": "CAPITAL_US100", "timeframe": "30", "bars": bars, "detail": True,
            })
            return versions, loaded
        finally:
            await runner.stop()

    versions, loaded = run(scenario())

    # PineTS keeps a formatter per time zone (admin_ui/vite/pinetsZoneFormat.ts): what a run's made_with records.
    assert versions["pinets"].endswith("+zone-format")
    plots = loaded["series"]["plots"]
    at = dict(zip(loaded["series"]["times"], zip(plots["Hour"], plots["Minute"], plots["Eight"])))

    def utc(day: int, hour: int, minute: int = 0) -> int:
        return int(datetime(2026, 10, day, hour, minute, tzinfo=timezone.utc).timestamp()) * 1000

    # 00:30 UTC is 02:30 in summer time, and 01:30 UTC is 02:30 again, in winter time.
    assert (at[utc(25, 0, 30)][:2], at[utc(25, 1, 30)][:2]) == ((2, 30), (2, 30))
    assert at[utc(24, 12)][:2] == (14, 0) and at[utc(25, 6)][:2] == (7, 0)
    # 08:00 in Paris: 06:00 UTC on the 24th, in summer time, and 07:00 UTC on the 25th.
    assert (at[utc(24, 12)][2], at[utc(25, 6)][2]) == (utc(24, 6), utc(25, 7))



# ── live rules and shadow rules, each on a runner of their own ──


class Sequence:
    """The order requests reach either runner in."""

    def __init__(self):
        self.seen = []


class NamedRunner(Runner):
    def __init__(self, name, sequence):
        super().__init__()
        self.name, self.sequence = name, sequence

    async def request(self, message):
        self.sequence.seen.append((self.name, message["op"], message.get("rule")))
        return await super().request(message)


def two_runner_engine(scripts, settings=None):
    live_rule = make_rule(scripts, mode="live", name="Live cross")
    shadow_rule = make_rule(scripts, mode="shadow", name="Claude · cross", made_by="claude")
    clock = Clock(T0 + 1.5)
    source = Source(ten_second_rows(T0 - 11 * 3600, 11 * 360 + 30), covered=T0)
    sequence = Sequence()
    live, shadow = NamedRunner("live", sequence), NamedRunner("shadow", sequence)
    journal, sender, telegram = Journal(), Sender(), Telegram()
    engine = Engine(
        settings=settings or TaEngineSettings(enabled=True, history_days=0),
        rules=lambda: read_rules(scripts.root), source=source, runner=live, shadow_runner=shadow, journal=journal,
        sender=sender, notify=telegram, clock=clock, version="test",
    )
    return engine, live_rule, shadow_rule, clock, source, live, shadow, sequence, journal, sender, telegram


def new_bars(source, clock, count=6):
    source.rows_by_time += ten_second_rows(source.covered, count, seed=len(source.rows_by_time))
    source.covered += count * 10
    clock.at = source.covered + 1.5


def test_live_rules_run_on_their_runner_first_and_shadow_rules_on_theirs(scripts):
    engine, live_rule, shadow_rule, clock, source, live, shadow, sequence, *_ = two_runner_engine(scripts)

    run(engine.tick())

    assert [entry for entry in sequence.seen] == [("live", "load", live_rule["id"]), ("shadow", "load", shadow_rule["id"])]


@pytest.mark.parametrize("why", ["The Pine runner ended (exit code 9)", "The Pine runner took longer than 30 s"])
def test_a_shadow_runner_that_ends_never_reloads_a_live_rule_whose_signal_is_still_sent(scripts, why):
    engine, live_rule, shadow_rule, clock, source, live, shadow, sequence, journal, sender, telegram = two_runner_engine(scripts)
    run(engine.tick())
    new_bars(source, clock, 6)
    live.alerting = {(source.covered - 60) * 1000}
    shadow.failing = ("append", RunnerDown(why))

    run(engine.tick())
    states = {state["rule_id"]: state for state in journal.rule_states[-1]}

    assert [op for name, op, _ in sequence.seen if name == "live"] == ["load", "append"]
    assert len(sender.sent) == 1
    assert states[live_rule["id"]]["state"] == "running"
    assert states[shadow_rule["id"]]["state"] == "error" and states[shadow_rule["id"]]["detail"] == why
    assert journal.states[-1]["state"] == "running"


def test_a_live_runner_that_ends_does_not_reload_the_shadow_rules_either(scripts):
    engine, live_rule, shadow_rule, clock, source, live, shadow, sequence, journal, *_ = two_runner_engine(scripts)
    run(engine.tick())
    new_bars(source, clock, 6)
    live.failing = ("append", RunnerDown("The Pine runner took longer than 30 s"))

    run(engine.tick())

    assert [op for name, op, _ in sequence.seen if name == "shadow"] == ["load", "append"]


def test_shadow_rules_take_turns_in_their_share_of_a_tick_and_lose_no_bar_when_they_wait(scripts):
    settings = TaEngineSettings(enabled=True, history_days=0, shadow_seconds_per_tick=1)
    engine, live_rule, shadow_rule, clock, source, live, shadow, sequence, *_ = two_runner_engine(scripts, settings)
    second = make_rule(scripts, mode="shadow", name="Claude · second", made_by="claude")
    original = shadow.request

    async def slow(message):
        # Each request of a shadow rule takes the whole of their share of the tick.
        clock.at += 2
        return await original(message)

    shadow.request = slow
    given = []
    for _ in range(3):
        run(engine.tick())
        given.append([(op, rule) for name, op, rule in sequence.seen if name == "shadow"])
        sequence.seen.clear()
        new_bars(source, clock, 6)

    # One shadow rule a tick, the one given its bars longest ago first; the live rule every tick.
    assert given == [[("load", shadow_rule["id"])], [("load", second["id"])], [("append", shadow_rule["id"])]]
    assert [request["op"] for request in live.requests] == ["load", "append", "append"]
    # The rule that waited a tick is given both bars since: none was lost.
    waited = shadow.requests[-1]["bars"]
    assert [bar[0] for bar in waited] == [T0 * 1000, (T0 + 60) * 1000]
    # The rule that waited to be loaded starts from the same history as the other.
    assert shadow.requests[1]["bars"][0][0] == shadow.requests[0]["bars"][0][0]


def test_a_rule_moved_from_shadow_to_live_is_loaded_again_on_the_live_runner(scripts):
    engine, live_rule, shadow_rule, clock, source, live, shadow, sequence, *_ = two_runner_engine(scripts)
    run(engine.tick())
    store = RuleStore(scripts)
    store.set_mode(live_rule["id"], "off", store.load()["revision"])
    store.set_mode(shadow_rule["id"], "live", store.load()["revision"])
    new_bars(source, clock, 6)

    run(engine.tick())

    assert ("shadow", "unload", shadow_rule["id"]) in sequence.seen
    assert sequence.seen[-1] == ("live", "load", shadow_rule["id"])


def test_a_shadow_rule_an_agent_made_is_said_on_telegram_once_a_day(scripts):
    engine, live_rule, shadow_rule, clock, source, live, shadow, sequence, journal, sender, telegram = two_runner_engine(scripts)
    shadow.failing = ("load", RunnerFailed({"message": "Undeclared identifier", "file": "script", "line": 4}))

    run(engine.tick())
    clock.at += 3600
    run(engine.tick())

    assert sum("Claude · cross" in message for message in telegram.messages) == 1


def test_the_shadow_runner_stops_once_no_shadow_rule_is_on(scripts):
    engine, live_rule, shadow_rule, clock, source, live, shadow, sequence, *_ = two_runner_engine(scripts)
    run(engine.tick())
    store = RuleStore(scripts)
    store.set_mode(shadow_rule["id"], "off", store.load()["revision"])

    run(engine.tick())

    assert ("shadow", "unload", shadow_rule["id"]) in sequence.seen
    assert not shadow.running and live.running


def test_without_a_shadow_rule_no_second_runner_is_started(scripts):
    rule = make_rule(scripts, mode="live")
    clock = Clock(T0 + 1.5)
    sequence = Sequence()
    live, shadow = NamedRunner("live", sequence), NamedRunner("shadow", sequence)
    engine = Engine(
        settings=TaEngineSettings(enabled=True, history_days=0), rules=lambda: read_rules(scripts.root),
        source=Source(ten_second_rows(T0 - 3600, 360), covered=T0), runner=live, shadow_runner=shadow, journal=Journal(),
        sender=Sender(), notify=Telegram(), clock=clock, version="test",
    )

    run(engine.tick())

    assert (live.starts, shadow.starts) == (1, 0)
