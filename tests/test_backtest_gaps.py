"""
What a backtest does when the recorded quotes of a turbo stop: the cases of the
review of the backtests' reliability (November 2026), each set against what the
live trader and position monitor do in the same case.

Saxo's stream reaches the price recorder in three ways when a turbo is not
quoted, and the recorder writes each its own way (src/price_recorder/bars.py):

- Saxo says the turbo has no market (NoMarket, NoAccess, Closed, or no bid):
  no bar until it is quoted again, a hole the data quality page shows;
- Saxo simply sends nothing for it while other instruments are quoted: its
  last quote is written again every ten seconds, unchanged, and only the index
  moving meanwhile tells it from a quote that did not move;
- nothing at all comes for 30 s: the recorder's own gap (``feed_ok`` false),
  no bar of any instrument, and a first bar after it that opens on the quote
  from before.

The days below are made by the recorder's own ``BarBuilder`` from what the
stream would have handed it, then traded by the simulator.
"""

import asyncio
import csv
import gzip
import json
import os
import sys
import types
from dataclasses import replace
from datetime import date

import pytest

# The web server's store is imported for the parity of a busy trader's reading; its package reads a configuration.
os.environ.setdefault("WATA_CONFIG_PATH", "tests/test_config.json")
# The live helpers are imported from the trading code, which imports the database layer.
asyncpg_stub = types.ModuleType("asyncpg")
asyncpg_stub.Pool = type("Pool", (), {})
asyncpg_stub.Record = dict
sys.modules.setdefault("asyncpg", asyncpg_stub)
sys.modules.setdefault("aio_pika", types.ModuleType("aio_pika"))

from src.backtest.clock import TradingClock, utc_iso  # noqa: E402
from src.backtest.data import DayData, FileData, IndexSeries, TurboRef, TurboSeries, sort_turbos  # noqa: E402
from src.backtest.execution import Noise  # noqa: E402
from src.backtest.figures import rests_on  # noqa: E402
from src.backtest.reconciliation import reconcile  # noqa: E402
from src.backtest.run import TESTED, _day_status  # noqa: E402
from src.backtest.selection import NotSelected, select_turbo  # noqa: E402
from src.backtest.settings import from_config  # noqa: E402
from src.backtest.simulator import SignalEvent, Simulator, stored  # noqa: E402
from src.price_recorder.bars import BarBuilder, Quote  # noqa: E402
from src.price_recorder.export import COLUMNS  # noqa: E402
from src.trade.async_services import filter_tradable_market_items  # noqa: E402
from src.web_server.signal_store import SignalStore  # noqa: E402

CLOCK = TradingClock("Europe/Paris")
# A Wednesday, with CPI at 14:30 in Paris, and the Thursday after it.
DAY = date(2026, 10, 14)
NEXT_DAY = date(2026, 10, 15)
INDEX = "index"

with open("etc/config_example.json", encoding="utf-8") as config_file:
    EXAMPLE = json.load(config_file)

# The live rules as a new run takes them: orders priced at the turbo's next quote, a position judged once its bid
# changed after its purchase, the monitor's exits a little under the bid that decided them. Without what makes a
# test's arithmetic long: no scaling, no trailing stop, no cooldown.
NEW_RUN = replace(
    from_config(EXAMPLE), time_scaling_enabled=False, confidence_enabled=False, trailing_enabled=False,
    cooldown_after_loss_minutes=0, max_trades_per_day=0, day_profit_target=50.0, no_entry_above_day_profit=50.0,
    capital=10_000.0,
)
# Room in the account for a next quote dearer than the ask an order was sized on, which Saxo would refuse.
ROOMY = replace(NEW_RUN, funds_percent=80)


def at(clock_time: str, day: date = DAY) -> float:
    return CLOCK.epoch(day, clock_time)


def ref(instrument_id: int, direction: str = "long", barrier: float = 28_555.0) -> TurboRef:
    # A long at 28,555 is worth about 5 € with the index at 29,000, as Saxo's are (0.0087 € a point, 130 points beyond).
    return TurboRef(
        instrument_id=instrument_id, uic=40_000_000 + instrument_id, asset_type="WarrantOpenEndKnockOut",
        symbol=f"T{instrument_id}", description=f"NASDAQ 100 Turbo {direction.title()} {barrier:g} Saxo", issuer="Saxo",
        barrier=barrier, direction=direction, currency="EUR",
    )


