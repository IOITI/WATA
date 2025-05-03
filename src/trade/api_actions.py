import os

# Import the original API class
from src.saxo_openapi.saxo_openapi import API as SaxoOpenApiLib
from src.saxo_openapi.exceptions import OpenAPIError as SaxoOpenApiLibError

# Keep other necessary imports from the previous refactoring
import logging
import re
import json
import math
from copy import deepcopy
from datetime import datetime
import pytz
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type, RetryError

# --- Saxo OpenApi Components ---
# Assume these endpoint definitions exist and work as before
import src.saxo_openapi.endpoints.referencedata as rd
import src.saxo_openapi.endpoints.trading as tr
import src.saxo_openapi.endpoints.portfolio as pf
from src.saxo_openapi.contrib.orders import MarketOrder, tie_account_to_order, direction_from_amount
from src.saxo_openapi.contrib.orders.helper import direction_invert
import requests # Import requests exceptions if needed for translation

# --- Local Imports ---
from src.saxo_authen import SaxoAuth
from .exceptions import (
    NoMarketAvailableException,
    NoTurbosAvailableException,
    PositionNotFoundException,
    InsufficientFundsException,
    ApiRequestException,
    TokenAuthenticationException,
    DatabaseOperationException, # Will likely be raised higher up now
    PositionCloseException,
    WebSocketConnectionException, # WebSocket logic removed for now
    SaxoApiError,
    OrderPlacementError,
    ConfigurationError
)
from src.mq_telegram.tools import send_message_to_mq_for_telegram
from src.configuration import ConfigurationManager
# --- Import DB Managers and TradingRule for injection ---
from src.database import DbOrderManager, DbPositionManager # Needed for new responsibilities
from src.trade.rules import TradingRule # Needed for PerformanceMonitor

# --- Constants ---
DEFAULT_RETRY_ATTEMPTS = 5
DEFAULT_RETRY_WAIT_SECONDS = 2

# --- Utilities ---

def parse_saxo_turbo_description(description):
    # (Keep the existing utility function)
    pattern = r"(.*) (\w+) (\w+) (\d+(?:\.\d+)?) (\w+)$"
    match = re.match(pattern, description)
    if match:
        return {
            "name": match.group(1), "kind": match.group(2),
            "buysell": match.group(3), "price": match.group(4),
            "from": match.group(5),
        }
    return None

# === Low-Level API Client Wrapper ===

class SaxoApiClient:
    """
    Acts as a Facade/Wrapper around the existing `src.saxo_openapi.saxo_openapi.API`
    class. It handles token management via SaxoAuth and translates exceptions
    from the underlying library into WATA-specific exceptions.
    """

    def __init__(self, config_manager: ConfigurationManager, saxo_auth: SaxoAuth):
        self.config_manager = config_manager
        self.saxo_auth = saxo_auth
        self.environment = config_manager.get_config_value("saxo_auth.env", "live")
        self._saxo_api_instance: SaxoOpenApiLib | None = None
        self._current_token: str | None = None
        self._ensure_valid_token_and_api_instance() # Initialize on creation

    def _ensure_valid_token_and_api_instance(self):
        """
        Ensures the underlying SaxoOpenApiLib instance exists and uses the latest token.
        Re-initializes the SaxoOpenApiLib instance if the token changes.
        """
        try:
            latest_token = self.saxo_auth.get_token()
            if latest_token != self._current_token or self._saxo_api_instance is None:
                logging.info(f"SaxoApiClient: Token changed or API instance missing. Re-initializing SaxoOpenApiLib for env '{self.environment}'.")
                # Configure request parameters if needed (e.g., timeouts)
                request_params = {"timeout": 30} # Example timeout
                self._saxo_api_instance = SaxoOpenApiLib(
                    access_token=latest_token,
                    environment=self.environment,
                    request_params=request_params
                    # Add headers if needed from config
                )
                self._current_token = latest_token
                logging.info("SaxoApiClient: SaxoOpenApiLib instance refreshed.")

        except TokenAuthenticationException:
            logging.critical("SaxoApiClient: Failed to obtain/refresh token during API instance setup.")
            raise # Propagate critical auth errors

    def request(self, endpoint_request_obj):
        """
        Makes an API request using the underlying SaxoOpenApiLib instance.

        Args:
            endpoint_request_obj: An instance representing the API endpoint
                                  (e.g., rd.instruments.Instruments).

        Returns:
            The response content from the API (typically dict or None).

        Raises:
            ApiRequestException: For connection issues or request setup problems.
            TokenAuthenticationException: If authentication fails.
            SaxoApiError: For general Saxo API errors (>=400 status codes).
            OrderPlacementError: For errors specifically related to order placement.
            InsufficientFundsException: For insufficient funds errors reported by API.
            Various specific exceptions based on error content.
        """
        self._ensure_valid_token_and_api_instance() # Check/refresh token and API instance

        if self._saxo_api_instance is None:
             # This should ideally be caught by _ensure_valid_token..., but defensive check
             logging.critical("SaxoApiClient: SaxoOpenApiLib instance is not available.")
             raise ApiRequestException("Saxo API client instance not initialized.", endpoint=str(endpoint_request_obj))

        try:
            # Delegate the actual request to the underlying library instance
            logging.debug(f"SaxoApiClient: Forwarding request to SaxoOpenApiLib for endpoint: {type(endpoint_request_obj).__name__}")
            response_content = self._saxo_api_instance.request(endpoint_request_obj)
            logging.debug(f"SaxoApiClient: Received response content (type: {type(response_content).__name__})")
            return response_content

        # --- Exception Translation ---
        except SaxoOpenApiLibError as e:
            # Handle errors raised by the underlying saxo_openapi.API library
            status_code = e.status_code
            content_str = e.msg # This is the decoded error content string
            saxo_error_details = None
            error_message = content_str
            error_code = None

            try:
                # Attempt to parse the error content as JSON for more details
                saxo_error_details = json.loads(content_str)
                error_message = saxo_error_details.get('Message', content_str)
                error_code = saxo_error_details.get('ErrorCode')
            except json.JSONDecodeError:
                saxo_error_details = content_str # Keep the raw string if not JSON

            logging.error(f"Saxo API Error Wrapper: Caught OpenAPIError (Status: {status_code}, Code: {error_code}, Msg: {error_message}), Endpoint: {type(endpoint_request_obj).__name__}")

            # Specific Error Mapping based on status code and potentially error_code/message
            if status_code == 400 and error_code == "InsufficientFunds": # Confirm the actual ErrorCode from Saxo docs/testing
                 raise InsufficientFundsException(
                      message=error_message or "Insufficient funds reported by API",
                      saxo_error_details=saxo_error_details
                 ) from e
            # Check if it looks like an order placement error
            # Use endpoint type or path if available from endpoint_request_obj
            endpoint_path = getattr(endpoint_request_obj, 'path', 'unknown')
            is_order_endpoint = "/trade/v2/orders" in endpoint_path
            if (status_code in [400, 403, 409] or error_code) and is_order_endpoint:
                 order_payload = getattr(endpoint_request_obj, 'data', None)
                 raise OrderPlacementError(
                      f"Saxo rejected order ({status_code}): {error_message}",
                      status_code=status_code,
                      saxo_error_details=saxo_error_details,
                      order_details=order_payload
                 ) from e
            if status_code == 401: # Should ideally be handled by token refresh, but catch if it leaks
                 raise TokenAuthenticationException(f"API returned 401 Unauthorized: {error_message}", saxo_error_details=saxo_error_details) from e
            if status_code == 429: # Rate limit exceeded despite underlying library's retry
                 logging.warning(f"Persistent Rate Limit Error (429) received from API: {error_message}")
                 raise SaxoApiError(f"Persistent Rate Limit Error (429): {error_message}", status_code=status_code, saxo_error_details=saxo_error_details) from e

            # Default to general SaxoApiError
            raise SaxoApiError(
                f"Saxo API Error ({status_code}): {error_message}",
                status_code=status_code,
                saxo_error_details=saxo_error_details,
                request_details={"endpoint_type": type(endpoint_request_obj).__name__, "params": getattr(endpoint_request_obj, 'params', {}), "data": getattr(endpoint_request_obj, 'data', None)}
            ) from e

        except requests.RequestException as e:
            # Handle connection errors, timeouts etc. from the underlying requests library
            logging.error(f"API Request Exception Wrapper for endpoint {type(endpoint_request_obj).__name__}: {e}", exc_info=True)
            raise ApiRequestException(f"Underlying request failed: {e}", endpoint=str(endpoint_request_obj)) from e

        except TokenAuthenticationException:
            # Re-raise if caught during _ensure_valid_token...
            logging.critical("Token authentication failed during request sequence.")
            raise

        except Exception as e:
            # Catch any other unexpected errors during the process
            logging.exception(f"Unexpected error during Saxo API request wrapper for {type(endpoint_request_obj).__name__}: {e}") # Use logging.exception
            raise ApiRequestException(f"Unexpected wrapper error: {e}", endpoint=str(endpoint_request_obj)) from e


