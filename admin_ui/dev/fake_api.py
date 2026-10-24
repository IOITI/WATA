"""
Development server for the admin interface: the real admin application
(src/admin) over in-memory fake data, for working on the UI without Docker,
PostgreSQL or Saxo.

    npm run dev:api        (from admin_ui/, or: PYTHONPATH=./:src/ python3 admin_ui/dev/fake_api.py)
    npm run dev            (in another terminal; Vite proxies /api to this server)

It listens on 127.0.0.1:8082 only and is not part of the deployed package
(package.sh ships ./src). Nothing here touches a real account: signals and
config saves land in memory and in a temporary directory.

Downloads of history are the exception to "fake": the prices are kept under
var/lib/price_history of this checkout, and fetching them from the page really
asks Dukascopy.

Backtests are real ones too: the real command, with the real Pine runner when
it is built (npm run build:runner), on the made-up recorded days of
fake_recorded.py, written as files at start. Their results stay under
var/lib/backtests of this checkout.

Set FAKE_SAXO_EXPIRED=1 to see the Saxo re-authorisation flow, and
FAKE_TOTP=1 to require a one-time code at login (the key is printed at start).
A close asked from the pages takes a few seconds, as the monitor's would: set
FAKE_SELL_REFUSED=1 to have Saxo refuse the first sell (the card that follows
a close shows each step). A signal sent by hand is decided two seconds later.

Set FAKE_MCP=1 for Claude's MCP endpoint, on http://127.0.0.1:8082/mcp: a
token is printed at start, its Pine studies are real ones (the real Pine
runner) on the made-up recorded days, and Claude's shadow rules are on.
"""

import asyncio
import json
import math
import os
import random
import shutil
import signal
import sys
import tempfile
import time
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
from src.admin.backtests import SIGNAL_CACHE_FOLDER, BacktestService
from src.admin.config_editor import ConfigEditor
from src.admin.context import AdminContext, AdminSettings
from src.admin.data import PriceHistory
from src.admin.downloads import HistoryDownloads, config_hours
from src.admin.events import EventHub
from src.admin.live_prices import LivePrices
from src.admin.mcp_server.calls import CallLog
from src.admin.mcp_server.pause import McpPause
from src.admin.mcp_server.tokens import TokenStore
from src.admin.order_requests import refusal_runs, requests_by_order
from src.admin.service_control import RestartRefused
from src.admin.vault import AdminVault
from src.vault import keys
from src.admin.service_restart import ORDER, RestartPlans, situation_of
from src.admin.queries import (
    _ORDER_POSITION_COLUMNS,
    _ORDER_SIGNAL_COLUMNS,
    _REFUSED_SIGNAL_COLUMNS,
    json_safe,
    summarise_trades,
)
from src.admin.studies import StudyService
from src.admin.weekday_hour import opened_weekday_hour, stats_by_weekday_hour
from src.configuration import ConfigurationManager
from src.message_helper import build_daily_trading_report_payload

import fake_capital
import fake_prices
import fake_recorded
import fake_ta

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


# The made-up history changes over this many days ago, as a server does when it
# is given the version that keeps what became of each order: before, the buy
# order alone, written once its position was confirmed; since, every order
# with its moments.
TRACKED_SINCE_DAYS = 30
ORDER_MARKS = (
    "purpose", "sent_at", "accepted_at", "confirmed_at", "gave_up_at", "lookups", "outcome",
    "cancel_accepted", "detail", "recorded_by",
)
NO_MARKS = dict.fromkeys(ORDER_MARKS)
# How long after Saxo accepted an order its position was seen, and at which look.
ENTRY_WAITS = ((60, 140, 1), (190, 330, 2), (420, 700, 3))
SLOW_ENTRY_WAITS = ((2600, 4200, 6), (5200, 7800, 9))
EXIT_WAITS = ((260, 340, 1), (580, 700, 2))


def order_marks(rng: random.Random, purpose: str, at_saxo: datetime, waits=None) -> dict:
    """What a service would have kept of an order Saxo filled at ``at_saxo`` (its own clock)."""
    sent_at = at_saxo - timedelta(milliseconds=rng.randint(40, 160))
    accepted_at = sent_at + timedelta(milliseconds=rng.randint(130, 320))
    low, high, lookups = rng.choice(waits or (ENTRY_WAITS if purpose == "open" else EXIT_WAITS))
    return {
        **NO_MARKS,
        "purpose": purpose,
        "sent_at": sent_at,
        "accepted_at": accepted_at,
        "confirmed_at": accepted_at + timedelta(milliseconds=rng.randint(low, high)),
        "lookups": lookups,
        "outcome": "confirmed",
        "recorded_by": "trader" if purpose == "open" else "position_monitor",
    }


def given_up(marks: dict, after_ms: int, lookups: int, detail: str | None, cancel_accepted: bool | None) -> dict:
    """The same order, had Saxo never shown what it was meant to do."""
    return {
        **marks,
        "confirmed_at": None,
        "gave_up_at": marks["accepted_at"] + timedelta(milliseconds=after_ms),
        "lookups": lookups,
        "outcome": "gave_up",
        "cancel_accepted": cancel_accepted,
        "detail": detail,
    }


