"""
A position just bought is not judged on a quote older than its purchase
(src/trade/fresh_quote.py), in the position monitor (``_evaluate_positions``).
The cases are the record's: on 19 October 2026 a long bought at 5.055 while
Saxo still quoted the turbo at 4.627, its stop "hit at -8.47 %", the sale
filled at +0.51 %; a daily profit target "met" on a quote from before a fall.
"""

import asyncio
import json
import sys
import types
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytz

asyncpg_stub = types.ModuleType("asyncpg")
asyncpg_stub.Pool = type("Pool", (), {})
asyncpg_stub.Record = dict
sys.modules.setdefault("asyncpg", asyncpg_stub)
sys.modules.setdefault("aio_pika", types.ModuleType("aio_pika"))

from src.backtest.settings import DictConfig  # noqa: E402
from src.trade.async_services import AsyncPerformanceMonitor  # noqa: E402
from src.trade.fresh_quote import FRESH_AFTER_SECONDS, FreshQuotes, opened_at, wait_for_fresh_quote  # noqa: E402
from src.trade.macro_calendar import NewsRule, read_calendar  # noqa: E402

with open("etc/config_example.json", encoding="utf-8") as config_file:
    EXAMPLE = json.load(config_file)

PARIS = pytz.timezone("Europe/Paris")
# 19 October 2026, 10:38:37 UTC: the purchase.
BOUGHT = datetime(2026, 10, 19, 10, 38, 37, tzinfo=timezone.utc).timestamp()


# ── the module ──


def test_a_position_seen_just_after_its_opening_waits_for_its_bid_to_change():
    quotes = FreshQuotes()

    assert quotes.judged("p1", 4.627, BOUGHT, BOUGHT + 1) is False
    assert quotes.judged("p1", 4.627, BOUGHT, BOUGHT + 3) is False
    assert quotes.waiting["p1"].bid == 4.627 and quotes.waiting["p1"].since == BOUGHT + 1
    # Saxo's next quote: judged from it on, whatever comes after.
    assert quotes.judged("p1", 5.083, BOUGHT, BOUGHT + 4) is True
    assert quotes.judged("p1", 4.627, BOUGHT, BOUGHT + 30) is True
    assert "p1" not in quotes.waiting


def test_a_position_seen_long_after_its_opening_or_whose_opening_is_unknown_is_judged_at_once():
    quotes = FreshQuotes()

    assert quotes.judged("old", 4.627, BOUGHT - FRESH_AFTER_SECONDS, BOUGHT) is True
    assert quotes.judged("unknown", 4.627, None, BOUGHT) is True
    assert quotes.waiting == {}


def test_switched_off_every_bid_is_judged():
    quotes = FreshQuotes(enabled=False)

    assert quotes.judged("p1", 4.627, BOUGHT, BOUGHT + 1) is True
    assert quotes.waiting == {}


def test_what_is_kept_goes_with_the_positions_no_longer_held():
    quotes = FreshQuotes()
    quotes.judged("waiting", 4.6, BOUGHT, BOUGHT + 1)
    quotes.judged("judged", 4.6, None, BOUGHT + 1)
    quotes.judged("kept", 4.6, BOUGHT, BOUGHT + 1)

    quotes.forget_except({"kept"})

    assert set(quotes.waiting) == {"kept"}
    # A position held again under a forgotten id would wait again: nothing of the old one is left.
    assert quotes.judged("judged", 4.6, BOUGHT, BOUGHT + 2) is False


@pytest.mark.parametrize("text, expected", [
    ("2026-10-19T10:38:37Z", BOUGHT),
    ("2026-10-19T10:38:37.1234567Z", BOUGHT + 0.123456),
    ("2026-10-19T10:38:37", BOUGHT),
    ("2026-10-19T12:38:37+02:00", BOUGHT),
    ("yesterday", None),
    ("", None),
    (None, None),
])
def test_the_opening_is_read_as_saxo_writes_it(text, expected):
    position = {"PositionBase": {"ExecutionTimeOpen": text}} if text is not None else {"PositionBase": {}}

    found = opened_at(position)

    assert found == (pytest.approx(expected) if expected is not None else None)


@pytest.mark.parametrize("section, expected", [
    (None, True), ({}, True), ({"wait_for_fresh_quote": True}, True), ({"wait_for_fresh_quote": False}, False),
])
def test_the_monitor_waits_unless_the_configuration_says_false(section, expected):
    assert wait_for_fresh_quote(section) is expected


# ── the position monitor ──


class Config(DictConfig):
    def __init__(self, config: dict, log_path: str):
        super().__init__(config)
        self._log_path = log_path

    def get_logging_config(self):
        return {"persistant": {"log_path": self._log_path}}


class OpenPosition:
    def __init__(self, day_percent: float = 0.0):
        self.day_percent = day_percent
        self.saved = []

    async def get_open_positions_ids_actions(self):
        return [{"position_id": "p1", "action": "long"}]

    async def get_percent_of_the_day(self):
        return self.day_percent

    async def get_max_position_percent(self, position_id):
        return 0.0

    async def update_turbo_position_data(self, position_id, data):
        self.saved.append(data)


