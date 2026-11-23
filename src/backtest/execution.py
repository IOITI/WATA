"""
How a run's orders are filled (the ``execution`` setting):

- ``fixed``, as always: each order filled ``order_seconds`` after it left, at
  the quote standing then, ``slippage`` worse; the position monitor's exits at
  the bar that decided them;
- ``measured``: each order's time to its fill and its price drawn from what
  WATA's own orders took and cost, the samples of the run's folder
  (``execution.json``, src/backtest/execution_calibration.py ``samples``),
  filled a drawn order time later, the monitor's exits too. Its price is the
  one a fixed run gives it (the bid that decided it for an exit of the
  monitor a quote decided, the turbo's next quote for a purchase and any
  other sale) plus what WATA's orders paid beyond that same quote, drawn
  (``at_fill``).

  Until v0.100.0, and still for a run priced at the standing quote
  (``fill_price``) or made from samples of before: the quote when the order
  left plus a drawn slippage measured against that quote. Saxo sends a turbo a
  new quote every 30 s and fills at its live price, so that measure is mostly
  the turbo's move since its last quote; drawn at random for another order it
  became a cost that order never had, and its largest (a quote 60 s old, 9 %
  of the turbo) a stop-loss on the spot. The calibrated run of 23 November
  2026 made +11 % where the run it was made of made +163 %, with stops "hit
  at -7 %" filled at -15 %.

The draws are seeded per simulator and day, so that a run gives the same files
again: the run itself and its sweep's cells draw alike, each repetition of the
random entries its own. Above the run's own ``order_seconds`` and ``slippage``,
a cell's (a stress sweep's later and dearer steps) add to every draw.

``fill_noise_percent`` (a stress step's, 0 unless asked) moves each fill price at
random by up to that share of it, either way, drawn from ``fill_noise_seed``,
the simulator and the day: how much a total rests on a few tenths of a percent
of each fill.
"""

import random

FIXED = "fixed"
MEASURED = "measured"
EXECUTIONS = (FIXED, MEASURED)
EXECUTION_FILE = "execution.json"
# What is drawn, and the measures of each a run needs at least.
DRAWN = ("order_seconds", "slippage_buy", "slippage_sell")
# ...and for an order priced where a fixed run prices it: what WATA's purchases, its sales between two quotes and
# its monitor's exits on a quote paid beyond that quote (the two kinds of sales drawn as one while either is short).
AT_FILL = ("slippage_buy_next", "slippage_sell_next", "slippage_exit")
LEAST_SAMPLES = 10
# settings.NEXT, which this module cannot import (settings imports it): held equal by a test.
NEXT_FILL = "next"


def priced_at_fill(settings, samples: dict | None) -> bool:
    """
    Whether a measured run prices its orders where a fixed run does and draws
    what WATA's orders paid beyond that: it fills at the turbo's next quote,
    and its samples hold those measures (those kept before v0.100.0 do not).
    """
    return getattr(settings, "fill_price", None) == NEXT_FILL and isinstance(samples, dict) and AT_FILL[0] in samples


def missing(samples: dict | None, *, at_fill: bool | None = None) -> list[str]:
    """
    What there are too few measures of to draw from, said as such; nothing when
    they will do. ``at_fill``: for a run that draws what orders paid beyond the
    quote it fills them at (told from the samples themselves unless given).
    """
    found = samples or {}
    if at_fill is None:
        at_fill = AT_FILL[0] in found
    counted = {kind: len(found.get(kind) or []) for kind in (*DRAWN, *AT_FILL)}
    if at_fill:
        asked = (("order times", counted["order_seconds"]), ("purchase prices", counted["slippage_buy_next"]),
                 ("sale prices", counted["slippage_sell_next"] + counted["slippage_exit"]))
    else:
        asked = (("order times", counted["order_seconds"]), ("purchase prices", counted["slippage_buy"]),
                 ("sale prices", counted["slippage_sell"]))
    return [f"{count} {name}" for name, count in asked if count < LEAST_SAMPLES]


class Noise:
    """Each fill moved at random by up to ``percent`` of its price (``fill_noise_percent``), seeded per day."""

    def __init__(self, percent: float, seed: str):
        self.share = max(0.0, float(percent or 0.0)) / 100.0
        self.seed = seed
        self._draw = random.Random(seed)

    def start_day(self, day: str) -> None:
        self._draw = random.Random(f"{self.seed}:{day}")

    def __call__(self, price: float) -> float:
        if not self.share:
            return price
        return price * (1.0 + self._draw.uniform(-self.share, self.share))


