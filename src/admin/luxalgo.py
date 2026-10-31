"""
LuxAlgo's MCP tools, read by the admin for the AI agent and the LuxAlgo Library
page (https://mcp.luxalgo.com/mcp). LuxAlgo, the maker of the Vela chart WATA
draws with, serves there its Library of trading concepts and indicators with
their Pine source, market trackers of US public records, and prop-firm tools.

- The address is a constant, never a setting: one read from the configuration
  could send the admin's calls, and the account's token, elsewhere.
- Only ALLOWED is called: the read-only tools of those three groups. Never the
  journal, the account or Edge stats, whatever a caller asks.
- LuxAlgo asks each call for a sentence of its purpose (``context``, for its
  analytics). WATA writes it, one per group, so that what leaves for LuxAlgo
  is the call's own arguments, that sentence, the server's address and, once
  signed in, the account's token (src/admin/luxalgo_auth.py).
- Arguments are checked against the tool's schema as LuxAlgo lists it (read at
  the first use, again every TOOLS_SECONDS); an answer is kept KEPT_SECONDS per
  tool, arguments and sign-in; CALLS_AN_HOUR at most across the admin,
  CALL_SECONDS at most each (SLOW_CALLS where LuxAlgo works longer). What
  fails is a LuxAlgoRefused, said in words.
- A source is taken whole or not at all: signed out, LuxAlgo gives the first
  lines of an indicator alone.
"""

import asyncio
import json
import logging
import re
import time
from collections import OrderedDict, deque
from contextlib import asynccontextmanager

import jsonschema

logger = logging.getLogger(__name__)

LUXALGO_URL = "https://mcp.luxalgo.com/mcp"
LIBRARY, TRACKERS, PROPFIRMS = "library", "trackers", "propfirms"
GROUPS = (LIBRARY, TRACKERS, PROPFIRMS)
ALLOWED = {
    **dict.fromkeys(("library_search", "library_get_concept", "library_get_indicator", "library_get_source_code",
                     "library_list_concepts", "library_list_indicators", "library_list_families", "library_get_family"),
                    LIBRARY),
    **dict.fromkeys(("trackers_datasets", "trackers_query", "trackers_latest", "trackers_ticker"), TRACKERS),
    **dict.fromkeys(("propfirms_search", "propfirms_get", "propfirms_search_challenges", "propfirms_search_offers",
                     "propfirms_list_simulatable", "propfirms_challenge_rules", "propfirms_simulate",
                     "propfirms_optimal_risk", "propfirms_compare", "propfirms_simulate_trades", "propfirms_pass_rates",
                     "propfirms_validate_strategy"), PROPFIRMS),
}
# What LuxAlgo's analytics are told of each call (15 to 25 words, as it asks): the group's purpose, nothing else.
CONTEXT = {
    LIBRARY: "A trading research assistant reads a technical-analysis concept or indicator to study it on its user's "
             "own recorded market data.",
    TRACKERS: "A trading research assistant reads public-record market data to give its user background for a "
              "research question.",
    PROPFIRMS: "A trading research assistant simulates funded-account challenges from a strategy's own statistics "
               "for its user.",
}
TOOLS_SECONDS = 6 * 3600
KEPT_SECONDS = 3600
KEPT_MOST = 500
CALLS_AN_HOUR = 300
CALL_SECONDS = 20.0
# What a call may take where LuxAlgo works longer: a screen of up to 40 challenges, 5,000 paths each (31 to 49 s for
# 36 and 38 challenges, measured on 2026-10-31; 2 s for 5).
SLOW_CALLS = {"propfirms_validate_strategy": 90.0}
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,120}$")
_LICENCE = (
    re.compile(r"licen[cs]ed under (?:a |an |the )?(?P<said>.+?)(?:\s+https?://\S+)?\s*$", re.IGNORECASE),
    re.compile(r"subject to the terms of the (?P<said>.+?)(?:\s+at\s+https?://\S+)?\s*$", re.IGNORECASE),
)
_SHORT = re.compile(r"\((?P<short>[A-Z][A-Za-z0-9 .\-]{1,40})\)\s*$")
_OWNER = re.compile(r"©\s*(?P<owner>.+?)\s*$")
# How LuxAlgo says it has no such thing: {"error": "Unknown indicator slug 'x'."}.
_UNKNOWN = re.compile(r"\bUnknown \w+ slug\b", re.IGNORECASE)


