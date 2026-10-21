"""
The MCP endpoint's reading tools, called as Claude Code calls them (the SDK's
client, in process) over the admin's real services on files: the backtest
service, the script and rule stores, the experiment journal. The database is
a fake that answers the named queries the tools may run, and nothing else.
"""

import asyncio
import csv
import io
import json
from datetime import date, datetime, timezone

import pytest
from mcp import Client

from src.admin.mcp_server import budget
from src.admin.mcp_server.facade import ConfigFacts, ReadQueries, ResearchServices
from src.admin.mcp_server.limits import McpLimits
from src.admin.mcp_server.server import build_server
from src.admin.mcp_server.shape import compacted
from src.admin.processes import DONE
from src.admin.ta_rules import RuleStore
from src.research.experiments import ExperimentJournal
from tests.test_admin_backtests import (  # noqa: F401 (fixtures)
    NOW,
    config,
    folder_of,
    pine,
    scripts,
    service,
)
from tests.test_admin_ta_rules import fields

CAPITAL_ROW = {"id": 7, "epic": "US100", "name": "US Tech 100", "label": "us100", "currency": "USD", "decimals": 1,
               "first_t": 1_760_500_000, "last_t": 1_760_900_000}
SAXO_INDEX = {"id": 1, "uic": 1909050, "asset_type": "StockIndex", "symbol": "US100", "description": "US Tech 100 Index",
              "currency": "USD", "role": "underlying", "indice": "us100", "underlying_uic": None, "direction": None,
              "barrier": None, "issuer": None, "first_t": 1_760_400_000, "last_t": 1_760_900_000}
SAXO_TURBO = {**SAXO_INDEX, "id": 2, "uic": 55, "asset_type": "WarrantKnockOut", "symbol": "TURBO",
              "description": "Turbo long US100 21000", "role": "turbo", "direction": "long", "barrier": 21000.0,
              "issuer": "SG"}
JOURNAL_ROW = {"signal_uuid": "a", "action": "long", "indice": "us100", "confidence": None,
               "signal_timestamp": datetime(2026, 10, 13, 7, 0, tzinfo=timezone.utc),
               "received_timestamp": datetime(2026, 10, 13, 7, 0, 1, tzinfo=timezone.utc),
               "decided_at": datetime(2026, 10, 13, 7, 0, 2, tzinfo=timezone.utc), "outcome": "executed",
               "reason": None, "position_id": "5001"}
POSITION_ROW = {"position_id": "5001", "action": "long", "execution_time_open": datetime(2026, 10, 13, 7, 0, 3, tzinfo=timezone.utc),
                "execution_time_close": datetime(2026, 10, 13, 9, 0, tzinfo=timezone.utc), "position_open_price": 5.1,
                "position_close_price": 5.4, "position_total_performance_percent": 5.882352941,
                "position_max_performance_percent": 7.2, "position_close_reason": "max_profit",
                "instrument_name": "Turbo long US100 21000", "instrument_uic": 55}


