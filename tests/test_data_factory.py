from copy import deepcopy

class TestDataFactory:
    """
    Factory for creating consistent and reusable test data fixtures for testing
    the trading application components.
    """

    @staticmethod
    def create_saxo_instrument(
        uic=123,
        identifier=456,
        description="TURBO LONG DAX 15000 CITI",
        asset_type="WarrantKnockOut",
        **overrides
    ):
        """Creates a basic instrument as returned from the initial instrument search."""
        instrument = {
            "Uic": uic,
            "Identifier": identifier,
            "Description": description,
            "AssetType": asset_type,
            "ExchangeId": "XEUR",
            "Symbol": "DE000CD4PTZ4",
            "Status": "Tradable",
            "CurrencyCode": "EUR",
            "PrimaryListing": identifier,
            "TradableOn": ["ATS", "SAXO"],
        }
        instrument.update(overrides)
        return instrument

    @staticmethod
    def create_saxo_infoprice(
        uic=123,
        asset_type="WarrantKnockOut",
        bid=10.0,
        ask=10.1,
        market_state="Open",
        price_type_ask="Tradable",
        price_type_bid="Tradable",
        **overrides
    ):
        """Creates a detailed InfoPrice object for an instrument."""
        infoprice = {
            "Uic": uic,
            "AssetType": asset_type,
            "LastUpdated": "2023-10-27T10:00:00.000Z",
            "PriceSource": "Calculated",
            "Quote": {
                "Amount": 1000,
                "Ask": ask,
                "Bid": bid,
                "DelayedByMinutes": 0,
                "MarketState": market_state,
                "PriceTypeAsk": price_type_ask,
                "PriceTypeBid": price_type_bid,
            },
            "DisplayAndFormat": {
                 "Currency": "EUR",
                 "Decimals": 3,
                 "Description": "TURBO LONG DAX 15000 CITI",
                 "Symbol": "DE000CD4PTZ4"
            }
        }
        # Deep merge overrides for nested structures like 'Quote'
        if overrides:
            base = deepcopy(infoprice)
            # A simple deep merge for 1-level nesting
            for key, value in overrides.items():
                if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                    base[key].update(value)
                else:
                    base[key] = value
            return base
        return infoprice

    @staticmethod
    def create_price_subscription_snapshot(
        uic=123,
        description="Final TURBO LONG DAX 15000 CITI",
        ask=10.05,
        bid=9.95,
        **overrides
    ):
        """Creates a snapshot as returned from a price subscription."""
        snapshot = {
            "Uic": uic,
            "DisplayAndFormat": {"Description": description, "Symbol": "Sym", "Currency": "EUR", "Decimals": 2},
            "Quote": {"Ask": ask, "Bid": bid},
            "Commissions": {"CostBuy": 1.5},
        }
        if overrides:
            base = deepcopy(snapshot)
            for key, value in overrides.items():
                if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                    base[key].update(value)
                else:
                    base[key] = value
            return base
        return snapshot

    @staticmethod
    def create_saxo_position(
        position_id="pos1",
        order_id="order1",
        uic=123,
        amount=100,
        open_price=10.0,
        current_bid=9.5,
        can_be_closed=True,
        **overrides
    ):
        """Creates a mock open position from the Saxo API."""
        position = {
            "PositionId": position_id,
            "PositionBase": {
                "AccountId": "acc1",
                "Amount": amount,
                "AssetType": "WarrantKnockOut",
                "CanBeClosed": can_be_closed,
                "ClientId": "client1",
                "OpenPrice": open_price,
                "SourceOrderId": order_id,
                "Status": "Open",
                "Uic": uic,
            },
            "PositionView": {
                "Bid": current_bid,
                "CurrentPrice": current_bid,
                "MarketValue": current_bid * amount,
                "ProfitLossOnTrade": (current_bid - open_price) * amount,
            },
            "DisplayAndFormat": {"Description": "Test Position", "Currency": "EUR"},
        }
        if overrides:
            base = deepcopy(position)
            for key, value in overrides.items():
                if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                    base[key].update(value)
                else:
                    base[key] = value
            return base
        return position

    @staticmethod
    def create_saxo_closed_position(
        opening_position_id="pos1",
        closing_price=9.5,
        open_price=10.0,
        amount=100,
        **overrides
    ):
        """Creates a mock closed position from the Saxo API."""
        closed_position = {
            "ClosedPosition": {
                "OpeningPositionId": opening_position_id,
                "ClosingPrice": closing_price,
                "OpenPrice": open_price,
                "Amount": amount,
                "ProfitLossOnTrade": (closing_price - open_price) * amount,
                "ExecutionTimeClose": "2023-10-27T12:00:00.000Z",
            },
            "DisplayAndFormat": {"Description": "Test Closed Position"},
        }
        closed_position.update(overrides)
        return closed_position

    @staticmethod
    def create_order_response(order_id="order123", **overrides):
        """Creates a mock successful order placement response."""
        response = {
            "OrderId": order_id,
            "Duration": {"DurationType": "DayOrder"},
            # ... other fields as needed
        }
        response.update(overrides)
        return response
