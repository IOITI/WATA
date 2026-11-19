"""
A trade autopsy: what separated a run's winning trades from its losing ones, using only what was known at entry, and
whether it held on later days.

Each trade is described by what WATA could know when the signal was raised: when it came (minutes after the US open,
weekday), which way, the day so far (its number in the day, the day's result before it, the previous trade's result
when it had closed), the turbo it bought (spread, price, distance to its barrier) and what the index did before it
(its move over 5, 15 and 60 minutes, signed by direction; its realised volatility over 30 minutes; where it stood in
the session's range so far, and from the session's open), the day's gap (after 09:30 New York only), the US open's
first half hour (after 10:00 only), and a scheduled release that day. Never what came after: a run's regime labels
of the whole day (src/backtest/regimes.py) look ahead, the asset's price at the fill is read after the decision, and a
trade's best and worst points are its outcome. The index's bars are read up to the moment the signal was raised (its
arrival less the run's signal delay), whole minutes only.

Each feature's cut is chosen on the first ``CHOOSE_SHARE`` of the tested days (among its quartiles there), then tested
on the days after: the difference of the mean result of the trades on its two sides, with a bootstrap of whole days
(trades within a day are not independent) for how often it would be no better by chance. The features are tested
together: Benjamini–Hochberg at ``FDR_Q`` keeps what holds; one whose difference changed sign is left. What holds is
said as a hypothesis to test, never applied: the research queue and an experiment are where it is tried.

The exits are read in-sample, as hints to sweep: how many losing trades had first been up, how many winners first
down. Pure: the caller reads the run's files and the index's bars.
"""

import math
import random
from datetime import datetime, timezone
from statistics import fmean, pstdev

AUTOPSY_VERSION = 1
CHOOSE_SHARE = 0.6
FDR_Q = 0.10
BOOTSTRAP = 2000
# Enough to say anything: trades and tested days in all, and on each side of a cut.
LEAST_TRADES, LEAST_DAYS = 30, 10
LEAST_SIDE_TRADES, LEAST_SIDE_DAYS = 8, 4
LEAST_TEST_SIDE = 4
NEW_YORK_OPEN_MINUTES = 0
OPEN_SETTLED_MINUTES = 30

# Each feature: its label, and whether it is a number or a category.
FEATURES = {
    "minutes_after_open": ("Minutes after the US open", "number"),
    "weekday": ("Weekday", "category"),
    "direction": ("Direction", "category"),
    "trade_of_day": ("Its number in the day", "number"),
    "day_before": ("The day's result before it (%)", "number"),
    "previous_result": ("The previous trade's result (%)", "number"),
    "spread_percent": ("The turbo's spread (% of its ask)", "number"),
    "turbo_price": ("The turbo's price (€)", "number"),
    "barrier_distance": ("Distance to its barrier (% of the index)", "number"),
    "move_5": ("The index's move over 5 min, its way (bp)", "number"),
    "move_15": ("The index's move over 15 min, its way (bp)", "number"),
    "move_60": ("The index's move over 60 min, its way (bp)", "number"),
    "volatility_30": ("The index's volatility over 30 min (bp a minute)", "number"),
    "range_place": ("Where it stood in the session's range, its way (0 to 1)", "number"),
    "from_open": ("From the session's open, its way (bp)", "number"),
    "gap": ("The day's gap, its way (%)", "number"),
    "us_open_move": ("The US open's first half hour, its way (%)", "number"),
    "release_day": ("A scheduled release that day", "category"),
}
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
LEFT_OUT = [
    "the day's regime labels (range, volatility, trend): worked out from the whole day, so after the entry",
    "the index's price at the fill: read after the decision",
    "a trade's best and worst points: its outcome",
]


def _moment(text) -> float | None:
    try:
        moment = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _sign(direction) -> int:
    return -1 if str(direction).lower() == "short" else 1


# ── what each trade was, at its entry ──

def _closes_before(bars: list[list], moment: float) -> list[tuple[float, float, float, float]]:
    """The index's minute bars that ended by ``moment``: (start, high, low, close) of the bid."""
    found = []
    for bar in bars:
        start, high, low, close = bar[0], bar[2], bar[3], bar[4]
        if start + 60 <= moment and close is not None:
            found.append((float(start), float(high), float(low), float(close)))
    return found


