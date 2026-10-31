"""LuxAlgo's MCP tools as the admin calls them (src/admin/luxalgo.py), on a LuxAlgo made up here: never the network."""

import asyncio
import json
import socket
import threading
import time

import pytest
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from src.admin import luxalgo
from src.admin.luxalgo import (
    ALLOWED, CONTEXT, LIBRARY, PROPFIRMS, TRACKERS, LuxAlgo, LuxAlgoRefused, LuxAlgoUnknown, licence_of,
)

HEADER = ("// This work is licensed under a Attribution-NonCommercial-ShareAlike 4.0 International (CC BY-NC-SA 4.0) "
          "https://creativecommons.org/licenses/by-nc-sa/4.0/\r\n// © LuxAlgo\r\n\r\n")
WHOLE = HEADER + '//@version=6\r\nindicator("Made up", overlay = true)\r\nplot(close)\r\n'


class FakeLuxAlgo:
    """LuxAlgo's server as it answers, a few of its tools and a journal's: what each call received is kept."""

    def __init__(self, *, whole=True, slow=0.0):
        self.received: list[tuple[str, dict]] = []
        self.whole = whole
        server = MCPServer(name="luxalgo", version="2.1.0", log_level="WARNING", subscriptions=False)

        @server.tool(name="library_search")
        async def library_search(query: str, context: str, type: str = "all", limit: int = 10) -> str:
            """Search the Library."""
            self.received.append(("library_search", {"query": query, "context": context, "type": type,
                                                     "limit": limit}))
            await asyncio.sleep(slow)
            return json.dumps({"results": [{"kind": "indicator", "slug": "made-up", "name": "Made up",
                                            "family": "trend"}]})

        @server.tool(name="library_get_source_code")
        async def library_get_source_code(slug: str, context: str) -> str:
            """An indicator's Pine source."""
            self.received.append(("library_get_source_code", {"slug": slug, "context": context}))
            if slug == "unknown":
                raise ToolError(json.dumps({"error": "Unknown indicator slug 'unknown'."}))
            if slug == "refused":
                raise ToolError("No indicator refused")
            if self.whole:
                return json.dumps({"slug": slug, "name": "Made up", "available": True, "complete": True,
                                   "source": WHOLE, "chart_url": "https://vela.luxalgo.com/chart?pineScriptCodeId=x"})
            return json.dumps({"slug": slug, "name": "Made up", "available": True, "complete": False,
                               "source": HEADER, "shown_lines": 3, "total_lines": 201,
                               "reason": "sign-in-for-full-source"})

        @server.tool(name="library_list_families")
        async def library_list_families(context: str) -> str:
            """The Library's families."""
            self.received.append(("library_list_families", {"context": context}))
            return json.dumps({"families": [{"key": "trend", "name": "Trend", "concept_count": 104}]})

        @server.tool(name="library_list_indicators")
        async def library_list_indicators(context: str, family: str = "", text: str = "", concept: str = "",
                                          sort: str = "date", direction: str = "desc", page: int = 0,
                                          page_size: int = 24) -> str:
            """The Library's indicators, a page at a time."""
            self.received.append(("library_list_indicators", {"context": context, "family": family, "text": text,
                                                              "page": page, "page_size": page_size, "sort": sort}))
            return json.dumps({"total": 1, "page": page, "page_size": page_size, "indicators": [
                {"slug": "made-up", "name": "Made up", "family": "trend", "description": "An indicator made up."}]})

        @server.tool(name="library_get_indicator")
        async def library_get_indicator(slug: str, context: str) -> str:
            """One indicator."""
            self.received.append(("library_get_indicator", {"slug": slug, "context": context}))
            if slug == "unknown":
                raise ToolError(json.dumps({"error": "Unknown indicator slug 'unknown'."}))
            return json.dumps({"slug": slug, "name": "Made up", "family": "trend", "body_markdown": "**Made up**.",
                               "concepts": [{"slug": "supertrend", "name": "supertrend", "primary": True}],
                               "code": {"available": True}})

        @server.tool(name="library_get_concept")
        async def library_get_concept(slug: str, context: str) -> str:
            """One concept."""
            self.received.append(("library_get_concept", {"slug": slug, "context": context}))
            return json.dumps({"slug": slug, "name": "Supertrend", "body_markdown": "A trend line."})

        @server.tool(name="trackers_latest")
        async def trackers_latest(dataset: str, context: str, limit: int = 20) -> str:
            """The latest rows of a dataset."""
            self.received.append(("trackers_latest", {"dataset": dataset, "context": context, "limit": limit}))
            return "Not JSON: 3 rows"

        @server.tool(name="propfirms_simulate")
        async def propfirms_simulate(winRate: float, avgWinR: float, tradesPerDay: float, riskValue: float,
                                     context: str) -> str:
            """A challenge simulated."""
            self.received.append(("propfirms_simulate", {"winRate": winRate, "context": context}))
            return json.dumps({"passRate": 0.31})

        @server.tool(name="propfirms_validate_strategy")
        async def propfirms_validate_strategy(tradesPerDay: float, riskValue: float, context: str,
                                              rSeries: list[float] | None = None, riskMode: str = "percent-of-balance",
                                              productType: str = "futures", minPassPerAttempt: float = 0.5,
                                              accountSizeMin: float | None = None,
                                              accountSizeMax: float | None = None) -> str:
            """Which challenges a strategy would pass."""
            self.received.append(("propfirms_validate_strategy", {
                "rSeries": rSeries, "tradesPerDay": tradesPerDay, "riskValue": riskValue, "riskMode": riskMode,
                "productType": productType, "minPassPerAttempt": minPassPerAttempt, "context": context,
                "accountSizeMin": accountSizeMin, "accountSizeMax": accountSizeMax}))
            # LuxAlgo's structured answer, as its simulator gives it (its text is a summary of the same).
            return json.dumps({"bar": {"minPassPerAttempt": minPassPerAttempt, "requirePositiveEv": False},
                               "profileKind": "bootstrap", "passing": [{
                                   "firmId": "example-funding", "firmName": "Example Funding",
                                   "challengeId": "example-50k", "name": "Example Funding - 50K", "productType": productType,
                                   "accountSize": 50000, "currency": "usd", "price": 345, "passPerAttempt": 0.62,
                                   "passCiLow": 0.6, "passCiHigh": 0.64, "fundedProbability": 0.41,
                                   "expectedAttempts": 1.6, "expectedCost": 552.0, "evTotal": 830.0, "pEvPositive": 0.7,
                                   "meetsBar": True, "flagIds": ["attempts-iid"], "inferredFields": []}],
                               "belowBar": [], "excludedNotSimulatable": 2, "note": "A screen of distributions.",
                               "meta": {"seed": 42, "paths": 5000}, "disclaimer": "Simulation, not prediction."})

        @server.tool(name="journal_add_trade")
        async def journal_add_trade(symbol: str, context: str) -> str:
            """Writes to the account's journal."""
            self.received.append(("journal_add_trade", {"symbol": symbol}))
            return "{}"

        self.server = server


