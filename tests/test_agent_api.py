"""
The AI agent's routes (src/admin/routers/agent.py) over the real service with a
scripted Claude API and fake tools (tests/test_agent_loop.py): off, it says so
and refuses the rest; on, a conversation asked from a page is worked on by the
admin's own task and read back request by request; its stream is followed on a
real socket, and a page that leaves it is no longer subscribed.
"""

import json
import time
from unittest.mock import patch

import pytest

from src.admin.agent.proposals import Proposals
from src.admin.agent.service import AgentService
from src.admin.agent.settings import AgentSettings
from src.admin.agent.store import ConversationStore, stamp
from src.admin.ta_scripts import ScriptStore
from src.backtest.settings import from_config
from tests.test_admin_api import (  # noqa: F401 (fixtures)
    PASSWORD,
    SAME_ORIGIN,
    RunningAdmin,
    _eventually,
    config_path,
    make_client,
    make_ctx,
)
from tests.test_agent_loop import FakeBacktests, FakeProvider, FakeScripts, FakeTools, answer, text

RUN = "20261027-101010-abcdef"


def with_agent(ctx, tmp_path, provider, key="sk-ant-api03-test-key-0123456789"):
    store = ConversationStore(str(tmp_path / "agent"))

    async def the_key():
        return key

    ctx.agent = AgentService(
        settings=AgentSettings(enabled=True), store=store, provider=provider, tools=FakeTools(),
        proposals=None, backtests=FakeBacktests(), scripts=FakeScripts(), key=the_key,
        publish=ctx.events.publish,
    )
    return ctx


def signed_in(ctx, tmp_path):
    client = make_client(ctx, tmp_path / "no-interface")
    client.__enter__()
    assert client.post("/api/session", json={"password": PASSWORD}, headers=SAME_ORIGIN).status_code == 204
    return client


