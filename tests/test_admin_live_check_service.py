"""
The live check of a signal rule (src/admin/live_check_service.py): the backtest configuration linked to it, its days
read from the runs and the database, and that configuration made again each evening with what the live trades measured.
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

from src.admin import live_check as lc  # noqa: E402
from src.admin.backtest_configurations import UnknownConfiguration  # noqa: E402
from src.admin.errors import InvalidRequest, NotPossible, UnknownRun  # noqa: E402
from src.admin.live_check_service import LiveCheck  # noqa: E402
from src.admin.paper import rule_body  # noqa: E402
from src.admin.processes import DONE  # noqa: E402
from src.admin.ta_rules import RuleStore  # noqa: E402
from tests.test_admin_backtests import config, folder_of, pine, scripts, service  # noqa: E402, F401

# Tuesday 20 October 2026, 23:00 in Paris: the example's market (8 to 22) closed, the day's hours over.
EVENING = datetime(2026, 10, 20, 21, 0, tzinfo=timezone.utc).timestamp()
RULE_ID = "a1b2c3d4e5f6"
DAY = "2026-10-15"
AT = datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc)


def utc(hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 10, 15, hour, minute, second, tzinfo=timezone.utc)


def text(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


class Queries:
    """The database as the live check reads it: the rule's signals, its positions, the ledger, the engine, the balance."""

    def __init__(self):
        self.rows, self.positions, self.ledger_positions, self.starts = [], [], [], []
        self.balance = 21.82

    async def rule_signals(self, rule_id, start, end, limit=20_000):
        return [row for row in self.rows if start <= row["raised_at"].date() <= end]

    async def closed_positions(self, start=None, end=None):
        return list(self.positions)

    async def execution_ledger_rows(self, start, end):
        return {"positions": list(self.ledger_positions), "alerts": [], "quotes": [],
                "engine": [{"signal_uuid": "u1", "bar_closed_at": utc(9, 0, 10)}]}

    async def engine_starts(self, start, end):
        return {"starts": list(self.starts), "kept_since": utc(0)}

    async def last_balance(self):
        return self.balance

    async def execution_rows(self, start, end):
        return {}


def _signal(number: int, at: datetime, title: str, action: str, **more) -> dict:
    return {"id": number, "day": DAY, "source": "pine", "action": action, "arrived_at": text(at), "decided_at": text(at),
            "fate": more.get("fate", "executed"), "reason": None, "rule": None, "trade_id": more.get("trade_id"),
            "bar_time": text(at), "bar_closed_at": text(at), "alert": {"type": "alertcondition", "title": title}}


def _trade(number: int, signal_id: int, entry: float, exit_: float) -> dict:
    return {"id": number, "day": DAY, "direction": "long", "signal_id": signal_id, "turbo": {"uic": 555, "description": "Turbo"},
            "entry": {"decided_at": text(utc(9, 0, 13)), "filled_at": text(utc(9, 0, 14)), "price": entry},
            "exit": {"filled_at": text(utc(9, 30)), "bid": exit_, "reason": "Performance (Daily profit target (3.0%))"},
            "performance_percent": round((exit_ / entry - 1) * 100, 2), "max_performance_percent": 3.0,
            "min_performance_percent": -1.0, "units": 3, "flags": []}


def _finish(tmp_path, backtests, run_id: str, days: list[str], signals: list[dict], trades: list[dict]) -> None:
    folder = folder_of(tmp_path, run_id)
    (folder / "days.json").write_text(json.dumps([{"day": day, "status": "tested", "day_percent": 0.0} for day in days]))
    (folder / "signals.json").write_text(json.dumps(signals))
    (folder / "trades.json").write_text(json.dumps(trades))
    (folder / "summary.json").write_text(json.dumps({"made_with": {"code": backtests.code}, "figures": {}}))
    (folder / "status.json").write_text(json.dumps({"state": DONE}))
    backtests._waiting.clear()