def build_history(today: date) -> tuple[list[dict], list[dict]]:
    """A few months of made-up trades, the same on every start."""
    rng = random.Random(7)
    # Its own draws, so that the trades themselves stay what they were.
    marks_rng = random.Random(11)
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
            order_id = str(7_000_000 + 2 * len(positions))
            close_order_id = str(7_000_001 + 2 * len(positions))
            close_reason = rng.choice(CLOSE_REASONS)
            tracked = days_ago <= TRACKED_SINCE_DAYS
            positions.append({
                "action": action,
                "position_id": position_id,
                "position_amount": amount,
                "position_open_price": open_price,
                "position_close_price": close_price,
                "position_close_reason": close_reason,
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
                "related_order_id": [],
                "instrument_name": name,
                "instrument_symbol": symbol,
                "instrument_uic": 40_000_000 + rng.randint(0, 9999),
                "instrument_currency": "EUR",
            })
            buy = {
                "action": action,
                "buy_sell": "Buy",
                "order_id": order_id,
                "order_amount": amount,
                "order_type": "Market",
                "order_kind": "main",
                "related_order_id": [],
                "position_id": position_id,
                "instrument_name": name,
                "instrument_symbol": symbol,
                "instrument_uic": positions[-1]["instrument_uic"],
                "instrument_price": open_price,
                "instrument_currency": "EUR",
                "order_cost": round(open_price * amount * 0.001, 2),
            }
            if not tracked:
                # Written once the position was confirmed, to the second: all there was.
                recorded_at = (opened_at + timedelta(seconds=1)).replace(microsecond=0)
                orders.append({**buy, "order_time": recorded_at, **NO_MARKS})
            else:
                marks = order_marks(marks_rng, "open", opened_at)
                orders.append({**buy, "order_time": marks["sent_at"].replace(microsecond=0), **marks})
                # A position Saxo closed by itself has no sell order from WATA.
                if close_reason != "SaxoAPI":
                    marks = order_marks(marks_rng, "close", closed_at)
                    orders.append({
                        **buy,
                        "buy_sell": "Sell",
                        "order_id": close_order_id,
                        "order_kind": "close",
                        "order_time": marks["sent_at"],
                        # The bid the sale was decided on, a little off what it was sold at.
                        "instrument_price": round(close_price + marks_rng.choice((-0.01, 0.0, 0.0, 0.01, 0.02)), 2),
                        "order_cost": None,
                        **marks,
                    })
            opened_at = closed_at

    add_orders_worth_a_look(positions, orders, marks_rng)

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
    marks = order_marks(marks_rng, "open", opened_at)
    orders.append({**orders[-1], "order_id": "7999999", "position_id": "5999999", "buy_sell": "Buy",
                   "order_kind": "main", "order_time": marks["sent_at"].replace(microsecond=0),
                   "order_amount": 260, "instrument_price": 8.42, "order_cost": 2.19,
                   "instrument_name": name, "instrument_symbol": symbol, "action": action, **marks})
    return positions, orders