def client(fake, **given) -> LuxAlgo:
    return LuxAlgo(server=fake.server, **given)


def test_the_allowlist_is_the_library_the_trackers_and_the_prop_firm_tools():
    assert set(ALLOWED.values()) == {LIBRARY, TRACKERS, PROPFIRMS}
    assert len(ALLOWED) == 24
    assert not [name for name in ALLOWED if not name.startswith(("library_", "trackers_", "propfirms_"))]
    for words in CONTEXT.values():
        assert 15 <= len(words.split()) <= 25, words


def test_only_the_allowlist_is_listed_and_without_its_context():
    fake = FakeLuxAlgo()
    tools = asyncio.run(client(fake).tools())
    assert set(tools) == {"library_search", "library_get_source_code", "library_list_families", "library_list_indicators",
                          "library_get_indicator", "library_get_concept", "trackers_latest", "propfirms_simulate",
                          "propfirms_validate_strategy"}
    for tool in tools.values():
        assert "context" not in tool["input_schema"].get("properties", {})
        assert "context" not in tool["input_schema"].get("required", [])
    assert tools["library_search"]["group"] == LIBRARY
    assert tools["library_search"]["input_schema"]["required"] == ["query"]


@pytest.mark.parametrize("tool", ["journal_add_trade", "account_get", "edge_get_stats", "library_delete"])
def test_a_tool_outside_the_allowlist_is_never_called(tool):
    fake = FakeLuxAlgo()
    with pytest.raises(LuxAlgoRefused, match="not one of the LuxAlgo tools WATA calls"):
        asyncio.run(client(fake).call(tool, {"symbol": "NQ"}))
    assert fake.received == []


