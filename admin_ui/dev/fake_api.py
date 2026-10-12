"""
Development server for the admin interface: the real admin application
(src/admin) over in-memory fake data, for working on the UI without Docker,
PostgreSQL or Saxo.

    npm run dev:api        (from admin_ui/, or: PYTHONPATH=./:src/ python3 admin_ui/dev/fake_api.py)
    npm run dev            (in another terminal; Vite proxies /api to this server)

It listens on 127.0.0.1:8082 only and is not part of the deployed package
(package.sh ships ./src). Nothing here touches a real account: signals and
config saves land in memory and in a temporary directory.

Set FAKE_SAXO_EXPIRED=1 to see the Saxo re-authorisation flow, and
FAKE_TOTP=1 to require a one-time code at login (the key is printed at start).
"""

import json
import math
import os
import random
import shutil
import signal
import sys
import tempfile
import types
import uuid
from datetime import date, datetime, timedelta, timezone

# The database layer imports asyncpg, which this server never uses.
if "asyncpg" not in sys.modules:
    try:
        import asyncpg  # noqa: F401
    except ImportError:
        asyncpg_stub = types.ModuleType("asyncpg")
        asyncpg_stub.Pool = object
        asyncpg_stub.Record = dict
        sys.modules["asyncpg"] = asyncpg_stub

import uvicorn

from src.admin.analysis import summarise_exits
from src.admin.app import SHUTDOWN_GRACE_SECONDS, create_app
from src.admin.audit import AuditLog
from src.admin.auth import CredentialStore, LoginThrottle, SessionStore
from src.admin.config_editor import ConfigEditor
from src.admin.context import AdminContext, AdminSettings
from src.admin.queries import json_safe, summarise_trades
from src.configuration import ConfigurationManager
from src.message_helper import build_daily_trading_report_payload

DEV_PASSWORD = "wata-admin-dev"
HOST = "127.0.0.1"
PORT = 8082
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

INSTRUMENTS = [
    ("TURBO LONG NASDAQ-100 KO 24150 SG", "SG-NDX-L1", "long"),
    ("TURBO LONG NASDAQ-100 KO 24300 BNP", "BNP-NDX-L2", "long"),
    ("TURBO SHORT NASDAQ-100 KO 25900 SG", "SG-NDX-S1", "short"),
    ("TURBO SHORT NASDAQ-100 KO 26050 BNP", "BNP-NDX-S2", "short"),
]
CLOSE_REASONS = [
    "Performance (Trailing Stop: peak 9.1%, now 0.8% (dd 8.3% >= 8%))",
    "Performance (Stoploss (-15%) hit at -15.4%)",
    "Performance (Takeprofit (60%) hit at 61.2%)",
    "Performance (Daily profit target (1.7%) potentially met (1.9%))",
    "Explicit Close (All)",
    "Explicit Close (All)",
    "SaxoAPI",
]