def _noise(settings, seed: str) -> Noise:
    return Noise(getattr(settings, "fill_noise_percent", 0.0), f"{getattr(settings, 'fill_noise_seed', 1)}:{seed}")


class FixedFills:
    """The settings' own values, for every order."""

    # An order is priced at the quote of its fill (the next one Saxo sent, with ``fill_price`` "next").
    at_fill = True

    def __init__(self, settings, seed: str = ""):
        self.settings = settings
        self.noise = _noise(settings, seed)

    def start_day(self, day: str) -> None:
        self.noise.start_day(day)

    def order_seconds(self) -> float:
        return self.settings.order_seconds

    def exit_seconds(self) -> float:
        # The position monitor's sale is taken at the bar that decided it.
        return 0.0

    def purchase(self, departure_ask: float, fill_ask: float) -> float:
        return self.noise(fill_ask + self.settings.slippage)

    def sale(self, departure_bid: float, fill_bid: float, *, decided: bool = False) -> float:
        return self.noise(fill_bid - self.settings.slippage)


class MeasuredFills:
    """
    Each order drawn from WATA's own: ``samples`` as ``execution.json`` keeps
    them. ``at_fill``: priced at the quote of its fill, as a fixed run prices
    it, plus what WATA's orders paid beyond that quote; else at the quote when
    it left plus what they paid beyond that one (the runs made before).
    """

    def __init__(self, samples: dict, seed: str, *, later_seconds: float = 0.0, worse_price: float = 0.0,
                 noise: Noise | None = None, at_fill: bool = False):
        self.at_fill = at_fill
        self.samples = {kind: list(samples.get(kind) or []) for kind in ((*DRAWN, *AT_FILL) if at_fill else DRAWN)}
        if at_fill:
            # A sale by a signal and an exit of the monitor are measured against different quotes: each draws from
            # its own, and from both while its own are too few to draw from.
            sells, exits = self.samples["slippage_sell_next"], self.samples["slippage_exit"]
            self._sells = sells if len(sells) >= LEAST_SAMPLES else sells + exits
            self._exits = exits if len(exits) >= LEAST_SAMPLES else sells + exits
        self.seed = seed
        self.later_seconds = later_seconds
        self.worse_price = worse_price
        self.noise = noise or Noise(0.0, seed)
        self._draw = random.Random(seed)

    def start_day(self, day: str) -> None:
        self._draw = random.Random(f"{self.seed}:{day}")
        self.noise.start_day(day)

    def order_seconds(self) -> float:
        return max(0.0, self._draw.choice(self.samples["order_seconds"])) + self.later_seconds

    def exit_seconds(self) -> float:
        return self.order_seconds()

    def purchase(self, departure_ask: float, fill_ask: float) -> float:
        if self.at_fill:
            return self.noise(fill_ask + self._draw.choice(self.samples["slippage_buy_next"]) + self.worse_price)
        return self.noise(departure_ask + self._draw.choice(self.samples["slippage_buy"]) + self.worse_price)

    def sale(self, departure_bid: float, fill_bid: float, *, decided: bool = False) -> float:
        """``decided``: an exit of the position monitor, sold on the bid that decided it; else a sale between two quotes."""
        if self.at_fill:
            return self.noise(fill_bid - self._draw.choice(self._exits if decided else self._sells) - self.worse_price)
        return self.noise(departure_bid - self._draw.choice(self.samples["slippage_sell"]) - self.worse_price)


def fills_for(settings, samples: dict | None, seed: str, base=None):
    """
    How ``settings`` fill their orders. ``base``: the run's own settings, of
    which a sweep cell's later and dearer fills are counted on measured draws.
    """
    if settings.execution != MEASURED:
        return FixedFills(settings, seed)
    at_fill = priced_at_fill(settings, samples)
    short = missing(samples, at_fill=at_fill)
    if short:
        raise ValueError(f"Too few of WATA's orders measured to draw from: {', '.join(short)}")
    base = base or settings
    return MeasuredFills(
        samples, seed,
        later_seconds=max(0.0, settings.order_seconds - base.order_seconds),
        worse_price=max(0.0, settings.slippage - base.slippage),
        noise=_noise(settings, seed),
        at_fill=at_fill,
    )
