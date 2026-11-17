"""Least squares over samples: how fast a figure moves, in units per day."""

from datetime import datetime

DAY_SECONDS = 86_400


def moment(value) -> datetime | None:
    """A sample's ``at`` (ISO 8601, UTC), None when it is not one."""
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def per_day(points: list[tuple[datetime, float]], least_days: float) -> float | None:
    """
    The slope of ``points`` (moment, value), per day; None when they span less than ``least_days`` or are
    fewer than three.
    """
    if len(points) < 3:
        return None
    first = min(at for at, _ in points)
    xs = [(at - first).total_seconds() / DAY_SECONDS for at, _ in points]
    if max(xs) - min(xs) < least_days:
        return None
    ys = [value for _, value in points]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    spread = sum((x - mean_x) ** 2 for x in xs)
    if spread == 0:
        return None
    return sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / spread
