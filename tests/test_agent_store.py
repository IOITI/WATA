"""
The AI agent's settings (admin.ai_agent) and files (src/admin/agent/store.py):
what config.json may say, ids checked before a path, the transcript only ever
appended to, a line cut by a stop skipped, spending by month, the pause, and
old conversations pruned with their lines.
"""

import json
import os

import pytest

from src.admin.agent.settings import AgentSettings
from src.admin.agent.store import ConversationStore, UnknownConversation, month_of, stamp

NOW = 1_793_174_400.0  # 2026-10-28T08:00:00Z


def test_the_settings_take_their_defaults_and_refuse_what_is_wrong():
    settings = AgentSettings.from_config(None)
    assert settings.enabled is False and settings.model == "claude-opus-5-5" and settings.effort == "high"
    assert settings.max_request_usd == 5.0 and settings.monthly_budget_usd == 50.0
    assert (settings.max_task_usd, settings.max_task_backtests, settings.max_task_hours) == (20.0, 40, 12.0)
    assert settings.debrief_enabled is False and settings.debrief_telegram is True
    assert (settings.debrief_model, settings.debrief_effort, settings.debrief_max_usd) == ("claude-sonnet-5-5", "medium", 0.5)

    # A whole number typed on the page is an amount too.
    assert AgentSettings.from_config({"max_request_usd": 7, "enabled": True}).max_request_usd == 7.0

    for wrong in ({"model": "gpt"}, {"effort": "huge"}, {"provider": "openai"}, {"enabled": "yes"},
                  {"max_tool_calls": 4}, {"max_tool_calls": True}, {"max_backtests": 31}, {"max_running": 1.5},
                  {"max_request_usd": 0.4}, {"max_request_usd": True}, {"monthly_budget_usd": 10_000.5},
                  {"keep_days": 29}, {"max_task_usd": 0.5}, {"max_task_backtests": 201}, {"max_task_hours": 73},
                  {"debrief_enabled": 1}, {"debrief_model": "gpt"}, {"debrief_effort": "none"},
                  {"debrief_max_usd": 6}, {"surprise": 1}):
        with pytest.raises(ValueError):
            AgentSettings.from_config(wrong)
    with pytest.raises(ValueError):
        AgentSettings.from_config([])


@pytest.fixture
def store(tmp_path):
    return ConversationStore(str(tmp_path / "agent"))


def meta(store, now=NOW, **changes):
    value = {"id": store.new_id(now), "title": "Analyse this run", "status": "idle", "updated_at": stamp(now),
             "request": 0, **changes}
    return store.create(value)


def test_an_id_is_checked_before_any_path_is_built(store):
    for wrong in ("../../etc", "20261028-080000-a1b2c3/..", "x", "", None):
        with pytest.raises(UnknownConversation):
            store.meta(wrong)
    with pytest.raises(UnknownConversation):
        store.meta("20261028-080000-a1b2c3")


def test_a_conversation_keeps_its_meta_and_its_journals(store):
    made = meta(store)
    assert store.new_id(NOW).startswith("20261028-080000-")

    store.append(made["id"], "messages", {"role": "user", "content": [{"type": "text", "text": "Hi"}]})
    store.append(made["id"], "activity", {"event": "request", "request": 0, "prompt": "Hi"})
    store.update_meta(made["id"], status="running")

    assert store.meta(made["id"])["status"] == "running"
    assert [line["role"] for line in store.read(made["id"], "messages")] == ["user"]
    assert store.read(made["id"], "activity")[0]["prompt"] == "Hi"
    with pytest.raises(ValueError):
        store.append(made["id"], "elsewhere", {})


def test_a_line_cut_by_a_stop_is_skipped_and_what_follows_it_read(store):
    made = meta(store)
    store.append(made["id"], "activity", {"event": "request", "request": 0})
    path = os.path.join(store.root, "conversations", made["id"], "activity.jsonl")
    with open(path, "a") as out_file:
        out_file.write('{"event": "tool", "requ')
    with open(path, "a") as out_file:
        out_file.write("\n")
    store.append(made["id"], "activity", {"event": "resumed", "request": 0})

    assert [event["event"] for event in store.read(made["id"], "activity")] == ["request", "resumed"]


def test_spending_is_counted_by_month_and_the_pause_kept(store):
    store.add_spend({"at": "2026-10-27T10:00:00Z", "usd": 0.25})
    store.add_spend({"at": "2026-10-28T10:00:00Z", "usd": 1.5})
    store.add_spend({"at": "2026-09-30T23:59:59Z", "usd": 9.0})

    assert month_of(NOW) == "2026-10"
    assert store.spent("2026-10") == pytest.approx(1.75)

    assert store.pause() is None
    assert store.set_pause(True, "10.8.0.2", NOW) == {"since": "2026-10-28T08:00:00Z", "by": "10.8.0.2"}
    assert store.pause()["by"] == "10.8.0.2"
    assert store.set_pause(False, "10.8.0.2", NOW) is None and store.pause() is None


def test_old_conversations_are_pruned_with_their_lines_and_one_under_way_kept(store):
    old = meta(store, now=NOW - 400 * 86400)
    busy = meta(store, now=NOW - 400 * 86400, status="running")
    recent = meta(store, now=NOW - 86400)
    store.add_artefact({"at": stamp(NOW - 400 * 86400), "kind": "run", "conversation": old["id"], "id": "r1"})
    store.add_artefact({"at": stamp(NOW - 86400), "kind": "run", "conversation": recent["id"], "id": "r2"})
    store.add_spend({"at": stamp(NOW - 400 * 86400), "usd": 1.0})

    assert store.prune(NOW, 365) == [old["id"]]

    assert sorted(store.ids()) == sorted([busy["id"], recent["id"]])
    assert [line["id"] for line in store.artefacts()] == ["r2"]
    assert store.spent(month_of(NOW - 400 * 86400)) == 0


def test_proposals_are_written_whole(store):
    made = meta(store)
    store.write_proposals(made["id"], {"p0123abcd": {"id": "p0123abcd", "status": "proposed"}})
    with open(os.path.join(store.root, "conversations", made["id"], "proposals.json")) as in_file:
        assert json.load(in_file)["p0123abcd"]["status"] == "proposed"
    assert store.proposals(made["id"])["p0123abcd"]["id"] == "p0123abcd"
    store.remove(made["id"])
    with pytest.raises(UnknownConversation):
        store.proposals(made["id"])
