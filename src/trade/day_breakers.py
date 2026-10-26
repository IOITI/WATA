"""
The day's circuit breakers (``risk_management.max_consecutive_losses`` and
``max_drawdown_from_day_best_percent``; 0, or absent, is off), counted on the
day's closed positions in the order they closed: no new entry for the rest of the
day once that many lost in a row, or once the day's result is that many points
under the best it reached at a close (0 before the first). The day's result is
compounded, as ``get_percent_of_the_day`` counts it.

The losses of 2 September to 20 October 2026 came on days of many trades (11 to
15 on 9, 14 and 19 October), which the −25 % day limit let reach −28.5 %.

Read by the trader, the backtests (src/backtest/rules.py) and the admin's
overview, for the dashboard's Next entry tile. Pure.
"""

from dataclasses import dataclass

LOSSES_KEY = "max_consecutive_losses"
DRAWDOWN_KEY = "max_drawdown_from_day_best_percent"

# Stable names of the two refusals, for the backtests' tallies.
LOSS_STREAK = "loss_streak"
DAY_DRAWDOWN = "day_drawdown"


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def losses_in_a_row(results) -> int:
    """The losing trades at the end of the day's closed ones, in the order they closed."""
    count = 0
    for result in reversed(list(results)):
        if result is None or result >= 0:
            break
        count += 1
    return count


def best_and_now(results) -> tuple[float, float]:
    """The best the day's compounded result reached at a close (0 before the first), and where it is now."""
    value, best = 1.0, 1.0
    for result in results:
        value *= 1 + (result if result is not None else 0) / 100.0
        best = max(best, value)
    return round((best - 1) * 100, 2), round((value - 1) * 100, 2)


@dataclass(frozen=True)
class DayBreakers:
    max_losses: int = 0
    max_drawdown: float = 0.0

    @classmethod
    def from_config(cls, risk_config: dict | None) -> "DayBreakers":
        risk_config = risk_config or {}
        losses, drawdown = risk_config.get(LOSSES_KEY), risk_config.get(DRAWDOWN_KEY)
        return cls(int(losses) if _number(losses) else 0, float(drawdown) if _number(drawdown) else 0.0)

    @property
    def on(self) -> bool:
        return self.max_losses > 0 or self.max_drawdown > 0

    def refusal(self, results) -> tuple[str, str] | None:
        """What refuses an entry after the day's closed ``results`` (its name and the trader's words), or None."""
        results = list(results)
        if self.max_losses > 0:
            streak = losses_in_a_row(results)
            if streak >= self.max_losses:
                return LOSS_STREAK, (
                    f"Breaking trading rule: {streak} losing trades in a row today ({self.max_losses} at most), "
                    f"so no more trade are allowed for today."
                )
        if self.max_drawdown > 0:
            best, now = best_and_now(results)
            fall = round(best - now, 2)
            if fall >= self.max_drawdown:
                return DAY_DRAWDOWN, (
                    f"Breaking trading rule: The day's result ({now}%) is {fall} points under its best ({best}%), "
                    f"{self.max_drawdown:g} at most, so no more trade are allowed for today."
                )
        return None


def refusal(risk_config: dict | None) -> str | None:
    """Why the breakers' settings in ``risk_management`` are not valid (None when they are, or are absent)."""
    risk_config = risk_config or {}
    losses, drawdown = risk_config.get(LOSSES_KEY), risk_config.get(DRAWDOWN_KEY)
    if losses is not None and (not isinstance(losses, int) or isinstance(losses, bool) or losses < 0):
        return f"{LOSSES_KEY} must be a whole number, 0 or more (0: off)"
    if drawdown is not None and (not _number(drawdown) or not 0 <= drawdown <= 1000):
        return f"{DRAWDOWN_KEY} must be between 0 and 1000 points (0: off)"
    return None
