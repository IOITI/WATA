"""
The AI agent's transcript as the Claude API is sent it and as the pages read it
(src/admin/agent/transcript.py), what an answer costs (provider.py) and what the
model is told (prompts.py).
"""

import pytest

from src.admin.agent import provider, transcript
from src.admin.agent.prompts import SYSTEM_PROMPT, limits_note
from src.admin.mcp_server.instructions import DO_NOT_BELIEVE, PINE_FOR_WATA

THINK = {"type": "thinking", "thinking": "", "signature": "s1"}
CALL = {"type": "tool_use", "id": "t1", "name": "backtest_result", "input": {"run_id": "r"}}


def test_an_answer_without_fallback_is_echoed_as_it_came():
    content = [THINK, {"type": "text", "text": "Reading it."}, CALL]
    assert transcript.echoed(content) is content
    assert transcript.runnable(content) == [CALL]


def test_after_a_fallback_the_declined_part_keeps_its_text_alone_and_its_calls_never_run():
    declined_call = {**CALL, "id": "t0"}
    fallback = {"type": "fallback", "from": {"model": "claude-opus-5-5"}, "to": {"model": "claude-opus-5"}}
    after = [{"type": "text", "text": "Going on."}, CALL]
    content = [THINK, {"type": "text", "text": "Partial"}, declined_call,
               {"type": "server_tool_use", "id": "s1", "name": "web_search", "input": {}}, fallback, *after]

    assert transcript.echoed(content) == [{"type": "text", "text": "Partial"}, fallback, *after]
    assert transcript.runnable(content) == [CALL]
    assert transcript.answer_text(content) == "Going on."


def test_a_note_left_unanswered_is_not_sent_and_the_others_are():
    lines = [
        {"role": "user", "content": [{"type": "text", "text": "First"}]},
        {"role": "system", "content": "Limits of the first"},
        {"role": "user", "content": [{"type": "text", "text": "Second"}]},
        {"role": "system", "content": "Limits of the second"},
        {"role": "assistant", "content": [{"type": "text", "text": "Answer"}]},
        {"role": "user", "content": [{"type": "text", "text": "Third"}]},
        {"role": "system", "content": "Limits of the third"},
    ]

    assert [(message["role"], message["content"] if isinstance(message["content"], str) else message["content"][0]["text"])
            for message in transcript.api_messages(lines)] == [
        ("user", "First"), ("user", "Second"), ("system", "Limits of the second"), ("assistant", "Answer"),
        ("user", "Third"), ("system", "Limits of the third"),
    ]


def test_the_pages_read_each_request_its_steps_and_how_it_ended():
    activity = [
        {"at": "2026-10-28T08:00:00Z", "event": "request", "request": 0, "prompt": "Analyse it",
         "shared": {"kind": "run", "id": "r1", "label": "Cross"}},
        {"at": "2026-10-28T08:00:01Z", "event": "started", "request": 0},
        {"at": "2026-10-28T08:00:02Z", "event": "note", "request": 0, "text": "Reading the run."},
        {"at": "2026-10-28T08:00:03Z", "event": "tool", "request": 0, "id": "t1", "tool": "backtest_start", "input": {}},
        {"at": "2026-10-28T08:00:04Z", "event": "run", "request": 0, "id": "20261028-080004-abcdef"},
        {"at": "2026-10-28T08:00:05Z", "event": "tool_done", "request": 0, "id": "t1", "is_error": False, "output": "{}"},
        {"at": "2026-10-28T08:00:06Z", "event": "tool", "request": 0, "id": "t2", "tool": "backtest_wait", "input": {}},
        {"at": "2026-10-28T08:01:06Z", "event": "waiting", "request": 0, "id": "t2", "detail": "r: 2nd in line"},
        {"at": "2026-10-28T08:01:07Z", "event": "ended", "request": 0, "status": "stopped", "detail": "Stopped",
         "cost_usd": 0.42},
        {"at": "2026-10-28T08:02:00Z", "event": "request", "request": 1, "prompt": "And now?"},
        {"at": "2026-10-28T08:02:01Z", "event": "started", "request": 1},
        {"at": "2026-10-28T08:02:09Z", "event": "answer", "request": 1, "text": "It holds.", "model": "claude-opus-5-5"},
        {"at": "2026-10-28T08:02:10Z", "event": "ended", "request": 1, "status": "done", "cost_usd": 0.1},
        {"at": "2026-10-28T08:02:20Z", "event": "feedback", "request": 1, "value": "like"},
    ]
    meta = {"id": "c", "title": "Analyse it", "status": "idle"}

    said = transcript.project(meta, activity, {"p1": {"id": "p1", "created_at": "x"}}, None)

    first, second = said["requests"]
    assert first["shared"]["id"] == "r1" and first["status"] == "stopped" and first["cost_usd"] == 0.42
    assert [step["kind"] for step in first["steps"]] == ["note", "tool", "tool"]
    assert first["steps"][1]["state"] == "done" and first["runs"] == ["20261028-080004-abcdef"]
    # A wait cut by the stop is not left running.
    assert first["steps"][2]["state"] == "cancelled" and first["steps"][2]["progress"] == "r: 2nd in line"
    assert second["answer"]["text"] == "It holds." and second["feedback"] == "like"
    assert said["proposals"] == [{"id": "p1", "created_at": "x"}]


