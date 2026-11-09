"""
Positions judged on the index between Saxo's quotes (``position_management.index_between_quotes``): the newest
quote of the index kept from the feed's messages (src/trade/index_feed.py), the position monitor's judgement on
the estimate (``judge_between_quotes``), what its heartbeat and the admin's health say of the index, the setting's
checks, and the backtests' simulator selling on the estimate.
"""

import asyncio
import json
import sys
import types
from dataclasses import replace
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytz

asyncpg_stub = types.ModuleType("asyncpg")
asyncpg_stub.Pool = type("Pool", (), {})
asyncpg_stub.Record = dict
sys.modules.setdefault("asyncpg", asyncpg_stub)
aio_pika_module = sys.modules.setdefault("aio_pika", types.ModuleType("aio_pika"))
if not hasattr(aio_pika_module, "IncomingMessage"):
    aio_pika_module.IncomingMessage = type("IncomingMessage", (), {})

import src.position_monitor as position_monitor  # noqa: E402
from src.admin.config_catalog.checks import warnings  # noqa: E402
from src.admin.health import OK, WARNING, position_monitor_health  # noqa: E402
from src.backtest.clock import TradingClock  # noqa: E402
from src.backtest.data import DayData, IndexSeries, TurboRef, TurboSeries, index_series  # noqa: E402
from src.backtest.settings import BacktestSettings, DictConfig, from_config, with_changes  # noqa: E402
from src.backtest.simulator import SignalEvent, Simulator  # noqa: E402
from src.capital_feed.live import EXCHANGE as FEED_EXCHANGE, LivePublisher, routing_key  # noqa: E402
from src.trade.async_services import AsyncPerformanceMonitor  # noqa: E402
from src.trade.index_estimate import INDEX_FRESH_SECONDS, PRIOR_SENSITIVITY, BetweenQuotes  # noqa: E402
from src.trade.index_feed import EXCHANGE, QUEUE_LENGTH, IndexQuotes, quote_key  # noqa: E402

with open("etc/config_example.json", encoding="utf-8") as config_file:
    EXAMPLE = json.load(config_file)

PARIS = pytz.timezone("Europe/Paris")
CLOCK = TradingClock("Europe/Paris")
DAY = date(2026, 10, 14)
# Wednesday 14 October 2026, 10:00 in Paris.
T0 = PARIS.localize(datetime(2026, 10, 14, 10, 0, 0)).timestamp()
# A long turbo 1,000 points above its barrier at 10 €: 0.01 € per point of distance, so the prior stands.
LONG_TURBO = "NASDAQ 100 Turbo Long 28000 SG"


def message(level: float, moment: float, *, ref: str = "capital/US100", kind: str = "quote") -> bytes:
    """A quote of the index as the feed publishes it, the index at ``level`` (its middle)."""
    return json.dumps({
        "type": kind, "ref": ref, "t": int(moment * 1000) - 40, "rx": int(moment * 1000),
        "bid": level - 0.5, "ask": level + 0.5, "bar": None,
    }).encode()


# ── the index's newest quote ──


def test_the_newest_quote_of_the_index_is_kept_from_the_feeds_messages():
    clock = {"now": 1000.0}
    quotes = IndexQuotes("US100", clock=lambda: clock["now"])

    assert quotes.latest() == (None, None)
    assert quotes.take(message(29000.0, 999.6))
    assert quotes.latest() == (29000.0, 999.6)
    # Another index, a bar, a body that is not JSON, prices that are not prices: left as they come.
    assert not quotes.take(message(1.0, 999.7, ref="capital/DE40"))
    assert not quotes.take(message(1.0, 999.7, kind="bar"))
    assert not quotes.take(b"{not json")
    assert not quotes.take(json.dumps({"type": "quote", "ref": "capital/US100", "bid": True, "ask": 1.0}).encode())
    assert not quotes.take(json.dumps({"type": "quote", "ref": "capital/US100", "bid": -1.0, "ask": 1.0}).encode())
    # A quote older than the one kept does not replace it; one said in the future is taken as of now.
    assert not quotes.take(message(28000.0, 999.0))
    assert quotes.take(message(29010.0, 1005.0))
    assert quotes.latest() == (29010.0, 1000.0)
    assert quotes.received == 2


def test_the_monitor_reads_the_feeds_exchange_and_keys_as_the_feed_publishes_them():
    publisher = LivePublisher(AsyncMock())
    publisher.quote("US100", bid=28999.5, ask=29000.5, quoted_ms=999_960, received_at=1000.0, bar=None)
    _, key, published = publisher._queue.get_nowait()
    quotes = IndexQuotes("US100", clock=lambda: 1000.5)

    assert EXCHANGE == FEED_EXCHANGE
    assert quote_key("US100") == key == routing_key("US100", "quote")
    assert quotes.take(json.dumps(published).encode())
    assert quotes.latest() == (29000.0, 1000.0)


