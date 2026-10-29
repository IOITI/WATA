"""
A conversation as the Claude API is sent it, and as the pages read it, from its
files (src/admin/agent/store.py). Pure.

- ``api_messages``: each line of the transcript as a message, an answer echoed
  as the API asks after a fallback inside it (``echoed``);
- ``runnable``: the tool calls of an answer to run, those of the model that
  answered last;
- ``project``: a conversation for the pages, request by request, from its
  activity (each step and its moments) and its proposals;
- ``context_view``: what the next request would put in the model's context
  window, part by part.
"""

import json

FALLBACK = "fallback"
# The API's own tool calls and their results, run on its side: none is used yet, kept only in pairs.
_SERVER_TOOL_USE = "server_tool_use"
# How much of a tool's answer the pages show of it: the whole is in the transcript.
SHOWN_OUTPUT_CHARS = 4000
SHOWN_INPUT_CHARS = 2000
# What a request is while the service works on it; any other is how it ended.
UNDER_WAY = ("queued", "running", "waiting")
# A conversation's tokens other than its thinking, by kind, split by their length: what the operator asked and
# shared (with the limits the admin adds), the tool calls, their results, the answers.
KINDS = ("asked", "calls", "results", "answers")
THINKING = ("thinking", "redacted_thinking")


def _last_fallback(content: list[dict]) -> int | None:
    indexes = [index for index, block in enumerate(content) if block.get("type") == FALLBACK]
    return indexes[-1] if indexes else None


def echoed(content: list[dict]) -> list[dict]:
    """
    An answer as it is sent back. Where a fallback answered part of it, the
    blocks before its last ``fallback`` block keep their text alone, and a call
    of the API's own tools with its result: the declined model's thinking and
    tool calls, a call without its result and anything unknown are left out.
    """
    last = _last_fallback(content)
    if last is None:
        return content
    before = content[:last]
    answered = {block.get("tool_use_id") for block in before if str(block.get("type", "")).endswith("_tool_result")}
    called = {block.get("id") for block in before if block.get("type") == _SERVER_TOOL_USE}
    kept = []
    for block in before:
        kind = block.get("type")
        if kind == "text":
            kept.append(block)
        elif kind == _SERVER_TOOL_USE and block.get("id") in answered:
            kept.append(block)
        elif str(kind).endswith("_tool_result") and block.get("tool_use_id") in called:
            kept.append(block)
    return kept + content[last:]


def answered_part(content: list[dict]) -> list[dict]:
    """The blocks of the model that answered last: after the last fallback, or all."""
    last = _last_fallback(content)
    return content if last is None else content[last + 1:]


def runnable(content: list[dict]) -> list[dict]:
    """The tool calls to run: the declined model's, before a fallback, never are."""
    return [block for block in answered_part(content) if block.get("type") == "tool_use"]


def answer_text(content: list[dict]) -> str:
    return "\n\n".join(block.get("text", "") for block in answered_part(content)
                       if block.get("type") == "text" and block.get("text"))


def notes_of(content: list[dict]) -> list[str]:
    """The model's notes between its tool calls (thinking shown as updates): empty when hidden."""
    return [block["thinking"].strip() for block in answered_part(content)
            if block.get("type") == "thinking" and (block.get("thinking") or "").strip()]


def api_messages(lines: list[dict]) -> list[dict]:
    """
    The transcript's lines as the API takes them: the answers echoed. A note of
    the agent's side (a request's limits) is sent last or before an answer: one
    that a request stopped before the API spoke left unanswered is not, and as
    nothing the API answered came after it, nothing it answered changes.
    """
    messages = []
    for position, line in enumerate(lines):
        role, content = line["role"], line["content"]
        if role == "system":
            following = lines[position + 1]["role"] if position + 1 < len(lines) else None
            if following not in (None, "assistant"):
                continue
        if role == "assistant":
            content = echoed(content)
        messages.append({"role": role, "content": content})
    return messages


def shown(value, limit: int = SHOWN_INPUT_CHARS):
    """A tool's input for the pages: each long text cut, the rest as it is."""
    if isinstance(value, str):
        return value if len(value) <= limit else f"{value[:limit]}… ({len(value):,} characters)"
    if isinstance(value, dict):
        return {key: shown(item, limit) for key, item in value.items()}
    if isinstance(value, list):
        return [shown(item, limit) for item in value]
    return value


def _request(requests: dict, index) -> dict:
    if index not in requests:
        requests[index] = {
            "index": index, "prompt": "", "shared": None, "asked_at": None, "started_at": None, "ended_at": None,
            "status": "queued", "detail": None, "steps": [], "answer": None, "proposals": [], "runs": [],
            "cost_usd": 0.0, "feedback": None, "model": None, "effort": None, "by": None, "memory": [],
        }
    return requests[index]


