"""
Paper trading of the signal rules in shadow (src/admin/paper.py): each
finished day, the signals a rule raised traded by a one-day run of the
backtest service, kept apart from the list of runs and set beside the daily
range of the run chosen as the rule's reference; and each one's report, one
run of its days since it began, made again after each finished day.
"""

import asyncio
import json
import sys
import types
from datetime import date, datetime, timedelta, timezone

import pytest

asyncpg_stub = types.ModuleType("asyncpg")
asyncpg_stub.Pool = type("Pool", (), {})
asyncpg_stub.Record = dict
sys.modules.setdefault("asyncpg", asyncpg_stub)
sys.modules.setdefault("aio_pika", types.ModuleType("aio_pika"))

import src.admin.paper as paper_module  # noqa: E402
from src.admin.backtests import LIVE_PAPER  # noqa: E402
from src.admin.errors import InvalidRequest, NotPossible  # noqa: E402
from src.admin.paper import PaperTrading, last_finished_day  # noqa: E402
from src.admin.processes import DONE, FAILED  # noqa: E402
from src.admin.ta_rules import RuleNotFound, RuleStore  # noqa: E402
from tests.test_admin_backtests import config, finished, folder_of, scripts, service  # noqa: E402, F401

# Tuesday 20 October 2026, 23:00 in Paris: the market closed by the example's hours (8 to 22).
EVENING = datetime(2026, 10, 20, 21, 0, tzinfo=timezone.utc).timestamp()
AFTERNOON = datetime(2026, 10, 20, 13, 0, tzinfo=timezone.utc).timestamp()
SHADOW, LIVE = "a00000000001", "a00000000002"

RULE = {
    "id": SHADOW, "name": "EMA cross on US100", "mode": "shadow", "created_at": "2026-10-14T09:00:00+00:00",
    "indice": "us100", "asset": {"provider": "capital", "key": "US100"}, "side": "bid", "bars": {"type": "range", "ticks": 40},
}


class Queries:
    def __init__(self):
        self.asked = []
        # The signals WATA received, its positions, its orders measured and its days: none unless a test gives some.
        self.journal, self.positions, self.execution, self.daily = [], [], {}, []
        self.journal_reads = 0
        # The version of the script that raised a day's signal: none unless a test gives one.
        self.version_of = None

    async def journal_between(self, start, end):
        self.journal_reads += 1
        return list(self.journal)

    async def closed_positions(self, start, end):
        return list(self.positions)

    async def execution_rows(self, start, end):
        return self.execution

    async def performance(self, start=None, end=None):
        return {"daily": list(self.daily)}

    async def rule_signals(self, rule_id, start, end, limit=20_000):
        """A signal at 8:00 each day of the period, oldest first: the newest ``limit``."""
        self.asked.append((start, end, rule_id))
        rows = []
        for offset in range((end - start).days + 1):
            day = start + timedelta(days=offset)
            moment = datetime(day.year, day.month, day.day, 8, 0, tzinfo=timezone.utc)
            rows.append({
                "id": 7 + offset, "rule_id": rule_id, "action": "long", "indice": "us100", "confidence": None,
                "mode": "shadow", "bar_time": moment, "bar_closed_at": moment, "raised_at": moment,
                "alert_title": "Cross up", "alert_message": "{}",
                **({"script_version": self.version_of(day)} if self.version_of else {}),
                # What the trader made of it, when it was sent: a nested row of moments too.
                "trader": {"decided_at": moment, "outcome": None, "reason": None, "position_id": None},
            })
        return rows[-limit:]


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
    # Then the rule's report, of its days since it was made on Wednesday 14.
    assert asyncio.run(trading.catch_up()) == 4
    assert queries.asked == [*((date(2026, 10, day), date(2026, 10, day), SHADOW) for day in (15, 16, 19)),
                             (date(2026, 10, 14), date(2026, 10, 19), SHADOW)]
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
    # Kept apart from the list of runs, the report too; nothing asked twice.
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
    # 23:00 in Paris, 17:00 in New York: the same days as with the hours of Paris, and the report.
    assert asyncio.run(trading.catch_up()) == 4
    assert [asked[0] for asked in queries.asked] == [date(2026, 10, day) for day in (15, 16, 19, 14)]


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


# ── the reports: one run of the days since it began ──


def _report_of(backtests, owner: str = SHADOW) -> str:
    return backtests.paper_reports()[owner]["runs"][0]


def _request(tmp_path, run_id: str) -> dict:
    return json.loads((folder_of(tmp_path, run_id) / "request.json").read_text())


