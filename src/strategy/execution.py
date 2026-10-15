"""
What WATA would have done with a strategy's decisions: the trader's rules on
each entry, a turbo bought, and the position monitor's exits.

The index alone says little about a WATA trade: the position monitor sells at
its stop, at its trailing stop, or as soon as the day's result reaches its
target, long before most moves are over. So a backtest applies those rules, on
the price a turbo would have had.

The rules are read from the same ``config.json`` as the services. They are
written a second time here, for a clock that is not the present:

- the trader's checks on an entry: ``handle_trading_signal`` (src/trader) and
  ``TradingRule`` (src/trade/rules.py);
- the monitor's exits: ``AsyncPerformanceMonitor._evaluate_positions``
  (src/trade/async_services.py);
- the day's result: ``_calculate_final_percentage`` (src/database/postgres.py).

A change to one of those must be made here too.

The turbo is a model, not a recorded price: its value is the distance from the
index to its knock-out level. Two numbers of it are assumptions until they
are calibrated on real trades (``TurboModel``).

Two kinds of decisions are replayed. A strategy says the position it wants:
going from long to short ends the long whatever becomes of the short. A signal
WATA received is an order to the trader (``entries_are_orders``): an entry it
refuses closes nothing, as in ``handle_trading_signal``.

Timing: a decision is known when its ten seconds end, and acted on at the open
of the next bar. Inside a bar the monitor is taken through its open, its low
and its high in the usual order, then its close. Neither way flatters the
result: a stop is sold at the worst price of the ten seconds that crossed it,
a target at the target and no better. ``Execution`` makes it less kind still:
a wait between a decision and its fill, and index points lost on each fill.

Every trade counts as the whole stake in the day's result, as WATA counts a
day. What WATA would really have put on each (its sizing by time of day and
by confidence) is kept with the trade as a share, and turned into money for a
starting capital by the report.

Pure logic: no clock of its own, no I/O.
"""

from dataclasses import MISSING, asdict, dataclass, field, fields
from datetime import date

from src.strategy.bars import IndexBar
from src.strategy.engine import FLAT, LONG, Decision, LocalClock, seconds_of

_HOUR = 3600
_MINUTE = 60

STOPLOSS = "Stoploss"
KNOCKED_OUT = "Knocked out"
TAKEPROFIT = "Takeprofit"
TRAILING_STOP = "Trailing stop"
DAILY_TARGET = "Daily profit target"
END_OF_DAY = "End of day"
SIGNAL = "Signal"
END_OF_DATA = "End of data"

REFUSED_HOURS = "Outside market hours"
REFUSED_CLOSED_DATE = "Market closed date"
REFUSED_RISKY_HOURS = "Risky market hours"
REFUSED_DAY_PROFIT = "Day profit reached"
REFUSED_DAY_LOSS = "Day loss limit reached"
REFUSED_COOLDOWN = "Cooldown after a loss"
REFUSED_TRADE_LIMIT = "Trade limit of the day reached"
REFUSED_DUPLICATE = "Same position already open"
REFUSED_CONFIDENCE = "Confidence below the minimum"


def _rule(config: dict, rule_type: str) -> dict | None:
    for rule in (config.get("trade") or {}).get("rules") or []:
        if rule.get("rule_type") == rule_type:
            return rule.get("rule_config") or {}
    return None


def _required_rule(config: dict, rule_type: str) -> dict:
    rule = _rule(config, rule_type)
    if rule is None:
        raise ValueError(f"Rule with type '{rule_type}' not found in the configuration.")
    return rule


