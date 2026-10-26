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
import uuid
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
    saxo_error_parts,
)
from .close_intents import CloseIntent, CloseIntents
from .fresh_quote import FreshQuotes, opened_at, wait_for_fresh_quote
from .macro_calendar import Calendar, NewsRule, flat_reason, flat_window_at
from .order_tracker import (
    CANCEL,
    CLOSE,
    OPEN,
    PLACE,
    STOP,
    OrderContext,
    OrderTrace,
    request_accepted,
    request_failed,
    start_request,
    track_order,
)

logger = logging.getLogger(__name__)

TURBO_DESCRIPTION_PATTERN = re.compile(r"(.*) (\w+) (\w+) (\d+(?:\.\d+)?) (\w+)$")
INITIAL_TURBO_SEARCH_LIMIT = 60
INFO_PRICE_CANDIDATE_LIMIT = 20
INFO_PRICE_RETRY_BACKOFFS = (0.2, 0.4, 0.8)
# The account's value read with the balance is the account guard's while this recent; older, it is read
# again (the keep-warm timer reads the balance every 30 s).
ACCOUNT_VALUE_FRESH_SECONDS = 120
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
# A managed position without a price for this long is said: no exit can be decided for it.
MISSING_PRICE_ALERT_SECONDS = 30.0
# How long after the scheduler's close of the day the monitor sells what is still held.
END_OF_DAY_GRACE_SECONDS = 60
END_OF_DAY_REASON = "Explicit Close (End of day)"
# How long a position Saxo lists has to be without a row before it is taken for one WATA lost: the
# trader looks for the position of its order for about 11 s before giving up, then writes its row.
ADOPT_AFTER_SECONDS = 60.0


# ──────────────────────────────────────────────
#  Utilities
# ──────────────────────────────────────────────

def clock_time(value):
    """A time of day written "21:55", or None for anything else."""
    try:
        return datetime.strptime(value, "%H:%M").time()
    except (TypeError, ValueError):
        return None


def lost_position_row(pos: dict, action: str | None) -> dict:
    """The row of a position Saxo lists, as the trader writes one for a position it bought (execute_trade_signal)."""
    pos_base = pos.get("PositionBase") or {}
    display = pos.get("DisplayAndFormat") or {}
    amount = pos_base.get("Amount") or 0
    open_price = pos_base.get("OpenPrice") or 0
    if action not in ("long", "short"):
        words = f" {(display.get('Description') or '').lower()} "
        action = "short" if " short " in words else "long" if " long " in words else None
    return {
        "action": action,
        "position_id": pos.get("PositionId"),
        "position_amount": amount,
        "position_open_price": open_price,
        "position_total_open_price": amount * open_price,
        "position_status": "Open",
        "position_kind": "main",
        "execution_time_open": pos_base.get("ExecutionTimeOpen"),
        "order_id": pos_base.get("SourceOrderId"),
        "related_order_id": [
            str(related["OrderId"]) for related in pos_base.get("RelatedOpenOrders") or [] if related.get("OrderId")
        ],
        "instrument_name": display.get("Description"),
        "instrument_symbol": display.get("Symbol"),
        "instrument_uic": pos_base.get("Uic"),
        "instrument_currency": display.get("Currency"),
    }


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


def delayed_minutes(quote: dict) -> int:
    """
    How late Saxo says a quote is (``DelayedByMinutes``): a session below
    FullTradingAndChat is served delayed prices, and nothing is bought on them.
    """
    delayed = quote.get("DelayedByMinutes")
    return int(delayed) if isinstance(delayed, (int, float)) and not isinstance(delayed, bool) and delayed > 0 else 0


def has_tradable_bid_in_price_range(item: dict, min_price: float, max_price: float) -> bool:
    quote = item.get("Quote")
    return (
        bool(quote)
        and quote.get("Bid") is not None
        and quote.get("PriceTypeAsk") != "NoMarket"
        and quote.get("PriceTypeBid") != "NoMarket"
        and quote.get("MarketState") != "Closed"
        and not delayed_minutes(quote)
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
        and not delayed_minutes(item["Quote"])
    ]


def no_market_reason(items: list[dict], keywords: str, exchange_id: str) -> str:
    """Why none of ``items`` can be bought: their quotes delayed, said as such, or no market."""
    delays = sorted({delayed_minutes(item["Quote"]) for item in items} - {0})
    if delays:
        return (f"Saxo's quotes of the turbos are delayed by {delays[-1]} min: none is bought on them "
                "(is WATA's session at Saxo below FullTradingAndChat?)")
    return f"No markets available for {keywords} in {exchange_id}."


def filter_items_in_price_range(items: list[dict], min_price: float, max_price: float) -> list[dict]:
    return [item for item in items if min_price <= item["Quote"]["Bid"] <= max_price]


def spread_percent(quote: dict) -> float | None:
    """The spread of a quote, in percent of its ask; None when it has no bid or ask."""
    bid, ask = quote.get("Bid"), quote.get("Ask")
    if not all(isinstance(price, (int, float)) and not isinstance(price, bool) for price in (bid, ask)) or ask <= 0:
        return None
    return (ask - bid) / ask * 100


def filter_items_within_spread(items: list[dict], max_spread_percent: float | None) -> list[dict]:
    """
    The candidates whose spread is at most ``max_spread_percent`` of their ask
    (``trade.config.turbo_preference.max_spread_percent``): all of them without
    a limit, none whose spread cannot be read with one. The cheapest turbo,
    which is chosen, is also the one whose spread weighs most.
    """
    if not max_spread_percent:
        return items
    return [item for item in items if (spread := spread_percent(item["Quote"])) is not None and spread <= max_spread_percent]