def test_the_monitor_follows_the_index_through_a_queue_of_its_own_without_acknowledgements(monkeypatch):
    declared = {}

    class Queue:
        async def bind(self, exchange, routing_key):
            declared["bound"] = (exchange, routing_key)

        async def consume(self, callback, no_ack=False):
            declared["consumer"], declared["no_ack"] = callback, no_ack

    class Channel:
        async def declare_exchange(self, name, kind, durable=True):
            declared["exchange"] = (name, kind, durable)
            return "exchange"

        async def declare_queue(self, **arguments):
            declared["queue"] = arguments
            return Queue()

    class Connection:
        async def channel(self):
            declared["channels"] = declared.get("channels", 0) + 1
            return Channel()

    monkeypatch.setattr(position_monitor, "aio_pika", SimpleNamespace(ExchangeType=SimpleNamespace(TOPIC="topic")))
    quotes = IndexQuotes("US100", clock=lambda: 1000.0)

    asyncio.run(position_monitor.follow_index(Connection(), quotes))
    asyncio.run(declared["consumer"](SimpleNamespace(body=message(29000.0, 999.8))))

    assert declared["channels"] == 1
    assert declared["exchange"] == ("wata.prices", "topic", False)
    assert declared["queue"] == {
        "exclusive": True, "auto_delete": True, "arguments": {"x-max-length": QUEUE_LENGTH, "x-overflow": "drop-head"},
    }
    assert declared["bound"] == ("exchange", "capital.US100.quote")
    assert declared["no_ack"] is True
    assert quotes.latest() == (29000.0, 999.8)


# ── the monitor's judgement between quotes ──


class Config(DictConfig):
    def __init__(self, config: dict, log_path: str = "."):
        super().__init__(config)
        self._log_path = log_path

    def get_logging_config(self):
        return {"persistant": {"log_path": self._log_path}}


class OpenPosition:
    def __init__(self, action: str = "long", day_percent: float = 0.0):
        self.action, self.day_percent = action, day_percent

    async def get_open_positions_ids_actions(self):
        return [{"position_id": "p1", "action": self.action}]

    async def get_percent_of_the_day(self):
        return self.day_percent

    async def get_max_position_percent(self, position_id):
        return 0.0

    async def update_turbo_position_data(self, position_id, data):
        return None

    async def lower_min_performance(self, position_id, value):
        return None


def configured(*, between: bool = True, feed: bool = True, wait: bool = False, **thresholds) -> dict:
    config = json.loads(json.dumps(EXAMPLE))
    management = config["trade"]["config"]["position_management"]
    management["index_between_quotes"] = between
    management["wait_for_fresh_quote"] = wait
    management["performance_thresholds"].update(thresholds)
    config["trade"]["config"]["capital_feed"]["enabled"] = feed
    for rule in config["trade"]["rules"]:
        if rule["rule_type"] == "day_trading":
            rule["rule_config"]["percent_profit_wanted_per_days"] = 100
    return config


def listed(bid: float, *, open_price: float = 10.05, description: str = LONG_TURBO, opened: float | None = None) -> dict:
    base = {"OpenPrice": open_price, "CanBeClosed": True, "Amount": 10, "Uic": 1}
    if opened is not None:
        base["ExecutionTimeOpen"] = datetime.fromtimestamp(opened, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"p1": {
        "PositionBase": base, "PositionView": {"Bid": bid, "Ask": round(bid + 0.05, 4)},
        "DisplayAndFormat": {"Description": description},
    }}


