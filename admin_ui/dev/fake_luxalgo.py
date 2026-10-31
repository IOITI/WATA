"""
A LuxAlgo made up for the development admin (FAKE_LUXALGO=1): its MCP server's
tools as the admin's client (src/admin/luxalgo.py) reads them, served in memory,
over a small Library of indicators written for WATA's development server (not
LuxAlgo's code: their header says so), a few concepts, trackers' rows and
prop-firm figures. Signed out, a source comes cut to its first lines, as
LuxAlgo gives it; the sign-in comes straight back to the admin, as if the
operator had said yes on LuxAlgo's page. Nothing reaches LuxAlgo.
"""

import json
import secrets
import time
from urllib.parse import urlencode

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from src.admin.errors import InvalidRequest
from src.admin.luxalgo_auth import CALLBACK, ISSUER

HEADER = ("// This work is licensed under a Attribution-NonCommercial-ShareAlike 4.0 International (CC BY-NC-SA 4.0) "
          "https://creativecommons.org/licenses/by-nc-sa/4.0/\n"
          "// © LuxAlgo (made up for WATA's development admin: not LuxAlgo's code)\n\n")
SHOWN_SIGNED_OUT = 8
FAMILIES = [
    ("trend", "Trend", 104), ("momentum", "Momentum", 85), ("volatility", "Volatility", 61),
    ("volume-orderflow", "Volume & Flow", 91), ("market-structure", "Structure", 33), ("smc-ict", "SMC / ICT", 55),
    ("wyckoff", "Wyckoff", 18), ("elliott-harmonics", "Elliott & Harmonics", 33), ("patterns", "Patterns", 88),
    ("levels", "Levels", 40), ("statistics", "Statistics", 37), ("machine-learning", "Machine Learning", 21),
    ("time-seasonality", "Time & Seasonality", 19), ("sentiment-breadth", "Sentiment & Breadth", 16),
    ("risk-exits", "Risk & Exits", 11), ("meta-composition", "Meta & Composition", 23), ("validation", "Validation", 2),
]
INDICATORS = [
    {
        "slug": "trend-ribbon-made-up", "name": "Trend Ribbon (made up)", "family": "trend", "date": "2026-10-27",
        "concept": "ema-crossover",
        "description": "Two exponential averages and the ribbon between them: an alert when the fast one crosses the slow "
                       "one, long above, short below.",
        "code": """//@version=6
indicator("Trend Ribbon (made up)", overlay = true)
fastLength = input.int(9, "Fast length", minval = 1)
slowLength = input.int(21, "Slow length", minval = 2)
fast = ta.ema(close, fastLength)
slow = ta.ema(close, slowLength)
plot(fast, "Fast", color = color.teal)
plot(slow, "Slow", color = color.orange)
alertcondition(ta.crossover(fast, slow), "Long", "The fast average crossed above the slow one")
alertcondition(ta.crossunder(fast, slow), "Short", "The fast average crossed below the slow one")
""",
    },
    {
        "slug": "momentum-pulse-made-up", "name": "Momentum Pulse (made up)", "family": "momentum", "date": "2026-10-20",
        "concept": "rsi",
        "description": "A relative strength index with two thresholds: an alert as it leaves the oversold zone upward "
                       "or the overbought zone downward.",
        "code": """//@version=6
indicator("Momentum Pulse (made up)")
length = input.int(14, "RSI length", minval = 2)
low_level = input.float(30, "Oversold")
high_level = input.float(70, "Overbought")
value = ta.rsi(close, length)
plot(value, "RSI", color = color.purple)
hline(low_level, "Oversold")
hline(high_level, "Overbought")
alertcondition(ta.crossover(value, low_level), "Long", "RSI left the oversold zone")
alertcondition(ta.crossunder(value, high_level), "Short", "RSI left the overbought zone")
""",
    },
    {
        "slug": "squeeze-dots-made-up", "name": "Squeeze Dots (made up)", "family": "volatility", "date": "2026-10-09",
        "concept": "bollinger-squeeze",
        "description": "Bollinger bands inside Keltner channels mark a squeeze; its release in the direction of the "
                       "close against the basis raises an alert.",
        "code": """//@version=6
indicator("Squeeze Dots (made up)")
length = input.int(20, "Length", minval = 2)
basis = ta.sma(close, length)
deviation = 2.0 * ta.stdev(close, length)
range_ = 1.5 * ta.atr(length)
squeezed = basis + deviation < basis + range_
released = squeezed[1] and not squeezed
plot(close - basis, "Momentum", style = plot.style_columns, color = close > basis ? color.teal : color.red)
plotshape(squeezed, "Squeeze", shape.circle, location.bottom, color.gray)
alertcondition(released and close > basis, "Long", "The squeeze released upward")
alertcondition(released and close < basis, "Short", "The squeeze released downward")
""",
    },
    {
        "slug": "session-vwap-bands-made-up", "name": "Session VWAP Bands (made up)", "family": "volume-orderflow",
        "date": "2026-09-30", "concept": "vwap",
        "description": "The session's volume-weighted average price with bands one and two deviations away: an alert "
                       "when the price comes back inside from the outer band.",
        "code": """//@version=6
indicator("Session VWAP Bands (made up)", overlay = true)
[middle, upper, lower] = ta.vwap(hlc3, timeframe.change("D"), 2.0)
plot(middle, "VWAP", color = color.blue)
plot(upper, "Upper", color = color.gray)
plot(lower, "Lower", color = color.gray)
alertcondition(ta.crossover(close, lower), "Long", "Back above the lower band")
alertcondition(ta.crossunder(close, upper), "Short", "Back below the upper band")
alertcondition(ta.cross(close, middle), "Close", "Crossed the session's VWAP")
""",
    },
    {
        "slug": "swing-structure-breaks-made-up", "name": "Swing Structure Breaks (made up)",
        "family": "market-structure", "date": "2026-09-14", "concept": "break-of-structure",
        "description": "Swing highs and lows found by pivots, and a break of the last one by a close: long through a "
                       "high, short through a low.",
        "code": """//@version=6
indicator("Swing Structure Breaks (made up)", overlay = true)
length = input.int(5, "Pivot length", minval = 1)
var float last_high = na
var float last_low = na
found_high = ta.pivothigh(high, length, length)
found_low = ta.pivotlow(low, length, length)
last_high := na(found_high) ? last_high : found_high
last_low := na(found_low) ? last_low : found_low
plot(last_high, "Swing high", color = color.green, style = plot.style_stepline)
plot(last_low, "Swing low", color = color.red, style = plot.style_stepline)
alertcondition(ta.crossover(close, last_high), "Long", "A close broke the last swing high")
alertcondition(ta.crossunder(close, last_low), "Short", "A close broke the last swing low")
""",
    },
    {
        "slug": "opening-range-box-made-up", "name": "Opening Range Box (made up)", "family": "levels",
        "date": "2026-08-28", "concept": "opening-range",
        "description": "The high and low of the session's first half hour, and a close out of them afterwards.",
        "code": """//@version=6
indicator("Opening Range Box (made up)", overlay = true)
minutes = input.int(30, "Minutes of the range", minval = 1)
var float top = na
var float bottom = na
var int started = na
if timeframe.change("D")
    top := high
    bottom := low
    started := time
in_range = time - started < minutes * 60 * 1000
if in_range
    top := math.max(top, high)
    bottom := math.min(bottom, low)
plot(top, "Range high", color = color.teal)
plot(bottom, "Range low", color = color.red)
alertcondition(not in_range and ta.crossover(close, top), "Long", "A close above the opening range")
alertcondition(not in_range and ta.crossunder(close, bottom), "Short", "A close below the opening range")
""",
    },
]
CONCEPTS = {
    "ema-crossover": ("EMA crossover", "trend", "Two exponential moving averages of different lengths: the fast one "
                      "crossing the slow one is read as a change of trend."),
    "rsi": ("Relative strength index", "momentum", "The share of the recent gains in the recent moves, from 0 to 100, "
            "read against two thresholds."),
    "bollinger-squeeze": ("Bollinger squeeze", "volatility", "Bands of standard deviations narrower than an average "
                          "range: a quiet market before a move."),
    "vwap": ("VWAP", "volume-orderflow", "The average price of a session weighted by its volume."),
    "break-of-structure": ("Break of structure", "market-structure", "A close through the last swing high or low."),
    "opening-range": ("Opening range", "levels", "The high and low of a session's first minutes."),
}
DATASETS = {
    "congress-trades": [
        {"date": "2026-10-28", "ticker": "NVDA", "member": "A made-up representative", "type": "purchase",
         "amount": "$15,001 - $50,000"},
        {"date": "2026-10-27", "ticker": "MSFT", "member": "A made-up senator", "type": "sale",
         "amount": "$1,001 - $15,000"},
    ],
    "insider-transactions": [
        {"date": "2026-10-29", "ticker": "AAPL", "insider": "A made-up officer", "type": "sale", "shares": 12000},
    ],
    "fed-communications": [
        {"date": "2026-10-28", "kind": "speech", "title": "A made-up speech on the outlook"},
    ],
}
FIRMS = [
    {"firmId": "example-funding", "name": "Example Funding (made up)", "challenges": [
        {"challengeId": "example-50k", "name": "50K, two steps", "accountSize": 50000, "price": 345}]},
    {"firmId": "sample-capital", "name": "Sample Capital (made up)", "challenges": [
        {"challengeId": "sample-100k", "name": "100K, one step", "accountSize": 100000, "price": 520}]},
]


