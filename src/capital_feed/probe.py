"""
What Capital.com gives this login, before the feed is switched on, and what
its documentation leaves open.

    python -m src.capital_feed.probe --search "US Tech 100"
    python -m src.capital_feed.probe --epic US100 --minutes 5

The login is read from CAPITAL_API_KEY, CAPITAL_IDENTIFIER and
CAPITAL_PASSWORD in the environment (``--environment live`` for a real
account), never from a file, and nothing of it is printed. No configuration
of WATA is needed: it can be run on any machine.

With ``--epic``, it says:
- the instrument as Capital.com describes it, with its opening hours;
- where the stream of the session is, and that it sends quotes;
- how many quotes arrive and how late (Capital.com's time of each to its receipt);
- minute by minute, the quotes received and the changes of price counted,
  beside the volume Capital.com gives the same minute (``lastTradedVolume``),
  which its documentation does not define: which of the two it counts, if
  either, is the volume TradingView's chart of Capital.com shows.
"""

import argparse
import asyncio
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from src.capital_feed.client import CapitalRest, CapitalStream, LoginRefused, MinuteCandle, market_epic, market_status
from src.capital_feed.hours import DAYS, parse_opening_hours
from src.capital_feed.settings import CREDENTIAL_VARIABLES, REST_URLS, Credentials


@dataclass(frozen=True)
class ProbeQuote:
    received_at: float
    quoted_ms: int | None
    bid: float
    ask: float

    @property
    def at(self) -> float:
        """When Capital.com says it quoted, else when it was received: what its minutes are made of."""
        return self.quoted_ms / 1000 if self.quoted_ms is not None else self.received_at


@dataclass
class MinuteFigures:
    minute: datetime
    messages: int = 0
    changes: int = 0
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None


def minutes_of(quotes: list[ProbeQuote]) -> list[MinuteFigures]:
    """The quotes counted and the bid's candle, minute by minute, by Capital.com's time of the quotes."""
    figures: dict[datetime, MinuteFigures] = {}
    previous = None
    for quote in sorted(quotes, key=lambda item: item.at):
        moment = datetime.fromtimestamp(quote.at, timezone.utc).replace(second=0, microsecond=0)
        minute = figures.setdefault(moment, MinuteFigures(minute=moment))
        minute.messages += 1
        if previous is None or (quote.bid, quote.ask) != (previous.bid, previous.ask):
            minute.changes += 1
        previous = quote
        if minute.open is None:
            minute.open = minute.high = minute.low = quote.bid
        minute.high = max(minute.high, quote.bid)
        minute.low = min(minute.low, quote.bid)
        minute.close = quote.bid
    return [figures[moment] for moment in sorted(figures)]


def delays_of(quotes: list[ProbeQuote]) -> list[float]:
    """Milliseconds from Capital.com's time of each quote to its receipt, sorted."""
    return sorted(quote.received_at * 1000 - quote.quoted_ms for quote in quotes if quote.quoted_ms is not None)


def _percentile(values: list[float], share: float) -> float | None:
    if not values:
        return None
    return values[min(len(values) - 1, round(share * (len(values) - 1)))]


def volume_verdict(minutes: list[MinuteFigures], candles: dict[datetime, MinuteCandle]) -> str:
    """What Capital.com's volume counts, from the whole minutes both sides saw."""
    compared = [
        (minute, candles[minute.minute].volume) for minute in minutes[1:-1]
        if minute.minute in candles and candles[minute.minute].volume is not None
    ]
    if not compared:
        return "No whole minute to compare with Capital.com's volume (run it longer, while the market is open)."
    messages = sum(1 for minute, volume in compared if volume == minute.messages)
    changes = sum(1 for minute, volume in compared if volume == minute.changes)
    lines = [
        f"Capital.com's volume equals the quotes received in {messages} of {len(compared)} whole minute(s),"
        f" the changes of price in {changes}.",
    ]
    ratios = [volume / minute.messages for minute, volume in compared if minute.messages]
    if ratios and not messages and not changes:
        lines.append(f"Neither: volume / quotes received is {min(ratios):.2f} to {max(ratios):.2f}.")
    return "\n".join(lines)


def _number(value, decimals: int = 2) -> str:
    return "-" if value is None else f"{value:.{decimals}f}"


