"""
The signal rules of the Technical Analysis pages (src/admin/ta_rules.py) and
their routes: what a rule pins, how it is checked, and how one goes live.
"""

import hashlib
import json
import os
from datetime import date, datetime, timedelta, timezone

import pytest

from src.admin.health import ta_engine_status
from src.admin.ta_compare import compare, default_tolerance
from src.admin.ta_rules import (
    RUN_FIELDS,
    InvalidRule,
    RuleConflict,
    RuleNotFound,
    RuleStore,
    checked_bars,
    timeframe_name,
    timeframe_seconds,
)
from src.admin.ta_scripts import ScriptConflict, ScriptStore
from tests.test_admin_api import (  # noqa: F401 (fixtures)
    PASSWORD,
    SAME_ORIGIN,
    config_path,
    ctx,
    make_client,
    make_ctx,
)

STRATEGY = """//@version=6
indicator("Cross", overlay = true)
import someone/Bands/1 as bands
fast = input.int(9, "Fast")
plot(bands.mid(close, fast), "Mid")
alertcondition(ta.crossover(close, bands.mid(close, fast)), title = "Enter Long", message = '{"action": "long"}')
alertcondition(ta.crossunder(close, bands.mid(close, fast)), title = "Exit", message = '{"action": "close-position"}')
"""

BANDS = """//@version=6
library("Bands")
import someone/Maths/1 as maths

export mid(float source, int length) =>
    maths.half(ta.highest(source, length) + ta.lowest(source, length))
"""

MATHS = """//@version=6
library("Maths")

export half(float value) =>
    value / 2
"""

LIBRARY_ONLY = """//@version=6
library("Lonely")

export one() =>
    1
"""


@pytest.fixture
def scripts(tmp_path):
    store = ScriptStore(str(tmp_path / "ta"))
    store.create("Maths", MATHS)
    store.create("Bands", BANDS)
    return store


@pytest.fixture
def cross(scripts):
    return scripts.create("Cross", STRATEGY)


@pytest.fixture
def rules(scripts):
    return RuleStore(scripts)


def fields(script, /, **changes):
    rule = {
        "name": "Cross on US100",
        "asset": {"provider": "capital", "key": "US100"},
        "side": "bid",
        "bars": {"type": "time", "timeframe": "1"},
        "script": {"id": script["id"], "version": script["version"]},
        "inputs": [{"id": "in_0", "var": "fast", "title": "Fast", "value": 12}],
        "alerts": [{"title": "Enter Long", "action": "long"}, {"title": "Exit", "action": "close-position"}],
        "alert_calls": "ignore",
        "confidence_plot": None,
        "indice": "us100",
    }
    rule.update(changes)
    return rule


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def created(rules, script, /, **changes):
    return rules.create(fields(script, **changes), rules.load()["revision"])


# ── the store ──


def test_a_rule_pins_its_script_and_each_library_dependencies_first(rules, scripts, cross):
    result = created(rules, cross)

    rule = result["rule"]
    assert rule["mode"] == "shadow"
    assert rule["script"] == {"id": cross["id"], "version": 1, "sha256": sha(STRATEGY)}
    assert [(entry["name"], entry["version"], entry["sha256"]) for entry in rule["libraries"]] == [
        ("Maths", 1, sha(MATHS)),
        ("Bands", 1, sha(BANDS)),
    ]
    assert result["revision"] == rules.load()["revision"] != hashlib.sha256(b"").hexdigest()


def test_the_rules_are_one_file_people_and_agents_can_read(rules, scripts, cross):
    rule = created(rules, cross)["rule"]

    with open(os.path.join(scripts.root, "rules.json"), encoding="utf-8") as rules_file:
        stored = json.load(rules_file)

    assert stored["rules"] == [rule]
    assert set(rule) == {
        "id", "name", "asset", "side", "bars", "script", "libraries", "inputs", "alerts", "alert_calls",
        "confidence_plot", "indice", "mode", "created_at", "updated_at", "mode_changed_at",
    }


def test_a_change_made_on_an_older_revision_is_refused(rules, cross):
    stale = rules.load()["revision"]
    created(rules, cross)

    with pytest.raises(RuleConflict, match="changed meanwhile"):
        rules.create(fields(cross, name="Another"), stale)


def test_names_are_unique_whatever_their_case(rules, cross):
    created(rules, cross)

    with pytest.raises(RuleConflict, match="already exists"):
        created(rules, cross, name="cross ON us100")


def test_a_rule_runs_on_bars_of_seconds_named_as_pine_names_them(rules, cross):
    assert created(rules, cross, bars={"type": "time", "timeframe": "10S"})["rule"]["bars"] == {"type": "time", "timeframe": "10S"}
    # Two minutes in seconds are the timeframe "2".
    two = created(rules, cross, name="Cross on US100, 2 min", bars={"type": "time", "timeframe": "120S"})["rule"]
    assert two["bars"] == {"type": "time", "timeframe": "2"}