def index_features(bars: list[list], moment: float, direction) -> dict:
    """What the index did before the signal was raised: its moves, its volatility, its place in the session."""
    before = _closes_before(bars, moment)
    if not before:
        return {}
    sign = _sign(direction)
    last = before[-1][3]
    found = {}
    for minutes, name in ((5, "move_5"), (15, "move_15"), (60, "move_60")):
        if len(before) > minutes:
            then = before[-1 - minutes][3]
            found[name] = round(sign * (last / then - 1) * 10_000, 2) if then else None
    window = [close for _, _, _, close in before[-31:]]
    if len(window) >= 11:
        returns = [math.log(second / first) * 10_000 for first, second in zip(window, window[1:]) if first and second]
        found["volatility_30"] = round(pstdev(returns), 3) if len(returns) > 1 else None
    session_high = max(high for _, high, _, _ in before)
    session_low = min(low for _, _, low, _ in before)
    if session_high > session_low:
        place = (last - session_low) / (session_high - session_low)
        found["range_place"] = round(place if sign > 0 else 1 - place, 3)
    opened = before[0][3]
    if opened:
        found["from_open"] = round(sign * (last / opened - 1) * 10_000, 2)
    found["_index"] = last
    return found


def trade_features(trade: dict, raised: float | None, bars: list[list], day_trades: list[dict], regime: dict | None) -> dict:
    """Everything known about a trade when its signal was raised (see the module's docstring)."""
    entry = trade.get("entry") or {}
    decided = _moment(entry.get("decided_at"))
    moment = raised if raised is not None else decided
    sign = _sign(trade.get("direction"))
    found: dict = {
        "minutes_after_open": _number(trade.get("minutes_after_open")),
        "weekday": WEEKDAYS[datetime.fromisoformat(trade["day"]).weekday()] if trade.get("day") else None,
        "direction": trade.get("direction"),
        "day_before": _number(trade.get("day_percent_before")),
    }
    earlier = sorted((other for other in day_trades if (_moment((other.get("entry") or {}).get("decided_at")) or 0) < (decided or 0)),
                     key=lambda other: _moment((other.get("entry") or {}).get("decided_at")) or 0)
    found["trade_of_day"] = float(len(earlier) + 1)
    closed = [other for other in earlier if (_moment((other.get("exit") or {}).get("filled_at")) or math.inf) <= (decided or 0)]
    found["previous_result"] = _number(closed[-1].get("performance_percent")) if closed else None
    ask, bid = _number(entry.get("ask")), _number(entry.get("bid"))
    found["spread_percent"] = round((ask - bid) / ask * 100, 3) if ask and bid is not None and ask > 0 else None
    found["turbo_price"] = ask
    if moment is not None and bars:
        index = index_features(bars, moment, trade.get("direction"))
        last = index.pop("_index", None)
        found.update(index)
        barrier = _number((trade.get("turbo") or {}).get("barrier"))
        if barrier and last:
            found["barrier_distance"] = round(abs(last - barrier) / last * 100, 3)
    minutes = found["minutes_after_open"]
    regime = regime or {}
    if minutes is not None and minutes >= NEW_YORK_OPEN_MINUTES and _number(regime.get("gap_percent")) is not None:
        found["gap"] = round(sign * regime["gap_percent"], 3)
    if minutes is not None and minutes >= OPEN_SETTLED_MINUTES and _number(regime.get("us_open_move_percent")) is not None:
        found["us_open_move"] = round(sign * regime["us_open_move_percent"], 3)
    news = regime.get("news")
    found["release_day"] = "yes" if news not in (None, "", "none", [], "no") else "no"
    return found


# ── does a cut hold on later days? ──

def _quartiles(values: list[float]) -> list[float]:
    ordered = sorted(values)
    found = []
    for share in (0.25, 0.5, 0.75):
        cut = ordered[min(len(ordered) - 1, max(0, round(share * (len(ordered) - 1))))]
        if cut not in found:
            found.append(cut)
    return found


