"""
The context window of the AI agent's conversations: what the next request would
put in the model's window, part by part (src/admin/agent/transcript.py, pure),
counted by the API through the service (service.py) against a scripted token
count, and estimated from the last answer when the API cannot count.
"""

import asyncio
import json

import pytest

from src.admin.agent import provider as claude
from src.admin.agent import transcript
from src.admin.agent.store import ConversationStore
from src.admin.errors import InvalidRequest
from tests.test_agent_loop import FakeProvider, conversation, service_of, text

THOUGHT = 500


def thinking(note="I look at the edge first.", signature="s"):
    return {"type": "thinking", "thinking": note, "signature": signature}


def used(prompt=9000, cached=8000, written=600, output=400, thought=250, **extra):
    return {"input_tokens": prompt - cached - written, "cache_read_input_tokens": cached,
            "cache_creation_input_tokens": written, "output_tokens": output,
            "output_tokens_details": {"thinking_tokens": thought}, **extra}


LINES = [
    {"role": "user", "request": 0, "content": [text("The operator shares the run."), text("Can it be believed?")]},
    {"role": "system", "request": 0, "content": "This request may make up to 60 tool calls."},
    {"role": "assistant", "request": 0, "model": "claude-opus-5-5", "usage": used(),
     "content": [thinking(), {"type": "tool_use", "id": "t1", "name": "backtest_result", "input": {"run_id": "r"}}]},
    {"role": "user", "request": 0,
     "content": [{"type": "tool_result", "tool_use_id": "t1", "content": '{"trades": 42, "days": 21}' * 10}]},
    {"role": "assistant", "request": 0, "model": "claude-opus-5-5", "at": "2026-10-28T08:05:00Z", "usd": 0.21,
     "usage": used(prompt=12000, cached=11000, written=900, output=700, thought=300),
     "context_management": {"applied_edits": [{"type": "clear_tool_uses_20250919", "cleared_input_tokens": 4000,
                                               "cleared_tool_uses": 3}]},
     "input_transformations": [{"type": "thinking_dropped", "reason": "model_binding_mismatch", "path": "x"}],
     "content": [thinking("It holds."), text("It can be believed: 42 trades on 21 days.")]},
]


# ── the pure parts ──


def test_a_conversation_is_measured_by_kind_its_thinking_aside():
    sizes = transcript.composition(transcript.api_messages(LINES))

    assert sizes["asked"] == len("The operator shares the run.") + len("Can it be believed?") + len(LINES[1]["content"])
    assert sizes["calls"] == len("backtest_result") + len(json.dumps({"run_id": "r"}))
    assert sizes["results"] == len('{"trades": 42, "days": 21}' * 10)
    assert sizes["answers"] == len("It can be believed: 42 trades on 21 days.")


def test_a_count_ends_on_the_operator_s_next_words_and_leaves_thinking_out_when_asked():
    after_answer = transcript.ending_with_operator([{"role": "assistant", "content": [text("Done.")]}], text("."))
    after_prompt = transcript.ending_with_operator([{"role": "user", "content": "Hello"}], text("."))

    assert after_answer[-1] == {"role": "user", "content": [text(".")]}
    assert after_prompt == [{"role": "user", "content": [text("Hello"), text(".")]}]
    stripped = transcript.without_thinking([
        {"role": "assistant", "content": [thinking(), text("Yes.")]},
        {"role": "assistant", "content": [{"type": "redacted_thinking", "data": "x"}]},
    ])
    assert stripped == [{"role": "assistant", "content": [text("Yes.")]}, {"role": "assistant", "content": [text(".")]}]


def test_the_last_answer_says_its_prompt_cached_or_not_what_was_cleared_and_the_notes_left_out():
    last = transcript.last_call(LINES)

    assert (last["prompt"], last["cache_read"], last["cache_write"], last["uncached"]) == (12000, 11000, 900, 100)
    assert (last["output"], last["thinking"], last["usd"]) == (700, 300, 0.21)
    assert (last["cleared_tokens"], last["cleared_tool_uses"]) == (4000, 3)
    assert last["dropped_notes"] == {"model_binding_mismatch": 1}
    assert transcript.last_call(LINES[:2]) is None


