"""A backtest run checked against funded challenges (src/admin/propfirm_check.py), with the LuxAlgo of the tests."""

import json

import pytest

from src.admin import propfirm_check
from src.admin.luxalgo import CONTEXT, PROPFIRMS, LuxAlgo
from src.admin.propfirm_check import NotCheckable, figures, r_series, request_of
from tests.test_admin_api import PASSWORD, SAME_ORIGIN, config_path, make_client, make_ctx  # noqa: F401 (fixtures)
from tests.test_admin_luxalgo import FakeLuxAlgo
from tests.test_admin_backtests import NOW, config, pine, scripts  # noqa: F401 (fixtures)
from tests.test_mcp_tools_read import answer, finished_run, folder_of, journal, refusal, research  # noqa: F401 (fixtures)

RUN = "20261030-101010-abcdef"


def trade(number: int, result: float, closed: str) -> dict:
    return {"id": number, "day": closed[:10], "performance_percent": result,
            "exit": {"filled_at": closed, "reason": "x"}}


# Twelve trades over three days, closed out of order: one past its stop (a knock-out).
TRADES = [trade(index, result, f"2026-10-{13 + index % 3:02d}T{8 + index:02d}:00:00Z")
          for index, result in enumerate([5.0, -10.0, 2.5, -4.0, 7.5, -10.0, 10.0, -100.0, 3.0, -2.0, 6.0, 1.0])]
DAYS = [{"day": f"2026-10-{day}", "status": "tested"} for day in (13, 14, 15)] + [{"day": "2026-10-16", "status": "closed"}]
SETTINGS = {"stoploss_percent": -10.0}


def test_a_runs_trades_become_r_multiples_in_the_order_they_closed():
    series = r_series(TRADES, -10.0)
    ordered = sorted(TRADES, key=lambda entry: entry["exit"]["filled_at"])
    assert series == [round(entry["performance_percent"] / 10, 4) for entry in ordered]
    assert min(series) == -10.0
    with pytest.raises(NotCheckable, match="no stop-loss"):
        r_series(TRADES, 0)
    with pytest.raises(NotCheckable, match="10 trades at least: this run made 3"):
        r_series(TRADES[:3], -10.0)


def test_what_a_run_says_of_itself_for_the_simulator():
    said = figures(TRADES, DAYS, SETTINGS)
    assert (said["trades"], said["days"], said["trades_per_day"]) == (12, 3, 4.0)
    assert said["win_rate"] == round(7 / 12, 4)
    assert any("advises a hundred" in note for note in said["notes"])
    assert any("1 trade(s) lost more than the stop" in note for note in said["notes"])


def test_the_request_is_the_figures_the_risk_and_the_scope_alone():
    said = figures(TRADES, DAYS, SETTINGS)
    asked = request_of(said, risk_percent=0.5, product_type="futures", account_max=100_000)
    assert set(asked) == {"rSeries", "tradesPerDay", "riskMode", "riskValue", "productType", "minPassPerAttempt",
                          "accountSizeMax"}
    assert asked["minPassPerAttempt"] == propfirm_check.BAR and asked["riskMode"] == "percent-of-balance"
    for wrong in ({"risk_percent": 9.0, "product_type": "futures"}, {"risk_percent": 0.5, "product_type": "stocks"},
                  {"risk_percent": 0.5, "product_type": "cfd", "account_min": 10}):
        with pytest.raises(NotCheckable):
            request_of(said, **wrong)


class FakeService:
    """A finished run, as the routes read it."""

    def __init__(self):
        self.files = {}

    async def run(self):
        """The admin's lifespan runs the backtests asked for: none here."""

    async def close(self):
        pass

    def trades(self, run_id):
        return TRADES

    def days(self, run_id):
        return DAYS

    def settings_of(self, run_id):
        return SETTINGS

    def kept(self, run_id, name):
        return self.files.get(name)

    def keep(self, run_id, name, content):
        self.files[name] = content


@pytest.fixture
def setup(config_path, tmp_path):
    ctx = make_ctx(config_path, tmp_path)
    ctx.settings.backtests_enabled = True
    ctx.backtests = FakeService()
    fake = FakeLuxAlgo()
    ctx.luxalgo = LuxAlgo(server=fake.server)
    with make_client(ctx, tmp_path / "no-interface") as client:
        assert client.post("/api/session", json={"password": PASSWORD}, headers=SAME_ORIGIN).status_code == 204
        yield client, ctx, fake


def test_a_run_is_checked_against_funded_challenges_and_the_answer_kept(setup):
    client, ctx, fake = setup
    before = client.get(f"/api/backtests/{RUN}/propfirms").json()
    assert before["kept"] is None and before["figures"]["trades"] == 12

    checked = client.post(f"/api/backtests/{RUN}/propfirms", json={"risk_percent": 0.75, "account_max": 100_000},
                          headers=SAME_ORIGIN)

    assert checked.status_code == 200
    [(tool, sent)] = fake.received
    assert tool == "propfirms_validate_strategy" and sent["context"] == CONTEXT[PROPFIRMS]
    assert sent["rSeries"] == r_series(TRADES, -10.0) and sent["riskValue"] == 0.75 and sent["accountSizeMax"] == 100_000
    # Only figures leave: no id of the run, the account or its value.
    assert RUN not in json.dumps(sent)
    kept = ctx.backtests.files[propfirm_check.FILE]
    assert kept["answer"]["passing"][0]["passPerAttempt"] == 0.62 and "rSeries" not in kept["asked"]
    assert client.get(f"/api/backtests/{RUN}/propfirms").json()["kept"]["bar"] == 0.5


def test_a_run_that_cannot_be_checked_says_why(setup):
    client, ctx, fake = setup
    ctx.backtests.settings_of = lambda run_id: {}
    said = client.get(f"/api/backtests/{RUN}/propfirms").json()
    assert said["figures"] is None and "no stop-loss" in said["refused"]
    refused = client.post(f"/api/backtests/{RUN}/propfirms", json={}, headers=SAME_ORIGIN)
    assert refused.status_code == 422 and fake.received == []
    ctx.luxalgo = None
    assert client.post(f"/api/backtests/{RUN}/propfirms", json={}, headers=SAME_ORIGIN).status_code == 409


def test_the_research_tools_give_a_runs_r_series(tmp_path, research, scripts):
    run_id = finished_run(tmp_path, research, scripts, {
        "summary.json": {"made_with": {"code": "x"}}, "trades.json": TRADES, "days.json": DAYS,
        "settings.json": {"settings": SETTINGS},
    })
    said = answer(research, "backtest_result", {"run_id": run_id, "part": "r_series"})
    assert said["trades"] == 12 and said["r_series"] == r_series(TRADES, -10.0)
    (folder_of(tmp_path, run_id) / "settings.json").write_text(json.dumps({"settings": {}}))
    assert "no stop-loss" in refusal(research, "backtest_result", {"run_id": run_id, "part": "r_series"})
