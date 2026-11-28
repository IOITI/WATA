"""A run's pace and the trading days to come, as the run page's "At this pace" draws them (src/admin/run_projection.py)."""

import math
from datetime import datetime

import pytest
import pytz

from src.admin.run_projection import NotProjectable, pace, projection, run_projection, seed_of
from src.backtest.settings import BacktestSettings
from src.trade.market_calendar import read_market_calendar
from tests.test_admin_api import PASSWORD, SAME_ORIGIN, config_path, make_client, make_ctx  # noqa: F401 (fixtures)

RUN_ID = "20261128-101010-abcdef"
CALENDAR = read_market_calendar()
# A Saturday: the days to come start on Monday 30 November.
NOW = datetime(2026, 11, 28, 15, 0, tzinfo=pytz.utc)
TESTED = ["2026-11-16", "2026-11-17", "2026-11-18", "2026-11-19", "2026-11-20", "2026-11-23", "2026-11-24",
          "2026-11-25", "2026-11-26", "2026-11-27"]
RULES = [{"rule_type": "market_closed_dates", "rule_config": {"market_closed_dates": ["31/12/2026", "nope"]}}]


def trade(number: int, day: str, profit: float, decided: str = "15:00", closed: str = "15:30") -> dict:
    return {"id": number, "day": day, "profit_loss": profit, "performance_percent": profit / 10,
            "entry": {"decided_at": f"{day}T{decided}:00Z", "filled_at": f"{day}T{decided}:01Z"},
            "exit": {"filled_at": f"{day}T{closed}:00Z"}}


def run(**more) -> dict:
    return {"id": RUN_ID, "kind": "run", "status": {"state": "done"}, "stale": False,
            "summary": {"figures": {"trades": 10}, "money": {"capital": 1000.0}}, **more}


DAYS = [{"day": day, "status": "tested"} for day in TESTED] + [
    {"day": "2026-11-21", "status": "closed"}, {"day": "2026-11-22", "status": "closed"}]
TRADES = [
    trade(1, "2026-11-16", 20.0),
    trade(2, "2026-11-17", -10.0),
    # A morning and an afternoon: an early close keeps the first (entries end at 17:54 UTC in November).
    trade(3, "2026-11-18", 30.0, decided="15:00"),
    trade(4, "2026-11-18", -15.0, decided="19:00", closed="20:00"),
    trade(5, "2026-11-19", 5.0, decided="17:00", closed="18:30"),  # still held past an early close's sale (17:55)
    # Held overnight: its result stays whole on the day it was opened.
    trade(6, "2026-11-20", 40.0, closed="23:00"),
    trade(7, "2026-11-23", -25.0),
    trade(8, "2026-11-24", 10.0),
    trade(9, "2026-11-25", 15.0),
    trade(10, "2026-11-27", 12.0),
]


def projected(days=DAYS, trades=TRADES, settings=None, rules=RULES, signals=(), **more) -> dict:
    return projection(run(**more), days, trades, settings or {}, rules, "Europe/Paris", NOW,
                      signals_of=lambda: list(signals), calendar=CALENDAR)


def test_the_pace_is_the_accounts_growth_day_by_day_from_its_capital():
    answer = projected()
    growths = answer["pace"]["growths"]

    assert answer["usable"] and answer["capital"] == 1000.0
    assert answer["pace"]["days_used"] == ["2026-11-16", "2026-11-17", "2026-11-18", "2026-11-19", "2026-11-20",
                                           "2026-11-23", "2026-11-24", "2026-11-25"]
    assert growths[:2] == [1.02, round(1010 / 1020, 8)]
    assert growths[2] == round(1025 / 1010, 8)  # both trades of the day
    # The days used and the run's early close, one after the other, are the account's whole result.
    total = math.prod(growths) * answer["pace"]["early_growths"][-1]
    assert total == pytest.approx(1 + sum(t["profit_loss"] for t in TRADES) / 1000, abs=1e-6)
    assert answer["pace"]["account_return_percent"] == pytest.approx(8.2)
    assert answer["pace"]["trades_used"] == 10 and answer["pace"]["sample"] == "too_few"


