import pytest
from unittest.mock import patch, MagicMock

from src.trade.api_actions import InstrumentService
from src.trade.exceptions import NoTurbosAvailableException, NoMarketAvailableException, ApiRequestException
from tests.test_data_factory import TestDataFactory

@pytest.fixture
def instrument_service(mock_api_client, mock_config_manager):
    """Fixture for InstrumentService."""
    return InstrumentService(mock_api_client, mock_config_manager, "account_key")

class TestInstrumentService:

    def test_find_turbos_happy_path(self, instrument_service, mock_api_client):
        # Arrange
        initial_instrument = TestDataFactory.create_saxo_instrument(identifier=1)
        infoprice = TestDataFactory.create_saxo_infoprice(uic=101, identifier=1, bid=10.0, ask=10.1)
        price_snapshot = TestDataFactory.create_price_subscription_snapshot(uic=101, ask=10.05, bid=9.95)

        mock_api_client.request.side_effect = [
            {"Data": [initial_instrument]},
            {"Data": [infoprice]},
            {"Snapshot": price_snapshot}
        ]

        # Act
        result = instrument_service.find_turbos("exchange1", "underlying1", "long")

        # Assert
        assert result['selected_instrument']['uic'] == 101
        assert result['selected_instrument']['latest_ask'] == 10.05
        assert result['selected_instrument']['latest_bid'] == 9.95
        assert mock_api_client.request.call_count == 3

    def test_find_turbos_no_initial_instruments(self, instrument_service, mock_api_client):
        mock_api_client.request.return_value = {"Data": []}
        with pytest.raises(NoTurbosAvailableException):
            instrument_service.find_turbos("e1", "u1", "long")

    @patch('src.trade.api_actions.parse_saxo_turbo_description')
    def test_should_handle_malformed_instrument_data(self, mock_parse, instrument_service, mock_api_client):
        """Test that instruments with unparsable descriptions are ignored."""
        # Arrange
        # The first instrument has a description that our mock parser will reject
        instrument1 = TestDataFactory.create_saxo_instrument(identifier=1, description="INVALID")
        # The second one is valid
        instrument2 = TestDataFactory.create_saxo_instrument(identifier=2, description="TURBO LONG DAX 15000 CITI")
        infoprice = TestDataFactory.create_saxo_infoprice(uic=102, identifier=2)
        price_snapshot = TestDataFactory.create_price_subscription_snapshot(uic=102)

        mock_api_client.request.side_effect = [
            {"Data": [instrument1, instrument2]},
            {"Data": [infoprice]},
            {"Snapshot": price_snapshot}
        ]
        # Make parse_saxo_turbo_description return None for the invalid description
        valid_parsed_data = {"name": "TURBO", "kind": "LONG", "buysell": "DAX", "price": "15000", "from": "CITI"}
        mock_parse.side_effect = [
            None, # For instrument1
            valid_parsed_data, # For instrument2
            valid_parsed_data, # For the final result construction
        ]

        # Act
        result = instrument_service.find_turbos("e1", "u1", "long")

        # Assert
        # It should have skipped instrument 1 and selected instrument 2
        assert result['selected_instrument']['uic'] == 102
        # Called for instrument1, instrument2, and the final result construction
        assert mock_parse.call_count == 3
        assert mock_api_client.request.call_count == 3


    @patch('time.sleep', return_value=None)
    def test_should_handle_partial_bid_data_within_tolerance(self, mock_sleep, instrument_service, mock_api_client):
        """Test bid retry logic with partial missing bid data under 50%."""
        # Arrange
        initial_instruments = [TestDataFactory.create_saxo_instrument(identifier=i) for i in range(4)]
        # 3 items have full quote, 1 is missing 'Bid'
        infoprices = [
            TestDataFactory.create_saxo_infoprice(uic=1, identifier=1),
            TestDataFactory.create_saxo_infoprice(uic=2, identifier=2),
            TestDataFactory.create_saxo_infoprice(uic=3, identifier=3),
            TestDataFactory.create_saxo_infoprice(uic=4, identifier=4, overrides={"Quote": {"Ask": 12.0}}) # No Bid
        ]
        price_snapshot = TestDataFactory.create_price_subscription_snapshot(uic=1) # Assume it selects the first one

        mock_api_client.request.side_effect = [
            {"Data": initial_instruments},
            {"Data": infoprices},
            {"Snapshot": price_snapshot}
        ]

        # Act
        result = instrument_service.find_turbos("e1", "u1", "long")

        # Assert
        # The process should continue as <50% of items are missing bids
        assert result is not None
        assert result['selected_instrument']['uic'] == 1
        # The InfoPrice call should only happen once, no retry needed
        # Call 1: Instruments, Call 2: InfoPrices, Call 3: Subscription
        assert mock_api_client.request.call_count == 3
        mock_sleep.assert_not_called()


    @patch('time.sleep', return_value=None)
    def test_should_retry_bid_check_on_websocket_refresh_rate_delay(self, mock_sleep, instrument_service, mock_api_client):
        """Test bid retry logic when >50% of bid data is missing initially."""
        # Arrange
        initial_instruments = [TestDataFactory.create_saxo_instrument(identifier=i) for i in range(4)]

        # First response: 3 out of 4 items are missing bids. Be explicit.
        infoprice_with_bid = TestDataFactory.create_saxo_infoprice(uic=1, identifier=1)
        infoprice_no_bid_1 = TestDataFactory.create_saxo_infoprice(uic=2, identifier=2)
        del infoprice_no_bid_1['Quote']['Bid']
        infoprice_no_bid_2 = TestDataFactory.create_saxo_infoprice(uic=3, identifier=3)
        del infoprice_no_bid_2['Quote']['Bid']
        infoprice_no_bid_3 = TestDataFactory.create_saxo_infoprice(uic=4, identifier=4)
        del infoprice_no_bid_3['Quote']['Bid']

        infoprices_missing_bids = [infoprice_with_bid, infoprice_no_bid_1, infoprice_no_bid_2, infoprice_no_bid_3]

        # Second response: All bids are present
        infoprices_full = [TestDataFactory.create_saxo_infoprice(uic=i, identifier=i-1) for i in range(1, 5)]
        price_snapshot = TestDataFactory.create_price_subscription_snapshot(uic=1)

        mock_api_client.request.side_effect = [
            {"Data": initial_instruments},
            {"Data": infoprices_missing_bids}, # First attempt fails bid check
            {"Data": infoprices_full},         # Second attempt passes
            {"Snapshot": price_snapshot}
        ]

        # Act
        result = instrument_service.find_turbos("e1", "u1", "long")

        # Assert
        assert result is not None
        assert result['selected_instrument']['uic'] == 1
        # Call 1: Instruments, Call 2: InfoPrices (fail), Call 3: InfoPrices (success), Call 4: Subscription
        assert mock_api_client.request.call_count == 4
        mock_sleep.assert_called_once()


    def test_should_handle_price_subscription_partial_failure(self, instrument_service, mock_api_client):
        """Test when price subscription returns a response but is missing the 'Snapshot'."""
        # Arrange
        initial_instrument = TestDataFactory.create_saxo_instrument(identifier=1)
        infoprice = TestDataFactory.create_saxo_infoprice(uic=101, identifier=1, bid=10.0, ask=10.1)
        # The subscription response is valid but has no 'Snapshot' key
        price_sub_response_no_snapshot = {"ContextId": "ctx123"}

        mock_api_client.request.side_effect = [
            {"Data": [initial_instrument]},
            {"Data": [infoprice]},
            price_sub_response_no_snapshot
        ]

        # Act
        result = instrument_service.find_turbos("e1", "u1", "long")

        # Assert
        # Should fall back to using the InfoPrice data
        assert result is not None
        assert result['selected_instrument']['uic'] == 101
        # Prices should be from the infoprice response, not the failed subscription
        assert result['selected_instrument']['latest_ask'] == 10.1
        assert result['selected_instrument']['latest_bid'] == 10.0
        # Subscription details should be None since it failed
        assert result['selected_instrument']['subscription_context_id'] is None

    @patch('time.sleep', return_value=None)
    def test_find_turbos_no_infoprice_data_after_retries(self, mock_sleep, instrument_service, mock_api_client):
        initial_instrument = TestDataFactory.create_saxo_instrument()
        # All calls to get infoprices fail. The outer while loop runs 5 times.
        mock_api_client.request.side_effect = [
            {"Data": [initial_instrument]},
            None, None, None, None, None
        ]
        with pytest.raises(NoMarketAvailableException, match="Failed to obtain valid InfoPrice data"):
            instrument_service.find_turbos("e1", "u1", "long")
        # Instruments (1) + InfoPrices (5 retries) = 6 calls
        assert mock_api_client.request.call_count == 6
        assert mock_sleep.call_count == 4 # Sleeps between retries

    @pytest.mark.parametrize("bid_price, should_be_found", [
        (4.0, True),   # Exactly at min price
        (15.0, True),  # Exactly at max price
        (9.5, True),   # Comfortably in the middle
        (3.99, False), # Just below min price
        (15.01, False) # Just above max price
    ])
    def test_should_validate_turbo_price_range_boundaries(self, bid_price, should_be_found, instrument_service, mock_api_client, mock_config_manager):
        """Test behavior at exact min/max price range boundaries."""
        # Arrange
        # The config fixture sets the range to min: 4, max: 15
        initial_instrument = TestDataFactory.create_saxo_instrument(identifier=1)
        infoprice = TestDataFactory.create_saxo_infoprice(uic=101, identifier=1, bid=bid_price, ask=bid_price + 0.1)
        price_snapshot = TestDataFactory.create_price_subscription_snapshot(uic=101)

        mock_api_client.request.side_effect = [
            {"Data": [initial_instrument]},
            {"Data": [infoprice]},
            {"Snapshot": price_snapshot}
        ]

        # Act & Assert
        if should_be_found:
            result = instrument_service.find_turbos("exchange1", "underlying1", "long")
            assert result is not None
            assert result['selected_instrument']['uic'] == 101
        else:
            with pytest.raises(NoTurbosAvailableException, match="No turbos found in price range"):
                instrument_service.find_turbos("exchange1", "underlying1", "long")

    def test_should_filter_out_closed_market_instruments(self, instrument_service, mock_api_client):
        """Test that instruments with MarketState 'Closed' are filtered out."""
        # Arrange
        initial_instrument = TestDataFactory.create_saxo_instrument(identifier=1)
        # This instrument is tradable, but the market is closed.
        infoprice_closed = TestDataFactory.create_saxo_infoprice(uic=101, identifier=1, market_state="Closed")

        mock_api_client.request.side_effect = [
            {"Data": [initial_instrument]},
            {"Data": [infoprice_closed]},
        ]

        # Act & Assert
        with pytest.raises(NoMarketAvailableException, match="No markets available"):
            instrument_service.find_turbos("exchange1", "underlying1", "long")

    @patch('time.sleep', return_value=None)
    def test_should_proceed_if_exactly_50_percent_bids_missing(self, mock_sleep, instrument_service, mock_api_client):
        """Test bid retry logic proceeds without retrying if exactly 50% of bids are missing."""
        # Arrange
        initial_instruments = [TestDataFactory.create_saxo_instrument(identifier=i) for i in range(4)]

        # Exactly 50% (2/4) are missing bids
        infoprice1 = TestDataFactory.create_saxo_infoprice(uic=1, identifier=1)
        infoprice2 = TestDataFactory.create_saxo_infoprice(uic=2, identifier=2)
        infoprice_no_bid_1 = TestDataFactory.create_saxo_infoprice(uic=3, identifier=3)
        del infoprice_no_bid_1['Quote']['Bid']
        infoprice_no_bid_2 = TestDataFactory.create_saxo_infoprice(uic=4, identifier=4)
        del infoprice_no_bid_2['Quote']['Bid']
        infoprices = [infoprice1, infoprice2, infoprice_no_bid_1, infoprice_no_bid_2]

        price_snapshot = TestDataFactory.create_price_subscription_snapshot(uic=1)

        mock_api_client.request.side_effect = [
            {"Data": initial_instruments},
            {"Data": infoprices},
            {"Snapshot": price_snapshot}
        ]

        # Act
        result = instrument_service.find_turbos("exchange1", "underlying1", "long")

        # Assert
        # The process should continue as the condition is >50%
        assert result is not None
        assert result['selected_instrument']['uic'] == 1
        # No retry should be triggered
        mock_sleep.assert_not_called()
        assert mock_api_client.request.call_count == 3