# === Domain-Specific Services ===

class InstrumentService:
    """Handles finding and retrieving instrument details."""

    def __init__(self, api_client: SaxoApiClient, config_manager: ConfigurationManager, account_key: str):
        self.api_client = api_client
        self.config = config_manager # Keep ref if needed for multiple values
        self.account_key = account_key
        self.api_limits = self.config.get_config_value("trade.config.general.api_limits", {"top_instruments": 200})
        self.turbo_price_range = self.config.get_config_value("trade.config.turbo_preference.price_range", {"min": 4, "max": 15})

    @retry(stop=stop_after_attempt(3), wait=wait_fixed(1), retry=retry_if_exception_type(ApiRequestException))
    def _get_instrument_details(self, identifiers_string: str, exchange_id: str):
        """Helper to get InfoPrices with retry."""
        logging.debug(f"Fetching InfoPrices for {identifiers_string} on {exchange_id}")
        req = tr.infoprices.InfoPrices(
            params={
                # "$top": self.api_limits["top_instruments"], # $top might not be valid here
                "AccountKey": self.account_key,
                "ExchangeId": exchange_id,
                "FieldGroups": "Commissions,DisplayAndFormat,Greeks,HistoricalChanges,InstrumentPriceDetails,MarketDepth,PriceInfo,PriceInfoDetails,Quote",
                "Uics": identifiers_string,
                "AssetType": "WarrantOpenEndKnockOut", # Be specific if possible
            }
        )
        try:
            return self.api_client.request(req)
        except ApiRequestException as e:
             logging.warning(f"ApiRequestException during _get_instrument_details (will retry): {e}")
             raise # Re-raise for tenacity

    def find_turbos(self, exchange_id: str, underlying_uics: str, keywords: str):
        """Finds suitable turbo warrants based on criteria."""
        logging.info(f"Finding turbos: Exchange={exchange_id}, Underlying={underlying_uics}, Keywords={keywords}")

        # 1. Initial Instrument Search
        req_instruments = rd.instruments.Instruments(
            params={
                "$top": self.api_limits["top_instruments"],
                "AccountKey": self.account_key,
                "ExchangeId": exchange_id,
                "Keywords": keywords,
                "IncludeNonTradable": False,
                "UnderlyingUics": underlying_uics,
                "AssetTypes": "WarrantKnockOut,WarrantOpenEndKnockOut",
            }
        )
        response_instruments = self.api_client.request(req_instruments)

        if not response_instruments or not response_instruments.get("Data"):
             logging.warning("No instruments found in initial search.")
             raise NoTurbosAvailableException("No instruments found in initial search.", search_context=req_instruments.params)

        # 2. Parse and Filter Initial List
        valid_items = []
        for item in response_instruments["Data"]:
            parsed_data = parse_saxo_turbo_description(item.get("Description", ""))
            if parsed_data:
                item["appParsedData"] = parsed_data
                valid_items.append(item)
            else:
                logging.warning(f"Failed to parse description: {item.get('Description')}")

        if not valid_items:
            logging.warning("No instruments remaining after parsing descriptions.")
            raise NoTurbosAvailableException("No instruments found with parsable descriptions.", search_context=req_instruments.params)

        logging.debug(f"Found {len(valid_items)} instruments with valid descriptions.")

        # 3. Sort by Knock-out Price (from parsed data)
        sort_reverse = keywords.lower() != "short" # True for long (higher price first), False for short (lower price first)
        try:
            sorted_instruments = sorted(
                valid_items,
                key=lambda x: float(x["appParsedData"]["price"]),
                reverse=sort_reverse,
            )
        except (KeyError, ValueError) as e:
            logging.error(f"Error sorting instruments by parsed price: {e}")
            raise ValueError("Could not sort instruments by parsed price.") from e

        # 4. Get Detailed Price Info for Sorted Instruments
        identifiers = [item["Identifier"] for item in sorted_instruments]
        if not identifiers:
             # Should be caught earlier, but defensive check
             raise NoTurbosAvailableException("No identifiers found after sorting.", search_context=req_instruments.params)

        identifiers_string = ",".join(map(str, identifiers[:self.api_limits["top_instruments"]])) # Limit query size

        try:
            response_infoprices = self._get_instrument_details(identifiers_string, exchange_id)
        except RetryError as e:
             logging.error(f"Failed to get instrument details after multiple retries: {e}")
             raise ApiRequestException("Failed to get instrument details after retries.") from e

        if not response_infoprices or not response_infoprices.get("Data"):
            logging.warning(f"No InfoPrice data received for identifiers: {identifiers_string}")
            raise NoTurbosAvailableException("No detailed price info received.", search_context={'Uics': identifiers_string})

        # 5. Filter by Market State and Availability
        available_items = [
            item for item in response_infoprices["Data"]
            if item.get("Quote") and
               item["Quote"].get("PriceTypeAsk") != "NoMarket" and
               item["Quote"].get("PriceTypeBid") != "NoMarket" and # Check Bid too
               item["Quote"].get("MarketState") != "Closed" and
               "Ask" in item["Quote"] and # Ensure Ask price exists
               "Bid" in item["Quote"] # Ensure Bid price exists
        ]

        if not available_items:
            logging.warning("No instruments available after filtering market state/price types.")
            raise NoMarketAvailableException(f"No markets available for {keywords} turbo in {exchange_id}.")

        logging.debug(f"{len(available_items)} instruments available after market state filtering.")

        # 6. Filter by Price Range (using Bid price for selection consistency)
        min_price = self.turbo_price_range["min"]
        max_price = self.turbo_price_range["max"]
        price_filtered_items = [
            item for item in available_items
            if min_price <= item["Quote"]["Bid"] <= max_price
        ]

        if not price_filtered_items:
            logging.warning(f"No turbos found within price range {min_price}-{max_price}.")
            raise NoTurbosAvailableException(
                f"No turbos found in price range {min_price}-{max_price}.",
                search_context={'PriceRange': (min_price, max_price), 'AvailableCount': len(available_items)}
            )

        logging.debug(f"{len(price_filtered_items)} instruments available after price filtering.")

        # 7. Select the Best Match (first one after filtering)
        # Re-sort based on original criteria (knock-out price) before selecting
        # This ensures we pick the one with the best knock-out among the price-valid ones.
        # Need to merge parsed data back or re-parse. Simpler: select based on Bid price for now.
        # If selection MUST be based on knock-out, need more complex merging/lookup.
        # Let's sort the final candidates by Bid price (opposite of initial KO sort logic)
        final_sort_reverse = not sort_reverse
        final_candidates = sorted(price_filtered_items, key=lambda x: x["Quote"]["Bid"], reverse=final_sort_reverse)

        selected_turbo_info = deepcopy(final_candidates[0])

        # 8. (Optional but recommended) Get latest snapshot price for the *selected* turbo
        # This avoids using potentially slightly stale data from the batch InfoPrices call
        try:
             # This uses the same subscription mechanism as before, maybe simplify?
             # Alternative: Just use the data from InfoPrices snapshot if sufficient
             # For now, retain the subscription logic but isolate it.
             snapshot = self._get_price_snapshot(selected_turbo_info["Uic"], selected_turbo_info["AssetType"])
             selected_turbo_info['CurrentPriceSnapshot'] = snapshot # Add latest price snapshot
        except Exception as e:
             logging.warning(f"Failed to get final price snapshot for {selected_turbo_info['Uic']}, proceeding with InfoPrice data: {e}")
             selected_turbo_info['CurrentPriceSnapshot'] = selected_turbo_info # Fallback

        # 9. Prepare and Return Result
        result = {
            "input_criteria": {
                "exchange_id": exchange_id,
                "underlying_uics": underlying_uics,
                "keywords": keywords,
            },
            "selected_instrument": {
                "uic": selected_turbo_info["Uic"],
                "asset_type": selected_turbo_info["AssetType"],
                "description": selected_turbo_info.get("DisplayAndFormat", {}).get("Description", "N/A"),
                "symbol": selected_turbo_info.get("DisplayAndFormat", {}).get("Symbol", "N/A"),
                "currency": selected_turbo_info.get("DisplayAndFormat", {}).get("Currency", "N/A"),
                "decimals": selected_turbo_info.get("DisplayAndFormat", {}).get("OrderDecimals", 2),
                "parsed_data": parse_saxo_turbo_description(selected_turbo_info.get("DisplayAndFormat", {}).get("Description", "")),
                "quote": selected_turbo_info.get("Quote", {}),
                "commissions": selected_turbo_info.get("Commissions", {}),
                # Include the latest snapshot if available
                "latest_price_snapshot": selected_turbo_info.get('CurrentPriceSnapshot', selected_turbo_info).get("Quote", {}),
                 # Add potentially needed fields from the latest snapshot
                "latest_ask": selected_turbo_info.get('CurrentPriceSnapshot', selected_turbo_info).get("Quote", {}).get("Ask"),
                "latest_bid": selected_turbo_info.get('CurrentPriceSnapshot', selected_turbo_info).get("Quote", {}).get("Bid"),
            }
        }
        logging.info(f"Selected Turbo: {result['selected_instrument']['description']}")
        return result


    def _get_price_snapshot(self, uic, asset_type):
         """Gets a single price snapshot for a specific instrument."""
         # Simplified: Use InfoPrices endpoint for a single UIC to get a snapshot
         # Avoiding the complexity of managing price subscriptions here if only snapshot needed
         logging.debug(f"Getting price snapshot for Uic: {uic}")
         req = tr.infoprices.InfoPrice( # Use singular InfoPrice endpoint
               Uic=uic,
               params={
                    "AccountKey": self.account_key,
                    "AssetType": asset_type,
                    "FieldGroups": "Commissions,DisplayAndFormat,InstrumentPriceDetails,MarketDepth,PriceInfo,PriceInfoDetails,Quote", # Adjust FieldGroups as needed
               }
          )
         response = self.api_client.request(req)
         if not response: # Handle cases where InfoPrice might return empty on success (unlikely)
             raise ValueError(f"Received empty response for InfoPrice request for {uic}")
         return response # The response itself is the snapshot structure