def build_history(today: date) -> tuple[list[dict], list[dict]]:
    """A few months of made-up trades, the same on every start."""
    rng = random.Random(7)
    positions, orders = [], []
    for days_ago in range(120, -1, -1):
        day = today - timedelta(days=days_ago)
        if day.weekday() >= 5:
            continue
        opened_at = datetime(day.year, day.month, day.day, 7, 30, tzinfo=timezone.utc)
        for _ in range(rng.randint(0, 4)):
            name, symbol, action = rng.choice(INSTRUMENTS)
            opened_at += timedelta(minutes=rng.randint(20, 150))
            closed_at = opened_at + timedelta(minutes=rng.randint(4, 90))
            amount = rng.randint(80, 400)
            open_price = round(rng.uniform(4.5, 14.0), 2)
            percent = round(rng.gauss(1.2, 6.5), 2)
            best = round(max(percent, 0) + abs(rng.gauss(0, 2.5)), 2)
            close_price = round(open_price * (1 + percent / 100), 2)
            position_id = str(5_000_000 + len(positions))
            order_id = str(7_000_000 + len(orders))
            close_order_id = str(7_000_001 + len(orders))
            positions.append({
                "action": action,
                "position_id": position_id,
                "position_amount": amount,
                "position_open_price": open_price,
                "position_close_price": close_price,
                "position_close_reason": rng.choice(CLOSE_REASONS),
                "position_profit_loss": round((close_price - open_price) * amount, 2),
                "position_total_open_price": round(open_price * amount, 2),
                "position_total_close_price": round(close_price * amount, 2),
                "position_total_performance_percent": percent,
                "position_max_performance_percent": best,
                "position_status": "Closed",
                "position_kind": "main",
                "execution_time_open": opened_at,
                "execution_time_close": closed_at,
                "order_id": order_id,
                "related_order_id": [close_order_id],
                "instrument_name": name,
                "instrument_symbol": symbol,
                "instrument_uic": 40_000_000 + rng.randint(0, 9999),
                "instrument_currency": "EUR",
            })
            for an_order_id, side, price, when in (
                (order_id, "Buy", open_price, opened_at),
                (close_order_id, "Sell", close_price, closed_at),
            ):
                orders.append({
                    "action": action,
                    "buy_sell": side,
                    "order_id": an_order_id,
                    "order_amount": amount,
                    "order_type": "Market",
                    "order_kind": "main",
                    "order_time": when,
                    "related_order_id": [],
                    "position_id": position_id,
                    "instrument_name": name,
                    "instrument_symbol": symbol,
                    "instrument_uic": positions[-1]["instrument_uic"],
                    "instrument_price": price,
                    "instrument_currency": "EUR",
                    "order_cost": round(price * amount, 2),
                })
            opened_at = closed_at

    # One position still open, taken a moment ago.
    name, symbol, action = INSTRUMENTS[0]
    opened_at = datetime.now(timezone.utc) - timedelta(minutes=18)
    positions.append({
        **positions[-1],
        "action": action,
        "position_id": "5999999",
        "position_amount": 260,
        "position_open_price": 8.42,
        "position_close_price": None,
        "position_close_reason": None,
        "position_profit_loss": None,
        "position_total_open_price": 2189.2,
        "position_total_close_price": None,
        "position_total_performance_percent": None,
        "position_max_performance_percent": 3.1,
        "position_status": "Open",
        "execution_time_open": opened_at,
        "execution_time_close": None,
        "order_id": "7999999",
        "related_order_id": [],
        "instrument_name": name,
        "instrument_symbol": symbol,
    })
    orders.append({**orders[-1], "order_id": "7999999", "position_id": "5999999", "buy_sell": "Buy",
                   "order_time": opened_at, "order_amount": 260, "instrument_price": 8.42, "order_cost": 2189.2,
                   "instrument_name": name, "instrument_symbol": symbol, "action": action})
    return positions, orders


def activity_time(position: dict) -> datetime:
    return position["execution_time_close"] or position["execution_time_open"]


def in_range(when: datetime, start: date | None, end: date | None) -> bool:
    day = when.date()
    return (start is None or day >= start) and (end is None or day <= end)


