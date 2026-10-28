import asyncio
import json
import logging
import os
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone

import pytest
import pytz
from fastapi.testclient import TestClient

from src.admin.app import create_app
from src.admin.audit import AuditLog
from src.admin.auth import (
    SECURE_SESSION_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    CredentialStore,
    LoginThrottle,
    SessionStore,
    TrustedProxies,
    totp_code,
)
from src.admin.config_editor import MASK, ConfigEditor
from src.admin.context import AdminContext, AdminSettings
from src.admin.errors import DatabaseUnavailable, ServiceUnavailable
from src.configuration import ConfigurationManager

PASSWORD = "correct horse battery"
ORIGIN = "https://admin.test"
SAME_ORIGIN = {"Origin": ORIGIN}
# The address requests come from in production: the reverse proxy, the only
# one whose X-Forwarded-For header is believed.
PROXY = "172.18.0.2"
OTHER_CONTAINER = "172.18.0.9"

POSITION = {
    "position_id": "5001",
    "action": "long",
    "position_status": "Open",
    "position_amount": 100,
    "position_open_price": 8.5,
    "instrument_name": "TURBO LONG NASDAQ",
    "order_id": "7001",
    "related_order_id": [],
}

AUTHENTICATED_ROUTES = [
    ("GET", "/api/session"),
    ("DELETE", "/api/session"),
    ("GET", "/api/overview"),
    ("GET", "/api/positions"),
    ("GET", "/api/positions/5001"),
    ("GET", "/api/positions/5001/chart"),
    ("GET", "/api/orders"),
    ("GET", "/api/performance"),
    ("GET", "/api/exits"),
    ("GET", "/api/report"),
    ("GET", "/api/journal"),
    ("GET", "/api/signals"),
    ("GET", "/api/watchlist"),
    ("GET", "/api/execution-timing"),
    ("GET", "/api/audit"),
    ("GET", "/api/events"),
    ("GET", "/api/system/health"),
    ("GET", "/api/system/saxo"),
    ("GET", "/api/system/services"),
    ("POST", "/api/system/services/restart"),
    ("POST", "/api/system/services/wata-api/restart"),
    ("GET", "/api/credentials"),
    ("PUT", "/api/credentials/config.json.bak-20260101-000000"),
    ("DELETE", "/api/credentials/config.json.bak-20260101-000000"),
    ("POST", "/api/credentials/config.json.bak-20260101-000000/rotate"),
    ("POST", "/api/credentials/webhook.token/show"),
    ("POST", "/api/credentials/telegram.bot/test"),
    ("POST", "/api/credentials/anthropic.api/test"),
    ("POST", "/api/mcp/tokens"),
    ("POST", "/api/system/saxo/authorize"),
    ("GET", "/api/logs"),
    ("GET", "/api/logs/wata-api"),
    ("POST", "/api/actions/signal"),
    ("POST", "/api/actions/pause-entries"),
    ("POST", "/api/actions/resume-entries"),
    ("POST", "/api/actions/close-position"),
    ("POST", "/api/actions/daily-stats"),
    ("POST", "/api/system/saxo/auth-code"),
    ("GET", "/api/data/providers"),
    ("GET", "/api/data/providers/saxo"),
    ("GET", "/api/data/providers/saxo/config"),
    ("PUT", "/api/data/providers/saxo/config"),
    ("GET", "/api/data/providers/saxo/availability"),
    ("GET", "/api/data/providers/dukascopy/instruments"),
    ("POST", "/api/data/providers/dukascopy/instruments"),
    ("DELETE", "/api/data/providers/dukascopy/instruments/de40"),
    ("GET", "/api/data/providers/dukascopy/coverage"),
    ("POST", "/api/data/providers/dukascopy/fetch"),
    ("POST", "/api/data/providers/dukascopy/fetch/stop"),
    ("PUT", "/api/data/providers/dukascopy/keep"),
    ("GET", "/api/data/assets"),
    ("GET", "/api/data/assets/saxo/2"),
    ("GET", "/api/data/assets/saxo/2/bars"),
    ("GET", "/api/data/assets/saxo/2/range-bars"),
    ("GET", "/api/data/assets/saxo/2/context"),
    ("GET", "/api/data/assets/saxo/2/statistics"),
    ("GET", "/api/data/assets/saxo/2/volume"),
    ("GET", "/api/data/assets/saxo/2/live"),
    ("GET", "/api/data/providers/capital/config"),
    ("PUT", "/api/data/providers/capital/config"),
    ("GET", "/api/config"),
    ("GET", "/api/config/catalog"),
    ("GET", "/api/config/history"),
    ("POST", "/api/config/validate"),
    ("PUT", "/api/config"),
    ("GET", "/api/config/backups"),
    ("GET", "/api/config/backups/config.json.bak-20260101-000000"),
    ("POST", "/api/config/backups/config.json.bak-20260101-000000/restore"),
    ("GET", "/api/backtests/options"),
    ("GET", "/api/backtests/coverage"),
    ("GET", "/api/backtests"),
    ("POST", "/api/backtests"),
    ("POST", "/api/backtests/remove"),
    ("DELETE", "/api/backtests/former"),
    ("GET", "/api/backtests/research"),
    ("PUT", "/api/backtests/research"),
    ("GET", "/api/backtests/20261012-213045-a1b2c3"),
    ("PATCH", "/api/backtests/20261012-213045-a1b2c3"),
    ("DELETE", "/api/backtests/20261012-213045-a1b2c3"),
    ("POST", "/api/backtests/20261012-213045-a1b2c3/cancel"),
    ("GET", "/api/backtests/20261012-213045-a1b2c3/days"),
    ("GET", "/api/backtests/20261012-213045-a1b2c3/signals"),
    ("GET", "/api/backtests/20261012-213045-a1b2c3/trades"),
    ("GET", "/api/backtests/20261012-213045-a1b2c3/breakdowns"),
    ("GET", "/api/backtests/20261012-213045-a1b2c3/baseline"),
    ("GET", "/api/backtests/20261012-213045-a1b2c3/sweep"),
    ("GET", "/api/backtests/20261012-213045-a1b2c3/edge"),
    ("GET", "/api/backtests/20261012-213045-a1b2c3/reconciliation"),
    ("POST", "/api/backtests/20261012-213045-a1b2c3/cells/2/run"),
    ("POST", "/api/backtests/20261012-213045-a1b2c3/stress"),
    ("POST", "/api/backtests/20261012-213045-a1b2c3/model-check"),
    ("GET", "/api/backtests/turbo-model"),
    ("GET", "/api/backtests/execution"),
    ("GET", "/api/backtests/paper"),
    ("GET", "/api/backtests/paper/live"),
    ("POST", "/api/backtests/paper/0123456789ab/report"),
    ("POST", "/api/backtests/20261012-213045-a1b2c3/reference"),
    ("POST", "/api/backtests/turbo-model"),
    ("GET", "/api/mcp"),
    ("POST", "/api/mcp/tokens/0123abcd/revoke"),
    ("PUT", "/api/mcp/paused"),
    ("GET", "/api/research/experiments"),
    ("PUT", "/api/research/lockbox"),
    ("GET", "/api/ta/scripts"),
    ("POST", "/api/ta/scripts"),
    ("GET", "/api/ta/scripts/0123456789ab"),
    ("PUT", "/api/ta/scripts/0123456789ab"),
    ("DELETE", "/api/ta/scripts/0123456789ab"),
    ("POST", "/api/ta/scripts/0123456789ab/duplicate"),
    ("GET", "/api/ta/scripts/0123456789ab/versions/1"),
    ("GET", "/api/ta/scripts/0123456789ab/source.pine"),
    ("GET", "/api/ta/libraries/config.json.bak-20260101-000000"),
    ("GET", "/api/ta/workspaces/2"),
    ("PUT", "/api/ta/workspaces/2"),
    ("GET", "/api/ta/rules"),
    ("GET", "/api/ta/history"),
    ("POST", "/api/ta/rules"),
    ("GET", "/api/ta/rules/0123456789ab"),
    ("PUT", "/api/ta/rules/0123456789ab"),
    ("DELETE", "/api/ta/rules/0123456789ab"),
    ("POST", "/api/ta/rules/0123456789ab/mode"),
    ("GET", "/api/ta/rules/0123456789ab/comparison"),
    ("GET", "/api/ta/signals"),
    ("GET", "/api/ta/engine"),
    ("GET", "/api/ta/monitor/now"),
    ("GET", "/api/ta/monitor"),
    ("GET", "/api/agent"),
    ("POST", "/api/agent/pause"),
    ("POST", "/api/agent/resume"),
    ("GET", "/api/agent/conversations"),
    ("POST", "/api/agent/conversations"),
    ("GET", "/api/agent/conversations/20261028-101500-a1b2c3"),
    ("PATCH", "/api/agent/conversations/20261028-101500-a1b2c3"),
    ("DELETE", "/api/agent/conversations/20261028-101500-a1b2c3"),
    ("POST", "/api/agent/conversations/20261028-101500-a1b2c3/requests"),
    ("POST", "/api/agent/conversations/20261028-101500-a1b2c3/stop"),
    ("POST", "/api/agent/conversations/20261028-101500-a1b2c3/retry"),
    ("POST", "/api/agent/conversations/20261028-101500-a1b2c3/feedback"),
    ("GET", "/api/agent/conversations/20261028-101500-a1b2c3/stream"),
    ("GET", "/api/agent/conversations/20261028-101500-a1b2c3/context"),
    ("GET", "/api/agent/context"),
    ("POST", "/api/agent/conversations/20261028-101500-a1b2c3/proposals/p0a1b2c3d/apply"),
    ("POST", "/api/agent/conversations/20261028-101500-a1b2c3/proposals/p0a1b2c3d/preview"),
    ("POST", "/api/agent/tasks"),
    ("POST", "/api/agent/conversations/20261028-101500-a1b2c3/task/stop"),
    ("POST", "/api/agent/conversations/20261028-101500-a1b2c3/proposals/p0a1b2c3d/dismiss"),
    ("GET", "/api/agent/artefacts"),
]


CLOSED_POSITION = {**POSITION, "position_id": "5002", "position_status": "Closed"}

JOURNAL_ENTRY = {
    "signal_uuid": "aaaaaaaa-0000-4000-8000-000000000001",
    "action": "long",
    "indice": "us100",
    "outcome": "refused",
    "reason": "Breaking trading rule: Cooldown active after last loss.",
}


PRICE_INDEX = {
    "id": 1, "uic": 1909050, "asset_type": "StockIndex", "symbol": "NDX.I", "description": "NASDAQ 100",
    "currency": "USD", "role": "underlying", "indice": "us100", "underlying_uic": 1909050, "direction": None,
    "barrier": None, "issuer": None,
}
PRICE_TURBO = {
    "id": 2, "uic": 40001234, "asset_type": "WarrantOpenEndKnockOut", "symbol": "SG-NDX-L1",
    "description": "NASDAQ 100 Turbo Long 24150 SG", "currency": "EUR", "role": "turbo", "indice": "us100",
    "underlying_uic": 1909050, "direction": "long", "barrier": 24150.0, "issuer": "SG",
}


class FakeQueries:
    def __init__(self):
        self.calls = []
        self.last_loss_at = None
        self.paused_since = None
        self.journal = [JOURNAL_ENTRY]
        self.failing = False
        self.recorder_status = None
        self.price_instruments_recorded = [PRICE_INDEX, PRICE_TURBO]
        self.price_bar_rows = [[1791792000, 8.40, 8.52, 8.36, 8.45, 8.45, 8.57, 8.41, 8.50, 3]]
        self.price_coverage_rows = []
        # Ten-second bars per instrument, as (epoch seconds, open, high, low, close).
        self.price_series_rows = {}
        # Stretches of consecutive bars (src.admin.prices.Run), and Saxo's volume per instrument.
        self.price_run_rows = []
        self.price_volume_by_instrument = {}
        # What price_assets adds to an instrument's row, by id, and Saxo's volume by instrument and day.
        self.price_asset_extras = {}
        self.price_volume_by_day = {}
        # The Capital.com feed: its instruments with their first and last bar (none unless a
        # test adds some), what it wrote of itself, and what its tables hold.
        self.capital_rows = []
        self.capital_state = None
        self.heartbeats = {}
        self.capital_bar_rows = []
        self.capital_series_rows = []
        # Both sides of each ten seconds, as the TA engine reads them (src/ta_engine/source.py).
        self.capital_engine_bar_rows = []
        self.capital_coverage_rows = []
        self.capital_volume_rows = []
        self.capital_volume_by_day = {}
        self.capital_hours = None
        # The TA engine: the signals it journaled, and what it wrote of itself.
        self.ta_signal_rows = []
        self.ta_engine = None
        self.ta_rules = {}
        self.ta_other_rows = []
        # What the engine measured of itself: the last minutes, and a period (AdminQueries' shapes).
        self.ta_monitor_recent_rows = {}
        self.ta_monitor_period_rows = {}
        # What backtests read: the turbos and the recorder by day, an asset's bars by day, the account's value.
        self.backtest_turbo_rows = []
        self.backtest_recorder_rows = []
        self.backtest_asset_rows = []
        self.balance = None

    def _check(self):
        if self.failing:
            raise DatabaseUnavailable("connection refused")

    async def price_recorder_status(self):
        self._check()
        return self.recorder_status

    async def ping(self):
        self._check()
        return True

    async def day_overview(self):
        self._check()
        return {"trade_count": 2, "closed_count": 1, "performance_percent": 1.2, "profit_loss": 34.5,
                "best_percent": 1.2, "worst_percent": 1.2, "by_action": []}

    async def last_loss_timestamp(self):
        return self.last_loss_at

    async def list_positions(self, status=None, start=None, end=None, limit=500):
        self._check()
        self.calls.append(("list_positions", status, start, end, limit))
        return {"items": [POSITION], "total": 1}

    async def position_chart(self, position_id):
        for position in (POSITION, CLOSED_POSITION):
            if position_id == position["position_id"]:
                return {"price_instrument_id": 42, "opened_at": datetime(2026, 10, 19, 22, 30, tzinfo=timezone.utc),
                        "closed_at": None, "signal": {"action": "long", "signal_timestamp": "2026-10-19T22:29:40Z"}}
        return None

    async def get_position(self, position_id):
        for position in (POSITION, CLOSED_POSITION):
            if position_id == position["position_id"]:
                return {"position": position, "orders": [], "requests": [], "requests_total": 0}
        return None

    async def open_positions(self):
        self._check()
        self.calls.append(("open_positions",))
        return [{
            **POSITION, "live_bid": 8.75, "live_performance_percent": 2.94, "live_observed_at": None,
            "sell_refusals": None,
        }]

    async def entries_paused_since(self):
        return self.paused_since

    async def entries_paused_by_guard(self):
        return getattr(self, "paused_by_guard", None)

    async def today_closed_results(self):
        return list(getattr(self, "closed_results", []))

    async def signal_journal(self, start=None, end=None, limit=200):
        self.calls.append(("signal_journal", start, end, limit))
        return self.journal

    async def journal_between(self, start=None, end=None):
        self._check()
        self.calls.append(("journal_between", start, end))
        return self.journal

    async def closed_positions(self, start=None, end=None):
        self._check()
        self.calls.append(("closed_positions", start, end))
        return [CLOSED_POSITION]

    async def execution_rows(self, start, end):
        self._check()
        self.calls.append(("execution_rows", start, end))
        return {"signals": [], "orders": [], "quotes": [], "requests": [
            {"requested_at": datetime(2026, 10, 13, 13, 30, tzinfo=timezone.utc), "purpose": "open", "outcome": "refused",
             "error_code": "TooFar"},
        ]}

    # ── what a backtest can be run on ──

    async def backtest_turbo_days(self, indice, first_day, last_day):
        self._check()
        self.calls.append(("backtest_turbo_days", indice, first_day, last_day))
        return self.backtest_turbo_rows

    async def backtest_recorder_days(self, first_day, last_day):
        self._check()
        self.calls.append(("backtest_recorder_days", first_day, last_day))
        return self.backtest_recorder_rows

    async def backtest_asset_days(self, provider, instrument_id, first_day, last_day):
        self._check()
        self.calls.append(("backtest_asset_days", provider, instrument_id, first_day, last_day))
        return self.backtest_asset_rows

    async def last_balance(self):
        self._check()
        return self.balance

    # ── recorded prices ──

    async def price_instruments(self, start, end):
        self._check()
        self.calls.append(("price_instruments", start, end))
        return list(self.price_instruments_recorded)

    async def price_instrument(self, instrument_id):
        self._check()
        return next((item for item in self.price_instruments_recorded if item["id"] == instrument_id), None)

    async def price_bars(self, instrument_id, start, end, interval, limit):
        self.calls.append(("price_bars", instrument_id, start, end, interval, limit))
        return [row for row in self.price_bar_rows if start.timestamp() <= row[0] < end.timestamp()][:limit]

    async def price_series(self, instrument_id, start, end, side, limit):
        self.calls.append(("price_series", instrument_id, start, end, side, limit))
        rows = self.price_series_rows.get(instrument_id, [])
        return [row for row in rows if start.timestamp() <= row[0] < end.timestamp()][:limit]

    async def price_coverage(self, start, end):
        self.calls.append(("price_coverage", start, end))
        return self.price_coverage_rows

    async def price_runs(self, start, end, limit):
        self.calls.append(("price_runs", start, end, limit))
        return self.price_run_rows[:limit]

    async def price_volumes(self, start, end):
        self.calls.append(("price_volumes", start, end))
        return dict(self.price_volume_by_instrument)

    async def price_trades(self, instrument, start, end):
        self._check()
        self.calls.append(("price_trades", instrument["id"], start, end))
        return [CLOSED_POSITION]

    async def price_assets(self, limit, since=None, instrument_id=None):
        self._check()
        self.calls.append(("price_assets", limit, since, instrument_id))
        now = datetime.now(timezone.utc)
        rows = [
            {
                **item,
                # In the recording set a minute ago; quoted on Monday 12 Oct 2026 from 08:00 to 23:59:50 in Paris.
                "first_seen_at": now - timedelta(hours=2), "last_seen_at": now - timedelta(minutes=1),
                "first_t": 1791784800, "last_t": 1791842390, "last_bid": 8.45, "last_ask": 8.5,
                **self.price_asset_extras.get(item["id"], {}),
            }
            for item in self.price_instruments_recorded
        ]
        if instrument_id is not None:
            rows = [row for row in rows if row["id"] == instrument_id]
        if since is not None:
            rows = [row for row in rows if row["last_seen_at"] >= since]
        return rows[:limit]

    async def price_index_of(self, indice, start, end):
        self.calls.append(("price_index_of", indice, start, end))
        return next(
            (item for item in self.price_instruments_recorded if item["role"] == "underlying" and item["indice"] == indice),
            None,
        )

    async def price_volume_days(self, instrument_id, start, end):
        self.calls.append(("price_volume_days", instrument_id, start, end))
        return dict(self.price_volume_by_day.get(instrument_id, {}))

    # ── the Capital.com feed ──

    async def capital_feed_state(self):
        self._check()
        return self.capital_state

    async def service_heartbeats(self):
        self._check()
        return self.heartbeats

    async def ta_signals(self, start=None, end=None, rule_id=None, limit=500):
        self._check()
        self.calls.append(("ta_signals", start, end, rule_id, limit))
        return self.ta_signal_rows

    async def rule_signals(self, rule_id, start, end, limit=20_000):
        self._check()
        self.calls.append(("rule_signals", rule_id, start, end, limit))
        return list(getattr(self, "raised_rows", []))[-limit:]

    async def ta_engine_state(self):
        self._check()
        return self.ta_engine

    async def ta_other_signals(self, indice, day):
        self._check()
        self.calls.append(("ta_other_signals", indice, day))
        return self.ta_other_rows

    async def ta_rule_states(self):
        self._check()
        return self.ta_rules

    async def ta_monitor_recent(self, since, *, slow_tick_ms):
        self._check()
        self.calls.append(("ta_monitor_recent", since, slow_tick_ms))
        return self.ta_monitor_recent_rows

    async def ta_monitor_period(self, start, end, bucket_seconds, rule_id=None, *, signal_age_seconds, slow_tick_ms):
        self._check()
        self.calls.append(("ta_monitor_period", start, end, bucket_seconds, rule_id, signal_age_seconds, slow_tick_ms))
        return self.ta_monitor_period_rows

    async def capital_assets(self, epic=None):
        self._check()
        self.calls.append(("capital_assets", epic))
        return [dict(row) for row in self.capital_rows if epic is None or row["epic"] == epic]

    async def capital_instrument(self, epic):
        rows = await self.capital_assets(epic)
        return rows[0] if rows else None

    async def capital_opening_hours(self, epic):
        self.calls.append(("capital_opening_hours", epic))
        return self.capital_hours

    async def capital_coverage(self, start, end):
        self._check()
        self.calls.append(("capital_coverage", start, end))
        return self.capital_coverage_rows

    async def capital_bars(self, instrument_id, start, end, interval, limit):
        self.calls.append(("capital_bars", instrument_id, start, end, interval, limit))
        rows = [row for row in self.capital_bar_rows if start.timestamp() <= row[0] < end.timestamp()][:limit]
        return rows, rows[-1][0] if rows else None

    async def capital_series(self, instrument_id, start, end, side, limit):
        self.calls.append(("capital_series", instrument_id, start, end, side, limit))
        return [row for row in self.capital_series_rows if start.timestamp() <= row[0] < end.timestamp()][:limit]

    async def capital_engine_rows(self, instrument_id, start, end, limit):
        self.calls.append(("capital_engine_rows", instrument_id, start, end, limit))
        return [row for row in self.capital_engine_bar_rows if start.timestamp() <= row[0] < end.timestamp()][:limit]

    async def capital_volume(self, instrument_id, start, end, interval, limit):
        self.calls.append(("capital_volume", instrument_id, start, end, interval, limit))
        return self.capital_volume_rows[:limit]

    async def capital_volume_days(self, instrument_id, start, end):
        self.calls.append(("capital_volume_days", instrument_id, start, end))
        return dict(self.capital_volume_by_day)

    async def index_trades(self, indice, start, end, *, every_position, limit=500):
        self._check()
        self.calls.append(("index_trades", indice, start, end, every_position))
        return [CLOSED_POSITION]

    async def exits(self, start=None, end=None):
        self.calls.append(("exits", start, end))
        return {"summary": {"trade_count": 0}, "by_reason": [], "by_hour": [], "by_holding_time": []}

    async def report(self, milestones_eur=None):
        self.calls.append(("report", milestones_eur))
        return {"report_date": "2026-10-12", "has_trade_history": False, "balance_history": []}

    async def list_orders(self, start=None, end=None, limit=500):
        self.calls.append(("list_orders", start, end, limit))
        return {"items": [], "total": 0, "refusals": []}

    async def performance(self, start=None, end=None):
        self.calls.append(("performance", start, end))
        return {"summary": {"trade_count": 0}, "daily": [], "by_action": [], "by_instrument": []}


