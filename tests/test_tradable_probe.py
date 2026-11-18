"""
`wataprices tradable` (src/price_recorder/tradable_probe.py): what Saxo answers
about the turbos, asked without trading. The streaming half runs on a WebSocket
server of this machine, the REST half on a fake client: nothing reaches Saxo.
"""

import asyncio
import gzip
import json
import random
import struct
import sys
import types
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import httpx
import pytest
import websockets

asyncpg_stub = types.ModuleType("asyncpg")
asyncpg_stub.Pool = type("Pool", (), {})
asyncpg_stub.Record = dict
asyncpg_stub.create_pool = AsyncMock()
sys.modules.setdefault("asyncpg", asyncpg_stub)

import src.saxo_openapi.endpoints.portfolio as pf  # noqa: E402
import src.saxo_openapi.endpoints.referencedata as rd  # noqa: E402
import src.saxo_openapi.endpoints.rootservices as rs  # noqa: E402
import src.saxo_openapi.endpoints.trading as tr  # noqa: E402
from src.price_recorder import tradable_probe as probe_module  # noqa: E402
from src.price_recorder.tradable_probe import (  # noqa: E402
    GuardedClient,
    NotAllowed,
    Observation,
    apart,
    asks_each_second,
    changes,
    check_allowed,
    chosen_turbos,
    feed_figures,
    lead,
    order_answers,
    outside_turbo_hours,
    parse_arguments,
    probe,
    report,
    write_series,
)

UIC = 61025835
TURBO = "WarrantOpenEndKnockOut"


def _run(scenario):
    return asyncio.run(asyncio.wait_for(scenario(), timeout=15))


# ── Nothing but what it may ask ───────────────────────────────────


@pytest.mark.parametrize("endpoint", [
    tr.orders.Order(data={"Uic": UIC, "BuySell": "Buy", "OrderType": "Market"}),
    tr.orders.ChangeOrder(data={"OrderId": "1"}),
    tr.orders.CancelOrders(OrderIds="1", params={"AccountKey": "k"}),
    tr.orders.PrecheckOrder(data={"Uic": UIC}),
    rs.sessions.PatchSessionCapabilities({"TradeLevel": "FullTradingAndChat"}),
    rs.sessions.ChangeSessionCapabilities({"TradeLevel": "FullTradingAndChat"}),
    pf.positions.PositionsMe(params={}),
], ids=lambda endpoint: f"{endpoint.method} {endpoint}")
def test_an_order_a_change_of_the_session_or_anything_else_is_refused_before_it_leaves(endpoint):
    inner = AsyncMock()

    with pytest.raises(NotAllowed):
        _run(lambda: GuardedClient(inner).request(endpoint))
    inner.request.assert_not_awaited()


def test_what_the_probe_asks_is_allowed():
    for endpoint in (
        rs.sessions.GetSessionCapabilities(),
        pf.accounts.AccountsMe(),
        rd.instruments.Instruments(params={}),
        rd.instruments.InstrumentsDetails(params={}),
        tr.infoprices.InfoPrices(params={}),
        tr.infoprices.CreateInfoPriceSubscription(data={}),
        tr.infoprices.RemoveInfoPriceSubscriptionById(ContextId="probe-0a1b2c3d4e5f", ReferenceId="info-1"),
        tr.prices.CreatePriceSubscription(data={}),
        tr.prices.PriceSubscriptionRemove(ContextId="probe-0a1b2c3d4e5f", ReferenceId=f"px-{UIC}"),
    ):
        check_allowed(endpoint.method, str(endpoint))
    # Removing every subscription of a context at once is not among them: the probe removes its own, one by one.
    with pytest.raises(NotAllowed):
        endpoint = tr.prices.PriceSubscriptionRemoveByTag(ContextId="probe-0a1b2c3d4e5f")
        check_allowed(endpoint.method, str(endpoint))


# ── The figures ───────────────────────────────────────────────────


def _point(at, ask, feed="tradable", bid=None, uic=UIC):
    return Observation(at=at, feed=feed, uic=uic, bid=bid if bid is not None else (None if ask is None else ask - 0.01),
                       ask=ask, price_type_bid="Tradable", price_type_ask="Tradable", market_state="Open",
                       delayed_minutes=0, last_updated=None)