class Stream:
    """What Saxo's stream hands the price recorder, in the recorder's own terms: a quote, or "not quotable"."""

    def __init__(self, *, day: date = DAY, index_from: str = "08:00", index_to: str = "22:00"):
        self.day = day
        self.rows: list[tuple[float, object, Quote | None]] = []
        # The index, quoted every five seconds: what keeps the recorder's feed alive.
        self.index(index_from, index_to)

    def index(self, start: str, end: str) -> None:
        moment, stop = at(start, self.day), at(end, self.day)
        while moment < stop:
            self.rows.append((moment, INDEX, Quote(bid=29_000.0, ask=29_000.0)))
            moment += 5

    def quote(self, key: int, clock_time: str, bid: float, spread: float = 0.05) -> None:
        self.rows.append((at(clock_time, self.day), key, Quote(bid=bid, ask=round(bid + spread, 4))))

    def no_market(self, key: int, clock_time: str) -> None:
        self.rows.append((at(clock_time, self.day), key, None))

    def recorded(self, until: str, *refs: TurboRef) -> tuple[dict[int, TurboSeries], set[int]]:
        """The turbos' ten-second bars as the recorder writes them, and the buckets it listened in."""
        builder = BarBuilder()
        for moment, key, quote in sorted(self.rows, key=lambda row: row[0]):
            builder.update(key, quote, moment)
        turbos = {one.instrument_id: TurboSeries(one) for one in refs}
        covered = set()
        for bucket in builder.close_until(at(until, self.day)):
            if bucket.feed_ok:
                covered.add(bucket.start)
            for bar in bucket.bars:
                if bar.key in turbos:
                    turbos[bar.key].append(bar.start, bar.bid_open, bar.bid_high, bar.bid_low, bar.bid_close,
                                           bar.ask_open, bar.ask_close)
        return turbos, covered


def index_path(*points, day: date = DAY, start: str = "08:00", end: str = "22:00") -> IndexSeries:
    """
    The index's ten-second bars from ``start`` to ``end``: each point (time, level) holds until the next, a point
    (time, open, high, low, close) is that one bar.
    """
    index = IndexSeries()
    marks = sorted((at(point[0], day), point[1:]) for point in points)
    level = 29_000.0
    moment = int(at(start, day))
    while moment < at(end, day):
        bar = next((values for when, values in marks if when == moment), None)
        if bar is not None and len(bar) == 4:
            index.append(moment, *bar)
            level = bar[3]
        else:
            for when, values in marks:
                if when <= moment and len(values) == 1:
                    level = values[0]
            index.append(moment, level, level, level, level)
        moment += 10
    return index


def day_of(turbos, covered=None, index: IndexSeries | None = None, day: date = DAY) -> DayData:
    start, end = CLOCK.day_bounds(day)
    by_direction: dict[str, list[TurboSeries]] = {}
    for series in turbos:
        by_direction.setdefault(series.ref.direction, []).append(series)
    return DayData(day=day, start=start, end=end, indice="us100", turbos=sort_turbos(by_direction), covered=covered,
                   index=index)


def signal(number: int, action: str, clock_time: str, *, later: float = 0.0, source: str = "pine",
           alert: str | None = None, **detail) -> SignalEvent:
    moment = at(clock_time) + later
    return SignalEvent(number, source, action, arrived_at=moment, alert_at=at(alert) if alert else moment,
                       indice="us100", detail=detail)


def close_of_day(number: int = 99, day: date = DAY) -> SignalEvent:
    moment = at(NEW_RUN.close_position_time, day) + 0.5
    return SignalEvent(number, "schedule", "close-position", arrived_at=moment, alert_at=moment, indice="n/a")


def run(day: DayData, events, settings=NEW_RUN) -> Simulator:
    """One day traded, then the run's end (src/backtest/run.py): what is still to be sold is said unresolved."""
    simulator = Simulator(settings, CLOCK)
    simulator.run_day(day, events)
    simulator.finish()
    return simulator


def fates(simulator: Simulator) -> dict[int, tuple]:
    return {record["id"]: (record["fate"], record["rule"]) for record in simulator.signals}


# ── what the recorder writes ──


def test_a_turbo_saxo_stops_quoting_is_written_again_every_ten_seconds_while_anything_else_is_quoted():
    stream = Stream(index_to="09:10")
    stream.quote(1, "09:00:02", 5.00)
    stream.quote(1, "09:00:31", 5.02)
    # Then nothing for this turbo for nine minutes.

    turbos, covered = stream.recorded("09:10", ref(1))

    turbo = turbos[1]
    assert (turbo.ts[0], turbo.ts[-1]) == (at("09:00:00"), at("09:09:50"))
    assert {later - earlier for earlier, later in zip(turbo.ts, turbo.ts[1:])} == {10}
    assert set(turbo.bid_close[3:]) == {5.02}
    assert all(stamp in covered for stamp in turbo.ts)
    # What the bars alone say: the quote standing at 09:09:50 is 555 s old.
    assert turbo.quote_age(int(at("09:09:50"))) == 555