class FakeSignIn:
    """The admin's sign-in to LuxAlgo, made up: LuxAlgo's page is never shown, the browser comes straight back."""

    def __init__(self):
        self._states: dict[str, float] = {}
        self.signed_in_at: float | None = None
        self.refused = None

    @property
    def signed_in(self) -> bool:
        return self.signed_in_at is not None

    async def status(self) -> dict:
        if not self.signed_in:
            return {"signed_in": False, "account": None, "expires_at": None, "refused": None}
        return {"signed_in": True, "account": "operator@example.com (made up)",
                "expires_at": int(self.signed_in_at + 3600), "refused": None}

    async def begin(self, origin: str) -> str:
        state = secrets.token_urlsafe(24)
        self._states[state] = time.time()
        return f"{origin}{CALLBACK}?" + urlencode({"code": "made-up-code-0001", "state": state, "iss": ISSUER})

    async def finish(self, code: str, state: str, by, issuer=None) -> dict:
        if self._states.pop(state or "", None) is None:
            raise InvalidRequest("This sign-in is unknown, used, or older than 10 minutes: sign in again")
        self.signed_in_at = time.time()
        print("[fake] LuxAlgo: signed in (made up)")
        return await self.status()

    async def hand_over(self, address: str, by) -> dict:
        from urllib.parse import parse_qs, urlsplit

        query = parse_qs(urlsplit(address).query)
        return await self.finish((query.get("code") or [""])[0], (query.get("state") or [""])[0], by)

    async def sign_out(self, by) -> dict:
        self.signed_in_at = None
        return await self.status()

    async def bearer(self) -> str | None:
        return "made-up-access-token" if self.signed_in else None


