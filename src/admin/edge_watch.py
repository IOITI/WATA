"""
Edge Watch: are a strategy's trades since its reference backtest what that backtest led to expect, or what random
entries make of the same days? Broken, or just unlucky. Pure: no file, no database, no clock.

The unit is a trade's result on its turbo, in percent, as WATA counts it (``performance_percent`` of a run's trades,
``position_total_performance_percent`` of a real position): a day and the day target compound these same numbers, so
nothing here depends on the account's size.

``build_model`` reads a reference run: its tested days, each a block of its trades' results in the order they closed
(a day without a trade is an empty block, which keeps the strategy's rate of trades), and its random entries' totals.
It draws whole days again, never single trades (the day target and the day's breakers shape a day's trades), into
paths of trades as long as the horizon, and keeps:

- the cone: where the compounded result, the deepest fall from a peak and the longest losing streak stood on those
  paths after n trades;
- a sequential test (Page's CUSUM) between H0, the reference's own trades, and H1, what random entries made of the
  same days (their mean per trade from the run's random-entry totals, which already pay the spread and the costs).
  Its threshold is the 95th percentile of the highest the statistic reached on each path under H0: on trades that
  are the reference's, it says "broken" within the horizon at most 5 % of the time. Paths shifted to H1's mean say
  how many trades it takes to tell (``trades_to_tell``).

``judge`` sets a list of results (a track: the real trades since the reference, or a rule's paper trades) against
the model. Only the test may say ``broken`` (and so pause entries, ``should_pause``): the cone's bands are judged
trade after trade, which they were not calibrated for, and only give ``watch``.

Lives in src/admin and not in src/backtest, whose every file is in a run's code digest (figures.py ``_CODE``).
"""

import hashlib
import math
import random
from statistics import fmean, median, pstdev

from src.backtest.figures import longest_losing_streak, quantile, sample_size, TOO_FEW
from src.backtest.rules import compounded
from src.backtest.settings import FIELDS

# A change of what a model holds or how it is drawn: the models cached before are made again.
VERSION = 1

PATHS = 2000
H1_PATHS = 500
# Paths shifted to random entries' mean are followed this many horizons at most to find when the test fires.
DETECT_HORIZONS = 4
ALPHA = 0.05
# The test's horizon: the trades of this many trading days at the reference's rate, held between the two bounds.
HORIZON_DAYS = 120
LEAST_HORIZON, MOST_HORIZON = 60, 400

# Under this, a reference says too little to draw from.
LEAST_DAYS, LEAST_TRADES = 10, 15
# Fewer trades than this are not judged.
JUDGED_FROM = 10
# The edge guard pauses entries only on this many real trades or more, and a reference that is not too few.
GUARD_LEAST_TRADES = 20
# A rule is ready on paper with this many trades since its reference, at least.
READY_TRADES = 20
# A result whose share of the reference's paths as bad or worse is under this is unusual.
UNUSUAL = 5
BETTER = 95

# The levels of the cone kept at each point of the grid, in percent of the paths.
LEVELS = (1, 2.5, 5, 10, 25, 50, 75, 90, 95, 97.5, 99)
BANDS = ((5, 95), (25, 75))

TOO_EARLY, NO_MODEL, NO_EDGE, BROKEN, WATCH, BETTER_THAN_EXPECTED, AS_EXPECTED = (
    "too_early", "no_model", "no_edge_over_random", "broken", "watch", "better_than_expected", "as_expected",
)
VERDICTS = (TOO_EARLY, NO_MODEL, NO_EDGE, BROKEN, WATCH, BETTER_THAN_EXPECTED, AS_EXPECTED)

# What H1 rests on.
RANDOM, ZERO = "random", "zero"

# The settings that shape a trade's result: a reference made under others does not describe the live trades.
SHAPING_GROUPS = ("entries", "day", "exits", "turbo")
NOT_SHAPING = {"pause_below_balance_eur", "pause_after_drawdown_percent"}
SHAPING_FIELDS = tuple(
    entry.name for entry in FIELDS if entry.group in SHAPING_GROUPS and entry.name not in NOT_SHAPING
)


