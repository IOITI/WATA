"""
Every quote the Capital.com feed receives, kept as files: one gzip CSV per instrument and UTC day.

    <prices_root>/capital/quotes/<label>/<YYYY-MM-DD>.csv.gz          a day that is over
    <prices_root>/capital/quotes/<label>/<YYYY-MM-DD>.csv.gz.part     the day being written

The ten-second rows (``capital_bar_10s``, the day files of ``capital/bars``) say what each ten seconds held, not
when inside them the price went where: a range bar broken in the first second of its row waits for the row's end
as one broken in its last. These files keep each quote as the feed received it, in the order the bars' builder
saw them, so that the moment can be measured, and bars built from quotes replayed one day.

Lines, under the header ``rx_ms,t_ms,bid,ask``:

- ``rx_ms``: when the feed received the quote, on its own clock (``int(received_at * 1000)``, as the live
  messages carry it): the time the ten-second rows are bucketed by. In arrival order; it can step back with
  the clock.
- ``t_ms``: Capital.com's own time of the quote, empty when it gave none.
- ``bid``, ``ask``: as received. A market's close is a line with ``t_ms``, ``bid`` and ``ask`` empty: the bars'
  builder takes it as the end of what it carries (``BarBuilder.update(epic, None, at)``), so a replay needs it.

A line's day is the UTC day of its ``rx_ms``. A restart of the feed starts its bars' builder anew: the first
quotes after one are what that builder saw first.

How they are written, so that nothing of it can delay or risk the bars:

- the stream's callback only appends the encoded line to memory (``take``: it never raises nor waits), at most
  ``MAX_PENDING_BYTES``, past which the newest lines are dropped and counted;
- a task of the recorder's own writes them every minute (``write``), each file operation one at a time on a
  thread of its own, never timed out nor cancelled: a thread cancelled goes on, and lines put back would be
  written twice;
- a write appends one complete gzip member to the day's ``.part``; one that fails is cut back to the size the
  file had before it, and its lines wait for the next;
- when the feed starts, each ``.part`` is cut after its last complete member (a feed killed while writing, the
  zeros a power cut leaves), then written on;
- a day that ended ``FINAL_AFTER_SECONDS`` ago is written again as one member with no time nor name in its
  header (the same quotes give the same bytes, whatever the writes were) into ``<day>.csv.gz.final.part``, put
  in place of ``<day>.csv.gz``, and its ``.part`` removed; a line of a day already finished is dropped and
  counted (``late``);
- a lock (``capital/.quotes.lock``) keeps a second feed from writing the same files.
"""

import asyncio
import fcntl
import glob
import gzip
import logging
import os
import time
import zlib
from collections import defaultdict
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

logger = logging.getLogger(__name__)

SOURCE = "capital"
FOLDER = "quotes"
SUFFIX = ".csv.gz"
PART = ".part"
FINAL_PART = ".final.part"
LOCK_NAME = ".quotes.lock"
HEADER = b"rx_ms,t_ms,bid,ask\n"
# Lines waiting in memory: past this the newest are dropped (about 180,000 quotes).
MAX_PENDING_BYTES = 8 * 1024 * 1024
# How often the recorder writes what waits.
WRITE_EVERY_SECONDS = 60.0
# A day is finished this long after its end, once no line of it waits.
FINAL_AFTER_SECONDS = 600.0
COMPRESSLEVEL = 6
READ_CHUNK = 64 * 1024
COPY_CHUNK = 1024 * 1024
# At the feed's stop: how long a write under way is waited for, then the last one.
STOP_WAIT_SECONDS = 1.0
LAST_WRITE_SECONDS = 5.0


def quotes_folder(prices_root: str) -> str:
    return os.path.join(prices_root, SOURCE, FOLDER)


def day_path(prices_root: str, label: str, day: date) -> str:
    return os.path.join(quotes_folder(prices_root), label, f"{day.isoformat()}{SUFFIX}")


def encode(received_at: float, quoted_ms, bid, ask) -> tuple[int, bytes]:
    """The line of one quote, and its rx_ms. A close is a quote without a price."""
    rx = int(received_at * 1000)
    if bid is None or ask is None:
        return rx, f"{rx},,,\n".encode()
    stamp = "" if quoted_ms is None else str(int(quoted_ms))
    return rx, f"{rx},{stamp},{float(bid)!r},{float(ask)!r}\n".encode()


def day_of(rx_ms: int) -> date:
    return datetime.fromtimestamp(rx_ms / 1000, timezone.utc).date()