def test_a_model_reads_the_notes_of_the_models_it_can_and_says_which_it_cannot():
    writers = transcript.thinking_writers([*LINES, {"role": "assistant", "model": "claude-sonnet-5-5",
                                                    "content": [thinking(), text("Yes.")]}])

    assert writers == {"claude-opus-5-5", "claude-sonnet-5-5"}
    assert claude.unread_by("claude-opus-5-5", writers) == []
    assert claude.unread_by("claude-sonnet-5-5", writers) == ["claude-opus-5-5"]
    assert claude.unread_by("claude-haiku-5-5", writers) == ["claude-opus-5-5", "claude-sonnet-5-5"]
    assert claude.unread_by("claude-fable-5-1", writers) == ["claude-sonnet-5-5"]
    assert transcript.thinking_written(LINES, claude.MODELS["claude-opus-5-5"].reads) == 550


def _view(**changes):
    given = dict(model="claude-opus-5-5", label="Claude Opus 5.5", window=1_000_000, answer_room=64_000, tools=31,
                 counted={"conversation": 20_000, "thinking": 6_000, "cleared": 4_000},
                 base={"system": 2_000, "tools": 6_000, "empty": 10, "with_tools": 8_010},
                 composition={"asked": 100, "calls": 100, "results": 700, "answers": 100}, last=None,
                 thinking_written=0, unread=[], clearing={"from_tokens": 150_000, "keep_tool_uses": 8})
    return transcript.context_view(**{**given, **changes})


def test_the_view_counts_each_part_and_splits_the_rest_of_the_conversation_by_its_length():
    view = _view()

    tokens = {part["id"]: part["tokens"] for part in view["parts"]}
    assert tokens == {"system": 2_000, "tools": 6_000, "asked": 1_400, "thinking": 6_000, "calls": 1_400,
                      "results": 9_800, "answers": 1_400}
    assert view["total"] == 28_000 and view["how"] == "counted" and view["cleared"] == 4_000
    assert [part["id"] for part in view["parts"] if part.get("estimated")] == ["asked", "calls", "results", "answers"]
    assert view["parts"][1]["count"] == 31


def test_without_the_api_s_counts_the_last_answer_stands_for_the_conversation():
    last = transcript.last_call(LINES)

    estimated = _view(counted=None, last=last, thinking_written=550)
    tokens = {part["id"]: part["tokens"] for part in estimated["parts"]}
    assert estimated["how"] == "estimated" and estimated["total"] == 12_700
    assert tokens["thinking"] == 550 and tokens["system"] == 2_000
    assert sum(tokens[kind] for kind in transcript.KINDS) == 12_700 - 8_000 - 550
    assert next(part for part in estimated["parts"] if part["id"] == "thinking")["estimated"] is True
    assert estimated["cleared"] == 4_000

    # Not even the system prompt counted: the whole alone.
    whole = _view(counted=None, base=None, last=last)
    assert whole["parts"] == [{"id": "conversation", "tokens": 12_700, "estimated": True}]
    assert _view(counted=None, base=None, last=None)["total"] is None


def test_a_new_conversation_holds_the_system_prompt_and_the_tools_alone():
    view = _view(counted={"conversation": 0, "thinking": 0, "cleared": 0}, composition=dict.fromkeys(transcript.KINDS, 0))

    assert view["total"] == 8_000 and view["cleared"] is None


# ── counted by the API, through the service ──