class FakeQueries:
    """The named queries the research tools may run, answered from fixed rows."""

    def __init__(self):
        self.calls = []

    async def capital_assets(self):
        return [CAPITAL_ROW]

    async def price_assets(self, limit, since=None, instrument_id=None):
        return [SAXO_INDEX, SAXO_TURBO][:limit]

    async def capital_instrument(self, epic):
        return CAPITAL_ROW if epic == "US100" else None

    async def capital_bars(self, instrument_id, start, end, interval, limit):
        self.calls.append(("capital_bars", interval, limit))
        base = int(start.timestamp())
        rows = [[base + index * interval, 21000.0 + index, 21001.5 + index, 20999.5 + index, 21000.25 + index,
                 21001.0 + index, 21002.5 + index, 21000.5 + index, 21001.25 + index, 12] for index in range(3)]
        return rows, rows[-1][0]

    async def backtest_turbo_days(self, indice, first, last):
        return [{"day": date(2026, 10, 13), "direction": "long", "turbos": 40,
                 "first_ts": datetime(2026, 10, 13, 6, 0, tzinfo=timezone.utc),
                 "last_ts": datetime(2026, 10, 13, 20, 0, tzinfo=timezone.utc)}]

    async def backtest_recorder_days(self, first, last):
        return [{"day": date(2026, 10, 13), "listened": 5040}, {"day": date(2026, 10, 14), "listened": 0}]

    async def backtest_asset_days(self, provider, instrument_id, first, last):
        return [{"day": date(2026, 10, 13), "bars": 5040}]

    async def journal_between(self, first, last):
        return [JOURNAL_ROW]

    async def closed_positions(self, first, last):
        return [POSITION_ROW]

    async def last_balance(self):
        return 25_000.0

    async def execution_rows(self, start, end):
        return {"signals": [], "orders": [], "quotes": [], "requests": []}

    async def ta_signals(self, start=None, end=None, rule_id=None, limit=500):
        return [{"id": 1, "rule_id": rule_id or "r1", "action": "long", "bar_time": datetime(2026, 10, 13, 7, 0, tzinfo=timezone.utc),
                 "outcome": "shadow", "trader": None}]

    async def ta_other_signals(self, indice, day):
        return [{**JOURNAL_ROW, "signal_timestamp": datetime(2026, 10, 13, 7, 1, tzinfo=timezone.utc)}]

    async def ta_rule_states(self):
        return {}

    # Never offered to the tools: ReadQueries refuses them by name.
    async def fetch(self, query, *args):
        raise AssertionError("a tool ran a statement")


class Calls:
    def __init__(self):
        self.entries = []

    def record(self, **entry):
        self.entries.append(entry)


@pytest.fixture
def journal(tmp_path):
    return ExperimentJournal(tmp_path / "backtests" / "_research" / "experiments.jsonl",
                             tmp_path / "backtests" / "_research" / "lockbox.json")


@pytest.fixture
def research(tmp_path, scripts, config, journal):
    return ResearchServices(
        version="9.9.9", timezone="Europe/Paris", limits=McpLimits(), calls=Calls(), tokens=None,
        config=ConfigFacts(config), journal=journal, backtests_path=str(tmp_path / "backtests"),
        backtests=service(tmp_path, scripts, config), scripts=scripts, rules=RuleStore(scripts),
        queries=ReadQueries(FakeQueries()), clock=lambda: NOW,
    )


def call(research, tool: str, arguments: dict | None = None):
    """What a tool answers Claude: (its text, whether it is an error)."""

    async def calling():
        async with Client(build_server(research)) as client:
            result = await client.call_tool(tool, arguments or {})
            return result.content[0].text, result.is_error

    return asyncio.run(calling())


def answer(research, tool: str, arguments: dict | None = None):
    text, error = call(research, tool, arguments)
    assert not error, text
    return json.loads(text)


def refusal(research, tool: str, arguments: dict | None = None) -> str:
    text, error = call(research, tool, arguments)
    assert error, text
    return text


def finished_run(tmp_path, research, scripts, files: dict, **research_fields) -> str:
    async def ask():
        return research.backtests.create(pine(scripts), **research_fields)

    run_id = asyncio.run(ask())["id"]
    folder = folder_of(tmp_path, run_id)
    for name, content in files.items():
        (folder / name).write_text(json.dumps(content))
    (folder / "status.json").write_text(json.dumps({"state": DONE}))
    research.backtests._waiting.clear()
    return run_id