def _rounded(value: float | None, digits: int = 2) -> float | None:
    return None if value is None or not math.isfinite(value) else round(value, digits)


# ── the reference ──


def day_blocks(days: list[dict], trades: list[dict]) -> list[list[float]]:
    """
    The reference's tested days in their order, each the results of the trades opened that day in the order they
    closed; a tested day without a trade is an empty block. Days a run could not test (closed, no turbos, no bars)
    are left out, and so are trades without a result or an exit.
    """
    tested = [day["day"] for day in days if day.get("status") == "tested"]
    of_day: dict[str, list[tuple[str, float]]] = {day: [] for day in tested}
    for trade in trades:
        result = trade.get("performance_percent")
        closed = (trade.get("exit") or {}).get("filled_at")
        if trade.get("day") in of_day and result is not None and closed:
            of_day[trade["day"]].append((str(closed), float(result)))
    return [[result for _, result in sorted(of_day[day])] for day in tested]


def geometric_mean(results: list[float]) -> float:
    """The result per trade that, compounded, gives the same total, in percent."""
    if not results:
        return 0.0
    total = 1.0
    for result in results:
        total *= max(0.0, 1 + result / 100)
    return (total ** (1 / len(results)) - 1) * 100


def random_mean(baseline: dict | None, spread: float) -> float | None:
    """
    What random entries made per trade on the same days, in percent, from the run's random-entry totals
    (``baseline.json``: each repetition's compounded total and its trades): the median of each repetition's
    geometric mean per trade, with the drag of compounding (``spread`` squared over 200, the reference's spread)
    added back, so that it compares with an arithmetic mean. None without a repetition that traded.
    """
    if not baseline:
        return None
    means = []
    for total, trades in zip(baseline.get("totals") or [], baseline.get("trades") or []):
        if total is None or not trades:
            continue
        means.append((max(0.0, 1 + total / 100) ** (1 / trades) - 1) * 100)
    if not means:
        return None
    return median(means) + spread ** 2 / 200


def seed_of(run_id: str) -> int:
    """A model's draws, the same for the same reference wherever it is made."""
    return int.from_bytes(hashlib.sha256(f"edge-watch:{run_id}".encode()).digest()[:8], "big")


def grid(horizon: int) -> list[int]:
    """The numbers of trades the cone is kept at: each to 50, every 5 to 200, every 10 beyond, and the horizon."""
    points = list(range(1, min(50, horizon) + 1))
    points += list(range(55, min(200, horizon) + 1, 5))
    points += list(range(210, horizon + 1, 10))
    if points[-1] != horizon:
        points.append(horizon)
    return points


def _path(blocks: list[list[float]], length: int, draw: random.Random) -> list[float]:
    path: list[float] = []
    while len(path) < length:
        path.extend(draw.choice(blocks))
    return path[:length]


def _levels(values: list[float]) -> list[float]:
    return [round(quantile(values, level / 100), 4) for level in LEVELS]


