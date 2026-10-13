import asyncio
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

from src.price_recorder.instruments import (
    SUBSCRIPTION_CHUNK,
    TURBO,
    UNDERLYING,
    Instrument,
    RecordingSet,
    discover_turbos,
    resolve_underlyings,
    turbo_from_candidate,
)
from src.price_recorder.settings import ExtraInstrument
from src.trade.exceptions import ApiRequestException, NoMarketAvailableException

NOW = datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc)
INDICES = {"us100": "1909050"}


def _candidate(uic, description, asset_type="WarrantOpenEndKnockOut"):
    return {
        "Uic": uic,
        "Identifier": uic,
        "AssetType": asset_type,
        "DisplayAndFormat": {"Symbol": f"SYM{uic}", "Description": description, "Currency": "EUR"},
        "Quote": {"Bid": 8.4, "Ask": 8.5},
    }


def _search_row(uic, description):
    name, kind, buysell, price, issuer = description.rsplit(" ", 4)
    return {
        "Identifier": uic,
        "Description": description,
        "appParsedData": {"name": name, "kind": kind, "buysell": buysell, "price": price, "from": issuer},
    }


# The shape of Saxo's descriptions, as the trader's parser reads them: name, kind, direction, knock-out level, issuer.
LONG_A = "NASDAQ 100 Turbo Long 24150 SG"
LONG_B = "NASDAQ 100 Turbo Long 24300.5 BNP"
SHORT_A = "NASDAQ 100 Turbo Short 25900 SG"


class InstrumentService:
    def __init__(self, by_direction):
        self.by_direction = by_direction
        self.calls = []

    async def build_watchlist_candidates(self, exchange_id, underlying_uics, keywords):
        self.calls.append((exchange_id, underlying_uics, keywords))
        result = self.by_direction[keywords]
        if isinstance(result, Exception):
            raise result
        return {
            "candidate_snapshots": [_candidate(uic, description) for uic, description in result],
            "selected_source_lookup": {uic: _search_row(uic, description) for uic, description in result},
        }


# ── a candidate of the search as an instrument ──


def test_a_candidate_carries_what_a_backtest_needs_to_know_about_the_turbo():
    turbo = turbo_from_candidate(
        _candidate(101, LONG_B), {101: _search_row(101, LONG_B)},
        indice="us100", underlying_uic="1909050", direction="long",
    )

    assert turbo == Instrument(
        uic=101, asset_type="WarrantOpenEndKnockOut", role=TURBO, symbol="SYM101", description=LONG_B,
        currency="EUR", indice="us100", underlying_uic=1909050, direction="long", barrier=24300.5, issuer="BNP",
    )
    assert turbo.key == (101, "WarrantOpenEndKnockOut")


def test_a_candidate_missing_from_the_search_rows_is_read_from_its_own_description():
    turbo = turbo_from_candidate(_candidate(101, LONG_A), {}, indice="us100", underlying_uic="1909050", direction="long")

    assert (turbo.barrier, turbo.issuer) == (24150.0, "SG")


def test_a_row_without_a_uic_is_left_out():
    assert turbo_from_candidate({"AssetType": "WarrantKnockOut"}, {}, indice="us100", underlying_uic="1", direction="long") is None


# ── looking for turbos ──


def test_the_search_is_the_traders_own_for_each_indice_and_direction():
    service = InstrumentService({"long": [(101, LONG_A), (102, LONG_B)], "short": [(201, SHORT_A)]})

    found = asyncio.run(discover_turbos(service, "CATS_SAXO", INDICES))

    assert service.calls == [("CATS_SAXO", "1909050", "long"), ("CATS_SAXO", "1909050", "short")]
    assert [(turbo.uic, turbo.direction, turbo.barrier) for turbo in found] == [
        (101, "long", 24150.0), (102, "long", 24300.5), (201, "short", 25900.0),
    ]


