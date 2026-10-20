"""
WATA's assets as Pine tickers, read as admin_ui/src/features/ta/engine/tickers.ts
reads them: the feed's name before the asset (CAPITAL_US100, DUKASCOPY_US100,
SAXO_12), then variants in any order, .ASK for the ask side and .R<ticks> for
range bars. An exchange prefix is accepted and ignored (CAPITAL:CAPITAL_US100).
"""

import re
from typing import NamedTuple

FEEDS = {"capital": "CAPITAL", "dukascopy": "DUKASCOPY", "saxo": "SAXO"}
_KEY = {
    "capital": re.compile(r"^[A-Z0-9][A-Z0-9_.]{0,63}$"),
    "dukascopy": re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$"),
    "saxo": re.compile(r"^[1-9][0-9]{0,9}$"),
}
_ASK = re.compile(r"\.ASK$", re.IGNORECASE)
_RANGE = re.compile(r"\.R([0-9]{1,6})$", re.IGNORECASE)


class Ticker(NamedTuple):
    provider: str
    key: str
    side: str = "bid"
    range_ticks: int | None = None

    @property
    def ref(self) -> str:
        return f"{self.provider}/{self.key}"

    def text(self) -> str:
        key = self.key.upper() if self.provider == "dukascopy" else self.key
        return (
            f"{FEEDS[self.provider]}_{key}"
            + (".ASK" if self.side == "ask" else "")
            + (f".R{self.range_ticks}" if self.range_ticks else "")
        )


def parse_ticker(text: str) -> Ticker | None:
    """A WATA ticker, or None when ``text`` names no asset WATA could hold."""
    if not isinstance(text, str):
        return None
    rest = text.strip()
    rest = rest[rest.rfind(":") + 1:]
    side, range_ticks = "bid", None
    while True:
        ask, ranged = _ASK.search(rest), _RANGE.search(rest)
        if ask and side == "bid":
            side, rest = "ask", rest[:ask.start()]
        elif ranged and range_ticks is None:
            range_ticks = int(ranged.group(1))
            if range_ticks < 1:
                return None
            rest = rest[:ranged.start()]
        else:
            break
    for provider, feed in FEEDS.items():
        prefix = f"{feed}_"
        if not rest.upper().startswith(prefix):
            continue
        raw = rest[len(prefix):]
        key = raw.lower() if provider == "dukascopy" else raw.upper() if provider == "capital" else raw
        return Ticker(provider, key, side, range_ticks) if _KEY[provider].match(key) else None
    return None