def test_a_request_under_way_takes_the_conversation_s_state():
    activity = [{"at": "t", "event": "request", "request": 0, "prompt": "Go"}]
    said = transcript.project({"status": "queued"}, activity, {}, None)
    assert said["requests"][0]["status"] == "queued"


def test_long_inputs_and_outputs_are_cut_for_the_pages():
    shown = transcript.shown({"source": "x" * 5000, "days": [1, 2]})
    assert shown["source"].endswith("(5,000 characters)") and len(shown["source"]) < 2100 and shown["days"] == [1, 2]


def test_an_answer_costs_its_tokens_at_its_model_s_prices():
    usage = {"input_tokens": 1000, "output_tokens": 2000, "cache_read_input_tokens": 100_000,
             "cache_creation_input_tokens": 10_000,
             "cache_creation": {"ephemeral_5m_input_tokens": 4000, "ephemeral_1h_input_tokens": 6000}}
    # Opus 5.5: $4 in, $20 out, $0.20 cache read, writes 1.25x and 2x the input price.
    expected = (1000 * 4 + 2000 * 20 + 100_000 * 0.20 + 4000 * 4 * 1.25 + 6000 * 4 * 2) / 1e6
    assert provider.cost_of(usage, "claude-opus-5-5") == pytest.approx(expected)

    without_split = {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 1000}
    assert provider.cost_of(without_split, "claude-sonnet-5-5") == pytest.approx(1000 * 2 * 1.25 / 1e6)


def test_a_fallback_s_part_is_priced_at_its_own_model_and_haiku_s_long_prompts_at_its_second_rate():
    usage = {"input_tokens": 3000, "output_tokens": 300, "iterations": [
        {"type": "message", "model": "claude-opus-5-5", "input_tokens": 1000, "output_tokens": 100},
        {"type": "fallback_message", "model": "claude-opus-5", "input_tokens": 2000, "output_tokens": 200},
    ]}
    assert provider.cost_of(usage, "claude-opus-5-5") == pytest.approx((1000 * 4 + 100 * 20 + 2000 * 5 + 200 * 25) / 1e6)

    long_prompt = {"input_tokens": 150_000, "output_tokens": 1000}
    assert provider.cost_of(long_prompt, "claude-haiku-5-5") == pytest.approx((150_000 * 0.5 + 1000 * 2.5) / 1e6)
    # A model the table does not know is never priced as nothing.
    assert provider.cost_of({"input_tokens": 1_000_000}, "claude-unknown") == pytest.approx(5.0)


def test_the_prompt_is_one_and_says_the_bar_and_the_rules_of_pine():
    assert DO_NOT_BELIEVE in SYSTEM_PROMPT and PINE_FOR_WATA in SYSTEM_PROMPT
    # Said apart, the limits change nothing of the prompt, which the API caches and binds thinking to.
    assert "$5.00" in limits_note(60, 8, 5.0) and "start no backtest" in limits_note(60, 0, 5.0)
    for unknown in ("Claude Code", "the Mac", "job_wait"):
        assert unknown not in SYSTEM_PROMPT