def build_model(blocks: list[list[float]], baseline: dict | None, *, run_id: str, paths: int = PATHS,
                h1_paths: int = H1_PATHS, alpha: float = ALPHA) -> dict:
    """The expectation a reference run gives (see the module's docstring); ``usable`` False says why not."""
    results = [result for block in blocks for result in block]
    days, count = len(blocks), len(results)
    spread = pstdev(results) if count > 1 else 0.0
    mean = fmean(results) if results else 0.0
    reference = {
        "days": days,
        "trades": count,
        "per_day": _rounded(count / days if days else 0.0, 3),
        "mean": _rounded(mean, 3),
        "sd": _rounded(spread, 3),
        "t": _rounded(mean / (spread / math.sqrt(count)), 2) if count > 1 and spread > 0 else None,
        "sample": sample_size(count, days),
    }
    model = {"version": VERSION, "run_id": run_id, "seed": seed_of(run_id), "usable": False, "why_not": None,
             "reference": reference, "test": None, "cone": None}
    if days < LEAST_DAYS or count < LEAST_TRADES:
        model["why_not"] = (
            f"its reference has {count} trade(s) over {days} tested day(s): "
            f"{LEAST_TRADES} trades over {LEAST_DAYS} days at least are needed to draw from"
        )
        return model
    low, high = quantile(results, 0.01), quantile(results, 0.99)
    clipped = [min(high, max(low, result)) for result in results]
    mu0, sigma = fmean(clipped), pstdev(clipped)
    if sigma <= 0:
        model["why_not"] = "every trade of its reference gave the same result: there is no spread to draw from"
        return model
    randomly = random_mean(baseline, sigma)
    mu1, h1 = (randomly, RANDOM) if randomly is not None else (0.0, ZERO)
    reference.update({
        "clip": [_rounded(low, 3), _rounded(high, 3)],
        "mean_clipped": _rounded(mu0, 3), "sd_clipped": _rounded(sigma, 3),
        "random_mean": _rounded(randomly, 3), "h1": h1, "h1_mean": _rounded(mu1, 3),
        "edge_over_random": _rounded(mu0 - mu1, 3),
    })
    model["usable"] = True
    horizon = max(LEAST_HORIZON, min(MOST_HORIZON, round(count / days * HORIZON_DAYS)))
    points = grid(horizon)
    at = {point: place for place, point in enumerate(points)}
    edge = mu0 > mu1
    slope = (mu1 - mu0) / sigma ** 2
    middle = (mu0 + mu1) / 2
    clip_of = {}

    def clip(result: float) -> float:
        value = clip_of.get(result)
        if value is None:
            value = clip_of[result] = min(high, max(low, result))
        return value

    draw = random.Random(model["seed"])
    cumulative: list[list[float]] = [[] for _ in points]
    deepest: list[list[float]] = [[] for _ in points]
    streaks: list[list[int]] = [[] for _ in points]
    highest: list[float] = []
    for _ in range(paths):
        level = peak = 1.0
        fall, current, longest, statistic, top = 0.0, 0, 0, 0.0, 0.0
        for number, result in enumerate(_path(blocks, horizon, draw), start=1):
            level *= max(0.0, 1 + result / 100)
            peak = max(peak, level)
            if peak > 0:
                fall = min(fall, (level / peak - 1) * 100)
            current = current + 1 if result <= 0 else 0
            longest = max(longest, current)
            if edge:
                statistic = max(0.0, statistic + slope * (clip(result) - middle))
                top = max(top, statistic)
            place = at.get(number)
            if place is not None:
                cumulative[place].append((level - 1) * 100)
                deepest[place].append(fall)
                streaks[place].append(longest)
        highest.append(top)
    model["horizon"] = horizon
    model["cone"] = {
        "levels": list(LEVELS),
        "n": points,
        "cumulative": [_levels(values) for values in cumulative],
        "drawdown": [_levels(values) for values in deepest],
        # The share of the paths, in percent, whose longest losing streak reached 1, 2, 3... trades by then.
        "streak_at_least": [_tail(values) for values in streaks],
    }
    if not edge:
        return model
    threshold = quantile(highest, 1 - alpha)
    if threshold is None or threshold <= 0:
        threshold = 1e-9
    longest_watch = horizon * DETECT_HORIZONS
    first_alarms: list[int | None] = []
    shift = mu1 - mu0
    for _ in range(h1_paths):
        statistic, fired = 0.0, None
        for number, result in enumerate(_path(blocks, longest_watch, draw), start=1):
            statistic = max(0.0, statistic + slope * (clip(result) + shift - middle))
            if statistic >= threshold:
                fired = number
                break
        first_alarms.append(fired)
    caught = sorted(number for number in first_alarms if number is not None)
    told = _nth(caught, len(first_alarms), 0.8)
    model["test"] = {
        "alpha": alpha,
        "h": round(threshold, 4),
        "slope": slope,
        "middle": middle,
        "trades_to_tell": told,
        "detect": {
            "median": _nth(caught, len(first_alarms), 0.5),
            "p80": told,
            "within_horizon": _rounded(sum(1 for number in caught if number <= horizon) / len(first_alarms), 3),
            "followed": longest_watch,
        },
    }
    return model