def _indicator(slug: str) -> dict:
    found = next((entry for entry in INDICATORS if entry["slug"] == slug), None)
    if found is None:
        raise ToolError(json.dumps({"error": f"Unknown indicator slug {slug!r}."}))
    return found


def _url(kind: str, slug: str) -> str:
    return f"https://www.luxalgo.com/library/{kind}/{slug}/"


def _listed(entry: dict) -> dict:
    return {"slug": entry["slug"], "name": entry["name"], "family": entry["family"],
            "description": entry["description"], "date_displayed": entry["date"], "url": _url("indicator", entry["slug"])}


def server(sign_in: FakeSignIn) -> MCPServer:
    made = MCPServer(name="luxalgo", version="2.1.0 (made up)", log_level="WARNING", subscriptions=False)

    @made.tool(name="library_list_families")
    async def library_list_families(context: str) -> str:
        """The Library's top-level taxonomy: its families of trading concepts."""
        return json.dumps({"families": [{"key": key, "name": name, "concept_count": count, "url": _url("family", key)}
                                        for key, name, count in FAMILIES]})

    @made.tool(name="library_list_indicators")
    async def library_list_indicators(context: str, family: str = "", text: str = "", concept: str = "",
                                      sort: str = "date", direction: str = "desc", page: int = 0,
                                      page_size: int = 24) -> str:
        """Browse the indicator catalog with filters and sorting, a page at a time."""
        chosen = [entry for entry in INDICATORS
                  if (not family or entry["family"] == family) and (not concept or entry["concept"] == concept)
                  and (not text or text.lower() in (entry["name"] + " " + entry["description"]).lower())]
        key = {"name": "name", "family": "family"}.get(sort, "date")
        chosen.sort(key=lambda entry: entry[key], reverse=direction == "desc")
        return json.dumps({"total": len(chosen), "page": page, "page_size": page_size,
                           "indicators": [_listed(entry) for entry in chosen[page * page_size:(page + 1) * page_size]]})

    @made.tool(name="library_search")
    async def library_search(query: str, context: str, type: str = "all", family: str = "", limit: int = 10) -> str:
        """Search the Library's concepts and indicators."""
        words = query.lower().split()
        results = []
        if type in ("all", "concepts"):
            results += [{"kind": "concept", "slug": slug, "name": name, "family": concept_family,
                         "url": _url("concept", slug)}
                        for slug, (name, concept_family, text) in CONCEPTS.items()
                        if all(word in f"{slug} {name} {text}".lower() for word in words)
                        and (not family or concept_family == family)]
        if type in ("all", "indicators"):
            results += [{"kind": "indicator", **_listed(entry)} for entry in INDICATORS
                        if all(word in f"{entry['slug']} {entry['name']} {entry['description']}".lower() for word in words)
                        and (not family or entry["family"] == family)]
        return json.dumps({"results": results[:limit]})

    @made.tool(name="library_get_indicator")
    async def library_get_indicator(slug: str, context: str) -> str:
        """Details for one indicator: what it does, how to read it, its family and concepts."""
        entry = _indicator(slug)
        name = CONCEPTS[entry["concept"]][0]
        body = (f"**{entry['name']}** {entry['description'][0].lower()}{entry['description'][1:]}\n\n"
                f"## How to read it\n\n- **Long**: its long alert.\n- **Short**: its short alert.\n\n"
                f"Made up for WATA's development admin, around [{name}](/library/concept/{entry['concept']}/).")
        return json.dumps({"slug": slug, "name": entry["name"], "author": "LuxAlgo", "family": entry["family"],
                           "body_markdown": body, "date_displayed": entry["date"], "url": _url("indicator", slug),
                           "concepts": [{"slug": entry["concept"], "name": name, "primary": True}],
                           "code": {"available": True}})

    @made.tool(name="library_get_source_code")
    async def library_get_source_code(slug: str, context: str) -> str:
        """The Pine Script source of a Library indicator."""
        entry = _indicator(slug)
        source = HEADER + entry["code"]
        lines = source.splitlines()
        chart = f"https://vela.luxalgo.com/chart?pineScriptCodeId={slug}"
        if sign_in.signed_in:
            return json.dumps({"slug": slug, "name": entry["name"], "available": True, "complete": True,
                               "source": source.replace("\n", "\r\n"), "chart_url": chart})
        return json.dumps({"slug": slug, "name": entry["name"], "available": True, "complete": False,
                           "source": "\r\n".join(lines[:SHOWN_SIGNED_OUT]), "shown_lines": SHOWN_SIGNED_OUT,
                           "total_lines": len(lines), "reason": "sign-in-for-full-source", "chart_url": chart})

    @made.tool(name="library_get_concept")
    async def library_get_concept(slug: str, context: str) -> str:
        """Explain a trading concept: its write-up and its indicators."""
        if slug not in CONCEPTS:
            raise ToolError(json.dumps({"error": f"Unknown concept slug {slug!r}."}))
        name, family, text = CONCEPTS[slug]
        carried = ", ".join(f"[{entry['name']}](/library/indicator/{entry['slug']}/)" for entry in INDICATORS
                            if entry["concept"] == slug)
        return json.dumps({"slug": slug, "name": name, "family": family, "url": _url("concept", slug),
                           "content_markdown": f"# {name}\n\n**{name}**: {text}\n\n## Indicators\n\n{carried}\n\n"
                                               "Made up for WATA's development admin."})

    @made.tool(name="trackers_datasets")
    async def trackers_datasets(context: str, dataset: str = "") -> str:
        """The Market Trackers catalog: datasets of US public-record market data."""
        return json.dumps({"datasets": [{"dataset": key, "rows": len(rows)} for key, rows in DATASETS.items()
                                        if not dataset or key == dataset]})

    @made.tool(name="trackers_latest")
    async def trackers_latest(dataset: str, context: str, ticker: str = "", limit: int = 20) -> str:
        """What the last daily publish added to one dataset."""
        rows = [row for row in DATASETS.get(dataset, []) if not ticker or row.get("ticker") == ticker.upper()]
        return json.dumps({"dataset": dataset, "rows": rows[:limit], "note": "Made up for WATA's development admin."})

    @made.tool(name="propfirms_list_simulatable")
    async def propfirms_list_simulatable(context: str, productType: str = "futures") -> str:
        """The prop firms and their simulatable challenges."""
        return json.dumps({"firms": FIRMS})

    @made.tool(name="propfirms_simulate")
    async def propfirms_simulate(winRate: float, avgWinR: float, tradesPerDay: float, riskValue: float, context: str,
                                 firmId: str = "example-funding", challengeId: str = "example-50k",
                                 avgLossR: float = 1.0) -> str:
        """Simulate a trader's statistics through a challenge (made up: a formula, not a Monte Carlo)."""
        edge = winRate * avgWinR - (1 - winRate) * avgLossR
        passing = max(0.02, min(0.95, 0.35 + edge * 0.8 - riskValue * 0.05))
        return json.dumps({"firmId": firmId, "challengeId": challengeId, "passProbability": round(passing, 3),
                           "expectancyR": round(edge, 3), "note": "Made up for WATA's development admin."})

    @made.tool(name="propfirms_validate_strategy")
    async def propfirms_validate_strategy(tradesPerDay: float, riskValue: float, context: str,
                                          rSeries: list[float] | None = None, riskMode: str = "percent-of-balance",
                                          productType: str = "futures", minPassPerAttempt: float = 0.5,
                                          accountSizeMin: float | None = None,
                                          accountSizeMax: float | None = None) -> str:
        """Which challenges a strategy's R-multiples would pass (made up: a formula, not a bootstrap)."""
        series = rSeries or []
        mean = sum(series) / len(series) if series else 0.0
        screened = []
        for firm in FIRMS:
            for challenge in firm["challenges"]:
                size = challenge["accountSize"]
                if (accountSizeMin is not None and size < accountSizeMin) or (
                        accountSizeMax is not None and size > accountSizeMax):
                    continue
                passing = max(0.02, min(0.95, 0.45 + mean * 0.5 - riskValue * 0.08 - size / 1_000_000))
                attempts = round(1 / passing, 2)
                cost = round(attempts * challenge["price"], 2)
                screened.append({
                    "firmId": firm["firmId"], "firmName": firm["name"], "challengeId": challenge["challengeId"],
                    "name": f"{firm['name']} - {challenge['name']}", "productType": productType,
                    "accountSize": size, "currency": "usd", "price": challenge["price"],
                    "passPerAttempt": round(passing, 3), "passCiLow": round(max(0.0, passing - 0.03), 3),
                    "passCiHigh": round(min(1.0, passing + 0.03), 3), "fundedProbability": round(passing * 0.7, 3),
                    "expectedAttempts": attempts, "expectedCost": cost,
                    "evTotal": round(size * 0.02 * passing - cost, 2), "pEvPositive": round(passing, 3),
                    "meetsBar": passing >= minPassPerAttempt, "flagIds": ["attempts-iid"], "inferredFields": []})
        return json.dumps({
            "bar": {"minPassPerAttempt": minPassPerAttempt, "requirePositiveEv": False}, "profileKind": "bootstrap",
            "passing": [entry for entry in screened if entry["meetsBar"]],
            "belowBar": [entry for entry in screened if not entry["meetsBar"]], "excludedNotSimulatable": 1,
            "note": "Made up for WATA's development admin: a formula, not a bootstrap.",
            "meta": {"seed": 42, "paths": 0}, "disclaimer": "Simulation, not prediction."})

    return made
