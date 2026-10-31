"""
One request of a conversation, from the operator's prompt to the answer: the
Claude API's tool use, as a loop written here rather than the SDK's tool runner,
because each step is kept as it happens (the transcript, its activity, what it
cost) and must outlive the process. A request goes on after a restart from
what was kept, waits for backtests without calling the API, and stops between
two tool calls, never in the middle of one that writes.

Each answer is streamed: its text, the model's notes between tool calls and
the growth of a tool call's input are published as they come, for the page
that follows the conversation. The request's limits (tool calls, backtests,
dollars) are said to the model when it starts, again near one of them, and
held here whatever it does.
"""

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Callable

from src.admin.agent import provider as claude
from src.admin.agent import transcript
from src.admin.agent.prompts import files_note, system_prompt, wrap_up_note
from src.admin.agent.store import stamp
from src.admin.agent.tools import MEMORY_SAVE, STARTS

logger = logging.getLogger(__name__)

# Answers of the API that could not be read whole (a tool call's input cut in the middle), asked again.
MAX_UNREADABLE = 2
# Reads run at once, as the MCP endpoint lets a token make four calls at once.
PARALLEL_READS = 4
# Calls of the API after a limit was reached, for the answer: then the request stops whatever it does.
CALLS_AFTER_LIMIT = 2
# The share of a limit at which the model is asked to answer.
NEAR = 0.8
# Server-side context editing: the oldest tool results cleared once a request's prompt passes this, the last few kept.
CLEAR_FROM_TOKENS = 150_000
KEEP_TOOL_USES = 8
CLEAR_AT_LEAST_TOKENS = 30_000

DONE, STOPPED, FAILED, DECLINED, LIMIT, CUT = "done", "stopped", "failed", "declined", "limit", "cut"


def prompt_for(tools: list[dict]) -> str:
    """The system prompt that goes with these tools: the memory's rules with the memory's tools."""
    return system_prompt(any(tool.get("name") == MEMORY_SAVE for tool in tools))


def digest_of(tools: list[dict]) -> str:
    """The prompt and tools a conversation was begun with: another one means its thinking blocks are another's."""
    return hashlib.sha256(json.dumps([prompt_for(tools), tools], sort_keys=True).encode()).hexdigest()[:16]


@dataclass
class Used:
    """What a request used so far."""

    tool_calls: int = 0
    backtests: int = 0
    usd: float = 0.0
    calls_after_limit: int = 0
    warned: bool = False
    # The tokens of its answers, by kind (input, output, cache_read, cache_write), for the conversation's totals.
    tokens: dict = field(default_factory=dict)


@dataclass
class Outcome:
    status: str
    detail: str | None = None
    retry: bool = False
    usd: float = 0.0


