# src/trade/async_services.py
"""
Async versions of SaxoApiClient, InstrumentService, OrderService,
PositionService, TradingOrchestrator, and PerformanceMonitor.

Key improvements over the sync versions:
  - Non-blocking HTTP via httpx.AsyncClient (AsyncAPI)
  - Parallel API calls where independent (find_turbos ‖ get_spending_power)
  - asyncio.sleep instead of blocking time.sleep
  - Async PostgreSQL persistence
  - Bounded concurrency for multi-position operations
"""

import asyncio
import json
import logging
import math
import os
import re
import time
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, ROUND_FLOOR

import pytz

# --- Saxo OpenApi Components ---
import src.saxo_openapi.endpoints.referencedata as rd
import src.saxo_openapi.endpoints.trading as tr
import src.saxo_openapi.endpoints.portfolio as pf
from src.saxo_openapi.contrib.orders import MarketOrder, tie_account_to_order, direction_from_amount
from src.saxo_openapi.contrib.orders.helper import direction_invert
from src.saxo_openapi.async_client import AsyncAPI
from src.saxo_openapi.exceptions import OpenAPIError as SaxoOpenApiLibError

# --- Local Imports ---
from src.saxo_authen import SaxoAuth
from src.configuration import ConfigurationManager
from src.database.postgres import (
    AsyncDbOrderManager,
    AsyncDbPositionManager,
)
from .exceptions import (
    NoMarketAvailableException,
    NoTurbosAvailableException,
    PositionNotFoundException,
    InsufficientFundsException,
    ApiRequestException,
    TokenAuthenticationException,
    DatabaseOperationException,
    SaxoApiError,
    OrderPlacementError,
)

logger = logging.getLogger(__name__)

TURBO_DESCRIPTION_PATTERN = re.compile(r"(.*) (\w+) (\w+) (\d+(?:\.\d+)?) (\w+)$")
INITIAL_TURBO_SEARCH_LIMIT = 60
INFO_PRICE_CANDIDATE_LIMIT = 20
INFO_PRICE_RETRY_BACKOFFS = (0.2, 0.4, 0.8)
# How long a token returned by SaxoAuth is trusted before asking it again. SaxoAuth
# renews tokens 120s before they expire, so a few seconds of reuse is safe.
TOKEN_RECHECK_INTERVAL_SECONDS = 5
# Waits before each lookup of a just-closed position in Saxo's closed positions
# (1.5s in total). The background variant keeps trying for longer since nothing
# is waiting on it.
CLOSED_POSITION_LOOKUP_DELAYS = (0.2, 0.3, 0.5, 0.5)
CLOSED_POSITION_BACKGROUND_LOOKUP_DELAYS = CLOSED_POSITION_LOOKUP_DELAYS + (1.0, 2.0, 3.0)
# Saxo keeps a sold position listed with one of these statuses on accounts that
# net positions at end of day.
CLOSED_POSITION_STATUSES = ("Closed", "Closing")
# How long a position check waits for the database. Past that it carries on with
# what it last read, so stop-losses keep working while the DB is slow or down,
# and it leaves the DB alone for a while instead of paying that wait on each check.
MANAGED_STATE_DB_TIMEOUT_SECONDS = 1.0
DB_RETRY_AFTER_FAILURE_SECONDS = 5.0


# ──────────────────────────────────────────────
#  Utilities
# ──────────────────────────────────────────────

def backoff_gaps(total_seconds: float, first_gap: float = 0.1, max_gap: float = 1.0) -> list[float]:
    """Doubling waits (capped at ``max_gap``) that add up to ``total_seconds``."""
    gaps = []
    gap = first_gap
    remaining = total_seconds
    while remaining > 1e-9:
        step = min(gap, remaining)
        gaps.append(step)
        remaining -= step
        gap = min(gap * 2, max_gap)
    return gaps


def stop_order_tick_size(instrument_details: dict, price: float) -> float | None:
    """Price increment Saxo accepts for a stop order at ``price`` (None when it isn't published)."""
    scheme = instrument_details.get("TickSizeScheme") or {}
    for element in sorted(scheme.get("Elements") or [], key=lambda element: element["HighPrice"]):
        if price <= element["HighPrice"]:
            return element["TickSize"]
    return (
        scheme.get("DefaultTickSize")
        or instrument_details.get("TickSizeStopOrder")
        or instrument_details.get("TickSize")
    )


def round_down_to_tick(price: float, tick_size: float) -> float:
    """Largest multiple of ``tick_size`` that is not above ``price``."""
    tick = Decimal(str(tick_size))
    # Rounded first: 10.1 * 0.7 is 7.069999999999999 in floating point, which
    # would otherwise land one tick below the 7.07 that was meant.
    exact_price = Decimal(str(round(price, 9)))
    return float((exact_price / tick).to_integral_value(rounding=ROUND_FLOOR) * tick)


def parse_saxo_turbo_description(description: str) -> dict | None:
    match = TURBO_DESCRIPTION_PATTERN.match(description)
    if match:
        return {
            "name": match.group(1),
            "kind": match.group(2),
            "buysell": match.group(3),
            "price": match.group(4),
            "from": match.group(5),
        }
    return None


def annotate_parsed_turbos(instruments: list[dict]) -> list[dict]:
    """Attach parsed description metadata to valid turbo search rows."""
    valid_items = []
    for item in instruments:
        parsed = parse_saxo_turbo_description(item.get("Description", ""))
        if parsed:
            item["appParsedData"] = parsed
            valid_items.append(item)
    return valid_items


def build_identifier_lookup(instruments: list[dict]) -> dict:
    return {
        item["Identifier"]: item
        for item in instruments
        if item.get("Identifier") is not None
    }


def select_candidate_instruments(valid_items: list[dict], keywords: str, limit: int = INFO_PRICE_CANDIDATE_LIMIT) -> list[dict]:
    sort_reverse = keywords.lower() != "short"
    sorted_instruments = sorted(valid_items, key=lambda item: float(item["appParsedData"]["price"]), reverse=sort_reverse)
    return sorted_instruments[:limit]


def group_instruments_by_asset_type(candidate_instruments: list[dict]) -> dict[str, list]:
    instrument_groups: dict[str, list] = defaultdict(list)
    for item in candidate_instruments:
        instrument_groups[item["AssetType"]].append(item)
    return instrument_groups


def has_tradable_bid_in_price_range(item: dict, min_price: float, max_price: float) -> bool:
    quote = item.get("Quote")
    return (
        bool(quote)
        and quote.get("Bid") is not None
        and quote.get("PriceTypeAsk") != "NoMarket"
        and quote.get("PriceTypeBid") != "NoMarket"
        and quote.get("MarketState") != "Closed"
        and min_price <= quote["Bid"] <= max_price
    )


def filter_items_with_valid_bid(items: list[dict]) -> list[dict]:
    return [
        item for item in items
        if item.get("Quote") and item["Quote"].get("Bid") is not None
    ]


def filter_tradable_market_items(items: list[dict]) -> list[dict]:
    return [
        item for item in items
        if item["Quote"].get("PriceTypeAsk") != "NoMarket"
        and item["Quote"].get("PriceTypeBid") != "NoMarket"
        and item["Quote"].get("MarketState") != "Closed"
    ]


def filter_items_in_price_range(items: list[dict], min_price: float, max_price: float) -> list[dict]:
    return [item for item in items if min_price <= item["Quote"]["Bid"] <= max_price]


def select_best_price_candidate(items: list[dict]) -> dict:
    return sorted(items, key=lambda item: item["Quote"]["Bid"])[0]


def merge_detail_rows_into_candidate_snapshots(candidate_quote_rows: list[dict], detail_rows: list[dict]) -> list[dict]:
    details_by_uic = {
        detail_row["Uic"]: detail_row
        for detail_row in detail_rows
        if detail_row.get("Uic") is not None
    }
    merged_rows = []
    for quote_row in candidate_quote_rows:
        merged_row = dict(quote_row)
        detail_row = details_by_uic.get(quote_row.get("Uic"), {})
        if detail_row.get("DisplayAndFormat"):
            merged_row["DisplayAndFormat"] = detail_row["DisplayAndFormat"]
        if detail_row.get("Commissions"):
            merged_row["Commissions"] = detail_row["Commissions"]
        if detail_row.get("InstrumentPriceDetails"):
            merged_row["InstrumentPriceDetails"] = detail_row["InstrumentPriceDetails"]
        merged_rows.append(merged_row)
    return merged_rows


def build_selected_turbo_from_candidate_snapshots(
    *,
    exchange_id: str,
    underlying_uics: str,
    keywords: str,
    candidate_snapshots: list[dict],
    selected_source_lookup: dict,
    min_price: float,
    max_price: float,
    subscription_context_id: str | None = None,
    subscription_reference_id: str | None = None,
    uic_to_subscription_reference_id: dict | None = None,
) -> dict:
    valid_bid_items = filter_items_with_valid_bid(candidate_snapshots)
    if not valid_bid_items:
        raise NoMarketAvailableException("No instruments with Bid data after filtering.")

    available_items = filter_tradable_market_items(valid_bid_items)
    if not available_items:
        raise NoMarketAvailableException(f"No markets available for {keywords} in {exchange_id}.")

    price_filtered = filter_items_in_price_range(available_items, min_price, max_price)
    if not price_filtered:
        raise NoTurbosAvailableException(
            f"No turbos in price range {min_price}-{max_price}.",
            search_context={"PriceRange": (min_price, max_price), "AvailableCount": len(available_items)},
        )

    selected = select_best_price_candidate(price_filtered)
    selected_source = selected_source_lookup.get(selected.get("Identifier"), {})
    resolved_subscription_reference_id = subscription_reference_id
    if resolved_subscription_reference_id is None and uic_to_subscription_reference_id is not None:
        resolved_subscription_reference_id = (
            uic_to_subscription_reference_id.get(selected.get("Uic"))
            or uic_to_subscription_reference_id.get(str(selected.get("Uic")))
        )
    return build_selected_turbo_result(
        exchange_id=exchange_id,
        underlying_uics=underlying_uics,
        keywords=keywords,
        selected=selected,
        selected_source=selected_source,
        detail_snapshot=selected,
        subscription_context_id=subscription_context_id,
        subscription_reference_id=resolved_subscription_reference_id,
    )