class OrderService:
    """Handles placing, retrieving, and cancelling orders."""

    def __init__(self, api_client: SaxoApiClient, account_key: str, client_key: str):
        self.api_client = api_client
        self.account_key = account_key
        self.client_key = client_key # Needed for some order endpoints

    def place_market_order(self, uic: int, asset_type: str, amount: int, buy_sell: str, order_duration: str = "DayOrder"):
        """Places a market order."""
        logging.info(f"Placing Market Order: {buy_sell} {amount} of {uic} ({asset_type})")
        pre_order_payload = {
            "Uic": uic,
            "AssetType": asset_type,
            "Amount": amount,
            "OrderType": "Market",
            "ManualOrder": False, # Assuming automated trades
            "BuySell": buy_sell, # "Buy" or "Sell"
            "OrderDuration": {"DurationType": order_duration},
            # Add StopLoss/TakeProfit here if API supports them reliably now
            # "Orders": [ # Example for OCO - requires specific Saxo structure
            #     onfill.StopLossDetails(...),
            #     onfill.TakeProfitDetails(...)
            # ]
        }

        # Inject AccountKey using the utility
        final_order_payload = tie_account_to_order(self.account_key, pre_order_payload)
        logging.debug(f"Final order payload: {json.dumps(final_order_payload)}")

        request_order = tr.orders.Order(data=final_order_payload)
        try:
            validated_order = self.api_client.request(request_order)
        except OrderPlacementError as e:
            # Add context if possible (re-raised from api_client)
             e.order_details = final_order_payload # Ensure order details are attached
             logging.error(f"Order placement rejected by API: {e}")
             raise e # Re-raise the specific error
        except SaxoApiError as e:
             logging.error(f"API error during order placement: {e}")
             # Potentially wrap in OrderPlacementError if context suggests it
             raise OrderPlacementError(f"API error during order placement: {e}", saxo_error_details=e.saxo_error_details, order_details=final_order_payload) from e

        if not validated_order or not validated_order.get("OrderId"):
            logging.error(f"Order placement response missing OrderId: {validated_order}")
            raise OrderPlacementError("Order placement response missing OrderId.", order_details=final_order_payload, saxo_error_details=validated_order)

        logging.info(f"Order placed successfully. OrderId: {validated_order['OrderId']}")
        return validated_order # Return the full response from Saxo

    def get_single_order(self, order_id: str):
        """Retrieves details for a single open order."""
        logging.debug(f"Getting details for order: {order_id}")
        req_single_order = pf.orders.GetOpenOrder(
            ClientKey=self.client_key,
            OrderId=order_id
        )
        return self.api_client.request(req_single_order)

    def get_open_orders(self):
        """Retrieves all open orders for the account."""
        # Note: Uses subscription endpoint in original code, might need adjustment
        # For simplicity, let's assume a snapshot endpoint exists or adapt as needed.
        # Using the subscription endpoint requires managing ContextId etc.
        # Let's use a hypothetical simpler endpoint for now, or adapt subscription.
        # Placeholder:
        logging.warning("get_open_orders using subscription endpoint logic not fully implemented here. Adapt if needed.")
        # context_id = str(uuid.uuid1()) # Need to manage context IDs statefully if using subscriptions
        # reference_id = str(uuid.uuid1())
        # req_all_order = pf.orders.CreateOpenOrdersSubscription(...)
        # return self.api_client.request(req_all_order)['Snapshot'] # Example
        # If no snapshot endpoint, this needs more complex state management.
        # Fallback: query orders individually if needed, less efficient.
        return {"Data": []} # Placeholder - implement properly

    def cancel_order(self, order_id: str):
        """Cancels a specific order."""
        logging.info(f"Attempting to cancel order: {order_id}")
        request_cancel = tr.orders.CancelOrders(
            OrderIds=order_id,
            params={"AccountKey": self.account_key}
        )
        try:
             # API might return 200 OK with details or potentially 204 No Content
             response = self.api_client.request(request_cancel)
             logging.info(f"Order cancellation request sent for {order_id}. Response: {response}")
             return response # Or True if successful
        except Exception as e:
             logging.error(f"Failed to cancel order {order_id}: {e}")
             raise # Re-raise error for upstream handling