def project(meta: dict, activity: list[dict], proposals: dict, pending: dict | None = None) -> dict:
    """A conversation for the pages: its requests in order, each step of each, and what it proposed."""
    requests: dict = {}
    steps: dict = {}
    for event in activity:
        kind, index = event.get("event"), event.get("request")
        if kind in ("applied", "dismissed"):
            continue
        request = _request(requests, index)
        at = event.get("at")
        if kind == "request":
            # by: the operator's address, or "task" for a research task's next request.
            request.update(prompt=event.get("prompt", ""), shared=event.get("shared"), asked_at=at,
                           model=event.get("model"), effort=event.get("effort"), by=event.get("by"))
        elif kind == "started":
            request.update(status="running", started_at=request["started_at"] or at)
            # A request asked before it could choose: the model it ran on, the effort the settings gave.
            request["model"] = request.get("model") or event.get("model")
            request["effort"] = request.get("effort") or event.get("effort")
        elif kind == "resumed":
            request["steps"].append({"kind": "note", "id": f"resumed-{len(request['steps'])}", "at": at,
                                     "text": event.get("text") or "The admin started again: going on."})
            request["status"] = "running"
        elif kind == "note":
            request["steps"].append({"kind": "note", "id": f"note-{len(request['steps'])}", "at": at,
                                     "text": event.get("text", "")})
        elif kind == "tool":
            step = {"kind": "tool", "id": event.get("id"), "tool": event.get("tool"), "input": event.get("input"),
                    "started_at": at, "ended_at": None, "state": "running", "output": None, "progress": None}
            steps[event.get("id")] = step
            request["steps"].append(step)
        elif kind == "waiting" and event.get("id") in steps:
            steps[event["id"]]["progress"] = event.get("detail")
            request["status"] = "waiting"
        elif kind == "tool_done" and event.get("id") in steps:
            steps[event["id"]].update(state="error" if event.get("is_error") else "done", ended_at=at,
                                      output=event.get("output"))
            if request["status"] == "waiting":
                request["status"] = "running"
        elif kind == "run":
            request["runs"].append(event.get("id"))
        elif kind == "proposal":
            request["proposals"].append(event.get("id"))
        elif kind == "memory":
            request["memory"].append({"name": event.get("name"), "action": event.get("action")})
        elif kind == "answer":
            request["answer"] = {"text": event.get("text", ""), "model": event.get("model"),
                                 "stop_reason": event.get("stop_reason")}
        elif kind == "ended":
            request.update(status=event.get("status", "done"), detail=event.get("detail"), ended_at=at,
                           cost_usd=event.get("cost_usd") or 0.0)
            # A tool call cut short by a stop or a restart: said so, never left running.
            for step in request["steps"]:
                if step["kind"] == "tool" and step["state"] == "running":
                    step["state"] = "cancelled"
        elif kind == "feedback":
            request["feedback"] = event.get("value")
    ordered = [requests[index] for index in sorted(requests, key=lambda key: (key is None, key))]
    if ordered and ordered[-1]["status"] in UNDER_WAY and meta.get("status") in UNDER_WAY:
        ordered[-1]["status"] = meta["status"]
    return {
        **{key: meta.get(key) for key in (
            "id", "title", "subject", "status", "model", "effort", "created_at", "updated_at", "usage", "cost_usd",
            "error", "context", "task",
        )},
        "requests": ordered,
        "proposals": sorted(proposals.values(), key=lambda proposal: proposal.get("created_at") or ""),
        "pending": pending,
    }


# ── the context window ──

def _blocks(content) -> list[dict]:
    return [{"type": "text", "text": content}] if isinstance(content, str) else list(content or [])


def _result_length(content) -> int:
    if isinstance(content, str):
        return len(content)
    return sum(len(block.get("text") or "") if block.get("type") == "text" else len(json.dumps(block))
               for block in content or [])


def composition(messages: list[dict]) -> dict:
    """The characters of a conversation as the API is sent it, by kind (KINDS), its thinking aside."""
    sizes = dict.fromkeys(KINDS, 0)
    for message in messages:
        for block in _blocks(message["content"]):
            kind = block.get("type")
            if kind == "text":
                sizes["answers" if message["role"] == "assistant" else "asked"] += len(block.get("text") or "")
            elif kind in ("tool_use", _SERVER_TOOL_USE):
                sizes["calls"] += len(block.get("name") or "") + len(json.dumps(block.get("input") or {}))
            elif str(kind).endswith("tool_result"):
                sizes["results"] += _result_length(block.get("content"))
    return sizes


def ending_with_operator(messages: list[dict], block: dict) -> list[dict]:
    """The conversation as its next request would end it, with the operator's words (``block`` for them)."""
    if messages and messages[-1]["role"] == "user":
        last = messages[-1]
        return [*messages[:-1], {**last, "content": [*_blocks(last["content"]), block]}]
    return [*messages, {"role": "user", "content": [block]}]


def without_thinking(messages: list[dict]) -> list[dict]:
    """The conversation without its thinking blocks: what tells how many tokens its thinking holds."""
    stripped = []
    for message in messages:
        if message["role"] == "assistant" and isinstance(message["content"], list):
            kept = [block for block in message["content"] if block.get("type") not in THINKING]
            message = {**message, "content": kept or [{"type": "text", "text": "."}]}
        stripped.append(message)
    return stripped


