"""
What the TA engine reads of the Capital.com feed's tables (CAPITAL_SCHEMA_SQL in
src/database/postgres.py): its instruments, its ten-second bars, and how far it
has written them. The feed writes each ten seconds' bars, then the coverage row
of those ten seconds: a coverage row says its bars are all in.
"""

from datetime import datetime, timezone

from src.database.postgres import CAPITAL_BAR_TABLE

# A row: epoch seconds, bid open, high, low, close, ask open, high, low, close, quote changes.
ROW_COLUMNS = ("ts", "bid_open", "bid_high", "bid_low", "bid_close", "ask_open", "ask_high", "ask_low", "ask_close", "updates")
BAR_SECONDS = 10


def _moment(seconds: int) -> datetime:
    return datetime.fromtimestamp(seconds, timezone.utc)


class CapitalTables:
    def __init__(self, db):
        self.db = db

    async def instrument(self, epic: str) -> dict | None:
        """The feed's id and decimals of an epic; None when the feed never recorded it."""
        row = await self.db.fetchrow("SELECT id, decimals FROM capital_instrument WHERE epic = $1", epic)
        return None if row is None else {"id": row["id"], "decimals": row["decimals"]}

    async def rows(self, instrument_id: int, start: int, end: int) -> list[tuple]:
        """The ten-second bars of an instrument that begin in [start, end), oldest first."""
        records = await self.db.fetch(
            f"""
            SELECT extract(epoch FROM ts)::bigint AS ts, bid_open, bid_high, bid_low, bid_close,
                   ask_open, ask_high, ask_low, ask_close, updates
            FROM {CAPITAL_BAR_TABLE}
            WHERE instrument_id = $1 AND ts >= $2 AND ts < $3
            ORDER BY ts
            """,
            instrument_id, _moment(start), _moment(end),
        )
        return [tuple(record[name] for name in ROW_COLUMNS) for record in records]

    async def covered_until(self) -> int | None:
        """Every ten seconds before this moment (epoch seconds) is written; None before the feed's first."""
        value = await self.db.fetchval(f"SELECT extract(epoch FROM max(ts))::bigint + {BAR_SECONDS} FROM capital_coverage")
        return None if value is None else int(value)
