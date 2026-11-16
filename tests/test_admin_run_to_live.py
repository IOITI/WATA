"""
A backtest's settings applied to WATA (src/admin/run_to_live.py and the routes
of src/admin/routers/backtests.py): what differs between the settings a run
traded with and config.json's now, read from the run's raw values (an old run's
missing settings are no difference), the window around US releases added or
removed as a rule, its releases as one list, the timezone of the trading one
as a key removed, a rule added for a setting kept in it; then config.json saved
as the Configuration page saves it, journaled as saved from the backtest and
audited, refused once the file moved on, for a sweep, for a day on paper.
"""

import json

import pytest

from src.admin import run_to_live
from src.admin.config_editor import ConfigEditor
from src.admin.errors import InvalidRequest
from src.backtest.settings import from_config
from tests.test_admin_api import SAME_ORIGIN, config_path, make_ctx, signed_in  # noqa: F401 (fixtures)

RUN_ID = "20261116-210000-abc123"
STOP = "trade.config.position_management.performance_thresholds.stoploss_percent"
COOLDOWN = "trade.rules[risk_management].rule_config.cooldown_after_loss_minutes"


def live_of(config_path) -> dict:
    return ConfigEditor(config_path).load()["config"]


def run_file(config: dict, changed: dict | None = None, **values) -> dict:
    """settings.json as a run keeps it: every setting it traded with, those it changed and their live values then."""
    settings = from_config(config).to_dict()
    live_before = {name: settings[name] for name in changed or {}}
    settings.update(changed or {})
    settings.update(values)
    return {"settings": settings, "changed": dict(changed or {}), "live_before": live_before}


def rows_by_name(found: dict) -> dict:
    return {row["setting"]: row for row in found["differences"]}


def test_nothing_differs_for_a_run_of_today_s_settings(config_path):
    config = live_of(config_path)

    assert run_to_live.differences(run_file(config), config) == {"differences": [], "backtest_only": []}


def test_a_run_s_own_change_and_a_live_change_made_since_are_told_apart(config_path):
    config = live_of(config_path)
    # The run asked for a tighter stop; the cooldown was 5 min on its day and is 10 now.
    found = run_to_live.differences(run_file(config, {"stoploss_percent": -10, "capital": 5000},
                                             cooldown_after_loss_minutes=5), config)
    rows = rows_by_name(found)

    assert rows["stoploss_percent"] == {
        "setting": "stoploss_percent", "label": "Stop-loss", "unit": "%", "group": "exits", "before": -20, "after": -10,
        "own": True, "paths": [STOP], "apply": True, "why_not": None, "note": None,
    }
    assert rows["cooldown_after_loss_minutes"]["own"] is False
    assert (rows["cooldown_after_loss_minutes"]["before"], rows["cooldown_after_loss_minutes"]["after"]) == (10, 5)
    # Only a backtest has a capital: said, never a row to apply.
    assert found["backtest_only"] == [{"setting": "capital", "label": "Account at the start", "unit": "€", "value": 5000}]


def test_an_old_run_without_a_setting_is_no_difference_and_its_raw_values_are_read(config_path):
    config = live_of(config_path)
    kept = run_file(config)
    # Made before funds were retried and quotes waited for: from_dict would read them as 0 and off.
    for name in ("funds_retries", "funds_retry_percent", "wait_for_fresh_quote", "news_ppi"):
        del kept["settings"][name]

    assert run_to_live.differences(kept, config)["differences"] == []


def test_a_value_config_json_would_refuse_is_shown_and_cannot_be_applied(config_path):
    config = live_of(config_path)
    found = rows_by_name(run_to_live.differences(run_file(config, {"trading_end_hour": 24}), config))

    assert found["trading_end_hour"]["apply"] is False
    assert found["trading_end_hour"]["why_not"] == "No entry from is from 0 up to 23 in config.json"
    with pytest.raises(InvalidRequest, match="Cannot be applied: No entry from"):
        run_to_live.placed(run_file(config, {"trading_end_hour": 24}), config, ["trading_end_hour"])