def build_selected_turbo_result(
    *,
    exchange_id: str,
    underlying_uics: str,
    keywords: str,
    selected: dict,
    selected_source: dict,
    detail_snapshot: dict,
    subscription_context_id: str | None = None,
    subscription_reference_id: str | None = None,
) -> dict:
    display_and_format = detail_snapshot.get("DisplayAndFormat", {})
    commissions = detail_snapshot.get("Commissions", {})
    description = display_and_format.get("Description", selected_source.get("Description", "N/A"))
    quote = selected.get("Quote", {})

    return {
        "input_criteria": {
            "exchange_id": exchange_id,
            "underlying_uics": underlying_uics,
            "keywords": keywords,
        },
        "selected_instrument": {
            "uic": selected["Uic"],
            "asset_type": selected["AssetType"],
            "description": description,
            "symbol": display_and_format.get("Symbol", "N/A"),
            "currency": display_and_format.get("Currency", "N/A"),
            "decimals": display_and_format.get("OrderDecimals", 2),
            "parsed_data": parse_saxo_turbo_description(description),
            "quote": quote,
            "commissions": commissions,
            "latest_ask": quote.get("Ask"),
            "latest_bid": quote.get("Bid"),
            "subscription_context_id": subscription_context_id,
            "subscription_reference_id": subscription_reference_id,
        },
    }


# ──────────────────────────────────────────────
#  Async SaxoApiClient
# ──────────────────────────────────────────────

class AsyncSaxoApiClient:
    """
    Async facade around AsyncAPI (httpx-based).
    Handles token refresh and translates exceptions.
    """

    def __init__(self, config_manager: ConfigurationManager, saxo_auth: SaxoAuth):
        self.config_manager = config_manager
        self.saxo_auth = saxo_auth
        self.environment = config_manager.get_config_value("saxo_auth.env", "live")
        self._api: AsyncAPI | None = None
        self._current_token: str | None = None
        self._token_checked_at = 0.0
        self._ready_lock = asyncio.Lock()

    def _token_recently_checked(self) -> bool:
        return (
            self._api is not None
            and time.monotonic() - self._token_checked_at < TOKEN_RECHECK_INTERVAL_SECONDS
        )

    async def ensure_ready(self):
        """Initialize the underlying AsyncAPI instance and keep its access token current."""
        if self._token_recently_checked():
            return

        async with self._ready_lock:
            if self._token_recently_checked():
                return

            # SaxoAuth is synchronous (file read, decryption, and an HTTP call when
            # the token needs renewing), so it runs in a thread to keep the loop free.
            latest_token = await asyncio.to_thread(self.saxo_auth.get_token)
            if self._api is None:
                logger.info("AsyncSaxoApiClient: initializing AsyncAPI for env '%s'", self.environment)
                self._api = AsyncAPI(
                    access_token=latest_token,
                    environment=self.environment,
                    timeout=30.0,
                )
            elif latest_token != self._current_token:
                # Swapped in place: rebuilding the client would drop its open
                # connection and break any request still in flight on it.
                logger.info("AsyncSaxoApiClient: access token refreshed")
                self._api.update_token(latest_token)
            self._current_token = latest_token
            self._token_checked_at = time.monotonic()

    async def close(self):
        if self._api:
            await self._api.close()
            self._api = None

    async def request(self, endpoint_request_obj):
        """
        Async API request with exception translation.
        Mirrors the sync SaxoApiClient.request() interface.
        """
        await self.ensure_ready()

        try:
            return await self._api.request(endpoint_request_obj)

        except SaxoOpenApiLibError as e:
            status_code = e.code
            content_str = str(e.content) if not isinstance(e.content, str) else e.content

            error_message = content_str
            error_code = None
            saxo_error_details = None
            try:
                saxo_error_details = json.loads(content_str)
                error_message = saxo_error_details.get("Message", e.reason)
                error_code = saxo_error_details.get("ErrorCode")
            except (json.JSONDecodeError, Exception):
                saxo_error_details = content_str

            logger.error(
                "Saxo API Error (async): Status=%s, Code=%s, Msg=%s, Endpoint=%s",
                status_code, error_code, error_message,
                type(endpoint_request_obj).__name__,
            )

            if status_code == 400 and error_code == "InsufficientFunds":
                raise InsufficientFundsException(
                    message=error_message or "Insufficient funds",
                    saxo_error_details=saxo_error_details,
                ) from e

            endpoint_path = getattr(endpoint_request_obj, "path", "unknown")
            is_order_endpoint = "/trade/v2/orders" in endpoint_path
            if (status_code in [400, 403, 409] or error_code) and is_order_endpoint:
                raise OrderPlacementError(
                    f"Saxo rejected order ({status_code}): {error_message}",
                    status_code=status_code,
                    saxo_error_details=saxo_error_details,
                    order_details=getattr(endpoint_request_obj, "data", None),
                ) from e

            if status_code == 401:
                # Don't keep trusting this token until the next periodic recheck.
                self._token_checked_at = 0.0
                raise TokenAuthenticationException(
                    f"API returned 401: {error_message}",
                    saxo_error_details=saxo_error_details,
                ) from e

            raise SaxoApiError(
                f"Saxo API Error ({status_code}): {error_message}",
                status_code=status_code,
                saxo_error_details=saxo_error_details,
            ) from e

        except Exception as e:
            if isinstance(e, (InsufficientFundsException, OrderPlacementError,
                              TokenAuthenticationException, SaxoApiError)):
                raise
            logger.exception("Unexpected error in async Saxo request: %s", e)
            raise ApiRequestException(
                f"Unexpected request error: {e}",
                endpoint=str(endpoint_request_obj),
            ) from e


# ──────────────────────────────────────────────
#  Async InstrumentService
# ──────────────────────────────────────────────

