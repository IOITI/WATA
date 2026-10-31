"""
A backtest run checked against funded-account challenges with LuxAlgo's
prop-firm simulator (propfirms_validate_strategy, src/admin/luxalgo.py): the
run's own trades as R-multiples, each result over the stop-loss it risked, in
the order they closed, so that the simulator's stationary bootstrap keeps
their streaks (a loss limit dies to a streak, not to an average).

Only those figures, the trades a day and the risk asked leave for LuxAlgo:
never an id, the account's value or a name. A knock-out, or a sale past the
stop, is below -1 R, and said so. Pure; the route keeps the answer in the
run's folder (``propfirms.json``). Outside src/backtest/: no run's code digest
changes for it.
"""

# Fewer trades than LuxAlgo takes; it recommends a hundred or more.
MIN_TRADES = 10
ADVISED_TRADES = 100
# The bar the answer is read against: a pass per attempt of one in two (LuxAlgo's own default).
BAR = 0.5
PRODUCT_TYPES = ("futures", "cfd")
RISK_PERCENT = (0.1, 5.0)
DEFAULT_RISK_PERCENT = 0.5
ACCOUNT_SIZES = (1_000, 1_000_000)
FILE = "propfirms.json"


class NotCheckable(ValueError):
    """What a run cannot be checked for, in words."""


def r_series(trades: list[dict], stoploss_percent) -> list[float]:
    """Each closed trade's result over the stop-loss it risked, in the order the trades closed."""
    if not isinstance(stoploss_percent, (int, float)) or isinstance(stoploss_percent, bool) or not stoploss_percent:
        raise NotCheckable("This run has no stop-loss: there is no risk to count its trades in R")
    risk = abs(float(stoploss_percent))
    closed = [trade for trade in trades if isinstance(trade.get("performance_percent"), (int, float))]
    closed.sort(key=lambda trade: ((trade.get("exit") or {}).get("filled_at") or "", trade.get("day") or ""))
    series = [round(trade["performance_percent"] / risk, 4) for trade in closed]
    if len(series) < MIN_TRADES:
        raise NotCheckable(f"LuxAlgo's simulator takes {MIN_TRADES} trades at least: this run made {len(series)}")
    return series


def figures(trades: list[dict], days: list[dict], settings: dict) -> dict:
    """What a run says of itself for the simulator: its R-series, its trades a day, and how far to trust them."""
    series = r_series(trades, settings.get("stoploss_percent"))
    tested = [day for day in days if day.get("status") == "tested"]
    per_day = round(len(series) / max(1, len(tested)), 3)
    wins = [value for value in series if value > 0]
    losses = [value for value in series if value <= 0]
    notes = []
    if len(series) < ADVISED_TRADES:
        notes.append(f"{len(series)} trades: LuxAlgo advises a hundred or more for its bootstrap")
    beyond = sum(1 for value in series if value < -1)
    if beyond:
        notes.append(f"{beyond} trade(s) lost more than the stop (a knock-out, or a sale past it): below -1 R")
    return {
        "stoploss_percent": settings.get("stoploss_percent"), "trades": len(series), "days": len(tested),
        "trades_per_day": per_day, "win_rate": round(len(wins) / len(series), 4),
        "average_win_r": round(sum(wins) / len(wins), 4) if wins else None,
        "average_loss_r": round(sum(losses) / len(losses), 4) if losses else None,
        "r_series": series, "notes": notes,
    }


def request_of(said: dict, *, risk_percent: float, product_type: str, account_min: float | None = None,
               account_max: float | None = None) -> dict:
    """The arguments of propfirms_validate_strategy: the figures, the risk asked and the challenges' scope."""
    if product_type not in PRODUCT_TYPES:
        raise NotCheckable(f"A product type is one of {', '.join(PRODUCT_TYPES)}")
    if not RISK_PERCENT[0] <= risk_percent <= RISK_PERCENT[1]:
        raise NotCheckable(f"The risk per trade is from {RISK_PERCENT[0]} % to {RISK_PERCENT[1]} % of the balance")
    for size in (account_min, account_max):
        if size is not None and not ACCOUNT_SIZES[0] <= size <= ACCOUNT_SIZES[1]:
            raise NotCheckable(f"An account size is from {ACCOUNT_SIZES[0]:,} to {ACCOUNT_SIZES[1]:,}")
    arguments = {
        "rSeries": said["r_series"], "tradesPerDay": said["trades_per_day"], "riskMode": "percent-of-balance",
        "riskValue": risk_percent, "productType": product_type, "minPassPerAttempt": BAR,
    }
    if account_min is not None:
        arguments["accountSizeMin"] = account_min
    if account_max is not None:
        arguments["accountSizeMax"] = account_max
    return arguments
