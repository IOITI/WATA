"""A signal rule's protections (src/admin/live_safety.py): each protection's state, and what the account buys."""

from datetime import date, datetime, timezone

import pytest
import pytz

from src.admin import live_safety as ls
from src.backtest.settings import BacktestSettings
from tests.test_admin_api import PASSWORD, SAME_ORIGIN, config_path, ctx, make_client  # noqa: F401 (fixtures)
from tests.test_admin_ta_rules import created, cross, rules, scripts  # noqa: F401 (fixtures)

RULE = {"id": "f13986be5e9b", "name": "Kernel VWAP", "indice": "us100", "mode": "live"}
ZONE = pytz.timezone("Europe/Paris")
# 15:40 in Paris, 9:40 in New York: an hour every time scale lets whole.
AT = datetime(2026, 11, 24, 14, 40, tzinfo=timezone.utc).timestamp()


def _config(*, risk: dict | None = None, day: dict | None = None, macro: dict | None = None) -> dict:
    rules = [
        {"rule_type": "day_trading", "rule_config": {"max_day_loss_percent": -25.0, **(day or {})}},
        {"rule_type": "risk_management", "rule_config": {"max_trades_per_day": 0, **(risk or {})}},
    ]
    if macro is not None:
        rules.append({"rule_type": "macro_news", "rule_config": macro})
    return {"trade": {"rules": rules, "config": {}}}


def _entry(ask: float, at: float = AT, confidence: float | None = None) -> dict:
    return {"at": at, "ask": ask, "confidence": confidence}


def test_every_protection_leads_to_where_it_is_set():
    assert ls.config_link(ls.FLOOR) == {"config": {"group": "day", "path": ls.FLOOR}}
    for path, group in ((ls.MAX_DAY_LOSS, "day"), (ls.LOSSES_IN_A_ROW, "day"), (ls.EDGE_GUARD, "day"),
                        (ls.RELEASES, "hours"), (ls.SOURCES, "signals"), (ls.FULL_SESSION, "saxo"), (ls.SAFETY, "sizing")):
        assert ls.config_link(path)["config"]["group"] == group, path
    assert ls.config_link("trade.config.nothing") is None
    # A rule not in config.json has no row of its keys on the page: its block's heading has.
    assert ls.config_link(ls.RELEASES, whole_block=True) == {"config": {"group": "hours", "path": ls.RELEASES, "block": "news"}}


def test_what_21_82_eur_buys_with_one_safety_turbo():
    entries = [_entry(ask) for ask in (8.5, 10.9, 10.95, 12.97, 14.3)]

    found = ls.funds_what_if(BacktestSettings(), 21.82, entries, {"kind": "backtest", "name": "c18c49"})

    # An order at full scale needs the ask twice over (one safety turbo): 21.82 / 2.
    assert found["dearest_ask"] == 10.91
    assert (found["entries"], found["affordable"], found["affordable_without_safety"]) == (5, 2, 5)
    assert found["needed_for_all"] == 28.6 and found["needed_for_most"] == 28.6
    assert found["dearest_entry_ask"] == 14.3

    check = ls.funds(found)
    assert check["state"] == ls.FAIL and check["link"] == ls.config_link(ls.SAFETY)
    assert check["detail"] == (
        "At 21.82 €, an order at full scale buys a turbo of 10.91 € at most: 2 of the 5 entries of c18c49, the others "
        "refused for funds; with no safety turbo, 5. An account of 28.60 € buys them all."
    )
    # Ten entries, one of them dear: 90 % of them are bought from less than all of them.
    spread = ls.funds_what_if(BacktestSettings(), 21.82, [_entry(10.0)] * 8 + [_entry(10.95), _entry(14.3)], None)
    assert (spread["needed_for_most"], spread["needed_for_all"]) == (21.9, 28.6)
    said = ls.funds(spread)["detail"]
    assert "8 of the 10 entries of its backtest, the others refused" in said
    assert said.endswith("An account of 28.60 € buys them all, 90 % of them from 21.90 €.")
    one = ls.funds(ls.funds_what_if(BacktestSettings(), 21.82, [_entry(10.0), _entry(14.3)], None))["detail"]
    assert "1 of the 2 entries of its backtest, the other refused for funds" in one