def test_a_new_rule_cannot_start_live(rules, cross):
    with pytest.raises(InvalidRule, match="off or in shadow"):
        rules.create(fields(cross), rules.load()["revision"], mode="live")


@pytest.mark.parametrize("changes, refusal", [
    ({"asset": {"provider": "dukascopy", "key": "us100"}}, "provider must be one of capital"),
    ({"asset": {"provider": "capital", "key": "../US100"}}, "Capital.com epic"),
    ({"side": "mid"}, "price side must be one of bid, ask"),
    ({"bars": {"type": "time", "timeframe": "5S"}}, "10 seconds to a day"),
    ({"bars": {"type": "time", "timeframe": "1441"}}, "10 seconds to a day"),
    ({"bars": {"type": "range", "ticks": 0}}, "Range bars are 1 to"),
    ({"bars": {"type": "range", "ticks": True}}, "Range bars are 1 to"),
    ({"bars": {"type": "renko"}}, "bars must be one of time, range"),
    ({"inputs": [{"id": "length", "value": 3}]}, "such as in_0"),
    ({"inputs": [{"id": "in_0", "value": 1}, {"id": "in_0", "value": 2}]}, "set twice"),
    ({"inputs": [{"id": "in_0", "value": float("nan")}]}, "not a number"),
    ({"inputs": [{"id": "in_0", "value": None}]}, "a number, a text or true/false"),
    ({"inputs": [{"id": "in_0", "value": "x" * 1001}]}, "1000 characters at most"),
    ({"inputs": [{"id": "in_0", "var": "1st", "value": 1}]}, "not a Pine name"),
    ({"alerts": [{"title": "Exit", "action": "sell"}]}, "action of 'Exit' must be one of"),
    ({"alerts": [{"title": "Exit", "action": "long"}, {"title": "Exit", "action": "short"}]}, "mapped twice"),
    ({"alerts": [{"title": " ", "action": "long"}]}, "named by its title"),
    ({"alert_calls": "long"}, "alert\\(\\) calls become must be one of ignore, message"),
    ({"indice": "US 100"}, "WATA's name of the market"),
    ({"name": "  "}, "name is missing"),
    ({"script": {"id": "../../etc", "version": 1}}, "by its id"),
    ({"script": {"id": "0123456789ab", "version": 1}}, "No script"),
    ({"history_days": 31}, "history is 0 to 30 days"),
    ({"history_days": -1}, "history is 0 to 30 days"),
    ({"history_days": True}, "history is 0 to 30 days"),
    ({"history_days": 1.5}, "history is 0 to 30 days"),
])
def test_a_rule_that_cannot_run_is_refused(rules, cross, changes, refusal):
    with pytest.raises(InvalidRule, match=refusal):
        created(rules, cross, **changes)
    assert rules.load()["rules"] == []


def test_a_rule_runs_a_version_that_exists_of_an_indicator_or_strategy(rules, scripts, cross):
    library = scripts.create("Lonely", LIBRARY_ONLY)

    with pytest.raises(InvalidRule, match="has no version 2"):
        created(rules, cross, script={"id": cross["id"], "version": 2})
    with pytest.raises(InvalidRule, match="is a library"):
        created(rules, cross, script={"id": library["id"], "version": 1})


def test_a_script_whose_library_is_missing_or_circular_is_refused(rules, scripts, cross, tmp_path):
    orphan = scripts.create("Orphan", '//@version=6\nindicator("O")\nimport me/Missing/1 as m\nplot(close)\n')
    with pytest.raises(InvalidRule, match='No library "Missing"'):
        created(rules, orphan)

    maths = next(meta for meta in scripts.scripts() if meta["name"] == "Maths")
    scripts.save(maths["id"], 1, MATHS.replace('library("Maths")', 'library("Maths")\nimport someone/Bands/1 as b'))
    with pytest.raises(InvalidRule, match="import each other: Bands → Maths → Bands"):
        created(rules, cross)


def test_the_inputs_are_kept_in_the_scripts_order(rules, cross):
    rule = created(rules, cross, inputs=[
        {"id": "in_10", "value": True},
        {"id": "in_2", "title": "  Source  ", "value": "close"},
        {"id": "in_0", "value": 2.5},
    ])["rule"]

    assert [(entry["id"], entry["title"], entry["value"]) for entry in rule["inputs"]] == [
        ("in_0", None, 2.5), ("in_2", "Source", "close"), ("in_10", None, True),
    ]


def test_a_rule_saved_again_keeps_its_libraries_until_asked(rules, scripts, cross):
    rule = created(rules, cross)["rule"]
    bands = next(meta for meta in scripts.scripts() if meta["name"] == "Bands")
    scripts.save(bands["id"], 1, BANDS + "\n// faster\n")

    kept = rules.update(rule["id"], fields(cross, name="Renamed"), rules.load()["revision"])["rule"]
    refreshed = rules.update(rule["id"], fields(cross, name="Renamed"), rules.load()["revision"], refresh_libraries=True)["rule"]

    assert [entry["version"] for entry in kept["libraries"]] == [1, 1]
    assert [entry["version"] for entry in refreshed["libraries"]] == [1, 2]
    assert kept["name"] == "Renamed" and kept["mode"] == "shadow"