class AsyncInstrumentService:
    """Finds and retrieves turbo instruments asynchronously."""

    def __init__(self, api_client: AsyncSaxoApiClient, config_manager: ConfigurationManager, account_key: str):
        self.api_client = api_client
        self.config = config_manager
        self.account_key = account_key
        self.api_limits = self.config.get_config_value("trade.config.general.api_limits", {"top_instruments": 200})
        self.turbo_price_range = self.config.get_config_value("trade.config.turbo_preference.price_range", {"min": 4, "max": 15})
        self.retry_config = self.config.get_config_value("trade.config.general.retry_config", {"max_retries": 3, "retry_sleep_seconds": 1})
        self.cache_config = self.config.get_config_value("trade.config.turbo_cache", {"enabled": False, "ttl_seconds": 30})
        self._turbo_cache: dict = {}
        self._instrument_details: dict[tuple, dict] = {}

    async def get_instrument_details(self, uic: int, asset_type: str) -> dict:
        """Reference data of one instrument (supported order types, tick sizes...), fetched once."""
        key = (uic, asset_type)
        if key not in self._instrument_details:
            req = rd.instruments.InstrumentDetails(Uic=uic, AssetType=asset_type, params={"AccountKey": self.account_key})
            self._instrument_details[key] = await self.api_client.request(req) or {}
        return self._instrument_details[key]

    async def _get_infoprices_for_asset_type(
        self,
        identifiers_string: str,
        exchange_id: str,
        asset_type: str,
        field_groups: str,
        top: int | None = None,
    ):
        req = tr.infoprices.InfoPrices(
            params={
                "$top": top or len(identifiers_string.split(",")),
                "AccountKey": self.account_key,
                "ExchangeId": exchange_id,
                "FieldGroups": field_groups,
                "Uics": identifiers_string,
                "AssetType": asset_type,
            }
        )
        for attempt in range(3):
            try:
                return await self.api_client.request(req)
            except ApiRequestException:
                if attempt < 2:
                    await asyncio.sleep(1)
                else:
                    raise

    async def _fetch_candidate_quote_rows(self, exchange_id: str, instrument_groups: dict[str, list]) -> list[dict]:
        max_retries = min(self.retry_config.get("max_retries", len(INFO_PRICE_RETRY_BACKOFFS)), len(INFO_PRICE_RETRY_BACKOFFS))
        retry_backoffs = INFO_PRICE_RETRY_BACKOFFS[:max_retries]
        min_price = self.turbo_price_range["min"]
        max_price = self.turbo_price_range["max"]

        for attempt in range(max_retries):
            tasks = []
            for asset_type, instruments in instrument_groups.items():
                ids_str = ",".join(str(item["Identifier"]) for item in instruments)
                tasks.append(
                    self._get_infoprices_for_asset_type(
                        ids_str,
                        exchange_id,
                        asset_type,
                        field_groups="Quote",
                        top=len(instruments),
                    )
                )

            results = await asyncio.gather(*tasks, return_exceptions=True)
            all_data = []
            for result in results:
                if isinstance(result, Exception):
                    logger.warning("InfoPrices group failed: %s", result)
                elif result and result.get("Data"):
                    all_data.extend(result["Data"])

            if not all_data:
                logger.warning("No InfoPrice data (attempt %d/%d)", attempt + 1, max_retries)
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_backoffs[attempt])
                continue

            if any(has_tradable_bid_in_price_range(item, min_price, max_price) for item in all_data):
                return all_data

            with_quote = [item for item in all_data if "Quote" in item]
            if not with_quote:
                return all_data

            missing_bid = [item for item in with_quote if "Bid" not in item["Quote"]]
            pct_missing = (len(missing_bid) / len(with_quote)) * 100 if with_quote else 0

            if pct_missing > 50:
                logger.warning("%.1f%% missing Bid (attempt %d/%d)", pct_missing, attempt + 1, max_retries)
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_backoffs[attempt])
                continue

            return all_data

        raise NoMarketAvailableException("Failed to obtain InfoPrice data after retries.")

    async def _hydrate_candidate_snapshots(self, exchange_id: str, candidate_quote_rows: list[dict]) -> list[dict]:
        if not candidate_quote_rows:
            return []

        instrument_groups: dict[str, list] = defaultdict(list)
        for row in candidate_quote_rows:
            instrument_groups[row["AssetType"]].append(row)

        detail_tasks = []
        for asset_type, rows in instrument_groups.items():
            uics_string = ",".join(str(row["Uic"]) for row in rows if row.get("Uic") is not None)
            detail_tasks.append(
                self._get_infoprices_for_asset_type(
                    uics_string,
                    exchange_id,
                    asset_type,
                    field_groups="Commissions,DisplayAndFormat,InstrumentPriceDetails",
                    top=len(rows),
                )
            )

        detail_results = await asyncio.gather(*detail_tasks, return_exceptions=True)
        detail_rows = []
        for result in detail_results:
            if isinstance(result, Exception):
                logger.warning("Failed to load candidate details: %s", result)
            elif result and result.get("Data"):
                detail_rows.extend(result["Data"])

        return merge_detail_rows_into_candidate_snapshots(candidate_quote_rows, detail_rows)

    async def build_watchlist_candidates(self, exchange_id: str, underlying_uics: str, keywords: str) -> dict:
        logger.info(
            "Building watchlist candidates: Exchange=%s, Underlying=%s, Keywords=%s",
            exchange_id,
            underlying_uics,
            keywords,
        )

        initial_search_limit = min(self.api_limits.get("top_instruments", INITIAL_TURBO_SEARCH_LIMIT), INITIAL_TURBO_SEARCH_LIMIT)
        req = rd.instruments.Instruments(
            params={
                "$top": initial_search_limit,
                "AccountKey": self.account_key,
                "ExchangeId": exchange_id,
                "Keywords": keywords,
                "IncludeNonTradable": False,
                "UnderlyingUics": underlying_uics,
                "AssetTypes": "WarrantKnockOut,WarrantOpenEndKnockOut,MiniFuture,WarrantDoubleKnockOut",
            }
        )
        response = await self.api_client.request(req)
        if not response or not response.get("Data"):
            raise NoTurbosAvailableException("No instruments found.", search_context=req.params)

        valid_items = annotate_parsed_turbos(response["Data"])
        if not valid_items:
            raise NoTurbosAvailableException("No instruments with parsable descriptions.", search_context=req.params)

        source_lookup = build_identifier_lookup(valid_items)
        candidate_instruments = select_candidate_instruments(valid_items, keywords)
        if not candidate_instruments:
            raise NoTurbosAvailableException("No identifiers found after sorting.", search_context=req.params)

        candidate_groups = group_instruments_by_asset_type(candidate_instruments)
        candidate_quote_rows = await self._fetch_candidate_quote_rows(exchange_id, candidate_groups)
        candidate_snapshots = await self._hydrate_candidate_snapshots(exchange_id, candidate_quote_rows)

        selected_result = None
        try:
            selected_result = build_selected_turbo_from_candidate_snapshots(
                exchange_id=exchange_id,
                underlying_uics=underlying_uics,
                keywords=keywords,
                candidate_snapshots=candidate_snapshots,
                selected_source_lookup=source_lookup,
                min_price=self.turbo_price_range["min"],
                max_price=self.turbo_price_range["max"],
            )
        except (NoMarketAvailableException, NoTurbosAvailableException) as exc:
            logger.info(
                "Watchlist candidates built without an immediately tradable winner for %s/%s: %s",
                underlying_uics,
                keywords,
                exc,
            )

        subscription_groups = []
        snapshots_by_asset_type = group_instruments_by_asset_type(candidate_snapshots)
        for asset_type, snapshots in snapshots_by_asset_type.items():
            subscription_groups.append(
                {
                    "asset_type": asset_type,
                    "uics": [snapshot["Uic"] for snapshot in snapshots if snapshot.get("Uic") is not None],
                }
            )

        return {
            "input_criteria": {
                "exchange_id": exchange_id,
                "underlying_uics": underlying_uics,
                "keywords": keywords,
            },
            "selected_result": selected_result,
            "candidate_snapshots": candidate_snapshots,
            "selected_source_lookup": source_lookup,
            "subscription_groups": subscription_groups,
        }

    async def find_turbos(self, exchange_id: str, underlying_uics: str, keywords: str) -> dict:
        """Finds turbos — uses cache if enabled, otherwise fetches fresh."""
        if self.cache_config.get("enabled", False):
            cache_key = (exchange_id, underlying_uics, keywords)
            cached = self._turbo_cache.get(cache_key)
            if cached:
                age = time.time() - cached["timestamp"]
                ttl = self.cache_config.get("ttl_seconds", 30)
                if age < ttl:
                    logger.info("Turbo cache HIT (age=%.1fs)", age)
                    return cached["result"]

        result = await self._find_turbos_uncached(exchange_id, underlying_uics, keywords)

        if self.cache_config.get("enabled", False):
            self._turbo_cache[(exchange_id, underlying_uics, keywords)] = {
                "result": result,
                "timestamp": time.time(),
            }
        return result

    async def _find_turbos_uncached(self, exchange_id: str, underlying_uics: str, keywords: str) -> dict:
        logger.info("Finding turbos: Exchange=%s, Underlying=%s, Keywords=%s", exchange_id, underlying_uics, keywords)

        # 1. Instrument search
        initial_search_limit = min(self.api_limits.get("top_instruments", INITIAL_TURBO_SEARCH_LIMIT), INITIAL_TURBO_SEARCH_LIMIT)
        req = rd.instruments.Instruments(
            params={
                "$top": initial_search_limit,
                "AccountKey": self.account_key,
                "ExchangeId": exchange_id,
                "Keywords": keywords,
                "IncludeNonTradable": False,
                "UnderlyingUics": underlying_uics,
                "AssetTypes": "WarrantKnockOut,WarrantOpenEndKnockOut,MiniFuture,WarrantDoubleKnockOut",
            }
        )
        response = await self.api_client.request(req)
        if not response or not response.get("Data"):
            raise NoTurbosAvailableException("No instruments found.", search_context=req.params)

        # 2. Parse & filter
        valid_items = annotate_parsed_turbos(response["Data"])

        if not valid_items:
            raise NoTurbosAvailableException("No instruments with parsable descriptions.", search_context=req.params)

        initial_instruments_by_identifier = build_identifier_lookup(valid_items)

        # 3. Sort
        candidate_instruments = select_candidate_instruments(valid_items, keywords)
        if not candidate_instruments:
            raise NoTurbosAvailableException("No identifiers found after sorting.", search_context=req.params)

        # 4. Group by AssetType
        instrument_groups = group_instruments_by_asset_type(candidate_instruments)

        # 5. Fetch quote-only InfoPrices for the top candidates
        max_retries = min(self.retry_config.get("max_retries", len(INFO_PRICE_RETRY_BACKOFFS)), len(INFO_PRICE_RETRY_BACKOFFS))
        retry_backoffs = INFO_PRICE_RETRY_BACKOFFS[:max_retries]
        min_price = self.turbo_price_range["min"]
        max_price = self.turbo_price_range["max"]

        response_infoprices = None
        for attempt in range(max_retries):
            tasks = []
            for asset_type, instruments in instrument_groups.items():
                ids_str = ",".join(str(i["Identifier"]) for i in instruments)
                tasks.append(
                    self._get_infoprices_for_asset_type(
                        ids_str,
                        exchange_id,
                        asset_type,
                        field_groups="Quote",
                        top=len(instruments),
                    )
                )

            results = await asyncio.gather(*tasks, return_exceptions=True)
            all_data = []
            for r in results:
                if isinstance(r, Exception):
                    logger.warning("InfoPrices group failed: %s", r)
                elif r and r.get("Data"):
                    all_data.extend(r["Data"])

            if not all_data:
                logger.warning("No InfoPrice data (attempt %d/%d)", attempt + 1, max_retries)
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_backoffs[attempt])
                continue

            has_valid_candidate = any(
                has_tradable_bid_in_price_range(item, min_price, max_price)
                for item in all_data
            )
            
            if has_valid_candidate:
                response_infoprices = {"Data": all_data}
                break

            # Check bid availability
            with_quote = [i for i in all_data if "Quote" in i]
            if not with_quote:
                break  # No quotes at all — nothing to retry for

            missing_bid = [i for i in with_quote if "Bid" not in i["Quote"]]
            pct_missing = (len(missing_bid) / len(with_quote)) * 100 if with_quote else 0

            if pct_missing > 50:
                logger.warning("%.1f%% missing Bid (attempt %d/%d)", pct_missing, attempt + 1, max_retries)
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_backoffs[attempt])
                continue

            response_infoprices = {"Data": all_data}
            break

        if not response_infoprices or not response_infoprices.get("Data"):
            raise NoMarketAvailableException("Failed to obtain InfoPrice data after retries.")

        # Filter items with valid Bid
        valid_bid_items = filter_items_with_valid_bid(response_infoprices["Data"])
        if not valid_bid_items:
            raise NoMarketAvailableException("No instruments with Bid data after filtering.")

        # 6. Market state filter
        available_items = filter_tradable_market_items(valid_bid_items)
        if not available_items:
            raise NoMarketAvailableException(f"No markets available for {keywords} in {exchange_id}.")

        # 7. Price range filter
        price_filtered = filter_items_in_price_range(available_items, min_price, max_price)
        if not price_filtered:
            raise NoTurbosAvailableException(
                f"No turbos in price range {min_price}-{max_price}.",
                search_context={"PriceRange": (min_price, max_price), "AvailableCount": len(available_items)},
            )

        # 8. Select best
        selected = select_best_price_candidate(price_filtered)
        selected_source = initial_instruments_by_identifier.get(selected.get("Identifier"), {})
        detail_response = await self._get_infoprices_for_asset_type(
            str(selected["Uic"]),
            exchange_id,
            selected["AssetType"],
            field_groups="Commissions,DisplayAndFormat,InstrumentPriceDetails",
            top=1,
        )
        detail_rows = (detail_response or {}).get("Data") or []
        detail_snapshot = detail_rows[0] if detail_rows else {}
        return build_selected_turbo_result(
            exchange_id=exchange_id,
            underlying_uics=underlying_uics,
            keywords=keywords,
            selected=selected,
            selected_source=selected_source,
            detail_snapshot=detail_snapshot,
        )