def test_a_us_holiday_traded_at_nothing_is_left_out_and_an_early_close_drawn_as_a_morning():
    answer = projected()
    left_out = answer["pace"]["left_out"]

    assert left_out == [{"day": "2026-11-26", "why": "us_closed", "name": "Thanksgiving Day", "estimated": False}]
    early = dict(zip(answer["pace"]["early_days_used"], answer["pace"]["early_growths"]))
    assert early["2026-11-18"] == round(1040 / 1010, 8)  # the morning trade alone
    assert early["2026-11-16"] == 1.02
    assert early["2026-11-27"] == round(1082 / 1070, 8)  # the run's own early close, as it stands
    assert answer["pace"]["held_past_early_sale"] == 2  # the trade sold at 18:30 and the one held overnight


def test_a_day_of_a_year_the_file_does_not_hold_is_read_by_the_rules():
    days = [{"day": "2025-11-27", "status": "tested"}, {"day": "2025-11-28", "status": "tested"}]
    trades = [trade(1, "2025-11-28", 10.0)]

    found = pace(days, trades, BacktestSettings(), 1000.0, CALENDAR)

    assert found["left_out"] == [{"day": "2025-11-27", "why": "us_closed", "name": "Thanksgiving Day",
                                  "estimated": True}]
    assert found["early_days_used"] == ["2025-11-28"] and found["growths"] == []


def test_the_runs_own_closed_dates_are_left_out():
    answer = projected(settings={"closed_dates": ["17/11/2026"]})

    assert {"day": "2026-11-17", "why": "closed_date", "name": None, "estimated": False} in answer["pace"]["left_out"]
    assert "2026-11-17" not in answer["pace"]["days_used"]


def test_once_the_account_guard_paused_entries_the_days_after_are_left_out():
    signals = [{"day": "2026-11-20", "rule": "account_guard"}, {"day": "2026-11-23", "rule": "account_guard"}]

    answer = projected(settings={"pause_below_balance_eur": 900}, signals=signals)

    assert answer["pace"]["days_used"][-1] == "2026-11-20"
    assert [day["why"] for day in answer["pace"]["left_out"]] == ["after_guard"] * 5
    assert any("account guard" in note for note in answer["notes"])
    # Without the guard set, its refusals are not even read.
    assert projected(signals=signals)["pace"]["guard_day"] is None


def test_an_emptied_account_ends_the_pace_on_that_day():
    trades = [trade(1, "2026-11-16", 20.0), trade(2, "2026-11-17", -1020.0), trade(3, "2026-11-18", 5.0)]

    answer = projected(trades=trades)

    assert answer["pace"]["growths"] == [1.02, 0.0]
    assert answer["pace"]["emptied"] and answer["pace"]["geometric_mean_percent"] == -100.0
    assert {day["why"] for day in answer["pace"]["left_out"]} == {"after_empty"}
    assert not answer["usable"]  # two days to draw from


def test_a_run_without_trades_or_with_too_few_days_says_why():
    assert projected(trades=[])["why_not"] == "The run made no trade: there is no pace to draw"
    few = projected(days=DAYS[:3])
    assert not few["usable"] and few["why_not"].startswith("3 day(s) to draw from")


def test_a_sweep_or_a_run_not_finished_is_refused():
    with pytest.raises(NotProjectable, match="sweep"):
        projected(kind="sweep")
    with pytest.raises(NotProjectable, match="not finished"):
        projected(status={"state": "running"})


def test_the_days_to_come_run_two_years_from_the_next_weekday_without_holidays_nor_closed_dates():
    answer = projected()
    days = {day["day"]: day for day in answer["days"]}
    skipped = {day["day"]: day for day in answer["skipped"]}

    assert (answer["from"], answer["to"]) == ("2026-11-30", "2028-11-29")
    assert "2026-12-25" not in days and skipped["2026-12-25"]["why"] == "us_closed"
    assert skipped["2027-01-01"]["name"] == "New Year's Day"
    assert skipped["2026-12-31"] == {"day": "2026-12-31", "why": "config_closed", "name": "A closed date of WATA's",
                                     "estimated": False}
    assert days["2026-12-24"]["kind"] == "early_close" and days["2028-11-24"]["kind"] == "early_close"
    assert answer["calendar"] == {"until": "2028-12-31", "estimated_from": None, "error": None,
                                  "config_unreadable": ["nope"]}
    assert 495 < len(answer["days"]) < 510