def test_a_rule_updated_to_a_new_version_runs_it_with_the_current_libraries(rules, scripts, cross):
    rule = created(rules, cross)["rule"]
    newer = scripts.save(cross["id"], 1, STRATEGY + "\n// v2\n")
    bands = next(meta for meta in scripts.scripts() if meta["name"] == "Bands")
    scripts.save(bands["id"], 1, BANDS + "\n// faster\n")

    updated = rules.update(rule["id"], fields(newer), rules.load()["revision"])["rule"]

    assert updated["script"]["version"] == 2 and updated["script"]["sha256"] == sha(STRATEGY + "\n// v2\n")
    assert [entry["version"] for entry in updated["libraries"]] == [1, 2]


def test_a_change_of_what_a_rule_runs_is_dated_and_a_rename_is_not(rules, scripts, cross):
    rule = created(rules, cross)["rule"]
    renamed = rules.update(rule["id"], fields(cross, name="Renamed"), rules.load()["revision"])["rule"]
    assert "run_changed_at" not in renamed

    tuned = rules.update(rule["id"], fields(cross, name="Renamed", inputs=[
        {"id": "in_0", "var": "fast", "title": "Fast", "value": 20}]), rules.load()["revision"])["rule"]
    assert tuned["run_changed_at"] == tuned["updated_at"]

    newer = scripts.save(cross["id"], 1, STRATEGY + "\n// v2\n")
    repinned = rules.update(rule["id"], fields(newer, name="Renamed", inputs=[
        {"id": "in_0", "var": "fast", "title": "Fast", "value": 20}]), rules.load()["revision"])["rule"]
    assert repinned["script"]["version"] == 2 and repinned["run_changed_at"] == repinned["updated_at"]


def test_what_a_rule_runs_is_what_the_engine_reloads_it_for():
    from src.ta_engine.rules import RUN_FIELDS as ENGINE_RUN_FIELDS

    assert RUN_FIELDS == ENGINE_RUN_FIELDS


def test_a_save_that_changes_nothing_keeps_the_revision(rules, cross):
    rule = created(rules, cross)["rule"]
    revision = rules.load()["revision"]

    result = rules.update(rule["id"], fields(cross), revision)

    assert result["revision"] == revision and result["rule"] == rule


def test_a_rule_keeps_its_own_history_only_when_it_has_one(rules, cross):
    # A rule saved before rules had a history of their own has none: the engine's.
    rule = created(rules, cross)["rule"]
    assert "history_days" not in rule

    own = rules.update(rule["id"], fields(cross, history_days=0), rules.load()["revision"])
    revision = rules.load()["revision"]
    again = rules.update(rule["id"], fields(cross, history_days=0), revision)
    engines = rules.update(rule["id"], fields(cross), rules.load()["revision"])["rule"]

    # No day before is a history of its own too; saved again the same, nothing changes.
    assert own["rule"]["history_days"] == 0
    assert again["rule"] == own["rule"] and again["revision"] == revision
    # Left out, it is the engine's again.
    assert "history_days" not in engines and rules.rule(rule["id"]) == engines


def test_one_rule_is_live_per_indice(rules, cross):
    first = created(rules, cross)["rule"]
    second = created(rules, cross, name="Second")["rule"]
    rules.set_mode(first["id"], "live", rules.load()["revision"])

    with pytest.raises(RuleConflict, match="'Cross on US100' is already live on us100"):
        rules.set_mode(second["id"], "live", rules.load()["revision"])
    other_indice = rules.update(second["id"], fields(cross, name="Second", indice="de40"), rules.load()["revision"])
    rules.set_mode(second["id"], "live", other_indice["revision"])
    # Nor may a live rule move onto an indice another live rule has.
    with pytest.raises(RuleConflict, match="already live on us100"):
        rules.update(second["id"], fields(cross, name="Second"), rules.load()["revision"])


def test_a_rule_whose_alerts_all_are_ignored_cannot_go_live(rules, cross):
    rule = created(rules, cross, alerts=[{"title": "Enter Long", "action": "ignore"}])["rule"]

    with pytest.raises(InvalidRule, match="becomes a signal"):
        rules.set_mode(rule["id"], "live", rules.load()["revision"])
    rules.update(rule["id"], fields(cross, alerts=[], alert_calls="message"), rules.load()["revision"])
    assert rules.set_mode(rule["id"], "live", rules.load()["revision"])["rule"]["mode"] == "live"


def test_a_rule_whose_files_changed_on_disk_cannot_go_live(rules, scripts, cross):
    rule = created(rules, cross)["rule"]
    path = os.path.join(scripts.root, "scripts", cross["id"], "v0001.pine")
    with open(path, "a", encoding="utf-8") as script_file:
        script_file.write("// edited by hand\n")

    with pytest.raises(InvalidRule, match="changed on disk"):
        rules.set_mode(rule["id"], "live", rules.load()["revision"])


