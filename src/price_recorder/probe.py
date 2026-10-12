"""
Ask Saxo whether it gives this account a price for an instrument, before
naming it in ``trade.config.price_recorder.instruments``.

    python -m src.price_recorder.probe --uic 1909050
    python -m src.price_recorder.probe --keywords "US Tech 100"

The underlying of the turbos is an index, and an index is not always quoted
to an account: this shows which instrument is, and how delayed its price is.
"""

import argparse
import asyncio
import json
import os
import sys

import src.saxo_openapi.endpoints.referencedata as rd
import src.saxo_openapi.endpoints.trading as tr

from src.price_recorder.bars import read_quote

DEFAULT_ASSET_TYPES = "StockIndex,CfdOnIndex"
SEARCH_LIMIT = 20


async def find_candidates(api_client, account_key: str, *, uic: int | None, keywords: str | None, asset_types: str) -> list[dict]:
    """The instruments to try: every asset type of a UIC, or the results of a search."""
    if uic is not None:
        response = await api_client.request(
            rd.instruments.InstrumentsDetails(params={"Uics": str(uic), "AccountKey": account_key})
        )
        return [
            {"uic": row["Uic"], "asset_type": row["AssetType"], "symbol": row.get("Symbol"), "description": row.get("Description")}
            for row in (response or {}).get("Data") or []
            if row.get("Uic") is not None and row.get("AssetType")
        ]
    response = await api_client.request(
        rd.instruments.Instruments(
            params={"Keywords": keywords, "AssetTypes": asset_types, "$top": SEARCH_LIMIT, "AccountKey": account_key}
        )
    )
    return [
        {"uic": row["Identifier"], "asset_type": row["AssetType"], "symbol": row.get("Symbol"), "description": row.get("Description")}
        for row in (response or {}).get("Data") or []
        if row.get("Identifier") is not None and row.get("AssetType")
    ]


async def quote_of(api_client, account_key: str, candidate: dict) -> dict:
    """What Saxo answers for one instrument, and whether the recorder could record it."""
    try:
        response = await api_client.request(
            tr.infoprices.InfoPrices(
                params={
                    "AccountKey": account_key,
                    "Uics": str(candidate["uic"]),
                    "AssetType": candidate["asset_type"],
                    "FieldGroups": "Quote",
                }
            )
        )
    except Exception as e:
        return {**candidate, "recordable": False, "why": f"request refused: {e}"}

    rows = (response or {}).get("Data") or []
    if not rows:
        return {**candidate, "recordable": False, "why": "Saxo returned no price row"}
    quote = rows[0].get("Quote") or {}
    read = read_quote(rows[0])
    result = {
        **candidate,
        "bid": quote.get("Bid"),
        "ask": quote.get("Ask"),
        "mid": quote.get("Mid"),
        "price_type_bid": quote.get("PriceTypeBid"),
        "price_type_ask": quote.get("PriceTypeAsk"),
        "market_state": quote.get("MarketState"),
        "delayed_minutes": quote.get("DelayedByMinutes"),
        "recordable": read is not None,
    }
    if read is None:
        result["why"] = (
            "the market is closed right now: try again in trading hours"
            if quote.get("MarketState") == "Closed"
            else "no price for this account (no market or no access to its data)"
        )
    elif read.delayed_minutes:
        result["why"] = f"recordable, but the price is {read.delayed_minutes} min late"
    else:
        result["why"] = "recordable, in real time"
    return result


async def probe(api_client, account_key: str, *, uic: int | None = None, keywords: str | None = None,
                asset_types: str = DEFAULT_ASSET_TYPES) -> list[dict]:
    candidates = await find_candidates(api_client, account_key, uic=uic, keywords=keywords, asset_types=asset_types)
    return [await quote_of(api_client, account_key, candidate) for candidate in candidates]


def report(results: list[dict], label: str = "us100") -> str:
    if not results:
        return "Saxo lists no such instrument."
    lines = []
    for result in results:
        lines.append(
            f"UIC {result['uic']}  {result['asset_type']}  {result.get('symbol') or ''}  {result.get('description') or ''}"
        )
        if "price_type_bid" in result:
            lines.append(
                f"    bid {result['bid']}  ask {result['ask']}  mid {result['mid']}  "
                f"({result['price_type_bid']}/{result['price_type_ask']}, market {result['market_state']})"
            )
        lines.append(f"    -> {result['why']}")
    best = next((result for result in results if result["recordable"] and not result.get("delayed_minutes")), None) \
        or next((result for result in results if result["recordable"]), None)
    if best is not None:
        entry = {"label": label, "uic": best["uic"], "asset_type": best["asset_type"]}
        lines += ["", "To record it, in trade.config.price_recorder.instruments:", f"    {json.dumps(entry)}"]
    return "\n".join(lines)


def parse_arguments(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check whether Saxo prices an instrument for this account.")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--uic", type=int, help="a Saxo instrument number, e.g. the underlying of an indice")
    target.add_argument("--keywords", help='a search, e.g. "US Tech 100"')
    parser.add_argument("--asset-types", default=DEFAULT_ASSET_TYPES, help=f"for a search (default: {DEFAULT_ASSET_TYPES})")
    parser.add_argument("--label", default="us100", help="the indice name to put in the suggested configuration entry")
    return parser.parse_args(argv)


async def run(arguments: argparse.Namespace) -> int:
    from src.configuration import ConfigurationManager
    from src.saxo_authen import SaxoAuth
    from src.trade.async_services import AsyncSaxoApiClient
    from src.trader import get_account_info_async

    config_path = os.getenv("WATA_CONFIG_PATH")
    if not config_path:
        print("WATA_CONFIG_PATH not set", file=sys.stderr)
        return 10
    config_manager = ConfigurationManager(config_path)
    api_client = AsyncSaxoApiClient(config_manager, SaxoAuth(config_manager))
    try:
        await api_client.ensure_ready()
        account_key = (await get_account_info_async(api_client)).AccountKey
        results = await probe(
            api_client, account_key, uic=arguments.uic, keywords=arguments.keywords, asset_types=arguments.asset_types,
        )
    finally:
        await api_client.close()
    print(report(results, arguments.label))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run(parse_arguments())))
