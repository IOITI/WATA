"""
The backtest's rules beside the live code, on the same inputs: the trader's
checks on an entry (``handle_trading_signal`` with a real ``TradingRule``),
its sizing (``AsyncTradingOrchestrator``) and the position monitor's exits
(``AsyncPerformanceMonitor._evaluate_positions``), each run with the clock
frozen at the moment the backtest decides. A change to the live rules that is
not made in src/backtest/rules.py fails here.
"""

import asyncio
import json
import sys
import types
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytz

asyncpg_stub = types.ModuleType("asyncpg")
asyncpg_stub.Pool = type("Pool", (), {})
asyncpg_stub.Record = dict
sys.modules.setdefault("asyncpg", asyncpg_stub)
sys.modules.setdefault("aio_pika", types.ModuleType("aio_pika"))

import src.trade.async_services as async_services  # noqa: E402
import src.trade.rules as live_rules  # noqa: E402
import src.trader as trader  # noqa: E402
from src.backtest.clock import TradingClock  # noqa: E402
from src.backtest.rules import entry_refusal, exit_decision, performance, position_scale, units_for  # noqa: E402
from src.backtest.settings import DictConfig, from_config  # noqa: E402
from src.trade.async_services import AsyncPerformanceMonitor, AsyncTradingOrchestrator  # noqa: E402
from src.trade.exceptions import InsufficientFundsException  # noqa: E402
from src.trade.rules import TradingRule  # noqa: E402
from src.trade.macro_calendar import flat_reason, read_calendar  # noqa: E402
from src.backtest.simulator import Simulator  # noqa: E402
from src.trade.exceptions import NoTurbosAvailableException  # noqa: E402

with open("etc/config_example.json", encoding="utf-8") as config_file:
    EXAMPLE = json.load(config_file)

PARIS = pytz.timezone("Europe/Paris")
CLOCK = TradingClock("Europe/Paris")


def frozen_at(moment: datetime):
    """A ``datetime`` whose ``now`` is ``moment``."""

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment.astimezone(tz) if tz is not None else moment.astimezone(timezone.utc).replace(tzinfo=None)

    return Frozen


def config_with(**rule_changes) -> dict:
    config = json.loads(json.dumps(EXAMPLE))
    for rule in config["trade"]["rules"]:
        rule["rule_config"].update(rule_changes.get(rule["rule_type"], {}))
    return config


class Config(DictConfig):
    def __init__(self, config: dict, log_path: str = "."):
        super().__init__(config)
        self._log_path = log_path

    def get_logging_config(self):
        return {"persistant": {"log_path": self._log_path}}


# ── the checks on an entry ──


class Positions:
    def __init__(self, open_actions=(), day_percent=0.0, trades_today=0):
        self.open_actions, self.day_percent, self.trades_today = list(open_actions), day_percent, trades_today

    async def get_open_positions_ids_actions(self):
        return [{"position_id": f"p{number}", "action": action} for number, action in enumerate(self.open_actions)]

    async def get_percent_of_the_day(self):
        return self.day_percent

    async def get_today_trade_count(self):
        return self.trades_today


class RiskState:
    def __init__(self, last_loss: datetime | None):
        self.last_loss = last_loss

    async def get_last_loss_timestamp(self):
        return self.last_loss

    async def get_entries_paused_since(self):
        return None


class Journal:
    def __init__(self):
        self.outcomes = []

    def record(self, signal, outcome, reason=None, **ids):
        self.outcomes.append((outcome, reason))