def test_a_turbo_saxo_says_has_no_market_has_no_bar_until_it_is_quoted_again():
    stream = Stream(index_to="09:10")
    stream.quote(1, "09:00:02", 5.00)
    stream.no_market(1, "09:01:05")
    stream.quote(1, "09:04:12", 4.60)

    turbos, covered = stream.recorded("09:10", ref(1))

    turbo = turbos[1]
    assert [(earlier, later) for earlier, later in zip(turbo.ts, turbo.ts[1:]) if later - earlier > 10] == [
        (at("09:01:00"), at("09:04:10")),
    ]
    resumed = turbo.index_at(int(at("09:04:10")))
    # The first bar after the hole opens on the new quote.
    assert (turbo.bid_open[resumed], turbo.bid_close[resumed]) == (4.60, 4.60)
    # The recorder listened all along: the hole is the turbo's.
    assert all(stamp in covered for stamp in range(int(at("09:01:10")), int(at("09:04:10")), 10))


def test_after_a_silence_of_the_whole_stream_the_first_bar_opens_on_the_quote_from_before_it():
    stream = Stream(index_to="09:01")
    stream.index("09:03:05", "09:10")
    stream.quote(1, "09:00:02", 5.00)
    # Nothing at all from 09:01 to 09:03:05, then the turbo's new quote.
    stream.quote(1, "09:03:06", 4.40)

    turbos, covered = stream.recorded("09:10", ref(1))

    turbo = turbos[1]
    resumed = turbo.first_from(at("09:01:30"))
    assert turbo.ts[resumed] == at("09:03:00")
    assert (turbo.bid_open[resumed], turbo.bid_close[resumed]) == (5.00, 4.40)
    assert at("09:02:00") not in covered


# ── a purchase ──


def frozen_then_caught_up() -> tuple[dict[int, TurboSeries], set[int]]:
    """Quoted at 09:00, then Saxo silent on it for five and a half minutes, then 11 % higher."""
    stream = Stream()
    stream.quote(1, "09:00:02", 5.00)
    stream.quote(1, "09:00:31", 5.01)
    stream.quote(1, "09:06:04", 5.60)
    stream.quote(1, "09:06:33", 5.61)
    return stream.recorded("22:00", ref(1))


def test_without_the_index_a_purchase_on_a_quote_frozen_past_two_minutes_takes_it_as_it_stands():
    turbos, covered = frozen_then_caught_up()

    simulator = run(day_of(turbos.values(), covered), [signal(1, "long", "09:03:00"), close_of_day()], ROOMY)

    (trade,) = simulator.trades
    # A quiet market and a silent Saxo cannot be told apart: the quote is taken as it stands, and nothing is said.
    assert trade["entry"]["price"] == pytest.approx(5.06)
    assert (trade["notes"], trade["flags"]) == ([], [])


def test_with_the_index_a_purchase_on_a_quote_frozen_while_the_index_moved_is_priced_from_the_index():
    turbos, covered = frozen_then_caught_up()
    # The index 64 points up from 09:02 while Saxo sent nothing: the turbo was worth 5.01 + 64 x 0.0086.
    index = index_path(("09:02:00", 29_064.0), ("09:06:00", 29_069.0))

    simulator = run(day_of(turbos.values(), covered, index), [signal(1, "long", "09:03:00"), close_of_day()], ROOMY)

    (trade,) = simulator.trades
    assert trade["entry"]["price"] == pytest.approx(5.61)
    assert trade["flags"] == ["frozen_quote", "index_priced"]
    assert trade["notes"][0] == ("Saxo sent the turbo nothing for 155 s while the index moved: priced at the index's 5.61, "
                                 "not its frozen ask 5.06")


def test_a_purchase_saxo_catches_up_within_two_minutes_is_priced_at_the_caught_up_quote_and_opens_at_the_order():
    turbos, covered = frozen_then_caught_up()

    simulator = run(day_of(turbos.values(), covered), [signal(1, "long", "09:05:00"), close_of_day()], ROOMY)

    (trade,) = simulator.trades
    assert trade["entry"]["price"] == pytest.approx(5.65)
    # Opened at the order, a minute before the quote it was priced at: Saxo fills at its live price, which that
    # quote caught up with.
    assert trade["entry"]["filled_at"] == utc_iso(at("09:05:01"))
    assert trade["notes"][0] == "Priced at the turbo's next quote, 5.65: the ask standing at the fill was 5.06"


def test_a_turbo_without_a_market_when_the_trader_chooses_is_passed_over_for_the_next_quoted_one():
    stream = Stream()
    stream.quote(1, "09:00:02", 4.60)
    stream.quote(2, "09:00:03", 5.80)
    stream.no_market(1, "09:59:00")
    stream.quote(1, "10:30:02", 4.70)
    turbos, covered = stream.recorded("22:00", ref(1, barrier=28_600.0), ref(2, barrier=28_500.0))

    simulator = run(day_of(turbos.values(), covered), [signal(1, "long", "10:01:00"), close_of_day()])

    trade = simulator.trades[0]
    assert trade["turbo"]["instrument_id"] == 2
    assert [(row["instrument_id"], row["bid"]) for row in trade["entry"]["candidates"]] == [(1, None), (2, 5.8)]