def test_each_call_carries_the_fixed_context_of_its_group_and_nothing_else():
    fake = FakeLuxAlgo()
    lux = client(fake)

    async def calls():
        await lux.call("library_search", {"query": "supertrend",
                                          "context": "The operator asked about the trade of position 4211"})
        await lux.call("trackers_latest", {"dataset": "congress-trades"})
        await lux.call("propfirms_simulate", {"winRate": 0.5, "avgWinR": 1.2, "tradesPerDay": 2, "riskValue": 1})

    asyncio.run(calls())
    assert [(tool, given["context"]) for tool, given in fake.received] == [
        ("library_search", CONTEXT[LIBRARY]), ("trackers_latest", CONTEXT[TRACKERS]),
        ("propfirms_simulate", CONTEXT[PROPFIRMS])]
    assert fake.received[0][1]["query"] == "supertrend"


def test_arguments_are_checked_against_the_tools_schema_before_anything_is_sent():
    fake = FakeLuxAlgo()
    lux = client(fake)
    with pytest.raises(LuxAlgoRefused, match="'query' is a required property"):
        asyncio.run(lux.call("library_search", {}))
    with pytest.raises(LuxAlgoRefused, match="library_search"):
        asyncio.run(lux.call("library_search", {"query": "x", "limit": "ten"}))
    with pytest.raises(LuxAlgoRefused, match="a slug is lowercase"):
        asyncio.run(lux.call("library_get_source_code", {"slug": "../../account"}))
    assert fake.received == []


def test_an_answer_is_kept_an_hour_and_asked_again_after():
    fake = FakeLuxAlgo()
    now = [1_000_000.0]
    lux = client(fake, clock=lambda: now[0])

    async def calls():
        first = await lux.call("library_search", {"query": "vwap"})
        assert await lux.call("library_search", {"query": "vwap"}) == first
        now[0] += luxalgo.KEPT_SECONDS + 1
        await lux.call("library_search", {"query": "vwap"})

    asyncio.run(calls())
    assert len(fake.received) == 2


def test_an_answer_kept_signed_out_is_not_given_signed_in():
    fake = FakeLuxAlgo()
    token = [None]

    async def bearer():
        return token[0]

    lux = client(fake, bearer=bearer)

    async def calls():
        await lux.call("library_search", {"query": "vwap"})
        token[0] = "an-access-token"
        await lux.call("library_search", {"query": "vwap"})

    asyncio.run(calls())
    assert len(fake.received) == 2


def test_a_sign_in_that_cannot_be_read_leaves_the_reads_signed_out():
    fake = FakeLuxAlgo()

    async def bearer():
        raise RuntimeError("the vault is down")

    assert json.loads(asyncio.run(client(fake, bearer=bearer).call("library_search", {"query": "x"})))["results"]


def test_the_admin_asks_luxalgo_so_many_times_an_hour_at_most(monkeypatch):
    monkeypatch.setattr(luxalgo, "CALLS_AN_HOUR", 2)
    fake = FakeLuxAlgo()
    now = [1_000_000.0]
    lux = client(fake, clock=lambda: now[0])

    async def calls():
        await lux.call("library_search", {"query": "a"})
        await lux.call("library_search", {"query": "b"})
        # Kept: not asked, not counted.
        await lux.call("library_search", {"query": "a"})
        with pytest.raises(LuxAlgoRefused, match="2 times this hour"):
            await lux.call("library_search", {"query": "c"})
        now[0] += 3601
        await lux.call("library_search", {"query": "c"})

    asyncio.run(calls())
    assert [given["query"] for _, given in fake.received] == ["a", "b", "c"]