class FakeQueries:
    def __init__(self):
        self.today = datetime.now(timezone.utc).date()
        self.positions, self.orders = build_history(self.today)
        self.paused_since = None
        self.journal: list[dict] = []
        self._ticks = 0
        now = datetime.now(timezone.utc)
        self.add_decision(
            {"action": "close-position", "indice": "n/a"}, "closed", "Closed 1 position(s). Errors: 0",
            decided_at=now - timedelta(hours=14),
        )
        self.add_decision(
            {"action": "short", "indice": "us100"}, "refused",
            "Breaking trading rule: Cooldown active after last loss. Remaining: 412s (cooldown: 10min)",
            decided_at=now - timedelta(minutes=47),
        )
        self.add_decision(
            {"action": "long", "indice": "us100", "confidence": 0.9}, "executed",
            decided_at=now - timedelta(minutes=18), position_id="5999999", order_id="7999999",
        )

    def add_decision(self, signal, outcome, reason=None, decided_at=None, **ids):
        decided_at = decided_at or datetime.now(timezone.utc)
        self.journal.insert(0, {
            "signal_uuid": signal.get("signal_uuid") or str(uuid.uuid4()),
            "action": signal.get("action"),
            "indice": signal.get("indice"),
            "confidence": signal.get("confidence"),
            "signal_timestamp": decided_at,
            "received_timestamp": decided_at,
            "decided_at": decided_at,
            "outcome": outcome,
            "reason": reason,
            "position_id": ids.get("position_id"),
            "order_id": ids.get("order_id"),
        })

    def close_position(self, position_id: str, reason: str) -> None:
        for position in self.positions:
            if position["position_id"] == position_id and position["position_status"] == "Open":
                close_price = round(position["position_open_price"] * 1.012, 2)
                position.update({
                    "position_status": "Closed",
                    "position_close_price": close_price,
                    "position_close_reason": reason,
                    "position_total_performance_percent": 1.2,
                    "position_profit_loss": round((close_price - position["position_open_price"]) * position["position_amount"], 2),
                    "position_total_close_price": round(close_price * position["position_amount"], 2),
                    "execution_time_close": datetime.now(timezone.utc),
                })

    async def ping(self):
        return True

    async def entries_paused_since(self):
        return self.paused_since

    async def open_positions(self):
        """Open positions with a made-up price that moves a little at every call."""
        self._ticks += 1
        positions = (await self.list_positions(status="Open"))["items"]
        result = []
        for position in positions:
            performance = round(1.4 + 1.6 * math.sin(self._ticks / 4), 2)
            result.append({
                **position,
                "live_bid": round(position["position_open_price"] * (1 + performance / 100), 2),
                "live_performance_percent": performance,
                "live_observed_at": datetime.now(timezone.utc) - timedelta(seconds=2),
            })
        return result

    async def signal_journal(self, start=None, end=None, limit=200):
        entries = [entry for entry in self.journal if in_range(entry["decided_at"], start, end)]
        return entries[:limit]

    async def exits(self, start=None, end=None):
        closed = [p for p in self.positions if p["position_status"] == "Closed"]
        closed = [p for p in closed if in_range(p["execution_time_close"], start, end)]
        return summarise_exits([
            {
                "action": p["action"],
                "close_reason": p["position_close_reason"],
                "performance_percent": p["position_total_performance_percent"],
                "max_performance_percent": p["position_max_performance_percent"],
                "profit_loss": p["position_profit_loss"],
                "open_hour": (p["execution_time_open"].hour + 2) % 24,
                "holding_seconds": (p["execution_time_close"] - p["execution_time_open"]).total_seconds(),
            }
            for p in closed
        ])

    async def report(self, milestones_eur=None):
        closed = sorted(
            (p for p in self.positions if p["position_status"] == "Closed"),
            key=lambda p: p["execution_time_close"],
        )
        trades = [
            {
                "action": p["action"],
                "position_id": p["position_id"],
                "performance_percent": p["position_total_performance_percent"],
                "max_performance_percent": p["position_max_performance_percent"],
                "profit_loss": p["position_profit_loss"],
                "execution_time_close": p["execution_time_close"],
            }
            for p in closed
        ]
        daily = summarise_trades(self._closed_trades())["daily"]
        balances, balance = [], 9000.0
        for day in daily:
            balance += day["profit_loss"]
            balances.append({"date_day": day["day_date"], "total_value": round(balance, 2), "recorded_at": None})
        payload = build_daily_trading_report_payload(
            report_date=self.today,
            closed_trades=trades,
            daily_profit_history=[
                {"day_date": day["day_date"], "sum_profit": day["profit_loss"], "trade_count": day["trade_count"]}
                for day in daily
            ],
            daily_real={}, daily_best={}, daily_max={},
            current_balance=balances[-1]["total_value"] if balances else None,
            milestones_eur=milestones_eur or [10_000, 25_000, 100_000],
        )
        return json_safe({
            **payload,
            "balance_recorded_on": balances[-1]["date_day"] if balances else None,
            "balance_history": balances,
        })

    def _closed_trades(self, start=None, end=None):
        closed = [p for p in self.positions if p["position_status"] == "Closed"]
        closed = [p for p in closed if in_range(p["execution_time_close"], start, end)]
        closed.sort(key=lambda p: p["execution_time_close"])
        return [
            {
                "day_date": p["execution_time_close"].date(),
                "action": p["action"],
                "instrument_name": p["instrument_name"],
                "performance_percent": p["position_total_performance_percent"],
                "profit_loss": p["position_profit_loss"],
            }
            for p in closed
        ]

    async def day_overview(self):
        today = summarise_trades(self._closed_trades(self.today, self.today))
        day = today["daily"][0] if today["daily"] else None
        opened_today = sum(1 for p in self.positions if p["execution_time_open"].date() == self.today)
        return {
            "trade_count": opened_today,
            "closed_count": day["trade_count"] if day else 0,
            "performance_percent": day["performance_percent"] if day else 0.0,
            "profit_loss": day["profit_loss"] if day else 0.0,
            "best_percent": None,
            "worst_percent": None,
            "by_action": [],
        }

    async def last_loss_timestamp(self):
        return datetime.now(timezone.utc) - timedelta(minutes=4)

    async def list_positions(self, status=None, start=None, end=None, limit=500):
        rows = [p for p in self.positions if status is None or p["position_status"] == status]
        rows = [p for p in rows if in_range(activity_time(p), start, end)]
        rows.sort(key=activity_time, reverse=True)
        return {"items": rows[:limit], "total": len(rows)}

    async def get_position(self, position_id):
        position = next((p for p in self.positions if p["position_id"] == position_id), None)
        if position is None:
            return None
        return {"position": position, "orders": [o for o in self.orders if o["position_id"] == position_id]}

    async def list_orders(self, start=None, end=None, limit=500):
        rows = [o for o in self.orders if in_range(o["order_time"], start, end)]
        rows.sort(key=lambda o: o["order_time"], reverse=True)
        return {"items": rows[:limit], "total": len(rows)}

    async def performance(self, start=None, end=None):
        before = sum(t["profit_loss"] for t in self._closed_trades(None, start - timedelta(days=1))) if start else 0.0
        return summarise_trades(self._closed_trades(start, end), before)