def no_turbo_within_spread(items: list[dict], max_spread_percent: float, min_price: float, max_price: float) -> str:
    """Why no turbo of the price range is bought: their spreads, the narrowest said."""
    spreads = [spread for item in items if (spread := spread_percent(item["Quote"])) is not None]
    narrowest = f"; the narrowest was {min(spreads):.2f} %" if spreads else ""
    return f"No turbo in price range {min_price}-{max_price} with a spread of {max_spread_percent:g} % or less{narrowest}."


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
    max_spread_percent: float | None = None,
) -> dict:
    valid_bid_items = filter_items_with_valid_bid(candidate_snapshots)
    if not valid_bid_items:
        raise NoMarketAvailableException("No instruments with Bid data after filtering.")

    available_items = filter_tradable_market_items(valid_bid_items)
    if not available_items:
        raise NoMarketAvailableException(no_market_reason(valid_bid_items, keywords, exchange_id))

    price_filtered = filter_items_in_price_range(available_items, min_price, max_price)
    if not price_filtered:
        raise NoTurbosAvailableException(
            f"No turbos in price range {min_price}-{max_price}.",
            search_context={"PriceRange": (min_price, max_price), "AvailableCount": len(available_items)},
        )
    spread_filtered = filter_items_within_spread(price_filtered, max_spread_percent)
    if not spread_filtered:
        raise NoTurbosAvailableException(
            no_turbo_within_spread(price_filtered, max_spread_percent, min_price, max_price),
            search_context={"MaxSpreadPercent": max_spread_percent, "InRangeCount": len(price_filtered)},
        )

    selected = select_best_price_candidate(spread_filtered)
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
                if not isinstance(saxo_error_details, dict):
                    raise ValueError("not a JSON object")
                # Saxo's message is read wherever it put it (under ErrorInfo for an order)...
                error_message = saxo_error_parts(saxo_error_details)[1] or e.reason
                # ...while which exception is raised below still depends on the top-level code alone.
                error_code = saxo_error_details.get("ErrorCode")
            except (json.JSONDecodeError, Exception):
                saxo_error_details = content_str

            logger.error(
                "Saxo API Error (async): Status=%s, Code=%s, Msg=%s, Endpoint=%s",
                status_code, saxo_error_parts(saxo_error_details)[0], error_message,
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
        # No turbo whose spread is wider than this share of its ask is bought; None: no limit.
        self.max_spread_percent = self.config.get_config_value("trade.config.turbo_preference.max_spread_percent") or None
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
                max_spread_percent=self.max_spread_percent,
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
            raise NoMarketAvailableException(no_market_reason(valid_bid_items, keywords, exchange_id))

        # 7. Price range filter
        price_filtered = filter_items_in_price_range(available_items, min_price, max_price)
        if not price_filtered:
            raise NoTurbosAvailableException(
                f"No turbos in price range {min_price}-{max_price}.",
                search_context={"PriceRange": (min_price, max_price), "AvailableCount": len(available_items)},
            )

        # 8. Spread limit, then the cheapest
        spread_filtered = filter_items_within_spread(price_filtered, self.max_spread_percent)
        if not spread_filtered:
            raise NoTurbosAvailableException(
                no_turbo_within_spread(price_filtered, self.max_spread_percent, min_price, max_price),
                search_context={"MaxSpreadPercent": self.max_spread_percent, "InRangeCount": len(price_filtered)},
            )
        selected = select_best_price_candidate(spread_filtered)
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

def with_request_id(endpoint, request) -> None:
    """
    Give a request to the order endpoint an ``x-request-id`` of its own: Saxo
    refuses an order request identical to one of the last 15 seconds (409)
    unless each carries its own, and a sale tried again after a refusal is
    identical. The id is the one the request is kept under (``order_request``);
    the client sends the same one again when Saxo asked it to wait (429).
    Never raises: it is on the way to the sale.
    """
    try:
        request_id = request.request_id if request is not None else str(uuid.uuid4())
        endpoint.HEADERS = {**(getattr(endpoint, "HEADERS", None) or {}), "x-request-id": request_id}
    except Exception as e:
        logger.warning("Could not give an order request its id: %r", e)


class AsyncOrderService:
    """
    Everything WATA asks of Saxo's order endpoint goes through here, and each
    request is kept with Saxo's answer (``order_request``) by ``order_tracker``,
    in the background: it is described before it leaves and handed over once
    answered, so that nothing more is awaited and what is raised is unchanged.
    """

    def __init__(self, api_client: AsyncSaxoApiClient, account_key: str, client_key: str, order_tracker=None):
        self.api_client = api_client
        self.account_key = account_key
        self.client_key = client_key
        self.order_tracker = order_tracker

    async def place_market_order(
        self, uic: int, asset_type: str, amount: int, buy_sell: str, context: OrderContext | None = None,
    ) -> dict:
        logger.info("Placing Market Order: %s %d of %d (%s)", buy_sell, amount, uic, asset_type)
        pre_order = MarketOrder(Uic=uic, AssetType=asset_type, Amount=amount, BuySell=buy_sell)
        final_payload = tie_account_to_order(self.account_key, pre_order)
        req = tr.orders.Order(data=final_payload)
        request = start_request(PLACE, final_payload, context)
        with_request_id(req, request)

        try:
            result = await self.api_client.request(req)
        except SaxoApiError as e:
            request_failed(self.order_tracker, request, e)
            # Its status and Saxo's answer are said by this exception: its own text is only what is not.
            detail = "" if e.saxo_error_details else f": {e.args[0] if e.args else e}"
            raise OrderPlacementError(
                f"API error placing order{detail}",
                status_code=e.status_code,
                saxo_error_details=e.saxo_error_details,
                order_details=final_payload,
            ) from e
        except BaseException as e:
            request_failed(self.order_tracker, request, e)
            raise

        try:
            if not result or not result.get("OrderId"):
                raise OrderPlacementError("Response missing OrderId.", order_details=final_payload, saxo_error_details=result)
        except BaseException as e:
            request_failed(self.order_tracker, request, e, response=result)
            raise

        request_accepted(self.order_tracker, request, response=result, order_id=result["OrderId"])
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
        context: OrderContext | None = None,
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
        request = start_request(PLACE, final_payload, context)
        req = tr.orders.Order(data=final_payload)
        with_request_id(req, request)
        try:
            result = await self.api_client.request(req) or {}
        except BaseException as e:
            request_failed(self.order_tracker, request, e)
            raise

        try:
            order_ids = [result.get("OrderId")] + [related.get("OrderId") for related in result.get("Orders") or []]
            order_id = next((order_id for order_id in order_ids if order_id), None)
            if not order_id:
                raise OrderPlacementError("Response missing OrderId.", order_details=final_payload, saxo_error_details=result)
        except BaseException as e:
            request_failed(self.order_tracker, request, e, response=result)
            raise

        request_accepted(self.order_tracker, request, response=result, order_id=order_id)
        logger.info("Stop order placed on position %s — OrderId: %s", position_id, order_id)
        return order_id

    async def cancel_order(self, order_id: str, context: OrderContext | None = None) -> bool:
        cancelled, _ = await self.cancel_order_with_reason(order_id, context=context)
        return cancelled

    async def cancel_order_with_reason(
        self, order_id: str, context: OrderContext | None = None,
    ) -> tuple[bool, str | None]:
        """Cancel an order: whether Saxo accepted that, and what it answered when it did not."""
        logger.info("Cancelling order: %s", order_id)
        req = tr.orders.CancelOrders(OrderIds=order_id, params={"AccountKey": self.account_key})
        request = start_request(CANCEL, {"OrderIds": order_id}, context, order_ids=order_id)
        with_request_id(req, request)
        try:
            result = await self.api_client.request(req)
            request_accepted(self.order_tracker, request, response=result)
            logger.info("Order %s cancelled.", order_id)
            return True, None
        except Exception as e:
            request_failed(self.order_tracker, request, e)
            logger.error("Failed to cancel order %s: %s", order_id, e)
            return False, f"{type(e).__name__}: {e}"
        except BaseException as e:
            # Stopped in flight (the service stopping): kept too, and let through as before.
            request_failed(self.order_tracker, request, e)
            raise


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
        # Saxo's TotalValue (cash and positions) of the last balance read, and when (monotonic): the account
        # guard's value, which costs no request of its own (src/trade/account_guard.py).
        self.last_total_value: tuple[float, float] | None = None

    def _note_total_value(self, resp: dict) -> None:
        total_value = resp.get("TotalValue")
        if isinstance(total_value, (int, float)) and not isinstance(total_value, bool):
            self.last_total_value = (float(total_value), time.monotonic())

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

    async def get_open_orders(self) -> list[dict]:
        """The orders still working on the account: a sale accepted but not filled yet is one of them."""
        req = pf.orders.GetOpenOrdersMe(params={"ClientKey": self.client_key, "FieldGroups": "DisplayAndFormat"})
        response = await self.api_client.request(req)
        return (response or {}).get("Data") or []

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

    async def find_position_by_order_id_with_retry(self, order_id: str, trace: OrderTrace | None = None) -> dict:
        """
        Finds position, checking straight away and then with exponential backoff
        (non-blocking).
        Attempts order cancellation if not found after all retries.

        ``trace`` is marked with each look and with how it ended, for whoever
        keeps what became of the order.
        """
        if not isinstance(trace, OrderTrace):
            trace = OrderTrace(OPEN)
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
            trace.looked()
            for p in positions.get("Data", []):
                if p.get("PositionBase", {}).get("SourceOrderId") == order_id:
                    trace.confirmed(p.get("PositionId"))
                    logger.info("Position found for order %s after %d attempt(s).", order_id, attempt + 1)
                    return p
            logger.debug("Position not found for order %s (attempt %d/%d)", order_id, attempt + 1, max_attempts)

        # All retries exhausted — attempt cancellation
        logger.critical("Position not found for order %s after %d retries. Cancelling.", order_id, max_attempts)
        cancelled, cancel_error = await self.order_service.cancel_order_with_reason(
            order_id, context=OrderContext(purpose=OPEN, reason="Saxo showed no position for the order"),
        )
        trace.gave_up(detail=cancel_error, cancel_accepted=cancelled)
        raise PositionNotFoundException(
            f"Position not found after {max_attempts} retries for order {order_id}",
            order_id=order_id,
            cancellation_attempted=True,
            cancellation_succeeded=cancelled,
            cancellation_error=cancel_error,
        )

    async def get_spending_power(self) -> float:
        req = pf.balances.AccountBalances(params={"ClientKey": self.client_key})
        resp = await self.api_client.request(req)
        if not resp or "SpendingPower" not in resp:
            raise SaxoApiError("Invalid balance response, missing SpendingPower.")
        self._note_total_value(resp)
        power = resp["SpendingPower"]
        logger.debug("Spending power: %s", power)
        return float(power)

    async def get_current_account_balance(self) -> float:
        """Fetch the current total account value (cash + open positions) for reporting."""
        req = pf.balances.AccountBalances(params={"ClientKey": self.client_key})
        resp = await self.api_client.request(req)
        if not resp or "TotalValue" not in resp:
            raise SaxoApiError("Invalid balance response, missing TotalValue.")
        self._note_total_value(resp)
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
        order_tracker=None,
    ):
        self.instrument_service = instrument_service
        self.order_service = order_service
        self.position_service = position_service
        self.config = config_manager
        self.db_order_manager = db_order_manager
        self.db_position_manager = db_position_manager
        self.watchlist_client = watchlist_client
        # Keeps what became of each order for the admin interface, in the background.
        self.order_tracker = order_tracker
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
                context=OrderContext(
                    purpose=STOP,
                    position_id=None if position_id is None else str(position_id),
                    action=position.get("action"),
                    reason=f"Protective stop at {percent}%",
                    instrument_name=instrument.get("description"),
                    instrument_symbol=instrument.get("symbol"),
                    instrument_currency=instrument.get("currency"),
                    price_seen=stop_price,
                ),
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

    async def account_value(self, fresh_seconds: float = ACCOUNT_VALUE_FRESH_SECONDS) -> float | None:
        """
        Saxo's TotalValue as last read with the balance (the keep-warm timer reads it every 30 s), read again
        when older than ``fresh_seconds``; None when Saxo does not answer. The account guard's value.
        """
        seen = getattr(self.position_service, "last_total_value", None)
        if not isinstance(seen, tuple) or time.monotonic() - seen[1] > fresh_seconds:
            try:
                await self.refresh_spending_power()
            except Exception as e:
                logger.warning("The account's value could not be read for the account guard: %s", e)
            seen = getattr(self.position_service, "last_total_value", None)
        return seen[0] if isinstance(seen, tuple) else None

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

    async def execute_trade_signal(
        self,
        exchange_id: str,
        underlying_uics: str,
        keywords: str,
        confidence: float = None,
        signal_uuid: str | None = None,
    ) -> dict:
        """
        Full async trade workflow.
        Key optimisation: the turbo selection and get_spending_power run **in parallel**.

        ``signal_uuid`` is kept with the requests sent to Saxo: an order Saxo
        refuses has no id, and that is what links it to its signal.
        """
        logger.info("--- Executing trade signal: %s on %s (conf=%s) ---", keywords, underlying_uics, confidence)
        timestamps = {"start": time.time()}
        validated_order = None
        confirmed_position = None
        turbo_info = None
        amount = None
        trace = OrderTrace(OPEN)

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
            context = self._entry_context(keywords, turbo_info, signal_uuid)
            trace.sent()
            validated_order = await self.order_service.place_market_order(
                uic=turbo_info["selected_instrument"]["uic"],
                asset_type=turbo_info["selected_instrument"]["asset_type"],
                amount=amount,
                buy_sell="Buy",
                context=context,
            )
            self.invalidate_spending_power()
            order_id = validated_order["OrderId"]
            timestamps["order_placed"] = time.time()
            trace.accepted(order_id)
            track_order(self.order_tracker, trace)

            # 5. Confirm position (exponential backoff)
            confirmed_position = await self.position_service.find_position_by_order_id_with_retry(order_id, trace=trace)
            timestamps["position_confirmed"] = time.time()
            trace.confirmed(confirmed_position.get("PositionId"))
            track_order(self.order_tracker, trace)

            # 6. Persist to PostgreSQL
            order_data = self._entry_order_data(
                keywords, order_id, amount, turbo_info, trace.sent_at, confirmed_position.get("PositionId"),
            )
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

        except PositionNotFoundException as e:
            # Normally marked where the looking stopped; this is for a caller that did not.
            trace.gave_up(detail=getattr(e, "cancellation_error", None), cancel_accepted=e.cancellation_succeeded)
            self._track_entry_without_position(trace, keywords, amount, turbo_info)
            raise
        except Exception as e:
            if validated_order and not confirmed_position:
                oid = validated_order.get("OrderId")
                if oid:
                    logger.warning("Cancelling orphan order %s due to failure: %s", oid, e)
                    cancelled = await self.order_service.cancel_order(
                        oid,
                        context=OrderContext(
                            purpose=OPEN, signal_uuid=signal_uuid, action=keywords,
                            reason=f"Stopped by an error once Saxo had the order: {type(e).__name__}",
                        ),
                    )
                    trace.failed(detail=f"{type(e).__name__}: {e}", cancel_accepted=cancelled)
                    self._track_entry_without_position(trace, keywords, amount, turbo_info)
            raise

    @staticmethod
    def _entry_context(keywords, turbo_info: dict, signal_uuid) -> OrderContext:
        """Why the buy is sent, for the record of the requests. Never raises: it is made just before the order leaves."""
        try:
            instrument = turbo_info["selected_instrument"]
            return OrderContext(
                purpose=OPEN,
                signal_uuid=None if signal_uuid is None else str(signal_uuid),
                action=keywords,
                instrument_name=instrument.get("description"),
                instrument_symbol=instrument.get("symbol"),
                instrument_currency=instrument.get("currency"),
                price_seen=instrument.get("latest_ask"),
            )
        except Exception:
            return OrderContext(purpose=OPEN, signal_uuid=None if signal_uuid is None else str(signal_uuid), action=keywords)

    @staticmethod
    def _entry_order_data(keywords, order_id, amount, turbo_info: dict, sent_at: datetime | None, position_id) -> dict:
        """The buy order as it is recorded: ``position_id`` is the position it gave, None when it gave none."""
        instrument = turbo_info["selected_instrument"]
        return {
            "action": keywords, "buy_sell": "Buy", "order_id": order_id,
            "order_amount": amount, "order_type": "Market", "order_kind": "main",
            # When the order left, not when its position was confirmed.
            "order_submit_time": (sent_at or datetime.now(pytz.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "related_order_id": [], "position_id": position_id,
            "instrument_name": instrument["description"],
            "instrument_symbol": instrument["symbol"],
            "instrument_uic": instrument["uic"],
            "instrument_price": instrument.get("latest_ask"),
            "instrument_currency": instrument["currency"],
            "order_cost": instrument.get("commissions", {}).get("CostBuy"),
        }

    def _track_entry_without_position(self, trace: OrderTrace, keywords, amount, turbo_info) -> None:
        """
        Keep an order Saxo accepted that gave no position, with the order
        itself: nothing else records it. Never raises: it runs while an error
        is on its way up.
        """
        if self.order_tracker is None or not trace.order_id:
            return
        try:
            order_row = self._entry_order_data(keywords, trace.order_id, amount, turbo_info, trace.sent_at, None)
        except Exception as e:
            logger.warning("Could not describe order %s for the record: %r", trace.order_id, e)
            order_row = None
        track_order(self.order_tracker, trace, order_row)

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
        order_tracker=None,
        order_requests=None,  # what WATA asked of Saxo's order endpoint (AsyncDbOrderRequestManager), read
    ):
        self.position_service = position_service
        self.order_service = order_service
        self.config = config_manager
        self.db_position_manager = db_position_manager
        self.trading_rule = trading_rule
        self.send_telegram = send_telegram_fn
        self.trigger_daily_stats = trigger_daily_stats_fn
        # Keeps what became of each sell order for the admin interface, in the background.
        self.order_tracker = order_tracker
        # Read to tell WATA's sales from others and to find positions WATA bought but lost.
        self.order_requests = order_requests
        # Positions at Saxo without a row: since when each was seen so, and those found not to be WATA's.
        self._unrecorded_since: dict[str, float] = {}
        self._not_wata_positions: set[str] = set()
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
        # The worst result seen of each position (0 until it falls under its open), kept as the best is,
        # the database keeping the lower one (lower_min_performance).
        self._min_perf_by_position: dict[str, float] = {}
        self._min_perf_pending_save: dict[str, float] = {}
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
            day_cfg = None
        # When nothing is to be held any more: after the scheduler's close, until the next day's start (keep_flat).
        self.close_position_time = clock_time(day_cfg.get("close_position_time")) if isinstance(day_cfg, dict) else None
        market_hours = getattr(self.trading_rule, "market_hours_config", None)
        start_hour = market_hours.get("trading_start_hour") if isinstance(market_hours, dict) else None
        self.trading_start_hour = start_hour if isinstance(start_hour, int) and not isinstance(start_hour, bool) else None
        # What is to be sold, kept until Saxo no longer lists it (src/trade/close_intents.py).
        self.close_intents = CloseIntents()
        # The positions just bought whose quote is older than their purchase, not judged on it yet (src/trade/fresh_quote.py).
        self.fresh_quotes = FreshQuotes(enabled=wait_for_fresh_quote(self.perf_config))
        # Since when each managed position has been without a price, and those already said.
        self._missing_price_since: dict[str, float] = {}
        self._missing_price_said: set[str] = set()

    @staticmethod
    def _now() -> float:
        """The time of a decision, in epoch seconds (tests give their own)."""
        return time.time()

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

    async def managed_position_ids(self) -> set[str]:
        """The positions WATA manages, as the DB last said (the last known ones while it is slow or down)."""
        db_positions, _ = await self._load_managed_state()
        return {str(pos["position_id"]) for pos in db_positions}

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

    async def _save_pending_min_perf(self) -> int:
        """Write to the DB the worst results it may not have yet; returns the error count."""
        for pid, min_perf in list(self._min_perf_pending_save.items()):
            if not self._db_usable():
                break
            try:
                await asyncio.wait_for(
                    self.db_position_manager.lower_min_performance(pid, min_perf),
                    MANAGED_STATE_DB_TIMEOUT_SECONDS,
                )
            except Exception as e:
                self._mark_db_unavailable()
                logger.error("Failed min-perf update for %s: %r", pid, e)
                return 1
            if self._min_perf_pending_save.get(pid) == min_perf:
                del self._min_perf_pending_save[pid]
        return 0

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

        A position that hits a threshold is to be sold (``close_intents``), and
        the sales due are attempted before this returns; recording those
        closures in the DB happens in the background.

        Parameters
        ----------
        api_dict : dict[str, dict]
            ``{PositionId: position_dict}`` — from either API response or stream cache.
        """
        now = self._now()
        self._end_intents_of_sold(api_dict)
        db_positions, today_pct = await self._load_managed_state()
        managed_ids = {db_pos["position_id"] for db_pos in db_positions}
        for known in (self._max_perf_by_position, self._max_perf_pending_save, self._min_perf_by_position,
                      self._min_perf_pending_save, self._live_values, self._missing_price_since):
            for pid in list(known):
                if pid not in managed_ids:
                    del known[pid]
        self._missing_price_said &= managed_ids
        self.fresh_quotes.forget_except(managed_ids)

        if not db_positions:
            logger.info("No open positions to check.")
            return {"closed_positions_processed": [], "db_updates": [], "errors": 0}

        for db_pos in db_positions:
            pid = db_pos["position_id"]
            if pid not in api_dict or pid in self._closing_position_ids:
                continue

            api_pos = api_dict[pid]
            open_price = api_pos.get("PositionBase", {}).get("OpenPrice")
            current_bid = api_pos.get("PositionView", {}).get("Bid")
            if not open_price or not current_bid or open_price == 0:
                self._note_missing_price(pid, now)
                continue
            self._missing_price_since.pop(pid, None)
            self._missing_price_said.discard(pid)

            perf_pct = round(((current_bid * 100) / open_price) - 100, 2)
            logger.info("Pos %s: Open=%.4f, Bid=%.4f, Perf=%.2f%%", pid, open_price, current_bid, perf_pct)
            self._live_values[pid] = (current_bid, perf_pct, datetime.now(pytz.utc))

            self._log_performance_detail(pid, api_pos, perf_pct)

            waiting = self.fresh_quotes.waiting.get(pid)
            if not self.fresh_quotes.judged(pid, current_bid, opened_at(api_pos), now):
                # Its bid may be older than its purchase: not judged on it, its best not raised. What comes with time still sells.
                if waiting is None:
                    logger.info("Pos %s: bid %.4f may be older than the purchase at %.4f: its exits wait for Saxo's next quote.",
                                pid, current_bid, open_price)
                if pid in self.close_intents:
                    self.close_intents.quoted(pid, current_bid, now)
                    continue
                flat = self._flat_reason(now)
                if flat:
                    self.close_intents.want(pid, f"Performance ({flat})", now, action=db_pos.get("action"), urgent=True)
                continue
            if waiting is not None:
                logger.info("Pos %s: Saxo's first quote after the purchase, %.0f s after the position was seen: bid %.4f.",
                            pid, now - waiting.since, current_bid)

            max_perf = await self._get_max_perf(pid)
            if max_perf is not None and perf_pct > max_perf:
                self._max_perf_by_position[pid] = perf_pct
                self._max_perf_pending_save[pid] = perf_pct
            if perf_pct < self._min_perf_by_position.get(pid, 0.0):
                self._min_perf_by_position[pid] = perf_pct
                self._min_perf_pending_save[pid] = perf_pct

            if pid in self.close_intents:
                # Already to be sold: its intent is tried below, with its own reason, soon once quoted again.
                self.close_intents.quoted(pid, current_bid, now)
                continue

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

            # Nothing held through a US release, when the macro_news rule says so
            if not close_reason:
                close_reason = self._flat_reason(now)

            if close_reason:
                # Urgent: nothing is asked of Saxo before this sale (see _attempt_close).
                self.close_intents.want(pid, f"Performance ({close_reason})", now, action=db_pos.get("action"), urgent=True)

        processed, errors = await self._attempt_due_closes(api_dict, now)

        # Apply max-perf DB updates, then the worst results
        db_updates, save_errors = await self._save_pending_max_perf()
        errors += save_errors
        errors += await self._save_pending_min_perf()

        logger.debug("Perf check done. Closed=%d, MaxPerf updates=%d, Errors=%d", len(processed), len(db_updates), errors)
        return {"closed_positions_processed": processed, "db_updates": db_updates, "errors": errors}

    def _flat_reason(self, now: float) -> str | None:
        """Why nothing is to be held at ``now``: a US release ahead, when the macro_news rule sells before it."""
        rule = getattr(self.trading_rule, "news_rule", None)
        calendar = getattr(self.trading_rule, "news_calendar", None)
        if not isinstance(rule, NewsRule) or not isinstance(calendar, Calendar):
            return None
        window = flat_window_at(calendar, rule, now)
        return None if window is None else flat_reason(window, self.timezone)

    def _market_timezone(self) -> str:
        zone = getattr(self.trading_rule, "market_timezone", None)
        return zone if isinstance(zone, str) else self.timezone

    def _day_sale_moment(self, now: float) -> float | None:
        """When the day of ``now`` sells what is held: the trading rule's (an early close sells earlier)."""
        if isinstance(getattr(self.trading_rule, "market_timezone", None), str):
            return self.trading_rule.sale_moment(now)
        if self.close_position_time is None:
            return None
        local = datetime.fromtimestamp(now, pytz.timezone(self.timezone))
        return local.replace(
            hour=self.close_position_time.hour, minute=self.close_position_time.minute, second=0, microsecond=0,
        ).timestamp()

    def _after_the_close(self, now: float) -> bool:
        """Whether ``now`` is past the day's close (and its grace), before the next day's start."""
        sale = self._day_sale_moment(now)
        if sale is None:
            return False
        if now - sale >= END_OF_DAY_GRACE_SECONDS:
            return True
        local = datetime.fromtimestamp(now, pytz.timezone(self._market_timezone()))
        return self.trading_start_hour is not None and local.hour < self.trading_start_hour

    async def keep_flat(self, streamed_positions: dict[str, dict]) -> dict:
        """
        The sales that come with time rather than with a price, and the sales
        due: what is held after the day's close, what is held into a US
        release. Run by the position monitor every second, whether the stream
        brings prices or not (an issuer that stops quoting brings none).
        """
        now = self._now()
        self._end_intents_of_sold(streamed_positions)
        reason = END_OF_DAY_REASON if self._after_the_close(now) else self._flat_reason(now)
        # Nothing listed at Saxo, nothing to read: the night's checks cost no query.
        if reason is not None and streamed_positions:
            try:
                db_positions, _ = await self._load_managed_state()
            except Exception as e:
                logger.warning("Could not read the managed positions to keep them flat: %r", e)
                db_positions = []
            for db_pos in db_positions:
                pid = db_pos["position_id"]
                if pid not in streamed_positions or pid in self.close_intents:
                    continue
                if reason == END_OF_DAY_REASON:
                    self.close_intents.want(pid, reason, now, action=db_pos.get("action"))
                    sale = datetime.fromtimestamp(self._day_sale_moment(now), pytz.timezone(self.timezone))
                    self._run_in_background(self.send_telegram(
                        f"Position {pid} was still open after the day's close ({sale.strftime('%H:%M')}): WATA sells it."
                    ))
                else:
                    self.close_intents.want(pid, f"Performance ({reason})", now, action=db_pos.get("action"))
        if not len(self.close_intents):
            return {"closed_positions_processed": [], "errors": 0}
        processed, errors = await self._attempt_due_closes(streamed_positions, now)
        return {"closed_positions_processed": processed, "errors": errors}

    async def request_close(self, position_id, reason: str) -> dict:
        """
        Sell one managed position, and go on until Saxo no longer lists it:
        what the operator asks from the admin interface, and what the trader
        hands over when its own sale did not go through.
        """
        position_id = str(position_id)
        none = {"closed_initiated_count": 0, "errors_count": 0, "closed_position_ids": [], "pending": False}
        db_positions = await self.db_position_manager.get_open_positions_ids_actions()
        db_pos = next((pos for pos in db_positions if str(pos["position_id"]) == position_id), None)
        if db_pos is None:
            return none
        api_resp = await self.position_service.get_open_positions()
        api_dict = {str(pos["PositionId"]): pos for pos in api_resp.get("Data", [])}
        if position_id not in api_dict:
            return none
        now = self._now()
        intent = self.close_intents.want(position_id, reason, now, action=db_pos.get("action"))
        if position_id in self._closing_position_ids or not intent.is_due(now):
            # A sale of it is on its way, or waits for its next try.
            return {**none, "pending": True}
        outcome = await self._attempt_close(intent, api_dict[position_id], now)
        if outcome is not None and outcome.get("status") == "Close order sent":
            return {**none, "closed_initiated_count": 1, "closed_position_ids": [position_id]}
        return {**none, "errors_count": 1, "pending": True}

    async def _attempt_due_closes(self, positions: dict[str, dict], now: float) -> tuple[list[dict], int]:
        """Try the sales due now, three at a time; what each did, and how many failed."""
        due = [
            intent for intent in self.close_intents.due(now, positions)
            if intent.position_id not in self._closing_position_ids
        ]
        if not due:
            return [], 0
        sem = asyncio.Semaphore(3)  # Max 3 concurrent closures

        async def _one(intent: CloseIntent) -> dict:
            async with sem:
                return await self._attempt_close(intent, positions[intent.position_id], now)

        processed = [outcome for outcome in await asyncio.gather(*[_one(intent) for intent in due]) if outcome is not None]
        return processed, sum(1 for outcome in processed if outcome.get("error"))

    async def _attempt_close(self, intent: CloseIntent, api_pos: dict, now: float) -> dict | None:
        """
        One attempt at the sale ``intent`` wants. A first, urgent attempt (a
        threshold just hit) asks nothing of Saxo before its sale; any other
        first looks for a sale already working at Saxo, which a new one would
        only compete with (SellOrdersAlreadyExistForOwnedContracts).
        """
        pid = intent.position_id
        # Checked again here: another check may have started selling it while this one was deciding.
        if pid in self._closing_position_ids:
            return None
        pos_base = api_pos.get("PositionBase", {})
        if not pos_base.get("CanBeClosed", False):
            self._close_failed(intent, "Saxo says it cannot be closed now", now)
            return {"id": pid, "status": "Skipped (Cannot Be Closed)", "error": True}

        self._closing_position_ids.add(pid)
        try:
            if intent.attempts or intent.sell_accepted_at is not None or not intent.urgent:
                working = await self._working_sales(pos_base)
                if working:
                    self._closing_position_ids.discard(pid)
                    self._close_failed(
                        intent, f"A sale is still working at Saxo (order {', '.join(working)}): waiting for it to be filled", now,
                    )
                    return {"id": pid, "status": "Waiting for a working sale"}
            trace = await self._send_close_order(api_pos, position_id=pid, action=intent.action, reason=intent.reason)
        except Exception as e:
            self._closing_position_ids.discard(pid)
            logger.error("Failed to close %s: %s", pid, e, exc_info=True)
            self._close_failed(intent, f"{type(e).__name__}: {e}", now)
            return {"id": pid, "status": f"Failed: {e}", "error": True}

        self.close_intents.accepted(pid, now)
        # Not awaited: the next price update must not wait for Saxo to
        # list this closure before the other positions are checked again.
        self._record_closed_position_in_background(pid, intent.reason, trace=trace)
        return {"id": pid, "status": "Close order sent"}

    async def _working_sales(self, pos_base: dict) -> list[str]:
        """The orders working at Saxo that sell this position's turbo; none when Saxo cannot say."""
        try:
            orders = await self.position_service.get_open_orders()
        except Exception as e:
            logger.warning("Could not list the orders working at Saxo: %r", e)
            return []
        side = direction_invert(direction_from_amount(pos_base.get("Amount") or 0))
        return [
            str(order.get("OrderId"))
            for order in orders
            if order.get("Uic") == pos_base.get("Uic") and order.get("BuySell") == side and order.get("OrderId")
        ]

    def _close_failed(self, intent: CloseIntent, error: str, now: float) -> None:
        """An attempt did not sell: said on its first failure, then less and less often (``close_intents``)."""
        if not self.close_intents.failed(intent.position_id, error, now):
            return
        if intent.attempts == 1:
            message = (f"Could not sell position {intent.position_id} ({intent.reason}): {error}. "
                       "WATA keeps trying until Saxo no longer lists it.")
        else:
            since = datetime.fromtimestamp(intent.since, pytz.timezone(self.timezone)).strftime("%H:%M:%S")
            message = (f"Position {intent.position_id} still not sold after {intent.attempts} attempts since {since} "
                       f"({intent.reason}). Last: {error}")
        # Not awaited: the other sales due must not wait for the message.
        self._run_in_background(self.send_telegram(message))

    def _end_intents_of_sold(self, positions: dict[str, dict]) -> None:
        """Forget the intents of positions Saxo no longer lists; say the end of those that had failed."""
        for intent in self.close_intents.gone(positions):
            if intent.attempts:
                self._run_in_background(self.send_telegram(
                    f"Position {intent.position_id} is no longer open at Saxo, after {intent.attempts} "
                    f"failed attempt(s) to sell it ({intent.reason})."
                ))

    def _note_missing_price(self, position_id: str, now: float) -> None:
        """A managed position without a price: said once it lasts, since no exit can be decided for it."""
        since = self._missing_price_since.setdefault(position_id, now)
        if now - since < MISSING_PRICE_ALERT_SECONDS or position_id in self._missing_price_said:
            return
        self._missing_price_said.add(position_id)
        at = datetime.fromtimestamp(since, pytz.timezone(self.timezone)).strftime("%H:%M:%S")
        self._run_in_background(self.send_telegram(
            f"No price for position {position_id} since {at}: no exit can be decided until Saxo quotes it again."
        ))

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

        # The reason of WATA's own sale, for a position whose sale was recorded by no one yet (its bookkeeping
        # may take longer than this reconciliation waits, or give up): "SaxoAPI" only for the others.
        sold_by_wata = await self._sale_reasons([pid for pid in potentially_closed if pid in closed_map])

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
                    "position_close_reason": sold_by_wata.get(pid) or "SaxoAPI",
                    "execution_time_close": cp.get("ExecutionTimeClose"),
                }
                updates.append((pid, update))
                if perf is not None and perf < 0:
                    # e.g. the broker-side stop was hit: the cooldown applies too
                    try:
                        await self.trading_rule.record_loss()
                    except Exception:
                        pass
                if pid not in sold_by_wata:
                    await self.send_telegram(f"SYNC CLOSE: {pid} ({dp.get('Description','N/A')})\nPerf: {perf}%")
            else:
                logger.warning("ANOMALY: %s open in DB, not in API open or closed.", pid)

        return {"updates_for_db": updates}

    async def _sale_reasons(self, position_ids: list[str]) -> dict[str, str]:
        """Why WATA sold each of these positions, when it did; nothing when it cannot be read in time."""
        if not position_ids or self.order_requests is None:
            return {}
        try:
            return await asyncio.wait_for(self.order_requests.sale_reasons(position_ids), MANAGED_STATE_DB_TIMEOUT_SECONDS)
        except Exception as e:
            logger.warning("Could not read WATA's sales of %s: %r", position_ids, e)
            return {}

    async def adopt_lost_positions(self, open_positions: dict[str, dict] | None = None) -> list[str]:
        """
        The positions Saxo lists that WATA bought but has no row of (a position
        listed after the trader gave up looking for it, a service stopped between
        the order and its row) are given one, so that they are stopped and
        closed at night like the others. A position not bought by WATA is named
        once and left alone. Returns the positions adopted.
        """
        if self.order_requests is None:
            return []
        if open_positions is None:
            api_resp = await self.position_service.get_open_positions()
            open_positions = {str(pos["PositionId"]): pos for pos in api_resp.get("Data", [])}
        now = self._now()
        recorded = {str(pid) for pid in await self.db_position_manager.get_open_positions_ids()}
        unrecorded = {
            str(pid): pos for pid, pos in open_positions.items()
            if str(pid) not in recorded and str(pid) not in self._closing_position_ids
            and str(pid) not in self._not_wata_positions
        }
        for pid in list(self._unrecorded_since):
            if pid not in unrecorded:
                del self._unrecorded_since[pid]
        lasting = {}
        for pid, pos in unrecorded.items():
            since = self._unrecorded_since.setdefault(pid, now)
            if now - since >= ADOPT_AFTER_SECONDS:
                lasting[pid] = pos
        if not lasting:
            return []

        by_order = {}
        for pid, pos in lasting.items():
            order_id = (pos.get("PositionBase") or {}).get("SourceOrderId")
            if order_id:
                by_order[str(order_id)] = pid
            else:
                self._leave_alone(pid, pos)
        purchases = await self.order_requests.purchases(list(by_order)) if by_order else {}

        adopted = []
        for order_id, pid in by_order.items():
            pos = lasting[pid]
            purchase = purchases.get(order_id)
            if purchase is None:
                self._leave_alone(pid, pos)
                continue
            await self.db_position_manager.insert_turbo_open_position_data(lost_position_row(pos, purchase.get("action")))
            self._unrecorded_since.pop(pid, None)
            adopted.append(pid)
            description = (pos.get("DisplayAndFormat") or {}).get("Description", "N/A")
            logger.warning("Adopted position %s (order %s), which had no row.", pid, order_id)
            await self.send_telegram(
                f"Position {pid} ({description}), bought by WATA (order {order_id}), had no record: "
                "WATA manages it again (stops, the day's close)."
            )
        return adopted

    def _leave_alone(self, position_id: str, pos: dict) -> None:
        """A position WATA did not buy: said once, never managed."""
        self._not_wata_positions.add(position_id)
        self._unrecorded_since.pop(position_id, None)
        description = (pos.get("DisplayAndFormat") or {}).get("Description", "N/A")
        logger.info("Position %s at Saxo was not bought by WATA: left alone.", position_id)
        self._run_in_background(self.send_telegram(
            f"Position {position_id} ({description}) at Saxo was not bought by WATA: WATA leaves it alone."
        ))

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

        What it could not sell is said, never skipped in silence: ``not_closed``
        (refused, failed, or Saxo saying it cannot be closed now), each with its
        reason, for the position monitor to take over (``request_close``), and
        ``not_listed``, open in WATA's records but not at Saxo, which the
        reconciliation settles.
        """
        logger.info(
            "--- Closing positions (filter=%s, exclude=%s, only=%s) ---",
            action_filter, exclude_position_id, only_position_id,
        )
        close_scope = f"position {only_position_id}" if only_position_id else (action_filter or "All")

        db_positions = await self.db_position_manager.get_open_positions_ids_actions()
        if not db_positions:
            return {"closed_initiated_count": 0, "errors_count": 0, "closed_position_ids": [], "not_closed": [], "not_listed": []}

        try:
            api_resp = await self.position_service.get_open_positions()
            api_dict = {p["PositionId"]: p for p in api_resp.get("Data", [])}
        except Exception as e:
            logger.error("Failed to get API positions for closure: %s", e)
            raise

        closed_count = 0
        error_count = 0
        closed_position_ids = []
        not_closed = []
        not_listed = []
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
                    not_listed.append(pid)
                    return
                if pid in self._closing_position_ids:
                    return

                api_pos = api_dict[pid]
                pos_base = api_pos.get("PositionBase", {})
                reason = f"Explicit Close ({close_scope})"
                if not pos_base.get("CanBeClosed", False):
                    not_closed.append({"position_id": pid, "action": action, "reason": reason,
                                       "why": "Saxo says it cannot be closed now"})
                    return

                self._closing_position_ids.add(pid)
                try:
                    trace = await self._send_close_order(api_pos, position_id=pid, action=action, reason=reason)
                    closed_count += 1
                    closed_position_ids.append(pid)
                    if defer_bookkeeping:
                        self._record_closed_position_in_background(pid, reason, trace=trace)
                    else:
                        await self._record_closed_position(pid, reason, trace=trace)
                except Exception as e:
                    self._closing_position_ids.discard(pid)
                    logger.error("Failed closing %s: %s", pid, e)
                    await self.send_telegram(f"ERROR closing {pid}: {e}")
                    error_count += 1
                    not_closed.append({"position_id": pid, "action": action, "reason": reason,
                                       "why": f"{type(e).__name__}: {e}"})

        await asyncio.gather(*[_close_one(p) for p in db_positions])
        logger.info("Closure done. Initiated=%d, Errors=%d, Not closed=%d, Not listed=%d",
                    closed_count, error_count, len(not_closed), len(not_listed))
        return {
            "closed_initiated_count": closed_count,
            "errors_count": error_count,
            "closed_position_ids": closed_position_ids,
            "not_closed": not_closed,
            "not_listed": not_listed,
        }

    async def _send_close_order(
        self, api_pos: dict, position_id=None, action: str | None = None, reason: str | None = None,
    ) -> OrderTrace:
        """
        Sell a position at market, after cancelling any order resting on it (e.g. its protective stop).

        Returns the marks of the sell order, to be completed once the position
        is seen closed. Nothing is awaited here but Saxo. ``reason`` is why it
        is sold, kept with the requests sent to Saxo.
        """
        pos_base = api_pos["PositionBase"]
        context = self._close_context(api_pos, position_id, action, reason)
        related_order_ids = [
            related["OrderId"]
            for related in pos_base.get("RelatedOpenOrders") or []
            if related.get("OrderId")
        ]
        if related_order_ids:
            # Left working, that order would compete with this sell for the same holding.
            await self.order_service.cancel_order(",".join(related_order_ids), context=context)

        direction = direction_from_amount(pos_base["Amount"])
        trace = OrderTrace(CLOSE, position_id=position_id)
        trace.sent()
        result = await self.order_service.place_market_order(
            uic=pos_base["Uic"],
            asset_type=pos_base["AssetType"],
            amount=pos_base["Amount"],
            buy_sell=direction_invert(direction),
            context=context,
        )
        self._track_close_order(trace, result, api_pos, action)
        return trace

    @staticmethod
    def _close_context(api_pos: dict, position_id, action: str | None, reason: str | None) -> OrderContext:
        """Why the position is sold, for the record of the requests. Never raises: it is made on the way to the sell."""
        position_id = None if position_id is None else str(position_id)
        try:
            display = api_pos.get("DisplayAndFormat") or {}
            view = api_pos.get("PositionView") or {}
            return OrderContext(
                purpose=CLOSE,
                position_id=position_id,
                action=action,
                reason=reason,
                instrument_name=display.get("Description"),
                instrument_symbol=display.get("Symbol"),
                instrument_currency=display.get("Currency"),
                price_seen=view.get("Bid"),
            )
        except Exception:
            return OrderContext(purpose=CLOSE, position_id=position_id, action=action, reason=reason)

    def _track_close_order(self, trace: OrderTrace, result, api_pos: dict, action: str | None) -> None:
        """
        Keep the sell order Saxo just accepted, with the order itself: nothing
        else records it. Never raises: an error here would read as a sell that
        failed, and the position would be sold again.
        """
        try:
            order_id = result.get("OrderId") if isinstance(result, dict) else None
            if not order_id:
                return
            trace.accepted(order_id)
            if self.order_tracker is None:
                return
            pos_base = api_pos.get("PositionBase") or {}
            display = api_pos.get("DisplayAndFormat") or {}
            amount = pos_base.get("Amount")
            order_row = {
                "action": action,
                "buy_sell": direction_invert(direction_from_amount(amount)),
                "order_id": trace.order_id,
                "order_amount": abs(int(amount)),
                "order_type": "Market",
                "order_kind": "close",
                "order_submit_time": trace.sent_at,
                "related_order_id": [],
                "position_id": trace.position_id,
                "instrument_name": display.get("Description"),
                "instrument_symbol": display.get("Symbol"),
                "instrument_uic": pos_base.get("Uic"),
                # The bid the sale was decided on: what it was sold at is on the position.
                "instrument_price": (api_pos.get("PositionView") or {}).get("Bid"),
                "instrument_currency": display.get("Currency"),
                "order_cost": None,
            }
            track_order(self.order_tracker, trace, order_row)
        except Exception as e:
            logger.warning("Could not keep the sell order of position %s: %r", trace.position_id, e)

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
        trace: OrderTrace | None = None,
    ) -> bool:
        """Record a closure whose sell order was just sent, then stop treating it as in flight."""
        try:
            return await self._fetch_and_update_closed_position(position_id, reason, lookup_delays, trace=trace)
        finally:
            self._closing_position_ids.discard(position_id)

    def _record_closed_position_in_background(
        self, position_id: str, reason: str, trace: OrderTrace | None = None,
    ) -> None:
        self._run_in_background(
            self._record_closed_position(position_id, reason, CLOSED_POSITION_BACKGROUND_LOOKUP_DELAYS, trace=trace)
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
        trace: OrderTrace | None = None,
    ) -> bool:
        """
        Fetch closed position from API (retrying while it isn't listed yet) and update DB.

        ``trace`` is the sell order's: it is marked with each look and with
        whether the position was seen closed.
        """
        if not isinstance(trace, OrderTrace):
            trace = OrderTrace(CLOSE, position_id=position_id)
        try:
            item = None
            for delay in lookup_delays:
                await asyncio.sleep(delay)  # Non-blocking wait for API to reflect closure
                item = await self._find_closed_position(position_id)
                trace.looked()
                if item is not None:
                    break

            if item is None:
                logger.warning("Closed position %s not found in API.", position_id)
                trace.gave_up(detail="Not among Saxo's closed positions")
                track_order(self.order_tracker, trace)
                return False
            trace.confirmed()
            track_order(self.order_tracker, trace)

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
            # Only counts when the looking itself failed: a closure already seen keeps its mark.
            trace.failed(detail=f"{type(e).__name__}: {e}")
            track_order(self.order_tracker, trace)
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
