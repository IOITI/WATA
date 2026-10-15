"""
A made-up Capital.com feed for the fake admin server: the Nasdaq 100 as
Capital.com quotes it, around the clock on its opening hours, with a quote a
few times a second while a chart listens.

The quotes go through the feed's own code (BarBuilder and LiveBar, as in
src/capital_feed/recorder.py) into the admin's real LivePrices hub, in place
of RabbitMQ; the bars closed meanwhile are kept, so that the bars read back
are the ones the chart was shown live. Before the server started, the bars
are a function of the time asked for, like the other made-up prices.
"""

import asyncio
import random
import time
from datetime import datetime, timedelta, timezone

from src.admin.data import aggregate
from src.capital_feed.hours import parse_opening_hours
from src.capital_feed.live import LiveBar, bar_row
from src.price_recorder.bars import BAR_SECONDS, BarBuilder, Quote, bucket_start

import fake_prices

EPIC = "US100"
REF = f"capital/{EPIC}"
INSTRUMENT = {
    "id": 101, "epic": EPIC, "label": "us100", "name": "US Tech 100", "instrument_type": "INDICES",
    "currency": "USD", "decimals": 1,
}
HOURS = {
    **{day: ["00:00 - 21:59", "23:05 - 00:00"] for day in ("mon", "tue", "wed", "thu")},
    "fri": ["00:00 - 21:59"], "sat": [], "sun": ["23:05 - 00:00"], "zone": "UTC",
}
OPENING_HOURS = parse_opening_hours(HOURS)
SPREAD = 1.8
# Recorded since a week before the server started.
HISTORY_DAYS = 7
STARTED = time.time()

# The ten seconds closed while the server ran, as the feed would have written them.
_recorded: dict[int, list] = {}
_received: list[tuple[float, float]] = []


def is_open(t: float) -> bool:
    return OPENING_HOURS.is_open(datetime.fromtimestamp(t, timezone.utc))


def quote_at(t: float) -> tuple[float, float]:
    """Capital.com's bid and ask at ``t``: the made-up index, quoted with one decimal and a spread."""
    bid = round(fake_prices.index_at(t) + 0.3, 1)
    return bid, round(bid + SPREAD, 1)


