"""
The challenger board: each forward check's candidate set beside the live configuration on the same later days.

A forward check says whether a kept candidate does on later days what its own backtest led to expect
(src/admin/forward_checks.py); this says whether it does better than what WATA trades now, on the same days. The
champion is the live configuration's paper days (the signals WATA received, traded each evening under the live
settings: ``BacktestService.paper()["live"]``); each check keeps the champion's day of each of its days when it is
settled, since paper days are pruned after sixty days.

Paired by day: on the days both traded, the difference of their day results. Said: how many days the challenger did
better, an exact two-sided sign test of that count (ties left out), a 90 % interval of the mean difference with the
days drawn again (seeded), and both compounded over those days. Few days say little: from ``LEAST_DAYS`` a board says
anything, and never more than "ahead" or "behind" with what it rests on. Pure.
"""

import math
import random
from statistics import fmean

from src.backtest.rules import compounded

LEAST_DAYS = 5
DRAWS = 2000
AHEAD, BEHIND, EVEN, TOO_FEW = "ahead", "behind", "even", "too_few"


def sign_test(wins: int, losses: int) -> float | None:
    """The two-sided probability of a split this uneven or more between ``wins`` and ``losses``, each as likely."""
    count = wins + losses
    if count == 0:
        return None
    tail = sum(math.comb(count, k) for k in range(0, min(wins, losses) + 1)) / 2 ** count
    return round(min(1.0, 2 * tail), 4)


def paired(challenger_days, champion_days) -> list[tuple[str, float, float]]:
    """The days both traded, in order: (day, the challenger's result, the champion's), each in percent."""
    champion = {str(day): float(percent) for day, percent, *_ in champion_days if percent is not None}
    return [(str(day), float(percent), champion[str(day)]) for day, percent, *_ in challenger_days
            if percent is not None and str(day) in champion]


def board_line(challenger_days, champion_days, *, seed: int) -> dict:
    """One challenger against the champion on their common days."""
    pairs = paired(challenger_days, champion_days)
    differences = [mine - theirs for _, mine, theirs in pairs]
    wins = sum(1 for value in differences if value > 0)
    losses = sum(1 for value in differences if value < 0)
    line = {
        "days": len(pairs), "first": pairs[0][0] if pairs else None, "last": pairs[-1][0] if pairs else None,
        "better_days": wins, "worse_days": losses,
        "challenger_percent": compounded([mine for _, mine, _ in pairs]),
        "champion_percent": compounded([theirs for _, _, theirs in pairs]),
        "mean_difference": round(fmean(differences), 3) if differences else None,
        "interval": None, "sign_p": sign_test(wins, losses), "verdict": TOO_FEW,
    }
    if len(pairs) < LEAST_DAYS:
        return line
    draw = random.Random(seed)
    means = sorted(fmean(draw.choice(differences) for _ in differences) for _ in range(DRAWS))
    low, high = means[int(0.05 * DRAWS)], means[int(0.95 * DRAWS) - 1]
    line["interval"] = [round(low, 3), round(high, 3)]
    line["verdict"] = AHEAD if low > 0 else BEHIND if high < 0 else EVEN
    return line


def seed_of(check_id) -> int:
    """A check's draws, the same each time it is read."""
    return int.from_bytes(str(check_id).encode()[:8].ljust(8, b"0"), "big")


def board(checks: list[dict], champion_days=None) -> list[dict]:
    """
    Every forward check with days judged, against the champion, in the order given. ``checks``: each with its id,
    title, family, state, ``days`` ([day, result %, trades]) and ``champion`` (the live paper days of its days kept when
    it was settled, [day, result %]); ``champion_days``: the live paper days for a check that kept none.
    """
    found = []
    for check in checks:
        if not check.get("days"):
            continue
        seed = seed_of(check.get("id"))
        found.append({"check": check.get("id"), "experiment": check.get("experiment"), "title": check.get("title"),
                      "family": check.get("family"), "state": check.get("state"),
                      **board_line(check["days"], check.get("champion") or champion_days or [], seed=seed)})
    return found
