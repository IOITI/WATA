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
"""

from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache

from src.admin.analysis import exit_group
from src.backtest.clock import BAR_SECONDS, TradingClock, ceil10, utc_iso
from src.backtest.data import DayData, TurboSeries
from src.backtest.execution import FixedFills
from src.backtest.rules import (
    STOP_LOSS,
    TRAILING,
    compounded,
    entry_refusal,
    exit_decision,
    performance,
    position_scale,
    units_for,
)
from src.backtest.selection import NotSelected, select_turbo
from src.backtest.settings import BacktestSettings
from src.trade.macro_calendar import Calendar, read_calendar

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

# A turbo no longer quoted while the recorder still listened, its bid by then under this share of
# its best of the day, is taken for knocked out (the rule of src/admin/prices.py ``availability``).
KNOCKED_OUT_SHARE = 0.25
KNOCKED_OUT = "SaxoAPI"
NO_QUOTE = "End of data (no quote)"


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


@lru_cache(maxsize=1)
def news_calendar() -> Calendar:
    """The calendar of US releases the trader reads, read once for every simulator of a run."""
    return read_calendar()


class Simulator:
    """One account traded day after day. ``run_day`` once per day, in order."""

    def __init__(self, settings: BacktestSettings, clock: TradingClock, *, asset_label: str | None = None,
                 calendar: Calendar | None = None, fills=None):
        self.settings = settings
        self.clock = clock
        # How its orders are filled: the settings' values, or drawn from WATA's own orders.
        self.fills = fills or FixedFills(settings)
        # The US releases, read only when no entry is to be made around them.
        self.calendar = calendar if calendar is not None or settings.news_rule() is None else news_calendar()
        self.cash = float(settings.capital)
        self.positions: list[Position] = []
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

    # ── the day ──

    def day_percent(self) -> float:
        """The day's result so far: its closed trades one after the other (``get_percent_of_the_day``)."""
        return compounded(self._day_percents)

    def run_day(self, day: DayData, events: list[SignalEvent]) -> dict:
        self._day = day
        self.fills.start_day(day.day.isoformat())
        self._day_percents = []
        self._entries_today = 0
        first_trade = len(self.trades)
        first_signal = len(self.signals)
        cash_before = self.cash
        for event in sorted(events, key=lambda event: (event.arrived_at, event.id)):
            decided = max(event.arrived_at, self.trader_free_at)
            self._monitor_until(decided)
            self._handle(event, decided)
        self._monitor_until(day.end)
        for position in list(self.positions):
            # Nothing sold it by the end of the day (no quote at the close): at its last bid.
            self._close_at_last(position, day.end, NO_QUOTE)
        return self._day_record(day, first_trade, first_signal, cash_before)

    def _day_record(self, day: DayData, first_trade: int, first_signal: int, cash_before: float) -> dict:
        trades = self.trades[first_trade:]
        signals = self.signals[first_signal:]
        by_fate = {fate: 0 for fate in FATES}
        for signal in signals:
            by_fate[signal["fate"]] = by_fate.get(signal["fate"], 0) + 1
        first, last = day.span()
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
            },
        }
        self.days.append(record)
        return record

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

    def _enter(self, event: SignalEvent, decided: float) -> None:
        settings = self.settings
        refusal = entry_refusal(
            settings, self.clock,
            action=event.action, indice=event.indice, decided_at=decided, alert_at=event.alert_at,
            open_directions=[position.direction for position in self.positions],
            day_percent=self.day_percent(), last_loss_at=self.last_loss_at,
            entries_today=self._entries_today, confidence=event.confidence, calendar=self.calendar,
        )
        if refusal is not None:
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
        choice = select_turbo(self._day, event.action, boundary, settings.turbo_min_price, settings.turbo_max_price)
        if isinstance(choice, NotSelected):
            self._signal(
                event, decided, choice.fate, choice.rule, choice.reason, closed_trade_ids=closed,
                candidates=_counted(choice.candidates), selection=choice.candidates,
            )
            self.trader_free_at = selection_at
            return

        scale = position_scale(settings, self.clock.minute_of_day(selection_at), event.confidence)
        units = units_for(settings, self.cash, choice.ask, scale)
        counted = _counted(choice.candidates)
        if units <= 0:
            self._signal(
                event, decided, FAILED, INSUFFICIENT_FUNDS, f"InsufficientFundsException: Insufficient funds @ {choice.ask}",
                closed_trade_ids=closed, candidates=counted, selection=choice.candidates,
            )
            self.trader_free_at = selection_at
            return

        fill_at = selection_at + self.fills.order_seconds()
        fill_boundary = ceil10(fill_at)
        quote = choice.series.quote_at(fill_boundary)
        if quote is None or quote[1] is None:
            self._signal(
                event, decided, FAILED, ORDER_REJECTED, "Order rejected: the turbo was not quoted when the order arrived",
                closed_trade_ids=closed, candidates=counted, selection=choice.candidates,
            )
            self.trader_free_at = fill_at
            return
        bid, ask = quote
        # Fixed: the quote at the fill, a slippage worse; measured: the quote when the order left, a drawn slippage worse.
        price = round(self.fills.purchase(choice.ask, ask), 6)
        cost = units * price
        commission = self._commission(cost)
        notes = []
        if cost + commission > self.cash + 1e-9:
            # The live sizing leaves one turbo aside and counts neither commission nor slippage.
            notes.append(
                f"The order cost {cost + commission - self.cash:.2f} more than the account held: Saxo may have refused it",
            )

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
                if position.cursor < len(position.series.ts) and position.series.ts[position.cursor] + BAR_SECONDS <= until
            ]
            if not waiting:
                break
            _, position = min(waiting, key=lambda pair: pair[0])
            self._look_at_bar(position)
        for position in list(self.positions):
            self._after_last_bar(position, until)

    def _look_at_bar(self, position: Position) -> None:
        series, index = position.series, position.cursor
        start = series.ts[index]
        if index > 0 and start - series.ts[index - 1] > BAR_SECONDS:
            self._note_gap(position, series.ts[index - 1] + BAR_SECONDS, start)
        position.cursor = index + 1
        opened, high, low, close = series.bid_open[index], series.bid_high[index], series.bid_low[index], series.bid_close[index]
        middle = (low, high) if close >= opened else (high, low)
        points = ((start, opened, True), (start + BAR_SECONDS, middle[0], False),
                  (start + BAR_SECONDS, middle[1], False), (start + BAR_SECONDS, close, False))
        for moment, bid, at_open in points:
            perf = performance(position.open_price, bid)
            decision = exit_decision(
                self.settings, open_price=position.open_price, perf=perf, best_before=position.best,
                day_percent=self.day_percent(),
            )
            if decision is None:
                position.best = max(position.best, perf)
                position.worst = perf if position.worst is None else min(position.worst, perf)
                continue
            if at_open:
                price = bid
            elif decision.kind in (STOP_LOSS, TRAILING):
                # The bid that crossed it, or the first quote at or under the stop's own price.
                price = bid if self.settings.stop_fill == "worst" else max(series.tick_at_or_below(decision.level), bid)
            else:
                # A take-profit or the day's target crossed inside the ten seconds: at the first quote that
                # reached it, never better than the bid seen.
                price = min(series.tick_at_or_above(decision.level), bid)
            # Measured fills: the sale order takes its drawn time to be filled.
            delay = self.fills.exit_seconds()
            self._sell(position, moment + delay if delay else moment, price, f"Performance ({decision.reason})", kind=decision.kind)
            return

    def _note_gap(self, position: Position, first_missing: int, resumed: int) -> None:
        day = self._day
        heard = any(day.is_covered(stamp) for stamp in range(first_missing, resumed, BAR_SECONDS))
        what = "the turbo was not quoted" if heard else "the price recorder was not listening"
        position.notes.append(
            f"{self.clock.local(first_missing):%H:%M:%S}–{self.clock.local(resumed):%H:%M:%S}: {what}",
        )

    def _after_last_bar(self, position: Position, until: float) -> None:
        """A turbo whose quotes stopped for good while the recorder listened: knocked out, at nothing."""
        series = position.series
        if position.cursor < len(series.ts) or not series.ts:
            return
        ended = series.ts[-1] + BAR_SECONDS
        if until <= ended:
            return
        if not any(self._day.is_covered(stamp) for stamp in range(ended, min(int(until), self._day.end), BAR_SECONDS)):
            return
        if series.bid_close[-1] < KNOCKED_OUT_SHARE * series.day_high():
            position.notes.append(f"No quote after {self.clock.local(ended):%H:%M:%S}: taken for knocked out")
            self._sell(position, ended, 0.0, KNOCKED_OUT, kind="knocked_out", costs=False)

    # ── orders ──

    def _commission(self, amount: float) -> float:
        return self.settings.commission_per_order + amount * self.settings.commission_percent / 100.0

    def _close(self, position: Position, moment: float, reason: str, *, signal_id: int | None = None,
               departed: float | None = None) -> int:
        """A sell order filled at ``moment``, at the bid standing at the next boundary; it left at ``departed``."""
        series = position.series
        boundary = ceil10(moment)
        quote = series.quote_at(boundary)
        if quote is not None:
            left = series.quote_at(ceil10(departed)) if departed is not None else None
            self._sell(position, moment, quote[0], reason, kind="explicit", signal_id=signal_id,
                       departure=left[0] if left is not None else None)
            return position.trade_id
        later = series.first_from(boundary)
        if later < len(series.ts):
            position.notes.append(
                f"Not quoted when the order was sent: sold at {self.clock.local(series.ts[later]):%H:%M:%S}, when it was again",
            )
            self._sell(position, series.ts[later], series.bid_open[later], reason, kind="explicit", signal_id=signal_id)
            return position.trade_id
        return self._close_at_last(position, moment, NO_QUOTE, signal_id=signal_id)

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
        })

    def _asset_price(self, moment: float) -> float | None:
        asset = self._day.asset if self._day is not None else None
        return asset.at(moment) if asset is not None else None


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