def test_a_live_rule_is_switched_off_before_it_is_deleted(rules, cross):
    rule = created(rules, cross)["rule"]
    rules.set_mode(rule["id"], "live", rules.load()["revision"])

    with pytest.raises(RuleConflict, match="is live"):
        rules.delete(rule["id"], rules.load()["revision"])
    rules.set_mode(rule["id"], "off", rules.load()["revision"])
    rules.delete(rule["id"], rules.load()["revision"])
    assert rules.load()["rules"] == []


def test_a_script_or_library_a_rule_runs_is_not_deleted(rules, scripts, cross):
    created(rules, cross)
    bands = next(meta for meta in scripts.scripts() if meta["name"] == "Bands")
    scripts.save(cross["id"], 1, STRATEGY.replace("import someone/Bands/1 as bands\n", "").replace("bands.mid", "ta.sma"))

    def users(meta):
        return [f"the signal rule {name!r}" for name in rules.users_of(meta["id"])]

    for script_id in (cross["id"], bands["id"]):
        with pytest.raises(ScriptConflict, match="used by the signal rule 'Cross on US100'"):
            scripts.delete(script_id, users)


def test_an_unknown_rule_and_an_id_that_is_not_one_of_ours(rules, cross):
    created(rules, cross)

    for rule_id in ("0123456789ab", "../rules", ""):
        with pytest.raises(RuleNotFound):
            rules.rule(rule_id)


def test_a_file_edited_into_something_else_is_said_to_be_so(rules, scripts):
    os.makedirs(scripts.root, exist_ok=True)
    with open(rules.path, "w", encoding="utf-8") as rules_file:
        rules_file.write("{not json")

    with pytest.raises(InvalidRule, match="is not JSON"):
        rules.load()


def test_the_number_of_rules_is_bounded(rules, cross, monkeypatch):
    import src.admin.ta_rules as ta_rules

    monkeypatch.setattr(ta_rules, "MAX_RULES", 1)
    created(rules, cross)

    with pytest.raises(InvalidRule, match="1 rules at most"):
        created(rules, cross, name="Second")


# ── the routes ──


def _client(ctx, tmp_path, scripts):
    ctx.ta_scripts = scripts
    client = make_client(ctx, tmp_path / "no-interface")
    client.__enter__()
    response = client.post("/api/session", json={"password": PASSWORD}, headers=SAME_ORIGIN)
    assert response.status_code == 204
    return client


@pytest.fixture
def feed_config_path(config_path):
    """The configuration of a server whose Capital.com feed records US100."""
    with open(config_path, encoding="utf-8") as config_file:
        config = json.load(config_file)
    config["trade"]["config"]["capital_feed"] = {"enabled": True, "instruments": [{"epic": "US100", "label": "us100"}]}
    with open(config_path, "w", encoding="utf-8") as config_file:
        json.dump(config, config_file, indent=2)
    return config_path


@pytest.fixture
def api(feed_config_path, tmp_path, scripts):
    ctx = make_ctx(feed_config_path, tmp_path)
    # A web server run with us100 set to the engine's signals (trade.config.signal_sources).
    ctx.services.webhook = {"sources": {"us100": "engine"}, "ignored": {}}
    client = _client(ctx, tmp_path, scripts)
    yield client, ctx
    client.__exit__(None, None, None)


def body(script, revision, /, **changes):
    return {**fields(script, **changes), "revision": revision}


def test_rules_are_created_listed_read_changed_and_deleted(api, cross):
    client, _ = api
    revision = client.get("/api/ta/rules").json()["revision"]

    created_rule = client.post("/api/ta/rules", json=body(cross, revision), headers=SAME_ORIGIN)
    assert created_rule.status_code == 201
    rule, revision = created_rule.json()["rule"], created_rule.json()["revision"]
    assert (rule["script_name"], rule["latest"]) == ("Cross", {"script": 1, "libraries": {"Maths": 1, "Bands": 1}})

    listed = client.get("/api/ta/rules").json()
    assert [entry["id"] for entry in listed["rules"]] == [rule["id"]] and listed["revision"] == revision
    # What a rule can be made of, from the configuration.
    assert listed["indices"] == ["us100"] and listed["instruments"] == [{"epic": "US100", "label": "us100"}]
    assert client.get(f"/api/ta/rules/{rule['id']}").json()["rule"]["name"] == "Cross on US100"

    changed = client.put(
        f"/api/ta/rules/{rule['id']}", json=body(cross, revision, side="ask"), headers=SAME_ORIGIN,
    )
    assert changed.status_code == 200 and changed.json()["rule"]["side"] == "ask"
    revision = changed.json()["revision"]

    assert client.delete(f"/api/ta/rules/{rule['id']}?revision={revision}", headers=SAME_ORIGIN).status_code == 204
    assert client.get("/api/ta/rules").json()["rules"] == []
    assert client.get(f"/api/ta/rules/{rule['id']}").status_code == 404