class Watched:
    """A live position monitor over one position, its clock, the index's quotes and the sales it sends."""

    def __init__(self, config: dict, log_path: str, *, action: str = "long", day_percent: float = 0.0,
                 quotes: bool = True):
        trading_rule = MagicMock()
        trading_rule.get_rule_config.side_effect = lambda rule_type: next(
            rule["rule_config"] for rule in config["trade"]["rules"] if rule["rule_type"] == rule_type
        )
        trading_rule.record_loss = AsyncMock()
        self.trading_rule = trading_rule
        self.monitor = AsyncPerformanceMonitor(
            MagicMock(), MagicMock(), Config(config, log_path), OpenPosition(action, day_percent), trading_rule, AsyncMock(),
        )
        self.sent: list[str] = []

        async def send_close_order(api_pos, *, position_id, action, reason):
            self.sent.append(reason)

        self.monitor._send_close_order = send_close_order
        self.monitor._record_closed_position_in_background = MagicMock()
        self.monitor.position_service.get_open_orders = AsyncMock(return_value=[])
        self.clock = {"now": T0}
        self.quotes = IndexQuotes("US100", clock=lambda: self.clock["now"]) if quotes else None
        self.monitor.index_quotes = self.quotes

    def at(self, moment: float, level: float | None = None) -> None:
        """The clock at ``moment``, and the index quoted there at ``level``."""
        self.clock["now"] = moment
        self.monitor._now = lambda: moment
        if level is not None and self.quotes is not None:
            self.quotes.take(message(level, moment))

    async def quote(self, moment: float, level: float | None, bid: float, **position) -> None:
        self.at(moment, level)
        await self.monitor._evaluate_positions(listed(bid, **position))

    async def second(self, moment: float, level: float | None, bid: float, **position) -> None:
        self.at(moment, level)
        await self.monitor.keep_flat(listed(bid, **position))


def test_an_estimate_past_the_stop_sells_at_once_urgently_and_once(tmp_path):
    watched = Watched(configured(), str(tmp_path))

    async def walk():
        await watched.quote(T0, 29000.0, 10.0)
        # The index 180 points lower 10 s later, Saxo's quote still 10: 10 − 0.0086 × 180 = 8.452, −15.9 %.
        await watched.second(T0 + 10, 28820.0, 10.0)
        await watched.second(T0 + 11, 28820.0, 10.0)
        await asyncio.sleep(0)

    asyncio.run(walk())

    assert watched.sent == ["Performance (Stoploss (-15%) hit at -15.9%; US100 between quotes: 8.452 from 10, 10 s)"]
    intent = watched.monitor.close_intents.get("p1")
    assert intent.urgent and intent.reason == watched.sent[0]
    # Nothing was asked of Saxo before the sale, and the loss starts the cooldown as a stop on Saxo's quote does.
    watched.monitor.position_service.get_open_orders.assert_not_awaited()
    watched.trading_rule.record_loss.assert_awaited()
    # The best and the worst stay Saxo's quotes.
    assert watched.monitor._min_perf_by_position.get("p1", 0.0) == pytest.approx(-0.5)
    assert watched.monitor._max_perf_by_position["p1"] == 0.0


@pytest.mark.parametrize("name, setup", [
    ("switched off", {"config": {"between": False}}),
    ("no feed recording the index", {"config": {"feed": False}}),
    ("no quote of the index", {"quotes": False}),
    ("the index quoted more than 2 s ago", {"stale": True}),
    ("a position whose quote is older than its purchase", {"config": {"wait": True}, "opened": True}),
])
def test_without_all_it_rests_on_nothing_is_judged_between_quotes(name, setup, tmp_path):
    watched = Watched(configured(**setup.get("config", {})), str(tmp_path), quotes=setup.get("quotes", True))
    position = {"opened": T0 - 1} if setup.get("opened") else {}

    async def walk():
        await watched.quote(T0, 29000.0, 10.0, **position)
        if setup.get("stale"):
            watched.at(T0 + 8, 28820.0)
            await watched.second(T0 + 8 + INDEX_FRESH_SECONDS + 0.5, None, 10.0, **position)
        else:
            await watched.second(T0 + 10, 28820.0, 10.0, **position)
        await asyncio.sleep(0)

    asyncio.run(walk())

    assert watched.sent == []


def test_a_position_already_being_sold_is_not_wanted_again_and_a_short_follows_the_index_the_other_way(tmp_path):
    watched = Watched(configured(), str(tmp_path), action="short")
    short = {"description": "NASDAQ 100 Turbo Short 30000 SG"}

    async def walk():
        await watched.quote(T0, 29000.0, 10.0, **short)
        # Up 180 points: a short turbo loses as much.
        await watched.second(T0 + 10, 29180.0, 10.0, **short)
        await watched.second(T0 + 12, 29300.0, 10.0, **short)
        await asyncio.sleep(0)

    asyncio.run(walk())

    assert watched.sent == ["Performance (Stoploss (-15%) hit at -15.9%; US100 between quotes: 8.452 from 10, 10 s)"]


def test_the_trailing_stop_is_judged_on_the_estimate_against_the_best_of_saxo_s_quotes(tmp_path):
    watched = Watched(configured(trailing_stop={"enabled": True, "activation_percent": 5, "drawdown_percent": 8}),
                      str(tmp_path))

    async def walk():
        await watched.quote(T0, 29000.0, 10.0)
        await watched.quote(T0 + 30, 29100.0, 10.86)
        await watched.second(T0 + 40, 28960.0, 10.86)
        await asyncio.sleep(0)

    asyncio.run(walk())

    assert watched.sent == [
        "Performance (Trailing Stop: peak 8.1%, now -3.9% (dd 12.0% >= 8%); US100 between quotes: 9.656 from 10.86, 10 s)"
    ]