def test_a_turbo_without_a_market_when_the_order_arrives_rejects_it_once_the_other_side_is_sold():
    stream = Stream()
    stream.quote(1, "09:00:02", 5.00)
    stream.quote(2, "09:00:03", 6.00)
    # Its last bar is the one of 10:00:10: chosen then, gone when the order arrives in the next ten seconds.
    stream.no_market(1, "10:00:15")
    stream.quote(1, "10:20:02", 5.10)
    turbos, covered = stream.recorded("22:00", ref(1), ref(2, "short", 29_445.0))
    events = [signal(1, "short", "09:30:00"), signal(2, "long", "10:00:08"), close_of_day()]

    simulator = run(day_of(turbos.values(), covered), events)

    assert fates(simulator)[2] == ("failed", "order_rejected")
    assert simulator.signals[1]["reason"] == "Order rejected: the turbo was not quoted when the order arrived"
    # The short was sold for it, and no purchase is tried again, as live.
    (short,) = simulator.trades
    assert (short["direction"], short["exit"]["reason"]) == ("short", "Explicit Close (All)")
    assert simulator.signals[1]["closed_trade_ids"] == [short["id"]]


def test_an_order_that_costs_more_than_the_account_holds_is_refused_as_saxo_refuses_it():
    stream = Stream()
    stream.quote(1, "09:00:02", 5.00)
    stream.quote(1, "09:00:44", 5.40)
    turbos, covered = stream.recorded("22:00", ref(1))

    simulator = run(day_of(turbos.values(), covered), [signal(1, "long", "09:00:30"), close_of_day()],
                    replace(NEW_RUN, capital=1_000.0))

    # Sized on the ask standing at the choice (5.05: 197 turbos), it fills at the next quote (5.45): 1,073.65 €.
    assert simulator.trades == []
    assert fates(simulator)[1] == ("failed", "funds_refused")
    assert simulator.signals[0]["reason"] == (
        "Order rejected: 197 turbos at 5.45 cost 1073.65, the account held 1000.00 [InsufficientFunds]"
    )


def test_a_quote_saxo_says_is_delayed_is_not_bought_as_live(tmp_path):
    os.makedirs(tmp_path / "saxo")
    with gzip.open(tmp_path / "saxo" / "prices_2026-10-14.csv.gz", "wt", newline="", encoding="utf-8") as out_file:
        writer = csv.DictWriter(out_file, fieldnames=list(COLUMNS))
        writer.writeheader()
        for minute in range(10):
            writer.writerow({
                "ts": f"2026-10-14T07:{minute:02d}:00Z", "uic": "40000001", "asset_type": "WarrantOpenEndKnockOut",
                "symbol": "T1", "description": "Turbo long 28555", "role": "turbo", "indice": "us100",
                "direction": "long", "barrier": "28555", "issuer": "Saxo", "bid_open": 5.0, "bid_high": 5.0,
                "bid_low": 5.0, "bid_close": 5.0, "ask_open": 5.05, "ask_high": 5.05, "ask_low": 5.05,
                "ask_close": 5.05, "updates": 1, "delayed_minutes": 15,
            })
    start, end = CLOCK.day_bounds(DAY)
    day = asyncio.run(FileData(str(tmp_path)).day("us100", DAY, start, end))

    choice = select_turbo(day, "long", int(at("09:05:00")), 4, 15)

    assert isinstance(choice, NotSelected)
    assert (choice.fate, choice.rule) == ("failed", "no_market")
    assert choice.reason.startswith("NoMarketAvailableException: Saxo's quotes of the turbos are delayed by 15 min")
    # The live selection's own filter, on the same quote.
    assert filter_tradable_market_items([{"Quote": {"Bid": 5.0, "Ask": 5.05, "DelayedByMinutes": 15}}]) == []


# ── a sale ──


def hole_from_ten_to_twenty_five() -> tuple[dict[int, TurboSeries], set[int]]:
    """A long quoted 5.00 from 09:00, without a market from 10:00:05 to 10:25:03, then quoted 4.10."""
    stream = Stream()
    stream.quote(1, "09:00:02", 5.00)
    stream.no_market(1, "10:00:05")
    stream.quote(1, "10:25:03", 4.10)
    stream.quote(2, "09:00:03", 6.00)
    return stream.recorded("22:00", ref(1), ref(2, "short", 29_445.0))


def test_a_sale_in_a_hole_is_left_to_the_monitor_and_filled_at_its_next_attempt_once_quoted_again():
    turbos, covered = hole_from_ten_to_twenty_five()
    events = [signal(1, "long", "09:30:00"), signal(2, "close-position", "10:05:00"), close_of_day()]

    simulator = run(day_of(turbos.values(), covered), events)

    (trade,) = simulator.trades
    # Tried from 10:05:01 with the monitor's growing waits (1, 2, 4, 8, then 15 s): the attempt after 10:25:00.
    assert (trade["exit"]["filled_at"], trade["exit"]["bid"]) == (utc_iso(at("10:25:01")), 4.10)
    assert trade["notes"][-1] == "Not quoted when it was to be sold (10:05:01): sold at 10:25:01, when Saxo quoted it again"
    assert trade["flags"] == ["hole", "sold_after_hole"]
    assert fates(simulator)[2] == ("closed", None)