def test_a_rules_own_history_goes_through_the_route_beside_the_engines(api, cross):
    client, _ = api
    listed = client.get("/api/ta/rules").json()

    made = client.post("/api/ta/rules", json=body(cross, listed["revision"], history_days=0), headers=SAME_ORIGIN)
    too_long = client.post("/api/ta/rules", json=body(cross, made.json()["revision"], name="Long", history_days=31),
                           headers=SAME_ORIGIN)

    # The test configuration has no history_days of its own: the engine's default.
    assert listed["engine_history_days"] == 1
    assert made.status_code == 201 and made.json()["rule"]["history_days"] == 0
    assert too_long.status_code == 422


def test_a_rule_changed_on_an_old_revision_is_refused_by_the_route(api, cross):
    client, _ = api
    revision = client.get("/api/ta/rules").json()["revision"]
    client.post("/api/ta/rules", json=body(cross, revision), headers=SAME_ORIGIN)

    response = client.post("/api/ta/rules", json=body(cross, revision, name="Again"), headers=SAME_ORIGIN)

    assert response.status_code == 409 and "changed meanwhile" in response.json()["detail"]


def test_a_rule_that_cannot_run_is_refused_by_the_route(api, cross):
    client, _ = api
    revision = client.get("/api/ta/rules").json()["revision"]

    bad_bars = client.post("/api/ta/rules", json=body(cross, revision, bars={"type": "range"}), headers=SAME_ORIGIN)
    bad_type = client.post("/api/ta/rules", json=body(cross, revision, inputs=[{"id": "in_0", "value": [1]}]), headers=SAME_ORIGIN)
    born_live = client.post("/api/ta/rules", json={**body(cross, revision), "mode": "live"}, headers=SAME_ORIGIN)

    assert bad_bars.status_code == 422 and "Range bars" in bad_bars.json()["detail"]
    assert bad_type.status_code == 422
    assert born_live.status_code == 422


@pytest.fixture
def ready(monkeypatch):
    """A rule taken for ready to go live: what is tested is the switch, not the readiness (tests/test_ta_readiness.py)."""
    import src.admin.routers.ta as ta_router

    monkeypatch.setattr(ta_router, "unmet_readiness", lambda *args, **kwargs: [])


def _new_rule(client, script, /, **changes):
    revision = client.get("/api/ta/rules").json()["revision"]
    answer = client.post("/api/ta/rules", json=body(script, revision, **changes), headers=SAME_ORIGIN).json()
    return answer["rule"], answer["revision"]


def test_going_live_is_an_operator_action_said_in_the_audit_trail(api, cross, ready):
    client, ctx = api
    rule, revision = _new_rule(client, cross)

    response = client.post(f"/api/ta/rules/{rule['id']}/mode", json={"mode": "live", "revision": revision}, headers=SAME_ORIGIN)

    assert response.status_code == 200 and response.json()["rule"]["mode"] == "live"
    entry = ctx.audit_log.read(1)[0]
    assert entry["action"] == "signal rule live"
    assert "rule 'Cross on US100'" in entry["detail"] and "indice=us100" in entry["detail"]
    assert "asset=capital/US100 bid bars=1 script='Cross' v1" in entry["detail"]


def test_leaving_live_is_said_too_and_allowed_when_actions_are_not(api, cross, ready):
    client, ctx = api
    rule, revision = _new_rule(client, cross)
    revision = client.post(
        f"/api/ta/rules/{rule['id']}/mode", json={"mode": "live", "revision": revision}, headers=SAME_ORIGIN,
    ).json()["revision"]
    ctx.settings.actions_enabled = False

    response = client.post(f"/api/ta/rules/{rule['id']}/mode", json={"mode": "shadow", "revision": revision}, headers=SAME_ORIGIN)

    assert response.status_code == 200 and response.json()["rule"]["mode"] == "shadow"
    assert ctx.audit_log.read(1)[0]["action"] == "signal rule no longer live (shadow)"


def test_a_rule_not_ready_does_not_go_live_unless_the_operator_goes_live_anyway(api, cross):
    client, ctx = api
    rule, revision = _new_rule(client, cross)

    refused = client.post(f"/api/ta/rules/{rule['id']}/mode", json={"mode": "live", "revision": revision}, headers=SAME_ORIGIN)
    anyway = client.post(
        f"/api/ta/rules/{rule['id']}/mode", json={"mode": "live", "revision": revision, "force": True}, headers=SAME_ORIGIN,
    )

    assert refused.status_code == 409
    assert refused.json()["detail"] == (
        "Not ready to go live: 0 weekday(s) in shadow of 5; no signal journaled yet. "
        "Go live anyway to switch it all the same: that is recorded"
    )
    assert anyway.status_code == 200 and anyway.json()["rule"]["mode"] == "live"
    assert ctx.audit_log.read(1)[0]["action"] == (
        "signal rule live WITHOUT its readiness (0 weekday(s) in shadow of 5; no signal journaled yet)"
    )