def monitor_for(tmp_path, *, wait=None, stoploss=-7, day_target=100, day_percent=0.0, trading_rule=None):
    config = json.loads(json.dumps(EXAMPLE))
    management = config["trade"]["config"]["position_management"]
    management["performance_thresholds"]["stoploss_percent"] = stoploss
    if wait is not None:
        management["wait_for_fresh_quote"] = wait
    for rule in config["trade"]["rules"]:
        if rule["rule_type"] == "day_trading":
            rule["rule_config"]["percent_profit_wanted_per_days"] = day_target
    if trading_rule is None:
        trading_rule = MagicMock()
        trading_rule.get_rule_config.side_effect = lambda rule_type: next(
            rule["rule_config"] for rule in config["trade"]["rules"] if rule["rule_type"] == rule_type
        )
        trading_rule.record_loss = AsyncMock()
        trading_rule.news_rule = None
    rows = OpenPosition(day_percent)
    monitor = AsyncPerformanceMonitor(MagicMock(), MagicMock(), Config(config, str(tmp_path)), rows, trading_rule, AsyncMock())
    sold = []

    async def send_close_order(api_pos, *, position_id, action, reason):
        sold.append(reason)

    monitor._send_close_order = send_close_order
    monitor._record_closed_position_in_background = MagicMock()
    return monitor, sold, rows


def listed(bid: float, *, open_price: float = 5.055, opened: str | None = "2026-10-19T10:38:37Z") -> dict:
    base = {"OpenPrice": open_price, "CanBeClosed": True, "Amount": 10, "Uic": 61025835}
    if opened is not None:
        base["ExecutionTimeOpen"] = opened
    return {"p1": {"PositionBase": base, "PositionView": {"Bid": bid}}}


def ticks(monitor, sold, bids: list[tuple[float, float]], **position) -> list[str | None]:
    """What the monitor sold at each (moment, bid), in order."""
    decided = []

    async def run():
        for moment, bid in bids:
            monitor._now = lambda moment=moment: moment
            before = len(sold)
            await monitor._evaluate_positions(listed(bid, **position))
            decided.append(sold[-1] if len(sold) > before else None)
        await asyncio.sleep(0)

    asyncio.run(run())
    return decided


def test_the_stop_of_19_october_is_not_taken_on_a_bid_older_than_the_purchase(tmp_path):
    monitor, sold, rows = monitor_for(tmp_path)

    # 4.627 was Saxo's quote from 10:38:10; its next one, at 10:38:40, was 5.083 (+0.55 % on the 5.055 paid).
    decided = ticks(monitor, sold, [(BOUGHT + 1, 4.627), (BOUGHT + 2, 4.627), (BOUGHT + 4, 5.083)])

    assert decided == [None, None, None]
    # Its best result is that of the quote after the purchase, not the one before.
    assert monitor._max_perf_by_position["p1"] == pytest.approx(0.55)
    assert monitor.live_values()["p1"][:2] == (5.083, 0.55)


def test_the_monitor_of_before_sold_on_it(tmp_path):
    monitor, sold, _ = monitor_for(tmp_path, wait=False)

    decided = ticks(monitor, sold, [(BOUGHT + 1, 4.627)])

    assert decided == ["Performance (Stoploss (-7%) hit at -8.47%)"]


def test_a_daily_target_is_not_met_on_a_quote_from_before_a_fall(tmp_path):
    # 5 October 2026 at the US open: a short bought at 3.923 shown at +4.23 %, sold at -1.96 %.
    monitor, sold, _ = monitor_for(tmp_path, day_target=0.5)
    opened = "2026-10-05T13:31:09Z"
    at = datetime(2026, 10, 5, 13, 31, 9, tzinfo=timezone.utc).timestamp()

    decided = ticks(monitor, sold, [(at + 2, 4.089), (at + 25, 3.85)], open_price=3.923, opened=opened)

    assert decided == [None, None]


def test_a_stop_after_saxos_next_quote_is_taken_at_once(tmp_path):
    monitor, sold, _ = monitor_for(tmp_path)

    decided = ticks(monitor, sold, [(BOUGHT + 1, 5.05), (BOUGHT + 31, 4.6)])

    assert decided == [None, "Performance (Stoploss (-7%) hit at -9.0%)"]


def test_a_position_held_since_before_the_monitor_started_is_judged_on_its_first_bid(tmp_path):
    monitor, sold, _ = monitor_for(tmp_path)

    decided = ticks(monitor, sold, [(BOUGHT + 600, 4.6)])

    assert decided == ["Performance (Stoploss (-7%) hit at -9.0%)"]


def test_a_position_waiting_for_its_quote_is_still_sold_before_a_us_release(tmp_path):
    # The CPI of 14 October 2026 at 14:30 in Paris, nothing held from ten minutes before.
    trading_rule = MagicMock()
    trading_rule.get_rule_config.side_effect = lambda rule_type: {"close_position_time": "21:55", "percent_profit_wanted_per_days": 100}
    trading_rule.record_loss = AsyncMock()
    trading_rule.news_rule = NewsRule(flatten_minutes_before=10)
    trading_rule.news_calendar = read_calendar()
    monitor, sold, _ = monitor_for(tmp_path, trading_rule=trading_rule)
    moment = PARIS.localize(datetime(2026, 10, 14, 14, 21)).timestamp()
    opened = datetime.fromtimestamp(moment - 2, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    decided = ticks(monitor, sold, [(moment, 5.0)], opened=opened)

    assert decided == ["Performance (Flat before CPI (14:30))"]
    assert "p1" in monitor.fresh_quotes.waiting