@dataclass(frozen=True)
class WataRules:
    timezone: str
    trading_start_hour: int
    trading_end_hour: int
    risky_start_hour: int
    risky_start_minute: int
    # As written in the configuration: "25/12/2026".
    closed_dates: frozenset[str]
    close_position_time: str
    # The monitor sells when the day's result, with the open position, reaches this.
    day_profit_target: float
    # No new entry once the day's result is at or above this, or at or below the loss limit.
    no_entry_above_day_profit: float
    max_day_loss: float
    cooldown_after_loss_minutes: int
    # 0: no limit.
    max_trades_per_day: int
    stoploss_percent: float
    max_profit_percent: float
    trailing_enabled: bool
    trailing_activation_percent: float
    trailing_drawdown_percent: float
    # An entry whose confidence is known and under this is refused. 0: no such check.
    min_confidence: float = 0.0
    # What an order is sized on: the share of the account it may use, in percent, and the
    # turbos left unbought as a margin (``_calculate_bid_amount``, src/trade/async_services.py).
    funds_percent: float = 100.0
    safety_units: float = 1.0
    # Sizing by time of day: (first minute, minute it ends before, percent) of each period. Empty: always 100.
    time_scaling: tuple = ()
    # Sizing by confidence: (from, under, percent). Empty: always 100.
    confidence_scaling: tuple = ()
    # The confidence taken for an entry that comes without one.
    default_confidence: float = 1.0

    @classmethod
    def from_config(cls, config: dict) -> "WataRules":
        """The rules of a WATA configuration (the whole ``config.json``, as a dict)."""
        day_trading = _required_rule(config, "day_trading")
        market_hours = _required_rule(config, "market_hours")
        closed_dates = _required_rule(config, "market_closed_dates")
        risk = _rule(config, "risk_management") or {}
        trade_config = (config.get("trade") or {}).get("config") or {}
        thresholds = (trade_config.get("position_management") or {}).get("performance_thresholds") or {}
        trailing = thresholds.get("trailing_stop") or {}
        sizing = trade_config.get("position_sizing") or {}
        confidence = sizing.get("confidence_scaling") or {}
        time_of_day = sizing.get("time_of_day_scaling") or {}
        buying_power = trade_config.get("buying_power") or {}
        return cls(
            timezone=(trade_config.get("general") or {}).get("timezone", "Europe/Paris"),
            trading_start_hour=market_hours["trading_start_hour"],
            trading_end_hour=market_hours["trading_end_hour"],
            risky_start_hour=market_hours["risky_trading_start_hour"],
            risky_start_minute=market_hours["risky_trading_start_minute"],
            closed_dates=frozenset(closed_dates["market_closed_dates"]),
            close_position_time=day_trading["close_position_time"],
            day_profit_target=day_trading.get("percent_profit_wanted_per_days", 1.0),
            no_entry_above_day_profit=day_trading["dont_enter_trade_if_day_profit_is_more_than"],
            max_day_loss=day_trading["max_day_loss_percent"],
            cooldown_after_loss_minutes=risk.get("cooldown_after_loss_minutes", 0),
            max_trades_per_day=risk.get("max_trades_per_day", 0),
            stoploss_percent=thresholds.get("stoploss_percent", -15),
            max_profit_percent=thresholds.get("max_profit_percent", 60),
            trailing_enabled=trailing.get("enabled", False),
            trailing_activation_percent=trailing.get("activation_percent", 5),
            trailing_drawdown_percent=trailing.get("drawdown_percent", 8),
            min_confidence=confidence.get("min_confidence_threshold", 0.0) if confidence.get("enabled", False) else 0.0,
            funds_percent=max(
                0, buying_power.get("max_account_funds_to_use_percentage", 100) - buying_power.get("reserve_cash_percent", 0),
            ),
            safety_units=(buying_power.get("safety_margins") or {}).get("bid_calculation", 1),
            time_scaling=tuple(
                (
                    period["start_hour"] * 60 + period.get("start_minute", 0),
                    period["end_hour"] * 60 + period.get("end_minute", 0),
                    period.get("scale_percent", 100),
                )
                for period in time_of_day.get("periods") or []
            ) if time_of_day.get("enabled", False) else (),
            confidence_scaling=tuple(
                (rule["min_confidence"], rule["max_confidence"], rule.get("scale_percent", 100))
                for rule in confidence.get("scaling_rules") or []
            ) if confidence.get("enabled", False) else (),
            default_confidence=confidence.get("default_confidence", 1.0),
        )

    def position_scale(self, seconds: int, confidence: float | None) -> float:
        """
        The share of what an order may use that WATA puts on an entry at that
        time of day (seconds since midnight) with that confidence: 1 is all of
        it (``_calculate_position_scale``).
        """
        minute = seconds // _MINUTE
        by_time = next((percent for first, end, percent in self.time_scaling if first <= minute < end), 100)
        if confidence is None:
            confidence = self.default_confidence
        by_confidence = next(
            (percent for least, under, percent in self.confidence_scaling if least <= confidence < under), 100,
        )
        return by_time / 100.0 * by_confidence / 100.0

    def to_dict(self) -> dict:
        """The rules as plain values, to be written as JSON and read back with ``from_dict``."""
        return {
            **asdict(self), "closed_dates": sorted(self.closed_dates),
            "time_scaling": [list(period) for period in self.time_scaling],
            "confidence_scaling": [list(rule) for rule in self.confidence_scaling],
        }

    @classmethod
    def from_dict(cls, values: dict) -> "WataRules":
        """
        Rules written by ``to_dict``, some of them changed perhaps: every one
        must be there, and no other. A rule added since they were written is
        the exception: it takes the value that changes nothing.
        """
        names = [spec.name for spec in fields(cls)]
        unknown = sorted(set(values) - set(names))
        missing = [spec.name for spec in fields(cls) if spec.default is MISSING and spec.name not in values]
        if unknown or missing:
            raise ValueError(
                "Not a set of rules: "
                + "; ".join(part for part in (
                    f"unknown {', '.join(unknown)}" if unknown else "",
                    f"missing {', '.join(missing)}" if missing else "",
                ) if part)
            )
        return cls(**{
            **values, "closed_dates": frozenset(values["closed_dates"]),
            **{
                name: tuple(tuple(entry) for entry in values[name])
                for name in ("time_scaling", "confidence_scaling") if name in values
            },
        })


