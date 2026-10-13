"""
Made-up recorded prices for the fake admin server: an index and six turbos
whose prices follow it, the recorder's coverage with two gaps a day, and a
couple of trades and signals placed on them. Three of the turbos are not
quoted all day, each in its own way: holes, a stop, a knock-out.

Everything is a function of the time asked for, so two requests for the same
period get the same prices, and nothing is kept in memory.
"""

import math
from datetime import datetime, timedelta, timezone

import pytz

BAR_SECONDS = 10
TRADING_TIMEZONE = pytz.timezone("Europe/Paris")
TRADING_HOURS = (8, 22)

INDEX = {
    "id": 1, "uic": 1909050, "asset_type": "StockIndex", "symbol": "NDX.I", "description": "NASDAQ 100",
    "currency": "USD", "role": "underlying", "indice": "us100", "underlying_uic": 1909050,
    "direction": None, "barrier": None, "issuer": None,
}


def _turbo(instrument_id: int, direction: str, barrier: float, issuer: str) -> dict:
    return {
        "id": instrument_id, "uic": 40_000_000 + instrument_id, "asset_type": "WarrantOpenEndKnockOut",
        "symbol": f"{issuer}-NDX-{direction[0].upper()}{instrument_id}",
        "description": f"NASDAQ 100 Turbo {direction.capitalize()} {barrier:g} {issuer}",
        "currency": "EUR", "role": "turbo", "indice": "us100", "underlying_uic": 1909050,
        "direction": direction, "barrier": barrier, "issuer": issuer,
    }


INSTRUMENTS = [
    INDEX,
    _turbo(2, "long", 24150.0, "SG"),
    _turbo(3, "long", 24300.0, "BNP"),
    _turbo(4, "long", 24450.0, "SG"),
    _turbo(5, "short", 25900.0, "SG"),
    _turbo(6, "short", 26050.0, "BNP"),
    _turbo(7, "short", 26200.0, "SG"),
]
BY_ID = {instrument["id"]: instrument for instrument in INSTRUMENTS}

# Each trading day the made-up recorder is down for seven minutes, and later
# receives nothing for two: (hour, minute, minutes, feed arriving).
DAILY_GAPS = ((10, 40, 7, None), (14, 10, 2, False))
# What the made-up trader does each trading day: (instrument, entry, exit, as local "HH:MM:SS").
DAILY_TRADES = ((2, "09:20:07", "10:05:31"), (5, "15:02:44", "15:40:12"))
# What Saxo does not quote, each trading day, while the recorder runs: per instrument,
# the stretches without a quote as (hour, minute, minutes) ...
DAILY_HOLES = {3: ((11, 5, 12), (16, 30, 4))}
# ... the minute of the day after which it is no longer quoted at all ...
DAILY_STOPS = {6: 13 * 60 + 20}
# ... and the one at which it is knocked out, its price falling to nothing over the twenty minutes before.
DAILY_KNOCK_OUTS = {4: 17 * 60 + 45}
KNOCK_OUT_SLIDE_MINUTES = 20
# What Saxo counts as traded in a day on the turbos it counts a volume for.
DAILY_VOLUMES = {2: 15300.0, 3: 2100.0, 5: 4200.0}


def index_at(t: float) -> float:
    """A wandering index level: slow swings, quicker ones, and a little noise."""
    noise = ((int(t * 10) * 2654435761) % 1000) / 1000 - 0.5
    return (
        25000
        + 180 * math.sin(t / 7000)
        + 70 * math.sin(t / 1300 + 1.3)
        + 22 * math.sin(t / 170 + 0.7)
        + 6 * math.sin(t / 23)
        + 3 * noise
    )


def bid_ask_at(instrument: dict, t: float) -> tuple[float, float]:
    index = index_at(t)
    if instrument["role"] == "underlying":
        level = round(index, 2)
        return level, level
    distance = index - instrument["barrier"] if instrument["direction"] == "long" else instrument["barrier"] - index
    bid = max(round(distance / 100 - 0.02, 2), 0.05)
    knock_out = DAILY_KNOCK_OUTS.get(instrument["id"])
    if knock_out is not None:
        left = knock_out - _minute_of_day(t)
        if left < KNOCK_OUT_SLIDE_MINUTES:
            bid = max(round(bid * max(left, 0) / KNOCK_OUT_SLIDE_MINUTES, 2), 0.01)
    return bid, round(bid + 0.05, 2)


def _local(t: float) -> datetime:
    return datetime.fromtimestamp(t, timezone.utc).astimezone(TRADING_TIMEZONE)


def _minute_of_day(t: float) -> float:
    local = _local(t)
    return local.hour * 60 + local.minute + local.second / 60


def is_trading_time(t: float) -> bool:
    local = _local(t)
    return local.weekday() < 5 and TRADING_HOURS[0] <= local.hour < TRADING_HOURS[1]


