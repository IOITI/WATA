"""
A turbo's bid between Saxo's quotes, estimated from the index.

Saxo quotes a turbo about every 30 s (28 to 34 s on 26-30 Oct 2026, the session at its full level) and fills an
order at its live price, while Capital.com's US100 moves several times a second: an exit judged on Saxo's last
quote acts on a price up to that old. Between two quotes, the turbo's bid is estimated from the index: Saxo's
last bid, plus the turbo's own sensitivity (euros of turbo per index point) times the index's move since that
quote.

The sensitivity is learned from the turbo's quote changes against the index at the same moments, never read
from its knock-out level: a TurboXL's financing level is not its barrier. Until it is learned, a prior stands for
it: on the eight Turbos and TurboXL, long and short, of 26-30 Oct 2026, every one moved 0.0083 to 0.0089 € a point
(0.01 $ a point at the euro's rate), while its bid was 0.0093 to 0.0123 € per point of distance to its barrier. A
prior above that last figure is of another kind of turbo, and waits to be learned.

Measured on those turbos' 57,468 pairs of quotes 90 s apart at most (the index read at the middle of the
quote's ten seconds): the estimate was 0.120 % of the bid from Saxo's next quote at the median and 0.589 % at the
95th percentile, Saxo's last bid left standing 0.253 % and 1.371 %; the index of 20 s before did no better than
the standing bid. Live, each quote's moment is known to the second.

With ``trade.config.position_management.index_between_quotes`` (off unless true), the position monitor judges
every price exit on the estimate between quotes (``BetweenQuotes``), as on Saxo's own quotes, and the backtests'
simulator does the same on the index's ten-second bars.

Pure: the position monitor and the backtests' simulator use it alike.
"""

from collections import deque
from dataclasses import dataclass, field
from statistics import median

# Index moves smaller than this between two quotes teach nothing of the sensitivity: the quote's own moment,
# its spread and the issuer's rounding weigh more than the move.
MIN_MOVE_POINTS = 8.0
# The last moves it is learned from (their median), and how many it needs before it stands for the prior.
KEPT_MOVES = 20
LEAST_MOVES = 5
# Euros of turbo per index point until learned: 0.01 $ a point at the euro's rate (26-30 Oct 2026).
PRIOR_SENSITIVITY = 0.0086
# The index's quote is fresh this long; Saxo's last quote of the turbo, this long.
INDEX_FRESH_SECONDS = 2.0
QUOTE_FRESH_SECONDS = 90.0
# An estimate further than this share from Saxo's last bid is not believed: an index of another market's
# moment, a feed that jumped.
MOST_STEP = 0.25
# What a reason decided on the index says, after the exit's own words (which keep its group on the Exits page).
BETWEEN_QUOTES = "between quotes"
# A position's close reason is a column of 128 characters, "Performance (...)" around it.
REASON_MOST_CHARACTERS = 128 - len("Performance ()")
# How a turbo's bid follows the index, by the direction it was bought for.
TURBO_DIRECTIONS = {"long": 1, "short": -1}
# What the Capital.com feed records when its section names no instrument: the feed's DEFAULT_INSTRUMENTS
# (src/capital_feed/settings.py, which this module does not import), held to it by tests/test_index_estimate.py.
FEED_DEFAULT_INSTRUMENTS = ({"epic": "US100", "label": "us100"},)


def index_between_quotes(position_management: dict | None) -> bool:
    """Whether the position monitor judges positions on the index between Saxo's quotes: not unless it says true."""
    return (position_management or {}).get("index_between_quotes") is True


def feed_instruments(capital_feed: dict | None) -> list:
    """
    The instruments the Capital.com feed records, as its section names them: the feed's own default (US100, labelled
    us100) when the section names none, as the feed itself reads it (``src/capital_feed/settings.py``). Until
    v0.85.0 a section without instruments named no index here, while the feed recorded US100.
    """
    feed = capital_feed if isinstance(capital_feed, dict) else {}
    if "instruments" not in feed:
        return [dict(instrument) for instrument in FEED_DEFAULT_INSTRUMENTS]
    return feed["instruments"] if isinstance(feed["instruments"], list) else []