def _split(rows: list[dict], name: str, kind: str, cut) -> tuple[list[dict], list[dict]]:
    """The trades on the cut's side (``in``) and the others, among those whose feature is known."""
    known = [row for row in rows if row["features"].get(name) is not None]
    if kind == "number":
        inside = [row for row in known if row["features"][name] > cut]
    else:
        inside = [row for row in known if row["features"][name] == cut]
    chosen = {id(row) for row in inside}
    return inside, [row for row in known if id(row) not in chosen]


def _mean(rows: list[dict]) -> float | None:
    return fmean(row["result"] for row in rows) if rows else None


def _days(rows: list[dict]) -> int:
    return len({row["day"] for row in rows})


def _choose(rows: list[dict], name: str, kind: str):
    """The cut that separates the results most on the days to choose on, both sides large enough; None when none is."""
    known = [row["features"][name] for row in rows if row["features"].get(name) is not None]
    if not known:
        return None
    cuts = _quartiles(known) if kind == "number" else sorted(set(known))
    best = None
    for cut in cuts:
        inside, outside = _split(rows, name, kind, cut)
        if (len(inside) < LEAST_SIDE_TRADES or len(outside) < LEAST_SIDE_TRADES
                or _days(inside) < LEAST_SIDE_DAYS or _days(outside) < LEAST_SIDE_DAYS):
            continue
        difference = _mean(inside) - _mean(outside)
        if best is None or abs(difference) > abs(best[1]):
            best = (cut, difference)
    return best


def _bootstrap_p(inside: list[dict], outside: list[dict], sign: int, draw: random.Random) -> float:
    """How often the difference is no better than zero in the chosen way, whole days drawn again."""
    days = sorted({row["day"] for row in inside} | {row["day"] for row in outside})
    of_day = {day: ([row["result"] for row in inside if row["day"] == day],
                    [row["result"] for row in outside if row["day"] == day]) for day in days}
    worse = 0
    for _ in range(BOOTSTRAP):
        chosen_in, chosen_out = [], []
        for _day in range(len(days)):
            day_in, day_out = of_day[draw.choice(days)]
            chosen_in += day_in
            chosen_out += day_out
        if not chosen_in or not chosen_out or sign * (fmean(chosen_in) - fmean(chosen_out)) <= 0:
            worse += 1
    return (worse + 1) / (BOOTSTRAP + 1)


def _fdr(found: list[dict]) -> None:
    """Benjamini–Hochberg q-values over the features tested on later days."""
    tested = sorted((entry for entry in found if entry.get("p") is not None), key=lambda entry: entry["p"])
    count = len(tested)
    running = 1.0
    for rank in range(count, 0, -1):
        entry = tested[rank - 1]
        running = min(running, entry["p"] * count / rank)
        entry["q"] = round(running, 4)


def _cut_said(name: str, kind: str, cut, better: bool) -> str:
    label = FEATURES[name][0]
    if kind == "number":
        return f"{label} above {cut:g}" if better else f"{label} at {cut:g} or under"
    return f"{label}: {cut}" if better else f"{label}: not {cut}"


