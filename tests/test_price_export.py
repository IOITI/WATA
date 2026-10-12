import asyncio
import csv
import gzip
import sys
import types
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock

import pytest

asyncpg_stub = types.ModuleType("asyncpg")
asyncpg_stub.Pool = type("Pool", (), {})
asyncpg_stub.Record = dict
asyncpg_stub.create_pool = AsyncMock()
sys.modules.setdefault("asyncpg", asyncpg_stub)

import src.price_recorder.export as export
import src.price_recorder.probe as probe

# ── export ──


def _bar(seconds, instrument_id=1, **changes):
    return {
        "ts": datetime(2026, 10, 12, 8, 0, seconds, tzinfo=timezone.utc), "instrument_id": instrument_id,
        "uic": 101, "asset_type": "WarrantOpenEndKnockOut", "symbol": "SG-NDX-L1",
        "description": "NASDAQ 100 Turbo Long 24150 SG", "role": "turbo", "indice": "us100", "direction": "long",
        "barrier": 24150.0, "issuer": "SG",
        "bid_open": 8.40, "bid_high": 8.52, "bid_low": 8.36, "bid_close": 8.45,
        "ask_open": 8.45, "ask_high": 8.57, "ask_low": 8.41, "ask_close": 8.50,
        "updates": 3, "delayed_minutes": 0, **changes,
    }


class History:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    async def bars_page(self, start, end, *, indice=None, after=None, limit=20000):
        self.calls.append((start, end, indice, after, limit))
        return self.pages.pop(0) if self.pages else []


START = datetime(2026, 10, 12, tzinfo=timezone.utc)
END = datetime(2026, 10, 13, tzinfo=timezone.utc)


def _read(path):
    with gzip.open(path, "rt", newline="", encoding="utf-8") as exported:
        return list(csv.reader(exported))


def test_a_period_is_written_as_a_compressed_csv_with_each_instrument_described(tmp_path):
    out = tmp_path / "sub" / "prices.csv.gz"
    history = History([[_bar(0), _bar(10, ask_open=None, ask_high=None, ask_low=None, ask_close=None, updates=0)]])

    count = asyncio.run(export.export_bars(history, START, END, str(out), indice="us100"))

    assert count == 2
    header, first, second = _read(out)
    assert header == list(export.COLUMNS)
    assert first == [
        "2026-10-12T08:00:00Z", "101", "WarrantOpenEndKnockOut", "SG-NDX-L1", "NASDAQ 100 Turbo Long 24150 SG",
        "turbo", "us100", "long", "24150.0", "SG",
        "8.4", "8.52", "8.36", "8.45", "8.45", "8.57", "8.41", "8.5", "3", "0",
    ]
    assert second[0] == "2026-10-12T08:00:10Z" and second[14:18] == ["", "", "", ""] and second[18] == "0"
    assert history.calls == [(START, END, "us100", None, export.PAGE_SIZE)]


def test_a_long_period_is_read_page_after_page(tmp_path, monkeypatch):
    monkeypatch.setattr(export, "PAGE_SIZE", 2)
    history = History([[_bar(0), _bar(10)], [_bar(20), _bar(30, instrument_id=4)], [_bar(40)]])

    count = asyncio.run(export.export_bars(history, START, END, str(tmp_path / "prices.csv.gz")))

    assert count == 5
    assert [call[3] for call in history.calls] == [
        None,
        (datetime(2026, 10, 12, 8, 0, 10, tzinfo=timezone.utc), 1),
        (datetime(2026, 10, 12, 8, 0, 30, tzinfo=timezone.utc), 4),
    ]
    assert len(_read(tmp_path / "prices.csv.gz")) == 6


def test_nothing_recorded_gives_a_file_with_its_header_only(tmp_path):
    out = tmp_path / "prices.csv.gz"

    assert asyncio.run(export.export_bars(History([]), START, END, str(out))) == 0
    assert _read(out) == [list(export.COLUMNS)]


def test_days_are_those_of_the_trading_timezone_both_ends_included():
    start, end = export.day_bounds(date(2026, 10, 12), date(2026, 10, 13), "Europe/Paris")

    assert start.astimezone(timezone.utc) == datetime(2026, 10, 11, 22, 0, tzinfo=timezone.utc)
    assert end.astimezone(timezone.utc) == datetime(2026, 10, 13, 22, 0, tzinfo=timezone.utc)

    # The day the clocks go back has 25 hours.
    start, end = export.day_bounds(date(2026, 10, 25), date(2026, 10, 25), "Europe/Paris")
    assert (end - start).total_seconds() == 25 * 3600


def test_arguments():
    arguments = export.parse_arguments(["--from", "2026-10-12"])
    assert (arguments.first_day, arguments.last_day, arguments.indice) == (date(2026, 10, 12), date(2026, 10, 12), None)

    arguments = export.parse_arguments(["--from", "2026-10-12", "--to", "2026-10-16", "--indice", "us100", "--out", "x.csv.gz"])
    assert (arguments.last_day, arguments.indice, arguments.out) == (date(2026, 10, 16), "us100", "x.csv.gz")

    with pytest.raises(SystemExit):
        export.parse_arguments(["--from", "2026-10-12", "--to", "2026-10-11"])
    with pytest.raises(SystemExit):
        export.parse_arguments([])