@pytest.fixture
def setup(tmp_path, scripts, config):  # noqa: F811
    backtests = service(tmp_path, scripts, config, clock=lambda: EVENING)
    template = backtests.create(pine(scripts))["id"]
    _finish(tmp_path, backtests, template, ["2026-10-13", "2026-10-14", DAY, "2026-10-16"],
            [_signal(1, AT, "Enter Long", "long", trade_id=1)], [_trade(1, 1, 5.0, 5.15)])
    ran = backtests.request_of(template)["source"]["rule"]
    rule = {key: ran[key] for key in ("asset", "side", "bars", "script", "libraries", "inputs", "alerts", "alert_calls")}
    rule.update(id=RULE_ID, name="Cross R60", mode="live", indice="us100", created_at="2026-10-10T08:00:00+00:00",
                mode_changed_at="2026-10-14T06:00:00+00:00", history_days=backtests.settings_of(template)["history_days"])
    rules = RuleStore(scripts)
    with open(rules.path, "w", encoding="utf-8") as out_file:
        json.dump({"rules": [rule]}, out_file)
    queries = Queries()
    check = LiveCheck(root=str(tmp_path / "backtests"), backtests=backtests, queries=queries, rules=rules,
                      config_path=config, clock=lambda: EVENING)
    return types.SimpleNamespace(backtests=backtests, queries=queries, check=check, template=template, rule=rule,
                                 rules=rules, configuration=backtests.configuration_of(template), tmp_path=tmp_path)


def _with_live_day(setup) -> None:
    """15 October: the paper day of the rule's signal, and the real position it opened."""
    rows = [{"id": 1, "rule_id": RULE_ID, "bar_time": AT, "bar_closed_at": utc(9, 0, 10), "alert_title": "Enter Long",
             "action": "long", "mode": "live", "raised_at": utc(9, 0, 11), "outcome": "sent", "sent_at": utc(9, 0, 12),
             "signal_uuid": "u1", "detail": None,
             "trader": {"decided_at": utc(9, 0, 20), "outcome": "executed", "reason": None, "position_id": "P1"}}]
    setup.queries.rows = rows
    setup.queries.positions = [{
        "position_id": "P1", "action": "long", "instrument_uic": 555, "instrument_name": "Turbo",
        "execution_time_open": utc(9, 0, 16), "execution_time_close": utc(9, 31), "position_open_price": 5.05,
        "position_close_price": 5.12, "position_total_performance_percent": 1.39, "position_max_performance_percent": 2.9,
        "position_min_performance_percent": -1.1, "position_close_reason": "Performance (Daily profit target (3.0%))",
    }]
    setup.queries.ledger_positions = [{
        **setup.queries.positions[0], "buy_sent_at": utc(9, 0, 14), "buy_accepted_at": utc(9, 0, 15), "buy_price_seen": 5.0,
        "signal_uuid": "u1", "signal_timestamp": utc(9, 0, 10), "received_timestamp": utc(9, 0, 12),
        "first_sale_at": utc(9, 30, 58), "sale_requested_at": utc(9, 30, 58), "sale_price_seen": 5.14,
        "sale_reason": "Performance (Daily profit target (3.0%))", "sale_accepted_at": utc(9, 30, 59),
    }]
    paper = setup.backtests.create(rule_body(setup.rule, date(2026, 10, 15), date(2026, 10, 15), report=False),
                                   journal=[{**rows[0], "raised_at": text(rows[0]["raised_at"])}], capital=21.82)["id"]
    _finish(setup.tmp_path, setup.backtests, paper, [DAY], [_signal(1, AT, "Enter Long", "long", trade_id=1)],
            [_trade(1, 1, 5.02, 5.16)])


# ── the link ──

def test_a_configuration_playing_the_rule_s_strategy_is_linked_and_offered(setup):
    linked = setup.check.link(RULE_ID, setup.configuration)

    assert linked["strategy"]["same"] is True
    assert setup.check.link_of(RULE_ID)["configuration"] == setup.configuration
    assert [found["configuration"] for found in setup.check.candidates(RULE_ID)] == [setup.configuration]
    setup.check.unlink(RULE_ID)
    assert setup.check.link_of(RULE_ID) is None