# ── what the heartbeat and the health say of it ──


def heartbeat(figures: dict, beat_at: datetime) -> dict:
    return {"beat_at": beat_at, "figures": {"stream_connected": True, "positions_listed": 1, **figures}}


def test_the_heartbeat_says_the_index_followed_and_its_newest_quote():
    monitor = MagicMock()
    monitor.between_quotes = BetweenQuotes(enabled=True)
    monitor.index_epic = "US100"
    monitor.index_quotes = IndexQuotes("US100", clock=lambda: 1_792_000_000.0)
    monitor.index_quotes.take(message(29000.0, 1_791_999_999.0))
    monitor.trading_rule.in_trading_hours.return_value = True
    handler = position_monitor.StreamPositionHandler(monitor, MagicMock(), MagicMock())

    assert handler.index_figures() == {
        "epic": "US100", "quote_at": datetime.fromtimestamp(1_791_999_999.0, timezone.utc).isoformat(),
        "in_trading_hours": True, "received": 1,
    }
    monitor.between_quotes = BetweenQuotes(enabled=False)
    assert handler.index_figures() is None


@pytest.mark.parametrize("index, said", [
    (None, (OK, "Streaming, 1 position at Saxo")),
    ({"epic": "US100", "quote_at": "2026-10-14T08:00:05+00:00", "in_trading_hours": True}, (OK, "Streaming, 1 position at Saxo")),
    ({"epic": "US100", "quote_at": "2026-10-14T07:59:40+00:00", "in_trading_hours": True},
     (WARNING, "No quote of US100 for 20 s: exits wait for Saxo's quotes")),
    ({"epic": "US100", "quote_at": None, "in_trading_hours": True},
     (WARNING, "No quote of US100 received: exits wait for Saxo's quotes")),
    ({"epic": "US100", "quote_at": None, "in_trading_hours": False}, (OK, "Streaming, 1 position at Saxo")),
    ({"epic": None, "quote_at": None, "in_trading_hours": True},
     (WARNING, "Exits between Saxo's quotes are on, but no index is followed: the Capital.com feed records none for "
               "the one indice the trader takes")),
])
def test_health_warns_when_positions_are_judged_between_quotes_without_the_index(index, said):
    beat_at = datetime(2026, 10, 14, 8, 0, 0, tzinfo=timezone.utc)

    assert position_monitor_health(heartbeat({"index_between_quotes": index}, beat_at), beat_at, "Europe/Paris") == said


# ── the setting ──


def test_switched_on_without_an_index_to_follow_the_save_says_so():
    path = "trade.config.position_management.index_between_quotes"

    assert path not in {found["path"] for found in warnings(configured(feed=True))}
    assert path in {found["path"] for found in warnings(configured(feed=False))}
    two = configured(feed=True)
    next(rule for rule in two["trade"]["rules"] if rule["rule_type"] == "allowed_indices")["rule_config"]["indice_ids"]["de40"] = "1"
    assert path in {found["path"] for found in warnings(two)}
    assert path not in {found["path"] for found in warnings(configured(between=False, feed=False))}


def test_a_backtest_judges_between_quotes_as_the_configuration_says_and_a_run_made_before_never():
    on, off = from_config(configured()), from_config(configured(between=False, feed=False))
    before = {name: value for name, value in on.to_dict().items() if name not in ("index_between_quotes", "index_epic")}

    assert (on.index_between_quotes, on.index_epic) == (True, "US100")
    assert (off.index_between_quotes, off.index_epic) == (False, "")
    assert (BacktestSettings.from_dict(before).index_between_quotes, BacktestSettings.from_dict(before).index_epic) == (False, "")
    changed, _, _ = with_changes(off, {"index_between_quotes": True})
    assert changed.index_between_quotes is True


# ── the backtests' simulator ──


def turbo(bars, *, start: float = T0, direction: str = "long", barrier: float = 28_000.0) -> TurboSeries:
    series = TurboSeries(TurboRef(
        instrument_id=1, uic=40_000_001, asset_type="WarrantOpenEndKnockOut", symbol="T1",
        description=f"NASDAQ 100 Turbo {direction.title()} {barrier:g} SG", issuer="SG", barrier=barrier,
        direction=direction, currency="EUR",
    ))
    moment = int(start)
    for bar in bars:
        opened, high, low, close = bar if isinstance(bar, tuple) else (bar, bar, bar, bar)
        series.append(moment, opened, high, low, close, opened + 0.05, close + 0.05)
        moment += 10
    return series


