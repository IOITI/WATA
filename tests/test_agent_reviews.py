"""
A change reviewed by the AI agent before the operator saves it (the review
routes of src/admin/routers/agent.py, the change and draft subjects of
src/admin/agent/service.py): a change of config.json as the Configuration page
previews it, masked, with the settings a backtest can test; a change of a
signal rule, or a new one, pinned as a save pins it; an unsaved draft of a
script, kept with its conversation, its difference and its check shared. Nothing
is saved by a review, and a subject of either kind is never taken from the
generic route.
"""

import asyncio
import copy
import json

import pytest

from src.admin.agent.store import ConversationStore
from src.admin.ta_rules import RuleStore
from src.admin.ta_scripts import ScriptStore
from tests.test_admin_api import SAME_ORIGIN, config_path, make_ctx  # noqa: F401 (fixtures)
from tests.test_admin_ta_rules import BANDS, MATHS, STRATEGY, fields
from tests.test_agent_api import signed_in, until_settled, with_agent
from tests.test_agent_loop import FakeProvider, answer, text
from tests.test_agent_tasks import asked_text

RISK = "trade.rules[risk_management].rule_config.max_trades_per_day"


def reviewing(config_path, tmp_path):
    ctx = with_agent(make_ctx(config_path, tmp_path), tmp_path,
                     FakeProvider(answer(text("It holds on 21 days.")), answer(text("Not yet: 9 trades."))))
    scripts = ScriptStore(str(tmp_path / "ta"))
    scripts.create("Maths", MATHS)
    scripts.create("Bands", BANDS)
    ctx.ta_scripts = scripts
    ctx.agent._scripts = scripts
    return ctx, scripts


def test_a_change_of_config_json_is_reviewed_as_previewed_and_nothing_is_saved(config_path, tmp_path):
    ctx, _ = reviewing(config_path, tmp_path)
    loaded = ctx.config_editor.load()
    changed = copy.deepcopy(loaded["config"])
    risk = next(rule for rule in changed["trade"]["rules"] if rule["rule_type"] == "risk_management")
    risk["rule_config"]["max_trades_per_day"] = risk["rule_config"].get("max_trades_per_day", 10) + 1
    client = signed_in(ctx, tmp_path)
    try:
        made = client.post("/api/agent/reviews/config", json={"config": changed}, headers=SAME_ORIGIN)
        said = until_settled(client, made.json()["id"])
        same = client.post("/api/agent/reviews/config", json={"config": loaded["config"]}, headers=SAME_ORIGIN)
        generic = client.post("/api/agent/conversations", json={"prompt": "Review", "subject": {"kind": "change",
                                                                                               "id": "0a1b2c3d"}},
                              headers=SAME_ORIGIN)
    finally:
        client.__exit__(None, None, None)

    assert made.status_code == 201 and said["status"] == "idle"
    subject = said["subject"]
    assert subject["kind"] == "change" and subject["label"] == "a change of config.json, 1 key"
    assert [change["path"] for change in subject["change"]["changes"]] == [RISK]
    assert subject["change"]["testable"] == {RISK: "max_trades_per_day"}
    assert "Review this change of config.json before the operator saves it" in asked_text(ctx.agent._provider.asked[0])
    assert ctx.config_editor.load()["revision"] == loaded["revision"], "nothing saved"
    assert same.status_code == 422 and "Nothing changed" in same.json()["detail"]
    assert generic.status_code == 422 and "from the page that edits" in generic.json()["detail"]