def test_a_configuration_of_another_strategy_is_refused(setup):
    other = {**setup.rule, "bars": {"type": "range", "ticks": 80}}
    with open(setup.rules.path, "w", encoding="utf-8") as out_file:
        json.dump({"rules": [other]}, out_file)

    with pytest.raises(NotPossible, match="do not play 'Cross R60': Another asset, side or bars"):
        setup.check.link(RULE_ID, setup.configuration)
    assert setup.check.candidates(RULE_ID) == []
    with pytest.raises(UnknownConfiguration):
        setup.check.link(RULE_ID, "0123456789ab")


# ── the days ──

def test_the_days_set_the_backtest_paper_and_live_side_by_side(setup):
    setup.check.link(RULE_ID, setup.configuration)
    _with_live_day(setup)

    found = asyncio.run(setup.check.summary(RULE_ID))

    assert found["period"] == {"from": "2026-10-10", "to": "2026-10-20"}
    days = {day["day"]: day for day in found["days"]}
    assert sorted(days) == ["2026-10-13", "2026-10-14", DAY, "2026-10-16"]
    assert days[DAY]["present"] == ["backtest", "paper", "live"]
    assert days["2026-10-13"]["present"] == ["backtest"]
    assert days[DAY]["gap"] == {"against": "live", "points": pytest.approx(1.39 - 3.0)}
    assert days[DAY]["runs"]["backtest"] == setup.template
    assert found["summary"]["tracks"]["live"] == {"days": 1, "trades": 1, "sum": 1.39, "mean": 1.39, "compounded": 1.39}
    assert found["summary"]["agreement"]["same_bar"] == 1
    # The same turbo: the gap from paper to live in its parts, which add up.
    to_live = found["summary"]["to_live"]
    assert {bucket["id"] for bucket in to_live["buckets"]} == {"entry_price", "exit_moment", "exit_fill"}
    assert to_live["start"]["sum"] + sum(bucket["points"] for bucket in to_live["buckets"]) == pytest.approx(to_live["end"]["sum"])
    assert found["link"]["configuration"] == setup.configuration and found["link"]["nightly"]["on"] is False
    assert found["strategy"]["same"] is True and found["template"]["id"] == setup.template
    # What the live trades measure for the calibrated run: one entry, too few for the delay; the account.
    now = found["calibration"]["now"]
    assert now["values"] == {"capital": 21.82} and now["measured"]["signal_delay_seconds"]["count"] == 1


def test_a_day_says_each_signal_and_each_trade(setup):
    setup.check.link(RULE_ID, setup.configuration)
    _with_live_day(setup)

    found = asyncio.run(setup.check.day(RULE_ID, date(2026, 10, 15)))

    assert [signal["key"] for signal in found["signals"]] == [lc.key_of(AT, "Enter Long")]
    trade = found["trades"][0]
    assert trade["backtest"]["entry_price"] == 5.0 and trade["paper"]["entry_price"] == 5.02
    assert trade["live"]["decided_price"] == 5.14 and trade["live"]["order_seconds"] == 4.0
    assert trade["same_turbo"] is True
    with pytest.raises(NotPossible, match="Nothing of the rule"):
        asyncio.run(setup.check.day(RULE_ID, date(2026, 10, 19)))
    with pytest.raises(NotPossible, match="not over yet"):
        asyncio.run(setup.check.day(RULE_ID, date(2026, 10, 21)))


def test_without_a_link_paper_and_live_are_still_set_side_by_side(setup):
    _with_live_day(setup)

    found = asyncio.run(setup.check.summary(RULE_ID))

    assert found["link"] is None and [day["present"] for day in found["days"]] == [["paper", "live"]]
    assert found["calibration"]["now"] is None


def test_a_period_is_checked(setup):
    with pytest.raises(InvalidRequest, match="ends before it starts"):
        asyncio.run(setup.check.summary(RULE_ID, date(2026, 10, 16), date(2026, 10, 15)))


# ── the calibrated run ──

