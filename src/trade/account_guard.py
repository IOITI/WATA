"""
The account guard (``risk_management.pause_below_balance_eur`` and
``pause_after_drawdown_percent``; 0, or absent, is off): new entries are paused
once the account is worth less than a floor, or a share less than the highest it
was worth since entries were last resumed. It sets the operator's own pause
(``trade_risk_state``), so that entries stay refused until the operator resumes
them from the admin, and the pause says it was the guard's.

The value is Saxo's ``TotalValue`` (cash and positions at their value), read with
the balance the trader asks for on its keep-warm timer and before sizing an
order: no request of its own. The highest is the larger of the daily values the
report records since entries were last resumed (``account_balance_history``) and
those the trader read since it started. A deposit raises the highest; a
withdrawal reads as a fall.

Read by the trader, mirrored by the backtests (src/backtest/rules.py). Pure.
"""

from dataclasses import dataclass

BELOW_KEY = "pause_below_balance_eur"
DRAWDOWN_KEY = "pause_after_drawdown_percent"


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


@dataclass(frozen=True)
class AccountGuard:
    below_eur: float = 0.0
    drawdown_percent: float = 0.0

    @classmethod
    def from_config(cls, risk_config: dict | None) -> "AccountGuard":
        risk_config = risk_config or {}
        below, drawdown = risk_config.get(BELOW_KEY), risk_config.get(DRAWDOWN_KEY)
        return cls(float(below) if _number(below) else 0.0, float(drawdown) if _number(drawdown) else 0.0)

    @property
    def on(self) -> bool:
        return self.below_eur > 0 or self.drawdown_percent > 0

    def trip(self, value: float | None, peak: float | None) -> str | None:
        """Why entries pause with the account worth ``value`` and its highest at ``peak``; None when they need not."""
        if value is None:
            return None
        if self.below_eur > 0 and value < self.below_eur:
            return f"The account is worth {value:.2f} €, under the account guard's {self.below_eur:g} €"
        if self.drawdown_percent > 0 and peak is not None and peak > 0:
            fall = (peak - value) / peak * 100
            if fall >= self.drawdown_percent:
                return (
                    f"The account is worth {value:.2f} €, {fall:.1f} % under its highest since entries were last "
                    f"resumed ({peak:.2f} €): the account guard pauses at {self.drawdown_percent:g} %"
                )
        return None


def refusal(risk_config: dict | None) -> str | None:
    """Why the guard's settings in ``risk_management`` are not valid (None when they are, or are absent)."""
    risk_config = risk_config or {}
    below, drawdown = risk_config.get(BELOW_KEY), risk_config.get(DRAWDOWN_KEY)
    if below is not None and (not _number(below) or below < 0):
        return f"{BELOW_KEY} must be a number of euros, 0 or more (0: off)"
    if drawdown is not None and (not _number(drawdown) or not 0 <= drawdown <= 100):
        return f"{DRAWDOWN_KEY} must be between 0 and 100 (0: off)"
    return None