def _finish(tmp_path, backtests, run_id: str, total: float = 1.5) -> None:
    """The run done, as the command leaves it: its days, its summary, its state."""
    folder = folder_of(tmp_path, run_id)
    (folder / "days.json").write_text(json.dumps([{"day": "2026-10-19", "status": "tested", "day_percent": total, "trades": 2}]))
    (folder / "summary.json").write_text(json.dumps({
        "made_with": {"code": backtests.code}, "days": {"asked": 6, "tested": 4},
        "figures": {"total_percent": total, "trades": 9, "win_rate": 55.5, "worst_drawdown": -2.1, "sample": "few"},
        "money": {"profit_loss": 15.0},
    }))
    (folder / "status.json").write_text(json.dumps({"state": DONE, "finished_at": "2026-10-20T21:05:00Z"}))
    if run_id in backtests._waiting:
        backtests._waiting.remove(run_id)


def test_last_finished_day_is_the_newest_weekday_before_today():
    assert last_finished_day(date(2026, 10, 20)) == date(2026, 10, 19)
    # A Monday's is the Friday before, and so are the weekend's.
    assert last_finished_day(date(2026, 10, 19)) == date(2026, 10, 16)
    assert last_finished_day(date(2026, 10, 18)) == date(2026, 10, 16)


def test_a_shadow_rule_s_report_covers_its_days_since_it_was_made_with_every_signal_and_no_random_entries(tmp_path, paper):
    backtests, queries, trading = paper

    asyncio.run(trading.catch_up())

    run_id = _report_of(backtests)
    request = _request(tmp_path, run_id)
    assert request["paper"] == {"rule": SHADOW, "report": True}
    assert (request["from"], request["to"], request["name"]) == ("2026-10-14", "2026-10-19", "Paper: EMA cross on US100, since 2026-10-14")
    assert request["source"] == {"kind": "ta_signals", "signals": 6, "rule": {"id": SHADOW, "name": "EMA cross on US100"}}
    assert request["baseline"] == {"repetitions": 0, "seed": 1} and request["tags"] == ["paper"]
    assert len(json.loads((folder_of(tmp_path, run_id) / "ta_signals.json").read_text())) == 6
    # Kept apart from the list of runs, said to be waiting its turn until it is done.
    assert backtests.list_runs() == []
    shown = backtests.paper()["rules"][SHADOW]
    assert shown["report"] is None
    assert (shown["next_report"]["run_id"], shown["next_report"]["state"]) == (run_id, "queued")
    assert shown["next_report"]["position"] == 4


def test_a_report_says_the_versions_of_the_script_that_raised_its_signals(tmp_path, paper):
    backtests, queries, trading = paper
    queries.version_of = lambda day: 3 if day < date(2026, 10, 17) else 4

    asyncio.run(trading.catch_up())

    assert _request(tmp_path, _report_of(backtests))["source"]["versions"] == [3, 4]


def test_the_report_is_made_again_after_the_next_finished_day_and_the_older_one_let_go(tmp_path, paper):
    backtests, _, trading = paper
    asyncio.run(trading.catch_up())
    older = _report_of(backtests)
    _finish(tmp_path, backtests, older, total=1.5)

    shown = backtests.paper()["rules"][SHADOW]
    assert (shown["report"]["run_id"], shown["report"]["from"], shown["report"]["to"]) == (older, "2026-10-14", "2026-10-19")
    assert shown["report"]["headline"]["total_percent"] == 1.5 and shown["report"]["made_at"] == "2026-10-20T21:05:00Z"
    assert shown["next_report"] is None
    # Nothing more that evening.
    assert trading.reports_due() == []

    # Wednesday evening: Tuesday is finished, the report is made again to it.
    backtests._clock = lambda: EVENING + 86_400
    asyncio.run(trading.catch_up(EVENING + 86_400))
    newer, kept = backtests.paper_reports()[SHADOW]["runs"]
    assert kept == older and (_request(tmp_path, newer)["from"], _request(tmp_path, newer)["to"]) == ("2026-10-14", "2026-10-20")
    # The older is shown while the newer waits its turn.
    shown = backtests.paper()["rules"][SHADOW]
    assert shown["report"]["run_id"] == older and shown["next_report"]["run_id"] == newer

    _finish(tmp_path, backtests, newer, total=2.0)
    assert backtests.settle_paper_reports() == 1
    assert backtests.paper_reports()[SHADOW]["runs"] == [newer] and not folder_of(tmp_path, older).exists()
    assert backtests.paper()["rules"][SHADOW]["report"]["run_id"] == newer