# ──────────────────────────────────────────────
#  Async OrderService
# ──────────────────────────────────────────────

class AsyncOrderService:
    def __init__(self, api_client: AsyncSaxoApiClient, account_key: str, client_key: str):
        self.api_client = api_client
        self.account_key = account_key
        self.client_key = client_key

    async def place_market_order(self, uic: int, asset_type: str, amount: int, buy_sell: str) -> dict:
        logger.info("Placing Market Order: %s %d of %d (%s)", buy_sell, amount, uic, asset_type)
        pre_order = MarketOrder(Uic=uic, AssetType=asset_type, Amount=amount, BuySell=buy_sell)
        final_payload = tie_account_to_order(self.account_key, pre_order)
        req = tr.orders.Order(data=final_payload)

        try:
            result = await self.api_client.request(req)
        except SaxoApiError as e:
            raise OrderPlacementError(
                f"API error placing order: {e}",
                saxo_error_details=e.saxo_error_details,
                order_details=final_payload,
            ) from e

        if not result or not result.get("OrderId"):
            raise OrderPlacementError("Response missing OrderId.", order_details=final_payload, saxo_error_details=result)

        logger.info("Order placed — OrderId: %s", result["OrderId"])
        return result

    async def place_related_stop_order(
        self,
        position_id: str,
        uic: int,
        asset_type: str,
        amount: int,
        stop_price: float,
        order_type: str,
    ) -> str:
        """
        Attach a resting stop order to an open position and return its OrderId.

        Being related to the position, it is listed in the position's
        ``RelatedOpenOrders`` and is sized to close it.
        """
        logger.info("Placing %s at %s on position %s (%d of %d)", order_type, stop_price, position_id, amount, uic)
        stop_order = {
            "Uic": uic,
            "AssetType": asset_type,
            "Amount": abs(amount),
            "BuySell": direction_invert(direction_from_amount(amount)),
            "OrderType": order_type,
            "OrderPrice": stop_price,
            "OrderDuration": {"DurationType": "GoodTillCancel"},
            "ManualOrder": False,
        }
        final_payload = tie_account_to_order(self.account_key, {"PositionId": position_id, "Orders": [stop_order]})
        result = await self.api_client.request(tr.orders.Order(data=final_payload)) or {}

        order_ids = [result.get("OrderId")] + [related.get("OrderId") for related in result.get("Orders") or []]
        order_id = next((order_id for order_id in order_ids if order_id), None)
        if not order_id:
            raise OrderPlacementError("Response missing OrderId.", order_details=final_payload, saxo_error_details=result)

        logger.info("Stop order placed on position %s — OrderId: %s", position_id, order_id)
        return order_id

    async def cancel_order(self, order_id: str) -> bool:
        logger.info("Cancelling order: %s", order_id)
        req = tr.orders.CancelOrders(OrderIds=order_id, params={"AccountKey": self.account_key})
        try:
            await self.api_client.request(req)
            logger.info("Order %s cancelled.", order_id)
            return True
        except Exception as e:
            logger.error("Failed to cancel order %s: %s", order_id, e)
            return False


# ──────────────────────────────────────────────
#  Async PositionService
# ──────────────────────────────────────────────

class AsyncPositionService:
    def __init__(self, api_client: AsyncSaxoApiClient, order_service: AsyncOrderService,
                 config_manager: ConfigurationManager, account_key: str, client_key: str):
        self.api_client = api_client
        self.order_service = order_service
        self.config = config_manager
        self.account_key = account_key
        self.client_key = client_key
        self.api_limits = self.config.get_config_value("trade.config.general.api_limits", {"top_positions": 200, "top_closed_positions": 500})
        retry_cfg = self.config.get_config_value("trade.config.general.retry_config", {"max_retries": 5, "retry_sleep_seconds": 2})
        self.max_retries = retry_cfg["max_retries"]
        self.retry_sleep = retry_cfg["retry_sleep_seconds"]

    async def get_open_positions(self) -> dict:
        req = pf.positions.PositionsMe(
            params={
                "ClientKey": self.client_key,
                "AccountKey": self.account_key,
                "FieldGroups": "PositionBase,PositionView,DisplayAndFormat,ExchangeInfo",
            }
        )
        response = await self.api_client.request(req)
        if response and "Data" in response and "__count" not in response:
            response["__count"] = len(response["Data"])
        elif not response:
            return {"__count": 0, "Data": []}
        return response

    async def get_closed_positions(self, top: int | None = None, skip: int = 0) -> dict:
        if top is None:
            top = self.api_limits["top_closed_positions"]
        req = pf.closedpositions.ClosedPositionsMe(
            params={
                "$top": top, "$skip": skip,
                "AccountKey": self.account_key,
                "FieldGroups": "ClosedPosition,ClosedPositionDetails,DisplayAndFormat,ExchangeInfo",
            }
        )
        response = await self.api_client.request(req)
        return response if response else {"__count": 0, "Data": []}

    async def get_single_position(self, position_id: str) -> dict:
        req = pf.positions.SinglePosition(
            PositionId=position_id,
            params={
                "ClientKey": self.client_key,
                "AccountKey": self.account_key,
                "FieldGroups": "PositionBase,PositionView,DisplayAndFormat,Costs,ExchangeInfo",
            },
        )
        return await self.api_client.request(req)

    async def find_position_by_order_id_with_retry(self, order_id: str) -> dict:
        """
        Finds position, checking straight away and then with exponential backoff
        (non-blocking).
        Attempts order cancellation if not found after all retries.
        """
        # Time allowed per configured retry (~10.8s total max). It is spent as an
        # immediate check followed by short, growing waits: Saxo usually lists the
        # position within milliseconds of accepting a market order.
        allowances = [0.3, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0]
        waits = [0, *backoff_gaps(sum(allowances[:self.max_retries]))]
        max_attempts = len(waits)

        for attempt in range(max_attempts):
            if waits[attempt]:
                await asyncio.sleep(waits[attempt])
            positions = await self.get_open_positions()
            for p in positions.get("Data", []):
                if p.get("PositionBase", {}).get("SourceOrderId") == order_id:
                    logger.info("Position found for order %s after %d attempt(s).", order_id, attempt + 1)
                    return p
            logger.debug("Position not found for order %s (attempt %d/%d)", order_id, attempt + 1, max_attempts)

        # All retries exhausted — attempt cancellation
        logger.critical("Position not found for order %s after %d retries. Cancelling.", order_id, max_attempts)
        cancelled = await self.order_service.cancel_order(order_id)
        raise PositionNotFoundException(
            f"Position not found after {max_attempts} retries for order {order_id}",
            order_id=order_id,
            cancellation_attempted=True,
            cancellation_succeeded=cancelled,
        )

    async def get_spending_power(self) -> float:
        req = pf.balances.AccountBalances(params={"ClientKey": self.client_key})
        resp = await self.api_client.request(req)
        if not resp or "SpendingPower" not in resp:
            raise SaxoApiError("Invalid balance response, missing SpendingPower.")
        power = resp["SpendingPower"]
        logger.debug("Spending power: %s", power)
        return float(power)

    async def get_current_account_balance(self) -> float:
        """Fetch the current total account value (cash + open positions) for reporting."""
        req = pf.balances.AccountBalances(params={"ClientKey": self.client_key})
        resp = await self.api_client.request(req)
        if not resp or "TotalValue" not in resp:
            raise SaxoApiError("Invalid balance response, missing TotalValue.")
        total_value = resp["TotalValue"]
        logger.info("Current account balance: %s", total_value)
        return float(total_value)


# ──────────────────────────────────────────────
#  Async TradingOrchestrator
# ──────────────────────────────────────────────

