"""
What Saxo answers about the turbos WATA buys, without trading: which orders it
takes on them, and how its tradable price moves beside the InfoPrice WATA reads.

    wataprices tradable                          the turbos the trader would buy first, each way
    wataprices tradable --uic 61025835 --minutes 10

WATA sizes its entries and judges its exits on InfoPrices, which Saxo calls
"not tradable" and which changed about every 30 s for these turbos; Saxo's
price for trading (/trade/v1/prices, streamed) was never read. Of WATA's timed
entries of October 2026, the median paid 0.59 % above the ask it was sized on
(9.0 % at most), and two market sales stayed working at Saxo for 31 and 58 s.
Whether the tradable price changes sooner, and which durations Saxo takes on
a turbo's orders (ImmediateOrCancel, FillOrKill...), decide what can be done
about either. This asks Saxo, once:

- the session's level (TradeLevel, DataLevel), never changed here;
- the round trip of ten small requests to Saxo's gateway, and the HTTP version they took;
- for each turbo, its details as Saxo gives them: the order types it takes and the durations of each;
- for ``--minutes``, its InfoPrice (asked as the watchlist manager asks it) and its tradable price,
  side by side on a WebSocket of its own: how often each changes, how far apart they stand, and which
  of the two moves first.

Nothing here places, changes or cancels an order, or changes the session: a
request to Saxo's gateway outside ``ALLOWED`` is refused before it leaves, and
the streaming host is only connected to and authorised again. It shares the
services' Saxo session: one more WebSocket, a few subscriptions (removed at
the end) and a dozen requests.
"""

import argparse
import asyncio
import gzip
import json
import logging
import os
import re
import sys
import time
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import httpx

import src.saxo_openapi.endpoints.referencedata as rd
import src.saxo_openapi.endpoints.rootservices as rs
import src.saxo_openapi.endpoints.trading as tr
from src.logging_helper.redaction import redact
from src.saxo_openapi.async_client import DEFAULT_HEADERS, TRADING_ENVIRONMENTS, _mk_endpoint
from src.saxo_openapi.contrib.ws.stream import decode_ws_msg
from src.saxo_streaming.client import _deep_merge

logger = logging.getLogger(__name__)

_ID = r"[\w-]+"
# Every request the probe, and the trader's search it reuses, may make to Saxo's gateway. Never an order
# (/trade/v2/orders, its precheck included) and never the session's capabilities changed.
ALLOWED = (
    ("GET", re.compile(r"^openapi/root/v1/sessions/capabilities/?$")),
    ("GET", re.compile(r"^openapi/port/v1/accounts/me/?$")),
    ("GET", re.compile(r"^openapi/ref/v1/instruments/?$")),
    ("GET", re.compile(r"^openapi/ref/v1/instruments/details/?$")),
    ("GET", re.compile(r"^openapi/trade/v1/infoprices/list/?$")),
    ("POST", re.compile(r"^openapi/trade/v1/infoprices/subscriptions/?$")),
    ("DELETE", re.compile(rf"^openapi/trade/v1/infoprices/subscriptions/{_ID}/{_ID}/?$")),
    ("POST", re.compile(r"^openapi/trade/v1/prices/subscriptions/?$")),
    ("DELETE", re.compile(rf"^openapi/trade/v1/prices/subscriptions/{_ID}/{_ID}/?$")),
)

# The turbos themselves (the trader's own search): what a UIC given by hand is looked for as first.
TURBO_ASSET_TYPES = frozenset({"WarrantKnockOut", "WarrantOpenEndKnockOut", "MiniFuture", "WarrantDoubleKnockOut"})
DIRECTIONS = ("long", "short")
TURBOS_PER_DIRECTION = 2
ROUND_TRIPS = 10
# What says how a turbo can be traded, among the details Saxo gives of it. The field groups add the
# durations each order type takes; a Saxo that refuses them is asked again without.
DETAIL_FIELD_GROUPS = "OrderSetting,SupportedOrderTypeSettings,TradingSessions"
ORDER_FIELDS = (
    "Description", "Symbol", "AssetType", "Uic", "Exchange", "ExchangeId", "IssuerName", "IsTradable", "NonTradableReason",
    "TradableOn", "TradableAs", "TradingStatus", "PlaceableStatus", "SupportedOrderTypes",
    "SupportedOrderTypeSettings", "OrderDistances", "OrderSetting", "MinimumTradeSize", "AmountDecimals",
    "TickSize", "TickSizeScheme", "PriceToContractFactor",
)
DEFAULT_MINUTES = 10.0
MOST_MINUTES = 30.0
# Asked of the tradable price; Saxo rounds a refresh rate up to its own steps. The InfoPrice keeps the
# services' own (trade.config.general.streaming.refresh_rate_ms), so that it is read as WATA reads it.
DEFAULT_TRADABLE_REFRESH_MS = 100
# A token renewed by SaxoAuth is handed to the streaming connection, as SaxoStreamClient does every 15 min.
REAUTHORIZE_EVERY_SECONDS = 60.0
# How far the InfoPrice may trail (or lead) the tradable price for the shift that brings them closest,
# and the seconds both must stand over for a shift to count.
MOST_LAG_SECONDS = 90
LEAST_OVERLAP_SECONDS = 30
# Where `wataprices` writes what it exports (relative to /app in the recorder's container).
DEFAULT_DIRECTORY = os.path.join("var", "lib", "price_export")
PARIS = ZoneInfo("Europe/Paris")