def utc_stamp(when: datetime, milliseconds: bool = False) -> str:
    if milliseconds:
        return when.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeRiskState:
    def __init__(self, queries: FakeQueries):
        self._queries = queries

    async def pause_entries(self, ts):
        if self._queries.paused_since is None:
            self._queries.paused_since = ts

    async def resume_entries(self):
        self._queries.paused_since = None

    async def get_entries_paused_since(self):
        return self._queries.paused_since


class FakeServices:
    watchlist_enabled = True
    watchlist_directions = ["long", "short"]

    def __init__(self, queries: FakeQueries):
        self._queries = queries
        now = datetime.now(timezone.utc)
        self._seq = 0
        self._signals: dict[str, dict] = {}
        self._store({"action": "long", "indice": "us100", "confidence": 0.9}, now - timedelta(minutes=18))
        self._store({"action": "close-position", "indice": "n/a"}, now - timedelta(hours=14))

    def _store(self, payload: dict, when: datetime) -> str:
        self._seq += 1
        signal_uuid = str(uuid.uuid4())
        self._signals[payload["indice"]] = {
            "signal_timestamp": utc_stamp(when),
            "alert_timestamp": utc_stamp(when),
            **payload,
            "signal_uuid": signal_uuid,
            "received_timestamp": utc_stamp(when, milliseconds=True),
            "seq": self._seq,
        }
        return signal_uuid

    async def latest_signals(self):
        return list(self._signals.values())

    async def send_signal(self, payload):
        print(f"[fake] signal: {payload}")
        signal_uuid = self._store(payload, datetime.now(timezone.utc))
        signal = {**payload, "signal_uuid": signal_uuid}
        # What a trader would have journaled a moment later.
        if payload["action"] in ("long", "short"):
            if self._queries.paused_since is not None:
                self._queries.add_decision(
                    signal, "refused",
                    f"Breaking trading rule: New entries are paused by the operator since {self._queries.paused_since}.",
                )
            else:
                self._queries.add_decision(signal, "refused", "Breaking trading rule: Cooldown active after last loss.")
        else:
            open_ids = [p["position_id"] for p in self._queries.positions if p["position_status"] == "Open"]
            for position_id in open_ids:
                self._queries.close_position(position_id, "Explicit Close (All)")
            if open_ids:
                self._queries.add_decision(signal, "closed", f"Closed {len(open_ids)} position(s). Errors: 0")
            else:
                self._queries.add_decision(signal, "ignored", "No open position to close")
        return signal_uuid

    async def watchlist_best(self, indice, direction):
        now = datetime.now(timezone.utc)
        if direction == "short":
            name, symbol, bid = "TURBO SHORT NASDAQ-100 KO 25900 SG", "SG-NDX-S1", 9.12
        else:
            name, symbol, bid = "TURBO LONG NASDAQ-100 KO 24150 SG", "SG-NDX-L1", 8.47
        return {
            "selected_instrument": {
                "description": name, "symbol": symbol, "currency": "EUR",
                "latest_bid": bid, "latest_ask": round(bid + 0.03, 2),
            },
            "watchlist": {
                "price_last_updated_at": utc_stamp(now - timedelta(seconds=3)),
                "search_refreshed_at": utc_stamp(now - timedelta(minutes=2)),
                "stale": direction == "short",
            },
        }

    async def watchlist_health(self):
        now = datetime.now(timezone.utc)
        return {
            "service": "watchlist_manager",
            "ready": True,
            "configured_targets": 2,
            "warm_entries": 2,
            "stale_entries": 1,
            "active_stream_subscriptions": 2,
            "desired_stream_subscriptions": 2,
            "last_refresh_completed_at": utc_stamp(now - timedelta(minutes=2)),
            "last_refresh_errors": [],
        }

    async def queue_stats(self):
        return [
            {"name": "trading-ops", "declared": True, "messages": 0, "consumers": 1},
            {"name": "telegram_channel", "declared": True, "messages": 2, "consumers": 1},
        ]

    async def publish_ops(self, message):
        print(f"[fake] trading-ops: {message}")
        if message.get("action") == "close_position":
            self._queries.close_position(message["position_id"], f"Explicit Close (position {message['position_id']})")


