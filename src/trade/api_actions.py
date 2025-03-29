from time import sleep

from src.saxo_openapi.contrib.session import account_info
from src.saxo_openapi.contrib.orders import MarketOrder, tie_account_to_order
import src.saxo_openapi.endpoints.referencedata as rd
import src.saxo_openapi.endpoints.trading as tr
import src.saxo_openapi.endpoints.portfolio as pf
import src.saxo_openapi.contrib.orders.onfill as onfill
from src.saxo_openapi.contrib.orders import direction_from_amount
from src.saxo_openapi.contrib.orders.helper import direction_invert

import websocket
import time
from src.saxo_openapi.contrib.ws import stream

from src.saxo_authen import SaxoAuth
from src.saxo_openapi import API

from . import exceptions

import logging
from copy import deepcopy
import uuid
import re
import json
import math
from datetime import datetime
import pytz
import random

from src.mq_telegram.tools import send_message_to_mq_for_telegram


def parse_saxo_turbo_description(description):
    # Adjusted regex to capture the last four parts of the string
    # This assumes that the last four parts are always in the format: kind, buysell, price, from
    # This allows for prices without a decimal part
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
    else:
        return None


class SaxoService:
    def __init__(
            self, config_manager, db_order_manager, db_position_manager, rabbit_connection, trading_rule
    ):
        # Retrieve logging configuration
        self.logging_config = config_manager.get_logging_config()
        self.percent_profit_wanted_per_days = trading_rule.get_rule_config("profit_per_days")["percent_profit_wanted_per_days"]

        self.saxo_auth = SaxoAuth(config_manager)

        self.db_order_manager = db_order_manager
        self.db_position_manager = db_position_manager
        self.rabbit_connection = rabbit_connection
        self.context_id = str(uuid.uuid1())

        try:
            self.token = self.saxo_auth.get_token()
        except Exception as token_exception:
            logging.error(f"Error getting token: {token_exception}")
            raise token_exception
        self.saxo_client = API(access_token=self.token, environment="live")
        self.account_info = account_info(self.saxo_client)

    def find_turbos(self, ExchangeId, UnderlyingUics, Keywords):
        try:
            r = rd.instruments.Instruments(
                params={
                    "$top": 200,
                    "AccountKey": self.account_info.AccountKey,
                    "ExchangeId": ExchangeId,
                    "Keywords": Keywords,
                    "IncludeNonTradable": False,
                    "UnderlyingUics": UnderlyingUics,
                    "AssetTypes": "WarrantKnockOut,WarrantOpenEndKnockOut",
                }
            )

            try:
                response = self.saxo_client.request(r)
            except Exception as exception:
                message = f"Error while get instruments : {exception}"
                logging.error(message)
                raise message

            initial_count = len(response["Data"])
            logging.debug(f"Initial Data Count: {initial_count}")

            # Create a new list to hold items that pass the parsing
            valid_items = []

            for item in response["Data"]:
                description = item["Description"]
                parsed_data = parse_saxo_turbo_description(description)
                if parsed_data is None:
                    # Log an error message and skip this item
                    logging.error(
                        f"Failed to parse instrument description: {description}"
                    )
                else:
                    item["appParsedData"] = parsed_data
                    valid_items.append(item)

            # Replace the original list with the filtered list
            response["Data"] = valid_items

            if Keywords == "short":
                # Sort the data by price
                sorted_data = sorted(
                    response["Data"],
                    key=lambda x: float(x["appParsedData"]["price"]),
                    reverse=False,
                )
            else:
                # Sort the data by price
                sorted_data = sorted(
                    response["Data"],
                    key=lambda x: float(x["appParsedData"]["price"]),
                    reverse=True,
                )
            # Extract the Identifiers from the sorted data
            identifiers = [item["Identifier"] for item in sorted_data]
            identifiers_string = ",".join(map(str, identifiers))
            logging.debug(f"Identifiers String: {identifiers_string}")

            r = tr.infoprices.InfoPrices(
                params={
                    "$top": 200,
                    "AccountKey": self.account_info.AccountKey,
                    "ExchangeId": ExchangeId,
                    "FieldGroups": "Commissions,DisplayAndFormat,Greeks,HistoricalChanges,InstrumentPriceDetails,MarketDepth,PriceInfo,PriceInfoDetails,Quote",
                    "Uics": identifiers_string,
                    "AssetType": "WarrantOpenEndKnockOut",
                }
            )

            try:
                response = self.saxo_client.request(r)
            except Exception as exception:
                message = f"Error while get InfoPrices : {exception}"
                logging.error(message)
                raise message

            info_prices_count = len(response["Data"])
            logging.debug(f"Data Count After InfoPrices Request: {info_prices_count}")

            # Clean NoMarket
            response["Data"] = [
                item
                for item in response["Data"]
                if item["Quote"]["PriceTypeAsk"] != "NoMarket"
            ]

            no_market_count = len(response["Data"])
            logging.debug(f"Data Count After Cleaning NoMarket: {no_market_count}")

            sorted_data = sorted(response["Data"], key=lambda x: x["Quote"]["Mid"])
            mid_sorted_count = len(sorted_data)
            logging.debug(f"Data Count After Sorting by Mid: {mid_sorted_count}")

            # Now, 'filtered_data' contains only the items where 'Mid' is between 4 and 15
            filtered_data = [
                item for item in sorted_data if 4 <= item["Quote"]["Mid"] <= 15
            ]

            final_count = len(filtered_data)
            logging.debug(f"Data Count After Filtering by Mid Range: {final_count}")

            # Check if filtered_data is empty
            if not filtered_data:
                # Raise the custom exception if no turbos are available
                raise exceptions.NoTurbosAvailableException(
                    "No turbos are available for the asked price."
                    f" - Data Count After InfoPrices Request: {info_prices_count}"
                    f" - Data Count After Cleaning NoMarket: {no_market_count}"
                    f" - Data Count After Sorting by Mid: {mid_sorted_count}"
                    f" - Data Count After Filtering by Mid Range: {final_count}"
                )

            info_price_final = deepcopy(filtered_data[0])

            reference_id = str(uuid.uuid1())

            r = tr.prices.CreatePriceSubscription(
                data={
                    "Arguments": {
                        "Uic": info_price_final["Uic"],
                        "AccountKey": self.account_info.AccountKey,
                        "AssetType": info_price_final["AssetType"],
                        "Amount": 1,
                        "FieldGroups": [
                            "Commissions",
                            "DisplayAndFormat",
                            "Greeks",
                            "HistoricalChanges",
                            "InstrumentPriceDetails",
                            "MarketDepth",
                            "PriceInfo",
                            "PriceInfoDetails",
                            "Quote",
                            "Timestamps",
                        ],
                    },
                    "ContextId": self.context_id,
                    "ReferenceId": reference_id,
                    "RefreshRate": 10000,
                    "Format": "application/json",
                }
            )

            try:
                response = self.saxo_client.request(r)
            except Exception as exception:
                logging.error(f"Error while CreatePriceSubscription : {exception}")
                raise exception

            price_final = deepcopy(response["Snapshot"])
            response = {
                "input": {
                    "exchange_id": ExchangeId,
                    "underlying_uics": UnderlyingUics,
                    "keywords": Keywords,
                },
                "appParsedData": parse_saxo_turbo_description(
                    price_final["DisplayAndFormat"]["Description"]
                ),
                "info_price": info_price_final,
                "price": price_final,
                "context_id": self.context_id,
                "reference_id": reference_id,
                "reason": "No Problem",
            }

            # TODO : Faire la gestion du "MarketState": "Closed" dans "Quote"
            return response
        except Exception as e:
            logging.error(f"Error while finding turbos: {e}")
            raise e

    def get_single_order(self, order_id):
        req_single_order = pf.orders.GetOpenOrder(
            ClientKey=self.account_info.ClientKey, OrderId=order_id
        )
        try:
            resp_single_order = self.saxo_client.request(req_single_order)
        except Exception as exception:
            logging.error(f"Error while get order id {order_id}: {exception}")
            raise exception

        return resp_single_order

    def get_all_order(self):
        reference_id = str(uuid.uuid1())

        req_all_order = pf.orders.CreateOpenOrdersSubscription(
            data={
                "Arguments": {
                    "AccountKey": self.account_info.AccountKey,
                    "ClientKey": self.account_info.ClientKey,
                },
                "ContextId": self.context_id,
                "Format": "application/json",
                "ReferenceId": reference_id,
                "RefreshRate": 1000,
            }
        )
        try:
            resp_all_order = self.saxo_client.request(req_all_order)
        except Exception as exception:
            logging.error(f"Error while get order all order of the client: {exception}")
            raise exception

        return resp_all_order

    def get_user_open_positions(self):
        # request the balances
        req_positions = pf.positions.PositionsMe(
            params={
                "$top": 200,
                "FieldGroups": "Costs,DisplayAndFormat,ExchangeInfo,Greeks,PositionBase,PositionIdOnly,PositionView",
            }
        )
        try:
            resp_positions = self.saxo_client.request(req_positions)
        except Exception as exception:
            logging.error(f"Error while get user trading position : {exception}")
            raise exception

        return resp_positions

    def get_user_closed_positions(self, skip=0, top=500):
        req_positions = pf.closedpositions.ClosedPositionsMe(
            params={
                "$skip": skip,
                "$top": top,
                "FieldGroups": "ClosedPosition,ClosedPositionDetails,DisplayAndFormat,ExchangeInfo",
            }
        )
        try:
            resp_positions = self.saxo_client.request(req_positions)
        except Exception as exception:
            logging.error(f"Error while get user closed position : {exception}")
            raise exception

        return resp_positions

    def get_single_positions(self, position_id):
        # request the balances
        request_single_position = pf.positions.SinglePosition(
            PositionId=position_id,
            params={"ClientKey": self.account_info.ClientKey,
                    "FieldGroups": "Costs,DisplayAndFormat,Greeks,PositionBase,PositionIdOnly,PositionView",
                    }
        )
        try:
            resp_single_position = self.saxo_client.request(request_single_position)
        except Exception as exception:
            logging.error(f"Error while try get single position: {exception}")
            raise exception
        return resp_single_position

    def calcul_bid_amount(self, founded_turbo):
        # request the balances
        req_balance = pf.balances.AccountBalances(
            params={"ClientKey": self.account_info.ClientKey}
        )
        try:
            resp_balance = self.saxo_client.request(req_balance)
        except Exception as exception:
            logging.error(f"Error while get account balance : {exception}")
            raise exception

        spending_power = resp_balance["SpendingPower"]
        logging.debug(f"Cash Balance on account: {spending_power}")

        pre_amount = spending_power / founded_turbo["price"]["Quote"]["Ask"]
        # Round down and convert to integer, minus 1 to assure transaction
        amount = int(math.floor(pre_amount)) - 1
        if amount <= 0:
            error_message = (
                f"The current spending power ({spending_power}) cannot buy 1 or more turbo @ "
                f"{founded_turbo['price']['Quote']['Ask']} (with trading security)"
            )
            logging.error(error_message)
            raise ValueError(error_message)
        return amount

    def calculate_bid_amount(self, founded_turbo):
        """Calculate bid amount with basic safety checks."""
        try:
            # Validate turbo data
            if not founded_turbo or 'price' not in founded_turbo:
                raise ValueError("Invalid turbo data for bid calculation")

            ask_price = founded_turbo["price"]["Quote"]["Ask"]
            if not ask_price or ask_price <= 0:
                raise ValueError(f"Invalid ask price for bid calculation: {ask_price}")

            # Get balance
            req_balance = pf.balances.AccountBalances(
                params={"ClientKey": self.account_info.ClientKey}
            )
            resp_balance = self.saxo_client.request(req_balance)

            if not resp_balance or "SpendingPower" not in resp_balance:
                raise ValueError("Invalid balance response for bid calculation")

            spending_power = resp_balance["SpendingPower"]
            logging.info(f"Cash Balance: {spending_power}")

            # Calculate amount with safety margin
            safety_margin = 1
            pre_amount = (spending_power / ask_price) - safety_margin
            amount = int(math.floor(pre_amount))

            if amount <= 0:
                raise ValueError(
                    f"Insufficient funds to buy 1 or more turbo @ {ask_price}"
                    f" (balance: {spending_power})"
                )

            return amount

        except Exception as e:
            logging.error(f"Error in bid calculation: {str(e)}")
            raise

    def buy_turbo_instrument(self, founded_turbo):
        try:
            amount = self.calculate_bid_amount(founded_turbo)

            # TODO : Faire un Trailing stop
            stop_loss_price = round(
                float(founded_turbo["price"]["Quote"]["Ask"] * 0.90),
                founded_turbo["price"]["DisplayAndFormat"]["OrderDecimals"],
            )
            profit_price = round(
                float(founded_turbo["price"]["Quote"]["Ask"] * 1.10),
                founded_turbo["price"]["DisplayAndFormat"]["OrderDecimals"],
            )
            logging.debug(f"Order Stop Loss Price: {stop_loss_price}")
            logging.debug(f"Order Profit Price: {profit_price}")

            pre_order = MarketOrder(
                Uic=founded_turbo["price"]["Uic"],
                AssetType=founded_turbo["price"]["AssetType"],
                Amount=amount,
                # TODO : Saxo API return error if StopLossOnFill and TakeProfitOnFill are set
                # StopLossOnFill=onfill.StopLossDetails(stop_loss_price),
                # TakeProfitOnFill=onfill.TakeProfitDetails(profit_price),
            )

            # Use tie_account_to_order to inject the AccountKey
            final_order = tie_account_to_order(self.account_info.AccountKey, pre_order)
            print(json.dumps(final_order))


            request_order = tr.orders.Order(data=final_order)
            try:
                order_validated = self.saxo_client.request(request_order)
            except Exception as exception:
                logging.error(f"Error while send buy order with {json.dumps(final_order)}, response is : {exception}")
                raise exception

            if not order_validated["OrderId"]:
                message = f"Error in buy order response there is no OrderId: {order_validated}"
                logging.error(message)
                send_message_to_mq_for_telegram(self.rabbit_connection, message)
                raise ValueError(message)

            try:
                position = self.find_position_with_validated_order(order_validated)
            except Exception as e:
                logging.error(
                    f"Error while get position for order id {order_validated["OrderId"]}: {e}"
                )
                raise e


            # Get the current time in UTC
            now_utc = datetime.now(pytz.utc)

            # Main Order
            db_main_order = {
                "action": founded_turbo["input"]["keywords"],
                "buy_sell": final_order["BuySell"],
                "order_id": order_validated["OrderId"],
                "order_amount": final_order["Amount"],
                "order_type": final_order["OrderType"],
                "order_kind": "main",
                "order_submit_time": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "related_order_id": [],  # This will be filled with sub-order IDs
                "position_id": position["PositionId"],
                "instrument_name": founded_turbo["price"]["DisplayAndFormat"][
                    "Description"
                ],
                "instrument_symbol": founded_turbo["price"]["DisplayAndFormat"][
                    "Symbol"
                ],
                "instrument_uic": final_order["Uic"],
                "instrument_price": founded_turbo["price"]["Quote"]["Ask"],
                "instrument_currency": founded_turbo["price"]["DisplayAndFormat"][
                    "Currency"
                ],
                "order_cost": founded_turbo["price"]["Commissions"]["CostBuy"],
            }

            if "Orders" in order_validated:
                # Sub-Orders
                sub_orders = []
                for i, sub_order in enumerate(final_order["Orders"]):
                    db_sub_order = {
                        "action": founded_turbo["input"]["keywords"],
                        "buy_sell": sub_order["BuySell"],
                        "order_id": order_validated["Orders"][i]["OrderId"],
                        "order_amount": sub_order["Amount"],
                        "order_type": sub_order["OrderType"],
                        "order_kind": "sub",
                        "order_submit_time": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "related_order_id": [
                            db_main_order["order_id"]
                        ],  # Link to main order
                        "position_id": None,
                        "instrument_name": founded_turbo["price"]["DisplayAndFormat"][
                            "Description"
                        ],
                        "instrument_symbol": founded_turbo["price"]["DisplayAndFormat"][
                            "Symbol"
                        ],
                        "instrument_uic": sub_order["Uic"],
                        "instrument_price": sub_order["OrderPrice"],
                        "instrument_currency": founded_turbo["price"][
                            "DisplayAndFormat"
                        ]["Currency"],
                        "order_cost": founded_turbo["price"]["Commissions"]["CostBuy"],
                    }
                    sub_orders.append(db_sub_order)

                # Update the main order with the sub-order IDs
                db_main_order["related_order_id"] = [
                    sub_order["order_id"] for sub_order in sub_orders
                ]

                # Combine the main order and sub-orders into a single list
                db_orders_list = [db_main_order] + sub_orders

            else:
                db_orders_list = [db_main_order]

            # Insert the order into the database
            for db_order in db_orders_list:
                self.db_order_manager.insert_turbo_order_data(db_order)
            position_total_open_price = float(
                position["PositionBase"]["Amount"]
                * position["PositionBase"]["OpenPrice"]
            )
            turbo_position_data_at_open = {
                "action": founded_turbo["input"]["keywords"],
                "position_id": position["PositionId"],
                "position_amount": position["PositionBase"]["Amount"],
                "position_open_price": position["PositionBase"]["OpenPrice"],
                "position_total_open_price": position_total_open_price,
                "position_status": position["PositionBase"]["Status"],
                "position_kind": "main",
                "execution_time_open": position["PositionBase"]["ExecutionTimeOpen"],
                "order_id": position["PositionBase"]["SourceOrderId"],
                "related_order_id": position["PositionBase"]["RelatedOpenOrders"],
                "instrument_name": position["DisplayAndFormat"]["Description"],
                "instrument_symbol": position["DisplayAndFormat"]["Symbol"],
                "instrument_uic": position["PositionBase"]["Uic"],
                "instrument_currency": position["DisplayAndFormat"]["Currency"],
            }
            # Insert the initial position data at open
            self.db_position_manager.insert_turbo_open_position_data(
                turbo_position_data_at_open
            )
            logging.info(
                f"Successfully open turbo position for {position["DisplayAndFormat"]["Description"]}"
            )

            buy_details = {
                "orders_list": db_orders_list,
                "position": turbo_position_data_at_open,
            }
            return buy_details

        except Exception as e:
            logging.error(f"Error while buying turbos: {e}")
            raise e

    def close_position(self, position):
        if not position["PositionBase"]["CanBeClosed"]:
            raise ValueError("Position already closed")

        direction = direction_from_amount(position["PositionBase"]["Amount"])
        order_direction = direction_invert(direction)

        pre_order = {
            "Uic": position["PositionBase"]["Uic"],
            "AssetType": position["PositionBase"]["AssetType"],
            "Amount": position["PositionBase"]["Amount"],
            "OrderType": "Market",
            "ManualOrder": False,
            "BuySell": order_direction,
            "OrderDuration": {"DurationType": "DayOrder"},
        }
        final_order = tie_account_to_order(self.account_info.AccountKey, pre_order)
        request_close = tr.orders.Order(data=final_order)
        try:
            order_validated = self.saxo_client.request(request_close)
        except Exception as exception:
            logging.error(f"Error while send {order_direction} order : {exception}")
            raise exception
        if not order_validated["OrderId"]:
            message = f"Error in {order_direction} order response there is no OrderId: {order_validated}"
            logging.error(message)
            send_message_to_mq_for_telegram(self.rabbit_connection, message)
            raise ValueError(message)

        logging.info(
            f"Successfully {order_direction} turbo position for {position['DisplayAndFormat']['Description']} with Order ID {order_validated["OrderId"]}"
        )

    def find_position_with_validated_order(self, order_validated):
        max_retries = 10
        retries = 0
        position_found = False

        while retries < max_retries:
            all_positions = self.get_user_open_positions()
            for position in all_positions["Data"]:
                if (
                        position["PositionBase"]["SourceOrderId"]
                        == order_validated["OrderId"]
                ):
                    return position
            sleep(1)
            retries += 1  # Increment the retry counter

        if not position_found:
            logging.error(
                f"Position not found after {max_retries} retries for order ID {order_validated['OrderId']}"
            )
            request_cancel_order = tr.orders.CancelOrders(
                OrderIds=order_validated["OrderId"],
                params={"AccountKey": self.account_info.AccountKey}
            )
            try:
                self.saxo_client.request(request_cancel_order)
                logging.info(
                    f"Successfully cancel order {order_validated['OrderId']}"
                )
                raise ValueError(
                    f"Position not found after {max_retries} retries for order ID {order_validated['OrderId']},"
                    f" Successfully cancel order {order_validated['OrderId']}"
                )
            except Exception as e:
                message = f"Position not found after {max_retries} retries for order ID {order_validated['OrderId']}, failed to cancel order {order_validated['OrderId']} because of exception {e}"
                logging.error(message)
                raise ValueError(message)

    def check_and_act_close_on_current_positions(
            self, all_positions, preferred_action=None
    ):
        if all_positions["__count"] > 0:
            logging.info(f"Found {all_positions["__count"]} positions")

            position_to_check_in_db = []
            for position in all_positions["Data"]:
                position_to_check_in_db.append(position["PositionId"])

            position_ids_db = self.db_position_manager.check_position_ids_exist(
                position_to_check_in_db
            )

            if position_ids_db["position_ids_not_found"]:
                logging.info(
                    f"Saxo give these positions id {position_ids_db["position_ids_not_found"]}, but they aren't in the database, so they don't managed by the app"
                )
                print(
                    f"Saxo give these positions id {position_ids_db["position_ids_not_found"]}, but they aren't in the database, so they don't managed by the app"
                )

            if position_ids_db["position_ids_in_db"]:
                logging.info(
                    f"Found these opens position to close {position_ids_db["position_ids_in_db"]}"
                )
                print(
                    f"Found these opens position to close {position_ids_db["position_ids_in_db"]}"
                )

                self.close_open_position_from_app(
                    all_positions, position_ids_db, preferred_action
                )
        else:
            logging.info("No open position found in Saxo API")

    def close_open_position_from_app(
            self, all_positions, position_ids_db, preferred_action=None
    ):
        position_ids_closed = []
        for api_position in all_positions["Data"]:
            if not api_position["PositionBase"]["CanBeClosed"]:
                message = (f"Position {api_position["PositionId"]} can't be closed, because API said CanBeClosed"
                           f" {api_position["PositionBase"]["CanBeClosed"]}")
                logging.error(message)
                raise ValueError(message)

            for db_position in position_ids_db["position_ids_in_db"]:
                if (
                        "SourceOrderId" in api_position["PositionBase"]
                        and api_position["PositionBase"]["SourceOrderId"]
                        == db_position["order_id"]
                ):
                    logging.debug(
                        f"Position {api_position["PositionId"]} match with database `order_id`"
                    )
                    if preferred_action:
                        if db_position["action"] == preferred_action:
                            try:
                                self.close_position(api_position)
                            except Exception as e:
                                logging.error(
                                    f"Failed to close position {api_position['PositionId']} : {e}"
                                )
                                raise e
                            position_ids_closed.append(api_position["PositionId"])
                        else:
                            logging.warning(
                                f"Not supported case : because the action {preferred_action} mismatch with "
                                f"db action {db_position["action"]} for position {api_position["PositionId"]}"
                            )
                            return None
                    else:
                        try:
                            self.close_position(api_position)
                        except Exception as e:
                            logging.error(
                                f"Failed to close position {api_position['PositionId']} : {e}"
                            )
                            raise e
                        position_ids_closed.append(api_position["PositionId"])
                else:
                    logging.error(
                        f"Error Order ID mismatch : API position {api_position["PositionId"]} and "
                        f"database position {db_position["position_id"]} doesn't match in order "
                        f"id : api/db = {api_position["PositionBase"]["SourceOrderId"]} / "
                        f"{db_position["order_id"]}"
                    )
                    raise ValueError(
                        f"Error Order ID mismatch : API position {api_position["PositionId"]} and "
                        f"database position {db_position["position_id"]} doesn't match in order "
                        f"id : api/db = {api_position["PositionBase"]["SourceOrderId"]} / "
                        f"{db_position["order_id"]}"
                    )

        if len(position_ids_closed) > 0:
            # Wait because some time API did'nt have the position instantly
            sleep(2)
            try:
                all_closed_positions = self.get_user_closed_positions()
            except Exception as e:
                logging.error(f"Failed to get user closed positions : {e}")
                raise e
            self.act_on_db_closed_position(
                all_closed_positions, position_ids_closed, "MEGA Close"
            )

    def act_on_db_closed_position(
            self, all_closed_positions, position_ids_closed, closed_from="Signal"
    ):
        for position_id in position_ids_closed:
            # Initialize a flag to indicate if the position is found
            position_found = False
            for api_closed_position in all_closed_positions["Data"]:
                if (
                        position_id
                        == api_closed_position["ClosedPosition"]["OpeningPositionId"]
                ):
                    position_total_close_price = float(
                        api_closed_position["ClosedPosition"]["ClosingPrice"]
                        * api_closed_position["ClosedPosition"]["Amount"]
                    )
                    performance_percent = round(((api_closed_position["ClosedPosition"]["ClosingPrice"] * 100) /
                                                 api_closed_position["ClosedPosition"]["OpenPrice"]) - 100, 2)

                    turbo_position_data_at_close = {
                        "position_close_price": api_closed_position["ClosedPosition"][
                            "ClosingPrice"
                        ],
                        "position_profit_loss": api_closed_position["ClosedPosition"][
                            "ProfitLossOnTrade"
                        ],
                        "position_total_close_price": position_total_close_price,
                        "position_status": "Closed",
                        "position_total_performance_percent": performance_percent,
                        "position_close_reason": closed_from,
                        "execution_time_close": api_closed_position["ClosedPosition"][
                            "ExecutionTimeClose"
                        ],
                    }
                    try:
                        self.db_position_manager.update_turbo_position_data(
                            position_id, turbo_position_data_at_close
                        )
                    except Exception as e:
                        error_message = f"Failed to update turbo position {position_id} for {api_closed_position['DisplayAndFormat']['Description']} on database : {e}. You need to manually update status for these position ID {position_ids_closed}"
                        logging.critical(error_message)
                        self.db_position_manager.mark_database_as_corrupted(
                            error_message
                        )
                        send_message_to_mq_for_telegram(
                            self.rabbit_connection, f"CRITICAL : {error_message}"
                        )
                        exit(1)
                    logging.info(
                        f"Successfully closed turbo position {position_id} for {api_closed_position["DisplayAndFormat"]["Description"]} on database"
                    )

                    max_position_percent = self.db_position_manager.get_max_position_percent(position_id)
                    today_percent = self.db_position_manager.get_percent_of_the_day()

                    message = f"""
--- CLOSED POSITION ---
Instrument : {api_closed_position["DisplayAndFormat"]["Description"]}
Open Price : {api_closed_position["ClosedPosition"]["OpenPrice"]}
Close Price : {api_closed_position["ClosedPosition"]["ClosingPrice"]}
Amount : {api_closed_position["ClosedPosition"]["Amount"]}
Total price : {position_total_close_price}
Profit/Loss : {api_closed_position["ClosedPosition"]["ProfitLossOnTrade"]}
Performance % : {performance_percent}
Close Time : {api_closed_position["ClosedPosition"]["ExecutionTimeClose"]}
Closed from ? {closed_from}
Opening Position ID : {api_closed_position["ClosedPosition"]["OpeningPositionId"]}
Max position % : {max_position_percent}
-------
Current today profit : {today_percent}%
"""
                    send_message_to_mq_for_telegram(self.rabbit_connection, message)
                    position_found = True
                    break  # No need to check further if we found a match

            # If the position was not found in any api_closed_position
            if not position_found:
                logging.warning(
                    f"Abnormal situation for position {position_id}, because she is not found in Saxo API Closed "
                    f"Positions"
                )

    def check_if_db_open_position_are_closed_on_api(self):
        db_open_position_ids = self.db_position_manager.get_open_positions_ids()
        all_open_positions = self.get_user_open_positions()

        potential_closed_positions = []
        # Iterate through each PositionId in db_open_position_ids
        for position_id in db_open_position_ids:
            # Check if the PositionId is not in any of the Data items
            if not any(
                    item["PositionId"] == position_id for item in all_open_positions["Data"]
            ):
                # If not found, add it to the list of potential closed positions
                potential_closed_positions.append(position_id)

        if len(potential_closed_positions) > 0:
            try:
                all_closed_positions = self.get_user_closed_positions()
            except Exception as e:
                logging.error(f"Failed to get user closed positions : {e}")
                raise e

            self.act_on_db_closed_position(
                all_closed_positions, potential_closed_positions, "Saxo"
            )
        else:
            logging.info(f"No open position in database that are closed on API")

    def check_positions_ws(self):

        reference_id = str(uuid.uuid1())
        request_position_sub = pf.positions.PositionListSubscription(
            data={
                "Arguments": {
                    "ClientKey": self.account_info.ClientKey,
                    "FieldGroups": [
                        "PositionBase",
                        "PositionView"
                    ],

                },
                "ContextId": self.context_id,
                "Format": "application/json",
                "ReferenceId": reference_id,
                "RefreshRate": 1000,
            }
        )
        try:
            resp_position_sub = self.saxo_client.request(request_position_sub)
        except Exception as exception:
            logging.error(f"Error while try to create WS subscription for position: {exception}")
            raise exception

        print("Created WS subscription")
        print(json.dumps(resp_position_sub))
        hdrs = [
            f"Authorization: Bearer {self.token}"
        ]
        URL = f"wss://streaming.saxotrader.com/openapi/streamingws/connect?contextId={self.context_id}"

        ws = websocket.WebSocket()
        ws.connect(URL, header=hdrs)

        start_time = time.time()  # Record the start time
        print(f"Connect to websocket at {start_time}")
        while True:
            result = ws.recv()
            if not result:
                print("Connection no result.")
                # break

            # Process the message
            print("----------------------------------")
            for decoded_message in stream.decode_ws_msg(result):
                print(f"Decoded message: {decoded_message}")

            # Check if the socket has been open for more than 20 seconds
            current_time = time.time()
            if current_time - start_time > 60:
                print("Socket has been open for more than 60 seconds, closing connection.")
                break

        ws.close()  # Close the WebSocket connection when exiting the loop
        request_del_position_sub = pf.positions.PositionSubscriptionRemove(self.context_id, reference_id)
        try:
            resp_del_position_sub = self.saxo_client.request(request_del_position_sub)
        except Exception as exception:
            logging.error(f"Error while try to close WS subscription for position with context {self.context_id} and "
                          f"reference {reference_id}: {exception}")
            raise exception
        print("Deleted WS subscription")

    def check_positions_performance(self):
        # TODO: Plus tard faire un trailing stop
        logging.info("Checking positions performance")
        db_open_position_ids = self.db_position_manager.get_open_positions_ids()
        position_founded = False
        position_ids_closed = []
        if len(db_open_position_ids) > 0:
            start_time = time.time()  # Record the start time
            while True:
                all_open_positions = self.get_user_open_positions()
                # Check if the PositionId is in any of the Data items
                for position_id in db_open_position_ids:
                    # Check if the position wasn't sell during the while
                    if position_id not in position_ids_closed:
                        # Check if the PositionId is not in any of the Data items
                        if not any(
                                item["PositionId"] == position_id for item in all_open_positions["Data"]
                        ):
                            # If not found, add it to the list of potential closed positions
                            message = (
                                f"The position ID {position_id} (from DB), is not in any of open position on SAXO, "
                                f"have you resync the db before ?")
                            logging.error(message)
                            raise ValueError(message)
                        for position_details in all_open_positions["Data"]:
                            if position_details["PositionId"] == position_id:
                                position_founded = True
                                need_close = False
                                performance_percent = round(((position_details["PositionView"]["Bid"] * 100) /
                                                             position_details["PositionBase"]["OpenPrice"]) - 100, 2)

                                # Get the current time in French timezone
                                current_time_compare = datetime.now(pytz.timezone('Europe/Paris'))

                                # Check if performance_percent is between stoploss and takeprofit
                                if -20 < performance_percent <= 60:
                                    message = f"Performance {performance_percent} with price bid at {position_details["PositionView"]["Bid"]} and open at {position_details["PositionBase"]["OpenPrice"]}"
                                    logging.info(message)
                                    print(message)
                                else:
                                    message = f"Close because the performance is {performance_percent}"
                                    print(message)
                                    logging.info(message)
                                    need_close = True

                                today_percent = self.db_position_manager.get_percent_of_the_day()

                                today_profit = 1.00 * (1 + today_percent / 100)

                                today_profit_and_position = today_profit * (1 + performance_percent / 100)

                                today_final_percent = round((today_profit_and_position - 1) * 100 , 2)

                                if today_final_percent >= self.percent_profit_wanted_per_days:
                                    message = f"Close because the today performance can be {today_final_percent}"
                                    print(message)
                                    logging.info(message)
                                    need_close = True

                                # Check if the current time is within the allowed range (08:00 to 22:00)
                                # if 7 <= current_time_compare.hour < 11:
                                #     if -5 < performance_percent <= 0.8:
                                #         message = f"Performance {performance_percent} with price bid at {position_details["PositionView"]["Bid"]} and open at {position_details["PositionBase"]["OpenPrice"]}"
                                #         logging.info(message)
                                #         print(message)
                                #     else:
                                #         message = f"Close because the performance is {performance_percent} before 14:00"
                                #         print(message)
                                #         logging.info(message)
                                #         need_close = True
                                # elif 11 <= current_time_compare.hour < 14:
                                #     if -5 < performance_percent <= 1:
                                #         message = f"Performance {performance_percent} with price bid at {position_details["PositionView"]["Bid"]} and open at {position_details["PositionBase"]["OpenPrice"]}"
                                #         logging.info(message)
                                #         print(message)
                                #     else:
                                #         message = f"Close because the performance is {performance_percent} before 14:00"
                                #         print(message)
                                #         logging.info(message)
                                #         need_close = True
                                # elif 19 <= current_time_compare.hour < 22:
                                #     if -5 < performance_percent <= 1.40:
                                #         message = f"Performance {performance_percent} with price bid at {position_details["PositionView"]["Bid"]} and open at {position_details["PositionBase"]["OpenPrice"]}"
                                #         logging.info(message)
                                #         print(message)
                                #     else:
                                #         message = f"Close because the performance is {performance_percent} before 14:00"
                                #         print(message)
                                #         logging.info(message)
                                #         need_close = True
                                # else:
                                #     # Check if performance_percent is between -5 and 3
                                #     if -5 < performance_percent <= 3:
                                #         message = f"Performance {performance_percent} with price bid at {position_details["PositionView"]["Bid"]} and open at {position_details["PositionBase"]["OpenPrice"]}"
                                #         logging.info(message)
                                #         print(message)
                                #     else:
                                #         message = f"Close because the performance is {performance_percent}"
                                #         print(message)
                                #         logging.info(message)
                                #         need_close = True

                                performance_json = {
                                    "position_id": position_details["PositionId"],
                                    "performance": performance_percent,
                                    "open_price": position_details["PositionBase"]["OpenPrice"],
                                    "bid": position_details["PositionView"]["Bid"],
                                    "time": current_time_compare.strftime("%Y-%m-%d %H:%M:%S"),
                                    "current_hour": current_time_compare.strftime("%H"),
                                    "current_minute": current_time_compare.strftime("%M"),
                                    "open_hour": datetime.fromisoformat(
                                        position_details["PositionBase"]["ExecutionTimeOpen"]).astimezone(
                                        pytz.timezone('Europe/Paris')).hour,
                                    "open_minute": datetime.fromisoformat(
                                        position_details["PositionBase"]["ExecutionTimeOpen"]).astimezone(
                                        pytz.timezone('Europe/Paris')).minute
                                }
                                self.write_to_performance_jsonl_file(performance_json)

                                #logging.info(f"Performance_json : {json.dumps(performance_json)}")

                                max_position_percent = self.db_position_manager.get_max_position_percent(
                                    position_details["PositionId"])

                                if performance_percent > max_position_percent:
                                    turbo_position_performance_db_data = {
                                        "position_max_performance_percent": performance_percent
                                    }
                                    try:
                                        self.db_position_manager.update_turbo_position_data(
                                            position_id, turbo_position_performance_db_data
                                        )
                                    except Exception as e:
                                        error_message = f"Failed to update performance for position {position_id} on database : {e}."
                                        logging.error(error_message)
                                        send_message_to_mq_for_telegram(
                                            self.rabbit_connection, f"CRITICAL : {error_message}"
                                        )

                                if need_close:
                                    print(f"Close position {position_details['PositionId']}")
                                    try:
                                        self.close_position(position_details)
                                    except Exception as e:
                                        logging.error(
                                            f"Failed to close position {position_details['PositionId']} : {e}"
                                        )
                                        raise e
                                    position_ids_closed.append(position_details["PositionId"])
                                # Stop the loop
                                break

                # Check if the socket has been open for more than 30 seconds
                current_time = time.time()
                if current_time - start_time > 20:
                    logging.info("Checking for more than 20 seconds, closing connection.")
                    break
                sleep(7)

        if not position_founded:
            logging.info(f"No manageable position in database can be check")

        if len(position_ids_closed) > 0:
            # Wait because some time API did'nt have the position instantly
            sleep(2)
            try:
                all_closed_positions = self.get_user_closed_positions()
            except Exception as e:
                logging.error(f"Failed to get user closed positions : {e}")
                raise e
            self.act_on_db_closed_position(
                all_closed_positions, position_ids_closed, "Performance limits exceeded"
            )

    def write_to_performance_jsonl_file(self, performance_json):
        # Get today's date in YYYY-MM-DD format
        today_date = datetime.now(pytz.timezone('Europe/Paris')).strftime("%Y-%m-%d")

        # Construct the filename using today's date
        filename = f"{self.logging_config['persistant']['log_path']}/performance_{today_date}.jsonl"

        # Write the performance_json to the JSON Lines file
        with open(filename, 'a') as file:
            file.write(json.dumps(performance_json) + '\n')
