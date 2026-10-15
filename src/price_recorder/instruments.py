"""
Which instruments the recorder follows.

The turbos are the candidates of the trader's own selection, found with the
same search (``AsyncInstrumentService.build_watchlist_candidates``). The list
of candidates shifts as the index moves; a turbo that left it may be the one
that was bought an hour ago, so everything seen in a day stays recorded until
the day ends. The underlying of each indice is recorded beside them.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable

import src.saxo_openapi.endpoints.referencedata as rd
from src.trade.async_services import parse_saxo_turbo_description
from src.trade.exceptions import ApiRequestException, NoMarketAvailableException, NoTurbosAvailableException

logger = logging.getLogger(__name__)

TURBO = "turbo"
UNDERLYING = "underlying"
DIRECTIONS = ("long", "short")
# Instruments per streaming subscription. Saxo's limit is not known from here
# (to be confirmed in simulation); the watchlist manager runs with 20.
SUBSCRIPTION_CHUNK = 100
# What a subscription asks Saxo for: the quotes, which are what is recorded.
QUOTE_FIELD_GROUPS = ("Quote",)
# Preferred when an underlying UIC exists under several asset types.
UNDERLYING_ASSET_TYPES = ("StockIndex", "CfdOnIndex")

InstrumentKey = tuple[int, str]


@dataclass(frozen=True)
class Instrument:
    uic: int
    asset_type: str
    role: str
    symbol: str | None = None
    description: str | None = None
    currency: str | None = None
    indice: str | None = None
    underlying_uic: int | None = None
    direction: str | None = None
    # Knock-out level of a turbo, read from its description.
    barrier: float | None = None
    issuer: str | None = None

    @property
    def key(self) -> InstrumentKey:
        return self.uic, self.asset_type


def _as_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def turbo_from_candidate(row: dict, source_lookup: dict, *, indice: str, underlying_uic, direction: str) -> Instrument | None:
    """A candidate row of the turbo search as an instrument to record (None when it has no UIC)."""
    uic = _as_int(row.get("Uic"))
    asset_type = row.get("AssetType")
    if uic is None or not asset_type:
        return None
    display = row.get("DisplayAndFormat") or {}
    source = source_lookup.get(row.get("Identifier")) or source_lookup.get(uic) or {}
    description = display.get("Description") or source.get("Description")
    parsed = source.get("appParsedData") or (parse_saxo_turbo_description(description) if description else None) or {}
    return Instrument(
        uic=uic,
        asset_type=asset_type,
        role=TURBO,
        symbol=display.get("Symbol") or source.get("Symbol"),
        description=description,
        currency=display.get("Currency") or source.get("CurrencyCode"),
        indice=indice,
        underlying_uic=_as_int(underlying_uic),
        direction=direction,
        barrier=_as_float(parsed.get("price")),
        issuer=parsed.get("from"),
    )


async def discover_turbos(instrument_service, exchange_id: str, allowed_indices: dict[str, str]) -> list[Instrument]:
    """
    The candidates of every indice and direction, as the trader's selection sees
    them now. A search that fails (market closed, Saxo unavailable) only means
    that this round adds nothing for it.
    """
    found: list[Instrument] = []
    for indice, underlying_uic in allowed_indices.items():
        for direction in DIRECTIONS:
            try:
                candidates = await instrument_service.build_watchlist_candidates(
                    exchange_id=exchange_id,
                    underlying_uics=underlying_uic,
                    keywords=direction,
                )
            except (NoMarketAvailableException, NoTurbosAvailableException) as e:
                logger.info("No turbo to record for %s %s right now: %s", indice, direction, e)
                continue
            except ApiRequestException as e:
                logger.warning("Turbo search failed for %s %s: %s", indice, direction, e)
                continue
            lookup = candidates.get("selected_source_lookup") or {}
            for row in candidates.get("candidate_snapshots") or []:
                instrument = turbo_from_candidate(
                    row, lookup, indice=indice, underlying_uic=underlying_uic, direction=direction,
                )
                if instrument is not None:
                    found.append(instrument)
    return found


async def resolve_underlyings(api_client, account_key: str, allowed_indices: dict[str, str], extra_instruments) -> list[Instrument]:
    """
    The instrument recorded for each indice: the one named in the configuration
    under the indice's name, or else the underlying UIC itself with the asset
    type Saxo lists for it. Other instruments of the configuration are recorded
    as well. Whether Saxo actually quotes them shows once the stream is open.
    """
    by_label = {extra.label: extra for extra in extra_instruments}
    underlyings = [
        Instrument(
            uic=extra.uic,
            asset_type=extra.asset_type,
            role=UNDERLYING,
            indice=extra.label,
            underlying_uic=_as_int(allowed_indices.get(extra.label)),
            description=f"{extra.label} (from the configuration)",
        )
        for extra in extra_instruments
    ]

    for indice, underlying_uic in allowed_indices.items():
        if indice in by_label:
            continue
        uic = _as_int(underlying_uic)
        if uic is None:
            logger.warning("The underlying of %s (%r) is not a UIC: its index is not recorded.", indice, underlying_uic)
            continue
        try:
            response = await api_client.request(
                rd.instruments.InstrumentsDetails(params={"Uics": str(uic), "AccountKey": account_key})
            )
        except Exception as e:
            logger.warning(
                "Could not look up the underlying of %s (UIC %s): %s. Its index is not recorded; "
                "name the instrument to record in trade.config.price_recorder.instruments.",
                indice, uic, e,
            )
            continue
        details = [row for row in (response or {}).get("Data") or [] if row.get("AssetType")]
        if not details:
            logger.warning(
                "Saxo lists no instrument for the underlying of %s (UIC %s). Its index is not recorded; "
                "find one with `python -m src.price_recorder.probe --keywords ...` and name it in "
                "trade.config.price_recorder.instruments.",
                indice, uic,
            )
            continue
        details.sort(
            key=lambda row: UNDERLYING_ASSET_TYPES.index(row["AssetType"])
            if row["AssetType"] in UNDERLYING_ASSET_TYPES
            else len(UNDERLYING_ASSET_TYPES)
        )
        chosen = details[0]
        underlyings.append(
            Instrument(
                uic=uic,
                asset_type=chosen["AssetType"],
                role=UNDERLYING,
                symbol=chosen.get("Symbol"),
                description=chosen.get("Description"),
                currency=chosen.get("CurrencyCode"),
                indice=indice,
                underlying_uic=uic,
            )
        )
    return underlyings


class RecordingSet:
    """The instruments recorded now: the underlyings, always, and the turbos seen today."""

    def __init__(self, max_instruments: int, today: Callable[[], date]):
        self._max_instruments = max_instruments
        self._today = today
        self._day: date = today()
        self._fixed: dict[InstrumentKey, Instrument] = {}
        self._turbos: dict[InstrumentKey, Instrument] = {}
        self._last_seen: dict[InstrumentKey, datetime] = {}

    def set_underlyings(self, instruments: list[Instrument]) -> None:
        self._fixed = {instrument.key: instrument for instrument in instruments}
        for key in self._fixed:
            self._turbos.pop(key, None)

    def add_turbos(self, instruments: list[Instrument], seen_at: datetime) -> list[Instrument]:
        """Add what a search found; returns the ones that were not recorded yet."""
        new = []
        for instrument in instruments:
            key = instrument.key
            if key in self._fixed:
                continue
            if key not in self._turbos:
                new.append(instrument)
            self._turbos[key] = instrument
            self._last_seen[key] = seen_at
        self._enforce_limit()
        return [instrument for instrument in new if instrument.key in self._turbos]

    def start_new_day_if_needed(self) -> bool:
        """At the change of day the turbos seen the day before are let go. True when that happened."""
        today = self._today()
        if today == self._day:
            return False
        self._day = today
        self._turbos = {}
        self._last_seen = {}
        return True

    def _enforce_limit(self) -> None:
        room = max(self._max_instruments - len(self._fixed), 0)
        excess = len(self._turbos) - room
        if excess <= 0:
            return
        # The ones seen longest ago go first: the least likely to be bought now or held.
        oldest_first = sorted(self._turbos, key=lambda key: self._last_seen[key])
        for key in oldest_first[:excess]:
            del self._turbos[key]
            del self._last_seen[key]
        logger.warning(
            "More than %d instruments to record: stopped recording the %d turbos seen longest ago "
            "(trade.config.price_recorder.max_instruments).",
            self._max_instruments, excess,
        )

    def __contains__(self, key: InstrumentKey) -> bool:
        return key in self._fixed or key in self._turbos

    def __len__(self) -> int:
        return len(self._fixed) + len(self._turbos)

    def keys(self) -> set[InstrumentKey]:
        return set(self._fixed) | set(self._turbos)

    def instruments(self) -> list[Instrument]:
        return [*self._fixed.values(), *self._turbos.values()]

    def get(self, key: InstrumentKey) -> Instrument | None:
        return self._fixed.get(key) or self._turbos.get(key)

    def subscription_definitions(self, field_groups: tuple[str, ...] = QUOTE_FIELD_GROUPS) -> list[dict]:
        """
        The streaming subscriptions that cover the set: per asset type, in chunks,
        quotes only unless more is asked for. UICs are sorted so that an unchanged
        set gives the same definitions, and the stream client leaves its
        subscriptions alone.
        """
        by_asset_type: dict[str, list[int]] = {}
        for uic, asset_type in self.keys():
            by_asset_type.setdefault(asset_type, []).append(uic)
        definitions = []
        for asset_type in sorted(by_asset_type):
            uics = sorted(by_asset_type[asset_type])
            for chunk, start in enumerate(range(0, len(uics), SUBSCRIPTION_CHUNK)):
                definitions.append({
                    "reference_id": f"pr-{asset_type}-{chunk}",
                    "asset_type": asset_type,
                    "uics": uics[start:start + SUBSCRIPTION_CHUNK],
                    "field_groups": list(field_groups),
                })
        return definitions