class PositionService:
    """Handles retrieving position and balance information."""

    def __init__(self, api_client: SaxoApiClient, config_manager: ConfigurationManager, account_key: str, client_key: str):
        self.api_client = api_client
        self.config = config_manager
        self.account_key = account_key
        self.client_key = client_key
        self.api_limits = self.config.get_config_value("trade.config.general.api_limits", {"top_positions": 200, "top_closed_positions": 500})
        self.retry_config = self.config.get_config_value("trade.config.general.retry_config", {"max_retries": DEFAULT_RETRY_ATTEMPTS, "retry_sleep_seconds": DEFAULT_RETRY_WAIT_SECONDS})


    def get_open_positions(self):
        """Retrieves all open positions for the account."""
        logging.debug("Getting open positions...")
        req_positions = pf.positions.PositionsMe(
            params={
                # "$top": self.api_limits["top_positions"], # Be careful with $top if pagination needed
                "ClientKey": self.client_key, # Often required
                "AccountKey": self.account_key, # Sometimes required
                "FieldGroups": "PositionBase,PositionView,DisplayAndFormat,ExchangeInfo", # Adjust fields as needed
            }
        )
        response = self.api_client.request(req_positions)
        # Add __count if not present for compatibility, or adjust usage upstream
        if response and 'Data' in response and '__count' not in response:
             response['__count'] = len(response['Data'])
        elif not response:
             return {'__count': 0, 'Data': []} # Return empty structure
        return response


    def get_closed_positions(self, top: int = None, skip: int = 0):
        """Retrieves closed positions."""
        if top is None:
             top = self.api_limits["top_closed_positions"]
        logging.debug(f"Getting closed positions (Top={top}, Skip={skip})...")
        req_positions = pf.closedpositions.ClosedPositionsMe(
            params={
                "$top": top,
                "$skip": skip,
                "AccountKey": self.account_key, # Often required
                "FieldGroups": "ClosedPosition,ClosedPositionDetails,DisplayAndFormat,ExchangeInfo", # Adjust as needed
            }
        )
        response = self.api_client.request(req_positions)
        if not response:
             return {'__count': 0, 'Data': []}
        return response

    def get_single_position(self, position_id: str):
        """Retrieves details for a single position."""
        logging.debug(f"Getting single position details for: {position_id}")
        request_single_position = pf.positions.SinglePosition(
            PositionId=position_id,
            params={
                "ClientKey": self.client_key,
                "AccountKey": self.account_key, # Often needed
                "FieldGroups": "PositionBase,PositionView,DisplayAndFormat,Costs,ExchangeInfo", # Adjust as needed
            }
        )
        return self.api_client.request(request_single_position)

    @retry(stop=stop_after_attempt(DEFAULT_RETRY_ATTEMPTS), wait=wait_fixed(DEFAULT_RETRY_WAIT_SECONDS), retry=retry_if_exception_type(PositionNotFoundException))
    def find_position_by_order_id_with_retry(self, order_id: str):
         """Finds an open position matching a source order ID, with retries."""
         logging.debug(f"Attempting to find position for OrderId: {order_id}")
         all_positions = self.get_open_positions()

         if all_positions and 'Data' in all_positions:
             for position in all_positions["Data"]:
                 if position.get("PositionBase", {}).get("SourceOrderId") == order_id:
                      logging.info(f"Position {position.get('PositionId')} found for order ID {order_id}.")
                      return position # Return the found position

         logging.warning(f"Position not found for OrderId {order_id} in current open positions. Retrying...")
         raise PositionNotFoundException(f"Position for order {order_id} not found yet.", order_id=order_id)


    def get_spending_power(self):
        """Gets the current account spending power."""
        logging.debug("Getting account balance/spending power...")
        # Assuming balance endpoint provides this. Adjust if needed.
        req_balance = pf.balances.AccountBalances(
            params={"ClientKey": self.client_key}
        )
        resp_balance = self.api_client.request(req_balance)
        if not resp_balance or "SpendingPower" not in resp_balance:
             logging.error(f"Invalid balance response: {resp_balance}")
             raise SaxoApiError("Invalid balance response received, missing SpendingPower.")
        spending_power = resp_balance["SpendingPower"]
        if not isinstance(spending_power, (int, float)):
             logging.error(f"Invalid SpendingPower value: {spending_power}")
             raise SaxoApiError(f"Invalid SpendingPower value received: {spending_power}")

        logging.info(f"Spending Power retrieved: {spending_power}")
        return spending_power