def test_a_sale_through_a_hole_brings_its_money_only_when_it_fills():
    turbos, covered = hole_from_ten_to_twenty_five()
    events = [signal(1, "long", "09:30:00"), signal(2, "short", "10:05:00"), close_of_day()]

    simulator = run(day_of(turbos.values(), covered), events)

    # The long, bought with nearly all the account, left 6.05 € of cash: the short finds nothing to buy with,
    # as live, which sizes on what Saxo holds while the long is not sold.
    assert fates(simulator)[2] == ("failed", "insufficient_funds")
    (long,) = simulator.trades
    assert round(10_000.0 - long["cost"], 2) == 6.05
    assert long["exit"]["filled_at"] == utc_iso(at("10:25:01"))


def test_after_a_silence_of_the_whole_stream_a_sale_is_filled_at_saxos_first_new_quote():
    stream = Stream(index_to="10:01")
    stream.index("10:03:05", "22:00")
    stream.quote(1, "09:00:02", 5.00)
    stream.quote(1, "10:03:06", 4.40)
    turbos, covered = stream.recorded("22:00", ref(1))
    events = [signal(1, "long", "09:30:00"), signal(2, "close-position", "10:01:30"), close_of_day()]

    simulator = run(day_of(turbos.values(), covered), events)

    (trade,) = simulator.trades
    # The bar of 10:03 opens on the quote from before the silence (5.00): the sale waits for Saxo's new one, 4.40,
    # in those ten seconds, and is made at the monitor's next attempt.
    assert (trade["exit"]["filled_at"], trade["exit"]["bid"]) == (utc_iso(at("10:03:16")), 4.40)


def test_without_the_index_a_sale_on_a_quote_frozen_past_two_minutes_takes_it_as_it_stands():
    stream = Stream()
    stream.quote(1, "09:00:02", 5.00)
    stream.quote(1, "09:00:31", 5.01)
    # Saxo silent on it from 09:00:31 to 10:10:04, when it comes back 12 % lower.
    stream.quote(1, "10:10:04", 4.40)
    turbos, covered = stream.recorded("22:00", ref(1))
    events = [signal(1, "long", "09:30:00"), signal(2, "close-position", "10:02:00"), close_of_day()]

    simulator = run(day_of(turbos.values(), covered), events)

    (trade,) = simulator.trades
    assert (trade["exit"]["filled_at"], trade["exit"]["bid"]) == (utc_iso(at("10:02:01")), 5.01)
    assert (trade["notes"], trade["flags"]) == ([], [])


def test_a_turbo_not_quoted_again_that_day_is_sold_on_the_next_day_it_is():
    first = Stream()
    first.quote(1, "09:00:02", 5.00)
    first.quote(1, "11:00:02", 4.80)
    first.no_market(1, "11:00:35")
    first_turbos, first_covered = first.recorded("22:00", ref(1))
    second = Stream(day=NEXT_DAY)
    second.quote(1, "08:00:02", 4.95)
    second_turbos, second_covered = second.recorded("22:00", ref(1))
    simulator = Simulator(NEW_RUN, CLOCK)

    simulator.run_day(day_of(first_turbos.values(), first_covered), [signal(1, "long", "09:30:00"), close_of_day()])
    held = list(simulator.positions)
    simulator.run_day(day_of(second_turbos.values(), second_covered, day=NEXT_DAY), [close_of_day(day=NEXT_DAY)])
    simulator.finish()

    # Not sold by the close: held overnight, as Saxo holds it, and sold at its first quote of the next day, at the
    # monitor's attempt after it (every 300 s once an intent is an hour old: 08:00:01.5).
    assert len(held) == 1 and simulator.days[0]["trades"] == 0
    (trade,) = simulator.trades
    assert trade["exit"]["reason"] == "Explicit Close (All)"
    assert trade["exit"]["filled_at"] == utc_iso(at("08:00:01", NEXT_DAY))
    assert trade["exit"]["bid"] == 4.95
    assert trade["flags"] == ["carried", "sold_after_hole"]
    assert simulator.days[1]["trades"] == 1


def test_a_turbo_never_quoted_again_while_the_run_lasted_is_said_unresolved():
    stream = Stream()
    stream.quote(1, "09:00:02", 5.00)
    stream.quote(1, "11:00:02", 4.80)
    stream.no_market(1, "11:00:35")
    turbos, covered = stream.recorded("22:00", ref(1))

    simulator = run(day_of(turbos.values(), covered), [signal(1, "long", "09:30:00"), close_of_day()])

    (trade,) = simulator.trades
    assert (trade["exit"]["reason"], trade["exit"]["bid"]) == ("End of data (never quoted again)", 4.80)
    assert trade["flags"] == ["unresolved"]
    assert trade["notes"][-2:] == [
        "Never quoted again while the run lasted: what it would have sold for is not known",
        "No quote to sell at: closed at its last bid",
    ]
    assert simulator.days[-1]["trades"] == 1