@pytest.mark.parametrize("failure", [NoMarketAvailableException("closed"), ApiRequestException("Saxo is down")])
def test_a_search_that_fails_only_adds_nothing_for_its_direction(failure):
    service = InstrumentService({"long": failure, "short": [(201, SHORT_A)]})

    found = asyncio.run(discover_turbos(service, "CATS_SAXO", INDICES))

    assert [turbo.uic for turbo in found] == [201]


# ── the underlying ──


class ApiClient:
    def __init__(self, details=None, error=None):
        self.details = details or []
        self.error = error
        self.requests = []

    async def request(self, endpoint):
        self.requests.append(endpoint)
        if self.error:
            raise self.error
        return {"Data": self.details}


def test_the_underlying_is_recorded_under_the_asset_type_saxo_lists_for_it():
    api = ApiClient([
        {"Uic": 1909050, "AssetType": "CfdOnIndex", "Symbol": "USNAS100.I", "Description": "US Tech 100 NAS"},
        {"Uic": 1909050, "AssetType": "StockIndex", "Symbol": "NDX.I", "Description": "NASDAQ 100", "CurrencyCode": "USD"},
    ])

    (underlying,) = asyncio.run(resolve_underlyings(api, "account", INDICES, ()))

    # The index itself rather than the contract on it, when both exist.
    assert underlying == Instrument(
        uic=1909050, asset_type="StockIndex", role=UNDERLYING, symbol="NDX.I", description="NASDAQ 100",
        currency="USD", indice="us100", underlying_uic=1909050,
    )
    assert api.requests[0].params == {"Uics": "1909050", "AccountKey": "account"}


def test_an_instrument_named_in_the_configuration_replaces_the_lookup():
    api = ApiClient()

    (underlying,) = asyncio.run(
        resolve_underlyings(api, "account", INDICES, (ExtraInstrument("us100", 4913, "CfdOnIndex"),))
    )

    assert (underlying.uic, underlying.asset_type, underlying.role, underlying.indice) == (4913, "CfdOnIndex", UNDERLYING, "us100")
    assert underlying.underlying_uic == 1909050
    assert api.requests == []


def test_other_instruments_of_the_configuration_are_recorded_beside_the_indices():
    api = ApiClient([{"Uic": 1909050, "AssetType": "StockIndex"}])

    underlyings = asyncio.run(
        resolve_underlyings(api, "account", INDICES, (ExtraInstrument("vix", 777, "StockIndex"),))
    )

    assert sorted((item.indice, item.uic) for item in underlyings) == [("us100", 1909050), ("vix", 777)]


@pytest.mark.parametrize("api", [ApiClient(error=RuntimeError("timeout")), ApiClient(details=[])])
def test_an_underlying_that_cannot_be_looked_up_is_left_out_and_said(api, caplog):
    with caplog.at_level("WARNING"):
        underlyings = asyncio.run(resolve_underlyings(api, "account", INDICES, ()))

    assert underlyings == []
    assert "trade.config.price_recorder.instruments" in caplog.text


# ── what is recorded now ──


def _turbo(uic, direction="long", asset_type="WarrantOpenEndKnockOut"):
    return Instrument(uic=uic, asset_type=asset_type, role=TURBO, indice="us100", direction=direction)


def _underlying(uic=1909050):
    return Instrument(uic=uic, asset_type="StockIndex", role=UNDERLYING, indice="us100")


class Calendar:
    def __init__(self, day):
        self.day = day

    def __call__(self):
        return self.day


def _set(max_instruments=150, calendar=None):
    return RecordingSet(max_instruments, today=calendar or Calendar(date(2026, 10, 12)))


def test_a_turbo_that_left_the_candidates_stays_recorded_for_the_day():
    recording = _set()
    recording.set_underlyings([_underlying()])

    assert [turbo.uic for turbo in recording.add_turbos([_turbo(101), _turbo(102)], NOW)] == [101, 102]
    # The index moved: 101 is no longer a candidate, 103 is new.
    assert [turbo.uic for turbo in recording.add_turbos([_turbo(102), _turbo(103)], NOW)] == [103]

    assert recording.keys() == {
        (1909050, "StockIndex"),
        (101, "WarrantOpenEndKnockOut"), (102, "WarrantOpenEndKnockOut"), (103, "WarrantOpenEndKnockOut"),
    }
    assert len(recording) == 4 and (101, "WarrantOpenEndKnockOut") in recording


