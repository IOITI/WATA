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
from copy import deepcopy
from datetime import datetime

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


# ──────────────────────────────────────────────
#  Utilities
# ──────────────────────────────────────────────

def parse_saxo_turbo_description(description: str) -> dict | None:
    pattern = r"(.*) (\w+) (\w+) (\d+(?:\.\d+)?) (\w+)$"
    match = re.match(pattern, description)
    if match:
        return {
            "name": match.group(1),
            "kind": match.group(2),
            "buysell": match.group(3),
            "price": match.group(4),
            "from": match.group(5),
        }
    return None


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

    async def ensure_ready(self):
        """Initialize or refresh the underlying AsyncAPI instance."""
        latest_token = self.saxo_auth.get_token()
        if latest_token != self._current_token or self._api is None:
            if self._api:
                await self._api.close()
            logger.info("AsyncSaxoApiClient: (re)initializing AsyncAPI for env '%s'", self.environment)
            self._api = AsyncAPI(
                access_token=latest_token,
                environment=self.environment,
                timeout=30.0,
            )
            self._current_token = latest_token

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
        self.websocket_config = self.config.get_config_value("trade.config.general.websocket", {"refresh_rate_ms": 10000})
        self.cache_config = self.config.get_config_value("trade.config.turbo_cache", {"enabled": False, "ttl_seconds": 30})
        self._turbo_cache: dict = {}

    async def _get_infoprices_for_asset_type(self, identifiers_string: str, exchange_id: str, asset_type: str):
        req = tr.infoprices.InfoPrices(
            params={
                "$top": self.api_limits["top_instruments"],
                "AccountKey": self.account_key,
                "ExchangeId": exchange_id,
                "FieldGroups": "Commissions,DisplayAndFormat,Greeks,HistoricalChanges,InstrumentPriceDetails,MarketDepth,PriceInfo,PriceInfoDetails,Quote",
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
                    return deepcopy(cached["result"])

        result = await self._find_turbos_uncached(exchange_id, underlying_uics, keywords)

        if self.cache_config.get("enabled", False):
            self._turbo_cache[(exchange_id, underlying_uics, keywords)] = {
                "result": deepcopy(result),
                "timestamp": time.time(),
            }
        return result

    async def _find_turbos_uncached(self, exchange_id: str, underlying_uics: str, keywords: str) -> dict:
        logger.info("Finding turbos: Exchange=%s, Underlying=%s, Keywords=%s", exchange_id, underlying_uics, keywords)

        # 1. Instrument search
        req = rd.instruments.Instruments(
            params={
                "$top": self.api_limits["top_instruments"],
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
        valid_items = []
        for item in response["Data"]:
            parsed = parse_saxo_turbo_description(item.get("Description", ""))
            if parsed:
                item["appParsedData"] = parsed
                valid_items.append(item)

        if not valid_items:
            raise NoTurbosAvailableException("No instruments with parsable descriptions.", search_context=req.params)

        # 3. Sort
        sort_reverse = keywords.lower() != "short"
        sorted_instruments = sorted(valid_items, key=lambda x: float(x["appParsedData"]["price"]), reverse=sort_reverse)

        # 4. Group by AssetType
        instrument_groups: dict[str, list] = defaultdict(list)
        for item in sorted_instruments:
            instrument_groups[item["AssetType"]].append(item)

        # 5. Fetch InfoPrices — parallelize across asset types
        max_retries = self.retry_config["max_retries"]
        retry_sleep = self.retry_config["retry_sleep_seconds"]

        response_infoprices = None
        for attempt in range(max_retries):
            # Fire all asset-type requests in parallel
            tasks = []
            for asset_type, instruments in instrument_groups.items():
                ids_str = ",".join(str(i["Identifier"]) for i in instruments)
                tasks.append(self._get_infoprices_for_asset_type(ids_str, exchange_id, asset_type))

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
                    await asyncio.sleep(retry_sleep)
                continue

            # Check bid availability
            with_quote = [i for i in all_data if "Quote" in i]
            if not with_quote:
                break  # No quotes at all — nothing to retry for

            missing_bid = [i for i in with_quote if "Bid" not in i["Quote"]]
            pct_missing = (len(missing_bid) / len(with_quote)) * 100 if with_quote else 0

            if pct_missing > 50:
                logger.warning("%.1f%% missing Bid (attempt %d/%d)", pct_missing, attempt + 1, max_retries)
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_sleep)
                continue

            response_infoprices = {"Data": all_data}
            break

        if not response_infoprices or not response_infoprices.get("Data"):
            raise NoMarketAvailableException("Failed to obtain InfoPrice data after retries.")

        # Filter items with valid Bid
        valid_bid_items = [
            i for i in response_infoprices["Data"]
            if i.get("Quote") and i["Quote"].get("Bid") is not None
        ]
        if not valid_bid_items:
            raise NoMarketAvailableException("No instruments with Bid data after filtering.")

        # 6. Market state filter
        available_items = [
            i for i in valid_bid_items
            if i["Quote"].get("PriceTypeAsk") != "NoMarket"
            and i["Quote"].get("PriceTypeBid") != "NoMarket"
            and i["Quote"].get("MarketState") != "Closed"
        ]
        if not available_items:
            raise NoMarketAvailableException(f"No markets available for {keywords} in {exchange_id}.")

        # 7. Price range filter
        mn, mx = self.turbo_price_range["min"], self.turbo_price_range["max"]
        price_filtered = [i for i in available_items if mn <= i["Quote"]["Bid"] <= mx]
        if not price_filtered:
            raise NoTurbosAvailableException(
                f"No turbos in price range {mn}-{mx}.",
                search_context={"PriceRange": (mn, mx), "AvailableCount": len(available_items)},
            )

        # 8. Select best
        final_candidates = sorted(price_filtered, key=lambda x: x["Quote"]["Bid"])
        selected = deepcopy(final_candidates[0])

        # 9. Price subscription (non-blocking, with fallback)
        ctx_id = str(uuid.uuid1())
        ref_id = str(uuid.uuid1())
        sub_ctx = None
        sub_ref = None
        final_snapshot = selected

        try:
            sub_req = tr.prices.CreatePriceSubscription(
                data={
                    "Arguments": {
                        "Uic": selected["Uic"],
                        "AccountKey": self.account_key,
                        "AssetType": selected["AssetType"],
                        "Amount": 1,
                        "FieldGroups": [
                            "Commissions", "DisplayAndFormat", "Greeks", "HistoricalChanges",
                            "InstrumentPriceDetails", "MarketDepth", "PriceInfo",
                            "PriceInfoDetails", "Quote", "Timestamps",
                        ],
                    },
                    "ContextId": ctx_id,
                    "ReferenceId": ref_id,
                    "RefreshRate": self.websocket_config["refresh_rate_ms"],
                    "Format": "application/json",
                }
            )
            resp = await self.api_client.request(sub_req)
            snapshot = resp.get("Snapshot") if resp else None
            if snapshot:
                final_snapshot = snapshot
                sub_ctx = ctx_id
                sub_ref = ref_id
        except Exception as e:
            logger.warning("Price subscription failed (fallback to InfoPrice): %s", e)

        return {
            "input_criteria": {"exchange_id": exchange_id, "underlying_uics": underlying_uics, "keywords": keywords},
            "selected_instrument": {
                "uic": selected["Uic"],
                "asset_type": selected["AssetType"],
                "description": final_snapshot.get("DisplayAndFormat", {}).get("Description", "N/A"),
                "symbol": final_snapshot.get("DisplayAndFormat", {}).get("Symbol", "N/A"),
                "currency": final_snapshot.get("DisplayAndFormat", {}).get("Currency", "N/A"),
                "decimals": final_snapshot.get("DisplayAndFormat", {}).get("OrderDecimals", 2),
                "parsed_data": parse_saxo_turbo_description(
                    final_snapshot.get("DisplayAndFormat", {}).get("Description", "")
                ),
                "quote": final_snapshot.get("Quote", {}),
                "commissions": final_snapshot.get("Commissions", {}),
                "latest_ask": final_snapshot.get("Quote", {}).get("Ask"),
                "latest_bid": final_snapshot.get("Quote", {}).get("Bid"),
                "subscription_context_id": sub_ctx,
                "subscription_reference_id": sub_ref,
            },
        }


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
        Finds position with exponential backoff (non-blocking).
        Attempts order cancellation if not found after all retries.
        """
        delays = [0.3, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0]  # ~10.8s total max
        max_attempts = min(len(delays), self.max_retries)

        for attempt in range(max_attempts):
            await asyncio.sleep(delays[attempt])
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
        logger.info("Spending power: %s", power)
        return float(power)


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
    ):
        self.instrument_service = instrument_service
        self.order_service = order_service
        self.position_service = position_service
        self.config = config_manager
        self.db_order_manager = db_order_manager
        self.db_position_manager = db_position_manager
        self.buying_power_config = self.config.get_config_value("trade.config.buying_power", {})
        self.safety_margins = self.buying_power_config.get("safety_margins", {"bid_calculation": 1})
        self.reserve_cash_percent = self.buying_power_config.get("reserve_cash_percent", 0)
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

    async def execute_trade_signal(self, exchange_id: str, underlying_uics: str, keywords: str, confidence: float = None) -> dict:
        """
        Full async trade workflow.
        Key optimisation: find_turbos and get_spending_power run **in parallel**.
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

            # 1+2. PARALLEL: find turbo + get spending power
            turbo_task = asyncio.create_task(
                self.instrument_service.find_turbos(exchange_id, underlying_uics, keywords)
            )
            balance_task = asyncio.create_task(
                self.position_service.get_spending_power()
            )
            turbo_info, spending_power = await asyncio.gather(turbo_task, balance_task)
            timestamps["turbo_found"] = time.time()
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
    ):
        self.position_service = position_service
        self.order_service = order_service
        self.config = config_manager
        self.db_position_manager = db_position_manager
        self.trading_rule = trading_rule
        self.send_telegram = send_telegram_fn
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

    async def _evaluate_positions(self, api_dict: dict[str, dict]) -> dict:
        """
        Shared evaluation logic used by both REST-polled and stream-fed paths.

        Parameters
        ----------
        api_dict : dict[str, dict]
            ``{PositionId: position_dict}`` — from either API response or stream cache.
        """
        db_positions = await self.db_position_manager.get_open_positions_ids_actions()
        if not db_positions:
            logger.info("No open positions to check.")
            return {"closed_positions_processed": [], "db_updates": [], "errors": 0}

        positions_to_close = []
        db_updates = []
        errors = 0

        for db_pos in db_positions:
            pid = db_pos["position_id"]
            if pid not in api_dict:
                continue

            api_pos = api_dict[pid]
            open_price = api_pos.get("PositionBase", {}).get("OpenPrice")
            current_bid = api_pos.get("PositionView", {}).get("Bid")
            if not open_price or not current_bid or open_price == 0:
                continue

            perf_pct = round(((current_bid * 100) / open_price) - 100, 2)
            logger.info("Pos %s: Open=%.4f, Bid=%.4f, Perf=%.2f%%", pid, open_price, current_bid, perf_pct)

            self._log_performance_detail(pid, api_pos, perf_pct)

            max_perf = await self.db_position_manager.get_max_position_percent(pid)
            if perf_pct > max_perf:
                db_updates.append((pid, {"position_max_performance_percent": perf_pct}))

            close_reason = None

            # Stop-loss
            if perf_pct <= self.thresholds["stoploss_percent"]:
                close_reason = f"Stoploss ({self.thresholds['stoploss_percent']}%) hit at {perf_pct}%"
                try:
                    self.trading_rule.record_loss()
                except Exception:
                    pass

            # Take-profit
            elif perf_pct >= self.thresholds["max_profit_percent"]:
                close_reason = f"Takeprofit ({self.thresholds['max_profit_percent']}%) hit at {perf_pct}%"

            # Trailing stop
            if not close_reason and self.trailing_stop_config.get("enabled", False):
                act_pct = self.trailing_stop_config.get("activation_percent", 5)
                dd_pct = self.trailing_stop_config.get("drawdown_percent", 8)
                if max_perf >= act_pct:
                    drawdown = max_perf - perf_pct
                    if drawdown >= dd_pct:
                        close_reason = f"Trailing Stop: peak {max_perf:.1f}%, now {perf_pct:.1f}% (dd {drawdown:.1f}% >= {dd_pct}%)"

            # Daily profit target
            if not close_reason:
                try:
                    today_pct = await self.db_position_manager.get_percent_of_the_day()
                    factor = (1 + today_pct / 100.0) * (1 + perf_pct / 100.0) - 1
                    potential = round(factor * 100, 2)
                    if potential >= self.percent_profit_wanted:
                        close_reason = f"Daily profit target ({self.percent_profit_wanted}%) potentially met ({potential}%)"
                except Exception as e:
                    logger.error("Daily profit check error for %s: %s", pid, e)

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

                try:
                    direction = direction_from_amount(api_pos["PositionBase"]["Amount"])
                    sell_dir = direction_invert(direction)
                    await self.order_service.place_market_order(
                        uic=api_pos["PositionBase"]["Uic"],
                        asset_type=api_pos["PositionBase"]["AssetType"],
                        amount=api_pos["PositionBase"]["Amount"],
                        buy_sell=sell_dir,
                    )
                    ok = await self._fetch_and_update_closed_position(pid, f"Performance ({reason})")
                    processed.append({"id": pid, "status": "Closed" if ok else "Closed (DB Update Failed)"})
                    if not ok:
                        errors += 1
                except Exception as e:
                    logger.error("Failed to close %s: %s", pid, e, exc_info=True)
                    await self.send_telegram(f"ERROR closing {pid}: {e}")
                    errors += 1
                    processed.append({"id": pid, "status": f"Failed: {e}"})

        await asyncio.gather(*[_close_one(p) for p in positions_to_close])

        # Apply max-perf DB updates
        for pid, data in db_updates:
            try:
                await self.db_position_manager.update_turbo_position_data(pid, data)
            except Exception as e:
                logger.error("Failed max-perf update for %s: %s", pid, e)
                errors += 1

        logger.info("Perf check done. Closed=%d, MaxPerf updates=%d, Errors=%d", len(processed), len(db_updates), errors)
        return {"closed_positions_processed": processed, "db_updates": db_updates, "errors": errors}

    async def sync_db_positions_with_api(self) -> dict:
        """Compare DB open positions vs API, return updates for positions closed externally."""
        logger.info("--- Syncing DB positions with API ---")
        db_open_ids = await self.db_position_manager.get_open_positions_ids()

        try:
            api_resp = await self.position_service.get_open_positions()
            api_open_ids = {p["PositionId"] for p in api_resp.get("Data", [])}
        except Exception as e:
            logger.error("Failed to get API open positions: %s", e)
            return {"updates_for_db": []}

        if not db_open_ids:
            return {"updates_for_db": []}

        potentially_closed = [pid for pid in db_open_ids if pid not in api_open_ids]
        if not potentially_closed:
            logger.info("All DB-open positions are still open on API.")
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
                await self.send_telegram(f"SYNC CLOSE: {pid} ({dp.get('Description','N/A')})\nPerf: {perf}%")
            else:
                logger.warning("ANOMALY: %s open in DB, not in API open or closed.", pid)

        return {"updates_for_db": updates}

    async def close_managed_positions_by_criteria(self, action_filter: str | None = None, exclude_position_id: str | None = None) -> dict:
        """Close open positions, optionally filtered by action, with concurrency."""
        logger.info("--- Closing positions (filter=%s, exclude=%s) ---", action_filter, exclude_position_id)

        db_positions = await self.db_position_manager.get_open_positions_ids_actions()
        if not db_positions:
            return {"closed_initiated_count": 0, "errors_count": 0}

        try:
            api_resp = await self.position_service.get_open_positions()
            api_dict = {p["PositionId"]: p for p in api_resp.get("Data", [])}
        except Exception as e:
            logger.error("Failed to get API positions for closure: %s", e)
            raise

        closed_count = 0
        error_count = 0
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
                if pid not in api_dict:
                    return

                api_pos = api_dict[pid]
                pos_base = api_pos.get("PositionBase", {})
                if not pos_base.get("CanBeClosed", False):
                    return

                try:
                    direction = direction_from_amount(pos_base["Amount"])
                    sell_dir = direction_invert(direction)
                    await self.order_service.place_market_order(
                        uic=pos_base["Uic"],
                        asset_type=pos_base["AssetType"],
                        amount=pos_base["Amount"],
                        buy_sell=sell_dir,
                    )
                    closed_count += 1
                    reason = f"Explicit Close ({action_filter or 'All'})"
                    await self._fetch_and_update_closed_position(pid, reason)
                except Exception as e:
                    logger.error("Failed closing %s: %s", pid, e)
                    await self.send_telegram(f"ERROR closing {pid}: {e}")
                    error_count += 1

        await asyncio.gather(*[_close_one(p) for p in db_positions])
        logger.info("Closure done. Initiated=%d, Errors=%d", closed_count, error_count)
        return {"closed_initiated_count": closed_count, "errors_count": error_count}

    async def _fetch_and_update_closed_position(self, position_id: str, reason: str) -> bool:
        """Fetch closed position from API after brief delay and update DB."""
        await asyncio.sleep(1.5)  # Non-blocking wait for API to reflect closure

        try:
            closed = await self.position_service.get_closed_positions(top=50)
            for item in closed.get("Data", []):
                cp = item.get("ClosedPosition", {})
                if cp.get("OpeningPositionId") == position_id:
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
                            self.trading_rule.record_loss()
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
                    return True

            logger.warning("Closed position %s not found in API.", position_id)
            return False

        except Exception as e:
            logger.error("Error updating closed position %s: %s", position_id, e, exc_info=True)
            return False

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