class AsyncTradingOrchestrator:
    """
    Orchestrates the full trade-execution flow asynchronously.
    Key improvement: find_turbos and get_spending_power run in parallel.
    """

    def __init__(
        self,
        instrument_service: AsyncInstrumentService,
        order_service: AsyncOrderService,
        position_service: AsyncPositionService,
        config_manager: ConfigurationManager,
        db_order_manager: AsyncDbOrderManager,
        db_position_manager: AsyncDbPositionManager,
        watchlist_client=None,
    ):
        self.instrument_service = instrument_service
        self.order_service = order_service
        self.position_service = position_service
        self.config = config_manager
        self.db_order_manager = db_order_manager
        self.db_position_manager = db_position_manager
        self.watchlist_client = watchlist_client
        self.buying_power_config = self.config.get_config_value("trade.config.buying_power", {})
        self.safety_margins = self.buying_power_config.get("safety_margins", {"bid_calculation": 1})
        self.reserve_cash_percent = self.buying_power_config.get("reserve_cash_percent", 0)
        # 0 = always ask Saxo for the balance when sizing an order.
        self.balance_cache_max_age_seconds = self.buying_power_config.get("balance_cache_max_age_seconds", 0)
        self._cached_spending_power: tuple[float, float] | None = None  # (value, monotonic time)
        self.protective_stop_config = self.config.get_config_value(
            "trade.config.position_management.protective_stop", {"enabled": False}
        )
        self.timezone = self.config.get_config_value("trade.config.general.timezone", "Europe/Paris")
        self.time_of_day_config = self.config.get_config_value("trade.config.position_sizing.time_of_day_scaling", {"enabled": False})
        self.confidence_config = self.config.get_config_value("trade.config.position_sizing.confidence_scaling", {"enabled": False})

    # ── Position sizing helpers (same logic, kept sync as they're pure computation) ──

    def _get_time_of_day_scale(self) -> float:
        if not self.time_of_day_config.get("enabled", False):
            return 100.0
        current_time = datetime.now(pytz.timezone(self.timezone))
        current_minutes = current_time.hour * 60 + current_time.minute
        for period in self.time_of_day_config.get("periods", []):
            start_m = period["start_hour"] * 60 + period.get("start_minute", 0)
            end_m = period["end_hour"] * 60 + period.get("end_minute", 0)
            if start_m <= current_minutes < end_m:
                return period.get("scale_percent", 100)
        return 100.0

    def _get_confidence_scale(self, confidence) -> float:
        if not self.confidence_config.get("enabled", False):
            return 100.0
        if confidence is None:
            confidence = self.confidence_config.get("default_confidence", 1.0)
        for rule in self.confidence_config.get("scaling_rules", []):
            if rule["min_confidence"] <= confidence < rule["max_confidence"]:
                return rule.get("scale_percent", 100)
        return 100.0

    def _calculate_position_scale(self, confidence=None) -> float:
        tod = self._get_time_of_day_scale() / 100.0
        conf = self._get_confidence_scale(confidence) / 100.0
        combined = tod * conf
        logger.info("Position scale: tod=%.2f × conf=%.2f = %.2f", tod, conf, combined)
        return combined

    def _calculate_bid_amount(self, turbo_info: dict, spending_power: float, position_scale: float = 1.0) -> int:
        ask_price = turbo_info["selected_instrument"].get("latest_ask")
        if ask_price is None:
            ask_price = turbo_info["selected_instrument"].get("quote", {}).get("Ask")
        if not ask_price or ask_price <= 0:
            raise ValueError(f"Invalid ask price: {ask_price}")

        reserve = self.reserve_cash_percent
        max_pct = self.buying_power_config.get("max_account_funds_to_use_percentage", 100)
        effective_pct = max(0, max_pct - reserve)
        available = spending_power * (effective_pct / 100.0) * position_scale

        safety = self.safety_margins.get("bid_calculation", 1)
        required = ask_price * (1 + safety)
        if available < required:
            pre = 0
        else:
            pre = (available / ask_price) - safety

        amount = int(math.floor(pre))
        if amount <= 0:
            raise InsufficientFundsException(
                f"Insufficient funds @ {ask_price}",
                available_funds=available,
                required_price=ask_price,
                calculated_amount=amount,
            )
        logger.info("Calculated bid amount: %d (available=%.2f, ask=%.4f, scale=%.2f)", amount, available, ask_price, position_scale)
        return amount

    async def _get_watchlist_turbo(self, underlying_uics: str, keywords: str) -> dict | None:
        if self.watchlist_client is None:
            return None

        turbo_info = await self.watchlist_client.get_best_turbo(
            underlying_uic=underlying_uics,
            direction=keywords,
        )
        if turbo_info is not None:
            logger.info("Using watchlist-managed turbo for underlying=%s direction=%s", underlying_uics, keywords)
        return turbo_info

    async def place_protective_stop(self, trade_result: dict) -> dict | None:
        """
        Attach a resting sell stop at the broker to the position just opened, when
        ``position_management.protective_stop`` is enabled.

        It is a safety net for when the position monitor can't act (outage,
        stream down). Set wider than the monitor's own stop-loss, it only
        triggers if the monitor didn't close the position first.

        Never raises. Returns None when disabled, otherwise the order id and
        price, or the reason why no stop could be placed.
        """
        if not self.protective_stop_config.get("enabled", False):
            return None

        position = trade_result["position_details"]
        instrument = trade_result["selected_turbo_info"]["selected_instrument"]
        position_id = position["position_id"]
        percent = self.protective_stop_config.get("percent")
        order_type = self.protective_stop_config.get("order_type", "StopIfTraded")

        try:
            details = await self.instrument_service.get_instrument_details(instrument["uic"], instrument["asset_type"])
            supported_order_types = details.get("SupportedOrderTypes") or []
            if order_type not in supported_order_types:
                raise ValueError(f"{order_type} orders are not supported on this instrument (supported: {supported_order_types})")

            target_price = position["position_open_price"] * (1 + percent / 100.0)
            tick_size = stop_order_tick_size(details, target_price) or 10 ** -instrument.get("decimals", 2)
            stop_price = round_down_to_tick(target_price, tick_size)
            if stop_price <= 0:
                raise ValueError(f"stop price {stop_price} is not a valid price")

            order_id = await self.order_service.place_related_stop_order(
                position_id=position_id,
                uic=instrument["uic"],
                asset_type=instrument["asset_type"],
                amount=position["position_amount"],
                stop_price=stop_price,
                order_type=order_type,
            )
        except Exception as e:
            logger.error("Protective stop NOT placed on position %s: %s", position_id, e)
            return {"error": f"{type(e).__name__}: {e}"}

        try:
            await self.db_position_manager.update_turbo_position_data(position_id, {"related_order_id": [order_id]})
        except Exception as e:
            logger.warning("Could not record protective stop %s of position %s: %s", order_id, position_id, e)

        return {"order_id": order_id, "price": stop_price, "percent": percent}

    async def refresh_spending_power(self) -> float:
        """Fetch the spending power from Saxo and remember it for the balance cache."""
        spending_power = await self.position_service.get_spending_power()
        self._cached_spending_power = (spending_power, time.monotonic())
        return spending_power

    def invalidate_spending_power(self):
        """Forget the cached spending power (call after anything that moves cash)."""
        self._cached_spending_power = None

    async def _get_spending_power(self) -> float:
        cached = self._cached_spending_power
        if (
            cached is not None
            and self.balance_cache_max_age_seconds > 0
            and time.monotonic() - cached[1] <= self.balance_cache_max_age_seconds
        ):
            logger.info("Using cached spending power %s (age=%.1fs)", cached[0], time.monotonic() - cached[1])
            return cached[0]
        return await self.refresh_spending_power()

    async def execute_trade_signal(self, exchange_id: str, underlying_uics: str, keywords: str, confidence: float = None) -> dict:
        """
        Full async trade workflow.
        Key optimisation: the turbo selection and get_spending_power run **in parallel**.
        """
        logger.info("--- Executing trade signal: %s on %s (conf=%s) ---", keywords, underlying_uics, confidence)
        timestamps = {"start": time.time()}
        validated_order = None
        confirmed_position = None
        turbo_info = None

        try:
            # 0. Position scale (instant)
            position_scale = self._calculate_position_scale(confidence)
            timestamps["scale_calculated"] = time.time()

            # 1. Try the fast watchlist path first, then fall back to the live Saxo search.
            async def _select_turbo():
                selected = await self._get_watchlist_turbo(underlying_uics, keywords)
                if selected is None:
                    selected = await self.instrument_service.find_turbos(exchange_id, underlying_uics, keywords)
                timestamps["turbo_found"] = time.time()
                return selected

            # 2. Fetch spending power in parallel with the turbo selection.
            turbo_info, spending_power = await asyncio.gather(
                _select_turbo(),
                self._get_spending_power(),
            )
            timestamps["spending_power_fetched"] = time.time()

            # 3. Calculate amount
            amount = self._calculate_bid_amount(turbo_info, spending_power, position_scale)
            timestamps["amount_calculated"] = time.time()

            # 4. Place order
            validated_order = await self.order_service.place_market_order(
                uic=turbo_info["selected_instrument"]["uic"],
                asset_type=turbo_info["selected_instrument"]["asset_type"],
                amount=amount,
                buy_sell="Buy",
            )
            self.invalidate_spending_power()
            order_id = validated_order["OrderId"]
            timestamps["order_placed"] = time.time()

            # 5. Confirm position (exponential backoff)
            confirmed_position = await self.position_service.find_position_by_order_id_with_retry(order_id)
            timestamps["position_confirmed"] = time.time()

            # 6. Persist to PostgreSQL
            now_utc = datetime.now(pytz.utc)
            order_data = {
                "action": keywords, "buy_sell": "Buy", "order_id": order_id,
                "order_amount": amount, "order_type": "Market", "order_kind": "main",
                "order_submit_time": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "related_order_id": [], "position_id": confirmed_position.get("PositionId"),
                "instrument_name": turbo_info["selected_instrument"]["description"],
                "instrument_symbol": turbo_info["selected_instrument"]["symbol"],
                "instrument_uic": turbo_info["selected_instrument"]["uic"],
                "instrument_price": turbo_info["selected_instrument"].get("latest_ask"),
                "instrument_currency": turbo_info["selected_instrument"]["currency"],
                "order_cost": turbo_info["selected_instrument"].get("commissions", {}).get("CostBuy"),
            }
            pos_base = confirmed_position.get("PositionBase", {})
            pos_disp = confirmed_position.get("DisplayAndFormat", {})
            position_data = {
                "action": keywords, "position_id": confirmed_position.get("PositionId"),
                "position_amount": pos_base.get("Amount"),
                "position_open_price": pos_base.get("OpenPrice"),
                "position_total_open_price": (pos_base.get("Amount", 0) * pos_base.get("OpenPrice", 0)),
                "position_status": pos_base.get("Status", "Open"), "position_kind": "main",
                "execution_time_open": pos_base.get("ExecutionTimeOpen"),
                "order_id": pos_base.get("SourceOrderId"),
                "related_order_id": pos_base.get("RelatedOpenOrders", []),
                "instrument_name": pos_disp.get("Description"),
                "instrument_symbol": pos_disp.get("Symbol"),
                "instrument_uic": pos_base.get("Uic"),
                "instrument_currency": pos_disp.get("Currency"),
            }

            try:
                await asyncio.gather(
                    self.db_order_manager.insert_turbo_order_data(order_data),
                    self.db_position_manager.insert_turbo_open_position_data(position_data),
                )
            except Exception as db_err:
                logger.critical("CRITICAL DB ERROR after execution! Order=%s. %s", order_id, db_err, exc_info=True)
                raise DatabaseOperationException(
                    f"Failed to persist trade {order_id}", operation="insert_trade_data", entity_id=order_id
                ) from db_err

            timestamps["db_persisted"] = time.time()
            timing = self._build_timing_summary(timestamps)
            logger.info("Trade execution complete. Timing: %s", timing)
            self._log_execution_timing(keywords, timestamps, confidence, position_scale)

            return {
                "order_details": order_data,
                "position_details": position_data,
                "selected_turbo_info": turbo_info,
                "execution_timing": timing,
                "raw_timestamps": timestamps,
                "position_scale": position_scale,
                "confidence": confidence,
                "message": f"Successfully executed trade for {keywords}.",
            }

        except PositionNotFoundException:
            raise
        except Exception as e:
            if validated_order and not confirmed_position:
                oid = validated_order.get("OrderId")
                if oid:
                    logger.warning("Cancelling orphan order %s due to failure: %s", oid, e)
                    await self.order_service.cancel_order(oid)
            raise

    @staticmethod
    def _build_timing_summary(timestamps: dict) -> dict:
        summary = {}
        start = timestamps.get("start")
        if not start:
            return summary
        steps = [
            ("scale_calculated", "Scale Calc"),
            ("turbo_found", "Find Turbo"),
            ("spending_power_fetched", "Get Balance"),
            ("amount_calculated", "Calc Amount"),
            ("order_placed", "Place Order"),
            ("position_confirmed", "Confirm Pos"),
            ("db_persisted", "DB Persist"),
        ]
        prev = start
        for key, label in steps:
            ts = timestamps.get(key)
            if ts:
                summary[label] = f"{(ts - prev) * 1000:.0f}ms"
                prev = ts
        total = timestamps.get("db_persisted", timestamps.get("position_confirmed", start))
        summary["TOTAL"] = f"{(total - start) * 1000:.0f}ms"
        return summary

    def _log_execution_timing(self, action, timestamps, confidence, position_scale):
        try:
            tz = pytz.timezone(self.timezone)
            now = datetime.now(tz)
            log_path = self.config.get_config_value("logging.persistant.log_path", ".")
            os.makedirs(log_path, exist_ok=True)
            data = {
                "action": action, "confidence": confidence,
                "position_scale": position_scale,
                "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
                "steps": {k: round((v - timestamps["start"]) * 1000) for k, v in timestamps.items() if k != "start"},
            }
            path = os.path.join(log_path, f"execution_timing_{now.strftime('%Y-%m-%d')}.jsonl")
            with open(path, "a") as f:
                f.write(json.dumps(data) + "\n")
        except Exception as e:
            logger.error("Failed to log execution timing: %s", e)


