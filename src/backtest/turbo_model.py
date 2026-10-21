"""
Turbos made up from an index, for the days the price recorder kept none: each
day a ladder of knock-out levels around the index's open, each turbo priced
as the recorded ones were for the same distance to their level, and knocked
out when the index crosses it. Months and years of an index are downloaded;
a week of turbos was recorded: this is what lets a strategy be tried on the
first with what was measured on the second.

What a modelled turbo is, measured on the recorded ones against the same
index (``calibrate``), by direction:

- ``factor``: euros of turbo per point of index (its parity and the euro's
  rate against the dollar, which the model holds at what they were);
- ``offset``: points between the level where its price would be nothing and
  its knock-out level, the issuer's margin;
- ``spacing``: points between two levels the trader could choose from;

and for all: the spread by hour, the hours turbos are quoted in, the
decimals of their prices. Until it is checked on the recorded days (``python
-m lab model validate``), a modelled run is a guess, and says so.

    python -m src.backtest.turbo_model calibrate --from 2026-10-13 --to 2026-10-19 --out calibration.json
"""

import argparse
import asyncio
import json
import math
import os
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from statistics import median

from src.backtest.clock import BAR_SECONDS, TradingClock, days_between
from src.backtest.data import LONG, SHORT, DayData, TurboRef, TurboSeries, sort_turbos

FORMAT = 1
# Levels made up in each direction, the closest to the open first: the trader looks at the twenty closest.
LEVELS = 40
# A recorded turbo with fewer bars beside the index says nothing of its price.
LEAST_BARS = 60
# Nor does one whose index barely moved (points of standard deviation), nor a bid this close to nothing.
LEAST_INDEX_SPREAD = 5.0
LEAST_BID = 0.05
DEFAULT_SPREAD = 0.01
UNDERLYING = {"provider": "dukascopy", "key": "us100", "side": "bid"}
# Under this many recorded days the measure is said to be thin.
FEW_DAYS = 5
# A model check: the trades of the run it checks, kept in its folder, and their pairing with its own.
RECORDED_TRADES_FILE = "recorded_trades.json"
MODEL_CHECK_FILE = "model_check.json"


class NoCalibration(ValueError):
    """No turbo can be made up: the measure is missing, or found nothing."""


@dataclass
class DirectionModel:
    factor: float
    offset: float
    spacing: float
    turbos: int
    bars: int
    r2: float | None = None
    # The median distance of the model's bid from the recorded one, in euros and in percent of it.
    error_eur: float | None = None
    error_percent: float | None = None

    def price(self, index: float, barrier: float, direction: str) -> float:
        level = barrier + self.offset
        return self.factor * ((index - level) if direction == LONG else (level - index))


@dataclass
class TurboFit:
    """One recorded turbo's price against the index: euros a point, and the level its price would be nothing at."""

    direction: str
    barrier: float
    factor: float
    level: float
    bars: int
    r2: float


def _regression(xs: list[float], ys: list[float]) -> tuple[float, float, float] | None:
    count = len(xs)
    mean_x, mean_y = sum(xs) / count, sum(ys) / count
    sxx = sum((x - mean_x) ** 2 for x in xs)
    if sxx <= 0:
        return None
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    slope = sxy / sxx
    intercept = mean_y - slope * mean_x
    syy = sum((y - mean_y) ** 2 for y in ys)
    residual = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys))
    return slope, intercept, (1 - residual / syy) if syy > 0 else 0.0


def pairs(series: TurboSeries, index: dict[int, float]) -> tuple[list[float], list[float]]:
    """The recorded bids of a turbo beside the index's price of the same ten seconds."""
    xs, ys = [], []
    for place, stamp in enumerate(series.ts):
        level = index.get(stamp)
        bid = series.bid_close[place]
        if level is not None and not math.isnan(bid) and bid >= LEAST_BID:
            xs.append(level)
            ys.append(bid)
    return xs, ys