SUMMARY = {
    "made_with": {"code": "x"}, "days": {"asked": 4, "tested": 2},
    "figures": {"total_percent": 1.23456789, "trades": 2, "win_rate": 50.0, "worst_drawdown": -0.5, "sample": "too_few",
                "risk": {"sharpe": 0.8}},
    "against_random": {"beaten": 71.0}, "money": {"profit_loss": 150.0},
    "curve": [{"day": "2026-10-13", "percent": 2.0, "cumulative": 2.0}, {"day": "2026-10-14", "percent": -0.8, "cumulative": 1.2}],
}
TRADES = [
    {"id": 1, "day": "2026-10-13", "direction": "long", "turbo": {"description": "Turbo long", "issuer": "SG", "barrier": 21000.0},
     "entry": {"filled_at": "2026-10-13T07:00:05Z", "price": 5.1}, "exit": {"filled_at": "2026-10-13T09:00:00Z", "reason": "max_profit", "group": "Target"},
     "performance_percent": 5.9, "max_performance_percent": 7.2, "min_performance_percent": -1.0, "holding_seconds": 7195},
    {"id": 2, "day": "2026-10-14", "direction": "short", "turbo": {"description": "Turbo short", "issuer": "BNP", "barrier": 22000.0},
     "entry": {"filled_at": "2026-10-14T13:00:05Z", "price": 4.0}, "exit": {"filled_at": "2026-10-14T13:30:00Z", "reason": "stoploss", "group": "Stop"},
     "performance_percent": -7.5, "max_performance_percent": 0.5, "min_performance_percent": -7.5, "holding_seconds": 1795},
]
DAYS = [{"day": "2026-10-13", "status": "tested", "trades": 1, "day_percent": 2.0,
         "recorded": {"long": 40, "short": 38, "minutes": 840}, "regime": {"gap": 0.4, "labels": {"volatility": "high"}}}]


# ── orientation ──


def test_status_says_where_things_stand(research):
    found = answer(research, "status")

    assert found["server"]["version"] == "9.9.9"
    assert found["indices"] == ["us100"]
    assert found["data"]["saxo_recorder"]["days_recorded_in_last_30"] == 1
    assert found["data"]["capital_feed"][0]["ref"] == "capital/US100"
    assert found["backtests"] == {"running": [], "waiting": 0, "claude_waiting": 0, "claude_may_wait": 3,
                                  "code_digest": research.backtests.code}
    assert found["journal"]["open_experiments"] == [] and found["journal"]["lockbox"] is None
    assert found["limits"]["max_waiting_runs"] == 3


# ── data ──


def test_the_assets_are_named_by_their_ref_and_turbos_are_counted_until_asked_for(research):
    listed = answer(research, "data_assets")

    assert [asset["ref"] for asset in listed["items"]] == ["capital/US100", "saxo/1"]
    assert listed["saxo_turbos"] == 1
    # What R<ticks> is counted in on each: Capital.com's decimals, nothing for an index never quoted.
    assert [asset["tick"] for asset in listed["items"]] == [0.1, None]
    turbos = answer(research, "data_assets", {"provider": "saxo"})
    assert turbos["items"][1] == {**turbos["items"][1], "ref": "saxo/2", "direction": "long", "barrier": 21000.0}


def test_coverage_says_what_each_day_has(research):
    found = answer(research, "data_coverage", {"start_day": "2026-10-13", "end_day": "2026-10-14", "asset": "capital/US100"})

    assert found["days"][0]["turbos"] == {"long": 40, "short": 0}
    assert found["days"][0]["asset_bars"] == 5040 and found["days"][1]["asset_bars"] == 0


def test_bars_come_as_compact_json_or_as_a_table_for_a_file(research):
    found = answer(research, "data_bars", {"asset": "capital/US100", "start_day": "2026-10-13", "interval_seconds": 45})
    table = call(research, "data_bars", {"asset": "capital/US100", "start_day": "2026-10-13", "format": "csv"})[0]

    assert found["columns"][0] == "t" and found["count"] == 3 and found["interval_seconds"] == 45
    assert found["asset"]["ref"] == "capital/US100"
    rows = list(csv.reader(io.StringIO(table)))
    assert rows[0][:2] == ["t", "bid_open"] and len(rows) == 4