class FakeRiskState:
    """The admin's write connection: it only ever sets or clears the pause."""

    def __init__(self, queries):
        self.queries = queries
        self.failing = False

    def _check(self):
        if self.failing:
            raise DatabaseUnavailable("connection refused")

    async def pause_entries(self, ts):
        self._check()
        if self.queries.paused_since is None:
            self.queries.paused_since = ts

    async def resume_entries(self):
        self._check()
        self.queries.paused_since = None

    async def get_entries_paused_since(self):
        return self.queries.paused_since


class FakeServices:
    watchlist_enabled = True
    watchlist_directions = ["long", "short"]

    def __init__(self):
        self.sent_signals = []
        self.published = []
        self.web_server_down = False
        self.webhook = {"sources": {}, "ignored": {}}
        self.consumers = {"trading-ops": 1, "telegram_channel": 1}

    async def latest_signals(self):
        if self.web_server_down:
            raise ServiceUnavailable("web_server: connection refused")
        return [{"action": "long", "indice": "us100", "seq": 1}, {"action": "short", "indice": "de40", "seq": 2}]

    async def webhook_state(self):
        if self.web_server_down:
            raise ServiceUnavailable("web_server: connection refused")
        return self.webhook

    async def send_signal(self, payload):
        if self.web_server_down:
            raise ServiceUnavailable("web_server: connection refused")
        self.sent_signals.append(payload)
        return "11111111-2222-3333-4444-555555555555"

    async def watchlist_health(self):
        return {"ready": True, "warm_entries": 2, "configured_targets": 2, "stale_entries": 0,
                "last_refresh_errors": []}

    async def queue_stats(self):
        return [
            {"name": name, "declared": True, "messages": 0, "consumers": consumers}
            for name, consumers in self.consumers.items()
        ]

    async def publish_ops(self, message):
        self.published.append(message)

    async def watchlist_best(self, indice, direction):
        if self.web_server_down:
            raise ServiceUnavailable("watchlist_manager: connection refused")
        if direction == "short":
            return None
        return {
            "selected_instrument": {
                "description": "TURBO LONG NASDAQ", "symbol": "SG-L1", "currency": "EUR",
                "latest_bid": 8.4, "latest_ask": 8.45, "commissions": {"secret-ish": 1},
            },
            "watchlist": {"price_last_updated_at": "2026-10-12T08:00:00Z", "stale": False},
        }


class FakeSaxo:
    def __init__(self):
        self.codes = []
        self.has_token = True
        self.refresh_token_expired = False
        self.unreadable = False

    def status(self):
        if self.unreadable:
            raise ValueError("token file is corrupt")
        return {
            "has_token": self.has_token,
            "refresh_token_expired": self.refresh_token_expired,
            "authorization_url": "https://saxo.test/authorize",
        }

    async def hand_over(self, code, *, state=None, environment=None, by=None):
        self.codes.append(code.strip())
        return environment or "live"

    async def authorize(self, environment=None):
        return "https://saxo.test/authorize?state=fresh"


@pytest.fixture
def config_path(tmp_path):
    with open("tests/test_config.json") as source:
        config = json.load(source)
    config["logging"]["persistant"]["log_path"] = str(tmp_path / "log")
    for rule in config["trade"]["rules"]:
        if rule["rule_type"] == "allowed_indices":
            rule["rule_config"]["indice_ids"] = {"us100": "1909050"}
    config["trade"]["rules"].append({
        "rule_type": "risk_management",
        "rule_name": "risk_management",
        "rule_config": {"cooldown_after_loss_minutes": 10, "max_trades_per_day": 8},
    })
    path = tmp_path / "etc" / "config.json"
    path.parent.mkdir()
    path.write_text(json.dumps(config, indent=2))
    return str(path)


def make_ctx(config_path, tmp_path, **settings_overrides):
    config_manager = ConfigurationManager(config_path)
    settings = AdminSettings.from_config(config_manager)
    settings.credentials_path = str(tmp_path / "admin" / "credentials.json")
    settings.backups_path = str(tmp_path / "backups")
    for name, value in settings_overrides.items():
        setattr(settings, name, value)
    credentials = CredentialStore(settings.credentials_path)
    credentials.set_password(PASSWORD)
    queries = FakeQueries()
    return AdminContext(
        config_manager=config_manager,
        settings=settings,
        credentials=credentials,
        sessions=SessionStore(idle_seconds=3600, max_age_seconds=3600),
        throttle=LoginThrottle(max_failures=3, lockout_seconds=600),
        queries=queries,
        services=FakeServices(),
        saxo=FakeSaxo(),
        config_editor=ConfigEditor(config_path),
        risk_state=FakeRiskState(queries),
        audit_log=AuditLog(str(tmp_path / "log" / "wata-admin" / "audit.jsonl")),
        trusted_proxies=TrustedProxies([PROXY]),
        version="9.9.9",
    )


def make_client(ctx, static_dir, peer=PROXY):
    return TestClient(create_app(ctx, static_dir=str(static_dir)), base_url=ORIGIN, client=(peer, 50000))


@pytest.fixture
def ctx(config_path, tmp_path):
    return make_ctx(config_path, tmp_path)


@pytest.fixture
def anonymous(ctx, tmp_path):
    with make_client(ctx, tmp_path / "no-interface") as client:
        yield client


@pytest.fixture
def client(anonymous):
    response = anonymous.post("/api/session", json={"password": PASSWORD}, headers=SAME_ORIGIN)
    assert response.status_code == 204
    return anonymous


# ── authentication ──


@pytest.mark.parametrize("method,path", AUTHENTICATED_ROUTES)
def test_every_route_requires_a_session(anonymous, method, path):
    response = anonymous.request(method, path, headers=SAME_ORIGIN, json={})

    assert response.status_code == 401


def test_every_api_route_is_covered_by_the_session_test(ctx, tmp_path):
    app = create_app(ctx, static_dir=str(tmp_path / "no-interface"))
    # Saxo sends the browser back there from its own site: the state admits it (src/admin/routers/saxo.py).
    public = {("POST", "/api/session"), ("GET", "/api/healthz"), ("GET", "/api/saxo/callback")}
    declared = {
        (
            method,
            route.path.replace("{position_id}", "5001")
            .replace("{service}", "wata-api")
            .replace("{name}", "config.json.bak-20260101-000000")
            .replace("{run_id}", "20261012-213045-a1b2c3")
            .replace("{cell}", "2")
            .replace("{day}", "2026-10-12")
            .replace("{provider}", "saxo" if "/assets/" in route.path else "saxo")
            .replace("{key}", "2")
            .replace("{label}", "de40")
            .replace("{script_id}", "0123456789ab")
            .replace("{rule_id}", "0123456789ab")
            .replace("{owner}", "0123456789ab")
            .replace("{token_id}", "0123abcd")
            .replace("{number}", "1")
            .replace("{conversation_id}", "20261028-101500-a1b2c3")
            .replace("{proposal_id}", "p0a1b2c3d"),
        )
        for route in app.routes
        if route.path.startswith("/api")
        for method in route.methods - {"HEAD", "OPTIONS"}
    }

    assert declared - public == set(AUTHENTICATED_ROUTES)


def test_login_sets_a_protected_session_cookie(anonymous):
    response = anonymous.post("/api/session", json={"password": PASSWORD}, headers=SAME_ORIGIN)

    assert response.status_code == 204
    cookie = response.headers["set-cookie"]
    # The __Host- prefix: a browser refuses such a cookie from a sibling subdomain.
    assert cookie.startswith(f"{SECURE_SESSION_COOKIE_NAME}=")
    assert SECURE_SESSION_COOKIE_NAME == "__Host-wata_admin_session"
    for attribute in ("HttpOnly", "Secure", "SameSite=strict", "Path=/"):
        assert attribute in cookie
    assert "Domain" not in cookie
    assert anonymous.get("/api/session").json()["version"] == "9.9.9"


def test_signing_out_removes_the_cookie_with_the_attributes_it_was_set_with(client):
    response = client.delete("/api/session", headers=SAME_ORIGIN)

    assert response.status_code == 204
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f'{SECURE_SESSION_COOKIE_NAME}=""')
    for attribute in ("Max-Age=0", "Secure", "Path=/"):
        assert attribute in cookie
    assert client.get("/api/session").status_code == 401


def test_cookie_has_no_prefix_when_it_is_not_limited_to_https(config_path, tmp_path):
    # Local use over plain HTTP: a browser would refuse the prefixed name there.
    ctx = make_ctx(config_path, tmp_path, cookie_secure=False)
    with TestClient(create_app(ctx, static_dir=str(tmp_path / "no-interface")), base_url="http://admin.test") as client:
        response = client.post("/api/session", json={"password": PASSWORD}, headers={"Origin": "http://admin.test"})

        assert response.headers["set-cookie"].startswith(f"{SESSION_COOKIE_NAME}=")
        assert "Secure" not in response.headers["set-cookie"]
        assert client.get("/api/session").status_code == 200
        assert client.delete("/api/session", headers={"Origin": "http://admin.test"}).status_code == 204
        assert client.get("/api/session").status_code == 401


def test_wrong_password_is_refused(anonymous):
    response = anonymous.post("/api/session", json={"password": "wrong password!"}, headers=SAME_ORIGIN)

    assert response.status_code == 401
    assert "set-cookie" not in response.headers
    assert anonymous.get("/api/session").status_code == 401


def test_login_is_locked_after_repeated_failures_even_with_the_right_password(anonymous):
    for _ in range(3):
        assert anonymous.post("/api/session", json={"password": "wrong password!"}, headers=SAME_ORIGIN).status_code == 401

    response = anonymous.post("/api/session", json={"password": PASSWORD}, headers=SAME_ORIGIN)

    assert response.status_code == 429
    assert int(response.headers["retry-after"]) > 0


def test_throttle_uses_the_address_added_by_the_proxy(anonymous):
    # A client-supplied X-Forwarded-For entry must not let it escape the lockout.
    for attempt in range(3):
        headers = {**SAME_ORIGIN, "X-Forwarded-For": f"10.0.0.{attempt}, 203.0.113.9"}
        anonymous.post("/api/session", json={"password": "wrong password!"}, headers=headers)

    headers = {**SAME_ORIGIN, "X-Forwarded-For": "10.9.9.9, 203.0.113.9"}
    assert anonymous.post("/api/session", json={"password": PASSWORD}, headers=headers).status_code == 429
    # Someone else, behind the same proxy, is not locked out with them.
    headers = {**SAME_ORIGIN, "X-Forwarded-For": "198.51.100.7"}
    assert anonymous.post("/api/session", json={"password": PASSWORD}, headers=headers).status_code == 204


def test_a_caller_that_is_not_the_proxy_cannot_choose_its_address(ctx, tmp_path):
    # Another container of the network reaching the admin port directly.
    with make_client(ctx, tmp_path / "no-interface", peer=OTHER_CONTAINER) as direct:
        for attempt in range(3):
            headers = {**SAME_ORIGIN, "X-Forwarded-For": f"203.0.113.{attempt}"}
            assert direct.post("/api/session", json={"password": "wrong password!"}, headers=headers).status_code == 401

        headers = {**SAME_ORIGIN, "X-Forwarded-For": "203.0.113.200"}
        locked = direct.post("/api/session", json={"password": PASSWORD}, headers=headers)

    assert locked.status_code == 429
    assert ctx.throttle.retry_after(OTHER_CONTAINER) > 0
    assert all(ctx.throttle.retry_after(f"203.0.113.{attempt}") == 0 for attempt in range(3))
    locked_out = ctx.audit_log.read(10)[0]
    assert (locked_out["action"], locked_out["ip"]) == ("login locked", OTHER_CONTAINER)
    # The operator, who comes through the proxy, is not locked out by it.
    with make_client(ctx, tmp_path / "no-interface") as through_proxy:
        headers = {**SAME_ORIGIN, "X-Forwarded-For": "203.0.113.200"}
        assert through_proxy.post("/api/session", json={"password": PASSWORD}, headers=headers).status_code == 204


def test_login_is_unavailable_until_a_password_is_set(config_path, tmp_path):
    ctx = make_ctx(config_path, tmp_path)
    os.remove(ctx.settings.credentials_path)

    with make_client(ctx, tmp_path / "no-interface") as client:
        response = client.post("/api/session", json={"password": PASSWORD}, headers=SAME_ORIGIN)

    assert response.status_code == 503


def test_logout_ends_the_session(client):
    assert client.delete("/api/session", headers=SAME_ORIGIN).status_code == 204

    assert client.get("/api/session").status_code == 401


def test_changing_the_password_ends_the_session(client, ctx):
    ctx.credentials.set_password("another long password")
    os.utime(ctx.settings.credentials_path, ns=(1, 1))

    assert client.get("/api/session").status_code == 401


@pytest.mark.parametrize("headers", [{}, {"Origin": "https://evil.example"}])
def test_state_changing_requests_from_another_origin_are_refused(client, ctx, headers):
    response = client.post("/api/actions/signal", json={"action": "close-position"}, headers=headers)

    assert response.status_code == 403
    assert ctx.services.sent_signals == []


def test_login_from_another_origin_is_refused(anonymous):
    response = anonymous.post("/api/session", json={"password": PASSWORD}, headers={"Origin": "https://evil.example"})

    assert response.status_code == 403


def test_served_on_a_port_of_its_own_the_same_name_on_another_port_is_another_origin(config_path, tmp_path):
    # Without a domain of its own, the interface is on a second HTTPS port of the webhook's domain.
    address = "https://admin.test:8443"
    ctx = make_ctx(config_path, tmp_path)
    with TestClient(create_app(ctx, static_dir=str(tmp_path / "no-interface")), base_url=address) as client:
        webhook_origin = {"Origin": "https://admin.test"}
        assert client.post("/api/session", json={"password": PASSWORD}, headers=webhook_origin).status_code == 403

        assert client.post("/api/session", json={"password": PASSWORD}, headers={"Origin": address}).status_code == 204
        assert client.get("/api/session").status_code == 200
        assert client.delete("/api/session", headers=webhook_origin).status_code == 403
        assert client.delete("/api/session", headers={"Origin": address}).status_code == 204


def test_responses_carry_security_headers(anonymous):
    response = anonymous.get("/api/healthz")

    assert response.status_code == 200
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"


# ── monitoring and reporting ──


def test_overview_combines_day_figures_limits_and_open_positions(client, ctx):
    ctx.queries.last_loss_at = datetime.now(timezone.utc) - timedelta(minutes=4)

    overview = client.get("/api/overview").json()

    assert overview["day"]["trade_count"] == 2
    assert overview["limits"]["max_trades_per_day"] == 8
    assert overview["limits"]["cooldown_after_loss_minutes"] == 10
    assert overview["cooldown"]["active"] is True
    assert overview["entries_paused_since"] is None
    # Open positions come with the value the position monitor last saw.
    assert overview["open_positions"][0]["position_id"] == POSITION["position_id"]
    assert overview["open_positions"][0]["live_performance_percent"] == 2.94


def test_overview_gives_what_decides_whether_a_signal_would_be_traded(config_path, tmp_path):
    today = datetime.now(pytz.timezone("Europe/Paris")).strftime("%d/%m/%Y")
    config = json.load(open(config_path))
    for rule in config["trade"]["rules"]:
        if rule["rule_type"] == "market_closed_dates":
            rule["rule_config"]["market_closed_dates"] = ["01/01/2020", today]
    with open(config_path, "w") as config_file:
        json.dump(config, config_file)
    ctx = make_ctx(config_path, tmp_path)

    with make_client(ctx, tmp_path / "no-interface") as client:
        client.post("/api/session", json={"password": PASSWORD}, headers=SAME_ORIGIN)
        overview = client.get("/api/overview").json()

    assert overview["market_closed_today"] is True
    market_hours = next(rule for rule in config["trade"]["rules"] if rule["rule_type"] == "market_hours")["rule_config"]
    for key in ("trading_start_hour", "trading_end_hour", "risky_trading_start_hour", "risky_trading_start_minute"):
        assert overview["limits"][key] == market_hours[key]


def test_overview_says_the_market_is_open_on_an_ordinary_day(client):
    assert client.get("/api/overview").json()["market_closed_today"] is False


def test_overview_says_a_us_holiday_and_an_early_close_as_the_trader_reads_them(client):
    from unittest.mock import patch

    from src.admin.routers import trading

    with patch.object(trading, "datetime") as clock:
        clock.now.side_effect = lambda tz=None: datetime(2026, 11, 26, 14, 0, tzinfo=timezone.utc).astimezone(tz)
        clock.fromtimestamp.side_effect = datetime.fromtimestamp
        thanksgiving = client.get("/api/overview").json()
        clock.now.side_effect = lambda tz=None: datetime(2026, 11, 27, 14, 0, tzinfo=timezone.utc).astimezone(tz)
        day_after = client.get("/api/overview").json()

    assert thanksgiving["market_closed_today"] is True
    assert thanksgiving["market_day"] == {"kind": "closed", "name": "Thanksgiving Day"}
    assert day_after["market_closed_today"] is False
    assert day_after["market_day"]["kind"] == "early_close"
    assert day_after["market_day"]["closes_at"] == "2026-11-27T18:00:00+00:00"


def test_cooldown_is_over_once_the_delay_has_passed(client, ctx):
    ctx.queries.last_loss_at = datetime.now(timezone.utc) - timedelta(minutes=11)

    assert client.get("/api/overview").json()["cooldown"]["active"] is False


def test_positions_filters_are_passed_to_the_query(client, ctx):
    response = client.get("/api/positions?status=closed&from=2026-10-01&to=2026-10-11&limit=50")

    assert response.status_code == 200
    assert response.json() == {"items": [POSITION], "total": 1}
    call = ctx.queries.calls[-1]
    assert call[1] == "Closed"
    assert (call[2].isoformat(), call[3].isoformat(), call[4]) == ("2026-10-01", "2026-10-11", 50)


@pytest.mark.parametrize("query", ["status=deleted", "from=yesterday", "limit=0", "limit=100000"])
def test_invalid_position_filters_are_refused(client, query):
    assert client.get(f"/api/positions?{query}").status_code == 422


def test_unknown_position_is_not_found(client):
    assert client.get("/api/positions/5001").status_code == 200
    assert client.get("/api/positions/nope").status_code == 404


def test_performance_passes_the_date_range(client, ctx):
    body = client.get("/api/performance?from=2026-09-01").json()

    assert body["from"] == "2026-09-01"
    assert body["to"] is None
    assert ctx.queries.calls[-1][1].isoformat() == "2026-09-01"


