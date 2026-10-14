"""
Development server for the admin interface: the real admin application
(src/admin) over in-memory fake data, for working on the UI without Docker,
PostgreSQL or Saxo.

    npm run dev:api        (from admin_ui/, or: PYTHONPATH=./:src/ python3 admin_ui/dev/fake_api.py)
    npm run dev            (in another terminal; Vite proxies /api to this server)

It listens on 127.0.0.1:8082 only and is not part of the deployed package
(package.sh ships ./src). Nothing here touches a real account: signals and
config saves land in memory and in a temporary directory.

Backtests are the exception to "fake": they are real ones, on the prices kept
under var/lib/price_history of this checkout, and their results stay under
var/lib/backtests. Fetching prices from the page really asks Dukascopy.

What a backtest sets against "what WATA really did" is made up here from those
same prices (``real_history``): a few signals on each day that has prices, and
the trades a turbo of a known leverage would have given, a little off. So a
replay of real signals finds its pairs, and measuring the turbo finds it.

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
from src.admin.backtests import BacktestService
from src.admin.config_editor import ConfigEditor
from src.admin.context import AdminContext, AdminSettings
from src.admin.data import PriceHistory
from src.admin.events import EventHub
from src.admin.queries import _ORDER_POSITION_COLUMNS, _ORDER_SIGNAL_COLUMNS, json_safe, summarise_trades
from src.configuration import ConfigurationManager
from src.message_helper import build_daily_trading_report_payload
from src.strategy.engine import LocalClock
from src.strategy.execution import Simulator, TurboModel, WataRules
from src.strategy.history import dukascopy
from src.strategy.signals import decisions_of
from src.strategy.sources import FileBars

import fake_prices

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


# The turbo the made-up real trades were "bought": what measuring it should find.
REAL_TURBO = TurboModel(entry_price=5.2, points_per_unit=104.0, spread=0.04)
# When TradingView "sent" a signal on each day, in the trading timezone.
REAL_SIGNALS = (("09:30", "long"), ("11:15", "short"), ("15:45", "long"), ("17:10", "short"), ("21:55", "close-position"))


def real_history(prices_root: str, rules: WataRules) -> tuple[list[dict], list[dict]]:
    """
    A journal and closed positions for the days that have prices under
    ``prices_root``: what WATA "did" with a few signals a day, on a turbo
    that follows the index of those prices a little loosely.
    """
    rng = random.Random(11)
    clock = LocalClock(rules.timezone)
    location = dukascopy.bars_directory(prices_root, "us100")
    journal, positions = [], []
    try:
        names = sorted(os.listdir(location))
    except OSError:
        return journal, positions
    for name in names:
        try:
            day = date.fromisoformat(name.removesuffix(".csv.gz"))
        except ValueError:
            continue
        start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        bars = list(FileBars(os.path.join(location, name), "us100").bars(start, start + timedelta(days=1)))
        sent = [
            {"at": clock.epoch(day, clock_time) + rng.randint(1, 8), "action": action, "confidence": round(rng.uniform(0.5, 1.9), 2)}
            for clock_time, action in REAL_SIGNALS
        ]
        simulator = Simulator(rules, REAL_TURBO, clock, entries_are_orders=True)
        waiting = decisions_of(sent)
        for bar in bars:
            simulator.on_bar(bar)
            while waiting and waiting[0].at < bar.start + 10:
                simulator.decide(waiting.pop(0))
        simulator.finish()

        opened = {}
        for trade in simulator.trades:
            position_id = str(6_000_000 + len(positions))
            # Filled a few seconds after the model, at a price a little off, on a turbo not always bought at the same price.
            bought = round(REAL_TURBO.entry_price * rng.uniform(0.85, 1.35), 2)
            performance = round(trade.performance + rng.gauss(0, 0.25), 2)
            sold = round(bought * (1 + performance / 100), 3)
            opened[trade.entry_at] = position_id
            positions.append({
                "position_id": position_id, "action": trade.direction,
                "execution_time_open": datetime.fromtimestamp(trade.entry_at + rng.randint(1, 6), timezone.utc),
                "execution_time_close": datetime.fromtimestamp(trade.exit_at + rng.randint(1, 9), timezone.utc),
                "position_open_price": bought, "position_close_price": sold,
                "position_total_performance_percent": round(sold * 100 / bought - 100, 2),
                "position_max_performance_percent": trade.max_performance,
                "position_close_reason": f"Performance ({trade.exit_reason})",
                "instrument_name": "TURBO NASDAQ-100 (made up)",
            })
        refused = {refusal.at: refusal.reason for refusal in simulator.refusals}
        for signal in sent:
            # Acted on at the first ten seconds after the signal.
            acted_at = signal["at"] - signal["at"] % 10 + 10
            entry = signal["action"] in ("long", "short")
            journal.append({
                "signal_uuid": str(uuid.UUID(int=rng.getrandbits(128))), "action": signal["action"],
                "indice": "us100" if entry else "n/a", "confidence": signal["confidence"] if entry else None,
                "signal_timestamp": datetime.fromtimestamp(signal["at"] - 2, timezone.utc),
                "received_timestamp": datetime.fromtimestamp(signal["at"] - 1, timezone.utc),
                "decided_at": datetime.fromtimestamp(signal["at"], timezone.utc),
                "outcome": "executed" if acted_at in opened else "refused" if acted_at in refused else "ignored" if not entry else "failed",
                "reason": refused.get(acted_at), "position_id": opened.get(acted_at),
            })
    return journal, positions


def activity_time(position: dict) -> datetime:
    return position["execution_time_close"] or position["execution_time_open"]


def in_range(when: datetime, start: date | None, end: date | None) -> bool:
    day = when.date()
    return (start is None or day >= start) and (end is None or day <= end)


class FakeQueries:
    def __init__(self, real: tuple[list[dict], list[dict]] = ([], [])):
        # What backtests are set against: see ``real_history``.
        self.real_journal, self.real_positions = real
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
        return [(row[0], row[first], row[first + 1], row[first + 2], row[first + 3]) for row in rows]

    async def price_coverage(self, start, end):
        return fake_prices.coverage(start, end, datetime.now(timezone.utc))

    async def price_trades(self, instrument, start, end, limit=500):
        return fake_prices.trades(instrument, start, end, datetime.now(timezone.utc))

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

    async def journal_between(self, start=None, end=None):
        return [entry for entry in self.real_journal if in_range(entry["decided_at"], start, end)]

    async def closed_positions(self, start=None, end=None):
        return [position for position in self.real_positions if in_range(position["execution_time_open"], start, end)]

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
        orders = sorted((o for o in self.orders if o["position_id"] == position_id), key=lambda o: o["order_time"])
        return {"position": position, "orders": [self._linked(order) for order in orders]}

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
        return {"items": [self._linked(order) for order in rows[:limit]], "total": len(rows)}

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
    # Shown in the service list, as on a server where the price recorder runs.
    config["trade"]["config"]["price_recorder"]["enabled"] = True

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

    def live_rules() -> WataRules:
        with open(config_path) as config_file:
            return WataRules.from_config(json.load(config_file))

    events = EventHub()
    backtests_root = os.path.join(REPO_ROOT, "var", "lib", "backtests")
    prices_root = os.path.join(REPO_ROOT, "var", "lib", "price_history")
    os.makedirs(backtests_root, exist_ok=True)
    backtests = BacktestService(
        runs_root=backtests_root,
        prices_root=prices_root,
        rules=live_rules,
        publish=events.publish,
    )

    queries = FakeQueries(real_history(prices_root, live_rules()))
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
        backtests=backtests,
        history=PriceHistory(prices_root),
        events=events,
        notifier=notifier,
        version="dev",
        closers=[backtests.close],
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
