"""
The replay of a rule as the TA engine runs it (src/ta_engine/replay.py), with
a stand-in for the Pine runner that raises alerts by the clock and notes what
it was loaded with.
"""

import asyncio
import csv
import gzip
import json
import os
import sys
import textwrap
from datetime import date, datetime, timezone

from src.price_recorder.export import COLUMNS
from src.ta_engine.replay import REPLAY_RULE_ID, AssetRows, ReplaySession, engine_windows
from src.ta_engine.rules import EngineRule

# Raises "Enter Long" at the full hour (UTC) when it is even, "Enter Short" when it is odd, "Exit" at a
# quarter to; notes each load (rule, bars, first and last bar, inputs) in the file named by its first argument.
FAKE_RUNNER = textwrap.dedent("""
    import json
    import sys

    LOG = sys.argv[1]

    for line in sys.stdin:
        message = json.loads(line)
        op = message.get("op")
        if op == "hello":
            print(json.dumps({"id": message["id"], "ok": True, "op": "hello", "pinets": "fake", "node": "fake"}), flush=True)
        elif op == "load":
            bars = message["bars"]
            with open(LOG, "a") as log:
                log.write(json.dumps({"rule": message["rule"], "bars": len(bars), "first": bars[0][0], "last": bars[-1][0],
                                      "inputs": message["inputs"], "timeframe": message["timeframe"]}) + "\\n")
            alerts = []
            for bar in bars:
                minute, hour = (bar[0] // 60000) % 60, (bar[0] // 3600000) % 24
                if minute == 0:
                    alerts.append({"type": "alertcondition", "title": "Enter Long" if hour % 2 == 0 else "Enter Short",
                                   "message": "", "time": bar[0]})
                elif minute == 45:
                    alerts.append({"type": "alertcondition", "title": "Exit", "message": "", "time": bar[0]})
            print(json.dumps({"id": message["id"], "ok": True, "op": "load", "bars": len(bars), "milliseconds": 2,
                              "alerts": alerts, "plots": {}, "warnings": ["made up"]}), flush=True)
""")


def fake_runner(folder) -> tuple[list[str], str]:
    """The command of a stand-in runner, and the file it notes its loads in."""
    script = os.path.join(str(folder), "fake_runner.py")
    log = os.path.join(str(folder), "loads.jsonl")
    with open(script, "w", encoding="utf-8") as out_file:
        out_file.write(FAKE_RUNNER)
    return [sys.executable, script, log], log


def loads(log: str) -> list[dict]:
    with open(log, encoding="utf-8") as in_file:
        return [json.loads(line) for line in in_file]