def day_end(day: date) -> float:
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() + 86400


def _day_of_name(name: str) -> date | None:
    try:
        return date.fromisoformat(name[:10])
    except ValueError:
        return None


def whole_members(path: str, on_member=None) -> int:
    """
    The bytes of ``path`` that are complete gzip members, read a little at a time; ``on_member`` is given each
    member's text once it is whole (its CRC and length checked by zlib). What follows the last whole member (a
    member cut off, zeros) is not counted.
    """
    whole = 0
    consumed = 0
    inflater = zlib.decompressobj(31)
    output: list[bytes] = []
    with open(path, "rb") as source:
        while chunk := source.read(READ_CHUNK):
            data = chunk
            while data:
                try:
                    out = inflater.decompress(data)
                except zlib.error:
                    return whole
                if on_member is not None and out:
                    output.append(out)
                if inflater.eof:
                    rest = inflater.unused_data
                    consumed += len(data) - len(rest)
                    whole = consumed
                    if on_member is not None:
                        on_member(b"".join(output))
                        output = []
                    data = rest
                    inflater = zlib.decompressobj(31)
                else:
                    consumed += len(data)
                    data = b""
    return whole


def repair(path: str) -> int:
    """Cut ``path`` after its last whole member; the bytes cut off."""
    size = os.path.getsize(path)
    whole = whole_members(path)
    if whole < size:
        os.truncate(path, whole)
    return size - whole


def read_quotes(path: str):
    """
    The lines of a file of quotes (finished or ``.part``), as (rx_ms, t_ms, bid, ask), t_ms, bid and ask None
    where empty. A member cut off at the end is left out.
    """
    texts: list[bytes] = []
    whole_members(path, texts.append)
    first = True
    for line in b"".join(texts).decode().splitlines():
        if first:
            first = False
            if line.encode() + b"\n" == HEADER:
                continue
        rx, stamp, bid, ask = line.split(",")
        yield (int(rx), int(stamp) if stamp else None, float(bid) if bid else None, float(ask) if ask else None)


def append_member(path: str, data: bytes) -> None:
    """One complete gzip member at the end of ``path``, the header first into an empty file; cut back if it fails."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        size = os.lseek(descriptor, 0, os.SEEK_END)
        member = memoryview(gzip.compress(HEADER + data if size == 0 else data, compresslevel=COMPRESSLEVEL, mtime=0))
        try:
            while member:
                member = member[os.write(descriptor, member):]
        except BaseException:
            os.ftruncate(descriptor, size)
            raise
    finally:
        os.close(descriptor)


def finish(part: str, final: str) -> None:
    """The day of ``part`` written again as one member, put in place of ``final``, then ``part`` removed."""
    repair(part)
    temporary = final + FINAL_PART
    with gzip.open(part, "rb") as source, open(temporary, "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=COMPRESSLEVEL) as target:
            while chunk := source.read(COPY_CHUNK):
                target.write(chunk)
    os.replace(temporary, final)
    os.remove(part)


def remove_days_before(prices_root: str, before: date) -> int:
    """Every instrument's files of quotes of a day before ``before``; how many were removed."""
    removed = 0
    for path in glob.glob(os.path.join(quotes_folder(prices_root), "*", "*")):
        day = _day_of_name(os.path.basename(path))
        if day is not None and day < before and os.path.isfile(path):
            os.remove(path)
            removed += 1
    return removed


@dataclass
class Written:
    """What one turn of writing did, handed back to the event loop."""

    lines: dict = field(default_factory=lambda: defaultdict(int))     # day -> lines appended
    late: int = 0
    failed: dict = field(default_factory=dict)                        # (label, day) -> the lines not written
    finished: list = field(default_factory=list)                      # (label, day) of the days finished
    pruned: int = 0
    error: str | None = None


def _said(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}" if str(error) else type(error).__name__