def test_the_entries_are_sized_as_the_trader_scales_them():
    settings = BacktestSettings(time_scaling_enabled=True, time_periods=BacktestSettings().time_periods)
    scaled = ls.funds_what_if(settings, 100.0, [_entry(30.0)], None)
    whole = ls.funds_what_if(BacktestSettings(), 100.0, [_entry(30.0)], None)
    assert whole["affordable"] == 1 and whole["needed_for_all"] == 60.0
    # Whatever the periods say at that minute, the value needed grows as the scale shrinks, never the other way.
    assert scaled["needed_for_all"] >= whole["needed_for_all"]


def test_every_entry_bought_and_no_entry_to_set_it_against():
    every = ls.funds(ls.funds_what_if(BacktestSettings(), 50.0, [_entry(9.0), _entry(12.0)], {"kind": "paper", "name": "its 3 days on paper"}))
    assert every["state"] == ls.PASS
    assert every["detail"] == "At 50.00 €, an order at full scale buys a turbo of 25.00 € at most: every one of the 2 entries of its 3 days on paper."

    none = ls.funds(ls.funds_what_if(BacktestSettings(), 50.0, [], None))
    assert none["state"] == ls.OPEN and "link a backtest of the rule" in none["detail"]

    unknown = ls.funds(ls.funds_what_if(BacktestSettings(), None, [_entry(9.0)], None))
    assert unknown["state"] == ls.OPEN and "not known yet" in unknown["detail"]


def test_a_run_s_entries_are_its_trades_and_the_signals_it_refused_for_funds():
    trades = [{"confidence": 0.8, "entry": {"selected_ask": 11.2, "ask": 11.4, "selected_at": "2026-11-24T14:40:00Z"}}]
    signals = [
        {"rule": "insufficient_funds", "decided_at": "2026-11-24T14:20:00Z", "confidence": None,
         "candidates": [{"ask": 9.0, "chosen": False}, {"ask": 13.1, "chosen": True}]},
        {"rule": "cooldown", "decided_at": "2026-11-24T14:30:00Z", "candidates": None},
    ]

    found = ls.entries_of(trades, signals)

    assert [(entry["ask"], entry["confidence"]) for entry in found] == [(13.1, None), (11.2, 0.8)]
    assert found[1]["at"] == AT


def test_the_signal_source_saxo_and_the_pause():
    assert ls.signal_source(RULE, {"sources": {"us100": "engine"}})["state"] == ls.PASS
    tradingview = ls.signal_source(RULE, {"sources": {}})
    assert tradingview["state"] == ls.FAIL and "from TradingView's alerts: going live is refused" in tradingview["detail"]
    assert ls.signal_source(RULE, None)["state"] == ls.OPEN

    authorised = {"where": "database", "has_token": True, "refresh_token_expired": False}
    assert ls.saxo({**authorised, "has_token": False}, None, False)["state"] == ls.FAIL
    assert ls.saxo(authorised, {"trade_level": "FullTradingAndChat"}, False)["state"] == ls.PASS
    lower = ls.saxo(authorised, {"trade_level": "OrdersOnly"}, False)
    assert lower["state"] == ls.OPEN and lower["link"] == ls.config_link(ls.FULL_SESSION)
    refused = ls.saxo(authorised, {"trade_level": "OrdersOnly", "wanted": "FullTradingAndChat", "request_error": "409"}, True)
    assert refused["state"] == ls.FAIL and "(409)" in refused["detail"]
    assert ls.saxo(None, None, False)["state"] == ls.OPEN

    assert ls.paused(None, None, None, ZONE)["state"] == ls.PASS
    by_guard = ls.paused(datetime(2026, 11, 24, 14, 5, tzinfo=timezone.utc), datetime.now(timezone.utc), None, ZONE)
    assert by_guard["state"] == ls.FAIL and by_guard["detail"].startswith("Paused by the account guard since 24 Nov 15:05")
    assert "by the edge guard" in ls.paused("2026-11-24T14:05:00Z", None, "2026-11-24T14:05:00Z", ZONE)["detail"]


