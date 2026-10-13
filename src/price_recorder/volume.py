"""
What Saxo counts as traded on an instrument: a total for the day, sent with
its quotes when the subscription asks for it (the ``PriceInfoDetails`` field
group). Not every instrument has one: an index never does, and a turbo quoted
by its issuer alone may not.

It is kept beside the bars, not in them: one row each time an instrument's
total changes. A turbo trades a few times a day; its quote moves all day.

Pure logic: no clock, no I/O.
"""

import math
from typing import Hashable, Iterable

VOLUME_FIELD_GROUP = "PriceInfoDetails"


def read_volume(row: dict) -> float | None:
    """The day's volume of a streamed InfoPrice row, None when Saxo gives none for it."""
    volume = (row.get(VOLUME_FIELD_GROUP) or {}).get("Volume")
    if isinstance(volume, bool) or not isinstance(volume, (int, float)):
        return None
    if not math.isfinite(volume) or volume < 0:
        return None
    return float(volume)


class VolumeChanges:
    """The last total received for each instrument, and those that changed since they were last taken."""

    def __init__(self):
        self._totals: dict[Hashable, float] = {}
        # Instrument: (total, when it was received), for the ones to write.
        self._changed: dict[Hashable, tuple[float, float]] = {}

    def note(self, key: Hashable, total: float, at: float) -> None:
        """A total received from the stream at ``at``. The first one seen for an instrument counts as a change."""
        if self._totals.get(key) == total:
            return
        self._totals[key] = total
        self._changed[key] = (total, at)

    def take(self) -> dict[Hashable, tuple[float, float]]:
        """The totals that changed, to be written; nothing is left waiting."""
        changed, self._changed = self._changed, {}
        return changed

    def give_back(self, changed: dict[Hashable, tuple[float, float]]) -> None:
        """What could not be written waits for the next time, unless it changed again meanwhile."""
        self._changed = {**changed, **self._changed}

    def retain(self, keys: Iterable[Hashable]) -> None:
        """
        Forget the totals of the instruments that are no longer recorded: found
        again, theirs is new. A change still waiting to be written stays.
        """
        keep = set(keys)
        self._totals = {key: total for key, total in self._totals.items() if key in keep}