class NotAllowed(Exception):
    """A request the probe never makes: refused before it leaves."""


def check_allowed(method: str, path: str) -> None:
    if not any(method.upper() == allowed and pattern.match(path) for allowed, pattern in ALLOWED):
        raise NotAllowed(f"The probe does not ask Saxo for {method.upper()} {path}")


class GuardedClient:
    """The Saxo client the probe, and the trader's search it reuses, go through: ``ALLOWED`` or nothing."""

    def __init__(self, api_client):
        self._api_client = api_client

    async def request(self, endpoint):
        check_allowed(endpoint.method, str(endpoint))
        return await self._api_client.request(endpoint)


# ── What is measured ──────────────────────────────────────────────


@dataclass
class Observation:
    """A turbo's quote as one feed stood after one of Saxo's messages."""

    at: float                   # seconds since the stream opened
    feed: str                   # "info" or "tradable"
    uic: int
    bid: float | None
    ask: float | None
    price_type_bid: str | None
    price_type_ask: str | None
    market_state: str | None
    delayed_minutes: int | None
    last_updated: str | None    # Saxo's own time of the quote, when it gives one


def _number(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def observation(feed: str, uic: int, row: dict, at: float) -> Observation:
    quote = row.get("Quote") or {}
    delayed = quote.get("DelayedByMinutes")
    return Observation(
        at=round(at, 3), feed=feed, uic=int(uic),
        bid=_number(quote.get("Bid")), ask=_number(quote.get("Ask")),
        price_type_bid=quote.get("PriceTypeBid"), price_type_ask=quote.get("PriceTypeAsk"),
        market_state=quote.get("MarketState"),
        delayed_minutes=int(delayed) if isinstance(delayed, (int, float)) else None,
        last_updated=row.get("LastUpdated"),
    )


def percentile(values: list[float], share: float) -> float:
    """The value under which ``share`` of ``values`` lie, interpolated between the two nearest."""
    ordered = sorted(values)
    place = (len(ordered) - 1) * share
    low = int(place)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (place - low)


def spread_of(values: list[float], digits: int = 3) -> dict | None:
    if not values:
        return None
    return {
        "median": round(percentile(values, 0.5), digits),
        "p90": round(percentile(values, 0.9), digits),
        "max": round(max(values), digits),
    }


def changes(points: list[Observation]) -> list[float]:
    """
    The moments a feed's quote changed: its bid or its ask other than the one
    before. The first quote is no change, and a message without one is passed over.
    """
    moments: list[float] = []
    last = None
    for point in points:
        quoted = (point.bid, point.ask)
        if quoted == (None, None):
            continue
        if last is not None and quoted != last:
            moments.append(point.at)
        last = quoted
    return moments


def feed_figures(points: list[Observation]) -> dict:
    moments = changes(points)
    return {
        "messages": len(points),
        "changes": len(moments),
        "seconds_between_changes": spread_of([after - before for before, after in zip(moments, moments[1:])]),
        "price_types": sorted({f"{point.price_type_bid}/{point.price_type_ask}" for point in points}),
        "market_states": sorted({point.market_state for point in points if point.market_state}),
        "delayed_minutes": max((point.delayed_minutes or 0 for point in points), default=None),
    }


def asks_each_second(points: list[Observation], seconds: float) -> list[float | None]:
    """The ask standing at each whole second of the run: the last one received by then."""
    ordered = sorted(points, key=lambda point: point.at)
    standing, index, asks = None, 0, []
    for second in range(int(seconds) + 1):
        while index < len(ordered) and ordered[index].at <= second:
            if ordered[index].ask is not None:
                standing = ordered[index].ask
            index += 1
        asks.append(standing)
    return asks


def apart(info_asks: list[float | None], tradable_asks: list[float | None]) -> dict | None:
    """How far the InfoPrice's ask stood from the tradable one, in % of the tradable, over the seconds both stood."""
    signed = [(info - tradable) / tradable * 100
              for info, tradable in zip(info_asks, tradable_asks) if info is not None and tradable]
    if not signed:
        return None
    return {
        "seconds": len(signed),
        "percent": spread_of([abs(value) for value in signed], 4),
        "signed_median_percent": round(percentile(signed, 0.5), 4),
    }


def lead(info_asks: list[float | None], tradable_asks: list[float | None], most: int = MOST_LAG_SECONDS) -> dict | None:
    """
    How many seconds the InfoPrice trails the tradable price: the shift of the
    tradable asks that brings them closest to the InfoPrice's (the mean of their
    differences in %), from ``most`` seconds ahead to ``most`` behind. Positive:
    the InfoPrice shows that many seconds later what the tradable price showed.
    """
    def distance(lag: int) -> float | None:
        differences = []
        for second, info in enumerate(info_asks):
            source = second - lag
            if info is None or not 0 <= source < len(tradable_asks) or not tradable_asks[source]:
                continue
            differences.append(abs(info - tradable_asks[source]) / tradable_asks[source] * 100)
        return sum(differences) / len(differences) if len(differences) >= LEAST_OVERLAP_SECONDS else None

    scored = {lag: score for lag in range(-most, most + 1) if (score := distance(lag)) is not None}
    if 0 not in scored:
        return None
    best = min(scored, key=lambda lag: (scored[lag], abs(lag)))
    return {
        "seconds": best,
        "mean_percent_then": round(scored[best], 4),
        "mean_percent_at_once": round(scored[0], 4),
    }


def summary(observations: list[Observation], uics: list[int], seconds: float) -> dict:
    by_feed: dict[tuple[str, int], list[Observation]] = defaultdict(list)
    for point in observations:
        by_feed[(point.feed, point.uic)].append(point)
    figures = {}
    for uic in uics:
        info, tradable = by_feed.get(("info", uic), []), by_feed.get(("tradable", uic), [])
        info_asks, tradable_asks = asks_each_second(info, seconds), asks_each_second(tradable, seconds)
        figures[uic] = {
            "info": feed_figures(info),
            "tradable": feed_figures(tradable),
            "apart": apart(info_asks, tradable_asks),
            "lead": lead(info_asks, tradable_asks),
        }
    return figures


# ── What Saxo says of a turbo's orders ─────────────────────────────


def durations_by_type(details: dict) -> dict[str, list[str] | None]:
    """The order types Saxo takes on an instrument, each with its durations (None when Saxo gave none)."""
    found: dict[str, list[str] | None] = {}
    for setting in details.get("SupportedOrderTypeSettings") or []:
        if isinstance(setting, dict) and setting.get("OrderType"):
            found[setting["OrderType"]] = list(setting.get("DurationTypes") or [])
    for order_type in details.get("SupportedOrderTypes") or []:
        found.setdefault(order_type, None)
    return found


def order_answers(details: dict) -> dict[str, bool | None]:
    """What the questions of the probe come to: True taken, False not, None when Saxo did not say."""
    types = durations_by_type(details)

    def takes(order_type: str, duration: str) -> bool | None:
        if order_type not in types:
            return False if types else None
        durations = types[order_type]
        return None if durations is None else duration in durations

    return {
        "Market with ImmediateOrCancel": takes("Market", "ImmediateOrCancel"),
        "Market with FillOrKill": takes("Market", "FillOrKill"),
        "Limit with ImmediateOrCancel": takes("Limit", "ImmediateOrCancel"),
        "Switch (sell one, buy another, in one order)": ("Switch" in types) if types else None,
    }


async def instrument_details(api_client, account_key: str, uics: list[int], notes: list[str]) -> list[dict]:
    """Saxo's details of the instruments, one row per UIC and asset type."""
    params = {"Uics": ",".join(str(uic) for uic in uics), "AccountKey": account_key}
    try:
        response = await api_client.request(
            rd.instruments.InstrumentsDetails(params={**params, "FieldGroups": DETAIL_FIELD_GROUPS})
        )
    except NotAllowed:
        raise
    except Exception as e:
        notes.append(f"Saxo refused the details' field groups ({DETAIL_FIELD_GROUPS}): asked without them. {e}")
        response = await api_client.request(rd.instruments.InstrumentsDetails(params=params))
    return [row for row in (response or {}).get("Data") or [] if row.get("Uic") is not None]


async def chosen_turbos(api_client, config_manager, account_key: str, notes: list[str]) -> list[dict]:
    """
    The turbos the trader would buy first now, each way: the lowest bids of its
    price band among what its own search finds (``build_watchlist_candidates``).
    """
    from src.trade.async_services import AsyncInstrumentService
    from src.trade.exceptions import ApiRequestException, NoMarketAvailableException, NoTurbosAvailableException
    from src.watchlist_manager.manager import load_allowed_indices

    service = AsyncInstrumentService(api_client, config_manager, account_key)
    exchange_id = config_manager.get_config_value("trade.config.turbo_preference.exchange_id")
    low, high = service.turbo_price_range["min"], service.turbo_price_range["max"]
    chosen = []
    for indice, underlying_uic in load_allowed_indices(config_manager).items():
        for direction in DIRECTIONS:
            try:
                found = await service.build_watchlist_candidates(
                    exchange_id=exchange_id, underlying_uics=underlying_uic, keywords=direction,
                )
            except (NoMarketAvailableException, NoTurbosAvailableException) as e:
                notes.append(f"No {indice} {direction} turbo found now: {e}")
                continue
            except ApiRequestException as e:
                notes.append(f"The search for {indice} {direction} turbos failed: {e}")
                continue
            in_band = [
                row for row in found.get("candidate_snapshots") or []
                if row.get("Uic") is not None
                and (bid := _number((row.get("Quote") or {}).get("Bid"))) is not None and low <= bid <= high
            ]
            in_band.sort(key=lambda row: row["Quote"]["Bid"])
            for row in in_band[:TURBOS_PER_DIRECTION]:
                chosen.append({
                    "uic": int(row["Uic"]), "asset_type": row["AssetType"], "indice": indice, "direction": direction,
                    "description": (row.get("DisplayAndFormat") or {}).get("Description"),
                })
    return chosen


# ── The session and the gateway ───────────────────────────────────


async def session_level(api_client) -> dict:
    answer = await api_client.request(rs.sessions.GetSessionCapabilities()) or {}
    return {"trade_level": answer.get("TradeLevel"), "data_level": answer.get("DataLevel")}


async def round_trip(environment: str, token: str, times: int = ROUND_TRIPS, transport=None) -> dict:
    """
    How long Saxo's gateway takes to answer a small request (the session's
    capabilities), on one connection as the services hold theirs (httpx, HTTP/2
    offered): the first opens it, the others reuse it.
    """
    endpoint = rs.sessions.GetSessionCapabilities()
    check_allowed(endpoint.method, str(endpoint))
    url = _mk_endpoint(endpoint, environment, "api")
    headers = {**DEFAULT_HEADERS, "Authorization": f"Bearer {token}"}
    seconds, versions, statuses = [], set(), set()
    async with httpx.AsyncClient(http2=True, headers=headers, timeout=30.0, transport=transport) as client:
        for _ in range(times):
            started = time.perf_counter()
            response = await client.get(url)
            seconds.append(time.perf_counter() - started)
            versions.add(response.http_version)
            statuses.add(response.status_code)
    return {
        "first_seconds": round(seconds[0], 4),
        "then": spread_of(seconds[1:], 4),
        "http_versions": sorted(versions),
        "statuses": sorted(statuses),
    }


# ── The two feeds, side by side ───────────────────────────────────


def stream_address(environment: str, token: str, context_id: str) -> str:
    """The WebSocket to connect to, as ``SaxoStreamClient._build_ws_url`` builds it."""
    found = TRADING_ENVIRONMENTS[environment]
    base = found["stream"].replace("https://", "wss://")
    path = f"/{found['prefix']}/oapi/streaming/ws/connect" if environment == "simulation" else "/oapi/streaming/ws/connect"
    return f"{base}{path}?{urlencode({'authorization': f'BEARER {token}', 'contextId': context_id})}"


def reauthorize_address(environment: str, context_id: str) -> str:
    found = TRADING_ENVIRONMENTS[environment]
    path = f"/{found['prefix']}/oapi/streaming/ws/authorize" if environment == "simulation" else "/oapi/streaming/ws/authorize"
    return f"{found['stream']}{path}?contextid={context_id}"


class SideBySide:
    """
    The turbos' InfoPrice and tradable price streamed together on one WebSocket
    of the probe's own context, each message kept as an ``Observation``.
    """

    def __init__(self, api_client, environment: str, account_key: str, turbos: list[dict], *, token_getter,
                 info_refresh_ms: int, tradable_refresh_ms: int = DEFAULT_TRADABLE_REFRESH_MS,
                 amount: float | None = None, clock=time.monotonic):
        self._api = api_client
        self._environment = environment
        self._account_key = account_key
        self._turbos = turbos
        self._token_getter = token_getter
        self._info_refresh_ms = info_refresh_ms
        self._tradable_refresh_ms = tradable_refresh_ms
        self._amount = amount
        self._clock = clock
        self.context_id = f"probe-{uuid.uuid4().hex[:12]}"
        self.observations: list[Observation] = []
        self.notes: list[str] = []
        self.heartbeats = 0
        self._info: dict[str, dict[int, dict]] = {}      # reference id -> {uic: merged row}
        self._tradable: dict[str, tuple[int, dict]] = {}  # reference id -> (uic, merged row)
        self._started = clock()
        self._ended = False

    def _at(self) -> float:
        return self._clock() - self._started

    async def run(self, seconds: float, address: str | None = None) -> float:
        """Listen for ``seconds``; the subscriptions are removed whatever happens. Returns the seconds listened."""
        import websockets

        token = await asyncio.to_thread(self._token_getter)
        address = address or stream_address(self._environment, token, self.context_id)
        listened = 0.0
        async with websockets.connect(address, ping_interval=20, ping_timeout=20, close_timeout=5,
                                      max_size=2 ** 22, proxy=None) as connection:
            self._started = self._clock()
            reauthorizing = asyncio.create_task(self._reauthorize(token))
            try:
                await self._subscribe()
                await self._read(connection, seconds)
                listened = self._at()
            finally:
                reauthorizing.cancel()
                await self._unsubscribe()
        return round(min(listened, seconds), 3)

    async def _subscribe(self):
        by_asset_type: dict[str, list[int]] = defaultdict(list)
        for turbo in self._turbos:
            by_asset_type[turbo["asset_type"]].append(turbo["uic"])
        for number, (asset_type, uics) in enumerate(sorted(by_asset_type.items()), start=1):
            reference_id = f"info-{number}"
            try:
                response = await self._api.request(tr.infoprices.CreateInfoPriceSubscription(data={
                    "Arguments": {
                        "AccountKey": self._account_key,
                        "AssetType": asset_type,
                        # As the watchlist manager asks it (SaxoStreamClient.set_subscriptions).
                        "FieldGroups": ["Quote", "DisplayAndFormat", "InstrumentPriceDetails", "Commissions"],
                        "Uics": ",".join(str(uic) for uic in uics),
                    },
                    "ContextId": self.context_id,
                    "ReferenceId": reference_id,
                    "RefreshRate": self._info_refresh_ms,
                    "Format": "application/json",
                }))
            except NotAllowed:
                raise
            except Exception as e:
                self.notes.append(f"Saxo refused the InfoPrice subscription of {asset_type} {uics}: {e}")
                continue
            rows = {int(row["Uic"]): row for row in ((response or {}).get("Snapshot") or {}).get("Data") or []
                    if row.get("Uic") is not None}
            self._info[reference_id] = rows
            for uic, row in rows.items():
                self.observations.append(observation("info", uic, row, self._at()))
        for turbo in self._turbos:
            await self._subscribe_tradable(turbo)

    async def _subscribe_tradable(self, turbo: dict):
        reference_id = f"px-{turbo['uic']}"
        arguments = {
            "AccountKey": self._account_key, "Uic": turbo["uic"], "AssetType": turbo["asset_type"],
            "FieldGroups": ["Quote"],
        }
        if self._amount is not None:
            arguments["Amount"] = self._amount
        refresh_rates = [self._tradable_refresh_ms]
        if self._info_refresh_ms != self._tradable_refresh_ms:
            refresh_rates.append(self._info_refresh_ms)
        for refresh_ms in refresh_rates:
            try:
                response = await self._api.request(tr.prices.CreatePriceSubscription(data={
                    "Arguments": arguments,
                    "ContextId": self.context_id,
                    "ReferenceId": reference_id,
                    "RefreshRate": refresh_ms,
                    "Format": "application/json",
                }))
            except NotAllowed:
                raise
            except Exception as e:
                self.notes.append(f"Saxo refused the tradable price of {turbo['uic']} at {refresh_ms} ms: {e}")
                continue
            if refresh_ms != self._tradable_refresh_ms:
                self.notes.append(f"The tradable price of {turbo['uic']} was taken at {refresh_ms} ms.")
            row = (response or {}).get("Snapshot") or {}
            self._tradable[reference_id] = (turbo["uic"], row)
            self.observations.append(observation("tradable", turbo["uic"], row, self._at()))
            return

    async def _read(self, connection, seconds: float):
        import websockets.exceptions

        while not self._ended:
            left = seconds - self._at()
            if left <= 0:
                return
            try:
                raw = await asyncio.wait_for(connection.recv(), timeout=left)
            except asyncio.TimeoutError:
                return
            except websockets.exceptions.ConnectionClosed as e:
                received = e.rcvd
                self.notes.append(
                    f"Saxo closed the connection after {self._at():.0f} s"
                    + (f" ({received.code} {received.reason})" if received is not None else "")
                )
                return
            if isinstance(raw, str):
                continue
            for message in decode_ws_msg(raw):
                self.take(message.get("refid", ""), message.get("msg"))

    def take(self, reference_id: str, payload) -> None:
        """One of Saxo's messages: merged into the feed it belongs to, and observed."""
        if reference_id.startswith("_"):
            if reference_id == "_heartbeat":
                self.heartbeats += 1
            elif reference_id in ("_resetsubscriptions", "_disconnect"):
                self.notes.append(f"Saxo sent {reference_id} after {self._at():.0f} s: the run ended there.")
                self._ended = True
            return
        if reference_id in self._info:
            rows = self._info[reference_id]
            if isinstance(payload, dict):
                deltas = payload.get("Data") if "Data" in payload else [payload]
            else:
                deltas = payload if isinstance(payload, list) else []
            for delta in deltas:
                if not isinstance(delta, dict) or delta.get("Uic") is None:
                    continue
                uic = int(delta["Uic"])
                _deep_merge(rows.setdefault(uic, {}), delta)
                self.observations.append(observation("info", uic, rows[uic], self._at()))
        elif reference_id in self._tradable and isinstance(payload, dict):
            uic, row = self._tradable[reference_id]
            _deep_merge(row, payload)
            self.observations.append(observation("tradable", uic, row, self._at()))

    async def _reauthorize(self, token: str):
        """
        Hands the connection each token SaxoAuth renews meanwhile, as SaxoStreamClient
        does (the REST client takes the renewed token by itself).
        """
        while True:
            await asyncio.sleep(REAUTHORIZE_EVERY_SECONDS)
            try:
                renewed = await asyncio.to_thread(self._token_getter)
                if renewed == token:
                    continue
                async with httpx.AsyncClient(timeout=10) as client:
                    answer = await client.put(reauthorize_address(self._environment, self.context_id),
                                              headers={"Authorization": f"Bearer {renewed}"})
                if answer.status_code == 202:
                    token = renewed
                else:
                    self.notes.append(f"The streaming connection's authorisation was refused ({answer.status_code}).")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.notes.append(f"The streaming connection could not be authorised again: {e}")

    async def _unsubscribe(self):
        removals = [tr.infoprices.RemoveInfoPriceSubscriptionById(ContextId=self.context_id, ReferenceId=reference_id)
                    for reference_id in self._info]
        removals += [tr.prices.PriceSubscriptionRemove(ContextId=self.context_id, ReferenceId=reference_id)
                     for reference_id in self._tradable]
        for removal in removals:
            try:
                await self._api.request(removal)
            except Exception as e:
                self.notes.append(f"A subscription could not be removed ({removal}): {e}")


# ── The report ────────────────────────────────────────────────────


def _said(answer: bool | None) -> str:
    return {True: "taken", False: "not taken", None: "Saxo did not say"}[answer]


def _gaps(figures: dict) -> str:
    gaps = figures["seconds_between_changes"]
    counted = f"{figures['changes']} change{'' if figures['changes'] == 1 else 's'}"
    if gaps is None:
        return counted
    return f"{counted}, every {gaps['median']:g} s at the median, {gaps['p90']:g} s at p90, {gaps['max']:g} s at most"


def report(found: dict) -> str:
    lines = []
    session = found.get("session") or {}
    lines.append(f"Saxo session: TradeLevel {session.get('trade_level')}, DataLevel {session.get('data_level')}")
    trip = found.get("round_trip")
    if trip:
        then = trip["then"] or {}
        lines.append(
            f"Gateway round trip ({ROUND_TRIPS} requests, {', '.join(trip['http_versions'])}): "
            f"{trip['first_seconds']:g} s for the first (it opens the connection), then "
            f"{then.get('median', 0):g} s at the median, {then.get('p90', 0):g} s at p90"
        )
    figures = found.get("figures") or {}
    for turbo in found.get("turbos") or []:
        details = turbo.get("details") or {}
        lines.append("")
        lines.append(
            f"UIC {turbo['uic']}  {turbo['asset_type']}  {details.get('Description') or turbo.get('description') or ''}"
            + (f"  ({turbo['indice']} {turbo['direction']})" if turbo.get("indice") else "")
        )
        exchange = details.get("ExchangeId") or (
            (details.get("Exchange") or {}).get("ExchangeId") if isinstance(details.get("Exchange"), dict)
            else details.get("Exchange")
        )
        lines.append(f"    tradable: {details.get('IsTradable')}; exchange {exchange}"
                     + (f"; issuer {details['IssuerName']}" if details.get("IssuerName") else ""))
        types = durations_by_type(details)
        if types:
            lines.append("    orders Saxo takes: " + "; ".join(
                f"{order_type} ({', '.join(durations)})" if durations else order_type
                for order_type, durations in types.items()
            ))
        for question, answer in order_answers(details).items():
            lines.append(f"    {question}: {_said(answer)}")
        measured = figures.get(turbo["uic"]) or figures.get(str(turbo["uic"]))
        if not measured:
            continue
        for feed, name in (("info", "InfoPrice"), ("tradable", "tradable price")):
            feed_found = measured[feed]
            lines.append(
                f"    {name}: {feed_found['messages']} messages, {_gaps(feed_found)}; "
                f"{', '.join(feed_found['price_types']) or 'no quote'}"
                + (f"; delayed {feed_found['delayed_minutes']} min" if feed_found["delayed_minutes"] else "")
            )
        if measured["apart"]:
            share = measured["apart"]["percent"]
            lines.append(
                f"    apart: the InfoPrice's ask stood {share['median']:g} % from the tradable ask at the median, "
                f"{share['p90']:g} % at p90, {share['max']:g} % at most "
                f"(signed median {measured['apart']['signed_median_percent']:g} %)"
            )
        if measured["lead"]:
            follows = measured["lead"]
            lines.append(
                f"    lead: the InfoPrice is closest to the tradable price {follows['seconds']} s later "
                f"({follows['mean_percent_then']:g} % apart then, {follows['mean_percent_at_once']:g} % at the same moment)"
            )
    notes = found.get("notes") or []
    if notes:
        lines += ["", "Notes:"] + [f"    {note}" for note in notes]
    if found.get("written_to"):
        lines += ["", f"Every message: {found['written_to']}"]
    return "\n".join(lines)


def write_series(path: str, found: dict, observations: list[Observation]) -> None:
    """The run as JSON lines: what was found first, then every observation."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as out:
        out.write(json.dumps({"kind": "probe", **found}, default=str) + "\n")
        for point in observations:
            out.write(json.dumps({"kind": "quote", **asdict(point)}) + "\n")


def outside_turbo_hours(now: datetime) -> bool:
    """Saxo quotes the turbos from 08:00 to 22:00 in Paris, on weekdays."""
    paris = now.astimezone(PARIS)
    return paris.weekday() >= 5 or not 8 <= paris.hour < 22


# ── The command ───────────────────────────────────────────────────


async def probe(api_client, config_manager, account_key: str, *, token_getter, uics: list[int] | None,
                seconds: float, tradable_refresh_ms: int = DEFAULT_TRADABLE_REFRESH_MS,
                amount: float | None = None, address: str | None = None, round_trip_transport=None) -> tuple[dict, list]:
    """Everything the command asks Saxo, through ``api_client`` (a ``GuardedClient``)."""
    environment = config_manager.get_config_value("saxo_auth.env", "live")
    notes: list[str] = []
    found: dict = {"started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "environment": environment}
    found["session"] = await session_level(api_client)
    try:
        found["round_trip"] = await round_trip(environment, await asyncio.to_thread(token_getter),
                                               transport=round_trip_transport)
    except Exception as e:
        notes.append(f"The round trip could not be measured: {e}")

    if uics:
        rows = await instrument_details(api_client, account_key, uics, notes)
        turbos = []
        for uic in uics:
            mine = [row for row in rows if row["Uic"] == uic]
            row = next((row for row in mine if row.get("AssetType") in TURBO_ASSET_TYPES), mine[0] if mine else None)
            if row is None:
                notes.append(f"Saxo lists no instrument {uic}.")
                continue
            turbos.append({"uic": uic, "asset_type": row["AssetType"], "details": row})
    else:
        turbos = await chosen_turbos(api_client, config_manager, account_key, notes)
        rows = []
        if turbos:
            try:
                rows = await instrument_details(api_client, account_key, [turbo["uic"] for turbo in turbos], notes)
            except NotAllowed:
                raise
            except Exception as e:
                # Their prices can still be compared: the search gave their asset types.
                notes.append(f"Saxo gave no details of the turbos: {e}")
        for turbo in turbos:
            turbo["details"] = next((row for row in rows
                                     if row["Uic"] == turbo["uic"] and row.get("AssetType") == turbo["asset_type"]), {})
    for turbo in turbos:
        turbo["details"] = {key: turbo["details"][key] for key in ORDER_FIELDS if key in turbo["details"]}
    found["turbos"] = turbos

    observations: list[Observation] = []
    if turbos and seconds > 0:
        streaming = config_manager.get_config_value("trade.config.general.streaming", {}) or {}
        side_by_side = SideBySide(
            api_client, environment, account_key, turbos, token_getter=token_getter,
            info_refresh_ms=int(streaming.get("refresh_rate_ms", 1000)),
            tradable_refresh_ms=tradable_refresh_ms, amount=amount,
        )
        found["seconds"] = await side_by_side.run(seconds, address=address)
        found["heartbeats"] = side_by_side.heartbeats
        notes += side_by_side.notes
        observations = side_by_side.observations
        found["figures"] = summary(observations, [turbo["uic"] for turbo in turbos], found["seconds"])
    # Cleaned as a log line is: a refusal can carry what was asked, the account's key with it.
    found["notes"] = [redact(note) for note in notes]
    return found, observations


def parse_arguments(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="wataprices tradable",
        description="Ask Saxo which orders it takes on the turbos, and how its tradable price moves beside the "
                    "InfoPrice WATA reads. Nothing is ordered.",
    )
    parser.add_argument("--uic", type=int, action="append",
                        help="a turbo's UIC, repeated for several (default: the two the trader would buy first, each way)")
    parser.add_argument("--minutes", type=float, default=DEFAULT_MINUTES,
                        help=f"how long to listen to both prices (default {DEFAULT_MINUTES:g}, at most {MOST_MINUTES:g}; "
                             "0 for the orders alone)")
    parser.add_argument("--refresh-ms", type=int, default=DEFAULT_TRADABLE_REFRESH_MS,
                        help=f"refresh rate asked for the tradable price (default {DEFAULT_TRADABLE_REFRESH_MS})")
    parser.add_argument("--amount", type=float, help="the order size the tradable price is asked for (default: Saxo's)")
    parser.add_argument("--out", help=f"the file of every message (default: {DEFAULT_DIRECTORY}/tradable-<moment>.jsonl.gz)")
    arguments = parser.parse_args(argv)
    if not 0 <= arguments.minutes <= MOST_MINUTES:
        parser.error(f"--minutes must be between 0 and {MOST_MINUTES:g}")
    if arguments.refresh_ms < 0:
        parser.error("--refresh-ms cannot be negative")
    return arguments


async def run(arguments: argparse.Namespace) -> int:
    from src.configuration import ConfigurationManager
    from src.saxo_authen import SaxoAuth
    from src.trade.async_services import AsyncSaxoApiClient
    from src.trader import get_account_info_async
    from src.vault import loader as vault

    config_path = os.getenv("WATA_CONFIG_PATH")
    if not config_path:
        print("WATA_CONFIG_PATH not set", file=sys.stderr)
        return 10
    config_manager = ConfigurationManager(config_path)
    # Saxo's app, which a renewal of the login needs, is sealed for the recorder: read as at its start.
    try:
        await vault.load(config_manager, "price_recorder")
    except vault.CredentialsUnavailable as e:
        print(f"PostgreSQL could not be read for the recorder's credentials ({e}): try again.", file=sys.stderr)
        return 1
    now = datetime.now(timezone.utc)
    if arguments.minutes and outside_turbo_hours(now):
        print("Saxo quotes the turbos from 08:00 to 22:00 in Paris on weekdays: outside them, both prices stand still.",
              file=sys.stderr)
    saxo_auth = SaxoAuth(config_manager)
    api_client = AsyncSaxoApiClient(config_manager, saxo_auth)
    guarded = GuardedClient(api_client)
    try:
        await api_client.ensure_ready()
        account_key = (await get_account_info_async(guarded)).AccountKey
        found, observations = await probe(
            guarded, config_manager, account_key, token_getter=saxo_auth.get_token, uics=arguments.uic,
            seconds=arguments.minutes * 60, tradable_refresh_ms=arguments.refresh_ms, amount=arguments.amount,
        )
    finally:
        await api_client.close()
    if observations:
        path = arguments.out or os.path.join(DEFAULT_DIRECTORY, f"tradable-{now.strftime('%Y%m%dT%H%M%SZ')}.jsonl.gz")
        write_series(path, found, observations)
        found["written_to"] = path
    print(redact(report(found)))
    return 0


def main() -> int:
    from src.logging_helper.redaction import RedactingFilter

    # What the libraries say goes to the terminal cleaned as a log line is: never a token or an account key.
    handler = logging.StreamHandler()
    handler.addFilter(RedactingFilter())
    logging.basicConfig(level=logging.WARNING, handlers=[handler], format="%(levelname)s %(name)s: %(message)s")
    return asyncio.run(run(parse_arguments()))


if __name__ == "__main__":
    sys.exit(main())