class LuxAlgoRefused(Exception):
    """What LuxAlgo, or the admin for it, did not do: said in words, never with a token."""


class LuxAlgoUnknown(LuxAlgoRefused):
    """LuxAlgo has no indicator, concept or family by that slug."""


def licence_of(source: str) -> dict:
    """What a source's opening comments say of its licence and its owner."""
    licence = owner = None
    for line in source.splitlines()[:20]:
        text = line.strip()
        if not text.startswith("//"):
            continue
        text = text.lstrip("/").strip()
        for pattern in _LICENCE:
            found = pattern.search(text)
            if found and licence is None:
                said = found.group("said").strip().rstrip(".")
                short = _SHORT.search(said)
                licence = short.group("short") if short else said[:120]
        mark = _OWNER.search(text)
        if mark and owner is None:
            owner = mark.group("owner")[:80]
    return {"licence": licence or "see LuxAlgo's NOTICE", "owner": owner}


def _said(error: BaseException) -> str:
    text = str(error).strip()
    return text[:200] if text else type(error).__name__


def _without_context(schema: dict) -> dict:
    schema = json.loads(json.dumps(schema or {"type": "object"}))
    schema.get("properties", {}).pop("context", None)
    if "context" in schema.get("required", []):
        schema["required"] = [name for name in schema["required"] if name != "context"]
    schema.pop("title", None)
    return schema