def until_settled(client, conversation_id, seconds=5.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        said = client.get(f"/api/agent/conversations/{conversation_id}").json()
        if said["status"] not in ("queued", "running", "waiting"):
            return said
        time.sleep(0.02)
    raise AssertionError("the request did not end")


def test_off_the_agent_says_so_and_refuses_the_rest(config_path, tmp_path):
    ctx = make_ctx(config_path, tmp_path)
    client = signed_in(ctx, tmp_path)
    try:
        state = client.get("/api/agent").json()
        asked = client.post("/api/agent/conversations", json={"prompt": "Hello"}, headers=SAME_ORIGIN)
    finally:
        client.__exit__(None, None, None)

    assert state == {"enabled": False, "model": "claude-opus-5-5"}
    assert asked.status_code == 403 and "Configuration › AI agent" in asked.json()["detail"]


def test_a_conversation_asked_from_a_page_is_worked_on_and_read_back(config_path, tmp_path):
    ctx = with_agent(make_ctx(config_path, tmp_path), tmp_path,
                     FakeProvider(answer(text("It holds on 21 days.")), answer(text("Run it again with 14."))))
    client = signed_in(ctx, tmp_path)
    try:
        made = client.post("/api/agent/conversations", headers=SAME_ORIGIN,
                           json={"prompt": "Analyse this run", "subject": {"kind": "run", "id": RUN}})
        first = until_settled(client, made.json()["id"])
        asked = client.post(f"/api/agent/conversations/{first['id']}/requests", json={"prompt": "What next?"},
                            headers=SAME_ORIGIN)
        second = until_settled(client, first["id"])
        liked = client.post(f"/api/agent/conversations/{first['id']}/feedback", json={"request": 1, "value": "like"},
                            headers=SAME_ORIGIN)
        renamed = client.patch(f"/api/agent/conversations/{first['id']}", json={"title": "Cross, analysed"},
                               headers=SAME_ORIGIN)
        about_the_run = client.get("/api/agent/conversations", params={"subject": f"run:{RUN}"}).json()["items"]
        state = client.get("/api/agent").json()
        unknown = client.get("/api/agent/conversations/20261028-000000-ffffff")
        malformed = client.get("/api/agent/conversations/..%2F..%2Fetc")
    finally:
        client.__exit__(None, None, None)

    assert made.status_code == 201 and made.json()["subject"]["label"] == "Cross R60"
    assert first["requests"][0]["answer"]["text"] == "It holds on 21 days."
    assert first["requests"][0]["shared"] == {"kind": "run", "id": RUN, "label": "Cross R60"}
    assert asked.status_code == 200 and second["requests"][1]["answer"]["text"] == "Run it again with 14."
    assert liked.status_code == 204 and renamed.json()["title"] == "Cross, analysed"
    assert [item["id"] for item in about_the_run] == [first["id"]]
    assert state["enabled"] and state["key"] is True and state["month"]["spent_usd"] > 0
    assert unknown.status_code == 404 and malformed.status_code == 404


def test_the_pause_holds_new_requests_and_a_conversation_is_removed(config_path, tmp_path):
    ctx = with_agent(make_ctx(config_path, tmp_path), tmp_path, FakeProvider(answer(text("Hello."))))
    client = signed_in(ctx, tmp_path)
    try:
        paused = client.post("/api/agent/pause", headers=SAME_ORIGIN).json()
        refused = client.post("/api/agent/conversations", json={"prompt": "Hello"}, headers=SAME_ORIGIN)
        client.post("/api/agent/resume", headers=SAME_ORIGIN)
        made = client.post("/api/agent/conversations", json={"prompt": "Hello"}, headers=SAME_ORIGIN).json()
        until_settled(client, made["id"])
        removed = client.delete(f"/api/agent/conversations/{made['id']}", headers=SAME_ORIGIN)
        gone = client.get(f"/api/agent/conversations/{made['id']}")
    finally:
        client.__exit__(None, None, None)

    assert paused["pause"]["since"] and refused.status_code == 409 and "paused" in refused.json()["detail"]
    assert removed.status_code == 204 and gone.status_code == 404


def test_without_a_key_nothing_is_asked(config_path, tmp_path):
    ctx = with_agent(make_ctx(config_path, tmp_path), tmp_path, FakeProvider(), key=None)
    client = signed_in(ctx, tmp_path)
    try:
        refused = client.post("/api/agent/conversations", json={"prompt": "Hello"}, headers=SAME_ORIGIN)
        state = client.get("/api/agent").json()
    finally:
        client.__exit__(None, None, None)

    assert refused.status_code == 409 and "No Claude API key" in refused.json()["detail"] and state["key"] is False


@pytest.fixture
def plain_http_ctx(config_path, tmp_path):
    # Over plain HTTP on a local socket, a Secure cookie would not come back.
    return make_ctx(config_path, tmp_path, cookie_secure=False)


def test_a_page_follows_its_conversation_as_it_is_written_and_is_let_go_when_it_leaves(plain_http_ctx, tmp_path):
    ctx = with_agent(plain_http_ctx, tmp_path, FakeProvider(answer(text("Done.")), answer(text("Streamed answer."))))
    agent = ctx.agent
    with patch("src.admin.routers.agent.STREAM_POLL_SECONDS", 0.05), \
            RunningAdmin(ctx, tmp_path / "no-interface") as admin, admin.signed_in_client() as client:
        made = client.post("/api/agent/conversations", json={"prompt": "Hello"},
                           headers={"Origin": admin.base_url}).json()
        assert _eventually(lambda: client.get(f"/api/agent/conversations/{made['id']}").json()["status"] == "idle")

        stream = client.stream("GET", f"/api/agent/conversations/{made['id']}/stream")
        lines = stream.__enter__().iter_lines()
        while next(lines) != "event: connected":
            pass
        assert agent._subscribers.get(made["id"])
        client.post(f"/api/agent/conversations/{made['id']}/requests", json={"prompt": "Again"},
                    headers={"Origin": admin.base_url})
        seen = []
        for line in lines:
            seen.append(line)
            if line.startswith("data:") and "Streamed answer." in line:
                break
        assert "event: changed" in seen and "event: delta" in seen

        # The tab is closed: the server finds out by itself.
        stream.__exit__(None, None, None)
        assert _eventually(lambda: not agent._subscribers.get(made["id"])), "the closed stream is still subscribed"


# ── a change of settings, saved as the Configuration page saves ──


class LiveBacktests:
    """The backtests' service as a settings proposal reads it: config.json's settings, and one finished run."""

    def __init__(self, config_path):
        self.config_path = config_path

    def live_settings(self):
        with open(self.config_path, encoding="utf-8") as config_file:
            return from_config(json.load(config_file))

    def get(self, run_id):
        return {"id": run_id, "name": "Stop at -12", "status": {"state": "done"}, "headline": {"trades": 41}}


def with_settings_proposal(ctx, tmp_path, config_path):
    """An agent whose conversation proposed a tighter stop and fewer entries a day."""
    store = ConversationStore(str(tmp_path / "agent"))
    proposals = Proposals(store, ScriptStore(str(tmp_path / "ta")), backtests=LiveBacktests(config_path))

    async def the_key():
        return "sk-ant-api03-test-key-0123456789"

    ctx.agent = AgentService(
        settings=AgentSettings(enabled=True), store=store, provider=FakeProvider(), tools=FakeTools(),
        proposals=proposals, backtests=FakeBacktests(), scripts=FakeScripts(), key=the_key, publish=ctx.events.publish,
    )
    conversation = "20261028-150000-a1b2c3"
    # Changed now: the agent's pruning keeps it.
    store.create({"id": conversation, "title": "Tune the exits", "status": "idle", "request": 0,
                  "updated_at": stamp(time.time())})
    proposal = proposals.propose_settings(conversation, 0, {
        "changes": [{"setting": "stoploss_percent", "value": -12}, {"setting": "max_trades_per_day", "value": 4}],
        "runs": [RUN], "title": "A tighter stop", "rationale": "Run X held on 22 days."})
    return f"/api/agent/conversations/{conversation}/proposals/{proposal['id']}"


def test_a_change_of_settings_is_previewed_then_saved_and_audited_as_the_configuration_page_saves(config_path, tmp_path):
    ctx = make_ctx(config_path, tmp_path)
    proposal = with_settings_proposal(ctx, tmp_path, config_path)
    client = signed_in(ctx, tmp_path)
    try:
        original = open(config_path).read()
        previewed = client.post(f"{proposal}/preview", headers=SAME_ORIGIN)
        preview = previewed.json()
        before = open(config_path).read()
        unsaved = client.post(f"{proposal}/apply", headers=SAME_ORIGIN)
        stale = client.post(f"{proposal}/apply", json={"revision": "0" * 64}, headers=SAME_ORIGIN)
        untouched = open(config_path).read()
        saved = client.post(f"{proposal}/apply", json={"revision": preview["revision"]}, headers=SAME_ORIGIN)
        again = client.post(f"{proposal}/preview", headers=SAME_ORIGIN)
        history = client.get("/api/config/history").json()["items"]
    finally:
        client.__exit__(None, None, None)

    assert previewed.status_code == 200, preview
    assert [change["path"] for change in preview["changes"]] == [
        "trade.config.position_management.performance_thresholds.stoploss_percent",
        "trade.rules[risk_management].rule_config.max_trades_per_day",
    ]
    assert preview["impact"]["risk"] == "trading" and "position_monitor" in preview["impact"]["services"]
    assert before == original, "a preview writes nothing"
    assert unsaved.status_code == 409 and "saved from its preview" in unsaved.json()["detail"]
    assert stale.status_code == 409 and untouched == before
    assert saved.status_code == 200 and saved.json()["status"] == "applied" and saved.json()["restart_required"]
    with open(config_path, encoding="utf-8") as config_file:
        config = json.load(config_file)
    assert config["trade"]["config"]["position_management"]["performance_thresholds"]["stoploss_percent"] == -12
    risk = next(rule for rule in config["trade"]["rules"] if rule["rule_type"] == "risk_management")
    assert risk["rule_config"] == {"cooldown_after_loss_minutes": 10, "max_trades_per_day": 4}
    assert again.status_code == 409
    assert history[0]["action"] == "saved from an AI agent's proposal" and len(history[0]["changes"]) == 2
    audit = next(entry for entry in ctx.audit_log.read() if entry["action"] == "configuration saved")
    assert audit["detail"].startswith("From the AI agent's proposal 'A tighter stop'. Changed ")


def test_with_config_editing_off_a_change_of_settings_is_neither_previewed_nor_saved(config_path, tmp_path):
    ctx = make_ctx(config_path, tmp_path, config_edit_enabled=False)
    proposal = with_settings_proposal(ctx, tmp_path, config_path)
    before = open(config_path).read()
    client = signed_in(ctx, tmp_path)
    try:
        preview = client.post(f"{proposal}/preview", headers=SAME_ORIGIN)
        saved = client.post(f"{proposal}/apply", json={"revision": "0" * 64}, headers=SAME_ORIGIN)
    finally:
        client.__exit__(None, None, None)

    assert preview.status_code == 403 and saved.status_code == 403
    assert open(config_path).read() == before


def test_a_research_task_is_started_bounded_and_stopped_from_the_pages(config_path, tmp_path):
    # Answers enough for whatever the task asks before the stop reaches it.
    ctx = with_agent(make_ctx(config_path, tmp_path), tmp_path, FakeProvider(*[answer(text("A look.")) for _ in range(5)]))
    client = signed_in(ctx, tmp_path)
    try:
        made = client.post("/api/agent/tasks", headers=SAME_ORIGIN,
                           json={"goal": "Find whether a slower line holds", "budget": {"usd": 3, "backtests": 5, "hours": 2}})
        stopped = client.post(f"/api/agent/conversations/{made.json()['id']}/task/stop", headers=SAME_ORIGIN)
        said = until_settled(client, made.json()["id"])
        too_dear = client.post("/api/agent/tasks", headers=SAME_ORIGIN,
                               json={"goal": "Anything", "budget": {"usd": 300, "backtests": 5, "hours": 2}})
        listed = client.get("/api/agent/conversations").json()["items"]
    finally:
        client.__exit__(None, None, None)

    assert made.status_code == 201 and made.json()["task"]["budget"] == {"usd": 3.0, "backtests": 5, "hours": 2.0}
    assert stopped.status_code == 200 and said["task"]["state"] == "ended"
    assert said["task"]["ended_because"] == "stopped by the operator"
    assert too_dear.status_code == 422 and "max_task_usd" in too_dear.json()["detail"]
    assert [item["task"]["state"] for item in listed] == ["ended"]


# ── files of the operator's computer, attached to a request ──


def test_a_file_is_staged_sent_with_its_request_and_read_back_as_it_was_sent(config_path, tmp_path):
    ctx = with_agent(make_ctx(config_path, tmp_path), tmp_path, FakeProvider(answer(text("A chart."))))
    client = signed_in(ctx, tmp_path)
    png = b"\x89PNG\r\n\x1a\n" + bytes(range(32))
    try:
        staged = client.post("/api/agent/files", headers=SAME_ORIGIN,
                             files={"file": ("../shots/chart.png", png, "image/png")})
        removed = client.post("/api/agent/files", headers=SAME_ORIGIN,
                              files={"file": ("calls.txt", b"Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345",
                                              "text/plain")})
        refused = client.post("/api/agent/files", headers=SAME_ORIGIN, files={"file": ("tool.exe", b"MZ\x90\x00",
                                                                                     "application/octet-stream")})
        unstaged = client.delete(f"/api/agent/files/{removed.json()['id']}", headers=SAME_ORIGIN)
        unknown = client.delete("/api/agent/files/f0123456789", headers=SAME_ORIGIN)
        made = client.post("/api/agent/conversations", headers=SAME_ORIGIN, json={
            "prompt": "What is it?", "attach": [{"kind": "file", "id": staged.json()["id"]}]})
        said = until_settled(client, made.json()["id"])
        image = client.get(f"/api/agent/conversations/{made.json()['id']}/files/{staged.json()['id']}")
        missing = client.get(f"/api/agent/conversations/{made.json()['id']}/files/f0123456789")
        too_many = client.post("/api/agent/conversations", headers=SAME_ORIGIN, json={
            "prompt": "And?", "attach": [{"kind": "day", "id": f"2026-10-{day}"} for day in range(20, 26)]})
    finally:
        client.__exit__(None, None, None)

    assert staged.status_code == 201 and staged.json()["name"] == "chart.png" and staged.json()["type"] == "image"
    assert removed.json()["masked"] == 1 and unstaged.status_code == 204 and unknown.status_code == 404
    assert refused.status_code == 422 and "Attach an image" in refused.json()["detail"]
    assert said["requests"][0]["attached"][0]["name"] == "chart.png"
    assert image.status_code == 200 and image.content == png and image.headers["content-type"] == "image/png"
    assert image.headers["x-content-type-options"] == "nosniff"
    assert image.headers["content-disposition"] == "inline; filename*=UTF-8''chart.png"
    assert missing.status_code == 404 and too_many.status_code == 422
