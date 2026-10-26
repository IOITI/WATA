"""
Paper trading of the signal rules in shadow (src/admin/paper.py): each
finished day, the signals a rule raised traded by a one-day run of the
backtest service, kept apart from the list of runs and set beside the daily
range of the run chosen as the rule's reference.
"""

import asyncio
import json
import sys
import types
from datetime import date, datetime, timezone

import pytest

asyncpg_stub = types.ModuleType("asyncpg")
asyncpg_stub.Pool = type("Pool", (), {})
asyncpg_stub.Record = dict
sys.modules.setdefault("asyncpg", asyncpg_stub)
sys.modules.setdefault("aio_pika", types.ModuleType("aio_pika"))

from src.admin.backtests import LIVE_PAPER  # noqa: E402
from src.admin.errors import NotPossible  # noqa: E402
from src.admin.paper import PaperTrading  # noqa: E402
from src.admin.ta_rules import RuleStore  # noqa: E402
from tests.test_admin_backtests import config, finished, folder_of, scripts, service  # noqa: E402, F401

# Tuesday 20 October 2026, 23:00 in Paris: the market closed by the example's hours (8 to 22).
EVENING = datetime(2026, 10, 20, 21, 0, tzinfo=timezone.utc).timestamp()
AFTERNOON = datetime(2026, 10, 20, 13, 0, tzinfo=timezone.utc).timestamp()
SHADOW, LIVE = "r00000000001", "r00000000002"

RULE = {
    "id": SHADOW, "name": "EMA cross on US100", "mode": "shadow", "created_at": "2026-10-14T09:00:00+00:00",
    "indice": "us100", "asset": {"provider": "capital", "key": "US100"}, "side": "bid", "bars": {"type": "range", "ticks": 40},
}


class Queries:
    def __init__(self):
        self.asked = []
        # The signals WATA received, its positions, its orders measured and its days: none unless a test gives some.
        self.journal, self.positions, self.execution, self.daily = [], [], {}, []

    async def journal_between(self, start, end):
        return list(self.journal)

    async def closed_positions(self, start, end):
        return list(self.positions)

    async def execution_rows(self, start, end):
        return self.execution

    async def performance(self, start=None, end=None):
        return {"daily": list(self.daily)}

    async def ta_signals(self, start, end, rule_id=None, limit=500):
        self.asked.append((start, end, rule_id))
        moment = datetime(start.year, start.month, start.day, 8, 0, tzinfo=timezone.utc)
        return [{
            "id": 7, "rule_id": rule_id, "action": "long", "indice": "us100", "confidence": None, "mode": "shadow",
            "bar_time": moment, "bar_closed_at": moment, "raised_at": moment, "alert_title": "Cross up", "alert_message": "{}",
            # What the trader made of it, when it was sent: a nested row of moments too.
            "trader": {"decided_at": moment, "outcome": None, "reason": None, "position_id": None},
        }]


@pytest.fixture
def paper(tmp_path, scripts, config):  # noqa: F811
    backtests = service(tmp_path, scripts, config)
    rules = RuleStore(scripts)
    with open(rules.path, "w", encoding="utf-8") as out_file:
        json.dump({"rules": [RULE, {**RULE, "id": LIVE, "mode": "live"}]}, out_file)
    queries = Queries()
    return backtests, queries, PaperTrading(backtests=backtests, queries=queries, rules=rules, config_path=config, clock=lambda: EVENING)


def test_each_finished_weekday_of_a_shadow_rule_is_traded_on_paper_once(tmp_path, paper):
    backtests, queries, trading = paper

    # The last five days: Thursday 15 to Monday 19, the weekend left out; the live rule is WATA's own trading.
    assert asyncio.run(trading.catch_up()) == 3
    assert queries.asked == [(date(2026, 10, day), date(2026, 10, day), SHADOW) for day in (15, 16, 19)]
    index = backtests.paper_index()
    assert sorted(index[SHADOW]) == ["2026-10-15", "2026-10-16", "2026-10-19"]
    # The signals WATA received: none came those days, kept as such and not asked again.
    assert set(index) == {SHADOW, LIVE_PAPER} and index[LIVE_PAPER] == {day: "" for day in index[SHADOW]}
    folder = folder_of(tmp_path, index[SHADOW]["2026-10-19"])
    request = json.loads((folder / "request.json").read_text())
    assert request["paper"] == {"rule": SHADOW, "day": "2026-10-19"}
    assert request["source"] == {"kind": "ta_signals", "signals": 1, "rule": {"id": SHADOW, "name": "EMA cross on US100"}}
    assert (request["from"], request["to"], request["chart"]["bars"]) == ("2026-10-19", "2026-10-19", {"type": "range", "ticks": 40})
    raised = json.loads((folder / "ta_signals.json").read_text())
    assert raised[0]["raised_at"] == "2026-10-19T08:00:00Z" and raised[0]["trader"]["decided_at"] == "2026-10-19T08:00:00Z"
    # The command reads them: no script is run again, the delay left is the journal's.
    assert json.loads((folder / "settings.json").read_text())["settings"]["signal_delay_seconds"] == 0.5
    # Kept apart from the list of runs; nothing asked twice.
    assert backtests.list_runs() == []
    assert asyncio.run(trading.catch_up()) == 0