# ──────────────────────────────────────────────
#  Async PerformanceMonitor
# ──────────────────────────────────────────────

class AsyncPerformanceMonitor:
    """
    Monitors open positions, checks SL/TP/trailing stop, syncs DB.
    Designed for the Position Monitor service (separate queue).
    """

    def __init__(
        self,
        position_service: AsyncPositionService,
        order_service: AsyncOrderService,
        config_manager: ConfigurationManager,
        db_position_manager: AsyncDbPositionManager,
        trading_rule,  # TradingRule instance (sync is fine — it's pure computation)
        send_telegram_fn,  # async callable(message: str) -> None
        trigger_daily_stats_fn=None,  # optional async callable() -> None
    ):
        self.position_service = position_service
        self.order_service = order_service
        self.config = config_manager
        self.db_position_manager = db_position_manager
        self.trading_rule = trading_rule
        self.send_telegram = send_telegram_fn
        self.trigger_daily_stats = trigger_daily_stats_fn
        self._last_daily_stats_trigger_date = None
        # Positions with a sell order sent whose closure isn't recorded in the DB
        # yet; they still read as open there and must not be sold a second time.
        self._closing_position_ids: set[str] = set()
        # Strong references to background bookkeeping (the loop only holds weak ones).
        self._background_tasks: set[asyncio.Task] = set()
        # What the DB last said about managed positions (see _load_managed_state).
        self._last_managed_positions: list[dict] | None = None
        self._last_today_pct: float | None = None
        # Highest performance seen per open position. Only position checks write
        # it, so it is read from the DB once and then kept here; values the DB
        # hasn't accepted yet wait in the pending map.
        self._max_perf_by_position: dict[str, float] = {}
        self._max_perf_pending_save: dict[str, float] = {}
        # Latest (bid, performance percent, observed at) per open position, kept
        # for the admin interface: see live_values(). Nothing here reads it back.
        self._live_values: dict[str, tuple[float, float, datetime]] = {}
        self._db_unavailable_until = 0.0
        self.perf_config = self.config.get_config_value("trade.config.position_management", {})
        self.thresholds = self.perf_config.get("performance_thresholds", {"stoploss_percent": -15, "max_profit_percent": 60})
        self.trailing_stop_config = self.thresholds.get("trailing_stop", {"enabled": False, "activation_percent": 5, "drawdown_percent": 8})
        self.timezone = self.config.get_config_value("trade.config.general.timezone", "Europe/Paris")
        self.logging_config = self.config.get_logging_config()
        try:
            day_cfg = self.trading_rule.get_rule_config("day_trading")
            self.percent_profit_wanted = day_cfg.get("percent_profit_wanted_per_days", 1.0)
        except Exception:
            self.percent_profit_wanted = 1.0

    async def check_positions_from_stream(self, streamed_positions: dict[str, dict]) -> dict:
        """
        Check performance using **pre-fetched** position data from the
        WebSocket stream instead of polling the REST API.

        Parameters
        ----------
        streamed_positions : dict[str, dict]
            Mapping of ``{PositionId: merged_position_dict}`` as maintained
            by :class:`SaxoStreamClient`.

        Returns the same structure as :meth:`check_all_positions_performance`.
        """
        return await self._evaluate_positions(streamed_positions)

    async def check_all_positions_performance(self) -> dict:
        """Check all open positions, close if thresholds hit, update max perf."""
        logger.info("--- Checking performance of open positions ---")
        db_positions = await self.db_position_manager.get_open_positions_ids_actions()
        if not db_positions:
            logger.info("No open positions to check.")
            return {"closed_positions_processed": [], "db_updates": [], "errors": 0}

        try:
            api_resp = await self.position_service.get_open_positions()
            api_dict = {p["PositionId"]: p for p in api_resp.get("Data", [])}
        except Exception as e:
            logger.error("Failed to get API positions: %s", e)
            return {"closed_positions_processed": [], "db_updates": [], "errors": 1}

        return await self._evaluate_positions(api_dict)

    def live_values(self) -> dict[str, tuple[float, float, datetime]]:
        """Latest observed (bid, performance percent, observed at) of each open position."""
        return dict(self._live_values)

    def forget_live_values_except(self, position_ids) -> None:
        """Drop what was observed for positions that are no longer open."""
        for pid in list(self._live_values):
            if pid not in position_ids:
                del self._live_values[pid]

    def _db_usable(self) -> bool:
        return time.monotonic() >= self._db_unavailable_until

    def _mark_db_unavailable(self):
        self._db_unavailable_until = time.monotonic() + DB_RETRY_AFTER_FAILURE_SECONDS

    async def _load_managed_state(self) -> tuple[list[dict], float | None]:
        """
        Read the open managed positions and today's realized percent from the DB.

        When the DB fails or doesn't answer in time, the values read last are
        returned instead (the very first read has nothing to fall back on).
        """
        if not self._db_usable() and self._last_managed_positions is not None:
            return self._last_managed_positions, self._last_today_pct

        try:
            db_positions, today_pct = await asyncio.wait_for(
                asyncio.gather(
                    self.db_position_manager.get_open_positions_ids_actions(),
                    self.db_position_manager.get_percent_of_the_day(),
                    return_exceptions=True,
                ),
                MANAGED_STATE_DB_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as e:
            db_positions = today_pct = e

        if isinstance(db_positions, Exception):
            self._mark_db_unavailable()
            if self._last_managed_positions is None:
                raise db_positions
            logger.warning("Could not read open positions from DB (%r); using the last known ones.", db_positions)
            db_positions = self._last_managed_positions
        else:
            self._last_managed_positions = db_positions

        if isinstance(today_pct, Exception):
            logger.warning("Could not read today's realized percent from DB (%r); using the last known one.", today_pct)
            today_pct = self._last_today_pct
        else:
            self._last_today_pct = today_pct

        return db_positions, today_pct

    async def _get_max_perf(self, position_id: str) -> float | None:
        """Highest performance seen for a position so far (None while it can't be read)."""
        if position_id not in self._max_perf_by_position:
            if not self._db_usable():
                return None
            try:
                self._max_perf_by_position[position_id] = await asyncio.wait_for(
                    self.db_position_manager.get_max_position_percent(position_id),
                    MANAGED_STATE_DB_TIMEOUT_SECONDS,
                )
            except Exception as e:
                self._mark_db_unavailable()
                logger.warning("Could not read max performance of %s: %r", position_id, e)
                return None
        return self._max_perf_by_position[position_id]

    async def _save_pending_max_perf(self) -> tuple[list, int]:
        """Write to the DB the max performances it doesn't have yet; returns (saved, error count)."""
        saved = []
        for pid, max_perf in list(self._max_perf_pending_save.items()):
            if not self._db_usable():
                break
            data = {"position_max_performance_percent": max_perf}
            try:
                await asyncio.wait_for(
                    self.db_position_manager.update_turbo_position_data(pid, data),
                    MANAGED_STATE_DB_TIMEOUT_SECONDS,
                )
            except Exception as e:
                self._mark_db_unavailable()
                logger.error("Failed max-perf update for %s: %r", pid, e)
                return saved, 1
            saved.append((pid, data))
            if self._max_perf_pending_save.get(pid) == max_perf:
                del self._max_perf_pending_save[pid]
        return saved, 0

    async def _record_loss(self):
        try:
            await self.trading_rule.record_loss()
        except Exception:
            pass

    def _run_in_background(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _evaluate_positions(self, api_dict: dict[str, dict]) -> dict:
        """
        Shared evaluation logic used by both REST-polled and stream-fed paths.

        Sell orders for positions that hit a threshold are sent before this
        returns; recording those closures in the DB happens in the background.

        Parameters
        ----------
        api_dict : dict[str, dict]
            ``{PositionId: position_dict}`` — from either API response or stream cache.
        """
        db_positions, today_pct = await self._load_managed_state()
        managed_ids = {db_pos["position_id"] for db_pos in db_positions}
        for known in (self._max_perf_by_position, self._max_perf_pending_save, self._live_values):
            for pid in list(known):
                if pid not in managed_ids:
                    del known[pid]

        if not db_positions:
            logger.info("No open positions to check.")
            return {"closed_positions_processed": [], "db_updates": [], "errors": 0}

        positions_to_close = []
        errors = 0

        for db_pos in db_positions:
            pid = db_pos["position_id"]
            if pid not in api_dict or pid in self._closing_position_ids:
                continue

            api_pos = api_dict[pid]
            open_price = api_pos.get("PositionBase", {}).get("OpenPrice")
            current_bid = api_pos.get("PositionView", {}).get("Bid")
            if not open_price or not current_bid or open_price == 0:
                continue

            perf_pct = round(((current_bid * 100) / open_price) - 100, 2)
            logger.info("Pos %s: Open=%.4f, Bid=%.4f, Perf=%.2f%%", pid, open_price, current_bid, perf_pct)
            self._live_values[pid] = (current_bid, perf_pct, datetime.now(pytz.utc))

            self._log_performance_detail(pid, api_pos, perf_pct)

            max_perf = await self._get_max_perf(pid)
            if max_perf is not None and perf_pct > max_perf:
                self._max_perf_by_position[pid] = perf_pct
                self._max_perf_pending_save[pid] = perf_pct

            close_reason = None

            # Stop-loss
            if perf_pct <= self.thresholds["stoploss_percent"]:
                close_reason = f"Stoploss ({self.thresholds['stoploss_percent']}%) hit at {perf_pct}%"
                # Not awaited: the sell must not wait for this DB write.
                self._run_in_background(self._record_loss())

            # Take-profit
            elif perf_pct >= self.thresholds["max_profit_percent"]:
                close_reason = f"Takeprofit ({self.thresholds['max_profit_percent']}%) hit at {perf_pct}%"

            # Trailing stop
            if not close_reason and max_perf is not None and self.trailing_stop_config.get("enabled", False):
                act_pct = self.trailing_stop_config.get("activation_percent", 5)
                dd_pct = self.trailing_stop_config.get("drawdown_percent", 8)
                if max_perf >= act_pct:
                    drawdown = max_perf - perf_pct
                    if drawdown >= dd_pct:
                        close_reason = f"Trailing Stop: peak {max_perf:.1f}%, now {perf_pct:.1f}% (dd {drawdown:.1f}% >= {dd_pct}%)"

            # Daily profit target
            if not close_reason and today_pct is not None:
                factor = (1 + today_pct / 100.0) * (1 + perf_pct / 100.0) - 1
                potential = round(factor * 100, 2)
                if potential >= self.percent_profit_wanted:
                    close_reason = f"Daily profit target ({self.percent_profit_wanted}%) potentially met ({potential}%)"

            if close_reason:
                positions_to_close.append({"position_id": pid, "api_details": api_pos, "reason": close_reason})

        # Execute closures concurrently (bounded)
        processed = []
        sem = asyncio.Semaphore(3)  # Max 3 concurrent closures

        async def _close_one(pos_info):
            nonlocal errors
            async with sem:
                pid = pos_info["position_id"]
                api_pos = pos_info["api_details"]
                reason = pos_info["reason"]

                if not api_pos.get("PositionBase", {}).get("CanBeClosed", False):
                    processed.append({"id": pid, "status": "Skipped (Cannot Be Closed)"})
                    return
                # Checked again here: another position check may have started
                # closing it while this one was deciding.
                if pid in self._closing_position_ids:
                    return

                self._closing_position_ids.add(pid)
                try:
                    await self._send_close_order(api_pos)
                except Exception as e:
                    self._closing_position_ids.discard(pid)
                    logger.error("Failed to close %s: %s", pid, e, exc_info=True)
                    await self.send_telegram(f"ERROR closing {pid}: {e}")
                    errors += 1
                    processed.append({"id": pid, "status": f"Failed: {e}"})
                    return

                # Not awaited: the next price update must not wait for Saxo to
                # list this closure before the other positions are checked again.
                self._record_closed_position_in_background(pid, f"Performance ({reason})")
                processed.append({"id": pid, "status": "Close order sent"})

        await asyncio.gather(*[_close_one(p) for p in positions_to_close])

        # Apply max-perf DB updates
        db_updates, save_errors = await self._save_pending_max_perf()
        errors += save_errors

        logger.debug("Perf check done. Closed=%d, MaxPerf updates=%d, Errors=%d", len(processed), len(db_updates), errors)
        return {"closed_positions_processed": processed, "db_updates": db_updates, "errors": errors}

    async def sync_db_positions_with_api(self, open_position_ids: set[str] | None = None) -> dict:
        """
        Compare DB open positions vs API, return updates for positions closed externally.

        ``open_position_ids`` are the positions known to be open at Saxo (e.g. from
        the position stream); when omitted they are fetched from the API.
        """
        logger.debug("--- Syncing DB positions with API ---")
        db_open_ids = await self.db_position_manager.get_open_positions_ids()
        # A closure this monitor has in flight is recorded by its own bookkeeping.
        db_open_ids = [pid for pid in db_open_ids if pid not in self._closing_position_ids]
        if not db_open_ids:
            return {"updates_for_db": []}

        if open_position_ids is not None:
            api_open_ids = open_position_ids
        else:
            try:
                api_resp = await self.position_service.get_open_positions()
                api_open_ids = {p["PositionId"] for p in api_resp.get("Data", [])}
            except Exception as e:
                logger.error("Failed to get API open positions: %s", e)
                return {"updates_for_db": []}

        potentially_closed = [pid for pid in db_open_ids if pid not in api_open_ids]
        if not potentially_closed:
            logger.debug("All DB-open positions are still open on API.")
            return {"updates_for_db": []}

        try:
            closed_resp = await self.position_service.get_closed_positions(top=len(potentially_closed) + 50)
            closed_map = {
                p["ClosedPosition"]["OpeningPositionId"]: p
                for p in closed_resp.get("Data", [])
                if p and "ClosedPosition" in p and "OpeningPositionId" in p["ClosedPosition"]
            }
        except Exception as e:
            logger.error("Failed to get API closed positions: %s", e)
            return {"updates_for_db": []}

        updates = []
        for pid in potentially_closed:
            if pid in closed_map:
                cp = closed_map[pid]["ClosedPosition"]
                dp = closed_map[pid].get("DisplayAndFormat", {})
                close_price = cp.get("ClosingPrice")
                open_price = cp.get("OpenPrice")
                amount = cp.get("Amount")
                perf = round(((close_price * 100) / open_price) - 100, 2) if open_price and close_price else None
                total_close = close_price * amount if close_price and amount else None

                update = {
                    "position_close_price": close_price,
                    "position_profit_loss": cp.get("ProfitLossOnTrade"),
                    "position_total_close_price": total_close,
                    "position_status": "Closed",
                    "position_total_performance_percent": perf,
                    "position_close_reason": "SaxoAPI",
                    "execution_time_close": cp.get("ExecutionTimeClose"),
                }
                updates.append((pid, update))
                if perf is not None and perf < 0:
                    # e.g. the broker-side stop was hit: the cooldown applies too
                    try:
                        await self.trading_rule.record_loss()
                    except Exception:
                        pass
                await self.send_telegram(f"SYNC CLOSE: {pid} ({dp.get('Description','N/A')})\nPerf: {perf}%")
            else:
                logger.warning("ANOMALY: %s open in DB, not in API open or closed.", pid)

        return {"updates_for_db": updates}

    async def close_managed_positions_by_criteria(
        self,
        action_filter: str | None = None,
        exclude_position_id: str | None = None,
        defer_bookkeeping: bool = False,
        only_position_id: str | None = None,
    ) -> dict:
        """
        Close open positions, optionally filtered by action or limited to one
        position, with concurrency.

        With ``defer_bookkeeping`` this returns as soon as the sell orders are
        accepted: looking up the closed positions, updating the DB and notifying
        run in the background, so the caller's next order isn't held up by them.
        """
        logger.info(
            "--- Closing positions (filter=%s, exclude=%s, only=%s) ---",
            action_filter, exclude_position_id, only_position_id,
        )
        close_scope = f"position {only_position_id}" if only_position_id else (action_filter or "All")

        db_positions = await self.db_position_manager.get_open_positions_ids_actions()
        if not db_positions:
            return {"closed_initiated_count": 0, "errors_count": 0, "closed_position_ids": []}

        try:
            api_resp = await self.position_service.get_open_positions()
            api_dict = {p["PositionId"]: p for p in api_resp.get("Data", [])}
        except Exception as e:
            logger.error("Failed to get API positions for closure: %s", e)
            raise

        closed_count = 0
        error_count = 0
        closed_position_ids = []
        sem = asyncio.Semaphore(3)

        async def _close_one(db_pos):
            nonlocal closed_count, error_count
            async with sem:
                pid = db_pos["position_id"]
                action = db_pos.get("action")

                if action_filter and action != action_filter:
                    return
                if exclude_position_id and str(pid) == str(exclude_position_id):
                    return
                if only_position_id and str(pid) != str(only_position_id):
                    return
                if pid not in api_dict:
                    return
                if pid in self._closing_position_ids:
                    return

                api_pos = api_dict[pid]
                pos_base = api_pos.get("PositionBase", {})
                if not pos_base.get("CanBeClosed", False):
                    return

                self._closing_position_ids.add(pid)
                try:
                    await self._send_close_order(api_pos)
                    closed_count += 1
                    closed_position_ids.append(pid)
                    reason = f"Explicit Close ({close_scope})"
                    if defer_bookkeeping:
                        self._record_closed_position_in_background(pid, reason)
                    else:
                        await self._record_closed_position(pid, reason)
                except Exception as e:
                    self._closing_position_ids.discard(pid)
                    logger.error("Failed closing %s: %s", pid, e)
                    await self.send_telegram(f"ERROR closing {pid}: {e}")
                    error_count += 1

        await asyncio.gather(*[_close_one(p) for p in db_positions])
        logger.info("Closure done. Initiated=%d, Errors=%d", closed_count, error_count)
        return {
            "closed_initiated_count": closed_count,
            "errors_count": error_count,
            "closed_position_ids": closed_position_ids,
        }

    async def _send_close_order(self, api_pos: dict) -> None:
        """Sell a position at market, after cancelling any order resting on it (e.g. its protective stop)."""
        pos_base = api_pos["PositionBase"]
        related_order_ids = [
            related["OrderId"]
            for related in pos_base.get("RelatedOpenOrders") or []
            if related.get("OrderId")
        ]
        if related_order_ids:
            # Left working, that order would compete with this sell for the same holding.
            await self.order_service.cancel_order(",".join(related_order_ids))

        direction = direction_from_amount(pos_base["Amount"])
        await self.order_service.place_market_order(
            uic=pos_base["Uic"],
            asset_type=pos_base["AssetType"],
            amount=pos_base["Amount"],
            buy_sell=direction_invert(direction),
        )

    async def wait_for_background_tasks(self, timeout: float = 10.0) -> None:
        """Give closures still being recorded in the background a chance to finish (used at shutdown)."""
        if self._background_tasks:
            await asyncio.wait(set(self._background_tasks), timeout=timeout)

    async def wait_for_positions_closed(self, position_ids: list[str], timeout: float = 1.5) -> bool:
        """
        Poll Saxo until none of ``position_ids`` is open any more, waiting at most
        ``timeout`` seconds in total. Returns whether they were all seen closed.

        Used between selling and buying, so the new order is sized on a balance
        that already includes the proceeds of the sale.
        """
        pending = set(position_ids)
        if not pending:
            return True

        for wait in [0, *backoff_gaps(timeout)]:
            if wait:
                await asyncio.sleep(wait)
            try:
                api_resp = await self.position_service.get_open_positions()
            except Exception as e:
                logger.warning("Could not check whether %s are closed: %s", sorted(pending), e)
                continue
            pending &= {
                p["PositionId"]
                for p in api_resp.get("Data", [])
                if p.get("PositionBase", {}).get("Status") not in CLOSED_POSITION_STATUSES
            }
            if not pending:
                return True

        logger.warning("Positions %s still reported open %.1fs after their sell order.", sorted(pending), timeout)
        return False

    async def _record_closed_position(
        self,
        position_id: str,
        reason: str,
        lookup_delays: tuple[float, ...] = CLOSED_POSITION_LOOKUP_DELAYS,
    ) -> bool:
        """Record a closure whose sell order was just sent, then stop treating it as in flight."""
        try:
            return await self._fetch_and_update_closed_position(position_id, reason, lookup_delays)
        finally:
            self._closing_position_ids.discard(position_id)

    def _record_closed_position_in_background(self, position_id: str, reason: str) -> None:
        self._run_in_background(
            self._record_closed_position(position_id, reason, CLOSED_POSITION_BACKGROUND_LOOKUP_DELAYS)
        )

    async def _find_closed_position(self, position_id: str) -> dict | None:
        closed = await self.position_service.get_closed_positions(top=50)
        for item in closed.get("Data", []):
            if item.get("ClosedPosition", {}).get("OpeningPositionId") == position_id:
                return item
        return None

    async def _fetch_and_update_closed_position(
        self,
        position_id: str,
        reason: str,
        lookup_delays: tuple[float, ...] = CLOSED_POSITION_LOOKUP_DELAYS,
    ) -> bool:
        """Fetch closed position from API (retrying while it isn't listed yet) and update DB."""
        try:
            item = None
            for delay in lookup_delays:
                await asyncio.sleep(delay)  # Non-blocking wait for API to reflect closure
                item = await self._find_closed_position(position_id)
                if item is not None:
                    break

            if item is None:
                logger.warning("Closed position %s not found in API.", position_id)
                return False

            cp = item.get("ClosedPosition", {})
            dp = item.get("DisplayAndFormat", {})
            close_price = cp.get("ClosingPrice")
            open_price = cp.get("OpenPrice")
            amount = cp.get("Amount")
            perf = round(((close_price * 100) / open_price) - 100, 2) if open_price and close_price and open_price != 0 else None
            total_close = close_price * amount if close_price and amount else None

            update = {
                "position_close_price": close_price,
                "position_profit_loss": cp.get("ProfitLossOnTrade"),
                "position_total_close_price": total_close,
                "position_status": "Closed",
                "position_total_performance_percent": perf,
                "position_close_reason": reason,
                "execution_time_close": cp.get("ExecutionTimeClose"),
            }
            await self.db_position_manager.update_turbo_position_data(position_id, update)

            if perf is not None and perf < 0:
                try:
                    await self.trading_rule.record_loss()
                except Exception:
                    pass

            max_perf = await self.db_position_manager.get_max_position_percent(position_id)
            today_pct = await self.db_position_manager.get_percent_of_the_day()

            msg = f"""--- CLOSED POSITION ---
Instrument: {dp.get('Description', 'N/A')}
Open: {open_price} → Close: {close_price}
Amount: {amount} | P/L: {cp.get('ProfitLossOnTrade')}
Perf: {perf}% | Max during trade: {max_perf}%
Reason: {reason}
Today realized: {today_pct}%"""
            await self.send_telegram(msg)
            await self._maybe_trigger_daily_stats(today_pct)
            return True

        except Exception as e:
            logger.error("Error updating closed position %s: %s", position_id, e, exc_info=True)
            return False

    async def _maybe_trigger_daily_stats(self, today_pct: float) -> None:
        """
        If today's realized profit has reached/exceeded the
        ``dont_enter_trade_if_day_profit_is_more_than`` threshold, fire the
        daily stats report early (normally only sent at 22:00 by the
        scheduler). Triggered at most once per day.
        """
        if self.trigger_daily_stats is None or today_pct is None:
            return

        threshold = getattr(self.trading_rule, "dont_enter_trade_if_day_profit_is_more_than", None)
        if threshold is None or today_pct < threshold:
            return

        tz = pytz.timezone(self.timezone)
        today = datetime.now(tz).date()
        if self._last_daily_stats_trigger_date == today:
            return
        self._last_daily_stats_trigger_date = today

        logger.info(
            "Daily profit %.2f%% reached target (%.2f%%) — triggering early daily stats.",
            today_pct, threshold,
        )
        try:
            await self.trigger_daily_stats()
        except Exception as e:
            logger.error("Failed to trigger early daily stats: %s", e, exc_info=True)

    def _log_performance_detail(self, position_id, api_pos, perf_pct):
        try:
            tz = pytz.timezone(self.timezone)
            now = datetime.now(tz)
            pos_base = api_pos.get("PositionBase", {})
            pos_view = api_pos.get("PositionView", {})
            data = {
                "position_id": position_id,
                "performance": perf_pct,
                "open_price": pos_base.get("OpenPrice"),
                "bid": pos_view.get("Bid"),
                "time": now.strftime("%Y-%m-%d %H:%M:%S"),
                "current_hour": now.hour,
                "current_minute": now.minute,
            }
            log_path = self.logging_config.get("persistant", {}).get("log_path", ".")
            os.makedirs(log_path, exist_ok=True)
            path = os.path.join(log_path, f"performance_{now.strftime('%Y-%m-%d')}.jsonl")
            with open(path, "a") as f:
                f.write(json.dumps(data) + "\n")
        except Exception as e:
            logger.error("Failed to write perf log: %s", e)