def test_off_and_shadow_are_not_operator_actions(api, cross):
    client, ctx = api
    rule, revision = _new_rule(client, cross)
    ctx.settings.actions_enabled = False

    response = client.post(f"/api/ta/rules/{rule['id']}/mode", json={"mode": "off", "revision": revision}, headers=SAME_ORIGIN)

    assert response.status_code == 200 and response.json()["rule"]["mode"] == "off"
    assert not any("signal rule" in entry["action"] for entry in ctx.audit_log.read(50))


def test_no_rule_goes_live_while_actions_are_disabled(api, cross):
    client, ctx = api
    rule, revision = _new_rule(client, cross)
    ctx.settings.actions_enabled = False

    response = client.post(f"/api/ta/rules/{rule['id']}/mode", json={"mode": "live", "revision": revision}, headers=SAME_ORIGIN)

    assert response.status_code == 403
    assert client.get(f"/api/ta/rules/{rule['id']}").json()["rule"]["mode"] == "shadow"


@pytest.mark.parametrize("changes, refusal", [
    ({"indice": "de40"}, "de40 is not one of the allowed indices"),
    ({"asset": {"provider": "capital", "key": "DE40"}}, "does not record DE40"),
])
def test_a_rule_goes_live_only_on_a_traded_indice_and_a_recorded_asset(api, cross, changes, refusal):
    client, _ = api
    rule, revision = _new_rule(client, cross, **changes)

    response = client.post(f"/api/ta/rules/{rule['id']}/mode", json={"mode": "live", "revision": revision}, headers=SAME_ORIGIN)

    assert response.status_code == 409 and refusal in response.json()["detail"]


@pytest.mark.parametrize("webhook, refusal", [
    ({"sources": {}, "ignored": {}}, "us100 takes TradingView's signals: set it to the engine"),
    ({"sources": {"us100": "tradingview"}, "ignored": {}}, "us100 takes TradingView's signals"),
    (None, "us100 takes TradingView's signals"),
    ("down", "The web server does not say which signals us100 takes"),
])
def test_a_rule_goes_live_only_on_an_indice_the_web_server_runs_with_set_to_the_engine(api, cross, ready, webhook, refusal):
    client, ctx = api
    rule, revision = _new_rule(client, cross)
    if webhook == "down":
        ctx.services.web_server_down = True
    else:
        ctx.services.webhook = webhook

    response = client.post(f"/api/ta/rules/{rule['id']}/mode", json={"mode": "live", "revision": revision}, headers=SAME_ORIGIN)

    assert response.status_code == 409 and refusal in response.json()["detail"]
    # "Go live anyway" skips readiness, never this.
    forced = client.post(
        f"/api/ta/rules/{rule['id']}/mode", json={"mode": "live", "revision": revision, "force": True}, headers=SAME_ORIGIN,
    )
    assert forced.status_code == 409
    assert client.get(f"/api/ta/rules/{rule['id']}").json()["rule"]["mode"] == "shadow"


def test_no_rule_goes_live_while_the_capital_com_feed_is_off(ctx, tmp_path, scripts, cross):
    client = _client(ctx, tmp_path, scripts)
    try:
        rule, revision = _new_rule(client, cross)
        response = client.post(
            f"/api/ta/rules/{rule['id']}/mode", json={"mode": "live", "revision": revision}, headers=SAME_ORIGIN,
        )
    finally:
        client.__exit__(None, None, None)

    assert response.status_code == 409 and "feed is off" in response.json()["detail"]


def test_a_live_rule_changes_only_as_an_operator_action(api, cross, ready):
    client, ctx = api
    rule, revision = _new_rule(client, cross)
    revision = client.post(
        f"/api/ta/rules/{rule['id']}/mode", json={"mode": "live", "revision": revision}, headers=SAME_ORIGIN,
    ).json()["revision"]

    ctx.settings.actions_enabled = False
    refused = client.put(f"/api/ta/rules/{rule['id']}", json=body(cross, revision, side="ask"), headers=SAME_ORIGIN)
    ctx.settings.actions_enabled = True
    allowed = client.put(f"/api/ta/rules/{rule['id']}", json=body(cross, revision, side="ask"), headers=SAME_ORIGIN)

    assert refused.status_code == 403
    assert allowed.status_code == 200 and allowed.json()["rule"]["mode"] == "live"
    entry = ctx.audit_log.read(1)[0]
    assert entry["action"] == "live signal rule changed" and entry["detail"].endswith("changed=side")


def test_a_script_a_rule_runs_is_not_deleted_by_the_route(api, cross):
    client, _ = api
    _new_rule(client, cross)

    response = client.delete(f"/api/ta/scripts/{cross['id']}", headers=SAME_ORIGIN)

    assert response.status_code == 409 and "the signal rule 'Cross on US100'" in response.json()["detail"]