def test_nothing_is_traded_on_paper_while_the_market_is_open(paper):
    backtests, queries, trading = paper

    assert asyncio.run(trading.catch_up(AFTERNOON)) == 0
    assert queries.asked == [] and backtests.paper_index() == {}


def test_hours_written_in_new_yorks_time_are_read_in_it_and_the_days_traded_stay_those_of_paris(paper, config):  # noqa: F811
    backtests, queries, trading = paper
    with open(config, encoding="utf-8") as config_file:
        live = json.load(config_file)
    hours = next(rule for rule in live["trade"]["rules"] if rule["rule_type"] == "market_hours")["rule_config"]
    hours.update(timezone="America/New_York", trading_start_hour=2, trading_end_hour=16, risky_trading_start_hour=15)
    with open(config, "w", encoding="utf-8") as config_file:
        json.dump(live, config_file)

    # Tuesday 20 October, 21:30 in Paris: 15:30 in New York, still open.
    assert asyncio.run(trading.catch_up(datetime(2026, 10, 20, 19, 30, tzinfo=timezone.utc).timestamp())) == 0
    # 23:00 in Paris, 17:00 in New York: the same days as with the hours of Paris.
    assert asyncio.run(trading.catch_up()) == 3
    assert [asked[0] for asked in queries.asked] == [date(2026, 10, day) for day in (15, 16, 19)]


def test_a_full_queue_leaves_the_rest_for_later(paper):
    backtests, _, trading = paper
    backtests.max_queued = 1

    assert asyncio.run(trading.catch_up()) == 1
    assert len(backtests.paper_index()[SHADOW]) == 1


def test_paper_days_are_set_beside_the_daily_range_of_the_rule_s_reference(tmp_path, paper, scripts):  # noqa: F811
    backtests, _, trading = paper
    asyncio.run(trading.catch_up())
    backtests._waiting.clear()
    for day, result in (("2026-10-15", 0.4), ("2026-10-16", -6.0)):
        folder = folder_of(tmp_path, backtests.paper_index()[SHADOW][day])
        (folder / "days.json").write_text(json.dumps([{"day": day, "status": "tested", "day_percent": result, "trades": 2}]))
        (folder / "status.json").write_text(json.dumps({"state": "done"}))

    # A run made from the rule, of twenty days from -1.75 % to +3 %: its 5 % and 95 % points are -1.5 and +2.75.
    reference = finished(tmp_path, backtests, scripts, {
        "days.json": [{"day": f"2026-09-{day:02d}", "status": "tested", "day_percent": -2 + day * 0.25} for day in range(1, 21)],
    })
    request_path = folder_of(tmp_path, reference) / "request.json"
    request = json.loads(request_path.read_text())
    with pytest.raises(NotPossible, match="not made from a signal rule"):
        backtests.set_reference(reference)
    request["source"]["from_rule"] = {"id": SHADOW, "name": "EMA cross on US100"}
    request_path.write_text(json.dumps(request))

    found = backtests.set_reference(reference)["rules"][SHADOW]

    assert found["reference"]["run_id"] == reference and found["reference"]["days"] == 20
    assert (found["reference"]["low"], found["reference"]["high"]) == (-1.5, 2.75)
    days = {day["day"]: day for day in found["days"]}
    assert [entry["day"] for entry in found["days"]] == ["2026-10-19", "2026-10-16", "2026-10-15"]
    assert (days["2026-10-15"]["day_percent"], days["2026-10-15"]["outside"]) == (0.4, False)
    assert (days["2026-10-16"]["day_percent"], days["2026-10-16"]["outside"]) == (-6.0, True)
    # Still to run: no result, nothing said.
    assert (days["2026-10-19"]["day_percent"], days["2026-10-19"]["outside"]) == (None, None)


def test_the_oldest_paper_days_are_let_go(tmp_path, paper):
    backtests, _, trading = paper
    asyncio.run(trading.catch_up())
    backtests._waiting.clear()
    oldest = backtests.paper_index()[SHADOW]["2026-10-15"]

    # The rule's two oldest days, and the same two of the signals received, which had no run.
    assert backtests.prune_paper(3, date(2026, 10, 20)) == 4

    assert sorted(backtests.paper_index()[SHADOW]) == ["2026-10-19"]
    assert sorted(backtests.paper_index()[LIVE_PAPER]) == ["2026-10-19"]
    assert not folder_of(tmp_path, oldest).exists()


# ── the signals WATA received, under the live settings ──