def test_a_week_of_bars_comes_as_one_csv_and_no_more(research):
    week = call(research, "data_bars", {"asset": "capital/US100", "start_day": "2026-10-13", "end_day": "2026-10-19",
                                        "interval_seconds": 10, "format": "csv"})
    longer = refusal(research, "data_bars", {"asset": "capital/US100", "start_day": "2026-10-13", "end_day": "2026-10-20",
                                             "interval_seconds": 10, "format": "csv"})

    assert not week[1] and week[0].splitlines()[0].startswith("t,bid_open")
    # Asked of the database as up to 70,000 bars, one more to know there is more.
    assert ("capital_bars", 10, 70_001) in research.queries._queries.calls
    assert "7 days at most" in longer


def test_numbers_keep_six_digits_and_a_price_its_thousandths():
    # Six digits of Dukascopy's 29143.543 were 29143.5, which hid the decimal its range bars count in.
    assert compacted({"price": 29143.543, "share": 12.3456789, "small": 0.123456789, "large": 1234567.891234,
                      "none": float("nan"), "whole": 3, "flag": True}) == {
        "price": 29143.543, "share": 12.3457, "small": 0.123457, "large": 1234567.891, "none": None, "whole": 3, "flag": True,
    }


def test_a_bar_length_the_charts_do_not_make_is_refused(research):
    assert "seconds" in refusal(research, "data_bars", {"asset": "capital/US100", "start_day": "2026-10-13", "interval_seconds": 5})


def test_the_signals_and_trades_are_read_a_page_at_a_time(research):
    received = answer(research, "data_signals", {"start_day": "2026-10-13"})
    engine = answer(research, "data_signals", {"start_day": "2026-10-13", "kind": "engine", "rule_id": "r9"})
    trades = answer(research, "data_trades", {"start_day": "2026-10-13"})

    assert received["items"][0]["decided_at"] == "2026-10-13T07:00:02Z" and received["next_offset"] is None
    assert engine["items"][0]["rule_id"] == "r9"
    assert trades["summary"]["wins"] == 1 and trades["summary"]["close_reasons"] == {"max_profit": 1}
    assert trades["items"][0]["position_total_performance_percent"] == 5.88235


def test_the_lock_box_days_are_not_looked_at(research, journal):
    journal.set_lockbox(date(2026, 10, 14), date(2026, 10, 20))

    for tool in ("data_bars", "data_signals", "data_trades"):
        arguments = {"start_day": "2026-10-13", "end_day": "2026-10-15"}
        if tool == "data_bars":
            arguments["asset"] = "capital/US100"
        assert "lock box" in refusal(research, tool, arguments)
    assert answer(research, "data_trades", {"start_day": "2026-10-13"})["summary"]["trades"] == 1


def test_execution_is_measured_on_the_orders_there_are(research):
    found = answer(research, "data_execution", {"start_day": "2026-10-01", "end_day": "2026-10-19"})

    assert found["from"] == "2026-10-01" and found["to"] == "2026-10-19"


# ── Pine scripts ──


def test_the_scripts_are_listed_with_who_made_them_and_the_rules_that_run_them(research, scripts):
    cross = next(meta for meta in scripts.scripts() if meta["name"] == "Cross")
    research.rules.create(fields(cross), research.rules.load()["revision"])
    scripts.create("Mine", "//@version=6\nindicator('Mine')\nplot(close)\n", made_by="claude")

    listed = answer(research, "pine_list")
    mine = answer(research, "pine_list", {"made_by": "claude"})

    by_name = {line["name"]: line for line in listed["items"]}
    assert by_name["Cross"]["used_by_rules"] == ["Cross on US100"] and by_name["Cross"]["imports"] == ["Bands"]
    assert [line["name"] for line in mine["items"]] == ["Mine"]


def test_a_scripts_source_is_read_at_any_version(research, scripts):
    cross = next(meta for meta in scripts.scripts() if meta["name"] == "Cross")
    first_source = scripts.source(cross["id"], 1)
    scripts.save(cross["id"], 1, source=first_source + "\n// two", made_by="claude")

    current = answer(research, "pine_get", {"script_id": cross["id"]})
    first = answer(research, "pine_get", {"script_id": cross["id"], "version": 1})

    assert current["version"] == 2 and current["source"].endswith("// two")
    assert first["source"] == first_source and first["current_version"] == 2
    assert [entry["made_by"] for entry in current["versions"]] == [None, "claude"]
    assert "No script" in refusal(research, "pine_get", {"script_id": "000000000000"})