def test_database_failure_is_reported_as_unavailable(client, ctx):
    ctx.queries.failing = True

    response = client.get("/api/positions")

    assert response.status_code == 503
    assert response.json() == {"detail": "The database is unavailable"}


def test_a_position_is_drawn_on_its_recorded_turbo_and_the_index_of_its_indice(client):
    answer = client.get("/api/positions/5002/chart").json()

    # The test configuration trades us100 without the Capital.com feed: Dukascopy's history below.
    assert answer["turbo"] == "saxo/42" and answer["index"] == "dukascopy/us100"
    # Its opening day in the trading timezone (Paris): 00:30 on the 20th.
    assert answer["day"] == "2026-10-20" and answer["signal"]["action"] == "long"
    assert client.get("/api/positions/9999/chart").status_code == 404


def test_signals_are_listed_newest_first(client):
    items = client.get("/api/signals").json()["items"]

    assert [signal["seq"] for signal in items] == [2, 1]


def test_signals_come_with_the_sources_and_the_alerts_the_web_server_ignored(client, ctx):
    ctx.services.webhook = {"sources": {"us100": "engine"}, "ignored": {"us100": {"count": 3, "first_at": "x", "last_at": "y"}}}

    answer = client.get("/api/signals").json()

    assert answer["webhook"]["ignored"]["us100"]["count"] == 3


def test_signals_report_an_unreachable_web_server(client, ctx):
    ctx.services.web_server_down = True

    assert client.get("/api/signals").status_code == 502


# ── system ──


def _statuses(health):
    return {service["id"]: service["status"] for service in health["services"]}


def test_health_reports_each_service(client):
    health = client.get("/api/system/health").json()

    statuses = _statuses(health)
    assert statuses["postgres"] == "ok"
    assert statuses["web_server"] == "ok"
    assert statuses["rabbitmq"] == "ok"
    assert statuses["position_monitor"] == "ok"
    assert statuses["telegram"] == "ok"
    assert statuses["watchlist_manager"] == "ok"
    # No log file to read a start line from.
    assert statuses["trader"] == "unknown"
    assert statuses["scheduler"] == "unknown"
    assert statuses["saxo"] == "ok"
    assert health["status"] == "ok"
    assert health["version"] == "9.9.9"


@pytest.mark.parametrize("has_token, refresh_token_expired", [(True, True), (False, False)])
def test_health_says_when_saxo_needs_a_new_authorisation(client, ctx, has_token, refresh_token_expired):
    # Every service is up, and nothing can be traded: the summary must not read "ok".
    ctx.saxo.has_token = has_token
    ctx.saxo.refresh_token_expired = refresh_token_expired

    health = client.get("/api/system/health").json()

    saxo = health["services"][0]
    assert (saxo["id"], saxo["name"], saxo["status"]) == ("saxo", "Saxo connection", "down")
    assert "Authorisation required" in saxo["detail"]
    assert health["status"] == "down"
    assert {service["status"] for service in health["services"][1:]} <= {"ok", "unknown"}


def test_health_survives_an_unreadable_saxo_token(client, ctx):
    ctx.saxo.unreadable = True

    response = client.get("/api/system/health")

    assert response.status_code == 200
    assert _statuses(response.json())["saxo"] == "unknown"
    assert response.json()["status"] == "ok"


# ── price recorder ──


def _with_recorder(config_path, tmp_path, **section):
    config = json.load(open(config_path))
    config["trade"]["config"]["price_recorder"] = {"enabled": True, **section}
    with open(config_path, "w") as config_file:
        json.dump(config, config_file)
    return make_ctx(config_path, tmp_path)


def _recorder_entry(ctx, tmp_path):
    with make_client(ctx, tmp_path / "no-interface") as client:
        client.post("/api/session", json={"password": PASSWORD}, headers=SAME_ORIGIN)
        health = client.get("/api/system/health").json()
    entries = [service for service in health["services"] if service["id"] == "price_recorder"]
    return (entries[0] if entries else None), health["status"]


def _bucket(seconds_ago, bars=43):
    return {"ts": datetime.now(timezone.utc) - timedelta(seconds=seconds_ago), "feed_ok": bars > 0, "instruments": bars, "bars": bars}


def test_the_price_recorder_is_not_listed_while_it_is_switched_off(client):
    health = client.get("/api/system/health").json()

    assert "price_recorder" not in _statuses(health)


def test_a_recorder_that_writes_is_up_with_what_it_records(config_path, tmp_path):
    ctx = _with_recorder(config_path, tmp_path)
    ctx.queries.recorder_status = _bucket(seconds_ago=14)

    entry, overall = _recorder_entry(ctx, tmp_path)

    assert (entry["name"], entry["status"], entry["detail"]) == ("Price recorder", "ok", "Recording 43 instruments")
    assert overall == "ok"


def test_a_recorder_that_stopped_writing_is_down_and_so_is_the_summary(config_path, tmp_path):
    ctx = _with_recorder(config_path, tmp_path)
    ctx.queries.recorder_status = _bucket(seconds_ago=10 + 5 * 60)

    entry, overall = _recorder_entry(ctx, tmp_path)

    assert (entry["status"], entry["detail"]) == ("down", "No sign of it for 5 min")
    # What it misses cannot be fetched later: it counts like a service that trades.
    assert overall == "down"


def test_a_recorder_that_never_wrote_is_down(config_path, tmp_path):
    ctx = _with_recorder(config_path, tmp_path)

    entry, _ = _recorder_entry(ctx, tmp_path)

    assert (entry["status"], entry["detail"]) == ("down", "Nothing recorded yet")


def test_a_recorder_with_nothing_to_record_is_a_problem_only_in_trading_hours(config_path, tmp_path):
    from unittest.mock import patch

    ctx = _with_recorder(config_path, tmp_path)
    ctx.queries.recorder_status = _bucket(seconds_ago=14, bars=0)

    with patch("src.admin.health.RecorderSettings.is_trading_time", return_value=True):
        open_market, _ = _recorder_entry(ctx, tmp_path)
    with patch("src.admin.health.RecorderSettings.is_trading_time", return_value=False):
        closed_market, overall = _recorder_entry(ctx, tmp_path)

    assert (open_market["status"], open_market["detail"]) == ("down", "Running, but no price is being recorded")
    assert (closed_market["status"], closed_market["detail"]) == ("ok", "Running; the markets are closed")
    assert overall == "ok"


def test_a_recorder_with_a_broken_configuration_says_which_field(config_path, tmp_path):
    ctx = _with_recorder(config_path, tmp_path, max_instruments="many")

    entry, _ = _recorder_entry(ctx, tmp_path)

    assert entry["status"] == "down"
    assert "trade.config.price_recorder.max_instruments" in entry["detail"]


def test_the_recorder_state_is_unknown_while_the_database_is_down(config_path, tmp_path):
    ctx = _with_recorder(config_path, tmp_path)
    ctx.queries.failing = True

    entry, _ = _recorder_entry(ctx, tmp_path)

    assert (entry["status"], entry["detail"]) == ("unknown", "The database is unavailable")


def test_health_details_count_in_plain_words(client, ctx):
    details = {service["id"]: service["detail"] for service in client.get("/api/system/health").json()["services"]}

    assert "(s)" not in " ".join(details.values())
    assert details["web_server"].endswith("in store")


def test_health_reports_failures_without_failing_itself(client, ctx):
    ctx.queries.failing = True
    ctx.services.web_server_down = True
    ctx.services.consumers["trading-ops"] = 0

    response = client.get("/api/system/health")

    assert response.status_code == 200
    statuses = _statuses(response.json())
    assert statuses["postgres"] == "down"
    assert statuses["web_server"] == "down"
    assert statuses["position_monitor"] == "down"
    assert statuses["telegram"] == "ok"
    assert response.json()["status"] == "down"


def test_health_reads_the_trader_state_from_its_log(client, ctx):
    log_dir = os.path.join(ctx.settings.log_path, "wata-trader-async")
    os.makedirs(log_dir)
    with open(os.path.join(log_dir, "wata-trader-async.log"), "w") as log_file:
        log_file.write("2026-10-11 08:00:00,000 - INFO - __main__ - --- Starting WATA Async Trader v0.11.0 ---\n")
        log_file.write("2026-10-11 08:00:05,000 - INFO - __main__ - Trader startup complete. Waiting for signals (long-poll)...\n")

    trader = next(s for s in client.get("/api/system/health").json()["services"] if s["id"] == "trader")
    assert trader["status"] == "ok"
    assert "2026-10-11 08:00:05" in trader["detail"]
    assert ",000" not in trader["detail"]

    with open(os.path.join(log_dir, "wata-trader-async.log"), "a") as log_file:
        log_file.write("2026-10-11 09:00:00,000 - INFO - __main__ - --- Shutting down WATA Async Trader ---\n")

    trader = next(s for s in client.get("/api/system/health").json()["services"] if s["id"] == "trader")
    assert trader["status"] == "down"


def _beat(age_seconds=5, **figures):
    return {"beat_at": datetime.now(timezone.utc) - timedelta(seconds=age_seconds), "started_at": None,
            "version": "0.35.0", "figures": figures}


def test_health_reads_the_trader_and_the_position_monitor_from_their_heartbeats(client, ctx):
    ctx.queries.heartbeats = {
        "trader": _beat(last_signal_at="2026-10-26T09:12:00+00:00", saxo_login={},
                        saxo_session={"trade_level": "OrdersOnly"}),
        "position_monitor": _beat(stream_connected=True, positions_listed=0, close_intents=[
            {"position_id": "p1", "reason": "Explicit Close (End of day)", "since": "2026-10-26T20:56:01+00:00",
             "attempts": 3, "last_error": "Saxo says it cannot be closed now"},
        ]),
    }

    services = {s["id"]: s for s in client.get("/api/system/health").json()["services"]}

    assert services["trader"] == {"id": "trader", "name": "Trader", "status": "ok",
                                  "detail": "Polling the web server, last signal 10:12; Saxo session at OrdersOnly"}
    assert services["position_monitor"]["status"] == "warning"
    assert services["position_monitor"]["detail"] == (
        "Selling position p1 since 21:56 (3 failed attempts: Saxo says it cannot be closed now)"
    )


def test_a_heartbeat_that_stopped_is_a_service_down(client, ctx):
    ctx.queries.heartbeats = {"trader": _beat(age_seconds=600), "position_monitor": _beat(age_seconds=95)}

    services = {s["id"]: s for s in client.get("/api/system/health").json()["services"]}

    assert (services["trader"]["status"], services["trader"]["detail"]) == ("down", "No heartbeat for 10 min")
    assert (services["position_monitor"]["status"], services["position_monitor"]["detail"]) == (
        "down", "No heartbeat for 1 min",
    )


def test_health_says_what_keeps_the_trader_from_trading(client, ctx):
    login = {"renewal_failing_since": "2026-10-23T18:05:00+00:00", "error": "Saxo answered 401: invalid_grant"}
    ctx.queries.heartbeats = {"trader": _beat(saxo_login=login)}

    trader = next(s for s in client.get("/api/system/health").json()["services"] if s["id"] == "trader")

    assert trader["status"] == "warning"
    assert trader["detail"] == "Saxo refuses to renew the login since 20:05 (Saxo answered 401: invalid_grant): no new position"


def test_the_overview_says_saxo_refusing_to_renew_the_login(client, ctx):
    assert client.get("/api/overview").json()["saxo_login"] is None
    login = {"renewal_failing_since": "2026-10-23T18:05:00+00:00", "error": "Saxo answered 401", "lapses_at": None}
    ctx.queries.heartbeats = {"trader": _beat(saxo_login=login)}

    assert client.get("/api/overview").json()["saxo_login"] == login
    ctx.queries.heartbeats = {"trader": _beat(age_seconds=600, saxo_login=login)}
    assert client.get("/api/overview").json()["saxo_login"] is None