def _made_up_bar(bucket: int) -> list:
    """The ten seconds of ``bucket`` before the server started: five quotes of the made-up index."""
    quotes = [quote_at(bucket + offset * 2.4) for offset in range(5)]
    bids = [quote[0] for quote in quotes]
    asks = [quote[1] for quote in quotes]
    return [bucket, bids[0], max(bids), min(bids), bids[-1], asks[0], max(asks), min(asks), asks[-1], 3 + bucket // 10 % 7]


def ten_second_bars(start: datetime, end: datetime, now: datetime) -> list[list]:
    """The bars recorded in [start, end): made up before the server started, as received since."""
    first_kept = int(STARTED) - HISTORY_DAYS * 86400
    first = max(int(start.timestamp()), first_kept)
    first += -first % BAR_SECONDS
    # The bucket in progress is not written yet.
    last = min(int(end.timestamp()), bucket_start(now.timestamp()))
    rows = []
    for bucket in range(first, last, BAR_SECONDS):
        if bucket in _recorded:
            rows.append(list(_recorded[bucket]))
        elif bucket < STARTED - BAR_SECONDS and is_open(bucket):
            rows.append(_made_up_bar(bucket))
    return rows


def bars(start: datetime, end: datetime, interval: int, limit: int, now: datetime) -> tuple[list[list], int | None]:
    rows = ten_second_bars(start, end, now)
    through = rows[-1][0] if rows else None
    if interval == BAR_SECONDS:
        return rows[:limit], through
    return aggregate(rows, interval, limit), through


def asset(now: datetime) -> dict:
    rows = ten_second_bars(now - timedelta(hours=60), now, now)
    last = rows[-1] if rows else None
    return {
        **INSTRUMENT,
        "first_seen_at": datetime.fromtimestamp(STARTED - HISTORY_DAYS * 86400, timezone.utc),
        "last_seen_at": now,
        "first_t": int(STARTED) - HISTORY_DAYS * 86400 + (-(int(STARTED) - HISTORY_DAYS * 86400)) % BAR_SECONDS,
        "last_t": last[0] if last else None,
        "last_bid": last[4] if last else None,
        "last_ask": last[8] if last else None,
    }


def coverage(start: datetime, end: datetime, now: datetime) -> list[dict]:
    first = max(int(start.timestamp()), int(STARTED) - HISTORY_DAYS * 86400)
    first += -first % BAR_SECONDS
    last = min(int(end.timestamp()), bucket_start(now.timestamp()))
    return [
        {"ts": datetime.fromtimestamp(bucket, timezone.utc), "feed_ok": is_open(bucket)}
        for bucket in range(first, last, BAR_SECONDS)
    ]


def volume(start: datetime, end: datetime, interval: int, limit: int, now: datetime) -> list[list]:
    """Capital.com's volume of each minute: made up, a little above the quotes the feed counted."""
    minutes = {}
    for row in ten_second_bars(start, end, now):
        minute = row[0] - row[0] % 60
        minutes[minute] = minutes.get(minute, 0) + row[9] * 3 + 4
    added = {}
    for minute, value in sorted(minutes.items()):
        bucket = minute - minute % interval
        added[bucket] = added.get(bucket, 0) + value
    return [[bucket, float(value)] for bucket, value in sorted(added.items())][:limit]


def volume_days(start: datetime, end: datetime, now: datetime) -> dict[str, float]:
    days: dict[str, float] = {}
    for bucket, value in volume(start, end, 60, 1_000_000, now):
        day = datetime.fromtimestamp(bucket, timezone.utc).astimezone(fake_prices.TRADING_TIMEZONE).date().isoformat()
        days[day] = days.get(day, 0.0) + value
    return days


def feed_state(now: datetime) -> dict:
    """What the feed would write of itself: streaming while its market is open."""
    recent = [delay for at, delay in _received if now.timestamp() - at <= 60]
    recent.sort()
    open_now = is_open(now.timestamp())
    opening = OPENING_HOURS.next_opening(now)
    return {
        "updated_at": now - timedelta(seconds=3), "started_at": datetime.fromtimestamp(STARTED, timezone.utc),
        "version": "dev", "environment": "demo",
        "state": "streaming" if open_now else "market_closed",
        "detail": f"Streaming {EPIC}" if open_now else "Every market followed is closed",
        "figures": {
            "instruments": [{
                "epic": EPIC, "label": "us100", "name": "US Tech 100", "market": "open" if open_now else "closed",
                "status": "TRADEABLE" if open_now else "CLOSED",
                "last_quote_at": (now - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ") if open_now else None,
                "next_opening": None if open_now or opening is None else opening.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "subscription": "PROCESSED",
            }],
            "quotes_per_minute": len(recent) or (240 if open_now else 0),
            "delay_ms": {
                "p50": recent[len(recent) // 2] if recent else 41.0,
                "p95": recent[int(len(recent) * 0.95)] if recent else 96.0,
                "max": recent[-1] if recent else 140.0,
                "count": len(recent),
            },
            "stream": {"connected": True, "since": datetime.fromtimestamp(STARTED, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "connections": 1, "last_error": None},
            "live": {"sent": len(_received), "dropped": 0, "failing": False, "last_error": None},
            "minutes": {"enabled": True, "last": (now - timedelta(seconds=20)).strftime("%Y-%m-%dT%H:%M:%SZ"), "error": None},
            "day_files": {"enabled": True, "written": 0, "last": None},
            "pending_buckets": 0, "dropped_buckets": 0,
            "last_bar_at": datetime.fromtimestamp(bucket_start(now.timestamp()), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    }


async def _quote_now(on_message, builder: BarBuilder, live: LiveBar, rng: random.Random) -> None:
    """Quotes as the stream sends them, and each ten seconds closed: while the market is open."""
    while True:
        await asyncio.sleep(rng.choice((0.05, 0.12, 0.2, 0.3, 0.45)))
        now = time.time()
        for bucket in builder.close_until(now):
            for bar in bucket.bars:
                row = bar_row(bar)
                _recorded[row[0]] = row
                on_message({"type": "bar", "ref": REF, "bar": row})
        if not is_open(now):
            continue
        bid, ask = quote_at(now + rng.uniform(-3, 3))
        quote = Quote(bid=bid, ask=ask)
        builder.update(EPIC, quote, now)
        bar = live.update(quote, now)
        quoted_ms = int(now * 1000) - rng.randint(25, 120)
        _received.append((now, now * 1000 - quoted_ms))
        del _received[:-2000]
        on_message({
            "type": "quote", "ref": REF, "t": quoted_ms, "rx": int(now * 1000),
            "bid": bid, "ask": ask, "bar": bar_row(bar) if bar else None,
        })


class _Running:
    def __init__(self, task: asyncio.Task):
        self._task = task

    async def close(self) -> None:
        self._task.cancel()


async def source(on_message):
    """Stands for RabbitMQ in LivePrices: starts the made-up stream, returns what stops it."""
    builder = BarBuilder()
    live = LiveBar(EPIC)
    on_message({"type": "state", "ref": REF, "market": "open" if is_open(time.time()) else "closed"})
    return _Running(asyncio.create_task(_quote_now(on_message, builder, live, random.Random())))