@dataclass
class RequestRun:
    """What one request needs, given by the service (src/admin/agent/service.py)."""

    conversation_id: str
    request: int
    store: object
    provider: object
    tools: object
    settings: object
    publish: Callable[[dict], None]
    stopping: Callable[[], bool]
    clock: Callable[[], float] = time.time
    used: Used = field(default_factory=Used)
    pending: dict = field(default_factory=dict)
    # A request not yet sent: its prompt, what the operator shared at the conversation's start, its limits.
    opening: dict | None = None
    # The model and the effort it was asked with; the settings' when none was chosen.
    model_id: str | None = None
    effort: str | None = None
    # A request of a research task: it may say the task is done (task_done).
    task: bool = False
    # The files attached to the conversation, read once a request: each answer of the API is sent them again.
    files: dict = field(default_factory=dict)

    # ── what is kept ──

    def _activity(self, event: str, **data) -> None:
        self.store.append(self.conversation_id, "activity", {"at": stamp(self.clock()), "event": event,
                                                              "request": self.request, **data})
        self.publish({"type": "changed"})

    def _message(self, role: str, content, **data) -> None:
        self.store.append(self.conversation_id, "messages", {"at": stamp(self.clock()), "request": self.request,
                                                              "role": role, "content": content, **data})

    def _ended(self, status: str, detail: str | None = None, retry: bool = False) -> Outcome:
        self.pending.clear()
        self._activity("ended", status=status, detail=detail, cost_usd=round(self.used.usd, 6))
        return Outcome(status, detail, retry, self.used.usd)

    def resume_counts(self) -> None:
        """What a request interrupted by a restart had used: its steps and its spending, read back."""
        for event in self.store.read(self.conversation_id, "activity"):
            if event.get("request") != self.request:
                continue
            if event.get("event") == "tool":
                self.used.tool_calls += 1
            elif event.get("event") == "run":
                self.used.backtests += 1
        for line in self.store.read(self.conversation_id, "messages"):
            if line.get("request") == self.request and line.get("role") == "assistant":
                self.used.usd += float(line.get("usd") or 0)

    # ── the loop ──

    async def run(self) -> Outcome:
        settings = self.settings
        model = claude.MODELS[self.model_id or settings.model]
        effort = self.effort or settings.effort
        meta = self.store.meta(self.conversation_id)
        tools = self.tools.definitions()
        drop_block = meta.get("digest") not in (None, digest_of(tools))
        if self.stopping():
            return self._ended(STOPPED, "Stopped by the operator before it started")
        self._activity("started", model=model.id, effort=effort)
        async with self.tools.session(self.conversation_id, task=self.task) as session:
            if self.opening is not None:
                await self._open(session)
            # A request cut by a restart after an answer: its tools' results are given first, or, answered, it ends
            # (the API takes no conversation that ends with its own answer).
            lines = self.store.read(self.conversation_id, "messages")
            if lines and lines[-1]["role"] == "assistant":
                last = lines[-1]
                calls = transcript.runnable(last["content"])
                if not calls:
                    self._activity("answer", text=transcript.answer_text(last["content"]), model=last.get("model"),
                                   stop_reason=last.get("stop_reason"))
                    return self._ended(DONE)
                await self._run_tools(session, calls, interrupted=True)
            while True:
                if self.stopping():
                    return self._ended(STOPPED, "Stopped by the operator")
                if self.used.usd >= settings.max_request_usd:
                    return self._ended(LIMIT, f"Stopped at its cost limit (${settings.max_request_usd:.2f})")
                if self._past_limits():
                    self.used.calls_after_limit += 1
                    if self.used.calls_after_limit > CALLS_AFTER_LIMIT:
                        return self._ended(LIMIT, "Stopped at its limits, without an answer")
                try:
                    answer = await self._answer(model, effort, tools, drop_block)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    sentence, retry = claude.failure_of(e)
                    if not isinstance(e, claude.NoKey):
                        logger.warning(f"AI agent {self.conversation_id}: the request failed: {e!r}")
                    return self._ended(FAILED, sentence, retry)
                content, stop = answer["content"], answer.get("stop_reason")
                for note in transcript.notes_of(content):
                    self._activity("note", text=note)
                calls = transcript.runnable(content)
                if stop == "refusal":
                    details = answer.get("stop_details") or {}
                    category = details.get("category")
                    return self._ended(DECLINED, f"Claude's safeguards declined the request{f' ({category})' if category else ''}")
                if stop == "model_context_window_exceeded":
                    return self._ended(FAILED, "The conversation is too long for the model: start a new one")
                if stop == "pause_turn":
                    continue
                if not calls:
                    self._activity("answer", text=transcript.answer_text(content), model=answer.get("model"),
                                   stop_reason=stop)
                    return self._ended(CUT if stop == "max_tokens" else DONE,
                                       "The answer reached its length limit" if stop == "max_tokens" else None)
                text = transcript.answer_text(content)
                if text:
                    self._activity("note", text=text)
                await self._run_tools(session, calls, cut=stop == "max_tokens")
                self._near_limits()

    async def _open(self, session) -> None:
        """
        The request as the API is sent it: the memory's list (a conversation's
        first) or what changed in it since, what was attached to it (each thing
        as one subject is shared, then the files, named first, as the
        transcript keeps them), the prompt, its limits.
        """
        opening, content, seen = self.opening, [], None
        if opening.get("memory") is not None:
            said, seen = opening["memory"]()
            if said:
                content.append({"type": "text", "text": said})
        attached = opening.get("attached")
        if attached is None:
            attached = [opening["subject"]] if opening.get("subject") else []
        for entry in attached:
            if entry.get("kind") != "file":
                shared = await opening["shared"](session, entry, self.request)
                content.append({"type": "text", "text": shared})
        files = [entry for entry in attached if entry.get("kind") == "file"]
        if files:
            content.append({"type": "text", "text": files_note(files)})
            content += [transcript.file_block(entry) for entry in files]
        content.append({"type": "text", "text": opening["prompt"]})
        self._message("user", content)
        self._message("system", opening["limits"])
        if seen is not None:
            # The next request is told what changed after this one.
            self.store.update_meta(self.conversation_id, memory_seq=seen)

    def _file(self, file_id: str) -> bytes | None:
        """A file attached to the conversation, read once a request."""
        if file_id not in self.files:
            self.files[file_id] = self.store.read_attachment(self.conversation_id, file_id)
        return self.files[file_id]

    def _past_limits(self) -> bool:
        return self.used.tool_calls >= self.settings.max_tool_calls

    def _near_limits(self) -> None:
        if self.used.warned:
            return
        settings = self.settings
        said = []
        if self.used.tool_calls >= NEAR * settings.max_tool_calls:
            said.append(f"{self.used.tool_calls} of its {settings.max_tool_calls} tool calls")
        if settings.max_backtests and self.used.backtests >= NEAR * settings.max_backtests:
            said.append(f"{self.used.backtests} of its {settings.max_backtests} backtests")
        if self.used.usd >= NEAR * settings.max_request_usd:
            said.append(f"${self.used.usd:.2f} of its ${settings.max_request_usd:.2f}")
        if said:
            self.used.warned = True
            self._message("system", wrap_up_note(" and ".join(said)))

    async def _answer(self, model, effort: str, tools: list[dict], drop_block: bool) -> dict:
        """One answer of the API, streamed and kept; a tool call it could not read whole asked again."""
        lines = self.store.read(self.conversation_id, "messages")
        messages = await asyncio.to_thread(transcript.api_messages, lines, self._file)
        params = request_params(model, effort, tools, messages, drop_block)
        for attempt in range(MAX_UNREADABLE + 1):
            self.pending.clear()
            self.pending.update(request=self.request, text={}, note={}, writing=None)
            try:
                message = await self.provider.stream(params, self._streamed)
                break
            except ValueError:
                # The SDK's parser could not read a tool call's input streamed as it came: no block to answer.
                if attempt == MAX_UNREADABLE:
                    raise
        usage = message.get("usage") or {}
        usd = claude.cost_of(usage, message.get("model") or model.id)
        self.used.usd += usd
        for kind, key in (("input", "input_tokens"), ("output", "output_tokens"), ("cache_read", "cache_read_input_tokens"),
                          ("cache_write", "cache_creation_input_tokens")):
            self.used.tokens[kind] = self.used.tokens.get(kind, 0) + (usage.get(key) or 0)
        # What context editing cleared, and the notes the model could not read: the context window's view says them.
        self._message("assistant", message.get("content") or [], model=message.get("model"),
                      stop_reason=message.get("stop_reason"), usage=message.get("usage"), usd=round(usd, 6),
                      context_management=message.get("context_management"),
                      input_transformations=message.get("input_transformations"))
        self.store.add_spend({"at": stamp(self.clock()), "conversation": self.conversation_id, "request": self.request,
                              "model": message.get("model"), "usd": round(usd, 6)})
        # The size of the conversation as this answer left it: the pages show it without counting it again.
        self.store.update_meta(self.conversation_id, context=transcript.call_size(usage, message.get("model"),
                                                                                  stamp(self.clock())))
        self.pending.clear()
        return message

    def _streamed(self, event: dict) -> None:
        kind = event["kind"]
        if kind == "block" and event.get("type") == "tool_use":
            self.pending["writing"] = {"tool": event.get("name"), "chars": 0}
            self.publish({"type": "writing", "request": self.request, "tool": event.get("name"), "chars": 0})
        elif kind in ("text", "thinking"):
            part = "text" if kind == "text" else "note"
            texts = self.pending.setdefault(part, {})
            block = str(event["index"])
            offset = len(texts.get(block, ""))
            texts[block] = texts.get(block, "") + event["text"]
            self.publish({"type": "delta", "request": self.request, "part": part, "block": block, "offset": offset,
                          "text": event["text"]})
        elif kind == "input" and self.pending.get("writing"):
            writing = self.pending["writing"]
            writing["chars"] += event["length"]
            self.publish({"type": "writing", "request": self.request, "tool": writing["tool"], "chars": writing["chars"]})

    async def _run_tools(self, session, calls: list[dict], *, interrupted: bool = False, cut: bool = False) -> None:
        """Each call answered, in one user message: reads at once, the others in their order."""
        results: dict[str, dict] = {}
        reads, others = [], []
        # The calls of one answer are held to the limits as they are planned, before any of them runs.
        planned = {"calls": 0, "starts": 0}
        for call in calls:
            refusal = self._refusal(call, interrupted, cut, planned)
            if refusal is None:
                planned["calls"] += 1
                planned["starts"] += call["name"] in STARTS
            else:
                if cut or interrupted:
                    # Calls made, though not run: an answer cut again and again ends at the request's limit.
                    self.used.tool_calls += 1
                results[call["id"]] = {"type": "tool_result", "tool_use_id": call["id"], "content": refusal,
                                       "is_error": True}
                continue
            if self.tools.is_read(call["name"]):
                reads.append(call)
            else:
                others.append(call)
        gate = asyncio.Semaphore(PARALLEL_READS)

        async def read(call):
            async with gate:
                results[call["id"]] = await self._call(session, call)

        await asyncio.gather(*(read(call) for call in reads))
        for call in others:
            if self.stopping():
                results[call["id"]] = {"type": "tool_result", "tool_use_id": call["id"], "is_error": True,
                                       "content": "Not run: the operator stopped the request"}
                continue
            # A write is let finish whatever happens meanwhile: a run queued must have its trials journaled.
            results[call["id"]] = await asyncio.shield(self._call(session, call))
        self._message("user", [results[call["id"]] for call in calls])

    def _refusal(self, call: dict, interrupted: bool, cut: bool, planned: dict) -> str | None:
        settings = self.settings
        if cut:
            return "Not run: your answer reached its length limit in the middle of this call: make it again, shorter"
        if interrupted and not self.tools.is_read(call["name"]):
            return ("Interrupted by a restart of the admin: check whether it was done (backtest_list, pine_list, "
                    "exp_get, memory_read) before doing it again")
        if self.stopping():
            return "Not run: the operator stopped the request"
        if self.used.tool_calls + planned["calls"] >= settings.max_tool_calls:
            return f"Not run: this request's {settings.max_tool_calls} tool calls are used: answer with what you have"
        if call["name"] in STARTS and self.used.backtests + planned["starts"] >= settings.max_backtests:
            return (f"Not run: this request may start no more backtests ({settings.max_backtests}): answer with what "
                    "you have, or say what you would run")
        return None

    async def _call(self, session, call: dict) -> dict:
        self.used.tool_calls += 1
        self._activity("tool", id=call["id"], tool=call["name"], input=transcript.shown(call.get("input")))
        started = time.monotonic()
        try:
            answer = await session.call(call["name"], call.get("input"), self.request,
                                        progress=lambda detail: self._activity("waiting", id=call["id"], detail=detail),
                                        stopping=self.stopping)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception(f"AI agent {self.conversation_id}: {call['name']} failed")
            answer = None
            text, is_error = f"Unexpected error ({type(e).__name__}) in {call['name']}: the admin's log has the details", True
        if answer is not None:
            text, is_error = answer.text, answer.is_error
            for run_id in answer.runs:
                self.used.backtests += 1
                self._activity("run", id=run_id, tool=call["name"])
                self.store.add_artefact({"at": stamp(self.clock()), "kind": "run", "conversation": self.conversation_id,
                                         "id": run_id, "tool": call["name"]})
            if answer.proposal is not None:
                self._activity("proposal", id=answer.proposal["id"])
            if answer.script is not None:
                self.store.add_artefact({"at": stamp(self.clock()), "kind": "script", "conversation": self.conversation_id,
                                         **answer.script})
            if answer.experiment is not None:
                self.store.add_artefact({"at": stamp(self.clock()), "kind": "experiment",
                                         "conversation": self.conversation_id, "id": answer.experiment})
            if answer.rule is not None:
                self.store.add_artefact({"at": stamp(self.clock()), "kind": "rule", "conversation": self.conversation_id,
                                         **answer.rule})
            if answer.task_done is not None:
                # Kept with the request: a restart before its answer still ends the task.
                self._activity("task_done", **answer.task_done)
            if answer.question is not None:
                # Kept with the request: once it ends, the task waits for the operator's answer.
                self._activity("question", **answer.question)
            if answer.memory is not None:
                self._activity("memory", **answer.memory)
                self.store.add_artefact({"at": stamp(self.clock()), "kind": "memory", "conversation": self.conversation_id,
                                         "id": answer.memory["name"], **answer.memory})
            if answer.chart is not None:
                self._activity("chart", id=call["id"], chart=answer.chart)
            if answer.verdict is not None:
                self._activity("verdict", **answer.verdict)
        self._activity("tool_done", id=call["id"], is_error=is_error, seconds=round(time.monotonic() - started, 1),
                       output=transcript.shown(text, transcript.SHOWN_OUTPUT_CHARS))
        result = {"type": "tool_result", "tool_use_id": call["id"], "content": text}
        if is_error:
            result["is_error"] = True
        return result