def _tail(streaks: list[int]) -> list[float]:
    """The share of ``streaks``, in percent, at least 1, 2, 3... long."""
    most = max(streaks) if streaks else 0
    counts = [0] * (most + 2)
    for streak in streaks:
        counts[streak] += 1
    shares, above = [], 0
    for length in range(most, 0, -1):
        above += counts[length]
        shares.append(round(above / len(streaks) * 100, 2))
    return list(reversed(shares))


def _nth(caught: list[int], total: int, share: float) -> int | None:
    """The number of trades by which ``share`` of the shifted paths had fired, or None if fewer than that ever did."""
    needed = math.ceil(share * total)
    return caught[needed - 1] if needed and len(caught) >= needed else None


# ── judging a track ──


def _between(model: dict, metric: str, number: int) -> list[float] | None:
    """The cone's levels of ``metric`` after ``number`` trades, between the two points of the grid around it."""
    points = model["cone"]["n"]
    if number < points[0] or number > points[-1]:
        return None
    values = model["cone"][metric]
    for place, point in enumerate(points):
        if point == number:
            return values[place]
        if point > number:
            before, after = points[place - 1], point
            weight = (number - before) / (after - before)
            return [low + (high - low) * weight for low, high in zip(values[place - 1], values[place])]
    return None


def share_at_or_below(value: float, levels: list[float]) -> tuple[float, str | None]:
    """
    The share of the paths, in percent, at or below ``value``, from the cone's levels; past its outer levels, the
    outer level and which way it is a bound (``at_most``: as rare or rarer).
    """
    if value < levels[0]:
        return LEVELS[0], "at_most"
    if value >= levels[-1]:
        return LEVELS[-1], "at_least"
    place = max(index for index, level in enumerate(levels) if level <= value)
    low, high = levels[place], levels[place + 1]
    weight = (value - low) / (high - low) if high > low else 0.0
    return LEVELS[place] + (LEVELS[place + 1] - LEVELS[place]) * weight, None


def _streak_share(model: dict, number: int, streak: int) -> float | None:
    """The share of the paths, in percent, whose longest losing streak was ``streak`` or longer by ``number`` trades."""
    if streak <= 0:
        return 100.0
    points = model["cone"]["n"]
    if number < points[0] or number > points[-1]:
        return None
    place = next(index for index, point in enumerate(points) if point >= number)
    shares = model["cone"]["streak_at_least"][place]
    return shares[streak - 1] if streak <= len(shares) else 0.0


def _fall(results: list[float]) -> float:
    level = peak = 1.0
    deepest = 0.0
    for result in results:
        level *= max(0.0, 1 + result / 100)
        peak = max(peak, level)
        if peak > 0:
            deepest = min(deepest, (level / peak - 1) * 100)
    return deepest


def statistic(model: dict, results: list[float]) -> tuple[float, float, int | None]:
    """The test's statistic after ``results``, the highest it reached and at which trade it first reached the threshold."""
    test = model.get("test")
    if not test:
        return 0.0, 0.0, None
    low, high = model["reference"]["clip"]
    value = top = 0.0
    fired = None
    for number, result in enumerate(results, start=1):
        value = max(0.0, value + test["slope"] * (min(high, max(low, result)) - test["middle"]))
        top = max(top, value)
        if fired is None and value >= test["h"]:
            fired = number
    return value, top, fired


