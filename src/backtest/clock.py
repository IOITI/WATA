"""
Time in a backtest: the trading timezone of the configuration, its days, and
the ten-second boundaries prices are read at.

A recorded bar opens on the quote standing when its ten seconds began
(src/price_recorder/bars.py), so the price at a moment is read at the first
boundary at or after it: up to ten seconds late, never early. A moment is
never rounded itself: only where its price is read.
"""

import math
from datetime import date, datetime, time, timedelta

import pytz

BAR_SECONDS = 10


def ceil10(moment: float) -> int:
    """The first ten-second boundary at or after ``moment`` (epoch seconds)."""
    return int(math.ceil(round(moment, 6) / BAR_SECONDS)) * BAR_SECONDS


def floor10(moment: float) -> int:
    """The boundary that begins the ten seconds ``moment`` falls in."""
    return int(math.floor(round(moment, 6) / BAR_SECONDS)) * BAR_SECONDS


class TradingClock:
    """The trading timezone (``trade.config.general.timezone``): local days, hours and minutes."""

    def __init__(self, timezone_name: str):
        self.name = timezone_name
        self.zone = pytz.timezone(timezone_name)

    def local(self, moment: float) -> datetime:
        return datetime.fromtimestamp(moment, self.zone)

    def day_of(self, moment: float) -> date:
        return self.local(moment).date()

    def minute_of_day(self, moment: float) -> int:
        local = self.local(moment)
        return local.hour * 60 + local.minute

    def epoch(self, day: date, clock_time: str | time) -> float:
        """The moment a local time of a day is, "HH:MM" or "HH:MM:SS"."""
        if isinstance(clock_time, str):
            parts = [int(part) for part in clock_time.split(":")]
            clock_time = time(*parts)
        return self.zone.localize(datetime.combine(day, clock_time)).timestamp()

    def day_bounds(self, day: date) -> tuple[int, int]:
        """From local midnight of ``day`` to the next: 23 or 25 hours on the days the clocks change."""
        start = self.zone.localize(datetime.combine(day, time()))
        end = self.zone.localize(datetime.combine(day + timedelta(days=1), time()))
        return int(start.timestamp()), int(end.timestamp())

    def iso(self, moment: float | None) -> str | None:
        return None if moment is None else self.local(moment).isoformat(timespec="seconds")


def utc_iso(moment: float | None) -> str | None:
    """A moment as the files say it: UTC, the second it falls in, "2026-10-12T08:00:00Z"."""
    if moment is None:
        return None
    return datetime.fromtimestamp(math.floor(round(moment, 6)), pytz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def days_between(first_day: date, last_day: date) -> list[date]:
    return [first_day + timedelta(days=offset) for offset in range((last_day - first_day).days + 1)]