class QuoteFiles:
    """The quotes of the feed's instruments, from memory to their files (see the module's docstring)."""

    def __init__(self, prices_root: str, *, retention_days: int = 0, clock=time.time,
                 max_pending_bytes: int = MAX_PENDING_BYTES, final_after_seconds: float = FINAL_AFTER_SECONDS):
        self._root = prices_root
        self._retention_days = retention_days
        self._clock = clock
        self._max_pending_bytes = max_pending_bytes
        self._final_after_seconds = final_after_seconds
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="quote-files")
        self._pending: dict[tuple[str, date], bytearray] = {}
        self._pending_bytes = 0
        self._in_flight: tuple[Future, dict] | None = None
        self._opened = False
        self._lock_file = None
        self._pruned_on: date | None = None
        # The writer's thread alone: files whose cut-back failed, repaired before they are written again.
        self._suspect: set[str] = set()
        # The figures.
        self.dropped = 0
        self.late = 0
        self.failed_takes = 0
        self.locked_out = False
        self.repaired = 0
        self._written: dict[date, int] = defaultdict(int)
        self.last_finished: tuple[str, date] | None = None
        self.last_write_at: float | None = None
        self.error: str | None = None

    # ── the stream's side: memory only ──

    def take(self, label: str, received_at: float, quoted_ms, bid, ask) -> None:
        """A quote received (or a close, bid and ask None): kept in memory for the next write. Never raises."""
        try:
            rx, line = encode(received_at, quoted_ms, bid, ask)
            if self._pending_bytes + len(line) > self._max_pending_bytes:
                self.dropped += 1
                return
            self._pending.setdefault((label, day_of(rx)), bytearray()).extend(line)
            self._pending_bytes += len(line)
        except Exception:
            self.failed_takes += 1

    def closed(self, label: str, at: float) -> None:
        """A market closed: a line without a price."""
        self.take(label, at, None, None, None)

    # ── the writer's side: one file operation at a time, on its own thread ──

    def _lock(self) -> bool:
        folder = os.path.join(self._root, SOURCE)
        os.makedirs(folder, exist_ok=True)
        handle = open(os.path.join(folder, LOCK_NAME), "a")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self._lock_file = handle
        return True

    def _open(self) -> int:
        """In the writer's thread, once the lock is held: leftovers of a feed stopped while writing put right."""
        repaired = 0
        folder = quotes_folder(self._root)
        for temporary in glob.glob(os.path.join(folder, "*", f"*{SUFFIX}{FINAL_PART}")):
            os.remove(temporary)
        for part in glob.glob(os.path.join(folder, "*", f"*{SUFFIX}{PART}")):
            if os.path.exists(part[: -len(PART)]):
                # Finished before the feed stopped, its .part not removed yet.
                os.remove(part)
                continue
            repaired += repair(part)
        return repaired

    def _write(self, batch: dict, now: float, prune_before: date | None) -> Written:
        written = Written()
        for (label, day), data in batch.items():
            final = day_path(self._root, label, day)
            if os.path.exists(final):
                written.late += data.count(b"\n")
                continue
            part = final + PART
            try:
                if part in self._suspect and os.path.exists(part):
                    repair(part)
                self._suspect.discard(part)
                append_member(part, bytes(data))
                written.lines[day] += data.count(b"\n")
            except Exception as e:
                # Cut back already, unless that failed too: then repaired before the next write.
                self._suspect.add(part)
                written.failed[(label, day)] = data
                written.error = _said(e)
        for part in sorted(glob.glob(os.path.join(quotes_folder(self._root), "*", f"*{SUFFIX}{PART}"))):
            label = os.path.basename(os.path.dirname(part))
            day = _day_of_name(os.path.basename(part))
            if day is None or (label, day) in written.failed or now < day_end(day) + self._final_after_seconds:
                continue
            try:
                finish(part, part[: -len(PART)])
                written.finished.append((label, day))
            except Exception as e:
                written.error = _said(e)
        if prune_before is not None:
            try:
                written.pruned = remove_days_before(self._root, prune_before)
            except Exception as e:
                written.error = _said(e)
        return written

    # ── the event loop's side ──

    def _settle(self, batch: dict, future: Future) -> None:
        """What a write did: its figures kept, the lines it could not write waiting again."""
        try:
            written: Written = future.result()
        except BaseException as e:
            # The writer itself failed: every line of it waits again.
            written = Written(failed=dict(batch), error=_said(e))
        self.late += written.late
        for day, lines in written.lines.items():
            self._written[day] += lines
        if self._written:
            newest = max(self._written)
            for day in [day for day in self._written if day < newest - timedelta(days=1)]:
                del self._written[day]
        if written.finished:
            self.last_finished = written.finished[-1]
            for label, day in written.finished:
                logger.info("Quotes of %s on %s: day finished.", label, day.isoformat())
        if written.pruned:
            logger.info("Retention of %d day(s) of quote files: %d removed.", self._retention_days, written.pruned)
        for key, data in written.failed.items():
            if self._pending_bytes + len(data) > self._max_pending_bytes:
                self.dropped += data.count(b"\n")
                continue
            waiting = self._pending.get(key)
            # Those of before first, in the order they came.
            self._pending[key] = bytearray(data) + (waiting or b"")
            self._pending_bytes += len(data)
        if written.error and written.error != self.error:
            logger.warning("Quote files: %s (their lines wait for the next write)", written.error)
        self.error = written.error
        self.last_write_at = self._clock()

    def _prune_due(self, now: float) -> date | None:
        if self._retention_days <= 0:
            return None
        today = datetime.fromtimestamp(now, timezone.utc).date()
        if self._pruned_on == today:
            return None
        self._pruned_on = today
        return today - timedelta(days=self._retention_days)

    async def _run(self, function, *args):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, function, *args)

    def _open_files(self) -> int | None:
        """The lock taken, then the files of a feed stopped while writing put right; None: another feed holds it."""
        if not self._lock():
            return None
        return self._open()

    async def _opening(self) -> bool:
        """Before the first write: whether this feed may write the files."""
        try:
            repaired = await self._run(self._open_files)
        except Exception as e:
            # Written all the same: a file that cannot be written keeps its lines waiting.
            self.error = _said(e)
            logger.warning("Quote files could not be checked: %s", self.error)
            self._opened = True
            return True
        if repaired is None:
            if not self.locked_out:
                logger.error("Another feed holds the lock of the quote files: this one writes none.")
            self.locked_out = True
            self._drop_pending()
            return False
        self.locked_out = False
        self.repaired = repaired
        if repaired:
            logger.warning("Quote files: %d byte(s) of a write cut off were removed.", repaired)
        self._opened = True
        return True

    async def write(self) -> None:
        """Write what waits, then finish the days that are over. Never raises; a write under way is never cut."""
        if not self._opened and not await self._opening():
            return
        batch, self._pending, self._pending_bytes = self._pending, {}, 0
        now = self._clock()
        future = self._executor.submit(self._write, batch, now, self._prune_due(now))
        self._in_flight = (future, batch)
        await asyncio.wrap_future(future)
        if self._in_flight is not None and self._in_flight[0] is future:
            self._in_flight = None
            self._settle(batch, future)

    def _drop_pending(self) -> None:
        self.dropped += sum(data.count(b"\n") for data in self._pending.values())
        self._pending, self._pending_bytes = {}, 0

    async def stop(self) -> None:
        """At the feed's stop: the write under way waited for a moment (never cut), then what is left written."""
        if self._in_flight is not None:
            future, batch = self._in_flight
            await asyncio.wait({asyncio.wrap_future(future)}, timeout=STOP_WAIT_SECONDS)
            if future.done():
                self._in_flight = None
                self._settle(batch, future)
        if self._pending and self._in_flight is None:
            opened = self._opened
            if not opened:
                attempt = self._executor.submit(self._open_files)
                await asyncio.wait({asyncio.wrap_future(attempt)}, timeout=LAST_WRITE_SECONDS)
                opened = attempt.done() and attempt.exception() is None and attempt.result() is not None
            if opened:
                batch, self._pending, self._pending_bytes = self._pending, {}, 0
                future = self._executor.submit(self._write, batch, self._clock(), None)
                await asyncio.wait({asyncio.wrap_future(future)}, timeout=LAST_WRITE_SECONDS)
                if future.done():
                    self._settle(batch, future)
        if self._pending or self._in_flight is not None:
            waiting = sum(data.count(b"\n") for data in self._pending.values())
            logger.warning("At the stop, %d quote line(s) could not be written%s.", waiting,
                           " (a write was still under way)" if self._in_flight is not None else "")
        self._executor.shutdown(wait=False)
        if self._lock_file is not None:
            self._lock_file.close()
            self._lock_file = None

    def figures(self) -> dict:
        today = datetime.fromtimestamp(self._clock(), timezone.utc).date()
        return {
            "enabled": True,
            "written_today": self._written.get(today, 0),
            "pending_bytes": self._pending_bytes,
            "dropped": self.dropped,
            "late": self.late,
            "last_finished": self.last_finished[1].isoformat() if self.last_finished else None,
            "last_write_at": (datetime.fromtimestamp(self.last_write_at, timezone.utc).isoformat(timespec="seconds")
                              if self.last_write_at is not None else None),
            "locked_out": self.locked_out,
            "error": self.error,
        }