def judge(model: dict | None, results: list[float], *, tested: list[float] | None = None,
          settings_differ: list[str] | None = None) -> dict:
    """
    The verdict on ``results`` (a track's trades since its start, in the order they closed) against ``model``;
    ``tested``: the results the test reads, when it starts later than the cone (the live test restarts when the
    operator resumes entries). What is said in words is in ``reasons``.
    """
    tested = results if tested is None else tested
    count = len(results)
    cumulative = compounded(results) if results else 0.0
    fall = round(_fall(results), 2)
    streak = longest_losing_streak(results)
    judgement = {
        "verdict": NO_MODEL, "reasons": [], "n": count, "cumulative": cumulative, "rank": None, "rank_bound": None,
        "drawdown": fall, "drawdown_rank": None, "drawdown_bound": None, "streak": streak, "streak_rank": None,
        "cusum": None, "cusum_top": None, "h": None, "alarm_at": None, "tested": len(tested),
        "trades_to_tell": None, "progress": None, "past_horizon": False,
        "reference": (model or {}).get("reference"), "run_id": (model or {}).get("run_id"),
        "settings_differ": list(settings_differ or []),
    }
    if not model:
        judgement["reasons"].append("no reference run to judge it against")
        return judgement
    if not model.get("usable"):
        judgement["reasons"].append(model.get("why_not") or "its reference cannot be drawn from")
        return judgement
    horizon = model["horizon"]
    judgement["past_horizon"] = count > horizon
    if count and count <= horizon:
        levels = _between(model, "cumulative", count)
        judgement["rank"], judgement["rank_bound"] = _share(levels, cumulative)
        falls = _between(model, "drawdown", count)
        judgement["drawdown_rank"], judgement["drawdown_bound"] = _share(falls, fall)
        judgement["streak_rank"] = _streak_share(model, count, streak)
    test = model.get("test")
    if test:
        value, top, fired = statistic(model, tested)
        told = test["trades_to_tell"]
        judgement.update({
            "cusum": round(value, 3), "cusum_top": round(top, 3), "h": test["h"], "alarm_at": fired,
            "trades_to_tell": told, "progress": _rounded(len(tested) / told, 2) if told else None,
        })
    reasons = judgement["reasons"]
    if not test:
        judgement["verdict"] = NO_EDGE
        reasons.append(
            f"its reference makes {model['reference']['mean_clipped']:+.2f} % a trade, no more than random entries "
            f"({model['reference']['h1_mean']:+.2f} %): there is no edge to watch"
        )
        return judgement
    if count < JUDGED_FROM:
        judgement["verdict"] = TOO_EARLY
        reasons.append(f"{count} trade(s) judged of the {JUDGED_FROM} it takes to say anything")
        return judgement
    unusual = []
    if judgement["rank"] is not None and judgement["rank"] < UNUSUAL:
        unusual.append(f"its result ({cumulative:+.2f} %) is under {UNUSUAL} % of its reference's paths")
    if judgement["drawdown_rank"] is not None and judgement["drawdown_rank"] < UNUSUAL:
        unusual.append(f"fewer than {UNUSUAL} % of its reference's paths fell as deep ({fall:.2f} %)")
    if judgement["streak_rank"] is not None and judgement["streak_rank"] < UNUSUAL:
        unusual.append(f"fewer than {UNUSUAL} % of its reference's paths lost {streak} trades in a row")
    if judgement["alarm_at"] is not None:
        judgement["verdict"] = BROKEN
        reasons.append(
            f"its trades look like random entries' rather than its reference's: the test passed its threshold at "
            f"trade {judgement['alarm_at']}, which trades like the reference's do at most {round(test['alpha'] * 100)} % "
            f"of the time"
        )
        reasons.extend(unusual)
    elif judgement["cusum"] >= test["h"] / 2 or unusual:
        judgement["verdict"] = WATCH
        if judgement["cusum"] >= test["h"] / 2:
            reasons.append(f"the test is past half of its threshold ({judgement['cusum']:.2f} of {test['h']:.2f})")
        reasons.extend(unusual)
    elif judgement["rank"] is not None and judgement["rank"] > BETTER:
        judgement["verdict"] = BETTER_THAN_EXPECTED
        reasons.append(
            f"its result ({cumulative:+.2f} %) is above {BETTER} % of its reference's paths: luckier than the "
            f"backtest, which is no reason to trust it more"
        )
    else:
        judgement["verdict"] = AS_EXPECTED
        reasons.append("its trades are what its reference led to expect")
    if judgement["past_horizon"]:
        reasons.append(f"past the {horizon} trades its reference was drawn for: a newer reference would judge it better")
    return judgement