# === High-Level Orchestration & Monitoring ===

class TradingOrchestrator:
    """Orchestrates the process of executing a trade signal."""

    def __init__(self, instrument_service: InstrumentService, order_service: OrderService, position_service: PositionService, config_manager: ConfigurationManager, db_order_manager: DbOrderManager, db_position_manager: DbPositionManager):
        self.instrument_service = instrument_service
        self.order_service = order_service
        self.position_service = position_service
        self.config = config_manager
        self.db_order_manager = db_order_manager
        self.db_position_manager = db_position_manager
        self.buying_power_config = self.config.get_config_value("trade.config.buying_power", {})
        self.safety_margins = self.buying_power_config.get("safety_margins", {"bid_calculation": 1})
        self.retry_config = self.config.get_config_value("trade.config.general.retry_config", {"max_retries": DEFAULT_RETRY_ATTEMPTS, "retry_sleep_seconds": DEFAULT_RETRY_WAIT_SECONDS})


    def _calculate_bid_amount(self, turbo_info: dict, spending_power: float):
        """Calculates the amount to buy based on turbo price and spending power."""
        # Use the latest snapshot Ask price if available, otherwise fallback
        ask_price = turbo_info['selected_instrument'].get('latest_ask')
        if ask_price is None:
             ask_price = turbo_info['selected_instrument'].get('quote', {}).get('Ask')

        if ask_price is None or not isinstance(ask_price, (int, float)) or ask_price <= 0:
            raise ValueError(f"Invalid ask price for bid calculation: {ask_price}")

        logging.info(f"Calculating amount: SpendingPower={spending_power}, AskPrice={ask_price}")

        max_account_percent = self.buying_power_config.get("max_account_funds_to_use_percentage", 100)
        available_funds = spending_power * (max_account_percent / 100.0)
        logging.info(f"Available funds for trading ({max_account_percent}% of {spending_power}): {available_funds:.2f}")

        safety_margin_units = self.safety_margins.get("bid_calculation", 1)
        cost_per_unit = ask_price # Add estimated commission per unit if significant and available

        # Check if funds cover at least one unit + margin
        if available_funds < (cost_per_unit * (1 + safety_margin_units)):
             pre_amount = 0 # Cannot afford even one unit with margin
        else:
             # Calculate max units affordable
             max_units = available_funds / cost_per_unit
             # Subtract safety margin (as units)
             pre_amount = max_units - safety_margin_units

        amount = int(math.floor(pre_amount))

        if amount <= 0:
             raise InsufficientFundsException(
                 message=f"Insufficient funds to buy required units @ {ask_price:.{turbo_info['selected_instrument']['decimals']}f}",
                 available_funds=available_funds,
                 required_price=ask_price,
                 calculated_amount=amount
             )

        logging.info(f"Calculated bid amount: {amount}")
        return amount


    def execute_trade_signal(self, exchange_id: str, underlying_uics: str, keywords: str):
        """
        Full workflow: Find -> Calculate -> Place Order -> Confirm Position -> **Persist Order/Position**.
        Returns details for logging/notification, not for DB persistence by caller.
        """
        logging.info(f"--- Executing & Recording Trade Signal: {keywords} on {underlying_uics} ---")
        confirmed_position = None # Initialize
        validated_order = None # Initialize
        turbo_info = None # Initialize

        try:
            # 1. Find Turbo
            # Exceptions (NoTurbos, NoMarket, Api) handled by caller or bubble up
            turbo_info = self.instrument_service.find_turbos(exchange_id, underlying_uics, keywords)

            # 2. Get Spending Power
            # Exceptions (Api, SaxoApiError) handled by caller or bubble up
            spending_power = self.position_service.get_spending_power()

            # 3. Calculate Amount
            # Raises InsufficientFundsException, ValueError
            amount = self._calculate_bid_amount(turbo_info, spending_power)

            # 4. Place Buy Order
            # Raises OrderPlacementError, SaxoApiError, ApiRequestException
            validated_order = self.order_service.place_market_order(
                uic=turbo_info['selected_instrument']['uic'],
                asset_type=turbo_info['selected_instrument']['asset_type'],
                amount=amount,
                buy_sell="Buy"
            )
            order_id = validated_order['OrderId']

            # 5. Confirm Position Creation (with retry)
            confirmed_position = self.position_service.find_position_by_order_id_with_retry(order_id)

            # --- *** 6. Persist to Database *** ---
            now_utc = datetime.now(pytz.utc)
            # Prepare Order Data for DB
            order_data_for_db = {
                "action": keywords, "buy_sell": "Buy", "order_id": order_id, "order_amount": amount,
                "order_type": "Market",
                "order_kind": "main", "order_submit_time": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "related_order_id": [],
                "position_id": confirmed_position.get("PositionId"),
                "instrument_name": turbo_info['selected_instrument']['description'],
                "instrument_symbol": turbo_info['selected_instrument']['symbol'],
                "instrument_uic": turbo_info['selected_instrument']['uic'],
                "instrument_price": turbo_info['selected_instrument'].get('latest_ask'),
                "instrument_currency": turbo_info['selected_instrument']['currency'],
                "order_cost": turbo_info['selected_instrument'].get('commissions', {}).get('CostBuy'),
            }
            # Prepare Position Data for DB
            pos_base = confirmed_position.get("PositionBase", {});
            pos_disp = confirmed_position.get("DisplayAndFormat", {})
            position_data_for_db = {
                "action": keywords, "position_id": confirmed_position.get("PositionId"),
                "position_amount": pos_base.get("Amount"),
                "position_open_price": pos_base.get("OpenPrice"),
                "position_total_open_price": (pos_base.get("Amount", 0) * pos_base.get("OpenPrice", 0)),
                "position_status": pos_base.get("Status", "Open"), "position_kind": "main",
                "execution_time_open": pos_base.get("ExecutionTimeOpen"),
                "order_id": pos_base.get("SourceOrderId"), "related_order_id": pos_base.get("RelatedOpenOrders", []),
                "instrument_name": pos_disp.get("Description"), "instrument_symbol": pos_disp.get("Symbol"),
                "instrument_uic": pos_base.get("Uic"), "instrument_currency": pos_disp.get("Currency"),
            }

            # Perform DB Inserts
            try:
                logging.info(f"Persisting order {order_id} to database...")
                self.db_order_manager.insert_turbo_order_data(order_data_for_db)
                # TODO: Handle potential sub-orders if OCO/related orders are implemented
                logging.info(f"Persisting position {position_data_for_db['position_id']} to database...")
                self.db_position_manager.insert_turbo_open_position_data(position_data_for_db)
                logging.info("Order and position persisted successfully.")
            except Exception as db_err:
                # CRITICAL: Trade executed but failed to record in DB!
                logging.critical(
                    f"CRITICAL DB ERROR: Failed to persist order/position after execution! OrderID: {order_id}, PositionID: {position_data_for_db['position_id']}. Error: {db_err}",
                    exc_info=True)
                # Raise a specific error indicating this critical state
                raise DatabaseOperationException(f"CRITICAL: Failed to persist executed trade OrderID {order_id}",
                                                 operation="insert_trade_data", entity_id=order_id) from db_err

            logging.info(
                f"Trade execution & recording successful for OrderId {order_id}, PositionId {confirmed_position.get('PositionId')}")

            # --- *** 7. Return Execution Details (for logging/notification) *** ---
            # Return details that might be useful for the caller (e.g., for composer)
            return {
                "order_details": order_data_for_db,  # Return the prepared DB data
                "position_details": position_data_for_db,
                "selected_turbo_info": turbo_info,
                "message": f"Successfully executed and recorded trade for {keywords}."
            }

        # --- Exception Handling during Execution ---
        except PositionNotFoundException as e:
            # This specific error already attempted order cancellation inside find_position...
            logging.critical(
                f"CRITICAL: Position not found for OrderId {e.order_id} after retries. Order cancellation attempted.")
            # Re-raise the critical exception for the main callback handler
            raise e
        except Exception as e:
            # Catch other errors (NoTurbos, InsufficientFunds, OrderPlacementError, DB errors, etc.)
            logging.error(f"Trade execution failed during '{keywords}' signal: {e}", exc_info=True)
            # Attempt cleanup if order was placed but position failed *before* confirmation loop
            if validated_order and not confirmed_position:
                order_id_to_cancel = validated_order.get('OrderId')
                if order_id_to_cancel:
                    logging.warning(
                        f"Attempting to cancel potentially orphan order {order_id_to_cancel} due to execution failure.")
                    try:
                        self.order_service.cancel_order(order_id_to_cancel)
                        logging.info(f"Successfully cancelled potentially orphan order {order_id_to_cancel}")
                    except Exception as cancel_err:
                        logging.error(f"Failed to cancel potentially orphan order {order_id_to_cancel}: {cancel_err}")
            # Re-raise the original error for the main callback handler
            raise e


