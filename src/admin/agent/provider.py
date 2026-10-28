"""
The Claude API, the one provider so far (``admin.ai_agent.provider``): the
models the agent may use, what each accepts and costs, and the calls it makes.
The SDK (anthropic) is imported in the functions that call it: with the agent
off, the admin never loads it.

Prices are Anthropic's list prices, per million tokens, as of PRICES_OF: what
a request costs is an estimate, and the pages say so. A cache write costs 1.25
times the input price for five minutes and twice for an hour; a cache read is
each model's own price.
"""

import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

from src.admin.agent.settings import EFFORTS
from src.logging_helper.redaction import register_secret

logger = logging.getLogger(__name__)

PRICES_OF = "2026-10"
# Above this many tokens of prompt, Haiku 5.5 bills its second rate.
LONG_PROMPT_TOKENS = 100_000
CACHE_WRITE_5M = 1.25
CACHE_WRITE_1H = 2.0
# The key's name for the log lines' redaction.
SECRET_NAME = "anthropic.api.key"
# The answer's length at most: thinking counts in it, and a long tool call (a script) too.
MAX_OUTPUT = 64_000
# Retries of the SDK itself (a 429, a 5xx, a connection lost), with its own waits, before the request fails.
MAX_RETRIES = 3

# The betas a request may use.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
UPDATES_BETA = "thinking-display-updates-2026-08-18"
CONTEXT_BETA = "context-management-2025-06-27"
BINDING_BETA = "thinking-binding-controls-2026-08-01"


@dataclass(frozen=True)
class Model:
    id: str
    label: str
    input: float
    output: float
    cache_read: float
    # Haiku 5.5's second rate, above LONG_PROMPT_TOKENS of prompt.
    long_input: float | None = None
    long_output: float | None = None
    long_cache_read: float | None = None
    # Anthropic's server-side fallback when its classifiers decline a request (fallbacks: "default").
    fallbacks: bool = True
    # Its notes between tool calls returned as they come (thinking display "updates"); "summarized" otherwise.
    updates: bool = True
    # Its context window and its longest answer, until the Models API says them for the key (model_info).
    window: int = 1_000_000
    max_output: int = 128_000
    # The models whose thinking it reads (the Preserved thinking page, 2026-10): a note another model wrote that
    # it cannot read is left out of its requests by the API, which answer without that reasoning.
    reads: tuple[str, ...] = ()


# What a request may choose (src/admin/agent/settings.py), then the models a fallback may answer with.
MODELS = {model.id: model for model in (
    Model("claude-opus-5-5", "Claude Opus 5.5", 4.0, 20.0, 0.20,
          reads=("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-opus-5", "claude-opus-4-8",
                 "claude-sonnet-5")),
    Model("claude-sonnet-5-5", "Claude Sonnet 5.5", 2.0, 10.0, 0.20,
          reads=("claude-sonnet-5-5", "claude-haiku-5-5", "claude-sonnet-5", "claude-opus-4-8")),
    Model("claude-haiku-5-5", "Claude Haiku 5.5", 0.10, 0.50, 0.01, long_input=0.50, long_output=2.50,
          long_cache_read=0.05, fallbacks=False, updates=False,
          reads=("claude-haiku-5-5", "claude-sonnet-5", "claude-opus-4-8")),
    Model("claude-fable-5-1", "Claude Fable 5.1", 10.0, 50.0, 0.25,
          reads=("claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-opus-4-8")),
    Model("claude-opus-5", "Claude Opus 5", 5.0, 25.0, 0.50),
    Model("claude-opus-4-8", "Claude Opus 4.8", 5.0, 25.0, 0.50),
    Model("claude-sonnet-5", "Claude Sonnet 5", 2.0, 10.0, 0.20),
)}
# A model this table does not know is priced as the dearest of the Opus line, never as nothing.
UNKNOWN_PRICED_AS = "claude-opus-5"


class NoKey(Exception):
    """No Claude API key is set (Credentials)."""


def label_of(model_id: str | None) -> str:
    model = MODELS.get(model_id or "")
    return model.label if model else (model_id or "")


def unread_by(model_id: str, writers: set[str]) -> list[str]:
    """Of the models that wrote a conversation's notes, those whose notes ``model_id`` cannot read."""
    reads = MODELS[model_id].reads
    return sorted(writer for writer in writers if writer not in reads)


def cost_of(usage: dict, model_id: str) -> float:
    """What an answer cost, in dollars: per model of its iterations when a fallback answered part of it."""
    iterations = [entry for entry in usage.get("iterations") or [] if isinstance(entry, dict) and "input_tokens" in entry]
    if iterations:
        return sum(_cost(entry, entry.get("model") or model_id) for entry in iterations)
    return _cost(usage, model_id)


def _cost(usage: dict, model_id: str) -> float:
    model = MODELS.get(model_id) or MODELS[UNKNOWN_PRICED_AS]
    uncached = usage.get("input_tokens") or 0
    written = usage.get("cache_creation_input_tokens") or 0
    read = usage.get("cache_read_input_tokens") or 0
    output = usage.get("output_tokens") or 0
    split = usage.get("cache_creation")
    if split:
        written_5m, written_1h = split.get("ephemeral_5m_input_tokens") or 0, split.get("ephemeral_1h_input_tokens") or 0
    else:
        written_5m, written_1h = written, 0
    long_prompt = model.long_input is not None and uncached + written + read > LONG_PROMPT_TOKENS
    input_price = model.long_input if long_prompt else model.input
    output_price = model.long_output if long_prompt else model.output
    read_price = model.long_cache_read if long_prompt else model.cache_read
    dollars = (uncached * input_price + written_5m * input_price * CACHE_WRITE_5M
               + written_1h * input_price * CACHE_WRITE_1H + read * read_price + output * output_price)
    return dollars / 1_000_000