def test_a_refusal_of_luxalgo_and_a_call_too_long_are_said(monkeypatch):
    with pytest.raises(LuxAlgoRefused, match="LuxAlgo refused library_get_source_code: .*No indicator refused"):
        asyncio.run(client(FakeLuxAlgo()).call("library_get_source_code", {"slug": "refused"}))
    # LuxAlgo's way of saying it has none, told apart: the pages answer it with their 404.
    with pytest.raises(LuxAlgoUnknown, match="Unknown indicator slug 'unknown'"):
        asyncio.run(client(FakeLuxAlgo()).call("library_get_source_code", {"slug": "unknown"}))
    monkeypatch.setattr(luxalgo, "CALL_SECONDS", 0.2)
    with pytest.raises(LuxAlgoRefused, match="LuxAlgo did not answer"):
        asyncio.run(client(FakeLuxAlgo(slow=2.0)).call("library_search", {"query": "x"}))
    # LuxAlgo's longer screens have their own limit.
    monkeypatch.setattr(luxalgo, "SLOW_CALLS", {"library_search": 5.0})
    assert "made-up" in asyncio.run(client(FakeLuxAlgo(slow=0.5)).call("library_search", {"query": "x"}))


def test_an_answer_is_read_as_json_or_as_text():
    lux = client(FakeLuxAlgo())
    assert asyncio.run(lux.read("library_search", {"query": "x"}))["results"][0]["slug"] == "made-up"
    assert asyncio.run(lux.read("trackers_latest", {"dataset": "congress-trades"})) == {"text": "Not JSON: 3 rows"}


def test_a_source_is_taken_whole_with_its_licence_or_not_at_all():
    whole = asyncio.run(client(FakeLuxAlgo()).source("made-up"))
    assert whole["source"].startswith("// This work is licensed") and "\r" not in whole["source"]
    assert (whole["licence"], whole["owner"], whole["name"]) == ("CC BY-NC-SA 4.0", "LuxAlgo", "Made up")
    assert whole["chart_url"].startswith("https://vela.luxalgo.com/")
    with pytest.raises(LuxAlgoRefused, match="LuxAlgo gives 3 of 201 lines of Made up .*sign in to LuxAlgo"):
        asyncio.run(client(FakeLuxAlgo(whole=False)).source("made-up"))


def test_licences_are_read_from_a_sources_opening_comments():
    assert licence_of(HEADER) == {"licence": "CC BY-NC-SA 4.0", "owner": "LuxAlgo"}
    mozilla = ("// This source code is subject to the terms of the Mozilla Public License 2.0 at "
               "https://mozilla.org/MPL/2.0/\n// © LuxAlgo\n//@version=5\nindicator('x')")
    assert licence_of(mozilla) == {"licence": "Mozilla Public License 2.0", "owner": "LuxAlgo"}
    assert licence_of("//@version=6\nindicator('x')") == {"licence": "see LuxAlgo's NOTICE", "owner": None}
    # A licence named lower in the code is not its header's.
    assert licence_of("//@version=6\n" + "x = 1\n" * 30 + "// licensed under MIT")["licence"] == "see LuxAlgo's NOTICE"


class _Recorded:
    """An ASGI middleware that keeps the Authorization header of each request."""

    def __init__(self, app):
        self.app = app
        self.headers: list[str | None] = []

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            self.headers.append(dict(scope["headers"]).get(b"authorization", b"").decode() or None)
        await self.app(scope, receive, send)


def test_over_the_wire_the_account_token_is_sent_once_signed_in(monkeypatch):
    import uvicorn
    from mcp.server.transport_security import TransportSecuritySettings

    fake = FakeLuxAlgo()
    app = _Recorded(fake.server.streamable_http_app(
        json_response=True, stateless_http=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False)))
    with socket.socket() as free:
        free.bind(("127.0.0.1", 0))
        port = free.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        monkeypatch.setattr(luxalgo, "LUXALGO_URL", f"http://127.0.0.1:{port}/mcp")
        token = [None]

        async def bearer():
            return token[0]

        lux = LuxAlgo(bearer=bearer)

        async def calls():
            await lux.call("library_search", {"query": "vwap"})
            token[0] = "an-access-token"
            await lux.call("library_search", {"query": "rsi"})

        asyncio.run(calls())
    finally:
        server.should_exit = True
        thread.join(timeout=10)
    assert [given["query"] for _, given in fake.received] == ["vwap", "rsi"]
    assert None in app.headers and "Bearer an-access-token" in app.headers
    assert {header for header in app.headers} <= {None, "Bearer an-access-token"}
