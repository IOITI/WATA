"""
A run's trade autopsy as the admin works it out (the method is src/admin/trade_autopsy.py, pure): its trades, signals
and days read from its folder, the index's minute bars of each traded day read where the Data pages read them
(src/admin/asset_bars.py: the recorder's or the Capital.com feed's tables, Dukascopy's files), the result kept beside
the run (``autopsy.json``) and read again until the method changes (``AUTOPSY_VERSION``). A run on the lock box's days
is refused: what a candidate is finally checked on is never looked into first.
"""

import asyncio
import hashlib
from datetime import date

from src.admin import asset_bars
from src.admin.errors import NotPossible
from src.admin.trade_autopsy import AUTOPSY_VERSION, autopsy

AUTOPSY_FILE = "autopsy.json"
MINUTE = 60


def seed_of(run_id: str) -> int:
    return int(hashlib.sha256(f"autopsy:{run_id}".encode()).hexdigest()[:8], 16)


async def run_autopsy(backtests, queries, history, journal, run_id: str, timezone_name: str, today: date) -> dict:
    """A finished run of one combination's autopsy, worked out once and kept; refused for a run on the lock box."""
    kept = backtests.kept(run_id, AUTOPSY_FILE)
    if isinstance(kept, dict) and kept.get("version") == AUTOPSY_VERSION:
        return kept
    request = backtests.request_of(run_id)
    if request.get("kind") != "run" or backtests.state(run_id).get("state") != "done":
        raise NotPossible("Only a finished run of one combination has an autopsy")
    first, last = date.fromisoformat(request["from"]), date.fromisoformat(request["to"])
    if journal is not None and journal.in_lockbox(first, last):
        raise NotPossible("This run holds days of the lock box: kept for a candidate's final check, never looked into first")
    asset = ((request.get("chart") or {}).get("asset")) or {}
    settings = backtests.settings_of(run_id)
    trades, days = backtests.trades(run_id), backtests.days(run_id)
    try:
        signals = backtests.signals(run_id)
    except NotPossible:
        # Without its signals, a trade's features are read up to the moment it was decided.
        signals = []
    traded = sorted({trade.get("day") for trade in trades if trade.get("day")})
    bars_by_day: dict[str, list] = {}
    for day in traded:
        try:
            found = await asset_bars.bars(queries, history, timezone_name, asset.get("provider"), asset.get("key"),
                                          date.fromisoformat(day), date.fromisoformat(day), MINUTE, today)
            bars_by_day[day] = found["bars"]
        except Exception:
            # A day the index cannot be read for: its trades are described without it.
            bars_by_day[day] = []
    found = await asyncio.to_thread(
        autopsy, trades, signals, days, lambda day: bars_by_day.get(day, []),
        signal_delay=float(settings.get("signal_delay_seconds") or 0.0), seed=seed_of(run_id),
    )
    found["asset"] = {"provider": asset.get("provider"), "key": asset.get("key")}
    backtests.keep(run_id, AUTOPSY_FILE, found)
    return found