def test_a_report_that_did_not_finish_is_said_beside_the_last_good_one_and_not_asked_again_that_day(tmp_path, paper):
    backtests, _, trading = paper
    asyncio.run(trading.catch_up())
    good = _report_of(backtests)
    _finish(tmp_path, backtests, good)
    wednesday = EVENING + 86_400
    backtests._clock = lambda: wednesday
    asyncio.run(trading.catch_up(wednesday))
    failed = _report_of(backtests)
    backtests._waiting.remove(failed)
    (folder_of(tmp_path, failed) / "status.json").write_text(json.dumps({"state": FAILED, "error": "The database is unavailable"}))

    shown = backtests.paper()["rules"][SHADOW]

    assert shown["report"]["run_id"] == good
    assert (shown["next_report"]["state"], shown["next_report"]["error"]) == ("failed", "The database is unavailable")
    assert trading.reports_due(wednesday) == []
    # Both kept until the next finished day's.
    assert backtests.settle_paper_reports() == 0 and backtests.paper_reports()[SHADOW]["runs"] == [failed, good]


@pytest.mark.parametrize("created", ["2026-01-05T09:00:00+00:00", None])
def test_a_report_is_held_to_the_longest_period_a_run_takes(tmp_path, paper, scripts, created):  # noqa: F811
    backtests, _, trading = paper
    rule = {**RULE, "created_at": created} if created else {key: value for key, value in RULE.items() if key != "created_at"}
    with open(RuleStore(scripts).path, "w", encoding="utf-8") as out_file:
        json.dump({"rules": [rule]}, out_file)

    asyncio.run(trading.catch_up())

    # 92 days to Monday 19 October.
    request = _request(tmp_path, _report_of(backtests))
    assert (request["from"], request["to"]) == ("2026-07-20", "2026-10-19")


def test_a_report_of_more_signals_than_a_run_takes_starts_at_the_first_whole_day_it_holds(tmp_path, paper, monkeypatch):
    backtests, _, trading = paper
    monkeypatch.setattr(paper_module, "MAX_RAISED_SIGNALS", 3)

    asyncio.run(trading.catch_up())

    # The newest three: Saturday 17 to Monday 19; Saturday may not be whole, so from Sunday.
    request = _request(tmp_path, _report_of(backtests))
    assert (request["from"], request["to"], request["source"]["signals"]) == ("2026-10-18", "2026-10-19", 2)
    assert request["name"] == "Paper: EMA cross on US100, since 2026-10-18"
    # Asked once: its newest goes to Monday already.
    assert trading.reports_due() == []


def test_a_rule_the_backtests_cannot_trade_leaves_the_others_on_paper(tmp_path, paper, scripts, caplog):  # noqa: F811
    backtests, _, trading = paper
    elsewhere = {**RULE, "id": "a00000000003", "name": "On the S&P 500", "indice": "us500"}
    with open(RuleStore(scripts).path, "w", encoding="utf-8") as out_file:
        json.dump({"rules": [elsewhere, RULE]}, out_file)

    asyncio.run(trading.catch_up())

    assert sorted(backtests.paper_index()[SHADOW]) == ["2026-10-15", "2026-10-16", "2026-10-19"]
    assert "a00000000003" not in backtests.paper_index() and "a00000000003" not in backtests.paper_reports()
    assert backtests.paper_reports()[SHADOW]["runs"]
    assert "'On the S&P 500' cannot be traded on paper" in caplog.text


def test_make_it_now_asks_a_shadow_rule_s_report_at_any_hour_and_once(tmp_path, paper):
    backtests, _, trading = paper
    # Tuesday afternoon, the market open: the last finished day is Monday.
    trading._clock = lambda: AFTERNOON

    asyncio.run(trading.ask_report(SHADOW))

    request = _request(tmp_path, _report_of(backtests))
    assert (request["from"], request["to"]) == ("2026-10-14", "2026-10-19")
    with pytest.raises(NotPossible, match="being made already"):
        asyncio.run(trading.ask_report(SHADOW))
    with pytest.raises(NotPossible, match="not in shadow"):
        asyncio.run(trading.ask_report(LIVE))
    with pytest.raises(RuleNotFound):
        asyncio.run(trading.ask_report("0123456789ab"))
    with pytest.raises(NotPossible, match="no day on paper yet"):
        asyncio.run(trading.ask_report(LIVE_PAPER))
    assert len(backtests.paper_reports()[SHADOW]["runs"]) == 1


def test_two_asks_at_once_make_one_report(tmp_path, paper):
    backtests, queries, trading = paper
    reading = queries.rule_signals

    async def both():
        gate = asyncio.Event()

        async def slow(*arguments, **named):
            await gate.wait()
            return await reading(*arguments, **named)

        queries.rule_signals = slow
        first = asyncio.create_task(trading.ask_report(SHADOW))
        await asyncio.sleep(0)
        # The first reads the rule's signals meanwhile: the second is refused, not queued as well.
        with pytest.raises(NotPossible, match="being made already"):
            await trading.ask_report(SHADOW)
        gate.set()
        await first

    asyncio.run(both())

    assert len(backtests.paper_reports()[SHADOW]["runs"]) == 1