def test_a_setting_of_a_rule_config_json_has_not_is_applied_by_adding_the_rule(config_path):
    config = live_of(config_path)
    config["trade"]["rules"] = [rule for rule in config["trade"]["rules"] if rule["rule_type"] != "risk_management"]
    kept = run_file(config, {"max_consecutive_losses": 3})

    row = rows_by_name(run_to_live.differences(kept, config))["max_consecutive_losses"]
    assert row["apply"] is True and row["note"] == "Adds the risk management rule to config.json"
    changed = run_to_live.placed(kept, config, ["max_consecutive_losses"])
    risk = next(rule for rule in changed["trade"]["rules"] if rule["rule_type"] == "risk_management")
    assert risk["rule_config"] == {"max_consecutive_losses": 3}
    assert all(rule["rule_type"] != "risk_management" for rule in config["trade"]["rules"]), "a copy is changed"


def test_the_window_around_us_releases_is_added_as_a_rule_with_the_run_s_minutes_and_releases(config_path):
    config = live_of(config_path)
    kept = run_file(config, {"news_window": True, "news_minutes_before": 10, "news_ppi": True, "news_pce": False})

    found = rows_by_name(run_to_live.differences(kept, config))
    # One row for the window: its minutes and releases go with it.
    assert set(found) == {"news_window"}
    assert found["news_window"]["note"] == ("Adds the rule: no entry from 10 min before a release to 15 min after it "
                                            "(45 after an FOMC statement), around FOMC, CPI, NFP, PPI")
    changed = run_to_live.placed(kept, config, ["news_window"])
    news = next(rule for rule in changed["trade"]["rules"] if rule["rule_type"] == "macro_news")
    assert news["rule_config"] == {"minutes_before": 10, "minutes_after": 15, "minutes_after_fomc": 45,
                                   "flatten_minutes_before": 0, "events": ["FOMC", "CPI", "NFP", "PPI"]}
    assert from_config(changed).news_window is True


def test_the_window_is_removed_and_its_releases_changed_as_one_list(config_path):
    config = live_of(config_path)
    config["trade"]["rules"].append({"rule_type": "macro_news", "rule_name": "macro_news",
                                     "rule_config": {"minutes_before": 5, "events": ["FOMC", "CPI"]}})

    off = run_file(config, {"news_window": False})
    assert set(rows_by_name(run_to_live.differences(off, config))) == {"news_window"}
    removed = run_to_live.placed(off, config, ["news_window"])
    assert all(rule["rule_type"] != "macro_news" for rule in removed["trade"]["rules"])

    more = run_file(config, {"news_nfp": True, "news_minutes_before": 8})
    rows = rows_by_name(run_to_live.differences(more, config))
    assert rows["news_events"]["before"] == ["FOMC", "CPI"] and rows["news_events"]["after"] == ["FOMC", "CPI", "NFP"]
    assert rows["news_events"]["own"] is True and rows["news_minutes_before"]["after"] == 8
    changed = run_to_live.placed(more, config, ["news_events", "news_minutes_before"])
    news = next(rule for rule in changed["trade"]["rules"] if rule["rule_type"] == "macro_news")
    assert news["rule_config"] == {"minutes_before": 8, "events": ["FOMC", "CPI", "NFP"]}


def test_the_hours_timezone_is_set_or_removed_for_the_trading_one(config_path):
    config = live_of(config_path)
    kept = run_file(config, {"market_timezone": "America/New_York"})
    changed = run_to_live.placed(kept, config, ["market_timezone"])
    hours = next(rule for rule in changed["trade"]["rules"] if rule["rule_type"] == "market_hours")
    assert hours["rule_config"]["timezone"] == "America/New_York"

    back = run_file(changed, {"market_timezone": "trading"})
    row = rows_by_name(run_to_live.differences(back, changed))["market_timezone"]
    assert row["note"] == "The hours are read in the trading timezone again: config.json's key is removed"
    again = run_to_live.placed(back, changed, ["market_timezone"])
    hours = next(rule for rule in again["trade"]["rules"] if rule["rule_type"] == "market_hours")
    assert "timezone" not in hours["rule_config"]


