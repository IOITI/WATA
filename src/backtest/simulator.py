"""
A trading day as WATA trades it, on recorded turbos: the signals reach the
trader one after the other, it checks them, sells and buys, and the position
monitor watches what was bought, ten seconds at a time.

The order of things is the live one (src/trader/__init__.py):

- an entry the rules refuse closes nothing;
- an entry taken closes every position first and buys with what they gave
  (``reserve_cash_percent`` 0, the classic mode), or buys first and closes
  the others after (a cash reserve);
- ``close-position`` closes everything, ``close-long`` and ``close-short`` one
  side; the end of the day is the scheduler's ``close-position``;
- the monitor sells on the stop-loss, the take-profit, the trailing stop and
  the day's target, on the turbo's bid (src/backtest/rules.py).

A quote is read at the first ten-second boundary at or after the moment it is
needed (src/backtest/clock.py). Inside ten seconds the bid is taken to go from
its open to its low and its high, the nearer to its close last (the range
bars' rule), and the monitor looks at each of the four.

Saxo quotes a turbo about every 30 seconds, and fills an order at the live
price. With ``fill_price`` "next" an order is priced at the turbo's next quote
after the one standing at its fill, nearer to what WATA paid than the standing
one. With ``wait_for_fresh_quote`` a position is judged only once its bid has
changed after its purchase, as the position monitor judges it
(src/trade/fresh_quote.py).

With ``index_between_quotes``, the monitor also judges every price exit on the
bid estimated from the index between Saxo's quotes (src/trade/index_estimate.py):
on the index's ten-second bar, at its open before the turbo's new quote, and at
its open, low, high and close in ten seconds without one. A turbo's quote is
taken to come in the middle of its ten seconds, the index read there. During a
hole in the turbo's bars it goes on, on the index's bars, as long as the
estimate may be believed.

A sale Saxo cannot take (no quote of the turbo when the order leaves) is left
to the position monitor, as live hands it over (src/trade/close_intents.py): it
is tried again with the monitor's growing waits and filled once Saxo quotes the
turbo again, at that new quote, its money only then. A turbo not quoted again
that day is sold on the next recorded day it is; never, and the run ends with
the trade said unresolved, at its last bid. A turbo whose quotes stop as the
index reaches its knock-out level is knocked out there, at nothing (what Saxo
pays back of a knocked-out turbo is not known).
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

from src.admin.analysis import exit_group
from src.backtest.clock import BAR_SECONDS, TradingClock, ceil10, utc_iso
from src.backtest.data import NEXT_QUOTE_SECONDS, DayData, TurboSeries
from src.backtest.execution import FIXED, FixedFills
from src.backtest.rules import (
    ACCOUNT_GUARD,
    DAY_TARGET,
    STOP_LOSS,
    TAKE_PROFIT,
    TRAILING,
    compounded,
    entry_refusal,
    exit_decision,
    performance,
    position_scale,
    units_for,
)
from src.backtest.selection import NotSelected, select_turbo
from src.backtest.settings import NEXT, BacktestSettings
from src.trade.close_intents import MAX_RETRY_SECONDS, retry_after
from src.trade.fresh_quote import FreshQuotes
from src.trade.index_estimate import (
    MOST_STEP,
    QUOTE_FRESH_SECONDS,
    TURBO_DIRECTIONS,
    BetweenQuotes,
    TurboSensitivity,
    between_quotes_reason,
    estimated_bid,
)
from src.trade.macro_calendar import Calendar, flat_reason, flat_window_at, read_calendar
from src.trade.market_calendar import MarketCalendar, read_market_calendar

LONG, SHORT = "long", "short"
ENTRY_ACTIONS = (LONG, SHORT)
# The side a close signal closes; None: every position.
CLOSE_ACTIONS = {"close-position": None, "close-long": LONG, "close-short": SHORT}

# What became of a signal.
EXECUTED = "executed"
REFUSED = "refused"
FAILED = "failed"
UNPRICED = "unpriced"
CLOSED = "closed"
IGNORED = "ignored"
FATES = (EXECUTED, REFUSED, FAILED, UNPRICED, CLOSED, IGNORED)

INSUFFICIENT_FUNDS = "insufficient_funds"
ORDER_REJECTED = "order_rejected"
# Saxo refuses an order the account's cash does not cover at the price it fills at (InsufficientFunds).
FUNDS_REFUSED = "funds_refused"
# The web server keeps the newest signal of an indice: one replaced while the trader was busy never reaches it.
SUPERSEDED = "superseded"

# Without the index, a turbo no longer quoted while the recorder still listened, its bid by then under this share
# of its best of the day, is taken for knocked out (the rule of src/admin/prices.py ``availability``).
KNOCKED_OUT_SHARE = 0.25
KNOCKED_OUT = "SaxoAPI"
# Sold before a US release (the macro_news rule's flatten_minutes_before), as the position monitor does.
FLAT_BEFORE_RELEASE = "flat_before_release"
NO_QUOTE = "End of data (no quote)"
# Still to be sold when the run ended: Saxo never quoted its turbo again while the run lasted.
UNRESOLVED = "End of data (never quoted again)"
# A fill priced at the turbo's next quote this far from the one standing then is said in the trade's notes.
NOTED_REQUOTE_SHARE = 0.01
# A quote standing longer than this at an order, Saxo having sent the turbo nothing since (the recorder writes the
# standing quote again every ten seconds), is frozen: the order is priced from the index when the run has it.
FROZEN_QUOTE_SECONDS = NEXT_QUOTE_SECONDS
# How the sale of a turbo that comes back after a hole is looked for: its first new quote within this long.
REQUOTE_SECONDS = NEXT_QUOTE_SECONDS
# A day the recorder listened to less of the trading hours than this share is said partly recorded.
PARTIAL_SHARE = 0.95
# The monitor's exits, charged ``exit_slippage_percent``.
MONITOR_EXITS = (STOP_LOSS, TAKE_PROFIT, TRAILING, DAY_TARGET, FLAT_BEFORE_RELEASE)

# What a trade rested on beyond the recorded quotes, counted in the summary (src/backtest/figures.py ``rests_on``).
FLAG_HOLE = "hole"                       # its turbo had no bar for a while it was held
FLAG_SOLD_AFTER_HOLE = "sold_after_hole"  # a sale waited for Saxo to quote the turbo again
FLAG_FROZEN = "frozen_quote"             # an order on a quote frozen past FROZEN_QUOTE_SECONDS
FLAG_INDEX_PRICED = "index_priced"       # that order priced from the index
FLAG_KNOCKED_OUT = "knocked_out"         # its quotes stopped as the index reached its knock-out level
FLAG_KNOCK_OUT_GUESS = "knock_out_guess"  # taken for knocked out without the index
FLAG_CARRIED = "carried"                 # sold on a later day than it was to be sold
FLAG_UNRESOLVED = "unresolved"           # never quoted again while the run lasted
FLAGS = (FLAG_HOLE, FLAG_SOLD_AFTER_HOLE, FLAG_FROZEN, FLAG_INDEX_PRICED, FLAG_KNOCKED_OUT, FLAG_KNOCK_OUT_GUESS,
         FLAG_CARRIED, FLAG_UNRESOLVED)


@dataclass(frozen=True)
class SignalEvent:
    id: int
    # pine | journal | schedule | random
    source: str
    action: str
    # When the trader can see it.
    arrived_at: float
    # The signal's own time, which its age is counted from.
    alert_at: float
    indice: str
    confidence: float | None = None
    # What the record of the signal keeps beside (bar times, alert, journal row...).
    detail: dict = field(default_factory=dict)


@dataclass
class Position:
    trade_id: int
    direction: str
    series: TurboSeries
    units: int
    open_price: float
    opened_at: float
    cost: float
    commission: float
    signal_id: int | None
    confidence: float | None
    scale_percent: float
    day_percent_before: float
    entry: dict
    # The best and worst results seen; the best starts at 0, as the monitor reads it.
    best: float = 0.0
    worst: float | None = None
    # The next bar of the turbo the monitor looks at.
    cursor: int = 0
    notes: list[str] = field(default_factory=list)
    # What the trade rested on beyond the recorded quotes (FLAGS).
    flags: list[str] = field(default_factory=list)
    # A sale Saxo could not take when it was sent: what the monitor goes on trying (reason, kind, signal_id, since).
    pending_sale: dict | None = None
    # Waiting to be sold on a day its turbo was not recorded: nothing to look at.
    orphan: bool = False

    def flag(self, name: str) -> None:
        if name not in self.flags:
            self.flags.append(name)


@lru_cache(maxsize=1)
def news_calendar() -> Calendar:
    """The calendar of US releases the trader reads, read once for every simulator of a run."""
    return read_calendar()


@lru_cache(maxsize=1)
def us_market_calendar() -> MarketCalendar:
    """The US holidays and early closes the trader reads, read once for every simulator of a run."""
    return read_market_calendar()


class Simulator:
    """One account traded day after day. ``run_day`` once per day, in order."""

    def __init__(self, settings: BacktestSettings, clock: TradingClock, *, asset_label: str | None = None,
                 calendar: Calendar | None = None, fills=None, market_calendar: MarketCalendar | None = None):
        self.settings = settings
        self.clock = clock
        # How its orders are filled: the settings' values, or drawn from WATA's own orders.
        self.fills = fills or FixedFills(settings)
        # The US releases, read only when no entry is to be made around them.
        self.calendar = calendar if calendar is not None or settings.news_rule() is None else news_calendar()
        # The US holidays and early closes, as the trader keeps them whatever the configuration.
        self.market_calendar = market_calendar if market_calendar is not None else us_market_calendar()
        self.cash = float(settings.capital)
        self.positions: list[Position] = []
        # The positions whose bid is still the one standing at their purchase, by trade id.
        self.fresh_quotes = FreshQuotes(enabled=settings.wait_for_fresh_quote)
        # Saxo's last quote of each position and its sensitivity to the index, when it is judged between quotes.
        self.between_quotes = BetweenQuotes(enabled=settings.index_between_quotes and bool(settings.index_epic))
        self.last_loss_at: float | None = None
        self.trader_free_at = 0.0
        self.trades: list[dict] = []
        self.signals: list[dict] = []
        self.days: list[dict] = []
        self._next_trade = 1
        self._day: DayData | None = None
        self._day_percents: list[float] = []
        self._entries_today = 0
        self._asset_label = asset_label
        # The account guard (src/trade/account_guard.py): the account's highest since the start, and when it
        # paused entries, which nothing resumes in a run.
        self._account_peak: float | None = None
        self._guard_paused_at: float | None = None

    # ── the day ──

    def day_percent(self) -> float:
        """The day's result so far: its closed trades one after the other (``get_percent_of_the_day``)."""
        return compounded(self._day_percents)

    def run_day(self, day: DayData, events: list[SignalEvent]) -> dict:
        self._day = day
        self.fills.start_day(day.day.isoformat())
        self.fresh_quotes.forget_except(position.trade_id for position in self.positions)
        self.between_quotes.forget_except(position.trade_id for position in self.positions)
        self._day_percents = []
        self._entries_today = 0
        first_trade = len(self.trades)
        first_signal = len(self.signals)
        cash_before = self.cash
        self._carry_over(day)
        ordered = sorted(events, key=lambda event: (event.arrived_at, event.id))
        place = 0
        while place < len(ordered):
            if ordered[place].arrived_at >= self.trader_free_at:
                event = ordered[place]
                place += 1
                self._monitor_until(event.arrived_at)
                self._handle(event, event.arrived_at)
                continue
            # The trader is busy: what comes meanwhile waits in the web server's store, read once it is free.
            free = self.trader_free_at
            waiting = []
            while place < len(ordered) and ordered[place].arrived_at <= free:
                waiting.append(ordered[place])
                place += 1
            read = stored(waiting)
            for event in waiting:
                if event in read:
                    continue
                kept = read[[one.indice for one in read].index(event.indice)]
                self._monitor_until(free)
                self._signal(event, free, IGNORED, SUPERSEDED,
                             f"Replaced by a newer {kept.action} of its indice before the trader read it"
                             if kept.arrived_at > event.arrived_at else
                             f"Not kept by the web server: the {kept.action} of the same moment was waiting")
            for event in read:
                decided = max(event.arrived_at, self.trader_free_at)
                self._monitor_until(decided)
                self._handle(event, decided)
        self._monitor_until(day.end)
        for position in list(self.positions):
            if position.pending_sale is not None:
                # Saxo did not quote it again today: the monitor goes on trying, on the next day it is quoted.
                continue
            # Nothing sold it by the end of the day: at its last bid.
            self._close_at_last(position, day.end, NO_QUOTE)
        return self._day_record(day, first_trade, first_signal, cash_before)

    def finish(self) -> None:
        """
        The run is over: what is still to be sold (its turbo never quoted again while the run lasted) is closed at
        its last bid and said unresolved, in the last day's record.
        """
        if not self.positions or not self.days or self._day is None:
            return
        record = self.days[-1]
        cash_before, first_trade = self.cash, len(self.trades)
        for position in list(self.positions):
            position.flag(FLAG_UNRESOLVED)
            position.notes.append("Never quoted again while the run lasted: what it would have sold for is not known")
            self._close_at_last(position, self._day.end, UNRESOLVED)
        record["trades"] += len(self.trades) - first_trade
        record["profit_loss"] = round(record["profit_loss"] + self.cash - cash_before, 2)
        record["equity"] = round(self.cash, 2)
        record["day_percent"] = self.day_percent()

    def _carry_over(self, day: DayData) -> None:
        """A position still to be sold follows its turbo to the day's bars, or waits for a day that has them."""
        for position in self.positions:
            if position.pending_sale is None:
                continue
            same = next((series for series in day.all_turbos()
                         if series.ref.instrument_id == position.series.ref.instrument_id), None)
            if same is None:
                position.orphan = True
                continue
            position.series, position.cursor, position.orphan = same, 0, False
            position.flag(FLAG_CARRIED)


    def _day_record(self, day: DayData, first_trade: int, first_signal: int, cash_before: float) -> dict:
        trades = self.trades[first_trade:]
        signals = self.signals[first_signal:]
        by_fate = {fate: 0 for fate in FATES}
        for signal in signals:
            by_fate[signal["fate"]] = by_fate.get(signal["fate"], 0) + 1
        if self._guard_on():
            # The day's value, as the daily report records it: what the account guard counts its highest from.
            self._account_peak = self.cash if self._account_peak is None else max(self._account_peak, self.cash)
        first, last = day.span()
        share = self._recorded_share(day)
        record = {
            "day": day.day.isoformat(),
            "status": "tested",
            "signals": by_fate,
            "entries": self._entries_today,
            "trades": len(trades),
            "day_percent": self.day_percent() if trades else 0.0,
            "profit_loss": round(self.cash - cash_before, 2),
            "equity": round(self.cash, 2),
            "recorded": {
                LONG: len(day.turbos.get(LONG) or []),
                SHORT: len(day.turbos.get(SHORT) or []),
                "minutes": day.recorded_minutes(),
                "first": utc_iso(first),
                "last": utc_iso(last),
                # The share of the trading hours the recorder listened to.
                "share": share,
            },
            # Recorded for less of its trading hours than PARTIAL_SHARE: its result rests on what was missed.
            "partial": share is not None and share < PARTIAL_SHARE,
        }
        self.days.append(record)
        return record

    def _recorded_share(self, day: DayData) -> float | None:
        """How much of the day's trading hours (the market's hours, in its timezone) the recorder listened to."""
        if not day.all_turbos():
            return None
        try:
            zone = ZoneInfo(self.settings.market_zone())
        except (KeyError, ValueError):
            return None
        start = datetime.combine(day.day, time(self.settings.trading_start_hour), zone)
        end = (datetime.combine(day.day, time(self.settings.trading_end_hour), zone) if self.settings.trading_end_hour < 24
               else datetime.combine(day.day + timedelta(days=1), time(), zone))
        return day.covered_share(ceil10(start.timestamp()), int(end.timestamp()))

    # ── the trader ──

    def _handle(self, event: SignalEvent, decided: float) -> None:
        if event.action in ENTRY_ACTIONS:
            self._enter(event, decided)
        elif event.action in CLOSE_ACTIONS:
            self._close_signal(event, decided)
        else:
            self._signal(event, decided, IGNORED, "unknown_action", f"No such action: {event.action}")
            self.trader_free_at = decided

    def _signal(self, event: SignalEvent, decided: float, fate: str, rule: str | None, reason: str | None, **more) -> dict:
        record = {
            "id": event.id,
            "day": self._day.day.isoformat(),
            "source": event.source,
            "action": event.action,
            "indice": event.indice,
            "confidence": event.confidence,
            "arrived_at": utc_iso(event.arrived_at),
            "decided_at": utc_iso(decided),
            "fate": fate,
            "rule": rule,
            "reason": reason,
            "trade_id": None,
            "closed_trade_ids": [],
            "candidates": None,
            **event.detail,
            **more,
        }
        self.signals.append(record)
        return record

    def _guard_on(self) -> bool:
        return self.settings.pause_below_balance_eur > 0 or self.settings.pause_after_drawdown_percent > 0

    def _account_value(self, moment: float) -> float:
        """Cash, and the positions at the bid standing then: Saxo's TotalValue, which the account guard reads."""
        value = self.cash
        boundary = ceil10(moment)
        for position in self.positions:
            quote = position.series.quote_at(boundary)
            value += position.units * (quote[0] if quote is not None else position.open_price)
        return value

    def _enter(self, event: SignalEvent, decided: float) -> None:
        settings = self.settings
        account_value = None
        if self._guard_on() and self._guard_paused_at is None:
            account_value = self._account_value(decided)
            self._account_peak = account_value if self._account_peak is None else max(self._account_peak, account_value)
        refusal = entry_refusal(
            settings, self.clock,
            action=event.action, indice=event.indice, decided_at=decided, alert_at=event.alert_at,
            open_directions=[position.direction for position in self.positions],
            day_percent=self.day_percent(), last_loss_at=self.last_loss_at,
            entries_today=self._entries_today, confidence=event.confidence, calendar=self.calendar,
            market_calendar=self.market_calendar,
            account_value=account_value, account_peak=self._account_peak, guard_paused_at=self._guard_paused_at,
            day_results=self._day_percents,
        )
        if refusal is not None:
            if refusal.rule == ACCOUNT_GUARD and self._guard_paused_at is None:
                self._guard_paused_at = decided
            self._signal(event, decided, REFUSED, refusal.rule, refusal.reason)
            self.trader_free_at = decided
            return

        closed: list[int] = []
        selection_at = decided
        if settings.reserve_cash_percent <= 0 and self.positions:
            # Classic mode: everything is sold first, and the new order is sized on what it gave.
            sale_at = decided + self.fills.order_seconds()
            self._monitor_until(ceil10(sale_at))
            for position in list(self.positions):
                closed.append(self._close(position, sale_at, "Explicit Close (All)", signal_id=event.id, departed=decided))
            selection_at = sale_at + settings.close_wait_seconds

        boundary = ceil10(selection_at)
        choice = select_turbo(self._day, event.action, boundary, settings.turbo_min_price, settings.turbo_max_price,
                              settings.max_spread_percent or None)
        if isinstance(choice, NotSelected):
            closed += self._sell_after_failed_entry(event, selection_at)
            self._signal(
                event, decided, choice.fate, choice.rule, choice.reason, closed_trade_ids=closed,
                candidates=_counted(choice.candidates), selection=choice.candidates,
            )
            self.trader_free_at = max(selection_at, self.trader_free_at)
            return

        scale = position_scale(settings, self.clock.minute_of_day(selection_at), event.confidence)
        units = units_for(settings, self.cash, choice.ask, scale)
        counted = _counted(choice.candidates)
        if units <= 0:
            closed += self._sell_after_failed_entry(event, selection_at)
            self._signal(
                event, decided, FAILED, INSUFFICIENT_FUNDS, f"InsufficientFundsException: Insufficient funds @ {choice.ask}",
                closed_trade_ids=closed, candidates=counted, selection=choice.candidates,
            )
            self.trader_free_at = max(selection_at, self.trader_free_at)
            return

        fill_at = selection_at + self.fills.order_seconds()
        fill_boundary = ceil10(fill_at)
        quote = choice.series.quote_at(fill_boundary)
        if quote is None or quote[1] is None:
            closed += self._sell_after_failed_entry(event, fill_at)
            self._signal(
                event, decided, FAILED, ORDER_REJECTED, "Order rejected: the turbo was not quoted when the order arrived",
                closed_trade_ids=closed, candidates=counted, selection=choice.candidates,
            )
            self.trader_free_at = max(fill_at, self.trader_free_at)
            return
        bid, ask = quote
        later = self._next_quote(choice.series, fill_boundary)
        fill_ask = later[1] if later is not None and later[1] is not None else ask
        notes, flags = [], []
        if ask > 0 and abs(fill_ask / ask - 1) >= NOTED_REQUOTE_SHARE:
            notes.append(f"Priced at the turbo's next quote, {fill_ask:g}: the ask standing at the fill was {ask:g}")
        if later is None:
            # No new quote by then: a frozen ask is priced from the index, as Saxo fills at its live price.
            fill_ask = self._unfrozen(choice.series, fill_boundary, ask, ask - bid, notes, flags, "ask")
        # Fixed: the ask at the fill (or the next one Saxo sent), a slippage worse; measured: the quote when the order
        # left, a drawn slippage worse.
        price = round(self.fills.purchase(choice.ask, fill_ask), 6)
        cost = units * price
        commission = self._commission(cost)
        if cost + commission > self.cash + 1e-9:
            # Sized on the ask standing at the choice, it costs more at the price it fills at: Saxo refuses an order
            # the cash does not cover (the live sizing leaves one turbo aside and counts neither commission nor slippage).
            closed += self._sell_after_failed_entry(event, fill_at)
            self._signal(
                event, decided, FAILED, FUNDS_REFUSED,
                f"Order rejected: {units} turbos at {price:g} cost {cost + commission:.2f}, the account held "
                f"{self.cash:.2f} [InsufficientFunds]",
                closed_trade_ids=closed, candidates=counted, selection=choice.candidates,
            )
            self.trader_free_at = max(fill_at, self.trader_free_at)
            return

        self.cash -= cost + commission
        self._entries_today += 1
        position = Position(
            trade_id=self._next_trade,
            direction=event.action,
            series=choice.series,
            units=units,
            open_price=price,
            opened_at=fill_at,
            cost=cost + commission,
            commission=commission,
            signal_id=event.id,
            confidence=event.confidence,
            scale_percent=round(scale * 100, 2),
            day_percent_before=self.day_percent(),
            entry={
                "decided_at": utc_iso(decided),
                "selected_at": utc_iso(selection_at),
                "filled_at": utc_iso(fill_at),
                "ask": ask,
                "bid": bid,
                "selected_ask": choice.ask,
                "selected_bid": choice.bid,
                "price": price,
                "asset_price": self._asset_price(fill_at),
                "candidates": choice.candidates,
            },
            cursor=choice.series.first_from(fill_boundary),
            notes=notes,
            flags=flags,
        )
        self._next_trade += 1
        self.positions.append(position)
        self._signal(
            event, decided, EXECUTED, None, None, trade_id=position.trade_id, closed_trade_ids=closed, candidates=counted,
        )
        self.trader_free_at = fill_at
        if settings.reserve_cash_percent > 0:
            # A cash reserve: the new position first, then the others are sold.
            sale_at = fill_at + self.fills.order_seconds()
            others = [other for other in self.positions if other is not position]
            if others:
                self._monitor_until(ceil10(sale_at))
                for other in [other for other in self.positions if other is not position]:
                    self.signals[-1]["closed_trade_ids"].append(
                        self._close(other, sale_at, "Explicit Close (All)", signal_id=event.id, departed=fill_at),
                    )
                self.trader_free_at = sale_at

    def _sell_after_failed_entry(self, event: SignalEvent, failed_at: float) -> list[int]:
        """
        With a cash reserve, an entry that failed still sells what is held, as
        the trader does (``handle_trading_signal``): the signal turned the other way.
        """
        if self.settings.reserve_cash_percent <= 0 or not self.positions:
            return []
        sale_at = failed_at + self.fills.order_seconds()
        # The monitor goes on until the orders leave: it may have sold first.
        self._monitor_until(ceil10(sale_at))
        sold = [
            self._close(position, sale_at, "Explicit Close (All)", signal_id=event.id, departed=failed_at)
            for position in list(self.positions)
        ]
        if sold:
            self.trader_free_at = sale_at
        return sold

    def _close_signal(self, event: SignalEvent, decided: float) -> None:
        side = CLOSE_ACTIONS[event.action]
        sale_at = decided + self.fills.order_seconds()
        if any(side is None or position.direction == side for position in self.positions):
            # The monitor goes on until the orders leave: it may have sold first.
            self._monitor_until(ceil10(sale_at))
        targets = [position for position in self.positions if side is None or position.direction == side]
        if not targets:
            self._signal(event, decided, IGNORED, None, "No open position to close")
            self.trader_free_at = decided
            return
        scope = side or "All"
        closed = [
            self._close(position, sale_at, f"Explicit Close ({scope})", signal_id=event.id, departed=decided)
            for position in targets
        ]
        self._signal(
            event, decided, CLOSED, None, f"Closed {len(closed)} position(s). Errors: 0", closed_trade_ids=closed,
        )
        self.trader_free_at = sale_at

    # ── the position monitor ──

    def _monitor_until(self, until: float) -> None:
        """Every ten seconds of the positions held that were over by ``until``, in time order."""
        while self.positions:
            # The position whose next ten seconds come first.
            waiting = [
                (position.series.ts[position.cursor], position) for position in self.positions
                if not position.orphan and position.cursor < len(position.series.ts)
                and position.series.ts[position.cursor] + BAR_SECONDS <= until
            ]
            if not waiting:
                break
            _, position = min(waiting, key=lambda pair: (pair[0], pair[1].trade_id))
            self._look_at_bar(position)
        for position in list(self.positions):
            self._after_last_bar(position, until)

    def _look_at_bar(self, position: Position) -> None:
        series, index = position.series, position.cursor
        start = series.ts[index]
        gap_from = None
        if index > 0 and start - series.ts[index - 1] > BAR_SECONDS:
            gap_from = series.ts[index - 1] + BAR_SECONDS
            self._note_gap(position, gap_from, start)
        position.cursor = index + 1
        if position.pending_sale is not None:
            # A sale Saxo could not take: sold once the turbo is quoted again.
            self._sell_pending(position, index)
            return
        if gap_from is not None:
            # Live goes on during a hole: on the index while its estimate may be believed, and towards a release.
            if self._judged_during_hole(position, gap_from, start):
                return
            flat = self._flat_window_between(gap_from, start)
            if flat is not None:
                window, since = flat
                position.pending_sale = {
                    "reason": f"Performance ({flat_reason(window, self.settings.timezone)})",
                    "kind": FLAT_BEFORE_RELEASE, "signal_id": None, "since": since,
                }
                self._sell_pending(position, index)
                return
        changed = series.changed(index)
        # Between Saxo's quotes, on the bid estimated from the index: before the quote this bar brings, or through it.
        if self._judged_between_quotes(position, start, whole=not changed):
            return
        opened, high, low, close = series.bid_open[index], series.bid_high[index], series.bid_low[index], series.bid_close[index]
        middle = (low, high) if close >= opened else (high, low)
        points = ((start, opened, True), (start + BAR_SECONDS, middle[0], False),
                  (start + BAR_SECONDS, middle[1], False), (start + BAR_SECONDS, close, False))
        for moment, bid, at_open in points:
            perf = performance(position.open_price, bid)
            # A bid still the one standing at the purchase is not judged, as the monitor does not (src/trade/fresh_quote.py).
            judged = self.fresh_quotes.judged(position.trade_id, bid, position.opened_at, moment)
            decision = exit_decision(
                self.settings, open_price=position.open_price, perf=perf, best_before=position.best,
                day_percent=self.day_percent(),
            ) if judged else None
            if decision is None:
                flat = self._flat_window(moment)
                if flat is not None:
                    # The monitor's last check (``_evaluate_positions``, ``keep_flat``): at the bid of that moment.
                    delay = self.fills.exit_seconds()
                    later = self._next_quote(series, ceil10(moment))
                    self._sell(position, moment + delay if delay else moment, later[0] if later is not None else bid,
                               f"Performance ({flat_reason(flat, self.settings.timezone)})", kind=FLAT_BEFORE_RELEASE)
                    return
                if judged:
                    position.best = max(position.best, perf)
                    position.worst = perf if position.worst is None else min(position.worst, perf)
                continue
            if at_open or decision.kind not in (STOP_LOSS, TRAILING) or self.settings.stop_fill == "worst":
                # The quote that decided it: what the monitor sees and sells on, a take-profit as a stop (never a
                # level no quote reached).
                price = bid
            else:
                # A stop crossed inside the ten seconds, ``stop_fill`` "level": at the first quote at or under its price.
                price = max(series.tick_at_or_below(decision.level), bid)
            # Measured fills: the sale order takes its drawn time to be filled.
            delay = self.fills.exit_seconds()
            self._sell(position, moment + delay if delay else moment, price, f"Performance ({decision.reason})", kind=decision.kind)
            return
        if changed or self.between_quotes.anchor(position.trade_id) is None:
            self._note_quote(position, index, start)

    def _note_quote(self, position: Position, index: int, start: int) -> None:
        """
        Saxo's new quote of a position it may be judged on (``_note_quote`` of the monitor): where its estimates
        between quotes start from, the index read in the middle of the ten seconds the quote came in.
        """
        if not self.between_quotes.enabled or position.trade_id in self.fresh_quotes.waiting:
            return
        bars = self._day.index if self._day is not None else None
        place = bars.bar_at(start) if bars is not None else None
        self.between_quotes.quoted(
            position.trade_id, position.series.bid_close[index], bars.middle(place) if place is not None else None,
            start + BAR_SECONDS / 2, TURBO_DIRECTIONS[position.direction],
        )

    def _judged_between_quotes(self, position: Position, start: int, *, whole: bool) -> bool:
        """
        The monitor's look at a position between Saxo's quotes (``judge_between_quotes``), on the index's bar of
        those ten seconds: at its open, then (``whole``: no new quote of the turbo in them) its low and high in the
        order the index went, the nearer to its close last, and its close. Sold at the estimate that decided it,
        as on a quote of the turbo. Whether it was sold.
        """
        bars = self._day.index if self._day is not None else None
        if not self.between_quotes.enabled or bars is None or position.trade_id in self.fresh_quotes.waiting:
            return False
        place = bars.bar_at(start)
        if place is None:
            return False
        opened, high, low, close = bars.open[place], bars.high[place], bars.low[place], bars.close[place]
        middle = (low, high) if close >= opened else (high, low)
        points = ((start, opened, True),)
        if whole:
            points += ((start + BAR_SECONDS, middle[0], False), (start + BAR_SECONDS, middle[1], False),
                       (start + BAR_SECONDS, close, False))
        series = position.series
        for moment, level, at_open in points:
            estimate, _ = self.between_quotes.estimate(
                position.trade_id, index=level, index_at=moment, now=moment, barrier=series.ref.barrier,
            )
            if estimate is None:
                continue
            decision = exit_decision(
                self.settings, open_price=position.open_price, perf=performance(position.open_price, estimate.bid),
                best_before=position.best, day_percent=self.day_percent(),
            )
            if decision is None:
                continue
            bid = round(estimate.bid, series.decimals)
            if at_open or decision.kind not in (STOP_LOSS, TRAILING) or self.settings.stop_fill == "worst":
                price = bid
            else:
                price = max(series.tick_at_or_below(decision.level), bid)
            delay = self.fills.exit_seconds()
            reason = between_quotes_reason(decision.reason, self.settings.index_epic, estimate, moment)
            self._sell(position, moment + delay if delay else moment, price, f"Performance ({reason})", kind=decision.kind)
            return True
        return False

    def _flat_window(self, moment: float):
        """The release nothing is to be held into at ``moment``, when the macro_news rule sells before it."""
        news = self.settings.news_rule()
        if news is None or self.calendar is None:
            return None
        return flat_window_at(self.calendar, news, moment)

    def _note_gap(self, position: Position, first_missing: int, resumed: int) -> None:
        day = self._day
        heard = any(day.is_covered(stamp) for stamp in range(first_missing, resumed, BAR_SECONDS))
        what = "the turbo was not quoted" if heard else "the price recorder was not listening"
        position.notes.append(
            f"{self.clock.local(first_missing):%H:%M:%S}–{self.clock.local(resumed):%H:%M:%S}: {what}",
        )
        position.flag(FLAG_HOLE)

    def _after_last_bar(self, position: Position, until: float) -> None:
        """
        A turbo whose quotes stopped for good while the recorder listened: knocked out, at nothing, where the index
        reached its knock-out level; without the index, when its bid had fallen under KNOCKED_OUT_SHARE of the day's
        high. Otherwise held, as Saxo holds it, until it is sold.
        """
        series = position.series
        if position.orphan or position.cursor < len(series.ts) or not series.ts:
            return
        ended = series.ts[-1] + BAR_SECONDS
        if until <= ended:
            return
        if not any(self._day.is_covered(stamp) for stamp in range(ended, min(int(until), self._day.end), BAR_SECONDS)):
            return
        index = self._day.index
        if index is not None and len(index):
            crossed = index.crossing(series.ts[-1], min(until, self._day.end), series.ref.barrier, series.ref.direction)
            if crossed is None:
                return
            position.notes.append(
                f"Knocked out at {self.clock.local(crossed):%H:%M:%S}: no quote after "
                f"{self.clock.local(ended):%H:%M:%S}, the index at its knock-out level {series.ref.barrier:g}; "
                "sold at nothing (what Saxo pays back is not known)",
            )
            position.flag(FLAG_KNOCKED_OUT)
            self._sell(position, max(crossed, series.ts[-1]), 0.0, KNOCKED_OUT, kind="knocked_out", costs=False)
            return
        if series.bid_close[-1] < KNOCKED_OUT_SHARE * series.day_high():
            position.notes.append(f"No quote after {self.clock.local(ended):%H:%M:%S}: taken for knocked out")
            position.flag(FLAG_KNOCK_OUT_GUESS)
            self._sell(position, ended, 0.0, KNOCKED_OUT, kind="knocked_out", costs=False)

    # ── what live does while the turbo is not quoted ──

    def _judged_during_hole(self, position: Position, gap_from: int, resumed: int) -> bool:
        """
        The monitor's look on the index each ten seconds of a hole in the turbo's bars (``judge_between_quotes``):
        as long as its estimate may be believed (QUOTE_FRESH_SECONDS from Saxo's last quote). Whether it was sold.
        """
        bars = self._day.index if self._day is not None else None
        if not self.between_quotes.enabled or bars is None or position.trade_id in self.fresh_quotes.waiting:
            return False
        anchor = self.between_quotes.anchor(position.trade_id)
        if anchor is None:
            return False
        for start in range(gap_from, resumed, BAR_SECONDS):
            if start - anchor.at > QUOTE_FRESH_SECONDS:
                break
            if self._judged_between_quotes(position, start, whole=True):
                return True
        return False

    def _flat_window_between(self, gap_from: int, resumed: int):
        """The release window a hole of the turbo's bars ran into, and the moment it began then; None without one."""
        if self.settings.news_rule() is None or self.calendar is None:
            return None
        moment = gap_from
        while moment < resumed:
            window = self._flat_window(moment)
            if window is not None:
                return window, moment
            moment += 30
        return None

    def _sell_pending(self, position: Position, index: int) -> None:
        """
        The sale Saxo could not take, at the turbo's first new quote from the bar ``index`` (live: the monitor's
        attempts, spaced by close_intents ``retry_after``, the next one within MAX_RETRY_SECONDS of a quote). Never
        the bar's open when it is still the quote from before the hole: the recorder opens a bar after its own
        silence on that one.
        """
        pending, series = position.pending_sale, position.series
        # After the recorder's own silence, a bar opens on the quote from before it (src/price_recorder/bars.py).
        stale_open = index > 0 and series.ts[index] - series.ts[index - 1] > BAR_SECONDS and not any(
            self._day.is_covered(stamp) for stamp in range(series.ts[index - 1] + BAR_SECONDS, series.ts[index], BAR_SECONDS)
        )
        requoted = self._requote(series, index, stale_open=stale_open)
        if requoted is None:
            # Quoted, but still the frozen quote of before: sold at what the index says it is worth, or on it.
            at = self._retried_at(pending["since"], series.ts[index])
            bid = self._unfrozen(series, ceil10(at), series.bid_open[index], 0.0, position.notes, position.flags, "bid")
        else:
            quoted_at, bid = requoted
            at = self._retried_at(pending["since"], quoted_at)
            standing = series.quote_at(ceil10(at))
            if standing is not None and ceil10(at) > quoted_at:
                bid = standing[0]
            later = self._next_quote(series, ceil10(at)) if pending["kind"] == "explicit" else None
            if later is not None:
                bid = later[0]
        position.notes.append(
            f"Not quoted when it was to be sold ({self.clock.local(pending['since']):%H:%M:%S}): sold at "
            f"{self.clock.local(at):%H:%M:%S}, when Saxo quoted it again",
        )
        position.flag(FLAG_SOLD_AFTER_HOLE)
        position.pending_sale = None
        self._sell(position, at, bid, pending["reason"], kind=pending["kind"], signal_id=pending["signal_id"])

    @staticmethod
    def _requote(series: TurboSeries, index: int, *, stale_open: bool) -> tuple[float, float] | None:
        """
        The turbo's quote when Saxo quotes it again from the bar ``index`` on (its moment and bid): the bar's open,
        unless it is the quote from before the recorder's silence, then its first change within REQUOTE_SECONDS.
        """
        if not stale_open or series.bid_open[index] != series.bid_close[index - 1]:
            return float(series.ts[index]), series.bid_open[index]
        scan = index
        while scan < len(series.ts) and series.ts[scan] < series.ts[index] + REQUOTE_SECONDS:
            if series.changed_inside(scan):
                return float(series.ts[scan] + BAR_SECONDS), series.bid_close[scan]
            scan += 1
        return None

    @staticmethod
    def _retried_at(since: float, quoted_at: float) -> float:
        """When the monitor's attempts, begun at ``since``, sold once Saxo quoted the turbo at ``quoted_at``."""
        attempt, at = 1, since
        while at < quoted_at:
            at += retry_after(attempt, at - since)
            attempt += 1
        return min(at, quoted_at + MAX_RETRY_SECONDS)

    def _unfrozen(self, series: TurboSeries, boundary: int, price: float, spread: float, notes: list, flags: list,
                  side: str) -> float:
        """
        The price of an order at ``boundary`` on a quote Saxo has not changed for FROZEN_QUOTE_SECONDS or more: the
        bid the index says it is worth now (plus ``spread`` for an ask), with fixed fills, when the run has the
        index; else the frozen ``price``. Said in ``notes`` and ``flags`` either way.
        """
        age = series.quote_age(boundary)
        if age is not None and age < FROZEN_QUOTE_SECONDS:
            return price
        # Frozen only if the index moved meanwhile: a quote may stand still because nothing moved. Without the
        # index the two cannot be told apart, and the quote is taken as it stands, as before.
        estimate = self._index_bid(series, boundary)
        if estimate is None:
            return price
        standing = price - spread
        if abs(estimate - standing) < 10 ** -series.decimals - 1e-9:
            return price
        said = f"{age:.0f} s" if age is not None else "over an hour"
        if FLAG_FROZEN not in flags:
            flags.append(FLAG_FROZEN)
        if self.settings.execution != FIXED:
            # Measured fills price from the quote when the order left, as their measure compares.
            notes.append(f"Priced at a quote {said} old while the index moved: Saxo sent the turbo nothing since")
            return price
        unfrozen = round(estimate + spread, series.decimals)
        notes.append(f"Saxo sent the turbo nothing for {said} while the index moved: priced at the index's {unfrozen:g}, "
                     f"not its frozen {side} {price:g}")
        if FLAG_INDEX_PRICED not in flags:
            flags.append(FLAG_INDEX_PRICED)
        return unfrozen

    def _index_bid(self, series: TurboSeries, boundary: int) -> float | None:
        """
        The turbo's bid at ``boundary`` estimated from the index (src/trade/index_estimate.py): its last quote moved as
        the index moved since, at the euros per point learned from its quotes of the day, else the prior where it may
        be believed. None without the index, or when the estimate is not to be believed (MOST_STEP).
        """
        bars = self._day.index if self._day is not None else None
        found = series.last_change(boundary) if bars is not None and len(bars) else None
        if found is None:
            return None
        changed_at, place = found
        then, now = bars.level_at(changed_at), bars.level_at(boundary)
        if then is None or now is None:
            return None
        direction = TURBO_DIRECTIONS[series.ref.direction]
        learning = TurboSensitivity(direction)
        for earlier in range(max(0, place - 3000), place + 1):
            if earlier == 0 or series.changed(earlier):
                learning.quote(series.bid_close[earlier], bars.level_at(series.ts[earlier] + BAR_SECONDS / 2))
        last = series.bid_close[place]
        sensitivity = learning.value(last, then, series.ref.barrier)
        if sensitivity is None or last <= 0:
            return None
        bid = estimated_bid(last, then, now, sensitivity, direction)
        if bid <= 0 or abs(bid / last - 1) > MOST_STEP:
            return None
        return round(bid, series.decimals)

    # ── orders ──

    def _commission(self, amount: float) -> float:
        return self.settings.commission_per_order + amount * self.settings.commission_percent / 100.0

    def _close(self, position: Position, moment: float, reason: str, *, signal_id: int | None = None,
               departed: float | None = None) -> int:
        """
        A sell order filled at ``moment``, at the bid standing at the next boundary; it left at ``departed``. Not
        quoted then, the sale is left to the monitor (``pending_sale``), as live hands it over: sold when Saxo quotes
        the turbo again, its money only then.
        """
        if position.pending_sale is not None:
            return position.trade_id
        series = position.series
        boundary = ceil10(moment)
        quote = series.quote_at(boundary) if not position.orphan else None
        if quote is not None:
            left = series.quote_at(ceil10(departed)) if departed is not None else None
            later = self._next_quote(series, boundary)
            bid = later[0] if later is not None else quote[0]
            if quote[0] > 0 and abs(bid / quote[0] - 1) >= NOTED_REQUOTE_SHARE:
                position.notes.append(f"Sold at the turbo's next quote, {bid:g}: the bid standing at the fill was {quote[0]:g}")
            if later is None:
                bid = self._unfrozen(series, boundary, bid, 0.0, position.notes, position.flags, "bid")
            self._sell(position, moment, bid, reason, kind="explicit", signal_id=signal_id,
                       departure=left[0] if left is not None else None)
            return position.trade_id
        position.pending_sale = {"reason": reason, "kind": "explicit", "signal_id": signal_id, "since": moment}
        return position.trade_id

    def _next_quote(self, series: TurboSeries, boundary: int) -> tuple[float, float | None] | None:
        """
        The turbo's next quote after the one standing at ``boundary``, when orders
        are priced at it (``fill_price``): not with measured fills, which price an
        order at the quote when it left, as their measure compares.
        """
        if self.settings.fill_price != NEXT or self.settings.execution != FIXED:
            return None
        return series.next_quote(boundary)

    def _close_at_last(self, position: Position, moment: float, reason: str, *, signal_id: int | None = None) -> int:
        series = position.series
        last = series.last_before(min(moment, self._day.end))
        price = series.bid_close[last] if last is not None else position.open_price
        position.notes.append("No quote to sell at: closed at its last bid")
        self._sell(position, moment, price, reason, kind="no_quote", signal_id=signal_id)
        return position.trade_id

    def _sell(self, position: Position, moment: float, price: float, reason: str, *, kind: str,
              signal_id: int | None = None, costs: bool = True, departure: float | None = None) -> None:
        """
        The position sold at ``price``, the bid standing at the fill (``departure``:
        the one when the order left, if known). ``costs`` False: no order was sent
        (a knock-out), so neither slippage nor commission.
        """
        if costs and kind in MONITOR_EXITS and self.settings.exit_slippage_percent and self.settings.execution == FIXED:
            # The monitor's exits fill a little under the bid that decided them (EXIT_SLIPPAGE_PERCENT, measured).
            price = position.series.tick_at_or_below(price * (1 - self.settings.exit_slippage_percent / 100))
        if costs:
            price = self.fills.sale(departure if departure is not None else price, price)
        price = round(max(0.0, price), 6)
        gross = position.units * price
        commission = self._commission(gross) if costs else 0.0
        self.cash += gross - commission
        result = performance(position.open_price, price)
        # What the monitor saw last is the price it sold at, not the rest of those ten seconds.
        position.best = max(position.best, result)
        position.worst = result if position.worst is None else min(position.worst, result)
        self._day_percents.append(result)
        if result < 0:
            self.last_loss_at = moment
        self.positions.remove(position)
        profit = gross - commission - position.cost
        self.trades.append({
            "id": position.trade_id,
            "day": self.clock.day_of(position.opened_at).isoformat(),
            "direction": position.direction,
            "signal_id": position.signal_id,
            "confidence": position.confidence,
            "scale_percent": position.scale_percent,
            "turbo": position.series.ref.to_dict(),
            "entry": position.entry,
            "exit": {
                "filled_at": utc_iso(moment),
                "bid": price,
                "reason": reason,
                "group": exit_group(reason),
                "kind": kind,
                "signal_id": signal_id,
                "asset_price": self._asset_price(moment),
            },
            "units": position.units,
            "cost": round(position.cost, 2),
            "proceeds": round(gross - commission, 2),
            "commission": round(position.commission + commission, 2),
            "profit_loss": round(profit, 2),
            "performance_percent": result,
            "max_performance_percent": round(position.best, 2),
            "min_performance_percent": round(position.worst, 2) if position.worst is not None else None,
            "holding_seconds": round(moment - position.opened_at),
            "day_percent_before": position.day_percent_before,
            "cash_after": round(self.cash, 2),
            "notes": position.notes,
            "flags": position.flags,
        })

    def _asset_price(self, moment: float) -> float | None:
        asset = self._day.asset if self._day is not None else None
        return asset.at(moment) if asset is not None else None