def report(details: dict, session_streaming_url: str, quotes: list[ProbeQuote], seconds: float,
           candles: list[MinuteCandle]) -> str:
    instrument = details.get("instrument") or {}
    snapshot = details.get("snapshot") or {}
    lines = [
        f"{market_epic(details) or '?'}: {instrument.get('name')} ({instrument.get('type')}, {instrument.get('currency')})",
        f"  market: {market_status(details)}, decimals: {snapshot.get('decimalPlacesFactor')},"
        f" delayed by: {snapshot.get('delayTime')} min, streamed: {instrument.get('streamingPricesAvailable')}",
    ]
    hours = instrument.get("openingHours") or {}
    if isinstance(hours, dict):
        lines.append(f"  opening hours ({hours.get('zone')}):")
        lines += [f"    {day}: {', '.join(hours.get(day) or []) or 'closed'}" for day in DAYS]
        if parse_opening_hours(hours) is None:
            lines.append("    (not in the form the feed reads: it will rely on the market state alone)")
    lines += ["", f"Stream: {session_streaming_url}", f"  {len(quotes)} quote(s) in {seconds:.0f} s"
              f" ({len(quotes) / seconds if seconds else 0:.1f} a second)"]
    delays = delays_of(quotes)
    if delays:
        lines.append(
            f"  delay, Capital.com to here: median {_number(_percentile(delays, 0.5), 0)} ms,"
            f" 95% under {_number(_percentile(delays, 0.95), 0)} ms, worst {_number(delays[-1], 0)} ms"
            " (includes the difference between the two clocks)"
        )
    minutes = minutes_of(quotes)
    by_minute = {candle.ts: candle for candle in candles}
    if minutes:
        lines += ["", "minute (UTC)  quotes  changes  Capital.com volume   bid O/H/L/C here  |  Capital.com"]
        for minute in minutes:
            candle = by_minute.get(minute.minute)
            ours = "/".join(_number(value) for value in (minute.open, minute.high, minute.low, minute.close))
            theirs = "-" if candle is None else "/".join(
                _number(value) for value in (candle.bid_open, candle.bid_high, candle.bid_low, candle.bid_close)
            )
            volume = "-" if candle is None or candle.volume is None else f"{candle.volume:g}"
            lines.append(f"{minute.minute:%H:%M}         {minute.messages:6d}  {minute.changes:7d}  {volume:>18}   {ours}  |  {theirs}")
        lines += ["", "(the first and last minutes are only partly seen)", volume_verdict(minutes, by_minute)]
    return "\n".join(lines)


async def _search(rest: CapitalRest, term: str) -> str:
    markets = await rest.search(term)
    if not markets:
        return f"Nothing found for {term!r}."
    lines = [f"{len(markets)} market(s) for {term!r}:"]
    for entry in markets:
        name = entry.get("instrumentName") or (entry.get("instrument") or {}).get("name")
        kind = entry.get("instrumentType") or (entry.get("instrument") or {}).get("type")
        lines.append(f"  {market_epic(entry):<16} {name}  ({kind}, {market_status(entry)})")
    lines.append("Name one with --epic to see its quotes, or in trade.config.capital_feed.instruments.")
    return "\n".join(lines)


async def _listen(rest: CapitalRest, epic: str, seconds: float) -> tuple[list[ProbeQuote], float]:
    quotes: list[ProbeQuote] = []

    def on_quote(quoted_epic, bid, ask, quoted_ms, received_at):
        if quoted_epic == epic:
            quotes.append(ProbeQuote(received_at, quoted_ms, bid, ask))

    stream = CapitalStream(rest, [epic], on_quote, expects_quotes=lambda: False)
    started = time.time()
    running = asyncio.create_task(stream.run())
    try:
        await asyncio.sleep(seconds)
    except asyncio.CancelledError:
        pass
    finally:
        stream.stop()
        await asyncio.gather(running, return_exceptions=True)
    return quotes, time.time() - started


def parse_arguments(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m src.capital_feed.probe",
        description=f"Check what Capital.com gives this login ({', '.join(CREDENTIAL_VARIABLES)} in the environment).",
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--search", help='a search, e.g. "US Tech 100"')
    target.add_argument("--epic", help="an instrument, e.g. US100: its details, its quotes and its volume")
    parser.add_argument("--environment", choices=sorted(REST_URLS), default="demo", help="which account the key logs into")
    parser.add_argument("--minutes", type=float, default=3.0, help="how long to listen to the stream (default 3)")
    return parser.parse_args(argv)


async def run(arguments: argparse.Namespace) -> int:
    credentials = Credentials.from_environment()
    if credentials is None:
        print(f"Set {', '.join(Credentials.missing())} in the environment first.", file=sys.stderr)
        return 2
    rest = CapitalRest(REST_URLS[arguments.environment], credentials)
    try:
        session = await rest.login()
        print(f"Logged in to the {arguments.environment} account.")
        if arguments.search:
            print(await _search(rest, arguments.search))
            return 0
        details = await rest.market(arguments.epic)
        print(f"Listening to {arguments.epic} for {arguments.minutes:g} min (Ctrl-C ends it sooner)...")
        quotes, seconds = await _listen(rest, arguments.epic, arguments.minutes * 60)
        candles = []
        if quotes:
            first = datetime.fromtimestamp(min(quote.at for quote in quotes), timezone.utc).replace(second=0, microsecond=0)
            # The last minute is over at Capital.com a moment after it ends.
            await asyncio.sleep(5)
            candles = await rest.prices(arguments.epic, first, datetime.now(timezone.utc) + timedelta(minutes=1))
        print(report(details, session.streaming_url, quotes, seconds, candles))
        return 0
    except LoginRefused as e:
        print(f"{e}. Check the three variables and the account ({arguments.environment}).", file=sys.stderr)
        return 1
    finally:
        await rest.close()


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(run(parse_arguments())))
    except KeyboardInterrupt:
        sys.exit(130)