def _client(key: str):
    import anthropic

    register_secret(SECRET_NAME, key)
    return anthropic.AsyncAnthropic(api_key=key, max_retries=MAX_RETRIES)


def failure_of(error: BaseException) -> tuple[str, bool]:
    """What an error of the API is said as, and whether the request may be asked again (retry)."""
    import anthropic

    from src.admin.errors import DatabaseUnavailable, NotPossible

    if isinstance(error, NoKey):
        return "No Claude API key is set: set it in Configuration › AI agent", False
    if isinstance(error, NotPossible):
        return str(error), False
    if isinstance(error, DatabaseUnavailable):
        return "The database is unavailable, and the Claude API key with it: retry in a while", True
    if isinstance(error, anthropic.AuthenticationError):
        return "The Claude API refused the key: set another in Configuration › AI agent", False
    if isinstance(error, anthropic.PermissionDeniedError):
        return "The Claude API refused this key the model or a feature asked for", False
    if isinstance(error, anthropic.NotFoundError):
        return "The Claude API does not know the model set (admin.ai_agent.model)", False
    if isinstance(error, anthropic.RateLimitError):
        return "The Claude API's limits were reached: retry in a while", True
    if isinstance(error, anthropic.BadRequestError):
        return f"The Claude API refused the request: {error.message}"[:600], False
    if isinstance(error, anthropic.APIStatusError):
        return f"The Claude API answered {error.status_code}: retry in a while", True
    if isinstance(error, anthropic.APIConnectionError):
        return "The Claude API could not be reached: retry in a while", True
    return f"Unexpected error ({type(error).__name__}): the admin's log has the details", True


async def check_key(key: str, model_id: str) -> tuple[bool, str]:
    """Whether the key opens the model: a read of the model's entry, which spends no token."""
    import anthropic

    client = _client(key)
    try:
        model = await client.with_options(max_retries=1, timeout=15).models.retrieve(model_id)
        return True, f"Works: {getattr(model, 'display_name', None) or label_of(model_id)} answers"
    except anthropic.APIError as e:
        return False, failure_of(e)[0]
    finally:
        await client.close()


class ClaudeProvider:
    """The API's client, made again when the key changes: the key is opened at each request, never kept elsewhere."""

    def __init__(self, key: Callable[[], Awaitable[str | None]]):
        self._key = key
        self._client = None
        self._client_key: str | None = None

    async def _current(self):
        key = await self._key()
        if not key:
            raise NoKey()
        if key != self._client_key:
            await self.close()
            self._client, self._client_key = _client(key), key
        return self._client

    async def stream(self, params: dict, on_event: Callable[[dict], None]) -> dict:
        """
        One answer of the API, streamed: ``on_event`` is told of each block that
        starts and each piece of text, of a note, or of a tool call's input as it
        grows (its length only); the whole answer comes back as the API's JSON.
        """
        client = await self._current()
        async with client.beta.messages.stream(**params) as stream:
            async for event in stream:
                said = _said(event)
                if said is not None:
                    on_event(said)
            message = await stream.get_final_message()
        return message.model_dump(mode="json", by_alias=True, exclude_none=True)

    async def model_info(self, model_id: str) -> dict:
        """What the Models API says of a model for this key: its window, its longest answer, the efforts it takes."""
        client = await self._current()
        found = (await client.with_options(max_retries=1, timeout=15).models.retrieve(model_id)).model_dump(mode="json")
        effort = (found.get("capabilities") or {}).get("effort") or {}
        return {
            "label": found.get("display_name"), "window": found.get("max_input_tokens"),
            "max_output": found.get("max_tokens"),
            "efforts": [level for level in EFFORTS if (effort.get(level) or {}).get("supported")] or None,
        }

    async def count_tokens(self, params: dict) -> dict:
        """The tokens of a request as it would be sent, counted by the API, which bills nothing for it."""
        client = await self._current()
        counted = await client.with_options(max_retries=1, timeout=30).beta.messages.count_tokens(**params)
        return counted.model_dump(mode="json", exclude_none=True)

    async def close(self) -> None:
        if self._client is not None:
            client, self._client, self._client_key = self._client, None, None
            try:
                await client.close()
            except Exception as e:
                logger.warning(f"The Claude API's client did not close: {e!r}")


def _said(event) -> dict | None:
    kind = getattr(event, "type", None)
    if kind == "message_start":
        return {"kind": "model", "model": event.message.model}
    if kind == "content_block_start":
        block = event.content_block
        return {"kind": "block", "index": event.index, "type": block.type, "name": getattr(block, "name", None)}
    if kind == "content_block_delta":
        delta = event.delta
        if delta.type == "text_delta":
            return {"kind": "text", "index": event.index, "text": delta.text}
        if delta.type == "thinking_delta":
            return {"kind": "thinking", "index": event.index, "text": delta.thinking}
        if delta.type == "input_json_delta":
            return {"kind": "input", "index": event.index, "length": len(delta.partial_json)}
    return None