def fit_turbo(series: TurboSeries, index: dict[int, float]) -> TurboFit | None:
    """A recorded turbo's euros per point and effective level, or None when its bars say too little."""
    xs, ys = pairs(series, index)
    if len(xs) < LEAST_BARS:
        return None
    mean = sum(xs) / len(xs)
    if math.sqrt(sum((x - mean) ** 2 for x in xs) / len(xs)) < LEAST_INDEX_SPREAD:
        return None
    found = _regression(xs, ys)
    if found is None:
        return None
    slope, intercept, r2 = found
    direction = series.ref.direction
    factor = slope if direction == LONG else -slope
    if factor <= 0:
        return None
    level = -intercept / factor if direction == LONG else intercept / factor
    return TurboFit(direction, series.ref.barrier, factor, level, len(xs), r2)


def _spacing(barriers: list[float]) -> float | None:
    distinct = sorted(set(barriers))
    gaps = [after - before for before, after in zip(distinct, distinct[1:]) if after > before]
    return median(gaps) if gaps else None


@dataclass
class Measure:
    """What the recorded days give, fed a day at a time, before it becomes a calibration."""

    clock: TradingClock
    fits: list = field(default_factory=list)
    # By local hour, the spreads of the recorded bars.
    spreads: dict = field(default_factory=lambda: defaultdict(list))
    # By direction, each day's spacing of the levels.
    spacings: dict = field(default_factory=lambda: defaultdict(list))
    first_minutes: list = field(default_factory=list)
    last_minutes: list = field(default_factory=list)
    decimals: list = field(default_factory=list)
    issuers: dict = field(default_factory=lambda: defaultdict(int))
    # By direction, (index, knock-out level, bid) of the turbos measured, to judge the model by.
    samples: dict = field(default_factory=lambda: defaultdict(list))
    days: list = field(default_factory=list)

    def add_day(self, recorded: DayData, index: dict[int, float]) -> None:
        """A recorded day: its turbos beside the index's closes by ten-second boundary."""
        turbos = recorded.all_turbos()
        if not turbos or not index:
            return
        used = False
        for direction in (LONG, SHORT):
            found = _spacing([series.ref.barrier for series in recorded.turbos.get(direction, [])])
            if found:
                self.spacings[direction].append(found)
        firsts, lasts = [], []
        for series in turbos:
            fit = fit_turbo(series, index)
            if fit is not None:
                self.fits.append(fit)
                used = True
                xs, ys = pairs(series, index)
                # A sample of each turbo's bars, to measure the model against.
                step = max(1, len(xs) // 200)
                self.samples[series.ref.direction] += [(x, series.ref.barrier, y) for x, y in zip(xs[::step], ys[::step])]
            self.issuers[series.ref.issuer or "Unknown"] += 1
            self.decimals.append(series.decimals)
            for place in range(len(series.ts)):
                ask, bid = series.ask_close[place], series.bid_close[place]
                if not math.isnan(ask) and not math.isnan(bid) and ask >= bid:
                    self.spreads[self.clock.local(series.ts[place]).hour].append(round(ask - bid, 6))
            if series.ts:
                firsts.append(series.ts[0])
                lasts.append(series.ts[-1] + BAR_SECONDS)
        if firsts:
            self.first_minutes.append(self.clock.minute_of_day(min(firsts)))
            self.last_minutes.append(self.clock.minute_of_day(max(lasts) - 1) + 1)
        if used:
            self.days.append(recorded.day.isoformat())

    def calibration(self, *, indice: str, underlying: dict, made_at: str) -> dict:
        """The measure as the model reads it; NoCalibration when no turbo could be fitted in a direction."""
        directions = {}
        for direction in (LONG, SHORT):
            fits = [fit for fit in self.fits if fit.direction == direction]
            spacing = median(self.spacings[direction]) if self.spacings[direction] else None
            if not fits or spacing is None:
                raise NoCalibration(f"No recorded {direction} turbo could be measured against the index on those days")
            model = DirectionModel(
                factor=median(fit.factor for fit in fits),
                offset=median(fit.level - fit.barrier for fit in fits),
                spacing=spacing,
                turbos=len(fits),
                bars=sum(fit.bars for fit in fits),
            )
            samples = self.samples[direction]
            predicted = [model.price(index, barrier, direction) for index, barrier, _ in samples]
            actual = [bid for _, _, bid in samples]
            mean = sum(actual) / len(actual)
            total = sum((bid - mean) ** 2 for bid in actual)
            residual = sum((bid - guess) ** 2 for bid, guess in zip(actual, predicted))
            model.r2 = round(1 - residual / total, 4) if total > 0 else None
            model.error_eur = round(median(abs(bid - guess) for bid, guess in zip(actual, predicted)), 4)
            model.error_percent = round(median(abs(bid - guess) / bid * 100 for bid, guess in zip(actual, predicted)), 2)
            model.factor, model.offset, model.spacing = round(model.factor, 8), round(model.offset, 2), round(model.spacing, 2)
            directions[direction] = asdict(model)
        hours = {str(hour): round(median(values), 4) for hour, values in sorted(self.spreads.items()) if values}
        return {
            "format": FORMAT,
            "made_at": made_at,
            "indice": indice,
            "underlying": underlying,
            "timezone": self.clock.zone.zone,
            "days": sorted(self.days),
            "directions": directions,
            "spread_by_hour": hours,
            "quoting": {
                "first_minute": int(median(self.first_minutes)) if self.first_minutes else 0,
                "last_minute": int(median(self.last_minutes)) if self.last_minutes else 1440,
            },
            "decimals": int(median(self.decimals)) if self.decimals else 2,
            "issuer": max(self.issuers, key=self.issuers.get) if self.issuers else None,
            "few_days": len(self.days) < FEW_DAYS,
        }


def index_closes(rows: list, side: str = "bid") -> dict[int, float]:
    """An asset's rows (src/ta_engine/source.py's columns) as their close by ten-second boundary."""
    column = 8 if side == "ask" else 4
    found = {}
    for row in rows:
        value = row[column] if row[column] is not None else row[4]
        if value is not None:
            found[int(row[0])] = float(value)
    return found


# ── the turbos of a day, made up ──


def _spread(calibration: dict, hour: int) -> float:
    hours = calibration.get("spread_by_hour") or {}
    if str(hour) in hours:
        return hours[str(hour)]
    if not hours:
        return DEFAULT_SPREAD
    nearest = min(hours, key=lambda known: abs(int(known) - hour))
    return hours[nearest]


def ladder(open_price: float, direction: str, spacing: float, levels: int = LEVELS) -> list[float]:
    """The knock-out levels of a day: multiples of ``spacing`` beyond the open, the closest first."""
    if direction == LONG:
        first = math.floor(open_price / spacing) * spacing
        if first >= open_price:
            first -= spacing
        return [round(first - step * spacing, 2) for step in range(levels)]
    first = math.ceil(open_price / spacing) * spacing
    if first <= open_price:
        first += spacing
    return [round(first + step * spacing, 2) for step in range(levels)]


def made_up_day(calibration: dict, bars: list, clock: TradingClock, day: date, indice: str, start: int, end: int) -> DayData:
    """
    The turbos of ``day`` made up from the index's rows ``bars`` (ts, bid open,
    high, low, close...), in the hours turbos are quoted. A turbo's bars stop
    at the one where the index crosses its level, that one ending at a tick.
    """
    quoting = calibration["quoting"]
    side = (calibration.get("underlying") or UNDERLYING).get("side", "bid")
    columns = (5, 6, 7, 8) if side == "ask" else (1, 2, 3, 4)
    day_start = clock.day_bounds(day)[0]
    window_start = day_start + quoting["first_minute"] * 60
    window_end = day_start + quoting["last_minute"] * 60
    index = []
    for row in bars:
        stamp = int(row[0])
        if not (window_start <= stamp < window_end and start <= stamp < end):
            continue
        values = [row[column] for column in columns]
        if any(value is None for value in values):
            continue
        index.append((stamp, *map(float, values)))
    if not index:
        return DayData(day=day, start=start, end=end, indice=indice, turbos={}, covered=set())
    decimals = calibration.get("decimals", 2)
    tick = 10 ** -decimals
    spreads = [_spread(calibration, clock.local(stamp).hour) for stamp, *_ in index]
    by_direction: dict[str, list[TurboSeries]] = {}
    for direction, measured in calibration["directions"].items():
        model = DirectionModel(**measured)
        for number, barrier in enumerate(ladder(index[0][4], direction, model.spacing)):
            series = TurboSeries(TurboRef(
                instrument_id=-(1_000_000 + (0 if direction == LONG else 500_000) + number),
                uic=None, asset_type="WarrantOpenEndKnockOut", symbol=f"MODEL-{direction[0].upper()}{barrier:g}",
                description=f"Modelled turbo {direction} {barrier:g}", issuer="Model", barrier=barrier,
                direction=direction, currency="EUR",
            ))
            for place, (stamp, opened, high, low, closed) in enumerate(index):
                crossed = low <= barrier if direction == LONG else high >= barrier
                best, worst = (high, low) if direction == LONG else (low, high)
                price = lambda level: max(tick, round(model.price(level, barrier, direction), decimals))  # noqa: E731
                if crossed:
                    # Knocked out inside these ten seconds: the last bar of the turbo ends at a tick.
                    series.append(stamp, price(opened), max(price(opened), price(best)), tick, tick, None, None)
                    break
                bid_open, bid_close = price(opened), price(closed)
                spread = spreads[place]
                series.append(stamp, bid_open, price(best), price(worst), bid_close,
                              round(bid_open + spread, decimals), round(bid_close + spread, decimals))
            if len(series):
                by_direction.setdefault(direction, []).append(series)
    return DayData(
        day=day, start=start, end=end, indice=indice, turbos=sort_turbos(by_direction),
        covered={stamp for stamp, *_ in index},
    )


class ModelData:
    """Days of made-up turbos, as DatabaseData and FileData give recorded ones."""

    def __init__(self, calibration: dict, rows, clock: TradingClock):
        if calibration.get("format") != FORMAT or not calibration.get("directions"):
            raise NoCalibration("The turbo model's measure is missing or of another version: measure it again")
        self._calibration = calibration
        self._rows = rows
        self._clock = clock
        underlying = calibration.get("underlying") or UNDERLYING
        self._provider, self._key = underlying["provider"], underlying["key"]

    @property
    def calibration(self) -> dict:
        return self._calibration

    async def day(self, indice: str, day: date, start: int, end: int) -> DayData:
        try:
            found, _ = await self._rows.rows(self._provider, self._key, start, end)
        except (ValueError, OSError):
            found = []
        return made_up_day(self._calibration, found, self._clock, day, indice, start, end)

    async def close(self) -> None:
        pass


def read_calibration(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as in_file:
            return json.load(in_file)
    except (OSError, ValueError):
        raise NoCalibration(f"No measure of the turbos at {path}: measure them first") from None


# ── a modelled run against a recorded one ──


def compare_trades(recorded: list[dict], modelled: list[dict]) -> dict:
    """
    The trades of the same signals on recorded and on modelled turbos, paired
    by signal: how far apart their results are, and the totals they make.
    """
    by_signal = {trade.get("signal_id"): trade for trade in modelled if trade.get("signal_id") is not None}
    paired = [(trade, by_signal[trade["signal_id"]]) for trade in recorded if trade.get("signal_id") in by_signal]
    differences = [modelled["performance_percent"] - real["performance_percent"] for real, modelled in paired]
    same_exit = sum(1 for real, modelled in paired if real["exit"]["reason"] == modelled["exit"]["reason"])
    real_results = [real["performance_percent"] for real, _ in paired]
    model_results = [modelled["performance_percent"] for _, modelled in paired]
    correlation = None
    if len(paired) >= 3:
        mean_real, mean_model = sum(real_results) / len(paired), sum(model_results) / len(paired)
        cov = sum((a - mean_real) * (b - mean_model) for a, b in zip(real_results, model_results))
        spread_real = math.sqrt(sum((a - mean_real) ** 2 for a in real_results))
        spread_model = math.sqrt(sum((b - mean_model) ** 2 for b in model_results))
        correlation = round(cov / (spread_real * spread_model), 3) if spread_real > 0 and spread_model > 0 else None
    entry_prices = [(real["entry"]["price"], modelled["entry"]["price"]) for real, modelled in paired]
    return {
        "recorded_trades": len(recorded),
        "modelled_trades": len(modelled),
        "paired": len(paired),
        "mean_difference_percent": round(sum(differences) / len(differences), 3) if differences else None,
        "median_absolute_difference_percent": round(median(abs(value) for value in differences), 3) if differences else None,
        "correlation": correlation,
        "same_exit_percent": round(same_exit / len(paired) * 100, 1) if paired else None,
        "median_entry_price": {
            "recorded": round(median(real for real, _ in entry_prices), 3) if entry_prices else None,
            "modelled": round(median(model for _, model in entry_prices), 3) if entry_prices else None,
        },
        "recorded_total_percent": round(sum(real_results), 2) if paired else None,
        "modelled_total_percent": round(sum(model_results), 2) if paired else None,
    }


# ── the command ──


async def _calibrate(arguments) -> dict:
    from src.backtest.run import _config, _sources

    clock = TradingClock(arguments.timezone)
    data, rows, _ = await _sources(arguments, _config())
    measure = Measure(clock)
    underlying = {"provider": arguments.underlying.split("/")[0], "key": arguments.underlying.split("/", 1)[1], "side": arguments.side}
    try:
        for day in days_between(date.fromisoformat(arguments.first), date.fromisoformat(arguments.last)):
            start, end = clock.day_bounds(day)
            recorded = await data.day(arguments.indice, day, start, end)
            if not recorded.all_turbos():
                continue
            found, _ = await rows.rows(underlying["provider"], underlying["key"], start, end)
            measure.add_day(recorded, index_closes(found, arguments.side))
    finally:
        await data.close()
    made_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return measure.calibration(indice=arguments.indice, underlying=underlying, made_at=made_at)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.backtest.turbo_model", description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    calibrate = commands.add_parser("calibrate", help="measure the recorded turbos against an index")
    calibrate.add_argument("--from", dest="first", required=True)
    calibrate.add_argument("--to", dest="last", required=True)
    calibrate.add_argument("--indice", default="us100")
    calibrate.add_argument("--underlying", default="dukascopy/us100", help="the index the turbos are made up from: provider/key")
    calibrate.add_argument("--side", default="bid", choices=("bid", "ask"))
    calibrate.add_argument("--timezone", default="Europe/Paris")
    calibrate.add_argument("--data", default="database", help="database (the configuration's), or files:<folder>")
    calibrate.add_argument("--prices", help="the price history folder (Dukascopy's days)")
    calibrate.add_argument("--out", required=True)
    calibrate.add_argument("--low-priority", action="store_true")
    arguments = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if arguments.low_priority:
        try:
            os.nice(19)
        except OSError:
            pass
    try:
        found = asyncio.run(_calibrate(arguments))
    except (NoCalibration, ValueError) as e:
        print(f"turbo model: {e}", file=sys.stderr)
        return 2
    partial = f"{arguments.out}.part"
    os.makedirs(os.path.dirname(os.path.abspath(arguments.out)), exist_ok=True)
    with open(partial, "w", encoding="utf-8") as out_file:
        json.dump(found, out_file, indent=2)
    os.replace(partial, arguments.out)
    for direction, measured in found["directions"].items():
        print(
            f"{direction}: {measured['factor']:.6f} € a point, level {measured['offset']:+.1f} points from the knock-out, "
            f"levels every {measured['spacing']:g} points; {measured['turbos']} turbos, R² {measured['r2']}, "
            f"median error {measured['error_eur']} € ({measured['error_percent']} %)"
        )
    print(f"{len(found['days'])} days; quoted {found['quoting']['first_minute'] // 60:02d}:{found['quoting']['first_minute'] % 60:02d}"
          f" to {found['quoting']['last_minute'] // 60:02d}:{found['quoting']['last_minute'] % 60:02d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
