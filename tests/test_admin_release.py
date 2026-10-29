"""
The release each container runs and the operator's request to apply the
newest (src/admin/release.py, src/admin/routers/release.py): what the admin
reads of watadeploy's status, what it writes for it, what it says once.
"""

import json
import os
from datetime import datetime, timezone

import pytest

from src.admin.release import Release, ReleaseRefused, outcome_said
from tests.test_admin_api import (  # noqa: F401 (fixtures)
    PASSWORD,
    SAME_ORIGIN,
    config_path,
    make_client,
    make_ctx,
)

NOW = datetime(2026, 10, 30, 23, 0, tzinfo=timezone.utc).timestamp()  # Friday, 19:00 in New York


def status(**changes) -> dict:
    found = {
        "newest": "0.61.0",
        "installed": ["0.61.0", "0.59.1"],
        "containers": [
            {"container": "admin1", "role": "admin", "kind": "unchanged", "when": "skip", "version": "0.61.0"},
            {"container": "trader1", "role": "trader", "kind": "change", "when": "pending", "version": "0.59.1",
             "reasons": {"code": ["src/trade/rules.py"]}, "held": "the market is open by WATA's hours"},
            {"container": "price_recorder1", "role": "price_recorder", "kind": "optional", "when": "skip",
             "version": "0.59.1", "reasons": {"platform": "Python 3.14.11"}},
            {"container": "ta_engine1", "role": "ta_engine", "kind": "unchanged", "when": "skip", "version": "0.59.1"},
        ],
        "applying": None,
        "last": None,
    }
    found.update(changes)
    return found


@pytest.fixture
def release(tmp_path):
    def made(**changes):
        path = tmp_path / "release" / "status.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(status(**changes)))
        return Release(str(path), str(tmp_path / "requests"), str(tmp_path / "announced.json"), clock=lambda: NOW)

    return made


def test_what_waits_and_in_short(release):
    found = release()
    assert sorted(found.waiting(found.status())) == ["price_recorder1", "trader1"]
    assert found.summary() == {"newest": "0.61.0", "applying": False, "waiting": ["price_recorder1", "trader1"],
                               "containers": {"admin1": "0.61.0", "trader1": "0.59.1", "price_recorder1": "0.59.1",
                                              "ta_engine1": "0.59.1"}}
    assert Release("/nowhere/status.json").summary() is None


def test_a_request_is_written_for_the_newest_release_and_containers_that_wait(release, tmp_path):
    found = release()
    asked = found.ask(["trader1", "trader1"], False, "the System page, 10.8.0.2")
    assert asked["containers"] == ["trader1"] and asked["version"] == "0.61.0" and asked["force"] is False
    written = json.loads((tmp_path / "requests" / f"{asked['id']}.json").read_text())
    assert written == asked
    assert set(written) == {"id", "version", "containers", "force", "asked_by", "asked_at"}
    assert len(asked["id"]) == 32


@pytest.mark.parametrize("containers, said", [(["admin1"], "Not waiting"), ([], "No container"),
                                              (["nobody1"], "Not waiting")])
def test_a_request_out_of_bounds_is_refused_before_it_is_written(release, tmp_path, containers, said):
    with pytest.raises(ReleaseRefused, match=said):
        release().ask(containers, False, "x")
    assert not (tmp_path / "requests").exists()


def test_no_request_while_an_apply_runs_or_before_watadeploy_said_anything(release, tmp_path):
    with pytest.raises(ReleaseRefused, match="under way"):
        release(applying={"id": "a", "done": False}).ask(["trader1"], False, "x")
    with pytest.raises(ReleaseRefused, match="watadeploy"):
        Release(str(tmp_path / "none.json"), str(tmp_path / "requests")).ask(["trader1"], False, "x")


def test_an_applys_outcome_is_said_once(release):
    last = {"id": "abc", "kind": "apply", "by": "playbook", "version": "0.61.0", "done": True,
            "finished_at": "2026-10-30T22:55:00+00:00",
            "steps": [{"container": "admin1", "state": "ready", "detail": "answering (200)"},
                      {"container": "trader1", "state": "rolled_back", "detail": "did not stay up; back on wata-base:0.59.1"}]}
    found = release(last=last, containers=[])
    messages = found.news(found.status(), {}, NOW)
    assert messages == [outcome_said(last)]
    assert messages[0].startswith("⚠️ WATA: 0.61.0 applied only in part, by playbook")
    assert "trader1: put back (did not stay up" in messages[0] and "admin1: at work" in messages[0]
    assert found.news(found.status(), {}, NOW) == []