# ── backtests ──


def test_options_say_every_setting_with_its_live_value(research):
    found = answer(research, "backtest_options")

    # 1,000 € unless a request says another; the live balance beside it.
    assert found["capital"] == {"value": 1_000.0, "balance": 25_000.0}
    assert any(entry["name"] == "stoploss_percent" for entry in found["fields"])


def test_runs_are_listed_with_their_headline_and_who_asked(tmp_path, research, scripts):
    mine = finished_run(tmp_path, research, scripts, {"summary.json": SUMMARY}, made_by="claude", experiment="a1b2c3")
    finished_run(tmp_path, research, scripts, {"summary.json": SUMMARY})

    everything = answer(research, "backtest_list")
    claudes = answer(research, "backtest_list", {"made_by": "claude"})

    assert everything["total"] == 2
    assert [run["id"] for run in claudes["items"]] == [mine]
    assert claudes["items"][0]["experiment"] == "a1b2c3" and claudes["items"][0]["headline"]["total_percent"] == 1.23457


def test_a_runs_summary_has_its_figures_and_its_curve_in_a_few_numbers(tmp_path, research, scripts):
    run_id = finished_run(tmp_path, research, scripts, {"summary.json": SUMMARY})

    found = answer(research, "backtest_result", {"run_id": run_id})

    assert found["state"] == DONE and found["result"]["figures"]["total_percent"] == 1.23457
    assert "curve" not in found["result"]
    assert found["curve"] == [["2026-10-13", 2.0, 2.0], ["2026-10-14", -0.8, 1.2]]
    assert found["asked"]["source"]["alerts"] == {"Enter Long": "long", "Exit": "close-position"}
    assert found["asked"]["source"]["inputs"] == {"in_0": 12}


def test_a_run_that_died_says_how_and_where(tmp_path, research, scripts):
    run_id = finished_run(tmp_path, research, scripts, {})
    failure = {"exit": -9, "said": "stopped by the kernel (SIGKILL)", "phase": "signals", "engine_day": "2026-09-16T21:15:00Z",
               "bars": 102365, "memory_mb": {"used": 1480, "limit": 1536}, "oom_kills": 1}
    (folder_of(tmp_path, run_id) / "status.json").write_text(json.dumps({"state": "failed", "error": "The backtest was stopped", "failure": failure}))

    [listed] = answer(research, "backtest_list")["items"]

    assert listed["state"] == "failed" and listed["failure"] == failure


def test_a_part_a_run_does_not_have_is_said(tmp_path, research, scripts):
    run_id = finished_run(tmp_path, research, scripts, {"summary.json": SUMMARY})

    assert "no edge" in answer(research, "backtest_result", {"run_id": run_id, "part": "edge"})["note"]
    assert "part is one of" in refusal(research, "backtest_result", {"run_id": run_id, "part": "everything"})
    assert "No such backtest" in refusal(research, "backtest_result", {"run_id": "20261019-200000-abcdef"})


def test_trades_are_sorted_and_narrowed_and_days_carry_their_regime(tmp_path, research, scripts):
    run_id = finished_run(tmp_path, research, scripts, {"summary.json": SUMMARY, "trades.json": TRADES, "days.json": DAYS})

    worst = answer(research, "backtest_trades", {"run_id": run_id, "order": "worst"})
    stops = answer(research, "backtest_trades", {"run_id": run_id, "exit_group": "Stop"})
    days = answer(research, "backtest_days", {"run_id": run_id})
    table = call(research, "backtest_trades", {"run_id": run_id, "format": "csv"})[0]

    assert [trade["id"] for trade in worst["items"]] == [2, 1]
    assert [trade["id"] for trade in stops["items"]] == [2]
    assert days["items"][0]["turbos"] == {"long": 40, "short": 38} and days["items"][0]["labels"] == {"volatility": "high"}
    assert "turbo.issuer" in table.splitlines()[0]