def index_epic(capital_feed: dict | None, indice: str) -> str | None:
    """The Capital.com instrument an indice's index is read from, while the feed records it (``trade.config.capital_feed``)."""
    feed = capital_feed if isinstance(capital_feed, dict) else {}
    if not feed.get("enabled"):
        return None
    return next((str(instrument["epic"]) for instrument in feed_instruments(feed)
                 if isinstance(instrument, dict) and instrument.get("label") == indice and instrument.get("epic")), None)


def followed_epic(capital_feed: dict | None, indice_ids: dict | None) -> str | None:
    """
    The index the positions are judged on between quotes: the feed's instrument of the one indice the trader takes
    (``allowed_indices``). None with several, since a position does not say which indice it was bought for, and
    while the feed does not record it.
    """
    indices = list(indice_ids) if isinstance(indice_ids, dict) else []
    return index_epic(capital_feed, indices[0]) if len(indices) == 1 else None


@dataclass
class TurboSensitivity:
    """
    Euros of turbo per index point, learned from its quotes: each pair of quotes between which the index moved
    at least MIN_MOVE_POINTS gives one, the move counted from the quote the last pair ended at.
    """

    # 1 for a long turbo (its bid rises with the index), -1 for a short one.
    direction: int
    prior: float | None = PRIOR_SENSITIVITY
    _moves: deque = field(default_factory=lambda: deque(maxlen=KEPT_MOVES))
    _anchor: tuple[float, float] | None = None

    def quote(self, bid: float, index: float | None) -> None:
        """Saxo's new quote of the turbo, with the index at that moment (None when not known: the move is lost)."""
        if index is None or bid <= 0:
            self._anchor = None
            return
        if self._anchor is None:
            self._anchor = (bid, index)
            return
        moved = index - self._anchor[1]
        if abs(moved) < MIN_MOVE_POINTS:
            # Too small a move to learn from: counted from the same quote until the index has moved enough.
            return
        ratio = (bid - self._anchor[0]) / moved * self.direction
        if ratio > 0:
            self._moves.append(ratio)
        self._anchor = (bid, index)

    @property
    def moves(self) -> int:
        return len(self._moves)

    @property
    def learned(self) -> float | None:
        """The median of its moves, once there are LEAST_MOVES of them."""
        return median(self._moves) if len(self._moves) >= LEAST_MOVES else None

    def value(self, bid: float, index: float, barrier: float | None) -> float | None:
        """
        Its sensitivity: as learned, else the prior while it is below the turbo's price per point of distance to
        its barrier at a quote (``bid`` with the index then: what any turbo of that kind is above); None when
        neither can be believed.
        """
        learned = self.learned
        if learned is not None:
            return learned
        if self.prior is None or barrier is None or bid <= 0:
            return None
        distance = abs(index - barrier)
        return self.prior if distance > 0 and self.prior <= bid / distance else None


def estimated_bid(last_bid: float, last_index: float, index: float, sensitivity: float, direction: int) -> float:
    """The turbo's bid now: Saxo's last, moved as the index moved since it, at the turbo's sensitivity."""
    return last_bid + direction * sensitivity * (index - last_index)


def why_not_estimated(
    *,
    now: float,
    index_at: float | None,
    quoted_at: float | None,
    sensitivity: float | None,
    last_bid: float,
    last_index: float | None,
    estimate: float | None,
    index: float | None,
    barrier: float | None,
    direction: int,
) -> str | None:
    """Why the estimate is not to be judged on at ``now``; None when it is."""
    if index is None or index_at is None or now - index_at > INDEX_FRESH_SECONDS:
        return "no fresh quote of the index"
    if last_index is None:
        return "the index was not known at Saxo's last quote"
    if quoted_at is None or now - quoted_at > QUOTE_FRESH_SECONDS:
        return "Saxo's last quote of it is too old"
    if sensitivity is None:
        return "its sensitivity to the index is not known yet"
    if barrier is not None and (index - barrier) * direction <= 0:
        return "the index is at or past its knock-out level"
    if estimate is None or estimate <= 0 or last_bid <= 0 or abs(estimate / last_bid - 1) > MOST_STEP:
        return "the estimate is too far from Saxo's last bid"
    return None