def test_the_day_s_limits_breakers_and_the_account_guard():
    limits = ls.day_limits(_config(risk={"max_trades_per_day": 4, "cooldown_after_loss_minutes": 30}))
    assert limits["state"] == ls.PASS
    assert limits["detail"] == "No new entry once the day is at -25 %; 4 trades a day at most; 30 min without an entry after a stop-loss."
    assert ls.day_limits(_config(day={"max_day_loss_percent": -100}))["state"] == ls.OPEN

    assert ls.breakers(_config())["state"] == ls.OPEN
    on = ls.breakers(_config(risk={"max_consecutive_losses": 3, "max_drawdown_from_day_best_percent": 2.5}))
    assert on["state"] == ls.PASS
    assert on["detail"] == "No new entry for the rest of the day after 3 losing trades in a row or once the day is 2.5 points under its best."

    assert ls.account_guard(_config(), 21.82)["state"] == ls.OPEN
    # Without the risk_management rule, which is optional, its keys have no row: their block's heading leads there.
    without = {"trade": {"rules": [{"rule_type": "day_trading", "rule_config": {"max_day_loss_percent": -25.0}}]}}
    assert ls.account_guard(without, 21.82)["link"]["config"]["block"] == ls.breakers(without)["link"]["config"]["block"]
    assert "block" not in ls.account_guard(_config(), 21.82)["link"]["config"]
    assert ls.edge_guard(RULE, enabled=True, guard_on=False, entry=_judged(), live_reference=False,
                         risk_rule=False)["link"]["config"]["block"] == ls.breakers(without)["link"]["config"]["block"]
    floor = ls.account_guard(_config(risk={"pause_below_balance_eur": 15, "pause_after_drawdown_percent": 30}), 21.82)
    assert floor["state"] == ls.PASS and floor["detail"] == (
        "Pauses new entries once the account is under 15.00 € or 30 % under its highest since entries were last resumed.")
    above = ls.account_guard(_config(risk={"pause_below_balance_eur": 25}), 21.82)
    assert above["state"] == ls.FAIL and "above the account's value (21.82 €)" in above["detail"]


def _judged(verdict: str = "as_expected", *, sample: str = "fair", differ: list[str] | None = None, real: bool = True) -> dict:
    track = {"verdict": verdict, "n": 7, "tested": 7, "reasons": ["its reference cannot be drawn from: 8 days"],
             "settings_differ": differ or []}
    return {"reference": {"run_id": "20261121-100000-abcdef", "name": "Kernel VWAP, from the rule"},
            "model": {"reference": {"sample": sample}},
            "tracks": {"paper": {**track, "n": 3}, **({"real": track} if real else {})}}


def test_the_edge_guard_on_this_rule():
    off = ls.edge_guard(RULE, enabled=False, guard_on=True, entry=None, live_reference=False)
    assert off["state"] == ls.OPEN and "needs the backtests" in off["detail"]

    none = ls.edge_guard(RULE, enabled=True, guard_on=True, entry=None, live_reference=True)
    assert none["state"] == ls.FAIL and none["link"] == {"href": "/backtests/new?rule=f13986be5e9b"}
    assert "Meanwhile its real trades are judged with WATA's other real trades" in none["detail"]
    assert ls.edge_guard(RULE, enabled=True, guard_on=False, entry=None, live_reference=False)["state"] == ls.OPEN

    able = ls.edge_guard(RULE, enabled=True, guard_on=True, entry=_judged(), live_reference=False)
    assert able["state"] == ls.PASS and able["detail"].endswith("7 of the 20 real trades it waits for so far.")
    switched_off = ls.edge_guard(RULE, enabled=True, guard_on=False, entry=_judged(), live_reference=False)
    assert switched_off["state"] == ls.OPEN and switched_off["link"] == ls.config_link(ls.EDGE_GUARD)

    for entry, said in ((_judged("no_edge_over_random"), "no better than random entries"),
                        (_judged(sample="too_few"), "too few trades or days"),
                        (_judged(differ=["stoploss_percent"]), "(stoploss_percent)"),
                        (_judged("no_model"), "cannot be drawn from")):
        unable = ls.edge_guard(RULE, enabled=True, guard_on=True, entry=entry, live_reference=False)
        assert unable["state"] == ls.FAIL and said in unable["detail"], said
        assert unable["link"] == {"href": "/backtests/20261121-100000-abcdef"}

    shadow = ls.edge_guard({**RULE, "mode": "shadow"}, enabled=True, guard_on=True, entry=_judged(real=False), live_reference=False)
    assert shadow["state"] == ls.PASS and "once it is live" in shadow["detail"]