class FakeSaxo:
    def __init__(self):
        self.expired = os.getenv("FAKE_SAXO_EXPIRED") == "1"

    def status(self):
        now = datetime.now(timezone.utc)
        saved_at = now - timedelta(minutes=11)
        return {
            "has_token": True,
            "saved_at": saved_at.isoformat(),
            "access_token_expires_at": (saved_at + timedelta(minutes=20)).isoformat(),
            "refresh_token_expires_at": (saved_at + timedelta(minutes=-5 if self.expired else 60)).isoformat(),
            "access_token_expired": self.expired,
            "refresh_token_expired": self.expired,
            "authorization_url": "https://live.logonvalidation.net/authorize?response_type=code&client_id=example",
        }

    def save_auth_code(self, code):
        print(f"[fake] Saxo authorization code received ({len(code)} characters)")
        self.expired = False


def write_sample_logs(log_path: str) -> None:
    now = datetime.now()

    def stamp(seconds_ago: int) -> str:
        return (now - timedelta(seconds=seconds_ago)).strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]

    samples = {
        "wata-trader-async": [
            (5400, "INFO", "__main__", "--- Starting WATA Async Trader v0.11.0 ---"),
            (5390, "INFO", "__main__", "Connecting to PostgreSQL..."),
            (5380, "INFO", "__main__", "Trader startup complete. Waiting for signals (long-poll)..."),
            (1080, "INFO", "__main__", "Dispatching action=long signal_uuid=3f1c2a9e-0000-4000-8000-000000000000"),
            (1078, "INFO", "__main__", "Trade long executed — Order 7999999, Position 5999999"),
            (300, "WARNING", "root", "Breaking trading rule: Cooldown active after last loss. Remaining: 420s (cooldown: 10min)"),
            (120, "ERROR", "src.trade.async_services", "Saxo request failed, retrying\nTraceback (most recent call last):\n  File \"async_services.py\", line 210, in request\nhttpx.ReadTimeout: timed out"),
        ],
        "wata-scheduler": [
            (5400, "INFO", "root", "-------------------------------- Started wata-scheduler application"),
            (5399, "INFO", "root", "WATA scheduler is running"),
        ],
        "wata-api": [
            (5400, "INFO", "root", "WATA Web-server API is running"),
            (1081, "INFO", "root", "Stored signal for indice=us100: {'action': 'long', 'indice': 'us100'}"),
            (60, "WARNING", "root", "Forbidden access attempt from IP: 198.51.100.23"),
        ],
    }
    for service, lines in samples.items():
        os.makedirs(os.path.join(log_path, service), exist_ok=True)
        with open(os.path.join(log_path, service, f"{service}.log"), "w") as log_file:
            for seconds_ago, level, logger_name, message in lines:
                log_file.write(f"{stamp(seconds_ago)} - {level} - {logger_name} - {message}\n")

    # The per-trade timings the trader writes beside its logs.
    rng = random.Random(11)
    for days_ago in range(6):
        day = now - timedelta(days=days_ago)
        with open(os.path.join(log_path, f"execution_timing_{day.strftime('%Y-%m-%d')}.jsonl"), "w") as timing_file:
            for trade in range(rng.randint(1, 4)):
                found = rng.randint(40, 180)
                placed = found + rng.randint(120, 320)
                confirmed = placed + rng.randint(150, 900)
                timing_file.write(json.dumps({
                    "action": rng.choice(["long", "short"]), "confidence": rng.choice([None, 0.6, 0.9]),
                    "position_scale": rng.choice([50, 75, 100]),
                    "timestamp": f"{day.strftime('%Y-%m-%d')} {9 + trade * 2:02d}:{rng.randint(0, 59):02d}:00",
                    "steps": {
                        "scale_calculated": 1, "turbo_found": found, "spending_power_fetched": found + 8,
                        "amount_calculated": found + 9, "order_placed": placed,
                        "position_confirmed": confirmed, "db_persisted": confirmed + rng.randint(5, 30),
                    },
                }) + "\n")


