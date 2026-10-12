"""
The strategy WATA has been trading from TradingView (kernel-multi-vwap.pine),
computed here on one feed.

On range bars of the index, a kernel regression of the closes is compared with
the average price of the session (its VWAP, restarted when the session
opens). An entry is the kernel crossing that average: above it, long; below
it, short. The reverse cross, or the end of the session, ends the trade.

Entries wait for three things, as in the Pine script: the session is open,
the prices of the session have spread enough around their average (once in
the session is enough), and the market is not racing or gapping.

What differs from the Pine script, and why:

- One feed, not the average of three providers.
- "Volume" is the number of quote changes in each ten seconds.
- Range bars are built from ten-second bars, so their times are known to ten
  seconds, and the day's bars always start from midnight of the trading
  timezone: a restart in the middle of the day gives the same bars.
- The session ends on the clock, not on the first bar that closes after it.
- ``entry_mode = cross_catchup`` adds an entry the Pine script does not have:
  when entries become allowed with the kernel already on one side of the
  average, there is no cross left to wait for, and that side is taken as soon
  as the kernel's slope agrees.

Pure logic: no clock of its own, no I/O.
"""

from collections import deque
from dataclasses import dataclass, fields, replace
from datetime import date
from typing import Callable

from src.price_recorder.bars import BAR_SECONDS
from src.price_recorder.range_bars import BuiltBar, RangeBarBuilder
from src.strategy.bars import IndexBar
from src.strategy.engine import FLAT, LONG, SHORT, Decision, LocalClock
from src.strategy.indicators import KernelRegression, SessionVwap

CROSS = "cross"
CATCH_UP = "catch-up"
REVERSE_CROSS = "reverse cross"
SESSION_END = "session end"

ENTRY_MODES = ("cross", "cross_catchup")
VWAP_WEIGHTS = ("updates", "time")

# The columns of a trace row: one per range bar, to compare with a chart.
TRACE_COLUMNS = ("ended", "close", "kernel", "vwap", "band", "entries_allowed", "target")

_BEFORE, _OPEN, _OVER = "before", "open", "over"
# "Bars since the last exit" when there has been none.
_LONG_AGO = 999
_SLOPE_MEMORY = 50


@dataclass(frozen=True)
class KernelVwapParams:
    # 60R on the TradingView chart: sixty ticks of 0.1.
    range_points: float = 6.0
    tick: float = 0.1
    kernel_style: str = "wave"
    kernel_bandwidth: int = 15
    # In the trading timezone.
    session_start: str = "08:00"
    session_end: str = "21:55"
    band_multiplier: float = 1.0
    # Entries wait until the band has been this wide once in the session, in points. 0: no wait.
    min_band_width: float = 20.0
    # Range bars to let pass after an exit before a new entry.
    cooldown_bars: int = 0
    # A racing market: this many range bars within this many seconds.
    fast_bars: int = 10
    fast_seconds: int = 10
    # A gap: a bar opening further from the last close than this many average bar sizes.
    gap_average_bars: int = 100
    gap_multiplier: float = 2.0
    entry_mode: str = "cross"
    # A catch-up entry waits while the kernel is further than this from the average, in points. 0: no limit.
    catchup_max_distance: float = 0.0
    vwap_weight: str = "updates"

    def __post_init__(self):
        if self.entry_mode not in ENTRY_MODES:
            raise ValueError(f"entry_mode must be one of {', '.join(ENTRY_MODES)}")
        if self.vwap_weight not in VWAP_WEIGHTS:
            raise ValueError(f"vwap_weight must be one of {', '.join(VWAP_WEIGHTS)}")
        if self.tick <= 0 or self.range_points < self.tick:
            raise ValueError("range_points must be one tick at least")
        if self.fast_bars < 2:
            raise ValueError("fast_bars must be 2 at least")

    def with_overrides(self, overrides: dict[str, str]) -> "KernelVwapParams":
        """The same parameters with some replaced by values given as text ("entry_mode" -> "cross_catchup")."""
        known = {spec.name: type(getattr(self, spec.name)) for spec in fields(self)}
        changes = {}
        for name, text in overrides.items():
            if name not in known:
                raise ValueError(f"Unknown parameter '{name}' (known: {', '.join(sorted(known))})")
            try:
                changes[name] = known[name](text)
            except ValueError:
                raise ValueError(f"Parameter '{name}' needs a {known[name].__name__}, not '{text}'") from None
        return replace(self, **changes)