def test_the_window_around_us_releases_and_telegram():
    today = date(2026, 11, 24)
    off = ls.release_window(_config(), None, True, today)
    assert off["state"] == ls.OPEN and off["link"]["config"]["block"] == "news"
    macro = {"minutes_before": 5, "minutes_after": 15, "flatten_minutes_before": 2}
    on = ls.release_window(_config(macro=macro), date(2027, 6, 30), True, today)
    assert on["state"] == ls.PASS and on["detail"] == (
        "No entry from 5 min before FOMC, CPI, NFP, PCE to 15 min after (45 after an FOMC statement); what is held is "
        "sold 2 min before. Its calendar is known until 2027-06-30.")
    ending = ls.release_window(_config(macro=macro), date(2026, 12, 10), True, today)
    assert ending["state"] == ls.FAIL and "known until 2026-12-10" in ending["detail"]
    assert ls.release_window(_config(macro=macro), None, False, today)["state"] == ls.FAIL
    assert ls.release_window(_config(macro={"events": ["NOPE"]}), None, True, today)["state"] == ls.FAIL

    assert ls.telegram(True)["state"] == ls.PASS
    assert ls.telegram(False)["state"] == ls.FAIL
    assert ls.telegram(None)["state"] == ls.OPEN


def test_the_checks_come_in_the_page_s_order_and_a_fact_not_read_is_said():
    checks = {
        "telegram": ls.telegram(True), "paused": ls.paused(None, None, None, ZONE),
        "signal_source": ls.not_read("signal_source", "Signal source", "the web server did not answer"),
    }
    listed = ls.protections(checks)
    assert [check["id"] for check in listed] == ["signal_source", "paused", "telegram"]
    assert listed[0] == {"id": "signal_source", "label": "Signal source", "state": ls.OPEN,
                         "detail": "Not read: the web server did not answer", "link": None}


# ── the route ──

@pytest.fixture
def signed_in(ctx, scripts, tmp_path):
    ctx.ta_scripts = scripts
    with make_client(ctx, tmp_path / "no-interface") as client:
        assert client.post("/api/session", json={"password": PASSWORD}, headers=SAME_ORIGIN).status_code == 204
        yield client


def test_the_route_says_each_protection_of_a_rule_in_the_page_s_order(signed_in, ctx, rules, cross):
    rule = created(rules, cross)["rule"]

    answer = signed_in.get(f"/api/ta/rules/{rule['id']}/protections")

    assert answer.status_code == 200
    found = answer.json()
    assert [check["id"] for check in found["checks"]] == [
        "signal_source", "saxo", "paused", "funds", "day_limits", "breakers", "account_guard", "edge_guard",
        "release_window", "telegram",
    ]
    states = {check["id"]: check["state"] for check in found["checks"]}
    # The test web server takes us100 from TradingView: going live would be refused.
    assert states["signal_source"] == ls.FAIL
    # No backtest to set the account against, no Edge Watch running: said open, never guessed.
    assert states["funds"] == ls.OPEN and states["edge_guard"] == ls.OPEN
    assert found["funds"]["entries"] == 0 and found["rule"] == rule["id"]

    ctx.services.webhook = {"sources": {"us100": "engine"}, "ignored": {}}
    again = {check["id"]: check["state"] for check in signed_in.get(f"/api/ta/rules/{rule['id']}/protections").json()["checks"]}
    assert again["signal_source"] == ls.PASS


def test_the_route_says_a_fact_it_could_not_read_and_knows_no_other_rule(signed_in, ctx, rules, cross):
    rule = created(rules, cross)["rule"]
    ctx.services.web_server_down = True

    checks = {check["id"]: check for check in signed_in.get(f"/api/ta/rules/{rule['id']}/protections").json()["checks"]}

    assert checks["signal_source"]["state"] == ls.OPEN and checks["signal_source"]["detail"].startswith("Not read: ")
    assert signed_in.get("/api/ta/rules/0123456789ab/protections").status_code == 404
    ctx.ta_scripts = None
    assert signed_in.get(f"/api/ta/rules/{rule['id']}/protections").status_code == 503
