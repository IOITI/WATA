"""
The turbo the trader would have bought at a moment, among those the price
recorder kept, chosen by the live helpers themselves
(src/trade/async_services.py): the candidates by knock-out level, the first
twenty, those with a bid, those whose bid is in the price range, the lowest
bid of them.

Saxo's search is not recorded: the candidates of a moment are the recorded
turbos of the direction that were alive then (recorded before and after it).
The recorder kept the twenty candidates of every refresh, so the first twenty
of them by level are what the search gave, give or take a turbo issued in the
last minutes.
"""

from dataclasses import dataclass, field

from src.backtest.data import DayData, TurboSeries
from src.trade.async_services import (
    INFO_PRICE_CANDIDATE_LIMIT,
    filter_items_in_price_range,
    filter_items_with_valid_bid,
    filter_items_within_spread,
    filter_tradable_market_items,
    no_turbo_within_spread,
    select_best_price_candidate,
    select_candidate_instruments,
)

# How an entry ends up without a turbo.
NO_RECORDED_TURBO = "no_recorded_turbo"
RECORDING_GAP = "recording_gap"
NO_MARKET = "no_market"
NO_TURBO_IN_RANGE = "no_turbo_in_range"
NO_TURBO_WITHIN_SPREAD = "no_turbo_within_spread"
NO_ASK = "no_ask"


@dataclass(frozen=True)
class Selected:
    series: TurboSeries
    bid: float
    ask: float
    # What the trader looked at: each of the first twenty, with its quote and whether it was taken.
    candidates: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class NotSelected:
    # "unpriced": nothing recorded to trade on; "failed": what the trader itself would have failed on.
    fate: str
    rule: str
    reason: str
    candidates: list[dict] = field(default_factory=list)


def _row(series: TurboSeries, boundary: int) -> dict:
    """A recorded turbo as a row of Saxo's search and its info price, as the helpers read them."""
    quote = series.quote_at(boundary)
    row = {
        "Identifier": series.ref.instrument_id,
        "Uic": series.ref.uic,
        "AssetType": series.ref.asset_type,
        "appParsedData": {"price": series.ref.barrier},
        "_series": series,
    }
    if quote is not None:
        bid, ask = quote
        row["Quote"] = {"Bid": bid, "Ask": ask}
    return row


def _described(row: dict, low: float, high: float, chosen: dict | None) -> dict:
    series: TurboSeries = row["_series"]
    quote = row.get("Quote") or {}
    bid = quote.get("Bid")
    return {
        "instrument_id": series.ref.instrument_id,
        "symbol": series.ref.symbol,
        "issuer": series.ref.issuer,
        "barrier": series.ref.barrier,
        "bid": bid,
        "ask": quote.get("Ask"),
        "in_range": bid is not None and low <= bid <= high,
        "chosen": row is chosen,
    }


def select_turbo(day: DayData, direction: str, boundary: int, low: float, high: float,
                 max_spread_percent: float | None = None) -> Selected | NotSelected:
    """The turbo bought at ``boundary`` (a ten-second boundary), or why there was none."""
    recorded = day.turbos.get(direction) or []
    if not recorded:
        return NotSelected("unpriced", NO_RECORDED_TURBO, f"No {direction} turbo was recorded that day")
    if not day.is_covered(boundary):
        return NotSelected("unpriced", RECORDING_GAP, "The price recorder was not listening at that moment")
    alive = [_row(series, boundary) for series in recorded if series.alive_at(boundary)]
    if not alive:
        return NotSelected("unpriced", NO_RECORDED_TURBO, f"No {direction} turbo was recorded at that moment")

    top = select_candidate_instruments(alive, direction, INFO_PRICE_CANDIDATE_LIMIT)
    valid = filter_items_with_valid_bid(top)
    tradable = filter_tradable_market_items(valid)
    in_range = filter_items_in_price_range(tradable, low, high)
    within_spread = filter_items_within_spread(in_range, max_spread_percent)
    chosen = select_best_price_candidate(within_spread) if within_spread else None
    candidates = [_described(row, low, high, chosen) for row in top]
    if not valid:
        return NotSelected("failed", NO_MARKET, "NoMarketAvailableException: No instruments with Bid data after filtering.", candidates)
    if not tradable:
        return NotSelected("failed", NO_MARKET, f"NoMarketAvailableException: No markets available for {direction}.", candidates)
    if not in_range:
        return NotSelected("failed", NO_TURBO_IN_RANGE, f"NoTurbosAvailableException: No turbos in price range {low}-{high}.", candidates)
    if chosen is None:
        return NotSelected("failed", NO_TURBO_WITHIN_SPREAD,
                           f"NoTurbosAvailableException: {no_turbo_within_spread(in_range, max_spread_percent, low, high)}",
                           candidates)
    ask = chosen["Quote"].get("Ask")
    if not ask or ask <= 0:
        return NotSelected("failed", NO_ASK, f"Invalid ask price: {ask}", candidates)
    return Selected(series=chosen["_series"], bid=chosen["Quote"]["Bid"], ask=ask, candidates=candidates)