def test_the_turbos_of_the_day_before_are_let_go_and_the_underlying_stays():
    calendar = Calendar(date(2026, 10, 12))
    recording = _set(calendar=calendar)
    recording.set_underlyings([_underlying()])
    recording.add_turbos([_turbo(101)], NOW)

    assert recording.start_new_day_if_needed() is False
    calendar.day = date(2026, 10, 13)

    assert recording.start_new_day_if_needed() is True
    assert recording.keys() == {(1909050, "StockIndex")}
    assert recording.start_new_day_if_needed() is False


def test_over_the_limit_the_turbos_seen_longest_ago_go_first_and_never_the_underlying(caplog):
    recording = _set(max_instruments=3)
    recording.set_underlyings([_underlying()])
    recording.add_turbos([_turbo(101)], datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc))
    recording.add_turbos([_turbo(102)], datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc))

    with caplog.at_level("WARNING"):
        added = recording.add_turbos([_turbo(102), _turbo(103)], datetime(2026, 10, 12, 10, 0, tzinfo=timezone.utc))

    assert [turbo.uic for turbo in added] == [103]
    assert recording.keys() == {(1909050, "StockIndex"), (102, "WarrantOpenEndKnockOut"), (103, "WarrantOpenEndKnockOut")}
    assert "max_instruments" in caplog.text


def test_an_underlying_is_never_recorded_twice_as_a_turbo():
    recording = _set()
    recording.set_underlyings([_underlying()])

    recording.add_turbos([Instrument(uic=1909050, asset_type="StockIndex", role=TURBO)], NOW)

    assert [instrument.role for instrument in recording.instruments()] == [UNDERLYING]


def test_subscriptions_are_per_asset_type_quotes_only_and_stable():
    recording = _set()
    recording.set_underlyings([_underlying()])
    recording.add_turbos([_turbo(103), _turbo(101), _turbo(202, asset_type="MiniFuture")], NOW)

    definitions = recording.subscription_definitions()

    assert definitions == [
        {"reference_id": "pr-MiniFuture-0", "asset_type": "MiniFuture", "uics": [202], "field_groups": ["Quote"]},
        {"reference_id": "pr-StockIndex-0", "asset_type": "StockIndex", "uics": [1909050], "field_groups": ["Quote"]},
        {
            "reference_id": "pr-WarrantOpenEndKnockOut-0", "asset_type": "WarrantOpenEndKnockOut",
            "uics": [101, 103], "field_groups": ["Quote"],
        },
    ]
    # Unchanged set, same definitions: the stream client then leaves its subscriptions alone.
    assert recording.subscription_definitions() == definitions


def test_subscriptions_ask_for_more_than_the_quotes_only_when_told_to():
    recording = _set()
    recording.set_underlyings([_underlying()])
    recording.add_turbos([_turbo(101)], NOW)

    definitions = recording.subscription_definitions(("Quote", "PriceInfoDetails"))

    assert [definition["field_groups"] for definition in definitions] == [["Quote", "PriceInfoDetails"]] * 2
    # The same subscriptions otherwise: asking for more replaces them, it does not add to them.
    assert [definition["reference_id"] for definition in definitions] == [
        definition["reference_id"] for definition in recording.subscription_definitions()
    ]


def test_a_large_set_is_split_into_several_subscriptions():
    recording = _set(max_instruments=1000)
    recording.add_turbos([_turbo(uic) for uic in range(1, SUBSCRIPTION_CHUNK * 2 + 6)], NOW)

    definitions = recording.subscription_definitions()

    assert [len(definition["uics"]) for definition in definitions] == [SUBSCRIPTION_CHUNK, SUBSCRIPTION_CHUNK, 5]
    assert len({definition["reference_id"] for definition in definitions}) == 3
    assert all(len(definition["reference_id"]) <= 50 for definition in definitions)
