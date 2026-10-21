"""
How a run's orders are filled (the ``execution`` setting):

- ``fixed``, as always: each order filled ``order_seconds`` after it left, at
  the quote standing then, ``slippage`` worse; the position monitor's exits at
  the bar that decided them;
- ``measured``: each order's time to its fill and its price drawn from what
  WATA's own orders took and cost, the samples of the run's folder
  (``execution.json``, src/backtest/execution_calibration.py ``samples``). A
  purchase is priced at the quote when its order left plus a drawn slippage,
  since that is what the measure compares, and filled a drawn order time
  later; a sale likewise, the monitor's exits too.

The draws are seeded per simulator and day, so that a run gives the same files
again: the run itself and its sweep's cells draw alike, each repetition of the
random entries its own. Above the run's own ``order_seconds`` and ``slippage``,
a cell's (a stress sweep's later and dearer steps) add to every draw.
"""

import random

FIXED = "fixed"
MEASURED = "measured"
EXECUTIONS = (FIXED, MEASURED)
EXECUTION_FILE = "execution.json"
# What is drawn, and the measures of each a run needs at least.
DRAWN = ("order_seconds", "slippage_buy", "slippage_sell")
LEAST_SAMPLES = 10


def missing(samples: dict | None) -> list[str]:
    """What there are too few measures of to draw from, said as such; nothing when they will do."""
    found = samples or {}
    names = {"order_seconds": "order times", "slippage_buy": "purchase prices", "slippage_sell": "sale prices"}
    return [
        f"{len(found.get(kind) or [])} {names[kind]}" for kind in DRAWN if len(found.get(kind) or []) < LEAST_SAMPLES
    ]


class FixedFills:
    """The settings' own values, for every order."""

    def __init__(self, settings):
        self.settings = settings

    def start_day(self, day: str) -> None:
        pass

    def order_seconds(self) -> float:
        return self.settings.order_seconds

    def exit_seconds(self) -> float:
        # The position monitor's sale is taken at the bar that decided it.
        return 0.0

    def purchase(self, departure_ask: float, fill_ask: float) -> float:
        return fill_ask + self.settings.slippage

    def sale(self, departure_bid: float, fill_bid: float) -> float:
        return fill_bid - self.settings.slippage


class MeasuredFills:
    """Each order drawn from WATA's own: ``samples`` as ``execution.json`` keeps them."""

    def __init__(self, samples: dict, seed: str, *, later_seconds: float = 0.0, worse_price: float = 0.0):
        self.samples = {kind: list(samples[kind]) for kind in DRAWN}
        self.seed = seed
        self.later_seconds = later_seconds
        self.worse_price = worse_price
        self._draw = random.Random(seed)

    def start_day(self, day: str) -> None:
        self._draw = random.Random(f"{self.seed}:{day}")

    def order_seconds(self) -> float:
        return max(0.0, self._draw.choice(self.samples["order_seconds"])) + self.later_seconds

    def exit_seconds(self) -> float:
        return self.order_seconds()

    def purchase(self, departure_ask: float, fill_ask: float) -> float:
        return departure_ask + self._draw.choice(self.samples["slippage_buy"]) + self.worse_price

    def sale(self, departure_bid: float, fill_bid: float) -> float:
        return departure_bid - self._draw.choice(self.samples["slippage_sell"]) - self.worse_price


def fills_for(settings, samples: dict | None, seed: str, base=None):
    """
    How ``settings`` fill their orders. ``base``: the run's own settings, of
    which a sweep cell's later and dearer fills are counted on measured draws.
    """
    if settings.execution != MEASURED:
        return FixedFills(settings)
    if missing(samples):
        raise ValueError(f"Too few of WATA's orders measured to draw from: {', '.join(missing(samples))}")
    base = base or settings
    return MeasuredFills(
        samples, seed,
        later_seconds=max(0.0, settings.order_seconds - base.order_seconds),
        worse_price=max(0.0, settings.slippage - base.slippage),
    )