class LuxAlgo:
    """
    The admin's client of LuxAlgo's MCP server. ``bearer``: an async callable
    giving the signed-in account's access token, or None signed out.
    ``server``: in the tests, an MCP server served in memory in place of
    LuxAlgo's address.
    """

    def __init__(self, *, bearer=None, server=None, clock=time.time):
        self._bearer = bearer
        self._server = server
        self._clock = clock
        self._tools: dict[str, dict] | None = None
        self._tools_at = 0.0
        self._kept: OrderedDict = OrderedDict()
        self._calls: deque = deque()

    async def _token(self) -> str | None:
        """The account's token; signed out when none, or when it cannot be read (a read goes on without it)."""
        if self._bearer is None:
            return None
        try:
            return await self._bearer()
        except Exception as e:
            logger.info(f"LuxAlgo is asked signed out: the sign-in could not be read ({_said(e)})")
            return None

    @asynccontextmanager
    async def _client(self, token: str | None, seconds: float | None = None):
        from mcp import Client

        if self._server is not None:
            async with Client(self._server) as client:
                yield client
            return
        import httpx2
        from mcp.client.streamable_http import streamable_http_client

        headers = {"Authorization": f"Bearer {token}"} if token else {}
        async with httpx2.AsyncClient(headers=headers, timeout=seconds or CALL_SECONDS) as http:
            async with Client(streamable_http_client(LUXALGO_URL, http_client=http)) as client:
                yield client

    async def tools(self) -> dict[str, dict]:
        """The tools WATA calls, as LuxAlgo lists them now: each its group, description and schema (no context)."""
        now = self._clock()
        if self._tools is not None and now - self._tools_at < TOOLS_SECONDS:
            return self._tools
        try:
            async with asyncio.timeout(CALL_SECONDS):
                async with self._client(await self._token()) as client:
                    listed = (await client.list_tools()).tools
        except Exception as e:
            if self._tools is not None:
                return self._tools
            raise LuxAlgoRefused(f"LuxAlgo did not answer: {_said(e)}") from None
        self._tools = {
            tool.name: {"group": ALLOWED[tool.name], "description": (tool.description or "").strip(),
                        "input_schema": _without_context(tool.input_schema)}
            for tool in listed if tool.name in ALLOWED
        }
        self._tools_at = now
        return self._tools

    def _count(self, now: float) -> None:
        while self._calls and self._calls[0] < now - 3600:
            self._calls.popleft()
        if len(self._calls) >= CALLS_AN_HOUR:
            raise LuxAlgoRefused(f"LuxAlgo was asked {CALLS_AN_HOUR} times this hour, the most the admin asks: "
                                 "the next calls wait")
        self._calls.append(now)

    async def call(self, tool: str, arguments: dict | None = None, *, structured: bool = False) -> str:
        """
        LuxAlgo's answer to one of the tools WATA calls, as text; ``structured``:
        its structured content as JSON when it gives one (its prop-firm
        simulations: each challenge's figures, where the text is a summary).
        """
        if tool not in ALLOWED:
            raise LuxAlgoRefused(f"{tool} is not one of the LuxAlgo tools WATA calls")
        arguments = {key: value for key, value in (arguments or {}).items() if key != "context"}
        listed = (await self.tools()).get(tool)
        if listed is None:
            raise LuxAlgoRefused(f"LuxAlgo does not serve {tool} now")
        try:
            jsonschema.validate(arguments, listed["input_schema"])
        except jsonschema.ValidationError as e:
            raise LuxAlgoRefused(f"{tool}: {e.message}"[:500]) from None
        if "slug" in arguments and not SLUG.match(str(arguments["slug"])):
            raise LuxAlgoRefused(f"{tool}: a slug is lowercase letters, digits and dashes")
        token = await self._token()
        key = (tool, json.dumps(arguments, sort_keys=True, default=str), bool(token), structured)
        now = self._clock()
        kept = self._kept.get(key)
        if kept is not None and now - kept[0] < KEPT_SECONDS:
            return kept[1]
        self._count(now)
        seconds = SLOW_CALLS.get(tool, CALL_SECONDS)
        try:
            async with asyncio.timeout(seconds):
                async with self._client(token, seconds) as client:
                    result = await client.call_tool(tool, {**arguments, "context": CONTEXT[ALLOWED[tool]]})
        except Exception as e:
            raise LuxAlgoRefused(f"LuxAlgo did not answer: {_said(e)}") from None
        text = "\n".join(getattr(block, "text", "") for block in result.content or [])
        if result.is_error:
            said = text
            try:
                said = str(json.loads(text).get("error") or text)
            except (ValueError, AttributeError):
                pass
            refused = LuxAlgoUnknown if _UNKNOWN.search(said) else LuxAlgoRefused
            raise refused(f"LuxAlgo refused {tool}: {said[:500] or 'no reason given'}")
        content = getattr(result, "structured_content", None)
        if structured and isinstance(content, dict) and set(content) != {"result"}:
            text = json.dumps(content, ensure_ascii=False)
        self._kept[key] = (now, text)
        while len(self._kept) > KEPT_MOST:
            self._kept.popitem(last=False)
        return text

    async def read(self, tool: str, arguments: dict | None = None, *, structured: bool = False):
        """An answer as data: LuxAlgo's JSON (its structured content when asked), or its text as it came."""
        text = await self.call(tool, arguments, structured=structured)
        try:
            return json.loads(text)
        except ValueError:
            return {"text": text}

    async def source(self, slug: str) -> dict:
        """An indicator's whole Pine source, its licence and its owner; refused when LuxAlgo gives a part of it."""
        found = await self.read("library_get_source_code", {"slug": slug})
        if not isinstance(found, dict) or not found.get("available") or not found.get("source"):
            raise LuxAlgoRefused(f"LuxAlgo has no source for {slug}")
        if not found.get("complete"):
            raise LuxAlgoRefused(
                f"LuxAlgo gives {found.get('shown_lines') or 'the first'} of {found.get('total_lines') or 'its'} lines "
                f"of {found.get('name') or slug} to an admin not signed in: sign in to LuxAlgo from the LuxAlgo "
                "Library page")
        source = found["source"].replace("\r\n", "\n")
        return {"slug": slug, "name": found.get("name") or slug, "source": source,
                "chart_url": found.get("chart_url"), **licence_of(source)}