def test_the_signals_received_have_their_report_since_their_first_day_on_paper(tmp_path, paper):
    backtests, queries, trading = paper
    queries.journal = [_received(day) for day in (15, 16, 19)]
    queries.execution = _measured_rows()

    asyncio.run(trading.catch_up())

    reports = backtests.paper_reports()
    assert reports[LIVE_PAPER]["since"] == "2026-10-15"
    run_id = reports[LIVE_PAPER]["runs"][0]
    request = _request(tmp_path, run_id)
    assert request["paper"] == {"rule": LIVE_PAPER, "report": True}
    assert (request["from"], request["to"], request["source"]["kind"]) == ("2026-10-15", "2026-10-19", "journal")
    assert request["name"] == "Paper: the signals received, since 2026-10-15"
    assert request["changes"] == {"execution": "measured"} and request["baseline"] == {"repetitions": 0, "seed": 1}
    # Set beside WATA's real positions of the period.
    assert (folder_of(tmp_path, run_id) / "positions.json").exists()
    assert backtests.list_runs() == []
    live = backtests.paper()["live"]
    assert live["next_report"]["run_id"] == run_id and len(live["days"]) == 3


def test_the_signals_received_keep_their_first_day_once_the_index_forgets_it(tmp_path, paper):
    backtests, queries, trading = paper
    queries.journal = [_received(day) for day in (15, 16, 19)]
    asyncio.run(trading.catch_up())
    first = _report_of(backtests, LIVE_PAPER)
    _finish(tmp_path, backtests, first)
    backtests._waiting.clear()
    backtests.prune_paper(4, date(2026, 10, 21))
    assert "2026-10-15" not in backtests.paper_index()[LIVE_PAPER]

    backtests._clock = lambda: EVENING + 86_400
    asyncio.run(trading.catch_up(EVENING + 86_400))

    assert (_request(tmp_path, _report_of(backtests, LIVE_PAPER))["from"]) == "2026-10-15"


def test_without_an_entry_signal_the_signals_received_have_no_report_until_the_next_day(tmp_path, paper):
    backtests, queries, trading = paper

    asyncio.run(trading.catch_up())
    reads = queries.journal_reads
    asyncio.run(trading.catch_up())

    assert backtests.paper_reports()[LIVE_PAPER] == {"since": "2026-10-15", "nothing_to": "2026-10-19"}
    assert queries.journal_reads == reads
    with pytest.raises(NotPossible, match="No signal of entry since 2026-10-15"):
        asyncio.run(trading.ask_report(LIVE_PAPER))


def test_reports_that_end_too_long_ago_are_let_go(tmp_path, paper):
    backtests, _, trading = paper
    asyncio.run(trading.catch_up())
    run_id = _report_of(backtests)
    backtests._waiting.clear()

    # Monday 19 is three days before Thursday 22.
    backtests.prune_paper(2, date(2026, 10, 22))

    assert backtests.paper_reports()[SHADOW]["runs"] == [] and not folder_of(tmp_path, run_id).exists()


def test_the_drift_reads_the_days_without_the_reports(paper):
    backtests, _, trading = paper
    asyncio.run(trading.catch_up())

    assert "report" not in backtests.paper(with_reports=False)["rules"][SHADOW]
    assert SHADOW in backtests.paper()["rules"] and "next_report" in backtests.paper()["rules"][SHADOW]


# ── what the service takes as a paper run ──


def _rule_body(**paper) -> dict:
    return {
        "from": "2026-10-14", "to": "2026-10-19", "indice": "us100", "source": {"kind": "ta_signals", "rule": {"id": SHADOW, "name": "EMA"}},
        "chart": {"asset": {"provider": "capital", "key": "US100"}, "side": "bid", "bars": {"type": "range", "ticks": 40}},
        "baseline": {"repetitions": 0, "seed": 1}, **({"paper": paper} if paper else {}),
    }


@pytest.mark.parametrize("paper_field", [
    {"rule": SHADOW, "day": "2026-10-14"},
    {"rule": "a00000000009", "report": True},
    {"rule": SHADOW},
])
def test_a_paper_run_is_one_day_or_a_report_of_its_own_rule(tmp_path, paper, paper_field):
    backtests, _, _ = paper

    with pytest.raises(InvalidRequest, match="A paper run is one day"):
        backtests.create(_rule_body(**paper_field), journal=[])


def test_a_run_made_from_a_paper_run_is_an_ordinary_one(tmp_path, paper):
    backtests, _, _ = paper
    made = backtests.create(_rule_body(rule=SHADOW, report=True), journal=[])
    _finish(tmp_path, backtests, made["id"])

    stressed = backtests.stress(made["id"])

    assert "paper" not in _request(tmp_path, stressed["id"])
    assert [run["id"] for run in backtests.list_runs()] == [stressed["id"]]
    # Its signals go with it.
    assert (folder_of(tmp_path, stressed["id"]) / "ta_signals.json").exists()