def request_params(model, effort: str, tools: list[dict], messages: list[dict], drop_block: bool) -> dict:
    """The request to the API: the prompt and tools cached for an hour, the conversation's tail too."""
    thinking = {"type": "adaptive", "display": "updates" if model.updates else "summarized"}
    betas = [claude.CONTEXT_BETA]
    if model.updates:
        betas.append(claude.UPDATES_BETA)
    if drop_block:
        # Begun under another prompt or tools (an upgrade): its earlier thinking blocks are dropped, not refused.
        thinking["block_binding"] = {"prefix_mismatch_behavior": "drop_block"}
        betas.append(claude.BINDING_BETA)
    params = {
        "model": model.id,
        "max_tokens": claude.MAX_OUTPUT,
        "system": [{"type": "text", "text": prompt_for(tools), "cache_control": {"type": "ephemeral", "ttl": "1h"}}],
        "tools": [{**tool, "eager_input_streaming": True} for tool in tools],
        "messages": messages,
        "thinking": thinking,
        "output_config": {"effort": effort},
        # Waits for backtests outlast five minutes: an hour's cache, then.
        "cache_control": {"type": "ephemeral", "ttl": "1h"},
        "context_management": {"edits": [{
            "type": "clear_tool_uses_20250919",
            "trigger": {"type": "input_tokens", "value": CLEAR_FROM_TOKENS},
            "keep": {"type": "tool_uses", "value": KEEP_TOOL_USES},
            "clear_at_least": {"type": "input_tokens", "value": CLEAR_AT_LEAST_TOKENS},
        }]},
        "betas": betas,
    }
    if model.fallbacks:
        params["fallbacks"] = "default"
        params["betas"] = [*betas, claude.FALLBACK_BETA]
    return params


def counted_params(params: dict) -> dict:
    """
    A request as the token count takes it: what only concerns the answer (its
    length, a fallback) left out. The tools stay as they are sent, streaming
    flag included: the thinking blocks are bound to them, and a count of tools
    written otherwise is refused as another conversation's (seen 2026-10-28).
    """
    counted = {key: value for key, value in params.items() if key not in ("max_tokens", "fallbacks")}
    counted["betas"] = [beta for beta in params["betas"] if beta != claude.FALLBACK_BETA]
    return counted