def test_a_rule_s_change_is_reviewed_part_by_part(config_path, tmp_path):
    ctx, scripts = reviewing(config_path, tmp_path)
    cross = scripts.create("Cross", STRATEGY)
    rules = RuleStore(scripts)
    rule = rules.create(fields(cross), rules.load()["revision"])["rule"]
    asked = {**fields(cross), "revision": rules.load()["revision"], "bars": {"type": "time", "timeframe": "5"}}
    client = signed_in(ctx, tmp_path)
    try:
        made = client.post("/api/agent/reviews/rule", json={"rule_id": rule["id"], "fields": asked}, headers=SAME_ORIGIN)
        said = until_settled(client, made.json()["id"])
        new = client.post("/api/agent/reviews/rule", json={"fields": {**asked, "name": "Cross, slow"}},
                          headers=SAME_ORIGIN)
    finally:
        client.__exit__(None, None, None)

    change = said["subject"]["change"]
    assert said["subject"]["label"] == "a change of the rule Cross on US100"
    assert change["rule"] == {"id": rule["id"], "name": "Cross on US100", "mode": "shadow"}
    assert [part["part"] for part in change["changes"]] == ["bars"]
    assert change["changes"][0] == {"part": "bars", "before": {"type": "time", "timeframe": "1"},
                                    "after": {"type": "time", "timeframe": "5"}}
    assert new.status_code == 201 and rules.rule(rule["id"])["bars"]["timeframe"] == "1", "nothing saved"


def test_a_script_s_draft_is_kept_with_its_conversation_and_shared_with_its_difference(config_path, tmp_path):
    ctx, scripts = reviewing(config_path, tmp_path)
    cross = scripts.create("Cross", STRATEGY)
    draft = STRATEGY.replace('input.int(9, "Fast")', 'input.int(14, "Fast")')
    client = signed_in(ctx, tmp_path)
    try:
        made = client.post("/api/agent/reviews/script", json={"script_id": cross["id"], "base_version": 1,
                                                               "source": draft}, headers=SAME_ORIGIN)
        said = until_settled(client, made.json()["id"])
        missing = client.post("/api/agent/reviews/script", json={"script_id": cross["id"], "base_version": 4,
                                                                  "source": draft}, headers=SAME_ORIGIN)
    finally:
        client.__exit__(None, None, None)

    store = ConversationStore(str(tmp_path / "agent"))
    assert said["subject"]["kind"] == "draft" and said["subject"]["base_version"] == 1
    assert store.read_file(said["id"], "draft.pine") == draft
    shared = asked_text(ctx.agent._provider.asked[0])
    assert '-fast = input.int(9, "Fast")' in shared and '+fast = input.int(14, "Fast")' in shared
    assert "Its check:" in shared and scripts.script(cross["id"])["version"] == 1, "nothing saved"
    assert missing.status_code == 422 and "has no version 4" in missing.json()["detail"]


def test_a_script_s_change_proposed_on_a_draft_is_put_in_the_editor_unsaved(config_path, tmp_path):
    from src.admin.agent.proposals import Proposals

    ctx, scripts = reviewing(config_path, tmp_path)
    cross = scripts.create("Cross", STRATEGY)
    ctx.agent.proposals = Proposals(ctx.agent.store, scripts)
    client = signed_in(ctx, tmp_path)
    try:
        made = client.post("/api/agent/reviews/script", json={"script_id": cross["id"], "base_version": 1,
                                                               "source": STRATEGY}, headers=SAME_ORIGIN).json()
        until_settled(client, made["id"])
        fix = scripts.create("Claude · cross fixed", STRATEGY.replace("9", "11"), made_by="claude")
        proposal = ctx.agent.proposals.propose_script(made["id"], 0, {
            "script_id": cross["id"], "base_version": 1, "from_script_id": fix["id"], "from_version": 1,
            "title": "A slower line", "rationale": "Fixed."})
        put = client.post(f"/api/agent/conversations/{made['id']}/proposals/{proposal['id']}/apply",
                          json={"into_editor": True}, headers=SAME_ORIGIN)
    finally:
        client.__exit__(None, None, None)

    assert put.status_code == 200 and put.json()["result"] == {"into_editor": True, "script_id": cross["id"]}
    assert scripts.script(cross["id"])["version"] == 1, "nothing saved"