def live_outcome(config: dict, *, now: datetime, action: str, indice: str, alert_at: datetime, positions: Positions,
                 last_loss: datetime | None, confidence: float | None) -> tuple[str, str | None]:
    orchestrator = MagicMock()
    orchestrator.reserve_cash_percent = 0
    orchestrator.execute_trade_signal = AsyncMock(return_value={
        "order_details": {"order_id": "o1"}, "position_details": {"position_id": "p9"}, "selected_turbo_info": {},
    })
    orchestrator.place_protective_stop = AsyncMock(return_value=None)
    monitor = MagicMock()
    monitor.close_managed_positions_by_criteria = AsyncMock(
        return_value={"closed_initiated_count": 0, "errors_count": 0, "closed_position_ids": []},
    )
    monitor.wait_for_positions_closed = AsyncMock(return_value=True)
    telegram = MagicMock()
    telegram.send = AsyncMock()
    journal = Journal()
    signal = {
        "action": action, "indice": indice, "signal_uuid": "u1",
        "signal_timestamp": alert_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "alert_timestamp": alert_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if confidence is not None:
        signal["confidence"] = confidence
    rule = TradingRule(Config(config), positions, RiskState(last_loss))
    with patch.object(live_rules, "datetime", frozen_at(now)), patch.object(trader, "TelegramMessageComposer"):
        asyncio.run(trader.handle_trading_signal(
            signal, orchestrator, monitor, rule, positions, "exchange", telegram, journal=journal,
        ))
    return journal.outcomes[0]


# Wednesday 14 October 2026, in Paris.
def paris(clock_time: str, day: int = 14) -> datetime:
    hour, minute, second = (int(part) for part in (clock_time + ":00")[:8].split(":"))
    return PARIS.localize(datetime(2026, 10, day, hour, minute, second))


SCENARIOS = [
    # (name, now, changes of the rules, action, indice, open actions, day %, minutes since a loss, trades today, confidence, alert age s)
    ("taken", "10:00", {}, "long", "us100", [], 0.0, None, 0, None, 1),
    ("taken with a position the other way", "10:00", {}, "short", "us100", ["long"], 0.5, None, 3, 0.9, 2),
    ("too old", "10:00", {}, "long", "us100", [], 0.0, None, 0, None, 3601),
    ("old but not too old", "10:00", {}, "long", "us100", [], 0.0, None, 0, None, 3600),
    ("before the market", "07:59:59", {}, "long", "us100", [], 0.0, None, 0, None, 1),
    ("after the market", "22:00", {}, "long", "us100", [], 0.0, None, 0, None, 1),
    ("risky minute", "21:54", {}, "long", "us100", [], 0.0, None, 0, None, 1),
    ("just before the risky minute", "21:53:59", {}, "long", "us100", [], 0.0, None, 0, None, 1),
    ("closed date", "10:00", {"market_closed_dates": {"market_closed_dates": ["14/10/2026"]}}, "long", "us100", [], 0.0, None, 0, None, 1),
    ("unknown indice", "10:00", {}, "long", "de40", [], 0.0, None, 0, None, 1),
    ("duplicate", "10:00", {}, "long", "us100", ["long"], 0.0, None, 0, None, 1),
    ("day above the limit", "10:00", {}, "long", "us100", [], 1.25, None, 0, None, 1),
    ("day under the loss", "10:00", {}, "long", "us100", [], -25.0, None, 0, None, 1),
    ("cooldown", "10:00", {}, "long", "us100", [], -0.5, 4, 1, None, 1),
    ("cooldown over", "10:00", {}, "long", "us100", [], -0.5, 11, 1, None, 1),
    ("most entries", "10:00", {}, "long", "us100", [], 0.0, None, 8, None, 1),
    ("low confidence", "10:00", {}, "long", "us100", [], 0.0, None, 0, 0.2, 1),
    ("confidence enough", "10:00", {}, "long", "us100", [], 0.0, None, 0, 0.3, 1),
    ("several reasons, the first wins", "07:00", {}, "long", "de40", ["long"], 5.0, 1, 9, 0.1, 1),
]


@pytest.mark.parametrize(
    "name, now, changes, action, indice, open_actions, day_percent, loss_minutes, trades_today, confidence, age", SCENARIOS,
    ids=[scenario[0] for scenario in SCENARIOS],
)
def test_an_entry_is_refused_for_the_same_reason_as_live(
    name, now, changes, action, indice, open_actions, day_percent, loss_minutes, trades_today, confidence, age,
):
    config = config_with(**changes)
    moment = paris(now)
    alert_at = moment - timedelta(seconds=age)
    last_loss = None if loss_minutes is None else moment - timedelta(minutes=loss_minutes)
    positions = Positions(open_actions, day_percent, trades_today)

    live = live_outcome(config, now=moment, action=action, indice=indice, alert_at=alert_at, positions=positions,
                        last_loss=last_loss, confidence=confidence)
    refusal = entry_refusal(
        from_config(config), CLOCK, action=action, indice=indice, decided_at=moment.timestamp(),
        alert_at=alert_at.timestamp(), open_directions=open_actions, day_percent=day_percent,
        last_loss_at=None if last_loss is None else last_loss.timestamp(), entries_today=trades_today,
        confidence=confidence,
    )

    mirrored = ("executed", None) if refusal is None else ("refused", refusal.reason)
    assert mirrored == live


def config_with_news(**news) -> dict:
    config = json.loads(json.dumps(EXAMPLE))
    config["trade"]["rules"].append({"rule_name": "No entry around US releases", "rule_type": "macro_news", "rule_config": news})
    return config


NEWS_SCENARIOS = [
    # (name, the moment in Paris, the rule, taken)
    ("just before CPI's window", (2026, 10, 14, 14, 24, 59), {}, True),
    ("CPI's window opens", (2026, 10, 14, 14, 25), {}, False),
    ("CPI's window about to close", (2026, 10, 14, 14, 44, 59), {}, False),
    ("CPI's window over", (2026, 10, 14, 14, 45), {}, True),
    ("FOMC's press conference", (2026, 10, 28, 19, 44), {}, False),
    ("FOMC's 45 minutes over", (2026, 10, 28, 19, 45), {}, True),
    ("CPI not named", (2026, 10, 14, 14, 30), {"events": ["FOMC"]}, True),
    ("a wider window", (2026, 10, 14, 14, 15), {"minutes_before": 15}, False),
    ("New York on summer time first", (2026, 3, 11, 13, 30), {}, False),
    ("PCE past the calendar's end", (2027, 1, 29, 14, 30), {}, True),
]


@pytest.mark.parametrize("name, moment, news, taken", NEWS_SCENARIOS, ids=[scenario[0] for scenario in NEWS_SCENARIOS])
def test_an_entry_around_a_us_release_is_refused_as_live(name, moment, news, taken):
    config = config_with_news(**news)
    now = PARIS.localize(datetime(*moment))

    live = live_outcome(config, now=now, action="long", indice="us100", alert_at=now - timedelta(seconds=1),
                        positions=Positions(), last_loss=None, confidence=None)
    refusal = entry_refusal(
        from_config(config), CLOCK, action="long", indice="us100", decided_at=now.timestamp(),
        alert_at=now.timestamp() - 1, open_directions=[], day_percent=0.0, last_loss_at=None, entries_today=0,
        confidence=None, calendar=read_calendar(),
    )

    mirrored = ("executed", None) if refusal is None else ("refused", refusal.reason)
    assert mirrored == live and (live[0] == "executed") == taken


# ── the size of an order ──


@pytest.mark.parametrize("now, confidence", [
    ("09:00", None), ("14:45", 0.4), ("15:59", 0.79), ("16:00", 0.8), ("21:30", 1.95), ("07:30", 2.5), ("12:00", 0.0),
])
@pytest.mark.parametrize("spending_power, ask", [(10_000.0, 5.05), (3_000.0, 13.9), (9.0, 5.0), (12.0, 5.0)])
def test_an_order_is_scaled_and_sized_as_live(now, confidence, spending_power, ask):
    config = config_with()
    moment = paris(now)
    orchestrator = AsyncTradingOrchestrator(MagicMock(), MagicMock(), MagicMock(), Config(config), MagicMock(), MagicMock())
    settings = from_config(config)

    with patch.object(async_services, "datetime", frozen_at(moment)):
        live_scale = orchestrator._calculate_position_scale(confidence)
    try:
        live_units = orchestrator._calculate_bid_amount({"selected_instrument": {"latest_ask": ask}}, spending_power, live_scale)
    except InsufficientFundsException:
        live_units = 0

    scale = position_scale(settings, CLOCK.minute_of_day(moment.timestamp()), confidence)
    assert scale == pytest.approx(live_scale)
    assert units_for(settings, spending_power, ask, scale) == live_units


def test_a_cash_reserve_leaves_its_share_aside_as_live():
    config = config_with()
    config["trade"]["config"]["buying_power"]["reserve_cash_percent"] = 20
    orchestrator = AsyncTradingOrchestrator(MagicMock(), MagicMock(), MagicMock(), Config(config), MagicMock(), MagicMock())

    live = orchestrator._calculate_bid_amount({"selected_instrument": {"latest_ask": 5.05}}, 10_000.0, 0.75)

    assert units_for(from_config(config), 10_000.0, 5.05, 0.75) == live


# ── what makes the monitor sell ──


class OpenPosition:
    def __init__(self, day_percent: float):
        self.day_percent = day_percent

    async def get_open_positions_ids_actions(self):
        return [{"position_id": "p1", "action": "long"}]

    async def get_percent_of_the_day(self):
        return self.day_percent

    async def get_max_position_percent(self, position_id):
        return 0.0

    async def update_turbo_position_data(self, position_id, data):
        return None


def live_exits(config: dict, open_price: float, bids: list[float], day_percent: float, log_path: str) -> list[str | None]:
    """What the live monitor decides at each bid, until it sells."""
    trading_rule = MagicMock()
    trading_rule.get_rule_config.side_effect = lambda rule_type: next(
        rule["rule_config"] for rule in config["trade"]["rules"] if rule["rule_type"] == rule_type
    )
    trading_rule.record_loss = AsyncMock()
    monitor = AsyncPerformanceMonitor(
        MagicMock(), MagicMock(), Config(config, log_path), OpenPosition(day_percent), trading_rule, AsyncMock(),
    )
    sent = []

    async def send_close_order(api_pos, *, position_id, action, reason):
        sent.append(reason)

    monitor._send_close_order = send_close_order
    monitor._record_closed_position_in_background = MagicMock()

    async def ticks():
        decided = []
        for bid in bids:
            before = len(sent)
            await monitor._evaluate_positions({"p1": {
                "PositionBase": {"OpenPrice": open_price, "CanBeClosed": True}, "PositionView": {"Bid": bid},
            }})
            decided.append(sent[-1] if len(sent) > before else None)
            if sent:
                break
        await asyncio.sleep(0)
        return decided

    return asyncio.run(ticks())


def mirrored_exits(config: dict, open_price: float, bids: list[float], day_percent: float) -> list[str | None]:
    settings = from_config(config)
    best = 0.0
    decided = []
    for bid in bids:
        perf = performance(open_price, bid)
        decision = exit_decision(settings, open_price=open_price, perf=perf, best_before=best, day_percent=day_percent)
        decided.append(None if decision is None else f"Performance ({decision.reason})")
        if decision is not None:
            break
        best = max(best, perf)
    return decided


@pytest.mark.parametrize("changes, open_price, bids, day_percent", [
    # The stop-loss, exactly at its level and beyond.
    ({}, 5.0, [5.0, 4.6, 4.25], 0.0),
    ({}, 5.0, [5.0, 4.24], 0.0),
    # The take-profit, the day's target first when the day already made some.
    ({"day_trading": {"percent_profit_wanted_per_days": 100}}, 5.0, [5.2, 7.9, 8.0], 0.0),
    ({}, 5.0, [5.02, 5.05, 5.07, 5.09], 0.0),
    ({}, 5.0, [5.02, 5.04], 1.5),
    # The trailing stop: armed at +5 %, sold 8 points under the best.
    ({"day_trading": {"percent_profit_wanted_per_days": 100}}, 5.0, [5.1, 5.3, 5.6, 5.3, 5.2, 5.19], 0.0),
    ({"day_trading": {"percent_profit_wanted_per_days": 100}}, 5.0, [5.1, 5.24, 4.9, 4.85], 0.0),
    # A day already lost: the target is far.
    ({}, 6.13, [6.2, 6.4, 6.3], -3.0),
])
def test_the_monitor_sells_at_the_same_bid_for_the_same_reason_as_live(changes, open_price, bids, day_percent, tmp_path):
    config = config_with(**changes)

    assert mirrored_exits(config, open_price, bids, day_percent) == live_exits(config, open_price, bids, day_percent, str(tmp_path))


def test_the_trailing_stop_switched_off_is_never_the_reason(tmp_path):
    config = config_with(day_trading={"percent_profit_wanted_per_days": 100})
    config["trade"]["config"]["position_management"]["performance_thresholds"]["trailing_stop"]["enabled"] = False
    bids = [5.1, 5.6, 5.1, 5.0, 4.5, 4.25]

    decided = mirrored_exits(config, 5.0, bids, 0.0)

    assert decided == live_exits(config, 5.0, bids, 0.0, str(tmp_path))
    assert decided[-1].startswith("Performance (Stoploss")


# ── sold before a US release ──


@pytest.mark.parametrize("flatten, clock_time, sold", [
    # CPI of 14 October 2026, 14:30 in Paris.
    (10, "14:19:59", False),
    (10, "14:20:00", True),
    (10, "14:44:59", True),
    (10, "14:45:00", False),
    (0, "14:29:00", False),
])
def test_a_position_is_sold_before_a_us_release_at_the_same_moment_and_for_the_same_reason_as_live(
    flatten, clock_time, sold, tmp_path,
):
    config = config_with()
    config["trade"]["rules"].append(
        {"rule_name": "No entry around US releases", "rule_type": "macro_news", "rule_config": {"flatten_minutes_before": flatten}},
    )
    moment = paris(clock_time).timestamp()

    rule = TradingRule(Config(config), Positions(), RiskState(None))
    monitor = AsyncPerformanceMonitor(
        MagicMock(), MagicMock(), Config(config, str(tmp_path)), OpenPosition(0.0), rule, AsyncMock(),
    )
    sent = []

    async def send_close_order(api_pos, *, position_id, action, reason):
        sent.append(reason)

    monitor._send_close_order = send_close_order
    monitor._record_closed_position_in_background = MagicMock()
    monitor.position_service.get_open_orders = AsyncMock(return_value=[])
    monitor._now = lambda: moment
    asyncio.run(monitor.keep_flat({"p1": {
        "PositionBase": {"OpenPrice": 5.0, "CanBeClosed": True, "Amount": 10, "Uic": 1}, "PositionView": {"Bid": 5.0},
    }}))

    simulator = Simulator(from_config(config), CLOCK)
    window = simulator._flat_window(moment)
    mirrored = [] if window is None else [f"Performance ({flat_reason(window, simulator.settings.timezone)})"]
    assert sent == mirrored
    assert bool(sent) is sold


# ── an entry that failed, with a cash reserve ──


def test_with_a_cash_reserve_an_entry_that_finds_no_turbo_sells_the_other_side_as_live():
    from src.backtest.clock import TradingClock as Clock
    from src.backtest.data import DayData, TurboRef, TurboSeries, sort_turbos
    from src.backtest.simulator import SignalEvent

    config = config_with()
    config["trade"]["config"]["buying_power"]["reserve_cash_percent"] = 20

    # Live: the long held is sold though the short found no turbo.
    orchestrator = MagicMock()
    orchestrator.reserve_cash_percent = 20
    orchestrator.execute_trade_signal = AsyncMock(side_effect=NoTurbosAvailableException("No turbos in price range 4-15."))
    monitor = MagicMock()
    monitor.close_managed_positions_by_criteria = AsyncMock(return_value={
        "closed_initiated_count": 1, "errors_count": 0, "closed_position_ids": ["p0"], "not_closed": [], "not_listed": [],
    })
    telegram = MagicMock()
    telegram.send = AsyncMock()
    now = paris("09:30")
    signal = {
        "action": "short", "indice": "us100", "signal_uuid": "u2",
        "signal_timestamp": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "alert_timestamp": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    rule = TradingRule(Config(config), Positions(), RiskState(None))
    with patch.object(live_rules, "datetime", frozen_at(now)), patch.object(trader, "TelegramMessageComposer"):
        asyncio.run(trader.handle_trading_signal(signal, orchestrator, monitor, rule, Positions(), "exchange", telegram))
    live_sold = monitor.close_managed_positions_by_criteria.await_count == 1

    # The backtest: a long held, a short signal with no short turbo recorded.
    clock = Clock("Europe/Paris")
    day = paris("08:00").date()
    turbo = TurboSeries(TurboRef(instrument_id=2, uic=40_000_002, asset_type="WarrantOpenEndKnockOut", symbol="T2",
                                 description="NASDAQ 100 Turbo Long 24800 SG", issuer="SG", barrier=24_800,
                                 direction="long", currency="EUR"))
    for step in range(6 * 60 * 6):
        moment = int(clock.epoch(day, "08:00")) + 10 * step
        turbo.append(moment, 5.0, 5.0, 5.0, 5.0, 5.05, 5.05)
    start, end = clock.day_bounds(day)
    data = DayData(day=day, start=start, end=end, indice="us100", turbos=sort_turbos({"long": [turbo]}))
    simulator = Simulator(from_config(config), clock)
    simulator.run_day(data, [
        SignalEvent(1, "pine", "long", arrived_at=clock.epoch(day, "08:30"), alert_at=clock.epoch(day, "08:30"), indice="us100"),
        SignalEvent(2, "pine", "short", arrived_at=clock.epoch(day, "09:30"), alert_at=clock.epoch(day, "09:30"), indice="us100"),
    ])
    mirrored_sold = simulator.trades[0]["exit"]["reason"] == "Explicit Close (All)" and simulator.signals[1]["closed_trade_ids"]

    assert live_sold and bool(mirrored_sold)


# ── the US market's days, and hours in another timezone ──

NEW_YORK_HOURS = {"market_hours": {"timezone": "America/New_York", "trading_start_hour": 10, "trading_end_hour": 16,
                                   "risky_trading_start_hour": 15, "risky_trading_start_minute": 54},
                  "day_trading": {"close_position_time": "15:55"}}
LONG_RISKY_WINDOW = {"market_hours": {"risky_trading_start_hour": 20, "risky_trading_start_minute": 30}}

MARKET_DAY_SCENARIOS = [
    # (name, the moment in Paris, changes of the rules, taken)
    ("Thanksgiving", (2026, 11, 26, 15, 0), {}, False),
    ("the day after, before its early close's last entry", (2026, 11, 27, 18, 53, 59), {}, True),
    ("the day after, from its early close less the risky minutes", (2026, 11, 27, 18, 54), {}, False),
    ("Christmas Eve's morning", (2026, 12, 24, 10, 0), {}, True),
    ("an ordinary day", (2026, 11, 25, 18, 54), {}, True),
    ("New York's hours, before 10h there", (2026, 10, 14, 15, 59), NEW_YORK_HOURS, False),
    ("New York's hours, from 10h there", (2026, 10, 14, 16, 0), NEW_YORK_HOURS, True),
    ("New York's risky minutes", (2026, 10, 14, 21, 54), NEW_YORK_HOURS, False),
    ("New York's hours the week the clocks differ", (2026, 10, 27, 15, 0), NEW_YORK_HOURS, True),
    ("a risky window over an hour, after its first hour", (2026, 10, 14, 21, 10), LONG_RISKY_WINDOW, False),
    ("just before such a window", (2026, 10, 14, 20, 29), LONG_RISKY_WINDOW, True),
]


@pytest.mark.parametrize("name, moment, changes, taken", MARKET_DAY_SCENARIOS, ids=[scenario[0] for scenario in MARKET_DAY_SCENARIOS])
def test_the_us_markets_days_and_the_hours_refuse_an_entry_as_live(name, moment, changes, taken):
    from src.backtest.simulator import us_market_calendar

    config = config_with(**changes)
    now = PARIS.localize(datetime(*moment))

    live = live_outcome(config, now=now, action="long", indice="us100", alert_at=now - timedelta(seconds=1),
                        positions=Positions(), last_loss=None, confidence=None)
    refusal = entry_refusal(
        from_config(config), CLOCK, action="long", indice="us100", decided_at=now.timestamp(),
        alert_at=now.timestamp() - 1, open_directions=[], day_percent=0.0, last_loss_at=None, entries_today=0,
        confidence=None, market_calendar=us_market_calendar(),
    )

    mirrored = ("executed", None) if refusal is None else ("refused", refusal.reason)
    assert mirrored == live and (live[0] == "executed") == taken


@pytest.mark.parametrize("day, changes, sold_at", [
    # 21:55 in Paris on an ordinary day; on the day after Thanksgiving, 5 minutes before 13:00 New York (19:00 Paris).
    ((2026, 11, 25), {}, (2026, 11, 25, 21, 55)),
    ((2026, 11, 27), {}, (2026, 11, 27, 18, 55)),
    # In New York's hours: 15:55 there, whichever side changed its clocks.
    ((2026, 10, 27), NEW_YORK_HOURS, (2026, 10, 27, 20, 55)),
    ((2026, 11, 3), NEW_YORK_HOURS, (2026, 11, 3, 21, 55)),
])
def test_the_days_close_comes_when_the_scheduler_sends_it(day, changes, sold_at):
    from src.backtest.signals import closing_signals
    from src.backtest.simulator import us_market_calendar

    config = config_with(**changes)
    rule = TradingRule(Config(config), Positions(), RiskState(None))
    noon = PARIS.localize(datetime(*day, 12, 0)).timestamp()

    (event,) = closing_signals([datetime(*day).date()], from_config(config), CLOCK, first_id=1,
                               market_calendar=us_market_calendar())

    expected = PARIS.localize(datetime(*sold_at)).timestamp()
    assert rule.sale_moment(noon) == expected
    assert event.arrived_at == expected + 0.5