def index_rows(day: date, first_hour: int, last_hour: int, instrument_id: int = 1, price: float = 25_000.0) -> list[dict]:
    """Ten-second rows of an index, in the columns of wataprices, from ``first_hour`` to ``last_hour`` (UTC)."""
    rows = []
    start = int(datetime(day.year, day.month, day.day, first_hour, tzinfo=timezone.utc).timestamp())
    for moment in range(start, start + (last_hour - first_hour) * 3600, 10):
        value = price + ((moment // 10) % 7) - 3
        rows.append({
            "ts": datetime.fromtimestamp(moment, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "uic": 1909050,
            "asset_type": "StockIndex", "symbol": "US100", "description": "NASDAQ 100", "role": "underlying",
            "indice": "us100", "direction": "", "barrier": "", "issuer": "",
            "bid_open": value, "bid_high": value + 1, "bid_low": value - 1, "bid_close": value,
            "ask_open": "", "ask_high": "", "ask_low": "", "ask_close": "", "updates": 2, "delayed_minutes": 0,
            "instrument_id": instrument_id,
        })
    return rows


def write_rows(path: str, rows: list[dict]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    columns = list(COLUMNS) + ["instrument_id"]
    with gzip.open(path, "wt", newline="", encoding="utf-8") as out_file:
        writer = csv.DictWriter(out_file, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def rule_on(asset: str = "1", inputs=None) -> dict:
    return {
        "id": REPLAY_RULE_ID, "name": "Hours", "asset": {"provider": "saxo", "key": asset}, "side": "bid",
        "bars": {"type": "time", "timeframe": "1"},
        "script": {"id": "aaaaaaaaaaaa", "version": 1, "sha256": "x"}, "libraries": [],
        "inputs": inputs or [{"id": "in_0", "var": "length", "title": "Length", "value": 12}],
        "alerts": [{"title": "Enter Long", "action": "long"}, {"title": "Enter Short", "action": "short"},
                   {"title": "Exit", "action": "close-position"}],
        "alert_calls": "ignore", "confidence_plot": None, "indice": "us100", "mode": "shadow",
    }


def test_the_engines_days_run_from_one_restart_to_the_next():
    start = int(datetime(2026, 10, 13, 22, tzinfo=timezone.utc).timestamp())
    end = int(datetime(2026, 10, 15, 22, tzinfo=timezone.utc).timestamp())

    windows = engine_windows(start, end, 21 * 60 + 15)

    assert [datetime.fromtimestamp(begin, timezone.utc).strftime("%d %H:%M") for begin, _ in windows] == [
        "13 21:15", "14 21:15", "15 21:15",
    ]
    assert all(finish - begin == 86_400 for begin, finish in windows)


def test_each_day_is_run_on_its_bars_from_history_days_before_under_one_rule(tmp_path):
    files = tmp_path / "recorded"
    write_rows(str(files / "saxo" / "index.csv.gz"),
               index_rows(date(2026, 10, 13), 6, 20) + index_rows(date(2026, 10, 14), 6, 20) + index_rows(date(2026, 10, 15), 6, 20))
    command, log = fake_runner(tmp_path)
    rule = rule_on()
    period = (int(datetime(2026, 10, 14, tzinfo=timezone.utc).timestamp()),
              int(datetime(2026, 10, 16, tzinfo=timezone.utc).timestamp()))

    async def scenario():
        session = ReplaySession(rule, EngineRule(rule=rule, source="//@version=6"), AssetRows(str(tmp_path), files_root=str(files)),
                                history_days=1, command=command)
        await session.start()
        try:
            return [await session.window(begin, end, period=period)
                    for begin, end in engine_windows(period[0], period[1], session.restart_minute)]
        finally:
            await session.stop()

    windows = asyncio.run(scenario())

    loaded = loads(log)
    # The same rule each day: the runner keeps one script, whatever the length of the period.
    assert {entry["rule"] for entry in loaded} == {REPLAY_RULE_ID}
    assert loaded[0]["inputs"] == {"in_0": 12} and loaded[0]["timeframe"] == "1"
    # The day of the 13th's restart to the 14th's, and its bars from a day before that restart.
    first_bar = datetime.fromtimestamp(loaded[0]["first"] / 1000, timezone.utc)
    assert first_bar == datetime(2026, 10, 13, 6, tzinfo=timezone.utc)
    # Its alerts: those of bars that closed in it and began in the period, from 06:00 of the 14th (UTC).
    hours = [(datetime.fromtimestamp(alert.bar[0] / 1000, timezone.utc).strftime("%d %H:%M"), alert.candidate.action)
             for alert in windows[0].alerts]
    assert hours[0] == ("14 06:00", "long")
    assert hours[1] == ("14 06:45", "close-position")
    assert hours[2] == ("14 07:00", "short")
    assert all(alert.bar[0] >= period[0] * 1000 for window in windows for alert in window.alerts)
    assert windows[0].warnings == ["made up"] and windows[0].milliseconds == 2


def test_a_day_without_bars_loads_nothing(tmp_path):
    files = tmp_path / "recorded"
    write_rows(str(files / "saxo" / "index.csv.gz"), index_rows(date(2026, 10, 14), 6, 8))
    command, log = fake_runner(tmp_path)
    rule = rule_on()
    begin = int(datetime(2026, 10, 15, 21, 15, tzinfo=timezone.utc).timestamp())

    async def scenario():
        session = ReplaySession(rule, EngineRule(rule=rule, source="x"), AssetRows(str(tmp_path), files_root=str(files)),
                                history_days=0, command=command)
        await session.start()
        try:
            return await session.window(begin, begin + 86_400)
        finally:
            await session.stop()

    window = asyncio.run(scenario())

    assert (window.bars, window.alerts) == (0, [])
    assert not os.path.exists(log)


def test_a_day_run_before_is_read_back_from_the_cache_until_what_decides_it_changes(tmp_path):
    files = tmp_path / "recorded"
    write_rows(str(files / "saxo" / "index.csv.gz"), index_rows(date(2026, 10, 13), 6, 20) + index_rows(date(2026, 10, 14), 6, 20))
    command, log = fake_runner(tmp_path)
    cache = str(tmp_path / "cache")
    period = (int(datetime(2026, 10, 14, tzinfo=timezone.utc).timestamp()),
              int(datetime(2026, 10, 15, tzinfo=timezone.utc).timestamp()))

    def replay(rule: dict):
        async def scenario():
            session = ReplaySession(rule, EngineRule(rule=rule, source="//@version=6"), AssetRows(str(tmp_path), files_root=str(files)),
                                    history_days=1, command=command, cache=cache)
            await session.start()
            try:
                windows = [await session.window(begin, end, period=period)
                           for begin, end in engine_windows(period[0], period[1], session.restart_minute)]
            finally:
                await session.stop()
            alerts = [(alert.bar[0], alert.candidate.action if alert.candidate else None) for window in windows for alert in window.alerts]
            return alerts, session.run_windows, session.cached_windows

        return asyncio.run(scenario())

    first, ran, read = replay(rule_on())
    loaded = len(loads(log))
    again, ran_again, read_again = replay(rule_on())
    # The alerts mapped otherwise: the runner's answer is the same, what the rule makes of it is not.
    remapped = rule_on()
    remapped["alerts"] = [{"title": "Enter Long", "action": "short"}]
    other_mapping, ran_remapped, read_remapped = replay(remapped)
    # Another input: another run.
    _, ran_other, read_other = replay(rule_on(inputs=[{"id": "in_0", "var": "length", "title": "Length", "value": 20}]))

    assert (ran, read) == (2, 0) and (ran_again, read_again) == (0, 2) and again == first
    assert (ran_remapped, read_remapped) == (0, 2)
    assert {action for _, action in other_mapping if action} == {"short"}
    assert (ran_other, read_other) == (2, 0)
    # The runner was asked for the first run's days and the other input's, nothing more.
    assert len(loads(log)) == loaded + 2