@dataclass(frozen=True)
class TurboModel:
    """
    A knock-out turbo priced from the index: its value is the distance to its
    knock-out level, bought at the ask and valued at the bid.

    ``points_per_unit`` and ``spread`` are assumptions until calibrated.
    """

    # What is paid for one turbo. The trader takes the lowest bid of its price range.
    entry_price: float = 4.4
    # Index points that move the turbo's price by 1.00: its ratio and the currency together.
    points_per_unit: float = 110.0
    # Ask minus bid.
    spread: float = 0.03

    def __post_init__(self):
        if self.points_per_unit <= 0 or self.spread < 0 or self.entry_price <= self.spread:
            raise ValueError("A turbo needs a price above its spread and a positive points_per_unit")

    def barrier(self, direction: str, index: float) -> float:
        """The knock-out level of the turbo bought at ``entry_price`` with the index there."""
        distance = (self.entry_price - self.spread / 2) * self.points_per_unit
        return index - distance if direction == LONG else index + distance

    def performance(self, direction: str, barrier: float, index: float) -> float:
        """Percent gained or lost on the bid, as the monitor computes it. -100 once knocked out."""
        distance = index - barrier if direction == LONG else barrier - index
        if distance <= 0:
            return -100.0
        bid = distance / self.points_per_unit - self.spread / 2
        # Adding zero turns a rounded "-0.0" into the 0.0 it is.
        return round(bid * 100 / self.entry_price - 100, 2) + 0.0

    def index_at(self, direction: str, barrier: float, performance: float) -> float:
        """Where the index is when the turbo shows that performance."""
        bid = (1 + performance / 100) * self.entry_price
        distance = (bid + self.spread / 2) * self.points_per_unit
        return barrier + distance if direction == LONG else barrier - distance