def test_each_evening_the_configuration_is_made_again_with_what_the_live_trades_measured(setup):
    setup.check.link(RULE_ID, setup.configuration)
    _with_live_day(setup)

    assert asyncio.run(setup.check.catch_up()) == 1

    kept = json.loads((setup.tmp_path / "backtests" / "live_check" / "calibrated.json").read_text())[RULE_ID]
    request = setup.backtests.request_of(kept["run"])
    assert request["calibrated_of"] == setup.template and request["calibrated"] == {"rule": RULE_ID, "values": {"capital": 21.82}}
    assert request["baseline"] == {"repetitions": 0, "seed": 1} and request["name"].startswith("Calibrated · ")
    # Its changes said in its request too: "Run again with changes" opens the form with them.
    assert request["changes"] == {"capital": 21.82}
    assert (request["from"], request["to"]) == ("2026-10-13", "2026-10-16")
    assert setup.backtests.settings_of(kept["run"])["capital"] == 21.82
    # A run of its template's configuration, not judged, never made again by a remake.
    assert setup.backtests.configuration_of(kept["run"]) == setup.configuration
    _finish(setup.tmp_path, setup.backtests, kept["run"], ["2026-10-13"], [], [])
    assert "live check" in setup.backtests._why_not_remade(kept["run"])
    assert setup.backtests.listed(kept["run"])["calibrated_of"] == setup.template
    history = setup.check.history(RULE_ID)
    assert history[0]["run"] == kept["run"] and any("delay keeps" in said for said in history[0]["said"])
    # Asked once an evening while it waits or is made.
    assert asyncio.run(setup.check.catch_up()) == 0


def test_nothing_is_asked_while_the_market_is_open_or_the_line_is_busy(setup):
    setup.check.link(RULE_ID, setup.configuration)
    afternoon = datetime(2026, 10, 20, 13, 0, tzinfo=timezone.utc).timestamp()

    assert asyncio.run(setup.check.catch_up(afternoon)) == 0
    setup.backtests._waiting.append("20261020-120000-abcdef")
    assert asyncio.run(setup.check.catch_up()) == 0


def test_once_done_the_older_calibrated_runs_are_let_go(setup):
    setup.check.link(RULE_ID, setup.configuration)
    asyncio.run(setup.check.catch_up())
    first = setup.check._read("calibrated.json")[RULE_ID]["run"]
    _finish(setup.tmp_path, setup.backtests, first, ["2026-10-13"], [], [])
    assert setup.check.settle() == 1

    # The next evening, the template made again by the nightly: a new calibrated run, the older let go once it is done.
    setup.check._change("calibrated.json", lambda kept: kept[RULE_ID].update(day="2026-10-19"))
    assert asyncio.run(setup.check.catch_up()) == 0  # same template and values: nothing new to make
    setup.check._change("calibrated.json", lambda kept: kept[RULE_ID].update(day="2026-10-19", template="20261001-000000-aaaaaa"))
    assert asyncio.run(setup.check.catch_up()) == 1
    second = setup.check._read("calibrated.json")[RULE_ID]["run"]
    _finish(setup.tmp_path, setup.backtests, second, ["2026-10-13"], [], [])
    setup.check.settle()

    assert not folder_of(setup.tmp_path, first).exists() and folder_of(setup.tmp_path, second).exists()


def test_only_a_finished_pine_run_is_calibrated(setup, scripts):  # noqa: F811
    with pytest.raises(UnknownRun):
        setup.backtests.calibrate("20261001-000000-aaaaaa", name="x", changes={}, execution=None, calibrated={})
    waiting = setup.backtests.create(pine(scripts))["id"]
    with pytest.raises(NotPossible, match="Only a finished run"):
        setup.backtests.calibrate(waiting, name="x", changes={}, execution=None, calibrated={})
    with pytest.raises(InvalidRequest, match="not a setting"):
        setup.backtests.calibrate(setup.template, name="x", changes={"nonsense": 1}, execution=None, calibrated={})
    with pytest.raises(NotPossible, match="Too few of WATA's orders"):
        setup.backtests.calibrate(setup.template, name="x", changes={"execution": "measured"}, execution={"samples": {}},
                                  calibrated={})


def test_a_rule_made_after_the_last_finished_day_has_an_empty_period_not_a_refusal(setup):
    rule = {**setup.rule, "created_at": "2026-10-21T08:00:00+00:00"}
    with open(setup.rules.path, "w", encoding="utf-8") as out_file:
        json.dump({"rules": [rule]}, out_file)

    found = asyncio.run(setup.check.summary(RULE_ID))

    assert found["period"] == {"from": "2026-10-20", "to": "2026-10-20"} and found["days"] == []