def _counted(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def autopsy(trades: list[dict], signals: list[dict], days: list[dict], bars_of, *, signal_delay: float, seed: int) -> dict:
    """
    What separated the run's winners from its losers, known at entry, chosen on its first days and tested on the
    later ones (see the module's docstring). ``bars_of(day)`` gives the index's minute bars of a day (BAR_COLUMNS);
    ``signal_delay`` is the run's, in seconds.
    """
    tested = sorted(day["day"] for day in days if day.get("status") == "tested")
    regimes = {day["day"]: day.get("regime") or {} for day in days}
    arrived = {signal.get("id"): _moment(signal.get("arrived_at")) for signal in signals}
    by_day: dict[str, list[dict]] = {}
    for trade in trades:
        if trade.get("day") in tested and trade.get("performance_percent") is not None:
            by_day.setdefault(trade["day"], []).append(trade)
    rows = []
    for day in tested:
        bars = bars_of(day) if by_day.get(day) else []
        for trade in by_day.get(day, []):
            moment = arrived.get(trade.get("signal_id"))
            raised = moment - signal_delay if moment is not None else None
            rows.append({"day": day, "result": float(trade["performance_percent"]),
                         "features": trade_features(trade, raised, bars, by_day[day], regimes.get(day))})
    answer = {"version": AUTOPSY_VERSION, "trades": len(rows), "days": len(tested), "choose_share": CHOOSE_SHARE,
              "fdr_q": FDR_Q, "left_out": LEFT_OUT, "features": [], "held": [], "too_few": None,
              "exits": exit_hints(trades)}
    if len(rows) < LEAST_TRADES or len(tested) < LEAST_DAYS:
        answer["too_few"] = (f"{_counted(len(rows), 'trade')} over {_counted(len(tested), 'day')}: {LEAST_TRADES} trades "
                             f"over {LEAST_DAYS} days at least are needed to choose a cut on some days and test it on "
                             "others")
        return answer
    split_at = max(1, min(len(tested) - 1, round(len(tested) * CHOOSE_SHARE)))
    choose_days, test_days = set(tested[:split_at]), set(tested[split_at:])
    answer["choose_days"] = [tested[0], tested[split_at - 1]]
    answer["test_days"] = [tested[split_at], tested[-1]]
    choose = [row for row in rows if row["day"] in choose_days]
    test = [row for row in rows if row["day"] in test_days]
    draw = random.Random(seed)
    for name, (label, kind) in FEATURES.items():
        known = sum(1 for row in rows if row["features"].get(name) is not None)
        entry = {"name": name, "label": label, "kind": kind, "known": known, "cut": None, "p": None, "q": None,
                 "held": False}
        chosen = _choose(choose, name, kind)
        if chosen is None:
            entry["why"] = "no cut leaves enough trades on both sides on the days to choose on"
            answer["features"].append(entry)
            continue
        cut, difference = chosen
        sign = 1 if difference > 0 else -1
        inside, outside = _split(test, name, kind, cut)
        entry.update(cut=cut, chosen={"in": len(_split(choose, name, kind, cut)[0]), "difference": round(difference, 3)})
        if len(inside) < LEAST_TEST_SIDE or len(outside) < LEAST_TEST_SIDE:
            entry["why"] = "too few trades on one side on the later days to test it"
            answer["features"].append(entry)
            continue
        later = _mean(inside) - _mean(outside)
        entry["tested"] = {"in": len(inside), "out": len(outside), "mean_in": round(_mean(inside), 3),
                           "mean_out": round(_mean(outside), 3), "difference": round(later, 3)}
        entry["p"] = round(_bootstrap_p(inside, outside, sign, draw), 4)
        entry["same_sign"] = (later > 0) == (difference > 0)
        entry["said"] = _cut_said(name, kind, cut, sign > 0)
        answer["features"].append(entry)
    _fdr(answer["features"])
    for entry in answer["features"]:
        entry["held"] = bool(entry.get("q") is not None and entry["q"] <= FDR_Q and entry.get("same_sign"))
        if entry["held"]:
            better = entry["tested"]["difference"] if entry["chosen"]["difference"] > 0 else -entry["tested"]["difference"]
            answer["held"].append({
                "name": entry["name"], "said": entry["said"], "q": entry["q"],
                "sentence": (f"{entry['said']}: {better:+.2f} points a trade on the later days "
                             f"({entry['tested']['in']} trades against {entry['tested']['out']}, q {entry['q']:.2f}). "
                             "A hypothesis to test in an experiment, with the days it came from named."),
            })
    return answer


# ── the exits, in-sample ──

def exit_hints(trades: list[dict]) -> dict:
    """How many losers had first been up, how many winners first down: hints for what to sweep, read in-sample."""
    losers = [trade for trade in trades if (trade.get("performance_percent") or 0) < 0]
    winners = [trade for trade in trades if (trade.get("performance_percent") or 0) > 0]

    def share(rows, test):
        return round(sum(1 for row in rows if test(row)) / len(rows) * 100, 1) if rows else None

    return {
        "losers": len(losers),
        "losers_up_first": {f"{level:g}": share(losers, lambda row, level=level: (row.get("max_performance_percent") or 0) >= level)
                            for level in (1.0, 2.0, 3.0)},
        "winners": len(winners),
        "winners_down_first": {f"{level:g}": share(winners, lambda row, level=level: (row.get("min_performance_percent") or 0) <= -level)
                               for level in (3.0, 5.0)},
        "in_sample": True,
    }