# ── a position held ──


def knocked_out_day(index: IndexSeries | None) -> DayData:
    """A long quoted 5.00 then 4.90, with no market from 10:00:40 to the end of the day."""
    stream = Stream()
    stream.quote(1, "09:00:02", 5.00)
    stream.quote(1, "10:00:02", 4.90)
    stream.no_market(1, "10:00:40")
    turbos, covered = stream.recorded("22:00", ref(1))
    return day_of(turbos.values(), covered, index)


def test_with_the_index_a_turbo_whose_quotes_stop_as_the_index_reaches_its_level_is_knocked_out_at_nothing():
    # The index falls through the turbo's knock-out level, 28,555, in the ten seconds of 10:00:40.
    index = index_path(("10:00:40", 29_000.0, 29_000.0, 28_550.0, 28_550.0))

    simulator = run(knocked_out_day(index), [signal(1, "long", "09:30:00"), signal(2, "long", "11:00:00"), close_of_day()])

    trade = simulator.trades[0]
    assert (trade["exit"]["reason"], trade["exit"]["bid"], trade["exit"]["filled_at"]) == (
        "SaxoAPI", 0.0, utc_iso(at("10:00:40")),
    )
    assert trade["performance_percent"] == -100.0
    assert trade["flags"] == ["knocked_out"]
    assert trade["notes"][-1] == ("Knocked out at 10:00:40: no quote after 10:00:50, the index at its knock-out level "
                                  "28555; sold at nothing (what Saxo pays back is not known)")


def test_with_the_index_a_turbo_whose_quotes_stop_above_its_level_is_not_knocked_out():
    index = index_path(("10:00:40", 28_700.0))

    simulator = run(knocked_out_day(index), [signal(1, "long", "09:30:00"), close_of_day()])

    (trade,) = simulator.trades
    assert trade["exit"]["reason"] == "End of data (never quoted again)"
    assert trade["flags"] == ["unresolved"]


def test_without_the_index_a_knock_out_while_unquoted_cannot_be_told_and_the_trade_is_said_unresolved():
    simulator = run(knocked_out_day(None), [signal(1, "long", "09:30:00"), signal(2, "long", "11:00:00"), close_of_day()])

    # Its last bid is not under a quarter of the day's high: held, as live holds it, and never sold.
    assert fates(simulator)[2] == ("refused", "duplicate")
    (trade,) = simulator.trades
    assert (trade["exit"]["reason"], trade["exit"]["bid"]) == ("End of data (never quoted again)", 4.90)
    assert trade["flags"] == ["unresolved"]


def between_quotes_settings():
    config = json.loads(json.dumps(EXAMPLE))
    management = config["trade"]["config"]["position_management"]
    management["index_between_quotes"] = True
    management["wait_for_fresh_quote"] = False
    config["trade"]["config"]["capital_feed"]["enabled"] = True
    return replace(
        from_config(config), time_scaling_enabled=False, confidence_enabled=False, trailing_enabled=False,
        cooldown_after_loss_minutes=0, max_trades_per_day=0, day_profit_target=50.0, no_entry_above_day_profit=50.0,
        capital=10_000.0, fill_price="standing", wait_for_fresh_quote=False, exit_slippage_percent=0.0,
    )


@pytest.mark.parametrize("hole", [False, True])
def test_the_index_is_judged_during_a_hole_while_its_estimate_may_be_believed(hole):
    # As tests/test_index_between_quotes.py: Saxo's quote stays 10.0 while the index falls 200 points at 10:00:40;
    # with the hole, the turbo has no bar from 10:00:40 to 10:01:40.
    start = int(at("10:00:00"))
    turbo = TurboSeries(ref(1, barrier=28_000.0))
    for offset in range(0, 700, 10):
        if hole and 40 <= offset < 100:
            continue
        turbo.append(start + offset, 10.0, 10.0, 10.0, 10.0, 10.05, 10.05)
    index = IndexSeries()
    for place, offset in enumerate(range(0, 700, 10)):
        if place == 4:
            index.append(start + offset, 29_000.0, 29_000.0, 28_800.0, 28_820.0)
        else:
            level = 29_000.0 if place < 4 else 28_820.0
            index.append(start + offset, level, level, level, level)
    day_start, day_end = CLOCK.day_bounds(DAY)
    events = [SignalEvent(1, "pine", "long", arrived_at=start + 1, alert_at=start + 1, indice="us100"),
              SignalEvent(2, "schedule", "close-position", arrived_at=start + 600, alert_at=start + 600, indice="n/a")]

    simulator = run(day_of([turbo], set(range(day_start, day_end, 10)), index), events, between_quotes_settings())

    (trade,) = simulator.trades
    assert trade["exit"]["group"] == "Stop-loss"
    assert "between quotes" in trade["exit"]["reason"]
    # The low of the index's ten seconds of 10:00:40 decides it, hole or not, as live decides it at 10:00:41.
    assert trade["exit"]["filled_at"] == utc_iso(at("10:00:50"))