def index_of(levels, *, start: float = T0) -> IndexSeries:
    index = IndexSeries()
    moment = int(start)
    for level in levels:
        opened, high, low, close = level if isinstance(level, tuple) else (level, level, level, level)
        index.append(moment, opened, high, low, close)
        moment += 10
    return index


def day_with(series: TurboSeries, index: IndexSeries | None) -> DayData:
    start, end = CLOCK.day_bounds(DAY)
    return DayData(day=DAY, start=start, end=end, indice="us100", turbos={series.ref.direction: [series]}, index=index)


SIMULATED = replace(
    from_config(configured()), time_scaling_enabled=False, confidence_enabled=False, trailing_enabled=False,
    cooldown_after_loss_minutes=0, max_trades_per_day=0, day_profit_target=50.0, no_entry_above_day_profit=50.0,
    capital=10_000.0, fill_price="standing", wait_for_fresh_quote=False, exit_slippage_percent=0.0,
)


def simulated(day: DayData, settings: BacktestSettings = SIMULATED) -> dict:
    simulator = Simulator(settings, CLOCK)
    entry = SignalEvent(1, "pine", "long", arrived_at=T0 + 1, alert_at=T0 + 1, indice="us100")
    close = SignalEvent(2, "schedule", "close-position", arrived_at=T0 + 600, alert_at=T0 + 600, indice="n/a")
    simulator.run_day(day, [entry, close])
    return simulator.trades[0]


def test_the_simulator_sells_on_the_estimate_in_ten_seconds_without_a_quote_of_the_turbo():
    # Bought at the ask of 10:00:10, 10.05; Saxo's quote stays 10.0 while the index falls 200 points at 10:00:40.
    path = [10.0] * 60
    levels = [29000.0] * 4 + [(29000.0, 29000.0, 28800.0, 28820.0)] + [28820.0] * 55

    trade = simulated(day_with(turbo(path), index_of(levels)))

    assert trade["exit"]["reason"] == "Performance (Stoploss (-15%) hit at -17.61%; US100 between quotes: 8.28 from 10, 35 s)"
    assert trade["exit"]["group"] == "Stop-loss"
    assert trade["exit"]["bid"] == 8.28
    assert trade["exit"]["filled_at"] == "2026-10-14T08:00:50Z"
    # Saxo's quotes were 10 throughout: the best and the worst until the sale are Saxo's.
    assert trade["max_performance_percent"] == 0.0


def test_the_simulator_judges_as_before_without_the_setting_or_the_index():
    # Quoted past the close at T0 + 600, which sells it.
    path = [10.0] * 70
    levels = [29000.0] * 4 + [(29000.0, 29000.0, 28800.0, 28820.0)] + [28820.0] * 55

    for day, settings in ((day_with(turbo(path), index_of(levels)), replace(SIMULATED, index_between_quotes=False)),
                          (day_with(turbo(path), None), SIMULATED)):
        trade = simulated(day, settings)
        assert trade["exit"]["reason"] == "Explicit Close (All)" or "between quotes" not in trade["exit"]["reason"]
        assert trade["exit"]["group"] != "Stop-loss"


def test_the_index_bars_are_the_middle_of_bid_and_ask_and_leave_out_ten_seconds_without_a_quote():
    rows = [
        (100, 28999.5, 29001.5, 28998.5, 29000.5, 29000.5, 29002.5, 28999.5, 29001.5, 31),
        (110, 29000.5, 29000.5, 29000.5, 29000.5, 29001.5, 29001.5, 29001.5, 29001.5, 0),
        (120, 29000.0, 29004.0, 28999.0, 29003.0, None, None, None, None, 12),
    ]

    bars = index_series(rows)

    assert list(bars.ts) == [100, 120]
    assert (bars.open[0], bars.high[0], bars.low[0], bars.close[0]) == (29000.0, 29002.0, 28999.0, 29001.0)
    assert bars.middle(0) == 29000.5
    assert (bars.open[1], bars.close[1]) == (29000.0, 29003.0)
    assert bars.bar_at(110) is None and bars.bar_at(120) == 1


def test_the_prior_used_live_and_in_the_backtests_is_the_one_measured():
    # 0.01 $ a point at the euro's rate of late October 2026 (1.16 $).
    assert PRIOR_SENSITIVITY == pytest.approx(0.01 / 1.16, abs=0.0001)