def test_the_engine_s_days_are_noted_and_a_section_config_json_lacks_is_not_made_up(config_path):
    config = live_of(config_path)
    row = rows_by_name(run_to_live.differences(run_file(config, {"history_days": 3}), config))["history_days"]

    assert row["note"] == run_to_live.ENGINE_NOTE
    assert row["apply"] is False and row["why_not"] == "config.json has no trade.config.ta_engine section to keep it in"


def test_a_name_no_longer_different_is_refused(config_path):
    config = live_of(config_path)

    with pytest.raises(InvalidRequest, match="No longer different"):
        run_to_live.placed(run_file(config), config, ["stoploss_percent"])
    with pytest.raises(InvalidRequest, match="at least one"):
        run_to_live.placed(run_file(config), config, [])


def test_the_audit_names_each_setting_from_the_live_value_to_the_run_s():
    rows = [{"setting": "stoploss_percent", "label": "Stop-loss", "before": -20, "after": -10},
            {"setting": "trailing_enabled", "label": "Trailing stop", "before": False, "after": True}]

    assert run_to_live.said(rows, ["stoploss_percent", "trailing_enabled"]) == "Stop-loss -20 → -10; Trailing stop off → on"


# ── the routes ──


def write_run(root, config: dict, *, kind="run", state="done", paper=None, **changes) -> None:
    folder = root / "runs" / RUN_ID
    folder.mkdir(parents=True)
    request = {"kind": kind, "name": "Tighter stop", "from": "2026-11-02", "to": "2026-11-13"}
    if paper:
        request["paper"] = paper
    (folder / "request.json").write_text(json.dumps(request))
    (folder / "status.json").write_text(json.dumps({"state": state}))
    (folder / "settings.json").write_text(json.dumps(run_file(config, changes)))


@pytest.fixture
def applying(config_path, tmp_path):
    from src.admin.backtests import BacktestService
    from src.admin.ta_scripts import ScriptStore

    async def never(arguments, log_path):
        raise AssertionError("nothing is to be run")

    ctx = make_ctx(config_path, tmp_path, backtests_enabled=True)
    ctx.backtests = BacktestService(root=str(tmp_path / "backtests"), prices_root=str(tmp_path / "prices"),
                                    config_path=config_path, scripts=ScriptStore(str(tmp_path / "ta")), launcher=never)
    return ctx


def test_a_run_s_settings_are_previewed_then_saved_as_the_configuration_page_saves(applying, config_path, tmp_path):
    ctx = applying
    write_run(tmp_path / "backtests", live_of(config_path), stoploss_percent=-10)
    with signed_in(ctx, tmp_path) as client:
        found = client.get(f"/api/backtests/{RUN_ID}/to-live").json()
        preview = client.post(f"/api/backtests/{RUN_ID}/to-live/preview", json={"settings": ["stoploss_percent"]},
                              headers=SAME_ORIGIN)
        saved = client.post(f"/api/backtests/{RUN_ID}/to-live/apply",
                            json={"settings": ["stoploss_percent"], "revision": preview.json()["revision"]},
                            headers=SAME_ORIGIN)
        again = client.post(f"/api/backtests/{RUN_ID}/to-live/apply",
                            json={"settings": ["stoploss_percent"], "revision": preview.json()["revision"]},
                            headers=SAME_ORIGIN)
        after = client.get(f"/api/backtests/{RUN_ID}/to-live").json()

    assert found["run"] == {"id": RUN_ID, "name": "Tighter stop"}
    assert [row["setting"] for row in found["differences"]] == ["stoploss_percent"]
    assert preview.status_code == 200
    assert [(change["path"], change["before"], change["after"]) for change in preview.json()["changes"]] == [(STOP, -20, -10)]
    assert "position_monitor" in preview.json()["impact"]["services"]
    assert saved.status_code == 200 and saved.json()["restart_required"] is True
    with open(config_path) as kept:
        assert json.load(kept)["trade"]["config"]["position_management"]["performance_thresholds"]["stoploss_percent"] == -10
    history = ConfigEditor(config_path).history()
    assert history[0]["action"] == "saved from the backtest 'Tighter stop'"
    audit = (tmp_path / "log" / "wata-admin" / "audit.jsonl").read_text().splitlines()
    entry = json.loads(audit[-1])
    assert entry["action"] == "configuration saved"
    assert entry["detail"].startswith(f"From the backtest 'Tighter stop' ({RUN_ID}): Stop-loss -20 → -10. Changed {STOP}.")
    # Already applied: nothing differs, and the same save again is refused rather than made twice.
    assert again.status_code == 422 and "No longer different" in again.json()["detail"]
    assert after["differences"] == []