@dataclass(frozen=True)
class Anchor:
    """Saxo's last quote of a position: its bid, the index at that moment (None when not known), and when."""

    bid: float
    index: float | None
    at: float


@dataclass(frozen=True)
class Estimate:
    """A position's bid estimated at a moment, from Saxo's last quote of it and the index then."""

    bid: float
    anchor: Anchor
    index: float


class BetweenQuotes:
    """
    What is kept of each position to judge it between Saxo's quotes: Saxo's last quote of it with the index at
    that moment, and its sensitivity learned from its quotes. Only quotes it may be judged on are given (not one
    older than its purchase, ``fresh_quote``). The position monitor and the simulator keep one each.
    """

    def __init__(self, enabled: bool = True, *, prior: float | None = PRIOR_SENSITIVITY):
        self.enabled = enabled
        self._prior = prior
        self._anchors: dict = {}
        self._learning: dict = {}

    def quoted(self, position_id, bid: float, index: float | None, at: float, direction: int) -> None:
        """Saxo's new quote of a position at ``at``, with the index then (None when it was not known)."""
        learning = self._learning.get(position_id)
        if learning is None or learning.direction != direction:
            learning = self._learning[position_id] = TurboSensitivity(direction, prior=self._prior)
        learning.quote(bid, index)
        self._anchors[position_id] = Anchor(bid, index, at)

    def anchor(self, position_id) -> Anchor | None:
        return self._anchors.get(position_id)

    def sensitivity(self, position_id) -> TurboSensitivity | None:
        return self._learning.get(position_id)

    def estimate(self, position_id, *, index: float | None, index_at: float | None, now: float,
                 barrier: float | None) -> tuple[Estimate | None, str | None]:
        """The position's bid at ``now`` estimated from the index, or why it is not to be judged on."""
        anchor, learning = self._anchors.get(position_id), self._learning.get(position_id)
        if anchor is None or learning is None:
            return None, "no quote of Saxo's to start from"
        # The prior is checked against the price per point of distance of that quote: its bid and the index then.
        sensitivity = learning.value(anchor.bid, anchor.index, barrier) if anchor.index is not None else None
        bid = (estimated_bid(anchor.bid, anchor.index, index, sensitivity, learning.direction)
               if sensitivity is not None and index is not None else None)
        why = why_not_estimated(
            now=now, index_at=index_at, quoted_at=anchor.at, sensitivity=sensitivity, last_bid=anchor.bid,
            last_index=anchor.index, estimate=bid, index=index, barrier=barrier, direction=learning.direction,
        )
        return (None, why) if why is not None else (Estimate(bid, anchor, index), None)

    def forget_except(self, position_ids) -> None:
        """Drop what is kept of the positions that are no longer held."""
        kept = set(position_ids)
        for known in (self._anchors, self._learning):
            for position_id in [one for one in known if one not in kept]:
                del known[position_id]


def _price(value: float) -> str:
    return f"{round(value, 4):g}"


def between_quotes_reason(reason: str, epic: str, estimate: Estimate, now: float) -> str:
    """
    A price exit decided on the index: its own reason, then the index, the estimate and Saxo's last bid with its
    age, held to what the close reason's column takes.
    """
    age = max(0, round(now - estimate.anchor.at))
    said = f"{reason}; {epic} {BETWEEN_QUOTES}: {_price(estimate.bid)} from {_price(estimate.anchor.bid)}, {age} s"
    return said if len(said) <= REASON_MOST_CHARACTERS else f"{reason}; {epic} {BETWEEN_QUOTES}"[:REASON_MOST_CHARACTERS]