def test_health_says_the_last_backup_and_when_it_failed_or_is_old(client, ctx):
    from src.admin.health import backups_entry

    now = datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc)
    good = {"at": "2026-10-27T02:17:03Z", "file": "wata-2026-10-27.dump", "bytes": 48_000_000, "ok": True, "detail": "dumped"}

    assert backups_entry(good, now, "Europe/Paris") == [{
        "id": "backups", "name": "Database backups", "status": "ok",
        "detail": "Last dump 27/10 03:17, 48 MB (wata-2026-10-27.dump); not sent off the server",
    }]
    assert backups_entry({**good, "ok": False, "detail": "pg_dump failed"}, now, "Europe/Paris")[0]["detail"] == (
        "The dump of 27/10 03:17 failed: pg_dump failed"
    )
    assert backups_entry(good, now + timedelta(hours=31), "Europe/Paris")[0]["status"] == "warning"
    assert backups_entry(None, now, "Europe/Paris")[0]["status"] == "unknown"

    os.makedirs(ctx.settings.backups_path, exist_ok=True)
    with open(os.path.join(ctx.settings.backups_path, "last.json"), "w") as report:
        json.dump({**good, "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}, report)
    backups = next(s for s in client.get("/api/system/health").json()["services"] if s["id"] == "backups")
    assert backups["status"] == "ok"


def test_health_warns_after_two_days_without_a_copy_off_the_server():
    from src.admin.health import backups_entry

    now = datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc)
    dumped = {"at": "2026-10-27T02:17:03Z", "file": "wata-2026-10-27.dump", "bytes": 48_000_000, "ok": True,
              "detail": "dumped"}

    def entry(ok, detail, last_ok_at):
        found = backups_entry({**dumped, "offsite": {"ok": ok, "detail": detail, "last_ok_at": last_ok_at}}, now,
                              "Europe/Paris")
        return found[0]["status"], found[0]["detail"]

    assert entry(True, "Sent to nightly/", "2026-10-27T02:18:10Z") == (
        "ok", "Last dump 27/10 03:17, 48 MB (wata-2026-10-27.dump), sent off the server")
    # One night failed: said, not yet a warning.
    assert entry(False, "rclone could not send it", "2026-10-26T02:18:10Z") == (
        "ok", "Last dump 27/10 03:17, 48 MB (wata-2026-10-27.dump); its copy off the server failed "
              "(rclone could not send it), the last one sent 26/10 03:18")
    assert entry(False, "rclone could not send it", "2026-10-25T02:18:10Z") == (
        "warning", "No copy sent off the server since 25/10 03:18: rclone could not send it")
    assert entry(False, "No BACKUP_AGE_RECIPIENT in .env: nothing is sent unencrypted", None) == (
        "warning", "No copy sent off the server yet: No BACKUP_AGE_RECIPIENT in .env: nothing is sent unencrypted")


def test_log_tail_is_filtered_by_level_and_text(client, ctx):
    log_dir = os.path.join(ctx.settings.log_path, "wata-api")
    os.makedirs(log_dir)
    with open(os.path.join(log_dir, "wata-api.log"), "w") as log_file:
        log_file.write("2026-10-11 08:00:00,000 - INFO - root - Stored signal for indice=us100\n")
        log_file.write("2026-10-11 08:00:01,000 - ERROR - root - Something failed\n")
        log_file.write("Traceback (most recent call last):\n")
        log_file.write("  ValueError: boom\n")
        log_file.write("2026-10-11 08:00:02,000 - WARNING - root - Forbidden access attempt\n")

    everything = client.get("/api/logs/wata-api").json()
    assert [entry["level"] for entry in everything["entries"]] == ["INFO", "ERROR", "WARNING"]
    assert everything["entries"][1]["message"].endswith("ValueError: boom")

    warnings = client.get("/api/logs/wata-api?level=WARNING").json()
    assert [entry["level"] for entry in warnings["entries"]] == ["ERROR", "WARNING"]

    searched = client.get("/api/logs/wata-api?q=BOOM").json()
    assert [entry["level"] for entry in searched["entries"]] == ["ERROR"]

    assert len(client.get("/api/logs/wata-api?lines=1").json()["entries"]) == 1


def test_missing_log_file_is_reported_as_such(client):
    body = client.get("/api/logs/wata-scheduler").json()

    assert body["exists"] is False
    assert body["entries"] == []


@pytest.mark.parametrize("service", ["unknown", "..%2F..%2Fetc%2Fpasswd", "wata-api.log", "%2Fetc%2Fpasswd"])
def test_only_known_log_files_can_be_read(client, service):
    assert client.get(f"/api/logs/{service}").status_code == 404


def test_saxo_status_is_returned(client):
    assert client.get("/api/system/saxo").json()["authorization_url"] == "https://saxo.test/authorize"


# ── operator actions ──


def test_manual_open_signal_is_sent_like_a_webhook(client, ctx, caplog):
    with caplog.at_level(logging.INFO, logger="src.admin.context"):
        response = client.post(
            "/api/actions/signal",
            json={"action": "long", "indice": "us100", "confidence": 0.8},
            headers=SAME_ORIGIN,
        )

    assert response.status_code == 200
    assert response.json()["signal_uuid"] == "11111111-2222-3333-4444-555555555555"
    payload = ctx.services.sent_signals[0]
    assert payload["action"] == "long"
    assert payload["indice"] == "us100"
    assert payload["confidence"] == 0.8
    assert payload["signal_timestamp"] == payload["alert_timestamp"]
    datetime.strptime(payload["signal_timestamp"], "%Y-%m-%dT%H:%M:%SZ")
    assert any("AUDIT" in record.message and "manual signal long" in record.message for record in caplog.records)


@pytest.mark.parametrize("action", ["close-position", "close-long", "close-short"])
def test_manual_close_signal_applies_to_every_indice(client, ctx, action):
    response = client.post(
        "/api/actions/signal",
        json={"action": action, "indice": "us100", "confidence": 0.5},
        headers=SAME_ORIGIN,
    )

    assert response.status_code == 200
    payload = ctx.services.sent_signals[0]
    assert payload["indice"] == "n/a"
    assert "confidence" not in payload


@pytest.mark.parametrize("body", [
    {"action": "long", "indice": "de40"},
    {"action": "long"},
    {"action": "daily_stats"},
    {"action": "buy", "indice": "us100"},
    {"action": "long", "indice": "us100", "confidence": 5},
    {},
])
def test_invalid_manual_signal_is_refused(client, ctx, body):
    response = client.post("/api/actions/signal", json=body, headers=SAME_ORIGIN)

    assert response.status_code == 422
    assert ctx.services.sent_signals == []


def test_manual_signal_reports_an_unreachable_web_server(client, ctx):
    ctx.services.web_server_down = True

    response = client.post("/api/actions/signal", json={"action": "close-position"}, headers=SAME_ORIGIN)

    assert response.status_code == 502


def test_daily_stats_request_is_published_to_the_ops_queue(client, ctx):
    response = client.post("/api/actions/daily-stats", headers=SAME_ORIGIN)

    assert response.status_code == 202
    assert ctx.services.published[0]["action"] == "daily_stats"
    assert ctx.services.published[0]["indice"] == "n/a"


def test_saxo_authorization_code_is_handed_over(client, ctx):
    response = client.post("/api/system/saxo/auth-code", json={"code": "  abc-123  "}, headers=SAME_ORIGIN)

    assert response.status_code == 204
    assert ctx.saxo.codes == ["abc-123"]


def test_actions_can_be_switched_off(config_path, tmp_path):
    ctx = make_ctx(config_path, tmp_path, actions_enabled=False)
    with make_client(ctx, tmp_path / "no-interface") as client:
        client.post("/api/session", json={"password": PASSWORD}, headers=SAME_ORIGIN)

        responses = [
            client.post("/api/actions/signal", json={"action": "close-position"}, headers=SAME_ORIGIN),
            client.post("/api/actions/daily-stats", headers=SAME_ORIGIN),
            client.post("/api/system/saxo/auth-code", json={"code": "abc"}, headers=SAME_ORIGIN),
        ]
        session = client.get("/api/session").json()

    assert [response.status_code for response in responses] == [403, 403, 403]
    assert ctx.services.sent_signals == [] and ctx.services.published == [] and ctx.saxo.codes == []
    assert session["actions_enabled"] is False


# ── configuration ──


def test_config_is_served_masked_with_its_revision(client):
    body = client.get("/api/config").json()

    assert body["config"]["webserver"]["app_secret"] == MASK
    assert "test_secret" not in json.dumps(body)
    assert body["editable"] is True
    assert body["restart_required"] is False
    assert "trade.config" in body["editable_paths"]


def test_config_save_flags_that_a_restart_is_required(client, ctx, config_path):
    loaded = client.get("/api/config").json()
    loaded["config"]["logging"]["level"] = "DEBUG"
    # The services (and this admin) started before the change.
    ctx.started_at -= 60

    response = client.put(
        "/api/config",
        json={"config": loaded["config"], "revision": loaded["revision"]},
        headers=SAME_ORIGIN,
    )

    assert response.status_code == 200
    assert response.json()["restart_required"] is True
    with open(config_path) as config_file:
        saved = json.load(config_file)
    assert saved["logging"]["level"] == "DEBUG"
    assert saved["webserver"]["app_secret"] == "test_secret"
    assert client.get("/api/session").json()["restart_required"] is True


def test_invalid_config_is_refused_with_the_validator_message(client, config_path):
    before = open(config_path).read()
    loaded = client.get("/api/config").json()
    loaded["config"]["logging"]["level"] = "LOUD"

    validated = client.post("/api/config/validate", json={"config": loaded["config"]}, headers=SAME_ORIGIN)
    saved = client.put(
        "/api/config",
        json={"config": loaded["config"], "revision": loaded["revision"]},
        headers=SAME_ORIGIN,
    )

    assert validated.status_code == 422
    assert "Invalid logging level" in validated.json()["detail"]
    assert saved.status_code == 422
    assert open(config_path).read() == before


def test_valid_config_passes_validation(client):
    loaded = client.get("/api/config").json()

    response = client.post("/api/config/validate", json={"config": loaded["config"]}, headers=SAME_ORIGIN)

    assert response.json()["valid"] is True
    assert response.json()["changes"] == []
    assert response.json()["impact"]["services"] == []


def test_a_change_is_previewed_with_the_services_it_reaches_and_nothing_is_written(client, config_path):
    before = open(config_path).read()
    loaded = client.get("/api/config").json()
    for rule in loaded["config"]["trade"]["rules"]:
        if rule["rule_type"] == "market_hours":
            rule["rule_config"]["trading_start_hour"] = 9

    preview = client.post("/api/config/validate", json={"config": loaded["config"]}, headers=SAME_ORIGIN).json()

    assert [change["path"] for change in preview["changes"]] == [
        "trade.rules[market_hours].rule_config.trading_start_hour"
    ]
    assert {"trader", "position_monitor", "scheduler"} <= set(preview["impact"]["services"])
    assert preview["impact"]["risk"] == "trading"
    assert open(config_path).read() == before


def test_a_save_is_written_down_key_by_key_and_said_in_the_audit(client, ctx, config_path):
    loaded = client.get("/api/config").json()
    loaded["config"]["logging"]["level"] = "DEBUG"

    saved = client.put("/api/config", json={"config": loaded["config"], "revision": loaded["revision"]},
                       headers=SAME_ORIGIN).json()
    history = client.get("/api/config/history").json()["items"]

    assert [change["path"] for change in saved["changes"]] == ["logging.level"]
    assert history[0]["changes"][0] == {"path": "logging.level", "before": "INFO", "after": "DEBUG",
                                        "added": False, "removed": False}
    assert history[0]["action"] == "saved" and history[0]["by"]
    audit = next(entry for entry in ctx.audit_log.read() if entry["action"] == "configuration saved")
    # What changed and where it applies, said in the audit trail and on Telegram.
    assert "Changed logging.level." in audit["detail"]
    assert "Applies at the next start of: Web server, Trader" in audit["detail"]


def test_the_catalog_is_served_and_the_unknown_keys_said(client):
    catalog = client.get("/api/config/catalog").json()
    loaded = client.get("/api/config").json()

    paths = {param["path"] for param in catalog["params"]}
    assert "trade.rules[market_hours].rule_config.trading_start_hour" in paths
    assert {group["id"] for group in catalog["groups"]} >= {"day", "hours", "exits", "credentials"}
    assert isinstance(loaded["unknown"], list)


def test_stale_config_revision_is_a_conflict(client):
    loaded = client.get("/api/config").json()

    response = client.put(
        "/api/config",
        json={"config": loaded["config"], "revision": "0" * 64},
        headers=SAME_ORIGIN,
    )

    assert response.status_code == 409


def test_config_editing_can_be_switched_off(config_path, tmp_path):
    before = open(config_path).read()
    ctx = make_ctx(config_path, tmp_path, config_edit_enabled=False)
    with make_client(ctx, tmp_path / "no-interface") as client:
        client.post("/api/session", json={"password": PASSWORD}, headers=SAME_ORIGIN)
        loaded = client.get("/api/config").json()
        response = client.put(
            "/api/config",
            json={"config": loaded["config"], "revision": loaded["revision"]},
            headers=SAME_ORIGIN,
        )

    assert loaded["editable"] is False
    assert response.status_code == 403
    assert open(config_path).read() == before


# ── audit ──


def test_audit_announces_the_action_on_telegram_without_blocking(ctx):
    messages = []

    async def notifier(message):
        messages.append(message)

    class Request:
        headers = {"x-forwarded-for": "203.0.113.9"}
        client = type("Client", (), {"host": PROXY})

    async def scenario():
        ctx.notifier = notifier
        ctx.audit(Request(), "manual signal long", "indice=us100")
        assert messages == []
        await asyncio.sleep(0.01)

    asyncio.run(scenario())

    assert len(messages) == 1
    assert "manual signal long" in messages[0]
    assert "indice=us100" in messages[0]
    assert "203.0.113.9" in messages[0]


def test_audit_survives_a_failing_notifier(ctx):
    async def notifier(message):
        raise ConnectionError("rabbitmq is down")

    class Request:
        headers = {}
        client = None

    async def scenario():
        ctx.notifier = notifier
        ctx.audit(Request(), "login")
        await asyncio.sleep(0.01)

    asyncio.run(scenario())


# ── interface ──


@pytest.fixture
def built_interface(tmp_path):
    static_dir = tmp_path / "static"
    (static_dir / "assets").mkdir(parents=True)
    (static_dir / "index.html").write_text("<html>WATA Admin</html>")
    (static_dir / "favicon.svg").write_text("<svg/>")
    (static_dir / "theme.js").write_text("document.documentElement.classList.toggle('dark', true)")
    (static_dir / "assets" / "app.js").write_text("console.log('app')")
    (tmp_path / "secret.txt").write_text("outside the build")
    return static_dir


def test_interface_is_served_with_a_fallback_to_the_application(ctx, built_interface):
    with make_client(ctx, built_interface) as client:
        assert client.get("/").text == "<html>WATA Admin</html>"
        assert client.get("/positions").text == "<html>WATA Admin</html>"
        assert client.get("/favicon.svg").text == "<svg/>"
        assert client.get("/assets/app.js").text == "console.log('app')"
        assert client.get("/api/unknown").status_code == 404
        assert client.get("/api").status_code == 404


def test_the_page_is_asked_again_at_each_load_so_that_a_deploy_shows_at_once(ctx, built_interface):
    with make_client(ctx, built_interface) as client:
        for path in ("/", "/config/hours", "/theme.js"):
            assert client.get(path).headers["cache-control"] == "no-cache", path
        # The assets, named by their content, are left to the browser.
        assert "cache-control" not in client.get("/assets/app.js").headers


def test_the_script_that_sets_the_theme_is_served_as_a_script_the_browser_will_run(ctx, built_interface):
    # It is a file of the build's root, not an asset, loaded before the application.
    # With "nosniff", a browser only runs it if it is declared as JavaScript; and the
    # policy that forbids inline scripts is why it is a file at all.
    with make_client(ctx, built_interface) as client:
        response = client.get("/theme.js")

    assert response.status_code == 200
    assert response.text.startswith("document.documentElement")
    assert response.headers["content-type"].split(";")[0] in ("text/javascript", "application/javascript")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "script-src 'self'" in response.headers["content-security-policy"]


@pytest.mark.parametrize("path", ["/..%2Fsecret.txt", "/%2E%2E/secret.txt", "/assets/..%2F..%2Fsecret.txt"])
def test_interface_does_not_serve_files_outside_the_build(ctx, built_interface, path):
    with make_client(ctx, built_interface) as client:
        response = client.get(path)

    assert "outside the build" not in response.text


@pytest.mark.parametrize("path", ["/%00", "/favicon.svg%00", "/assets/%00app.js", "/api/logs/wata-api%00"])
def test_a_path_no_file_system_accepts_is_simply_not_found(ctx, built_interface, path):
    with make_client(ctx, built_interface) as client:
        response = client.get(path)

    assert response.status_code == 404
    assert response.headers["x-content-type-options"] == "nosniff"


# ── second factor ──


def _totp_now(ctx):
    import time

    with open(ctx.settings.credentials_path) as credentials_file:
        secret = json.load(credentials_file)["totp_secret"]
    return totp_code(secret, int(time.time() // 30))


def test_nothing_but_the_sign_in_method_is_said_before_signing_in(anonymous):
    assert anonymous.get("/api/healthz").json() == {"service": "admin", "totp": False}
    assert "9.9.9" not in anonymous.get("/api/healthz").text


def test_login_page_can_tell_whether_a_code_is_needed(anonymous, ctx):
    assert anonymous.get("/api/healthz").json()["totp"] is False

    ctx.credentials.enable_totp()

    assert anonymous.get("/api/healthz").json()["totp"] is True


def test_second_factor_is_required_once_enabled(anonymous, ctx):
    ctx.credentials.enable_totp()

    without_code = anonymous.post("/api/session", json={"password": PASSWORD}, headers=SAME_ORIGIN)
    wrong_code = anonymous.post("/api/session", json={"password": PASSWORD, "code": "000000"}, headers=SAME_ORIGIN)

    assert without_code.status_code == 401
    assert wrong_code.status_code == 401
    # Nothing says which of the two was wrong.
    assert without_code.json() == wrong_code.json() == {"detail": "Invalid password or code"}
    assert anonymous.get("/api/session").status_code == 401


def test_password_and_code_sign_in_and_a_code_works_once(config_path, tmp_path):
    ctx = make_ctx(config_path, tmp_path)
    ctx.credentials.enable_totp()
    code = _totp_now(ctx)

    with make_client(ctx, tmp_path / "no-interface") as client:
        first = client.post("/api/session", json={"password": PASSWORD, "code": code}, headers=SAME_ORIGIN)
        assert client.get("/api/session").json()["totp_enabled"] is True
        client.delete("/api/session", headers=SAME_ORIGIN)
        replay = client.post("/api/session", json={"password": PASSWORD, "code": code}, headers=SAME_ORIGIN)

    assert first.status_code == 204
    assert replay.status_code == 401


def test_right_code_with_a_wrong_password_is_refused_and_not_consumed(anonymous, ctx):
    ctx.credentials.enable_totp()
    code = _totp_now(ctx)

    refused = anonymous.post("/api/session", json={"password": "wrong password!", "code": code}, headers=SAME_ORIGIN)
    accepted = anonymous.post("/api/session", json={"password": PASSWORD, "code": code}, headers=SAME_ORIGIN)

    assert refused.status_code == 401
    assert accepted.status_code == 204


def test_failed_codes_count_towards_the_lockout(anonymous, ctx):
    ctx.credentials.enable_totp()
    for _ in range(3):
        anonymous.post("/api/session", json={"password": PASSWORD, "code": "000000"}, headers=SAME_ORIGIN)

    response = anonymous.post("/api/session", json={"password": PASSWORD, "code": _totp_now(ctx)}, headers=SAME_ORIGIN)

    assert response.status_code == 429


# ── analysis, journal, watchlist, timing ──


def test_open_positions_come_with_their_live_value_whatever_the_date_range(client, ctx):
    body = client.get("/api/positions?status=open&from=2020-01-01&to=2020-01-02").json()

    assert body["total"] == 1
    assert body["items"][0]["live_bid"] == 8.75
    assert ("open_positions",) in ctx.queries.calls


def test_journal_exits_and_report_pass_their_filters(client, ctx):
    journal = client.get("/api/journal?from=2026-10-01&limit=50").json()
    exits = client.get("/api/exits?from=2026-09-01&to=2026-09-30").json()
    report = client.get("/api/report").json()

    assert journal["items"] == [JOURNAL_ENTRY]
    assert ctx.queries.calls[-3][0] == "signal_journal"
    assert (ctx.queries.calls[-3][1].isoformat(), ctx.queries.calls[-3][3]) == ("2026-10-01", 50)
    assert (exits["from"], exits["to"], exits["timezone"]) == ("2026-09-01", "2026-09-30", "Europe/Paris")
    assert report["report_date"] == "2026-10-12"
    assert ctx.queries.calls[-1] == ("report", None)


def test_watchlist_lists_what_would_be_bought_per_indice_and_direction(client):
    body = client.get("/api/watchlist").json()

    assert body["enabled"] is True
    long_entry, short_entry = body["items"]
    assert (long_entry["indice"], long_entry["direction"], long_entry["available"]) == ("us100", "long", True)
    assert (long_entry["description"], long_entry["latest_bid"], long_entry["stale"]) == ("TURBO LONG NASDAQ", 8.4, False)
    assert short_entry == {"indice": "us100", "direction": "short", "available": False}
    # Only the fields the page shows leave the server.
    assert "commissions" not in json.dumps(body)


def test_watchlist_reports_an_unreachable_manager_without_failing(client, ctx):
    ctx.services.web_server_down = True

    response = client.get("/api/watchlist")

    assert response.status_code == 200
    assert response.json()["items"] == []
    assert "watchlist_manager" in response.json()["error"]


def test_execution_timings_are_read_from_the_trader_files(client, ctx):
    os.makedirs(ctx.settings.log_path, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    with open(os.path.join(ctx.settings.log_path, f"execution_timing_{today}.jsonl"), "w") as timing_file:
        timing_file.write(json.dumps({
            "action": "long", "confidence": 0.9, "position_scale": 75, "timestamp": f"{today} 14:07:09",
            "steps": {"turbo_found": 120, "order_placed": 340, "position_confirmed": 610, "db_persisted": 640},
        }) + "\n")

    body = client.get("/api/execution-timing?days=7").json()

    assert body["summary"]["trade_count"] == 1
    assert body["entries"][0]["total_ms"] == 640
    assert body["entries"][0]["steps"]["order_placed"] == 340


# ── pause and single close ──


def test_pausing_and_resuming_entries(client, ctx):
    paused = client.post("/api/actions/pause-entries", headers=SAME_ORIGIN)
    overview_paused = client.get("/api/overview").json()
    resumed = client.post("/api/actions/resume-entries", headers=SAME_ORIGIN)

    assert paused.status_code == 200
    assert paused.json()["entries_paused_since"] is not None
    assert overview_paused["entries_paused_since"] == paused.json()["entries_paused_since"]
    assert resumed.json() == {"entries_paused_since": None}
    assert ctx.queries.paused_since is None


def test_pause_that_could_not_be_written_says_so(client, ctx):
    ctx.risk_state.failing = True

    response = client.post("/api/actions/pause-entries", headers=SAME_ORIGIN)

    assert response.status_code == 503
    assert "NOT set" in response.json()["detail"]
    assert ctx.queries.paused_since is None


def test_close_of_one_position_is_sent_to_the_position_monitor(client, ctx):
    response = client.post("/api/actions/close-position", json={"position_id": "5001"}, headers=SAME_ORIGIN)

    assert response.status_code == 202
    message = ctx.services.published[0]
    assert (message["action"], message["position_id"]) == ("close_position", "5001")
    datetime.strptime(message["requested_at"], "%Y-%m-%dT%H:%M:%SZ")


@pytest.mark.parametrize("position_id,status", [("nope", 404), ("5002", 409)])
def test_only_an_open_position_can_be_closed(client, ctx, position_id, status):
    response = client.post("/api/actions/close-position", json={"position_id": position_id}, headers=SAME_ORIGIN)

    assert response.status_code == status
    assert ctx.services.published == []


def test_close_is_not_sent_when_the_position_monitor_is_not_listening(client, ctx):
    ctx.services.consumers["trading-ops"] = 0

    response = client.post("/api/actions/close-position", json={"position_id": "5001"}, headers=SAME_ORIGIN)

    assert response.status_code == 503
    assert ctx.services.published == []


def test_pause_and_close_are_operator_actions_that_can_be_switched_off(config_path, tmp_path):
    ctx = make_ctx(config_path, tmp_path, actions_enabled=False)
    with make_client(ctx, tmp_path / "no-interface") as client:
        client.post("/api/session", json={"password": PASSWORD}, headers=SAME_ORIGIN)
        statuses = [
            client.post("/api/actions/pause-entries", headers=SAME_ORIGIN).status_code,
            client.post("/api/actions/resume-entries", headers=SAME_ORIGIN).status_code,
            client.post("/api/actions/close-position", json={"position_id": "5001"}, headers=SAME_ORIGIN).status_code,
        ]

    assert statuses == [403, 403, 403]
    assert ctx.queries.paused_since is None and ctx.services.published == []


# ── audit trail ──


def test_actions_are_listed_in_the_audit_trail_newest_first(client):
    client.post("/api/actions/pause-entries", headers={**SAME_ORIGIN, "X-Forwarded-For": "203.0.113.9"})
    client.post("/api/actions/resume-entries", headers=SAME_ORIGIN)

    items = client.get("/api/audit").json()["items"]

    assert [item["action"] for item in items[:3]] == ["new entries resumed", "new entries paused", "login"]
    assert items[1]["ip"] == "203.0.113.9"
    assert datetime.fromisoformat(items[0]["at"]).tzinfo is not None
    assert len(client.get("/api/audit?limit=1").json()["items"]) == 1


# ── configuration backups ──


def _save_change(client, level):
    loaded = client.get("/api/config").json()
    loaded["config"]["logging"]["level"] = level
    response = client.put(
        "/api/config", json={"config": loaded["config"], "revision": loaded["revision"]}, headers=SAME_ORIGIN,
    )
    assert response.status_code == 200
    return response.json()


def test_backups_are_listed_and_shown_masked(client):
    saved = _save_change(client, "DEBUG")

    backups = client.get("/api/config/backups").json()["items"]
    backup = client.get(f"/api/config/backups/{saved['backup']}").json()

    assert [item["name"] for item in backups] == [saved["backup"]]
    assert backups[0]["size"] > 0
    assert backup["config"]["logging"]["level"] == "INFO"
    assert backup["config"]["webserver"]["app_secret"] == MASK


def test_restoring_a_backup_puts_the_previous_parameters_back_and_keeps_the_replaced_file(client, config_path):
    before = json.load(open(config_path))
    saved = _save_change(client, "DEBUG")
    revision = client.get("/api/config").json()["revision"]

    restored = client.post(
        f"/api/config/backups/{saved['backup']}/restore", json={"revision": revision}, headers=SAME_ORIGIN,
    )

    assert restored.status_code == 200
    assert json.load(open(config_path)) == before
    kept = restored.json()["backup"]
    assert kept != saved["backup"]
    assert client.get(f"/api/config/backups/{kept}").json()["config"]["logging"]["level"] == "DEBUG"


def test_restore_does_not_bring_back_settings_that_are_changed_on_the_server_only(client, config_path):
    saved = _save_change(client, "DEBUG")
    # Since that backup was made, the operator switched the actions off and
    # changed a secret, over SSH.
    on_server = json.load(open(config_path))
    on_server["admin"] = {"actions_enabled": False, "config_edit_enabled": True}
    on_server["webserver"]["app_secret"] = "rotated_secret"
    with open(config_path, "w") as config_file:
        json.dump(on_server, config_file)
    revision = client.get("/api/config").json()["revision"]

    restored = client.post(
        f"/api/config/backups/{saved['backup']}/restore", json={"revision": revision}, headers=SAME_ORIGIN,
    )

    assert restored.status_code == 200
    now = json.load(open(config_path))
    assert now["logging"]["level"] == "INFO"
    assert now["admin"] == {"actions_enabled": False, "config_edit_enabled": True}
    assert now["webserver"]["app_secret"] == "rotated_secret"


@pytest.mark.parametrize("path, value", [
    (("signal_polling", "service_url"), "http://attacker.example"),
    (("watchlist_manager", "service_url"), "http://attacker.example"),
    (("watchlist_manager", "api_host"), "0.0.0.0"),
    (("watchlist_manager", "api_port"), 9999),
])
def test_where_the_services_connect_cannot_be_changed_from_the_interface(client, config_path, path, value):
    before = open(config_path).read()
    listing = sorted(os.listdir(os.path.dirname(config_path)))
    loaded = client.get("/api/config").json()
    section, key = path
    loaded["config"]["trade"]["config"].setdefault(section, {})[key] = value

    validated = client.post("/api/config/validate", json={"config": loaded["config"]}, headers=SAME_ORIGIN)
    saved = client.put(
        "/api/config", json={"config": loaded["config"], "revision": loaded["revision"]}, headers=SAME_ORIGIN,
    )

    for response in (validated, saved):
        assert response.status_code == 422
        assert f"trade.config.{section}.{key}" in response.json()["detail"]
        assert "only be changed on the server" in response.json()["detail"]
    assert open(config_path).read() == before
    assert sorted(os.listdir(os.path.dirname(config_path))) == listing


def test_config_says_which_settings_are_protected(client):
    protected = client.get("/api/config").json()["protected_paths"]

    assert "trade.config.signal_polling.service_url" in protected
    assert "trade.config.watchlist_manager.service_url" in protected


def test_restore_needs_the_current_revision(client):
    saved = _save_change(client, "DEBUG")

    response = client.post(
        f"/api/config/backups/{saved['backup']}/restore", json={"revision": "0" * 64}, headers=SAME_ORIGIN,
    )

    assert response.status_code == 409


@pytest.mark.parametrize("name", [
    "config.json",
    "config.json.bak-20260101-000000",
    "..%2Fconfig.json.bak-20260101-000000",
    "credentials.json",
])
def test_only_existing_backups_can_be_read_or_restored(client, name):
    revision = client.get("/api/config").json()["revision"]

    assert client.get(f"/api/config/backups/{name}").status_code == 404
    restore = client.post(f"/api/config/backups/{name}/restore", json={"revision": revision}, headers=SAME_ORIGIN)
    assert restore.status_code == 404


def test_restore_is_refused_when_editing_is_switched_off(config_path, tmp_path):
    ctx = make_ctx(config_path, tmp_path)
    with make_client(ctx, tmp_path / "no-interface") as client:
        client.post("/api/session", json={"password": PASSWORD}, headers=SAME_ORIGIN)
        saved = _save_change(client, "DEBUG")
        ctx.settings.config_edit_enabled = False
        revision = client.get("/api/config").json()["revision"]
        response = client.post(
            f"/api/config/backups/{saved['backup']}/restore", json={"revision": revision}, headers=SAME_ORIGIN,
        )

    assert response.status_code == 403


# ── live events ──


def test_event_stream_sends_what_is_published_until_the_session_ends(client, ctx):
    from unittest.mock import patch

    real_subscribe = ctx.events.subscribe

    def subscribe_with_an_event_waiting():
        queue = real_subscribe()
        ctx.events.publish("position_closed", position_id="5001", profit_loss=12.5)
        return queue

    alive = iter([True, True, False])
    with patch.object(ctx.events, "subscribe", subscribe_with_an_event_waiting), \
            patch.object(ctx.sessions, "is_alive", lambda *args: next(alive)), \
            patch("src.admin.routers.system.EVENT_POLL_SECONDS", 0.01), \
            patch("src.admin.routers.system.EVENT_KEEP_ALIVE_SECONDS", 0.0):
        response = client.get("/api/events")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    frames = response.text.split("\n\n")
    assert frames[0] == "retry: 5000"
    assert frames[1] == "event: connected\ndata: {}"
    assert frames[2].startswith("event: position_closed\ndata: ")
    assert json.loads(frames[2].split("data: ", 1)[1]) == {
        "type": "position_closed", "position_id": "5001", "profit_loss": 12.5,
    }
    assert frames[3] == ": keep-alive"
    # The browser that left is no longer written to.
    assert not ctx.events.has_subscribers


# ── recorded prices ──


def test_without_a_period_the_prices_are_those_of_today(client, ctx):
    today = datetime.now(pytz.timezone("Europe/Paris")).date().isoformat()

    body = client.get("/api/data/assets/saxo/2/bars").json()

    assert (body["from"], body["to"]) == (today, today)


def test_bars_come_as_compact_rows_with_their_column_names(client, ctx):
    response = client.get("/api/data/assets/saxo/2/bars?from=2026-10-12&interval=60")

    assert response.status_code == 200
    body = response.json()
    assert body["instrument"]["symbol"] == "SG-NDX-L1"
    assert body["interval"] == 60 and body["timezone"] == "Europe/Paris"
    assert body["columns"] == [
        "t", "bid_open", "bid_high", "bid_low", "bid_close", "ask_open", "ask_high", "ask_low", "ask_close", "updates",
    ]
    assert body["bars"] == [[1791792000, 8.40, 8.52, 8.36, 8.45, 8.45, 8.57, 8.41, 8.50, 3]]
    call = ctx.queries.calls[-1]
    assert (call[0], call[1], call[4]) == ("price_bars", 2, 60)


def test_bars_are_ten_seconds_long_unless_asked_otherwise(client, ctx):
    client.get("/api/data/assets/saxo/2/bars?from=2026-10-12")

    assert ctx.queries.calls[-1][4] == 10


@pytest.mark.parametrize("interval", [20, 45, 90, 14_400, 86_400])
def test_bars_of_any_length_from_ten_seconds_to_a_day_are_served(client, ctx, interval):
    response = client.get(f"/api/data/assets/saxo/2/bars?from=2026-10-12&interval={interval}")

    assert response.status_code == 200 and response.json()["interval"] == interval
    assert ctx.queries.calls[-1][4] == interval


@pytest.mark.parametrize("query, reason", [
    ("interval=5", "The interval is a whole number of seconds from 10 to 86400"),
    ("interval=86401", "The interval is a whole number of seconds from 10 to 86400"),
    ("from=2026-10-12&to=2026-10-11", "The period ends before it starts"),
    ("from=2025-01-01&to=2026-10-11", "The period is longer than 366 days"),
])
def test_a_request_for_bars_that_makes_no_sense_is_refused_saying_why(client, query, reason):
    response = client.get(f"/api/data/assets/saxo/2/bars?{query}")

    assert response.status_code == 422
    assert response.json()["detail"] == reason


@pytest.mark.parametrize("query", ["from=yesterday", "interval=abc", "to=2026-13-01"])
def test_malformed_price_parameters_are_refused(client, query):
    assert client.get(f"/api/data/assets/saxo/2/bars?{query}").status_code == 422


def test_bars_of_an_instrument_that_was_never_recorded_are_not_found(client):
    response = client.get("/api/data/assets/saxo/99/bars?from=2026-10-12")

    assert response.status_code == 404
    assert client.get("/api/data/assets/saxo/99/context?from=2026-10-12").status_code == 404


def test_a_period_with_too_many_bars_asks_for_a_longer_interval(client, ctx):
    from src.admin.prices import MAX_BUCKETS

    # Ten-second bars up to the morning of the 12th, all within the period asked.
    first = 1791792000 - 10 * (MAX_BUCKETS + 1)
    ctx.queries.price_bar_rows = [[first + 10 * n, 1, 1, 1, 1, 1, 1, 1, 1, 0] for n in range(MAX_BUCKETS + 1)]

    response = client.get("/api/data/assets/saxo/2/bars?from=2026-10-01&to=2026-10-12")

    assert response.status_code == 422
    assert "choose a longer interval or a shorter period" in response.json()["detail"]
    # One more than the limit was asked of the database, not the whole period.
    assert ctx.queries.calls[-1][5] == MAX_BUCKETS + 1

    ctx.queries.price_bar_rows = ctx.queries.price_bar_rows[:MAX_BUCKETS]
    assert client.get("/api/data/assets/saxo/2/bars?from=2026-10-01&to=2026-10-12").status_code == 200


# Monday 12 Oct 2026, 08:00 in Paris.
RANGE_T0 = 1791784800


def flat_bars(*prices, start=RANGE_T0, step=10):
    """Ten-second bars that each hold one price."""
    return [(start + index * step, price, price, price, price) for index, price in enumerate(prices)]


def test_range_bars_end_when_the_price_has_left_the_range_asked_for(client, ctx):
    ctx.queries.price_series_rows = {2: flat_bars(8.40, 8.44, 8.52, 8.49)}

    response = client.get("/api/data/assets/saxo/2/range-bars?from=2026-10-12&size=0.10")

    assert response.status_code == 200
    body = response.json()
    assert body["instrument"]["id"] == 2
    assert (body["tick"], body["decimals"], body["ticks"], body["size"], body["auto"]) == (0.01, 2, 10, 0.1, False)
    assert (body["from"], body["to"], body["timezone"], body["side"]) == ("2026-10-12", "2026-10-12", "Europe/Paris", "bid")
    assert body["columns"] == [
        "t", "open", "high", "low", "close", "began", "ended", "complete", "after_pause", "index", "volume",
    ]
    assert body["bars"] == [
        [RANGE_T0, 8.4, 8.5, 8.4, 8.5, RANGE_T0, RANGE_T0 + 30, True, False, None, 0],
        [RANGE_T0 + 20, 8.5, 8.52, 8.49, 8.49, RANGE_T0 + 20, RANGE_T0 + 40, False, False, None, 0],
    ]
    assert body["under"] is None


def test_without_a_size_a_range_that_suits_the_period_is_chosen_and_said(client, ctx):
    # Between 8.00 and 10.50: a twenty-fifth is ten cents.
    ctx.queries.price_series_rows = {2: flat_bars(8.00, 9.20, 10.50, 9.90)}

    body = client.get("/api/data/assets/saxo/2/range-bars?from=2026-10-12").json()

    assert (body["auto"], body["ticks"], body["size"]) == (True, 10, 0.1)
    assert len(body["bars"]) > 20


def test_the_range_is_counted_in_the_steps_the_prices_are_quoted_in(client, ctx):
    ctx.queries.price_series_rows = {2: flat_bars(0.855, 0.861, 0.872)}

    body = client.get("/api/data/assets/saxo/2/range-bars?from=2026-10-12&size=0.01").json()

    assert (body["tick"], body["decimals"], body["ticks"], body["size"]) == (0.001, 3, 10, 0.01)
    assert body["bars"][0][1:5] == [0.855, 0.865, 0.855, 0.865]


def test_range_bars_of_the_ask_are_built_from_the_ask(client, ctx):
    ctx.queries.price_series_rows = {2: flat_bars(8.45, 8.60)}

    body = client.get("/api/data/assets/saxo/2/range-bars?from=2026-10-12&size=0.10&side=ask").json()

    assert body["side"] == "ask"
    assert [call[4] for call in ctx.queries.calls if call[0] == "price_series"] == ["ask"]


def test_the_index_under_a_turbo_comes_with_each_bar_as_it_was_when_the_bar_ended(client, ctx):
    ctx.queries.price_series_rows = {
        2: flat_bars(8.40, 8.44, 8.52, 8.49),
        1: flat_bars(25000.0, 25004.5, 25012.25, 25009.0),
    }

    body = client.get("/api/data/assets/saxo/2/range-bars?from=2026-10-12&size=0.10&under=1").json()

    assert body["under"]["id"] == 1
    # The first bar ended in the third ten seconds, the second is still going in the fourth.
    assert [bar[9] for bar in body["bars"]] == [25012.25, 25009.0]
    # The index is read on its bid, whatever side the turbo is shown on.
    assert [(call[1], call[4]) for call in ctx.queries.calls if call[0] == "price_series"] == [(2, "bid"), (1, "bid")]


def test_a_night_in_the_period_cuts_the_bar_and_flags_the_one_after(client, ctx):
    ctx.queries.price_series_rows = {2: flat_bars(8.40, 8.44) + flat_bars(9.10, start=RANGE_T0 + 3600)}

    body = client.get("/api/data/assets/saxo/2/range-bars?from=2026-10-12&size=0.10").json()

    assert [(bar[1], bar[4], bar[7], bar[8]) for bar in body["bars"]] == [(8.4, 8.44, False, False), (9.1, 9.1, False, True)]
    # The first one ended with its last ten seconds, not when the next began an hour later.
    assert [(bar[5], bar[6]) for bar in body["bars"]] == [(RANGE_T0, RANGE_T0 + 20), (RANGE_T0 + 3600, RANGE_T0 + 3610)]


def test_a_period_with_nothing_recorded_has_no_range_bar(client):
    body = client.get("/api/data/assets/saxo/2/range-bars?from=2026-10-12").json()

    assert body["bars"] == []
    assert (body["tick"], body["ticks"], body["auto"]) == (0.01, 1, True)


def test_the_ten_second_bars_are_read_a_page_at_a_time(client, ctx, monkeypatch):
    from src.admin.routers import data as data_router

    monkeypatch.setattr(data_router, "SERIES_PAGE", 2)
    ctx.queries.price_series_rows = {2: flat_bars(8.40, 8.41, 8.42, 8.43, 8.44)}

    body = client.get("/api/data/assets/saxo/2/range-bars?from=2026-10-12&size=1").json()

    assert body["bars"] == [[RANGE_T0, 8.4, 8.44, 8.4, 8.44, RANGE_T0, RANGE_T0 + 50, False, False, None, 0]]
    starts = [call[2].timestamp() for call in ctx.queries.calls if call[0] == "price_series"]
    # Each page starts just after the last bar of the one before; a short page ends the reading.
    assert starts[1:] == [RANGE_T0 + 11, RANGE_T0 + 31]


@pytest.mark.parametrize("query, reason", [
    ("from=2026-10-12&size=0.001", "The range is smaller than one step of these prices (0.01)"),
    ("from=2026-10-12&side=mid", "The side must be one of bid, ask"),
    ("from=2026-09-01&to=2026-10-12", "Range bars are built for periods of 31 days at most"),
    ("from=2026-10-12&to=2026-10-11", "The period ends before it starts"),
])
def test_a_request_for_range_bars_that_makes_no_sense_is_refused_saying_why(client, ctx, query, reason):
    ctx.queries.price_series_rows = {2: flat_bars(8.40, 8.44)}

    response = client.get(f"/api/data/assets/saxo/2/range-bars?{query}")

    assert response.status_code == 422
    assert response.json()["detail"] == reason


@pytest.mark.parametrize("query", ["size=0", "size=-1", "size=abc", "under=0", "ticks=0", "ticks=1.5"])
def test_malformed_range_parameters_are_refused(client, query):
    assert client.get(f"/api/data/assets/saxo/2/range-bars?{query}").status_code == 422


def test_range_bars_carry_the_quote_changes_of_their_ten_seconds_as_volume(client, ctx):
    ctx.queries.price_series_rows = {2: [
        (RANGE_T0 + index * 10, price, price, price, price, updates)
        for index, (price, updates) in enumerate([(8.40, 3), (8.44, 4), (8.52, 6), (8.49, 1)])
    ]}

    body = client.get("/api/data/assets/saxo/2/range-bars?from=2026-10-12&size=0.10").json()

    assert [bar[10] for bar in body["bars"]] == [3 + 4 + 6, 1]


def test_a_range_given_in_ticks_counts_steps_of_the_prices_read(client, ctx):
    ctx.queries.price_series_rows = {2: flat_bars(8.40, 8.44, 8.52, 8.49)}

    body = client.get("/api/data/assets/saxo/2/range-bars?from=2026-10-12&ticks=10").json()

    assert (body["ticks"], body["size"], body["auto"]) == (10, 0.1, False)
    both = client.get("/api/data/assets/saxo/2/range-bars?from=2026-10-12&ticks=10&size=0.1")
    assert both.status_code == 422 and both.json()["detail"] == "Give the range as a size or as ticks, not both"


def test_range_bars_of_an_instrument_or_an_index_that_was_never_recorded_are_not_found(client):
    assert client.get("/api/data/assets/saxo/99/range-bars?from=2026-10-12").status_code == 404
    assert client.get("/api/data/assets/saxo/2/range-bars?under=99&from=2026-10-12").status_code == 404


def test_a_range_that_gives_too_many_bars_asks_for_a_larger_one(client, ctx):
    # One cent bars over a climb of 150 euros: more than a chart is given.
    ctx.queries.price_series_rows = {2: flat_bars(8.00, 158.00)}

    response = client.get("/api/data/assets/saxo/2/range-bars?from=2026-10-12&size=0.01")

    assert response.status_code == 422
    assert response.json()["detail"] == "More than 12000 range bars in this period: choose a larger range or a shorter period"


def test_a_period_with_more_recorded_bars_than_can_be_read_is_refused(client, ctx, monkeypatch):
    from src.admin.routers import data as data_router

    monkeypatch.setattr(data_router, "RANGE_MAX_ROWS", 3)
    ctx.queries.price_series_rows = {2: flat_bars(8.40, 8.41, 8.42, 8.43)}

    response = client.get("/api/data/assets/saxo/2/range-bars?from=2026-10-12&size=0.10")

    assert response.status_code == 422
    assert "choose a shorter one" in response.json()["detail"]


def test_range_bars_after_a_moment_are_those_of_a_whole_answer_from_it(client, ctx):
    # A climb of 25 cents each ten seconds, each with its quote changes: two or three ten-cent bars in the same ten seconds.
    ctx.queries.price_series_rows = {2: [
        (RANGE_T0 + 10 * n, *([round(8.40 + 0.25 * n, 2)] * 4), n + 1) for n in range(30)
    ]}
    url = "/api/data/assets/saxo/2/range-bars?from=2026-10-12&size=0.10"
    whole = client.get(url).json()["bars"]

    for position in (1, 17, len(whole) - 1):
        tail = client.get(f"{url}&after={whole[position][0]}").json()
        # Their volumes too: ten seconds go to the first bar running at their start, which may come before.
        assert tail["bars"] == whole[position:]
        assert tail["cut"] is False
    assert client.get(f"{url}&after={whole[-1][0] + 1}").json()["bars"] == []


def test_newest_gives_the_last_bars_of_a_period_that_makes_too_many_rather_than_refusing_it(client, ctx, monkeypatch):
    from src.admin.routers import data as data_router

    monkeypatch.setattr(data_router, "MAX_BUCKETS", 5)
    ctx.queries.price_series_rows = {2: flat_bars(*[round(8.40 + 0.11 * n, 2) for n in range(20)])}
    url = "/api/data/assets/saxo/2/range-bars?from=2026-10-12&size=0.10"

    assert client.get(url).status_code == 422
    whole = client.get(f"{url}&after=0").json()["bars"]
    newest = client.get(f"{url}&newest=true").json()
    assert (newest["cut"], newest["bars"]) == (True, whole[-5:])
    assert client.get(f"{url}&newest=true&after={whole[-2][0]}").json()["bars"] == whole[-2:]

    # Built from the first day asked, a period is still refused past what is built at most.
    monkeypatch.setattr(data_router, "BUILT_RANGE_BARS", 8)
    refused = client.get(f"{url}&newest=true")
    assert refused.status_code == 422 and refused.json()["detail"].startswith("More than 8 range bars")


def test_newest_builds_the_bars_from_the_first_day_whose_bars_fit_and_says_which(client, ctx, monkeypatch):
    from src.admin.routers import data as data_router

    monkeypatch.setattr(data_router, "MAX_BUCKETS", 12)
    # About twenty bars on Monday the 12th, ten on Tuesday the 13th: together more than are given.
    climb = [round(8.40 + 0.11 * n, 2) for n in range(20)]
    ctx.queries.price_series_rows = {2: flat_bars(*climb) + flat_bars(*climb[:10], start=RANGE_T0 + 86_400)}

    both = client.get("/api/data/assets/saxo/2/range-bars?from=2026-10-12&to=2026-10-13&size=0.10&newest=true").json()

    assert (both["from"], both["to"], both["cut"]) == ("2026-10-13", "2026-10-13", False)
    # The bars of Tuesday alone, as a chart anchored there reads them again.
    tuesday = client.get("/api/data/assets/saxo/2/range-bars?from=2026-10-13&size=0.10").json()
    assert both["bars"] == tuesday["bars"] and len(both["bars"]) == 10
    # The series is read once, the days tried on it.
    assert len([call for call in ctx.queries.calls if call[0] == "price_series"]) == 2


def test_an_answer_with_rows_is_the_json_fastapi_would_have_written():
    from fastapi.encoders import jsonable_encoder

    from src.admin.routers.data import _with_rows

    answer = {
        "instrument": {"id": 2, "name": "NASDAQ 100 Turbo Long", "first_seen_at": datetime(2026, 10, 12, 8, tzinfo=timezone.utc)},
        "under": None,
        "from": date(2026, 10, 12),
        "tick": 0.01,
        "auto": False,
        "bars": [
            [RANGE_T0, 8.4, 8.5, 8.4, 8.5, RANGE_T0, RANGE_T0 + 30, True, False, None, 0],
            [RANGE_T0 + 20, 1e-07, 25012.25, -0.0, 3, 4, 5, False, True, 25009.0, 7],
        ],
    }

    response = _with_rows(answer, "bars")

    assert response.media_type == "application/json"
    assert json.loads(response.body) == json.loads(json.dumps(jsonable_encoder(answer)))
    # What is not a row is written as FastAPI writes it: a moment keeps its own form.
    assert '"first_seen_at":"2026-10-12T08:00:00+00:00"' in response.body.decode()
    assert json.loads(_with_rows({"bars": []}, "bars").body) == {"bars": []}


def test_a_big_answer_goes_out_compressed_and_a_small_one_as_it_is(client, ctx):
    # A climb of eleven cents each ten seconds: a bar of ten cents each time, some tens of kilobytes of them.
    ctx.queries.price_series_rows = {2: flat_bars(*[round(8.40 + 0.11 * n, 2) for n in range(400)])}

    big = client.get("/api/data/assets/saxo/2/range-bars?from=2026-10-12&size=0.10", headers={"Accept-Encoding": "gzip"})

    assert big.headers["content-encoding"] == "gzip"
    assert len(big.json()["bars"]) > 300
    ctx.queries.price_series_rows = {2: flat_bars(8.40, 8.44)}
    small = client.get("/api/data/assets/saxo/2/range-bars?from=2026-10-12&size=0.10", headers={"Accept-Encoding": "gzip"})
    assert "content-encoding" not in small.headers


def test_a_stream_and_the_mcp_endpoint_are_never_compressed():
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route

    from src.admin.app import CompressedAnswers

    async def big(_):
        return PlainTextResponse("x" * 10_000)

    app = Starlette(routes=[Route("/api/big", big), Route("/mcp", big)])
    app.add_middleware(CompressedAnswers)
    with TestClient(app) as bare:
        assert bare.get("/api/big", headers={"Accept-Encoding": "gzip"}).headers.get("content-encoding") == "gzip"
        # The compressing middleware holds an answer's headers back until its first bytes: a stream would wait.
        as_stream = bare.get("/api/big", headers={"Accept-Encoding": "gzip", "Accept": "text/event-stream"})
        assert "content-encoding" not in as_stream.headers
        assert "content-encoding" not in bare.get("/mcp", headers={"Accept-Encoding": "gzip"}).headers


def test_context_of_a_turbo_gives_its_trades_and_the_signals_of_its_indice(client, ctx):
    ctx.queries.journal = [
        {**JOURNAL_ENTRY, "signal_uuid": "b", "indice": "us100", "decided_at": "2026-10-12T09:00:00+00:00"},
        {**JOURNAL_ENTRY, "signal_uuid": "c", "indice": "germany40", "decided_at": "2026-10-12T08:30:00+00:00"},
        {**JOURNAL_ENTRY, "signal_uuid": "a", "indice": "n/a", "action": "close-position",
         "decided_at": "2026-10-12T08:00:00+00:00"},
    ]

    response = client.get("/api/data/assets/saxo/2/context?from=2026-10-12")

    assert response.status_code == 200
    body = response.json()
    assert body["instrument"]["id"] == 2
    assert [trade["position_id"] for trade in body["trades"]] == [CLOSED_POSITION["position_id"]]
    # Its own indice and the signals that name none, oldest first; another indice's are left out.
    assert [signal["signal_uuid"] for signal in body["signals"]] == ["a", "b"]


def test_context_lists_what_was_not_recorded_during_trading_hours(client, ctx):
    # Monday 12 Oct 2026, long past: the recorder ran from 08:00 to 22:00 in Paris, except five minutes.
    def rows(start, end):
        moment, found = start, []
        while moment < end:
            found.append({"ts": moment, "feed_ok": True})
            moment += timedelta(seconds=10)
        return found

    day = datetime(2026, 10, 12, tzinfo=timezone.utc)
    ctx.queries.price_coverage_rows = (
        rows(day.replace(hour=6), day.replace(hour=9)) + rows(day.replace(hour=9, minute=5), day.replace(hour=20))
    )

    with patch_now(datetime(2026, 10, 20, tzinfo=timezone.utc)):
        body = client.get("/api/data/assets/saxo/2/context?from=2026-10-12").json()

    assert body["gaps"] == [
        {"start": "2026-10-12T09:00:00+00:00", "end": "2026-10-12T09:05:00+00:00", "seconds": 300, "reason": "down"},
    ]


def test_gaps_are_not_worked_out_for_a_period_too_long_to_read_line_by_line(client, ctx):
    body = client.get("/api/data/assets/saxo/2/context?from=2026-08-01&to=2026-10-12").json()

    assert body["gaps"] is None
    assert not any(call[0] == "price_coverage" for call in ctx.queries.calls)


def _run(instrument_id, first, last, **figures):
    from src.admin.prices import Run

    start, end = (int(datetime(2026, 10, 12, *clock, tzinfo=timezone.utc).timestamp()) for clock in (first, last))
    return Run(
        instrument_id, start, end - 10, bars=(end - start) // 10, updates=figures.get("updates", 0), without_ask=0,
        delayed_minutes=0, high=figures.get("high", 8.0), last_bid=figures.get("last_bid", 7.5),
    )


def test_availability_says_which_instruments_saxo_left_without_a_quote(client, ctx):
    # Monday 12 Oct 2026, 08:00 to 22:00 in Paris. A second turbo, quoted all day, and the first with twelve
    # minutes missing in the morning; the recorder itself was down for five of them.
    ctx.queries.price_instruments_recorded = [PRICE_INDEX, PRICE_TURBO, {**PRICE_TURBO, "id": 3}]
    ctx.queries.price_run_rows = [
        _run(1, (13, 30), (20, 0)),
        _run(2, (6, 0), (9, 0), updates=900), _run(2, (9, 12), (20, 0), updates=3100),
        _run(3, (6, 0), (20, 0)),
    ]
    day = datetime(2026, 10, 12, tzinfo=timezone.utc)
    ctx.queries.price_coverage_rows = [
        {"ts": day.replace(hour=6) + timedelta(seconds=10 * n), "feed_ok": True}
        for n in range(14 * 360)
        if not 9 * 360 <= n + 6 * 360 < 9 * 360 + 30
    ]

    with patch_now(datetime(2026, 10, 20, tzinfo=timezone.utc)):
        response = client.get("/api/data/providers/saxo/availability?from=2026-10-12")

    assert response.status_code == 200
    body = response.json()
    assert (body["from"], body["to"], body["timezone"], body["max_days"]) == ("2026-10-12", "2026-10-12", "Europe/Paris", 7)
    turbo = body["items"]["2"]
    assert (turbo["bars"], turbo["updates"], turbo["issues"]) == (5040 - 72, 4000, ["holes"])
    assert (turbo["holes"], turbo["hole_seconds"], turbo["missing_seconds"], turbo["stops"]) == (1, 420, 420, [])
    assert turbo["stretches"] == [
        {"start": "2026-10-12T09:05:00+00:00", "end": "2026-10-12T09:12:00+00:00", "seconds": 420, "reason": "unquoted"},
    ]
    assert (turbo["first_bar"], turbo["last_bar"]) == ("2026-10-12T06:00:00+00:00", "2026-10-12T19:59:50+00:00")
    assert body["items"]["3"]["issues"] == []
    # The index, quoted in its own hours only, is not said to have stopped.
    assert body["items"]["1"]["issues"] == []


def test_availability_is_not_worked_out_for_a_period_too_long_to_read_bar_by_bar(client, ctx):
    body = client.get("/api/data/providers/saxo/availability?from=2026-10-01&to=2026-10-12").json()

    assert body["items"] is None and body["max_days"] == 7
    assert not any(call[0] == "price_runs" for call in ctx.queries.calls)
    # Seven days are.
    assert client.get("/api/data/providers/saxo/availability?from=2026-10-06&to=2026-10-12").json()["items"] == {}


def test_a_period_with_too_many_interruptions_is_refused_rather_than_half_read(client, ctx):
    from src.admin.prices import MAX_RUNS

    ctx.queries.price_run_rows = [_run(2, (6, 0), (6, 1))] * (MAX_RUNS + 1)

    response = client.get("/api/data/providers/saxo/availability?from=2026-10-12")

    assert response.status_code == 422
    assert "shorter" in response.json()["detail"]
    # One more than the limit was asked for: enough to know, without reading the rest.
    assert next(call[3] for call in ctx.queries.calls if call[0] == "price_runs") == MAX_RUNS + 1


# ── the Data pages: providers and assets ──


def _history_day(prices_root, label, day, count=6, price=25000.0, symbol="USATECHIDXUSD", first_hour=6):
    """A day of a Dukascopy history, written as the downloader writes it."""
    from src.price_recorder.bars import Bar
    from src.price_history import dukascopy

    start = int(datetime(day.year, day.month, day.day, first_hour, tzinfo=timezone.utc).timestamp())
    bars = [
        Bar(key="index", start=start + 10 * n, bid_open=price + n, bid_high=price + n, bid_low=price + n,
            bid_close=price + n, ask_open=price + n + 1.5, ask_high=price + n + 1.5, ask_low=price + n + 1.5,
            ask_close=price + n + 1.5, updates=2, delayed_minutes=0)
        for n in range(count)
    ]
    dukascopy.write_day_file(dukascopy.day_path(str(prices_root), label, day), bars, symbol=symbol, label=label)


@pytest.fixture
def history(ctx, tmp_path):
    from datetime import date

    from src.admin.data import PriceHistory

    ctx.history = PriceHistory(str(tmp_path / "prices"))
    _history_day(tmp_path / "prices", "us100", date(2026, 10, 12))
    _history_day(tmp_path / "prices", "us100", date(2026, 10, 13), count=3, price=25100.0)
    return ctx.history


def _provider(body, provider_id):
    return next(item for item in body["items"] if item["id"] == provider_id)


def test_the_providers_are_saxo_dukascopy_and_capital_com(client, ctx):
    body = client.get("/api/data/providers").json()

    saxo, dukascopy, capital = body["items"]
    assert (saxo["id"], saxo["kind"], dukascopy["id"], dukascopy["kind"]) == ("saxo", "live", "dukascopy", "download")
    # Switched off in the configuration, but two instruments were recorded: it is listed.
    assert (saxo["status"], saxo["detail"], saxo["enabled"], saxo["active"]) == (
        "disabled", "Switched off in the configuration", False, True,
    )
    assert (saxo["assets"], saxo["first_t"], saxo["last_t"]) == (2, 1791784800, 1791842390)
    assert (saxo["first_day"], saxo["last_day"]) == ("2026-10-12", "2026-10-12")
    # No download service and nothing fetched: offered to be added.
    assert (dukascopy["status"], dukascopy["enabled"], dukascopy["active"], dukascopy["assets"]) == ("disabled", False, False, 0)
    # Switched off and never run: offered to be added.
    assert (capital["id"], capital["kind"], capital["status"], capital["active"], capital["assets"]) == (
        "capital", "live", "disabled", False, 0,
    )
    assert [(entry["id"], entry["available"]) for entry in body["catalog"]] == [
        ("saxo", True), ("dukascopy", True), ("capital", True),
    ]


def test_a_recorder_switched_on_and_writing_is_a_provider_at_work(config_path, tmp_path):
    ctx = _with_recorder(config_path, tmp_path)
    ctx.queries.recorder_status = _bucket(seconds_ago=14)

    with make_client(ctx, tmp_path / "no-interface") as client:
        client.post("/api/session", json={"password": PASSWORD}, headers=SAME_ORIGIN)
        saxo = _provider(client.get("/api/data/providers").json(), "saxo")

    assert (saxo["status"], saxo["detail"], saxo["enabled"]) == ("ok", "Recording 43 instruments", True)


def test_a_recorder_switched_off_on_disk_records_until_it_is_restarted(config_path, tmp_path):
    ctx = _with_recorder(config_path, tmp_path, enabled=False)
    ctx.queries.recorder_status = _bucket(seconds_ago=14)

    with make_client(ctx, tmp_path / "no-interface") as client:
        client.post("/api/session", json={"password": PASSWORD}, headers=SAME_ORIGIN)
        saxo = _provider(client.get("/api/data/providers").json(), "saxo")

    assert (saxo["status"], saxo["detail"]) == ("warning", "Switched off in the configuration: it records until it is restarted")


def test_the_providers_are_listed_while_the_database_is_down(client, ctx):
    ctx.queries.failing = True

    saxo = _provider(client.get("/api/data/providers").json(), "saxo")

    assert (saxo["status"], saxo["detail"], saxo["assets"]) == ("unknown", "The database is unavailable", None)


def test_the_dukascopy_provider_says_what_was_fetched_and_what_can_be_added(client, ctx, history, downloads):
    body = client.get("/api/data/providers/dukascopy").json()

    assert (body["status"], body["detail"], body["enabled"], body["active"], body["assets"]) == (
        "ok", "Kept up to date", True, True, 1,
    )
    assert (body["first_day"], body["last_day"]) == ("2026-10-12", "2026-10-13")
    (instrument,) = body["instruments"]
    assert (instrument["label"], instrument["symbol"], instrument["built_in"], instrument["days"]) == (
        "us100", "USATECHIDXUSD", True, 2,
    )
    assert (instrument["first_day"], instrument["last_day"]) == ("2026-10-12", "2026-10-13")
    assert instrument["bytes"] > 0
    assert [entry["symbol"] for entry in body["offered"]] == ["USA500IDXUSD", "DEUIDXEUR"]


def test_the_saxo_provider_says_how_it_is_set_and_what_it_missed_today(client, ctx):
    body = client.get("/api/data/providers/saxo").json()

    assert body["settings"] == {
        "enabled": False, "record_volume": False, "max_instruments": 150, "refresh_interval_seconds": 300,
        "refresh_rate_ms": 1000, "retention_months": 0, "alert_after_seconds": 300, "instruments": [],
    }
    assert body["gaps_today"] is not None
    assert body["restart_required"] is False
    assert client.get("/api/data/providers/elsewhere").status_code == 404


# ── the recorder's configuration, from its provider ──


def test_the_recorder_section_is_read_with_what_the_form_needs(client, ctx):
    body = client.get("/api/data/providers/saxo/config").json()

    assert body["section"] == {}
    assert body["defaults"]["max_instruments"] == 150 and body["defaults"]["instruments"] == []
    assert body["asset_types"] == ["StockIndex", "CfdOnIndex"]
    assert body["indices"] == sorted(body["indices"]) and body["editable"] is True
    assert body["revision"] == ctx.config_editor.load()["revision"]


def test_the_recorder_section_is_saved_alone_and_said_in_the_audit_trail(client, ctx, config_path):
    revision = client.get("/api/data/providers/saxo/config").json()["revision"]
    section = {"enabled": True, "record_volume": True, "instruments": [{"label": "dax", "uic": 4913, "asset_type": "CfdOnIndex"}]}

    response = client.put("/api/data/providers/saxo/config", json={"section": section, "revision": revision}, headers=SAME_ORIGIN)

    assert response.status_code == 200
    assert response.json()["restart_required"] is True
    assert json.load(open(config_path))["trade"]["config"]["price_recorder"] == section
    assert client.get("/api/data/providers/saxo/config").json()["section"] == section
    assert ctx.audit_log.read(1)[0]["action"] == "price recorder configuration saved"
    assert client.get("/api/session").json()["restart_required"] is True


@pytest.mark.parametrize("section, status, reason", [
    ({"enabled": True, "max_instruments": 0}, 422, "trade.config.price_recorder.max_instruments"),
    ({"enabled": True, "instruments": [{"label": "dax", "uic": 1, "asset_type": "CfdOnIndex"},
                                       {"label": "dax", "uic": 2, "asset_type": "CfdOnIndex"}]}, 422, "Two instruments are labelled 'dax'"),
    ({"enabled": True, "instruments": [{"label": "a", "uic": 1, "asset_type": "CfdOnIndex"},
                                       {"label": "b", "uic": 1, "asset_type": "CfdOnIndex"}]}, 422, "UIC 1 as CfdOnIndex is listed twice"),
])
def test_a_recorder_section_that_would_not_work_is_refused_saying_why(client, ctx, config_path, section, status, reason):
    before = open(config_path).read()
    revision = client.get("/api/data/providers/saxo/config").json()["revision"]

    response = client.put("/api/data/providers/saxo/config", json={"section": section, "revision": revision}, headers=SAME_ORIGIN)

    assert response.status_code == status
    assert reason in response.json()["detail"]
    assert open(config_path).read() == before


def test_a_recorder_section_is_not_saved_over_a_file_changed_since(client, ctx, config_path):
    response = client.put(
        "/api/data/providers/saxo/config", json={"section": {"enabled": True}, "revision": "stale"}, headers=SAME_ORIGIN,
    )

    assert response.status_code == 409


def test_the_recorder_section_is_not_saved_when_editing_is_switched_off(config_path, tmp_path):
    ctx = make_ctx(config_path, tmp_path, config_edit_enabled=False)

    with make_client(ctx, tmp_path / "no-interface") as client:
        client.post("/api/session", json={"password": PASSWORD}, headers=SAME_ORIGIN)
        response = client.put(
            "/api/data/providers/saxo/config", json={"section": {"enabled": True}, "revision": "x"}, headers=SAME_ORIGIN,
        )
        assert client.get("/api/data/providers/saxo/config").json()["editable"] is False

    assert response.status_code == 403


# ── the Dukascopy instruments ──


def test_a_dukascopy_instrument_is_added_and_removed(client, ctx, history, downloads):
    added = client.post(
        "/api/data/providers/dukascopy/instruments", json={"symbol": "DEUIDXEUR", "label": "de40"}, headers=SAME_ORIGIN,
    )

    assert (added.status_code, added.json()["label"], added.json()["built_in"]) == (201, "de40", False)
    listed = client.get("/api/data/providers/dukascopy/instruments").json()
    assert [entry["label"] for entry in listed["items"]] == ["us100", "de40"]
    assert "DEUIDXEUR" not in [entry["symbol"] for entry in listed["offered"]]

    assert client.delete("/api/data/providers/dukascopy/instruments/de40", headers=SAME_ORIGIN).status_code == 204
    assert [entry["label"] for entry in client.get("/api/data/providers/dukascopy/instruments").json()["items"]] == ["us100"]


@pytest.mark.parametrize("method, path, body, status", [
    ("POST", "/api/data/providers/dukascopy/instruments", {"symbol": "EURUSD", "label": "fx"}, 422),
    ("POST", "/api/data/providers/dukascopy/instruments", {"symbol": "DEUIDXEUR", "label": "us100"}, 422),
    ("POST", "/api/data/providers/dukascopy/instruments", {"symbol": "DEUIDXEUR", "label": "De 40"}, 422),
    ("DELETE", "/api/data/providers/dukascopy/instruments/us100", None, 422),
    ("DELETE", "/api/data/providers/dukascopy/instruments/de40", None, 404),
    ("DELETE", "/api/data/providers/dukascopy/instruments/..%2Fus100", None, 404),
])
def test_a_dukascopy_instrument_that_cannot_be_fetched_rightly_is_refused(client, ctx, history, downloads, method, path, body, status):
    response = client.request(method, path, json=body, headers=SAME_ORIGIN)

    assert response.status_code == status


def test_dukascopy_instruments_are_not_changed_while_downloads_are_switched_off(config_path, tmp_path):
    from src.admin.data import PriceHistory

    ctx = make_ctx(config_path, tmp_path, backtests_enabled=False)
    ctx.history = PriceHistory(str(tmp_path / "prices"))

    with make_client(ctx, tmp_path / "no-interface") as client:
        client.post("/api/session", json={"password": PASSWORD}, headers=SAME_ORIGIN)
        response = client.post(
            "/api/data/providers/dukascopy/instruments", json={"symbol": "DEUIDXEUR", "label": "de40"}, headers=SAME_ORIGIN,
        )

    assert response.status_code == 403


# ── assets ──


def test_every_asset_of_every_provider_is_listed(client, ctx, history):
    body = client.get("/api/data/assets").json()

    assert (body["timezone"], body["seen"], body["truncated"], body["saxo_error"]) == ("Europe/Paris", "all", False, None)
    assert [(item["ref"], item["status"]) for item in body["items"]] == [
        ("saxo/1", "recording"), ("saxo/2", "recording"), ("dukascopy/us100", "downloaded"),
    ]
    turbo = body["items"][1]
    assert (turbo["provider"], turbo["key"], turbo["barrier"], turbo["last_bid"]) == ("saxo", "2", 24150.0, 8.45)
    assert body["items"][2]["days"] == 2
    assert next(call for call in ctx.queries.calls if call[0] == "price_assets")[:4] == ("price_assets", 5001, None, None)


def test_an_instrument_no_longer_named_by_the_recorder_has_ended(client, ctx):
    ctx.queries.price_asset_extras = {2: {"last_seen_at": datetime.now(timezone.utc) - timedelta(days=3)}}

    statuses = [item["status"] for item in client.get("/api/data/assets").json()["items"]]

    assert statuses == ["recording", "ended"]


def test_the_assets_seen_lately_are_those_recorded_since_the_start_of_a_day(client, ctx, history):
    with patch_now(datetime(2026, 10, 13, 12, tzinfo=timezone.utc)):
        body = client.get("/api/data/assets?seen=today").json()

    since = next(call[2] for call in ctx.queries.calls if call[0] == "price_assets")
    # The start of 13 October in Paris.
    assert since.astimezone(timezone.utc) == datetime(2026, 10, 12, 22, tzinfo=timezone.utc)
    # The Dukascopy history's last bar is on the 13th, early: still seen today.
    assert [item["ref"] for item in body["items"] if item["provider"] == "dukascopy"] == ["dukascopy/us100"]
    assert client.get("/api/data/assets?seen=forever").status_code == 422


def test_the_files_are_listed_while_the_database_is_down(client, ctx, history):
    ctx.queries.failing = True

    body = client.get("/api/data/assets").json()

    assert [item["ref"] for item in body["items"]] == ["dukascopy/us100"]
    assert "database is unavailable" in body["saxo_error"]


def test_a_list_longer_than_shown_says_so(client, ctx, monkeypatch):
    from src.admin.routers import data as data_router

    monkeypatch.setattr(data_router, "MAX_ASSETS", 1)

    body = client.get("/api/data/assets").json()

    assert (len(body["items"]), body["truncated"]) == (1, True)


def test_a_turbo_comes_with_its_days_how_it_was_recorded_and_its_index(client, ctx):
    body = client.get("/api/data/assets/saxo/2").json()

    assert body["asset"]["ref"] == "saxo/2" and body["asset"]["status"] == "recording"
    assert (body["first_day"], body["last_day"], body["timezone"]) == ("2026-10-12", "2026-10-12", "Europe/Paris")
    assert body["index"]["ref"] == "saxo/1"
    # Its index on its last day, unless another period is asked for.
    _, indice, start, end = next(call for call in ctx.queries.calls if call[0] == "price_index_of")
    assert indice == "us100" and start.astimezone(timezone.utc) == datetime(2026, 10, 11, 22, tzinfo=timezone.utc)
    assert body["recording"]["retention_months"] == 0
    assert body["max_days"] == {"chart": 366, "range": 31, "statistics": 31, "availability": 7, "gaps": 31}


def test_a_dukascopy_history_comes_with_its_files(client, ctx, history):
    body = client.get("/api/data/assets/dukascopy/us100").json()

    assert (body["asset"]["symbol"], body["asset"]["days"], body["index"]) == ("USATECHIDXUSD", 2, None)
    recording = body["recording"]
    assert (recording["label"], recording["fetchable"], recording["days"], recording["price_scale"]) == ("us100", True, 2, 1000)
    assert (recording["first_file"], recording["last_file"]) == ("2026-10-12", "2026-10-13")
    assert body["max_days"]["chart"] == 31


def test_a_dukascopy_history_opens_on_the_day_of_its_last_file(client, ctx, tmp_path):
    from datetime import date

    from src.admin.data import PriceHistory

    ctx.history = PriceHistory(str(tmp_path / "prices"))
    # Its last hour of UTC is past midnight in Paris: two hours of the next day, outside trading.
    _history_day(tmp_path / "prices", "us100", date(2026, 10, 13), count=360, first_hour=23)

    body = client.get("/api/data/assets/dukascopy/us100").json()

    assert body["asset"]["last_t"] == int(datetime(2026, 10, 13, 23, 59, 50, tzinfo=timezone.utc).timestamp())
    assert (body["first_day"], body["last_day"]) == ("2026-10-13", "2026-10-13")
    assert (body["asset"]["first_day"], body["asset"]["last_day"]) == ("2026-10-13", "2026-10-13")


@pytest.mark.parametrize("path", [
    "/api/data/assets/saxo/99", "/api/data/assets/saxo/abc", "/api/data/assets/saxo/0",
    "/api/data/assets/dukascopy/de40", "/api/data/assets/dukascopy/US100", "/api/data/assets/capital/1",
    "/api/data/assets/capital/us100", "/api/data/assets/capital/US100/bars",
    "/api/data/assets/saxo/99/bars", "/api/data/assets/dukascopy/de40/bars", "/api/data/assets/elsewhere/1/statistics",
])
def test_an_asset_that_does_not_exist_is_not_found(client, ctx, history, path):
    assert client.get(path).status_code == 404


def test_the_bars_of_a_recorded_turbo_are_read_from_the_recorder_s_tables(client, ctx):
    body = client.get("/api/data/assets/saxo/2/bars?from=2026-10-12&interval=60").json()

    assert body["instrument"]["ref"] == "saxo/2"
    assert body["bars"] == [[1791792000, 8.40, 8.52, 8.36, 8.45, 8.45, 8.57, 8.41, 8.50, 3]]
    assert ctx.queries.calls[-1][:2] == ("price_bars", 2) and ctx.queries.calls[-1][4] == 60


def test_the_bars_of_a_dukascopy_history_are_read_from_its_files(client, ctx, history):
    body = client.get("/api/data/assets/dukascopy/us100/bars?from=2026-10-12&interval=60").json()

    assert body["instrument"]["ref"] == "dukascopy/us100"
    # 08:00 in Paris, six bars of ten seconds merged into one minute.
    assert body["bars"] == [[1791784800, 25000.0, 25005.0, 25000.0, 25005.0, 25001.5, 25006.5, 25001.5, 25006.5, 12]]
    assert len(client.get("/api/data/assets/dukascopy/us100/bars?from=2026-10-12&to=2026-10-13").json()["bars"]) == 9


def test_a_dukascopy_history_is_read_a_month_at_a_time(client, ctx, history):
    response = client.get("/api/data/assets/dukascopy/us100/bars?from=2026-09-01&to=2026-10-12&interval=3600")

    assert response.status_code == 422
    assert response.json()["detail"] == "The Dukascopy history is read 31 days at a time at most"


def test_range_bars_of_a_dukascopy_history_are_built_from_its_files(client, ctx, history):
    body = client.get("/api/data/assets/dukascopy/us100/range-bars?from=2026-10-12&size=2&side=ask").json()

    assert (body["side"], body["tick"], body["ticks"], body["under"]) == ("ask", 0.01, 200, None)
    assert body["bars"][0][1:5] == [25001.5, 25003.5, 25001.5, 25003.5]


def test_the_context_of_a_dukascopy_history_is_the_signals_of_its_indice(client, ctx, history):
    ctx.queries.journal = [
        {**JOURNAL_ENTRY, "signal_uuid": "b", "indice": "us100", "decided_at": "2026-10-12T09:00:00+00:00"},
        {**JOURNAL_ENTRY, "signal_uuid": "c", "indice": "germany40", "decided_at": "2026-10-12T08:30:00+00:00"},
    ]

    body = client.get("/api/data/assets/dukascopy/us100/context?from=2026-10-12").json()

    assert [signal["signal_uuid"] for signal in body["signals"]] == ["b"]
    assert (body["gaps"], body["volume"]) == (None, None)
    assert [trade["position_id"] for trade in body["trades"]] == [CLOSED_POSITION["position_id"]]


def test_the_context_of_a_dukascopy_history_does_without_the_database(client, ctx, history):
    ctx.queries.failing = True

    body = client.get("/api/data/assets/dukascopy/us100/context?from=2026-10-12").json()

    assert body["trades"] == []


def test_the_context_of_a_turbo_comes_with_the_volume_saxo_counted(client, ctx):
    ctx.queries.price_volume_by_day = {2: {"2026-10-12": 1200.0, "2026-10-13": 300.0}}

    body = client.get("/api/data/assets/saxo/2/context?from=2026-10-12&to=2026-10-13").json()

    assert body["volume"] == 1500.0
    assert client.get("/api/data/assets/saxo/1/context?from=2026-10-12").json()["volume"] is None


def test_the_statistics_of_a_turbo_are_read_a_page_at_a_time(client, ctx, monkeypatch):
    from src.admin.routers import data as data_router

    monkeypatch.setattr(data_router, "SERIES_PAGE", 2)
    ctx.queries.price_bar_rows = [
        [RANGE_T0 + 10 * n, 8.40 + n / 100, 8.40 + n / 100, 8.40 + n / 100, 8.40 + n / 100,
         8.45 + n / 100, 8.45 + n / 100, 8.45 + n / 100, 8.45 + n / 100, 1]
        for n in range(3)
    ]
    ctx.queries.price_volume_by_day = {2: {"2026-10-12": 1200.0}}

    body = client.get("/api/data/assets/saxo/2/statistics?from=2026-10-12").json()

    (day,) = body["days"]
    assert (day["day"], day["bars"], day["open"], day["close"], day["volume"]) == ("2026-10-12", 3, 8.4, 8.42, 1200.0)
    assert day["spread_average"] == pytest.approx(0.05)
    assert body["spread"] is True and body["spread_by_time"][0]["slot"] == "08:00"
    reads = [call for call in ctx.queries.calls if call[0] == "price_bars"]
    # Ten-second rows, a page at a time, each starting just after the last one read.
    assert [(call[4], call[5]) for call in reads] == [(10, 2), (10, 2)]
    assert reads[1][2].timestamp() == RANGE_T0 + 11


def test_the_recorder_s_index_has_no_spread_to_speak_of(client, ctx):
    body = client.get("/api/data/assets/saxo/1/statistics?from=2026-10-12").json()

    assert body["spread"] is False and body["spread_by_time"] == []
    assert body["days"][0]["spread_average"] is None


def test_the_statistics_of_a_dukascopy_history_are_read_from_its_files(client, ctx, history):
    body = client.get("/api/data/assets/dukascopy/us100/statistics?from=2026-10-12&to=2026-10-13").json()

    assert [(day["day"], day["bars"], day["open"], day["close"]) for day in body["days"]] == [
        ("2026-10-12", 6, 25000.0, 25005.0), ("2026-10-13", 3, 25100.0, 25102.0),
    ]
    assert body["spread"] is True and body["days"][0]["spread_average"] == pytest.approx(1.5)
    # Both days' bars, at 08:00 in Paris: the spread through the day is read from the same pages as the days.
    assert [(slot["slot"], slot["bars"]) for slot in body["spread_by_time"]] == [("08:00", 9)]


def test_statistics_are_worked_out_for_a_month_at_most(client, ctx):
    response = client.get("/api/data/assets/saxo/2/statistics?from=2026-09-01&to=2026-10-12")

    assert response.status_code == 422
    assert response.json()["detail"] == "Statistics are worked out for periods of 31 days at most"


class patch_now:
    """Freeze "now" for the data routes: the period of today, and the gaps that may still be filling."""

    def __init__(self, moment):
        self.moment = moment

    def __enter__(self):
        from unittest.mock import patch

        import src.admin.routers.data as data_router

        self._patch = patch.object(data_router, "_now", lambda: self.moment)
        self._patch.start()

    def __exit__(self, *exc_info):
        self._patch.stop()


class RunningAdmin:
    """The admin served on a real socket: what the in-memory test client cannot show about a stream."""

    def __init__(self, ctx, static_dir, **server_options):
        import socket
        import threading

        import uvicorn

        with socket.socket() as free_port:
            free_port.bind(("127.0.0.1", 0))
            port = free_port.getsockname()[1]
        self.base_url = f"http://127.0.0.1:{port}"
        app = create_app(ctx, static_dir=str(static_dir))
        self.server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", **server_options)
        )
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        assert _eventually(lambda: self.server.started), "the server did not start"
        return self

    def __exit__(self, *exc_info):
        self.server.should_exit = True
        self.thread.join(timeout=10)

    @contextmanager
    def signed_in_client(self):
        import httpx

        with httpx.Client(base_url=self.base_url, timeout=5) as client:
            signed_in = client.post("/api/session", json={"password": PASSWORD}, headers={"Origin": self.base_url})
            assert signed_in.status_code == 204
            yield client


def _eventually(condition, seconds=5.0):
    import time

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


def _open_event_stream(client):
    stream = client.stream("GET", "/api/events")
    lines = stream.__enter__().iter_lines()
    while next(lines) != "event: connected":
        pass
    # Kept for as long as the stream is: an iterator that is let go closes the
    # connection under it, and the server then sees the browser leave before
    # the test says it does.
    stream.lines = lines
    return stream


@pytest.fixture
def plain_http_ctx(config_path, tmp_path):
    # Over plain HTTP on a local socket, a Secure cookie would not come back.
    return make_ctx(config_path, tmp_path, cookie_secure=False)


def test_a_browser_that_leaves_the_event_stream_is_no_longer_subscribed(plain_http_ctx, tmp_path):
    from unittest.mock import patch

    ctx = plain_http_ctx
    with patch("src.admin.routers.system.EVENT_POLL_SECONDS", 0.05), \
            RunningAdmin(ctx, tmp_path / "no-interface") as admin, admin.signed_in_client() as client:
        stream = _open_event_stream(client)
        assert ctx.events.has_subscribers

        # The tab is closed. Nothing is published and no keep-alive is due:
        # the server has to find out by itself.
        stream.__exit__(None, None, None)

        assert _eventually(lambda: not ctx.events.has_subscribers), "the closed stream is still subscribed"


def test_a_page_left_open_does_not_keep_the_server_from_stopping(plain_http_ctx, tmp_path):
    ctx = plain_http_ctx
    with RunningAdmin(ctx, tmp_path / "no-interface", timeout_graceful_shutdown=1) as admin, \
            admin.signed_in_client() as client:
        stream = _open_event_stream(client)

        admin.server.should_exit = True

        assert _eventually(lambda: not admin.thread.is_alive(), seconds=8), "the server is still running"
        assert not ctx.events.has_subscribers
        try:
            stream.__exit__(None, None, None)
        except Exception:
            # The server closed it first.
            pass


def test_streamed_and_plain_responses_all_carry_the_security_headers(plain_http_ctx, tmp_path):
    from unittest.mock import patch

    with patch("src.admin.routers.system.EVENT_POLL_SECONDS", 0.05), \
            RunningAdmin(plain_http_ctx, tmp_path / "no-interface") as admin, admin.signed_in_client() as client:
        with client.stream("GET", "/api/events") as stream:
            assert stream.headers["content-security-policy"].startswith("default-src 'self'")
            assert stream.headers["cache-control"] == "no-store"
            assert stream.headers["x-content-type-options"] == "nosniff"
        refused = client.get("/api/positions/unknown")
        assert refused.headers["x-frame-options"] == "DENY"
        assert refused.headers["cache-control"] == "no-store"


# ── downloads of history ──


class FakeDownloads:
    """Stands for the downloads of Dukascopy's history: remembers what it was asked, and fails when told to."""

    def __init__(self):
        self.asked = []
        self.failure = None

    def _answer(self, what, *arguments, answer=None):
        self.asked.append((what, *arguments))
        if self.failure is not None:
            raise self.failure
        return answer

    def coverage(self, first_day, last_day, label=None):
        return self._answer("coverage", first_day, last_day, label, answer={"days": []})

    def fetch_status(self):
        return {"state": "idle", "lines": []}

    def keep_status(self):
        return {"enabled": True, "days": 120, "state": "up_to_date", "missing": 0, "settled": 0, "failures": 0,
                "next_at": None, "limits": {"days": 730}}

    def start_fetch(self, first_day, last_day, *, label=None):
        return self._answer("start_fetch", first_day, last_day, label, answer={"state": "running"})

    def stop_fetch(self):
        return self._answer("stop_fetch", answer={"state": "running"})

    def set_keep(self, enabled, days):
        return self._answer("set_keep", enabled, days, answer={"enabled": enabled, "days": days})

    def storage(self):
        return {"days": 2, "bars_bytes": 1024, "fetched_bytes": 4096}

    async def keep_prices(self):
        await asyncio.Event().wait()

    async def close(self):
        pass


@contextmanager
def signed_in(ctx, tmp_path):
    with make_client(ctx, tmp_path / "no-interface") as client:
        assert client.post("/api/session", json={"password": PASSWORD}, headers=SAME_ORIGIN).status_code == 204
        yield client


@pytest.fixture
def downloads(ctx):
    ctx.downloads = FakeDownloads()
    return ctx.downloads


def test_the_session_says_whether_downloads_and_backtests_can_be_asked_for(client, ctx):
    assert client.get("/api/session").json()["backtests_enabled"] is False

    ctx.downloads = FakeDownloads()
    assert client.get("/api/session").json()["backtests_enabled"] is True
    ctx.settings.backtests_enabled = False
    assert client.get("/api/session").json()["backtests_enabled"] is False


def test_downloads_switched_off_or_not_wired_answer_as_such(client, ctx):
    assert client.get("/api/data/providers/dukascopy/coverage").status_code == 503

    ctx.downloads = FakeDownloads()
    ctx.settings.backtests_enabled = False
    response = client.post("/api/data/providers/dukascopy/fetch", json={"from": "2026-10-05", "to": "2026-10-09"}, headers=SAME_ORIGIN)

    assert response.status_code == 403
    assert ctx.downloads.asked == []


def test_the_history_panel_reads_the_last_months_unless_told_a_period(client, downloads):
    today = datetime.now(timezone.utc).date()

    assert client.get("/api/data/providers/dukascopy/coverage").json() == {"days": []}
    client.get("/api/data/providers/dukascopy/coverage?from=2026-09-01&to=2026-09-30")
    client.get("/api/data/providers/dukascopy/coverage?from=2026-09-01&to=2026-09-30&label=de40")

    assert downloads.asked == [
        ("coverage", today - timedelta(days=119), today, None),
        ("coverage", datetime(2026, 9, 1).date(), datetime(2026, 9, 30).date(), None),
        # Another Dukascopy instrument: the service checks the label before anything is made of it.
        ("coverage", datetime(2026, 9, 1).date(), datetime(2026, 9, 30).date(), "de40"),
    ]


def test_a_download_of_prices_is_started_and_stopped(client, downloads):
    started = client.post("/api/data/providers/dukascopy/fetch", json={"from": "2026-10-05", "to": "2026-10-09"}, headers=SAME_ORIGIN)
    stopped = client.post("/api/data/providers/dukascopy/fetch/stop", headers=SAME_ORIGIN)
    other = client.post(
        "/api/data/providers/dukascopy/fetch", json={"from": "2026-10-05", "to": "2026-10-09", "label": "de40"}, headers=SAME_ORIGIN,
    )

    assert (started.status_code, stopped.status_code, other.status_code) == (202, 200, 202)
    assert downloads.asked == [
        ("start_fetch", datetime(2026, 10, 5).date(), datetime(2026, 10, 9).date(), None), ("stop_fetch",),
        ("start_fetch", datetime(2026, 10, 5).date(), datetime(2026, 10, 9).date(), "de40"),
    ]


def test_the_service_is_told_to_keep_the_prices_up_to_date_or_to_stop(client, downloads):
    kept = client.put("/api/data/providers/dukascopy/keep", json={"enabled": True, "days": 250}, headers=SAME_ORIGIN)
    by_default = client.put("/api/data/providers/dukascopy/keep", json={"enabled": False}, headers=SAME_ORIGIN)

    assert (kept.status_code, kept.json()) == (200, {"enabled": True, "days": 250})
    assert by_default.json() == {"enabled": False, "days": 120}
    assert client.put("/api/data/providers/dukascopy/keep", json={"days": 30}, headers=SAME_ORIGIN).status_code == 422
    assert client.put("/api/data/providers/dukascopy/keep", json={"enabled": True, "days": 0}, headers=SAME_ORIGIN).status_code == 422
    assert downloads.asked == [("set_keep", True, 250), ("set_keep", False, 120)]


def test_what_the_download_service_refuses_is_answered_with_its_reason(client, downloads):
    from src.admin.errors import InvalidRequest, NotPossible

    downloads.failure = InvalidRequest("92 days at most are fetched at a time")
    response = client.post("/api/data/providers/dukascopy/fetch", json={"from": "2026-01-01", "to": "2026-10-09"}, headers=SAME_ORIGIN)
    assert (response.status_code, response.json()["detail"]) == (422, "92 days at most are fetched at a time")

    downloads.failure = NotPossible("No download is running")
    response = client.post("/api/data/providers/dukascopy/fetch/stop", headers=SAME_ORIGIN)
    assert (response.status_code, response.json()["detail"]) == (409, "No download is running")


def test_the_dukascopy_provider_says_how_much_disk_its_prices_take(client, ctx, history, downloads):
    body = client.get("/api/data/providers/dukascopy").json()

    assert body["storage"] == {"days": 2, "bars_bytes": 1024, "fetched_bytes": 4096}
    assert (body["fetch"]["state"], body["keep"]["state"]) == ("idle", "up_to_date")


# ── backtests ──

RUN = "20261012-213045-a1b2c3"


class FakeBacktests:
    """Stands for the backtest service: remembers what it was asked, and fails when told to."""

    max_period_days = 92

    def __init__(self):
        self.asked = []
        self.failure = None

    def _answer(self, what, *arguments, answer=None):
        self.asked.append((what, *arguments))
        if self.failure is not None:
            raise self.failure
        return answer

    def options(self, capital=None):
        return self._answer("options", capital, answer={"fields": []})

    def create(self, body, *, journal=None, positions=None, capital=None, **research):
        # Who asked through an agent (made_by, experiment...): never anything from the operator's form.
        assert all(value is None for value in research.values()), research
        return self._answer("create", body, journal, capital, positions, answer={"id": RUN, "status": {"state": "queued"}})

    def list_runs(self):
        return self._answer("list_runs", answer=[{"id": RUN}])

    def storage(self):
        return {"runs": {"count": 1, "bytes": 2048}, "former": {"count": 0, "bytes": 0}}

    def get(self, run_id):
        return self._answer("get", run_id, answer={"id": run_id})

    def update(self, run_id, name=None, tags=None):
        return self._answer("update", run_id, name, tags, answer={"id": run_id, "name": name, "tags": tags})

    def cancel(self, run_id):
        return self._answer("cancel", run_id, answer={"id": run_id})

    def delete(self, run_id):
        return self._answer("delete", run_id)

    def delete_many(self, run_ids):
        return self._answer("delete_many", run_ids, answer={"removed": run_ids, "kept": []})

    def delete_former(self):
        return self._answer("delete_former", answer={"removed": 3})

    def days(self, run_id):
        return self._answer("days", run_id, answer=[{"day": "2026-10-12"}])

    def signals(self, run_id, day=None, fate=None):
        return self._answer("signals", run_id, day, fate, answer=[{"id": 1}])

    def trades(self, run_id, day=None):
        return self._answer("trades", run_id, day, answer=[{"id": 1}])

    def breakdowns(self, run_id):
        return self._answer("breakdowns", run_id, answer={"groups": {}})

    def baseline(self, run_id):
        return self._answer("baseline", run_id, answer=None)

    def sweep(self, run_id):
        return self._answer("sweep", run_id, answer={"cells": []})

    def run_cell(self, run_id, cell):
        return self._answer("run_cell", run_id, cell, answer={"id": RUN})

    def edge(self, run_id):
        return self._answer("edge", run_id, answer={"horizons": []})

    def reconciliation(self, run_id):
        return self._answer("reconciliation", run_id, answer={"summary": {"paired": 0}})

    def paper(self):
        return self._answer("paper", answer={"rules": {}})

    def set_reference(self, run_id):
        return self._answer("set_reference", run_id, answer={"rules": {"r1": {"days": [], "reference": {"run_id": run_id}}}})

    def turbo_model(self):
        return self._answer("turbo_model", answer={"state": "idle", "measure": None})

    def measure_turbos(self, first, last):
        return self._answer("measure_turbos", first, last, answer={"state": "running"})

    def stress(self, run_id):
        return self._answer("stress", run_id, answer={"id": RUN, "stress_of": run_id})

    async def run(self):
        await asyncio.Event().wait()

    async def close(self):
        pass


@pytest.fixture
def backtests(ctx):
    ctx.backtests = FakeBacktests()
    return ctx.backtests


def test_backtests_switched_off_or_not_wired_answer_as_such(client, ctx):
    assert client.get("/api/backtests").status_code == 503

    ctx.backtests = FakeBacktests()
    ctx.settings.backtests_enabled = False
    response = client.post("/api/backtests", json={"from": "2026-10-13", "to": "2026-10-16"}, headers=SAME_ORIGIN)

    assert (response.status_code, response.json()["detail"]) == (403, "Backtests are disabled in the configuration")
    assert ctx.backtests.asked == []


class FakeResearch:
    def __init__(self):
        self.asked = []
        self.state = {"enabled": False, "keep_days": 30, "state": "off", "days": 0}

    def status(self):
        self.asked.append(("status",))
        return self.state

    def update(self, *, enabled=None, keep_days=None):
        from src.admin.errors import InvalidRequest

        self.asked.append(("update", enabled, keep_days))
        if keep_days is not None and not 1 <= keep_days <= 365:
            raise InvalidRequest("The days kept are 1 to 365")
        if enabled is not None:
            self.state = {**self.state, "enabled": enabled}
        return self.state


def test_the_research_files_are_switched_on_and_read_beside_the_runs(client, ctx, backtests):
    ctx.research = FakeResearch()

    assert client.get("/api/backtests/research").json()["enabled"] is False
    switched = client.put("/api/backtests/research", json={"enabled": True, "keep_days": 14}, headers=SAME_ORIGIN)
    refused = client.put("/api/backtests/research", json={"keep_days": 0}, headers=SAME_ORIGIN)

    assert switched.json()["enabled"] is True
    assert (refused.status_code, refused.json()["detail"]) == (422, "The days kept are 1 to 365")
    assert ctx.research.asked == [("status",), ("update", True, 14), ("update", None, 0)]


def test_the_research_files_follow_the_backtests_switch(client, ctx, backtests):
    ctx.research = FakeResearch()
    ctx.settings.backtests_enabled = False
    assert client.get("/api/backtests/research").status_code == 403
    ctx.settings.backtests_enabled = True
    ctx.research = None
    assert client.get("/api/backtests/research").status_code == 503


def test_a_run_starts_at_the_backtests_account_and_the_form_is_told_the_live_one(client, ctx, backtests):
    ctx.queries.balance = 12_345.6
    body = {"from": "2026-10-13", "to": "2026-10-16", "source": {"kind": "pine"}}

    response = client.post("/api/backtests", json=body, headers=SAME_ORIGIN)
    options = client.get("/api/backtests/options").json()

    assert response.status_code == 202
    # No capital handed in: the run's own default, unless its changes say another.
    assert backtests.asked == [("create", body, None, None, None), ("options", 12_345.6)]
    assert options == {"fields": []}


def test_a_replay_of_the_journal_is_given_its_signals_with_a_day_either_side(client, ctx, backtests):
    body = {"from": "2026-10-13", "to": "2026-10-16", "source": {"kind": "journal"}}

    client.post("/api/backtests", json=body, headers=SAME_ORIGIN)

    assert ("journal_between", date(2026, 10, 12), date(2026, 10, 17)) in ctx.queries.calls
    assert backtests.asked[0][2] == [JOURNAL_ENTRY]
    # And what WATA did with them, to be set beside the replay.
    assert ("closed_positions", date(2026, 10, 12), date(2026, 10, 17)) in ctx.queries.calls
    assert backtests.asked[0][4] == [CLOSED_POSITION]
    refused = client.post("/api/backtests", json={"from": "monday", "source": {"kind": "journal"}}, headers=SAME_ORIGIN)
    assert refused.status_code == 422


RAISED = {"id": 7, "rule_id": "0123456789ab", "action": "long", "raised_at": datetime(2026, 10, 14, 13, 40, tzinfo=timezone.utc),
          "bar_closed_at": datetime(2026, 10, 14, 13, 40, tzinfo=timezone.utc), "script_version": 2, "trader": None}


def test_a_run_of_the_signals_a_rule_raised_is_given_them_for_its_days(client, ctx, backtests, monkeypatch):
    ctx.queries.raised_rows = [RAISED, {**RAISED, "id": 8, "action": "close-position"}]
    body = {"from": "2026-10-13", "to": "2026-10-16", "source": {"kind": "ta_signals", "rule": {"id": "0123456789ab", "name": "EMA"}}}

    response = client.post("/api/backtests", json=body, headers=SAME_ORIGIN)

    assert response.status_code == 202
    assert ("rule_signals", "0123456789ab", date(2026, 10, 13), date(2026, 10, 16), 20_000) in ctx.queries.calls
    assert backtests.asked[0][2] == ctx.queries.raised_rows
    unnamed = client.post("/api/backtests", json={**body, "source": {"kind": "ta_signals", "rule": {"id": "../etc"}}}, headers=SAME_ORIGIN)
    assert unnamed.status_code == 422
    # More than a run takes: a shorter period.
    monkeypatch.setattr("src.admin.backtests.MAX_RAISED_SIGNALS", 2)
    refused = client.post("/api/backtests", json=body, headers=SAME_ORIGIN)
    assert (refused.status_code, refused.json()["detail"]) == (
        409, "The rule raised more than 2 signals in that period: ask for a shorter one")


def test_the_form_is_told_the_entries_a_rule_raised_each_day(client, ctx, backtests):
    ctx.queries.raised_rows = [RAISED, {**RAISED, "id": 8, "action": "close-position"},
                               {**RAISED, "id": 9, "raised_at": datetime(2026, 10, 15, 13, 40, tzinfo=timezone.utc)}]

    found = client.get("/api/backtests/coverage?from=2026-10-14&to=2026-10-15&source=ta_signals&rule=0123456789ab").json()

    assert [day["signals"] for day in found["days"]] == [1, 1]
    assert client.get("/api/backtests/coverage?from=2026-10-14&to=2026-10-15&source=ta_signals&rule=nope").status_code == 422


class FakePaper:
    """Stands for the paper task: what it was asked to make now, refused when told to."""

    def __init__(self):
        self.asked, self.failure = [], None

    async def ask_report(self, owner):
        self.asked.append(owner)
        if self.failure is not None:
            raise self.failure

    async def drift(self):
        return [], None, None


def test_a_paper_report_is_made_now_when_asked(client, ctx, backtests):
    from src.admin.errors import NotPossible

    assert client.post("/api/backtests/paper/0123456789ab/report", headers=SAME_ORIGIN).status_code == 503
    ctx.paper = FakePaper()

    made = client.post("/api/backtests/paper/0123456789ab/report", headers=SAME_ORIGIN)
    live = client.post("/api/backtests/paper/live/report", headers=SAME_ORIGIN)

    assert (made.status_code, made.json()) == (202, {"rules": {}})
    assert live.status_code == 202 and ctx.paper.asked == ["0123456789ab", "live"]
    assert client.post("/api/backtests/paper/somebody/report", headers=SAME_ORIGIN).status_code == 422
    ctx.paper.failure = NotPossible("Its report is being made already")
    refused = client.post("/api/backtests/paper/0123456789ab/report", headers=SAME_ORIGIN)
    assert (refused.status_code, refused.json()["detail"]) == (409, "Its report is being made already")
    # The live paper days come with their report.
    assert client.get("/api/backtests/paper/live").json() == {
        "days": [], "reference": None, "warning": None, "report": None, "next_report": None,
    }


def test_without_the_database_a_run_starts_from_the_default_account_and_a_replay_cannot_be_asked(client, ctx, backtests):
    ctx.queries.failing = True

    pine = client.post("/api/backtests", json={"from": "2026-10-13", "to": "2026-10-16", "source": {"kind": "pine"}}, headers=SAME_ORIGIN)
    journal = client.post("/api/backtests", json={"from": "2026-10-13", "to": "2026-10-16", "source": {"kind": "journal"}},
                          headers=SAME_ORIGIN)

    assert pine.status_code == 202 and backtests.asked[0][3] is None
    assert journal.status_code == 503


def test_what_the_service_refuses_is_answered_with_its_reason(client, backtests):
    from src.admin.errors import InvalidRequest, NotPossible, UnknownRun

    backtests.failure = UnknownRun(RUN)
    for method, path in (("GET", f"/api/backtests/{RUN}"), ("DELETE", f"/api/backtests/{RUN}"), ("POST", f"/api/backtests/{RUN}/cancel")):
        response = client.request(method, path, headers=SAME_ORIGIN)
        assert (response.status_code, response.json()["detail"]) == (404, "No such backtest")
    backtests.failure = InvalidRequest("A period of 92 days at most")
    response = client.post("/api/backtests", json={"from": "2026-01-01", "to": "2026-10-09"}, headers=SAME_ORIGIN)
    assert (response.status_code, response.json()["detail"]) == (422, "A period of 92 days at most")
    backtests.failure = NotPossible("Stop this backtest before removing it")
    response = client.delete(f"/api/backtests/{RUN}", headers=SAME_ORIGIN)
    assert (response.status_code, response.json()["detail"]) == (409, "Stop this backtest before removing it")


def test_runs_are_listed_read_renamed_stopped_and_removed(client, backtests):
    assert client.get("/api/backtests").json() == {"items": [{"id": RUN}], "storage": {
        "runs": {"count": 1, "bytes": 2048}, "former": {"count": 0, "bytes": 0},
    }}
    assert client.get(f"/api/backtests/{RUN}").json() == {"id": RUN}
    assert client.patch(f"/api/backtests/{RUN}", json={"name": "Kernel", "tags": ["a"]}, headers=SAME_ORIGIN).json()["name"] == "Kernel"
    assert client.post(f"/api/backtests/{RUN}/cancel", headers=SAME_ORIGIN).json() == {"id": RUN}
    assert client.delete(f"/api/backtests/{RUN}", headers=SAME_ORIGIN).status_code == 204
    assert client.post("/api/backtests/remove", json={"ids": [RUN]}, headers=SAME_ORIGIN).json()["removed"] == [RUN]
    assert client.post("/api/backtests/remove", json={"ids": ["x"] * 201}, headers=SAME_ORIGIN).status_code == 422
    assert client.delete("/api/backtests/former", headers=SAME_ORIGIN).json() == {"removed": 3}

    assert [entry[0] for entry in backtests.asked] == [
        "list_runs", "get", "update", "cancel", "delete", "delete_many", "delete_former",
    ]


def test_what_a_run_found_is_read_by_day_and_fate(client, backtests):
    assert client.get(f"/api/backtests/{RUN}/days").json() == {"items": [{"day": "2026-10-12"}]}
    assert client.get(f"/api/backtests/{RUN}/signals?day=2026-10-13&fate=refused").json() == {"items": [{"id": 1}]}
    assert client.get(f"/api/backtests/{RUN}/signals?fate=lost").status_code == 422
    assert client.get(f"/api/backtests/{RUN}/trades?day=2026-10-13").json() == {"items": [{"id": 1}]}
    assert client.get(f"/api/backtests/{RUN}/breakdowns").json() == {"groups": {}}
    assert client.get(f"/api/backtests/{RUN}/baseline").json() == {}
    assert client.get(f"/api/backtests/{RUN}/sweep").json() == {"cells": []}
    assert client.get(f"/api/backtests/{RUN}/edge").json() == {"horizons": []}
    assert client.get(f"/api/backtests/{RUN}/reconciliation").json() == {"summary": {"paired": 0}}
    assert client.get("/api/backtests/paper").json() == {"rules": {}}
    assert client.post(f"/api/backtests/{RUN}/reference", headers=SAME_ORIGIN).json()["rules"]["r1"]["reference"] == {"run_id": RUN}
    assert client.post(f"/api/backtests/{RUN}/cells/2/run", headers=SAME_ORIGIN).status_code == 202
    assert client.post(f"/api/backtests/{RUN}/cells/second/run", headers=SAME_ORIGIN).status_code == 422
    assert backtests.asked[-1] == ("run_cell", RUN, 2)
    assert client.post(f"/api/backtests/{RUN}/stress", headers=SAME_ORIGIN).json() == {"id": RUN, "stress_of": RUN}

    assert backtests.asked[1] == ("signals", RUN, date(2026, 10, 13), "refused")
    assert backtests.asked[-1] == ("stress", RUN)


def test_the_turbos_are_measured_for_the_runs_on_made_up_turbos(client, backtests):
    assert client.get("/api/backtests/turbo-model").json() == {"state": "idle", "measure": None}
    answer = client.post("/api/backtests/turbo-model", json={"from": "2026-10-13", "to": "2026-10-19"}, headers=SAME_ORIGIN)
    assert (answer.status_code, answer.json()) == (202, {"state": "running"})
    assert backtests.asked[-1] == ("measure_turbos", date(2026, 10, 13), date(2026, 10, 19))
    assert client.post("/api/backtests/turbo-model", json={"from": "monday"}, headers=SAME_ORIGIN).status_code == 422


def test_the_execution_of_wata_s_orders_is_measured_over_local_days(client, ctx, backtests):
    found = client.get("/api/backtests/execution?from=2026-10-13&to=2026-10-14").json()

    assert (found["from"], found["to"], found["requests"]) == ("2026-10-13", "2026-10-14", 1)
    assert found["refusals"]["by_hour"] == [{"hour": 15, "requests": 1, "refused": 1}] and found["suggested"] == {}
    # From midnight of the first day in Paris to midnight after the last.
    assert ctx.queries.calls[-1] == ("execution_rows", datetime(2026, 10, 12, 22, 0, tzinfo=timezone.utc),
                                     datetime(2026, 10, 14, 22, 0, tzinfo=timezone.utc))
    assert client.get("/api/backtests/execution?from=2026-10-14&to=2026-10-13").status_code == 422
    assert client.get("/api/backtests/execution").json()["requests"] == 1


def test_the_form_reads_what_was_recorded_each_day(client, ctx, backtests, history):
    ctx.queries.backtest_turbo_rows = [
        {"day": date(2026, 10, 12), "direction": "long", "turbos": 18,
         "first_ts": datetime(2026, 10, 12, 6, 0, tzinfo=timezone.utc), "last_ts": datetime(2026, 10, 12, 20, 0, tzinfo=timezone.utc)},
        {"day": date(2026, 10, 12), "direction": "short", "turbos": 20,
         "first_ts": datetime(2026, 10, 12, 5, 59, 50, tzinfo=timezone.utc), "last_ts": datetime(2026, 10, 12, 19, 0, tzinfo=timezone.utc)},
    ]
    ctx.queries.backtest_recorder_rows = [{"day": date(2026, 10, 12), "listened": 5040}]
    ctx.queries.backtest_asset_rows = [{"day": date(2026, 10, 13), "bars": 4200}]

    saxo = client.get("/api/backtests/coverage?from=2026-10-12&to=2026-10-13&asset=saxo/2&source=journal").json()
    dukascopy = client.get("/api/backtests/coverage?from=2026-10-12&to=2026-10-13&asset=dukascopy/us100").json()

    monday, tuesday = saxo["days"]
    assert (monday["turbos"], monday["first"], monday["last"]) == (
        {"long": 18, "short": 20}, "2026-10-12T05:59:50Z", "2026-10-12T20:00:00Z",
    )
    assert (monday["recorded_minutes"], monday["asset_bars"], tuesday["asset_bars"]) == (840, 0, 4200)
    assert ("backtest_asset_days", "saxo", 2, date(2026, 10, 12), date(2026, 10, 13)) in ctx.queries.calls
    # The journal's entries of the indice, by the day they were received.
    assert [day["signals"] for day in saxo["days"]] == [0, 0]
    assert [day["asset_bars"] for day in dukascopy["days"]] == [1, 1]
    assert client.get("/api/backtests/coverage?from=2026-10-12&to=2026-10-13&asset=saxo/..").status_code == 422
    assert client.get("/api/backtests/coverage?from=2026-06-01&to=2026-10-13").status_code == 422


def test_the_form_reads_the_days_fetched_while_the_database_is_down(client, ctx, backtests, history):
    ctx.queries.failing = True

    answer = client.get("/api/backtests/coverage?from=2026-10-12&to=2026-10-13&asset=dukascopy/us100").json()

    assert answer["database"] is False
    assert [(day["turbos"], day["asset_bars"]) for day in answer["days"]] == [({"long": 0, "short": 0}, 1)] * 2


def test_a_path_that_tries_to_leave_the_runs_folder_finds_no_run(ctx, tmp_path, config_path):
    from src.admin.backtests import BacktestService
    from src.admin.ta_scripts import ScriptStore

    async def never(arguments, log_path):
        raise AssertionError("nothing is to be run")

    ctx.backtests = BacktestService(root=str(tmp_path / "backtests"), prices_root=str(tmp_path / "prices"),
                                    config_path=config_path, scripts=ScriptStore(str(tmp_path / "ta")), launcher=never)
    (tmp_path / "backtests").mkdir()
    (tmp_path / "backtests" / "status.json").write_text("{}")

    with signed_in(ctx, tmp_path) as client:
        for run_id in ("..", "%2E%2E", "..%2F..%2Fetc", "_fetch", "_calibration", RUN, "runs"):
            assert client.get(f"/api/backtests/{run_id}/trades").status_code == 404
            assert client.get(f"/api/backtests/{run_id}/days").status_code == 404
            assert client.get(f"/api/backtests/{run_id}/breakdowns").status_code == 404
            assert client.get(f"/api/backtests/{run_id}/edge").status_code == 404
            assert client.patch(f"/api/backtests/{run_id}", json={"name": "x"}, headers=SAME_ORIGIN).status_code in (404, 405)
            assert client.post(f"/api/backtests/{run_id}/cells/0/run", headers=SAME_ORIGIN).status_code in (404, 405)
            assert client.post(f"/api/backtests/{run_id}/stress", headers=SAME_ORIGIN).status_code in (404, 405)
            assert client.delete(f"/api/backtests/{run_id}", headers=SAME_ORIGIN).status_code in (404, 405)
        outcome = client.post("/api/backtests/remove", json={"ids": ["..", "_fetch", "../prices"]}, headers=SAME_ORIGIN).json()
        assert (outcome["removed"], [entry["reason"] for entry in outcome["kept"]]) == ([], ["No such backtest"] * 3)
    assert (tmp_path / "backtests" / "status.json").exists()