@pytest.mark.parametrize(("hole", "sold_at"), [(False, "14:25:00"), (True, "14:50:10")])
def test_the_sale_before_a_release_is_tried_through_a_hole_and_made_once_quoted_again(hole, sold_at):
    news = replace(NEW_RUN, news_window=True, news_flatten_minutes_before=5)
    stream = Stream()
    stream.quote(1, "09:00:02", 5.00)
    stream.quote(1, "14:00:02", 5.02)
    if hole:
        # No market from 14:24 to 14:50: the sale from 14:25 (CPI at 14:30) waits for Saxo's quote of 14:50:00.
        stream.no_market(1, "14:24:05")
    stream.quote(1, "14:50:03", 4.90)
    turbos, covered = stream.recorded("22:00", ref(1))

    simulator = run(day_of(turbos.values(), covered), [signal(1, "long", "13:00:00"), close_of_day()], news)

    (trade,) = simulator.trades
    assert trade["exit"]["reason"] == "Performance (Flat before CPI (14:30))"
    assert trade["exit"]["filled_at"] == utc_iso(at(sold_at))


@pytest.mark.parametrize(("slippage", "sold_at"), [(0.0, 4.00), (0.1, 3.99)])
def test_the_monitors_exits_are_charged_the_exit_slippage(slippage, sold_at):
    stream = Stream()
    stream.quote(1, "09:00:02", 5.00)
    stream.quote(1, "09:40:02", 4.00)
    turbos, covered = stream.recorded("22:00", ref(1))

    simulator = run(day_of(turbos.values(), covered), [signal(1, "long", "09:30:00"), close_of_day()],
                    replace(NEW_RUN, exit_slippage_percent=slippage))

    (trade,) = simulator.trades
    assert trade["exit"]["reason"].startswith("Performance (Stoploss (-15%) hit at")
    # Decided on the bid of 4.00, filled 0.1 % under it, to the turbo's cent below.
    assert trade["exit"]["bid"] == sold_at


# ── signals ──


def test_a_signal_replaced_while_the_trader_was_busy_never_reaches_it():
    stream = Stream()
    stream.quote(1, "09:00:02", 5.00)
    stream.quote(2, "09:00:03", 6.00)
    turbos, covered = stream.recorded("22:00", ref(1), ref(2, "short", 29_445.0))
    # Busy with the first until 09:30:01, the trader reads the web server once free: the third replaced the second.
    events = [signal(1, "long", "09:30:00"), signal(2, "short", "09:30:00", later=0.4),
              signal(3, "long", "09:30:00", later=0.8), close_of_day()]

    simulator = run(day_of(turbos.values(), covered), events)

    assert [fates(simulator)[number] for number in (1, 2, 3)] == [
        ("executed", None), ("ignored", "superseded"), ("refused", "duplicate"),
    ]
    assert simulator.signals[1]["reason"] == "Replaced by a newer long of its indice before the trader read it"


def test_a_close_of_the_same_moment_never_replaces_an_entry_while_the_trader_is_busy():
    stream = Stream()
    stream.quote(1, "09:00:02", 5.00)
    stream.quote(2, "09:00:03", 6.00)
    turbos, covered = stream.recorded("22:00", ref(1), ref(2, "short", 29_445.0))
    events = [signal(1, "long", "09:30:00"), signal(2, "short", "09:30:00", later=0.4, alert="09:29:50"),
              signal(3, "close-position", "09:30:00", later=0.8, alert="09:29:50"), close_of_day()]

    simulator = run(day_of(turbos.values(), covered), events)

    assert [fates(simulator)[number] for number in (1, 2, 3)] == [
        ("executed", None), ("executed", None), ("ignored", "superseded"),
    ]
    assert simulator.signals[1]["reason"] == "Not kept by the web server: the short of the same moment was waiting"


def test_what_a_busy_trader_reads_is_what_the_web_servers_store_keeps():
    def event(number: int, action: str, alert: str, indice: str = "us100") -> SignalEvent:
        return SignalEvent(number, "pine", action, arrived_at=at("09:30:00") + number, alert_at=at(alert), indice=indice)

    for waiting in (
        [event(1, "long", "09:29:00"), event(2, "short", "09:29:10")],
        [event(1, "long", "09:29:10"), event(2, "close-position", "09:29:10")],
        [event(1, "close-position", "09:29:10"), event(2, "long", "09:29:10")],
        [event(1, "long", "09:29:10"), event(2, "short", "09:29:00")],
        [event(1, "long", "09:29:00"), event(2, "close-position", "09:29:05", "n/a")],
    ):
        store = SignalStore()
        for one in waiting:
            asyncio.run(store.upsert({
                "indice": one.indice, "action": one.action, "id": one.id,
                "signal_timestamp": utc_iso(one.alert_at),
            }))
        kept = {signal["id"] for signal in asyncio.run(store.get_all())}

        assert {one.id for one in stored(waiting)} == kept