def test_an_old_apply_is_not_news_to_an_admin_that_never_said_one(release):
    last = {"id": "old", "kind": "apply", "version": "0.59.1", "done": True, "finished_at": "2026-10-01T00:00:00+00:00",
            "steps": []}
    found = release(last=last, containers=[])
    assert found.news(found.status(), {}, NOW) == []


def test_an_image_that_would_refuse_the_configuration_is_said_once(release):
    config = {"at": "x", "images": [{"image": "wata-base:0.59.1", "containers": ["trader1"], "accepted": False,
                                    "detail": "ValueError: Missing required configuration sections: x"}]}
    found = release(config=config, containers=[])
    messages = found.news(found.status(), {}, NOW)
    assert len(messages) == 1 and "trader1 on wata-base:0.59.1 would not start on config.json" in messages[0]
    assert found.news(found.status(), {}, NOW) == []


def test_what_waits_is_said_once_a_day_after_the_close(release):
    config = {"trade": {"rules": [{"rule_type": "market_hours", "rule_config": {
        "trading_start_hour": 2, "trading_end_hour": 16, "timezone": "America/New_York"}}]}}
    found = release()
    open_market = datetime(2026, 10, 30, 14, 0, tzinfo=timezone.utc).timestamp()
    assert found.news(found.status(), config, open_market) == []
    messages = found.news(found.status(), config, NOW)
    assert messages == ["🕗 WATA 0.61.0 waits for price_recorder1, trader1: apply it from the System page, or with "
                        "watadeploy apply on the server"]
    assert found.news(found.status(), config, NOW + 600) == []


# ── the routes ──


@pytest.fixture
def client_with(config_path, tmp_path):
    def made(ctx_status=None, **overrides):
        ctx = make_ctx(config_path, tmp_path, **overrides)
        path = tmp_path / "release" / "status.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(ctx_status or status()))
        ctx.release = Release(str(path), str(tmp_path / "requests"), str(tmp_path / "announced.json"))
        client = make_client(ctx, tmp_path / "no-interface")
        client.__enter__()
        assert client.post("/api/session", json={"password": PASSWORD}, headers=SAME_ORIGIN).status_code == 204
        return ctx, client

    return made


def test_the_page_reads_the_status_and_what_holds_each_waiting_container_back(client_with, monkeypatch):
    from src.admin.routers import release as routes
    from src.release.guards import Situation

    async def market_open(ctx):
        return Situation(market_open=True)

    monkeypatch.setattr(routes, "situation_of", market_open)
    _, client = client_with()
    answer = client.get("/api/system/release").json()
    assert answer["available"] and answer["status"]["newest"] == "0.61.0"
    assert answer["held"] == {"trader1": "the market is open by WATA's hours",
                              "price_recorder1": "the market is open by WATA's hours, and the bars it would miss are "
                                                 "never fetched again"}


def test_apply_is_held_back_unless_forced_then_written_and_audited(client_with, monkeypatch, tmp_path):
    from src.admin.routers import release as routes
    from src.release.guards import Situation

    async def market_open(ctx):
        return Situation(market_open=True)

    monkeypatch.setattr(routes, "situation_of", market_open)
    ctx, client = client_with()
    held = client.post("/api/system/release/apply", json={"containers": ["trader1"]}, headers=SAME_ORIGIN)
    assert held.status_code == 409 and "Apply anyway" in held.json()["detail"]
    assert not (tmp_path / "requests").exists()
    forced = client.post("/api/system/release/apply", json={"containers": ["trader1"], "force": True},
                         headers=SAME_ORIGIN)
    assert forced.status_code == 202
    request = forced.json()["request"]
    assert json.loads((tmp_path / "requests" / f"{request['id']}.json").read_text())["force"] is True
    audit = (tmp_path / "log" / "wata-admin" / "audit.jsonl").read_text()
    assert "apply 0.61.0 to trader1 ANYWAY (trader1: the market is open" in audit


def test_apply_needs_the_actions_and_refuses_what_does_not_wait(client_with):
    _, client = client_with(actions_enabled=False)
    assert client.post("/api/system/release/apply", json={"containers": ["trader1"]},
                       headers=SAME_ORIGIN).status_code == 403
    _, client = client_with()
    refused = client.post("/api/system/release/apply", json={"containers": ["admin1"]}, headers=SAME_ORIGIN)
    assert refused.status_code == 409 and "Not waiting" in refused.json()["detail"]


def test_no_restart_plan_starts_while_an_apply_runs(client_with):
    from tests.test_service_control import FakeControl

    ctx, client = client_with(ctx_status=status(applying={"id": "a", "done": False}))
    ctx.service_control = FakeControl()
    answer = client.post("/api/system/services/telegram/restart", json={}, headers=SAME_ORIGIN)
    assert answer.status_code == 409 and "deploy" in answer.json()["detail"]