# ── probe ──


class Api:
    def __init__(self, details=None, search=None, quotes=None):
        self.details = details or []
        self.search = search or []
        self.quotes = quotes or {}
        self.requests = []

    async def request(self, endpoint):
        self.requests.append(endpoint)
        name = type(endpoint).__name__
        if name == "InstrumentsDetails":
            return {"Data": self.details}
        if name == "Instruments":
            return {"Data": self.search}
        quote = self.quotes.get((int(endpoint.params["Uics"]), endpoint.params["AssetType"]))
        if isinstance(quote, Exception):
            raise quote
        return {"Data": [] if quote is None else [{"Uic": int(endpoint.params["Uics"]), "Quote": quote}]}


INDEX = {"Uic": 1909050, "AssetType": "StockIndex", "Symbol": "NDX.I", "Description": "NASDAQ 100"}
CFD = {"Uic": 1909050, "AssetType": "CfdOnIndex", "Symbol": "USNAS100.I", "Description": "US Tech 100 NAS"}


def test_a_uic_is_tried_under_each_of_its_asset_types():
    api = Api(details=[INDEX, CFD], quotes={
        (1909050, "StockIndex"): {"PriceTypeBid": "NoAccess", "PriceTypeAsk": "NoAccess"},
        (1909050, "CfdOnIndex"): {"Bid": 24150.1, "Ask": 24151.3, "PriceTypeBid": "Tradable", "PriceTypeAsk": "Tradable",
                                  "MarketState": "Open"},
    })

    index, cfd = asyncio.run(probe.probe(api, "account", uic=1909050))

    assert (index["asset_type"], index["recordable"]) == ("StockIndex", False)
    assert "no access" in index["why"]
    assert (cfd["asset_type"], cfd["recordable"], cfd["why"]) == ("CfdOnIndex", True, "recordable, in real time")
    assert api.requests[0].params == {"Uics": "1909050", "AccountKey": "account"}
    assert api.requests[1].params["FieldGroups"] == "Quote"


def test_a_search_lists_what_saxo_finds_with_whether_each_is_priced():
    api = Api(
        search=[{"Identifier": 4913, "AssetType": "CfdOnIndex", "Symbol": "USNAS100.I", "Description": "US Tech 100 NAS"}],
        quotes={(4913, "CfdOnIndex"): {"Mid": 24150.5, "PriceTypeBid": "Indicative", "PriceTypeAsk": "Indicative",
                                       "DelayedByMinutes": 15}},
    )

    (found,) = asyncio.run(probe.probe(api, "account", keywords="US Tech 100"))

    assert found["uic"] == 4913 and found["recordable"] is True
    assert found["why"] == "recordable, but the price is 15 min late"
    assert api.requests[0].params["Keywords"] == "US Tech 100"
    assert api.requests[0].params["AssetTypes"] == "StockIndex,CfdOnIndex"


@pytest.mark.parametrize("quote, reason", [
    ({"Bid": 1.0, "Ask": 1.1, "MarketState": "Closed"}, "the market is closed right now"),
    (None, "Saxo returned no price row"),
    (RuntimeError("403"), "request refused: 403"),
])
def test_what_cannot_be_recorded_says_why(quote, reason):
    api = Api(details=[INDEX], quotes={(1909050, "StockIndex"): quote})

    (result,) = asyncio.run(probe.probe(api, "account", uic=1909050))

    assert result["recordable"] is False and reason in result["why"]


def test_the_report_ends_with_the_entry_to_put_in_the_configuration():
    results = [
        {"uic": 1909050, "asset_type": "StockIndex", "symbol": "NDX.I", "description": "NASDAQ 100",
         "recordable": False, "why": "no price for this account (no market or no access to its data)"},
        {"uic": 4913, "asset_type": "CfdOnIndex", "symbol": "USNAS100.I", "description": "US Tech 100 NAS",
         "bid": 24150.1, "ask": 24151.3, "mid": None, "price_type_bid": "Tradable", "price_type_ask": "Tradable",
         "market_state": "Open", "delayed_minutes": 0, "recordable": True, "why": "recordable, in real time"},
    ]

    text = probe.report(results, "us100")

    assert "UIC 1909050  StockIndex" in text and "-> no price for this account" in text
    assert text.endswith('{"label": "us100", "uic": 4913, "asset_type": "CfdOnIndex"}')


def test_the_report_prefers_a_real_time_price_and_says_when_there_is_none():
    late = {"uic": 1, "asset_type": "StockIndex", "recordable": True, "delayed_minutes": 15, "why": "recordable, but the price is 15 min late"}
    live = {"uic": 2, "asset_type": "CfdOnIndex", "recordable": True, "delayed_minutes": 0, "why": "recordable, in real time"}

    assert '"uic": 2' in probe.report([late, live])
    assert '"uic": 1' in probe.report([late])
    assert probe.report([]) == "Saxo lists no such instrument."
    assert "trade.config.price_recorder.instruments" not in probe.report([{"uic": 1, "asset_type": "StockIndex", "recordable": False, "why": "x"}])