def test_a_save_after_config_json_moved_on_is_refused(applying, config_path, tmp_path):
    ctx = applying
    write_run(tmp_path / "backtests", live_of(config_path), stoploss_percent=-10, cooldown_after_loss_minutes=3)
    with signed_in(ctx, tmp_path) as client:
        revision = client.post(f"/api/backtests/{RUN_ID}/to-live/preview", json={"settings": ["stoploss_percent"]},
                               headers=SAME_ORIGIN).json()["revision"]
        client.post(f"/api/backtests/{RUN_ID}/to-live/apply", json={"settings": ["cooldown_after_loss_minutes"],
                                                                     "revision": revision}, headers=SAME_ORIGIN)
        stale = client.post(f"/api/backtests/{RUN_ID}/to-live/apply",
                            json={"settings": ["stoploss_percent"], "revision": revision}, headers=SAME_ORIGIN)

    assert stale.status_code == 409


def test_config_json_s_validator_says_why_a_preview_is_refused(applying, config_path, tmp_path):
    ctx = applying
    # Turbos of one price: a run takes it, the trader's validator wants the lowest under the highest.
    write_run(tmp_path / "backtests", live_of(config_path), turbo_min_price=10, turbo_max_price=10)
    with signed_in(ctx, tmp_path) as client:
        response = client.post(f"/api/backtests/{RUN_ID}/to-live/preview",
                               json={"settings": ["turbo_min_price", "turbo_max_price"]}, headers=SAME_ORIGIN)

    assert response.status_code == 422
    assert "Minimum price (10) must be less than maximum price (10)" in response.json()["detail"]


def test_the_difference_is_read_with_editing_off_and_nothing_saved(applying, config_path, tmp_path):
    ctx = applying
    ctx.settings.config_edit_enabled = False
    write_run(tmp_path / "backtests", live_of(config_path), stoploss_percent=-10)
    with signed_in(ctx, tmp_path) as client:
        found = client.get(f"/api/backtests/{RUN_ID}/to-live")
        preview = client.post(f"/api/backtests/{RUN_ID}/to-live/preview", json={"settings": ["stoploss_percent"]},
                              headers=SAME_ORIGIN)
        saved = client.post(f"/api/backtests/{RUN_ID}/to-live/apply",
                            json={"settings": ["stoploss_percent"], "revision": "x"}, headers=SAME_ORIGIN)

    assert found.status_code == 200 and len(found.json()["differences"]) == 1
    assert (preview.status_code, saved.status_code) == (403, 403)


@pytest.mark.parametrize("made, said", [
    ({"kind": "sweep"}, "A sweep's combinations"),
    ({"paper": {"rule": "live"}}, "A day on paper"),
    ({"state": "running"}, "Only a finished run"),
])
def test_a_sweep_a_day_on_paper_or_an_unfinished_run_has_nothing_to_apply(applying, config_path, tmp_path, made, said):
    ctx = applying
    write_run(tmp_path / "backtests", live_of(config_path), stoploss_percent=-10, **{"kind": "run", **made})
    with signed_in(ctx, tmp_path) as client:
        response = client.get(f"/api/backtests/{RUN_ID}/to-live")

    assert response.status_code == 409 and response.json()["detail"].startswith(said)