def _received(day: int, action: str = "long") -> dict:
    moment = datetime(2026, 10, day, 8, 0, tzinfo=timezone.utc)
    return {"signal_uuid": f"s{day}{action}", "action": action, "indice": "us100", "signal_timestamp": moment,
            "alert_timestamp": moment, "received_timestamp": moment, "decided_at": moment, "outcome": "refused",
            "reason": "Breaking trading rule: New entries are paused by the operator since 2026-10-14.", "position_id": None}


def test_the_signals_received_each_day_are_traded_on_paper_under_the_live_settings(tmp_path, paper):
    backtests, queries, trading = paper
    queries.journal = [_received(day) for day in (15, 16, 19)]

    asyncio.run(trading.catch_up())

    live = backtests.paper_index()[LIVE_PAPER]
    assert sorted(live) == ["2026-10-15", "2026-10-16", "2026-10-19"] and all(live.values())
    request = json.loads((folder_of(tmp_path, live["2026-10-19"]) / "request.json").read_text())
    assert request["paper"] == {"rule": LIVE_PAPER, "day": "2026-10-19"}
    assert request["source"]["kind"] == "journal" and (request["from"], request["to"]) == ("2026-10-19", "2026-10-19")
    # Too few of WATA's orders measured: the settings' fixed fills.
    assert request["changes"] == {}
    # Kept apart from the list of runs and from the rules' paper days.
    assert backtests.list_runs() == [] and LIVE_PAPER not in backtests.paper()["rules"]
    assert [day["day"] for day in backtests.paper()["live"]["days"]] == ["2026-10-19", "2026-10-16", "2026-10-15"]


def _measured_rows(count: int = 12) -> dict:
    """WATA's orders as execution_rows gives them: count purchases and count sales, each with its quote."""
    sent = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
    orders, quotes = [], []
    for number in range(2 * count):
        purpose = "open" if number < count else "close"
        orders.append({"order_id": str(number), "purpose": purpose, "sent_at": sent, "accepted_at": sent, "outcome": "confirmed",
                       "instrument_uic": 7, "position_open_price": 5.05 if purpose == "open" else None,
                       "position_close_price": 4.9 if purpose == "close" else None})
        quotes.append({"order_id": str(number), "bid_close": 4.95, "ask_close": 5.0})
    return {"signals": [], "orders": orders, "quotes": quotes, "requests": []}


def test_with_enough_orders_measured_the_live_paper_days_draw_their_fills_from_them(tmp_path, paper):
    backtests, queries, trading = paper
    queries.journal = [_received(19)]
    queries.execution = _measured_rows()

    asyncio.run(trading.catch_up())

    run_id = backtests.paper_index()[LIVE_PAPER]["2026-10-19"]
    request = json.loads((folder_of(tmp_path, run_id) / "request.json").read_text())
    assert request["changes"] == {"execution": "measured"}


def test_two_days_outside_the_live_reference_are_said_once(tmp_path, paper, scripts):  # noqa: F811
    from tests.test_admin_backtests import finished

    backtests, queries, trading = paper
    said = []

    async def notify(message):
        said.append(message)

    trading._notify = notify
    # The reference: a replay of the journal whose days made -1 to +1 %.
    reference = finished(tmp_path, backtests, scripts, {"days.json": [
        {"day": f"2026-09-{day:02d}", "status": "tested", "day_percent": result}
        for day, result in zip(range(1, 11), (-1, -0.5, 0, 0.2, 0.5, 1, -0.2, 0.1, 0.3, -0.8))
    ]})
    request_path = folder_of(tmp_path, reference) / "request.json"
    request = json.loads(request_path.read_text())
    request["source"] = {"kind": "journal", "signals": 10, "entries": 10}
    request_path.write_text(json.dumps(request))
    backtests.set_reference(reference)
    assert backtests.paper()["live"]["reference"]["low"] == -1
    # Paused: no real day; the paper days of 16 and 19 October fell under the range.
    for day, result in (("2026-10-16", -6.0), ("2026-10-19", -9.5)):
        run_id = finished(tmp_path, backtests, scripts, {"days.json": [
            {"day": day, "status": "tested", "day_percent": result, "trades": 3}]})
        backtests._index_paper(LIVE_PAPER, day, run_id)

    asyncio.run(trading._say_drift())
    asyncio.run(trading._say_drift())

    assert len(said) == 1
    assert said[0].startswith("⚠️ WATA: 2 days in a row outside the live reference's daily range (-1.00 to +1.00 %)")
    assert "2026-10-19 paper -9.50 %; 2026-10-16 paper -6.00 %" in said[0]
    # A real day back in the range ends it.
    queries.daily = [{"day_date": "2026-10-20", "trade_count": 2, "performance_percent": 0.4}]
    days, warning, _ = asyncio.run(trading.drift())
    assert warning is None and days[0]["day"] == "2026-10-20" and days[0]["real_outside"] is False