def test_the_signals_are_read_with_their_filters(api):
    client, ctx = api
    ctx.queries.ta_signal_rows = [{"id": 1, "rule_id": "0123456789ab", "outcome": "shadow", "trader": None}]

    response = client.get("/api/ta/signals?from=2026-10-13&to=2026-10-14&rule=0123456789ab&limit=20")

    assert response.json() == {"items": ctx.queries.ta_signal_rows}
    assert ctx.queries.calls[-1][0] == "ta_signals" and ctx.queries.calls[-1][3:] == ("0123456789ab", 20)
    assert client.get("/api/ta/signals?rule=../x").status_code == 422


def test_the_engine_says_what_it_does_or_nothing_when_it_never_ran(api):
    client, ctx = api
    assert client.get("/api/ta/engine").json() == {
        "enabled": False, "status": "disabled", "detail": "Switched off in the configuration", "engine": None, "rules": {},
    }

    now = datetime.now(timezone.utc)
    ctx.queries.ta_engine = {"updated_at": now, "state": "running", "detail": "1 rule(s) on", "figures": {"rules": 1}}
    ctx.queries.ta_rules = {"0123456789ab": {"state": "running", "mode": "shadow"}}
    ctx.config_manager.config_data["trade"]["config"]["ta_engine"] = {"enabled": True}

    body = client.get("/api/ta/engine").json()
    assert (body["enabled"], body["status"], body["detail"]) == (True, "ok", "1 rule(s) on")
    assert body["rules"] == {"0123456789ab": {"state": "running", "mode": "shadow"}}


@pytest.mark.parametrize("enabled, state, age, expected", [
    (False, None, 0, ("disabled", "Switched off in the configuration")),
    (True, None, 0, ("down", 'Never started: add "ta" to COMPOSE_PROFILES in .env and start it')),
    (True, "running", 600, ("down", "No sign of it for 10 min")),
    (True, "disabled", 5, ("warning", "Switched on in the configuration: it starts at its next start")),
    (False, "running", 5, ("warning", "Switched off in the configuration: it runs until it is restarted")),
    (True, "idle", 5, ("ok", "the detail")),
    (True, "stale_feed", 5, ("warning", "the detail")),
    (True, "runner_down", 5, ("down", "the detail")),
    (True, "no_runner", 5, ("down", "the detail")),
])
def test_the_engine_is_said_as_the_capital_com_feed_is(enabled, state, age, expected):
    now = datetime.now(timezone.utc)
    row = None if state is None else {"updated_at": now - timedelta(seconds=age), "state": state, "detail": "the detail"}

    assert ta_engine_status(enabled, row, now) == expected


def test_the_engine_appears_on_the_system_page_once_switched_on_and_its_log_can_be_read(api):
    client, ctx = api
    assert "ta_engine" not in {service["id"] for service in client.get("/api/system/health").json()["services"]}

    ctx.config_manager.config_data["trade"]["config"]["ta_engine"] = {"enabled": True}
    ctx.queries.ta_engine = {"updated_at": datetime.now(timezone.utc), "state": "runner_down", "detail": "No Node"}
    services = {service["id"]: service for service in client.get("/api/system/health").json()["services"]}

    assert (services["ta_engine"]["name"], services["ta_engine"]["status"], services["ta_engine"]["detail"]) == (
        "TA engine", "down", "No Node",
    )
    logs = {service["id"]: service["name"] for service in client.get("/api/logs").json()["services"]}
    assert logs["wata-ta-engine"] == "TA engine"


def test_the_engine_and_signals_say_when_the_database_is_down(api):
    client, ctx = api
    ctx.queries.failing = True

    assert client.get("/api/ta/engine").status_code == 503
    assert client.get("/api/ta/signals").status_code == 503


# ── WATA's signals beside TradingView's ──

DAY0 = datetime(2026, 10, 15, 13, 30, tzinfo=timezone.utc)


def ours(minutes, action, **more):
    return {"bar_time": DAY0 + timedelta(minutes=minutes), "action": action, "outcome": "shadow", **more}


def theirs(minutes, action, seconds=0):
    return {"signal_timestamp": DAY0 + timedelta(minutes=minutes, seconds=seconds), "action": action, "outcome": "executed"}


def test_signals_are_paired_by_action_with_the_nearest_within_the_tolerance():
    result = compare(
        [ours(0, "long"), ours(10, "close-position"), ours(30, "short"), ours(31, None)],
        [theirs(1, "long"), theirs(0, "short"), theirs(11, "close-long"), theirs(50, "long")],
        tolerance_seconds=120,
    )

    pairs = [(row["action"], row["wata"] is not None, row["tradingview"] is not None, row["delta_seconds"]) for row in result["rows"]]
    assert pairs == [
        ("long", True, True, 60),
        # Its short at :00, at the time of WATA's long, is not a long: it stays alone.
        ("short", False, True, None),
        # TradingView's close of the longs is WATA's close-position.
        ("close-position", True, True, 60),
        ("short", True, False, None),
        ("long", False, True, None),
    ]
    assert (result["paired"], result["only_wata"], result["only_tradingview"]) == (2, 1, 2)