@dataclass(frozen=True)
class Execution:
    """How much less kind than the bars the fills are taken to be."""

    # Seconds between the moment a decision is known and its fill, on top of the wait for the next bar.
    fill_delay: int = 0
    # Index points lost against the trade on each fill, the entry and the exit alike.
    slippage: float = 0.0

    def __post_init__(self):
        if self.fill_delay < 0 or self.slippage < 0:
            raise ValueError("A fill is neither early nor better than the price: delay and slippage are 0 or more")


@dataclass
class _Position:
    direction: str
    reason: str
    confidence: float | None
    entry_at: int
    entry_index: float
    barrier: float
    scale: float = 1.0
    max_performance: float = 0.0
    min_performance: float = 0.0


@dataclass(frozen=True)
class Trade:
    direction: str
    # Why the strategy entered: "cross", "catch-up"...
    entry_reason: str
    confidence: float | None
    entry_at: int
    entry_index: float
    exit_at: int
    exit_index: float
    # Percent on the turbo, as WATA records it.
    performance: float
    max_performance: float
    exit_reason: str
    # The worst the monitor saw of it, at or below 0: what a stop has to leave room for.
    min_performance: float = 0.0
    # The share of what an order may use that WATA's sizing puts on it: 1 is all of it.
    scale: float = 1.0

    @property
    def points(self) -> float:
        """What the index did in the direction of the trade."""
        move = self.exit_index - self.entry_index
        return move if self.direction == LONG else -move


@dataclass(frozen=True)
class Refusal:
    at: int
    direction: str
    reason: str


@dataclass
class DayResult:
    day: date
    # The performances of the day's closed positions, in order.
    performances: list[float] = field(default_factory=list)
    entries: int = 0
    # Bars inside the market hours: a day with none had no price to trade on.
    market_bars: int = 0

    @property
    def percent(self) -> float:
        return compounded(self.performances)


def compounded(performances: list[float]) -> float:
    """Several results in percent as one, each applied on the one before."""
    value = 1.0
    for percent in performances:
        value *= 1 + percent / 100.0
    return round((value - 1) * 100, 2)