def test_without_the_file_every_day_is_the_rules_and_said_estimated():
    answer = projection(run(), DAYS, TRADES, {}, [], "Europe/Paris", NOW, calendar=None, calendar_error="gone")

    assert answer["calendar"]["estimated_from"] == "2026-11-30" and answer["calendar"]["error"] == "gone"
    assert {"2026-12-25", "2027-01-01"} <= {day["day"] for day in answer["skipped"]}
    assert all(day["estimated"] for day in answer["days"] + answer["skipped"])
    assert answer["pace"]["left_out"][0]["estimated"] is True


def test_the_seed_is_the_runs_and_fits_a_browsers_numbers():
    assert seed_of(RUN_ID) == seed_of(RUN_ID) != seed_of("20261128-101010-abcdee")
    assert 0 <= seed_of(RUN_ID) < 2 ** 32


def test_a_stale_or_derived_run_is_said_so():
    notes = projected(stale=True, decomposes="x", paper={"rule": "r", "report": True})["notes"]

    assert any("older backtest code" in note for note in notes)
    assert any("day's caps" in note for note in notes)
    assert any("paper report" in note for note in notes)


class FakeService:
    """A finished run, as the route reads it."""

    def __init__(self, kind="run"):
        self.kind = kind
        self.read = []

    async def run(self):
        """The admin's lifespan runs the backtests asked for: none here."""

    async def close(self):
        pass

    def get(self, run_id):
        return run(kind=self.kind)

    def days(self, run_id):
        self.read.append("days")
        return DAYS

    def trades(self, run_id):
        return TRADES

    def settings_of(self, run_id):
        return {}

    def signals(self, run_id):
        return []


@pytest.fixture
def client_of(config_path, tmp_path):
    def make(service, enabled=True):
        ctx = make_ctx(config_path, tmp_path)
        ctx.settings.backtests_enabled = enabled
        ctx.backtests = service
        return make_client(ctx, tmp_path / "no-interface")
    return make


def test_the_route_answers_the_projection_of_a_run(client_of):
    with client_of(FakeService()) as client:
        assert client.post("/api/session", json={"password": PASSWORD}, headers=SAME_ORIGIN).status_code == 204
        answer = client.get(f"/api/backtests/{RUN_ID}/projection").json()

    assert answer["usable"] and answer["run_id"] == RUN_ID
    assert answer["pace"]["days_used"][0] == "2026-11-16"
    assert answer["days"] and answer["calendar"]["until"] == "2028-12-31"


def test_the_route_refuses_a_sweep_in_its_answer_without_reading_its_days(client_of):
    service = FakeService(kind="sweep")
    with client_of(service) as client:
        assert client.post("/api/session", json={"password": PASSWORD}, headers=SAME_ORIGIN).status_code == 204
        answer = client.get(f"/api/backtests/{RUN_ID}/projection").json()

    assert answer["usable"] is False and "sweep" in answer["why_not"] and service.read == []


def test_the_route_is_closed_while_backtests_are_off(client_of):
    with client_of(FakeService(), enabled=False) as client:
        assert client.post("/api/session", json={"password": PASSWORD}, headers=SAME_ORIGIN).status_code == 204
        assert client.get(f"/api/backtests/{RUN_ID}/projection").status_code == 403


def test_reading_the_file_fails_over_to_the_rules(monkeypatch):
    from src.admin import run_projection as module

    def unreadable():
        raise OSError("no file")

    monkeypatch.setattr(module, "read_market_calendar", unreadable)

    answer = run_projection(run(), DAYS, TRADES, {}, [], "Europe/Paris", NOW)

    assert answer["calendar"]["error"] == "no file" and answer["calendar"]["estimated_from"] == "2026-11-30"