def test_each_signal_is_paired_once():
    result = compare([ours(0, "long"), ours(1, "long")], [theirs(0, "long", seconds=40)], tolerance_seconds=120)

    assert [(row["wata"]["bar_time"].minute, row["tradingview"] is not None) for row in result["rows"]] == [(30, True), (31, False)]


def test_the_tolerance_is_two_bars_of_minutes_or_five_minutes_of_range_bars():
    assert default_tolerance({"type": "time", "timeframe": "5"}) == 600
    assert default_tolerance({"type": "time", "timeframe": "45S"}) == 90
    assert default_tolerance({"type": "range", "ticks": 60}) == 300


@pytest.mark.parametrize("timeframe, seconds, name", [
    ("10S", 10, "10S"), ("45S", 45, "45S"), ("45s", 45, "45S"), ("90S", 90, "90S"), ("120S", 120, "2"),
    ("1", 60, "1"), ("5", 300, "5"), ("240", 14_400, "240"), ("1440", 86_400, "1440"), ("86400S", 86_400, "1440"),
])
def test_a_rule_or_a_backtest_runs_on_bars_of_any_length_from_ten_seconds_to_a_day(timeframe, seconds, name):
    assert timeframe_seconds(timeframe) == seconds and timeframe_name(seconds) == name
    assert checked_bars({"type": "time", "timeframe": timeframe}) == {"type": "time", "timeframe": name}


@pytest.mark.parametrize("timeframe", ["5S", "0", "1441", "86401S", "D", "1W", "4h", "-10S", "10S5", " 10S", 45, None])
def test_bars_of_a_length_wata_does_not_make_are_refused(timeframe):
    assert timeframe_seconds(timeframe) is None
    with pytest.raises(InvalidRule, match="10 seconds to a day"):
        checked_bars({"type": "time", "timeframe": timeframe})


def test_the_comparison_of_a_day_reads_both_sides(api, cross):
    client, ctx = api
    rule, _ = _new_rule(client, cross)
    ctx.queries.ta_signal_rows = [ours(0, "long", id=1)]
    ctx.queries.ta_other_rows = [theirs(1, "long")]

    body = client.get(f"/api/ta/rules/{rule['id']}/comparison?day=2026-10-15").json()

    assert (body["day"], body["indice"], body["tolerance_seconds"], body["paired"]) == ("2026-10-15", "us100", 120, 1)
    assert ("ta_other_signals", "us100", date(2026, 10, 15)) in ctx.queries.calls
    assert client.get(f"/api/ta/rules/{rule['id']}/comparison?day=2026-10-15&tolerance=30").json()["paired"] == 0
    assert client.get(f"/api/ta/rules/{rule['id']}/comparison").status_code == 422


def test_a_rule_whose_script_is_luxalgos_goes_live_only_once_its_licence_is_checked(api, cross, ready, tmp_path):
    from src.admin.script_origins import ScriptOrigins, luxalgo_origin

    client, ctx = api
    ctx.script_origins = ScriptOrigins(str(tmp_path / "origins"))
    found = {"slug": "tri-star", "name": "Tri-star", "licence": "CC BY-NC-SA 4.0", "owner": "LuxAlgo"}
    ctx.script_origins.record(cross["id"], luxalgo_origin(found, "operator", 1_800_000_000))
    rule, revision = _new_rule(client, cross)
    path = f"/api/ta/rules/{rule['id']}/mode"

    refused = client.post(path, json={"mode": "live", "revision": revision}, headers=SAME_ORIGIN)
    anyway = client.post(path, json={"mode": "live", "revision": revision, "force": True}, headers=SAME_ORIGIN)
    checked = client.post(path, json={"mode": "live", "revision": revision, "licence_checked": True}, headers=SAME_ORIGIN)

    assert refused.status_code == 409 and "under CC BY-NC-SA 4.0" in refused.json()["detail"]
    assert anyway.status_code == 409
    assert checked.status_code == 200 and checked.json()["rule"]["mode"] == "live"
    assert "the licence of its script (CC BY-NC-SA 4.0) checked by the operator" in ctx.audit_log.read(1)[0]["action"]


def test_a_licence_that_allows_trading_asks_for_no_check(api, cross, ready, tmp_path):
    from src.admin.script_origins import ScriptOrigins, luxalgo_origin

    client, ctx = api
    ctx.script_origins = ScriptOrigins(str(tmp_path / "origins"))
    found = {"slug": "old-one", "name": "Old one", "licence": "Mozilla Public License 2.0", "owner": "LuxAlgo"}
    ctx.script_origins.record(cross["id"], luxalgo_origin(found, "operator", 1_800_000_000))
    rule, revision = _new_rule(client, cross)

    response = client.post(f"/api/ta/rules/{rule['id']}/mode", json={"mode": "live", "revision": revision},
                           headers=SAME_ORIGIN)

    assert response.status_code == 200