class Simulator:
    """
    Give it every bar in order with ``on_bar``, and each decision with
    ``decide`` once the bar that brought it is over.

    ``entries_are_orders``: the decisions are signals as the trader receives
    them, not the position a strategy wants. An entry the rules refuse then
    leaves an open position of the other side where it is.
    """

    def __init__(
        self,
        rules: WataRules,
        turbo: TurboModel,
        clock: LocalClock,
        *,
        entries_are_orders: bool = False,
        execution: Execution = Execution(),
    ):
        self._rules = rules
        self._turbo = turbo
        self._clock = clock
        self._entries_are_orders = entries_are_orders
        self._delay = execution.fill_delay
        self._slippage = execution.slippage
        self._close_time = seconds_of(rules.close_position_time)
        self._cooldown = rules.cooldown_after_loss_minutes * _MINUTE

        self._position: _Position | None = None
        self._pending: list[Decision] = []
        self._day: DayResult | None = None
        # 1 + the day's result so far: asked at every bar of a position, and only changed by a close.
        self._day_factor = 1.0
        self._last_loss_at: int | None = None
        self._last_bar: IndexBar | None = None

        self.trades: list[Trade] = []
        self.refusals: list[Refusal] = []
        self.days: list[DayResult] = []

    def decide(self, decision: Decision) -> None:
        self._pending.append(decision)

    @property
    def busy(self) -> bool:
        """A position is open, or a decision waits for the next bar: the bars matter to it."""
        return self._position is not None or bool(self._pending)

    def on_bar(self, bar: IndexBar) -> None:
        day, seconds = self._clock.at(bar.start)
        if self._day is None or day != self._day.day:
            # No bar at the closing time of the day before: sold at the first price there is.
            if self._position is not None:
                self._close(bar.start, bar.mid_open, END_OF_DAY)
            self._day = DayResult(day)
            self._day_factor = 1.0
            self.days.append(self._day)
        self._last_bar = bar
        if self._rules.trading_start_hour <= seconds // _HOUR < self._rules.trading_end_hour:
            self._day.market_bars += 1

        # At the open: the scheduler's end of day, then what the strategy decided before.
        if self._position is not None and seconds >= self._close_time:
            self._close(bar.start, bar.mid_open, END_OF_DAY)
        if self._pending:
            if self._delay:
                # Filled at the first bar that opens once the wait is over.
                due = [decision for decision in self._pending if decision.at + self._delay <= bar.start]
                self._pending = [decision for decision in self._pending if decision.at + self._delay > bar.start]
            else:
                due, self._pending = self._pending, []
            for decision in due:
                self._act(decision, bar, day, seconds)

        if self._position is not None and not self._nothing_sells(bar):
            rose = bar.mid_close >= bar.mid_open
            path = (
                (bar.mid_open, bar.mid_low, bar.mid_high, bar.mid_close) if rose
                else (bar.mid_open, bar.mid_high, bar.mid_low, bar.mid_close)
            )
            for step, index in enumerate(path):
                if self._monitor(bar.start, index, at_open=step == 0):
                    break

    def finish(self) -> None:
        """No more bars: a position still open is valued at the last price."""
        if self._position is not None and self._last_bar is not None:
            self._close(self._last_bar.start, self._last_bar.mid_close, END_OF_DATA)

    # ── the trader ──

    def _refusal(self, at: int, day: date, seconds: int, direction: str, confidence: float | None) -> str | None:
        """Why the trader would refuse this entry, in the order it checks."""
        rules = self._rules
        hour, minute = seconds // _HOUR, seconds % _HOUR // _MINUTE
        if day.strftime("%d/%m/%Y") in rules.closed_dates:
            return REFUSED_CLOSED_DATE
        if not rules.trading_start_hour <= hour < rules.trading_end_hour:
            return REFUSED_HOURS
        if rules.risky_start_hour <= hour < rules.trading_end_hour and minute >= rules.risky_start_minute:
            return REFUSED_RISKY_HOURS
        if self._position is not None and self._position.direction == direction:
            return REFUSED_DUPLICATE
        today = self._day.percent
        if today >= rules.no_entry_above_day_profit:
            return REFUSED_DAY_PROFIT
        if today <= rules.max_day_loss:
            return REFUSED_DAY_LOSS
        if self._cooldown and self._last_loss_at is not None and at < self._last_loss_at + self._cooldown:
            return REFUSED_COOLDOWN
        if rules.max_trades_per_day > 0 and self._day.entries >= rules.max_trades_per_day:
            return REFUSED_TRADE_LIMIT
        if confidence is not None and confidence < rules.min_confidence:
            return REFUSED_CONFIDENCE
        return None

    def _act(self, decision: Decision, bar: IndexBar, day: date, seconds: int) -> None:
        index = bar.mid_open
        if decision.target == FLAT:
            if self._position is not None and decision.closes in (None, self._position.direction):
                self._close(bar.start, index, f"{SIGNAL}: {decision.reason}")
            return

        reversal = self._position is not None and self._position.direction != decision.target
        # The strategy's exit closes the other side whatever becomes of the entry.
        if reversal and not self._entries_are_orders:
            self._close(bar.start, index, f"{SIGNAL}: reversal")

        reason = self._refusal(bar.start, day, seconds, decision.target, decision.confidence)
        if reason is not None:
            self.refusals.append(Refusal(bar.start, decision.target, reason))
            return
        # The trader closes what is open once the entry is accepted, not before.
        if reversal and self._entries_are_orders:
            self._close(bar.start, index, f"{SIGNAL}: reversal")

        self._day.entries += 1
        # Bought with the index further on by the slippage: the turbo starts that much behind.
        paid = index + self._slippage if decision.target == LONG else index - self._slippage
        self._position = _Position(
            direction=decision.target,
            reason=decision.reason,
            confidence=decision.confidence,
            entry_at=bar.start,
            entry_index=paid,
            barrier=self._turbo.barrier(decision.target, paid),
            scale=self._rules.position_scale(seconds, decision.confidence),
        )

    # ── the position monitor ──

    def _with_the_day(self, performance: float) -> float:
        """The day's result if the open position were closed at ``performance``."""
        return round((self._day_factor * (1 + performance / 100.0) - 1) * 100, 2)

    def _nothing_sells(self, bar: IndexBar) -> bool:
        """
        Whether no exit can be reached inside the bar, told from its two
        extremes: every exit is reached by the turbo's result going far enough
        one way, and it goes no further than them. Such a bar, which most bars
        of a position are, only leaves its best and its worst behind; the
        others are gone through step by step (``_monitor``), which decides.
        """
        rules, position = self._rules, self._position
        if position.direction == LONG:
            against, with_it = bar.mid_low, bar.mid_high
        else:
            against, with_it = bar.mid_high, bar.mid_low
        worst = self._turbo.performance(position.direction, position.barrier, against)
        best = self._turbo.performance(position.direction, position.barrier, with_it)
        if worst <= rules.stoploss_percent or best >= rules.max_profit_percent:
            return False
        peak = best if best > position.max_performance else position.max_performance
        if (
            rules.trailing_enabled and peak >= rules.trailing_activation_percent
            and peak - worst >= rules.trailing_drawdown_percent
        ):
            return False
        if self._with_the_day(best) >= rules.day_profit_target:
            return False
        position.max_performance = peak
        if worst < position.min_performance:
            position.min_performance = worst
        return True

    def _monitor(self, at: int, index: float, at_open: bool) -> bool:
        """The monitor's checks with the index at ``index``; True when it sold."""
        rules = self._rules
        position = self._position
        performance = self._turbo.performance(position.direction, position.barrier, index)
        best_before = position.max_performance
        if performance > position.max_performance:
            position.max_performance = performance
        if performance < position.min_performance:
            position.min_performance = performance

        reason = None
        if performance <= rules.stoploss_percent:
            reason = KNOCKED_OUT if performance <= -100 else STOPLOSS
        elif performance >= rules.max_profit_percent:
            reason = TAKEPROFIT
        if reason is None and rules.trailing_enabled and position.max_performance >= rules.trailing_activation_percent:
            if position.max_performance - performance >= rules.trailing_drawdown_percent:
                reason = TRAILING_STOP
        if reason is None and self._with_the_day(performance) >= rules.day_profit_target:
            reason = DAILY_TARGET
        if reason is None:
            return False

        # A target crossed inside the ten seconds is sold at the target: the
        # high of the bar is not a price the monitor would have obtained.
        if not at_open and reason in (TAKEPROFIT, DAILY_TARGET):
            target = rules.max_profit_percent
            if reason == DAILY_TARGET:
                target = ((1 + rules.day_profit_target / 100.0) / (1 + self._day.percent / 100.0) - 1) * 100
            if target < performance:
                index = self._turbo.index_at(position.direction, position.barrier, target)
                # What the bar did beyond the target was never seen by a position already sold.
                position.max_performance = max(best_before, round(target, 2))
        self._close(at, index, reason)
        return True

    def _close(self, at: int, index: float, reason: str) -> None:
        position = self._position
        # Sold with the index back by the slippage.
        index = index - self._slippage if position.direction == LONG else index + self._slippage
        performance = self._turbo.performance(position.direction, position.barrier, index)
        self._position = None
        self.trades.append(Trade(
            direction=position.direction,
            entry_reason=position.reason,
            confidence=position.confidence,
            entry_at=position.entry_at,
            entry_index=position.entry_index,
            exit_at=at,
            exit_index=index,
            performance=performance,
            max_performance=max(position.max_performance, performance),
            exit_reason=reason,
            min_performance=min(position.min_performance, performance),
            scale=position.scale,
        ))
        self._day.performances.append(performance)
        self._day_factor = 1 + self._day.percent / 100.0
        # A losing close starts the cooldown, whatever closed it.
        if performance < 0:
            self._last_loss_at = at