def add_orders_worth_a_look(positions: list[dict], orders: list[dict], rng: random.Random) -> None:
    """
    What the Orders page is for, among the tracked trades: an entry Saxo took
    seconds to show, two orders that gave no position, a sale never seen closed.
    """
    tracked = [order for order in orders if order["sent_at"] is not None]
    buys = [order for order in tracked if order["buy_sell"] == "Buy"]
    sells = [order for order in tracked if order["buy_sell"] == "Sell"]
    if len(buys) < 6 or len(sells) < 3:
        return

    def position_of(order: dict) -> dict:
        return next(position for position in positions if position["position_id"] == order["position_id"])

    # Two entries whose position took seconds to be shown.
    for slow in (buys[-2], buys[len(buys) // 2]):
        slow.update(order_marks(rng, "open", position_of(slow)["execution_time_open"], SLOW_ENTRY_WAITS))

    # Two orders Saxo accepted that gave no position: one cancelled, one whose
    # cancellation Saxo refused.
    refusal = (
        'SaxoApiError: Request failed (Status: 400) '
        '{"ErrorInfo": {"ErrorCode": "OrderNotFound", "Message": "Order not found"}}'
    )
    for number, (like, detail, cancel_accepted) in enumerate(
        ((buys[-4], None, True), (buys[len(buys) // 3], refusal, False)),
    ):
        left_at = like["sent_at"] + timedelta(minutes=7)
        marks = order_marks(rng, "open", left_at)
        orders.append({
            **like,
            "order_id": str(7_900_000 + number),
            "position_id": None,
            "order_time": marks["sent_at"].replace(microsecond=0),
            **given_up(marks, after_ms=10_800 + rng.randint(600, 1400), lookups=14, detail=detail,
                       cancel_accepted=cancel_accepted),
        })

    # A sale Saxo did not list among its closed positions in time: the closure
    # was found later, when the database was set against Saxo.
    unseen = sells[-3]
    unseen.update(given_up(
        {key: unseen[key] for key in ORDER_MARKS}, after_ms=7_500 + rng.randint(500, 900), lookups=7,
        detail="Not among Saxo's closed positions", cancel_accepted=None,
    ))
    position_of(unseen)["position_close_reason"] = "SaxoAPI"


# What Saxo answered on 2026-10-14, refusing a market sell in a volatile market.
LIMIT_ONLY = "À ce stade, seuls des ordres limites sont autorisés"
LIMIT_ONLY_BODY = json.dumps({"ErrorInfo": {"ErrorCode": "OtherError", "Message": LIMIT_ONLY}}, ensure_ascii=False)
# The open position whose sell Saxo keeps refusing, while the server runs.
STILL_REFUSED = "5999999"


def fake_request(at: datetime, order: dict | None = None, operation: str = "place", outcome: str = "accepted",
                 answered_ms: int = 140, **fields) -> dict:
    """A request to Saxo's order endpoint as order_request keeps it, about ``order`` when given."""
    order = order or {}
    selling = order.get("buy_sell") == "Sell"
    purpose = fields.pop("purpose", "close" if selling else "open")
    body = None
    if operation == "place":
        body = {
            "Uic": order.get("instrument_uic"), "AssetType": "WarrantOpenEndKnockOut",
            "Amount": order.get("order_amount"), "BuySell": order.get("buy_sell"), "ManualOrder": False,
            "OrderType": "Market", "AmountType": "Quantity", "OrderDuration": {"DurationType": "DayOrder"},
        }
    elif operation == "cancel":
        body = {"OrderIds": fields.get("order_id")}
    refused = outcome == "refused"
    request = {
        "request_id": str(uuid.uuid4()),
        "requested_at": at,
        "answered_at": at + (timedelta(seconds=30) if outcome == "no_answer" else timedelta(milliseconds=answered_ms)),
        "recorded_by": "trader" if purpose == "open" else "position_monitor",
        "operation": operation,
        "purpose": purpose,
        "position_id": order.get("position_id") if purpose != "open" else None,
        "signal_uuid": None,
        "action": order.get("action"),
        "reason": None,
        "instrument_uic": order.get("instrument_uic"),
        "asset_type": "WarrantOpenEndKnockOut",
        "instrument_name": order.get("instrument_name"),
        "instrument_symbol": order.get("instrument_symbol"),
        "instrument_currency": order.get("instrument_currency", "EUR"),
        "buy_sell": order.get("buy_sell") if operation == "place" else None,
        "amount": order.get("order_amount") if operation == "place" else None,
        "order_type": "Market" if operation == "place" else None,
        "price_seen": order.get("instrument_price"),
        "outcome": outcome,
        "order_id": order.get("order_id") if outcome == "accepted" and operation == "place" else None,
        "http_status": 400 if refused else None,
        "error_code": "OtherError" if refused else None,
        "error_message": LIMIT_ONLY if refused else ("ApiRequestException: Unexpected request error: ReadTimeout" if outcome == "no_answer" else None),
        "request_body": json.dumps(body) if body else None,
        "response_body": LIMIT_ONLY_BODY if refused else (json.dumps({"OrderId": order.get("order_id")}) if outcome == "accepted" and operation == "place" else None),
    }
    request.update(fields)
    return request


def build_requests(positions: list[dict], orders: list[dict]) -> tuple[list[dict], dict | None]:
    """
    The requests a server keeps from the version that records them: every
    tracked order's, a run of refusals like the one of 2026-10-14 before a sale,
    a sell left without an answer, resting orders cancelled, a buy Saxo refused
    (with the journal entry of its signal), and the sells of the open position
    Saxo has been refusing for a few minutes.
    """
    by_position = {position["position_id"]: position for position in positions}
    requests = []
    tracked = [order for order in orders if order["sent_at"] is not None]
    for order in tracked:
        position = by_position.get(order["position_id"]) if order["position_id"] else None
        reason = position["position_close_reason"] if position and order["buy_sell"] == "Sell" else None
        requests.append(fake_request(order["sent_at"], order, reason=reason,
                                     answered_ms=int((order["accepted_at"] - order["sent_at"]).total_seconds() * 1000)))
        if order["cancel_accepted"] is not None:
            requests.append(fake_request(
                order["gave_up_at"], order, operation="cancel", order_id=order["order_id"],
                outcome="accepted" if order["cancel_accepted"] else "refused",
                reason="Saxo showed no position for the order",
                **({} if order["cancel_accepted"] else {
                    "http_status": 404, "error_code": "OrderNotFound", "error_message": "Order not found",
                    "response_body": json.dumps({"ErrorInfo": {"ErrorCode": "OrderNotFound", "Message": "Order not found"}}),
                }),
            ))

    sells = [order for order in tracked if order["buy_sell"] == "Sell" and order["outcome"] == "confirmed"]
    refused_buy = None
    if len(sells) >= 4:
        # 37 sells refused over 2 min 19 s, about one a price update, before the sale went through.
        sale = sells[-2]
        reason = by_position[sale["position_id"]]["position_close_reason"]
        first = sale["sent_at"] - timedelta(seconds=140)
        for attempt in range(37):
            requests.append(fake_request(first + timedelta(milliseconds=round(attempt * 139_000 / 36)), sale,
                                         outcome="refused", answered_ms=110 + attempt % 5 * 20, reason=reason))
        # A sell left without an answer, sold at the next try.
        unanswered = sells[-4]
        requests.append(fake_request(unanswered["sent_at"] - timedelta(seconds=31), unanswered, outcome="no_answer",
                                     reason=by_position[unanswered["position_id"]]["position_close_reason"]))
        # The protective stop resting on a position, cancelled on the way to its sale.
        resting = sells[-5] if len(sells) >= 5 else sells[0]
        requests.append(fake_request(resting["sent_at"] - timedelta(milliseconds=180), resting, operation="cancel",
                                     order_id=f"stop-{resting['order_id']}", reason="Explicit Close (All)"))

        # A buy Saxo refused: no order, no position, the signal journaled "failed".
        like = next(order for order in reversed(tracked) if order["buy_sell"] == "Buy")
        at = like["sent_at"] - timedelta(minutes=95)
        signal_uuid = str(uuid.uuid4())
        buy = {**like, "order_id": None, "position_id": None}
        requests.append(fake_request(at, buy, outcome="refused", signal_uuid=signal_uuid))
        refused_buy = {
            "signal": {"action": like["action"], "indice": "us100", "signal_uuid": signal_uuid},
            "decided_at": at + timedelta(milliseconds=160),
            "reason": (
                f"Order rejected: API error placing order (Status: 400) (Saxo: {LIMIT_ONLY} [OtherError]) "
                f"(Order: Buy {like['order_amount']} units of {like['instrument_uic']})"
            ),
        }

    # The open position: Saxo has been refusing its sell for a few minutes.
    still_open = by_position.get(STILL_REFUSED)
    if still_open is not None:
        sale = {
            "buy_sell": "Sell", "position_id": STILL_REFUSED, "action": still_open["action"],
            "instrument_uic": still_open["instrument_uic"], "instrument_name": still_open["instrument_name"],
            "instrument_symbol": still_open["instrument_symbol"], "order_amount": still_open["position_amount"],
            "instrument_price": round(still_open["position_open_price"] * 0.93, 2), "instrument_currency": "EUR",
        }
        now = datetime.now(timezone.utc)
        for attempt in range(12):
            requests.append(fake_request(now - timedelta(seconds=240 - attempt * 20), sale, outcome="refused",
                                         reason="Performance (Stoploss (-7%) hit at -7.1%)"))
    requests.sort(key=lambda request: request["requested_at"])
    return requests, refused_buy


def activity_time(position: dict) -> datetime:
    return position["execution_time_close"] or position["execution_time_open"]


def in_range(when: datetime, start: date | None, end: date | None) -> bool:
    day = when.date()
    return (start is None or day >= start) and (end is None or day <= end)


class FakeQueries:
    def __init__(self):
        self.today = datetime.now(timezone.utc).date()
        self.positions, self.orders = build_history(self.today)
        self.requests, refused_buy = build_requests(self.positions, self.orders)
        self.paused_since = None
        # FAKE_SELL_REFUSED=1: Saxo refuses the first sell asked from the pages.
        self.refuse_next_sell = os.getenv("FAKE_SELL_REFUSED") == "1"
        self.journal: list[dict] = []
        self._ticks = 0
        if refused_buy is not None:
            self.add_decision(refused_buy["signal"], "failed", refused_buy["reason"], decided_at=refused_buy["decided_at"])
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
        # The signals behind the made-up trades of the Prices page, for the last week.
        for days_ago in range(7):
            day = (now.astimezone(fake_prices.TRADING_TIMEZONE) - timedelta(days=days_ago)).date()
            if day.weekday() < 5:
                for signal, outcome, reason, decided_at in fake_prices.daily_signals(day, now):
                    self.add_decision(signal, outcome, reason, decided_at=decided_at)
        # The signals behind the last tracked entries, the ones that gave no position among them.
        tracked_buys = [o for o in self.orders if o["sent_at"] is not None and o["buy_sell"] == "Buy"]
        recent = sorted(tracked_buys, key=lambda o: o["sent_at"], reverse=True)[:12]
        for order in [o for o in tracked_buys if o["position_id"] is None or o in recent]:
            if order["order_id"] == "7999999":
                continue
            received_at = order["sent_at"] - timedelta(milliseconds=170)
            failed = order["position_id"] is None
            self.add_decision(
                {"action": order["action"], "indice": "us100"},
                "failed" if failed else "executed",
                f"PositionNotFoundException: Position not found after 14 retries for order {order['order_id']}"
                if failed else None,
                decided_at=order["gave_up_at"] or order["confirmed_at"], received_at=received_at,
                position_id=order["position_id"], order_id=order["order_id"],
            )
        self.journal.sort(key=lambda entry: entry["decided_at"], reverse=True)

    def add_decision(self, signal, outcome, reason=None, decided_at=None, received_at=None, **ids):
        decided_at = decided_at or datetime.now(timezone.utc)
        received_at = received_at or decided_at
        self.journal.insert(0, {
            "signal_uuid": signal.get("signal_uuid") or str(uuid.uuid4()),
            "action": signal.get("action"),
            "indice": signal.get("indice"),
            "confidence": signal.get("confidence"),
            "signal_timestamp": received_at - timedelta(milliseconds=450) if received_at != decided_at else decided_at,
            "received_timestamp": received_at,
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
                marks = order_marks(random.Random(), "close", position["execution_time_close"])
                self.orders.append({
                    "action": position["action"], "buy_sell": "Sell", "order_id": str(8_000_000 + len(self.orders)),
                    "order_amount": position["position_amount"], "order_type": "Market", "order_kind": "close",
                    "order_time": marks["sent_at"], "related_order_id": [], "position_id": position_id,
                    "instrument_name": position["instrument_name"], "instrument_symbol": position["instrument_symbol"],
                    "instrument_uic": position["instrument_uic"], "instrument_price": close_price,
                    "instrument_currency": position["instrument_currency"], "order_cost": None, **marks,
                })

    async def ping(self):
        return True

    async def entries_paused_since(self):
        return self.paused_since

    async def price_recorder_status(self):
        # As if the recorder had just written the bucket that ended.
        now = datetime.now(timezone.utc)
        return {"ts": now - timedelta(seconds=12), "feed_ok": True, "instruments": 43, "bars": 43}

    # ── recorded prices: made up, see fake_prices.py ──

    async def price_instruments(self, start, end):
        # Like the real query: only what has bars in the period (none on a weekend).
        now = datetime.now(timezone.utc)
        if not fake_prices.bars(fake_prices.INDEX["id"], start, end, 3600, 1, now):
            return []
        return [
            {**instrument, **fake_prices.last_quote(instrument["id"], start, end, now)}
            for instrument in fake_prices.INSTRUMENTS
        ]

    async def price_runs(self, start, end, limit):
        from src.admin.prices import Run

        return [Run(*row) for row in fake_prices.runs(start, end, datetime.now(timezone.utc))][:limit]

    async def price_volumes(self, start, end):
        return fake_prices.volumes(start, end, datetime.now(timezone.utc))

    async def price_instrument(self, instrument_id):
        instrument = fake_prices.BY_ID.get(instrument_id)
        found = fake_prices.assets(datetime.now(timezone.utc), instrument_id)
        if instrument is None or not found:
            return instrument
        return {**instrument, "first_seen_at": found[0]["first_seen_at"], "last_seen_at": found[0]["last_seen_at"]}

    async def price_assets(self, limit, since=None, instrument_id=None):
        rows = fake_prices.assets(datetime.now(timezone.utc), instrument_id)
        return [row for row in rows if since is None or row["last_seen_at"] >= since][:limit]

    async def price_index_of(self, indice, start, end):
        now = datetime.now(timezone.utc)
        index = fake_prices.INDEX
        if index["indice"] != indice or not fake_prices.bars(index["id"], start, end, 3600, 1, now):
            return None
        return dict(index)

    async def price_volume_days(self, instrument_id, start, end):
        return fake_prices.volume_days(instrument_id, start, end, datetime.now(timezone.utc))

    async def price_bars(self, instrument_id, start, end, interval, limit):
        return fake_prices.bars(instrument_id, start, end, interval, limit, datetime.now(timezone.utc))

    async def price_series(self, instrument_id, start, end, side, limit):
        first = 1 if side == "bid" else 5
        rows = fake_prices.bars(instrument_id, start, end, 10, limit, datetime.now(timezone.utc))
        return [(row[0], row[first], row[first + 1], row[first + 2], row[first + 3], row[9]) for row in rows]

    async def price_coverage(self, start, end):
        return fake_prices.coverage(start, end, datetime.now(timezone.utc))

    # ── what backtests read: the days written as files at start, see fake_recorded.py ──

    recorded_days: list = []

    async def backtest_turbo_days(self, indice, first_day, last_day):
        return fake_recorded.turbo_days(self.recorded_days, indice, first_day, last_day, datetime.now(timezone.utc))

    async def backtest_recorder_days(self, first_day, last_day):
        return fake_recorded.recorder_days(self.recorded_days, first_day, last_day, datetime.now(timezone.utc))

    async def backtest_asset_days(self, provider, instrument_id, first_day, last_day):
        return fake_recorded.asset_days(provider, instrument_id, first_day, last_day, datetime.now(timezone.utc))

    async def last_balance(self):
        return 10_250.0

    async def price_trades(self, instrument, start, end, limit=500):
        return fake_prices.trades(instrument, start, end, datetime.now(timezone.utc))

    # ── the Capital.com feed: made up, see fake_capital.py ──

    async def capital_feed_state(self):
        return fake_capital.feed_state(datetime.now(timezone.utc))

    async def service_heartbeats(self):
        # The trader and the position monitor alive, polling and streaming, Saxo's login renewed.
        now = datetime.now(timezone.utc)
        started = now - timedelta(hours=3)
        return {
            "trader": {"beat_at": now, "started_at": started, "version": "dev", "figures": {
                "last_poll_at": now.isoformat(), "poll_failing_since": None, "poll_error": None,
                "last_signal_at": (now - timedelta(minutes=7)).isoformat(), "signal_silence_minutes": None,
                "signals_silent": False,
                "saxo_login": {"renewal_failing_since": None, "error": None,
                               "lapses_at": (now + timedelta(minutes=50)).isoformat()},
                "saxo_session": {"trade_level": "FullTradingAndChat", "data_level": "Standard", "checked_at": now.isoformat()},
            }},
            "position_monitor": {"beat_at": now, "started_at": started, "version": "dev", "figures": {
                "stream_connected": True, "last_message_at": now.isoformat(), "last_update_at": now.isoformat(),
                "positions_listed": 1, "rest_fallback_since": None, "close_intents": [],
            }},
        }

    # ── the TA engine: made up from the signal rules of the store, see fake_ta.py ──

    ta_engine: "fake_ta.FakeEngine | None" = None

    async def ta_signals(self, start=None, end=None, rule_id=None, limit=500):
        return self.ta_engine.signals(start, end, rule_id, limit) if self.ta_engine else []

    async def ta_other_signals(self, indice, day):
        return self.ta_engine.others(indice, day) if self.ta_engine else []

    async def ta_engine_state(self):
        return self.ta_engine.state() if self.ta_engine else None

    async def ta_rule_states(self):
        return self.ta_engine.rule_states() if self.ta_engine else {}

    async def ta_monitor_recent(self, since, *, slow_tick_ms):
        return self.ta_engine.monitor_recent(since, slow_tick_ms) if self.ta_engine else {}

    async def ta_monitor_period(self, start, end, bucket_seconds, rule_id=None, *, signal_age_seconds, slow_tick_ms):
        return self.ta_engine.monitor_period(start, end, bucket_seconds, rule_id, signal_age_seconds) if self.ta_engine else {}

    async def capital_assets(self, epic=None):
        if epic not in (None, fake_capital.EPIC):
            return []
        return [fake_capital.asset(datetime.now(timezone.utc))]

    async def capital_instrument(self, epic):
        rows = await self.capital_assets(epic)
        return rows[0] if rows else None

    async def capital_opening_hours(self, epic):
        return fake_capital.HOURS if epic == fake_capital.EPIC else None

    async def capital_coverage(self, start, end):
        return fake_capital.coverage(start, end, datetime.now(timezone.utc))

    async def capital_bars(self, instrument_id, start, end, interval, limit):
        return fake_capital.bars(start, end, interval, limit, datetime.now(timezone.utc))

    async def capital_series(self, instrument_id, start, end, side, limit):
        first = 1 if side == "bid" else 5
        rows = fake_capital.ten_second_bars(start, end, datetime.now(timezone.utc))[:limit]
        return [(row[0], row[first], row[first + 1], row[first + 2], row[first + 3], row[9]) for row in rows]

    async def capital_volume(self, instrument_id, start, end, interval, limit):
        return fake_capital.volume(start, end, interval, limit, datetime.now(timezone.utc))

    async def capital_volume_days(self, instrument_id, start, end):
        return fake_capital.volume_days(start, end, datetime.now(timezone.utc))

    async def index_trades(self, indice, start, end, *, every_position, limit=500):
        return fake_prices.trades(dict(fake_prices.INDEX), start, end, datetime.now(timezone.utc))

    async def open_positions(self):
        """Open positions with a made-up price that moves a little at every call."""
        self._ticks += 1
        self._keep_refusing()
        positions = (await self.list_positions(status="Open"))["items"]
        result = []
        for position in positions:
            performance = round(1.4 + 1.6 * math.sin(self._ticks / 4), 2)
            result.append({
                **position,
                "live_bid": round(position["position_open_price"] * (1 + performance / 100), 2),
                "live_performance_percent": performance,
                "live_observed_at": datetime.now(timezone.utc) - timedelta(seconds=2),
                "sell_refusals": self._sell_refusals(position["position_id"]),
            })
        return result

    async def signal_journal(self, start=None, end=None, limit=200):
        entries = [entry for entry in self.journal if in_range(entry["decided_at"], start, end)]
        return entries[:limit]

    async def journal_between(self, start=None, end=None):
        return sorted(
            (entry for entry in self.journal if in_range(entry["decided_at"], start, end)),
            key=lambda entry: entry["decided_at"],
        )

    async def closed_positions(self, start=None, end=None):
        return [
            position for position in self.positions
            if position["position_status"] == "Closed" and in_range(position["execution_time_open"], start, end)
        ]

    async def execution_rows(self, start, end):
        """
        The rows the execution measure reads (src/backtest/execution_calibration.py):
        the tracked orders, each against a made-up quote of when it left, a cent
        or two better than what it was filled at.
        """
        rng = random.Random(13)
        positions = {position["position_id"]: position for position in self.positions}
        orders, quotes = [], []
        for order in self.orders:
            if order.get("sent_at") is None or order.get("purpose") not in ("open", "close") or not start <= order["sent_at"] < end:
                continue
            position = positions.get(order["position_id"], {})
            orders.append({
                "order_id": order["order_id"], "purpose": order["purpose"], "sent_at": order["sent_at"],
                "accepted_at": order["accepted_at"], "outcome": order["outcome"], "instrument_uic": order["instrument_uic"],
                "position_open_price": position.get("position_open_price"),
                "position_close_price": position.get("position_close_price"),
            })
            worse = rng.choice((0.0, 0.01, 0.01, 0.02, 0.03))
            if order["purpose"] == "open" and position.get("position_open_price") is not None:
                quotes.append({"order_id": order["order_id"], "bid_close": None,
                               "ask_close": round(position["position_open_price"] - worse, 2)})
            elif order["purpose"] == "close" and position.get("position_close_price") is not None:
                quotes.append({"order_id": order["order_id"], "ask_close": None,
                               "bid_close": round(position["position_close_price"] + worse, 2)})
        signals = [
            {key: entry[key] for key in ("signal_timestamp", "received_timestamp", "decided_at", "outcome")}
            for entry in self.journal if entry["action"] in ("long", "short") and start <= entry["decided_at"] < end
        ]
        requests = [
            {key: request[key] for key in ("requested_at", "purpose", "outcome", "error_code")}
            for request in self.requests if request["operation"] == "place" and start <= request["requested_at"] < end
        ]
        return {"signals": signals, "orders": orders, "quotes": quotes, "requests": requests}

    async def exits(self, start=None, end=None):
        closed = [p for p in self.positions if p["position_status"] == "Closed"]
        closed = [p for p in closed if in_range(p["execution_time_close"], start, end)]
        trades = [
            {
                "action": p["action"],
                "close_reason": p["position_close_reason"],
                "performance_percent": p["position_total_performance_percent"],
                "max_performance_percent": p["position_max_performance_percent"],
                "profit_loss": p["position_profit_loss"],
                "open_hour": (p["execution_time_open"].hour + 2) % 24,
                "open_weekday": (p["execution_time_open"] + timedelta(hours=2)).isoweekday(),
                "holding_seconds": (p["execution_time_close"] - p["execution_time_open"]).total_seconds(),
            }
            for p in closed
        ]
        return {**summarise_exits(trades), "by_weekday_hour": stats_by_weekday_hour(trades, opened_weekday_hour)}

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
        orders = sorted((o for o in self.orders if o["position_id"] == position_id), key=lambda o: o["order_time"])
        requests = [
            request for request in self.requests
            if request["position_id"] == position_id or (request["order_id"] and request["order_id"] == position["order_id"])
        ]
        linked = [self._linked(order) for order in orders]
        attached = requests_by_order(requests, [order["order_id"] for order in linked])
        for order in linked:
            order["requests"] = attached.get(order["order_id"], [])
        return {"position": position, "orders": linked, "requests": requests, "requests_total": len(requests)}

    def _sell_refusals(self, position_id: str) -> dict | None:
        """What AdminQueries.sell_refusals says of a position."""
        refused = [
            request for request in self.requests
            if request["position_id"] == position_id and request["operation"] == "place"
            and request["purpose"] == "close" and request["outcome"] != "accepted"
        ]
        if not refused:
            return None
        last = refused[-1]
        return {
            "position_id": position_id, "count": len(refused), "first_at": refused[0]["requested_at"],
            "last_at": last["requested_at"], "outcome": last["outcome"], "http_status": last["http_status"],
            "error_code": last["error_code"], "error_message": last["error_message"],
        }

    def _keep_refusing(self) -> None:
        """Now and then, one more sell of the open position refused: what the live alert is for."""
        position = next((p for p in self.positions if p["position_id"] == STILL_REFUSED and p["position_status"] == "Open"), None)
        last = next((r for r in reversed(self.requests) if r["position_id"] == STILL_REFUSED), None)
        now = datetime.now(timezone.utc)
        if position is None or last is None or now - last["requested_at"] < timedelta(seconds=20):
            return
        self.requests.append({**last, "request_id": str(uuid.uuid4()), "requested_at": now,
                              "answered_at": now + timedelta(milliseconds=130)})

    def _linked(self, order: dict) -> dict:
        """An order as the admin's query gives it: with the position it gave or closed and its signal."""
        position = next((p for p in self.positions if p["position_id"] == order["position_id"]), None)
        signal = next((entry for entry in self.journal if entry["order_id"] == order["order_id"]), None)
        return {
            **order,
            "position": position and {column: position[column] for column in _ORDER_POSITION_COLUMNS},
            "signal": signal and {column: signal[column] for column in _ORDER_SIGNAL_COLUMNS},
        }

    async def list_orders(self, start=None, end=None, limit=500):
        rows = [o for o in self.orders if in_range(o["order_time"], start, end)]
        rows.sort(key=lambda o: o["order_time"], reverse=True)
        items = [self._linked(order) for order in rows[:limit]]
        requests = [request for request in self.requests if in_range(request["requested_at"], start, end)]
        attached = requests_by_order(requests, [order["order_id"] for order in items])
        for order in items:
            order["requests"] = attached.get(order["order_id"], [])
        refusals = refusal_runs(requests)
        for run in refusals:
            position = next((p for p in self.positions if p["position_id"] == run["position_id"]), None)
            signal = next((entry for entry in self.journal if entry["signal_uuid"] == run["signal_uuid"]), None)
            run["position"] = position and {column: position[column] for column in _ORDER_POSITION_COLUMNS}
            run["signal"] = signal and {column: signal[column] for column in _REFUSED_SIGNAL_COLUMNS}
        return {"items": items, "total": len(rows), "refusals": refusals}

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
        # What a trader would have journaled a moment later: its next poll, then its decision.
        asyncio.get_running_loop().call_later(2.0, self._decide, payload, signal)
        return signal_uuid

    def _decide(self, payload, signal):
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
            # As the monitor would: its sell leaves a moment later, and Saxo shows the position closed after it.
            asyncio.get_running_loop().call_later(1.5, self._sell, message["position_id"])

    def _sell(self, position_id: str) -> None:
        position = next((p for p in self._queries.positions if p["position_id"] == position_id), None)
        if position is None or position["position_status"] != "Open":
            return
        reason = f"Explicit Close (position {position_id})"
        refused = self._queries.refuse_next_sell
        self._queries.refuse_next_sell = False
        sell = {
            "buy_sell": "Sell", "action": position["action"], "position_id": position_id,
            "order_id": str(8_100_000 + len(self._queries.requests)), "order_amount": position["position_amount"],
            "instrument_uic": position["instrument_uic"], "instrument_name": position["instrument_name"],
            "instrument_symbol": position["instrument_symbol"], "instrument_currency": position["instrument_currency"],
            "instrument_price": position.get("live_bid") or position["position_open_price"],
        }
        request = fake_request(datetime.now(timezone.utc), sell, outcome="refused" if refused else "accepted")
        request["reason"] = reason
        self._queries.requests.append(request)
        if not refused:
            asyncio.get_running_loop().call_later(1.5, self._queries.close_position, position_id, reason)


def seeded_mcp_calls(calls: CallLog) -> CallLog:
    """A few of Claude's calls, done, refused and failed: System › Claude has a trace to show without FAKE_MCP."""
    calls.record(caller="claude-mac", tool="status", arguments={}, outcome="ok", milliseconds=38)
    calls.record(
        caller="claude-mac", tool="pine_save", outcome="ok", milliseconds=2400,
        arguments={"name": "EMA cross", "source": "//@version=6\nindicator('EMA cross', overlay=true)\n" + "// …\n" * 40},
    )
    calls.record(
        caller="claude-mac", tool="backtest_start", outcome="refused", milliseconds=3,
        arguments={"from": "2026-10-13", "to": "2026-10-22", "source": {"kind": "pine", "script": "ema-cross", "version": 2}},
        detail="Experiment 1a2b3c is concluded (kept): open a new one with exp_new",
    )
    calls.record(
        caller="claude-mac", tool="pine_run", outcome="failed", milliseconds=45012,
        arguments={"script": "ema-cross", "days": 5, "alert_titles": ["Long", "Short"]},
        detail="The Pine runner stopped: JavaScript heap out of memory",
    )
    return calls


class FakeControl:
    """
    The control service, as if Docker ran WATA's containers here: started
    with the heartbeats' services, a restart taking a few seconds. The admin's
    own restart is only said: it would stop this server.
    """

    available = True
    RESTART_SECONDS = 4

    def __init__(self, started: datetime):
        self.states = {
            service: {
                "service": service, "container": f"{service}1", "state": "running", "running": True,
                "started_at": started.isoformat(), "finished_at": None, "exit_code": 0, "oom_killed": False,
                "restart_count": 0, "image": "wata-base:dev", "restarting": False, "last_restart": None,
            }
            for service in ORDER
        }
        self._until: dict[str, tuple[float, str]] = {}

    async def services(self, max_age=0):
        now = time.monotonic()
        for service, (until, requested_at) in list(self._until.items()):
            if now >= until:
                state = self.states[service]
                state.update(
                    state="running", running=True, restarting=False, started_at=datetime.now(timezone.utc).isoformat(),
                    restart_count=state["restart_count"] + 1,
                    last_restart={"requested_at": requested_at, "done_at": datetime.now(timezone.utc).isoformat(), "ok": True, "detail": None},
                )
                del self._until[service]
        return [dict(state) for state in self.states.values()]

    async def restart(self, service):
        if self._until:
            raise RestartRefused(409, f"{', '.join(self._until)} is being restarted: one at a time")
        requested_at = datetime.now(timezone.utc).isoformat()
        if service == "admin":
            print("[fake] The admin would restart now")
        else:
            self._until[service] = (time.monotonic() + self.RESTART_SECONDS, requested_at)
            self.states[service].update(state="restarting", running=False, restarting=True)
        return {"service": service, "container": f"{service}1", "requested_at": requested_at}

    async def close(self):
        pass


class FakeCredentialTable:
    """The tables credential and credential_check in memory, for the admin's vault (src/admin/vault.py)."""

    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.checks: dict[tuple[str, str], dict] = {}

    async def fetch(self, query, *args):
        if "FROM credential WHERE name = ANY" in query:
            return [{"name": name, **row} for name, row in self.rows.items() if name in args[0]]
        if "FROM credential ORDER BY" in query:
            return [{"name": name, **row} for name, row in sorted(self.rows.items())]
        if "FROM credential_check" in query:
            return list(self.checks.values())
        return []

    async def execute(self, query, *args):
        query = " ".join(query.split())
        if query.startswith("INSERT INTO credential ("):
            name, public, sealed, readers, fingerprint, set_at, by = args
            if "DO NOTHING" in query and name in self.rows:
                return "INSERT 0 0"
            self.rows[name] = {"public": json.loads(public), "sealed": json.loads(sealed), "readers": readers,
                               "fingerprint": fingerprint, "set_at": set_at, "set_by": by}
            return "INSERT 0 1"
        if query.startswith("INSERT INTO credential_check"):
            name, role, checked_at, ok, detail = args
            self.checks[(name, role)] = {"name": name, "role": role, "checked_at": checked_at, "ok": ok, "detail": detail}
            return "INSERT 0 1"
        if query.startswith("DELETE FROM credential WHERE"):
            return "DELETE 1" if self.rows.pop(args[0], None) is not None else "DELETE 0"
        if query.startswith("DELETE FROM credential_check"):
            for key in [key for key in self.checks if key[0] == args[0]]:
                del self.checks[key]
            return "DELETE"
        return "OK"


class FakeReadiness:
    """A service running again is at work at once, here."""

    async def before(self, service):
        return None

    async def ready(self, service, started_at, before):
        return True, "At work again (made up)"


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
            "needs_authorization_since": saved_at.isoformat() if self.expired else None,
            "last_refusal": None,
            "environment": "live",
            "where": "database",
            "saved_by": "position_monitor",
            "redirect_url": "https://admin.example.com/api/saxo/callback",
            "callback": True,
            "apps": {"live": {"set": True, "app_key": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6", "fingerprint": "f00d"},
                     "simulation": {"set": False, "app_key": None, "fingerprint": None}},
            "authorization_url": None,
        }

    async def authorize(self, environment=None):
        # Saxo's page is not opened in development: the link leads straight to the admin's callback.
        return "/api/saxo/callback?code=fake-code-0123456789&state=fake-state"

    async def hand_over(self, code, *, state=None, environment=None, by=None):
        print(f"[fake] Saxo authorization code received ({len(code)} characters)")
        self.expired = False
        return environment or "live"


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
    # Shown in the service list, as on a server where the price recorder and the Capital.com feed run.
    config["trade"]["config"]["price_recorder"]["enabled"] = True
    config["trade"]["config"]["capital_feed"]["enabled"] = True
    config["trade"]["config"]["ta_engine"]["enabled"] = True
    mcp = os.getenv("FAKE_MCP") == "1"
    if mcp:
        config["admin"]["mcp_enabled"] = True
        config["admin"]["mcp"]["shadow_rules"] = True

    config_path = os.path.join(work_dir, "etc", "config.json")
    os.makedirs(os.path.dirname(config_path))
    with open(config_path, "w") as config_file:
        json.dump(config, config_file, indent=2)
    # Written before the containers started (FakeControl): what is saved from now on is what they do not run with.
    written = (datetime.now(timezone.utc) - timedelta(hours=4)).timestamp()
    os.utime(config_path, (written, written))

    config_manager = ConfigurationManager(config_path)
    settings = AdminSettings.from_config(config_manager)
    settings.credentials_path = os.path.join(work_dir, "lib", "admin", "credentials.json")
    # Where the backtests below keep their runs: Claude's journal and lock box are kept beside them.
    settings.backtests_path = os.path.join(REPO_ROOT, "var", "lib", "backtests")
    # Plain HTTP on localhost: a Secure cookie would not come back in every browser.
    settings.cookie_secure = False
    write_sample_logs(settings.log_path)

    credentials = CredentialStore(settings.credentials_path)
    credentials.set_password(DEV_PASSWORD)
    if os.getenv("FAKE_TOTP") == "1":
        print(f"One-time code key for an authenticator app: {credentials.enable_totp()}")

    async def notifier(message: str) -> None:
        print(f"[fake] Telegram: {message!r}")

    events = EventHub()
    prices_root = os.path.join(REPO_ROOT, "var", "lib", "price_history")
    downloads = HistoryDownloads(prices_root=prices_root, hours=config_hours(config_path), publish=events.publish)

    queries = FakeQueries()
    ta_scripts = fake_ta.seeded_store(os.path.join(work_dir, "lib", "admin", "ta"))
    recorded_root = os.path.join(work_dir, "recorded")
    queries.recorded_days = fake_recorded.write(recorded_root, datetime.now(timezone.utc))
    backtests = BacktestService(
        root=settings.backtests_path,
        prices_root=prices_root,
        config_path=config_path,
        scripts=ta_scripts,
        publish=events.publish,
        data=f"files:{recorded_root}",
    )
    queries.ta_engine = fake_ta.FakeEngine(ta_scripts, datetime.now(timezone.utc) - timedelta(hours=3))
    closers = [backtests.close, downloads.close]
    settings.mcp_tokens_path = os.path.join(work_dir, "lib", "admin", "mcp_tokens.json")
    mcp_tokens = TokenStore(settings.mcp_tokens_path)
    studies = None
    if mcp:
        studies = StudyService(
            root=os.path.join(settings.backtests_path, "_studies"), ta_root=ta_scripts.root,
            prices_root=prices_root, signal_cache=os.path.join(settings.backtests_path, SIGNAL_CACHE_FOLDER),
            memory_mb=settings.mcp_limits.study_memory_mb, timeout_seconds=settings.mcp_limits.study_timeout_seconds,
            minutes_per_hour=settings.mcp_limits.study_minutes_per_hour, data=f"files:{recorded_root}",
        )
        closers.append(studies.close)
        token, _ = mcp_tokens.create("dev")
        print(f"Claude's MCP endpoint on http://{HOST}:{PORT}/mcp, token: {token}")
    ctx = AdminContext(
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
        backtests=backtests,
        downloads=downloads,
        history=PriceHistory(prices_root),
        # The made-up Capital.com stream, in place of RabbitMQ.
        live_prices=LivePrices(fake_capital.source),
        # The Pine scripts, on the temporary folder, with a few examples.
        ta_scripts=ta_scripts,
        mcp_tokens=mcp_tokens,
        mcp_calls=seeded_mcp_calls(CallLog(os.path.join(settings.log_path, "wata-admin", "mcp.jsonl"))),
        mcp_pause=McpPause(os.path.join(work_dir, "lib", "admin", "mcp_pause.json")),
        studies=studies,
        # The containers, as the heartbeats say: started three hours ago.
        service_control=FakeControl(datetime.now(timezone.utc) - timedelta(hours=3)),
        events=events,
        notifier=notifier,
        version="dev",
        closers=closers,
    )
    ctx.restarts = RestartPlans(ctx.service_control, FakeReadiness(), lambda: situation_of(ctx), events, poll_seconds=1)
    # The credentials, sealed with keys made for this run: the admin's own and every public one, as the
    # compose file mounts them. FAKE_NO_KEYS=1 shows the page of an admin without them (the lab).
    keys_root = os.path.join(work_dir, "keys")
    view = os.path.join(work_dir, "admin-keys")
    if os.getenv("FAKE_NO_KEYS") != "1":
        keys.generate(keys_root)
        os.makedirs(os.path.join(view, "own"))
        shutil.copy(os.path.join(keys_root, "admin", "private.key"), os.path.join(view, "own", "private.key"))
        os.symlink(os.path.join(keys_root, "public"), os.path.join(view, "public"))
    table = FakeCredentialTable()
    ctx.vault = AdminVault(reader=table, writer=table, config_manager=config_manager, keys_dir=view,
                           feed_state=queries.capital_feed_state)
    return ctx


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