def thinking_writers(lines: list[dict]) -> set[str]:
    """The models whose thinking the transcript holds."""
    return {line["model"] for line in lines
            if line.get("role") == "assistant" and line.get("model")
            and any(block.get("type") in THINKING for block in echoed(line.get("content") or []))}


def thinking_written(lines: list[dict], reads: tuple[str, ...]) -> int:
    """The thinking the answers wrote, as their usage says, of the models ``reads`` names: its size, estimated."""
    return sum(((line.get("usage") or {}).get("output_tokens_details") or {}).get("thinking_tokens") or 0
               for line in lines if line.get("role") == "assistant" and line.get("model") in reads)


def call_size(usage: dict, model: str | None, at: str) -> dict:
    """The conversation's size an answer leaves: its prompt and its output, which the next request carries on."""
    prompt = sum(usage.get(key) or 0 for key in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    return {"tokens": prompt + (usage.get("output_tokens") or 0), "model": model, "at": at}


def last_call(lines: list[dict]) -> dict | None:
    """What the last answer of the API used: its prompt, cached or not, its output, what context editing cleared."""
    for line in reversed(lines):
        usage = line.get("usage")
        if line.get("role") != "assistant" or not usage:
            continue
        uncached = usage.get("input_tokens") or 0
        read, written = usage.get("cache_read_input_tokens") or 0, usage.get("cache_creation_input_tokens") or 0
        edits = (line.get("context_management") or {}).get("applied_edits") or []
        dropped: dict[str, int] = {}
        for change in line.get("input_transformations") or []:
            if change.get("type") == "thinking_dropped":
                reason = change.get("reason") or "unknown"
                dropped[reason] = dropped.get(reason, 0) + 1
        return {
            "at": line.get("at"), "model": line.get("model"), "usd": line.get("usd"),
            "prompt": uncached + read + written, "uncached": uncached, "cache_read": read, "cache_write": written,
            "output": usage.get("output_tokens") or 0,
            "thinking": (usage.get("output_tokens_details") or {}).get("thinking_tokens"),
            "cleared_tokens": sum(edit.get("cleared_input_tokens") or 0 for edit in edits) or None,
            "cleared_tool_uses": sum(edit.get("cleared_tool_uses") or 0 for edit in edits) or None,
            "dropped_notes": dropped or None,
        }
    return None


def _shares(tokens: int, sizes: dict) -> dict:
    """``tokens`` split as ``sizes`` are, in whole numbers that add up to it."""
    whole = sum(sizes.values())
    if tokens <= 0 or whole <= 0:
        return dict.fromkeys(sizes, 0)
    exact = {kind: tokens * size / whole for kind, size in sizes.items()}
    split = {kind: int(value) for kind, value in exact.items()}
    for kind in sorted(exact, key=lambda kind: exact[kind] - split[kind], reverse=True)[:tokens - sum(split.values())]:
        split[kind] += 1
    return split


def context_view(*, model: str, label: str, window: int, answer_room: int, tools: int, counted: dict | None,
                 base: dict | None, composition: dict, last: dict | None, thinking_written: int, unread: list[str],
                 clearing: dict) -> dict:
    """
    The next request in the model's context window, part by part. The system
    prompt, the tools and the conversation's thinking are counted by the API
    (``base``, ``counted``); the rest of the conversation is split by its
    length (``estimated``). Without the API's counts, the last answer's prompt
    and output stand for the conversation, and its thinking for what the
    answers wrote.
    """
    parts: list[dict] = []
    if base is not None:
        parts += [{"id": "system", "tokens": base["system"]}, {"id": "tools", "tokens": base["tools"], "count": tools}]
    how = conversation = thinking = cleared = None
    if counted is not None and base is not None:
        how = "counted"
        conversation, thinking, cleared = counted["conversation"], counted["thinking"], counted["cleared"] or None
    elif last is not None:
        how = "estimated"
        conversation = last["prompt"] + last["output"] - (base["system"] + base["tools"] if base else 0)
        thinking, cleared = thinking_written, last["cleared_tokens"]
    if conversation is not None and base is not None:
        thinking = max(0, min(thinking, conversation))
        rest = _shares(max(conversation - thinking, 0), composition)
        parts += [{"id": "asked", "tokens": rest["asked"], "estimated": True},
                  {"id": "thinking", "tokens": thinking, "estimated": how == "estimated"},
                  {"id": "calls", "tokens": rest["calls"], "estimated": True},
                  {"id": "results", "tokens": rest["results"], "estimated": True},
                  {"id": "answers", "tokens": rest["answers"], "estimated": True}]
    elif conversation is not None:
        # Only the whole is known: the last answer's prompt and output.
        parts = [{"id": "conversation", "tokens": max(conversation, 0), "estimated": True}]
    if how is None and base is not None:
        how = "counted"
    return {
        "model": model, "label": label, "window": window, "answer_room": answer_room, "how": how,
        "total": sum(part["tokens"] for part in parts) if parts else None, "parts": parts, "cleared": cleared,
        "clearing": clearing, "last": last, "unread": unread,
    }