def build_dev_context(work_dir: str) -> AdminContext:
    with open(os.path.join(REPO_ROOT, "etc", "config_example.json")) as source:
        config = json.load(source)
    config["logging"]["persistant"]["log_path"] = os.path.join(work_dir, "log")
    for name in ("authentication", "webserver"):
        token_dir = os.path.join(work_dir, "lib", name)
        config[name]["persistant"]["token_path"] = os.path.join(token_dir, "persist_token.json")
    config["trade"]["persistant"]["last_action_file"] = os.path.join(work_dir, "lib", "last_action.json")

    config_path = os.path.join(work_dir, "etc", "config.json")
    os.makedirs(os.path.dirname(config_path))
    with open(config_path, "w") as config_file:
        json.dump(config, config_file, indent=2)

    config_manager = ConfigurationManager(config_path)
    settings = AdminSettings.from_config(config_manager)
    settings.credentials_path = os.path.join(work_dir, "lib", "admin", "credentials.json")
    # Plain HTTP on localhost: a Secure cookie would not come back in every browser.
    settings.cookie_secure = False
    write_sample_logs(settings.log_path)

    credentials = CredentialStore(settings.credentials_path)
    credentials.set_password(DEV_PASSWORD)
    if os.getenv("FAKE_TOTP") == "1":
        print(f"One-time code key for an authenticator app: {credentials.enable_totp()}")

    async def notifier(message: str) -> None:
        print(f"[fake] Telegram: {message!r}")

    queries = FakeQueries()
    return AdminContext(
        config_manager=config_manager,
        settings=settings,
        credentials=credentials,
        sessions=SessionStore(idle_seconds=3600, max_age_seconds=12 * 3600),
        throttle=LoginThrottle(),
        queries=queries,
        services=FakeServices(queries),
        saxo=FakeSaxo(),
        config_editor=ConfigEditor(config_path),
        risk_state=FakeRiskState(queries),
        audit_log=AuditLog(os.path.join(settings.log_path, "wata-admin", "audit.jsonl")),
        notifier=notifier,
        version="dev",
    )


def _exit_normally(*_):
    raise SystemExit(0)


if __name__ == "__main__":
    # uvicorn hands a stop request back to the process once it has shut down:
    # exit normally then, so that the temporary directory is removed.
    signal.signal(signal.SIGTERM, _exit_normally)
    work_dir = tempfile.mkdtemp(prefix="wata-admin-dev-")
    try:
        ctx = build_dev_context(work_dir)
        print(f"WATA Admin with fake data on http://{HOST}:{PORT}  (password: {DEV_PASSWORD})")
        print(f"Temporary config and logs in {work_dir}")
        uvicorn.run(
            create_app(ctx), host=HOST, port=PORT, log_level="warning",
            timeout_graceful_shutdown=SHUTDOWN_GRACE_SECONDS,
        )
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