class CountingProvider(FakeProvider):
    """The token count: a token for four characters, a thinking block worth THOUGHT, tool results cleared."""

    def __init__(self, failing=False):
        super().__init__()
        self.counted = []
        self.failing = failing

    async def count_tokens(self, params):
        if self.failing:
            raise ConnectionError("the API is out of reach")
        self.counted.append(params)
        tokens = 10
        if "system" in params:
            tokens += len(json.dumps(params["system"])) // 4
        if "tools" in params:
            tokens += len(json.dumps(params["tools"])) // 4
        cleared = 0
        for message in params["messages"]:
            for block in transcript._blocks(message["content"]):
                size = THOUGHT if block.get("type") in transcript.THINKING else len(json.dumps(block)) // 4
                tokens += size
                # Context editing leaves the old tool results out: their tokens before, not after.
                cleared += size if block.get("type") == "tool_result" else 0
        counted = {"input_tokens": tokens - cleared}
        if cleared:
            counted["context_management"] = {"original_input_tokens": tokens}
        return counted


def _conversation(store, model="claude-opus-5-5"):
    conversation_id = conversation(store, status="idle", model=model, effort="high")
    for line in LINES:
        store.append(conversation_id, "messages", line)
    return conversation_id


@pytest.fixture
def store(tmp_path):
    return ConversationStore(str(tmp_path / "agent"))


def test_the_api_counts_the_system_prompt_and_tools_once_then_each_state_of_a_conversation(store):
    provider = CountingProvider()
    agent, _ = service_of(store, provider)
    conversation_id = _conversation(store)

    view = asyncio.run(agent.context(conversation_id))
    again = asyncio.run(agent.context(conversation_id))

    # Three counts for the system prompt and the tools, two for the conversation: none more for the same state.
    assert len(provider.counted) == 5 and again == view
    assert view["how"] == "counted" and view["window"] == 1_000_000 and view["answer_room"] == claude.MAX_OUTPUT
    tokens = {part["id"]: part["tokens"] for part in view["parts"]}
    assert tokens["thinking"] == 2 * THOUGHT and view["cleared"] == len(json.dumps(LINES[3]["content"][0])) // 4
    assert tokens["tools"] > 0 and tokens["system"] > 0
    assert view["total"] == sum(tokens.values())
    # Counted as the next request would be sent: the conversation's model, the operator's words last.
    full = provider.counted[3]
    assert full["model"] == "claude-opus-5-5" and full["messages"][-1] == {"role": "user", "content": [text(".")]}
    assert "max_tokens" not in full and "fallbacks" not in full and claude.FALLBACK_BETA not in full["betas"]
    # The tools as they are sent: the thinking blocks are bound to them.
    assert all(tool["eager_input_streaming"] for tool in full["tools"])
    assert view["last"]["prompt"] == 12_000 and view["unread"] == []

    store.append(conversation_id, "messages", {"role": "user", "request": 1, "content": [text("And now?")]})
    asyncio.run(agent.context(conversation_id))
    assert len(provider.counted) == 7


def test_a_conversation_is_counted_for_another_model_which_says_whose_notes_it_cannot_read(store):
    provider = CountingProvider()
    agent, _ = service_of(store, provider)
    conversation_id = _conversation(store)

    view = asyncio.run(agent.context(conversation_id, "claude-sonnet-5-5"))

    assert view["model"] == "claude-sonnet-5-5" and view["label"] == "Claude Sonnet 5.5"
    assert all(params["model"] == "claude-sonnet-5-5" for params in provider.counted)
    assert view["unread"] == ["Claude Opus 5.5"]
    with pytest.raises(InvalidRequest, match="The model is one of"):
        asyncio.run(agent.context(conversation_id, "claude-opus-4-8"))


def test_a_new_conversation_s_window_and_one_the_api_cannot_count(store):
    agent, _ = service_of(store, CountingProvider())

    fresh = asyncio.run(agent.context(None))
    assert [part["id"] for part in fresh["parts"]] == ["system", "tools", "asked", "thinking", "calls", "results",
                                                        "answers"]
    assert fresh["total"] == fresh["parts"][0]["tokens"] + fresh["parts"][1]["tokens"]

    unreachable, _ = service_of(store, CountingProvider(failing=True))
    conversation_id = _conversation(store)
    view = asyncio.run(unreachable.context(conversation_id))
    assert view["how"] == "estimated" and view["parts"] == [{"id": "conversation", "tokens": 12_700, "estimated": True}]