# ── the run's account of itself ──


def test_a_day_recorded_for_less_of_its_trading_hours_is_said_partly_recorded():
    whole, part = Stream(), Stream(index_to="20:07")
    for stream in (whole, part):
        stream.quote(1, "08:00:02", 5.00)
    simulator = Simulator(NEW_RUN, CLOCK)

    for stream in (whole, part):
        turbos, covered = stream.recorded("22:00", ref(1))
        simulator.run_day(day_of(turbos.values(), covered), [])

    assert [day["partial"] for day in simulator.days] == [False, True]
    assert simulator.days[0]["recorded"]["share"] == 1.0
    assert 0.85 < simulator.days[1]["recorded"]["share"] < 0.88
    # Still tested: the run says it beside its result.
    assert _day_status(simulator.days[1], pine=False, strategy_bars=0, weekend=False) == TESTED


def test_what_a_run_rests_on_counts_its_trades_flags_refusals_and_partial_days():
    trades = [{"flags": []}, {"flags": ["hole"]}, {"flags": ["hole", "sold_after_hole"]}, {"flags": ["knocked_out"]}]
    signals = [{"rule": "funds_refused"}, {"rule": "duplicate"}, {"rule": "funds_refused"}]
    days = [{"day": "2026-10-22"}, {"day": "2026-10-23", "partial": True}]

    found = rests_on(trades, signals, days)

    assert found == {
        "trades": 4, "flags": {"hole": 2, "knocked_out": 1, "sold_after_hole": 1},
        # A hole met while held changed nothing of how the trade was priced; a sale after it did.
        "trades_priced_off_quotes": 2, "share_percent": 50.0,
        "funds_refused": 2, "partial_days": ["2026-10-23"],
    }


def test_the_reconciliation_reads_the_exit_price_of_a_simulated_trade():
    stream = Stream()
    stream.quote(1, "09:00:02", 5.00)
    turbos, covered = stream.recorded("22:00", ref(1))
    simulator = run(day_of(turbos.values(), covered),
                    [signal(1, "long", "09:30:00", source="journal", signal_uuid="u1"), close_of_day()])
    position = {
        "position_id": "7001", "execution_time_open": "2026-10-14T07:30:01Z", "execution_time_close": "2026-10-14T19:55:01Z",
        "position_open_price": 5.06, "position_close_price": 4.99, "position_total_performance_percent": -1.38,
        "position_close_reason": "Explicit Close (All)", "instrument_uic": 40_000_001,
    }

    found = reconcile(simulator.trades, simulator.signals, [{"signal_uuid": "u1", "position_id": "7001"}], [position])

    (pair,) = found["pairs"]
    assert pair["exit_price"] == {"backtest": 5.0, "real": 4.99}
    assert pair["entry_price_percent"] == pytest.approx(-0.2, abs=0.01)
    assert pair["exit_price_percent"] == pytest.approx(0.2, abs=0.01)
    summary = found["summary"]
    assert (summary["median_entry_price_percent"], summary["median_exit_price_percent"]) == (
        pair["entry_price_percent"], pair["exit_price_percent"],
    )
    assert summary["median_exit_seconds"] == pair["exit_seconds"]


def test_the_fill_noise_moves_each_fill_within_its_share_and_repeats_with_its_seed():
    def draws(seed: str) -> list[float]:
        noise = Noise(0.5, seed)
        noise.start_day("2026-10-14")
        return [noise(5.0) for _ in range(200)]

    first = draws("1:run")

    assert all(4.975 <= value <= 5.025 for value in first) and len(set(first)) > 100
    assert draws("1:run") == first and draws("2:run") != first
    assert Noise(0.0, "1:run")(5.0) == 5.0


def test_a_fill_noise_stress_says_how_far_the_total_moved_at_each_level():
    from src.backtest.figures import noise_spread

    cells = [{"values": [level, seed], "whole": {"total_percent": total}} for level, seed, total in (
        (0.1, 1, 10.0), (0.1, 2, 9.0), (0.5, 1, 4.0), (0.5, 2, -3.0),
    )]

    assert noise_spread(cells) == [
        {"percent": 0.1, "draws": 2, "lowest": 9.0, "highest": 10.0, "median": 9.5},
        {"percent": 0.5, "draws": 2, "lowest": -3.0, "highest": 4.0, "median": 0.5},
    ]
