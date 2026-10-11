import asyncio
import copy
import hashlib
import logging
from datetime import datetime

import pytz

from src.configuration import ConfigurationManager
from src.trade.async_services import (
    AsyncInstrumentService,
    NoMarketAvailableException,
    NoTurbosAvailableException,
    build_selected_turbo_from_candidate_snapshots,
)

logger = logging.getLogger(__name__)

DEFAULT_WATCHLIST_DIRECTIONS = ("long", "short")


def load_allowed_indices(config_manager: ConfigurationManager) -> dict[str, str]:
    for rule in config_manager.get_config_value("trade.rules", []):
        if rule.get("rule_type") == "allowed_indices":
            rule_config = rule.get("rule_config", {})
            return dict(rule_config.get("indice_ids", {}))
    return {}


def utc_now() -> datetime:
    return datetime.now(pytz.utc)


def utc_timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


class TurboWatchlistManager:
    """Background cache of best turbos per configured indice and direction."""

    def __init__(
        self,
        instrument_service: AsyncInstrumentService,
        config_manager: ConfigurationManager,
        exchange_id: str,
        allowed_indices: dict[str, str],
        stream_client=None,
    ):
        self.instrument_service = instrument_service
        self.exchange_id = exchange_id
        self.allowed_indices = dict(allowed_indices)
        self.stream_client = stream_client

        config = config_manager.get_config_value("trade.config.watchlist_manager", {})
        self.refresh_interval_seconds = config.get("refresh_interval_seconds", 300)
        self.stale_after_seconds = config.get("stale_after_seconds", self.refresh_interval_seconds + 30)
        self.startup_refresh = config.get("startup_refresh", True)

        directions = config.get("directions", list(DEFAULT_WATCHLIST_DIRECTIONS))
        self.directions = [direction.lower() for direction in directions if direction.lower() in DEFAULT_WATCHLIST_DIRECTIONS]
        if not self.directions:
            self.directions = list(DEFAULT_WATCHLIST_DIRECTIONS)

        self._cache: dict[tuple[str, str], dict] = {}
        self._last_refresh_started_at: datetime | None = None
        self._last_refresh_completed_at: datetime | None = None
        self._last_refresh_errors: dict[str, str] = {}
        self._refresh_task: asyncio.Task | None = None
        self._stream_task: asyncio.Task | None = None
        self._running = False
        self._lock = asyncio.Lock()

    def attach_stream_client(self, stream_client):
        self.stream_client = stream_client

    async def start(self):
        self._running = True
        if self.startup_refresh:
            await self.refresh_all()
        if self.stream_client is not None:
            self._stream_task = asyncio.create_task(self.stream_client.start())
        self._refresh_task = asyncio.create_task(self._refresh_loop())

    async def stop(self):
        self._running = False
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
        if self.stream_client is not None:
            await self.stream_client.stop()
        if self._stream_task and not self._stream_task.done():
            try:
                await self._stream_task
            except asyncio.CancelledError:
                pass

    async def refresh_all(self):
        targets = self._build_targets()
        self._last_refresh_started_at = utc_now()

        if not targets:
            self._last_refresh_completed_at = utc_now()
            self._last_refresh_errors = {}
            logger.warning("Watchlist refresh skipped because no allowed indices were configured.")
            return

        tasks = [
            self._refresh_target(indice, underlying_uic, direction)
            for indice, underlying_uic, direction in targets
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        errors = {}
        async with self._lock:
            for (indice, underlying_uic, direction), result in zip(targets, results):
                cache_label = f"{indice}:{direction}"
                if isinstance(result, Exception):
                    errors[cache_label] = f"{type(result).__name__}: {result}"
                    logger.warning(
                        "Watchlist refresh failed for indice=%s direction=%s underlying=%s: %s",
                        indice,
                        direction,
                        underlying_uic,
                        result,
                    )
                    continue

                self._cache[(indice, direction)] = result

            self._last_refresh_completed_at = utc_now()
            self._last_refresh_errors = errors

        await self._sync_stream_subscriptions()

        logger.info(
            "Watchlist refresh completed: %d/%d entries warm.",
            len(self._cache),
            len(targets),
        )

    def get_cached_turbo(
        self,
        *,
        indice: str | None = None,
        underlying_uic: str | None = None,
        direction: str,
    ) -> dict | None:
        cache_key = self._resolve_cache_key(indice=indice, underlying_uic=underlying_uic, direction=direction)
        if cache_key is None:
            return None

        entry = self._cache.get(cache_key)
        if not entry or entry.get("result") is None:
            return None

        payload = copy.deepcopy(entry["result"])
        cached_at = entry["price_updated_at"]
        payload["watchlist"] = {
            "source": "background-watchlist-manager",
            "indice": entry["indice"],
            "underlying_uic": entry["underlying_uic"],
            "direction": entry["direction"],
            "cached_at": utc_timestamp(cached_at),
            "price_last_updated_at": utc_timestamp(entry.get("price_updated_at")),
            "search_refreshed_at": utc_timestamp(entry.get("search_refreshed_at")),
            "stale_after_seconds": self.stale_after_seconds,
            "stale": (utc_now() - cached_at).total_seconds() > self.stale_after_seconds,
            "last_refresh_completed_at": utc_timestamp(self._last_refresh_completed_at),
        }
        return payload

    def get_health_snapshot(self) -> dict:
        target_count = len(self._build_targets())
        stale_entries = 0
        for entry in self._cache.values():
            if (utc_now() - entry["price_updated_at"]).total_seconds() > self.stale_after_seconds:
                stale_entries += 1

        return {
            "service": "watchlist_manager",
            "ready": target_count == 0 or bool(self._cache),
            "configured_targets": target_count,
            "warm_entries": len(self._cache),
            "stale_entries": stale_entries,
            "active_stream_subscriptions": self.stream_client.active_subscription_count if self.stream_client is not None else 0,
            "desired_stream_subscriptions": self.stream_client.desired_subscription_count if self.stream_client is not None else 0,
            "refresh_interval_seconds": self.refresh_interval_seconds,
            "stale_after_seconds": self.stale_after_seconds,
            "last_refresh_started_at": utc_timestamp(self._last_refresh_started_at),
            "last_refresh_completed_at": utc_timestamp(self._last_refresh_completed_at),
            "last_refresh_errors": copy.deepcopy(self._last_refresh_errors),
        }

    def _build_targets(self) -> list[tuple[str, str, str]]:
        targets = []
        for indice, underlying_uic in self.allowed_indices.items():
            for direction in self.directions:
                targets.append((indice, underlying_uic, direction))
        return targets

    async def _refresh_target(self, indice: str, underlying_uic: str, direction: str) -> dict:
        watchlist_candidates = await self.instrument_service.build_watchlist_candidates(
            exchange_id=self.exchange_id,
            underlying_uics=underlying_uic,
            keywords=direction,
        )
        refreshed_at = utc_now()
        grouped_snapshots = self._group_snapshots_by_subscription_reference(
            indice=indice,
            direction=direction,
            subscription_groups=watchlist_candidates["subscription_groups"],
            candidate_snapshots=watchlist_candidates["candidate_snapshots"],
        )

        return {
            "indice": indice,
            "underlying_uic": underlying_uic,
            "direction": direction,
            "search_refreshed_at": refreshed_at,
            "price_updated_at": refreshed_at,
            "result": watchlist_candidates["selected_result"],
            "selected_source_lookup": watchlist_candidates["selected_source_lookup"],
            "candidate_snapshots": watchlist_candidates["candidate_snapshots"],
            "subscription_groups": watchlist_candidates["subscription_groups"],
            "subscription_rows_by_ref": grouped_snapshots,
        }

    async def _refresh_loop(self):
        while self._running:
            try:
                await asyncio.sleep(self.refresh_interval_seconds)
                if not self._running:
                    break
                await self.refresh_all()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Watchlist refresh loop failed: %s", exc, exc_info=True)

    def _resolve_cache_key(
        self,
        *,
        indice: str | None,
        underlying_uic: str | None,
        direction: str,
    ) -> tuple[str, str] | None:
        direction = direction.lower()
        if direction not in DEFAULT_WATCHLIST_DIRECTIONS:
            return None

        if indice:
            return indice, direction

        if underlying_uic:
            for allowed_indice, allowed_uic in self.allowed_indices.items():
                if str(allowed_uic) == str(underlying_uic):
                    return allowed_indice, direction

        return None

    async def _sync_stream_subscriptions(self):
        if self.stream_client is None:
            return

        async with self._lock:
            definitions = []
            for entry in self._cache.values():
                for group in entry.get("subscription_groups", []):
                    if not group.get("uics"):
                        continue
                    definitions.append(
                        {
                            "reference_id": self._build_subscription_reference_id(
                                entry["indice"],
                                entry["direction"],
                                group["asset_type"],
                            ),
                            "indice": entry["indice"],
                            "underlying_uic": entry["underlying_uic"],
                            "direction": entry["direction"],
                            "asset_type": group["asset_type"],
                            "uics": group["uics"],
                            "field_groups": [
                                "Quote",
                                "DisplayAndFormat",
                                "InstrumentPriceDetails",
                                "Commissions",
                            ],
                        }
                    )

        await self.stream_client.set_subscriptions(definitions)

    async def handle_info_price_subscription_update(self, definition: dict, snapshot_rows: list[dict], context_id: str | None):
        cache_key = (definition["indice"], definition["direction"])
        async with self._lock:
            entry = self._cache.get(cache_key)
            if entry is None:
                return

            ref_id = definition["reference_id"]
            rows_by_uic = {
                row["Uic"]: copy.deepcopy(row)
                for row in snapshot_rows
                if row.get("Uic") is not None
            }
            entry.setdefault("subscription_rows_by_ref", {})[ref_id] = rows_by_uic

            all_snapshots = []
            uic_to_ref_id = {}
            for current_ref_id, current_rows in entry["subscription_rows_by_ref"].items():
                for uic, snapshot in current_rows.items():
                    all_snapshots.append(copy.deepcopy(snapshot))
                    uic_to_ref_id[uic] = current_ref_id

            entry["candidate_snapshots"] = all_snapshots
            entry["price_updated_at"] = utc_now()

            if not all_snapshots:
                entry["result"] = None
                return

            try:
                entry["result"] = build_selected_turbo_from_candidate_snapshots(
                    exchange_id=self.exchange_id,
                    underlying_uics=entry["underlying_uic"],
                    keywords=entry["direction"],
                    candidate_snapshots=all_snapshots,
                    selected_source_lookup=entry["selected_source_lookup"],
                    min_price=self.instrument_service.turbo_price_range["min"],
                    max_price=self.instrument_service.turbo_price_range["max"],
                    subscription_context_id=context_id,
                    uic_to_subscription_reference_id=uic_to_ref_id,
                )
            except (NoMarketAvailableException, NoTurbosAvailableException):
                entry["result"] = None

    def _group_snapshots_by_subscription_reference(
        self,
        *,
        indice: str,
        direction: str,
        subscription_groups: list[dict],
        candidate_snapshots: list[dict],
    ) -> dict[str, dict[int, dict]]:
        snapshots_by_uic = {
            snapshot["Uic"]: copy.deepcopy(snapshot)
            for snapshot in candidate_snapshots
            if snapshot.get("Uic") is not None
        }
        grouped_snapshots = {}
        for group in subscription_groups:
            ref_id = self._build_subscription_reference_id(indice, direction, group["asset_type"])
            grouped_snapshots[ref_id] = {
                int(uic): copy.deepcopy(snapshots_by_uic[int(uic)])
                for uic in group.get("uics", [])
                if int(uic) in snapshots_by_uic
            }
        return grouped_snapshots

    @staticmethod
    def _build_subscription_reference_id(indice: str, direction: str, asset_type: str) -> str:
        digest = hashlib.sha1(f"{indice}:{direction}:{asset_type}".encode("utf-8")).hexdigest()[:10]
        return f"wl-{indice}-{direction[:1]}-{digest}"