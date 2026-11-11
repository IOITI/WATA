"""
A purchase Saxo refuses for funds is sent again with fewer turbos
(``trade.config.buying_power.safety_margins``: ``funds_retry_percent``, 4 unless set, 0 for none, and
``funds_retries``, 2 unless set). The trader and the backtests' simulator take their attempts from here
(``tests/test_backtest_parity.py``).

WATA sizes an order on the turbo's last quote and leaves one turbo aside (``safety_margins.bid_calculation``). Saxo
sends that quote only about every 30 seconds, and it checks the cash, and fills, at its live price. In a fast market the
live price is a few percent dearer, so an order sized on the whole account no longer fits: Saxo refuses it
(``InsufficientFunds``) and the entry is lost. A backtest of 15 October to 11 November 2026 (run
20261111-130923-8c1c4c) had 22 of its 54 orders refused this way. Their fills were 0.7 to 13.9 % above the ask the order
was sized on, 2.9 % at the median.

So after such a refusal the order is sized again on the ask raised by ``percent`` for each attempt so far (4 %, then
8 %), at most ``retries`` times. Each attempt asks for fewer turbos than the order refused before it. The ask is the one
the turbo was chosen on, since WATA has no newer price for it. At 4 % and twice, 21 of those 22 entries were bought, at
96 to 98 % of the account. A refusal for any other reason is not retried here. Pure.
"""

from dataclasses import dataclass

PERCENT_KEY = "funds_retry_percent"
RETRIES_KEY = "funds_retries"
DEFAULT_PERCENT = 4.0
DEFAULT_RETRIES = 2
MAX_PERCENT = 25.0
MAX_RETRIES = 3


@dataclass(frozen=True)
class FundsRetry:
    """How an order refused for funds is sized again: ``percent`` higher above the ask each time, ``retries`` times."""

    percent: float = 0.0
    retries: int = 0

    @property
    def on(self) -> bool:
        return self.percent > 0 and self.retries > 0

    def margin(self, attempt: int) -> float:
        """How far above the ask, in percent, the ``attempt``-th order after a refusal (1, 2...) is sized."""
        return self.percent * attempt


def funds_retry(buying_power: dict | None) -> FundsRetry:
    """As ``trade.config.buying_power`` says: 4 % and twice unless set; 0 for either turns it off."""
    margins = (buying_power or {}).get("safety_margins") or {}
    percent = margins.get(PERCENT_KEY, DEFAULT_PERCENT)
    retries = margins.get(RETRIES_KEY, DEFAULT_RETRIES)
    return FundsRetry(float(percent or 0), int(retries or 0))


def sizing_ask(ask: float, margin_percent: float) -> float:
    """The ask an order is sized on, ``margin_percent`` above the turbo's own (the ask itself at 0)."""
    return ask * (1 + margin_percent / 100.0) if margin_percent else ask


def next_amount(previous: int, sized: int) -> int:
    """The turbos the next attempt asks for: as sized, but always fewer than the order Saxo refused (0: none left)."""
    return max(0, min(sized, previous - 1))


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def refusal(safety_margins: dict | None) -> str | None:
    """Why the retry's settings in ``buying_power.safety_margins`` are not valid (None when they are, or are absent)."""
    safety_margins = safety_margins or {}
    percent, retries = safety_margins.get(PERCENT_KEY), safety_margins.get(RETRIES_KEY)
    if percent is not None and (not _number(percent) or not 0 <= percent <= MAX_PERCENT):
        return f"{PERCENT_KEY} must be between 0 and {MAX_PERCENT:g} (0: an order refused for funds is not sent again)"
    if retries is not None and (not _number(retries) or retries != int(retries) or not 0 <= retries <= MAX_RETRIES):
        return f"{RETRIES_KEY} must be a whole number from 0 to {MAX_RETRIES}"
    return None
