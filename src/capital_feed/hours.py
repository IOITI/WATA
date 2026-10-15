"""
When a market of Capital.com is open, from the opening hours it gives with an
instrument's details:

    "openingHours": {"mon": ["00:00 - 21:59", "23:05 - 00:00"], ..., "sat": [], "zone": "UTC"}

Read as Capital.com shows them on its platform: the last minute named is
open (21:59 means until 22:00), and an end at 00:00 is midnight at the end of
the day. Pure: no clock.
"""

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone

import pytz

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_DAY_SECONDS = 86400
_MINUTE = 60


@dataclass(frozen=True)
class OpeningHours:
    # Per weekday (Monday 0), the stretches open as [start, end) in seconds of the day, in ``zone``.
    days: tuple[tuple[tuple[int, int], ...], ...]
    zone: str

    def is_open(self, moment: datetime) -> bool:
        local = moment.astimezone(self._tz())
        seconds = local.hour * 3600 + local.minute * 60 + local.second
        return any(start <= seconds < end for start, end in self.days[local.weekday()])

    def next_opening(self, moment: datetime) -> datetime | None:
        """The next moment the market opens after ``moment`` (itself if it opens then); None if it never does."""
        zone = self._tz()
        local = moment.astimezone(zone)
        for offset in range(8):
            day = local.date() + timedelta(days=offset)
            for start, _ in self.days[day.weekday()]:
                # A stretch that goes on from the day before is not an opening.
                if start == 0 and self._open_at_end_of(day.weekday() - 1):
                    continue
                opening = zone.localize(datetime.combine(day, time(start // 3600, start % 3600 // 60)))
                if opening >= moment:
                    return opening.astimezone(timezone.utc)
        return None

    def _open_at_end_of(self, weekday: int) -> bool:
        return any(end == _DAY_SECONDS for _, end in self.days[weekday % 7])

    def _tz(self):
        return pytz.timezone(self.zone)


def _clock(text: str) -> int:
    hours, minutes = text.strip().split(":")
    hour, minute = int(hours), int(minutes)
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError(text)
    return hour * 3600 + minute * 60


def _stretch(text: str) -> tuple[int, int]:
    first, last = text.split("-")
    start, end = _clock(first), _clock(last)
    # "23:05 - 00:00": to midnight. Otherwise the last minute named is open.
    end = _DAY_SECONDS if end == 0 else end + _MINUTE
    if end <= start:
        raise ValueError(text)
    return start, end


def parse_opening_hours(value) -> OpeningHours | None:
    """The opening hours of an instrument's details; None when they are missing or not in this form."""
    if not isinstance(value, dict):
        return None
    zone = value.get("zone") or "UTC"
    try:
        pytz.timezone(zone)
        days = []
        for name in DAYS:
            stretches = value.get(name) or []
            if not isinstance(stretches, list):
                return None
            days.append(tuple(sorted(_stretch(text) for text in stretches if isinstance(text, str) and text.strip())))
    except (ValueError, pytz.UnknownTimeZoneError):
        return None
    return OpeningHours(days=tuple(days), zone=zone)