def _walk(steps: int, seed: int = 7) -> list[float]:
    """A price that moves at each step, never back to the one before, and repeats no pattern."""
    chance = random.Random(seed)
    prices = [10.0]
    for _ in range(steps - 1):
        prices.append(round(prices[-1] + chance.choice((0.01, 0.02, 0.03, -0.01, -0.02)), 2))
    return prices


def test_a_change_is_a_quote_other_than_the_one_before():
    points = [_point(0, 10.0), _point(1, 10.0), _point(2, None), _point(3, 10.1), _point(4, 10.1), _point(6, 10.0)]

    assert changes(points) == [3, 6]
    figures = feed_figures(points)
    assert figures["messages"] == 6
    assert figures["changes"] == 2
    assert figures["seconds_between_changes"] == {"median": 3.0, "p90": 3.0, "max": 3.0}
    assert figures["price_types"] == ["Tradable/Tradable"]


def test_an_infoprice_that_shows_the_tradable_price_twenty_seconds_late_is_said_to_trail_it_by_twenty():
    seconds = 300
    prices = _walk(seconds // 2 + 1)
    tradable = [_point(t, prices[t // 2]) for t in range(0, seconds + 1, 2)]
    info = [_point(t + 20, prices[t // 2], feed="info") for t in range(0, seconds - 20 + 1, 2)]

    tradable_asks, info_asks = asks_each_second(tradable, seconds), asks_each_second(info, seconds)
    found = lead(info_asks, tradable_asks)

    assert found["seconds"] == 20
    assert found["mean_percent_then"] == 0
    assert found["mean_percent_at_once"] > 0
    assert apart(info_asks, tradable_asks)["percent"]["max"] > 0


def test_an_infoprice_sampled_every_thirty_seconds_changes_fifteen_times_less_and_trails_by_half_of_it():
    seconds = 600
    prices = _walk(seconds // 2 + 1)
    tradable = [_point(t, prices[t // 2]) for t in range(0, seconds + 1, 2)]
    info = [_point(t, prices[t // 2], feed="info") for t in range(0, seconds + 1, 30)]

    assert feed_figures(info)["seconds_between_changes"]["median"] == 30
    assert feed_figures(tradable)["seconds_between_changes"]["median"] == 2
    found = lead(asks_each_second(info, seconds), asks_each_second(tradable, seconds))
    # A quote held 30 s is, on average, half of that behind the price it was taken from.
    assert 5 <= found["seconds"] <= 25


def test_the_asks_standing_each_second_are_the_last_received():
    points = [_point(0.4, 10.0), _point(2.5, 10.2), _point(2.9, None), _point(4.0, 10.3)]

    assert asks_each_second(points, 5) == [None, 10.0, 10.0, 10.2, 10.3, 10.3]


def test_two_feeds_that_never_stand_together_say_nothing():
    assert apart([None, None], [10.0, 10.0]) is None
    assert lead([None] * 100, [10.0] * 100) is None


# ── What Saxo says of a turbo's orders ─────────────────────────────


def test_the_durations_saxo_gives_answer_the_questions():
    details = {
        "SupportedOrderTypes": ["Market", "Limit", "Stop"],
        "SupportedOrderTypeSettings": [
            {"OrderType": "Market", "DurationTypes": ["DayOrder"]},
            {"OrderType": "Limit", "DurationTypes": ["DayOrder", "GoodTillCancel", "ImmediateOrCancel"]},
        ],
    }

    assert order_answers(details) == {
        "Market with ImmediateOrCancel": False,
        "Market with FillOrKill": False,
        "Limit with ImmediateOrCancel": True,
        "Switch (sell one, buy another, in one order)": False,
    }


def test_what_saxo_does_not_say_is_not_guessed():
    assert set(order_answers({}).values()) == {None}
    types_alone = order_answers({"SupportedOrderTypes": ["Market", "Limit"]})
    assert types_alone["Market with ImmediateOrCancel"] is None
    assert types_alone["Switch (sell one, buy another, in one order)"] is False


def test_the_turbos_hours_are_those_saxo_quotes_them():
    paris_summer = datetime(2026, 7, 15, 6, 0, tzinfo=timezone.utc)   # 08:00 in Paris, a Wednesday
    assert not outside_turbo_hours(paris_summer)
    assert outside_turbo_hours(paris_summer.replace(hour=5, minute=59))
    assert not outside_turbo_hours(datetime(2026, 11, 18, 20, 59, tzinfo=timezone.utc))
    assert outside_turbo_hours(datetime(2026, 11, 18, 21, 0, tzinfo=timezone.utc))
    assert outside_turbo_hours(datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc))   # a Saturday


def test_the_command_line():
    arguments = parse_arguments(["--uic", "1", "--uic", "2", "--minutes", "0"])
    assert arguments.uic == [1, 2]
    assert arguments.minutes == 0
    assert parse_arguments([]).uic is None
    with pytest.raises(SystemExit):
        parse_arguments(["--minutes", "31"])


# ── A whole run, on this machine ──────────────────────────────────


def _frame(message_id: int, reference_id: str, payload) -> bytes:
    """A message as Saxo sends it (what ``decode_ws_msg`` reads back)."""
    reference = reference_id.encode()
    body = json.dumps(payload).encode()
    return (
        struct.pack("Q", message_id) + b"\x00\x00"
        + struct.pack("B", len(reference)) + reference
        + struct.pack("B", 0) + struct.pack("i", len(body)) + body
    )


class LocalSaxoStream:
    """A WebSocket server on this machine: ``script`` is run for each connection."""

    def __init__(self, script):
        self._script = script
        self.asked = []

    async def __aenter__(self):
        self._server = await websockets.serve(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *_):
        self._server.close()
        await self._server.wait_closed()

    async def _handle(self, connection):
        self.asked.append(connection.request.path)
        await self._script(connection)


class FakeSaxo:
    """Saxo's gateway as the probe sees it: what it was asked, and its answers."""

    def __init__(self, refuse_tradable=False):
        self.asked = []
        self._refuse_tradable = refuse_tradable
        self.subscribed = asyncio.Event()

    async def request(self, endpoint):
        self.asked.append((endpoint.method, str(endpoint), getattr(endpoint, "data", None)))
        name = type(endpoint).__name__
        if name == "GetSessionCapabilities":
            return {"TradeLevel": "OrdersOnly", "DataLevel": "Premium"}
        if name == "InstrumentsDetails":
            return {"Data": [{
                "Uic": UIC, "AssetType": TURBO, "Description": "NASDAQ 100 TurboXL Long 29028.9327 Saxo",
                "Exchange": {"ExchangeId": "CATS_SAXO"}, "IsTradable": True,
                "SupportedOrderTypes": ["Market", "Limit"],
                "SupportedOrderTypeSettings": [{"OrderType": "Market", "DurationTypes": ["DayOrder"]},
                                               {"OrderType": "Limit", "DurationTypes": ["DayOrder", "ImmediateOrCancel"]}],
                "NotAboutOrders": "left out",
            }]}
        if name == "CreateInfoPriceSubscription":
            return {"Snapshot": {"Data": [{"Uic": UIC, "Quote": {"Bid": 4.60, "Ask": 4.61, "PriceTypeBid": "Indicative",
                                                                "PriceTypeAsk": "Indicative", "MarketState": "Open"}}]}}
        if name == "CreatePriceSubscription":
            if self._refuse_tradable:
                raise RuntimeError("Saxo said no")
            self.subscribed.set()
            return {"Snapshot": {"Uic": UIC, "Quote": {"Bid": 4.60, "Ask": 4.61, "PriceTypeBid": "Tradable",
                                                      "PriceTypeAsk": "Tradable", "MarketState": "Open"}}}
        return None

    def removed(self) -> list[str]:
        return [path for method, path, _ in self.asked if method == "DELETE"]


class Config:
    def get_config_value(self, path, default=None):
        return {"saxo_auth.env": "live", "trade.config.general.streaming": {"refresh_rate_ms": 1000}}.get(path, default)


def _gateway(request: httpx.Request) -> httpx.Response:
    assert request.url.path == "/openapi/root/v1/sessions/capabilities/"
    assert request.headers["Authorization"] == "Bearer token-1"
    return httpx.Response(200, json={"TradeLevel": "OrdersOnly"})


def _quotes(connection):
    """What Saxo streams: the tradable price twice, then the InfoPrice once, and a heartbeat."""
    async def script():
        await asyncio.sleep(0.05)
        await connection.send(_frame(1, f"px-{UIC}", {"Quote": {"Ask": 4.63, "Bid": 4.62}}))
        await connection.send(_frame(2, f"px-{UIC}", {"Quote": {"Ask": 4.65, "Bid": 4.64}}))
        await connection.send(_frame(3, "info-1", [{"Uic": UIC, "Quote": {"Ask": 4.65, "Bid": 4.64}}]))
        await connection.send(_frame(4, "_heartbeat", [{"OriginatingReferenceId": "info-1", "Reason": "NoNewData"}]))
        await connection.wait_closed()
    return script()


async def _probe(saxo: FakeSaxo, stream: LocalSaxoStream, seconds: float = 1.0):
    return await probe(
        GuardedClient(saxo), Config(), "account-key", token_getter=lambda: "token-1", uics=[UIC], seconds=seconds,
        address=f"ws://127.0.0.1:{stream.port}/oapi/streaming/ws/connect", round_trip_transport=httpx.MockTransport(_gateway),
    )


def test_a_run_asks_streams_compares_and_removes_its_subscriptions(tmp_path):
    saxo = FakeSaxo()

    async def scenario():
        async with LocalSaxoStream(_quotes) as stream:
            return await _probe(saxo, stream)

    found, observations = _run(scenario)

    assert found["session"] == {"trade_level": "OrdersOnly", "data_level": "Premium"}
    assert found["round_trip"]["statuses"] == [200]
    assert found["round_trip"]["http_versions"] == ["HTTP/1.1"]
    turbo = found["turbos"][0]
    assert turbo["asset_type"] == TURBO and "NotAboutOrders" not in turbo["details"]
    figures = found["figures"][UIC]
    assert figures["tradable"]["messages"] == 3 and figures["tradable"]["changes"] == 2
    assert figures["info"]["messages"] == 2 and figures["info"]["changes"] == 1
    assert figures["tradable"]["price_types"] == ["Tradable/Tradable"]
    assert found["heartbeats"] == 1
    # Asked on the probe's own context, as the services ask theirs, and removed one by one at the end.
    created = {type_name: data for _, path, data in saxo.asked
               for type_name in ("infoprices", "prices") if path == f"openapi/trade/v1/{type_name}/subscriptions"}
    context = created["prices"]["ContextId"]
    assert context.startswith("probe-") and created["infoprices"]["ContextId"] == context
    assert created["infoprices"]["RefreshRate"] == 1000 and created["prices"]["RefreshRate"] == 100
    assert created["prices"]["Arguments"]["FieldGroups"] == ["Quote"]
    assert sorted(saxo.removed()) == sorted([f"openapi/trade/v1/infoprices/subscriptions/{context}/info-1",
                                             f"openapi/trade/v1/prices/subscriptions/{context}/px-{UIC}"])

    said = report(found)
    assert "TradeLevel OrdersOnly" in said
    assert "Market with ImmediateOrCancel: not taken" in said
    assert "Limit with ImmediateOrCancel: taken" in said
    assert "tradable price: 3 messages, 2 changes" in said

    path = tmp_path / "series.jsonl.gz"
    write_series(str(path), found, observations)
    lines = [json.loads(line) for line in gzip.open(path, "rt").read().splitlines()]
    assert lines[0]["kind"] == "probe" and len(lines) == 1 + len(observations)
    assert {line["feed"] for line in lines[1:]} == {"info", "tradable"}


def test_a_tradable_price_saxo_refuses_is_said_and_the_infoprice_is_still_measured():
    saxo = FakeSaxo(refuse_tradable=True)

    async def scenario():
        async with LocalSaxoStream(_quotes) as stream:
            return await _probe(saxo, stream, seconds=0.5)

    found, _ = _run(scenario)

    refused = [note for note in found["notes"] if note.startswith("Saxo refused the tradable price")]
    assert len(refused) == 2          # at 100 ms, then at the services' 1000 ms
    assert found["figures"][UIC]["info"]["messages"] == 2
    assert found["figures"][UIC]["tradable"]["messages"] == 0
    assert saxo.removed() == [f"openapi/trade/v1/infoprices/subscriptions/{_context(saxo)}/info-1"]


def _context(saxo: FakeSaxo) -> str:
    return next(data["ContextId"] for _, path, data in saxo.asked if path.endswith("infoprices/subscriptions"))


def test_a_run_cut_short_still_removes_its_subscriptions():
    saxo = FakeSaxo()

    async def scenario():
        async with LocalSaxoStream(_quotes) as stream:
            running = asyncio.create_task(_probe(saxo, stream, seconds=60))
            await asyncio.wait_for(saxo.subscribed.wait(), 5)
            await asyncio.sleep(0.1)
            running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await running

    _run(scenario)

    assert len(saxo.removed()) == 2


def test_saxo_ending_the_subscriptions_ends_the_run_and_says_so():
    saxo = FakeSaxo()

    def reset(connection):
        async def script():
            await asyncio.sleep(0.05)
            await connection.send(_frame(1, "_resetsubscriptions", {"TargetReferenceIds": []}))
            await connection.wait_closed()
        return script()

    async def scenario():
        async with LocalSaxoStream(reset) as stream:
            return await _probe(saxo, stream, seconds=30)

    found, _ = _run(scenario)

    assert found["seconds"] < 5
    assert any("_resetsubscriptions" in note for note in found["notes"])
    assert len(saxo.removed()) == 2


def test_by_default_the_turbos_are_the_two_the_trader_would_buy_first_each_way(monkeypatch):
    import src.trade.async_services as services
    import src.watchlist_manager.manager as manager

    class Search:
        """The trader's own search, as the probe uses it: its candidates, and its price band."""
        turbo_price_range = {"min": 4, "max": 15}

        def __init__(self, api_client, config_manager, account_key):
            self.api_client = api_client

        async def build_watchlist_candidates(self, exchange_id, underlying_uics, keywords):
            await self.api_client.request(tr.infoprices.InfoPrices(params={}))   # through the probe's guard
            if keywords == "short":
                from src.trade.exceptions import NoTurbosAvailableException
                raise NoTurbosAvailableException("No instruments found.")
            return {"candidate_snapshots": [
                {"Uic": round(bid * 10), "AssetType": TURBO, "Quote": {"Bid": bid},
                 "DisplayAndFormat": {"Description": f"Long {bid}"}}
                for bid in (3.9, 5.1, 16.0, 4.6, 4.2)
            ]}

    monkeypatch.setattr(services, "AsyncInstrumentService", Search)
    monkeypatch.setattr(manager, "load_allowed_indices", lambda config: {"us100": "4912"})
    notes = []

    chosen = _run(lambda: chosen_turbos(GuardedClient(FakeSaxo()), Config(), "account-key", notes))

    # The lowest bids of the band, as the trader picks: 4.2 first, then 4.6 (3.9 and 16.0 are outside it).
    assert [(turbo["direction"], turbo["uic"]) for turbo in chosen] == [("long", 42), ("long", 46)]
    assert notes == ["No us100 short turbo found now: No instruments found."]


def test_a_note_never_carries_the_accounts_key(monkeypatch):
    """The search's refusal names what it asked, AccountKey included: the notes are cleaned as a log line is."""
    import src.trade.async_services as services
    import src.watchlist_manager.manager as manager
    from src.logging_helper.redaction import register_secret
    from src.trade.exceptions import NoTurbosAvailableException

    key = "Zq3xKey9AccountT0"

    class Search:
        turbo_price_range = {"min": 4, "max": 15}

        def __init__(self, *_):
            pass

        async def build_watchlist_candidates(self, exchange_id, underlying_uics, keywords):
            raise NoTurbosAvailableException("No instruments found.", search_context={"AccountKey": key})

    monkeypatch.setattr(services, "AsyncInstrumentService", Search)
    monkeypatch.setattr(manager, "load_allowed_indices", lambda config: {"us100": "4912"})
    register_secret("saxo.account_key", key)   # as get_account_info_async registers it
    try:
        found, observations = _run(lambda: probe(
            GuardedClient(FakeSaxo()), Config(), key, token_getter=lambda: "token-1", uics=None, seconds=0,
            round_trip_transport=httpx.MockTransport(_gateway),
        ))
    finally:
        register_secret("saxo.account_key", None)

    assert found["turbos"] == [] and observations == []
    assert len(found["notes"]) == 2
    assert not any(key in note for note in found["notes"])


def test_the_module_never_names_an_order_endpoint():
    """The probe's own code asks for no order of any kind, whatever the guard would say of it."""
    source = open(probe_module.__file__).read()
    for name in ("tr.orders", "trading.orders", "PatchSessionCapabilities", "ChangeSessionCapabilities"):
        assert name not in source, name
