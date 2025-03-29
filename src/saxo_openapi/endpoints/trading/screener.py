# -*- encoding: utf-8 -*-

"""Handle trading-prices endpoints."""

from .base import Trading
from ..decorators import dyndoc_insert, endpoint
from .responses.prices import responses


@endpoint("openapi/trade/v1/screener/subscriptions", "POST", 201)
class CreateScreenerSubscription(Trading):
    """Sets up an active screener subscription on an instrument and returns an
    initial snapshot of the most recent price.
    """

    @dyndoc_insert(responses)
    def __init__(self, data):
        """Instantiate a CreatePriceSubscription request.

        Parameters
        ----------
        data : dict (required)
            dict representing the data body, in this case an order spec.


        """
        super(CreateScreenerSubscription, self).__init__()
        self.data = data