def test_the_turbos_an_entry_could_have_bought_come_only_when_asked(tmp_path, research, scripts):
    offered = [{"uic": number, "ask": 5.0 + number / 100} for number in range(20)]
    trades = [{**TRADES[0], "entry": {**TRADES[0]["entry"], "candidates": offered}}]
    run_id = finished_run(tmp_path, research, scripts, {"summary.json": SUMMARY, "trades.json": trades, "days.json": DAYS})

    plain = answer(research, "backtest_trades", {"run_id": run_id})
    asked = answer(research, "backtest_trades", {"run_id": run_id, "candidates": True})
    table = call(research, "backtest_trades", {"run_id": run_id, "format": "csv"})[0]

    assert "candidates" not in plain["items"][0] and len(asked["items"][0]["candidates"]) == 20
    assert "candidates" not in table.splitlines()[0]


def test_two_runs_are_set_side_by_side_day_by_day(tmp_path, research, scripts):
    first = finished_run(tmp_path, research, scripts, {"summary.json": SUMMARY})
    second = finished_run(tmp_path, research, scripts, {"summary.json": {**SUMMARY, "curve": [{"day": "2026-10-13", "percent": 1.0}]}})

    found = answer(research, "backtest_compare", {"run_ids": [first, second]})

    assert found["days"][0] == {"day": "2026-10-13", first: 2.0, second: 1.0}
    assert "Compare 2 to 4" in refusal(research, "backtest_compare", {"run_ids": [first]})


def test_backtests_switched_off_are_off_for_claude_too(research):
    research.backtests = None

    assert "admin.backtests_enabled" in refusal(research, "backtest_list")
    assert answer(research, "status")["backtests"].startswith("switched off")


# ── rules ──


def test_rules_are_listed_with_their_mode_and_who_made_them(research, scripts):
    cross = next(meta for meta in scripts.scripts() if meta["name"] == "Cross")
    research.rules.create(fields(cross), research.rules.load()["revision"], made_by="claude")

    found = answer(research, "rule_list")

    [rule] = found["rules"]
    assert (rule["mode"], rule["made_by"], rule["bars"], rule["asset"]) == ("shadow", "claude", "1", "capital/US100")
    assert found["shadow_rules_switch"] is False


def test_a_rules_signals_are_set_beside_tradingviews(research, scripts):
    cross = next(meta for meta in scripts.scripts() if meta["name"] == "Cross")
    rule = research.rules.create(fields(cross), research.rules.load()["revision"])["rule"]

    found = answer(research, "rule_compare", {"rule_id": rule["id"], "day": "2026-10-13"})

    assert (found["paired"], found["only_wata"], found["only_tradingview"]) == (1, 0, 0)
    assert found["rows"][0]["delta_seconds"] == 60


# ── what is never reached ──


def test_a_query_that_is_not_named_cannot_be_run():
    queries = ReadQueries(FakeQueries())

    for name in ("fetch", "execute", "db", "open_positions", "list_orders", "order_requests"):
        with pytest.raises(AttributeError):
            getattr(queries, name)


def test_every_answer_stays_within_what_goes_into_claudes_context(tmp_path, research, scripts, monkeypatch):
    many = [{**TRADES[number % 2], "id": number} for number in range(5000)]
    run_id = finished_run(tmp_path, research, scripts, {"summary.json": SUMMARY, "trades.json": many, "days.json": DAYS * 92})

    for tool, arguments in (("backtest_result", {"run_id": run_id}), ("backtest_result", {"run_id": run_id, "part": "breakdowns"}),
                            ("backtest_trades", {"run_id": run_id}), ("backtest_days", {"run_id": run_id})):
        text, error = call(research, tool, arguments)
        assert not error, (tool, text[:300])
        assert len(text) <= budget.CONTEXT_CHARS, (tool, len(text))