def stored(waiting: list[SignalEvent]) -> list[SignalEvent]:
    """
    What a busy trader reads of the signals that came while it was busy: the web server keeps the newest of each
    indice (src/web_server/signal_store.py ``upsert``), by the signal's own time, a close-position never replacing an
    entry of the same moment, an older one never replacing a newer. In the order they came.
    """
    store: dict[str, SignalEvent] = {}
    for event in waiting:
        existing = store.get(event.indice)
        if existing is not None and (event.alert_at < existing.alert_at or (
            event.alert_at == existing.alert_at and event.action == "close-position" and existing.action in ENTRY_ACTIONS
        )):
            continue
        store[event.indice] = event
    return sorted(store.values(), key=lambda event: (event.arrived_at, event.id))


def _counted(candidates: list[dict]) -> dict:
    return {
        "looked_at": len(candidates),
        "quoted": sum(1 for candidate in candidates if candidate["bid"] is not None),
        "in_range": sum(1 for candidate in candidates if candidate["in_range"]),
    }


def finished_day(day: date, status: str) -> dict:
    """A day of the period that was not traded, and why."""
    return {
        "day": day.isoformat(), "status": status, "signals": {fate: 0 for fate in FATES}, "entries": 0, "trades": 0,
        "day_percent": 0.0, "profit_loss": 0.0, "equity": None, "recorded": None,
    }

