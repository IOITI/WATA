import asyncio
import json
import logging
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

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
    ("GET", "/api/logs"),
    ("GET", "/api/logs/wata-api"),
    ("POST", "/api/actions/signal"),
    ("POST", "/api/actions/pause-entries"),
    ("POST", "/api/actions/resume-entries"),
    ("POST", "/api/actions/close-position"),
    ("POST", "/api/actions/daily-stats"),
    ("POST", "/api/system/saxo/auth-code"),
    ("GET", "/api/config"),
    ("POST", "/api/config/validate"),
    ("PUT", "/api/config"),
    ("GET", "/api/config/backups"),
    ("GET", "/api/config/backups/config.json.bak-20260101-000000"),
    ("POST", "/api/config/backups/config.json.bak-20260101-000000/restore"),
]


CLOSED_POSITION = {**POSITION, "position_id": "5002", "position_status": "Closed"}

JOURNAL_ENTRY = {
    "signal_uuid": "aaaaaaaa-0000-4000-8000-000000000001",
    "action": "long",
    "indice": "us100",
    "outcome": "refused",
    "reason": "Breaking trading rule: Cooldown active after last loss.",
}


class FakeQueries:
    def __init__(self):
        self.calls = []
        self.last_loss_at = None
        self.paused_since = None
        self.journal = [JOURNAL_ENTRY]
        self.failing = False

    def _check(self):
        if self.failing:
            raise DatabaseUnavailable("connection refused")

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

    async def get_position(self, position_id):
        for position in (POSITION, CLOSED_POSITION):
            if position_id == position["position_id"]:
                return {"position": position, "orders": []}
        return None

    async def open_positions(self):
        self._check()
        self.calls.append(("open_positions",))
        return [{**POSITION, "live_bid": 8.75, "live_performance_percent": 2.94, "live_observed_at": None}]

    async def entries_paused_since(self):
        return self.paused_since

    async def signal_journal(self, start=None, end=None, limit=200):
        self.calls.append(("signal_journal", start, end, limit))
        return self.journal

    async def exits(self, start=None, end=None):
        self.calls.append(("exits", start, end))
        return {"summary": {"trade_count": 0}, "by_reason": [], "by_hour": [], "by_holding_time": []}

    async def report(self, milestones_eur=None):
        self.calls.append(("report", milestones_eur))
        return {"report_date": "2026-10-12", "has_trade_history": False, "balance_history": []}

    async def list_orders(self, start=None, end=None, limit=500):
        self.calls.append(("list_orders", start, end, limit))
        return {"items": [], "total": 0}

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
        self.consumers = {"trading-ops": 1, "telegram_channel": 1}

    async def latest_signals(self):
        if self.web_server_down:
            raise ServiceUnavailable("web_server: connection refused")
        return [{"action": "long", "indice": "us100", "seq": 1}, {"action": "short", "indice": "de40", "seq": 2}]

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

    def save_auth_code(self, code):
        self.codes.append(code)


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
    public = {("POST", "/api/session"), ("GET", "/api/healthz")}
    declared = {
        (
            method,
            route.path.replace("{position_id}", "5001")
            .replace("{service}", "wata-api")
            .replace("{name}", "config.json.bak-20260101-000000"),
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


def test_signals_are_listed_newest_first(client):
    items = client.get("/api/signals").json()["items"]

    assert [signal["seq"] for signal in items] == [2, 1]


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

    assert response.json() == {"valid": True}


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