def quoted(instrument_id: int, t: float) -> bool:
    """Whether Saxo quotes the instrument at that moment of a trading day."""
    minute = _minute_of_day(t)
    if minute >= DAILY_STOPS.get(instrument_id, 24 * 60) or minute >= DAILY_KNOCK_OUTS.get(instrument_id, 24 * 60):
        return False
    return not any(
        hour * 60 + start <= minute < hour * 60 + start + minutes
        for hour, start, minutes in DAILY_HOLES.get(instrument_id, ())
    )


def _gap_at(t: float):
    """None when recording normally; otherwise what the coverage says of that moment (None: no row at all)."""
    local = _local(t)
    minute_of_day = local.hour * 60 + local.minute
    for hour, minute, minutes, feed_ok in DAILY_GAPS:
        start = hour * 60 + minute
        if start <= minute_of_day < start + minutes:
            return ("gap", feed_ok)
    return None


def _first_bucket(start: datetime, step: int) -> int:
    seconds = int(start.timestamp())
    return seconds + (-seconds) % step


def bars(instrument_id: int, start: datetime, end: datetime, interval: int, limit: int, now: datetime) -> list[list]:
    instrument = BY_ID.get(instrument_id)
    if instrument is None:
        return []
    last = min(int(end.timestamp()), int(now.timestamp()) - BAR_SECONDS)
    samples = 5 if interval <= 60 else 12
    rows: list[list] = []
    bucket = _first_bucket(start, interval)
    while bucket + interval <= last and len(rows) < limit:
        # A longer bar hides a short gap or hole; nothing hides a turbo that is no longer quoted.
        if interval > 600:
            recorded = quoted(instrument_id, bucket) or quoted(instrument_id, bucket + interval - BAR_SECONDS)
        else:
            recorded = _gap_at(bucket) is None and quoted(instrument_id, bucket)
        if is_trading_time(bucket) and recorded:
            quotes = [bid_ask_at(instrument, bucket + offset * (interval - 0.01) / (samples - 1)) for offset in range(samples)]
            bids = [quote[0] for quote in quotes]
            asks = [quote[1] for quote in quotes]
            rows.append([
                bucket, bids[0], max(bids), min(bids), bids[-1], asks[0], max(asks), min(asks), asks[-1],
                (bucket // BAR_SECONDS) % 9 * (interval // BAR_SECONDS),
            ])
        bucket += interval
    return rows


def last_quote(instrument_id: int, start: datetime, end: datetime, now: datetime) -> dict:
    """The quote of the instrument's last ten-second bar in the period, as the list of instruments gives it."""
    bucket = min(int(end.timestamp()), int(now.timestamp()) - BAR_SECONDS)
    bucket -= bucket % BAR_SECONDS + BAR_SECONDS
    first = _first_bucket(start, BAR_SECONDS)
    # Back from the end, a minute at a time through what is not trading time.
    while bucket >= first:
        if not is_trading_time(bucket):
            # To the last ten seconds of the minute before.
            bucket -= bucket % 60 + BAR_SECONDS
            continue
        if _gap_at(bucket) is None and quoted(instrument_id, bucket):
            bid, ask = bid_ask_at(BY_ID[instrument_id], bucket + BAR_SECONDS - 0.01)
            return {"last_t": bucket, "last_bid": bid, "last_ask": ask}
        bucket -= BAR_SECONDS
    return {"last_t": None, "last_bid": None, "last_ask": None}


def runs(start: datetime, end: datetime, now: datetime) -> list[tuple]:
    """
    The bars of the period as stretches without a break, by instrument and in
    time order: (instrument id, first, last, bars, updates, without ask,
    delayed minutes, highest bid, last bid), like ``AdminQueries.price_runs``.
    """
    last = min(int(end.timestamp()), int(now.timestamp()) - BAR_SECONDS)
    first = _first_bucket(start, BAR_SECONDS)
    recorded = [
        bucket for bucket in range(first, last - BAR_SECONDS + 1, BAR_SECONDS)
        if is_trading_time(bucket) and _gap_at(bucket) is None
    ]
    found = []
    for instrument in INSTRUMENTS:
        run = None
        for bucket in recorded:
            if not quoted(instrument["id"], bucket):
                continue
            if run is None or bucket - run["last"] != BAR_SECONDS:
                if run is not None:
                    found.append(_as_run(instrument, run))
                run = {"first": bucket, "last": bucket, "bars": 0, "updates": 0, "high": 0.0}
            run["last"] = bucket
            run["bars"] += 1
            run["updates"] += (bucket // BAR_SECONDS) % 9
            # The high of a minute is near enough for a made-up price.
            if bucket % 60 == 0 or run["bars"] == 1:
                run["high"] = max(run["high"], bid_ask_at(instrument, bucket)[0])
        if run is not None:
            found.append(_as_run(instrument, run))
    return found


def _as_run(instrument: dict, run: dict) -> tuple:
    last_bid = bid_ask_at(instrument, run["last"] + BAR_SECONDS - 0.01)[0]
    return (
        instrument["id"], run["first"], run["last"], run["bars"], run["updates"], 0, 0,
        max(run["high"], last_bid), last_bid,
    )


def volumes(start: datetime, end: datetime, now: datetime) -> dict[int, float]:
    """What Saxo counted as traded in the period, for the turbos it counts a volume for."""
    days = sum(1 for day in _trading_days(start, min(end, now)) if _at(day, "08:00:00") <= now)
    return {instrument_id: volume * days for instrument_id, volume in DAILY_VOLUMES.items()} if days else {}


def coverage(start: datetime, end: datetime, now: datetime) -> list[dict]:
    """The recorder ran day and night; quotes only arrived in trading hours."""
    rows = []
    last = min(int(end.timestamp()), int(now.timestamp()) - BAR_SECONDS)
    bucket = _first_bucket(start, BAR_SECONDS)
    while bucket + BAR_SECONDS <= last:
        gap = _gap_at(bucket) if is_trading_time(bucket) else None
        if gap is None:
            rows.append({"ts": datetime.fromtimestamp(bucket, timezone.utc), "feed_ok": is_trading_time(bucket)})
        elif gap[1] is not None:
            rows.append({"ts": datetime.fromtimestamp(bucket, timezone.utc), "feed_ok": gap[1]})
        bucket += BAR_SECONDS
    return rows


def _trading_days(start: datetime, end: datetime):
    day = start.astimezone(TRADING_TIMEZONE).date()
    last = (end - timedelta(seconds=1)).astimezone(TRADING_TIMEZONE).date()
    while day <= last:
        if day.weekday() < 5:
            yield day
        day += timedelta(days=1)


def _at(day, clock: str) -> datetime:
    hour, minute, second = (int(part) for part in clock.split(":"))
    return TRADING_TIMEZONE.localize(datetime(day.year, day.month, day.day, hour, minute, second))


def trades(instrument: dict, start: datetime, end: datetime, now: datetime) -> list[dict]:
    found = []
    for day in _trading_days(start, end):
        for number, (instrument_id, entry_clock, exit_clock) in enumerate(DAILY_TRADES):
            traded = BY_ID[instrument_id]
            if instrument["role"] != "underlying" and traded["id"] != instrument["id"]:
                continue
            opened_at, closed_at = _at(day, entry_clock), _at(day, exit_clock)
            if opened_at > now:
                continue
            still_open = closed_at > now
            open_price = bid_ask_at(traded, opened_at.timestamp())[1]
            close_price = None if still_open else bid_ask_at(traded, closed_at.timestamp())[0]
            amount = 260
            profit = None if still_open else round((close_price - open_price) * amount, 2)
            found.append({
                "action": traded["direction"],
                "position_id": f"9{day:%m%d}{number}",
                "position_amount": amount,
                "position_open_price": open_price,
                "position_close_price": close_price,
                "position_close_reason": None if still_open else "Performance (Trailing Stop: peak 9.1%, now 0.8% (dd 8.3% >= 8%))",
                "position_profit_loss": profit,
                "position_total_open_price": round(open_price * amount, 2),
                "position_total_close_price": None if still_open else round(close_price * amount, 2),
                "position_total_performance_percent": None if still_open else round((close_price / open_price - 1) * 100, 2),
                "position_max_performance_percent": None,
                "position_status": "Open" if still_open else "Closed",
                "position_kind": "main",
                "execution_time_open": opened_at.astimezone(timezone.utc),
                "execution_time_close": None if still_open else closed_at.astimezone(timezone.utc),
                "order_id": None,
                "related_order_id": [],
                "instrument_name": traded["description"],
                "instrument_symbol": traded["symbol"],
                "instrument_uic": traded["uic"],
                "instrument_currency": "EUR",
            })
    return found


def daily_signals(day, now: datetime) -> list[tuple[dict, str, str | None, datetime]]:
    """The signals behind the day's made-up trades, and one that was refused: (signal, outcome, reason, when)."""
    planned = [
        ({"action": "long", "indice": "us100", "confidence": 0.9}, "executed", None, "09:20:05"),
        ({"action": "long", "indice": "us100"}, "refused",
         "Breaking trading rule: The signal is refused due to an open position with the same action long.", "09:41:10"),
        ({"action": "short", "indice": "us100", "confidence": 0.7}, "executed", None, "15:02:42"),
        ({"action": "close-position", "indice": "n/a"}, "ignored", "No open position to close", "21:55:00"),
    ]
    return [
        (signal, outcome, reason, _at(day, clock).astimezone(timezone.utc))
        for signal, outcome, reason, clock in planned
        if _at(day, clock) <= now
    ]