class PerformanceMonitor:
    """Monitors open positions, checks performance, triggers closures, syncs DB."""

    def __init__(self, position_service: PositionService, order_service: OrderService, config_manager: ConfigurationManager, db_position_manager: DbPositionManager, trading_rule: TradingRule, rabbit_connection):
        self.position_service = position_service
        self.order_service = order_service
        self.config = config_manager
        self.db_position_manager = db_position_manager # Needed for daily profit check, max performance
        self.trading_rule = trading_rule # Needed for daily profit target
        self.rabbit_connection = rabbit_connection # For notifications
        self.perf_config = self.config.get_config_value("trade.config.position_management", {})
        self.thresholds = self.perf_config.get("performance_thresholds", {"stoploss_percent": -20, "max_profit_percent": 60})
        self.general_config = self.config.get_config_value("trade.config.general", {})
        self.timezone = self.general_config.get("timezone", "Europe/Paris")
        self.logging_config = self.config.get_logging_config()
        # Get daily profit target from trading_rule config
        try:
            day_trading_rules = self.trading_rule.get_rule_config("day_trading")
            self.percent_profit_wanted_per_days = day_trading_rules.get("percent_profit_wanted_per_days", 1.0) # Default 1%
        except Exception as e:
             logging.warning(f"Could not get day_trading rules for profit target, defaulting: {e}")
             self.percent_profit_wanted_per_days = 1.0


    def check_all_positions_performance(self):
        """Checks performance of all DB-managed open positions against thresholds and rules."""
        logging.info("--- Checking Performance of Open Positions ---")
        db_open_positions = self.db_position_manager.get_open_positions_details() # Assume this returns list of dicts with needed data like position_id, action
        if not db_open_positions:
            logging.info("No manageable open positions found in database to check.")
            return {"closed_positions": [], "db_updates": []}

        db_position_ids = [p['position_id'] for p in db_open_positions]
        logging.debug(f"Checking DB positions: {db_position_ids}")

        try:
            api_positions_response = self.position_service.get_open_positions()
            api_positions_dict = {p["PositionId"]: p for p in api_positions_response.get("Data", [])}
        except Exception as e:
            logging.error(f"Failed to get open positions from API during performance check: {e}")
            # Cannot proceed without API data
            return {"closed_positions": [], "db_updates": []} # Or raise


        positions_to_close = []
        db_updates = [] # List of (position_id, update_dict) tuples

        for db_pos in db_open_positions:
            position_id = db_pos['position_id']
            if position_id not in api_positions_dict:
                logging.warning(f"Position {position_id} is open in DB but not found in API open positions. Triggering sync check later.")
                # TODO: Add mechanism to trigger sync_db_positions_with_api if this happens frequently
                continue

            api_pos = api_positions_dict[position_id]

            # 1. Calculate Performance
            open_price = api_pos.get("PositionBase", {}).get("OpenPrice")
            current_bid = api_pos.get("PositionView", {}).get("Bid")
            performance_percent = None
            if open_price and current_bid and open_price != 0:
                 performance_percent = round(((current_bid * 100) / open_price) - 100, 2)
                 logging.info(f"Pos {position_id}: Open={open_price}, Bid={current_bid}, Perf={performance_percent}%")

                 # Record detailed performance log
                 self._log_performance_detail(position_id, api_pos, performance_percent)

                 # Check for new max performance
                 max_perf = self.db_position_manager.get_max_position_percent(position_id)
                 if performance_percent > max_perf:
                      db_updates.append((position_id, {"position_max_performance_percent": performance_percent}))
            else:
                 logging.warning(f"Could not calculate performance for {position_id}: OpenPrice={open_price}, Bid={current_bid}")
                 continue # Skip checks if performance cannot be calculated

            # 2. Check Thresholds
            close_reason = None
            if performance_percent <= self.thresholds["stoploss_percent"]:
                 close_reason = f"Stoploss threshold ({self.thresholds['stoploss_percent']}%) hit"
            elif performance_percent >= self.thresholds["max_profit_percent"]:
                 close_reason = f"Takeprofit threshold ({self.thresholds['max_profit_percent']}%) hit"

            # 3. Check Daily Profit Target (only if not already closing)
            if not close_reason:
                 try:
                      today_percent = self.db_position_manager.get_percent_of_the_day()
                      # Calculate potential final % if this position is closed now
                      today_profit_factor = 1.0 + (today_percent / 100.0)
                      position_profit_factor = 1.0 + (performance_percent / 100.0)
                      potential_today_factor = today_profit_factor * position_profit_factor # This assumes only one position, needs adjustment for multiple
                      # Simplification: If current position profit + today's realized profit exceeds target
                      # This is complex logic, refine based on exact requirement (e.g., use total account value change)
                      # Using simpler check for now: if potential daily profit including this position exceeds target
                      potential_today_percent = round((today_profit_factor * position_profit_factor - 1) * 100, 2)

                      if potential_today_percent >= self.percent_profit_wanted_per_days:
                            close_reason = f"Daily profit target ({self.percent_profit_wanted_per_days}%) potentially met ({potential_today_percent}%)"
                 except Exception as e:
                      logging.error(f"Error checking daily profit target for {position_id}: {e}")


            # 4. Add to Close List if Needed
            if close_reason:
                 logging.info(f"Marking position {position_id} for closure. Reason: {close_reason}")
                 positions_to_close.append({"position_id": position_id, "api_details": api_pos, "reason": close_reason})


        # 5. Execute Closures
        closed_position_ids = []
        for pos_to_close in positions_to_close:
             api_pos = pos_to_close["api_details"]
             position_id = pos_to_close["position_id"]
             close_reason = pos_to_close["reason"]

             if not api_pos.get("PositionBase", {}).get("CanBeClosed", False):
                 logging.warning(f"Position {position_id} flagged for closure but CanBeClosed is False. Skipping.")
                 continue

             try:
                 direction = direction_from_amount(api_pos["PositionBase"]["Amount"])
                 order_direction = direction_invert(direction) # Sell to close Buy, Buy to close Sell
                 logging.info(f"Attempting to close {position_id} ({order_direction} {api_pos['PositionBase']['Amount']}). Reason: {close_reason}")

                 # Place closing order
                 close_order_result = self.order_service.place_market_order(
                     uic=api_pos["PositionBase"]["Uic"],
                     asset_type=api_pos["PositionBase"]["AssetType"],
                     amount=api_pos["PositionBase"]["Amount"],
                     buy_sell=order_direction
                 )
                 closed_position_ids.append({"id": position_id, "close_reason": close_reason})
                 logging.info(f"Close order placed for {position_id}. OrderId: {close_order_result.get('OrderId')}")

                 # Send immediate notification (optional, sync might handle final update)
                 close_message = f"PERF CLOSE: Closing {api_pos['DisplayAndFormat']['Description']} (PosID: {position_id}). Reason: {close_reason}. Close Order: {close_order_result.get('OrderId')}"
                 send_message_to_mq_for_telegram(self.rabbit_connection, close_message)

             except (OrderPlacementError, SaxoApiError, ApiRequestException) as e:
                  logging.error(f"Failed to place close order for position {position_id}: {e}")
                  # Send error notification
                  error_message = f"ERROR: Failed closing {position_id}. Reason: {close_reason}. Error: {e}"
                  send_message_to_mq_for_telegram(self.rabbit_connection, error_message)
                  # Should we raise? Or just log and continue checking others? Continue for now.
             except Exception as e:
                  logging.error(f"Unexpected error closing position {position_id}: {e}", exc_info=True)
                  error_message = f"CRITICAL ERROR: Unexpected error closing {position_id}. Error: {e}"
                  send_message_to_mq_for_telegram(self.rabbit_connection, error_message)
                  # Continue for now

        # Return results for DB updates (actual updates happen in main.py handler)
        return {"closed_positions": closed_position_ids, "db_updates": db_updates}


    def sync_db_positions_with_api(self):
        """Compares DB open positions with API closed positions and returns updates."""
        logging.info("--- Syncing DB Positions with API Closed Positions ---")
        db_open_positions = self.db_position_manager.get_open_positions_ids() # Get only IDs
        if not db_open_positions:
            logging.info("No open positions in DB to sync.")
            return {"updates_for_db": []}

        try:
            api_open_positions_response = self.position_service.get_open_positions()
            api_open_position_ids = {p["PositionId"] for p in api_open_positions_response.get("Data", [])}
        except Exception as e:
            logging.error(f"Failed to get API open positions during sync: {e}")
            return {"updates_for_db": []} # Cannot proceed

        potential_closed_in_db = []
        for db_pos_id in db_open_positions:
            if db_pos_id not in api_open_position_ids:
                logging.info(f"Position {db_pos_id} is open in DB but not in API open list. Checking closed API positions.")
                potential_closed_in_db.append(db_pos_id)

        if not potential_closed_in_db:
            logging.info("All DB open positions found in API open positions. Sync complete.")
            return {"updates_for_db": []}

        # Fetch recent closed positions from API
        try:
            # Fetch a decent number to increase chance of finding the match
            api_closed_positions_response = self.position_service.get_closed_positions(top=len(potential_closed_in_db) + 50)
            # Create dict mapping OpeningPositionId to closed position data
            api_closed_map = {
                p["ClosedPosition"]["OpeningPositionId"]: p
                for p in api_closed_positions_response.get("Data", [])
                if "ClosedPosition" in p and "OpeningPositionId" in p["ClosedPosition"]
            }
        except Exception as e:
            logging.error(f"Failed to get API closed positions during sync: {e}")
            return {"updates_for_db": []} # Cannot proceed


        updates_for_db = []
        for position_id_to_check in potential_closed_in_db:
            if position_id_to_check in api_closed_map:
                 api_closed_pos = api_closed_map[position_id_to_check]
                 logging.info(f"Found match for DB open position {position_id_to_check} in API closed positions. Preparing DB update.")

                 close_price = api_closed_pos["ClosedPosition"]["ClosingPrice"]
                 open_price = api_closed_pos["ClosedPosition"]["OpenPrice"]
                 amount = api_closed_pos["ClosedPosition"]["Amount"]

                 performance_percent = None
                 if open_price and open_price != 0:
                      performance_percent = round(((close_price * 100) / open_price) - 100, 2)

                 update_data = {
                     "position_close_price": close_price,
                     "position_profit_loss": api_closed_pos["ClosedPosition"]["ProfitLossOnTrade"],
                     "position_total_close_price": close_price * amount,
                     "position_status": "Closed",
                     "position_total_performance_percent": performance_percent,
                     "position_close_reason": "SaxoAPI", # Indicates found closed via API sync
                     "execution_time_close": api_closed_pos["ClosedPosition"]["ExecutionTimeClose"],
                 }
                 updates_for_db.append((position_id_to_check, update_data))

                 # Send notification
                 message = f"""SYNC CLOSE: Position {position_id_to_check} ({api_closed_pos['DisplayAndFormat']['Description']}) closed on API.
Open: {open_price}, Close: {close_price}, Amount: {amount}
P/L: {update_data['position_profit_loss']}, Perf: {performance_percent}%
Close Time: {update_data['execution_time_close']}"""
                 send_message_to_mq_for_telegram(self.rabbit_connection, message)

            else:
                 # Position is open in DB, not in API open, not in recent API closed.
                 # This is an anomaly. Maybe closed long ago, or error state.
                 logging.warning(f"ANOMALY: Position {position_id_to_check} open in DB, not found in API open or recent closed positions.")
                 # Consider marking it as 'Unknown' or 'SyncError' in DB? For now, just log.
                 # updates_for_db.append((position_id_to_check, {"position_status": "SyncError", "position_close_reason": "SyncAnomaly"}))


        logging.info(f"Sync check complete. Found {len(updates_for_db)} positions closed on API to update in DB.")
        return {"updates_for_db": updates_for_db}


    def _log_performance_detail(self, position_id, api_pos, performance_percent):
        """Writes detailed performance data to a JSONL file."""
        try:
            current_time = datetime.now(pytz.timezone(self.timezone))
            open_time_str = api_pos.get("PositionBase", {}).get("ExecutionTimeOpen")
            open_time = None
            open_hour = None
            open_minute = None
            if open_time_str:
                try:
                     open_time = datetime.fromisoformat(open_time_str).astimezone(pytz.timezone(self.timezone))
                     open_hour = open_time.hour
                     open_minute = open_time.minute
                except ValueError:
                     logging.warning(f"Could not parse open time {open_time_str}")


            performance_json = {
                "position_id": position_id,
                "performance": performance_percent,
                "open_price": api_pos.get("PositionBase", {}).get("OpenPrice"),
                "bid": api_pos.get("PositionView", {}).get("Bid"),
                "time": current_time.strftime("%Y-%m-%d %H:%M:%S"),
                "current_hour": current_time.hour,
                "current_minute": current_time.minute,
                "open_hour": open_hour,
                "open_minute": open_minute
            }

            # Construct the filename using today's date
            today_date = current_time.strftime("%Y-%m-%d")
            log_path = self.logging_config.get('persistant', {}).get('log_path', '.') # Get log path safely
            filename = os.path.join(log_path, f"performance_{today_date}.jsonl")

            # Write the performance_json to the JSON Lines file
            with open(filename, 'a') as file:
                file.write(json.dumps(performance_json) + '\n')

        except Exception as e:
            logging.error(f"Failed to write performance log for position {position_id}: {e}")

    def close_managed_positions_by_criteria(self, action_filter: str | None = None):
        """
        Closes open positions managed by the app, optionally filtered by action ('long'/'short').
        Relies on sync_db_positions_with_api for final DB updates.

        Args:
            action_filter: If 'long' or 'short', closes only positions matching that action.
                           If None, closes all managed open positions.
        """
        logging.info(f"--- Closing Managed Positions by Criteria (Filter: {action_filter}) ---")
        closed_initiated_count = 0
        errors_count = 0

        # 1. Get currently open positions managed by the app from DB
        # Need position_id and the original action ('long'/'short')
        try:
            db_open_positions = self.db_position_manager.get_open_positions_details() # Assume this gets needed fields
            if not db_open_positions:
                 logging.info("No managed positions open in DB to close.")
                 return {"closed_initiated_count": 0, "errors_count": 0}
        except Exception as e:
            logging.error(f"Failed to get open positions from DB for closure: {e}")
            raise # Re-raise as we cannot proceed

        # 2. Get currently open positions from API
        try:
            api_positions_response = self.position_service.get_open_positions()
            api_positions_dict = {p["PositionId"]: p for p in api_positions_response.get("Data", [])}
        except Exception as e:
            logging.error(f"Failed to get open positions from API for closure: {e}")
            raise # Re-raise as we cannot compare

        # 3. Filter and Initiate Closure
        for db_pos in db_open_positions:
            position_id = db_pos.get('position_id')
            db_action = db_pos.get('action') # Get action ('long'/'short') from DB data

            # Apply filter
            if action_filter and db_action != action_filter:
                continue # Skip if action doesn't match filter

            # Check if position exists and is closable on API
            if position_id not in api_positions_dict:
                logging.warning(f"Position {position_id} (Action: {db_action}) to be closed is not open on API. Skipping (will be synced later).")
                continue

            api_pos = api_positions_dict[position_id]
            if not api_pos.get("PositionBase", {}).get("CanBeClosed", False):
                logging.warning(f"Position {position_id} (Action: {db_action}) cannot be closed via API (CanBeClosed=False). Skipping.")
                continue

            # Initiate closure
            try:
                direction = direction_from_amount(api_pos["PositionBase"]["Amount"])
                order_direction = direction_invert(direction)
                logging.info(f"Initiating explicit close for {position_id} (Action: {db_action}), Filter: {action_filter}. Order: {order_direction} {api_pos['PositionBase']['Amount']}")

                close_order_result = self.order_service.place_market_order(
                    uic=api_pos["PositionBase"]["Uic"],
                    asset_type=api_pos["PositionBase"]["AssetType"],
                    amount=api_pos["PositionBase"]["Amount"],
                    buy_sell=order_direction
                )
                closed_initiated_count += 1
                logging.info(f"Close order placed for {position_id}. OrderId: {close_order_result.get('OrderId')}")

                # Send notification
                close_message = f"EXPLICIT CLOSE: Closing {api_pos['DisplayAndFormat']['Description']} (PosID: {position_id}, Action: {db_action}). Filter: {action_filter}. Order: {close_order_result.get('OrderId')}"
                send_message_to_mq_for_telegram(self.rabbit_connection, close_message)

            except (OrderPlacementError, SaxoApiError, ApiRequestException) as e:
                 logging.error(f"Failed to place explicit close order for position {position_id}: {e}")
                 error_message = f"ERROR: Failed explicit close for {position_id} (Action: {db_action}). Error: {e}"
                 send_message_to_mq_for_telegram(self.rabbit_connection, error_message)
                 errors_count += 1
            except Exception as e:
                 logging.error(f"Unexpected error during explicit close for position {position_id}: {e}", exc_info=True)
                 error_message = f"CRITICAL ERROR: Unexpected error during explicit close for {position_id}. Error: {e}"
                 send_message_to_mq_for_telegram(self.rabbit_connection, error_message)
                 errors_count += 1

        logging.info(f"Explicit closure process finished. Initiated: {closed_initiated_count}, Errors: {errors_count}. Final DB updates via sync.")
        return {"closed_initiated_count": closed_initiated_count, "errors_count": errors_count}