def _share(levels: list[float] | None, value: float) -> tuple[float | None, str | None]:
    if levels is None:
        return None, None
    share, bound = share_at_or_below(value, levels)
    return round(share, 1), bound


def summary(judgement: dict) -> dict:
    """A judgement without its reference's details, for lists."""
    return {key: judgement.get(key) for key in (
        "verdict", "reasons", "n", "cumulative", "rank", "rank_bound", "drawdown", "drawdown_rank", "streak",
        "streak_rank", "cusum", "h", "alarm_at", "trades_to_tell", "progress", "run_id", "settings_differ",
    )} | {"sample": (judgement.get("reference") or {}).get("sample")}


# ── what follows from a verdict ──


def guard_refusal(judgement: dict) -> str | None:
    """Why the edge guard does not pause entries on this judgement of the real trades; None when it does."""
    if judgement.get("verdict") != BROKEN:
        return "its verdict is not broken"
    if judgement.get("tested", 0) < GUARD_LEAST_TRADES:
        return f"{judgement.get('tested', 0)} real trade(s) since the test started, of the {GUARD_LEAST_TRADES} it waits for"
    if (judgement.get("reference") or {}).get("sample") == TOO_FEW:
        return "its reference rests on too few trades or days to pause entries on"
    if judgement.get("settings_differ"):
        return f"the live settings are not its reference's ({', '.join(judgement['settings_differ'][:4])})"
    return None


def should_pause(judgement: dict) -> bool:
    return guard_refusal(judgement) is None


def readiness_unmet(judgement: dict | None) -> list[str]:
    """What a rule's Edge Watch on paper holds back from going live, in words; empty once its paper trades agree."""
    if judgement is None:
        return ["no reference run to judge its paper trades against (set one from a run of the rule)"]
    verdict = judgement["verdict"]
    if verdict == NO_MODEL:
        return [f"its reference cannot be watched: {judgement['reasons'][0]}"]
    if verdict == NO_EDGE:
        return ["its reference does no better than random entries"]
    unmet = []
    if judgement["n"] < READY_TRADES:
        unmet.append(f"{judgement['n']} paper trade(s) since its reference of {READY_TRADES}")
    if verdict == BROKEN:
        unmet.append("its paper trades look like random entries', not like its reference's")
    elif judgement.get("rank") is not None and judgement["rank"] < UNUSUAL:
        unmet.append(f"its paper result is under {UNUSUAL} % of its reference's paths")
    return unmet


def settings_differ(reference: dict | None, live: dict | None) -> list[str]:
    """The settings that shape a trade's result and differ between a reference run's and the live ones."""
    if not reference or not live:
        return []
    return [name for name in SHAPING_FIELDS if name in reference and name in live and reference[name] != live[name]]


LABELS = {
    TOO_EARLY: "Too early to tell", NO_MODEL: "Cannot be watched", NO_EDGE: "No edge over random entries",
    BROKEN: "Broken", WATCH: "Worth watching", BETTER_THAN_EXPECTED: "Better than expected",
    AS_EXPECTED: "As expected",
}


def said(name: str, judgement: dict) -> str:
    """What Telegram is told of a new verdict on ``name`` (the live trades, or a rule on paper)."""
    reasons = "; ".join(judgement["reasons"][:2])
    return (
        f"📐 WATA Edge Watch, {name}: {LABELS.get(judgement['verdict'], judgement['verdict'])} after "
        f"{judgement['n']} trade(s), {judgement['cumulative']:+.2f} %. {reasons[:1].upper()}{reasons[1:]}."
    )