class KernelVwap:
    def __init__(
        self,
        params: KernelVwapParams,
        clock: LocalClock,
        trace: Callable[[dict], None] | None = None,
    ):
        self._params = params
        self._clock = clock
        self._trace = trace
        self._range_ticks = round(params.range_points / params.tick)
        self.start_day(date(1970, 1, 1))

    def start_day(self, day: date) -> None:
        params = self._params
        self._session_start = self._clock.epoch(day, params.session_start)
        self._session_end = self._clock.epoch(day, params.session_end)
        self._session = _BEFORE

        self._ranges = RangeBarBuilder(self._range_ticks)
        self._kernel = KernelRegression(params.kernel_bandwidth, params.kernel_style)
        self._vwap = SessionVwap()

        self._position = FLAT
        self._bars_since_exit = _LONG_AGO
        self._band_was_wide = False
        self._entries_were_allowed = False
        self._catchup_armed = False

        self._previous_kernel: float | None = None
        self._previous_reference: float | None = None
        self._previous_close: float | None = None
        self._begins: deque[int] = deque(maxlen=params.fast_bars)
        self._sizes: deque[float] = deque(maxlen=params.gap_average_bars)
        self._slopes: deque[float] = deque(maxlen=_SLOPE_MEMORY)

    @property
    def position(self) -> str:
        return self._position

    def on_bar(self, bar: IndexBar) -> list[Decision]:
        params = self._params
        decisions: list[Decision] = []

        if self._session == _BEFORE and bar.start >= self._session_start:
            self._session = _OPEN if bar.start < self._session_end else _OVER
        if self._session == _OPEN and bar.start >= self._session_end:
            self._session = _OVER
            if self._position != FLAT:
                self._position = FLAT
                decisions.append(Decision(bar.start, FLAT, SESSION_END))

        if self._session == _OPEN:
            weight = bar.updates if params.vwap_weight == "updates" else 1
            self._vwap.add((bar.mid_high + bar.mid_low + bar.mid_close) / 3, weight)

        tick = params.tick
        ended = self._ranges.add(
            bar.start,
            round(bar.mid_open / tick), round(bar.mid_high / tick),
            round(bar.mid_low / tick), round(bar.mid_close / tick),
        )
        # A range bar is known when the ten seconds that ended it are over.
        known_at = bar.start + BAR_SECONDS
        for built in ended:
            decision = self._on_range_bar(built, known_at)
            if decision is not None:
                decisions.append(decision)
        return decisions

    def _is_risky(self, built: BuiltBar, open_price: float, size: float) -> bool:
        """The market is racing, or the bar opened on a gap: no entry on it."""
        params = self._params
        self._begins.append(built.began)
        racing = len(self._begins) == params.fast_bars and built.ended - self._begins[0] <= params.fast_seconds

        self._sizes.append(size)
        gapping = False
        if self._previous_close is not None and len(self._sizes) == params.gap_average_bars:
            average_size = sum(self._sizes) / len(self._sizes)
            gapping = abs(open_price - self._previous_close) > average_size * params.gap_multiplier
        return racing or gapping

    def _confidence(self, kernel: float, reference: float, band: float | None, slope: float, with_slope: bool) -> float:
        """The Pine script's score from 0 to 2: four parts of 0.5 at most."""
        steepest = max(self._slopes) if self._slopes else 0.0
        steepness = min(slope / steepest, 1.0) * 0.5 if steepest > 0 else 0.0
        width = max(band or 0.0, 0.001)
        depth = min(abs(kernel - reference) / width, 1.5) / 1.5 * 0.5
        # Providers cannot disagree when there is one.
        agreement = 0.5
        direction = 0.5 if with_slope else (0.15 if slope == 0 else 0.0)
        return round(steepness + depth + agreement + direction, 2)

    def _on_range_bar(self, built: BuiltBar, known_at: int) -> Decision | None:
        params = self._params
        tick = params.tick
        close = built.close * tick
        risky = self._is_risky(built, built.open * tick, (built.high - built.low) * tick)

        kernel = self._kernel.push(close)
        previous_kernel = self._previous_kernel
        both_kernels = kernel is not None and previous_kernel is not None
        rising = both_kernels and kernel > previous_kernel
        falling = both_kernels and kernel < previous_kernel
        slope = abs(kernel - previous_kernel) if both_kernels else 0.0
        self._slopes.append(slope)

        in_session = self._session == _OPEN
        reference = self._vwap.value if in_session else None
        deviation = self._vwap.deviation if in_session else None
        band = None if deviation is None else deviation * params.band_multiplier
        if band is not None and band >= params.min_band_width:
            self._band_was_wide = True

        previous_reference = self._previous_reference
        comparable = both_kernels and reference is not None and previous_reference is not None
        cross_above = comparable and kernel > reference and previous_kernel <= previous_reference
        cross_below = comparable and kernel < reference and previous_kernel >= previous_reference

        entries_allowed = (
            in_session and self._band_was_wide and kernel is not None
            and reference is not None and previous_reference is not None
        )
        rested = self._bars_since_exit >= params.cooldown_bars
        can_enter = entries_allowed and rested and not risky

        entry = None
        reason = CROSS
        if can_enter and cross_above and self._position != LONG:
            entry = LONG
        elif can_enter and cross_below and self._position != SHORT:
            entry = SHORT

        # Catch-up: armed when entries become allowed, given up at the first cross.
        if cross_above or cross_below:
            self._catchup_armed = False
        elif entries_allowed and not self._entries_were_allowed:
            self._catchup_armed = True
        self._entries_were_allowed = entries_allowed
        if entry is None and can_enter and self._catchup_armed and params.entry_mode == "cross_catchup":
            near_enough = params.catchup_max_distance == 0 or abs(kernel - reference) <= params.catchup_max_distance
            if near_enough and kernel < reference and falling and self._position != SHORT:
                entry, reason = SHORT, CATCH_UP
            elif near_enough and kernel > reference and rising and self._position != LONG:
                entry, reason = LONG, CATCH_UP
            if entry is not None:
                self._catchup_armed = False

        exited = (self._position == LONG and cross_below) or (self._position == SHORT and cross_above)

        decision = None
        if entry is not None:
            with_slope = rising if entry == LONG else falling
            self._position = entry
            decision = Decision(known_at, entry, reason, self._confidence(kernel, reference, band, slope, with_slope))
        elif exited:
            self._position = FLAT
            decision = Decision(known_at, FLAT, REVERSE_CROSS)
        self._bars_since_exit = 0 if exited else self._bars_since_exit + 1

        self._previous_kernel = kernel
        self._previous_reference = reference
        self._previous_close = close

        if self._trace is not None:
            self._trace({
                "ended": built.ended, "close": close, "kernel": kernel, "vwap": reference, "band": band,
                "entries_allowed": entries_allowed, "target": self._position,
            })
        return decision
